#!/usr/bin/env python3
"""Code rules: every file a pull request changes must be clean.

    python3 code_rules.py                     # the files this branch changes
    python3 code_rules.py --all               # every file; exit 1 on a problem
    python3 code_rules.py --all --report-only # what is left; always exit 0
    python3 code_rules.py --format-only       # prove a branch only re-formats

What a clean file is (standard settings; a project changes them only with a reason, in
its own ruff config):

* **ruff** with the project's own configuration (pyproject.toml / ruff.toml; ruff's
  defaults otherwise: 88 columns), plus pycodestyle and pyflakes (E, W, F) and three
  complexity limits added on top: McCabe complexity 15 (C901), 15 branches (PLR0912)
  and 60 statements (PLR0915) per function. No finding.
* **format**: a Python file passes ``ruff format --check`` (``--no-format`` leaves
  this out, for a project that has not re-formatted yet).
* **size**: a tracked source file (.py .js .ts .tsx .sh) is at most 800 lines, a test
  at most 1200.

The rule: **every file a pull request changes leaves clean**. There is no baseline. A
finding can only appear in a file somebody changes, and a changed file must be clean,
so nothing gets worse and no file is shared between pull requests. A project adopts
the standard with the debt it has and pays it down one changed file at a time. The
last line of every run counts what is left in the repository; ``--all`` lists it.
When it reads zero, the project checks every file on every run (``--all``).

What "changed" means: the files the branch adds or changes against ``origin/main``, or
against the ref or commit in ``CODE_RULES_BASE`` (the python-lint workflow sets it). A
renamed file is a changed file; a deleted one is not checked. When the base cannot be
found, a CI run fails and says how to fetch it (without a base it would check
nothing); outside CI it is a note.

``--format-only`` is for a branch that re-formats files and does nothing else. Such a
branch changes files that may still carry findings, so the rule above would refuse it.
It is held to a stricter proof instead: every file it changes is a Python file that
existed before, passes ``ruff format --check``, and has the same syntax tree as
before. No code changed, so no behaviour can have. Two things a formatter may rewrite
are compared by what they mean: a docstring by its text without its indentation, and
``del (a, b)`` as ``del a, b``.

Standard library only, apart from ruff.
"""

from __future__ import annotations

import argparse
import ast
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

SUFFIXES = (".py", ".js", ".ts", ".tsx", ".sh")
COMPLEXITY = ("C901", "PLR0912", "PLR0915")
STANDARD = [
    "--extend-select",
    "E,W,F," + ",".join(COMPLEXITY),
    "--config",
    "lint.mccabe.max-complexity=15",
    "--config",
    "lint.pylint.max-branches=15",
    "--config",
    "lint.pylint.max-statements=60",
]
FUNC_RE = re.compile(r"`(\w+)`")
PARSE_RE = re.compile(r"Failed to parse (.+?):\d+:\d+:")
BASE_ENV = "CODE_RULES_BASE"
DEFAULT_BASE = "origin/main"
OLD_BASELINE = "code_rules_baseline.json"


def is_test(rel: str) -> bool:
    name = Path(rel).name
    return (
        "/test/" in f"/{rel}"
        or "/tests/" in f"/{rel}"
        or name.startswith("test_")
        or ".test." in name
    )


def limit_for(rel: str, max_lines: int = 800, max_test_lines: int = 1200) -> int:
    return max_test_lines if is_test(rel) else max_lines


def git_cmd(root: Path) -> list[str]:
    """git, trusting the checkout (a container may run as another user)."""
    return ["git", "-c", f"safe.directory={root.resolve()}"]


def git_out(root: Path, *args: str) -> str | None:
    """git's output, or None when git refuses."""
    try:
        res = subprocess.run(
            [*git_cmd(root), *args], cwd=root, capture_output=True, text=True
        )
    except OSError:
        return None
    return res.stdout if res.returncode == 0 else None


def tracked_files(root: Path) -> list[str]:
    """Every tracked file; every file when there is no git (a bare source tree)."""
    out = git_out(root, "ls-files", "-z")
    if out is not None:
        return [f for f in out.split("\0") if f]
    return [
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and ".git" not in p.parts
    ]


def tracked_sources(root: Path) -> list[str]:
    return sorted(
        f
        for f in tracked_files(root)
        if f.endswith(SUFFIXES) and "/vendor/" not in f and "node_modules" not in f
    )


