"""Business logic and orchestration for Community Local Inference."""

import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.core.config import settings
from app.core.errors import AuthorizationError, BadRequestError, ConflictError, NotFoundError
from app.modules.community import crypto, repository, streams
from app.modules.community.schemas import (
    CommunityAvailableModel,
    CommunityJobStatusResponse,
    CommunityJobSubmitRequest,
    CommunityJobSubmitResponse,
    CommunityNodeModelResponse,
    CommunityNodeModelsUpdateRequest,
    CommunityNodeResponse,
    CommunityNodeUpdateRequest,
    NodePairRequest,
    NodePairResponse,
    PairingCodeResponse,
    SignedJobAuthorization,
)

logger = logging.getLogger("ragbait.community.service")


# ------------------------------------------------------------------------------
# 1. Pairing & Lifecycle
# ------------------------------------------------------------------------------
async def generate_pairing_code(owner_id: UUID) -> PairingCodeResponse:
    """Generate a single-use 128-bit pairing token (32 hex chars) with configured expiry."""
    raw_bytes = secrets.token_bytes(16)
    hex_str = raw_bytes.hex().upper()
    raw_code = f"RB-{hex_str[:8]}-{hex_str[8:16]}-{hex_str[16:24]}-{hex_str[24:32]}"
    code_hash = hashlib.sha256(raw_code.encode("utf-8")).hexdigest()
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.COMMUNITY_PAIRING_CODE_TTL_SECONDS)

    await repository.create_pairing_code(
        owner_id=owner_id,
        code_hash=code_hash,
        expires_at=expires_at,
    )

    return PairingCodeResponse(
        pairing_code=raw_code,
        expires_at=expires_at,
        ttl_seconds=settings.COMMUNITY_PAIRING_CODE_TTL_SECONDS,
    )


async def complete_pairing(req: NodePairRequest) -> NodePairResponse:
    """Validate and consume pairing code, registering node's public key."""
    canonical_code = req.pairing_code.strip().upper()
    code_hash = hashlib.sha256(canonical_code.encode("utf-8")).hexdigest()
    owner_id = await repository.consume_pairing_code(code_hash)
    if not owner_id:
        raise BadRequestError("Invalid, expired, or already used pairing code")

    # Check if public key already exists
    existing = await repository.get_node_by_public_key(req.public_key.lower())
    if existing:
        if existing["owner_id"] != owner_id:
            raise ConflictError("Public key is already associated with another account")
        if existing["status"] == "revoked":
            raise ConflictError("This node was previously revoked. Please generate a new key.")
        node_id = existing["id"]
        display_name = existing["display_name"]
    else:
        node = await repository.create_node(
            owner_id=owner_id,
            display_name=req.display_name.strip(),
            public_key=req.public_key.lower(),
            agent_version=req.agent_version,
            os_name=req.os,
            architecture=req.architecture,
            ollama_version=req.ollama_version,
        )
        node_id = node["id"]
        display_name = node["display_name"]

    await repository.record_audit_event(
        actor_id=owner_id,
        event_type="node_paired",
        node_id=node_id,
        details={"agent_version": req.agent_version, "display_name": display_name},
    )

    return NodePairResponse(
        node_id=node_id,
        display_name=display_name,
        status="active",
        server_public_key=settings.COMMUNITY_SERVER_VERIFYING_KEY_HEX,
        server_key_id=settings.COMMUNITY_KEY_ID,
    )


async def list_user_nodes(owner_id: UUID) -> list[CommunityNodeResponse]:
    """Retrieve all nodes owned by the contributor."""
    nodes = await repository.list_nodes_by_owner(owner_id)
    results: list[CommunityNodeResponse] = []
    for n in nodes:
        models = await repository.list_node_models(n["id"])
        model_responses = [
            CommunityNodeModelResponse(
                id=m["id"],
                node_id=m["node_id"],
                model_name=m["model_name"],
                enabled=m["enabled"],
                parameter_size=m["parameter_size"],
                quantization_level=m["quantization_level"],
                size_bytes=m["size_bytes"],
                last_seen_at=m["last_seen_at"],
            )
            for m in models
        ]
        results.append(
            CommunityNodeResponse(
                id=n["id"],
                owner_id=n["owner_id"],
                display_name=n["display_name"],
                public_key=n["public_key"],
                key_version=n["key_version"],
                agent_version=n["agent_version"],
                os=n["os"],
                architecture=n["architecture"],
                ollama_version=n["ollama_version"],
                status=n["status"],
                sharing_enabled=n["sharing_enabled"],
                max_concurrent_jobs=n["max_concurrent_jobs"],
                max_execution_duration=n["max_execution_duration"],
                last_seen_at=n["last_seen_at"],
                created_at=n["created_at"],
                revoked_at=n["revoked_at"],
                models=model_responses,
            )
        )
    return results


