"""Tests for administrative authorization and listing endpoints."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest

from app.core.dependencies import get_current_profile, get_current_user
from app.core.security import AuthenticatedUser
from app.main import app
from tests.conftest import create_test_jwt, make_test_profile


@pytest.mark.asyncio
async def test_non_admin_forbidden_from_admin_overview(client: httpx.AsyncClient) -> None:
    """A regular user attempting to query /api/v1/admin/overview receives HTTP 403 Forbidden."""
    user_id = uuid4()
    token = create_test_jwt(user_id=user_id)
    profile = make_test_profile(user_id=user_id, role="user", approval_status="approved")

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id=user_id,
        email="student@example.edu",
        role="authenticated",
        session_id=str(uuid4()),
        claims={},
    )
    app.dependency_overrides[get_current_profile] = lambda: profile

    try:
        resp = await client.get(
            "/api/v1/admin/overview",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "ADMIN_REQUIRED"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_admin_can_access_overview(client: httpx.AsyncClient) -> None:
    """An approved administrator can successfully query /api/v1/admin/overview."""
    admin_id = uuid4()
    token = create_test_jwt(user_id=admin_id)
    profile = make_test_profile(user_id=admin_id, role="admin", approval_status="approved")

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        user_id=admin_id,
        email="admin@example.edu",
        role="authenticated",
        session_id=str(uuid4()),
        claims={},
    )
    app.dependency_overrides[get_current_profile] = lambda: profile

    with patch("app.api.v1.admin.get_admin_overview", new_callable=AsyncMock) as mock_overview:
        mock_overview.return_value = {
            "total_users": 10,
            "pending_requests": 2,
            "approved_users": 8,
            "suspended_users": 0,
            "rejected_users": 0,
            "active_users_now": 3,
            "active_users_recent": 5,
        }

        try:
            resp = await client.get(
                "/api/v1/admin/overview",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["total_users"] == 10
            assert data["pending_requests"] == 2
        finally:
            app.dependency_overrides.clear()
