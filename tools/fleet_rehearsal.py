"""The rehearsal before a release: the candidate tools against every adopter.

    python3 fleet.py --run-tools v2.3.0

fleet.py finds the adopters; this module does the rest. For each adopter it:

1. takes ``tools/`` of this checkout at the ref (``git archive``, so a tag, a
   branch or a commit all work, and nothing is fetched);
2. clones the adopter into a temporary directory (``gh repo clone``, depth 1);
3. runs the tools the way the adopter's workflows would. A repository that calls
   ``python-lint`` gets ``code_rules.py --all``, with ``--no-format`` unless it
   sets ``format-check: true``, and with its ``paths`` and ``working-directory``.
   A repository that calls ``standards-check`` gets ``check_standards.py``, with
   the opt-in checks it turned on;
4. prints pass or FAIL, and the last line of each tool;
5. deletes the clone. The temporary directory goes when the run ends, however
   it ends.

``debt`` is a third result: ``code_rules.py --all`` failed in a repository that
lints in ``mode: changed``. Its pull requests check only the files they touch, so
a finding elsewhere does not fail them.

``code_rules.py`` needs ruff. Where ruff is not on PATH, the rehearsal stops and
prints the Docker command that runs it (``docker_hint``).

``node-lint`` is not rehearsed: ESLint comes from the project's ``node_modules``.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from pathlib import Path

RUFF = "0.6.9"  # the ruff python-lint.yml pins (a test keeps the two in step)
CHECK_FLAGS = (
    ("check-pr-template", "--require-pr-template"),
    ("check-pre-commit-config", "--require-pre-commit"),
    ("check-ticket-template", "--require-ticket-template"),
)
WORST = ("error", "FAIL", "debt", "pass", "-")


def docker_hint(ref: str) -> str:
    """What to run where ruff is not installed: the same rehearsal in a container."""
    return f"""ruff is not on PATH, and code_rules.py needs it.
Run the rehearsal in Docker, from the root of this repository:

  GH_TOKEN="$(gh auth token)" docker run --rm -e GH_TOKEN \\
    -e DEBIAN_FRONTEND=noninteractive -e PIP_DISABLE_PIP_VERSION_CHECK=1 \\
    -v "$PWD:/standards:ro" -w /standards python:3.12-slim sh -c '
      apt-get -qq update && apt-get -qq install -y git gh >/dev/null &&
      pip install -q --root-user-action=ignore ruff=={RUFF} &&
      git config --global --add safe.directory /standards &&
      python tools/fleet.py --run-tools {ref}'"""


def last_line(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1].strip() if lines else ""


def plain(value: str | None) -> str:
    """A ``with:`` value, or nothing when it is an expression only a run resolves."""
    return value if value and "${{" not in value else ""


def plan(row: dict) -> list[dict]:
    """The tool runs a repository's callers ask for, each once.

    Two callers that ask for the same run share it. The run is in mode changed
    only when every one of them is.
    """
    steps: list[dict] = []
    for call in row["calls"]:
        given = call["with"]
        if call["workflow"] == "python-lint":
            args = ["--all"]
            if given.get("format-check") != "true":
                args.append("--no-format")
            paths = plain(given.get("paths")).split()
            if paths:
                args += ["--paths", *paths]
            step = {
                "tool": "code_rules.py",
                "args": args,
                "cwd": plain(given.get("working-directory")) or ".",
                "changed": given.get("mode") == "changed",
            }
        elif call["workflow"] == "standards-check":
            args = [flag for key, flag in CHECK_FLAGS if given.get(key) == "true"]
            step = {
                "tool": "check_standards.py",
                "args": args,
                "cwd": ".",
                "changed": False,
            }
        else:
            continue
        same = [s for s in steps if step_key(s) == step_key(step)]
        if same:
            same[0]["changed"] = same[0]["changed"] and step["changed"]
        else:
            steps.append(step)
    return steps


def step_key(step: dict) -> tuple:
    return step["tool"], step["args"], step["cwd"]


def tools_at(ref: str, root: Path, dest: Path) -> Path:
    """``tools/`` of the checkout at ``root``, as it is at ``ref``, under ``dest``."""
    cmd = ["git", "-C", str(root), "archive", "--format=tar", ref, "--", "tools"]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"git did not run ({e})")
    if res.returncode != 0:
        why = last_line(res.stderr.decode(errors="replace"))
        raise RuntimeError(
            f"cannot read tools/ at `{ref}` from this checkout ({why}); "
            "is the ref fetched? `git fetch --tags origin`"
        )
    with tarfile.open(fileobj=io.BytesIO(res.stdout)) as tar:
        tar.extractall(dest, filter="data")
    return dest / "tools"