async def get_user_node(owner_id: UUID, node_id: UUID) -> CommunityNodeResponse:
    """Retrieve an individual owned node with its model allowlist."""
    node = await repository.get_node_by_id(node_id)
    if not node:
        raise NotFoundError("Community node not found")
    if node["owner_id"] != owner_id:
        raise AuthorizationError("Access denied: You do not own this node")

    models = await repository.list_node_models(node_id)
    model_responses = [
        CommunityNodeModelResponse(
            id=m["id"],
            node_id=m["node_id"],
            model_name=m["model_name"],
            enabled=m["enabled"],
            parameter_size=m["parameter_size"],
            quantization_level=m["quantization_level"],
            size_bytes=m["size_bytes"],
            last_seen_at=m["last_seen_at"],
        )
        for m in models
    ]
    return CommunityNodeResponse(
        id=node["id"],
        owner_id=node["owner_id"],
        display_name=node["display_name"],
        public_key=node["public_key"],
        key_version=node["key_version"],
        agent_version=node["agent_version"],
        os=node["os"],
        architecture=node["architecture"],
        ollama_version=node["ollama_version"],
        status=node["status"],
        sharing_enabled=node["sharing_enabled"],
        max_concurrent_jobs=node["max_concurrent_jobs"],
        max_execution_duration=node["max_execution_duration"],
        last_seen_at=node["last_seen_at"],
        created_at=node["created_at"],
        revoked_at=node["revoked_at"],
        models=model_responses,
    )


async def update_node_config(
    owner_id: UUID,
    node_id: UUID,
    req: CommunityNodeUpdateRequest,
) -> CommunityNodeResponse:
    """Update display name, sharing toggle, or limits on an owned node."""
    node = await repository.get_node_by_id(node_id)
    if not node:
        raise NotFoundError("Community node not found")
    if node["owner_id"] != owner_id:
        raise AuthorizationError("Access denied")

    updated = await repository.update_node_config(
        node_id=node_id,
        owner_id=owner_id,
        display_name=req.display_name,
        sharing_enabled=req.sharing_enabled,
        max_concurrent_jobs=req.max_concurrent_jobs,
        max_execution_duration=req.max_execution_duration,
    )
    if not updated:
        raise NotFoundError("Could not update node configuration")

    if req.sharing_enabled is not None:
        await repository.record_audit_event(
            actor_id=owner_id,
            event_type="sharing_toggled",
            node_id=node_id,
            details={"sharing_enabled": req.sharing_enabled},
        )

    return await get_user_node(owner_id, node_id)


async def update_node_models_sharing(
    owner_id: UUID,
    node_id: UUID,
    req: CommunityNodeModelsUpdateRequest,
) -> list[CommunityNodeModelResponse]:
    """Update model sharing checkboxes for an owned node."""
    node = await repository.get_node_by_id(node_id)
    if not node:
        raise NotFoundError("Community node not found")
    if node["owner_id"] != owner_id:
        raise AuthorizationError("Access denied")

    for item in req.models:
        await repository.update_node_model_sharing(
            node_id=node_id,
            model_name=item.model_name,
            enabled=item.enabled,
        )

    await repository.record_audit_event(
        actor_id=owner_id,
        event_type="models_updated",
        node_id=node_id,
        details={"updated_count": len(req.models)},
    )

    models = await repository.list_node_models(node_id)
    return [
        CommunityNodeModelResponse(
            id=m["id"],
            node_id=m["node_id"],
            model_name=m["model_name"],
            enabled=m["enabled"],
            parameter_size=m["parameter_size"],
            quantization_level=m["quantization_level"],
            size_bytes=m["size_bytes"],
            last_seen_at=m["last_seen_at"],
        )
        for m in models
    ]


