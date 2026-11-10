"""Tag source plugin registry.

A provider turns a search query into candidates. Built-ins cover filename
parsing, MusicBrainz (+ Cover Art Archive), NetEase and LRCLIB; declarative JSON
plugins let an administrator add another HTTP API without shipping code.
"""

from __future__ import annotations

import difflib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Protocol

from .. import db, httpclient
from ..config import config

PLUGIN_DIR = config.data_dir / "plugins"


@dataclass
class Candidate:
    provider: str
    source_id: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    albumartist: str = ""
    year: int | None = None
    track_no: int | None = None
    disc_no: int | None = None
    genre: str = ""
    duration: float = 0.0
    cover_url: str = ""
    lyrics: str = ""
    composer: str = ""
    isrc: str = ""
    mb_album_id: str = ""
    mb_track_id: str = ""
    score: float = 0.0
    extra: dict = field(default_factory=dict)

    def fields(self) -> dict:
        return {
            "title": self.title,
            "artist": self.artist,
            "albumartist": self.albumartist or self.artist,
            "album": self.album,
            "year": self.year,
            "track_no": self.track_no,
            "disc_no": self.disc_no,
            "genre": self.genre,
            "composer": self.composer,
            "isrc": self.isrc,
            "mb_album_id": self.mb_album_id,
            "mb_track_id": self.mb_track_id,
            "lyrics": self.lyrics,
        }

    def as_dict(self) -> dict:
        data = dict(self.__dict__)
        data.pop("extra", None)
        return data


class Provider(Protocol):
    name: str
    label: str
    needs_network: bool

    def search(self, query: dict) -> list[Candidate]: ...


