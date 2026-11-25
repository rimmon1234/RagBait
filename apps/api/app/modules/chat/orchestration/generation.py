"""Cloud generation adapter delegating strictly to M2.9 route_and_dispatch()."""

import logging
from uuid import UUID

from app.modules.chat.citations.schemas import CitationValidationError, CitationValidationResult
from app.modules.chat.citations.validator import build_citation_repair_messages, validate_citations
from app.modules.chat.context.config import context_settings
from app.modules.llm_gateway.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
)
from app.modules.routing.schemas import RouteDecision
from app.modules.routing.service import route_and_dispatch

logger = logging.getLogger("ragbait.chat.orchestration.generation")


class CloudGenerationOutput:
    """Normalized cloud generation and citation validation output."""

    def __init__(
        self,
        *,
        final_text: str,
        validation: CitationValidationResult,
        response: ChatCompletionResponse,
        decision: RouteDecision,
        repair_attempted: bool,
    ) -> None:
        self.final_text = final_text
        self.validation = validation
        self.response = response
        self.decision = decision
        self.repair_attempted = repair_attempted


async def execute_grounded_cloud_generation(
    *,
    user_id: UUID,
    llm_messages: list[dict[str, str]],
    included_citation_keys: list[str],
    idempotency_key: str,
    generation_request_id: str,
    routing_mode: str = "auto",
    provider_id: str | None = None,
    model_id: str | None = None,
    credential_id: UUID | None = None,
    temperature: float = 0.7,
    max_output_tokens: int = 2048,
) -> CloudGenerationOutput:
    """Execute non-streaming cloud generation via route_and_dispatch and validate citations.

    Guarantees:
    - Reuses M2.9 route_and_dispatch() exclusively (no duplicate adapters or vault paths).
    - Preserves all quota, admission, free-tier, and preference policies.
    - Runs deterministic citation validation and at most one bounded repair pass.
    - Raises CitationValidationError if unknown/malformed citations persist after repair.
    """
    gateway_msgs = [ChatMessage(role=m["role"], content=m["content"]) for m in llm_messages]  # type: ignore[arg-type]
    req = ChatCompletionRequest(
        routing_mode=routing_mode,  # type: ignore[arg-type]
        provider_id=provider_id,
        model_id=model_id,
        credential_id=credential_id,
        community_opt_in=False,
        messages=gateway_msgs,
        stream=False,
        idempotency_key=f"chat-{idempotency_key}",
    )
    req.params.temperature = temperature
    req.params.max_output_tokens = max_output_tokens

    raw_result, is_streaming, decision = await route_and_dispatch(
        user_id=user_id,
        req=req,
        correlation_id=generation_request_id,
    )
    if is_streaming or not isinstance(raw_result, ChatCompletionResponse):
        raise RuntimeError(
            "M4.5 non-streaming orchestration received unexpected streaming iterator"
        )

    answer_text = raw_result.content
    validation = validate_citations(answer_text, included_citation_keys)
    repair_attempted = False

    if not validation.is_valid and context_settings.CITATION_REPAIR_ATTEMPTS > 0:
        repair_attempted = True
        logger.info(
            f"Citation validation failed for request {generation_request_id}; "
            f"attempting bounded single repair pass (unknown={validation.unknown_keys})"
        )
        repair_msgs = build_citation_repair_messages(
            original_answer=answer_text,
            valid_included_keys=included_citation_keys,
            unknown_keys=validation.unknown_keys,
            malformed_tokens=validation.malformed_tokens,
        )
        repair_req = ChatCompletionRequest(
            routing_mode=routing_mode,  # type: ignore[arg-type]
            provider_id=decision.provider_id,
            model_id=decision.model_id,
            credential_id=credential_id,
            community_opt_in=False,
            messages=[ChatMessage(role=m["role"], content=m["content"]) for m in repair_msgs],  # type: ignore[arg-type]
            stream=False,
            idempotency_key=f"repair-{idempotency_key}",
        )
        repair_req.params.temperature = 0.0
        repair_req.params.max_output_tokens = max_output_tokens

        try:
            rep_res, rep_streaming, _ = await route_and_dispatch(
                user_id=user_id,
                req=repair_req,
                correlation_id=f"{generation_request_id}-repair",
            )
            if not rep_streaming and isinstance(rep_res, ChatCompletionResponse):
                repaired_validation = validate_citations(rep_res.content, included_citation_keys)
                validation = repaired_validation
                answer_text = rep_res.content
                raw_result = rep_res
        except Exception as repair_exc:
            logger.warning(
                f"Bounded citation repair failed for request {generation_request_id}: {repair_exc}"
            )

    if not validation.is_valid:
        raise CitationValidationError(
            message="Generated response contained unsupported citation references that failed validation",
            details={
                "unknown_keys": validation.unknown_keys,
                "malformed_tokens": validation.malformed_tokens,
                "repair_attempted": repair_attempted,
            },
        )

    return CloudGenerationOutput(
        final_text=answer_text.strip(),
        validation=validation,
        response=raw_result,
        decision=decision,
        repair_attempted=repair_attempted,
    )
