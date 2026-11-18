"""RagBait FastAPI Application Entrypoint."""

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
from app.core.redis import close_redis, init_redis


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application lifespan: database and redis connection lifecycles."""
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

    yield

    # Clean shutdown
    await close_db_pool()
    await close_redis()


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
