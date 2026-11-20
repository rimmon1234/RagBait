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
from uuid import UUID, uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.dependencies import get_current_profile, get_current_user
from app.core.redis import close_redis, init_redis
from app.core.security import AuthenticatedUser
from app.main import app
from app.modules.community import crypto, repository
from app.modules.users.schemas import ApprovalStatus, UserRole
from tests.conftest import create_test_jwt, make_test_profile

CREATED_TEST_USERS: list[UUID] = []


@pytest.fixture(autouse=True)
async def setup_test_environment():
    """Ensure database connection pools and Redis are initialized and cleaned up."""
    await init_db_pool()
    await init_redis()
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
    await close_db_pool()
    await close_redis()


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
    assert len(pairing_code) == 9
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
