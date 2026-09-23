"""Sanitizer dispatch and the sandbox that runs it.

Every sanitizer runs in a child process with an address-space cap, a CPU cap
and a wall-clock timeout, in its own session so a timeout can kill the whole
process group rather than leaving an orphaned ffmpeg behind.
"""

from __future__ import annotations

import asyncio
import os
import resource
import signal
import subprocess
import sys
from pathlib import Path

from .. import config
from . import media

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_ALWAYS = {"jpeg", "png", "gif", "webp", "pdf", "docx", "xlsx", "pptx", "text"}
_NEEDS_FFMPEG = {"mp4", "mov", "webm", "mp3", "m4a", "wav", "flac"}


class SanitizeError(Exception):
    """The sanitizer failed. Treated as a server-side problem."""


class SanitizeRejected(Exception):
    """The file is not acceptable. The message is shown to the user."""


def supported_kinds() -> set[str]:
    """The live allowlist. Media types drop off if ffmpeg is not installed,
    so nothing is ever served through a sanitizer that is not present."""
    kinds = set(_ALWAYS)
    if media.available():
        kinds |= _NEEDS_FFMPEG
    return kinds


def _apply_limits() -> None:  # runs in the child, between fork and exec
    resource.setrlimit(resource.RLIMIT_AS, (config.SANITIZE_MEM_LIMIT,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (config.SANITIZE_TIMEOUT,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    os.setsid()


def run(kind: str, src: Path, dst: Path) -> None:
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/nonexistent",
        "LANG": "C.UTF-8",
        "PYTHONPATH": str(PROJECT_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "SUPLOAD_DATA": str(config.DATA_DIR),
        "SUPLOAD_MAX_IMAGE_PIXELS": str(config.MAX_IMAGE_PIXELS),
        "SUPLOAD_SANITIZE_TIMEOUT": str(config.SANITIZE_TIMEOUT),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.sanitize.worker", kind, str(src), str(dst)],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        preexec_fn=_apply_limits,
    )
    try:
        _, err = proc.communicate(timeout=config.SANITIZE_TIMEOUT + 10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        raise SanitizeError("Sanitizing that file took too long.") from None

    message = err.decode("utf-8", "replace").strip()
    if proc.returncode == 3:
        raise SanitizeRejected(message or "That file was rejected.")
    if proc.returncode != 0:
        raise SanitizeError(message or f"Sanitizer exited {proc.returncode}.")


_semaphore = asyncio.Semaphore(config.SANITIZE_CONCURRENCY)


async def run_async(kind: str, src: Path, dst: Path) -> None:
    """Serialised: on a 1GB box, two concurrent strips are an OOM kill."""
    async with _semaphore:
        await asyncio.to_thread(run, kind, src, dst)
