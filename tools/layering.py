#!/usr/bin/env python3
"""The layering contracts: which package of a project may import which.

    python3 layering.py               # the .importlinter here; exit 1 when one is broken
    python3 layering.py --config api/.importlinter     # the packages are looked for in api/

The contracts are in ``.importlinter``, in import-linter's INI shape: an
``[importlinter]`` section with ``root_packages`` (or ``root_package``), and one
``[importlinter:contract:<id>]`` section per contract, of the type ``forbidden``,
with ``source_modules``, ``forbidden_modules`` and ``ignore_imports``. import-linter
is not needed: this file reads the imports with Python's own ``ast``.

* **What is read.** Each root package, and the package of each module a
  contract's ``source_modules`` names, is found among the tracked ``*.py`` files:
  the shallowest directory of that name that holds an ``__init__.py``, or else the
  shallowest directory of that name (a namespace package, such as ``tools/``), or
  else a module file of that name. A directory inside a package is never one.
  Tests are left out (``code_rules.is_test``, and ``conftest.py``). Every import
  counts, also one inside a function or under ``TYPE_CHECKING``.
* **Direct imports only**, as import-linter's ``allow_indirect_imports = True``.
* **A module matches a name** when it is that module or inside it.
* **An allowed breach** is a line of ``ignore_imports``: ``importer -> imported``
  with the ticket that removes it in a comment (``# #123``, ``# owner/repo#123`` or
  the issue's URL). A line with no ticket fails, and so does one that no longer
  happens, so the list only shrinks.
* **An exempt module** is a line of ``exempt_modules``, ``module  # why``: a module
  the contract allows for good, whose imports it does not check. A line with no
  reason fails, and so does one whose module imports nothing the contract forbids.
  import-linter does not know this option.

No ``.importlinter``: one line says there is nothing to check, and the run passes.
A contract of another type or a wildcard is an error in the file (exit 2). A
package that is not found (one the project installs, or one that moved out) is
said, and nothing in it is read. Other sections, such as a project's own notes, are skipped.
The ``layering`` check of the ``checks`` action runs this file in its
``working-directory``; the ``layering`` pre-commit hook runs it in the root of the
checkout.

Standard library only.
"""

from __future__ import annotations

import argparse
import ast
import configparser
import re
import sys
from pathlib import Path, PurePosixPath

from code_rules import is_test, tracked_files

CONFIG = ".importlinter"
SECTION = "importlinter:contract:"
TICKET_RE = re.compile(r"#\d+|/(?:issues|pull)/\d+")
KEPT, BROKEN, BAD_CONFIG = 0, 1, 2


class ConfigError(Exception):
    """The contracts cannot be read as written."""


# -- the contracts ---------------------------------------------------------------------


def lines_of(value: str) -> list[str]:
    return [x.strip() for x in value.splitlines() if x.strip()]


def no_wildcard(section: str, line: str) -> str:
    if "*" in line:
        raise ConfigError(f"[{section}]: `{line}`: wildcards are not supported")
    return line


def ignore_entry(section: str, line: str) -> dict:
    """``a -> b  # #123 why`` as {importer, imported, ticket}; ticket is None when
    the comment names none."""
    rule, _, comment = no_wildcard(section, line).partition("#")
    importer, arrow, imported = rule.partition("->")
    if not arrow or not importer.strip() or not imported.strip():
        raise ConfigError(f"[{section}]: `{line}` is not `importer -> imported`")
    comment = "#" + comment if comment else ""
    return {
        "importer": importer.strip(),
        "imported": imported.strip(),
        "ticket": comment.strip() if TICKET_RE.search(comment) else None,
    }


def exempt_entry(section: str, line: str) -> dict:
    """``module  # why`` as {module, reason}."""
    module, _, reason = no_wildcard(section, line).partition("#")
    return {"module": module.strip(), "reason": reason.strip()}


