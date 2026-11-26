"""Unit and sandbox tests for PDF Extraction M4.10 (quality metrics, triage, OCR, VLM, provenance)."""

from __future__ import annotations

import json
import zlib
from pathlib import Path
from uuid import uuid4

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, NumberObject, StreamObject

from app.modules.chat.context.formatter import format_source_block
from app.modules.knowledge_base.chunking import PageInput, chunk_document_pages
from app.modules.knowledge_base.extraction_diagnostics import assess_pdf_extraction_quality
from app.modules.knowledge_base.parser_process import run_parser_isolated
from app.modules.knowledge_base.parser_sandbox import is_sandbox_image_present
from app.modules.knowledge_base.retrieval.schemas import RetrievedChunk
from app.modules.knowledge_base.visual_understanding import (
    compute_page_quality_status,
    enrich_pages_with_visual_understanding,
)


def _add_page(
    writer: PdfWriter,
    lines: list[str],
    *,
    images: list[tuple[int, int]] | None = None,
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
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )

    cmds = ["BT", "/F1 12 Tf", "72 720 Td"]
    for idx, ln in enumerate(lines):
        esc = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if idx > 0:
            cmds.append("0 -16 Td")
        cmds.append(f"({esc}) Tj")
    cmds.append("ET")
    stream_parts = ["\n".join(cmds).encode("latin-1")]

    if images:
        xobjs = DictionaryObject()
        for idx, (w, h) in enumerate(images, start=1):
            img_s = StreamObject()
            img_s.update(
                {
                    NameObject("/Type"): NameObject("/XObject"),
                    NameObject("/Subtype"): NameObject("/Image"),
                    NameObject("/Width"): NumberObject(w),
                    NameObject("/Height"): NumberObject(h),
                    NameObject("/ColorSpace"): NameObject("/DeviceGray"),
                    NameObject("/BitsPerComponent"): NumberObject(8),
                }
            )
            img_s.set_data(b"\x80" * 16)
            ref = writer._add_object(img_s)
            xobjs[NameObject(f"/Im{idx}")] = ref
            stream_parts.append(f"q 100 0 0 100 72 400 cm /Im{idx} Do Q".encode("latin-1"))
        resources[NameObject("/XObject")] = xobjs

    page[NameObject("/Resources")] = resources
    content = StreamObject()
    content.set_data(zlib.compress(b"\n".join(stream_parts)))
    content[NameObject("/Filter")] = NameObject("/FlateDecode")
    page[NameObject("/Contents")] = writer._add_object(content)


def test_mandatory_quality_metrics_and_visual_triage_differentiation(tmp_path: Path) -> None:
    pdf_path = tmp_path / "triage.pdf"
    writer = PdfWriter()

    # p.1: boilerplate / closing only (has_native_text=True, has_usable_normalized_text=False)
    _add_page(writer, ["Thank You !! 1"])
    # p.2: clean prose (text_complete)
    _add_page(
        writer,
        [
            "Binary Decision Diagrams represent canonical Boolean functions cleanly when ordered and reduced.",
            "Merging isomorphic subgraphs eliminates redundant nodes in the canonical representation.",
        ],
    )
    # p.3: table / case slide (table_recovery)
    _add_page(
        writer,
        ["Recursive BDDAPPLY Operation:", "Several cases under consideration :"],
        images=[(1600, 620)],
    )
    # p.4: flowchart slide (flowchart_or_diagram_visual)
    _add_page(
        writer,
        ["High-Level Synthesis Flow :"],
        images=[(1200, 830)],
    )
    # p.5: mixed equations + diagram (mixed_ocr_and_visual)
    _add_page(
        writer,
        ["Decision variable (xi) for node     , value missing below."],
        images=[(2042, 240), (1170, 850)],
    )

    with pdf_path.open("wb") as f:
        writer.write(f)

    report = assess_pdf_extraction_quality(pdf_path)
    p1, p2, p3, p4, p5 = report.pages

    assert p1.has_native_text is True
    assert p1.has_usable_normalized_text is False
    assert p1.visual_triage_class == "none"
    assert p1.extraction_quality_status == "boilerplate_only"

    assert p2.has_native_text is True
    assert p2.has_usable_normalized_text is True
    assert p2.visual_triage_class == "none"
    assert p2.extraction_quality_status == "text_complete"

    assert p3.visual_triage_class == "table_recovery"
    assert p3.extraction_quality_status == "needs_enrichment"

    assert p4.visual_triage_class == "flowchart_or_diagram_visual"
    assert p5.visual_triage_class == "mixed_ocr_and_visual"


_FONT_5X7: dict[str, tuple[str, ...]] = {
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
}


