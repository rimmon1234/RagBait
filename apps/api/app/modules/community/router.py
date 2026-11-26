"""FastAPI HTTP and SSE endpoints for Community Local Inference (DISCONTINUED).

Authoritative Product Policy:
A student's locally installed Ollama models and local computing resources
must remain exclusively available to that student. RagBait strictly prohibits
donating, pooling, sharing, or exposing local models or Ollama instances
for use by other students.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.core.dependencies import rate_limit_general, require_approved_user
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

community_router = APIRouter(prefix="/community", tags=["Community Local Inference"])
community_router.include_router(ws_router)

DISCONTINUED_DETAIL = {
    "code": "COMMUNITY_LOCAL_INFERENCE_DISCONTINUED",
    "message": (
        "Community local inference and Ollama node sharing have been discontinued. "
        "A student's local Ollama models and computing resources remain strictly private to their owner."
    ),
}


def _raise_discontinued() -> None:
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=DISCONTINUED_DETAIL,
    )


# ------------------------------------------------------------------------------
# 1. Contributor Node Management Endpoints (Discontinued - 410 Gone)
# ------------------------------------------------------------------------------
@community_router.post(
    "/nodes/pair-code",
    response_model=PairingCodeResponse,
    dependencies=[Depends(rate_limit_general)],
)
async def create_pairing_code(
    _current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> PairingCodeResponse:
    """Discontinued: Node pairing is no longer supported."""
    _raise_discontinued()
    raise RuntimeError("unreachable")


@community_router.post(
    "/nodes/pair",
    response_model=NodePairResponse,
    status_code=status.HTTP_201_CREATED,
)
async def pair_node_agent(
    _req: NodePairRequest,
    _request: Request,
) -> NodePairResponse:
    """Discontinued: Node registration is no longer supported."""
    _raise_discontinued()
    raise RuntimeError("unreachable")


@community_router.get(
    "/nodes",
    response_model=list[CommunityNodeResponse],
    dependencies=[Depends(rate_limit_general)],
)
async def list_owned_nodes(
    _current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> list[CommunityNodeResponse]:
    """Safe backward-compatibility list: returns empty list as community nodes are retired."""
    return []


@community_router.get(
    "/nodes/{node_id}",
    response_model=CommunityNodeResponse,
    dependencies=[Depends(rate_limit_general)],
)
async def get_owned_node(
    node_id: UUID,
    _current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> CommunityNodeResponse:
    """Discontinued."""
    _raise_discontinued()
    raise RuntimeError("unreachable")


@community_router.patch(
    "/nodes/{node_id}",
    response_model=CommunityNodeResponse,
    dependencies=[Depends(rate_limit_general)],
)
async def update_node_configuration(
    node_id: UUID,
    _req: CommunityNodeUpdateRequest,
    _current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> CommunityNodeResponse:
    """Discontinued."""
    _raise_discontinued()
    raise RuntimeError("unreachable")


@community_router.put(
    "/nodes/{node_id}/models",
    response_model=list[CommunityNodeModelResponse],
    dependencies=[Depends(rate_limit_general)],
)
async def update_node_models(
    node_id: UUID,
    _req: CommunityNodeModelsUpdateRequest,
    _current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> list[CommunityNodeModelResponse]:
    """Discontinued."""
    _raise_discontinued()
    raise RuntimeError("unreachable")


@community_router.post(
    "/nodes/{node_id}/pause",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_general)],
)
async def pause_node(
    node_id: UUID,
    _current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> None:
    """Discontinued."""
    _raise_discontinued()


@community_router.post(
    "/nodes/{node_id}/resume",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_general)],
)
async def resume_node(
    node_id: UUID,
    _current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> None:
    """Discontinued."""
    _raise_discontinued()


@community_router.delete(
    "/nodes/{node_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_general)],
)
async def revoke_node(
    node_id: UUID,
    _current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> None:
    """Discontinued."""
    _raise_discontinued()


# ------------------------------------------------------------------------------
# 2. Requester Community Inference Endpoints (Discontinued - 410 Gone)
# ------------------------------------------------------------------------------
@community_router.get(
    "/models",
    response_model=list[CommunityAvailableModel],
    dependencies=[Depends(rate_limit_general)],
)
async def list_available_community_models(
    _current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> list[CommunityAvailableModel]:
    """Safe backward-compatibility list: returns empty list as no community models are shared."""
    return []


@community_router.post(
    "/jobs/submit",
    response_model=CommunityJobSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(rate_limit_general)],
)
async def submit_community_inference(
    _req: CommunityJobSubmitRequest,
    _current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> CommunityJobSubmitResponse:
    """Discontinued: Community inference job submission is disabled."""
    _raise_discontinued()
    raise RuntimeError("unreachable")


@community_router.get(
    "/jobs/{job_id}",
    response_model=CommunityJobStatusResponse,
    dependencies=[Depends(rate_limit_general)],
)
async def get_community_job_status(
    job_id: UUID,
    _current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> CommunityJobStatusResponse:
    """Discontinued."""
    _raise_discontinued()
    raise RuntimeError("unreachable")


@community_router.post(
    "/jobs/{job_id}/cancel",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit_general)],
)
async def cancel_community_job(
    job_id: UUID,
    _current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> None:
    """Discontinued."""
    _raise_discontinued()


@community_router.get("/jobs/{job_id}/stream")
async def stream_community_job_results(
    job_id: UUID,
    _current_user: Annotated[UserProfile, Depends(require_approved_user)],
    _last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Discontinued: Community inference stream is disabled."""
    _raise_discontinued()
    raise RuntimeError("unreachable")
