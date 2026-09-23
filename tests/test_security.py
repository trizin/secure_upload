"""Rejections, headers, and the guarantees around the token."""

import io

import pytest
from PIL import Image

from app import config, crypto, db, detect


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (0, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


@pytest.mark.parametrize(
    "name,data,expect_fragment",
    [
        ("page.html", b"<!DOCTYPE html><html><body>hi</body></html>", "JavaScript"),
        ("logo.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>x()</script></svg>', "JavaScript"),
        ("bundle.zip", None, "Archives"),
        ("tool.exe", b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 64, "Unrecognised"),
    ],
)
def test_dangerous_and_unstrippable_types_are_refused(upload, name, data, expect_fragment):
    if data is None:
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("a.txt", "hello")
        data = buf.getvalue()

    response = upload(name, data)
    assert response.status_code == 415
    assert expect_fragment.lower() in response.json()["error"].lower()


def test_empty_file_is_refused(upload):
    assert upload("nothing.txt", b"").status_code == 400


def test_oversize_is_refused(upload, monkeypatch):
    monkeypatch.setattr(config, "MAX_FILE_SIZE", 1024)
    response = upload("big.txt", b"x" * 4096)
    assert response.status_code == 413
    assert "limit" in response.json()["error"].lower()


def test_decompression_bomb_is_refused(upload, monkeypatch):
    """A small upload that decodes to an enormous bitmap must not be allowed
    to exhaust memory on a 1GB host."""
    monkeypatch.setattr(config, "MAX_IMAGE_PIXELS", 1000)
    buf = io.BytesIO()
    Image.new("RGB", (400, 400), (255, 255, 255)).save(buf, "PNG")
    response = upload("bomb.png", buf.getvalue())
    assert response.status_code == 415
    assert "megapixel" in response.json()["error"].lower()


def test_filename_cannot_escape_or_inject(upload, client):
    response = upload("../../../etc/cron.d/evil\r\nX-Injected: 1.png", _png())
    assert response.status_code == 200
    name = response.json()["name"]
    assert "/" not in name and "\\" not in name
    assert "\r" not in name and "\n" not in name
    assert name.endswith(".png")

    token = response.json()["url"].rsplit("/", 1)[-1]
    raw = client.get(f"/d/{token}/raw")
    assert "x-injected" not in {k.lower() for k in raw.headers}


def test_extension_follows_the_bytes_not_the_claim(upload):
    """A PNG uploaded as 'invoice.pdf' is served as a .png."""
    response = upload("invoice.pdf", _png(), "application/pdf")
    assert response.status_code == 200
    assert response.json()["name"] == "invoice.png"


def test_everything_is_served_as_an_attachment(upload, client):
    token = upload("pic.png", _png()).json()["url"].rsplit("/", 1)[-1]
    raw = client.get(f"/d/{token}/raw")
    assert raw.headers["content-disposition"].startswith("attachment;")
    assert raw.headers["x-content-type-options"] == "nosniff"
    assert raw.headers["referrer-policy"] == "no-referrer"


def test_app_pages_carry_a_strict_csp(client):
    headers = client.get("/").headers
    assert "default-src 'self'" in headers["content-security-policy"]
    assert "unsafe-inline" not in headers["content-security-policy"]
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["x-frame-options"] == "DENY"


def test_token_is_never_written_to_the_database(upload):
    token = upload("pic.png", _png()).json()["url"].rsplit("/", 1)[-1]
    stored = config.DB_PATH.read_bytes()
    assert token.encode() not in stored
    assert crypto.lookup_key(token).encode() in stored


def test_blob_on_disk_is_not_the_file(upload):
    """Whoever reads the disk without the link gets ciphertext."""
    original = _png()
    token = upload("pic.png", original).json()["url"].rsplit("/", 1)[-1]
    row = db.get_file(crypto.lookup_key(token))
    blob = (config.BLOB_DIR / row["blob_id"]).read_bytes()
    assert b"\x89PNG" not in blob
    assert original not in blob


def test_unknown_token_is_indistinguishable_from_expired(client):
    a = client.get("/d/" + "A" * 43)
    assert a.status_code == 404
    assert "expired, been deleted, or never existed" in a.text


def test_host_split_is_enforced(client, monkeypatch, upload):
    token = upload("pic.png", _png()).json()["url"].rsplit("/", 1)[-1]
    monkeypatch.setattr(config, "ENFORCE_HOSTS", True)
    monkeypatch.setattr(config, "APP_HOST", "app.test")
    monkeypatch.setattr(config, "FILE_HOST", "files.test")

    # Bytes are refused on the app origin...
    assert client.get(f"/d/{token}/raw", headers={"host": "app.test"}).status_code == 404
    # ...and everything but bytes is refused on the file origin.
    assert client.get("/", headers={"host": "files.test"}).status_code == 404
    assert client.get(f"/d/{token}", headers={"host": "files.test"}).status_code == 404
    # Each does its own job.
    assert client.get("/", headers={"host": "app.test"}).status_code == 200
    assert client.get(f"/d/{token}/raw", headers={"host": "files.test"}).status_code == 200
    # An unknown Host (e.g. hitting the bare IP) gets nothing.
    assert client.get("/", headers={"host": "203.0.113.9"}).status_code == 404


def test_file_origin_is_fully_sandboxed(client, monkeypatch, upload):
    token = upload("pic.png", _png()).json()["url"].rsplit("/", 1)[-1]
    monkeypatch.setattr(config, "ENFORCE_HOSTS", True)
    monkeypatch.setattr(config, "APP_HOST", "app.test")
    monkeypatch.setattr(config, "FILE_HOST", "files.test")
    csp = client.get(f"/d/{token}/raw", headers={"host": "files.test"}).headers[
        "content-security-policy"
    ]
    assert "sandbox" in csp and "default-src 'none'" in csp


def test_head_reports_size_without_decrypting(client, upload):
    body = upload("pic.png", _png()).json()
    token = body["url"].rsplit("/", 1)[-1]

    head = client.head(f"/d/{token}/raw")
    assert head.status_code == 200
    assert head.headers["content-length"] == str(body["size"])
    assert head.headers["content-disposition"].startswith("attachment;")
    assert head.content == b""

    # A HEAD must not look like a download in the stats.
    row = db.get_file(crypto.lookup_key(token))
    assert row["downloads"] == 0

    assert client.head("/d/" + "A" * 43).status_code == 404


def test_upload_returns_both_a_share_link_and_a_direct_file_link(upload, client):
    body = upload("pic.png", _png()).json()
    token = body["url"].rsplit("/", 1)[-1]

    assert body["url"] == f"http://testserver/d/{token}"
    assert body["file_url"] == f"http://files.testserver/d/{token}/raw"

    # The direct link is the file itself, not a page about it.
    direct = client.get(f"/d/{token}/raw")
    assert direct.status_code == 200
    assert direct.content == _png()


def test_landing_page_shows_the_direct_link(client, upload):
    token = upload("pic.png", _png()).json()["url"].rsplit("/", 1)[-1]
    page = client.get(f"/d/{token}").text
    assert f"http://files.testserver/d/{token}/raw" in page
    assert 'data-copy="direct"' in page
    assert ">Download</a>" in page  # the button is still there


def test_watermark_page_is_served_and_self_contained(client):
    page = client.get("/watermark")
    assert page.status_code == 200
    body = page.text
    for asset in ("/static/stego.js", "/static/robustmark.js",
                  "/static/imagefx.js", "/static/watermark.js"):
        assert asset in body
    # Strict CSP must still apply: no inline script anywhere on the page.
    assert "unsafe-inline" not in page.headers["content-security-policy"]
    assert "<script>" not in body

    for asset in ("stego.js", "robustmark.js", "imagefx.js", "watermark.js"):
        served = client.get(f"/static/{asset}")
        assert served.status_code == 200
        assert "javascript" in served.headers["content-type"]


def test_watermark_page_has_no_server_side_endpoint(client):
    """The image must never be uploaded to do the work. If a POST handler ever
    appears here, this test should be the thing that objects."""
    assert client.post("/watermark").status_code == 405
    assert client.post("/api/watermark").status_code == 404


def test_watermark_page_is_forbidden_from_using_the_network(client):
    """The locality guarantee is enforced by the browser, not by my good
    intentions: connect-src 'none' blocks fetch, XHR, WebSocket and beacon."""
    csp = client.get("/watermark").headers["content-security-policy"]
    assert "connect-src 'none'" in csp
    assert "form-action 'none'" in csp

    # The rest of the site still needs to talk to itself, or uploading breaks.
    assert "connect-src 'self'" in client.get("/").headers["content-security-policy"]


def test_watermark_page_can_still_load_a_local_image(client):
    """blob: has to be listed explicitly. It is how the page reads the file
    you picked, and 'self' does not cover blob URLs - omit it and the page
    silently cannot open any image at all."""
    csp = client.get("/watermark").headers["content-security-policy"]
    img = [d for d in csp.split(";") if d.strip().startswith("img-src")][0]
    assert "blob:" in img


def test_watermark_javascript_contains_no_network_calls(client):
    """A guard against someone adding a convenience upload later and quietly
    undoing the guarantee the page makes."""
    import re

    for asset in ("stego.js", "robustmark.js", "imagefx.js", "watermark.js"):
        source = client.get(f"/static/{asset}").text
        for banned in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "importScripts"):
            assert banned not in source, f"{asset} can reach the network via {banned}"
        # Only blob: object URLs may be assigned to .src on this page.
        for match in re.findall(r"\.src = (\w+)", source):
            assert match in ("url",), f"{asset} assigns an unexpected source: {match}"


def test_watermark_page_states_its_limits(client):
    """The page promises robustness; it must also admit what it cannot do."""
    import re

    # Strip tags and collapse whitespace: these assert what the page SAYS, not
    # where the source wraps or where a <strong> happens to fall.
    raw = client.get("/watermark").text
    body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).lower()

    assert "not survive cropping" in body, "page must admit the mark dies on cropping"
    assert "leaves your device" in body, "page must state that the image stays local"


def test_watermark_page_offers_both_hidden_layers(client):
    """The two invisible marks make opposite trades - a sentence that breaks
    easily, and a 48-bit ID that does not. Both must be on the page, and the
    page must say which survives what."""
    import re

    raw = client.get("/watermark").text
    assert 'id="wmStegoText"' in raw, "message layer missing"
    assert 'id="wmRobustId"' in raw, "robust ID layer missing"

    body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).lower()
    assert "48 bits" in body
    assert "not survive cropping" in body, "must say the message dies on a crop"
    assert "cropping" in body and "downscaling" in body
    assert "rotation defeats it" in body, "must state the robust layer's limit too"