def _make_crisp_raster_pdf(pdf_path: Path, word: str = "HILL") -> None:
    """Create a textless PDF page embedding a clean monochrome raster /Image XObject."""
    scale = 12
    pad = 40
    cols = len(word) * 6 * scale + pad * 2
    rows = 7 * scale + pad * 2
    buf = bytearray([255] * (cols * rows))

    for char_idx, ch in enumerate(word):
        glyph = _FONT_5X7[ch]
        x0 = pad + char_idx * 6 * scale
        for gy, row_bits in enumerate(glyph):
            for gx, bit in enumerate(row_bits):
                if bit == "1":
                    for dy in range(scale):
                        for dx in range(scale):
                            py = pad + gy * scale + dy
                            px = x0 + gx * scale + dx
                            buf[py * cols + px] = 0

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    img_s = StreamObject()
    img_s.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(cols),
            NameObject("/Height"): NumberObject(rows),
            NameObject("/ColorSpace"): NameObject("/DeviceGray"),
            NameObject("/BitsPerComponent"): NumberObject(8),
            NameObject("/Filter"): NameObject("/FlateDecode"),
        }
    )
    img_s.set_data(zlib.compress(bytes(buf)))
    img_ref = writer._add_object(img_s)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/XObject"): DictionaryObject({NameObject("/Im1"): img_ref})}
    )
    content = StreamObject()
    content.set_data(f"q {cols} 0 0 {rows} 40 500 cm /Im1 Do Q".encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(content)
    with pdf_path.open("wb") as f:
        writer.write(f)


@pytest.mark.asyncio
async def test_stage4_container_ocr_recovers_scanned_pdf_inside_sandbox(tmp_path: Path) -> None:
    if not await is_sandbox_image_present("ragbait-parser-sandbox:latest"):
        pytest.skip("ragbait-parser-sandbox:latest not built locally")

    pdf_path = tmp_path / "scanned_image_only.pdf"
    _make_crisp_raster_pdf(pdf_path, "HILL")

    renders_out = tmp_path / "renders"
    res = await run_parser_isolated(
        input_path=pdf_path,
        mime_type="application/pdf",
        isolation_mode="container_required",
        enable_conditional_ocr=True,
        render_output_dir=renders_out,
        timeout_seconds=30.0,
    )
    assert res["parser_backend"] == "container_sandbox"
    p1 = res["pages"][0]
    assert p1["visual_triage_class"] == "scanned_ocr"
    assert p1["ocr_status"] == "completed"
    assert p1["has_ocr_text"] is True
    assert "HILL" in (p1["ocr_text"] or "").upper()
    assert 0.0 <= float(p1["ocr_confidence"]) <= 1.0
    assert p1["raw_text"] == ""  # verbatim native layer preserved untouched


@pytest.mark.asyncio
async def test_stage5_visual_understanding_and_multimodal_provenance(tmp_path: Path) -> None:
    renders_dir = tmp_path / "renders"
    renders_dir.mkdir()
    (renders_dir / "page_0009.png").write_bytes(b"\x89PNG\r\n\x1a\nfakepng")

    async def fake_vision(prompt: str, png: bytes, triage: str) -> tuple[str, str]:
        assert "High-Level Synthesis Flow" in prompt
        assert triage == "flowchart_or_diagram_visual"
        return (
            json.dumps(
                {
                    "visual_summary": "HLS flow converts Behavioral RTL specification into Allocation, Scheduling, and Binding.",
                    "diagram_elements": [
                        "Behavioral Description -> Compilation & Transformation (CDFG)",
                        "CDFG -> Allocation, Scheduling, Binding -> Structural RTL",
                    ],
                    "extracted_equations": [],
                    "structured_table": None,
                    "uncertainty_notes": "Bottom edge arrow label partially low-resolution.",
                    "confidence": 0.88,
                }
            ),
            "google/gemma-4-31b-it:free",
        )

    pages = [
        {
            "page_number": 9,
            "raw_text": "High Level Synthesis (contnd…)\n• High-Level Synthesis Flow :\n9\n",
            "normalized_text": "- High-Level Synthesis Flow :",
            "has_text": True,
            "has_native_text": True,
            "has_usable_normalized_text": False,
            "visual_triage_class": "flowchart_or_diagram_visual",
            "ocr_status": "not_needed",
            "has_ocr_text": False,
            "rendered_png_filename": "page_0009.png",
        }
    ]

    enriched = await enrich_pages_with_visual_understanding(
        pages,
        render_dir=renders_dir,
        vision_caller=fake_vision,
    )
    ep = enriched[0]
    assert ep["raw_text"].startswith("High Level Synthesis (contnd…)")
    assert ep["has_visual_description"] is True
    assert ep["visual_status"] == "completed"
    assert ep["visual_model"] == "google/gemma-4-31b-it:free"
    assert ep["visual_confidence"] == pytest.approx(0.88)
    assert ep["extraction_quality_status"] == "enriched"
    assert "Allocation, Scheduling, Binding" in ep["visual_description"]
    assert "Visual uncertainty:" in ep["visual_description"]

    chunks = chunk_document_pages(
        [
            PageInput(
                page_number=9,
                text=f"{ep['normalized_text']}\n\n[DERIVED VISUAL DESCRIPTION — Page 9]\n{ep['visual_description']}",
                has_text=True,
                source_kind="multimodal_combined",
            )
        ]
    )
    assert len(chunks) == 1
    assert chunks[0].source_kind == "multimodal_combined"

    xml_block = format_source_block(
        "S1",
        RetrievedChunk(
            chunk_id=uuid4(),
            document_id=uuid4(),
            subject_id=uuid4(),
            document_title="High-Level Synthesis",
            raw_text=chunks[0].raw_text,
            page_number_start=9,
            page_number_end=9,
            page_spans=[{"page_number": 9, "start_char": 0, "end_char": 100}],
            content_hash="abc",
            chunk_version=3,
            model_fingerprint="fp",
            rrf_score=0.016,
            final_rank=1,
            source_kind="multimodal_combined",
        ),
    )
    assert "<source_kind>multimodal_combined</source_kind>" in xml_block
    assert "<physical_pages>9</physical_pages>" in xml_block


def test_compute_page_quality_status_transitions() -> None:
    assert (
        compute_page_quality_status(
            has_native_text=True,
            has_usable_normalized_text=False,
            triage_class="mixed_ocr_and_visual",
            ocr_status="completed",
            has_ocr_text=True,
            visual_status="failed",
            has_visual_description=False,
        )
        == "partially_enriched"
    )
    assert (
        compute_page_quality_status(
            has_native_text=False,
            has_usable_normalized_text=False,
            triage_class="scanned_ocr",
            ocr_status="failed",
            has_ocr_text=False,
            visual_status="not_needed",
            has_visual_description=False,
        )
        == "failed_enrichment"
    )
