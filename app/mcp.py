"""MCP (Model Context Protocol) server over Streamable HTTP.

Agents connect to ``POST /mcp`` with an API key and can inspect and maintain the
library with the same permission model as the web UI. JSON-RPC 2.0 is
implemented directly so the app keeps zero dependencies.
"""

from __future__ import annotations

import json

from . import db, library, scrape
from .config import config
from .server import ApiError, Request, Response
from .tasks import tasks

PROTOCOL_VERSION = "2025-06-18"

TOOLS = [
    {
        "name": "library_stats",
        "description": "Overview of the music library: tracks, albums, artists, duration, untagged and duplicate counts.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "search_tracks",
        "description": "Search tracks by free text (title/artist/album/path) and optional filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "artist": {"type": "string"},
                "album": {"type": "string"},
                "genre": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
        },
    },
    {
        "name": "get_track",
        "description": "Full metadata for one track, including lyrics, cover URL and version history count.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
    {
        "name": "update_track",
        "description": "Update tags of a track. Writes the audio file when supported and records a version snapshot.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "fields": {
                    "type": "object",
                    "description": "title/artist/albumartist/album/genre/year/track_no/disc_no/comment/lyrics/...",
                },
                "write_file": {"type": "boolean", "default": True},
            },
            "required": ["id", "fields"],
        },
    },
    {
        "name": "scan_library",
        "description": "Start a background scan for one library, or all enabled libraries.",
        "inputSchema": {
            "type": "object",
            "properties": {"library_id": {"type": "integer"}},
        },
    },
    {
        "name": "scrape_track",
        "description": "Find tag candidates for a track; optionally apply the best match.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "apply": {"type": "boolean", "default": False},
            },
            "required": ["id"],
        },
    },
    {
        "name": "list_playlists",
        "description": "List playlists (static and smart) with their track counts.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "create_playlist",
        "description": "Create a playlist. Use kind=smart with rules for a dynamic playlist.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "kind": {"type": "string", "enum": ["static", "smart"]},
                "rules": {"type": "object", "description": "{match,rules:[{field,op,value}],sort,order,limit}"},
                "track_ids": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_duplicates",
        "description": "List duplicate groups with a suggested file to keep.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "recent_activity",
        "description": "Latest play history and finished tasks.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}},
        },
    },
]


def _text(payload) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}],
        "isError": False,
    }


