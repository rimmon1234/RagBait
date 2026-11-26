"""Orchestration service for authorized subject-scoped hybrid retrieval."""

import logging
from uuid import UUID

import asyncpg

from app.core.database import get_pool
from app.modules.knowledge_base.retrieval.authorization import verify_retrieval_authorization
from app.modules.knowledge_base.retrieval.config import retrieval_settings
from app.modules.knowledge_base.retrieval.query_service import prepare_query_vector
from app.modules.knowledge_base.retrieval.retrieval_repository import search_subject_chunks
from app.modules.knowledge_base.retrieval.schemas import RetrievalResult, RetrievedChunk

logger = logging.getLogger("ragbait.kb.retrieval.service")


class RetrievalService:
    """Orchestrates authorized hybrid retrieval across semantic vector and keyword search."""

    @staticmethod
    async def retrieve_subject_chunks(
        user_id: UUID,
        conversation_id: UUID,
        query_text: str,
        limit: int = 5,
        vector_weight: float | None = None,
        fts_weight: float | None = None,
        rrf_k: int | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> RetrievalResult:
        """Execute authorized hybrid retrieval for a conversation's bound academic subject.

        Flow:
        1. Acquire backend connection from pool or reuse provided connection.
        2. Verify user approval status, conversation ownership, active conversation, and subject status.
        3. Derive authoritative subject_id from the database conversation record.
        4. Generate validated query vector (local FastEmbed INT8 ONNX, <= 128 tokens).
        5. Invoke PostgreSQL match_subject_chunks() with pgvector cosine distance, FTS, and RRF.
        6. Return typed RetrievalResult with exact page provenance and scores.

        Guarantees:
        - Never trusts client-supplied subject IDs.
        - Fences archived documents, deleted documents, inactive chunks, stale versions, and uncompleted embeddings.
        - Cross-subject document retrieval is strictly impossible.
        """
        bounded_limit = min(max(1, limit), retrieval_settings.max_match_count)
        eff_vector_weight = (
            vector_weight if vector_weight is not None else retrieval_settings.default_vector_weight
        )
        eff_fts_weight = (
            fts_weight if fts_weight is not None else retrieval_settings.default_fts_weight
        )
        eff_rrf_k = rrf_k if rrf_k is not None else retrieval_settings.default_rrf_k

        async def _execute(db_conn: asyncpg.Connection) -> RetrievalResult:
            # 1. Authorize user and derive bound subject
            subject_id = await verify_retrieval_authorization(
                conn=db_conn,
                user_id=user_id,
                conversation_id=conversation_id,
            )

            # 2. Prepare query embedding vector
            embedding_res = prepare_query_vector(query_text)

            # 3. Execute hybrid retrieval with RRF in PostgreSQL
            chunks = await search_subject_chunks(
                conn=db_conn,
                subject_id=subject_id,
                query_vector=embedding_res.vector,
                query_text=query_text,
                model_fingerprint=embedding_res.model_fingerprint,
                match_count=bounded_limit,
                candidate_limit=retrieval_settings.default_candidate_limit,
                rrf_k=eff_rrf_k,
                vector_weight=eff_vector_weight,
                fts_weight=eff_fts_weight,
            )

            logger.info(
                f"Retrieved {len(chunks)} chunks for conversation {conversation_id} in subject {subject_id}."
            )

            return RetrievalResult(
                conversation_id=conversation_id,
                subject_id=subject_id,
                query_text=query_text,
                model_fingerprint=embedding_res.model_fingerprint,
                chunks=chunks,
                total_retrieved=len(chunks),
            )

        if conn is not None:
            return await _execute(conn)

        pool = get_pool()
        async with pool.acquire() as pool_conn:
            return await _execute(pool_conn)

    @staticmethod
    async def retrieve_subject_chunks_multi(
        user_id: UUID,
        conversation_id: UUID,
        queries: list[str],
        limit: int = 15,
        per_query_limit: int = 10,
        vector_weight: float | None = None,
        fts_weight: float | None = None,
        rrf_k: int | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> RetrievalResult:
        """Execute authorized subject-scoped hybrid retrieval across multiple rewritten/expanded queries.

        Deduplicates chunks by chunk_id, preserves exact provenance and subject fencing,
        and rewards passages retrieved across multiple expanded query variants.
        """
        clean_queries: list[str] = []
        seen_q: set[str] = set()
        for q in queries:
            trimmed = (q or "").strip()
            if trimmed and trimmed.lower() not in seen_q:
                seen_q.add(trimmed.lower())
                clean_queries.append(trimmed)
            if len(clean_queries) >= 4:
                break

        if not clean_queries:
            raise ValueError("At least one non-empty query is required for multi-query retrieval")

        if len(clean_queries) == 1:
            return await RetrievalService.retrieve_subject_chunks(
                user_id=user_id,
                conversation_id=conversation_id,
                query_text=clean_queries[0],
                limit=limit,
                vector_weight=vector_weight,
                fts_weight=fts_weight,
                rrf_k=rrf_k,
                conn=conn,
            )

        bounded_limit = min(max(1, limit), retrieval_settings.max_match_count)
        bounded_per_query = min(max(1, per_query_limit), retrieval_settings.max_match_count)
        eff_vector_weight = (
            vector_weight if vector_weight is not None else retrieval_settings.default_vector_weight
        )
        eff_fts_weight = (
            fts_weight if fts_weight is not None else retrieval_settings.default_fts_weight
        )
        eff_rrf_k = rrf_k if rrf_k is not None else retrieval_settings.default_rrf_k

        async def _execute_multi(db_conn: asyncpg.Connection) -> RetrievalResult:
            subject_id = await verify_retrieval_authorization(
                conn=db_conn,
                user_id=user_id,
                conversation_id=conversation_id,
            )

            merged: dict[UUID, RetrievedChunk] = {}
            hit_counts: dict[UUID, int] = {}
            best_fingerprint = ""

            for idx, q_text in enumerate(clean_queries):
                try:
                    embedding_res = prepare_query_vector(q_text)
                except Exception:
                    if idx == 0:
                        raise
                    continue

                if not best_fingerprint:
                    best_fingerprint = embedding_res.model_fingerprint

                batch = await search_subject_chunks(
                    conn=db_conn,
                    subject_id=subject_id,
                    query_vector=embedding_res.vector,
                    query_text=q_text,
                    model_fingerprint=embedding_res.model_fingerprint,
                    match_count=bounded_per_query,
                    candidate_limit=retrieval_settings.default_candidate_limit,
                    rrf_k=eff_rrf_k,
                    vector_weight=eff_vector_weight,
                    fts_weight=eff_fts_weight,
                )
                for chunk in batch:
                    hit_counts[chunk.chunk_id] = hit_counts.get(chunk.chunk_id, 0) + 1
                    existing = merged.get(chunk.chunk_id)
                    if existing is None or chunk.rrf_score > existing.rrf_score:
                        merged[chunk.chunk_id] = chunk

            scored: list[RetrievedChunk] = []
            for cid, ch in merged.items():
                bonus = 0.004 * max(0, hit_counts[cid] - 1)
                scored.append(ch.model_copy(update={"rrf_score": float(ch.rrf_score) + bonus}))

            scored.sort(key=lambda c: (-c.rrf_score, str(c.chunk_id)))
            ranked_chunks = [
                ch.model_copy(update={"final_rank": rank})
                for rank, ch in enumerate(scored[:bounded_limit], start=1)
            ]

            return RetrievalResult(
                conversation_id=conversation_id,
                subject_id=subject_id,
                query_text=clean_queries[0],
                model_fingerprint=best_fingerprint,
                chunks=ranked_chunks,
                total_retrieved=len(ranked_chunks),
            )

        if conn is not None:
            return await _execute_multi(conn)

        pool = get_pool()
        async with pool.acquire() as pool_conn:
            return await _execute_multi(pool_conn)
