#!/usr/bin/env python3
"""The fleet report: which repository follows which version, and what it lacks.

    python3 fleet.py                       # one table, one row per repository
    python3 fleet.py --json                # the same, for a program to read
    python3 fleet.py --run-tools v2.3.0    # the rehearsal before a release

It lists the owner's repositories that are not archived. For each one it prints:

* the ``murphy360/standards`` workflows it calls, and at which ref: the current
  major tag (``v2``), an older major, or a commit SHA (shown by its first seven
  characters);
* the lint mode (``all`` or ``changed``) and whether a caller passes
  ``format-only``;
* which templates it has: ``CLAUDE.md``, ``.github/dependabot.yml``,
  ``.pre-commit-config.yaml``, ``.github/pull_request_template.md``, and a CI job
  named ``result``;
* whether a ruleset named ``main`` is applied (``?`` when the API refuses to say);
* its open Dependabot pull requests, and the age in days of the oldest;
* whether it carries the repository topic ``no-standards``. That topic is how a
  repository says it stays outside the standard: no list of names is kept here.

A summary follows: adopters at the current major, at an older major, at a commit
SHA, and repositories outside the standard without the topic.

``--run-tools <ref>`` is the rehearsal before a release (fleet_rehearsal.py). It
clones each adopter, runs ``code_rules.py --all`` and ``check_standards.py`` from
this checkout at ``<ref>`` against it, and prints pass or fail per repository.

The report is printed and never written to a file. This repository is public and
most of the repositories it lists are private: do not commit the output, and do
not paste it in a pull request or a ticket. Counts are fine.

Standard library and the ``gh`` CLI only (``gh`` must be signed in).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import fleet_rehearsal

ROOT = Path(__file__).resolve().parents[1]
STANDARDS = "murphy360/standards"
DEFAULT_OWNER = "murphy360"
TOPIC = "no-standards"
LINT_WORKFLOWS = ("python-lint", "node-lint")
DEPENDABOT = ("dependabot", "dependabot[bot]")
TEMPLATES = ("CLAUDE.md", "dependabot", "pre-commit", "pr-template", "result")
PAGE = 10

# One page of the owner's repositories, with everything a row needs. The files are
# read at the default branch (HEAD).
QUERY = (
    """
query($owner: String!, $cursor: String) {
  repositoryOwner(login: $owner) {
    repositories(first: %d, after: $cursor, ownerAffiliations: OWNER,
                 isArchived: false, orderBy: {field: NAME, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        repositoryTopics(first: 50) { nodes { topic { name } } }
        root: object(expression: "HEAD:") { ... on Tree { entries { name } } }
        github: object(expression: "HEAD:.github") {
          ... on Tree { entries { name } }
        }
        workflows: object(expression: "HEAD:.github/workflows") {
          ... on Tree { entries { name object { ... on Blob { text } } } }
        }
        rulesets(first: 50) { nodes { name enforcement } }
        pullRequests(states: OPEN, first: 100,
                     orderBy: {field: CREATED_AT, direction: ASC}) {
          nodes { createdAt author { login } }
        }
      }
    }
  }
}
"""
    % PAGE
)

KEY_RE = re.compile(r"^( *)([\w.-]+|\"[^\"]+\"|'[^']+') *:(?: +(.*))?$")
USES_RE = re.compile(r"^([^\s\"'#]+)[\"']?\s*(?:#\s*(\S+))?")
CALL_RE = re.compile(
    re.escape(STANDARDS) + r"/\.github/workflows/([\w.-]+)\.ya?ml@(\S+)$",
    re.IGNORECASE,
)
LOCAL_RE = re.compile(r"\./\.github/workflows/([\w.-]+)\.ya?ml$")
VERSION_RE = re.compile(r"v(\d+)(\.\d+){0,2}")
SHA_RE = re.compile(r"[0-9a-f]{40}")


class FleetError(RuntimeError):
    """The report cannot be made; the message says why."""


def scalar(raw: str | None) -> str:
    """A YAML value as text: without its quotes, or without a trailing comment."""
    raw = (raw or "").strip()
    if raw[:1] in ('"', "'"):
        end = raw.find(raw[0], 1)
        return raw[1:end] if end > 0 else raw[1:]
    return re.sub(r"\s+#.*$", "", raw).strip()


def inline_map(raw: str) -> dict[str, str]:
    """``{mode: changed, format-check: true}`` as a dict."""
    out = {}
    for part in raw.strip().strip("{}").split(","):
        key, sep, value = part.partition(":")
        if sep:
            out[scalar(key)] = scalar(value)
    return out


def jobs(text: str) -> list[dict]:
    """The jobs of a workflow: key, name, ``uses`` and the ``with:`` values.

    A small line parser, so no YAML library is needed. It reads the block style
    GitHub's own examples use, and a ``with:`` written as an inline map.
    """
    out: list[dict] = []
    in_jobs, job_indent, prop_indent, in_with = False, None, None, False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            in_jobs = re.match(r"jobs\s*:\s*(#.*)?$", line) is not None
            job_indent = None
            continue
        if not in_jobs:
            continue
        m = KEY_RE.match(line)
        if job_indent is None:
            job_indent = indent
        if indent == job_indent:
            if m:
                key = m.group(2).strip("\"'")
                out.append({"key": key, "name": None, "uses": "", "with": {}})
            prop_indent, in_with = None, False
            continue
        if not out:
            continue
        if prop_indent is None:
            prop_indent = indent
        if indent == prop_indent:
            in_with = False
            if m:
                in_with = job_property(out[-1], m.group(2), m.group(3))
        elif in_with and m:
            out[-1]["with"][m.group(2).strip("\"'")] = scalar(m.group(3))
    return out


def job_property(job: dict, key: str, raw: str | None) -> bool:
    """Keep a job's name, ``uses`` or inline ``with``. True when a ``with:`` block
    starts on this line."""
    if key == "name":
        job["name"] = scalar(raw)
    elif key == "uses":
        job["uses"] = (raw or "").strip().lstrip("\"'")
    elif key == "with":
        if raw and raw.lstrip().startswith("{"):
            job["with"].update(inline_map(raw))
        return True
    return False


def ref_kind(ref: str, major: int) -> str:
    """current, older, sha or other: what a caller's ref is."""
    if ref == "local":
        return "current"
    m = VERSION_RE.fullmatch(ref)
    if m:
        return "current" if int(m.group(1)) >= major else "older"
    return "sha" if SHA_RE.fullmatch(ref) else "other"


