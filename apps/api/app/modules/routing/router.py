"""FastAPI router for Intelligent Inference Routing inspection and preview."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.dependencies import rate_limit_general, require_chat_entitlement
from app.modules.llm_gateway.schemas import ChatCompletionRequest
from app.modules.routing import service
from app.modules.routing.schemas import RoutePreviewResponse
from app.modules.users.schemas import UserProfile

routing_router = APIRouter(prefix="/routing", tags=["Inference Routing"])


@routing_router.post(
    "/preview",
    response_model=RoutePreviewResponse,
    dependencies=[Depends(rate_limit_general)],
)
async def preview_route_decision(
    req: ChatCompletionRequest,
    current_user: Annotated[UserProfile, Depends(require_chat_entitlement)],
) -> RoutePreviewResponse:
    """Preview prospective route selection, eligible candidates, and ranking without executing."""
    return await service.preview_route(user_id=current_user.id, req=req)
