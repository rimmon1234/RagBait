"""Hard process-isolated document parser execution with OS-level termination."""

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from app.modules.knowledge_base.extractor_exceptions import (
    ChecksumMismatchError,
    CorruptFileError,
    EncryptedPdfError,
    ExtractionError,
    ExtractionTimeoutError,
    FileSizeMismatchError,
    InvalidMimeTypeError,
    MalformedTextError,
    PageLimitExceededError,
    PayloadTooLargeError,
    PermanentExtractionError,
    TransientExtractionError,
)
from app.modules.knowledge_base.pdf_extractor import extract_pdf_pages
from app.modules.knowledge_base.txt_extractor import extract_txt_pages

logger = logging.getLogger("ragbait.kb.parser_process")

ERROR_CODE_MAP: dict[str, type[ExtractionError]] = {
    "CORRUPT_FILE": CorruptFileError,
    "ENCRYPTED_PDF": EncryptedPdfError,
    "PAGE_LIMIT_EXCEEDED": PageLimitExceededError,
    "PAYLOAD_TOO_LARGE": PayloadTooLargeError,
    "MALFORMED_TEXT": MalformedTextError,
    "INVALID_MIME_TYPE": InvalidMimeTypeError,
    "CHECKSUM_MISMATCH": ChecksumMismatchError,
    "FILE_SIZE_MISMATCH": FileSizeMismatchError,
    "PARSER_TIMEOUT": ExtractionTimeoutError,
}


def _run_extraction_sync(
    input_path: Path,
    mime_type: str,
    max_pages: int,
    max_bytes: int,
) -> dict[str, Any]:
    """Dispatch file extraction to the appropriate parser synchronously."""
    if mime_type == "application/pdf":
        return extract_pdf_pages(
            file_path=input_path,
            max_pages=max_pages,
            max_extracted_text_bytes=max_bytes,
        )
    elif mime_type == "text/plain":
        return extract_txt_pages(
            file_path=input_path,
            max_extracted_text_bytes=max_bytes,
        )
    else:
        raise InvalidMimeTypeError(f"Unsupported MIME type: {mime_type}")


def main() -> None:
    """CLI entry point for isolated child process."""
    parser = argparse.ArgumentParser(description="Isolated Document Parser Process")
    parser.add_argument("--input", required=True, help="Path to input document file")
    parser.add_argument("--mime", required=True, help="MIME type of document")
    parser.add_argument("--output", required=True, help="Path to write JSON output")
    parser.add_argument("--max-pages", type=int, default=2000, help="Maximum physical pages")
    parser.add_argument("--max-bytes", type=int, default=26_214_400, help="Maximum text bytes")

    args = parser.parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    payload: dict[str, Any]

    try:
        result = _run_extraction_sync(
            input_path=input_path,
            mime_type=args.mime,
            max_pages=args.max_pages,
            max_bytes=args.max_bytes,
        )
        payload = {"status": "success", "result": result}
    except ExtractionError as e:
        payload = {
            "status": "error",
            "error_code": e.code,
            "error_message": e.message,
            "is_permanent": isinstance(e, PermanentExtractionError),
        }
    except Exception as e:
        payload = {
            "status": "error",
            "error_code": "CORRUPT_FILE",
            "error_message": f"Unexpected parser crash: {e}",
            "is_permanent": True,
        }

    try:
        output_path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as e:
        sys.stderr.write(f"Failed to write parser output file: {e}\n")
        sys.exit(1)

    sys.exit(0)


async def run_parser_isolated(
    input_path: Path,
    mime_type: str,
    timeout_seconds: float = 120.0,
    max_pages: int = 2000,
    max_bytes: int = 26_214_400,
) -> dict[str, Any]:
    """Execute document parsing inside an isolated OS process with hard timeout.

    If the timeout expires, the child process is terminated immediately with SIGKILL / TerminateProcess.
    This guarantees immunity against infinite loops, catastrophic regex backtracking,
    or memory-exhaustion hangs in C/Rust libraries.
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, prefix="kb_parser_out_"
    ) as tmp_out:
        output_file = Path(tmp_out.name)

    try:
        # Determine PYTHONPATH so child process imports application modules reliably
        env = dict(os.environ)
        # Ensure apps/api directory is on PYTHONPATH
        api_root = str(Path(__file__).resolve().parent.parent.parent.parent)
        current_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{api_root}{os.pathsep}{current_pythonpath}" if current_pythonpath else api_root
        )

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "app.modules.knowledge_base.parser_process",
            "--input",
            str(input_path),
            "--mime",
            mime_type,
            "--output",
            str(output_file),
            "--max-pages",
            str(max_pages),
            "--max-bytes",
            str(max_bytes),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_seconds
            )
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            raise ExtractionTimeoutError(
                f"Document parsing exceeded execution deadline of {timeout_seconds} seconds."
            ) from None

        if not output_file.exists() or output_file.stat().st_size == 0:
            stderr_msg = stderr_bytes.decode(errors="ignore").strip()
            raise CorruptFileError(
                f"Parser process exited abnormally (code {proc.returncode}): {stderr_msg}"
            )

        try:
            content = output_file.read_text(encoding="utf-8")
            data = json.loads(content)
        except Exception as e:
            raise CorruptFileError(f"Failed to read parser output payload: {e}") from e

        if data.get("status") == "success":
            return data["result"]  # type: ignore[no-any-return]

        err_code = data.get("error_code", "EXTRACTION_FAILED")
        err_msg = data.get("error_message", "Document extraction failed.")
        is_permanent = data.get("is_permanent", True)

        exc_class = ERROR_CODE_MAP.get(err_code)
        if exc_class is not None:
            raise exc_class(err_msg)

        if is_permanent:
            raise PermanentExtractionError(err_msg, code=err_code)
        else:
            raise TransientExtractionError(err_msg, code=err_code)

    finally:
        try:
            if output_file.exists():
                output_file.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
