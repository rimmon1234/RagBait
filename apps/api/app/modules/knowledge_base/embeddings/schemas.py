"""Data schemas for local embeddings."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EmbeddingResult:
    """Validated result of an embedding inference operation."""

    vector: list[float]
    dimensions: int
    model_id: str
    model_fingerprint: str
    input_tokens: int
    preprocessing_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "vector": self.vector,
            "dimensions": self.dimensions,
            "model_id": self.model_id,
            "model_fingerprint": self.model_fingerprint,
            "input_tokens": self.input_tokens,
            "preprocessing_version": self.preprocessing_version,
        }
