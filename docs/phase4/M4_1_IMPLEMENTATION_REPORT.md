# RagBait Phase 4 — Milestone M4.1 Implementation Report

**Milestone:** M4.1 — Persistent Chat Infrastructure & Conversation Management  
**Branch:** `phase4`  
**Execution Date:** November 23, 2026  
**Status:** COMPLETE & FULLY VERIFIED (Hardened & Audited)  
**Baseline Commit:** `72d8e1c`  
**Implementation Commit:** `dd70da7`  

---

## 1. Executive Summary

Milestone **M4.1 (Chat Persistence & Conversation Management)** establishes the persistent, multi-tenant foundation for RagBait Phase 4's AI Study Assistant. Prior to this milestone, chat functionality in RagBait was confined to transient in-memory state within the Phase 2 local inference playground.

Under M4.1, RagBait provides durable, multi-tenant, subject-scoped conversational persistence backed by PostgreSQL with transactional integrity, Row-Level Security (RLS), cursor-based pagination, platform contribution entitlement enforcement, and complete frontend conversation management.

### Key Deliverables Completed:
1. **Database Schema & RLS Policies** (`supabase/migrations/20260301000000_m4_1_chat_persistence.sql`):
   - Created tables `public.chat_conversations` and `public.chat_messages`.
   - Custom enum types `chat_conversation_status`, `chat_message_role`, and `chat_message_status`.
   - Strict subject binding: every conversation is permanently bound to `public.kb_subjects` (`ON DELETE RESTRICT`).
   - Transactional, monotonic sequence generation ensuring message sequence integrity (`(conversation_id, sequence_number)` unique).
   - Strict Row-Level Security (RLS) policies isolating conversations between students; public/anonymous access revoked.
2. **Database Verification Suite** (`supabase/tests/`):
   - **All 11 pgTAP test suites passed: 240 of 240 subtests PASSED (100%)**, including all 28 subtests in `m4_1_chat_persistence_test.sql`.
3. **Backend Chat Engine (`apps/api`)**:
   - Pydantic v2 schemas with strict validation, whitespace stripping, and Unicode control-character sanitization (`schemas.py`).
   - Parameterized asyncpg repository with pessimistic row locking (`SELECT FOR UPDATE`) for race-free sequence allocation (`repository.py`).
   - Domain service enforcing subject verification, opaque URL-safe base64 cursor pagination, and privacy-preserving 404 responses (`service.py`).
   - REST API router mounted at `/api/v1/chat/` (`router.py`).
   - Contribution entitlement checker and dependency (`check_user_chat_entitlement`, `require_chat_entitlement` in `dependencies.py`).
   - Authoritative entitlement discovery endpoint `GET /api/v1/me/chat-entitlement` (`me.py`).
   - Chat persistence test suite (`apps/api/tests/test_chat_persistence.py`): **16 of 16 pytest integration tests PASSED**.
   - Backend regression test suite (`test_auth.py`, `test_credentials_management.py`, etc.): **21 of 21 pytest tests PASSED**.
   - Code quality: Ruff check and format clean (0 warnings/errors), Mypy strict mode clean (0 issues).
4. **Frontend Chat Client & UI Components (`apps/web`)**:
   - Strongly-typed API client and TypeScript definitions (`apps/web/src/types/chat.ts`, `apps/web/src/lib/chat-client.ts`, `apps/web/src/types/index.ts`).
   - Conversation management sidebar (`ChatSidebar.tsx`) with active/archived tabs, subject filter, title search, and new chat trigger.
   - Comprehensive modals for creating (`NewConversationModal.tsx`), renaming (`RenameConversationModal.tsx`), and deleting (`DeleteConversationModal.tsx`) conversations.
   - Conversation viewer (`ChatInterface.tsx`) rendering message history with sequence badges (`#1`, `#2`), timestamps, role indicators, and honest M4.1 infrastructure banners.
   - Dedicated protected routes (`/chat` and `/chat/[id]`), plus direct "Study Chat" link from Knowledge Base subject pages (`/knowledge-base/[subjectSlug]`).
   - Full frontend test suite (23 test files): **141 of 141 Vitest tests PASSED (100%)**.
   - TypeScript compilation (`tsc --noEmit`): **0 errors**.
   - ESLint (`next lint`): **0 errors**.
   - Production build (`next build`): **0 errors** across all 26 static and dynamic routes.
