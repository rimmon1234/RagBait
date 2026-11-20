"""Credential Vault Error Definitions."""

from fastapi import status

from app.core.errors import AppError, ConflictError


class VaultError(AppError):
    """Base exception for all credential vault operations."""

    def __init__(
        self,
        message: str = "Credential vault operation failed",
        code: str = "VAULT_ERROR",
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
    ):
        super().__init__(code=code, message=message, status_code=status_code)


class VaultConfigurationError(VaultError):
    """Raised when vault encryption keys, pepper, or connection URLs are missing or invalid."""

    def __init__(
        self,
        message: str = "Invalid vault configuration",
        code: str = "VAULT_CONFIG_ERROR",
    ):
        super().__init__(
            message=message,
            code=code,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


class VaultKeyError(VaultError):
    """Raised when master KEK or DEK version is unknown or invalid."""

    def __init__(
        self,
        message: str = "Vault key or version error",
        code: str = "VAULT_KEY_ERROR",
    ):
        super().__init__(
            message=message,
            code=code,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


class VaultTamperError(VaultError):
    """Raised when ciphertext, authentication tag, or context AAD verification fails."""

    def __init__(
        self,
        message: str = "Authentication tag or AAD verification failed",
        code: str = "VAULT_TAMPER_DETECTED",
    ):
        super().__init__(
            message=message,
            code=code,
            status_code=status.HTTP_400_BAD_REQUEST,
        )


class VaultDecryptionError(VaultError):
    """Raised when credential secret cannot be decrypted."""

    def __init__(
        self,
        message: str = "Failed to decrypt credential secret",
        code: str = "VAULT_DECRYPT_FAILED",
    ):
        super().__init__(
            message=message,
            code=code,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


class VaultAuthorizationError(VaultError):
    """Raised when ownership, consent, or credential authorization check fails."""

    def __init__(
        self,
        message: str = "Unauthorized credential access",
        code: str = "VAULT_UNAUTHORIZED",
    ):
        super().__init__(
            message=message,
            code=code,
            status_code=status.HTTP_403_FORBIDDEN,
        )


class VaultDuplicateError(ConflictError):
    """Raised when attempting to store a credential with a duplicate fingerprint."""

    def __init__(
        self,
        message: str = "This credential key has already been registered for this provider",
        code: str = "CREDENTIAL_ALREADY_EXISTS",
    ):
        super().__init__(message=message, code=code)


class VaultDatabaseError(VaultError):
    """Raised when the dedicated vault database connection or query fails."""

    def __init__(
        self,
        message: str = "Vault database operation failed",
        code: str = "VAULT_DATABASE_ERROR",
    ):
        super().__init__(
            message=message,
            code=code,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
