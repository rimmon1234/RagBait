"""Tests for cryptographic JWT verification and identity resolution."""

from uuid import uuid4

import pytest

from app.core.errors import AuthenticationError
from app.core.security import verify_supabase_jwt
from tests.conftest import create_test_jwt


def test_valid_jwt_verification() -> None:
    """Test that a valid Supabase JWT is correctly verified and claims extracted."""
    user_id = uuid4()
    session_id = str(uuid4())
    token = create_test_jwt(user_id=user_id, email="student@university.edu", session_id=session_id)

    auth_user = verify_supabase_jwt(token)
    assert auth_user.user_id == user_id
    assert auth_user.email == "student@university.edu"
    assert auth_user.role == "authenticated"
    assert auth_user.session_id == session_id


def test_expired_jwt_rejected() -> None:
    """Test that an expired JWT raises AuthenticationError with TOKEN_EXPIRED."""
    user_id = uuid4()
    token = create_test_jwt(user_id=user_id, expires_in_minutes=-10)

    with pytest.raises(AuthenticationError) as exc_info:
        verify_supabase_jwt(token)
    assert exc_info.value.code == "TOKEN_EXPIRED"


def test_invalid_signature_rejected() -> None:
    """Test that a JWT signed with a different key is rejected."""
    user_id = uuid4()
    token = create_test_jwt(user_id=user_id, secret="wrong-secret-key-at-least-32-chars-long")

    with pytest.raises(AuthenticationError) as exc_info:
        verify_supabase_jwt(token)
    assert exc_info.value.code == "INVALID_TOKEN"


def test_unauthorized_database_role_rejected() -> None:
    """Test that a token carrying role other than 'authenticated' is rejected."""
    user_id = uuid4()
    token = create_test_jwt(user_id=user_id, role="anon")

    with pytest.raises(AuthenticationError) as exc_info:
        verify_supabase_jwt(token)
    assert exc_info.value.code == "UNAUTHORIZED_TOKEN_ROLE"


def test_missing_or_invalid_sub_rejected() -> None:
    """Test that tokens with malformed UUID sub claims are rejected."""
    token = create_test_jwt(user_id=uuid4(), extra_claims={"sub": "not-a-uuid"})

    with pytest.raises(AuthenticationError) as exc_info:
        verify_supabase_jwt(token)
    assert exc_info.value.code == "INVALID_SUBJECT"
