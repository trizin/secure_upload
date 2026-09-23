"""Token handling and chunked AES-256-GCM file encryption.

The link token is the only key material. The database stores sha256(token), so
a database leak alone does not unlock anything: the file key is derived from
the token, which exists only inside live URLs.

On-disk format is a sequence of frames:

    [4-byte big-endian ciphertext length][ciphertext || 16-byte GCM tag]

Each frame's nonce is nonce_prefix(4) || counter(8), and each frame is
authenticated with AAD = counter || is_final. Binding the index prevents frames
being reordered or swapped between files; binding is_final means a truncated
file fails to authenticate instead of silently decrypting short.
"""

from __future__ import annotations

import hashlib
import secrets
import struct
from pathlib import Path
from typing import Iterator

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import config

_INFO = b"supload-v1-file-encryption"


class CorruptBlob(Exception):
    """Raised when stored ciphertext fails authentication."""


def new_token() -> str:
    return secrets.token_urlsafe(config.TOKEN_BYTES)


def lookup_key(token: str) -> str:
    """What the database stores in place of the token itself."""
    return hashlib.sha256(token.encode()).hexdigest()


def derive_key(token: str, salt: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=salt, info=_INFO
    ).derive(token.encode())


def _nonce(prefix: bytes, counter: int) -> bytes:
    return prefix + struct.pack(">Q", counter)


def _aad(counter: int, final: bool) -> bytes:
    return struct.pack(">Q?", counter, final)


def encrypt_file(src: Path, dst: Path, key: bytes, nonce_prefix: bytes) -> None:
    aes = AESGCM(key)
    counter = 0
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        pending = fi.read(config.CHUNK_SIZE)
        while True:
            nxt = fi.read(config.CHUNK_SIZE)
            final = not nxt
            ct = aes.encrypt(_nonce(nonce_prefix, counter), pending, _aad(counter, final))
            fo.write(struct.pack(">I", len(ct)))
            fo.write(ct)
            counter += 1
            if final:
                break
            pending = nxt


def decrypt_iter(path: Path, key: bytes, nonce_prefix: bytes) -> Iterator[bytes]:
    """Stream plaintext back. Never buffers the whole file."""
    aes = AESGCM(key)
    counter = 0
    with open(path, "rb") as f:
        while True:
            header = f.read(4)
            if len(header) != 4:
                raise CorruptBlob("truncated before final frame")
            (n,) = struct.unpack(">I", header)
            if n > config.CHUNK_SIZE + 64:
                raise CorruptBlob("implausible frame length")
            ct = f.read(n)
            if len(ct) != n:
                raise CorruptBlob("truncated frame")
            nonce = _nonce(nonce_prefix, counter)
            try:
                plain = aes.decrypt(nonce, ct, _aad(counter, False))
                final = False
            except InvalidTag:
                try:
                    plain = aes.decrypt(nonce, ct, _aad(counter, True))
                    final = True
                except InvalidTag as exc:
                    raise CorruptBlob("authentication failed") from exc
            yield plain
            counter += 1
            if final:
                return