def workflow_calls(text: str, major: int, local: bool = False) -> list[dict]:
    """The standards' workflows a workflow file calls, each with its ref.

    ``local`` is for the standards repository itself, which calls its own
    workflows by path: that counts as the current major.
    """
    out = []
    for job in jobs(text):
        m = USES_RE.match(job["uses"])
        if not m:
            continue
        call = CALL_RE.match(m.group(1))
        here = LOCAL_RE.match(m.group(1)) if local else None
        hit = call or here
        if not hit:
            continue
        ref = call.group(2) if call else "local"
        out.append(
            {
                "workflow": hit.group(1),
                "ref": ref,
                "kind": ref_kind(ref, major),
                "version": m.group(2),
                "with": job["with"],
            }
        )
    return out


def has_result_job(text: str) -> bool:
    """Whether a workflow has a job whose check is named ``result``."""
    return any((job["name"] or job["key"]) == "result" for job in jobs(text))


def lint_mode(calls: list[dict]) -> str | None:
    """``all``, ``changed`` or both: the mode of the lint callers. None without one.

    ``expr`` is a mode written as an expression, which only a run can resolve.
    """
    modes = set()
    for call in calls:
        if call["workflow"] in LINT_WORKFLOWS:
            mode = call["with"].get("mode", "all")
            modes.add("expr" if "${{" in mode else mode)
    return "+".join(sorted(modes)) or None


def format_only(calls: list[dict]) -> bool:
    return any(call["with"].get("format-only", "false") != "false" for call in calls)


def templates(root: set[str], github: set[str], result_job: bool) -> dict[str, bool]:
    """Which templates a repository has, from the names in its root and .github."""
    pr_names = {name.lower() for name in root | github}
    return {
        "CLAUDE.md": "CLAUDE.md" in root,
        "dependabot": "dependabot.yml" in github,
        "pre-commit": ".pre-commit-config.yaml" in root,
        "pr-template": "pull_request_template.md" in pr_names,
        "result": result_job,
    }


def ruleset_state(rulesets: dict | None) -> str:
    """yes, off (it exists but is not enforced), no, or ``?`` when refused."""
    if rulesets is None:
        return "?"
    found = [r for r in rulesets.get("nodes") or [] if r and r.get("name") == "main"]
    if not found:
        return "no"
    active = any(r.get("enforcement") == "ACTIVE" for r in found)
    return "yes" if active else "off"


def dependabot_prs(pulls: list[dict], now: datetime) -> tuple[int, int | None]:
    """How many open pull requests are Dependabot's, and the oldest one's age."""
    created = [
        datetime.fromisoformat(p["createdAt"])
        for p in pulls
        if p and (p.get("author") or {}).get("login") in DEPENDABOT
    ]
    if not created:
        return 0, None
    return len(created), max(0, (now - min(created)).days)


