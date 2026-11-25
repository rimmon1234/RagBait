"""Cloud generation adapter delegating strictly to M2.9 route_and_dispatch()."""

import logging
from typing import Any
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


def normalize_token_usage_record(
    raw_usage: dict[str, Any] | None,
    *,
    fallback_source: str = "exact_provider_reported",
    repair_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Label token usage honestly across provider-reported, tokenizer, and local estimates."""
    usage = dict(raw_usage or {})
    raw_src = str(usage.get("usage_source") or fallback_source)
    label_map = {
        "authoritative": "exact_provider_reported",
        "exact_provider_reported": "exact_provider_reported",
        "provider_reported": "exact_provider_reported",
        "exact_tokenizer": "exact_tokenizer",
        "derived": "local_estimate",
        "estimated": "local_estimate",
        "local_estimate": "local_estimate",
        "device_reported": "device_reported",
        "unknown": "local_estimate",
    }
    count_label = label_map.get(raw_src, "local_estimate")

    inp = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    out = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    tot = int(usage.get("total_tokens") or (inp + out))
    out_dict: dict[str, Any] = {
        **usage,
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": tot,
        "usage_source": raw_src,
        "count_label": count_label,
        "is_approximate": count_label == "local_estimate",
    }
    if repair_usage is not None:
        rep_in = int(repair_usage.get("input_tokens") or 0)
        rep_out = int(repair_usage.get("output_tokens") or 0)
        out_dict["initial_input_tokens"] = inp
        out_dict["initial_output_tokens"] = out
        out_dict["repair_input_tokens"] = rep_in
        out_dict["repair_output_tokens"] = rep_out
        out_dict["input_tokens"] = inp + rep_in
        out_dict["output_tokens"] = out + rep_out
        out_dict["total_tokens"] = inp + rep_in + out + rep_out
    return out_dict


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
        normalized_usage: dict[str, Any],
    ) -> None:
        self.final_text = final_text
        self.validation = validation
        self.response = response
        self.decision = decision
        self.repair_attempted = repair_attempted
        self.normalized_usage = normalized_usage


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
    repair_usage_dict: dict[str, Any] | None = None
    initial_usage_dict = dict(raw_result.usage or {})

    if not validation.is_valid and context_settings.CITATION_REPAIR_ATTEMPTS > 0:
        repair_attempted = True
        logger.info(
            f"Citation validation failed for request {generation_request_id}; "
            f"attempting bounded single repair pass (status={validation.status}, unknown={validation.unknown_keys})"
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
                repair_usage_dict = dict(rep_res.usage or {})
                # Re-run FULL citation + emptiness validation on repaired output
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
            message="Generated response contained unsupported citation references or empty output that failed validation",
            details={
                "status": validation.status,
                "unknown_keys": validation.unknown_keys,
                "malformed_tokens": validation.malformed_tokens,
                "repair_attempted": repair_attempted,
            },
        )

    normalized_usage = normalize_token_usage_record(
        initial_usage_dict,
        repair_usage=repair_usage_dict,
    )

    return CloudGenerationOutput(
        final_text=answer_text.strip(),
        validation=validation,
        response=raw_result,
        decision=decision,
        repair_attempted=repair_attempted,
        normalized_usage=normalized_usage,
    )
