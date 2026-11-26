"""Stage 1 read-only page-level extraction quality diagnostics for PDFs.

Inspects native PDF structure (raster Image XObjects, Form XObjects, vector
drawing operators, font ToUnicode completeness) alongside pypdf-extracted page
text (blank equation gaps, orphan delimiters, repeating slide headers/footers,
inline page-number bleed, PUA/encoding artifacts) without modifying or
overwriting the existing pypdf extraction contract.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pypdf import PdfReader
from pypdf.generic import ContentStream

from app.modules.knowledge_base.extractor_exceptions import (
    CorruptedFileError,
    EncryptedPdfError,
)
from app.modules.knowledge_base.pdf_extractor import extract_pdf_pages

RecommendedModality = Literal[
    "text_only",
    "normalize_boilerplate",
    "needs_ocr",
    "needs_visual_understanding",
    "title_or_closing",
]
VisualTriageClass = Literal[
    "none",
    "scanned_ocr",
    "math_equation_ocr",
    "flowchart_or_diagram_visual",
    "table_recovery",
    "mixed_ocr_and_visual",
]
ExtractionQualityStatus = Literal[
    "text_complete",
    "boilerplate_only",
    "needs_enrichment",
    "empty",
]

_VECTOR_DRAW_OPS: frozenset[bytes] = frozenset({b"m", b"l", b"c", b"v", b"y", b"re"})
_CID_TOKEN_RE = re.compile(r"\bcid:\d+\b", re.IGNORECASE)
_ORPHAN_DELIMITER_LINE_RE = re.compile(r"[:=→]\s*$")
_INLINE_MISSING_TOKEN_GAP_RE = re.compile(r"\S {3,}[,=→)]|\bnode {2,},", re.IGNORECASE)
_CLOSING_SLIDE_RE = re.compile(
    r"^\s*(thank\s+you|thanks|questions\s*\??|q\s*&\s*a)\b", re.IGNORECASE
)
_CONTENTS_HEADER_RE = re.compile(r"^\s*(contents|outline|agenda|syllabus)\b", re.IGNORECASE)
_TABLE_HINT_RE = re.compile(
    r"\b(?:cases\s+under\s+consideration|truth\s+table|table|bddapply|comparison|matrix)\b",
    re.IGNORECASE,
)
_PUA_AND_BULLET_RE = re.compile(r"[\ue000-\uf8ff❖➢]")
_PAGE_NUMBER_LINE_RE = re.compile(r"^\d{1,4}$")


@dataclass(frozen=True)
class ImageXObjectInfo:
    """Metadata of an embedded raster /Image XObject without pixel decoding."""

    name: str
    width: int
    height: int

    @property
    def pixel_area(self) -> int:
        return self.width * self.height

    @property
    def is_significant(self) -> bool:
        """True when large enough to be a figure/equation rather than a tiny icon."""
        return self.width >= 48 and self.height >= 48

    @property
    def is_equation_strip(self) -> bool:
        """Heuristic for wide horizontal equation/expression snips pasted onto slides."""
        return self.is_significant and self.width >= 3 * max(1, self.height) and self.height <= 450


@dataclass(frozen=True)
class PageQualityDiagnostics:
    """Comprehensive Stage 1 quality diagnostic profile for one physical PDF page."""

    page_number: int
    character_count: int
    word_count: int
    line_count: int
    non_boilerplate_char_count: int
    has_text: bool
    image_count: int
    significant_image_count: int
    equation_strip_count: int
    max_image_pixels: int
    form_xobject_count: int
    path_operator_count: int
    font_count: int
    fonts_without_tounicode: int
    replacement_char_count: int
    cid_token_count: int
    pua_bullet_count: int
    orphan_delimiter_count: int
    blank_gap_run_count: int
    repeated_header_detected: bool
    repeated_footer_detected: bool
    trailing_page_number_bleed: bool
    is_title_or_closing_slide: bool
    flags: tuple[str, ...]
    recommended_modality: RecommendedModality
    visual_triage_class: VisualTriageClass
    has_native_text: bool
    has_usable_normalized_text: bool
    extraction_quality_status: ExtractionQualityStatus
    suspected_information_loss: bool

    def to_dict(self) -> dict[str, Any]:
        """Serialize diagnostics into a JSON-compatible dictionary."""
        return {
            "page_number": self.page_number,
            "character_count": self.character_count,
            "word_count": self.word_count,
            "line_count": self.line_count,
            "non_boilerplate_char_count": self.non_boilerplate_char_count,
            "has_text": self.has_text,
            "image_count": self.image_count,
            "significant_image_count": self.significant_image_count,
            "equation_strip_count": self.equation_strip_count,
            "max_image_pixels": self.max_image_pixels,
            "form_xobject_count": self.form_xobject_count,
            "path_operator_count": self.path_operator_count,
            "font_count": self.font_count,
            "fonts_without_tounicode": self.fonts_without_tounicode,
            "replacement_char_count": self.replacement_char_count,
            "cid_token_count": self.cid_token_count,
            "pua_bullet_count": self.pua_bullet_count,
            "orphan_delimiter_count": self.orphan_delimiter_count,
            "blank_gap_run_count": self.blank_gap_run_count,
            "repeated_header_detected": self.repeated_header_detected,
            "repeated_footer_detected": self.repeated_footer_detected,
            "trailing_page_number_bleed": self.trailing_page_number_bleed,
            "is_title_or_closing_slide": self.is_title_or_closing_slide,
            "flags": list(self.flags),
            "recommended_modality": self.recommended_modality,
            "visual_triage_class": self.visual_triage_class,
            "has_native_text": self.has_native_text,
            "has_usable_normalized_text": self.has_usable_normalized_text,
            "extraction_quality_status": self.extraction_quality_status,
            "suspected_information_loss": self.suspected_information_loss,
        }


@dataclass(frozen=True)
class DocumentQualityReport:
    """Aggregated Stage 1 extraction quality report across an entire PDF document."""

    total_pages: int
    pages_with_text: int
    pages_with_usable_text: int
    low_text_pages: int
    pages_with_images: int
    pages_with_vector_diagrams: int
    pages_with_symbol_gaps: int
    pages_with_malformed_encoding: int
    pages_with_boilerplate: int
    pages_suspected_info_loss: int
    triage_breakdown: dict[str, int]
    repeated_headers: tuple[str, ...]
    repeated_footers: tuple[str, ...]
    pages: tuple[PageQualityDiagnostics, ...]

    def to_dict(self) -> dict[str, Any]:
        """Serialize summary and per-page diagnostics to JSON-compatible dicts."""
        return {
            "summary": {
                "total_pages": self.total_pages,
                "pages_with_text": self.pages_with_text,
                "pages_with_usable_text": self.pages_with_usable_text,
                "low_text_pages": self.low_text_pages,
                "pages_with_images": self.pages_with_images,
                "pages_with_vector_diagrams": self.pages_with_vector_diagrams,
                "pages_with_symbol_gaps": self.pages_with_symbol_gaps,
                "pages_with_malformed_encoding": self.pages_with_malformed_encoding,
                "pages_with_boilerplate": self.pages_with_boilerplate,
                "pages_suspected_info_loss": self.pages_suspected_info_loss,
                "triage_breakdown": dict(self.triage_breakdown),
                "repeated_headers": list(self.repeated_headers),
                "repeated_footers": list(self.repeated_footers),
            },
            "pages": [page.to_dict() for page in self.pages],
        }


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _collect_resources_signals(page: Any, reader: PdfReader) -> dict[str, Any]:
    """Inspect XObjects (raster images + Form XObjects), fonts, and path operators."""
    images: list[ImageXObjectInfo] = []
    form_count = 0
    font_total = 0
    fonts_without_tounicode = 0
    seen_ids: set[int] = set()

    def _walk_resources(resources: Any) -> None:
        nonlocal form_count, font_total, fonts_without_tounicode
        if not resources or not hasattr(resources, "get"):
            return

        fonts = resources.get("/Font")
        if hasattr(fonts, "get_object"):
            fonts = fonts.get_object()
        if hasattr(fonts, "keys"):
            for fk in fonts.keys():
                try:
                    font_ref = fonts[fk]
                    font_obj = (
                        font_ref.get_object() if hasattr(font_ref, "get_object") else font_ref
                    )
                    obj_id = id(font_obj)
                    if obj_id in seen_ids:
                        continue
                    seen_ids.add(obj_id)
                    font_total += 1
                    if hasattr(font_obj, "get") and "/ToUnicode" not in font_obj:
                        # Standard-14 Type1 fonts without /ToUnicode or custom symbol fonts
                        fonts_without_tounicode += 1
                except Exception:
                    continue

        xobjs = resources.get("/XObject")
        if hasattr(xobjs, "get_object"):
            xobjs = xobjs.get_object()
        if hasattr(xobjs, "keys"):
            for key in xobjs.keys():
                try:
                    raw_ref = xobjs[key]
                    obj = raw_ref.get_object() if hasattr(raw_ref, "get_object") else raw_ref
                    subtype = str(obj.get("/Subtype", "")) if hasattr(obj, "get") else ""
                    if subtype == "/Image":
                        images.append(
                            ImageXObjectInfo(
                                name=str(key),
                                width=_safe_int(obj.get("/Width", 0)),
                                height=_safe_int(obj.get("/Height", 0)),
                            )
                        )
                    elif subtype == "/Form":
                        obj_id = id(obj)
                        if obj_id in seen_ids:
                            continue
                        seen_ids.add(obj_id)
                        form_count += 1
                        nested_res = obj.get("/Resources") if hasattr(obj, "get") else None
                        _walk_resources(nested_res)
                except Exception:
                    continue

    try:
        res = page.get("/Resources")
        if hasattr(res, "get_object"):
            res = res.get_object()
        _walk_resources(res)
    except Exception:
        pass

    path_ops = 0
    try:
        contents = page.get_contents()
        if contents is not None:
            cs = ContentStream(contents, reader)
            for _, op in cs.operations:
                if op in _VECTOR_DRAW_OPS:
                    path_ops += 1
    except Exception:
        pass

    return {
        "images": images,
        "form_xobject_count": form_count,
        "path_operator_count": path_ops,
        "font_count": font_total,
        "fonts_without_tounicode": fonts_without_tounicode,
    }


def _detect_repeated_edge_lines(
    page_texts: list[str],
    *,
    min_ratio: float,
) -> tuple[set[str], set[str]]:
    """Identify header (first line) and footer (last non-number line) strings repeated across pages."""
    if len(page_texts) < 3:
        return set(), set()

    threshold = max(3, int(len(page_texts) * min_ratio))
    first_counts: Counter[str] = Counter()
    last_counts: Counter[str] = Counter()

    for raw in page_texts:
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if not lines:
            continue
        first = lines[0]
        if not _PAGE_NUMBER_LINE_RE.match(first):
            first_counts[first] += 1
        # Check last line (ignoring standalone page number line right below it)
        last_idx = len(lines) - 1
        if last_idx > 0 and _PAGE_NUMBER_LINE_RE.match(lines[last_idx]):
            last_idx -= 1
        last = lines[last_idx]
        if last_idx > 0 and not _PAGE_NUMBER_LINE_RE.match(last):
            last_counts[last] += 1

    headers = {k for k, v in first_counts.items() if v >= threshold}
    footers = {k for k, v in last_counts.items() if v >= threshold}
    return headers, footers


def _diagnose_single_page(
    *,
    page_number: int,
    raw_text: str,
    structural: dict[str, Any],
    repeated_headers: set[str],
    repeated_footers: set[str],
) -> PageQualityDiagnostics:
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    char_count = len(raw_text)
    word_count = len(raw_text.split())
    line_count = len(lines)
    has_text = bool(raw_text.strip())

    repeated_header = bool(lines and lines[0] in repeated_headers)
    repeated_footer = False
    for idx in range(len(lines) - 1, -1, -1):
        if _PAGE_NUMBER_LINE_RE.match(lines[idx]):
            continue
        repeated_footer = lines[idx] in repeated_footers
        break

    trailing_page_number_bleed = False
    if lines:
        last_line = lines[-1]
        # e.g. "are not executed at the same time 18" where non-digit prose ends with slide page_number
        if not _PAGE_NUMBER_LINE_RE.match(last_line) and re.search(
            rf"\s+{page_number}$", last_line
        ):
            trailing_page_number_bleed = True

    content_lines = list(lines)
    if repeated_header and content_lines:
        content_lines = content_lines[1:]
    if content_lines and _PAGE_NUMBER_LINE_RE.match(content_lines[-1]):
        content_lines = content_lines[:-1]
    if repeated_footer and content_lines and content_lines[-1] in repeated_footers:
        content_lines = content_lines[:-1]

    non_boilerplate_text = "\n".join(content_lines)
    non_boilerplate_char_count = len(non_boilerplate_text)

    images: list[ImageXObjectInfo] = structural["images"]
    image_count = len(images)
    sig_images = [img for img in images if img.is_significant]
    eq_strips = [img for img in images if img.is_equation_strip]
    max_image_pixels = max((img.pixel_area for img in images), default=0)
    form_count = structural["form_xobject_count"]
    path_ops = structural["path_operator_count"]
    font_count = structural["font_count"]
    fonts_without_tounicode = structural["fonts_without_tounicode"]

    replacement_char_count = raw_text.count("\ufffd") + raw_text.count("\x00")
    cid_token_count = len(_CID_TOKEN_RE.findall(raw_text))
    pua_bullet_count = len(_PUA_AND_BULLET_RE.findall(raw_text))

    orphan_delimiter_count = 0
    for idx, ln in enumerate(content_lines):
        if not _ORPHAN_DELIMITER_LINE_RE.search(ln):
            continue
        # Only flag if next line looks empty, a bullet symbol only, or another heading ending with ':'
        # (indicating the value/formula after ':' was likely visual/missing) OR there are large images
        next_ln = content_lines[idx + 1] if idx + 1 < len(content_lines) else ""
        if not next_ln or next_ln in ("•", "-", "❖") or sig_images:
            # Avoid false positive on normal bullet intro line if followed by plain prose without images
            if (
                not next_ln
                or next_ln in ("•", "-", "❖")
                or _ORPHAN_DELIMITER_LINE_RE.search(next_ln)
            ):
                orphan_delimiter_count += 1

    blank_gap_run_count = len(_INLINE_MISSING_TOKEN_GAP_RE.findall(raw_text))

    is_title_or_closing = False
    if image_count == 0 and path_ops < 25:
        if any(_CLOSING_SLIDE_RE.match(ln) for ln in content_lines):
            is_title_or_closing = True
        elif page_number <= 2 and non_boilerplate_char_count < 160:
            is_title_or_closing = True
        elif any(_CONTENTS_HEADER_RE.match(ln) for ln in lines[:2]) and line_count <= 5:
            is_title_or_closing = True

    flags: list[str] = []
    if not has_text or char_count < 40:
        flags.append("empty_or_near_empty")
    elif non_boilerplate_char_count < 120 or char_count < 120:
        flags.append("low_text")

    if len(sig_images) > 0:
        flags.append("has_raster_images")
    if len(eq_strips) > 0:
        flags.append("has_equation_strips")
    if path_ops >= 30 or form_count > 0:
        flags.append("has_vector_diagrams")
    if orphan_delimiter_count >= 2 or blank_gap_run_count >= 1:
        flags.append("missing_equation_or_symbol_gaps")
    if (
        replacement_char_count > 0
        or cid_token_count > 0
        or (font_count > 0 and fonts_without_tounicode == font_count and not has_text)
    ):
        flags.append("malformed_encoding")
    if repeated_header or repeated_footer:
        flags.append("repeated_header_boilerplate")
    if trailing_page_number_bleed:
        flags.append("trailing_page_number_bleed")
    if is_title_or_closing:
        flags.append("title_or_closing_slide")

    suspected_info_loss = False
    if not is_title_or_closing:
        if (
            not has_text
            or "empty_or_near_empty" in flags
            or "low_text" in flags
            or "has_raster_images" in flags
            or "has_vector_diagrams" in flags
            or "missing_equation_or_symbol_gaps" in flags
            or "malformed_encoding" in flags
        ):
            suspected_info_loss = True

    if is_title_or_closing and not suspected_info_loss:
        modality: RecommendedModality = "title_or_closing"
    elif "malformed_encoding" in flags or (
        not has_text and len(sig_images) == 1 and path_ops < 10 and char_count == 0
    ):
        modality = "needs_ocr"
    elif (
        len(sig_images) > 0
        or "has_vector_diagrams" in flags
        or "missing_equation_or_symbol_gaps" in flags
        or (not is_title_or_closing and ("empty_or_near_empty" in flags or "low_text" in flags))
    ):
        modality = "needs_visual_understanding"
    elif "repeated_header_boilerplate" in flags or "trailing_page_number_bleed" in flags:
        modality = "normalize_boilerplate"
    else:
        modality = "text_only"

    # Mandatory M4.10 per-page visual triage classification
    has_eq_signals = len(eq_strips) > 0 or "missing_equation_or_symbol_gaps" in flags
    non_strip_images = max(0, len(sig_images) - len(eq_strips))
    has_table_cues = bool(_TABLE_HINT_RE.search(non_boilerplate_text))

    if not suspected_info_loss:
        triage: VisualTriageClass = "none"
    elif modality == "needs_ocr" or (not has_text and len(sig_images) >= 1 and not has_table_cues):
        triage = "scanned_ocr"
    elif has_table_cues and (len(sig_images) > 0 or "has_vector_diagrams" in flags):
        triage = "table_recovery"
    elif has_eq_signals and (non_strip_images > 0 or path_ops >= 30):
        triage = "mixed_ocr_and_visual"
    elif has_eq_signals:
        triage = "math_equation_ocr"
    else:
        triage = "flowchart_or_diagram_visual"

    has_native_text = bool(has_text)
    has_usable_normalized_text = bool(non_boilerplate_char_count >= 40 and not is_title_or_closing)
    if triage != "none":
        quality_status: ExtractionQualityStatus = "needs_enrichment"
    elif has_usable_normalized_text:
        quality_status = "text_complete"
    elif has_native_text:
        quality_status = "boilerplate_only"
    else:
        quality_status = "empty"

    return PageQualityDiagnostics(
        page_number=page_number,
        character_count=char_count,
        word_count=word_count,
        line_count=line_count,
        non_boilerplate_char_count=non_boilerplate_char_count,
        has_text=has_text,
        image_count=image_count,
        significant_image_count=len(sig_images),
        equation_strip_count=len(eq_strips),
        max_image_pixels=max_image_pixels,
        form_xobject_count=form_count,
        path_operator_count=path_ops,
        font_count=font_count,
        fonts_without_tounicode=fonts_without_tounicode,
        replacement_char_count=replacement_char_count,
        cid_token_count=cid_token_count,
        pua_bullet_count=pua_bullet_count,
        orphan_delimiter_count=orphan_delimiter_count,
        blank_gap_run_count=blank_gap_run_count,
        repeated_header_detected=repeated_header,
        repeated_footer_detected=repeated_footer,
        trailing_page_number_bleed=trailing_page_number_bleed,
        is_title_or_closing_slide=is_title_or_closing,
        flags=tuple(flags),
        recommended_modality=modality,
        visual_triage_class=triage,
        has_native_text=has_native_text,
        has_usable_normalized_text=has_usable_normalized_text,
        extraction_quality_status=quality_status,
        suspected_information_loss=suspected_info_loss,
    )


def assess_pdf_extraction_quality(
    file_path: Path | str,
    *,
    extracted_result: dict[str, Any] | None = None,
    max_pages: int = 2000,
    max_extracted_text_bytes: int = 26_214_400,
    header_min_page_ratio: float = 0.25,
) -> DocumentQualityReport:
    """Assess per-page extraction quality across native PDF structure and pypdf text.

    Reuses `extract_pdf_pages()` by default so the baseline extraction output
    remains completely unchanged.
    """
    path_obj = Path(file_path)
    if not path_obj.exists() or not path_obj.is_file():
        raise CorruptedFileError(f"Target file does not exist: {file_path}")

    baseline = (
        extracted_result
        if extracted_result is not None
        else extract_pdf_pages(
            path_obj,
            max_pages=max_pages,
            max_extracted_text_bytes=max_extracted_text_bytes,
        )
    )

    try:
        reader = PdfReader(str(path_obj))
    except Exception as exc:
        raise CorruptedFileError(f"Failed to read PDF structure for diagnostics: {exc}") from exc

    if reader.is_encrypted:
        raise EncryptedPdfError("PDF file is password protected or encrypted.")

    page_records: list[dict[str, Any]] = list(baseline.get("pages", []))
    raw_texts = [str(p.get("raw_text", "")) for p in page_records]
    repeated_headers, repeated_footers = _detect_repeated_edge_lines(
        raw_texts,
        min_ratio=header_min_page_ratio,
    )

    diagnosed_pages: list[PageQualityDiagnostics] = []
    for idx, page_rec in enumerate(page_records):
        page_num = int(page_rec["page_number"])
        pdf_page = reader.pages[idx] if idx < len(reader.pages) else None
        structural = (
            _collect_resources_signals(pdf_page, reader)
            if pdf_page is not None
            else {
                "images": [],
                "form_xobject_count": 0,
                "path_operator_count": 0,
                "font_count": 0,
                "fonts_without_tounicode": 0,
            }
        )
        diag = _diagnose_single_page(
            page_number=page_num,
            raw_text=str(page_rec.get("raw_text", "")),
            structural=structural,
            repeated_headers=repeated_headers,
            repeated_footers=repeated_footers,
        )
        diagnosed_pages.append(diag)

    triage_counts: dict[str, int] = {}
    for p in diagnosed_pages:
        triage_counts[p.visual_triage_class] = triage_counts.get(p.visual_triage_class, 0) + 1

    return DocumentQualityReport(
        total_pages=len(diagnosed_pages),
        pages_with_text=sum(1 for p in diagnosed_pages if p.has_text),
        pages_with_usable_text=sum(1 for p in diagnosed_pages if p.has_usable_normalized_text),
        low_text_pages=sum(
            1
            for p in diagnosed_pages
            if ("low_text" in p.flags or "empty_or_near_empty" in p.flags)
            and not p.is_title_or_closing_slide
        ),
        pages_with_images=sum(1 for p in diagnosed_pages if p.significant_image_count > 0),
        pages_with_vector_diagrams=sum(
            1 for p in diagnosed_pages if "has_vector_diagrams" in p.flags
        ),
        pages_with_symbol_gaps=sum(
            1 for p in diagnosed_pages if "missing_equation_or_symbol_gaps" in p.flags
        ),
        pages_with_malformed_encoding=sum(
            1 for p in diagnosed_pages if "malformed_encoding" in p.flags
        ),
        pages_with_boilerplate=sum(
            1
            for p in diagnosed_pages
            if p.repeated_header_detected
            or p.repeated_footer_detected
            or p.trailing_page_number_bleed
        ),
        pages_suspected_info_loss=sum(1 for p in diagnosed_pages if p.suspected_information_loss),
        triage_breakdown=triage_counts,
        repeated_headers=tuple(sorted(repeated_headers)),
        repeated_footers=tuple(sorted(repeated_footers)),
        pages=tuple(diagnosed_pages),
    )
