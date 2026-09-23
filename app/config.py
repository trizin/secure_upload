"""Configuration. Everything here is overridable via SUPLOAD_* env vars."""

import os
import secrets
from pathlib import Path


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


DATA_DIR = Path(os.environ.get("SUPLOAD_DATA", "/root/vault/data"))
BLOB_DIR = DATA_DIR / "blobs"
TMP_DIR = DATA_DIR / "tmp"
DB_PATH = DATA_DIR / "vault.db"
SECRET_PATH = DATA_DIR / "secret.key"

# Raw bytes are served from a throwaway origin, so a stored payload can never
# execute against the origin that hosts the UI.
APP_HOST = os.environ.get("SUPLOAD_APP_HOST", "supload.org")
FILE_HOST = os.environ.get("SUPLOAD_FILE_HOST", "files.supload.org")
ENFORCE_HOSTS = os.environ.get("SUPLOAD_ENFORCE_HOSTS", "1") == "1"
PUBLIC_SCHEME = os.environ.get("SUPLOAD_SCHEME", "https")

MAX_FILE_SIZE = _int("SUPLOAD_MAX_FILE_SIZE", 100 * 1024 * 1024)
TOTAL_QUOTA = _int("SUPLOAD_TOTAL_QUOTA", 20 * 1024 * 1024 * 1024)
RETENTION_SECONDS = _int("SUPLOAD_RETENTION", 48 * 3600)

# A 400KB PNG can decode to 1.6GB of pixels. On a 967MB box that is the fastest
# route to the OOM killer, so pixels are capped independently of file size.
MAX_IMAGE_PIXELS = _int("SUPLOAD_MAX_IMAGE_PIXELS", 50_000_000)

# Which peers are allowed to set X-Forwarded-For. Behind systemd the proxy is
# on loopback, but in a container it arrives from the bridge network, so this
# has to be configurable - otherwise every request looks like it came from the
# proxy and per-IP rate limiting silently becomes one global bucket.
# Comma-separated IPs or CIDRs. "*" trusts any peer, which is only safe when
# the app port is unreachable except through the proxy.
TRUSTED_PROXIES = os.environ.get("SUPLOAD_TRUSTED_PROXIES", "127.0.0.1/32,::1/128")

RATE_UPLOADS_PER_HOUR = _int("SUPLOAD_RATE_UPLOADS", 10)
RATE_BYTES_PER_DAY = _int("SUPLOAD_RATE_BYTES", 500 * 1024 * 1024)

SANITIZE_CONCURRENCY = _int("SUPLOAD_SANITIZE_CONCURRENCY", 1)
SANITIZE_TIMEOUT = _int("SUPLOAD_SANITIZE_TIMEOUT", 120)
SANITIZE_MEM_LIMIT = _int("SUPLOAD_SANITIZE_MEM", 600 * 1024 * 1024)

CHUNK_SIZE = 64 * 1024
TOKEN_BYTES = 32  # 256 bits

ABUSE_EMAIL = os.environ.get("SUPLOAD_ABUSE_EMAIL", "abuse@supload.org")


def server_secret() -> bytes:
    """Persistent secret used to hash client IPs before they reach the DB."""
    if SECRET_PATH.exists():
        return SECRET_PATH.read_bytes()
    SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    s = secrets.token_bytes(32)
    SECRET_PATH.write_bytes(s)
    SECRET_PATH.chmod(0o600)
    return s


def ensure_dirs() -> None:
    for d in (DATA_DIR, BLOB_DIR, TMP_DIR):
        d.mkdir(parents=True, exist_ok=True)
