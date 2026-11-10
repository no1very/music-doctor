"""Navidrome artwork maintenance CLI.

Single entry point for the three commands this project needs after touching
``ArtistArtPriority`` / ``ArtistImageFolder`` — see ``app/navidrome.py`` for the
why.  Works locally (``docker exec``) and against a Navidrome on another box
(``ssh``), which is the usual NAS setup.

Examples::

    python tools/navidrome.py status
    python tools/navidrome.py reprocess
    python tools/navidrome.py explain ar-1234567890abcdef
    python tools/navidrome.py explain 1234567890abcdef --json
    python tools/navidrome.py doctor          # 只在图片目录变化时重算
    python tools/navidrome.py hooks           # 只打印拼出来的命令，不执行
    python tools/navidrome.py status --ssh nas --container navidrome

Exit codes: 0 success, 1 Navidrome/传输失败, 2 配置错误。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:  # Windows 控制台可能不是 UTF-8，避免中文输出乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from app import navidrome  # noqa: E402


def _print_result(result: dict, as_json: bool) -> int:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if result.get("command"):
        print(f"$ {result['command']}")
    body = result.get("stdout") or ""
    if body:
        print(body)
    err = result.get("stderr") or ""
    if err:
        print(err, file=sys.stderr)
    if not result.get("ok"):
        print(f"[失败] code={result.get('code')} recipe={result.get('recipe') or '-'}", file=sys.stderr)
        return 1
    return 0


def _config_lines() -> list[str]:
    cfg = navidrome.settings()
    ok, why = navidrome.availability(cfg)
    container = cfg["container"] or navidrome.detect_container(cfg) or "<未检测到>"
    lines = [
        f"容器        : {container}",
        f"datafolder  : {cfg['data_folder']}",
        f"执行方式    : {'ssh ' + cfg['ssh'] if cfg['ssh'] else '本机 docker'}",
        f"自动重算    : {'开启' if cfg['auto'] else '关闭'}",
        f"可用性      : {'可用' if ok else why}",
    ]
    return lines


def cmd_status(args) -> int:
    return _print_result(navidrome.run("status", timeout=args.timeout, container=args.container,
                                       data_folder=args.datafolder, ssh=args.ssh, docker=args.docker),
                         args.json)


def cmd_reprocess(args) -> int:
    result = navidrome.run("reprocess", timeout=args.timeout, container=args.container,
                           data_folder=args.datafolder, ssh=args.ssh, docker=args.docker)
    if not args.json and result.get("ok"):
        print("\n已记录重算时间：之后 doctor 只在图片目录再次变化时重算。")
    return _print_result(result, args.json)


def cmd_explain(args) -> int:
    artist = navidrome.normalize_artist_id(args.artist)
    if not artist:
        print("explain 需要艺术家 ID（ar-<id> 或裸 id；ID 见 Navidrome 艺术家页 URL）", file=sys.stderr)
        return 2
    return _print_result(navidrome.run("explain", artist, timeout=args.timeout,
                                       container=args.container, data_folder=args.datafolder,
                                       ssh=args.ssh, docker=args.docker), args.json)


def cmd_doctor(args) -> int:
    if not args.json:
        for line in _config_lines():
            print(line)
        print("-" * 56)
    status_result = None
    code = 0
    if not args.skip_status:
        status_result = navidrome.run("status", timeout=args.timeout, container=args.container,
                                      data_folder=args.datafolder, ssh=args.ssh, docker=args.docker)
        code = 0 if status_result.get("ok") else 1
        if not args.json:
            print(status_result.get("stdout") or status_result.get("stderr") or "")
    else:
        ok, why = navidrome.availability()
        if not ok:
            print(why, file=sys.stderr)
            return 1
    need, why = navidrome.needs_reprocess()
    if not args.json:
        print(f"\n是否需要重算：{'是' if need else '否'} — {why}")
    report = {"config": navidrome.settings(), "status": status_result,
              "needs_reprocess": need, "needs_reprocess_reason": why, "reprocess": None}
    if need:
        if args.dry_run:
            built = navidrome.command("reprocess", container=args.container, data_folder=args.datafolder,
                                      ssh=args.ssh, docker=args.docker)
            if not args.json:
                print(f"[dry-run] 将执行：{built['command']}")
            report["reprocess"] = {"dry_run": True, "command": built["command"]}
        else:
            result = navidrome.run("reprocess", timeout=args.timeout, container=args.container,
                                   data_folder=args.datafolder, ssh=args.ssh, docker=args.docker)
            report["reprocess"] = result
            if not args.json:
                print(f"$ {result.get('command', '')}")
                print(result.get("stdout") or result.get("stderr") or "")
            if not result.get("ok"):
                code = 1
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


def cmd_hooks(args) -> int:
    """Print the exact commands for the current config without running them."""
    payload = {kind: navidrome.command(kind, "ar-<id>" if kind == "explain" else "",
                                       container=args.container, data_folder=args.datafolder,
                                       ssh=args.ssh, docker=args.docker)
               for kind in navidrome.ARTWORK_KINDS}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    for line in _config_lines():
        print(line)
    print("-" * 56)
    print("兼容你原来的写法（在宿主机直接执行）：")
    print("  docker exec navidrome /app/navidrome artwork reprocess --kind ar -y --datafolder /data")
    print("  docker exec navidrome /app/navidrome artwork explain ar-<艺术家ID> --datafolder /data")
    print("  docker exec navidrome /app/navidrome artwork status --datafolder /data")
    print("\n本项目当前配置会拼出：")
    for kind, built in payload.items():
        print(f"  {kind:<9} {built['command']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="navidrome.py",
        description="Navidrome artwork 缓存维护（改过 ArtistArtPriority/ArtistImageFolder 后重算）",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--container", help="Navidrome 容器名（默认自动检测）")
    common.add_argument("--datafolder", help="容器内 data 目录（默认 /data）")
    common.add_argument("--ssh", help="通过 ssh 执行，值为 user@host 或 ~/.ssh/config 别名")
    common.add_argument("--docker", help="docker 可执行文件（默认 docker）")
    common.add_argument("--timeout", type=int, help="超时秒数（默认 900）")
    common.add_argument("--json", action="store_true", help="输出 JSON（便于脚本消费）")

    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status", parents=[common], help="artwork status：总览有哪些艺术家缺图")
    p.set_defaults(func=cmd_status)
    p = sub.add_parser("reprocess", parents=[common], help="artwork reprocess：重算全部艺术家图片")
    p.set_defaults(func=cmd_reprocess)
    p = sub.add_parser("explain", parents=[common], help="artwork explain：查某位艺术家为什么有/没有图")
    p.add_argument("artist", help="ar-<艺术家ID> 或裸 ID")
    p.set_defaults(func=cmd_explain)
    p = sub.add_parser("doctor", parents=[common], help="status + 仅在需要时 reprocess")
    p.add_argument("--dry-run", action="store_true", help="只打印将要执行的命令")
    p.add_argument("--skip-status", action="store_true",
                   help="跳过 artwork status（cron 用，省一次 docker exec）")
    p.set_defaults(func=cmd_doctor)
    p = sub.add_parser("hooks", parents=[common], help="只打印当前配置会执行的命令")
    p.set_defaults(func=cmd_hooks)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"[配置错误] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
