#!/usr/bin/env python3
"""The reuse report: what a project's Python code holds twice. It informs and
never fails.

    python3 reuse_report.py                            # the lists, as text
    python3 reuse_report.py --paths src tools          # only these directories
    python3 reuse_report.py --base <sha> --summary F   # what a pull request added

It reads every tracked ``*.py`` file under the directory it runs in (or under
``--paths``), tests left out (``code_rules.is_test``, and ``conftest.py``), and
lists:

* **names defined twice**: a top-level function or class of one name in more than
  one file (``main`` and dunder names aside: every script has a ``main``);
* **duplicated blocks**: eight or more lines alike in two places, as pylint's
  ``duplicate-code`` finds them. Comments, docstrings, imports, blank lines and
  lines of brackets alone are left out before comparing.

``--base`` is the commit a pull request is compared with. The same lists are made
from that commit's files, read with git (``git ls-tree`` and ``git cat-file``, so
nothing is checked out), and what the head has and the base does not is what the
pull request added. ``--summary`` appends the counts, and what was added, to a
Markdown file: the job summary on GitHub. A base git cannot read (a shallow
checkout) is said, and the report is the head's alone.

No unused-code list: vulture is not in the standard library, and what it finds at
80 % confidence is mostly unused imports, which the lint fails on already (F401).

Standard library only.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import subprocess
import sys
import tokenize
from pathlib import Path, PurePosixPath

from code_rules import git_cmd, git_out, in_paths, is_test, tracked_files

MIN_LINES = 8
SKIP_NAMES = {"main"}
BRACKETS = set("()[]{},:")
MOST_COPIES = 50  # a window alike in more places is boilerplate, not a copy
MOST_LISTED = 50  # the summary lists this many additions, then counts the rest

Sources = dict[str, str]


# -- the files -------------------------------------------------------------------------


def wanted(rel: str, paths: tuple[str, ...]) -> bool:
    return (
        rel.endswith(".py")
        and not is_test(rel)
        and PurePosixPath(rel).name != "conftest.py"
        and in_paths(rel, paths)
    )


def head_sources(root: Path, paths: tuple[str, ...] = ()) -> Sources:
    """{path: text} of the files the report reads, from the working tree."""
    out = {}
    for rel in sorted(tracked_files(root)):
        if wanted(rel, paths) and (root / rel).is_file():
            try:
                out[rel] = (root / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
    return out


def parse_batch(data: bytes, names: list[str]) -> Sources:
    """``git cat-file --batch``'s answer to ``names``, one object each, as text."""
    out, at = {}, 0
    for rel in names:
        end = data.index(b"\n", at)
        header = data[at:end].split()
        at = end + 1
        if len(header) != 3 or header[1] != b"blob":
            continue  # missing, or not a file
        size = int(header[2])
        blob, at = data[at : at + size], at + size + 1
        try:
            out[rel] = blob.decode("utf-8")
        except UnicodeDecodeError:
            continue
    return out


def base_sources(root: Path, base: str, paths: tuple[str, ...] = ()) -> Sources:
    """{path: text} of the same files at the commit ``base``, read with git."""
    prefix = (git_out(root, "rev-parse", "--show-prefix") or "").strip()
    listed = git_out(
        root, "ls-tree", "-r", "-z", "--full-name", "--name-only", base, "--", "."
    )
    if listed is None:
        raise OSError(f"git cannot read the commit {base}")
    full = [f for f in listed.split("\0") if f.startswith(prefix)]
    names = [f[len(prefix) :] for f in full]
    keep = [(f, rel) for f, rel in zip(full, names, strict=True) if wanted(rel, paths)]
    if not keep:
        return {}
    ask = "".join(f"{base}:{f}\n" for f, _ in keep).encode()
    res = subprocess.run(
        [*git_cmd(root), "cat-file", "--batch"],
        cwd=root,
        input=ask,
        capture_output=True,
    )
    if res.returncode != 0:
        raise OSError(f"git cannot read the files of {base}")
    return parse_batch(res.stdout, [rel for _, rel in keep])


def trees(files: Sources) -> dict[str, ast.Module]:
    """The files that parse, each as its syntax tree."""
    out = {}
    for rel, text in files.items():
        try:
            out[rel] = ast.parse(text, rel)
        except (SyntaxError, ValueError):
            continue
    return out


