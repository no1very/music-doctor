"""End-to-end verification against a running Music-Doctor instance.

Usage:
    python tools/e2e.py --base http://127.0.0.1:8399 --library "C:/path/to/music"

It exercises: login, library creation, scan task, track listing, tag editing
(file write-back + version history + rollback), batch edit, playlist + smart
playlist, duplicates, Subsonic token auth, streaming Range requests and the SSE
event stream. Exits non-zero when a check fails.
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from xml.sax.saxutils import escape, quoteattr

PASS: list[str] = []
FAIL: list[str] = []
DAV_PORT = 8929
DAV_FILES = {
    "/dav/Album A/01 - Remote Song.mp3": b"\xff\xfb\x90\x00" + b"\x00" * 4096,
    "/dav/Album A/02 - Remote Tune.mp3": b"\xff\xfb\x90\x00" + b"\x00" * 2048,
}


class DavHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def _children(self, path: str) -> list[str]:
        if path == "/dav/":
            return ["/dav/Album A/"]
        if path == "/dav/Album A/":
            return list(DAV_FILES)
        return []

    def do_PROPFIND(self):  # noqa: N802
        path = urllib.parse.unquote(self.path)
        if not path.endswith("/"):
            path += "/"
        if path not in ("/dav/", "/dav/Album A/"):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        entries = [path] + self._children(path)
        parts = ['<?xml version="1.0" encoding="utf-8"?><d:multistatus xmlns:d="DAV:">']
        for entry in entries:
            href = urllib.parse.quote(entry)
            parts.append("<d:response><d:href>%s</d:href><d:propstat><d:prop>" % escape(href))
            if entry.endswith("/"):
                parts.append("<d:resourcetype><d:collection/></d:resourcetype>")
            else:
                size = len(DAV_FILES.get(entry, b""))
                parts.append(f"<d:resourcetype/><d:getcontentlength>{size}</d:getcontentlength>")
                parts.append("<d:getlastmodified>Sun, 08 Nov 2026 10:00:00 GMT</d:getlastmodified>")
            parts.append(
                "</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>"
            )
        parts.append("</d:multistatus>")
        body = "".join(parts).encode("utf-8")
        self.send_response(207)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        path = urllib.parse.unquote(self.path)
        if path not in DAV_FILES:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = DAV_FILES[path]
        start, end = 0, len(body) - 1
        status = 200
        range_header = self.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            first, _, last = range_header[6:].partition("-")
            start = int(first) if first else 0
            end = min(int(last), len(body) - 1) if last else len(body) - 1
            status = 206
        chunk = body[start:end + 1]
        self.send_response(status)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", str(len(chunk)))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(body)}")
        self.end_headers()
        self.wfile.write(chunk)


def start_dav_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", DAV_PORT), DavHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL  {name} {detail}")


def request(base: str, method: str, path: str, body=None, token: str | None = None,
            raw: bool = False):
    url = base + path
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = resp.read()
            if raw:
                return resp.status, dict(resp.headers), payload
            return resp.status, dict(resp.headers), json.loads(payload or b"{}")
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        if raw:
            return exc.code, dict(exc.headers), payload
        try:
            return exc.code, dict(exc.headers), json.loads(payload or b"{}")
        except json.JSONDecodeError:
            return exc.code, dict(exc.headers), {"error": payload.decode("utf-8", "replace")}


def wait_task(base: str, token: str, task_id: int, timeout: float = 120) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, _, data = request(base, "GET", f"/api/tasks/{task_id}", token=token)
        if status == 200 and data.get("status") in ("done", "failed", "canceled"):
            return data
        time.sleep(0.5)
    return {"status": "timeout"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8399")
    parser.add_argument("--library", default="")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default=os.environ.get("MTW_PASSWORD", ""))
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()

    password = args.password
    if not password:
        note = os.path.join(args.data_dir, "INITIAL_ADMIN.txt")
        if os.path.exists(note):
            for line in open(note, encoding="utf-8"):
                if line.startswith("password:"):
                    password = line.split(":", 1)[1].strip()
    if not password:
        print("no password: pass --password or keep data/INITIAL_ADMIN.txt")
        return 2

    base = args.base.rstrip("/")
    print(f"\n[1] auth against {base}")
    status, headers, data = request(base, "POST", "/api/auth/login",
                                    {"username": args.username, "password": password})
    check("login returns 200", status == 200, f"status={status} {data}")
    token = data.get("token", "")
    check("login returns token", bool(token))
    check("session cookie set", "omt_session" in (headers.get("Set-Cookie") or ""))
    check("login rejects bad password", request(base, "POST", "/api/auth/login",
                                                {"username": args.username, "password": "nope"})[0] == 401)
    check("unauthenticated /api/tracks is 401", request(base, "GET", "/api/tracks")[0] == 401)

    print("\n[2] library setup + scan")
    library_path = os.path.abspath(args.library) if args.library else ""
    if library_path:
        status, _, lib = request(base, "POST", "/api/libraries",
                                 {"name": "E2E", "path": library_path}, token)
        if status == 409:
            _, _, libs = request(base, "GET", "/api/libraries", token=token)
            lib = [item for item in libs["items"] if item["path"] == library_path][0]
        check("library created", status in (200, 409), f"status={status} {lib}")

        status, _, task = request(base, "POST", f"/api/libraries/{lib['id']}/scan", {"force": True}, token)
        check("scan task queued", status == 200 and task.get("task_id"))
        result = wait_task(base, token, task["task_id"])
        check("scan completed", result.get("status") == "done", str(result.get("status")))
        scan_result = result.get("result") or {}
        indexed = (scan_result.get("added", 0) + scan_result.get("updated", 0)
                   + scan_result.get("unchanged", 0))
        check("scan found files", indexed >= 4, str(scan_result))

    print("\n[3] browsing")
    status, _, tracks = request(base, "GET", "/api/tracks?limit=100", token=token)
    check("track list", status == 200 and tracks["total"] >= 1, f"total={tracks.get('total')}")
    items = tracks.get("items", [])
    flac = next((t for t in items if t["ext"] == ".flac"), None)
    check("flac indexed with duration", bool(flac) and flac["duration"] > 1.5, str(flac and flac["duration"]))
    status, _, albums = request(base, "GET", "/api/albums", token=token)
    check("albums aggregated", status == 200 and albums["total"] >= 1, f"total={albums.get('total')}")
    status, _, artists = request(base, "GET", "/api/artists", token=token)
    check("artists aggregated", status == 200 and artists["total"] >= 1)
    status, _, stats = request(base, "GET", "/api/stats", token=token)
    check("stats", status == 200 and stats.get("tracks", 0) >= 1, str(stats.get("tracks")))

    if not items:
        print("\nno tracks scanned; stopping")
        return 1

    print("\n[4] editing + version history + rollback")
    target = items[0]
    new_title = "E2E 标题 ✓"
    status, _, edited = request(base, "PATCH", f"/api/tracks/{target['id']}", {
        "title": new_title, "artist": "E2E Artist", "album": "E2E Album",
        "year": 2001, "track_no": 7, "genre": "Test",
    }, token)
    check("PATCH track", status == 200 and edited["title"] == new_title, str(status))
    check("album_id recomputed", edited["album_id"] != target["album_id"])

    status, _, reread = request(base, "GET", f"/api/tracks/{target['id']}", token=token)
    check("db reflects edit", reread["artist"] == "E2E Artist")

    status, _, versions = request(base, "GET", f"/api/tracks/{target['id']}/versions", token=token)
    check("version recorded", status == 200 and len(versions["items"]) >= 1)
    version_id = versions["items"][0]["id"]
    status, _, rolled = request(base, "POST",
                                f"/api/tracks/{target['id']}/versions/{version_id}/restore", {}, token)
    check("rollback restores title", status == 200 and rolled["title"] == target["title"],
          str(rolled.get("title")))

    print("\n[5] batch edit")
    ids = [t["id"] for t in items[:2]]
    status, _, batch = request(base, "POST", "/api/tracks/batch",
                               {"ids": ids, "action": "edit", "fields": {"comment": "batch ✓"}}, token)
    check("batch edit applied", status == 200 and batch.get("updated") == len(ids), str(batch))
    for track_id in ids:
        _, _, item = request(base, "GET", f"/api/tracks/{track_id}", token=token)
        check(f"batch comment on {track_id}", item.get("comment") == "batch ✓", str(item.get("comment")))

    print("\n[6] playlists (static + smart)")
    status, _, playlist = request(base, "POST", "/api/playlists",
                                  {"name": "E2E Static", "kind": "static"}, token)
    check("static playlist created", status == 200 and playlist.get("id"))
    status, _, filled = request(base, "POST", f"/api/playlists/{playlist['id']}/tracks",
                                {"ids": ids}, token)
    check("playlist tracks added", status == 200 and len(filled.get("tracks", [])) == len(ids))

    status, _, smart = request(base, "POST", "/api/playlists", {
        "name": "E2E Smart", "kind": "smart",
        "rules": {"match": "all", "rules": [{"field": "comment", "op": "contains", "value": "batch"}],
                  "sort": "title", "order": "asc", "limit": 50},
    }, token)
    check("smart playlist created", status == 200 and smart.get("id"))
    status, _, smart_full = request(base, "GET", f"/api/playlists/{smart['id']}", token=token)
    check("smart playlist evaluates", status == 200 and len(smart_full.get("tracks", [])) >= 1,
          f"tracks={len(smart_full.get('tracks', []))}")

    print("\n[7] duplicates + scraping surfaces")
    status, _, dups = request(base, "GET", "/api/duplicates", token=token)
    check("duplicates endpoint", status == 200 and "items" in dups)
    status, _, candidates = request(base, "GET",
                                    f"/api/tracks/{target['id']}/candidates?providers=filename",
                                    token=token)
    check("filename provider returns candidates", status == 200 and len(candidates["items"]) >= 1,
          str(status))
    status, _, providers_list = request(base, "GET", "/api/providers", token=token)
    check("providers listed", status == 200 and len(providers_list["items"]) >= 4)

    print("\n[8] streaming with Range")
    status, headers, body = request(base, "GET", f"/api/tracks/{target['id']}/stream",
                                    token=token, raw=True)
    check("stream full body", status == 200 and len(body) > 1000, f"status={status} bytes={len(body)}")
    req = urllib.request.Request(base + f"/api/tracks/{target['id']}/stream")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Range", "bytes=0-99")
    with urllib.request.urlopen(req, timeout=30) as resp:
        partial = resp.read()
        check("range request 206", resp.status == 206, str(resp.status))
        check("range length", len(partial) == 100, str(len(partial)))

    print("\n[9] Subsonic API")
    params = {
        "u": args.username,
        "t": __import__("hashlib").md5((password + "abc123").encode()).hexdigest(),
        "s": "abc123", "v": "1.16.1", "c": "e2e", "f": "json",
    }
    query = urllib.parse.urlencode(params)
    status, _, ping = request(base, "GET", f"/rest/ping.view?{query}")
    check("subsonic token auth ping", status == 200 and ping["subsonic-response"]["status"] == "ok",
          str(ping.get("subsonic-response", {}).get("error")))
    status, _, artists3 = request(base, "GET", f"/rest/getArtists.view?{query}")
    check("subsonic getArtists", status == 200 and "artists" in artists3["subsonic-response"])
    status, _, search3 = request(base, "GET", f"/rest/search3.view?{query}&query=Airbag")
    check("subsonic search3", status == 200 and len(
        search3["subsonic-response"]["searchResult3"]["song"]) >= 1)
    status, headers, xml = request(base, "GET", f"/rest/ping.view?{query.replace('f=json', 'f=xml')}",
                                   raw=True)
    check("subsonic xml response", status == 200 and b"subsonic-response" in xml)
    status, _, bad = request(base, "GET", f"/rest/ping.view?u=x&p=nope&v=1.16.1&c=e2e&f=json")
    check("subsonic rejects bad credentials",
          bad["subsonic-response"]["status"] == "failed")
    status, _, cover = request(base, "GET", f"/rest/getCoverArt.view?{query}&id={target['album_id']}")
    check("subsonic cover endpoint responds", status in (200, 404), str(status))

    print("\n[10] SSE + settings + users")
    try:
        req = urllib.request.Request(base + "/api/events")
        req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            chunk = resp.readline()
            check("SSE handshake", resp.status == 200
                  and resp.headers.get("Content-Type", "").startswith("text/event-stream")
                  and b"retry" in chunk, repr(chunk))
    except Exception as exc:  # noqa: BLE001
        check("SSE handshake", False, str(exc))
    status, _, settings = request(base, "GET", "/api/settings", token=token)
    check("settings readable", status == 200 and "items" in settings)
    status, _, updated = request(base, "PUT", "/api/settings",
                                 {"scrape.min_score": "0.7"}, token)
    check("settings writable", status == 200 and updated["items"]["scrape.min_score"] == "0.7")
    status, _, created = request(base, "POST", "/api/users",
                                 {"username": "listener1", "password": "secret123",
                                  "role": "listener"}, token)
    check("user created", status == 200 and created.get("role") == "listener", str(created))
    status, _, forbidden = request(base, "PUT", "/api/settings",
                                   {"scrape.min_score": "0.8"},
                                   created_user_token(base, "listener1", "secret123"))
    check("listener cannot change settings", status == 403, str(status))
    status, _, deleted = request(base, "DELETE", f"/api/users/{created['id']}", token=token)
    check("user deleted", status == 204)

    print("\n[11] rename with variable template")
    request(base, "PATCH", f"/api/tracks/{target['id']}", {"track_no": 7}, token)
    status, _, renamed = request(base, "POST", f"/api/tracks/{target['id']}/rename",
                                 {"template": "${tracknumber.zfill(2)} - ${title}"}, token)
    check("rename applied", status == 200 and os.path.basename(renamed["path"]).startswith("07 - "),
          str(renamed.get("path")))

    print("\n[12] API keys + MCP")
    status, _, key_data = request(base, "POST", "/api/apikeys", {"name": "e2e"}, token)
    api_key = key_data.get("key", "")
    check("api key created", status == 200 and api_key.startswith("omt_"), str(status))
    status, _, init = request(base, "POST", "/mcp",
                              {"jsonrpc": "2.0", "id": 1, "method": "initialize"}, api_key)
    check("mcp initialize", init.get("result", {}).get("serverInfo", {}).get("name") == "music-doctor",
          str(init)[:160])
    status, _, tools = request(base, "POST", "/mcp",
                               {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, api_key)
    names = {tool["name"] for tool in tools.get("result", {}).get("tools", [])}
    check("mcp tools listed", {"search_tracks", "update_track", "scan_library"} <= names,
          str(sorted(names)))
    status, _, called = request(base, "POST", "/mcp", {
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "library_stats", "arguments": {}},
    }, api_key)
    check("mcp tool call", status == 200 and not called.get("result", {}).get("isError"),
          str(called)[:160])
    status, _, search_call = request(base, "POST", "/mcp", {
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "search_tracks", "arguments": {"query": "Airbag", "limit": 3}},
    }, api_key)
    check("mcp search returns tracks", "Airbag" in str(search_call))
    status, _, _ = request(base, "POST", "/mcp",
                           {"jsonrpc": "2.0", "id": 5, "method": "initialize"})
    check("mcp rejects missing key", status == 401, str(status))
    request(base, "DELETE", f"/api/apikeys/{key_data['item']['id']}", None, token)
    status, _, _ = request(base, "POST", "/mcp",
                           {"jsonrpc": "2.0", "id": 6, "method": "initialize"}, api_key)
    check("revoked key rejected", status == 401, str(status))

    print("\n[13] sidecar lyrics + rename preview")
    request(base, "PUT", "/api/settings", {"scrape.write_lrc": "1"}, token)
    first = tracks["items"][0]
    request(base, "PATCH", f"/api/tracks/{first['id']}", {"lyrics": "e2e sidecar lyric"}, token)
    _, _, refreshed = request(base, "GET", f"/api/tracks/{first['id']}", token=token)
    lrc_file = os.path.splitext(refreshed["path"])[0] + ".lrc"
    check("external .lrc written", os.path.exists(lrc_file), lrc_file)
    status, _, preview = request(base, "POST", "/api/tracks/rename/preview", {
        "ids": ids, "template": "${tracknumber.zfill(2)} ${title}",
    }, token)
    check("rename preview", status == 200 and len(preview["items"]) == len(ids), str(status))

    print("\n[14] scrape report + backup")
    status, _, report = request(base, "GET", "/api/reports/scrape?days=30", token=token)
    check("report endpoint", status == 200 and "by_source" in report and "by_day" in report)
    status, _, backup = request(base, "GET", "/api/backup", token=token, raw=True)
    check("backup zip", status == 200 and backup[:2] == b"PK", f"status={status}")

    print("\n[15] WebDAV remote library")
    dav = start_dav_server()
    try:
        status, _, remote_lib = request(base, "POST", "/api/libraries", {
            "kind": "webdav", "name": "E2E Remote",
            "url": f"http://127.0.0.1:{DAV_PORT}/dav/",
            "username": "u", "password": "p",
        }, token)
        if status == 409:
            _, _, libs = request(base, "GET", "/api/libraries", token=token)
            remote_lib = next(item for item in libs["items"] if item.get("kind") == "webdav")
        check("webdav library created", status in (200, 409), str(remote_lib)[:160])
        status, _, remote_task = request(
            base, "POST", f"/api/libraries/{remote_lib['id']}/scan", {}, token
        )
        remote_result = wait_task(base, token, remote_task["task_id"])
        check("webdav scan completed", remote_result.get("status") == "done",
              str(remote_result.get("error")))
        check("webdav indexed files", (remote_result.get("result") or {}).get("added", 0) >= 2,
              str(remote_result.get("result")))

        status, _, remote_tracks = request(base, "GET", "/api/tracks?q=Remote&limit=20", token=token)
        remote_track = next((item for item in remote_tracks["items"] if item.get("remote")), None)
        check("remote track indexed", bool(remote_track))
        check("remote track read-only", bool(remote_track) and not remote_track["writable"])
        if remote_track:
            status, _, patched = request(
                base, "PATCH", f"/api/tracks/{remote_track['id']}",
                {"title": "Remote Song (edited)"}, token,
            )
            check("remote metadata edit (index only)",
                  status == 200 and patched.get("title") == "Remote Song (edited)", str(patched)[:120])
            status, headers, body = request(
                base, "GET", f"/api/tracks/{remote_track['id']}/stream", token=token, raw=True
            )
            check("remote stream proxied", status == 200 and len(body) > 1000,
                  f"status={status} bytes={len(body)}")
            req = urllib.request.Request(base + f"/api/tracks/{remote_track['id']}/stream")
            req.add_header("Authorization", f"Bearer {token}")
            req.add_header("Range", "bytes=0-99")
            with urllib.request.urlopen(req, timeout=30) as resp:
                partial = resp.read()
                check("remote range request", resp.status == 206 and len(partial) == 100,
                      f"status={resp.status} len={len(partial)}")
        status, _, _ = request(base, "DELETE", f"/api/libraries/{remote_lib['id']}", None, token)
        check("webdav library removed", status == 204, str(status))
    finally:
        dav.shutdown()

    print("\n[16] password change + admin reset")
    status, _, pw_user = request(base, "POST", "/api/users", {
        "username": "pwuser", "password": "oldpass1", "role": "listener",
    }, token)
    check("password test user created", status == 200, str(pw_user)[:120])
    status, _, old_login = request(base, "POST", "/api/auth/login",
                                   {"username": "pwuser", "password": "oldpass1"})
    old_token = old_login.get("token", "")
    status, _, _ = request(base, "POST", "/api/auth/password",
                           {"current": "oldpass1", "new": "newpass2"}, old_token)
    check("self password change accepted", status == 200, str(status))
    check("old password rejected",
          request(base, "POST", "/api/auth/login",
                  {"username": "pwuser", "password": "oldpass1"})[0] == 401)
    status, _, new_login = request(base, "POST", "/api/auth/login",
                                   {"username": "pwuser", "password": "newpass2"})
    check("new password works", status == 200 and bool(new_login.get("token")))
    status, _, _ = request(base, "PATCH", f"/api/users/{pw_user['id']}",
                           {"password": "resetpass3"}, token)
    check("admin reset accepted", status == 200, str(status))
    check("sessions revoked after reset",
          request(base, "GET", "/api/auth/me", token=new_login.get("token", ""))[0] == 401)
    check("reset password works",
          request(base, "POST", "/api/auth/login",
                  {"username": "pwuser", "password": "resetpass3"})[0] == 200)
    request(base, "DELETE", f"/api/users/{pw_user['id']}", None, token)
    check("password test user removed",
          request(base, "POST", "/api/auth/login",
                  {"username": "pwuser", "password": "resetpass3"})[0] == 401)

    print("\n[17] artist profile scraping")
    _, _, artists_data = request(base, "GET", "/api/artists?limit=1000", token=token)
    artist_target = next((a for a in artists_data["items"] if a["name"] == "刘德华"),
                         artists_data["items"][0] if artists_data["items"] else None)
    check("artist list available", bool(artist_target))
    if artist_target:
        artist_id = artist_target["artist_id"]
        status, _, artist_detail = request(base, "GET", f"/api/artists/{artist_id}", token=token)
        check("artist detail has profile fields",
              status == 200 and {"image_url", "bio", "aliases"} <= set(artist_detail),
              str(sorted(artist_detail))[:160])
        status, _, candidates = request(base, "GET", f"/api/artists/{artist_id}/candidates", token=token)
        check("artist candidates endpoint", status == 200 and "items" in candidates, str(status))
        if candidates.get("items"):
            candidate = candidates["items"][0]
            status, _, applied = request(
                base, "POST", f"/api/artists/{artist_id}/scrape",
                {"candidate": candidate}, token,
            )
            check("artist scrape applied",
                  status == 200 and applied.get("meta_source") == candidate["source"],
                  f"status={status} source={applied.get('meta_source')}")
            if applied.get("image_url"):
                status, _, image_bytes = request(base, "GET", applied["image_url"], raw=True)
                check("artist portrait public + served", status == 200 and len(image_bytes) > 500,
                      f"status={status} bytes={len(image_bytes)}")
        status, _, edited = request(base, "PATCH", f"/api/artists/{artist_id}", {
            "bio": "E2E artist biography", "aliases": ["Andy Lau", "华仔"],
        }, token)
        check("artist manual edit",
              status == 200 and edited.get("bio") == "E2E artist biography"
              and "华仔" in (edited.get("aliases") or []), str(edited.get("bio"))[:60])

        export_dir = os.path.join(os.getcwd(), "test-artist-export")
        shutil.rmtree(export_dir, ignore_errors=True)
        os.makedirs(export_dir, exist_ok=True)
        status, _, exported = request(base, "POST", "/api/artists/export",
                                      {"folder": export_dir}, token)
        check("artist images exported", status == 200 and exported.get("exported", 0) >= 1,
              str(exported))
        exported_files = os.listdir(export_dir) if os.path.isdir(export_dir) else []
        check("export name matches Navidrome convention",
              any(os.path.splitext(name)[0] == artist_target["name"] for name in exported_files),
              str(exported_files[:6]))
        shutil.rmtree(export_dir, ignore_errors=True)

    print("\n" + "=" * 60)
    print(f"  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  failures: " + ", ".join(FAIL))
    print("=" * 60)
    return 1 if FAIL else 0


def created_user_token(base: str, username: str, password: str) -> str:
    status, _, data = request(base, "POST", "/api/auth/login",
                              {"username": username, "password": password})
    return data.get("token", "")


if __name__ == "__main__":
    sys.exit(main())
