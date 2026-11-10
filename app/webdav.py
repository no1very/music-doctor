"""WebDAV / AList remote libraries.

Remote music is indexed by PROPFIND and streamed through the server so that the
web player and Subsonic clients work with cloud files. Tag writing is not
possible for remote objects, so those tracks are read-only; scraping still
updates the local index and can write sidecar files into a mounted cache only
when the library is local.
"""

from __future__ import annotations

import base64
import calendar
import os
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from . import db
from .config import config
from .secretbox import decrypt

NS = {"d": "DAV:"}
HTTP_DATE_RE = re.compile(r"^[A-Za-z]{3},\s+\d{2}\s+[A-Za-z]{3}\s+\d{4}\s+\d{2}:\d{2}:\d{2}\s+GMT$")


class WebDAVError(RuntimeError):
    pass


def library_headers(row) -> dict[str, str]:
    headers = {"User-Agent": config.user_agent, "Accept": "*/*"}
    username = row["username"] if "username" in row.keys() else ""
    cipher = row["password_cipher"] if "password_cipher" in row.keys() else ""
    if username:
        password = decrypt(cipher or "")
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    return headers


def base_url(row) -> str:
    return (row["url"] or row["path"] or "").rstrip("/") + "/"


def _request(url: str, headers: dict, *, method: str = "GET", depth: str | None = None,
             body: bytes | None = None, timeout: int | None = None):
    request = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    if depth is not None:
        request.add_header("Depth", depth)
    if method == "PROPFIND":
        request.add_header("Content-Type", "application/xml; charset=utf-8")
    return urllib.request.urlopen(request, timeout=timeout or config.http_timeout)


def propfind(url: str, headers: dict, depth: str = "1") -> bytes:
    body = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<d:propfind xmlns:d="DAV:"><d:prop>'
        b"<d:resourcetype/><d:getcontentlength/><d:getlastmodified/>"
        b"<d:displayname/><d:getcontenttype/>"
        b"</d:prop></d:propfind>"
    )
    try:
        with _request(url, headers, method="PROPFIND", depth=depth, body=body) as response:
            return response.read(8 << 20)
    except urllib.error.HTTPError as exc:
        raise WebDAVError(f"PROPFIND {url} failed: HTTP {exc.code}") from exc
    except OSError as exc:
        raise WebDAVError(f"PROPFIND {url} failed: {exc}") from exc


def _href_to_url(base: str, href: str) -> str:
    href = href.strip().replace(" ", "%20")
    absolute = urllib.parse.urljoin(base, href)
    if href.endswith("/") and not absolute.endswith("/"):
        absolute += "/"
    return absolute


