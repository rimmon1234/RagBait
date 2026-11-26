"""Comprehensive Security, Authorization, and Lifecycle Tests for Credential Management (M2.3)."""

from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
import pytest

from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.dependencies import get_current_profile, get_current_user
from app.core.redis import close_redis, init_redis
from app.core.security import AuthenticatedUser
from app.main import app
from app.modules.credentials.validator import ValidationOutcome, ValidationResult
from app.modules.credentials.vault import close_vault_pool, init_vault_pool
from app.modules.users.schemas import ApprovalStatus, UserRole
from tests.conftest import create_test_jwt, make_test_profile

CREATED_TEST_USERS: list[UUID] = []


@pytest.fixture(autouse=True)
async def setup_test_environment():
    """Ensure database connection pools and Redis are initialized and cleaned up."""
    await init_db_pool()
    await init_vault_pool()
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
    await close_vault_pool()
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
# 1. Credential Ownership & Cross-Owner Isolation Tests
# ==============================================================================


@pytest.mark.anyio
async def test_create_and_list_owned_credentials(client: httpx.AsyncClient):
    """User A can create and list their own credentials. Never returns plaintext."""
    user_a = await create_db_user("user_a@university.edu")
    headers_a = setup_auth_context(user_a)

    raw_key = "gsk_test_credential_user_a_12345"
    payload = {
        "provider_id": "groq",
        "nickname": "My Research Groq Key",
        "api_key": raw_key,
        "daily_request_limit": 1000,
        "monthly_request_limit": 25000,
    }

    # 1. Create credential
    res = await client.post("/api/v1/me/api-keys", json=payload, headers=headers_a)
    assert res.status_code == 201
    data = res.json()
    assert data["provider_id"] == "groq"
    assert data["nickname"] == "My Research Groq Key"
    assert data["status"] == "disabled"  # Default disabled
    assert data["validation_status"] == "unvalidated"  # Default unvalidated
    assert data["sharing_scope"] == "owner_only"
    assert data["masked_key"] == "gsk_...2345"
    # CRITICAL: Plaintext key must NOT be returned
    assert "api_key" not in data
    assert raw_key not in str(data)

    cred_id = data["id"]

    # 2. List credentials
    list_res = await client.get("/api/v1/me/api-keys", headers=headers_a)
    assert list_res.status_code == 200
    list_data = list_res.json()
    assert list_data["total"] >= 1
    found = next((c for c in list_data["items"] if c["id"] == cred_id), None)
    assert found is not None
    assert found["masked_key"] == "gsk_...2345"
    assert raw_key not in str(list_data)


@pytest.mark.anyio
async def test_cross_owner_isolation_full_matrix(client: httpx.AsyncClient):
    """User B receives uniform 404 for any operation on User A's credential."""
    user_a = await create_db_user("owner_a@university.edu")
    user_b = await create_db_user("owner_b@university.edu")

    headers_a = setup_auth_context(user_a)

    # User A creates a key
    create_res = await client.post(
        "/api/v1/me/api-keys",
        json={
            "provider_id": "groq",
            "nickname": "User A Key",
            "api_key": "gsk_confidential_key_a_9999",
        },
        headers=headers_a,
    )
    assert create_res.status_code == 201
    cred_id = create_res.json()["id"]

    # Switch to User B
    headers_b = setup_auth_context(user_b)

    # 1. User B listing must not contain User A's key
    list_b = await client.get("/api/v1/me/api-keys", headers=headers_b)
    assert list_b.status_code == 200
    assert not any(c["id"] == cred_id for c in list_b.json()["items"])

    # 2. User B GET detail returns 404
    get_res = await client.get(f"/api/v1/me/api-keys/{cred_id}", headers=headers_b)
    assert get_res.status_code == 404
    assert get_res.json()["error"]["code"] == "NOT_FOUND"

    # 3. User B PATCH update returns 404
    patch_res = await client.patch(
        f"/api/v1/me/api-keys/{cred_id}",
        json={"nickname": "Hacked Name"},
        headers=headers_b,
    )
    assert patch_res.status_code == 404
    assert patch_res.json()["error"]["code"] == "NOT_FOUND"

    # 4. User B POST validate returns 404
    val_res = await client.post(f"/api/v1/me/api-keys/{cred_id}/validate", headers=headers_b)
    assert val_res.status_code == 404
    assert val_res.json()["error"]["code"] == "NOT_FOUND"

    # 5. User B POST enable returns 404
    enable_res = await client.post(f"/api/v1/me/api-keys/{cred_id}/enable", headers=headers_b)
    assert enable_res.status_code == 404
    assert enable_res.json()["error"]["code"] == "NOT_FOUND"

    # 6. User B POST disable returns 404
    disable_res = await client.post(f"/api/v1/me/api-keys/{cred_id}/disable", headers=headers_b)
    assert disable_res.status_code == 404
    assert disable_res.json()["error"]["code"] == "NOT_FOUND"

    # 7. User B DELETE returns 404
    del_res = await client.delete(f"/api/v1/me/api-keys/{cred_id}", headers=headers_b)
    assert del_res.status_code == 404
    assert del_res.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.anyio
