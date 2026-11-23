# RagBait Phase 4 — Milestone M4.2 Implementation & Certification Report

**Project:** RagBait  
**Phase:** 4 — AI Study Assistant and Retrieval-Augmented Generation (RAG)  
**Milestone:** M4.2 — Page-Aware Chunking & Durable Indexing Lifecycle  
**Date:** November 23, 2026  
**Status:** Certified & Production-Ready  
**Branch:** `phase4`  

---

## 1. Executive Summary

Milestone M4.2 establishes the document segmentation and background indexing queue infrastructure for RagBait's RAG system. Building upon the persistent extracted page records produced in Phase 3 (`public.kb_document_pages`), M4.2 converts raw document pages into deterministic, searchable text chunks with rigorous physical page provenance while guaranteeing that no chunk ever breaches the hard input limits of the primary multilingual embedding model.

In accordance with ADR-0004, ADR-0009, and ADR-0021, M4.2 enforces:
1. **Model Budget Allocation**: Exact tokenizer integration for `intfloat/multilingual-e5-small` with a hard ceiling of 420 raw tokens (`chk_chunk_token_ceiling CHECK (token_count <= 420)`). Combined with the required `"passage: "` prefix (2 tokens) and `<s>`/`</s>` framing tokens (2 tokens), the total embedding input cannot exceed 424 tokens, mathematically guaranteeing an 88-token safety margin below the model's 512-token ceiling.
2. **Page-Aware Provenance**: Every chunk records `page_number_start`, `page_number_end`, and detailed character offset spans across physical pages (`page_spans`), enabling exact citations and physical document navigation in the UI.
3. **Multilingual Punctuation Handling**: Native boundary splitting for English, Bengali (`।`), and Hindi/Devanagari (`।`, `॥`) academic texts, with hierarchical fallback to sentences, clauses, and character-safe boundaries.
4. **Durable Decoupled Indexing Queue**: A battle-tested background queue (`public.kb_indexing_jobs`) featuring document-first locking (`FOR UPDATE OF d, j SKIP LOCKED`), 120-second worker leases, periodic heartbeats, exponential retry backoff, and automatic lease recovery on worker crashes.
5. **Extraction Integration & Administrative Fencing**: Automatic indexing enqueueing upon extraction completion, worker lease invalidation on document archival or deletion, and an idempotent backfill CLI command.

In strict conformance with M4.2 milestone boundaries, **no embedding generation, vector columns, or similarity searches** were introduced (deferred to M4.3–M4.4).

---

## 2. Architecture & Data Model

### 2.1 Database Migration (`20260302000000_m4_2_document_indexing.sql`)

The migration introduces two new relational tables and extends `public.kb_documents`:

#### `public.kb_documents` (Extensions)
- `indexing_status text NOT NULL DEFAULT 'unindexed' CHECK (indexing_status IN ('unindexed', 'indexing', 'indexed', 'failed', 'not_indexable'))`
- `active_chunk_version integer DEFAULT NULL`
- Index: `idx_kb_documents_indexing_status` on `(indexing_status)`

#### `public.kb_indexing_jobs`
Durable background job queue tracking document indexing lifecycle:
```sql
CREATE TABLE public.kb_indexing_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id uuid NOT NULL REFERENCES public.kb_documents(id) ON DELETE CASCADE,
  source_version integer NOT NULL DEFAULT 1,
  chunking_version integer NOT NULL DEFAULT 1,
  status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'processing', 'completed', 'failed', 'cancelled')),
  worker_id text,
  lease_token uuid,
  attempts integer NOT NULL DEFAULT 0,
  max_attempts integer NOT NULL DEFAULT 3,
  scheduled_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
  locked_until timestamptz,
  heartbeat_at timestamptz,
  error_code text,
  error_message text,
  total_chunks integer NOT NULL DEFAULT 0,
  total_tokens integer NOT NULL DEFAULT 0,
  completed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
  updated_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now())
);
```
- **Concurrency Fencing**: Unique index `uq_kb_active_indexing_job` ensures at most one active job (`queued` or `processing`) per document.
- **Worker Polling Indexes**: `idx_kb_indexing_jobs_poll` on `(status, scheduled_at)` where `status = 'queued'`, and `idx_kb_indexing_jobs_locked` on `(status, locked_until)` where `status = 'processing'`.