5. **Strict Scope Compliance**:
   - **Zero fake AI**: No mock streaming, no fake assistant responses, no client-forged messages.
   - **Zero premature RAG**: Vector extensions, embeddings, and chunking remain deferred to subsequent milestones (M4.2/M4.3).

---

## 2. Database Architecture & Security Enforcement

### 2.1 Database Schema Definition

```
                                  +-------------------+
                                  |    kb_subjects    |
                                  +-------------------+
                                            |
                                            | (RESTRICT)
                                            v
+-------------------+             +--------------------+             +-------------------+
|      profiles     | <---------- | chat_conversations | <---------- |   chat_messages   |
+-------------------+  (CASCADE)  +--------------------+  (CASCADE)  +-------------------+
                                  | id (UUID PK)       |             | id (UUID PK)      |
                                  | user_id (UUID FK)  |             | conv_id (UUID FK) |
                                  | subject_id (UUID)  |             | sequence_number   |
                                  | title (VARCHAR)    |             | role (enum)       |
                                  | status (enum)      |             | content (TEXT)    |
                                  | next_seq_num (INT) |             | status (enum)     |
                                  | message_count(INT) |             | created_at (TIMEST)
                                  +--------------------+             +-------------------+
```

#### Key Constraints:
- `uq_chat_messages_conv_seq`: `UNIQUE (conversation_id, sequence_number)` guarantees sequential numbering cannot have collisions or duplicates.
- `ck_chat_conversations_title_length`: `char_length(trim(title)) >= 1 AND char_length(title) <= 255`.
- `ck_chat_messages_content_length`: `char_length(content) > 0 AND octet_length(content) <= 32768` (32 KB limit).
- `fk_chat_conversations_subject`: Foreign key to `public.kb_subjects(id) ON DELETE RESTRICT`, preventing accidental cascade deletion of student chat history when a subject is managed.

### 2.2 Row-Level Security (RLS) Policy Verification
Row-Level Security is strictly enabled on both tables:
- **`public.chat_conversations`**:
  - `SELECT`: `user_id = auth.uid() AND status != 'deleted'`
  - `INSERT`: `WITH CHECK (user_id = auth.uid())`
  - `UPDATE`: `USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid())`
  - `DELETE`: `USING (user_id = auth.uid())`
- **`public.chat_messages`**:
  - `SELECT`: `EXISTS (SELECT 1 FROM public.chat_conversations c WHERE c.id = chat_messages.conversation_id AND c.user_id = auth.uid() AND c.status != 'deleted')`
  - `INSERT`: `WITH CHECK (EXISTS (SELECT 1 FROM public.chat_conversations c WHERE c.id = chat_messages.conversation_id AND c.user_id = auth.uid() AND c.status = 'active'))`
  - `UPDATE`: `USING (EXISTS (SELECT 1 FROM public.chat_conversations c WHERE c.id = chat_messages.conversation_id AND c.user_id = auth.uid()))`
  - `DELETE`: `USING (EXISTS (SELECT 1 FROM public.chat_conversations c WHERE c.id = chat_messages.conversation_id AND c.user_id = auth.uid()))`

Anonymous privileges (`anon`) have been completely revoked.

---

## 3. Contribution Entitlement & Authorization Matrix

A central audit requirement in M4.1 was establishing and enforcing the platform's contribution entitlement boundary on the backend rather than relying on frontend-only checks.

### 3.1 Platform Contribution Rule
In accordance with RagBait's community-driven architecture:
1. **Admin Exemption**: Platform administrators (`admin`, `super_admin`) have full, unconstrained access.
2. **Approved Regular Users**: To access shared AI compute and initiate new conversations, a user must have at least **one active, validated, explicitly consented free-tier cloud API credential** in `public.api_key_metadata` with:
   - `runtime_type = 'cloud_api'`
   - `allows_community_sharing = true` in `public.llm_providers` (e.g., Groq, OpenRouter, Cerebras)
   - `sharing_scope = 'community_shared'`
   - `shared_consent = true`
   - `validation_status = 'valid'`
   - `status = 'active'`
3. **Ineligible Keys**:
   - Private local Ollama daemons do NOT qualify (they serve only the local browser).
   - Paid API keys do NOT qualify for community sharing.
   - Unvalidated or inactive keys do NOT satisfy the entitlement requirement.

### 3.2 Authorization Boundary Matrix

