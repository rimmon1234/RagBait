"""PostgreSQL transactional repository for usage reservations, budget counters, and audit events."""

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from app.core.database import get_pool
from app.core.errors import AppError, NotFoundError
from app.modules.admission.errors import BudgetExhaustedError, InvalidStateTransitionError
from app.modules.admission.schemas import (
    AdmissionReservation,
    ReleaseResult,
    ReservationView,
    SettlementResult,
    UsageSummaryResponse,
)

logger = logging.getLogger("ragbait.admission.ledger")


def compute_cost_microcents(
    input_tokens: int,
    output_tokens: int,
    input_cost_per_million_cents: int,
    output_cost_per_million_cents: int,
) -> int:
    """Calculate exact cost in integer microcents ($10^-6 cents = $10^-8 USD).

    Monetary unit definitions:
      - input_cost_per_million_cents: Cents per 1,000,000 tokens ($10^-2 USD / 10^6 tokens).
      - Output cost per token in microcents = output_cost_per_million_cents.
      - Input cost per token in microcents = input_cost_per_million_cents.
      - 1 USD = 100 Cents = 100,000,000 Microcents.
      - 1 Cent = 1,000,000 Microcents.
    """
    return (input_tokens * input_cost_per_million_cents) + (
        output_tokens * output_cost_per_million_cents
    )


def microcents_to_ceiling_cents(cost_microcents: int) -> int:
    """Conservatively round upward to whole cents for budget reservations and display."""
    if cost_microcents <= 0:
        return 0
    return (cost_microcents + 999_999) // 1_000_000


