"""Stage 2 & Stage 3 benchmark: container-sandboxed pypdf vs pymupdf + normalization on ECE 3106."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import asyncpg

API_ROOT = Path(__file__).resolve().parents[2]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.modules.knowledge_base.chunking import PageInput, chunk_document_pages  # noqa: E402
from app.modules.knowledge_base.parser_process import run_parser_isolated  # noqa: E402
from app.modules.knowledge_base.storage import (  # noqa: E402
    close_storage_client,
    download_object_bytes,
)

RESULTS_PATH = Path(__file__).resolve().parent / "results_stage2_stage3_ece3106.json"
FLAGSHIP_CHECK_PAGES: dict[str, tuple[int, ...]] = {
    "High-Level Synthesis": (6, 9, 18),
    "Logic Systhesis": (10, 17, 18, 22),
}


def _summarize_engine_run(
    title: str,
    engine: str,
    result: dict[str, Any],
    elapsed_ms: float,
) -> dict[str, Any]:
    pages = result["pages"]
    raw_texts = [p["raw_text"] for p in pages]
    norm_texts = [p.get("normalized_text", p["raw_text"]) for p in pages]

    raw_chunks = chunk_document_pages(
        [
            PageInput(page_number=p["page_number"], text=p["raw_text"], has_text=p["has_text"])
            for p in pages
        ]
    )
    norm_chunks = chunk_document_pages(
        [
            PageInput(
                page_number=p["page_number"],
                text=p.get("normalized_text") or "",
                has_text=bool((p.get("normalized_text") or "").strip()),
            )
            for p in pages
        ]
    )

    stats_totals: dict[str, int] = {}
    for p in pages:
        for k, v in (p.get("quality_diagnostics", {}).get("normalization_stats") or {}).items():
            stats_totals[k] = stats_totals.get(k, 0) + int(v)

    flagship = {}
    for pno in FLAGSHIP_CHECK_PAGES.get(title, ()):
        match = next((p for p in pages if p["page_number"] == pno), None)
        if match:
            flagship[str(pno)] = {
                "raw_chars": len(match["raw_text"]),
                "normalized_chars": len(match.get("normalized_text") or ""),
                "raw_preview": match["raw_text"][:260],
                "normalized_preview": (match.get("normalized_text") or "")[:260],
            }

    return {
        "engine": engine,
        "parser_backend": result.get("parser_backend"),
        "elapsed_ms": round(elapsed_ms, 2),
        "total_pages": result["total_pages"],
        "pages_with_text": result["pages_with_text"],
        "raw_total_chars": sum(len(t) for t in raw_texts),
        "normalized_total_chars": sum(len(t) for t in norm_texts),
        "raw_chunk_count": len(raw_chunks),
        "normalized_chunk_count": len(norm_chunks),
        "normalization_totals": stats_totals,
        "flagship_samples": flagship,
    }


async def run_benchmark(dsn: str) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(dsn)
    docs_out: list[dict[str, Any]] = []
    try:
        docs = await conn.fetch(
            """
            SELECT id, title, original_filename, storage_path
            FROM public.kb_documents
            WHERE mime_type = 'application/pdf' AND NOT is_archived
            ORDER BY created_at
            """
        )
        with tempfile.TemporaryDirectory(prefix="ragbait_stage2_3_") as tmp_dir:
            for doc in docs:
                raw_bytes = await download_object_bytes(doc["storage_path"])
                tmp_pdf = Path(tmp_dir) / f"{doc['id']}.pdf"
                tmp_pdf.write_bytes(raw_bytes)

                engine_runs: dict[str, Any] = {}
                for engine in ("pypdf", "pymupdf"):
                    t0 = time.perf_counter()
                    res = await run_parser_isolated(
                        input_path=tmp_pdf,
                        mime_type="application/pdf",
                        timeout_seconds=60.0,
                        isolation_mode="container_required",
                        parser_engine=engine,  # type: ignore[arg-type]
                    )
                    elapsed_ms = (time.perf_counter() - t0) * 1000.0
                    engine_runs[engine] = _summarize_engine_run(
                        doc["title"], engine, res, elapsed_ms
                    )

                docs_out.append(
                    {
                        "document_id": str(doc["id"]),
                        "title": doc["title"],
                        "original_filename": doc["original_filename"],
                        "engines": engine_runs,
                    }
                )
    finally:
        await close_storage_client()
        await conn.close()
    return docs_out


def main() -> None:
    dsn = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
    docs_out = asyncio.run(run_benchmark(dsn))
    payload = {"stage": "stage2_normalization_and_stage3_sandbox_benchmark", "documents": docs_out}
    RESULTS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("\n=== Stage 2 & Stage 3 Container-Sandboxed ECE 3106 Benchmark ===")
    for doc in docs_out:
        print(f"\nDocument: {doc['title']} ({doc['original_filename']})")
        for eng_name, run in doc["engines"].items():
            print(
                f"  [{eng_name} | {run['parser_backend']} | {run['elapsed_ms']} ms] "
                f"raw_chars={run['raw_total_chars']} -> norm_chars={run['normalized_total_chars']} | "
                f"chunks: {run['raw_chunk_count']} -> {run['normalized_chunk_count']} | "
                f"norm_stats={run['normalization_totals']}"
            )
    print(f"\nSaved Stage 2/3 benchmark JSON to: {RESULTS_PATH}")


if __name__ == "__main__":
    main()
