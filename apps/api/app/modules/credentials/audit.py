"""Safe Audit Logging Helper for Credential Operations."""

import json
import logging
from typing import Any
from uuid import UUID

from app.core.database import get_admin_db_session

logger = logging.getLogger("ragbait.credentials.audit")


async def log_credential_audit(
    actor_id: UUID,
    action: str,
    credential_id: UUID,
    provider_id: str,
    outcome: str = "success",
    details: dict[str, Any] | None = None,
) -> None:
    """Record an immutable audit log entry for credential lifecycle actions.

    CRITICAL SECURITY INVARIANT:
    Never serializes raw keys, authorization headers, fingerprints, or secret material.
    """
    safe_payload = {
        "provider_id": provider_id,
        "outcome": outcome,
        **(details or {}),
    }

    try:
        async with get_admin_db_session() as conn:
            await conn.execute(
                """
                INSERT INTO public.audit_logs (actor_id, action, target_type, target_id, payload)
                VALUES ($1, $2, 'api_credential', $3, $4::jsonb)
                """,
                actor_id,
                action,
                credential_id,
                json.dumps(safe_payload),
            )
    except Exception as exc:
        # Non-blocking logger warning so audit logging failures don't crash primary flows,
        # but are still recorded in operational logs.
        logger.error(
            f"Failed to record audit log for credential action '{action}': {exc}",
            exc_info=True,
        )