async def test_admin_cannot_access_contributor_credentials_via_api(client: httpx.AsyncClient):
    """Platform administrator cannot view or manipulate another contributor's credentials."""
    contributor = await create_db_user("contributor_key_owner@university.edu")
    admin = await create_db_user("admin_attacker@university.edu", role="super_admin")

    headers_contrib = setup_auth_context(contributor)

    create_res = await client.post(
        "/api/v1/me/api-keys",
        json={
            "provider_id": "groq",
            "nickname": "Private Research Key",
            "api_key": "gsk_contributor_secret_material_xyz",
        },
        headers=headers_contrib,
    )
    assert create_res.status_code == 201
    cred_id = create_res.json()["id"]

    # Admin attempts to inspect contributor credential via /me/api-keys/{id}
    headers_admin = setup_auth_context(admin, role="super_admin")
    get_res = await client.get(f"/api/v1/me/api-keys/{cred_id}", headers=headers_admin)
    assert get_res.status_code == 404
    assert get_res.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.anyio
async def test_forged_owner_id_in_request_rejected(client: httpx.AsyncClient):
    """Attempting to inject a custom owner_id in the create request is rejected with 422."""
    user = await create_db_user("honest_user@university.edu")
    headers = setup_auth_context(user)
    victim_id = str(uuid4())

    payload = {
        "provider_id": "groq",
        "nickname": "Forged Key",
        "api_key": "gsk_some_secret_key_12345",
        "owner_id": victim_id,  # Extra field
    }

    res = await client.post("/api/v1/me/api-keys", json=payload, headers=headers)
    assert res.status_code == 422  # Extra forbidden by Pydantic schema


# ==============================================================================
# 2. Authentication & Access Enforcement Tests
# ==============================================================================


@pytest.mark.anyio
async def test_unauthenticated_request_rejected(client: httpx.AsyncClient):
    """Requests without a valid Bearer token receive 401."""
    res = await client.get("/api/v1/me/api-keys")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "MISSING_TOKEN"


@pytest.mark.anyio
async def test_unapproved_user_rejected(client: httpx.AsyncClient):
    """Pending and suspended users cannot access credential management."""
    pending_user = await create_db_user("pending@university.edu", status="pending")
    headers_pending = setup_auth_context(pending_user, status="pending")

    res = await client.get("/api/v1/me/api-keys", headers=headers_pending)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "ACCOUNT_NOT_APPROVED"

    suspended_user = await create_db_user("suspended@university.edu", status="suspended")
    headers_suspended = setup_auth_context(suspended_user, status="suspended")

    res2 = await client.get("/api/v1/me/api-keys", headers=headers_suspended)
    assert res2.status_code == 403
    assert res2.json()["error"]["code"] == "ACCOUNT_SUSPENDED"


# ==============================================================================
# 3. Sharing Scope & Explicit Consent Tests
# ==============================================================================


