"""Deterministic model-aware context budget allocator and evidence selector."""

import logging
from uuid import UUID

from app.modules.chat.context.config import context_settings
from app.modules.chat.context.formatter import (
    format_evidence_envelope,
    format_source_block,
    format_summary_envelope,
    format_user_query_envelope,
    format_web_evidence_envelope,
    format_web_source_block,
)
from app.modules.chat.context.prompt_builder import (
    build_llm_messages,
    build_system_prompt,
    combine_system_prompt,
)
from app.modules.chat.context.schemas import (
    AnswerPlan,
    AnswerPolicy,
    ConversationHistoryContext,
    HistoryTurnMessage,
    ModelCapability,
    OmittedEvidenceRecord,
    PromptBudgetExceededError,
    SelectedEvidenceSource,
    SelectedWebSource,
    TokenAccountingBreakdown,
)
from app.modules.chat.context.token_counter import ModelTokenCounter
from app.modules.chat.web_search.schemas import RawWebResult
from app.modules.knowledge_base.retrieval.schemas import RetrievedChunk

logger = logging.getLogger("ragbait.chat.context.budget")


def _has_valid_provenance(chunk: RetrievedChunk) -> bool:
    """Verify minimum required physical provenance metadata on a retrieved candidate."""
    return (
        bool(chunk.raw_text and chunk.raw_text.strip())
        and bool(chunk.document_title and chunk.document_title.strip())
        and bool(chunk.content_hash and chunk.content_hash.strip())
        and chunk.page_number_start >= 1
        and chunk.page_number_end >= chunk.page_number_start
    )


def _group_history_into_turns(
    messages: list[HistoryTurnMessage],
) -> list[list[HistoryTurnMessage]]:
    """Group completed chronological history messages into complete conversational turns.

    Adjacent (user, assistant) pairs are kept atomic so turns are never sliced in half.
    Orphan messages (e.g. partial turn recovery) form single-message turn groups.
    """
    turns: list[list[HistoryTurnMessage]] = []
    idx = 0
    n = len(messages)
    while idx < n:
        cur = messages[idx]
        if cur.role == "user" and (idx + 1) < n and messages[idx + 1].role == "assistant":
            turns.append([cur, messages[idx + 1]])
            idx += 2
        else:
            turns.append([cur])
            idx += 1
    return turns


class ContextBudgetAllocation:
    """Container holding allocated sections and verified token breakdown."""

    def __init__(
        self,
        *,
        system_prompt: str,
        evidence_xml_block: str,
        selected_evidence: list[SelectedEvidenceSource],
        omitted_candidates: list[OmittedEvidenceRecord],
        summary_text: str | None,
        recent_history: list[HistoryTurnMessage],
        llm_messages: list[dict[str, str]],
        accounting: TokenAccountingBreakdown,
        web_evidence_xml_block: str | None = None,
        selected_web_sources: list[SelectedWebSource] | None = None,
    ) -> None:
        self.system_prompt = system_prompt
        self.evidence_xml_block = evidence_xml_block
        self.web_evidence_xml_block = web_evidence_xml_block
        self.selected_evidence = selected_evidence
        self.selected_web_sources = selected_web_sources or []
        self.omitted_candidates = omitted_candidates
        self.summary_text = summary_text
        self.recent_history = recent_history
        self.llm_messages = llm_messages
        self.accounting = accounting


