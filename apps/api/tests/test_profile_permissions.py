"""Tests for profile column-level permission boundaries and schema validation."""

from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.core.dependencies import get_current_profile, get_current_user
from app.core.security import AuthenticatedUser
from app.main import app
from app.modules.users.schemas import ProfileUpdateRequest
from tests.conftest import create_test_jwt, make_test_profile


def test_schema_rejects_unauthorized_columns() -> None:
    """Test that ProfileUpdateRequest strictly forbids role or approval_status fields."""
    with pytest.raises(ValidationError):
        # Attempting to supply role must fail validation
        ProfileUpdateRequest.model_validate({"display_name": "Valid Name", "role": "admin"})

    with pytest.raises(ValidationError):
        # Attempting to supply approval_status must fail validation
        ProfileUpdateRequest.model_validate(
            {"display_name": "Valid Name", "approval_status": "approved"}
        )

    with pytest.raises(ValidationError):
        # Extra fields of any kind must fail validation
        ProfileUpdateRequest.model_validate({"display_name": "Valid Name", "is_admin": True})


def test_schema_allows_valid_display_name() -> None:
    """Test that valid display_name passes validation."""
    req = ProfileUpdateRequest.model_validate({"display_name": "Alice Cooper"})
    assert req.display_name == "Alice Cooper"


@pytest.mark.asyncio
async def test_patch_profile_extra_fields_returns_422(client: httpx.AsyncClient) -> None:
    """API test: PATCH /api/v1/me/profile with extra/forbidden fields returns HTTP 422."""
    user_id = uuid4()
    token = create_test_jwt(user_id=user_id)
    profile = make_test_profile(user_id=user_id)

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id=user_id,
        email="test@example.edu",
        role="authenticated",
        session_id=str(uuid4()),
        claims={},
    )
    app.dependency_overrides[get_current_profile] = lambda: profile

    try:
        # User attempts to self-promote via PATCH
        resp = await client.patch(
            "/api/v1/me/profile",
            headers={"Authorization": f"Bearer {token}"},
            json={"display_name": "Hacker", "role": "super_admin"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
    finally:
        app.dependency_overrides.clear()
