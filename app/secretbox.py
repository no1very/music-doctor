"""Reversible password storage required by the Subsonic token handshake.

Subsonic clients authenticate with ``t = md5(password + salt)``, which a server
cannot verify from a one-way hash. Like other Subsonic servers we keep an
encrypted copy of the password (HMAC-SHA256 stream cipher + tag, key derived
from the instance secret). The database alone is not enough to recover it
without ``data/secret.key``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

from .config import config

MAGIC = b"OMT1"


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def _key() -> bytes:
    return hmac.new(config.secret_key, b"subsonic-password", hashlib.sha256).digest()


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    raw = plaintext.encode("utf-8")
    nonce = os.urandom(16)
    cipher = bytes(a ^ b for a, b in zip(raw, _keystream(_key(), nonce, len(raw))))
    tag = hmac.new(_key(), MAGIC + nonce + cipher, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(MAGIC + nonce + tag + cipher).decode("ascii")


def decrypt(blob: str) -> str:
    if not blob:
        return ""
    try:
        data = base64.urlsafe_b64decode(blob)
    except ValueError:
        return ""
    if len(data) < 4 + 16 + 16 or data[:4] != MAGIC:
        return ""
    nonce = data[4:20]
    tag = data[20:36]
    cipher = data[36:]
    expected = hmac.new(_key(), MAGIC + nonce + cipher, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(tag, expected):
        return ""
    return bytes(a ^ b for a, b in zip(cipher, _keystream(_key(), nonce, len(cipher)))).decode(
        "utf-8", errors="replace"
    )
