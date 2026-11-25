"""SSE cloud generation streaming lifecycle, admission waiting, heartbeat, and abort recovery."""

import asyncio
import json
import logging
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.core.database import get_pool
from app.core.errors import AppError, ConflictError
from app.modules.chat.citations.schemas import MessageSourceRead
from app.modules.chat.citations.source_repository import get_message_sources_with_revalidation
from app.modules.chat.citations.validator import validate_citations
from app.modules.chat.context.compaction import compact_conversation, schedule_compaction_if_needed
from app.modules.chat.context.config import context_settings
from app.modules.chat.context.context_service import ContextService
from app.modules.chat.context.schemas import PreparedContext
from app.modules.chat.orchestration.cancellation import (
    is_generation_cancelled_in_redis,
    watch_cancellation_and_disconnect,
)
from app.modules.chat.orchestration.config import generation_settings
from app.modules.chat.orchestration.generation import (
    normalize_token_usage_record,
    run_bounded_citation_repair,
    stream_grounded_cloud_generation,
)
from app.modules.chat.orchestration.persistence import (
    complete_cloud_turn_atomically,
    lookup_existing_turn_by_idempotency,
    mark_assistant_message_cancelled,
    mark_assistant_message_failed,
    reserve_turn_messages,
    verify_conversation_ready_for_cloud,
)
from app.modules.chat.orchestration.schemas import (
    ChatMessageCreateRequest,
    OrchestrationTelemetry,
)
from app.modules.chat.orchestration.semaphores import (
    CloudGenerationPermit,
    raise_for_blocked_scope,
    register_waiter,
    release_cloud_permits,
    start_permit_heartbeat,
    stop_permit_heartbeat,
    try_acquire_cloud_permits,
    unregister_waiter,
)
from app.modules.chat.schemas import MessageRead
from app.modules.llm_gateway.interfaces import GatewayStreamEvent
from app.modules.llm_gateway.streaming import format_sse_comment, format_sse_event

logger = logging.getLogger("ragbait.chat.orchestration.streaming")


@dataclass
class PreparedCloudStreamPlan:
    """Validated pre-stream execution plan returned before committing HTTP SSE headers."""

    conversation_id: UUID
    user_id: UUID
    request: ChatMessageCreateRequest
    generation_request_id: str
    user_message_id: UUID
    assistant_message_id: UUID
    user_row: dict[str, Any]
    started_monotonic: float
    permit: CloudGenerationPermit | None = None
    prepared: PreparedContext | None = None
    is_replay: bool = False
    replay_assistant_row: dict[str, Any] | None = None
    replay_sources: list[MessageSourceRead] | None = None


async def _background_compact_task(conversation_id: UUID) -> None:
    """Fire-and-forget asynchronous summary compaction wrapper with exception guard."""
    try:
        await compact_conversation(conversation_id, is_private_local=False)
    except Exception as exc:
        logger.warning(f"Background compaction task failed for {conversation_id}: {exc}")


async def _persist_cancel(
    *,
    assistant_message_id: UUID,
    partial_text: str,
    reason: str,
    generation_request_id: str,
) -> int:
    """Persist cancellation state safely using an isolated pool connection."""
    async with get_pool().acquire() as db_conn:
        return await mark_assistant_message_cancelled(
            db_conn,
            assistant_message_id=assistant_message_id,
            partial_content=partial_text,
            reason=reason,
            expected_generation_request_id=generation_request_id,
        )


async def _persist_failure(
    *,
    assistant_message_id: UUID,
    error_code: str,
    error_message: str,
    generation_request_id: str,
) -> None:
    """Persist failure state safely using an isolated pool connection."""
    async with get_pool().acquire() as db_conn:
        await mark_assistant_message_failed(
            db_conn,
            assistant_message_id=assistant_message_id,
            error_code=error_code,
            error_message=error_message,
            expected_generation_request_id=generation_request_id,
        )


def _format_sources_identified_list(
    prepared: PreparedContext | None,
    replay_sources: list[MessageSourceRead] | None,
) -> list[dict[str, Any]]:
    """Serialize safe citation source summaries for the `sources_identified` SSE event."""
    if prepared is not None:
        return [
            {
                "citation_key": ev.citation_key,
                "document_id": str(ev.document_id),
                "document_title": ev.document_title,
                "page_number_start": ev.page_number_start,
                "page_number_end": ev.page_number_end,
                "relevance_score": ev.rrf_score,
            }
            for ev in prepared.selected_evidence
        ]
    return [
        {
            "citation_key": src.citation_key,
            "document_id": str(src.document_id) if src.document_id else None,
            "document_title": src.document_title,
            "page_number_start": src.page_number_start,
            "page_number_end": src.page_number_end,
            "relevance_score": src.relevance_score,
        }
        for src in (replay_sources or [])
    ]


