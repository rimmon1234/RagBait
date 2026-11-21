"""Abandoned upload reservation and deletion recovery sweeper utility."""

import logging

from app.core.database import get_admin_db_session
from app.modules.knowledge_base import repository, storage

logger = logging.getLogger("ragbait.kb.cleanup")


async def sweep_abandoned_uploads(batch_size: int = 50) -> int:
    """Sweep expired pending_upload records and recover stranded deleting documents."""
    total_swept = 0

    # 1. Sweep expired pending reservations
    async with get_admin_db_session() as conn:
        swept = await repository.sweep_abandoned_pending_uploads(conn, batch_size=batch_size)

    if swept:
        logger.info(f"Swept {len(swept)} abandoned upload reservations.")
        for item in swept:
            try:
                await storage.delete_storage_object(item["storage_path"])
            except Exception as e:
                logger.warning(
                    f"Failed to delete storage object for expired reservation {item['id']}: {e}"
                )
        total_swept += len(swept)

    # 2. Recover any stranded documents in 'deleting' state > 10 minutes (failure recovery)
    async with get_admin_db_session() as conn:
        stranded = await repository.sweep_stale_deleting_documents(
            conn, stale_threshold_seconds=600, batch_size=batch_size
        )

    if stranded:
        logger.info(f"Recovering {len(stranded)} stranded deleting documents.")
        for item in stranded:
            try:
                await storage.delete_storage_object(item["storage_path"])
            except Exception as e:
                logger.warning(f"Error purging storage for stranded deleting doc {item['id']}: {e}")
            async with get_admin_db_session() as conn:
                await repository.finalize_document_deletion(conn, item["id"])
        total_swept += len(stranded)

    return total_swept
