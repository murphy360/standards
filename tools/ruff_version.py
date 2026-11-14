#!/usr/bin/env python3
"""Which ruff a project is linted with, and where that version is written.

    python3 ruff_version.py                   # ruff <version> (the standard's default)
    python3 ruff_version.py --project-ruff    # ruff <version> (the project's pin, ...)
    python3 ruff_version.py --install         # print it, then pip install it

``python-lint`` and ``node-lint`` run this file to install ruff, and
``code_rules.py`` imports it, so CI and the ``code-rules`` hook choose the same way.
The version comes from the first of three places that names one:

1. **Given**: ``--ruff-version`` (the ``ruff-version`` input of the workflows).
2. **The project's pin**, with ``--project-ruff`` (the ``project-ruff`` input; off by
   default in v2): a ``ruff==<version>`` line in the project's development
   requirements. They are read in this order, and the first pin wins:
   ``requirements-dev.txt``, every other ``requirements*.txt`` by name, then
   ``[dependency-groups]`` and ``[project.optional-dependencies]`` of
   ``pyproject.toml``. Only the directory the lint runs in is read (``--root``, the
   workflow's ``working-directory``). Only an exact pin counts: ``ruff>=0.6`` names
   no version. ``pyproject.toml`` is read on Python 3.11 or newer.
3. **The standard's default**: the one dependency of this repository's own
   ``pyproject.toml``, the one place it is written. Dependabot moves it there.

Without a flag ``code_rules.py`` runs the ruff on PATH, whatever its version, as it
always has: the workflow installed it, and pre-commit built the hook with it. With
``--ruff-version`` or ``--project-ruff`` it runs that version and no other. It takes
the ruff on PATH when that is the one, and otherwise installs it with pip into a
directory of its own: inside the environment that runs it (pre-commit's hook
environment), or the user's cache directory. So the hook follows a project's pin by
itself, and a Dependabot pull request that moves the pin moves CI and the hook.

``pin_problems`` is the opt-in check of ``check_standards.py``
(``--require-one-ruff-version``): every place a project names a ruff version names
the same one.

Standard library only.
"""

from __future__ import annotations

import argparse
import functools
import os
import re
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python before 3.11 cannot read a pyproject.toml
    tomllib = None

PACKAGE = "murphy360-standards"
VERSION_ENV = "CODE_RULES_RUFF_VERSION"
PROJECT_ENV = "CODE_RULES_PROJECT_RUFF"
FIRST = "requirements-dev.txt"
PIN_RE = re.compile(
    r"\s*ruff\s*==\s*([0-9][0-9A-Za-z.]*)\s*(?:[;#\\]|--|$)", re.IGNORECASE
)
DEFAULT_RE = re.compile(
    r'^dependencies\s*=\s*\[\s*"ruff==([0-9][0-9A-Za-z.]*)"\s*\]', re.MULTILINE
)
GROUP_TABLES = ("dependency-groups", "project.optional-dependencies")
RUFF_HOOKS_RE = re.compile(
    r"repo:\s*[\"']?https://github\.com/astral-sh/ruff-pre-commit/?[\"']?\s*\n"
    r"\s*rev:\s*[\"']?([^\s\"'#]+)[\"']?[ \t]*(?:#(.*))?"
)
HOOK_RE = re.compile(r"^(\s*)-\s*id:\s*[\"']?(code-rules(?:-node)?)[\"']?\s*(?:#.*)?$")
LINT_WORKFLOWS = ("python-lint", "node-lint")
ONE_VERSION = (
    "Pin ruff once in requirements-dev.txt (`ruff==<version>`), pass `project-ruff: "
    "true` to the lint job and `--project-ruff` in the args of the code-rules hook, "
    "and set the ruff-pre-commit rev to the commit of the same release"
)


