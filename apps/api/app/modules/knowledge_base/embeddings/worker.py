"""Durable background worker daemon for RAG Knowledge Base document vector embeddings."""

import argparse
import asyncio
import logging
import signal
from uuid import UUID, uuid4

import asyncpg

from app.core.config import settings
from app.modules.knowledge_base.embeddings.model_manifest import get_model_manifest
from app.modules.knowledge_base.embeddings.repository import (
    claim_next_embedding_job,
    complete_embedding_job_transaction,
    fail_embedding_job,
    fetch_chunks_for_embedding,
    persist_chunk_embeddings,
    renew_embedding_lease,
)
from app.modules.knowledge_base.embeddings.service import embed_passages

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
)
logger = logging.getLogger("ragbait.kb.embeddings.worker")


class KnowledgeBaseEmbeddingWorker:
    """Production background worker for durable document chunk vector embedding."""

    def __init__(
        self,
        worker_id: str | None = None,
        poll_interval: float = 2.0,
        lease_ttl_seconds: int = 120,
        batch_size: int = 16,
        once: bool = False,
        dsn: str | None = None,
    ) -> None:
        self.worker_id = worker_id or f"kb-embed-{uuid4().hex[:8]}"
        self.poll_interval = poll_interval
        self.lease_ttl_seconds = lease_ttl_seconds
        self.batch_size = batch_size
        self.once = once
        self.dsn = dsn or settings.DATABASE_URL
        self.pool: asyncpg.Pool | None = None
        self.is_running = False
        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        """Initialize database pool and worker state."""
        manifest = get_model_manifest()
        logger.info(
            f"Starting KB embedding worker '{self.worker_id}' "
            f"(model={manifest.model_id}, fingerprint={manifest.model_fingerprint}, "
            f"batch_size={self.batch_size}, poll={self.poll_interval}s)"
        )
        self.pool = await asyncpg.create_pool(
            dsn=self.dsn,
            min_size=1,
            max_size=5,
            command_timeout=30.0,
        )
        self.is_running = True

    async def stop(self) -> None:
        """Signal worker shutdown and close connection pools cleanly."""
        logger.info(f"Stopping KB embedding worker '{self.worker_id}'...")
        self.is_running = False
        self._shutdown_event.set()
        if self.pool is not None:
            await self.pool.close()
            self.pool = None
        logger.info(f"Embedding worker '{self.worker_id}' shutdown complete.")

    def _setup_signals(self) -> None:
        """Configure graceful shutdown signal handlers."""
        loop = asyncio.get_running_loop()

        def _handle_shutdown(sig_name: str) -> None:
            logger.info(f"Received signal {sig_name}. Initiating graceful worker shutdown...")
            self._shutdown_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _handle_shutdown, sig.name)
            except (NotImplementedError, RuntimeError):
                # Windows event loop fallback
                signal.signal(
                    sig,
                    lambda s, f: _handle_shutdown(signal.Signals(s).name),
                )

    async def step(self) -> bool:
        """Attempt to claim and process a single embedding job.

        Returns:
            True if a job was claimed and processed, False if queue was empty.
        """
        if not self.pool:
            return False

        manifest = get_model_manifest()

        async with self.pool.acquire() as conn:
            job = await claim_next_embedding_job(
                conn=conn,
                worker_id=self.worker_id,
                lease_ttl_seconds=self.lease_ttl_seconds,
            )
            if not job:
                return False

            job_id: UUID = job["job_id"]
            document_id: UUID = job["document_id"]
            chunk_version: int = job["chunk_version"]
            model_fingerprint: str = job["model_fingerprint"]
            lease_token: UUID = job["lease_token"]
            doc_title: str = job.get("title") or "Unknown"

            logger.info(
                f"Processing embedding job {job_id} for doc '{doc_title}' ({document_id}) "
                f"[v={chunk_version}, worker={self.worker_id}, attempt={job.get('attempts')}]"
            )

            try:
                # 1. Fetch chunks for this document and active version
                chunks = await fetch_chunks_for_embedding(
                    conn=conn,
                    document_id=document_id,
                    chunk_version=chunk_version,
                )
                if not chunks:
                    logger.warning(
                        f"No active chunks found for document {document_id} v={chunk_version}. Marking completed."
                    )
                    await complete_embedding_job_transaction(
                        conn=conn,
                        job_id=job_id,
                        document_id=document_id,
                        worker_id=self.worker_id,
                        lease_token=lease_token,
                        chunk_version=chunk_version,
                        model_fingerprint=model_fingerprint,
                    )
                    return True

                total_chunks = len(chunks)
                logger.info(
                    f"Generating embeddings for {total_chunks} chunks (batch_size={self.batch_size})..."
                )

                # 2. Process in bounded batches
                embedded_count = 0
                for start_idx in range(0, total_chunks, self.batch_size):
                    batch = chunks[start_idx : start_idx + self.batch_size]

                    # Heartbeat lease before CPU inference
                    await renew_embedding_lease(
                        conn=conn,
                        job_id=job_id,
                        worker_id=self.worker_id,
                        lease_token=lease_token,
                        lease_ttl_seconds=self.lease_ttl_seconds,
                    )

                    raw_texts = [c["raw_text"] for c in batch]
                    emb_results = embed_passages(raw_texts, batch_size=len(batch))

                    records: list[tuple[UUID, UUID, int, str, str, int, str, str]] = []
                    for c_meta, res in zip(batch, emb_results, strict=False):
                        # Format vector as string for asyncpg extensions.vector cast: '[0.1,0.2,...]'
                        vec_str = "[" + ",".join(f"{x:.6f}" for x in res.vector) + "]"
                        records.append(
                            (
                                c_meta["id"],
                                document_id,
                                chunk_version,
                                manifest.model_id,
                                model_fingerprint,
                                manifest.embedding_dimension,
                                vec_str,
                                c_meta["content_hash"],
                            )
                        )

                    saved = await persist_chunk_embeddings(conn, records)
                    embedded_count += saved

                # 3. Complete job transaction
                success = await complete_embedding_job_transaction(
                    conn=conn,
                    job_id=job_id,
                    document_id=document_id,
                    worker_id=self.worker_id,
                    lease_token=lease_token,
                    chunk_version=chunk_version,
                    model_fingerprint=model_fingerprint,
                )

                if success:
                    logger.info(
                        f"Successfully completed embedding job {job_id} for doc '{doc_title}' ({embedded_count}/{total_chunks} chunks)."
                    )
                else:
                    logger.warning(
                        f"Embedding job {job_id} transaction aborted (lease lost or document modified)."
                    )

                return success

            except Exception as exc:
                logger.error(
                    f"Error processing embedding job {job_id} for doc {document_id}: {exc}",
                    exc_info=True,
                )
                try:
                    await fail_embedding_job(
                        conn=conn,
                        job_id=job_id,
                        document_id=document_id,
                        worker_id=self.worker_id,
                        lease_token=lease_token,
                        error_code="EMBEDDING_FAILED",
                        error_message=str(exc),
                        is_transient=True,
                    )
                except Exception as fail_err:
                    logger.error(f"Failed to record job failure for {job_id}: {fail_err}")

                return False

    async def run(self) -> None:
        """Run the continuous worker poll loop."""
        self._setup_signals()
        await self.start()
        logger.info(f"Worker '{self.worker_id}' entered polling loop.")

        try:
            while self.is_running and not self._shutdown_event.is_set():
                did_work = await self.step()
                if self.once:
                    break

                if not did_work:
                    try:
                        await asyncio.wait_for(
                            self._shutdown_event.wait(),
                            timeout=self.poll_interval,
                        )
                    except TimeoutError:
                        pass
        finally:
            await self.stop()


def main() -> None:
    """CLI entry point for knowledge base embedding worker."""
    parser = argparse.ArgumentParser(description="Run KB Document Embedding Worker Daemon")
    parser.add_argument("--worker-id", type=str, default=None, help="Custom worker identifier")
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Polling interval in seconds when queue is empty",
    )
    parser.add_argument(
        "--lease-ttl",
        type=int,
        default=120,
        help="Worker lease TTL in seconds",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=16,
        help="Inference batch size",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process a single job and exit",
    )
    parser.add_argument("--dsn", type=str, default=None, help="PostgreSQL connection string")
    args = parser.parse_args()

    worker = KnowledgeBaseEmbeddingWorker(
        worker_id=args.worker_id,
        poll_interval=args.poll_interval,
        lease_ttl_seconds=args.lease_ttl,
        batch_size=args.batch_size,
        once=args.once,
        dsn=args.dsn,
    )
    asyncio.run(worker.run())


if __name__ == "__main__":
    main()
