"""Tag engine facade.

``read`` returns a normalised dictionary regardless of container format;
``write`` supports the formats where a safe rewrite is possible (MP3 + FLAC).
Unsupported containers stay readable and are flagged read-only by the UI.
"""

from __future__ import annotations

import os
import struct

from . import flac as flac_tags
from . import id3 as id3_tags
from . import mp4 as mp4_tags
from . import probe as probe_mod

WRITABLE = {".mp3", ".flac"}

FORMAT_EXTENSIONS = {
    "MP3": {".mp3", ".mp2", ".mpa"},
    "FLAC": {".flac"},
}

CODEC_NAMES = {
    ".mp3": "MP3",
    ".flac": "FLAC",
    ".m4a": "M4A",
    ".mp4": "M4A",
    ".ogg": "OGG",
    ".oga": "OGG",
    ".opus": "OPUS",
    ".wav": "WAV",
    ".aiff": "AIFF",
    ".aif": "AIFF",
    ".ape": "APE",
    ".wv": "WavPack",
    ".dsf": "DSD",
}

TEXT_FIELDS = (
    "title",
    "artist",
    "albumartist",
    "album",
    "genre",
    "composer",
    "lyricist",
    "comment",
    "lyrics",
    "isrc",
    "mb_album_id",
    "mb_track_id",
)


class UnsupportedFormat(Exception):
    pass


def can_write(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in WRITABLE


def is_writable(path: str, codec: str | None = None) -> bool:
    """Extension must match the sniffed container before we rewrite a file."""
    ext = os.path.splitext(path)[1].lower()
    if codec:
        return ext in FORMAT_EXTENSIONS.get(str(codec).upper(), set())
    return ext in WRITABLE


def read(path: str) -> dict:
    ext = os.path.splitext(path)[1].lower()
    tags: dict = {}
    try:
        if ext in (".mp3", ".mp2", ".mpa"):
            tags, _extras, audio_offset = id3_tags.read(path)
            tags.update(probe_mod.probe_mp3(path, audio_offset))
        elif ext == ".flac":
            tags.update(flac_tags.read(path))
        elif ext in (".m4a", ".mp4"):
            tags.update(mp4_tags.read(path))
        elif ext == ".wav":
            tags.update(_wav_info(path))
        else:
            tags.setdefault("codec", CODEC_NAMES.get(ext, ext.lstrip(".").upper()))
            tags.setdefault("duration", 0.0)
    except (ValueError, OSError, struct.error, IndexError):
        # Wrong extension is common in real libraries (MP3 renamed to .flac,
        # RIFF files called .mp3 ...). Sniff the magic bytes and retry.
        tags = _read_by_magic(path)
        if not tags:
            tags = {"codec": CODEC_NAMES.get(ext, ext.lstrip(".").upper()), "duration": 0.0}
    tags.setdefault("codec", CODEC_NAMES.get(ext, ext.lstrip(".").upper()))
    duration = float(tags.get("duration") or 0)
    if ext == ".flac" and duration > 0 and tags.get("codec") == "FLAC":
        tags["bitrate"] = int(os.path.getsize(path) * 8 / duration / 1000)
    tags["ext"] = ext
    tags["writable"] = is_writable(path, tags.get("codec"))
    return tags


def _read_by_magic(path: str) -> dict:
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return {}
    try:
        if head[:4] == b"fLaC":
            result = flac_tags.read(path)
            result["codec"] = "FLAC"
            return result
        if head[:3] == b"ID3" or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
            result, _extras, audio_offset = id3_tags.read(path)
            result.update(probe_mod.probe_mp3(path, audio_offset))
            result["codec"] = "MP3"
            return result
        if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
            result = _wav_info(path)
            result["codec"] = "WAV"
            return result
        if head[4:8] == b"ftyp":
            result = mp4_tags.read(path)
            result["codec"] = "M4A"
            return result
    except (ValueError, OSError, struct.error, IndexError):
        return {}
    return {}


def _wav_info(path: str) -> dict:
    try:
        with open(path, "rb") as fh:
            header = fh.read(12)
            if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WAVE":
                return {"codec": "WAV"}
            info = {"codec": "WAV"}
            while True:
                chunk = fh.read(8)
                if len(chunk) < 8:
                    break
                name, size = chunk[:4], struct.unpack("<I", chunk[4:8])[0]
                if name == b"fmt ":
                    body = fh.read(size)
                    if len(body) >= 16:
                        channels, rate = struct.unpack("<HI", body[2:8])
                        bits = struct.unpack("<H", body[14:16])[0]
                        info.update(
                            channels=channels,
                            sample_rate=rate,
                            bit_depth=bits,
                            bitrate=int(rate * channels * bits / 1000),
                        )
                elif name == b"data":
                    rate = info.get("sample_rate", 0)
                    bits = info.get("bit_depth", 0)
                    channels = info.get("channels", 0)
                    if rate and bits and channels:
                        info["duration"] = round(size / (rate * channels * bits / 8), 3)
                    fh.seek(size, 1)
                else:
                    fh.seek(size + (size & 1), 1)
                if size & 1 and name == b"fmt ":
                    fh.seek(1, 1)
            return info
    except (OSError, struct.error):
        return {"codec": "WAV"}


def write(path: str, fields: dict) -> None:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".mp3", ".mp2", ".mpa"):
        _write_mp3(path, fields)
    elif ext == ".flac":
        flac_tags.write(path, fields)
    else:
        raise UnsupportedFormat(f"tag writing is not supported for {ext or 'this file'}")


def _write_mp3(path: str, fields: dict) -> None:
    _old, extras, audio_offset = id3_tags.read(path)
    tag = id3_tags.build(fields, extras)
    size = os.path.getsize(path)
    end = size
    with open(path, "rb") as fh:
        if size >= 128:
            fh.seek(-128, 2)
            if fh.read(3) == b"TAG":
                end -= 128
    tmp = f"{path}.omt-tmp"
    try:
        with open(path, "rb") as src, open(tmp, "wb") as dst:
            dst.write(tag)
            src.seek(audio_offset)
            remaining = end - audio_offset
            while remaining > 0:
                chunk = src.read(min(1 << 20, remaining))
                if not chunk:
                    break
                dst.write(chunk)
                remaining -= len(chunk)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