| Operation | Endpoint | Required Role / Dependency | Non-Contributor Behavior |
|---|---|---|---|
| **Create Conversation** | `POST /api/v1/chat/conversations` | `require_chat_entitlement` | **403 Forbidden** (`CHAT_CONTRIBUTION_REQUIRED` + diagnostic rejection reason) |
| **List Conversations** | `GET /api/v1/chat/conversations` | `require_approved_user` | **200 OK** (Can list owned conversations and history) |
| **Read Conversation** | `GET /api/v1/chat/conversations/{id}` | `require_approved_user` + Owner Check | **200 OK** (Owner can read conversation details) |
| **Read Messages** | `GET /api/v1/chat/conversations/{id}/messages` | `require_approved_user` + Owner Check | **200 OK** (Owner can read past transcripts without compute lock) |
| **Rename Conversation** | `PATCH /api/v1/chat/conversations/{id}` | `require_approved_user` + Owner Check | **200 OK** (Organization allowed) |
| **Archive Conversation** | `POST /api/v1/chat/conversations/{id}/archive` | `require_approved_user` + Owner Check | **200 OK** (Organization allowed) |
| **Restore Conversation** | `POST /api/v1/chat/conversations/{id}/restore` | `require_approved_user` + Owner Check | **200 OK** (Organization allowed) |
| **Delete Conversation** | `DELETE /api/v1/chat/conversations/{id}` | `require_approved_user` + Owner Check | **200 OK** (Owner can soft-delete) |
| **Check Entitlement** | `GET /api/v1/me/chat-entitlement` | `require_not_suspended` | **200 OK** (Returns status, diagnostic reason, and qualifying key count) |
| **Knowledge Base** | `GET /api/v1/kb/...` | `require_approved_user` | **200 OK** (Reading study materials is always accessible to approved users) |

This boundary guarantees that students who lose their credential or whose key becomes rate-limited **never lose access to their past conversations, study notes, or academic materials**. Only the creation of new conversations and future AI generation operations require active contribution entitlement.

---

## 4. Backend Architecture & Mechanics (`apps/api`)

### 4.1 Concurrency & Monotonic Sequence Numbering
Message sequence numbers are allocated under pessimistic row locks in `ChatRepository.insert_message`:
```sql
SELECT next_sequence_number
FROM public.chat_conversations
WHERE id = $1
FOR UPDATE;
```
This guarantees that concurrent message insertions never race or allocate duplicate sequence numbers, verified by concurrency stress testing (`test_concurrent_message_insertion_sequence_integrity`).

### 4.2 Cursor-Based Pagination
Pagination cursors are opaque URL-safe base64-encoded strings:
- Conversations: `base64(last_message_at/created_at + "|" + conversation_id)`
- Messages: `base64(sequence_number)`
This prevents deep SQL offset performance degradation and eliminates pagination drift when messages or conversations are inserted in real time.

---

## 5. Frontend Architecture (`apps/web`)

### 5.1 Component Tree & Routes

```
apps/web/src/
├── app/(protected)/
│   ├── chat/
│   │   ├── page.tsx            # Multi-conversation dashboard & subject landing
│   │   └── [id]/page.tsx       # Active conversation transcript & message viewer
│   └── knowledge-base/
│       └── [subjectSlug]/page.tsx # "Study Chat" action button integration
├── components/chat/
│   ├── ChatSidebar.tsx         # Sidebar: Search, active/archived tabs, subjects, actions
│   ├── ChatInterface.tsx       # Message list, sequence badges, timestamps, M4.1 banner
│   ├── NewConversationModal.tsx    # Subject picker & custom title modal
│   ├── RenameConversationModal.tsx # Conversation rename dialog
│   └── DeleteConversationModal.tsx # Irreversible deletion warning modal
├── lib/
│   └── chat-client.ts          # Strongly typed API client with fetchWithAuth
└── types/
    ├── chat.ts                 # ChatConversation, ChatMessage, and pagination types
    └── index.ts                # Platform-wide types including ChatEntitlementResponse
```

### 5.2 User Experience Features
- **Subject-Scoped Conversations**: Students can select their academic subject or start directly from any Knowledge Base subject page.
- **Active / Archived Navigation**: Instantly toggle between active discussions and archived review sessions without loss of history.
- **Cursor History Loading**: Older messages load seamlessly without layout jump or unexpected re-renders.
- **Honest System Status**: The chat input displays clear information indicating that message persistence is operational, and that grounded RAG answering will be activated in upcoming milestones (M4.5/M4.6).

---

## 6. Automated Verification Results

