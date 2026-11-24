"""Database repository operations for invoking match_subject_chunks."""

import json
import logging
from uuid import UUID

import asyncpg

from app.modules.knowledge_base.retrieval.schemas import RetrievedChunk

logger = logging.getLogger("ragbait.kb.retrieval.repo")


async def search_subject_chunks(
    conn: asyncpg.Connection,
    subject_id: UUID,
    query_vector: list[float],
    query_text: str,
    model_fingerprint: str,
    match_count: int = 5,
    candidate_limit: int = 30,
    rrf_k: int = 60,
    vector_weight: float = 0.60,
    fts_weight: float = 0.40,
) -> list[RetrievedChunk]:
    """Execute the database-side hybrid search function and map returned records.

    Calls public.match_subject_chunks() with strict parameters and type conversion.
    """
    # Format vector as standard pgvector text representation: "[0.123,0.456,...]"
    vec_str = f"[{','.join(f'{x:.8f}' for x in query_vector)}]"

    query = """
        SELECT
            chunk_id,
            document_id,
            subject_id,
            topic_id,
            document_title,
            raw_text,
            page_number_start,
            page_number_end,
            page_spans,
            content_hash,
            chunk_version,
            model_fingerprint,
            vector_rank,
            fts_rank,
            vector_distance,
            fts_score,
            rrf_score,
            final_rank
        FROM public.match_subject_chunks(
            $1::uuid,
            $2::text::extensions.vector,
            $3::text,
            $4::text,
            $5::integer,
            $6::integer,
            $7::integer,
            $8::double precision,
            $9::double precision
        );
    """

    rows = await conn.fetch(
        query,
        subject_id,
        vec_str,
        query_text,
        model_fingerprint,
        match_count,
        candidate_limit,
        rrf_k,
        vector_weight,
        fts_weight,
    )

    chunks: list[RetrievedChunk] = []
    for r in rows:
        spans_raw = r["page_spans"]
        if isinstance(spans_raw, str):
            try:
                page_spans = json.loads(spans_raw)
            except Exception:
                page_spans = []
        elif isinstance(spans_raw, list):
            page_spans = spans_raw
        else:
            page_spans = []

        chunk = RetrievedChunk(
            chunk_id=r["chunk_id"],
            document_id=r["document_id"],
            subject_id=r["subject_id"],
            topic_id=r["topic_id"],
            document_title=r["document_title"],
            raw_text=r["raw_text"],
            page_number_start=r["page_number_start"],
            page_number_end=r["page_number_end"],
            page_spans=page_spans,
            content_hash=r["content_hash"],
            chunk_version=r["chunk_version"],
            model_fingerprint=r["model_fingerprint"],
            vector_rank=r["vector_rank"],
            fts_rank=r["fts_rank"],
            vector_distance=r["vector_distance"],
            fts_score=r["fts_score"],
            rrf_score=r["rrf_score"],
            final_rank=r["final_rank"],
        )
        chunks.append(chunk)

    return chunks
