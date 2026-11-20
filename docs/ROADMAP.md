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

#### M2 — Secure API Credential Management & Shared LLM Gateway (Completed)
The architectural foundation for unified, affordable AI inference combining public APIs and private local compute:
- **M2.1 — Provider/Model Registry & Dynamic Quota Catalog**: Administrator-controlled model catalog (`public.llm_providers`, `public.llm_models`), dynamic provider quotas (`public.provider_quotas`) with full provenance tracking, Gemini free-tier data disclosures, and ToS compliance enforcement.
- **M2.2 — Encrypted Credential Vault & Dedicated DB Service Role**: Isolated `private.api_key_secrets` schema, dedicated least-privilege DB role `ragbait_vault_service`, separate FastAPI `vault_pool`, AES-256-GCM envelope encryption with context-bound AAD, and HMAC key fingerprinting via independent `VAULT_PEPPER_SECRET`.
- **M2.3 — Owner-Only Credential Management**: User API key CRUD (`/api/v1/me/api-keys`), masked presentation, non-mutating capability test probes, and instant pool withdrawal and active revocation (with 30-day backup lifecycle).
- **M2.4 — Admission Control, Multi-Tier Rate Limiting & Usage Ledger**: Redis Lua scripts for 4-tier rate limits, common 5-RPM / 1-concurrency baseline safety envelope, provider-account quota scopes, atomic reservations, and transactional usage recording (`public.api_key_usage`) established **before** shared dispatch.
- **M2.5 — Core Free/Low-Cost Cloud Adapters**: Normalized streaming and non-streaming HTTP adapters for Google Gemini (Gemini 3.8 Flash), GroqCloud (GPT-OSS 120B/20B, Qwen 3.8 27B), OpenRouter, and Cerebras.
- **M2.6 — Optional Paid Provider Adapters**: Metered adapters for xAI / Grok and administrator-managed OpenAI project-scoped service accounts (`sk-proj-...`).
- **M2.7 — Private Local Inference**: Student local Ollama runtime connection and exclusive private loopback routing. A student's locally installed Ollama models and local computing resources remain exclusively available to that student and are never pooled or shared.
- **M2.8 — Community-Shared Local Compute (Discontinued)**: Discontinued per authoritative privacy policy. Local Ollama instances cannot be pooled or shared across students. Shared inference pooling is restricted exclusively to explicitly consented, provider-eligible cloud API credentials.
- **M2.9 — Unified Intelligent Routing**: Unified deterministic routing layer selecting between user private keys and institutional/community-pooled cloud API credentials with quota enforcement, budget limits, and admission control. Local Ollama models remain strictly private to the student's browser.
- **M2.10 — Verification & Security Hardening**: Comprehensive test suite covering credential isolation, envelope encryption, admission control, quota verification, fallback behavior, streaming, and privacy boundaries.

> **Official Policy Statement:**
> *RagBait allows students to use their own locally installed Ollama models privately. These models and their computing resources are never shared with other students. Shared inference pooling is restricted to explicitly consented, provider-eligible cloud API credentials.*

#### M3 — Collaborative Subject-Wise Knowledge Base & Document Ingestion
- Subject folder hierarchy and collaborative access permissions.
- Document uploads with strict validation (MIME types, magic byte verification, 25MB max size, 500-page limit).
- Document ownership tracking, public subject sharing, and secure download signing.
- Document parsing and ingestion pipeline for PDF and TXT materials.
*(Note: M3 relies purely on cloud and private local compute; no peer-hosted local compute is utilized).*

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
