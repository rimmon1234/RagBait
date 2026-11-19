"""Tests for server-side conservative token estimation."""

import pytest

from app.modules.llm_gateway.token_estimation import estimate_generation_tokens


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

    assert estimate.estimator_type == "conservative_heuristic_v1"
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
    # Long message
    huge_text = "a" * 50000
    messages = [{"role": "user", "content": huge_text}]

    with pytest.raises(ValueError, match="exceeds model context window"):
        estimate_generation_tokens(
            messages=messages,
            model_context_window=4096,
            model_max_output_tokens=1024,
        )


def test_conservative_multiplier_and_empty_content():
    messages = [
        {"role": "user", "content": ""},
        {"role": "assistant", "content": "Yes"},
    ]
    estimate = estimate_generation_tokens(
        messages=messages,
        model_context_window=8192,
        model_max_output_tokens=1024,
    )
    assert estimate.estimated_input_tokens > 5
