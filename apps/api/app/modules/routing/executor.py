"""Execution engine for committed routing candidates (Cloud)."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.core.database import get_pool
from app.modules.admission.errors import AdmissionRejectionError
from app.modules.admission.schemas import AdmissionRequest, AdmissionReservation, SettlementRequest
from app.modules.admission.service import (
    mark_dispatched,
    mark_outcome_unknown,
    release_reservation,
    reserve_generation,
    settle_reservation,
)
from app.modules.credentials.repository import retrieve_decrypted_credential
from app.modules.llm_gateway import service as gateway_service
from app.modules.llm_gateway.errors import ProviderAdapterError
from app.modules.llm_gateway.interfaces import (
    GatewayStreamEvent,
    NormalizedUsage,
    UsageSourceType,
)
from app.modules.llm_gateway.schemas import ChatCompletionRequest, ChatCompletionResponse
from app.modules.llm_gateway.streaming import format_sse_event
from app.modules.llm_gateway.token_estimation import TokenEstimate, estimate_text_tokens
from app.modules.routing.free_models import is_approved_free_model
from app.modules.routing.schemas import RoutingCandidate

logger = logging.getLogger("ragbait.routing.executor")


class PreDispatchCandidateError(Exception):
    """Exception raised when a candidate fails a pre-dispatch check and can be safely fallen back from."""

    def __init__(self, message: str, is_candidate_specific: bool = True):
        super().__init__(message)
        self.is_candidate_specific = is_candidate_specific


class PreFirstTokenCandidateError(Exception):
    """Raised when an upstream candidate fails before emitting any visible token (safe to failover)."""

    def __init__(self, message: str, *, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


async def _verify_shared_execution_free(
    candidate: RoutingCandidate,
    resolved_model: str,
    actual_cost_microcents: int | None,
    user_id: UUID,
) -> None:
    """Ensure that shared pool executions resolve strictly to approved free models without billing."""
    if not candidate.is_shared_credential:
        return

    is_free = False
    if resolved_model.endswith(":free"):
        is_free = True
    else:
        pool = get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT free_tier_eligible, input_cost_per_million_cents, output_cost_per_million_cents
                FROM public.llm_models
                WHERE id = $1
                """,
                resolved_model,
            )
            if (
                row
                and row["free_tier_eligible"]
                and (row["input_cost_per_million_cents"] or 0) == 0
                and (row["output_cost_per_million_cents"] or 0) == 0
            ):
                is_free = True

    if not is_free or (actual_cost_microcents or 0) > 0:
        logger.critical(
            f"SECURITY INCIDENT: Shared credential {candidate.credential_id} resolved to non-free model '{resolved_model}' or incurred unexpected cost ({actual_cost_microcents} microcents)"
        )
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE public.api_key_metadata
                SET status = 'disabled', updated_at = now()
                WHERE id = $1
                """,
                candidate.credential_id,
            )
            await conn.execute(
                """
                INSERT INTO public.audit_logs (actor_id, action, target_type, target_id, payload)
                VALUES ($1, 'shared_pool.unexpected_billable_usage', 'api_key_metadata', $2, $3::jsonb)
                """,
                user_id,
                candidate.credential_id,
                json.dumps(
                    {
                        "resolved_model": resolved_model,
                        "cost_microcents": actual_cost_microcents,
                        "provider_id": candidate.provider_id,
                    }
                ),
            )
        raise RuntimeError(
            f"Shared inference execution violation: resolved model '{resolved_model}' is not an approved free-tier model or incurred cost"
        )


async def _prepare_cloud_dispatch(
    user_id: UUID,
    candidate: RoutingCandidate,
    req: ChatCompletionRequest,
    token_estimate: TokenEstimate,
    request_id: str,
    payload_hash: str | None = None,
) -> tuple[AdmissionReservation, str, Any, list[dict[str, str]]]:
    """Run shared free-model validation, admission reservation, decryption, and dispatch commit."""
    assert candidate.credential_id is not None, "Cloud candidate must have credential_id"

    if candidate.is_shared_credential and not is_approved_free_model(
        candidate.provider_id, candidate.model_id
    ):
        raise PreDispatchCandidateError(
            f"Community-shared credential for provider '{candidate.provider_id}' "
            f"cannot be dispatched with non-free model '{candidate.model_id}'.",
            is_candidate_specific=False,
        )

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
        if reservation.status not in ("reserved", "dispatched"):
            raise PreDispatchCandidateError(
                f"Reservation '{reservation.reservation_id}' cannot be dispatched from status '{reservation.status}'.",
                is_candidate_specific=True,
            )
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
        if are.category in ("provider_cooldown", "rate_limited", "quota_exhausted"):
            logger.info(
                f"Candidate-specific admission rejection for {candidate.provider_id}: {are}"
            )
            raise PreDispatchCandidateError(str(are), is_candidate_specific=True) from are
        raise are
    except Exception as exc:
        err_msg = str(exc)
        is_cand_specific = "credential" in err_msg.lower() or "provider" in err_msg.lower()
        raise PreDispatchCandidateError(
            f"Admission reservation failed: {exc}", is_candidate_specific=is_cand_specific
        ) from exc

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

    await mark_dispatched(reservation.reservation_id)
    adapter = gateway_service._get_provider_adapter(candidate.provider_id)
    messages_payload = [{"role": m.role, "content": m.content} for m in req.messages]
    return reservation, raw_key, adapter, messages_payload


async def stream_cloud_candidate_events(
    user_id: UUID,
    candidate: RoutingCandidate,
    req: ChatCompletionRequest,
    token_estimate: TokenEstimate,
    request_id: str,
    payload_hash: str | None = None,
    *,
    cancel_check: Callable[[], bool] | None = None,
    first_token_timeout_seconds: float | None = None,
    idle_timeout_seconds: float | None = None,
) -> AsyncIterator[GatewayStreamEvent]:
    """Stream typed GatewayStreamEvents through existing gateway adapters and admission ledger."""
    from app.modules.admission.cooldown import set_cooldown as admission_set_cooldown

    reservation, raw_key, adapter, messages_payload = await _prepare_cloud_dispatch(
        user_id=user_id,
        candidate=candidate,
        req=req,
        token_estimate=token_estimate,
        request_id=request_id,
        payload_hash=payload_hash,
    )

    collected_text = ""
    last_finish_reason = "stop"
    usage_received: NormalizedUsage | None = None
    resolved_model = candidate.model_id
    upstream_gen_id: str | None = None
    first_delta_emitted = False
    finalized = False

    adapter_stream = adapter.stream(
        model_id=candidate.model_id,
        messages=messages_payload,
        raw_key=raw_key,
        temperature=req.params.temperature,
        max_output_tokens=token_estimate.reserved_output_tokens,
        top_p=req.params.top_p,
    )

    try:
        while True:
            if cancel_check is not None and cancel_check():
                raise asyncio.CancelledError("Generation cancelled before upstream completion")

            timeout_val = (
                idle_timeout_seconds if first_delta_emitted else first_token_timeout_seconds
            )
            try:
                if timeout_val and timeout_val > 0:
                    chunk = await asyncio.wait_for(adapter_stream.__anext__(), timeout=timeout_val)
                else:
                    chunk = await adapter_stream.__anext__()
            except StopAsyncIteration:
                break
            except TimeoutError as te:
                raise TimeoutError("Upstream provider stream timed out waiting for tokens") from te

            if chunk.resolved_model:
                resolved_model = chunk.resolved_model
            if chunk.upstream_generation_id:
                upstream_gen_id = chunk.upstream_generation_id
            if chunk.finish_reason:
                last_finish_reason = chunk.finish_reason
            if chunk.usage:
                usage_received = chunk.usage

            if chunk.delta:
                if not first_delta_emitted:
                    first_delta_emitted = True
                    yield GatewayStreamEvent(
                        event_type="started",
                        reservation_id=reservation.reservation_id,
                        resolved_model=resolved_model,
                        provider_id=candidate.provider_id,
                        upstream_generation_id=upstream_gen_id,
                    )
                collected_text += chunk.delta
                yield GatewayStreamEvent(
                    event_type="delta",
                    delta=chunk.delta,
                    resolved_model=resolved_model,
                    provider_id=candidate.provider_id,
                    upstream_generation_id=upstream_gen_id,
                    reservation_id=reservation.reservation_id,
                )

        if not first_delta_emitted or not collected_text.strip():
            raise ProviderAdapterError(
                provider_id=candidate.provider_id,
                category="malformed_upstream_response",
                message="Upstream provider stream completed with empty text output",
                status_code=502,
            )

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

        if candidate.is_shared_credential:
            await _verify_shared_execution_free(
                candidate=candidate,
                resolved_model=resolved_model,
                actual_cost_microcents=None,
                user_id=user_id,
            )

        actual_cost_micro = (
            0 if (resolved_model.endswith(":free") or candidate.is_shared_credential) else None
        )
        settlement = await settle_reservation(
            reservation_id=reservation.reservation_id,
            req=SettlementRequest(
                actual_input_tokens=usage_received.input_tokens,
                actual_output_tokens=usage_received.output_tokens,
                actual_cost_microcents=actual_cost_micro,
                resolved_model_id=resolved_model,
                upstream_provider=candidate.provider_id,
                upstream_generation_id=upstream_gen_id,
                usage_source=usage_received.usage_source,
            ),
            model_id=candidate.model_id,
            resolved_model_id=resolved_model,
            upstream_provider=candidate.provider_id,
            upstream_generation_id=upstream_gen_id,
            usage_source=usage_received.usage_source,
        )
        finalized = True

        yield GatewayStreamEvent(
            event_type="completed",
            finish_reason=last_finish_reason,
            usage=usage_received,
            resolved_model=resolved_model,
            provider_id=candidate.provider_id,
            upstream_generation_id=upstream_gen_id,
            reservation_id=reservation.reservation_id,
            cost_microcents=settlement.actual_cost_microcents,
            cost_cents=settlement.actual_cost_cents,
        )

    except asyncio.CancelledError:
        if not finalized:
            finalized = True
            partial_out = estimate_text_tokens(collected_text) if collected_text else 0
            partial_src: UsageSourceType = "derived" if collected_text else "unknown"
            with suppress(Exception):
                await settle_reservation(
                    reservation_id=reservation.reservation_id,
                    req=SettlementRequest(
                        actual_input_tokens=token_estimate.estimated_input_tokens
                        if collected_text
                        else 0,
                        actual_output_tokens=partial_out,
                        actual_cost_microcents=None,
                        error_category="cancelled_by_client",
                        resolved_model_id=resolved_model,
                        upstream_provider=candidate.provider_id,
                        upstream_generation_id=upstream_gen_id,
                        usage_source=partial_src,
                    ),
                    model_id=candidate.model_id,
                    resolved_model_id=resolved_model,
                    upstream_provider=candidate.provider_id,
                    upstream_generation_id=upstream_gen_id,
                    usage_source=partial_src,
                )
        yield GatewayStreamEvent(
            event_type="cancelled",
            finish_reason="cancelled",
            resolved_model=resolved_model,
            provider_id=candidate.provider_id,
            reservation_id=reservation.reservation_id,
        )
    except Exception as exc:
        retry_after = getattr(exc, "retry_after", None)
        err_category = getattr(
            exc,
            "category",
            "upstream_timeout" if isinstance(exc, TimeoutError) else "provider_stream_failed",
        )
        if retry_after:
            with suppress(Exception):
                await admission_set_cooldown(
                    scope_type="provider",
                    scope_id=candidate.provider_id,
                    retry_after_seconds=retry_after,
                    reason=f"Provider {candidate.provider_id} returned HTTP 429 during stream",
                )
        if not finalized:
            finalized = True
            with suppress(Exception):
                await mark_outcome_unknown(
                    reservation.reservation_id,
                    reason=err_category,
                )

        if not first_delta_emitted and err_category in (
            "rate_limited",
            "quota_exceeded",
            "provider_unavailable",
            "upstream_timeout",
            "model_unavailable",
            "malformed_upstream_response",
        ):
            raise PreFirstTokenCandidateError(
                f"Candidate {candidate.provider_id}:{candidate.model_id} failed before first token: {exc}",
                retry_after=retry_after,
            ) from exc

        yield GatewayStreamEvent(
            event_type="error",
            error_code=getattr(exc, "code", "PROVIDER_STREAM_FAILED"),
            error_message="Generation could not be completed due to an upstream error.",
            resolved_model=resolved_model,
            provider_id=candidate.provider_id,
            reservation_id=reservation.reservation_id,
            retryable=err_category in ("rate_limited", "provider_unavailable", "upstream_timeout"),
        )
    finally:
        with suppress(Exception):
            await adapter_stream.aclose()
        if not finalized:
            with suppress(Exception):
                await mark_outcome_unknown(reservation.reservation_id, reason="stream_aborted")


async def execute_cloud_candidate(
    user_id: UUID,
    candidate: RoutingCandidate,
    req: ChatCompletionRequest,
    token_estimate: TokenEstimate,
    request_id: str,
    payload_hash: str | None = None,
) -> tuple[AsyncIterator[str] | ChatCompletionResponse, bool]:
    """Execute inference on an approved cloud candidate."""
    reservation, raw_key, adapter, messages_payload = await _prepare_cloud_dispatch(
        user_id=user_id,
        candidate=candidate,
        req=req,
        token_estimate=token_estimate,
        request_id=request_id,
        payload_hash=payload_hash,
    )

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
            is_settled = False

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

                # Post-response pre-settlement check: shared execution must strictly resolve to approved free-tier model
                if candidate.is_shared_credential:
                    await _verify_shared_execution_free(
                        candidate=candidate,
                        resolved_model=resolved_model,
                        actual_cost_microcents=None,
                        user_id=user_id,
                    )

                actual_cost_micro = 0 if (resolved_model.endswith(":free") or candidate.is_shared_credential) else None
                settlement = await settle_reservation(
                    reservation_id=reservation.reservation_id,
                    req=SettlementRequest(
                        actual_input_tokens=usage_received.input_tokens,
                        actual_output_tokens=usage_received.output_tokens,
                        actual_cost_microcents=actual_cost_micro,
                        resolved_model_id=resolved_model,
                        upstream_provider=candidate.provider_id,
                        upstream_generation_id=upstream_gen_id,
                        usage_source=usage_received.usage_source,
                    ),
                    model_id=candidate.model_id,
                    resolved_model_id=resolved_model,
                    upstream_provider=candidate.provider_id,
                    upstream_generation_id=upstream_gen_id,
                    usage_source=usage_received.usage_source,
                )
                is_settled = True

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

                # Post-response invariant: shared execution must strictly resolve to approved free-tier model
                await _verify_shared_execution_free(
                    candidate=candidate,
                    resolved_model=resolved_model,
                    actual_cost_microcents=settlement.actual_cost_microcents,
                    user_id=user_id,
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
                if not is_settled:
                    try:
                        await mark_outcome_unknown(
                            reservation.reservation_id, reason="mid_stream_failure"
                        )
                        is_settled = True
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
            finally:
                if not is_settled:
                    try:
                        await mark_outcome_unknown(
                            reservation.reservation_id, reason="stream_cancelled_or_aborted"
                        )
                        is_settled = True
                    except Exception:
                        pass

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
            await mark_outcome_unknown(
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

    # Post-response pre-settlement check: shared execution must strictly resolve to approved free-tier model
    if candidate.is_shared_credential:
        await _verify_shared_execution_free(
            candidate=candidate,
            resolved_model=gen_result.resolved_model,
            actual_cost_microcents=None,
            user_id=user_id,
        )

    actual_cost_micro = 0 if (gen_result.resolved_model.endswith(":free") or candidate.is_shared_credential) else None
    settlement = await settle_reservation(
        reservation_id=reservation.reservation_id,
        req=SettlementRequest(
            actual_input_tokens=usage.input_tokens,
            actual_output_tokens=usage.output_tokens,
            actual_cost_microcents=actual_cost_micro,
            resolved_model_id=gen_result.resolved_model,
            upstream_provider=candidate.provider_id,
            upstream_generation_id=gen_result.upstream_generation_id,
            usage_source=usage.usage_source,
        ),
        model_id=candidate.model_id,
        resolved_model_id=gen_result.resolved_model,
        upstream_provider=candidate.provider_id,
        upstream_generation_id=gen_result.upstream_generation_id,
        usage_source=usage.usage_source,
    )

    # Post-response invariant: shared execution must strictly resolve to approved free-tier model
    await _verify_shared_execution_free(
        candidate=candidate,
        resolved_model=gen_result.resolved_model,
        actual_cost_microcents=settlement.actual_cost_microcents,
        user_id=user_id,
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



