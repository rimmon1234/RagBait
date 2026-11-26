"""Approved Free-Model Verification Policy for Community-Shared Credentials.

Ensures that community-shared API keys (OpenRouter, Google Gemini, GroqCloud free-tier)
can ONLY be dispatched to explicitly approved free-tier model variants. Paid-only providers
(such as OpenAI and xAI) are strictly excluded from community dispatch.
"""

from typing import Final

# Explicit allowlist of approved OpenRouter free models.
# In accordance with official OpenRouter policy, free-tier models have identifiers ending in ':free'.
# References: https://openrouter.ai/collections/free-models
APPROVED_OPENROUTER_FREE_MODELS: Final[set[str]] = {
    "openrouter/meta-llama/llama-3.3-70b-instruct:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "openrouter/meta-llama/llama-3.2-3b-instruct:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "openrouter/google/gemini-2.0-flash-exp:free",
    "google/gemini-2.0-flash-exp:free",
    "openrouter/google/gemini-2.0-pro-exp-02-05:free",
    "google/gemini-2.0-pro-exp-02-05:free",
    "openrouter/qwen/qwen-2.5-coder-32b-instruct:free",
    "qwen/qwen-2.5-coder-32b-instruct:free",
    "openrouter/deepseek/deepseek-r1:free",
    "deepseek/deepseek-r1:free",
}

APPROVED_GEMINI_FREE_MODELS: Final[set[str]] = {
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
}

APPROVED_GROQ_FREE_MODELS: Final[set[str]] = {
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
}


def is_approved_free_model(provider_id: str, model_id: str) -> bool:
    """Check whether a model qualifies as an approved free-tier model for pooled community credentials.

    Rules:
    1. Provider must be an approved free-tier sharing provider (openrouter, gemini, groq).
    2. For OpenRouter, model ID must explicitly end with ':free' OR be in the approved allowlist
       (excluding unverified dynamic alias 'openrouter/free').
    3. For Gemini and Groq, model ID must match an approved free-tier model variant.
    4. Paid-only providers (e.g. openai, xai) and unlisted paid models are strictly rejected.
    """
    clean_provider = provider_id.lower().strip()
    clean_model = model_id.lower().strip()

    if clean_provider == "openrouter":
        # Dynamic router openrouter/free is not an explicit :free variant without fallback verification
        if clean_model in ("openrouter/free", "free"):
            return False
        if clean_model.endswith(":free"):
            return True
        if clean_model in {m.lower() for m in APPROVED_OPENROUTER_FREE_MODELS}:
            return True
        return False

    if clean_provider == "gemini":
        return clean_model in APPROVED_GEMINI_FREE_MODELS

    if clean_provider == "groq":
        return clean_model in APPROVED_GROQ_FREE_MODELS

    return False
