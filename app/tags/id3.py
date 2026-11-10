"""Frame level ID3v2 reader/writer (v2.2, v2.3 and v2.4).

Only the frames this application manages are interpreted; unknown frames are
preserved verbatim on write so third party tags (ReplayGain, MusicBrainz disc
ids, ...) survive a metadata edit.
"""

from __future__ import annotations

import struct

FIELDS = {
    "title": "TIT2",
    "artist": "TPE1",
    "albumartist": "TPE2",
    "album": "TALB",
    "year": "TDRC",
    "genre": "TCON",
    "composer": "TCOM",
    "lyricist": "TEXT",
    "isrc": "TSRC",
    "bpm": "TBPM",
    "comment": "COMM",
    "lyrics": "USLT",
    "cover": "APIC",
    "track_no": "TRCK",
    "disc_no": "TPOS",
    "mb_album_id": "TXXX:MusicBrainz Album Id",
    "mb_track_id": "TXXX:MusicBrainz Release Track Id",
}

# v2.2 three character ids mapped onto their v2.3 equivalents
V22 = {
    "TT2": "TIT2",
    "TP1": "TPE1",
    "TP2": "TPE2",
    "TAL": "TALB",
    "TYE": "TYER",
    "TCO": "TCON",
    "TCM": "TCOM",
    "TXT": "TEXT",
    "TRK": "TRCK",
    "TPA": "TPOS",
    "COM": "COMM",
    "ULT": "USLT",
    "PIC": "APIC",
    "TBP": "TBPM",
}

MANAGED = {"TIT2", "TPE1", "TPE2", "TALB", "TYER", "TDRC", "TCON", "TCOM", "TEXT",
           "TSRC", "TBPM", "COMM", "USLT", "APIC", "TRCK", "TPOS", "TXXX"}

ID3V1_GENRES = (
    "Blues", "Classic Rock", "Country", "Dance", "Disco", "Funk", "Grunge", "Hip-Hop",
    "Jazz", "Metal", "New Age", "Oldies", "Other", "Pop", "R&B", "Rap", "Reggae", "Rock",
    "Techno", "Industrial", "Alternative", "Ska", "Death Metal", "Pranks", "Soundtrack",
    "Euro-Techno", "Ambient", "Trip-Hop", "Vocal", "Jazz+Funk", "Fusion", "Trance",
    "Classical", "Instrumental", "Acid", "House", "Game", "Sound Clip", "Gospel", "Noise",
    "AlternRock", "Bass", "Soul", "Punk", "Space", "Meditative", "Instrumental Pop",
    "Instrumental Rock", "Ethnic", "Gothic", "Darkwave", "Techno-Industrial", "Electronic",
    "Pop-Folk", "Eurodance", "Dream", "Southern Rock", "Comedy", "Cult", "Gangsta", "Top 40",
    "Christian Rap", "Pop/Funk", "Jungle", "Native American", "Cabaret", "New Wave",
    "Psychedelic", "Rave", "Showtunes", "Trailer", "Lo-Fi", "Tribal", "Acid Punk",
    "Acid Jazz", "Polka", "Retro", "Musical", "Rock & Roll", "Hard Rock", "Folk",
    "Folk-Rock", "National Folk", "Swing", "Fast Fusion", "Bebob", "Latin", "Revival",
    "Celtic", "Bluegrass", "Avantgarde", "Gothic Rock", "Progressive Rock", "Psychedelic Rock",
    "Symphonic Rock", "Slow Rock", "Big Band", "Chorus", "Easy Listening", "Acoustic",
    "Humour", "Speech", "Chanson", "Opera", "Chamber Music", "Sonata", "Symphony",
    "Booty Bass", "Primus", "Porn Groove", "Satire", "Slow Jam", "Club", "Tango", "Samba",
    "Folklore", "Ballad", "Power Ballad", "Rhythmic Soul", "Freestyle", "Duet", "Punk Rock",
    "Drum Solo", "A Cappella", "Euro-House", "Dance Hall", "Goa", "Drum & Bass", "Club-House",
    "Hardcore Techno", "Terror", "Indie", "BritPop", "Negerpunk", "Polsk Punk", "Beat",
    "Christian Gangsta Rap", "Heavy Metal", "Black Metal", "Crossover", "Contemporary Christian",
    "Christian Rock", "Merengue", "Salsa", "Thrash Metal", "Anime", "JPop", "Synthpop",
)


