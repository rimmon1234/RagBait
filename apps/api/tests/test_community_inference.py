"""Integration and Security Tests for Shared Community Local Inference (M2.8).

Validates:
- Contributor node pairing code lifecycle and single-use consumption.
- Contributor node management (list, update, pause, resume, revoke).
- Model allowlist syncing and granular per-model sharing toggles.
- Dual-consent validation (requester privacy disclosure + contributor model allowlist).
- Server Ed25519 job authorizations and payload digest integrity.
- Job submission, status inspection, and user cancellation.
"""

import secrets
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.dependencies import get_current_profile, get_current_user
from app.core.redis import (
    close_community_redis,
    close_redis,
    get_community_redis,
    get_redis,
    init_community_redis,
    init_redis,
)
from app.core.security import AuthenticatedUser
from app.main import app
from app.modules.community import crypto, repository, streams
from app.modules.users.schemas import ApprovalStatus, UserRole
from tests.conftest import create_test_jwt, make_test_profile

CREATED_TEST_USERS: list[UUID] = []


@pytest.fixture(autouse=True)
async def setup_test_environment():
    """Ensure database connection pools and Redis are initialized and cleaned up."""
    await init_db_pool()
    r = await init_redis()
    cr = await init_community_redis()
    if r:
        try:
            keys = await r.keys("ragbait:ratelimit:*")
            if keys:
                await r.delete(*keys)
        except Exception:
            pass
    if cr:
        try:
            keys = await cr.keys("ragbait:comm:*") + await cr.keys("stream:node:*")
            if keys:
                await cr.delete(*keys)
        except Exception:
            pass
    yield
    app.dependency_overrides.clear()
    if CREATED_TEST_USERS:
        try:
            pool = get_pool()
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM auth.users WHERE id = ANY($1)", CREATED_TEST_USERS)
        except Exception:
            pass
        CREATED_TEST_USERS.clear()
    if r:
        try:
            keys = await r.keys("ragbait:ratelimit:*")
            if keys:
                await r.delete(*keys)
        except Exception:
            pass
    if cr:
        try:
            keys = await cr.keys("ragbait:comm:*") + await cr.keys("stream:node:*")
            if keys:
                await cr.delete(*keys)
        except Exception:
            pass
    await close_db_pool()
    await close_redis()
    await close_community_redis()


