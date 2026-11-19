"""Service layer for Provider, Model Registry, and Dynamic Quotas."""

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.core.database import get_admin_db_session, get_pool
from app.core.errors import AuthorizationError, ConflictError, NotFoundError
from app.modules.admission.cooldown import set_cooldown
from app.modules.admission.ledger import mark_outcome_unknown_transactional
from app.modules.admission.schemas import AdmissionRequest, SettlementRequest
from app.modules.admission.service import (
    mark_dispatched,
    release_reservation,
    reserve_generation,
    settle_reservation,
)
from app.modules.credentials.repository import retrieve_decrypted_credential
from app.modules.llm_gateway.interfaces import NormalizedUsage
from app.modules.llm_gateway.providers.cerebras import CerebrasAdapter
from app.modules.llm_gateway.providers.gemini import GeminiAdapter
from app.modules.llm_gateway.providers.groq import GroqAdapter
from app.modules.llm_gateway.providers.openrouter import OpenRouterAdapter
from app.modules.llm_gateway.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    LLMModelCreate,
    LLMModelResponse,
    LLMModelUpdate,
    LLMProviderResponse,
    LLMProviderUpdate,
    ProviderQuotaCreate,
    ProviderQuotaResponse,
    ProviderQuotaUpdate,
    ProviderSyncResult,
    ResolvedModelInfo,
)
from app.modules.llm_gateway.streaming import format_sse_event
from app.modules.llm_gateway.token_estimation import estimate_generation_tokens

logger = logging.getLogger("ragbait.llm_gateway")


