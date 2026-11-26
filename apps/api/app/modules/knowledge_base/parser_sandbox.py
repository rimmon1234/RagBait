"""Stage 3 container-level parser isolation runner using hardened Docker boundaries.

Enforces genuine OS/container isolation around untrusted PDF/TXT parsing:
- --network=none (zero network egress/ingress)
- --read-only (immutable rootfs) with bounded noexec/nosuid/nodev /tmp tmpfs
- --cap-drop=ALL & --security-opt=no-new-privileges:true
- --user 65532:65532 (unprivileged non-root execution)
- --pids-limit, --memory, --memory-swap, and --cpus resource bounds
- Single-file read-only bind mount for input + dedicated output dir mount
- Deterministic container cleanup (`docker rm -f`) on timeout or cancellation
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from app.modules.knowledge_base.extractor_exceptions import (
    CorruptedFileError,
    ExtractionTimeoutError,
    PermanentExtractionError,
    TransientExtractionError,
)

logger = logging.getLogger("ragbait.kb.parser_sandbox")

ParserIsolationMode = Literal[
    "container_required",
    "container_preferred",
    "subprocess_legacy",
]
ParserEngine = Literal["pypdf", "pymupdf"]


@dataclass(frozen=True)
class ContainerSandboxConfig:
    """Resource and security settings for ephemeral parser containers."""

    image: str = "ragbait-parser-sandbox:latest"
    memory_limit: str = "512m"
    cpus_limit: str = "1.0"
    pids_limit: int = 32
    tmpfs_size: str = "64m"
    uid_gid: str = "65532:65532"


def is_docker_available() -> bool:
    """Return True if the `docker` CLI binary is present in PATH."""
    return shutil.which("docker") is not None


async def is_sandbox_image_present(image: str) -> bool:
    """Check whether the parser sandbox Docker image exists locally."""
    if not is_docker_available():
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "image",
            "inspect",
            image,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        rc = await asyncio.wait_for(proc.wait(), timeout=5.0)
        return rc == 0
    except Exception:
        return False


def build_docker_sandbox_command(
    *,
    container_name: str,
    input_path: Path,
    output_dir: Path,
    mime_type: str,
    max_pages: int,
    max_bytes: int,
    parser_engine: ParserEngine = "pypdf",
    config: ContainerSandboxConfig | None = None,
    enable_conditional_ocr: bool = False,
    enable_render_dir: bool = False,
) -> list[str]:
    """Construct strict `docker run` arguments enforcing container isolation."""
    cfg = config or ContainerSandboxConfig()
    ext = input_path.suffix or (".pdf" if mime_type == "application/pdf" else ".txt")
    container_input = f"/sandbox/in/source{ext}"
    container_output = "/sandbox/out/parser_output.json"

    cmd = [
        "docker",
        "run",
        "--rm",
        "--init",
        "--name",
        container_name,
        "--network=none",
        "--read-only",
        "--tmpfs",
        f"/tmp:rw,noexec,nosuid,nodev,size={cfg.tmpfs_size}",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--user",
        cfg.uid_gid,
        "--pids-limit",
        str(cfg.pids_limit),
        "--memory",
        cfg.memory_limit,
        "--memory-swap",
        cfg.memory_limit,
        "--cpus",
        cfg.cpus_limit,
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
        "-e",
        "PYTHONUNBUFFERED=1",
        "--mount",
        f"type=bind,src={str(input_path.resolve())},dst={container_input},readonly",
        "--mount",
        f"type=bind,src={str(output_dir.resolve())},dst=/sandbox/out",
        cfg.image,
        "--input",
        container_input,
        "--mime",
        mime_type,
        "--output",
        container_output,
        "--max-pages",
        str(max_pages),
        "--max-bytes",
        str(max_bytes),
        "--parser-engine",
        parser_engine,
    ]
    if enable_conditional_ocr:
        cmd.append("--enable-conditional-ocr")
    if enable_render_dir:
        cmd.extend(["--render-dir", "/sandbox/out/renders"])
    return cmd


async def _force_remove_container(container_name: str) -> None:
    """Best-effort forced removal of a timed-out or cancelled parser container."""
    try:
        rm_proc = await asyncio.create_subprocess_exec(
            "docker",
            "rm",
            "-f",
            container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(rm_proc.wait(), timeout=5.0)
    except Exception:
        pass


async def run_parser_in_container(
    *,
    input_path: Path,
    mime_type: str,
    timeout_seconds: float = 120.0,
    max_pages: int = 2000,
    max_bytes: int = 26_214_400,
    output_file: Path | None = None,
    active_procs: set[asyncio.subprocess.Process] | None = None,
    parser_engine: ParserEngine = "pypdf",
    config: ContainerSandboxConfig | None = None,
    error_code_map: dict[str, type[Exception]] | None = None,
    enable_conditional_ocr: bool = False,
    render_output_dir: Path | None = None,
) -> dict[str, Any]:
    """Execute document parsing inside an ephemeral hardened Docker container."""
    if not is_docker_available():
        raise TransientExtractionError(
            "Docker CLI unavailable for container parser sandbox.",
            code="SANDBOX_UNAVAILABLE",
        )

    sandbox_dir = Path(tempfile.mkdtemp(prefix="ragbait_sandbox_out_"))
    try:
        try:
            # Output mount must allow container UID 65532 to write parser_output.json & renders/
            os.chmod(sandbox_dir, 0o777)
        except OSError:
            pass

        container_name = f"ragbait-parser-{uuid4().hex[:12]}"
        cmd = build_docker_sandbox_command(
            container_name=container_name,
            input_path=input_path,
            output_dir=sandbox_dir,
            mime_type=mime_type,
            max_pages=max_pages,
            max_bytes=max_bytes,
            parser_engine=parser_engine,
            config=config,
            enable_conditional_ocr=enable_conditional_ocr,
            enable_render_dir=render_output_dir is not None,
        )

        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            if active_procs is not None:
                active_procs.add(proc)

            try:
                _, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_seconds
                )
            except TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await _force_remove_container(container_name)
                await proc.wait()
                raise ExtractionTimeoutError(
                    f"Sandboxed container parser exceeded {timeout_seconds} seconds."
                ) from None

            host_output = sandbox_dir / "parser_output.json"
            if not host_output.exists() or host_output.stat().st_size == 0:
                stderr_msg = stderr_bytes.decode(errors="ignore").strip()
                if "Unable to find image" in stderr_msg or "No such image" in stderr_msg:
                    raise TransientExtractionError(
                        f"Parser sandbox image missing: {stderr_msg}",
                        code="SANDBOX_UNAVAILABLE",
                    )
                raise CorruptedFileError(
                    f"Container parser exited abnormally (code {proc.returncode}): {stderr_msg}"
                )

            # Cap JSON read to guard against malformed huge outputs
            max_json_bytes = (max_bytes * 3) + 2_097_152
            if host_output.stat().st_size > max_json_bytes:
                raise PermanentExtractionError(
                    "Sandboxed parser JSON output exceeded maximum safe size.",
                    code="PAYLOAD_TOO_LARGE",
                )

            raw_json = host_output.read_text(encoding="utf-8")
            if output_file is not None:
                output_file.write_text(raw_json, encoding="utf-8")

            data = json.loads(raw_json)
            if data.get("status") == "success":
                result = dict(data["result"])
                result["parser_backend"] = "container_sandbox"
                if render_output_dir is not None:
                    renders_src = sandbox_dir / "renders"
                    if renders_src.exists():
                        render_output_dir.mkdir(parents=True, exist_ok=True)
                        for png in renders_src.glob("*.png"):
                            shutil.copy2(png, render_output_dir / png.name)
                return result

            err_code = str(data.get("error_code", "EXTRACTION_FAILED"))
            err_msg = str(data.get("error_message", "Document extraction failed."))
            is_permanent = bool(data.get("is_permanent", True))

            if error_code_map and err_code in error_code_map:
                raise error_code_map[err_code](err_msg)
            if is_permanent:
                raise PermanentExtractionError(err_msg, code=err_code)
            raise TransientExtractionError(err_msg, code=err_code)
        finally:
            if proc is not None and active_procs is not None:
                active_procs.discard(proc)
    finally:
        shutil.rmtree(sandbox_dir, ignore_errors=True)
