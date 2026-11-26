"""Model manifest loading and validation for local multilingual-e5-small."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

MANIFEST_PATH = Path(__file__).parent / "model_manifest.json"


@dataclass(frozen=True)
class ArtifactMeta:
    """Metadata for a required model artifact."""

    rel_path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ModelManifest:
    """Authoritative specifications and cryptographic hashes for embedding model."""

    model_id: str
    model_revision: str
    source_repo: str
    embedding_dimension: int
    max_sequence_length: int
    runtime: str
    runtime_provider: str
    quantization: str
    pooling: str
    normalization: str
    passage_prefix: str
    query_prefix: str
    model_fingerprint: str
    artifacts: dict[str, ArtifactMeta]

    @classmethod
    def load(cls, manifest_path: Path | None = None) -> "ModelManifest":
        """Load manifest from json file."""
        path = manifest_path or MANIFEST_PATH
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        artifacts = {
            rel_path: ArtifactMeta(
                rel_path=rel_path,
                sha256=meta["sha256"],
                size_bytes=meta["size_bytes"],
            )
            for rel_path, meta in data["artifacts"].items()
        }

        return cls(
            model_id=data["model_id"],
            model_revision=data["model_revision"],
            source_repo=data["source_repo"],
            embedding_dimension=data["embedding_dimension"],
            max_sequence_length=data["max_sequence_length"],
            runtime=data["runtime"],
            runtime_provider=data["runtime_provider"],
            quantization=data["quantization"],
            pooling=data["pooling"],
            normalization=data["normalization"],
            passage_prefix=data["passage_prefix"],
            query_prefix=data["query_prefix"],
            model_fingerprint=data["model_fingerprint"],
            artifacts=artifacts,
        )

    def verify_local_directory(self, target_dir: Path) -> tuple[bool, list[str]]:
        """Verify that all required artifacts in target_dir exist and match SHA256 and size.

        Returns:
            (is_valid, list_of_error_reasons)
        """
        errors: list[str] = []
        if not target_dir.is_dir():
            return False, [f"Target directory {target_dir} does not exist"]

        for rel_path, meta in self.artifacts.items():
            file_path = target_dir / rel_path
            if not file_path.is_file():
                errors.append(f"Missing artifact: {rel_path}")
                continue

            # Verify size
            actual_size = file_path.stat().st_size
            if actual_size != meta.size_bytes:
                errors.append(
                    f"Size mismatch for {rel_path}: expected {meta.size_bytes}, got {actual_size}"
                )
                continue

            # Verify SHA256
            hasher = hashlib.sha256()
            with open(file_path, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            actual_sha = hasher.hexdigest().lower()
            if actual_sha != meta.sha256.lower():
                errors.append(
                    f"SHA256 mismatch for {rel_path}: expected {meta.sha256}, got {actual_sha}"
                )

        return len(errors) == 0, errors


# Canonical cached manifest instance
_CACHED_MANIFEST: ModelManifest | None = None


def get_model_manifest() -> ModelManifest:
    """Return the cached ModelManifest singleton."""
    global _CACHED_MANIFEST
    if _CACHED_MANIFEST is None:
        _CACHED_MANIFEST = ModelManifest.load()
    return _CACHED_MANIFEST
