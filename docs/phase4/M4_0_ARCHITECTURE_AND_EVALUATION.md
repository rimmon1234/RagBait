# RagBait Phase 4 — M4.0: Master Architecture & Evaluation Specification

**Document Version:** 1.0.0  
**Phase:** Phase 4 — AI Study Assistant and Retrieval-Augmented Generation (RAG)  
**Milestone:** M4.0 — Architecture, Technical Research, Evaluation and Implementation Planning  
**Date:** November 22, 2026  
**Status:** Approved Architecture Plan  
**Target Execution Environment:** Next.js (TypeScript), FastAPI (Python 3.12+), PostgreSQL 17.12 (Supabase pgvector 0.8.2), Redis 7.2  

---

## 1. Executive Summary

RagBait Phase 3 established a collaborative academic knowledge-base platform allowing university students and faculty to browse subjects and topics, upload study materials (PDF/TXT), and view extracted text page-by-page.

**Phase 4 transforms RagBait into a persistent, grounded AI study assistant.** Students can create independent conversations strictly scoped to an academic subject, ask questions about uploaded textbooks and lecture notes, and receive answers rigorously grounded in retrieved passages with verified physical page citations (`[S1]`, `[S2]`). A signature **Sources & Context** panel shows students the exact excerpt used, the physical page number, and offers one-click navigation to the verified document page in the reader.

This document serves as the master architectural specification for Phase 4. It establishes the end-to-end design across text chunking, local vector embeddings, Supabase pgvector hybrid retrieval, persistent conversation history, context-window management, prompt-injection defense, and production concurrency control.

```mermaid
flowchart TD
    subgraph Client ["Frontend: Next.js Web Application"]
        UI["Student Chat Interface (/chat/[id])"]
        Sidebar["Subject Conversations Sidebar"]
        SourcesPanel["Sources & Context Drawer"]
        Reader["Document Viewer (?page=N)"]
    end

    subgraph Gateway ["API & Inference Routing (FastAPI)"]
        ChatRouter["/api/v1/chat/* Endpoints"]
        AuthEntitle["Auth & Entitlement Verification"]
        ContextMgr["Context Budget & Compaction Engine"]
        RouteExec["route_and_dispatch() Engine"]
    end

    subgraph Retrieval ["Hybrid Retrieval Subsystem"]
        QueryEmbed["Local Embedding Service (ONNX Runtime)"]
        HybridSearch["Hybrid Ranker (RRF k=60)"]
        VectorSearch["pgvector Cosine Search"]
        KeywordSearch["PostgreSQL Full-Text Search (tsvector)"]
    end

    subgraph Indexing ["Durable Indexing Worker"]
        JobQueue["kb_indexing_jobs (SKIP LOCKED)"]
        Chunker["Page-Aware Deterministic Chunker"]
        BatchEmbed["Local Batch Embedding Generator"]
    end

    subgraph Storage ["PostgreSQL 17.12 & Storage"]
        KB_Docs["kb_documents & kb_document_pages"]
        Chunks["kb_document_chunks"]
        Vectors["kb_chunk_embeddings (vector 384)"]
        ChatDB["chat_conversations & chat_messages"]
        EvidenceDB["chat_message_sources"]
        Vault["pgcrypto / Dedicated Vault Pool"]
    end

    UI --> ChatRouter
    Sidebar --> ChatRouter
    SourcesPanel --> Reader
    ChatRouter --> AuthEntitle
    AuthEntitle --> ContextMgr
    ContextMgr --> QueryEmbed
    QueryEmbed --> HybridSearch
    HybridSearch --> VectorSearch
    HybridSearch --> KeywordSearch
    VectorSearch --> Vectors
    KeywordSearch --> Chunks
    HybridSearch --> ContextMgr
    ContextMgr --> RouteExec
    RouteExec --> ChatDB
    RouteExec --> EvidenceDB
    RouteExec -. SSE Stream .-> UI

    KB_Docs --> JobQueue
    JobQueue --> Chunker
    Chunker --> Chunks
    Chunker --> BatchEmbed
    BatchEmbed --> Vectors
```

---

## 2. Baseline Architecture & Inherited Assets