def similarity(a: str, b: str) -> float:
    a = normalize(a)
    b = normalize(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def normalize(value: str) -> str:
    value = str(value or "").lower()
    value = re.sub(r"[\(\[].*?[\)\]]", " ", value)
    value = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", value)
    return " ".join(value.split())


def score_candidate(candidate: Candidate, query: dict) -> float:
    title = similarity(candidate.title, query.get("title", ""))
    artist = max(
        similarity(candidate.artist, query.get("artist", "")),
        similarity(candidate.albumartist, query.get("albumartist", "")),
    )
    album = similarity(candidate.album, query.get("album", "")) if query.get("album") else 0.5
    duration = 0.0
    if query.get("duration") and candidate.duration:
        duration = max(0.0, 1 - abs(float(candidate.duration) - float(query["duration"])) / 12)
    score = title * 0.55 + artist * 0.3 + album * 0.1 + duration * 0.05
    if not query.get("album"):
        score = title * 0.62 + artist * 0.33 + duration * 0.05
    return round(min(score, 1.0), 4)


# --------------------------------------------------------------------------- #
def _parse_filename(path: str) -> dict:
    stem = os.path.splitext(os.path.basename(path))[0]
    parent = os.path.basename(os.path.dirname(path))
    grandparent = os.path.basename(os.path.dirname(os.path.dirname(path)))
    stem = re.sub(r"^\s*\d{1,3}[\s._\-]+", "", stem)
    artist, title = "", stem
    for sep in (" - ", " – ", "-", "—"):
        if sep in stem:
            left, right = stem.split(sep, 1)
            if left.strip() and right.strip():
                artist, title = left.strip(), right.strip()
                break
    track_no = None
    match = re.match(r"^\s*(\d{1,3})", os.path.basename(path))
    if match:
        track_no = int(match.group(1)) or None
    album = parent if parent and not re.fullmatch(r"(cd|disc|disk)\s*\d+", parent, re.I) else grandparent
    return {"title": title, "artist": artist, "album": album, "track_no": track_no}


class FilenameProvider:
    name = "filename"
    label = "Filename parser"
    needs_network = False

    def search(self, query: dict) -> list[Candidate]:
        parsed = _parse_filename(query.get("path", ""))
        artist = query.get("artist") or parsed["artist"]
        title = query.get("title") or parsed["title"]
        if not title:
            return []
        candidate = Candidate(
            provider=self.name,
            source_id="filename",
            title=title,
            artist=artist,
            album=query.get("album") or parsed["album"],
            albumartist=artist,
            track_no=query.get("track_no") or parsed["track_no"],
        )
        candidate.score = score_candidate(candidate, query)
        return [candidate]


class MusicBrainzProvider:
    name = "musicbrainz"
    label = "MusicBrainz"
    needs_network = True

    def search(self, query: dict) -> list[Candidate]:
        parts = []
        if query.get("title"):
            parts.append(f'recording:"{query["title"]}"')
        if query.get("artist"):
            parts.append(f'artist:"{query["artist"]}"')
        if query.get("album"):
            parts.append(f'release:"{query["album"]}"')
        if not parts:
            return []
        url = httpclient.build_url("https://musicbrainz.org/ws/2/recording", {
            "query": " AND ".join(parts), "fmt": "json", "limit": 8,
        })
        payload = httpclient.fetch_json(
            url, headers={"Accept": "application/json"}, min_interval=1.05
        )
        candidates: list[Candidate] = []
        for recording in payload.get("recordings", []) or []:
            releases = recording.get("releases") or []
            release = releases[0] if releases else {}
            media = (release.get("media") or [{}])[0]
            tracks = media.get("tracks") or [{}]
            artist_credit = "".join(
                (credit.get("name") or "") + (credit.get("joinphrase") or "")
                for credit in recording.get("artist-credit", [])
            )
            candidate = Candidate(
                provider=self.name,
                source_id=recording.get("id", ""),
                title=recording.get("title", ""),
                artist=artist_credit,
                album=release.get("title", ""),
                albumartist=artist_credit,
                year=_year(release.get("date")),
                track_no=_int(tracks[0].get("number")),
                disc_no=_int(media.get("position")),
                duration=(recording.get("length") or 0) / 1000,
                mb_album_id=release.get("id", ""),
                mb_track_id=recording.get("id", ""),
                isrc=((recording.get("isrcs") or [""])[0]),
            )
            if candidate.mb_album_id:
                candidate.cover_url = f"https://coverartarchive.org/release/{candidate.mb_album_id}/front-500"
            candidate.score = score_candidate(candidate, query)
            candidates.append(candidate)
        return candidates


class NetEaseProvider:
    name = "netease"
    label = "NetEase Cloud Music"
    needs_network = True

    def search(self, query: dict) -> list[Candidate]:
        keyword = " ".join(filter(None, [query.get("artist"), query.get("title")])).strip()
        if not keyword:
            return []
        url = httpclient.build_url("https://music.163.com/api/search/get", {
            "s": keyword, "type": 1, "limit": 6, "offset": 0,
        })
        payload = httpclient.fetch_json(url, headers={"Referer": "https://music.163.com/"})
        songs = ((payload.get("result") or {}).get("songs") or [])
        out: list[Candidate] = []
        for song in songs:
            artists = "/".join(a.get("name", "") for a in song.get("artists", []))
            album = song.get("album") or {}
            candidate = Candidate(
                provider=self.name,
                source_id=str(song.get("id", "")),
                title=song.get("name", ""),
                artist=artists,
                album=album.get("name", ""),
                albumartist=artists,
                duration=(song.get("duration") or 0) / 1000,
                cover_url=album.get("picUrl", "") or "",
            )
            candidate.score = score_candidate(candidate, query)
            out.append(candidate)
        return out

    def lyrics(self, query) -> str:
        # The scrape pipeline calls lyrics() with a plain dict, while candidate
        # objects also reach this method; accept both instead of failing with
        # AttributeError (which fetch_lyrics would swallow silently).
        if isinstance(query, Candidate):
            data = {"artist": query.artist, "title": query.title, "source_id": query.source_id}
        else:
            data = query or {}
        source_id = str(data.get("source_id") or data.get("id") or "")
        if not source_id and (data.get("artist") or data.get("title")):
            found = [item for item in self.search({
                "artist": data.get("artist"), "title": data.get("title"),
            }) if item.source_id]
            source_id = found[0].source_id if found else ""
        if not source_id:
            return ""
        url = httpclient.build_url("https://music.163.com/api/song/lyric", {
            "id": source_id, "lv": 1, "tv": -1,
        })
        payload = httpclient.fetch_json(url, headers={"Referer": "https://music.163.com/"})
        return ((payload.get("lrc") or {}).get("lyric") or "").strip()


class LrclibProvider:
    name = "lrclib"
    label = "LRCLIB lyrics"
    needs_network = True

    def search(self, query: dict) -> list[Candidate]:
        return []

    def lyrics(self, query: dict) -> str:
        url = httpclient.build_url("https://lrclib.net/api/get", {
            "artist_name": query.get("artist", ""),
            "track_name": query.get("title", ""),
            "album_name": query.get("album", "") or None,
            "duration": int(query["duration"]) if query.get("duration") else None,
        })
        try:
            payload = httpclient.fetch_json(url, headers={"Lrclib-Client": config.user_agent})
        except Exception:  # noqa: BLE001 - fall back to search endpoint
            url = httpclient.build_url("https://lrclib.net/api/search", {
                "track_name": query.get("title", ""),
                "artist_name": query.get("artist", ""),
            })
            payload = httpclient.fetch_json(url, headers={"Lrclib-Client": config.user_agent})
            if isinstance(payload, list) and payload:
                payload = payload[0]
            else:
                return ""
        return (payload.get("syncedLyrics") or payload.get("plainLyrics") or "").strip()


class JsonPluginProvider:
    """Declarative plugin: JSON file in data/plugins describing an HTTP API."""

    needs_network = True

    def __init__(self, name: str, spec: dict) -> None:
        self.name = name
        self.label = spec.get("label") or name
        self.spec = spec

    def search(self, query: dict) -> list[Candidate]:
        spec = self.spec.get("search") or {}
        url = (spec.get("url") or "").format(**{k: httpclient.quote(v) for k, v in query.items()})
        if not url:
            return []
        payload = httpclient.fetch_json(url, headers=spec.get("headers") or {})
        items = _dig(payload, spec.get("items_path") or "")
        if not isinstance(items, list):
            return []
        mapping = spec.get("fields") or {}
        out: list[Candidate] = []
        for item in items[:10]:
            def pick(key: str) -> str:
                path = mapping.get(key)
                return "" if not path else str(_dig(item, path) or "")

            candidate = Candidate(
                provider=self.name,
                source_id=pick("id"),
                title=pick("title"),
                artist=pick("artist"),
                album=pick("album"),
                albumartist=pick("albumartist") or pick("artist"),
                year=_int(pick("year")),
                track_no=_int(pick("track_no")),
                duration=_float(pick("duration")),
                cover_url=pick("cover"),
                genre=pick("genre"),
            )
            candidate.score = score_candidate(candidate, query)
            out.append(candidate)
        return out


def _dig(data, path: str):
    if not path:
        return data
    current = data
    for part in path.split("."):
        if part.endswith("]") and "[" in part:
            name, _, index = part.partition("[")
            index = index.rstrip("]")
            if name:
                current = current.get(name) if isinstance(current, dict) else None
            if current is None:
                return None
            try:
                current = current[int(index)]
            except (ValueError, IndexError, TypeError):
                return None
            continue
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current


def _int(value) -> int | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return int(digits) if digits else None


def _float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _year(value) -> int | None:
    return _int(str(value or "")[:4])


# --------------------------------------------------------------------------- #
class Registry:
    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> Provider | None:
        return self._providers.get(name)

    def all(self) -> list[Provider]:
        return list(self._providers.values())

    def enabled(self) -> list[str]:
        names = json.loads(db.get_setting("scrape.providers", "[]") or "[]")
        return [name for name in names if name in self._providers] or ["filename"]

    def load_plugins(self) -> None:
        PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        for path in sorted(PLUGIN_DIR.glob("*.json")):
            try:
                spec = json.loads(path.read_text(encoding="utf-8"))
                name = spec.get("name") or path.stem
                self.register(JsonPluginProvider(name, spec))
                db.execute(
                    "INSERT OR IGNORE INTO tag_sources(name, kind, enabled, config, builtin)"
                    " VALUES(?, 'json', 1, ?, 0)",
                    (name, json.dumps(spec, ensure_ascii=False)),
                )
            except (OSError, ValueError):
                continue


registry = Registry()
registry.register(FilenameProvider())
registry.register(MusicBrainzProvider())
registry.register(NetEaseProvider())
registry.register(LrclibProvider())
