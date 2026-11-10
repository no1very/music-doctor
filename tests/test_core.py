"""Unit tests (stdlib unittest): tag engine, templates, security, smart queries.

Run with:  python -m unittest discover -s tests -v
No third party packages or audio tools are required: the fixtures are synthesised
byte by byte.
"""

from __future__ import annotations

import json
import os
import struct
import unittest
import uuid

_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tmp")
os.makedirs(_TMP, exist_ok=True)
os.environ.setdefault("MTW_DATA_DIR", _TMP)


def make_dir(prefix: str) -> str:
    """tempfile.mkdtemp is avoided: some sandboxes deny writes below its dirs."""
    path = os.path.join(_TMP, f"{prefix}{uuid.uuid4().hex[:8]}")
    os.makedirs(path, exist_ok=True)
    return path

from app import tags, variables  # noqa: E402
from app.secretbox import decrypt, encrypt  # noqa: E402
from app.security import (  # noqa: E402
    hash_password,
    issue_token,
    decode_token,
    subsonic_password,
    verify_password,
)


def make_mp3(frames: int = 24) -> bytes:
    header = bytes([0xFF, 0xFB, 0x90, 0x00])  # MPEG1 L3, 128 kbps, 44.1 kHz, stereo
    frame = header + b"\x00" * (417 - len(header))
    return frame * frames


def flac_streaminfo(samples: int = 88200) -> bytes:
    rate, channels, bits = 44100, 2, 16
    packed = (rate << 44) | ((channels - 1) << 41) | ((bits - 1) << 36) | samples
    body = struct.pack(">HH", 4096, 4096) + b"\x00" * 6 + packed.to_bytes(8, "big") + b"\x00" * 16
    return body


def make_flac() -> bytes:
    body = flac_streaminfo()
    header = bytes([0x80 | 0]) + len(body).to_bytes(3, "big")
    return b"fLaC" + header + body + b"\xff\xf8" + b"\x00" * 512


class TagEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = make_dir("omt-tag-")

    def test_mp3_roundtrip(self) -> None:
        path = os.path.join(self.dir, "song.mp3")
        with open(path, "wb") as fh:
            fh.write(make_mp3())
        tags.write(path, {
            "title": "冰雨", "artist": "刘德华", "albumartist": "刘德华", "album": "忘情水",
            "track_no": 1, "disc_no": 1, "year": 1997, "genre": "Pop", "composer": "陈耀川",
            "lyricist": "刘德华", "comment": "测试注释", "lyrics": "冷冷的冰雨\n在脸上胡乱的拍",
            "bpm": 64, "isrc": "HK1234567890",
            "mb_album_id": "abc-123", "mb_track_id": "def-456",
            "cover": ("image/jpeg", b"\xff\xd8\xff\xe0" + b"j" * 200),
        })
        read = tags.read(path)
        self.assertEqual(read["title"], "冰雨")
        self.assertEqual(read["artist"], "刘德华")
        self.assertEqual(read["album"], "忘情水")
        self.assertEqual(read["track_no"], 1)
        self.assertEqual(read["disc_no"], 1)
        self.assertEqual(read["year"], 1997)
        self.assertEqual(read["bpm"], 64)
        self.assertIn("冷冷的冰雨", read["lyrics"])
        self.assertEqual(read["comment"], "测试注释")
        self.assertEqual(read["mb_album_id"], "abc-123")
        self.assertEqual(read["mb_track_id"], "def-456")
        self.assertEqual(read["cover"][0], "image/jpeg")
        self.assertEqual(len(read["cover"][1]), 204)
        self.assertEqual(read["bitrate"], 128)
        self.assertEqual(read["sample_rate"], 44100)
        self.assertGreater(read["duration"], 0.4)

    def test_mp3_preserves_unknown_frames(self) -> None:
        from app.tags import id3
        path = os.path.join(self.dir, "keep.mp3")
        with open(path, "wb") as fh:
            fh.write(make_mp3())
        tag = id3.build({"title": "one"}, {"PRIV": b"owner\x00payload"})
        with open(path, "wb") as fh:
            fh.write(tag + make_mp3())
        tags.write(path, {"title": "two", "artist": "someone"})
        with open(path, "rb") as fh:
            raw = fh.read()
        self.assertIn(b"PRIV", raw)
        self.assertIn(b"payload", raw)
        read = tags.read(path)
        self.assertEqual(read["title"], "two")

    def test_ascii_shortcuts(self) -> None:
        path = os.path.join(self.dir, "ascii.mp3")
        with open(path, "wb") as fh:
            fh.write(make_mp3())
        tags.write(path, {"title": "Hello", "artist": "World", "comment": "plain"})
        read = tags.read(path)
        self.assertEqual((read["title"], read["artist"], read["comment"]), ("Hello", "World", "plain"))

    def test_flac_roundtrip(self) -> None:
        path = os.path.join(self.dir, "song.flac")
        with open(path, "wb") as fh:
            fh.write(make_flac())
        before = tags.read(path)
        self.assertEqual(before["duration"], 2.0)
        self.assertEqual(before["sample_rate"], 44100)
        self.assertEqual(before["channels"], 2)
        self.assertEqual(before["bit_depth"], 16)
        tags.write(path, {
            "title": "忘情水", "artist": "刘德华", "albumartist": "刘德华", "album": "忘情水",
            "track_no": 2, "disc_no": 1, "year": 1997, "genre": "Pop",
            "lyrics": "曾经年少爱追梦", "comment": "flac 注释",
            "cover": ("image/png", b"\x89PNG\r\n\x1a\n" + b"p" * 128),
        })
        read = tags.read(path)
        self.assertEqual(read["title"], "忘情水")
        self.assertEqual(read["artist"], "刘德华")
        self.assertEqual(read["track_no"], 2)
        self.assertEqual(read["year"], 1997)
        self.assertIn("曾经年少", read["lyrics"])
        self.assertEqual(read["cover"][0], "image/png")
        self.assertEqual(len(read["cover"][1]), 136)
        # writing again must keep the audio stream intact
        tags.write(path, {"title": "忘情水 2", "artist": "刘德华"})
        self.assertEqual(tags.read(path)["title"], "忘情水 2")
        with open(path, "rb") as fh:
            self.assertTrue(fh.read().endswith(b"\x00" * 512))

    def test_writable_flags(self) -> None:
        self.assertTrue(tags.can_write("a.mp3"))
        self.assertTrue(tags.can_write("a.FLAC"))
        self.assertFalse(tags.can_write("a.m4a"))
        self.assertTrue(tags.is_writable("a.mp3", "MP3"))
        self.assertFalse(tags.is_writable("a.flac", "MP3"))
        with self.assertRaises(tags.UnsupportedFormat):
            tags.write(os.path.join(self.dir, "x.m4a"), {"title": "nope"})

    def test_misnamed_container_falls_back_to_magic(self) -> None:
        from app.tags import id3
        path = os.path.join(self.dir, "actually-mp3.flac")
        with open(path, "wb") as fh:
            fh.write(id3.build({"title": "Sniffed", "artist": "Tester", "album": "Magic"}) + make_mp3())
        read = tags.read(path)
        self.assertEqual(read["codec"], "MP3")
        self.assertEqual(read["title"], "Sniffed")
        self.assertEqual(read["artist"], "Tester")
        self.assertFalse(read["writable"], "misnamed container must stay read-only")

    def test_genre_normalisation(self) -> None:
        from app.tags.id3 import normalize_genre
        self.assertEqual(normalize_genre("(17)"), "Rock")
        self.assertEqual(normalize_genre("17"), "Rock")
        self.assertEqual(normalize_genre("(17)Rock"), "Rock")
        self.assertEqual(normalize_genre("City Pop"), "City Pop")


class TemplateTests(unittest.TestCase):
    def test_basic_render(self) -> None:
        ctx = variables.context_for({
            "title": "冰雨", "artist": "刘德华/陈耀川", "album": "忘情水",
            "track_no": 1, "disc_no": 1, "path": "D:/Music/刘德华/忘情水/01. 冰雨.flac",
        })
        self.assertEqual(variables.render("${title} - ${artist}", ctx), "冰雨 - 刘德华/陈耀川")
        self.assertEqual(variables.render("${first_artist}", ctx), "刘德华")
        self.assertEqual(variables.render("${tracknumber.zfill(2)}. ${title}", ctx), "01. 冰雨")
        self.assertEqual(
            variables.render("${discnumber.zfill(2) + '_' if discnumber and tracknumber else ''}${tracknumber.zfill(2)}", ctx),
            "01_01",
        )
        self.assertEqual(variables.render("${title or '未知标题'}", ctx), "冰雨")
        self.assertEqual(variables.render("${filename}", ctx), "01. 冰雨.flac")
        self.assertEqual(variables.render("${file_song_name}", ctx), "01. 冰雨")
        self.assertEqual(variables.render("${missing_variable}", ctx), "")

    def test_missing_values_fallback(self) -> None:
        ctx = variables.context_for({"title": "", "artist": ""})
        self.assertEqual(variables.render("${title or '未知标题'} - ${artist or '未知歌手'}", ctx),
                         "未知标题 - 未知歌手")
        self.assertEqual(variables.render("${tracknumber.zfill(2) if tracknumber else ''}", ctx), "")

    def test_rejects_unsafe_expressions(self) -> None:
        for template in (
            "${__import__('os').system('echo hi')}",
            "${title.__class__.__mro__}",
            "${open('/etc/passwd').read()}",
            "${eval('1+1')}",
        ):
            with self.assertRaises(variables.TemplateError):
                variables.validate(template)

    def test_invalid_syntax_raises(self) -> None:
        with self.assertRaises(variables.TemplateError):
            variables.validate("${title")


