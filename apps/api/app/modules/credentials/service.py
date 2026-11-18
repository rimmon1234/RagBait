"""Service Layer for Contributor API Credential Management."""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.database import get_pool
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.modules.credentials.audit import log_credential_audit
from app.modules.credentials.errors import (
    VaultAuthorizationError,
    VaultDecryptionError,
)
from app.modules.credentials.repository import (
    delete_credential_secret,
    get_credential_metadata,
    list_credentials_by_owner,
    retrieve_decrypted_credential,
    set_credential_status,
    store_credential,
    update_credential_metadata,
    update_validation_status,
)
from app.modules.credentials.schemas import (
    CredentialCreateRequest,
    CredentialListResponse,
    CredentialResponse,
    CredentialUpdateRequest,
    StoredCredentialResult,
    ValidationResultResponse,
)
from app.modules.credentials.validator import validate_provider_credential

logger = logging.getLogger("ragbait.credentials.service")


async def _get_provider_details(provider_id: str) -> dict[str, Any]:
    """Fetch provider availability and community sharing approval status."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, name, is_enabled, allows_community_sharing
            FROM public.llm_providers
            WHERE id = $1
            """,
            provider_id.lower(),
        )
    if not row:
        raise NotFoundError(f"Provider '{provider_id}' is not recognized or supported")
    return dict(row)


def _to_credential_response(
    res: StoredCredentialResult, provider_name: str | None = None
) -> CredentialResponse:
    """Map StoredCredentialResult to safe public CredentialResponse."""
    return CredentialResponse(
        id=res.id,
        owner_id=res.owner_id,
        provider_id=res.provider_id,
        provider_name=provider_name,
        nickname=res.nickname,
        masked_key=res.masked_key,
        status=res.status,
        sharing_scope=res.sharing_scope,
        shared_consent=res.shared_consent,
        sharing_consent_timestamp=res.sharing_consent_timestamp,
        consent_version=res.consent_version,
        validation_status=res.validation_status,
        last_validated_at=res.last_validated_at,
        validation_error_code=res.validation_error_code,
        account_scope_id=res.account_scope_id,
        daily_request_limit=res.daily_request_limit,
        monthly_request_limit=res.monthly_request_limit,
        daily_spending_limit_cents=res.daily_spending_limit_cents,
        monthly_spending_limit_cents=res.monthly_spending_limit_cents,
        created_at=res.created_at,
        updated_at=res.updated_at,
    )


async def create_user_credential(
    owner_id: UUID,
    payload: CredentialCreateRequest,
) -> CredentialResponse:
    """Store and encrypt a new API credential for the authenticated user."""
    provider = await _get_provider_details(payload.provider_id)
    if not provider["is_enabled"]:
        raise ConflictError(f"Provider '{payload.provider_id}' is currently disabled")

    # Sharing scope & explicit consent evaluation
    consent_timestamp: datetime | None = None
    consent_version: str | None = None
    sharing_scope = payload.sharing_scope
    shared_consent = payload.shared_consent

    if sharing_scope == "community_shared":
        if not provider.get("allows_community_sharing", False):
            raise ConflictError(
                f"Provider '{payload.provider_id}' is not approved for community sharing"
            )
        if not shared_consent:
            raise ValidationError(
                "Explicit consent must be granted when selecting community sharing"
            )
        consent_timestamp = datetime.now(UTC)
        consent_version = "v1"
    else:
        sharing_scope = "owner_only"
        shared_consent = False

    # Store in vault repository
    stored = await store_credential(
        owner_id=owner_id,
        provider_id=provider["id"],
        nickname=payload.nickname.strip(),
        raw_key=payload.api_key.strip(),
        sharing_scope=sharing_scope,
        shared_consent=shared_consent,
        sharing_consent_timestamp=consent_timestamp,
        consent_version=consent_version,
        account_scope_id=payload.account_scope_id.strip() if payload.account_scope_id else None,
        daily_request_limit=payload.daily_request_limit,
        monthly_request_limit=payload.monthly_request_limit,
        daily_spending_limit_cents=payload.daily_spending_limit_cents,
        monthly_spending_limit_cents=payload.monthly_spending_limit_cents,
        status="disabled",  # Credentials start disabled until validated
    )

    await log_credential_audit(
        actor_id=owner_id,
        action="credential.created",
        credential_id=stored.id,
        provider_id=stored.provider_id,
        outcome="success",
        details={
            "nickname": stored.nickname,
            "sharing_scope": stored.sharing_scope,
            "masked_key": stored.masked_key,
        },
    )

    return _to_credential_response(stored, provider["name"])


