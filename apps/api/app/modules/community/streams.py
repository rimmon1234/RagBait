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
    if r is None:
        logger.error("Cannot create challenge nonce: Redis is unavailable")
        from app.core.errors import AppError

        raise AppError(
            code="SERVICE_UNAVAILABLE",
            message="Security challenge service temporarily unavailable",
            status_code=503,
        )

    nonce = os.urandom(32).hex()
    key = f"node:nonce:{nonce}"
    await r.set(key, str(node_id), ex=ttl_seconds, nx=True)
    return nonce


async def consume_challenge_nonce(nonce: str, expected_node_id: UUID) -> bool:
    """Atomically consume challenge nonce and verify it was issued for expected node.

    Fails closed: Rejects nonces if Redis is unavailable or disconnected.
    Never accepts nonces based on length/format alone.
    """
    r = get_redis()
    if r is None:
        logger.error("Cannot consume challenge nonce: Redis is unavailable")
        from app.core.errors import AppError

        raise AppError(
            code="SERVICE_UNAVAILABLE",
            message="Security challenge verification temporarily unavailable",
            status_code=503,
        )

    key = f"node:nonce:{nonce}"
    stored = await r.getdel(key)
    if not stored:
        return False
    return stored == str(expected_node_id)


# ------------------------------------------------------------------------------
# 2. Concurrency Reservations in Redis (Sorted Set)
# ------------------------------------------------------------------------------
ACQUIRE_CONCURRENCY_LUA = """
local key = KEYS[1]
local job_id = ARGV[1]
local max_concurrent = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local lease_exp = tonumber(ARGV[4])

-- 1. Prune expired reservations
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)

-- 2. Idempotency check: if job_id already holds a reservation, update lease timestamp
local existing_score = redis.call('ZSCORE', key, job_id)
if existing_score then
    redis.call('ZADD', key, lease_exp, job_id)
    return 1
end

-- 3. Check capacity against active count
local active_count = redis.call('ZCARD', key)
if active_count >= max_concurrent then
    return 0
end

-- 4. Record new reservation
redis.call('ZADD', key, lease_exp, job_id)
return 1
"""

RENEW_CONCURRENCY_LUA = """
local key = KEYS[1]
local job_id = ARGV[1]
local lease_exp = tonumber(ARGV[2])

local existing_score = redis.call('ZSCORE', key, job_id)
if existing_score then
    redis.call('ZADD', key, lease_exp, job_id)
    return 1
end
return 0
"""


async def acquire_node_concurrency(
    node_id: UUID,
    job_id: UUID,
    max_concurrent: int,
    lease_expires_at: float,
) -> bool:
    """Atomically acquire an idempotent concurrency slot using Redis Sorted Set."""
    r = get_redis()
    if r is None:
        return True

    import time
    now = time.time()
    key = f"ragbait:comm:node:{node_id}:reservations"
    res = await r.eval(
        ACQUIRE_CONCURRENCY_LUA,
        1,
        key,
        str(job_id),
        max_concurrent,
        now,
        lease_expires_at,
    )
    return res == 1


async def renew_node_concurrency(
    node_id: UUID,
    job_id: UUID,
    lease_expires_at: float,
) -> bool:
    """Atomically extend the lease score for an active job reservation."""
    r = get_redis()
    if r is None:
        return True

    key = f"ragbait:comm:node:{node_id}:reservations"
    res = await r.eval(
        RENEW_CONCURRENCY_LUA,
        1,
        key,
        str(job_id),
        lease_expires_at,
    )
    return res == 1


async def release_node_concurrency(node_id: UUID, job_id: UUID) -> bool:
    """Idempotently release a specific job reservation from the sorted set."""
    r = get_redis()
    if r is None:
        return True

    key = f"ragbait:comm:node:{node_id}:reservations"
    val = await r.zrem(key, str(job_id))
    return val > 0


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


async def autoclaim_pending_jobs(
    node_id: UUID,
    consumer_id: str,
    min_idle_seconds: int = 30,
    count: int = 10,
) -> list[dict[str, Any]]:
    """Recover unacknowledged jobs left pending in PEL by dead consumer sessions."""
    r = get_redis()
    if r is None:
        return []

    stream_key = f"stream:node:{node_id}:jobs"
    await ensure_consumer_group(node_id)
    min_idle_ms = int(min_idle_seconds * 1000)
    try:
        res = await r.xautoclaim(
            stream_key,
            CONSUMER_GROUP_NAME,
            consumer_id,
            min_idle_time=min_idle_ms,
            start_id="0-0",
            count=count,
        )
        entries = res[1] if isinstance(res, (list, tuple)) and len(res) >= 2 else []
        results: list[dict[str, Any]] = []
        if isinstance(entries, list):
            for entry_item in entries:
                if isinstance(entry_item, (list, tuple)) and len(entry_item) == 2:
                    entry_id, data = entry_item
                    if isinstance(data, dict):
                        results.append({"entry_id": str(entry_id), **data})
        return results
    except Exception as e:
        logger.debug(f"XAUTOCLAIM error on {stream_key}: {e}")
        return []


async def ack_job_dispatch(node_id: UUID, entry_id: str) -> None:
    """Acknowledge job dispatch entry after durable acceptance in PostgreSQL."""
    r = get_redis()
    if r is None:
        return

    stream_key = f"stream:node:{node_id}:jobs"
    await r.xack(stream_key, CONSUMER_GROUP_NAME, entry_id)


async def ack_and_delete_job_dispatch(node_id: UUID, entry_id: str) -> None:
    """Acknowledge and delete dispatch entry to minimize sensitive prompt retention."""
    r = get_redis()
    if r is None:
        return

    stream_key = f"stream:node:{node_id}:jobs"
    await r.xack(stream_key, CONSUMER_GROUP_NAME, entry_id)
    await r.xdel(stream_key, entry_id)


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
