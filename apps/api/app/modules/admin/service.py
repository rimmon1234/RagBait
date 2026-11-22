"""Administrative domain business logic, concurrency control, and audit logging."""

import json
import logging
from uuid import UUID

from app.core.database import acquire_super_admin_advisory_lock, get_admin_db_session
from app.core.errors import AppError, AuthorizationError, ConflictError, NotFoundError
from app.modules.access.schemas import (
    AccessRequestResponse,
    PaginatedAccessRequestsResponse,
)
from app.modules.admin.schemas import (
    AuditLogResponse,
    OverviewResponse,
    PaginatedAuditLogsResponse,
)
from app.modules.users.schemas import (
    ApprovalStatus,
    PaginatedUsersResponse,
    UserAdminResponse,
    UserRole,
)

logger = logging.getLogger("ragbait.admin")


async def get_admin_overview() -> OverviewResponse:
    """Retrieve platform aggregate statistics."""
    async with get_admin_db_session() as conn:
        counts = await conn.fetchrow(
            """
            SELECT
                count(*) AS total_users,
                count(*) FILTER (WHERE approval_status = 'approved') AS approved_users,
                count(*) FILTER (WHERE approval_status = 'suspended') AS suspended_users,
                count(*) FILTER (WHERE approval_status = 'rejected') AS rejected_users
            FROM public.profiles
            """
        )

        pending_req_count = await conn.fetchval(
            """
            SELECT count(*)
            FROM public.access_requests
            WHERE status = 'pending'
            """
        )

        presence = await conn.fetchrow(
            """
            SELECT
                count(DISTINCT user_id) FILTER (WHERE expires_at > now()) AS active_now,
                count(DISTINCT user_id) FILTER (WHERE last_seen_at > now() - interval '15 minutes') AS active_recent
            FROM public.user_presence
            """
        )

    return OverviewResponse(
        total_users=counts["total_users"] if counts else 0,
        pending_requests=pending_req_count or 0,
        approved_users=counts["approved_users"] if counts else 0,
        suspended_users=counts["suspended_users"] if counts else 0,
        rejected_users=counts["rejected_users"] if counts else 0,
        active_users_now=presence["active_now"] if presence else 0,
        active_users_recent=presence["active_recent"] if presence else 0,
    )