async def create_db_user(
    email: str | None = None, role: str = "user", status: str = "approved"
) -> UUID:
    """Create an actual user in the test database so foreign keys pass cleanly."""
    pool = get_pool()
    user_id = uuid4()
    if not email:
        unique_email = f"user_{user_id.hex[:8]}@university.edu"
    else:
        prefix, domain = email.split("@")
        unique_email = f"{prefix}_{user_id.hex[:6]}@{domain}"

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"full_name": "Test User"}'::jsonb)
            """,
            user_id,
            unique_email,
        )
        await conn.execute(
            """
            UPDATE public.profiles
            SET role = $1, approval_status = $2
            WHERE id = $3
            """,
            role,
            status,
            user_id,
        )
    CREATED_TEST_USERS.append(user_id)
    return user_id


def setup_auth_context(
    user_id: UUID,
    email: str = "contributor@university.edu",
    role: UserRole = "user",
    status: ApprovalStatus = "approved",
) -> dict[str, str]:
    """Configure dependency overrides for authenticated user and return auth headers."""
    profile = make_test_profile(user_id=user_id, email=email, role=role, approval_status=status)
    token = create_test_jwt(user_id=user_id, email=email)

    app.dependency_overrides[get_current_profile] = lambda: profile
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id=user_id,
        email=email,
        role="authenticated",
        session_id=str(uuid4()),
        claims={},
    )
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# 1. Cryptographic Unit Tests
# ==============================================================================
def test_crypto_challenge_and_job_auth():
    """Verify Ed25519 challenge signing, job authorization signing, and payload digest."""
    node_priv = ed25519.Ed25519PrivateKey.generate()
    node_pub_bytes = node_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    node_pub_hex = node_pub_bytes.hex()
    node_id = uuid4()
    nonce = secrets.token_hex(32)
    session_id = str(uuid4())
    timestamp = 1700000000

    canonical = crypto.build_challenge_canonical_string(
        node_id=node_id,
        nonce_hex=nonce,
        session_id=session_id,
        timestamp=timestamp,
    )
    signature_hex = node_priv.sign(canonical).hex()

    # Server verifies signature
    is_valid = crypto.verify_node_challenge_signature(
        signature_hex=signature_hex,
        node_id=node_id,
        nonce_hex=nonce,
        session_id=session_id,
        timestamp=timestamp,
        node_public_key_hex=node_pub_hex,
    )
    assert is_valid is True

    # Tampered nonce fails
    is_invalid = crypto.verify_node_challenge_signature(
        signature_hex=signature_hex,
        node_id=node_id,
        nonce_hex="a" * 64,
        session_id=session_id,
        timestamp=timestamp,
        node_public_key_hex=node_pub_hex,
    )
    assert is_invalid is False

    # Test payload digest and job authorization
    payload = {
        "messages": [
            {"role": "system", "content": "You are a study assistant."},
            {"role": "user", "content": "What is reinforcement learning?"},
        ]
    }
    digest = crypto.compute_payload_digest(payload)
    assert len(digest) == 64  # SHA-256 in hex

    job_id = uuid4()
    auth_nonce = secrets.token_hex(16)
    sig = crypto.sign_job_authorization(
        job_id=job_id,
        node_id=node_id,
        model_name="llama3.2:3b",
        payload_digest=digest,
        max_output_tokens=2048,
        hard_deadline_ts=1800000000,
        auth_nonce=auth_nonce,
    )
    assert len(sig) == 128

    # Verify job auth
    verified = crypto.verify_job_authorization(
        signature_hex=sig,
        job_id=job_id,
        node_id=node_id,
        model_name="llama3.2:3b",
        payload_digest=digest,
        max_output_tokens=2048,
        hard_deadline_ts=1800000000,
        auth_nonce=auth_nonce,
    )
    assert verified is True


# ==============================================================================
# 2. Pairing Code & Registration Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_pairing_code_and_node_registration(client: httpx.AsyncClient):
    """Test pairing code creation and atomic consumption during node pairing."""
    contributor_id = await create_db_user()
    headers = setup_auth_context(contributor_id)

    # 1. Generate pairing code
    resp = await client.post("/api/v1/community/nodes/pair-code", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    pairing_code = data["pairing_code"]
    assert pairing_code.startswith("RB-")
    # 128 bits of entropy: 'RB-' + four 8-hex blocks with 3 hyphens = 38 chars
    assert len(pairing_code) == 38
    assert data["ttl_seconds"] == 300

    # 2. Attempt pairing with invalid code
    node_priv = ed25519.Ed25519PrivateKey.generate()
    node_pub_hex = node_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

    bad_pair_resp = await client.post(
        "/api/v1/community/nodes/pair",
        json={
            "pairing_code": "INVALID9",
            "public_key": node_pub_hex,
            "display_name": "Test Desktop Node",
            "agent_version": "0.1.0",
            "operating_system": "Windows",
        },
    )
    assert bad_pair_resp.status_code == 400

    # 3. Pair successfully with valid code
    pair_resp = await client.post(
        "/api/v1/community/nodes/pair",
        json={
            "pairing_code": pairing_code,
            "public_key": node_pub_hex,
            "display_name": "Test Desktop Node",
            "agent_version": "0.1.0",
            "operating_system": "Windows",
        },
    )
    assert pair_resp.status_code == 201
    paired_data = pair_resp.json()
    assert "node_id" in paired_data
    assert paired_data["display_name"] == "Test Desktop Node"
    assert "server_public_key" in paired_data

    # 4. Attempt to reuse already consumed pairing code
    reuse_resp = await client.post(
        "/api/v1/community/nodes/pair",
        json={
            "pairing_code": pairing_code,
            "public_key": node_pub_hex,
            "display_name": "Duplicate Node",
            "agent_version": "0.1.0",
            "operating_system": "Windows",
        },
    )
    assert reuse_resp.status_code == 400


# ==============================================================================
# 3. Contributor Node Management & Isolation Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_node_management_and_isolation(client: httpx.AsyncClient):
    """Test node list, update, pause, resume, and revoke with owner isolation."""
    contributor_1 = await create_db_user()
    contributor_2 = await create_db_user()

    headers_1 = setup_auth_context(contributor_1)

    # Generate code and pair node for Contributor 1
    code_resp = await client.post("/api/v1/community/nodes/pair-code", headers=headers_1)
    code = code_resp.json()["pairing_code"]

    node_priv = ed25519.Ed25519PrivateKey.generate()
    node_pub_hex = node_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

    pair_resp = await client.post(
        "/api/v1/community/nodes/pair",
        json={
            "pairing_code": code,
            "public_key": node_pub_hex,
            "display_name": "Lab PC 1",
            "agent_version": "0.1.0",
            "operating_system": "Linux",
        },
    )
    node_id = pair_resp.json()["node_id"]

    # Contributor 1 lists nodes
    list_resp = await client.get("/api/v1/community/nodes", headers=headers_1)
    assert list_resp.status_code == 200
    nodes = list_resp.json()
    assert len(nodes) == 1
    assert nodes[0]["id"] == node_id
    assert nodes[0]["display_name"] == "Lab PC 1"

    # Contributor 1 updates node config
    patch_resp = await client.patch(
        f"/api/v1/community/nodes/{node_id}",
        json={"display_name": "Updated Lab PC", "max_concurrent_jobs": 2},
        headers=headers_1,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["display_name"] == "Updated Lab PC"
    assert patch_resp.json()["max_concurrent_jobs"] == 2

    # Contributor 2 attempts to view Contributor 1's node -> 403 Forbidden
    headers_2 = setup_auth_context(contributor_2)

    unauthorized_get = await client.get(f"/api/v1/community/nodes/{node_id}", headers=headers_2)
    assert unauthorized_get.status_code == 403

    # Contributor 2 attempts to pause Contributor 1's node -> 403 Forbidden
    unauthorized_pause = await client.post(
        f"/api/v1/community/nodes/{node_id}/pause", headers=headers_2
    )
    assert unauthorized_pause.status_code == 403

    # Contributor 1 pauses and resumes node
    headers_1 = setup_auth_context(contributor_1)
    pause_resp = await client.post(f"/api/v1/community/nodes/{node_id}/pause", headers=headers_1)
    assert pause_resp.status_code == 204

    node_detail = await client.get(f"/api/v1/community/nodes/{node_id}", headers=headers_1)
    assert node_detail.json()["status"] == "paused"

    resume_resp = await client.post(f"/api/v1/community/nodes/{node_id}/resume", headers=headers_1)
    assert resume_resp.status_code == 204

    node_detail = await client.get(f"/api/v1/community/nodes/{node_id}", headers=headers_1)
    assert node_detail.json()["status"] in ("online", "active")

    # Contributor 1 revokes node
    revoke_resp = await client.delete(f"/api/v1/community/nodes/{node_id}", headers=headers_1)
    assert revoke_resp.status_code == 204

    node_detail = await client.get(f"/api/v1/community/nodes/{node_id}", headers=headers_1)
    assert node_detail.json()["status"] == "revoked"


# ==============================================================================
# 4. Model Allowlist & Dual-Consent Validation Tests
# ==============================================================================
@pytest.mark.asyncio
async def test_dual_consent_and_job_submission(client: httpx.AsyncClient):
    """Test model syncing, per-model allowlist toggles, and dual-consent enforcement."""
    contributor = await create_db_user()
    requester = await create_db_user()

    headers_c = setup_auth_context(contributor)

    # Register node
    code_resp = await client.post("/api/v1/community/nodes/pair-code", headers=headers_c)
    code = code_resp.json()["pairing_code"]

    node_priv = ed25519.Ed25519PrivateKey.generate()
    node_pub_hex = node_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

    pair_resp = await client.post(
        "/api/v1/community/nodes/pair",
        json={
            "pairing_code": code,
            "public_key": node_pub_hex,
            "display_name": "Inference Station",
            "agent_version": "0.1.0",
            "operating_system": "Windows",
        },
    )
    node_id_str = pair_resp.json()["node_id"]
    node_id = UUID(node_id_str)

    # Contributor enables node sharing
    patch_share_resp = await client.patch(
        f"/api/v1/community/nodes/{node_id}",
        json={"sharing_enabled": True},
        headers=headers_c,
    )
    assert patch_share_resp.status_code == 200

    # Simulate node heartbeat so last_seen_at is active
    await repository.update_node_heartbeat(node_id)

    # Sync discovered models directly via repository (simulating agent WS sync)
    models_to_sync = [
        {"model_name": "llama3.2:3b", "parameter_size": "3B", "quantization_level": "Q4_K_M"},
        {"model_name": "mistral:7b", "parameter_size": "7B", "quantization_level": "Q4_0"},
    ]
    await repository.sync_node_models(node_id, models_to_sync)

    # Contributor shares ONLY llama3.2:3b, leaves mistral:7b unshared
    put_models_resp = await client.put(
        f"/api/v1/community/nodes/{node_id}/models",
        json={
            "models": [
                {"model_name": "llama3.2:3b", "enabled": True},
                {"model_name": "mistral:7b", "enabled": False},
            ]
        },
        headers=headers_c,
    )
    assert put_models_resp.status_code == 200

    # Requester checks available community models
    headers_r = setup_auth_context(requester)

    models_resp = await client.get("/api/v1/community/models", headers=headers_r)
    assert models_resp.status_code == 200
    avail = models_resp.json()
    model_names = [m["model_name"] for m in avail]
    assert "llama3.2:3b" in model_names
    assert "mistral:7b" not in model_names  # Unshared model must not appear

    # Test 1: Requester attempts to submit without consent -> 400
    no_consent_resp = await client.post(
        "/api/v1/community/jobs/submit",
        json={
            "model_name": "llama3.2:3b",
            "messages": [{"role": "user", "content": "Explain gravity."}],
            "consent_accepted": False,
        },
        headers=headers_r,
    )
    assert no_consent_resp.status_code == 400
    assert "consent" in no_consent_resp.json()["error"]["message"].lower()

    # Test 2: Requester attempts to submit job for unshared model -> 409 Conflict
    unshared_resp = await client.post(
        "/api/v1/community/jobs/submit",
        json={
            "model_name": "mistral:7b",
            "messages": [{"role": "user", "content": "Explain gravity."}],
            "consent_accepted": True,
        },
        headers=headers_r,
    )
    assert unshared_resp.status_code == 409
    assert "no online community node" in unshared_resp.json()["error"]["message"].lower()

    # Test 3: Requester submits with valid dual-consent -> 202 Accepted
    submit_resp = await client.post(
        "/api/v1/community/jobs/submit",
        json={
            "model_name": "llama3.2:3b",
            "messages": [{"role": "user", "content": "Explain gravity."}],
            "consent_accepted": True,
            "params": {"max_tokens": 512},
        },
        headers=headers_r,
    )
    assert submit_resp.status_code == 202
    job_data = submit_resp.json()
    job_id = job_data["job_id"]
    assert job_data["status"].upper() == "QUEUED"
    assert job_data["model_name"] == "llama3.2:3b"

    # Test 4: Requester inspects job status
    status_resp = await client.get(f"/api/v1/community/jobs/{job_id}", headers=headers_r)
    assert status_resp.status_code == 200
    assert status_resp.json()["job_id"] == job_id
    assert status_resp.json()["status"].upper() == "QUEUED"

    # Test 5: Requester cancels the job
    cancel_resp = await client.post(f"/api/v1/community/jobs/{job_id}/cancel", headers=headers_r)
    assert cancel_resp.status_code == 204

    # Verify status changed to cancelled
    status_resp_after = await client.get(f"/api/v1/community/jobs/{job_id}", headers=headers_r)
    assert status_resp_after.status_code == 200
    assert status_resp_after.json()["status"].upper() == "CANCELLED"


def test_signing_key_pair_validation():
    """Verify SEC-06: Server validates cryptographic match between signing and verifying keys."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from app.core.config import Settings

    # 1. Matching pair passes
    priv_key = Ed25519PrivateKey.generate()
    priv_hex = priv_key.private_bytes_raw().hex()
    pub_hex = priv_key.public_key().public_bytes_raw().hex()

    s = Settings(
        COMMUNITY_SERVER_SIGNING_KEY_HEX=priv_hex,
        COMMUNITY_SERVER_VERIFYING_KEY_HEX=pub_hex,
    )
    assert s.COMMUNITY_SERVER_SIGNING_KEY_HEX == priv_hex

    # 2. Mismatched pair raises ValueError
    other_priv = Ed25519PrivateKey.generate()
    other_pub_hex = other_priv.public_key().public_bytes_raw().hex()
    with pytest.raises(ValueError, match="does not match the public key derived"):
        Settings(
            COMMUNITY_SERVER_SIGNING_KEY_HEX=priv_hex,
            COMMUNITY_SERVER_VERIFYING_KEY_HEX=other_pub_hex,
        )

    # 3. Invalid key lengths raise ValueError
    with pytest.raises(ValueError):
        Settings(
            COMMUNITY_SERVER_SIGNING_KEY_HEX="badhex",
            COMMUNITY_SERVER_VERIFYING_KEY_HEX=pub_hex,
        )


