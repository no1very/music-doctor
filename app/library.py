"""Library model: track serialisation, indexing, editing and smart queries."""

from __future__ import annotations

import hashlib
import json
import os
import re

from . import db, tags, variables
from .config import config
from .events import publish

TEXT_FIELDS = (
    "title", "artist", "albumartist", "album", "genre", "composer", "lyricist",
    "comment", "lyrics", "isrc", "mb_album_id", "mb_track_id",
)
INT_FIELDS = ("track_no", "disc_no", "year", "bpm", "rating")
EDITABLE = tuple(TEXT_FIELDS) + tuple(INT_FIELDS)
SORTS = {
    "title": "t.title COLLATE NOCASE",
    "artist": "t.artist COLLATE NOCASE",
    "album": "t.album COLLATE NOCASE",
    "added": "t.added_at",
    "year": "t.year",
    "duration": "t.duration",
    "plays": "t.play_count",
    "track": "t.track_no",
    "random": "RANDOM()",
}


def _as_int(value, minimum: int | None = 0):
    if value is None or value == "":
        return None
    try:
        number = int(str(value).strip().split("/")[0])
    except (TypeError, ValueError):
        return None
    if minimum is not None and number < minimum:
        return None
    return number


def clean_fields(raw: dict) -> dict:
    out: dict = {}
    for key, value in raw.items():
        if key in TEXT_FIELDS:
            out[key] = "" if value is None else str(value).strip()
        elif key in INT_FIELDS:
            out[key] = _as_int(value)
    return out


def cover_url(cover_hash: str) -> str:
    return f"/api/covers/{cover_hash}" if cover_hash else ""


def is_remote(path: str) -> bool:
    return str(path).startswith(("http://", "https://"))


def lrc_path(audio_path: str) -> str:
    return os.path.splitext(audio_path)[0] + ".lrc"


def read_sidecar_lyrics(audio_path: str) -> str:
    if is_remote(audio_path):
        return ""
    try:
        with open(lrc_path(audio_path), "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return ""


SIDECAR_COVER_NAMES = ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.png", "front.jpg")


def read_sidecar_cover(audio_path: str) -> tuple[str, bytes] | None:
    if is_remote(audio_path):
        return None
    directory = os.path.dirname(audio_path)
    for name in SIDECAR_COVER_NAMES:
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate):
            try:
                with open(candidate, "rb") as fh:
                    data = fh.read(8 << 20)
            except OSError:
                continue
            mime = "image/png" if candidate.lower().endswith(".png") else "image/jpeg"
            return mime, data
    return None


