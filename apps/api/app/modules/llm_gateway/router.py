"""FastAPI endpoints for LLM Providers, Model Registry, and Dynamic Quotas."""

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import StreamingResponse

from app.core.dependencies import (
    rate_limit_admin_mutate,
    rate_limit_admin_read,
    rate_limit_general,
    require_admin,
    require_approved_user,
    require_not_suspended,
)
from app.modules.llm_gateway import service
from app.modules.llm_gateway.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    LLMModelCreate,
    LLMModelListResponse,
    LLMModelResponse,
    LLMModelUpdate,
    LLMProviderResponse,
    LLMProviderUpdate,
    ProviderQuotaCreate,
    ProviderQuotaListResponse,
    ProviderQuotaResponse,
    ProviderQuotaUpdate,
    ProviderSyncRequest,
    ProviderSyncResult,
)
from app.modules.users.schemas import UserProfile

# Routers
llm_router = APIRouter(prefix="/llm", tags=["LLM Gateway"])
admin_llm_router = APIRouter(prefix="/admin", tags=["LLM Admin Registry"])


# ==============================================================================
# Public / Authenticated Gateway Endpoints
# ==============================================================================
@llm_router.get(
    "/providers",
    response_model=list[LLMProviderResponse],
    dependencies=[Depends(require_not_suspended), Depends(rate_limit_general)],
)
async def list_providers(
    only_enabled: Annotated[
        bool, Query(description="Filter to active/enabled providers only")
    ] = True,
) -> list[LLMProviderResponse]:
    """List available AI inference providers."""
    return await service.list_providers(only_enabled=only_enabled)


@llm_router.get(
    "/models",
    response_model=LLMModelListResponse,
    dependencies=[Depends(require_not_suspended), Depends(rate_limit_general)],
)
async def list_active_models(
    provider_id: Annotated[str | None, Query(description="Filter models by provider ID")] = None,
) -> LLMModelListResponse:
    """List active, routable AI models available for inference."""
    items = await service.list_models(provider_id=provider_id, active_only=True)
    return LLMModelListResponse(items=items, total=len(items))


@llm_router.get(
    "/models/{model_id:path}",
    response_model=LLMModelResponse,
    dependencies=[Depends(require_not_suspended), Depends(rate_limit_general)],
)
async def get_model_details(model_id: str) -> LLMModelResponse:
    """Get metadata, context window, and capabilities for a specific model."""
    return await service.get_model(model_id)


@llm_router.get(
    "/quotas",
    response_model=ProviderQuotaListResponse,
    dependencies=[Depends(require_not_suspended), Depends(rate_limit_general)],
)
async def list_active_quotas(
    provider_id: Annotated[str | None, Query(description="Filter quotas by provider ID")] = None,
    scope_id: Annotated[str | None, Query(description="Filter quotas by scope ID")] = None,
) -> ProviderQuotaListResponse:
    """List active provider rate limit and quota envelopes."""
    items = await service.list_quotas(provider_id=provider_id, scope_id=scope_id, active_only=True)
    return ProviderQuotaListResponse(items=items, total=len(items))


@llm_router.post(
    "/chat/completions",
    response_model=ChatCompletionResponse,
    dependencies=[Depends(rate_limit_general)],
)
async def chat_completions(
    req: ChatCompletionRequest,
    current_user: Annotated[UserProfile, Depends(require_approved_user)],
) -> Response:
    """Execute unified AI chat completion through provider adapters with streaming or non-streaming."""
    result, is_streaming = await service.dispatch_chat_completion(
        user_id=current_user.id,
        req=req,
    )

    if is_streaming:
        assert isinstance(result, AsyncIterator)
        return StreamingResponse(
            content=result,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    assert isinstance(result, ChatCompletionResponse)
    return Response(
        content=result.model_dump_json(),
        media_type="application/json",
        status_code=status.HTTP_200_OK,
    )


# ==============================================================================
# Administrator Registry Endpoints
# ==============================================================================
@admin_llm_router.get(
    "/models",
    response_model=LLMModelListResponse,
    dependencies=[Depends(require_admin), Depends(rate_limit_admin_read)],
)
async def admin_list_all_models(
    provider_id: Annotated[str | None, Query(description="Filter models by provider ID")] = None,
    availability_status: Annotated[
        str | None, Query(description="Filter by lifecycle status")
    ] = None,
) -> LLMModelListResponse:
    """List all models across active, disabled, deprecated, and shutdown lifecycle states."""
    items = await service.list_models(
        provider_id=provider_id,
        availability_status=availability_status,
        active_only=False,
    )
    return LLMModelListResponse(items=items, total=len(items))


@admin_llm_router.post(
    "/models",
    response_model=LLMModelResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_admin_mutate)],
)
async def admin_register_model(
    data: LLMModelCreate,
    admin: Annotated[UserProfile, Depends(require_admin)],
) -> LLMModelResponse:
    """Register a new AI model in the administrator-controlled catalog."""
    return await service.create_model(data=data, actor_id=admin.id)


