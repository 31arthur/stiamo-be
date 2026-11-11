"""Expressions for workflow nodes, in the spirit of n8n's: `{{ $json.subject }}` inside any text a node is given.

A small, safe language of my own (nothing is evaluated as Python): literals, `$` paths, comparison, `&&` `||` `!`, `+ -`, and a short list of
functions. A parameter that is exactly one `{{ … }}` keeps the value's type (a list stays a list); inside other text the value is written in.

What a path can start from:
  $json         the item being worked on          $json.sender.name, $json.tags[0]
  $input        every item that came in           $input.length, $input[0].subject
  $node         what an earlier node produced     $node["Mail"].items, $node["Mail"].first.subject, $node["Mail"].count
  $workspace    the workspace the run is limited to ("" for every one)
  $vars         values stored by Set nodes        $vars.tone
  $now, $today  the current time and day
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any


class ExpressionError(ValueError):
    """An expression that can't be read or evaluated, with words a person can act on."""


_TOKEN = re.compile(
    r"""\s*(?:
    (?P<num>\d+(?:\.\d+)?)|
    (?P<str>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|
    (?P<op>==|!=|>=|<=|&&|\|\||[-+!<>().,\[\]])|
    (?P<id>\$?[A-Za-z_][\w]*)
    )""",
    re.VERBOSE,
)
MAX_LEN = 500


def _tokens(src: str) -> list[tuple[str, str]]:
    if len(src) > MAX_LEN:
        raise ExpressionError("That expression is too long.")
    out, i = [], 0
    while i < len(src):
        if src[i:].strip() == "":
            break
        m = _TOKEN.match(src, i)
        if not m or m.end() == i:
            raise ExpressionError(f"Can't read “{src[i:i + 12].strip()}” in the expression.")
        i = m.end()
        kind = m.lastgroup or ""
        out.append((kind, m.group(kind)))
    return out


class _Parser:
    def __init__(self, src: str, scope: dict[str, Any]) -> None:
        self.t, self.i, self.scope = _tokens(src), 0, scope

    def _peek(self) -> tuple[str, str] | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def _eat(self, text: str | None = None) -> tuple[str, str]:
        tok = self._peek()
        if tok is None or (text is not None and tok[1] != text):
            raise ExpressionError(f"Expected “{text}” in the expression." if text else "The expression ends too soon.")
        self.i += 1
        return tok

    def parse(self) -> Any:
        v = self._or()
        if self._peek() is not None:
            raise ExpressionError(f"Unexpected “{self._peek()[1]}” in the expression.")  # type: ignore[index]
        return v

    def _or(self) -> Any:
        v = self._and()
        while (t := self._peek()) and t[1] == "||":
            self._eat()
            r = self._and()
            v = bool(v) or bool(r)
        return v

    def _and(self) -> Any:
        v = self._cmp()
        while (t := self._peek()) and t[1] == "&&":
            self._eat()
            r = self._cmp()
            v = bool(v) and bool(r)
        return v

    def _cmp(self) -> Any:
        a = self._add()
        if (t := self._peek()) and t[1] in ("==", "!=", ">", "<", ">=", "<="):
            self._eat()
            b = self._add()
            return _compare(t[1], a, b)
        return a

    def _add(self) -> Any:
        v = self._unary()
        while (t := self._peek()) and t[1] in ("+", "-"):
            self._eat()
            r = self._unary()
            if t[1] == "+":
                v = (v + r) if isinstance(v, (int, float)) and isinstance(r, (int, float)) and not isinstance(v, bool) else f"{_s(v)}{_s(r)}"
            else:
                v = _num(v) - _num(r)
        return v

    def _unary(self) -> Any:
        if (t := self._peek()) and t[1] == "!":
            self._eat()
            return not self._unary()
        if t and t[1] == "-":
            self._eat()
            return -_num(self._unary())
        return self._primary()

    def _primary(self) -> Any:
        kind, text = self._eat()
        if kind == "num":
            return float(text) if "." in text else int(text)
        if kind == "str":
            return bytes(text[1:-1], "utf-8").decode("unicode_escape") if "\\" in text else text[1:-1]
        if text == "(":
            v = self._or()
            self._eat(")")
            return v
        if kind == "id":
            if text in ("true", "false", "null"):
                return {"true": True, "false": False, "null": None}[text]
            if text.startswith("$"):
                return self._path(self.scope.get(text), text)
            return self._call(text)
        raise ExpressionError(f"Unexpected “{text}” in the expression.")

    def _path(self, value: Any, root: str) -> Any:
        while (t := self._peek()) and t[1] in (".", "["):
            self._eat()
            if t[1] == "[":
                key = self._or()
                self._eat("]")
            else:
                kind, key = self._eat()
                if kind != "id":
                    raise ExpressionError("Expected a name after “.” in the expression.")
            value = _get(value, key)
        return value

    def _call(self, name: str) -> Any:
        self._eat("(")
        args: list[Any] = []
        if (t := self._peek()) and t[1] != ")":
            args.append(self._or())
            while (t := self._peek()) and t[1] == ",":
                self._eat()
                args.append(self._or())
        self._eat(")")
        fn = FUNCTIONS.get(name)
        if fn is None:
            raise ExpressionError(f"Unknown function “{name}”. Known: {', '.join(sorted(FUNCTIONS))}.")
        try:
            return fn(*args)
        except (TypeError, ValueError, IndexError) as exc:
            raise ExpressionError(f"“{name}” couldn't use what it was given.") from exc


