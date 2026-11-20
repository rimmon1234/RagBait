"""Execution engine for committed routing candidates (Cloud)."""

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.database import get_pool
from app.modules.admission.errors import AdmissionRejectionError
from app.modules.admission.ledger import mark_outcome_unknown_transactional
from app.modules.admission.schemas import AdmissionRequest, SettlementRequest
from app.modules.admission.service import (
    mark_dispatched,
    release_reservation,
    reserve_generation,
    settle_reservation,
)
from app.modules.credentials.repository import retrieve_decrypted_credential
from app.modules.llm_gateway import service as gateway_service
from app.modules.llm_gateway.interfaces import NormalizedUsage, UsageSourceType
from app.modules.llm_gateway.schemas import ChatCompletionRequest, ChatCompletionResponse
from app.modules.llm_gateway.streaming import format_sse_event
from app.modules.llm_gateway.token_estimation import TokenEstimate, estimate_text_tokens
from app.modules.routing.schemas import RoutingCandidate

logger = logging.getLogger("ragbait.routing.executor")


class PreDispatchCandidateError(Exception):
    """Exception raised when a candidate fails a pre-dispatch check and can be safely fallen back from."""

    def __init__(self, message: str, is_candidate_specific: bool = True):
        super().__init__(message)
        self.is_candidate_specific = is_candidate_specific