def gh_clone(slug: str, dest: Path) -> str | None:
    """A shallow clone of ``owner/name``. Returns what went wrong, or None."""
    cmd = ["gh", "repo", "clone", slug, str(dest), "--", "--depth", "1", "--quiet"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"the clone did not run ({e})"
    if res.returncode != 0:
        return last_line(res.stderr) or "the clone failed"
    return None


def run_tool(argv: list[str], cwd: Path) -> tuple[int, str]:
    """A tool's exit code and its output."""
    try:
        res = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, f"the tool did not run ({e})"
    return res.returncode, res.stdout or res.stderr


def worst(results: list[str]) -> str:
    return min(results, key=WORST.index) if results else "-"


def rehearse_one(row: dict, slug: str, tools: Path, dest: Path, clone, run) -> dict:
    """Clone one adopter, run its tools, delete the clone."""
    out = {"name": row["name"], "result": "-", "runs": []}
    steps = plan(row)
    if not steps:
        return out
    try:
        error = clone(slug, dest)
        if error:
            out["runs"].append({"tool": "clone", "result": "error", "last": error})
        for step in () if error else steps:
            argv = [sys.executable, str(tools / step["tool"]), *step["args"]]
            code, output = run(argv, dest / step["cwd"])
            failed = "debt" if step["changed"] else "FAIL"
            out["runs"].append(
                {
                    "tool": " ".join([step["tool"], *step["args"]]),
                    "cwd": step["cwd"],
                    "result": "pass" if code == 0 else failed,
                    "last": last_line(output),
                }
            )
    finally:
        shutil.rmtree(dest, ignore_errors=True)
    out["result"] = worst([r["result"] for r in out["runs"]])
    return out


def rehearse(
    rows: list[dict],
    owner: str,
    ref: str,
    root: Path,
    *,
    clone=gh_clone,
    run=run_tool,
    emit=None,
) -> list[dict]:
    """The rehearsal for every row that calls a workflow. ``emit`` gets each
    result as it is ready. The clones never outlive the call."""
    results = []
    with tempfile.TemporaryDirectory(prefix="fleet-") as tmp:
        tools = tools_at(ref, root, Path(tmp) / "standards")
        for row in rows:
            if not row["calls"]:
                continue
            slug = f"{owner}/{row['name']}"
            result = rehearse_one(row, slug, tools, Path(tmp) / "clone", clone, run)
            results.append(result)
            if emit:
                emit(result)
    return results


def result_lines(result: dict) -> list[str]:
    """One repository: its result, then a line per tool with the tool's last line."""
    if not result["runs"]:
        return [f"{result['name']}: - (it calls no workflow that runs these tools)"]
    out = [f"{result['name']}: {result['result']}"]
    for r in result["runs"]:
        where = "" if r.get("cwd", ".") == "." else f" (in {r['cwd']})"
        out.append(f"  {r['result']:<5} {r['tool']}{where}: {r['last']}")
    return out


def closing_lines(results: list[dict], rows: list[dict], ref: str) -> list[str]:
    """The counts under the rehearsal, and what a reader must know to read them."""
    counts = Counter(r["result"] for r in results)
    parts = [f"{counts[k]} {k}" for k in WORST[:-1] if counts[k]]
    if counts["-"]:
        parts.append(f"{counts['-']} with nothing to run")
    out = [f"{len(results)} adopters rehearsed at {ref}: {', '.join(parts) or 'none'}."]
    if counts["debt"]:
        out.append(
            "debt: `code_rules.py --all` fails, but the repository lints in mode "
            "changed, so its pull requests check only the files they touch."
        )
    node = sum(
        any(c["workflow"] == "node-lint" for c in r["calls"])
        for r in rows
        if r["calls"]
    )
    if node:
        out.append(
            f"node-lint is not rehearsed ({node} repositories call it): ESLint "
            "comes from the project's node_modules."
        )
    return out


def main(
    rows: list[dict],
    owner: str,
    ref: str,
    root: Path,
    *,
    as_json: bool = False,
    which=shutil.which,
    clone=gh_clone,
    run=run_tool,
) -> int:
    """Run the rehearsal and print it. 0: no adopter fails. 1: one does. 2: no ruff."""
    if which("ruff") is None:
        print(docker_hint(ref), file=sys.stderr)
        return 2

    def show(result: dict) -> None:
        print("\n".join(result_lines(result)), flush=True)

    emit = None if as_json else show
    results = rehearse(rows, owner, ref, root, clone=clone, run=run, emit=emit)
    closing = closing_lines(results, rows, ref)
    if as_json:
        print(
            json.dumps({"ref": ref, "rehearsal": results, "notes": closing}, indent=2)
        )
    else:
        print()
        print("\n".join(closing))
    return 1 if any(r["result"] in ("error", "FAIL") for r in results) else 0
