"""The whole /watermark save path, end to end.

Conceal areas, embed the hidden mark over the result, encode to JPEG - then
check that the concealed content is genuinely gone from the file people will
actually send, and that the hidden mark still reads.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFilter

CLI = Path(__file__).parent / "js" / "pipeline_cli.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node absent")

BOX = {"x0": 120, "y0": 150, "x1": 420, "y1": 230}
MESSAGE = "issued to acme bank 2026-09-22"


def _document(secret: str) -> Image.Image:
    """A document photo: texture, and a line of small print to conceal."""
    img = Image.new("RGB", (700, 460), (214, 209, 198))
    d = ImageDraw.Draw(img)
    for y in range(0, 460, 3):
        d.line([(0, y), (700, y)], fill=(206 + y % 7, 201 + y % 5, 190 + y % 9))
    d.rectangle([40, 40, 660, 420], outline=(120, 115, 105), width=2)
    d.text((60, 70), "CERTIFICATE OF IDENTITY", fill=(40, 40, 45))
    for i, line in enumerate(["NAME    BERKAY S", "ISSUED  2024-03-11", "OFFICE  CENTRAL"]):
        d.text((60, 100 + i * 16), line, fill=(60, 60, 66))
    # The bit that gets concealed.
    for i in range(6):
        d.text((BOX["x0"] + 6, BOX["y0"] + 6 + i * 12), secret, fill=(25, 25, 30))
    return img.filter(ImageFilter.GaussianBlur(0.3))


def _run(img: Image.Image, tmp: Path, tag: str, message: str, **spec) -> Image.Image:
    raw, out = tmp / f"{tag}.raw", tmp / f"{tag}.out"
    raw.write_bytes(img.convert("RGBA").tobytes())
    options = {**BOX, "style": "defocus", "strength": 10, **spec}
    subprocess.run(
        ["node", str(CLI), str(raw), str(img.width), str(img.height),
         json.dumps(options), message, str(out)],
        capture_output=True, text=True, check=True,
    )
    return Image.frombytes("RGBA", img.size, out.read_bytes()).convert("RGB")


def _extract(img: Image.Image, tmp: Path) -> dict:
    raw = tmp / "check.raw"
    raw.write_bytes(img.convert("RGBA").tobytes())
    out = subprocess.run(
        ["node", str(tmp.parent / "js" / "stego_cli.js") if False else
         str(Path(__file__).parent / "js" / "stego_cli.js"),
         "extract", str(raw), str(img.width), str(img.height)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def _box_difference(a: Image.Image, b: Image.Image) -> float:
    """Mean absolute per-channel difference inside the concealed box, 0-255."""
    crop = (BOX["x0"], BOX["y0"], BOX["x1"], BOX["y1"])
    pa = list(a.crop(crop).getdata())
    pb = list(b.crop(crop).getdata())
    total = sum(abs(x - y) for p, q in zip(pa, pb) for x, y in zip(p, q))
    return total / (len(pa) * 3)


# Measured as an absolute mean difference out of 255, not a ratio. A ratio is
# misleading here: when two secrets look alike the baseline is tiny, so a
# perfectly good result scores badly. What matters is whether the residual is
# below the noise JPEG itself introduces, which is where recovery stops being
# possible.
#
# Two different secrets are needed to measure two different things:
#   SIMILAR ink density  -> isolates leaked STRUCTURE, which must be ~nothing
#   DIFFERENT ink density -> measures leaked BRIGHTNESS, which is unavoidable
#                            for anything meant to look like natural defocus
SIMILAR = ("ACCOUNT 4419 8827 0031", "ACCOUNT 9075 1164 5528")
DIFFERENT = ("ACCOUNT 4419 8827 0031", "WWWWWW 0000 MMMM 8888")
MAX_STRUCTURE = 1.0
MAX_BRIGHTNESS = 3.0


def test_concealed_text_is_gone_from_the_saved_jpeg(tmp_path):
    """Two documents differing only in the concealed line must be
    indistinguishable in the file that actually gets sent."""
    a, b = _document(SIMILAR[0]), _document(SIMILAR[1])
    before = _box_difference(a, b)
    assert before > 3, "the two documents must actually differ for this to mean anything"

    ca = _run(a, tmp_path, "a", MESSAGE)
    cb = _run(b, tmp_path, "b", MESSAGE)
    pa, pb = tmp_path / "a.jpg", tmp_path / "b.jpg"
    ca.save(pa, "JPEG", quality=92)
    cb.save(pb, "JPEG", quality=92)

    after = _box_difference(Image.open(pa).convert("RGB"), Image.open(pb).convert("RGB"))
    assert after < MAX_STRUCTURE, f"residual {after:.2f}/255 is recoverable detail"


def test_hidden_mark_survives_the_whole_save(tmp_path):
    marked = _run(_document("ACCOUNT 4419 8827 0031"), tmp_path, "m", MESSAGE)
    path = tmp_path / "m.jpg"
    marked.save(path, "JPEG", quality=92)
    assert _extract(Image.open(path), tmp_path)["text"] == MESSAGE


def test_hidden_mark_survives_conceal_soften_and_recompression(tmp_path):
    """The hardest combination: areas concealed, whole photo softened, then
    saved and saved again by whoever received it."""
    marked = _run(_document("ACCOUNT 4419 8827 0031"), tmp_path, "s", MESSAGE,
                  soften=4, style="motion", strength=12)
    first, second = tmp_path / "s1.jpg", tmp_path / "s2.jpg"
    marked.save(first, "JPEG", quality=90)
    Image.open(first).save(second, "JPEG", quality=75)
    assert _extract(Image.open(second), tmp_path)["text"] == MESSAGE


@pytest.mark.parametrize("style", ["defocus", "motion", "glare", "lowlight"])
def test_every_style_destroys_the_content_in_the_saved_file(tmp_path, style):
    a, b = _document(SIMILAR[0]), _document(SIMILAR[1])
    before = _box_difference(a, b)
    ca = _run(a, tmp_path, f"a{style}", "", style=style, strength=11)
    cb = _run(b, tmp_path, f"b{style}", "", style=style, strength=11)
    pa, pb = tmp_path / f"a{style}.jpg", tmp_path / f"b{style}.jpg"
    ca.save(pa, "JPEG", quality=92); cb.save(pb, "JPEG", quality=92)
    after = _box_difference(Image.open(pa).convert("RGB"), Image.open(pb).convert("RGB"))
    assert after < MAX_STRUCTURE, f"{style}: residual {after:.2f}/255"


def test_the_marked_image_still_passes_through_upload_sanitising(tmp_path):
    """A watermarked image must survive being uploaded: metadata stripping
    must not wipe the hidden mark."""
    from app.sanitize import images

    marked = _run(_document("ACCOUNT 4419 8827 0031"), tmp_path, "u", MESSAGE)
    src, dst = tmp_path / "u.jpg", tmp_path / "u-clean.jpg"
    marked.save(src, "JPEG", quality=92)
    images.sanitize("jpeg", src, dst)
    assert _extract(Image.open(dst), tmp_path)["text"] == MESSAGE


def test_only_overall_brightness_survives_and_it_is_faint(tmp_path):
    """The honest limit, asserted so it cannot quietly get worse.

    Concealment preserves roughly how dark an area was - that is exactly what
    makes it read as the same scene out of focus rather than a patch pasted
    on. So a heavily inked field and a sparse one stay faintly distinguishable
    even though neither can be read. Do not rely on this to hide *whether* a
    field was filled in; the glare and low-light styles discard even that.
    """
    a, b = _document(DIFFERENT[0]), _document(DIFFERENT[1])
    ca = _run(a, tmp_path, "da", "", style="defocus", strength=11)
    cb = _run(b, tmp_path, "db", "", style="defocus", strength=11)
    pa, pb = tmp_path / "da.jpg", tmp_path / "db.jpg"
    ca.save(pa, "JPEG", quality=92); cb.save(pb, "JPEG", quality=92)
    defocus = _box_difference(Image.open(pa).convert("RGB"), Image.open(pb).convert("RGB"))
    assert defocus < MAX_BRIGHTNESS, f"brightness leak {defocus:.2f}/255 is too strong"

    ga = _run(a, tmp_path, "ga", "", style="glare", strength=13)
    gb = _run(b, tmp_path, "gb", "", style="glare", strength=13)
    qa, qb = tmp_path / "ga.jpg", tmp_path / "gb.jpg"
    ga.save(qa, "JPEG", quality=92); gb.save(qb, "JPEG", quality=92)
    glare = _box_difference(Image.open(qa).convert("RGB"), Image.open(qb).convert("RGB"))
    assert glare < defocus, "glare should discard more brightness than defocus"
