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
* **The instruction file** exists at the root: ``AGENTS.md``, the file every agent
  tool reads (from the standards' template), with or without a ``CLAUDE.md`` that
  imports it. A project that has only ``CLAUDE.md`` passes too.

Five more checks exist but default off, so a project already pinned at ``@v2`` sees no
new failure until it turns one on (``--require-pr-template``, ``--require-pre-commit``,
``--require-ticket-template``, ``--require-current-templates``,
``--require-agent-settings``; the ``standards-check`` workflow has a matching input for
each):

* **pull request template**: ``.github/pull_request_template.md`` exists (from
  ``templates/pull_request_template.md``).
* **pre-commit config**: ``.pre-commit-config.yaml`` exists at the root (from
  ``templates/pre-commit-config.yaml``).
* **ticket template**: ``.github/ISSUE_TEMPLATE`` holds an issue template, ``*.md``
  or ``*.yml`` (from ``templates/ISSUE_TEMPLATE/ticket.md``).
* **current templates**: the shared text of ``AGENTS.md`` or ``CLAUDE.md``, between
  the two ``standards:shared`` markers, is the template's (``../templates`` beside
  this file, so the tool and the template are one version), a ``CLAUDE.md`` beside an
  ``AGENTS.md`` imports it, and a workflow has a job named ``result``, the check the
  ruleset requires. ``sync_templates.py`` is the fix.
* **agent settings**: ``.claude/settings.json`` runs the guard as a ``PreToolUse``
  hook on the shell tool, and the guard file it names exists (from
  ``templates/claude/``).

Standard library only.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import subprocess
import sys
from pathlib import Path

USES_RE = re.compile(r'^\s*(?:-\s*)?uses:\s*["\']?([^\s"\'#]+)')
SHA_RE = re.compile(r"@[0-9a-f]{40}$")
STANDARDS_RE = re.compile(
    r"^murphy360/standards/\.github/workflows/[\w.-]+\.ya?ml@v\d+(\.\d+){0,2}$"
)
INSTRUCTIONS = ("AGENTS.md", "CLAUDE.md")  # either is the instruction file
GUARD_RE = re.compile(r"CLAUDE_PROJECT_DIR\}?[\"']?/(\.claude/[\w./-]*guard[\w.-]*)")
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


def has_pr_template(files: list[str]) -> bool:
    """Whether a pull request template exists, wherever GitHub would find it."""
    for f in files:
        name = Path(f).name.lower()
        parent = Path(f).parent.as_posix().lower()
        if name == "pull_request_template.md" and parent in (".github", ".", "docs"):
            return True
        if parent == ".github/pull_request_template" and name.endswith(".md"):
            return True
    return False


def has_ticket_template(files: list[str]) -> bool:
    """Whether an issue template exists where GitHub looks: ``.github/ISSUE_TEMPLATE``.

    A Markdown template (``*.md``) or an issue form (``*.yml``). GitHub reads the names
    without regard to case. ``config.yml`` sets up the chooser and is not a template.
    """
    for f in files:
        path = Path(f.lower())
        if path.parent.as_posix() != ".github/issue_template":
            continue
        if path.suffix in (".md", ".yml") and path.name != "config.yml":
            return True
    return False


def sibling(name: str):
    """A module beside this file, also when this file is run from another
    directory or loaded by its path."""
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    return importlib.import_module(name)


def stale_templates(root: Path, files: list[str]) -> list[str]:
    """The opt-in template check: the shared text is the template's
    (sync_templates.py holds the comparison), and CI has a job named ``result``
    (fleet.py holds the reader of a workflow's jobs, so the report and the check
    agree)."""
    problems = sibling("sync_templates").block_problems(root)
    has_result_job = sibling("fleet").has_result_job
    workflows = [
        f
        for f in files
        if f.startswith(".github/workflows/") and f.endswith((".yml", ".yaml"))
    ]
    if not any(
        has_result_job((root / f).read_text(errors="replace")) for f in workflows
    ):
        problems.append(
            "no workflow under .github/workflows/ has a job named `result`, the "
            "check the ruleset requires (copy the job from templates/ci.yml in "
            "murphy360/standards)"
        )
    return problems


