"""SQLite storage layer.

A single file database with WAL enabled. Connections are thread local because the
HTTP server is threaded and the task workers run on their own threads.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .config import config
from .secretbox import encrypt as encrypt_secret
from .security import hash_password, subsonic_password

SCHEMA_VERSION = 3
_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    display_name  TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    password_md5  TEXT NOT NULL DEFAULT '',
    password_cipher TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL DEFAULT 'listener',
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS libraries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    path       TEXT NOT NULL UNIQUE,
    kind       TEXT NOT NULL DEFAULT 'local',
    url        TEXT NOT NULL DEFAULT '',
    username   TEXT NOT NULL DEFAULT '',
    password_cipher TEXT NOT NULL DEFAULT '',
    enabled    INTEGER NOT NULL DEFAULT 1,
    auto_scan  INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_scan_at TEXT
);

CREATE TABLE IF NOT EXISTS api_keys (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL DEFAULT '',
    prefix     TEXT NOT NULL DEFAULT '',
    key_hash   TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT
);

CREATE TABLE IF NOT EXISTS tracks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    library_id   INTEGER NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
    path         TEXT NOT NULL UNIQUE,
    rel_path     TEXT NOT NULL DEFAULT '',
    ext          TEXT NOT NULL DEFAULT '',
    size         INTEGER NOT NULL DEFAULT 0,
    mtime        REAL NOT NULL DEFAULT 0,
    added_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    title        TEXT NOT NULL DEFAULT '',
    artist       TEXT NOT NULL DEFAULT '',
    albumartist  TEXT NOT NULL DEFAULT '',
    album        TEXT NOT NULL DEFAULT '',
    track_no     INTEGER,
    disc_no      INTEGER,
    year         INTEGER,
    genre        TEXT NOT NULL DEFAULT '',
    composer     TEXT NOT NULL DEFAULT '',
    lyricist     TEXT NOT NULL DEFAULT '',
    comment      TEXT NOT NULL DEFAULT '',
    lyrics       TEXT NOT NULL DEFAULT '',
    duration     REAL NOT NULL DEFAULT 0,
    bitrate      INTEGER NOT NULL DEFAULT 0,
    sample_rate  INTEGER NOT NULL DEFAULT 0,
    channels     INTEGER NOT NULL DEFAULT 0,
    bit_depth    INTEGER NOT NULL DEFAULT 0,
    codec        TEXT NOT NULL DEFAULT '',
    quick_hash   TEXT NOT NULL DEFAULT '',
    mb_album_id  TEXT NOT NULL DEFAULT '',
    mb_track_id  TEXT NOT NULL DEFAULT '',
    isrc         TEXT NOT NULL DEFAULT '',
    bpm          INTEGER,
    cover_hash   TEXT NOT NULL DEFAULT '',
    play_count   INTEGER NOT NULL DEFAULT 0,
    last_played_at TEXT,
    rating       INTEGER NOT NULL DEFAULT 0,
    album_id     TEXT NOT NULL DEFAULT '',
    artist_id    TEXT NOT NULL DEFAULT '',
    missing      INTEGER NOT NULL DEFAULT 0,
    tagged       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album_id);
CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist_id);
CREATE INDEX IF NOT EXISTS idx_tracks_library ON tracks(library_id);
CREATE INDEX IF NOT EXISTS idx_tracks_title ON tracks(title);

CREATE TABLE IF NOT EXISTS covers (
    hash TEXT PRIMARY KEY,
    mime TEXT NOT NULL DEFAULT 'image/jpeg',
    data BLOB NOT NULL,
    size INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS favorites (
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    track_id INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, track_id)
);

CREATE TABLE IF NOT EXISTS play_history (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  INTEGER NOT NULL,
    track_id INTEGER NOT NULL,
    played_at TEXT NOT NULL DEFAULT (datetime('now')),
    client   TEXT NOT NULL DEFAULT 'web',
    ms_played INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_history_user ON play_history(user_id, played_at);

CREATE TABLE IF NOT EXISTS play_queues (
    user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    current    INTEGER NOT NULL DEFAULT 0,
    position_ms INTEGER NOT NULL DEFAULT 0,
    items      TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS playlists (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'static',
    rules      TEXT NOT NULL DEFAULT '{}',
    description TEXT NOT NULL DEFAULT '',
    public     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS playlist_items (
    playlist_id INTEGER NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    track_id    INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (playlist_id, track_id)
);

CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'queued',
    progress   REAL NOT NULL DEFAULT 0,
    total      INTEGER NOT NULL DEFAULT 0,
    done       INTEGER NOT NULL DEFAULT 0,
    payload    TEXT NOT NULL DEFAULT '{}',
    result     TEXT NOT NULL DEFAULT '{}',
    error      TEXT NOT NULL DEFAULT '',
    created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, id);

CREATE TABLE IF NOT EXISTS task_logs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    ts      TEXT NOT NULL DEFAULT (datetime('now')),
    level   TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_logs ON task_logs(task_id, id);

CREATE TABLE IF NOT EXISTS meta_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id    INTEGER NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    user_id     INTEGER,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    action      TEXT NOT NULL DEFAULT 'edit',
    source      TEXT NOT NULL DEFAULT '',
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json  TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_versions_track ON meta_versions(track_id, id);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS rooms (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL DEFAULT '',
    owner_id   INTEGER NOT NULL,
    state      TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS room_members (
    room_id   INTEGER NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    user_id   INTEGER NOT NULL,
    last_seen TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (room_id, user_id)
);

CREATE TABLE IF NOT EXISTS artist_meta (
    artist_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    image_hash  TEXT NOT NULL DEFAULT '',
    bio         TEXT NOT NULL DEFAULT '',
    aliases     TEXT NOT NULL DEFAULT '[]',
    source      TEXT NOT NULL DEFAULT '',
    source_id   TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tag_sources (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE,
    kind    TEXT NOT NULL DEFAULT 'builtin',
    enabled INTEGER NOT NULL DEFAULT 1,
    config  TEXT NOT NULL DEFAULT '{}',
    builtin INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE VIEW IF NOT EXISTS v_albums AS
SELECT
    t.album_id                                   AS album_id,
    COALESCE(NULLIF(MAX(t.album), ''), 'Unknown Album') AS name,
    COALESCE(NULLIF(MAX(t.albumartist), ''), NULLIF(MAX(t.artist), ''), '未知艺术家') AS artist,
    MAX(t.artist_id)                              AS artist_id,
    MAX(t.year)                                  AS year,
    MAX(t.genre)                                 AS genre,
    COUNT(*)                                     AS track_count,
    SUM(t.duration)                              AS duration,
    MAX(t.cover_hash)                            AS cover_hash,
    MIN(t.added_at)                              AS added_at
FROM tracks t
WHERE t.missing = 0 AND t.album_id <> ''
GROUP BY t.album_id;

CREATE VIEW IF NOT EXISTS v_artists AS
SELECT
    t.artist_id                                  AS artist_id,
    COALESCE(NULLIF(MAX(m.name), ''),
             COALESCE(NULLIF(MAX(t.albumartist), ''), NULLIF(MAX(t.artist), ''), '未知艺术家')) AS name,
    COUNT(DISTINCT t.album_id)                   AS album_count,
    COUNT(*)                                     AS track_count,
    SUM(t.duration)                              AS duration,
    MAX(t.cover_hash)                            AS cover_hash,
    MAX(m.image_hash)                            AS image_hash,
    MAX(m.bio)                                   AS bio,
    MAX(m.source)                                AS meta_source,
    MAX(m.updated_at)                            AS meta_updated_at
FROM tracks t
LEFT JOIN artist_meta m ON m.artist_id = t.artist_id
WHERE t.missing = 0 AND t.artist_id <> ''
GROUP BY t.artist_id;

CREATE VIEW IF NOT EXISTS v_duplicates AS
SELECT
    lower(trim(t.title)) || '|' || lower(trim(t.artist)) AS dup_key,
    COUNT(*) AS copies,
    MIN(t.id) AS keep_id,
    GROUP_CONCAT(t.id) AS ids,
    MAX(t.bitrate) AS best_bitrate
FROM tracks t
WHERE t.missing = 0 AND trim(t.title) <> ''
GROUP BY dup_key
HAVING COUNT(*) > 1;
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.db_path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def db() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    return conn


@contextmanager
def transaction():
    conn = db()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return db().execute(sql, params).fetchall()


def one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    return db().execute(sql, params).fetchone()


def scalar(sql: str, params: tuple = (), default=None):
    row = one(sql, params)
    if row is None:
        return default
    value = row[0]
    return default if value is None else value


def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    return db().execute(sql, params)


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------- #
# settings helpers
# --------------------------------------------------------------------------- #
def get_setting(key: str, default: str = "") -> str:
    row = one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def get_json_setting(key: str, default):
    raw = get_setting(key, "")
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


# --------------------------------------------------------------------------- #
# bookkeeping
# --------------------------------------------------------------------------- #
def album_ids(album: str, albumartist: str, artist: str) -> tuple[str, str]:
    album_name = (album or "").strip()
    artist_name = (albumartist or artist or "").strip()
    album_key = f"{artist_name.lower()}\x1f{album_name.lower()}"
    artist_key = artist_name.lower()
    album_id = hashlib.sha1(album_key.encode("utf-8")).hexdigest()[:16]
    artist_id = hashlib.sha1(artist_key.encode("utf-8")).hexdigest()[:16]
    return album_id, artist_id


def init_db() -> None:
    conn = db()
    # Views are derived data: recreate them on start so schema tweaks apply.
    for view in ("v_albums", "v_artists", "v_duplicates"):
        conn.execute(f"DROP VIEW IF EXISTS {view}")
    conn.executescript(SCHEMA)
    _migrate(conn)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    _seed()


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migration for databases created by older versions."""

    def columns(table: str) -> set[str]:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    library_columns = columns("libraries")
    additions = {
        "kind": "TEXT NOT NULL DEFAULT 'local'",
        "url": "TEXT NOT NULL DEFAULT ''",
        "username": "TEXT NOT NULL DEFAULT ''",
        "password_cipher": "TEXT NOT NULL DEFAULT ''",
    }
    for name, ddl in additions.items():
        if name not in library_columns:
            conn.execute(f"ALTER TABLE libraries ADD COLUMN {name} {ddl}")

    version_columns = columns("meta_versions")
    if "source" not in version_columns:
        conn.execute("ALTER TABLE meta_versions ADD COLUMN source TEXT NOT NULL DEFAULT ''")


