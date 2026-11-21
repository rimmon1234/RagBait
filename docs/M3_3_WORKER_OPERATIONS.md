# RagBait — M3.3 Extraction Worker Operations Guide

## Overview

The RagBait Knowledge Base background extraction worker is a standalone, durable Python service that processes confirmed PDF and TXT document uploads from the PostgreSQL job queue (`public.kb_extraction_jobs`).

Key Architectural Principles:
1. **Durable Queuing & Atomic Claiming**: PostgreSQL `FOR UPDATE SKIP LOCKED` prevents race conditions between concurrent worker processes.
2. **Crash Recovery & Fencing**: Expired leases (`locked_until < NOW()`) are recovered by active workers. Stale workers are fenced via cryptographically random `lease_token` re-verification.
3. **Bounded Memory**: Documents stream in chunks (default 64 KiB) directly to disk with incremental SHA-256 and size verification. Documents are never loaded entirely into worker memory.
4. **Deterministic Physical Page Provenance**: Every physical PDF page creates exactly one record in `public.kb_document_pages` (1-indexed). Empty pages are preserved. Scanned PDFs (no text layer) transition to `completed_no_text`.
5. **Logical Text Pagination**: TXT files support form-feed `\f` pagination with automatic UTF-8/Latin-1 encoding detection and anti-binary heuristics.
6. **Hard Process-Isolated Timeouts**: Document parsers run in isolated OS child processes. When execution deadlines expire, the parent process terminates the child at the OS level (`SIGKILL` / `TerminateProcess`).
7. **Strict Scope Boundaries**: Phase 3 extraction is purely deterministic. No OCR, vector embeddings, pgvector, or external LLM/API calls are performed.

---

## Configuration Reference

The worker is configured via environment variables with the `KB_WORKER_` prefix or defaults in `apps/api/app/modules/knowledge_base/worker_config.py`:

| Environment Variable | Default | Allowed Range | Description |
|----------------------|---------|---------------|-------------|
| `KB_WORKER_WORKER_ID` | Auto-generated (`worker-{pid}-{uuid}`) | string | Unique identity for worker instance |
| `KB_WORKER_DATABASE_URL` | Application `DATABASE_URL` | string | PostgreSQL connection DSN |
| `KB_WORKER_JOB_LEASE_TTL_SECONDS` | `120` | `30` – `600` | Lease validity duration before recovery |
| `KB_WORKER_HEARTBEAT_INTERVAL_SECONDS` | `30` | `5` – `120` | Lease extension heartbeat cadence |
| `KB_WORKER_MAX_EXTRACTION_DURATION_SECONDS` | `120` | `10` – `600` | Process timeout deadline for child parser |
| `KB_WORKER_MAX_ATTEMPTS` | `3` | `1` – `10` | Total retry attempts before permanent failure |
| `KB_WORKER_BACKOFF_BASE_SECONDS` | `10` | `1` – `60` | Base multiplier for exponential backoff |
| `KB_WORKER_BACKOFF_MAX_SECONDS` | `300` | `10` – `3600` | Maximum backoff delay cap |
| `KB_WORKER_CONCURRENCY` | `1` | `1` – `8` | Parallel extraction tasks per worker process |
| `KB_WORKER_POLL_INTERVAL_SECONDS` | `3.0` | `0.5` – `30.0` | Sleep duration when queue is empty |
| `KB_WORKER_MAX_PDF_PAGES` | `2000` | `1` – `10000` | Maximum allowed physical PDF pages |
| `KB_WORKER_MAX_EXTRACTED_TEXT_BYTES` | `26214400` (25 MiB) | `1024` – `104857600` | Cumulative extracted text payload ceiling |
| `KB_WORKER_DOWNLOAD_CHUNK_SIZE` | `65536` (64 KiB) | `4096` – `1048576` | Chunk size for streaming file download |

---

## Running the Worker

### Development Mode (Continuous Polling)

From `apps/api`:

```powershell
# PowerShell
$env:PYTHONPATH = "D:\Projects\RagBait\apps\api"
.\.venv\Scripts\python -m app.modules.knowledge_base.worker
```

```bash
# Bash / Linux
export PYTHONPATH="/path/to/RagBait/apps/api"
python -m app.modules.knowledge_base.worker
```

### Single-Batch Mode (`--once`)

Processes currently eligible jobs and terminates when the queue is exhausted. Suitable for scheduled cron tasks or testing:

```powershell
.\.venv\Scripts\python -m app.modules.knowledge_base.worker --once
```

### Multi-Worker Horizontal Scaling

Because job claiming uses atomic `FOR UPDATE SKIP LOCKED` at the PostgreSQL engine level, multiple worker processes can run simultaneously without duplicate claims or distributed locks:

```powershell
# Terminal 1
.\.venv\Scripts\python -m app.modules.knowledge_base.worker --worker-id worker-node-1

# Terminal 2
.\.venv\Scripts\python -m app.modules.knowledge_base.worker --worker-id worker-node-2
```

---

## Error Codes and Lifecycle Behavior

### Permanent Errors (No Retry)

When a permanent failure is detected, the extraction job and document immediately transition to `status = 'failed'`:

| Error Code | Trigger Condition |
|------------|-------------------|
| `CORRUPT_FILE` | PDF structure corrupted or unreadable by parser |
| `ENCRYPTED_PDF` | PDF is encrypted or password-protected |
| `PAGE_LIMIT_EXCEEDED` | PDF physical pages exceed configured limit (default: 2000) |
| `PAYLOAD_TOO_LARGE` | Cumulative extracted text exceeds payload limit (default: 25 MiB) |
| `MALFORMED_TEXT` | TXT file contains null bytes (`\x00`) or excessive control chars |
| `INVALID_MIME_TYPE` | File is neither `application/pdf` nor `text/plain` |
| `CHECKSUM_MISMATCH` | Downloaded file SHA-256 does not match database record |
| `FILE_SIZE_MISMATCH` | Downloaded byte count does not match database record |

### Transient Errors (Exponential Backoff Retry)

When a transient failure occurs (e.g., storage stream drop, process execution timeout, DB disconnect):
1. If `attempts < max_attempts`:
   - Calculates backoff delay: `delay = min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * (2 ^ (attempts - 1)))`.
   - Sets `status = 'queued'`, `scheduled_at = NOW() + delay`, `lease_token = NULL`, `locked_until = NULL`.
   - Document `extraction_status` is updated to `'queued'`.
2. If `attempts >= max_attempts`:
   - Transitioned permanently to `status = 'failed'` with `error_code = 'MAX_ATTEMPTS_EXCEEDED'`.

---

## Verification & Health Check

1. **Check extraction jobs queue**:
   ```sql
   SELECT status, count(*) FROM public.kb_extraction_jobs GROUP BY status;
   ```

2. **Inspect failed extraction jobs**:
   ```sql
   SELECT id, document_id, attempts, error_code, error_message, updated_at
   FROM public.kb_extraction_jobs
   WHERE status = 'failed'
   ORDER BY updated_at DESC
   LIMIT 10;
   ```

3. **Check document page counts**:
   ```sql
   SELECT d.id, d.title, d.extraction_status, d.total_pages, d.pages_with_text, count(p.id) AS stored_pages
   FROM public.kb_documents d
   LEFT JOIN public.kb_document_pages p ON p.document_id = d.id
   GROUP BY d.id
   ORDER BY d.created_at DESC
   LIMIT 10;
   ```