@pytest.mark.anyio
async def test_community_sharing_consent_rules(client: httpx.AsyncClient):
    """Community sharing requires explicit consent and provider eligibility."""
    user = await create_db_user("consent_tester@university.edu")
    headers = setup_auth_context(user)

    # 1. Community sharing without consent is rejected
    res_no_consent = await client.post(
        "/api/v1/me/api-keys",
        json={
            "provider_id": "openrouter",
            "nickname": "Shared No Consent",
            "api_key": "sk-or-v1-key-without-consent-123",
            "sharing_scope": "community_shared",
            "shared_consent": False,
        },
        headers=headers,
    )
    assert res_no_consent.status_code == 422

    # 2. Community sharing on an unapproved provider (e.g. Gemini) is rejected
    res_unapproved_provider = await client.post(
        "/api/v1/me/api-keys",
        json={
            "provider_id": "gemini",
            "nickname": "Gemini Shared",
            "api_key": "AIzaSy_gemini_fake_key_12345",
            "sharing_scope": "community_shared",
            "shared_consent": True,
        },
        headers=headers,
    )
    assert res_unapproved_provider.status_code == 409
    assert (
        "not approved for community sharing" in res_unapproved_provider.json()["error"]["message"]
    )

    # 3. Community sharing on approved provider (OpenRouter) with consent succeeds
    res_approved = await client.post(
        "/api/v1/me/api-keys",
        json={
            "provider_id": "openrouter",
            "nickname": "Valid Shared OpenRouter",
            "api_key": "sk-or-v1-key-with-explicit-consent-987",
            "sharing_scope": "community_shared",
            "shared_consent": True,
        },
        headers=headers,
    )
    assert res_approved.status_code == 201
    data = res_approved.json()
    assert data["sharing_scope"] == "community_shared"
    assert data["shared_consent"] is True
    assert data["sharing_consent_timestamp"] is not None
    assert data["consent_version"] == "v1"

    cred_id = data["id"]

    # 4. Switching back to owner_only revokes consent and clears timestamp
    update_res = await client.patch(
        f"/api/v1/me/api-keys/{cred_id}",
        json={"sharing_scope": "owner_only"},
        headers=headers,
    )
    assert update_res.status_code == 200
    updated = update_res.json()
    assert updated["sharing_scope"] == "owner_only"
    assert updated["shared_consent"] is False
    assert updated["sharing_consent_timestamp"] is None


# ==============================================================================
# 4. Validation Probes & Lifecycle (Enable/Disable/Delete) Tests
# ==============================================================================


@pytest.mark.anyio
async def test_validation_probe_and_enable_flow(client: httpx.AsyncClient):
    """Credential must pass non-generative validation before being enabled."""
    user = await create_db_user("lifecycle_tester@university.edu")
    headers = setup_auth_context(user)

    # 1. Create credential (starts disabled and unvalidated)
    create_res = await client.post(
        "/api/v1/me/api-keys",
        json={
            "provider_id": "groq",
            "nickname": "Lifecycle Key",
            "api_key": "gsk_live_lifecycle_key_12345",
        },
        headers=headers,
    )
    assert create_res.status_code == 201
    cred_id = create_res.json()["id"]
    assert create_res.json()["status"] == "disabled"
    assert create_res.json()["validation_status"] == "unvalidated"

    # 2. Attempting to enable unvalidated credential must fail with 409
    enable_fail = await client.post(f"/api/v1/me/api-keys/{cred_id}/enable", headers=headers)
    assert enable_fail.status_code == 409
    assert "must pass validation" in enable_fail.json()["error"]["message"]

    # 3. Mock provider validation check (returning VALID)
    mock_result = ValidationResult(
        outcome=ValidationOutcome.VALID,
        safe_code="AUTHENTICATED",
        message="Groq API credential authenticated successfully.",
    )
    with patch(
        "app.modules.credentials.service.validate_provider_credential",
        new=AsyncMock(return_value=mock_result),
    ) as mock_probe:
        val_res = await client.post(f"/api/v1/me/api-keys/{cred_id}/validate", headers=headers)
        assert val_res.status_code == 200
        val_data = val_res.json()
        assert val_data["outcome"] == "valid"
        assert val_data["safe_code"] == "AUTHENTICATED"
        mock_probe.assert_called_once()

    # 4. Now enabling must succeed
    enable_res = await client.post(f"/api/v1/me/api-keys/{cred_id}/enable", headers=headers)
    assert enable_res.status_code == 200
    assert enable_res.json()["status"] == "active"

    # 5. Disable credential
    disable_res = await client.post(f"/api/v1/me/api-keys/{cred_id}/disable", headers=headers)
    assert disable_res.status_code == 200
    assert disable_res.json()["status"] == "disabled"

    # 6. Delete credential
    del_res = await client.delete(f"/api/v1/me/api-keys/{cred_id}", headers=headers)
    assert del_res.status_code == 200
    assert del_res.json()["deleted"] is True

    # Confirm record is gone
    get_after_del = await client.get(f"/api/v1/me/api-keys/{cred_id}", headers=headers)
    assert get_after_del.status_code == 404