# -- names defined twice ---------------------------------------------------------------


def names_defined_twice(parsed: dict[str, ast.Module]) -> list[dict]:
    """[{name, where: [path:line, ...]}] for a top-level function or class name that
    more than one file defines."""
    seen: dict[str, list[str]] = {}
    for rel, tree in sorted(parsed.items()):
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if n.name in SKIP_NAMES or n.name.startswith("__"):
                    continue
                seen.setdefault(n.name, []).append(f"{rel}:{n.lineno}")
    return [
        {"name": k, "where": v}
        for k, v in sorted(seen.items())
        if len({w.rsplit(":", 1)[0] for w in v}) > 1
    ]


# -- duplicated blocks -----------------------------------------------------------------


def docstring_lines(n: ast.AST) -> range:
    body = getattr(n, "body", None)
    if (
        not isinstance(
            n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        )
        or not body
    ):
        return range(0)
    first = body[0]
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return range(first.lineno, (first.end_lineno or first.lineno) + 1)
    return range(0)


def skipped_lines(tree: ast.AST | None) -> set[int]:
    """The lines of docstrings and imports."""
    out: set[int] = set()
    for n in ast.walk(tree) if tree is not None else ():
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            out.update(range(n.lineno, (n.end_lineno or n.lineno) + 1))
        out.update(docstring_lines(n))
    return out


def without_comments(text: str) -> list[str]:
    """The source's lines with every comment removed."""
    lines = text.splitlines()
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError):
        return lines
    for t in reversed(tokens):
        if t.type == tokenize.COMMENT and t.start[0] <= len(lines):
            row, col = t.start
            lines[row - 1] = lines[row - 1][:col]
    return lines


def normalized(text: str, tree: ast.AST | None) -> list[tuple[int, str]]:
    """(line number, stripped text) of the lines that count for a duplicate."""
    skip = skipped_lines(tree)
    out = []
    for i, line in enumerate(without_comments(text), start=1):
        s = line.strip()
        if s and i not in skip and not set(s) <= BRACKETS:
            out.append((i, s))
    return out


def windows(norm: dict[str, list[tuple[int, str]]]) -> dict[str, list]:
    """{hash of MIN_LINES lines: [(path, index), ...]}"""
    out: dict[str, list] = {}
    for rel, lines in norm.items():
        for i in range(len(lines) - MIN_LINES + 1):
            text = "\n".join(s for _, s in lines[i : i + MIN_LINES])
            key = hashlib.sha1(text.encode()).hexdigest()
            out.setdefault(key, []).append((rel, i))
    return out


def pairs(found: dict[str, list]) -> set[tuple]:
    """(path a, index a, path b, index b) for every two windows alike that do not
    overlap."""
    out = set()
    for places in found.values():
        if len(places) < 2 or len(places) > MOST_COPIES:
            continue
        for x in range(len(places)):
            for y in range(x + 1, len(places)):
                (fa, ia), (fb, ib) = places[x], places[y]
                if fa == fb and abs(ia - ib) < MIN_LINES:
                    continue
                out.add((fa, ia, fb, ib))
    return out


def duplicated_blocks(files: Sources, parsed: dict[str, ast.Module]) -> list[dict]:
    """[{key, lines, where: [path:first-last, ...]}], longest first: each longest run
    of lines alike in two or more places."""
    norm = {rel: normalized(text, parsed.get(rel)) for rel, text in files.items()}
    alike = pairs(windows(norm))
    blocks: dict[str, dict] = {}
    for fa, ia, fb, ib in alike:
        if (fa, ia - 1, fb, ib - 1) in alike:
            continue  # not the start of its run
        n = 1
        while (fa, ia + n, fb, ib + n) in alike:
            n += 1
        length = n + MIN_LINES - 1
        text = "\n".join(s for _, s in norm[fa][ia : ia + length])
        key = hashlib.sha1(text.encode()).hexdigest()
        entry = blocks.setdefault(key, {"lines": length, "where": set()})
        for rel, i in ((fa, ia), (fb, ib)):
            first, last = norm[rel][i][0], norm[rel][i + length - 1][0]
            entry["where"].add(f"{rel}:{first}-{last}")
    out = [
        {"key": k, "lines": b["lines"], "where": sorted(b["where"])}
        for k, b in blocks.items()
    ]
    return sorted(out, key=lambda b: (-b["lines"], b["where"]))