def _write_sidecars(audio_path: str, fields: dict, cover: tuple[str, bytes] | None) -> None:
    """External .lrc and cover.jpg files, mirroring the documented V2 option."""
    if is_remote(audio_path):
        return
    if db.get_setting("scrape.write_lrc", "1") == "1":
        lyrics = str(fields.get("lyrics") or "").strip()
        target = lrc_path(audio_path)
        try:
            if lyrics:
                with open(target, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(lyrics + "\n")
            elif os.path.exists(target):
                os.remove(target)
        except OSError:
            pass
    if db.get_setting("scrape.write_cover_file", "0") == "1" and cover and cover[1]:
        extension = ".png" if cover[0] == "image/png" else ".jpg"
        target = os.path.join(os.path.dirname(audio_path), "cover" + extension)
        try:
            with open(target, "wb") as fh:
                fh.write(cover[1])
        except OSError:
            pass


def track_dict(row, include_lyrics: bool = False) -> dict:
    data = {key: row[key] for key in row.keys()}
    data["stream_url"] = f"/api/tracks/{row['id']}/stream"
    data["cover_url"] = cover_url(row["cover_hash"])
    data["writable"] = tags.is_writable(row["path"], row["codec"]) and not is_remote(row["path"])
    data["remote"] = is_remote(row["path"])
    data["has_lyrics"] = bool(row["lyrics"])
    if not include_lyrics:
        data.pop("lyrics", None)
    return data


def get_track(track_id: int, include_lyrics: bool = False) -> dict | None:
    row = db.one("SELECT * FROM tracks WHERE id = ?", (track_id,))
    return track_dict(row, include_lyrics) if row else None


def store_cover(data: bytes, mime: str) -> str:
    digest = hashlib.sha1(data).hexdigest()
    db.execute(
        "INSERT OR IGNORE INTO covers(hash, mime, data, size) VALUES(?, ?, ?, ?)",
        (digest, mime or "image/jpeg", data, len(data)),
    )
    return digest


def load_cover(cover_hash: str) -> tuple[str, bytes] | None:
    row = db.one("SELECT mime, data FROM covers WHERE hash = ?", (cover_hash,))
    if not row:
        return None
    return row["mime"], bytes(row["data"])


def quick_hash(path: str) -> str:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            head = fh.read(262144)
        return hashlib.sha1(head + str(size).encode()).hexdigest()
    except OSError:
        return ""


def index_file(library_id: int, path: str, rel_path: str = "") -> tuple[int | None, str]:
    """Read one file and upsert it. Returns (track_id, action)."""
    try:
        stat = os.stat(path)
    except OSError:
        return None, "missing"
    info = tags.read(path)
    cover_hash = ""
    cover = info.pop("cover", None)
    if not cover:
        cover = read_sidecar_cover(path)
    if cover and cover[1]:
        cover_hash = store_cover(cover[1], cover[0])
    if not info.get("lyrics") and not is_remote(path):
        sidecar_lyrics = read_sidecar_lyrics(path)
        if sidecar_lyrics:
            info["lyrics"] = sidecar_lyrics
    album_id, artist_id = db.album_ids(
        info.get("album", ""), info.get("albumartist", ""), info.get("artist", "")
    )
    values = {
        "library_id": library_id,
        "path": path,
        "rel_path": rel_path,
        "ext": info.get("ext") or os.path.splitext(path)[1].lower(),
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "title": info.get("title") or os.path.splitext(os.path.basename(path))[0],
        "artist": info.get("artist") or "",
        "albumartist": info.get("albumartist") or "",
        "album": info.get("album") or "",
        "track_no": info.get("track_no"),
        "disc_no": info.get("disc_no"),
        "year": info.get("year"),
        "genre": info.get("genre") or "",
        "composer": info.get("composer") or "",
        "lyricist": info.get("lyricist") or "",
        "comment": info.get("comment") or "",
        "lyrics": info.get("lyrics") or "",
        "duration": float(info.get("duration") or 0),
        "bitrate": int(info.get("bitrate") or 0),
        "sample_rate": int(info.get("sample_rate") or 0),
        "channels": int(info.get("channels") or 0),
        "bit_depth": int(info.get("bit_depth") or 0),
        "codec": info.get("codec") or "",
        "quick_hash": quick_hash(path),
        "mb_album_id": info.get("mb_album_id") or "",
        "mb_track_id": info.get("mb_track_id") or "",
        "isrc": info.get("isrc") or "",
        "bpm": info.get("bpm"),
        "cover_hash": cover_hash,
        "album_id": album_id,
        "artist_id": artist_id,
        "tagged": 1 if (info.get("title") and (info.get("artist") or info.get("album"))) else 0,
    }
    exists = db.one("SELECT id, cover_hash, mtime, size FROM tracks WHERE path = ?", (path,))
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    updates = ", ".join(f"{key} = excluded.{key}" for key in values if key not in ("path",))
    db.execute(
        f"INSERT INTO tracks({columns}, added_at, updated_at, missing) "
        f"VALUES({placeholders}, datetime('now'), datetime('now'), 0) "
        f"ON CONFLICT(path) DO UPDATE SET {updates}, missing = 0,"
        f" updated_at = datetime('now')",
        tuple(values.values()),
    )
    row = db.one("SELECT id FROM tracks WHERE path = ?", (path,))
    track_id = int(row["id"]) if row else None
    if exists:
        changed = exists["mtime"] != stat.st_mtime or exists["size"] != stat.st_size
        return track_id, "updated" if changed else "unchanged"
    return track_id, "added"


def _snapshot(track: dict) -> dict:
    return {key: track.get(key) for key in TEXT_FIELDS + INT_FIELDS}


def update_track(
    track_id: int,
    fields: dict,
    user_id: int | None = None,
    action: str = "edit",
    write_file: bool = True,
    cover: tuple[str, bytes] | None = None,
    remove_cover: bool = False,
    source: str = "",
) -> dict:
    row = db.one("SELECT * FROM tracks WHERE id = ?", (track_id,))
    if not row:
        raise LookupError("track not found")
    before = get_track(track_id, include_lyrics=True)
    cleaned = clean_fields(fields)

    payload = dict(before)
    payload.update(cleaned)
    payload["album_id"], payload["artist_id"] = db.album_ids(
        payload.get("album", ""), payload.get("albumartist", ""), payload.get("artist", "")
    )
    if record_cover := cover:
        payload["cover_hash"] = store_cover(record_cover[1], record_cover[0])
    elif remove_cover:
        payload["cover_hash"] = ""

    if write_file and not config.read_only and not is_remote(before["path"]):
        if not tags.is_writable(before["path"], before.get("codec")):
            raise RuntimeError(
                f"{before.get('ext') or 'this format'} does not support tag writing; "
                "index-only edit was refused so the library stays in sync with the files"
            )
        merged = {key: payload.get(key) for key in TEXT_FIELDS + INT_FIELDS}
        existing = load_cover(payload["cover_hash"]) if payload.get("cover_hash") else None
        merged["cover"] = existing
        try:
            tags.write(before["path"], merged)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"cannot write tags: {exc}") from exc
        if db.get_setting("scrape.write_lrc", "1") == "1" or db.get_setting(
            "scrape.write_cover_file", "0"
        ) == "1":
            _write_sidecars(before["path"], merged, existing)

    columns = list(EDITABLE) + ["album_id", "artist_id", "cover_hash", "updated_at"]
    values = [payload.get(key) for key in EDITABLE]
    values += [payload["album_id"], payload["artist_id"], payload.get("cover_hash") or ""]
    db.execute(
        f"UPDATE tracks SET {', '.join(f'{name} = ?' for name in columns[:-1])},"
        f" updated_at = datetime('now') WHERE id = ?",
        (*values, track_id),
    )
    after = get_track(track_id, include_lyrics=True)
    db.execute(
        "INSERT INTO meta_versions(track_id, user_id, action, source, before_json, after_json)"
        " VALUES(?, ?, ?, ?, ?, ?)",
        (track_id, user_id, action, source, json.dumps(_snapshot(before), ensure_ascii=False),
         json.dumps(_snapshot(after), ensure_ascii=False)),
    )
    publish("track.updated", track_id=track_id)
    return after


