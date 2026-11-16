#!/usr/bin/env python3
"""Code rules: every file is clean.

    python3 code_rules.py                       # every file: what CI runs
    python3 code_rules.py --paths src tools     # only these directories
    python3 code_rules.py --eslint --prettier   # also ESLint and prettier

What a clean file is (standard settings; a project changes them only with a reason, in
its own ruff config):

* **ruff** with the project's own configuration (pyproject.toml / ruff.toml), plus the
  standard's rules added on top: pyflakes (F), ruff's default pycodestyle errors (E4,
  E7, E9), likely bugs (B, without B008), old syntax (UP), simpler code (SIM), import
  order (I), McCabe complexity 15 (C901) and 60 statements per function (PLR0915). No
  finding. The formatter owns the layout: there is no line-length rule.
* **which ruff** (``ruff_version.py``): the release the project pins
  (``ruff==<version>``), or the standard's default when it pins none.
  ``--ruff-version`` runs one release, and ``--no-project-ruff`` the ruff on PATH.
* **no noqa**: ruff runs with ``--ignore-noqa``, so a ``# noqa`` hides nothing, and a
  ``# noqa`` or ``# ruff: noqa`` comment in a Python file is a problem of its own.
  Comments are read with the tokenizer: the words in a string or a docstring pass.
* **silencers** (``code_rules_silencers.py``; ``--no-silencers`` leaves it out): a
  pytest skip or xfail, a type ignore, a ``# pragma: no cover``, a
  ``# shellcheck disable=`` and a ``.skip`` in a JavaScript test each carry a reason,
  ``.only`` is a problem, and the last line counts the silencers left.
* **format**: a Python file passes ``ruff format --check`` (``--no-format`` leaves
  this out, for a project that has not re-formatted yet).
* **size**: a tracked source file (.py .js .ts .tsx .sh) is at most 800 lines, a test
  at most 1200.
* **ESLint** (``--eslint``): the project's ESLint from ``node_modules``, with its
  config, plus complexity 15 and 60 statements. A finding fails like a ruff finding.
  ruff is skipped when no Python file is tracked.
* **Prettier** (``--prettier``): ``prettier --check`` on every file in ``--paths``.
  Python stays with ruff.
* **the config** (``code_rules_config.py``): a project's own config adds rules and
  excludes files, and each Python file ruff does not check is printed. It cannot
  switch a standard rule off: not with a per-file ignore and not with an
  ``eslint-disable`` comment (ESLint runs with ``--no-inline-config``;
  ``--inline-config`` leaves that out).

The rule: **every file is clean**, checked on every run. A breach in any file fails.
There is no baseline, no changed-files mode and no flag that lets a finding pass. Each
finding in a file that fails is printed with its line, its rule and how to fix it, so
the log alone says what to change. The last line counts what the repository holds.

``--paths`` limits every check to some directories or files of the repository (the
default is every tracked file). A project that adopts the standard with debt narrows
it to the files that are clean, with the reason beside it, and widens it as it pays
the debt down.

Standard library only, apart from ruff, and ESLint and Prettier when asked for.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # the module beside this one
import code_rules_config as config
import code_rules_silencers as silencers
import ruff_version

SUFFIXES = (".py", ".js", ".ts", ".tsx", ".sh")
COMPLEXITY, STANDARD = config.COMPLEXITY, config.STANDARD
FUNC_RE = re.compile(r"`(\w+)`")
JS_SUFFIXES = config.JS_SUFFIXES
ESLINT_COMPLEXITY = ("complexity", "max-statements")
ESLINT_RULES = [
    "--rule",
    '{"complexity": ["error", 15]}',
    "--rule",
    '{"max-statements": ["error", 60]}',
]
ESLINT_FUNC_RE = re.compile(r"'([^']+)'")
# No .svg: prettier has no SVG parser, so an icon would stop the whole run.
PRETTIER_SUFFIXES = (*JS_SUFFIXES, ".json", ".md", ".yml", ".yaml", ".css", ".html")
MAX_EXPLAINED = 50
FIXES = {
    "B023": "pass the loop variable in as an argument (`lambda x=x: ...`), or build "
    "the function outside the loop",
    "B904": "raise it `from` the exception caught (`raise New(...) from err`), or "
    "`from None` when the cause says nothing",
    "B905": "add `strict=True` when the iterables must be the same length, "
    "`strict=False` when the shortest may end the loop",
    "F401": "remove the import, or add the name to the module's `__all__` if it is "
    "re-exported",
    "F811": "remove the second definition; a shared pytest fixture goes in a "
    "conftest.py, not in an import",
    "RUF100": "remove the `# noqa`: the standard allows none",
}
SAFE_FIX = "`ruff check --fix` fixes it"
noqa_comments = silencers.noqa_comments  # the first silencer: not allowed at all
RUFF: dict[str, str] = {}  # the release the flags chose (ruff_version.py), set by main


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


def in_paths(rel: str, paths: tuple[str, ...]) -> bool:
    """Whether ``rel`` is one of ``paths`` or inside one; every file when none."""
    if not paths:
        return True
    return any(rel == p or rel.startswith(p.rstrip("/") + "/") for p in paths)


def tracked_sources(root: Path, paths: tuple[str, ...] = ()) -> list[str]:
    return sorted(
        f
        for f in tracked_files(root)
        if f.endswith(SUFFIXES)
        and "/vendor/" not in f
        and "node_modules" not in f
        and in_paths(f, paths)
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
    """The release --ruff-version or the project's pin chose, or the ruff on PATH."""
    return ruff_version.command(RUFF.get("version"))


