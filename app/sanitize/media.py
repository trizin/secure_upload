"""Audio and video metadata removal via ffmpeg.

Uses stream copy (-c copy), so nothing is re-encoded: this is a fast container
rewrite that discards every tag and chapter. -fflags +bitexact stops ffmpeg
stamping its own muxer name into the output, and the protocol whitelist stops a
crafted input from persuading ffmpeg to open a network URL.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .. import config

_FORMATS = {
    "mp4": "mp4",
    "mov": "mov",
    "webm": "webm",
    "mp3": "mp3",
    "m4a": "ipod",
    "wav": "wav",
    "flac": "flac",
}


def available() -> bool:
    return shutil.which("ffmpeg") is not None


def sanitize(kind: str, src: Path, dst: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is not installed")

    cmd = [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-protocol_whitelist", "file",
        "-i", str(src),
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        "-c", "copy",
        "-fflags", "+bitexact",
        "-f", _FORMATS[kind],
        str(dst),
    ]
    if kind in ("mp4", "m4a", "mov"):
        cmd[-3:-3] = ["-movflags", "+faststart"]

    proc = subprocess.run(
        cmd, capture_output=True, timeout=config.SANITIZE_TIMEOUT,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(detail[-1] if detail else "ffmpeg failed")
