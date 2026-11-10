"""Audio stream probing: duration, bitrate, sample rate, channel count."""

from __future__ import annotations

import os

MP3_BITRATES = {
    (1, 1): [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448],
    (1, 2): [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384],
    (1, 3): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
    (2, 1): [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256],
    (2, 2): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
    (2, 3): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
}

MP3_RATES = {1: [44100, 48000, 32000], 2: [22050, 24000, 16000], 25: [11025, 12000, 8000]}


def _mp3_frame_info(data: bytes, offset: int = 0):
    """Locate the first valid MPEG audio frame and return its properties."""
    limit = min(len(data) - 4, offset + (1 << 20))
    pos = offset
    while pos < limit:
        b1 = data[pos]
        b2 = data[pos + 1] if pos + 1 < len(data) else 0
        if b1 == 0xFF and (b2 & 0xE0) == 0xE0:
            b3 = data[pos + 2]
            b4 = data[pos + 3]
            version_bits = (b2 >> 3) & 0x03
            layer_bits = (b2 >> 1) & 0x03
            bitrate_index = (b3 >> 4) & 0x0F
            rate_index = (b3 >> 2) & 0x03
            padding = (b3 >> 1) & 0x01
            channel_mode = (b4 >> 6) & 0x03
            if version_bits == 1 or layer_bits == 0 or bitrate_index in (0, 15) or rate_index == 3:
                pos += 1
                continue
            version = {0: 25, 2: 2, 3: 1}[version_bits]
            layer = {1: 3, 2: 2, 3: 1}[layer_bits]
            table_key = (1 if version == 1 else 2, 3 if layer == 3 else (2 if layer == 2 else 1))
            bitrates = MP3_BITRATES.get(table_key)
            if not bitrates:
                pos += 1
                continue
            bitrate = bitrates[bitrate_index]
            sample_rate = MP3_RATES[version][rate_index]
            channels = 1 if channel_mode == 3 else 2
            samples_per_frame = 1152 if layer == 3 else (384 if layer == 1 else 1152)
            if version != 1 and layer == 3:
                samples_per_frame = 576
            return {
                "pos": pos,
                "version": version,
                "layer": layer,
                "bitrate": bitrate,
                "sample_rate": sample_rate,
                "channels": channels,
                "padding": padding,
                "samples_per_frame": samples_per_frame,
            }
        pos += 1
    return None


def _xing_frames(data: bytes, info: dict) -> int | None:
    mono = info["channels"] == 1
    if info["version"] == 1:
        side = 17 if mono else 32
    else:
        side = 9 if mono else 17
    offset = info["pos"] + 4 + side
    if offset + 8 > len(data):
        return None
    marker = data[offset:offset + 4]
    if marker not in (b"Xing", b"Info"):
        return None
    flags = int.from_bytes(data[offset + 4:offset + 8], "big")
    cursor = offset + 8
    if flags & 0x1:
        if cursor + 4 > len(data):
            return None
        return int.from_bytes(data[cursor:cursor + 4], "big")
    return None


def probe_mp3(path: str, audio_offset: int = 0) -> dict:
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        fh.seek(audio_offset)
        data = fh.read(1 << 20)
    info = _mp3_frame_info(data)
    if not info:
        return {"codec": "MP3", "duration": 0.0}
    frames = _xing_frames(data, info)
    bitrate = info["bitrate"]
    if frames:
        duration = frames * info["samples_per_frame"] / info["sample_rate"]
    else:
        audio_bytes = max(0, size - audio_offset)
        duration = audio_bytes * 8 / (bitrate * 1000) if bitrate else 0.0
    return {
        "codec": "MP3",
        "duration": round(duration, 3),
        "bitrate": bitrate,
        "sample_rate": info["sample_rate"],
        "channels": info["channels"],
        "bit_depth": 0,
    }