def get_period_boundaries(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Calculate the UTC start of the current day and current month."""
    t = now or datetime.now(UTC)
    day_start = datetime(t.year, t.month, t.day, 0, 0, 0, tzinfo=UTC)
    month_start = datetime(t.year, t.month, 1, 0, 0, 0, tzinfo=UTC)
    return day_start, month_start


async def _get_or_create_counter_row(
    conn: asyncpg.Connection,
    credential_id: UUID,
    period_type: str,
    period_start: datetime,
) -> asyncpg.Record:
    """Fetch budget counter row with FOR UPDATE lock, initializing if missing."""
    row = await conn.fetchrow(
        """
        SELECT credential_id, period_type, period_start,
               reserved_requests, dispatched_requests,
               reserved_cost_cents, actual_cost_cents,
               COALESCE(reserved_cost_microcents, reserved_cost_cents * 1000000) as reserved_cost_microcents,
               COALESCE(actual_cost_microcents, actual_cost_cents * 1000000) as actual_cost_microcents
        FROM public.llm_usage_budget_counters
        WHERE credential_id = $1 AND period_type = $2 AND period_start = $3
        FOR UPDATE
        """,
        credential_id,
        period_type,
        period_start,
    )
    if row is not None:
        return row

    # Insert initial zeroed counter row
    await conn.execute(
        """
        INSERT INTO public.llm_usage_budget_counters (
            credential_id, period_type, period_start,
            reserved_requests, dispatched_requests,
            reserved_cost_cents, actual_cost_cents,
            reserved_cost_microcents, actual_cost_microcents
        )
        VALUES ($1, $2, $3, 0, 0, 0, 0, 0, 0)
        ON CONFLICT (credential_id, period_type, period_start) DO NOTHING
        """,
        credential_id,
        period_type,
        period_start,
    )

    # Re-fetch with row lock
    locked_row = await conn.fetchrow(
        """
        SELECT credential_id, period_type, period_start,
               reserved_requests, dispatched_requests,
               reserved_cost_cents, actual_cost_cents,
               COALESCE(reserved_cost_microcents, reserved_cost_cents * 1000000) as reserved_cost_microcents,
               COALESCE(actual_cost_microcents, actual_cost_cents * 1000000) as actual_cost_microcents
        FROM public.llm_usage_budget_counters
        WHERE credential_id = $1 AND period_type = $2 AND period_start = $3
        FOR UPDATE
        """,
        credential_id,
        period_type,
        period_start,
    )
    if locked_row is None:
        raise AppError(code="DATABASE_ERROR", message="Failed to lock budget counter row")
    return locked_row


async def reserve_budget_transactional(
    user_id: UUID,
    credential_id: UUID,
    provider_id: str,
    model_id: str,
    idempotency_key: str,
    request_id: str,
    estimated_input_tokens: int,
    estimated_output_tokens: int,
    estimated_cost_cents: int,
    pricing_snapshot: dict[str, Any],
    lease_token: str,
    lease_expires_at: datetime,
    daily_request_limit: int,
    monthly_request_limit: int,
    daily_spending_limit_cents: int | None,
    monthly_spending_limit_cents: int | None,
    account_scope_id: str | None = None,
    estimated_cost_microcents: int | None = None,
    effective_daily_request_limit: int | None = None,
) -> AdmissionReservation:
    """Atomically verify and lock daily/monthly limits and persist a durable reservation."""
    pool = get_pool()
    day_start, month_start = get_period_boundaries()
    now_utc = datetime.now(UTC)
    cost_microcents = (
        estimated_cost_microcents
        if estimated_cost_microcents is not None
        else (estimated_cost_cents * 1_000_000)
    )
    daily_req_cap = (
        min(daily_request_limit, effective_daily_request_limit)
        if effective_daily_request_limit is not None
        else daily_request_limit
    )

    async with pool.acquire() as conn:
        async with conn.transaction():
            # 1. Check for existing reservation by idempotency key
            existing = await conn.fetchrow(
                """
                SELECT id, user_id, credential_id, provider_id, model_id,
                       status, estimated_input_tokens, estimated_output_tokens,
                       estimated_cost_cents,
                       COALESCE(estimated_cost_microcents, estimated_cost_cents * 1000000) as estimated_cost_microcents,
                       currency, lease_token, lease_expires_at, created_at
                FROM public.llm_usage_reservations
                WHERE user_id = $1 AND idempotency_key = $2
                """,
                user_id,
                idempotency_key,
            )
            if existing:
                return AdmissionReservation(
                    reservation_id=existing["id"],
                    lease_token=existing["lease_token"] or lease_token,
                    lease_expires_at=existing["lease_expires_at"] or lease_expires_at,
                    user_id=existing["user_id"],
                    credential_id=existing["credential_id"],
                    provider_id=existing["provider_id"],
                    model_id=existing["model_id"],
                    status=existing["status"],
                    estimated_input_tokens=existing["estimated_input_tokens"],
                    estimated_output_tokens=existing["estimated_output_tokens"],
                    estimated_cost_cents=existing["estimated_cost_cents"],
                    estimated_cost_microcents=existing["estimated_cost_microcents"],
                    currency=existing["currency"],
                    created_at=existing["created_at"],
                )

            # 2. Lock and check daily counter
            day_counter = await _get_or_create_counter_row(conn, credential_id, "daily", day_start)
            total_daily_reqs = day_counter["dispatched_requests"] + day_counter["reserved_requests"]
            if total_daily_reqs + 1 > daily_req_cap:
                raise BudgetExhaustedError(
                    reason=f"Daily request limit exceeded ({total_daily_reqs}/{daily_req_cap})",
                    limit_type="requests",
                    period="daily",
                    current_val=total_daily_reqs,
                    limit_val=daily_req_cap,
                )

            if daily_spending_limit_cents is not None:
                spending_limit_microcents = daily_spending_limit_cents * 1_000_000
                total_daily_cost_microcents = (
                    day_counter["actual_cost_microcents"] + day_counter["reserved_cost_microcents"]
                )
                if total_daily_cost_microcents + cost_microcents > spending_limit_microcents:
                    raise BudgetExhaustedError(
                        reason=f"Daily spending cap exceeded (${(total_daily_cost_microcents + cost_microcents) / 100_000_000:.4f} > ${daily_spending_limit_cents / 100:.2f})",
                        limit_type="spending_cents",
                        period="daily",
                        current_val=total_daily_cost_microcents // 1_000_000,
                        limit_val=daily_spending_limit_cents,
                    )

            # 3. Lock and check monthly counter
            month_counter = await _get_or_create_counter_row(
                conn, credential_id, "monthly", month_start
            )
            total_monthly_reqs = (
                month_counter["dispatched_requests"] + month_counter["reserved_requests"]
            )
            if total_monthly_reqs + 1 > monthly_request_limit:
                raise BudgetExhaustedError(
                    reason=f"Monthly request limit exceeded ({total_monthly_reqs}/{monthly_request_limit})",
                    limit_type="requests",
                    period="monthly",
                    current_val=total_monthly_reqs,
                    limit_val=monthly_request_limit,
                )

            if monthly_spending_limit_cents is not None:
                monthly_spending_limit_microcents = monthly_spending_limit_cents * 1_000_000
                total_monthly_cost_microcents = (
                    month_counter["actual_cost_microcents"]
                    + month_counter["reserved_cost_microcents"]
                )
                if (
                    total_monthly_cost_microcents + cost_microcents
                    > monthly_spending_limit_microcents
                ):
                    raise BudgetExhaustedError(
                        reason=f"Monthly spending cap exceeded (${(total_monthly_cost_microcents + cost_microcents) / 100_000_000:.4f} > ${monthly_spending_limit_cents / 100:.2f})",
                        limit_type="spending_cents",
                        period="monthly",
                        current_val=total_monthly_cost_microcents // 1_000_000,
                        limit_val=monthly_spending_limit_cents,
                    )

            # 4. Increment reserved counters
            await conn.execute(
                """
                UPDATE public.llm_usage_budget_counters
                SET reserved_requests = reserved_requests + 1,
                    reserved_cost_cents = reserved_cost_cents + $1,
                    reserved_cost_microcents = reserved_cost_microcents + $2,
                    updated_at = now()
                WHERE credential_id = $3 AND period_type = 'daily' AND period_start = $4
                """,
                estimated_cost_cents,
                cost_microcents,
                credential_id,
                day_start,
            )
            await conn.execute(
                """
                UPDATE public.llm_usage_budget_counters
                SET reserved_requests = reserved_requests + 1,
                    reserved_cost_cents = reserved_cost_cents + $1,
                    reserved_cost_microcents = reserved_cost_microcents + $2,
                    updated_at = now()
                WHERE credential_id = $3 AND period_type = 'monthly' AND period_start = $4
                """,
                estimated_cost_cents,
                cost_microcents,
                credential_id,
                month_start,
            )

            # 5. Insert durable reservation row
            res_id = uuid4()
            await conn.execute(
                """
                INSERT INTO public.llm_usage_reservations (
                    id, user_id, credential_id, provider_id, model_id,
                    idempotency_key, request_id, account_scope_id, status,
                    estimated_input_tokens, estimated_output_tokens,
                    estimated_cost_cents, estimated_cost_microcents,
                    currency, pricing_snapshot,
                    lease_token, lease_expires_at, created_at, updated_at
                )
                VALUES (
                    $1, $2, $3, $4, $5,
                    $6, $7, $8, 'reserved',
                    $9, $10,
                    $11, $12, 'USD', $13,
                    $14, $15, $16, $16
                )
                """,
                res_id,
                user_id,
                credential_id,
                provider_id,
                model_id,
                idempotency_key,
                request_id,
                account_scope_id,
                estimated_input_tokens,
                estimated_output_tokens,
                estimated_cost_cents,
                cost_microcents,
                json.dumps(pricing_snapshot),
                lease_token,
                lease_expires_at,
                now_utc,
            )

            # 6. Record append-only usage event
            await conn.execute(
                """
                INSERT INTO public.llm_usage_events (
                    reservation_id, event_type, user_id, credential_id,
                    tokens_delta, cost_cents_delta, cost_microcents_delta, currency, details
                )
                VALUES (
                    $1, 'reservation_created', $2, $3,
                    $4, $5, $6, 'USD', $7
                )
                """,
                res_id,
                user_id,
                credential_id,
                estimated_input_tokens + estimated_output_tokens,
                estimated_cost_cents,
                cost_microcents,
                json.dumps(
                    {
                        "request_id": request_id,
                        "provider_id": provider_id,
                        "model_id": model_id,
                    }
                ),
            )

            return AdmissionReservation(
                reservation_id=res_id,
                lease_token=lease_token,
                lease_expires_at=lease_expires_at,
                user_id=user_id,
                credential_id=credential_id,
                provider_id=provider_id,
                model_id=model_id,
                status="reserved",
                estimated_input_tokens=estimated_input_tokens,
                estimated_output_tokens=estimated_output_tokens,
                estimated_cost_cents=estimated_cost_cents,
                estimated_cost_microcents=cost_microcents,
                currency="USD",
                created_at=now_utc,
            )


async def commit_dispatch_transactional(
    reservation_id: UUID,
) -> None:
    """Transition reservation to dispatched and shift reserved request to dispatched."""
    pool = get_pool()
    now_utc = datetime.now(UTC)

    async with pool.acquire() as conn:
        async with conn.transaction():
            res = await conn.fetchrow(
                """
                SELECT id, user_id, credential_id, status, created_at
                FROM public.llm_usage_reservations
                WHERE id = $1
                FOR UPDATE
                """,
                reservation_id,
            )
            if not res:
                raise NotFoundError(f"Reservation '{reservation_id}' not found")

            current_status = res["status"]
            if current_status == "dispatched":
                return  # Idempotent return

            if current_status != "reserved":
                raise InvalidStateTransitionError(current_status, "dispatched")

            credential_id = res["credential_id"]
            user_id = res["user_id"]
            day_start, month_start = get_period_boundaries(res["created_at"])

            # Decrement reserved_requests and increment dispatched_requests
            await conn.execute(
                """
                UPDATE public.llm_usage_budget_counters
                SET reserved_requests = GREATEST(0, reserved_requests - 1),
                    dispatched_requests = dispatched_requests + 1,
                    updated_at = now()
                WHERE credential_id = $1 AND period_type = 'daily' AND period_start = $2
                """,
                credential_id,
                day_start,
            )
            await conn.execute(
                """
                UPDATE public.llm_usage_budget_counters
                SET reserved_requests = GREATEST(0, reserved_requests - 1),
                    dispatched_requests = dispatched_requests + 1,
                    updated_at = now()
                WHERE credential_id = $1 AND period_type = 'monthly' AND period_start = $2
                """,
                credential_id,
                month_start,
            )

            # Update reservation status
            await conn.execute(
                """
                UPDATE public.llm_usage_reservations
                SET status = 'dispatched',
                    dispatched_at = $2,
                    updated_at = now()
                WHERE id = $1
                """,
                reservation_id,
                now_utc,
            )

            # Append dispatch_committed event
            await conn.execute(
                """
                INSERT INTO public.llm_usage_events (
                    reservation_id, event_type, user_id, credential_id,
                    tokens_delta, cost_cents_delta, currency, details
                )
                VALUES ($1, 'dispatch_committed', $2, $3, 0, 0, 'USD', '{}'::jsonb)
                """,
                reservation_id,
                user_id,
                credential_id,
            )


async def settle_usage_transactional(
    reservation_id: UUID,
    actual_input_tokens: int,
    actual_output_tokens: int,
    actual_cost_cents: int,
    error_category: str | None = None,
    actual_cost_microcents: int | None = None,
) -> SettlementResult:
    """Settle actual usage metrics, update aggregates, and append immutable ledger event."""
    pool = get_pool()
    now_utc = datetime.now(UTC)
    cost_microcents = (
        actual_cost_microcents
        if actual_cost_microcents is not None
        else (actual_cost_cents * 1_000_000)
    )
    cost_cents = (
        actual_cost_cents
        if actual_cost_cents is not None and actual_cost_microcents is None
        else microcents_to_ceiling_cents(cost_microcents)
    )

    async with pool.acquire() as conn:
        async with conn.transaction():
            res = await conn.fetchrow(
                """
                SELECT id, user_id, credential_id, status, estimated_cost_cents,
                       COALESCE(estimated_cost_microcents, estimated_cost_cents * 1000000) as estimated_cost_microcents,
                       actual_cost_cents, actual_cost_microcents, currency, created_at
                FROM public.llm_usage_reservations
                WHERE id = $1
                FOR UPDATE
                """,
                reservation_id,
            )
            if not res:
                raise NotFoundError(f"Reservation '{reservation_id}' not found")

            current_status = res["status"]
            if current_status == "settled":
                # Idempotent return
                return SettlementResult(
                    reservation_id=res["id"],
                    status="settled",
                    actual_input_tokens=actual_input_tokens,
                    actual_output_tokens=actual_output_tokens,
                    actual_cost_cents=res["actual_cost_cents"]
                    if res["actual_cost_cents"] is not None
                    else cost_cents,
                    actual_cost_microcents=res["actual_cost_microcents"]
                    if res["actual_cost_microcents"] is not None
                    else cost_microcents,
                    currency=res["currency"],
                    settled_at=now_utc,
                )

            if current_status not in ("dispatched", "outcome_unknown", "reconciliation_required"):
                raise InvalidStateTransitionError(current_status, "settled")

            credential_id = res["credential_id"]
            user_id = res["user_id"]
            estimated_cost_cents = res["estimated_cost_cents"]
            estimated_cost_microcents = res["estimated_cost_microcents"]
            day_start, month_start = get_period_boundaries(res["created_at"])

            # Reconcile budget counters: subtract estimated cost reservation, add confirmed actual cost
            await conn.execute(
                """
                UPDATE public.llm_usage_budget_counters
                SET reserved_cost_cents = GREATEST(0, reserved_cost_cents - $1),
                    reserved_cost_microcents = GREATEST(0, reserved_cost_microcents - $2),
                    actual_cost_microcents = actual_cost_microcents + $3,
                    actual_cost_cents = CEIL((actual_cost_microcents + $3)::numeric / 1000000)::bigint,
                    updated_at = now()
                WHERE credential_id = $4 AND period_type = 'daily' AND period_start = $5
                """,
                estimated_cost_cents,
                estimated_cost_microcents,
                cost_microcents,
                credential_id,
                day_start,
            )
            await conn.execute(
                """
                UPDATE public.llm_usage_budget_counters
                SET reserved_cost_cents = GREATEST(0, reserved_cost_cents - $1),
                    reserved_cost_microcents = GREATEST(0, reserved_cost_microcents - $2),
                    actual_cost_microcents = actual_cost_microcents + $3,
                    actual_cost_cents = CEIL((actual_cost_microcents + $3)::numeric / 1000000)::bigint,
                    updated_at = now()
                WHERE credential_id = $4 AND period_type = 'monthly' AND period_start = $5
                """,
                estimated_cost_cents,
                estimated_cost_microcents,
                cost_microcents,
                credential_id,
                month_start,
            )

            # Update reservation row
            await conn.execute(
                """
                UPDATE public.llm_usage_reservations
                SET status = 'settled',
                    actual_input_tokens = $2,
                    actual_output_tokens = $3,
                    actual_cost_cents = $4,
                    actual_cost_microcents = $5,
                    error_category = $6,
                    settled_at = $7,
                    updated_at = now()
                WHERE id = $1
                """,
                reservation_id,
                actual_input_tokens,
                actual_output_tokens,
                cost_cents,
                cost_microcents,
                error_category,
                now_utc,
            )

            # Append usage_settled event
            total_actual_tokens = actual_input_tokens + actual_output_tokens
            cost_cents_delta = cost_cents - estimated_cost_cents
            cost_microcents_delta = cost_microcents - estimated_cost_microcents
            await conn.execute(
                """
                INSERT INTO public.llm_usage_events (
                    reservation_id, event_type, user_id, credential_id,
                    tokens_delta, cost_cents_delta, cost_microcents_delta, currency, details
                )
                VALUES ($1, 'usage_settled', $2, $3, $4, $5, $6, 'USD', $7)
                """,
                reservation_id,
                user_id,
                credential_id,
                total_actual_tokens,
                cost_cents_delta,
                cost_microcents_delta,
                json.dumps(
                    {
                        "actual_input": actual_input_tokens,
                        "actual_output": actual_output_tokens,
                        "actual_cost_cents": cost_cents,
                        "actual_cost_microcents": cost_microcents,
                        "error_category": error_category,
                    }
                ),
            )

            return SettlementResult(
                reservation_id=reservation_id,
                status="settled",
                actual_input_tokens=actual_input_tokens,
                actual_output_tokens=actual_output_tokens,
                actual_cost_cents=cost_cents,
                actual_cost_microcents=cost_microcents,
                currency=res["currency"],
                settled_at=now_utc,
            )


async def release_reservation_transactional(
    reservation_id: UUID,
    reason: str = "pre_dispatch_cancellation",
) -> ReleaseResult:
    """Release uncommitted reservation, refunding reserved request & spending budget."""
    pool = get_pool()
    now_utc = datetime.now(UTC)

    async with pool.acquire() as conn:
        async with conn.transaction():
            res = await conn.fetchrow(
                """
                SELECT id, user_id, credential_id, status, estimated_cost_cents,
                       COALESCE(estimated_cost_microcents, estimated_cost_cents * 1000000) as estimated_cost_microcents,
                       created_at
                FROM public.llm_usage_reservations
                WHERE id = $1
                FOR UPDATE
                """,
                reservation_id,
            )
            if not res:
                raise NotFoundError(f"Reservation '{reservation_id}' not found")

            current_status = res["status"]
            if current_status in ("released", "expired"):
                return ReleaseResult(
                    reservation_id=res["id"],
                    status=current_status,
                    released_at=now_utc,
                    reason=reason,
                )

            if current_status != "reserved":
                raise InvalidStateTransitionError(current_status, "released")

            credential_id = res["credential_id"]
            user_id = res["user_id"]
            estimated_cost_cents = res["estimated_cost_cents"]
            estimated_cost_microcents = res["estimated_cost_microcents"]
            day_start, month_start = get_period_boundaries(res["created_at"])

            # Decrement reserved counters
            await conn.execute(
                """
                UPDATE public.llm_usage_budget_counters
                SET reserved_requests = GREATEST(0, reserved_requests - 1),
                    reserved_cost_cents = GREATEST(0, reserved_cost_cents - $1),
                    reserved_cost_microcents = GREATEST(0, reserved_cost_microcents - $2),
                    updated_at = now()
                WHERE credential_id = $3 AND period_type = 'daily' AND period_start = $4
                """,
                estimated_cost_cents,
                estimated_cost_microcents,
                credential_id,
                day_start,
            )
            await conn.execute(
                """
                UPDATE public.llm_usage_budget_counters
                SET reserved_requests = GREATEST(0, reserved_requests - 1),
                    reserved_cost_cents = GREATEST(0, reserved_cost_cents - $1),
                    reserved_cost_microcents = GREATEST(0, reserved_cost_microcents - $2),
                    updated_at = now()
                WHERE credential_id = $3 AND period_type = 'monthly' AND period_start = $4
                """,
                estimated_cost_cents,
                estimated_cost_microcents,
                credential_id,
                month_start,
            )

            # Update status to released
            await conn.execute(
                """
                UPDATE public.llm_usage_reservations
                SET status = 'released',
                    updated_at = now()
                WHERE id = $1
                """,
                reservation_id,
            )

            # Append reservation_released event
            await conn.execute(
                """
                INSERT INTO public.llm_usage_events (
                    reservation_id, event_type, user_id, credential_id,
                    tokens_delta, cost_cents_delta, cost_microcents_delta, currency, details
                )
                VALUES ($1, 'reservation_released', $2, $3, 0, $4, $5, 'USD', $6)
                """,
                reservation_id,
                user_id,
                credential_id,
                -estimated_cost_cents,
                -estimated_cost_microcents,
                json.dumps({"reason": reason}),
            )

            return ReleaseResult(
                reservation_id=reservation_id,
                status="released",
                released_at=now_utc,
                reason=reason,
            )


async def expire_reservation_transactional(
    reservation_id: UUID,
    reason: str = "lease_expired",
) -> ReleaseResult:
    """Expire an uncommitted reservation whose lease timed out, refunding reserved counters."""
    pool = get_pool()
    now_utc = datetime.now(UTC)

    async with pool.acquire() as conn:
        async with conn.transaction():
            res = await conn.fetchrow(
                """
                SELECT id, user_id, credential_id, status, estimated_cost_cents,
                       COALESCE(estimated_cost_microcents, estimated_cost_cents * 1000000) as estimated_cost_microcents,
                       created_at
                FROM public.llm_usage_reservations
                WHERE id = $1
                FOR UPDATE
                """,
                reservation_id,
            )
            if not res:
                raise NotFoundError(f"Reservation '{reservation_id}' not found")

            current_status = res["status"]
            if current_status == "expired":
                return ReleaseResult(
                    reservation_id=res["id"],
                    status="expired",
                    released_at=now_utc,
                    reason=reason,
                )

            if current_status != "reserved":
                raise InvalidStateTransitionError(current_status, "expired")

            credential_id = res["credential_id"]
            user_id = res["user_id"]
            estimated_cost_cents = res["estimated_cost_cents"]
            estimated_cost_microcents = res["estimated_cost_microcents"]
            day_start, month_start = get_period_boundaries(res["created_at"])

            # Decrement reserved counters
            await conn.execute(
                """
                UPDATE public.llm_usage_budget_counters
                SET reserved_requests = GREATEST(0, reserved_requests - 1),
                    reserved_cost_cents = GREATEST(0, reserved_cost_cents - $1),
                    reserved_cost_microcents = GREATEST(0, reserved_cost_microcents - $2),
                    updated_at = now()
                WHERE credential_id = $3 AND period_type = 'daily' AND period_start = $4
                """,
                estimated_cost_cents,
                estimated_cost_microcents,
                credential_id,
                day_start,
            )
            await conn.execute(
                """
                UPDATE public.llm_usage_budget_counters
                SET reserved_requests = GREATEST(0, reserved_requests - 1),
                    reserved_cost_cents = GREATEST(0, reserved_cost_cents - $1),
                    reserved_cost_microcents = GREATEST(0, reserved_cost_microcents - $2),
                    updated_at = now()
                WHERE credential_id = $3 AND period_type = 'monthly' AND period_start = $4
                """,
                estimated_cost_cents,
                estimated_cost_microcents,
                credential_id,
                month_start,
            )

            # Update status to expired
            await conn.execute(
                """
                UPDATE public.llm_usage_reservations
                SET status = 'expired',
                    updated_at = now()
                WHERE id = $1
                """,
                reservation_id,
            )

            # Append reservation_expired event
            await conn.execute(
                """
                INSERT INTO public.llm_usage_events (
                    reservation_id, event_type, user_id, credential_id,
                    tokens_delta, cost_cents_delta, cost_microcents_delta, currency, details
                )
                VALUES ($1, 'reservation_expired', $2, $3, 0, $4, $5, 'USD', $6)
                """,
                reservation_id,
                user_id,
                credential_id,
                -estimated_cost_cents,
                -estimated_cost_microcents,
                json.dumps({"reason": reason}),
            )

            return ReleaseResult(
                reservation_id=reservation_id,
                status="expired",
                released_at=now_utc,
                reason=reason,
            )


async def sweep_expired_reservations_batch(
    batch_size: int = 50,
    reason: str = "lease_expired_sweeper",
) -> list[dict[str, Any]]:
    """Atomically find and expire reservations whose lease has passed in PostgreSQL.

    Uses SELECT ... FOR UPDATE SKIP LOCKED to guarantee that multiple concurrent
    sweepers will never lock or process the same reservation.
    Only expires reservations with status = 'reserved' (never touches dispatched,
    settled, outcome_unknown, or released).
    Returns metadata list of swept reservations for best-effort Redis cleanup.
    """
    pool = get_pool()
    now_utc = datetime.now(UTC)
    swept_items: list[dict[str, Any]] = []

    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT id, user_id, credential_id, provider_id, model_id, request_id, lease_token,
                       estimated_cost_cents,
                       COALESCE(estimated_cost_microcents, estimated_cost_cents * 1000000) as estimated_cost_microcents,
                       created_at
                FROM public.llm_usage_reservations
                WHERE status = 'reserved'
                  AND lease_expires_at < $1
                ORDER BY lease_expires_at ASC
                LIMIT $2
                FOR UPDATE SKIP LOCKED
                """,
                now_utc,
                batch_size,
            )

            if not rows:
                return []

            for row in rows:
                res_id = row["id"]
                user_id = row["user_id"]
                credential_id = row["credential_id"]
                estimated_cost_cents = row["estimated_cost_cents"]
                estimated_cost_microcents = row["estimated_cost_microcents"]
                day_start, month_start = get_period_boundaries(row["created_at"])

                # Decrement reserved counters
                await conn.execute(
                    """
                    UPDATE public.llm_usage_budget_counters
                    SET reserved_requests = GREATEST(0, reserved_requests - 1),
                        reserved_cost_cents = GREATEST(0, reserved_cost_cents - $1),
                        reserved_cost_microcents = GREATEST(0, reserved_cost_microcents - $2),
                        updated_at = now()
                    WHERE credential_id = $3 AND period_type = 'daily' AND period_start = $4
                    """,
                    estimated_cost_cents,
                    estimated_cost_microcents,
                    credential_id,
                    day_start,
                )
                await conn.execute(
                    """
                    UPDATE public.llm_usage_budget_counters
                    SET reserved_requests = GREATEST(0, reserved_requests - 1),
                        reserved_cost_cents = GREATEST(0, reserved_cost_cents - $1),
                        reserved_cost_microcents = GREATEST(0, reserved_cost_microcents - $2),
                        updated_at = now()
                    WHERE credential_id = $3 AND period_type = 'monthly' AND period_start = $4
                    """,
                    estimated_cost_cents,
                    estimated_cost_microcents,
                    credential_id,
                    month_start,
                )

                # Update reservation status to expired
                await conn.execute(
                    """
                    UPDATE public.llm_usage_reservations
                    SET status = 'expired',
                        updated_at = now()
                    WHERE id = $1
                    """,
                    res_id,
                )

                # Append immutable reservation_expired event
                await conn.execute(
                    """
                    INSERT INTO public.llm_usage_events (
                        reservation_id, event_type, user_id, credential_id,
                        tokens_delta, cost_cents_delta, cost_microcents_delta, currency, details
                    )
                    VALUES ($1, 'reservation_expired', $2, $3, 0, $4, $5, 'USD', $6)
                    """,
                    res_id,
                    user_id,
                    credential_id,
                    -estimated_cost_cents,
                    -estimated_cost_microcents,
                    json.dumps({"reason": reason, "swept_at": now_utc.isoformat()}),
                )

                swept_items.append(dict(row))

    return swept_items