@pytest.mark.asyncio
async def test_nonce_fail_closed_and_replay_prevention(monkeypatch):
    """Verify SEC-05: Nonce handling fails closed when Redis is unavailable, rejects replay."""
    from app.core.errors import AppError
    from app.modules.community import streams

    node_id = uuid4()

    # 1. When Redis is unavailable -> Fails closed with 503 AppError
    monkeypatch.setattr(streams, "get_redis", lambda: None)
    with pytest.raises(AppError) as exc_info:
        await streams.create_challenge_nonce(node_id)
    assert exc_info.value.status_code == 503

    with pytest.raises(AppError) as exc_info:
        await streams.consume_challenge_nonce("a" * 64, node_id)
    assert exc_info.value.status_code == 503

    # 2. When Redis is available -> Single use and replay resistance
    fake_redis_store = {}

    class MockRedis:
        async def set(self, key, value, ex=None, nx=False):
            if nx and key in fake_redis_store:
                return None
            fake_redis_store[key] = value
            return True

        async def getdel(self, key):
            return fake_redis_store.pop(key, None)

    mock_r = MockRedis()
    monkeypatch.setattr(streams, "get_redis", lambda: mock_r)

    nonce = await streams.create_challenge_nonce(node_id)
    assert len(nonce) == 64

    # Wrong node fails
    wrong_node = uuid4()
    # Need to re-create because getdel consumes on check
    nonce2 = await streams.create_challenge_nonce(node_id)
    assert await streams.consume_challenge_nonce(nonce2, wrong_node) is False

    # Valid node succeeds once
    nonce3 = await streams.create_challenge_nonce(node_id)
    assert await streams.consume_challenge_nonce(nonce3, node_id) is True

    # Replay fails (already consumed)
    assert await streams.consume_challenge_nonce(nonce3, node_id) is False


