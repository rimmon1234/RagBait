"""Stage 4 containerized conditional OCR and flagged-page PNG rendering.

Executes strictly inside the isolated parser container (`--network=none`,
`--read-only`, UID 65532):
- Renders ONLY pages flagged by `visual_triage_class != 'none'` using PyMuPDF.
- Runs Tesseract OCR ONLY on triage classes requiring pixel text/equation/table OCR
  (`scanned_ocr`, `math_equation_ocr`, `table_recovery`, `mixed_ocr_and_visual`).
- Exports rendered PNGs for visual/table/equation/mixed pages to a bounded output
  directory so trusted host workers can run Stage 5 vision models without exposing
  network or secrets to the untrusted PDF parser container.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

OCR_ELIGIBLE_TRIAGE: frozenset[str] = frozenset(
    {
        "scanned_ocr",
        "math_equation_ocr",
        "table_recovery",
        "mixed_ocr_and_visual",
    }
)
RENDER_ELIGIBLE_TRIAGE: frozenset[str] = frozenset(
    {
        "math_equation_ocr",
        "flowchart_or_diagram_visual",
        "table_recovery",
        "mixed_ocr_and_visual",
    }
)


@dataclass(frozen=True)
class PageStage4Result:
    """Conditional OCR output and optional rendered PNG path for one page."""

    page_number: int
    ocr_text: str | None
    ocr_confidence: float | None
    ocr_model: str | None
    ocr_version: str | None
    ocr_status: str
    ocr_error: str | None
    rendered_png_filename: str | None


def run_conditional_ocr_and_renders(
    pdf_path: Path,
    page_diagnostics: list[dict[str, Any]],
    *,
    render_output_dir: Path | None = None,
    dpi: int = 200,
    max_rendered_pages: int = 120,
) -> dict[int, PageStage4Result]:
    """Render flagged PDF pages and run Tesseract OCR where triage requires it."""
    results: dict[int, PageStage4Result] = {}

    flagged_pages = [
        d for d in page_diagnostics if str(d.get("visual_triage_class", "none")) != "none"
    ]
    if not flagged_pages:
        return results

    try:
        import fitz
    except ImportError as exc:
        for d in flagged_pages:
            pno = int(d["page_number"])
            triage = str(d.get("visual_triage_class", "none"))
            if triage in OCR_ELIGIBLE_TRIAGE:
                results[pno] = PageStage4Result(
                    page_number=pno,
                    ocr_text=None,
                    ocr_confidence=None,
                    ocr_model=None,
                    ocr_version=None,
                    ocr_status="skipped",
                    ocr_error=f"Stage 4 sandbox renderer unavailable: {exc}",
                    rendered_png_filename=None,
                )
        return results

    tesseract_available = False
    tesseract_ver: str | None = None
    pytesseract_mod: Any = None
    pil_image_mod: Any = None
    try:
        import pytesseract
        from PIL import Image

        pytesseract_mod = pytesseract
        pil_image_mod = Image
        tesseract_ver = str(pytesseract.get_tesseract_version())
        tesseract_available = True
    except Exception:
        pass

    if render_output_dir is not None:
        render_output_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(pdf_path))
    rendered_count = 0
    try:
        for d in flagged_pages:
            pno = int(d["page_number"])
            triage = str(d.get("visual_triage_class", "none"))
            need_ocr = triage in OCR_ELIGIBLE_TRIAGE
            need_render = render_output_dir is not None and triage in RENDER_ELIGIBLE_TRIAGE

            if not need_ocr and not need_render:
                continue

            if pno < 1 or pno > doc.page_count:
                continue

            png_filename: str | None = None
            ocr_text: str | None = None
            ocr_confidence: float | None = None
            ocr_status = "not_needed"
            ocr_error: str | None = None

            try:
                page = doc.load_page(pno - 1)
                pix = page.get_pixmap(dpi=dpi, alpha=False)

                if (
                    need_render
                    and render_output_dir is not None
                    and rendered_count < max_rendered_pages
                ):
                    png_filename = f"page_{pno:04d}.png"
                    pix.save(str(render_output_dir / png_filename))
                    rendered_count += 1

                if need_ocr:
                    if not tesseract_available or pytesseract_mod is None or pil_image_mod is None:
                        ocr_status = "skipped"
                        ocr_error = "Tesseract OCR binary not available in parser runtime."
                    else:
                        img = pil_image_mod.frombytes("RGB", (pix.width, pix.height), pix.samples)
                        # --psm 6 preserves block/table line grouping cleanly
                        data = pytesseract_mod.image_to_data(
                            img,
                            lang="eng",
                            config="--psm 6",
                            output_type=pytesseract_mod.Output.DICT,
                        )
                        text_str = pytesseract_mod.image_to_string(
                            img,
                            lang="eng",
                            config="--psm 6",
                        )
                        confs = [
                            float(c)
                            for c, tok in zip(
                                data.get("conf", []), data.get("text", []), strict=False
                            )
                            if str(tok).strip() and str(c) not in ("-1", "")
                        ]
                        mean_conf = (
                            round(max(0.0, min(1.0, (sum(confs) / len(confs)) / 100.0)), 4)
                            if confs
                            else 0.0
                        )
                        cleaned_ocr = text_str.strip()
                        ocr_text = cleaned_ocr if cleaned_ocr else None
                        ocr_confidence = mean_conf if cleaned_ocr else 0.0
                        ocr_status = "completed"
            except Exception as exc:
                if need_ocr:
                    ocr_status = "failed"
                    ocr_error = f"OCR failure on page {pno}: {exc}"

            results[pno] = PageStage4Result(
                page_number=pno,
                ocr_text=ocr_text,
                ocr_confidence=ocr_confidence,
                ocr_model="tesseract" if need_ocr and ocr_status == "completed" else None,
                ocr_version=tesseract_ver if need_ocr and ocr_status == "completed" else None,
                ocr_status=ocr_status,
                ocr_error=ocr_error,
                rendered_png_filename=png_filename,
            )
    finally:
        doc.close()

    return results
