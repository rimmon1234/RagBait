"""Tests for Server-Sent Events (SSE) stream parsing and edge cases."""

import pytest

from app.modules.llm_gateway.streaming import format_sse_event, parse_sse_stream


async def _async_iter(items: list[bytes]):
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_parse_simple_sse_stream():
    chunks = [
        b"event: message\ndata: hello world\n\n",
        b"data: second message\n\n",
    ]
    events = [e async for e in parse_sse_stream(_async_iter(chunks))]
    assert len(events) == 2
    assert events[0] == {"event": "message", "data": "hello world"}
    assert events[1] == {"event": "message", "data": "second message"}


@pytest.mark.asyncio
async def test_parse_split_chunks_across_network_packets():
    # A single event fragmented across multiple small packet chunks
    chunks = [
        b"event: tex",
        b"t_delta\nda",
        b'ta: {"char": ',
        b'"RagBait"}\n\n',
    ]
    events = [e async for e in parse_sse_stream(_async_iter(chunks))]
    assert len(events) == 1
    assert events[0] == {"event": "text_delta", "data": '{"char": "RagBait"}'}


@pytest.mark.asyncio
async def test_parse_multiple_events_in_single_chunk():
    chunk = b'data: {"id": 1}\n\ndata: {"id": 2}\n\ndata: [DONE]\n\n'
    events = [e async for e in parse_sse_stream(_async_iter([chunk]))]
    assert len(events) == 3
    assert events[0]["data"] == '{"id": 1}'
    assert events[1]["data"] == '{"id": 2}'
    assert events[2]["data"] == "[DONE]"


@pytest.mark.asyncio
async def test_parse_multibyte_utf8_split_across_chunks():
    # Emoji 🔥 is 4 bytes in UTF-8: b'\xf0\x9f\x94\xa5'
    # Split between byte 2 and byte 3
    part1 = b"data: fire " + b"\xf0\x9f"
    part2 = b"\x94\xa5\n\n"

    events = [e async for e in parse_sse_stream(_async_iter([part1, part2]))]
    assert len(events) == 1
    assert events[0]["data"] == "fire 🔥"


@pytest.mark.asyncio
async def test_parse_crlf_and_comments():
    chunks = [
        b": keepalive comment\r\n",
        b"event: text\r\ndata: line with CRLF\r\n\r\n",
        b": another comment\n",
        b"data: plain line\n\n",
    ]
    events = [e async for e in parse_sse_stream(_async_iter(chunks))]
    assert len(events) == 2
    assert events[0] == {"event": "text", "data": "line with CRLF"}
    assert events[1] == {"event": "message", "data": "plain line"}


def test_format_sse_event():
    out = format_sse_event("custom_event", {"key": "value", "count": 42})
    assert out == 'event: custom_event\ndata: {"key":"value","count":42}\n\n'
