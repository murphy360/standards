"""Silencers: each way to turn a check off in a file carries its reason, and is counted.

``code_rules.py`` imports this module. A silencer reaches a green run without fixing
anything. ``# noqa`` is not allowed at all (``noqa_comments``, on every run). The
others are allowed with a reason, and ``--silencers`` checks that each has one:

* **a pytest skip or xfail**: ``pytest.mark.skip``, ``skipif`` and ``xfail`` need a
  ``reason=``. A condition says when, not why, so ``skipif`` and ``xfail`` need it
  too. The text of ``pytest.mark.skip("text")``, ``pytest.skip("text")`` and
  ``pytest.xfail("text")`` is the reason. ``pytest.importorskip`` names the module
  that is missing, and that is its reason. Marks are read from the syntax tree, by
  the names the file imports from pytest.
* **a type ignore**: ``# type: ignore`` and ``# pyright: ignore`` need the error code
  in brackets, and a comment that says why: after it on the same line, or on the line
  above.
* **``# pragma: no cover``** and **``# shellcheck disable=``**: a comment on the line
  above that says why. shellcheck directives are read in ``*.sh`` and in workflow
  files, where actionlint runs shellcheck on every ``run:`` script.
* **``.skip``** on ``it``, ``test`` or ``describe`` in a JavaScript or TypeScript test
  file: a comment on the line above. **``.only``** there is always a problem.

A reason is a comment with words of its own: not a shebang and not another directive.
A run of directives on lines of their own shares the comment above the first. The
tool checks that a reason is there. Whether it is a good one is for a review.

Python comments are read with the tokenizer and marks with the syntax tree, so the
words in a string or a docstring are not silencers. JavaScript is read with its
comments and strings blanked. A shell script and a workflow are read by the line.

The last line of a run counts every silencer left, with or without its reason, and
every ``eslint-disable`` comment, so a rising count shows in every run.

Not seen here: a removed test, a weakened assertion, and what a tool's own config
switches off. Standard library only.
"""

from __future__ import annotations

import ast
import re
import tokenize
from pathlib import Path

import code_rules_config as config

HELP = (
    "a skip, an xfail, a type ignore, a `# pragma: no cover` and a `# shellcheck "
    "disable=` each need a reason, `.only` is a problem, and the last line counts them"
)
NOQA_RE = re.compile(r"#\s*(ruff\s*:\s*)?noqa\b", re.IGNORECASE)
# As pyright reads it: after any `#` of the comment, then a bracket, a space or the end.
IGNORE_RE = re.compile(r"#\s*(type|pyright):\s*ignore(\[[^\]]*\])?(?=\s|#|$)")
# coverage.py's own default pattern.
NO_COVER_RE = re.compile(r"#\s*(pragma|PRAGMA)[:\s]?\s*(no|NO)\s*(cover|COVER)")
SHELLCHECK_RE = re.compile(r"#\s*shellcheck\s+(?:\S+\s+)*?disable=(\S+)")
# `it.skip`, `describe.only`, `test.concurrent.skip`; not `it.skipIf`, not `a.it.skip`.
JS_TEST_RE = re.compile(r"(?<![\w$.])(it|test|describe)((?:\.\w+)*?)\.(skip|only)\b")
JS_TEST_DIRS = {"test", "tests", "__tests__"}
# A comment that speaks to a tool, so it cannot be the reason of another one.
DIRECTIVE_RE = re.compile(
    r"type:\s*ignore|pyright:|mypy:|pragma:|(ruff\s*:\s*)?noqa\b|fmt:|isort:|"
    r"shellcheck\s+\S+=|eslint-|prettier-ignore|@ts-",
    re.IGNORECASE,
)
WORD_RE = re.compile(r"[^\W\d_]{2,}")  # two letters together
MARKS = {"mark.skip", "mark.skipif", "mark.xfail"}
CALLS = {"skip", "xfail", "importorskip"}
TEXT_IS_REASON = {"mark.skip", "skip", "xfail"}  # the first argument is the reason
ABOVE = "has no reason; say why in a comment on the line above"
NO_MARK_REASON = (
    'has no reason; say why in `reason="..."` (a condition says when, not why), or '
    "remove the mark and fix the test"
)
NO_CALL_REASON = (
    "has no reason; pass the reason as its text, or remove the call and fix the test"
)
ONLY = "is not allowed: it switches every other test off; remove the `.only`"

# One silencer: its line, what it is, and what it lacks with its fix (None: nothing).
Found = tuple[int, str, "str | None"]


def says_why(comment: str | None) -> bool:
    """Whether a comment has words of its own: no shebang, no other directive."""
    text = (comment or "").strip()
    if text.startswith("#!"):
        return False
    text = text.lstrip("#/*").strip()
    return bool(WORD_RE.search(text)) and not DIRECTIVE_RE.match(text)


