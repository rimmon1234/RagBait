"""Stage 2 equation-safe text normalization for PDF/text indexing representation.

Produces clean `normalized_text` alongside untouched `raw_text`:
- Applies Unicode NFC (never blind NFKC) so superscripts, subscripts, negative
  signs, math operators, truth tables, and Indic scripts stay intact.
- Repairs standard Latin presentation ligatures (fi/fl/ff/ffi/ffl) and removes
  invisible soft-hyphen/BOM/ZWSP artifacts while preserving ZWNJ/ZWJ.
- Removes repeating document headers/footers and slide-number bleed without
  corrupting equations, counts, or labeled step/value tokens.
- Standardizes slide bullet glyphs cleanly and de-hyphenates wrapped prose words
  while keeping equations, tables, headings, and paragraph boundaries intact.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from app.modules.knowledge_base.extraction_diagnostics import _detect_repeated_edge_lines

NORMALIZATION_VERSION = 1

_LIGATURES: tuple[tuple[str, str], ...] = (
    ("\ufb00", "ff"),
    ("\ufb01", "fi"),
    ("\ufb02", "fl"),
    ("\ufb03", "ffi"),
    ("\ufb04", "ffl"),
    ("\ufb05", "st"),
    ("\ufb06", "st"),
)
_STRIP_INVISIBLE_RE = re.compile(r"[\u00ad\u200b\ufeff\x00]")
_DEHYPHENATE_RE = re.compile(r"([A-Za-z]{2,})-\n[ \t]*([a-z]{2,})")
_PAGE_NUMBER_LINE_RE = re.compile(r"^\d{1,4}$")
_SAFE_TRAILING_LABEL_WORDS = frozenset(
    {
        "bit",
        "case",
        "chapter",
        "class",
        "col",
        "column",
        "eq",
        "equation",
        "example",
        "fig",
        "figure",
        "group",
        "item",
        "lecture",
        "level",
        "line",
        "mod",
        "module",
        "node",
        "order",
        "page",
        "part",
        "pass",
        "phase",
        "row",
        "rule",
        "sec",
        "section",
        "set",
        "slide",
        "stage",
        "state",
        "step",
        "table",
        "type",
        "unit",
        "value",
        "version",
    }
)
_TOP_BULLET_RE = re.compile(r"^\s*[•●]\s+")
_SUB_BULLET_RE = re.compile(r"^\s*[❖➢▪▫]\s+")
_RING_SUB_BULLET_RE = re.compile(r"^\s*o\s+(?=[A-Z0-9])")
_LIST_START_RE = re.compile(r"^\s*(?:[-*]|\d+[.)]|\([a-zA-Z0-9]+\))\s+")
_EQUATION_OR_TABLE_RE = re.compile(r"(?:<=|>=|!=|:=|->|<-|<->|[=|+×÷±≤≥≠→↔∧∨⊕¬∑∏∫≈]|\S {3,}\S)")
_HEADING_END_RE = re.compile(r"[:.!?।॥]$")


@dataclass(frozen=True)
class NormalizedPageResult:
    """Normalized indexing text plus deterministic transformation stats."""

    page_number: int
    normalized_text: str
    normalization_version: int
    stats: dict[str, int]


def _looks_like_equation_or_table_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if _EQUATION_OR_TABLE_RE.search(stripped):
        return True
    # Binary / truth table rows like "0 1 0 1"
    if re.fullmatch(r"(?:[01xX\-]+\s+){2,}[01xX\-]+", stripped):
        return True
    return False


def _replace_ligatures_and_nfc(text: str) -> tuple[str, int]:
    nfc = unicodedata.normalize("NFC", text)
    cleaned = _STRIP_INVISIBLE_RE.sub("", nfc)
    ligature_fixes = 0
    for src, dst in _LIGATURES:
        if src in cleaned:
            ligature_fixes += cleaned.count(src)
            cleaned = cleaned.replace(src, dst)
    # Normalize CR/LF consistently
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    return cleaned, ligature_fixes


def _strip_edge_boilerplate(
    lines: list[str],
    *,
    page_number: int,
    repeated_headers: set[str],
    repeated_footers: set[str],
) -> tuple[list[str], int, int]:
    """Remove repeated slide headers/footers and slide-number bleed safely."""
    removed_bp = 0
    stripped_page_nums = 0
    out = list(lines)

    if out and out[0].strip() in repeated_headers:
        out = out[1:]
        removed_bp += 1

    # Strip trailing standalone page number line when it matches slide numbering
    if out:
        last_s = out[-1].strip()
        if _PAGE_NUMBER_LINE_RE.match(last_s) and int(last_s) == page_number:
            out = out[:-1]
            stripped_page_nums += 1

    # Strip repeated footer right above/at bottom
    if out and out[-1].strip() in repeated_footers:
        out = out[:-1]
        removed_bp += 1

    # Strip inline slide number appended to last prose line (e.g. "...at the same time 18")
    if out and not _looks_like_equation_or_table_line(out[-1]):
        match = re.match(
            rf"^(.+?\b([A-Za-z]{{2,}}))\s+{page_number}\s*$",
            out[-1],
        )
        if match:
            prev_word = match.group(2).lower()
            prefix_text = match.group(1)
            # Only strip if prefix isn't tiny title text and preceding word isn't a numeric label
            if (
                prev_word not in _SAFE_TRAILING_LABEL_WORDS
                and len(prefix_text.split()) >= 3
                and not prefix_text.rstrip().endswith((":", "=", "-", "+", "(", ","))
            ):
                out[-1] = prefix_text.rstrip()
                stripped_page_nums += 1

    return out, removed_bp, stripped_page_nums


def _standardize_bullets(lines: list[str]) -> tuple[list[str], int]:
    """Standardize non-ASCII slide bullets into Markdown list hierarchy."""
    converted: list[str] = []
    bullet_count = 0
    for line in lines:
        if _TOP_BULLET_RE.match(line):
            converted.append(_TOP_BULLET_RE.sub("- ", line, count=1).rstrip())
            bullet_count += 1
        elif _SUB_BULLET_RE.match(line):
            converted.append("  - " + _SUB_BULLET_RE.sub("", line, count=1).rstrip())
            bullet_count += 1
        elif _RING_SUB_BULLET_RE.match(line):
            converted.append("    - " + _RING_SUB_BULLET_RE.sub("", line, count=1).rstrip())
            bullet_count += 1
        else:
            converted.append(line.rstrip())
    return converted, bullet_count


def _should_join_wrapped_lines(prev_line: str, next_line: str) -> bool:
    """True only when `next_line` is a continuation of wrapped prose in `prev_line`."""
    prev_s = prev_line.strip()
    next_s = next_line.strip()
    if not prev_s or not next_s:
        return False

    if _LIST_START_RE.match(next_line) or _LIST_START_RE.match(prev_s):
        # Allow continuation line under an open bullet item when next line is indented/lowercase
        # or clearly continues an incomplete wrapped bullet phrase (e.g. "hardware resources for a given\ndesign")
        if _LIST_START_RE.match(next_line):
            return False
    if _HEADING_END_RE.search(prev_s):
        return False
    if _looks_like_equation_or_table_line(prev_s) or _looks_like_equation_or_table_line(next_s):
        return False

    # Never join short title lines (likely section heading) into next block unless next starts lowercase
    if len(prev_s) < 42 and not _LIST_START_RE.match(prev_s) and next_s[0].isupper():
        return False

    # Join if next line clearly continues an unfinished lowercase phrase or prepositional tail
    if prev_s.endswith((" -", " +")):
        return False
    if next_s[0].islower():
        return True

    # Also join wrapped prose mid-sentence before capitalized acronym/term when prev ends cleanly without terminator
    if prev_s.endswith((",", "and", "or", "of", "the", "a", "an", "to", "in", "for", "with")):
        return True

    return False


def _merge_soft_wraps(lines: list[str]) -> tuple[list[str], int, int]:
    """Unwrap wrapped prose lines while protecting formulas, tables, headings, and lists."""
    merged: list[str] = []
    joins = 0
    eq_preserved = 0

    for raw_line in lines:
        stripped = raw_line.strip()
        if _looks_like_equation_or_table_line(raw_line):
            eq_preserved += 1
        if not stripped:
            if merged and merged[-1] != "":
                merged.append("")
            continue

        if not merged or merged[-1] == "":
            merged.append(raw_line.rstrip())
            continue

        prev = merged[-1]
        if _should_join_wrapped_lines(prev, raw_line):
            merged[-1] = f"{prev.rstrip()} {stripped}"
            joins += 1
        else:
            merged.append(raw_line.rstrip())

    return merged, joins, eq_preserved


def normalize_page_text(
    raw_text: str,
    *,
    page_number: int,
    repeated_headers: set[str] | None = None,
    repeated_footers: set[str] | None = None,
) -> NormalizedPageResult:
    """Normalize a single page's text while keeping math notation and structure intact."""
    cleaned, ligature_fixes = _replace_ligatures_and_nfc(raw_text)

    # First strip boilerplate headers/footers at line granularity before de-hyphenation
    lines_no_bp, removed_bp, stripped_nums = _strip_edge_boilerplate(
        [ln.rstrip() for ln in cleaned.split("\n") if ln.strip()],
        page_number=page_number,
        repeated_headers=repeated_headers or set(),
        repeated_footers=repeated_footers or set(),
    )

    # Rejoin split alphabetic English hyphenations only across lowercase suffix lines
    rejoined_block, dehyph_count = _DEHYPHENATE_RE.subn(r"\1\2", "\n".join(lines_no_bp))
    stage_lines = rejoined_block.split("\n") if rejoined_block.strip() else []
    bulleted_lines, bullet_count = _standardize_bullets(stage_lines)
    unwrapped_lines, joins, eq_preserved = _merge_soft_wraps(bulleted_lines)

    # Collapse spaces around inline bullet runs cleanly while preserving tabular gaps
    final_lines: list[str] = []
    for ln in unwrapped_lines:
        if ln == "":
            if final_lines and final_lines[-1] != "":
                final_lines.append("")
            continue
        if _looks_like_equation_or_table_line(ln):
            final_lines.append(ln.rstrip())
        else:
            # Preserve leading bullet indent (up to 4 spaces) while tidying internal runs
            leading = len(ln) - len(ln.lstrip(" "))
            indent = " " * min(leading, 4)
            body = re.sub(r"[ \t]{2,}", " ", ln.strip())
            final_lines.append(f"{indent}{body}")

    normalized_text = "\n".join(final_lines).strip()
    return NormalizedPageResult(
        page_number=page_number,
        normalized_text=normalized_text,
        normalization_version=NORMALIZATION_VERSION,
        stats={
            "ligatures_repaired": ligature_fixes,
            "dehyphenations": dehyph_count,
            "boilerplate_lines_removed": removed_bp,
            "trailing_page_numbers_stripped": stripped_nums,
            "bullets_standardized": bullet_count,
            "soft_wraps_joined": joins,
            "equations_preserved_lines": eq_preserved,
        },
    )


def normalize_document_pages(
    pages: list[dict[str, Any]],
    *,
    header_min_page_ratio: float = 0.25,
) -> list[NormalizedPageResult]:
    """Normalize an ordered list of extracted page dicts without mutating `raw_text`."""
    raw_texts = [str(p.get("raw_text", "")) for p in pages]
    repeated_headers, repeated_footers = _detect_repeated_edge_lines(
        raw_texts,
        min_ratio=header_min_page_ratio,
    )
    results: list[NormalizedPageResult] = []
    for p in pages:
        results.append(
            normalize_page_text(
                str(p.get("raw_text", "")),
                page_number=int(p["page_number"]),
                repeated_headers=repeated_headers,
                repeated_footers=repeated_footers,
            )
        )
    return results
