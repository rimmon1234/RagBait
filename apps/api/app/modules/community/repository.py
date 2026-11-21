"""Database repository for Community Local Inference entities."""

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.database import get_pool
from app.core.errors import AuthorizationError

logger = logging.getLogger("ragbait.community.repository")


# ------------------------------------------------------------------------------
# 1. Pairing Codes
# ------------------------------------------------------------------------------
async def create_pairing_code(owner_id: UUID, code_hash: str, expires_at: datetime) -> None:
    """Store short-lived pairing challenge code."""
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.community_node_pairing_codes (owner_id, code_hash, expires_at)
            VALUES ($1, $2, $3)
            """,
            owner_id,
            code_hash,
            expires_at,
        )


async def consume_pairing_code(code_hash: str) -> UUID | None:
    """Atomically consume an unused, unexpired pairing code and return owner_id."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE public.community_node_pairing_codes
            SET used = true, used_at = now()
            WHERE code_hash = $1 AND used = false AND expires_at > now()
            RETURNING owner_id
            """,
            code_hash,
        )
        return row["owner_id"] if row else None


# ------------------------------------------------------------------------------
# 2. Community Nodes
# ------------------------------------------------------------------------------
async def create_node(
    owner_id: UUID,
    display_name: str,
    public_key: str,
    agent_version: str = "0.1.0",
    os_name: str | None = None,
    architecture: str | None = None,
    ollama_version: str | None = None,
) -> dict[str, Any]:
    """Register a new community node in PostgreSQL."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.community_nodes (
                owner_id, display_name, public_key, agent_version,
                os, architecture, ollama_version, status, sharing_enabled
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, 'active', false)
            RETURNING *
            """,
            owner_id,
            display_name,
            public_key,
            agent_version,
            os_name,
            architecture,
            ollama_version,
        )
        return dict(row)


async def get_node_by_id(node_id: UUID) -> dict[str, Any] | None:
    """Fetch node record by UUID."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.community_nodes WHERE id = $1",
            node_id,
        )
        return dict(row) if row else None


async def get_node_by_public_key(public_key: str) -> dict[str, Any] | None:
    """Fetch node record by its 64-char hex Ed25519 public key."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.community_nodes WHERE public_key = $1",
            public_key,
        )
        return dict(row) if row else None


async def list_nodes_by_owner(owner_id: UUID) -> list[dict[str, Any]]:
    """List all nodes registered by a specific contributor."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM public.community_nodes WHERE owner_id = $1 ORDER BY created_at DESC",
            owner_id,
        )
        return [dict(r) for r in rows]


async def update_node_config(
    node_id: UUID,
    owner_id: UUID,
    display_name: str | None = None,
    sharing_enabled: bool | None = None,
    max_concurrent_jobs: int | None = None,
    max_execution_duration: int | None = None,
) -> dict[str, Any] | None:
    """Update owner-configurable fields on a node."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE public.community_nodes
            SET display_name = COALESCE($3, display_name),
                sharing_enabled = COALESCE($4, sharing_enabled),
                max_concurrent_jobs = COALESCE($5, max_concurrent_jobs),
                max_execution_duration = COALESCE($6, max_execution_duration),
                updated_at = now()
            WHERE id = $1 AND owner_id = $2 AND status != 'revoked'
            RETURNING *
            """,
            node_id,
            owner_id,
            display_name,
            sharing_enabled,
            max_concurrent_jobs,
            max_execution_duration,
        )
        return dict(row) if row else None


async def update_node_status(node_id: UUID, status: str) -> None:
    """Authoritatively transition node status."""
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public.community_nodes
            SET status = $2, updated_at = now()
            WHERE id = $1 AND status != 'revoked'
            """,
            node_id,
            status,
        )


async def update_node_heartbeat(
    node_id: UUID,
    agent_version: str | None = None,
    ollama_version: str | None = None,
) -> None:
    """Record node heartbeat timestamp."""
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE public.community_nodes
            SET last_seen_at = now(),
                status = CASE WHEN status = 'offline' THEN 'active' ELSE status END,
                agent_version = COALESCE($2, agent_version),
                ollama_version = COALESCE($3, ollama_version),
                updated_at = now()
            WHERE id = $1 AND status != 'revoked'
            """,
            node_id,
            agent_version,
            ollama_version,
        )


