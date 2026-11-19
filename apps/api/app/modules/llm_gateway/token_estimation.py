"""Conservative server-side token estimation for admission control."""

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class TokenEstimate(BaseModel):
    """Server-side conservative token estimate for budget and rate reservation."""

    estimator_type: str = "conservative_heuristic_v1"
    estimated_input_tokens: int = Field(..., ge=0)
    reserved_output_tokens: int = Field(..., ge=0)
    total_estimated_tokens: int = Field(..., ge=0)
    requested_max_output_tokens: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def estimate_generation_tokens(
    messages: list[dict[str, str]],
    model_context_window: int,
    model_max_output_tokens: int,
    requested_max_output: int | None = None,
) -> TokenEstimate:
    """Calculate a conservative, server-validated token estimate.

    Guarantees:
      - Operates purely server-side (never trusts client claims).
      - Applies conservative multiplier (1 token per ~3.2 characters + per-message framing overhead).
      - Caps reserved output at model limits.
      - Verifies that input + output does not exceed the model context window.
    """
    total_input = 3  # Base prompt framing overhead

    for msg in messages:
        content = msg.get("content", "")
        # Per-message structural token overhead (role indicator, formatting tokens)
        message_overhead = 4
        # Conservative char-to-token ratio (3.2 chars/token for English/code, minimum 1 token)
        content_tokens = max(1, int(len(content) / 3.2) + 1) if content else 0
        total_input += message_overhead + content_tokens

    # Determine reserved output tokens
    if requested_max_output is not None and requested_max_output > 0:
        reserved_output = min(requested_max_output, model_max_output_tokens)
    else:
        # Default reservation: up to 2048 tokens or model max
        reserved_output = min(2048, model_max_output_tokens)

    # Ensure context window is respected
    if total_input > model_context_window:
        raise ValueError(
            f"Estimated input tokens ({total_input}) exceeds model context window ({model_context_window})"
        )

    # Adjust output if remaining context window is smaller than requested output
    available_output = model_context_window - total_input
    if available_output <= 0:
        raise ValueError(
            f"Input tokens ({total_input}) exhaust available context window ({model_context_window})"
        )

    effective_output = min(reserved_output, available_output)

    return TokenEstimate(
        estimator_type="conservative_heuristic_v1",
        estimated_input_tokens=total_input,
        reserved_output_tokens=effective_output,
        total_estimated_tokens=total_input + effective_output,
        requested_max_output_tokens=requested_max_output,
    )