def _syncsafe(data: bytes) -> int:
    return (data[0] << 21) | (data[1] << 14) | (data[2] << 7) | data[3]


def _to_syncsafe(value: int) -> bytes:
    return bytes([
        (value >> 21) & 0x7F,
        (value >> 14) & 0x7F,
        (value >> 7) & 0x7F,
        value & 0x7F,
    ])


def _deunsync(data: bytes) -> bytes:
    return data.replace(b"\xff\x00", b"\xff")


def _decode_text(payload: bytes) -> list[str]:
    if not payload:
        return []
    enc = payload[0]
    body = payload[1:]
    if enc == 1:
        text = body.decode("utf-16", errors="replace")
    elif enc == 2:
        text = body.decode("utf-16-be", errors="replace")
    elif enc == 3:
        text = body.decode("utf-8", errors="replace")
    else:
        text = body.decode("latin-1", errors="replace")
    return [part for part in text.replace("\x00", "\x01").split("\x01") if part != ""]


def _decode_desc(payload: bytes) -> tuple[str, bytes]:
    """Split an (encoding, description\\0, value) frame such as TXXX."""
    if not payload:
        return "", b""
    return _split_desc(payload[0], payload[1:])


def _split_desc(enc: int, body: bytes) -> tuple[str, bytes]:
    if enc in (1, 2):
        # UTF-16 frames: the terminator is two zero bytes on an even boundary
        for i in range(0, len(body) - 1, 2):
            if body[i] == 0 and body[i + 1] == 0:
                desc = body[:i].decode("utf-16", errors="replace")
                return desc, body[i + 2:]
        return "", body
    idx = body.find(b"\x00")
    if idx < 0:
        return "", body
    codec = "latin-1" if enc == 0 else "utf-8"
    return body[:idx].decode(codec, errors="replace"), body[idx + 1:]


def _text_payload(text: str) -> bytes:
    try:
        raw = text.encode("latin-1")
        if b"\x00" not in raw:
            return b"\x00" + raw
    except UnicodeEncodeError:
        pass
    return b"\x01" + b"\xff\xfe" + text.encode("utf-16-le")


def _descriptor_payload(language: bytes, label: str, text: str, latin1_ok: bool) -> bytes:
    if latin1_ok and text.isascii():
        return b"\x00" + language + label.encode("latin-1") + b"\x00" + text.encode("latin-1")
    return (
        b"\x01"
        + language
        + b"\xff\xfe"
        + label.encode("utf-16-le")
        + b"\x00\x00"
        + b"\xff\xfe"
        + text.encode("utf-16-le")
    )


def _comment_payload(text: str) -> bytes:
    return _descriptor_payload(b"eng", "Comment", text, True)


def _lyrics_payload(text: str) -> bytes:
    return _descriptor_payload(b"eng", "Lyrics", text, True)


def _find_mpeg_start(path: str, offset: int) -> int:
    with open(path, "rb") as fh:
        fh.seek(offset)
        data = fh.read(1 << 20)
    i = 0
    while i + 1 < len(data):
        if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
            return offset + i
        i += 1
    return offset