def allocate_context_budget(
    *,
    capability: ModelCapability,
    token_counter: ModelTokenCounter,
    user_query: str,
    candidates: list[RetrievedChunk],
    history: ConversationHistoryContext,
    requested_max_output: int | None = None,
    evidence_ratio: float | None = None,
    max_evidence_tokens: int | None = None,
    safety_margin_override: int | None = None,
    answer_policy: AnswerPolicy = "notes_only",
    answer_plan: AnswerPlan | None = None,
    web_candidates: list[RawWebResult] | None = None,
    subject_name: str | None = None,
) -> ContextBudgetAllocation:
    """Execute the deterministic M4.0 context allocation formula:

        B_avail = C_model - (T_out + T_sys + T_guard + T_safe)

    Guarantees:
    - T_user (current question) is prioritized first and never truncated silently.
    - Evidence budget B_evidence = min(B_rem1 * ratio, max_evidence_tokens).
    - Candidates are selected greedily in authoritative retrieval rank order without truncation.
    - A candidate exceeding remaining budget does not block smaller later candidates.
    - Conversation summary and complete recent history turns fit into the remaining headroom.
    - Final invariant enforced: T_actual_prompt + T_out <= C_model.
    """
    clean_query = user_query.strip() if user_query else ""
    if not clean_query:
        raise PromptBudgetExceededError(
            message="User message cannot be empty",
            code="EMPTY_USER_MESSAGE",
        )

    eff_ratio = (
        evidence_ratio if evidence_ratio is not None else context_settings.CONTEXT_EVIDENCE_RATIO
    )
    eff_max_ev = (
        max_evidence_tokens
        if max_evidence_tokens is not None
        else context_settings.CONTEXT_MAX_EVIDENCE_TOKENS
    )

    c_model = min(capability.recommended_budget, capability.context_window)
    t_out = (
        min(requested_max_output, capability.max_output_tokens)
        if requested_max_output and requested_max_output > 0
        else min(capability.default_output_reserve, capability.max_output_tokens)
    )
    t_safe = (
        safety_margin_override
        if safety_margin_override is not None
        else capability.safety_margin_tokens
    )

    # Measure system & guardrail overhead conservatively across evidence states for active policy
    core_with_ev, guard_text = build_system_prompt(
        has_evidence=True,
        has_web_evidence=bool(web_candidates),
        answer_policy=answer_policy,
        answer_plan=answer_plan,
    )
    core_no_ev, _ = build_system_prompt(
        has_evidence=False,
        has_web_evidence=False,
        answer_policy=answer_policy,
        answer_plan=answer_plan,
    )
    t_sys = max(token_counter.count_text(core_with_ev), token_counter.count_text(core_no_ev))
    t_guard = (
        token_counter.count_text(guard_text)
        + capability.primer_overhead_tokens
        + (capability.message_overhead_tokens * 2)  # system role + current user turn framing
    )

    b_avail = c_model - (t_out + t_sys + t_guard + t_safe)
    if b_avail <= 0:
        raise PromptBudgetExceededError(
            message=(
                f"Model '{capability.model_id}' context limit ({c_model}) is insufficient "
                f"for required output/system reserves ({t_out + t_sys + t_guard + t_safe})"
            ),
            details={"model_id": capability.model_id, "c_model": c_model},
        )

    # 1. Allocate Current User Prompt (Mandatory, never truncated)
    user_query_xml = format_user_query_envelope(clean_query)
    t_user = token_counter.count_text(user_query_xml)
    if t_user > b_avail:
        raise PromptBudgetExceededError(
            message=(
                f"Current user question ({t_user} tokens) exceeds available working budget "
                f"({b_avail} tokens for '{capability.model_id}')"
            ),
            details={"question_tokens": t_user, "available_budget": b_avail},
        )

    b_rem1 = b_avail - t_user

    # 2. Allocate Evidence Pool & Select Passages Deterministically
    b_evidence = min(int(b_rem1 * eff_ratio), eff_max_ev)
    empty_env = format_evidence_envelope([])
    wrapper_overhead = token_counter.count_text(
        '<study_context_evidence trust_level="untrusted_reference_data">\n\n</study_context_evidence>'
    )
    empty_ev_tokens = token_counter.count_text(empty_env)

    # Sort deterministically by authoritative retrieval rank, then deduplicate by chunk_id
    sorted_candidates = sorted(candidates, key=lambda c: (c.final_rank, str(c.chunk_id)))
    unique_candidates: list[RetrievedChunk] = []
    seen_chunk_ids: set[UUID] = set()
    omitted: list[OmittedEvidenceRecord] = []

    for c in sorted_candidates:
        if c.chunk_id in seen_chunk_ids:
            omitted.append(
                OmittedEvidenceRecord(
                    chunk_id=c.chunk_id,
                    document_id=c.document_id,
                    retrieval_rank=c.final_rank,
                    formatted_token_count=0,
                    reason="duplicate_candidate_id",
                )
            )
            continue
        seen_chunk_ids.add(c.chunk_id)
        unique_candidates.append(c)

    selected_evidence: list[SelectedEvidenceSource] = []
    ev_budget_rem = max(0, b_evidence - wrapper_overhead)

    for cand in unique_candidates:
        if not _has_valid_provenance(cand):
            omitted.append(
                OmittedEvidenceRecord(
                    chunk_id=cand.chunk_id,
                    document_id=cand.document_id,
                    retrieval_rank=cand.final_rank,
                    formatted_token_count=0,
                    reason="missing_provenance",
                )
            )
            continue

        tentative_key = f"S{len(selected_evidence) + 1}"
        xml_block = format_source_block(tentative_key, cand)
        block_tokens = token_counter.count_text(xml_block)

        if block_tokens <= ev_budget_rem:
            selected_evidence.append(
                SelectedEvidenceSource(
                    citation_key=tentative_key,
                    chunk_id=cand.chunk_id,
                    document_id=cand.document_id,
                    subject_id=cand.subject_id,
                    topic_id=cand.topic_id,
                    document_title=cand.document_title,
                    raw_text=cand.raw_text,
                    page_number_start=cand.page_number_start,
                    page_number_end=cand.page_number_end,
                    page_spans=cand.page_spans,
                    content_hash=cand.content_hash,
                    chunk_version=cand.chunk_version,
                    retrieval_rank=cand.final_rank,
                    rrf_score=cand.rrf_score,
                    formatted_xml_block=xml_block,
                    formatted_token_count=block_tokens,
                    included_in_context=True,
                )
            )
            ev_budget_rem -= block_tokens
        else:
            reason = (
                "exceeds_evidence_budget_ceiling"
                if block_tokens > max(0, b_evidence - wrapper_overhead)
                else "exceeds_remaining_evidence_budget"
            )
            omitted.append(
                OmittedEvidenceRecord(
                    chunk_id=cand.chunk_id,
                    document_id=cand.document_id,
                    retrieval_rank=cand.final_rank,
                    formatted_token_count=block_tokens,
                    reason=reason,
                )
            )

    evidence_xml_block = format_evidence_envelope(
        [s.formatted_xml_block for s in selected_evidence]
    )
    t_evidence = (
        token_counter.count_text(evidence_xml_block) if selected_evidence else empty_ev_tokens
    )

    # If the serialized envelope slightly exceeded b_evidence due to block boundary rounding,
    # pop the last selected evidence items cleanly until t_evidence <= b_evidence (or empty).
    while selected_evidence and t_evidence > b_evidence:
        popped = selected_evidence.pop()
        omitted.append(
            OmittedEvidenceRecord(
                chunk_id=popped.chunk_id,
                document_id=popped.document_id,
                retrieval_rank=popped.retrieval_rank,
                formatted_token_count=popped.formatted_token_count,
                reason="envelope_rounding_exceeded_evidence_budget",
            )
        )
        evidence_xml_block = format_evidence_envelope(
            [s.formatted_xml_block for s in selected_evidence]
        )
        t_evidence = (
            token_counter.count_text(evidence_xml_block) if selected_evidence else empty_ev_tokens
        )

    b_rem2 = max(0, b_rem1 - t_evidence)

    # 2b. Allocate External Web Evidence Budget (never crowds out KB notes!)
    selected_web_sources: list[SelectedWebSource] = []
    web_xml_block: str | None = None
    t_web = 0
    if web_candidates:
        b_web = min(
            int(b_rem2 * context_settings.WEB_EVIDENCE_MAX_RATIO),
            context_settings.WEB_EVIDENCE_MAX_TOKENS,
        )
        web_wrapper_tokens = token_counter.count_text(
            '<web_context_evidence trust_level="untrusted_external_web">\n\n</web_context_evidence>'
        )
        web_budget_rem = max(0, b_web - web_wrapper_tokens)
        seen_urls: set[str] = set()
        for w_cand in web_candidates:
            norm_url = w_cand.url.strip().rstrip("/")
            if norm_url in seen_urls or not w_cand.excerpt.strip():
                continue
            seen_urls.add(norm_url)
            w_key = f"W{len(selected_web_sources) + 1}"
            w_xml = format_web_source_block(
                w_key,
                title=w_cand.title,
                url=w_cand.url,
                domain=w_cand.domain,
                excerpt=w_cand.excerpt,
                published_at=w_cand.published_at,
                retrieved_at_iso=w_cand.retrieved_at.isoformat(),
                snippet_only=w_cand.snippet_only,
            )
            w_tokens = token_counter.count_text(w_xml)
            if w_tokens <= web_budget_rem:
                selected_web_sources.append(
                    SelectedWebSource(
                        citation_key=w_key,
                        title=w_cand.title,
                        url=w_cand.url,
                        domain=w_cand.domain,
                        publisher=w_cand.publisher,
                        published_at=w_cand.published_at,
                        retrieved_at=w_cand.retrieved_at,
                        excerpt=w_cand.excerpt,
                        content_hash=w_cand.content_hash,
                        snippet_only=w_cand.snippet_only,
                        retrieval_rank=len(selected_web_sources) + 1,
                        relevance_score=w_cand.relevance_score,
                        formatted_xml_block=w_xml,
                        formatted_token_count=w_tokens,
                        included_in_context=True,
                    )
                )
                web_budget_rem -= w_tokens

        if selected_web_sources:
            web_xml_block = format_web_evidence_envelope(
                [w.formatted_xml_block for w in selected_web_sources]
            )
            t_web = token_counter.count_text(web_xml_block)

    b_rem2_after_web = max(0, b_rem2 - t_web)

    # 3. Allocate Conversation Summary
    included_summary: str | None = None
    t_summary = 0
    if history.summary_text and history.summary_text.strip():
        summary_env = format_summary_envelope(history.summary_text)
        cand_summary_tokens = token_counter.count_text(summary_env)
        if cand_summary_tokens <= b_rem2_after_web:
            included_summary = history.summary_text.strip()
            t_summary = cand_summary_tokens

    b_rem3 = max(0, b_rem2_after_web - t_summary)

    # 4. Allocate Recent History in Complete Turn Pairs (Newest -> Oldest)
    turn_groups = _group_history_into_turns(history.recent_messages)
    chosen_turns: list[list[HistoryTurnMessage]] = []
    t_history = 0

    for turn in reversed(turn_groups):
        turn_tokens = sum(token_counter.count_message(m.role, m.content) for m in turn)
        if t_history + turn_tokens <= b_rem3:
            chosen_turns.append(turn)
            t_history += turn_tokens
        else:
            # Stop at first turn that does not fit completely to preserve tail continuity
            break

    chosen_turns.reverse()
    selected_history: list[HistoryTurnMessage] = [m for grp in chosen_turns for m in grp]

    # 5. Final Assembly & Hard Total Context Verification (T_actual + T_out <= C_model)
    core_sys, guard_sys = build_system_prompt(
        has_evidence=len(selected_evidence) > 0,
        has_web_evidence=len(selected_web_sources) > 0,
        answer_policy=answer_policy,
        answer_plan=answer_plan,
    )
    system_prompt = combine_system_prompt(core_sys, guard_sys)

    def _assemble_and_count(
        hist_msgs: list[HistoryTurnMessage],
        sum_txt: str | None,
        web_xml: str | None,
    ) -> tuple[list[dict[str, str]], int]:
        from app.modules.chat.context.prompt_builder import build_user_turn_prompt

        u_prompt = build_user_turn_prompt(
            evidence_xml_block=evidence_xml_block,
            user_query=clean_query,
            summary_text=sum_txt,
            web_evidence_xml_block=web_xml,
            subject_name=subject_name,
        )
        msgs = build_llm_messages(
            system_prompt=system_prompt,
            recent_history=hist_msgs,
            user_turn_prompt=u_prompt,
        )
        return msgs, token_counter.count_messages(msgs)

    llm_messages, total_input_tokens = _assemble_and_count(
        selected_history, included_summary, web_xml_block
    )

    # Trim oldest history turns -> summary -> web evidence -> note evidence cleanly if needed
    while (total_input_tokens + t_out) > c_model and chosen_turns:
        chosen_turns.pop(0)
        selected_history = [m for grp in chosen_turns for m in grp]
        t_history = sum(token_counter.count_message(m.role, m.content) for m in selected_history)
        llm_messages, total_input_tokens = _assemble_and_count(
            selected_history, included_summary, web_xml_block
        )

    if (total_input_tokens + t_out) > c_model and included_summary is not None:
        included_summary = None
        t_summary = 0
        llm_messages, total_input_tokens = _assemble_and_count(
            selected_history, None, web_xml_block
        )

    while (total_input_tokens + t_out) > c_model and selected_web_sources:
        selected_web_sources.pop()
        web_xml_block = (
            format_web_evidence_envelope([w.formatted_xml_block for w in selected_web_sources])
            if selected_web_sources
            else None
        )
        core_sys, guard_sys = build_system_prompt(
            has_evidence=len(selected_evidence) > 0,
            has_web_evidence=len(selected_web_sources) > 0,
            answer_policy=answer_policy,
            answer_plan=answer_plan,
        )
        system_prompt = combine_system_prompt(core_sys, guard_sys)
        llm_messages, total_input_tokens = _assemble_and_count(
            selected_history, included_summary, web_xml_block
        )

    while (total_input_tokens + t_out) > c_model and selected_evidence:
        popped = selected_evidence.pop()
        omitted.append(
            OmittedEvidenceRecord(
                chunk_id=popped.chunk_id,
                document_id=popped.document_id,
                retrieval_rank=popped.retrieval_rank,
                formatted_token_count=popped.formatted_token_count,
                reason="total_window_ceiling_guard",
            )
        )
        evidence_xml_block = format_evidence_envelope(
            [s.formatted_xml_block for s in selected_evidence]
        )
        t_evidence = (
            token_counter.count_text(evidence_xml_block) if selected_evidence else empty_ev_tokens
        )
        core_sys, guard_sys = build_system_prompt(
            has_evidence=len(selected_evidence) > 0,
            has_web_evidence=len(selected_web_sources) > 0,
            answer_policy=answer_policy,
            answer_plan=answer_plan,
        )
        system_prompt = combine_system_prompt(core_sys, guard_sys)
        llm_messages, total_input_tokens = _assemble_and_count(
            selected_history, included_summary, web_xml_block
        )

    if (total_input_tokens + t_out) > c_model:
        raise PromptBudgetExceededError(
            message=(
                f"Prepared prompt ({total_input_tokens} tokens) plus output reserve ({t_out} tokens) "
                f"exceeds model effective limit ({c_model} tokens)"
            ),
            details={
                "total_input_tokens": total_input_tokens,
                "reserved_output_tokens": t_out,
                "model_effective_ceiling": c_model,
            },
        )

    utilization = round((total_input_tokens + t_out) / c_model, 4)
    accounting = TokenAccountingBreakdown(
        model_effective_ceiling=c_model,
        reserved_output_tokens=t_out,
        system_prompt_tokens=t_sys,
        guardrail_overhead_tokens=t_guard,
        safety_margin_tokens=t_safe,
        available_working_budget=b_avail,
        current_question_tokens=t_user,
        evidence_budget_ceiling=b_evidence,
        evidence_token_count=t_evidence,
        summary_token_count=t_summary,
        recent_history_token_count=t_history,
        total_input_token_count=total_input_tokens,
        total_reserved_tokens=total_input_tokens + t_out,
        utilization_ratio=utilization,
        count_source="exact_tokenizer"
        if capability.exact_tokenizer_available
        else "local_estimate",
        counting_strategy=capability.token_counting_strategy,
        is_approximate=not capability.exact_tokenizer_available,
    )

    return ContextBudgetAllocation(
        system_prompt=system_prompt,
        evidence_xml_block=evidence_xml_block,
        web_evidence_xml_block=web_xml_block,
        selected_evidence=selected_evidence,
        selected_web_sources=selected_web_sources,
        omitted_candidates=omitted,
        summary_text=included_summary,
        recent_history=selected_history,
        llm_messages=llm_messages,
        accounting=accounting,
    )
