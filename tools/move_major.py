"""Moves the major tag to a release that was just published (``release.yml``).

    python3 tools/move_major.py --repo murphy360/standards --tag v2.4.1

A release ``vX.Y.Z`` that is not a pre-release moves ``vX`` to its commit, so every
caller at ``@vX`` picks it up. ``vX.0.0`` creates ``vX``. Nothing moves when:

* the tag is not ``vX.Y.Z``, or the release is a pre-release;
* a newer ``vX`` release is already published (a fix to an older line);
* the ``result`` check of CI did not pass on the release's commit. The tool waits for
  a run that is still going (``--wait`` seconds). It fails the run in this case: the
  release is out, and ``vX`` is not.

Each outcome is one line, printed and written to the run summary. ``GH_TOKEN`` must
be allowed past the repository's tag ruleset (``release.yml`` mints it from a GitHub
App). Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

SEMVER_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
CHECK = "result"
POLL_S = 20


class Api:
    """The few GitHub REST calls this tool makes, with ``GH_TOKEN``."""

    def __init__(self, repo: str, token: str, url: str = "https://api.github.com"):
        self.base = f"{url}/repos/{repo}"
        self.token = token

    def call(self, method: str, path: str, body: dict | None = None) -> dict | None:
        """The JSON reply; None for a 404."""
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return json.loads(res.read() or b"{}")
        except urllib.error.HTTPError as err:
            if err.code == 404:
                return None
            raise SystemExit(
                f"move_major: {method} {path}: HTTP {err.code} {err.reason}"
            )


def version(tag: str) -> tuple[int, int, int] | None:
    m = SEMVER_RE.match(tag)
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def major_for(tag: str, prerelease: bool, published: list[str]) -> tuple[str, str]:
    """(the major tag to move, or "" for none; why)."""
    mine = version(tag)
    if mine is None:
        return "", f"{tag} is not vX.Y.Z: no major tag moves"
    if prerelease:
        return "", f"{tag} is a pre-release: v{mine[0]} stays"
    same_line = [v for v in map(version, published) if v and v[0] == mine[0]]
    newest = max(same_line, default=mine)
    if newest > mine:
        return (
            "",
            f"{tag} is older than v{'.'.join(map(str, newest))}: v{mine[0]} stays",
        )
    return f"v{mine[0]}", ""


def published_tags(api: Api) -> list[str]:
    """The tags of the published releases that are not pre-releases."""
    releases = api.call("GET", "/releases?per_page=100") or []
    return [
        r["tag_name"]
        for r in releases
        if not r.get("draft") and not r.get("prerelease")
    ]


def commit_of(api: Api, tag: str) -> str:
    """The commit a tag names, through an annotated tag."""
    ref = api.call("GET", f"/git/ref/tags/{tag}")
    if ref is None:
        raise SystemExit(f"move_major: no tag {tag}")
    obj = ref["object"]
    while obj["type"] == "tag":
        obj = api.call("GET", f"/git/tags/{obj['sha']}")["object"]
    return obj["sha"]


def ci_passed(api: Api, sha: str, wait_s: int, sleep=time.sleep) -> tuple[bool, str]:
    """Whether the ``result`` check passed on the commit, waiting while it runs."""
    deadline = time.monotonic() + wait_s
    while True:
        runs = api.call("GET", f"/commits/{sha}/check-runs?check_name={CHECK}") or {}
        done = [r for r in runs.get("check_runs", []) if r.get("status") == "completed"]
        if any(r.get("conclusion") == "success" for r in done):
            return True, ""
        if done:
            return False, f"`{CHECK}` did not pass on {sha[:7]}"
        if time.monotonic() >= deadline:
            return False, f"no finished `{CHECK}` run on {sha[:7]} after {wait_s} s"
        sleep(POLL_S)


def move(api: Api, major: str, sha: str, dry_run: bool) -> str:
    """Point ``major`` at ``sha``; what was done, in a line."""
    ref = api.call("GET", f"/git/ref/tags/{major}")
    now = ref["object"]["sha"] if ref else ""
    if now == sha:
        return f"{major} is already at {sha[:7]}"
    verb = f"moved from {now[:7]}" if now else "created"
    if dry_run:
        return f"dry run: {major} would be {verb} to {sha[:7]}"
    if now:
        api.call("PATCH", f"/git/refs/tags/{major}", {"sha": sha, "force": True})
    else:
        api.call("POST", "/git/refs", {"ref": f"refs/tags/{major}", "sha": sha})
    return f"{major} {verb} to {sha[:7]}"


def run(
    api: Api, tag: str, prerelease: bool, wait_s: int, dry_run: bool
) -> tuple[int, str]:
    """(exit code, the line that says what happened)."""
    major, why = major_for(tag, prerelease, published_tags(api))
    if not major:
        return 0, why
    sha = commit_of(api, tag)
    ok, why = ci_passed(api, sha, wait_s)
    if not ok:
        return 1, f"{major} stays: {why}. Move it by hand once CI passes on {tag}"
    return 0, move(api, major, sha, dry_run)


def report(line: str) -> None:
    print(f"move_major: {line}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"{line}\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--tag", required=True, help="the release's tag, vX.Y.Z")
    p.add_argument("--prerelease", action="store_true")
    p.add_argument("--wait", type=int, default=900, help="seconds to wait for CI")
    p.add_argument("--dry-run", action="store_true", help="say what would move")
    a = p.parse_args(argv)
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise SystemExit("move_major: GH_TOKEN is not set")
    code, line = run(Api(a.repo, token), a.tag, a.prerelease, a.wait, a.dry_run)
    report(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
