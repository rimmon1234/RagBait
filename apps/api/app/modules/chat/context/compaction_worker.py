"""Durable background worker daemon for conversation summary compaction and crash recovery."""

import argparse
import asyncio
import logging
import signal
from uuid import uuid4

import asyncpg

from app.core.config import settings
from app.modules.chat.context.compaction import (
    SummaryGenerator,
    claim_next_compaction_job,
    compact_conversation,
)

logger = logging.getLogger("ragbait.chat.context.compaction_worker")


class ConversationCompactionWorker:
    """Durable PostgreSQL-leased worker recovering pending and interrupted chat compactions."""

    def __init__(
        self,
        worker_id: str | None = None,
        poll_interval: float = 2.0,
        lease_ttl_seconds: int = 60,
        once: bool = False,
        respect_backoff: bool = True,
        summarizer: SummaryGenerator | None = None,
        dsn: str | None = None,
    ) -> None:
        self.worker_id = worker_id or f"chat-compact-{uuid4().hex[:8]}"
        self.poll_interval = poll_interval
        self.lease_ttl_seconds = lease_ttl_seconds
        self.once = once
        self.respect_backoff = respect_backoff
        self.summarizer = summarizer
        self.dsn = dsn or settings.DATABASE_URL
        self.pool: asyncpg.Pool | None = None
        self.is_running = False
        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        """Initialize dedicated database pool for worker execution."""
        if self.pool is None:
            self.pool = await asyncpg.create_pool(
                dsn=self.dsn,
                min_size=1,
                max_size=4,
                command_timeout=30.0,
            )
        self.is_running = True

    async def stop(self) -> None:
        """Signal shutdown and close connection pool cleanly."""
        self.is_running = False
        self._shutdown_event.set()
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    def _setup_signals(self) -> None:
        loop = asyncio.get_running_loop()

        def _handle_shutdown(sig_name: str) -> None:
            logger.info(f"Compaction worker '{self.worker_id}' stopping on {sig_name}")
            self._shutdown_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _handle_shutdown, sig.name)
            except (NotImplementedError, RuntimeError):
                signal.signal(sig, lambda s, f: _handle_shutdown(signal.Signals(s).name))

    async def step(self) -> bool:
        """Attempt to claim and execute one durable compaction job. Returns True if work was processed."""
        if not self.pool:
            return False

        async with self.pool.acquire() as conn:
            job = await claim_next_compaction_job(
                conn,
                worker_id=self.worker_id,
                lease_ttl_seconds=self.lease_ttl_seconds,
                respect_backoff=self.respect_backoff,
            )
            if job is None:
                return False

            await compact_conversation(
                job["conversation_id"],
                is_private_local=False,
                summarizer=self.summarizer,
                force=True,
                worker_id=self.worker_id,
                lease_ttl_seconds=self.lease_ttl_seconds,
                claimed_lease_token=job["lease_token"],
                conn=conn,
            )
            return True

    async def run(self) -> None:
        """Run polling compaction loop until stopped or one pass completes (`once=True`)."""
        await self.start()
        self._setup_signals()
        try:
            while not self._shutdown_event.is_set():
                did_work = await self.step()
                if self.once:
                    if not did_work:
                        break
                    continue
                if not did_work:
                    try:
                        await asyncio.wait_for(
                            self._shutdown_event.wait(), timeout=self.poll_interval
                        )
                    except TimeoutError:
                        pass
        finally:
            await self.stop()


async def _main() -> None:
    parser = argparse.ArgumentParser(description="RagBait conversation compaction worker")
    parser.add_argument("--once", action="store_true", help="Drain ready jobs once and exit")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    args = parser.parse_args()
    worker = ConversationCompactionWorker(once=args.once, poll_interval=args.poll_interval)
    await worker.run()


if __name__ == "__main__":
    asyncio.run(_main())
