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
    if _vault_pool is not None and (
        _vault_pool._closed
        or getattr(_vault_pool, "_loop", None) is None
        or _vault_pool._loop.is_closed()
    ):
        _vault_pool = None
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
        logger.info("Closing dedicated vault connection pool...")
        try:
            await _vault_pool.close()
        except Exception:
            try:
                _vault_pool.terminate()
            except Exception:
                pass
        finally:
            _vault_pool = None


def get_vault_pool() -> asyncpg.Pool:
    """Retrieve the active dedicated vault connection pool."""
    if _vault_pool is None:
        raise VaultDatabaseError(
            "Vault connection pool is not initialized",
            code="VAULT_POOL_UNINITIALIZED",
        )
    return _vault_pool


async def check_vault_health() -> bool:
    """Ping dedicated vault database to confirm health for readiness check."""
    global _vault_pool
    if _vault_pool is None:
        return False
    try:
        async with _vault_pool.acquire() as conn:
            val = await conn.fetchval("SELECT 1")
            return bool(val == 1)
    except Exception as exc:
        logger.warning(f"Vault database readiness ping failed: {exc}")
        return False


@asynccontextmanager
async def get_vault_session() -> AsyncGenerator[asyncpg.Connection, None]:
    """Provide a transactional database session over the dedicated vault connection pool.

    Guarantees that vault operations execute within an atomic transaction.
    Any failure triggers an automatic rollback to prevent partial writes.
    Lazily initializes the pool if it has not yet been initialized or was closed.
    """
    global _vault_pool
    if (
        _vault_pool is None
        or _vault_pool._closed
        or getattr(_vault_pool, "_loop", None) is None
        or _vault_pool._loop.is_closed()
    ):
        await init_vault_pool()
    pool = _vault_pool
    if pool is None:
        raise VaultDatabaseError("Vault connection pool is not initialized")
    async with pool.acquire() as conn:
        async with conn.transaction():
            yield conn