def read(path: str) -> tuple[dict, dict[str, bytes], int]:
    """Return (tags, extra_raw_frames, audio_offset)."""
    with open(path, "rb") as fh:
        head = fh.read(10)
        if head[:3] != b"ID3":
            return _read_v1(path), {}, 0
        version = head[3]
        flags = head[5]
        size = _syncsafe(head[6:10])
        body = fh.read(size)
    if flags & 0x80 and version < 4:
        body = _deunsync(body)
    tags: dict = {}
    extras: dict[str, bytes] = {}
    pos = 0
    txxx: dict[str, str] = {}
    id_len, sz_len = (3, 3) if version == 2 else (4, 4)
    while pos + id_len + sz_len <= len(body):
        frame_id = body[pos:pos + id_len].decode("latin-1", errors="replace")
        if not frame_id.strip("\x00"):
            break
        raw_size = body[pos + id_len:pos + id_len + sz_len]
        if version == 2:
            frame_size = int.from_bytes(raw_size, "big")
            hdr = 6
            fflags = 0
        elif version == 4:
            frame_size = _syncsafe(raw_size)
            hdr = 10
            fflags = int.from_bytes(body[pos + 8:pos + 10], "big")
        else:
            frame_size = int.from_bytes(raw_size, "big")
            hdr = 10
            fflags = int.from_bytes(body[pos + 8:pos + 10], "big")
        if frame_size <= 0 or pos + hdr + frame_size > len(body) + 16:
            # tolerate writers that incorrectly use syncsafe sizes in v2.3
            alt = _syncsafe(raw_size) if len(raw_size) == 4 else 0
            if 0 < alt and pos + hdr + alt <= len(body):
                frame_size = alt
            else:
                break
        payload = body[pos + hdr:pos + hdr + frame_size]
        pos += hdr + frame_size
        if version == 2:
            frame_id = V22.get(frame_id, frame_id)
        if not payload:
            continue
        if fflags & 0x00C0 if version == 3 else fflags & 0x000C:
            # compressed or encrypted frames cannot be parsed safely
            continue
        try:
            _apply_frame(frame_id, payload, tags, txxx, version)
        except (UnicodeDecodeError, struct.error, IndexError):
            continue
    if txxx:
        tags.setdefault("mb_album_id", txxx.get("MusicBrainz Album Id", ""))
        tags.setdefault("mb_track_id", txxx.get("MusicBrainz Release Track Id", ""))
    tags.pop("_present", None)
    extras = {fid: raw for fid, raw in tags.pop("_raw_extra", {}).items()
              if fid not in MANAGED and len(fid) == 4}
    audio_offset = 10 + size + (10 if (flags & 0x10) and version == 4 else 0)
    audio_offset = _find_mpeg_start(path, audio_offset)
    return tags, extras, audio_offset


def _apply_frame(frame_id: str, payload: bytes, tags: dict, txxx: dict, version: int) -> None:
    present = tags.setdefault("_present", set())
    raw_extra = tags.setdefault("_raw_extra", {})
    present.add(frame_id)

    def text_value() -> str:
        parts = _decode_text(payload)
        return "/".join(parts) if parts else ""

    if frame_id == "TXXX":
        enc = payload[0]
        desc, value = _decode_desc(payload)
        if enc == 0:
            val = value.decode("latin-1", errors="replace")
        elif enc == 3:
            val = value.decode("utf-8", errors="replace")
        else:
            val = value.decode("utf-16", errors="replace")
        txxx[desc] = val.split("\x00")[0]
        return
    if frame_id == "APIC":
        if len(payload) < 4:
            return
        enc = payload[0]
        rest = payload[1:]
        mime_end = rest.find(b"\x00")
        if mime_end < 0:
            return
        mime = rest[:mime_end].decode("latin-1", errors="replace")
        rest = rest[mime_end + 1:]
        if not rest:
            return
        rest = rest[1:]  # picture type
        if enc in (1, 2):
            idx = -1
            for i in range(0, len(rest) - 1, 2):
                if rest[i] == 0 and rest[i + 1] == 0:
                    idx = i
                    break
            data = rest[idx + 2:] if idx >= 0 else b""
        else:
            idx = rest.find(b"\x00")
            data = rest[idx + 1:] if idx >= 0 else rest
        if data:
            tags["cover"] = (mime or "image/jpeg", data)
        return
    if frame_id in ("COMM", "USLT"):
        enc = payload[0]
        # encoding byte + 3 byte language code, then description + value
        desc, value = _split_desc(enc, payload[4:] if len(payload) > 4 else b"")
        text = value.decode("utf-16", errors="replace") if enc in (1, 2) else value.decode(
            "latin-1" if enc == 0 else "utf-8", errors="replace"
        )
        tags["lyrics" if frame_id == "USLT" else "comment"] = text.split("\x00")[0]
        return
    if frame_id in ("TRCK", "TPOS"):
        raw = text_value()
        number = raw.split("/")[0].strip()
        key = "track_no" if frame_id == "TRCK" else "disc_no"
        tags[key] = _to_int(number)
        return
    if frame_id in ("TYER", "TDRC"):
        value = text_value()
        tags["year"] = _to_int(value[:4])
        return
    mapping = {
        "TIT2": "title",
        "TPE1": "artist",
        "TPE2": "albumartist",
        "TALB": "album",
        "TCON": "genre",
        "TCOM": "composer",
        "TEXT": "lyricist",
        "TSRC": "isrc",
        "TBPM": "bpm",
    }
    key = mapping.get(frame_id)
    if key is None:
        raw_extra[frame_id] = payload
        return
    value = text_value()
    if key == "genre":
        value = normalize_genre(value)
    if key == "bpm":
        tags[key] = _to_int(value)
    else:
        tags[key] = value


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    return int(digits) if digits else None


