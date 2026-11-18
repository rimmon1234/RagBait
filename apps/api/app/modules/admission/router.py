"""FastAPI Router for Contributor Usage Ledger & Quota Consumption."""

from fastapi import APIRouter, Depends

from app.core.dependencies import rate_limit_general, require_approved_user
from app.modules.admission.schemas import UsageSummaryResponse
from app.modules.admission.service import get_usage_summary_for_user
from app.modules.users.schemas import UserProfile

router = APIRouter(prefix="/me/usage", tags=["Usage Ledger"])


@router.get(
    "",
    response_model=UsageSummaryResponse,
    dependencies=[Depends(rate_limit_general)],
    summary="Get contributor usage summary and ledger",
)
async def get_usage_summary(
    current_user: UserProfile = Depends(require_approved_user),
) -> UsageSummaryResponse:
    """Retrieve personal usage consumption, budget aggregates, and recent reservations."""
    return await get_usage_summary_for_user(user_id=current_user.id)