def guard_files(settings: dict) -> list[str]:
    """The guard files the ``PreToolUse`` hooks on the shell tool run."""
    found = []
    hooks = settings.get("hooks")
    groups = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            continue
        matcher = group.get("matcher") or "*"
        if matcher != "*" and "Bash" not in matcher:
            continue
        for hook in group["hooks"]:
            if isinstance(hook, dict) and isinstance(hook.get("args", []), list):
                words = [hook.get("command"), *hook.get("args", [])]
                found += GUARD_RE.findall(" ".join(w for w in words if w))
    return found


def agent_settings_gaps(root: Path, files: list[str]) -> list[str]:
    """What keeps ``.claude/settings.json`` from refusing the never-rules."""
    name = ".claude/settings.json"
    source = "from templates/claude in murphy360/standards"
    if name not in files:
        return [f"{name} is missing (copy settings.json and guard.py {source})"]
    try:
        settings = json.loads((root / name).read_text())
    except (OSError, ValueError) as error:
        return [f"{name} is not valid JSON ({error})"]
    guards = guard_files(settings) if isinstance(settings, dict) else []
    if not guards:
        return [
            f"{name} has no PreToolUse hook on Bash that runs the guard "
            f"(copy settings.json {source})"
        ]
    return [
        f"{name} runs {guard}, which is missing (copy guard.py {source})"
        for guard in guards
        if guard not in files
    ]


def check(
    root: Path,
    *,
    require_pr_template: bool = False,
    require_pre_commit: bool = False,
    require_ticket_template: bool = False,
    require_current_templates: bool = False,
    require_agent_settings: bool = False,
) -> list[str]:
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
    if not any((root / name).exists() for name in INSTRUCTIONS):
        problems.append(
            "AGENTS.md is missing (start from templates/AGENTS.md and "
            "templates/CLAUDE.md in murphy360/standards)"
        )
    if require_pr_template and not has_pr_template(files):
        problems.append(
            ".github/pull_request_template.md is missing "
            "(copy templates/pull_request_template.md from murphy360/standards)"
        )
    if require_pre_commit and ".pre-commit-config.yaml" not in files:
        problems.append(
            ".pre-commit-config.yaml is missing "
            "(copy templates/pre-commit-config.yaml from murphy360/standards)"
        )
    if require_ticket_template and not has_ticket_template(files):
        problems.append(
            ".github/ISSUE_TEMPLATE has no ticket template "
            "(copy templates/ISSUE_TEMPLATE/ticket.md from murphy360/standards)"
        )
    if require_current_templates:
        problems += stale_templates(root, files)
    if require_agent_settings:
        problems += agent_settings_gaps(root, files)
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=".")
    ap.add_argument(
        "--require-pr-template",
        action="store_true",
        help="also require .github/pull_request_template.md (opt-in, added in v2.1.0)",
    )
    ap.add_argument(
        "--require-pre-commit",
        action="store_true",
        help="also require .pre-commit-config.yaml (opt-in, added in v2.1.0)",
    )
    ap.add_argument(
        "--require-ticket-template",
        action="store_true",
        help="also require an issue template in .github/ISSUE_TEMPLATE (opt-in)",
    )
    ap.add_argument(
        "--require-current-templates",
        action="store_true",
        help="also require the shared text of AGENTS.md or CLAUDE.md to be the "
        "template's, a CLAUDE.md beside an AGENTS.md to import it, and a CI job "
        "named `result` (opt-in)",
    )
    ap.add_argument(
        "--require-agent-settings",
        action="store_true",
        help="also require .claude/settings.json with the guard hook (opt-in)",
    )
    a = ap.parse_args(argv)
    problems = check(
        Path(a.root).resolve(),
        require_pr_template=a.require_pr_template,
        require_pre_commit=a.require_pre_commit,
        require_ticket_template=a.require_ticket_template,
        require_current_templates=a.require_current_templates,
        require_agent_settings=a.require_agent_settings,
    )
    for p in problems:
        print(p)
    print("standards: " + (f"FAILED, {len(problems)} gap(s)" if problems else "OK"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
