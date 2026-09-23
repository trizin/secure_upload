"""Content-type detection from magic bytes.

The client's declared Content-Type and the file extension are never trusted for
anything but display. What gets stored and served is decided here, from the
bytes themselves.

Anything not on the allowlist is rejected at upload, which is what lets the UI
honestly claim every served file has been through a real sanitizer.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

# kind -> (mime, extension, inline-safe)
# "inline-safe" means we are willing to let a browser render it directly.
# Everything else is served as application/octet-stream.
KINDS: dict[str, tuple[str, str, bool]] = {
    "jpeg": ("image/jpeg", "jpg", True),
    "png": ("image/png", "png", True),
    "gif": ("image/gif", "gif", True),
    "webp": ("image/webp", "webp", True),
    "pdf": ("application/pdf", "pdf", False),
    "mp4": ("video/mp4", "mp4", True),
    "mov": ("video/quicktime", "mov", False),
    "webm": ("video/webm", "webm", True),
    "mp3": ("audio/mpeg", "mp3", True),
    "m4a": ("audio/mp4", "m4a", True),
    "wav": ("audio/wav", "wav", True),
    "flac": ("audio/flac", "flac", True),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx", False),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx", False),
    "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "pptx", False),
    "text": ("text/plain", "txt", False),
}

_MP4_BRANDS = {
    b"isom", b"iso2", b"iso4", b"iso5", b"iso6", b"mp41", b"mp42",
    b"avc1", b"dash", b"mmp4", b"M4V ", b"M4VP",
}

_MARKUP_MARKERS = (b"<svg", b"<html", b"<!doctype html", b"<?xml", b"<script")


class Rejected(Exception):
    """Upload refused. The message is shown to the user verbatim."""


def _sniff_ftyp(head: bytes) -> str | None:
    if len(head) < 12 or head[4:8] != b"ftyp":
        return None
    brand = head[8:12]
    if brand == b"M4A ":
        return "m4a"
    if brand == b"qt  ":
        return "mov"
    if brand in _MP4_BRANDS:
        return "mp4"
    return None


def _sniff_riff(head: bytes) -> str | None:
    if len(head) < 12 or head[:4] != b"RIFF":
        return None
    if head[8:12] == b"WEBP":
        return "webp"
    if head[8:12] == b"WAVE":
        return "wav"
    return None


def _sniff_zip(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
    except (zipfile.BadZipFile, OSError) as exc:
        raise Rejected("That file looks like a ZIP but could not be read.") from exc
    if "word/document.xml" in names:
        return "docx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    if "ppt/presentation.xml" in names:
        return "pptx"
    raise Rejected(
        "Archives are not accepted. There is no way to strip metadata from "
        "arbitrary archive contents, so they are not on the allowlist."
    )


def _sniff_text(path: Path, head: bytes) -> str:
    lowered = head[:1024].lstrip().lower()
    for marker in _MARKUP_MARKERS:
        if lowered.startswith(marker):
            raise Rejected(
                "HTML and SVG are not accepted: both can execute JavaScript "
                "when opened, which makes them unsafe to host."
            )
    if b"\x00" in head:
        raise Rejected("Unrecognised binary format. Only known file types are accepted.")
    try:
        path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Rejected("Unrecognised file format. Only known file types are accepted.") from exc
    return "text"


def detect(path: Path) -> str:
    """Return the canonical kind for a file, or raise Rejected."""
    with open(path, "rb") as f:
        head = f.read(4096)

    if not head:
        raise Rejected("That file is empty.")

    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "webm"
    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"ID3") or (head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mp3"

    riff = _sniff_riff(head)
    if riff:
        return riff

    ftyp = _sniff_ftyp(head)
    if ftyp:
        return ftyp

    if head.startswith(b"PK\x03\x04"):
        return _sniff_zip(path)

    return _sniff_text(path, head)


def mime_for(kind: str) -> str:
    return KINDS[kind][0]


def extension_for(kind: str) -> str:
    return KINDS[kind][1]


def is_inline_safe(kind: str) -> bool:
    return KINDS[kind][2]
