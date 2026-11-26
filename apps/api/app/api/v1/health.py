"""Liveness and readiness probes."""

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.core.database import check_db_health
from app.core.redis import check_redis_health
from app.modules.credentials.vault import check_vault_health

router = APIRouter(prefix="/health", tags=["Health"])


class HealthResponse(BaseModel):
    status: str
    database: bool
    redis: bool
    vault: bool = True
    admission_ready: bool


@router.get("/live", response_model=dict[str, str])
async def liveness() -> dict[str, str]:
    """Basic process liveness check."""
    return {"status": "alive"}


@router.get("/ready", response_model=HealthResponse)
async def readiness(response: Response) -> HealthResponse:
    """Readiness probe checking database, redis, and vault dependencies."""
    db_ok = await check_db_health()
    redis_ok = await check_redis_health()
    vault_ok = await check_vault_health()

    is_healthy = db_ok and redis_ok and vault_ok
    if not is_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthResponse(
        status="ready" if is_healthy else "degraded",
        database=db_ok,
        redis=redis_ok,
        vault=vault_ok,
        admission_ready=is_healthy,
    )