### 2.1 Reused Components & Verified Interfaces

The Phase 4 architecture reuses the following verified Phase 0–Phase 3 infrastructure without duplication:

| Subsystem | Existing Component | Reused Capability | Phase 4 Integration Role |
| :--- | :--- | :--- | :--- |
| **Inference Routing** | `app/modules/routing/executor.py` | Multi-candidate discovery, free-tier policy scoring, credential retrieval, adapter dispatch | Phase 4 answers are dispatched through `route_and_dispatch()`. |
| **Provider Adapters** | `app/modules/llm_gateway/providers/` | Groq, OpenRouter, Cerebras, Gemini adapters with usage parsing and retry | Executes final completion requests with provider fallbacks. |
| **Admission & Quota** | `app/modules/admission/service.py` | Reservation leases, idempotency, microcent cost accounting, concurrency gating | Governs generation leases, per-user limits, and request idempotency. |
| **Credential Vault** | `app/modules/credentials/vault.py` | Dedicated connection pool, pgcrypto decryption, owner-only protection | Decrypts API credentials for cloud models at runtime. |
| **Document Hierarchy** | `public.kb_subjects`, `public.kb_topics`, `public.kb_documents` | Academic taxonomy, document metadata, checksums, and soft-delete states | Provides conversation scoping, topic groupings, and document visibility fencing. |
| **Extracted Text** | `public.kb_document_pages` | Physical page numbers (`page_number`), character/word counts, verified text content | Provides authoritative input text and physical page provenance for chunking. |
| **Worker Framework** | `app/modules/knowledge_base/job_repository.py` | `FOR UPDATE OF d, j SKIP LOCKED`, lease heartbeat, retry backoff | Pattern reused for durable `kb_indexing_jobs` worker daemon. |
| **Local Private LLM** | `apps/web/src/lib/local-inference/` | Loopback permission detection, Ollama client, direct private inference | Private Local mode runs local Ollama models with retrieved context without server transmission. |

### 2.2 Phase 3 Dependencies & Carry-Over Technical Risks

1. **Parser Isolation Sandbox**:
   - *Status*: Phase 3 Conditional GO noted that the extraction worker runs pypdf in an isolated subprocess. While robust for development and staging, production deployment with untrusted student PDFs requires a gVisor / container sandbox boundary before accepting open campus uploads.
   - *Phase 4 Impact*: Phase 4 operates downstream of extraction on `kb_document_pages` and does not parse raw PDF bytes directly.
2. **Missing Chat Persistence**:
   - *Status*: The Phase 2 inference playground (`/local-inference`) used ephemeral React state. No database tables or REST endpoints existed for conversation history or message storage.
   - *Phase 4 Impact*: M4.1 implements first-class conversation tables and paginated message APIs.
3. **pgvector Extension Status**:
   - *Status*: Local Supabase PostgreSQL 17.12 has `vector 0.8.2` and `pg_trgm 1.6` available in `pg_available_extensions`, but not yet enabled.
   - *Phase 4 Impact*: M4.3 migration will execute `CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;` and `CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA extensions;`.

---

## 3. Core System Requirements & Operational Invariants

### 3.1 Hard Product Constraints
1. **Strict Subject Scoping**: Every conversation belongs to exactly one `subject_id`. Cross-subject queries are rejected in MVP. All semantic and keyword queries filter on `kb_documents.subject_id = conversation.subject_id`.
2. **Physical Page Provenance**: Every retrieved passage must carry `page_number_start` and `page_number_end` derived from `kb_document_pages`.
3. **Sources & Context Exclusivity**: The Sources & Context drawer displays **only** evidence actually supplied in the LLM's final prompt budget, never raw candidate search outputs.
4. **Historical Evidence Immutability**: Old conversation messages render evidence snapshots stored at generation time, never re-queried dynamic search results.
5. **Access Revocation Fencing**: If a document is archived or soft-deleted after an answer was generated, expanding its source excerpt in history renders `[Source Unavailable / Archived by Admin]`.
6. **Zero Community Compute / Zero Peer Sharing**: Embeddings run strictly server-side using a self-contained local model. Student devices are never used for peer compute or remote pooling.
7. **Free-Tier Cloud Sharing Preserved**: Automatic routing uses only verified, explicitly shared free-tier cloud credentials (`sharing_scope='community'`, `billing_tier='free_tier'`).

