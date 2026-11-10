"""``${expression}`` template rendering, matching the documented V2 syntax.

Only single expressions are supported (no Mako blocks). Expressions are Python
syntax but evaluated against a whitelist: literals, comparisons, conditionals,
``or``/``and``, string methods like ``zfill``/``strip``/``replace``/``split``.
"""

from __future__ import annotations

import ast
import re

TEMPLATE_RE = re.compile(r"\$\{([^{}]*)\}")

SAFE_CALLS = {"str", "int", "len", "round", "abs", "bool"}
SAFE_METHODS = {
    "zfill", "strip", "lstrip", "rstrip", "lower", "upper", "title", "capitalize",
    "replace", "split", "rsplit", "join", "startswith", "endswith", "format",
}
ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
    ast.Call, ast.Constant, ast.Name, ast.Attribute, ast.Subscript, ast.List,
    ast.Tuple, ast.Dict, ast.Load, ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Eq, ast.NotEq, ast.Lt,
    ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot, ast.Slice,
    ast.JoinedStr, ast.FormattedValue,
)
FORBIDDEN_NAMES = {"__import__", "eval", "exec", "open", "compile", "globals", "locals",
                   "vars", "getattr", "setattr", "delattr", "input", "breakpoint"}


class TemplateError(ValueError):
    pass


def _validate(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise TemplateError(f"unsupported expression element: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            raise TemplateError(f"forbidden name: {node.id}")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("_") or node.attr not in SAFE_METHODS:
                raise TemplateError(f"forbidden attribute: {node.attr}")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id not in SAFE_CALLS:
                    raise TemplateError(f"forbidden function: {func.id}")
            elif isinstance(func, ast.Attribute):
                if func.attr not in SAFE_METHODS:
                    raise TemplateError(f"forbidden method: {func.attr}")
            else:
                raise TemplateError("only simple calls are allowed")


def _eval(expression: str, context: dict):
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise TemplateError(f"invalid expression: {exc.msg}") from exc
    _validate(tree)
    try:
        return eval(compile(tree, "<template>", "eval"), {"__builtins__": {}}, context)  # noqa: S307
    except Exception as exc:  # noqa: BLE001 - user template must degrade gracefully
        raise TemplateError(str(exc)) from exc


def render(template: str, context: dict) -> str:
    if not template:
        return ""
    ctx = dict(context)
    ctx.setdefault("null", "")

    def replace(match: re.Match) -> str:
        expression = match.group(1).strip()
        try:
            value = _eval(expression, ctx)
        except TemplateError:
            name = expression
            if name in ctx:
                value = ctx[name]
            else:
                return ""
        if value is None:
            return ""
        return str(value)

    return TEMPLATE_RE.sub(replace, template).strip()


def context_for(track: dict) -> dict:
    """Build the variable namespace documented for V2 templates."""
    artist = str(track.get("artist") or "")
    albumartist = str(track.get("albumartist") or "")
    first = artist.split("/")[0].strip()
    filename = str(track.get("path") or "").replace("\\", "/").rsplit("/", 1)[-1]
    song_name = filename.rsplit(".", 1)[0]
    parent = str(track.get("path") or "").replace("\\", "/").rsplit("/", 1)[0]
    grandparent = parent.rsplit("/", 1)[0] if "/" in parent else ""
    track_no = track.get("track_no")
    disc_no = track.get("disc_no")
    return {
        "title": str(track.get("title") or ""),
        "artist": artist,
        "first_artist": first,
        "first_artist_letter": first[:1].upper(),
        "album": str(track.get("album") or ""),
        "albumartist": albumartist,
        "discnumber": str(disc_no) if disc_no else "",
        "tracknumber": str(track_no) if track_no else "",
        "filename": filename,
        "file_song_name": song_name,
        "parent_path": parent,
        "parent_parent_path": grandparent,
        "lyrics": str(track.get("lyrics") or ""),
        "comment": str(track.get("comment") or ""),
        "year": str(track.get("year") or ""),
        "genre": str(track.get("genre") or ""),
        "composer": str(track.get("composer") or ""),
        "lyricist": str(track.get("lyricist") or ""),
        "counter": str(track.get("counter") or ""),
        "null": "",
    }


def validate(template: str) -> None:
    template = template or ""
    if template.count("${") != len(TEMPLATE_RE.findall(template)):
        raise TemplateError("unclosed ${ expression")
    for match in TEMPLATE_RE.finditer(template):
        try:
            tree = ast.parse(match.group(1).strip(), mode="eval")
        except SyntaxError as exc:
            raise TemplateError(f"invalid expression: {exc.msg}") from exc
        _validate(tree)
