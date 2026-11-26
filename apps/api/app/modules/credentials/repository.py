"""Repository for Encrypted Storage and Retrieval of API Credentials."""

import logging
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg  # type: ignore[import-untyped]

from app.core.config import settings
from app.modules.credentials.crypto import (
    build_payload_aad,
    build_wrapping_aad,
    compute_fingerprint,
    decrypt_payload,
    encrypt_payload,
    generate_dek,
    mask_credential,
)
from app.modules.credentials.errors import (
    VaultAuthorizationError,
    VaultDecryptionError,
    VaultDuplicateError,
    VaultKeyError,
)
from app.modules.credentials.kms import KEKProvider, LocalHKDFKEKProvider
from app.modules.credentials.schemas import (
    DecryptedCredential,
    StoredCredentialResult,
)
from app.modules.credentials.vault import get_vault_session

logger = logging.getLogger("ragbait.vault")


async def store_credential(
    owner_id: UUID,
    provider_id: str,
    nickname: str,
    raw_key: str,
    kek_provider: KEKProvider | None = None,
    data_encryption_version: int | None = None,
    kek_version: int | None = None,
    wrapping_context_version: int | None = None,
    sharing_scope: str = "owner_only",
    shared_consent: bool = False,
    sharing_consent_timestamp: datetime | None = None,
    consent_version: str | None = None,
    account_scope_id: str | None = None,
    account_scope_verified: bool = False,
    account_scope_source: str | None = None,
    account_scope_verified_at: datetime | None = None,
    daily_request_limit: int = 500,
    monthly_request_limit: int = 15000,
    daily_spending_limit_cents: int | None = None,
    monthly_spending_limit_cents: int | None = None,
    credential_id: UUID | None = None,
    status: str = "active",
) -> StoredCredentialResult:
    """Securely store an API credential using envelope encryption.

    1. Computes an HMAC-SHA256 fingerprint using the independent pepper.
    2. Validates uniqueness across (provider_id, key_fingerprint).
    3. Generates a fresh, cryptographically random 256-bit DEK.
    4. Encrypts payload with DEK bound to distinct payload AAD.
    5. Wraps the DEK using the KEK provider bound to distinct wrapping AAD.
    6. Atomically inserts metadata and secrets in a single transaction.
    """
    if not raw_key or not raw_key.strip():
        raise ValueError("Credential key cannot be empty")

    data_enc_ver = data_encryption_version or settings.VAULT_DATA_ENCRYPTION_VERSION
    kek_ver = kek_version or settings.VAULT_KEK_VERSION
    wrap_ctx_ver = wrapping_context_version or settings.VAULT_WRAPPING_CONTEXT_VERSION

    provider = kek_provider or LocalHKDFKEKProvider(settings.VAULT_MASTER_KEY)

    # 1. Fingerprint and mask
    fingerprint = compute_fingerprint(raw_key, settings.VAULT_PEPPER_SECRET)
    masked = mask_credential(raw_key)
    cred_id = credential_id or uuid4()

    async with get_vault_session() as conn:
        # Check duplicate fingerprint within this provider
        existing = await conn.fetchval(
            """
            SELECT id FROM public.api_key_metadata
            WHERE provider_id = $1 AND key_fingerprint = $2
            """,
            provider_id,
            fingerprint,
        )
        if existing:
            raise VaultDuplicateError()

        # 2. Build distinct context AADs
        payload_aad = build_payload_aad(owner_id, cred_id, provider_id, data_enc_ver)
        wrapping_aad = build_wrapping_aad(owner_id, cred_id, provider_id, kek_ver, wrap_ctx_ver)

        # 3. Generate DEK & encrypt payload
        dek = generate_dek()
        ciphertext, nonce = encrypt_payload(raw_key, dek, payload_aad)

        # 4. Wrap DEK with master KEK
        encrypted_dek, dek_nonce, dek_auth_tag = provider.wrap_dek(dek, wrapping_aad, kek_ver)

        # 5. Insert public metadata (default status: disabled, unvalidated)
        try:
            meta_row = await conn.fetchrow(
                """
                INSERT INTO public.api_key_metadata (
                    id, owner_id, provider_id, nickname, masked_key, key_fingerprint,
                    status, sharing_scope, shared_consent, sharing_consent_timestamp,
                    consent_version, validation_status, account_scope_id,
                    account_scope_verified, account_scope_source, account_scope_verified_at,
                    daily_request_limit, monthly_request_limit,
                    daily_spending_limit_cents, monthly_spending_limit_cents
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6,
                    $7, $8, $9, $10,
                    $11, 'unvalidated', $12,
                    $13, $14, $15,
                    $16, $17,
                    $18, $19
                )
                RETURNING id, owner_id, provider_id, nickname, masked_key, key_fingerprint,
                          status, sharing_scope, shared_consent, sharing_consent_timestamp,
                          consent_version, validation_status, last_validated_at,
                          validation_error_code, account_scope_id,
                          account_scope_verified, account_scope_source, account_scope_verified_at,
                          billing_tier, billing_verified_at, billing_verification_error, billing_verification_source,
                          daily_request_limit, monthly_request_limit,
                          daily_spending_limit_cents, monthly_spending_limit_cents,
                          created_at, updated_at
                """,
                cred_id,
                owner_id,
                provider_id,
                nickname,
                masked,
                fingerprint,
                status,
                sharing_scope,
                shared_consent,
                sharing_consent_timestamp,
                consent_version,
                account_scope_id,
                account_scope_verified,
                account_scope_source,
                account_scope_verified_at,
                daily_request_limit,
                monthly_request_limit,
                daily_spending_limit_cents,
                monthly_spending_limit_cents,
            )
        except asyncpg.UniqueViolationError as err:
            raise VaultDuplicateError() from err

        # 6. Insert private secret material
        await conn.execute(
            """
            INSERT INTO private.api_key_secrets (
                id, encrypted_dek, dek_nonce, dek_auth_tag, ciphertext, nonce,
                data_encryption_version, kek_version, wrapping_context_version
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
            cred_id,
            encrypted_dek,
            dek_nonce,
            dek_auth_tag,
            ciphertext,
            nonce,
            data_enc_ver,
            kek_ver,
            wrap_ctx_ver,
        )

        # Explicitly clear plaintext key and DEK from scope
        del dek

    logger.info(
        f"Credential {cred_id} securely stored in vault (payload_v: {data_enc_ver}, kek_v: {kek_ver})"
    )

    return StoredCredentialResult(
        id=meta_row["id"],
        owner_id=meta_row["owner_id"],
        provider_id=meta_row["provider_id"],
        nickname=meta_row["nickname"],
        masked_key=meta_row["masked_key"],
        key_fingerprint=meta_row["key_fingerprint"],
        status=meta_row["status"],
        sharing_scope=meta_row["sharing_scope"],
        shared_consent=meta_row["shared_consent"],
        sharing_consent_timestamp=meta_row["sharing_consent_timestamp"],
        consent_version=meta_row["consent_version"],
        validation_status=meta_row["validation_status"],
        last_validated_at=meta_row["last_validated_at"],
        validation_error_code=meta_row["validation_error_code"],
        account_scope_id=meta_row["account_scope_id"],
        account_scope_verified=meta_row["account_scope_verified"],
        account_scope_source=meta_row["account_scope_source"],
        account_scope_verified_at=meta_row["account_scope_verified_at"],
        billing_tier=meta_row["billing_tier"],
        billing_verified_at=meta_row["billing_verified_at"],
        billing_verification_error=meta_row["billing_verification_error"],
        billing_verification_source=meta_row["billing_verification_source"],
        daily_request_limit=meta_row["daily_request_limit"],
        monthly_request_limit=meta_row["monthly_request_limit"],
        daily_spending_limit_cents=meta_row["daily_spending_limit_cents"],
        monthly_spending_limit_cents=meta_row["monthly_spending_limit_cents"],
        data_encryption_version=data_enc_ver,
        kek_version=kek_ver,
        created_at=meta_row["created_at"],
        updated_at=meta_row["updated_at"],
    )


async def retrieve_decrypted_credential(
    credential_id: UUID,
    caller_id: UUID,
    check_active: bool = True,
    kek_provider: KEKProvider | None = None,
) -> DecryptedCredential:
    """Retrieve and decrypt an API credential from the vault.

    Verifies owner authorization and active status before unwrapping the DEK.
    Separates wrapping AAD for DEK unwrapping and payload AAD for payload decryption.
    """
    provider = kek_provider or LocalHKDFKEKProvider(settings.VAULT_MASTER_KEY)

    async with get_vault_session() as conn:
        row = await conn.fetchrow(
            """
            SELECT m.id, m.owner_id, m.provider_id, m.nickname, m.masked_key,
                   m.status, m.sharing_scope, m.shared_consent, m.account_scope_id,
                   m.account_scope_verified, m.account_scope_source, m.account_scope_verified_at,
                   s.encrypted_dek, s.dek_nonce, s.dek_auth_tag,
                   s.ciphertext, s.nonce,
                   s.data_encryption_version, s.kek_version, s.wrapping_context_version
            FROM public.api_key_metadata m
            JOIN private.api_key_secrets s ON m.id = s.id
            WHERE m.id = $1
            """,
            credential_id,
        )

    if not row:
        raise VaultDecryptionError(
            f"Credential '{credential_id}' not found in vault",
            code="CREDENTIAL_NOT_FOUND",
        )

    # 1. Authorize caller ownership (NO admin bypass)
    if row["owner_id"] != caller_id:
        raise VaultAuthorizationError(
            "Caller is not authorized to decrypt this credential",
            code="VAULT_UNAUTHORIZED",
        )

    # 2. Check active status if requested
    if check_active and row["status"] != "active":
        raise VaultAuthorizationError(
            f"Credential '{credential_id}' is not active (status: {row['status']})",
            code="CREDENTIAL_NOT_ACTIVE",
        )

    # 3. Unwrap DEK using distinct wrapping AAD
    wrapping_aad = build_wrapping_aad(
        row["owner_id"],
        credential_id,
        row["provider_id"],
        row["kek_version"],
        row["wrapping_context_version"],
    )
    dek = provider.unwrap_dek(
        row["encrypted_dek"],
        row["dek_nonce"],
        row["dek_auth_tag"],
        wrapping_aad,
        row["kek_version"],
    )

    # 4. Decrypt payload using distinct payload AAD
    payload_aad = build_payload_aad(
        row["owner_id"],
        credential_id,
        row["provider_id"],
        row["data_encryption_version"],
    )
    raw_key = decrypt_payload(row["ciphertext"], row["nonce"], dek, payload_aad)

    # Explicitly clear DEK
    del dek

    return DecryptedCredential(
        id=row["id"],
        owner_id=row["owner_id"],
        provider_id=row["provider_id"],
        raw_key=raw_key,
        masked_key=row["masked_key"],
        status=row["status"],
        sharing_scope=row["sharing_scope"],
        shared_consent=row["shared_consent"],
        account_scope_id=row["account_scope_id"],
        account_scope_verified=row["account_scope_verified"],
        account_scope_source=row["account_scope_source"],
        account_scope_verified_at=row["account_scope_verified_at"],
        data_encryption_version=row["data_encryption_version"],
        kek_version=row["kek_version"],
        wrapping_context_version=row["wrapping_context_version"],
    )


async def rewrap_credential_dek(
    credential_id: UUID,
    new_kek_version: int | None = None,
    new_key_version: int | None = None,
    current_kek_provider: KEKProvider | None = None,
    new_kek_provider: KEKProvider | None = None,
    new_wrapping_context_version: int = 1,
) -> None:
    """Rewrap a credential's DEK under a new KEK version (for master KEK rotation).

    CRITICAL SECURITY INVARIANT:
    Does NOT decrypt or re-encrypt the credential payload.
    The original ciphertext and nonce remain completely untouched.
    Only the DEK is unwrapped under the old KEK/wrapping AAD and rewrapped under
    the new KEK/wrapping AAD.
    """
    target_kek_ver = new_kek_version if new_kek_version is not None else new_key_version
    if target_kek_ver is None or target_kek_ver < 1:
        raise VaultKeyError("New KEK version must be >= 1")

    old_provider = current_kek_provider or LocalHKDFKEKProvider(settings.VAULT_MASTER_KEY)
    new_provider = new_kek_provider or old_provider

    async with get_vault_session() as conn:
        row = await conn.fetchrow(
            """
            SELECT m.owner_id, m.provider_id, s.encrypted_dek, s.dek_nonce,
                   s.dek_auth_tag, s.kek_version, s.wrapping_context_version
            FROM public.api_key_metadata m
            JOIN private.api_key_secrets s ON m.id = s.id
            WHERE m.id = $1
            """,
            credential_id,
        )
        if not row:
            raise VaultDecryptionError(f"Credential '{credential_id}' not found for rewrap")

        old_kek_ver = row["kek_version"]
        old_ctx_ver = row["wrapping_context_version"]
        if (
            old_kek_ver == target_kek_ver
            and old_ctx_ver == new_wrapping_context_version
            and old_provider is new_provider
        ):
            return  # Nothing to rotate

        # 1. Unwrap DEK using old wrapping AAD
        old_wrapping_aad = build_wrapping_aad(
            row["owner_id"],
            credential_id,
            row["provider_id"],
            old_kek_ver,
            old_ctx_ver,
        )
        dek = old_provider.unwrap_dek(
            row["encrypted_dek"],
            row["dek_nonce"],
            row["dek_auth_tag"],
            old_wrapping_aad,
            old_kek_ver,
        )

        # 2. Rewrap the EXACT SAME DEK under the new KEK and new wrapping AAD
        new_wrapping_aad = build_wrapping_aad(
            row["owner_id"],
            credential_id,
            row["provider_id"],
            target_kek_ver,
            new_wrapping_context_version,
        )
        new_encrypted_dek, new_dek_nonce, new_dek_auth_tag = new_provider.wrap_dek(
            dek,
            new_wrapping_aad,
            target_kek_ver,
        )

        # 3. Atomically update ONLY the DEK and KEK version fields in private.api_key_secrets
        # Notice: ciphertext, nonce, and data_encryption_version are NEVER modified!
        await conn.execute(
            """
            UPDATE private.api_key_secrets
            SET encrypted_dek = $1,
                dek_nonce = $2,
                dek_auth_tag = $3,
                kek_version = $4,
                wrapping_context_version = $5,
                updated_at = now()
            WHERE id = $6
            """,
            new_encrypted_dek,
            new_dek_nonce,
            new_dek_auth_tag,
            target_kek_ver,
            new_wrapping_context_version,
            credential_id,
        )

        del dek

    logger.info(
        f"Credential {credential_id} DEK successfully rewrapped (kek_v: {old_kek_ver} -> {target_kek_ver}, ctx_v: {new_wrapping_context_version})"
    )


async def delete_credential_secret(credential_id: UUID, caller_id: UUID) -> None:
    """Delete a credential from the vault, purging both metadata and private secret."""
    async with get_vault_session() as conn:
        owner_id = await conn.fetchval(
            "SELECT owner_id FROM public.api_key_metadata WHERE id = $1",
            credential_id,
        )
        if not owner_id:
            raise VaultDecryptionError(
                f"Credential '{credential_id}' not found",
                code="CREDENTIAL_NOT_FOUND",
            )
        if owner_id != caller_id:
            raise VaultAuthorizationError(
                "Caller is not authorized to delete this credential",
                code="VAULT_UNAUTHORIZED",
            )

        # Deleting from public.api_key_metadata cascades to private.api_key_secrets
        await conn.execute("DELETE FROM public.api_key_metadata WHERE id = $1", credential_id)

    logger.info(f"Credential {credential_id} successfully deleted from vault")


async def get_credential_metadata(credential_id: UUID, caller_id: UUID) -> StoredCredentialResult:
    """Retrieve non-sensitive credential metadata (never decrypts secret material)."""
    async with get_vault_session() as conn:
        row = await conn.fetchrow(
            """
            SELECT m.id, m.owner_id, m.provider_id, m.nickname, m.masked_key, m.key_fingerprint,
                   m.status, m.sharing_scope, m.shared_consent, m.sharing_consent_timestamp,
                   m.consent_version, m.validation_status, m.last_validated_at,
                   m.validation_error_code, m.account_scope_id,
                   m.account_scope_verified, m.account_scope_source, m.account_scope_verified_at,
                   m.billing_tier, m.billing_verified_at, m.billing_verification_error, m.billing_verification_source,
                   m.daily_request_limit, m.monthly_request_limit,
                   m.daily_spending_limit_cents, m.monthly_spending_limit_cents,
                   s.data_encryption_version, s.kek_version,
                   m.created_at, m.updated_at
            FROM public.api_key_metadata m
            LEFT JOIN private.api_key_secrets s ON m.id = s.id
            WHERE m.id = $1
            """,
            credential_id,
        )

    if not row:
        raise VaultDecryptionError(
            f"Credential '{credential_id}' not found",
            code="CREDENTIAL_NOT_FOUND",
        )
    if row["owner_id"] != caller_id:
        raise VaultAuthorizationError(
            "Caller is not authorized to view this credential metadata",
            code="VAULT_UNAUTHORIZED",
        )

    return StoredCredentialResult(
        id=row["id"],
        owner_id=row["owner_id"],
        provider_id=row["provider_id"],
        nickname=row["nickname"],
        masked_key=row["masked_key"],
        key_fingerprint=row["key_fingerprint"],
        status=row["status"],
        sharing_scope=row["sharing_scope"],
        shared_consent=row["shared_consent"],
        sharing_consent_timestamp=row["sharing_consent_timestamp"],
        consent_version=row["consent_version"],
        validation_status=row["validation_status"],
        last_validated_at=row["last_validated_at"],
        validation_error_code=row["validation_error_code"],
        account_scope_id=row["account_scope_id"],
        account_scope_verified=row["account_scope_verified"],
        account_scope_source=row["account_scope_source"],
        account_scope_verified_at=row["account_scope_verified_at"],
        billing_tier=row["billing_tier"],
        billing_verified_at=row["billing_verified_at"],
        billing_verification_error=row["billing_verification_error"],
        billing_verification_source=row["billing_verification_source"],
        daily_request_limit=row["daily_request_limit"],
        monthly_request_limit=row["monthly_request_limit"],
        daily_spending_limit_cents=row["daily_spending_limit_cents"],
        monthly_spending_limit_cents=row["monthly_spending_limit_cents"],
        data_encryption_version=row["data_encryption_version"] or 1,
        kek_version=row["kek_version"] or 1,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def list_credentials_by_owner(owner_id: UUID) -> list[StoredCredentialResult]:
    """List all credential metadata records owned by an authenticated contributor."""
    async with get_vault_session() as conn:
        rows = await conn.fetch(
            """
            SELECT m.id, m.owner_id, m.provider_id, m.nickname, m.masked_key, m.key_fingerprint,
                   m.status, m.sharing_scope, m.shared_consent, m.sharing_consent_timestamp,
                   m.consent_version, m.validation_status, m.last_validated_at,
                   m.validation_error_code, m.account_scope_id,
                   m.account_scope_verified, m.account_scope_source, m.account_scope_verified_at,
                   m.billing_tier, m.billing_verified_at, m.billing_verification_error, m.billing_verification_source,
                   m.daily_request_limit, m.monthly_request_limit,
                   m.daily_spending_limit_cents, m.monthly_spending_limit_cents,
                   s.data_encryption_version, s.kek_version,
                   m.created_at, m.updated_at
            FROM public.api_key_metadata m
            LEFT JOIN private.api_key_secrets s ON m.id = s.id
            WHERE m.owner_id = $1
            ORDER BY m.created_at DESC
            """,
            owner_id,
        )

    return [
        StoredCredentialResult(
            id=r["id"],
            owner_id=r["owner_id"],
            provider_id=r["provider_id"],
            nickname=r["nickname"],
            masked_key=r["masked_key"],
            key_fingerprint=r["key_fingerprint"],
            status=r["status"],
            sharing_scope=r["sharing_scope"],
            shared_consent=r["shared_consent"],
            sharing_consent_timestamp=r["sharing_consent_timestamp"],
            consent_version=r["consent_version"],
            validation_status=r["validation_status"],
            last_validated_at=r["last_validated_at"],
            validation_error_code=r["validation_error_code"],
            account_scope_id=r["account_scope_id"],
            account_scope_verified=r["account_scope_verified"],
            account_scope_source=r["account_scope_source"],
            account_scope_verified_at=r["account_scope_verified_at"],
            billing_tier=r["billing_tier"],
            billing_verified_at=r["billing_verified_at"],
            billing_verification_error=r["billing_verification_error"],
            billing_verification_source=r["billing_verification_source"],
            daily_request_limit=r["daily_request_limit"],
            monthly_request_limit=r["monthly_request_limit"],
            daily_spending_limit_cents=r["daily_spending_limit_cents"],
            monthly_spending_limit_cents=r["monthly_spending_limit_cents"],
            data_encryption_version=r["data_encryption_version"] or 1,
            kek_version=r["kek_version"] or 1,
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]


async def update_credential_metadata(
    credential_id: UUID,
    owner_id: UUID,
    updates: dict[str, Any],
) -> StoredCredentialResult:
    """Update permitted non-cryptographic metadata fields on an owned credential."""
    # Strip any client-injected account scope or billing fields to prevent manual tampering
    updates.pop("account_scope_id", None)
    updates.pop("account_scope_verified", None)
    updates.pop("account_scope_source", None)
    updates.pop("account_scope_verified_at", None)
    updates.pop("billing_tier", None)
    updates.pop("billing_verified_at", None)
    updates.pop("billing_verification_error", None)
    updates.pop("billing_verification_source", None)

    if not updates:
        return await get_credential_metadata(credential_id, owner_id)

    set_clauses: list[str] = []
    params: list[Any] = [credential_id, owner_id]

    for key, val in updates.items():
        params.append(val)
        set_clauses.append(f"{key} = ${len(params)}")

    set_clause = ", ".join(set_clauses)

    async with get_vault_session() as conn:
        updated_row = await conn.fetchrow(
            f"""
            UPDATE public.api_key_metadata
            SET {set_clause}, updated_at = now()
            WHERE id = $1 AND owner_id = $2
            RETURNING id
            """,
            *params,
        )

        if not updated_row:
            raise VaultAuthorizationError(
                "Caller is not authorized to update this credential or credential does not exist",
                code="VAULT_UNAUTHORIZED",
            )

    return await get_credential_metadata(credential_id, owner_id)


async def update_validation_status(
    credential_id: UUID,
    owner_id: UUID,
    validation_status: str,
    validation_error_code: str | None = None,
    billing_tier: str = "unknown",
    billing_verification_error: str | None = None,
    billing_verification_source: str | None = None,
) -> StoredCredentialResult:
    """Record validation outcome timestamp, error code, and billing tier verification."""
    async with get_vault_session() as conn:
        updated = await conn.fetchval(
            """
            UPDATE public.api_key_metadata
            SET validation_status = $1,
                last_validated_at = now(),
                validation_error_code = $2,
                billing_tier = $3,
                billing_verified_at = now(),
                billing_verification_error = $4,
                billing_verification_source = $5,
                updated_at = now()
            WHERE id = $6 AND owner_id = $7
            RETURNING id
            """,
            validation_status,
            validation_error_code,
            billing_tier,
            billing_verification_error,
            billing_verification_source,
            credential_id,
            owner_id,
        )
        if not updated:
            raise VaultAuthorizationError(
                "Caller is not authorized to validate this credential or credential does not exist",
                code="VAULT_UNAUTHORIZED",
            )

    return await get_credential_metadata(credential_id, owner_id)


async def set_credential_status(
    credential_id: UUID,
    owner_id: UUID,
    new_status: str,
) -> StoredCredentialResult:
    """Change status (active or disabled) on an owned credential."""
    async with get_vault_session() as conn:
        updated = await conn.fetchval(
            """
            UPDATE public.api_key_metadata
            SET status = $1, updated_at = now()
            WHERE id = $2 AND owner_id = $3
            RETURNING id
            """,
            new_status,
            credential_id,
            owner_id,
        )
        if not updated:
            raise VaultAuthorizationError(
                "Caller is not authorized to modify this credential or credential does not exist",
                code="VAULT_UNAUTHORIZED",
            )

    return await get_credential_metadata(credential_id, owner_id)


async def set_verified_account_scope(
    credential_id: UUID,
    account_scope_id: str,
    source: str = "provider_response_header",
) -> StoredCredentialResult:
    """Record server-verified provider account identity.

    Only callable by trusted internal backend components (e.g. M2.5 dispatch adapters).
    """
    async with get_vault_session() as conn:
        row = await conn.fetchrow(
            """
            UPDATE public.api_key_metadata
            SET account_scope_id = $1,
                account_scope_verified = true,
                account_scope_source = $2,
                account_scope_verified_at = now(),
                updated_at = now()
            WHERE id = $3
            RETURNING owner_id
            """,
            account_scope_id,
            source,
            credential_id,
        )
        if not row:
            raise VaultDecryptionError(
                f"Credential '{credential_id}' not found",
                code="CREDENTIAL_NOT_FOUND",
            )

    return await get_credential_metadata(credential_id, caller_id=row["owner_id"])
