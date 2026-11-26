"""Stage 5 trusted Visual Understanding service for diagrams, flowcharts, tables, and equations.

Executes strictly outside the untrusted parser container using rendered PNGs
produced by Stage 4. Keeps derived AI visual interpretations completely separate
from `raw_text`, `normalized_text`, and `ocr_text`, storing model/version,
confidence, triage provenance, and failure status on each physical page record.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

from app.modules.knowledge_base.ocr_processor import OCR_ELIGIBLE_TRIAGE, RENDER_ELIGIBLE_TRIAGE
from app.modules.llm_gateway.errors import ProviderAdapterError, map_http_status_to_category
from app.modules.llm_gateway.http_client import get_http_client, validate_upstream_url
from app.modules.llm_gateway.providers.openrouter import OPENROUTER_ENDPOINT

logger = logging.getLogger("ragbait.kb.visual_understanding")

VISUAL_UNDERSTANDING_VERSION = "m4.10-v1"
DEFAULT_OPENROUTER_VISION_MODELS: tuple[str, ...] = (
    "openrouter/free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "nvidia/nemotron-3.5-super-vl:free",
    "google/gemma-4-26b-a4b-it:free",
)

# Async callable taking (prompt_text, png_bytes, triage_class) -> (raw_response_text, model_id)
VisionModelCallable = Callable[[str, bytes, str], Awaitable[tuple[str, str]]]


@dataclass(frozen=True)
class VisualAnalysisOutcome:
    """Structured Stage 5 output preserving uncertainty and provenance."""

    visual_description: str | None
    visual_metadata: dict[str, Any] | None
    visual_model: str | None
    visual_version: str
    visual_confidence: float | None
    visual_status: str
    visual_error: str | None
    has_visual_description: bool


def _extract_json_object(raw_text: str) -> dict[str, Any]:
    """Extract outermost JSON object cleanly, salvaging truncated structured responses safely."""
    import re

    stripped = raw_text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2:
            stripped = "\n".join(
                lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:]
            ).strip()
    start = stripped.find("{")
    if start >= 0:
        depth = 0
        in_string = False
        escaped = False
        for idx in range(start, len(stripped)):
            ch = stripped[idx]
            if escaped:
                escaped = False
                continue
            if ch == "\\" and in_string:
                escaped = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if not in_string:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return dict(json.loads(stripped[start : idx + 1]))

        # Salvage partial visual_summary / diagram_elements from truncated JSON output
        summary_m = re.search(r'"visual_summary"\s*:\s*"((?:[^"\\]|\\.)*)', stripped[start:])
        elems_m = re.findall(r'"((?:[^"\\]|\\.){4,})"', stripped[start:])
        if summary_m:
            summary_val = (
                summary_m.group(1).encode("utf-8").decode("unicode_escape", errors="ignore")
            )
            return {
                "visual_summary": summary_val,
                "diagram_elements": [],
                "extracted_equations": [],
                "structured_table": None,
                "uncertainty_notes": "Partial JSON response truncated at token limit.",
                "confidence": 0.65,
            }
        if elems_m:
            return {
                "visual_summary": " | ".join(elems_m[:6]),
                "diagram_elements": elems_m[1:8],
                "extracted_equations": [],
                "structured_table": None,
                "uncertainty_notes": "Partial JSON response truncated at token limit.",
                "confidence": 0.6,
            }

    if stripped:
        return {
            "visual_summary": stripped[:1500],
            "diagram_elements": [],
            "extracted_equations": [],
            "structured_table": None,
            "uncertainty_notes": "Returned unstructured prose instead of strict JSON.",
            "confidence": 0.6,
        }
    raise ValueError("Empty response returned by vision model.")


def build_vision_prompt(
    *,
    page_number: int,
    triage_class: str,
    native_snippet: str,
    ocr_snippet: str | None,
) -> str:
    """Build triage-specific, anti-hallucination vision prompt for a slide PNG."""
    role_guidance = {
        "flowchart_or_diagram_visual": (
            "Focus on the diagram, flowchart, ROBDD tree, datapath, or schematic. "
            "List every visible box/node, directed arrow/edge, branch label (e.g. 0-child / 1-child / Yes / No), "
            "and step ordering EXACTLY as drawn."
        ),
        "table_recovery": (
            "Focus on any table, matrix, or enumerated case breakdown. "
            "Reconstruct a clean Markdown table preserving every column header, cell value, 0/1/X state, and rule."
        ),
        "math_equation_ocr": (
            "Focus on mathematical notation, Boolean expressions, subscripts, superscripts, and equation strips. "
            "Transcribe each visible formula accurately in LaTeX or Unicode math notation."
        ),
        "mixed_ocr_and_visual": (
            "This slide combines both mathematical expressions/equations AND visual diagrams/trees. "
            "Recover ALL visible equations verbatim AND describe every diagram node, edge, and branch clearly."
        ),
    }.get(
        triage_class,
        "Describe all visible visual diagrams, equations, and structured figures accurately.",
    )

    ref_context = native_snippet.strip()[:600]
    ocr_part = (
        f"\n<ocr_text_reference>{ocr_snippet.strip()[:600]}</ocr_text_reference>"
        if ocr_snippet
        else ""
    )

    return (
        f"You are inspecting physical page {page_number} of an engineering lecture slide deck "
        f"(triage category: {triage_class}).\n"
        f"{role_guidance}\n\n"
        "CRITICAL HONESTY & ANTI-HALLUCINATION RULES:\n"
        "1. Report ONLY symbols, labels, arrows, equations, and relationships clearly visible in the image.\n"
        "2. If any label, subscript, or arrow is blurry, clipped, or ambiguous, write UNREADABLE in place "
        "of guessing and record the issue in uncertainty_notes.\n"
        "3. Treat <slide_text_reference> only as secondary context—verify against the pixels.\n\n"
        f"<slide_text_reference>{ref_context}</slide_text_reference>{ocr_part}\n\n"
        "Return ONLY valid JSON matching this schema:\n"
        "{\n"
        '  "visual_summary": "concise factual overview of the figure/table/equations on this slide",\n'
        '  "diagram_elements": ["node or relationship or step strings"],\n'
        '  "extracted_equations": ["exact equations/expressions visible"],\n'
        '  "structured_table": "Markdown table string if table/cases exist, else null",\n'
        '  "uncertainty_notes": "note any ambiguous/unreadable visual regions, else null",\n'
        '  "confidence": 0.0_to_1.0\n'
        "}"
    )


def format_visual_description_text(parsed: dict[str, Any], triage_class: str) -> tuple[str, float]:
    """Format validated vision JSON into structured, uncertainty-aware text."""
    parts: list[str] = [f"[Visual Interpretation | class={triage_class}]"]
    summary = str(parsed.get("visual_summary") or "").strip()
    if summary:
        parts.append(summary)

    elements = [str(x).strip() for x in (parsed.get("diagram_elements") or []) if str(x).strip()]
    if elements:
        parts.append(
            "Diagram elements & relationships:\n" + "\n".join(f"- {el}" for el in elements)
        )

    equations = [
        str(x).strip() for x in (parsed.get("extracted_equations") or []) if str(x).strip()
    ]
    if equations:
        parts.append("Visual equations:\n" + "\n".join(f"- {eq}" for eq in equations))

    table_md = str(parsed.get("structured_table") or "").strip()
    if table_md and table_md.lower() != "null":
        parts.append("Recovered visual table:\n" + table_md)

    uncertainty = str(parsed.get("uncertainty_notes") or "").strip()
    if uncertainty and uncertainty.lower() != "null":
        parts.append(f"Visual uncertainty: {uncertainty}")

    raw_conf = parsed.get("confidence", 0.75)
    try:
        conf = max(0.0, min(1.0, float(raw_conf)))
    except (TypeError, ValueError):
        conf = 0.75

    return "\n\n".join(parts).strip(), round(conf, 4)


async def discover_active_openrouter_keys(conn: asyncpg.Connection) -> list[str]:
    """Resolve active verified free-tier OpenRouter API keys from the credential vault pool."""
    from app.modules.credentials.repository import retrieve_decrypted_credential

    rows = await conn.fetch(
        """
        SELECT id, owner_id
        FROM public.api_key_metadata
        WHERE provider_id = 'openrouter'
          AND status = 'active'
        ORDER BY
          CASE WHEN sharing_scope = 'community_shared' THEN 0 ELSE 1 END,
          updated_at DESC
        LIMIT 8
        """
    )
    keys: list[str] = []
    for row in rows:
        try:
            decrypted = await retrieve_decrypted_credential(
                credential_id=row["id"],
                caller_id=row["owner_id"],
                check_active=True,
            )
            secret = str(decrypted.raw_key).strip()
            del decrypted
            if secret and secret not in keys:
                keys.append(secret)
        except Exception as exc:
            logger.debug("Skipping candidate OpenRouter key %s: %s", row["id"], exc)
    return keys


async def discover_active_openrouter_key(conn: asyncpg.Connection) -> str | None:
    """Resolve the first active OpenRouter key from the credential vault."""
    keys = await discover_active_openrouter_keys(conn)
    return keys[0] if keys else None


def make_openrouter_vision_caller(
    raw_keys: str | list[str],
    *,
    models: tuple[str, ...] = DEFAULT_OPENROUTER_VISION_MODELS,
    timeout_seconds: float = 40.0,
) -> VisionModelCallable:
    """Create a live multimodal caller pooling across OpenRouter keys and free vision models."""
    validate_upstream_url(OPENROUTER_ENDPOINT)
    key_pool = [raw_keys] if isinstance(raw_keys, str) else [k for k in raw_keys if k]
    if not key_pool:
        raise ValueError("At least one OpenRouter API key is required for vision calls.")
    call_counter = 0

    async def _call(prompt_text: str, png_bytes: bytes, triage_class: str) -> tuple[str, str]:
        nonlocal call_counter
        start_key_idx = call_counter % len(key_pool)
        call_counter += 1
        b64_img = base64.b64encode(png_bytes).decode("ascii")
        client = get_http_client()
        last_error: Exception | None = None

        for key_offset in range(len(key_pool)):
            api_key = key_pool[(start_key_idx + key_offset) % len(key_pool)]
            headers = {
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://ragbait.app",
                "X-Title": "RagBait M4.10 Visual Extraction",
                "Content-Type": "application/json",
            }
            for model_id in models:
                payload = {
                    "model": model_id,
                    "temperature": 0.1,
                    "max_tokens": 2600,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt_text},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/png;base64,{b64_img}"},
                                },
                            ],
                        }
                    ],
                }
                try:
                    resp = await asyncio.wait_for(
                        client.post(OPENROUTER_ENDPOINT, headers=headers, json=payload),
                        timeout=timeout_seconds,
                    )
                    if resp.status_code != 200:
                        body: dict[str, Any] = {}
                        try:
                            body = resp.json()
                        except Exception:
                            pass
                        category, msg, status = map_http_status_to_category(resp.status_code, body)
                        last_error = ProviderAdapterError(
                            provider_id="openrouter",
                            category=category,
                            message=f"[{model_id}] {msg}",
                            status_code=status,
                        )
                        if resp.status_code in (400, 404, 429, 500, 502, 503):
                            await asyncio.sleep(0.2)
                            continue
                        raise last_error

                    data = resp.json()
                    choices = data.get("choices") or []
                    if not choices:
                        last_error = ValueError(f"Empty choices returned by {model_id}")
                        continue
                    content = choices[0].get("message", {}).get("content") or ""
                    if not str(content).strip():
                        last_error = ValueError(f"Empty content returned by {model_id}")
                        continue
                    resolved = str(data.get("model") or model_id)
                    return str(content), resolved
                except TimeoutError as exc:
                    last_error = exc
                    continue
                except Exception as exc:
                    last_error = exc
                    continue

        raise RuntimeError(f"All free vision models failed ({triage_class}): {last_error}")

    return _call


def compute_page_quality_status(
    *,
    has_native_text: bool,
    has_usable_normalized_text: bool,
    triage_class: str,
    ocr_status: str,
    has_ocr_text: bool,
    visual_status: str,
    has_visual_description: bool,
) -> str:
    """Compute final M4.10 extraction_quality_status accurately reflecting usability & enrichment."""
    if triage_class == "none":
        if has_usable_normalized_text:
            return "text_complete"
        return "boilerplate_only" if has_native_text else "empty"

    needs_ocr = triage_class in OCR_ELIGIBLE_TRIAGE
    needs_visual = triage_class in RENDER_ELIGIBLE_TRIAGE

    ocr_done = (not needs_ocr) or (ocr_status == "completed" and has_ocr_text)
    visual_done = (not needs_visual) or (visual_status == "completed" and has_visual_description)

    if ocr_done and visual_done:
        return "enriched"
    if (
        has_ocr_text
        or has_visual_description
        or (ocr_status == "completed")
        or (visual_status == "completed")
    ):
        return "partially_enriched"
    if (needs_ocr and ocr_status == "failed") or (needs_visual and visual_status == "failed"):
        return "failed_enrichment" if not has_usable_normalized_text else "partially_enriched"
    return "needs_enrichment"


async def enrich_pages_with_visual_understanding(
    pages: list[dict[str, Any]],
    *,
    render_dir: Path | None,
    vision_caller: VisionModelCallable | None,
    max_concurrency: int = 2,
) -> list[dict[str, Any]]:
    """Run Stage 5 visual understanding on rendered flagged pages with full provenance."""
    semaphore = asyncio.Semaphore(max(1, max_concurrency))

    async def _process_one(page: dict[str, Any]) -> dict[str, Any]:
        diag = dict(page.get("quality_diagnostics") or {})
        triage = str(page.get("visual_triage_class") or diag.get("visual_triage_class") or "none")
        need_visual = triage in RENDER_ELIGIBLE_TRIAGE

        has_native_text = bool(page.get("has_native_text", page.get("has_text", False)))
        has_usable_norm = bool(
            page.get("has_usable_normalized_text", diag.get("has_usable_normalized_text", False))
        )
        ocr_status = str(page.get("ocr_status") or "not_needed")
        has_ocr_text = bool(page.get("has_ocr_text") or (page.get("ocr_text") or "").strip())

        if not need_visual:
            status = compute_page_quality_status(
                has_native_text=has_native_text,
                has_usable_normalized_text=has_usable_norm,
                triage_class=triage,
                ocr_status=ocr_status,
                has_ocr_text=has_ocr_text,
                visual_status="not_needed",
                has_visual_description=False,
            )
            return {
                **page,
                "visual_status": "not_needed",
                "has_visual_description": False,
                "extraction_quality_status": status,
            }

        png_name = page.get("rendered_png_filename")
        png_path = (render_dir / png_name) if (render_dir is not None and png_name) else None
        if vision_caller is None or png_path is None or not png_path.exists():
            skip_reason = (
                "No active vision model caller configured."
                if vision_caller is None
                else "Rendered page PNG missing from sandbox output."
            )
            status = compute_page_quality_status(
                has_native_text=has_native_text,
                has_usable_normalized_text=has_usable_norm,
                triage_class=triage,
                ocr_status=ocr_status,
                has_ocr_text=has_ocr_text,
                visual_status="skipped",
                has_visual_description=False,
            )
            return {
                **page,
                "visual_status": "skipped",
                "visual_error": skip_reason,
                "visual_version": VISUAL_UNDERSTANDING_VERSION,
                "has_visual_description": False,
                "extraction_quality_status": status,
            }

        async with semaphore:
            try:
                prompt = build_vision_prompt(
                    page_number=int(page["page_number"]),
                    triage_class=triage,
                    native_snippet=str(page.get("normalized_text") or page.get("raw_text") or ""),
                    ocr_snippet=page.get("ocr_text"),
                )
                png_bytes = png_path.read_bytes()
                raw_resp, model_id = await vision_caller(prompt, png_bytes, triage)
                parsed = _extract_json_object(raw_resp)
                desc_text, conf = format_visual_description_text(parsed, triage)
                has_vis = bool(desc_text.strip())
                vis_status = "completed" if has_vis else "failed"
                vis_meta = {
                    "triage_class": triage,
                    "diagram_elements_count": len(parsed.get("diagram_elements") or []),
                    "equations_count": len(parsed.get("extracted_equations") or []),
                    "has_structured_table": bool(
                        parsed.get("structured_table")
                        and str(parsed.get("structured_table")).lower() != "null"
                    ),
                    "uncertainty_notes": parsed.get("uncertainty_notes"),
                }
                status = compute_page_quality_status(
                    has_native_text=has_native_text,
                    has_usable_normalized_text=has_usable_norm,
                    triage_class=triage,
                    ocr_status=ocr_status,
                    has_ocr_text=has_ocr_text,
                    visual_status=vis_status,
                    has_visual_description=has_vis,
                )
                return {
                    **page,
                    "visual_description": desc_text if has_vis else None,
                    "visual_metadata": vis_meta,
                    "visual_model": model_id,
                    "visual_version": VISUAL_UNDERSTANDING_VERSION,
                    "visual_confidence": conf,
                    "visual_status": vis_status,
                    "visual_error": None if has_vis else "Empty visual description generated.",
                    "visual_processed_at": datetime.now(UTC).isoformat(),
                    "has_visual_description": has_vis,
                    "extraction_quality_status": status,
                }
            except Exception as exc:
                logger.warning(
                    "Stage 5 visual understanding failed for page %s: %s",
                    page.get("page_number"),
                    exc,
                )
                status = compute_page_quality_status(
                    has_native_text=has_native_text,
                    has_usable_normalized_text=has_usable_norm,
                    triage_class=triage,
                    ocr_status=ocr_status,
                    has_ocr_text=has_ocr_text,
                    visual_status="failed",
                    has_visual_description=False,
                )
                return {
                    **page,
                    "visual_description": None,
                    "visual_metadata": {"triage_class": triage},
                    "visual_model": None,
                    "visual_version": VISUAL_UNDERSTANDING_VERSION,
                    "visual_confidence": None,
                    "visual_status": "failed",
                    "visual_error": str(exc)[:500],
                    "visual_processed_at": datetime.now(UTC).isoformat(),
                    "has_visual_description": False,
                    "extraction_quality_status": status,
                }

    return list(await asyncio.gather(*(_process_one(p) for p in pages)))
