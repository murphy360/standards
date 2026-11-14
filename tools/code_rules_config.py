"""The standard rules, and what a project's own linter config cannot do to them.

``code_rules.py`` imports this module. It runs ruff and ESLint with the project's own
config. That config may add rules, set the line length (with a reason beside it) and
exclude files. It may not switch a standard rule off. Each way a config could do that
unseen is closed here:

* ``per-file-ignores`` and ``lint.exclude``: ruff runs a second time, with the standard
  rules alone and both options emptied on the command line. A finding only that run
  sees is a finding like any other, and its fix says the config cannot hide it.
* ``extend-per-file-ignores``: ruff adds this option up from every source, so the
  command line cannot empty it. An entry that names a standard rule is a problem.
* Exclusions (``exclude``, ``extend-exclude``, ``include``, ruff's default excludes,
  a ``.gitignore``): a tracked Python file ruff does not check is printed by name on
  every run, and the last line counts them. It is not a failure: tracked generated
  code is a real case. It is no longer silent.
* ``eslint-disable`` comments (``--no-inline-config``): ESLint runs with
  ``--no-inline-config``, so a disable comment hides nothing, and each one in a
  tracked JavaScript or TypeScript file is a problem, as a ``# noqa`` is.
* ruff's default rules: from ruff 0.16.0 a config that selects no rules gets about
  400 of them, where it had 59. So the standard names the old default itself
  (``project_rules``), and a new ruff changes no project's rules unasked.

Standard library only.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python before 3.11 cannot read the project's ruff config
    tomllib = None

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
# The second run: the standard rules alone (--select, not --extend-select), and the
# two options that hide a file from them emptied.
STANDARD_ALONE = [
    "--select",
    *STANDARD[1:],
    "--config",
    "lint.per-file-ignores={}",
    "--config",
    "lint.exclude=[]",
]
HIDDEN = (
    "the project's ruff config hid this finding, and a per-file ignore (or "
    "`lint.exclude`) cannot switch a standard rule off"
)
# ruff's default rules before 0.16.0. A command-line `select` replaces the config's
# own `extend-select` and `ignore`, so project_rules repeats them after it.
OLD_DEFAULT = 'lint.select=["E4", "E7", "E9", "F"]'
CONFIG_NAMES = (".ruff.toml", "ruff.toml", "pyproject.toml")  # ruff's order
EXTEND_IGNORES = "extend-per-file-ignores"
JS_SUFFIXES = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")
# A comment or a string of JavaScript. A string is matched so that a comment opener
# inside one is not read as a comment.
JS_PIECE_RE = re.compile(
    r"""//[^\n]*|/\*.*?\*/|'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"|`(?:\\.|[^`\\])*`""",
    re.DOTALL,
)
# As ESLint reads a directive: the first word of the comment, then a space or its end.
DISABLE_RE = re.compile(r"(?://|/\*)\s*eslint-disable(?:-next-line|-line)?(?=\s|\*/|$)")
INLINE_HELP = (
    "with --eslint: an `eslint-disable` comment hides nothing and is a problem"
)


def finding_key(f: dict) -> tuple:
    return (f["rel"], f["line"], f["col"], f["code"], f["message"])


def unhidden(root: Path, keep: set[str] | None, ruff_findings, fix_for) -> list[dict]:
    """ruff's findings with the project's config, plus the standard ones it hides.

    ``ruff_findings`` runs ruff and ``fix_for`` gives a rule's fix (both from
    code_rules). A finding both runs see is counted once.
    """
    found = ruff_findings(root, keep, project_rules(root, keep))
    seen = {finding_key(f) for f in found}
    for f in ruff_findings(root, keep, STANDARD_ALONE):
        if finding_key(f) not in seen:
            found.append({**f, "fix": f"{fix_for(f['code'])}; {HIDDEN}"})
    return found


