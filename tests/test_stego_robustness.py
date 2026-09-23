"""Does the invisible watermark actually survive what happens to a shared image?

The claim on the page is "survives re-saving and JPEG re-compression". These
tests put the real shipped JavaScript through a real JPEG cycle and check it,
including the cases where it is expected to fail.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFilter

CLI = Path(__file__).parent / "js" / "stego_cli.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node absent")

MESSAGE = "shared with acme bank 2026-09-22"


def _photo(w=800, h=600) -> Image.Image:
    """Something photo-like: gradients, edges and grain, not flat colour."""
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        draw.line([(0, y), (w, y)], fill=(40 + y * 120 // h, 70 + y * 90 // h, 120 - y * 60 // h))
    for i in range(24):
        x0, y0 = (i * 97) % w, (i * 61) % h
        draw.ellipse([x0, y0, x0 + 70 + i, y0 + 50 + i],
                     fill=((i * 37) % 256, (i * 91) % 256, (i * 53) % 256))
    draw.text((30, 30), "SPECIMEN DOCUMENT 1234 5678", fill=(250, 250, 250))
    return img.filter(ImageFilter.GaussianBlur(0.4))


def _node(*args) -> dict:
    out = subprocess.run(["node", str(CLI), *map(str, args)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _embed(img: Image.Image, tmp: Path, strength="") -> Image.Image:
    """Empty strength means "use the shipped default", which is what the page
    actually does - the test must not quietly pick a different one."""
    raw, out = tmp / "in.raw", tmp / "out.raw"
    raw.write_bytes(img.convert("RGBA").tobytes())
    _node("embed", raw, img.width, img.height, MESSAGE, out, strength)
    return Image.frombytes("RGBA", img.size, out.read_bytes()).convert("RGB")


def _extract(img: Image.Image, tmp: Path) -> dict:
    raw = tmp / "check.raw"
    raw.write_bytes(img.convert("RGBA").tobytes())
    return _node("extract", raw, img.width, img.height)


def test_survives_a_plain_png_resave(tmp_path):
    marked = _embed(_photo(), tmp_path)
    path = tmp_path / "a.png"
    marked.save(path)
    assert _extract(Image.open(path), tmp_path)["text"] == MESSAGE


@pytest.mark.parametrize("quality", [95, 90, 85, 75, 65, 55])
def test_survives_jpeg_recompression(tmp_path, quality):
    marked = _embed(_photo(), tmp_path)
    path = tmp_path / f"q{quality}.jpg"
    marked.save(path, "JPEG", quality=quality)
    result = _extract(Image.open(path), tmp_path)
    assert result["text"] == MESSAGE, f"lost at quality {quality}"
    assert result["confidence"] > 0.8


def test_survives_being_saved_twice(tmp_path):
    """The realistic path: you save it, they save it again."""
    marked = _embed(_photo(), tmp_path)
    first, second = tmp_path / "1.jpg", tmp_path / "2.jpg"
    marked.save(first, "JPEG", quality=90)
    Image.open(first).save(second, "JPEG", quality=80)
    assert _extract(Image.open(second), tmp_path)["text"] == MESSAGE


def test_survives_mild_brightness_change(tmp_path):
    from PIL import ImageEnhance

    marked = _embed(_photo(), tmp_path)
    brighter = ImageEnhance.Brightness(marked).enhance(1.12)
    assert _extract(brighter, tmp_path)["text"] == MESSAGE


def test_survives_the_full_supload_pipeline(tmp_path):
    """Watermark, then upload: the metadata stripper must not destroy it."""
    from app.sanitize import images

    marked = _embed(_photo(), tmp_path)
    src, dst = tmp_path / "marked.jpg", tmp_path / "stripped.jpg"
    marked.save(src, "JPEG", quality=92)
    images.sanitize("jpeg", src, dst)
    assert _extract(Image.open(dst), tmp_path)["text"] == MESSAGE


# --- the honest failure cases -------------------------------------------

def test_does_not_survive_resizing(tmp_path):
    """Stated as a limitation on the page; asserted here so it stays true."""
    marked = _embed(_photo(), tmp_path)
    smaller = marked.resize((marked.width // 2, marked.height // 2))
    assert _extract(smaller, tmp_path)["text"] != MESSAGE


def test_does_not_survive_cropping(tmp_path):
    """Cropping shifts the 8x8 grid, so the blocks no longer line up."""
    marked = _embed(_photo(), tmp_path)
    cropped = marked.crop((13, 7, marked.width - 20, marked.height - 11))
    assert _extract(cropped, tmp_path)["text"] != MESSAGE


def test_fails_closed_rather_than_inventing_a_message(tmp_path):
    """A wrong answer would be worse than no answer: the CRC must catch it."""
    assert _extract(_photo(), tmp_path)["text"] is None
    marked = _embed(_photo(), tmp_path)
    assert _extract(marked.resize((333, 249)), tmp_path)["text"] is None
