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

    # Initialize connection pools if not running in test mode with mocks
    try:
        await init_db_pool()
    except Exception:
        # Allow starting up in dev/test if DB not immediately up
        pass

    try:
        await init_redis()
    except Exception:
        pass

    try:
        await init_community_redis()
    except Exception:
        pass

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

    # Clean shutdown
    sweeper_task.cancel()
    community_sweeper_task.cancel()
    try:
        await sweeper_task
    except (asyncio.CancelledError, Exception):
        pass
    try:
        await community_sweeper_task
    except (asyncio.CancelledError, Exception):
        pass

    await close_db_pool()
    await close_redis()
    await close_community_redis()
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
