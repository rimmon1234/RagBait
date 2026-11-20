"""Cryptographic Primitives for Envelope Encryption and Key Fingerprinting."""

import hashlib
import hmac
import os
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.modules.credentials.errors import (
    VaultConfigurationError,
    VaultKeyError,
    VaultTamperError,
)


def generate_dek() -> bytes:
    """Generate a unique, cryptographically random 256-bit Data Encryption Key (DEK)."""
    return os.urandom(32)


def generate_nonce() -> bytes:
    """Generate a unique, fresh 96-bit (12-byte) initialization vector/nonce for AES-GCM."""
    return os.urandom(12)


def build_payload_aad(
    owner_id: UUID,
    credential_id: UUID,
    provider_id: str,
    data_encryption_version: int = 1,
) -> bytes:
    """Construct deterministic Authenticated Additional Data (AAD) for payload encryption context.

    Prevents ciphertext transplant attacks across owners, credentials, providers, or payload versions.
    """
    return f"payload:owner:{owner_id}:cred:{credential_id}:prov:{provider_id}:v:{data_encryption_version}".encode()


def build_wrapping_aad(
    owner_id: UUID,
    credential_id: UUID,
    provider_id: str,
    kek_version: int = 1,
    wrapping_context_version: int = 1,
) -> bytes:
    """Construct deterministic Authenticated Additional Data (AAD) for DEK key wrapping context.

    Binds the wrapped DEK to the specific credential metadata, KEK version, and wrapping context version.
    """
    return f"wrap:owner:{owner_id}:cred:{credential_id}:prov:{provider_id}:kek_v:{kek_version}:ctx_v:{wrapping_context_version}".encode()


def build_aad(
    owner_id: UUID,
    credential_id: UUID,
    provider_id: str,
    version: int = 1,
) -> bytes:
    """Legacy AAD builder alias for payload context."""
    return build_payload_aad(owner_id, credential_id, provider_id, version)


def encrypt_payload(raw_key: str, dek: bytes, aad: bytes) -> tuple[bytes, bytes]:
    """Encrypt a raw API credential using AES-256-GCM bound to context AAD.

    Returns:
        tuple of (ciphertext, nonce) where ciphertext includes the appended 16-byte auth tag.
    """
    if len(dek) != 32:
        raise VaultKeyError(
            f"DEK must be exactly 32 bytes, got {len(dek)}",
            code="INVALID_DEK_LENGTH",
        )
    if not raw_key:
        raise ValueError("Cannot encrypt an empty credential key")

    nonce = generate_nonce()
    aesgcm = AESGCM(dek)
    ciphertext = aesgcm.encrypt(nonce, raw_key.encode("utf-8"), aad)
    return ciphertext, nonce


def decrypt_payload(ciphertext: bytes, nonce: bytes, dek: bytes, aad: bytes) -> str:
    """Decrypt a ciphertext payload with DEK, validating the GCM authentication tag and AAD.

    Raises VaultTamperError if ciphertext, nonce, tag, or AAD does not match.
    """
    if len(dek) != 32:
        raise VaultKeyError(
            f"DEK must be exactly 32 bytes, got {len(dek)}",
            code="INVALID_DEK_LENGTH",
        )
    if len(nonce) != 12:
        raise VaultTamperError(
            f"Nonce must be exactly 12 bytes, got {len(nonce)}",
            code="CORRUPT_NONCE",
        )

    aesgcm = AESGCM(dek)
    try:
        raw_bytes = aesgcm.decrypt(nonce, ciphertext, aad)
        return raw_bytes.decode("utf-8")
    except InvalidTag as err:
        raise VaultTamperError(
            "Payload authentication failed: ciphertext, tag, or AAD invalid",
            code="PAYLOAD_TAMPER_DETECTED",
        ) from err


def compute_fingerprint(raw_key: str, pepper: str) -> str:
    """Calculate an HMAC-SHA256 fingerprint using the independent VAULT_PEPPER_SECRET.

    Validates pepper length by decoded byte length (>= 32 bytes).
    Used exclusively for duplicate key detection without exposing plaintext or using KEK.
    """
    pepper_bytes = pepper.strip().encode("utf-8")
    if len(pepper_bytes) < 32:
        raise VaultConfigurationError(
            f"VAULT_PEPPER_SECRET must contain at least 32 bytes, got {len(pepper_bytes)}",
            code="INVALID_PEPPER_SECRET",
        )
    cleaned_key = raw_key.strip().encode("utf-8")
    return hmac.new(pepper_bytes, cleaned_key, hashlib.sha256).hexdigest()


def mask_credential(raw_key: str) -> str:
    """Generate a masked representation of an API credential for safe display.

    Never exposes full key material. Shows first few characters and last 4 characters.
    """
    cleaned = raw_key.strip()
    if len(cleaned) <= 8:
        return cleaned[:2] + "..." + cleaned[-2:]
    return cleaned[:4] + "..." + cleaned[-4:]
