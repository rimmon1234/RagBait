"""Presence heartbeat and active user monitor service."""

import json
from datetime import datetime
from typing import Any
from uuid import UUID

from app.core.database import get_admin_db_session, get_user_db_session
from app.modules.presence.schemas import PresenceListResponse, PresenceUserResponse


async def record_presence_heartbeat(
    user_id: UUID,
    claims: dict[str, Any],
    session_id: str,
    client_info: dict[str, Any],
) -> tuple[datetime, datetime]:
    """Upsert presence heartbeat for the user using the stable JWT session_id."""
    client_info_json = json.dumps(client_info)

    async with get_user_db_session(user_id, claims) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.user_presence (user_id, session_id, last_seen_at, expires_at, client_info)
            VALUES ($1, $2, now(), now() + interval '120 seconds', $3::jsonb)
            ON CONFLICT (user_id, session_id)
            DO UPDATE SET
                last_seen_at = now(),
                expires_at = now() + interval '120 seconds',
                client_info = EXCLUDED.client_info
            RETURNING last_seen_at, expires_at
            """,
            user_id,
            session_id,
            client_info_json,
        )

    if not row:
        raise RuntimeError("Failed to update presence record")

    return row["last_seen_at"], row["expires_at"]


async def get_presence_list(threshold_seconds: int = 120) -> PresenceListResponse:
    """Retrieve active and recently active users for the administration monitor."""
    async with get_admin_db_session() as conn:
        rows = await conn.fetch(
            f"""
            SELECT DISTINCT ON (up.user_id)
                up.user_id,
                p.email,
                p.display_name,
                p.role,
                up.last_seen_at,
                (up.expires_at > now()) AS is_active,
                up.client_info
            FROM public.user_presence up
            JOIN public.profiles p ON p.id = up.user_id
            WHERE up.last_seen_at > now() - interval '{threshold_seconds} seconds'
            ORDER BY up.user_id, up.last_seen_at DESC
            """
        )

    active_users = [
        PresenceUserResponse(
            user_id=r["user_id"],
            email=r["email"],
            display_name=r["display_name"],
            role=r["role"],
            last_seen_at=r["last_seen_at"],
            is_active=r["is_active"],
            client_info=json.loads(r["client_info"])
            if isinstance(r["client_info"], str)
            else r["client_info"],
        )
        for r in rows
    ]

    return PresenceListResponse(
        active_users=active_users,
        total_active=len(active_users),
    )
