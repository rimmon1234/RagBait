"""Isolated FastAPI worker entrypoint for M4.8 real multi-process Redis & SSE verification."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import uvicorn

import app.modules.chat.orchestration.generation as chat_generation
from app.main import app
from app.modules.llm_gateway.interfaces import GatewayStreamEvent, NormalizedUsage
from app.modules.llm_gateway.schemas import ChatCompletionResponse
from app.modules.routing.schemas import RouteDecision


async def _stub_route_and_dispatch(
    *,
    user_id: Any,
    req: Any,
    correlation_id: str | None = None,
) -> tuple[ChatCompletionResponse, bool, RouteDecision]:
    """Deterministic sync/repair provider stub for multi-process worker tests."""
    delay = float(os.environ.get("M48_STUB_DELAY_SECONDS", "0.15"))
    if delay > 0:
        await asyncio.sleep(min(delay, 0.25))

    content = "Validated study answer grounded in context [S1]."
    if any("citation-syntax correction pass" in m.content for m in req.messages):
        content = "Repaired grounded answer without invalid tags [S1]."

    resp = ChatCompletionResponse(
        id=f"cmpl-{uuid4().hex[:8]}",
        request_id=correlation_id or f"req-{uuid4().hex[:8]}",
        reservation_id=uuid4(),
        provider_id="openrouter",
        model_id="openrouter/m48-multiprocess-stub",
        resolved_model_id="m48-multiprocess-stub",
        upstream_provider="openrouter",
        content=content,
        finish_reason="stop",
        usage={
            "input_tokens": 120,
            "output_tokens": 24,
            "total_tokens": 144,
            "usage_source": "exact_provider_reported",
        },
        created_at=datetime.now(UTC),
    )
    decision = RouteDecision(
        routing_mode="cloud_only",
        selected_source="cloud",
        provider_id="openrouter",
        model_id="openrouter/m48-multiprocess-stub",
        resolved_model_id="m48-multiprocess-stub",
        decision_reason="m48_multiprocess_stub",
        created_at=datetime.now(UTC),
    )
    return resp, False, decision


async def _stub_stream_route_and_dispatch(
    *,
    user_id: Any,
    req: Any,
    correlation_id: str | None = None,
    cancel_check: Callable[[], bool] | None = None,
    first_token_timeout_seconds: float | None = None,
    idle_timeout_seconds: float | None = None,
) -> tuple[AsyncIterator[GatewayStreamEvent], RouteDecision]:
    """Deterministic SSE provider stub supporting configurable chunk delays, cancellation, and repair."""
    user_prompt = req.messages[-1].content if req.messages else ""
    total_delay = float(os.environ.get("M48_STUB_DELAY_SECONDS", "0.35"))
    if "SLOW_HOLD" in user_prompt:
        total_delay = 3.5
    elif "VERY_SLOW_CRASH" in user_prompt:
        total_delay = 15.0

    steps = int(os.environ.get("M48_STUB_STEPS", "5"))
    step_sleep = total_delay / max(1, steps)
    trigger_repair = "TRIGGER_REPAIR" in user_prompt

    decision = RouteDecision(
        routing_mode="cloud_only",
        selected_source="cloud",
        provider_id="openrouter",
        model_id="openrouter/m48-multiprocess-stub",
        resolved_model_id="m48-multiprocess-stub",
        decision_reason="m48_multiprocess_stub",
        created_at=datetime.now(UTC),
    )

    async def _gen() -> AsyncIterator[GatewayStreamEvent]:
        yield GatewayStreamEvent(
            event_type="started",
            resolved_model=decision.resolved_model_id,
            provider_id=decision.provider_id,
        )
        chunks = [f"Segment {i + 1} " for i in range(steps - 1)]
        chunks.append("grounded [S1] [S99]." if trigger_repair else "grounded [S1].")
        for chunk in chunks:
            await asyncio.sleep(step_sleep)
            if cancel_check and cancel_check():
                yield GatewayStreamEvent(event_type="cancelled", finish_reason="cancelled")
                return
            yield GatewayStreamEvent(event_type="delta", delta=chunk)

        yield GatewayStreamEvent(
            event_type="completed",
            finish_reason="stop",
            usage=NormalizedUsage(
                input_tokens=150, output_tokens=30, total_tokens=180, usage_source="authoritative"
            ),
        )

    return _gen(), decision


chat_generation.route_and_dispatch = _stub_route_and_dispatch  # type: ignore[assignment]
chat_generation.stream_route_and_dispatch = _stub_stream_route_and_dispatch  # type: ignore[assignment]


if __name__ == "__main__":
    port = int(os.environ.get("M48_WORKER_PORT", "18101"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
