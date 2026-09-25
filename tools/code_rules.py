#!/usr/bin/env python3
"""Code rules with a ratchet: ruff, complexity limits and a file-size limit.

What is already in the code is a baseline that may only go down.

    python3 code_rules.py                    # check the repository in the current
    directory; exit 1 on a breach
    python3 code_rules.py --update           # write the first baseline, or lower it
    after a clean-up

What it checks (standard settings; a project changes them only with a reason, in its own
ruff config):

* **ruff** with the project's own configuration (pyproject.toml / ruff.toml; ruff's
  defaults otherwise: 88 columns),
  plus pycodestyle and pyflakes (E, W, F) and three complexity limits added on top:
  McCabe complexity 15
  (C901), 15 branches (PLR0912) and 60 statements (PLR0915) per function.
* **file size**: a tracked source file (.py .js .ts .tsx .sh) over 800 lines, a test
  over 1200, fails.

The ratchet: ``code_rules_baseline.json`` (at the repository root) holds today's
findings per file and rule (per
function for complexity) and each large file's line count, as ceilings. Above a ceiling,
or anything new, fails. Below
a ceiling also fails until ``--update`` lowers the baseline in the same pull request, so
it only ever goes down and a
file that shrank cannot grow back. ``--update`` never takes in a new finding. Standard
library only, apart from ruff.
"""

from __future__ import annotations

import argparse
import json
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


def is_test(rel: str) -> bool:
    name = Path(rel).name
    return (
        "/test/" in f"/{rel}"
        or "/tests/" in f"/{rel}"
        or name.startswith("test_")
        or ".test." in name
    )


def tracked_sources(root: Path) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        out = [
            p.relative_to(root).as_posix()
            for p in root.rglob("*")
            if p.is_file() and ".git" not in p.parts
        ]
    return sorted(
        f
        for f in out
        if f.endswith(SUFFIXES) and "/vendor/" not in f and "node_modules" not in f
    )


def file_sizes(
    files: list[str], root: Path, max_lines: int, max_test_lines: int
) -> dict[str, int]:
    out = {}
    for rel in files:
        path = root / rel
        if path.is_file():
            n = sum(1 for _ in path.open(errors="replace"))
            if n > (max_test_lines if is_test(rel) else max_lines):
                out[rel] = n
    return out


def ruff_counts(root: Path) -> Counter:
    exe = shutil.which("ruff")
    cmd = [exe] if exe else [sys.executable, "-m", "ruff"]
    res = subprocess.run(
        [
            *cmd,
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
        code = f.get("code") or "syntax"
        key = f"{rel}::{code}"
        if code in COMPLEXITY:
            m = FUNC_RE.search(f.get("message") or "")
            key += f'::{m.group(1) if m else "?"}'
        counts[key] += 1
    return counts


def compare(kind: str, now: dict, base: dict) -> list[str]:
    out = []
    for key in sorted(set(now) | set(base)):
        n, ceiling = now.get(key, 0), base.get(key)
        if ceiling is None:
            out.append(
                f"{kind}: {key}: {n} (new: fix it; the baseline only takes "
                "what was there when it was made)"
            )
        elif n > ceiling:
            out.append(f"{kind}: {key}: {n}, above its ceiling {ceiling}")
        elif n < ceiling:
            out.append(
                f"{kind}: {key}: {n}, below its ceiling {ceiling}: good; "
                "lower the baseline with "
                f"`code_rules.py --update` in this pull request"
            )
    return out


def lowered(now: dict, base: dict, first: bool) -> dict:
    """The new baseline: today as it is the first time.

    Afterwards it only lowers, and never takes in a new entry.
    """
    return {
        kind: dict(now[kind])
        if first
        else {
            k: min(v, base.get(kind, {})[k])
            for k, v in now[kind].items()
            if k in base.get(kind, {})
        }
        for kind in ("ruff", "lines")
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--root",
        default=".",
        help="the repository to check (default: the current directory)",
    )
    ap.add_argument(
        "--baseline", default="code_rules_baseline.json", help="relative to --root"
    )
    ap.add_argument("--max-lines", type=int, default=800)
    ap.add_argument("--max-test-lines", type=int, default=1200)
    ap.add_argument(
        "--update",
        action="store_true",
        help="write the first baseline, or lower it; never raises one",
    )
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    base_path = root / a.baseline
    files = tracked_sources(root)
    now = {
        "ruff": dict(ruff_counts(root)),
        "lines": file_sizes(files, root, a.max_lines, a.max_test_lines),
    }
    base = (
        json.loads(base_path.read_text())
        if base_path.exists()
        else {"ruff": {}, "lines": {}}
    )
    if a.update:
        new = lowered(now, base, not base_path.exists())
        base_path.write_text(
            json.dumps(
                {
                    "note": "code rules ratchet: ceilings only go down "
                    "(murphy360/standards)",
                    **new,
                },
                indent=1,
                sort_keys=True,
            )
            + "\n"
        )
        print(
            f"code_rules: baseline written "
            f"({sum(len(v) for v in new.values())} entries)"
        )
        return 0
    problems = compare("ruff", now["ruff"], base.get("ruff", {})) + compare(
        "lines", now["lines"], base.get("lines", {})
    )
    for p in problems:
        print(p)
    print(
        "code_rules: "
        + (f"FAILED, {len(problems)} problem(s)" if problems else "OK")
        + f" ({sum(now['ruff'].values())} baseline finding(s) left, "
        f"{len(now['lines'])} file(s) over the size limit)"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