async def revoke_node(node_id: UUID, owner_id: UUID) -> bool:
    """Permanently revoke a community node."""
    pool = get_pool()
    async with pool.acquire() as conn:
        res = await conn.execute(
            """
            UPDATE public.community_nodes
            SET status = 'revoked', revoked_at = now(), sharing_enabled = false, updated_at = now()
            WHERE id = $1 AND owner_id = $2 AND status != 'revoked'
            """,
            node_id,
            owner_id,
        )
        return bool(res == "UPDATE 1")


# ------------------------------------------------------------------------------
# 3. Models
# ------------------------------------------------------------------------------
async def sync_node_models(node_id: UUID, models_data: list[dict[str, Any]]) -> None:
    """Upsert models reported by agent; preserves existing enabled status."""
    if not models_data:
        return
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for m in models_data:
                name = m.get("name") or m.get("model_name")
                if not name:
                    continue
                details = m.get("details", {})
                param_size = details.get("parameter_size") or m.get("parameter_size")
                quant_level = details.get("quantization_level") or m.get("quantization_level")
                size_bytes = m.get("size") or m.get("size_bytes")

                await conn.execute(
                    """
                    INSERT INTO public.community_node_models (
                        node_id, model_name, enabled, parameter_size,
                        quantization_level, size_bytes, last_seen_at
                    )
                    VALUES ($1, $2, false, $3, $4, $5, now())
                    ON CONFLICT (node_id, model_name) DO UPDATE SET
                        parameter_size = COALESCE(EXCLUDED.parameter_size, public.community_node_models.parameter_size),
                        quantization_level = COALESCE(EXCLUDED.quantization_level, public.community_node_models.quantization_level),
                        size_bytes = COALESCE(EXCLUDED.size_bytes, public.community_node_models.size_bytes),
                        last_seen_at = now(),
                        updated_at = now()
                    """,
                    node_id,
                    name,
                    param_size,
                    quant_level,
                    size_bytes,
                )


