"""REST API for the web client and third party integrations."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import shutil
import time

from . import apikeys, artist_scrape, db, library, navidrome, providers, scanner, scrape, variables
from .config import config
from .events import publish
from .secretbox import encrypt as encrypt_secret
from .security import (
    ROLES,
    hash_password,
    issue_token,
    subsonic_password,
    token_digest,
    verify_password,
)
from .server import ApiError, Request, Response, route
from .tasks import tasks
from .tags import can_write

SESSION_COOKIE = "omt_session"
MAX_COVER = 8 << 20


def public_user(row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "active": bool(row["active"]),
        "created_at": row["created_at"],
    }


def _session_response(user: dict, token: str) -> Response:
    resp = Response.json({"user": user, "token": token})
    resp.headers["Set-Cookie"] = (
        f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; "
        f"Max-Age={config.session_days * 86400}"
    )
    return resp


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
@route("POST", "/api/auth/login")
def login(req: Request):
    data = req.json()
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    row = db.one("SELECT * FROM users WHERE lower(username) = lower(?)", (username,))
    if not row or not row["active"] or not verify_password(password, row["password_hash"]):
        raise ApiError(401, "invalid username or password")
    token, digest = issue_token(row["id"], row["username"], row["role"])
    db.execute(
        "INSERT INTO sessions(token_hash, user_id, expires_at)"
        " VALUES(?, ?, datetime('now', ?))",
        (digest, row["id"], f"+{config.session_days} days"),
    )
    return _session_response(public_user(row), token)


@route("POST", "/api/auth/logout")
def logout(req: Request):
    token = req.cookies.get(SESSION_COOKIE)
    if token:
        db.execute("DELETE FROM sessions WHERE token_hash = ?", (token_digest(token),))
    resp = Response.json({"ok": True})
    resp.headers["Set-Cookie"] = f"{SESSION_COOKIE}=; Path=/; HttpOnly; Max-Age=0"
    return resp


@route("GET", "/api/auth/me")
def me(req: Request):
    if not req.user:
        raise ApiError(401, "authentication required")
    row = db.one("SELECT * FROM users WHERE id = ?", (req.user["id"],))
    return {"user": public_user(row)}


@route("POST", "/api/auth/password")
def change_password(req: Request):
    req.require("listener")
    data = req.json()
    row = db.one("SELECT * FROM users WHERE id = ?", (req.user["id"],))
    if not verify_password(str(data.get("current") or ""), row["password_hash"]):
        raise ApiError(400, "current password is incorrect")
    new = str(data.get("new") or "")
    if len(new) < 6:
        raise ApiError(400, "new password must be at least 6 characters")
    db.execute(
        "UPDATE users SET password_hash = ?, password_md5 = ?, password_cipher = ? WHERE id = ?",
        (hash_password(new), subsonic_password(new), encrypt_secret(new), row["id"]),
    )
    db.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
    return {"ok": True}


@route("GET", "/api/auth/bootstrap")
def bootstrap(req: Request):
    return {
        "needs_setup": db.scalar("SELECT COUNT(*) FROM users", (), 0) == 0,
        "allow_signup": config.allow_signup,
        "version": config.version,
        "site_name": db.get_setting("site.name", "Music-Doctor"),
    }


# --------------------------------------------------------------------------- #
# users (admin)
# --------------------------------------------------------------------------- #
@route("GET", "/api/users")
def list_users(req: Request):
    req.require("admin")
    rows = db.query("SELECT * FROM users ORDER BY id")
    return {"items": [public_user(row) for row in rows]}


@route("POST", "/api/users")
def create_user(req: Request):
    req.require("admin")
    data = req.json()
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    role = str(data.get("role") or "listener")
    if not username or len(username) < 2:
        raise ApiError(400, "username must be at least 2 characters")
    if len(password) < 6:
        raise ApiError(400, "password must be at least 6 characters")
    if role not in ROLES:
        raise ApiError(400, "invalid role")
    if db.one("SELECT id FROM users WHERE lower(username) = lower(?)", (username,)):
        raise ApiError(409, "username already exists")
    cur = db.execute(
        "INSERT INTO users(username, display_name, password_hash, password_md5, password_cipher, role)"
        " VALUES(?, ?, ?, ?, ?, ?)",
        (username, str(data.get("display_name") or username), hash_password(password),
         subsonic_password(password), encrypt_secret(password), role),
    )
    row = db.one("SELECT * FROM users WHERE id = ?", (cur.lastrowid,))
    return public_user(row)


@route("PATCH", "/api/users/{user_id}")
def update_user(req: Request, user_id: str):
    req.require("admin")
    data = req.json()
    row = db.one("SELECT * FROM users WHERE id = ?", (int(user_id),))
    if not row:
        raise ApiError(404, "user not found")
    updates = []
    values = []
    if "role" in data:
        if data["role"] not in ROLES:
            raise ApiError(400, "invalid role")
        updates.append("role = ?")
        values.append(data["role"])
    if "active" in data:
        updates.append("active = ?")
        values.append(1 if data["active"] else 0)
    if "display_name" in data:
        updates.append("display_name = ?")
        values.append(str(data["display_name"]))
    if data.get("password"):
        password = str(data["password"])
        if len(password) < 6:
            raise ApiError(400, "password must be at least 6 characters")
        updates += ["password_hash = ?", "password_md5 = ?", "password_cipher = ?"]
        values += [hash_password(password), subsonic_password(password), encrypt_secret(password)]
    if row["role"] == "admin" and row["active"]:
        next_role = str(data["role"]) if "role" in data else row["role"]
        next_active = (1 if data["active"] else 0) if "active" in data else row["active"]
        if next_role != "admin" or not next_active:
            others = db.scalar(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1 AND id <> ?",
                (int(user_id),), 0,
            )
            if others == 0:
                raise ApiError(400, "at least one active administrator is required")
    if updates:
        db.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", (*values, int(user_id)))
    if data.get("password"):
        db.execute("DELETE FROM sessions WHERE user_id = ?", (int(user_id),))
    return public_user(db.one("SELECT * FROM users WHERE id = ?", (int(user_id),)))


@route("DELETE", "/api/users/{user_id}")
def delete_user(req: Request, user_id: str):
    req.require("admin")
    row = db.one("SELECT * FROM users WHERE id = ?", (int(user_id),))
    if not row:
        raise ApiError(404, "user not found")
    if int(user_id) == req.user["id"]:
        raise ApiError(400, "you cannot delete your own account")
    if row["role"] == "admin" and row["active"]:
        others = db.scalar(
            "SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1 AND id <> ?",
            (int(user_id),), 0,
        )
        if others == 0:
            raise ApiError(400, "at least one active administrator is required")
    db.execute("DELETE FROM users WHERE id = ?", (int(user_id),))
    return Response.empty()


# --------------------------------------------------------------------------- #
# libraries
# --------------------------------------------------------------------------- #
@route("GET", "/api/libraries")
def list_libraries(req: Request):
    req.require("listener")
    rows = db.query("SELECT * FROM libraries ORDER BY id")
    items = []
    is_admin = req.user["role"] == "admin"
    for row in rows:
        item = dict(row)
        # The encrypted WebDAV credential is only meaningful to administrators
        # who can edit the library; never hand it to plain listeners.
        if not is_admin:
            item.pop("password_cipher", None)
        item["track_count"] = db.scalar(
            "SELECT COUNT(*) FROM tracks WHERE library_id = ? AND missing = 0", (row["id"],), 0
        )
        items.append(item)
    return {"items": items}


@route("POST", "/api/libraries")
def create_library(req: Request):
    req.require("admin")
    data = req.json()
    kind = str(data.get("kind") or "local")
    if kind == "webdav":
        url = str(data.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise ApiError(400, "WebDAV 地址必须以 http:// 或 https:// 开头")
        try:
            item = scanner.add_library(
                str(data.get("name") or ""), url, bool(data.get("auto_scan", True)),
                kind="webdav", url=url, username=str(data.get("username") or ""),
                password_cipher=encrypt_secret(str(data.get("password") or "")),
            )
        except ValueError as exc:
            raise ApiError(400, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise ApiError(409, f"cannot add library: {exc}") from exc
        return item
    path = str(data.get("path") or "").strip()
    if not path:
        raise ApiError(400, "path is required")
    try:
        item = scanner.add_library(str(data.get("name") or ""), path,
                                   bool(data.get("auto_scan", True)))
    except FileNotFoundError as exc:
        raise ApiError(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - UNIQUE violation and friends
        raise ApiError(409, f"cannot add library: {exc}") from exc
    return item


@route("PATCH", "/api/libraries/{library_id}")
def update_library(req: Request, library_id: str):
    req.require("admin")
    data = req.json()
    row = db.one("SELECT * FROM libraries WHERE id = ?", (int(library_id),))
    if not row:
        raise ApiError(404, "library not found")
    updates, values = [], []
    for key in ("name", "enabled", "auto_scan"):
        if key in data:
            updates.append(f"{key} = ?")
            values.append(data[key] if key in ("enabled", "auto_scan") else str(data[key]))
    if row["kind"] == "webdav":
        if "url" in data:
            url = str(data["url"]).strip().rstrip("/") + "/"
            if not url.startswith(("http://", "https://")):
                raise ApiError(400, "WebDAV 地址必须以 http:// 或 https:// 开头")
            updates += ["url = ?", "path = ?"]
            values += [url, url]
        if "username" in data:
            updates.append("username = ?")
            values.append(str(data["username"]))
        if data.get("password"):
            updates.append("password_cipher = ?")
            values.append(encrypt_secret(str(data["password"])))
    if updates:
        db.execute(f"UPDATE libraries SET {', '.join(updates)} WHERE id = ?", (*values, int(library_id)))
    return dict(db.one("SELECT * FROM libraries WHERE id = ?", (int(library_id),)))


@route("DELETE", "/api/libraries/{library_id}")
def delete_library(req: Request, library_id: str):
    req.require("admin")
    db.execute("DELETE FROM libraries WHERE id = ?", (int(library_id),))
    return Response.empty()


def _scan_job(library_id: int, force: bool, user_id: int | None):
    def job(ctx):
        result = scanner.scan_library(library_id, ctx, force=force)
        if db.get_setting("scrape.auto_on_scan", "0") == "1":
            ids = [r["id"] for r in db.query(
                "SELECT id FROM tracks WHERE library_id = ? AND missing = 0 AND tagged = 0"
                " LIMIT 500", (library_id,)
            )]
            if ids:
                ctx.log(f"auto-scrape queued for {len(ids)} untagged tracks")
                result["scraped"] = scrape.batch_scrape(ids, user_id, ctx, only_untagged=True)
        return result

    return job


@route("POST", "/api/libraries/{library_id}/scan")
def scan_library(req: Request, library_id: str):
    req.require("editor")
    force = bool((req.json_raw() or {}).get("force"))
    task_id = tasks.start(
        "scan", f"Scan library #{library_id}", {"library_id": int(library_id), "force": force},
        _scan_job(int(library_id), force, req.user["id"]),
        req.user["id"],
    )
    return {"task_id": task_id}


# --------------------------------------------------------------------------- #
# tracks
# --------------------------------------------------------------------------- #
@route("GET", "/api/tracks")
def list_tracks(req: Request):
    req.require("listener")
    params = dict(req.query)
    params["user_id"] = req.user["id"]
    return library.list_tracks(params)


@route("GET", "/api/tracks/{track_id}")
def get_track(req: Request, track_id: str):
    req.require("listener")
    track = library.get_track(int(track_id), include_lyrics=True)
    if not track:
        raise ApiError(404, "track not found")
    track["favorite"] = bool(db.one(
        "SELECT 1 FROM favorites WHERE track_id = ? AND user_id = ?",
        (track["id"], req.user["id"]),
    ))
    return track


@route("PATCH", "/api/tracks/{track_id}")
def edit_track(req: Request, track_id: str):
    req.require("editor")
    data = req.json()
    fields = {key: value for key, value in data.items()
              if key in library.EDITABLE}
    cover = None
    if data.get("cover_base64"):
        try:
            raw = base64.b64decode(str(data["cover_base64"]).split(",")[-1], validate=True)
        except (ValueError, TypeError) as exc:
            raise ApiError(400, "invalid cover data") from exc
        if len(raw) > MAX_COVER:
            raise ApiError(413, "cover image too large")
        mime = str(data.get("cover_mime") or "image/jpeg")
        cover = (mime, raw)
    try:
        track = library.update_track(
            int(track_id), fields, user_id=req.user["id"], action="edit",
            write_file=not config.read_only, cover=cover,
            remove_cover=bool(data.get("remove_cover")),
        )
    except RuntimeError as exc:
        raise ApiError(409, str(exc)) from exc
    return track


@route("DELETE", "/api/tracks/{track_id}")
def delete_track(req: Request, track_id: str):
    req.require("admin")
    delete_file = req.arg("file") == "1"
    row = db.one("SELECT * FROM tracks WHERE id = ?", (int(track_id),))
    if not row:
        raise ApiError(404, "track not found")
    if delete_file and os.path.exists(row["path"]):
        trash = config.data_dir / "trash" / time.strftime("%Y%m%d-%H%M%S")
        trash.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(row["path"], trash / f"{int(track_id)}_{os.path.basename(row['path'])}")
        except OSError as exc:
            raise ApiError(409, f"cannot move file to trash: {exc}") from exc
    db.execute("DELETE FROM tracks WHERE id = ?", (int(track_id),))
    publish("track.deleted", track_id=int(track_id))
    return Response.empty()


@route("GET", "/api/tracks/{track_id}/stream")
def stream_track(req: Request, track_id: str):
    if not req.user:
        raise ApiError(401, "authentication required")
    row = db.one("SELECT path, library_id FROM tracks WHERE id = ?", (int(track_id),))
    if not row:
        raise ApiError(404, "track not found")
    if library.is_remote(row["path"]):
        return _remote_response(row)
    if not os.path.isfile(row["path"]):
        raise ApiError(404, "file not found")
    return Response.stream(row["path"])


def _remote_response(row, download: bool = False) -> Response:
    from . import webdav
    library_row = db.one("SELECT * FROM libraries WHERE id = ?", (row["library_id"],))
    if not library_row:
        raise ApiError(404, "library not found")
    headers = webdav.library_headers(library_row)
    mime = mimetypes.guess_type(row["path"])[0] or "audio/mpeg"
    resp = Response.remote_stream(row["path"], headers, mime)
    if download:
        resp.headers["Content-Disposition"] = (
            f'attachment; filename="{library.safe_filename(os.path.basename(row["path"]))}"'
        )
    return resp


@route("GET", "/api/tracks/{track_id}/download")
def download_track(req: Request, track_id: str):
    req.require("listener")
    row = db.one("SELECT path, library_id FROM tracks WHERE id = ?", (int(track_id),))
    if not row:
        raise ApiError(404, "track not found")
    if library.is_remote(row["path"]):
        return _remote_response(row, download=True)
    if not os.path.isfile(row["path"]):
        raise ApiError(404, "file not found")
    resp = Response.stream(row["path"])
    resp.headers["Content-Disposition"] = (
        f'attachment; filename="{library.safe_filename(os.path.basename(row["path"]))}"'
    )
    return resp


def _cover_to_base64(cover_hash: str) -> dict:
    cover = library.load_cover(cover_hash) if cover_hash else None
    if not cover:
        return {"cover_base64": "", "cover_mime": ""}
    import mimetypes  # local import keeps module import light
    return {
        "cover_base64": base64.b64encode(cover[1]).decode("ascii"),
        "cover_mime": cover[0] or mimetypes.guess_type("x.jpg")[0],
    }


@route("GET", "/api/tracks/{track_id}/candidates")
def track_candidates(req: Request, track_id: str):
    req.require("editor")
    names = [n for n in (req.arg("providers") or "").split(",") if n]
    return {"items": scrape.search(track_id=int(track_id), names=names or None)}


@route("POST", "/api/tracks/{track_id}/scrape")
def apply_scrape(req: Request, track_id: str):
    req.require("editor")
    data = req.json()
    candidate = data.get("candidate") or data
    if not candidate.get("title"):
        raise ApiError(400, "candidate must include a title")
    try:
        track = scrape.apply_candidate(
            int(track_id), candidate, req.user["id"],
            with_cover=bool(data.get("with_cover", True)),
            with_lyrics=bool(data.get("with_lyrics", True)),
        )
    except RuntimeError as exc:
        raise ApiError(409, str(exc)) from exc
    return track


@route("POST", "/api/tracks/{track_id}/auto-scrape")
def auto_scrape_track(req: Request, track_id: str):
    req.require("editor")
    track = library.get_track(int(track_id))
    if not track:
        raise ApiError(404, "track not found")
    try:
        result = scrape.auto_scrape_track(track, req.user["id"])
    except RuntimeError as exc:
        raise ApiError(409, str(exc)) from exc
    if not result.get("matched"):
        raise ApiError(409, result.get("reason") or "no confident match")
    return result


@route("POST", "/api/tracks/{track_id}/lyrics")
def fetch_track_lyrics(req: Request, track_id: str):
    req.require("editor")
    track = library.get_track(int(track_id))
    if not track:
        raise ApiError(404, "track not found")
    lyrics = scrape.fetch_lyrics(scrape.track_query(track))
    if not lyrics:
        raise ApiError(404, "no lyrics found")
    return library.update_track(int(track_id), {"lyrics": lyrics}, req.user["id"], "lyrics")


@route("POST", "/api/tracks/{track_id}/favorite")
def toggle_favorite(req: Request, track_id: str):
    req.require("listener")
    exists = db.one(
        "SELECT 1 FROM favorites WHERE track_id = ? AND user_id = ?",
        (int(track_id), req.user["id"]),
    )
    if exists:
        db.execute("DELETE FROM favorites WHERE track_id = ? AND user_id = ?",
                   (int(track_id), req.user["id"]))
        favorite = False
    else:
        db.execute("INSERT OR IGNORE INTO favorites(track_id, user_id) VALUES(?, ?)",
                   (int(track_id), req.user["id"]))
        favorite = True
    publish("favorite", track_id=int(track_id), user_id=req.user["id"], favorite=favorite)
    return {"favorite": favorite}


@route("POST", "/api/tracks/{track_id}/play")
def record_play(req: Request, track_id: str):
    req.require("listener")
    data = req.json_raw()
    library.record_play(
        int(track_id), req.user["id"], str(data.get("client") or "web"),
        int(data.get("ms_played") or 0), bool(data.get("completed")),
    )
    return {"ok": True}


@route("GET", "/api/tracks/{track_id}/versions")
def track_versions(req: Request, track_id: str):
    req.require("listener")
    return {"items": library.versions(int(track_id))}


@route("POST", "/api/tracks/{track_id}/versions/{version_id}/restore")
def restore_track_version(req: Request, track_id: str, version_id: str):
    req.require("editor")
    try:
        return library.restore_version(int(version_id), req.user["id"])
    except RuntimeError as exc:
        raise ApiError(409, str(exc)) from exc


@route("POST", "/api/tracks/{track_id}/rename")
def rename_track(req: Request, track_id: str):
    req.require("editor")
    template = str(req.json_raw().get("template") or db.get_setting("naming.template", ""))
    try:
        variables.validate(template)
    except variables.TemplateError as exc:
        raise ApiError(400, f"invalid template: {exc}") from exc
    try:
        return library.rename_track(int(track_id), template, req.user["id"])
    except FileExistsError as exc:
        raise ApiError(409, str(exc)) from exc
    except ValueError as exc:
        raise ApiError(400, str(exc)) from exc


@route("POST", "/api/tracks/batch")
def batch_action(req: Request):
    req.require("editor")
    data = req.json()
    ids = [int(i) for i in (data.get("ids") or [])][:2000]
    action = str(data.get("action") or "")
    if not ids:
        raise ApiError(400, "no tracks selected")
    if action == "scrape":
        names = data.get("providers") or None
        if names:
            db.set_setting("scrape.providers", json.dumps(names))
        task_id = tasks.start(
            "scrape", f"Scrape {len(ids)} tracks", {"ids": ids},
            lambda ctx: scrape.batch_scrape(ids, req.user["id"], ctx,
                                           only_untagged=bool(data.get("only_untagged"))),
            req.user["id"],
        )
        return {"task_id": task_id}
    if action == "edit":
        fields = {k: v for k, v in (data.get("fields") or {}).items() if k in library.EDITABLE}
        if not fields:
            raise ApiError(400, "no fields to apply")
        updated = 0
        errors = []
        for track_id in ids:
            try:
                library.update_track(track_id, fields, req.user["id"], "batch-edit")
                updated += 1
            except Exception as exc:  # noqa: BLE001
                errors.append({"id": track_id, "error": str(exc)})
        return {"updated": updated, "errors": errors}
    if action == "reindex":
        count = 0
        for track_id in ids:
            row = db.one("SELECT * FROM tracks WHERE id = ?", (track_id,))
            if row:
                library.index_file(row["library_id"], row["path"], row["rel_path"])
                count += 1
        return {"reindexed": count}
    if action == "delete":
        return delete_many(req, ids, bool(data.get("delete_files")))
    if action == "rename":
        template = str(data.get("template") or "")
        if not template:
            raise ApiError(400, "template is required")
        try:
            variables.validate(template)
        except variables.TemplateError as exc:
            raise ApiError(400, f"invalid template: {exc}") from exc
        renamed, errors = 0, []
        for track_id in ids:
            try:
                library.rename_track(track_id, template, req.user["id"])
                renamed += 1
            except Exception as exc:  # noqa: BLE001
                errors.append({"id": track_id, "error": str(exc)})
        return {"renamed": renamed, "errors": errors}
    raise ApiError(400, f"unknown batch action: {action}")


@route("POST", "/api/tracks/rename/preview")
def preview_rename(req: Request):
    req.require("editor")
    data = req.json()
    ids = [int(i) for i in (data.get("ids") or [])][:500]
    template = str(data.get("template") or "")
    if not ids:
        raise ApiError(400, "no tracks selected")
    if not template:
        raise ApiError(400, "template is required")
    try:
        variables.validate(template)
    except variables.TemplateError as exc:
        raise ApiError(400, f"invalid template: {exc}") from exc
    items = []
    for track_id in ids:
        track = library.get_track(track_id)
        if not track:
            continue
        if library.is_remote(track["path"]):
            items.append({"id": track_id, "from": track["rel_path"], "to": "",
                          "error": "远程文件不支持重命名"})
            continue
        name = library.safe_filename(variables.render(template, variables.context_for(track)))
        ext = os.path.splitext(track["path"])[1]
        target = os.path.join(os.path.dirname(track["path"]), name + ext) if name else ""
        conflict = bool(target and target != track["path"] and os.path.exists(target))
        items.append({
            "id": track_id,
            "from": os.path.basename(track["path"]),
            "to": (name + ext) if name else "",
            "conflict": conflict,
            "error": "" if name else "模板输出为空",
        })
    return {"items": items}


# --------------------------------------------------------------------------- #
# API keys (scripts, MCP, home automation)
# --------------------------------------------------------------------------- #
@route("GET", "/api/apikeys")
def list_api_keys(req: Request):
    req.require("listener")
    return {"items": apikeys.list_for(req.user["id"])}


@route("POST", "/api/apikeys")
def create_api_key(req: Request):
    req.require("listener")
    data = req.json()
    item, raw = apikeys.create(req.user["id"], str(data.get("name") or "api key"))
    return {"key": raw, "item": item}


@route("DELETE", "/api/apikeys/{key_id}")
def delete_api_key(req: Request, key_id: str):
    req.require("listener")
    if not apikeys.revoke(req.user["id"], int(key_id)):
        raise ApiError(404, "key not found")
    return Response.empty()


# --------------------------------------------------------------------------- #
# scrape report
# --------------------------------------------------------------------------- #
@route("GET", "/api/reports/scrape")
def scrape_report(req: Request):
    req.require("listener")
    days = min(max(int(req.arg("days") or 14), 1), 365)
    since = f"-{days} days"
    rows = db.query(
        "SELECT date(created_at) AS day, COALESCE(NULLIF(source, ''), 'unknown') AS source,"
        " COUNT(*) AS count FROM meta_versions WHERE action = 'scrape'"
        " AND created_at >= datetime('now', ?) GROUP BY day, source ORDER BY day DESC",
        (since,),
    )
    by_day: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for row in rows:
        by_day[row["day"]] = by_day.get(row["day"], 0) + row["count"]
        by_source[row["source"]] = by_source.get(row["source"], 0) + row["count"]
    failed = db.query(
        "SELECT id, title, kind, error, finished_at FROM tasks WHERE status = 'failed'"
        " AND finished_at >= datetime('now', ?) ORDER BY id DESC LIMIT 10", (since,)
    )
    return {
        "days": days,
        "total": sum(by_day.values()),
        "by_day": [{"day": day, "count": count} for day, count in sorted(by_day.items())],
        "by_source": [{"source": source, "count": count}
                      for source, count in sorted(by_source.items(), key=lambda kv: -kv[1])],
        "untagged": db.scalar("SELECT COUNT(*) FROM tracks WHERE missing = 0 AND tagged = 0", (), 0),
        "duplicates": len(library.duplicate_groups()),
        "failed_tasks": [dict(row) for row in failed],
    }


def delete_many(req: Request, ids: list[int], delete_files: bool) -> dict:
    req.require("admin")
    moved = deleted = 0
    trash = config.data_dir / "trash" / time.strftime("%Y%m%d-%H%M%S")
    for track_id in ids:
        row = db.one("SELECT path FROM tracks WHERE id = ?", (track_id,))
        if not row:
            continue
        if delete_files and os.path.exists(row["path"]):
            trash.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(row["path"], trash / f"{track_id}_{os.path.basename(row['path'])}")
                moved += 1
            except OSError:
                pass
        db.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
        deleted += 1
        publish("track.deleted", track_id=track_id)
    return {"deleted": deleted, "files_moved": moved}


# --------------------------------------------------------------------------- #
# albums / artists / covers / search
# --------------------------------------------------------------------------- #
@route("GET", "/api/albums")
def list_albums(req: Request):
    req.require("listener")
    return library.list_albums(dict(req.query))


@route("GET", "/api/albums/{album_id}")
def get_album(req: Request, album_id: str):
    req.require("listener")
    album = library.album_detail(album_id, req.user["id"])
    if not album:
        raise ApiError(404, "album not found")
    return album


@route("GET", "/api/artists/{artist_id}/candidates")
def artist_candidates(req: Request, artist_id: str):
    req.require("editor")
    row = db.one("SELECT name FROM v_artists WHERE artist_id = ?", (artist_id,))
    if not row:
        raise ApiError(404, "artist not found")
    names = [name for name in (req.arg("providers") or "").split(",") if name]
    return {"items": artist_scrape.search(row["name"], names or None)}


@route("POST", "/api/artists/export")
def export_artist_images(req: Request):
    req.require("editor")
    data = req.json_raw()
    try:
        result = artist_scrape.export_images(str(data.get("folder") or ""))
    except (FileNotFoundError, ValueError) as exc:
        raise ApiError(400, str(exc)) from exc
    # Navidrome caches "no art" per artist: new files only show up after a recompute.
    result["navidrome"] = navidrome.after_export(
        int(result.get("exported") or 0), req.user["id"], reason="export artist images"
    )
    return result


@route("POST", "/api/artists/batch/scrape")
def batch_artist_scrape(req: Request):
    req.require("editor")
    data = req.json()
    ids = [str(item) for item in (data.get("ids") or []) if item]
    if not ids:
        ids = artist_scrape.missing_portraits(int(data.get("limit") or 300))
    if not ids:
        raise ApiError(400, "没有需要刮削的艺术家")
    task_id = tasks.start(
        "artist-scrape", f"刮削 {len(ids)} 位艺术家", {"ids": ids},
        lambda ctx: artist_scrape.batch_scrape(ids, req.user["id"], ctx),
        req.user["id"],
    )
    return {"task_id": task_id, "total": len(ids)}


@route("POST", "/api/artists/{artist_id}/scrape")
def scrape_artist(req: Request, artist_id: str):
    req.require("editor")
    data = req.json()
    candidate = data.get("candidate") or {}
    exported = 0
    if candidate.get("name"):
        try:
            artist_scrape.apply_candidate(artist_id, candidate, req.user["id"])
        except Exception as exc:  # noqa: BLE001
            raise ApiError(409, f"刮削失败：{exc}") from exc
        exported = artist_scrape.export_one(artist_id)
    else:
        result = artist_scrape.auto_scrape(artist_id, req.user["id"])
        if not result.get("matched"):
            raise ApiError(404, result.get("reason") or "没有找到匹配的艺术家资料")
        exported = int(result.get("exported") or 0)
    artist = library.artist_detail(artist_id, req.user["id"])
    if not artist:
        raise ApiError(404, "artist not found")
    artist["navidrome"] = navidrome.after_export(
        exported, req.user["id"], reason="scrape artist"
    )
    return artist


@route("PATCH", "/api/artists/{artist_id}")
def edit_artist(req: Request, artist_id: str):
    req.require("editor")
    data = req.json()
    image_hash = None
    if data.get("image_base64"):
        try:
            raw = base64.b64decode(str(data["image_base64"]).split(",")[-1], validate=True)
        except (ValueError, TypeError) as exc:
            raise ApiError(400, "invalid image data") from exc
        if len(raw) > MAX_COVER:
            raise ApiError(413, "image too large")
        image_hash = library.store_cover(raw, str(data.get("image_mime") or "image/jpeg"))
    aliases = data.get("aliases")
    if isinstance(aliases, str):
        aliases = [part.strip() for part in aliases.split(",") if part.strip()]
    artist_scrape.save_meta(
        artist_id,
        name=str(data["name"]).strip() if "name" in data else None,
        bio=str(data["bio"]) if "bio" in data else None,
        image_hash=image_hash,
        aliases=aliases if isinstance(aliases, list) else None,
        source="manual",
    )
    artist = library.artist_detail(artist_id, req.user["id"])
    if not artist:
        raise ApiError(404, "artist not found")
    return artist


@route("GET", "/api/artists")
def list_artists(req: Request):
    req.require("listener")
    return library.list_artists(dict(req.query))


@route("GET", "/api/artists/{artist_id}")
def get_artist(req: Request, artist_id: str):
    req.require("listener")
    artist = library.artist_detail(artist_id, req.user["id"])
    if not artist:
        raise ApiError(404, "artist not found")
    return artist


@route("GET", "/api/covers/{cover_hash}")
def get_cover(req: Request, cover_hash: str):
    # Public on purpose: cover art is not sensitive, and Subsonic clients fetch
    # artist/album images without a session cookie.
    cover = library.load_cover(cover_hash)
    if not cover:
        raise ApiError(404, "cover not found")
    resp = Response(200, cover[1], cover[0])
    resp.headers["Cache-Control"] = "public, max-age=604800, immutable"
    return resp


@route("GET", "/api/search")
def search_all(req: Request):
    req.require("listener")
    q = (req.arg("q") or "").strip()
    if len(q) < 1:
        return {"tracks": [], "albums": [], "artists": []}
    return {
        "tracks": library.list_tracks({"q": q, "limit": 20})["items"],
        "albums": library.list_albums({"q": q, "limit": 12})["items"],
        "artists": library.list_artists({"q": q, "limit": 12})["items"],
    }


# --------------------------------------------------------------------------- #
# library stats / history / settings
# --------------------------------------------------------------------------- #
@route("GET", "/api/stats")
def get_stats(req: Request):
    req.require("listener")
    data = library.stats()
    recent_plays = db.query(
        "SELECT h.played_at, h.user_id, u.username, t.id, t.title, t.artist, t.album_id,"
        " t.cover_hash FROM play_history h JOIN tracks t ON t.id = h.track_id"
        " LEFT JOIN users u ON u.id = h.user_id ORDER BY h.id DESC LIMIT 12"
    )
    data["recent_plays"] = [dict(row) for row in recent_plays]
    data["libraries"] = [
        dict(row) for row in db.query("SELECT * FROM libraries ORDER BY id")
    ]
    return data


@route("GET", "/api/history")
def get_history(req: Request):
    req.require("listener")
    limit = min(int(req.arg("limit") or 50), 500)
    rows = db.query(
        "SELECT h.*, t.title, t.artist, t.album_id, t.cover_hash, u.username"
        " FROM play_history h JOIN tracks t ON t.id = h.track_id"
        " LEFT JOIN users u ON u.id = h.user_id ORDER BY h.id DESC LIMIT ?", (limit,)
    )
    return {"items": [dict(row) for row in rows]}


@route("GET", "/api/settings")
def get_settings(req: Request):
    req.require("listener")
    rows = db.query("SELECT key, value FROM settings ORDER BY key")
    return {"items": {row["key"]: row["value"] for row in rows}}


@route("PUT", "/api/settings")
def put_settings(req: Request):
    req.require("admin")
    data = req.json()
    if "naming.template" in data:
        try:
            variables.validate(str(data["naming.template"]))
        except variables.TemplateError as exc:
            raise ApiError(400, f"invalid template: {exc}") from exc
    for key, value in data.items():
        if key.startswith("_") or key in ("secret",):
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        db.set_setting(str(key)[:120], str(value))
    return {"items": {row["key"]: row["value"] for row in db.query("SELECT key, value FROM settings")}}


# --------------------------------------------------------------------------- #
# tasks
# --------------------------------------------------------------------------- #
@route("GET", "/api/tasks")
def list_tasks(req: Request):
    req.require("listener")
    rows = db.query(
        "SELECT id, kind, title, status, progress, total, done, error, created_by,"
        " created_at, started_at, finished_at FROM tasks ORDER BY id DESC LIMIT ?",
        (min(int(req.arg("limit") or 50), 300),),
    )
    return {"items": [dict(row) for row in rows]}


@route("GET", "/api/tasks/{task_id}")
def get_task(req: Request, task_id: str):
    req.require("listener")
    row = db.one("SELECT * FROM tasks WHERE id = ?", (int(task_id),))
    if not row:
        raise ApiError(404, "task not found")
    data = dict(row)
    data["payload"] = json.loads(data.pop("payload") or "{}")
    data["result"] = json.loads(data.pop("result") or "{}")
    data["logs"] = [dict(r) for r in db.query(
        "SELECT ts, level, message FROM task_logs WHERE task_id = ? ORDER BY id", (int(task_id),)
    )]
    return data


@route("POST", "/api/tasks/{task_id}/cancel")
def cancel_task(req: Request, task_id: str):
    req.require("editor")
    if not tasks.cancel(int(task_id)):
        raise ApiError(400, "task is not running")
    return {"ok": True}


# --------------------------------------------------------------------------- #
# playlists
# --------------------------------------------------------------------------- #
def _playlist_row(row) -> dict:
    item = dict(row)
    item["rules"] = json.loads(item.get("rules") or "{}")
    return item


@route("GET", "/api/playlists")
def list_playlists(req: Request):
    req.require("listener")
    rows = db.query(
        "SELECT p.*, (SELECT COUNT(*) FROM playlist_items WHERE playlist_id = p.id) AS count"
        " FROM playlists p WHERE p.owner_id = ? OR p.public = 1 ORDER BY p.id DESC",
        (req.user["id"],),
    )
    return {"items": [_playlist_row(row) for row in rows]}


@route("POST", "/api/playlists")
def create_playlist(req: Request):
    req.require("listener")
    data = req.json()
    name = str(data.get("name") or "").strip()
    if not name:
        raise ApiError(400, "name is required")
    kind = "smart" if data.get("kind") == "smart" else "static"
    rules = data.get("rules") or {}
    cur = db.execute(
        "INSERT INTO playlists(owner_id, name, kind, rules, description, public)"
        " VALUES(?, ?, ?, ?, ?, ?)",
        (req.user["id"], name, kind, json.dumps(rules, ensure_ascii=False),
         str(data.get("description") or ""), 1 if data.get("public") else 0),
    )
    return _playlist_row(db.one("SELECT * FROM playlists WHERE id = ?", (cur.lastrowid,)))


@route("GET", "/api/playlists/{playlist_id}")
def get_playlist(req: Request, playlist_id: str):
    req.require("listener")
    row = db.one("SELECT * FROM playlists WHERE id = ?", (int(playlist_id),))
    if not row:
        raise ApiError(404, "playlist not found")
    if row["owner_id"] != req.user["id"] and not row["public"] and req.user["role"] != "admin":
        raise ApiError(403, "playlist is private")
    playlist = _playlist_row(row)
    if playlist["kind"] == "smart":
        playlist["tracks"] = library.smart_tracks(playlist["rules"])
    else:
        rows = db.query(
            "SELECT t.* FROM playlist_items i JOIN tracks t ON t.id = i.track_id"
            " WHERE i.playlist_id = ? ORDER BY i.position, i.track_id",
            (int(playlist_id),),
        )
        playlist["tracks"] = [library.track_dict(r) for r in rows]
    return playlist


@route("PATCH", "/api/playlists/{playlist_id}")
def update_playlist(req: Request, playlist_id: str):
    req.require("listener")
    row = db.one("SELECT * FROM playlists WHERE id = ?", (int(playlist_id),))
    if not row:
        raise ApiError(404, "playlist not found")
    if row["owner_id"] != req.user["id"] and req.user["role"] != "admin":
        raise ApiError(403, "only the owner can edit this playlist")
    data = req.json()
    updates, values = [], []
    if "name" in data:
        updates.append("name = ?")
        values.append(str(data["name"]))
    if "description" in data:
        updates.append("description = ?")
        values.append(str(data["description"]))
    if "public" in data:
        updates.append("public = ?")
        values.append(1 if data["public"] else 0)
    if "rules" in data:
        updates.append("rules = ?")
        values.append(json.dumps(data["rules"] or {}, ensure_ascii=False))
    if updates:
        db.execute(
            f"UPDATE playlists SET {', '.join(updates)}, updated_at = datetime('now') WHERE id = ?",
            (*values, int(playlist_id)),
        )
    return get_playlist(req, playlist_id)


@route("DELETE", "/api/playlists/{playlist_id}")
def delete_playlist(req: Request, playlist_id: str):
    req.require("listener")
    row = db.one("SELECT * FROM playlists WHERE id = ?", (int(playlist_id),))
    if not row:
        raise ApiError(404, "playlist not found")
    if row["owner_id"] != req.user["id"] and req.user["role"] != "admin":
        raise ApiError(403, "only the owner can delete this playlist")
    db.execute("DELETE FROM playlists WHERE id = ?", (int(playlist_id),))
    return Response.empty()


@route("POST", "/api/playlists/{playlist_id}/tracks")
def add_playlist_tracks(req: Request, playlist_id: str):
    req.require("listener")
    row = db.one("SELECT * FROM playlists WHERE id = ?", (int(playlist_id),))
    if not row:
        raise ApiError(404, "playlist not found")
    if row["owner_id"] != req.user["id"] and req.user["role"] != "admin":
        raise ApiError(403, "only the owner can edit this playlist")
    data = req.json()
    ids = [int(i) for i in (data.get("ids") or [])]
    if not ids:
        raise ApiError(400, "no tracks selected")
    position = db.scalar(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM playlist_items WHERE playlist_id = ?",
        (int(playlist_id),), 0,
    )
    for track_id in ids:
        db.execute(
            "INSERT OR IGNORE INTO playlist_items(playlist_id, track_id, position) VALUES(?, ?, ?)",
            (int(playlist_id), track_id, position),
        )
        position += 1
    db.execute("UPDATE playlists SET updated_at = datetime('now') WHERE id = ?", (int(playlist_id),))
    return get_playlist(req, playlist_id)


def _editable_playlist(req: Request, playlist_id: str):
    row = db.one("SELECT * FROM playlists WHERE id = ?", (int(playlist_id),))
    if not row:
        raise ApiError(404, "playlist not found")
    if row["owner_id"] != req.user["id"] and req.user["role"] != "admin":
        raise ApiError(403, "only the owner can edit this playlist")
    return row


@route("DELETE", "/api/playlists/{playlist_id}/tracks/{track_id}")
def remove_playlist_track(req: Request, playlist_id: str, track_id: str):
    req.require("listener")
    _editable_playlist(req, playlist_id)
    db.execute("DELETE FROM playlist_items WHERE playlist_id = ? AND track_id = ?",
               (int(playlist_id), int(track_id)))
    db.execute("UPDATE playlists SET updated_at = datetime('now') WHERE id = ?", (int(playlist_id),))
    return get_playlist(req, playlist_id)


@route("POST", "/api/playlists/{playlist_id}/reorder")
def reorder_playlist(req: Request, playlist_id: str):
    req.require("listener")
    _editable_playlist(req, playlist_id)
    data = req.json()
    ids = [int(i) for i in (data.get("ids") or [])]
    if not ids:
        raise ApiError(400, "no tracks selected")
    known = [row["track_id"] for row in db.query(
        "SELECT track_id FROM playlist_items WHERE playlist_id = ? ORDER BY position, track_id",
        (int(playlist_id),),
    )]
    unknown = [track_id for track_id in ids if track_id not in set(known)]
    if unknown:
        raise ApiError(400, "tracks are not part of this playlist")
    ordered = set(ids)
    for position, track_id in enumerate(ids):
        db.execute(
            "UPDATE playlist_items SET position = ? WHERE playlist_id = ? AND track_id = ?",
            (position, int(playlist_id), track_id),
        )
    # Keep the untouched tail of the list behind the reordered ones.
    position = len(ids)
    for track_id in known:
        if track_id in ordered:
            continue
        db.execute(
            "UPDATE playlist_items SET position = ? WHERE playlist_id = ? AND track_id = ?",
            (position, int(playlist_id), track_id),
        )
        position += 1
    db.execute("UPDATE playlists SET updated_at = datetime('now') WHERE id = ?", (int(playlist_id),))
    return get_playlist(req, playlist_id)


# --------------------------------------------------------------------------- #
# duplicates
# --------------------------------------------------------------------------- #
@route("GET", "/api/duplicates")
def duplicates(req: Request):
    req.require("listener")
    groups = library.duplicate_groups()
    for group in groups:
        for track in group.get("tracks", []):
            track["keep"] = track["id"] == group["keep_id"]
    return {"items": groups, "total": len(groups)}


@route("POST", "/api/duplicates/resolve")
def resolve_duplicates(req: Request):
    req.require("admin")
    data = req.json()
    ids = [int(i) for i in (data.get("ids") or [])]
    if not ids:
        raise ApiError(400, "no tracks selected")
    return delete_many(req, ids, bool(data.get("delete_files")))


# --------------------------------------------------------------------------- #
# providers / plugins
# --------------------------------------------------------------------------- #
@route("GET", "/api/providers")
def list_providers(req: Request):
    req.require("listener")
    enabled = providers.registry.enabled()
    items = []
    for provider in providers.registry.all():
        items.append({
            "name": provider.name,
            "label": provider.label,
            "needs_network": provider.needs_network,
            "enabled": provider.name in enabled,
            "custom": type(provider).__name__ == "JsonPluginProvider",
        })
    return {"items": items}


@route("POST", "/api/providers/test")
def test_provider(req: Request):
    req.require("editor")
    data = req.json()
    query = {
        "title": str(data.get("title") or ""),
        "artist": str(data.get("artist") or ""),
        "album": str(data.get("album") or ""),
        "path": str(data.get("path") or ""),
        "duration": float(data.get("duration") or 0),
    }
    return {"items": scrape.search(query=query, names=[data["provider"]] if data.get("provider") else None)}


@route("POST", "/api/providers/plugins")
def add_plugin(req: Request):
    req.require("admin")
    spec = req.json()
    name = str(spec.get("name") or "").strip()
    if not name or not (spec.get("search") or {}).get("url"):
        raise ApiError(400, "plugin needs a name and search.url")
    plugin_dir = config.data_dir / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_")
    if not safe:
        raise ApiError(400, "plugin name must be alphanumeric")
    (plugin_dir / f"{safe}.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    providers.registry.load_plugins()
    db.execute(
        "INSERT INTO tag_sources(name, kind, enabled, config, builtin) VALUES(?, 'json', 1, ?, 0)"
        " ON CONFLICT(name) DO UPDATE SET config = excluded.config",
        (safe, json.dumps(spec, ensure_ascii=False)),
    )
    return {"ok": True, "name": safe}


# --------------------------------------------------------------------------- #
# play queues + listening rooms
# --------------------------------------------------------------------------- #
@route("GET", "/api/queue")
def get_queue(req: Request):
    req.require("listener")
    row = db.one("SELECT * FROM play_queues WHERE user_id = ?", (req.user["id"],))
    if not row:
        return {"items": [], "current": 0, "position_ms": 0, "updated_at": None}
    return {
        "items": json.loads(row["items"] or "[]"),
        "current": row["current"],
        "position_ms": row["position_ms"],
        "updated_at": row["updated_at"],
    }


@route("PUT", "/api/queue")
def put_queue(req: Request):
    req.require("listener")
    data = req.json()
    items = [int(i) for i in (data.get("items") or [])][:5000]
    db.execute(
        "INSERT INTO play_queues(user_id, items, current, position_ms, updated_at)"
        " VALUES(?, ?, ?, ?, datetime('now')) ON CONFLICT(user_id) DO UPDATE SET"
        " items = excluded.items, current = excluded.current,"
        " position_ms = excluded.position_ms, updated_at = datetime('now')",
        (req.user["id"], json.dumps(items), int(data.get("current") or 0),
         int(data.get("position_ms") or 0)),
    )
    publish("queue", user_id=req.user["id"])
    return {"ok": True}


def _room_dict(row) -> dict:
    item = dict(row)
    try:
        item["state"] = json.loads(item["state"] or "{}")
    except json.JSONDecodeError:
        item["state"] = {}
    return item


@route("GET", "/api/rooms")
def list_rooms(req: Request):
    req.require("listener")
    rows = db.query(
        "SELECT r.*, (SELECT COUNT(*) FROM room_members m WHERE m.room_id = r.id) AS members"
        " FROM rooms r ORDER BY r.id DESC LIMIT 50"
    )
    return {"items": [_room_dict(row) for row in rows]}


@route("POST", "/api/rooms")
def create_room(req: Request):
    req.require("listener")
    data = req.json()
    import secrets
    code = secrets.token_hex(3).upper()
    cur = db.execute(
        "INSERT INTO rooms(code, name, owner_id, state) VALUES(?, ?, ?, ?)",
        (code, str(data.get("name") or f"{req.user['username']}'s room"), req.user["id"],
         json.dumps({"playing": False, "track_id": None, "position_ms": 0, "updated_by": None})),
    )
    db.execute("INSERT OR IGNORE INTO room_members(room_id, user_id) VALUES(?, ?)",
               (cur.lastrowid, req.user["id"]))
    return _room_dict(db.one("SELECT * FROM rooms WHERE id = ?", (cur.lastrowid,)))


@route("POST", "/api/rooms/join")
def join_room(req: Request):
    req.require("listener")
    code = str(req.json_raw().get("code") or "").upper().strip()
    row = db.one("SELECT * FROM rooms WHERE code = ?", (code,))
    if not row:
        raise ApiError(404, "room not found")
    db.execute(
        "INSERT INTO room_members(room_id, user_id, last_seen) VALUES(?, ?, datetime('now'))"
        " ON CONFLICT(room_id, user_id) DO UPDATE SET last_seen = datetime('now')",
        (row["id"], req.user["id"]),
    )
    return _room_dict(row)


@route("GET", "/api/rooms/{code}")
def get_room(req: Request, code: str):
    req.require("listener")
    row = db.one("SELECT * FROM rooms WHERE code = ?", (code.upper(),))
    if not row:
        raise ApiError(404, "room not found")
    item = _room_dict(row)
    item["members"] = [dict(r) for r in db.query(
        "SELECT m.user_id, u.username, m.last_seen FROM room_members m"
        " LEFT JOIN users u ON u.id = m.user_id WHERE m.room_id = ? ORDER BY m.last_seen DESC",
        (row["id"],),
    )]
    return item


@route("DELETE", "/api/rooms/{code}")
def delete_room(req: Request, code: str):
    req.require("listener")
    row = db.one("SELECT * FROM rooms WHERE code = ?", (code.upper(),))
    if not row:
        raise ApiError(404, "room not found")
    if row["owner_id"] != req.user["id"] and req.user["role"] != "admin":
        raise ApiError(403, "only the owner can close this room")
    db.execute("DELETE FROM room_members WHERE room_id = ?", (row["id"],))
    db.execute("DELETE FROM rooms WHERE id = ?", (row["id"],))
    publish("room.deleted", code=row["code"])
    return Response.empty()


@route("PUT", "/api/rooms/{code}/state")
def put_room_state(req: Request, code: str):
    req.require("listener")
    row = db.one("SELECT * FROM rooms WHERE code = ?", (code.upper(),))
    if not row:
        raise ApiError(404, "room not found")
    if not db.one("SELECT 1 FROM room_members WHERE room_id = ? AND user_id = ?",
                  (row["id"], req.user["id"])):
        raise ApiError(403, "join the room before controlling playback")
    data = req.json()
    state = json.loads(row["state"] or "{}")
    for key in ("playing", "track_id", "position_ms"):
        if key in data:
            state[key] = data[key]
    state["updated_by"] = req.user["username"]
    state["updated_at"] = int(time.time() * 1000)
    db.execute("UPDATE rooms SET state = ?, updated_at = datetime('now') WHERE id = ?",
               (json.dumps(state), row["id"]))
    publish("room.state", code=row["code"], state=state)
    return {"ok": True, "state": state}


@route("POST", "/api/rooms/{code}/leave")
def leave_room(req: Request, code: str):
    req.require("listener")
    row = db.one("SELECT id FROM rooms WHERE code = ?", (code.upper(),))
    if row:
        db.execute("DELETE FROM room_members WHERE room_id = ? AND user_id = ?",
                   (row["id"], req.user["id"]))
    return {"ok": True}


# --------------------------------------------------------------------------- #
# misc
# --------------------------------------------------------------------------- #
@route("GET", "/api/health")
def health(req: Request):
    return {
        "status": "ok",
        "version": config.version,
        "tracks": db.scalar("SELECT COUNT(*) FROM tracks", (), 0),
        "read_only": config.read_only,
    }


@route("GET", "/api/backup")
def backup(req: Request):
    import io
    import sqlite3
    import zipfile

    req.require("admin")
    snapshot = config.data_dir / f"backup-{os.getpid()}-{time.time_ns()}.db"
    if snapshot.exists():
        snapshot.unlink()
    source = db.db()
    target = sqlite3.connect(snapshot)
    try:
        source.backup(target)
    finally:
        target.close()
    buffer = io.BytesIO()
    settings = {row["key"]: row["value"] for row in db.query("SELECT key, value FROM settings")}
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(snapshot, "library.db")
        archive.writestr("settings.json", json.dumps(settings, ensure_ascii=False, indent=2))
        archive.writestr("VERSION.txt", config.version + "\n")
    try:
        snapshot.unlink()
    except OSError:
        pass
    stamp = time.strftime("%Y%m%d-%H%M%S")
    resp = Response(200, buffer.getvalue(), "application/zip")
    resp.headers["Content-Disposition"] = f'attachment; filename="open-music-tag-{stamp}.zip"'
    return resp


@route("GET", "/api/events")
def events(req: Request):
    if not req.user:
        raise ApiError(401, "authentication required")
    return Response.event_stream()


@route("POST", "/api/variables/preview")
def preview_template(req: Request):
    req.require("editor")
    data = req.json()
    template = str(data.get("template") or "")
    track = library.get_track(int(data.get("track_id") or 0), include_lyrics=True) if data.get("track_id") else None
    context = variables.context_for(track or {})
    try:
        return {"result": variables.render(template, context)}
    except variables.TemplateError as exc:
        raise ApiError(400, str(exc)) from exc
