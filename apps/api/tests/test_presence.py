"""Tests for presence heartbeat tracking and session stability."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.modules.presence.service import record_presence_heartbeat


@pytest.mark.asyncio
async def test_presence_heartbeat_uses_session_id() -> None:
    """Verify that presence heartbeat upserts using user_id and stable session_id."""
    user_id = uuid4()
    session_id = str(uuid4())
    client_info = {"browser": "Chrome"}
    now = datetime.now(UTC)

    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {
        "last_seen_at": now,
        "expires_at": now,
    }

    with patch("app.modules.presence.service.get_user_db_session") as mock_session_ctx:
        mock_session_ctx.return_value.__aenter__.return_value = mock_conn

        last_seen, expires_at = await record_presence_heartbeat(
            user_id=user_id,
            claims={},
            session_id=session_id,
            client_info=client_info,
        )

        assert last_seen == now
        assert expires_at == now

        # Verify SQL query used session_id
        mock_conn.fetchrow.assert_called_once()
        query = mock_conn.fetchrow.call_args[0][0]
        assert "ON CONFLICT (user_id, session_id)" in query
        assert mock_conn.fetchrow.call_args[0][1] == user_id
        assert mock_conn.fetchrow.call_args[0][2] == session_id
