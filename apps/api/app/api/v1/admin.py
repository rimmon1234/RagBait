"""Administrative endpoints for user, access request, role, and presence management."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.dependencies import (
    rate_limit_admin_mutate,
    rate_limit_admin_read,
    require_admin,
)
from app.modules.access.schemas import (
    AccessDecisionRequest,
    AccessRequestResponse,
    PaginatedAccessRequestsResponse,
)
from app.modules.admin.schemas import (
    OverviewResponse,
    PaginatedAuditLogsResponse,
    RoleUpdateRequest,
    StatusUpdateRequest,
)
from app.modules.admin.service import (
    get_admin_overview,
    list_access_requests,
    list_audit_logs,
    list_users,
    process_access_decision,
    update_user_role,
    update_user_status,
)
from app.modules.presence.schemas import PresenceListResponse
from app.modules.presence.service import get_presence_list
from app.modules.users.schemas import (
    PaginatedUsersResponse,
    UserAdminResponse,
    UserProfile,
)

router = APIRouter(prefix="/admin", tags=["Administration"])


@router.get(
    "/overview", response_model=OverviewResponse, dependencies=[Depends(rate_limit_admin_read)]
)
async def get_overview(
    _: UserProfile = Depends(require_admin),
) -> OverviewResponse:
    """Retrieve platform aggregate statistics."""
    return await get_admin_overview()


@router.get(
    "/users", response_model=PaginatedUsersResponse, dependencies=[Depends(rate_limit_admin_read)]
)
async def get_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    role: str | None = Query(None),
    approval_status: str | None = Query(None),
    _: UserProfile = Depends(require_admin),
) -> PaginatedUsersResponse:
    """List platform users with pagination and search."""
    return await list_users(
        page=page,
        page_size=page_size,
        search=search,
        role=role,
        approval_status=approval_status,
    )


@router.get(
    "/access-requests",
    response_model=PaginatedAccessRequestsResponse,
    dependencies=[Depends(rate_limit_admin_read)],
)
async def get_access_requests(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query("pending"),
    _: UserProfile = Depends(require_admin),
) -> PaginatedAccessRequestsResponse:
    """List access requests."""
    return await list_access_requests(
        page=page,
        page_size=page_size,
        status=status,
    )


@router.post(
    "/access-requests/{request_id}/decision",
    response_model=AccessRequestResponse,
    dependencies=[Depends(rate_limit_admin_mutate)],
)
async def decide_access_request(
    request_id: UUID,
    body: AccessDecisionRequest,
    admin: UserProfile = Depends(require_admin),
) -> AccessRequestResponse:
    """Approve or reject a pending access request."""
    return await process_access_decision(
        actor_id=admin.id,
        request_id=request_id,
        decision=body.decision,
        reason=body.reason,
    )


@router.patch(
    "/users/{user_id}/role",
    response_model=UserAdminResponse,
    dependencies=[Depends(rate_limit_admin_mutate)],
)
async def change_user_role(
    user_id: UUID,
    body: RoleUpdateRequest,
    admin: UserProfile = Depends(require_admin),
) -> UserAdminResponse:
    """Change a user's role with advisory locking and super-admin protection."""
    return await update_user_role(
        actor_id=admin.id,
        target_user_id=user_id,
        new_role=body.role,
    )


@router.patch(
    "/users/{user_id}/status",
    response_model=UserAdminResponse,
    dependencies=[Depends(rate_limit_admin_mutate)],
)
async def change_user_status(
    user_id: UUID,
    body: StatusUpdateRequest,
    admin: UserProfile = Depends(require_admin),
) -> UserAdminResponse:
    """Suspend or reactivate a user account with advisory locking and super-admin protection."""
    return await update_user_status(
        actor_id=admin.id,
        target_user_id=user_id,
        new_status=body.approval_status,
        reason=body.reason,
    )


@router.get(
    "/presence", response_model=PresenceListResponse, dependencies=[Depends(rate_limit_admin_read)]
)
async def get_active_users(
    threshold_seconds: int = Query(120, ge=10, le=3600),
    _: UserProfile = Depends(require_admin),
) -> PresenceListResponse:
    """List currently active and recently active users."""
    return await get_presence_list(threshold_seconds=threshold_seconds)


@router.get(
    "/audit-logs",
    response_model=PaginatedAuditLogsResponse,
    dependencies=[Depends(rate_limit_admin_read)],
)
async def get_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _: UserProfile = Depends(require_admin),
) -> PaginatedAuditLogsResponse:
    """List administrative audit logs."""
    return await list_audit_logs(
        page=page,
        page_size=page_size,
    )
