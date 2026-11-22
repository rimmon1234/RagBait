"""WebSocket endpoint for Ollama Node Agents (DISCONTINUED).

Authoritative Product Policy:
A student's local Ollama models and computing resources are strictly private.
Community Node Agents are no longer permitted to connect or receive dispatch work.
"""

import logging

from fastapi import APIRouter, WebSocket, status

from app.modules.community.schemas import WSErrorMessage

logger = logging.getLogger("ragbait.community.ws")
ws_router = APIRouter()


@ws_router.websocket("/ws")
async def community_node_websocket(websocket: WebSocket) -> None:
    """Immediately reject outbound WebSocket connection from any Ollama Node Agent."""
    await websocket.accept()
    await websocket.send_text(
        WSErrorMessage(
            code="COMMUNITY_LOCAL_INFERENCE_DISCONTINUED",
            message=(
                "Community local inference and Ollama node sharing have been discontinued. "
                "A student's local Ollama models and computing resources remain strictly private."
            ),
        ).model_dump_json()
    )
<<<<<<< HEAD

    # 6. Active connection loop
    consumer_id = f"cons-{authoritative_session_id}"
    dispatched_map: dict[str, str] = {}  # job_id -> entry_id
    dispatch_task = asyncio.create_task(
        _dispatch_loop(
            websocket, node_id, consumer_id, authoritative_session_id, epoch, dispatched_map
        )
    )
    receive_task = asyncio.create_task(
        _receive_loop(websocket, node_id, authoritative_session_id, epoch, dispatched_map)
    )

    ACTIVE_CONNECTIONS[node_id] = (
        authoritative_session_id,
        websocket,
        [dispatch_task, receive_task],
    )

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
        if ACTIVE_CONNECTIONS.get(node_id, (None,))[0] == authoritative_session_id:
            ACTIVE_CONNECTIONS.pop(node_id, None)
            # Only mark offline if session is still authoritative in PostgreSQL
            if await repository.verify_node_session(node_id, authoritative_session_id, epoch):
                await repository.update_node_status(node_id, "offline")


async def _dispatch_loop(
    websocket: WebSocket,
    node_id: UUID,
    consumer_id: str,
    session_id: str,
    epoch: int,
    dispatched_map: dict[str, str],
) -> None:
    """Continuously poll node's Redis Stream, recover pending PEL entries, and dispatch jobs over WebSocket."""
    last_autoclaim_time = 0.0
    while True:
        try:
            # 1. Authoritative session verification
            if not await repository.verify_node_session(node_id, session_id, epoch):
                logger.info(
                    f"Dispatch loop terminating: session {session_id} (epoch {epoch}) superseded for node {node_id}"
                )
                break

            now = time.time()
            # Periodically (every 10s) recover pending jobs from PEL via XAUTOCLAIM
            if now - last_autoclaim_time > 10.0:
                last_autoclaim_time = now
                claimed_entries = await streams.autoclaim_pending_jobs(
                    node_id=node_id,
                    consumer_id=consumer_id,
                    min_idle_seconds=settings.COMMUNITY_PENDING_CLAIM_IDLE_SECONDS,
                    count=10,
                )
                for entry in claimed_entries:
                    job_id_str = entry["job_id"]
                    entry_id = entry["entry_id"]
                    try:
                        job_uuid = UUID(job_id_str)
                    except ValueError:
                        await streams.ack_and_delete_job_dispatch(node_id, entry_id)
                        continue

                    db_job = await repository.get_job_by_id(job_uuid)
                    if not db_job or db_job["status"] in (
                        "COMPLETED",
                        "FAILED",
                        "CANCELLED",
                        "EXPIRED",
                        "OUTCOME_UNKNOWN",
                    ):
                        await streams.ack_and_delete_job_dispatch(node_id, entry_id)
                        continue

                    if db_job["hard_deadline_at"] <= datetime.now(UTC):
                        await repository.update_job_status(
                            job_uuid, "EXPIRED", failure_code="HARD_DEADLINE_EXPIRED"
                        )
                        await streams.release_node_concurrency(node_id, job_uuid)
                        await streams.ack_and_delete_job_dispatch(node_id, entry_id)
                        continue

                    if db_job["status"] in ("AGENT_ACCEPTED", "RUNNING"):
                        await streams.ack_and_delete_job_dispatch(node_id, entry_id)
                        continue

                    # Re-dispatch
                    auth_data = json.loads(entry["authorization"])
                    payload_data = json.loads(entry["payload"])
                    dispatch_frame = WSJobDispatchMessage(
                        job_id=job_id_str,
                        authorization=SignedJobAuthorization(**auth_data),
                        payload=payload_data,
                    )
                    dispatched_map[job_id_str] = entry_id
                    await websocket.send_text(dispatch_frame.model_dump_json())
                    await repository.update_job_status(job_uuid, "SENT")

            # Read unread entries
            entries = await streams.read_pending_jobs(node_id, consumer_id=consumer_id, count=1)
            for entry in entries:
                job_id_str = entry["job_id"]
                entry_id = entry["entry_id"]
                try:
                    job_uuid = UUID(job_id_str)
                except ValueError:
                    await streams.ack_and_delete_job_dispatch(node_id, entry_id)
                    continue

                db_job = await repository.get_job_by_id(job_uuid)
                if not db_job or db_job["status"] in (
                    "COMPLETED",
                    "FAILED",
                    "CANCELLED",
                    "EXPIRED",
                    "OUTCOME_UNKNOWN",
                ):
                    await streams.ack_and_delete_job_dispatch(node_id, entry_id)
                    continue

                if db_job["hard_deadline_at"] <= datetime.now(UTC):
                    await repository.update_job_status(
                        job_uuid, "EXPIRED", failure_code="HARD_DEADLINE_EXPIRED"
                    )
                    await streams.release_node_concurrency(node_id, job_uuid)
                    await streams.ack_and_delete_job_dispatch(node_id, entry_id)
                    continue

                auth_data = json.loads(entry["authorization"])
                payload_data = json.loads(entry["payload"])

                dispatch_frame = WSJobDispatchMessage(
                    job_id=job_id_str,
                    authorization=SignedJobAuthorization(**auth_data),
                    payload=payload_data,
                )
                dispatched_map[job_id_str] = entry_id
                await websocket.send_text(dispatch_frame.model_dump_json())
                await repository.update_job_status(job_uuid, "SENT")

            await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Error in dispatch loop for node {node_id}: {e}")
            await asyncio.sleep(1.0)


