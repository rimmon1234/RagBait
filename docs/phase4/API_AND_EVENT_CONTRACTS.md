# RagBait Phase 4 — API and Event Contracts

**Document Version:** 1.0.0  
**Phase:** Phase 4 — AI Study Assistant and Retrieval-Augmented Generation (RAG)  
**Date:** November 22, 2026  
**Status:** Approved API Specification  

---

## 1. REST API Overview & Namespacing

All Phase 4 conversational endpoints are mounted under `/api/v1/chat/` within the FastAPI application. All requests require standard Bearer token authentication via Supabase JWT (`Authorization: Bearer <token>`).

| Method | Endpoint | Description | Auth Requirement |
| :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/chat/conversations` | Create a new subject-scoped conversation | Approved Student |
| `GET` | `/api/v1/chat/conversations` | List user's conversations (paginated, subject filter) | Approved Student |
| `GET` | `/api/v1/chat/conversations/{id}` | Retrieve conversation metadata and state | Conversation Owner |
| `PATCH` | `/api/v1/chat/conversations/{id}` | Rename title or update status (`active`, `archived`) | Conversation Owner |
| `DELETE` | `/api/v1/chat/conversations/{id}` | Soft-delete conversation thread | Conversation Owner |
| `GET` | `/api/v1/chat/conversations/{id}/messages` | Paginated message history (cursor-based) | Conversation Owner |
| `POST` | `/api/v1/chat/conversations/{id}/messages` | Post user prompt & stream assistant answer (SSE) | Conversation Owner + Entitled |
| `POST` | `/api/v1/chat/conversations/{id}/messages/{msg_id}/cancel` | Abort active generation stream | Conversation Owner |
| `GET` | `/api/v1/chat/messages/{message_id}/sources` | Retrieve immutable Sources & Context evidence | Conversation Owner |
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

### 3.1 Send Message and Stream Completion
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
The server opens an HTTP/1.1 or HTTP/2 Server-Sent Events stream emitting newline-delimited chunks:

1. **`event: generation_started`**: Emitted when prompt validation, subject scoping, and admission reservation pass.
```
event: generation_started
data: {"message_id":"m-8f7a6b5c","generation_request_id":"req-019283","model_id":"gemini-3.8-flash","provider_id":"gemini"}
```

2. **`event: sources_identified`**: Emitted immediately upon hybrid search completion, alerting the UI to identified candidate sources before token generation begins.
```
event: sources_identified
data: {"sources":[{"citation_key":"S1","document_id":"d-123","document_title":"Silberschatz OS 10th Ed.pdf","page_number_start":142,"page_number_end":143}]}
```

3. **`event: text_delta`**: Emitted repeatedly as completion tokens arrive from the upstream provider.
```
event: text_delta
data: {"delta":"Round Robin (RR) is a preemptive CPU scheduling algorithm [S1]"}
```

4. **`event: generation_completed`**: Emitted when generation finishes successfully.
```
event: generation_completed
data: {"message_id":"m-8f7a6b5c","finish_reason":"stop","token_usage":{"prompt_tokens":840,"completion_tokens":165,"total_tokens":1005}}
```

5. **`event: generation_error`**: Emitted if generation fails mid-stream.
```
event: generation_error
data: {"error_code":"PROVIDER_OVERLOADED","message":"Upstream provider rate limited; unable to complete response."}
```

### 3.2 Cancel Generation
- **Route**: `POST /api/v1/chat/conversations/{id}/messages/{msg_id}/cancel`
- **Success Response (200 OK)**:
```json
{
  "message_id": "m-8f7a6b5c",
  "status": "cancelled",
  "retained_length": 182
}
```

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
      "is_available": true,
      "document_viewer_url": "/knowledge-base/document/d-1234-abcd?page=142"
    }
  ]
}
```
*Note on Access Revocation*: If the document has been archived by administrators since generation, `is_available` returns `false`, `excerpt` is masked with `"[Source Unavailable / Moderated by Admin]"`, and `document_viewer_url` returns `null`.

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
- `CHAT_CONTRIBUTION_REQUIRED`: Student account is approved but has not satisfied the M2 free-tier contribution policy (HTTP 403).
- `PROMPT_BUDGET_EXCEEDED`: User prompt alone exceeds model context ceiling.
- `STREAM_CANCELLED_BY_CLIENT`: Client requested abortion.
