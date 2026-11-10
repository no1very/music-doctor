"""Password hashing, token signing and permission helpers.

Passwords use PBKDF2-HMAC-SHA256 (stdlib). Session tokens are compact
HMAC-SHA256 signed strings so the app needs no JWT dependency, and every token
is additionally recorded server side so it can be revoked.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from .config import config

ROLES = ("admin", "editor", "listener")
ROLE_RANK = {"listener": 1, "editor": 2, "admin": 3}
PBKDF2_ROUNDS = 210_000


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), _unb64(salt), int(rounds)
        )
        return hmac.compare_digest(calc, _unb64(digest))
    except (ValueError, TypeError):
        return False


def subsonic_password(password: str) -> str:
    """Subsonic token auth needs md5(password + salt); store the md5 of the
    password only (never the plaintext) so the server can finish the sum."""
    return hashlib.md5(password.encode("utf-8")).hexdigest()


def issue_token(user_id: int, username: str, role: str) -> tuple[str, str]:
    """Return (token, token_sha256). The DB stores only the hash."""
    payload = {
        "uid": user_id,
        "u": username,
        "r": role,
        "exp": int(time.time()) + config.session_days * 86400,
        "jti": secrets.token_hex(8),
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64(hmac.new(config.secret_key, body.encode("ascii"), hashlib.sha256).digest())
    token = f"{body}.{sig}"
    return token, token_digest(token)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def decode_token(token: str) -> dict | None:
    try:
        body, sig = token.split(".", 1)
        expected = _b64(
            hmac.new(config.secret_key, body.encode("ascii"), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_unb64(body))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except (ValueError, TypeError, KeyError):
        return None


def can(role: str, needed: str) -> bool:
    return ROLE_RANK.get(role, 0) >= ROLE_RANK.get(needed, 99)
