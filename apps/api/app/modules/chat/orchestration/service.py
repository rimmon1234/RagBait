"""Orchestration service composing RAG context preparation, generation, validation, and snapshots."""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.core.database import get_pool
from app.core.errors import AppError, AuthorizationError, ConflictError
from app.modules.chat.citations.source_repository import (
    get_message_sources_with_revalidation,
    load_server_included_keys,
    mark_referenced_citations,
)
from app.modules.chat.citations.validator import validate_citations
from app.modules.chat.context.compaction import (
    compact_conversation,
    schedule_compaction_if_needed,
)
from app.modules.chat.context.config import context_settings
from app.modules.chat.context.context_service import ContextService
from app.modules.chat.orchestration.cancellation import is_generation_cancelled_in_redis
from app.modules.chat.orchestration.generation import (
    execute_grounded_cloud_generation,
    normalize_token_usage_record,
)
from app.modules.chat.orchestration.persistence import (
    complete_cloud_turn_atomically,
    compute_prepared_expiry,
    lock_and_verify_prepared_local_message,
    lookup_existing_turn_by_idempotency,
    mark_assistant_message_cancelled,
    mark_assistant_message_failed,
    reserve_turn_messages,
    verify_conversation_ready_for_cloud,
)
from app.modules.chat.orchestration.schemas import (
    ChatMessageCreateRequest,
    ChatTurnResponse,
    FinalizeLocalRequest,
    FinalizeLocalResponse,
    OrchestrationTelemetry,
    PreparedSourceMetadata,
    PrepareLocalRequest,
    PrepareLocalResponse,
)
from app.modules.chat.orchestration.semaphores import (
    CloudGenerationPermit,
    release_cloud_permits,
    start_permit_heartbeat,
    stop_permit_heartbeat,
    wait_for_sync_cloud_permits,
)
from app.modules.chat.schemas import MessageRead

logger = logging.getLogger("ragbait.chat.orchestration.service")


@asynccontextmanager
async def _borrow_conn(provided: asyncpg.Connection | None) -> AsyncIterator[asyncpg.Connection]:
    """Yield caller-provided connection or temporarily acquire one from the pool."""
    if provided is not None:
        yield provided
        return
    pool = get_pool()
    async with pool.acquire() as conn:
        yield conn


def _safe_error_text(exc: Exception) -> str:
    """Extract non-secret error message safe for database storage."""
    if isinstance(exc, AppError):
        return exc.message
    return "Generation failed due to an internal or upstream error"


async def _background_compact_task(conversation_id: UUID) -> None:
    """Fire-and-forget asynchronous summary compaction wrapper with exception guard."""
    try:
        await compact_conversation(conversation_id, is_private_local=False)
    except Exception as exc:
        logger.warning(f"Background compaction task failed for {conversation_id}: {exc}")


