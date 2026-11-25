"""High-level Context Manager orchestrator producing PreparedContext."""

import logging
from typing import Any
from uuid import UUID

import asyncpg

from app.core.database import get_pool
from app.modules.chat.context.budget import allocate_context_budget
from app.modules.chat.context.config import context_settings
from app.modules.chat.context.history import load_conversation_history_context
from app.modules.chat.context.model_capabilities import (
    get_model_capability,
    resolve_cloud_model_capability,
)
from app.modules.chat.context.prompt_builder import (
    build_user_turn_prompt,
    render_single_prompt,
)
from app.modules.chat.context.schemas import PreparedContext
from app.modules.chat.context.token_counter import build_token_counter
from app.modules.knowledge_base.retrieval import RetrievalResult, RetrievalService

logger = logging.getLogger("ragbait.chat.context.service")


class ContextService:
    """Orchestrates authorization, M4.4 retrieval, history, budgeting, and prompt assembly."""

    @staticmethod
    async def prepare_grounded_context(
        *,
        user_id: UUID,
        conversation_id: UUID,
        user_query: str,
        inference_mode: str = "cloud",
        requested_model_id: str | None = None,
        requested_provider_id: str | None = None,
        routing_mode: str = "auto",
        explicit_credential_id: UUID | None = None,
        requested_max_output: int | None = None,
        retrieval_limit: int | None = None,
        exclude_message_id: UUID | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> PreparedContext:
        """Prepare a complete, budgeted, injection-safe RAG context contract.

        Flow:
        1. Resolve trusted server-authoritative ModelCapability (cloud or local).
        2. Build model-aware token counter (distinct from M4.3 E5 tokenizer).
        3. Execute M4.4 authorized hybrid retrieval (authoritative subject binding).
        4. Load committed conversation summary and recent completed messages.
        5. Run deterministic context budget allocation and greedy evidence selection.
        6. Construct grounded XML-delimited prompt and structured LLM messages.
        7. Log non-sensitive telemetry and return PreparedContext.
        """
        eff_retrieval_limit = retrieval_limit or context_settings.RETRIEVAL_CANDIDATE_POOL_LIMIT

        async def _execute(db_conn: asyncpg.Connection) -> PreparedContext:
            # 1. Resolve Model Capability upfront from authoritative server registry
            if inference_mode == "local":
                target_model = requested_model_id or "llama3.2:3b"
                capability = await get_model_capability(
                    target_model, inference_mode="local", conn=db_conn
                )
            else:
                capability = await resolve_cloud_model_capability(
                    user_id=user_id,
                    requested_model_id=requested_model_id,
                    requested_provider_id=requested_provider_id,
                    routing_mode=routing_mode,
                    explicit_credential_id=explicit_credential_id,
                    conn=db_conn,
                )

            token_counter = build_token_counter(capability)

            # 2. Execute authorized M4.4 hybrid retrieval (never trusts client subject_id)
            retrieval_res: RetrievalResult = await RetrievalService.retrieve_subject_chunks(
                user_id=user_id,
                conversation_id=conversation_id,
                query_text=user_query,
                limit=eff_retrieval_limit,
                conn=db_conn,
            )

            # 3. Load conversation summary & uncompacted recent history
            history_ctx = await load_conversation_history_context(
                conn=db_conn,
                conversation_id=conversation_id,
                token_counter=token_counter,
                exclude_message_id=exclude_message_id,
            )

            # 4. Run deterministic context allocation & greedy evidence selection
            alloc = allocate_context_budget(
                capability=capability,
                token_counter=token_counter,
                user_query=user_query,
                candidates=retrieval_res.chunks,
                history=history_ctx,
                requested_max_output=requested_max_output,
            )

            user_turn_prompt = build_user_turn_prompt(
                evidence_xml_block=alloc.evidence_xml_block,
                user_query=user_query,
                summary_text=alloc.summary_text,
            )
            rendered_single = render_single_prompt(
                system_prompt=alloc.system_prompt,
                recent_history=alloc.recent_history,
                user_turn_prompt=user_turn_prompt,
            )

            # Non-sensitive M4.5 telemetry (never logs document/user contents or secrets)
            telemetry: dict[str, Any] = {
                "conversation_id": str(conversation_id),
                "subject_id": str(retrieval_res.subject_id),
                "inference_mode": inference_mode,
                "model_id": capability.model_id,
                "retrieval_candidate_count": retrieval_res.total_retrieved,
                "selected_evidence_count": len(alloc.selected_evidence),
                "evidence_omitted_count": len(alloc.omitted_candidates),
                "evidence_token_count": alloc.accounting.evidence_token_count,
                "summary_token_count": alloc.accounting.summary_token_count,
                "recent_history_token_count": alloc.accounting.recent_history_token_count,
                "current_question_token_count": alloc.accounting.current_question_tokens,
                "reserved_output_tokens": alloc.accounting.reserved_output_tokens,
                "total_input_token_count": alloc.accounting.total_input_token_count,
                "model_effective_ceiling": alloc.accounting.model_effective_ceiling,
                "context_utilization_ratio": alloc.accounting.utilization_ratio,
                "compaction_status": history_ctx.compaction_status,
            }
            logger.info("M4.5 context prepared", extra={"context_telemetry": telemetry})

            return PreparedContext(
                conversation_id=conversation_id,
                subject_id=retrieval_res.subject_id,
                user_id=user_id,
                model_capability=capability,
                current_user_message=user_query.strip(),
                system_prompt=alloc.system_prompt,
                evidence_xml_block=alloc.evidence_xml_block,
                user_turn_prompt=user_turn_prompt,
                rendered_single_prompt=rendered_single,
                llm_messages=alloc.llm_messages,
                conversation_summary=alloc.summary_text,
                compacted_through_sequence=history_ctx.compacted_through_sequence,
                recent_history=alloc.recent_history,
                selected_evidence=alloc.selected_evidence,
                omitted_evidence_count=len(alloc.omitted_candidates),
                omitted_candidates=alloc.omitted_candidates,
                retrieval_candidate_count=retrieval_res.total_retrieved,
                accounting=alloc.accounting,
                model_fingerprint=retrieval_res.model_fingerprint,
            )

        if conn is not None:
            return await _execute(conn)

        pool = get_pool()
        async with pool.acquire() as pool_conn:
            return await _execute(pool_conn)
