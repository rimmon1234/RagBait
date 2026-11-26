"""Pytest verification wrapper enforcing M4.8 50-question bilingual RAG release gates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.evaluation.run_m4_8_evaluation import (
    DATASET_PATH,
    DEFAULT_RESULTS_PATH,
    run_evaluation,
    validate_golden_dataset,
)


def test_m4_8_golden_dataset_schema_and_balance() -> None:
    """Verify frozen 50-question golden dataset meets exact bilingual and category counts."""
    data = json.loads(Path(DATASET_PATH).read_text(encoding="utf-8"))
    validate_golden_dataset(data)

    by_cat: dict[str, int] = {}
    for q in data["questions"]:
        by_cat[q["category"]] = by_cat.get(q["category"], 0) + 1
    assert by_cat == {
        "direct_factual": 10,
        "multi_passage_synthesis": 10,
        "multi_page_evidence": 8,
        "cross_language_retrieval": 8,
        "ambiguous_or_conflicting": 6,
        "unanswerable": 8,
    }


@pytest.mark.asyncio
async def test_m4_8_retrieval_and_grounded_generation_gates(tmp_path: Path) -> None:
    """Run full 50-question benchmark against live Postgres+pgvector+FastEmbed and assert release gates."""
    out_file = tmp_path / "results_m4_8_pytest.json"
    report = await run_evaluation(output_path=out_file)
    assert out_file.exists()

    # Keep canonical artifact synchronized on full run
    DEFAULT_RESULTS_PATH.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 1. Hard security isolation gates (zero leaks across all 3 retrieval modes)
    sec = report["security_fencing_checks"]
    for key, leak_count in sec.items():
        assert leak_count == 0, f"Security retrieval leak detected in {key}: {leak_count}"

    # 2. Retrieval quality thresholds (Section 15.2)
    hybrid = report["retrieval_comparison"]["hybrid_rrf"]
    assert hybrid["overall"]["recall_at_5"] >= 0.85
    assert hybrid["english"]["recall_at_5"] >= 0.80
    assert hybrid["bengali"]["recall_at_5"] >= 0.80
    assert hybrid["overall"]["mrr"] >= 0.70

    # 3. FTS short keyword probe sanity (confirms GIN index works on concise keywords)
    for probe in report["fts_keyword_probes"]:
        assert probe["hits"] >= 1, f"Short FTS keyword probe returned 0 hits: {probe}"

    # 4. Grounded generation, citation validity, abstention & injection resistance (Section 15.3)
    gen = report["generation_summary"]
    assert gen["total_evaluated"] == 50
    assert gen["citation_validity_rate"] == 1.0
    assert gen["abstention_correctness"] == 1.0
    assert gen["prompt_injection_pass_rate"] == 1.0
    assert gen["budget_compliance_rate"] == 1.0
    assert gen["repair_triggered_and_cleaned"] is True
