"""Server-side web search orchestrator with caching, rate limiting, and source ranking."""

import hashlib
import json
import logging
import time
from uuid import UUID

from app.core.redis import get_redis
from app.modules.chat.context.config import context_settings
from app.modules.chat.web_search.adapters import (
    BaseWebSearchAdapter,
    BraveSearchAdapter,
    TavilySearchAdapter,
)
from app.modules.chat.web_search.schemas import (
    RawWebResult,
    WebSearchOptions,
    WebSearchOutcome,
)
from app.modules.chat.web_search.security import (
    fetch_web_page,
    redact_search_query,
    validate_safe_public_http_url,
)

logger = logging.getLogger("ragbait.chat.web_search.service")

_HIGH_TRUST_DOMAIN_SUFFIXES = (".gov", ".edu", ".ac.uk", ".edu.in", ".org")
_TECH_AUTHORITY_DOMAINS = (
    "docs.python.org",
    "peps.python.org",
    "postgresql.org",
    "developer.mozilla.org",
    "rfc-editor.org",
    "arxiv.org",
    "ieee.org",
    "acm.org",
    "w3.org",
    "openjdk.org",
    "docs.oracle.com",
    "react.dev",
    "nextjs.org",
    "fastapi.tiangolo.com",
)


def _source_authority_boost(domain: str) -> float:
    d = (domain or "").lower()
    if any(d == td or d.endswith(f".{td}") for td in _TECH_AUTHORITY_DOMAINS):
        return 0.35
    if d.endswith(_HIGH_TRUST_DOMAIN_SUFFIXES):
        return 0.25
    if d.startswith("docs.") or d.startswith("developer."):
        return 0.20
    return 0.0


def _resolve_configured_adapter() -> BaseWebSearchAdapter | None:
    if not context_settings.WEB_SEARCH_ENABLED:
        return None
    provider = context_settings.WEB_SEARCH_PROVIDER.strip().lower()
    api_key = context_settings.WEB_SEARCH_API_KEY.strip()
    if provider == "tavily" and api_key:
        return TavilySearchAdapter(api_key=api_key)
    if provider == "brave" and api_key:
        return BraveSearchAdapter(api_key=api_key)
    return None


