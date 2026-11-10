#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Navidrome 头像/简介桥接服务。

一个容器里干四件事，全部可通过环境变量开关：

  1) unstick   把「已经有图却卡在重试队列」的艺术家从 artwork_queue 摘掉
  2) reprocess 让 navidrome 重算缺失头像（artwork reprocess --source absent --kind ar）
  3) image     按 Navidrome 的艺术家名去网易云补头像，写成 <艺术家名>.<ext>
  4) bio       按名字去百度百科 / Last.fm 补 artist.biography（只填空行）

对 docker 的调用走 Engine API（unix socket），镜像里不需要 docker CLI。

用法：
  bridge.py once / loop / status / unstick / reprocess / backfill-images / backfill-bio
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import socket
import sqlite3
import sys
import time
import urllib.parse
import urllib.request

POSTER_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MIN_BIO_LEN = 60
SEPARATORS = (";", "&", "、", "/", "•", "·")

UA = {"User-Agent": "open-music-tag-navidrome-bridge/1.0"}
NETEASE_HEADERS = {
    "Referer": "https://music.163.com/",
    "Origin": "https://music.163.com",
    "User-Agent": UA["User-Agent"],
}
BAIKE_HEADERS = {
    "Referer": "https://baike.baidu.com/",
    "User-Agent": ("Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
}

DEFAULT_LASTFM_KEY = "a42bb5a8fe6769ca88309c6806cbb2e1"


def env_flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def log(message: str) -> None:
    print(time.strftime("[%Y-%m-%d %H:%M:%S] ") + message, flush=True)


class Settings:
    def __init__(self) -> None:
        self.container = os.environ.get("NAV_CONTAINER", "navidrome")
        self.artist_dir = os.environ.get("ARTIST_DIR", "/music/artist")
        self.db_path = os.environ.get("NAV_DB", "/data/navidrome.db")
        self.datafolder = os.environ.get("NAV_DATAFOLDER", "/data")
        self.docker_socket = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
        self.interval = int(os.environ.get("INTERVAL", "120"))
        self.enable_unstick = env_flag("ENABLE_UNSTICK", "1")
        self.enable_reprocess = env_flag("ENABLE_REPROCESS", "1")
        self.enable_image = env_flag("ENABLE_IMAGE", "0")
        self.enable_bio = env_flag("ENABLE_BIO", "0")
        self.lastfm_key = os.environ.get("LASTFM_APIKEY", DEFAULT_LASTFM_KEY)
        self.netease_delay = float(os.environ.get("NETEASE_DELAY", "1.0"))
        self.bio_delay = float(os.environ.get("BIO_DELAY", "0.6"))


# --------------------------------------------------------------------------- #
# Docker Engine API（unix socket，无 CLI 依赖）
# --------------------------------------------------------------------------- #
class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: int = 300) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.socket_path)


class Docker:
    def __init__(self) -> None:
        self.socket_path = Settings().docker_socket

    def call(self, method: str, path: str, body: dict | None = None,
             timeout: int = 300) -> tuple[int, bytes]:
        conn = _UnixHTTPConnection(self.socket_path, timeout=timeout)
        headers = {"Content-Type": "application/json"} if body else {}
        payload = json.dumps(body).encode() if body else None
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        data = response.read()
        status = response.status
        conn.close()
        return status, data

    def exec(self, command: list[str], timeout: int = 300) -> tuple[int, str]:
        settings = Settings()
        status, data = self.call("POST", "/containers/%s/exec" % settings.container, {
            "Cmd": command, "AttachStdout": True, "AttachStderr": True, "Tty": False,
        })
        if status != 201:
            raise RuntimeError("exec create failed: %s %s" % (status, data[:200]))
        exec_id = json.loads(data)["Id"]
        _, raw = self.call("POST", "/exec/%s/start" % exec_id,
                           {"Detach": False, "Tty": False}, timeout=timeout)
        _, inspect_raw = self.call("GET", "/exec/%s/json" % exec_id)
        exit_code = json.loads(inspect_raw).get("ExitCode", -1)
        text = raw.decode("utf-8", "replace")
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        return exit_code, text

    def stop(self) -> None:
        self.call("POST", "/containers/%s/stop?t=30" % Settings().container, timeout=90)

    def start(self) -> None:
        self.call("POST", "/containers/%s/start" % Settings().container, timeout=90)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def http_json(url: str, headers: dict, timeout: int = 12) -> dict:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def http_bytes(url: str, headers: dict, timeout: int = 20) -> bytes:
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------- #
# Navidrome 库
# --------------------------------------------------------------------------- #
def connect(readonly: bool = True) -> sqlite3.Connection:
    path = Settings().db_path
    if readonly:
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
    else:
        con = sqlite3.connect(path, timeout=30)
    con.execute("PRAGMA busy_timeout = 15000")
    return con


