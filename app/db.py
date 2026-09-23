"""SQLite metadata store.

The raw token is never written here. `lookup_key` is sha256(token), so the
database on its own identifies nothing and unlocks nothing: the file key is
derived from the token, which only ever exists inside a live URL.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    lookup_key   TEXT PRIMARY KEY,
    delete_key   TEXT NOT NULL UNIQUE,
    blob_id      TEXT NOT NULL UNIQUE,
    salt         BLOB NOT NULL,
    nonce_prefix BLOB NOT NULL,
    kind         TEXT NOT NULL,
    mime         TEXT NOT NULL,
    orig_name    TEXT NOT NULL,
    size         INTEGER NOT NULL,
    created_at   INTEGER NOT NULL,
    expires_at   INTEGER NOT NULL,
    downloads    INTEGER NOT NULL DEFAULT 0,
    reported     INTEGER NOT NULL DEFAULT 0,
    blocked      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_files_expires ON files(expires_at);

CREATE TABLE IF NOT EXISTS rate (
    ip_hash TEXT NOT NULL,
    ts      INTEGER NOT NULL,
    bytes   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rate_ip_ts ON rate(ip_hash, ts);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(config.DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init() -> None:
    config.ensure_dirs()
    with connect() as conn:
        conn.executescript(_SCHEMA)
    config.DB_PATH.chmod(0o600)


def insert_file(**row) -> None:
    columns = ", ".join(row)
    placeholders = ", ".join(f":{k}" for k in row)
    with connect() as conn:
        conn.execute(f"INSERT INTO files ({columns}) VALUES ({placeholders})", row)


def get_file(lookup_key: str) -> sqlite3.Row | None:
    """Only ever returns a live row. Expired and blocked rows read as absent,
    so a dead sweeper can never cause an expired file to be served."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM files WHERE lookup_key = ? AND blocked = 0 AND expires_at > ?",
            (lookup_key, int(time.time())),
        ).fetchone()
    return row


def bump_download(lookup_key: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE files SET downloads = downloads + 1 WHERE lookup_key = ?",
            (lookup_key,),
        )


def mark_reported(lookup_key: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE files SET reported = reported + 1 WHERE lookup_key = ?",
            (lookup_key,),
        )


def delete_by_delete_key(delete_key: str) -> str | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT blob_id FROM files WHERE delete_key = ?", (delete_key,)
        ).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM files WHERE delete_key = ?", (delete_key,))
    return row["blob_id"]


def expired_rows(now: int | None = None) -> list[sqlite3.Row]:
    now = now if now is not None else int(time.time())
    with connect() as conn:
        return conn.execute(
            "SELECT lookup_key, blob_id FROM files WHERE expires_at <= ?", (now,)
        ).fetchall()


def delete_row(lookup_key: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM files WHERE lookup_key = ?", (lookup_key,))


def live_blob_ids() -> set[str]:
    with connect() as conn:
        return {r["blob_id"] for r in conn.execute("SELECT blob_id FROM files")}


def total_stored_bytes() -> int:
    with connect() as conn:
        row = conn.execute("SELECT COALESCE(SUM(size), 0) AS total FROM files").fetchone()
    return int(row["total"])


def get_by_delete_key(delete_key: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM files WHERE delete_key = ?", (delete_key,)
        ).fetchone()