def _row_to_provider(row: asyncpg.Record) -> LLMProviderResponse:
    return LLMProviderResponse(
        id=row["id"],
        name=row["name"],
        runtime_type=row["runtime_type"],
        description=row["description"],
        is_enabled=row["is_enabled"],
        requires_account_scope=row["requires_account_scope"],
        allows_community_sharing=row["allows_community_sharing"]
        if "allows_community_sharing" in row
        else False,
        account_tier=row["account_tier"],
        billing_status=row["billing_status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_model(row: asyncpg.Record) -> LLMModelResponse:
    return LLMModelResponse(
        id=row["id"],
        provider_id=row["provider_id"],
        family=row["family"],
        display_name=row["display_name"],
        availability_status=row["availability_status"],
        release_stage=row["release_stage"],
        is_routing_alias=row["is_routing_alias"],
        alias_target=row["alias_target"],
        free_tier_eligible=row["free_tier_eligible"],
        input_cost_per_million_cents=row["input_cost_per_million_cents"],
        output_cost_per_million_cents=row["output_cost_per_million_cents"],
        capabilities=list(row["capabilities"]) if row["capabilities"] else ["streaming"],
        context_window=row["context_window"],
        max_output_tokens=row["max_output_tokens"],
        modalities_input=list(row["modalities_input"]) if row["modalities_input"] else ["text"],
        modalities_output=list(row["modalities_output"]) if row["modalities_output"] else ["text"],
        source_documentation=row["source_documentation"],
        last_verification_time=row["last_verification_time"],
        shutdown_at=row["shutdown_at"],
        replacement_model_id=row["replacement_model_id"],
        is_active=row["is_active"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_quota(row: asyncpg.Record) -> ProviderQuotaResponse:
    return ProviderQuotaResponse(
        id=row["id"],
        provider_id=row["provider_id"],
        scope_type=row["scope_type"],
        scope_id=row["scope_id"],
        model_id=row["model_id"],
        limit_type=row["limit_type"],
        limit_value=row["limit_value"],
        source_documentation=row["source_documentation"],
        last_verification_time=row["last_verification_time"],
        confidence=row["confidence"],
        provenance=row["provenance"],
        is_active=row["is_active"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ------------------------------------------------------------------------------
# Provider Operations
# ------------------------------------------------------------------------------
async def list_providers(only_enabled: bool = False) -> list[LLMProviderResponse]:
    """Retrieve all registered providers."""
    pool = get_pool()
    query = """
        SELECT id, name, runtime_type, description, is_enabled,
               requires_account_scope, account_tier, billing_status,
               created_at, updated_at
        FROM public.llm_providers
    """
    if only_enabled:
        query += " WHERE is_enabled = true"
    query += " ORDER BY name ASC"

    async with pool.acquire() as conn:
        rows = await conn.fetch(query)

    return [_row_to_provider(r) for r in rows]


async def get_provider(provider_id: str) -> LLMProviderResponse:
    """Retrieve single provider by id."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, name, runtime_type, description, is_enabled,
                   requires_account_scope, account_tier, billing_status,
                   created_at, updated_at
            FROM public.llm_providers
            WHERE id = $1
            """,
            provider_id,
        )

    if not row:
        raise NotFoundError(f"Provider '{provider_id}' not found", code="PROVIDER_NOT_FOUND")

    return _row_to_provider(row)


async def update_provider(
    provider_id: str, data: LLMProviderUpdate, actor_id: UUID
) -> LLMProviderResponse:
    """Update provider settings with administrative audit logging."""
    await get_provider(provider_id)  # Confirm existence

    updates: list[str] = []
    params: list[Any] = [provider_id]

    if data.name is not None:
        params.append(data.name)
        updates.append(f"name = ${len(params)}")
    if data.description is not None:
        params.append(data.description)
        updates.append(f"description = ${len(params)}")
    if data.is_enabled is not None:
        params.append(data.is_enabled)
        updates.append(f"is_enabled = ${len(params)}")
    if data.requires_account_scope is not None:
        params.append(data.requires_account_scope)
        updates.append(f"requires_account_scope = ${len(params)}")
    if data.account_tier is not None:
        params.append(data.account_tier)
        updates.append(f"account_tier = ${len(params)}")
    if data.billing_status is not None:
        params.append(data.billing_status)
        updates.append(f"billing_status = ${len(params)}")

    if not updates:
        return await get_provider(provider_id)

    async with get_admin_db_session() as conn:
        set_clause = ", ".join(updates)
        row = await conn.fetchrow(
            f"""
            UPDATE public.llm_providers
            SET {set_clause}, updated_at = now()
            WHERE id = $1
            RETURNING id, name, runtime_type, description, is_enabled,
                      requires_account_scope, account_tier, billing_status,
                      created_at, updated_at
            """,
            *params,
        )

        payload = json.dumps(
            {"provider_id": provider_id, "changes": data.model_dump(exclude_unset=True)}
        )
        await conn.execute(
            """
            INSERT INTO public.audit_logs (actor_id, action, target_type, target_id, payload)
            VALUES ($1, 'llm_provider.update', 'llm_provider', NULL, $2::jsonb)
            """,
            actor_id,
            payload,
        )

    return _row_to_provider(row)


# ------------------------------------------------------------------------------
# Model Registry Operations
# ------------------------------------------------------------------------------
async def list_models(
    provider_id: str | None = None,
    availability_status: str | None = None,
    active_only: bool = True,
) -> list[LLMModelResponse]:
    """Retrieve models from the administrator-controlled registry."""
    pool = get_pool()
    conditions: list[str] = []
    params: list[Any] = []

    if active_only:
        conditions.append("is_active = true")
    elif availability_status:
        params.append(availability_status)
        conditions.append(f"availability_status = ${len(params)}")

    if provider_id:
        params.append(provider_id)
        conditions.append(f"provider_id = ${len(params)}")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"""
        SELECT id, provider_id, family, display_name, availability_status,
               release_stage, is_routing_alias, alias_target, free_tier_eligible,
               input_cost_per_million_cents, output_cost_per_million_cents,
               capabilities, context_window, max_output_tokens,
               modalities_input, modalities_output, source_documentation,
               last_verification_time, shutdown_at, replacement_model_id,
               is_active, created_at, updated_at
        FROM public.llm_models
        {where_clause}
        ORDER BY display_name ASC
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)

    return [_row_to_model(r) for r in rows]


async def get_model(model_id: str) -> LLMModelResponse:
    """Retrieve single model by ID."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, provider_id, family, display_name, availability_status,
                   release_stage, is_routing_alias, alias_target, free_tier_eligible,
                   input_cost_per_million_cents, output_cost_per_million_cents,
                   capabilities, context_window, max_output_tokens,
                   modalities_input, modalities_output, source_documentation,
                   last_verification_time, shutdown_at, replacement_model_id,
                   is_active, created_at, updated_at
            FROM public.llm_models
            WHERE id = $1
            """,
            model_id,
        )

    if not row:
        raise NotFoundError(f"Model '{model_id}' not found", code="MODEL_NOT_FOUND")

    return _row_to_model(row)


async def create_model(data: LLMModelCreate, actor_id: UUID) -> LLMModelResponse:
    """Register a new model in the catalog."""
    await get_provider(data.provider_id)  # Validate provider exists

    pool = get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval("SELECT id FROM public.llm_models WHERE id = $1", data.id)
        if existing:
            raise ConflictError(
                f"Model with identifier '{data.id}' already exists", code="MODEL_ALREADY_EXISTS"
            )

    async with get_admin_db_session() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.llm_models (
                id, provider_id, family, display_name, availability_status, release_stage,
                is_routing_alias, alias_target, free_tier_eligible,
                input_cost_per_million_cents, output_cost_per_million_cents,
                capabilities, context_window, max_output_tokens,
                modalities_input, modalities_output, source_documentation
            )
            VALUES (
                $1, $2, $3, $4, 'active', $5,
                $6, $7, $8,
                $9, $10,
                $11, $12, $13,
                $14, $15, $16
            )
            RETURNING id, provider_id, family, display_name, availability_status,
                      release_stage, is_routing_alias, alias_target, free_tier_eligible,
                      input_cost_per_million_cents, output_cost_per_million_cents,
                      capabilities, context_window, max_output_tokens,
                      modalities_input, modalities_output, source_documentation,
                      last_verification_time, shutdown_at, replacement_model_id,
                      is_active, created_at, updated_at
            """,
            data.id,
            data.provider_id,
            data.family,
            data.display_name,
            data.release_stage,
            data.is_routing_alias,
            data.alias_target,
            data.free_tier_eligible,
            data.input_cost_per_million_cents,
            data.output_cost_per_million_cents,
            data.capabilities,
            data.context_window,
            data.max_output_tokens,
            data.modalities_input,
            data.modalities_output,
            data.source_documentation,
        )

        payload = json.dumps({"model_id": data.id, "provider_id": data.provider_id})
        await conn.execute(
            """
            INSERT INTO public.audit_logs (actor_id, action, target_type, target_id, payload)
            VALUES ($1, 'llm_model.create', 'llm_model', NULL, $2::jsonb)
            """,
            actor_id,
            payload,
        )

    return _row_to_model(row)


async def update_model(model_id: str, data: LLMModelUpdate, actor_id: UUID) -> LLMModelResponse:
    """Update model configuration, availability status, or deprecation metadata."""
    await get_model(model_id)

    if data.replacement_model_id is not None:
        await get_model(data.replacement_model_id)

    updates: list[str] = []
    params: list[Any] = [model_id]

    if data.display_name is not None:
        params.append(data.display_name)
        updates.append(f"display_name = ${len(params)}")
    if data.family is not None:
        params.append(data.family)
        updates.append(f"family = ${len(params)}")
    if data.availability_status is not None:
        params.append(data.availability_status)
        updates.append(f"availability_status = ${len(params)}")
    if data.release_stage is not None:
        params.append(data.release_stage)
        updates.append(f"release_stage = ${len(params)}")
    if data.is_routing_alias is not None:
        params.append(data.is_routing_alias)
        updates.append(f"is_routing_alias = ${len(params)}")
    if data.alias_target is not None:
        params.append(data.alias_target)
        updates.append(f"alias_target = ${len(params)}")
    if data.free_tier_eligible is not None:
        params.append(data.free_tier_eligible)
        updates.append(f"free_tier_eligible = ${len(params)}")
    if data.input_cost_per_million_cents is not None:
        params.append(data.input_cost_per_million_cents)
        updates.append(f"input_cost_per_million_cents = ${len(params)}")
    if data.output_cost_per_million_cents is not None:
        params.append(data.output_cost_per_million_cents)
        updates.append(f"output_cost_per_million_cents = ${len(params)}")
    if data.capabilities is not None:
        params.append(data.capabilities)
        updates.append(f"capabilities = ${len(params)}")
    if data.context_window is not None:
        params.append(data.context_window)
        updates.append(f"context_window = ${len(params)}")
    if data.max_output_tokens is not None:
        params.append(data.max_output_tokens)
        updates.append(f"max_output_tokens = ${len(params)}")
    if data.modalities_input is not None:
        params.append(data.modalities_input)
        updates.append(f"modalities_input = ${len(params)}")
    if data.modalities_output is not None:
        params.append(data.modalities_output)
        updates.append(f"modalities_output = ${len(params)}")
    if data.source_documentation is not None:
        params.append(data.source_documentation)
        updates.append(f"source_documentation = ${len(params)}")
    if data.shutdown_at is not None:
        params.append(data.shutdown_at)
        updates.append(f"shutdown_at = ${len(params)}")
    if data.replacement_model_id is not None:
        params.append(data.replacement_model_id)
        updates.append(f"replacement_model_id = ${len(params)}")

    if not updates:
        return await get_model(model_id)

    async with get_admin_db_session() as conn:
        set_clause = ", ".join(updates)
        row = await conn.fetchrow(
            f"""
            UPDATE public.llm_models
            SET {set_clause}, last_verification_time = now(), updated_at = now()
            WHERE id = $1
            RETURNING id, provider_id, family, display_name, availability_status,
                      release_stage, is_routing_alias, alias_target, free_tier_eligible,
                      input_cost_per_million_cents, output_cost_per_million_cents,
                      capabilities, context_window, max_output_tokens,
                      modalities_input, modalities_output, source_documentation,
                      last_verification_time, shutdown_at, replacement_model_id,
                      is_active, created_at, updated_at
            """,
            *params,
        )

        payload = json.dumps(
            {"model_id": model_id, "changes": data.model_dump(mode="json", exclude_unset=True)}
        )
        await conn.execute(
            """
            INSERT INTO public.audit_logs (actor_id, action, target_type, target_id, payload)
            VALUES ($1, 'llm_model.update', 'llm_model', NULL, $2::jsonb)
            """,
            actor_id,
            payload,
        )

    return _row_to_model(row)


# ------------------------------------------------------------------------------
# Dynamic Quota Operations
# ------------------------------------------------------------------------------
async def list_quotas(
    provider_id: str | None = None,
    scope_id: str | None = None,
    model_id: str | None = None,
    active_only: bool = True,
) -> list[ProviderQuotaResponse]:
    """Retrieve dynamic provider quotas with full provenance records."""
    pool = get_pool()
    conditions: list[str] = []
    params: list[Any] = []

    if active_only:
        conditions.append("is_active = true")

    if provider_id:
        params.append(provider_id)
        conditions.append(f"provider_id = ${len(params)}")

    if scope_id:
        params.append(scope_id)
        conditions.append(f"scope_id = ${len(params)}")

    if model_id is not None:
        params.append(model_id)
        conditions.append(f"(model_id = ${len(params)} OR model_id IS NULL)")

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    query = f"""
        SELECT id, provider_id, scope_type, scope_id, model_id, limit_type, limit_value,
               source_documentation, last_verification_time, confidence, provenance,
               is_active, created_at, updated_at
        FROM public.provider_quotas
        {where_clause}
        ORDER BY provider_id ASC, limit_type ASC
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)

    return [_row_to_quota(r) for r in rows]


async def get_quota(quota_id: UUID) -> ProviderQuotaResponse:
    """Retrieve single quota rule by ID."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, provider_id, scope_type, scope_id, model_id, limit_type, limit_value,
                   source_documentation, last_verification_time, confidence, provenance,
                   is_active, created_at, updated_at
            FROM public.provider_quotas
            WHERE id = $1
            """,
            quota_id,
        )

    if not row:
        raise NotFoundError(f"Quota rule '{quota_id}' not found", code="QUOTA_NOT_FOUND")

    return _row_to_quota(row)


async def create_quota(data: ProviderQuotaCreate, actor_id: UUID) -> ProviderQuotaResponse:
    """Create a new dynamic quota rule."""
    await get_provider(data.provider_id)
    if data.model_id:
        await get_model(data.model_id)

    async with get_admin_db_session() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO public.provider_quotas (
                    provider_id, scope_type, scope_id, model_id, limit_type, limit_value,
                    source_documentation, confidence, provenance, is_active
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING id, provider_id, scope_type, scope_id, model_id, limit_type, limit_value,
                          source_documentation, last_verification_time, confidence, provenance,
                          is_active, created_at, updated_at
                """,
                data.provider_id,
                data.scope_type,
                data.scope_id,
                data.model_id,
                data.limit_type,
                data.limit_value,
                data.source_documentation,
                data.confidence,
                data.provenance,
                data.is_active,
            )
        except asyncpg.UniqueViolationError as err:
            raise ConflictError(
                f"A quota rule for provider '{data.provider_id}' on scope '{data.scope_id}' with limit '{data.limit_type}' already exists",
                code="QUOTA_RULE_EXISTS",
            ) from err

        payload = json.dumps({"quota_id": str(row["id"]), "provider_id": data.provider_id})
        await conn.execute(
            """
            INSERT INTO public.audit_logs (actor_id, action, target_type, target_id, payload)
            VALUES ($1, 'provider_quota.create', 'provider_quota', $2, $3::jsonb)
            """,
            actor_id,
            row["id"],
            payload,
        )

    return _row_to_quota(row)


async def update_quota(
    quota_id: UUID, data: ProviderQuotaUpdate, actor_id: UUID
) -> ProviderQuotaResponse:
    """Update dynamic quota limit value, confidence, or provenance."""
    await get_quota(quota_id)

    updates: list[str] = []
    params: list[Any] = [quota_id]

    if data.limit_value is not None:
        params.append(data.limit_value)
        updates.append(f"limit_value = ${len(params)}")
    if data.source_documentation is not None:
        params.append(data.source_documentation)
        updates.append(f"source_documentation = ${len(params)}")
    if data.confidence is not None:
        params.append(data.confidence)
        updates.append(f"confidence = ${len(params)}")
    if data.provenance is not None:
        params.append(data.provenance)
        updates.append(f"provenance = ${len(params)}")
    if data.is_active is not None:
        params.append(data.is_active)
        updates.append(f"is_active = ${len(params)}")

    if not updates:
        return await get_quota(quota_id)

    async with get_admin_db_session() as conn:
        set_clause = ", ".join(updates)
        row = await conn.fetchrow(
            f"""
            UPDATE public.provider_quotas
            SET {set_clause}, last_verification_time = now(), updated_at = now()
            WHERE id = $1
            RETURNING id, provider_id, scope_type, scope_id, model_id, limit_type, limit_value,
                      source_documentation, last_verification_time, confidence, provenance,
                      is_active, created_at, updated_at
            """,
            *params,
        )

        payload = json.dumps(
            {"quota_id": str(quota_id), "changes": data.model_dump(mode="json", exclude_unset=True)}
        )
        await conn.execute(
            """
            INSERT INTO public.audit_logs (actor_id, action, target_type, target_id, payload)
            VALUES ($1, 'provider_quota.update', 'provider_quota', $2, $3::jsonb)
            """,
            actor_id,
            quota_id,
            payload,
        )

    return _row_to_quota(row)


# ------------------------------------------------------------------------------
# Routing Alias Resolution
# ------------------------------------------------------------------------------
def resolve_model_identity(
    model: LLMModelResponse,
    provider_response_model: str | None = None,
    upstream_provider: str | None = None,
) -> ResolvedModelInfo:
    """Resolve requested model and upstream provider for routing aliases and standard models.

    For dynamic aliases (e.g., 'openrouter/free'):
    - The caller requests model.id ('openrouter/free').
    - The actual upstream model executed is extracted from the provider response
      (or falls back to model.alias_target if explicitly specified).
    - The actual upstream provider is captured (defaulting to model.provider_id).
    """
    if model.is_routing_alias:
        actual_model = provider_response_model or model.alias_target or model.id
        actual_provider = upstream_provider or model.provider_id
        return ResolvedModelInfo(
            requested_model_id=model.id,
            is_routing_alias=True,
            resolved_model_id=actual_model,
            upstream_provider=actual_provider,
        )

    return ResolvedModelInfo(
        requested_model_id=model.id,
        is_routing_alias=False,
        resolved_model_id=model.id,
        upstream_provider=model.provider_id,
    )


# ------------------------------------------------------------------------------
# Discovery & Synchronization
# ------------------------------------------------------------------------------
async def sync_provider_models(
    provider_id: str,
    actor_id: UUID,
    discovered_models: list[dict[str, Any]] | None = None,
) -> ProviderSyncResult:
    """Execute model discovery and synchronization for a provider.

    Verifies existing models and records newly discovered models without automatically
    activating unverified models. Newly discovered models are recorded with status 'disabled'
    and require explicit administrator review before activation.

    Existing administrator configurations (display names, lifecycle statuses, pricing,
    replacements) are strictly preserved and never overwritten by sync operations.
    """
    await get_provider(provider_id)
    existing_models = await list_models(provider_id=provider_id, active_only=False)
    existing_ids = {m.id for m in existing_models}

    newly_added: list[str] = []

    async with get_admin_db_session() as conn:
        if discovered_models:
            for disc in discovered_models:
                m_id = disc["id"]
                if m_id not in existing_ids:
                    # Newly discovered model: quarantine in disabled state
                    await conn.execute(
                        """
                        INSERT INTO public.llm_models (
                            id, provider_id, family, display_name, availability_status,
                            release_stage, is_routing_alias, alias_target, free_tier_eligible,
                            input_cost_per_million_cents, output_cost_per_million_cents,
                            capabilities, context_window, max_output_tokens,
                            modalities_input, modalities_output, source_documentation
                        )
                        VALUES ($1, $2, $3, $4, 'disabled', $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
                        """,
                        m_id,
                        provider_id,
                        disc.get("family", "unknown"),
                        disc.get("display_name", m_id),
                        disc.get("release_stage", "preview"),
                        disc.get("is_routing_alias", False),
                        disc.get("alias_target", None),
                        disc.get("free_tier_eligible", False),
                        disc.get("input_cost_per_million_cents", 0),
                        disc.get("output_cost_per_million_cents", 0),
                        disc.get("capabilities", ["streaming"]),
                        disc.get("context_window", 4096),
                        disc.get("max_output_tokens", 2048),
                        disc.get("modalities_input", ["text"]),
                        disc.get("modalities_output", ["text"]),
                        disc.get("source_documentation", None),
                    )
                    newly_added.append(m_id)
                else:
                    # Existing model: DO NOT overwrite admin metadata. Only touch verification timestamp.
                    await conn.execute(
                        """
                        UPDATE public.llm_models
                        SET last_verification_time = now(), updated_at = now()
                        WHERE id = $1
                        """,
                        m_id,
                    )
        else:
            # Baseline timestamp verification for all provider models
            await conn.execute(
                """
                UPDATE public.llm_models
                SET last_verification_time = now(), updated_at = now()
                WHERE provider_id = $1
                """,
                provider_id,
            )

        # Audit sync action
        payload = json.dumps(
            {
                "provider_id": provider_id,
                "models_checked": len(existing_models) + len(newly_added),
                "unverified_discovered_count": len(newly_added),
                "unverified_model_ids": newly_added,
            }
        )
        await conn.execute(
            """
            INSERT INTO public.audit_logs (actor_id, action, target_type, target_id, payload)
            VALUES ($1, 'llm_provider.sync', 'llm_provider', NULL, $2::jsonb)
            """,
            actor_id,
            payload,
        )

    # Reload model catalogue to construct sync response
    models = await list_models(provider_id=provider_id, active_only=False)
    active_count = sum(1 for m in models if m.is_active)
    deprecated_count = sum(1 for m in models if m.availability_status in ("deprecated", "shutdown"))

    details = [
        {
            "model_id": m.id,
            "display_name": m.display_name,
            "status": m.availability_status,
            "release_stage": m.release_stage,
            "last_verified": m.last_verification_time.isoformat(),
        }
        for m in models
    ]

    return ProviderSyncResult(
        provider_id=provider_id,
        models_discovered=len(models),
        models_active=active_count,
        models_deprecated=deprecated_count,
        unverified_discovered=newly_added,
        details=details,
    )


# ------------------------------------------------------------------------------
# Unified Inference Gateway Dispatch Lifecycle
# ------------------------------------------------------------------------------
def _get_provider_adapter(provider_id: str) -> Any:
    """Instantiate the corresponding provider adapter."""
    if provider_id == "gemini":
        return GeminiAdapter()
    if provider_id == "groq":
        return GroqAdapter()
    if provider_id == "openrouter":
        return OpenRouterAdapter()
    if provider_id == "cerebras":
        return CerebrasAdapter()
    raise ConflictError(f"Unsupported inference provider '{provider_id}'")


async def dispatch_chat_completion(
    user_id: UUID,
    req: ChatCompletionRequest,
    correlation_id: str | None = None,
) -> tuple[AsyncIterator[str] | ChatCompletionResponse, bool]:
    """Execute the full 10-step unified inference gateway lifecycle.

    Returns:
        tuple[stream_generator_or_response, is_streaming]
    """
    # 1. Provider & Model Registry Validation
    provider = await get_provider(req.provider_id)
    if not provider.is_enabled:
        raise ConflictError(f"Provider '{req.provider_id}' is currently disabled")

    model = await get_model(req.model_id)
    if model.provider_id != req.provider_id:
        raise ConflictError(
            f"Model '{req.model_id}' belongs to provider '{model.provider_id}', not '{req.provider_id}'"
        )
    if model.availability_status != "active":
        raise ConflictError(
            f"Model '{req.model_id}' is not active (status: {model.availability_status})"
        )
    if model.shutdown_at and model.shutdown_at <= datetime.now(UTC):
        raise ConflictError(f"Model '{req.model_id}' has been shut down and retired")

    # 2. Credential Authorization & Discovery
    pool = get_pool()
    cred_id = req.credential_id
    cred_row: dict[str, Any] | None = None

    async with pool.acquire() as conn:
        if cred_id is not None:
            row = await conn.fetchrow(
                """
                SELECT id, owner_id, provider_id, status, validation_status,
                       sharing_scope, shared_consent, account_scope_id, account_scope_verified
                FROM public.api_key_metadata
                WHERE id = $1
                """,
                cred_id,
            )
            if not row:
                raise NotFoundError(f"Credential '{cred_id}' not found")
            cred_row = dict(row)
        else:
            # Auto-discover user's own active, validated credential for this provider
            row = await conn.fetchrow(
                """
                SELECT id, owner_id, provider_id, status, validation_status,
                       sharing_scope, shared_consent, account_scope_id, account_scope_verified
                FROM public.api_key_metadata
                WHERE owner_id = $1 AND provider_id = $2
                  AND status = 'active' AND validation_status = 'valid'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                user_id,
                req.provider_id,
            )
            if row:
                cred_id = row["id"]
                cred_row = dict(row)
            elif provider.allows_community_sharing:
                # Gated community credential pool
                row_shared = await conn.fetchrow(
                    """
                    SELECT id, owner_id, provider_id, status, validation_status,
                           sharing_scope, shared_consent, account_scope_id, account_scope_verified
                    FROM public.api_key_metadata
                    WHERE provider_id = $1 AND sharing_scope = 'community_shared'
                      AND shared_consent = true AND status = 'active' AND validation_status = 'valid'
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    req.provider_id,
                )
                if row_shared:
                    cred_id = row_shared["id"]
                    cred_row = dict(row_shared)

    if not cred_row or cred_id is None:
        raise NotFoundError(
            f"No eligible active API credential available for provider '{req.provider_id}'. "
            "Please register an API key in Credential Management."
        )

    # Enforce credential state
    if cred_row["status"] != "active":
        raise ConflictError(f"Credential '{cred_id}' is not active (status: {cred_row['status']})")
    if cred_row["validation_status"] != "valid":
        raise ConflictError(
            f"Credential '{cred_id}' has not passed validation (status: {cred_row['validation_status']})"
        )

    # Ownership & Community Sharing Verification
    if cred_row["owner_id"] != user_id:
        if cred_row["sharing_scope"] != "community_shared" or not cred_row["shared_consent"]:
            raise AuthorizationError("Access denied: private owner-only credential")
        if not provider.allows_community_sharing:
            raise AuthorizationError(
                f"Community credential dispatch is not permitted for provider '{req.provider_id}' by administrative policy"
            )

    # 3. Server-side Token Estimation
    messages_payload = [{"role": m.role, "content": m.content} for m in req.messages]
    token_estimate = estimate_generation_tokens(
        messages=messages_payload,
        model_context_window=model.context_window,
        model_max_output_tokens=model.max_output_tokens,
        requested_max_output=req.params.max_output_tokens,
    )

    # 4. Multi-Tier Admission Reservation (Rate Limiting, Concurrency, TPD, Budget)
    request_id = correlation_id or f"req-{uuid4().hex[:12]}"
    admission_request = AdmissionRequest(
        user_id=user_id,
        credential_id=cred_id,
        provider_id=req.provider_id,
        model_id=req.model_id,
        estimated_input_tokens=token_estimate.estimated_input_tokens,
        max_output_tokens=token_estimate.reserved_output_tokens,
        idempotency_key=req.idempotency_key,
        request_id=request_id,
    )
    reservation = await reserve_generation(admission_request)

    # 5. Just-in-Time Vault Credential Decryption
    try:
        decrypted = await retrieve_decrypted_credential(
            credential_id=cred_id,
            caller_id=cred_row["owner_id"],
            check_active=True,
        )
        raw_key = decrypted.raw_key
    except Exception as exc:
        # Decryption failed before dispatch: refund uncommitted reservation
        await release_reservation(reservation.reservation_id, reason="vault_decryption_failed")
        raise ConflictError(f"Failed to decrypt vault credential: {exc}") from exc

    # 6. Durable Dispatch Commitment (Immediate mark_dispatched before network call)
    await mark_dispatched(reservation.reservation_id)

    # 7. Select Upstream Adapter
    adapter = _get_provider_adapter(req.provider_id)

    # 8. Dispatch Execution (Streaming vs Non-Streaming)
    if req.stream:

        async def stream_generator() -> AsyncIterator[str]:
            yield format_sse_event(
                "generation_started",
                {
                    "request_id": request_id,
                    "reservation_id": str(reservation.reservation_id),
                    "model_id": req.model_id,
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )

            collected_text = ""
            last_finish_reason = "stop"
            usage_received: NormalizedUsage | None = None
            resolved_model = req.model_id
            upstream_gen_id: str | None = None

            try:
                async for chunk in adapter.stream(
                    model_id=req.model_id,
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

                # If upstream provider omitted trailing usage frame, conservatively derive usage
                if not usage_received:
                    output_tokens = (
                        max(1, int(len(collected_text) / 3.2) + 1) if collected_text else 0
                    )
                    usage_received = NormalizedUsage(
                        input_tokens=token_estimate.estimated_input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=token_estimate.estimated_input_tokens + output_tokens,
                        cached_tokens=0,
                        reasoning_tokens=0,
                        usage_source="derived",
                    )

                # Settle usage accounting
                settlement = await settle_reservation(
                    reservation_id=reservation.reservation_id,
                    req=SettlementRequest(
                        actual_input_tokens=usage_received.input_tokens,
                        actual_output_tokens=usage_received.output_tokens,
                        actual_cost_microcents=None,  # Automatically computed from model pricing
                        resolved_model_id=resolved_model,
                        upstream_provider=req.provider_id,
                        upstream_generation_id=upstream_gen_id,
                        usage_source=usage_received.usage_source,
                    ),
                    model_id=resolved_model,
                    resolved_model_id=resolved_model,
                    upstream_provider=req.provider_id,
                    upstream_generation_id=upstream_gen_id,
                    usage_source=usage_received.usage_source,
                )

                # Emit usage frame
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

                # Emit terminal completion frame
                yield format_sse_event(
                    "generation_completed",
                    {
                        "request_id": request_id,
                        "finish_reason": last_finish_reason,
                    },
                )

            except Exception as exc:
                logger.warning(f"Mid-stream generation error during {request_id}: {exc}")
                err_category = getattr(
                    exc,
                    "category",
                    "upstream_timeout"
                    if isinstance(exc, TimeoutError)
                    else "unknown_provider_error",
                )

                # If provider returned 429 Retry-After, activate Redis cooldown
                retry_after = getattr(exc, "retry_after", None)
                if retry_after:
                    try:
                        await set_cooldown(
                            scope_type="provider",
                            scope_id=req.provider_id,
                            retry_after_seconds=retry_after,
                            reason=f"Provider {req.provider_id} returned HTTP 429",
                        )
                    except Exception:
                        pass

                # Preserve conservative reservation in outcome_unknown status
                try:
                    await mark_outcome_unknown_transactional(
                        reservation.reservation_id,
                        reason=err_category,
                    )
                except Exception:
                    pass

                # Emit normalized error frame to client
                yield format_sse_event(
                    "generation_error",
                    {
                        "request_id": request_id,
                        "error_code": getattr(exc, "code", "GENERATION_FAILED"),
                        "message": str(exc),
                    },
                )

        return stream_generator(), True

    # Non-streaming execution
    try:
        gen_result = await adapter.generate(
            model_id=req.model_id,
            messages=messages_payload,
            raw_key=raw_key,
            temperature=req.params.temperature,
            max_output_tokens=token_estimate.reserved_output_tokens,
            top_p=req.params.top_p,
        )
    except Exception as exc:
        err_category = getattr(
            exc,
            "category",
            "upstream_timeout" if isinstance(exc, TimeoutError) else "connection_failure",
        )
        retry_after = getattr(exc, "retry_after", None)
        if retry_after:
            try:
                await set_cooldown(
                    scope_type="provider",
                    scope_id=req.provider_id,
                    retry_after_seconds=retry_after,
                    reason=f"Provider {req.provider_id} returned HTTP 429",
                )
            except Exception:
                pass

        try:
            await mark_outcome_unknown_transactional(
                reservation.reservation_id,
                reason=err_category,
            )
        except Exception:
            pass
        raise

    # Settle non-streaming generation
    settlement = await settle_reservation(
        reservation_id=reservation.reservation_id,
        req=SettlementRequest(
            actual_input_tokens=gen_result.usage.input_tokens,
            actual_output_tokens=gen_result.usage.output_tokens,
            actual_cost_microcents=None,
            resolved_model_id=gen_result.resolved_model,
            upstream_provider=req.provider_id,
            upstream_generation_id=gen_result.upstream_generation_id,
            usage_source=gen_result.usage.usage_source,
        ),
        model_id=gen_result.resolved_model,
        resolved_model_id=gen_result.resolved_model,
        upstream_provider=req.provider_id,
        upstream_generation_id=gen_result.upstream_generation_id,
        usage_source=gen_result.usage.usage_source,
    )

    response_payload = ChatCompletionResponse(
        id=gen_result.upstream_generation_id or f"gen-{uuid4().hex[:12]}",
        request_id=request_id,
        reservation_id=reservation.reservation_id,
        provider_id=req.provider_id,
        model_id=req.model_id,
        resolved_model_id=gen_result.resolved_model,
        upstream_provider=req.provider_id,
        upstream_generation_id=gen_result.upstream_generation_id,
        content=gen_result.content,
        finish_reason=gen_result.finish_reason,
        usage=gen_result.usage.model_dump(),
        cost_microcents=settlement.actual_cost_microcents,
        cost_cents=settlement.actual_cost_cents,
        currency="USD",
        created_at=datetime.now(UTC),
    )

    return response_payload, False
