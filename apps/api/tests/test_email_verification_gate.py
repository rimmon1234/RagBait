"""Tests for authoritative email verification gate and idempotent access request creation."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.modules.access.service import resolve_effective_access_state
from tests.conftest import make_test_profile


@pytest.mark.asyncio
async def test_unverified_email_blocked_from_requesting_access() -> None:
    """Unverified users must receive effective_state='unverified' without access request creation."""
    user_id = uuid4()
    profile = make_test_profile(user_id=user_id, approval_status="pending")

    with patch(
        "app.modules.access.service.fetch_authoritative_auth_user",
        new_callable=AsyncMock,
        return_value=(False, "user@example.edu"),
    ):
        result = await resolve_effective_access_state(
            token="dummy_token",
            user_id=user_id,
            claims={},
            profile=profile,
        )

    assert result.effective_state == "unverified"
    assert result.email_verified is False
    assert result.has_pending_request is False


@pytest.mark.asyncio
async def test_verified_email_idempotently_creates_access_request() -> None:
    """Verified email users transition to 'pending' with idempotent access request creation."""
    user_id = uuid4()
    profile = make_test_profile(user_id=user_id, approval_status="pending")

    mock_conn = AsyncMock()
    # First query checks existing request (returns None)
    mock_conn.fetchrow.return_value = None

    with (
        patch(
            "app.modules.access.service.fetch_authoritative_auth_user",
            new_callable=AsyncMock,
            return_value=(True, "user@example.edu"),
        ),
        patch("app.modules.access.service.get_user_db_session") as mock_session_ctx,
    ):
        mock_session_ctx.return_value.__aenter__.return_value = mock_conn

        result = await resolve_effective_access_state(
            token="dummy_token",
            user_id=user_id,
            claims={},
            profile=profile,
        )

        assert result.effective_state == "pending"
        assert result.email_verified is True
        assert result.has_pending_request is True

        # Assert idempotent insert was executed
        mock_conn.execute.assert_called_once()
        query_str = mock_conn.execute.call_args[0][0]
        assert "INSERT INTO public.access_requests" in query_str
        assert "ON CONFLICT (user_id) WHERE status = 'pending' DO NOTHING" in query_str


@pytest.mark.asyncio
async def test_approved_regular_user_resolves_contribution_required() -> None:
    """Approved regular users must receive 'contribution_required' state."""
    user_id = uuid4()
    profile = make_test_profile(user_id=user_id, role="user", approval_status="approved")

    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {"id": uuid4(), "status": "approved"}

    with (
        patch(
            "app.modules.access.service.fetch_authoritative_auth_user",
            new_callable=AsyncMock,
            return_value=(True, "user@example.edu"),
        ),
        patch("app.modules.access.service.get_user_db_session") as mock_session_ctx,
    ):
        mock_session_ctx.return_value.__aenter__.return_value = mock_conn

        result = await resolve_effective_access_state(
            token="dummy_token",
            user_id=user_id,
            claims={},
            profile=profile,
        )

        assert result.effective_state == "contribution_required"
        assert result.email_verified is True


@pytest.mark.asyncio
async def test_approved_admin_resolves_admin_state() -> None:
    """Approved admin users must receive 'admin' state."""
    user_id = uuid4()
    profile = make_test_profile(user_id=user_id, role="admin", approval_status="approved")

    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {"id": uuid4(), "status": "approved"}

    with (
        patch(
            "app.modules.access.service.fetch_authoritative_auth_user",
            new_callable=AsyncMock,
            return_value=(True, "admin@example.edu"),
        ),
        patch("app.modules.access.service.get_user_db_session") as mock_session_ctx,
    ):
        mock_session_ctx.return_value.__aenter__.return_value = mock_conn

        result = await resolve_effective_access_state(
            token="dummy_token",
            user_id=user_id,
            claims={},
            profile=profile,
        )

        assert result.effective_state == "admin"
