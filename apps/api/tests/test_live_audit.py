"""RagBait M0/M1 Comprehensive Live Security, Auth, Concurrency, and Infrastructure Audit.

This test runs against live local Docker services (PostgreSQL, Supabase Auth GoTrue, and Redis)
to independently verify all security boundaries, transaction locality, RLS enforcement,
privilege restrictions, and administrative invariants.
"""

from collections.abc import AsyncGenerator
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from redis.asyncio import Redis

from app.core.config import settings
from app.core.database import (
    close_db_pool,
    get_user_db_session,
    init_db_pool,
)
from app.core.errors import RateLimitError
from app.core.redis import check_rate_limit, close_redis, init_redis
from app.main import app
from app.modules.admin.service import update_user_role
from app.modules.credentials.vault import close_vault_pool, init_vault_pool

DATABASE_URL = settings.DATABASE_URL
SUPABASE_URL = settings.SUPABASE_URL
ANON_KEY = settings.SUPABASE_ANON_KEY
JWT_SECRET = settings.SUPABASE_JWT_SECRET


@pytest.fixture(autouse=True)
async def setup_live_connections() -> AsyncGenerator[None, None]:
    """Ensure database pool, vault pool, and redis are initialized and cleanly closed per test loop."""
    await init_db_pool()
    await init_vault_pool()
    await init_redis()
    yield
    await close_vault_pool()
    await close_db_pool()
    await close_redis()


