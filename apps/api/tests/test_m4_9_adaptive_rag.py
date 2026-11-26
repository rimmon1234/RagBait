"""Comprehensive backend unit & security tests for M4.9 Adaptive Note-First RAG & Web Search."""

import json
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest

from app.core.database import get_pool, init_db_pool
from app.core.redis import init_redis
from app.modules.chat.citations.source_repository import (
    get_message_sources_with_revalidation,
    insert_included_web_source_snapshots,
    mark_referenced_web_citations,
)
from app.modules.chat.citations.validator import build_citation_repair_messages, validate_citations
from app.modules.chat.context.answer_planner import build_answer_plan
from app.modules.chat.context.budget import allocate_context_budget
from app.modules.chat.context.schemas import (
    ConversationHistoryContext,
    ModelCapability,
    SelectedWebSource,
)
from app.modules.chat.context.token_counter import ModelTokenCounter
from app.modules.chat.web_search.adapters import BaseWebSearchAdapter
from app.modules.chat.web_search.schemas import RawWebResult
from app.modules.chat.web_search.security import (
    UnsafeWebUrlError,
    extract_safe_text_from_html,
    fetch_web_page,
    redact_search_query,
    validate_safe_public_http_url,
)
from app.modules.chat.web_search.service import WebSearchService
from app.modules.knowledge_base.retrieval.advanced_rag import (
    plan_and_expand_queries,
    rerank_and_subselect_chunks,
)
from app.modules.knowledge_base.retrieval.schemas import RetrievedChunk


def _cap() -> ModelCapability:
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


def _chunk(idx: int, text: str, rank: int = 1, rrf: float = 0.02) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        subject_id=uuid4(),
        topic_id=uuid4(),
        document_title=f"Lecture_{idx}.pdf",
        raw_text=text,
        page_number_start=idx,
        page_number_end=idx,
        page_spans=[{"page_number": idx, "char_start": 0, "char_end": len(text)}],
        content_hash=f"hash-{idx}",
        chunk_version=1,
        model_fingerprint="e5-test",
        vector_rank=rank,
        fts_rank=rank,
        vector_distance=0.12,
        fts_score=0.5,
        rrf_score=rrf,
        final_rank=rank,
    )


@pytest.mark.asyncio
async def test_web_url_ssrf_and_redirect_protections():
    """SSRF validator and fetcher reject localhost, private IPs, metadata, bad schemes, and malicious redirects."""
    blocked = [
        "http://127.0.0.1:8000/admin",
        "http://localhost/secrets",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/",
        "http://[::1]/",
        "file:///etc/passwd",
        "ftp://example.com/resource",
        "https://user:secret@example.com/",
        "http://metadata.google.internal/computeMetadata/v1/",
    ]
    for bad_url in blocked:
        with pytest.raises(UnsafeWebUrlError):
            await validate_safe_public_http_url(bad_url, resolve_dns=False)

    # Redirect-based SSRF to loopback must fail after hop check
    async def _redirect_handler(request: httpx.Request) -> httpx.Response:
        if "93.184.216.34" in str(request.url):
            return httpx.Response(302, headers={"location": "http://127.0.0.1:54322/postgres"})
        return httpx.Response(200, text="should never reach")

    transport = httpx.MockTransport(_redirect_handler)
    async with httpx.AsyncClient(transport=transport, follow_redirects=False) as client:
        with pytest.raises(UnsafeWebUrlError):
            await fetch_web_page("https://93.184.216.34/docs", client=client)

    # Disallowed content-type & oversized byte body rejected cleanly
    async def _binary_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/octet-stream"}, content=b"\x00\x01"
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_binary_handler)) as client:
        with pytest.raises(ValueError, match="Disallowed external content-type"):
            await fetch_web_page("https://93.184.216.34/file.bin", client=client)

    clean = extract_safe_text_from_html(
        b"<html><head><script>alert(1)</script></head><body><h1>Python Docs</h1><p>Safe text.</p></body></html>",
        "text/html",
    )
    assert "alert(1)" not in clean
    assert "Python Docs" in clean
    assert "Safe text." in clean