def parse_multistatus(xml_bytes: bytes, base: str, skip_self: bool = True) -> list[dict]:
    """Parse a WebDAV multistatus body into path entries."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise WebDAVError(f"invalid WebDAV response: {exc}") from exc
    entries: list[dict] = []
    base_norm = base.rstrip("/")
    for response in root.findall("d:response", NS):
        href = response.findtext("d:href", default="", namespaces=NS)
        if not href:
            continue
        url = _href_to_url(base, href)
        prop = None
        for propstat in response.findall("d:propstat", NS):
            status = propstat.findtext("d:status", default="", namespaces=NS)
            candidate = propstat.find("d:prop", NS)
            if candidate is not None and (" 200 " in status or prop is None):
                prop = candidate
                if " 200 " in status:
                    break
        if prop is None:
            continue
        collection = prop.find("d:resourcetype/d:collection", NS) is not None
        size_text = prop.findtext("d:getcontentlength", default="", namespaces=NS)
        modified = prop.findtext("d:getlastmodified", default="", namespaces=NS)
        name = prop.findtext("d:displayname", default="", namespaces=NS)
        if not name:
            name = urllib.parse.unquote(url.rstrip("/").rsplit("/", 1)[-1])
        if skip_self and url.rstrip("/") == base_norm:
            continue
        mtime = 0.0
        if modified and HTTP_DATE_RE.match(modified.strip()):
            try:
                mtime = calendar.timegm(time.strptime(modified.strip(), "%a, %d %b %Y %H:%M:%S GMT"))
            except ValueError:
                mtime = 0.0
        entries.append({
            "url": url,
            "name": name,
            "is_dir": collection,
            "size": int(size_text) if size_text.isdigit() else 0,
            "mtime": mtime,
            "modified": modified,
        })
    return entries


AUDIO_EXTS = {
    ".mp3", ".flac", ".m4a", ".mp4", ".ogg", ".oga", ".opus", ".wav", ".aiff", ".aif",
    ".ape", ".wv", ".dsf",
}


def iter_files(base: str, headers: dict, ctx=None, max_dirs: int = 2000) -> list[dict]:
    """Breadth-first walk of the WebDAV tree, yielding audio files."""
    files: list[dict] = []
    queue = [base]
    visited = 0
    while queue and visited < max_dirs:
        directory = queue.pop(0)
        visited += 1
        if ctx:
            ctx.check_cancel()
        try:
            entries = parse_multistatus(propfind(directory, headers, "1"), directory)
        except WebDAVError as exc:
            if ctx:
                ctx.log(f"! {exc}", "warn")
            continue
        for entry in entries:
            if entry["is_dir"]:
                if not entry["name"].startswith("."):
                    queue.append(entry["url"] if entry["url"].endswith("/") else entry["url"] + "/")
            elif os.path.splitext(entry["name"])[1].lower() in AUDIO_EXTS:
                files.append(entry)
        if ctx and visited % 20 == 0:
            ctx.log(f"… {visited} directories, {len(files)} audio files")
    return files


def _guess_meta(rel_path: str) -> dict:
    parts = [part for part in rel_path.split("/") if part]
    filename = parts[-1] if parts else rel_path
    stem = os.path.splitext(filename)[0]
    stem = re.sub(r"^\s*\d{1,3}[\s._\-]+", "", stem)
    artist = ""
    album = ""
    if len(parts) >= 3:
        artist, album = parts[-3], parts[-2]
    elif len(parts) == 2:
        album = parts[0]
    title = stem
    for separator in (" - ", " – ", "-"):
        if separator in stem:
            left, right = stem.split(separator, 1)
            if left.strip() and right.strip():
                artist, title = left.strip(), right.strip()
                break
    return {"title": title, "artist": artist, "album": album}


def scan(library_row, ctx=None) -> dict:
    library_id = library_row["id"]
    base = base_url(library_row)
    if not base.startswith(("http://", "https://")):
        raise WebDAVError("remote library needs an http(s) URL")
    headers = library_headers(library_row)
    ctx and ctx.log(f"webdav scan: {base}")
    files = iter_files(base, headers, ctx)
    total = len(files)
    if ctx:
        ctx.progress(0, total)
    counts = {"added": 0, "updated": 0, "unchanged": 0, "failed": 0, "missing": 0}
    seen: set[str] = set()
    base_path = urllib.parse.urlsplit(base).path
    for index, entry in enumerate(files, start=1):
        if ctx:
            ctx.check_cancel()
        url = entry["url"]
        seen.add(url)
        rel = urllib.parse.unquote(urllib.parse.urlsplit(url).path)
        if rel.startswith(base_path):
            rel = rel[len(base_path):]
        rel = rel.lstrip("/")
        meta = _guess_meta(rel)
        ext = os.path.splitext(entry["name"])[1].lower()
        album_id, artist_id = db.album_ids(meta["album"], meta["artist"], meta["artist"])
        existing = db.one("SELECT id, size FROM tracks WHERE path = ?", (url,))
        tagged = 1 if (meta["artist"] and meta["album"]) else 0
        data = {
            "library_id": library_id,
            "path": url,
            "rel_path": rel,
            "ext": ext,
            "size": entry["size"],
            "mtime": entry["mtime"],
            "title": meta["title"],
            "artist": meta["artist"],
            "albumartist": meta["artist"],
            "album": meta["album"],
            "codec": ext.lstrip(".").upper(),
            "album_id": album_id,
            "artist_id": artist_id,
            "tagged": tagged,
        }
        columns = ", ".join(data)
        placeholders = ", ".join("?" for _ in data)
        updates = ", ".join(f"{key} = excluded.{key}" for key in data if key != "path")
        db.execute(
            f"INSERT INTO tracks({columns}, added_at, updated_at, missing)"
            f" VALUES({placeholders}, datetime('now'), datetime('now'), 0)"
            f" ON CONFLICT(path) DO UPDATE SET {updates}, missing = 0, updated_at = datetime('now')",
            tuple(data.values()),
        )
        if existing:
            counts["unchanged" if existing["size"] == entry["size"] else "updated"] += 1
        else:
            counts["added"] += 1
        if ctx:
            ctx.progress(index, total)
    missing = [
        row["id"] for row in db.query(
            "SELECT id, path FROM tracks WHERE library_id = ? AND missing = 0", (library_id,)
        ) if row["path"] not in seen
    ]
    for track_id in missing:
        db.execute("UPDATE tracks SET missing = 1 WHERE id = ?", (track_id,))
    counts["missing"] = len(missing)
    db.execute("UPDATE libraries SET last_scan_at = datetime('now') WHERE id = ?", (library_id,))
    result = {**counts, "total": total}
    ctx and ctx.log(
        "webdav scan finished: {added} added, {updated} updated, {unchanged} unchanged, "
        "{missing} missing".format(**counts)
    )
    return result


def proxy_headers(library_row, range_header: str | None = None) -> dict[str, str]:
    headers = library_headers(library_row)
    if range_header:
        headers["Range"] = range_header
    return headers


def open_stream(library_row, url: str, range_header: str | None = None):
    headers = proxy_headers(library_row, range_header)
    return _request(url, headers, method="GET")
