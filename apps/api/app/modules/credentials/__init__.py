"""Encrypted Credential Vault Module (RagBait M2.2)."""

from app.modules.credentials.crypto import (
    build_aad,
    compute_fingerprint,
    decrypt_payload,
    encrypt_payload,
    generate_dek,
    generate_nonce,
    mask_credential,
)
from app.modules.credentials.errors import (
    VaultAuthorizationError,
    VaultConfigurationError,
    VaultDatabaseError,
    VaultDecryptionError,
    VaultDuplicateError,
    VaultError,
    VaultKeyError,
    VaultTamperError,
)
from app.modules.credentials.kms import KEKProvider, LocalHKDFKEKProvider
from app.modules.credentials.repository import (
    delete_credential_secret,
    get_credential_metadata,
    retrieve_decrypted_credential,
    rewrap_credential_dek,
    store_credential,
)
from app.modules.credentials.schemas import (
    ApiKeyMetadataRecord,
    DecryptedCredential,
    EncryptedSecretRecord,
    StoredCredentialResult,
)
from app.modules.credentials.vault import (
    close_vault_pool,
    get_vault_pool,
    get_vault_session,
    init_vault_pool,
)

__all__ = [
    "ApiKeyMetadataRecord",
    "DecryptedCredential",
    "EncryptedSecretRecord",
    "KEKProvider",
    "LocalHKDFKEKProvider",
    "StoredCredentialResult",
    "VaultAuthorizationError",
    "VaultConfigurationError",
    "VaultDatabaseError",
    "VaultDecryptionError",
    "VaultDuplicateError",
    "VaultError",
    "VaultKeyError",
    "VaultTamperError",
    "build_aad",
    "close_vault_pool",
    "compute_fingerprint",
    "decrypt_payload",
    "delete_credential_secret",
    "encrypt_payload",
    "generate_dek",
    "generate_nonce",
    "get_credential_metadata",
    "get_vault_pool",
    "get_vault_session",
    "init_vault_pool",
    "mask_credential",
    "retrieve_decrypted_credential",
    "rewrap_credential_dek",
    "store_credential",
]
