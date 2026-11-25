"""WebSocket endpoint for Ollama Node Agents (DISCONTINUED).

Authoritative Product Policy:
A student's local Ollama models and computing resources are strictly private.
Community Node Agents are no longer permitted to connect or receive dispatch work.
"""

import logging

from fastapi import APIRouter, WebSocket, status

from app.modules.community.schemas import WSErrorMessage

logger = logging.getLogger("ragbait.community.ws")
ws_router = APIRouter()


@ws_router.websocket("/ws")
async def community_node_websocket(websocket: WebSocket) -> None:
    """Immediately reject outbound WebSocket connection from any Ollama Node Agent."""
    await websocket.accept()
    await websocket.send_text(
        WSErrorMessage(
            code="COMMUNITY_LOCAL_INFERENCE_DISCONTINUED",
            message=(
                "Community local inference and Ollama node sharing have been discontinued. "
                "A student's local Ollama models and computing resources remain strictly private."
            ),
        ).model_dump_json()
    )
    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
