# RagBait Phase 4 — Architectural Decision Records (ADRs 01 – 22)

**Document Version:** 1.1.0  
**Phase:** Phase 4 — AI Study Assistant and Retrieval-Augmented Generation (RAG)  
**Date:** November 22, 2026  
**Status:** Approved Architectural Decisions (Remediated Baseline)  

---

### ADR 01: Reusability of Existing Chat & Routing Infrastructure
- **Context**: RagBait has an inference routing engine (`route_and_dispatch()`) in `apps/api/app/modules/routing/`, but the frontend `/local-inference` playground stores messages in transient React state without database tables.
- **Options**:
  1. *Reuse routing engine, introduce relational persistence*: Dispatch through `route_and_dispatch()` while adding dedicated `chat_conversations` and `chat_messages` tables.
  2. *Duplicate LLM Gateway*: Build an independent chat gateway separate from `llm_gateway`.
- **Decision**: **Option 1**.
- **Reasoning**: `route_and_dispatch()` already handles credential vault decryption, provider quotas, admission leases, and multi-provider failover. Duplicating it would fragment quota tracking and credential management.
- **Trade-offs**: Chat router must format retrieved RAG context into `ChatCompletionRequest`.
- **Risks**: None; clean separation between persistence and inference.
- **Revisiting Conditions**: If real-time bidirectional agent tooling requires stateful WebSockets.

---

### ADR 02: Authoritative Conversation and Message Storage Model
- **Context**: Need durable persistence for user conversations and messages with strict ordering and fast retrieval.
- **Options**:
  1. *Relational PostgreSQL tables*: `chat_conversations` and `chat_messages` with monotonic integer `sequence_number`.
  2. *NoSQL / Document store (e.g. MongoDB or JSON blobs in Redis)*.
  3. *Single JSONB array on conversation row*.
- **Decision**: **Option 1 (Relational PostgreSQL tables)**.
- **Reasoning**: PostgreSQL is already our primary ACID database. Row-level storage with `sequence_number` enables simple cursor pagination, atomic appending, and transactional joins with evidence snapshots.
- **Trade-offs**: Requires foreign key joins when loading conversation threads.
- **Risks**: High message volume in long chats; mitigated by cursor pagination.
- **Revisiting Conditions**: Never; standard relational model is optimal.

---

### ADR 03: Subject-Scoping Architecture & Fencing
- **Context**: Students study within specific university courses (e.g., Operating Systems, DBMS).
- **Options**:
  1. *Strict 1:1 Subject Scoping*: A conversation belongs to exactly one `subject_id`.
  2. *Global / Cross-Subject Chat*: Chat searches across all subjects simultaneously.
  3. *Topic-Level Scoping*: Bound to a single topic.
- **Decision**: **Option 1 (Strict 1:1 Subject Scoping)**.
- **Reasoning**: Course terminology often collides across disciplines (e.g., "Paging" in OS vs memory allocation in Embedded Systems; "Transactions" in DBMS vs Financial Systems). Scoping to a subject prevents false-positive retrieval collisions and mirrors academic curricula.
- **Trade-offs**: Students cannot ask multi-subject cross-cutting questions in a single chat.
- **Risks**: None for university course study.
- **Revisiting Conditions**: Future interdisciplinary study modes in Phase 5.

---

### ADR 04: Page-Aware Chunking Strategy
- **Context**: Extracted document pages in `kb_document_pages` must be chunked for vector indexing without losing sentence coherence or physical page provenance, while respecting the 512-token input limit of `intfloat/multilingual-e5-small`.
- **Options**:
  1. *Deterministic Page-Aware Sentence/Paragraph Windowing (350–400 tokens, ceiling 420 tokens)*: Overlap 60–80 tokens, tracking `page_number_start` and `page_number_end`. Sentence/clause fallback for long paragraphs.
  2. *Legacy 500–600 Token Windowing*: Conflicts with 512-token model limit and prefix overhead.
  3. *Fixed-Character Slicing*: Splitting every 1,500 characters arbitrarily.
