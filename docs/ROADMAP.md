# RagBait Product Roadmap

## Milestone Overview

### Completed Milestones
- **M0 — Project Foundation** (Current)
  - Monorepo structure (`apps/web`, `apps/api`, `supabase`, `docs`).
  - Next.js 15 App Router & FastAPI 0.115+ setup.
  - Supabase PostgreSQL schema, migrations, and Row Level Security.
  - Redis distributed rate limiting.
  - Structured logging, centralized error handling, and testing infrastructure.
- **M1 — Authentication & Authorization** (Current)
  - Full-name registration and email verification.
  - Access request workflow (unverified -> pending -> approved / rejected / suspended).
  - Effective access state resolution.
  - Admin dashboard, user management, and access request reviews.
  - Role management with concurrency locking and last super-admin protection.
  - Active user presence heartbeat monitoring.
  - Immutable audit logging.

---

### Upcoming Milestones

### M2: Secure Provider Credential Management & LLM Gateway
- Shared and private AI provider credential management (Gemini, OpenAI, OpenRouter).
- Envelope encryption in a private schema (`vault`).
- Spending caps, user contribution verification, and provider fallback adapters.

### M3: Collaborative Subject-Wise Document Repository
- Subject hierarchy and access permissions.
- Document uploads with strict validation (MIME, magic bytes, 25MB, 500 pages).
- Document-level ownership, public subject sharing, and private download links.

### M4: Document Ingestion Pipeline & Subject-Scoped RAG
- Background processing for PDF text and table extraction.
- Text chunking, token tracking, and embedding generation via `pgvector`.
- Hybrid vector + keyword search scoped by subject with source citation tracking.

### M5: AI Study Assistant & Exam Generation
- Streaming conversational AI assistant.
- Subject-grounded retrieval with exact page citations.
- Exam mode, practice assignment generation, and answer evaluation.

### M6: Production Hardening, Observability & Performance
- Multi-region deployment, CDN caching for static assets.
- Distributed tracing with OpenTelemetry.
- Automated security scanning and compliance auditing.
