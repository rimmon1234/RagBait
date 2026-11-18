"""Tests for health endpoints."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest


@pytest.mark.asyncio
async def test_liveness_endpoint(client: httpx.AsyncClient) -> None:
    """GET /health/live returns 200 alive."""
    resp = await client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive"}


@pytest.mark.asyncio
async def test_readiness_healthy(client: httpx.AsyncClient) -> None:
    """GET /health/ready returns 200 when DB and Redis are healthy."""
    with (
        patch("app.api.v1.health.check_db_health", new_callable=AsyncMock, return_value=True),
        patch("app.api.v1.health.check_redis_health", new_callable=AsyncMock, return_value=True),
    ):
        resp = await client.get("/health/ready")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_readiness_degraded_returns_503(client: httpx.AsyncClient) -> None:
    """GET /health/ready returns 503 when a dependency is unhealthy."""
    with (
        patch("app.api.v1.health.check_db_health", new_callable=AsyncMock, return_value=False),
        patch("app.api.v1.health.check_redis_health", new_callable=AsyncMock, return_value=True),
    ):
        resp = await client.get("/health/ready")
        assert resp.status_code == 503
        assert resp.json()["status"] == "degraded"
