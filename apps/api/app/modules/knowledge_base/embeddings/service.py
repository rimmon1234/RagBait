"""Embedding service interface for document passages and query embeddings."""

import logging
from collections.abc import Sequence

from app.modules.knowledge_base.chunking import count_tokens
from app.modules.knowledge_base.embeddings.config import embedding_settings
from app.modules.knowledge_base.embeddings.model_loader import get_embedding_model
from app.modules.knowledge_base.embeddings.schemas import EmbeddingResult
from app.modules.knowledge_base.embeddings.vector_validation import validate_vector

logger = logging.getLogger("ragbait.kb.embeddings.service")


class QueryLengthExceededError(ValueError):
    """Raised when student query input exceeds the maximum token limit."""

    pass


class PassageLengthExceededError(ValueError):
    """Raised when formatted passage input exceeds the 512-token ceiling."""

    pass


def embed_passages(
    raw_texts: Sequence[str],
    batch_size: int | None = None,
) -> list[EmbeddingResult]:
    """Generate 384-dimensional dense embeddings for document passages.

    Steps:
    1. Validates each input text is non-empty.
    2. Prefixes text with 'passage: '.
    3. Validates total formatted tokens <= 512.
    4. Executes bounded batch ONNX inference using FastEmbed on CPU.
    5. Validates L2 normalization, dimensions, and finite numbers.
    6. Returns validated EmbeddingResult objects.

    Raises:
        ValueError: If input is empty or contains blank texts.
        PassageLengthExceededError: If formatted input exceeds 512 tokens.
        VectorValidationError: If model output fails numerical validation.
    """
    if not raw_texts:
        return []

    embedder, manifest = get_embedding_model()
    bs = batch_size or embedding_settings.batch_size

    formatted_inputs: list[str] = []
    token_counts: list[int] = []

    for idx, text in enumerate(raw_texts):
        cleaned = text.strip() if text else ""
        if not cleaned:
            raise ValueError(f"Passage text at index {idx} is empty or whitespace-only")

        formatted = f"{manifest.passage_prefix}{cleaned}"
        # Exact token count including prefix
        tokens = count_tokens(formatted)
        if tokens > manifest.max_sequence_length:
            raise PassageLengthExceededError(
                f"Passage at index {idx} exceeds {manifest.max_sequence_length} tokens ({tokens} tokens)"
            )

        formatted_inputs.append(formatted)
        token_counts.append(tokens)

    # Run batched inference via FastEmbed
    results: list[EmbeddingResult] = []
    vectors = list(embedder.embed(formatted_inputs, batch_size=bs))

    for vec, tok_cnt in zip(vectors, token_counts, strict=False):
        vec_list = [float(x) for x in vec]
        validate_vector(vec_list, expected_dimensions=manifest.embedding_dimension)

        results.append(
            EmbeddingResult(
                vector=vec_list,
                dimensions=manifest.embedding_dimension,
                model_id=manifest.model_id,
                model_fingerprint=manifest.model_fingerprint,
                input_tokens=tok_cnt,
                preprocessing_version=1,
            )
        )

    return results


def embed_query(query_text: str) -> EmbeddingResult:
    """Generate a 384-dimensional dense embedding for a student search query.

    Internal model capability for subsequent M4.4 integration.
    Enforces maximum query length (128 tokens) and adds 'query: ' prefix.

    Raises:
        ValueError: If query is empty.
        QueryLengthExceededError: If raw query exceeds 128 tokens.
        VectorValidationError: If output vector fails validation.
    """
    cleaned = query_text.strip() if query_text else ""
    if not cleaned:
        raise ValueError("Query text cannot be empty or whitespace-only")

    embedder, manifest = get_embedding_model()

    # Verify query token length before formatting
    raw_tokens = count_tokens(cleaned)
    if raw_tokens > embedding_settings.max_query_tokens:
        raise QueryLengthExceededError(
            f"Query exceeds maximum allowed limit of {embedding_settings.max_query_tokens} tokens "
            f"(received {raw_tokens} tokens)"
        )

    formatted = f"{manifest.query_prefix}{cleaned}"
    total_tokens = count_tokens(formatted)

    vectors = list(embedder.embed([formatted]))
    if not vectors:
        raise RuntimeError("Embedding model produced no output for query")

    vec_list = [float(x) for x in vectors[0]]
    validate_vector(vec_list, expected_dimensions=manifest.embedding_dimension)

    return EmbeddingResult(
        vector=vec_list,
        dimensions=manifest.embedding_dimension,
        model_id=manifest.model_id,
        model_fingerprint=manifest.model_fingerprint,
        input_tokens=total_tokens,
        preprocessing_version=1,
    )