def file_sizes(
    files: list[str], root: Path, max_lines: int, max_test_lines: int
) -> dict[str, int]:
    """Every source file over its limit, with its line count."""
    out = {}
    for rel in files:
        path = root / rel
        if path.is_file():
            n = sum(1 for _ in path.open(errors="replace"))
            if n > limit_for(rel, max_lines, max_test_lines):
                out[rel] = n
    return out


def ruff_cmd() -> list[str]:
    exe = shutil.which("ruff")
    return [exe] if exe else [sys.executable, "-m", "ruff"]


def ruff_counts(root: Path, keep: set[str] | None = None) -> Counter:
    """ruff's findings as {"file::rule" or "file::rule::function": count}.

    Only the files in ``keep`` count when it is given: the tracked files, so that a
    checkout of the standards' tools beside the project is not counted as the project.
    """
    res = subprocess.run(
        [
            *ruff_cmd(),
            "check",
            "--no-cache",
            "--output-format",
            "json",
            "--exit-zero",
            *STANDARD,
            ".",
        ],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise SystemExit(
            f"code_rules: ruff did not run ({res.stderr.strip()[:300]}); "
            "pip install ruff"
        )
    counts: Counter = Counter()
    for f in json.loads(res.stdout or "[]"):
        rel = Path(f["filename"]).resolve().relative_to(root.resolve()).as_posix()
        if keep is not None and rel not in keep:
            continue
        code = f.get("code") or "syntax"
        key = f"{rel}::{code}"
        if code in COMPLEXITY:
            m = FUNC_RE.search(f.get("message") or "")
            key += f"::{m.group(1) if m else '?'}"
        counts[key] += 1
    return counts


def unformatted(files: list[str], root: Path) -> list[str]:
    """The Python files among ``files`` that fail ``ruff format --check``.

    The project's own ruff configuration applies, with its exclusions. A file that
    does not parse cannot be formatted, so it fails too.
    """
    files = [f for f in files if f.endswith(".py")]
    found: list[str] = []
    for i in range(0, len(files), 200):
        res = subprocess.run(
            [
                *ruff_cmd(),
                "format",
                "--check",
                "--no-cache",
                "--force-exclude",
                *files[i : i + 200],
            ],
            cwd=root,
            capture_output=True,
            text=True,
        )
        broken = PARSE_RE.findall(res.stderr)
        if res.returncode not in (0, 1) and not broken:
            raise SystemExit(
                f"code_rules: ruff format did not run ({res.stderr.strip()[:300]}); "
                "pip install ruff"
            )
        found += [Path(p).as_posix() for p in broken]
        for line in res.stdout.splitlines():
            if line.startswith("Would reformat: "):
                found.append(Path(line.split(": ", 1)[1]).as_posix())
    return sorted(set(found))


def in_ci() -> bool:
    """Whether this is a CI run (GitHub Actions sets both)."""
    return bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))


def base_ref() -> str:
    return os.environ.get(BASE_ENV) or DEFAULT_BASE


def fetch_hint(base: str) -> str:
    """The command that fetches the base into a shallow checkout."""
    fetch = "git fetch --no-tags --depth=1 origin"
    if base.startswith("origin/"):
        return f"{fetch} +refs/heads/{base.removeprefix('origin/')}:refs/remotes/{base}"
    return f"{fetch} {base}"


def missing_base(what: str) -> str:
    base = base_ref()
    return (
        f"base: {what}: `{base}` is not available. Fetch it (`{fetch_hint(base)}`) "
        f"or set {BASE_ENV} to a commit that is present"
    )


def changed_files(root: Path) -> tuple[list[str] | None, str | None]:
    """Files the branch adds or changes against the base; None and why if unknown."""
    base = base_ref()
    note = (
        "code_rules: note: skipped the changed-file checks because "
        f"`{base}` is not available"
    )
    if git_out(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}") is None:
        return None, note
    # --no-renames: a renamed file is an added file, so it is checked;
    # --relative: paths from here, as `git ls-files` gives them
    args = ("-z", "--name-only", "--no-renames", "--relative", "--diff-filter=AM")
    out = None
    # the working tree against the merge base, so that a local run sees what is not
    # committed yet; a shallow CI checkout has no merge base: the base itself then
    for spec in (("--merge-base", base), (base,)):
        out = git_out(root, "diff", *args, *spec)
        if out is not None:
            break
    if out is None:
        return None, note
    return sorted(f for f in out.split("\0") if f and (root / f).is_file()), None