---

## 4. End-to-End RAG Sequence & Data Flow

```mermaid
sequenceDiagram
    autonumber
    actor Student
    participant UI as Chat UI (/chat/[id])
    participant API as FastAPI Chat Router
    participant ContextMgr as Context & Budget Manager
    participant Embed as Local Embedding Engine
    participant DB as Supabase PostgreSQL
    participant Router as route_and_dispatch()
    participant LLM as Target LLM Provider

    Student->>UI: Types question & clicks Send
    UI->>API: POST /api/v1/chat/conversations/{id}/messages (SSE, idempotency_key)
    API->>DB: Check conversation ownership & active subject_id
    API->>DB: Verify Chat contribution entitlement
    API->>DB: Insert User Message into chat_messages (status: 'completed')
    API->>Embed: Embed student question (intfloat/multilingual-e5-small)
    Embed-->>API: Query vector (384 floats, normalized)
    
    rect rgb(240, 248, 255)
        note over API, DB: Hybrid Retrieval (Subject Scoped)
        API->>DB: Hybrid Search RPC (Cosine <=> + tsvector Full-Text, subject_id, limit=20)
        DB-->>API: Top 20 Candidates with RRF scores & physical page ranges
    end

    API->>API: Filter candidate passages against active document visibility
    API->>ContextMgr: Construct Prompt (Model Context Budget Formula)
    ContextMgr->>ContextMgr: Select top N chunks fitting evidence budget (assign [S1], [S2]...)
    ContextMgr->>ContextMgr: Format system prompt + summary + history + evidence

    API->>DB: Insert Assistant Message placeholder (status: 'generating')
    API->>DB: Insert chat_message_sources snapshot (citations S1..Sn with excerpts & pages)

    API->>Router: Dispatch completion request (stream=True)
    Router->>LLM: Stream prompt via provider adapter
    
    loop SSE Token Streaming
        LLM-->>Router: text_delta chunks
        Router-->>API: text_delta event
        API-->>UI: event: text_delta, data: {"delta": "..."}
    end

    LLM-->>Router: generation_completed (usage stats, finish_reason)
    API->>API: Validate cited [Sn] markers in output text against supplied sources
    API->>DB: Update Assistant Message (content, status: 'completed', usage, finish_reason)
    API-->>UI: event: generation_completed, data: {message_id, sources: [...]}
    UI->>Student: Renders Markdown response with clickable [S1] tags & Sources drawer
```

---

## 5. Technology Selection & Specification Summary

| Domain | Technology / Selection | Justification & Baseline Specs |
| :--- | :--- | :--- |
| **Embedding Engine** | `intfloat/multilingual-e5-small` | 384 dimensions, 512 max tokens, MIT license, 133MB weights. Runs locally on CPU via ONNX Runtime / `fastembed`. Exceptional English, Bengali, and Hindi academic retrieval performance. |
| **Vector Storage** | Supabase PostgreSQL `pgvector 0.8.2` | Native extension in Supabase PostgreSQL 17. Normalized cosine distance `<=>`. Native integration with PostgreSQL transactions and ACID guarantees. |
| **Vector Indexing** | Exact Scan ($N < 20,000$) $\to$ HNSW ($N \ge 20,000$) | For typical university subjects with under 20k chunks, exact cosine distance with B-Tree filtering on `subject_id` achieves $< 8\text{ms}$ latency with 100% recall. HNSW (`m=16`, `ef_construction=64`) used as corpus scales. |
| **Keyword Search** | PostgreSQL Full-Text Search (`tsvector`, `tsquery`) | Built-in text search using English / simple dictionary with GIN indexing. Captures exact technical algorithms, acronyms, and course codes that semantic embeddings miss. |
| **Hybrid Ranking** | Reciprocal Rank Fusion (RRF, $k=60$) | $RRF(d) = \frac{0.6}{60 + rank_{vec}(d)} + \frac{0.4}{60 + rank_{fts}(d)}$. Scale-invariant rank fusion outperforming linear score combination. |
| **Context Budgeting** | Deterministic Floor & Ceiling Formula | Hard budget preventing provider context truncation: reserves output tokens first, limits retrieved evidence to 60% of remainder, and slides historical window over the rest. |
| **Long-Chat Memory** | Asynchronous Map-Reduce Summarization | Retains last $K=6$ turns verbatim. Messages older than $K$ turns are summarized into `chat_conversation_summaries`. Original transcript is permanently preserved. |
| **Streaming Protocol** | Server-Sent Events (SSE) | HTTP/1.1 and HTTP/2 compatible streaming over standard fetch API. Native disconnect detection and clean event framing (`generation_started`, `text_delta`, `generation_completed`). |
| **Concurrency Control** | Redis Distributed Semaphores | Max 1 generation per conversation (race-free sequence), max 2 per user, max 10 concurrent requests to cloud tier. Bounded waiting queue with 30s timeout. |