- **Decision**: **Option 1 (Deterministic Page-Aware Windowing with 420-token ceiling)**.
- **Reasoning**: `multilingual-e5-small` has a 512-token ceiling. Embedding requires `"passage: "` (2 tokens) and `<s>`/`</s>` (2 tokens). Capping raw chunk text at 420 tokens yields an exact maximum embedding input of 424 tokens, preserving an 88-token safety margin.
- **Trade-offs**: Slightly more chunks per document than 600-token chunks.
- **Risks**: Exceptionally long unbroken paragraphs; solved via sentence and clause punctuation boundary fallback.
- **Revisiting Conditions**: If a model with native 8,192-token context is adopted.

---

### ADR 05: Primary Embedding Model Selection
- **Context**: Need an accurate, low-latency, cost-effective embedding model for university textbooks and notes.
- **Options**:
  1. *`intfloat/multilingual-e5-small`*: 384 dimensions, 512 tokens, MIT license, FastEmbed ONNX INT8 (118MB) and FP32 (470MB).
  2. *`BAAI/bge-small-en-v1.5`*: 384 dimensions, English only.
  3. *`BAAI/bge-m3`*: 1024 dimensions, 2.2GB RAM.
  4. *OpenAI / Google Remote Embedding APIs*.
- **Decision**: **Option 1 (`intfloat/multilingual-e5-small`)**.
- **Reasoning**: Superior handling of bilingual (English and Bengali) technical university materials. 384-dim keeps pgvector memory minimal. Runs at $>25\text{ chunks/sec}$ on CPU via FastEmbed ONNX Runtime without external API costs or rate limits.
- **Trade-offs**: Requires `"query: "` prefix for queries and `"passage: "` for chunks.
- **Risks**: Model drift if upgraded; mitigated by storing `model_id` and `model_version` on embeddings.
- **Revisiting Conditions**: If campus corpus requires native 8,000-token long-context embedding.

---

### ADR 06: Embedding Execution Environment
- **Context**: Where should embedding vectors be computed?
- **Options**:
  1. *Backend Host Local Worker (FastEmbed ONNX Runtime)*.
  2. *Remote Embedding API (OpenAI/Gemini)*.
  3. *Peer Community Pooling (Student laptops)*.
- **Decision**: **Option 1 (Backend Host Local Worker)**.
- **Reasoning**: Eliminates per-token API charges, guarantees 100% data privacy (study notes never leave the host for embedding), and respects the strict prohibition on community student device compute.
- **Trade-offs**: Consumes CPU cycles on the backend server.
- **Risks**: Server CPU contention; mitigated by running indexing in a low-priority background process.
- **Revisiting Conditions**: If document ingestion volume exceeds 1,000 documents/day.

---

### ADR 07: Vector Storage Architecture in Supabase
- **Context**: Vector persistence and indexing in PostgreSQL 17.12.
- **Options**:
  1. *Option A: Standalone Vector Database (e.g. Pinecone, Qdrant)*.
  2. *Option B: In-Database pgvector 0.8.2 in Supabase (`kb_chunk_embeddings`) with native core FTS*.
  3. *Option C: Embedding column directly on `kb_document_chunks` with `pg_trgm`*.
- **Decision**: **Option B (pgvector in normalized `kb_chunk_embeddings` table + native FTS)**.
- **Reasoning**: Supabase PostgreSQL 17 already includes `vector 0.8.2`. Normalizing embeddings into a separate table allows re-indexing with a newer model without touching raw chunk text. Native PostgreSQL full-text search (`tsvector` + GIN) eliminates unnecessary third-party extensions like `pg_trgm`.
- **Trade-offs**: Requires 1:1 join between chunks and embeddings.
- **Risks**: Index building overhead; mitigated by HNSW scale threshold ($N \ge 20,000$).
- **Revisiting Conditions**: If vector count exceeds 2 million records.

---

### ADR 08: Hybrid Search & Ranking Strategy
- **Context**: Pure vector similarity search frequently misses exact algorithm acronyms, formulas, and course codes.
- **Options**:
  1. *Reciprocal Rank Fusion (RRF, candidate baseline $k=60, w_{vec}=0.6, w_{fts}=0.4$) combining pgvector and Full-Text Search*.
  2. *Pure Vector Search*.
  3. *Cross-Encoder Re-ranker (e.g. BGE-Reranker-Large)*.