def default() -> str:
    """The standard's default ruff: the one dependency of this repository.

    Read from the ``pyproject.toml`` beside ``tools/`` (a checkout, as a workflow
    has it), or from the installed package (the hook pre-commit built).
    """
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text()
        pin = DEFAULT_RE.search(text)
        if pin and f'name = "{PACKAGE}"' in text:
            return pin.group(1)
    try:
        requires = metadata.requires(PACKAGE) or []
    except metadata.PackageNotFoundError:
        requires = []
    for requirement in requires:
        pin = PIN_RE.match(requirement)
        if pin:
            return pin.group(1)
    raise SystemExit(
        "ruff_version: the standard's default ruff was not found: neither a "
        f"pyproject.toml beside tools/ nor an installed {PACKAGE} names it. "
        "Pass --ruff-version."
    )


def requirement_files(root: Path) -> list[Path]:
    """``requirements-dev.txt`` first, then the other ``requirements*.txt`` by name."""
    found = [p for p in root.glob("requirements*.txt") if p.is_file()]
    return sorted(found, key=lambda p: (p.name != FIRST, p.name))


def pyproject_pins(path: Path) -> list[tuple[str, str]]:
    """The ``ruff==`` pins in the dependency groups and extras of a pyproject.toml."""
    if tomllib is None or not path.is_file():
        return []
    try:
        doc = tomllib.loads(path.read_text(errors="replace"))
    except ValueError:  # ruff reports a pyproject.toml it cannot read
        return []
    project = doc.get("project")
    extras = project.get("optional-dependencies") if isinstance(project, dict) else {}
    pins = []
    for name, table in zip(GROUP_TABLES, (doc.get("dependency-groups"), extras)):
        for group, entries in table.items() if isinstance(table, dict) else ():
            for entry in entries if isinstance(entries, list) else ():
                pin = PIN_RE.match(entry) if isinstance(entry, str) else None
                if pin:
                    pins.append((pin.group(1), f"{path.name} [{name}] {group}"))
    return pins


def project_pins(root: Path) -> list[tuple[str, str]]:
    """Every ``ruff==<version>`` in the project's development requirements, in the
    order they are read: (version, where)."""
    pins = []
    for path in requirement_files(root):
        for line in path.read_text(errors="replace").splitlines():
            pin = PIN_RE.match(line)
            if pin:
                pins.append((pin.group(1), path.name))
    return pins + pyproject_pins(root / "pyproject.toml")


def choose(root: Path, given: str = "", project: bool = False) -> tuple[str, str]:
    """The ruff version to lint with, and where it comes from."""
    given = (given or "").strip()
    if given:
        return given, "given: ruff-version"
    if not project:
        return default(), "the standard's default"
    pins = project_pins(root)
    if pins:
        return pins[0][0], f"the project's pin, {pins[0][1]}"
    return default(), "the standard's default: the project pins no ruff"


def same(one: str | None, other: str | None) -> bool:
    """Whether two versions are one release: ``0.6`` and ``0.6.0`` are."""

    def release(version: str) -> list[str]:
        parts = version.strip().lstrip("v").split(".")
        while len(parts) > 1 and parts[-1] == "0":
            parts.pop()
        return parts

    return bool(one and other) and release(one) == release(other)


def path_command() -> list[str]:
    """The ruff on PATH, or the module of the Python that runs this file."""
    exe = shutil.which("ruff")
    return [exe] if exe else [sys.executable, "-m", "ruff"]


def installed(command: list[str]) -> str | None:
    """The version a ruff command reports; None when it does not run."""
    try:
        res = subprocess.run([*command, "--version"], capture_output=True, text=True)
    except OSError:
        return None
    said = re.match(r"ruff (\S+)", res.stdout)
    return said.group(1) if res.returncode == 0 and said else None


def cache_dir() -> Path:
    """Where a ruff this module installs goes: inside the environment that runs it
    (pre-commit's hook environment, which `pre-commit clean` removes), or the
    user's cache directory."""
    if sys.prefix != sys.base_prefix:
        return Path(sys.prefix) / "ruff-versions"
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / PACKAGE


