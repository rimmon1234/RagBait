"""Deterministic TXT extraction with logical pagination and encoding validation."""

from pathlib import Path
from typing import Any

from app.modules.knowledge_base.extractor_exceptions import (
    CorruptedFileError,
    MalformedTextError,
    PayloadTooLargeError,
)


def extract_txt_pages(
    file_path: Path | str,
    max_extracted_text_bytes: int = 26_214_400,  # 25 MiB
) -> dict[str, Any]:
    """Deterministically extract logical pages from a TXT document.

    Guarantees:
    - Splits on form-feed character ('\\f') for logical pagination.
    - If no '\\f' exists, the entire document is treated as page 1.
    - Preserves empty logical pages.
    - Detects and rejects binary files disguised as text (null bytes, control char ratio).
    - Supports UTF-8 with automatic fallback to Latin-1.
    - Enforces maximum cumulative extracted text payload (raises PayloadTooLargeError).
    - Preserves exact character count and word count matching DB check constraints.
    """
    path_obj = Path(file_path)
    if not path_obj.exists() or not path_obj.is_file():
        raise CorruptedFileError(f"Target file does not exist: {file_path}")

    try:
        raw_bytes = path_obj.read_bytes()
    except OSError as e:
        raise CorruptedFileError(f"Failed to read file from disk: {e}") from e

    # Check for binary null bytes
    if b"\x00" in raw_bytes:
        raise MalformedTextError("File contains null bytes; binary files are not permitted.")

    # Decode with UTF-8 primary, Latin-1 fallback
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw_bytes.decode("latin-1")
        except Exception as e:
            raise MalformedTextError(f"Unable to decode text file: {e}") from e

    # Heuristic for non-printable control characters
    if text:
        control_chars = sum(1 for ch in text if ord(ch) < 32 and ch not in "\r\n\t\f")
        if (control_chars / len(text)) > 0.05:
            raise MalformedTextError("File contains excessive non-printable control characters.")

    # Check total payload size
    total_bytes = len(text.encode("utf-8"))
    if total_bytes > max_extracted_text_bytes:
        raise PayloadTooLargeError(
            f"Extracted text exceeds limit ({total_bytes} bytes; "
            f"maximum allowed is {max_extracted_text_bytes} bytes)."
        )

    # Form-feed '\\f' logical pagination
    if "\f" in text:
        raw_segments = text.split("\f")
    else:
        raw_segments = [text]

    pages: list[dict[str, Any]] = []
    for page_num, segment in enumerate(raw_segments, start=1):
        char_count = len(segment)
        word_count = len(segment.split())
        has_text = bool(segment.strip())

        pages.append(
            {
                "page_number": page_num,
                "raw_text": segment,
                "character_count": char_count,
                "word_count": word_count,
                "has_text": has_text,
            }
        )

    pages_with_text = sum(1 for p in pages if p["has_text"])
    has_any_text = pages_with_text > 0

    return {
        "pages": pages,
        "total_pages": len(pages),
        "pages_with_text": pages_with_text,
        "has_any_text": has_any_text,
    }
