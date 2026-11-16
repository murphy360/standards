#!/usr/bin/env python3
"""A small, strict subset of YAML, read with the standard library.

    from yaml_subset import YamlError, load
    data = load(text)   # dicts, lists, strings and None

It reads the shape a hand-written list of records takes:

* block mappings (``key: value``) and block sequences (``- item``), nested by
  indentation with spaces; a sequence under a key may sit at the key's own
  indentation, and a sequence item may start a mapping (``- term: Route``);
* plain scalars, also over more indented lines that follow, which are joined with
  a space (a blank line between them is a line break); single- and double-quoted
  scalars on one line; ``|`` and ``>`` block scalars, with ``-`` to drop the last
  line break;
* flow sequences of scalars on one line: ``[a, "b, c"]``;
* ``#`` comments, and one ``---`` on the first line.

Every scalar is text: ``yes``, ``12`` and ``null`` stay strings. A key with no value
is ``None``. A key is a plain word or words. Anything else is an error that names its
line, never a guess: anchors, aliases, tags, flow mappings, a quoted or flow value
over several lines, a tab in the indentation, a key given twice, a plain value with
``: `` in it, a ``+`` or a number after ``|`` or ``>``. What this reads, a YAML parser
reads as the same structure, so a file that passes here passes pre-commit's
check-yaml too.

A sequence is an ``Items``, a list that also holds the line each item starts on.

Standard library only.
"""

from __future__ import annotations

import json
import re

KEY_RE = re.compile(r"([A-Za-z0-9_][^:#]*?) *:(?: +(.*))?$")
BLOCK_RE = re.compile(r"([|>])(-?)")
# A plain scalar cannot start with these: YAML gives each a meaning of its own.
NOT_PLAIN = set("{}[]#&*!%@`,'\"|>")
NOT_PLAIN_PAIRS = ("- ", "? ", ": ")
NOT_READ = {
    "&": "an anchor is not read",
    "*": "an alias is not read",
    "!": "a tag is not read",
    "{": "a {mapping} on one line is not read: write each key on a line of its own",
    "|": "a block takes `|`, `|-`, `>` or `>-`, alone on the line",
    ">": "a block takes `|`, `|-`, `>` or `>-`, alone on the line",
}


class YamlError(ValueError):
    """The text is not in the subset; ``line`` is 1-based."""

    def __init__(self, line: int, message: str):
        super().__init__(f"line {line}: {message}")
        self.line = line
        self.message = message


class Items(list):
    """A sequence; ``lines[i]`` is the line item ``i`` starts on (1-based)."""

    def __init__(self):
        super().__init__()
        self.lines: list[int] = []

    def add(self, value, line: int) -> None:
        self.append(value)
        self.lines.append(line)


def cut_comment(text: str) -> tuple[str, bool]:
    """``text`` up to a ``#`` that starts it or follows a space, and whether there
    was one."""
    for i, char in enumerate(text):
        if char == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i], True
    return text, False


def is_dash(text: str) -> bool:
    return text == "-" or text.startswith("- ")


def rest_is_empty(rest: str, line: int) -> None:
    """What follows a quoted value or a flow sequence: nothing, or a comment."""
    if rest.strip() and not (rest[0] in " \t" and rest.strip().startswith("#")):
        raise YamlError(line, f"`{rest.strip()}` after the value")


def check_plain(text: str, line: int, where: str = "") -> str:
    if not text:
        raise YamlError(line, f"an empty value{where}")
    if text[0] in NOT_READ:
        raise YamlError(line, f"`{text}`: {NOT_READ[text[0]]}")
    if text[0] in NOT_PLAIN or text[:2] in NOT_PLAIN_PAIRS or text in ("-", "?", ":"):
        raise YamlError(
            line, f"`{text}`: put a value that starts with `{text[0]}` in quotes"
        )
    if ": " in text or text.endswith(":"):
        raise YamlError(line, f"`{text}`: put a value that holds `: ` in quotes")
    return text


