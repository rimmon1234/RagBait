"""Durable background worker for deterministic Knowledge Base document extraction."""

import argparse
import asyncio
import hashlib
import logging
import signal
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]

from app.core.errors import NotFoundError
from app.modules.knowledge_base.extractor_exceptions import (
    ChecksumMismatchError,
    CorruptFileError,
    FileSizeMismatchError,
    PermanentExtractionError,
    StorageStreamError,
    TransientExtractionError,
)
from app.modules.knowledge_base.job_repository import (
    claim_next_job,
    complete_job_success,
    fail_job_permanent,
    fail_job_transient,
    renew_job_lease,
)
from app.modules.knowledge_base.parser_process import run_parser_isolated
from app.modules.knowledge_base.storage import open_storage_stream
from app.modules.knowledge_base.worker_config import WorkerSettings, worker_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
)
logger = logging.getLogger("ragbait.kb.worker")


class KnowledgeBaseExtractionWorker:
    """Production background worker for durable document extraction."""

    def __init__(
        self,
        settings: WorkerSettings | None = None,
        once: bool = False,
    ) -> None:
        self.settings = settings or worker_settings
        self.worker_id = self.settings.WORKER_ID
        self.once = once
        self.pool: asyncpg.Pool | None = None
        self.is_running = False
        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        """Initialize database pool and worker state."""
        dsn = self.settings.get_database_url()
        logger.info(
            f"Starting KB extraction worker '{self.worker_id}' (lease_ttl={self.settings.JOB_LEASE_TTL_SECONDS}s, "
            f"heartbeat={self.settings.HEARTBEAT_INTERVAL_SECONDS}s)"
        )
        self.pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=1,
            max_size=self.settings.CONCURRENCY + 2,
            command_timeout=30.0,
        )
        self.is_running = True

    async def stop(self) -> None:
        """Signal shutdown and close resources cleanly."""
        logger.info(f"Stopping KB extraction worker '{self.worker_id}'...")
        self.is_running = False
        self._shutdown_event.set()
        if self.pool is not None:
            await self.pool.close()
            self.pool = None
        logger.info(f"Worker '{self.worker_id}' shutdown complete.")

    async def run(self) -> None:
        """Main worker execution loop."""
        await self.start()
        self._setup_signals()

        try:
            while self.is_running:
                claimed = await self.process_next_job()
                if not claimed:
                    if self.once:
                        logger.info("No more eligible jobs found in '--once' mode. Exiting.")
                        break
                    try:
                        await asyncio.wait_for(
                            self._shutdown_event.wait(),
                            timeout=self.settings.POLL_INTERVAL_SECONDS,
                        )
                    except TimeoutError:
                        pass
                elif self.once:
                    logger.info("Processed job in '--once' mode. Exiting.")
                    break
        except asyncio.CancelledError:
            logger.info("Worker run task cancelled.")
        finally:
            await self.stop()

    def _setup_signals(self) -> None:
        """Register cross-platform termination signals."""

        def _handle_signal(signum: int, frame: Any) -> None:
            logger.info(f"Received termination signal ({signum}). Requesting worker shutdown...")
            self.is_running = False
            self._shutdown_event.set()

        try:
            signal.signal(signal.SIGINT, _handle_signal)
            signal.signal(signal.SIGTERM, _handle_signal)
        except (ValueError, AttributeError):
            pass

    async def process_next_job(self) -> bool:
        """Claim and process one extraction job from the durable queue."""
        if not self.pool:
            raise RuntimeError("Worker pool is not initialized.")

        async with self.pool.acquire() as conn:
            job = await claim_next_job(
                conn=conn,
                worker_id=self.worker_id,
                lease_ttl_seconds=self.settings.JOB_LEASE_TTL_SECONDS,
            )

        if not job:
            return False

        await self._execute_job(job)
        return True

    async def _heartbeat_loop(
        self,
        job_id: UUID,
        lease_token: UUID,
        stop_event: asyncio.Event,
    ) -> None:
        """Periodically extend the job lease until extraction finishes or lease is lost."""
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self.settings.HEARTBEAT_INTERVAL_SECONDS,
                )
                break
            except TimeoutError:
                if not self.pool or not self.is_running:
                    break
                try:
                    async with self.pool.acquire() as conn:
                        renewed = await renew_job_lease(
                            conn=conn,
                            job_id=job_id,
                            worker_id=self.worker_id,
                            lease_token=lease_token,
                            lease_ttl_seconds=self.settings.JOB_LEASE_TTL_SECONDS,
                        )
                        if not renewed:
                            logger.warning(
                                f"Worker '{self.worker_id}' lost lease on job {job_id} or document was deleted."
                            )
                            break
                except Exception as e:
                    logger.warning(f"Error during lease renewal for job {job_id}: {e}")

    async def _execute_job(self, job: dict[str, Any]) -> None:
        """Execute extraction workflow for a claimed job."""
        if not self.pool:
            raise RuntimeError("Worker pool is not initialized.")

        job_id: UUID = job["job_id"]
        document_id: UUID = job["document_id"]
        lease_token: UUID = job["lease_token"]
        storage_path: str = job["storage_path"]
        mime_type: str = job["mime_type"]
        expected_size: int = job["file_size_bytes"]
        expected_sha256: str | None = job["sha256_checksum"]

        logger.info(
            f"Worker '{self.worker_id}' processing job {job_id} for doc {document_id} "
            f"(attempt {job['attempts']}/{job['max_attempts']}, mime={mime_type})"
        )

        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(job_id, lease_token, heartbeat_stop)
        )

        # Temporary file for bounded streaming download
        suffix = ".pdf" if mime_type == "application/pdf" else ".txt"
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=suffix, delete=False, prefix="kb_worker_doc_"
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)

        try:
            # 1. Stream download with bounded memory and incremental SHA-256 verification
            hasher = hashlib.sha256()
            total_bytes = 0

            try:
                async with open_storage_stream(
                    path=storage_path, chunk_size=self.settings.DOWNLOAD_CHUNK_SIZE
                ) as stream:
                    with open(tmp_path, "wb") as f:
                        async for chunk in stream:
                            total_bytes += len(chunk)
                            hasher.update(chunk)
                            f.write(chunk)
            except NotFoundError as e:
                raise CorruptFileError(f"Storage object not found: {e.message}") from e
            except Exception as e:
                raise StorageStreamError(f"Failed to stream object from storage: {e}") from e

            # 2. Verify file size and checksum
            actual_sha256 = hasher.hexdigest()
            if expected_sha256 and actual_sha256.lower() != expected_sha256.lower():
                raise ChecksumMismatchError(
                    f"SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}"
                )
            if total_bytes != expected_size:
                raise FileSizeMismatchError(
                    f"File size mismatch: expected {expected_size} bytes, got {total_bytes} bytes"
                )

            # 3. Execute extraction inside isolated process with hard timeout
            result = await run_parser_isolated(
                input_path=tmp_path,
                mime_type=mime_type,
                timeout_seconds=float(self.settings.MAX_EXTRACTION_DURATION_SECONDS),
                max_pages=self.settings.MAX_PDF_PAGES,
                max_bytes=self.settings.MAX_EXTRACTED_TEXT_BYTES,
            )

            # 4. Atomic completion & page persistence
            async with self.pool.acquire() as conn:
                completed = await complete_job_success(
                    conn=conn,
                    job_id=job_id,
                    document_id=document_id,
                    worker_id=self.worker_id,
                    lease_token=lease_token,
                    pages=result["pages"],
                    has_any_text=result["has_any_text"],
                )

            if completed:
                logger.info(
                    f"Job {job_id} successfully completed ({result['total_pages']} pages, "
                    f"{result['pages_with_text']} with text, has_text={result['has_any_text']})"
                )
            else:
                logger.warning(
                    f"Job {job_id} could not be completed (lease expired or document deleted)."
                )

        except PermanentExtractionError as e:
            logger.error(
                f"Permanent extraction failure for job {job_id} (doc {document_id}): [{e.code}] {e.message}"
            )
            async with self.pool.acquire() as conn:
                await fail_job_permanent(
                    conn=conn,
                    job_id=job_id,
                    document_id=document_id,
                    worker_id=self.worker_id,
                    lease_token=lease_token,
                    error_code=e.code,
                    error_message=e.message,
                )

        except (TimeoutError, TransientExtractionError) as e:
            err_code = getattr(e, "code", "PARSER_TIMEOUT")
            err_msg = getattr(e, "message", str(e))
            logger.warning(
                f"Transient extraction failure for job {job_id} (doc {document_id}): [{err_code}] {err_msg}"
            )
            async with self.pool.acquire() as conn:
                await fail_job_transient(
                    conn=conn,
                    job_id=job_id,
                    document_id=document_id,
                    worker_id=self.worker_id,
                    lease_token=lease_token,
                    error_code=err_code,
                    error_message=err_msg,
                    backoff_base_seconds=self.settings.BACKOFF_BASE_SECONDS,
                    backoff_max_seconds=self.settings.BACKOFF_MAX_SECONDS,
                )

        except Exception as e:
            logger.exception(
                f"Unexpected extraction error for job {job_id} (doc {document_id}): {e}"
            )
            async with self.pool.acquire() as conn:
                await fail_job_transient(
                    conn=conn,
                    job_id=job_id,
                    document_id=document_id,
                    worker_id=self.worker_id,
                    lease_token=lease_token,
                    error_code="EXTRACTION_CRASH",
                    error_message=str(e),
                    backoff_base_seconds=self.settings.BACKOFF_BASE_SECONDS,
                    backoff_max_seconds=self.settings.BACKOFF_MAX_SECONDS,
                )

        finally:
            # Cancel heartbeat loop
            heartbeat_stop.set()
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

            # Clean temporary file
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass


def main() -> None:
    """CLI entry point for background document extraction worker."""
    parser = argparse.ArgumentParser(
        description="RagBait Durable Knowledge Base Document Extraction Worker"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Poll once, process available jobs, and exit.",
    )
    parser.add_argument(
        "--worker-id",
        type=str,
        default=None,
        help="Override worker instance identifier.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Override worker concurrency.",
    )

    args = parser.parse_args()

    settings_dict = {}
    if args.worker_id:
        settings_dict["WORKER_ID"] = args.worker_id
    if args.concurrency:
        settings_dict["CONCURRENCY"] = args.concurrency

    custom_settings = WorkerSettings(**settings_dict) if settings_dict else None
    worker = KnowledgeBaseExtractionWorker(settings=custom_settings, once=args.once)

    try:
        asyncio.run(worker.run())
    except KeyboardInterrupt:
        logger.info("Worker stopped by KeyboardInterrupt.")
        sys.exit(0)


if __name__ == "__main__":
    main()
