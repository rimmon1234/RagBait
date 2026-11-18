# RagBait Operational Limits & Failure Modes

## 1. Rate Limiting Specifications

Distributed rate limiting is enforced via atomic Redis operations.

| Endpoint Group | Rate Limit | Scope | Action on Breach |
|---|---|---|---|
| General Authenticated API | 60 requests / minute | Per User (`sub`) | HTTP 429 (`Retry-After`) |
| Presence Heartbeat | 1 request / 45 seconds | Per User (`sub`) | HTTP 429 (`Retry-After`) |
| Admin Reads | 60 requests / minute | Per Admin (`sub`) | HTTP 429 (`Retry-After`) |
| Admin Mutations | 30 requests / minute | Per Admin (`sub`) | HTTP 429 (`Retry-After`) |
| Auth / OTP Verification | 3 requests / 15 minutes | Per Email | Handled via Supabase Auth rate controls |

---

## 2. Failure Handling Policies

### 2.1 Redis Unavailability
- **Development**: If Redis is unreachable and `ENVIRONMENT=development`, an error log is emitted and requests proceed with a local fallback limiter if configured, or fail gracefully.
- **Production (`ENVIRONMENT=production`)**: Rate limiting fails closed. The `/health/ready` probe returns `503 Service Unavailable`, preventing traffic routing to degraded instances.

### 2.2 Database Pool Exhaustion
- Connection acquisition timeout: 5.0 seconds.
- Statement execution timeout: 10.0 seconds.
- On pool exhaustion: returns HTTP `503 Service Unavailable` with structured error code `DB_POOL_EXHAUSTED`.

### 2.3 JWT Expiration or Signature Mismatch
- Expired tokens return HTTP `401 Unauthorized` with code `TOKEN_EXPIRED`.
- Invalid signatures or malformed claims return HTTP `401 Unauthorized` with code `INVALID_TOKEN`.
- Client must trigger a session refresh with Supabase Auth or re-authenticate.

---

## 3. Future Milestone Limits (Documented for Extension)

### 3.1 Document Ingestion Limits (M3 & M4)
- **Supported Formats**: PDF, TXT only.
- **Max File Size**: 25 MB.
- **Max PDF Pages**: 500 pages.
- **Upload Rate**: Max 10 uploads per hour per user.
- **Concurrent Ingestion Jobs**: Max 2 per user.
- **Storage Strategy**: Private Supabase Storage bucket with signed download URLs. No public read access.

### 3.2 AI & LLM Generation Limits (M2 & M5)
- **Default AI Generation Rate**: 10 requests / minute per user.
- **Max Concurrent Generations**: 2 per user.
- **Spending / Quota Guard**: Per-user and per-provider monthly token and spending caps.
- **Failure Classification**: Provider timeouts (retry with exponential backoff & jitter), quota exhausted (no retry, return `PROVIDER_QUOTA_EXCEEDED`), invalid credentials (alert admin, do not retry).