class ChatOrchestrationService:
    """Main entry point orchestrating M4.5 grounded chat generation workflows."""

    @staticmethod
    async def execute_cloud_chat_turn(
        *,
        user_id: UUID,
        conversation_id: UUID,
        request: ChatMessageCreateRequest,
        spawn_background_compaction: bool = True,
        conn: asyncpg.Connection | None = None,
    ) -> ChatTurnResponse:
        """Execute a complete synchronous M4.5 grounded RAG turn over cloud inference."""
        start_ts = time.monotonic()
        gen_request_id = f"req-{uuid4().hex[:12]}"

        # 1 & 2 & 3. Pre-LLM DB work (idempotency check -> retrieval/context -> reserve messages)
        # Releases pool connection before upstream HTTP inference unless caller injected `conn`.
        async with _borrow_conn(conn) as db_conn:
            existing_user, existing_asst = await lookup_existing_turn_by_idempotency(
                db_conn, conversation_id=conversation_id, idempotency_key=request.idempotency_key
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
                        db_conn, message_id=existing_asst["id"], user_id=user_id
                    )
                    raw_meta = existing_asst.get("metadata") or {}
                    if isinstance(raw_meta, str):
                        raw_meta = json.loads(raw_meta)
                    telemetry = OrchestrationTelemetry(
                        conversation_id=conversation_id,
                        user_message_id=existing_user["id"],
                        assistant_message_id=existing_asst["id"],
                        generation_request_id=existing_asst.get("generation_request_id")
                        or gen_request_id,
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
                        provider_id=existing_asst.get("provider_id"),
                        model_id=existing_asst.get("model_id"),
                        resolved_model_id=raw_meta.get("resolved_model_id"),
                        duration_ms=int((time.monotonic() - start_ts) * 1000),
                    )
                    return ChatTurnResponse(
                        conversation_id=conversation_id,
                        user_message=MessageRead.model_validate(existing_user),
                        assistant_message=MessageRead.model_validate(existing_asst),
                        sources=sources,
                        telemetry=telemetry,
                    )

            await verify_conversation_ready_for_cloud(
                db_conn,
                user_id=user_id,
                conversation_id=conversation_id,
            )

        permit: CloudGenerationPermit = await wait_for_sync_cloud_permits(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        try:
            return await ChatOrchestrationService._execute_cloud_turn_with_permit(
                user_id=user_id,
                conversation_id=conversation_id,
                request=request,
                gen_request_id=gen_request_id,
                start_ts=start_ts,
                existing_user_id=existing_user["id"] if existing_user else None,
                permit=permit,
                spawn_background_compaction=spawn_background_compaction,
                conn=conn,
            )
        finally:
            await stop_permit_heartbeat(permit)
            await release_cloud_permits(permit)

    @staticmethod
    async def _execute_cloud_turn_with_permit(
        *,
        user_id: UUID,
        conversation_id: UUID,
        request: ChatMessageCreateRequest,
        gen_request_id: str,
        start_ts: float,
        existing_user_id: UUID | None,
        permit: CloudGenerationPermit,
        spawn_background_compaction: bool,
        conn: asyncpg.Connection | None,
    ) -> ChatTurnResponse:
        async with _borrow_conn(conn) as db_conn:
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
                exclude_message_id=existing_user_id,
                conn=db_conn,
            )

            async with db_conn.transaction():
                user_row, asst_row = await reserve_turn_messages(
                    db_conn,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    user_content=request.content,
                    idempotency_key=request.idempotency_key,
                    generation_request_id=gen_request_id,
                    mode="cloud",
                    model_id=prepared.model_capability.model_id,
                    provider_id=prepared.model_capability.provider_id,
                    extra_metadata={"model_fingerprint": prepared.model_fingerprint},
                )
        assistant_id: UUID = asst_row["id"]
        start_permit_heartbeat(permit)

        # 4. Call inference gateway + validate citations (NO DB connection held during upstream I/O!)
        included_keys = [ev.citation_key for ev in prepared.selected_evidence]
        try:
            gen_out = await execute_grounded_cloud_generation(
                user_id=user_id,
                llm_messages=prepared.llm_messages,
                included_citation_keys=included_keys,
                idempotency_key=request.idempotency_key,
                generation_request_id=gen_request_id,
                routing_mode=request.routing_mode,
                provider_id=request.provider_id,
                model_id=request.model_id,
                credential_id=request.credential_id,
                temperature=request.temperature,
                max_output_tokens=prepared.accounting.reserved_output_tokens,
            )
        except Exception as exc:
            err_code = exc.code if isinstance(exc, AppError) else "GENERATION_FAILED"
            async with _borrow_conn(conn) as fail_conn:
                await mark_assistant_message_failed(
                    fail_conn,
                    assistant_message_id=assistant_id,
                    error_code=err_code,
                    error_message=_safe_error_text(exc),
                    expected_generation_request_id=gen_request_id,
                )
            raise

        if await is_generation_cancelled_in_redis(assistant_id, gen_request_id):
            async with _borrow_conn(conn) as cancel_conn:
                await mark_assistant_message_cancelled(
                    cancel_conn,
                    assistant_message_id=assistant_id,
                    partial_content="",
                    reason="user_requested",
                    expected_generation_request_id=gen_request_id,
                )
            raise ConflictError("Generation was cancelled", code="GENERATION_CANCELLED")

        # 5 & 6 & 7. Post-LLM atomic completion + compaction scheduling + revalidated sources
        referenced_set = set(gen_out.validation.valid_keys)
        telemetry_meta: dict[str, Any] = {
            "retrieval_candidate_count": prepared.retrieval_candidate_count,
            "evidence_omitted_count": prepared.omitted_evidence_count,
            "citation_validation_status": gen_out.validation.status,
            "valid_citation_count": len(gen_out.validation.valid_keys),
            "repair_attempted": gen_out.repair_attempted,
            "resolved_model_id": gen_out.response.resolved_model_id,
            "accounting": prepared.accounting.model_dump(mode="json"),
        }

        async with _borrow_conn(conn) as db_conn:
            try:
                completed_asst = await complete_cloud_turn_atomically(
                    db_conn,
                    assistant_message_id=assistant_id,
                    conversation_id=conversation_id,
                    final_content=gen_out.final_text,
                    model_id=gen_out.response.model_id,
                    provider_id=gen_out.response.provider_id,
                    token_usage=gen_out.normalized_usage,
                    finish_reason=gen_out.response.finish_reason,
                    selected_evidence=prepared.selected_evidence,
                    referenced_keys=referenced_set,
                    extra_metadata=telemetry_meta,
                    expected_generation_request_id=gen_request_id,
                )
            except ConflictError:
                raise
            except Exception:
                await mark_assistant_message_failed(
                    db_conn,
                    assistant_message_id=assistant_id,
                    error_code="PERSISTENCE_FAILED",
                    error_message="Failed to persist assistant response and source provenance",
                    expected_generation_request_id=gen_request_id,
                )
                raise

            compaction_scheduled = await schedule_compaction_if_needed(
                db_conn, conversation_id, is_private_local=False
            )
            if compaction_scheduled and spawn_background_compaction:
                asyncio.create_task(_background_compact_task(conversation_id))

            _, sources = await get_message_sources_with_revalidation(
                db_conn, message_id=assistant_id, user_id=user_id
            )

        duration_ms = int((time.monotonic() - start_ts) * 1000)

        telemetry = OrchestrationTelemetry(
            conversation_id=conversation_id,
            user_message_id=user_row["id"],
            assistant_message_id=assistant_id,
            generation_request_id=gen_request_id,
            retrieval_candidate_count=prepared.retrieval_candidate_count,
            selected_evidence_count=len(prepared.selected_evidence),
            evidence_omitted_count=prepared.omitted_evidence_count,
            accounting=prepared.accounting,
            compaction_scheduled=compaction_scheduled,
            citation_validation_status=gen_out.validation.status,
            valid_citation_count=len(gen_out.validation.valid_keys),
            invalid_citation_count=len(gen_out.validation.unknown_keys),
            repair_attempted=gen_out.repair_attempted,
            provider_id=gen_out.response.provider_id,
            model_id=gen_out.response.model_id,
            resolved_model_id=gen_out.response.resolved_model_id,
            duration_ms=duration_ms,
        )
        logger.info(
            f"M4.5 cloud turn completed conv={conversation_id} msg={assistant_id} "
            f"sources={len(sources)} cited={len(referenced_set)} duration_ms={duration_ms}"
        )
        return ChatTurnResponse(
            conversation_id=conversation_id,
            user_message=MessageRead.model_validate(user_row),
            assistant_message=MessageRead.model_validate(completed_asst),
            sources=sources,
            telemetry=telemetry,
        )

    @staticmethod
    async def prepare_local_chat_turn(
        *,
        user_id: UUID,
        conversation_id: UUID,
        request: PrepareLocalRequest,
        conn: asyncpg.Connection | None = None,
    ) -> PrepareLocalResponse:
        """Prepare M4.4 retrieval, budgeted XML prompt, and Stage-3 evidence for Private Local Ollama.

        Guarantees:
        - ZERO calls to external cloud providers or shared API keys.
        - Bypasses cloud concurrency semaphores cleanly.
        - Persists authoritative Stage-3 evidence snapshots server-side so finalize-local
          never trusts client-supplied citation or source metadata.
        """
        gen_request_id = f"local-{uuid4().hex[:12]}"
        prepared_request_id = f"prep-{uuid4().hex}"
        expires_at = compute_prepared_expiry(context_settings.PREPARED_LOCAL_TTL_SECONDS)

        async def _run(db_conn: asyncpg.Connection) -> PrepareLocalResponse:
            # 1. Check idempotency
            existing_user, existing_asst = await lookup_existing_turn_by_idempotency(
                db_conn, conversation_id=conversation_id, idempotency_key=request.idempotency_key
            )
            if existing_user and existing_asst:
                if existing_user["content"].strip() != request.content.strip():
                    raise ConflictError(
                        f"Idempotency key '{request.idempotency_key}' already used with different content",
                        code="IDEMPOTENCY_KEY_CONFLICT",
                    )
                if existing_asst["status"] == "completed":
                    raise ConflictError(
                        "Prepared local request has already been finalized",
                        code="ALREADY_FINALIZED",
                    )
                if existing_asst["status"] in ("pending", "streaming"):
                    raise ConflictError(
                        "Prepared local request for this idempotency key is already in progress",
                        code="GENERATION_IN_PROGRESS",
                    )

            # 2. Prepare context in 'local' mode
            prepared = await ContextService.prepare_grounded_context(
                user_id=user_id,
                conversation_id=conversation_id,
                user_query=request.content,
                inference_mode="local",
                requested_model_id=request.model_id,
                requested_max_output=request.max_output_tokens,
                exclude_message_id=existing_user["id"] if existing_user else None,
                conn=db_conn,
            )

            # 3. Persist user message + pending local assistant + Stage-3 chat_message_sources
            extra_meta = {
                "prepared_request_id": prepared_request_id,
                "prepared_expires_at": expires_at.isoformat(),
                "model_fingerprint": prepared.model_fingerprint,
                "retrieval_candidate_count": prepared.retrieval_candidate_count,
                "evidence_omitted_count": prepared.omitted_evidence_count,
                "accounting": prepared.accounting.model_dump(mode="json"),
            }
            async with db_conn.transaction():
                user_row, asst_row = await reserve_turn_messages(
                    db_conn,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    user_content=request.content,
                    idempotency_key=request.idempotency_key,
                    generation_request_id=gen_request_id,
                    mode="private_local",
                    model_id=prepared.model_capability.model_id,
                    provider_id="ollama",
                    extra_metadata=extra_meta,
                    selected_evidence=prepared.selected_evidence,
                )

            safe_sources = [
                PreparedSourceMetadata(
                    citation_key=ev.citation_key,
                    document_id=ev.document_id,
                    document_title=ev.document_title,
                    page_number_start=ev.page_number_start,
                    page_number_end=ev.page_number_end,
                    relevance_score=ev.rrf_score,
                )
                for ev in prepared.selected_evidence
            ]

            logger.info(
                f"M4.5 prepare-local succeeded conv={conversation_id} msg={asst_row['id']} "
                f"model={prepared.model_capability.model_id} sources={len(safe_sources)}"
            )
            return PrepareLocalResponse(
                message_id=asst_row["id"],
                user_message_id=user_row["id"],
                prepared_request_id=prepared_request_id,
                conversation_id=conversation_id,
                model_id=prepared.model_capability.model_id,
                expires_at=expires_at,
                prompt=prepared.rendered_single_prompt,
                messages=prepared.llm_messages,
                sources=safe_sources,
                accounting=prepared.accounting,
                evidence_omitted_count=prepared.omitted_evidence_count,
            )

        if conn is not None:
            return await _run(conn)

        pool = get_pool()
        async with pool.acquire() as pool_conn:
            return await _run(pool_conn)

    @staticmethod
    async def finalize_local_chat_turn(
        *,
        user_id: UUID,
        conversation_id: UUID,
        request: FinalizeLocalRequest,
        conn: asyncpg.Connection | None = None,
    ) -> FinalizeLocalResponse:
        """Reconcile citations against server-stored sources and finalize a Private Local turn.

        Guarantees:
        - NEVER trusts client-supplied source lists or citation claims.
        - Enforces ownership, non-expiry, and single-use finalization.
        - Revalidates current Knowledge Base visibility before exposing sources.
        - ZERO cloud API calls (invalid citations stripped deterministically if present).
        """

        async def _run(db_conn: asyncpg.Connection) -> FinalizeLocalResponse:
            import json

            # Verify approved user status cleanly
            prof = await db_conn.fetchrow(
                "SELECT approval_status FROM public.profiles WHERE id = $1", user_id
            )
            if not prof or prof["approval_status"] != "approved":
                raise AuthorizationError(
                    "User account is not approved", code="ACCOUNT_NOT_APPROVED"
                )

            try:
                async with db_conn.transaction():
                    asst_row = await lock_and_verify_prepared_local_message(
                        db_conn,
                        message_id=request.message_id,
                        conversation_id=conversation_id,
                        user_id=user_id,
                        prepared_request_id=request.prepared_request_id,
                    )

                    server_sources = await load_server_included_keys(db_conn, request.message_id)
                    included_keys = [str(s["citation_key"]) for s in server_sources]
                    revoked_count = sum(
                        1 for s in server_sources if not bool(s["is_currently_available"])
                    )

                    validation = validate_citations(request.completion_text, included_keys)
                    final_text = (
                        request.completion_text.strip()
                        if validation.is_valid
                        else validation.sanitized_text
                    )
                    validation_status = (
                        validation.status if validation.is_valid else "repaired_stripped_unverified"
                    )

                    await mark_referenced_citations(
                        db_conn,
                        message_id=request.message_id,
                        referenced_keys=validation.valid_keys,
                    )

                    updated_meta = {
                        **asst_row["metadata"],
                        "stage": "completed",
                        "citation_validation_status": validation_status,
                        "unverified_citations_stripped": validation.unknown_keys,
                        "malformed_citations_stripped": validation.malformed_tokens,
                        "revoked_sources_at_finalize": revoked_count,
                    }

                    safe_usage = normalize_token_usage_record(
                        {
                            "input_tokens": int(request.token_usage.get("prompt_tokens") or 0),
                            "output_tokens": int(request.token_usage.get("completion_tokens") or 0),
                            "total_tokens": int(request.token_usage.get("total_tokens") or 0),
                            "usage_source": "device_reported",
                        },
                        fallback_source="device_reported",
                    )

                    updated_row = await db_conn.fetchrow(
                        """
                        UPDATE public.chat_messages
                        SET content = $2,
                            status = 'completed',
                            finish_reason = $3,
                            token_usage = $4::jsonb,
                            metadata = $5::jsonb,
                            updated_at = timezone('utc'::text, now())
                        WHERE id = $1
                          AND status = 'pending'
                          AND metadata->>'prepared_request_id' = $6
                        RETURNING id;
                        """,
                        request.message_id,
                        final_text,
                        request.finish_reason,
                        json.dumps(safe_usage),
                        json.dumps(updated_meta),
                        request.prepared_request_id,
                    )
                    if not updated_row:
                        raise ConflictError(
                            "Prepared local request could not be finalized due to concurrent state change",
                            code="ALREADY_FINALIZED",
                        )

                    await db_conn.execute(
                        """
                        UPDATE public.chat_conversations
                        SET last_message_at = timezone('utc'::text, now()),
                            updated_at = timezone('utc'::text, now())
                        WHERE id = $1;
                        """,
                        conversation_id,
                    )

                    # Respect zero-cloud privacy: defer local compaction cleanly without calling cloud LLM
                    await schedule_compaction_if_needed(
                        db_conn, conversation_id, is_private_local=True
                    )
            except ConflictError as ce:
                # Commit expiration marking outside rolled-back transaction if expired
                if ce.code == "PREPARED_REQUEST_EXPIRED":
                    await mark_assistant_message_failed(
                        db_conn,
                        assistant_message_id=request.message_id,
                        error_code="PREPARED_REQUEST_EXPIRED",
                        error_message="Prepared local request expired before finalization",
                    )
                raise

            _, revalidated_sources = await get_message_sources_with_revalidation(
                db_conn, message_id=request.message_id, user_id=user_id
            )

            logger.info(
                f"M4.5 finalize-local completed conv={conversation_id} msg={request.message_id} "
                f"valid_citations={len(validation.valid_keys)} stripped={len(validation.unknown_keys)}"
            )
            return FinalizeLocalResponse(
                message_id=request.message_id,
                status="completed",
                citations_reconciled=len(validation.valid_keys),
                valid_citations=validation.valid_keys,
                invalid_citations_stripped=validation.unknown_keys,
                citation_validation_status=validation_status,
                sources=revalidated_sources,
            )

        if conn is not None:
            return await _run(conn)

        pool = get_pool()
        async with pool.acquire() as pool_conn:
            return await _run(pool_conn)

    @staticmethod
    async def get_message_sources(
        *,
        user_id: UUID,
        message_id: UUID,
        conn: asyncpg.Connection | None = None,
    ) -> tuple[UUID, list[Any]]:
        """Retrieve immutable message source snapshots with live KB visibility revalidation."""

        async def _run(db_conn: asyncpg.Connection) -> tuple[UUID, list[Any]]:
            return await get_message_sources_with_revalidation(
                db_conn, message_id=message_id, user_id=user_id
            )

        if conn is not None:
            return await _run(conn)

        pool = get_pool()
        async with pool.acquire() as pool_conn:
            return await _run(pool_conn)