def _safe_error_message(exc: Exception) -> tuple[str, str, bool]:
    """Map an exception into (code, safe_user_message, retryable)."""
    if isinstance(exc, AppError):
        retryable = exc.status_code in (429, 502, 503, 504) or exc.code in (
            "RATE_LIMITED",
            "CONCURRENCY_TIMEOUT",
            "PROVIDER_UNAVAILABLE",
            "PROVIDER_STREAM_FAILED",
            "PROVIDER_RATE_LIMITED",
            "PROVIDER_QUOTA_EXCEEDED",
            "PROVIDER_UPSTREAM_TIMEOUT",
            "CITATION_VALIDATION_FAILED",
        )
        return exc.code, exc.message, retryable
    return "GENERATION_FAILED", "Generation could not be completed due to an upstream error.", True


async def prepare_cloud_sse_stream(
    *,
    user_id: UUID,
    conversation_id: UUID,
    request: ChatMessageCreateRequest,
) -> PreparedCloudStreamPlan:
    """Validate access, idempotency, semaphores, and context BEFORE committing HTTP 200 SSE."""
    started_monotonic = time.monotonic()
    gen_request_id = f"req-{uuid4().hex[:12]}"
    pool = get_pool()

    async with pool.acquire() as db_conn:
        existing_user, existing_asst = await lookup_existing_turn_by_idempotency(
            db_conn,
            conversation_id=conversation_id,
            idempotency_key=request.idempotency_key,
        )
        if existing_user:
            if existing_user["content"].strip() != request.content.strip():
                raise ConflictError(
                    f"Idempotency key '{request.idempotency_key}' already used with different content",
                    code="IDEMPOTENCY_KEY_CONFLICT",
                )
            if existing_asst and existing_asst["status"] in ("pending", "streaming"):
                raise ConflictError(
                    "Generation for this idempotency key is already in progress",
                    code="GENERATION_IN_PROGRESS",
                )
            if existing_asst and existing_asst["status"] == "completed":
                _, sources = await get_message_sources_with_revalidation(
                    db_conn,
                    message_id=existing_asst["id"],
                    user_id=user_id,
                )
                return PreparedCloudStreamPlan(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    request=request,
                    generation_request_id=str(
                        existing_asst.get("generation_request_id") or gen_request_id
                    ),
                    user_message_id=existing_user["id"],
                    assistant_message_id=existing_asst["id"],
                    user_row=existing_user,
                    started_monotonic=started_monotonic,
                    is_replay=True,
                    replay_assistant_row=existing_asst,
                    replay_sources=sources,
                )

        await verify_conversation_ready_for_cloud(
            db_conn,
            user_id=user_id,
            conversation_id=conversation_id,
        )

    # Immediate Redis semaphore admission check before DB message reservation.
    # Conversation (409), user (429), and Redis outage (503) fail fast BEFORE SSE headers.
    # Global saturation falls through to SSE HTTP 200 + `admission_waiting`.
    permit, blocked_scope, _ = await try_acquire_cloud_permits(
        conversation_id=conversation_id,
        user_id=user_id,
    )
    if permit is None and blocked_scope in ("conversation", "user"):
        raise_for_blocked_scope(
            scope=blocked_scope,
            conversation_id=conversation_id,
            conv_limit=generation_settings.GENERATION_CONVERSATION_LIMIT,
            user_limit=generation_settings.GENERATION_USER_LIMIT,
        )

    try:
        async with pool.acquire() as db_conn:
            prepared = await ContextService.prepare_grounded_context(
                user_id=user_id,
                conversation_id=conversation_id,
                user_query=request.content,
                inference_mode="cloud",
                requested_model_id=request.model_id,
                requested_provider_id=request.provider_id,
                routing_mode=request.routing_mode,
                explicit_credential_id=request.credential_id,
                requested_max_output=request.max_output_tokens,
                exclude_message_id=existing_user["id"] if existing_user else None,
                conn=db_conn,
            )

            initial_status = "streaming" if permit is not None else "pending"
            async with db_conn.transaction():
                user_row, asst_row = await reserve_turn_messages(
                    db_conn,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    user_content=request.content,
                    idempotency_key=request.idempotency_key,
                    generation_request_id=gen_request_id,
                    mode="cloud",
                    initial_status=initial_status,
                    model_id=prepared.model_capability.model_id,
                    provider_id=prepared.model_capability.provider_id,
                    extra_metadata={
                        "model_fingerprint": prepared.model_fingerprint,
                        "streaming": True,
                    },
                )

        return PreparedCloudStreamPlan(
            conversation_id=conversation_id,
            user_id=user_id,
            request=request,
            generation_request_id=gen_request_id,
            user_message_id=user_row["id"],
            assistant_message_id=asst_row["id"],
            user_row=user_row,
            started_monotonic=started_monotonic,
            permit=permit,
            prepared=prepared,
            is_replay=False,
        )
    except BaseException:
        if permit is not None:
            await release_cloud_permits(permit)
        raise