def normalize_genre(value: str) -> str:
    value = value.strip()
    if value.startswith("(") and ")" in value:
        number = value[1:value.index(")")]
        rest = value[value.index(")") + 1:].strip()
        if rest:
            return rest
        if number.isdigit() and 0 <= int(number) < len(ID3V1_GENRES):
            return ID3V1_GENRES[int(number)]
    if value.isdigit() and 0 <= int(value) < len(ID3V1_GENRES):
        return ID3V1_GENRES[int(value)]
    return value


def _read_v1(path: str) -> dict:
    try:
        with open(path, "rb") as fh:
            fh.seek(-128, 2)
            data = fh.read(128)
    except OSError:
        return {}
    if len(data) != 128 or data[:3] != b"TAG":
        return {}
    return {
        "title": data[3:33].split(b"\x00")[0].decode("latin-1", errors="replace").strip(),
        "artist": data[33:63].split(b"\x00")[0].decode("latin-1", errors="replace").strip(),
        "album": data[63:93].split(b"\x00")[0].decode("latin-1", errors="replace").strip(),
        "year": _to_int(data[93:97].decode("latin-1", errors="replace")),
        "comment": data[97:127].split(b"\x00")[0].decode("latin-1", errors="replace").strip(),
    }


def build(fields: dict, extras: dict[str, bytes] | None = None) -> bytes:
    frames: list[bytes] = []

    def add(frame_id: str, payload: bytes) -> None:
        frames.append(
            frame_id.encode("latin-1") + struct.pack(">I", len(payload)) + b"\x00\x00" + payload
        )

    text_map = [
        ("title", "TIT2"),
        ("artist", "TPE1"),
        ("albumartist", "TPE2"),
        ("album", "TALB"),
        ("genre", "TCON"),
        ("composer", "TCOM"),
        ("lyricist", "TEXT"),
        ("isrc", "TSRC"),
    ]
    for key, frame_id in text_map:
        value = str(fields.get(key) or "").strip()
        if value:
            add(frame_id, _text_payload(value))
    if fields.get("year"):
        add("TDRC", _text_payload(str(fields["year"])))
    if fields.get("track_no"):
        add("TRCK", _text_payload(str(fields["track_no"])))
    if fields.get("disc_no"):
        add("TPOS", _text_payload(str(fields["disc_no"])))
    if fields.get("bpm"):
        add("TBPM", _text_payload(str(fields["bpm"])))
    if fields.get("comment"):
        add("COMM", _comment_payload(str(fields["comment"])))
    if fields.get("lyrics"):
        add("USLT", _lyrics_payload(str(fields["lyrics"])))
    for key, desc in (("mb_album_id", "MusicBrainz Album Id"),
                      ("mb_track_id", "MusicBrainz Release Track Id")):
        value = str(fields.get(key) or "").strip()
        if value:
            add("TXXX", _text_payload(desc) + b"\x00" + value.encode("latin-1", errors="replace"))

    cover = fields.get("cover")
    if cover:
        mime, data = cover
        if data:
            add("APIC", b"\x00" + mime.encode("latin-1") + b"\x00\x03" + b"\x00" + data)

    for frame_id, payload in (extras or {}).items():
        if frame_id in MANAGED or len(frame_id) != 4 or not payload:
            continue
        add(frame_id, payload)

    body = b"".join(frames) + b"\x00" * 1024
    return b"ID3\x03\x00\x00" + _to_syncsafe(len(body)) + body