### 6.1 Database pgTAP Tests (`supabase/tests/`)
Executed via Supabase CLI against PostgreSQL 17:
- `m4_1_chat_persistence_test.sql`: **28 of 28 tests PASSED (100%)**
- Total database test suite (11 test files): **240 of 240 tests PASSED (100%)**

### 6.2 Backend Pytest Suite (`apps/api/tests/`)
Executed via Python 3.12 pytest runner:
- `test_chat_persistence.py` (16 tests):
  - `test_unauthenticated_request_rejected`: PASSED
  - `test_unapproved_user_rejected`: PASSED
  - `test_non_contributor_create_conversation_rejected`: PASSED
  - `test_non_contributor_can_list_and_read_conversations`: PASSED
  - `test_non_contributor_can_access_knowledge_base`: PASSED
  - `test_chat_entitlement_endpoint`: PASSED
  - `test_local_ollama_and_unapproved_providers_do_not_grant_entitlement`: PASSED
  - `test_create_conversation_success`: PASSED
  - `test_create_conversation_default_title`: PASSED
  - `test_create_conversation_inactive_subject_rejected`: PASSED
  - `test_create_conversation_invalid_subject_rejected`: PASSED
  - `test_title_validation`: PASSED
  - `test_cross_user_isolation`: PASSED
  - `test_conversation_mutations`: PASSED
  - `test_message_pagination`: PASSED
  - `test_concurrent_message_insertion_sequence_integrity`: PASSED
  - **Result: 16 of 16 tests PASSED (100%).**
- Regression test suite (`test_auth.py`, `test_credentials_management.py`, `test_profile_permissions.py`, `test_health.py`):
  - **Result: 21 of 21 tests PASSED (100%).**
- Static Analysis:
  - `ruff check`: PASSED (0 errors)
  - `ruff format --check`: PASSED (0 errors)
  - `mypy`: PASSED strict mode (0 errors across 7 files)

### 6.3 Frontend Vitest Suite (`apps/web/tests/`)
Executed via Vitest:
- `tests/chat/chat-client.test.ts` (8 tests): PASSED
- `tests/chat/ChatComponents.test.tsx` (11 tests): PASSED
- `tests/CredentialsPage.test.tsx` (5 tests): PASSED
- Full web application test suite (23 test files, 141 tests): **141 of 141 tests PASSED (100%)**.
- TypeScript typecheck (`tsc --noEmit`): **0 errors**.
- ESLint (`next lint`): **0 errors**.
- Production build (`next build`): **0 errors** (26 pages compiled).

---

## 7. Scope Boundary Compliance

| Requirement | Milestone M4.1 Status | Scheduled Milestone |
|---|---|---|
| Conversation & Message Schema | Complete | M4.1 |
| Conversation CRUD & Lifecycle | Complete | M4.1 |
| Sequence Integrity & Pessimistic Locks | Complete | M4.1 |
| Cursor Pagination | Complete | M4.1 |
| Multi-tenant RLS Isolation | Complete | M4.1 |
| Contribution Entitlement Enforcement | Complete | M4.1 |
| Authoritative Entitlement API (`/me/chat-entitlement`) | Complete | M4.1 |
| Frontend Sidebar, Modals & Message Shell | Complete | M4.1 |
| Document Chunking Engine | Deferred | M4.2 |
| Asynchronous Indexing Worker | Deferred | M4.2 |
| pgvector Extension & Vector Storage | Deferred | M4.3 |
| Embedding Generation (`multilingual-e5-small`) | Deferred | M4.3 |
| Hybrid Retrieval (Reciprocal Rank Fusion) | Deferred | M4.4 |
| Context Window Compaction & Budgeting | Deferred | M4.5 |
| Prompt-Injection Guardrails | Deferred | M4.5 |
| Grounded RAG Orchestration | Deferred | M4.6 |
| Sources & Context Drawer | Deferred | M4.7 |
| End-to-End Evaluation & Certification | Deferred | M4.8 |

---

## 8. Next Milestone Transition

With Milestone M4.1 fully hardened, audited, verified, and closed out:
- **Immediate Next Step**: Begin **Milestone M4.2 — Page-Aware Chunking and Durable Indexing Lifecycle**.
- M4.2 will introduce:
  - Deterministic document chunking bounded strictly to 420 tokens (`chk_chunk_token_ceiling`).
  - Sentence and clause splitting with page provenance retention (`page_number_start`, `page_number_end`).
  - Durable indexing job lifecycle with transactional lease locking (`SKIP LOCKED`) and automated extraction triggering.
