"""Server-authoritative Model Capability Registry reusing M2 public.llm_models."""

import logging
from typing import Any
from uuid import UUID

import asyncpg

from app.core.database import get_pool
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.modules.chat.context.config import context_settings
from app.modules.chat.context.schemas import ModelCapability
from app.modules.routing.discovery import discover_cloud_candidates
from app.modules.routing.policy import filter_candidates, rank_candidates
from app.modules.routing.service import DEFAULT_CLOUD_MODEL

logger = logging.getLogger("ragbait.chat.context.capabilities")

# Authoritative server-side budget caps and family metadata complementing public.llm_models.
# Client-supplied context limits are strictly ignored.
_RECOMMENDED_BUDGET_CAPS: dict[str, int] = {
    "gemini-3.8-flash": 65536,
    "gemini-3.5-flash-lite": 32768,
    "gemini-3.1-flash-lite": 32768,
    "gemini-3.1-pro-preview": 65536,
    "openai/gpt-oss-120b": 16384,
    "openai/gpt-oss-20b": 16384,
    "qwen/qwen3.8-27b": 16384,
    "openrouter/free": 8192,
    "openrouter/meta-llama/llama-3.3-70b-instruct:free": 16384,
    "cerebras/gpt-oss-120b": 16384,
    "gpt-4o": 32768,
    "qwen2.5:7b": 16384,
}

# Known local Ollama capability profiles for Private Local mode when not seeded in DB llm_models.
_LOCAL_OLLAMA_PROFILES: dict[str, dict[str, Any]] = {
    "llama3.2:3b": {
        "family": "llama",
        "context_window": 8192,
        "recommended_budget": 8192,
        "max_output_tokens": 2048,
        "safety_margin_tokens": 512,
    },
    "llama3.1:8b": {
        "family": "llama",
        "context_window": 16384,
        "recommended_budget": 16384,
        "max_output_tokens": 2048,
        "safety_margin_tokens": 512,
    },
    "qwen2.5-coder:7b": {
        "family": "qwen",
        "context_window": 32768,
        "recommended_budget": 16384,
        "max_output_tokens": 4096,
        "safety_margin_tokens": 512,
    },
    "qwen2.5:7b": {
        "family": "qwen",
        "context_window": 32768,
        "recommended_budget": 16384,
        "max_output_tokens": 4096,
        "safety_margin_tokens": 512,
    },
    "mistral:7b": {
        "family": "mistral",
        "context_window": 8192,
        "recommended_budget": 8192,
        "max_output_tokens": 2048,
        "safety_margin_tokens": 512,
    },
    "gemma2:9b": {
        "family": "gemma",
        "context_window": 8192,
        "recommended_budget": 8192,
        "max_output_tokens": 2048,
        "safety_margin_tokens": 512,
    },
}

# Explicitly documented conservative ceiling for unlisted local Ollama tags (e.g. custom modelfiles).
_CONSERVATIVE_UNKNOWN_LOCAL_CEILING = 4096
_CONSERVATIVE_UNKNOWN_LOCAL_OUTPUT = 1024
_CONSERVATIVE_UNKNOWN_LOCAL_SAFETY = 256


def _compute_recommended_budget(model_id: str, raw_context_window: int) -> int:
    """Compute effective server budget ceiling capped safely within raw_context_window."""
    if model_id in _RECOMMENDED_BUDGET_CAPS:
        return min(_RECOMMENDED_BUDGET_CAPS[model_id], raw_context_window)
    # Default safe operational ceiling up to 16,384 unless model window is smaller
    return min(raw_context_window, 16384)


