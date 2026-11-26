"""Deterministic ranking and hard eligibility filtering policy for LLM routing (M2.9)."""

import logging
from typing import Any

from app.modules.routing.free_models import is_approved_free_model
from app.modules.routing.schemas import (
    CandidateFilterResult,
    RoutingCandidate,
    RoutingMode,
)

logger = logging.getLogger("ragbait.routing.policy")


def filter_candidates(
    candidates: list[RoutingCandidate],
    routing_mode: RoutingMode,
    community_opt_in: bool,
    estimated_input_tokens: int,
    requested_max_output: int,
    stream: bool = True,
    requested_model_id: str | None = None,
) -> tuple[list[RoutingCandidate], list[CandidateFilterResult]]:
    """Apply strict, deterministic hard filters to candidate pool.

    Returns:
        (eligible_candidates, filter_results)
    """
    eligible: list[RoutingCandidate] = []
    results: list[CandidateFilterResult] = []

    for c in candidates:
        # 1. Mode & Source constraints
        if c.source_type == "community":
            results.append(
                CandidateFilterResult(
                    candidate=c,
                    eligible=False,
                    rejection_code="LOCAL_MODEL_SHARING_DISABLED",
                    rejection_reason="Local model sharing has been disabled; community local nodes are not eligible",
                )
            )
            continue

        if routing_mode == "community_only":
            results.append(
                CandidateFilterResult(
                    candidate=c,
                    eligible=False,
                    rejection_code="COMMUNITY_LOCAL_INFERENCE_DISCONTINUED",
                    rejection_reason="Community local inference has been discontinued",
                )
            )
            continue

        if routing_mode == "cloud_only" and c.source_type != "cloud":
            results.append(
                CandidateFilterResult(
                    candidate=c,
                    eligible=False,
                    rejection_code="MODE_SOURCE_MISMATCH",
                    rejection_reason="Candidate is community source but mode is cloud_only",
                )
            )
            continue

        # 2. Strict Free-Model boundary for community-shared credentials
        if c.is_shared_credential and not is_approved_free_model(c.provider_id, c.model_id):
            results.append(
                CandidateFilterResult(
                    candidate=c,
                    eligible=False,
                    rejection_code="PAID_MODEL_COMMUNITY_DISALLOWED",
                    rejection_reason=(
                        f"Community-shared credential for provider '{c.provider_id}' "
                        f"cannot be dispatched with non-free model '{c.model_id}'"
                    ),
                )
            )
            continue

        # 3. Model matching if specific model requested
        if requested_model_id:
            # Match exact model_id or dynamic alias
            model_matches = (
                c.model_id == requested_model_id
                or (c.is_routing_alias and c.model_id == requested_model_id)
                or (c.alias_target == requested_model_id)
            )
            if not model_matches:
                results.append(
                    CandidateFilterResult(
                        candidate=c,
                        eligible=False,
                        rejection_code="MODEL_MISMATCH",
                        rejection_reason=f"Model '{c.model_id}' does not match requested model '{requested_model_id}'",
                    )
                )
                continue

        # 3. Capability verification
        if stream and "streaming" not in c.capabilities:
            results.append(
                CandidateFilterResult(
                    candidate=c,
                    eligible=False,
                    rejection_code="CAPABILITY_UNSUPPORTED",
                    rejection_reason=f"Model '{c.model_id}' does not support requested streaming capability",
                )
            )
            continue

        # 4. Context window check
        total_tokens_needed = estimated_input_tokens + requested_max_output
        if total_tokens_needed > c.context_window:
            results.append(
                CandidateFilterResult(
                    candidate=c,
                    eligible=False,
                    rejection_code="CONTEXT_WINDOW_EXCEEDED",
                    rejection_reason=(
                        f"Estimated tokens ({total_tokens_needed}) exceed context window ({c.context_window})"
                    ),
                )
            )
            continue

        # 5. Output limit check
        if requested_max_output > c.max_output_tokens:
            results.append(
                CandidateFilterResult(
                    candidate=c,
                    eligible=False,
                    rejection_code="OUTPUT_LIMIT_EXCEEDED",
                    rejection_reason=(
                        f"Requested output tokens ({requested_max_output}) exceed maximum allowed ({c.max_output_tokens})"
                    ),
                )
            )
            continue

        # 6. Source-specific checks
        if c.source_type == "cloud":
            if not c.credential_id:
                results.append(
                    CandidateFilterResult(
                        candidate=c,
                        eligible=False,
                        rejection_code="NO_ELIGIBLE_CREDENTIAL",
                        rejection_reason=f"No active, authorized credential available for cloud provider '{c.provider_id}'",
                    )
                )
                continue

            if c.is_cooling_down:
                results.append(
                    CandidateFilterResult(
                        candidate=c,
                        eligible=False,
                        rejection_code="PROVIDER_COOLDOWN_ACTIVE",
                        rejection_reason=(
                            f"Provider '{c.provider_id}' is currently in cooldown ({c.cooldown_remaining_seconds}s remaining)"
                        ),
                    )
                )
                continue

            # Shared pool free-tier and cost invariants (RagBait mandatory policy)
            if c.is_shared_credential:
                if c.credential_billing_tier and c.credential_billing_tier != "verified_free_only":
                    results.append(
                        CandidateFilterResult(
                            candidate=c,
                            eligible=False,
                            rejection_code="SHARED_POOL_UNVERIFIED_BILLING",
                            rejection_reason="Shared pool credential billing tier is not verified as free-only",
                        )
                    )
                    continue
                if not c.free_tier_eligible:
                    results.append(
                        CandidateFilterResult(
                            candidate=c,
                            eligible=False,
                            rejection_code="SHARED_POOL_FREE_TIER_ONLY",
                            rejection_reason="Shared pool credentials can only be used for free-tier eligible models; paid shared usage is prohibited",
                        )
                    )
                    continue
                if (c.pricing_input_cents_per_million or 0) > 0 or (
                    c.pricing_output_cents_per_million or 0
                ) > 0:
                    results.append(
                        CandidateFilterResult(
                            candidate=c,
                            eligible=False,
                            rejection_code="SHARED_POOL_ZERO_COST_ONLY",
                            rejection_reason="Shared pool credentials cannot incur metered cloud charges",
                        )
                    )
                    continue

        elif c.source_type == "community":
            if c.active_concurrency >= c.max_concurrent_jobs:
                results.append(
                    CandidateFilterResult(
                        candidate=c,
                        eligible=False,
                        rejection_code="COMMUNITY_CONCURRENCY_EXHAUSTED",
                        rejection_reason=(
                            f"Community node '{c.node_display_name}' has reached capacity ({c.active_concurrency}/{c.max_concurrent_jobs})"
                        ),
                    )
                )
                continue

        # Passed all hard filters
        eligible.append(c)
        results.append(
            CandidateFilterResult(
                candidate=c,
                eligible=True,
            )
        )

    return eligible, results


