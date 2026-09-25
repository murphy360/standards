#!/usr/bin/env python3
"""Does this repository follow the murphy360 standards? The check that the
``standards-check`` workflow runs on every project.

    python3 check_standards.py            # exit 1, one line per gap

It checks:

* **Dependabot**: ``.github/dependabot.yml`` exists and covers every ecosystem the
  repository uses:
  ``github-actions`` when it has workflows, ``pip`` for a pyproject.toml /
  requirements*.txt, ``docker`` for a
  Dockerfile, ``npm`` for a package.json (each in the directory that holds it).
* **Pinned actions**: every ``uses:`` of a third-party action is a full commit SHA (a
  comment may name the version).
  Local actions (``./``), ``docker://`` images with a digest, and
  ``murphy360/standards`` at a version tag are allowed.
* **CLAUDE.md** exists at the root (from the standards' template).

Standard library only.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

USES_RE = re.compile(r'^\s*(?:-\s*)?uses:\s*["\']?([^\s"\'#]+)')
SHA_RE = re.compile(r"@[0-9a-f]{40}$")
STANDARDS_RE = re.compile(
    r"^murphy360/standards/\.github/workflows/[\w.-]+\.ya?ml@v\d+(\.\d+){0,2}$"
)
ECOSYSTEM_FILES = {
    "pip": ("pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.py"),
    "docker": ("Dockerfile",),
    "npm": ("package.json",),
}


def tracked(root: Path) -> list[str]:
    try:
        return subprocess.run(
            ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return [
            p.relative_to(root).as_posix()
            for p in root.rglob("*")
            if p.is_file() and ".git" not in p.parts
        ]


def needed_ecosystems(files: list[str]) -> set[tuple[str, str]]:
    """(ecosystem, directory) pairs the repository needs Dependabot to watch."""
    out = set()
    if any(f.startswith(".github/workflows/") for f in files):
        out.add(("github-actions", "/"))
    for eco, names in ECOSYSTEM_FILES.items():
        for f in files:
            if Path(f).name in names or (
                eco == "docker" and Path(f).name.startswith("Dockerfile")
            ):
                d = Path(f).parent.as_posix()
                out.add((eco, "/" if d == "." else "/" + d))
    return out


def dependabot_entries(text: str) -> set[tuple[str, str]]:
    """(ecosystem, directory) pairs a dependabot.yml declares.

    A small parser, so no YAML library is needed.
    """
    out, eco = set(), None
    for line in text.splitlines():
        m = re.match(r'\s*-?\s*package-ecosystem:\s*["\']?([\w-]+)', line)
        if m:
            eco = m.group(1)
            continue
        m = re.match(r'\s*directory:\s*["\']?([^"\'\s#]+)', line)
        if m and eco:
            out.add((eco, m.group(1).rstrip("/") or "/"))
        m = re.match(r"\s*directories:\s*\[(.*)\]", line)
        if m and eco:
            out |= {
                (eco, d.strip(" \"'").rstrip("/") or "/")
                for d in m.group(1).split(",")
                if d.strip()
            }
    return out


def unpinned(root: Path, files: list[str]) -> list[str]:
    out = []
    for f in files:
        if not (
            f.startswith(".github/workflows/") or f.endswith("action.yml")
        ) or not f.endswith((".yml", ".yaml")):
            continue
        for n, line in enumerate(
            (root / f).read_text(errors="replace").splitlines(), 1
        ):
            m = USES_RE.match(line)
            if not m:
                continue
            ref = m.group(1)
            if (
                ref.startswith("./")
                or STANDARDS_RE.match(ref)
                or (ref.startswith("docker://") and "@sha256:" in ref)
            ):
                continue
            if not SHA_RE.search(ref):
                out.append(f"{f}:{n}: `{ref}` is not pinned to a commit SHA")
    return out


def check(root: Path) -> list[str]:
    files = tracked(root)
    problems = []
    dep = root / ".github" / "dependabot.yml"
    if not dep.exists():
        problems.append(
            ".github/dependabot.yml is missing "
            "(copy templates/dependabot.yml from murphy360/standards)"
        )
        have = set()
    else:
        have = dependabot_entries(dep.read_text())
    for eco, d in sorted(needed_ecosystems(files) - have):
        if dep.exists():
            problems.append(f".github/dependabot.yml does not watch {eco} in {d}")
    problems += unpinned(root, files)
    if not (root / "CLAUDE.md").exists():
        problems.append(
            "CLAUDE.md is missing "
            "(start from templates/CLAUDE.md in murphy360/standards)"
        )
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=".")
    a = ap.parse_args(argv)
    problems = check(Path(a.root).resolve())
    for p in problems:
        print(p)
    print("standards: " + (f"FAILED, {len(problems)} gap(s)" if problems else "OK"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
