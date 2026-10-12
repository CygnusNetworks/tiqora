"""Safe parser for Perl data literals as Znuny stores them (wave-4 step 0, pure).

Znuny 6.x keeps ITSM CI class definitions (``configitem_definition``) and some SysConfig values
as Perl source that Znuny itself ``eval``s. Tiqora never evaluates Perl: this module parses the
data subset only, with a hand-written linear scanner and a bounded recursive descent.

Grammar (union of the ITSM core's and the ITSM importer's needs):

* ``[ … ]`` arrays and ``{ … }`` hashes, items separated by ``,``, trailing commas allowed;
  hash pairs are ``key => value``;
* hash keys: barewords ``[A-Za-z_][A-Za-z0-9_:]*``, quoted strings or numbers (Perl hash keys
  are strings, so numeric keys become ``str``);
* single-quoted strings: ``\\\\`` → ``\\``, ``\\'`` → ``'``, any other backslash stays literal;
* double-quoted strings: escapes ``\\\\ \\' \\" \\n \\t`` only; ``$`` or ``@`` (interpolation)
  is an error;
* integers and decimals ``-?(0|[1-9][0-9]*)(\\.[0-9]+)?`` (``int`` / ``float``);
* ``undef`` → ``None`` (the only bareword value);
* ``#`` comments to the end of the line, outside strings.

Anything else (``sub``, ``qw``, ``do``, ``eval``, sigils, references, backticks, octal or hex
numbers, operators, trailing text) raises :class:`PerlDataError` with the character offset.
"""

from __future__ import annotations

from typing import Any, Final

#: input larger than this is rejected before scanning (definitions are a few KiB)
MAX_TEXT_CHARS: Final = 1024 * 1024

_BAREWORD_START: Final = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_")
_BAREWORD_CHARS: Final = _BAREWORD_START | frozenset("0123456789:")
_DIGITS: Final = frozenset("0123456789")
_WHITESPACE: Final = frozenset(" \t\r\n\f\v")
_SINGLE_ESCAPES: Final = {"\\": "\\", "'": "'"}
_DOUBLE_ESCAPES: Final = {"\\": "\\", "'": "'", '"': '"', "n": "\n", "t": "\t"}


class PerlDataError(ValueError):
    """The text is not a supported Perl data literal; ``position`` is the character offset."""

    def __init__(self, position: int, message: str) -> None:
        self.position = position
        self.message = message
        super().__init__(f"{message} at offset {position}")