async def list_node_models(node_id: UUID) -> list[dict[str, Any]]:
    """List all models registered for a specific node."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM public.community_node_models WHERE node_id = $1 ORDER BY model_name ASC",
            node_id,
        )
        return [dict(r) for r in rows]


async def update_node_model_sharing(node_id: UUID, model_name: str, enabled: bool) -> bool:
    """Toggle sharing status for a single model on a node."""
    pool = get_pool()
    async with pool.acquire() as conn:
        res = await conn.execute(
            """
            UPDATE public.community_node_models
            SET enabled = $3, updated_at = now()
            WHERE node_id = $1 AND model_name = $2
            """,
            node_id,
            model_name,
            enabled,
        )
        return bool(res == "UPDATE 1")


async def list_available_community_models() -> list[dict[str, Any]]:
    """Query currently available models on online, sharing-enabled community nodes."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                cnm.model_name,
                cnm.parameter_size,
                count(DISTINCT cn.id) as online_nodes_count
            FROM public.community_node_models cnm
            JOIN public.community_nodes cn ON cn.id = cnm.node_id
            WHERE cnm.enabled = true
              AND cn.sharing_enabled = true
              AND cn.status = 'active'
              AND cn.last_seen_at >= now() - interval '60 seconds'
            GROUP BY cnm.model_name, cnm.parameter_size
            ORDER BY cnm.model_name ASC
            """
        )
        return [dict(r) for r in rows]


# ------------------------------------------------------------------------------
# 4. Job Scheduling & Queries
# ------------------------------------------------------------------------------
async def find_eligible_node(model_name: str) -> dict[str, Any] | None:
    """Find an active, online node that shares this model and has available concurrency."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT cn.*
            FROM public.community_nodes cn
            JOIN public.community_node_models cnm ON cnm.node_id = cn.id
            WHERE cnm.model_name = $1
              AND cnm.enabled = true
              AND cn.sharing_enabled = true
              AND cn.status = 'active'
              AND cn.last_seen_at >= now() - interval '45 seconds'
            ORDER BY cn.last_seen_at DESC
            LIMIT 1
            """,
            model_name,
        )
        return dict(row) if row else None


async def create_job(
    requester_id: UUID,
    node_id: UUID,
    model_name: str,
    payload_digest: str,
    auth_key_id: str,
    consent_policy_version: str,
    hard_deadline_at: datetime,
    job_id: UUID | None = None,
    redis_run_id: str | None = None,
) -> dict[str, Any]:
    """Persist new community inference job in QUEUED status."""
    pool = get_pool()
    async with pool.acquire() as conn:
        if job_id:
            row = await conn.fetchrow(
                """
                INSERT INTO public.community_inference_jobs (
                    id, requester_id, node_id, model_name, status,
                    payload_digest, authorization_key_id, consent_policy_version,
                    hard_deadline_at, redis_run_id
                )
                VALUES ($1, $2, $3, $4, 'QUEUED', $5, $6, $7, $8, $9)
                RETURNING *
                """,
                job_id,
                requester_id,
                node_id,
                model_name,
                payload_digest,
                auth_key_id,
                consent_policy_version,
                hard_deadline_at,
                redis_run_id,
            )
        else:
            row = await conn.fetchrow(
                """
                INSERT INTO public.community_inference_jobs (
                    requester_id, node_id, model_name, status,
                    payload_digest, authorization_key_id, consent_policy_version,
                    hard_deadline_at, redis_run_id
                )
                VALUES ($1, $2, $3, 'QUEUED', $4, $5, $6, $7, $8)
                RETURNING *
                """,
                requester_id,
                node_id,
                model_name,
                payload_digest,
                auth_key_id,
                consent_policy_version,
                hard_deadline_at,
                redis_run_id,
            )
        return dict(row)


async def get_job_by_id(job_id: UUID) -> dict[str, Any] | None:
    """Fetch authoritative job record."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.community_inference_jobs WHERE id = $1",
            job_id,
        )
        return dict(row) if row else None


async def update_job_status(
    job_id: UUID,
    status: str,
    accepted_at: datetime | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    cancelled_at: datetime | None = None,
    lease_expires_at: datetime | None = None,
    failure_code: str | None = None,
    failure_details: str | None = None,
    reported_input_tokens: int | None = None,
    reported_output_tokens: int | None = None,
    duration_ms: int | None = None,
    tokens_per_second: float | None = None,
) -> dict[str, Any] | None:
    """Atomically update job lifecycle state."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE public.community_inference_jobs
            SET status = $2,
                accepted_at = COALESCE($3, accepted_at),
                started_at = COALESCE($4, started_at),
                completed_at = COALESCE($5, completed_at),
                cancelled_at = COALESCE($6, cancelled_at),
                lease_expires_at = COALESCE($7, lease_expires_at),
                failure_code = COALESCE($8, failure_code),
                failure_details = COALESCE($9, failure_details),
                reported_input_tokens = COALESCE($10, reported_input_tokens),
                reported_output_tokens = COALESCE($11, reported_output_tokens),
                duration_ms = COALESCE($12, duration_ms),
                tokens_per_second = COALESCE($13, tokens_per_second),
                updated_at = now()
            WHERE id = $1
              AND status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED', 'EXPIRED', 'OUTCOME_UNKNOWN')
            RETURNING *
            """,
            job_id,
            status,
            accepted_at,
            started_at,
            completed_at,
            cancelled_at,
            lease_expires_at,
            failure_code,
            failure_details,
            reported_input_tokens,
            reported_output_tokens,
            duration_ms,
            tokens_per_second,
        )
        return dict(row) if row else None


