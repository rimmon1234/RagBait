"""Unit tests for Stage 1 pure-Python PDF extraction quality diagnostics."""

from __future__ import annotations

import zlib
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import (
    DictionaryObject,
    NameObject,
    NumberObject,
    StreamObject,
)

from app.modules.knowledge_base.extraction_diagnostics import assess_pdf_extraction_quality
from app.modules.knowledge_base.pdf_extractor import extract_pdf_pages


def _make_text_page_stream(lines: list[str]) -> bytes:
    cmds = ["BT", "/F1 12 Tf", "72 720 Td"]
    for idx, line in enumerate(lines):
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if idx > 0:
            cmds.append("0 -16 Td")
        cmds.append(f"({escaped}) Tj")
    cmds.append("ET")
    return "\n".join(cmds).encode("latin-1")


def _add_synthetic_page(
    writer: PdfWriter,
    lines: list[str],
    *,
    images: list[tuple[int, int]] | None = None,
    vector_lines: int = 0,
) -> None:
    page = writer.add_blank_page(width=612, height=792)
    font_dict = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font_dict)

    resources = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
        }
    )

    stream_parts = [_make_text_page_stream(lines)]

    if images:
        xobj_dict = DictionaryObject()
        for idx, (width, height) in enumerate(images, start=1):
            img_stream = StreamObject()
            img_stream.update(
                {
                    NameObject("/Type"): NameObject("/XObject"),
                    NameObject("/Subtype"): NameObject("/Image"),
                    NameObject("/Width"): NumberObject(width),
                    NameObject("/Height"): NumberObject(height),
                    NameObject("/ColorSpace"): NameObject("/DeviceGray"),
                    NameObject("/BitsPerComponent"): NumberObject(8),
                }
            )
            img_stream.set_data(b"\x80" * 16)
            img_ref = writer._add_object(img_stream)
            name = f"/Im{idx}"
            xobj_dict[NameObject(name)] = img_ref
            stream_parts.append(f"q 100 0 0 100 72 400 cm {name} Do Q".encode("latin-1"))
        resources[NameObject("/XObject")] = xobj_dict

    if vector_lines > 0:
        vec_cmds = ["q"]
        for i in range(vector_lines):
            y = 200 + i
            vec_cmds.append(f"100 {y} m 300 {y} l S")
        vec_cmds.append("Q")
        stream_parts.append("\n".join(vec_cmds).encode("latin-1"))

    page[NameObject("/Resources")] = resources
    content = StreamObject()
    content.set_data(zlib.compress(b"\n".join(stream_parts)))
    content[NameObject("/Filter")] = NameObject("/FlateDecode")
    content_ref = writer._add_object(content)
    page[NameObject("/Contents")] = content_ref