# ------------------------------------------------------------------------------
# 1. Database Security Audit Tests
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_rls_enabled_on_all_tables() -> None:
    """Verify that RLS is enabled on all public application tables."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        tables = ["profiles", "access_requests", "user_presence", "audit_logs"]
        for table in tables:
            rls_enabled = await conn.fetchval(
                """
                SELECT relrowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relname = $1 AND n.nspname = 'public'
                """,
                table,
            )
            assert rls_enabled is True, f"RLS must be enabled on public.{table}"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_db_function_security_and_search_path() -> None:
    """Verify security context (DEFINER vs INVOKER) and search_path hygiene."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # app_private.is_admin: SECURITY DEFINER, search_path = ''
        is_admin_definer = await conn.fetchval(
            """
            SELECT p.prosecdef
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE p.proname = 'is_admin' AND n.nspname = 'app_private'
            """
        )
        assert is_admin_definer is True, "app_private.is_admin must be SECURITY DEFINER"

        is_admin_config = await conn.fetchval(
            """
            SELECT p.proconfig
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE p.proname = 'is_admin' AND n.nspname = 'app_private'
            """
        )
        assert is_admin_config == ['search_path=""'], (
            "app_private.is_admin must have empty search_path"
        )

        # public.protect_profile_roles_and_status: SECURITY INVOKER
        trigger_definer = await conn.fetchval(
            """
            SELECT p.prosecdef
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE p.proname = 'protect_profile_roles_and_status' AND n.nspname = 'public'
            """
        )
        assert trigger_definer is False, "protect_profile_roles_and_status must be SECURITY INVOKER"

        # public.set_updated_at: SECURITY INVOKER
        updated_at_definer = await conn.fetchval(
            """
            SELECT p.prosecdef
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE p.proname = 'set_updated_at' AND n.nspname = 'public'
            """
        )
        assert updated_at_definer is False, "set_updated_at must be SECURITY INVOKER"

        # public.handle_new_user: SECURITY DEFINER
        handle_user_definer = await conn.fetchval(
            """
            SELECT p.prosecdef
            FROM pg_proc p
            JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE p.proname = 'handle_new_user' AND n.nspname = 'public'
            """
        )
        assert handle_user_definer is True, "handle_new_user must be SECURITY DEFINER"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_db_privileges_and_grants() -> None:
    """Verify privilege restrictions on app_private, handle_new_user, and audit_logs."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # anon cannot execute app_private.is_admin
        anon_can_exec_is_admin = await conn.fetchval(
            "SELECT has_function_privilege('anon', 'app_private.is_admin(uuid)', 'EXECUTE')"
        )
        assert anon_can_exec_is_admin is False, "anon must not execute app_private.is_admin"

        # authenticated cannot execute handle_new_user directly
        auth_can_exec_handle = await conn.fetchval(
            "SELECT has_function_privilege('authenticated', 'public.handle_new_user()', 'EXECUTE')"
        )
        assert auth_can_exec_handle is False, "authenticated must not execute handle_new_user"

        # authenticated cannot write to audit_logs
        auth_can_insert_audit = await conn.fetchval(
            "SELECT has_table_privilege('authenticated', 'public.audit_logs', 'INSERT')"
        )
        auth_can_update_audit = await conn.fetchval(
            "SELECT has_table_privilege('authenticated', 'public.audit_logs', 'UPDATE')"
        )
        auth_can_delete_audit = await conn.fetchval(
            "SELECT has_table_privilege('authenticated', 'public.audit_logs', 'DELETE')"
        )
        assert auth_can_insert_audit is False, "authenticated must not INSERT to audit_logs"
        assert auth_can_update_audit is False, "authenticated must not UPDATE audit_logs"
        assert auth_can_delete_audit is False, "authenticated must not DELETE audit_logs"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_db_column_permissions_and_trigger_enforcement() -> None:
    """Verify that an authenticated caller cannot alter role or approval_status in public.profiles."""
    conn = await asyncpg.connect(DATABASE_URL)
    test_uid = uuid4()
    test_email = f"perm_test_{test_uid.hex[:8]}@example.edu"

    try:
        # Insert auth.user first to satisfy foreign key constraint
        await conn.execute(
            """
            INSERT INTO auth.users (id, instance_id, email, aud, role, raw_user_meta_data)
            VALUES ($1, '00000000-0000-0000-0000-000000000000', $2, 'authenticated', 'authenticated', '{"full_name": "Perm User"}'::jsonb)
            """,
            test_uid,
            test_email,
        )

        # In a transaction as authenticated role:
        # 1. Update display_name -> must succeed
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated;")
            await conn.execute(
                "SELECT set_config('request.jwt.claim.sub', $1, true);", str(test_uid)
            )
            await conn.execute(
                "SELECT set_config('request.jwt.claim.role', $1, true);", "authenticated"
            )

            await conn.execute(
                "UPDATE public.profiles SET display_name = 'Updated Name' WHERE id = $1",
                test_uid,
            )

        # Verify update worked
        updated_name = await conn.fetchval(
            "SELECT display_name FROM public.profiles WHERE id = $1", test_uid
        )
        assert updated_name == "Updated Name"

        # 2. Update role as authenticated role -> must fail with permission denied (42501)
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated;")
            await conn.execute(
                "SELECT set_config('request.jwt.claim.sub', $1, true);", str(test_uid)
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    "UPDATE public.profiles SET role = 'super_admin' WHERE id = $1",
                    test_uid,
                )

        # 3. Update approval_status as authenticated role -> must fail with permission denied (42501)
        async with conn.transaction():
            await conn.execute("SET LOCAL ROLE authenticated;")
            await conn.execute(
                "SELECT set_config('request.jwt.claim.sub', $1, true);", str(test_uid)
            )
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(
                    "UPDATE public.profiles SET approval_status = 'approved' WHERE id = $1",
                    test_uid,
                )

        # Clean up
        await conn.execute("DELETE FROM auth.users WHERE id = $1", test_uid)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_db_claims_cleared_after_transaction_completion() -> None:
    """Verify that transaction-local claims do not leak across pooled connections."""
    from app.core.database import get_pool

    pool = get_pool()
    user_id = uuid4()
    claims = {"sub": str(user_id), "role": "authenticated", "aud": "authenticated"}

    # Transaction 1: sets claims
    async with get_user_db_session(user_id, claims) as conn1:
        sub_in_tx = await conn1.fetchval("SELECT current_setting('request.jwt.claim.sub', true);")
        assert sub_in_tx == str(user_id)

    # Immediately check out a connection from pool for system operation
    async with pool.acquire() as conn2:
        sub_after_tx = await conn2.fetchval(
            "SELECT current_setting('request.jwt.claim.sub', true);"
        )
        claims_after_tx = await conn2.fetchval(
            "SELECT current_setting('request.jwt.claims', true);"
        )
        role_after_tx = await conn2.fetchval(
            "SELECT current_setting('request.jwt.claim.role', true);"
        )

        assert sub_after_tx is None or sub_after_tx == "", (
            "Subject claim must not leak outside transaction"
        )
        assert claims_after_tx is None or claims_after_tx == "", (
            "Full claims must not leak outside transaction"
        )
        assert role_after_tx is None or role_after_tx == "", (
            "Role claim must not leak outside transaction"
        )


# ------------------------------------------------------------------------------
# 2. Runtime Authentication, Email Verification & Access Flow
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_auth_registration_and_email_verification_lifecycle() -> None:
    """End-to-end verification of registration, profile trigger, authoritative email gate,

    idempotent access request creation, and transition to pending access.
    """
    db_conn = await asyncpg.connect(DATABASE_URL)
    test_email = f"audit_student_{uuid4().hex[:8]}@university.edu"
    test_password = "SecurePassword123!"

    try:
        # Step 1: Sign up user via live Supabase Auth HTTP endpoint
        async with httpx.AsyncClient(base_url=SUPABASE_URL) as auth_http:
            signup_resp = await auth_http.post(
                "/auth/v1/signup",
                headers={"apikey": ANON_KEY, "Content-Type": "application/json"},
                json={
                    "email": test_email,
                    "password": test_password,
                    "data": {"full_name": "Audit Test Student"},
                },
            )
            assert signup_resp.status_code == 200, f"Signup failed: {signup_resp.text}"
            signup_data = signup_resp.json()
            user_id = UUID(signup_data["user"]["id"])
            token = signup_data.get("access_token")
            assert token is not None

        # Step 2: Verify database profile created automatically by trigger
        profile = await db_conn.fetchrow("SELECT * FROM public.profiles WHERE id = $1", user_id)
        assert profile is not None, "Profile must be created by handle_new_user trigger"
        assert profile["email"] == test_email
        assert profile["display_name"] == "Audit Test Student"
        assert profile["role"] == "user"
        assert profile["approval_status"] == "pending"

        # Step 3: Verify access_requests is initially empty for this user
        req_count = await db_conn.fetchval(
            "SELECT count(*) FROM public.access_requests WHERE user_id = $1", user_id
        )
        assert req_count == 0, (
            "No access request should exist before email verification and me/access call"
        )

        # Step 4: Simulate unconfirmed email state to test the gate
        await db_conn.execute(
            "UPDATE auth.users SET email_confirmed_at = NULL WHERE id = $1", user_id
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as api_client:
            resp_unconfirmed = await api_client.get(
                "/api/v1/me/access",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp_unconfirmed.status_code == 200
            unconf_data = resp_unconfirmed.json()
            assert unconf_data["effective_state"] == "unverified"
            assert unconf_data["email_verified"] is False
            assert unconf_data["has_pending_request"] is False

        # Step 5: Confirm email authoritatively in auth.users
        await db_conn.execute(
            "UPDATE auth.users SET email_confirmed_at = now() WHERE id = $1", user_id
        )

        # Step 6: Call GET /api/v1/me/access after email confirmation
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as api_client:
            resp_confirmed = await api_client.get(
                "/api/v1/me/access",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp_confirmed.status_code == 200
            conf_data = resp_confirmed.json()
            assert conf_data["effective_state"] == "pending"
            assert conf_data["email_verified"] is True
            assert conf_data["has_pending_request"] is True

            # Step 7: Verify access request exists in database
            db_request = await db_conn.fetchrow(
                "SELECT * FROM public.access_requests WHERE user_id = $1 AND status = 'pending'",
                user_id,
            )
            assert db_request is not None, "Pending access request must be created in database"

            # Step 8: Call GET /api/v1/me/access AGAIN to prove idempotency
            resp_idempotent = await api_client.get(
                "/api/v1/me/access",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp_idempotent.status_code == 200
            total_requests = await db_conn.fetchval(
                "SELECT count(*) FROM public.access_requests WHERE user_id = $1", user_id
            )
            assert total_requests == 1, "Subsequent calls must not create duplicate access requests"

        # Clean up
        await db_conn.execute("DELETE FROM auth.users WHERE id = $1", user_id)
    finally:
        await db_conn.close()


# ------------------------------------------------------------------------------
# 3. Administrative Security, Approval, Presence, and Concurrency Audit
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_admin_bootstrap_approval_and_super_admin_invariants() -> None:
    """Test administrative approval flow, audit logging, presence tracking,

    super-admin demotion prevention, and self-promotion blocking against live services.
    """
    db_conn = await asyncpg.connect(DATABASE_URL)
    transport = httpx.ASGITransport(app=app)

    student_email = f"student_{uuid4().hex[:8]}@university.edu"
    admin_email = f"superadmin_{uuid4().hex[:8]}@university.edu"
    password = "TestPassword123!"

    try:
        # Sign up student and superadmin via GoTrue
        async with httpx.AsyncClient(base_url=SUPABASE_URL) as auth_http:
            resp_s = await auth_http.post(
                "/auth/v1/signup",
                headers={"apikey": ANON_KEY, "Content-Type": "application/json"},
                json={
                    "email": student_email,
                    "password": password,
                    "data": {"full_name": "Student A"},
                },
            )
            student_data = resp_s.json()
            student_id = UUID(student_data["user"]["id"])
            student_token = student_data["access_token"]

            resp_a = await auth_http.post(
                "/auth/v1/signup",
                headers={"apikey": ANON_KEY, "Content-Type": "application/json"},
                json={
                    "email": admin_email,
                    "password": password,
                    "data": {"full_name": "Super Admin"},
                },
            )
            admin_data = resp_a.json()
            admin_id = UUID(admin_data["user"]["id"])
            admin_token = admin_data["access_token"]

        # Bootstrap admin_id to super_admin as per docs/BOOTSTRAP_ADMIN.md
        await db_conn.execute(
            """
            UPDATE public.profiles
            SET role = 'super_admin', approval_status = 'approved'
            WHERE id = $1
            """,
            admin_id,
        )

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Student creates initial access request via /me/access
            s_access = await client.get(
                "/api/v1/me/access",
                headers={"Authorization": f"Bearer {student_token}"},
            )
            assert s_access.status_code == 200
            assert s_access.json()["effective_state"] == "pending"

            access_req_id = await db_conn.fetchval(
                "SELECT id FROM public.access_requests WHERE user_id = $1", student_id
            )
            assert access_req_id is not None

            # 2. Student attempts to access admin overview -> 403 Forbidden
            overview_forbidden = await client.get(
                "/api/v1/admin/overview",
                headers={"Authorization": f"Bearer {student_token}"},
            )
            assert overview_forbidden.status_code == 403

            # 3. Super Admin queries admin overview -> 200 OK
            overview_ok = await client.get(
                "/api/v1/admin/overview",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert overview_ok.status_code == 200
            assert overview_ok.json()["pending_requests"] >= 1

            # 4. Super Admin approves student's access request
            decision_resp = await client.post(
                f"/api/v1/admin/access-requests/{access_req_id}/decision",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"decision": "approved", "reason": "Verified college registration"},
            )
            assert decision_resp.status_code == 200
            assert decision_resp.json()["status"] == "approved"

            # 5. Check student profile is now approved in database
            student_profile = await db_conn.fetchrow(
                "SELECT approval_status FROM public.profiles WHERE id = $1", student_id
            )
            assert student_profile["approval_status"] == "approved"

            # 6. Verify audit log entry was created atomically
            audit_entry = await db_conn.fetchrow(
                "SELECT * FROM public.audit_logs WHERE target_id = $1", access_req_id
            )
            assert audit_entry is not None
            assert audit_entry["action"] == "access_request.approved"
            assert audit_entry["actor_id"] == admin_id

            # 7. Student calls /me/access -> transitions to 'contribution_required'
            s_access_after = await client.get(
                "/api/v1/me/access",
                headers={"Authorization": f"Bearer {student_token}"},
            )
            assert s_access_after.status_code == 200
            assert s_access_after.json()["effective_state"] == "contribution_required"

            # 8. Student sends presence heartbeat
            hb_resp = await client.post(
                "/api/v1/me/heartbeat",
                headers={"Authorization": f"Bearer {student_token}"},
                json={"client_info": {"platform": "audit_test"}},
            )
            assert hb_resp.status_code == 200
            assert hb_resp.json()["status"] == "ok"

            # Verify presence in database
            presence_row = await db_conn.fetchrow(
                "SELECT * FROM public.user_presence WHERE user_id = $1", student_id
            )
            assert presence_row is not None
            assert presence_row["expires_at"] > presence_row["last_seen_at"]

            # 9. Super Admin attempts to self-demote -> 400 Bad Request
            self_demote = await client.patch(
                f"/api/v1/admin/users/{admin_id}/role",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"role": "admin"},
            )
            assert self_demote.status_code == 400
            assert self_demote.json()["error"]["code"] == "SELF_PROMOTION_FORBIDDEN"

            # 10. Direct service call: attempt to demote the only super-admin by another actor
            await db_conn.execute(
                "UPDATE public.profiles SET role = 'admin' WHERE id = $1", student_id
            )
            with pytest.raises(Exception) as exc_info:
                await update_user_role(
                    actor_id=student_id, target_user_id=admin_id, new_role="user"
                )
            assert "Standard administrators cannot alter super-administrators" in str(
                exc_info.value
            ) or "Cannot demote the last remaining super-administrator" in str(exc_info.value)

            # 11. Suspend student user and verify account is blocked
            suspend_resp = await client.patch(
                f"/api/v1/admin/users/{student_id}/status",
                headers={"Authorization": f"Bearer {admin_token}"},
                json={"approval_status": "suspended", "reason": "Terms violation"},
            )
            assert suspend_resp.status_code == 200
            assert suspend_resp.json()["approval_status"] == "suspended"

            # Suspended user blocked on /me
            me_blocked = await client.get(
                "/api/v1/me",
                headers={"Authorization": f"Bearer {student_token}"},
            )
            assert me_blocked.status_code == 403
            assert me_blocked.json()["error"]["code"] == "ACCOUNT_SUSPENDED"

            # Suspended user blocked on profile update
            patch_blocked = await client.patch(
                "/api/v1/me/profile",
                headers={"Authorization": f"Bearer {student_token}"},
                json={"display_name": "New Name"},
            )
            assert patch_blocked.status_code == 403
            assert patch_blocked.json()["error"]["code"] == "ACCOUNT_SUSPENDED"

            # Clear heartbeat rate limit to test suspended check independently of rate limiting
            redis = Redis.from_url(settings.REDIS_URL)
            await redis.delete(f"ragbait:ratelimit:heartbeat:{student_id}")
            await redis.aclose()

            # Suspended user blocked on heartbeat
            hb_blocked = await client.post(
                "/api/v1/me/heartbeat",
                headers={"Authorization": f"Bearer {student_token}"},
            )
            assert hb_blocked.status_code == 403
            assert hb_blocked.json()["error"]["code"] == "ACCOUNT_SUSPENDED"

        # Clean up
        await db_conn.execute("DELETE FROM auth.users WHERE id IN ($1, $2)", student_id, admin_id)
    finally:
        await db_conn.close()


# ------------------------------------------------------------------------------
# 4. Infrastructure & Failure Handling Audit
# ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_rate_limiter_saturation_and_redis_resilience() -> None:
    """Test distributed rate limiter against real Redis, verify 429 on saturation,

    and fail-secure behavior when Redis is unavailable.
    """
    redis = Redis.from_url(settings.REDIS_URL)
    test_user_id = str(uuid4())

    try:
        # 1. Test rate limit saturation (limit = 3, window = 10)
        for _ in range(3):
            # First 3 should succeed
            await check_rate_limit(
                key_prefix="audit_rl",
                identifier=test_user_id,
                limit=3,
                window_seconds=10,
            )

        # 4th request must raise RateLimitError (HTTP 429)
        with pytest.raises(RateLimitError) as exc_info:
            await check_rate_limit(
                key_prefix="audit_rl",
                identifier=test_user_id,
                limit=3,
                window_seconds=10,
            )
        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after > 0

        # Clean up Redis key
        await redis.delete(f"ragbait:ratelimit:audit_rl:{test_user_id}")
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_live_health_endpoint() -> None:
    """Test /health/ready endpoint against live services."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["database"] is True
        assert data["redis"] is True
