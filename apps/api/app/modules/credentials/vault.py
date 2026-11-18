"""Isolated Connection Pool and Session Management for Credential Vault."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import asyncpg

from app.core.config import settings
from app.modules.credentials.errors import (
    VaultConfigurationError,
    VaultDatabaseError,
)

logger = logging.getLogger("ragbait.vault")

# Strictly private to this module - never export or share across general routers
_vault_pool: asyncpg.Pool | None = None


async def init_vault_pool(dsn: str | None = None) -> asyncpg.Pool:
    """Initialize dedicated connection pool authenticated as ragbait_vault_service.

    Restricted strictly to the credential vault service.
    """
    global _vault_pool
    if _vault_pool is not None:
        return _vault_pool

    vault_dsn = dsn or settings.VAULT_DATABASE_URL
    if not vault_dsn:
        raise VaultConfigurationError(
            "VAULT_DATABASE_URL is not configured",
            code="VAULT_CONFIG_ERROR",
        )

    try:
        _vault_pool = await asyncpg.create_pool(
            dsn=vault_dsn,
            min_size=settings.VAULT_POOL_MIN,
            max_size=settings.VAULT_POOL_MAX,
            command_timeout=settings.DATABASE_COMMAND_TIMEOUT,
        )
        logger.info("Dedicated vault connection pool initialized successfully")
        return _vault_pool
    except Exception as exc:
        logger.error(f"Failed to initialize dedicated vault connection pool: {exc}")
        raise VaultDatabaseError(
            f"Vault database connection failed: {exc}",
            code="VAULT_DB_CONN_FAILED",
        ) from exc


async def close_vault_pool() -> None:
    """Close the dedicated vault connection pool cleanly."""
    global _vault_pool
    if _vault_pool is not None:
        await _vault_pool.close()
        _vault_pool = None
        logger.info("Dedicated vault connection pool closed")


def get_vault_pool() -> asyncpg.Pool:
    """Retrieve the active dedicated vault connection pool."""
    if _vault_pool is None:
        raise VaultDatabaseError(
            "Vault connection pool is not initialized",
            code="VAULT_POOL_UNINITIALIZED",
        )
    return _vault_pool


@asynccontextmanager
async def get_vault_session() -> AsyncGenerator[asyncpg.Connection, None]:
    """Provide a transactional database session over the dedicated vault connection pool.

    Guarantees that vault operations execute within an atomic transaction.
    Any failure triggers an automatic rollback to prevent partial writes.
    """
    pool = get_vault_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            yield conn