def reason_above(own: dict[int, str], line: int) -> bool:
    """Whether the comment on the line above says why.

    ``own`` holds each comment that has a line of its own, by its line. A run of
    directives on lines of their own shares the comment above the first of them.
    """
    above = line - 1
    while above in own and DIRECTIVE_RE.match(own[above].lstrip("#/* \t")):
        above -= 1
    return says_why(own.get(above))


def python_tokens(path: Path) -> list[tokenize.TokenInfo] | None:
    """A Python file's tokens; None when it does not tokenize (ruff reports it)."""
    try:
        with path.open("rb") as fh:
            return list(tokenize.tokenize(fh.readline))
    except (tokenize.TokenError, SyntaxError):
        return None


def noqa_comments(files: list[str], root: Path) -> list[str]:
    """Every ``# noqa`` or ``# ruff: noqa`` comment in the Python files.

    Comments are read with the tokenizer, so the words in a string or a docstring
    are not comments. A file that does not tokenize is left to ruff, which reports it.
    """
    out = []
    for rel in files:
        path = root / rel
        if not rel.endswith(".py") or not path.is_file():
            continue
        for tok in python_tokens(path) or ():
            if tok.type == tokenize.COMMENT and NOQA_RE.search(tok.string):
                out.append(
                    f"noqa: {rel}:{tok.start[0]}: a `# noqa` is not allowed; remove "
                    "it and fix the finding it hid"
                )
    return out


def ignore_problem(hit: re.Match, after: str, above: bool) -> str | None:
    """What a type ignore lacks: its error code, its reason or both."""
    lacks = []
    if not (hit.group(2) or "").strip("[], "):
        lacks.append(
            f"names no error code (write `# {hit.group(1)}: ignore[code]`, with the "
            "code the type checker prints)"
        )
    if not above and not says_why(after):
        lacks.append(
            "has no reason (say why in a comment after it on the same line, or on "
            "the line above)"
        )
    return " and ".join(lacks) + "; or fix the type error" if lacks else None


def comment_silencers(tokens: list[tokenize.TokenInfo]) -> list[Found]:
    """The type ignores and the ``# pragma: no cover`` among a file's comments."""
    comments = [t for t in tokens if t.type == tokenize.COMMENT]
    own = {t.start[0]: t.string for t in comments if t.line.lstrip().startswith("#")}
    out: list[Found] = []
    for tok in comments:
        line, text = tok.start[0], tok.string
        above = reason_above(own, line)
        for hit in IGNORE_RE.finditer(text):
            label = f"`# {hit.group(1)}: ignore{hit.group(2) or ''}`"
            out.append((line, label, ignore_problem(hit, text[hit.end() :], above)))
        if NO_COVER_RE.search(text):
            lacks = None if above else f"{ABOVE}, or test the code"
            out.append((line, "`# pragma: no cover`", lacks))
    return out


def pytest_names(tree: ast.Module) -> dict[str, str]:
    """What each name the file imports from pytest is there: "" for pytest itself."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(
                {a.asname or a.name: "" for a in node.names if a.name == "pytest"}
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "pytest":
            names.update({a.asname or a.name: a.name for a in node.names})
    return names


def pytest_name(node: ast.AST, names: dict[str, str]) -> str | None:
    """``mark.skip`` for ``pytest.mark.skip``, however the file imported pytest."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name) or node.id not in names:
        return None
    return ".".join(filter(None, [names[node.id], *reversed(parts)]))


def has_reason(name: str, call: ast.Call) -> bool:
    """Whether a mark or a skip call gives its reason.

    ``reason=`` (``msg=`` before pytest 7), or the first argument where pytest reads
    it as the reason. A literal must be a text that is not blank; any other
    expression is taken as given. ``importorskip`` names the missing module.
    """
    if name == "importorskip":
        return True
    given = [k.value for k in call.keywords if k.arg in ("reason", "msg", None)]
    if name in TEXT_IS_REASON:
        given += call.args[:1]
    return any(
        not isinstance(v, ast.Constant)
        or (isinstance(v.value, str) and bool(v.value.strip()))
        for v in given
    )


def mark_silencers(tree: ast.Module) -> list[Found]:
    """The pytest skips and xfails of a file: marks, called or bare, and calls."""
    names = pytest_names(tree)
    out: list[Found] = []
    called = set()
    # ast.walk gives a call before the name it calls, so a called mark is seen once
    for node in ast.walk(tree) if names else ():
        if isinstance(node, ast.Call):
            called.add(id(node.func))
            name = pytest_name(node.func, names)
            if name in MARKS:
                lacks = None if has_reason(name, node) else NO_MARK_REASON
                out.append((node.lineno, f"`pytest.{name}`", lacks))
            elif name in CALLS:
                lacks = None if has_reason(name, node) else NO_CALL_REASON
                out.append((node.lineno, f"`pytest.{name}()`", lacks))
        elif isinstance(node, ast.Attribute) and id(node) not in called:
            name = pytest_name(node, names)
            if name in MARKS:
                out.append((node.lineno, f"`pytest.{name}`", NO_MARK_REASON))
    return out


