"""tools/repo-settings.sh and the templates it applies.

``--dry-run`` never calls ``gh``, so the script's own behaviour is tested without
network access or a real token; only its printed plan is checked. Where a test needs
GitHub's answer, a stub ``gh`` stands first on the PATH and no login is in reach.
"""

import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "repo-settings.sh"
RULESET = ROOT / "templates" / "rulesets" / "main.json"
TAG_RULESET = ROOT / "templates" / "rulesets" / "version-tags.json"
PRE_COMMIT = ROOT / "templates" / "pre-commit-config.yaml"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
GH_STUB = """#!/bin/sh
# Stands in for gh: logs each call, and refuses the calls whose body holds $GH_REFUSE,
# as GitHub refuses secret scanning on a private repository of a personal account.
body=""
case " $* " in *" --input "*) body="$(cat)" ;; esac
printf '%s %s\\n' "$*" "$(printf '%s' "$body" | tr -d ' \\n')" >> "$GH_LOG"
case "$body" in
  *"$GH_REFUSE"*)
    echo '{"message": "Secret scanning is not available for this repository."}'
    echo "gh: Secret scanning is not available for this repository. (HTTP 422)" >&2
    exit 1
    ;;
esac
"""


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True)


def test_dry_run_plans_every_call_without_invoking_gh():
    res = run("murphy360/standards", "--dry-run")
    assert res.returncode == 0, res.stderr
    out = res.stdout
    assert "DRY RUN: gh api --method PATCH repos/murphy360/standards" in out
    assert '"allow_squash_merge": true' in out
    assert '"allow_merge_commit": false' in out
    assert '"allow_auto_merge": true' in out
    assert '"allow_update_branch": true' in out
    assert (
        "DRY RUN: gh api --method PUT repos/murphy360/standards/vulnerability-alerts"
        in out
    )
    assert "repos/murphy360/standards/automated-security-fixes" in out
    assert "DRY RUN: gh api --method POST repos/murphy360/standards/rulesets" in out
    assert '"name": "main"' in out
    assert '"name": "version-tags"' in out
    assert (
        out.count("DRY RUN: gh api --method POST repos/murphy360/standards/rulesets")
        == 2
    )
    assert "Done (dry run: nothing was changed)" in out


def test_dry_run_plans_secret_scanning_and_push_protection():
    res = run("murphy360/standards", "--dry-run")
    assert res.returncode == 0, res.stderr
    out = res.stdout
    assert '"secret_scanning": {"status": "enabled"}' in out
    assert '"secret_scanning_push_protection": {"status": "enabled"}' in out
    # one call each, apart from the merge settings: one refusal does not take the other
    assert out.count("DRY RUN: gh api --method PATCH repos/murphy360/standards\n") == 3
    assert "warning" not in res.stderr


