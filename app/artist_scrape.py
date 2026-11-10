"""Artist metadata scraping: portrait, biography and aliases.

Artists are derived from track tags, so the scraped profile lives in
``artist_meta`` keyed by ``artist_id`` and is merged into the ``v_artists`` view.
NetEase is the primary source (best coverage for Chinese libraries); Wikipedia
and Deezer are tried as fallbacks for biography and portraits.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from . import db, httpclient, library, navidrome, scrape
from .config import config
from .providers import similarity

NETEASE_HEADERS = {"Referer": "https://music.163.com/", "Origin": "https://music.163.com"}
IMAGE_EXTENSIONS = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
                    "image/webp": ".webp", "image/gif": ".gif"}


@dataclass
class ArtistCandidate:
    source: str
    source_id: str = ""
    name: str = ""
    image_url: str = ""
    bio: str = ""
    aliases: list[str] = field(default_factory=list)
    album_size: int = 0
    score: float = 0.0

    def as_dict(self) -> dict:
        return {
            "source": self.source,
            "source_id": self.source_id,
            "name": self.name,
            "image_url": self.image_url,
            "bio": self.bio,
            "aliases": self.aliases,
            "album_size": self.album_size,
            "score": round(self.score, 4),
        }


def _score(name: str, candidate: ArtistCandidate) -> float:
    names = [candidate.name, *candidate.aliases]
    best = max((similarity(name, item) for item in names if item), default=0.0)
    if candidate.image_url:
        best += 0.02
    if candidate.bio:
        best += 0.02
    return min(round(best, 4), 1.0)


# --------------------------------------------------------------------------- #
# providers
# --------------------------------------------------------------------------- #
def search_netease(name: str) -> list[ArtistCandidate]:
    url = httpclient.build_url("https://music.163.com/api/search/get", {
        "s": name, "type": 100, "limit": 5, "offset": 0,
    })
    payload = httpclient.fetch_json(url, headers=NETEASE_HEADERS, timeout=15)
    artists = ((payload.get("result") or {}).get("artists") or [])
    out: list[ArtistCandidate] = []
    for item in artists[:5]:
        source_id = str(item.get("id", ""))
        bio = ""
        image = item.get("picUrl") or item.get("img1v1Url") or ""
        if source_id:
            try:
                detail = httpclient.fetch_json(
                    f"https://music.163.com/api/artist/{source_id}",
                    headers=NETEASE_HEADERS, timeout=15,
                ).get("artist") or {}
                bio = (detail.get("briefDesc") or "").strip()
                image = detail.get("picUrl") or detail.get("img1v1Url") or image
            except Exception:  # noqa: BLE001 - profile is optional
                pass
        candidate = ArtistCandidate(
            source="netease",
            source_id=source_id,
            name=item.get("name") or "",
            image_url=image,
            bio=bio,
            aliases=[alias for alias in (item.get("alias") or []) if alias],
            album_size=int(item.get("albumSize") or 0),
        )
        candidate.score = _score(name, candidate)
        out.append(candidate)
    return out


def search_wikipedia(name: str) -> list[ArtistCandidate]:
    out: list[ArtistCandidate] = []
    for lang in ("zh", "en"):
        try:
            payload = httpclient.fetch_json(
                "https://" + lang + ".wikipedia.org/w/api.php?" + httpclient.build_url("", {
                    "action": "query", "titles": name, "prop": "extracts|pageimages",
                    "exintro": 1, "explaintext": 1, "pithumbsize": 600,
                    "format": "json", "redirects": 1,
                }).lstrip("?"),
                timeout=10,
            )
        except Exception:  # noqa: BLE001 - wikipedia is a fallback
            continue
        pages = ((payload.get("query") or {}).get("pages") or {})
        for page in pages.values():
            if "missing" in page:
                continue
            extract = (page.get("extract") or "").strip()
            thumb = ((page.get("thumbnail") or {}).get("source") or "")
            if not extract and not thumb:
                continue
            candidate = ArtistCandidate(
                source=f"wikipedia-{lang}",
                source_id=str(page.get("pageid", "")),
                name=page.get("title") or name,
                image_url=thumb,
                bio=extract[:4000],
            )
            candidate.score = _score(name, candidate)
            out.append(candidate)
        if out:
            break
    return out


def search_deezer(name: str) -> list[ArtistCandidate]:
    try:
        payload = httpclient.fetch_json(
            "https://api.deezer.com/search/artist?" + httpclient.build_url("", {
                "q": name, "limit": 5,
            }).lstrip("?"),
            timeout=10,
        )
    except Exception:  # noqa: BLE001
        return []
    out: list[ArtistCandidate] = []
    for item in (payload.get("data") or [])[:5]:
        candidate = ArtistCandidate(
            source="deezer",
            source_id=str(item.get("id", "")),
            name=item.get("name") or "",
            image_url=item.get("picture_xl") or item.get("picture_big") or "",
            album_size=int(item.get("nb_album") or 0),
        )
        candidate.score = _score(name, candidate)
        out.append(candidate)
    return out


PROVIDERS = {
    "netease": search_netease,
    "wikipedia": search_wikipedia,
    "deezer": search_deezer,
}


def enabled_providers() -> list[str]:
    names = json.loads(db.get_setting("artist.providers", '["netease","wikipedia","deezer"]') or "[]")
    return [name for name in names if name in PROVIDERS] or ["netease"]


def search(name: str, providers: list[str] | None = None) -> list[dict]:
    name = (name or "").strip()
    if not name:
        return []
    results: list[ArtistCandidate] = []
    for provider in providers or enabled_providers():
        handler = PROVIDERS.get(provider)
        if not handler:
            continue
        try:
            results.extend(handler(name))
        except Exception:  # noqa: BLE001 - one source must not break search
            continue
    seen: set[tuple[str, str]] = set()
    unique: list[ArtistCandidate] = []
    for candidate in sorted(results, key=lambda item: item.score, reverse=True):
        key = (candidate.source, candidate.source_id or candidate.name)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return [candidate.as_dict() for candidate in unique[:12]]


# --------------------------------------------------------------------------- #
# persistence
# --------------------------------------------------------------------------- #
def get_meta(artist_id: str) -> dict | None:
    row = db.one("SELECT * FROM artist_meta WHERE artist_id = ?", (artist_id,))
    if not row:
        return None
    meta = dict(row)
    try:
        meta["aliases"] = json.loads(meta.get("aliases") or "[]")
    except json.JSONDecodeError:
        meta["aliases"] = []
    return meta


def save_meta(artist_id: str, *, name: str | None = None, image_hash: str | None = None,
              bio: str | None = None, aliases: list[str] | None = None,
              source: str = "", source_id: str = "") -> dict:
    existing = get_meta(artist_id) or {}
    payload = {
        "artist_id": artist_id,
        "name": name if name is not None else existing.get("name", ""),
        "image_hash": image_hash if image_hash is not None else existing.get("image_hash", ""),
        "bio": bio if bio is not None else existing.get("bio", ""),
        "aliases": json.dumps(aliases if aliases is not None else existing.get("aliases", []),
                              ensure_ascii=False),
        "source": source or existing.get("source", ""),
        "source_id": source_id or existing.get("source_id", ""),
    }
    db.execute(
        "INSERT INTO artist_meta(artist_id, name, image_hash, bio, aliases, source, source_id, updated_at)"
        " VALUES(?, ?, ?, ?, ?, ?, ?, datetime('now'))"
        " ON CONFLICT(artist_id) DO UPDATE SET name = excluded.name,"
        " image_hash = excluded.image_hash, bio = excluded.bio, aliases = excluded.aliases,"
        " source = excluded.source, source_id = excluded.source_id, updated_at = datetime('now')",
        (payload["artist_id"], payload["name"], payload["image_hash"], payload["bio"],
         payload["aliases"], payload["source"], payload["source_id"]),
    )
    return get_meta(artist_id) or {}


def apply_candidate(artist_id: str, candidate: dict, user_id: int | None = None) -> dict:
    image_hash = None
    image_url = candidate.get("image_url") or ""
    if image_url:
        downloaded = scrape.download_cover(image_url)
        if downloaded:
            image_hash = library.store_cover(downloaded[1], downloaded[0])
    aliases = [str(alias) for alias in (candidate.get("aliases") or []) if alias]
    meta = save_meta(
        artist_id,
        image_hash=image_hash if image_hash else None,
        bio=str(candidate.get("bio") or "").strip() or None,
        aliases=aliases or None,
        source=str(candidate.get("source") or ""),
        source_id=str(candidate.get("source_id") or ""),
    )
    meta["artist_id"] = artist_id
    meta["user_id"] = user_id
    return meta


def auto_scrape(artist_id: str, user_id: int | None = None, ctx=None) -> dict:
    artist = db.one("SELECT artist_id, name FROM v_artists WHERE artist_id = ?", (artist_id,))
    if not artist:
        raise LookupError("artist not found")
    threshold = float(db.get_setting("artist.min_score", "0.5") or 0.5)
    candidates = search(artist["name"])
    if not candidates:
        return {"artist_id": artist_id, "matched": False, "reason": "no candidates"}
    best = candidates[0]
    if best["score"] < threshold:
        return {"artist_id": artist_id, "matched": False,
                "reason": f"best score {best['score']:.2f} < {threshold:.2f}"}
    if ctx and best.get("bio"):
        ctx.log(f"↳ {artist['name']}: {best['source']} score {best['score']:.2f}")
    apply_candidate(artist_id, best, user_id)
    exported = export_one(artist_id, ctx)
    return {"artist_id": artist_id, "matched": True, "score": best["score"],
            "source": best["source"], "exported": exported}


def batch_scrape(artist_ids: list[str], user_id: int | None, ctx) -> dict:
    matched = skipped = failed = exported = 0
    total = len(artist_ids)
    ctx.progress(0, total)
    for index, artist_id in enumerate(artist_ids, start=1):
        ctx.check_cancel()
        try:
            result = auto_scrape(artist_id, user_id, ctx)
            if result.get("matched"):
                matched += 1
                exported += int(result.get("exported") or 0)
            else:
                skipped += 1
                if ctx:
                    ctx.log(f"– {artist_id[:8]}: {result.get('reason')}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            ctx.log(f"! {artist_id[:8]}: {exc}", "warn")
        ctx.progress(index, total)
    ctx.log(f"artist scrape finished: {matched} matched, {skipped} skipped, {failed} failed")
    hook = navidrome.after_export(exported, user_id, reason=f"batch scrape {matched} artists")
    if exported:
        ctx.log(f"navidrome artwork: {hook.get('reason') or '已排队'}")
    return {"matched": matched, "skipped": skipped, "failed": failed, "total": total,
            "exported": exported, "navidrome": hook}


def export_filename(name: str) -> str:
    """Navidrome matches ArtistImageFolder files by exact (case-insensitive) name."""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "-", str(name or "")).strip()
    return cleaned.strip(".")[:150]


def export_images(folder: str | None = None, artist_ids: list[str] | None = None) -> dict:
    """Write scraped portraits as ``<artist name>.<ext>`` for Navidrome's
    ``ArtistImageFolder`` (config: ND_ARTISTIMAGEFOLDER)."""
    folder = (folder or db.get_setting("artist.image_folder", "") or "").strip()
    if not folder:
        raise ValueError("未配置艺术家图片导出目录（设置 → 刮削与标签 → 艺术家图片目录）")
    if not os.path.isdir(folder):
        raise FileNotFoundError(f"目录不存在：{folder}")
    sql = (
        "SELECT a.artist_id, a.name, m.image_hash FROM v_artists a"
        " JOIN artist_meta m ON m.artist_id = a.artist_id"
        " WHERE m.image_hash <> ''"
    )
    params: tuple = ()
    if artist_ids:
        placeholders = ", ".join("?" for _ in artist_ids)
        sql += f" AND a.artist_id IN ({placeholders})"
        params = tuple(artist_ids)
    rows = db.query(sql, params)
    exported = skipped = failed = 0
    for row in rows:
        cover = library.load_cover(row["image_hash"])
        if not cover:
            skipped += 1
            continue
        filename = export_filename(row["name"])
        if not filename:
            skipped += 1
            continue
        extension = IMAGE_EXTENSIONS.get(str(cover[0]).lower(), ".jpg")
        target = os.path.join(folder, filename + extension)
        tmp = target + ".omt-tmp"
        try:
            with open(tmp, "wb") as handle:
                handle.write(cover[1])
            os.replace(tmp, target)
            exported += 1
        except OSError:
            failed += 1
            try:
                os.remove(tmp)
            except OSError:
                pass
    return {"exported": exported, "skipped": skipped, "failed": failed, "folder": folder}


def export_one(artist_id: str, ctx=None) -> int:
    """Write this artist's portrait into the configured Navidrome folder.

    Never raises: a filesystem/permission problem must not fail the scrape.  The
    returned count feeds the Navidrome recompute hook.
    """
    folder = (db.get_setting("artist.image_folder", "") or "").strip()
    if not folder:
        return 0
    try:
        return int(export_images(folder, [artist_id]).get("exported") or 0)
    except Exception as exc:  # noqa: BLE001 - export must not fail the scrape
        if ctx:
            ctx.log(f"! export image: {exc}", "warn")
        return 0


def missing_portraits(limit: int = 500) -> list[str]:
    rows = db.query(
        "SELECT a.artist_id FROM v_artists a LEFT JOIN artist_meta m ON m.artist_id = a.artist_id"
        " WHERE (m.image_hash IS NULL OR m.image_hash = '') ORDER BY a.track_count DESC LIMIT ?",
        (limit,),
    )
    return [row["artist_id"] for row in rows]


__all__ = [
    "ArtistCandidate", "search", "apply_candidate", "auto_scrape", "batch_scrape",
    "get_meta", "save_meta", "missing_portraits", "enabled_providers", "PROVIDERS",
    "export_images", "export_filename", "export_one",
]
