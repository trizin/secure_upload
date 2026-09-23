"""Per-IP upload limits for a public, anonymous service.

Client IPs are hashed with a persistent server secret before they are stored,
so the database holds no plaintext addresses.
"""

from __future__ import annotations

import hmac
import hashlib
import ipaddress
import time

from . import config, db


def _trusted_networks():
    """Peers allowed to set X-Forwarded-For. Caddy appends the peer it actually
    saw, so the LAST entry is the real client and cannot be spoofed by
    prepending values - but only if the request genuinely came from Caddy,
    which is what this checks."""
    raw = config.TRUSTED_PROXIES.strip()
    if raw == "*":
        return "*"
    networks = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    return networks


def _is_trusted(peer: str) -> bool:
    networks = _trusted_networks()
    if networks == "*":
        return True
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(address in net for net in networks)


class RateLimited(Exception):
    pass


def client_ip(request) -> str:
    peer = request.client.host if request.client else "unknown"
    if _is_trusted(peer):
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
