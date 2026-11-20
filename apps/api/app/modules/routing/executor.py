"""Execution engine for committed routing candidates (Cloud and Community)."""

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.core.config import settings
from app.core.database import get_pool
from app.core.errors import ConflictError
from app.core.redis import get_community_redis_run_id
from app.modules.admission.errors import AdmissionRejectionError
from app.modules.admission.ledger import mark_outcome_unknown_transactional
from app.modules.admission.schemas import AdmissionRequest, SettlementRequest
from app.modules.admission.service import (
    mark_dispatched,
    release_reservation,
    reserve_generation,
    settle_reservation,
)
from app.modules.community import crypto as comm_crypto
from app.modules.community import repository as comm_repo
from app.modules.community import streams as comm_streams
from app.modules.credentials.service import retrieve_decrypted_credential
from app.modules.llm_gateway import service as gateway_service
from app.modules.llm_gateway.interfaces import NormalizedUsage, UsageSourceType
from app.modules.llm_gateway.schemas import ChatCompletionRequest, ChatCompletionResponse
from app.modules.llm_gateway.service import format_sse_event
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
    """Execute inference on an approved community volunteer node."""
    assert candidate.node_id is not None, "Community candidate must have node_id"
    node_id = candidate.node_id
    hard_deadline_at = datetime.now(UTC) + timedelta(seconds=candidate.max_execution_duration)
    job_id = uuid4()

    # 1. Acquire atomic ZSET concurrency lease in Community Redis
    has_capacity = await comm_streams.acquire_node_concurrency(
        node_id=node_id,
        job_id=job_id,
        max_concurrent=candidate.max_concurrent_jobs,
        lease_expires_at=hard_deadline_at.timestamp(),
    )
    if not has_capacity:
        raise PreDispatchCandidateError(
            f"Node '{candidate.node_display_name}' has no available concurrency slots",
            is_candidate_specific=True,
        )

    # 2. Build payload & signature
    messages_payload = [{"role": m.role, "content": m.content} for m in req.messages]
    payload: dict[str, Any] = {
        "model": candidate.model_id,
        "messages": messages_payload,
        "options": {
            "temperature": req.params.temperature,
            "num_predict": token_estimate.reserved_output_tokens,
            "top_p": req.params.top_p,
        },
    }
    payload_digest = comm_crypto.compute_payload_digest(payload)
    redis_run_id = await get_community_redis_run_id()

    # 3. Create authoritative job in PostgreSQL (POST-DISPATCH: NO FALLBACK AFTER THIS POINT!)
    try:
        await comm_repo.create_job(
            requester_id=user_id,
            node_id=node_id,
            model_name=candidate.model_id,
            payload_digest=payload_digest,
            auth_key_id=settings.COMMUNITY_KEY_ID,
            consent_policy_version=req.community_consent_version
            or settings.COMMUNITY_CONSENT_POLICY_VERSION,
            hard_deadline_at=hard_deadline_at,
            job_id=job_id,
            redis_run_id=redis_run_id,
        )
    except Exception as exc:
        await comm_streams.release_node_concurrency(node_id, job_id)
        raise PreDispatchCandidateError(
            f"Failed to record community job: {exc}", is_candidate_specific=False
        ) from exc

    # 4. Sign ticket & Enqueue into dedicated volatile Redis Stream
    # (FastAPI WebSocket worker reads and forwards to Node Agent over outbound WSS)
    auth_nonce = secrets.token_hex(16)
    sig = comm_crypto.sign_job_authorization(
        job_id=job_id,
        node_id=node_id,
        model_name=candidate.model_id,
        payload_digest=payload_digest,
        max_output_tokens=token_estimate.reserved_output_tokens,
        hard_deadline_ts=int(hard_deadline_at.timestamp()),
        auth_nonce=auth_nonce,
    )
    from app.modules.community.schemas import SignedJobAuthorization

    auth = SignedJobAuthorization(
        job_id=job_id,
        node_id=node_id,
        model_name=candidate.model_id,
        payload_digest=payload_digest,
        max_output_tokens=token_estimate.reserved_output_tokens,
        hard_deadline_ts=int(hard_deadline_at.timestamp()),
        auth_nonce=auth_nonce,
        signature=sig,
        key_id=settings.COMMUNITY_KEY_ID,
    )

    await comm_streams.enqueue_job_dispatch(
        node_id=node_id, job_id=job_id, authorization=auth, payload=payload
    )

    # 5. Stream results
    if req.stream:

        async def community_stream_generator() -> AsyncIterator[str]:
            yield format_sse_event(
                "generation_started",
                {
                    "request_id": request_id,
                    "job_id": str(job_id),
                    "model_id": candidate.model_id,
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )

            current_seq = 1
            max_iterations = 600  # Up to 2 minutes
            iters = 0
            collected_text = ""

            try:
                while iters < max_iterations:
                    chunks = await comm_streams.read_job_chunks_range(job_id, start_seq=current_seq)
                    for c in chunks:
                        current_seq = c["seq_id"] + 1
                        if c["delta"]:
                            collected_text += c["delta"]
                            yield format_sse_event(
                                "text_delta",
                                {
                                    "request_id": request_id,
                                    "delta": c["delta"],
                                    "index": 0,
                                },
                            )
                        if c["done"]:
                            # Terminal frame reached
                            stats = c.get("stats") or {}
                            input_tokens = stats.get(
                                "input_tokens", token_estimate.estimated_input_tokens
                            )
                            output_tokens = stats.get(
                                "output_tokens", estimate_text_tokens(collected_text)
                            )

                            yield format_sse_event(
                                "usage",
                                {
                                    "request_id": request_id,
                                    "usage": {
                                        "input_tokens": input_tokens,
                                        "output_tokens": output_tokens,
                                        "total_tokens": input_tokens + output_tokens,
                                        "usage_source": "device_reported",
                                    },
                                    "cost_microcents": 0,
                                    "cost_cents": 0,
                                    "resolved_model_id": candidate.model_id,
                                },
                            )
                            yield format_sse_event(
                                "generation_completed",
                                {
                                    "request_id": request_id,
                                    "finish_reason": "stop",
                                },
                            )
                            return

                    # Check DB status for cancellation / failure
                    db_job = await comm_repo.get_job_by_id(job_id)
                    if db_job and db_job["status"] in (
                        "CANCELLED",
                        "FAILED",
                        "EXPIRED",
                        "OUTCOME_UNKNOWN",
                    ):
                        yield format_sse_event(
                            "generation_error",
                            {
                                "request_id": request_id,
                                "error_code": db_job["status"],
                                "message": db_job.get("failure_details")
                                or "Community execution halted",
                            },
                        )
                        return

                    await asyncio.sleep(0.2)
                    iters += 1

                # Loop timed out
                yield format_sse_event(
                    "generation_error",
                    {
                        "request_id": request_id,
                        "error_code": "COMMUNITY_NODE_TIMEOUT",
                        "message": "Community node stream timed out",
                    },
                )
            finally:
                await comm_streams.release_node_concurrency(node_id, job_id)

        return community_stream_generator(), True

    # Non-streaming community execution
    collected_text = ""
    current_seq = 1
    max_iters = 300
    iters = 0
    final_stats: dict[str, Any] = {}

    try:
        while iters < max_iters:
            chunks = await comm_streams.read_job_chunks_range(job_id, start_seq=current_seq)
            for c in chunks:
                current_seq = c["seq_id"] + 1
                if c["delta"]:
                    collected_text += c["delta"]
                if c["done"]:
                    final_stats = c.get("stats") or {}
                    break
            if final_stats or (chunks and any(c["done"] for c in chunks)):
                break

            db_job = await comm_repo.get_job_by_id(job_id)
            if db_job and db_job["status"] in ("CANCELLED", "FAILED", "EXPIRED", "OUTCOME_UNKNOWN"):
                raise ConflictError(
                    f"Community job terminated with status {db_job['status']}: {db_job.get('failure_details')}"
                )

            await asyncio.sleep(0.2)
            iters += 1
    finally:
        await comm_streams.release_node_concurrency(node_id, job_id)

    in_tokens = final_stats.get("input_tokens", token_estimate.estimated_input_tokens)
    out_tokens = final_stats.get("output_tokens", estimate_text_tokens(collected_text))

    resp = ChatCompletionResponse(
        id=f"chatcmpl-{uuid4().hex[:16]}",
        request_id=request_id,
        reservation_id=job_id,
        provider_id="ollama",
        model_id=candidate.model_id,
        resolved_model_id=candidate.model_id,
        upstream_provider="ollama",
        content=collected_text,
        finish_reason="stop",
        usage={
            "input_tokens": in_tokens,
            "output_tokens": out_tokens,
            "total_tokens": in_tokens + out_tokens,
            "usage_source": "device_reported",
        },
        cost_microcents=0,
        cost_cents=0,
        currency="USD",
        created_at=datetime.now(UTC),
    )
    return resp, False