def versions(track_id: int) -> list[dict]:
    rows = db.query(
        "SELECT v.*, u.username FROM meta_versions v LEFT JOIN users u ON u.id = v.user_id"
        " WHERE v.track_id = ? ORDER BY v.id DESC",
        (track_id,),
    )
    out = []
    for row in rows:
        item = dict(row)
        item["before"] = json.loads(item.pop("before_json") or "{}")
        item["after"] = json.loads(item.pop("after_json") or "{}")
        out.append(item)
    return out


def restore_version(version_id: int, user_id: int | None = None) -> dict:
    row = db.one("SELECT * FROM meta_versions WHERE id = ?", (version_id,))
    if not row:
        raise LookupError("version not found")
    snapshot = json.loads(row["before_json"] or "{}")
    return update_track(
        row["track_id"], {k: v for k, v in snapshot.items() if k in EDITABLE},
        user_id=user_id, action="rollback", write_file=True,
    )


# --------------------------------------------------------------------------- #
# querying
# --------------------------------------------------------------------------- #
def _where(params: dict) -> tuple[str, list]:
    clauses = ["1=1"]
    values: list = []
    if params.get("library_id"):
        clauses.append("t.library_id = ?")
        values.append(int(params["library_id"]))
    if params.get("album_id"):
        clauses.append("t.album_id = ?")
        values.append(params["album_id"])
    if params.get("artist_id"):
        clauses.append("t.artist_id = ?")
        values.append(params["artist_id"])
    if params.get("genre"):
        clauses.append("t.genre = ?")
        values.append(params["genre"])
    if params.get("ids"):
        ids = [int(i) for i in str(params["ids"]).split(",") if i.strip().isdigit()]
        if ids:
            clauses.append(f"t.id IN ({', '.join('?' for _ in ids)})")
            values.extend(ids)
    q = (params.get("q") or "").strip()
    if q:
        clauses.append("(t.title LIKE ? OR t.artist LIKE ? OR t.album LIKE ? OR t.path LIKE ?)")
        values.extend([f"%{q}%"] * 4)
    if params.get("artist_like"):
        clauses.append("t.artist LIKE ?")
        values.append(f"%{params['artist_like']}%")
    if params.get("album_like"):
        clauses.append("t.album LIKE ?")
        values.append(f"%{params['album_like']}%")
    if params.get("artist_exact"):
        clauses.append("t.artist = ?")
        values.append(params["artist_exact"])
    if params.get("missing") in ("0", "false", False, 0):
        clauses.append("t.missing = 0")
    elif params.get("missing") in ("1", "true", True, 1):
        clauses.append("t.missing = 1")
    else:
        clauses.append("t.missing = 0")
    if params.get("favorites_only"):
        clauses.append("EXISTS(SELECT 1 FROM favorites f WHERE f.track_id = t.id AND f.user_id = ?)")
        values.append(int(params.get("user_id") or 0))
    if str(params.get("tagged", "")) == "0":
        clauses.append("t.tagged = 0")
    elif str(params.get("tagged", "")) == "1":
        clauses.append("t.tagged = 1")
    return " AND ".join(clauses), values


