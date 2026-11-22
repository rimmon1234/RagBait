"""FastAPI HTTP and SSE endpoints for Community Local Inference."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.core.dependencies import (
    get_trusted_client_ip,
    rate_limit_general,
    rate_limit_pair_code_create,
    require_approved_user,
)
from app.core.errors import AuthorizationError, NotFoundError
from app.core.redis import check_rate_limit
from app.modules.community import repository, service, streams
from app.modules.community.schemas import (
    CommunityAvailableModel,
    CommunityJobStatusResponse,
    CommunityJobSubmitRequest,
    CommunityJobSubmitResponse,
    CommunityNodeModelResponse,
    CommunityNodeModelsUpdateRequest,
    CommunityNodeResponse,
    CommunityNodeUpdateRequest,
    NodePairRequest,
    NodePairResponse,
    PairingCodeResponse,
)
from app.modules.community.ws import ws_router
from app.modules.users.schemas import UserProfile

logger = logging.getLogger("ragbait.community.router")

community_router = APIRouter(prefix="/community", tags=["Community Local Inference"])
community_router.include_router(ws_router)


# ------------------------------------------------------------------------------
# 1. Contributor Node Management Endpoints
# ------------------------------------------------------------------------------
@community_router.post(
    "/nodes/pair-code",
    response_model=PairingCodeResponse,
    dependencies=[Depends(rate_limit_pair_code_create)],
)
async def create_pairing_code(
    current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> PairingCodeResponse:
    """Generate a single-use pairing code for registering an Ollama Node Agent."""
    return await service.generate_pairing_code(owner_id=current_user.id)


@community_router.post(
    "/nodes/pair",
    response_model=NodePairResponse,
    status_code=status.HTTP_201_CREATED,
)
async def pair_node_agent(
    req: NodePairRequest,
    request: Request,
) -> NodePairResponse:
    """Complete device pairing using a valid one-time pairing code with IP rate limiting."""
    client_ip = get_trusted_client_ip(request)
    await check_rate_limit(
        key_prefix="pair_attempt",
        identifier=client_ip,
        limit=settings.RATE_LIMIT_PAIR_ATTEMPT,
        window_seconds=300,
    )
    return await service.complete_pairing(req)


@community_router.get(
    "/nodes",
    response_model=list[CommunityNodeResponse],
    dependencies=[Depends(rate_limit_general)],
)
async def list_owned_nodes(
    current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> list[CommunityNodeResponse]:
    """List all community inference nodes owned by authenticated contributor."""
    return await service.list_user_nodes(owner_id=current_user.id)


@community_router.get(
    "/nodes/{node_id}",
    response_model=CommunityNodeResponse,
    dependencies=[Depends(rate_limit_general)],
)
async def get_owned_node(
    node_id: UUID,
    current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> CommunityNodeResponse:
    """Get details and model allowlist for an owned community node."""
    return await service.get_user_node(owner_id=current_user.id, node_id=node_id)


@community_router.patch(
    "/nodes/{node_id}",
    response_model=CommunityNodeResponse,
    dependencies=[Depends(rate_limit_general)],
)
async def update_node_configuration(
    node_id: UUID,
    req: CommunityNodeUpdateRequest,
    current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> CommunityNodeResponse:
    """Update settings, limits, or master sharing toggle for an owned node."""
    return await service.update_node_config(owner_id=current_user.id, node_id=node_id, req=req)


@community_router.put(
    "/nodes/{node_id}/models",
    response_model=list[CommunityNodeModelResponse],
    dependencies=[Depends(rate_limit_general)],
)
async def update_node_models(
    node_id: UUID,
    req: CommunityNodeModelsUpdateRequest,
    current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> list[CommunityNodeModelResponse]:
    """Update sharing status for local models discovered on an owned node."""
    return await service.update_node_models_sharing(
        owner_id=current_user.id, node_id=node_id, req=req
    )


@community_router.post(
    "/nodes/{node_id}/pause",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_general)],
)
async def pause_node(
    node_id: UUID,
    current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> None:
    """Pause an active node so no new inference jobs are dispatched to it."""
    await service.pause_node(owner_id=current_user.id, node_id=node_id)


@community_router.post(
    "/nodes/{node_id}/resume",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_general)],
)
async def resume_node(
    node_id: UUID,
    current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> None:
    """Resume a paused node."""
    await service.resume_node(owner_id=current_user.id, node_id=node_id)


@community_router.delete(
    "/nodes/{node_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_general)],
)
async def revoke_node(
    node_id: UUID,
    current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> None:
    """Permanently revoke a community node."""
    await service.revoke_node(owner_id=current_user.id, node_id=node_id)


# ------------------------------------------------------------------------------
# 2. Requester Community Inference Endpoints
# ------------------------------------------------------------------------------
@community_router.get(
    "/models",
    response_model=list[CommunityAvailableModel],
    dependencies=[Depends(rate_limit_general)],
)
async def list_available_community_models(
    _current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> list[CommunityAvailableModel]:
    """List distinct models actively shared across online community nodes."""
    return await service.list_available_models()


@community_router.post(
    "/jobs/submit",
    response_model=CommunityJobSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_general)],
)
async def submit_community_inference(
    req: CommunityJobSubmitRequest,
    current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> CommunityJobSubmitResponse:
    """Submit prompt for community local inference (enforces explicit consent)."""
    return await service.submit_community_job(requester_id=current_user.id, req=req)


@community_router.get(
    "/jobs/{job_id}",
    response_model=CommunityJobStatusResponse,
    dependencies=[Depends(rate_limit_general)],
)
async def get_community_job_status(
    job_id: UUID,
    current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> CommunityJobStatusResponse:
    """Retrieve status, completion state, and reported telemetry for a job."""
    return await service.get_job_status(user_id=current_user.id, job_id=job_id)


@community_router.post(
    "/jobs/{job_id}/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_general)],
)
async def cancel_community_job(
    job_id: UUID,
    current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> None:
    """Cancel an active or queued community inference job."""
    await service.cancel_job(user_id=current_user.id, job_id=job_id)


@community_router.get("/jobs/{job_id}/stream")
async def stream_community_job_results(
    job_id: UUID,
    current_user: Annotated[UserProfile, Depends(require_approved_user)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Stream token chunks from Redis Stream over Server-Sent Events (SSE).

    Requires authenticated requester ownership. Node owner is explicitly forbidden.
    """
    # Verify job exists
    job = await repository.get_job_by_id(job_id)
    if not job:
        raise NotFoundError("Inference job not found")

    # Authorize requester: strictly requester only (node owner cannot view content)
    if job["requester_id"] != current_user.id:
        raise AuthorizationError(
            "Access denied: You do not have permission to view this inference stream"
        )

    start_seq = 1
    if last_event_id:
        try:
            start_seq = int(last_event_id) + 1
        except ValueError:
            start_seq = 1

    async def sse_event_generator() -> AsyncGenerator[str, None]:
        # Yield initial SSE comment to commit HTTP headers and establish connection immediately
        yield ": connected\n\n"
        current_seq = start_seq
        max_poll_iterations = 600  # Up to 5 minutes timeout
        iterations = 0

        while iterations < max_poll_iterations:
            chunks = await streams.read_job_chunks_range(job_id, start_seq=current_seq)
            for c in chunks:
                seq = c["seq_id"]
                current_seq = seq + 1
                yield f"id: {seq}\nevent: message\ndata: {json.dumps(c)}\n\n"
                if c["done"]:
                    return

            # Check if job was cancelled or failed in DB
            refreshed = await repository.get_job_by_id(job_id)
            if refreshed and refreshed["status"] in ("CANCELLED", "FAILED", "OUTCOME_UNKNOWN"):
                err_data = {
                    "error": refreshed["status"],
                    "failure_code": refreshed.get("failure_code"),
                    "failure_details": refreshed.get("failure_details"),
                    "done": True,
                }
                yield f"id: {current_seq}\nevent: error\ndata: {json.dumps(err_data)}\n\n"
                return

            await asyncio.sleep(0.2)
            iterations += 1

    return StreamingResponse(
        sse_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