async def _check_search_rate_limit(user_id: UUID) -> bool:
    """Enforce bounded per-user and global RPM for server-side web search via Redis."""
    redis_client = get_redis()
    if redis_client is None:
        return True  # Non-blocking when optional web cache/rate key store unavailable

    now_bucket = int(time.time() // 60)
    user_key = f"ragbait:websearch:user:{user_id}:{now_bucket}"
    global_key = f"ragbait:websearch:global:{now_bucket}"
    try:
        pipe = redis_client.pipeline()
        pipe.incr(user_key)
        pipe.expire(user_key, 90)
        pipe.incr(global_key)
        pipe.expire(global_key, 90)
        u_cnt, _, g_cnt, _ = await pipe.execute()
        if int(u_cnt) > context_settings.WEB_SEARCH_USER_RPM:
            return False
        if int(g_cnt) > context_settings.WEB_SEARCH_GLOBAL_RPM:
            return False
    except Exception:
        return True
    return True


async def _read_cached_results(redacted_query: str) -> list[RawWebResult] | None:
    ttl = context_settings.WEB_SEARCH_CACHE_TTL_SECONDS
    if ttl <= 0:
        return None
    redis_client = get_redis()
    if redis_client is None:
        return None
    q_hash = hashlib.sha256(redacted_query.lower().encode("utf-8")).hexdigest()
    key = f"ragbait:websearch:cache:{q_hash}"
    try:
        raw = await redis_client.get(key)
        if not raw:
            return None
        payload = json.loads(raw)
        return [RawWebResult.model_validate(item) for item in payload]
    except Exception:
        return None


async def _write_cached_results(redacted_query: str, results: list[RawWebResult]) -> None:
    ttl = context_settings.WEB_SEARCH_CACHE_TTL_SECONDS
    if ttl <= 0 or not results:
        return
    redis_client = get_redis()
    if redis_client is None:
        return
    q_hash = hashlib.sha256(redacted_query.lower().encode("utf-8")).hexdigest()
    key = f"ragbait:websearch:cache:{q_hash}"
    try:
        serialized = json.dumps([r.model_dump(mode="json") for r in results])
        await redis_client.setex(key, ttl, serialized)
    except Exception:
        pass


class WebSearchService:
    """Executes redacted, bounded, rate-limited server-side web search."""

    @staticmethod
    async def search(
        *,
        user_id: UUID,
        queries: list[str],
        options: WebSearchOptions | None = None,
        adapter: BaseWebSearchAdapter | None = None,
    ) -> WebSearchOutcome:
        started = time.monotonic()
        eff_opts = options or WebSearchOptions(
            max_queries=context_settings.WEB_SEARCH_MAX_QUERIES_PER_TURN,
            max_results_per_query=context_settings.WEB_SEARCH_MAX_RESULTS_PER_QUERY,
            fetch_top_pages=context_settings.WEB_SEARCH_MAX_PAGE_FETCHES,
            timeout_seconds=context_settings.WEB_SEARCH_TIMEOUT_SECONDS,
        )

        active_adapter = adapter or _resolve_configured_adapter()
        if active_adapter is None:
            return WebSearchOutcome(
                status="unavailable_unconfigured",
                provider="none",
                error_message="No approved server-side web search provider is configured.",
            )

        if not await _check_search_rate_limit(user_id):
            return WebSearchOutcome(
                status="unavailable_rate_limited",
                provider=active_adapter.provider_name,
                error_message="Web search rate limit exceeded. Try again shortly.",
            )

        clean_queries: list[str] = []
        seen_q: set[str] = set()
        for q in queries:
            redacted = redact_search_query(q)
            if redacted and redacted.lower() not in seen_q:
                seen_q.add(redacted.lower())
                clean_queries.append(redacted)
            if len(clean_queries) >= eff_opts.max_queries:
                break

        if not clean_queries:
            return WebSearchOutcome(
                status="executed",
                provider=active_adapter.provider_name,
                queries_executed=[],
                results=[],
            )

        deduped_by_url: dict[str, RawWebResult] = {}
        deduped_by_hash: set[str] = set()
        cache_hits = 0
        had_error: str | None = None

        for q in clean_queries:
            cached = await _read_cached_results(q) if adapter is None else None
            if cached is not None:
                cache_hits += 1
                batch = cached
            else:
                try:
                    batch = await active_adapter.search(
                        query=q,
                        max_results=eff_opts.max_results_per_query,
                        timeout_seconds=eff_opts.timeout_seconds,
                    )
                    if adapter is None and batch:
                        await _write_cached_results(q, batch)
                except Exception as exc:
                    logger.warning(
                        "Web search query failed provider=%s err=%s",
                        active_adapter.provider_name,
                        str(exc)[:160],
                    )
                    had_error = str(exc)
                    batch = []

            for item in batch:
                try:
                    safe_url = await validate_safe_public_http_url(item.url, resolve_dns=False)
                except Exception:
                    continue
                canonical_url = safe_url.rstrip("/")
                if canonical_url in deduped_by_url or item.content_hash in deduped_by_hash:
                    continue
                boosted = float(item.relevance_score) + _source_authority_boost(item.domain)
                deduped_by_hash.add(item.content_hash)
                deduped_by_url[canonical_url] = item.model_copy(
                    update={"url": safe_url, "relevance_score": boosted}
                )

        ranked = sorted(
            deduped_by_url.values(),
            key=lambda r: (-r.relevance_score, r.retrieval_rank),
        )

        # Optional bounded full-page fetch for top authoritative results when enabled
        fetched_count = 0
        final_results: list[RawWebResult] = []
        for idx, item in enumerate(ranked, start=1):
            updated = item.model_copy(update={"retrieval_rank": idx})
            if fetched_count < eff_opts.fetch_top_pages and item.snippet_only:
                try:
                    page_text, page_hash = await fetch_web_page(
                        item.url,
                        timeout_seconds=min(eff_opts.timeout_seconds, 6.0),
                        max_bytes=context_settings.WEB_FETCH_MAX_BYTES,
                    )
                    updated = updated.model_copy(
                        update={
                            "excerpt": page_text,
                            "content_hash": page_hash,
                            "snippet_only": False,
                        }
                    )
                    fetched_count += 1
                except Exception:
                    # Keep safe search snippet transparently marked snippet_only=True
                    pass
            final_results.append(updated)

        latency_ms = int((time.monotonic() - started) * 1000)
        if not final_results and had_error:
            return WebSearchOutcome(
                status="unavailable_error",
                provider=active_adapter.provider_name,
                queries_executed=clean_queries,
                results=[],
                cache_hits=cache_hits,
                latency_ms=latency_ms,
                error_message="External web search provider was unavailable.",
            )

        logger.info(
            "Web search executed provider=%s queries=%d results=%d cache_hits=%d latency_ms=%d",
            active_adapter.provider_name,
            len(clean_queries),
            len(final_results),
            cache_hits,
            latency_ms,
        )
        return WebSearchOutcome(
            status="executed",
            provider=active_adapter.provider_name,
            queries_executed=clean_queries,
            results=final_results,
            cache_hits=cache_hits,
            latency_ms=latency_ms,
        )
