"""M4.9 server-side adaptive answer planning & evidence sufficiency evaluation."""

import re

from app.modules.chat.context.schemas import (
    AnswerPlan,
    AnswerPolicy,
    AttributionMode,
    EvidenceSufficiencyStatus,
    QuestionType,
)
from app.modules.knowledge_base.retrieval.schemas import RetrievedChunk

_WORD_RE = re.compile(r"[A-Za-z0-9_+#]{2,}|[\u0980-\u09FF]{2,}")
_STOPWORDS = frozenset(
    {
        "what",
        "why",
        "how",
        "when",
        "where",
        "which",
        "does",
        "is",
        "are",
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "explain",
        "give",
        "example",
        "compare",
        "between",
        "difference",
        "notes",
        "search",
        "online",
        "internet",
    }
)


def _compute_lexical_overlap_ratio(query: str, chunks: list[RetrievedChunk]) -> float:
    """Measure fraction of meaningful query tokens present in retrieved text."""
    q_tokens = {
        tok.lower() for tok in _WORD_RE.findall(query or "") if tok.lower() not in _STOPWORDS
    }
    if not q_tokens or not chunks:
        return 0.0
    combined_corpus = " ".join(c.raw_text.lower() for c in chunks[:5])
    matched = sum(1 for tok in q_tokens if tok in combined_corpus)
    return matched / len(q_tokens)


def evaluate_evidence_sufficiency(
    *,
    user_query: str,
    rewritten_query: str,
    chunks: list[RetrievedChunk],
    question_type: QuestionType,
    llm_sufficiency_hint: str | None = None,
    requires_web_search_hint: bool = False,
) -> tuple[EvidenceSufficiencyStatus, str]:
    """Evaluate retrieved passage coverage independently of mere retrieval existence.

    Retrieval hits do not guarantee complete answer sufficiency (e.g. a note defines
    a concept while the student asks for real-world applications or latest APIs).
    """
    if not chunks:
        if requires_web_search_hint:
            return "external_verification_recommended", "no_kb_chunks_and_external_needed"
        return "notes_insufficient", "no_matching_kb_chunks"

    best_rrf = max(c.rrf_score for c in chunks)
    overlap = _compute_lexical_overlap_ratio(rewritten_query or user_query, chunks)
    min_vec_dist = min(
        (c.vector_distance for c in chunks if c.vector_distance is not None),
        default=None,
    )

    if llm_sufficiency_hint == "conflicting":
        return "notes_conflicting", "reranker_detected_conflict"
    if llm_sufficiency_hint == "insufficient":
        if requires_web_search_hint:
            return "external_verification_recommended", "reranker_marked_insufficient_with_web_need"
        return "notes_insufficient", "reranker_marked_insufficient"

    # Low semantic closeness AND minimal lexical overlap indicates off-topic hits
    if min_vec_dist is not None and min_vec_dist > 0.35 and overlap < 0.15 and best_rrf < 0.012:
        if requires_web_search_hint:
            return "external_verification_recommended", "low_similarity_and_overlap"
        return "notes_insufficient", "low_similarity_and_overlap"

    # Explicit web/current checks cannot rely solely on potentially stale course PDFs
    if requires_web_search_hint:
        if overlap >= 0.45 and llm_sufficiency_hint in ("sufficient", "partial"):
            return "notes_partially_sufficient", "notes_present_but_external_verification_needed"
        return "external_verification_recommended", "external_verification_needed"

    if llm_sufficiency_hint == "partial":
        return "notes_partially_sufficient", "reranker_marked_partial"

    # Comparative / procedural / application questions often require supplementation if notes only cover one side
    lowered = user_query.lower()
    wants_extrapolation = any(
        kw in lowered
        for kw in (
            "real-world",
            "real world",
            "application",
            "applications",
            "disadvantage",
            "advantages",
            "example",
            "why is",
            "difference between",
            "compare",
            "prerequisite",
        )
    )
    if wants_extrapolation and (overlap < 0.70 or len(chunks) == 1):
        return "notes_partially_sufficient", "notes_cover_core_but_explanation_benefits"

    if llm_sufficiency_hint == "sufficient" or (overlap >= 0.45 and best_rrf >= 0.01):
        return "notes_sufficient", "strong_kb_coverage"

    if overlap >= 0.20:
        return "notes_partially_sufficient", "moderate_kb_overlap"

    return "notes_insufficient", "weak_kb_coverage"


