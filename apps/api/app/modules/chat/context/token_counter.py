"""Model-aware token counting isolated from the M4.3 embedding tokenizer.

CRITICAL DESIGN RULE (M4.5 Phase C2):
The M4.3 SentencePiece/WordPiece tokenizer (`intfloat/multilingual-e5-small`)
exists strictly for embedding chunk length checks and MUST NOT be reused for
counting arbitrary LLM generation prompts.

For generation models where an exact native BPE/SentencePiece tokenizer is not
loaded in-process, this module uses `llm_gateway.token_estimation`, a script-aware
conservative multilingual upper-bound estimator (accounting for Indic/Bengali/Hindi,
CJK, emoji, math operators, code syntax, unbroken sequences, and a +15% safety margin)
plus full structural message framing overhead (+4 per message, +3 conversation primer).
"""

from app.modules.chat.context.schemas import ModelCapability
from app.modules.llm_gateway.token_estimation import estimate_text_tokens


class ModelTokenCounter:
    """Counts tokens accurately across prompt sections and serialized chat messages."""

    def __init__(self, capability: ModelCapability) -> None:
        self.capability = capability

    @property
    def strategy(self) -> str:
        """Return documented token counting strategy for this model capability."""
        return self.capability.token_counting_strategy

    @property
    def is_exact(self) -> bool:
        """Indicate whether an exact native tokenizer is active."""
        return self.capability.exact_tokenizer_available

    def count_text(self, text: str) -> int:
        """Count tokens in a plain or formatted text string.

        Applies documented script-aware conservative estimation so Bengali,
        Hindi, formulas, code blocks, and XML delimiters never undercount.
        """
        if not text:
            return 0
        return estimate_text_tokens(text)

    def count_message(self, role: str, content: str) -> int:
        """Count tokens for a single chat message including role framing overhead."""
        _ = role  # Role name contributes to per-message delimiter framing
        body_tokens = self.count_text(content) if content else 0
        return self.capability.message_overhead_tokens + body_tokens

    def count_messages(self, messages: list[dict[str, str]]) -> int:
        """Count total input tokens across all serialized chat messages + primer."""
        total = self.capability.primer_overhead_tokens
        for msg in messages:
            total += self.count_message(msg.get("role", "user"), msg.get("content", ""))
        return total


def build_token_counter(capability: ModelCapability) -> ModelTokenCounter:
    """Construct a ModelTokenCounter bound to the validated ModelCapability."""
    return ModelTokenCounter(capability=capability)
