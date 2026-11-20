"""Candidate discovery for cloud providers and community local inference nodes."""

import logging
from typing import Any, Literal
from uuid import UUID

from app.core.database import get_pool
from app.core.errors import AuthorizationError, ConflictError, NotFoundError
from app.modules.admission.cooldown import check_cooldown
from app.modules.routing.schemas import RoutingCandidate, RoutingMode

logger = logging.getLogger("ragbait.routing.discovery")


async def discover_cloud_candidates(
    user_id: UUID,
    requested_provider_id: str | None = None,
    requested_model_id: str | None = None,
    explicit_credential_id: UUID | None = None,
    credential_scope: Literal["eligible_pool", "owner_only"] = "eligible_pool",
) -> list[RoutingCandidate]:
    """Discover candidate cloud models and resolve authorized credentials for the user."""
    pool = get_pool()
    candidates: list[RoutingCandidate] = []

    async with pool.acquire() as conn:
        # 1. Fetch active models on enabled cloud providers
        model_query = """
            SELECT m.id AS model_id, m.provider_id, m.display_name, m.family,
                   m.release_stage, m.is_routing_alias, m.alias_target,
                   m.free_tier_eligible, m.input_cost_per_million_cents,
                   m.output_cost_per_million_cents, m.capabilities,
                   m.context_window, m.max_output_tokens, m.source_documentation,
                   m.updated_at, p.allows_community_sharing, p.is_enabled AS provider_enabled
            FROM public.llm_models m
            JOIN public.llm_providers p ON m.provider_id = p.id
            WHERE p.is_enabled = true
              AND p.runtime_type = 'cloud_api'
              AND m.availability_status = 'active'
              AND (m.shutdown_at IS NULL OR m.shutdown_at > now())
        """
        params: list[Any] = []
        if requested_provider_id:
            params.append(requested_provider_id)
            model_query += f" AND m.provider_id = ${len(params)}"
        if requested_model_id:
            params.append(requested_model_id)
            model_query += f" AND (m.id = ${len(params)} OR (m.is_routing_alias = true AND m.id = ${len(params)}))"

        model_rows = await conn.fetch(model_query, *params)
        if not model_rows:
            return []

        # 2. Cache credentials for the user and eligible community-shared credentials
        user_creds = await conn.fetch(
            """
            SELECT id, owner_id, provider_id, status, validation_status,
                   sharing_scope, shared_consent, account_scope_id, account_scope_verified
            FROM public.api_key_metadata
            WHERE owner_id = $1 AND status = 'active' AND validation_status = 'valid'
            ORDER BY created_at DESC
            """,
            user_id,
        )
        user_cred_by_provider: dict[str, dict[str, Any]] = {}
        for c in user_creds:
            p_id = c["provider_id"]
            if p_id not in user_cred_by_provider:
                user_cred_by_provider[p_id] = dict(c)

        shared_cred_by_provider: dict[str, dict[str, Any]] = {}
        if credential_scope != "owner_only":
            shared_creds = await conn.fetch(
                """
                SELECT id, owner_id, provider_id, status, validation_status,
                       sharing_scope, shared_consent, account_scope_id, account_scope_verified
                FROM public.api_key_metadata
                WHERE sharing_scope = 'community_shared'
                  AND shared_consent = true
                  AND status = 'active'
                  AND validation_status = 'valid'
                ORDER BY created_at ASC
                """
            )
            for sc in shared_creds:
                p_id = sc["provider_id"]
                if p_id not in shared_cred_by_provider:
                    shared_cred_by_provider[p_id] = dict(sc)

        # Explicit credential check if specified
        explicit_cred: dict[str, Any] | None = None
        if explicit_credential_id:
            row_explicit = await conn.fetchrow(
                """
                SELECT id, owner_id, provider_id, status, validation_status,
                       sharing_scope, shared_consent, account_scope_id, account_scope_verified
                FROM public.api_key_metadata
                WHERE id = $1
                """,
                explicit_credential_id,
            )
            if not row_explicit:
                raise NotFoundError(f"Credential '{explicit_credential_id}' not found")

            if row_explicit["status"] != "active":
                raise ConflictError(
                    f"Credential '{explicit_credential_id}' is not active (status: {row_explicit['status']})"
                )
            if row_explicit["validation_status"] != "valid":
                raise ConflictError(
                    f"Credential '{explicit_credential_id}' has not passed validation (status: {row_explicit['validation_status']})"
                )

            if requested_provider_id and row_explicit["provider_id"] != requested_provider_id:
                raise ConflictError(
                    f"Credential '{explicit_credential_id}' belongs to provider '{row_explicit['provider_id']}', not '{requested_provider_id}'"
                )
            if requested_model_id:
                model_check = await conn.fetchrow(
                    "SELECT provider_id FROM public.llm_models WHERE id = $1", requested_model_id
                )
                if model_check and model_check["provider_id"] != row_explicit["provider_id"]:
                    raise ConflictError(
                        f"Credential '{explicit_credential_id}' belongs to provider '{row_explicit['provider_id']}', not '{model_check['provider_id']}'"
                    )

            if credential_scope == "owner_only" and row_explicit["owner_id"] != user_id:
                raise AuthorizationError(
                    "Access denied: credential_scope is owner_only but explicit credential belongs to another user"
                )

            if row_explicit["owner_id"] != user_id:
                if (
                    row_explicit["sharing_scope"] != "community_shared"
                    or not row_explicit["shared_consent"]
                ):
                    raise AuthorizationError("Access denied: private owner-only credential")
                prov_row = await conn.fetchrow(
                    "SELECT allows_community_sharing FROM public.llm_providers WHERE id = $1",
                    row_explicit["provider_id"],
                )
                if prov_row and not prov_row["allows_community_sharing"]:
                    raise AuthorizationError(
                        f"Community credential dispatch is not permitted for provider '{row_explicit['provider_id']}' by administrative policy"
                    )

            explicit_cred = dict(row_explicit)

        # 3. Build candidate objects
        for m in model_rows:
            provider_id = m["provider_id"]
            selected_cred: dict[str, Any] | None = None
            is_shared = False

            if explicit_cred:
                if explicit_cred["provider_id"] == provider_id:
                    # Validate authorization on explicit credential
                    if explicit_cred["owner_id"] == user_id:
                        selected_cred = explicit_cred
                    elif (
                        credential_scope != "owner_only"
                        and explicit_cred["sharing_scope"] == "community_shared"
                        and explicit_cred["shared_consent"]
                        and m["allows_community_sharing"]
                    ):
                        selected_cred = explicit_cred
                        is_shared = True
                else:
                    continue
            elif provider_id in user_cred_by_provider:
                selected_cred = user_cred_by_provider[provider_id]
                is_shared = False
            elif (
                credential_scope != "owner_only"
                and m["allows_community_sharing"]
                and provider_id in shared_cred_by_provider
            ):
                selected_cred = shared_cred_by_provider[provider_id]
                is_shared = True

            # Missing or non-positive prices represented as None for metered models unless free_tier_eligible
            raw_in = m["input_cost_per_million_cents"]
            raw_out = m["output_cost_per_million_cents"]
            in_cost = (
                raw_in
                if raw_in is not None and raw_in > 0
                else (0 if m["free_tier_eligible"] else None)
            )
            out_cost = (
                raw_out
                if raw_out is not None and raw_out > 0
                else (0 if m["free_tier_eligible"] else None)
            )

            # Check cooldown status for this provider/model/credential
            is_cooling = False
            cooldown_rem = 0
            if selected_cred:
                cooldown_scopes = [
                    ("provider", provider_id),
                    ("model", m["model_id"]),
                    ("credential", str(selected_cred["id"])),
                ]
                try:
                    is_cooling, cooldown_rem, _ = await check_cooldown(cooldown_scopes)
                except Exception:
                    # Fail closed on redis outage during admission
                    pass

            candidates.append(
                RoutingCandidate(
                    source_type="cloud",
                    provider_id=provider_id,
                    model_id=m["model_id"],
                    display_name=m["display_name"],
                    family=m["family"],
                    release_stage=m["release_stage"],
                    capabilities=list(m["capabilities"] or []),
                    context_window=m["context_window"],
                    max_output_tokens=m["max_output_tokens"],
                    is_routing_alias=bool(m["is_routing_alias"]),
                    alias_target=m["alias_target"],
                    credential_id=selected_cred["id"] if selected_cred else None,
                    credential_owner_id=selected_cred["owner_id"] if selected_cred else None,
                    is_shared_credential=is_shared,
                    pricing_input_cents_per_million=in_cost,
                    pricing_output_cents_per_million=out_cost,
                    pricing_provenance="provider_published"
                    if m["source_documentation"]
                    else "estimated",
                    pricing_updated_at=m["updated_at"],
                    free_tier_eligible=bool(m["free_tier_eligible"]),
                    is_cooling_down=is_cooling,
                    cooldown_remaining_seconds=cooldown_rem,
                )
            )

    return candidates


