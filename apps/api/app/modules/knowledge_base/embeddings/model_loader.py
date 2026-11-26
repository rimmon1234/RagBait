"""Offline singleton model loader for multilingual-e5-small via FastEmbed."""

import logging
import threading
from pathlib import Path

from fastembed import TextEmbedding
from fastembed.common.model_description import ModelSource, PoolingType

from app.modules.knowledge_base.embeddings.config import embedding_settings
from app.modules.knowledge_base.embeddings.model_manifest import (
    ModelManifest,
    get_model_manifest,
)

logger = logging.getLogger("ragbait.kb.embeddings.model_loader")

_LOADER_LOCK = threading.Lock()
_CACHED_EMBEDDER: TextEmbedding | None = None
_CUSTOM_MODEL_REGISTERED: bool = False


class ModelNotProvisionedError(RuntimeError):
    """Raised when local model artifacts are missing or fail cryptographic verification."""

    pass


def _register_custom_e5_model(manifest: ModelManifest) -> None:
    """Register multilingual-e5-small in FastEmbed's dynamic registry."""
    global _CUSTOM_MODEL_REGISTERED
    if _CUSTOM_MODEL_REGISTERED:
        return

    # Check if already present in supported models
    for m in TextEmbedding.list_supported_models():
        if m["model"].lower() == manifest.model_id.lower():
            _CUSTOM_MODEL_REGISTERED = True
            return

    logger.info(
        f"Registering custom FastEmbed model: {manifest.model_id} (source: {manifest.source_repo})"
    )
    TextEmbedding.add_custom_model(
        model=manifest.model_id,
        pooling=PoolingType.MEAN,
        normalization=True,
        sources=ModelSource(hf=manifest.source_repo),
        dim=manifest.embedding_dimension,
        model_file="onnx/model_quantized.onnx",
        description=f"Multilingual E5 Small INT8 ONNX (fingerprint: {manifest.model_fingerprint})",
        license="mit",
        size_in_gb=0.118,
        additional_files=[
            "tokenizer.json",
            "config.json",
            "special_tokens_map.json",
            "tokenizer_config.json",
        ],
    )
    _CUSTOM_MODEL_REGISTERED = True


def get_embedding_model(
    custom_dir: Path | None = None,
    verify_offline: bool = True,
) -> tuple[TextEmbedding, ModelManifest]:
    """Return the cached offline FastEmbed TextEmbedding model and its manifest.

    Guarantees:
    1. Loads from local model directory only (local_files_only=True).
    2. Verifies cryptographic manifest before loading.
    3. Thread-safe singleton creation per worker process.
    4. Never triggers Hugging Face downloads during normal runtime.

    Raises:
        ModelNotProvisionedError: If artifacts are missing or invalid.
    """
    global _CACHED_EMBEDDER

    manifest = get_model_manifest()
    model_dir = custom_dir or embedding_settings.model_dir

    if _CACHED_EMBEDDER is not None and custom_dir is None:
        return _CACHED_EMBEDDER, manifest

    with _LOADER_LOCK:
        if _CACHED_EMBEDDER is not None and custom_dir is None:
            return _CACHED_EMBEDDER, manifest

        # 1. Verify local files
        if verify_offline:
            is_valid, errors = manifest.verify_local_directory(model_dir)
            if not is_valid:
                error_msg = (
                    f"Local embedding model artifacts at {model_dir} are missing or invalid: "
                    + "; ".join(errors)
                    + ". Run 'uv run python -m app.cli.provision_embedding_model' to provision."
                )
                logger.error(error_msg)
                raise ModelNotProvisionedError(error_msg)

        # 2. Register custom model if needed
        _register_custom_e5_model(manifest)

        # 3. Instantiate FastEmbed pointing strictly to local files
        logger.info(
            f"Initializing FastEmbed TextEmbedding from local artifacts at {model_dir} (threads={embedding_settings.onnx_threads})"
        )
        embedder = TextEmbedding(
            model_name=manifest.model_id,
            specific_model_path=str(model_dir),
            local_files_only=True,
            threads=embedding_settings.onnx_threads,
        )

        if custom_dir is None:
            _CACHED_EMBEDDER = embedder

        return embedder, manifest


def reset_model_loader() -> None:
    """Reset cached embedder instance (primarily for tests)."""
    global _CACHED_EMBEDDER
    with _LOADER_LOCK:
        _CACHED_EMBEDDER = None
