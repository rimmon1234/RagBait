"""Shared async HTTP transport and connection pooling for LLM provider adapters."""

import logging
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("ragbait.llm_gateway.http")

# Immutable allowlist of permitted upstream AI inference hostnames
ALLOWED_UPSTREAM_HOSTS: frozenset[str] = frozenset(
    {
        "generativelanguage.googleapis.com",
        "api.groq.com",
        "openrouter.ai",
        "api.cerebras.ai",
    }
)

_client: httpx.AsyncClient | None = None


def validate_upstream_url(url: str) -> None:
    """Verify that an outbound request targets a strictly allowlisted HTTPS hostname."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Inference gateway requires HTTPS; rejected scheme '{parsed.scheme}'")
    host = parsed.hostname
    if not host or host.lower() not in ALLOWED_UPSTREAM_HOSTS:
        raise ValueError(f"Host '{host}' is not in the trusted LLM provider allowlist")


async def init_http_client() -> None:
    """Initialize shared HTTP/2 connection pool on application startup."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            http2=False,  # Use standard HTTP/1.1 or let httpx negotiate safely
            follow_redirects=False,  # Explicitly forbid automatic redirect following
            timeout=httpx.Timeout(
                connect=5.0,
                read=60.0,
                write=10.0,
                pool=10.0,
            ),
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=50,
                keepalive_expiry=30.0,
            ),
        )
        logger.info("Shared LLM Gateway HTTP transport initialized")


async def close_http_client() -> None:
    """Gracefully close active connection pool on application shutdown."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
        _client = None
        logger.info("Shared LLM Gateway HTTP transport closed")


def get_http_client() -> httpx.AsyncClient:
    """Retrieve active shared HTTP transport client."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(connect=5.0, read=60.0, write=10.0, pool=10.0),
            limits=httpx.Limits(
                max_keepalive_connections=20, max_connections=50, keepalive_expiry=30.0
            ),
        )
    return _client
