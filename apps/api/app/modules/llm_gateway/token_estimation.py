"""Conservative server-side token estimation for admission control.

This module provides a safe, conservative upper-bound token estimator for
validating incoming prompts against model context windows, TPM rate limits,
and daily token (TPD) budget caps.

NOTE ON ACCURACY & HEURISTICS:
Exact tokenization varies across model families (e.g., SentencePiece for Gemini,
Tiktoken BPE for GPT/Llama, byte-level BPE for Qwen). Where native tokenizers
are not loaded in-process, this estimator provides a documented, script-aware
conservative upper-bound heuristic with an explicit safety margin. It is
deliberately designed to prevent undercounting across multilingual scripts,
code syntax, emoji, and mathematical notation. It is marked as approximate and
must not be represented as an exact token count.
"""

import math
import re
from datetime import UTC, datetime

from pydantic import BaseModel, Field

# Unicode regex patterns for distinct tokenization characteristics
_INDIC_PATTERN = re.compile(
    r"[\u0900-\u0D7F]"
)  # Devanagari, Bengali, Gurmukhi, Gujarati, Oriya, Tamil, Telugu, Kannada, Malayalam, Sinhala
_CJK_PATTERN = re.compile(
    r"[\u3040-\u30FF\u3400-\u4DBF\u4E00-\u9FFF\uAC00-\uD7AF]"
)  # Hiragana, Katakana, CJK Unified, Hangul
_EMOJI_PATTERN = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF]")  # Emoji, pictographs, dingbats
_MATH_SYMBOLS_PATTERN = re.compile(
    r"[\u2200-\u22FF\u2A00-\u2AFF\u27C0-\u27EF]"
)  # Mathematical operators and symbols
_CODE_SYNTAX_CHARS = set("{}[]()<>=+-*/%!^&|~;:'\",.?\\#@$`")


class TokenEstimate(BaseModel):
    """Server-side conservative token estimate for budget and rate reservation."""

    estimator_type: str = "conservative_multilingual_upper_bound_v2"
    is_approximate: bool = True
    safety_margin_ratio: float = 0.15
    estimated_input_tokens: int = Field(..., ge=0)
    reserved_output_tokens: int = Field(..., ge=0)
    total_estimated_tokens: int = Field(..., ge=0)
    requested_max_output_tokens: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def estimate_text_tokens(text: str) -> int:
    """Calculate a conservative upper-bound token count for a text string.

    Accounts for subword splitting behaviors across:
      - Bengali and Indic scripts (UTF-8 byte expansion: ~1.5 tokens/char)
      - CJK ideographs and syllables (unsegmented ideograms: ~1.5 tokens/char)
      - Emoji and pictographs (complex graphemes, ZWJ sequences: ~2.5 tokens/char)
      - Mathematical notation and code syntax (operators, brackets, syntax: ~1.0 token/char)
      - Long strings without whitespace (base64, hex hashes: ~1 token per 2.5 chars)
      - Standard Latin prose (max of 3.0 chars/token or 1.33 tokens/word)
    """
    if not text:
        return 0

    # Segregate character counts by script/type
    indic_count = len(_INDIC_PATTERN.findall(text))
    cjk_count = len(_CJK_PATTERN.findall(text))
    emoji_count = len(_EMOJI_PATTERN.findall(text))
    math_count = len(_MATH_SYMBOLS_PATTERN.findall(text))

    code_syntax_count = sum(1 for c in text if c in _CODE_SYNTAX_CHARS)

    # Count Latin/alphanumeric and whitespace
    # Filter out characters already accounted for above
    remaining_chars = (
        len(text) - indic_count - cjk_count - emoji_count - math_count - code_syntax_count
    )
    remaining_chars = max(0, remaining_chars)

    # Analyze whitespace segmentation for long unbroken sequences
    words = text.split()
    long_unbroken_penalty = 0
    for w in words:
        if len(w) > 20:
            # Long continuous sequence without spaces (e.g. hash, base64)
            # Tokenizers break these into 2-3 char chunks
            long_unbroken_penalty += max(1, math.ceil(len(w) / 2.5))

    # Base Latin estimate: max of char ratio and word ratio
    latin_tokens = (
        max(
            math.ceil(remaining_chars / 3.0),
            math.ceil(len(words) * 1.33),
        )
        if remaining_chars > 0
        else 0
    )

    # Weighted conservative sum
    raw_estimate = (
        latin_tokens
        + math.ceil(indic_count * 1.5)
        + math.ceil(cjk_count * 1.5)
        + math.ceil(emoji_count * 2.5)
        + math_count
        + code_syntax_count
        + long_unbroken_penalty
    )

    # Apply 15% safety margin upper bound
    safe_bound = math.ceil(raw_estimate * 1.15)
    return max(1, safe_bound)


def estimate_generation_tokens(
    messages: list[dict[str, str]],
    model_context_window: int,
    model_max_output_tokens: int,
    requested_max_output: int | None = None,
) -> TokenEstimate:
    """Calculate a conservative, server-validated token estimate across a chat conversation.

    Guarantees:
      - Operates purely server-side (never trusts client claims).
      - Applies script-aware conservative estimation across Indic, CJK, emoji, code, and math.
      - Includes an explicit 15% safety margin on input estimation.
      - Adds 4 tokens structural framing per message + 3 tokens system/conversation base.
      - Caps reserved output at model limits.
      - Verifies that input does not exceed the model context window (rejects upfront with ValueError).
    """
    total_input = 3  # Base primer / conversation framing tokens

    for msg in messages:
        content = msg.get("content", "")
        # Per-message structural token overhead (role indicator, delimiters)
        message_overhead = 4
        content_tokens = estimate_text_tokens(content) if content else 0
        total_input += message_overhead + content_tokens

    # Determine reserved output tokens
    if requested_max_output is not None and requested_max_output > 0:
        reserved_output = min(requested_max_output, model_max_output_tokens)
    else:
        # Default reservation: up to 2048 tokens or model max
        reserved_output = min(2048, model_max_output_tokens)

    # Ensure context window is respected
    if total_input > model_context_window:
        raise ValueError(
            f"Estimated input tokens ({total_input}) exceeds model context window ({model_context_window})"
        )

    # Adjust output if remaining context window is smaller than requested output
    available_output = model_context_window - total_input
    if available_output <= 0:
        raise ValueError(
            f"Input tokens ({total_input}) exhaust available context window ({model_context_window})"
        )

    effective_output = min(reserved_output, available_output)

    return TokenEstimate(
        estimator_type="conservative_multilingual_upper_bound_v2",
        is_approximate=True,
        safety_margin_ratio=0.15,
        estimated_input_tokens=total_input,
        reserved_output_tokens=effective_output,
        total_estimated_tokens=total_input + effective_output,
        requested_max_output_tokens=requested_max_output,
    )
