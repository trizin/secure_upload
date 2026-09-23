"""Sandboxed sanitizer entry point.

Always runs as a separate process under resource limits, so a malformed file
that crashes or wedges a parser takes down a throwaway child rather than the
web server. Exit codes: 0 accepted, 3 rejected (reason on stderr), 1 failed.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REJECTED = 3


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print("usage: worker <kind> <src> <dst>", file=sys.stderr)
        return EXIT_ERROR

    kind, src, dst = argv[1], Path(argv[2]), Path(argv[3])

    from . import images, media, ooxml, pdf, plain

    routes = {
        "jpeg": images.sanitize, "png": images.sanitize,
        "gif": images.sanitize, "webp": images.sanitize,
        "pdf": pdf.sanitize,
        "mp4": media.sanitize, "mov": media.sanitize, "webm": media.sanitize,
        "mp3": media.sanitize, "m4a": media.sanitize,
        "wav": media.sanitize, "flac": media.sanitize,
        "docx": ooxml.sanitize, "xlsx": ooxml.sanitize, "pptx": ooxml.sanitize,
        "text": plain.sanitize,
    }

    handler = routes.get(kind)
    if handler is None:
        print(f"No sanitizer for {kind!r}.", file=sys.stderr)
        return EXIT_REJECTED

    try:
        handler(kind, src, dst)
    except images.TooLarge as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_REJECTED
    except MemoryError:
        print("File needs more memory than the server allows.", file=sys.stderr)
        return EXIT_REJECTED
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return EXIT_ERROR

    if not dst.exists() or dst.stat().st_size == 0:
        print("Sanitizer produced no output.", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv))