def contract(section: str, values) -> dict:
    kind = values.get("type", "forbidden").strip()
    if kind != "forbidden":
        raise ConfigError(f"[{section}]: type {kind}: only forbidden is supported")
    source = [
        no_wildcard(section, x) for x in lines_of(values.get("source_modules", ""))
    ]
    forbidden = [
        no_wildcard(section, x) for x in lines_of(values.get("forbidden_modules", ""))
    ]
    if not source or not forbidden:
        raise ConfigError(f"[{section}]: needs source_modules and forbidden_modules")
    return {
        "id": section[len(SECTION) :],
        "name": values.get("name", section).strip(),
        "source": source,
        "forbidden": forbidden,
        "ignore": [
            ignore_entry(section, x) for x in lines_of(values.get("ignore_imports", ""))
        ],
        "exempt": [
            exempt_entry(section, x) for x in lines_of(values.get("exempt_modules", ""))
        ],
    }


def load(text: str) -> tuple[list[str], list[dict]]:
    """The root packages and the contracts of a ``.importlinter``, in their order."""
    cp = configparser.ConfigParser(interpolation=None, comment_prefixes=("#", ";"))
    try:
        cp.read_string(text, source=CONFIG)
    except configparser.Error as error:
        raise ConfigError(str(error)) from error
    top = cp["importlinter"] if cp.has_section("importlinter") else {}
    roots = lines_of(top.get("root_packages", "")) + lines_of(
        top.get("root_package", "")
    )
    contracts = [contract(s, cp[s]) for s in cp.sections() if s.startswith(SECTION)]
    return roots, contracts


# -- the modules -----------------------------------------------------------------------


def parent(rel: str) -> str:
    return str(PurePosixPath(rel).parent)


def ancestors(files: list[str]) -> set[str]:
    """Every directory that holds one of ``files``, at any depth, but the root."""
    out: set[str] = set()
    for f in files:
        d = parent(f)
        while d not in (".", "") and d not in out:
            out.add(d)
            d = parent(d)
    return out


def locate(name: str, files: list[str]) -> str | None:
    """Where the top-level package ``name`` is: its directory, or its file for a
    module of one file. None when no tracked file is there. A directory inside a
    package (one that holds an ``__init__.py``) is that package's, so never one."""
    present = set(files)

    def is_package(d: str) -> bool:
        return d not in (".", "") and f"{d}/__init__.py" in present

    def outside_packages(path: str) -> bool:
        d = parent(path)
        while d not in (".", ""):
            if is_package(d):
                return False
            d = parent(d)
        return True

    named = [d for d in ancestors(files) if PurePosixPath(d).name == name]
    modules = [f for f in files if PurePosixPath(f).name == f"{name}.py"]
    regular = [d for d in named if is_package(d)]
    tiers = (
        [p for p in tier if outside_packages(p)] for tier in (regular, named, modules)
    )
    pool = next((tier for tier in tiers if tier), [])
    if not pool:
        return None
    depth = min(p.count("/") for p in pool)
    best = sorted(p for p in pool if p.count("/") == depth)
    if len(best) > 1:
        raise ConfigError(f"package `{name}` is in more than one place: {best}")
    return best[0]


def module_name(rel: str, base: str, package: str) -> str:
    if base.endswith(".py"):
        return package
    parts = [package, *PurePosixPath(rel).relative_to(base).with_suffix("").parts]
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def absolute(module: str, node: ast.ImportFrom, is_package: bool) -> str:
    """The absolute name a relative ``from`` import names."""
    base = module.split(".")
    if not is_package:
        base = base[:-1]
    base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
    return ".".join(base + ([node.module] if node.module else []))


