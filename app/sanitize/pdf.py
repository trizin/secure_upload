"""PDF metadata removal via pikepdf.

Covers all three places a PDF hides information about its author: the document
info dictionary, the XMP metadata stream hanging off the catalog, and per-object
/Metadata and /PieceInfo entries that editors leave behind on individual pages.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf


def sanitize(kind: str, src: Path, dst: Path) -> None:
    with pikepdf.open(src) as pdf:
        pdf.trailer.get("/Info")
        if "/Info" in pdf.trailer:
            del pdf.trailer["/Info"]

        root = pdf.Root
        for key in ("/Metadata", "/PieceInfo"):
            if key in root:
                del root[key]

        for page in pdf.pages:
            for key in ("/Metadata", "/PieceInfo"):
                if key in page.obj:
                    del page.obj[key]

        pdf.save(
            dst,
            linearize=False,
            compress_streams=True,
            deterministic_id=True,
        )