def poster_for(name: str) -> str | None:
    directory = Settings().artist_dir
    for ext in POSTER_EXTS:
        path = os.path.join(directory, name + ext)
        if os.path.exists(path):
            return path
    return None


def missing_poster_names() -> list[str]:
    con = connect()
    rows = con.execute(
        "select a.name from artist a join item_artwork x"
        " on x.item_kind='ar' and x.item_id=a.id"
        " where coalesce(x.hash,'')='' and a.name <> '' order by a.name"
    ).fetchall()
    con.close()
    return [name for (name,) in rows if not poster_for(name)]


def empty_bio_names() -> list[str]:
    con = connect()
    rows = con.execute(
        "select name from artist where coalesce(biography,'')='' and name <> '' order by name"
    ).fetchall()
    con.close()
    return [name for (name,) in rows]


def write_bios(entries: dict) -> int:
    """写 biography 前先停容器，避免它用内存里的旧值覆盖。"""
    if not entries:
        return 0
    Docker().stop()
    time.sleep(1)
    written = 0
    con = connect(readonly=False)
    try:
        con.execute("BEGIN IMMEDIATE")
        for name, bio in entries.items():
            bio = (bio or "").strip()
            if not bio:
                continue
            row = con.execute(
                "select coalesce(biography,'') from artist where name=?", (name,)
            ).fetchone()
            if not row or row[0]:
                continue
            con.execute(
                "update artist set biography=? where name=? and coalesce(biography,'')=''",
                (bio, name),
            )
            written += 1
        con.commit()
    finally:
        con.close()
    Docker().start()
    time.sleep(6)
    return written


# --------------------------------------------------------------------------- #
# 动作
# --------------------------------------------------------------------------- #
def unstick() -> int:
    """有图却在重试队列里的艺术家 -> 删掉队列行。"""
    con = connect(readonly=False)
    artists = con.execute("select id, name from artist where name <> ''").fetchall()
    with_file = [aid for aid, name in artists if poster_for(name)]
    if not with_file:
        con.close()
        return 0
    stuck: list[str] = []
    for start in range(0, len(with_file), 200):
        chunk = with_file[start:start + 200]
        placeholders = ",".join("?" for _ in chunk)
        rows = con.execute(
            "select item_id from artwork_queue where item_kind='ar' and item_id in (%s)"
            % placeholders, tuple(chunk),
        ).fetchall()
        stuck.extend(row[0] for row in rows)
    if not stuck:
        con.close()
        return 0
    for _ in range(5):
        try:
            con.execute("BEGIN IMMEDIATE")
            for item_id in stuck:
                con.execute("delete from artwork_queue where item_kind='ar' and item_id=?",
                            (item_id,))
            con.commit()
            con.close()
            return len(stuck)
        except sqlite3.OperationalError:
            con.rollback()
            time.sleep(2)
    con.close()
    return 0


def reprocess() -> bool:
    settings = Settings()
    try:
        code, output = Docker().exec(
            ["/app/navidrome", "artwork", "reprocess", "--source", "absent",
             "--kind", "ar", "-y", "--datafolder", settings.datafolder], timeout=300)
    except Exception as exc:  # noqa: BLE001 - docker socket 不可用时只记录
        log("reprocess 失败：%s" % exc)
        return False
    tail = [line for line in output.strip().splitlines() if line.strip()][-3:]
    log("reprocess(exit=%d): %s" % (code, " | ".join(tail) if tail else "no output"))
    return code == 0


