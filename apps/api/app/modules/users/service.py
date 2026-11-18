"""User domain business logic."""

from typing import Any
from uuid import UUID

from app.core.database import get_user_db_session
from app.core.errors import NotFoundError
from app.modules.users.schemas import UserProfile


async def update_user_display_name(
    user_id: UUID, full_claims: dict[str, Any], new_display_name: str
) -> UserProfile:
    """Update mutable display_name for the authenticated user using RLS-aware session."""
    async with get_user_db_session(user_id, full_claims) as conn:
        row = await conn.fetchrow(
            """
            UPDATE public.profiles
            SET display_name = $1
            WHERE id = $2
            RETURNING id, email, display_name, role, approval_status, created_at, updated_at
            """,
            new_display_name,
            user_id,
        )

    if not row:
        raise NotFoundError("Profile not found")

    return UserProfile(
        id=row["id"],
        email=row["email"],
        display_name=row["display_name"],
        role=row["role"],
        approval_status=row["approval_status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