def attach_favorites(tracks: list[dict], user_id: int | None) -> list[dict]:
    if not user_id or not tracks:
        return tracks
    ids = [track["id"] for track in tracks]
    if not ids:
        return tracks
    rows = db.query(
        f"SELECT track_id FROM favorites WHERE user_id = ? AND track_id IN ({', '.join('?' for _ in ids)})",
        (user_id, *ids),
    )
    favorites = {row["track_id"] for row in rows}
    for track in tracks:
        track["favorite"] = track["id"] in favorites
    return tracks


def list_tracks(params: dict) -> dict:
    where, values = _where(params)
    sort = SORTS.get(str(params.get("sort") or "title"), SORTS["title"])
    order = "DESC" if str(params.get("order", "asc")).lower() == "desc" else "ASC"
    if sort == "RANDOM()":
        order = ""
    limit = min(int(params.get("limit") or 100), 2000)
    offset = max(int(params.get("offset") or 0), 0)
    total = db.scalar(f"SELECT COUNT(*) FROM tracks t WHERE {where}", tuple(values), 0)
    rows = db.query(
        f"SELECT t.* FROM tracks t WHERE {where} ORDER BY {sort} {order} LIMIT ? OFFSET ?",
        tuple(values) + (limit, offset),
    )
    items = attach_favorites([track_dict(r) for r in rows], params.get("user_id"))
    return {"total": total, "limit": limit, "offset": offset, "items": items}


def list_albums(params: dict) -> dict:
    where = ["1=1"]
    values: list = []
    if params.get("q"):
        where.append("(name LIKE ? OR artist LIKE ?)")
        values.extend([f"%{params['q']}%"] * 2)
    if params.get("artist_id"):
        where.append("artist_id = ?")
        values.append(params["artist_id"])
    clause = " AND ".join(where)
    sort = {"name": "name COLLATE NOCASE", "artist": "artist COLLATE NOCASE", "year": "year",
            "added": "added_at", "tracks": "track_count"}.get(str(params.get("sort") or "name"), "name COLLATE NOCASE")
    order = "DESC" if str(params.get("order", "asc")).lower() == "desc" else "ASC"
    limit = min(int(params.get("limit") or 60), 500)
    offset = max(int(params.get("offset") or 0), 0)
    total = db.scalar(f"SELECT COUNT(*) FROM v_albums WHERE {clause}", tuple(values), 0)
    rows = db.query(
        f"SELECT * FROM v_albums WHERE {clause} ORDER BY {sort} {order} LIMIT ? OFFSET ?",
        tuple(values) + (limit, offset),
    )
    items = []
    for row in rows:
        item = dict(row)
        item["cover_url"] = cover_url(item.get("cover_hash") or "")
        items.append(item)
    return {"total": total, "limit": limit, "offset": offset, "items": items}


