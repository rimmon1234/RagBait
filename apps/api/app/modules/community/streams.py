"""Redis Streams dispatcher and sequenced chunk transport for Community Inference."""

import json
import logging
import os
from typing import Any
from uuid import UUID

from app.core.redis import get_redis
from app.modules.community.schemas import SignedJobAuthorization

logger = logging.getLogger("ragbait.community.streams")

CONSUMER_GROUP_NAME = "cg:nodes"
CHUNKS_STREAM_TTL_SECONDS = 3600  # 1 hour retention
JOBS_STREAM_TTL_SECONDS = 7200  # 2 hours retention


# ------------------------------------------------------------------------------
# 1. Nonce Management (Replay Prevention)
# ------------------------------------------------------------------------------
async def create_challenge_nonce(node_id: UUID, ttl_seconds: int = 60) -> str:
    """Generate and store a single-use cryptographically random 32-byte nonce."""
    r = get_redis()
    nonce = os.urandom(32).hex()
    if r is not None:
        key = f"node:nonce:{nonce}"
        await r.set(key, str(node_id), ex=ttl_seconds, nx=True)
    return nonce


async def consume_challenge_nonce(nonce: str, expected_node_id: UUID) -> bool:
    """Atomically consume challenge nonce and verify it was issued for expected node."""
    r = get_redis()
    if r is None:
        # In testing without redis, allow valid length
        return len(nonce) == 64

    key = f"node:nonce:{nonce}"
    stored = await r.getdel(key)
    if not stored:
        return False
    return stored == str(expected_node_id)


# ------------------------------------------------------------------------------
# 2. Concurrency Counter in Redis
# ------------------------------------------------------------------------------
async def acquire_node_concurrency(node_id: UUID, max_concurrent: int = 1) -> bool:
    """Atomically check and increment node concurrency counter."""
    r = get_redis()
    if r is None:
        return True

    key = f"node:concurrency:{node_id}"
    current = await r.incr(key)
    if current == 1:
        await r.expire(key, 600)  # Safe bounded TTL

    if current > max_concurrent:
        await r.decr(key)
        return False
    return True


async def release_node_concurrency(node_id: UUID) -> None:
    """Decrement node concurrency counter."""
    r = get_redis()
    if r is None:
        return

    key = f"node:concurrency:{node_id}"
    val = await r.decr(key)
    if val <= 0:
        await r.delete(key)


# ------------------------------------------------------------------------------
# 3. Redis Streams: Job Dispatch
# ------------------------------------------------------------------------------
async def ensure_consumer_group(node_id: UUID) -> None:
    """Ensure consumer group exists on node's job stream."""
    r = get_redis()
    if r is None:
        return

    stream_key = f"stream:node:{node_id}:jobs"
    try:
        await r.xgroup_create(stream_key, CONSUMER_GROUP_NAME, id="0", mkstream=True)
    except Exception as e:
        if "BUSYGROUP" not in str(e):
            logger.debug(f"Consumer group init on {stream_key}: {e}")


async def enqueue_job_dispatch(
    node_id: UUID,
    job_id: UUID,
    authorization: SignedJobAuthorization,
    payload: dict[str, Any],
) -> str:
    """Enqueue a signed job dispatch entry onto the node's Redis Stream."""
    r = get_redis()
    if r is None:
        return f"{int(os.times()[4] * 1000)}-0"

    await ensure_consumer_group(node_id)
    stream_key = f"stream:node:{node_id}:jobs"
    fields: dict[Any, Any] = {
        "job_id": str(job_id),
        "authorization": authorization.model_dump_json(),
        "payload": json.dumps(payload),
    }
    entry_id = await r.xadd(stream_key, fields, maxlen=500, approximate=True)
    await r.expire(stream_key, JOBS_STREAM_TTL_SECONDS)
    return str(entry_id)


async def read_pending_jobs(
    node_id: UUID,
    consumer_id: str,
    count: int = 1,
) -> list[dict[str, Any]]:
    """Read unread jobs from node's Redis Stream using consumer group."""
    r = get_redis()
    if r is None:
        return []

    stream_key = f"stream:node:{node_id}:jobs"
    try:
        raw_entries = await r.xreadgroup(
            CONSUMER_GROUP_NAME,
            consumer_id,
            {stream_key: ">"},
            count=count,
            block=200,
        )
    except Exception:
        await ensure_consumer_group(node_id)
        return []

    results: list[dict[str, Any]] = []
    if raw_entries and isinstance(raw_entries, list):
        for item in raw_entries:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                _stream_name, stream_entries = item
                if isinstance(stream_entries, list):
                    for entry_item in stream_entries:
                        if isinstance(entry_item, (list, tuple)) and len(entry_item) == 2:
                            entry_id, data = entry_item
                            if isinstance(data, dict):
                                results.append({"entry_id": str(entry_id), **data})
    return results


async def ack_job_dispatch(node_id: UUID, entry_id: str) -> None:
    """Acknowledge job dispatch entry after durable acceptance in PostgreSQL."""
    r = get_redis()
    if r is None:
        return

    stream_key = f"stream:node:{node_id}:jobs"
    await r.xack(stream_key, CONSUMER_GROUP_NAME, entry_id)


# ------------------------------------------------------------------------------
# 4. Redis Streams: Result Chunks & Sequencing
# ------------------------------------------------------------------------------
async def append_job_chunk(
    job_id: UUID,
    seq_id: int,
    delta: str,
    done: bool = False,
    stats: dict[str, Any] | None = None,
) -> str:
    """Append a sequenced token chunk to the job's result stream and publish wake-up."""
    r = get_redis()
    stream_key = f"stream:job:{job_id}:chunks"
    pubsub_channel = f"pubsub:job:{job_id}"
    fields: dict[Any, Any] = {
        "seq_id": str(seq_id),
        "delta": delta,
        "done": "1" if done else "0",
        "stats": json.dumps(stats or {}),
    }

    if r is None:
        return str(seq_id)

    entry_id = await r.xadd(stream_key, fields, maxlen=2000, approximate=True)
    await r.expire(stream_key, CHUNKS_STREAM_TTL_SECONDS)
    await r.publish(pubsub_channel, json.dumps({"seq_id": seq_id, "done": done}))
    return str(entry_id)


async def read_job_chunks_range(
    job_id: UUID,
    start_seq: int = 1,
) -> list[dict[str, Any]]:
    """Read token chunks starting from a specific sequence ID (supports SSE replay)."""
    r = get_redis()
    if r is None:
        return []

    stream_key = f"stream:job:{job_id}:chunks"
    raw_entries = await r.xrange(stream_key, min="-", max="+")
    chunks: list[dict[str, Any]] = []
    if raw_entries and isinstance(raw_entries, list):
        for item in raw_entries:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                _entry_id, data = item
                if isinstance(data, dict):
                    try:
                        seq = int(str(data.get("seq_id", 0)))
                        if seq >= start_seq:
                            chunks.append(
                                {
                                    "seq_id": seq,
                                    "delta": str(data.get("delta", "")),
                                    "done": str(data.get("done")) == "1",
                                    "stats": json.loads(str(data.get("stats", "{}"))),
                                }
                            )
                    except (ValueError, json.JSONDecodeError):
                        continue
    return chunks
