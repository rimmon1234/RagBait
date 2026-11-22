# RagBait Phase 4 — Architectural Decision Records (ADRs 01 – 22)

**Document Version:** 1.0.0  
**Phase:** Phase 4 — AI Study Assistant and Retrieval-Augmented Generation (RAG)  
**Date:** November 22, 2026  
**Status:** Approved Architectural Decisions  

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
- **Context**: Extracted document pages in `kb_document_pages` must be chunked for vector indexing without losing sentence coherence or physical page provenance.
- **Options**:
  1. *Deterministic Page-Aware Sentence/Paragraph Windowing*: 500–600 tokens per chunk with 80–100 tokens overlap, preserving page boundaries.
  2. *Fixed-Character Slicing*: Splitting every 1,500 characters arbitrarily.
  3. *Hierarchical Parent-Child Chunking*.
- **Decision**: **Option 1 (Deterministic Page-Aware Windowing)**.
- **Reasoning**: Paragraph/sentence windowing prevents broken mathematical equations and truncated definitions. Tracking `page_number_start` and `page_number_end` enables exact physical page navigation in the document reader.
- **Trade-offs**: Slightly more complex chunking logic than naive character splitting.
- **Risks**: Very long unbroken paragraphs; mitigated by sentence-boundary fallback.
- **Revisiting Conditions**: If tabular data extraction requires table-aware markdown chunking.

---

### ADR 05: Primary Embedding Model Selection
- **Context**: Need an accurate, low-latency, cost-effective embedding model for university textbooks and notes.
- **Options**:
  1. *`intfloat/multilingual-e5-small`*: 384 dimensions, 512 tokens, MIT license, 133MB (ONNX).
  2. *`BAAI/bge-small-en-v1.5`*: 384 dimensions, English only.
  3. *`BAAI/bge-m3`*: 1024 dimensions, 2.2GB RAM.
  4. *OpenAI / Google Remote Embedding APIs*.
- **Decision**: **Option 1 (`intfloat/multilingual-e5-small`)**.
- **Reasoning**: Superior handling of bilingual and non-English technical university materials. 384-dim keeps pgvector memory minimal. Runs at $>25\text{ chunks/sec}$ on CPU via ONNX Runtime without external API costs or rate limits.
- **Trade-offs**: Requires `query: ` and `passage: ` prefixing.
- **Risks**: Model drift if upgraded; mitigated by storing `model_id` and `model_version` on embeddings.
- **Revisiting Conditions**: If campus corpus requires native 8,000-token long-context embedding.

---

### ADR 06: Embedding Execution Environment
- **Context**: Where should embedding vectors be computed?
- **Options**:
  1. *Backend Host Local Worker (ONNX Runtime / fastembed)*.
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
  2. *Option B: In-Database pgvector 0.8.2 in Supabase (`kb_chunk_embeddings`)*.
  3. *Option C: Embedding column directly on `kb_document_chunks`*.
- **Decision**: **Option B (pgvector in normalized `kb_chunk_embeddings` table)**.
- **Reasoning**: Supabase PostgreSQL 17 already includes `vector 0.8.2`. Normalizing embeddings into a separate table allows re-indexing with a newer model without touching raw chunk text. Keeping vectors in PostgreSQL provides ACID transactions with document deletions.
- **Trade-offs**: Requires 1:1 join between chunks and embeddings.
- **Risks**: Index building overhead; mitigated by HNSW scale threshold ($N \ge 20,000$).
- **Revisiting Conditions**: If vector count exceeds 2 million records.

---

### ADR 08: Hybrid Search & Ranking Strategy
- **Context**: Pure vector similarity search frequently misses exact algorithm acronyms, formulas, and course codes.
- **Options**:
  1. *Reciprocal Rank Fusion (RRF, $k=60$) combining pgvector and Full-Text Search*.
  2. *Pure Vector Search*.
  3. *Cross-Encoder Re-ranker (e.g. BGE-Reranker-Large)*.
- **Decision**: **Option 1 (RRF with pgvector + FTS)**.
- **Reasoning**: RRF combines semantic similarity with exact keyword matching scale-invariantly without requiring score calibration. Cross-encoders add $>200\text{ms}$ latency and high CPU load, which is unnecessary for university note retrieval.
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
  1. *Immutable Snapshot Table (`chat_message_sources`)*.
  2. *Dynamic lookup of current chunks at render time*.
  3. *JSON blob embedded in message row*.
- **Decision**: **Option 1 (Immutable Snapshot Table `chat_message_sources`)**.
- **Reasoning**: Documents can be edited, re-indexed, or removed. A student viewing a 2-month-old answer must see the exact excerpt and page number that generated that answer. Relational rows allow individual source status inspection and access revocation filtering.
- **Trade-offs**: Consumes additional relational storage ($\approx 1\text{KB}$ per citation).
- **Risks**: Database growth; negligible for text excerpts.
- **Revisiting Conditions**: Never.

---

### ADR 11: Source Access Revalidation & Revocation Fencing
- **Context**: What happens when a student opens an old chat message citing a document that an administrator later archived or deleted?
- **Options**:
  1. *Server-Side Revalidation with Excerpt Masking*: Inspect document status; if archived/deleted, return masked placeholder.
  2. *Permanent Unconditional Display*: Always show cached excerpt.
  3. *Delete entire historical chat message*.
