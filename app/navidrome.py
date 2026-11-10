"""Navidrome artwork cache maintenance.

Navidrome caches *per artist* whether it has artwork.  Changing
``ArtistArtPriority`` / ``ArtistImageFolder`` (or exporting new portraits from
this project) therefore does not take effect by itself: the cached "no image"
answer survives restarts.  The cache has to be recomputed once:

    docker exec navidrome /app/navidrome artwork reprocess --kind ar -y --datafolder /data
    docker exec navidrome /app/navidrome artwork explain ar-<artist id> --datafolder /data
    docker exec navidrome /app/navidrome artwork status --datafolder /data

This module is the single place that knows those three commands.  Everything
else in the project — the CLI in ``tools/navidrome.py``, the export/scrape
hooks, the background task — calls into here, so the flags are never restated
(and never drift apart).

Commands can be run against a local Docker daemon or, when Navidrome lives on
another box (NAS), over SSH.  Configuration is environment-only, matching the
rest of the app:

    MTW_NAVIDROME_CONTAINER   container name; auto-detected when empty
    MTW_NAVIDROME_DATA_FOLDER path inside the container (default ``/data``)
    MTW_NAVIDROME_SSH         ``ssh`` target (user@host or an ~/.ssh/config alias)
    MTW_NAVIDROME_DOCKER      docker binary (default ``docker``)
    MTW_NAVIDROME_TIMEOUT     seconds before a call is abandoned (default 900)
    MTW_NAVIDROME_AUTOREPROCESS  ``0`` disables the post-export hook
    MTW_NAVIDROME_LOCAL_IMAGE_FOLDER  host path of the export folder, for host-side
                                      ``doctor`` runs when the app sees a different
                                      mount (e.g. ``/diskc/Music/artist``)
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import threading
import time

from . import db
from .config import config

NAV_BINARY = "/app/navidrome"
ARTWORK_KINDS = ("reprocess", "explain", "status")
STATE_FILE = "navidrome-artwork.json"

_GUARD = threading.Lock()
_ACTIVE = False
_CONTAINER_CACHE: dict[str, str] = {}


def settings() -> dict:
    """Current effective configuration (env overrides + defaults)."""
    try:
        timeout = int(os.environ.get("MTW_NAVIDROME_TIMEOUT", "900") or 900)
    except ValueError:
        timeout = 900
    auto_raw = os.environ.get("MTW_NAVIDROME_AUTOREPROCESS", "1")
    return {
        "container": os.environ.get("MTW_NAVIDROME_CONTAINER", "").strip(),
        "data_folder": os.environ.get("MTW_NAVIDROME_DATA_FOLDER", "").strip() or "/data",
        "ssh": os.environ.get("MTW_NAVIDROME_SSH", "").strip(),
        "docker": os.environ.get("MTW_NAVIDROME_DOCKER", "").strip() or "docker",
        "auto": auto_raw.strip().lower() not in {"0", "false", "no", "off"},
        "timeout": max(10, timeout),
    }


def normalize_artist_id(value: str) -> str:
    """``ar-<id>`` is what Navidrome wants; accept a bare id too."""
    artist = str(value or "").strip()
    if not artist:
        return ""
    if artist.startswith("ar-"):
        return artist
    return f"ar-{artist}"


def _ssh_prefix(ssh: str) -> list[str]:
    return ["ssh", *shlex.split(ssh)]


def _remote_escape(argv: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in argv)


def command(
    kind: str,
    artist: str = "",
    *,
    container: str | None = None,
    data_folder: str | None = None,
    ssh: str | None = None,
    docker: str | None = None,
) -> dict:
    """Build (but do not run) one artwork command.

    Returns ``{"argv", "command", "recipe", "container", "data_folder"}``.
    ``container`` may be empty here — :func:`run` resolves it lazily so a plain
    ``command("status")`` stays free of side effects.
    """
    if kind not in ARTWORK_KINDS:
        raise ValueError(f"unknown artwork command: {kind}")
    cfg = settings()
    container = (container if container is not None else cfg["container"]).strip()
    data_folder = (data_folder if data_folder is not None else cfg["data_folder"]).strip() or "/data"
    ssh = (ssh if ssh is not None else cfg["ssh"]).strip()
    docker = (docker if docker is not None else cfg["docker"]).strip() or "docker"

    inner: list[str] = [NAV_BINARY, "artwork", kind]
    if kind == "reprocess":
        inner += ["--kind", "ar", "-y"]
    elif kind == "explain":
        artist_id = normalize_artist_id(artist)
        if not artist_id:
            raise ValueError("explain 需要艺术家 ID（ar-<id> 或裸 id）")
        inner.append(artist_id)
    inner += ["--datafolder", data_folder]

    remote = [docker, "exec", container or "<navidrome>", *inner]
    recipe = "ssh" if ssh else "local"
    if ssh:
        argv = [*_ssh_prefix(ssh), _remote_escape(remote)]
        display = " ".join(argv)
    else:
        argv = remote
        display = " ".join(remote)  # display only; argv is passed without a shell
    return {
        "argv": argv,
        "command": display,
        "recipe": recipe,
        "container": container,
        "data_folder": data_folder,
    }


def _run_process(argv: list[str], timeout: int) -> dict:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "ok": proc.returncode == 0,
            "code": proc.returncode,
            "stdout": (proc.stdout or "").strip(),
            "stderr": (proc.stderr or "").strip(),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": -1, "stdout": "", "stderr": "命令执行超时", "timed_out": True}
    except OSError as exc:
        return {"ok": False, "code": -2, "stdout": "", "stderr": f"无法执行：{exc}", "timed_out": False}


def availability(cfg: dict | None = None) -> tuple[bool, str]:
    """Whether the configured transport exists on this machine at all."""
    cfg = cfg or settings()
    ssh = cfg["ssh"]
    if ssh:
        if shutil.which("ssh"):
            return True, ""
        return False, "本机找不到 ssh 客户端：请装 OpenSSH 或去掉 MTW_NAVIDROME_SSH"
    if shutil.which(cfg["docker"]):
        return True, ""
    return (
        False,
        "本机找不到 docker 命令（容器内运行本项目时属于正常情况）："
        "请在宿主机或 NAS 上执行 python tools/navidrome.py reprocess",
    )


def detect_container(cfg: dict | None = None, refresh: bool = False) -> str:
    """Find the Navidrome container: exact name first, then image/name match."""
    cfg = cfg or settings()
    if cfg["container"]:
        return cfg["container"]
    key = f"{cfg['ssh']}|{cfg['docker']}"
    if not refresh and key in _CONTAINER_CACHE:
        return _CONTAINER_CACHE[key]
    ok, _ = availability(cfg)
    if not ok:
        return ""
    ps_argv = [cfg["docker"], "ps", "--format", "{{.Names}}\t{{.Image}}"]
    argv = [*_ssh_prefix(cfg["ssh"]), _remote_escape(ps_argv)] if cfg["ssh"] else ps_argv
    result = _run_process(argv, 30)
    if not result["ok"]:
        return ""
    names: list[tuple[str, str]] = []
    for line in result["stdout"].splitlines():
        parts = line.split("\t")
        name = parts[0].strip()
        image = parts[1].strip() if len(parts) > 1 else ""
        if name:
            names.append((name, image))
    chosen = ""
    for name, _image in names:
        if name == "navidrome":
            chosen = name
            break
    if not chosen:
        for name, image in names:
            if "navidrome" in image.lower() or "navidrome" in name.lower():
                chosen = name
                break
    if chosen:
        _CONTAINER_CACHE[key] = chosen
    return chosen


def run(kind: str, artist: str = "", *, timeout: int | None = None, **overrides) -> dict:
    """Run one artwork command and return a JSON-safe result."""
    cfg = settings()
    ok, why = availability(cfg)
    if not ok:
        return {
            "ok": False, "code": -3, "command": "", "recipe": "", "stdout": "", "stderr": why,
            "container": cfg["container"], "data_folder": cfg["data_folder"], "timed_out": False,
        }
    built = command(kind, artist, **overrides)
    if not built["container"]:
        container = detect_container(cfg, refresh=True)
        if not container:
            return {
                "ok": False, "code": -4, "command": built["command"], "recipe": built["recipe"],
                "stdout": "", "stderr": "找不到 navidrome 容器：用 MTW_NAVIDROME_CONTAINER 指定容器名",
                "container": "", "data_folder": built["data_folder"], "timed_out": False,
            }
        overrides = {**overrides, "container": container}
        built = command(kind, artist, **overrides)
    result = _run_process(built["argv"], timeout or cfg["timeout"])
    result.update(
        {
            "command": built["command"],
            "recipe": built["recipe"],
            "container": built["container"],
            "data_folder": built["data_folder"],
        }
    )
    if result["ok"] and kind == "reprocess":
        _remember_reprocess(built)
    return result


def reprocess(**kwargs) -> dict:
    return run("reprocess", **kwargs)


def status(**kwargs) -> dict:
    return run("status", **kwargs)


def explain(artist: str, **kwargs) -> dict:
    return run("explain", artist, **kwargs)


# --------------------------------------------------------------------------- #
# "should we recompute?" bookkeeping
# --------------------------------------------------------------------------- #
def _state() -> dict:
    path = config.data_dir / STATE_FILE
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _remember_reprocess(built: dict) -> None:
    state = _state()
    state.update(
        {
            "last_reprocess_at": int(time.time()),
            "last_reprocess_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "container": built.get("container", ""),
            "data_folder": built.get("data_folder", ""),
            "image_signature": image_signature() or state.get("image_signature"),
        }
    )
    try:
        (config.data_dir / STATE_FILE).write_text(
            json.dumps(state, ensure_ascii=False, indent=2), "utf-8"
        )
    except OSError:
        pass


def image_signature(folder: str | None = None) -> str:
    """Cheap fingerprint of the export folder: count + newest mtime.

    Empty when the folder is not readable from this machine (the usual case
    inside the app container, where only Navidrome's mount is visible).

    ``MTW_NAVIDROME_LOCAL_IMAGE_FOLDER`` maps the *host* path of the same folder
    (e.g. ``/diskc/Music/artist`` for a container-side ``/music/artist``), which
    is what a host-side ``doctor`` run needs to see changes written by the app.
    """
    if folder is None:
        folder = os.environ.get("MTW_NAVIDROME_LOCAL_IMAGE_FOLDER", "").strip()
        if not folder:
            try:
                folder = db.get_setting("artist.image_folder", "") or ""
            except Exception:  # noqa: BLE001 - CLI use before the database exists
                folder = ""
    folder = str(folder).strip()
    if not folder or not os.path.isdir(folder):
        return ""
    count = 0
    newest = 0.0
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if not entry.is_file():
                    continue
                count += 1
                try:
                    newest = max(newest, entry.stat().st_mtime)
                except OSError:
                    continue
    except OSError:
        return ""
    return f"{count}:{int(newest)}"


def needs_reprocess() -> tuple[bool, str]:
    """True when the export folder changed after the last successful recompute."""
    state = _state()
    signature = image_signature()
    if not signature:
        return (False, "读不到艺术家图片目录，无法判断是否需要重算（请用 status/explain 手动确认）")
    if not state.get("image_signature"):
        return (True, "还没有重算记录")
    if state["image_signature"] != signature:
        return (True, f"图片目录已变化（{state['image_signature']} → {signature}）")
    return (False, "图片目录自上次重算后未变化")


# --------------------------------------------------------------------------- #
# post-export hook (called from the API and the batch scrape task)
# --------------------------------------------------------------------------- #
def _busy() -> bool:
    with _GUARD:
        return _ACTIVE


def _claim() -> bool:
    """Reserve the single reprocess slot (recompute is idempotent, cheap to merge)."""
    global _ACTIVE
    with _GUARD:
        if _ACTIVE:
            return False
        _ACTIVE = True
        return True


def _release() -> None:
    global _ACTIVE
    with _GUARD:
        _ACTIVE = False


def after_export(exported: int, user_id: int | None = None, reason: str = "") -> dict:
    """Auto-recompute artwork after new portrait files were written.

    Never raises: an unreachable Docker/SSH setup must not fail the export that
    triggered it.  Returns a JSON-safe summary for the API/UI, e.g.
    ``{"enabled": true, "started": true, "task_id": 12, "container": "navidrome"}``.
    """
    cfg = settings()
    summary: dict = {
        "enabled": cfg["auto"],
        "started": False,
        "exported": int(exported or 0),
        "container": cfg["container"],
        "data_folder": cfg["data_folder"],
        "recipe": "ssh" if cfg["ssh"] else "local",
        "reason": "",
    }
    if summary["exported"] <= 0:
        summary["reason"] = "没有写入新图片，跳过重算"
        return summary
    if not cfg["auto"]:
        summary["reason"] = "MTW_NAVIDROME_AUTOREPROCESS=0，已关闭自动重算"
        return summary
    ok, why = availability(cfg)
    if not ok:
        summary["reason"] = why
        return summary
    summary["container"] = detect_container(cfg) or cfg["container"]
    if not summary["container"]:
        summary["reason"] = "找不到 navidrome 容器：用 MTW_NAVIDROME_CONTAINER 指定容器名"
        return summary
    if not _claim():
        summary["reason"] = "已有重算任务在进行，本次合并"
        return summary
    try:
        from .tasks import tasks  # local import: tasks imports db/config, avoids cycles
    except Exception as exc:  # noqa: BLE001
        _release()
        summary["reason"] = f"后台任务不可用：{exc}"
        return summary
    title = "Navidrome 图像缓存重算"
    try:
        task_id = tasks.start(
            "navidrome-artwork",
            title,
            {"exported": summary["exported"], "reason": reason},
            lambda ctx: _task_reprocess(ctx, cfg),
            user_id,
        )
    except Exception as exc:  # noqa: BLE001
        _release()
        summary["reason"] = f"无法创建任务：{exc}"
        return summary
    summary["started"] = True
    summary["task_id"] = task_id
    summary["reason"] = "已自动触发重算"
    return summary


def _task_reprocess(ctx, cfg: dict) -> dict:
    try:
        ctx.log("artwork reprocess: 开始（Navidrome 对每位艺术家缓存过有无图片，改过配置必须重算）")
        result = run("reprocess", timeout=cfg["timeout"])
        ctx.log(f"$ {result.get('command', '')}")
        for line in (result.get("stdout") or "").splitlines()[-20:]:
            ctx.log(line)
        if result.get("stderr"):
            ctx.log(result["stderr"][-2000:], "warn" if not result.get("ok") else "info")
        ctx.log("重算完成" if result.get("ok") else f"重算失败（code {result.get('code')}）",
                "info" if result.get("ok") else "error")
        return {"ok": bool(result.get("ok")), "code": result.get("code"),
                "container": result.get("container"), "command": result.get("command")}
    finally:
        _release()


__all__ = [
    "NAV_BINARY",
    "after_export",
    "availability",
    "command",
    "detect_container",
    "explain",
    "image_signature",
    "needs_reprocess",
    "normalize_artist_id",
    "reprocess",
    "run",
    "settings",
    "status",
]