async def sweep_expired_jobs(batch_size: int = 50) -> list[dict[str, Any]]:
    """Identify expired or dangling jobs across non-terminal states and transition atomically."""
    pool = get_pool()
    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE public.community_inference_jobs
            SET status = CASE
                    WHEN status = 'RUNNING' THEN 'OUTCOME_UNKNOWN'
                    ELSE 'EXPIRED'
                END,
                failure_code = CASE
                    WHEN status = 'RUNNING' THEN 'LEASE_EXPIRED'
                    ELSE 'HARD_DEADLINE_EXPIRED'
                END,
                failure_details = CASE
                    WHEN status = 'RUNNING' THEN 'Node lease expired without renewal'
                    ELSE 'Hard execution deadline elapsed before completion'
                END,
                completed_at = COALESCE(completed_at, $1),
                updated_at = now()
            WHERE id IN (
                SELECT id FROM public.community_inference_jobs
                WHERE (status = 'RUNNING' AND lease_expires_at < $1)
                   OR (status IN ('QUEUED', 'CLAIMED', 'SENT', 'AGENT_ACCEPTED') AND hard_deadline_at < $1)
                ORDER BY created_at ASC
                LIMIT $2
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, node_id, status
            """,
            now,
            batch_size,
        )
        return [dict(r) for r in rows]


async def register_node_session(node_id: UUID, session_id: str) -> tuple[int, str]:
    """Atomically register a new authenticated session under row lock, incrementing session_epoch."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id, status, session_epoch
                FROM public.community_nodes
                WHERE id = $1
                FOR UPDATE
                """,
                node_id,
            )
            if not row or row["status"] == "revoked":
                raise AuthorizationError("Node does not exist or has been revoked")

            new_epoch = row["session_epoch"] + 1
            await conn.execute(
                """
                UPDATE public.community_nodes
                SET active_session_id = $2,
                    session_epoch = $3,
                    status = 'active',
                    last_seen_at = now(),
                    updated_at = now()
                WHERE id = $1
                """,
                node_id,
                session_id,
                new_epoch,
            )
            return new_epoch, session_id


async def verify_node_session(
    node_id: UUID, session_id: str, expected_epoch: int | None = None
) -> bool:
    """Authoritatively verify against PostgreSQL that the session_id and epoch are current and active."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT active_session_id, session_epoch, status
            FROM public.community_nodes
            WHERE id = $1
            """,
            node_id,
        )
        if not row or row["status"] != "active":
            return False
        if row["active_session_id"] != session_id:
            return False
        if expected_epoch is not None and row["session_epoch"] != expected_epoch:
            return False
        return True


async def update_job_status_fenced(
    node_id: UUID,
    session_id: str,
    job_id: UUID,
    status: str,
    expected_epoch: int | None = None,
    accepted_at: datetime | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    cancelled_at: datetime | None = None,
    lease_expires_at: datetime | None = None,
    failure_code: str | None = None,
    failure_details: str | None = None,
    reported_input_tokens: int | None = None,
    reported_output_tokens: int | None = None,
    duration_ms: int | None = None,
    tokens_per_second: float | None = None,
) -> dict[str, Any] | None:
    """Atomically update job lifecycle state only if the worker's session is the authoritative active session."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            node = await conn.fetchrow(
                """
                SELECT active_session_id, session_epoch, status
                FROM public.community_nodes
                WHERE id = $1
                FOR SHARE
                """,
                node_id,
            )
            if not node or node["status"] != "active" or node["active_session_id"] != session_id:
                logger.warning(
                    f"Rejected fenced job update on {job_id}: Session {session_id} is superseded "
                    f"(authoritative: {node['active_session_id'] if node else None})"
                )
                return None
            if expected_epoch is not None and node["session_epoch"] != expected_epoch:
                logger.warning(
                    f"Rejected fenced job update on {job_id}: Epoch mismatch ({expected_epoch} vs {node['session_epoch']})"
                )
                return None

            row = await conn.fetchrow(
                """
                UPDATE public.community_inference_jobs
                SET status = $2,
                    accepted_at = COALESCE($3, accepted_at),
                    started_at = COALESCE($4, started_at),
                    completed_at = COALESCE($5, completed_at),
                    cancelled_at = COALESCE($6, cancelled_at),
                    lease_expires_at = COALESCE($7, lease_expires_at),
                    failure_code = COALESCE($8, failure_code),
                    failure_details = COALESCE($9, failure_details),
                    reported_input_tokens = COALESCE($10, reported_input_tokens),
                    reported_output_tokens = COALESCE($11, reported_output_tokens),
                    duration_ms = COALESCE($12, duration_ms),
                    tokens_per_second = COALESCE($13, tokens_per_second),
                    updated_at = now()
                WHERE id = $1
                  AND node_id = $14
                  AND status NOT IN ('COMPLETED', 'FAILED', 'CANCELLED', 'EXPIRED', 'OUTCOME_UNKNOWN')
                RETURNING *
                """,
                job_id,
                status,
                accepted_at,
                started_at,
                completed_at,
                cancelled_at,
                lease_expires_at,
                failure_code,
                failure_details,
                reported_input_tokens,
                reported_output_tokens,
                duration_ms,
                tokens_per_second,
                node_id,
            )
            return dict(row) if row else None