def unchecked(root: Path, files: list[str], ruff: list[str]) -> list[str]:
    """The Python files among ``files`` that ruff does not check.

    ``ruff check --show-files`` lists what ruff checks, after the project's config,
    ruff's default excludes and any ``.gitignore``.
    """
    python = [f for f in files if f.endswith(".py") and (root / f).is_file()]
    if not python:
        return []
    res = subprocess.run(
        [*ruff, "check", "--show-files", "."], cwd=root, capture_output=True, text=True
    )
    if res.returncode != 0:
        raise SystemExit(
            f"code_rules: ruff did not list its files ({res.stderr.strip()[:300]})"
        )
    base, checked = root.resolve(), set()
    for line in res.stdout.splitlines():
        try:
            checked.add(Path(line.strip()).resolve().relative_to(base).as_posix())
        except ValueError:
            continue
    return [f for f in python if f not in checked]


def notes(now: dict) -> list[str]:
    """What a run always says, pass or fail: each file ruff does not check."""
    out = [
        f"code_rules: note: ruff does not check {rel}: the project's ruff config, "
        "ruff's default excludes or a .gitignore leaves it out"
        for rel in now["unchecked"]
    ]
    if tomllib is None and any(f.endswith(".py") for f in now["files"]):
        out.append(
            f"code_rules: note: `{EXTEND_IGNORES}` in the project's ruff config was "
            "not checked, and its `extend-select` and `ignore` were not repeated: "
            "reading the config needs Python 3.11 or newer"
        )
    return out


def names_standard_rule(selector: str) -> bool:
    """Whether a rule selector covers a standard rule: ``E501``, ``F``, ``PLR``."""
    return bool(
        selector == "ALL"
        or re.fullmatch(r"[EWF]\d*", selector)
        or (selector and any(code.startswith(selector) for code in COMPLEXITY))
    )


def ruff_settings(path: Path) -> dict | None:
    """The ruff settings a config file holds; None when it holds none.

    A file that cannot be read or parsed holds none here: ruff reports it itself.
    """
    if tomllib is None:
        return None
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    if path.name == "pyproject.toml":
        doc = doc.get("tool", {}).get("ruff")
    return doc if isinstance(doc, dict) else None


def add_config(found: dict[Path, dict], directory: Path) -> bool:
    """Add the one config ruff reads in this directory; whether it has one."""
    for name in CONFIG_NAMES:
        settings = ruff_settings(directory / name)
        if settings is not None:
            found[(directory / name).resolve()] = settings
            return True
    return False


def ruff_configs(root: Path, python: list[str]) -> dict[Path, dict]:
    """Each config file ruff reads for these Python files, with its ruff settings.

    As ruff finds them: the config of each directory between a file and the root,
    the nearest one above when the root has none, and every file one of them extends.
    """
    found: dict[Path, dict] = {}
    below = {root / parent for rel in python for parent in Path(rel).parents}
    for directory in sorted(below - {root}):
        add_config(found, directory)
    for directory in (root, *root.parents):
        if add_config(found, directory):
            break
    queue = list(found.items())
    while queue:
        path, settings = queue.pop()
        target = settings.get("extend")
        if not isinstance(target, str):
            continue
        target = (path.parent / Path(target).expanduser()).resolve()
        more = None if target in found else ruff_settings(target)
        if more is not None:
            found[target] = more
            queue.append((target, more))
    return found


def rule_list(settings: dict, key: str) -> list[str] | None:
    """A rule list a config sets, at the top level or under ``lint``; None if unset."""
    lint = settings.get("lint")
    tables = [t for t in (settings, lint) if isinstance(t, dict) and key in t]
    if not tables:
        return None
    return [
        s
        for t in tables
        if isinstance(t[key], list)
        for s in t[key]
        if isinstance(s, str)
    ]


