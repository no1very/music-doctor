"""FLAC metadata: STREAMINFO parsing plus Vorbis comment / PICTURE rewriting."""

from __future__ import annotations

import os
import struct

TYPE_STREAMINFO = 0
TYPE_PADDING = 1
TYPE_APPLICATION = 2
TYPE_SEEKTABLE = 3
TYPE_VORBIS_COMMENT = 4
TYPE_CUESHEET = 5
TYPE_PICTURE = 6

COMMENT_MAP = [
    ("title", "TITLE"),
    ("artist", "ARTIST"),
    ("albumartist", "ALBUMARTIST"),
    ("album", "ALBUM"),
    ("genre", "GENRE"),
    ("composer", "COMPOSER"),
    ("lyricist", "LYRICIST"),
    ("comment", "COMMENT"),
    ("lyrics", "LYRICS"),
    ("isrc", "ISRC"),
    ("mb_album_id", "MUSICBRAINZ_ALBUMID"),
    ("mb_track_id", "MUSICBRAINZ_TRACKID"),
]


def is_flac(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == b"fLaC"
    except OSError:
        return False


def _read_blocks(path: str) -> tuple[list[tuple[int, bytes]], int]:
    blocks: list[tuple[int, bytes]] = []
    with open(path, "rb") as fh:
        if fh.read(4) != b"fLaC":
            raise ValueError("not a FLAC file")
        while True:
            header = fh.read(4)
            if len(header) < 4:
                raise ValueError("truncated FLAC metadata")
            last = bool(header[0] & 0x80)
            btype = header[0] & 0x7F
            length = int.from_bytes(header[1:4], "big")
            data = fh.read(length)
            if len(data) != length:
                raise ValueError("truncated FLAC metadata block")
            blocks.append((btype, data))
            if last:
                break
        audio_offset = fh.tell()
    return blocks, audio_offset


def _streaminfo(data: bytes) -> dict:
    if len(data) < 34:
        return {}
    bits = int.from_bytes(data[10:18], "big")
    sample_rate = (bits >> 44) & 0xFFFFF
    channels = ((bits >> 41) & 0x07) + 1
    bit_depth = ((bits >> 36) & 0x1F) + 1
    total_samples = bits & 0xFFFFFFFFF
    duration = total_samples / sample_rate if sample_rate else 0.0
    return {
        "sample_rate": sample_rate,
        "channels": channels,
        "bit_depth": bit_depth,
        "duration": round(duration, 3),
        "total_samples": total_samples,
        "bitrate": int(bit_depth * sample_rate * channels / 1000) if sample_rate else 0,
    }


def _parse_comments(data: bytes) -> dict[str, list[str]]:
    comments: dict[str, list[str]] = {}
    pos = 0
    if len(data) < 4:
        return comments
    vendor_len = int.from_bytes(data[pos:pos + 4], "little")
    pos += 4 + vendor_len
    if pos + 4 > len(data):
        return comments
    count = int.from_bytes(data[pos:pos + 4], "little")
    pos += 4
    for _ in range(min(count, 4096)):
        if pos + 4 > len(data):
            break
        length = int.from_bytes(data[pos:pos + 4], "little")
        pos += 4
        chunk = data[pos:pos + length]
        pos += length
        if b"=" not in chunk:
            continue
        key, _, value = chunk.partition(b"=")
        name = key.decode("ascii", errors="replace").upper()
        comments.setdefault(name, []).append(value.decode("utf-8", errors="replace"))
    return comments


def read(path: str) -> dict:
    blocks, _ = _read_blocks(path)
    tags: dict = {"codec": "FLAC"}
    comments: dict[str, list[str]] = {}
    for btype, data in blocks:
        if btype == TYPE_STREAMINFO:
            tags.update(_streaminfo(data))
        elif btype == TYPE_VORBIS_COMMENT:
            comments = _parse_comments(data)
        elif btype == TYPE_PICTURE and "cover" not in tags:
            cover = _parse_picture(data)
            if cover:
                tags["cover"] = cover

    reverse = {value: key for key, value in COMMENT_MAP}
    for name, values in comments.items():
        key = reverse.get(name)
        value = values[0] if values else ""
        if key:
            if key == "lyrics":
                tags["lyrics"] = value
            elif key in ("mb_album_id", "mb_track_id"):
                tags[key] = value
            else:
                tags[key] = value
        elif name in ("YEAR", "DATE") and "year" not in tags:
            tags["year"] = _to_int(value[:4])
        elif name == "UNSYNCEDLYRICS" and "lyrics" not in tags:
            tags["lyrics"] = value
        elif name == "TRACKNUMBER":
            tags["track_no"] = _to_int(value)
        elif name == "DISCNUMBER":
            tags["disc_no"] = _to_int(value)
        elif name == "BPM":
            tags["bpm"] = _to_int(value)
    # TRACKNUMBER/DISCNUMBER are handled above only when they are not already
    # present in COMMENT_MAP, so parse them explicitly as well.
    if "TRACKNUMBER" in comments and "track_no" not in tags:
        tags["track_no"] = _to_int(comments["TRACKNUMBER"][0])
    if "DISCNUMBER" in comments and "disc_no" not in tags:
        tags["disc_no"] = _to_int(comments["DISCNUMBER"][0])
    if "DATE" in comments and "year" not in tags:
        tags["year"] = _to_int(comments["DATE"][0][:4])
    if "YEAR" in comments and "year" not in tags:
        tags["year"] = _to_int(comments["YEAR"][0][:4])
    if "BPM" in comments and "bpm" not in tags:
        tags["bpm"] = _to_int(comments["BPM"][0])
    return tags


def _to_int(value: str | None) -> int | None:
    if not value:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return int(digits) if digits else None


def _parse_picture(data: bytes) -> tuple[str, bytes] | None:
    if len(data) < 32:
        return None
    try:
        pos = 4  # picture type
        mime_len = int.from_bytes(data[pos:pos + 4], "big")
        pos += 4
        mime = data[pos:pos + mime_len].decode("latin-1", errors="replace")
        pos += mime_len
        desc_len = int.from_bytes(data[pos:pos + 4], "big")
        pos += 4 + desc_len
        pos += 16  # width, height, depth, colors
        data_len = int.from_bytes(data[pos:pos + 4], "big")
        pos += 4
        picture = data[pos:pos + data_len]
        if picture:
            return mime or "image/jpeg", picture
    except (ValueError, IndexError):
        return None
    return None


def _picture_block(mime: str, data: bytes) -> bytes:
    head = struct.pack(">I", 3) + struct.pack(">I", len(mime)) + mime.encode("latin-1")
    head += struct.pack(">I", 0) + struct.pack(">IIII", 0, 0, 0, 0) + struct.pack(">I", len(data))
    return head + data


def _comment_block(fields: dict, vendor: str = "Music-Doctor") -> bytes:
    entries: list[bytes] = []
    for key, name in COMMENT_MAP:
        value = str(fields.get(key) or "").strip()
        if not value:
            continue
        entries.append(f"{name}={value}".encode("utf-8"))
    for key, name in (("track_no", "TRACKNUMBER"), ("disc_no", "DISCNUMBER"), ("year", "DATE")):
        value = fields.get(key)
        if value in (None, ""):
            continue
        entries.append(f"{name}={value}".encode("utf-8"))
    if fields.get("bpm"):
        entries.append(f"BPM={fields['bpm']}".encode("utf-8"))
    out = bytearray()
    raw_vendor = vendor.encode("utf-8")
    out += struct.pack("<I", len(raw_vendor)) + raw_vendor
    out += struct.pack("<I", len(entries))
    for entry in entries:
        out += struct.pack("<I", len(entry)) + entry
    return bytes(out)


def write(path: str, fields: dict, vendor: str = "Music-Doctor") -> None:
    blocks, audio_offset = _read_blocks(path)
    keep: list[tuple[int, bytes]] = []
    for btype, data in blocks:
        if btype in (TYPE_PADDING, TYPE_SEEKTABLE, TYPE_VORBIS_COMMENT, TYPE_PICTURE):
            continue
        keep.append((btype, data))
    if not keep or keep[0][0] != TYPE_STREAMINFO:
        raise ValueError("FLAC file has no STREAMINFO block")

    new_blocks: list[tuple[int, bytes]] = [keep[0], (TYPE_VORBIS_COMMENT, _comment_block(fields, vendor))]
    cover = fields.get("cover")
    if cover and cover[1]:
        new_blocks.append((TYPE_PICTURE, _picture_block(cover[0] or "image/jpeg", cover[1])))
    new_blocks.extend(keep[1:])

    tmp = f"{path}.omt-tmp"
    try:
        with open(path, "rb") as src, open(tmp, "wb") as dst:
            src.seek(audio_offset)
            dst.write(b"fLaC")
            for index, (btype, data) in enumerate(new_blocks):
                last = 0x80 if index == len(new_blocks) - 1 else 0x00
                dst.write(bytes([last | btype]) + len(data).to_bytes(3, "big") + data)
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                dst.write(chunk)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
