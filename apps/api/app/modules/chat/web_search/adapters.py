"""Modular server-side web search provider adapters."""

import hashlib
import logging
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlparse

import httpx

from app.modules.chat.web_search.schemas import RawWebResult
from app.modules.chat.web_search.security import UnsafeWebUrlError, validate_safe_public_http_url

logger = logging.getLogger("ragbait.chat.web_search.adapters")


def _extract_domain(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return "unknown"


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class BaseWebSearchAdapter(ABC):
    """Abstract interface for server-side web search providers."""

    provider_name: str = "base"

    @abstractmethod
    async def search(
        self,
        *,
        query: str,
        max_results: int = 5,
        timeout_seconds: float = 8.0,
    ) -> list[RawWebResult]:
        """Execute a single redacted web search query."""


class TavilySearchAdapter(BaseWebSearchAdapter):
    """Adapter for Tavily Search REST API (https://api.tavily.com/search).

    Uses an administrator-configured server credential (`RAGBAIT_WEB_SEARCH_API_KEY`).
    Never exposes or reuses student LLM credentials.
    """

    provider_name = "tavily"
    ENDPOINT = "https://api.tavily.com/search"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(
        self,
        *,
        query: str,
        max_results: int = 5,
        timeout_seconds: float = 8.0,
    ) -> list[RawWebResult]:
        if not self._api_key:
            return []

        payload = {
            "api_key": self._api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
        }
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(connect=3.0, read=timeout_seconds, write=4.0, pool=5.0),
        ) as client:
            resp = await client.post(self.ENDPOINT, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"Tavily search returned HTTP {resp.status_code}")
            data: dict[str, Any] = resp.json()

        results: list[RawWebResult] = []
        for idx, item in enumerate(data.get("results") or [], start=1):
            raw_url = str(item.get("url") or "").strip()
            snippet = str(item.get("content") or "").strip()
            title = str(item.get("title") or "").strip()
            if not raw_url or not snippet or not title:
                continue
            try:
                safe_url = await validate_safe_public_http_url(raw_url, resolve_dns=False)
            except UnsafeWebUrlError:
                continue
            domain = _extract_domain(safe_url)
            results.append(
                RawWebResult(
                    title=title[:300],
                    url=safe_url,
                    domain=domain,
                    publisher=domain,
                    published_at=item.get("published_date"),
                    excerpt=snippet[:1600],
                    content_hash=_hash_text(snippet),
                    snippet_only=True,
                    retrieval_rank=idx,
                    relevance_score=float(item.get("score") or max(0.1, 1.0 - idx * 0.1)),
                    query_used=query,
                )
            )
        return results


class BraveSearchAdapter(BaseWebSearchAdapter):
    """Adapter for Brave Search REST API (https://api.search.brave.com/res/v1/web/search)."""

    provider_name = "brave"
    ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(
        self,
        *,
        query: str,
        max_results: int = 5,
        timeout_seconds: float = 8.0,
    ) -> list[RawWebResult]:
        if not self._api_key:
            return []

        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self._api_key,
        }
        params: dict[str, str | int] = {"q": query, "count": max_results}
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(connect=3.0, read=timeout_seconds, write=4.0, pool=5.0),
        ) as client:
            resp = await client.get(self.ENDPOINT, headers=headers, params=params)
            if resp.status_code != 200:
                raise RuntimeError(f"Brave search returned HTTP {resp.status_code}")
            data: dict[str, Any] = resp.json()

        web_items = (data.get("web") or {}).get("results") or []
        results: list[RawWebResult] = []
        for idx, item in enumerate(web_items, start=1):
            raw_url = str(item.get("url") or "").strip()
            snippet = str(item.get("description") or "").strip()
            title = str(item.get("title") or "").strip()
            if not raw_url or not snippet or not title:
                continue
            try:
                safe_url = await validate_safe_public_http_url(raw_url, resolve_dns=False)
            except UnsafeWebUrlError:
                continue
            domain = _extract_domain(safe_url)
            results.append(
                RawWebResult(
                    title=title[:300],
                    url=safe_url,
                    domain=domain,
                    publisher=(item.get("meta_url") or {}).get("hostname") or domain,
                    published_at=item.get("page_age"),
                    excerpt=snippet[:1600],
                    content_hash=_hash_text(snippet),
                    snippet_only=True,
                    retrieval_rank=idx,
                    relevance_score=max(0.1, 1.0 - idx * 0.1),
                    query_used=query,
                )
            )
        return results