async def list_users(
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
    role: str | None = None,
    approval_status: str | None = None,
) -> PaginatedUsersResponse:
    """List users with pagination, text search, and role/status filtering."""
    offset = (page - 1) * page_size
    conditions: list[str] = []
    params: list[object] = []

    if search:
        params.append(f"%{search}%")
        conditions.append(f"(email ILIKE ${len(params)} OR display_name ILIKE ${len(params)})")
    if role:
        params.append(role)
        conditions.append(f"role = ${len(params)}")
    if approval_status:
        params.append(approval_status)
        conditions.append(f"approval_status = ${len(params)}")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    async with get_admin_db_session() as conn:
        total = await conn.fetchval(f"SELECT count(*) FROM public.profiles {where_clause}", *params)

        params.extend([page_size, offset])
        rows = await conn.fetch(
            f"""
            SELECT id, email, display_name, role, approval_status, created_at, updated_at
            FROM public.profiles
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """,
            *params,
        )

    items = [
        UserAdminResponse(
            id=r["id"],
            email=r["email"],
            display_name=r["display_name"],
            role=r["role"],
            approval_status=r["approval_status"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]

    total_count = total or 0
    total_pages = max(1, (total_count + page_size - 1) // page_size)

    return PaginatedUsersResponse(
        items=items,
        total=total_count,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


async def list_access_requests(
    page: int = 1,
    page_size: int = 20,
    status: str | None = "pending",
) -> PaginatedAccessRequestsResponse:
    """List access requests with joined user profile information."""
    offset = (page - 1) * page_size
    conditions: list[str] = []
    params: list[object] = []

    if status:
        params.append(status)
        conditions.append(f"ar.status = ${len(params)}")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    async with get_admin_db_session() as conn:
        total = await conn.fetchval(
            f"SELECT count(*) FROM public.access_requests ar {where_clause}", *params
        )

        params.extend([page_size, offset])
        rows = await conn.fetch(
            f"""
            SELECT
                ar.id, ar.user_id, ar.status, ar.decision_reason,
                ar.reviewed_by, ar.reviewed_at, ar.created_at, ar.updated_at,
                p.email, p.display_name
            FROM public.access_requests ar
            JOIN public.profiles p ON p.id = ar.user_id
            {where_clause}
            ORDER BY ar.created_at DESC
            LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """,
            *params,
        )

    items = [
        AccessRequestResponse(
            id=r["id"],
            user_id=r["user_id"],
            email=r["email"],
            display_name=r["display_name"],
            status=r["status"],
            decision_reason=r["decision_reason"],
            reviewed_by=r["reviewed_by"],
            reviewed_at=r["reviewed_at"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]

    total_count = total or 0
    total_pages = max(1, (total_count + page_size - 1) // page_size)

    return PaginatedAccessRequestsResponse(
        items=items,
        total=total_count,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


async def process_access_decision(
    actor_id: UUID,
    request_id: UUID,
    decision: str,
    reason: str | None,
) -> AccessRequestResponse:
    """Approve or reject an access request atomically updating request, profile, and audit logs."""
    async with get_admin_db_session() as conn:
        req = await conn.fetchrow(
            """
            SELECT ar.id, ar.user_id, ar.status, p.email, p.display_name
            FROM public.access_requests ar
            JOIN public.profiles p ON p.id = ar.user_id
            WHERE ar.id = $1
            FOR UPDATE
            """,
            request_id,
        )

        if not req:
            raise NotFoundError("Access request not found")

        user_id = req["user_id"]

        # 1. Update access request
        updated_req = await conn.fetchrow(
            """
            UPDATE public.access_requests
            SET status = $1, decision_reason = $2, reviewed_by = $3, reviewed_at = now(), updated_at = now()
            WHERE id = $4
            RETURNING id, user_id, status, decision_reason, reviewed_by, reviewed_at, created_at, updated_at
            """,
            decision,
            reason,
            actor_id,
            request_id,
        )

        # 2. Update user profile approval status
        await conn.execute(
            """
            UPDATE public.profiles
            SET approval_status = $1, updated_at = now()
            WHERE id = $2
            """,
            decision,
            user_id,
        )

        # 3. Create audit log entry atomically
        payload = json.dumps(
            {
                "decision": decision,
                "reason": reason,
                "user_id": str(user_id),
                "email": req["email"],
                "request_id": str(request_id),
            }
        )
        await conn.execute(
            """
            INSERT INTO public.audit_logs (actor_id, action, target_type, target_id, payload)
            VALUES ($1, $2, 'access_request', $3, $4::jsonb)
            """,
            actor_id,
            f"access_request.{decision}",
            request_id,
            payload,
        )

    return AccessRequestResponse(
        id=updated_req["id"],
        user_id=updated_req["user_id"],
        email=req["email"],
        display_name=req["display_name"],
        status=updated_req["status"],
        decision_reason=updated_req["decision_reason"],
        reviewed_by=updated_req["reviewed_by"],
        reviewed_at=updated_req["reviewed_at"],
        created_at=updated_req["created_at"],
        updated_at=updated_req["updated_at"],
    )


async def update_user_role(
    actor_id: UUID,
    target_user_id: UUID,
    new_role: UserRole,
) -> UserAdminResponse:
    """Safely promote or demote a user with advisory locking and super-admin protection."""
    if actor_id == target_user_id:
        raise AppError(
            code="SELF_PROMOTION_FORBIDDEN",
            message="Administrators cannot modify their own role",
            status_code=400,
        )

    async with get_admin_db_session() as conn:
        # 1. Acquire transaction-level advisory lock to serialize super-admin changes
        await acquire_super_admin_advisory_lock(conn)

        # 2. Verify actor's current database role
        actor_profile = await conn.fetchrow(
            "SELECT role, approval_status FROM public.profiles WHERE id = $1 FOR UPDATE",
            actor_id,
        )
        if not actor_profile or actor_profile["approval_status"] != "approved":
            raise AuthorizationError("Actor profile is not active")

        actor_role = actor_profile["role"]

        # Only super_admins can promote someone to super_admin
        if new_role == "super_admin" and actor_role != "super_admin":
            raise AuthorizationError("Only super-administrators can promote users to super-admin")

        # 3. Verify target user
        target_profile = await conn.fetchrow(
            "SELECT id, email, display_name, role, approval_status, created_at, updated_at FROM public.profiles WHERE id = $1 FOR UPDATE",
            target_user_id,
        )
        if not target_profile:
            raise NotFoundError("Target user not found")

        old_role = target_profile["role"]

        # Ordinary admin cannot alter a super_admin
        if old_role == "super_admin" and actor_role != "super_admin":
            raise AuthorizationError("Standard administrators cannot alter super-administrators")

        # 4. Protect the last remaining super_admin
        if old_role == "super_admin" and new_role != "super_admin":
            super_admin_count = await conn.fetchval(
                "SELECT count(*) FROM public.profiles WHERE role = 'super_admin' AND approval_status = 'approved'"
            )
            if (super_admin_count or 0) <= 1:
                raise ConflictError("Cannot demote the last remaining super-administrator")

        # 5. Apply update
        updated = await conn.fetchrow(
            """
            UPDATE public.profiles
            SET role = $1, updated_at = now()
            WHERE id = $2
            RETURNING id, email, display_name, role, approval_status, created_at, updated_at
            """,
            new_role,
            target_user_id,
        )

        # 6. Audit log entry
        payload = json.dumps(
            {
                "previous_role": old_role,
                "new_role": new_role,
                "email": target_profile["email"],
            }
        )
        await conn.execute(
            """
            INSERT INTO public.audit_logs (actor_id, action, target_type, target_id, payload)
            VALUES ($1, 'user.role_change', 'user', $2, $3::jsonb)
            """,
            actor_id,
            target_user_id,
            payload,
        )

    return UserAdminResponse(
        id=updated["id"],
        email=updated["email"],
        display_name=updated["display_name"],
        role=updated["role"],
        approval_status=updated["approval_status"],
        created_at=updated["created_at"],
        updated_at=updated["updated_at"],
    )


async def update_user_status(
    actor_id: UUID,
    target_user_id: UUID,
    new_status: ApprovalStatus,
    reason: str | None = None,
) -> UserAdminResponse:
    """Suspend or reactivate a user account with concurrency and super-admin protection."""
    if actor_id == target_user_id and new_status == "suspended":
        raise AppError(
            code="SELF_SUSPENSION_FORBIDDEN",
            message="Administrators cannot suspend their own account",
            status_code=400,
        )

    async with get_admin_db_session() as conn:
        await acquire_super_admin_advisory_lock(conn)

        actor_profile = await conn.fetchrow(
            "SELECT role, approval_status FROM public.profiles WHERE id = $1 FOR UPDATE",
            actor_id,
        )
        if not actor_profile or actor_profile["approval_status"] != "approved":
            raise AuthorizationError("Actor profile is not active")

        actor_role = actor_profile["role"]

        target_profile = await conn.fetchrow(
            "SELECT id, email, display_name, role, approval_status, created_at, updated_at FROM public.profiles WHERE id = $1 FOR UPDATE",
            target_user_id,
        )
        if not target_profile:
            raise NotFoundError("Target user not found")

        old_status = target_profile["approval_status"]

        # Ordinary admin cannot alter a super_admin
        if target_profile["role"] == "super_admin" and actor_role != "super_admin":
            raise AuthorizationError("Standard administrators cannot alter super-administrators")

        # Protect last super-admin from suspension
        if target_profile["role"] == "super_admin" and new_status == "suspended":
            super_admin_count = await conn.fetchval(
                "SELECT count(*) FROM public.profiles WHERE role = 'super_admin' AND approval_status = 'approved'"
            )
            if (super_admin_count or 0) <= 1:
                raise ConflictError("Cannot suspend the last remaining super-administrator")

        updated = await conn.fetchrow(
            """
            UPDATE public.profiles
            SET approval_status = $1, updated_at = now()
            WHERE id = $2
            RETURNING id, email, display_name, role, approval_status, created_at, updated_at
            """,
            new_status,
            target_user_id,
        )

        payload = json.dumps(
            {
                "previous_status": old_status,
                "new_status": new_status,
                "reason": reason,
                "email": target_profile["email"],
            }
        )
        await conn.execute(
            """
            INSERT INTO public.audit_logs (actor_id, action, target_type, target_id, payload)
            VALUES ($1, 'user.status_change', 'user', $2, $3::jsonb)
            """,
            actor_id,
            target_user_id,
            payload,
        )

    return UserAdminResponse(
        id=updated["id"],
        email=updated["email"],
        display_name=updated["display_name"],
        role=updated["role"],
        approval_status=updated["approval_status"],
        created_at=updated["created_at"],
        updated_at=updated["updated_at"],
    )


async def list_audit_logs(
    page: int = 1,
    page_size: int = 20,
) -> PaginatedAuditLogsResponse:
    """Retrieve paginated audit logs for administrators."""
    offset = (page - 1) * page_size

    async with get_admin_db_session() as conn:
        total = await conn.fetchval("SELECT count(*) FROM public.audit_logs")
        rows = await conn.fetch(
            """
            SELECT id, actor_id, action, target_type, target_id, payload, created_at
            FROM public.audit_logs
            ORDER BY created_at DESC
            LIMIT $1 OFFSET $2
            """,
            page_size,
            offset,
        )

    items = [
        AuditLogResponse(
            id=r["id"],
            actor_id=r["actor_id"],
            action=r["action"],
            target_type=r["target_type"],
            target_id=r["target_id"],
            payload=json.loads(r["payload"]) if isinstance(r["payload"], str) else r["payload"],
            created_at=r["created_at"],
        )
        for r in rows
    ]

    total_count = total or 0
    total_pages = max(1, (total_count + page_size - 1) // page_size)

    return PaginatedAuditLogsResponse(
        items=items,
        total=total_count,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )
