"""Regression tests for the bugs fixed during the code audit.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import shutil
import unittest
import uuid

_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tmp")
os.makedirs(_TMP, exist_ok=True)
os.environ.setdefault("MTW_DATA_DIR", _TMP)

from app import api, db, library, providers, subsonic  # noqa: E402
from app.config import config  # noqa: E402
from app.server import ApiError, Request  # noqa: E402


def make_request(body: dict | None = None, user: dict | None = None) -> Request:
    raw = json.dumps(body or {}).encode("utf-8") if body is not None else b""
    request = Request("POST", "/", {}, {}, raw, {})
    request.user = user
    return request


class IsolatedDatabase(unittest.TestCase):
    """Each test class gets its own SQLite file so user counts are meaningful."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.dir = os.path.join(_TMP, f"regr-{uuid.uuid4().hex[:8]}")
        os.makedirs(cls.dir, exist_ok=True)
        cls._old_db = config.db_path
        cls._old_data = config.data_dir
        config.data_dir = __import__("pathlib").Path(cls.dir)
        config.db_path = config.data_dir / "library.db"
        old = getattr(db._local, "conn", None)
        if old is not None:
            old.close()
            db._local.conn = None
        db.init_db()

    @classmethod
    def tearDownClass(cls) -> None:
        config.db_path = cls._old_db
        config.data_dir = cls._old_data
        old = getattr(db._local, "conn", None)
        if old is not None:
            old.close()
            db._local.conn = None
        shutil.rmtree(cls.dir, ignore_errors=True)


# --------------------------------------------------------------------------- #
class XmlTextTests(unittest.TestCase):
    def test_text_node_is_inline(self) -> None:
        xml = subsonic._xml("genre", {"@songCount": 3, "#text": "Rock & Roll"})
        self.assertIn("<genre songCount=\"3\">Rock &amp; Roll</genre>", xml)
        self.assertNotIn("#text", xml)

    def test_text_with_children_is_escaped(self) -> None:
        xml = subsonic._xml("lyrics", {"@artist": "A", "#text": "a < b"})
        self.assertIn('artist="A"', xml)
        self.assertIn("a &lt; b", xml)
        self.assertNotIn("#text", xml)


# --------------------------------------------------------------------------- #
class DuplicateGroupTests(IsolatedDatabase):
    def _add_track(self, title: str, artist: str, quick_hash: str) -> int:
        cur = db.execute(
            "INSERT INTO tracks(library_id, path, title, artist, albumartist, quick_hash, duration)"
            " VALUES(1, ?, ?, ?, ?, ?, 200)",
            (f"/tmp/{uuid.uuid4().hex}.mp3", title, artist, artist, quick_hash),
        )
        return int(cur.lastrowid)

    def test_same_file_is_not_reported_twice(self) -> None:
        db.execute("INSERT OR IGNORE INTO libraries(id, name, path) VALUES(1, 'test', '/tmp')")
        shared = "hash-shared"
        self._add_track("Song", "Artist", shared)
        self._add_track("Song", "Artist", shared)
        groups = library.duplicate_groups()
        reported = [track["id"] for group in groups for track in group["tracks"]]
        self.assertEqual(len(reported), len(set(reported)), "ids must appear in exactly one group")
        hash_keys = [group["key"] for group in groups if group["key"].startswith("hash:")]
        self.assertFalse(hash_keys, "identical copies were already covered by the title group")


# --------------------------------------------------------------------------- #
class AdminGuardTests(IsolatedDatabase):
    def _admin(self) -> dict:
        db.execute("DELETE FROM users")
        cur = db.execute(
            "INSERT INTO users(username, display_name, password_hash, role, active)"
            " VALUES('root', 'Root', 'x', 'admin', 1)"
        )
        return {"id": int(cur.lastrowid), "username": "root", "role": "admin"}

    def test_demoting_the_last_admin_is_rejected_without_writing(self) -> None:
        admin = self._admin()
        request = make_request({"role": "listener"}, admin)
        with self.assertRaises(ApiError) as raised:
            api.update_user(request, str(admin["id"]))
        self.assertEqual(raised.exception.status, 400)
        row = db.one("SELECT role FROM users WHERE id = ?", (admin["id"],))
        self.assertEqual(row["role"], "admin", "rejected request must not persist the change")

    def test_deactivating_the_last_admin_is_rejected(self) -> None:
        admin = self._admin()
        request = make_request({"active": False}, admin)
        with self.assertRaises(ApiError):
            api.update_user(request, str(admin["id"]))
        row = db.one("SELECT active FROM users WHERE id = ?", (admin["id"],))
        self.assertEqual(row["active"], 1)

    def test_deleting_the_last_admin_is_rejected(self) -> None:
        admin = self._admin()
        # The actor is a different administrator; the last remaining admin must
        # survive even if the request reaches the endpoint.
        actor = {"id": admin["id"] + 100, "username": "ghost", "role": "admin"}
        request = make_request({}, actor)
        with self.assertRaises(ApiError) as raised:
            api.delete_user(request, str(admin["id"]))
        self.assertEqual(raised.exception.status, 400)
        self.assertIsNotNone(db.one("SELECT id FROM users WHERE id = ?", (admin["id"],)))


# --------------------------------------------------------------------------- #
class PlaylistPermissionTests(IsolatedDatabase):
    def setUp(self) -> None:
        db.execute("DELETE FROM playlist_items")
        db.execute("DELETE FROM playlists")
        owner = self._user("owner")
        self.other = self._user("other")
        cur = db.execute("INSERT INTO playlists(owner_id, name) VALUES(?, 'private')", (owner["id"],))
        self.playlist_id = int(cur.lastrowid)
        track = self._track()
        db.execute(
            "INSERT INTO playlist_items(playlist_id, track_id, position) VALUES(?, ?, 0)",
            (self.playlist_id, track),
        )
        self.track_id = track
        self.owner = owner

    def _user(self, name: str) -> dict:
        row = db.one("SELECT * FROM users WHERE username = ?", (name,))
        if row:
            return {"id": row["id"], "username": name, "role": row["role"]}
        cur = db.execute(
            "INSERT INTO users(username, display_name, password_hash, role)"
            " VALUES(?, ?, 'x', 'listener')", (name, name),
        )
        return {"id": int(cur.lastrowid), "username": name, "role": "listener"}

    def _track(self) -> int:
        db.execute("INSERT OR IGNORE INTO libraries(id, name, path) VALUES(1, 'test', '/tmp')")
        cur = db.execute(
            "INSERT INTO tracks(library_id, path, title, artist, duration)"
            " VALUES(1, ?, 'T', 'A', 100)", (f"/tmp/{uuid.uuid4().hex}.mp3",),
        )
        return int(cur.lastrowid)

    def test_foreign_listener_cannot_remove_tracks(self) -> None:
        request = make_request({}, self.other)
        with self.assertRaises(ApiError) as raised:
            api.remove_playlist_track(request, str(self.playlist_id), str(self.track_id))
        self.assertEqual(raised.exception.status, 403)
        row = db.one(
            "SELECT 1 FROM playlist_items WHERE playlist_id = ? AND track_id = ?",
            (self.playlist_id, self.track_id),
        )
        self.assertIsNotNone(row, "rejected request must not delete the item")

    def test_foreign_listener_cannot_reorder(self) -> None:
        request = make_request({"ids": [self.track_id]}, self.other)
        with self.assertRaises(ApiError) as raised:
            api.reorder_playlist(request, str(self.playlist_id))
        self.assertEqual(raised.exception.status, 403)

    def test_owner_can_reorder_and_unknown_ids_are_rejected(self) -> None:
        request = make_request({"ids": [self.track_id]}, self.owner)
        api.reorder_playlist(request, str(self.playlist_id))
        request = make_request({"ids": [self.track_id, 999999]}, self.owner)
        with self.assertRaises(ApiError) as raised:
            api.reorder_playlist(request, str(self.playlist_id))
        self.assertEqual(raised.exception.status, 400)


# --------------------------------------------------------------------------- #
class SettingsValidationTests(IsolatedDatabase):
    def test_invalid_template_does_not_overwrite_the_old_one(self) -> None:
        admin = {"id": 1, "username": "admin", "role": "admin"}
        db.set_setting("naming.template", "${title}")
        request = make_request({"naming.template": "${title", "ui.theme": "light"}, admin)
        with self.assertRaises(ApiError) as raised:
            api.put_settings(request)
        self.assertEqual(raised.exception.status, 400)
        self.assertEqual(db.get_setting("naming.template"), "${title}")
        self.assertNotEqual(db.get_setting("ui.theme", "dark"), "light")


# --------------------------------------------------------------------------- #
class NetEaseLyricsTests(unittest.TestCase):
    def test_lyrics_accepts_a_plain_dict(self) -> None:
        provider = providers.NetEaseProvider()
        calls = {}

        def fake_fetch_json(url, headers=None, **kwargs):
            calls["url"] = url
            return {"lrc": {"lyric": "[00:01]hi"}}

        original = providers.httpclient.fetch_json
        providers.httpclient.fetch_json = fake_fetch_json
        try:
            lyrics = provider.lyrics({"artist": "A", "title": "T", "source_id": "123"})
        finally:
            providers.httpclient.fetch_json = original
        self.assertEqual(lyrics, "[00:01]hi")
        self.assertIn("id=123", calls["url"])

    def test_lyrics_without_id_falls_back_to_search(self) -> None:
        provider = providers.NetEaseProvider()
        provider.search = lambda query: [providers.Candidate(provider="netease", source_id="42")]
        original = providers.httpclient.fetch_json
        providers.httpclient.fetch_json = lambda url, headers=None, **kwargs: {"lrc": {"lyric": "x"}}
        try:
            lyrics = provider.lyrics({"artist": "A", "title": "T"})
        finally:
            providers.httpclient.fetch_json = original
        self.assertEqual(lyrics, "x")


if __name__ == "__main__":
    unittest.main()
