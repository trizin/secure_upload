"""The robust ID layer: does it actually survive what it claims to?

This mark exists because the block-DCT one in stego.js dies on cropping and
resizing. It carries 48 bits instead of a sentence, and that trade is only
worth making if the robustness is real - so every claim made on the page is
asserted here, including the point where it stops working.
"""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFilter

CLI = Path(__file__).parent / "js" / "robustmark_cli.js"
STEGO_CLI = Path(__file__).parent / "js" / "stego_cli.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node absent")

ID = "a1b2c3d4e5f6"


def _photo(w=1400, h=1000) -> Image.Image:
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=(45 + y * 110 // h, 75 + y * 85 // h, 125 - y * 55 // h))
    for i in range(40):
        x0, y0 = (i * 137) % w, (i * 89) % h
        d.ellipse([x0, y0, x0 + 60 + i * 2, y0 + 45 + i],
                  fill=((i * 37) % 256, (i * 91) % 256, (i * 53) % 256))
    d.text((40, 40), "SPECIMEN 1234", fill=(250, 250, 250))
    return img.filter(ImageFilter.GaussianBlur(0.4))


def _embed(img: Image.Image, tmp: Path, ident=ID) -> Image.Image:
    raw, out = tmp / "i.raw", tmp / "o.raw"
    raw.write_bytes(img.convert("RGBA").tobytes())
    subprocess.run(["node", str(CLI), "embed", str(raw), str(img.width), str(img.height),
                    ident, str(out), ""], capture_output=True, check=True)
    return Image.frombytes("RGBA", img.size, out.read_bytes()).convert("RGB")


def _extract(img: Image.Image, tmp: Path):
    raw = tmp / "c.raw"
    raw.write_bytes(img.convert("RGBA").tobytes())
    r = subprocess.run(["node", str(CLI), "extract", str(raw), str(img.width), str(img.height)],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout).get("id")


def _jpeg(img: Image.Image, tmp: Path, quality: int) -> Image.Image:
    p = tmp / f"q{quality}.jpg"
    img.save(p, "JPEG", quality=quality)
    return Image.open(p).convert("RGB")


def test_round_trips(tmp_path):
    assert _extract(_embed(_photo(), tmp_path), tmp_path) == ID


def test_is_invisible(tmp_path):
    src = _photo()
    marked = _embed(src, tmp_path)
    a, b = src.tobytes(), marked.tobytes()
    mse = sum((x - y) ** 2 for x, y in zip(a, b)) / len(a)
    psnr = 10 * math.log10(255 * 255 / mse)
    assert psnr > 44, f"only {psnr:.1f} dB"


@pytest.mark.parametrize("quality", [90, 75, 60])
def test_survives_jpeg(tmp_path, quality):
    marked = _embed(_photo(), tmp_path)
    assert _extract(_jpeg(marked, tmp_path, quality), tmp_path) == ID


@pytest.mark.parametrize("box", [
    (13, 7, 1300, 940),      # small trim
    (62, 41, 1100, 800),     # off-grid offset, both axes
    (200, 150, 800, 620),    # half the picture gone
])
def test_survives_cropping(tmp_path, box):
    """The headline claim, and the thing the block-DCT mark cannot do."""
    marked = _embed(_photo(), tmp_path)
    x, y, w, h = box
    assert _extract(marked.crop((x, y, x + w, y + h)), tmp_path) == ID


def test_survives_crop_then_jpeg(tmp_path):
    marked = _embed(_photo(), tmp_path)
    cropped = marked.crop((62, 41, 1160, 840))
    assert _extract(_jpeg(cropped, tmp_path, 75), tmp_path) == ID


@pytest.mark.parametrize("factor", [0.75, 0.5])
def test_survives_downscaling(tmp_path, factor):
    marked = _embed(_photo(), tmp_path)
    smaller = marked.resize((int(marked.width * factor), int(marked.height * factor)), Image.LANCZOS)
    assert _extract(smaller, tmp_path) == ID


def test_survives_downscale_then_jpeg(tmp_path):
    marked = _embed(_photo(), tmp_path)
    half = marked.resize((marked.width // 2, marked.height // 2), Image.LANCZOS)
    assert _extract(_jpeg(half, tmp_path, 80), tmp_path) == ID


def test_survives_supload_metadata_stripping(tmp_path):
    from app.sanitize import images

    marked = _embed(_photo(), tmp_path)
    src, dst = tmp_path / "m.jpg", tmp_path / "m-clean.jpg"
    marked.save(src, "JPEG", quality=92)
    images.sanitize("jpeg", src, dst)
    assert _extract(Image.open(dst), tmp_path) == ID


def test_coexists_with_the_message_mark(tmp_path):
    """Both layers in one image: the sentence and the robust ID. Neither may
    destroy the other, or layering them is pointless."""
    message = "shared with acme bank"
    marked = _embed(_photo(), tmp_path)

    raw, out = tmp_path / "s.raw", tmp_path / "s.out"
    raw.write_bytes(marked.convert("RGBA").tobytes())
    subprocess.run(["node", str(STEGO_CLI), "embed", str(raw), str(marked.width),
                    str(marked.height), message, str(out), ""],
                   capture_output=True, check=True)
    both = Image.frombytes("RGBA", marked.size, out.read_bytes()).convert("RGB")
    path = tmp_path / "both.jpg"
    both.save(path, "JPEG", quality=92)
    saved = Image.open(path).convert("RGB")

    assert _extract(saved, tmp_path) == ID, "robust ID lost when both are present"

    chk = tmp_path / "chk.raw"
    chk.write_bytes(saved.convert("RGBA").tobytes())
    r = subprocess.run(["node", str(STEGO_CLI), "extract", str(chk), str(saved.width),
                        str(saved.height)], capture_output=True, text=True, check=True)
    assert json.loads(r.stdout)["text"] == message, "message lost when both are present"


# --- the limits, asserted so they stay honest ---------------------------

def test_does_not_survive_a_heavy_downscale(tmp_path):
    """Stated on the page as roughly 2x. Past that the carrier frequencies
    fall below the new Nyquist limit and stop existing in the file - no
    decoder can recover them, so the page must not imply otherwise."""
    marked = _embed(_photo(), tmp_path)
    tiny = marked.resize((int(marked.width * 0.35), int(marked.height * 0.35)), Image.LANCZOS)
    assert _extract(tiny, tmp_path) != ID


def test_does_not_survive_rotation(tmp_path):
    marked = _embed(_photo(), tmp_path)
    assert _extract(marked.rotate(7, expand=True), tmp_path) != ID


def test_reports_nothing_on_an_unmarked_image(tmp_path):
    assert _extract(_photo(), tmp_path) is None


def test_fails_closed_rather_than_inventing_an_id(tmp_path):
    """A wrong ID would accuse the wrong person, which is worse than no
    answer. The CRC has to catch it."""
    marked = _embed(_photo(), tmp_path)
    for attack in (
        lambda i: i.resize((int(i.width * 0.3), int(i.height * 0.3)), Image.LANCZOS),
        lambda i: i.rotate(13, expand=True),
        lambda i: _jpeg(i, tmp_path, 20),
    ):
        got = _extract(attack(marked), tmp_path)
        assert got in (None, ID), f"invented a different ID: {got}"
