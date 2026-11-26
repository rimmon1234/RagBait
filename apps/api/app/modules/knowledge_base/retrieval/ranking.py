"""Pure Reciprocal Rank Fusion (RRF) logic and ranking algorithms."""

import math
from typing import Any
from uuid import UUID


class RRFParameterError(ValueError):
    """Raised when RRF parameters or weights violate mathematical bounds."""

    pass


def validate_rrf_parameters(
    k: int = 60,
    vector_weight: float = 0.60,
    fts_weight: float = 0.40,
) -> None:
    """Validate mathematical invariants for RRF hyperparameters.

    Rules:
    1. k must be a positive integer (k > 0).
    2. Weights must be finite numbers (no NaN or Inf).
    3. Weights must be non-negative (vector_weight >= 0, fts_weight >= 0).
    4. Total weight must be strictly positive (vector_weight + fts_weight > 0).
    """
    if k <= 0:
        raise RRFParameterError(f"RRF constant k must be positive, got {k}")

    if not math.isfinite(vector_weight) or not math.isfinite(fts_weight):
        raise RRFParameterError("RRF weights must be finite numbers")

    if vector_weight < 0.0 or fts_weight < 0.0:
        raise RRFParameterError(
            f"RRF weights must be non-negative, got vector={vector_weight}, fts={fts_weight}"
        )

    if (vector_weight + fts_weight) <= 0.0:
        raise RRFParameterError("Total RRF weight must be strictly positive")


def compute_rrf_score(
    vector_rank: int | None,
    fts_rank: int | None,
    k: int = 60,
    vector_weight: float = 0.60,
    fts_weight: float = 0.40,
) -> float:
    """Compute the combined RRF score for an individual candidate chunk.

    RRF(d) = (w_vec / (k + rank_vec(d))) + (w_fts / (k + rank_fts(d)))

    Ranks must be 1-based positive integers. A missing rank contributes 0.0.
    """
    validate_rrf_parameters(k, vector_weight, fts_weight)

    score = 0.0
    if vector_rank is not None:
        if vector_rank <= 0:
            raise RRFParameterError(
                f"vector_rank must be a 1-based positive integer, got {vector_rank}"
            )
        score += vector_weight / (k + vector_rank)

    if fts_rank is not None:
        if fts_rank <= 0:
            raise RRFParameterError(f"fts_rank must be a 1-based positive integer, got {fts_rank}")
        score += fts_weight / (k + fts_rank)

    return score


def fuse_ranked_candidates(
    vector_candidates: list[dict[str, Any]],
    fts_candidates: list[dict[str, Any]],
    k: int = 60,
    vector_weight: float = 0.60,
    fts_weight: float = 0.40,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Fuse ranked candidate lists from vector and full-text branches using RRF.

    Guarantees:
    1. Deduplication by chunk_id.
    2. Preservation of individual branch ranks and scores.
    3. Deterministic tie-breaking:
       ORDER BY rrf_score DESC, vector_rank ASC NULLS LAST, fts_rank ASC NULLS LAST, chunk_id ASC.
    4. 1-based final_rank assignment.
    5. Bounded result list length (<= limit).
    """
    validate_rrf_parameters(k, vector_weight, fts_weight)

    if limit <= 0:
        return []

    merged: dict[UUID, dict[str, Any]] = {}

    # Ingest vector candidates (must have 'chunk_id' and 1-based 'vector_rank')
    for idx, c in enumerate(vector_candidates, start=1):
        cid = c["chunk_id"] if isinstance(c["chunk_id"], UUID) else UUID(str(c["chunk_id"]))
        v_rank = c.get("vector_rank", idx)
        item = dict(c)
        item["chunk_id"] = cid
        item["vector_rank"] = v_rank
        item.setdefault("fts_rank", None)
        item.setdefault("fts_score", None)
        merged[cid] = item

    # Ingest FTS candidates (must have 'chunk_id' and 1-based 'fts_rank')
    for idx, c in enumerate(fts_candidates, start=1):
        cid = c["chunk_id"] if isinstance(c["chunk_id"], UUID) else UUID(str(c["chunk_id"]))
        f_rank = c.get("fts_rank", idx)
        if cid in merged:
            merged[cid]["fts_rank"] = f_rank
            merged[cid]["fts_score"] = c.get("fts_score")
        else:
            item = dict(c)
            item["chunk_id"] = cid
            item["fts_rank"] = f_rank
            item.setdefault("vector_rank", None)
            item.setdefault("vector_distance", None)
            merged[cid] = item

    # Compute RRF score for each unique candidate
    results: list[dict[str, Any]] = []
    for item in merged.values():
        v_rank = item.get("vector_rank")
        f_rank = item.get("fts_rank")
        score = compute_rrf_score(
            vector_rank=v_rank,
            fts_rank=f_rank,
            k=k,
            vector_weight=vector_weight,
            fts_weight=fts_weight,
        )
        item["rrf_score"] = score
        results.append(item)

    # Sort with deterministic tie-breaking
    def sort_key(x: dict[str, Any]) -> tuple[float, int, int, str]:
        # Negative rrf_score for descending order
        rrf_val = -x["rrf_score"]
        v_rank = x["vector_rank"] if x["vector_rank"] is not None else 999999
        f_rank = x["fts_rank"] if x["fts_rank"] is not None else 999999
        cid_str = str(x["chunk_id"])
        return (rrf_val, v_rank, f_rank, cid_str)

    results.sort(key=sort_key)

    # Assign 1-based final_rank and slice
    final_output: list[dict[str, Any]] = []
    for rank, item in enumerate(results[:limit], start=1):
        item["final_rank"] = rank
        final_output.append(item)

    return final_output