def quoted(text: str, line: int) -> tuple[str, str]:
    """The quoted scalar ``text`` starts with, and what follows its closing quote."""
    if text[0] == "'":
        out, i = [], 1
        while i < len(text):
            if text[i] == "'" and text[i + 1 : i + 2] == "'":
                out.append("'")
                i += 2
            elif text[i] == "'":
                return "".join(out), text[i + 1 :]
            else:
                out.append(text[i])
                i += 1
    else:
        i = 1
        while i < len(text):
            if text[i] == "\\":
                i += 2
            elif text[i] == '"':
                try:
                    return json.loads(text[: i + 1]), text[i + 1 :]
                except json.JSONDecodeError as error:
                    raise YamlError(
                        line, "an escape this reader does not know: use single quotes"
                    ) from error
            else:
                i += 1
    raise YamlError(
        line, "a quoted value closes on its own line (use > for a long one)"
    )


def flow(text: str, line: int) -> Items:
    """``[a, 'b', "c"]`` on one line: a sequence of scalars."""
    items, i, expect = Items(), 1, True
    while True:
        while i < len(text) and text[i] == " ":
            i += 1
        if i >= len(text):
            raise YamlError(line, "a [list] closes on its own line")
        char = text[i]
        if char == "]":
            i += 1
            break
        if char == "," and not expect:
            expect, i = True, i + 1
            continue
        if not expect or char in ",[{":
            raise YamlError(line, f"`{text}`: a [list] holds plain or quoted values")
        if char in "'\"":
            value, rest = quoted(text[i:], line)
            i = len(text) - len(rest)
        else:
            found = re.match(r"[^,\[\]{}]*", text[i:])
            value = found.group(0).rstrip() if found else ""
            i += len(found.group(0)) if found else 0
            check_plain(value, line, " in a [list]")
            if " #" in value:
                raise YamlError(line, "a comment inside a [list]")
        items.add(value, line)
        expect = False
    rest_is_empty(text[i:], line)
    return items


def fold(lines: list[str]) -> str:
    """The lines of a ``>`` block: joined with a space, a blank line a line break."""
    pieces: list[str] = []
    blanks = 0
    for text in lines:
        if not text:
            blanks += 1
            continue
        if pieces:
            pieces.append("\n" * blanks if blanks else " ")
        elif blanks:
            pieces.append("\n" * blanks)
        pieces.append(text)
        blanks = 0
    return "".join(pieces)