def _capability_from_db_row(row: asyncpg.Record) -> ModelCapability:
    """Convert a public.llm_models joined row into a validated ModelCapability."""
    model_id: str = row["id"]
    provider_id: str = row["provider_id"]
    context_window: int = int(row["context_window"])
    max_out: int = int(row["max_output_tokens"])
    caps = list(row["capabilities"] or [])
    modalities_in = list(row["modalities_input"] or ["text"])
    modalities_out = list(row["modalities_output"] or ["text"])

    supports_text = "text" in modalities_in and "text" in modalities_out
    source_type: str = "local" if row["runtime_type"] == "local_runtime" else "cloud"
    recommended = _compute_recommended_budget(model_id, context_window)
    default_out = min(context_settings.CONTEXT_OUTPUT_RESERVE, max_out)
    # Scale safety margin safely for small windows
    safety = min(context_settings.CONTEXT_SAFETY_MARGIN, max(128, recommended // 8))

    return ModelCapability(
        model_id=model_id,
        provider_id=provider_id,
        family=row["family"],
        source_type="local" if source_type == "local" else "cloud",
        context_window=context_window,
        recommended_budget=recommended,
        max_output_tokens=max_out,
        default_output_reserve=default_out,
        safety_margin_tokens=safety,
        token_counting_strategy="conservative_multilingual_upper_bound_v2",
        exact_tokenizer_available=False,
        supports_generation=supports_text,
        supports_streaming="streaming" in caps,
        is_known_registry_model=True,
    )


async def get_model_capability(
    model_id: str,
    *,
    inference_mode: str = "cloud",
    conn: asyncpg.Connection | None = None,
    allow_conservative_local_fallback: bool = True,
) -> ModelCapability:
    """Resolve trusted server-side capabilities for an explicit model_id.

    Never accepts or trusts client-supplied context windows.
    """
    clean_id = model_id.strip() if model_id else ""
    if not clean_id:
        raise BadRequestError("Model identifier cannot be empty", code="INVALID_MODEL_ID")

    async def _lookup(db_conn: asyncpg.Connection) -> ModelCapability | None:
        row = await db_conn.fetchrow(
            """
            SELECT m.id, m.provider_id, m.family, m.availability_status,
                   m.context_window, m.max_output_tokens, m.capabilities,
                   m.modalities_input, m.modalities_output, m.shutdown_at,
                   p.runtime_type, p.is_enabled
            FROM public.llm_models m
            JOIN public.llm_providers p ON p.id = m.provider_id
            WHERE m.id = $1
            """,
            clean_id,
        )
        if not row:
            return None
        if not row["is_enabled"] or row["availability_status"] != "active":
            raise BadRequestError(
                f"Model '{clean_id}' is disabled or inactive",
                code="MODEL_UNAVAILABLE",
            )
        cap = _capability_from_db_row(row)
        if not cap.supports_generation:
            raise BadRequestError(
                f"Model '{clean_id}' does not support text chat generation",
                code="UNSUPPORTED_MODEL_CAPABILITY",
            )
        return cap

    if conn is not None:
        cap = await _lookup(conn)
    else:
        pool = get_pool()
        async with pool.acquire() as pool_conn:
            cap = await _lookup(pool_conn)

    if cap is not None:
        return cap

    # For Private Local mode (client-side Ollama), check curated local presets or fail safely
    if inference_mode == "local":
        lower_id = clean_id.lower()
        if lower_id in _LOCAL_OLLAMA_PROFILES:
            prof = _LOCAL_OLLAMA_PROFILES[lower_id]
            return ModelCapability(
                model_id=clean_id,
                provider_id="ollama",
                family=prof["family"],
                source_type="local",
                context_window=prof["context_window"],
                recommended_budget=prof["recommended_budget"],
                max_output_tokens=prof["max_output_tokens"],
                default_output_reserve=min(
                    context_settings.CONTEXT_OUTPUT_RESERVE, prof["max_output_tokens"]
                ),
                safety_margin_tokens=prof["safety_margin_tokens"],
                token_counting_strategy="conservative_multilingual_upper_bound_v2",
                exact_tokenizer_available=False,
                supports_generation=True,
                supports_streaming=True,
                is_known_registry_model=True,
            )

        if allow_conservative_local_fallback:
            logger.info(
                f"Using documented conservative capability limit ({_CONSERVATIVE_UNKNOWN_LOCAL_CEILING} tokens) "
                f"for unlisted Private Local model '{clean_id}'."
            )
            return ModelCapability(
                model_id=clean_id,
                provider_id="ollama",
                family="ollama_local",
                source_type="local",
                context_window=_CONSERVATIVE_UNKNOWN_LOCAL_CEILING,
                recommended_budget=_CONSERVATIVE_UNKNOWN_LOCAL_CEILING,
                max_output_tokens=_CONSERVATIVE_UNKNOWN_LOCAL_OUTPUT,
                default_output_reserve=_CONSERVATIVE_UNKNOWN_LOCAL_OUTPUT,
                safety_margin_tokens=_CONSERVATIVE_UNKNOWN_LOCAL_SAFETY,
                token_counting_strategy="conservative_multilingual_upper_bound_v2",
                exact_tokenizer_available=False,
                supports_generation=True,
                supports_streaming=True,
                is_known_registry_model=False,
            )

    raise NotFoundError(
        f"Model '{clean_id}' is not registered with verified capability limits",
        code="UNKNOWN_MODEL_CAPABILITY",
    )


async def resolve_cloud_model_capability(
    user_id: UUID,
    *,
    requested_model_id: str | None = None,
    requested_provider_id: str | None = None,
    routing_mode: str = "auto",
    explicit_credential_id: UUID | None = None,
    conn: asyncpg.Connection | None = None,
) -> ModelCapability:
    """Resolve the effective ModelCapability for cloud RAG generation.

    If requested_model_id is specified, validates it directly against the registry.
    If omitted in 'auto'/'cloud_only' mode, probes eligible routing candidates for the
    student so the prompt budget strictly fits the highest-ranked available model.
    """
    if requested_model_id:
        return await get_model_capability(requested_model_id, inference_mode="cloud", conn=conn)

    # Probe eligible candidates using existing routing discovery + hard filters
    effective_mode = "cloud_only" if routing_mode == "auto" else routing_mode
    candidates = await discover_cloud_candidates(
        user_id=user_id,
        requested_provider_id=requested_provider_id,
        requested_model_id=requested_model_id,
        explicit_credential_id=explicit_credential_id,
    )
    if candidates:
        eligible, _ = filter_candidates(
            candidates=candidates,
            routing_mode=effective_mode,  # type: ignore[arg-type]
            community_opt_in=False,
            estimated_input_tokens=512,
            requested_max_output=min(context_settings.CONTEXT_OUTPUT_RESERVE, 2048),
            stream=False,
            requested_model_id=None,
        )
        if eligible:
            ranked = rank_candidates(
                eligible_candidates=eligible,
                estimated_input_tokens=512,
                estimated_output_tokens=min(context_settings.CONTEXT_OUTPUT_RESERVE, 2048),
                requested_model_id=DEFAULT_CLOUD_MODEL,
            )
            top = ranked[0]
            return await get_model_capability(top.model_id, inference_mode="cloud", conn=conn)

    # Fallback check if discovery returned candidates filtered by credential:
    # if no eligible candidates exist for the user at all, raise ConflictError/NotFoundError early
    # BEFORE generating embeddings or constructing prompts unless caller explicitly passed default.
    if requested_provider_id:
        raise NotFoundError(
            f"No eligible active credential or model found for provider '{requested_provider_id}'",
            code="NO_ELIGIBLE_CANDIDATE",
        )

    raise ConflictError(
        "No eligible cloud model or credential is currently available for generation",
        code="NO_ELIGIBLE_CANDIDATE",
    )
