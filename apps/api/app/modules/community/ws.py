"""WebSocket endpoint handling outbound connections from Ollama Node Agents."""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status

from app.core.config import settings
from app.modules.community import crypto, repository, streams
from app.modules.community.schemas import (
    SignedJobAuthorization,
    WSAuthMessage,
    WSAuthSuccessMessage,
    WSChallengeMessage,
    WSErrorMessage,
    WSHeartbeatAckMessage,
    WSHeartbeatMessage,
    WSJobAcceptedMessage,
    WSJobChunkMessage,
    WSJobCompletedMessage,
    WSJobDispatchMessage,
)

logger = logging.getLogger("ragbait.community.ws")
ws_router = APIRouter()


@ws_router.websocket("/ws")
async def community_node_websocket(websocket: WebSocket) -> None:
    """Handle authenticated outbound WebSocket connection from an Ollama Node Agent."""
    await websocket.accept()

    node_id_str = websocket.headers.get("x-node-id")
    if not node_id_str:
        # Check query params as secondary
        node_id_str = websocket.query_params.get("node_id")

    if not node_id_str:
        await websocket.send_text(
            WSErrorMessage(
                code="MISSING_NODE_ID", message="X-Node-ID header is required"
            ).model_dump_json()
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        node_id = UUID(node_id_str)
    except ValueError:
        await websocket.send_text(
            WSErrorMessage(
                code="INVALID_NODE_ID", message="Invalid node UUID format"
            ).model_dump_json()
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Fetch node from DB
    node = await repository.get_node_by_id(node_id)
    if not node or node["status"] == "revoked":
        await websocket.send_text(
            WSErrorMessage(
                code="NODE_NOT_FOUND", message="Node does not exist or has been revoked"
            ).model_dump_json()
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # 1. Issue fresh challenge nonce
    nonce = await streams.create_challenge_nonce(node_id, ttl_seconds=60)
    session_id = f"sess-{time.time_ns()}"
    now_ts = int(time.time())

    challenge = WSChallengeMessage(
        nonce=nonce,
        session_id=session_id,
        timestamp=now_ts,
    )
    await websocket.send_text(challenge.model_dump_json())

    # 2. Wait for auth response
    try:
        raw_auth = await asyncio.wait_for(websocket.receive_text(), timeout=15.0)
        auth_data = json.loads(raw_auth)
        auth_msg = WSAuthMessage(**auth_data)
    except (TimeoutError, Exception) as e:
        logger.warning(f"Handshake failed for node {node_id}: {e}")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # 3. Verify challenge nonce and timestamp
    if abs(int(time.time()) - auth_msg.timestamp) > 60:
        await websocket.send_text(
            WSErrorMessage(
                code="EXPIRED_TIMESTAMP", message="Handshake timestamp clock skew exceeds 60s"
            ).model_dump_json()
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    nonce_valid = await streams.consume_challenge_nonce(nonce, node_id)
    if not nonce_valid:
        await websocket.send_text(
            WSErrorMessage(
                code="INVALID_NONCE", message="Challenge nonce is invalid or already consumed"
            ).model_dump_json()
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # 4. Verify Ed25519 signature
    sig_valid = crypto.verify_node_challenge_signature(
        signature_hex=auth_msg.signature,
        node_id=node_id,
        nonce_hex=nonce,
        session_id=session_id,
        timestamp=auth_msg.timestamp,
        node_public_key_hex=node["public_key"],
    )

    if not sig_valid:
        await websocket.send_text(
            WSErrorMessage(
                code="INVALID_SIGNATURE", message="Ed25519 challenge signature verification failed"
            ).model_dump_json()
        )
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # 5. Auth Success
    await repository.update_node_status(node_id, "active")
    await repository.update_node_heartbeat(node_id)
    await websocket.send_text(
        WSAuthSuccessMessage(
            session_id=session_id,
            heartbeat_interval_sec=settings.COMMUNITY_HEARTBEAT_INTERVAL_SECONDS,
        ).model_dump_json()
    )

    # 6. Active connection loop
    consumer_id = f"cons-{session_id}"
    dispatch_task = asyncio.create_task(_dispatch_loop(websocket, node_id, consumer_id))
    receive_task = asyncio.create_task(_receive_loop(websocket, node_id))

    try:
        done, pending = await asyncio.wait(
            [dispatch_task, receive_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    except Exception as e:
        logger.debug(f"Node {node_id} connection terminated: {e}")
    finally:
        dispatch_task.cancel()
        receive_task.cancel()
        await repository.update_node_status(node_id, "offline")


async def _dispatch_loop(websocket: WebSocket, node_id: UUID, consumer_id: str) -> None:
    """Continuously poll node's Redis Stream and dispatch jobs over WebSocket."""
    while True:
        try:
            entries = await streams.read_pending_jobs(node_id, consumer_id=consumer_id, count=1)
            for entry in entries:
                job_id = entry["job_id"]
                auth_data = json.loads(entry["authorization"])
                payload_data = json.loads(entry["payload"])

                # Check if job was cancelled while in queue
                db_job = await repository.get_job_by_id(UUID(job_id))
                if not db_job or db_job["status"] == "CANCELLED":
                    await streams.ack_job_dispatch(node_id, entry["entry_id"])
                    continue

                dispatch_frame = WSJobDispatchMessage(
                    job_id=job_id,
                    authorization=SignedJobAuthorization(**auth_data),
                    payload=payload_data,
                )
                await websocket.send_text(dispatch_frame.model_dump_json())

                # Update status to SENT
                await repository.update_job_status(UUID(job_id), "SENT")

            await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Error in dispatch loop for node {node_id}: {e}")
            await asyncio.sleep(1.0)


async def _receive_loop(websocket: WebSocket, node_id: UUID) -> None:
    """Handle incoming protocol frames from the agent."""
    while True:
        try:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            frame_type = data.get("type")

            if frame_type == "heartbeat":
                hb = WSHeartbeatMessage(**data)
                await repository.update_node_heartbeat(node_id)
                if hb.installed_models:
                    await repository.sync_node_models(node_id, hb.installed_models)
                await websocket.send_text(
                    WSHeartbeatAckMessage(timestamp=int(time.time())).model_dump_json()
                )

            elif frame_type == "job_accepted":
                acc = WSJobAcceptedMessage(**data)
                job_id = UUID(acc.job_id)
                lease_exp = datetime.now(UTC) + timedelta(
                    seconds=settings.COMMUNITY_JOB_LEASE_SECONDS
                )
                await repository.update_job_status(
                    job_id=job_id,
                    status="RUNNING",
                    accepted_at=datetime.now(UTC),
                    lease_expires_at=lease_exp,
                )

            elif frame_type == "job_chunk":
                chunk = WSJobChunkMessage(**data)
                job_id = UUID(chunk.job_id)

                # Check if cancelled
                db_job = await repository.get_job_by_id(job_id)
                if db_job and db_job["status"] == "CANCELLED":
                    # Drop chunks from cancelled job
                    continue

                # Renew lease
                lease_exp = datetime.now(UTC) + timedelta(
                    seconds=settings.COMMUNITY_JOB_LEASE_SECONDS
                )
                await repository.update_job_status(job_id, "RUNNING", lease_expires_at=lease_exp)

                # Append to Redis Stream
                await streams.append_job_chunk(
                    job_id=job_id,
                    seq_id=chunk.seq_id,
                    delta=chunk.delta,
                    done=chunk.done,
                )

            elif frame_type == "job_completed":
                comp = WSJobCompletedMessage(**data)
                job_id = UUID(comp.job_id)
                now = datetime.now(UTC)

                # Atomically update DB to COMPLETED
                await repository.update_job_status(
                    job_id=job_id,
                    status="COMPLETED",
                    completed_at=now,
                    reported_input_tokens=comp.reported_input_tokens,
                    reported_output_tokens=comp.reported_output_tokens,
                    duration_ms=comp.duration_ms,
                    tokens_per_second=comp.tokens_per_second,
                )

                # Release concurrency slot
                await streams.release_node_concurrency(node_id)

                # Append terminal chunk
                await streams.append_job_chunk(
                    job_id=job_id,
                    seq_id=999999,
                    delta="",
                    done=True,
                    stats={
                        "input_tokens": comp.reported_input_tokens,
                        "output_tokens": comp.reported_output_tokens,
                        "duration_ms": comp.duration_ms,
                        "tokens_per_second": comp.tokens_per_second,
                    },
                )

        except (WebSocketDisconnect, asyncio.CancelledError):
            break
        except Exception as e:
            logger.warning(f"Error handling frame from node {node_id}: {e}")