def project_rules(root: Path, keep: set[str] | None) -> list[str]:
    """The standard's flags for the run with the project's own config.

    When no config ruff reads sets ``select``, ruff's old default is pinned on the
    command line, and each config's ``extend-select`` and ``ignore`` are repeated
    after it (their union, when there are several configs). An ignore that names a
    standard rule is left out: the standard's ``--extend-select`` always overrode it.
    """
    pinned = [*STANDARD, "--config", OLD_DEFAULT]
    if tomllib is None:
        return pinned
    python = sorted(f for f in keep or () if f.endswith(".py"))
    configs = list(ruff_configs(root, python).values())
    if any(rule_list(c, "select") is not None for c in configs):
        return STANDARD

    def union(*keys: str) -> list[str]:
        return sorted({s for c in configs for k in keys for s in rule_list(c, k) or ()})

    extend = union("extend-select")
    ignore = [s for s in union("ignore", "extend-ignore") if not names_standard_rule(s)]
    for flag, selectors in (("--extend-select", extend), ("--ignore", ignore)):
        pinned += [flag, ",".join(selectors)] if selectors else []
    return pinned


def extend_ignores(settings: dict) -> list[tuple[str, list[str]]]:
    """(pattern, the standard rules it names) of ``extend-per-file-ignores``."""
    lint = settings.get("lint")
    tables = (
        settings.get(EXTEND_IGNORES),
        isinstance(lint, dict) and lint.get(EXTEND_IGNORES),
    )
    out = []
    for table in tables:
        for pattern, selectors in table.items() if isinstance(table, dict) else ():
            named = [
                s
                for s in (selectors if isinstance(selectors, list) else ())
                if isinstance(s, str) and names_standard_rule(s)
            ]
            if named:
                out.append((pattern, named))
    return out


def ignore_problems(root: Path, files: list[str]) -> list[str]:
    """An ``extend-per-file-ignores`` entry that names a standard rule, one line each.

    ruff adds this option up from every config and from the command line, so the
    second run cannot empty it the way it empties ``per-file-ignores``.
    """
    python = [f for f in files if f.endswith(".py")]
    if tomllib is None or not python:
        return []
    out = []
    for path, settings in sorted(ruff_configs(root, python).items()):
        rel = Path(os.path.relpath(path, root.resolve())).as_posix()
        out += [
            f"ruff-config: {rel}: `{EXTEND_IGNORES}` names {', '.join(named)} for "
            f"`{pattern}`; a per-file ignore cannot switch a standard rule off: "
            "remove it and fix the findings it hid"
            for pattern, named in extend_ignores(settings)
        ]
    return out


def eslint_extra(a) -> tuple:
    """What ``eslint_findings`` is given after its two arguments: the extra flags."""
    return (["--no-inline-config"],) if a.no_inline_config else ()


def eslint_disable_comments(files: list[str], root: Path) -> list[str]:
    """Every ``eslint-disable`` comment in the JavaScript and TypeScript files.

    All its forms: ``eslint-disable``, ``eslint-disable-line`` and
    ``eslint-disable-next-line``, in a line or a block comment. The words in a string
    are not a comment.
    """
    out = []
    for rel in files:
        path = root / rel
        if not rel.endswith(JS_SUFFIXES) or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for piece in JS_PIECE_RE.finditer(text):
            if DISABLE_RE.match(piece.group()):
                line = text.count("\n", 0, piece.start()) + 1
                out.append(
                    f"eslint-disable: {rel}:{line}: an `eslint-disable` comment is "
                    "not allowed; remove it and fix the finding it hid"
                )
    return out


def problems(root: Path, tracked: set[str], a) -> list[str]:
    """The problems a config makes by itself, not a finding in a file.

    ``tracked`` is every tracked file in ``--paths``. Vendored code is left out, as
    it is from the size limit.
    """
    files = sorted(
        f for f in tracked if "/vendor/" not in f and "node_modules" not in f
    )
    out = ignore_problems(root, files)
    if a.no_inline_config:
        out += eslint_disable_comments(files, root)
    return out
