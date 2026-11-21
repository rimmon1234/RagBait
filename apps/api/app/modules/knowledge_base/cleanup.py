"""Abandoned upload reservation sweeper utility."""

import logging

from app.core.database import get_admin_db_session
from app.modules.knowledge_base import repository, storage

logger = logging.getLogger("ragbait.kb.cleanup")


async def sweep_abandoned_uploads(batch_size: int = 50) -> int:
    """Sweep expired pending_upload records and remove any orphaned storage objects."""
    async with get_admin_db_session() as conn:
        swept = await repository.sweep_abandoned_pending_uploads(conn, batch_size=batch_size)

    if not swept:
        return 0

    logger.info(f"Swept {len(swept)} abandoned upload reservations.")

    for item in swept:
        try:
            await storage.delete_storage_object(item["storage_path"])
        except Exception as e:
            logger.warning(
                f"Failed to delete storage object for expired reservation {item['id']}: {e}"
            )

    return len(swept)
