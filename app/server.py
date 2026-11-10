"""HTTP server: routing, authentication, static assets, range streaming, SSE."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import db
from .config import WEB_ROOT, config
from .events import broker, encode_sse
from .security import can, decode_token, token_digest


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


class Request:
    def __init__(self, method: str, path: str, query: dict, headers, body: bytes,
                 cookies: dict) -> None:
        self.method = method
        self.path = path
        self.query = query
        self.headers = headers
        self.body = body
        self.cookies = cookies
        self.user: dict | None = None
        self._json: dict | None = None

    def json(self) -> dict:
        if self._json is None:
            if not self.body:
                self._json = {}
            else:
                try:
                    data = json.loads(self.body.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise ApiError(400, "invalid JSON body") from exc
                if not isinstance(data, dict):
                    raise ApiError(400, "JSON body must be an object")
                self._json = data
        return self._json

    def arg(self, name: str, default: str | None = None) -> str | None:
        if name in self.query and self.query[name] != "":
            return self.query[name]
        value = self.json_raw().get(name)
        if value is None or value == "":
            return default
        return str(value)

    def json_raw(self) -> dict:
        if self.method in ("POST", "PUT", "PATCH") and self.body:
            try:
                return self.json()
            except ApiError:
                return {}
        return {}

    def int_arg(self, name: str, default: int | None = None) -> int | None:
        value = self.arg(name)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError as exc:
            raise ApiError(400, f"{name} must be an integer") from exc

    @property
    def role(self) -> str:
        return (self.user or {}).get("role", "")

    def require(self, role: str = "listener") -> None:
        if not self.user:
            raise ApiError(401, "authentication required")
        if not can(self.role, role):
            raise ApiError(403, "insufficient permissions")


class Response:
    def __init__(self, status: int = 200, body: bytes = b"",
                 content_type: str = "application/json; charset=utf-8",
                 headers: dict | None = None) -> None:
        self.status = status
        self.body = body
        self.content_type = content_type
        self.headers = headers or {}
        self.file_path: str | None = None
        self.remote: tuple[str, dict] | None = None
        self.sse = None

    @classmethod
    def json(cls, data, status: int = 200, headers: dict | None = None) -> "Response":
        return cls(
            status,
            json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"),
            "application/json; charset=utf-8",
            headers,
        )

    @classmethod
    def text(cls, text: str, status: int = 200,
             content_type: str = "text/plain; charset=utf-8") -> "Response":
        return cls(status, text.encode("utf-8"), content_type)

    @classmethod
    def redirect(cls, location: str, status: int = 303) -> "Response":
        return cls(status, b"", "text/plain", {"Location": location})

    @classmethod
    def empty(cls, status: int = 204) -> "Response":
        return cls(status, b"", "text/plain")

    @classmethod
    def stream(cls, path: str, mime: str | None = None) -> "Response":
        resp = cls(200, b"", mime or mimetypes.guess_type(path)[0] or "application/octet-stream")
        resp.file_path = path
        return resp

    @classmethod
    def remote_stream(cls, url: str, headers: dict, mime: str | None = None) -> "Response":
        resp = cls(200, b"", mime or mimetypes.guess_type(url)[0] or "application/octet-stream")
        resp.remote = (url, headers)
        return resp

    @classmethod
    def event_stream(cls) -> "Response":
        resp = cls(200, b"", "text/event-stream; charset=utf-8")
        resp.sse = True
        return resp


class Router:
    def __init__(self) -> None:
        self.routes: list[tuple[str, re.Pattern, object]] = []

    def add(self, method: str, pattern: str, handler) -> None:
        source = re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", r"(?P<\1>[^/]+)", pattern)
        regex = re.compile("^" + source + "/?$")
        self.routes.append((method, regex, handler))

    def match(self, method: str, path: str):
        allowed = False
        for route_method, regex, handler in self.routes:
            match = regex.match(path)
            if not match:
                continue
            if route_method != method and not (method == "HEAD" and route_method == "GET"):
                allowed = True
                continue
            return handler, match.groupdict()
        if allowed:
            raise ApiError(405, "method not allowed")
        return None, None


router = Router()


def route(method: str, pattern: str):
    def decorator(handler):
        router.add(method, pattern, handler)
        return handler
    return decorator


# --------------------------------------------------------------------------- #
def _cookies(header: str | None) -> dict:
    out: dict = {}
    for part in (header or "").split(";"):
        if "=" in part:
            name, _, value = part.strip().partition("=")
            out[name] = urllib.parse.unquote(value)
    return out


def resolve_user(raw_token: str | None) -> dict | None:
    if not raw_token:
        return None
    payload = decode_token(raw_token)
    if not payload:
        return None
    row = db.one(
        "SELECT u.id, u.username, u.display_name, u.role, u.active, s.expires_at"
        " FROM sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.token_hash = ?",
        (token_digest(raw_token),),
    )
    if not row or not row["active"]:
        return None
    if db.scalar("SELECT 1 WHERE ? <= datetime('now')", (row["expires_at"],)) :
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
    }


class AppHandler(BaseHTTPRequestHandler):
    server_version = "OpenMusicTag/" + config.version
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------
    def log_message(self, fmt: str, *args) -> None:  # quieter, structured-ish logging
        # drop query strings so ?token= (SSE/audio) never lands in access.log
        message = re.sub(r'\?[^ "]*', "", fmt % args)
        line = f"{self.address_string()} {message}"
        try:
            with open(config.data_dir / "logs" / "access.log", "a", encoding="utf-8") as fh:
                fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
        except OSError:
            pass

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        limit = config.max_upload_mb * 1024 * 1024
        if length > limit:
            raise ApiError(413, f"request body exceeds {config.max_upload_mb} MB")
        return self.rfile.read(length)

    def _handle(self, method: str) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = {key: values[-1] for key, values in urllib.parse.parse_qs(parsed.query).items()}
        cookies = _cookies(self.headers.get("Cookie"))
        try:
            body = self._read_body()
        except ApiError as exc:
            return self._send(Response.json({"error": exc.message}, exc.status))

        req = Request(method, path, query, self.headers, body, cookies)
        token = None
        auth = self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        token = token or cookies.get("omt_session") or query.get("token")
        api_key = self.headers.get("X-API-Key") or ""
        try:
            from . import apikeys
            req.user = apikeys.resolve(api_key or token) or resolve_user(token)
        except Exception:  # noqa: BLE001
            req.user = None

        if path == "/mcp":
            from .mcp import handle as mcp_handle
            try:
                response = mcp_handle(req)
            except ApiError as exc:
                response = Response.json({"error": exc.message}, exc.status)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                response = Response.json({"error": "internal error"}, 500)
            return self._send(response)

        if path.startswith("/rest/"):
            from .subsonic import handle as subsonic_handle
            try:
                response = subsonic_handle(req)
            except ApiError as exc:
                response = Response.json({"error": exc.message}, exc.status)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                response = Response.json({"error": "internal error"}, 500)
            return self._send(response)

        handler = None
        try:
            handler, params = router.match(method, path)
            if handler is None:
                if method in ("GET", "HEAD"):
                    return self._send(self._static(path))
                raise ApiError(404, "not found")
            result = handler(req, **params)
            if isinstance(result, Response):
                response = result
            elif isinstance(result, tuple):
                response = Response.json(result[0], result[1])
            elif result is None:
                response = Response.empty()
            else:
                response = Response.json(result)
        except ApiError as exc:
            response = Response.json({"error": exc.message, "code": exc.code}, exc.status)
        except FileNotFoundError as exc:
            response = Response.json({"error": str(exc) or "not found"}, 404)
        except LookupError as exc:
            response = Response.json({"error": str(exc) or "not found"}, 404)
        except PermissionError as exc:
            response = Response.json({"error": str(exc) or "forbidden"}, 403)
        except Exception as exc:  # noqa: BLE001 - last resort
            traceback.print_exc()
            response = Response.json({"error": f"internal error: {exc}"}, 500)
        self._send(response)

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    # -- static -----------------------------------------------------------
    def _static(self, path: str) -> Response:
        if path in ("/", "/index.html"):
            target = WEB_ROOT / "index.html"
        else:
            candidate = (WEB_ROOT / path.lstrip("/")).resolve()
            try:
                candidate.relative_to(WEB_ROOT.resolve())
            except ValueError:
                raise ApiError(404, "not found") from None
            target = candidate
        if not target.is_file():
            raise ApiError(404, "not found")
        mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if mime.startswith("text/") or mime in ("application/javascript", "application/json"):
            mime += "; charset=utf-8"
        resp = Response(200, target.read_bytes(), mime)
        if target.suffix.lower() in (".html", ".js", ".mjs", ".css"):
            # always revalidate: a deploy must never leave a stale SPA in the browser
            resp.headers["Cache-Control"] = "no-store, must-revalidate"
        else:
            resp.headers["Cache-Control"] = "public, max-age=3600"
        return resp

    # -- sending ----------------------------------------------------------
    def _send(self, resp: Response) -> None:
        if resp.sse:
            return self._send_sse()
        if resp.remote:
            return self._send_remote(resp)
        if resp.file_path:
            return self._send_file(resp)
        try:
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.content_type)
            self.send_header("Content-Length", str(len(resp.body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            for key, value in resp.headers.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(resp.body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_file(self, resp: Response) -> None:
        path = resp.file_path
        assert path
        try:
            size = os.path.getsize(path)
            start, end = 0, max(size - 1, 0)
            status = 200
            range_header = self.headers.get("Range")
            if range_header and range_header.startswith("bytes="):
                spec = range_header[6:].split(",")[0].strip()
                first, _, last = spec.partition("-")
                if first:
                    start = int(first)
                    if last:
                        end = min(int(last), size - 1)
                elif last:
                    start = max(size - int(last), 0)
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", resp.content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            for key, value in resp.headers.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command == "HEAD":
                return
            with open(path, "rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(262144, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError:
            pass

    def _send_remote(self, resp: Response) -> None:
        """Proxy a remote (WebDAV/AList) stream, forwarding Range requests."""
        url, headers = resp.remote or ("", {})
        request = urllib.request.Request(url, headers=dict(headers))
        range_header = self.headers.get("Range")
        if range_header:
            request.add_header("Range", range_header)
        try:
            with urllib.request.urlopen(request, timeout=60) as upstream:
                self.send_response(upstream.status)
                content_type = upstream.headers.get("Content-Type") or resp.content_type
                self.send_header("Content-Type", content_type)
                for header in ("Content-Length", "Content-Range", "Accept-Ranges"):
                    value = upstream.headers.get(header)
                    if value:
                        self.send_header(header, value)
                for key, value in resp.headers.items():
                    self.send_header(key, value)
                self.end_headers()
                if self.command == "HEAD":
                    return
                while True:
                    chunk = upstream.read(262144)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except urllib.error.HTTPError as exc:
            self.send_response(exc.code)
            self.send_header("Content-Length", "0")
            self.send_header("X-Upstream-Error", str(exc.code))
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError:
            try:
                self.send_response(502)
                self.send_header("Content-Length", "0")
                self.end_headers()
            except OSError:
                pass

    def _send_sse(self) -> None:
        queue = broker.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.wfile.write(b"retry: 3000\n\n")
            self.wfile.flush()
            while True:
                try:
                    event = queue.get(timeout=15)
                except Exception:  # noqa: BLE001 - Empty queue == heartbeat
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(encode_sse(event))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            broker.unsubscribe(queue)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def handle_error(self, request, client_address) -> None:
        """Clients closing audio/SSE connections is normal; do not print tracebacks."""
        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionResetError, ConnectionAbortedError,
                               BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)

    def __init__(self, address):
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(address, AppHandler)


def serve(host: str | None = None, port: int | None = None) -> None:
    db.init_db()
    from . import api  # noqa: F401 - registers all REST routes
    from .tasks import tasks
    tasks.recover()
    from .providers import registry
    registry.load_plugins()
    from . import watcher
    watcher.start()
    if db.get_setting("scrape.musicbrainz_cover", "1") == "1":
        pass

    class _ReusableServer(Server):
        pass

    httpd = _ReusableServer((host or config.host, port or config.port))
    banner = (
        f"\n  Music-Doctor {config.version}\n"
        f"  listening on http://{host or config.host}:{port or config.port}\n"
        f"  data directory: {config.data_dir}\n"
    )
    print(banner)
    if not config.base_url:
        pass
    thread = threading.Thread(target=httpd.serve_forever, name="mtw-http", daemon=True)
    thread.start()
    return httpd
