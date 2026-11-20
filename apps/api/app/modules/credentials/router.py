"""FastAPI Router for Contributor API Credential Management."""

from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.core.dependencies import (
    rate_limit_general,
    rate_limiter,
    require_approved_user,
)
from app.modules.credentials.schemas import (
    CredentialCreateRequest,
    CredentialListResponse,
    CredentialResponse,
    CredentialUpdateRequest,
    ValidationResultResponse,
)
from app.modules.credentials.service import (
    create_user_credential,
    delete_user_credential,
    disable_user_credential,
    enable_user_credential,
    get_user_credential,
    list_user_credentials,
    update_user_credential,
    validate_user_credential,
)
from app.modules.users.schemas import UserProfile

router = APIRouter(prefix="/me/api-keys", tags=["Credentials"])

# Dedicated rate limiter for non-generative validation probes (10 per minute per user)
rate_limit_validation = rate_limiter("credential_validate", limit=10, window=60)


@router.get(
    "",
    response_model=CredentialListResponse,
    dependencies=[Depends(rate_limit_general)],
    summary="List owned API credentials",
)
async def list_credentials(
    current_user: UserProfile = Depends(require_approved_user),
) -> CredentialListResponse:
    """List all API credentials owned by the authenticated contributor."""
    return await list_user_credentials(owner_id=current_user.id)


@router.post(
    "",
    response_model=CredentialResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_general)],
    summary="Add and encrypt an API credential",
)
async def create_credential(
    payload: CredentialCreateRequest,
    current_user: UserProfile = Depends(require_approved_user),
) -> CredentialResponse:
    """Store and encrypt a new API credential in the vault."""
    return await create_user_credential(owner_id=current_user.id, payload=payload)


@router.get(
    "/{credential_id}",
    response_model=CredentialResponse,
    dependencies=[Depends(rate_limit_general)],
    summary="Get credential metadata",
)
async def get_credential(
    credential_id: UUID,
    current_user: UserProfile = Depends(require_approved_user),
) -> CredentialResponse:
    """Retrieve non-sensitive metadata for an owned API credential."""
    return await get_user_credential(owner_id=current_user.id, credential_id=credential_id)


@router.patch(
    "/{credential_id}",
    response_model=CredentialResponse,
    dependencies=[Depends(rate_limit_general)],
    summary="Update credential metadata and limits",
)
async def update_credential(
    credential_id: UUID,
    payload: CredentialUpdateRequest,
    current_user: UserProfile = Depends(require_approved_user),
) -> CredentialResponse:
    """Update permitted metadata, sharing preferences, and limits for an owned credential."""
    return await update_user_credential(
        owner_id=current_user.id, credential_id=credential_id, payload=payload
    )


@router.post(
    "/{credential_id}/validate",
    response_model=ValidationResultResponse,
    dependencies=[Depends(rate_limit_validation)],
    summary="Test credential validity via safe probe",
)
async def validate_credential(
    credential_id: UUID,
    current_user: UserProfile = Depends(require_approved_user),
) -> ValidationResultResponse:
    """Run a safe, non-generative authentication check on an owned credential."""
    return await validate_user_credential(owner_id=current_user.id, credential_id=credential_id)


@router.post(
    "/{credential_id}/enable",
    response_model=CredentialResponse,
    dependencies=[Depends(rate_limit_general)],
    summary="Enable an owned, validated credential",
)
async def enable_credential(
    credential_id: UUID,
    current_user: UserProfile = Depends(require_approved_user),
) -> CredentialResponse:
    """Enable an owned credential that has passed validation."""
    return await enable_user_credential(owner_id=current_user.id, credential_id=credential_id)


@router.post(
    "/{credential_id}/disable",
    response_model=CredentialResponse,
    dependencies=[Depends(rate_limit_general)],
    summary="Disable an owned credential",
)
async def disable_credential(
    credential_id: UUID,
    current_user: UserProfile = Depends(require_approved_user),
) -> CredentialResponse:
    """Disable an owned credential."""
    return await disable_user_credential(owner_id=current_user.id, credential_id=credential_id)


@router.delete(
    "/{credential_id}",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit_general)],
    summary="Delete an owned credential and encrypted secret",
)
async def delete_credential(
    credential_id: UUID,
    current_user: UserProfile = Depends(require_approved_user),
) -> dict[str, str | bool]:
    """Permanently delete an owned credential and its encrypted vault secret."""
    return await delete_user_credential(owner_id=current_user.id, credential_id=credential_id)