def provision(version: str, target: Path) -> None:
    """``pip install`` one ruff release into a directory of its own."""
    fresh = target.with_name(f"{target.name}.{os.getpid()}")
    shutil.rmtree(fresh, ignore_errors=True)
    fresh.parent.mkdir(parents=True, exist_ok=True)
    pip = [sys.executable, "-m", "pip", "install", "--quiet", "--no-deps"]
    pip += ["--disable-pip-version-check", "--target", str(fresh), f"ruff=={version}"]
    try:
        res = subprocess.run(pip, capture_output=True, text=True)
        failed = res.stderr.strip()[-300:] if res.returncode else ""
    except OSError as error:
        failed = str(error)
    if failed or not (fresh / "bin").is_dir():
        shutil.rmtree(fresh, ignore_errors=True)
        raise SystemExit(
            f"code_rules: ruff {version} is not installed here, and pip could not "
            f"install it ({failed or 'it installed no bin/ruff'}); "
            f"`pip install ruff=={version}` puts it on PATH"
        )
    shutil.rmtree(target, ignore_errors=True)
    os.replace(fresh, target)


@functools.lru_cache(maxsize=None)
def at_version(version: str) -> tuple[str, ...]:
    """The command of one ruff release: the one on PATH, or one installed for it."""
    here = path_command()
    if same(installed(here), version):
        return tuple(here)
    target = cache_dir() / f"ruff-{version}"
    exe = shutil.which("ruff", path=str(target / "bin"))
    if not exe or not same(installed([exe]), version):
        provision(version, target)
        exe = shutil.which("ruff", path=str(target / "bin"))
    if not exe or not same(installed([exe]), version):
        raise SystemExit(
            f"code_rules: {target}/bin/ruff is not ruff {version}, though pip "
            "installed it"
        )
    return (exe,)


def command(version: str | None = None) -> list[str]:
    """The ruff to run: the one on PATH as it is, or the chosen release."""
    return list(at_version(version)) if version else path_command()


def chosen(root: Path, given: str = "", project: bool = False) -> dict[str, str]:
    """What ``code_rules.py`` keeps of the flags: the version to run, and nothing
    without a flag. It prints the choice, so the log says which ruff ran and why."""
    if not (given or "").strip() and not project:
        return {}
    version, source = choose(root, given, project)
    print(f"code_rules: ruff {version} ({source})", flush=True)
    return {"version": version}


def add_arguments(ap: argparse.ArgumentParser) -> None:
    """The two flags, for this file and ``code_rules.py``. A workflow sets them in
    the environment once, for the step that installs and the step that lints."""
    ap.add_argument(
        "--ruff-version",
        default=os.environ.get(VERSION_ENV, ""),
        metavar="VERSION",
        help="run this ruff release; it wins over the project's pin",
    )
    ap.add_argument(
        "--project-ruff",
        action="store_true",
        default=os.environ.get(PROJECT_ENV, "") == "true",
        help="run the ruff the project pins in its development requirements "
        "(`ruff==<version>`), and the standard's default when it pins none",
    )


# the opt-in check: every place a project names a ruff version names the same one


def hook_words(lines: list[str], start: int, indent: int) -> list[str]:
    """The words of one hook of a pre-commit config: its args, its entry and its
    additional dependencies, inline or as a list. Read without a YAML library."""
    words: list[str] = []
    for line in lines[start + 1 :]:
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if len(line) - len(line.lstrip(" ")) <= indent:
            break  # the next hook, or the next repository
        key = re.match(r"(?:args|entry|additional_dependencies)\s*:(.*)", text)
        if key:
            value = key.group(1)
        elif text.startswith("- "):
            value = text[2:]
        else:
            continue
        words += re.findall(r"[^\s\[\],\"']+", value.split(" #")[0])
    return words


def flags(words: list[str]) -> tuple[str, bool, str]:
    """``--ruff-version``, ``--project-ruff`` and ``--root`` among a hook's words."""
    given, root = "", "."
    for i, word in enumerate(words):
        key, _, value = word.partition("=")
        value = value or (words[i + 1] if i + 1 < len(words) else "")
        if key == "--ruff-version":
            given = value
        elif key == "--root":
            root = value
    return given, "--project-ruff" in words, root


