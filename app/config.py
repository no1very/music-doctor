"""Runtime configuration for Music-Doctor.

Everything is resolved from a data directory; all values can be overridden with
``MTW_*`` environment variables so the app stays container friendly.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_ROOT = ROOT / "web"


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


class Config:
    def __init__(self) -> None:
        self.data_dir = Path(_env("MTW_DATA_DIR", str(ROOT / "data"))).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "logs").mkdir(exist_ok=True)
        self.db_path = self.data_dir / "library.db"
        self.secret_key = self._load_secret()
        self.host = _env("MTW_HOST", "0.0.0.0")
        self.port = _env_int("MTW_PORT", 8318)
        self.base_url = _env("MTW_BASE_URL", "").rstrip("/")
        self.workers = max(1, _env_int("MTW_WORKERS", "2"))
        self.session_days = _env_int("MTW_SESSION_DAYS", "30")
        self.allow_signup = _env("MTW_ALLOW_SIGNUP", "0") == "1"
        self.scan_exts = {
            ".mp3",
            ".flac",
            ".m4a",
            ".mp4",
            ".ogg",
            ".oga",
            ".opus",
            ".wav",
            ".aiff",
            ".aif",
            ".ape",
            ".wv",
            ".dsf",
        }
        self.max_upload_mb = _env_int("MTW_MAX_UPLOAD_MB", "64")
        # MusicBrainz requires an identifying user agent.
        self.user_agent = _env(
            "MTW_USER_AGENT",
            "MusicDoctor/1.0 (https://github.com/example/open-music-tag)",
        )
        self.http_timeout = _env_int("MTW_HTTP_TIMEOUT", "12")
        self.read_only = _env("MTW_READ_ONLY", "0") == "1"
        self.version = "1.0.0"

    def _load_secret(self) -> bytes:
        env = os.environ.get("MTW_SECRET_KEY")
        if env:
            return env.encode("utf-8")
        path = self.data_dir / "secret.key"
        if path.exists():
            return path.read_bytes().strip()
        secret = secrets.token_urlsafe(48).encode("utf-8")
        path.write_bytes(secret)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return secret


config = Config()
