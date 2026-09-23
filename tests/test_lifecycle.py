"""Expiry, deletion and the limits that keep a public service standing."""

import io
import os
import time

from PIL import Image

from app import config, crypto, db, sweeper


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (200, 100, 0)).save(buf, "PNG")
    return buf.getvalue()


def _expire(token: str) -> None:
    with db.connect() as conn:
        conn.execute(
            "UPDATE files SET expires_at = ? WHERE lookup_key = ?",
            (int(time.time()) - 1, crypto.lookup_key(token)),
        )


def test_upload_expires_in_48_hours(upload):
    body = upload("pic.png", _png()).json()
    assert 47 * 3600 < body["expires_at"] - time.time() <= 48 * 3600


def test_expired_link_is_dead_even_if_the_sweeper_never_runs(client, upload):
    """The lazy check is the guarantee; the sweeper only reclaims space."""
    token = upload("pic.png", _png()).json()["url"].rsplit("/", 1)[-1]
    assert client.get(f"/d/{token}").status_code == 200

    _expire(token)

    assert client.get(f"/d/{token}").status_code == 404
    assert client.get(f"/d/{token}/raw").status_code == 404
    # The bytes are demonstrably still on disk - nothing has swept yet.
    with db.connect() as conn:
        row = conn.execute(
            "SELECT blob_id FROM files WHERE lookup_key = ?",
            (crypto.lookup_key(token),),
        ).fetchone()
    assert (config.BLOB_DIR / row["blob_id"]).exists()


def test_sweeper_reclaims_expired_bytes(client, upload):
    token = upload("pic.png", _png()).json()["url"].rsplit("/", 1)[-1]
    lookup = crypto.lookup_key(token)
    blob = config.BLOB_DIR / db.get_file(lookup)["blob_id"]
    assert blob.exists()

    _expire(token)
    stats = sweeper.sweep_once()

    assert stats["expired"] >= 1
    assert not blob.exists()
    with db.connect() as conn:
        assert conn.execute(
            "SELECT 1 FROM files WHERE lookup_key = ?", (lookup,)
        ).fetchone() is None


def test_sweeper_reconciles_orphaned_blobs(client):
    """A crash between writing bytes and committing the row must not leak
    files that nothing will ever delete."""
    orphan = config.BLOB_DIR / "deadbeef"
    orphan.write_bytes(b"leftovers")
    old = time.time() - sweeper.ORPHAN_GRACE - 60
    os.utime(orphan, (old, old))

    stats = sweeper.sweep_once()

    assert stats["orphans"] >= 1
    assert not orphan.exists()


def test_recent_orphans_are_left_alone(client, upload):
    """An in-flight upload must not have its blob swept out from under it."""
    fresh = config.BLOB_DIR / "just-written"
    fresh.write_bytes(b"in flight")
    sweeper.sweep_once()
    assert fresh.exists()
    fresh.unlink()


def test_owner_can_delete_early(client, upload):
    body = upload("pic.png", _png()).json()
    token = body["url"].rsplit("/", 1)[-1]
    delete_token = body["delete_url"].rsplit("/", 1)[-1]
    blob = config.BLOB_DIR / db.get_file(crypto.lookup_key(token))["blob_id"]

    assert client.get(f"/x/{delete_token}").status_code == 200
    assert client.post(f"/x/{delete_token}").status_code == 200

    assert not blob.exists()
    assert client.get(f"/d/{token}").status_code == 404
    # The revocation link is single use.
    assert client.post(f"/x/{delete_token}").status_code == 404


def test_download_link_does_not_reveal_the_delete_link(upload):
    body = upload("pic.png", _png()).json()
    assert body["url"].rsplit("/", 1)[-1] != body["delete_url"].rsplit("/", 1)[-1]


def test_rate_limit_stops_a_flood(client, upload, monkeypatch):
    with db.connect() as conn:
        conn.execute("DELETE FROM rate")
    monkeypatch.setattr(config, "RATE_UPLOADS_PER_HOUR", 2)

    assert upload("a.png", _png()).status_code == 200
    assert upload("b.png", _png()).status_code == 200
    blocked = upload("c.png", _png())
    assert blocked.status_code == 429
    assert "limit" in blocked.json()["error"].lower()


def test_disk_quota_stops_new_uploads(upload, monkeypatch):
    monkeypatch.setattr(config, "TOTAL_QUOTA", 1)
    response = upload("pic.png", _png())
    assert response.status_code == 507
    assert "capacity" in response.json()["error"].lower()


def test_report_flags_a_file(client, upload):
    token = upload("pic.png", _png()).json()["url"].rsplit("/", 1)[-1]
    assert client.get(f"/r/{token}").status_code == 200
    assert client.post(f"/r/{token}").status_code == 200
    with db.connect() as conn:
        row = conn.execute(
            "SELECT reported FROM files WHERE lookup_key = ?",
            (crypto.lookup_key(token),),
        ).fetchone()
    assert row["reported"] == 1


def test_robots_blocks_indexing(client):
    body = client.get("/robots.txt").text
    assert "Disallow: /" in body
