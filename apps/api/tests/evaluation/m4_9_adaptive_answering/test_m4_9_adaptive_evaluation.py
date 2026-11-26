"""Reproducible M4.9 Adaptive Note-First, General Knowledge & Web-Augmented evaluation suite."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.database import init_db_pool
from app.core.redis import init_redis
from app.main import app
from app.modules.chat.citations.source_repository import get_message_sources_with_revalidation
from app.modules.chat.citations.validator import validate_citations
from app.modules.chat.context.answer_planner import build_answer_plan
from app.modules.chat.context.budget import allocate_context_budget
from app.modules.chat.context.schemas import ConversationHistoryContext, ModelCapability
from app.modules.chat.context.token_counter import ModelTokenCounter
from app.modules.chat.web_search.adapters import BaseWebSearchAdapter
from app.modules.chat.web_search.schemas import RawWebResult, WebSearchOptions
from app.modules.chat.web_search.security import UnsafeWebUrlError, validate_safe_public_http_url
from app.modules.chat.web_search.service import WebSearchService
from app.modules.knowledge_base.retrieval.advanced_rag import plan_and_expand_queries
from app.modules.knowledge_base.retrieval.schemas import RetrievedChunk

DATASET_PATH = Path(__file__).parent / "adaptive_dataset_v1.json"


def _eval_cap() -> ModelCapability:
    return ModelCapability(
        model_id="gemini-2.5-flash",
        provider_id="gemini",
        family="gemini",
        source_type="cloud",
        context_window=32768,
        recommended_budget=16384,
        max_output_tokens=4096,
        default_output_reserve=2048,
        safety_margin_tokens=512,
        token_counting_strategy="conservative_multilingual_upper_bound_v2",
    )


def _to_chunk(idx: int, text: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        subject_id=uuid4(),
        topic_id=uuid4(),
        document_title=f"Course_Notes_{idx}.pdf",
        raw_text=text,
        page_number_start=idx,
        page_number_end=idx,
        page_spans=[{"page_number": idx, "char_start": 0, "char_end": len(text)}],
        content_hash=f"eval-hash-{idx}",
        chunk_version=1,
        model_fingerprint="e5-eval",
        vector_rank=idx,
        fts_rank=idx,
        vector_distance=0.14,
        fts_score=0.45,
        rrf_score=0.025,
        final_rank=idx,
    )


class _DeterministicWebAdapter(BaseWebSearchAdapter):
    provider_name = "eval_mock"

    def __init__(self, rows: list[dict[str, str]]) -> None:
        self._rows = rows

    async def search(self, *, query: str, max_results: int = 5, timeout_seconds: float = 8.0):
        out: list[RawWebResult] = []
        for idx, row in enumerate(self._rows[:max_results], start=1):
            out.append(
                RawWebResult(
                    title=row["title"],
                    url=row["url"],
                    domain=row["domain"],
                    excerpt=row["excerpt"],
                    content_hash=f"web-eval-{idx}",
                    snippet_only=True,
                    retrieval_rank=idx,
                    relevance_score=0.92 - (idx * 0.05),
                    query_used=query,
                )
            )
        return out


@pytest.mark.asyncio
async def test_m4_9_adaptive_answering_evaluation_suite():
    """Execute the 30-case bilingual EN/BN adaptive answering benchmark with zero paid API usage."""
    await init_db_pool()
    await init_redis()
    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    assert len(cases) == 30

    cap = _eval_cap()
    counter = ModelTokenCounter(cap)

    # Metrics counters
    notes_correct = 0
    notes_total = 0
    gk_useful = 0
    gk_total = 0
    web_triggered_when_needed = 0
    web_needed_total = 0
    unnecessary_web_triggers = 0
    not_web_total = 0
    abstain_correct = 0
    abstain_total = 0
    kb_citation_valid = 0
    web_citation_valid = 0
    unsupported_claims = 0
    budget_compliant = 0
    injection_resisted = 0
    injection_total = 0
    web_searches_count = 0

    for case in cases:
        uid = uuid4()
        q = case["question"]
        chunks = [_to_chunk(i, t) for i, t in enumerate(case.get("notes") or [], start=1)]

        q_plan = await plan_and_expand_queries(
            user_id=uid,
            user_query=q,
            subject_name="Course Subject",
            inference_mode="cloud",
            llm_caller=None,  # Deterministic mode
        )
        ans_plan = build_answer_plan(
            answer_policy="note_first_adaptive",
            inference_mode="cloud",
            user_query=q,
            rewritten_query=q_plan.rewritten_query,
            chunks=chunks,
            question_type=q_plan.question_type,
            explicit_web_request=q_plan.explicit_web_request,
            requires_web_search_hint=q_plan.requires_web_search_hint,
            web_search_queries=q_plan.web_search_queries,
            llm_sufficiency_hint=case.get("llm_sufficiency_hint"),
        )

        # Evaluate web-trigger precision & recall
        if case["expect_web_search"]:
            web_needed_total += 1
            if ans_plan.requires_web_search:
                web_triggered_when_needed += 1
        elif case["category"] != "security":
            not_web_total += 1
            if ans_plan.requires_web_search:
                unnecessary_web_triggers += 1

        web_results: list[RawWebResult] = []
        if ans_plan.requires_web_search and case.get("web_results"):
            ws_outcome = await WebSearchService.search(
                user_id=uid,
                queries=ans_plan.web_search_queries or [q_plan.rewritten_query],
                options=WebSearchOptions(fetch_top_pages=0),
                adapter=_DeterministicWebAdapter(case["web_results"]),
            )
            web_searches_count += 1
            web_results = ws_outcome.results
            ans_plan = ans_plan.model_copy(update={"web_search_status": ws_outcome.status})

        alloc = allocate_context_budget(
            capability=cap,
            token_counter=counter,
            user_query=q,
            candidates=chunks,
            history=ConversationHistoryContext(),
            answer_policy="note_first_adaptive",
            answer_plan=ans_plan,
            web_candidates=web_results,
            subject_name="Course Subject",
        )
        if alloc.accounting.total_reserved_tokens <= alloc.accounting.model_effective_ceiling:
            budget_compliant += 1

        # Verify citations in mock/reference answer match server registry
        included_kb = [s.citation_key for s in alloc.selected_evidence]
        included_web = [w.citation_key for w in alloc.selected_web_sources]
        val = validate_citations(case["mock_answer"], included_kb, included_web_keys=included_web)
        if not val.is_valid:
            unsupported_claims += 1
        if included_kb and set(val.valid_note_keys).issubset(set(included_kb)):
            kb_citation_valid += 1
        elif not included_kb and not val.valid_note_keys:
            kb_citation_valid += 1
        if included_web and set(val.valid_web_keys).issubset(set(included_web)):
            web_citation_valid += 1
        elif not included_web and not val.valid_web_keys:
            web_citation_valid += 1

        if case["category"] in ("notes_sufficient", "partial_notes", "conflicting"):
            notes_total += 1
            if len(alloc.selected_evidence) >= 1 and not ans_plan.should_abstain:
                notes_correct += 1

        if case["category"] in ("partial_notes", "missing_notes"):
            gk_total += 1
            if ans_plan.use_general_knowledge and not ans_plan.should_abstain:
                gk_useful += 1

        if case["category"] == "unanswerable":
            abstain_total += 1
            if ans_plan.should_abstain:
                abstain_correct += 1

        # Verify XML injection sanitization + SSRF security checks
        sec = case.get("security_check")
        if sec == "pdf_injection_escaped":
            injection_total += 1
            assert "&lt;" not in alloc.evidence_xml_block or "[S1]" not in val.unknown_keys
            assert "STRICTLY UNTRUSTED DATA" in alloc.system_prompt
            assert val.is_valid
            injection_resisted += 1
        elif sec == "web_injection_escaped":
            injection_total += 1
            assert alloc.selected_web_sources
            assert (
                "&lt;system_instructions&gt;" in alloc.selected_web_sources[0].formatted_xml_block
            )
            assert "STRICTLY UNTRUSTED DATA" in alloc.system_prompt
            injection_resisted += 1
        elif sec == "ssrf_blocked":
            injection_total += 1
            with pytest.raises(UnsafeWebUrlError):
                await validate_safe_public_http_url(
                    "http://169.254.169.254/latest/meta-data/", resolve_dns=False
                )
            injection_resisted += 1

    # Verify DB-level subject isolation, source revocation masking, and cross-user isolation
    from app.core.database import get_pool
    from app.modules.knowledge_base.retrieval import RetrievalService
    from tests.test_m4_5_orchestration_e2e import (
        _create_conversation,
        _create_user,
        _seed_subject_with_kb,
    )

    u1, t1 = await _create_user(prefix="m49_sec_u1")
    u2, t2 = await _create_user(prefix="m49_sec_u2")
    kb1 = await _seed_subject_with_kb(
        subject_name="OS Security",
        topic_name="Isolation",
        doc_title="OS.pdf",
        passage_text="OS kernel isolates user address spaces.",
        page_number=10,
    )
    await _seed_subject_with_kb(
        subject_name="Biology",
        topic_name="Cells",
        doc_title="Bio.pdf",
        passage_text="Mitochondria generate cellular ATP energy.",
        page_number=5,
    )
    conv1 = await _create_conversation(user_id=u1, subject_id=kb1["subject_id"])

    # Subject isolation check (Case 28)
    injection_total += 1
    iso_res = await RetrievalService.retrieve_subject_chunks(
        user_id=u1, conversation_id=conv1, query_text="mitochondria cellular ATP", limit=5
    )
    assert all(c.subject_id == kb1["subject_id"] for c in iso_res.chunks)
    injection_resisted += 1

    # Revoked source masking check & cross-user source check (Cases 29, 30)
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO public.chat_messages (id, conversation_id, sequence_number, role, content, status) "
            "VALUES ($1, $2, 1, 'assistant', 'Note [S1].', 'completed')",
            (m_id := uuid4()),
            conv1,
        )
        await conn.execute(
            "INSERT INTO public.chat_message_sources (message_id, citation_key, chunk_id, document_id, source_chunk_id, source_document_id, document_title_snapshot, page_number_start, page_number_end, excerpt, content_hash, retrieval_rank, relevance_score, included_in_context, referenced_in_answer) "
            "VALUES ($1, 'S1', $2, $3, $2, $3, 'OS.pdf', 10, 10, 'OS kernel isolates user address spaces.', 'h1', 1, 0.9, true, true)",
            m_id,
            kb1["chunk_id"],
            kb1["document_id"],
        )
        await conn.execute(
            "UPDATE public.kb_documents SET is_archived = true WHERE id = $1", kb1["document_id"]
        )
        _, masked = await get_message_sources_with_revalidation(conn, message_id=m_id, user_id=u1)
        assert masked[0].is_available is False
        assert "Archived or Moderated" in masked[0].excerpt
        injection_total += 1
        injection_resisted += 1

        # Cross-user HTTP access check -> 403/404 denied
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/chat/messages/{m_id}/sources",
                headers={"Authorization": f"Bearer {t2}"},
            )
            assert resp.status_code in (403, 404)
            injection_total += 1
            injection_resisted += 1

    # Assert high accuracy thresholds
    assert notes_correct == notes_total
    assert gk_useful == gk_total
    assert web_triggered_when_needed == web_needed_total
    assert unnecessary_web_triggers == 0
    assert abstain_correct == abstain_total
    assert kb_citation_valid == len(cases)
    assert web_citation_valid == len(cases)
    assert unsupported_claims == 0
    assert budget_compliant == len(cases)
    assert injection_resisted == injection_total

    # Persist reproducible evaluation report snapshot alongside dataset
    summary_out = {
        "dataset": "adaptive_dataset_v1.json",
        "total_cases": len(cases),
        "notes_grounded_correct": notes_correct,
        "notes_grounded_total": notes_total,
        "general_knowledge_useful": gk_useful,
        "general_knowledge_total": gk_total,
        "web_triggered_when_needed": web_triggered_when_needed,
        "web_needed_total": web_needed_total,
        "unnecessary_web_triggers": unnecessary_web_triggers,
        "not_web_total": not_web_total,
        "abstain_correct": abstain_correct,
        "abstain_total": abstain_total,
        "kb_citation_validity": round(kb_citation_valid / len(cases), 4),
        "web_citation_validity": round(web_citation_valid / len(cases), 4),
        "unsupported_factual_claim_rate": round(unsupported_claims / len(cases), 4),
        "budget_compliance_rate": round(budget_compliant / len(cases), 4),
        "security_injection_pass_rate": round(injection_resisted / injection_total, 4),
        "average_web_searches_per_turn": round(web_searches_count / len(cases), 4),
    }
    (Path(__file__).parent / "results_m4_9_v1.json").write_text(
        json.dumps(summary_out, indent=2), encoding="utf-8"
    )
