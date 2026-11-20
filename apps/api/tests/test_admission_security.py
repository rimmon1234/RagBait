"""Comprehensive Security and Multi-Tenant Isolation Tests for Admission & Usage Ledger (M2.4)."""

from uuid import UUID, uuid4

import httpx
import pytest

from app.core.database import close_db_pool, get_pool, init_db_pool
from app.core.dependencies import get_current_profile, get_current_user
from app.core.errors import AuthorizationError
from app.core.redis import close_redis, get_redis, init_redis
from app.core.security import AuthenticatedUser
from app.main import app
from app.modules.admission.schemas import AdmissionRequest, SettlementRequest
from app.modules.admission.service import (
    mark_dispatched,
    reserve_generation,
    settle_reservation,
)
from app.modules.credentials.repository import store_credential, update_validation_status
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

    redis_conn = get_redis()
    if redis_conn:
        keys = await redis_conn.keys("ragbait:rate:*")
        keys.extend(await redis_conn.keys("ragbait:concurrency:*"))
        keys.extend(await redis_conn.keys("ragbait:cooldown:*"))
        if keys:
            await redis_conn.delete(*keys)

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
        unique_email = f"sec_user_{user_id.hex[:8]}@university.edu"
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
# 1. Contributor Usage Endpoint Authentication & Permissions
# ==============================================================================


@pytest.mark.anyio
async def test_usage_endpoint_requires_authenticated_approved_user(client: httpx.AsyncClient):
    """Unauthenticated or unapproved users cannot access /api/v1/me/usage."""
    # 1. No auth headers -> 401
    res_no_auth = await client.get("/api/v1/me/usage")
    assert res_no_auth.status_code == 401

    # 2. Pending user -> 403
    pending_user = await create_db_user("pending@university.edu", status="pending")
    headers_pending = setup_auth_context(pending_user, status="pending")
    res_pending = await client.get("/api/v1/me/usage", headers=headers_pending)
    assert res_pending.status_code == 403

    # 3. Approved user -> 200
    approved_user = await create_db_user("approved@university.edu", status="approved")
    headers_approved = setup_auth_context(approved_user, status="approved")
    res_approved = await client.get("/api/v1/me/usage", headers=headers_approved)
    assert res_approved.status_code == 200
    data = res_approved.json()
    assert data["daily_request_usage"] == 0
    assert data["daily_spending_cents"] == 0


# ==============================================================================
# 2. Cross-User Usage Data Isolation
# ==============================================================================


@pytest.mark.anyio
async def test_cross_user_usage_isolation(client: httpx.AsyncClient):
    """User B cannot see User A's reservations, spending, or request counts."""
    user_a = await create_db_user("user_a@university.edu")
    user_b = await create_db_user("user_b@university.edu")

    # Provision credential for User A
    cred_a = await store_credential(
        owner_id=user_a,
        provider_id="groq",
        nickname="Key A",
        raw_key=f"gsk_test_{uuid4().hex}",
        status="active",
        daily_request_limit=100,
        daily_spending_limit_cents=500,
    )
    await update_validation_status(cred_a.id, user_a, "valid")

    # User A performs a reservation and settlement
    req = AdmissionRequest(
        user_id=user_a,
        credential_id=cred_a.id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key=f"idem_{uuid4().hex}",
        request_id=f"req_{uuid4().hex}",
        estimated_input_tokens=1000,
        max_output_tokens=1000,
    )
    reservation = await reserve_generation(req)
    await mark_dispatched(reservation.reservation_id)
    await settle_reservation(
        reservation.reservation_id,
        SettlementRequest(
            actual_input_tokens=1000,
            actual_output_tokens=1000,
            duration_ms=450,
            provider_status_code=200,
        ),
    )

    # User B queries their usage endpoint
    headers_b = setup_auth_context(user_b)
    res_b = await client.get("/api/v1/me/usage", headers=headers_b)
    assert res_b.status_code == 200
    data_b = res_b.json()
    assert data_b["daily_request_usage"] == 0
    assert data_b["daily_spending_cents"] == 0
    assert len(data_b["recent_reservations"]) == 0

    # User A queries their usage endpoint
    headers_a = setup_auth_context(user_a)
    res_a = await client.get("/api/v1/me/usage", headers=headers_a)
    assert res_a.status_code == 200
    data_a = res_a.json()
    assert data_a["daily_request_usage"] == 1
    assert data_a["daily_spending_cents"] > 0
    assert len(data_a["recent_reservations"]) == 1
    assert data_a["recent_reservations"][0]["id"] == str(reservation.reservation_id)


# ==============================================================================
# 3. Cross-Owner Credential Access Authorization in Admission
# ==============================================================================