- **Decision**: **Option 1 (Server-Side Revalidation with Excerpt Masking)**.
- **Reasoning**: Protects academic copyright and administrative moderation. Prevents students from using old chat logs as an unauthorized backdoor to view purged materials, while preserving conversation readability.
- **Trade-offs**: Endpoint must join `kb_documents` to verify `is_archived = false`.
- **Risks**: None.
- **Revisiting Conditions**: Never.

---

### ADR 12: Context-Window Budgeting Algorithm
- **Context**: Models have finite context windows; chat history and retrieved passages must not overflow the provider limit.
- **Options**:
  1. *Deterministic Floor-and-Ceiling Formula with Priority Allocation*.
  2. *Frontend-supplied context budget*.
  3. *Naive arbitrary truncation*.
- **Decision**: **Option 1 (Deterministic Server-Side Formula)**.
- **Reasoning**: Server calculates $B_{avail} = C_{model} - (T_{out} + T_{sys} + T_{safe})$. Allocates user prompt first, caps evidence at 60% of remainder, and fits summary and sliding history into the rest. Prevents provider 422/context overflow errors.
- **Trade-offs**: When prompts are massive, older history is compacted.
- **Risks**: Oversized prompts; rejected cleanly with HTTP 422.
- **Revisiting Conditions**: If dynamic context compression models become viable.

---

### ADR 13: Long-History Compaction & Summarization
- **Context**: Long academic study sessions exceed context windows if all turns are passed verbatim.
- **Options**:
  1. *Sliding Window ($K=6$) + Asynchronous Map-Reduce Summaries*.
  2. *Hard truncation (drop messages older than 5 turns)*.
  3. *Vector search over message history*.
- **Decision**: **Option 1 (Sliding Window + Summaries)**.
- **Reasoning**: Preserves immediate conversational flow (last 3 turns verbatim) while retaining historical context through a concise 250-word summary in `chat_conversation_summaries`. Original messages remain permanently preserved in `chat_messages`.
- **Trade-offs**: Generates occasional background LLM summary requests.
- **Risks**: Stale summaries; mitigated by versioned sequence tracking (`compacted_through_sequence`).
- **Revisiting Conditions**: If conversations rarely exceed 6 turns.

---

### ADR 14: Message Generation State Machine
- **Context**: Explicit lifecycle states for message generation.
- **Options**:
  1. *6-State Lifecycle*: `accepted` $\to$ `retrieving` $\to$ `preparing_context` $\to$ `streaming` $\to$ `completed` (or `cancelled` / `failed`).
  2. *Binary status*: `pending` / `completed`.
- **Decision**: **Option 1 (6-State Lifecycle)**.
- **Reasoning**: Allows granular observability, UI progress indicators (`Searching materials...`, `Generating answer...`), and distinct handling of client aborts (`cancelled`) vs system crashes (`failed`).
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

### ADR 16: Concurrency Governance & Rate Limiting
- **Context**: Preventing a single student or multiple tabs from monopolizing shared platform resources.
- **Options**:
  1. *Redis Distributed Semaphores (1 per conv, 2 per user, 10 global cloud)*.
  2. *Unrestricted concurrent requests*.
  3. *PostgreSQL row locks*.
- **Decision**: **Option 1 (Redis Distributed Semaphores)**.
- **Reasoning**: Redis provides sub-millisecond atomic lease management with self-expiring TTLs (60s). Enforcing 1 active stream per conversation prevents race conditions in message sequence numbering.
- **Trade-offs**: Requires active Redis connection (already running on port 6379).
- **Risks**: Redis failure; falls back to in-memory process locks.
- **Revisiting Conditions**: If student user base expands past 1,000 active concurrent users.

---

### ADR 17: Generation Queue Architecture Selection
- **Context**: Is a durable background queue (e.g. Celery / BullMQ) necessary for interactive chat?
- **Options**:
  1. *Bounded Direct Streaming with Redis Concurrency Semaphores*.
  2. *Durable PostgreSQL / Celery Worker Queue*.
- **Decision**: **Option 1 (Bounded Direct Streaming with Redis Semaphores)**.
- **Reasoning**: Interactive AI chat requires instant Time-to-First-Token ($<1.5\text{s}$). Routing conversational requests through a durable asynchronous background queue adds IPC overhead, WebSocket synchronization complexity, and server cost for no benefit at the pilot scale of 30–100 students.
- **Trade-offs**: Requests waiting $>30\text{s}$ for a slot are rejected with HTTP 429 rather than queued indefinitely.
- **Risks**: None; college students prefer fast failover to waiting minutes in a queue.
- **Revisiting Conditions**: If cloud providers impose strict 1-request-at-a-time rate limits.

---

### ADR 18: Provider Fallback & Free-Tier Sharing Integrity
- **Context**: Ensuring automatic provider fallback never violates RagBait's core privacy and sharing policy.
- **Options**:
  1. *Strict Preference Enclosure with Free-Tier Validation*: Fallback searches exclusively within matching verified credentials.
  2. *Unrestricted Fallback (fallback to paid keys or local models)*.
- **Decision**: **Option 1 (Strict Preference Enclosure)**.
- **Reasoning**: Core project law: Paid keys and private local models are NEVER pooled or shared. In `auto` mode, fallback selects only from active, verified free-tier contributed credentials (`sharing_scope='community'`, `billing_tier='free_tier'`).
- **Trade-offs**: If all contributed free-tier cloud keys are cooling down, generation fails rather than borrowing private keys.
- **Risks**: None; absolute security boundary.
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
  1. *50-Question Manually Verified Golden Dataset across 11 Query Categories*.
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