def test_query_redaction_strips_secrets_and_pii():
    raw = (
        "Check my key sk-proj-abc1234567890123 and email student@heritageit.edu.in for Python 3.15"
    )
    redacted = redact_search_query(raw)
    assert "sk-proj-" not in redacted
    assert "student@heritageit.edu.in" not in redacted
    assert "Python 3.15" in redacted


@pytest.mark.asyncio
async def test_unconfigured_web_search_does_not_fabricate_results():
    """Without configured provider keys, WebSearchService reports unavailable_unconfigured honestly."""
    outcome = await WebSearchService.search(
        user_id=uuid4(),
        queries=["What is the latest Python release?"],
    )
    assert outcome.status == "unavailable_unconfigured"
    assert outcome.results == []


@pytest.mark.asyncio
async def test_web_search_rate_limit_and_mock_adapter_ranking():
    """WebSearchService enforces per-user RPM cap and ranks authoritative sources first."""

    class _FakeAdapter(BaseWebSearchAdapter):
        provider_name = "mock"

        async def search(self, *, query: str, max_results: int = 5, timeout_seconds: float = 8.0):
            return [
                RawWebResult(
                    title="Random Blog",
                    url="https://93.184.216.34/blog",
                    domain="randomblog.example",
                    excerpt="Generic blog snippet",
                    content_hash="h1",
                    retrieval_rank=1,
                    relevance_score=0.75,
                ),
                RawWebResult(
                    title="Official Python Docs",
                    url="https://docs.python.org/3/whatsnew/3.15.html",
                    domain="docs.python.org",
                    excerpt="Official Python release notes snippet.",
                    content_hash="h2",
                    retrieval_rank=2,
                    relevance_score=0.70,
                ),
            ]

    uid = uuid4()
    outcome = await WebSearchService.search(
        user_id=uid,
        queries=["Python 3.15 whats new"],
        adapter=_FakeAdapter(),
    )
    assert outcome.status == "executed"
    assert outcome.results[0].domain == "docs.python.org"

    with patch("app.modules.chat.web_search.service._check_search_rate_limit", return_value=False):
        limited = await WebSearchService.search(
            user_id=uid,
            queries=["Python 3.15"],
            adapter=_FakeAdapter(),
        )
    assert limited.status == "unavailable_rate_limited"
    assert limited.results == []


@pytest.mark.asyncio
async def test_advanced_rag_rewrite_expansion_rerank_and_local_privacy():
    """LLM rewrites/expands queries and re-ranks chunks in cloud mode, never leaks in local mode."""
    calls: list[list[dict[str, str]]] = []

    async def _mock_planner(msgs: list[dict[str, str]], max_tok: int):
        calls.append(msgs)
        if "RAG query planner" in msgs[0]["content"]:
            return (
                json.dumps(
                    {
                        "rewritten_query": "binary search vs linear search O(log n) comparison",
                        "expanded_queries": [
                            "binary search time complexity O(log n)",
                            "linear search O(n) sequential comparison",
                        ],
                        "question_type": "comparative",
                        "requires_web_search": False,
                        "web_queries": [],
                    }
                ),
                "mock-model",
            )
        return (
            json.dumps(
                {
                    "selected_indices": [2, 1],
                    "sufficiency": "partial",
                    "reason": "Chunk 2 directly defines logarithmic halving",
                }
            ),
            "mock-model",
        )

    uid = uuid4()
    plan = await plan_and_expand_queries(
        user_id=uid,
        user_query="Why is binary search faster than linear search?",
        subject_name="Data Structures",
        inference_mode="cloud",
        llm_caller=_mock_planner,
    )
    assert plan.used_llm is True
    assert "O(log n)" in plan.rewritten_query
    assert len(plan.expanded_queries) == 2

    c1 = _chunk(1, "Arrays store contiguous elements in memory.", rank=1, rrf=0.03)
    c2 = _chunk(2, "Binary search halves a sorted array each step.", rank=2, rrf=0.02)
    c3 = _chunk(3, "Unrelated graph traversal BFS.", rank=3, rrf=0.01)

    reranked = await rerank_and_subselect_chunks(
        user_id=uid,
        user_query="Why is binary search faster?",
        rewritten_query=plan.rewritten_query,
        candidates=[c1, c2, c3],
        inference_mode="cloud",
        llm_caller=_mock_planner,
    )
    assert reranked.used_llm is True
    assert [c.chunk_id for c in reranked.selected_chunks] == [c2.chunk_id, c1.chunk_id]
    assert reranked.dropped_chunk_count == 1
    assert reranked.llm_sufficiency_hint == "partial"

    # Private Local MUST NOT call cloud LLM planner or reranker
    pre_calls = len(calls)
    local_plan = await plan_and_expand_queries(
        user_id=uid,
        user_query="Why is binary search faster?",
        subject_name="Data Structures",
        inference_mode="local",
        llm_caller=_mock_planner,
    )
    assert local_plan.used_llm is False
    assert len(calls) == pre_calls