class _Parser:
    def __init__(self, text: str, *, max_depth: int, max_nodes: int) -> None:
        self.text = text
        self.pos = 0
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.nodes = 0

    # -- scanning -------------------------------------------------------------------------

    def skip_space(self) -> None:
        text, n = self.text, len(self.text)
        while self.pos < n:
            ch = text[self.pos]
            if ch in _WHITESPACE:
                self.pos += 1
            elif ch == "#":
                end = text.find("\n", self.pos)
                self.pos = n if end < 0 else end + 1
            else:
                return

    def peek(self) -> str:
        self.skip_space()
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def fail(self, message: str, position: int | None = None) -> PerlDataError:
        return PerlDataError(self.pos if position is None else position, message)

    def count_node(self) -> None:
        self.nodes += 1
        if self.nodes > self.max_nodes:
            raise self.fail(f"too many nodes (limit {self.max_nodes})")

    def fat_comma(self) -> bool:
        self.skip_space()
        if self.text.startswith("=>", self.pos):
            self.pos += 2
            return True
        return False

    # -- grammar --------------------------------------------------------------------------

    def value(self, depth: int) -> Any:
        ch = self.peek()
        if ch == "[":
            return self.array(depth + 1)
        if ch == "{":
            return self.hash(depth + 1)
        if ch in ("'", '"'):
            self.count_node()
            return self.string()
        if ch == "-" or ch in _DIGITS:
            self.count_node()
            return self.number()
        if ch in _BAREWORD_START:
            start = self.pos
            word = self.bareword()
            if word == "undef":
                self.count_node()
                return None
            raise self.fail(f"bareword {word!r} is not a value", start)
        if not ch:
            raise self.fail("unexpected end of input")
        raise self.fail(f"unexpected character {ch!r}")

    def enter(self, depth: int) -> None:
        if depth > self.max_depth:
            raise self.fail(f"nesting depth exceeds {self.max_depth}")
        self.count_node()
        self.pos += 1  # the opening bracket

    def array(self, depth: int) -> list[Any]:
        self.enter(depth)
        items: list[Any] = []
        while True:
            ch = self.peek()
            if ch == "]":
                self.pos += 1
                return items
            items.append(self.value(depth))
            ch = self.peek()
            if ch == ",":
                self.pos += 1
            elif ch != "]":
                raise self.fail("expected ',' or ']'" if ch else "unterminated array")

    def hash(self, depth: int) -> dict[str, Any]:
        self.enter(depth)
        out: dict[str, Any] = {}
        while True:
            ch = self.peek()
            if ch == "}":
                self.pos += 1
                return out
            key = self.hash_key()
            if not self.fat_comma():
                raise self.fail(
                    "expected '=>' after hash key" if self.peek() else "unterminated hash"
                )
            out[key] = self.value(depth)
            ch = self.peek()
            if ch == ",":
                self.pos += 1
            elif ch != "}":
                raise self.fail("expected ',' or '}'" if ch else "unterminated hash")

    def hash_key(self) -> str:
        ch = self.peek()
        if ch in ("'", '"'):
            return self.string()
        if ch == "-" or ch in _DIGITS:
            return self.number_text()
        if ch in _BAREWORD_START:
            return self.bareword()
        if not ch:
            raise self.fail("unterminated hash")
        raise self.fail(f"unexpected character {ch!r} as hash key")

    def bareword(self) -> str:
        text, n, start = self.text, len(self.text), self.pos
        end = start + 1
        while end < n and text[end] in _BAREWORD_CHARS:
            end += 1
        self.pos = end
        return text[start:end]

    def number_text(self) -> str:
        text, n, start = self.text, len(self.text), self.pos
        pos = start
        if text[pos] == "-":
            pos += 1
        digits_start = pos
        while pos < n and text[pos] in _DIGITS:
            pos += 1
        if pos == digits_start:
            raise self.fail("expected digits after '-'", start)
        if text[digits_start] == "0" and pos - digits_start > 1:
            raise self.fail("octal number literals are not supported", start)
        if pos < n and text[pos] == ".":
            frac_start = pos + 1
            pos = frac_start
            while pos < n and text[pos] in _DIGITS:
                pos += 1
            if pos == frac_start:
                raise self.fail("expected digits after '.'", start)
        if pos < n and (text[pos] in _BAREWORD_CHARS or text[pos] == "."):
            raise self.fail("unsupported number literal", start)
        self.pos = pos
        return text[start:pos]

    def number(self) -> int | float:
        raw = self.number_text()
        return float(raw) if "." in raw else int(raw)

    def string(self) -> str:
        text, n = self.text, len(self.text)
        start = self.pos
        quote = text[start]
        escapes = _SINGLE_ESCAPES if quote == "'" else _DOUBLE_ESCAPES
        parts: list[str] = []
        pos = chunk = start + 1
        while pos < n:
            ch = text[pos]
            if ch == quote:
                parts.append(text[chunk:pos])
                self.pos = pos + 1
                return "".join(parts)
            if ch == "\\":
                nxt = text[pos + 1] if pos + 1 < n else ""
                if nxt in escapes:
                    parts.append(text[chunk:pos])
                    parts.append(escapes[nxt])
                    pos += 2
                    chunk = pos
                    continue
                if quote == '"':
                    raise self.fail(f"unsupported escape '\\{nxt}' in double-quoted string", pos)
                pos += 1  # single quotes: the backslash stays literal
                continue
            if quote == '"' and ch in ("$", "@"):
                raise self.fail(f"interpolation ('{ch}') in double-quoted string", pos)
            pos += 1
        raise self.fail("unterminated string", start)


def parse_perl_data(text: str, *, max_depth: int = 64, max_nodes: int = 200_000) -> Any:
    """Parse one Perl data literal (see the module docstring for the grammar).

    ``max_depth`` bounds array/hash nesting, ``max_nodes`` the number of values. Raises
    :class:`PerlDataError` on anything outside the grammar; never evaluates code.
    """
    if len(text) > MAX_TEXT_CHARS:
        raise PerlDataError(0, f"input larger than {MAX_TEXT_CHARS} characters")
    parser = _Parser(text, max_depth=max_depth, max_nodes=max_nodes)
    if not parser.peek():
        raise PerlDataError(parser.pos, "empty input")
    result = parser.value(0)
    if parser.peek():
        raise parser.fail("unexpected text after the value")
    return result


__all__ = ["MAX_TEXT_CHARS", "PerlDataError", "parse_perl_data"]