async def _emit_replay_stream(plan: PreparedCloudStreamPlan) -> AsyncIterator[str]:
    """Emit idempotent SSE replay frames for an already-completed generation turn."""
    assert plan.replay_assistant_row is not None
    asst = plan.replay_assistant_row
    sources = plan.replay_sources or []
    raw_meta = asst.get("metadata") or {}
    if isinstance(raw_meta, str):
        raw_meta = json.loads(raw_meta)

    seq = 0

    def _emit(event: str, payload: dict[str, Any]) -> str:
        nonlocal seq
        seq += 1
        return format_sse_event(event, payload, event_id=seq)

    yield _emit(
        "generation_started",
        {
            "message_id": str(plan.assistant_message_id),
            "user_message_id": str(plan.user_message_id),
            "conversation_id": str(plan.conversation_id),
            "generation_request_id": plan.generation_request_id,
            "model_id": asst.get("model_id"),
            "provider_id": asst.get("provider_id"),
            "resolved_model_id": raw_meta.get("resolved_model_id"),
            "streaming": True,
            "replayed": True,
            "started_at": datetime.now(UTC).isoformat(),
        },
    )
    yield _emit(
        "sources_identified",
        {
            "message_id": str(plan.assistant_message_id),
            "conversation_id": str(plan.conversation_id),
            "sources": _format_sources_identified_list(None, sources),
            "omitted_count": int(raw_meta.get("evidence_omitted_count", 0)),
            "retrieval_candidate_count": int(raw_meta.get("retrieval_candidate_count", 0)),
        },
    )
    replay_content = str(asst.get("content") or "")
    if replay_content:
        yield _emit(
            "text_delta",
            {
                "message_id": str(plan.assistant_message_id),
                "delta": replay_content,
            },
        )

    telemetry = OrchestrationTelemetry(
        conversation_id=plan.conversation_id,
        user_message_id=plan.user_message_id,
        assistant_message_id=plan.assistant_message_id,
        generation_request_id=plan.generation_request_id,
        retrieval_candidate_count=int(raw_meta.get("retrieval_candidate_count", 0)),
        selected_evidence_count=len(sources),
        evidence_omitted_count=int(raw_meta.get("evidence_omitted_count", 0)),
        accounting=raw_meta["accounting"],
        compaction_scheduled=bool(raw_meta.get("compaction_scheduled", False)),
        citation_validation_status=str(
            raw_meta.get("citation_validation_status", "valid_grounded")
        ),
        valid_citation_count=sum(1 for s in sources if s.referenced_in_answer),
        invalid_citation_count=0,
        repair_attempted=bool(raw_meta.get("repair_attempted", False)),
        provider_id=asst.get("provider_id"),
        model_id=asst.get("model_id"),
        resolved_model_id=raw_meta.get("resolved_model_id"),
        duration_ms=int((time.monotonic() - plan.started_monotonic) * 1000),
    )

    yield _emit(
        "generation_completed",
        {
            "message_id": str(plan.assistant_message_id),
            "user_message_id": str(plan.user_message_id),
            "conversation_id": str(plan.conversation_id),
            "content": replay_content,
            "finish_reason": asst.get("finish_reason") or "stop",
            "usage": asst.get("token_usage") or {},
            "citation_validation_status": telemetry.citation_validation_status,
            "valid_citations": [s.citation_key for s in sources if s.referenced_in_answer],
            "repair_attempted": telemetry.repair_attempted,
            "sources": [s.model_dump(mode="json") for s in sources],
            "user_message": MessageRead.model_validate(plan.user_row).model_dump(mode="json"),
            "assistant_message": MessageRead.model_validate(asst).model_dump(mode="json"),
            "telemetry": telemetry.model_dump(mode="json"),
            "replayed": True,
        },
    )


