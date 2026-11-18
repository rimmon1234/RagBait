"""Structured JSON logging configuration."""

import json
import logging
import sys
import time
import uuid
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# Fields that must never appear in log payloads
REDACTED_FIELDS = {
    "password",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "secret",
    "service_role_key",
    "api_key",
}


def sanitize_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively redact sensitive keys from dictionary logging payloads."""
    cleaned: dict[str, Any] = {}
    for k, v in data.items():
        if any(term in k.lower() for term in REDACTED_FIELDS):
            cleaned[k] = "[REDACTED]"
        elif isinstance(v, dict):
            cleaned[k] = sanitize_dict(v)
        else:
            cleaned[k] = v
    return cleaned


class JSONFormatter(logging.Formatter):
    """Formats log records into single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if hasattr(record, "request_id"):
            log_obj["request_id"] = record.request_id
        if hasattr(record, "route"):
            log_obj["route"] = record.route
        if hasattr(record, "http_status"):
            log_obj["http_status"] = record.http_status
        if hasattr(record, "duration_ms"):
            log_obj["duration_ms"] = record.duration_ms
        if hasattr(record, "user_id"):
            log_obj["user_id"] = record.user_id

        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_obj)


def setup_logging(level: str = "INFO") -> None:
    """Configures the root logger to use JSON formatting."""
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root_logger.addHandler(handler)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware attaching request_id and logging incoming and completed HTTP requests."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id

        start_time = time.perf_counter()
        logger = logging.getLogger("ragbait.access")

        try:
            response = await call_next(request)
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

            extra: dict[str, Any] = {
                "request_id": request_id,
                "route": request.url.path,
                "http_status": response.status_code,
                "duration_ms": duration_ms,
            }
            if hasattr(request.state, "user_id"):
                extra["user_id"] = str(request.state.user_id)

            logger.info(
                f"{request.method} {request.url.path} {response.status_code} ({duration_ms}ms)",
                extra=extra,
            )
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.exception(
                f"{request.method} {request.url.path} failed with unhandled exception",
                extra={
                    "request_id": request_id,
                    "route": request.url.path,
                    "duration_ms": duration_ms,
                },
            )
            raise
