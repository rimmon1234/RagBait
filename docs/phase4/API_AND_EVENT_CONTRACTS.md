# RagBait Phase 4 — API and Event Contracts

**Document Version:** 1.2.0  
**Phase:** Phase 4 — AI Study Assistant and Retrieval-Augmented Generation (RAG)  
**Date:** November 25, 2026  
**Status:** Approved API Specification (Post-Audit Hardened Baseline)  

---

## 1. REST API Overview & Namespacing

All Phase 4 conversational endpoints are mounted under `/api/v1/chat/` within the FastAPI application. All requests require standard Bearer token authentication via Supabase JWT (`Authorization: Bearer <token>`), approved account status, AND active free-tier cloud API contribution entitlement (`require_chat_entitlement`, returning `403 CHAT_CONTRIBUTION_REQUIRED` when locked while preserving saved DB conversation history). Legacy `POST /api/v1/llm/chat/completions` and `POST /api/v1/routing/preview` also enforce `require_chat_entitlement`.

| Method | Endpoint | Description | Auth Requirement |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/chat/conversations` | Create a new subject-scoped conversation | Approved Student + Entitled |
| `GET` | `/api/v1/chat/conversations` | List user's conversations (paginated, subject filter) | Approved Student + Entitled |
| `GET` | `/api/v1/chat/conversations/{id}` | Retrieve conversation metadata and state | Conversation Owner + Entitled |
| `PATCH` | `/api/v1/chat/conversations/{id}` | Rename title or update status (`active`, `archived`) | Conversation Owner + Entitled |
| `POST` | `/api/v1/chat/conversations/{id}/archive` | Archive conversation thread | Conversation Owner + Entitled |
| `POST` | `/api/v1/chat/conversations/{id}/restore` | Restore archived conversation thread | Conversation Owner + Entitled |
| `DELETE` | `/api/v1/chat/conversations/{id}` | Soft-delete conversation thread | Conversation Owner + Entitled |
| `GET` | `/api/v1/chat/conversations/{id}/messages` | Paginated message history (cursor-based) | Conversation Owner + Entitled |
| `POST` | `/api/v1/chat/conversations/{id}/messages` | Post user prompt & stream assistant answer (SSE) | Conversation Owner + Entitled |
| `POST` | `/api/v1/chat/conversations/{id}/messages/{msg_id}/cancel` | Abort active generation stream | Conversation Owner + Entitled |
| `POST` | `/api/v1/chat/conversations/{id}/messages/prepare-local` | Prepare prompt & hybrid context for local Ollama | Conversation Owner + Entitled |
| `POST` | `/api/v1/chat/conversations/{id}/messages/finalize-local` | Reconcile citations & persist local completion | Conversation Owner + Entitled |
| `GET` | `/api/v1/chat/messages/{message_id}/sources` | Retrieve immutable Sources & Context evidence | Conversation Owner + Entitled |
| `POST` | `/api/v1/kb/documents/upload-intent` | Reserve signed upload URL (currently restricted while parser lacks OS sandbox) | Approved Admin / Super Admin |
| `POST` | `/api/v1/kb/documents/{id}/confirm-upload` | Confirm uploaded document and enqueue extraction | Approved Admin / Super Admin |
| `POST` | `/api/v1/kb/admin/documents/{id}/reindex` | Trigger document re-chunking and re-embedding | Admin / Super Admin |

---

## 2. Conversation Endpoints

### 2.1 Create Conversation
- **Route**: `POST /api/v1/chat/conversations`
- **Request Body**:
```json
{
  "subject_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "title": "CPU Scheduling Algorithms"
}
```
*Note*: `title` is optional. If omitted, the server generates a default title (e.g. `"New Conversation — Operating Systems"`).

- **Success Response (201 Created)**:
```json
{
  "id": "c1f88720-3b4c-4e89-8d75-8e7c10bcf22a",
  "user_id": "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
  "subject_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
  "subject_name": "Operating Systems",
  "title": "CPU Scheduling Algorithms",
  "status": "active",
  "message_count": 0,
  "created_at": "2026-11-23T10:00:00Z",
  "updated_at": "2026-11-23T10:00:00Z"
}
```

### 2.2 List Conversations
- **Route**: `GET /api/v1/chat/conversations?subject_id={uuid}&status=active&limit=20&cursor={iso_timestamp}`
- **Success Response (200 OK)**:
```json
{
  "items": [
    {
      "id": "c1f88720-3b4c-4e89-8d75-8e7c10bcf22a",
      "subject_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
      "subject_name": "Operating Systems",
      "title": "CPU Scheduling Algorithms",
      "status": "active",
      "message_count": 4,
      "updated_at": "2026-11-23T10:15:00Z"
    }
  ],
  "next_cursor": "2026-11-23T09:45:00Z",
  "has_more": true
}
```

---

## 3. Message & Streaming Endpoints

### 3.1 Send Message and Stream Completion (Cloud / Auto Mode)
- **Route**: `POST /api/v1/chat/conversations/{id}/messages`
- **Request Headers**:
  - `Accept: text/event-stream`
  - `Authorization: Bearer <supabase_jwt>`
- **Request Body**:
```json
{
  "content": "Explain Round Robin scheduling with quantum = 2ms and cite the textbook.",
  "idempotency_key": "idem-msg-9b1deb4d-01",
  "inference_preference": "auto",
  "model_id": null
}
```

#### SSE Stream Protocol & Events
By default (`"stream": true` or `Accept: text/event-stream`), the server opens an HTTP 200 Server-Sent Events stream with `id: <seq>`, keepalive pings (`: keep-alive`), and JSON `data:` payloads (passing `"stream": false` without SSE `Accept` returns synchronous `ChatTurnResponse` JSON):

1. **`event: admission_waiting`** (Emitted when global cloud cap of 10 is saturated before admission timeout):
```
id: 1
event: admission_waiting
data: {"message_id":"...","conversation_id":"...","queue_position":1,"estimated_wait_seconds":30,"status":"queued"}
```

2. **`event: generation_started`**: Emitted when upstream cloud generation begins (or replayed idempotently).
```
id: 2
event: generation_started
data: {"message_id":"...","user_message_id":"...","conversation_id":"...","generation_request_id":"req-019283","model_id":"gemini-2.5-flash","provider_id":"gemini","resolved_model_id":"gemini-2.5-flash","streaming":true,"replayed":false}
```

3. **`event: sources_identified`**: Emitted alongside generation start with safe citation source metadata (excluding raw chunks/secrets).
```
id: 3
event: sources_identified
data: {"message_id":"...","conversation_id":"...","sources":[{"citation_key":"S1","document_id":"d-123","document_title":"Silberschatz OS 10th Ed.pdf","page_number_start":142,"page_number_end":143,"relevance_score":0.884}],"omitted_count":0,"retrieval_candidate_count":4}
```

4. **`event: text_delta`**: Emitted repeatedly as normalized UTF-8 completion chunks arrive.
```
id: 4
event: text_delta
data: {"message_id":"...","delta":"Round Robin (RR) is a preemptive CPU scheduling algorithm [S1]"}
```

5. **`event: content_replaced`** (Emitted if post-stream citation validation triggers bounded single repair):
```
id: 5
event: content_replaced
data: {"message_id":"...","content":"Corrected answer [S1]","reason":"citation_repair"}
```

6. **`event: generation_completed`**: Terminal event emitted after atomic database and provenance commit.
```
id: 6
event: generation_completed
data: {"message_id":"...","finish_reason":"stop","usage":{"input_tokens":840,"output_tokens":165,"total_tokens":1005},"citation_validation_status":"valid_grounded","valid_citations":["S1"],"repair_attempted":false,"sources":[...],"replayed":false}
```

7. **`event: generation_failed`**: Terminal event emitted on unrecoverable timeout, citation validation failure, lease loss, or mid-stream provider error.
```
id: 7
event: generation_failed
data: {"message_id":"...","code":"RATE_LIMITED","message":"All cloud generation slots remained busy for 30s. Please retry shortly.","retryable":true}
```

8. **`event: generation_cancelled`**: Terminal event emitted when student cancellation stops an active or queued stream.
```
id: 8
event: generation_cancelled
data: {"message_id":"...","conversation_id":"...","partial_content":"...","retained_length":42,"reason":"user_requested"}
```

### 3.2 Private Local Ollama Endpoints

#### Prepare Local Generation
- **Route**: `POST /api/v1/chat/conversations/{id}/messages/prepare-local`
- **Request Body**:
```json
{
  "content": "Explain paging and page faults with citations.",
  "idempotency_key": "idem-local-01923"
}
```
- **Success Response (200 OK)**:
```json
{
  "message_id": "m-local-8f7a6b5c",
  "conversation_id": "c1f88720-3b4c-4e89-8d75-8e7c10bcf22a",
  "prompt": "<system_instructions>...</system_instructions>\n<study_context_evidence>...</study_context_evidence>\n<user_query>Explain paging and page faults with citations.</user_query>",
  "sources": [
    {
      "citation_key": "S1",
      "document_id": "d-1234-abcd",
      "document_title": "Operating Systems Concepts.pdf",
      "page_number_start": 180,
      "page_number_end": 182,
      "relevance_score": 0.892
    }
  ]
}
```

#### Finalize Local Generation
- **Route**: `POST /api/v1/chat/conversations/{id}/messages/finalize-local`
- **Request Body**:
```json
{
  "message_id": "m-local-8f7a6b5c",
  "prepared_request_id": "prep-9f8e7d6c5b4a",
  "completion_text": "Paging is a memory management scheme that eliminates the need for contiguous allocation of physical memory [S1]...",
  "finish_reason": "stop",
  "token_usage": {
    "prompt_tokens": 620,
    "completion_tokens": 140,
    "total_tokens": 760
  }
}
```
- **Success Response (200 OK)**:
```json
{
  "message_id": "m-local-8f7a6b5c",
  "status": "completed",
  "citations_reconciled": 1
}
```

### 3.3 Cancel Generation
- **Primary Route**: `POST /api/v1/chat/messages/{message_id}/cancel`
- **Conversation-Scoped Alias**: `POST /api/v1/chat/conversations/{conversation_id}/messages/{message_id}/cancel`
- **Success Response (200 OK)**:
```json
{
  "message_id": "m-8f7a6b5c",
  "conversation_id": "c1f88720-3b4c-4e89-8d75-8e7c10bcf22a",
  "status": "cancelled",
  "already_cancelled": false,
  "retained_length": 182,
  "reason": "user_requested"
}
```
- **Conflict Responses (`409`)**: Already-terminal `completed` (`GENERATION_ALREADY_COMPLETED`) or `failed` (`GENERATION_ALREADY_FAILED`) messages are never overwritten to `cancelled`. Non-owners receive `404 MESSAGE_NOT_FOUND`.

---

## 4. Sources & Context Evidence Endpoint

### 4.1 Retrieve Message Sources
- **Route**: `GET /api/v1/chat/messages/{message_id}/sources`
- **Success Response (200 OK)**:
```json
{
  "message_id": "m-8f7a6b5c",
  "sources": [
    {
      "citation_key": "S1",
      "document_id": "d-1234-abcd",
      "document_title": "Operating Systems Concepts (10th Ed).pdf",
      "file_type": "application/pdf",
      "page_number_start": 142,
      "page_number_end": 143,
      "excerpt": "The Round-Robin (RR) scheduling algorithm is designed especially for timesharing systems. It is similar to FCFS scheduling, but preemption is added to enable the system to switch between processes. A small unit of time, called a time quantum or time slice, is defined...",
      "relevance_score": 0.884,
      "included_in_context": true,
      "referenced_in_answer": true,
      "is_available": true,
      "document_viewer_url": "/knowledge-base/document/d-1234-abcd?page=142"
    }
  ]
}
```
*Note on Access Revocation*: If the document has been archived by administrators since generation, `is_available` returns `false`, `excerpt` is masked with `"[Source Unavailable — Document Archived or Moderated by Administrator]"`, and `document_viewer_url` returns `null`.

---

## 5. Standard Error Envelopes

All Phase 4 API errors strictly adhere to the unified RagBait JSON error schema:
```json
{
  "error": {
    "code": "CONVERSATION_CONCURRENCY_LIMIT",
    "message": "Another response is currently generating in this conversation. Please wait for it to complete.",
    "status_code": 409,
    "details": {
      "conversation_id": "c1f88720-3b4c-4e89-8d75-8e7c10bcf22a",
      "active_message_id": "m-8f7a6b5c"
    }
  }
}
```

### Standard Phase 4 Error Codes
- `SUBJECT_INACTIVE`: Subject is archived or disabled.
- `CONVERSATION_NOT_FOUND`: Conversation does not exist or user lacks ownership.
- `CONVERSATION_CONCURRENCY_LIMIT`: Active stream already in flight for this conversation (HTTP 409).
- `USER_CONCURRENCY_LIMIT`: Student has reached maximum 2 concurrent streams across all tabs (HTTP 429).
- `CONCURRENCY_TIMEOUT`: Request timed out waiting for a free generation slot after 30 seconds (HTTP 429).
- `CHAT_CONTRIBUTION_REQUIRED`: Student account is approved but has not satisfied the M2 free-tier contribution policy (HTTP 403).
- `ADMIN_REQUIRED`: Endpoint requires administrator privileges; non-admin KB uploads are currently restricted while parser lacks OS/container isolation (HTTP 403).
- `PROMPT_BUDGET_EXCEEDED`: User prompt alone exceeds model context ceiling (HTTP 422).
- `STREAM_CANCELLED_BY_CLIENT`: Client requested abortion.
