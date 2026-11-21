"""Deterministic PDF text extraction using pypdf with physical page provenance."""

from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.errors import FileNotDecryptedError, PyPdfError, WrongPasswordError

from app.modules.knowledge_base.extractor_exceptions import (
    CorruptFileError,
    EncryptedPdfError,
    PageLimitExceededError,
    PayloadTooLargeError,
)


def extract_pdf_pages(
    file_path: Path | str,
    max_pages: int = 2000,
    max_extracted_text_bytes: int = 26_214_400,  # 25 MiB
) -> dict[str, Any]:
    """Deterministically extract text page-by-page from a PDF file.

    Guarantees:
    - Exactly one record per physical PDF page (1-indexed).
    - Preserves empty physical pages (raw_text="", character_count=0, word_count=0, has_text=False).
    - Detects password-protected/encrypted PDFs and raises EncryptedPdfError.
    - Detects malformed/corrupted files and raises CorruptFileError.
    - Enforces maximum physical page limit (raises PageLimitExceededError).
    - Enforces maximum cumulative extracted text payload (raises PayloadTooLargeError).
    - Correctly flags scanned PDFs (has_any_text=False).
    """
    path_obj = Path(file_path)
    if not path_obj.exists() or not path_obj.is_file():
        raise CorruptFileError(f"Target file does not exist: {file_path}")

    try:
        reader = PdfReader(str(path_obj))
    except (WrongPasswordError, FileNotDecryptedError) as e:
        raise EncryptedPdfError(f"PDF is encrypted: {e}") from e
    except (PyPdfError, ValueError, OSError) as e:
        raise CorruptFileError(f"Failed to read PDF file structure: {e}") from e
    except Exception as e:
        raise CorruptFileError(f"Unexpected error reading PDF: {e}") from e

    # Check encryption flag
    if reader.is_encrypted:
        raise EncryptedPdfError("PDF file is password protected or encrypted.")

    num_pages = len(reader.pages)
    if num_pages == 0:
        raise CorruptFileError("PDF file contains zero pages.")

    if num_pages > max_pages:
        raise PageLimitExceededError(
            f"PDF exceeds page limit ({num_pages} pages; maximum allowed is {max_pages})."
        )

    pages: list[dict[str, Any]] = []
    total_text_bytes = 0

    for page_num, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except (WrongPasswordError, FileNotDecryptedError) as e:
            raise EncryptedPdfError(f"Page {page_num} is encrypted: {e}") from e
        except Exception:
            # If text extraction on a single corrupted page stream fails,
            # preserve page record as empty text rather than aborting entire document
            page_text = ""

        # Enforce exact character and word count calculations matching DB constraints
        # PostgreSQL: character_count = char_length(raw_text)
        # PostgreSQL: has_text = (char_length(trim(raw_text)) > 0)
        char_count = len(page_text)
        word_count = len(page_text.split())
        has_text = bool(page_text.strip())

        text_bytes = len(page_text.encode("utf-8"))
        total_text_bytes += text_bytes
        if total_text_bytes > max_extracted_text_bytes:
            raise PayloadTooLargeError(
                f"Cumulative extracted text exceeds limit ({total_text_bytes} bytes; "
                f"maximum allowed is {max_extracted_text_bytes} bytes)."
            )

        pages.append(
            {
                "page_number": page_num,
                "raw_text": page_text,
                "character_count": char_count,
                "word_count": word_count,
                "has_text": has_text,
            }
        )

    pages_with_text = sum(1 for p in pages if p["has_text"])
    has_any_text = pages_with_text > 0

    return {
        "pages": pages,
        "total_pages": num_pages,
        "pages_with_text": pages_with_text,
        "has_any_text": has_any_text,
    }
