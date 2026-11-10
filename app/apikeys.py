"""Personal API keys for scripts, MCP agents and home automation.

Keys are shown once, stored as SHA-256 digests, and carry the permissions of the
user that created them. They are accepted wherever a session token is accepted
(``Authorization: Bearer omt_...`` or ``X-API-Key``).
"""

from __future__ import annotations

import hashlib
import secrets

from . import db

PREFIX = "omt_"


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create(user_id: int, name: str) -> tuple[dict, str]:
    raw = PREFIX + secrets.token_hex(24)
    prefix = raw[: len(PREFIX) + 6]
    cur = db.execute(
        "INSERT INTO api_keys(user_id, name, prefix, key_hash) VALUES(?, ?, ?, ?)",
        (user_id, (name or "api key")[:80], prefix, _digest(raw)),
    )
    row = db.one("SELECT * FROM api_keys WHERE id = ?", (cur.lastrowid,))
    return dict(row), raw


def list_for(user_id: int) -> list[dict]:
    rows = db.query(
        "SELECT id, name, prefix, created_at, last_used_at FROM api_keys"
        " WHERE user_id = ? ORDER BY id DESC",
        (user_id,),
    )
    return [dict(row) for row in rows]


def revoke(user_id: int, key_id: int) -> bool:
    cur = db.execute("DELETE FROM api_keys WHERE id = ? AND user_id = ?", (key_id, user_id))
    return cur.rowcount > 0


def resolve(raw: str | None) -> dict | None:
    if not raw or not raw.startswith(PREFIX):
        return None
    row = db.one(
        "SELECT k.id AS key_id, k.user_id, u.username, u.display_name, u.role, u.active"
        " FROM api_keys k JOIN users u ON u.id = k.user_id WHERE k.key_hash = ?",
        (_digest(raw),),
    )
    if not row or not row["active"]:
        return None
    db.execute("UPDATE api_keys SET last_used_at = datetime('now') WHERE id = ?", (row["key_id"],))
    return {
        "id": row["user_id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
    }
