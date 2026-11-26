"""CLI command to download, verify, and provision local multilingual-e5-small model artifacts."""

import argparse
import logging
import shutil
import sys
import tempfile
from pathlib import Path

from huggingface_hub import hf_hub_download

from app.modules.knowledge_base.embeddings.model_manifest import get_model_manifest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("ragbait.cli.provision_embedding_model")


def get_default_model_dir() -> Path:
    """Return the canonical local model directory in assets."""
    assets_dir = Path(__file__).parent.parent / "modules" / "knowledge_base" / "assets"
    return assets_dir / "models" / "multilingual-e5-small"


def provision_model(target_dir: Path | None = None, verify_only: bool = False) -> bool:
    """Download and atomically verify model artifacts according to the model manifest.

    Args:
        target_dir: Target directory for artifacts. Defaults to assets/models/multilingual-e5-small.
        verify_only: If True, only verifies existing files without downloading.

    Returns:
        True if artifacts are fully verified, False otherwise.
    """
    manifest = get_model_manifest()
    out_dir = target_dir or get_default_model_dir()

    logger.info(f"Target model directory: {out_dir}")
    logger.info(f"Model ID: {manifest.model_id} (fingerprint: {manifest.model_fingerprint})")
    logger.info(f"Source repo: {manifest.source_repo} @ revision {manifest.model_revision}")

    if verify_only:
        logger.info("Running verification on existing model directory...")
        is_valid, errors = manifest.verify_local_directory(out_dir)
        if is_valid:
            logger.info("Model verification SUCCESSFUL: all artifacts match manifest.")
            return True
        for err in errors:
            logger.error(f"  Verification error: {err}")
        return False

    # Check if already fully provisioned and valid
    if out_dir.is_dir():
        is_valid, _ = manifest.verify_local_directory(out_dir)
        if is_valid:
            logger.info("Local model is already fully provisioned and verified.")
            return True

    # Staging directory for atomic download
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix="e5-model-staging-", dir=out_dir.parent))

    try:
        logger.info("Downloading required artifacts from HuggingFace Hub...")
        for rel_path, meta in manifest.artifacts.items():
            dest_file = temp_dir / rel_path
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            logger.info(f"Downloading {rel_path} (expected size: {meta.size_bytes} bytes)...")
            downloaded_path = hf_hub_download(
                repo_id=manifest.source_repo,
                filename=rel_path,
                revision=manifest.model_revision,
            )
            shutil.copy(downloaded_path, dest_file)

        # Verify staged files
        logger.info("Verifying staged artifacts against cryptographic manifest...")
        is_valid, errors = manifest.verify_local_directory(temp_dir)
        if not is_valid:
            for err in errors:
                logger.error(f"  Staged artifact verification failed: {err}")
            return False

        # Atomic installation: move files into target_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        for rel_path in manifest.artifacts.keys():
            staged_file = temp_dir / rel_path
            final_file = out_dir / rel_path
            final_file.parent.mkdir(parents=True, exist_ok=True)
            if final_file.exists():
                final_file.unlink()
            shutil.move(str(staged_file), str(final_file))

        logger.info(f"Successfully provisioned model artifacts into {out_dir}")
        return True

    except Exception as exc:
        logger.error(f"Failed to provision embedding model: {exc}", exc_info=True)
        return False

    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Provision and verify local multilingual-e5-small ONNX model artifacts."
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=None,
        help="Custom destination directory for model artifacts",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify existing artifacts against manifest without downloading",
    )
    args = parser.parse_args()

    success = provision_model(target_dir=args.target_dir, verify_only=args.verify_only)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