def test_trusted_proxy_client_ip_resolution():
    """Verify SEC-02: get_trusted_client_ip ignores untrusted X-Forwarded-For headers."""
    from starlette.requests import Request

    from app.core.config import settings
    from app.core.dependencies import get_trusted_client_ip

    # Case 1: No trusted proxies configured -> uses client.host directly
    req1 = Request(
        {
            "type": "http",
            "client": ("198.51.100.10", 12345),
            "headers": [(b"x-forwarded-for", b"203.0.113.195")],
        }
    )
    assert get_trusted_client_ip(req1) == "198.51.100.10"

    # Case 2: Direct peer is in TRUSTED_PROXY_IPS -> parses rightmost untrusted hop
    orig_trusted = settings.TRUSTED_PROXY_IPS
    try:
        settings.TRUSTED_PROXY_IPS = ["10.0.0.1", "10.0.0.2"]
        req2 = Request(
            {
                "type": "http",
                "client": ("10.0.0.1", 12345),
                "headers": [(b"x-forwarded-for", b"203.0.113.50, 198.51.100.22, 10.0.0.2")],
            }
        )
        # 10.0.0.2 is trusted, so rightmost untrusted is 198.51.100.22
        assert get_trusted_client_ip(req2) == "198.51.100.22"

        # Case 3: Direct peer is NOT in TRUSTED_PROXY_IPS -> ignores header completely
        req3 = Request(
            {
                "type": "http",
                "client": ("192.168.1.100", 12345),
                "headers": [(b"x-forwarded-for", b"8.8.8.8")],
            }
        )
        assert get_trusted_client_ip(req3) == "192.168.1.100"
    finally:
        settings.TRUSTED_PROXY_IPS = orig_trusted


