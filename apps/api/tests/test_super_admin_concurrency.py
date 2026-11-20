"""Tests for administrative concurrency, super-admin invariants, and audit logging."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core.errors import AppError, AuthorizationError, ConflictError
from app.modules.admin.service import update_user_role, update_user_status


@pytest.mark.asyncio
async def test_self_role_change_blocked() -> None:
    """Administrators must not be allowed to change their own role."""
    actor_id = uuid4()
    with pytest.raises(AppError) as exc_info:
        await update_user_role(actor_id=actor_id, target_user_id=actor_id, new_role="super_admin")
    assert exc_info.value.code == "SELF_PROMOTION_FORBIDDEN"


@pytest.mark.asyncio
async def test_standard_admin_cannot_alter_super_admin() -> None:
    """Standard admin cannot modify a super-admin's role."""
    actor_id = uuid4()
    target_id = uuid4()

    mock_conn = AsyncMock()
    # Actor profile query returns standard admin
    mock_conn.fetchrow.side_effect = [
        {"role": "admin", "approval_status": "approved"},  # Actor
        {"role": "super_admin", "approval_status": "approved", "email": "sa@example.edu"},  # Target
    ]

    with (
        patch("app.modules.admin.service.get_admin_db_session") as mock_db,
        patch("app.modules.admin.service.acquire_super_admin_advisory_lock") as mock_lock,
    ):
        mock_db.return_value.__aenter__.return_value = mock_conn

        with pytest.raises(AuthorizationError) as exc_info:
            await update_user_role(actor_id=actor_id, target_user_id=target_id, new_role="user")
        assert "Standard administrators cannot alter super-administrators" in str(exc_info.value)
        mock_lock.assert_called_once_with(mock_conn)


@pytest.mark.asyncio
async def test_last_super_admin_cannot_be_demoted() -> None:
    """Demoting the last remaining super-admin must fail with ConflictError."""
    actor_id = uuid4()
    target_id = uuid4()

    mock_conn = AsyncMock()
    mock_conn.fetchrow.side_effect = [
        {"role": "super_admin", "approval_status": "approved"},  # Actor
        {
            "id": target_id,
            "email": "sa@example.edu",
            "display_name": "SA",
            "role": "super_admin",
            "approval_status": "approved",
            "created_at": None,
            "updated_at": None,
        },  # Target
    ]
    # Count of super_admins returns 1
    mock_conn.fetchval.return_value = 1

    with (
        patch("app.modules.admin.service.get_admin_db_session") as mock_db,
        patch("app.modules.admin.service.acquire_super_admin_advisory_lock"),
    ):
        mock_db.return_value.__aenter__.return_value = mock_conn

        with pytest.raises(ConflictError) as exc_info:
            await update_user_role(actor_id=actor_id, target_user_id=target_id, new_role="admin")
        assert "Cannot demote the last remaining super-administrator" in str(exc_info.value)


@pytest.mark.asyncio
async def test_self_suspension_blocked() -> None:
    """Administrators must not be allowed to suspend their own account."""
    actor_id = uuid4()
    with pytest.raises(AppError) as exc_info:
        await update_user_status(actor_id=actor_id, target_user_id=actor_id, new_status="suspended")
    assert exc_info.value.code == "SELF_SUSPENSION_FORBIDDEN"
