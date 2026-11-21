"""RagBait FastAPI Application Entrypoint."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.health import router as root_health_router
from app.api.v1.router import api_v1_router
from app.core.config import settings
from app.core.database import close_db_pool, init_db_pool
from app.core.errors import register_error_handlers
from app.core.logging import RequestLoggingMiddleware, setup_logging
from app.core.redis import (
    close_redis,
    init_redis,
)
from app.modules.credentials.vault import close_vault_pool, init_vault_pool

logger = logging.getLogger("ragbait.api")


async def _run_reservation_sweeper_task() -> None:
    """Background periodic loop to sweep and recover expired reservations."""
    from app.modules.admission.service import recover_expired_reservations

    while True:
        try:
            await asyncio.sleep(settings.LLM_SWEEPER_INTERVAL_SECONDS)
            swept = await recover_expired_reservations(batch_size=settings.LLM_SWEEPER_BATCH_SIZE)
            if swept > 0:
                logger.info(
                    f"Background reservation sweeper recovered {swept} expired reservations"
                )
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning(f"Background reservation sweeper encountered error: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifespan: database, redis, vault, and http connection lifecycles."""
    setup_logging(level="DEBUG" if settings.DEBUG else "INFO")

    initialized_resources: list[str] = []
    sweeper_task: asyncio.Task[None] | None = None

    try:
        # 1. Initialize main database pool
        try:
            await init_db_pool()
            initialized_resources.append("db_pool")
        except Exception as exc:
            logger.warning(f"Database pool init warning: {exc}")

        # 2. Initialize redis
        try:
            await init_redis()
            initialized_resources.append("redis")
        except Exception as exc:
            logger.warning(f"Redis init warning: {exc}")

        # 3. Initialize HTTP client
        try:
            from app.modules.llm_gateway.http_client import init_http_client

            await init_http_client()
            initialized_resources.append("http_client")
        except Exception as exc:
            logger.warning(f"HTTP client init warning: {exc}")

        # 4. Initialize dedicated vault pool (strictly after database)
        try:
            await init_vault_pool()
            initialized_resources.append("vault_pool")
        except Exception as exc:
            logger.error(f"Dedicated vault pool init failed: {exc}")
            # If in non-test mode or strict mode, fail startup or set degraded state
            if settings.ENVIRONMENT not in ("test", "testing"):
                raise

        # Startup recovery sweep for any reservations abandoned by crashed previous workers
        try:
            from app.modules.admission.service import recover_expired_reservations

            startup_swept = await recover_expired_reservations(
                batch_size=settings.LLM_SWEEPER_BATCH_SIZE * 2
            )
            if startup_swept > 0:
                logger.info(
                    f"Startup recovery swept {startup_swept} expired dangling reservations"
                )
        except Exception as exc:
            logger.warning(f"Startup reservation recovery failed: {exc}")

        # Launch background periodic sweeper task for reservations
        sweeper_task = asyncio.create_task(_run_reservation_sweeper_task())

        yield

    finally:
        # Clean shutdown in reverse dependency order
        if sweeper_task is not None:
            sweeper_task.cancel()
            try:
                await sweeper_task
            except (asyncio.CancelledError, Exception):
                pass

        # 1. Close vault pool first before closing underlying database
        if "vault_pool" in initialized_resources:
            try:
                await close_vault_pool()
            except Exception as exc:
                logger.warning(f"Error closing vault pool: {exc}")

        # 2. Close main DB pool
        if "db_pool" in initialized_resources:
            try:
                await close_db_pool()
            except Exception as exc:
                logger.warning(f"Error closing db pool: {exc}")

        # 3. Close redis
        if "redis" in initialized_resources:
            try:
                await close_redis()
            except Exception as exc:
                logger.warning(f"Error closing redis: {exc}")

        # 4. Close HTTP client
        if "http_client" in initialized_resources:
            try:
                from app.modules.llm_gateway.http_client import close_http_client

                await close_http_client()
            except Exception as exc:
                logger.warning(f"Error closing http client: {exc}")


def create_app() -> FastAPI:
    """Application factory for RagBait API."""
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version="0.1.0",
        lifespan=lifespan,
    )

    # Global error handlers
    register_error_handlers(app)

    # Middleware
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API routers
    app.include_router(api_v1_router, prefix=settings.API_V1_PREFIX)
    # Also include root-level health endpoints for orchestrators / load balancers
    app.include_router(root_health_router)

    return app


app = create_app()