async def _receive_loop(
    websocket: WebSocket,
    node_id: UUID,
    session_id: str,
    epoch: int,
    dispatched_map: dict[str, str],
) -> None:
    """Handle incoming protocol frames from the agent with PostgreSQL-authoritative session fencing."""
    while True:
        try:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            frame_type = data.get("type")

            # 1. Authoritative PostgreSQL session fencing verification
            is_valid_session = await repository.verify_node_session(node_id, session_id, epoch)
            if not is_valid_session:
                logger.warning(
                    f"Received frame {frame_type} from superseded session {session_id} (epoch {epoch}) "
                    f"for node {node_id}. Closing connection with WS 4001."
                )
                try:
                    await websocket.close(code=4001, reason="Session superseded")
                except Exception:
                    pass
                break

            if frame_type == "heartbeat":
                hb = WSHeartbeatMessage(**data)
                await repository.update_node_heartbeat(node_id)
                # Refresh Redis session fencing TTL
                await streams.set_node_session_fencing(node_id, session_id, epoch)
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
                # Atomically update status only if this session is the active session
                updated = await repository.update_job_status_fenced(
                    node_id=node_id,
                    session_id=session_id,
                    job_id=job_id,
                    status="AGENT_ACCEPTED",
                    expected_epoch=epoch,
                    accepted_at=datetime.now(UTC),
                    lease_expires_at=lease_exp,
                )
                if not updated:
                    # Check if failure was caused by session superseding
                    if not await repository.verify_node_session(node_id, session_id, epoch):
                        await websocket.close(code=4001, reason="Session superseded")
                        break
                    continue

                # Renew lease score in ZSET
                await streams.renew_node_concurrency(node_id, job_id, lease_exp.timestamp())
                # Acknowledge and delete entry from Redis Stream
                entry_id = dispatched_map.pop(acc.job_id, None)
                if entry_id:
                    await streams.ack_and_delete_job_dispatch(node_id, entry_id)

            elif frame_type == "job_chunk":
                chunk = WSJobChunkMessage(**data)
                job_id = UUID(chunk.job_id)
                lease_exp = datetime.now(UTC) + timedelta(
                    seconds=settings.COMMUNITY_JOB_LEASE_SECONDS
                )
                updated = await repository.renew_job_lease_fenced(
                    node_id=node_id,
                    session_id=session_id,
                    job_id=job_id,
                    lease_expires_at=lease_exp,
                    expected_epoch=epoch,
                )
                if not updated:
                    if not await repository.verify_node_session(node_id, session_id, epoch):
                        await websocket.close(code=4001, reason="Session superseded")
                        break
                    continue

                # Renew lease in ZSET
                await streams.renew_node_concurrency(node_id, job_id, lease_exp.timestamp())
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

                updated = await repository.update_job_status_fenced(
                    node_id=node_id,
                    session_id=session_id,
                    job_id=job_id,
                    status="COMPLETED",
                    expected_epoch=epoch,
                    completed_at=now,
                    reported_input_tokens=comp.reported_input_tokens,
                    reported_output_tokens=comp.reported_output_tokens,
                    duration_ms=comp.duration_ms,
                    tokens_per_second=comp.tokens_per_second,
                )
                # Release concurrency slot
                await streams.release_node_concurrency(node_id, job_id)

                if not updated:
                    if not await repository.verify_node_session(node_id, session_id, epoch):
                        await websocket.close(code=4001, reason="Session superseded")
                        break
                    continue

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
=======
    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
>>>>>>> 32cabfcfacede0a2b543f06e7083707ae399a44d