async def renew_job_lease_fenced(
    node_id: UUID,
    session_id: str,
    job_id: UUID,
    lease_expires_at: datetime,
    expected_epoch: int | None = None,
) -> dict[str, Any] | None:
    """Renew job lease only if session is authoritative and active."""
    return await update_job_status_fenced(
        node_id=node_id,
        session_id=session_id,
        job_id=job_id,
        status="RUNNING",
        expected_epoch=expected_epoch,
        lease_expires_at=lease_expires_at,
    )


async def reconcile_redis_restart_jobs(current_run_id: str) -> list[dict[str, Any]]:
    """Transition non-terminal jobs whose volatile Redis queue generation differs from current Redis run_id."""
    pool = get_pool()
    now = datetime.now(UTC)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE public.community_inference_jobs
            SET status = CASE
                    WHEN status IN ('AGENT_ACCEPTED', 'RUNNING') THEN 'OUTCOME_UNKNOWN'
                    ELSE 'EXPIRED'
                END,
                failure_code = CASE
                    WHEN status IN ('AGENT_ACCEPTED', 'RUNNING') THEN 'REDIS_RESTART_STREAM_LOST'
                    ELSE 'REDIS_RESTART_PAYLOAD_LOST'
                END,
                failure_details = CASE
                    WHEN status IN ('AGENT_ACCEPTED', 'RUNNING') THEN 'Dedicated community Redis restarted mid-execution; stream lost'
                    ELSE 'Dedicated community Redis restarted before job acceptance; payload evicted'
                END,
                completed_at = COALESCE(completed_at, $1),
                updated_at = now()
            WHERE status IN ('QUEUED', 'CLAIMED', 'SENT', 'AGENT_ACCEPTED', 'RUNNING')
              AND redis_run_id IS NOT NULL
              AND redis_run_id != $2
            RETURNING id, node_id, status
            """,
            now,
            current_run_id,
        )
        return [dict(r) for r in rows]


async def update_node_active_session(node_id: UUID, session_id: str) -> int:
    """Update active session ID and increment session epoch for session fencing."""
    new_epoch, _ = await register_node_session(node_id, session_id)
    return new_epoch


async def get_node_active_session(node_id: UUID) -> str | None:
    """Fetch currently active session ID for session fencing verification."""
    pool = get_pool()
    async with pool.acquire() as conn:
        val = await conn.fetchval(
            "SELECT active_session_id FROM public.community_nodes WHERE id = $1",
            node_id,
        )
        return str(val) if val is not None else None


# ------------------------------------------------------------------------------
# 5. Audit Logging
# ------------------------------------------------------------------------------
async def record_audit_event(
    actor_id: UUID,
    event_type: str,
    node_id: UUID | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Record immutable community audit event."""
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO public.community_node_audit_events (
                node_id, actor_id, event_type, details
            )
            VALUES ($1, $2, $3, $4::jsonb)
            """,
            node_id,
            actor_id,
            event_type,
            json.dumps(details or {}),
        )
