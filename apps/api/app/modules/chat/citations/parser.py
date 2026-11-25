"""Deterministic regex parser for standard `[S1]` bracketed citation identifiers."""

import re

from app.modules.chat.citations.schemas import CitationParseResult

# Matches complete positive numeric bracketed citations like [S1], [S2], [S10]
# Negative lookbehind prevents matching identifier indexing such as arr[S1] or Foo_S[1].
_VALID_CITATION_RE = re.compile(r"(?<![A-Za-z0-9_])\[(S[1-9][0-9]*)\]")

# Matches malformed or non-canonical citation-like brackets (e.g., [SOURCE1], [s1], [S0], [S 1], [S-1])
_MALFORMED_CITATION_RE = re.compile(
    r"(?<![A-Za-z0-9_])\[(?:"
    r"(?:SOURCE|source|Source|SRC|src|REF|ref)[\s_-]*[0-9]+"
    r"|s[1-9][0-9]*"
    r"|S0+[0-9]*"
    r"|S-[0-9]+"
    r"|S\s+[0-9]+"
    r"|S[1-9][0-9]*(?:\s*,\s*S[1-9][0-9]*)+"
    r")\]"
)


def parse_citations(text: str) -> CitationParseResult:
    """Extract standard citation keys (`S1`, `S10`, ...) and detect malformed markers.

    Guarantees:
    - Multi-digit keys `[S10]` are extracted cleanly and never partially matched as `[S1]`.
    - Duplicate references preserve order of first appearance while logging duplicates.
    - Non-matching prose brackets like `[1]`, `"S1"`, or `arr[S1]` are ignored cleanly.
    """
    if not text:
        return CitationParseResult()

    raw_tokens: list[str] = []
    unique_keys: list[str] = []
    duplicate_keys: list[str] = []
    seen: set[str] = set()
    dup_seen: set[str] = set()

    for match in _VALID_CITATION_RE.finditer(text):
        key = match.group(1)
        raw_tokens.append(f"[{key}]")
        if key not in seen:
            seen.add(key)
            unique_keys.append(key)
        elif key not in dup_seen:
            dup_seen.add(key)
            duplicate_keys.append(key)

    malformed = [m.group(0) for m in _MALFORMED_CITATION_RE.finditer(text)]

    return CitationParseResult(
        raw_tokens=raw_tokens,
        unique_keys=unique_keys,
        duplicate_keys=duplicate_keys,
        malformed_tokens=malformed,
        total_occurrences=len(raw_tokens),
    )
