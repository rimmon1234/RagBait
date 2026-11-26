"""Configuration settings and defaults for local embedding infrastructure."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class EmbeddingSettings(BaseSettings):
    """Runtime configuration for ONNX Runtime FastEmbed local embeddings."""

    # Model directory defaults to canonical assets directory
    model_dir: Path = Field(
        default_factory=lambda: (
            Path(__file__).parent.parent / "assets" / "models" / "multilingual-e5-small"
        ),
        description="Path to locally provisioned multilingual-e5-small model artifacts",
    )
    expected_fingerprint: str = Field(
        default="e5_small_int8_761b726d_f80102d3",
        description="Cryptographic fingerprint required of the active embedding model",
    )
    batch_size: int = Field(
        default=16,
        description="Maximum batch size for CPU ONNX embedding inference",
    )
    max_query_tokens: int = Field(
        default=128,
        description="Hard maximum token ceiling for user query input",
    )
    max_passage_tokens: int = Field(
        default=512,
        description="Hard maximum token ceiling for formatted passage input",
    )
    onnx_threads: int = Field(
        default=4,
        description="Number of CPU threads allocated to ONNX Runtime session",
    )


# Singleton settings instance
embedding_settings = EmbeddingSettings()