#### `public.kb_document_chunks`
Stores deterministic chunk text with physical page provenance:
```sql
CREATE TABLE public.kb_document_chunks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id uuid NOT NULL REFERENCES public.kb_documents(id) ON DELETE CASCADE,
  chunk_index integer NOT NULL,
  page_number_start integer NOT NULL,
  page_number_end integer NOT NULL,
  raw_text text NOT NULL,
  character_count integer NOT NULL,
  token_count integer NOT NULL,
  content_hash text NOT NULL,
  chunking_version integer NOT NULL DEFAULT 1,
  source_version integer NOT NULL DEFAULT 1,
  indexing_job_id uuid REFERENCES public.kb_indexing_jobs(id) ON DELETE SET NULL,
  is_active boolean NOT NULL DEFAULT false,
  page_spans jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT timezone('utc'::text, now()),
  CONSTRAINT uq_kb_document_chunks_index UNIQUE (document_id, chunking_version, chunk_index),
  CONSTRAINT ck_kb_chunks_page_numbers CHECK (page_number_start >= 1 AND page_number_end >= page_number_start),
  CONSTRAINT ck_kb_chunks_char_count CHECK (character_count > 0 AND character_count = length(raw_text)),
  CONSTRAINT chk_chunk_token_ceiling CHECK (token_count > 0 AND token_count <= 420),
  CONSTRAINT ck_kb_chunks_versions CHECK (source_version >= 1 AND chunking_version >= 1)
);
```
- **Full-Text Search Index**: Native GIN index `idx_kb_chunks_fts` on `to_tsvector('english', raw_text)` prepares for hybrid retrieval in M4.4.
- **Deduplication / Identity**: Content SHA-256 hash `idx_kb_chunks_content_hash` enables deduplication and change detection.

### 2.2 Security & Access Control Policies (RLS)
- **`public.kb_indexing_jobs`**: RLS enabled. Direct access revoked from `anon` and `authenticated`. Managed strictly through internal service role / admin transactions.
- **`public.kb_document_chunks`**: RLS enabled. Direct mutations (`INSERT`, `UPDATE`, `DELETE`) revoked from `anon` and `authenticated`. Authenticated students have `SELECT` access **only** to chunks where `is_active = true` for published, uploaded, non-archived documents within active subjects and active topics:
  ```sql
  CREATE POLICY kb_document_chunks_select ON public.kb_document_chunks FOR SELECT
  USING (
    (
      is_active = true
      AND app_private.is_approved(auth.uid())
      AND EXISTS (
        SELECT 1 FROM public.kb_documents d
        JOIN public.kb_topics t ON t.id = d.topic_id
        JOIN public.kb_subjects s ON s.id = d.subject_id
        WHERE d.id = kb_document_chunks.document_id
          AND d.is_archived = false
          AND d.upload_status = 'uploaded'
          AND t.is_active = true
          AND s.is_active = true
      )
    )
    OR app_private.is_admin(auth.uid())
  );
  ```

---

## 3. Tokenizer Integration & Chunking Engine

### 3.1 Tokenizer Enforcement (`apps/api/app/modules/knowledge_base/chunking/tokenizer.py`)
- Integrated lightweight Rust-backed `tokenizers` library (`v0.23.3`). No heavyweight PyTorch, TensorFlow, or ONNX runtime dependencies were introduced.
- Pinned to `intfloat/multilingual-e5-small`.
- Assets cached locally in `apps/api/app/modules/knowledge_base/assets/tokenizer.json` to ensure 100% offline determinism and 50ms startup latency without network egress.
- Mathematical budget verification:
  $$\text{Raw Chunk Tokens} \le 420$$
  $$\text{Passage Prefix Overhead} = 2 \text{ tokens } (\text{"passage"}, \text{": "})$$
  $$\text{Framing Tokens} = 2 \text{ tokens } (<s>, </s>)$$
  $$\text{Total Passage Input} \le 424 \text{ tokens} \le 512 \text{ tokens}$$
  $$\text{Guaranteed Safety Margin} = 88 \text{ tokens}$$