def imports_of(tree: ast.AST, module: str, is_package: bool) -> list[tuple[str, int]]:
    """Every name a module imports, as (name, line). ``from a import b`` gives
    ``a.b``, which is the module a.b or the name b in the module a."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out += [(a.name, n.lineno) for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            base = absolute(module, n, is_package) if n.level else n.module or ""
            out += [
                (base if a.name == "*" else f"{base}.{a.name}", n.lineno)
                for a in n.names
            ]
    return out


def packages_of(roots: list[str], contracts: list[dict]) -> list[str]:
    """The root packages, then the package of each source module, each once."""
    out: list[str] = []
    for name in roots + [s.split(".")[0] for c in contracts for s in c["source"]]:
        if name not in out:
            out.append(name)
    return out


def source_files(root: Path) -> list[str]:
    """The tracked ``*.py`` files under ``root`` that are not tests."""
    return sorted(
        f
        for f in tracked_files(root)
        if f.endswith(".py")
        and not is_test(f)
        and PurePosixPath(f).name != "conftest.py"
        and (root / f).is_file()
    )


def scan(root: Path, packages: list[str]) -> tuple[dict[str, dict], list[str], list]:
    """{module: {"path", "imports"}} for every module of ``packages``, the files
    that did not parse, and the packages that are not here."""
    files = source_files(root)
    modules: dict[str, dict] = {}
    unread: list[str] = []
    missing: list[str] = []
    for package in packages:
        base = locate(package, files)
        if base is None:
            missing.append(package)
            continue
        inside = (
            [base]
            if base.endswith(".py")
            else [f for f in files if f.startswith(base + "/")]
        )
        for rel in inside:
            try:
                tree = ast.parse((root / rel).read_text(encoding="utf-8"), rel)
            except (SyntaxError, UnicodeDecodeError, ValueError):
                unread.append(rel)
                continue
            name = module_name(rel, base, package)
            is_package = rel.endswith("/__init__.py")
            modules[name] = {"path": rel, "imports": imports_of(tree, name, is_package)}
    return modules, unread, missing


# -- the check -------------------------------------------------------------------------


def within(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def imported_module(name: str, forbidden: str, modules: dict) -> str:
    """The module an import names: ``a.b.c`` when that is a module, else its parent
    (``from a.b import c`` of a name c), never above the forbidden module."""
    while name != forbidden and name not in modules and "." in name:
        name = name.rsplit(".", 1)[0]
    return name


def breaches(rule: dict, modules: dict) -> list[dict]:
    """Every import that ``rule`` forbids: one per importer and imported module,
    at its first line."""
    seen: dict[tuple[str, str], dict] = {}
    for mod, info in sorted(modules.items()):
        if not any(within(mod, s) for s in rule["source"]):
            continue
        for name, line in info["imports"]:
            hit = next((f for f in rule["forbidden"] if within(name, f)), None)
            if hit is None:
                continue
            imported = imported_module(name, hit, modules)
            at = f"{info['path']}:{line}"
            seen.setdefault(
                (mod, imported), {"importer": mod, "imported": imported, "at": at}
            )
    return list(seen.values())


def check_contract(rule: dict, modules: dict) -> dict:
    """What a contract finds: the imports it forbids (broken), the ones it allows
    for now (ignored) or for good (exempted), and the lines of the file that fail
    it (stale, unticketed, exempt_unused, exempt_no_reason)."""
    allowed = {(i["importer"], i["imported"]): i for i in rule["ignore"]}
    broken, ignored, exempted, used, exempt_used = [], [], [], set(), set()
    for b in breaches(rule, modules):
        exempt = next(
            (e for e in rule["exempt"] if within(b["importer"], e["module"])), None
        )
        key = (b["importer"], b["imported"])
        if exempt:
            exempt_used.add(exempt["module"])
            exempted.append(dict(b, module=exempt["module"]))
        elif key in allowed:
            used.add(key)
            ignored.append(dict(b, ticket=allowed[key]["ticket"]))
        else:
            broken.append(b)
    return {
        "id": rule["id"],
        "name": rule["name"],
        "broken": broken,
        "ignored": ignored,
        "exempted": exempted,
        "stale": [i for k, i in allowed.items() if k not in used],
        "unticketed": [i for i in rule["ignore"] if i["ticket"] is None],
        "exempt_unused": [e for e in rule["exempt"] if e["module"] not in exempt_used],
        "exempt_no_reason": [e for e in rule["exempt"] if not e["reason"]],
    }


FAILING = ("broken", "stale", "unticketed", "exempt_unused", "exempt_no_reason")


def kept(result: dict) -> bool:
    return not any(result[key] for key in FAILING)


def finding_lines(r: dict) -> list[str]:
    """A line per finding of one contract, each with what to do about it."""
    out = [
        f"  {b['at']}: {b['importer']} imports {b['imported']}, which the contract "
        "forbids (move the code, or list the import in ignore_imports with its ticket)"
        for b in r["broken"]
    ]
    out += [
        f"  allowed but no longer imported: {i['importer']} -> {i['imported']} "
        f"(remove the line from {CONFIG})"
        for i in r["stale"]
    ]
    out += [
        f"  allowed with no ticket: {i['importer']} -> {i['imported']} "
        "(add `# #<ticket>` to the line: the ticket that removes the import)"
        for i in r["unticketed"]
    ]
    out += [
        f"  exempt but imports nothing the contract forbids: {e['module']} "
        "(remove the line from exempt_modules)"
        for e in r["exempt_unused"]
    ]
    out += [
        f"  exempt with no reason: {e['module']} (add `# <why>` to the line)"
        for e in r["exempt_no_reason"]
    ]
    return out


def report(results: list[dict]) -> list[str]:
    """A line per contract with its findings under it, then the last line."""
    out = []
    for r in results:
        extra = f", {len(r['ignored'])} allowed for now" if r["ignored"] else ""
        modules = sorted({e["module"] for e in r["exempted"]})
        extra += f", exempt: {', '.join(modules)}" if modules else ""
        out.append(f"{'KEPT' if kept(r) else 'BROKEN'}  {r['name']}{extra}")
        out += finding_lines(r)
    broken = [r["id"] for r in results if not kept(r)]
    if broken:
        out.append(
            f"layering: FAILED, {len(broken)} of {len(results)} contracts broken: "
            + ", ".join(broken)
        )
    else:
        out.append(f"layering: OK, {len(results)} of {len(results)} contracts kept")
    return out


def check(root: Path, config: Path) -> tuple[list[dict], list[str]]:
    """Every contract's result, and the lines to print before the report."""
    roots, contracts = load(config.read_text(encoding="utf-8"))
    packages = packages_of(roots, contracts)
    modules, unread, missing = scan(root, packages)
    read = [p for p in packages if p not in missing]
    notes = [f"layering: {len(modules)} modules read, in {', '.join(read)}"]
    notes += [
        f"layering: package {p} is not in this checkout, so it was not read"
        for p in missing
    ]
    notes += [f"layering: {rel} does not parse, so it was not read" for rel in unread]
    return [check_contract(c, modules) for c in contracts], notes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument(
        "--config",
        help=f"the contracts (default: {CONFIG} here); the packages are looked for "
        "in its directory",
    )
    a = ap.parse_args(argv)
    root = Path.cwd()
    config = root / a.config if a.config else root / CONFIG
    if not config.is_file():
        if a.config:
            print(f"layering: {a.config} is not a file")
            return BAD_CONFIG
        print(f"layering: no {CONFIG} here, so there is no contract to check")
        return KEPT
    root = config.parent
    try:
        results, notes = check(root, config)
    except (ConfigError, OSError) as error:
        print(f"layering: {config.name}: {error}")
        print("layering: FAILED, the contracts could not be read")
        return BAD_CONFIG
    print("\n".join(notes + report(results)), flush=True)
    return KEPT if all(kept(r) for r in results) else BROKEN


if __name__ == "__main__":
    sys.exit(main())
