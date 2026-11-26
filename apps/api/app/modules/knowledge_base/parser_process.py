"""Hard process- and container-isolated document parser execution."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.modules.knowledge_base.extraction_diagnostics import assess_pdf_extraction_quality
from app.modules.knowledge_base.extractor_exceptions import (
    ChecksumMismatchError,
    CorruptedFileError,
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
from app.modules.knowledge_base.ocr_processor import (
    OCR_ELIGIBLE_TRIAGE,
    PageStage4Result,
    run_conditional_ocr_and_renders,
)
from app.modules.knowledge_base.pdf_extractor import extract_pdf_pages
from app.modules.knowledge_base.text_normalizer import (
    NORMALIZATION_VERSION,
    normalize_document_pages,
)
from app.modules.knowledge_base.txt_extractor import extract_txt_pages

if TYPE_CHECKING:
    from app.modules.knowledge_base.parser_sandbox import (
        ContainerSandboxConfig,
        ParserEngine,
        ParserIsolationMode,
    )

logger = logging.getLogger("ragbait.kb.parser_process")

# Explicit environment allowlist: excludes all database, Redis, Supabase, and API credentials
SAFE_SUBPROCESS_ENV_KEYS: frozenset[str] = frozenset(
    {
        # Windows essential runtime keys
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        # POSIX essential runtime keys
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "LD_LIBRARY_PATH",
    }
)

ERROR_CODE_MAP: dict[str, type[ExtractionError]] = {
    "CORRUPTED_FILE": CorruptedFileError,
    "CORRUPT_FILE": CorruptedFileError,  # Backward compatibility alias
    "ENCRYPTED_PDF": EncryptedPdfError,
    "PAGE_LIMIT_EXCEEDED": PageLimitExceededError,
    "PAYLOAD_TOO_LARGE": PayloadTooLargeError,
    "MALFORMED_TEXT": MalformedTextError,
    "INVALID_MIME_TYPE": InvalidMimeTypeError,
    "CHECKSUM_MISMATCH": ChecksumMismatchError,
    "FILE_SIZE_MISMATCH": FileSizeMismatchError,
    "PARSER_TIMEOUT": ExtractionTimeoutError,
}


def _extract_pdf_with_pymupdf(
    input_path: Path,
    *,
    max_pages: int,
    max_extracted_text_bytes: int,
) -> dict[str, Any]:
    """PyMuPDF extraction helper intended strictly for hardened container sandbox execution."""
    try:
        import fitz
    except ImportError as exc:
        raise PermanentExtractionError(
            f"PyMuPDF engine is only available inside the parser container sandbox: {exc}",
            code="SANDBOX_REQUIRED_FOR_ENGINE",
        ) from exc

    try:
        doc = fitz.open(str(input_path))
    except Exception as exc:
        raise CorruptedFileError(f"Failed to read PDF with PyMuPDF: {exc}") from exc

    try:
        if doc.needs_pass:
            raise EncryptedPdfError("PDF file is password protected or encrypted.")
        num_pages = doc.page_count
        if num_pages == 0:
            raise CorruptedFileError("PDF file contains zero pages.")
        if num_pages > max_pages:
            raise PageLimitExceededError(
                f"PDF exceeds page limit ({num_pages} pages; maximum allowed is {max_pages})."
            )

        pages: list[dict[str, Any]] = []
        total_text_bytes = 0
        for page_idx in range(num_pages):
            raw_text = doc.load_page(page_idx).get_text("text") or ""
            if "\x00" in raw_text:
                raw_text = raw_text.replace("\x00", "")
            text_bytes = len(raw_text.encode("utf-8"))
            total_text_bytes += text_bytes
            if total_text_bytes > max_extracted_text_bytes:
                raise PayloadTooLargeError(
                    f"Cumulative extracted text exceeds limit ({total_text_bytes} bytes; "
                    f"maximum allowed is {max_extracted_text_bytes} bytes)."
                )
            pages.append(
                {
                    "page_number": page_idx + 1,
                    "raw_text": raw_text,
                    "character_count": len(raw_text),
                    "word_count": len(raw_text.split()),
                    "has_text": bool(raw_text.strip()),
                }
            )
        pages_with_text = sum(1 for p in pages if p["has_text"])
        return {
            "pages": pages,
            "total_pages": num_pages,
            "pages_with_text": pages_with_text,
            "has_any_text": pages_with_text > 0,
        }
    finally:
        doc.close()


def _attach_stage1_and_stage2_representations(
    result: dict[str, Any],
    *,
    input_path: Path,
    mime_type: str,
    extraction_method: str,
    max_pages: int,
    max_bytes: int,
    enable_conditional_ocr: bool = False,
    render_dir: Path | None = None,
) -> dict[str, Any]:
    """Attach Stage 1 diagnostics, Stage 2 normalized_text, and optional Stage 4 OCR/renders."""
    pages = result.get("pages", [])
    norm_results = normalize_document_pages(pages)

    diag_map: dict[int, dict[str, Any]] = {}
    diag_list: list[dict[str, Any]] = []
    quality_summary: dict[str, Any] = {}
    if mime_type == "application/pdf":
        report = assess_pdf_extraction_quality(
            input_path,
            extracted_result=result,
            max_pages=max_pages,
            max_extracted_text_bytes=max_bytes,
        )
        report_dict = report.to_dict()
        quality_summary = report_dict["summary"]
        diag_list = list(report_dict["pages"])
        diag_map = {int(p["page_number"]): p for p in diag_list}
    else:
        quality_summary = {
            "total_pages": len(pages),
            "pages_with_text": sum(1 for p in pages if p.get("has_text")),
        }

    stage4_map: dict[int, PageStage4Result] = {}
    if mime_type == "application/pdf" and (enable_conditional_ocr or render_dir is not None):
        stage4_map = run_conditional_ocr_and_renders(
            input_path,
            diag_list,
            render_output_dir=render_dir,
        )

    enriched_pages: list[dict[str, Any]] = []
    for idx, page in enumerate(pages):
        pno = int(page["page_number"])
        norm = norm_results[idx]
        diag_payload = dict(diag_map.get(pno, {}))
        diag_payload["normalization_stats"] = norm.stats
        triage_class = str(diag_payload.get("visual_triage_class", "none"))
        has_native_text = bool(diag_payload.get("has_native_text", page.get("has_text", False)))
        has_usable_norm = bool(
            diag_payload.get(
                "has_usable_normalized_text",
                len(norm.normalized_text.strip()) >= 40,
            )
        )
        s4 = stage4_map.get(pno)
        ocr_text = s4.ocr_text if s4 is not None else None
        has_ocr_text = bool(ocr_text and ocr_text.strip())
        ocr_status = (
            s4.ocr_status
            if s4 is not None
            else ("pending" if triage_class in OCR_ELIGIBLE_TRIAGE else "not_needed")
        )
        quality_status = str(diag_payload.get("extraction_quality_status", "pending"))
        if s4 is not None and triage_class != "none":
            if has_ocr_text and s4.rendered_png_filename is None:
                quality_status = "enriched"
            elif has_ocr_text:
                quality_status = "partially_enriched"

        enriched_pages.append(
            {
                **page,
                "normalized_text": norm.normalized_text,
                "normalization_version": NORMALIZATION_VERSION,
                "extraction_method": extraction_method,
                "quality_diagnostics": diag_payload,
                "visual_triage_class": triage_class,
                "has_native_text": has_native_text,
                "has_usable_normalized_text": has_usable_norm,
                "has_ocr_text": has_ocr_text,
                "ocr_text": ocr_text,
                "ocr_confidence": s4.ocr_confidence if s4 is not None else None,
                "ocr_model": s4.ocr_model if s4 is not None else None,
                "ocr_version": s4.ocr_version if s4 is not None else None,
                "ocr_status": ocr_status,
                "ocr_error": s4.ocr_error if s4 is not None else None,
                "rendered_png_filename": s4.rendered_png_filename if s4 is not None else None,
                "has_visual_description": False,
                "visual_status": (
                    "pending" if (s4 is not None and s4.rendered_png_filename) else "not_needed"
                ),
                "extraction_quality_status": quality_status,
            }
        )

    return {
        **result,
        "pages": enriched_pages,
        "quality_summary": quality_summary,
        "extraction_method": extraction_method,
        "normalization_version": NORMALIZATION_VERSION,
    }


def _run_extraction_sync(
    input_path: Path,
    mime_type: str,
    max_pages: int,
    max_bytes: int,
    parser_engine: str = "pypdf",
    enable_conditional_ocr: bool = False,
    render_dir: Path | None = None,
) -> dict[str, Any]:
    """Dispatch file extraction synchronously and attach traceable representations."""
    if mime_type == "application/pdf":
        if parser_engine == "pymupdf":
            raw_res = _extract_pdf_with_pymupdf(
                input_path=input_path,
                max_pages=max_pages,
                max_extracted_text_bytes=max_bytes,
            )
            method = "pymupdf_text"
        else:
            raw_res = extract_pdf_pages(
                file_path=input_path,
                max_pages=max_pages,
                max_extracted_text_bytes=max_bytes,
            )
            method = "pypdf_text"
    elif mime_type == "text/plain":
        raw_res = extract_txt_pages(
            file_path=input_path,
            max_extracted_text_bytes=max_bytes,
        )
        method = "pypdf_text"
    else:
        raise InvalidMimeTypeError(f"Unsupported MIME type: {mime_type}")

    return _attach_stage1_and_stage2_representations(
        raw_res,
        input_path=input_path,
        mime_type=mime_type,
        extraction_method=method,
        max_pages=max_pages,
        max_bytes=max_bytes,
        enable_conditional_ocr=enable_conditional_ocr,
        render_dir=render_dir,
    )


def main() -> None:
    """CLI entry point for isolated parser child process / sandbox container."""
    parser = argparse.ArgumentParser(description="Isolated Document Parser Process")
    parser.add_argument("--input", required=True, help="Path to input document file")
    parser.add_argument("--mime", required=True, help="MIME type of document")
    parser.add_argument("--output", required=True, help="Path to write JSON output")
    parser.add_argument("--max-pages", type=int, default=2000, help="Maximum physical pages")
    parser.add_argument("--max-bytes", type=int, default=26_214_400, help="Maximum text bytes")
    parser.add_argument(
        "--parser-engine",
        choices=("pypdf", "pymupdf"),
        default="pypdf",
        help="PDF parser engine (pymupdf permitted only inside container sandbox)",
    )
    parser.add_argument(
        "--enable-conditional-ocr",
        action="store_true",
        help="Run Stage 4 conditional Tesseract OCR inside sandbox",
    )
    parser.add_argument(
        "--render-dir",
        default=None,
        help="Optional directory inside sandbox to export rendered PNGs of flagged pages",
    )

    args = parser.parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    render_dir = Path(args.render_dir) if args.render_dir else None
    payload: dict[str, Any]

    try:
        result = _run_extraction_sync(
            input_path=input_path,
            mime_type=args.mime,
            max_pages=args.max_pages,
            max_bytes=args.max_bytes,
            parser_engine=args.parser_engine,
            enable_conditional_ocr=args.enable_conditional_ocr,
            render_dir=render_dir,
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
            "error_code": "CORRUPTED_FILE",
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
    output_file: Path | None = None,
    active_procs: set[asyncio.subprocess.Process] | None = None,
    isolation_mode: ParserIsolationMode = "container_preferred",
    parser_engine: ParserEngine = "pypdf",
    sandbox_config: ContainerSandboxConfig | None = None,
    enable_conditional_ocr: bool = False,
    render_output_dir: Path | None = None,
) -> dict[str, Any]:
    """Execute document parsing in a hardened container sandbox (or sanitized subprocess fallback).

    - `container_required`: enforces container isolation and fails closed if Docker/image is missing.
    - `container_preferred`: runs inside Docker sandbox when image is available, falling back to
      env-sanitized OS subprocess when unavailable.
    - `subprocess_legacy`: runs pure-Python pypdf/txt inside an env-stripped host subprocess.
    """
    from app.modules.knowledge_base.parser_sandbox import (
        ContainerSandboxConfig,
        is_sandbox_image_present,
        run_parser_in_container,
    )

    cfg = sandbox_config or ContainerSandboxConfig()

    if isolation_mode in ("container_required", "container_preferred"):
        has_image = await is_sandbox_image_present(cfg.image)
        if has_image:
            return await run_parser_in_container(
                input_path=input_path,
                mime_type=mime_type,
                timeout_seconds=timeout_seconds,
                max_pages=max_pages,
                max_bytes=max_bytes,
                output_file=output_file,
                active_procs=active_procs,
                parser_engine=parser_engine,
                config=cfg,
                error_code_map=ERROR_CODE_MAP,  # type: ignore[arg-type]
                enable_conditional_ocr=enable_conditional_ocr,
                render_output_dir=render_output_dir,
            )
        if isolation_mode == "container_required":
            raise TransientExtractionError(
                f"Container parser sandbox image '{cfg.image}' is not available.",
                code="SANDBOX_UNAVAILABLE",
            )
        logger.info(
            "Parser sandbox image '%s' unavailable locally; using env-sanitized subprocess fallback.",
            cfg.image,
        )

    if parser_engine != "pypdf":
        raise PermanentExtractionError(
            f"Parser engine '{parser_engine}' requires Stage 3 container sandbox isolation.",
            code="SANDBOX_REQUIRED_FOR_ENGINE",
        )
    created_tmp = False
    if output_file is None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, prefix="kb_parser_out_"
        ) as tmp_out:
            output_file = Path(tmp_out.name)
            created_tmp = True

    proc: asyncio.subprocess.Process | None = None

    try:
        # Build sanitized environment excluding all sensitive credentials
        api_root = str(Path(__file__).resolve().parent.parent.parent.parent)
        clean_env = {k: os.environ[k] for k in SAFE_SUBPROCESS_ENV_KEYS if k in os.environ}
        clean_env["PYTHONPATH"] = api_root
        clean_env["PYTHONUNBUFFERED"] = "1"
        clean_env["PYTHONDONTWRITEBYTECODE"] = "1"

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
            env=clean_env,
        )

        if active_procs is not None:
            active_procs.add(proc)

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
            raise CorruptedFileError(
                f"Parser process exited abnormally (code {proc.returncode}): {stderr_msg}"
            )

        try:
            content = output_file.read_text(encoding="utf-8")
            data = json.loads(content)
        except Exception as e:
            raise CorruptedFileError(f"Failed to read parser output payload: {e}") from e

        if data.get("status") == "success":
            result = dict(data["result"])
            result.setdefault("parser_backend", "subprocess_isolated")
            return result

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
        if proc is not None and active_procs is not None:
            active_procs.discard(proc)
        if created_tmp:
            try:
                if output_file.exists():
                    output_file.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    main()