async def pause_node(owner_id: UUID, node_id: UUID) -> None:
    """Pause an active node so it accepts no new jobs."""
    node = await repository.get_node_by_id(node_id)
    if not node:
        raise NotFoundError("Community node not found")
    if node["owner_id"] != owner_id:
        raise AuthorizationError("Access denied: You do not own this node")
    await repository.update_node_status(node_id, "paused")
    await repository.record_audit_event(owner_id, "node_paused", node_id)


async def resume_node(owner_id: UUID, node_id: UUID) -> None:
    """Resume a paused node."""
    node = await repository.get_node_by_id(node_id)
    if not node:
        raise NotFoundError("Community node not found")
    if node["owner_id"] != owner_id:
        raise AuthorizationError("Access denied: You do not own this node")
    await repository.update_node_status(node_id, "active")
    await repository.record_audit_event(owner_id, "node_resumed", node_id)


async def revoke_node(owner_id: UUID, node_id: UUID) -> None:
    """Permanently revoke a node."""
    success = await repository.revoke_node(node_id, owner_id)
    if not success:
        raise NotFoundError("Node not found or already revoked")
    await repository.record_audit_event(owner_id, "node_revoked", node_id)


# ------------------------------------------------------------------------------
# 2. Requester Community Inference
# ------------------------------------------------------------------------------
async def list_available_models() -> list[CommunityAvailableModel]:
    """List distinct models currently active on community nodes."""
    raw = await repository.list_available_community_models()
    return [
        CommunityAvailableModel(
            model_name=r["model_name"],
            parameter_size=r["parameter_size"],
            online_nodes_count=r["online_nodes_count"],
        )
        for r in raw
    ]


async def submit_community_job(
    requester_id: UUID,
    req: CommunityJobSubmitRequest,
) -> CommunityJobSubmitResponse:
    """Enqueue community inference request after verifying dual consent and node eligibility."""
    # 1. Enforce explicit requester consent
    if not req.consent_accepted:
        raise BadRequestError(
            "Explicit requester consent is required to process prompts on community local nodes"
        )
    if req.consent_policy_version != settings.COMMUNITY_CONSENT_POLICY_VERSION:
        raise BadRequestError(
            f"Consent policy version mismatch. Expected '{settings.COMMUNITY_CONSENT_POLICY_VERSION}'"
        )

    # 2. Find eligible node sharing this model
    node = await repository.find_eligible_node(req.model_name)
    if not node:
        raise ConflictError(
            f"No online community node is currently sharing model '{req.model_name}'. Please try another model or check back later."
        )

    node_id = node["id"]
    hard_deadline_at = datetime.now(UTC) + timedelta(seconds=node["max_execution_duration"])
    job_id = uuid4()

    # 3. Check and acquire node concurrency slot using atomic ZSET reservation
    has_capacity = await streams.acquire_node_concurrency(
        node_id=node_id,
        job_id=job_id,
        max_concurrent=node["max_concurrent_jobs"],
        lease_expires_at=hard_deadline_at.timestamp(),
    )
    if not has_capacity:
        raise ConflictError(
            f"Community node for '{req.model_name}' has reached its maximum concurrent capacity. Please retry shortly."
        )

    # 4. Construct payload and compute payload digest
    payload: dict[str, Any] = {
        "model": req.model_name,
        "messages": [m.model_dump() for m in req.messages],
        "options": {
            "temperature": req.params.temperature,
            "num_predict": req.params.max_output_tokens,
            "top_p": req.params.top_p,
        },
    }
    payload_digest = crypto.compute_payload_digest(payload)

    # 5. Create authoritative record in PostgreSQL
    from app.core.redis import get_community_redis_run_id

    redis_run_id = await get_community_redis_run_id()
    try:
        job = await repository.create_job(
            requester_id=requester_id,
            node_id=node_id,
            model_name=req.model_name,
            payload_digest=payload_digest,
            auth_key_id=settings.COMMUNITY_KEY_ID,
            consent_policy_version=req.consent_policy_version,
            hard_deadline_at=hard_deadline_at,
            job_id=job_id,
            redis_run_id=redis_run_id,
        )
    except Exception:
        await streams.release_node_concurrency(node_id, job_id)
        raise

    # 6. Generate server-signed job authorization token
    auth_nonce = secrets.token_hex(16)
    hard_deadline_ts = int(hard_deadline_at.timestamp())
    max_output = req.params.max_output_tokens or 4096

    signature = crypto.sign_job_authorization(
        job_id=job_id,
        node_id=node_id,
        model_name=req.model_name,
        payload_digest=payload_digest,
        max_output_tokens=max_output,
        hard_deadline_ts=hard_deadline_ts,
        auth_nonce=auth_nonce,
    )

    authorization = SignedJobAuthorization(
        job_id=job_id,
        node_id=node_id,
        model_name=req.model_name,
        payload_digest=payload_digest,
        max_output_tokens=max_output,
        hard_deadline_ts=hard_deadline_ts,
        auth_nonce=auth_nonce,
        signature=signature,
        key_id=settings.COMMUNITY_KEY_ID,
    )

    # 7. Push to node's Redis Stream
    await streams.enqueue_job_dispatch(
        node_id=node_id,
        job_id=job_id,
        authorization=authorization,
        payload=payload,
    )

    return CommunityJobSubmitResponse(
        job_id=job_id,
        node_id=node_id,
        model_name=req.model_name,
        status="QUEUED",
        created_at=job["created_at"],
    )


