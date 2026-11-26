"""High-level Context Manager orchestrator producing PreparedContext."""

import logging
from typing import Any
from uuid import UUID

import asyncpg

from app.core.database import get_pool
from app.modules.chat.context.answer_planner import build_answer_plan
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
from app.modules.chat.context.schemas import (
    AdvancedRagTrace,
    AnswerPolicy,
    PreparedContext,
)
from app.modules.chat.context.token_counter import build_token_counter
from app.modules.chat.web_search import BaseWebSearchAdapter, RawWebResult, WebSearchService
from app.modules.knowledge_base.retrieval import RetrievalResult, RetrievalService
from app.modules.knowledge_base.retrieval.advanced_rag import (
    LLMCaller,
    plan_and_expand_queries,
    rerank_and_subselect_chunks,
)

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
        answer_policy: AnswerPolicy = "note_first_adaptive",
        web_search_adapter: BaseWebSearchAdapter | None = None,
        advanced_rag_llm_caller: LLMCaller | None = None,
        conn: asyncpg.Connection | None = None,
    ) -> PreparedContext:
        """Prepare a complete, adaptive M4.9 note-first RAG + optional web context contract."""
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

            # 2. Load conversation summary, recent history, and authoritative subject title
            history_ctx = await load_conversation_history_context(
                conn=db_conn,
                conversation_id=conversation_id,
                token_counter=token_counter,
                exclude_message_id=exclude_message_id,
            )
            subject_name = await db_conn.fetchval(
                """
                SELECT s.name
                FROM public.chat_conversations c
                JOIN public.kb_subjects s ON s.id = c.subject_id
                WHERE c.id = $1
                """,
                conversation_id,
            )

            # 3. Advanced RAG Stage 1: Query rewriting, multi-query expansion & intent classification
            q_plan = await plan_and_expand_queries(
                user_id=user_id,
                user_query=user_query,
                subject_name=str(subject_name) if subject_name else None,
                recent_history=history_ctx.recent_messages,
                inference_mode=inference_mode,
                routing_mode=routing_mode,
                requested_provider_id=requested_provider_id,
                requested_model_id=requested_model_id,
                explicit_credential_id=explicit_credential_id,
                llm_caller=advanced_rag_llm_caller,
            )

            search_queries: list[str] = []
            for q_cand in [q_plan.rewritten_query, *q_plan.expanded_queries, user_query.strip()]:
                if q_cand and q_cand not in search_queries:
                    search_queries.append(q_cand)

            # 4. Authorized M4.4 multi-query hybrid subject retrieval (notes prioritized first)
            retrieval_res: RetrievalResult = await RetrievalService.retrieve_subject_chunks_multi(
                user_id=user_id,
                conversation_id=conversation_id,
                queries=search_queries,
                limit=eff_retrieval_limit,
                conn=db_conn,
            )

            # 5. Advanced RAG Stage 2: LLM passage re-ranking & subselection
            rerank_out = await rerank_and_subselect_chunks(
                user_id=user_id,
                user_query=user_query,
                rewritten_query=q_plan.rewritten_query,
                candidates=retrieval_res.chunks,
                inference_mode=inference_mode,
                routing_mode=routing_mode,
                requested_provider_id=requested_provider_id,
                requested_model_id=requested_model_id,
                explicit_credential_id=explicit_credential_id,
                llm_caller=advanced_rag_llm_caller,
            )

            # 6. Server-side Answer Planning & Sufficiency Evaluation
            answer_plan = build_answer_plan(
                answer_policy=answer_policy,
                inference_mode=inference_mode,
                user_query=user_query,
                rewritten_query=q_plan.rewritten_query,
                chunks=rerank_out.selected_chunks,
                question_type=q_plan.question_type,
                explicit_web_request=q_plan.explicit_web_request,
                requires_web_search_hint=q_plan.requires_web_search_hint,
                web_search_queries=q_plan.web_search_queries,
                llm_sufficiency_hint=rerank_out.llm_sufficiency_hint,
            )

            # 7. Optional bounded server-side web search fallback (never in notes_only or local)
            web_candidates: list[RawWebResult] = []
            if answer_plan.requires_web_search and inference_mode != "local":
                ws_queries = answer_plan.web_search_queries or [q_plan.rewritten_query]
                ws_outcome = await WebSearchService.search(
                    user_id=user_id,
                    queries=ws_queries,
                    adapter=web_search_adapter,
                )
                answer_plan = answer_plan.model_copy(
                    update={"web_search_status": ws_outcome.status}
                )
                web_candidates = ws_outcome.results

            # 8. Deterministic context allocation (notes ALWAYS prioritized over web & history)
            alloc = allocate_context_budget(
                capability=capability,
                token_counter=token_counter,
                user_query=user_query,
                candidates=rerank_out.selected_chunks,
                history=history_ctx,
                requested_max_output=requested_max_output,
                answer_policy=answer_policy,
                answer_plan=answer_plan,
                web_candidates=web_candidates,
                subject_name=str(subject_name) if subject_name else None,
            )

            user_turn_prompt = build_user_turn_prompt(
                evidence_xml_block=alloc.evidence_xml_block,
                user_query=user_query,
                summary_text=alloc.summary_text,
                web_evidence_xml_block=alloc.web_evidence_xml_block,
                subject_name=str(subject_name) if subject_name else None,
            )
            rendered_single = render_single_prompt(
                system_prompt=alloc.system_prompt,
                recent_history=alloc.recent_history,
                user_turn_prompt=user_turn_prompt,
            )

            adv_trace = AdvancedRagTrace(
                original_query=q_plan.original_query,
                rewritten_query=q_plan.rewritten_query,
                expanded_queries=q_plan.expanded_queries,
                used_llm_query_rewrite=q_plan.used_llm,
                used_llm_query_expansion=q_plan.used_llm and len(q_plan.expanded_queries) > 0,
                used_llm_reranking=rerank_out.used_llm,
                raw_candidate_count=retrieval_res.total_retrieved,
                reranked_selected_count=len(rerank_out.selected_chunks),
                planner_model_id=rerank_out.reranker_model_id or q_plan.planner_model_id,
            )

            # Non-sensitive telemetry (never logs document/user contents or secrets)
            telemetry: dict[str, Any] = {
                "conversation_id": str(conversation_id),
                "subject_id": str(retrieval_res.subject_id),
                "inference_mode": inference_mode,
                "model_id": capability.model_id,
                "answer_policy": answer_policy,
                "sufficiency": answer_plan.sufficiency,
                "web_search_status": answer_plan.web_search_status,
                "retrieval_candidate_count": retrieval_res.total_retrieved,
                "selected_evidence_count": len(alloc.selected_evidence),
                "selected_web_count": len(alloc.selected_web_sources),
                "evidence_omitted_count": len(alloc.omitted_candidates),
                "used_llm_rewrite": adv_trace.used_llm_query_rewrite,
                "used_llm_reranking": adv_trace.used_llm_reranking,
                "total_input_token_count": alloc.accounting.total_input_token_count,
                "model_effective_ceiling": alloc.accounting.model_effective_ceiling,
                "context_utilization_ratio": alloc.accounting.utilization_ratio,
                "compaction_status": history_ctx.compaction_status,
            }
            logger.info("M4.9 context prepared", extra={"context_telemetry": telemetry})

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
                selected_web_sources=alloc.selected_web_sources,
                answer_plan=answer_plan,
                advanced_rag_trace=adv_trace,
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
