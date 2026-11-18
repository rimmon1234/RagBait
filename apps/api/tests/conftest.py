"""Test configuration and reusable fixtures."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import jwt
import pytest

from app.core.config import settings
from app.main import app
from app.modules.users.schemas import ApprovalStatus, UserProfile, UserRole


def create_test_jwt(
    user_id: UUID,
    email: str = "test@example.edu",
    role: str = "authenticated",
    session_id: str | None = None,
    expires_in_minutes: int = 60,
    secret: str = settings.SUPABASE_JWT_SECRET,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Generate a valid signed Supabase-style JWT for testing."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "aud": "authenticated",
        "session_id": session_id or str(uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_in_minutes)).timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(payload, secret, algorithm="HS256")


def make_test_profile(
    user_id: UUID | None = None,
    email: str = "student@example.edu",
    display_name: str = "Student User",
    role: UserRole = "user",
    approval_status: ApprovalStatus = "approved",
) -> UserProfile:
    """Generate a test UserProfile instance."""
    now = datetime.now(UTC)
    return UserProfile(
        id=user_id or uuid4(),
        email=email,
        display_name=display_name,
        role=role,
        approval_status=approval_status,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def mock_pool() -> AsyncMock:
    """Mock asyncpg connection pool."""
    pool = AsyncMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    return pool


@pytest.fixture
async def client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Test client for FastAPI app."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
