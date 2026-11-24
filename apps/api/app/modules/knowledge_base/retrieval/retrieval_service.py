"""Orchestration service for authorized subject-scoped hybrid retrieval."""

import logging
from uuid import UUID

import asyncpg

from app.core.database import get_pool
from app.modules.knowledge_base.retrieval.authorization import verify_retrieval_authorization
from app.modules.knowledge_base.retrieval.config import retrieval_settings
from app.modules.knowledge_base.retrieval.query_service import prepare_query_vector
from app.modules.knowledge_base.retrieval.retrieval_repository import search_subject_chunks
from app.modules.knowledge_base.retrieval.schemas import RetrievalResult

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
