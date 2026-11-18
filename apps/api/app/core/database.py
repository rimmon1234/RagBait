"""Database connection pool and RLS-aware transaction context management."""

import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import asyncpg

from app.core.config import settings

logger = logging.getLogger("ragbait.database")

_pool: asyncpg.Pool | None = None


async def init_db_pool() -> asyncpg.Pool:
    """Initialize the global asyncpg connection pool."""
    global _pool
    if _pool is None:
        logger.info("Initializing asyncpg connection pool...")
        _pool = await asyncpg.create_pool(
            dsn=settings.DATABASE_URL,
            min_size=settings.DATABASE_POOL_MIN,
            max_size=settings.DATABASE_POOL_MAX,
            command_timeout=settings.DATABASE_COMMAND_TIMEOUT,
        )
    return _pool


async def close_db_pool() -> None:
    """Close the global asyncpg connection pool."""
    global _pool
    if _pool is not None:
        logger.info("Closing asyncpg connection pool...")
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    """Retrieve the initialized connection pool or raise RuntimeError."""
    if _pool is None:
        raise RuntimeError("Database connection pool is not initialized")
    return _pool


async def check_db_health() -> bool:
    """Ping database to confirm health for readiness check."""
    if _pool is None:
        return False
    try:
        async with _pool.acquire() as conn:
            val = await conn.fetchval("SELECT 1")
            return bool(val == 1)
    except Exception as e:
        logger.warning(f"Database readiness ping failed: {e}")
        return False


@asynccontextmanager
async def get_user_db_session(
    user_id: UUID, full_claims: dict[str, Any]
) -> AsyncGenerator[asyncpg.Connection, None]:
    """Execute user-scoped database operations with transaction-local RLS claims.

    Guarantees:
    1. Executed inside an explicit transaction.
    2. Role is set to 'authenticated'.
    3. Parameterized set_config() sets request.jwt.claims, request.jwt.claim.sub,
       and request.jwt.claim.role with is_local=true.
    4. Upon commit/rollback, all claims and local settings are wiped by PostgreSQL.
    """
    pool = get_pool()
    claims_json = json.dumps(full_claims)

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Constant SET LOCAL ROLE authenticated
            await conn.execute("SET LOCAL ROLE authenticated;")
            # Parameterized set_config calls to prevent SQL injection & ensure transaction locality
            await conn.execute("SELECT set_config('request.jwt.claims', $1, true);", claims_json)
            await conn.execute(
                "SELECT set_config('request.jwt.claim.sub', $1, true);", str(user_id)
            )
            await conn.execute(
                "SELECT set_config('request.jwt.claim.role', $1, true);", "authenticated"
            )
            yield conn


@asynccontextmanager
async def get_admin_db_session() -> AsyncGenerator[asyncpg.Connection, None]:
    """Execute administrative / system database operations inside a transaction.

    Used by administrative endpoints to perform authorized mutations and atomic audit logging.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            yield conn


async def acquire_super_admin_advisory_lock(conn: asyncpg.Connection) -> None:
    """Acquire a transaction-level advisory lock protecting super-admin role changes."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext('ragbait_super_admin_role_lock'));")