- **Decision**: **Option 1 (RRF with pgvector + FTS)**.
- **Reasoning**: RRF combines semantic similarity with exact keyword matching scale-invariantly without requiring score calibration. Baseline weights provide balanced recall, and will be fine-tuned via grid search in M4.4/M4.8.
- **Trade-offs**: Executes two parallel queries in PostgreSQL.
- **Risks**: None; executes in a single SQL function (`match_subject_chunks`) in $<25\text{ms}$.
- **Revisiting Conditions**: If keyword retrieval precision drops below 75%.

---

### ADR 09: Indexing Job Management & Failure Recovery
- **Context**: Document extraction and RAG vector indexing must be decoupled lifecycles.
- **Options**:
  1. *Dedicated `kb_indexing_jobs` table with lease tokens and `SKIP LOCKED`*.
  2. *Synchronous indexing inside the PDF extraction transaction*.
  3. *In-memory Celery / Redis queue*.
- **Decision**: **Option 1 (`kb_indexing_jobs` with `SKIP LOCKED`)**.
- **Reasoning**: Mirrors the battle-tested Phase 3 extraction worker architecture (`job_repository.py`). If indexing crashes or embedding fails, extraction remains successful. Supports independent retries and automatic lease recovery after 60s.
- **Trade-offs**: Requires running an indexing worker daemon alongside the extraction worker.
- **Risks**: None; proven operational reliability.
- **Revisiting Conditions**: Never.

---

### ADR 10: Citation & Evidence Snapshot Persistence
- **Context**: How should evidence cited in assistant answers be stored?
- **Options**:
  1. *Immutable Snapshot Table (`chat_message_sources`) with 4-stage evidence taxonomy*.
  2. *Dynamic lookup of current chunks at render time*.
  3. *JSON blob embedded in message row*.
- **Decision**: **Option 1 (Immutable Snapshot Table `chat_message_sources`)**.
- **Reasoning**: Documents can be edited, re-indexed, or removed. Storing explicit snapshots with `included_in_context` and `referenced_in_answer` booleans allows precise UI tab filtering ("Referenced in Answer" vs "All Considered Context") and preserves historical study integrity.
- **Trade-offs**: Consumes additional relational storage ($\approx 1\text{KB}$ per citation).
- **Risks**: Database growth; negligible for text excerpts.
- **Revisiting Conditions**: Never.

---

### ADR 11: Source Access Revalidation & Revocation Fencing
- **Context**: What happens when a student opens an old chat message citing a document that an administrator later archived or deleted?
- **Options**:
  1. *Server-Side Live Join Revalidation with Excerpt Masking*: Check live catalog status; if archived/deleted, return masked placeholder.
  2. *Permanent Unconditional Display*: Always show cached excerpt.
  3. *Delete entire historical chat message*.
- **Decision**: **Option 1 (Server-Side Live Join Revalidation with Excerpt Masking)**.
- **Reasoning**: Protects academic copyright and administrative moderation. Uses an authoritative join predicate against `kb_documents`, `kb_topics`, and `kb_subjects`. Prevents unauthorized backdoors to purged materials while preserving conversation readability.
- **Trade-offs**: Endpoint must join catalog tables.
- **Risks**: None.
- **Revisiting Conditions**: Never.

---

### ADR 12: Context-Window Budgeting Algorithm
- **Context**: Models have finite context windows; chat history and retrieved passages must not overflow the provider limit.
- **Options**:
  1. *Deterministic Server-Side Formula: $B_{avail} = C_{model} - (T_{out} + T_{sys} + T_{guard} + T_{safe})$*.
  2. *Frontend-supplied context budget*.
  3. *Naive arbitrary truncation*.
- **Decision**: **Option 1 (Deterministic Server-Side Formula)**.
- **Reasoning**: Server calculates exact available budget, allocates prompt first, caps evidence at 60%, and fits summary and sliding history into remainder. Prevents provider 422/context overflow errors.
- **Trade-offs**: When prompts are massive, older history is compacted.
- **Risks**: Oversized prompts; rejected cleanly with HTTP 422.
- **Revisiting Conditions**: If dynamic context compression models become viable.

