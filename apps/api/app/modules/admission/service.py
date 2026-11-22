"""Admission control orchestrator for rate limits, concurrency leases, and usage ledger."""

import logging
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
    compute_cost_microcents,
    expire_reservation_transactional,
    get_user_usage_summary,
    mark_outcome_unknown_transactional,
    microcents_to_ceiling_cents,
    reconcile_usage_transactional,
    release_reservation_transactional,
    reserve_budget_transactional,
    settle_usage_transactional,
    sweep_expired_reservations_batch,
)
from app.modules.admission.rate_limiter import (
    RateLimitScope,
    reconcile_rate_slots,
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

logger = logging.getLogger("ragbait.admission.service")


def _compute_ceiling_cost(
    input_tokens: int,
    output_tokens: int,
    input_cost_per_million: int,
    output_cost_per_million: int,
) -> int:
    """Legacy helper returning integer cents; delegates to microcents calculation."""
    micro = compute_cost_microcents(
        input_tokens, output_tokens, input_cost_per_million, output_cost_per_million
    )
    return microcents_to_ceiling_cents(micro)


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
                   sharing_scope, shared_consent, account_scope_id, account_scope_verified,
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
    # 0. Opportunistic bounded sweeper pass: recover any expired reservations (e.g. from crashed workers)
    try:
        await recover_expired_reservations(batch_size=10)
    except Exception as exc:
        logger.warning(f"Opportunistic admission recovery encountered non-fatal error: {exc}")

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

    # Trusted account identity comes strictly from server-verified credential metadata.
    # Unverified account scopes are strictly ignored to prevent quota and cooldown poisoning.
    is_verified = bool(cred.get("account_scope_verified"))
    raw_scope_id = cred.get("account_scope_id")
    trusted_account_scope_id = raw_scope_id.strip() if (is_verified and raw_scope_id) else None

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

    # 3. Calculate exact estimated cost in microcents and conservative ceiling in cents
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

    estimated_cost_microcents = compute_cost_microcents(
        req.estimated_input_tokens,
        req.max_output_tokens,
        input_cost,
        output_cost,
    )
    estimated_cost_cents = microcents_to_ceiling_cents(estimated_cost_microcents)

    pricing_snapshot = {
        "model_id": req.model_id,
        "input_cost_per_million_cents": input_cost,
        "output_cost_per_million_cents": output_cost,
        "estimated_cost_microcents": estimated_cost_microcents,
        "estimated_cost_cents": estimated_cost_cents,
        "free_tier_eligible": model["free_tier_eligible"],
        "timestamp": datetime.now(UTC).isoformat(),
    }

    # 4. Check active cooldowns
    cooldown_scopes: list[tuple[str, str]] = [
        ("provider", req.provider_id),
        ("model", req.model_id),
        ("credential", str(req.credential_id)),
    ]
    if trusted_account_scope_id:
        cooldown_scopes.append(("account", f"{req.provider_id}:{trusted_account_scope_id}"))

    is_cooling, ttl_remaining, active_scope = await check_cooldown(cooldown_scopes)
    if is_cooling:
        raise AdmissionRejectionError(
            reason=f"Provider cooldown active on {active_scope}",
            category="provider_cooldown",
            retry_after=ttl_remaining,
            details={"cooldown_scope": active_scope},
        )

    # 5. Resolve applicable quotas
    applicable_quotas = await _get_applicable_quotas(
        req.provider_id, req.model_id, trusted_account_scope_id
    )

    # Platform baseline safeguards
    user_rpm = settings.LLM_USER_RPM
    cred_rpm = settings.LLM_CREDENTIAL_RPM
    burst = settings.LLM_RATE_BURST

    # Inspect provider / model published quotas across all limit types
    provider_rpm_quota: int | None = None
    account_rpm_quota: int | None = None
    model_rpm_quota: int | None = None

    provider_tpm_quota: int | None = None
    account_tpm_quota: int | None = None
    model_tpm_quota: int | None = None

    provider_rpd_quota: int | None = None
    provider_tpd_quota: int | None = None
    account_tpd_quota: int | None = None
    model_tpd_quota: int | None = None

    for q in applicable_quotas:
        l_type = q["limit_type"]
        val = q["limit_value"]
        q_scope_id = q.get("scope_id")
        q_model_id = q.get("model_id")

        if l_type == "rpm":
            if q_model_id == req.model_id:
                model_rpm_quota = min(model_rpm_quota or val, val)
            elif trusted_account_scope_id and q_scope_id == trusted_account_scope_id:
                account_rpm_quota = min(account_rpm_quota or val, val)
            else:
                provider_rpm_quota = min(provider_rpm_quota or val, val)

        elif l_type == "tpm":
            if q_model_id == req.model_id:
                model_tpm_quota = min(model_tpm_quota or val, val)
            elif trusted_account_scope_id and q_scope_id == trusted_account_scope_id:
                account_tpm_quota = min(account_tpm_quota or val, val)
            else:
                provider_tpm_quota = min(provider_tpm_quota or val, val)

        elif l_type == "rpd":
            provider_rpd_quota = min(provider_rpd_quota or val, val)

        elif l_type == "tpd":
            if q_model_id == req.model_id:
                model_tpd_quota = min(model_tpd_quota or val, val)
            elif trusted_account_scope_id and q_scope_id == trusted_account_scope_id:
                account_tpd_quota = min(account_tpd_quota or val, val)
            else:
                provider_tpd_quota = min(provider_tpd_quota or val, val)

    tpd_candidates = [
        q for q in [model_tpd_quota, account_tpd_quota, provider_tpd_quota] if q is not None
    ]
    effective_daily_token_limit = min(tpd_candidates) if tpd_candidates else None

    rate_scopes = [
        RateLimitScope(
            scope_name="user",
            key=f"ragbait:rate:user:{req.user_id}",
            limit=user_rpm,
            burst=burst,
            weight=1,
            unit="requests",
        ),
        RateLimitScope(
            scope_name="credential",
            key=f"ragbait:rate:credential:{req.credential_id}",
            limit=cred_rpm,
            burst=burst,
            weight=1,
            unit="requests",
        ),
    ]

    # Shared account scope across credentials with identical account identity
    if trusted_account_scope_id:
        acc_limit = account_rpm_quota or provider_rpm_quota or cred_rpm
        acc_burst = 0 if account_rpm_quota else burst
        rate_scopes.append(
            RateLimitScope(
                scope_name="account",
                key=f"ragbait:rate:account:{req.provider_id}:{trusted_account_scope_id}",
                limit=acc_limit,
                burst=acc_burst,
                weight=1,
                unit="requests",
            )
        )

    if provider_rpm_quota:
        rate_scopes.append(
            RateLimitScope(
                scope_name="provider",
                key=f"ragbait:rate:provider:{req.provider_id}",
                limit=provider_rpm_quota,
                burst=0,
                weight=1,
                unit="requests",
            )
        )
    if model_rpm_quota:
        rate_scopes.append(
            RateLimitScope(
                scope_name="model",
                key=f"ragbait:rate:model:{req.model_id}",
                limit=model_rpm_quota,
                burst=0,
                weight=1,
                unit="requests",
            )
        )

    # TPM token-based sliding-window enforcement
    estimated_tokens = req.estimated_input_tokens + req.max_output_tokens
    if estimated_tokens > 0:
        if model_tpm_quota:
            rate_scopes.append(
                RateLimitScope(
                    scope_name="model_tpm",
                    key=f"ragbait:rate:tpm:model:{req.model_id}",
                    limit=model_tpm_quota,
                    burst=0,
                    weight=estimated_tokens,
                    unit="tokens",
                )
            )
        if account_tpm_quota and trusted_account_scope_id:
            rate_scopes.append(
                RateLimitScope(
                    scope_name="account_tpm",
                    key=f"ragbait:rate:tpm:account:{req.provider_id}:{trusted_account_scope_id}",
                    limit=account_tpm_quota,
                    burst=0,
                    weight=estimated_tokens,
                    unit="tokens",
                )
            )
        elif provider_tpm_quota:
            rate_scopes.append(
                RateLimitScope(
                    scope_name="provider_tpm",
                    key=f"ragbait:rate:tpm:provider:{req.provider_id}",
                    limit=provider_tpm_quota,
                    burst=0,
                    weight=estimated_tokens,
                    unit="tokens",
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
            account_scope_id=trusted_account_scope_id,
            estimated_cost_microcents=estimated_cost_microcents,
            effective_daily_request_limit=provider_rpd_quota,
            effective_daily_token_limit=effective_daily_token_limit,
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
    resolved_model_id: str | None = None,
    upstream_provider: str | None = None,
    upstream_generation_id: str | None = None,
    usage_source: str = "authoritative",
) -> SettlementResult:
    """Settle actual usage metrics, release concurrency lease, and finalize accounting."""
    pool = get_pool()
    async with pool.acquire() as conn:
        res = await conn.fetchrow(
            """
            SELECT id, user_id, credential_id, model_id, lease_token, status, request_id
            FROM public.llm_usage_reservations
            WHERE id = $1
            """,
            reservation_id,
        )
        if not res:
            raise NotFoundError(f"Reservation '{reservation_id}' not found")

        # Compute actual cost in exact microcents
        actual_cost_microcents = req.actual_cost_microcents
        if actual_cost_microcents is None:
            if req.actual_cost_cents is not None:
                actual_cost_microcents = req.actual_cost_cents * 1_000_000
            else:
                active_model_id = model_id or res["model_id"]
                try:
                    model = await _get_model_details(active_model_id)
                except NotFoundError:
                    model = await _get_model_details(res["model_id"])
                actual_cost_microcents = compute_cost_microcents(
                    req.actual_input_tokens,
                    req.actual_output_tokens,
                    model["input_cost_per_million_cents"],
                    model["output_cost_per_million_cents"],
                )

        actual_cost_cents = microcents_to_ceiling_cents(actual_cost_microcents)

        settled = await settle_usage_transactional(
            reservation_id=reservation_id,
            actual_input_tokens=req.actual_input_tokens,
            actual_output_tokens=req.actual_output_tokens,
            actual_cost_cents=actual_cost_cents,
            error_category=req.error_category,
            actual_cost_microcents=actual_cost_microcents,
            resolved_model_id=resolved_model_id,
            upstream_provider=upstream_provider,
            upstream_generation_id=upstream_generation_id,
            usage_source=usage_source,
        )

        # Release concurrency lease
        if res["lease_token"]:
            await release_concurrency_lease(
                res["user_id"], res["credential_id"], res["lease_token"]
            )

        # Reconcile token rate slots in Redis
        actual_tokens = req.actual_input_tokens + req.actual_output_tokens
        token_scopes = [
            RateLimitScope(
                scope_name="model_tpm",
                key=f"ragbait:rate:tpm:model:{res['model_id']}",
                limit=1000000,
                burst=0,
                weight=actual_tokens,
                unit="tokens",
            )
        ]
        await reconcile_rate_slots(token_scopes, res["request_id"], actual_tokens)

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
            RateLimitScope(
                "model_tpm",
                f"ragbait:rate:tpm:model:{res['model_id']}",
                1000000,
                0,
                unit="tokens",
            ),
        ]
        await release_rate_slots(rate_scopes, member_id=res["request_id"])

        return released


async def expire_reservation(
    reservation_id: UUID,
    reason: str = "lease_expired",
) -> ReleaseResult:
    """Expire an uncommitted reservation whose lease has timed out."""
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

        expired = await expire_reservation_transactional(reservation_id, reason=reason)

        if res["lease_token"]:
            await release_concurrency_lease(
                res["user_id"], res["credential_id"], res["lease_token"]
            )

        rate_scopes = [
            RateLimitScope("user", f"ragbait:rate:user:{res['user_id']}", 10, 1),
            RateLimitScope("credential", f"ragbait:rate:credential:{res['credential_id']}", 5, 1),
            RateLimitScope("provider", f"ragbait:rate:provider:{res['provider_id']}", 100, 1),
            RateLimitScope("model", f"ragbait:rate:model:{res['model_id']}", 100, 1),
            RateLimitScope(
                "model_tpm",
                f"ragbait:rate:tpm:model:{res['model_id']}",
                1000000,
                0,
                unit="tokens",
            ),
        ]
        await release_rate_slots(rate_scopes, member_id=res["request_id"])
        return expired


async def recover_expired_reservations(batch_size: int = 50) -> int:
    """Execute bounded sweeper pass: recover PostgreSQL state, then perform best-effort Redis cleanup.

    Concurrency-safe: uses FOR UPDATE SKIP LOCKED.
    Idempotent: safe to invoke concurrently from multiple workers.
    Returns count of expired reservations successfully recovered.
    """
    swept_items = await sweep_expired_reservations_batch(batch_size=batch_size)
    if not swept_items:
        return 0

    for item in swept_items:
        # Best-effort Redis concurrency lease release
        if item.get("lease_token"):
            try:
                await release_concurrency_lease(
                    item["user_id"], item["credential_id"], item["lease_token"]
                )
            except Exception as exc:
                logger.warning(
                    f"Best-effort Redis lease release failed for swept reservation {item['id']}: {exc}"
                )

        # Best-effort Redis rate slots release
        try:
            rate_scopes = [
                RateLimitScope("user", f"ragbait:rate:user:{item['user_id']}", 10, 1),
                RateLimitScope(
                    "credential", f"ragbait:rate:credential:{item['credential_id']}", 5, 1
                ),
                RateLimitScope("provider", f"ragbait:rate:provider:{item['provider_id']}", 100, 1),
                RateLimitScope("model", f"ragbait:rate:model:{item['model_id']}", 100, 1),
                RateLimitScope(
                    "model_tpm",
                    f"ragbait:rate:tpm:model:{item['model_id']}",
                    1000000,
                    0,
                    unit="tokens",
                ),
            ]
            await release_rate_slots(rate_scopes, member_id=item["request_id"])
        except Exception as exc:
            logger.warning(
                f"Best-effort Redis rate slots release failed for swept reservation {item['id']}: {exc}"
            )

    return len(swept_items)


async def reconcile_reservation(
    reservation_id: UUID,
    actual_input_tokens: int,
    actual_output_tokens: int,
    actual_cost_microcents: int | None = None,
    reconciliation_notes: str | None = None,
    usage_source: str = "authoritative",
) -> SettlementResult:
    """Reconcile an outcome_unknown or reconciliation_required reservation with confirmed actual usage."""
    pool = get_pool()
    async with pool.acquire() as conn:
        res = await conn.fetchrow(
            """
            SELECT id, user_id, credential_id, model_id, status, request_id
            FROM public.llm_usage_reservations
            WHERE id = $1
            """,
            reservation_id,
        )
        if not res:
            raise NotFoundError(f"Reservation '{reservation_id}' not found")

        if actual_cost_microcents is None:
            model = await _get_model_details(res["model_id"])
            actual_cost_microcents = compute_cost_microcents(
                actual_input_tokens,
                actual_output_tokens,
                model["input_cost_per_million_cents"],
                model["output_cost_per_million_cents"],
            )

        return await reconcile_usage_transactional(
            reservation_id=reservation_id,
            actual_input_tokens=actual_input_tokens,
            actual_output_tokens=actual_output_tokens,
            actual_cost_microcents=actual_cost_microcents,
            reconciliation_notes=reconciliation_notes,
            usage_source=usage_source,
        )


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