# --------------------------------------------------------------------------- #
# 补头像（网易云）
# --------------------------------------------------------------------------- #
def sniff_ext(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    return ".jpg"


def netease_poster(name: str) -> str:
    url = "https://music.163.com/api/search/get?" + urllib.parse.urlencode(
        {"s": name, "type": 100, "limit": 5, "offset": 0})
    payload = http_json(url, NETEASE_HEADERS)
    key = name.strip().casefold()
    best = None
    for item in ((payload.get("result") or {}).get("artists") or [])[:5]:
        candidate = (item.get("name") or "").strip()
        if not candidate:
            continue
        candidate_key = candidate.casefold()
        if candidate_key == key:
            rank = 3
        elif key in candidate_key or candidate_key in key:
            rank = 2
        elif any(str(alias).strip().casefold() in (candidate_key, key)
                 for alias in (item.get("alias") or []) if alias):
            rank = 1
        else:
            continue
        if best is None or rank > best[0]:
            best = (rank, item)
    if not best:
        return ""
    item = best[1]
    image = item.get("picUrl") or item.get("img1v1Url") or ""
    if item.get("id"):
        try:
            detail = http_json("https://music.163.com/api/artist/%s" % item["id"],
                               NETEASE_HEADERS).get("artist") or {}
            image = detail.get("picUrl") or detail.get("img1v1Url") or image
        except Exception:  # noqa: BLE001 - 详情只是补充
            pass
    return image


def backfill_images(limit: int = 0) -> dict:
    settings = Settings()
    names = missing_poster_names()
    if limit:
        names = names[:limit]
    ok, missed, failed = [], [], []
    log("缺图艺术家 %d 位" % len(names))
    for index, name in enumerate(names, start=1):
        if "/" in name:
            failed.append(name)
            log("[%d/%d] x %s 名字含 /，无法作为文件名" % (index, len(names), name))
            continue
        try:
            image = netease_poster(name)
            if not image:
                missed.append(name)
                continue
            data = http_bytes(image, NETEASE_HEADERS)
            if not data or len(data) > MAX_IMAGE_BYTES:
                failed.append(name)
                continue
            target = os.path.join(settings.artist_dir, name + sniff_ext(data))
            temporary = target + ".omt-tmp"
            with open(temporary, "wb") as handle:
                handle.write(data)
            os.replace(temporary, target)
            ok.append(name)
            log("[%d/%d] + %s (%dKB)" % (index, len(names), name, len(data) // 1024))
        except Exception as exc:  # noqa: BLE001 - 单个失败不影响整体
            failed.append(name)
            log("[%d/%d] x %s %s" % (index, len(names), name, exc))
        time.sleep(settings.netease_delay)
    log("补图完成：写入 %d / 源里没有 %d / 失败 %d" % (len(ok), len(missed), len(failed)))
    return {"written": len(ok), "missing": len(missed), "failed": len(failed)}


# --------------------------------------------------------------------------- #
# 补简介（百度百科 -> Last.fm 兜底）
# --------------------------------------------------------------------------- #
def lastfm_bio(name: str, key: str) -> tuple[str, str]:
    url = "https://ws.audioscrobbler.com/2.0/?" + urllib.parse.urlencode({
        "method": "artist.getinfo", "artist": name, "api_key": key,
        "format": "json", "lang": "zh",
    })
    data = http_json(url, UA)
    artist = data.get("artist") or {}
    matched = (artist.get("name") or "").strip()
    bio = strip_tags((artist.get("bio") or {}).get("summary") or "")
    bio = re.sub(r"(Read more( about [^.]*?)? on Last\.fm\.?)$", "", bio, flags=re.I).strip()
    return matched, bio


def baike_bio(name: str) -> tuple[str, str]:
    url = "https://baike.baidu.com/api/openapi/BaikeLemmaCardApi?" + urllib.parse.urlencode({
        "scope": 103, "format": "json", "appid": 379020, "bk_key": name, "bk_length": 800,
    })
    data = http_json(url, BAIKE_HEADERS)
    return (strip_tags(str(data.get("key") or data.get("word") or "")),
            strip_tags(data.get("abstract") or ""))


def name_ok(a: str, b: str) -> bool:
    a, b = a.strip().casefold(), b.strip().casefold()
    return a == b or a in b or b in a


def backfill_bios(limit: int = 0) -> dict:
    settings = Settings()
    names = empty_bio_names()
    if limit:
        names = names[:limit]
    con = connect()
    total_before = con.execute(
        "select count(*) from artist where coalesce(biography,'')<>''").fetchone()[0]
    con.close()

    found: dict[str, str] = {}
    for index, name in enumerate(names, start=1):
        if any(sep in name for sep in SEPARATORS):
            continue
        try:
            key, bio = baike_bio(name)
            if key and not name_ok(name, key):
                bio = ""
            if len(bio) < MIN_BIO_LEN:
                matched, bio = lastfm_bio(name, settings.lastfm_key)
                if not matched or not name_ok(name, matched):
                    bio = ""
            if len(bio) >= MIN_BIO_LEN:
                found[name] = bio
                log("[%d/%d] + %s (%d字)" % (index, len(names), name, len(bio)))
        except Exception as exc:  # noqa: BLE001
            log("[%d/%d] x %s %s" % (index, len(names), name, exc))
        time.sleep(settings.bio_delay)

    written = write_bios(found)
    con = connect()
    total_after = con.execute(
        "select count(*) from artist where coalesce(biography,'')<>''").fetchone()[0]
    con.close()
    log("补简介完成：采集 %d 位，写入 %d 位（%d -> %d）"
        % (len(found), written, total_before, total_after))
    return {"collected": len(found), "written": written}


# --------------------------------------------------------------------------- #
# 状态
# --------------------------------------------------------------------------- #
def status() -> None:
    settings = Settings()
    con = connect()
    total = con.execute("select count(*) from artist").fetchone()[0]
    with_art = con.execute(
        "select count(*) from item_artwork where item_kind='ar' and coalesce(hash,'')<>''"
    ).fetchone()[0]
    with_bio = con.execute(
        "select count(*) from artist where coalesce(biography,'')<>''").fetchone()[0]
    queued = con.execute(
        "select count(*) from artwork_queue where item_kind='ar'").fetchone()[0]
    con.close()
    files = [f for f in os.listdir(settings.artist_dir)
             if os.path.isfile(os.path.join(settings.artist_dir, f))]
    log("艺术家 %d 位｜有图 %d｜有简介 %d｜队列中 %d｜目录文件 %d"
        % (total, with_art, with_bio, queued, len(files)))


def run_once() -> None:
    settings = Settings()
    status()
    if settings.enable_unstick:
        log("摘掉卡死队列行：%d 条" % unstick())
    if settings.enable_image:
        backfill_images()
    if settings.enable_bio:
        backfill_bios()
    if settings.enable_reprocess:
        reprocess()
        status()


def loop() -> None:
    settings = Settings()
    log("常驻启动：间隔 %d 秒" % settings.interval)
    while True:
        try:
            run_once()
        except Exception as exc:  # noqa: BLE001 - 守护进程不能因为一次异常退出
            log("! 本轮出错：%s: %s" % (type(exc).__name__, exc))
        time.sleep(settings.interval)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Navidrome 头像/简介桥接")
    parser.add_argument("command", nargs="?", default="loop",
                        choices=["loop", "once", "status", "unstick", "reprocess",
                                 "backfill-images", "backfill-bio"])
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)

    if args.command == "loop":
        loop()
    elif args.command == "once":
        run_once()
    elif args.command == "status":
        status()
    elif args.command == "unstick":
        log("摘掉卡死队列行：%d 条" % unstick())
    elif args.command == "reprocess":
        reprocess()
    elif args.command == "backfill-images":
        backfill_images(args.limit)
    elif args.command == "backfill-bio":
        backfill_bios(args.limit)
    else:
        parser.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
