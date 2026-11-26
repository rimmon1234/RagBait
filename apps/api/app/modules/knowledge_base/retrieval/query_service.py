"""Query validation and query vector generation using M4.3 embeddings."""

import logging

from app.core.errors import BadRequestError
from app.modules.knowledge_base.embeddings import (
    EmbeddingResult,
    QueryLengthExceededError,
    embed_query,
)
from app.modules.knowledge_base.retrieval.config import retrieval_settings

logger = logging.getLogger("ragbait.kb.retrieval.query")


def prepare_query_vector(query_text: str) -> EmbeddingResult:
    """Validate query text and generate a normalized 384-dim semantic query vector.

    Guarantees:
    1. Query text is non-empty and stripped of surrounding whitespace.
    2. Strict query token limit (<= 128 tokens) is enforced.
    3. Proper 'query: ' prefix is applied by M4.3 embed_query().
    4. Model fingerprint matches the approved local INT8 ONNX manifest.
    5. No external cloud calls; inference runs purely on local CPU.

    Raises:
        BadRequestError: If query is empty or exceeds the 128-token limit.
    """
    clean_query = query_text.strip() if query_text else ""
    if not clean_query:
        raise BadRequestError("Search query text cannot be empty or whitespace-only.")

    try:
        embedding_res = embed_query(clean_query)
    except QueryLengthExceededError as exc:
        logger.warning(
            f"Query token limit exceeded ({retrieval_settings.max_query_tokens} tokens): {exc}"
        )
        raise BadRequestError(
            f"Query exceeds maximum allowed limit of {retrieval_settings.max_query_tokens} tokens."
        ) from exc
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc

    return embedding_res