async def get_job_status(user_id: UUID, job_id: UUID) -> CommunityJobStatusResponse:
    """Retrieve current status of a community inference job."""
    job = await repository.get_job_by_id(job_id)
    if not job:
        raise NotFoundError("Inference job not found")

    # Access check: requester or node owner
    node = await repository.get_node_by_id(job["node_id"])
    if job["requester_id"] != user_id and (not node or node["owner_id"] != user_id):
        raise AuthorizationError("Access denied: You do not have permission to view this job")

    return CommunityJobStatusResponse(
        job_id=job["id"],
        node_id=job["node_id"],
        model_name=job["model_name"],
        status=job["status"],
        accepted_at=job["accepted_at"],
        started_at=job["started_at"],
        completed_at=job["completed_at"],
        cancelled_at=job["cancelled_at"],
        failure_code=job["failure_code"],
        failure_details=job["failure_details"],
        reported_input_tokens=job["reported_input_tokens"],
        reported_output_tokens=job["reported_output_tokens"],
        duration_ms=job["duration_ms"],
        tokens_per_second=float(job["tokens_per_second"]) if job["tokens_per_second"] else None,
        usage_provenance=job["usage_provenance"],
        created_at=job["created_at"],
    )


async def cancel_job(user_id: UUID, job_id: UUID) -> None:
    """Cancel an active or queued job."""
    job = await repository.get_job_by_id(job_id)
    if not job:
        raise NotFoundError("Inference job not found")
    if job["requester_id"] != user_id:
        raise AuthorizationError("Only the job requester can cancel this inference")

    if job["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
        return  # Terminal state already

    now = datetime.now(UTC)
    await repository.update_job_status(
        job_id=job_id,
        status="CANCELLED",
        cancelled_at=now,
        failure_code="CANCELLED_BY_USER",
    )

    # Release concurrency lease in Redis
    await streams.release_node_concurrency(job["node_id"], job_id)
    await repository.record_audit_event(
        user_id, "job_cancelled", job["node_id"], {"job_id": str(job_id)}
    )


async def recover_expired_community_jobs(current_run_id: str | None = None) -> int:
    """Sweeper recovering dangling community jobs, reconciling Redis restarts, and releasing concurrency slots."""
    from app.core.redis import get_community_redis_run_id

    total_recovered = 0
    if current_run_id is None:
        current_run_id = await get_community_redis_run_id()

    # 1. Reconcile lost payloads/streams if dedicated volatile Redis restarted
    if current_run_id:
        restarted_jobs = await repository.reconcile_redis_restart_jobs(current_run_id)
        for j in restarted_jobs:
            try:
                await streams.release_node_concurrency(node_id=j["node_id"], job_id=j["id"])
            except Exception as e:
                logger.warning(f"Error releasing concurrency for restarted job {j['id']}: {e}")
        total_recovered += len(restarted_jobs)

    # 2. Reconcile expired jobs by deadline/lease
    swept_jobs = await repository.sweep_expired_jobs(batch_size=50)
    for j in swept_jobs:
        try:
            await streams.release_node_concurrency(node_id=j["node_id"], job_id=j["id"])
        except Exception as e:
            logger.warning(f"Error releasing concurrency for swept job {j['id']}: {e}")
    total_recovered += len(swept_jobs)

    return total_recovered
