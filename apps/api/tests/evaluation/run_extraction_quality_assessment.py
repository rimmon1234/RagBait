"""Stage 1 read-only PDF extraction quality assessment runner for ECE 3106 & KB PDFs.

Downloads PDFs read-only from private Supabase Storage (or accepts local --pdf files),
runs the unmodified baseline `extract_pdf_pages()` plus Stage 1 page-level diagnostics
`assess_pdf_extraction_quality()`, verifies parity with persisted `kb_document_pages`,
and emits a detailed baseline JSON report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import asyncpg

API_ROOT = Path(__file__).resolve().parents[2]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.modules.knowledge_base.extraction_diagnostics import assess_pdf_extraction_quality  # noqa: E402
from app.modules.knowledge_base.pdf_extractor import extract_pdf_pages  # noqa: E402
from app.modules.knowledge_base.storage import (  # noqa: E402
    close_storage_client,
    download_object_bytes,
)

RESULTS_PATH = Path(__file__).resolve().parent / "results_stage1_ece3106.json"
FLAGSHIP_PROBLEM_PAGES: dict[str, tuple[int, ...]] = {
    "High-Level Synthesis": (6, 9, 17, 18, 24, 31),
    "Logic Systhesis": (10, 17, 22, 24, 25, 26, 27, 28, 31, 41, 45),
}


def _assess_local_pdf(
    pdf_path: Path,
    *,
    title: str,
    original_filename: str,
    persisted_chars: dict[int, int] | None = None,
) -> dict[str, Any]:
    baseline = extract_pdf_pages(pdf_path)
    report = assess_pdf_extraction_quality(pdf_path, extracted_result=baseline)

    mismatched_persisted_pages: list[int] = []
    if persisted_chars:
        for p in baseline["pages"]:
            pno = int(p["page_number"])
            if pno in persisted_chars and persisted_chars[pno] != p["character_count"]:
                mismatched_persisted_pages.append(pno)

    report_dict = report.to_dict()
    modality_counts: dict[str, int] = {}
    for p in report.pages:
        modality_counts[p.recommended_modality] = modality_counts.get(p.recommended_modality, 0) + 1

    flagship_pages = [
        p.to_dict() for p in report.pages if p.page_number in FLAGSHIP_PROBLEM_PAGES.get(title, ())
    ]

    return {
        "title": title,
        "original_filename": original_filename,
        "persisted_page_parity_mismatches": mismatched_persisted_pages,
        "modality_breakdown": modality_counts,
        "flagship_problem_pages": flagship_pages,
        **report_dict,
    }


async def run_from_database(dsn: str) -> list[dict[str, Any]]:
    """Read-only fetch of active PDFs in kb_documents and run Stage 1 diagnostics."""
    conn = await asyncpg.connect(dsn)
    results: list[dict[str, Any]] = []
    try:
        docs = await conn.fetch(
            """
            SELECT id, title, original_filename, storage_path
            FROM public.kb_documents
            WHERE mime_type = 'application/pdf' AND NOT is_archived
            ORDER BY created_at
            """
        )
        with tempfile.TemporaryDirectory(prefix="ragbait_stage1_eval_") as tmp_dir:
            for doc in docs:
                raw_bytes = await download_object_bytes(doc["storage_path"])
                tmp_pdf = Path(tmp_dir) / f"{doc['id']}.pdf"
                tmp_pdf.write_bytes(raw_bytes)
                persisted_rows = await conn.fetch(
                    """
                    SELECT page_number, character_count
                    FROM public.kb_document_pages
                    WHERE document_id = $1
                    ORDER BY page_number
                    """,
                    doc["id"],
                )
                persisted_chars = {
                    int(r["page_number"]): int(r["character_count"]) for r in persisted_rows
                }
                entry = _assess_local_pdf(
                    tmp_pdf,
                    title=str(doc["title"]),
                    original_filename=str(doc["original_filename"]),
                    persisted_chars=persisted_chars,
                )
                entry["document_id"] = str(doc["id"])
                results.append(entry)
    finally:
        await close_storage_client()
        await conn.close()
    return results


def _print_summary(entries: list[dict[str, Any]]) -> None:
    print("\n=== Stage 1 PDF Extraction Quality Assessment Baseline ===")
    for doc in entries:
        s = doc["summary"]
        print(
            f"\nDocument: {doc['title']} ({doc['original_filename']})\n"
            f"  Pages: {s['total_pages']} | Low/Empty (non-title): {s['low_text_pages']} "
            f"| Images: {s['pages_with_images']} | Symbol/Eq gaps: {s['pages_with_symbol_gaps']} "
            f"| Boilerplate: {s['pages_with_boilerplate']} | Suspected info loss: {s['pages_suspected_info_loss']}\n"
            f"  Repeated headers: {s['repeated_headers']} | Modalities: {doc['modality_breakdown']}\n"
            f"  Persisted DB parity mismatches: {doc['persisted_page_parity_mismatches']}"
        )
        if doc["flagship_problem_pages"]:
            print("  Flagship problem pages:")
            for fp in doc["flagship_problem_pages"]:
                print(
                    f"    - p.{fp['page_number']}: chars={fp['character_count']} "
                    f"(non_bp={fp['non_boilerplate_char_count']}), imgs={fp['significant_image_count']} "
                    f"(eq_strips={fp['equation_strip_count']}), gaps={fp['blank_gap_run_count']}/{fp['orphan_delimiter_count']}, "
                    f"modality={fp['recommended_modality']}, flags={fp['flags']}"
                )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Stage 1 PDF Extraction Quality Diagnostics")
    parser.add_argument(
        "--pdf", action="append", default=[], help="Optional local PDF path(s) to assess"
    )
    parser.add_argument("--out", type=Path, default=RESULTS_PATH, help="Output JSON path")
    args = parser.parse_args()

    if args.pdf:
        entries = [
            _assess_local_pdf(
                Path(p),
                title=Path(p).stem,
                original_filename=Path(p).name,
            )
            for p in args.pdf
        ]
    else:
        dsn = os.environ.get(
            "DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
        )
        entries = asyncio.run(run_from_database(dsn))

    payload = {"stage": "stage1_extraction_quality_assessment", "documents": entries}
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _print_summary(entries)
    print(f"\nSaved Stage 1 diagnostics JSON to: {args.out}")


if __name__ == "__main__":
    main()