def hook_versions(root: Path, config: str) -> list[tuple[str, str]]:
    """What each code-rules hook of a pre-commit config runs: (version, place)."""
    out = []
    lines = (root / config).read_text(errors="replace").splitlines()
    for n, line in enumerate(lines):
        hook = HOOK_RE.match(line)
        if not hook:
            continue
        words = hook_words(lines, n, len(hook.group(1)))
        given, project, where = flags(words)
        extra = [PIN_RE.match(w) for w in words]
        if not given and not project and any(extra):  # a local hook's own ruff
            version, source = next(e for e in extra if e).group(1), "its dependency"
        else:
            version, source = choose(root / where, given, project)
        out.append((version, f"{config}, the {hook.group(2)} hook ({source})"))
    return out


def rev_version(root: Path, config: str) -> tuple[str | None, str] | None:
    """The release the ruff-pre-commit hooks are pinned at: a tag, or the version in
    the comment beside a commit SHA. None when the config has no such hooks."""
    found = RUFF_HOOKS_RE.search((root / config).read_text(errors="replace"))
    if not found:
        return None
    rev, comment = found.group(1), found.group(2) or ""
    named = re.search(
        r"\d+(?:\.\d+)+", comment if re.fullmatch(r"[0-9a-f]{40}", rev) else rev
    )
    return (named.group(0) if named else None), f"{config}, the ruff-pre-commit rev"


def lint_calls(root: Path, files: list[str], calls) -> list[tuple[str, dict]]:
    """Each call of python-lint or node-lint in the project's workflows: the file
    with the workflow's name, and the ``with:`` values.

    ``calls`` reads a workflow's calls of the standard (``fleet.workflow_calls``).
    """
    out = []
    for rel in files:
        if not rel.startswith(".github/workflows/"):
            continue
        if not rel.endswith((".yml", ".yaml")):
            continue
        for call in calls((root / rel).read_text(errors="replace"), 0, True):
            if call["workflow"] in LINT_WORKFLOWS:
                out.append((f"{rel}, {call['workflow']}", call["with"]))
    return out


def lint_directory(given: dict) -> str:
    """The directory a lint job runs in; the root when only a run can say."""
    where = given.get("working-directory", ".")
    return "." if "${{" in where else where


def pin_problems(root: Path, files: list[str], calls) -> list[str]:
    """The opt-in check: the ruff versions a project names, when they differ.

    The places: the pins in its development requirements (at the root and in each
    directory a lint job runs in), the ruff-pre-commit rev, what each code-rules
    hook runs and what each lint job runs.
    """
    config = ".pre-commit-config.yaml"
    lint = lint_calls(root, files, calls)
    places = []
    for where in sorted({".", *(lint_directory(given) for _, given in lint)}):
        prefix = "" if where == "." else f"{where}/"
        places += [(v, prefix + place) for v, place in project_pins(root / where)]
    for name, given in lint:
        version = given.get("ruff-version", "")
        if "${{" in version:
            continue  # only a run resolves an expression
        version, source = choose(
            root / lint_directory(given), version, given.get("project-ruff") == "true"
        )
        places.append((version, f"{name} ({source})"))
    problems = []
    if config in files:
        rev = rev_version(root, config)
        if rev and rev[0] is None:
            problems.append(
                f"{rev[1]} names no release: write its version in a comment beside "
                "the commit (`rev: <sha>  # v<version>`)"
            )
        elif rev:
            places.append(rev)
        places += hook_versions(root, config)
    versions: list[str] = []
    for version, _ in places:
        if not any(same(version, seen) for seen in versions):
            versions.append(version)
    if len(versions) > 1:
        named = "; ".join(f"{version} in {place}" for version, place in places)
        problems.append(
            f"ruff is not one version here, so local and CI can differ: {named}. "
            f"{ONE_VERSION}"
        )
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--root",
        default=".",
        help="the directory the lint runs in (default: the current directory)",
    )
    add_arguments(ap)
    ap.add_argument(
        "--install",
        action="store_true",
        help="also `pip install` that release into the Python that runs this file",
    )
    a = ap.parse_args(argv)
    version, source = choose(Path(a.root).resolve(), a.ruff_version, a.project_ruff)
    print(f"ruff {version} ({source})", flush=True)
    if not a.install:
        return 0
    pip = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    return subprocess.run([*pip, f"ruff=={version}"]).returncode


if __name__ == "__main__":
    sys.exit(main())
