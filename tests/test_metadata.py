"""The tests that justify the whole service: metadata really is gone."""

import io
import zipfile

import pikepdf
import pytest
from PIL import Image

from app import config


def _jpeg_with_exif() -> bytes:
    image = Image.new("RGB", (64, 48), (120, 30, 30))
    exif = Image.Exif()
    exif[0x010F] = "ACME Camera Co"          # Make
    exif[0x0110] = "SpyCam 9000"             # Model
    exif[0x013B] = "Jane Doe"                # Artist
    exif[0x8298] = "Copyright Jane Doe"      # Copyright
    exif[0x9003] = "2021:01:02 03:04:05"     # DateTimeOriginal
    buf = io.BytesIO()
    image.save(buf, "JPEG", exif=exif, quality=92)
    return buf.getvalue()


def _png_with_text() -> bytes:
    from PIL import PngImagePlugin

    meta = PngImagePlugin.PngInfo()
    meta.add_text("Author", "Jane Doe")
    meta.add_text("Comment", "taken at home")
    meta.add_text("Software", "SecretEditor 1.0")
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), (10, 90, 160)).save(buf, "PNG", pnginfo=meta)
    return buf.getvalue()


def _fetch(client, response) -> bytes:
    token = response.json()["url"].rsplit("/", 1)[-1]
    raw = client.get(f"/d/{token}/raw")
    assert raw.status_code == 200
    return raw.content


def test_jpeg_exif_is_removed(client, upload):
    original = _jpeg_with_exif()
    # Sanity check: the fixture really does carry EXIF to begin with.
    assert Image.open(io.BytesIO(original)).getexif().get(0x010F) == "ACME Camera Co"
    assert b"ACME Camera Co" in original

    served = _fetch(client, upload("holiday.jpg", original, "image/jpeg"))

    assert dict(Image.open(io.BytesIO(served)).getexif()) == {}
    for secret in (b"ACME Camera Co", b"SpyCam 9000", b"Jane Doe", b"2021:01:02"):
        assert secret not in served
    assert b"\xff\xe1" not in served[:4]  # no APP1 marker at the head


def test_jpeg_strip_is_lossless(client, upload):
    """Pixels must survive untouched: a photo should not lose quality just
    because its metadata was removed."""
    original = _jpeg_with_exif()
    served = _fetch(client, upload("holiday.jpg", original, "image/jpeg"))

    before = Image.open(io.BytesIO(original)).convert("RGB").tobytes()
    after = Image.open(io.BytesIO(served)).convert("RGB").tobytes()
    assert before == after
    assert len(served) < len(original)  # metadata genuinely gone, not moved


def test_png_text_chunks_are_removed(client, upload):
    original = _png_with_text()
    assert b"SecretEditor" in original

    served = _fetch(client, upload("chart.png", original, "image/png"))

    assert Image.open(io.BytesIO(served)).text == {}
    assert b"SecretEditor" not in served
    assert b"Jane Doe" not in served
    # Lossless: identical pixels.
    assert (Image.open(io.BytesIO(original)).tobytes()
            == Image.open(io.BytesIO(served)).tobytes())


def test_pdf_metadata_is_removed(client, upload):
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    with pdf.open_metadata() as meta:
        meta["dc:creator"] = ["Jane Doe"]
        meta["dc:title"] = "Quarterly Secrets"
    pdf.docinfo["/Author"] = "Jane Doe"
    pdf.docinfo["/Producer"] = "SecretEditor 1.0"
    buf = io.BytesIO()
    pdf.save(buf)
    original = buf.getvalue()
    assert b"Jane Doe" in original

    served = _fetch(client, upload("report.pdf", original, "application/pdf"))

    with pikepdf.open(io.BytesIO(served)) as out:
        assert "/Info" not in out.trailer
        assert "/Metadata" not in out.Root
    assert b"Jane Doe" not in served
    assert b"SecretEditor" not in served


def test_docx_properties_and_embedded_photo_are_removed(client, upload):
    """A Word file leaks twice: its own docProps, and the EXIF of any photo
    pasted into it."""
    photo = _jpeg_with_exif()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?><Relationships>'
            '<Relationship Id="rId1" Type="http://x/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://x/core-properties" Target="docProps/core.xml"/>'
            "</Relationships>",
        )
        zf.writestr("word/document.xml", "<document>hello</document>")
        zf.writestr(
            "docProps/core.xml",
            "<coreProperties><creator>Jane Doe</creator>"
            "<lastModifiedBy>Jane Doe</lastModifiedBy></coreProperties>",
        )
        zf.writestr("docProps/app.xml", "<Properties><Company>ACME</Company></Properties>")
        zf.writestr("word/media/image1.jpeg", photo)
    original = buf.getvalue()

    served = _fetch(client, upload("memo.docx", original, "application/octet-stream"))

    with zipfile.ZipFile(io.BytesIO(served)) as zf:
        names = zf.namelist()
        assert not any(n.lower().startswith("docprops/") for n in names)
        assert "word/document.xml" in names
        assert "word/media/image1.jpeg" in names
        embedded = zf.read("word/media/image1.jpeg")
        assert b"ACME Camera Co" not in embedded
        assert dict(Image.open(io.BytesIO(embedded)).getexif()) == {}
        rels = zf.read("_rels/.rels")
        assert b"docProps" not in rels
        assert b"word/document.xml" in rels


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg absent")
def test_video_metadata_is_removed(client, upload, tmp_path):
    import subprocess

    source = tmp_path / "in.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10:duration=1",
         "-metadata", "title=Secret Trip",
         "-metadata", "artist=Jane Doe",
         "-metadata", "location=51.5074N/0.1278W",
         "-pix_fmt", "yuv420p", str(source)],
        check=True,
    )
    original = source.read_bytes()
    assert b"Secret Trip" in original

    served = _fetch(client, upload("clip.mp4", original, "video/mp4"))

    assert b"Secret Trip" not in served
    assert b"Jane Doe" not in served
    assert b"51.5074N" not in served