---

## 6. Document Map for Phase 4 Architecture

This master specification is supported by 10 specialized planning documents in `docs/phase4/`:

1. [M4_0_ARCHITECTURE_AND_EVALUATION.md](file:///d:/Projects/RagBait/docs/phase4/M4_0_ARCHITECTURE_AND_EVALUATION.md): Master architecture overview, sequence flow, and executive decisions.
2. [PHASE4_ROADMAP.md](file:///d:/Projects/RagBait/docs/phase4/PHASE4_ROADMAP.md): Detailed milestones M4.0 to M4.8 with deliverables, dependencies, and exit criteria.
3. [DATA_MODEL_AND_MIGRATION_DESIGN.md](file:///d:/Projects/RagBait/docs/phase4/DATA_MODEL_AND_MIGRATION_DESIGN.md): Detailed schema designs for chunks, embeddings, indexing jobs, conversations, messages, and evidence snapshots.
4. [API_AND_EVENT_CONTRACTS.md](file:///d:/Projects/RagBait/docs/phase4/API_AND_EVENT_CONTRACTS.md): Authoritative REST endpoint specifications, JSON schemas, SSE streaming protocols, and error envelopes.
5. [CHAT_CONTEXT_AND_GUARDRAILS.md](file:///d:/Projects/RagBait/docs/phase4/CHAT_CONTEXT_AND_GUARDRAILS.md): Prompt budgeting, model capabilities registry, summarization engine, and prompt-injection defense.
6. [SOURCES_AND_CITATIONS_DESIGN.md](file:///d:/Projects/RagBait/docs/phase4/SOURCES_AND_CITATIONS_DESIGN.md): Citation formatting, evidence snapshots, Sources & Context drawer UX, and physical page navigation.
7. [RETRIEVAL_AND_EMBEDDING_DESIGN.md](file:///d:/Projects/RagBait/docs/phase4/RETRIEVAL_AND_EMBEDDING_DESIGN.md): Deterministic chunking, embedding model evaluation, pgvector indexing, and RRF hybrid search.
8. [GENERATION_RELIABILITY_AND_LOAD_PLAN.md](file:///d:/Projects/RagBait/docs/phase4/GENERATION_RELIABILITY_AND_LOAD_PLAN.md): Concurrency control, Redis semaphores, cancellation, retry backoff, and failure recovery.
9. [SECURITY_AND_PRIVACY_THREAT_MODEL.md](file:///d:/Projects/RagBait/docs/phase4/SECURITY_AND_PRIVACY_THREAT_MODEL.md): Row-Level Security, multi-tenant isolation, prompt injection threat analysis, and data leak prevention.
10. [RAG_EVALUATION_SPECIFICATION.md](file:///d:/Projects/RagBait/docs/phase4/RAG_EVALUATION_SPECIFICATION.md): 50-question golden dataset, precision/recall metrics, faithfulness benchmarks, and acceptance thresholds.
11. [ARCHITECTURE_DECISIONS.md](file:///d:/Projects/RagBait/docs/phase4/ARCHITECTURE_DECISIONS.md): Architectural Decision Records (ADR 01 through ADR 22) documenting options, selections, trade-offs, and conditions for revisiting.
