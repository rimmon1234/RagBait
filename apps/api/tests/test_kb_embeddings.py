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


def test_m4_2_to_m4_3_end_to_end_boundary_integration():
    """Real-model integration test connecting M4.2 page-aware chunking to M4.3 vector embeddings.

    Verifies the complete pipeline:
    Long extracted multi-page document (English, Bengali, Hindi, Math, Unicode)
    -> M4.2 chunker
    -> Page-aware chunks with valid page bounds (<= 420 tokens)
    -> M4.3 passage formatting ("passage: ")
    -> Actual tokenizer verification (<= 512 tokens)
    -> Real ONNX FastEmbed execution
    -> 384-dimensional unit-norm vectors with correct model fingerprint.
    """
    from app.modules.knowledge_base.chunking.chunker import PageInput, chunk_document_pages
    from app.modules.knowledge_base.chunking.tokenizer import get_tokenizer

    # Realistic multilingual academic document across 4 physical pages
    page_1_en = (
        "Operating systems coordinate CPU scheduling, memory management, and concurrent processes. "
        "The kernel manages critical resources through abstractions like virtual memory and threads. "
        "Formulas like P(success) = \\sum_{i=1}^{n} p_i and complexity bounds like O(n \\log n) are common. "
        "ACID transaction properties ensure Atomicity, Consistency, Isolation, and Durability in relational DBMS. "
        "When page faults occur, the translation lookaside buffer (TLB) misses must be handled by the memory management unit. "
        * 3
    )

    page_2_bn = (
        "অপারেটিং সিস্টেম হলো কম্পিউটারের এমন একটি মৌলিক প্রোগ্রাম যা হার্ডওয়্যার এবং ব্যবহারকারীর মধ্যে সমন্বয় স্থাপন করে। "
        "মেমরি ম্যানেজমেন্ট এবং প্রসেস শিডিউলিং এর মাধ্যমে সিস্টেম রিসোর্সের সুষ্ঠু ব্যবহার নিশ্চিত করা হয়। "
        "একটি পেজ ফল্ট তখনই ঘটে যখন মেমরি ম্যানেজমেন্ট ইউনিট কাঙ্ক্ষিত পেজটি ফিজিক্যাল র‍্যামে খুঁজে পেতে ব্যর্থ হয়। "
        "অ্যালগরিদমের জটিলতা বিশ্লেষণ করার জন্য বিগ-ও নোটেশন বহুল ব্যবহৃত হয়, যেমন O(n \\log n) বা O(n^2)। "
        * 3
    )

    page_3_hi = (
        "ऑपरेटिंग सिस्टम कंप्यूटर हार्डवेयर और उपयोगकर्ता अनुप्रयोगों के बीच एक आवश्यक मध्यस्थ के रूप में कार्य करता है। "
        "वर्चुअल मेमोरी तकनीक प्रक्रियाओं को भौतिक मेमोरी सीमाओं से परे निष्पादित करने की अनुमति देती है। "
        "सीपीयू शेड्यूलिंग एल्गोरिदम जैसे राउंड-रॉबिन और फर्स्ट-कम-फर्स्ट-सर्व्ड प्रक्रिया निष्पादन को अनुकूलित करते हैं। "
        "डेटाबेस प्रबंधन प्रणाली में ACID गुण डेटा अखंडता और संगति सुनिश्चित करने के लिए महत्वपूर्ण हैं। "
        * 3
    )

    page_4_mixed = (
        "আধুনিক Cloud Computing আর্কিটেকচারে Microservices এবং Kubernetes ব্যবহার করে distributed systems স্কেল করা হয়। "
        "आधुनिक Distributed Systems में Raft consensus algorithm और Paxos протокол का उपयोग डेटा संगति बनाए रखने के लिए होता है। "
        "Mathematical relations like \\int_0^\\infty e^{-x} dx = 1 and inequalities like \\epsilon > 0 are standard notations. "
        "In modern systems, low latency and high availability are achieved through horizontal partitioning and replication. "
        * 3
    )

    pages = [
        PageInput(page_number=1, text=page_1_en, has_text=True),
        PageInput(page_number=2, text=page_2_bn, has_text=True),
        PageInput(page_number=3, text=page_3_hi, has_text=True),
        PageInput(page_number=4, text=page_4_mixed, has_text=True),
    ]

    # Step 1: Chunk the multi-page document using M4.2 chunker
    chunks = chunk_document_pages(pages)
    assert len(chunks) >= 4, f"Expected at least 4 chunks, got {len(chunks)}"

    tok = get_tokenizer()
    expected_fingerprint = "e5_small_int8_761b726d_f80102d3"

    # Step 2: Verify chunk properties, raw token ceilings, and page ranges
    for c in chunks:
        assert 1 <= c.page_number_start <= c.page_number_end <= 4
        assert len(c.page_spans) >= 1
        assert c.token_count <= 420, f"Chunk exceeded 420 raw tokens: {c.token_count}"
        assert len(c.raw_text.strip()) > 0
        assert len(c.content_hash) == 64

        # Step 3: Verify passage framing with actual tokenizer
        passage_text = f"passage: {c.raw_text}"
        passage_encoded = tok.encode(passage_text, add_special_tokens=True)
        passage_len = len(passage_encoded.ids)
        assert passage_len <= 512, f"Formatted passage exceeded 512 tokens: {passage_len}"

    # Step 4: Real-model local ONNX embedding inference (no mocks)
    raw_texts = [c.raw_text for c in chunks]
    embedding_results = embed_passages(raw_texts)
    assert len(embedding_results) == len(chunks)

    # Step 5: Verify vector invariants on every generated embedding
    for res in embedding_results:
        assert isinstance(res, EmbeddingResult)
        assert len(res.vector) == 384
        assert res.dimensions == 384
        assert res.model_fingerprint == expected_fingerprint

        # Finiteness check
        assert all(math.isfinite(val) for val in res.vector)

        # L2 unit-norm check
        norm = math.sqrt(sum(val * val for val in res.vector))
        assert abs(norm - 1.0) < 1e-4, f"Vector not L2 unit normalized: norm={norm}"

