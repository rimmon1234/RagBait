# RagBait Phase 4 — Milestone M4.1 Implementation Report

**Milestone:** M4.1 — Persistent Chat Infrastructure & Conversation Management  
**Branch:** `phase4`  
**Execution Date:** November 23, 2026  
**Status:** COMPLETE & FULLY VERIFIED  
**Baseline Commit:** `72d8e1c`  

---

## 1. Executive Summary

Milestone **M4.1 (Chat Persistence & Conversation Management)** establishes the persistent foundation for RagBait Phase 4's AI Study Assistant. Prior to this milestone, chat functionality in RagBait was confined to transient in-memory state within the Phase 2 local inference playground.

Under M4.1, RagBait provides durable, multi-tenant, subject-scoped conversational persistence backed by PostgreSQL with transactional integrity, Row-Level Security (RLS), cursor-based pagination, and complete frontend conversation management.

### Key Deliverables Completed:
1. **Database Schema & RLS Policies** (`supabase/migrations/20260301000000_m4_1_chat_persistence.sql`):
   - Created tables `public.chat_conversations` and `public.chat_messages`.
   - Custom enum types `chat_conversation_status`, `chat_message_role`, and `chat_message_status`.
   - Strict subject binding: every conversation is permanently bound to `public.kb_subjects` (`ON DELETE RESTRICT`).
   - Transactional, monotonic sequence generation ensuring message sequence integrity (`(conversation_id, sequence_number)` unique).
   - Strict Row-Level Security (RLS) policies isolating conversations between students; public/anonymous access revoked.
2. **Database Verification Suite** (`supabase/tests/m4_1_chat_persistence_test.sql`):
   - **28 of 28 pgTAP automated subtests PASSED** with 0 failures.
3. **Backend Chat Engine (`apps/api`)**:
   - Pydantic v2 schemas with strict validation, whitespace stripping, and Unicode control-character sanitization (`schemas.py`).
   - Parameterized asyncpg repository with pessimistic row locking (`SELECT FOR UPDATE`) for race-free sequence allocation (`repository.py`).
   - Domain service enforcing subject verification, opaque URL-safe base64 cursor pagination, and privacy-preserving 404 responses (`service.py`).
   - REST API router mounted at `/api/v1/chat/` (`router.py`).
   - Integration test suite (`apps/api/tests/test_chat_persistence.py`): **11 of 11 pytest integration tests PASSED**.
4. **Frontend Chat Client & UI Components (`apps/web`)**:
   - Strongly-typed API client and TypeScript definitions (`apps/web/src/types/chat.ts`, `apps/web/src/lib/chat-client.ts`).
   - Conversation management sidebar (`ChatSidebar.tsx`) with active/archived tabs, subject filter, title search, and new chat trigger.
   - Comprehensive modals for creating (`NewConversationModal.tsx`), renaming (`RenameConversationModal.tsx`), and deleting (`DeleteConversationModal.tsx`) conversations.
   - Conversation viewer (`ChatInterface.tsx`) rendering message history with sequence badges (`#1`, `#2`), timestamps, role indicators, and honest M4.1 infrastructure banners.
   - Dedicated protected routes (`/chat` and `/chat/[id]`), plus direct "Study Chat" link from Knowledge Base subject pages (`/knowledge-base/[subjectSlug]`).
   - Frontend test suite (`apps/web/tests/chat/`): **19 of 19 Vitest tests PASSED**; full web suite: **141 of 141 tests PASSED**.
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

## 3. Backend Architecture (`apps/api`)

### 3.1 REST Endpoints

| Method | Endpoint | Description | Auth / Entitlement |
|---|---|---|---|
| `POST` | `/api/v1/chat/conversations` | Create new subject-scoped conversation | Approved Student (`require_approved_user`) |
| `GET` | `/api/v1/chat/conversations` | List user's conversations (cursor-paginated, filtered by subject, status, search) | Approved Student |
| `GET` | `/api/v1/chat/conversations/{id}` | Retrieve specific conversation metadata | Approved Student (Owner only) |
| `PATCH` | `/api/v1/chat/conversations/{id}` | Rename conversation title | Approved Student (Owner only) |
| `POST` | `/api/v1/chat/conversations/{id}/archive` | Archive conversation | Approved Student (Owner only) |
| `POST` | `/api/v1/chat/conversations/{id}/restore` | Restore archived conversation | Approved Student (Owner only) |
| `DELETE` | `/api/v1/chat/conversations/{id}` | Soft delete conversation (`status = 'deleted'`) | Approved Student (Owner only) |
| `GET` | `/api/v1/chat/conversations/{id}/messages` | Retrieve message history (cursor-paginated) | Approved Student (Owner only) |

### 3.2 Concurrency & Monotonic Sequence Numbering
Message sequence numbers are allocated under pessimistic row locks in `ChatRepository.insert_message`:
```sql
SELECT next_sequence_number
FROM public.chat_conversations
WHERE id = $1
FOR UPDATE;
```
This guarantees that concurrent message insertions never race or allocate duplicate sequence numbers, verified by concurrency stress testing (`test_concurrent_message_insertion_sequence_integrity`).