def list_artists(params: dict) -> dict:
    q = (params.get("q") or "").strip()
    where = "1=1"
    values: list = []
    if q:
        where = "name LIKE ?"
        values.append(f"%{q}%")
    sort = {"name": "name COLLATE NOCASE", "albums": "album_count", "tracks": "track_count"}.get(
        str(params.get("sort") or "name"), "name COLLATE NOCASE"
    )
    order = "DESC" if str(params.get("order", "asc")).lower() == "desc" else "ASC"
    limit = min(int(params.get("limit") or 200), 1000)
    offset = max(int(params.get("offset") or 0), 0)
    total = db.scalar(f"SELECT COUNT(*) FROM v_artists WHERE {where}", tuple(values), 0)
    rows = db.query(
        f"SELECT * FROM v_artists WHERE {where} ORDER BY {sort} {order} LIMIT ? OFFSET ?",
        tuple(values) + (limit, offset),
    )
    items = []
    for row in rows:
        item = dict(row)
        item["cover_url"] = cover_url(item.get("cover_hash") or "")
        item["image_url"] = cover_url(item.get("image_hash") or item.get("cover_hash") or "")
        item["bio"] = item.get("bio") or ""
        items.append(item)
    return {"total": total, "limit": limit, "offset": offset, "items": items}


def album_detail(album_id: str, user_id: int | None = None) -> dict | None:
    row = db.one("SELECT * FROM v_albums WHERE album_id = ?", (album_id,))
    if not row:
        return None
    album = dict(row)
    album["cover_url"] = cover_url(album.get("cover_hash") or "")
    album["tracks"] = attach_favorites([
        track_dict(r) for r in db.query(
            "SELECT * FROM tracks WHERE album_id = ? AND missing = 0 ORDER BY disc_no, track_no, title",
            (album_id,),
        )
    ], user_id)
    return album


def artist_detail(artist_id: str, user_id: int | None = None) -> dict | None:
    row = db.one("SELECT * FROM v_artists WHERE artist_id = ?", (artist_id,))
    if not row:
        return None
    artist = dict(row)
    artist["cover_url"] = cover_url(artist.get("cover_hash") or "")
    artist["image_url"] = cover_url(artist.get("image_hash") or artist.get("cover_hash") or "")
    artist["bio"] = artist.get("bio") or ""
    meta = db.one("SELECT * FROM artist_meta WHERE artist_id = ?", (artist_id,))
    if meta:
        coach = dict(meta)
        try:
            artist["aliases"] = json.loads(coach.get("aliases") or "[]")
        except json.JSONDecodeError:
            artist["aliases"] = []
        artist["meta_source"] = coach.get("source") or ""
        artist["meta_updated_at"] = coach.get("updated_at") or ""
    else:
        artist["aliases"] = []
        artist["meta_source"] = ""
        artist["meta_updated_at"] = ""
    artist["albums"] = list_albums({"artist_id": artist_id, "limit": 500})["items"]
    artist["tracks"] = attach_favorites([
        track_dict(r) for r in db.query(
            "SELECT * FROM tracks WHERE artist_id = ? AND missing = 0"
            " ORDER BY album COLLATE NOCASE, disc_no, track_no, title LIMIT 1000",
            (artist_id,),
        )
    ], user_id)
    return artist


# --------------------------------------------------------------------------- #
# smart playlists
# --------------------------------------------------------------------------- #
FIELD_MAP = {
    "title": "t.title", "artist": "t.artist", "album": "t.album", "albumartist": "t.albumartist",
    "genre": "t.genre", "year": "t.year", "play_count": "t.play_count", "rating": "t.rating",
    "duration": "t.duration", "bitrate": "t.bitrate", "codec": "t.codec", "added": "t.added_at",
    "last_played": "t.last_played_at", "composer": "t.composer", "track": "t.track_no",
    "disc": "t.disc_no", "format": "t.ext",
}

OPS = {
    "contains": "LIKE", "not_contains": "NOT LIKE", "starts_with": "LIKE", "ends_with": "LIKE",
    "equals": "=", "not_equals": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=",
    "before": "<", "after": ">",
}


