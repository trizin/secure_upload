"""Plain text. Nothing to strip, but the encoding is re-validated in the
sandbox before the bytes are accepted."""

from __future__ import annotations

import shutil
from pathlib import Path


def sanitize(kind: str, src: Path, dst: Path) -> None:
    src.read_bytes().decode("utf-8")
    shutil.copyfile(src, dst)
