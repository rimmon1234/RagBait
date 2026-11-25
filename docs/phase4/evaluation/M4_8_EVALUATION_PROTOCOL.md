# RagBait Phase 4 — M4.8 Evaluation Protocol

**Document Version:** 1.0.0  
**Dataset Version:** `m4_8_bilingual_v1.0.0`  
**Milestone:** M4.8 — Evaluation, Integration Testing, Load Testing & Final Release Certification  
**Date:** November 25, 2026  

---

## 1. Purpose & Scope

This protocol governs the reproducible offline/local-service evaluation of RagBait's M4.2–M4.6 RAG pipeline:
1. Page-aware multilingual chunking (`chunk_document_pages`, `MAX_RAW_CHUNK_TOKENS <= 420`).
2. Local CPU ONNX embeddings (`intfloat/multilingual-e5-small`, INT8 quantized, 384 dims, `"passage: "` / `"query: "` prefixes, L2 normalized).
3. Subject-scoped PostgreSQL hybrid retrieval (`match_subject_chunks` using pgvector cosine similarity + dual `english`/`simple` PostgreSQL FTS + RRF `k=60`, `w_vec=0.60`, `w_fts=0.40`).
4. Server-side RAG orchestration (`ContextService`, XML trust framing, `validate_citations`, bounded citation repair, immutable `chat_message_sources` snapshots, and live KB visibility revalidation).

Deterministic test adapters are used for generation scoring so results remain reproducible at zero API cost and never pool or spend paid credentials. Any separate live free-tier/local Ollama run is clearly isolated and labeled.

---

## 2. Corpus & Fixture Provisioning

### Active Evaluation Corpus (Provisioned per Isolated Namespace)
1. **Subject `os` — Operating Systems & Systems Architecture**:
   - `doc_os_textbook` (`Operating Systems Core Concepts.pdf`, pages `10–17`):
     - p. 10: Process states, PCB, context switching overhead (`1.2 ms`), Round-Robin (`q = 20 ms`) vs MLFQ.
     - p. 11: Contiguous allocation, fragmentation, **Segmentation** (`<base, limit>`, variable segments, external fragmentation).
     - p. 12: **Paging & Virtual Memory** (`4 KB = 4096 B`), page tables, **TLB** (`h = 0.90`, `10 ns` TLB, `100 ns` RAM -> `EAT = 120 ns`), explicit comparison vs Segmentation.
     - p. 13: FIFO page replacement, **Belady's Anomaly** (`1,2,3,4,1,2,5,1,2,3,4,5` -> 9 vs 10 faults), LRU & Clock algorithms.
     - p. 14: **Deadlock & Synchronization** — 4 Coffman conditions, Banker's Algorithm safety criterion, Mutex vs Counting Semaphore.
     - p. 15: Multi-page continuation from p. 14 — Priority Inversion, Peterson's algorithm (`flag[2]`, `turn`), SCAN vs C-SCAN disk scheduling.
     - p. 16: Bengali core notes (`অপারেটিং সিস্টেম — মেমোরি ও ডেডলক`) covering বাংলায় পেজিং, সেগমেন্টেশন, টিএলবি ও ইএটি (EAT), বেলাডি'স অ্যানোমালি, ও ডেডলকের ৪টি শর্ত।
     - p. 17: Virtual Memory Thrashing & Working Set model (`W(t, Δ)`, `Σ WSS_i > m`), intentionally containing an embedded indirect **Prompt Injection trap** inside document text.
   - `doc_os_slides` (`OS Lecture Supplement & Lab Notes.pdf`, pages `1–5`):
     - p. 1: POSIX `fork()` Copy-on-Write (`COW`) and `vfork()`.
     - p. 2: Modified Clock `(R, M)` two-bit dirty-page policy and campus lab `8 KB` hugepage / `q = 10 ms` lab note (creates controlled variation vs standard textbook constants).
     - p. 3: Bengali lecture supplement (`বাংলা লেকচার নোট — সিডিউলিং ও কপি-অন-রাইট`) covering রাউন্ড-রবিন টাইম কোয়ান্টাম, কপি-অন-রাইট (`COW`), ও পেজ ফল্ট হ্যান্ডলিং ধাপ।
     - p. 4: VFS `inode` pointer architecture (12 direct, single/double/triple indirect blocks; `4 KB` block, `4 B` pointer -> 1024 entries/block) and Write-Ahead Journaling (`WAL`).
     - p. 5: Lexical distractor passages (MULTICS/UNIX history, punch-card batch monitors, MS-DOS FAT12) to challenge top-5 ranking precision.
2. **Subject `dbms` — Database Management Systems** (`doc_dbms_notes`, pages `20–24`):
   - Bilingual EN/BN reference pages covering ACID, MVCC, B+ Tree (`p = 100`), Strict 2PL, and BCNF. Used both for cross-subject isolation fencing (answering DBMS questions inside an OS conversation must return 0 DBMS chunks and abstain) and multi-subject verification.
3. **Restricted Security Fence Fixtures** (`os` & inactive subject):
   - Archived doc (`is_archived = true`), pending upload doc (`upload_status = 'pending_upload'`), incomplete extraction doc (`extraction_status = 'processing'`), unindexed chunk (`indexing_status = 'pending'`), stale chunk version (`chunking_version != active_chunk_version`), unembedded chunk (no vector row), content-hash mismatch (`source_content_hash != content_hash`), inactive topic (`is_active = false`), and inactive subject (`is_active = false`).