def build_smart_query(rules: dict) -> tuple[str, list]:
    match = " OR " if str(rules.get("match", "all")).lower() == "any" else " AND "
    clauses: list[str] = []
    values: list = []
    for rule in rules.get("rules", []):
        field = FIELD_MAP.get(str(rule.get("field", "")))
        op = str(rule.get("op", "contains"))
        if not field or op not in OPS and op not in ("in", "is_set", "is_not_set", "between"):
            continue
        value = rule.get("value")
        if op == "is_set":
            clauses.append(f"{field} IS NOT NULL AND {field} <> ''")
            continue
        if op == "is_not_set":
            clauses.append(f"({field} IS NULL OR {field} = '')")
            continue
        if op == "between":
            low, high = (value or [None, None])[:2]
            clauses.append(f"{field} BETWEEN ? AND ?")
            values.extend([low, high])
            continue
        if op == "in":
            items = value if isinstance(value, list) else str(value or "").split(",")
            items = [str(item).strip() for item in items if str(item).strip()]
            if not items:
                continue
            clauses.append(f"{field} IN ({', '.join('?' for _ in items)})")
            values.extend(items)
            continue
        sql_op = OPS[op]
        if sql_op in ("LIKE", "NOT LIKE"):
            pattern = str(value if value is not None else "")
            if op == "starts_with":
                pattern = f"{pattern}%"
            elif op == "ends_with":
                pattern = f"%{pattern}"
            else:
                pattern = f"%{pattern}%"
            clauses.append(f"{field} {sql_op} ?")
            values.append(pattern)
        else:
            clauses.append(f"{field} {sql_op} ?")
            values.append(value)
    if not clauses:
        clauses.append("1=1")
    return "t.missing = 0 AND (" + match.join(clauses) + ")", values


