"""Strict SSRF prevention, DNS validation, query redaction, and safe HTML text extraction."""

import asyncio
import hashlib
import ipaddress
import logging
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger("ragbait.chat.web_search.security")

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_API_SECRET_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{12,}|AIza[0-9A-Za-z_-]{20,}|gsk_[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9._-]+)\b",
    re.IGNORECASE,
)
_BLOCKED_HOSTNAMES: frozenset[str] = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata.google.internal",
        "metadata",
        "instance-data",
    }
)
_BLOCKED_HOST_SUFFIXES: tuple[str, ...] = (".local", ".localhost", ".internal", ".home", ".lan")
_ALLOWED_CONTENT_TYPES: tuple[str, ...] = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
)


class UnsafeWebUrlError(ValueError):
    """Raised when a URL fails HTTPS/HTTP or public-IP SSRF checks."""


def _is_public_ip(ip_str: str) -> bool:
    """Return True strictly when `ip_str` is a routable global unicast address."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    ):
        return False
    return bool(addr.is_global)


async def validate_safe_public_http_url(
    raw_url: str,
    *,
    resolve_dns: bool = True,
) -> str:
    """Validate that `raw_url` uses HTTP(S) and resolves strictly to public IP addresses.

    Blocks loopback, RFC-1918/private IP ranges, link-local / cloud metadata IPs
    (`169.254.169.254`, `100.100.100.200`), embedded credentials, and non-HTTP schemes.
    """
    cleaned = (raw_url or "").strip()
    parsed = urlparse(cleaned)
    if parsed.scheme.lower() not in ("http", "https"):
        raise UnsafeWebUrlError(f"Unsupported URL scheme: '{parsed.scheme}'")
    if parsed.username or parsed.password:
        raise UnsafeWebUrlError("URLs containing embedded userinfo credentials are rejected")
    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        raise UnsafeWebUrlError("URL hostname is missing")
    if hostname in _BLOCKED_HOSTNAMES or hostname.endswith(_BLOCKED_HOST_SUFFIXES):
        raise UnsafeWebUrlError(f"Internal hostname rejected: '{hostname}'")

    # Reject literal non-global IPs immediately (covers decimal/IPv4/IPv6 literals)
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        if not _is_public_ip(hostname):
            raise UnsafeWebUrlError(f"Non-public IP literal rejected: '{hostname}'")
        return cleaned

    if resolve_dns:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        try:
            infos = await asyncio.to_thread(
                socket.getaddrinfo,
                hostname,
                port,
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise UnsafeWebUrlError(f"DNS resolution failed for '{hostname}': {exc}") from exc

        resolved_ips = [info[4][0] for info in infos if info and info[4]]
        if not resolved_ips:
            raise UnsafeWebUrlError(f"No IP addresses resolved for '{hostname}'")
        for ip_val in resolved_ips:
            # Strip IPv6 scope IDs like '%eth0'
            base_ip = str(ip_val).split("%", 1)[0]
            if not _is_public_ip(base_ip):
                raise UnsafeWebUrlError(
                    f"Hostname '{hostname}' resolved to non-public IP '{base_ip}'"
                )

    return cleaned


def redact_search_query(raw_query: str, max_length: int = 120) -> str:
    """Construct a minimal, redacted search query without secrets or private identifiers."""
    text = (raw_query or "").strip()
    text = _API_SECRET_RE.sub("[redacted]", text)
    text = _EMAIL_RE.sub("[redacted]", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_length:
        text = text[:max_length].rsplit(" ", 1)[0].strip() or text[:max_length]
    return text


class _VisibleTextExtractor(HTMLParser):
    """Stdlib HTML parser stripping scripts/styles/noscript/svg tags into readable text."""

    _SKIP_TAGS: frozenset[str] = frozenset({"script", "style", "noscript", "svg", "iframe"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self._SKIP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data:
            self._parts.append(data)

    def get_text(self) -> str:
        joined = " ".join(self._parts)
        return re.sub(r"\s+", " ", joined).strip()


def extract_safe_text_from_html(
    raw_bytes: bytes,
    content_type: str,
    *,
    max_chars: int = 2400,
) -> str:
    """Safely convert bounded HTML/plain-text bytes to inert plain text."""
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    if ctype and not any(ctype == allowed for allowed in _ALLOWED_CONTENT_TYPES):
        raise ValueError(f"Unsupported external content-type: '{ctype}'")

    decoded = raw_bytes.decode("utf-8", errors="replace")
    if ctype == "text/plain":
        clean = re.sub(r"\s+", " ", decoded).strip()
    else:
        parser = _VisibleTextExtractor()
        parser.feed(decoded)
        parser.close()
        clean = parser.get_text()

    if len(clean) > max_chars:
        clean = f"{clean[:max_chars].rstrip()}..."
    return clean


async def fetch_web_page(
    url: str,
    *,
    timeout_seconds: float = 6.0,
    max_bytes: int = 524_288,
    max_redirects: int = 3,
    client: httpx.AsyncClient | None = None,
) -> tuple[str, str]:
    """Fetch an external HTML/plain page safely with redirect & byte-size guards.

    Returns:
        (inert_text_excerpt, sha256_hex)
    """
    current_url = await validate_safe_public_http_url(url)

    owns_client = client is None
    http_client = client or httpx.AsyncClient(
        follow_redirects=False,
        timeout=httpx.Timeout(connect=3.0, read=timeout_seconds, write=3.0, pool=5.0),
    )

    try:
        for _ in range(max_redirects + 1):
            async with http_client.stream(
                "GET",
                current_url,
                headers={"User-Agent": "RagBaitStudyBot/1.0 (+https://ragbait.app)"},
            ) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location")
                    if not location:
                        raise UnsafeWebUrlError("Redirect response missing Location header")
                    next_url = urljoin(current_url, location)
                    current_url = await validate_safe_public_http_url(next_url)
                    continue

                if resp.status_code != 200:
                    raise ValueError(f"External web fetch failed with status {resp.status_code}")

                content_length_hdr = resp.headers.get("content-length")
                if content_length_hdr:
                    try:
                        if int(content_length_hdr) > max_bytes:
                            raise ValueError("External page exceeded maximum allowed byte limit")
                    except ValueError:
                        raise

                content_type = resp.headers.get("content-type", "text/html")
                ctype_base = content_type.split(";", 1)[0].strip().lower()
                if ctype_base and not any(ctype_base == a for a in _ALLOWED_CONTENT_TYPES):
                    raise ValueError(f"Disallowed external content-type: '{ctype_base}'")

                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        raise ValueError("External page exceeded maximum allowed download bytes")

                raw_bytes = bytes(buf)
                text = extract_safe_text_from_html(raw_bytes, content_type)
                if not text:
                    raise ValueError("External page yielded empty readable text")
                content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                return text, content_hash

        raise UnsafeWebUrlError("Too many HTTP redirects while fetching external page")
    finally:
        if owns_client:
            await http_client.aclose()
