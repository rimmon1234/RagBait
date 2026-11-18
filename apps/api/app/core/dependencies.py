"""FastAPI authentication, authorization, and rate limiting dependencies."""

import logging
from collections.abc import Callable
from typing import Any

from fastapi import Depends, Header, Request

from app.core.config import settings
from app.core.database import get_user_db_session
from app.core.errors import AuthenticationError, AuthorizationError
from app.core.redis import check_rate_limit
from app.core.security import AuthenticatedUser, verify_supabase_jwt
from app.modules.users.schemas import UserProfile

logger = logging.getLogger("ragbait.dependencies")


async def get_token(authorization: str | None = Header(None)) -> str:
    """Extract Bearer token from Authorization header."""
    if not authorization:
        raise AuthenticationError("Authorization header is missing", code="MISSING_TOKEN")

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthenticationError(
            "Authorization header format must be Bearer <token>", code="INVALID_TOKEN_FORMAT"
        )

    return parts[1]


async def get_current_user(request: Request, token: str = Depends(get_token)) -> AuthenticatedUser:
    """Validate JWT token and return authenticated principal."""
    user = verify_supabase_jwt(token)
    request.state.user_id = str(user.user_id)
    return user


async def get_current_profile(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> UserProfile:
    """Fetch database profile for the authenticated user using RLS-aware session."""
    async with get_user_db_session(current_user.user_id, current_user.claims) as conn:
        row = await conn.fetchrow(
            """
            SELECT id, email, display_name, role, approval_status, created_at, updated_at
            FROM public.profiles
            WHERE id = $1
            """,
            current_user.user_id,
        )

    if not row:
        logger.warning(f"Profile not found for authenticated user {current_user.user_id}")
        raise AuthenticationError("User profile does not exist", code="PROFILE_NOT_FOUND")

    return UserProfile(
        id=row["id"],
        email=row["email"],
        display_name=row["display_name"],
        role=row["role"],
        approval_status=row["approval_status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def require_not_suspended(
    profile: UserProfile = Depends(get_current_profile),
) -> UserProfile:
    """Ensure user is not suspended."""
    if profile.approval_status == "suspended":
        raise AuthorizationError("Account is suspended", code="ACCOUNT_SUSPENDED")
    return profile


async def require_approved_user(
    profile: UserProfile = Depends(require_not_suspended),
) -> UserProfile:
    """Require approved user status."""
    if profile.approval_status != "approved":
        raise AuthorizationError(
            f"Account approval status is {profile.approval_status}, expected approved",
            code="ACCOUNT_NOT_APPROVED",
        )
    return profile


async def require_admin(
    profile: UserProfile = Depends(require_not_suspended),
) -> UserProfile:
    """Enforce administrator privileges (admin or super_admin with approved status)."""
    if profile.role not in ("admin", "super_admin"):
        raise AuthorizationError("Administrator privileges required", code="ADMIN_REQUIRED")
    if profile.approval_status != "approved":
        raise AuthorizationError("Administrator account is not approved", code="ADMIN_NOT_APPROVED")
    return profile


async def require_super_admin(
    profile: UserProfile = Depends(require_admin),
) -> UserProfile:
    """Enforce super-administrator privileges."""
    if profile.role != "super_admin":
        raise AuthorizationError(
            "Super-administrator privileges required", code="SUPER_ADMIN_REQUIRED"
        )
    return profile


def rate_limiter(prefix: str, limit: int, window: int = 60) -> Callable[..., Any]:
    """Factory creating rate limiting dependencies based on principal identifier."""

    async def _rate_limit_dependency(
        user: AuthenticatedUser = Depends(get_current_user),
    ) -> None:
        await check_rate_limit(
            key_prefix=prefix,
            identifier=str(user.user_id),
            limit=limit,
            window_seconds=window,
        )

    return _rate_limit_dependency


# Common rate limiter dependencies
rate_limit_general = rate_limiter("api", settings.RATE_LIMIT_DEFAULT, 60)
rate_limit_heartbeat = rate_limiter("heartbeat", settings.RATE_LIMIT_HEARTBEAT, 45)
rate_limit_admin_read = rate_limiter("admin_read", settings.RATE_LIMIT_ADMIN_READ, 60)
rate_limit_admin_mutate = rate_limiter("admin_mutate", settings.RATE_LIMIT_ADMIN_MUTATE, 60)
