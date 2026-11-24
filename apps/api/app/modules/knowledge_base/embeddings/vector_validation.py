"""Deterministic validation for dense embedding vectors."""

import math
from collections.abc import Sequence

EXPECTED_DIMENSIONS: int = 384
L2_NORM_TOLERANCE: float = 1e-4


class VectorValidationError(ValueError):
    """Raised when an embedding vector fails mathematical or schema invariants."""

    pass


def validate_vector(
    vector: Sequence[float],
    expected_dimensions: int = EXPECTED_DIMENSIONS,
    tolerance: float = L2_NORM_TOLERANCE,
) -> None:
    """Validate that vector meets strict dimensional, finite, and L2 unit norm invariants.

    Raises:
        VectorValidationError: If any condition is violated.
    """
    if len(vector) != expected_dimensions:
        raise VectorValidationError(
            f"Vector dimension mismatch: expected {expected_dimensions}, got {len(vector)}"
        )

    sum_sq = 0.0
    for idx, val in enumerate(vector):
        if not math.isfinite(val):
            raise VectorValidationError(f"Vector contains non-finite element at index {idx}: {val}")
        sum_sq += val * val

    if sum_sq <= 0.0:
        raise VectorValidationError("Vector has zero magnitude")

    norm = math.sqrt(sum_sq)
    if abs(norm - 1.0) > tolerance:
        raise VectorValidationError(
            f"Vector is not L2 unit normalized: norm={norm:.6f}, expected 1.0 (tol={tolerance})"
        )
