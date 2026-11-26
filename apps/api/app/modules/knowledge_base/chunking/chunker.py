"""Page-aware deterministic text chunker with physical page provenance.

Splits document page contents into chunks respecting:
1. Target Chunk Size: 350-400 tokens (TARGET_CHUNK_TOKENS = 380).
2. Hard Token Ceiling: <= 420 tokens (MAX_RAW_CHUNK_TOKENS = 420).
3. Overlap: 50-70 tokens (TARGET_OVERLAP_TOKENS = 60).
4. Physical Page Provenance: page_number_start, page_number_end, page_spans.
5. Multilingual Sentence Boundary Support: English, Bengali (।), Hindi (।, ॥).
6. Deterministic Content Hashing: sha256 of raw_text.
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.modules.knowledge_base.chunking.tokenizer import (
    MAX_RAW_CHUNK_TOKENS,
    TARGET_CHUNK_TOKENS,
    TARGET_OVERLAP_TOKENS,
    count_tokens,
)

logger = logging.getLogger("ragbait.kb.chunking.chunker")

# Sentence boundary regex supporting Latin, Bengali (।), Hindi/Devanagari (।, ॥)
SENTENCE_SPLIT_REGEX = re.compile(r"(?<=[.!?\n।॥])\s+")
CLAUSE_SPLIT_REGEX = re.compile(r"(?<=[;,:\—\-])\s+")


@dataclass(frozen=True)
class PageInput:
    """Input extracted page record from public.kb_document_pages."""

    page_number: int
    text: str
    has_text: bool = True
    source_kind: str = "text_layer"


@dataclass(frozen=True)
class TextUnit:
    """Atomic fragment of text with exact physical page location."""

    text: str
    token_count: int
    page_number: int
    start_char: int
    end_char: int
    source_kind: str = "text_layer"


@dataclass(frozen=True)
class ChunkOutput:
    """Deterministic chunk ready for public.kb_document_chunks."""

    chunk_index: int
    page_number_start: int
    page_number_end: int
    raw_text: str
    character_count: int
    token_count: int
    content_hash: str
    page_spans: list[dict[str, Any]] = field(default_factory=list)
    source_kind: str = "text_layer"


def _split_sentence_into_clauses(
    sentence: str,
    page_number: int,
    base_char_offset: int,
) -> list[TextUnit]:
    """Split an oversized sentence into clause-level units."""
    clauses = CLAUSE_SPLIT_REGEX.split(sentence)
    units: list[TextUnit] = []
    char_offset = base_char_offset

    for clause in clauses:
        clause_str = clause.strip()
        if not clause_str:
            char_offset += len(clause)
            continue

        c_tokens = count_tokens(clause_str)
        if c_tokens <= MAX_RAW_CHUNK_TOKENS:
            units.append(
                TextUnit(
                    text=clause_str,
                    token_count=c_tokens,
                    page_number=page_number,
                    start_char=char_offset,
                    end_char=char_offset + len(clause_str),
                )
            )
            char_offset += len(clause)
        else:
            # Fallback: Clause still exceeds 420 tokens; slice by whitespace or words
            words = clause_str.split(" ")
            current_words: list[str] = []
            word_start_offset = char_offset

            for word in words:
                candidate = " ".join([*current_words, word])
                cand_tokens = count_tokens(candidate)
                if cand_tokens <= MAX_RAW_CHUNK_TOKENS:
                    current_words.append(word)
                else:
                    if current_words:
                        slice_text = " ".join(current_words)
                        units.append(
                            TextUnit(
                                text=slice_text,
                                token_count=count_tokens(slice_text),
                                page_number=page_number,
                                start_char=word_start_offset,
                                end_char=word_start_offset + len(slice_text),
                            )
                        )
                        word_start_offset += len(slice_text) + 1
                    current_words = [word]

            if current_words:
                slice_text = " ".join(current_words)
                slice_tokens = count_tokens(slice_text)
                # If a single word itself exceeds MAX_RAW_CHUNK_TOKENS (e.g. 500-char string)
                if slice_tokens > MAX_RAW_CHUNK_TOKENS:
                    # Character-level slicing fallback
                    step = 300
                    for s_idx in range(0, len(slice_text), step):
                        sub = slice_text[s_idx : s_idx + step]
                        units.append(
                            TextUnit(
                                text=sub,
                                token_count=count_tokens(sub),
                                page_number=page_number,
                                start_char=word_start_offset + s_idx,
                                end_char=word_start_offset + s_idx + len(sub),
                            )
                        )
                else:
                    units.append(
                        TextUnit(
                            text=slice_text,
                            token_count=slice_tokens,
                            page_number=page_number,
                            start_char=word_start_offset,
                            end_char=word_start_offset + len(slice_text),
                        )
                    )
            char_offset += len(clause)

    return units


def _extract_page_units(page: PageInput) -> list[TextUnit]:
    """Break a page into atomic TextUnits (paragraphs / sentences)."""
    raw = page.text
    if not raw or not raw.strip():
        return []

    units: list[TextUnit] = []
    # Normalize double newlines into paragraph markers
    paragraphs = re.split(r"\n\s*\n", raw)
    page_char_offset = 0

    for paragraph in paragraphs:
        p_text = paragraph.strip()
        if not p_text:
            page_char_offset += len(paragraph) + 2
            continue

        p_tokens = count_tokens(p_text)
        if p_tokens <= TARGET_CHUNK_TOKENS:
            # Paragraph fits within target size
            units.append(
                TextUnit(
                    text=p_text,
                    token_count=p_tokens,
                    page_number=page.page_number,
                    start_char=page_char_offset,
                    end_char=page_char_offset + len(p_text),
                )
            )
        else:
            # Split large paragraph into sentences
            sentences = SENTENCE_SPLIT_REGEX.split(p_text)
            sent_offset = page_char_offset

            for sentence in sentences:
                s_text = sentence.strip()
                if not s_text:
                    sent_offset += len(sentence)
                    continue

                s_tokens = count_tokens(s_text)
                if s_tokens <= MAX_RAW_CHUNK_TOKENS:
                    units.append(
                        TextUnit(
                            text=s_text,
                            token_count=s_tokens,
                            page_number=page.page_number,
                            start_char=sent_offset,
                            end_char=sent_offset + len(s_text),
                        )
                    )
                else:
                    # Oversized sentence -> split at clauses
                    clause_units = _split_sentence_into_clauses(
                        sentence=s_text,
                        page_number=page.page_number,
                        base_char_offset=sent_offset,
                    )
                    units.extend(clause_units)

                sent_offset += len(sentence)

        page_char_offset += len(paragraph) + 2

    return units


def chunk_document_pages(pages: list[PageInput]) -> list[ChunkOutput]:
    """Deterministically segment a document's extracted pages into bounded chunks.

    Guarantees:
    - Target: 350-400 tokens per chunk.
    - Hard Ceiling: Every chunk token count <= 420.
    - Overlap: ~60 tokens preserved between consecutive chunks.
    - Physical page provenance preserved on every chunk.
    """
    valid_pages = [p for p in pages if p.has_text and p.text and p.text.strip()]
    if not valid_pages:
        return []

    # 1. Collect all atomic units across all document pages
    all_units: list[TextUnit] = []
    for page in valid_pages:
        page_units = _extract_page_units(page)
        for u in page_units:
            all_units.append(
                TextUnit(
                    text=u.text,
                    token_count=u.token_count,
                    page_number=u.page_number,
                    start_char=u.start_char,
                    end_char=u.end_char,
                    source_kind=page.source_kind,
                )
            )

    if not all_units:
        return []

    chunks: list[ChunkOutput] = []
    unit_idx = 0
    total_units = len(all_units)
    chunk_index = 0

    while unit_idx < total_units:
        current_units: list[TextUnit] = []
        accumulated_text = ""

        # Accumulate units until reaching target tokens or max ceiling
        scan_idx = unit_idx
        while scan_idx < total_units:
            next_unit = all_units[scan_idx]
            separator = "\n\n" if (accumulated_text and "\n" not in accumulated_text[-2:]) else " "
            candidate_text = (
                f"{accumulated_text}{separator}{next_unit.text}".strip()
                if accumulated_text
                else next_unit.text
            )
            cand_tokens = count_tokens(candidate_text)

            if cand_tokens <= MAX_RAW_CHUNK_TOKENS:
                current_units.append(next_unit)
                accumulated_text = candidate_text
                scan_idx += 1

                # If we've reached the target token range, finish chunk
                if cand_tokens >= TARGET_CHUNK_TOKENS:
                    break
            else:
                # Next unit would exceed ceiling
                if not current_units:
                    # Single unit exceeds MAX_RAW_CHUNK_TOKENS (should not happen due to pre-splitting)
                    current_units.append(next_unit)
                    accumulated_text = next_unit.text
                    scan_idx += 1
                break

        if not current_units:
            break

        final_text = accumulated_text.strip()
        final_token_count = count_tokens(final_text)

        # Enforce hard ceiling safety back-off
        while final_token_count > MAX_RAW_CHUNK_TOKENS and len(current_units) > 1:
            current_units.pop()
            scan_idx -= 1
            final_text = "\n\n".join(u.text for u in current_units).strip()
            final_token_count = count_tokens(final_text)

        if not final_text:
            unit_idx = max(unit_idx + 1, scan_idx)
            continue

        # Physical page provenance calculation
        p_start = min(u.page_number for u in current_units)
        p_end = max(u.page_number for u in current_units)

        # Build aggregated page spans
        spans_by_page: dict[int, list[int]] = {}
        for u in current_units:
            if u.page_number not in spans_by_page:
                spans_by_page[u.page_number] = [u.start_char, u.end_char]
            else:
                spans_by_page[u.page_number][0] = min(spans_by_page[u.page_number][0], u.start_char)
                spans_by_page[u.page_number][1] = max(spans_by_page[u.page_number][1], u.end_char)

        page_spans = [
            {"page_number": p_num, "start_char": rng[0], "end_char": rng[1]}
            for p_num, rng in sorted(spans_by_page.items())
        ]

        content_hash = hashlib.sha256(final_text.encode("utf-8")).hexdigest()
        kinds = {u.source_kind for u in current_units}
        if kinds == {"text_layer"}:
            chunk_source_kind = "text_layer"
        elif len(kinds) == 1:
            chunk_source_kind = next(iter(kinds))
        else:
            chunk_source_kind = "multimodal_combined"

        chunks.append(
            ChunkOutput(
                chunk_index=chunk_index,
                page_number_start=p_start,
                page_number_end=p_end,
                raw_text=final_text,
                character_count=len(final_text),
                token_count=final_token_count,
                content_hash=content_hash,
                page_spans=page_spans,
                source_kind=chunk_source_kind,
            )
        )
        chunk_index += 1

        if scan_idx >= total_units:
            # Processed all units
            break

        # Calculate overlap for the next chunk
        # Walk backwards from scan_idx to find units summing up to TARGET_OVERLAP_TOKENS
        overlap_units: list[TextUnit] = []
        overlap_tokens = 0
        overlap_walk = scan_idx - 1

        while overlap_walk >= unit_idx and overlap_walk >= 0:
            u = all_units[overlap_walk]
            if overlap_tokens + u.token_count <= TARGET_OVERLAP_TOKENS:
                overlap_units.insert(0, u)
                overlap_tokens += u.token_count
                overlap_walk -= 1
            else:
                break

        # Advance unit_idx ensuring forward progress:
        # At minimum, advance by at least 1 unit beyond current unit_idx
        next_unit_idx = scan_idx - len(overlap_units)
        if next_unit_idx <= unit_idx:
            next_unit_idx = unit_idx + 1

        unit_idx = next_unit_idx

    return chunks