def _get(value: Any, key: Any) -> Any:
    """A step along a path. Anything missing is None, never an error: an expression about a field that isn't there is simply empty."""
    if isinstance(value, dict):
        return value.get(key)
    if isinstance(value, list):
        if key == "length":
            return len(value)
        if key == "first":
            return value[0] if value else None
        if key == "last":
            return value[-1] if value else None
        if isinstance(key, (int, float)) and not isinstance(key, bool):
            return value[int(key)] if -len(value) <= int(key) < len(value) else None
    if isinstance(value, str) and key == "length":
        return len(value)
    return None


def _s(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError) as exc:
        raise ExpressionError(f"“{_s(v)}” isn't a number.") from exc


def _compare(op: str, a: Any, b: Any) -> bool:
    if op in ("==", "!="):
        same = _s(a).lower() == _s(b).lower() if isinstance(a, str) or isinstance(b, str) else a == b
        return same if op == "==" else not same
    try:
        x, y = _num(a), _num(b)
    except ExpressionError:
        x, y = _s(a), _s(b)  # words compare as words
    return {">": x > y, "<": x < y, ">=": x >= y, "<=": x <= y}[op]  # type: ignore[operator]


FUNCTIONS: dict[str, Any] = {
    "contains": lambda a, b: _s(b).lower() in _s(a).lower() if not isinstance(a, list) else b in a,
    "startsWith": lambda a, b: _s(a).lower().startswith(_s(b).lower()),
    "endsWith": lambda a, b: _s(a).lower().endswith(_s(b).lower()),
    "matches": lambda a, p: re.search(_s(p)[:200], _s(a), re.IGNORECASE) is not None,
    "lower": lambda a: _s(a).lower(),
    "upper": lambda a: _s(a).upper(),
    "trim": lambda a: _s(a).strip(),
    "len": lambda a: len(a) if isinstance(a, (list, dict, str)) else 0,
    "join": lambda a, sep=", ": _s(sep).join(_s(x) for x in (a or [])),
    "first": lambda a: a[0] if a else None,
    "last": lambda a: a[-1] if a else None,
    "isEmpty": lambda a: a in (None, "", [], {}, 0, False) or (isinstance(a, str) and a.strip() == ""),
    "default": lambda a, b: b if a in (None, "") else a,
    "int": lambda a: int(_num(a)),
    "str": lambda a: _s(a),
    "now": lambda: datetime.now(UTC).isoformat(timespec="seconds"),
    "today": lambda: datetime.now(UTC).date().isoformat(),
    "slice": lambda a, n: (a or [])[: int(n)] if isinstance(a, list) else _s(a)[: int(n)],
}

_WHOLE = re.compile(r"^\s*\{\{(.+?)\}\}\s*$", re.DOTALL)
_ANY = re.compile(r"\{\{(.+?)\}\}", re.DOTALL)


def evaluate(src: str, scope: dict[str, Any]) -> Any:
    """One expression (the text between the braces) against a scope."""
    return _Parser(src, scope).parse()


def resolve(value: Any, scope: dict[str, Any]) -> Any:
    """A node parameter with its expressions worked out. Text that is only one `{{ … }}` keeps the type of its value; text with several, or with
    words around them, is written out as text. Lists and objects are resolved inside. Anything else is returned as it is."""
    if isinstance(value, str):
        if "{{" not in value:
            return value
        if (m := _WHOLE.match(value)) and "}}" not in m.group(1):
            return evaluate(m.group(1), scope)
        return _ANY.sub(lambda m: _s(evaluate(m.group(1), scope)), value)
    if isinstance(value, list):
        return [resolve(v, scope) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v, scope) for k, v in value.items()}
    return value
