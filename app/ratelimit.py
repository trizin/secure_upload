"""Per-IP upload limits for a public, anonymous service.

Client IPs are hashed with a persistent server secret before they are stored,
so the database holds no plaintext addresses.
"""

from __future__ import annotations

import hmac
import hashlib
import time

from . import config, db

# Only loopback is trusted to have set X-Forwarded-For, because only the local
# reverse proxy can reach the app. Caddy appends the peer it actually saw, so
# the LAST entry is the real client and cannot be spoofed by prepending values.
_TRUSTED_PEERS = {"127.0.0.1", "::1"}


class RateLimited(Exception):
    pass


def client_ip(request) -> str:
    peer = request.client.host if request.client else "unknown"
    if peer in _TRUSTED_PEERS:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return peer


def hash_ip(ip: str) -> str:
    return hmac.new(config.server_secret(), ip.encode(), hashlib.sha256).hexdigest()


def check(ip_hash: str, incoming_bytes: int) -> None:
    now = int(time.time())
    with db.connect() as conn:
        uploads = conn.execute(
            "SELECT COUNT(*) AS n FROM rate WHERE ip_hash = ? AND ts > ?",
            (ip_hash, now - 3600),
        ).fetchone()["n"]
        if uploads >= config.RATE_UPLOADS_PER_HOUR:
            raise RateLimited(
                f"Upload limit reached ({config.RATE_UPLOADS_PER_HOUR} per hour). "
                "Try again later."
            )

        sent = conn.execute(
            "SELECT COALESCE(SUM(bytes), 0) AS b FROM rate WHERE ip_hash = ? AND ts > ?",
            (ip_hash, now - 86400),
        ).fetchone()["b"]
        if sent + incoming_bytes > config.RATE_BYTES_PER_DAY:
            raise RateLimited(
                f"Daily transfer limit reached "
                f"({config.RATE_BYTES_PER_DAY // (1024 * 1024)} MB per day)."
            )


def record(ip_hash: str, size: int) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO rate (ip_hash, ts, bytes) VALUES (?, ?, ?)",
            (ip_hash, int(time.time()), size),
        )