def names(tree: dict | None) -> set[str]:
    return {e["name"] for e in (tree or {}).get("entries") or []}


def row(node: dict, major: int, now: datetime, standards_name: str | None) -> dict:
    """One repository's row, from its GraphQL node."""
    local = node["name"] == standards_name
    calls, result_job = [], False
    for entry in (node.get("workflows") or {}).get("entries") or []:
        if not entry["name"].endswith((".yml", ".yaml")):
            continue
        text = (entry.get("object") or {}).get("text") or ""
        calls += workflow_calls(text, major, local)
        result_job = result_job or has_result_job(text)
    topics = (node.get("repositoryTopics") or {}).get("nodes") or []
    pulls = (node.get("pullRequests") or {}).get("nodes") or []
    count, oldest = dependabot_prs(pulls, now)
    return {
        "name": node["name"],
        "calls": calls,
        "kinds": sorted({c["kind"] for c in calls}),
        "lint_mode": lint_mode(calls),
        "format_only": format_only(calls),
        "templates": templates(
            names(node.get("root")), names(node.get("github")), result_job
        ),
        "ruleset": ruleset_state(node.get("rulesets")),
        "dependabot_prs": count,
        "oldest_days": oldest,
        "topic": any(t["topic"]["name"] == TOPIC for t in topics if t),
    }