def findings_by_file(ruff: dict) -> dict[str, Counter]:
    """ruff's findings as {file: {rule: count}}."""
    out: dict[str, Counter] = {}
    for key, n in ruff.items():
        rel, rule = key.split("::")[:2]
        out.setdefault(rel, Counter())[rule] += n
    return out


def detail(rules: Counter) -> str:
    return ", ".join(f"{rule} {n}" for rule, n in sorted(rules.items()))


def touched_problems(files: list[str], now: dict) -> list[str]:
    """A file the branch changes leaves clean: formatted, no finding, not too long."""
    touched = set(files)
    out = [
        f"ruff-format: {rel}: fails `ruff format --check`: run `ruff format` on it "
        "(a format-only commit first keeps the change readable)"
        for rel in now["unformatted"] or ()
        if rel in touched
    ]
    for rel, rules in sorted(findings_by_file(now["ruff"]).items()):
        if rel in touched:
            out.append(
                f"changed: {rel}: {sum(rules.values())} ruff finding(s) "
                f"({detail(rules)}); a file this pull request changes leaves clean"
            )
    for rel, lines in sorted(now["lines"].items()):
        if rel in touched:
            out.append(
                f"changed: {rel}: {lines} lines, over its limit "
                f"{limit_for(rel, *now['limits'])}; split it in this pull request"
            )
    return out


def all_problems(now: dict) -> list[str]:
    """Every file that is not clean, one line for each reason."""
    out = [
        f"all: {rel}: {sum(rules.values())} ruff finding(s) ({detail(rules)})"
        for rel, rules in sorted(findings_by_file(now["ruff"]).items())
    ]
    out += [
        f"all: {rel}: {lines} lines, over its limit {limit_for(rel, *now['limits'])}"
        for rel, lines in sorted(now["lines"].items())
    ]
    out += [
        f"all: {rel}: fails `ruff format --check`" for rel in now["unformatted"] or ()
    ]
    return out


def changed_problems(now: dict, root: Path) -> tuple[list[str], list[str]]:
    """(problems, notes) of the files this branch changes."""
    changed, note = changed_files(root)
    if changed is None:
        if not in_ci():
            return [], [note]
        return [missing_base("cannot tell what this branch changed")], []
    notes = [f"code_rules: {len(changed)} changed file(s) checked"]
    return touched_problems(changed, now), notes


def syntax_tree(source: str) -> str:
    """The syntax tree of a Python file, as text, without what a formatter may change.

    A docstring is compared by its text without its indentation and trailing blanks,
    and ``del (a, b)`` is the same statement as ``del a, b``.
    """
    tree = ast.parse(source)
    scopes = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if isinstance(node, scopes) and node.body:
            first = node.body[0]
            value = getattr(first, "value", None)
            if isinstance(first, ast.Expr) and isinstance(value, ast.Constant):
                if isinstance(value.value, str):
                    lines = inspect.cleandoc(value.value).splitlines()
                    value.value = "\n".join(line.rstrip() for line in lines).strip()
        if isinstance(node, ast.Delete):
            flat: list[ast.expr] = []
            for target in node.targets:
                flat.extend(target.elts if isinstance(target, ast.Tuple) else [target])
            node.targets = flat
    return ast.dump(tree)


