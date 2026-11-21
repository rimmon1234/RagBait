"""Validation and sanitization helpers for Knowledge Base documents."""

import hashlib
import os
from pathlib import PurePosixPath

from app.core.config import settings
from app.core.errors import BadRequestError

ALLOWED_MIME_TYPES: set[str] = {"application/pdf", "text/plain"}
MIME_EXTENSION_MAP: dict[str, set[str]] = {
    "application/pdf": {".pdf"},
    "text/plain": {".txt"},
}


def sanitize_title(title: str) -> str:
    """Sanitize and validate document title."""
    cleaned = title.strip()
    if not cleaned:
        raise BadRequestError("Document title cannot be empty.", code="INVALID_TITLE")
    if len(cleaned) > 255:
        raise BadRequestError("Document title exceeds 255 characters.", code="TITLE_TOO_LONG")
    return cleaned


def validate_filename(filename: str, mime_type: str) -> str:
    """Sanitize original filename and verify extension matches MIME type."""
    if not filename or not filename.strip():
        raise BadRequestError("Filename cannot be empty.", code="INVALID_FILENAME")

    # Sanitize path traversal across POSIX and Windows separators
    sanitized = filename.replace("\\", "/")
    basename = PurePosixPath(sanitized).name.strip()

    # Reject empty, dot-only, or hidden file names
    if not basename or basename.startswith(".") or basename in {".", ".."}:
        raise BadRequestError("Invalid filename provided.", code="INVALID_FILENAME")

    # Reject null bytes or suspicious control characters
    if "\x00" in basename or any(ord(c) < 32 for c in basename):
        raise BadRequestError(
            "Filename contains invalid control characters.", code="INVALID_FILENAME"
        )

    if len(basename) > 255:
        raise BadRequestError("Filename exceeds 255 characters.", code="FILENAME_TOO_LONG")

    # Verify MIME type is supported
    if mime_type not in ALLOWED_MIME_TYPES:
        raise BadRequestError(
            f"Unsupported MIME type '{mime_type}'. Supported: {sorted(ALLOWED_MIME_TYPES)}",
            code="UNSUPPORTED_MIME_TYPE",
        )

    # Check extension
    _, ext = os.path.splitext(basename)
    ext_lower = ext.lower()
    allowed_exts = MIME_EXTENSION_MAP.get(mime_type, set())

    if ext_lower not in allowed_exts:
        raise BadRequestError(
            f"Filename extension '{ext}' does not match MIME type '{mime_type}'. Expected one of {sorted(allowed_exts)}",
            code="EXTENSION_MIME_MISMATCH",
        )

    return basename


def validate_content_and_checksum(
    data: bytes,
    mime_type: str,
    expected_size: int | None = None,
    expected_checksum: str | None = None,
) -> tuple[int, str]:
    """Inspect file content, verify magic bytes / encoding, and compute SHA-256.

    Returns:
        tuple[int, str]: (actual_size_bytes, actual_sha256_hex)
    """
    actual_size = len(data)
    if actual_size == 0:
        raise BadRequestError("Uploaded file is empty (0 bytes).", code="EMPTY_FILE")

    if actual_size > settings.KB_MAX_FILE_SIZE_BYTES:
        raise BadRequestError(
            f"Uploaded file size ({actual_size} bytes) exceeds maximum allowed {settings.KB_MAX_FILE_SIZE_BYTES} bytes (50MB).",
            code="FILE_TOO_LARGE",
        )

    if expected_size is not None and actual_size != expected_size:
        raise BadRequestError(
            f"Uploaded file size ({actual_size} bytes) does not match expected size ({expected_size} bytes).",
            code="FILE_SIZE_MISMATCH",
        )

    # Format inspection
    if mime_type == "application/pdf":
        _validate_pdf_structure(data)
    elif mime_type == "text/plain":
        _validate_text_structure(data)
    else:
        raise BadRequestError(f"Unsupported MIME type '{mime_type}'.", code="UNSUPPORTED_MIME_TYPE")

    # SHA-256 verification
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if expected_checksum:
        cleaned_expected = expected_checksum.strip().lower()
        if actual_sha256.lower() != cleaned_expected:
            raise BadRequestError(
                f"SHA-256 checksum mismatch. Expected {cleaned_expected}, calculated {actual_sha256}.",
                code="CHECKSUM_MISMATCH",
            )

    return actual_size, actual_sha256


def _validate_pdf_structure(data: bytes) -> None:
    """Validate PDF header and EOF trailer presence."""
    # Magic bytes check: %PDF- in first 1024 bytes (ISO 32000)
    head = data[:1024]
    if b"%PDF-" not in head:
        raise BadRequestError(
            "File is not a valid PDF document: missing '%PDF-' header.",
            code="INVALID_PDF_FORMAT",
        )

    # EOF marker check: %%EOF towards end of file (last 4096 bytes)
    tail = data[-4096:] if len(data) > 4096 else data
    if b"%%EOF" not in tail:
        raise BadRequestError(
            "File is not a valid PDF document: missing '%%EOF' trailer marker.",
            code="INVALID_PDF_FORMAT",
        )


def _validate_text_structure(data: bytes) -> None:
    """Validate text file does not contain binary nulls and decodes properly."""
    # Binary null check
    if b"\x00" in data:
        raise BadRequestError(
            "File contains binary null characters and is not a valid plain text document.",
            code="INVALID_TEXT_FORMAT",
        )

    # Decoding check: must decode as UTF-8 or Latin-1
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            data.decode("latin-1")
        except UnicodeDecodeError as e:
            raise BadRequestError(
                f"File contains invalid text encoding: {e}",
                code="INVALID_TEXT_ENCODING",
            ) from e