---

### ADR 13: Long-History Compaction & Summarization
- **Context**: Long academic study sessions exceed context windows if all turns are passed verbatim.
- **Options**:
  1. *Sliding Window ($K=6$) + Concurrency-Safe Map-Reduce Summaries with Redis Mutex and Optimistic Versioning*.
  2. *Hard truncation (drop messages older than 5 turns)*.
  3. *Vector search over message history*.
- **Decision**: **Option 1 (Sliding Window + Concurrency-Safe Summaries)**.
- **Reasoning**: Preserves immediate conversational flow (last 3 turns verbatim) while retaining historical context through a concise 250-word summary in `chat_conversation_summaries`. Optimistic version check on `compacted_through_sequence` prevents race conditions.
- **Trade-offs**: Generates occasional background LLM summary requests.
- **Risks**: Stale summaries; eliminated by versioned sequence tracking and Redis mutex.
- **Revisiting Conditions**: If conversations rarely exceed 6 turns.

---

### ADR 14: Message Generation State Machine
- **Context**: Explicit lifecycle states for message generation.
- **Options**:
  1. *State Machine*: `accepted` $\to$ `waiting_admission` $\to$ `retrieving` $\to$ `preparing_context` $\to$ `streaming` $\to$ `completed` (or `cancelled` / `failed`).
  2. *Binary status*: `pending` / `completed`.
- **Decision**: **Option 1 (State Machine)**.
- **Reasoning**: Allows granular observability, UI progress indicators (`Waiting for slot...`, `Searching materials...`, `Generating answer...`), and distinct handling of client aborts (`cancelled`) vs system crashes (`failed`).
- **Trade-offs**: Requires state updates across the generation lifecycle.
- **Risks**: None.
- **Revisiting Conditions**: Never.

---

### ADR 15: Real-Time Streaming & Client Cancellation Protocol
- **Context**: How should assistant tokens stream to the web client?
- **Options**:
  1. *Server-Sent Events (SSE) over standard HTTP fetch stream*.
  2. *WebSockets*.
  3. *Polling*.
- **Decision**: **Option 1 (Server-Sent Events over HTTP)**.
- **Reasoning**: SSE is unidirectional, lightweight, works seamlessly with standard Supabase JWT Bearer headers, traverses campus proxies effortlessly, and supports native browser cancellation via `AbortController`.
- **Trade-offs**: Unidirectional only; client requests (like cancellation) use a standard POST endpoint.
- **Risks**: Campus proxy buffering; mitigated by flushing chunks immediately (`X-Accel-Buffering: no`).
- **Revisiting Conditions**: If multi-user real-time collaborative chat rooms are introduced.

---

### ADR 16: Concurrency Governance & Distributed Semaphores
- **Context**: Preventing a single student or multiple tabs from monopolizing shared platform resources.
- **Options**:
  1. *Redis Distributed Semaphores with UUID4 Lease Tokens and Atomic Lua Release Script*.
  2. *Unrestricted concurrent requests*.
  3. *PostgreSQL row locks*.
- **Decision**: **Option 1 (Redis Semaphores with Lua Release)**.
- **Reasoning**: Redis provides sub-millisecond atomic lease management with self-expiring TTLs (60s). Checking token equality via Lua before deletion guarantees that a delayed worker will never accidentally delete a subsequent generation's lock.
- **Trade-offs**: Requires active Redis connection (port 6379).
- **Risks**: Redis outage; updated in M4.6 (ADR 23) to **fail closed** with HTTP `503 SERVICE_UNAVAILABLE` for cloud generation rather than falling back to unsafe per-worker in-memory locks across multi-worker deployments, while Private Local Ollama continues uninterrupted.
- **Revisiting Conditions**: If student user base expands past 1,000 active concurrent users.

---

### ADR 17: Generation Queue Architecture Selection
- **Context**: Is a durable background queue (e.g. Celery / BullMQ) necessary for interactive chat?
- **Options**:
  1. *Bounded Direct Streaming with Redis Admission Waiting (Max 30s)*.
  2. *Durable PostgreSQL / Celery Worker Queue*.
