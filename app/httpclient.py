"""Small HTTP helper (stdlib only): JSON fetch, gzip, per-host rate limiting."""

from __future__ import annotations

import gzip
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import config

_lock = threading.Lock()
_last_call: dict[str, float] = {}


def _throttle(host: str, min_interval: float) -> None:
    if min_interval <= 0:
        return
    with _lock:
        now = time.monotonic()
        last = _last_call.get(host, 0.0)
        wait = min_interval - (now - last)
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.monotonic()


def fetch(url: str, *, headers: dict | None = None, timeout: int | None = None,
          min_interval: float = 0.0, max_bytes: int = 8 << 20) -> bytes:
    parsed = urllib.parse.urlsplit(url)
    _throttle(parsed.netloc, min_interval)
    request = urllib.request.Request(url, headers={
        "User-Agent": config.user_agent,
        "Accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip",
        **(headers or {}),
    })
    with urllib.request.urlopen(request, timeout=timeout or config.http_timeout) as response:
        raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError("response too large")
        if response.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw


def fetch_json(url: str, **kwargs):
    return json.loads(fetch(url, **kwargs).decode("utf-8", errors="replace"))


def quote(value: str) -> str:
    return urllib.parse.quote(str(value), safe="")


def build_url(base: str, params: dict) -> str:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    return f"{base}?{query}" if query else base