@pytest.mark.asyncio
async def test_sse_stream_authentication_and_authorization(client: httpx.AsyncClient):
    """Verify SEC-01: SSE job stream requires authentication and strictly permits only requester."""
    requester_id = await create_db_user()
    other_user_id = await create_db_user()
    node_owner_id = await create_db_user()

    # 1. Register a node for node_owner directly in repository
    node_priv = ed25519.Ed25519PrivateKey.generate()
    node_pub_hex = node_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    node = await repository.create_node(
        owner_id=node_owner_id,
        display_name="Stream Test Node",
        public_key=node_pub_hex,
    )
    node_id = node["id"]
    await repository.update_node_status(node_id, "active")
    await repository.update_node_config(node_id, node_owner_id, sharing_enabled=True)
    await repository.update_node_heartbeat(node_id)
    await repository.sync_node_models(node_id, [{"model_name": "qwen2.5:1.5b"}])
    headers_owner = setup_auth_context(node_owner_id)

    # Enable model
    await client.put(
        f"/api/v1/community/nodes/{node_id}/models",
        json={"models": [{"model_name": "qwen2.5:1.5b", "enabled": True}]},
        headers=headers_owner,
    )

    # 2. Requester submits a job
    headers_req = setup_auth_context(requester_id)
    submit_resp = await client.post(
        "/api/v1/community/jobs/submit",
        json={
            "model_name": "qwen2.5:1.5b",
            "messages": [{"role": "user", "content": "Hello"}],
            "consent_accepted": True,
        },
        headers=headers_req,
    )
    job_id = submit_resp.json()["job_id"]

    # 3. Anonymous request to SSE stream -> 401 Unauthorized
    app.dependency_overrides.clear()
    anon_resp = await client.get(f"/api/v1/community/jobs/{job_id}/stream")
    assert anon_resp.status_code == 401

    # 4. Third-party student request to SSE stream -> 403 Forbidden
    headers_other = setup_auth_context(other_user_id)
    other_resp = await client.get(f"/api/v1/community/jobs/{job_id}/stream", headers=headers_other)
    assert other_resp.status_code == 403

    # 5. Node owner request to SSE stream -> 403 Forbidden (node owner must NOT have access to requester stream)
    owner_stream_resp = await client.get(
        f"/api/v1/community/jobs/{job_id}/stream", headers=headers_owner
    )
    assert owner_stream_resp.status_code == 403

    # 6. Non-existent job with requester auth -> 404 Not Found
    non_existent = str(uuid4())
    not_found_resp = await client.get(
        f"/api/v1/community/jobs/{non_existent}/stream", headers=headers_req
    )
    assert not_found_resp.status_code == 404

    # 7. Authorized requester receives 200 StreamingResponse and chunks
    headers_req = setup_auth_context(requester_id)
    await streams.append_job_chunk(job_id=UUID(job_id), seq_id=1, delta="Done", done=True)
    async with client.stream(
        "GET", f"/api/v1/community/jobs/{job_id}/stream", headers=headers_req
    ) as sse_stream:
        assert sse_stream.status_code == 200
        assert "text/event-stream" in sse_stream.headers.get("content-type", "")
        lines = [line async for line in sse_stream.aiter_lines()]
        assert any("Done" in line_text for line_text in lines)


