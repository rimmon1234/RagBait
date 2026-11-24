"""Tokenizer interface and exact token-counting utilities for multilingual-e5-small.

Enforces the 512-token model budget:
- Capping raw chunk text at MAX_RAW_CHUNK_TOKENS = 420 tokens.
- Accounting for 'passage: ' prefix (2 tokens) and <s>/</s> special tokens (2 tokens).
- Guaranteed maximum embedding input: 424 tokens <= 512 tokens (88 token margin).
"""

import logging
from pathlib import Path

from tokenizers import Tokenizer

logger = logging.getLogger("ragbait.kb.chunking.tokenizer")

# Authoritative Constants per ADR-0004 & ADR-0021
MODEL_NAME: str = "intfloat/multilingual-e5-small"
PASSAGE_PREFIX: str = "passage: "
QUERY_PREFIX: str = "query: "

MAX_MODEL_INPUT_TOKENS: int = 512
MAX_RAW_CHUNK_TOKENS: int = 420
TARGET_CHUNK_TOKENS: int = 380
TARGET_OVERLAP_TOKENS: int = 60

# Prefix & framing overhead: <s> (1) + 'passage' (1) + ': ' (1) + </s> (1) = 4 tokens
PREFIX_FRAMING_OVERHEAD: int = 4

_CACHED_TOKENIZER: Tokenizer | None = None


def get_tokenizer() -> Tokenizer:
    """Return the cached multilingual-e5-small Tokenizer instance.

    Attempts to load from the local asset cache first.
    Falls back to HuggingFace Hub download and caches the asset locally.
    """
    global _CACHED_TOKENIZER
    if _CACHED_TOKENIZER is not None:
        return _CACHED_TOKENIZER

    asset_dir = Path(__file__).parent.parent / "assets"
    local_path = asset_dir / "tokenizer.json"

    if local_path.is_file():
        try:
            logger.info(f"Loading local tokenizer asset from {local_path}")
            _CACHED_TOKENIZER = Tokenizer.from_file(str(local_path))
            return _CACHED_TOKENIZER
        except Exception as exc:
            logger.warning(
                f"Failed to load tokenizer from {local_path}: {exc}. Falling back to HuggingFace."
            )

    logger.info(f"Downloading tokenizer for {MODEL_NAME} from HuggingFace Hub...")
    tok = Tokenizer.from_pretrained(MODEL_NAME)

    try:
        asset_dir.mkdir(parents=True, exist_ok=True)
        tok.save(str(local_path))
        logger.info(f"Cached tokenizer asset locally to {local_path}")
    except Exception as exc:
        logger.warning(f"Could not cache tokenizer asset locally: {exc}")

    _CACHED_TOKENIZER = tok
    return _CACHED_TOKENIZER


def count_tokens(text: str) -> int:
    """Return the exact token count of text without special tokens."""
    if not text:
        return 0
    tok = get_tokenizer()
    encoded = tok.encode(text, add_special_tokens=False)
    return len(encoded.ids)


def count_passage_tokens(text: str) -> int:
    """Return the exact token count when formatted as an embedding passage."""
    if not text:
        return 0
    tok = get_tokenizer()
    encoded = tok.encode(f"{PASSAGE_PREFIX}{text}", add_special_tokens=True)
    return len(encoded.ids)


def validate_chunk_tokens(text: str) -> tuple[bool, int, int]:
    """Validate that chunk text meets the hard ceiling requirements.

    Returns:
        (is_valid, raw_token_count, passage_token_count)
    """
    raw_tokens = count_tokens(text)
    passage_tokens = count_passage_tokens(text)
    is_valid = (
        raw_tokens > 0
        and raw_tokens <= MAX_RAW_CHUNK_TOKENS
        and passage_tokens <= MAX_MODEL_INPUT_TOKENS
    )
    return is_valid, raw_tokens, passage_tokens
