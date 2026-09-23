"""Operator CLI.

    python -m app.admin stats
    python -m app.admin reported
    python -m app.admin block <token-or-url>     # kill a link, keep the record
    python -m app.admin purge <token-or-url>     # delete the bytes outright
    python -m app.admin sweep
"""

from __future__ import annotations

import argparse
import sys
import time

from . import config, crypto, db, sweeper


def _token_from(value: str) -> str:
    return value.rstrip("/").rsplit("/", 1)[-1]


def cmd_stats(_: argparse.Namespace) -> int:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(size),0) b, COALESCE(SUM(downloads),0) d,"
            " COALESCE(SUM(reported),0) r, COALESCE(SUM(blocked),0) k FROM files"
        ).fetchone()
    print(f"files stored : {row['n']}")
    print(f"bytes stored : {row['b'] / 1e6:.1f} MB of {config.TOTAL_QUOTA / 1e9:.0f} GB")
    print(f"downloads    : {row['d']}")
    print(f"reported     : {row['r']}")
    print(f"blocked      : {row['k']}")
    return 0


def cmd_reported(_: argparse.Namespace) -> int:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT lookup_key, orig_name, size, reported, blocked, expires_at"
            " FROM files WHERE reported > 0 ORDER BY reported DESC"
        ).fetchall()
    if not rows:
        print("nothing reported")
        return 0
    now = int(time.time())
    for r in rows:
        state = "BLOCKED" if r["blocked"] else "live"
        print(
            f"{r['lookup_key'][:16]}  {state:8}  reports={r['reported']:<3} "
            f"{r['size'] / 1e6:6.2f}MB  expires in {(r['expires_at'] - now) // 3600}h  "
            f"{r['orig_name']}"
        )
    return 0


def _resolve(conn, ident: str):
    """Accept a full URL, a bare token, or a lookup-key prefix."""
    key = crypto.lookup_key(_token_from(ident))
    row = conn.execute("SELECT * FROM files WHERE lookup_key = ?", (key,)).fetchone()
    if row:
        return row
    return conn.execute(
        "SELECT * FROM files WHERE lookup_key LIKE ?", (_token_from(ident) + "%",)
    ).fetchone()


def cmd_block(args: argparse.Namespace) -> int:
    with db.connect() as conn:
        row = _resolve(conn, args.ident)
        if row is None:
            print("no such file", file=sys.stderr)
            return 1
        conn.execute(
            "UPDATE files SET blocked = 1 WHERE lookup_key = ?", (row["lookup_key"],)
        )
    print(f"blocked {row['orig_name']} ({row['lookup_key'][:16]})")
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    with db.connect() as conn:
        row = _resolve(conn, args.ident)
        if row is None:
            print("no such file", file=sys.stderr)
            return 1
        conn.execute("DELETE FROM files WHERE lookup_key = ?", (row["lookup_key"],))
    (config.BLOB_DIR / row["blob_id"]).unlink(missing_ok=True)
    print(f"purged {row['orig_name']} ({row['lookup_key'][:16]})")
    return 0


def cmd_sweep(_: argparse.Namespace) -> int:
    print(sweeper.sweep_once())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="supload-admin")
    subs = parser.add_subparsers(dest="cmd", required=True)
    subs.add_parser("stats").set_defaults(fn=cmd_stats)
    subs.add_parser("reported").set_defaults(fn=cmd_reported)
    subs.add_parser("sweep").set_defaults(fn=cmd_sweep)
    for name, fn in (("block", cmd_block), ("purge", cmd_purge)):
        p = subs.add_parser(name)
        p.add_argument("ident", help="share URL, token, or lookup-key prefix")
        p.set_defaults(fn=fn)

    args = parser.parse_args()
    db.init()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