@pytest.mark.asyncio
async def test_terminal_job_status_guard_cas():
    """Verify REL-03: update_job_status CAS guard prevents mutating terminal states."""
    user_id = await create_db_user()
    node = await repository.create_node(
        owner_id=user_id,
        display_name="Guard Test Node",
        public_key=ed25519.Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
        .hex(),
    )
    node_id = node["id"]
    job = await repository.create_job(
        requester_id=user_id,
        node_id=node_id,
        model_name="llama3.2:1b",
        payload_digest="a" * 64,
        auth_key_id="k1",
        consent_policy_version="2026-02-09",
        hard_deadline_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    job_id = job["id"]

    # 1. Cancel the job (transition to CANCELLED)
    cancelled = await repository.update_job_status(
        job_id=job_id,
        status="CANCELLED",
        cancelled_at=datetime.now(UTC),
    )
    assert cancelled is not None
    assert cancelled["status"] == "CANCELLED"

    # 2. Attempt late transition to COMPLETED (e.g. from late WS frame)
    late_completed = await repository.update_job_status(
        job_id=job_id,
        status="COMPLETED",
        completed_at=datetime.now(UTC),
    )
    # CAS guard should return None (0 rows updated)
    assert late_completed is None

    # Verify status in DB is still CANCELLED
    db_job = await repository.get_job_by_id(job_id)
    assert db_job["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_sweeper_recovers_queued_sent_jobs_and_releases_zset_concurrency():
    """Verify REL-02: sweeper recovers expired QUEUED/SENT jobs and deallocates ZSET reservation."""
    from app.core.redis import get_community_redis
    from app.modules.community import service

    user_id = await create_db_user()
    node = await repository.create_node(
        owner_id=user_id,
        display_name="Sweeper Test Node",
        public_key=ed25519.Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
        .hex(),
    )
    node_id = node["id"]
    past_deadline = datetime.now(UTC) - timedelta(seconds=10)

    # Create expired QUEUED job
    job = await repository.create_job(
        requester_id=user_id,
        node_id=node_id,
        model_name="llama3.2:1b",
        payload_digest="b" * 64,
        auth_key_id="k1",
        consent_policy_version="2026-02-09",
        hard_deadline_at=past_deadline,
    )
    job_id = job["id"]

    # Acquire ZSET concurrency reservation
    acquired = await streams.acquire_node_concurrency(
        node_id=node_id,
        job_id=job_id,
        max_concurrent=5,
        lease_expires_at=past_deadline.timestamp(),
    )
    assert acquired is True

    # Run sweeper
    swept_count = await service.recover_expired_community_jobs()
    assert swept_count >= 1

    # Verify DB status transitioned to EXPIRED
    db_job = await repository.get_job_by_id(job_id)
    assert db_job["status"] == "EXPIRED"
    assert db_job["failure_code"] == "HARD_DEADLINE_EXPIRED"

    # Verify ZSET reservation is deleted
    r = get_community_redis()
    if r:
        zscore = await r.zscore(f"ragbait:comm:node:{node_id}:reservations", str(job_id))
        assert zscore is None


@pytest.mark.asyncio
async def test_job_dispatch_stream_ack_and_delete():
    """Verify REL-01: stream entries are acknowledged and pruned via ack_and_delete_job_dispatch."""
    from app.core.redis import get_community_redis
    from app.modules.community.schemas import SignedJobAuthorization

    node_id = uuid4()
    job_id = uuid4()
    r = get_community_redis()
    if not r:
        pytest.skip("Community Redis not configured")

    auth = SignedJobAuthorization(
        job_id=job_id,
        node_id=node_id,
        model_name="llama3.2:1b",
        payload_digest="digest",
        max_output_tokens=100,
        hard_deadline_ts=int(time.time()) + 300,
        auth_nonce="nonce",
        signature="0" * 128,
        key_id="k1",
    )

    entry_id = await streams.enqueue_job_dispatch(
        node_id=node_id,
        job_id=job_id,
        authorization=auth,
        payload={"model": "llama3.2:1b"},
    )
    assert entry_id is not None

    # Read pending job
    entries = await streams.read_pending_jobs(node_id, consumer_id="test-consumer", count=1)
    assert len(entries) == 1
    assert entries[0]["entry_id"] == entry_id

    # Acknowledge and delete upon acceptance
    await streams.ack_and_delete_job_dispatch(node_id, entry_id)

    # Check PEL is empty
    xinfo = await r.xpending(f"stream:node:{node_id}:jobs", streams.CONSUMER_GROUP_NAME)
    assert xinfo["pending"] == 0

    # Check stream entry was deleted
    stream_entries = await r.xrange(f"stream:node:{node_id}:jobs", min=entry_id, max=entry_id)
    assert len(stream_entries) == 0


@pytest.mark.asyncio
async def test_distributed_session_fencing_cross_instance():
    """Verify Blocker C: PostgreSQL-authoritative distributed session fencing prevents stale-worker split-brain."""
    user_id = await create_db_user()
    node = await repository.create_node(
        owner_id=user_id,
        display_name="Fenced Node",
        public_key=ed25519.Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
        .hex(),
    )
    node_id = node["id"]

    # 1. Instance 1 connects and registers Session 1
    session_1 = f"session-instance-1-{uuid4()}"
    epoch_1, auth_session_1 = await repository.register_node_session(node_id, session_1)
    assert epoch_1 >= 1
    assert auth_session_1 == session_1
    assert await repository.verify_node_session(node_id, session_1, epoch_1) is True

    # 2. Instance 2 connects and registers Session 2 for the same node (reconnection/failover)
    session_2 = f"session-instance-2-{uuid4()}"
    epoch_2, auth_session_2 = await repository.register_node_session(node_id, session_2)
    assert epoch_2 > epoch_1
    assert auth_session_2 == session_2
    assert await repository.verify_node_session(node_id, session_2, epoch_2) is True

    # Session 1 is now superseded and must fail verification
    assert await repository.verify_node_session(node_id, session_1, epoch_1) is False
    assert await repository.verify_node_session(node_id, session_1, None) is False

    # 3. Create a job for testing fenced state transitions
    job = await repository.create_job(
        requester_id=user_id,
        node_id=node_id,
        model_name="llama3.2:1b",
        payload_digest="c" * 64,
        auth_key_id="k1",
        consent_policy_version="2026-02-09",
        hard_deadline_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    job_id = job["id"]

    # 4. Stale Instance 1 attempts update_job_status_fenced -> REJECTED (returns None, DB untouched)
    stale_update = await repository.update_job_status_fenced(
        node_id=node_id,
        session_id=session_1,
        job_id=job_id,
        status="RUNNING",
        started_at=datetime.now(UTC),
        expected_epoch=epoch_1,
    )
    assert stale_update is None
    db_job = await repository.get_job_by_id(job_id)
    assert db_job["status"] == "QUEUED"

    # 5. Stale Instance 1 attempts renew_job_lease_fenced -> REJECTED (returns None)
    lease_ts = datetime.now(UTC) + timedelta(seconds=60)
    stale_renew = await repository.renew_job_lease_fenced(
        node_id=node_id,
        session_id=session_1,
        job_id=job_id,
        expected_epoch=epoch_1,
        lease_expires_at=lease_ts,
    )
    assert stale_renew is None

    # 6. Authoritative Instance 2 attempts update_job_status_fenced -> ACCEPTED
    active_update = await repository.update_job_status_fenced(
        node_id=node_id,
        session_id=session_2,
        job_id=job_id,
        status="RUNNING",
        started_at=datetime.now(UTC),
        expected_epoch=epoch_2,
    )
    assert active_update is not None
    assert active_update["status"] == "RUNNING"

    # 7. Authoritative Instance 2 attempts renew_job_lease_fenced -> ACCEPTED
    active_renew = await repository.renew_job_lease_fenced(
        node_id=node_id,
        session_id=session_2,
        job_id=job_id,
        expected_epoch=epoch_2,
        lease_expires_at=lease_ts,
    )
    assert active_renew is not None


@pytest.mark.asyncio
async def test_volatile_redis_restart_recovery():
    """Verify Blocker B: Redis restart reconciliation detects lost volatile queues via run_id."""
    from app.modules.community import service

    user_id = await create_db_user()
    node = await repository.create_node(
        owner_id=user_id,
        display_name="Restart Test Node",
        public_key=ed25519.Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(Encoding.Raw, PublicFormat.Raw)
        .hex(),
    )
    node_id = node["id"]

    old_generation_run_id = "old-redis-run-id-12345"
    future_deadline = datetime.now(UTC) + timedelta(minutes=5)

    # Job 1: QUEUED with old run_id
    job1 = await repository.create_job(
        requester_id=user_id,
        node_id=node_id,
        model_name="llama3.2:1b",
        payload_digest="d" * 64,
        auth_key_id="k1",
        consent_policy_version="2026-02-09",
        hard_deadline_at=future_deadline,
        redis_run_id=old_generation_run_id,
    )
    job1_id = job1["id"]

    # Job 2: RUNNING with old run_id
    job2 = await repository.create_job(
        requester_id=user_id,
        node_id=node_id,
        model_name="llama3.2:1b",
        payload_digest="e" * 64,
        auth_key_id="k1",
        consent_policy_version="2026-02-09",
        hard_deadline_at=future_deadline,
        redis_run_id=old_generation_run_id,
    )
    job2_id = job2["id"]
    await repository.update_job_status(job2_id, "RUNNING", started_at=datetime.now(UTC))

    # Job 3: Same generation run_id (should NOT be swept)
    new_generation_run_id = "new-redis-run-id-67890"
    job3 = await repository.create_job(
        requester_id=user_id,
        node_id=node_id,
        model_name="llama3.2:1b",
        payload_digest="f" * 64,
        auth_key_id="k1",
        consent_policy_version="2026-02-09",
        hard_deadline_at=future_deadline,
        redis_run_id=new_generation_run_id,
    )
    job3_id = job3["id"]

    # Acquire ZSET concurrency reservations in Community Redis
    await streams.acquire_node_concurrency(
        node_id=node_id,
        job_id=job1_id,
        max_concurrent=5,
        lease_expires_at=future_deadline.timestamp(),
    )
    await streams.acquire_node_concurrency(
        node_id=node_id,
        job_id=job2_id,
        max_concurrent=5,
        lease_expires_at=future_deadline.timestamp(),
    )

    # Trigger recovery with current run_id = new_generation_run_id
    reconciled_count = await service.recover_expired_community_jobs(
        current_run_id=new_generation_run_id
    )
    assert reconciled_count >= 2

    # Verify Job 1 (QUEUED) transitioned to EXPIRED with failure code REDIS_RESTART_PAYLOAD_LOST
    db_job1 = await repository.get_job_by_id(job1_id)
    assert db_job1["status"] == "EXPIRED"
    assert db_job1["failure_code"] == "REDIS_RESTART_PAYLOAD_LOST"

    # Verify Job 2 (RUNNING) transitioned to OUTCOME_UNKNOWN with failure code REDIS_RESTART_STREAM_LOST
    db_job2 = await repository.get_job_by_id(job2_id)
    assert db_job2["status"] == "OUTCOME_UNKNOWN"
    assert db_job2["failure_code"] == "REDIS_RESTART_STREAM_LOST"

    # Verify Job 3 (current generation) remains untouched in QUEUED state
    db_job3 = await repository.get_job_by_id(job3_id)
    assert db_job3["status"] == "QUEUED"

    # Verify ZSET reservations for lost jobs were released in Community Redis
    cr = get_community_redis()
    if cr:
        zscore1 = await cr.zscore(f"ragbait:comm:node:{node_id}:reservations", str(job1_id))
        assert zscore1 is None
        zscore2 = await cr.zscore(f"ragbait:comm:node:{node_id}:reservations", str(job2_id))
        assert zscore2 is None


@pytest.mark.asyncio
async def test_dedicated_community_redis_isolation():
    """Verify Blocker B: Dedicated volatile Redis is strictly isolated from shared persistent Redis."""
    from app.core.config import settings
    from app.core.redis import get_community_redis
    from app.modules.community.schemas import SignedJobAuthorization

    # 1. Verify configuration separates the two endpoints
    assert settings.COMMUNITY_REDIS_URL != settings.REDIS_URL
    assert ":6380" in settings.COMMUNITY_REDIS_URL
    assert ":6379" in settings.REDIS_URL

    shared_r = get_redis()
    comm_r = get_community_redis()
    if not shared_r or not comm_r:
        pytest.skip("Redis instances not available")

    # 2. Write community job stream entry and chunk into community redis
    test_node_id = uuid4()
    test_job_id = uuid4()
    auth = SignedJobAuthorization(
        job_id=test_job_id,
        node_id=test_node_id,
        model_name="llama3.2:1b",
        payload_digest="digest-isolation",
        max_output_tokens=50,
        hard_deadline_ts=int(time.time()) + 120,
        auth_nonce="iso-nonce",
        signature="0" * 128,
        key_id="k1",
    )

    await streams.enqueue_job_dispatch(
        node_id=test_node_id,
        job_id=test_job_id,
        authorization=auth,
        payload={"model": "llama3.2:1b", "prompt": "confidential student material"},
    )
    await streams.append_job_chunk(test_job_id, seq_id=1, delta="chunk-1", done=False)

    # 3. Assert community keys exist in Community Redis
    stream_key = f"stream:node:{test_node_id}:jobs"
    chunks_key = f"stream:job:{test_job_id}:chunks"

    comm_stream_len = await comm_r.xlen(stream_key)
    assert comm_stream_len >= 1
    comm_chunks_len = await comm_r.xlen(chunks_key)
    assert comm_chunks_len >= 1

    # 4. Assert community keys DO NOT exist in Shared Persistent Redis (Fail-Closed Isolation)
    shared_stream_type = await shared_r.type(stream_key)
    assert shared_stream_type == "none"
    shared_chunks_type = await shared_r.type(chunks_key)
    assert shared_chunks_type == "none"

    # 5. Clean up test keys in community redis
    await comm_r.delete(stream_key, chunks_key)