class SecurityTests(unittest.TestCase):
    def test_password_hash(self) -> None:
        stored = hash_password("s3cret!")
        self.assertTrue(verify_password("s3cret!", stored))
        self.assertFalse(verify_password("wrong", stored))
        self.assertNotIn("s3cret!", stored)

    def test_token_roundtrip_and_tamper(self) -> None:
        token, digest = issue_token(1, "admin", "admin")
        payload = decode_token(token)
        self.assertEqual(payload["uid"], 1)
        self.assertEqual(payload["r"], "admin")
        self.assertEqual(len(digest), 64)
        self.assertIsNone(decode_token(token + "x"))
        self.assertIsNone(decode_token("garbage"))

    def test_subsonic_md5(self) -> None:
        self.assertEqual(subsonic_password("abc"), "900150983cd24fb0d6963f7d28e17f72")

    def test_secretbox(self) -> None:
        blob = encrypt("pässwörd")
        self.assertEqual(decrypt(blob), "pässwörd")
        self.assertEqual(decrypt("tampered"), "")


class SmartQueryTests(unittest.TestCase):
    def test_build(self) -> None:
        from app.library import build_smart_query
        where, values = build_smart_query({
            "match": "any",
            "rules": [
                {"field": "artist", "op": "contains", "value": "刘德华"},
                {"field": "year", "op": "between", "value": [1990, 2000]},
                {"field": "rating", "op": "gte", "value": 4},
                {"field": "genre", "op": "is_set"},
            ],
        })
        self.assertIn(" OR ", where)
        self.assertIn("LIKE", where)
        self.assertIn("BETWEEN", where)
        self.assertEqual(len(values), 4)
        self.assertEqual(values[0], "%刘德华%")

    def test_injection_is_parameterised(self) -> None:
        from app.library import build_smart_query
        where, values = build_smart_query({
            "rules": [{"field": "title", "op": "equals", "value": "'; DROP TABLE tracks; --"}],
        })
        self.assertNotIn("DROP TABLE", where)
        self.assertEqual(values[0], "'; DROP TABLE tracks; --")

    def test_unknown_field_ignored(self) -> None:
        from app.library import build_smart_query
        where, values = build_smart_query({"rules": [{"field": "password", "op": "equals", "value": "x"}]})
        self.assertEqual(values, [])
        self.assertIn("1=1", where)


class IdTests(unittest.TestCase):
    def test_album_ids_are_stable(self) -> None:
        from app.db import album_ids
        first = album_ids("OK Computer", "Radiohead", "")
        second = album_ids("ok computer", "radiohead", "Thom Yorke")
        self.assertEqual(first, second)
        self.assertNotEqual(first, album_ids("Kid A", "Radiohead", ""))
        self.assertEqual(len(first[0]), 16)

    def test_safe_filename(self) -> None:
        from app.library import safe_filename
        self.assertEqual(safe_filename('AC/DC: "Live"'), "AC-DC- -Live-")
        self.assertEqual(safe_filename("  .hidden.  "), "hidden")
        self.assertEqual(safe_filename("../../etc/passwd"), "-..-etc-passwd")
        self.assertEqual(safe_filename("a" * 300), "a" * 180)
        self.assertEqual(safe_filename("冰雨 - 刘德华"), "冰雨 - 刘德华")

    def test_remote_detection(self) -> None:
        from app.library import is_remote
        self.assertTrue(is_remote("https://alist.example.com/dav/a.flac"))
        self.assertTrue(is_remote("http://127.0.0.1/a.mp3"))
        self.assertFalse(is_remote("D:/Music/a.flac"))
        self.assertFalse(is_remote("/music/a.flac"))


class ApiKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from app import db
        db.init_db()
        row = db.one("SELECT id FROM users ORDER BY id LIMIT 1")
        if not row:
            db.execute(
                "INSERT INTO users(username, password_hash, password_md5, role)"
                " VALUES('k', 'x', 'y', 'admin')"
            )
            row = db.one("SELECT id FROM users ORDER BY id LIMIT 1")
        cls.user_id = int(row["id"])

    def test_lifecycle(self) -> None:
        from app import apikeys
        item, raw = apikeys.create(self.user_id, "unit test")
        self.assertTrue(raw.startswith("omt_"))
        self.assertNotIn(raw, str(item))
        resolved = apikeys.resolve(raw)
        self.assertEqual(resolved["id"], self.user_id)
        self.assertIsNone(apikeys.resolve("omt_deadbeef"))
        self.assertIsNone(apikeys.resolve("not-a-key"))
        listed = apikeys.list_for(self.user_id)
        self.assertTrue(any(key["id"] == item["id"] for key in listed))
        self.assertTrue(apikeys.revoke(self.user_id, item["id"]))
        self.assertIsNone(apikeys.resolve(raw))
        self.assertFalse(apikeys.revoke(self.user_id, 999999))


WEBDAV_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/dav/music/</d:href>
    <d:propstat>
      <d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/music/Album%20A/</d:href>
    <d:propstat>
      <d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/music/Album%20A/01%20-%20Song.flac</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype/>
        <d:getcontentlength>1234567</d:getcontentlength>
        <d:getlastmodified>Sun, 08 Nov 2026 10:20:30 GMT</d:getlastmodified>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
    <d:propstat>
      <d:prop><d:getcontentlength/></d:prop>
      <d:status>HTTP/1.1 404 Not Found</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
