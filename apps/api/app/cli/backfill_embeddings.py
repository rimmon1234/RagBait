"""Idempotent CLI command to backfill RAG embedding jobs for active indexed documents."""

import argparse
import asyncio
import logging
from typing import Any
from uuid import UUID

import asyncpg

from app.core.config import settings
from app.modules.knowledge_base.embeddings.model_manifest import get_model_manifest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("ragbait.cli.backfill_embeddings")


async def run_embedding_backfill(
    dsn: str,
    subject_id: UUID | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Find indexed documents missing embeddings and enqueue embedding jobs idempotently.

    Returns:
        (discovered_count, enqueued_count)
    """
    manifest = get_model_manifest()
    fingerprint = manifest.model_fingerprint

    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=3)
    try:
        async with pool.acquire() as conn:
            query = """
                SELECT d.id, d.title, d.active_chunk_version, d.embedding_status, s.name as subject_name
                FROM public.kb_documents d
                JOIN public.kb_topics t ON t.id = d.topic_id
                JOIN public.kb_subjects s ON s.id = t.subject_id
                WHERE d.upload_status = 'uploaded'
                  AND d.indexing_status = 'indexed'
                  AND d.is_archived = false
                  AND t.is_active = true
                  AND s.is_active = true
                  AND d.active_chunk_version IS NOT NULL
                  AND (
                      d.embedding_status IN ('unembedded', 'failed')
                      OR d.active_embedding_fingerprint IS DISTINCT FROM $1
                      OR NOT EXISTS (
                          SELECT 1 FROM public.kb_chunk_embeddings e
                          JOIN public.kb_document_chunks c ON c.id = e.chunk_id
                          WHERE c.document_id = d.id
                            AND c.chunking_version = d.active_chunk_version
                            AND c.is_active = true
                            AND e.model_fingerprint = $1
                      )
                  )
            """
            params: list[Any] = [fingerprint]
            if subject_id:
                query += f" AND s.id = ${len(params) + 1}"
                params.append(subject_id)

            query += " ORDER BY d.created_at ASC"
            if limit:
                query += f" LIMIT ${len(params) + 1}"
                params.append(limit)

            rows = await conn.fetch(query, *params)
            discovered_count = len(rows)

            logger.info(
                f"Discovered {discovered_count} documents eligible for RAG embedding backfill "
                f"(fingerprint: {fingerprint})."
            )
            if dry_run or discovered_count == 0:
                for r in rows:
                    logger.info(
                        f"  [DRY RUN] Document '{r['title']}' ({r['id']}) in subject '{r['subject_name']}' [v={r['active_chunk_version']}]"
                    )
                return discovered_count, 0

            enqueued_count = 0
            for r in rows:
                doc_id = r["id"]
                chunk_ver = r["active_chunk_version"]
                res = await conn.fetchrow(
                    """
                    INSERT INTO public.kb_embedding_jobs (
                        document_id,
                        chunk_version,
                        model_fingerprint,
                        status,
                        scheduled_at
                    ) VALUES ($1, $2, $3, 'queued', timezone('utc'::text, now()))
                    ON CONFLICT (document_id, model_fingerprint) WHERE status IN ('queued', 'processing')
                    DO NOTHING
                    RETURNING id;
                    """,
                    doc_id,
                    chunk_ver,
                    fingerprint,
                )
                if res:
                    enqueued_count += 1
                    logger.info(f"Enqueued embedding job {res['id']} for '{r['title']}' ({doc_id})")
                else:
                    logger.info(
                        f"Document '{r['title']}' ({doc_id}) already has an active embedding job."
                    )

            logger.info(
                f"Backfill complete: enqueued {enqueued_count} jobs out of {discovered_count} discovered."
            )
            return discovered_count, enqueued_count

    finally:
        await pool.close()


def main() -> None:
    """CLI entry point for backfill embeddings."""
    parser = argparse.ArgumentParser(
        description="Backfill RAG embedding jobs for active indexed KB documents."
    )
    parser.add_argument(
        "--subject-id", type=UUID, default=None, help="Filter by specific subject UUID"
    )
    parser.add_argument("--limit", type=int, default=None, help="Max documents to enqueue")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print candidate documents without enqueuing",
    )
    args = parser.parse_args()

    asyncio.run(
        run_embedding_backfill(
            dsn=settings.DATABASE_URL,
            subject_id=args.subject_id,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    )


if __name__ == "__main__":
    main()
