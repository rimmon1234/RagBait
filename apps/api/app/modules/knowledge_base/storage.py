"""Supabase Storage client and signed capability helpers for Knowledge Base."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from storage3.exceptions import StorageApiError
from supabase import AsyncClient, create_async_client

from app.core.config import settings
from app.core.errors import AppError, NotFoundError

logger = logging.getLogger("ragbait.kb.storage")

_async_supabase_client: AsyncClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


async def get_storage_client() -> AsyncClient:
    """Retrieve or initialize the service-role Supabase async client."""
    global _async_supabase_client, _client_loop
    current_loop = asyncio.get_running_loop()
    if _async_supabase_client is None or _client_loop != current_loop:
        _async_supabase_client = await create_async_client(
            settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY
        )
        _client_loop = current_loop
    return _async_supabase_client


async def close_storage_client() -> None:
    """Close and reset storage client."""
    global _async_supabase_client, _client_loop
    _async_supabase_client = None
    _client_loop = None


def _normalize_storage_url(url: str) -> str:
    """Ensure URL is fully qualified with origin and storage path prefix."""
    if url.startswith("http://") or url.startswith("https://"):
        return url
    base = settings.SUPABASE_URL.rstrip("/")
    if url.startswith("/storage/v1"):
        return f"{base}{url}"
    if url.startswith("/"):
        return f"{base}/storage/v1{url}"
    return f"{base}/storage/v1/{url}"


async def create_signed_upload(path: str) -> dict[str, Any]:
    """Generate a time-limited signed upload capability for Supabase Storage."""
    client = await get_storage_client()
    bucket = client.storage.from_(settings.KB_STORAGE_BUCKET)

    try:
        res = await bucket.create_signed_upload_url(path)
        upload_url = str(res.get("signed_url") or res.get("signedUrl") or res.get("url") or "")
        token = str(res.get("token") or "")
        full_url = _normalize_storage_url(upload_url)
        return {
            "signed_url": full_url,
            "token": token,
            "path": path,
        }
    except Exception as e:
        logger.error(f"Failed to generate signed upload URL for path '{path}': {e}")
        raise AppError(
            code="STORAGE_SIGNING_FAILED",
            message="Failed to generate secure upload capability.",
            status_code=500,
        ) from e


async def download_object_bytes(path: str) -> bytes:
    """Download raw binary content of an object from private Supabase Storage."""
    client = await get_storage_client()
    bucket = client.storage.from_(settings.KB_STORAGE_BUCKET)

    try:
        data = await bucket.download(path)
        return data
    except StorageApiError as e:
        logger.warning(f"Storage API error downloading path '{path}': {e}")
        # StorageApiError args contain dict with statusCode or error
        status_code = getattr(e, "status_code", None)
        if not status_code and hasattr(e, "args") and e.args and isinstance(e.args[0], dict):
            status_code = e.args[0].get("statusCode")
        if status_code == 404 or "not_found" in str(e).lower() or "not found" in str(e).lower():
            raise NotFoundError(
                message=f"Storage object at path '{path}' does not exist.",
                code="STORAGE_OBJECT_NOT_FOUND",
            ) from e
        raise AppError(
            code="STORAGE_DOWNLOAD_FAILED",
            message="Failed to retrieve file from storage.",
            status_code=502,
        ) from e
    except Exception as e:
        logger.error(f"Unexpected error downloading path '{path}': {e}")
        raise AppError(
            code="STORAGE_DOWNLOAD_FAILED",
            message="Unexpected error accessing storage.",
            status_code=500,
        ) from e


@asynccontextmanager
async def open_storage_stream(
    path: str,
    chunk_size: int = 64 * 1024,
) -> AsyncGenerator[AsyncGenerator[bytes, None], None]:
    """Stream object bytes directly from private Supabase Storage with bounded memory."""
    url = (
        f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1/object/{settings.KB_STORAGE_BUCKET}/{path}"
    )
    headers = {"Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}"}

    client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
    try:
        req = client.build_request("GET", url, headers=headers)
        resp = await client.send(req, stream=True)
        if resp.status_code != 200:
            err_bytes = await resp.aread()
            await resp.aclose()
            err_str = err_bytes.decode(errors="ignore").lower()
            if (
                resp.status_code == 404
                or "not_found" in err_str
                or "nosuchkey" in err_str
                or "object not found" in err_str
            ):
                raise NotFoundError(
                    message=f"Storage object at path '{path}' does not exist.",
                    code="STORAGE_OBJECT_NOT_FOUND",
                )
            raise AppError(
                code="STORAGE_DOWNLOAD_FAILED",
                message=f"Failed to stream storage object (HTTP {resp.status_code}).",
                status_code=502,
            )

        async def _generator() -> AsyncGenerator[bytes, None]:
            try:
                async for chunk in resp.aiter_bytes(chunk_size=chunk_size):
                    yield chunk
            finally:
                await resp.aclose()

        yield _generator()
    finally:
        await client.aclose()


async def create_signed_download(path: str, expires_in: int = 300) -> str:
    """Generate a short-lived signed download URL for an object."""
    client = await get_storage_client()
    bucket = client.storage.from_(settings.KB_STORAGE_BUCKET)

    try:
        res = await bucket.create_signed_url(path, expires_in=expires_in)
        dl_url = str(res.get("signed_url") or res.get("signedUrl") or "")
        if not dl_url:
            raise NotFoundError(
                message=f"Storage object at path '{path}' not found.",
                code="STORAGE_OBJECT_NOT_FOUND",
            )
        return _normalize_storage_url(dl_url)
    except StorageApiError as e:
        logger.warning(f"Storage API error creating signed download for '{path}': {e}")
        status_code = getattr(e, "status_code", None)
        if not status_code and hasattr(e, "args") and e.args and isinstance(e.args[0], dict):
            status_code = e.args[0].get("statusCode")
        if status_code == 404 or "not_found" in str(e).lower() or "not found" in str(e).lower():
            raise NotFoundError(
                message=f"Storage object at path '{path}' does not exist.",
                code="STORAGE_OBJECT_NOT_FOUND",
            ) from e
        raise AppError(
            code="STORAGE_SIGNING_FAILED",
            message="Failed to generate secure download capability.",
            status_code=502,
        ) from e
    except NotFoundError:
        raise
    except Exception as e:
        logger.error(f"Unexpected error generating download URL for path '{path}': {e}")
        raise AppError(
            code="STORAGE_SIGNING_FAILED",
            message="Unexpected error generating download capability.",
            status_code=500,
        ) from e


async def delete_storage_object(path: str) -> bool:
    """Delete an object from private Supabase Storage."""
    client = await get_storage_client()
    bucket = client.storage.from_(settings.KB_STORAGE_BUCKET)

    try:
        await bucket.remove([path])
        return True
    except Exception as e:
        logger.warning(f"Error removing storage object '{path}': {e}")
        return False
