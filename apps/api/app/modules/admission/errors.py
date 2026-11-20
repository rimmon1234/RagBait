"""Domain exceptions for Admission Control, Rate Limiting, and Usage Ledger."""

from typing import Any

from app.core.errors import AppError


class AdmissionError(AppError):
    """Base exception for all admission control and usage ledger errors."""

    def __init__(
        self,
        code: str = "ADMISSION_ERROR",
        message: str = "Admission control failure",
        status_code: int = 400,
        details: Any = None,
    ) -> None:
        super().__init__(code=code, message=message, status_code=status_code, details=details)


class AdmissionRejectionError(AdmissionError):
    """Raised when an inference attempt is rejected due to rate limits or cooldowns."""

    def __init__(
        self,
        reason: str,
        category: str,
        retry_after: int = 1,
        details: Any = None,
    ) -> None:
        self.category = category
        self.retry_after = retry_after
        super().__init__(
            code="RATE_LIMIT_EXCEEDED",
            message=f"Admission rejected: {reason}",
            status_code=429,
            details={"category": category, "retry_after": retry_after, **(details or {})},
        )


class ConcurrencyLimitError(AdmissionError):
    """Raised when user or credential maximum active concurrency is reached."""

    def __init__(
        self,
        reason: str,
        scope: str,
        current_limit: int,
    ) -> None:
        self.scope = scope
        self.current_limit = current_limit
        super().__init__(
            code="CONCURRENCY_LIMIT_EXCEEDED",
            message=f"Concurrency limit reached ({scope}): {reason}",
            status_code=429,
            details={"scope": scope, "limit": current_limit, "retry_after": 2},
        )


class BudgetExhaustedError(AdmissionError):
    """Raised when a credential's daily or monthly request or spending budget is exhausted."""

    def __init__(
        self,
        reason: str,
        limit_type: str,
        period: str,
        current_val: int,
        limit_val: int,
    ) -> None:
        super().__init__(
            code="BUDGET_EXHAUSTED",
            message=f"Credential budget exhausted ({period} {limit_type}): {reason}",
            status_code=429,
            details={
                "limit_type": limit_type,
                "period": period,
                "current": current_val,
                "limit": limit_val,
            },
        )


class LeaseError(AdmissionError):
    """Raised when a concurrency lease token is invalid, stale, or expired."""

    def __init__(self, message: str = "Concurrency lease is expired or invalid") -> None:
        super().__init__(
            code="LEASE_INVALID",
            message=message,
            status_code=409,
        )


class InvalidStateTransitionError(AdmissionError):
    """Raised when attempting an illegal transition on a usage reservation."""

    def __init__(self, from_state: str, to_state: str) -> None:
        super().__init__(
            code="INVALID_STATE_TRANSITION",
            message=f"Cannot transition reservation from '{from_state}' to '{to_state}'",
            status_code=409,
        )


class PricingUnavailableError(AdmissionError):
    """Raised when model pricing is unknown but credential enforces a spending cap."""

    def __init__(self, model_id: str) -> None:
        super().__init__(
            code="PRICING_UNAVAILABLE",
            message=f"Model '{model_id}' pricing is unavailable; failing closed for spending-capped credential",
            status_code=422,
        )