- **Decision**: **Option 1 (Bounded Direct Streaming with Admission Waiting)**.
- **Reasoning**: Interactive AI chat requires instant Time-to-First-Token ($<1.5\text{s}$). When global capacity is saturated on streaming requests, the server opens SSE (`HTTP 200`), emits `event: admission_waiting`, and waits up to 30 seconds for a slot; if none opens, it emits `event: generation_failed` (`code="RATE_LIMITED"`, `retryable=true`). Synchronous `stream=false` requests wait up to 30 seconds and return `HTTP 429 CONCURRENCY_TIMEOUT` (see ADR 23).
- **Trade-offs**: Requests waiting $>30\text{s}$ are rejected cleanly.
- **Risks**: None; students prefer actionable notices to silent delays.
- **Revisiting Conditions**: If cloud providers impose strict 1-request-at-a-time rate limits.

---

### ADR 18: Provider Fallback & Private Local Ollama Bypass
- **Context**: Ensuring automatic provider fallback respects user preferences and free-tier sharing integrity.
- **Options**:
  1. *Strict Preference Enclosure with Private Local Direct Loopback Bypass*.
  2. *Unrestricted Fallback (fallback to paid keys or local models)*.
- **Decision**: **Option 1 (Strict Preference Enclosure with Private Local Bypass)**.
- **Reasoning**: Paid keys and private local models are NEVER pooled or shared. When `inference_preference = 'local'`, the browser streams directly from Ollama on `127.0.0.1:11434`, bypassing the global cloud concurrency semaphore completely and guaranteeing zero cloud API leakage.
- **Trade-offs**: Cloud fallback selects only from active community free-tier credentials.
- **Risks**: None; absolute security and privacy boundary.
- **Revisiting Conditions**: Never.

---

### ADR 19: Sources & Context User Experience Design
- **Context**: How should source provenance be presented in the frontend?
- **Options**:
  1. *Interactive Citation Chips `[S1]` + Slide-Over Sources Drawer (380px) + One-Click Page Navigation*.
  2. *Raw text footnotes at the bottom of the message*.
  3. *Hover-only popover tooltips*.
- **Decision**: **Option 1 (Chips + Drawer + Page Navigation)**.
- **Reasoning**: Provides a clean, uncluttered reading flow while offering deep inspection on demand. Clicking `"Open Document at Page 142"` directly loads the Phase 3 document viewer at the exact physical page, creating a complete study loop.
- **Trade-offs**: Requires building a responsive slide-over drawer component.
- **Risks**: Screen real estate on mobile; solved by bottom-sheet layout.
- **Revisiting Conditions**: Never.

---

### ADR 20: Golden Evaluation Dataset & Release Thresholds
- **Context**: Establishing rigorous quantitative exit criteria for Phase 4.
- **Options**:
  1. *50-Question Manually Verified Golden Dataset across 11 Query Categories (Bilingual English/Bengali)*.
  2. *Ad-hoc manual smoke testing*.
- **Decision**: **Option 1 (50-Question Golden Dataset)**.
- **Reasoning**: Release certification requires empirical proof: Recall@5 $\ge 80\%$, Citation Validity = $100\%$, Abstention $\ge 90\%$, Zero cross-subject leakage.
- **Trade-offs**: Requires manual authoring and gold-passage verification of 50 academic test questions.
- **Risks**: None; ensures production readiness.
- **Revisiting Conditions**: If course curriculum changes significantly.

---

### ADR 21: Supported Workload Profile & Pilot Concurrency
- **Context**: What is the initial operational scale of Phase 4?
- **Options**:
  1. *Pilot Cohort: 30–100 registered students, 5–10 concurrent streams, burst to 20*.
  2. *Full campus enterprise scale (10,000+ users)*.
- **Decision**: **Option 1 (Pilot Cohort: 30–100 students)**.
- **Reasoning**: Fits local college hardware, single-node Docker deployment, and free-tier cloud API rate limits. Bounded Redis semaphores ensure stability under burst load.
- **Trade-offs**: High concurrent demand ($>20$) receives clean 429 throttle notices.
- **Risks**: High simultaneous exam-period demand; mitigated by recommending Private Local Ollama.
- **Revisiting Conditions**: Campus-wide rollout in future phases.