async def list_user_credentials(owner_id: UUID) -> CredentialListResponse:
    """List all credentials owned by the authenticated user."""
    items = await list_credentials_by_owner(owner_id)

    # Enrich with provider display names
    pool = get_pool()
    async with pool.acquire() as conn:
        providers = await conn.fetch("SELECT id, name FROM public.llm_providers")
        provider_names = {r["id"]: r["name"] for r in providers}

    response_items = [
        _to_credential_response(it, provider_names.get(it.provider_id, it.provider_id))
        for it in items
    ]
    return CredentialListResponse(items=response_items, total=len(response_items))


async def get_user_credential(owner_id: UUID, credential_id: UUID) -> CredentialResponse:
    """Get metadata for a single owned credential. Returns 404 for non-existent or other users'."""
    try:
        res = await get_credential_metadata(credential_id=credential_id, caller_id=owner_id)
    except (VaultAuthorizationError, VaultDecryptionError) as err:
        raise NotFoundError("Credential not found") from err

    provider = await _get_provider_details(res.provider_id)
    return _to_credential_response(res, provider["name"])


async def update_user_credential(
    owner_id: UUID,
    credential_id: UUID,
    payload: CredentialUpdateRequest,
) -> CredentialResponse:
    """Update permitted metadata, limits, and sharing preferences on an owned credential."""
    try:
        existing = await get_credential_metadata(credential_id=credential_id, caller_id=owner_id)
    except (VaultAuthorizationError, VaultDecryptionError) as err:
        raise NotFoundError("Credential not found") from err

    updates: dict[str, Any] = {}

    if payload.nickname is not None:
        updates["nickname"] = payload.nickname.strip()

    if payload.account_scope_id is not None:
        updates["account_scope_id"] = (
            payload.account_scope_id.strip() if payload.account_scope_id else None
        )

    if payload.daily_request_limit is not None:
        updates["daily_request_limit"] = payload.daily_request_limit

    if payload.monthly_request_limit is not None:
        updates["monthly_request_limit"] = payload.monthly_request_limit

    if payload.daily_spending_limit_cents is not None:
        updates["daily_spending_limit_cents"] = payload.daily_spending_limit_cents

    if payload.monthly_spending_limit_cents is not None:
        updates["monthly_spending_limit_cents"] = payload.monthly_spending_limit_cents

    # Handle sharing scope & consent updates
    if payload.sharing_scope is not None or payload.shared_consent is not None:
        target_scope = (
            payload.sharing_scope if payload.sharing_scope is not None else existing.sharing_scope
        )
        target_consent = (
            payload.shared_consent
            if payload.shared_consent is not None
            else existing.shared_consent
        )

        if target_scope == "community_shared":
            provider = await _get_provider_details(existing.provider_id)
            if not provider.get("allows_community_sharing", False):
                raise ConflictError(
                    f"Provider '{existing.provider_id}' is not approved for community sharing"
                )
            if not target_consent:
                raise ValidationError("Explicit consent is required to enable community sharing")
            updates["sharing_scope"] = "community_shared"
            updates["shared_consent"] = True
            updates["sharing_consent_timestamp"] = datetime.now(UTC)
            updates["consent_version"] = "v1"
        else:
            updates["sharing_scope"] = "owner_only"
            updates["shared_consent"] = False
            updates["sharing_consent_timestamp"] = None
            updates["consent_version"] = None

    updated = await update_credential_metadata(
        credential_id=credential_id,
        owner_id=owner_id,
        updates=updates,
    )

    await log_credential_audit(
        actor_id=owner_id,
        action="credential.updated",
        credential_id=credential_id,
        provider_id=existing.provider_id,
        outcome="success",
        details={"updated_fields": list(updates.keys())},
    )

    provider = await _get_provider_details(updated.provider_id)
    return _to_credential_response(updated, provider["name"])