def python_silencers(path: Path) -> list[Found]:
    """A Python file's silencers; none when it does not parse (ruff reports it)."""
    tokens = python_tokens(path)
    try:
        tree = ast.parse(path.read_bytes())
    except (SyntaxError, ValueError):
        return []
    if tokens is None:
        return []
    return comment_silencers(tokens) + mark_silencers(tree)


def shell_silencers(text: str) -> list[Found]:
    """The ``# shellcheck disable=`` directives of a shell script or a workflow.

    shellcheck reads a directive only on a line of its own, so only those count.
    """
    own = {
        n: line.strip()
        for n, line in enumerate(text.splitlines(), 1)
        if line.lstrip().startswith("#")
    }
    out: list[Found] = []
    for line, comment in own.items():
        hit = SHELLCHECK_RE.match(comment)
        if hit:
            lacks = f"{ABOVE}, or fix what shellcheck found"
            label = f"`# shellcheck disable={hit.group(1)}`"
            out.append((line, label, None if reason_above(own, line) else lacks))
    return out


def js_silencers(text: str) -> list[Found]:
    """The ``.skip`` and ``.only`` of a JavaScript or TypeScript test file.

    Each comment and string is blanked first, so the words in one are not a call. A
    comment that starts a line is kept by the line it ends on, for the reason.
    """
    own: dict[int, str] = {}

    def blank(piece: re.Match) -> str:
        found = piece.group()
        start = text.rfind("\n", 0, piece.start()) + 1
        if found.startswith("/") and not text[start : piece.start()].strip():
            own[text.count("\n", 0, piece.end()) + 1] = found
        return re.sub(r"[^\n]", " ", found)

    code = config.JS_PIECE_RE.sub(blank, text)
    out: list[Found] = []
    for hit in JS_TEST_RE.finditer(code):
        line = code.count("\n", 0, hit.start()) + 1
        lacks = ONLY
        if hit.group(3) == "skip":
            lacks = f"{ABOVE}, or remove the `.skip` and fix the test"
            lacks = None if reason_above(own, line) else lacks
        out.append((line, f"`{hit.group(1)}{hit.group(2)}.{hit.group(3)}`", lacks))
    return out


def is_js_test(rel: str) -> bool:
    """A JavaScript or TypeScript test: in a test directory, or `.test.` or `.spec.`."""
    path = Path(rel)
    return rel.endswith(config.JS_SUFFIXES) and (
        bool(JS_TEST_DIRS & set(path.parts[:-1]))
        or ".test." in path.name
        or ".spec." in path.name
    )


def is_workflow(rel: str) -> bool:
    return rel.endswith((".yml", ".yaml")) and "/.github/workflows/" in f"/{rel}"


def file_silencers(rel: str, path: Path) -> list[Found]:
    """The silencers of one file, by what kind of file it is."""
    if rel.endswith(".py"):
        return python_silencers(path)
    if rel.endswith(".sh") or is_workflow(rel):
        return shell_silencers(path.read_text(encoding="utf-8", errors="replace"))
    if is_js_test(rel):
        return js_silencers(path.read_text(encoding="utf-8", errors="replace"))
    return []


def scan(root: Path, files: list[str]) -> list[tuple[str, int, str, str | None]]:
    """Every silencer in ``files``: its file, its line, what it is, what it lacks."""
    out = []
    for rel in files:
        path = root / rel
        if path.is_file():
            found = sorted(file_silencers(rel, path), key=lambda f: f[0])
            out += [(rel, *f) for f in found]
    return out


def measure(root: Path, tracked: set[str], a) -> tuple[list[str], int | None]:
    """(the problem lines, the count of silencers left); ([], None) without the flag.

    ``tracked`` is every tracked file in ``--paths``. Vendored code is left out, as
    it is from the size limit. The count holds the ``eslint-disable`` comments too:
    ``--no-inline-config`` is what makes one a problem.
    """
    if not a.silencers:
        return [], None
    files = sorted(
        f for f in tracked if "/vendor/" not in f and "node_modules" not in f
    )
    found = scan(root, files)
    problems = [
        f"silencer: {rel}:{line}: {label} {lacks}"
        for rel, line, label, lacks in found
        if lacks
    ]
    return problems, len(found) + len(config.eslint_disable_comments(files, root))


def left(now: dict) -> list[str]:
    """What the last line says of the silencers; nothing without the flag."""
    count = now.get("silencers")
    return [] if count is None else [f"{count} silencer(s)"]