def _error(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _track_brief(track: dict) -> dict:
    keys = (
        "id", "title", "artist", "albumartist", "album", "genre", "year", "track_no",
        "disc_no", "duration", "bitrate", "codec", "play_count", "album_id", "artist_id",
        "rel_path", "cover_url", "favorite",
    )
    return {key: track.get(key) for key in keys if key in track}


def call_tool(req: Request, name: str, arguments: dict) -> dict:
    if name == "library_stats":
        return _text(library.stats())

    if name == "search_tracks":
        params = {
            "q": arguments.get("query", ""),
            "artist_like": arguments.get("artist", ""),
            "album_like": arguments.get("album", ""),
            "genre": arguments.get("genre", ""),
            "limit": min(int(arguments.get("limit") or 30), 200),
            "user_id": req.user["id"],
        }
        data = library.list_tracks(params)
        return _text({"total": data["total"], "items": [_track_brief(t) for t in data["items"]]})

    if name == "get_track":
        track = library.get_track(int(arguments.get("id") or 0), include_lyrics=True)
        if not track:
            return _error("track not found")
        track["favorite"] = bool(db.one(
            "SELECT 1 FROM favorites WHERE track_id = ? AND user_id = ?",
            (track["id"], req.user["id"]),
        ))
        track["versions"] = len(library.versions(track["id"]))
        return _text(track)

    if name == "update_track":
        req.require("editor")
        fields = arguments.get("fields") or {}
        unknown = [key for key in fields if key not in library.EDITABLE]
        if unknown:
            return _error(f"unknown fields: {', '.join(unknown)}")
        try:
            track = library.update_track(
                int(arguments["id"]), fields, user_id=req.user["id"], action="edit",
                write_file=bool(arguments.get("write_file", True)) and not config.read_only,
            )
        except RuntimeError as exc:
            return _error(str(exc))
        return _text(_track_brief(track))

    if name == "scan_library":
        req.require("editor")
        library_id = arguments.get("library_id")
        if library_id:
            row = db.one("SELECT * FROM libraries WHERE id = ?", (int(library_id),))
            if not row:
                return _error("library not found")
            rows = [row]
        else:
            rows = db.query("SELECT * FROM libraries WHERE enabled = 1")
        started = []
        for row in rows:
            from .api import _scan_job
            task_id = tasks.start(
                "scan", f"Scan {row['name']}", {"library_id": row["id"]},
                _scan_job(row["id"], False, req.user["id"]), req.user["id"],
            )
            started.append({"library_id": row["id"], "task_id": task_id})
        return _text({"started": started})

    if name == "scrape_track":
        req.require("editor")
        track = library.get_track(int(arguments.get("id") or 0))
        if not track:
            return _error("track not found")
        if arguments.get("apply"):
            result = scrape.auto_scrape_track(track, req.user["id"])
            return _text(result)
        return _text({"candidates": scrape.search(track_id=track["id"], limit=10)})

    if name == "list_playlists":
        rows = db.query(
            "SELECT p.*, (SELECT COUNT(*) FROM playlist_items WHERE playlist_id = p.id) AS count"
            " FROM playlists p WHERE p.owner_id = ? OR p.public = 1 ORDER BY p.id DESC",
            (req.user["id"],),
        )
        return _text([
            {"id": r["id"], "name": r["name"], "kind": r["kind"], "public": bool(r["public"]),
             "count": r["count"], "rules": json.loads(r["rules"] or "{}")}
            for r in rows
        ])

    if name == "create_playlist":
        req.require("listener")
        name_value = str(arguments.get("name") or "").strip()
        if not name_value:
            return _error("name is required")
        kind = "smart" if arguments.get("kind") == "smart" else "static"
        rules = arguments.get("rules") or {}
        cur = db.execute(
            "INSERT INTO playlists(owner_id, name, kind, rules) VALUES(?, ?, ?, ?)",
            (req.user["id"], name_value, kind, json.dumps(rules, ensure_ascii=False)),
        )
        playlist_id = int(cur.lastrowid)
        for position, track_id in enumerate(arguments.get("track_ids") or []):
            db.execute(
                "INSERT OR IGNORE INTO playlist_items(playlist_id, track_id, position) VALUES(?, ?, ?)",
                (playlist_id, int(track_id), position),
            )
        return _text({"id": playlist_id, "name": name_value, "kind": kind})

    if name == "list_duplicates":
        groups = library.duplicate_groups()
        return _text([
            {
                "title": group["title"], "artist": group.get("artist", ""),
                "copies": group["copies"], "keep_id": group["keep_id"],
                "tracks": [_track_brief(t) for t in group["tracks"]],
            }
            for group in groups
        ])

    if name == "recent_activity":
        limit = min(int(arguments.get("limit") or 10), 50)
        plays = db.query(
            "SELECT h.played_at, t.id, t.title, t.artist, u.username FROM play_history h"
            " JOIN tracks t ON t.id = h.track_id LEFT JOIN users u ON u.id = h.user_id"
            " ORDER BY h.id DESC LIMIT ?", (limit,)
        )
        finished = db.query(
            "SELECT id, kind, title, status, error, finished_at FROM tasks"
            " WHERE status IN ('done','failed','canceled') ORDER BY id DESC LIMIT ?", (limit,)
        )
        return _text({
            "plays": [dict(row) for row in plays],
            "tasks": [dict(row) for row in finished],
        })

    return _error(f"unknown tool: {name}")


def _dispatch(req: Request, message: dict) -> dict | None:
    method = message.get("method")
    params = message.get("params") or {}
    msg_id = message.get("id")

    def ok(result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def fail(code: int, text: str):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": text}}

    if method == "initialize":
        return ok({
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "music-doctor", "version": config.version},
            "instructions": (
                "Music-Doctor library management. Use search_tracks/get_track to inspect, "
                "update_track to fix tags (writes files and keeps versions), scrape_track to "
                "find metadata, scan_library to index new files."
            ),
        })
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return fail(-32602, "arguments must be an object")
        try:
            return ok(call_tool(req, str(name), arguments))
        except ApiError as exc:
            return ok(_error(exc.message))
        except Exception as exc:  # noqa: BLE001 - JSON-RPC boundary
            return ok(_error(f"{type(exc).__name__}: {exc}"))
    if method == "resources/list":
        return ok({"resources": [
            {"uri": "omt://library/stats", "name": "Library statistics", "mimeType": "application/json"},
        ]})
    if method == "resources/read":
        uri = str(params.get("uri") or "")
        if uri == "omt://library/stats":
            return ok({"contents": [{
                "uri": uri, "mimeType": "application/json",
                "text": json.dumps(library.stats(), ensure_ascii=False, default=str),
            }]})
        return fail(-32602, "unknown resource")
    return fail(-32601, f"method not found: {method}")


def handle(req: Request) -> Response:
    if not req.user:
        raise ApiError(401, "MCP requires an API key or session token")
    try:
        payload = req.json()
    except ApiError:
        return Response.json(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}, 400
        )
    if isinstance(payload, list):
        responses = [r for r in (_dispatch(req, item) for item in payload) if r]
        return Response.json(responses) if responses else Response.empty(202)
    if not isinstance(payload, dict) or "method" not in payload:
        return Response.json(
            {"jsonrpc": "2.0", "id": payload.get("id") if isinstance(payload, dict) else None,
             "error": {"code": -32600, "message": "invalid request"}}, 400
        )
    response = _dispatch(req, payload)
    if response is None:
        return Response.empty(202)
    return Response.json(response)
