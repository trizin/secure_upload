"""Encryption invariants."""

import os
import secrets

import pytest

from app import config, crypto


def _roundtrip(tmp_path, payload: bytes) -> bytes:
    src, dst = tmp_path / "plain", tmp_path / "enc"
    src.write_bytes(payload)
    token = crypto.new_token()
    salt, prefix = secrets.token_bytes(16), secrets.token_bytes(4)
    key = crypto.derive_key(token, salt)
    crypto.encrypt_file(src, dst, key, prefix)
    return b"".join(crypto.decrypt_iter(dst, key, prefix))


@pytest.mark.parametrize("size", [0, 1, 64 * 1024 - 1, 64 * 1024, 64 * 1024 + 1, 300_000])
def test_roundtrip_across_chunk_boundaries(tmp_path, size):
    payload = os.urandom(size)
    assert _roundtrip(tmp_path, payload) == payload


def test_token_is_256_bits_of_entropy():
    tokens = {crypto.new_token() for _ in range(500)}
    assert len(tokens) == 500
    assert len(tokens.pop()) >= 43


def test_wrong_token_cannot_decrypt(tmp_path):
    src, dst = tmp_path / "plain", tmp_path / "enc"
    src.write_bytes(b"sensitive" * 100)
    salt, prefix = secrets.token_bytes(16), secrets.token_bytes(4)
    crypto.encrypt_file(src, dst, crypto.derive_key(crypto.new_token(), salt), prefix)

    other = crypto.derive_key(crypto.new_token(), salt)
    with pytest.raises(crypto.CorruptBlob):
        list(crypto.decrypt_iter(dst, other, prefix))


def test_tampering_is_detected(tmp_path):
    src, dst = tmp_path / "plain", tmp_path / "enc"
    src.write_bytes(b"A" * 5000)
    token = crypto.new_token()
    salt, prefix = secrets.token_bytes(16), secrets.token_bytes(4)
    key = crypto.derive_key(token, salt)
    crypto.encrypt_file(src, dst, key, prefix)

    blob = bytearray(dst.read_bytes())
    blob[len(blob) // 2] ^= 0x01
    dst.write_bytes(bytes(blob))

    with pytest.raises(crypto.CorruptBlob):
        list(crypto.decrypt_iter(dst, key, prefix))


def test_truncation_is_detected(tmp_path):
    """Chopping the tail off must fail loudly rather than silently returning a
    short file."""
    src, dst = tmp_path / "plain", tmp_path / "enc"
    src.write_bytes(os.urandom(200_000))
    token = crypto.new_token()
    salt, prefix = secrets.token_bytes(16), secrets.token_bytes(4)
    key = crypto.derive_key(token, salt)
    crypto.encrypt_file(src, dst, key, prefix)

    blob = dst.read_bytes()
    dst.write_bytes(blob[: len(blob) // 2])

    with pytest.raises(crypto.CorruptBlob):
        list(crypto.decrypt_iter(dst, key, prefix))


def test_same_token_different_salt_gives_different_key():
    token = crypto.new_token()
    a = crypto.derive_key(token, secrets.token_bytes(16))
    b = crypto.derive_key(token, secrets.token_bytes(16))
    assert a != b
