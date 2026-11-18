"""Admission control orchestrator for rate limits, concurrency leases, and usage ledger."""

import math
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.config import settings
from app.core.database import get_pool
from app.core.errors import AuthorizationError, ConflictError, NotFoundError
from app.modules.admission.concurrency import (
    acquire_concurrency_lease,
    release_concurrency_lease,
    renew_concurrency_lease,
)
from app.modules.admission.cooldown import check_cooldown
from app.modules.admission.errors import (
    AdmissionRejectionError,
    PricingUnavailableError,
)
from app.modules.admission.ledger import (
    commit_dispatch_transactional,
    get_user_usage_summary,
    mark_outcome_unknown_transactional,
    release_reservation_transactional,
    reserve_budget_transactional,
    settle_usage_transactional,
)
from app.modules.admission.rate_limiter import (
    RateLimitScope,
    release_rate_slots,
    reserve_rate_slots,
)
from app.modules.admission.schemas import (
    AdmissionRequest,
    AdmissionReservation,
    LeaseRenewalResult,
    ReleaseResult,
    SettlementRequest,
    SettlementResult,
    UsageSummaryResponse,
)


def _compute_ceiling_cost(
    input_tokens: int,
    output_tokens: int,
    input_cost_per_million: int,
    output_cost_per_million: int,
) -> int:
    """Calculate maximum estimated cost in integer cents using ceiling division."""
    if input_cost_per_million == 0 and output_cost_per_million == 0:
        return 0

    cost_cents_float = (
        (input_tokens * input_cost_per_million) + (output_tokens * output_cost_per_million)
    ) / 1_000_000.0
    return math.ceil(cost_cents_float)


async def _get_model_details(model_id: str) -> dict[str, Any]:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, provider_id, availability_status, release_stage,
                   free_tier_eligible, input_cost_per_million_cents, output_cost_per_million_cents
            FROM public.llm_models
            WHERE id = $1
            """,
            model_id,
        )
        if not row:
            raise NotFoundError(f"Model '{model_id}' not found in registry")
        return dict(row)


async def _get_credential_details(credential_id: UUID) -> dict[str, Any]:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, owner_id, provider_id, nickname, status, validation_status,
                   sharing_scope, shared_consent, account_scope_id,
                   daily_request_limit, monthly_request_limit,
                   daily_spending_limit_cents, monthly_spending_limit_cents
            FROM public.api_key_metadata
            WHERE id = $1
            """,
            credential_id,
        )
        if not row:
            raise NotFoundError(f"Credential '{credential_id}' not found")
        return dict(row)