# -- the report ------------------------------------------------------------------------


def build(files: Sources) -> dict:
    parsed = trees(files)
    return {
        "files": len(files),
        "names": names_defined_twice(parsed),
        "blocks": duplicated_blocks(files, parsed),
    }


def files_of(where: list[str]) -> set[str]:
    return {w.rsplit(":", 1)[0] for w in where}


def added(head: dict, base: dict) -> dict[str, list]:
    """What ``head`` has that ``base`` does not: a name defined in a file it was not
    defined in before (only those places, with how many files had it already), and
    a block whose text is new or has more copies than before."""
    before = {n["name"]: files_of(n["where"]) for n in base["names"]}
    names = []
    for n in head["names"]:
        old = before.get(n["name"], set())
        new = [w for w in n["where"] if w.rsplit(":", 1)[0] not in old]
        if new and old:
            names.append({"name": n["name"], "where": new, "before": len(old)})
        elif new:
            names.append(n)
    old_blocks = {(b["key"], len(b["where"])) for b in base["blocks"]}
    return {
        "names": names,
        "blocks": [
            b for b in head["blocks"] if (b["key"], len(b["where"])) not in old_blocks
        ],
    }


def counts_line(report: dict) -> str:
    return (
        f"{report['files']} files read: {len(report['names'])} names defined in more "
        f"than one file, {len(report['blocks'])} duplicated blocks of {MIN_LINES} "
        "lines or more"
    )


def entry_lines(new: dict[str, list], indent: str = "  ") -> list[str]:
    out = [
        f"{indent}name {n['name']}: {', '.join(n['where'])}"
        + (f" (already in {n['before']} files)" if n.get("before") else "")
        for n in new["names"]
    ]
    out += [
        f"{indent}block of {b['lines']} lines: {', '.join(b['where'])}"
        for b in new["blocks"]
    ]
    return out


def summary(head: dict, new: dict | None, note: str) -> str:
    """The job summary: the counts, and what the pull request added."""
    out = ["### reuse", "", f"{counts_line(head)}. The report informs and never fails."]
    if note:
        out += ["", note]
    if new is not None and not any(new.values()):
        out += ["", "This pull request adds nothing to either list."]
    elif new is not None:
        lines = entry_lines(new, "- ")
        out += ["", "What this pull request adds:", "", *lines[:MOST_LISTED]]
        if len(lines) > MOST_LISTED:
            out.append(f"- and {len(lines) - MOST_LISTED} more (the log lists them)")
    return "\n".join(out) + "\n"


def log_lines(head: dict, new: dict | None, note: str) -> list[str]:
    """The log: the counts and both lists, then what the change added."""
    out = [f"reuse: {counts_line(head)}", *entry_lines(head)]
    if new is not None and any(new.values()):
        out += [f"reuse: {note} This change adds:", *entry_lines(new)]
    elif new is not None:
        out.append(f"reuse: {note} This change adds nothing to either list.")
    elif note:
        out.append(f"reuse: {note}")
    return [*out, "reuse: informs, never fails"]


def compare(root: Path, head: dict, base: str, paths: tuple) -> tuple[dict | None, str]:
    """What the head added to the base's lists, and a note for the summary."""
    try:
        before = build(base_sources(root, base, paths))
    except (OSError, ValueError) as error:
        return (
            None,
            f"The base {base[:12]} could not be read ({error}): nothing to compare.",
        )
    return added(head, before), f"Compared with the base, {base[:12]}."


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--paths", nargs="+", default=[], help="only these directories")
    ap.add_argument("--base", help="the commit a pull request is compared with")
    ap.add_argument("--summary", help="append the Markdown summary to this file")
    a = ap.parse_args(argv)
    root = Path.cwd()
    paths = tuple(a.paths)
    try:
        head = build(head_sources(root, paths))
        new, note = compare(root, head, a.base, paths) if a.base else (None, "")
    except Exception as error:  # it informs: a fault of its own is said, never fatal
        print(f"reuse: the report did not run ({type(error).__name__}: {error})")
        return 0
    print("\n".join(log_lines(head, new, note)), flush=True)
    if a.summary:
        with open(a.summary, "a", encoding="utf-8") as out:
            out.write(summary(head, new, note))
    return 0


if __name__ == "__main__":
    sys.exit(main())
