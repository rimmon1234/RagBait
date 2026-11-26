"""Unit tests for Intelligent Routing Policy and Deterministic Comparator (M2.9)."""

from uuid import UUID, uuid4

from app.modules.routing.policy import filter_candidates, rank_candidates
from app.modules.routing.schemas import RoutingCandidate, SourceType


def _make_candidate(
    model_id: str = "gemini-2.5-flash",
    provider_id: str = "google",
    source_type: SourceType = "cloud",
    context_window: int = 1_000_000,
    max_output_tokens: int = 8192,
    supports_streaming: bool = True,
    free_tier_eligible: bool = False,
    pricing_input_cents_per_million: int | None = 10,
    pricing_output_cents_per_million: int | None = 40,
    credential_id: UUID | None = UUID("11111111-1111-1111-1111-111111111111"),
    is_cooling_down: bool = False,
    max_concurrent_jobs: int = 5,
    active_concurrency: int = 0,
    node_id: UUID | None = None,
) -> RoutingCandidate:
    capabilities = ["chat"]
    if supports_streaming:
        capabilities.append("streaming")

    return RoutingCandidate(
        source_type=source_type,
        provider_id=provider_id,
        model_id=model_id,
        display_name=f"Test {model_id}",
        family="gemini" if "gemini" in model_id else "llama",
        capabilities=capabilities,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        free_tier_eligible=free_tier_eligible,
        pricing_input_cents_per_million=pricing_input_cents_per_million,
        pricing_output_cents_per_million=pricing_output_cents_per_million,
        credential_id=credential_id,
        is_cooling_down=is_cooling_down,
        max_concurrent_jobs=max_concurrent_jobs,
        active_concurrency=active_concurrency,
        node_id=node_id,
    )


def test_deterministic_ranking_identical_outputs():
    """Identical candidate sets must produce identical ranked orders every time."""
    c1 = _make_candidate(
        model_id="m1_free",
        free_tier_eligible=True,
        pricing_input_cents_per_million=0,
        pricing_output_cents_per_million=0,
    )
    c2 = _make_candidate(model_id="m2_community", source_type="community", credential_id=None)
    c3 = _make_candidate(
        model_id="m3_pricier",
        pricing_input_cents_per_million=20,
        pricing_output_cents_per_million=40,
    )
    c4 = _make_candidate(
        model_id="m4_cheaper",
        pricing_input_cents_per_million=5,
        pricing_output_cents_per_million=10,
    )

    candidates = [c3, c1, c4, c2]

    ranked_1 = rank_candidates(
        list(candidates), estimated_input_tokens=100, estimated_output_tokens=200
    )
    ranked_2 = rank_candidates(
        list(candidates), estimated_input_tokens=100, estimated_output_tokens=200
    )

    assert [c.model_id for c in ranked_1] == [c.model_id for c in ranked_2]
    # Free tier first
    assert ranked_1[0].model_id == "m1_free"
    # Community second
    assert ranked_1[1].model_id == "m2_community"
    # Metered sorted by microcent cost (m4 cheaper than m3)
    assert ranked_1[2].model_id == "m4_cheaper"
    assert ranked_1[3].model_id == "m3_pricier"


def test_deterministic_tie_breaker_alphabetical():
    """Candidates in the same tier and cost must be sorted alphabetically by provider/model identifier."""
    c_b = _make_candidate(
        provider_id="beta_provider",
        model_id="m1",
        free_tier_eligible=True,
        pricing_input_cents_per_million=0,
    )
    c_a = _make_candidate(
        provider_id="alpha_provider",
        model_id="m1",
        free_tier_eligible=True,
        pricing_input_cents_per_million=0,
    )
    c_z = _make_candidate(
        provider_id="zeta_provider",
        model_id="m1",
        free_tier_eligible=True,
        pricing_input_cents_per_million=0,
    )

    ranked = rank_candidates([c_z, c_b, c_a], estimated_input_tokens=50, estimated_output_tokens=50)
    assert [c.provider_id for c in ranked] == ["alpha_provider", "beta_provider", "zeta_provider"]


def test_missing_pricing_represented_as_none_not_free():
    """Missing pricing must be represented as None, ranked last, NEVER treated as free tier or 0 cost."""
    free_cand = _make_candidate(
        model_id="free_c",
        free_tier_eligible=True,
        pricing_input_cents_per_million=0,
        pricing_output_cents_per_million=0,
    )
    metered_known = _make_candidate(
        model_id="metered_k",
        pricing_input_cents_per_million=10,
        pricing_output_cents_per_million=10,
    )
    metered_unknown = _make_candidate(
        model_id="metered_u",
        pricing_input_cents_per_million=None,
        pricing_output_cents_per_million=None,
    )

    ranked = rank_candidates(
        [metered_unknown, metered_known, free_cand],
        estimated_input_tokens=1000,
        estimated_output_tokens=500,
    )

    # Free tier (Tier 0 in cost_tier)
    assert ranked[0].model_id == "free_c"
    # Metered with known pricing
    assert ranked[1].model_id == "metered_k"
    # Metered with unknown pricing - must NEVER sort ahead of known pricing
    assert ranked[2].model_id == "metered_u"


