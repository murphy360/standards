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

It then does the same on a project that pins its own ruff, with ``--project-ruff``:
the release the python-lint fixture pins (``tests/fixtures/python-project``), and one
more file, laid out as that release formats it. The default ruff would re-format it.
The hook must say that it runs the pinned release, and must not report the file.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "code_rules.py"
FIXTURE = ROOT / "tests" / "fixtures" / "python-project"
PINNED = "requirements-dev.txt"
# An assert as ruff lays it out since its 2025 style: the message in parentheses. The
# standard's default ruff is older and puts the condition in parentheses instead.
NEW_STYLE = (
    "def check(name):\n"
    '    assert name.strip() and not name.startswith(" ") and not '
    'name.endswith(" "), (\n'
    '        "a name has no space around it"\n'
    "    )\n"
)
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
        "tangled.py": "def tangled(value):\n    count = 0\n"
        f"{branches}    return count\n",
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


def hook_config(repo: Path, rev: str, args: list[str] | None = None) -> str:
    """A project's pre-commit config naming the code-rules hook at one commit."""
    hooks = "    hooks:\n      - id: code-rules\n"
    if args:
        hooks += f"        args: [{', '.join(args)}]\n"
    return f"repos:\n  - repo: {repo}\n    rev: {rev}\n{hooks}"


def fixture_pin() -> str:
    """The ruff release the python-lint fixture pins: newer than the default."""
    lines = (FIXTURE / PINNED).read_text().splitlines()
    return next(ln for ln in lines if ln.startswith("ruff==")).removeprefix("ruff==")


def compare(
    rev: str,
    args: list[str],
    extra: dict[str, str],
    expected: tuple[str, ...],
    absent: str = "",
) -> list[str]:
    """Run the hook at ``rev`` and the tool on the scratch project: what differs."""
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "project"
        config = hook_config(ROOT, rev, args)
        write_project(project, {".pre-commit-config.yaml": config, **extra})
        run = {"cwd": project, "capture_output": True, "text": True}
        hook = subprocess.run(["pre-commit", "run", "--all-files"], **run)
        tool = subprocess.run([sys.executable, str(TOOL), *args], **run)
    print(f"$ pre-commit run --all-files   # the hook at {rev}: {' '.join(args)}")
    print(hook.stdout + hook.stderr)
    problems = []
    if hook.returncode != 1 or tool.returncode != 1:
        problems.append(
            f"the hook exited {hook.returncode} and the tool {tool.returncode}; "
            "both fail this project with 1"
        )
    if tool.stdout.strip() not in hook.stdout:
        problems.append(
            f"the hook did not print what `tools/code_rules.py {' '.join(args)}` "
            "prints:\n" + tool.stdout
        )
    problems += [
        f"the hook did not print: {line}"
        for line in expected
        if line not in hook.stdout
    ]
    if absent and absent in hook.stdout:
        problems.append(f"the hook reported {absent}: it did not run the pinned ruff")
    return problems


def main() -> int:
    rev = git(ROOT, "rev-parse", "HEAD")
    problems = compare(rev, ["--all"], {}, EXPECTED)
    pin = fixture_pin()
    pinned = {PINNED: f"ruff=={pin}\n", "new_style.py": NEW_STYLE}
    said = f"code_rules: ruff {pin} (the project's pin, {PINNED})"
    problems += compare(
        rev, ["--all", "--project-ruff"], pinned, (said,), "new_style.py"
    )
    for problem in problems:
        print(f"hook_project: {problem}")
    print(
        "hook_project: " + (f"FAILED, {len(problems)} problem(s)" if problems else "OK")
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
