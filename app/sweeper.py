"""Expiry sweeper.

This is the second of two layers. The first is db.get_file(), which refuses to
return anything past its expiry, so files stop being served the moment they
expire whether or not this ever runs. The sweeper is what actually reclaims
the bytes, and reconciles blobs orphaned by a crash between the file write and
the database commit.
"""

from __future__ import annotations

import asyncio
import logging
import time

from . import config, db

log = logging.getLogger("supload.sweeper")

SWEEP_INTERVAL = 300
ORPHAN_GRACE = 3600


def sweep_once() -> dict[str, int]:
    stats = {"expired": 0, "orphans": 0, "rate_rows": 0}

    for row in db.expired_rows():
        blob = config.BLOB_DIR / row["blob_id"]
        try:
            blob.unlink(missing_ok=True)
        except OSError:
            log.warning("could not unlink blob for expired file")
            continue
        db.delete_row(row["lookup_key"])
        stats["expired"] += 1

    live = db.live_blob_ids()
    cutoff = time.time() - ORPHAN_GRACE
    for path in config.BLOB_DIR.iterdir():
        if not path.is_file() or path.name in live:
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                stats["orphans"] += 1
        except OSError:
            continue

    with db.connect() as conn:
        cur = conn.execute("DELETE FROM rate WHERE ts < ?", (int(time.time()) - 172800,))
        stats["rate_rows"] = cur.rowcount

    return stats


async def run_forever() -> None:
    while True:
        try:
            stats = sweep_once()
            if any(stats.values()):
                log.info("sweep: %s", stats)
        except Exception:
            log.exception("sweep failed")
        await asyncio.sleep(SWEEP_INTERVAL)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db.init()
    print(sweep_once())