def smart_tracks(rules: dict, limit: int = 500) -> list[dict]:
    where, values = build_smart_query(rules)
    sort = SORTS.get(str(rules.get("sort") or "title"), SORTS["title"])
    order = "DESC" if str(rules.get("order", "asc")).lower() == "desc" else "ASC"
    if sort == "RANDOM()":
        order = ""
    rows = db.query(
        f"SELECT t.* FROM tracks t WHERE {where} ORDER BY {sort} {order} LIMIT ?",
        tuple(values) + (min(int(rules.get("limit") or limit), 2000),),
    )
    return [track_dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# duplicates / stats
# --------------------------------------------------------------------------- #
def _score(track: dict) -> float:
    score = float(track.get("bitrate") or 0)
    if str(track.get("codec")).upper() == "FLAC":
        score += 400
    if (track.get("bit_depth") or 0) >= 24:
        score += 300
    score += min(float(track.get("size") or 0) / 1e6, 60)
    return score


def duplicate_groups() -> list[dict]:
    tolerance = float(db.get_setting("dedupe.duration_tolerance", "2") or 2)
    groups: list[dict] = []
    rows = db.query(
        "SELECT lower(trim(title)) AS t, lower(trim(artist)) AS a, COUNT(*) AS n"
        " FROM tracks WHERE missing = 0 AND trim(title) <> ''"
        " GROUP BY t, a HAVING n > 1"
    )
    for row in rows:
        tracks = [
            track_dict(r) for r in db.query(
                "SELECT * FROM tracks WHERE missing = 0 AND lower(trim(title)) = ?"
                " AND lower(trim(artist)) = ? ORDER BY duration",
                (row["t"], row["a"]),
            )
        ]
        clusters: list[list[dict]] = []
        for track in tracks:
            for cluster in clusters:
                if abs((cluster[0].get("duration") or 0) - (track.get("duration") or 0)) <= tolerance:
                    cluster.append(track)
                    break
            else:
                clusters.append([track])
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            ranked = sorted(cluster, key=_score, reverse=True)
            groups.append({
                "key": f"{row['t']}|{row['a']}|{int(cluster[0].get('duration') or 0)}",
                "title": ranked[0].get("title") or "",
                "artist": ranked[0].get("artist") or "",
                "copies": len(ranked),
                "keep_id": ranked[0]["id"],
                "best": ranked[0],
                "tracks": ranked,
            })
    identical = []
    reported: set[int] = {
        track["id"] for group in groups for track in group["tracks"]
    }
    for row in db.query(
        "SELECT quick_hash, COUNT(*) AS n FROM tracks WHERE missing = 0 AND quick_hash <> ''"
        " GROUP BY quick_hash HAVING n > 1"
    ):
        tracks = [track_dict(r) for r in db.query(
            "SELECT * FROM tracks WHERE quick_hash = ? AND missing = 0", (row["quick_hash"],)
        )]
        # Files already grouped by title/artist/duration would otherwise show up
        # twice and "delete the rest" would run twice on the same ids.
        tracks = [track for track in tracks if track["id"] not in reported]
        if len(tracks) < 2:
            continue
        ranked = sorted(tracks, key=_score, reverse=True)
        identical.append({
            "key": f"hash:{row['quick_hash']}",
            "title": ranked[0].get("title") or "",
            "artist": ranked[0].get("artist") or "",
            "copies": len(ranked),
            "keep_id": ranked[0]["id"],
            "best": ranked[0],
            "tracks": ranked,
        })
        reported.update(track["id"] for track in ranked)
    return [*groups, *identical]


def stats() -> dict:
    return {
        "tracks": db.scalar("SELECT COUNT(*) FROM tracks WHERE missing = 0", (), 0),
        "albums": db.scalar("SELECT COUNT(*) FROM v_albums", (), 0),
        "artists": db.scalar("SELECT COUNT(*) FROM v_artists", (), 0),
        "libraries": db.scalar("SELECT COUNT(*) FROM libraries", (), 0),
        "duration": db.scalar("SELECT COALESCE(SUM(duration), 0) FROM tracks WHERE missing = 0", (), 0),
        "size": db.scalar("SELECT COALESCE(SUM(size), 0) FROM tracks WHERE missing = 0", (), 0),
        "missing": db.scalar("SELECT COUNT(*) FROM tracks WHERE missing = 1", (), 0),
        "untagged": db.scalar("SELECT COUNT(*) FROM tracks WHERE missing = 0 AND tagged = 0", (), 0),
        "recent": [track_dict(r) for r in db.query(
            "SELECT * FROM tracks WHERE missing = 0 ORDER BY added_at DESC, id DESC LIMIT 12"
        )],
        "plays": db.scalar("SELECT COUNT(*) FROM play_history", (), 0),
        "duplicates": len(duplicate_groups()),
        "tasks_running": db.scalar(
            "SELECT COUNT(*) FROM tasks WHERE status IN ('queued','running','canceling')", (), 0
        ),
    }


def record_play(track_id: int, user_id: int | None, client: str, ms_played: int = 0,
                completed: bool = False) -> None:
    row = db.one("SELECT * FROM tracks WHERE id = ?", (track_id,))
    if not row:
        raise LookupError("track not found")
    db.execute("UPDATE tracks SET play_count = play_count + 1, last_played_at = datetime('now')"
               " WHERE id = ?", (track_id,))
    db.execute(
        "INSERT INTO play_history(user_id, track_id, client, ms_played, completed) VALUES(?, ?, ?, ?, ?)",
        (user_id, track_id, client, int(ms_played), 1 if completed else 0),
    )
    publish("play", track_id=track_id, user_id=user_id, title=row["title"])


def safe_filename(name: str) -> str:
    """Strip path separators and characters Windows/NAS exports reject."""
    cleaned = str(name or "")
    for char in '<>:"/\\|?*':
        cleaned = cleaned.replace(char, "-")
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    return cleaned[:180]


def rename_track(track_id: int, template: str, user_id: int | None = None) -> dict:
    row = db.one("SELECT * FROM tracks WHERE id = ?", (track_id,))
    if not row:
        raise LookupError("track not found")
    track = track_dict(row, include_lyrics=True)
    raw_name = variables.render(template, variables.context_for(track))
    new_name = safe_filename(raw_name)
    if not new_name:
        raise ValueError("template produced an empty or invalid name")
    if is_remote(track["path"]):
        raise ValueError("remote tracks cannot be renamed (no write access)")
    ext = os.path.splitext(track["path"])[1]
    directory = os.path.dirname(track["path"])
    target = os.path.join(directory, new_name + ext)
    if target == track["path"]:
        return track
    if os.path.exists(target):
        raise FileExistsError(f"target already exists: {os.path.basename(target)}")
    os.replace(track["path"], target)
    library_row = db.one("SELECT path FROM libraries WHERE id = ?", (row["library_id"],))
    rel_path = (
        os.path.relpath(target, library_row["path"]) if library_row else os.path.basename(target)
    )
    db.execute(
        "UPDATE tracks SET path = ?, rel_path = ? WHERE id = ?",
        (target, rel_path, track_id),
    )
    db.execute(
        "INSERT INTO meta_versions(track_id, user_id, action, before_json, after_json)"
        " VALUES(?, ?, 'rename', ?, ?)",
        (track_id, user_id, json.dumps({"path": track["path"]}), json.dumps({"path": target})),
    )
    publish("track.updated", track_id=track_id)
    return get_track(track_id)
