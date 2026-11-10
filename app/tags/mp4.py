"""Minimal MP4/M4A metadata reader (tags are read-only for this format).

Writing MP4 tags safely requires relocating the moov atom and patching stco
offsets; a corrupt M4A is worse than an untouched one, so the app reports these
files as read-only in the UI instead of risking the file.
"""

from __future__ import annotations

CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta", b"ilst", b"meta", b"edts"}

TAG_MAP = {
    b"\xa9nam": "title",
    b"\xa9ART": "artist",
    b"aART": "albumartist",
    b"\xa9alb": "album",
    b"\xa9day": "year",
    b"\xa9gen": "genre",
    b"gnre": "genre",
    b"\xa9wrt": "composer",
    b"\xa9cmt": "comment",
    b"\xa9lyr": "lyrics",
    b"covr": "cover",
    b"trkn": "track_no",
    b"disk": "disc_no",
    b"tmpo": "bpm",
}


def _atoms(data: bytes, start: int = 0, end: int | None = None):
    end = len(data) if end is None else end
    pos = start
    while pos + 8 <= end:
        size = int.from_bytes(data[pos:pos + 4], "big")
        atom = data[pos + 4:pos + 8]
        header = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = int.from_bytes(data[pos + 8:pos + 16], "big")
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        yield atom, pos + header, pos + size
        pos += size


def _find(data: bytes, path: list[bytes]):
    """Yield (payload_start, payload_end) for atoms matching the path."""
    results = [(0, len(data))]
    for name in path:
        nxt = []
        for start, end in results:
            for atom, ps, pe in _atoms(data, start, end):
                if atom == name:
                    if name == b"meta":
                        ps += 4  # full box version + flags
                    nxt.append((ps, pe))
        results = nxt
        if not results:
            return []
    return results


def _to_int(raw: bytes) -> int | None:
    if not raw:
        return None
    return int.from_bytes(raw, "big")


def read(path: str) -> dict:
    with open(path, "rb") as fh:
        data = fh.read()
    tags: dict = {"codec": "M4A"}
    for start, end in _find(data, [b"moov", b"mvhd"]):
        payload = data[start:end]
        if not payload:
            continue
        version = payload[0]
        if version == 0 and len(payload) >= 20:
            timescale = int.from_bytes(payload[12:16], "big")
            duration = int.from_bytes(payload[16:20], "big")
        elif version == 1 and len(payload) >= 32:
            timescale = int.from_bytes(payload[20:24], "big")
            duration = int.from_bytes(payload[24:32], "big")
        else:
            continue
        if timescale:
            tags["duration"] = round(duration / timescale, 3)
        break

    for start, end in _find(data, [b"moov", b"udta", b"meta", b"ilst"]):
        for atom, ps, pe in _atoms(data, start, end):
            key = TAG_MAP.get(atom)
            if key is None:
                continue
            for sub, sps, spe in _atoms(data, ps, pe):
                if sub != b"data" or spe - sps < 8:
                    continue
                kind = int.from_bytes(data[sps:sps + 4], "big") & 0xFFFFFF
                value = data[sps + 8:spe]
                if key == "cover":
                    mime = "image/png" if kind == 14 else "image/jpeg"
                    if value:
                        tags["cover"] = (mime, value)
                elif key in ("track_no", "disc_no"):
                    if len(value) >= 4:
                        tags[key] = int.from_bytes(value[2:4], "big") or None
                elif key in ("year", "bpm"):
                    tags[key] = _to_int(value)
                elif key == "genre" and atom == b"gnre" and len(value) == 2:
                    from .id3 import ID3V1_GENRES
                    index = int.from_bytes(value, "big") - 1
                    if 0 <= index < len(ID3V1_GENRES):
                        tags["genre"] = ID3V1_GENRES[index]
                else:
                    tags[key] = value.decode("utf-8", errors="replace")
    return tags