async def stream_cloud_sse_events(
    plan: PreparedCloudStreamPlan,
    *,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[str]:
    """Stream SSE frames through admission wait, gateway deltas, repair, and atomic DB completion."""
    if plan.is_replay:
        async for frame in _emit_replay_stream(plan):
            yield frame
        return

    assert plan.prepared is not None
    prepared = plan.prepared
    permit = plan.permit
    seq = 0

    def _emit(event: str, payload: dict[str, Any]) -> str:
        nonlocal seq
        seq += 1
        return format_sse_event(event, payload, event_id=seq)

    waiter_token: str | None = None
    stream_iter: AsyncIterator[GatewayStreamEvent] | None = None
    next_chunk_task: asyncio.Task[GatewayStreamEvent] | None = None
    cancel_wait_task: asyncio.Task[bool] | None = None
    disconnect_wait_task: asyncio.Task[bool] | None = None
    lease_wait_task: asyncio.Task[bool] | None = None

    stop_event = asyncio.Event()
    cancel_event = asyncio.Event()
    disconnect_event = asyncio.Event()

    if await is_generation_cancelled_in_redis(
        plan.assistant_message_id, plan.generation_request_id
    ):
        cancel_event.set()

    watcher_task = asyncio.create_task(
        watch_cancellation_and_disconnect(
            message_id=plan.assistant_message_id,
            generation_request_id=plan.generation_request_id,
            cancel_event=cancel_event,
            disconnect_event=disconnect_event,
            is_disconnected=is_disconnected,
            stop_event=stop_event,
        )
    )

    collected_parts: list[str] = []
    provider_id: str = prepared.model_capability.provider_id
    model_id: str = prepared.model_capability.model_id
    resolved_model_id: str = prepared.model_capability.model_id
    finish_reason = "stop"
    stream_usage: dict[str, Any] = {}
    started_emitted = False

    def _emit_started_and_sources() -> list[str]:
        nonlocal started_emitted
        if started_emitted:
            return []
        started_emitted = True
        return [
            _emit(
                "generation_started",
                {
                    "message_id": str(plan.assistant_message_id),
                    "user_message_id": str(plan.user_message_id),
                    "conversation_id": str(plan.conversation_id),
                    "generation_request_id": plan.generation_request_id,
                    "model_id": model_id,
                    "provider_id": provider_id,
                    "resolved_model_id": resolved_model_id,
                    "streaming": True,
                    "replayed": False,
                    "started_at": datetime.now(UTC).isoformat(),
                },
            ),
            _emit(
                "sources_identified",
                {
                    "message_id": str(plan.assistant_message_id),
                    "conversation_id": str(plan.conversation_id),
                    "sources": _format_sources_identified_list(prepared, None),
                    "omitted_count": prepared.omitted_evidence_count,
                    "retrieval_candidate_count": prepared.retrieval_candidate_count,
                },
            ),
        ]

    try:
        # 1. Bounded waiting when global cloud semaphore (10) was saturated at preflight
        if permit is None:
            waiter_token = f"wait_{plan.generation_request_id}"
            max_wait = float(generation_settings.GENERATION_ADMISSION_TIMEOUT_SECONDS)
            pos = await register_waiter(waiter_token, wait_ttl_seconds=int(max_wait) + 15)
            yield _emit(
                "admission_waiting",
                {
                    "message_id": str(plan.assistant_message_id),
                    "conversation_id": str(plan.conversation_id),
                    "queue_position": pos,
                    "estimated_wait_seconds": generation_settings.GENERATION_ADMISSION_TIMEOUT_SECONDS,
                    "status": "queued",
                },
            )

            loop = asyncio.get_running_loop()
            wait_deadline = loop.time() + max_wait
            last_keepalive = loop.time()
            delay = 0.2

            while permit is None:
                if cancel_event.is_set():
                    retained = await _persist_cancel(
                        assistant_message_id=plan.assistant_message_id,
                        partial_text="",
                        reason="user_requested",
                        generation_request_id=plan.generation_request_id,
                    )
                    yield _emit(
                        "generation_cancelled",
                        {
                            "message_id": str(plan.assistant_message_id),
                            "conversation_id": str(plan.conversation_id),
                            "partial_content": "",
                            "retained_length": retained,
                            "reason": "user_requested",
                        },
                    )
                    return

                if disconnect_event.is_set():
                    await _persist_cancel(
                        assistant_message_id=plan.assistant_message_id,
                        partial_text="",
                        reason="client_disconnected",
                        generation_request_id=plan.generation_request_id,
                    )
                    return

                try:
                    acq_permit, blocked_scope, _ = await try_acquire_cloud_permits(
                        conversation_id=plan.conversation_id,
                        user_id=plan.user_id,
                        lease_token=waiter_token,
                    )
                except AppError as err:
                    await _persist_failure(
                        assistant_message_id=plan.assistant_message_id,
                        error_code=err.code,
                        error_message=err.message,
                        generation_request_id=plan.generation_request_id,
                    )
                    yield _emit(
                        "generation_failed",
                        {
                            "message_id": str(plan.assistant_message_id),
                            "code": err.code,
                            "message": err.message,
                            "retryable": True,
                        },
                    )
                    return

                if acq_permit is not None:
                    permit = acq_permit
                    break

                if blocked_scope in ("conversation", "user"):
                    code = (
                        "CONVERSATION_CONCURRENCY_LIMIT"
                        if blocked_scope == "conversation"
                        else "USER_CONCURRENCY_LIMIT"
                    )
                    msg = (
                        "Another response is currently generating in this conversation."
                        if blocked_scope == "conversation"
                        else "Maximum concurrent generations per student reached."
                    )
                    await _persist_failure(
                        assistant_message_id=plan.assistant_message_id,
                        error_code=code,
                        error_message=msg,
                        generation_request_id=plan.generation_request_id,
                    )
                    yield _emit(
                        "generation_failed",
                        {
                            "message_id": str(plan.assistant_message_id),
                            "code": code,
                            "message": msg,
                            "retryable": True,
                        },
                    )
                    return

                now_t = loop.time()
                remaining = wait_deadline - now_t
                if remaining <= 0:
                    err_msg = (
                        "All cloud generation slots remained busy for 30s. Please retry shortly."
                    )
                    await _persist_failure(
                        assistant_message_id=plan.assistant_message_id,
                        error_code="RATE_LIMITED",
                        error_message=err_msg,
                        generation_request_id=plan.generation_request_id,
                    )
                    yield _emit(
                        "generation_failed",
                        {
                            "message_id": str(plan.assistant_message_id),
                            "code": "RATE_LIMITED",
                            "message": err_msg,
                            "retryable": True,
                        },
                    )
                    return

                if now_t - last_keepalive >= float(
                    generation_settings.GENERATION_KEEPALIVE_SECONDS
                ):
                    yield format_sse_comment("keep-alive")
                    last_keepalive = now_t

                sleep_slice = min(delay + random.uniform(0.0, 0.05), remaining, 1.0)
                cancel_wait = asyncio.create_task(cancel_event.wait())
                disc_wait = asyncio.create_task(disconnect_event.wait())
                try:
                    await asyncio.wait(
                        {cancel_wait, disc_wait},
                        timeout=sleep_slice,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    for t in (cancel_wait, disc_wait):
                        if not t.done():
                            t.cancel()
                            with suppress(asyncio.CancelledError):
                                await t
                delay = min(0.5, delay * 1.4)

            await unregister_waiter(waiter_token)
            waiter_token = None

            # Promote queued 'pending' message to 'streaming' using conditional CAS
            async with get_pool().acquire() as db_conn:
                promoted = await db_conn.fetchval(
                    """
                    UPDATE public.chat_messages
                    SET status = 'streaming',
                        metadata = metadata || '{"stage": "streaming"}'::jsonb,
                        updated_at = timezone('utc'::text, now())
                    WHERE id = $1
                      AND status = 'pending'
                      AND generation_request_id = $2
                    RETURNING id;
                    """,
                    plan.assistant_message_id,
                    plan.generation_request_id,
                )
            if promoted is None or cancel_event.is_set():
                retained = await _persist_cancel(
                    assistant_message_id=plan.assistant_message_id,
                    partial_text="",
                    reason="user_requested",
                    generation_request_id=plan.generation_request_id,
                )
                yield _emit(
                    "generation_cancelled",
                    {
                        "message_id": str(plan.assistant_message_id),
                        "conversation_id": str(plan.conversation_id),
                        "partial_content": "",
                        "retained_length": retained,
                        "reason": "user_requested",
                    },
                )
                return

        assert permit is not None
        start_permit_heartbeat(permit)

        if cancel_event.is_set() or await is_generation_cancelled_in_redis(
            plan.assistant_message_id, plan.generation_request_id
        ):
            retained = await _persist_cancel(
                assistant_message_id=plan.assistant_message_id,
                partial_text="",
                reason="user_requested",
                generation_request_id=plan.generation_request_id,
            )
            yield _emit(
                "generation_cancelled",
                {
                    "message_id": str(plan.assistant_message_id),
                    "conversation_id": str(plan.conversation_id),
                    "partial_content": "",
                    "retained_length": retained,
                    "reason": "user_requested",
                },
            )
            return

        if disconnect_event.is_set():
            await _persist_cancel(
                assistant_message_id=plan.assistant_message_id,
                partial_text="",
                reason="client_disconnected",
                generation_request_id=plan.generation_request_id,
            )
            return

        # 2. Initiate upstream cloud provider stream via M2.9 routing + gateway
        try:
            stream_iter, decision = await stream_grounded_cloud_generation(
                user_id=plan.user_id,
                llm_messages=prepared.llm_messages,
                idempotency_key=plan.request.idempotency_key,
                generation_request_id=plan.generation_request_id,
                routing_mode=plan.request.routing_mode,
                provider_id=plan.request.provider_id,
                model_id=plan.request.model_id,
                credential_id=plan.request.credential_id,
                temperature=plan.request.temperature,
                max_output_tokens=prepared.accounting.reserved_output_tokens,
                cancel_check=lambda: (
                    cancel_event.is_set()
                    or disconnect_event.is_set()
                    or permit.lease_lost_event.is_set()
                ),
                first_token_timeout_seconds=float(
                    generation_settings.GENERATION_FIRST_TOKEN_TIMEOUT_SECONDS
                ),
                idle_timeout_seconds=float(generation_settings.GENERATION_IDLE_TIMEOUT_SECONDS),
            )
        except Exception as exc:
            err_code, err_msg, retryable = _safe_error_message(exc)
            await _persist_failure(
                assistant_message_id=plan.assistant_message_id,
                error_code=err_code,
                error_message=err_msg,
                generation_request_id=plan.generation_request_id,
            )
            yield _emit(
                "generation_failed",
                {
                    "message_id": str(plan.assistant_message_id),
                    "code": err_code,
                    "message": err_msg,
                    "retryable": retryable,
                },
            )
            return

        provider_id = decision.provider_id or provider_id
        model_id = decision.model_id or model_id
        resolved_model_id = decision.resolved_model_id or decision.model_id or resolved_model_id

        # 3. Consume stream with keepalives, cancellation, client disconnect & lease-loss watch
        cancel_wait_task = asyncio.create_task(cancel_event.wait())
        disconnect_wait_task = asyncio.create_task(disconnect_event.wait())
        lease_wait_task = asyncio.create_task(permit.lease_lost_event.wait())
        max_deadline = (
            asyncio.get_running_loop().time() + generation_settings.GENERATION_MAX_DURATION_SECONDS
        )
        keepalive_sec = float(generation_settings.GENERATION_KEEPALIVE_SECONDS)

        async def _abort_upstream_chunk() -> None:
            nonlocal next_chunk_task, stream_iter
            if next_chunk_task is not None and not next_chunk_task.done():
                next_chunk_task.cancel()
                with suppress(asyncio.CancelledError, TimeoutError, Exception):
                    await asyncio.wait_for(next_chunk_task, timeout=1.5)
                next_chunk_task = None
            if stream_iter is not None and hasattr(stream_iter, "aclose"):
                with suppress(Exception):
                    await stream_iter.aclose()
                stream_iter = None

        async def _next_event() -> GatewayStreamEvent:
            assert stream_iter is not None
            return await stream_iter.__anext__()

        while True:
            assert stream_iter is not None
            if next_chunk_task is None:
                next_chunk_task = asyncio.create_task(_next_event())

            remaining_max = max_deadline - asyncio.get_running_loop().time()
            if remaining_max <= 0:
                await _abort_upstream_chunk()
                err_msg = "Generation exceeded maximum allowed streaming duration."
                await _persist_failure(
                    assistant_message_id=plan.assistant_message_id,
                    error_code="GENERATION_TIMEOUT",
                    error_message=err_msg,
                    generation_request_id=plan.generation_request_id,
                )
                yield _emit(
                    "generation_failed",
                    {
                        "message_id": str(plan.assistant_message_id),
                        "code": "GENERATION_TIMEOUT",
                        "message": err_msg,
                        "retryable": True,
                    },
                )
                return

            done_tasks, _ = await asyncio.wait(
                {next_chunk_task, cancel_wait_task, disconnect_wait_task, lease_wait_task},
                timeout=min(keepalive_sec, remaining_max),
                return_when=asyncio.FIRST_COMPLETED,
            )

            if cancel_event.is_set():
                await _abort_upstream_chunk()
                partial = "".join(collected_parts)
                retained = await _persist_cancel(
                    assistant_message_id=plan.assistant_message_id,
                    partial_text=partial,
                    reason="user_requested",
                    generation_request_id=plan.generation_request_id,
                )
                yield _emit(
                    "generation_cancelled",
                    {
                        "message_id": str(plan.assistant_message_id),
                        "conversation_id": str(plan.conversation_id),
                        "partial_content": partial,
                        "retained_length": retained,
                        "reason": "user_requested",
                    },
                )
                return

            if disconnect_event.is_set():
                await _abort_upstream_chunk()
                partial = "".join(collected_parts)
                await _persist_cancel(
                    assistant_message_id=plan.assistant_message_id,
                    partial_text=partial,
                    reason="client_disconnected",
                    generation_request_id=plan.generation_request_id,
                )
                return

            if permit.lease_lost_event.is_set():
                await _abort_upstream_chunk()
                err_msg = "Cloud generation semaphore lease expired and could not be renewed."
                await _persist_failure(
                    assistant_message_id=plan.assistant_message_id,
                    error_code="LEASE_LOST",
                    error_message=err_msg,
                    generation_request_id=plan.generation_request_id,
                )
                yield _emit(
                    "generation_failed",
                    {
                        "message_id": str(plan.assistant_message_id),
                        "code": "LEASE_LOST",
                        "message": err_msg,
                        "retryable": True,
                    },
                )
                return

            if next_chunk_task not in done_tasks:
                yield format_sse_comment("keep-alive")
                continue

            chunk_task = next_chunk_task
            next_chunk_task = None
            try:
                ev = chunk_task.result()
            except StopAsyncIteration:
                break
            except Exception as exc:
                err_code, err_msg, retryable = _safe_error_message(exc)
                await _persist_failure(
                    assistant_message_id=plan.assistant_message_id,
                    error_code=err_code,
                    error_message=err_msg,
                    generation_request_id=plan.generation_request_id,
                )
                yield _emit(
                    "generation_failed",
                    {
                        "message_id": str(plan.assistant_message_id),
                        "code": err_code,
                        "message": err_msg,
                        "retryable": retryable,
                    },
                )
                return

            if ev.provider_id:
                provider_id = ev.provider_id
            if ev.resolved_model:
                resolved_model_id = ev.resolved_model
                if ev.provider_id and ev.provider_id != decision.provider_id:
                    model_id = ev.resolved_model

            if ev.event_type == "started":
                for frame in _emit_started_and_sources():
                    yield frame
                continue

            if ev.event_type == "delta":
                for frame in _emit_started_and_sources():
                    yield frame
                if ev.delta:
                    collected_parts.append(ev.delta)
                    yield _emit(
                        "text_delta",
                        {
                            "message_id": str(plan.assistant_message_id),
                            "delta": ev.delta,
                        },
                    )
                continue

            if ev.event_type == "completed":
                finish_reason = ev.finish_reason or finish_reason
                if ev.usage is not None:
                    stream_usage = ev.usage.model_dump(mode="json")
                continue

            if ev.event_type == "cancelled":
                partial = "".join(collected_parts)
                retained = await _persist_cancel(
                    assistant_message_id=plan.assistant_message_id,
                    partial_text=partial,
                    reason="user_requested",
                    generation_request_id=plan.generation_request_id,
                )
                yield _emit(
                    "generation_cancelled",
                    {
                        "message_id": str(plan.assistant_message_id),
                        "conversation_id": str(plan.conversation_id),
                        "partial_content": partial,
                        "retained_length": retained,
                        "reason": "user_requested",
                    },
                )
                return

            if ev.event_type == "error":
                err_code = ev.error_code or "PROVIDER_STREAM_FAILED"
                err_msg = ev.error_message or "Generation interrupted mid-stream."
                await _persist_failure(
                    assistant_message_id=plan.assistant_message_id,
                    error_code=err_code,
                    error_message=err_msg,
                    generation_request_id=plan.generation_request_id,
                )
                yield _emit(
                    "generation_failed",
                    {
                        "message_id": str(plan.assistant_message_id),
                        "code": err_code,
                        "message": err_msg,
                        "retryable": ev.retryable,
                    },
                )
                return

        # 4. Final race check before validating citations & committing 'completed'
        if cancel_event.is_set() or await is_generation_cancelled_in_redis(
            plan.assistant_message_id, plan.generation_request_id
        ):
            partial = "".join(collected_parts)
            retained = await _persist_cancel(
                assistant_message_id=plan.assistant_message_id,
                partial_text=partial,
                reason="user_requested",
                generation_request_id=plan.generation_request_id,
            )
            yield _emit(
                "generation_cancelled",
                {
                    "message_id": str(plan.assistant_message_id),
                    "conversation_id": str(plan.conversation_id),
                    "partial_content": partial,
                    "retained_length": retained,
                    "reason": "user_requested",
                },
            )
            return

        if disconnect_event.is_set():
            partial = "".join(collected_parts)
            await _persist_cancel(
                assistant_message_id=plan.assistant_message_id,
                partial_text=partial,
                reason="client_disconnected",
                generation_request_id=plan.generation_request_id,
            )
            return

        if permit.lease_lost_event.is_set():
            err_msg = "Cloud generation semaphore lease expired before final commit."
            await _persist_failure(
                assistant_message_id=plan.assistant_message_id,
                error_code="LEASE_LOST",
                error_message=err_msg,
                generation_request_id=plan.generation_request_id,
            )
            yield _emit(
                "generation_failed",
                {
                    "message_id": str(plan.assistant_message_id),
                    "code": "LEASE_LOST",
                    "message": err_msg,
                    "retryable": True,
                },
            )
            return

        for frame in _emit_started_and_sources():
            yield frame

        # 5. Deterministic citation validation & single bounded repair pass
        raw_answer = "".join(collected_parts)
        included_keys = [ev.citation_key for ev in prepared.selected_evidence]
        validation = validate_citations(raw_answer, included_keys)
        repair_attempted = False
        repair_usage_dict: dict[str, Any] | None = None
        final_text = raw_answer.strip()

        if not validation.is_valid and context_settings.CITATION_REPAIR_ATTEMPTS > 0:
            repair_attempted = True
            rep_res = await run_bounded_citation_repair(
                user_id=plan.user_id,
                original_answer=raw_answer,
                included_citation_keys=included_keys,
                unknown_keys=validation.unknown_keys,
                malformed_tokens=validation.malformed_tokens,
                idempotency_key=plan.request.idempotency_key,
                generation_request_id=plan.generation_request_id,
                routing_mode=plan.request.routing_mode,
                provider_id=provider_id,
                model_id=model_id,
                credential_id=plan.request.credential_id,
                max_output_tokens=prepared.accounting.reserved_output_tokens,
            )
            if rep_res is not None:
                repair_usage_dict = dict(rep_res.usage or {})
                validation = validate_citations(rep_res.content, included_keys)
                final_text = rep_res.content.strip()
                resolved_model_id = rep_res.resolved_model_id or resolved_model_id
                if validation.is_valid and final_text != raw_answer.strip():
                    yield _emit(
                        "content_replaced",
                        {
                            "message_id": str(plan.assistant_message_id),
                            "content": final_text,
                            "reason": "citation_repair",
                        },
                    )

        if not validation.is_valid:
            err_msg = "Generated response contained unsupported citation references or empty output that failed validation"
            await _persist_failure(
                assistant_message_id=plan.assistant_message_id,
                error_code="CITATION_VALIDATION_FAILED",
                error_message=err_msg,
                generation_request_id=plan.generation_request_id,
            )
            yield _emit(
                "generation_failed",
                {
                    "message_id": str(plan.assistant_message_id),
                    "code": "CITATION_VALIDATION_FAILED",
                    "message": err_msg,
                    "retryable": True,
                },
            )
            return

        normalized_usage = normalize_token_usage_record(
            stream_usage,
            fallback_source="exact_provider_reported",
            repair_usage=repair_usage_dict,
        )
        referenced_set = set(validation.valid_keys)

        telemetry_meta: dict[str, Any] = {
            "retrieval_candidate_count": prepared.retrieval_candidate_count,
            "evidence_omitted_count": prepared.omitted_evidence_count,
            "citation_validation_status": validation.status,
            "valid_citation_count": len(validation.valid_keys),
            "repair_attempted": repair_attempted,
            "resolved_model_id": resolved_model_id,
            "accounting": prepared.accounting.model_dump(mode="json"),
        }

        async with get_pool().acquire() as db_conn:
            try:
                completed_asst = await complete_cloud_turn_atomically(
                    db_conn,
                    assistant_message_id=plan.assistant_message_id,
                    conversation_id=plan.conversation_id,
                    final_content=final_text,
                    model_id=model_id,
                    provider_id=provider_id,
                    token_usage=normalized_usage,
                    finish_reason=finish_reason,
                    selected_evidence=prepared.selected_evidence,
                    referenced_keys=referenced_set,
                    extra_metadata=telemetry_meta,
                    expected_generation_request_id=plan.generation_request_id,
                )
            except ConflictError as ce:
                if ce.code == "MESSAGE_NOT_ACTIVE":
                    partial = "".join(collected_parts)
                    retained = await _persist_cancel(
                        assistant_message_id=plan.assistant_message_id,
                        partial_text=partial,
                        reason="user_requested",
                        generation_request_id=plan.generation_request_id,
                    )
                    yield _emit(
                        "generation_cancelled",
                        {
                            "message_id": str(plan.assistant_message_id),
                            "conversation_id": str(plan.conversation_id),
                            "partial_content": partial,
                            "retained_length": retained,
                            "reason": "user_requested",
                        },
                    )
                    return
                raise
            except Exception:
                await mark_assistant_message_failed(
                    db_conn,
                    assistant_message_id=plan.assistant_message_id,
                    error_code="PERSISTENCE_FAILED",
                    error_message="Failed to persist assistant response and source provenance",
                    expected_generation_request_id=plan.generation_request_id,
                )
                yield _emit(
                    "generation_failed",
                    {
                        "message_id": str(plan.assistant_message_id),
                        "code": "PERSISTENCE_FAILED",
                        "message": "Failed to persist assistant response and source provenance",
                        "retryable": False,
                    },
                )
                return

            compaction_scheduled = await schedule_compaction_if_needed(
                db_conn, plan.conversation_id, is_private_local=False
            )
            if compaction_scheduled:
                asyncio.create_task(_background_compact_task(plan.conversation_id))

            _, sources = await get_message_sources_with_revalidation(
                db_conn,
                message_id=plan.assistant_message_id,
                user_id=plan.user_id,
            )

        duration_ms = int((time.monotonic() - plan.started_monotonic) * 1000)
        telemetry = OrchestrationTelemetry(
            conversation_id=plan.conversation_id,
            user_message_id=plan.user_message_id,
            assistant_message_id=plan.assistant_message_id,
            generation_request_id=plan.generation_request_id,
            retrieval_candidate_count=prepared.retrieval_candidate_count,
            selected_evidence_count=len(sources),
            evidence_omitted_count=prepared.omitted_evidence_count,
            accounting=prepared.accounting,
            compaction_scheduled=compaction_scheduled,
            citation_validation_status=validation.status,
            valid_citation_count=len(validation.valid_keys),
            invalid_citation_count=len(validation.unknown_keys),
            repair_attempted=repair_attempted,
            provider_id=provider_id,
            model_id=model_id,
            resolved_model_id=resolved_model_id,
            duration_ms=duration_ms,
        )

        yield _emit(
            "generation_completed",
            {
                "message_id": str(plan.assistant_message_id),
                "user_message_id": str(plan.user_message_id),
                "conversation_id": str(plan.conversation_id),
                "content": final_text,
                "finish_reason": finish_reason,
                "usage": normalized_usage,
                "citation_validation_status": validation.status,
                "valid_citations": validation.valid_keys,
                "repair_attempted": repair_attempted,
                "sources": [s.model_dump(mode="json") for s in sources],
                "user_message": MessageRead.model_validate(plan.user_row).model_dump(mode="json"),
                "assistant_message": MessageRead.model_validate(completed_asst).model_dump(
                    mode="json"
                ),
                "telemetry": telemetry.model_dump(mode="json"),
                "replayed": False,
            },
        )

    except asyncio.CancelledError:
        partial = "".join(collected_parts)
        with suppress(Exception):
            await _persist_cancel(
                assistant_message_id=plan.assistant_message_id,
                partial_text=partial,
                reason="client_disconnected",
                generation_request_id=plan.generation_request_id,
            )
        raise
    finally:
        stop_event.set()
        cleanup_tasks: list[asyncio.Task[Any] | None] = [
            next_chunk_task,
            cancel_wait_task,
            disconnect_wait_task,
            lease_wait_task,
            watcher_task,
        ]
        for task_item in cleanup_tasks:
            if task_item is not None and not task_item.done():
                task_item.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task_item
        if stream_iter is not None and hasattr(stream_iter, "aclose"):
            with suppress(Exception):
                await stream_iter.aclose()
        if waiter_token is not None:
            await unregister_waiter(waiter_token)
        if permit is not None:
            await stop_permit_heartbeat(permit)
            await release_cloud_permits(permit)