async def discover_community_candidates(
    requested_model_id: str | None = None,
) -> list[RoutingCandidate]:
    """Community local inference candidate discovery is discontinued.

    Authoritative Product Policy:
    A student's local Ollama models and compute are strictly private and never shared.
    Community Ollama nodes are never discovered as route candidates.
    """
    return []


async def discover_all_candidates(
    user_id: UUID,
    routing_mode: RoutingMode,
    credential_scope: Literal["eligible_pool", "owner_only"] = "eligible_pool",
    community_opt_in: bool = False,
    requested_provider_id: str | None = None,
    requested_model_id: str | None = None,
    explicit_credential_id: UUID | None = None,
) -> list[RoutingCandidate]:
    """Unified entry point to gather all potential candidates based on routing mode.

    Under RagBait's product privacy policy, only eligible cloud credentials (user-owned
    and explicitly shared free-tier credentials) are eligible candidates.
    Local models are strictly private and never returned.
    """
    candidates: list[RoutingCandidate] = []

    # 1. Cloud discovery only
    if routing_mode in ("auto", "cloud_only", "provider_specific"):
        cloud_candidates = await discover_cloud_candidates(
            user_id=user_id,
            requested_provider_id=requested_provider_id,
            requested_model_id=requested_model_id,
            explicit_credential_id=explicit_credential_id,
            credential_scope=credential_scope,
        )
        candidates.extend(cloud_candidates)

    return candidates
