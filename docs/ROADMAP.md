# RagBait Product Roadmap

## Milestone Overview

The development of RagBait follows a strict, sequential milestone architecture designed for security, maintainability, and clean boundaries.

---

### Completed & Audited Milestones

#### M0 — Project Foundation (Completed & Audited)
- Monorepo structure (`apps/web`, `apps/api`, `supabase`, `docs`).
- Next.js 15 App Router & FastAPI 0.115+ architecture.
- Supabase PostgreSQL 17 schema, migrations, and Row Level Security.
- Redis distributed atomic rate limiting.
- Structured JSON logging, centralized error models, and testing infrastructure.

#### M1 — Authentication & Authorization (Completed & Audited)
- Full-name registration and authoritative email verification gate.
- Access request lifecycle (`unverified` $\to$ `pending` $\to$ `approved` / `rejected` / `suspended`).
- Deterministic effective access state resolution (`/me/access`).
- Administrator dashboard, paginated user management, and access request reviews.
- Role management with PostgreSQL advisory locking and last super-admin protection.
- Active user presence heartbeat monitoring.
- Transactional, immutable administrative audit logging.

---

### Upcoming Milestones

#### M2 — Secure API Credential Management & Shared LLM Gateway (Next)
- Shared pool and private contributor AI provider credential management (Gemini, OpenAI, OpenRouter).
- Envelope encryption at rest using AES-GCM / KMS in a private schema (`vault`).
- Dynamic provider adapter layer with fallback routing, retry strategies, and rate limiting.
- Spending limits, token tracking, and student API key contribution verification to unlock application access.

#### M3 — Collaborative Subject-Wise Knowledge Base & Document Ingestion
- Subject folder hierarchy and collaborative access permissions.
- Document uploads with strict validation (MIME types, magic byte verification, 25MB max size, 500-page limit).
- Document ownership tracking, public subject sharing, and secure download signing.
- Document parsing and ingestion pipeline for PDF and TXT materials.

#### M4 — RAG Engine & Vector Retrieval
- Document chunking strategies with contextual metadata preservation.
- Vector embedding generation and indexing with PostgreSQL `pgvector`.
- Hybrid retrieval (dense vector similarity + sparse keyword search) scoped strictly by subject.
- Verifiable citation extraction mapping chunks directly to document pages.

#### M5 — AI Study Assistant
- Streaming conversational AI study interface.
- Subject-grounded assistant enforcing strict non-hallucination boundaries and citation display.
- Practice exam generation, assignment creation, and automated answer evaluation.

#### M6 — Production Hardening & Deployment
- Production containerization and multi-region cloud deployment.
- High-availability database failover, connection pooling, and CDN caching for static assets.
- Distributed tracing with OpenTelemetry, observability dashboards, and automated security scans.
