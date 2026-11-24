"""Unit and integration tests for local multilingual-e5-small embedding pipeline."""

import math
from pathlib import Path

import pytest

from app.modules.knowledge_base.embeddings import (
    EmbeddingResult,
    PassageLengthExceededError,
    QueryLengthExceededError,
    VectorValidationError,
    embed_passages,
    embed_query,
    get_model_manifest,
    validate_vector,
)


def test_model_manifest_properties_and_verification():
    """Verify manifest records the expected authoritative model specifications."""
    manifest = get_model_manifest()
    assert manifest.model_id == "intfloat/multilingual-e5-small"
    assert manifest.model_revision == "761b726dd34fb83930e26aab4e9ac3899aa1fa78"
    assert manifest.embedding_dimension == 384
    assert manifest.max_sequence_length == 512
    assert manifest.passage_prefix == "passage: "
    assert manifest.query_prefix == "query: "
    assert manifest.model_fingerprint == "e5_small_int8_761b726d_f80102d3"
    assert "onnx/model_quantized.onnx" in manifest.artifacts
    assert manifest.artifacts["onnx/model_quantized.onnx"].size_bytes == 118308185

    # Verification on non-existent directory fails cleanly
    is_valid, errors = manifest.verify_local_directory(Path("/tmp/nonexistent-model-dir-xyz"))
    assert is_valid is False
    assert len(errors) > 0


def test_vector_validation_invariants():
    """Verify strict mathematical validation for 384-dim L2 unit vectors."""
    # 1. Valid unit vector
    vec = [1.0 / math.sqrt(384)] * 384
    validate_vector(vec)

    # 2. Wrong dimension
    with pytest.raises(VectorValidationError, match="dimension mismatch"):
        validate_vector([1.0 / math.sqrt(383)] * 383)

    # 3. Non-finite value (NaN or Inf)
    bad_vec = list(vec)
    bad_vec[10] = float("nan")
    with pytest.raises(VectorValidationError, match="non-finite"):
        validate_vector(bad_vec)

    bad_vec[10] = float("inf")
    with pytest.raises(VectorValidationError, match="non-finite"):
        validate_vector(bad_vec)

    # 4. Zero magnitude
    with pytest.raises(VectorValidationError, match="zero magnitude"):
        validate_vector([0.0] * 384)

    # 5. Non-normalized vector
    unnorm_vec = [1.0] * 384
    with pytest.raises(VectorValidationError, match="not L2 unit normalized"):
        validate_vector(unnorm_vec)


def test_query_token_boundary_enforcement():
    """Student query inputs must be <= 128 tokens and non-empty."""
    # Empty query raises ValueError
    with pytest.raises(ValueError, match="cannot be empty"):
        embed_query("")

    with pytest.raises(ValueError, match="cannot be empty"):
        embed_query("    \n\t  ")

    # Valid short query succeeds
    res = embed_query("What is virtual memory?")
    assert isinstance(res, EmbeddingResult)
    assert len(res.vector) == 384
    assert res.dimensions == 384
    assert res.model_fingerprint == "e5_small_int8_761b726d_f80102d3"

    # Query exceeding 128 tokens is rejected with clear error
    huge_query = "explain operating system concept in detail " * 40
    with pytest.raises(
        QueryLengthExceededError, match="exceeds maximum allowed limit of 128 tokens"
    ):
        embed_query(huge_query)


def test_passage_embedding_and_batch_processing():
    """Passage embeddings prepend 'passage: ' and enforce 512 token ceiling."""
    # Empty input list returns empty list
    assert embed_passages([]) == []

    # Whitespace text raises ValueError
    with pytest.raises(ValueError, match="empty or whitespace-only"):
        embed_passages(["Operating systems", "   "])

    # Valid batch of passages
    texts = [
        "Operating systems manage CPU scheduling and virtual memory.",
        "Computer networks rely on packet switching and layered protocols.",
    ]
    results = embed_passages(texts)
    assert len(results) == 2
    for r in results:
        assert len(r.vector) == 384
        assert r.dimensions == 384
        assert r.model_fingerprint == "e5_small_int8_761b726d_f80102d3"
        # Unit norm verified
        norm = math.sqrt(sum(x * x for x in r.vector))
        assert abs(norm - 1.0) < 1e-4

    # Passage exceeding 512 tokens raises PassageLengthExceededError
    huge_passage = "memory allocation and process scheduling " * 150
    with pytest.raises(PassageLengthExceededError, match="exceeds 512 tokens"):
        embed_passages([huge_passage])


def test_multilingual_real_model_smoke_and_semantic_ordering():
    """Test actual CPU ONNX inference with English, Bengali, Hindi, and semantic similarity."""
    passages = [
        # Related English
        "Operating systems manage hardware concurrency, CPU scheduling, and virtual memory.",
        # Related Bengali
        "কম্পিউটার বিজ্ঞান তথ্য এবং গণনার তাত্ত্বিক ভিত্তি নিয়ে আলোচনা করে।",
        # Related Hindi
        "कंप्यूटर विज्ञान सूचना और संगणना के सैद्धांतिक आधारों का अध्ययन है।",
        # Unrelated
        "Baking sourdough bread requires flour, water, salt, and active fermentation starter.",
    ]

    p_results = embed_passages(passages)
    assert len(p_results) == 4

    q_result = embed_query("What is computer science and operating systems?")

    # Compute cosine similarity via dot product (since vectors are L2 unit normalized)
    def dot(v1: list[float], v2: list[float]) -> float:
        return sum(a * b for a, b in zip(v1, v2, strict=False))

    sim_en = dot(q_result.vector, p_results[0].vector)
    sim_bn = dot(q_result.vector, p_results[1].vector)
    sim_hi = dot(q_result.vector, p_results[2].vector)
    sim_unrelated = dot(q_result.vector, p_results[3].vector)

    # Sanity checks: Academic passages must have significantly higher similarity than baking bread
    assert sim_en > sim_unrelated
    assert sim_bn > sim_unrelated
    assert sim_hi > sim_unrelated
    assert sim_en > 0.70
    assert sim_bn > 0.65