def build_answer_plan(
    *,
    answer_policy: AnswerPolicy,
    inference_mode: str,
    user_query: str,
    rewritten_query: str,
    chunks: list[RetrievedChunk],
    question_type: QuestionType,
    explicit_web_request: bool,
    requires_web_search_hint: bool,
    web_search_queries: list[str],
    llm_sufficiency_hint: str | None = None,
) -> AnswerPlan:
    """Execute the deterministic M4.9 Stage 4 answering decision matrix."""
    sufficiency, reason_note = evaluate_evidence_sufficiency(
        user_query=user_query,
        rewritten_query=rewritten_query,
        chunks=chunks,
        question_type=question_type,
        llm_sufficiency_hint=llm_sufficiency_hint,
        requires_web_search_hint=requires_web_search_hint,
    )

    # Enforce strict closed-book mode deterministically server-side when selected
    if answer_policy == "notes_only":
        abstain = (
            not chunks
            or sufficiency in ("notes_insufficient", "external_verification_recommended")
            or question_type == "ambiguous_or_private"
        )
        return AnswerPlan(
            answer_policy="notes_only",
            question_type=question_type,
            sufficiency=sufficiency,
            requires_web_search=False,
            explicit_web_request=explicit_web_request,
            use_general_knowledge=False,
            should_abstain=abstain,
            abstain_reason=(
                "Requested strict notes-only mode without matching subject evidence."
                if abstain
                else None
            ),
            web_search_queries=[],
            web_search_status="skipped_notes_only",
            expected_attribution_mode="abstention" if abstain else "notes_only",
            planning_notes=reason_note,
        )

    # Ambiguous or private personal logistics questions must not be fabricated
    if question_type == "ambiguous_or_private":
        return AnswerPlan(
            answer_policy="note_first_adaptive",
            question_type=question_type,
            sufficiency=sufficiency,
            requires_web_search=False,
            explicit_web_request=explicit_web_request,
            use_general_knowledge=False,
            should_abstain=True,
            abstain_reason="Question depends on unavailable private user data or missing clarifying context.",
            web_search_queries=[],
            web_search_status="not_needed",
            expected_attribution_mode="abstention",
            planning_notes=reason_note,
        )

    should_search_web = (
        explicit_web_request
        or requires_web_search_hint
        or (sufficiency == "external_verification_recommended")
    )
    web_status: str = "not_needed"
    if should_search_web and inference_mode == "local":
        # Threat Vector 8: Private Local never triggers external cloud/web calls from user notes
        should_search_web = False
        web_status = "skipped_private_local"

    has_notes = len(chunks) > 0 and sufficiency != "notes_insufficient"
    if has_notes and should_search_web:
        expected_mode: AttributionMode = "hybrid_all_sources"
    elif has_notes and sufficiency == "notes_sufficient":
        expected_mode = "notes_only"
    elif has_notes:
        expected_mode = "hybrid_notes_and_general"
    elif should_search_web:
        expected_mode = "web_augmented"
    else:
        expected_mode = "general_knowledge"

    return AnswerPlan(
        answer_policy="note_first_adaptive",
        question_type=question_type,
        sufficiency=sufficiency,
        requires_web_search=should_search_web,
        explicit_web_request=explicit_web_request,
        use_general_knowledge=True,
        should_abstain=False,
        abstain_reason=None,
        web_search_queries=web_search_queries[:2] if should_search_web else [],
        web_search_status=web_status,  # type: ignore[arg-type]
        expected_attribution_mode=expected_mode,
        planning_notes=reason_note,
    )