def gh_graphql(query: str, variables: dict[str, str | None]) -> dict:
    """One GraphQL call through ``gh``. A refused field comes back as null with
    an entry in ``errors``, and ``gh`` exits 1: the data is still used."""
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is not None:
            cmd += ["-f", f"{key}={value}"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise FleetError(f"`gh` did not run ({e}); install it and `gh auth login`")
    try:
        answer = json.loads(res.stdout)
    except ValueError:
        answer = None
    if not isinstance(answer, dict) or not answer.get("data"):
        why = res.stderr.strip() or res.stdout.strip()[:300]
        raise FleetError(f"`gh api graphql` failed: {why}")
    return answer


def list_repositories(owner: str, fetch=gh_graphql) -> list[dict]:
    """Every repository of the owner that is not archived, page by page."""
    nodes, cursor = [], None
    while True:
        answer = fetch(QUERY, {"owner": owner, "cursor": cursor})
        found = (answer.get("data") or {}).get("repositoryOwner")
        if not found:
            raise FleetError(f"no owner named `{owner}`, or this token cannot see it")
        page = found["repositories"]
        nodes += [n for n in page["nodes"] if n]
        if not page["pageInfo"]["hasNextPage"]:
            return nodes
        cursor = page["pageInfo"]["endCursor"]


def report(nodes: list[dict], owner: str, major: int, now: datetime) -> list[dict]:
    """A row per repository: the adopters first, then the rest, each by name."""
    standards_owner, standards_name = STANDARDS.split("/")
    own = standards_name if owner.lower() == standards_owner else None
    rows = [row(node, major, now, own) for node in nodes]
    return sorted(rows, key=lambda r: (not r["calls"], r["name"].lower()))


def summary(rows: list[dict]) -> dict[str, int]:
    """The counts under the table. A repository that calls two kinds of ref is
    counted under each."""
    adopters = [r for r in rows if r["calls"]]
    outside = [r for r in rows if not r["calls"]]
    return {
        "repositories": len(rows),
        "adopters": len(adopters),
        "current": sum(r["kinds"] == ["current"] for r in adopters),
        "older": sum("older" in r["kinds"] for r in adopters),
        "sha": sum("sha" in r["kinds"] for r in adopters),
        "other": sum("other" in r["kinds"] for r in adopters),
        "outside_with_topic": sum(r["topic"] for r in outside),
        "outside_without_topic": sum(not r["topic"] for r in outside),
    }


def summary_lines(counts: dict[str, int], major: int) -> list[str]:
    out = [
        f"{counts['repositories']} repositories, {counts['adopters']} adopters.",
        f"  at the current major (v{major}) only: {counts['current']}",
        f"  calling an older major: {counts['older']}",
        f"  calling a commit SHA: {counts['sha']}",
    ]
    if counts["other"]:
        out.append(f"  calling another ref (a branch): {counts['other']}")
    out += [
        f"  outside, with the topic {TOPIC}: {counts['outside_with_topic']}",
        f"  outside, without the topic: {counts['outside_without_topic']}",
    ]
    return out


def notes(rows: list[dict]) -> list[str]:
    """What the table cannot say, each said once."""
    unknown = sum(r["ruleset"] == "?" for r in rows)
    if not unknown:
        return []
    return [
        f"ruleset `?`: the API refused to list the rulesets of {unknown} "
        "repositories. The token needs admin rights there."
    ]


def short(workflow: str) -> str:
    """A column title: ``python-lint`` is ``python``."""
    return re.sub(r"-(lint|check)$", "", workflow)


def refs_cell(calls: list[dict], workflow: str) -> str:
    refs = []
    for call in calls:
        if call["workflow"] == workflow:
            ref = call["ref"][:7] if call["kind"] == "sha" else call["ref"]
            if ref not in refs:
                refs.append(ref)
    return ",".join(refs) or "-"


def yes(value: bool) -> str:
    return "yes" if value else "-"


def aligned(lines: list[list[str]]) -> list[str]:
    """Rows of cells as text, each column as wide as its widest cell."""
    widths = [max(len(cell) for cell in column) for column in zip(*lines)]
    return [
        "  ".join(cell.ljust(width) for cell, width in zip(line, widths)).rstrip()
        for line in lines
    ]


def table(rows: list[dict]) -> list[str]:
    """The table as lines: a column per workflow any repository calls."""
    workflows = sorted({c["workflow"] for r in rows for c in r["calls"]})
    head = ["repository", *(short(w) for w in workflows), "lint", "format-only"]
    head += [*TEMPLATES, "ruleset", "dependabot PRs", "oldest", TOPIC]
    body = []
    for r in rows:
        oldest = "-" if r["oldest_days"] is None else f"{r['oldest_days']}d"
        body.append(
            [
                r["name"],
                *(refs_cell(r["calls"], w) for w in workflows),
                r["lint_mode"] or "-",
                yes(r["format_only"]),
                *(yes(r["templates"][t]) for t in TEMPLATES),
                r["ruleset"],
                str(r["dependabot_prs"]),
                oldest,
                yes(r["topic"]),
            ]
        )
    return aligned([head, *body])


def default_major() -> int:
    """The current major: the one this checkout's pyproject.toml names."""
    try:
        text = (ROOT / "pyproject.toml").read_text()
    except OSError:
        return 2
    m = re.search(r'^version\s*=\s*"(\d+)\.', text, re.MULTILINE)
    return int(m.group(1)) if m else 2


def default_owner() -> str:
    """The owner of the repository ``gh`` resolves here, or murphy360."""
    cmd = ["gh", "repo", "view", "--json", "owner", "-q", ".owner.login"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return DEFAULT_OWNER
    found = res.stdout.strip() if res.returncode == 0 else ""
    return found or DEFAULT_OWNER


def parse_args(argv) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument(
        "--owner",
        help="whose repositories (default: the owner of the repository `gh` "
        f"resolves here, or {DEFAULT_OWNER})",
    )
    ap.add_argument(
        "--major",
        type=int,
        default=default_major(),
        help="the current major version (default: the one in pyproject.toml)",
    )
    ap.add_argument(
        "--repo",
        action="append",
        default=[],
        metavar="NAME",
        help="only this repository; may be given more than once",
    )
    ap.add_argument("--json", action="store_true", help="print JSON, not a table")
    ap.add_argument(
        "--run-tools",
        metavar="REF",
        help="the rehearsal: run code_rules.py and check_standards.py from this "
        "checkout at REF against a clone of each adopter",
    )
    return ap.parse_args(argv)


def main(argv=None, *, fetch=gh_graphql, now: datetime | None = None) -> int:
    args = parse_args(argv)
    now = now or datetime.now(timezone.utc)
    try:
        owner = args.owner or default_owner()
        rows = report(list_repositories(owner, fetch), owner, args.major, now)
        if args.repo:
            unknown = sorted(set(args.repo) - {r["name"] for r in rows})
            if unknown:
                raise FleetError(f"{owner} has no repository named {unknown[0]}")
            rows = [r for r in rows if r["name"] in args.repo]
        if args.run_tools:
            return fleet_rehearsal.main(
                rows, owner, args.run_tools, ROOT, as_json=args.json
            )
    except RuntimeError as e:
        print(f"fleet: {e}", file=sys.stderr)
        return 2
    counts = summary(rows)
    if args.json:
        doc = {
            "owner": owner,
            "major": args.major,
            "repositories": rows,
            "summary": counts,
            "notes": notes(rows),
        }
        print(json.dumps(doc, indent=2))
        return 0
    print("\n".join(table(rows)))
    print()
    print("\n".join(summary_lines(counts, args.major) + notes(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
