"""Office document metadata removal.

A .docx/.xlsx/.pptx is a ZIP. The author, company, revision count and edit time
live in docProps/, so those parts are dropped and the relationships pointing at
them are removed too, which keeps the document valid.

Embedded images are stripped as well: a Word file containing a phone photo
carries that photo's GPS tags, and removing only docProps would miss it.
ZIP entry timestamps are flattened, since they leak when the file was authored.
"""

from __future__ import annotations

import re
import tempfile
import zipfile
from pathlib import Path

from .. import config
from . import images

_MEDIA_DIRS = ("word/media/", "xl/media/", "ppt/media/")
_IMAGE_KINDS = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".gif": "gif", ".webp": "webp"}
_FIXED_TIME = (1980, 1, 1, 0, 0, 0)
_MAX_UNCOMPRESSED = 512 * 1024 * 1024

_RELS_DROP = re.compile(
    rb'<Relationship\b[^>]*Target="[^"]*docProps/[^"]*"[^>]*/>', re.IGNORECASE
)


def _strip_embedded_image(data: bytes, name: str, workdir: Path) -> bytes:
    kind = _IMAGE_KINDS.get(Path(name).suffix.lower())
    if kind is None:
        return data  # vector/EMF and friends carry no EXIF
    src = workdir / "embedded.in"
    dst = workdir / "embedded.out"
    src.write_bytes(data)
    try:
        images.sanitize(kind, src, dst)
        return dst.read_bytes()
    finally:
        src.unlink(missing_ok=True)
        dst.unlink(missing_ok=True)


def sanitize(kind: str, src: Path, dst: Path) -> None:
    with zipfile.ZipFile(src) as zin:
        infos = zin.infolist()
        if sum(i.file_size for i in infos) > _MAX_UNCOMPRESSED:
            raise ValueError("Document expands to an implausible size.")

        with tempfile.TemporaryDirectory(dir=config.TMP_DIR) as td:
            workdir = Path(td)
            with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
                for info in infos:
                    name = info.filename
                    low = name.lower()
                    if low.startswith("docprops/") or name.endswith("/"):
                        continue

                    data = zin.read(info)
                    if low == "_rels/.rels":
                        data = _RELS_DROP.sub(b"", data)
                    elif any(low.startswith(d) for d in _MEDIA_DIRS):
                        data = _strip_embedded_image(data, name, workdir)

                    entry = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
                    entry.compress_type = zipfile.ZIP_DEFLATED
                    entry.external_attr = info.external_attr
                    zout.writestr(entry, data)
