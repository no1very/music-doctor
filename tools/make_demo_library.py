"""Create a small demo music library for trying the app.

    python tools/make_demo_library.py demo-music

Uses ffmpeg (if available) to synthesize short tones, otherwise writes valid
silent frames. Tags are written through the app's own tag engine, so the demo
also exercises the writer.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import tags  # noqa: E402

TRACKS = [
    ("刘德华/忘情水", "01. 冰雨.mp3", {
        "title": "冰雨", "artist": "刘德华", "albumartist": "刘德华", "album": "忘情水",
        "track_no": 1, "year": 1997, "genre": "Pop", "composer": "陈耀川",
        "lyrics": "冷冷的冰雨在脸上胡乱的拍\n暖暖的眼泪跟寒雨混成一块",
    }, 440),
    ("刘德华/忘情水", "02. 忘情水.mp3", {
        "title": "忘情水", "artist": "刘德华", "albumartist": "刘德华", "album": "忘情水",
        "track_no": 2, "year": 1997, "genre": "Pop",
    }, 480),
    ("刘德华/忘情水", "03. 缠绵.flac", {
        "title": "缠绵", "artist": "刘德华", "albumartist": "刘德华", "album": "忘情水",
        "track_no": 3, "year": 1997, "genre": "Pop",
    }, 520),
    ("Radiohead/OK Computer", "01. Airbag.flac", {
        "title": "Airbag", "artist": "Radiohead", "albumartist": "Radiohead",
        "album": "OK Computer", "track_no": 1, "year": 1997, "genre": "Alternative Rock",
    }, 560),
    ("Radiohead/OK Computer", "02. Paranoid Android.flac", {
        "title": "Paranoid Android", "artist": "Radiohead", "albumartist": "Radiohead",
        "album": "OK Computer", "track_no": 2, "year": 1997, "genre": "Alternative Rock",
    }, 600),
    ("Nujabes/Modal Soul", "01. Feather.mp3", {
        "title": "Feather", "artist": "Nujabes", "albumartist": "Nujabes",
        "album": "Modal Soul", "track_no": 1, "year": 2005, "genre": "Jazz Hip-Hop",
    }, 660),
    ("_Unsorted", "Unknown Artist - Untagged Song.mp3", {}, 330),
]


def ffmpeg_path() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # optional convenience on some setups

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


def silent_mp3(path: str, seconds: float = 3.0) -> None:
    header = bytes([0xFF, 0xFB, 0x90, 0x00])
    frame = header + b"\x00" * (417 - len(header))
    frames = max(4, int(seconds * 44100 / 1152))
    with open(path, "wb") as fh:
        fh.write(frame * frames)


def silent_flac(path: str, seconds: float = 3.0) -> None:
    samples = int(44100 * seconds)
    rate, channels, bits = 44100, 2, 16
    packed = (rate << 44) | ((channels - 1) << 41) | ((bits - 1) << 36) | samples
    body = struct.pack(">HH", 4096, 4096) + b"\x00" * 6 + packed.to_bytes(8, "big") + b"\x00" * 16
    with open(path, "wb") as fh:
        fh.write(b"fLaC" + bytes([0x80]) + len(body).to_bytes(3, "big") + body)
        fh.write(b"\xff\xf8" + b"\x00" * (samples * channels * 2 // 8))


def render(path: str, flac: bool, freq: int, seconds: float = 3.0) -> None:
    exe = ffmpeg_path()
    if not exe:
        silent_flac(path, seconds) if flac else silent_mp3(path, seconds)
        return
    args = [
        exe, "-y", "-loglevel", "error", "-f", "lavfi",
        "-i", f"sine=frequency={freq}:duration={seconds}",
    ]
    if not flac:
        args += ["-id3v2_version", "3", "-codec:a", "libmp3lame", "-b:a", "192k"]
    args.append(path)
    result = subprocess.run(args, capture_output=True)
    if result.returncode != 0:
        silent_flac(path, seconds) if flac else silent_mp3(path, seconds)


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "demo-music"
    target = os.path.abspath(target)
    os.makedirs(target, exist_ok=True)
    for folder, filename, fields, freq in TRACKS:
        directory = os.path.join(target, folder)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, filename)
        is_flac = filename.lower().endswith(".flac")
        render(path, is_flac, freq)
        if fields:
            tags.write(path, fields)
            print(f"  tagged  {os.path.relpath(path, target)}")
        else:
            print(f"  created {os.path.relpath(path, target)} (untagged on purpose)")
    print(f"\ndemo library ready: {target}")
    print("add it as a library from the web UI, or run:")
    print(f'  python tools/e2e.py --base http://127.0.0.1:8318 --library "{target}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
