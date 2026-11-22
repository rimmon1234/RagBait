"""Access control state calculation and idempotent request creation service."""

import logging
from typing import Any
from uuid import UUID

from app.core.database import get_user_db_session
from app.core.security import fetch_authoritative_auth_user
from app.modules.access.schemas import AccessStateResponse, EffectiveState
from app.modules.users.schemas import UserProfile

logger = logging.getLogger("ragbait.access")


async def resolve_effective_access_state(
    token: str,
    user_id: UUID,
    claims: dict[str, Any],
    profile: UserProfile,
) -> AccessStateResponse:
    """Authoritatively determine the user's access state and ensure access request exists if verified.

    Flow:
    1. Authoritative check of email verification status (Supabase Auth server-side).
    2. If unverified -> effective_state = "unverified".
    3. If verified -> idempotently create pending access request if none exists.
    4. Derive effective state:
       - suspended -> "suspended"
       - rejected -> "rejected"
       - approved + admin -> "admin"
       - approved + user -> "contribution_required"
       - pending -> "pending"
    """
    # Authoritative check: strictly verify email_confirmed_at via Supabase Auth
    # Never rely on client-supplied claims or default email_verified JWT attributes
    is_confirmed, _ = await fetch_authoritative_auth_user(token, user_id)

    if not is_confirmed:
        return AccessStateResponse(
            effective_state="unverified",
            email_verified=False,
            role=profile.role,
            approval_status=profile.approval_status,
            has_pending_request=False,
        )

    # User is email-verified; check or insert access request idempotently
    has_pending = False
    async with get_user_db_session(user_id, claims) as conn:
        # Check if a pending or approved request exists
        existing_req = await conn.fetchrow(
            """
            SELECT id, status FROM public.access_requests
            WHERE user_id = $1 AND status = 'pending'
            LIMIT 1
            """,
            user_id,
        )

        if existing_req:
            has_pending = True
        elif profile.approval_status == "pending":
            # Idempotently insert initial pending access request
            await conn.execute(
                """
                INSERT INTO public.access_requests (user_id, status)
                VALUES ($1, 'pending')
                ON CONFLICT (user_id) WHERE status = 'pending' DO NOTHING
                """,
                user_id,
            )
            has_pending = True

    # Compute effective state
    effective_state: EffectiveState
    if profile.approval_status == "suspended":
        effective_state = "suspended"
    elif profile.role in ("admin", "super_admin"):
        effective_state = "admin"
    elif profile.approval_status == "rejected":
        effective_state = "rejected"
    elif profile.approval_status == "approved":
        effective_state = "contribution_required"
    else:
        effective_state = "pending"

    return AccessStateResponse(
        effective_state=effective_state,
        email_verified=True,
        role=profile.role,
        approval_status=profile.approval_status,
        has_pending_request=has_pending,
    )
