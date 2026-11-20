"""Unified Intelligent Routing Service (M2.9)."""

import hashlib
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.core.config import settings
from app.core.database import get_pool
from app.core.errors import AuthorizationError, BadRequestError, ConflictError, NotFoundError
from app.modules.llm_gateway.schemas import ChatCompletionRequest, ChatCompletionResponse
from app.modules.llm_gateway.service import get_model, get_provider
from app.modules.llm_gateway.token_estimation import estimate_generation_tokens
from app.modules.routing.discovery import discover_all_candidates
from app.modules.routing.executor import (
    PreDispatchCandidateError,
    execute_cloud_candidate,
    execute_community_candidate,
)
from app.modules.routing.policy import filter_candidates, rank_candidates
from app.modules.routing.schemas import (
    RouteDecision,
    RoutePreviewResponse,
    RoutingCandidate,
    RoutingMode,
)

logger = logging.getLogger("ragbait.routing.service")

# Server-controlled default models when client omits model selection
DEFAULT_CLOUD_MODEL = "gemini-3.8-flash"
DEFAULT_COMMUNITY_MODEL = "llama3.2:3b"


def compute_request_payload_hash(req: ChatCompletionRequest) -> str:
    """Compute deterministic SHA-256 hash of the inference request payload for idempotency verification."""
    data = {
        "routing_mode": req.routing_mode,
        "provider_id": req.provider_id,
        "model_id": req.model_id,
        "community_opt_in": req.community_opt_in,
        "messages": [{"role": m.role, "content": m.content} for m in req.messages],
        "params": req.params.model_dump(),
        "stream": req.stream,
    }
    canonical_json = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


async def check_user_idempotency(
    user_id: UUID,
    idempotency_key: str,
    payload_hash: str,
) -> dict[str, Any] | None:
    """Check if an idempotency key has already been submitted by this user.

    Raises:
        ConflictError if key was reused with a different payload hash.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, user_id, idempotency_key, request_id, status, pricing_snapshot, created_at
            FROM public.llm_usage_reservations
            WHERE user_id = $1 AND idempotency_key = $2
            """,
            user_id,
            idempotency_key,
        )
        if not row:
            return None

        snapshot = row["pricing_snapshot"] or {}
        if isinstance(snapshot, str):
            try:
                snapshot = json.loads(snapshot)
            except Exception:
                snapshot = {}
        stored_hash = snapshot.get("payload_hash")
        if stored_hash and stored_hash != payload_hash:
            raise ConflictError(
                f"Idempotency key '{idempotency_key}' was previously used with different request parameters"
            )

        return dict(row)


