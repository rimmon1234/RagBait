"""Outbound WebSocket client managing communication with RagBait server."""

import asyncio
import json
import logging
import time
from typing import Any

import websockets

from ragbait_node import crypto, ollama
from ragbait_node.config import NodeConfig
from ragbait_node.protocol import (
    WSAuthMessage,
    WSAuthSuccessMessage,
    WSChallengeMessage,
    WSErrorMessage,
    WSHeartbeatAckMessage,
    WSHeartbeatMessage,
    WSJobAcceptedMessage,
    WSJobCancelledAckMessage,
    WSJobCancelMessage,
    WSJobChunkMessage,
    WSJobCompletedMessage,
    WSJobDispatchMessage,
)
from ragbait_node.storage import StateStorage

logger = logging.getLogger("ragbait.node.client")


class NodeClient:
    """Agent runtime managing outbound connection, heartbeat, and job execution."""

    def __init__(self, config: NodeConfig):
        self.config = config
        self.storage = StateStorage(config.state_db)
        self.private_key = crypto.get_or_create_keypair(config.key_file)
        self.active_jobs: dict[str, asyncio.Task[None]] = {}
        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        """Start agent client with automatic exponential reconnect loop."""
        backoff = 2.0
        max_backoff = 30.0

        logger.info(f"Starting RagBait Node Agent [Node ID: {self.config.node_id}]")
        logger.info(f"Target Server: {self.config.ws_url}")

        # Check local Ollama
        alive, version = await ollama.check_ollama_alive()
        if not alive:
            logger.warning(
                f"⚠️  Local Ollama daemon at http://127.0.0.1:11434 is unreachable ({version}). "
                "The agent will connect, but will report 0 models until Ollama is started."
            )
        else:
            logger.info(f"✓ Local Ollama daemon active (version: {version})")

        while not self._shutdown_event.is_set():
            try:
                await self._connect_and_run()
                backoff = 2.0
            except (websockets.exceptions.ConnectionClosed, OSError) as e:
                if self._shutdown_event.is_set():
                    break
                logger.warning(f"Connection dropped ({e}). Reconnecting in {backoff:.1f}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, max_backoff)
            except Exception as e:
                if self._shutdown_event.is_set():
                    break
                logger.error(f"Unexpected client error: {e}. Reconnecting in {backoff:.1f}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, max_backoff)

        logger.info("Agent stopped.")

    def stop(self) -> None:
        """Signal graceful shutdown."""
        self._shutdown_event.set()
        for _job_id, task in list(self.active_jobs.items()):
            task.cancel()

    async def _connect_and_run(self) -> None:
        """Single authenticated connection session."""
        headers = {
            "X-Node-ID": self.config.node_id,
            "X-Protocol-Version": "1",
        }

        async with websockets.connect(
            self.config.ws_url,
            additional_headers=headers,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            logger.info("Connected to RagBait server. Initiating cryptographic handshake...")

            # 1. Receive challenge
            raw_challenge = await ws.recv()
            challenge_data = json.loads(raw_challenge)

            if challenge_data.get("type") == "error":
                err = WSErrorMessage(**challenge_data)
                logger.error(f"Server rejected connection: [{err.code}] {err.message}")
                return

            challenge = WSChallengeMessage(**challenge_data)

            # 2. Sign challenge canonical string
            now_ts = int(time.time())
            signature = crypto.sign_challenge(
                private_key=self.private_key,
                node_id=self.config.node_id,
                nonce=challenge.nonce,
                session_id=challenge.session_id,
                timestamp=now_ts,
            )

            # 3. Send Auth message
            auth_msg = WSAuthMessage(
                node_id=self.config.node_id,
                signature=signature,
                timestamp=now_ts,
            )
            await ws.send(auth_msg.model_dump_json())

            # 4. Wait for Auth success
            raw_auth_res = await ws.recv()
            auth_res_data = json.loads(raw_auth_res)

            if auth_res_data.get("type") == "error":
                err = WSErrorMessage(**auth_res_data)
                logger.error(f"Handshake failed: [{err.code}] {err.message}")
                return

            auth_success = WSAuthSuccessMessage(**auth_res_data)
            logger.info(
                f"✓ Authenticated successfully with RagBait! Session: {auth_success.session_id}"
            )

            # 5. Run Heartbeat & Message handler concurrently
            heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(ws, auth_success.heartbeat_interval_sec)
            )
            receive_task = asyncio.create_task(self._receive_loop(ws))

            try:
                _done, pending = await asyncio.wait(
                    [heartbeat_task, receive_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
            finally:
                heartbeat_task.cancel()
                receive_task.cancel()

    async def _heartbeat_loop(self, ws: websockets.WebSocketClientProtocol, interval: int) -> None:
        """Periodically report node health, local Ollama models, and active jobs."""
        while not self._shutdown_event.is_set():
            try:
                models = await ollama.list_ollama_models()
                hb = WSHeartbeatMessage(
                    installed_models=models,
                    active_jobs=len(self.active_jobs),
                )
                await ws.send(hb.model_dump_json())
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Heartbeat send error: {e}")
                break

    async def _receive_loop(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Handle incoming protocol frames from RagBait server."""
        while not self._shutdown_event.is_set():
            try:
                raw = await ws.recv()
                data = json.loads(raw)
                frame_type = data.get("type")

                if frame_type == "heartbeat_ack":
                    _ = WSHeartbeatAckMessage(**data)

                elif frame_type == "job_dispatch":
                    msg = WSJobDispatchMessage(**data)
                    asyncio.create_task(self._handle_job_dispatch(ws, msg))

                elif frame_type == "job_cancel":
                    cancel_msg = WSJobCancelMessage(**data)
                    self._handle_job_cancel(ws, cancel_msg.job_id)

                elif frame_type == "error":
                    err = WSErrorMessage(**data)
                    logger.warning(f"Server notification: [{err.code}] {err.message}")

            except (websockets.exceptions.ConnectionClosed, asyncio.CancelledError):
                break
            except Exception as e:
                logger.warning(f"Error in receive loop: {e}")

    async def _handle_job_dispatch(
        self, ws: websockets.WebSocketClientProtocol, msg: WSJobDispatchMessage
    ) -> None:
        """Validate server signature, deduplicate, and execute authorized inference job."""
        job_id = msg.job_id
        payload = msg.payload
        model_name = payload.get("model", "")
        messages = payload.get("messages", [])
        options = payload.get("options", {})

        # 1. Deduplication check in local SQLite
        if self.storage.has_job(job_id):
            logger.info(f"Ignoring duplicate job dispatch for {job_id}")
            return

        # 2. Cryptographic signature verification
        is_valid = crypto.verify_server_job_authorization(
            auth=msg.authorization,
            payload=payload,
            server_public_key_hex=self.config.server_public_key,
            expected_node_id=self.config.node_id,
        )

        if not is_valid:
            logger.warning(
                f"🚨 REJECTED job {job_id}: Invalid server authorization signature or expired deadline!"
            )
            return

        logger.info(f"Executing authorized job {job_id} on model '{model_name}'...")

        # 3. Record acceptance in local state & notify server
        self.storage.record_job(job_id, model_name, "accepted")
        await ws.send(WSJobAcceptedMessage(job_id=job_id).model_dump_json())

        # 4. Stream tokens from local Ollama
        task = asyncio.create_task(
            self._run_inference_stream(ws, job_id, model_name, messages, options)
        )
        self.active_jobs[job_id] = task
        try:
            await task
        finally:
            self.active_jobs.pop(job_id, None)

    async def _run_inference_stream(
        self,
        ws: websockets.WebSocketClientProtocol,
        job_id: str,
        model_name: str,
        messages: list[dict[str, Any]],
        options: dict[str, Any],
    ) -> None:
        """Stream chunks from Ollama to WebSocket."""
        seq_id = 1
        final_stats: dict[str, Any] = {}
        try:
            async for delta, done, stats in ollama.stream_chat_completion(
                model=model_name,
                messages=messages,
                options=options,
            ):
                if delta:
                    chunk = WSJobChunkMessage(
                        job_id=job_id,
                        seq_id=seq_id,
                        delta=delta,
                        done=False,
                    )
                    await ws.send(chunk.model_dump_json())
                    seq_id += 1

                if done and stats:
                    final_stats = stats

            # Send job completed frame
            comp = WSJobCompletedMessage(
                job_id=job_id,
                reported_input_tokens=final_stats.get("input_tokens", 0),
                reported_output_tokens=final_stats.get("output_tokens", 0),
                duration_ms=final_stats.get("duration_ms", 0),
                tokens_per_second=final_stats.get("tokens_per_second", 0.0),
            )
            await ws.send(comp.model_dump_json())
            self.storage.record_job(job_id, model_name, "completed")
            logger.info(
                f"✓ Job {job_id} completed: {final_stats.get('output_tokens', 0)} tokens "
                f"in {final_stats.get('duration_ms', 0)}ms ({final_stats.get('tokens_per_second', 0.0)} tok/s)"
            )
        except asyncio.CancelledError:
            logger.info(f"Job {job_id} cancelled.")
            self.storage.record_job(job_id, model_name, "cancelled")
            raise
        except Exception as e:
            logger.error(f"Inference failure on job {job_id}: {e}")
            self.storage.record_job(job_id, model_name, f"failed: {e}")

    def _handle_job_cancel(self, ws: websockets.WebSocketClientProtocol, job_id: str) -> None:
        """Cancel active inference task."""
        task = self.active_jobs.get(job_id)
        if task and not task.done():
            task.cancel()
            logger.info(f"Cancelled active task for job {job_id}")

        asyncio.create_task(ws.send(WSJobCancelledAckMessage(job_id=job_id).model_dump_json()))