"""


class WebDAVTests(unittest.TestCase):
    def test_parse_multistatus(self) -> None:
        from app.webdav import parse_multistatus
        entries = parse_multistatus(WEBDAV_XML, "http://host/dav/music/")
        self.assertEqual(len(entries), 2, entries)
        directory, file_entry = entries
        self.assertTrue(directory["is_dir"])
        self.assertEqual(directory["name"], "Album A")
        self.assertFalse(file_entry["is_dir"])
        self.assertEqual(file_entry["name"], "01 - Song.flac")
        self.assertEqual(file_entry["size"], 1234567)
        self.assertGreater(file_entry["mtime"], 1_700_000_000)
        self.assertTrue(file_entry["url"].endswith("/dav/music/Album%20A/01%20-%20Song.flac"))

    def test_invalid_body(self) -> None:
        from app.webdav import WebDAVError, parse_multistatus
        with self.assertRaises(WebDAVError):
            parse_multistatus(b"<not-xml", "http://host/dav/")

    def test_guess_meta(self) -> None:
        from app.webdav import _guess_meta
        meta = _guess_meta("Radiohead/OK Computer/01. Airbag.flac")
        self.assertEqual(meta["artist"], "Radiohead")
        self.assertEqual(meta["album"], "OK Computer")
        self.assertEqual(meta["title"], "Airbag")
        meta = _guess_meta("Loose/Artist - Title.mp3")
        self.assertEqual((meta["artist"], meta["title"]), ("Artist", "Title"))


class SidecarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = make_dir("omt-sidecar-")

    def test_lrc_and_cover(self) -> None:
        from app import library
        audio = os.path.join(self.dir, "song.mp3")
        with open(audio, "wb") as fh:
            fh.write(b"audio")
        self.assertEqual(library.read_sidecar_lyrics(audio), "")
        with open(library.lrc_path(audio), "w", encoding="utf-8") as fh:
            fh.write("line one\nline two")
        self.assertIn("line two", library.read_sidecar_lyrics(audio))
        with open(os.path.join(self.dir, "cover.jpg"), "wb") as fh:
            fh.write(b"\xff\xd8\xff\xe0" + b"c" * 32)
        cover = library.read_sidecar_cover(audio)
        self.assertIsNotNone(cover)
        self.assertEqual(cover[0], "image/jpeg")
        self.assertGreater(len(cover[1]), 10)

    def test_remote_has_no_sidecars(self) -> None:
        from app import library
        self.assertEqual(library.read_sidecar_lyrics("https://host/a.mp3"), "")
        self.assertIsNone(library.read_sidecar_cover("https://host/a.mp3"))


class MCPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from app import db
        db.init_db()
        from app import apikeys
        row = db.one("SELECT * FROM users ORDER BY id LIMIT 1")
        cls.user = {
            "id": int(row["id"]), "username": row["username"],
            "display_name": row["display_name"], "role": row["role"],
        }

    def _call(self, payload):
        import json as jsonlib
        from app.server import Request
        from app import mcp
        request = Request("POST", "/mcp", {}, {}, jsonlib.dumps(payload).encode(), {})
        request.user = self.user
        response = mcp.handle(request)
        return response.status, jsonlib.loads(response.body or b"{}")

    def test_initialize_and_tools(self) -> None:
        status, data = self._call({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(status, 200)
        self.assertEqual(data["result"]["serverInfo"]["name"], "music-doctor")
        self.assertIn("protocolVersion", data["result"])
        status, data = self._call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {tool["name"] for tool in data["result"]["tools"]}
        self.assertIn("search_tracks", names)
        self.assertIn("update_track", names)
        self.assertGreaterEqual(len(names), 9)

    def test_tool_call_and_errors(self) -> None:
        status, data = self._call({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "library_stats", "arguments": {}},
        })
        self.assertEqual(status, 200)
        self.assertFalse(data["result"]["isError"])
        payload = json.loads(data["result"]["content"][0]["text"])
        self.assertIn("tracks", payload)
        _, data = self._call({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "nope", "arguments": {}},
        })
        self.assertTrue(data["result"]["isError"])
        _, data = self._call({"jsonrpc": "2.0", "id": 5, "method": "unknown/method"})
        self.assertEqual(data["error"]["code"], -32601)

    def test_notification_and_bad_request(self) -> None:
        import json as jsonlib
        from app.server import Request
        from app import mcp
        request = Request("POST", "/mcp", {}, {}, jsonlib.dumps(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}).encode(), {})
        request.user = self.user
        self.assertEqual(mcp.handle(request).status, 202)
        request = Request("POST", "/mcp", {}, {}, b"{bad json", {})
        request.user = self.user
        self.assertEqual(mcp.handle(request).status, 400)


class ArtistMetaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from app import db
        db.init_db()

    def test_upsert_merges_fields(self) -> None:
        from app import artist_scrape
        artist_id = "artist-meta-test"
        first = artist_scrape.save_meta(
            artist_id, image_hash="hash-1", bio="第一版简介",
            aliases=["Alias A"], source="netease", source_id="123",
        )
        self.assertEqual(first["bio"], "第一版简介")
        self.assertEqual(first["aliases"], ["Alias A"])
        second = artist_scrape.save_meta(artist_id, bio="第二版简介")
        self.assertEqual(second["bio"], "第二版简介")
        self.assertEqual(second["image_hash"], "hash-1", "unset fields must be preserved")
        self.assertEqual(second["aliases"], ["Alias A"])
        self.assertEqual(second["source"], "netease")
        fetched = artist_scrape.get_meta(artist_id)
        self.assertEqual(fetched["bio"], "第二版简介")
        self.assertIsNone(artist_scrape.get_meta("does-not-exist"))

    def test_candidate_scoring(self) -> None:
        from app import artist_scrape
        exact = artist_scrape.ArtistCandidate(source="x", name="刘德华", image_url="http://a")
        other = artist_scrape.ArtistCandidate(source="x", name="张学友")
        self.assertGreater(artist_scrape._score("刘德华", exact), 0.9)
        self.assertLess(artist_scrape._score("刘德华", other), 0.5)

    def test_export_filename(self) -> None:
        from app.artist_scrape import export_filename
        self.assertEqual(export_filename("AC/DC"), "AC-DC")
        self.assertEqual(export_filename('a:b*c?d"e<f>g|h'), "a-b-c-d-e-f-g-h")
        self.assertEqual(export_filename("  刘德华  "), "刘德华")
        self.assertEqual(export_filename("..."), "")


class MigrationTests(unittest.TestCase):
    def test_columns_exist(self) -> None:
        from app import db
        db.init_db()
        library_columns = {row[1] for row in db.query("PRAGMA table_info(libraries)")}
        for column in ("kind", "url", "username", "password_cipher"):
            self.assertIn(column, library_columns)
        version_columns = {row[1] for row in db.query("PRAGMA table_info(meta_versions)")}
        self.assertIn("source", version_columns)
        self.assertGreaterEqual(db.scalar("PRAGMA user_version", (), 0), 2)


if __name__ == "__main__":
    unittest.main()
