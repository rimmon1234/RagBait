"""JWT verification and authoritative authentication credentials check."""

import logging
from typing import Any
from uuid import UUID

import jwt
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError
from pydantic import BaseModel
from supabase import Client, create_client

from app.core.config import settings
from app.core.errors import AuthenticationError

logger = logging.getLogger("ragbait.security")


class AuthenticatedUser(BaseModel):
    """Verified principal identity and claims."""

    user_id: UUID
    email: str
    role: str
    session_id: str
    claims: dict[str, Any]


def verify_supabase_jwt(token: str) -> AuthenticatedUser:
    """Validate cryptographic signature and mandatory claims of a Supabase JWT.

    Enforces:
    - Signature check using SUPABASE_JWT_SECRET.
    - aud == 'authenticated'.
    - role == 'authenticated' (rejects unexpected database roles).
    - Valid non-expired timestamp.
    - Valid sub as UUID.
    """
    try:
        # Decode without verification first to check header if needed
        unverified_header = jwt.get_unverified_header(token)
        alg = unverified_header.get("alg", "HS256")

        payload: dict[str, Any] = jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=[alg],
            audience="authenticated",
            options={
                "verify_aud": True,
                "verify_exp": True,
                "verify_signature": True,
            },
        )

        # 1. Enforce database role == 'authenticated'
        role = payload.get("role")
        if role != "authenticated":
            logger.warning(f"Rejected token with unauthorized database role: {role}")
            raise AuthenticationError("Unauthorized token role", code="UNAUTHORIZED_TOKEN_ROLE")

        # 2. Extract sub as UUID
        sub = payload.get("sub")
        if not sub:
            raise AuthenticationError("Token missing subject (sub)", code="INVALID_SUBJECT")

        try:
            user_uuid = UUID(sub)
        except ValueError as err:
            raise AuthenticationError(
                "Token subject is not a valid UUID", code="INVALID_SUBJECT"
            ) from err

        # 3. Extract stable session_id claim
        session_id = payload.get("session_id")
        if not session_id:
            # Fallback for environments where session_id might be named differently
            session_id = str(user_uuid)

        email = payload.get("email", "")

        return AuthenticatedUser(
            user_id=user_uuid,
            email=email,
            role=role,
            session_id=str(session_id),
            claims=payload,
        )

    except ExpiredSignatureError as e:
        logger.info(f"Token expired: {e}")
        raise AuthenticationError("Authentication token has expired", code="TOKEN_EXPIRED") from e
    except InvalidTokenError as e:
        logger.warning(f"Invalid token: {e}")
        raise AuthenticationError(f"Token verification failed: {e}", code="INVALID_TOKEN") from e


_supabase_client: Client | None = None


def get_supabase_client() -> Client:
    """Return or initialize singleton Supabase SDK client."""
    global _supabase_client
    if _supabase_client is None:
        _supabase_client = create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
    return _supabase_client


async def fetch_authoritative_auth_user(
    token: str, user_id: UUID | None = None
) -> tuple[bool, str | None]:
    """Authoritative server-side lookup of Auth user using documented client.auth.get_user(jwt).

    Validates the token with Supabase Auth and confirms that email_confirmed_at is populated.
    Does not rely on client-supplied claims or auth.admin.get_user_by_id().
    """
    try:
        client = get_supabase_client()
        user_resp = client.auth.get_user(jwt=token)
        if user_resp and user_resp.user:
            is_confirmed = user_resp.user.email_confirmed_at is not None
            return is_confirmed, user_resp.user.email
        return False, None
    except Exception as e:
        logger.warning(f"Authoritative Supabase Auth lookup failed: {e}")
        return False, None