def rank_candidates(
    eligible_candidates: list[RoutingCandidate],
    estimated_input_tokens: int,
    estimated_output_tokens: int,
    requested_model_id: str | None = None,
) -> list[RoutingCandidate]:
    """Sort eligible candidates using a deterministic, multi-tier ranking comparator.

    Lower score tuple ranks higher (0 is best).
    """

    def scoring_key(c: RoutingCandidate) -> tuple[Any, ...]:
        # Tier 1: User Request Alignment (0 = exact match, 1 = family match, 2 = other)
        pref_tier = 2
        if requested_model_id:
            if c.model_id == requested_model_id or (
                c.is_routing_alias and c.model_id == requested_model_id
            ):
                pref_tier = 0
            elif (
                requested_model_id.lower() in c.family.lower()
                or c.family.lower() in requested_model_id.lower()
            ):
                pref_tier = 1

        # Tier 2: Cost & Source Priority Preference
        # 0: Free Tier Cloud model (verified zero cost, e.g. openrouter/free)
        # 1: Community local inference node (volunteer peer compute, avoids cloud API charges)
        # 2: User-owned paid cloud credential (spends user's own budget before community resources)
        # 3: Community-shared paid cloud credential (preserves pooled community keys)
        if (
            c.source_type == "cloud"
            and c.free_tier_eligible
            and (c.pricing_input_cents_per_million or 0) == 0
        ):
            cost_tier = 0
            microcents = 0
        elif c.source_type == "community":
            cost_tier = 1
            microcents = 0
        elif c.source_type == "cloud" and not c.is_shared_credential:
            cost_tier = 2
            if (
                c.pricing_input_cents_per_million is not None
                and c.pricing_output_cents_per_million is not None
            ):
                microcents = (
                    estimated_input_tokens * c.pricing_input_cents_per_million
                    + estimated_output_tokens * c.pricing_output_cents_per_million
                )
            else:
                microcents = 999_999_999  # Missing price ranked last among metered
        else:
            cost_tier = 3
            if (
                c.pricing_input_cents_per_million is not None
                and c.pricing_output_cents_per_million is not None
            ):
                microcents = (
                    estimated_input_tokens * c.pricing_input_cents_per_million
                    + estimated_output_tokens * c.pricing_output_cents_per_million
                )
            else:
                microcents = 999_999_999

        # Tier 3: Health & Capacity Margin
        # 0 = normal capacity, 1 = cooling down
        # For community: negative remaining concurrency slots (more free slots ranks higher)
        health_tier = 1 if c.is_cooling_down else 0
        concurrency_margin = (
            -(c.max_concurrent_jobs - c.active_concurrency) if c.source_type == "community" else 0
        )

        # Tier 4: Release Stage & Headroom
        stage_rank = {"ga": 0, "preview": 1, "experimental": 2}.get(c.release_stage, 3)
        context_headroom = -(c.context_window - (estimated_input_tokens + estimated_output_tokens))

        # Tier 5: Deterministic Stable Tie-Breaker (String sort)
        tie_breaker = (
            f"{c.provider_id}:{c.model_id}:{c.source_type}:{c.node_id or c.credential_id or ''}"
        )

        return (
            pref_tier,
            cost_tier,
            microcents,
            health_tier,
            concurrency_margin,
            stage_rank,
            context_headroom,
            tie_breaker,
        )

    return sorted(eligible_candidates, key=scoring_key)