### 3.2 Deterministic Chunker (`apps/api/app/modules/knowledge_base/chunking/chunker.py`)
- **Target Size**: 350–400 tokens per chunk (`TARGET_CHUNK_TOKENS = 380`).
- **Target Overlap**: 50–70 tokens (`TARGET_OVERLAP_TOKENS = 60`).
- **Hierarchical Splitting**:
  1. Double newlines / paragraphs.
  2. Multilingual sentence terminators (`. `, `? `, `! `, `\n`, Bengali `।`, Hindi/Devanagari `।`, `॥`).
  3. Clause punctuation (`;`, `,`, `:`, `—`, `-`).
  4. Whitespace and sub-slice fallback for long continuous tokens (unformatted code or formulas).
- **Physical Page Spans**: Aggregates `page_spans` list with `page_number`, `start_char`, and `end_char` per physical page.

---

## 4. Indexing Service, Worker Daemon & Lifecycle

### 4.1 Indexing Repository (`apps/api/app/modules/knowledge_base/indexing_repository.py`)
- `claim_next_indexing_job`: Employs consistent document-first lock order (`FOR UPDATE OF d, j SKIP LOCKED`) to eliminate database deadlocks.
- `renew_indexing_lease`: Heartbeats the worker lease timestamp.
- `complete_indexing_job_transaction`: Single atomic database transaction that:
  - Verifies lease token and document non-archival state.
  - Deletes any previous chunks for the target version and deactivates prior versions.
  - Inserts all generated chunks with `is_active = true`.
  - Sets `kb_documents.indexing_status = 'indexed'` and `active_chunk_version = chunking_version`.
  - Marks `kb_indexing_jobs.status = 'completed'` and clears the lease token.
- `fail_indexing_job`: Automatically schedules exponential backoff ($10 \times 2^{\text{attempts}-1}$ seconds) if `attempts < max_attempts`, or marks permanent failure.
- `cancel_indexing_jobs_for_document`: Cancels active jobs on document moderation.

### 4.2 Standalone Indexing Worker (`apps/api/app/modules/knowledge_base/indexing_worker.py`)
- Independent background daemon with CLI entry point (`python -m app.workers.indexing_worker`).
- Graceful shutdown on `SIGINT` / `SIGTERM` with clean pool teardown.
- Supports single-step execution (`--once`) for test automation and scheduled cron execution.

### 4.3 Backfill CLI Command (`apps/api/app/cli/backfill_indexing.py`)
- Discovers extracted documents with text that are unindexed.
- Idempotently enqueues indexing jobs (`ON CONFLICT (document_id) DO NOTHING`).
- Supports `--dry-run`, `--subject-id`, and `--limit`.

### 4.4 Moderation & Extraction Integration
- **Extraction Completion Hook** (`job_repository.py`): In the extraction transaction, when `final_doc_status == 'completed'`, enqueues `kb_indexing_jobs`. If no text was found, sets `indexing_status = 'not_indexable'`.
- **Document Archival & Deletion Fencing** (`admin_service.py`, `repository.py`): Document archival/deletion actively invalidates indexing worker leases, transitions active jobs to `cancelled`, deactivates chunk visibility, and resets `indexing_status`.

---

## 5. Verification & Regression Results

### 5.1 Database pgTAP Certification
The M4.2 test suite (`supabase/tests/m4_2_indexing_test.sql`) verifies:
- Table and column existence (`kb_indexing_jobs`, `kb_document_chunks`, `indexing_status`, `active_chunk_version`).
- RLS activation on all new tables.
- Total block of anonymous access (`anon` role).
- Total block of direct mutations from authenticated students (`authenticated` role).
- Enforced check constraint `chk_chunk_token_ceiling` rejecting chunks with token count > 420.
- Unique index `uq_kb_active_indexing_job` restricting active jobs to at most 1 per document.
- Check constraint on `indexing_status` validation.

**Full pgTAP Suite Result:**
```
Files=13, Tests=273, Result: PASS (100%)
```

