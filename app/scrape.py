"""Scraping pipeline: candidate search, cover/lyrics fetching, batch jobs."""

from __future__ import annotations

import mimetypes

from . import db, httpclient, library, providers

MAX_COVER_BYTES = 6 << 20


def track_query(track: dict) -> dict:
    return {
        "path": track.get("path", ""),
        "title": track.get("title", ""),
        "artist": track.get("artist", ""),
        "albumartist": track.get("albumartist", ""),
        "album": track.get("album", ""),
        "duration": track.get("duration") or 0,
        "track_no": track.get("track_no"),
    }


def search(track_id: int | None = None, query: dict | None = None,
           names: list[str] | None = None, limit: int = 12) -> list[dict]:
    if track_id is not None:
        track = library.get_track(track_id)
        if not track:
            raise LookupError("track not found")
        query = track_query(track)
    query = query or {}
    names = names or providers.registry.enabled()
    results: list[dict] = []
    for name in names:
        provider = providers.registry.get(name)
        if not provider:
            continue
        try:
            candidates = provider.search(query)
        except Exception:  # noqa: BLE001 - one failing source must not break search
            continue
        for candidate in candidates:
            if not candidate.score:
                candidate.score = providers.score_candidate(candidate, query)
            results.append(candidate.as_dict())
    results.sort(key=lambda item: item.get("score", 0), reverse=True)
    for item in results:
        if not item.get("cover_url") and item.get("mb_album_id"):
            item["cover_url"] = f"https://coverartarchive.org/release/{item['mb_album_id']}/front-500"
    return results[:limit]


def download_cover(url: str) -> tuple[str, bytes] | None:
    if not url:
        return None
    try:
        raw = httpclient.fetch(url, max_bytes=MAX_COVER_BYTES, min_interval=0.0)
    except Exception:  # noqa: BLE001 - covers are best effort
        return None
    if not raw:
        return None
    mime = "image/jpeg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        mime = "image/webp"
    elif raw[:6] in (b"GIF87a", b"GIF89a"):
        mime = "image/gif"
    return mime, raw


def fetch_lyrics(query: dict, provider_names: list[str] | None = None) -> str:
    names = provider_names or [db.get_setting("lyrics.provider", "lrclib")]
    for name in names:
        provider = providers.registry.get(name)
        if provider is None or not hasattr(provider, "lyrics"):
            continue
        try:
            lyrics = provider.lyrics(query)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            continue
        if lyrics:
            return lyrics
    return ""


def apply_candidate(track_id: int, candidate: dict, user_id: int | None,
                    with_cover: bool = True, with_lyrics: bool = True) -> dict:
    fields = {key: candidate.get(key) for key in (
        "title", "artist", "albumartist", "album", "year", "track_no", "disc_no",
        "genre", "composer", "isrc", "mb_album_id", "mb_track_id", "lyrics",
    ) if candidate.get(key) not in (None, "")}
    lyrics = fields.get("lyrics") or ""
    if with_lyrics and not lyrics:
        lyrics = fetch_lyrics({**candidate, "duration": candidate.get("duration") or 0})
        if lyrics:
            fields["lyrics"] = lyrics
    cover = None
    if with_cover and candidate.get("cover_url"):
        cover = download_cover(candidate["cover_url"])
    return library.update_track(
        track_id, fields, user_id=user_id, action="scrape", cover=cover,
        source=str(candidate.get("provider") or ""),
        write_file=db.get_setting("scrape.write_tags", "1") == "1",
    )


def auto_scrape_track(track: dict, user_id: int | None = None, ctx=None) -> dict:
    threshold = float(db.get_setting("scrape.min_score", "0.72") or 0.72)
    query = track_query(track)
    candidates = search(query=query, limit=8)
    if not candidates:
        return {"track_id": track["id"], "matched": False, "reason": "no candidates"}
    best = candidates[0]
    if best["score"] < threshold:
        return {
            "track_id": track["id"], "matched": False,
            "reason": f"best score {best['score']:.2f} < {threshold:.2f}",
        }
    with_cover = db.get_setting("scrape.write_cover", "1") == "1"
    with_lyrics = db.get_setting("scrape.write_lyrics", "1") == "1"
    if ctx:
        ctx.log(f"↳ {track.get('rel_path') or track.get('title')}: {best['provider']} "
                f"score {best['score']:.2f} → {best.get('artist')} - {best.get('title')}")
    updated = apply_candidate(track["id"], best, user_id, with_cover, with_lyrics)
    return {"track_id": track["id"], "matched": True, "score": best["score"],
            "provider": best["provider"], "track": updated}


def batch_scrape(track_ids: list[int], user_id: int | None, ctx,
                 only_untagged: bool = False) -> dict:
    matched = skipped = failed = 0
    total = len(track_ids)
    ctx.progress(0, total)
    for index, track_id in enumerate(track_ids, start=1):
        ctx.check_cancel()
        track = library.get_track(track_id)
        if not track:
            skipped += 1
            ctx.progress(index, total)
            continue
        if only_untagged and track.get("tagged"):
            skipped += 1
            ctx.progress(index, total)
            continue
        try:
            result = auto_scrape_track(track, user_id, ctx)
            if result.get("matched"):
                matched += 1
            else:
                skipped += 1
                ctx.log(f"– {track.get('rel_path') or track.get('title')}: {result.get('reason')}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            ctx.log(f"! {track.get('rel_path')}: {exc}", "warn")
        ctx.progress(index, total)
    ctx.log(f"scrape finished: {matched} matched, {skipped} skipped, {failed} failed")
    return {"matched": matched, "skipped": skipped, "failed": failed, "total": total}