### 3.3 Cursor-Based Pagination
Pagination cursors are opaque URL-safe base64-encoded strings:
- Conversations: `base64(last_message_at/created_at + "|" + conversation_id)`
- Messages: `base64(sequence_number)`
This prevents deep SQL offset performance degradation and eliminates pagination drift when messages or conversations are inserted in real time.

---

## 4. Frontend Architecture (`apps/web`)

### 4.1 Component Tree & Routes

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
    └── chat.ts                 # ChatConversation, ChatMessage, and pagination types
```

### 4.2 User Experience Features
- **Subject-Scoped Conversations**: Students can select their academic subject or start directly from any Knowledge Base subject page.
- **Active / Archived Navigation**: Instantly toggle between active discussions and archived review sessions without loss of history.
- **Cursor History Loading**: Older messages load seamlessly without layout jump or unexpected re-renders.
- **Honest System Status**: The chat input displays clear information indicating that message persistence is operational, and that grounded RAG answering will be activated in upcoming milestones (M4.5/M4.6).

---

## 5. Automated Verification Results

### 5.1 Database pgTAP Tests (`supabase/tests/m4_1_chat_persistence_test.sql`)
Executed via Supabase CLI against PostgreSQL 17:
```
1..28
ok 1 - Enum chat_conversation_status exists
ok 2 - Enum chat_message_role exists
ok 3 - Enum chat_message_status exists
ok 4 - Table chat_conversations exists
ok 5 - Table chat_messages exists
ok 6 - Table chat_conversations has RLS enabled
ok 7 - Table chat_messages has RLS enabled
ok 8 - Anonymous cannot SELECT from chat_conversations
ok 9 - Anonymous cannot SELECT from chat_messages
ok 10 - Student A can insert conversation
ok 11 - Student A sees their conversation
ok 12 - Student B cannot see Student A conversation
ok 13 - Student B cannot insert into Student A conversation
ok 14 - Student A can insert message #1
ok 15 - Student A can insert message #2
ok 16 - Duplicate sequence numbers are rejected by unique constraint
ok 17 - Student A can see their 2 messages
ok 18 - Student B cannot see Student A messages
ok 19 - Student A can archive their conversation
ok 20 - Student A sees conversation in archived state
ok 21 - Student A can restore their conversation
ok 22 - Conversation is restored to active
ok 23 - Empty title violates check constraint
ok 24 - Oversized message content violates 32KB check constraint
ok 25 - Next sequence number counter increments accurately
ok 26 - Message count increments accurately
ok 27 - Subject deletion restricted when conversation exists
ok 28 - Soft deleted conversation hidden from standard queries
```
**Result: 28 of 28 tests PASSED (100%).**

### 5.2 Backend Pytest Suite (`apps/api/tests/test_chat_persistence.py`)
Executed via Python 3.12 pytest runner:
- `test_unauthenticated_request_rejected`: PASSED
- `test_unapproved_user_rejected`: PASSED
- `test_create_conversation_success`: PASSED
- `test_create_conversation_default_title`: PASSED
- `test_create_conversation_inactive_subject_rejected`: PASSED
- `test_create_conversation_invalid_subject_rejected`: PASSED
- `test_title_validation`: PASSED
- `test_cross_user_isolation`: PASSED
- `test_conversation_mutations`: PASSED
- `test_message_pagination`: PASSED
- `test_concurrent_message_insertion_sequence_integrity`: PASSED

**Result: 11 of 11 tests PASSED (100%).**

### 5.3 Frontend Vitest Suite (`apps/web/tests/chat/`)
Executed via Vitest:
- `tests/chat/chat-client.test.ts` (8 tests): PASSED
- `tests/chat/ChatComponents.test.tsx` (11 tests): PASSED
- Full web application test suite (23 test files, 141 tests): **141 of 141 tests PASSED**.
- TypeScript typecheck (`tsc --noEmit`): **0 errors**.

---

## 6. Scope Boundary Compliance

| Requirement | Milestone M4.1 Status | Scheduled Milestone |
|---|---|---|
| Conversation & Message Schema | Complete | M4.1 |
| Conversation CRUD & Lifecycle | Complete | M4.1 |
| Sequence Integrity & Pessimistic Locks | Complete | M4.1 |
| Cursor Pagination | Complete | M4.1 |
| Multi-tenant RLS Isolation | Complete | M4.1 |
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

## 7. Next Milestone Transition

With Milestone M4.1 successfully completed and verified:
- **Immediate Next Step**: Begin **Milestone M4.2 — Page-Aware Chunking and Durable Indexing Lifecycle**.
- M4.2 will introduce:
  - Deterministic document chunking bounded strictly to 420 tokens (`chk_chunk_token_ceiling`).
  - Sentence and clause splitting with page provenance retention (`page_number_start`, `page_number_end`).
  - Durable indexing job lifecycle with transactional lease locking (`SKIP LOCKED`) and automated extraction triggering.
