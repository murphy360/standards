#!/usr/bin/env python3
"""Type check: a call matches what it calls.

    python3 type_check.py                          # Python: pyright on this project
    python3 type_check.py --language typescript    # the project's own tsc --noEmit
    python3 type_check.py --root path/to/project

A linter checks layout, size and complexity. It does not check that a name, an
argument or an attribute exists. A type checker does, without running a test.

* **Python**: pyright, the one on the PATH (the type-check workflow installs a
  pinned release). It reads the project's own config: ``pyrightconfig.json``, or
  ``[tool.pyright]`` in ``pyproject.toml``. The mode is ``basic`` unless that config
  sets ``typeCheckingMode`` (or extends another config, which then decides). An
  error is a finding. A warning is counted on the last line and fails nothing.
  pyright must see what the code imports, so the project's dependencies are
  installed first. A run that analysed no file fails: it proves nothing.
* **TypeScript**: ``tsc --noEmit`` from the project's ``node_modules``, with the
  project's ``tsconfig.json``. Every error is a finding.

Each finding is printed as ``code_rules.py`` prints one: path, line and column, the
rule, the checker's message and a one-sentence fix. At most 50 are shown. The last
line is the verdict.

There is no baseline and no flag that lets a finding pass. A project with debt
narrows ``include`` in its own pyright config or ``tsconfig.json``, with the reason
beside it, and widens it as it pays the debt down.

pyright has no flag for the mode, so ``basic`` is set through a small config file
that extends the project's own. It is written beside the project's config, because
pyright takes the project's root from where its config is, and removed after the run.

Standard library only, apart from pyright, and the project's tsc.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))  # the module beside this one
import code_rules

BASIC = "basic"
EXTENDS = "extends"  # the project's config extends another, which decides the mode
GENERATED = ".type-check-pyright.json"
JSON_CONFIG, TOML_CONFIG = "pyrightconfig.json", "pyproject.toml"
# pyrightconfig.json may hold comments: a string is kept, a comment is dropped.
JSON_COMMENT_RE = re.compile(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/', re.DOTALL)
MODE_RE = re.compile(r'"typeCheckingMode"\s*:\s*"(\w+)"')
EXTENDS_RE = re.compile(r'"extends"\s*:')
TSC_RE = re.compile(
    r"^(?:(?P<rel>.+?)\((?P<line>\d+),(?P<col>\d+)\): )?"
    r"error (?P<code>TS\d+): (?P<message>.*)$"
)
NONE_FIX = (
    "handle `None` before the access: test for it, or raise where it cannot happen"
)
NAME_FIX = "define or import the name, or correct its spelling"
MEMBER_FIX = (
    "use a name the object has: read its definition, then correct the spelling or "
    "add what is missing"
)
ARGUMENT_FIX = (
    "pass a value of the parameter's type: convert it, or handle the other case "
    "before the call"
)
ASSIGN_FIX = (
    "assign a value of the declared type, or widen the declaration to what it "
    "really holds"
)
CALL_FIX = "call it with the arguments its definition takes: read the definition"
NULL_FIX = "handle `null` and `undefined` before the access"
PYRIGHT_FIXES = {
    "reportAttributeAccessIssue": MEMBER_FIX,
    "reportCallIssue": CALL_FIX,
    "reportArgumentType": ARGUMENT_FIX,
    "reportAssignmentType": ASSIGN_FIX,
    "reportReturnType": "return what the signature promises, or change the signature "
    "and every caller",
    "reportUndefinedVariable": NAME_FIX,
    "reportPossiblyUnbound": "assign the name on every path before it is read",
    "reportMissingImports": "install the package in the job (`install-command`), or "
    "correct the module's name",
    "reportOptionalMemberAccess": NONE_FIX,
    "reportOptionalSubscript": NONE_FIX,
    "reportOptionalIterable": NONE_FIX,
    "reportOptionalCall": NONE_FIX,
    "reportOptionalOperand": NONE_FIX,
    "error": "pyright names no rule for this: fix what the message says",
}
TSC_FIXES = {
    "TS2339": MEMBER_FIX,
    "TS2304": NAME_FIX,
    "TS2552": NAME_FIX,
    "TS2345": ARGUMENT_FIX,
    "TS2322": ASSIGN_FIX,
    "TS2554": CALL_FIX,
    "TS2307": "add the package to the dependencies (and its `@types/` package when "
    "it ships no types), or correct the path",
    "TS2531": NULL_FIX,
    "TS2532": NULL_FIX,
    "TS18047": NULL_FIX,
    "TS18048": NULL_FIX,
    "TS7006": "give the parameter a type",
    "TS5057": "add a tsconfig.json at the project's root; its `include` names the "
    "source directories",
    "TS18003": "make `include` in tsconfig.json name a directory that holds "
    "TypeScript files",
}


def fix_for(code: str, tool: str) -> str:
    """How to fix a finding of this rule, in one sentence."""
    if tool == "tsc":
        return TSC_FIXES.get(
            code, f"make the code and its types agree; `{code}` names the mismatch"
        )
    return PYRIGHT_FIXES.get(
        code,
        f"make the code and its types agree; pyright's configuration page explains "
        f"`{code}`",
    )


def finding(tool: str, rel: str, line: int, col: int, code: str, message: str) -> dict:
    """One finding, in the shape ``code_rules.explained`` prints.

    A checker's message can run over several lines and end with a full stop. The
    first line says what is wrong, and the line it is printed on adds the stop.
    """
    first = (message.strip().splitlines() or [""])[0].rstrip().rstrip(".")
    return {
        "rel": rel,
        "line": line,
        "col": col,
        "code": code,
        "message": first,
        "tool": tool,
        "fix": fix_for(code, tool),
    }


def relative(path: str, root: Path) -> str:
    """A path as the project names it; a file outside the project keeps its own."""
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def parse_pyright(output: str, root: Path) -> tuple[list[dict], dict]:
    """(findings, summary) from ``pyright --outputjson``. Only an error is a finding.

    pyright counts lines and columns from 0; a finding counts them from 1, as an
    editor does.
    """
    try:
        data = json.loads(output.strip() or "{}")
    except json.JSONDecodeError as e:
        raise SystemExit(f"type_check: pyright did not return JSON ({e})") from e
    found = []
    for entry in data.get("generalDiagnostics") or []:
        if entry.get("severity") != "error":
            continue
        start = (entry.get("range") or {}).get("start") or {}
        found.append(
            finding(
                "pyright",
                relative(entry.get("file") or "", root),
                start.get("line", 0) + 1,
                start.get("character", 0) + 1,
                entry.get("rule") or "error",
                entry.get("message") or "",
            )
        )
    summary = dict(data.get("summary") or {})
    summary["version"] = data.get("version") or "?"
    return found, summary


def parse_tsc(output: str) -> list[dict]:
    """The errors of ``tsc --pretty false``, one for each line that names one.

    An indented line under an error explains it further and is not an error of its
    own. An error with no file is about the project's config.
    """
    found = []
    for text in output.splitlines():
        hit = TSC_RE.match(text)
        if not hit:
            continue
        rel = Path(hit["rel"]).as_posix() if hit["rel"] else "tsconfig.json"
        found.append(
            finding(
                "tsc",
                rel,
                int(hit["line"] or 1),
                int(hit["col"] or 1),
                hit["code"],
                hit["message"],
            )
        )
    return found


def toml_config(text: str) -> dict | None:
    """``[tool.pyright]`` of a pyproject.toml; None when it has no such table."""
    if "[tool.pyright" not in text:
        return None
    try:
        import tomllib
    except ModuleNotFoundError as e:
        raise SystemExit(
            f"type_check: reading [tool.pyright] in {TOML_CONFIG} needs Python 3.11 "
            f"or newer; move the config to {JSON_CONFIG}"
        ) from e
    try:
        table = tomllib.loads(text).get("tool", {}).get("pyright")
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(f"type_check: {TOML_CONFIG} does not parse ({e})") from e
    return table if isinstance(table, dict) else None


def project_config(root: Path) -> tuple[str | None, str | None]:
    """(the project's pyright config, the mode it decides); None where there is none.

    pyright reads ``pyrightconfig.json`` first and ``pyproject.toml`` only without
    it. A config that extends another leaves the mode to that one: ``EXTENDS``.
    """
    path = root / JSON_CONFIG
    if path.is_file():
        text = JSON_COMMENT_RE.sub(lambda m: m.group(1) or "", path.read_text())
        mode = MODE_RE.search(text)
        if mode:
            return JSON_CONFIG, mode.group(1)
        return JSON_CONFIG, EXTENDS if EXTENDS_RE.search(text) else None
    path = root / TOML_CONFIG
    table = toml_config(path.read_text()) if path.is_file() else None
    if table is None:
        return None, None
    if table.get("typeCheckingMode"):
        return TOML_CONFIG, str(table["typeCheckingMode"])
    return TOML_CONFIG, EXTENDS if EXTENDS in table else None


def mode_said(name: str | None, mode: str | None) -> str:
    """The mode of a run, and which file set it, for the last line."""
    if mode is None:
        return BASIC
    if mode == EXTENDS:
        return f"set by the config {name} extends"
    return f"{mode}, set in {name}"


def pyright_findings(root: Path) -> tuple[list[dict], str]:
    """pyright's errors, and what ran: its version, the mode and its own counts."""
    exe = shutil.which("pyright")
    if not exe:
        raise SystemExit(
            "type_check: pyright not found; `npm install --global pyright@<version>` "
            "(the type-check workflow installs a pinned one)"
        )
    name, mode = project_config(root)
    cmd, generated = [exe, "--outputjson"], root / GENERATED
    try:
        if mode is None:  # the project names no mode: the standard's
            own = {"extends": name} if name else {}
            generated.write_text(json.dumps({**own, "typeCheckingMode": BASIC}) + "\n")
            cmd += ["--project", GENERATED]
        res = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
    finally:
        generated.unlink(missing_ok=True)
    if res.returncode not in (0, 1):  # 0 clean, 1 errors; else pyright did not run
        err = (res.stderr.strip() or res.stdout.strip())[:300]
        raise SystemExit(f"type_check: pyright did not run ({err})")
    found, summary = parse_pyright(res.stdout, root)
    files = summary.get("filesAnalyzed", 0)
    what = (
        f"pyright {summary['version']}, mode {mode_said(name, mode)}: "
        f"{len(found)} error(s), {summary.get('warningCount', 0)} warning(s), "
        f"{files} file(s) analysed"
    )
    if not files:
        found.append(no_file(name))
    return found, what


def no_file(name: str | None) -> dict:
    """A run that read nothing proves nothing, so it is a finding of its own."""
    fix = (
        f"make `include` in {name} name a directory that holds Python files"
        if name
        else "run it where the Python files are (`working-directory`, or `--root`)"
    )
    found = finding("pyright", name or ".", 1, 1, "no-file", "pyright analysed no file")
    return found | {"fix": fix}


def tsc_findings(root: Path) -> tuple[list[dict], str]:
    """The errors of the project's own tsc, and its version."""
    exe = root / "node_modules" / ".bin" / "tsc"
    if not exe.exists():
        raise SystemExit(
            "type_check: node_modules/.bin/tsc not found; add typescript to "
            "devDependencies and run npm ci"
        )
    said = subprocess.run(
        [str(exe), "--version"], cwd=root, capture_output=True, text=True
    )
    version = said.stdout.strip().removeprefix("Version ") or "?"
    res = subprocess.run(
        [str(exe), "--noEmit", "--pretty", "false", "--project", "."],
        cwd=root,
        capture_output=True,
        text=True,
    )
    found = parse_tsc(res.stdout)
    if res.returncode != 0 and not found:
        err = (res.stderr.strip() or res.stdout.strip())[:300]
        raise SystemExit(f"type_check: tsc did not run ({err})")
    return found, f"tsc {version}: {len(found)} error(s)"


def report(found: list[dict], tool: str) -> list[str]:
    """One line per file that fails, then one line per finding, as code_rules has."""
    counts = Counter(f"{f['rel']}::{f['code']}" for f in found)
    problems = code_rules.rule_lines("types", counts, tool, None)
    return problems + code_rules.explained(found, {f["rel"] for f in found})


def parse_args(argv) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument(
        "--root",
        default=".",
        help="the project to check (default: the current directory)",
    )
    ap.add_argument(
        "--language",
        choices=("python", "typescript"),
        default="python",
        help="python: pyright (the default); typescript: the project's tsc --noEmit",
    )
    return ap.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    root = Path(a.root).resolve()
    if a.language == "typescript":
        tool = "tsc"
        found, what = tsc_findings(root)
    else:
        tool = "pyright"
        found, what = pyright_findings(root)
    for line in report(found, tool):
        print(line)
    files = len({f["rel"] for f in found})
    verdict = f"FAILED, {files} problem(s)" if found else "OK"
    print(f"type_check: {verdict} ({what})")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