async def route_and_dispatch(
    user_id: UUID,
    req: ChatCompletionRequest,
    correlation_id: str | None = None,
) -> tuple[AsyncIterator[str] | ChatCompletionResponse, bool, RouteDecision]:
    """Execute unified inference through deterministic routing and admission control.

    Returns:
        (stream_or_response, is_streaming, route_decision)
    """
    routing_mode = req.routing_mode
    request_id = correlation_id or f"req-{uuid4().hex[:12]}"
    payload_hash = compute_request_payload_hash(req)

    # 1. User-scoped Idempotency Verification
    await check_user_idempotency(user_id, req.idempotency_key, payload_hash)

    # 2. Resolve target model preference
    preferred_default = DEFAULT_COMMUNITY_MODEL if routing_mode == "community_only" else DEFAULT_CLOUD_MODEL
    target_model_id = req.model_id

    # 3. Community Consent Verification
    if routing_mode == "community_only" and not req.community_opt_in:
        raise BadRequestError(
            "Explicit requester consent (community_opt_in: true) is required for community_only routing"
        )
    if routing_mode in ("auto", "community_only") and req.community_opt_in:
        if req.community_consent_version != settings.COMMUNITY_CONSENT_POLICY_VERSION:
            raise BadRequestError(
                f"Community consent policy version mismatch. Expected '{settings.COMMUNITY_CONSENT_POLICY_VERSION}'"
            )

    # 4. Discover Candidate Pool
    candidates = await discover_all_candidates(
        user_id=user_id,
        routing_mode=routing_mode,
        community_opt_in=req.community_opt_in,
        requested_provider_id=req.provider_id,
        requested_model_id=target_model_id,
        explicit_credential_id=req.credential_id,
    )

    if not candidates:
        if routing_mode == "community_only":
            raise ConflictError(
                f"No online community node is currently sharing model '{target_model_id or preferred_default}'. Please check back later."
            )
        if routing_mode == "provider_specific":
            raise NotFoundError(
                f"No eligible candidate found for requested provider '{req.provider_id}' and model '{req.model_id}'"
            )
        raise ConflictError("No candidates available matching the requested criteria")

    # 5. Estimate Tokens for Context Checks
    messages_payload = [{"role": m.role, "content": m.content} for m in req.messages]
    # Use standard baseline context limit for estimation
    token_estimate = estimate_generation_tokens(
        messages=messages_payload,
        model_context_window=131072,
        model_max_output_tokens=8192,
        requested_max_output=req.params.max_output_tokens,
    )

    # 6. Apply Hard Filters
    eligible, filter_results = filter_candidates(
        candidates=candidates,
        routing_mode=routing_mode,
        community_opt_in=req.community_opt_in,
        estimated_input_tokens=token_estimate.estimated_input_tokens,
        requested_max_output=token_estimate.reserved_output_tokens,
        stream=req.stream,
        requested_model_id=target_model_id,
    )

    if not eligible:
        # Formulate descriptive error based on filter rejection reasons
        rejection_reasons = {r.rejection_code: r.rejection_reason for r in filter_results if not r.eligible}
        if "NO_ELIGIBLE_CREDENTIAL" in rejection_reasons:
            raise NotFoundError(
                f"No active, authorized API credential available for provider '{req.provider_id or 'cloud'}'. "
                "Please configure an API key in Credential Management."
            )
        if "COMMUNITY_CONCURRENCY_EXHAUSTED" in rejection_reasons:
            raise ConflictError("Community nodes are currently at peak capacity. Please retry shortly.")
        if "PROVIDER_COOLDOWN_ACTIVE" in rejection_reasons:
            raise ConflictError("Requested provider is temporarily cooling down due to upstream rate limits.")
        raise ConflictError(
            f"No eligible inference sources available. Rejections: {list(rejection_reasons.keys())}"
        )

    # 7. Deterministic Ranking
    ranked = rank_candidates(
        eligible_candidates=eligible,
        estimated_input_tokens=token_estimate.estimated_input_tokens,
        estimated_output_tokens=token_estimate.reserved_output_tokens,
        requested_model_id=target_model_id or preferred_default,
    )

    # 8. Safe Pre-Dispatch Fallback Execution Loop
    last_error: Exception | None = None
    for attempt_idx, candidate in enumerate(ranked):
        decision_reason = "exact_model_match" if candidate.model_id == target_model_id else "deterministic_ranking"
        if candidate.free_tier_eligible and (candidate.pricing_input_cents_per_million or 0) == 0:
            decision_reason = "free_tier_priority"

        decision = RouteDecision(
            routing_mode=routing_mode,
            selected_source=candidate.source_type,
            provider_id=candidate.provider_id,
            model_id=candidate.model_id,
            resolved_model_id=candidate.model_id,
            credential_id=candidate.credential_id,
            node_id=candidate.node_id,
            decision_reason=decision_reason,
            attempt_count=attempt_idx + 1,
            created_at=datetime.now(UTC),
        )

        try:
            if candidate.source_type == "cloud":
                result, is_streaming = await execute_cloud_candidate(
                    user_id=user_id,
                    candidate=candidate,
                    req=req,
                    token_estimate=token_estimate,
                    request_id=request_id,
                    payload_hash=payload_hash,
                )
                return result, is_streaming, decision
            elif candidate.source_type == "community":
                result, is_streaming = await execute_community_candidate(
                    user_id=user_id,
                    candidate=candidate,
                    req=req,
                    token_estimate=token_estimate,
                    request_id=request_id,
                )
                return result, is_streaming, decision

        except PreDispatchCandidateError as pe:
            # Candidate-specific pre-dispatch failure: safe to try next eligible candidate!
            logger.warning(
                f"Candidate {candidate.provider_id}:{candidate.model_id} failed pre-dispatch: {pe}. "
                f"Evaluating next candidate ({attempt_idx + 1}/{len(ranked)})."
            )
            last_error = pe
            if not pe.is_candidate_specific:
                # Infrastructure or non-candidate error: halt immediately with ConflictError
                raise ConflictError(f"Inference dispatch halted: {pe}")
            continue

    if last_error:
        raise ConflictError(f"All eligible inference candidates failed pre-dispatch admission: {last_error}")
    raise ConflictError("No candidate could be executed")


async def preview_route(
    user_id: UUID,
    req: ChatCompletionRequest,
) -> RoutePreviewResponse:
    """Preview prospective route selection without reserving admission or executing."""
    routing_mode = req.routing_mode
    preferred_default = DEFAULT_COMMUNITY_MODEL if routing_mode == "community_only" else DEFAULT_CLOUD_MODEL
    target_model_id = req.model_id

    candidates = await discover_all_candidates(
        user_id=user_id,
        routing_mode=routing_mode,
        community_opt_in=req.community_opt_in,
        requested_provider_id=req.provider_id,
        requested_model_id=target_model_id,
        explicit_credential_id=req.credential_id,
    )

    messages_payload = [{"role": m.role, "content": m.content} for m in req.messages]
    token_estimate = estimate_generation_tokens(
        messages=messages_payload,
        model_context_window=131072,
        model_max_output_tokens=8192,
        requested_max_output=req.params.max_output_tokens,
    )

    eligible, filter_results = filter_candidates(
        candidates=candidates,
        routing_mode=routing_mode,
        community_opt_in=req.community_opt_in,
        estimated_input_tokens=token_estimate.estimated_input_tokens,
        requested_max_output=token_estimate.reserved_output_tokens,
        stream=req.stream,
        requested_model_id=target_model_id,
    )

    ranked = rank_candidates(
        eligible_candidates=eligible,
        estimated_input_tokens=token_estimate.estimated_input_tokens,
        estimated_output_tokens=token_estimate.reserved_output_tokens,
        requested_model_id=target_model_id or preferred_default,
    )

    selected = ranked[0] if ranked else None
    reason = "exact_model_match" if (selected and selected.model_id == target_model_id) else "deterministic_ranking"
    if selected and selected.free_tier_eligible and (selected.pricing_input_cents_per_million or 0) == 0:
        reason = "free_tier_priority"

    summary: dict[str, int] = {}
    for r in filter_results:
        if not r.eligible and r.rejection_code:
            summary[r.rejection_code] = summary.get(r.rejection_code, 0) + 1

    return RoutePreviewResponse(
        routing_mode=routing_mode,
        selected_candidate=selected,
        candidates_considered=len(candidates),
        candidates_eligible=len(eligible),
        decision_reason=reason if selected else "no_eligible_candidate",
        rejected_reasons_summary=summary,
    )
