"""FastAPI authentication, authorization, and rate limiting dependencies."""

import logging
from collections.abc import Callable
from typing import Any

from fastapi import Depends, Header, Request

from app.core.config import settings
from app.core.database import get_pool, get_user_db_session
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
    """Require approved user status (or administrator)."""
    if profile.approval_status != "approved" and profile.role not in ("admin", "super_admin"):
        raise AuthorizationError(
            f"Account approval status is {profile.approval_status}, expected approved",
            code="ACCOUNT_NOT_APPROVED",
        )
    return profile


async def check_user_chat_entitlement(
    user_id: Any,
    profile: UserProfile,
) -> tuple[bool, str | None, str, int]:
    """Authoritatively evaluate whether a user satisfies the Chat contribution requirement.

    Rules:
    - Administrator (admin, super_admin): exempt, eligible = True.
    - Non-approved user: eligible = False, code = ACCOUNT_NOT_APPROVED.
    - Approved regular user: MUST have at least one active, validated, explicitly consented
      free-tier cloud credential (allows_community_sharing = true on a cloud_api provider).
      Private local Ollama, paid keys, or unvalidated keys do NOT qualify.
    """
    if profile.role in ("admin", "super_admin"):
        return True, None, "Admin account has full access to AI chat.", 1

    if profile.approval_status != "approved":
        return (
            False,
            "ACCOUNT_NOT_APPROVED",
            f"Account approval status is {profile.approval_status}.",
            0,
        )

    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id, c.provider_id, c.status, c.validation_status, c.sharing_scope, c.shared_consent,
                   p.runtime_type, p.allows_community_sharing
            FROM public.api_key_metadata c
            JOIN public.llm_providers p ON p.id = c.provider_id
            WHERE c.owner_id = $1
            ORDER BY c.created_at DESC
            """,
            user_id,
        )

    if not rows:
        return (
            False,
            "NO_CREDENTIAL_CONTRIBUTED",
            "No qualifying key contributed yet. Please add a verified free-tier cloud API key (e.g. Groq, OpenRouter, Cerebras) and enable community sharing to unlock AI Chat.",
            0,
        )

    # Count qualifying credentials
    qualifying_count = sum(
        1
        for r in rows
        if (
            r["runtime_type"] == "cloud_api"
            and r["allows_community_sharing"] is True
            and r["sharing_scope"] == "community_shared"
            and r["shared_consent"] is True
            and r["validation_status"] == "valid"
            and r["status"] == "active"
        )
    )

    if qualifying_count > 0:
        return (
            True,
            None,
            "Qualifying free-tier cloud credential contributed and active.",
            qualifying_count,
        )

    # Identify most informative rejection reason
    for r in rows:
        if r["runtime_type"] == "cloud_api" and r["allows_community_sharing"] is True:
            if r["sharing_scope"] == "community_shared" and r["shared_consent"] is True:
                if r["status"] != "active":
                    return (
                        False,
                        "CREDENTIAL_INACTIVE",
                        "Your contributed credential is currently inactive. Please enable your key in Key Management.",
                        0,
                    )
                if r["validation_status"] != "valid":
                    return (
                        False,
                        "VALIDATION_REQUIRED",
                        "Key validation required. Please run the validation probe in Key Management.",
                        0,
                    )
            elif r["sharing_scope"] != "community_shared" or not r["shared_consent"]:
                return (
                    False,
                    "SHARING_CONSENT_REQUIRED",
                    "Sharing consent required. Please enable community sharing on your key.",
                    0,
                )

    # If user has only local or non-sharable credentials
    for r in rows:
        if r["runtime_type"] != "cloud_api" or not r["allows_community_sharing"]:
            return (
                False,
                "PROVIDER_NOT_ELIGIBLE_FOR_SHARING",
                "Private local Ollama models or non-sharable providers do not satisfy the shared cloud contribution requirement. Please contribute an approved free-tier key.",
                0,
            )

    return (
        False,
        "NO_CREDENTIAL_CONTRIBUTED",
        "A qualifying free-tier cloud credential is required before accessing AI Chat.",
        0,
    )


async def require_chat_entitlement(
    current_user: AuthenticatedUser = Depends(get_current_user),
    profile: UserProfile = Depends(require_approved_user),
) -> UserProfile:
    """Enforce Chat contribution entitlement on conversation creation and generation."""
    eligible, reason_code, message, _ = await check_user_chat_entitlement(
        user_id=current_user.user_id,
        profile=profile,
    )
    if not eligible:
        raise AuthorizationError(
            message=message,
            code="CHAT_CONTRIBUTION_REQUIRED",
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
rate_limit_pair_code_create = rate_limiter(
    "pair_code_create", settings.RATE_LIMIT_PAIR_CODE_CREATE, 3600
)


def get_trusted_client_ip(request: Request) -> str:
    """Extract client IP, validating against TRUSTED_PROXY_IPS before reading X-Forwarded-For."""
    peer_ip = request.client.host if request.client else "127.0.0.1"
    if not settings.TRUSTED_PROXY_IPS or peer_ip not in settings.TRUSTED_PROXY_IPS:
        return peer_ip

    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return peer_ip

    # Parse rightmost untrusted hop
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    if not hops:
        return peer_ip

    for hop in reversed(hops):
        if hop not in settings.TRUSTED_PROXY_IPS:
            return hop
    return hops[0]
