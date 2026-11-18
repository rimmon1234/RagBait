"""RagBait Admission Control, Concurrency Leases & Usage Ledger Module."""

from app.modules.admission.errors import (
    AdmissionError,
    AdmissionRejectionError,
    BudgetExhaustedError,
    ConcurrencyLimitError,
    InvalidStateTransitionError,
    LeaseError,
    PricingUnavailableError,
)
from app.modules.admission.router import router as usage_router
from app.modules.admission.schemas import (
    AdmissionRequest,
    AdmissionReservation,
    LeaseRenewalResult,
    ReleaseResult,
    ReservationStatus,
    ReservationView,
    SettlementRequest,
    SettlementResult,
    UsageEventType,
    UsageSummaryResponse,
)
from app.modules.admission.service import (
    get_usage_summary_for_user,
    mark_dispatched,
    mark_outcome_unknown,
    release_reservation,
    renew_reservation_lease,
    reserve_generation,
    settle_reservation,
)

__all__ = [
    "AdmissionError",
    "AdmissionRejectionError",
    "AdmissionRequest",
    "AdmissionReservation",
    "BudgetExhaustedError",
    "ConcurrencyLimitError",
    "InvalidStateTransitionError",
    "LeaseError",
    "LeaseRenewalResult",
    "PricingUnavailableError",
    "ReleaseResult",
    "ReservationStatus",
    "ReservationView",
    "SettlementRequest",
    "SettlementResult",
    "UsageEventType",
    "UsageSummaryResponse",
    "get_usage_summary_for_user",
    "mark_dispatched",
    "mark_outcome_unknown",
    "release_reservation",
    "renew_reservation_lease",
    "reserve_generation",
    "settle_reservation",
    "usage_router",
]