@pytest.mark.anyio
async def test_cross_owner_credential_authorization():
    """User B cannot reserve with User A's private credential, but can when community-shared."""
    user_a = await create_db_user("owner_a@university.edu")
    user_b = await create_db_user("borrower_b@university.edu")

    # 1. Private credential (owner_only)
    cred_private = await store_credential(
        owner_id=user_a,
        provider_id="groq",
        nickname="Private Key",
        raw_key=f"gsk_test_{uuid4().hex}",
        status="active",
        sharing_scope="owner_only",
        shared_consent=False,
    )
    await update_validation_status(cred_private.id, user_a, "valid")

    req_private = AdmissionRequest(
        user_id=user_b,  # Borrower User B
        credential_id=cred_private.id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key=f"idem_{uuid4().hex}",
        request_id=f"req_{uuid4().hex}",
        estimated_input_tokens=100,
        max_output_tokens=100,
    )

    with pytest.raises(AuthorizationError) as exc_info:
        await reserve_generation(req_private)
    assert "Access to credential denied" in str(exc_info.value)

    # 2. Shared credential (community_shared with consent)
    cred_shared = await store_credential(
        owner_id=user_a,
        provider_id="groq",
        nickname="Community Shared Key",
        raw_key=f"gsk_test_{uuid4().hex}",
        status="active",
        sharing_scope="community_shared",
        shared_consent=True,
    )
    await update_validation_status(cred_shared.id, user_a, "valid")

    req_shared = AdmissionRequest(
        user_id=user_b,  # Borrower User B
        credential_id=cred_shared.id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key=f"idem_{uuid4().hex}",
        request_id=f"req_{uuid4().hex}",
        estimated_input_tokens=100,
        max_output_tokens=100,
    )

    reservation = await reserve_generation(req_shared)
    assert reservation.reservation_id is not None
    assert reservation.status == "reserved"


# ==============================================================================
# 4. No Raw Secrets, Keys, or Prompts in Usage Ledger
# ==============================================================================


@pytest.mark.anyio
async def test_no_raw_secrets_or_prompts_in_ledger_storage():
    """Verify that ledger tables never store or log raw API keys, tokens, or prompt texts."""
    pool = get_pool()
    user_id = await create_db_user("audit@university.edu")
    raw_api_key = f"gsk_secret_never_leak_{uuid4().hex}"

    cred = await store_credential(
        owner_id=user_id,
        provider_id="groq",
        nickname="Audit Key",
        raw_key=raw_api_key,
        status="active",
    )
    await update_validation_status(cred.id, user_id, "valid")

    req = AdmissionRequest(
        user_id=user_id,
        credential_id=cred.id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key=f"idem_{uuid4().hex}",
        request_id=f"req_{uuid4().hex}",
        estimated_input_tokens=150,
        max_output_tokens=250,
    )
    reservation = await reserve_generation(req)
    await mark_dispatched(reservation.reservation_id)
    await settle_reservation(
        reservation.reservation_id,
        SettlementRequest(
            actual_input_tokens=150,
            actual_output_tokens=250,
            duration_ms=300,
            provider_status_code=200,
        ),
    )

    async with pool.acquire() as conn:
        # Check column names of llm_usage_reservations
        res_cols = await conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'llm_usage_reservations'
            """
        )
        col_names = {r["column_name"] for r in res_cols}
        assert "api_key" not in col_names
        assert "secret" not in col_names
        assert "prompt" not in col_names
        assert "raw_prompt" not in col_names
        assert "completion" not in col_names

        # Check raw row values
        row = await conn.fetchrow(
            "SELECT * FROM public.llm_usage_reservations WHERE id = $1",
            reservation.reservation_id,
        )
        row_str = str(dict(row))
        assert raw_api_key not in row_str

        # Check event log row values
        event_rows = await conn.fetch(
            "SELECT * FROM public.llm_usage_events WHERE reservation_id = $1",
            reservation.reservation_id,
        )
        for ev in event_rows:
            ev_str = str(dict(ev))
            assert raw_api_key not in ev_str


# ==============================================================================
# 5. Ledger Immutability Enforcement
# ==============================================================================


@pytest.mark.anyio
async def test_usage_events_table_immutability():
    """Direct UPDATE or DELETE on llm_usage_events is blocked by trigger (SQLSTATE 42501)."""
    pool = get_pool()
    user_id = await create_db_user("immutable@university.edu")
    cred = await store_credential(
        owner_id=user_id,
        provider_id="groq",
        nickname="Immutable Key",
        raw_key=f"gsk_test_{uuid4().hex}",
        status="active",
    )
    await update_validation_status(cred.id, user_id, "valid")

    req = AdmissionRequest(
        user_id=user_id,
        credential_id=cred.id,
        provider_id="groq",
        model_id="openai/gpt-oss-120b",
        idempotency_key=f"idem_{uuid4().hex}",
        request_id=f"req_{uuid4().hex}",
        estimated_input_tokens=100,
        max_output_tokens=100,
    )
    reservation = await reserve_generation(req)

    async with pool.acquire() as conn:
        event = await conn.fetchrow(
            "SELECT id FROM public.llm_usage_events WHERE reservation_id = $1 LIMIT 1",
            reservation.reservation_id,
        )
        assert event is not None
        event_id = event["id"]

        # Attempting UPDATE must fail
        with pytest.raises(Exception) as exc_update:
            await conn.execute(
                "UPDATE public.llm_usage_events SET event_type = 'tampered' WHERE id = $1",
                event_id,
            )
        assert "42501" in str(exc_update.value) or "immutable" in str(exc_update.value).lower()

        # Attempting DELETE must fail
        with pytest.raises(Exception) as exc_delete:
            await conn.execute(
                "DELETE FROM public.llm_usage_events WHERE id = $1",
                event_id,
            )
        assert "42501" in str(exc_delete.value) or "immutable" in str(exc_delete.value).lower()