def test_exact_integer_microcent_calculation():
    """Microcents must follow formula: est_in * in_cost + est_out * out_cost with no float rounding."""
    est_in = 1500
    est_out = 300
    in_cents_per_m = 25  # 25 cents per million tokens
    out_cents_per_m = 100  # 100 cents per million tokens

    # Expected microcents:
    # 1500 * 25 = 37,500
    # 300 * 100 = 30,000
    # Total = 67,500 microcents
    c_cheaper = _make_candidate(
        model_id="calc_c",
        pricing_input_cents_per_million=in_cents_per_m,
        pricing_output_cents_per_million=out_cents_per_m,
    )
    c_pricier = _make_candidate(
        model_id="calc_expensive",
        pricing_input_cents_per_million=in_cents_per_m * 2,
        pricing_output_cents_per_million=out_cents_per_m * 2,
    )
    ranked = rank_candidates(
        [c_pricier, c_cheaper], estimated_input_tokens=est_in, estimated_output_tokens=est_out
    )
    assert ranked[0].model_id == "calc_c"
    assert ranked[1].model_id == "calc_expensive"


def test_hard_filters_context_window_and_output_tokens():
    """Candidates exceeding context window or max output tokens must be filtered out."""
    c_small_ctx = _make_candidate(model_id="c_small", context_window=1000)
    c_large_ctx = _make_candidate(model_id="c_large", context_window=128000)

    eligible, results = filter_candidates(
        [c_small_ctx, c_large_ctx],
        routing_mode="auto",
        community_opt_in=False,
        estimated_input_tokens=800,
        requested_max_output=500,  # 800 + 500 = 1300 > 1000
        stream=True,
    )

    assert len(eligible) == 1
    assert eligible[0].model_id == "c_large"
    rejected = [r for r in results if not r.eligible]
    assert len(rejected) == 1
    assert rejected[0].candidate.model_id == "c_small"
    assert rejected[0].rejection_code == "CONTEXT_WINDOW_EXCEEDED"


def test_hard_filters_streaming_capability():
    """Streaming request must reject candidates without streaming support."""
    c_no_stream = _make_candidate(model_id="c_no_stream", supports_streaming=False)
    c_stream = _make_candidate(model_id="c_stream", supports_streaming=True)

    eligible, results = filter_candidates(
        [c_no_stream, c_stream],
        routing_mode="auto",
        community_opt_in=False,
        estimated_input_tokens=50,
        requested_max_output=100,
        stream=True,
    )

    assert len(eligible) == 1
    assert eligible[0].model_id == "c_stream"
    rejected = [r for r in results if not r.eligible]
    assert rejected[0].candidate.model_id == "c_no_stream"
    assert rejected[0].rejection_code == "CAPABILITY_UNSUPPORTED"


def test_hard_filters_missing_credentials_for_metered_cloud():
    """Cloud candidate that has no active credential must be rejected."""
    c_no_cred = _make_candidate(model_id="c_no_cred", credential_id=None)
    c_with_cred = _make_candidate(model_id="c_with_cred", credential_id=uuid4())

    eligible, results = filter_candidates(
        [c_no_cred, c_with_cred],
        routing_mode="auto",
        community_opt_in=False,
        estimated_input_tokens=50,
        requested_max_output=100,
        stream=True,
    )

    eligible_ids = {c.model_id for c in eligible}
    assert "c_no_cred" not in eligible_ids
    assert "c_with_cred" in eligible_ids

    rejected_codes = {r.candidate.model_id: r.rejection_code for r in results if not r.eligible}
    assert rejected_codes["c_no_cred"] == "NO_ELIGIBLE_CREDENTIAL"


def test_hard_filters_cooldown_active():
    """Candidate currently in cooldown must be rejected."""
    c_cooldown = _make_candidate(model_id="c_cooldown", is_cooling_down=True)
    c_active = _make_candidate(model_id="c_active", is_cooling_down=False)

    eligible, results = filter_candidates(
        [c_cooldown, c_active],
        routing_mode="auto",
        community_opt_in=False,
        estimated_input_tokens=50,
        requested_max_output=100,
        stream=True,
    )

    assert len(eligible) == 1
    assert eligible[0].model_id == "c_active"
    rejected = [r for r in results if not r.eligible]
    assert rejected[0].rejection_code == "PROVIDER_COOLDOWN_ACTIVE"


def test_hard_filters_community_candidates_always_rejected():
    """All community node candidates must be rejected under private local inference product architecture."""
    c_comm_1 = _make_candidate(
        model_id="comm_1",
        source_type="community",
        credential_id=None,
        max_concurrent_jobs=2,
        active_concurrency=2,
    )
    c_comm_2 = _make_candidate(
        model_id="comm_2",
        source_type="community",
        credential_id=None,
        max_concurrent_jobs=3,
        active_concurrency=1,
    )

    eligible, results = filter_candidates(
        [c_comm_1, c_comm_2],
        routing_mode="auto",
        community_opt_in=True,
        estimated_input_tokens=50,
        requested_max_output=100,
        stream=True,
    )

    assert len(eligible) == 0
    for r in results:
        assert not r.eligible
        assert r.rejection_code == "LOCAL_MODEL_SHARING_DISABLED"