---

### ADR 22: Carry-Over Technical Prerequisites & Fencing from Phase 3
- **Context**: Integrating with existing Phase 3 Knowledge Base assets and respecting certified security boundaries.
- **Options**:
  1. *Acknowledge Parser Sandbox Limitation & Decouple Indexing from Uploads*.
  2. *Re-implement extraction inside RAG*.
- **Decision**: **Option 1 (Decouple Indexing & Respect Sandbox Boundary)**.
- **Reasoning**: Phase 3 Conditional GO noted that the extraction worker runs pypdf in an isolated subprocess. Phase 4 operates strictly on already-extracted `kb_document_pages` and does not parse raw PDF bytes, insulating the RAG engine from file-parsing vulnerabilities.
- **Trade-offs**: Documents with extraction status `completed_no_text` cannot be indexed.
- **Risks**: None.
- **Revisiting Conditions**: When gVisor / container sandboxing is enabled for Phase 3.

---

### ADR 23: M4.6 Streaming Contracts, Redis Fail-Closed Policy, Cancel Routes & Prepare-Local Binding
- **Context**: During M4.6 implementation, four cross-document ambiguities were resolved to preserve distributed multi-worker correctness, M4.5 hardening guarantees, and clean HTTP/SSE semantics:
  1. **Cancellation route**: `API_AND_EVENT_CONTRACTS.md` documented `/conversations/{id}/messages/{message_id}/cancel` while M4.6 operational specs mandated `/messages/{message_id}/cancel`.
  2. **Global queue saturation HTTP code vs SSE**: `HTTP 200 text/event-stream` headers cannot retroactively switch to `HTTP 429` once `event: admission_waiting` has been flushed to the wire.
  3. **Redis outage handling**: `GENERATION_RELIABILITY_AND_LOAD_PLAN.md` §5.3 mentioned in-memory process fallback, whereas multi-worker uvicorn deployments cannot enforce a platform-wide 10-stream cloud cap locally.
  4. **`finalize-local` replay token**: M4.5 hardening required `prepared_request_id` (`min_length=8`) to close replay/cross-message races.
- **Decision**:
  1. Support **both** `POST /api/v1/chat/messages/{message_id}/cancel` (authoritative, derives `conversation_id` safely from DB with ownership verification) and `POST /api/v1/chat/conversations/{conversation_id}/messages/{message_id}/cancel` (verifies path `conversation_id` matches message row or returns `404`).
  2. Preflight conversation (`409 CONVERSATION_CONCURRENCY_LIMIT`), user (`429 USER_CONCURRENCY_LIMIT`), authorization (`403`/`404`), and Redis availability (`503 SERVICE_UNAVAILABLE`) fail BEFORE SSE headers. When only the global cap (10) is saturated on streaming (`stream=true`, default), the endpoint commits `HTTP 200 text/event-stream`, emits `event: admission_waiting`, and upon 30s timeout emits terminal `event: generation_failed` (`code="RATE_LIMITED"`, `retryable=true`). Non-streaming (`stream=false`) requests synchronously wait and reject with `HTTP 429 CONCURRENCY_TIMEOUT`.
  3. Fail **closed** (`HTTP 503 SERVICE_UNAVAILABLE`) for cloud generation whenever Redis is unavailable; never fall back to `asyncio.Semaphore` or per-process locks. Private Local Ollama (`prepare-local` / `finalize-local`) continues bypassing Redis cloud semaphores completely.
  4. Keep `FinalizeLocalRequest.prepared_request_id` mandatory (`min_length=8`) and verify it under `FOR UPDATE` CAS.
- **Consequences**: Guaranteed cross-process cloud concurrency safety, clean SSE terminal state machine without HTTP framing violations, and zero breaking drift between API contracts and M4.5/M4.6 security controls.
- **Revisiting Conditions**: If a durable brokered job queue or single-process embedded mode is explicitly added post-Phase 4.
