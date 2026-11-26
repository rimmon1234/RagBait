"""Server-side citation validator and bounded repair prompt generator."""

import re
from collections.abc import Iterable
from typing import Literal

from app.modules.chat.citations.parser import parse_citations
from app.modules.chat.citations.schemas import CitationValidationResult


def strip_invalid_citations(
    text: str,
    invalid_keys: Iterable[str],
    malformed_tokens: Iterable[str],
) -> str:
    """Remove unsupported or malformed citation markers cleanly without corrupting prose."""
    cleaned = text
    for key in sorted(set(invalid_keys), key=len, reverse=True):
        pattern = re.compile(rf"(?<![A-Za-z0-9_])\s*\[{re.escape(key)}\]")
        cleaned = pattern.sub("", cleaned)

    for tok in sorted(set(malformed_tokens), key=len, reverse=True):
        pattern = re.compile(rf"\s*{re.escape(tok)}")
        cleaned = pattern.sub("", cleaned)

    # Clean up extra horizontal whitespace left before punctuation or double spaces per line
    lines = []
    for line in cleaned.splitlines():
        line_clean = re.sub(r"[ \t]{2,}", " ", line)
        line_clean = re.sub(r"[ \t]+([.,;:!?])", r"\1", line_clean)
        lines.append(line_clean.rstrip())
    return "\n".join(lines).strip()


def validate_citations(
    answer_text: str,
    included_keys: Iterable[str],
    *,
    included_web_keys: Iterable[str] = (),
) -> CitationValidationResult:
    """Validate generated note ([Sn]) and web ([Wn]) citations against server registries.

    Rules:
    - Note keys ([S1]..) are permitted ONLY if in `included_keys`.
    - Web keys ([W1]..) are permitted ONLY if in `included_web_keys`.
    - Unknown keys (e.g. [S99], [W9], or excluded retrieval candidates) are rejected.
    - General knowledge prose without fabricated tags passes without requiring fake citations.
    - Malformed markers (e.g. [SOURCE1], [WEB1]) are flagged.
    """
    if not answer_text or not answer_text.strip():
        return CitationValidationResult(
            is_valid=False,
            status="invalid_empty_response",
            valid_keys=[],
            valid_note_keys=[],
            valid_web_keys=[],
            unknown_keys=[],
            duplicate_keys=[],
            malformed_tokens=[],
            sanitized_text="",
            total_occurrences=0,
        )

    allowed_notes = set(included_keys)
    allowed_web = set(included_web_keys)
    parsed = parse_citations(answer_text)

    valid_note_keys = [k for k in parsed.unique_keys if k.startswith("S") and k in allowed_notes]
    valid_web_keys = [k for k in parsed.unique_keys if k.startswith("W") and k in allowed_web]
    valid_keys = [k for k in parsed.unique_keys if k in allowed_notes or k in allowed_web]
    unknown_keys = [
        k for k in parsed.unique_keys if k not in allowed_notes and k not in allowed_web
    ]
    has_malformed = len(parsed.malformed_tokens) > 0

    if not unknown_keys and not has_malformed:
        valid_status: Literal["valid_grounded", "valid_no_sources"] = (
            "valid_grounded" if (allowed_notes or allowed_web) else "valid_no_sources"
        )
        return CitationValidationResult(
            is_valid=True,
            status=valid_status,
            valid_keys=valid_keys,
            valid_note_keys=valid_note_keys,
            valid_web_keys=valid_web_keys,
            unknown_keys=[],
            duplicate_keys=parsed.duplicate_keys,
            malformed_tokens=[],
            sanitized_text=answer_text.strip(),
            total_occurrences=parsed.total_occurrences,
        )

    sanitized = strip_invalid_citations(answer_text, unknown_keys, parsed.malformed_tokens)
    invalid_status: Literal["invalid_unknown_citations", "invalid_malformed_citations"] = (
        "invalid_unknown_citations" if unknown_keys else "invalid_malformed_citations"
    )

    return CitationValidationResult(
        is_valid=False,
        status=invalid_status,
        valid_keys=valid_keys,
        valid_note_keys=valid_note_keys,
        valid_web_keys=valid_web_keys,
        unknown_keys=unknown_keys,
        duplicate_keys=parsed.duplicate_keys,
        malformed_tokens=parsed.malformed_tokens,
        sanitized_text=sanitized,
        total_occurrences=parsed.total_occurrences,
    )


def build_citation_repair_messages(
    *,
    original_answer: str,
    valid_included_keys: list[str],
    unknown_keys: list[str],
    malformed_tokens: list[str],
    valid_web_keys: list[str] = (),  # type: ignore[assignment]
) -> list[dict[str, str]]:
    """Build a tightly bounded single repair prompt preserving general knowledge & fixing bad tags."""
    combined_allowed = [*valid_included_keys, *list(valid_web_keys)]
    allowed_desc = (
        ", ".join(f"[{k}]" for k in combined_allowed)
        if combined_allowed
        else "NONE (zero source citations permitted; remove all bracketed [S...] or [W...] tags while keeping helpful academic explanations)"
    )
    bad_items = ", ".join([*(f"[{k}]" for k in unknown_keys), *malformed_tokens])

    return [
        {
            "role": "system",
            "content": (
                "You are performing a strict citation-syntax correction pass. "
                "Preserve valid general-knowledge explanations and formatting accurately, "
                f"and REMOVE or fix ONLY invalid/malformed citation markers ({bad_items}). "
                f"Permitted citation markers for this response are strictly: {allowed_desc}. "
                "Do NOT invent any new sources, URLs, or numbers. Output ONLY the corrected response."
            ),
        },
        {
            "role": "user",
            "content": f"Correct the citation markers in this draft response:\n\n{original_answer}",
        },
    ]