def _seed() -> None:
    if one("SELECT id FROM users LIMIT 1") is None:
        password = os.environ.get("MTW_ADMIN_PASSWORD") or uuid.uuid4().hex[:12]
        execute(
            "INSERT INTO users(username, display_name, password_hash, password_md5,"
            " password_cipher, role) VALUES(?, ?, ?, ?, ?, 'admin')",
            ("admin", "Administrator", hash_password(password), subsonic_password(password),
             encrypt_secret(password)),
        )
        note = config.data_dir / "INITIAL_ADMIN.txt"
        note.write_text(
            "Music-Doctor initial administrator\n"
            "username: admin\n"
            f"password: {password}\n"
            "Change it after the first login.\n",
            encoding="utf-8",
        )
        print(f"[init] created admin user, password written to {note}")

    defaults = {
        "scrape.auto_on_scan": "0",
        "scrape.min_score": "0.72",
        "scrape.providers": json.dumps(["filename", "musicbrainz"]),
        "scrape.write_cover": "1",
        "scrape.write_lyrics": "1",
        "scrape.write_lrc": "0",
        "scrape.write_cover_file": "0",
        "scrape.write_tags": "1",
        "lyrics.provider": "lrclib",
        "artist.providers": json.dumps(["netease", "wikipedia", "deezer"]),
        "artist.min_score": "0.5",
        "artist.image_folder": "",
        "naming.template": "${discnumber.zfill(2) + '_' if discnumber and tracknumber else ''}"
        "${tracknumber.zfill(2) + '. ' if tracknumber else ''}${title} - ${artist}",
        "naming.enabled": "0",
        "library.watch_interval": "300",
        "playback.crossfade": "0",
        "ui.theme": "dark",
        "site.name": "Music-Doctor",
        "subsonic.enabled": "1",
        "dedupe.duration_tolerance": "2",
    }
    for key, value in defaults.items():
        execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (key, value))

    for name in ("filename", "musicbrainz", "coverart", "lrclib", "netease"):
        execute(
            "INSERT OR IGNORE INTO tag_sources(name, kind, enabled, config, builtin)"
            " VALUES(?, 'builtin', 1, '{}', 1)",
            (name,),
        )


def db_size() -> int:
    try:
        return Path(config.db_path).stat().st_size
    except OSError:
        return 0