async def validate_user_credential(
    owner_id: UUID,
    credential_id: UUID,
) -> ValidationResultResponse:
    """Run a safe, non-generative authentication probe on an owned credential."""
    try:
        existing = await get_credential_metadata(credential_id=credential_id, caller_id=owner_id)
    except (VaultAuthorizationError, VaultDecryptionError) as err:
        raise NotFoundError("Credential not found") from err

    # Decrypt raw key inside trusted service layer (check_active=False allows validating disabled keys)
    decrypted = await retrieve_decrypted_credential(
        credential_id=credential_id,
        caller_id=owner_id,
        check_active=False,
    )

    # Dispatch probe
    probe_result = await validate_provider_credential(
        provider_id=existing.provider_id,
        raw_key=decrypted.raw_key,
    )

    # Explicitly clear plaintext key from memory
    del decrypted

    # Record outcome in repository
    outcome_val = probe_result.outcome.value
    await update_validation_status(
        credential_id=credential_id,
        owner_id=owner_id,
        validation_status=outcome_val,
        validation_error_code=probe_result.safe_code,
    )

    await log_credential_audit(
        actor_id=owner_id,
        action="credential.validated",
        credential_id=credential_id,
        provider_id=existing.provider_id,
        outcome=outcome_val,
        details={"safe_code": probe_result.safe_code},
    )

    return ValidationResultResponse(
        credential_id=credential_id,
        provider_id=existing.provider_id,
        outcome=outcome_val,  # type: ignore[arg-type]
        safe_code=probe_result.safe_code,
        message=probe_result.message,
        validated_at=datetime.now(UTC),
    )


async def enable_user_credential(
    owner_id: UUID,
    credential_id: UUID,
) -> CredentialResponse:
    """Enable an owned, validated credential."""
    try:
        existing = await get_credential_metadata(credential_id=credential_id, caller_id=owner_id)
    except (VaultAuthorizationError, VaultDecryptionError) as err:
        raise NotFoundError("Credential not found") from err

    # Invariant: credential must pass validation before being enabled
    if existing.validation_status != "valid":
        raise ConflictError(
            f"Credential must pass validation before being enabled (current validation status: {existing.validation_status})"
        )
    if existing.status == "revoked":
        raise ConflictError("Revoked credentials cannot be re-enabled")

    updated = await set_credential_status(credential_id, owner_id, "active")

    await log_credential_audit(
        actor_id=owner_id,
        action="credential.enabled",
        credential_id=credential_id,
        provider_id=existing.provider_id,
        outcome="success",
    )

    provider = await _get_provider_details(updated.provider_id)
    return _to_credential_response(updated, provider["name"])


async def disable_user_credential(
    owner_id: UUID,
    credential_id: UUID,
) -> CredentialResponse:
    """Disable an owned credential."""
    try:
        existing = await get_credential_metadata(credential_id=credential_id, caller_id=owner_id)
    except (VaultAuthorizationError, VaultDecryptionError) as err:
        raise NotFoundError("Credential not found") from err

    updated = await set_credential_status(credential_id, owner_id, "disabled")

    await log_credential_audit(
        actor_id=owner_id,
        action="credential.disabled",
        credential_id=credential_id,
        provider_id=existing.provider_id,
        outcome="success",
    )

    provider = await _get_provider_details(updated.provider_id)
    return _to_credential_response(updated, provider["name"])


async def delete_user_credential(
    owner_id: UUID,
    credential_id: UUID,
) -> dict[str, Any]:
    """Delete an owned credential and purge its encrypted secret."""
    try:
        existing = await get_credential_metadata(credential_id=credential_id, caller_id=owner_id)
    except (VaultAuthorizationError, VaultDecryptionError) as err:
        raise NotFoundError("Credential not found") from err

    await delete_credential_secret(credential_id=credential_id, caller_id=owner_id)

    await log_credential_audit(
        actor_id=owner_id,
        action="credential.deleted",
        credential_id=credential_id,
        provider_id=existing.provider_id,
        outcome="success",
    )

    return {"deleted": True, "id": str(credential_id)}