def compared_commit(root: Path) -> str | None:
    """What HEAD is compared with: its merge base with the base, or the base itself."""
    base = base_ref()
    commit = git_out(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")
    if commit is None:
        return None
    # a shallow CI checkout has no merge base: compare with the base's tree instead
    merge_base = git_out(root, "merge-base", base, "HEAD")
    return (merge_base or commit).strip()


def same_code(root: Path, commit: str, rel: str) -> str | None:
    """Why this file is more than a re-format of what it was, or None when it is not."""
    old = git_out(root, "show", f"{commit}:./{rel}")
    new = git_out(root, "show", f"HEAD:./{rel}")
    if old is None or new is None:
        return "its content cannot be read from git"
    try:
        before, after = syntax_tree(old), syntax_tree(new)
    except SyntaxError as e:
        return f"it does not parse ({e.msg}, line {e.lineno})"
    if before != after:
        return "its code changed (the syntax tree is not the one it had)"
    return None


def format_only_problems(root: Path) -> tuple[list[str], list[str]]:
    """(problems, notes): this branch re-formats Python files and does nothing else."""
    commit = compared_commit(root)
    diff = commit and git_out(
        root,
        "diff",
        "-z",
        "--name-status",
        "--no-renames",
        "--relative",
        commit,
        "HEAD",
    )
    if diff is None:
        return [missing_base("cannot prove a format-only change")], []
    what = {"A": "is added", "D": "is deleted"}
    problems, python = [], []
    fields = diff.split("\0")
    for status, rel in zip(fields[0::2], fields[1::2]):
        if status != "M":
            did = what.get(status, f"has status {status}")
            problems.append(
                f"format-only: {rel}: {did}; a format-only pull request changes "
                "only the layout of files that were there"
            )
        elif not rel.endswith(".py"):
            problems.append(
                f"format-only: {rel}: is not a Python file; a format-only pull "
                "request changes nothing else"
            )
        else:
            why = same_code(root, commit, rel)
            if why:
                problems.append(f"format-only: {rel}: {why}")
            else:  # it parses: ruff can say whether it is formatted
                python.append(rel)
    problems += [
        f"ruff-format: {rel}: fails `ruff format --check`: run `ruff format` on it"
        for rel in unformatted(python, root)
    ]
    return problems, [
        f"code_rules: format-only: {len(python)} re-formatted file(s), each with "
        "the syntax tree it had"
    ]


def measure(root: Path, a: argparse.Namespace) -> dict:
    """What is in the repository now: findings, large files, unformatted files."""
    files = tracked_sources(root)
    return {
        "ruff": dict(ruff_counts(root, set(tracked_files(root)))),
        "lines": file_sizes(files, root, a.max_lines, a.max_test_lines),
        "limits": (a.max_lines, a.max_test_lines),
        "unformatted": None if a.no_format else unformatted(files, root),
    }


def summary(now: dict, problems: list[str], report_only: bool) -> str:
    """The last line: the verdict, and what is left in the repository."""
    verdict = f"FAILED, {len(problems)} problem(s)" if problems else "OK"
    if problems and report_only:
        verdict = f"{len(problems)} problem(s), reported only"
    left = [
        f"{sum(now['ruff'].values())} finding(s)",
        f"{len(now['lines'])} file(s) over the size limit",
    ]
    if now["unformatted"] is not None:
        left.append(f"{len(now['unformatted'])} file(s) not formatted")
    return f"code_rules: {verdict} (left in the repository: {', '.join(left)})"


def parse_args(argv) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--root",
        default=".",
        help="the repository to check (default: the current directory)",
    )
    ap.add_argument("--max-lines", type=int, default=800)
    ap.add_argument("--max-test-lines", type=int, default=1200)
    ap.add_argument(
        "--all",
        action="store_true",
        help="check every file, not only the files this branch changes",
    )
    ap.add_argument(
        "--report-only", action="store_true", help="print the problems and exit 0"
    )
    ap.add_argument(
        "--format-only",
        action="store_true",
        help="prove that this branch only re-formats Python files",
    )
    ap.add_argument(
        "--no-format",
        action="store_true",
        help="leave `ruff format --check` out (a project not re-formatted yet)",
    )
    a = ap.parse_args(argv)
    if a.all and a.format_only:
        ap.error("--all and --format-only are two different checks")
    if a.format_only:
        a.no_format = False  # the proof needs the format check
    return a


def main(argv=None) -> int:
    a = parse_args(argv)
    root = Path(a.root).resolve()
    now = measure(root, a)
    notes = []
    if (root / OLD_BASELINE).exists():
        notes.append(
            f"code_rules: note: {OLD_BASELINE} is no longer read (every file a pull "
            "request changes must be clean instead); delete it"
        )
    if a.all:
        problems = all_problems(now)
    elif a.format_only:
        problems, more = format_only_problems(root)
        notes += more
    else:
        problems, more = changed_problems(now, root)
        notes += more
    for line in (*problems, *notes):
        print(line)
    print(summary(now, problems, a.report_only))
    return 1 if problems and not a.report_only else 0


if __name__ == "__main__":
    sys.exit(main())