def run_with_stub_gh(tmp_path, refuse: str) -> tuple[subprocess.CompletedProcess, str]:
    """The script for real, against a stub ``gh`` that refuses some calls."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(GH_STUB)
    (bin_dir / "gh").chmod(0o755)
    log = tmp_path / "gh.log"
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),  # no login in reach, whatever gh this finds
        "GH_CONFIG_DIR": str(tmp_path / "no-login"),
        "GH_LOG": str(log),
        "GH_REFUSE": refuse,
    }
    res = subprocess.run(
        ["bash", str(SCRIPT), "example-owner/example-repo"],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    return res, log.read_text()


def test_refused_secret_scanning_is_a_warning_and_the_rest_still_applies(tmp_path):
    res, log = run_with_stub_gh(tmp_path, "security_and_analysis")
    assert res.returncode == 0, res.stderr
    assert "warning: GitHub refused to turn on Secret scanning" in res.stderr
    assert "warning: GitHub refused to turn on Push protection" in res.stderr
    assert res.stderr.count("warning:") == 2
    calls = log.splitlines()
    refused = max(i for i, call in enumerate(calls) if "security_and_analysis" in call)
    after = calls[refused + 1 :]
    posts = "--method POST repos/example-owner/example-repo/rulesets"
    assert sum(posts in call for call in after) == 2  # both rulesets still applied
    assert res.stdout.rstrip().endswith("Done")


def test_any_other_refused_call_still_stops_the_script(tmp_path):
    res, log = run_with_stub_gh(tmp_path, "allow_squash_merge")
    assert res.returncode != 0
    assert "warning:" not in res.stderr
    assert len(log.splitlines()) == 1  # nothing was sent after the refusal


def test_usage_errors_without_calling_gh():
    assert run().returncode == 1
    assert "Usage:" in run().stderr
    assert run("not-a-slash-repo", "--dry-run").returncode == 1
    missing = run("owner/repo", "--dry-run", "--ruleset", "/no/such/file.json")
    assert missing.returncode == 1
    assert "ruleset file not found" in missing.stderr


def test_a_custom_ruleset_names_itself_in_the_plan(tmp_path):
    custom = tmp_path / "other.json"
    custom.write_text(json.dumps({"name": "custom-ruleset", "rules": []}))
    res = run("owner/repo", "--dry-run", "--ruleset", str(custom))
    assert res.returncode == 0, res.stderr
    assert "Ruleset 'custom-ruleset'" in res.stdout
    assert '"name": "custom-ruleset"' in res.stdout
    assert "version-tags" not in res.stdout  # --ruleset replaces the defaults


def test_release_app_is_let_past_tag_rulesets_only():
    res = run("owner/repo", "--dry-run", "--release-app", "42")
    assert res.returncode == 0, res.stderr
    main_part, tag_part = res.stdout.split("Ruleset 'version-tags'")
    assert '"actor_type": "Integration"' not in main_part
    assert '"actor_id": 42' in tag_part
    assert '"actor_type": "Integration"' in tag_part
    assert run("owner/repo", "--dry-run", "--release-app", "x").returncode == 1


def test_ruleset_can_be_given_more_than_once(tmp_path):
    paths = []
    for name in ("first", "second"):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"name": name, "rules": []}))
        paths += ["--ruleset", str(path)]
    res = run("owner/repo", "--dry-run", *paths)
    assert res.returncode == 0, res.stderr
    assert "Ruleset 'first'" in res.stdout
    assert "Ruleset 'second'" in res.stdout


# templates/rulesets/main.json: the ticket's four rules; the owner's bypass works
# only through a pull request, so a direct push to main is refused for everyone


def test_the_ruleset_template_has_the_rules_the_ticket_names():
    data = json.loads(RULESET.read_text())
    types = {r["type"] for r in data["rules"]}
    assert types == {
        "required_linear_history",
        "non_fast_forward",
        "pull_request",
        "required_status_checks",
    }
    checks = next(r for r in data["rules"] if r["type"] == "required_status_checks")[
        "parameters"
    ]["required_status_checks"]
    assert {c["context"] for c in checks} == {"result"}
    bypass = data["bypass_actors"]
    assert len(bypass) == 1
    assert bypass[0]["actor_type"] == "User"  # OrganizationAdmin: not a personal repo
    assert bypass[0]["bypass_mode"] == "pull_request"
    assert isinstance(bypass[0]["actor_id"], int)


# templates/rulesets/version-tags.json: only the owner makes, moves or deletes a v* tag


def test_the_tag_ruleset_guards_every_version_tag():
    data = json.loads(TAG_RULESET.read_text())
    assert data["target"] == "tag"
    assert data["enforcement"] == "active"
    assert data["conditions"]["ref_name"]["include"] == ["refs/tags/v*"]
    types = {r["type"] for r in data["rules"]}
    assert types == {"creation", "update", "deletion", "non_fast_forward"}
    bypass = data["bypass_actors"]
    owner = json.loads(RULESET.read_text())["bypass_actors"][0]["actor_id"]
    assert bypass == [
        {"actor_id": owner, "actor_type": "User", "bypass_mode": "always"}
    ]


# templates/pre-commit-config.yaml: every hook pinned to a commit SHA


def test_every_pre_commit_hook_is_pinned_to_a_commit_sha():
    revs = re.findall(r"^\s*rev:\s*(\S+)", PRE_COMMIT.read_text(), re.MULTILINE)
    assert len(revs) == 5  # pre-commit-hooks, ruff, shellcheck, actionlint, gitleaks
    assert all(SHA_RE.match(rev) for rev in revs), revs


def test_pre_commit_config_names_the_tools_it_pins():
    text = PRE_COMMIT.read_text()
    hooks = ("ruff", "ruff-format", "shellcheck", "actionlint-docker", "gitleaks")
    for hook_id in hooks:
        assert f"id: {hook_id}\n" in text
