"""Approved Free-Model Verification Policy for Community-Shared Credentials (M4.2).

Ensures that community-shared API keys (e.g. OpenRouter verified free tier)
can ONLY be dispatched to explicitly approved zero-cost free model variants.
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


def is_approved_free_model(provider_id: str, model_id: str) -> bool:
    """Check whether a model qualifies as an approved free-tier model for pooled community credentials.

    Rules:
    1. Provider must be an approved sharing provider (currently OpenRouter).
    2. Model ID must explicitly end with ':free' OR be in the approved allowlist.
    3. Non-free models (e.g. gpt-4o, claude-3-5-sonnet, gemini-3.8-flash) or zero-price models
       without explicit ':free' variant classification are strictly rejected for community dispatch.
    """
    clean_provider = provider_id.lower().strip()
    clean_model = model_id.lower().strip()

    if clean_provider == "openrouter":
        # Dynamic router openrouter/free is not an explicit :free variant without fallback verification
        if clean_model in ("openrouter/free", "free"):
            return False
        # Exact :free suffix check
        if clean_model.endswith(":free"):
            return True
        # Allowlist check
        if clean_model in {m.lower() for m in APPROVED_OPENROUTER_FREE_MODELS}:
            return True
        return False

    return False