class Reader:
    """One document, read top to bottom. ``i`` is the next line to read."""

    def __init__(self, text: str):
        self.lines = [x.rstrip("\r") for x in text.split("\n")]
        self.i = 0

    def indent(self, j: int) -> int:
        line = self.lines[j]
        lead = line[: len(line) - len(line.lstrip(" \t"))]
        if "\t" in lead:
            raise YamlError(j + 1, "a tab in the indentation: indent with spaces")
        return len(lead)

    def peek(self) -> int | None:
        """The next line from ``i`` that holds more than a comment."""
        for j in range(self.i, len(self.lines)):
            text = self.lines[j].strip()
            if text and not text.startswith("#"):
                self.indent(j)
                return j
        return None

    def document(self):
        j = self.peek()
        if j is not None and self.lines[j] == "---":
            self.i = j + 1
            j = self.peek()
        if j is None:
            return None
        if self.indent(j) != 0:
            raise YamlError(j + 1, "the top level starts in the first column")
        value = self.node(0, -1)
        j = self.peek()
        if j is not None:
            raise YamlError(j + 1, "the indentation does not match the lines above it")
        return value

    def node(self, indent: int, parent: int):
        """The node whose first line ``peek`` gives, at column ``indent``, inside a
        node at column ``parent``."""
        j = self.peek()
        assert j is not None  # every caller peeked first
        text = self.lines[j][indent:]
        if is_dash(text):
            return self.sequence(indent)
        if KEY_RE.match(text):
            return self.mapping(indent)
        return self.value(text, j, parent)

    def nested(self, indent: int):
        """What sits under a ``key:`` or a ``-`` with nothing after it."""
        j = self.peek()
        if j is None or self.indent(j) <= indent:
            return None
        return self.node(self.indent(j), indent)

    def sequence(self, indent: int) -> Items:
        items = Items()
        while (j := self.peek()) is not None and self.indent(j) == indent:
            line = self.lines[j]
            if not is_dash(line[indent:]):
                break
            rest = line[indent + 1 :]
            if not cut_comment(rest)[0].strip():
                self.i = j + 1
                items.add(self.nested(indent), j + 1)
                continue
            col = indent + 1 + len(rest) - len(rest.lstrip(" "))
            # The item is read as a node of its own at its column: the dash
            # becomes a space, so that a mapping's later keys line up with it.
            self.lines[j] = " " * col + line[col:]
            self.i = j
            items.add(self.node(col, indent), j + 1)
        return items

    def mapping(self, indent: int) -> dict:
        out: dict = {}
        while (j := self.peek()) is not None and self.indent(j) == indent:
            text = self.lines[j][indent:]
            found = KEY_RE.match(text)
            if is_dash(text) or not found:
                raise YamlError(j + 1, f"`{text.strip()}`: expected `key: value`")
            key, rest = found.group(1), found.group(2) or ""
            if key in out:
                raise YamlError(j + 1, f"`{key}` is given twice")
            self.i = j + 1
            if cut_comment(rest)[0].strip():
                out[key] = self.value(rest, j, indent)
                continue
            k = self.peek()
            if (
                k is not None
                and self.indent(k) == indent
                and is_dash(self.lines[k][indent:])
            ):
                out[key] = self.sequence(indent)
            else:
                out[key] = self.nested(indent)
        return out

    def value(self, text: str, j: int, parent: int):
        """A scalar or a flow sequence that starts on line ``j``."""
        text = text.strip()
        if text[0] in "'\"":
            value, rest = quoted(text, j + 1)
            rest_is_empty(rest, j + 1)
            self.i = j + 1
            return value
        if text[0] == "[":
            self.i = j + 1
            return flow(text, j + 1)
        block = BLOCK_RE.fullmatch(cut_comment(text)[0].strip())
        if block:
            return self.block(block.group(1) == ">", block.group(2) == "-", j, parent)
        return self.plain(text, j, parent)

    def plain(self, text: str, j: int, parent: int) -> str:
        """A plain scalar, with the more indented lines that follow it. A comment
        ends it."""
        first, ended = cut_comment(text)
        value = check_plain(first.strip(), j + 1)
        k = j + 1
        while not ended:
            blanks = 0
            while k < len(self.lines) and not self.lines[k].strip():
                blanks, k = blanks + 1, k + 1
            if k >= len(self.lines) or self.lines[k].strip().startswith("#"):
                break
            if self.indent(k) <= parent:
                break
            more, ended = cut_comment(self.lines[k].strip())
            value += ("\n" * blanks or " ") + check_plain(more.strip(), k + 1)
            j, k = k, k + 1
        self.i = j + 1
        return value

    def block(self, folded: bool, strip: bool, j: int, parent: int) -> str:
        """A ``|`` or ``>`` block: the lines under it, more indented than its key."""
        lines: list[str] = []
        at = None
        k = j + 1
        while k < len(self.lines):
            line = self.lines[k]
            if not line.strip():
                lines.append("")
                k += 1
                continue
            indent = self.indent(k)
            if indent <= parent:
                break
            if at is not None and indent < at:
                raise YamlError(k + 1, "less indented than the first line of its block")
            at = indent if at is None else at
            lines.append(line[at:])
            k += 1
        self.i = k
        while lines and not lines[-1]:
            lines.pop()
        if folded and any(x[:1] in (" ", "\t") for x in lines):
            raise YamlError(j + 1, "a > block's lines start at one indentation: use |")
        text = fold(lines) if folded else "\n".join(lines)
        return text if strip or not text else text + "\n"


def load(text: str):
    """The document ``text`` holds: dicts, ``Items``, strings and None."""
    return Reader(text).document()
