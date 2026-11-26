"""Robust Server-Sent Events (SSE) stream parser and serialization."""

import codecs
import json
from collections.abc import AsyncIterator
from typing import Any


def format_sse_event(
    event: str,
    data: dict[str, Any],
    *,
    event_id: int | None = None,
) -> str:
    """Format an SSE frame: optional 'id: <seq>\\n', 'event: <name>\\ndata: <json>\\n\\n'."""
    serialized = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return f"{prefix}event: {event}\ndata: {serialized}\n\n"


def format_sse_comment(comment: str = "keep-alive") -> str:
    """Format an SSE comment / keepalive ping frame."""
    clean = comment.replace("\r", " ").replace("\n", " ").strip() or "keep-alive"
    return f": {clean}\n\n"


async def parse_sse_stream(
    byte_stream: AsyncIterator[bytes],
    *,
    include_id: bool = False,
) -> AsyncIterator[dict[str, str]]:
    """Incrementally parse an incoming byte stream into complete SSE events.

    Guarantees:
      - UTF-8 incremental decoding: Multibyte characters split across network chunks are buffered safely.
      - Line buffer: Lines split across network packets are buffered until a complete newline is observed.
      - Handles both '\\r\\n' and '\\n' line endings.
      - Ignores comments (lines starting with ':') and keep-alive pings.
      - Emits when an empty line indicates end-of-event.
    """
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buffer = ""

    current_event = "message"
    current_id: str | None = None
    data_lines: list[str] = []

    def _build_frame() -> dict[str, str]:
        frame: dict[str, str] = {"event": current_event, "data": "\n".join(data_lines)}
        if include_id and current_id is not None:
            frame["id"] = current_id
        return frame

    async for chunk in byte_stream:
        text = decoder.decode(chunk, final=False)
        buffer += text

        # Process lines
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.rstrip("\r")

            if not line:
                # Empty line indicates dispatch of buffered SSE message
                if data_lines:
                    yield _build_frame()
                    data_lines = []
                    current_event = "message"
                    current_id = None
                continue

            if line.startswith(":"):
                # SSE comment / keepalive line
                continue

            if line.startswith("event:"):
                current_event = line[6:].strip()
            elif line.startswith("id:"):
                current_id = line[3:].strip()
            elif line.startswith("data:"):
                data_content = line[5:].strip()
                data_lines.append(data_content)

    # Flush any remaining buffer at end of stream
    remaining = decoder.decode(b"", final=True)
    buffer += remaining
    if buffer:
        line = buffer.rstrip("\r")
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())

    if data_lines:
        yield _build_frame()
