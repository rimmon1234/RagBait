"""Service layer for Provider, Model Registry, and Dynamic Quotas."""

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg

from app.core.database import get_admin_db_session, get_pool
from app.core.errors import ConflictError, NotFoundError
from app.modules.llm_gateway.schemas import (
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

logger = logging.getLogger("ragbait.llm_gateway")


def _row_to_provider(row: asyncpg.Record) -> LLMProviderResponse:
    return LLMProviderResponse(
        id=row["id"],
        name=row["name"],
        runtime_type=row["runtime_type"],
        description=row["description"],
        is_enabled=row["is_enabled"],
        requires_account_scope=row["requires_account_scope"],
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
