"""Image metadata removal.

JPEG and PNG are stripped losslessly by rewriting the container and dropping
the metadata segments/chunks outright. The pixel data is copied byte for byte,
so photos do not lose quality on upload.

The one case that forces a re-encode is a JPEG whose EXIF Orientation says the
image should be rotated. Dropping that tag without baking the rotation into the
pixels would serve the photo sideways, so those are transposed and re-encoded.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

from .. import config

Image.MAX_IMAGE_PIXELS = config.MAX_IMAGE_PIXELS


class TooLarge(Exception):
    pass


# PNG chunks worth keeping: these affect how the image renders, and carry no
# information about who made it or where. Everything else (tEXt, zTXt, iTXt,
# eXIf, tIME, iCCP, and anything unrecognised) is dropped.
_PNG_KEEP = {
    b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS",
    b"gAMA", b"cHRM", b"sRGB", b"sBIT", b"bKGD", b"pHYs",
    b"acTL", b"fcTL", b"fdAT",  # APNG animation
}


def _check_pixels(path: Path) -> None:
    limit_mp = config.MAX_IMAGE_PIXELS / 1e6
    try:
        with Image.open(path) as im:
            w, h = im.size
    except Image.DecompressionBombError as exc:
        # Pillow refuses outright well past the limit; turn that into the same
        # user-facing rejection rather than an opaque server error.
        raise TooLarge(f"Image exceeds the {limit_mp:.0f} megapixel limit.") from exc
    if w * h > config.MAX_IMAGE_PIXELS:
        raise TooLarge(
            f"Image is {w}x{h} ({w * h / 1e6:.0f} megapixels). "
            f"The limit is {config.MAX_IMAGE_PIXELS / 1e6:.0f} megapixels."
        )


def _jpeg_orientation(path: Path) -> int:
    try:
        with Image.open(path) as im:
            return int(im.getexif().get(0x0112, 1) or 1)
    except Exception:
        return 1


def _strip_jpeg_lossless(src: Path, dst: Path) -> None:
    """Drop every APPn and COM segment, copy the compressed scan verbatim."""
    data = src.read_bytes()
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")

    out = bytearray(b"\xff\xd8")
    i, n = 2, len(data)
    while i < n:
        if data[i] != 0xFF:
            raise ValueError("bad JPEG structure")
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            break
        marker = data[i]
        i += 1

        if marker == 0xD9:  # EOI
            out += b"\xff\xd9"
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:  # standalone markers
            out += bytes([0xFF, marker])
            continue

        seglen = int.from_bytes(data[i:i + 2], "big")
        if seglen < 2:
            raise ValueError("bad segment length")
        segment = data[i:i + seglen]
        i += seglen

        if marker == 0xDA:  # start of scan: entropy data runs to the end
            out += bytes([0xFF, marker]) + segment + data[i:]
            break

        if 0xE0 <= marker <= 0xEF or marker == 0xFE:
            continue  # APP0-APP15 (EXIF, XMP, IPTC, ICC, JFIF) and COM

        out += bytes([0xFF, marker]) + segment

    dst.write_bytes(bytes(out))


def _strip_png_lossless(src: Path, dst: Path) -> None:
    data = src.read_bytes()
    sig = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(sig):
        raise ValueError("not a PNG")

    out = bytearray(sig)
    i, n = 8, len(data)
    while i + 8 <= n:
        length = int.from_bytes(data[i:i + 4], "big")
        ctype = data[i + 4:i + 8]
        end = i + 12 + length
        if length > n or end > n:
            raise ValueError("truncated PNG chunk")
        if ctype in _PNG_KEEP:
            out += data[i:end]
        i = end
        if ctype == b"IEND":
            break
    dst.write_bytes(bytes(out))


def _resave(src: Path, dst: Path, fmt: str) -> None:
    """Fallback path: decode and re-encode, which drops everything not pixels."""
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im) or im
        params: dict = {}
        if fmt == "JPEG":
            params = {"quality": 95, "subsampling": "keep", "optimize": True}
            if im.mode not in ("RGB", "L", "CMYK"):
                im = im.convert("RGB")
        elif fmt in ("GIF", "WEBP"):
            if getattr(im, "n_frames", 1) > 1:
                params["save_all"] = True
        im.save(dst, fmt, **params)


def sanitize(kind: str, src: Path, dst: Path) -> None:
    _check_pixels(src)

    if kind == "jpeg":
        if _jpeg_orientation(src) in (0, 1):
            _strip_jpeg_lossless(src, dst)
        else:
            _resave(src, dst, "JPEG")
        return

    if kind == "png":
        _strip_png_lossless(src, dst)
        return

    _resave(src, dst, {"gif": "GIF", "webp": "WEBP"}[kind])