def test_citation_validator_supports_mixed_note_web_and_general_knowledge():
    """Validator permits [Sn] notes, [Wn] web sources, and general knowledge, while rejecting fakes."""
    # Mixed valid answer
    ok = validate_citations(
        "**From your notes**\nPaging uses page tables [S1].\n\n"
        "**Additional explanation**\nThink of it like a book index.\n\n"
        "**Web research**\nLinux 6.12 updated folio handling [W1].",
        ["S1"],
        included_web_keys=["W1"],
    )
    assert ok.is_valid is True
    assert ok.status == "valid_grounded"
    assert ok.valid_note_keys == ["S1"]
    assert ok.valid_web_keys == ["W1"]

    # Pure general knowledge when no notes/web included
    gk = validate_citations(
        "**Additional explanation**\nRecursion solves problems by calling itself on smaller inputs.",
        [],
        included_web_keys=[],
    )
    assert gk.is_valid is True
    assert gk.status == "valid_no_sources"

    # Fabricated web or note key must be rejected
    bad = validate_citations(
        "Notes say X [S9] and web says Y [W4].", ["S1"], included_web_keys=["W1"]
    )
    assert bad.is_valid is False
    assert set(bad.unknown_keys) == {"S9", "W4"}

    repair_msgs = build_citation_repair_messages(
        original_answer="Bad [S9] and good [W1] with general explanation.",
        valid_included_keys=["S1"],
        valid_web_keys=["W1"],
        unknown_keys=["S9"],
        malformed_tokens=[],
    )
    assert "Preserve valid general-knowledge" in repair_msgs[0]["content"]
    assert "[W1]" in repair_msgs[0]["content"]


