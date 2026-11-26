"""Idempotent CLI command to backfill RAG indexing jobs for completed extracted documents."""

import argparse
import asyncio
import logging
from typing import Any
from uuid import UUID

import asyncpg

from app.core.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("ragbait.cli.backfill_indexing")


async def run_backfill(
    dsn: str,
    subject_id: UUID | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Find unindexed completed documents and enqueue indexing jobs idempotently.

    Returns:
        (discovered_count, enqueued_count)
    """
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=3)
    try:
        async with pool.acquire() as conn:
            query = """
                SELECT d.id, d.title, d.extraction_version, d.indexing_status, s.name as subject_name
                FROM public.kb_documents d
                JOIN public.kb_topics t ON t.id = d.topic_id
                JOIN public.kb_subjects s ON s.id = t.subject_id
                WHERE d.upload_status = 'uploaded'
                  AND d.extraction_status = 'completed'
                  AND d.is_archived = false
                  AND t.is_active = true
                  AND s.is_active = true
                  AND (
                      d.indexing_status IN ('unindexed', 'failed')
                      OR NOT EXISTS (
                          SELECT 1 FROM public.kb_document_chunks c
                          WHERE c.document_id = d.id AND c.is_active = true
                      )
                  )
            """
            params: list[Any] = []
            if subject_id:
                query += " AND s.id = $1"
                params.append(subject_id)

            query += " ORDER BY d.created_at ASC"
            if limit:
                query += f" LIMIT ${len(params) + 1}"
                params.append(limit)

            rows = await conn.fetch(query, *params)
            discovered_count = len(rows)

            logger.info(
                f"Discovered {discovered_count} documents eligible for RAG indexing backfill."
            )
            if dry_run or discovered_count == 0:
                for r in rows:
                    logger.info(
                        f"  [DRY RUN] Document '{r['title']}' ({r['id']}) in subject '{r['subject_name']}'"
                    )
                return discovered_count, 0

            enqueued_count = 0
            for r in rows:
                doc_id = r["id"]
                ext_ver = r["extraction_version"] or 1
                res = await conn.fetchrow(
                    """
                    INSERT INTO public.kb_indexing_jobs (
                        document_id,
                        source_version,
                        chunking_version,
                        status,
                        scheduled_at
                    ) VALUES ($1, $2, 1, 'queued', timezone('utc'::text, now()))
                    ON CONFLICT (document_id) WHERE status IN ('queued', 'processing')
                    DO NOTHING
                    RETURNING id;
                    """,
                    doc_id,
                    ext_ver,
                )
                if res:
                    enqueued_count += 1
                    logger.info(f"Enqueued indexing job {res['id']} for '{r['title']}' ({doc_id})")
                else:
                    logger.info(
                        f"Document '{r['title']}' ({doc_id}) already has an active indexing job."
                    )

            logger.info(
                f"Backfill complete: enqueued {enqueued_count} jobs out of {discovered_count} discovered."
            )
            return discovered_count, enqueued_count

    finally:
        await pool.close()


def main() -> None:
    """CLI entry point for backfill indexing."""
    parser = argparse.ArgumentParser(
        description="Backfill RAG indexing jobs for completed KB documents."
    )
    parser.add_argument(
        "--subject-id", type=UUID, default=None, help="Filter by specific subject UUID"
    )
    parser.add_argument("--limit", type=int, default=None, help="Max documents to enqueue")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print candidate documents without enqueuing"
    )
    args = parser.parse_args()

    discovered, enqueued = asyncio.run(
        run_backfill(
            dsn=settings.DATABASE_URL,
            subject_id=args.subject_id,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    )
    logger.info(f"Result: {discovered} discovered, {enqueued} enqueued.")


if __name__ == "__main__":
    main()
