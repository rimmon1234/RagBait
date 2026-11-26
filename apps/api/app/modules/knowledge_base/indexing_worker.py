"""Durable background worker daemon for RAG Knowledge Base document chunking and indexing."""

import argparse
import asyncio
import logging
import signal
from uuid import uuid4

import asyncpg

from app.core.config import settings
from app.modules.knowledge_base.indexing_repository import claim_next_indexing_job
from app.modules.knowledge_base.indexing_service import process_single_indexing_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
)
logger = logging.getLogger("ragbait.kb.indexing_worker")


class KnowledgeBaseIndexingWorker:
    """Production background worker for durable document chunking and indexing."""

    def __init__(
        self,
        worker_id: str | None = None,
        poll_interval: float = 2.0,
        lease_ttl_seconds: int = 120,
        once: bool = False,
        dsn: str | None = None,
    ) -> None:
        self.worker_id = worker_id or f"kb-indexing-{uuid4().hex[:8]}"
        self.poll_interval = poll_interval
        self.lease_ttl_seconds = lease_ttl_seconds
        self.once = once
        self.dsn = dsn or settings.DATABASE_URL
        self.pool: asyncpg.Pool | None = None
        self.is_running = False
        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        """Initialize database pool and worker state."""
        logger.info(
            f"Starting KB indexing worker '{self.worker_id}' (poll_interval={self.poll_interval}s, lease_ttl={self.lease_ttl_seconds}s)"
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
        logger.info(f"Stopping KB indexing worker '{self.worker_id}'...")
        self.is_running = False
        self._shutdown_event.set()
        if self.pool is not None:
            await self.pool.close()
            self.pool = None
        logger.info(f"Worker '{self.worker_id}' shutdown complete.")

    def _setup_signals(self) -> None:
        """Configure graceful shutdown signal handlers."""
        loop = asyncio.get_running_loop()

        def _handle_shutdown(sig_name: str) -> None:
            logger.info(f"Received signal {sig_name}. Initiating graceful shutdown...")
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
        """Attempt to claim and process a single indexing job.

        Returns:
            True if a job was claimed and processed, False if queue was empty.
        """
        if not self.pool:
            return False

        async with self.pool.acquire() as conn:
            job = await claim_next_indexing_job(
                conn=conn,
                worker_id=self.worker_id,
                lease_ttl_seconds=self.lease_ttl_seconds,
            )
            if not job:
                return False

            await process_single_indexing_job(
                conn=conn,
                job=job,
                worker_id=self.worker_id,
            )
            return True

    async def run(self) -> None:
        """Main worker execution loop."""
        await self.start()
        self._setup_signals()

        try:
            while not self._shutdown_event.is_set():
                had_job = await self.step()
                if self.once:
                    break

                if not had_job:
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
    """CLI entry point for running the indexing worker."""
    parser = argparse.ArgumentParser(description="RagBait KB Document Indexing Worker")
    parser.add_argument("--worker-id", type=str, default=None, help="Unique worker identifier")
    parser.add_argument("--interval", type=float, default=2.0, help="Polling interval in seconds")
    parser.add_argument("--lease-ttl", type=int, default=120, help="Job lease TTL in seconds")
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit")
    args = parser.parse_args()

    worker = KnowledgeBaseIndexingWorker(
        worker_id=args.worker_id,
        poll_interval=args.interval,
        lease_ttl_seconds=args.lease_ttl,
        once=args.once,
    )
    asyncio.run(worker.run())


if __name__ == "__main__":
    main()