def test_assess_pdf_extraction_quality_flags_diagrams_gaps_and_preserves_pypdf(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "synthetic_lecture.pdf"
    writer = PdfWriter()

    # p.1: clean title slide
    _add_synthetic_page(
        writer,
        ["Logic Synthesis Course", "Lecture 1"],
    )
    # p.2: clean prose + repeated header
    _add_synthetic_page(
        writer,
        [
            "Lecture Notes Header",
            "Binary Decision Diagrams represent canonical Boolean functions when ordered and reduced.",
            "Reduction applies two rules: merge isomorphic subgraphs and eliminate redundant nodes.",
            "This detailed explanation covers the complete theoretical foundation without any visual omissions or missing symbols in text.",
        ],
    )
    # p.3: diagram-only slide (low text + large raster image) + repeated header
    _add_synthetic_page(
        writer,
        ["Lecture Notes Header", "ROBDD Tree Derivation :"],
        images=[(1344, 744)],
    )
    # p.4: deceptive >=120 char slide with equation strip + inline blank symbol gap + repeated header
    _add_synthetic_page(
        writer,
        [
            "Lecture Notes Header",
            "Consider an n-level Binary Tree representing an arbitrary Boolean function in logic synthesis.",
            "Two children define the recursion structure cleanly for hardware verification:",
            "Decision variable for node     , value attribute missing below.",
            "Extra detailed explanatory prose padding to ensure total character count clears the heuristic threshold cleanly.",
        ],
        images=[(2042, 245)],
    )
    # p.5: vector flowchart slide without raster images (path_operator_count >= 30)
    _add_synthetic_page(
        writer,
        [
            "Lecture Notes Header",
            "Synthesis Flow Chart Diagram",
        ],
        vector_lines=40,
    )
    # p.6: prose slide ending with inline page number bleed (6)
    _add_synthetic_page(
        writer,
        [
            "Lecture Notes Header",
            "Allocation determines functional unit counts before scheduling begins across control steps.",
            "Scheduling binds every operation in the sequencing graph to a discrete clock cycle.",
            "Resource sharing assigns distinct hardware operations to shared functional units 6",
        ],
    )
    # p.7: closing slide
    _add_synthetic_page(writer, ["Thank You !! 7"])

    with pdf_path.open("wb") as f:
        writer.write(f)

    # Confirm baseline extract_pdf_pages behavior works identically on same file
    baseline = extract_pdf_pages(pdf_path)
    report = assess_pdf_extraction_quality(
        pdf_path, extracted_result=baseline, header_min_page_ratio=0.4
    )

    assert report.total_pages == 7
    assert "Lecture Notes Header" in report.repeated_headers
    assert report.pages_with_images == 2
    assert report.pages_with_vector_diagrams == 1
    assert report.pages_with_symbol_gaps == 1

    p1, p2, p3, p4, p5, p6, p7 = report.pages
    assert p1.is_title_or_closing_slide is True
    assert p1.suspected_information_loss is False
    assert p1.recommended_modality == "title_or_closing"

    assert p2.repeated_header_detected is True
    assert p2.suspected_information_loss is False
    assert p2.recommended_modality == "normalize_boilerplate"

    assert p3.significant_image_count == 1
    assert "low_text" in p3.flags
    assert "has_raster_images" in p3.flags
    assert p3.suspected_information_loss is True
    assert p3.recommended_modality == "needs_visual_understanding"

    # p.4 clears >= 120 chars yet is caught via equation strips + blank symbol gap!
    assert p4.character_count > 120
    assert p4.equation_strip_count == 1
    assert p4.blank_gap_run_count >= 1
    assert "missing_equation_or_symbol_gaps" in p4.flags
    assert p4.suspected_information_loss is True
    assert p4.recommended_modality == "needs_visual_understanding"

    # p.5 has vector flowchart without raster images
    assert p5.image_count == 0
    assert p5.path_operator_count >= 40
    assert "has_vector_diagrams" in p5.flags
    assert p5.suspected_information_loss is True
    assert p5.recommended_modality == "needs_visual_understanding"

    assert p6.trailing_page_number_bleed is True
    assert p7.is_title_or_closing_slide is True
    assert p7.recommended_modality == "title_or_closing"


def test_diagnostics_handles_inline_font_stream_without_tounicode_cleanly(tmp_path: Path) -> None:
    """Smoke-check dictionary/serialized report contract stays JSON-friendly."""
    pdf_path = tmp_path / "empty_scan.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    # Attach a dummy image with no text layer (scanned page)
    img_stream = StreamObject()
    img_stream.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(1200),
            NameObject("/Height"): NumberObject(1600),
            NameObject("/ColorSpace"): NameObject("/DeviceGray"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    img_stream.set_data(b"\x80" * 16)
    img_ref = writer._add_object(img_stream)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/XObject"): DictionaryObject({NameObject("/Im1"): img_ref})}
    )
    content = StreamObject()
    content.set_data(b"q 612 0 0 792 0 0 cm /Im1 Do Q")
    page[NameObject("/Contents")] = writer._add_object(content)

    with pdf_path.open("wb") as f:
        writer.write(f)

    report = assess_pdf_extraction_quality(pdf_path)
    as_dict = report.to_dict()
    assert as_dict["summary"]["total_pages"] == 1
    assert as_dict["summary"]["pages_suspected_info_loss"] == 1
    assert as_dict["pages"][0]["recommended_modality"] == "needs_ocr"