async def execute_cloud_candidate(
    user_id: UUID,
    candidate: RoutingCandidate,
    req: ChatCompletionRequest,
    token_estimate: TokenEstimate,
    request_id: str,
    payload_hash: str | None = None,
) -> tuple[AsyncIterator[str] | ChatCompletionResponse, bool]:
    """Execute inference on an approved cloud candidate."""
    assert candidate.credential_id is not None, "Cloud candidate must have credential_id"

    # 1. Attempt M2.4 Admission Reservation
    admission_request = AdmissionRequest(
        user_id=user_id,
        credential_id=candidate.credential_id,
        provider_id=candidate.provider_id,
        model_id=candidate.model_id,
        estimated_input_tokens=token_estimate.estimated_input_tokens,
        max_output_tokens=token_estimate.reserved_output_tokens,
        idempotency_key=req.idempotency_key,
        request_id=request_id,
    )

    try:
        reservation = await reserve_generation(admission_request)
        if payload_hash:
            pool = get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE public.llm_usage_reservations
                    SET pricing_snapshot = pricing_snapshot || jsonb_build_object('payload_hash', $2::text)
                    WHERE id = $1
                    """,
                    reservation.reservation_id,
                    payload_hash,
                )
    except AdmissionRejectionError as are:
        # Check if candidate-specific (e.g. provider cooldown or provider quota) vs user-wide
        if are.category in ("provider_cooldown", "rate_limited", "quota_exhausted"):
            logger.info(
                f"Candidate-specific admission rejection for {candidate.provider_id}: {are}"
            )
            raise PreDispatchCandidateError(str(are), is_candidate_specific=True) from are
        # User-wide limit (e.g. user budget cap, user RPM) cannot be bypassed by another candidate
        raise are
    except Exception as exc:
        err_msg = str(exc)
        is_cand_specific = "credential" in err_msg.lower() or "provider" in err_msg.lower()
        raise PreDispatchCandidateError(
            f"Admission reservation failed: {exc}", is_candidate_specific=is_cand_specific
        ) from exc

    # 2. Just-In-Time Vault Credential Decryption
    try:
        decrypted = await retrieve_decrypted_credential(
            credential_id=candidate.credential_id,
            caller_id=candidate.credential_owner_id or user_id,
            check_active=True,
        )
        raw_key = decrypted.raw_key
    except Exception as exc:
        await release_reservation(reservation.reservation_id, reason="vault_decryption_failed")
        raise PreDispatchCandidateError(
            f"Vault decryption failed for {candidate.provider_id}: {exc}",
            is_candidate_specific=True,
        ) from exc

    # 3. Durable Dispatch Commitment (POST-DISPATCH: NO FALLBACK AFTER THIS POINT!)
    await mark_dispatched(reservation.reservation_id)

    # 4. Adapter execution
    adapter = gateway_service._get_provider_adapter(candidate.provider_id)
    messages_payload = [{"role": m.role, "content": m.content} for m in req.messages]

    if req.stream:

        async def stream_generator() -> AsyncIterator[str]:
            yield format_sse_event(
                "generation_started",
                {
                    "request_id": request_id,
                    "reservation_id": str(reservation.reservation_id),
                    "model_id": candidate.model_id,
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )

            collected_text = ""
            last_finish_reason = "stop"
            usage_received: NormalizedUsage | None = None
            resolved_model = candidate.model_id
            upstream_gen_id: str | None = None

            try:
                async for chunk in adapter.stream(
                    model_id=candidate.model_id,
                    messages=messages_payload,
                    raw_key=raw_key,
                    temperature=req.params.temperature,
                    max_output_tokens=token_estimate.reserved_output_tokens,
                    top_p=req.params.top_p,
                ):
                    if chunk.resolved_model:
                        resolved_model = chunk.resolved_model
                    if chunk.upstream_generation_id:
                        upstream_gen_id = chunk.upstream_generation_id

                    if chunk.delta:
                        collected_text += chunk.delta
                        yield format_sse_event(
                            "text_delta",
                            {
                                "request_id": request_id,
                                "delta": chunk.delta,
                                "index": 0,
                            },
                        )

                    if chunk.finish_reason:
                        last_finish_reason = chunk.finish_reason

                    if chunk.usage:
                        usage_received = chunk.usage

                if not usage_received:
                    output_tokens = estimate_text_tokens(collected_text) if collected_text else 0
                    source: UsageSourceType = "derived" if collected_text else "unknown"
                    usage_received = NormalizedUsage(
                        input_tokens=token_estimate.estimated_input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=token_estimate.estimated_input_tokens + output_tokens,
                        cached_tokens=0,
                        reasoning_tokens=0,
                        usage_source=source,
                    )

                settlement = await settle_reservation(
                    reservation_id=reservation.reservation_id,
                    req=SettlementRequest(
                        actual_input_tokens=usage_received.input_tokens,
                        actual_output_tokens=usage_received.output_tokens,
                        actual_cost_microcents=None,
                        resolved_model_id=resolved_model,
                        upstream_provider=candidate.provider_id,
                        upstream_generation_id=upstream_gen_id,
                        usage_source=usage_received.usage_source,
                    ),
                    model_id=resolved_model,
                    resolved_model_id=resolved_model,
                    upstream_provider=candidate.provider_id,
                    upstream_generation_id=upstream_gen_id,
                    usage_source=usage_received.usage_source,
                )

                yield format_sse_event(
                    "usage",
                    {
                        "request_id": request_id,
                        "usage": usage_received.model_dump(),
                        "cost_microcents": settlement.actual_cost_microcents,
                        "cost_cents": settlement.actual_cost_cents,
                        "resolved_model_id": resolved_model,
                        "upstream_generation_id": upstream_gen_id,
                    },
                )

                yield format_sse_event(
                    "generation_completed",
                    {
                        "request_id": request_id,
                        "finish_reason": last_finish_reason,
                    },
                )

            except Exception as exc:
                logger.warning(f"Mid-stream generation error during {request_id}: {exc}")
                try:
                    await mark_outcome_unknown_transactional(
                        reservation.reservation_id, reason="mid_stream_failure"
                    )
                except Exception:
                    pass
                yield format_sse_event(
                    "generation_error",
                    {
                        "request_id": request_id,
                        "error_code": getattr(exc, "code", "GENERATION_FAILED"),
                        "message": str(exc),
                    },
                )

        return stream_generator(), True

    # Non-streaming cloud execution
    try:
        gen_result = await adapter.generate(
            model_id=candidate.model_id,
            messages=messages_payload,
            raw_key=raw_key,
            temperature=req.params.temperature,
            max_output_tokens=token_estimate.reserved_output_tokens,
            top_p=req.params.top_p,
        )
    except Exception:
        try:
            await mark_outcome_unknown_transactional(
                reservation.reservation_id, reason="non_streaming_failure"
            )
        except Exception:
            pass
        raise

    usage = gen_result.usage or NormalizedUsage(
        input_tokens=token_estimate.estimated_input_tokens,
        output_tokens=estimate_text_tokens(gen_result.content),
        total_tokens=token_estimate.estimated_input_tokens
        + estimate_text_tokens(gen_result.content),
    )

    settlement = await settle_reservation(
        reservation_id=reservation.reservation_id,
        req=SettlementRequest(
            actual_input_tokens=usage.input_tokens,
            actual_output_tokens=usage.output_tokens,
            actual_cost_microcents=None,
            resolved_model_id=gen_result.resolved_model,
            upstream_provider=candidate.provider_id,
            upstream_generation_id=gen_result.upstream_generation_id,
            usage_source=usage.usage_source,
        ),
        model_id=gen_result.resolved_model,
        resolved_model_id=gen_result.resolved_model,
        upstream_provider=candidate.provider_id,
        upstream_generation_id=gen_result.upstream_generation_id,
        usage_source=usage.usage_source,
    )

    resp = ChatCompletionResponse(
        id=f"chatcmpl-{uuid4().hex[:16]}",
        request_id=request_id,
        reservation_id=reservation.reservation_id,
        provider_id=candidate.provider_id,
        model_id=candidate.model_id,
        resolved_model_id=gen_result.resolved_model,
        upstream_provider=candidate.provider_id,
        upstream_generation_id=gen_result.upstream_generation_id,
        content=gen_result.content,
        finish_reason=gen_result.finish_reason,
        usage=usage.model_dump(),
        cost_microcents=settlement.actual_cost_microcents,
        cost_cents=settlement.actual_cost_cents,
        currency="USD",
        created_at=datetime.now(UTC),
    )
    return resp, False


async def execute_community_candidate(
    user_id: UUID,
    candidate: RoutingCandidate,
    req: ChatCompletionRequest,
    token_estimate: TokenEstimate,
    request_id: str,
) -> tuple[AsyncIterator[str] | ChatCompletionResponse, bool]:
    """Community local inference execution has been discontinued."""
    raise PreDispatchCandidateError(
        "Community local execution has been discontinued in RagBait product architecture",
        is_candidate_specific=False,
    )



