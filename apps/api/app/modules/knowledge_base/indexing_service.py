"""Service layer orchestrating page retrieval, deterministic chunking, and persistence."""

import logging
from typing import Any
from uuid import UUID

import asyncpg

from app.modules.knowledge_base.chunking import PageInput, chunk_document_pages
from app.modules.knowledge_base.indexing_repository import (
    complete_indexing_job_transaction,
    fail_indexing_job,
    fetch_document_pages_for_indexing,
    renew_indexing_lease,
)

logger = logging.getLogger("ragbait.kb.indexing_service")


async def process_single_indexing_job(
    conn: asyncpg.Connection,
    job: dict[str, Any],
    worker_id: str,
) -> bool:
    """Process a single claimed indexing job to completion.

    Fetches extracted pages, chunks them deterministically with physical page provenance,
    verifies <= 420 token ceiling, and atomically saves the active chunks.
    """
    job_id: UUID = job["job_id"]
    document_id: UUID = job["document_id"]
    lease_token: UUID = job["lease_token"]
    source_version: int = job.get("source_version") or 1
    chunking_version: int = job.get("chunking_version") or 1
    doc_title: str = job.get("title") or "Unknown"

    logger.info(
        f"Processing indexing job {job_id} for doc '{doc_title}' ({document_id}) [worker={worker_id}, attempt={job.get('attempts')}]"
    )

    try:
        # 1. Fetch extracted pages
        pages = await fetch_document_pages_for_indexing(conn, document_id)
        if not pages:
            logger.warning(f"No extracted pages found for document {document_id}. Marking not_indexable.")
            return await complete_indexing_job_transaction(
                conn=conn,
                job_id=job_id,
                document_id=document_id,
                worker_id=worker_id,
                lease_token=lease_token,
                chunks=[],
                chunking_version=chunking_version,
                source_version=source_version,
            )

        page_inputs = [
            PageInput(
                page_number=p["page_number"],
                text=p["raw_text"],
                has_text=p["has_text"],
            )
            for p in pages
        ]

        # 2. Heartbeat lease before chunking CPU work
        await renew_indexing_lease(conn, job_id, worker_id, lease_token)

        # 3. Deterministically chunk pages
        chunks = chunk_document_pages(page_inputs)
        logger.info(
            f"Generated {len(chunks)} chunks for document {document_id} across {len(pages)} pages."
        )

        # 4. Atomic transaction completion & activation
        success = await complete_indexing_job_transaction(
            conn=conn,
            job_id=job_id,
            document_id=document_id,
            worker_id=worker_id,
            lease_token=lease_token,
            chunks=chunks,
            chunking_version=chunking_version,
            source_version=source_version,
        )

        if success:
            logger.info(f"Successfully completed indexing job {job_id} for document {document_id}.")
        else:
            logger.warning(f"Failed to complete indexing job {job_id} (lease expired or document modified).")

        return success

    except Exception as exc:
        logger.error(f"Error processing indexing job {job_id} for document {document_id}: {exc}", exc_info=True)
        try:
            await fail_indexing_job(
                conn=conn,
                job_id=job_id,
                document_id=document_id,
                worker_id=worker_id,
                lease_token=lease_token,
                error_code="INDEXING_FAILED",
                error_message=str(exc),
                is_transient=True,
            )
        except Exception as fail_err:
            logger.error(f"Failed to record failure for job {job_id}: {fail_err}")

        return False
