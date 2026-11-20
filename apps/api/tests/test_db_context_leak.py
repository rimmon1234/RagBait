"""Tests verifying that transaction-local RLS claims do not leak between pooled connections."""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.database import get_user_db_session


@pytest.mark.asyncio
async def test_user_session_claims_configuration() -> None:
    """Verify that get_user_db_session issues parameterized set_config calls inside a transaction."""
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_conn.execute = AsyncMock()

    mock_tx = MagicMock()
    mock_tx.__aenter__ = AsyncMock(return_value=None)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction = MagicMock(return_value=mock_tx)

    mock_acquire = MagicMock()
    mock_acquire.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_acquire.__aexit__ = AsyncMock(return_value=None)
    mock_pool.acquire = MagicMock(return_value=mock_acquire)

    user_id = uuid4()
    claims = {"sub": str(user_id), "role": "authenticated", "custom": "data"}

    with patch("app.core.database.get_pool", return_value=mock_pool):
        async with get_user_db_session(user_id, claims) as conn:
            assert conn is mock_conn

    # Verify that SET LOCAL ROLE authenticated was issued
    mock_conn.execute.assert_any_call("SET LOCAL ROLE authenticated;")

    # Verify parameterized set_config calls (never string-interpolated)
    mock_conn.execute.assert_any_call(
        "SELECT set_config('request.jwt.claims', $1, true);", json.dumps(claims)
    )
    mock_conn.execute.assert_any_call(
        "SELECT set_config('request.jwt.claim.sub', $1, true);", str(user_id)
    )
    mock_conn.execute.assert_any_call(
        "SELECT set_config('request.jwt.claim.role', $1, true);", "authenticated"
    )

    # Verify transaction context was entered and exited
    mock_conn.transaction.assert_called_once()
    mock_tx.__aenter__.assert_called_once()
    mock_tx.__aexit__.assert_called_once()