@admin_llm_router.patch(
    "/models/{model_id:path}",
    response_model=LLMModelResponse,
    dependencies=[Depends(rate_limit_admin_mutate)],
)
async def admin_update_model(
    model_id: str,
    data: LLMModelUpdate,
    admin: Annotated[UserProfile, Depends(require_admin)],
) -> LLMModelResponse:
    """Update model capabilities, pricing, availability status, or shutdown deprecation."""
    return await service.update_model(model_id=model_id, data=data, actor_id=admin.id)


@admin_llm_router.patch(
    "/providers/{provider_id}",
    response_model=LLMProviderResponse,
    dependencies=[Depends(rate_limit_admin_mutate)],
)
async def admin_update_provider(
    provider_id: str,
    data: LLMProviderUpdate,
    admin: Annotated[UserProfile, Depends(require_admin)],
) -> LLMProviderResponse:
    """Update provider settings, account tier, or billing status."""
    return await service.update_provider(provider_id=provider_id, data=data, actor_id=admin.id)


@admin_llm_router.get(
    "/provider-quotas",
    response_model=ProviderQuotaListResponse,
    dependencies=[Depends(require_admin), Depends(rate_limit_admin_read)],
)
async def admin_list_quotas(
    provider_id: Annotated[str | None, Query()] = None,
    scope_id: Annotated[str | None, Query()] = None,
) -> ProviderQuotaListResponse:
    """List all dynamic quotas with complete provenance and confidence metadata."""
    items = await service.list_quotas(provider_id=provider_id, scope_id=scope_id, active_only=False)
    return ProviderQuotaListResponse(items=items, total=len(items))


@admin_llm_router.post(
    "/provider-quotas",
    response_model=ProviderQuotaResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_admin_mutate)],
)
async def admin_create_quota(
    data: ProviderQuotaCreate,
    admin: Annotated[UserProfile, Depends(require_admin)],
) -> ProviderQuotaResponse:
    """Create a new dynamic quota rule with verification provenance."""
    return await service.create_quota(data=data, actor_id=admin.id)


@admin_llm_router.patch(
    "/provider-quotas/{quota_id}",
    response_model=ProviderQuotaResponse,
    dependencies=[Depends(rate_limit_admin_mutate)],
)
async def admin_update_quota(
    quota_id: UUID,
    data: ProviderQuotaUpdate,
    admin: Annotated[UserProfile, Depends(require_admin)],
) -> ProviderQuotaResponse:
    """Update limit value, confidence, or active status on a dynamic quota rule."""
    return await service.update_quota(quota_id=quota_id, data=data, actor_id=admin.id)


@admin_llm_router.post(
    "/providers/{provider_id}/sync",
    response_model=ProviderSyncResult,
    dependencies=[Depends(rate_limit_admin_mutate)],
)
async def admin_sync_provider(
    provider_id: str,
    admin: Annotated[UserProfile, Depends(require_admin)],
    request: ProviderSyncRequest | None = None,
) -> ProviderSyncResult:
    """Trigger provider discovery and synchronization probe."""
    discovered = request.discovered_models if request else None
    return await service.sync_provider_models(
        provider_id=provider_id, actor_id=admin.id, discovered_models=discovered
    )