@pytest.mark.anyio
async def test_validation_error_outcomes(client: httpx.AsyncClient):
    """Provider error responses are translated to safe diagnostic categories."""
    user = await create_db_user("val_outcomes@university.edu")
    headers = setup_auth_context(user)

    create_res = await client.post(
        "/api/v1/me/api-keys",
        json={
            "provider_id": "groq",
            "nickname": "Probe Test Key",
            "api_key": "gsk_probe_error_key_999",
        },
        headers=headers,
    )
    cred_id = create_res.json()["id"]

    # Case 1: Invalid key
    mock_invalid = ValidationResult(
        outcome=ValidationOutcome.INVALID_CREDENTIAL,
        safe_code="INVALID_API_KEY",
        message="Key rejected.",
    )
    with patch(
        "app.modules.credentials.service.validate_provider_credential",
        new=AsyncMock(return_value=mock_invalid),
    ):
        res1 = await client.post(f"/api/v1/me/api-keys/{cred_id}/validate", headers=headers)
        assert res1.status_code == 200
        assert res1.json()["outcome"] == "invalid_credential"

    # Enabling after invalid validation must fail with 409
    enable_blocked = await client.post(f"/api/v1/me/api-keys/{cred_id}/enable", headers=headers)
    assert enable_blocked.status_code == 409

    # Case 2: Upstream 5xx / timeout
    mock_unavailable = ValidationResult(
        outcome=ValidationOutcome.PROVIDER_UNAVAILABLE,
        safe_code="TIMEOUT",
        message="Provider timeout.",
    )
    with patch(
        "app.modules.credentials.service.validate_provider_credential",
        new=AsyncMock(return_value=mock_unavailable),
    ):
        res2 = await client.post(f"/api/v1/me/api-keys/{cred_id}/validate", headers=headers)
        assert res2.status_code == 200
        assert res2.json()["outcome"] == "provider_unavailable"


# ==============================================================================
# 5. Secret Leakage Tests
# ==============================================================================


@pytest.mark.anyio
async def test_zero_secret_leakage_across_all_endpoints(client: httpx.AsyncClient):
    """Plaintext keys never appear in API responses, metadata table, or audit logs."""
    user = await create_db_user("leak_tester@university.edu")
    headers = setup_auth_context(user)
    raw_key = "gsk_super_confidential_raw_key_material_unique_987654"

    # 1. Create
    res = await client.post(
        "/api/v1/me/api-keys",
        json={
            "provider_id": "groq",
            "nickname": "Secret Leakage Test Key",
            "api_key": raw_key,
        },
        headers=headers,
    )
    assert res.status_code == 201
    assert raw_key not in res.text
    cred_id = res.json()["id"]

    # 2. Get detail
    detail_res = await client.get(f"/api/v1/me/api-keys/{cred_id}", headers=headers)
    assert detail_res.status_code == 200
    assert raw_key not in detail_res.text

    # 3. List
    list_res = await client.get("/api/v1/me/api-keys", headers=headers)
    assert list_res.status_code == 200
    assert raw_key not in list_res.text

    # 4. Check public.api_key_metadata table
    pool = get_pool()
    async with pool.acquire() as conn:
        meta_row = await conn.fetchrow(
            "SELECT * FROM public.api_key_metadata WHERE id = $1", UUID(cred_id)
        )
        assert meta_row is not None
        assert raw_key not in str(dict(meta_row))

    # 5. Check public.audit_logs table
    async with pool.acquire() as conn:
        audit_rows = await conn.fetch(
            "SELECT payload FROM public.audit_logs WHERE target_id = $1", UUID(cred_id)
        )
        assert len(audit_rows) >= 1
        for a in audit_rows:
            assert raw_key not in str(a["payload"])
