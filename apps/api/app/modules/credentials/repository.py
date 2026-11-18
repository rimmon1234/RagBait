"""Repository for Encrypted Storage and Retrieval of API Credentials."""

import logging
from uuid import UUID, uuid4

import asyncpg

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
    account_scope_id: str | None = None,
    daily_request_limit: int = 500,
    monthly_request_limit: int = 15000,
    daily_spending_limit_cents: int | None = None,
    monthly_spending_limit_cents: int | None = None,
    credential_id: UUID | None = None,
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

        # 5. Insert public metadata
        try:
            meta_row = await conn.fetchrow(
                """
                INSERT INTO public.api_key_metadata (
                    id, owner_id, provider_id, nickname, masked_key, key_fingerprint,
                    status, sharing_scope, shared_consent, account_scope_id,
                    daily_request_limit, monthly_request_limit,
                    daily_spending_limit_cents, monthly_spending_limit_cents
                )
                VALUES (
                    $1, $2, $3, $4, $5, $6,
                    'active', $7, $8, $9,
                    $10, $11,
                    $12, $13
                )
                RETURNING id, owner_id, provider_id, nickname, masked_key, key_fingerprint,
                          status, sharing_scope, shared_consent, account_scope_id,
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
                sharing_scope,
                shared_consent,
                account_scope_id,
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
        account_scope_id=meta_row["account_scope_id"],
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

    # 1. Authorize caller ownership
    if row["owner_id"] != caller_id:
        raise VaultAuthorizationError(
            "Caller is not authorized to decrypt this credential",
            code="VAULT_UNAUTHORIZED",
        )

    # 2. Check active status
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
                   m.status, m.sharing_scope, m.shared_consent, m.account_scope_id,
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
        account_scope_id=row["account_scope_id"],
        daily_request_limit=row["daily_request_limit"],
        monthly_request_limit=row["monthly_request_limit"],
        daily_spending_limit_cents=row["daily_spending_limit_cents"],
        monthly_spending_limit_cents=row["monthly_spending_limit_cents"],
        data_encryption_version=row["data_encryption_version"] or 1,
        kek_version=row["kek_version"] or 1,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