def ruff_findings(
    root: Path, keep: set[str] | None = None, rules: list[str] = STANDARD
) -> list[dict]:
    """ruff's findings, each with its file, line, column, rule and message.

    Only the files in ``keep`` count when it is given: the tracked files in ``--paths``.
    An untracked file is never the project (an older workflow left the tools there).
    ruff runs with ``--ignore-noqa``: a ``# noqa`` hides nothing.
    """
    res = subprocess.run(
        [
            *ruff_cmd(),
            "check",
            "--no-cache",
            "--output-format",
            "json",
            "--exit-zero",
            "--ignore-noqa",
            *rules,
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
    found = []
    for f in json.loads(res.stdout or "[]"):
        rel = Path(f["filename"]).resolve().relative_to(root.resolve()).as_posix()
        if keep is not None and rel not in keep:
            continue
        place = f.get("location") or {}
        found.append(
            {
                "rel": rel,
                "line": place.get("row", 0),
                "col": place.get("column", 0),
                "code": f.get("code") or "syntax",
                "message": f.get("message") or "",
                "safe_fix": (f.get("fix") or {}).get("applicability") == "safe",
            }
        )
    return found


def count_findings(found: list[dict]) -> Counter:
    """Findings as {"file::rule" or "file::rule::function": count}."""
    counts: Counter = Counter()
    for f in found:
        key = f"{f['rel']}::{f['code']}"
        if f["code"] in COMPLEXITY:
            m = FUNC_RE.search(f["message"])
            key += f"::{m.group(1) if m else '?'}"
        counts[key] += 1
    return counts


def node_bin(root: Path, name: str) -> Path:
    exe = root / "node_modules" / ".bin" / name
    if exe.exists():
        return exe
    raise SystemExit(
        f"code_rules: node_modules/.bin/{name} not found; "
        f"add {name} to devDependencies and run npm ci"
    )


def parse_eslint(output: str, root: Path, keep: set[str] | None = None) -> list[dict]:
    """ESLint JSON errors. A warning is skipped. No rule id means a parse error."""
    try:
        data = json.loads(output.strip() or "[]")
    except json.JSONDecodeError as e:
        raise SystemExit(f"code_rules: eslint did not return JSON ({e})") from e
    found, root_r = [], root.resolve()
    for entry in data:
        try:
            rel = Path(entry["filePath"]).resolve().relative_to(root_r).as_posix()
        except (KeyError, ValueError):
            continue
        if keep is not None and rel not in keep:
            continue
        for msg in entry.get("messages", []):
            rule = msg.get("ruleId") or "syntax"
            if rule != "syntax" and msg.get("severity", 2) < 2:
                continue
            func = None
            if rule in ESLINT_COMPLEXITY:
                hit = ESLINT_FUNC_RE.search(msg.get("message") or "")
                func = hit.group(1) if hit else "?"
            found.append(
                {
                    "rel": rel,
                    "line": msg.get("line") or 0,
                    "col": msg.get("column") or 0,
                    "code": rule,
                    "message": msg.get("message") or "",
                    "tool": "eslint",
                    "func": func,
                }
            )
    return found


def eslint_count(found: list[dict]) -> Counter:
    counts: Counter = Counter()
    for f in found:
        key = f"{f['rel']}::{f['code']}"
        if f["code"] in ESLINT_COMPLEXITY:
            key += f"::{f.get('func') or '?'}"
        counts[key] += 1
    return counts


def eslint_findings(root: Path, keep: set[str] | None = None, extra=()) -> list[dict]:
    res = subprocess.run(
        [str(node_bin(root, "eslint")), "--format", "json", *ESLINT_RULES, *extra, "."],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if res.returncode not in (0, 1):  # 0 clean, 1 findings; else ESLint did not run
        err = (res.stderr or res.stdout).strip()[:300]
        raise SystemExit(f"code_rules: eslint did not run ({err})")
    return parse_eslint(res.stdout, root, keep)


def parse_prettier(stdout: str, stderr: str = "") -> list[str]:
    out = []
    for line in f"{stdout}\n{stderr}".splitlines():
        if line.startswith(("[warn] ", "[error] ")):
            path = line.split(" ", 1)[1].strip()
            if path.startswith(("Code style issues", "No parser")):
                continue
            if line.startswith("[error] "):
                path = path.split(":", 1)[0].strip()
            if " " not in path:
                out.append(Path(path).as_posix())
    return out


def prettier_unformatted(root: Path, paths: tuple[str, ...]) -> list[str]:
    """Files that fail ``prettier --check``. Python stays with ruff."""
    files = sorted(
        f
        for f in tracked_files(root)
        if f.endswith(PRETTIER_SUFFIXES)
        and "node_modules" not in f
        and "/vendor/" not in f
        and in_paths(f, paths)
        and (root / f).is_file()
    )
    if not files:
        return []
    exe = str(node_bin(root, "prettier"))
    found: list[str] = []
    for i in range(0, len(files), 100):
        res = subprocess.run(
            [exe, "--check", "--ignore-unknown", *files[i : i + 100]],
            cwd=root,
            capture_output=True,
            text=True,
        )
        if res.returncode not in (0, 1):
            err = (res.stderr or res.stdout).strip()[:300]
            raise SystemExit(f"code_rules: prettier did not run ({err})")
        found += parse_prettier(res.stdout, res.stderr)
    return sorted(set(found))


def fix_for(code: str, tool: str = "ruff", safe_fix: bool | None = False) -> str:
    """How to fix a finding of this rule, in one sentence."""
    if tool == "eslint":
        if code in ESLINT_COMPLEXITY:
            return (
                "split the function into named helpers, each one step, in the same "
                "order"
            )
        if code == "syntax":
            return "fix the syntax error so the file parses"
        return f"the ESLint rule `{code}` says how to fix it"
    if code in FIXES:
        return FIXES[code]
    if code in COMPLEXITY:
        return "split the function into named helpers, each one step, in the same order"
    if safe_fix:  # UP045, I001 and most of UP and I
        return SAFE_FIX
    return f"`ruff rule {code}` explains the rule and its fix"


def finding_fix(f: dict) -> str:
    return fix_for(f["code"], f.get("tool", "ruff"), f.get("safe_fix"))


def explained(found: list[dict], files: set[str]) -> list[str]:
    """One line per finding in ``files``: where it is, what it is, how to fix it."""
    shown = sorted(
        (f for f in found if f["rel"] in files),
        key=lambda f: (f["rel"], f["line"], f["col"]),
    )
    out = [
        f"  {f['rel']}:{f['line']}:{f['col']}: {f['code']} {f['message']}. "
        f"Fix: {f.get('fix') or finding_fix(f)}"
        for f in shown[:MAX_EXPLAINED]
    ]
    if len(shown) > MAX_EXPLAINED:
        out.append(f"  ... and {len(shown) - MAX_EXPLAINED} more finding(s)")
    return out


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
        broken, ugly = ruff_version.format_check(res.stdout, res.stderr)
        if res.returncode not in (0, 1) and not broken:
            raise SystemExit(
                f"code_rules: ruff format did not run ({res.stderr.strip()[:300]}); "
                "pip install ruff"
            )
        found += broken + ugly
    return sorted(set(found))


def findings_by_file(ruff: dict) -> dict[str, Counter]:
    """ruff's findings as {file: {rule: count}}."""
    out: dict[str, Counter] = {}
    for key, n in ruff.items():
        rel, rule = key.split("::")[:2]
        out.setdefault(rel, Counter())[rule] += n
    return out


def detail(rules: Counter) -> str:
    return ", ".join(f"{rule} {n}" for rule, n in sorted(rules.items()))


def rule_lines(counts: dict, label: str, kind: str = "all") -> list[str]:
    return [
        f"{kind}: {rel}: {sum(rules.values())} {label} finding(s) ({detail(rules)})"
        for rel, rules in sorted(findings_by_file(counts).items())
    ]


def all_problems(now: dict, root: Path) -> list[str]:
    """Every file that is not clean, one line for each reason."""
    out = rule_lines(now["ruff"], "ruff")
    out += rule_lines(now.get("eslint") or {}, "eslint")
    out += [
        f"all: {rel}: {lines} lines, over its limit {limit_for(rel, *now['limits'])}"
        for rel, lines in sorted(now["lines"].items())
    ]
    out += [
        f"all: {rel}: fails `ruff format --check`" for rel in now["unformatted"] or ()
    ]
    out += [
        f"all: {rel}: fails `prettier --check`" for rel in now.get("prettier") or ()
    ]
    return out + now["config"] + noqa_comments(now["files"], root)


def measure(root: Path, a: argparse.Namespace) -> dict:
    """What is in the repository now: findings, large files, unformatted files."""
    paths = tuple(a.paths)
    files = tracked_sources(root, paths)
    keep = {f for f in tracked_files(root) if in_paths(f, paths)}
    py = any(f.endswith(".py") for f in files)
    found = config.unhidden(root, keep, ruff_findings, fix_for) if py else []
    eslint = eslint_findings(root, keep, *config.eslint_extra(a)) if a.eslint else []
    unreasoned, silencer_count = silencers.measure(root, keep, a)
    return {
        "files": files,
        "found": found + eslint,
        "ruff": dict(count_findings(found)),
        "eslint": dict(eslint_count(eslint)),
        "lines": file_sizes(files, root, a.max_lines, a.max_test_lines),
        "limits": (a.max_lines, a.max_test_lines),
        "unformatted": None if a.no_format or not py else unformatted(files, root),
        "prettier": prettier_unformatted(root, paths) if a.prettier else None,
        "config": config.problems(root, keep, a) + unreasoned,
        "silencers": silencer_count,
        "unchecked": config.unchecked(root, files, ruff_cmd()) if py else [],
    }


def summary(now: dict, problems: list[str]) -> str:
    """The last line: the verdict, and what is left in the repository."""
    verdict = f"FAILED, {len(problems)} problem(s)" if problems else "OK"
    findings = sum(now["ruff"].values()) + sum(now.get("eslint", {}).values())
    left = [
        f"{findings} finding(s)",
        f"{len(now['lines'])} file(s) over the size limit",
    ]
    if now["unformatted"] is not None:
        left.append(f"{len(now['unformatted'])} file(s) not formatted")
    if now.get("prettier") is not None:
        left.append(f"{len(now['prettier'])} file(s) failing prettier")
    if now["unchecked"]:
        left.append(f"{len(now['unchecked'])} Python file(s) ruff does not check")
    left += silencers.left(now)
    return f"code_rules: {verdict} (left in the repository: {', '.join(left)})"


def parse_args(argv) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument(
        "--root",
        default=".",
        help="the repository to check (default: the current directory)",
    )
    ap.add_argument("--max-lines", type=int, default=800)
    ap.add_argument("--max-test-lines", type=int, default=1200)
    # v2's "every file": now the only way. Accepted, and ignored, so that a v2 hook's
    # `args: [--all]` keeps working; it goes in v4.
    ap.add_argument("--all", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument(
        "--paths",
        nargs="+",
        default=[],
        metavar="PATH",
        help="check only these directories or files (default: every tracked file)",
    )
    ap.add_argument(
        "--no-format",
        action="store_true",
        help="leave `ruff format --check` out (a project not re-formatted yet)",
    )
    ap.add_argument(
        "--eslint",
        action="store_true",
        help="also run the project's ESLint (JavaScript and TypeScript)",
    )
    ap.add_argument(
        "--prettier",
        action="store_true",
        help="also run prettier --check, on every file in --paths",
    )
    config.add_arguments(ap)  # --inline-config
    silencers.add_arguments(ap)  # --silencers, --no-silencers
    ruff_version.add_arguments(ap)  # --ruff-version, --project-ruff, --no-project-ruff
    return ap.parse_args(argv)


def problem_file(line: str) -> str:
    """The file a problem line names ("kind: path: why" or "noqa: path:line: why")."""
    parts = line.split(": ", 2)
    return parts[1].split(":")[0] if len(parts) > 1 else ""


def main(argv=None) -> int:
    a = parse_args(argv)
    root = Path(a.root).resolve()
    RUFF.clear()
    RUFF.update(ruff_version.chosen(root, a.ruff_version, a.project_ruff))
    now = measure(root, a)
    found = all_problems(now, root)
    reported = {problem_file(line) for line in found}
    for line in (*found, *explained(now["found"], reported), *config.notes(now)):
        print(line)
    print(summary(now, found))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
