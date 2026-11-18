"""Key Encryption Key (KEK) Provider and KMS Abstraction."""

import os
from abc import ABC, abstractmethod

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.modules.credentials.errors import (
    VaultConfigurationError,
    VaultKeyError,
    VaultTamperError,
)


class KEKProvider(ABC):
    """Abstract interface for Key Encryption Key (KEK) management.

    Supports replaceable implementations: Local HKDF-derived KEK for development,
    and Cloud KMS (Google Cloud KMS / AWS KMS) for production deployments.
    """

    @abstractmethod
    def wrap_dek(
        self,
        dek: bytes,
        wrapping_aad: bytes,
        kek_version: int,
    ) -> tuple[bytes, bytes, bytes]:
        """Wrap an ephemeral DEK with the master KEK.

        Returns:
            tuple of (encrypted_dek, dek_nonce, dek_auth_tag)
        """
        ...

    @abstractmethod
    def unwrap_dek(
        self,
        encrypted_dek: bytes,
        dek_nonce: bytes,
        dek_auth_tag: bytes,
        wrapping_aad: bytes,
        kek_version: int,
    ) -> bytes:
        """Unwrap an encrypted DEK using the master KEK.

        Returns:
            Raw 256-bit DEK
        """
        ...


class LocalHKDFKEKProvider(KEKProvider):
    """Local development KEK provider using HKDF-SHA256 and authenticated AES-256-GCM.

    Derives a distinct 256-bit KEK per KEK version from the master secret and
    authenticates the wrapped DEK with its own fresh 96-bit nonce, 128-bit tag, and wrapping AAD.
    Strictly prohibited in production.
    """

    SALT: bytes = b"ragbait-m2-vault-salt"

    def __init__(self, master_key: str, allow_in_production: bool = False):
        from app.core.config import settings

        if settings.ENVIRONMENT == "production" and not allow_in_production:
            raise VaultConfigurationError(
                "LocalHKDFKEKProvider is strictly prohibited in production. "
                "A production Cloud KMS provider (e.g. Google Cloud KMS or AWS KMS) must be used.",
                code="LOCAL_KMS_IN_PRODUCTION",
            )

        master_bytes = master_key.strip().encode("utf-8")
        if len(master_bytes) < 32:
            raise VaultConfigurationError(
                f"VAULT_MASTER_KEY must contain at least 32 bytes, got {len(master_bytes)}",
                code="INVALID_MASTER_KEY",
            )
        self._master_key_bytes: bytes = master_bytes

    def _derive_kek(self, kek_version: int) -> bytes:
        """Derive an authenticated 256-bit KEK for a specific KEK version."""
        if kek_version < 1:
            raise VaultKeyError(
                f"Invalid KEK version: {kek_version}. Version must be >= 1",
                code="INVALID_KEY_VERSION",
            )
        info = f"ragbait-m2-vault-kek-v{kek_version}".encode()
        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=self.SALT,
            info=info,
        )
        return hkdf.derive(self._master_key_bytes)

    def wrap_dek(
        self,
        dek: bytes,
        wrapping_aad: bytes,
        kek_version: int,
    ) -> tuple[bytes, bytes, bytes]:
        """Wrap an ephemeral 256-bit DEK using AES-256-GCM bound to wrapping context AAD."""
        if len(dek) != 32:
            raise VaultKeyError(
                f"DEK must be exactly 32 bytes (256 bits), got {len(dek)}",
                code="INVALID_DEK_LENGTH",
            )

        kek = self._derive_kek(kek_version)
        dek_nonce = os.urandom(12)  # 96-bit fresh nonce
        aesgcm = AESGCM(kek)
        wrapped = aesgcm.encrypt(dek_nonce, dek, wrapping_aad)

        # In AESGCM, the 16-byte authentication tag is appended to the ciphertext
        encrypted_dek = wrapped[:-16]
        dek_auth_tag = wrapped[-16:]

        return encrypted_dek, dek_nonce, dek_auth_tag

    def unwrap_dek(
        self,
        encrypted_dek: bytes,
        dek_nonce: bytes,
        dek_auth_tag: bytes,
        wrapping_aad: bytes,
        kek_version: int,
    ) -> bytes:
        """Unwrap an encrypted DEK, verifying the GCM tag and wrapping context AAD."""
        if len(encrypted_dek) != 32:
            raise VaultTamperError(
                f"Encrypted DEK must be 32 bytes, got {len(encrypted_dek)}",
                code="CORRUPT_ENCRYPTED_DEK",
            )
        if len(dek_nonce) != 12:
            raise VaultTamperError(
                f"DEK nonce must be 12 bytes, got {len(dek_nonce)}",
                code="CORRUPT_DEK_NONCE",
            )
        if len(dek_auth_tag) != 16:
            raise VaultTamperError(
                f"DEK auth tag must be 16 bytes, got {len(dek_auth_tag)}",
                code="CORRUPT_DEK_AUTH_TAG",
            )

        kek = self._derive_kek(kek_version)
        aesgcm = AESGCM(kek)
        wrapped = encrypted_dek + dek_auth_tag

        try:
            return aesgcm.decrypt(dek_nonce, wrapped, wrapping_aad)
        except InvalidTag as err:
            raise VaultTamperError(
                "DEK authentication failed: auth tag or wrapping AAD invalid",
                code="DEK_TAMPER_DETECTED",
            ) from err
