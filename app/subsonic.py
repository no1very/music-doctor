"""Subsonic / OpenSubsonic compatible API.

Implements the subset that music clients (DSub, Symfonium, Feishin, Sonixd,
Amcfy, 音流 ...) rely on: browsing, search, streaming, cover art, playlists,
starring, scrobbling and play queue sync.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from xml.sax.saxutils import escape, quoteattr

from . import db, library
from .config import config
from .secretbox import decrypt as decrypt_secret
from .security import verify_password
from .server import Request, Response

API_VERSION = "1.16.1"
XMLNS = "http://subsonic.org/restapi"


class SubError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _xml(name: str, value, indent: int = 1) -> str:
    pad = "  " * indent
    if isinstance(value, dict):
        attrs = "".join(
            f" {key[1:]}={quoteattr(str(item))}" for key, item in value.items() if key.startswith("@")
        )
        # "#text" is the JSON representation of an element's text content
        # (Subsonic returns genres and lyrics that way). Emitting it as a child
        # element called "#text" produces XML no client can parse.
        text = value.get("#text")
        children = "".join(
            _xml(key, item, indent + 1) for key, item in value.items()
            if not key.startswith("@") and key != "#text"
        )
        if text is not None:
            escaped = escape(str(text))
            inner = f"\n{children}{pad}" if children else escaped
            return f"{pad}<{name}{attrs}>{inner}</{name}>\n"
        inner = f"\n{children}{pad}" if children else ""
        return f"{pad}<{name}{attrs}>{inner}</{name}>\n"
    if isinstance(value, (list, tuple)):
        return "".join(_xml(name, item, indent) for item in value)
    if isinstance(value, bool):
        value = _bool(value)
    elif value is None:
        value = ""
    return f"{pad}<{name}>{escape(str(value))}</{name}>\n"


def render(payload: dict, fmt: str) -> Response:
    if fmt == "json":
        return Response.json(payload)
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<subsonic-response xmlns="{XMLNS}" '
        f'status={quoteattr(payload["subsonic-response"]["status"])} version="{API_VERSION}">\n'
    )
    for key, value in payload["subsonic-response"].items():
        if key in ("status", "version"):
            continue
        body += _xml(key, value)
    body += "</subsonic-response>\n"
    return Response(200, body.encode("utf-8"), "text/xml; charset=utf-8")


def error(code: int, message: str, fmt: str) -> Response:
    payload = {"subsonic-response": {
        "status": "failed", "version": API_VERSION,
        "error": {"@code": code, "@message": message},
    }}
    if fmt == "json":
        payload["subsonic-response"]["error"] = {"code": code, "message": message}
        return Response.json(payload)
    return render(payload, "xml")


def authenticate(req: Request) -> dict:
    q = req.query
    username = (q.get("u") or "").strip()
    if username:
        row = db.one("SELECT * FROM users WHERE lower(username) = lower(?)", (username,))
        if not row or not row["active"]:
            raise SubError(40, "wrong username or password")
        token = q.get("t")
        salt = q.get("s")
        if token and salt:
            plain = decrypt_secret(row["password_cipher"] or "")
            if not plain:
                raise SubError(41, "token authentication is not available for this account")
            expected = hashlib.md5(f"{plain}{salt}".encode("utf-8")).hexdigest()
            if not hmac.compare_digest(expected, token):
                raise SubError(40, "wrong username or password")
        elif q.get("p"):
            password = q["p"]
            if password.startswith("enc:"):
                try:
                    password = bytes.fromhex(password[4:]).decode("utf-8")
                except ValueError as exc:
                    raise SubError(40, "invalid encoded password") from exc
            if not verify_password(password, row["password_hash"]):
                raise SubError(40, "wrong username or password")
        else:
            raise SubError(10, "missing credentials")
        return dict(row)
    if req.user:
        row = db.one("SELECT * FROM users WHERE id = ?", (req.user["id"],))
        if row:
            return dict(row)
    raise SubError(10, "authentication required")


# --------------------------------------------------------------------------- #
# serialisers
# --------------------------------------------------------------------------- #
SUFFIX_MIME = {
    ".mp3": "audio/mpeg", ".flac": "audio/flac", ".m4a": "audio/mp4", ".mp4": "audio/mp4",
    ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg", ".wav": "audio/wav",
    ".aiff": "audio/aiff", ".aif": "audio/aiff", ".ape": "audio/ape", ".wv": "audio/wavpack",
}


def song(track: dict, username: str = "") -> dict:
    ext = (track.get("ext") or os.path.splitext(track.get("path", ""))[1]).lower()
    data = {
        "@id": str(track["id"]),
        "@parent": track.get("album_id") or "",
        "@isDir": "false",
        "@title": track.get("title") or "",
        "@album": track.get("album") or "",
        "@artist": track.get("artist") or "",
        "@track": track.get("track_no") or 0,
        "@year": track.get("year") or 0,
        "@genre": track.get("genre") or "",
        "@coverArt": track.get("cover_hash") or track.get("album_id") or "",
        "@size": track.get("size") or 0,
        "@contentType": SUFFIX_MIME.get(ext, "audio/mpeg"),
        "@suffix": ext.lstrip("."),
        "@duration": int(track.get("duration") or 0),
        "@bitRate": int((track.get("bitrate") or 0)),
        "@path": track.get("rel_path") or os.path.basename(track.get("path") or ""),
        "@discNumber": track.get("disc_no") or 1,
        "@created": (track.get("added_at") or "").replace(" ", "T"),
        "@albumId": track.get("album_id") or "",
        "@artistId": track.get("artist_id") or "",
        "@type": "music",
        "@isVideo": "false",
        "@playCount": track.get("play_count") or 0,
    }
    if track.get("bpm"):
        data["@bpm"] = track["bpm"]
    if username:
        data["@username"] = username
    return data


def album(album_row: dict, songs: list[dict] | None = None) -> dict:
    data = {
        "@id": album_row.get("album_id") or "",
        "@name": album_row.get("name") or "",
        "@artist": album_row.get("artist") or "",
        "@artistId": album_row.get("artist_id") or "",
        "@coverArt": album_row.get("cover_hash") or album_row.get("album_id") or "",
        "@songCount": album_row.get("track_count") or 0,
        "@duration": int(album_row.get("duration") or 0),
        "@playCount": 0,
        "@created": (album_row.get("added_at") or "").replace(" ", "T"),
    }
    if album_row.get("year"):
        data["@year"] = album_row["year"]
    if album_row.get("genre"):
        data["@genre"] = album_row["genre"]
    if songs is not None:
        data["song"] = songs
    return data


def artist(artist_row: dict) -> dict:
    return {
        "@id": artist_row.get("artist_id") or "",
        "@name": artist_row.get("name") or "",
        "@coverArt": artist_row.get("image_hash") or artist_row.get("cover_hash")
        or artist_row.get("artist_id") or "",
        "@albumCount": artist_row.get("album_count") or 0,
        "@artistImageUrl": "",
    }


def _songs_for_album(album_id: str) -> list[dict]:
    rows = db.query(
        "SELECT * FROM tracks WHERE album_id = ? AND missing = 0 ORDER BY disc_no, track_no, title",
        (album_id,),
    )
    return [library.track_dict(row) for row in rows]


def _album_row(album_id: str) -> dict | None:
    row = db.one("SELECT * FROM v_albums WHERE album_id = ?", (album_id,))
    return dict(row) if row else None


def _starred_ids(user_id: int) -> set[int]:
    return {r["track_id"] for r in db.query(
        "SELECT track_id FROM favorites WHERE user_id = ?", (user_id,)
    )}


# --------------------------------------------------------------------------- #
# endpoints
# --------------------------------------------------------------------------- #
def ping(req: Request, user: dict) -> dict:
    return {}


def get_license(req: Request, user: dict) -> dict:
    return {"license": {"@valid": "true"}}


def get_music_folders(req: Request, user: dict) -> dict:
    rows = db.query("SELECT id, name FROM libraries ORDER BY id")
    folders = [{"@id": row["id"], "@name": row["name"]} for row in rows] or [
        {"@id": 1, "@name": "Music"}
    ]
    return {"musicFolders": {"musicFolder": folders}}


def _index_letter(name: str) -> str:
    first = (name or "#")[:1].upper()
    return first if first.isalnum() else "#"


def get_indexes(req: Request, user: dict) -> dict:
    rows = db.query("SELECT * FROM v_artists ORDER BY name COLLATE NOCASE")
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(_index_letter(row["name"]), []).append({
            "@name": row["name"], "@id": row["artist_id"],
        })
    indexes = [{"@name": letter, "artist": items} for letter, items in sorted(groups.items())]
    return {"indexes": {"@lastModified": int(time.time() * 1000), "@ignoredArticles": "",
                        "index": indexes}}


def get_artists(req: Request, user: dict) -> dict:
    rows = db.query("SELECT * FROM v_artists ORDER BY name COLLATE NOCASE")
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(_index_letter(row["name"]), []).append(artist(dict(row)))
    indexes = [{"@name": letter, "artist": items} for letter, items in sorted(groups.items())]
    return {"artists": {"@ignoredArticles": "", "index": indexes}}


def get_artist(req: Request, user: dict) -> dict:
    artist_id = req.query.get("id") or ""
    row = db.one("SELECT * FROM v_artists WHERE artist_id = ?", (artist_id,))
    if not row:
        raise SubError(70, "artist not found")
    albums = db.query("SELECT * FROM v_albums WHERE artist_id = ? ORDER BY year, name", (artist_id,))
    data = artist(dict(row))
    data["album"] = [album(dict(item), []) for item in albums]
    return {"artist": data}


def get_album(req: Request, user: dict) -> dict:
    album_id = req.query.get("id") or ""
    row = _album_row(album_id)
    if not row:
        raise SubError(70, "album not found")
    tracks = _songs_for_album(album_id)
    data = album(row, [song(track, user["username"]) for track in tracks])
    return {"album": data}


def _sorted_albums(kind: str, size: int, offset: int, genre: str = "", user_id: int = 0) -> list[dict]:
    base = "SELECT * FROM v_albums"
    where = ""
    params: tuple = ()
    if genre:
        where = " WHERE genre = ?"
        params = (genre,)
    if kind in ("newest", "recent"):
        sql = f"{base}{where} ORDER BY added_at DESC LIMIT ? OFFSET ?"
    elif kind == "frequent":
        sql = (
            "SELECT a.* FROM v_albums a LEFT JOIN ("
            " SELECT t.album_id, COUNT(*) AS plays FROM play_history h"
            " JOIN tracks t ON t.id = h.track_id GROUP BY t.album_id) p"
            " ON p.album_id = a.album_id ORDER BY COALESCE(p.plays, 0) DESC LIMIT ? OFFSET ?"
        )
    elif kind == "random":
        sql = f"{base}{where} ORDER BY RANDOM() LIMIT ? OFFSET ?"
    elif kind == "alphabeticalByArtist":
        sql = f"{base}{where} ORDER BY artist COLLATE NOCASE, name COLLATE NOCASE LIMIT ? OFFSET ?"
    elif kind == "starred":
        sql = (
            "SELECT DISTINCT a.* FROM v_albums a JOIN tracks t ON t.album_id = a.album_id"
            " JOIN favorites f ON f.track_id = t.id AND f.user_id = ?"
            " ORDER BY a.name LIMIT ? OFFSET ?"
        )
        params = (int(user_id),)
    elif kind == "highest":
        sql = f"{base}{where} ORDER BY track_count DESC LIMIT ? OFFSET ?"
    else:
        sql = f"{base}{where} ORDER BY name COLLATE NOCASE LIMIT ? OFFSET ?"
    rows = db.query(sql, params + (max(size, 1), max(offset, 0)))
    return [dict(row) for row in rows]


def get_album_list2(req: Request, user: dict) -> dict:
    kind = req.query.get("type") or "alphabeticalByName"
    size = int(req.query.get("size") or 50)
    offset = int(req.query.get("offset") or 0)
    genre = req.query.get("genre") or ""
    albums = _sorted_albums(kind, min(size, 500), offset, genre, user["id"])
    return {"albumList2": {"album": [album(row, []) for row in albums]}}


def get_album_list(req: Request, user: dict) -> dict:
    data = get_album_list2(req, user)
    data["albumList"] = data.pop("albumList2")
    return data


def get_song(req: Request, user: dict) -> dict:
    track = library.get_track(int(req.query.get("id") or 0))
    if not track:
        raise SubError(70, "song not found")
    return {"song": song(track, user["username"])}


def get_random_songs(req: Request, user: dict) -> dict:
    size = min(int(req.query.get("size") or 20), 500)
    rows = db.query("SELECT * FROM tracks WHERE missing = 0 ORDER BY RANDOM() LIMIT ?", (size,))
    return {"randomSongs": {"song": [song(library.track_dict(r), user["username"]) for r in rows]}}


def get_genres(req: Request, user: dict) -> dict:
    rows = db.query(
        "SELECT genre, COUNT(*) AS n, COUNT(DISTINCT album_id) AS albums, COUNT(DISTINCT artist_id) AS artists"
        " FROM tracks WHERE missing = 0 AND genre <> '' GROUP BY genre ORDER BY genre"
    )
    return {"genres": {"genre": [
        {"@songCount": row["n"], "@albumCount": row["albums"], "@artistCount": row["artists"],
         "#text": row["genre"]} for row in rows
    ]}}


def _search(req: Request, user: dict) -> dict:
    query = req.query.get("query") or req.query.get("queryPrefix") or ""
    count = int(req.query.get("songCount") or 20)
    offset = int(req.query.get("songOffset") or 0)
    tracks = library.list_tracks({"q": query, "limit": min(count, 200), "offset": offset})["items"]
    albums = library.list_albums({"q": query, "limit": 20})["items"]
    artists = library.list_artists({"q": query, "limit": 20})["items"]
    return {
        "artist": [artist(item) for item in artists],
        "album": [album(item, []) for item in albums],
        "song": [song(track, user["username"]) for track in tracks],
    }


def search3(req: Request, user: dict) -> dict:
    return {"searchResult3": _search(req, user)}


def search2(req: Request, user: dict) -> dict:
    return {"searchResult2": _search(req, user)}


def stream(req: Request, user: dict) -> Response:
    track = library.get_track(int(req.query.get("id") or 0))
    if not track:
        raise SubError(70, "song not found")
    if library.is_remote(track["path"]):
        from . import webdav
        library_row = db.one("SELECT * FROM libraries WHERE id = ?", (track["library_id"],))
        if not library_row:
            raise SubError(70, "library not found")
        ext = (track.get("ext") or "").lower()
        return Response.remote_stream(
            track["path"], webdav.library_headers(library_row),
            SUFFIX_MIME.get(ext, "audio/mpeg"),
        )
    if not os.path.isfile(track["path"]):
        raise SubError(70, "song not found")
    ext = (track.get("ext") or "").lower()
    return Response.stream(track["path"], SUFFIX_MIME.get(ext, "audio/mpeg"))


def download(req: Request, user: dict) -> Response:
    track = library.get_track(int(req.query.get("id") or 0))
    if not track:
        raise SubError(70, "song not found")
    if library.is_remote(track["path"]):
        from . import webdav
        library_row = db.one("SELECT * FROM libraries WHERE id = ?", (track["library_id"],))
        resp = Response.remote_stream(track["path"], webdav.library_headers(library_row))
        resp.headers["Content-Disposition"] = (
            f'attachment; filename="{library.safe_filename(os.path.basename(track["path"]))}"'
        )
        return resp
    if not os.path.isfile(track["path"]):
        raise SubError(70, "song not found")
    resp = Response.stream(track["path"])
    resp.headers["Content-Disposition"] = (
        f'attachment; filename="{library.safe_filename(os.path.basename(track["path"]))}"'
    )
    return resp


def get_cover_art(req: Request, user: dict) -> Response:
    ident = req.query.get("id") or ""
    cover = library.load_cover(ident)
    if not cover:
        album_row = _album_row(ident)
        if album_row and album_row.get("cover_hash"):
            cover = library.load_cover(album_row["cover_hash"])
    if not cover:
        track = library.get_track(int(ident)) if ident.isdigit() else None
        if track and track.get("cover_hash"):
            cover = library.load_cover(track["cover_hash"])
    if not cover:
        raise SubError(70, "cover art not found")
    resp = Response(200, cover[1], cover[0])
    resp.headers["Cache-Control"] = "public, max-age=604800, immutable"
    return resp


def scrobble(req: Request, user: dict) -> dict:
    ids = req.query.get("id") or ""
    for part in ids.split(","):
        if not part.strip().isdigit():
            continue
        submission = (req.query.get("submission") or "true").lower() != "false"
        if not submission:
            continue
        try:
            library.record_play(int(part), user["id"], req.query.get("c") or "subsonic",
                                int(float(req.query.get("time") or 0) * 1000))
        except LookupError:
            continue
    return {}


def _toggle_star(req: Request, user: dict, star: bool) -> dict:
    ids = [part for part in (req.query.get("id") or "").split(",") if part.strip().isdigit()]
    for part in ids:
        if star:
            db.execute("INSERT OR IGNORE INTO favorites(track_id, user_id) VALUES(?, ?)",
                       (int(part), user["id"]))
        else:
            db.execute("DELETE FROM favorites WHERE track_id = ? AND user_id = ?",
                       (int(part), user["id"]))
    return {}


def star(req: Request, user: dict) -> dict:
    return _toggle_star(req, user, True)


def unstar(req: Request, user: dict) -> dict:
    return _toggle_star(req, user, False)


def get_starred2(req: Request, user: dict) -> dict:
    rows = db.query(
        "SELECT t.* FROM tracks t JOIN favorites f ON f.track_id = t.id"
        " WHERE f.user_id = ? AND t.missing = 0 ORDER BY f.created_at DESC",
        (user["id"],),
    )
    tracks = [library.track_dict(row) for row in rows]
    album_ids = {track["album_id"] for track in tracks}
    albums = [dict(db.one("SELECT * FROM v_albums WHERE album_id = ?", (album_id,)) or {})
              for album_id in album_ids]
    albums = [item for item in albums if item]
    artist_ids = {track["artist_id"] for track in tracks}
    artists = []
    for artist_id in artist_ids:
        row = db.one("SELECT * FROM v_artists WHERE artist_id = ?", (artist_id,))
        if row:
            artists.append(dict(row))
    return {"starred2": {
        "song": [song(track, user["username"]) for track in tracks],
        "album": [album(item, []) for item in albums],
        "artist": [artist(item) for item in artists],
    }}


def get_starred(req: Request, user: dict) -> dict:
    payload = get_starred2(req, user)
    return {"starred": payload["starred2"]}


def get_playlists(req: Request, user: dict) -> dict:
    rows = db.query("SELECT * FROM playlists WHERE owner_id = ? ORDER BY id DESC", (user["id"],))
    items = []
    for row in rows:
        items.append({
            "@id": row["id"],
            "@name": row["name"],
            "@comment": row["description"] or "",
            "@owner": user["username"],
            "@public": _bool(bool(row["public"])),
            "@songCount": db.scalar(
                "SELECT COUNT(*) FROM playlist_items WHERE playlist_id = ?", (row["id"],), 0
            ),
            "@created": (row["created_at"] or "").replace(" ", "T"),
            "@changed": (row["updated_at"] or "").replace(" ", "T"),
        })
    return {"playlists": {"playlist": items}}


def get_playlist(req: Request, user: dict) -> dict:
    playlist_id = int(req.query.get("id") or 0)
    row = db.one(
        "SELECT p.*, u.username AS owner_name FROM playlists p"
        " LEFT JOIN users u ON u.id = p.owner_id WHERE p.id = ?",
        (playlist_id,),
    )
    if not row:
        raise SubError(70, "playlist not found")
    if row["owner_id"] != user["id"] and not row["public"] and user["role"] != "admin":
        raise SubError(50, "not authorized to read this playlist")
    if row["kind"] == "smart":
        import json
        tracks = library.smart_tracks(json.loads(row["rules"] or "{}"))
    else:
        tracks = [library.track_dict(r) for r in db.query(
            "SELECT t.* FROM playlist_items i JOIN tracks t ON t.id = i.track_id"
            " WHERE i.playlist_id = ? ORDER BY i.position, i.track_id", (playlist_id,)
        )]
    data = {
        "@id": row["id"], "@name": row["name"], "@comment": row["description"] or "",
        "@owner": row["owner_name"] or user["username"], "@public": _bool(bool(row["public"])),
        "@songCount": len(tracks),
        "entry": [song(track, user["username"]) for track in tracks],
    }
    return {"playlist": data}


def create_playlist(req: Request, user: dict) -> dict:
    name = (req.query.get("name") or "").strip()
    if not name:
        raise SubError(0, "playlist name required")
    playlist_id = req.query.get("playlistId")
    song_ids = [int(part) for part in (req.query.get("songId") or "").split(",") if part.strip().isdigit()]
    if playlist_id and playlist_id.isdigit():
        row = db.one("SELECT * FROM playlists WHERE id = ? AND owner_id = ?", (int(playlist_id), user["id"]))
        if not row:
            raise SubError(70, "playlist not found")
        db.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (int(playlist_id),))
        db.execute("UPDATE playlists SET name = ? WHERE id = ?", (name, int(playlist_id)))
        target = int(playlist_id)
    else:
        cur = db.execute("INSERT INTO playlists(owner_id, name, kind) VALUES(?, ?, 'static')",
                         (user["id"], name))
        target = int(cur.lastrowid)
    for position, track_id in enumerate(song_ids):
        db.execute("INSERT OR IGNORE INTO playlist_items(playlist_id, track_id, position)"
                   " VALUES(?, ?, ?)", (target, track_id, position))
    return {"playlist": {"@id": target, "@name": name}}


def update_playlist(req: Request, user: dict) -> dict:
    playlist_id = int(req.query.get("playlistId") or 0)
    row = db.one("SELECT * FROM playlists WHERE id = ? AND owner_id = ?", (playlist_id, user["id"]))
    if not row:
        raise SubError(70, "playlist not found")
    if req.query.get("name"):
        db.execute("UPDATE playlists SET name = ? WHERE id = ?", (req.query["name"], playlist_id))
    for part in (req.query.get("songIdToAdd") or "").split(","):
        if part.strip().isdigit():
            position = db.scalar(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM playlist_items WHERE playlist_id = ?",
                (playlist_id,), 0,
            )
            db.execute("INSERT OR IGNORE INTO playlist_items(playlist_id, track_id, position)"
                       " VALUES(?, ?, ?)", (playlist_id, int(part), position))
    for part in (req.query.get("songIndexToRemove") or "").split(","):
        if part.strip().isdigit():
            index = int(part)
            ids = [r["track_id"] for r in db.query(
                "SELECT track_id FROM playlist_items WHERE playlist_id = ? ORDER BY position",
                (playlist_id,),
            )]
            if 0 <= index < len(ids):
                db.execute("DELETE FROM playlist_items WHERE playlist_id = ? AND track_id = ?",
                           (playlist_id, ids[index]))
    db.execute("UPDATE playlists SET updated_at = datetime('now') WHERE id = ?", (playlist_id,))
    return {}


def delete_playlist(req: Request, user: dict) -> dict:
    playlist_id = req.query.get("id") or "0"
    db.execute("DELETE FROM playlists WHERE id = ? AND owner_id = ?", (int(playlist_id), user["id"]))
    return {}


def get_play_queue(req: Request, user: dict) -> dict:
    row = db.one("SELECT * FROM play_queues WHERE user_id = ?", (user["id"],))
    if not row:
        return {"playQueue": {}}
    import json
    ids = json.loads(row["items"] or "[]")
    tracks = []
    for track_id in ids:
        track = library.get_track(int(track_id))
        if track:
            tracks.append(song(track, user["username"]))
    return {"playQueue": {
        "@current": row["current"],
        "@position": row["position_ms"],
        "@username": user["username"],
        "@changed": (row["updated_at"] or "").replace(" ", "T"),
        "entry": tracks,
    }}


def save_play_queue(req: Request, user: dict) -> dict:
    import json
    ids = [int(part) for part in (req.query.get("id") or "").split(",") if part.strip().isdigit()]
    current = int(req.query.get("current") or 0)
    position = int(req.query.get("position") or 0)
    db.execute(
        "INSERT INTO play_queues(user_id, items, current, position_ms, updated_at)"
        " VALUES(?, ?, ?, ?, datetime('now')) ON CONFLICT(user_id) DO UPDATE SET"
        " items = excluded.items, current = excluded.current,"
        " position_ms = excluded.position_ms, updated_at = datetime('now')",
        (user["id"], json.dumps(ids), current, position),
    )
    return {}


def get_user(req: Request, user: dict) -> dict:
    q = req.query
    return {"user": {
        "@username": user["username"],
        "@email": "",
        "@scrobblingEnabled": "true",
        "@adminRole": _bool(user["role"] == "admin"),
        "@settingsRole": _bool(user["role"] in ("admin", "editor")),
        "@downloadRole": "true",
        "@uploadRole": "false",
        "@playlistRole": "true",
        "@coverArtRole": "false",
        "@commentRole": "false",
        "@podcastRole": "false",
        "@streamRole": "true",
        "@jukeboxRole": "false",
        "@shareRole": "false",
        "@videoConversionRole": "false",
        "@folder": [row["id"] for row in db.query("SELECT id FROM libraries")],
    }}


def get_now_playing(req: Request, user: dict) -> dict:
    rows = db.query(
        "SELECT t.*, u.username FROM play_history h JOIN tracks t ON t.id = h.track_id"
        " LEFT JOIN users u ON u.id = h.user_id WHERE h.played_at > datetime('now', '-1 hour')"
        " ORDER BY h.id DESC LIMIT 20"
    )
    return {"nowPlaying": {"entry": [song(library.track_dict(row), row["username"] or "")
                                     for row in rows]}}


def get_lyrics(req: Request, user: dict) -> dict:
    artist = req.query.get("artist") or ""
    title = req.query.get("title") or ""
    row = db.one(
        "SELECT * FROM tracks WHERE lower(title) = lower(?) AND lower(artist) = lower(?)"
        " AND lyrics <> '' LIMIT 1", (title, artist)
    )
    if not row:
        return {"lyrics": {"@artist": artist, "@title": title}}
    return {"lyrics": {"@artist": row["artist"], "@title": row["title"], "#text": row["lyrics"]}}


def get_lyrics_by_song_id(req: Request, user: dict) -> dict:
    track = library.get_track(int(req.query.get("id") or 0))
    if not track:
        raise SubError(70, "song not found")
    if not track.get("lyrics"):
        return {"lyricsList": {"structuredLyrics": []}}
    return {"lyricsList": {"structuredLyrics": [{
        "@displayArtist": track.get("artist") or "",
        "@displayTitle": track.get("title") or "",
        "@lang": "und",
        "@synced": "false",
        "line": [{"#text": line} for line in track["lyrics"].splitlines()],
    }]}}


def get_scan_status(req: Request, user: dict) -> dict:
    running = db.one("SELECT * FROM tasks WHERE kind = 'scan' AND status IN ('running','queued')")
    return {"scanStatus": {
        "@scanning": _bool(bool(running)),
        "@count": db.scalar("SELECT COUNT(*) FROM tracks WHERE missing = 0", (), 0),
    }}


def start_scan(req: Request, user: dict) -> dict:
    from .tasks import tasks
    from .scanner import scan_library
    for row in db.query("SELECT id FROM libraries WHERE enabled = 1"):
        tasks.start("scan", "Scan library", {"library_id": row["id"]},
                    lambda ctx, lid=row["id"]: scan_library(lid, ctx), user["id"])
    return {}


def get_open_subsonic_extensions(req: Request, user: dict) -> dict:
    return {"openSubsonicExtensions": [
        {"@name": "apiKeyAuthentication", "versions": [1]},
        {"@name": "formPost", "versions": [1]},
        {"@name": "songLyrics", "versions": [1]},
        {"@name": "playbackReport", "versions": [1]},
    ]}


def get_artist_info2(req: Request, user: dict) -> dict:
    artist_id = req.query.get("id") or ""
    row = db.one("SELECT * FROM v_artists WHERE artist_id = ?", (artist_id,))
    if not row:
        raise SubError(70, "artist not found")
    image_hash = row["image_hash"] if "image_hash" in row.keys() else ""
    host = req.headers.get("Host") or f"{config.host}:{config.port}"
    image_url = f"http://{host}/api/covers/{image_hash}" if image_hash else ""
    return {"artistInfo2": {
        "biography": row["bio"] or "",
        "musicBrainzId": "",
        "lastFmUrl": "",
        "smallImageUrl": image_url,
        "mediumImageUrl": image_url,
        "largeImageUrl": image_url,
    }}


def get_album_info2(req: Request, user: dict) -> dict:
    return {"albumInfo": {"notes": "", "musicBrainzId": "", "lastFmUrl": ""}}


HANDLERS = {
    "ping": ping,
    "getLicense": get_license,
    "getMusicFolders": get_music_folders,
    "getIndexes": get_indexes,
    "getArtists": get_artists,
    "getArtist": get_artist,
    "getArtistInfo2": get_artist_info2,
    "getAlbum": get_album,
    "getAlbumInfo2": get_album_info2,
    "getAlbumList": get_album_list,
    "getAlbumList2": get_album_list2,
    "getSong": get_song,
    "getRandomSongs": get_random_songs,
    "getGenres": get_genres,
    "search2": search2,
    "search3": search3,
    "stream": stream,
    "download": download,
    "getCoverArt": get_cover_art,
    "scrobble": scrobble,
    "star": star,
    "unstar": unstar,
    "getStarred": get_starred,
    "getStarred2": get_starred2,
    "getPlaylists": get_playlists,
    "getPlaylist": get_playlist,
    "createPlaylist": create_playlist,
    "updatePlaylist": update_playlist,
    "deletePlaylist": delete_playlist,
    "getPlayQueue": get_play_queue,
    "savePlayQueue": save_play_queue,
    "getUser": get_user,
    "getNowPlaying": get_now_playing,
    "getLyrics": get_lyrics,
    "getLyricsBySongId": get_lyrics_by_song_id,
    "getScanStatus": get_scan_status,
    "startScan": start_scan,
    "getOpenSubsonicExtensions": get_open_subsonic_extensions,
}


def handle(req: Request) -> Response:
    method = req.path[len("/rest/"):] if req.path.startswith("/rest/") else req.path
    method = method.rstrip("/")
    if method.endswith(".view"):
        method = method[:-5]
    fmt = "json" if (req.query.get("f") or "").lower() == "json" else "xml"
    if db.get_setting("subsonic.enabled", "1") != "1":
        return error(50, "Subsonic API is disabled", fmt)
    handler = HANDLERS.get(method)
    if handler is None:
        return error(0, f"unknown method: {method}", fmt)
    try:
        user = authenticate(req)
        data = handler(req, user) or {}
    except SubError as exc:
        return error(exc.code, exc.message, fmt)
    except (LookupError, ValueError):
        return error(70, "requested data not found", fmt)
    payload = {"subsonic-response": {
        "status": "ok",
        "version": API_VERSION,
        **data,
    }}
    if fmt == "json":
        return Response.json(payload)
    return render(payload, "xml")
