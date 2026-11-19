"""Tests for server-side conservative multilingual token estimation."""

import pytest

from app.modules.llm_gateway.token_estimation import (
    estimate_generation_tokens,
    estimate_text_tokens,
)


def test_basic_token_estimation():
    messages = [
        {"role": "system", "content": "You are a concise tutor."},
        {"role": "user", "content": "What is 2+2?"},
    ]
    estimate = estimate_generation_tokens(
        messages=messages,
        model_context_window=8192,
        model_max_output_tokens=2048,
        requested_max_output=500,
    )

    assert estimate.estimator_type == "conservative_multilingual_upper_bound_v2"
    assert estimate.is_approximate is True
    assert estimate.safety_margin_ratio == 0.15
    assert estimate.estimated_input_tokens > 0
    assert estimate.reserved_output_tokens == 500
    assert estimate.total_estimated_tokens == estimate.estimated_input_tokens + 500


def test_output_capped_at_model_max():
    messages = [{"role": "user", "content": "Hello"}]
    estimate = estimate_generation_tokens(
        messages=messages,
        model_context_window=8192,
        model_max_output_tokens=1000,
        requested_max_output=4000,
    )
    # Capped at model_max_output_tokens
    assert estimate.reserved_output_tokens == 1000


def test_default_output_reservation():
    messages = [{"role": "user", "content": "Hello"}]
    estimate = estimate_generation_tokens(
        messages=messages,
        model_context_window=8192,
        model_max_output_tokens=4096,
        requested_max_output=None,
    )
    # Default is min(2048, 4096) = 2048
    assert estimate.reserved_output_tokens == 2048


def test_input_exceeds_context_window_rejected():
    huge_text = "a" * 50000
    messages = [{"role": "user", "content": huge_text}]

    with pytest.raises(ValueError, match="exceeds model context window"):
        estimate_generation_tokens(
            messages=messages,
            model_context_window=4096,
            model_max_output_tokens=1024,
        )


def test_bengali_and_indic_scripts():
    """Bengali / Indic script takes ~1.5 tokens per char due to UTF-8 subwords."""
    bengali_text = "আমি বাংলায় গান গাই, আমি বাংলার গান গাই।"
    tokens = estimate_text_tokens(bengali_text)
    # 40 chars * 1.5 * 1.15 >= 60 tokens, avoiding naive 40/3.2 = 12 undercount
    assert tokens >= 50
    assert tokens > len(bengali_text)  # Guaranteed conservative upper bound


def test_chinese_japanese_text():
    """CJK ideographs and kana require dedicated weighting."""
    cjk_text = "人工智能与机器学习模型在自然语言处理中的应用"
    tokens = estimate_text_tokens(cjk_text)
    # 22 chars: naive 22/3.2 = 6, but CJK has 1-2 tokens per ideograph
    assert tokens >= 30


def test_emoji_sequences():
    """Emoji and pictographs require conservative weighting for multi-codepoint sequences."""
    emoji_text = "🚀🌟🎉👨‍👩‍👧‍👦🤖✨🔥"
    tokens = estimate_text_tokens(emoji_text)
    assert tokens >= 20


def test_source_code():
    """Source code contains syntax symbols, braces, brackets, and indentation."""
    code_text = "def calculate_loss(y_true: torch.Tensor, y_pred: torch.Tensor) -> torch.Tensor:\n    return torch.mean((y_true - y_pred) ** 2)"
    tokens = estimate_text_tokens(code_text)
    # Code with operators, parentheses, arrows, brackets must be conservatively estimated
    assert tokens >= 50


def test_mathematical_notation():
    """Mathematical notation and LaTeX commands contain dense operators and symbols."""
    math_text = r"\int_{-\infty}^{\infty} e^{-x^2} \, dx = \sqrt{\pi} \quad \text{and} \quad \sum_{n=1}^{\infty} \frac{1}{n^2} = \frac{\pi^2}{6}"
    tokens = estimate_text_tokens(math_text)
    assert tokens >= 60


def test_long_strings_without_spaces():
    """Long base64 or hex hashes without whitespace are tokenized into 2-3 char chunks."""
    unbroken_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    tokens = estimate_text_tokens(unbroken_hash)
    # 64 chars without spaces: penalty ensures at least ~30 tokens
    assert tokens >= 30


def test_mixed_language_input():
    """Mixed language combining English, Bengali, CJK, Emoji, and Math."""
    mixed_text = "Hello বিশ্ব! 机器学习 is fun 🚀: x = y^2 + 5"
    tokens = estimate_text_tokens(mixed_text)
    assert tokens >= 30