async def reconcile_usage_transactional(
    reservation_id: UUID,
    actual_input_tokens: int,
    actual_output_tokens: int,
    actual_cost_microcents: int,
    reconciliation_notes: str | None = None,
) -> SettlementResult:
    """Reconcile an outcome_unknown or reconciliation_required reservation with confirmed actual usage."""
    pool = get_pool()
    now_utc = datetime.now(UTC)

    async with pool.acquire() as conn:
        async with conn.transaction():
            res = await conn.fetchrow(
                """
                SELECT id, user_id, credential_id, status, estimated_cost_cents,
                       COALESCE(estimated_cost_microcents, estimated_cost_cents * 1000000) as estimated_cost_microcents,
                       actual_cost_cents, actual_cost_microcents, currency, created_at
                FROM public.llm_usage_reservations
                WHERE id = $1
                FOR UPDATE
                """,
                reservation_id,
            )
            if not res:
                raise NotFoundError(f"Reservation '{reservation_id}' not found")

            current_status = res["status"]
            if current_status == "settled":
                return SettlementResult(
                    reservation_id=res["id"],
                    status="settled",
                    actual_input_tokens=actual_input_tokens,
                    actual_output_tokens=actual_output_tokens,
                    actual_cost_cents=res["actual_cost_cents"]
                    if res["actual_cost_cents"] is not None
                    else (actual_cost_microcents // 1_000_000),
                    actual_cost_microcents=res["actual_cost_microcents"]
                    if res["actual_cost_microcents"] is not None
                    else actual_cost_microcents,
                    currency=res["currency"],
                    settled_at=now_utc,
                )

            if current_status not in ("outcome_unknown", "reconciliation_required"):
                raise InvalidStateTransitionError(current_status, "settled")

            credential_id = res["credential_id"]
            user_id = res["user_id"]
            estimated_cost_cents = res["estimated_cost_cents"]
            estimated_cost_microcents = res["estimated_cost_microcents"]
            actual_cost_cents = microcents_to_ceiling_cents(actual_cost_microcents)
            day_start, month_start = get_period_boundaries(res["created_at"])

            # In outcome_unknown, conservative estimated cost was retained.
            # Deduct conservative reservation, apply confirmed actual cost.
            await conn.execute(
                """
                UPDATE public.llm_usage_budget_counters
                SET reserved_cost_cents = GREATEST(0, reserved_cost_cents - $1),
                    reserved_cost_microcents = GREATEST(0, reserved_cost_microcents - $2),
                    actual_cost_microcents = actual_cost_microcents + $3,
                    actual_cost_cents = CEIL((actual_cost_microcents + $3)::numeric / 1000000)::bigint,
                    updated_at = now()
                WHERE credential_id = $4 AND period_type = 'daily' AND period_start = $5
                """,
                estimated_cost_cents,
                estimated_cost_microcents,
                actual_cost_microcents,
                credential_id,
                day_start,
            )
            await conn.execute(
                """
                UPDATE public.llm_usage_budget_counters
                SET reserved_cost_cents = GREATEST(0, reserved_cost_cents - $1),
                    reserved_cost_microcents = GREATEST(0, reserved_cost_microcents - $2),
                    actual_cost_microcents = actual_cost_microcents + $3,
                    actual_cost_cents = CEIL((actual_cost_microcents + $3)::numeric / 1000000)::bigint,
                    updated_at = now()
                WHERE credential_id = $4 AND period_type = 'monthly' AND period_start = $5
                """,
                estimated_cost_cents,
                estimated_cost_microcents,
                actual_cost_microcents,
                credential_id,
                month_start,
            )

            # Update reservation row to settled
            await conn.execute(
                """
                UPDATE public.llm_usage_reservations
                SET status = 'settled',
                    actual_input_tokens = $2,
                    actual_output_tokens = $3,
                    actual_cost_cents = $4,
                    actual_cost_microcents = $5,
                    settled_at = $6,
                    updated_at = now()
                WHERE id = $1
                """,
                reservation_id,
                actual_input_tokens,
                actual_output_tokens,
                actual_cost_cents,
                actual_cost_microcents,
                now_utc,
            )

            # Append usage_reconciled event
            total_actual_tokens = actual_input_tokens + actual_output_tokens
            cost_cents_delta = actual_cost_cents - estimated_cost_cents
            cost_microcents_delta = actual_cost_microcents - estimated_cost_microcents
            await conn.execute(
                """
                INSERT INTO public.llm_usage_events (
                    reservation_id, event_type, user_id, credential_id,
                    tokens_delta, cost_cents_delta, cost_microcents_delta, currency, details
                )
                VALUES ($1, 'usage_reconciled', $2, $3, $4, $5, $6, 'USD', $7)
                """,
                reservation_id,
                user_id,
                credential_id,
                total_actual_tokens,
                cost_cents_delta,
                cost_microcents_delta,
                json.dumps({"reconciliation_notes": reconciliation_notes}),
            )

            return SettlementResult(
                reservation_id=reservation_id,
                status="settled",
                actual_input_tokens=actual_input_tokens,
                actual_output_tokens=actual_output_tokens,
                actual_cost_cents=actual_cost_cents,
                actual_cost_microcents=actual_cost_microcents,
                currency=res["currency"],
                settled_at=now_utc,
            )


async def mark_outcome_unknown_transactional(
    reservation_id: UUID,
    reason: str = "upstream_network_timeout",
) -> None:
    """Transition reservation to outcome_unknown, retaining reserved spending conservatively."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            res = await conn.fetchrow(
                """
                SELECT id, user_id, credential_id, status
                FROM public.llm_usage_reservations
                WHERE id = $1
                FOR UPDATE
                """,
                reservation_id,
            )
            if not res:
                raise NotFoundError(f"Reservation '{reservation_id}' not found")

            current_status = res["status"]
            if current_status == "outcome_unknown":
                return

            if current_status != "dispatched":
                raise InvalidStateTransitionError(current_status, "outcome_unknown")

            await conn.execute(
                """
                UPDATE public.llm_usage_reservations
                SET status = 'outcome_unknown',
                    error_category = $2,
                    updated_at = now()
                WHERE id = $1
                """,
                reservation_id,
                reason,
            )

            await conn.execute(
                """
                INSERT INTO public.llm_usage_events (
                    reservation_id, event_type, user_id, credential_id,
                    tokens_delta, cost_cents_delta, currency, details
                )
                VALUES ($1, 'outcome_unknown', $2, $3, 0, 0, 'USD', $4)
                """,
                reservation_id,
                res["user_id"],
                res["credential_id"],
                json.dumps({"reason": reason}),
            )


async def get_user_usage_summary(
    user_id: UUID,
) -> UsageSummaryResponse:
    """Aggregate total usage, budget consumption, and recent reservations for the user."""
    pool = get_pool()
    day_start, month_start = get_period_boundaries()

    async with pool.acquire() as conn:
        # Aggregated totals across all user's reservations
        totals = await conn.fetchrow(
            """
            SELECT
                COALESCE(COUNT(*) FILTER (WHERE status IN ('dispatched', 'settled', 'outcome_unknown')), 0) as dispatched_count,
                COALESCE(COUNT(*) FILTER (WHERE status = 'settled'), 0) as settled_count,
                COALESCE(SUM(actual_input_tokens) FILTER (WHERE status = 'settled'), 0) as total_input,
                COALESCE(SUM(actual_output_tokens) FILTER (WHERE status = 'settled'), 0) as total_output,
                COALESCE(SUM(actual_cost_cents) FILTER (WHERE status = 'settled'), 0) as total_cost,
                COALESCE(SUM(COALESCE(actual_cost_microcents, actual_cost_cents * 1000000)) FILTER (WHERE status = 'settled'), 0) as total_cost_microcents,
                COALESCE(SUM(estimated_cost_cents) FILTER (WHERE status IN ('reserved', 'dispatched', 'outcome_unknown')), 0) as total_reserved_cost,
                COALESCE(COUNT(*) FILTER (WHERE status = 'outcome_unknown'), 0) as unknown_count
            FROM public.llm_usage_reservations
            WHERE user_id = $1
            """,
            user_id,
        )

        # Daily usage across user's owned credentials
        daily_agg = await conn.fetchrow(
            """
            SELECT
                COALESCE(SUM(dispatched_requests), 0) as daily_reqs,
                COALESCE(SUM(actual_cost_cents), 0) as daily_spend,
                COALESCE(SUM(COALESCE(actual_cost_microcents, actual_cost_cents * 1000000)), 0) as daily_spend_microcents
            FROM public.llm_usage_budget_counters b
            JOIN public.api_key_metadata m ON m.id = b.credential_id
            WHERE m.owner_id = $1 AND b.period_type = 'daily' AND b.period_start = $2
            """,
            user_id,
            day_start,
        )

        # Monthly usage across user's owned credentials
        monthly_agg = await conn.fetchrow(
            """
            SELECT
                COALESCE(SUM(dispatched_requests), 0) as monthly_reqs,
                COALESCE(SUM(actual_cost_cents), 0) as monthly_spend,
                COALESCE(SUM(COALESCE(actual_cost_microcents, actual_cost_cents * 1000000)), 0) as monthly_spend_microcents
            FROM public.llm_usage_budget_counters b
            JOIN public.api_key_metadata m ON m.id = b.credential_id
            WHERE m.owner_id = $1 AND b.period_type = 'monthly' AND b.period_start = $2
            """,
            user_id,
            month_start,
        )

        # Credential configured limits (sum of daily/monthly limits across active owned credentials)
        limits_row = await conn.fetchrow(
            """
            SELECT
                COALESCE(SUM(daily_request_limit), 0) as daily_limit,
                COALESCE(SUM(monthly_request_limit), 0) as monthly_limit,
                COALESCE(SUM(daily_spending_limit_cents), NULL) as daily_spending_cap,
                COALESCE(SUM(monthly_spending_limit_cents), NULL) as monthly_spending_cap
            FROM public.api_key_metadata
            WHERE owner_id = $1 AND status != 'revoked'
            """,
            user_id,
        )

        # Recent 20 reservations
        rows = await conn.fetch(
            """
            SELECT id, request_id, provider_id, model_id, status,
                   estimated_input_tokens, estimated_output_tokens, estimated_cost_cents,
                   COALESCE(estimated_cost_microcents, estimated_cost_cents * 1000000) as estimated_cost_microcents,
                   actual_input_tokens, actual_output_tokens, actual_cost_cents,
                   actual_cost_microcents,
                   currency, created_at, dispatched_at, settled_at
            FROM public.llm_usage_reservations
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT 20
            """,
            user_id,
        )

        recent = [
            ReservationView(
                id=r["id"],
                request_id=r["request_id"],
                provider_id=r["provider_id"],
                model_id=r["model_id"],
                status=r["status"],
                estimated_input_tokens=r["estimated_input_tokens"],
                estimated_output_tokens=r["estimated_output_tokens"],
                estimated_cost_cents=r["estimated_cost_cents"],
                estimated_cost_microcents=r["estimated_cost_microcents"],
                actual_input_tokens=r["actual_input_tokens"],
                actual_output_tokens=r["actual_output_tokens"],
                actual_cost_cents=r["actual_cost_cents"],
                actual_cost_microcents=r["actual_cost_microcents"],
                currency=r["currency"],
                created_at=r["created_at"],
                dispatched_at=r["dispatched_at"],
                settled_at=r["settled_at"],
            )
            for r in rows
        ]

        total_cost_microcents = int(totals["total_cost_microcents"])
        daily_spend_microcents = int(daily_agg["daily_spend_microcents"]) if daily_agg else 0
        monthly_spend_microcents = (
            int(monthly_agg["monthly_spend_microcents"]) if monthly_agg else 0
        )

        # At display boundary, round whole cents conservatively using ceiling so non-zero expenditures are visible
        total_actual_cost_cents = microcents_to_ceiling_cents(total_cost_microcents)
        daily_spending_cents = microcents_to_ceiling_cents(daily_spend_microcents)
        monthly_spending_cents = microcents_to_ceiling_cents(monthly_spend_microcents)

        return UsageSummaryResponse(
            total_dispatched_requests=int(totals["dispatched_count"]),
            total_settled_requests=int(totals["settled_count"]),
            total_input_tokens=int(totals["total_input"]),
            total_output_tokens=int(totals["total_output"]),
            total_actual_cost_cents=total_actual_cost_cents,
            total_actual_cost_microcents=total_cost_microcents,
            total_reserved_cost_cents=int(totals["total_reserved_cost"]),
            daily_request_usage=int(daily_agg["daily_reqs"]) if daily_agg else 0,
            daily_request_limit=int(limits_row["daily_limit"]) if limits_row else 500,
            monthly_request_usage=int(monthly_agg["monthly_reqs"]) if monthly_agg else 0,
            monthly_request_limit=int(limits_row["monthly_limit"]) if limits_row else 15000,
            daily_spending_cents=daily_spending_cents,
            daily_spending_microcents=daily_spend_microcents,
            daily_spending_budget_cents=limits_row["daily_spending_cap"] if limits_row else None,
            monthly_spending_cents=monthly_spending_cents,
            monthly_spending_microcents=monthly_spend_microcents,
            monthly_spending_budget_cents=limits_row["monthly_spending_cap"]
            if limits_row
            else None,
            unknown_outcome_count=int(totals["unknown_count"]),
            recent_reservations=recent,
        )