def test_context_budget_prioritizes_notes_over_web_and_enforces_notes_only_server_side():
    """KB notes never get crowded out by web sources, and notes_only abstains without notes."""
    cap = _cap()
    counter = ModelTokenCounter(cap)
    c1 = _chunk(1, "Normalization eliminates transitive dependencies (3NF).", rank=1, rrf=0.05)
    web1 = RawWebResult(
        title="Postgres Docs",
        url="https://postgresql.org/docs/current/ddl.html",
        domain="postgresql.org",
        excerpt="PostgreSQL supports ACID relational tables.",
        content_hash="wh1",
        snippet_only=True,
        retrieval_rank=1,
        relevance_score=0.95,
    )

    plan_adaptive = build_answer_plan(
        answer_policy="note_first_adaptive",
        inference_mode="cloud",
        user_query="Explain normalization and real-world database use.",
        rewritten_query="database normalization 3NF real-world applications",
        chunks=[c1],
        question_type="academic_conceptual",
        explicit_web_request=False,
        requires_web_search_hint=False,
        web_search_queries=[],
        llm_sufficiency_hint="partial",
    )
    assert plan_adaptive.should_abstain is False
    assert plan_adaptive.use_general_knowledge is True

    alloc = allocate_context_budget(
        capability=cap,
        token_counter=counter,
        user_query="Explain normalization and real-world database use.",
        candidates=[c1],
        history=ConversationHistoryContext(),
        answer_policy="note_first_adaptive",
        answer_plan=plan_adaptive,
        web_candidates=[web1],
        subject_name="DBMS",
    )
    assert len(alloc.selected_evidence) == 1
    assert len(alloc.selected_web_sources) == 1
    assert alloc.accounting.total_reserved_tokens <= alloc.accounting.model_effective_ceiling
    assert "NOTE_FIRST_ADAPTIVE" in alloc.system_prompt
    assert '<web_source id="W1">' in (alloc.web_evidence_xml_block or "")

    # Server-enforced strict notes_only policy when zero notes exist -> abstention directive, no web
    strict_plan = build_answer_plan(
        answer_policy="notes_only",
        inference_mode="cloud",
        user_query="Explain quantum tunneling.",
        rewritten_query="quantum tunneling",
        chunks=[],
        question_type="academic_conceptual",
        explicit_web_request=True,
        requires_web_search_hint=True,
        web_search_queries=["quantum tunneling"],
    )
    assert strict_plan.should_abstain is True
    assert strict_plan.requires_web_search is False
    assert strict_plan.use_general_knowledge is False


@pytest.mark.asyncio
async def test_web_source_snapshot_persistence_and_revalidation():
    """Web source snapshots persist cleanly and distinguish web_source from kb_note in revalidation."""
    await init_db_pool()
    await init_redis()
    from tests.test_m4_5_orchestration_e2e import (
        _create_conversation,
        _create_user,
        _seed_subject_with_kb,
    )

    owner_id, _ = await _create_user(prefix="web_snap_owner")
    kb = await _seed_subject_with_kb(
        subject_name="Modern Computing",
        topic_name="Runtimes",
        doc_title="Intro.pdf",
        passage_text="Python is a high-level interpreted language.",
        page_number=2,
    )
    conv_id = await _create_conversation(user_id=owner_id, subject_id=kb["subject_id"])

    pool = get_pool()
    async with pool.acquire() as conn:
        user_row = await conn.fetchrow(
            """
            INSERT INTO public.chat_messages (conversation_id, sequence_number, role, content, status)
            VALUES ($1, 1, 'user', 'What is new in Python?', 'completed')
            RETURNING id;
            """,
            conv_id,
        )
        assert user_row is not None
        asst_row = await conn.fetchrow(
            """
            INSERT INTO public.chat_messages (conversation_id, sequence_number, role, content, status)
            VALUES ($1, 2, 'assistant', 'Python evolves rapidly [W1].', 'completed')
            RETURNING id;
            """,
            conv_id,
        )
        assert asst_row is not None
        msg_id = asst_row["id"]

        inserted = await insert_included_web_source_snapshots(
            conn,
            message_id=msg_id,
            selected_web_sources=[
                SelectedWebSource(
                    citation_key="W1",
                    title="Python 3.15 Docs",
                    url="https://docs.python.org/3.15/",
                    domain="docs.python.org",
                    publisher="PSF",
                    published_at="2026-10-01",
                    excerpt="Python 3.15 adds performance enhancements.",
                    content_hash="abc123hash",
                    snippet_only=True,
                    retrieval_rank=1,
                    relevance_score=0.95,
                )
            ],
            referenced_keys=set(),
        )
        assert inserted == 1
        updated = await mark_referenced_web_citations(
            conn, message_id=msg_id, referenced_keys=["W1"]
        )
        assert updated == 1

        _, sources = await get_message_sources_with_revalidation(
            conn, message_id=msg_id, user_id=owner_id
        )

    assert len(sources) == 1
    assert sources[0].source_type == "web_source"
    assert sources[0].citation_key == "W1"
    assert sources[0].url == "https://docs.python.org/3.15/"
    assert sources[0].referenced_in_answer is True
    assert sources[0].snippet_only is True
