#!/usr/bin/env python3
"""A scratch project that breaks three code rules, and the hook run on it for real.

The project holds a function of complexity 16, a ``# noqa`` and a source file of 801
lines. ``tests/test_code_rules_hook.py`` runs the tool on it, without the network.

Run as a script, this file proves that the hook and the tool agree:

    pip install pre-commit ruff==<the pin> && python3 tests/hook_project.py

It builds the ``code-rules`` hook from the commit that is checked out here, as a
project's ``pre-commit run --all-files`` would, and compares what the hook prints with
what ``python3 tools/code_rules.py --all`` prints: the lines CI prints. It needs the
network (pre-commit installs ruff into the hook's environment), so it is a CI job of
this repository (``hook`` in ci.yml) and no pytest test.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "code_rules.py"
FIX = "split the function into named helpers, each one step, in the same order"
EXPECTED = (
    "all: tangled.py: 1 ruff finding(s) (C901 1)",
    "all: long.py: 801 lines, over its limit 800",
    "noqa: hidden.py:1: a `# noqa` is not allowed; remove it and fix the finding it "
    "hid",
    f"  tangled.py:1:5: C901 `tangled` is too complex (16 > 15). Fix: {FIX}",
    "code_rules: FAILED, 3 problem(s) (left in the repository: 1 finding(s), 1 "
    "file(s) over the size limit, 0 file(s) not formatted)",
)


def project_files() -> dict[str, str]:
    """Three files, one rule broken in each, and nothing else wrong with them."""
    branches = "".join(
        f"    if value == {i}:\n        count += {i}\n" for i in range(1, 16)
    )
    return {
        "tangled.py": f"def tangled(value):\n    count = 0\n{branches}    return count\n",
        "hidden.py": "VALUE = 1  # noqa: E501\n",
        "long.py": "".join(f"LINE_{i} = {i}\n" for i in range(801)),
    }


def git(root: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    )
    return res.stdout.strip()


def write_project(root: Path, extra: dict[str, str] | None = None) -> None:
    """The scratch project as a git repository, with every file committed."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in {**project_files(), **(extra or {})}.items():
        (root / rel).write_text(text)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Standards Tests")
    git(root, "config", "user.email", "tests@example.com")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "a project that breaks three rules")


def hook_config(repo: Path, rev: str) -> str:
    """A project's pre-commit config naming the code-rules hook at one commit."""
    return f"repos:\n  - repo: {repo}\n    rev: {rev}\n    hooks:\n      - id: code-rules\n"


def main() -> int:
    rev = git(ROOT, "rev-parse", "HEAD")
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "project"
        write_project(project, {".pre-commit-config.yaml": hook_config(ROOT, rev)})
        run = {"cwd": project, "capture_output": True, "text": True}
        hook = subprocess.run(["pre-commit", "run", "--all-files"], **run)
        tool = subprocess.run([sys.executable, str(TOOL), "--all"], **run)
    print(f"$ pre-commit run --all-files   # the hook at {rev}")
    print(hook.stdout + hook.stderr)
    problems = []
    if hook.returncode != 1 or tool.returncode != 1:
        problems.append(
            f"the hook exited {hook.returncode} and the tool {tool.returncode}; "
            "both fail this project with 1"
        )
    if tool.stdout.strip() not in hook.stdout:
        problems.append(
            "the hook did not print what `tools/code_rules.py --all` prints:\n"
            + tool.stdout
        )
    problems += [
        f"the hook did not print: {line}"
        for line in EXPECTED
        if line not in hook.stdout
    ]
    for problem in problems:
        print(f"hook_project: {problem}")
    print(
        "hook_project: " + (f"FAILED, {len(problems)} problem(s)" if problems else "OK")
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
