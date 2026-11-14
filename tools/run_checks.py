#!/usr/bin/env python3
"""The quick checks of a project in one job: each one runs, and the run fails at the
end. The one step of the ``checks`` action (``actions/checks``).

    python3 run_checks.py                         # the checks CHECKS names, or the default
    python3 run_checks.py shell-lint actionlint   # these two

A check is named after the reusable workflow that runs the same step:

* ``standards-check``: ``steps/standards-check.sh`` (``check_standards.py``);
* ``python-lint``: ``ruff_version.py --install``, then ``steps/code-rules.sh python``;
* ``node-lint``: the same install, ``npm ci``, then ``steps/code-rules.sh node``;
* ``shell-lint``: ``steps/shell-lint.sh`` (shellcheck);
* ``actionlint``: ``steps/actionlint.sh``;
* ``secret-scan``: ``steps/secret-scan.sh`` (gitleaks).

The two lints run in ``WORKING_DIRECTORY``, the others in the root of the checkout,
which is the directory this file is run in. Every other input is a variable the step
script reads itself (each script's header lists its own). This file reads four:
``CHECKS``, ``WORKING_DIRECTORY``, ``SHELLCHECK_VERSION`` and ``ACTIONLINT_VERSION``.

Every check runs, also after one failed, so one run reports every tool's findings.
Inside one check the commands stop at the first that fails: no lint without its
install. Then a line per check says passed or FAILED, and the exit code is the worst
one: the highest. A name that is no check counts as a failed one (exit 2), and the
others still run.

On GitHub each check's output is a group of the log, each failed check is an error
annotation that names its group, and the job summary gets the same table.

Standard library only.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

TOOLS = Path(__file__).resolve().parent
STEPS = TOOLS / "steps"
PARTS = (
    "standards-check",
    "python-lint",
    "node-lint",
    "shell-lint",
    "actionlint",
    "secret-scan",
)
LINTS = ("python-lint", "node-lint")
# The default of the action's `checks` input. fleet.py reads it for a caller that
# passes none, and a test keeps actions/checks/action.yml the same.
DEFAULT = "standards-check python-lint shell-lint actionlint secret-scan"
NOT_A_CHECK = 2
DID_NOT_START = 127

Command = tuple[list[str], str]
Runner = Callable[[list[str], str], int]


def step(script: str, *args: str) -> list[str]:
    """A step script under ``steps/``, run by bash as a workflow runs it."""
    return ["bash", str(STEPS / script), *args]


def commands(part: str, env: dict[str, str]) -> list[Command]:
    """What a check runs, in order: each command with the directory it runs in."""
    lint_dir = env.get("WORKING_DIRECTORY") or "."
    install = [sys.executable, str(TOOLS / "ruff_version.py"), "--install"]
    table: dict[str, list[Command]] = {
        "standards-check": [(step("standards-check.sh"), ".")],
        "python-lint": [
            (install, lint_dir),
            (step("code-rules.sh", "python"), lint_dir),
        ],
        "node-lint": [
            (install, lint_dir),
            (["npm", "ci"], lint_dir),
            (step("code-rules.sh", "node"), lint_dir),
        ],
        "shell-lint": [(step("shell-lint.sh", env.get("SHELLCHECK_VERSION", "")), ".")],
        "actionlint": [(step("actionlint.sh", env.get("ACTIONLINT_VERSION", "")), ".")],
        "secret-scan": [(step("secret-scan.sh"), ".")],
    }
    return table[part]


def run_command(argv: list[str], cwd: str) -> int:
    """One command, its output going where this file's goes. The exit code; a
    command that a signal ended counts as 128 plus the signal, as a shell says it."""
    sys.stdout.flush()
    try:
        code = subprocess.run(argv, cwd=cwd).returncode
    except OSError as error:
        print(f"checks: `{argv[0]}` did not start in {cwd} ({error})", flush=True)
        return DID_NOT_START
    return code if code >= 0 else 128 - code


def run_part(part: str, env: dict[str, str], run: Runner) -> int:
    """A check's commands, until one fails. The first exit code that is not 0."""
    for argv, cwd in commands(part, env):
        code = run(argv, cwd)
        if code != 0:
            return code
    return 0


def named(given: list[str], env: dict[str, str]) -> list[str]:
    """The checks to run: the arguments, or CHECKS, or the default. Each once, in
    the order given. Commas and new lines separate names as spaces do."""
    text = " ".join(given).strip() or env.get("CHECKS", "").strip() or DEFAULT
    out: list[str] = []
    for name in text.replace(",", " ").split():
        if name not in out:
            out.append(name)
    return out


def run_all(
    parts: list[str],
    env: dict[str, str],
    run: Runner = run_command,
    on_github: bool = False,
    clock: Callable[[], float] = time.monotonic,
) -> list[dict]:
    """Every check, whatever the one before it did. A result per check: its name,
    its exit code and the seconds it took."""
    results = []
    for part in parts:
        print(f"::group::checks: {part}" if on_github else f"checks: {part}")
        sys.stdout.flush()
        start = clock()
        if part in PARTS:
            code = run_part(part, env, run)
        else:
            print(f"`{part}` is not a check. The checks: {', '.join(PARTS)}.")
            code = NOT_A_CHECK
        results.append({"part": part, "code": code, "seconds": clock() - start})
        if on_github:
            print("::endgroup::")
        sys.stdout.flush()
    return results


def worst(results: list[dict]) -> int:
    """The exit code of the run: the highest of the checks', 0 when all passed."""
    return max((r["code"] for r in results), default=0)


def verdict(result: dict) -> str:
    return "passed" if result["code"] == 0 else f"FAILED (exit {result['code']})"


def summary_lines(results: list[dict]) -> list[str]:
    """A line per check, then the last line: how many failed, and which."""
    out = [f"checks: {r['part']}: {verdict(r)}, {r['seconds']:.0f}s" for r in results]
    failed = [r["part"] for r in results if r["code"] != 0]
    if failed:
        out.append(
            f"checks: FAILED, {len(failed)} of {len(results)}: {', '.join(failed)}"
        )
    else:
        out.append(f"checks: OK, {len(results)} of {len(results)} passed")
    return out


def annotations(results: list[dict]) -> list[str]:
    """An error annotation per failed check, naming the group that holds its lines."""
    return [
        f"::error title=checks::{r['part']} failed (exit {r['code']}). Its lines are "
        f'in the group "checks: {r["part"]}" of this step\'s log.'
        for r in results
        if r["code"] != 0
    ]


def summary_table(results: list[dict]) -> str:
    """The same as Markdown, for the job summary."""
    rows = [
        "### checks",
        "",
        "| check | result | seconds |",
        "|---|---|---|",
    ]
    rows += [f"| {r['part']} | {verdict(r)} | {r['seconds']:.0f} |" for r in results]
    return "\n".join(rows) + "\n"


def main(argv=None, *, run: Runner = run_command, env=None) -> int:
    env = dict(os.environ if env is None else env)
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument(
        "checks",
        nargs="*",
        help=f"the checks to run (default: CHECKS, or `{DEFAULT}`)",
    )
    parts = named(ap.parse_args(argv).checks, env)
    on_github = env.get("GITHUB_ACTIONS") == "true"
    results = run_all(parts, env, run, on_github)
    lines = summary_lines(results) + (annotations(results) if on_github else [])
    print("\n".join(lines), flush=True)
    target = env.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as out:
            out.write(summary_table(results))
    return worst(results)


if __name__ == "__main__":
    sys.exit(main())