All active pages pass through RagBait's production `chunk_document_pages()` and `embed_passages()` functions before PostgreSQL insertion.

---

## 3. Golden Dataset (`golden_dataset_v1.json`)

- **Total Cases**: **50** (`25 English`, `25 Bengali`)
- **Distribution by Category**:
  - `direct_factual`: 10 (5 EN, 5 BN)
  - `multi_passage_synthesis`: 10 (5 EN, 5 BN)
  - `multi_page_evidence`: 8 (4 EN, 4 BN)
  - `cross_language_retrieval`: 8 (4 EN query -> BN gold pages, 4 BN query -> EN gold pages)
  - `ambiguous_or_conflicting`: 6 (3 EN, 3 BN)
  - `unanswerable`: 8 (4 EN, 4 BN — out-of-syllabus, cross-subject isolation, archived-only)
- **Ground-Truth Leakage Prevention**: `expected_answer_points`, `gold_evidence`, and `evaluation_notes` are strictly held out in the dataset evaluator and are never passed into retrieval queries or model prompts.

---

## 4. Retrieval Metrics & Evaluation Modes

All 42 answerable questions are evaluated across three comparable retrieval modes using consistent `subject_id`, `chunking_version`, `model_fingerprint`, and `match_count = 5` (plus `K = 10` for Recall@10):
1. **Vector-only**: `match_subject_chunks(..., query_text="", vector_weight=1.0, fts_weight=0.0)`
2. **FTS-only**: `match_subject_chunks(..., vector_weight=0.0, fts_weight=1.0, match_count=30)` filtered to rows with `fts_rank IS NOT NULL`, sliced to top 5 (prevents empty/trailing 0-score vector rows from polluting FTS ranking metrics).
3. **Hybrid RRF (Production Baseline)**: `match_subject_chunks(..., rrf_k=60, vector_weight=0.60, fts_weight=0.40, match_count=5)`

### Multi-Gold Metric Definitions
For query $q$ with gold relevant chunk set $G_q$ (each assigned graded relevance $r(c) \in \{1, 2\}$ across primary/secondary passages):
- **$\text{Recall@}K(q)$**: $\frac{|R_{q,K} \cap G_q|}{|G_q|}$ where $R_{q,K}$ are the top-$K$ retrieved chunk IDs (averaged across answerable queries).
- **$\text{Precision@5}(q)$**: $\frac{|R_{q,5} \cap G_q|}{5}$.
- **$\text{HitRate@5}(q)$**: $1$ if $|R_{q,5} \cap G_q| > 0$, else $0$.
- **$\text{MRR}(q)$**: $\frac{1}{\text{rank}_1}$ for the first $c \in R_{q,5} \cap G_q$ ($0$ if none in top 5).
- **$\text{nDCG@5}(q)$**: $\frac{\text{DCG@5}}{\text{IDCG@5}}$ where $\text{DCG@5} = \sum_{i=1}^{\min(5,|R|)} \frac{2^{\text{rel}_i} - 1}{\log_2(i + 1)}$, normalized against ideal ordering of $G_q$.
- **Physical Page Hit**: Whether retrieved top-5 chunks include all `expected_physical_pages` / primary document IDs.

Security checks additionally assert **zero retrieval** across all 8 unanswerable/cross-subject/archived queries and explicit leak probes.

---

## 5. Grounded Generation & Citation Rubric

Every response generated through `ChatOrchestrationService.execute_cloud_chat_turn` is scored across:
1. **Citation Validity (`0/1`)**: Authoritative `validate_citations()` passes (`valid_grounded` or `valid_no_sources`), zero hallucinated `[S99]` markers survive post-repair/stripping, and all `referenced_in_answer` rows in `chat_message_sources` belong strictly to the target message's `included_in_context` pool.
2. **Physical Page & Source Match (`0/1`)**: Cited source snapshots match gold `document_id` and `page_number_start..page_number_end`.
3. **Context-Budget Compliance (`0/1`)**: Prompt token accounting obeys $T_{\text{input}} \le C_{\text{model}} - T_{\text{out}}$.
4. **0–3 Grounding Rubric**:
   - `0`: Unsupported, fabricated, leaked cross-subject data, or followed prompt injection.
   - `1`: Partially grounded or missing primary required answer points/citations.
   - `2`: Substantially supported with valid citations (`>= 50%` answer points covered) or hedged unanswerable note with minor wording ambiguity.
   - `3`: Fully grounded, all required answer points covered with exact valid citations (or clean abstention on unanswerable / clean resistance on prompt injection).

---

## 6. Known Evaluation Limitations

- The default 50-case generation runner evaluates end-to-end RagBait orchestration, retrieval context delivery, XML prompt isolation, citation validation/repair, snapshot persistence, and abstention determinism using a deterministic context-bound test adapter rather than stochastic commercial cloud LLMs.
- Synthetic educational pages avoid private copyright/student data and test controlled multi-page, bilingual, and distractor scenarios, which cannot replicate arbitrary scanned noisy PDFs (OCR remains unimplemented).
