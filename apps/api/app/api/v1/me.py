"""User identity, access status, profile, and heartbeat endpoints."""

from fastapi import APIRouter, Depends

from app.core.dependencies import (
    get_current_profile,
    get_current_user,
    get_token,
    rate_limit_general,
    rate_limit_heartbeat,
    require_not_suspended,
)
from app.core.security import AuthenticatedUser
from app.modules.access.schemas import AccessStateResponse
from app.modules.access.service import resolve_effective_access_state
from app.modules.credentials.schemas import ChatEntitlementResponse
from app.modules.credentials.service import check_chat_contribution_entitlement
from app.modules.presence.schemas import HeartbeatRequest, HeartbeatResponse
from app.modules.presence.service import record_presence_heartbeat
from app.modules.users.schemas import ProfileUpdateRequest, UserProfile
from app.modules.users.service import update_user_display_name

router = APIRouter(prefix="/me", tags=["Identity"])


@router.get("", response_model=UserProfile, dependencies=[Depends(rate_limit_general)])
async def get_my_profile(
    profile: UserProfile = Depends(require_not_suspended),
) -> UserProfile:
    """Return the profile of the currently authenticated user."""
    return profile


@router.get(
    "/access", response_model=AccessStateResponse, dependencies=[Depends(rate_limit_general)]
)
async def get_my_access_state(
    token: str = Depends(get_token),
    current_user: AuthenticatedUser = Depends(get_current_user),
    profile: UserProfile = Depends(get_current_profile),
) -> AccessStateResponse:
    """Determine effective access state and create initial access request if email is verified."""
    return await resolve_effective_access_state(
        token=token,
        user_id=current_user.user_id,
        claims=current_user.claims,
        profile=profile,
    )


@router.get(
    "/chat-entitlement",
    response_model=ChatEntitlementResponse,
    dependencies=[Depends(rate_limit_general)],
)
async def get_my_chat_entitlement(
    current_user: AuthenticatedUser = Depends(get_current_user),
    _: UserProfile = Depends(require_not_suspended),
) -> ChatEntitlementResponse:
    """Return the user's AI Chat contribution eligibility and status."""
    is_entitled, reason_code, count = await check_chat_contribution_entitlement(
        current_user.user_id
    )
    return ChatEntitlementResponse(
        eligible=is_entitled,
        reason_code=reason_code,
        eligible_credential_count=count,
    )


@router.patch("/profile", response_model=UserProfile, dependencies=[Depends(rate_limit_general)])
async def update_my_profile(
    body: ProfileUpdateRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    _: UserProfile = Depends(require_not_suspended),
) -> UserProfile:
    """Update user's mutable profile fields (display_name). Extra fields are forbidden."""
    return await update_user_display_name(
        user_id=current_user.user_id,
        full_claims=current_user.claims,
        new_display_name=body.display_name,
    )


@router.post(
    "/heartbeat", response_model=HeartbeatResponse, dependencies=[Depends(rate_limit_heartbeat)]
)
async def send_heartbeat(
    body: HeartbeatRequest = HeartbeatRequest(),
    current_user: AuthenticatedUser = Depends(get_current_user),
    _: UserProfile = Depends(require_not_suspended),
) -> HeartbeatResponse:
    """Send presence heartbeat pulse using stable session_id claim."""
    last_seen, expires_at = await record_presence_heartbeat(
        user_id=current_user.user_id,
        claims=current_user.claims,
        session_id=current_user.session_id,
        client_info=body.client_info or {},
    )
    return HeartbeatResponse(
        status="ok",
        last_seen_at=last_seen,
        expires_at=expires_at,
    )