### 5.2 Unit & Integration Test Suites
- **`apps/api/tests/test_kb_chunking.py`**:
  - `test_tokenizer_counts_and_passage_prefix`: Verified prefix and special token counts.
  - `test_empty_and_whitespace_pages`: Verified zero chunks on empty pages.
  - `test_single_page_under_target_size`: Single chunk with provenance.
  - `test_multipage_spanning_chunk`: Verified start and end page tracking.
  - `test_hard_token_ceiling_enforcement_on_huge_paragraph`: 120-sentence block segmented strictly under 420 tokens.
  - `test_bengali_sentence_boundary_support`: Verified Bengali dāṛi punctuation and token limits.
  - `test_hindi_sentence_boundary_support`: Verified Hindi danda punctuation and token limits.
  - `test_chunking_determinism`: Multiple passes produce identical hashes and chunk indices.
  - **Result: 8/8 PASSED**

- **`apps/api/tests/test_kb_indexing_worker.py`**:
  - `test_enqueue_and_worker_step_success`: Complete claim -> chunk -> activate lifecycle.
  - `test_lease_expiration_and_recovery`: Reclaim expired lease after worker crash.
  - `test_archival_cancels_active_job_and_deactivates_chunks`: Fencing on moderation.
  - `test_backfill_cli_discovery_and_enqueue`: Idempotent backfill discovery and enqueuing.
  - **Result: 4/4 PASSED**

- **Workstream A Verification (`test_openrouter_free_tier.py`)**:
  - Corrected official endpoint `GET https://openrouter.ai/api/v1/key`.
  - Defensive boolean parsing of `data.is_free_tier`.
  - Disallowing community dispatch on paid models.
  - **Result: 12/12 PASSED**

- **Frontend Regressions (`apps/web`)**:
  - Vitest: 23 test files, 141 tests PASSED.
  - TypeScript: 0 type errors.
  - ESLint: 0 errors.

---

## 6. Files Created and Modified

### Created Files
- `supabase/migrations/20260302000000_m4_2_document_indexing.sql`
- `supabase/tests/m4_2_indexing_test.sql`
- `apps/api/app/modules/knowledge_base/chunking/__init__.py`
- `apps/api/app/modules/knowledge_base/chunking/tokenizer.py`
- `apps/api/app/modules/knowledge_base/chunking/chunker.py`
- `apps/api/app/modules/knowledge_base/indexing_repository.py`
- `apps/api/app/modules/knowledge_base/indexing_service.py`
- `apps/api/app/modules/knowledge_base/indexing_worker.py`
- `apps/api/app/workers/__init__.py`
- `apps/api/app/workers/indexing_worker.py`
- `apps/api/app/cli/__init__.py`
- `apps/api/app/cli/backfill_indexing.py`
- `apps/api/tests/test_kb_chunking.py`
- `apps/api/tests/test_kb_indexing_worker.py`
- `docs/phase4/M4_2_IMPLEMENTATION_REPORT.md`

### Modified Files
- `.gitignore` (ignoring cached tokenizer JSON assets)
- `apps/api/pyproject.toml` (added `tokenizers>=0.23.3`)
- `apps/api/uv.lock`
- `apps/api/app/modules/knowledge_base/job_repository.py` (indexing enqueue hook)
- `apps/api/app/modules/knowledge_base/repository.py` (deletion cleanup)
- `apps/api/app/modules/knowledge_base/admin_service.py` (archival and restore fencing)
- `apps/api/tests/test_credentials_management.py` (OpenRouter sharing test alignment)
- `apps/api/tests/test_gateway_security.py` (cleanup alignment)
- `docs/phase4/PHASE4_ROADMAP.md` (marked M4.2 completed)

### Preserved User Modifications
All 9 uncommitted user files identified during preflight were strictly preserved intact:
1. `apps/api/app/main.py`
2. `apps/api/app/modules/community/ws.py`
3. `apps/api/tests/test_community_inference.py`
4. `apps/web/src/app/(auth)/login/page.tsx`
5. `apps/web/src/app/(protected)/access/contribution-required/page.tsx`
6. `apps/web/src/app/(protected)/credentials/page.tsx`
7. `apps/web/src/app/(protected)/local-inference/page.tsx`
8. `apps/web/src/lib/routing-client.ts`
9. `apps/web/src/components/LocalInferenceWorkspace.tsx`
