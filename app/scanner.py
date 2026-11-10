"""Library discovery: walk configured roots, index new/changed files, mark gaps."""

from __future__ import annotations

import os
import time
from pathlib import Path

from . import db, library
from .config import config
from .events import publish

SKIP_DIRS = {".@__thumb", "@eaDir", "#recycle", "$RECYCLE.BIN", "System Volume Information",
             ".git", "node_modules", ".venv", "__pycache__"}


def iter_audio(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name for name in dirnames
            if not name.startswith(".") and name not in SKIP_DIRS
        )
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in config.scan_exts:
                yield os.path.join(dirpath, name)


def scan_library(library_id: int, ctx=None, force: bool = False) -> dict:
    row = db.one("SELECT * FROM libraries WHERE id = ?", (library_id,))
    if not row:
        raise LookupError("library not found")
    if (row["kind"] if "kind" in row.keys() else "local") == "webdav":
        from . import webdav
        return webdav.scan(row, ctx)
    root = row["path"]
    if not os.path.isdir(root):
        raise FileNotFoundError(f"library path does not exist: {root}")

    counts = {"added": 0, "updated": 0, "unchanged": 0, "failed": 0, "missing": 0}
    seen: set[str] = set()
    total = None
    if ctx:
        total = sum(1 for _ in iter_audio(root))
        ctx.log(f"scanning {root} ({total} audio files)")
        ctx.progress(0, total)
    start = time.time()
    for index, path in enumerate(iter_audio(root), start=1):
        if ctx:
            ctx.check_cancel()
        seen.add(path)
        rel = os.path.relpath(path, root)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        existing = db.one("SELECT id, size, mtime FROM tracks WHERE path = ?", (path,))
        if existing and not force and existing["size"] == stat.st_size and abs(
            float(existing["mtime"]) - stat.st_mtime
        ) < 1e-6:
            counts["unchanged"] += 1
        else:
            try:
                track_id, action = library.index_file(library_id, path, rel)
                if track_id is None:
                    counts["failed"] += 1
                else:
                    counts[action] = counts.get(action, 0) + 1
                    if ctx and action == "added":
                        ctx.log(f"+ {rel}")
            except Exception as exc:  # noqa: BLE001 - one bad file must not stop a scan
                counts["failed"] += 1
                if ctx:
                    ctx.log(f"! {rel}: {exc}", "warn")
        if ctx:
            ctx.progress(index, total)
            if index % 250 == 0:
                publish("library.progress", library_id=library_id, done=index, total=total)

    missing_ids = [
        r["id"] for r in db.query(
            "SELECT id, path FROM tracks WHERE library_id = ? AND missing = 0", (library_id,)
        ) if r["path"] not in seen
    ]
    for track_id in missing_ids:
        db.execute("UPDATE tracks SET missing = 1 WHERE id = ?", (track_id,))
    counts["missing"] = len(missing_ids)
    if ctx and missing_ids:
        ctx.log(f"{len(missing_ids)} files no longer present (kept as missing records)")

    db.execute("UPDATE libraries SET last_scan_at = datetime('now') WHERE id = ?", (library_id,))
    result = {**counts, "seconds": round(time.time() - start, 1), "total": total or 0}
    if ctx:
        ctx.log(
            "scan finished: {added} added, {updated} updated, {unchanged} unchanged, "
            "{failed} failed, {missing} missing".format(**result)
        )
        publish("library.scanned", library_id=library_id)
    return result


def add_library(name: str, path: str, auto_scan: bool = True, kind: str = "local",
                url: str = "", username: str = "", password_cipher: str = "") -> dict:
    if kind == "webdav":
        remote = (url or path).strip()
        if not remote.startswith(("http://", "https://")):
            raise ValueError("WebDAV library needs an http(s) URL")
        remote = remote.rstrip("/") + "/"
        cur = db.execute(
            "INSERT INTO libraries(name, path, kind, url, username, password_cipher, auto_scan)"
            " VALUES(?, ?, 'webdav', ?, ?, ?, ?)",
            (name.strip() or "Remote library", remote, remote, username, password_cipher,
             1 if auto_scan else 0),
        )
        return dict(db.one("SELECT * FROM libraries WHERE id = ?", (cur.lastrowid,)))
    resolved = str(Path(path).expanduser().resolve())
    if not os.path.isdir(resolved):
        raise FileNotFoundError(f"directory not found: {resolved}")
    cur = db.execute(
        "INSERT INTO libraries(name, path, kind, auto_scan) VALUES(?, ?, 'local', ?)",
        (name.strip() or os.path.basename(resolved), resolved, 1 if auto_scan else 0),
    )
    return dict(db.one("SELECT * FROM libraries WHERE id = ?", (cur.lastrowid,)))
