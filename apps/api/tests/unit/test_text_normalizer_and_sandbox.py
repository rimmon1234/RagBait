"""Unit tests for Stage 2 equation-safe normalization and Stage 3 parser sandbox."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.modules.knowledge_base.extractor_exceptions import (
    PermanentExtractionError,
    TransientExtractionError,
)
from app.modules.knowledge_base.parser_process import run_parser_isolated
from app.modules.knowledge_base.parser_sandbox import (
    ContainerSandboxConfig,
    build_docker_sandbox_command,
)
from app.modules.knowledge_base.text_normalizer import (
    normalize_document_pages,
    normalize_page_text,
)


def test_normalize_page_preserves_math_notation_scripts_and_tables() -> None:
    raw = (
        "Logic Synthesis (contnd…)\n"
        "• Reduction of OBDD:\n"
        "❖ Represent Boolean function f(x₁, x₂) = x₁ ∧ x₂ ⊕ x² − x³\n"
        "  0   1   0   1\n"
        "  1   0   1   1\n"
        "  Verify x -\ny subtraction stays separate while algo-\nrithm rejoins cleanly.\n"
        "  ﬁnding ﬂow opti-\nmization for step 4\n"
        "  এবং অ্যালগরিদমের সম্পূর্ণ বর্ণনা।\n"
        "4\n"
    )
    res = normalize_page_text(
        raw,
        page_number=4,
        repeated_headers={"Logic Synthesis (contnd…)"},
        repeated_footers=set(),
    )
    norm = res.normalized_text

    # Boilerplate header & trailing standalone page number removed
    assert "Logic Synthesis (contnd…)" not in norm
    assert not norm.endswith("\n4") and norm != "4"

    # Superscripts, subscripts, math symbols, truth table rows preserved (NOT NFKC flattened)
    assert "f(x₁, x₂) = x₁ ∧ x₂ ⊕ x² − x³" in norm
    assert "  0   1   0   1" in norm
    assert "  1   0   1   1" in norm
    assert "x -\ny" in norm

    # Safe ligature repair, wrapped word de-hyphenation, bullet standardization, Bengali intact
    assert "algorithm rejoins cleanly." in norm
    assert "finding flow optimization for step 4" in norm
    assert "- Reduction of OBDD:" in norm
    assert "  - Represent Boolean function" in norm
    assert "এবং অ্যালগরিদমের সম্পূর্ণ বর্ণনা।" in norm


def test_normalize_document_pages_handles_inline_slide_number_bleed_safely() -> None:
    pages = [
        {
            "page_number": 1,
            "raw_text": "High Level Synthesis (contnd…)\nAllocation with value 1\n1",
        },
        {
            "page_number": 2,
            "raw_text": "High Level Synthesis (contnd…)\nAssign operation to step 2\n2",
        },
        {
            "page_number": 3,
            "raw_text": "High Level Synthesis (contnd…)\nOperations share hardware if not executed at the same time 3",
        },
        {
            "page_number": 4,
            "raw_text": "High Level Synthesis (contnd…)\nEmpty visual-only slide body\n4",
        },
    ]
    results = normalize_document_pages(pages, header_min_page_ratio=0.5)

    assert "High Level Synthesis (contnd…)" not in results[0].normalized_text
    # "value 1" and "step 2" are technical terms, NOT stripped as slide bleed
    assert "Allocation with value 1" in results[0].normalized_text
    assert "Assign operation to step 2" in results[1].normalized_text
    # Inline prose trailing slide number 3 IS stripped cleanly
    assert (
        results[2].normalized_text == "Operations share hardware if not executed at the same time"
    )
    # Raw input dicts remain untouched
    assert pages[2]["raw_text"].endswith("at the same time 3")


def test_build_docker_sandbox_command_enforces_hard_isolation(tmp_path: Path) -> None:
    input_pdf = tmp_path / "lecture.pdf"
    input_pdf.write_bytes(b"%PDF-1.4\n")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    cmd = build_docker_sandbox_command(
        container_name="ragbait-parser-test123",
        input_path=input_pdf,
        output_dir=out_dir,
        mime_type="application/pdf",
        max_pages=500,
        max_bytes=10_000_000,
        parser_engine="pymupdf",
        config=ContainerSandboxConfig(
            image="ragbait-parser-sandbox:latest",
            memory_limit="256m",
            cpus_limit="0.5",
            pids_limit=24,
        ),
    )

    assert cmd[:4] == ["docker", "run", "--rm", "--init"]
    assert "--network=none" in cmd
    assert "--read-only" in cmd
    assert "--cap-drop=ALL" in cmd
    assert "--security-opt=no-new-privileges:true" in cmd
    assert "65532:65532" in cmd
    assert "256m" in cmd
    assert "0.5" in cmd
    assert "24" in cmd
    # Single read-only input mount + no secret env vars
    mounts = [arg for arg in cmd if arg.startswith("type=bind,")]
    assert any(",dst=/sandbox/in/source.pdf,readonly" in m for m in mounts)
    assert any(",dst=/sandbox/out" in m for m in mounts)
    joined = " ".join(cmd)
    assert "DATABASE_URL" not in joined
    assert "SUPABASE" not in joined


@pytest.mark.asyncio
async def test_container_required_fails_closed_when_image_unavailable(tmp_path: Path) -> None:
    sample = tmp_path / "sample.txt"
    sample.write_text("Hello world", encoding="utf-8")

    with patch(
        "app.modules.knowledge_base.parser_sandbox.is_sandbox_image_present",
        new=AsyncMock(return_value=False),
    ):
        with pytest.raises(TransientExtractionError) as exc_info:
            await run_parser_isolated(
                input_path=sample,
                mime_type="text/plain",
                isolation_mode="container_required",
            )
    assert exc_info.value.code == "SANDBOX_UNAVAILABLE"


@pytest.mark.asyncio
async def test_live_container_sandbox_enforces_non_root_readonly_no_network(
    tmp_path: Path,
) -> None:
    """When ragbait-parser-sandbox image exists, verify real container isolation end-to-end."""
    import asyncio

    from pypdf import PdfWriter

    from app.modules.knowledge_base.parser_sandbox import is_sandbox_image_present

    if not await is_sandbox_image_present("ragbait-parser-sandbox:latest"):
        pytest.skip("ragbait-parser-sandbox:latest not built locally")

    # Probe container user UID and read-only / no-network isolation directly
    probe = await asyncio.create_subprocess_exec(
        "docker",
        "run",
        "--rm",
        "--network=none",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--user",
        "65532:65532",
        "--entrypoint",
        "python",
        "ragbait-parser-sandbox:latest",
        "-c",
        (
            "import os, socket; "
            "assert os.getuid() == 65532; "
            "assert 'DATABASE_URL' not in os.environ; "
            "print(socket.gethostname())"
        ),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await asyncio.wait_for(probe.communicate(), timeout=15.0)
    assert probe.returncode == 0, err.decode(errors="ignore")

    # End-to-end PDF parsing inside container_required mode
    pdf_path = tmp_path / "sandbox_sample.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with pdf_path.open("wb") as f:
        writer.write(f)

    res = await run_parser_isolated(
        input_path=pdf_path,
        mime_type="application/pdf",
        isolation_mode="container_required",
        parser_engine="pypdf",
        timeout_seconds=30.0,
    )
    assert res["parser_backend"] == "container_sandbox"
    assert res["total_pages"] == 1
    assert res["pages"][0]["extraction_method"] == "pypdf_text"
    assert "quality_diagnostics" in res["pages"][0]


@pytest.mark.asyncio
async def test_pymupdf_blocked_outside_container_sandbox(tmp_path: Path) -> None:
    sample = tmp_path / "sample.pdf"
    sample.write_bytes(b"%PDF-1.4\n")

    with pytest.raises(PermanentExtractionError) as exc_info:
        await run_parser_isolated(
            input_path=sample,
            mime_type="application/pdf",
            isolation_mode="subprocess_legacy",
            parser_engine="pymupdf",
        )
    assert exc_info.value.code == "SANDBOX_REQUIRED_FOR_ENGINE"