async def _get_applicable_quotas(
    provider_id: str,
    model_id: str,
    account_scope_id: str | None = None,
) -> list[dict[str, Any]]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT provider_id, scope_type, scope_id, model_id, limit_type, limit_value,
                   confidence, provenance
            FROM public.provider_quotas
            WHERE provider_id = $1
              AND is_active = true
              AND (model_id IS NULL OR model_id = $2)
              AND (
                scope_id = 'global'
                OR ($3::text IS NOT NULL AND scope_id = $3::text)
              )
            """,
            provider_id,
            model_id,
            account_scope_id,
        )
        return [dict(r) for r in rows]


async def reserve_generation(
    req: AdmissionRequest,
) -> AdmissionReservation:
    """Validate prerequisites, reserve multi-tier rate/concurrency, and persist budget reservation.

    This method is strictly internal and must never receive raw credentials or prompts.
    """
    # 1. Fetch & verify credential
    cred = await _get_credential_details(req.credential_id)
    if cred["status"] != "active":
        raise ConflictError(
            f"Credential '{req.credential_id}' is not active (status: {cred['status']})"
        )
    if cred["validation_status"] != "valid":
        raise ConflictError(
            f"Credential '{req.credential_id}' has not passed validation (status: {cred['validation_status']})"
        )

    # Cross-owner verification
    if cred["owner_id"] != req.user_id:
        if cred["sharing_scope"] != "community_shared" or not cred["shared_consent"]:
            raise AuthorizationError("Access to credential denied: private owner-only credential")

    # 2. Fetch & verify model
    model = await _get_model_details(req.model_id)
    if model["availability_status"] != "active":
        raise ConflictError(
            f"Model '{req.model_id}' is not available (status: {model['availability_status']})"
        )
    if model["provider_id"] != req.provider_id:
        raise ConflictError(
            f"Model '{req.model_id}' belongs to provider '{model['provider_id']}', not '{req.provider_id}'"
        )

    # 3. Calculate ceiling estimated cost
    input_cost = model["input_cost_per_million_cents"]
    output_cost = model["output_cost_per_million_cents"]

    has_spending_cap = (
        cred["daily_spending_limit_cents"] is not None
        or cred["monthly_spending_limit_cents"] is not None
    )
    if (
        has_spending_cap
        and input_cost == 0
        and output_cost == 0
        and not model["free_tier_eligible"]
    ):
        raise PricingUnavailableError(req.model_id)

    estimated_cost_cents = _compute_ceiling_cost(
        req.estimated_input_tokens,
        req.max_output_tokens,
        input_cost,
        output_cost,
    )

    pricing_snapshot = {
        "model_id": req.model_id,
        "input_cost_per_million_cents": input_cost,
        "output_cost_per_million_cents": output_cost,
        "free_tier_eligible": model["free_tier_eligible"],
        "timestamp": datetime.now(UTC).isoformat(),
    }

    # 4. Check active cooldowns
    cooldown_scopes: list[tuple[str, str]] = [
        ("provider", req.provider_id),
        ("model", req.model_id),
        ("credential", str(req.credential_id)),
    ]
    if req.account_scope_id:
        cooldown_scopes.append(("account", f"{req.provider_id}:{req.account_scope_id}"))

    is_cooling, ttl_remaining, active_scope = await check_cooldown(cooldown_scopes)
    if is_cooling:
        raise AdmissionRejectionError(
            reason=f"Provider cooldown active on {active_scope}",
            category="provider_cooldown",
            retry_after=ttl_remaining,
            details={"cooldown_scope": active_scope},
        )

    # 5. Resolve rate limit scopes & strictest applicable policy
    applicable_quotas = await _get_applicable_quotas(
        req.provider_id, req.model_id, req.account_scope_id
    )

    # Platform baseline safeguards
    user_rpm = settings.LLM_USER_RPM
    cred_rpm = settings.LLM_CREDENTIAL_RPM
    burst = settings.LLM_RATE_BURST

    # Inspect provider / model published quotas
    provider_rpm_quota: int | None = None
    model_rpm_quota: int | None = None
    for q in applicable_quotas:
        if q["limit_type"] == "rpm":
            if q["model_id"] == req.model_id:
                model_rpm_quota = min(model_rpm_quota or q["limit_value"], q["limit_value"])
            else:
                provider_rpm_quota = min(provider_rpm_quota or q["limit_value"], q["limit_value"])

    rate_scopes = [
        RateLimitScope(
            scope_name="user",
            key=f"ragbait:rate:user:{req.user_id}",
            limit=user_rpm,
            burst=burst,
        ),
        RateLimitScope(
            scope_name="credential",
            key=f"ragbait:rate:credential:{req.credential_id}",
            limit=cred_rpm,
            burst=burst,
        ),
    ]
    if provider_rpm_quota:
        rate_scopes.append(
            RateLimitScope(
                scope_name="provider",
                key=f"ragbait:rate:provider:{req.provider_id}",
                limit=provider_rpm_quota,
                burst=burst,
            )
        )
    if model_rpm_quota:
        rate_scopes.append(
            RateLimitScope(
                scope_name="model",
                key=f"ragbait:rate:model:{req.model_id}",
                limit=model_rpm_quota,
                burst=burst,
            )
        )

    # 6. Step A: Atomic distributed rate slot reservation
    await reserve_rate_slots(rate_scopes, member_id=req.request_id)

    # 7. Step B: Atomic distributed concurrency lease reservation
    lease: Any = None
    try:
        lease = await acquire_concurrency_lease(
            user_id=req.user_id,
            credential_id=req.credential_id,
        )
    except Exception:
        # Roll back acquired rate slot on concurrency failure
        await release_rate_slots(rate_scopes, member_id=req.request_id)
        raise

    # 8. Step C: Transactional PostgreSQL budget reservation & durable record
    try:
        reservation = await reserve_budget_transactional(
            user_id=req.user_id,
            credential_id=req.credential_id,
            provider_id=req.provider_id,
            model_id=req.model_id,
            idempotency_key=req.idempotency_key,
            request_id=req.request_id,
            estimated_input_tokens=req.estimated_input_tokens,
            estimated_output_tokens=req.max_output_tokens,
            estimated_cost_cents=estimated_cost_cents,
            pricing_snapshot=pricing_snapshot,
            lease_token=lease.lease_token,
            lease_expires_at=lease.expires_at,
            daily_request_limit=cred["daily_request_limit"],
            monthly_request_limit=cred["monthly_request_limit"],
            daily_spending_limit_cents=cred["daily_spending_limit_cents"],
            monthly_spending_limit_cents=cred["monthly_spending_limit_cents"],
            account_scope_id=req.account_scope_id,
        )
        return reservation
    except Exception:
        # Roll back Redis concurrency and rate slot on database failure
        await release_concurrency_lease(req.user_id, req.credential_id, lease.lease_token)
        await release_rate_slots(rate_scopes, member_id=req.request_id)
        raise


async def mark_dispatched(reservation_id: UUID) -> None:
    """Commit reservation as dispatched immediately before upstream provider network call."""
    await commit_dispatch_transactional(reservation_id)


async def renew_reservation_lease(
    user_id: UUID,
    credential_id: UUID,
    reservation_id: UUID,
    lease_token: str,
) -> LeaseRenewalResult:
    """Extend the duration of an active concurrency lease during long streaming generations."""
    new_expires_at = await renew_concurrency_lease(user_id, credential_id, lease_token)
    return LeaseRenewalResult(
        reservation_id=reservation_id,
        lease_token=lease_token,
        lease_expires_at=new_expires_at,
        is_active=True,
    )


async def settle_reservation(
    reservation_id: UUID,
    req: SettlementRequest,
    model_id: str | None = None,
) -> SettlementResult:
    """Settle actual usage metrics, release concurrency lease, and finalize accounting."""
    pool = get_pool()
    async with pool.acquire() as conn:
        res = await conn.fetchrow(
            """
            SELECT id, user_id, credential_id, model_id, lease_token, status
            FROM public.llm_usage_reservations
            WHERE id = $1
            """,
            reservation_id,
        )
        if not res:
            raise NotFoundError(f"Reservation '{reservation_id}' not found")

        # Compute actual cost if not explicitly supplied
        actual_cost = req.actual_cost_cents
        if actual_cost is None:
            active_model_id = model_id or res["model_id"]
            model = await _get_model_details(active_model_id)
            actual_cost = _compute_ceiling_cost(
                req.actual_input_tokens,
                req.actual_output_tokens,
                model["input_cost_per_million_cents"],
                model["output_cost_per_million_cents"],
            )

        settled = await settle_usage_transactional(
            reservation_id=reservation_id,
            actual_input_tokens=req.actual_input_tokens,
            actual_output_tokens=req.actual_output_tokens,
            actual_cost_cents=actual_cost,
            error_category=req.error_category,
        )

        # Release concurrency lease
        if res["lease_token"]:
            await release_concurrency_lease(
                res["user_id"], res["credential_id"], res["lease_token"]
            )

        return settled


async def release_reservation(
    reservation_id: UUID,
    reason: str = "pre_dispatch_cancellation",
) -> ReleaseResult:
    """Cancel and release an uncommitted reservation before dispatch."""
    pool = get_pool()
    async with pool.acquire() as conn:
        res = await conn.fetchrow(
            """
            SELECT id, user_id, credential_id, provider_id, model_id, request_id, lease_token, status
            FROM public.llm_usage_reservations
            WHERE id = $1
            """,
            reservation_id,
        )
        if not res:
            raise NotFoundError(f"Reservation '{reservation_id}' not found")

        released = await release_reservation_transactional(reservation_id, reason=reason)

        # Release Redis concurrency lease
        if res["lease_token"]:
            await release_concurrency_lease(
                res["user_id"], res["credential_id"], res["lease_token"]
            )

        # Release Redis rate slot
        rate_scopes = [
            RateLimitScope("user", f"ragbait:rate:user:{res['user_id']}", 10, 1),
            RateLimitScope("credential", f"ragbait:rate:credential:{res['credential_id']}", 5, 1),
            RateLimitScope("provider", f"ragbait:rate:provider:{res['provider_id']}", 100, 1),
            RateLimitScope("model", f"ragbait:rate:model:{res['model_id']}", 100, 1),
        ]
        await release_rate_slots(rate_scopes, member_id=res["request_id"])

        return released


async def mark_outcome_unknown(
    reservation_id: UUID,
    reason: str = "network_timeout",
) -> None:
    """Record an ambiguous upstream network failure, conservatively retaining budget reservation."""
    pool = get_pool()
    async with pool.acquire() as conn:
        res = await conn.fetchrow(
            """
            SELECT id, user_id, credential_id, lease_token
            FROM public.llm_usage_reservations
            WHERE id = $1
            """,
            reservation_id,
        )
        if not res:
            raise NotFoundError(f"Reservation '{reservation_id}' not found")

        await mark_outcome_unknown_transactional(reservation_id, reason=reason)

        # Release concurrency lease so other requests are not blocked
        if res["lease_token"]:
            await release_concurrency_lease(
                res["user_id"], res["credential_id"], res["lease_token"]
            )


async def get_usage_summary_for_user(user_id: UUID) -> UsageSummaryResponse:
    """Retrieve personal usage statistics and recent reservation ledger for contributor."""
    return await get_user_usage_summary(user_id)
