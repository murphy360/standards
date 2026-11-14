"""secret-scan.yml, the scan it runs and the gitleaks pre-commit hook.

The scan is ``tools/steps/secret-scan.sh``: the workflow's one step, and the
secret-scan part of the ``checks`` action. It is run here on scratch repositories.
Most tests put a stub ``docker`` first on the PATH, so they need neither Docker nor
the network: they check which commits the step asks gitleaks for, and what it makes
of gitleaks' answer. One test runs the real image on a commit that holds a
token-shaped string; it is skipped where the image is not already present, and it
never pulls. The string is built at run time, so this file holds no secret.
"""

import importlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
run_checks = importlib.import_module("run_checks")
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "secret-scan.yml"
SCRIPT = ROOT / "tools" / "steps" / "secret-scan.sh"  # the scan step
PIN = ROOT / "tools" / "images" / "Dockerfile.gitleaks"  # where Dependabot moves it
CONFIGS = (
    ROOT / ".pre-commit-config.yaml",
    ROOT / "templates" / "pre-commit-config.yaml",
)
IMAGE_RE = re.compile(
    r"^FROM (ghcr\.io/gitleaks/gitleaks:v([\d.]+)@sha256:[0-9a-f]{64})$",
    re.MULTILINE,
)
HOOK_RE = re.compile(
    r"- repo: https://github\.com/gitleaks/gitleaks\n"
    r"\s+rev: ([0-9a-f]{40})  # v([\d.]+)\n"
    r"\s+hooks:\n"
    r"\s+- id: gitleaks\n"
)
IMAGE, VERSION = IMAGE_RE.search(PIN.read_text()).groups()
ZEROS = "0" * 40
ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "Standards Tests",
    "GIT_AUTHOR_EMAIL": "tests@example.com",
    "GIT_COMMITTER_NAME": "Standards Tests",
    "GIT_COMMITTER_EMAIL": "tests@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}
STUB_DOCKER = """#!/bin/sh
# Stands in for docker: records what it was asked, answers as the test says.
printf '%s\\n' "$@" > "$STUB_ARGS"
[ -z "${STUB_STDERR:-}" ] || printf '%s\\n' "$STUB_STDERR" >&2
[ -z "${STUB_STDOUT:-}" ] || printf '%s\\n' "$STUB_STDOUT"
exit "${STUB_EXIT:-0}"
"""


def fake_token() -> str:
    """Shaped like a GitHub personal access token, and made up: it opens nothing."""
    return "gh" + "p_" + "".join(ALPHABET[(i * 7 + 3) % 62] for i in range(36))


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        env={**os.environ, **GIT_ENV},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def commit(root: Path, name: str, text: str) -> str:
    (root / name).write_text(text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", f"change {name}")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path) -> Path:
    """A repository with one commit on main."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    commit(root, "README.md", "a project\n")
    return root


class Scan:
    """Runs the scan script in a repository, with a stub docker unless ``real``."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.args_file = tmp_path / "docker-args"
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "docker").write_text(STUB_DOCKER)
        (bin_dir / "docker").chmod(0o755)
        self.stub_path = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"

    def run(self, root: Path, event: str, real: bool = False, **env: str):
        full = {
            **os.environ,
            **GIT_ENV,
            "EVENT": event,
            "PR_BASE": "",
            "BEFORE": "",
            "FULL_HISTORY": "false",
            "RUNNER_TEMP": str(self.tmp / "runner"),
            "STUB_ARGS": str(self.args_file),
            **env,
        }
        if not real:
            full["PATH"] = self.stub_path
        return subprocess.run(
            ["bash", str(SCRIPT)],
            cwd=root,
            env=full,
            capture_output=True,
            text=True,
        )

    def log_opts(self) -> str:
        args = self.args_file.read_text().splitlines()
        return next(a for a in args if a.startswith("--log-opts=")).split("=", 1)[1]


@pytest.fixture
def scan(tmp_path) -> Scan:
    return Scan(tmp_path)


# the pins: one gitleaks version, in the workflow and in both pre-commit configs


def test_the_image_is_pinned_by_digest_and_named_once():
    assert len(IMAGE_RE.findall(PIN.read_text())) == 1
    workflow, script = WORKFLOW.read_text(), SCRIPT.read_text()
    assert "ghcr.io/gitleaks" not in workflow + script  # named in the pin file only
    assert 'GITLEAKS_IMAGE="$(sh "$here/../image-ref.sh" gitleaks)"\n' in script
    assert "gitleaks-action" not in workflow  # that action wants a licence
    # the workflow holds no shell of its own: its last step is the script
    assert workflow.endswith(
        '        run: bash "$RUNNER_TEMP/standards/tools/steps/secret-scan.sh"\n'
    )


def test_both_pre_commit_configs_pin_the_hook_at_the_workflows_version():
    pins = [HOOK_RE.search(path.read_text()) for path in CONFIGS]
    assert all(pins), "the gitleaks hook, pinned to a commit SHA, version in a comment"
    assert pins[0].group(1) == pins[1].group(1)  # the same commit in both
    assert {pin.group(2) for pin in pins} == {VERSION}  # and the workflow's version


# the workflow: the shape the other check workflows have


def test_the_workflow_has_the_shape_of_the_other_check_workflows():
    text = WORKFLOW.read_text()
    other = (WORKFLOWS / "shell-lint.yml").read_text()
    for line in other.splitlines():
        if "runs-on" in line or "self-hosted" in line:
            assert line in text, line
    assert "permissions:\n  contents: read\n" in text
    assert re.search(r"^    timeout-minutes: \d+$", text, re.MULTILINE)
    assert "fetch-depth: 0" in text  # the range needs the history
    assert "--redact" in SCRIPT.read_text()
    assert "--verbose" not in SCRIPT.read_text()  # it prints the line with the secret


def test_ci_calls_the_workflow_and_result_needs_it():
    text = (WORKFLOWS / "ci.yml").read_text()
    uses = "./.github/workflows/secret-scan.yml"
    jobs = re.findall(
        rf"^  ([\w-]+):\n    uses: {re.escape(uses)}$", text, re.MULTILINE
    )
    assert jobs, f"ci.yml calls {uses}"
    needs = text.split("\n  result:\n", 1)[1].split("steps:", 1)[0]
    for job in jobs:
        assert re.search(rf"(?<![\w-]){re.escape(job)}(?![\w-])", needs), job


def test_the_template_scans_in_its_checks_job_with_the_history():
    """The checks action runs the scan by default, so the job fetches the history."""
    text = (ROOT / "templates" / "ci.yml").read_text()
    job = text.split("\n  checks:\n", 1)[1].split("\n  test:\n", 1)[0]
    assert "      - uses: murphy360/standards/actions/checks@v2\n" in job
    assert not re.search(r"^\s+checks:", job, re.MULTILINE)  # the default list
    assert "secret-scan" in run_checks.DEFAULT.split()
    assert re.search(r"^          fetch-depth: 0(\s|$)", job, re.MULTILINE)
    needs = text.split("\n  result:\n", 1)[1].split("steps:", 1)[0]
    assert "needs: [checks, " in needs


# what is scanned: the commits of the change, or everything HEAD reaches


def test_a_pull_request_is_scanned_from_the_first_parent_of_its_merge_commit(
    repo, scan
):
    stale_base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "feat")
    commit(repo, "feature.py", "x = 1\n")
    git(repo, "checkout", "-q", "main")
    moved_base = commit(repo, "other.py", "y = 2\n")  # main moved on meanwhile
    git(repo, "checkout", "-q", "--detach")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge", "feat")  # GitHub's merge ref
    res = scan.run(repo, "pull_request", PR_BASE=stale_base)
    assert res.returncode == 0, res.stderr
    assert scan.log_opts() == f"{moved_base}..HEAD"
    assert "1 commit(s) of this pull_request" in res.stdout
    assert "secret scan: no finding" in res.stdout


def test_a_pull_request_without_a_merge_commit_uses_its_base(repo, scan):
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, "feature.py", "x = 1\n")
    assert scan.run(repo, "pull_request", PR_BASE=base).returncode == 0
    assert scan.log_opts() == f"{base}..HEAD"


def test_a_push_is_scanned_from_the_commit_before_it(repo, scan):
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, "a.py", "x = 1\n")
    commit(repo, "b.py", "y = 2\n")
    res = scan.run(repo, "push", BEFORE=before)
    assert res.returncode == 0, res.stderr
    assert scan.log_opts() == f"{before}..HEAD"
    assert "2 commit(s) of this push" in res.stdout


@pytest.mark.parametrize(
    ("event", "env", "said"),
    [
        ("push", {"BEFORE": ZEROS}, "no base to compare with on a push event"),
        ("push", {"BEFORE": "1" * 40}, "is not in this clone"),  # a force push
        ("schedule", {}, "no base to compare with on a schedule event"),
        ("pull_request", {"FULL_HISTORY": "true"}, "full-history is on"),
    ],
)
def test_without_a_base_or_on_request_the_whole_history_is_scanned(
    repo, scan, event, env, said
):
    commit(repo, "a.py", "x = 1\n")
    res = scan.run(repo, event, **env)
    assert res.returncode == 0, res.stderr
    assert scan.log_opts() == "HEAD"
    assert said in res.stdout
    assert "the whole history of" in res.stdout
    assert "2 commit(s)" in res.stdout


def test_a_shallow_clone_is_refused_and_the_step_says_what_to_set(repo, scan, tmp_path):
    """One commit of many: gitleaks would blame every file on it. The workflow
    always fetches the history; a project's own checks job must ask for it."""
    commit(repo, "a.py", "x = 1\n")
    shallow = tmp_path / "shallow"
    git(tmp_path, "clone", "-q", "--depth=1", f"file://{repo}", str(shallow))
    res = scan.run(shallow, "push", BEFORE=ZEROS)
    assert res.returncode == 2
    assert "::error::" in res.stdout and "fetch-depth: 0" in res.stdout
    assert not scan.args_file.exists()  # gitleaks was never asked


def test_gitleaks_is_asked_to_redact_and_cannot_write_to_the_checkout(repo, scan):
    assert scan.run(repo, "push", BEFORE=ZEROS).returncode == 0
    args = scan.args_file.read_text().splitlines()
    assert IMAGE in args
    assert "--redact" in args
    assert f"{repo}:/repo:ro" in args  # read-only: nothing is left for the next job
    template = (scan.tmp / "runner" / "secret-scan" / "findings.tmpl").read_text()
    assert ".File" in template and ".StartLine" in template and ".RuleID" in template
    assert ".Secret" not in template and ".Match" not in template
    assert ".Line " not in template  # the line of source that holds the secret


# what the step makes of gitleaks' answer


def test_a_finding_fails_the_run_and_says_what_to_do(repo, scan):
    line = "::error file=conf.py,line=2,title=secret-scan::github-pat at conf.py:2"
    res = scan.run(repo, "push", BEFORE=ZEROS, STUB_EXIT="1", STUB_STDOUT=line)
    assert res.returncode == 1
    assert line in res.stdout
    assert "revoke it first" in res.stdout
    assert ".gitleaksignore" in res.stdout


def test_an_error_from_gitleaks_fails_the_run_though_it_exits_zero(repo, scan):
    # gitleaks logs git's failure, reports "no leaks found" and exits 0
    err = "\x1b[90m10:53PM\x1b[0m \x1b[31mERR\x1b[0m [git] fatal: Invalid revision"
    res = scan.run(repo, "push", BEFORE=ZEROS, STUB_STDERR=err)
    assert res.returncode == 1
    assert "the scan proves nothing" in res.stdout
    assert "secret scan: no finding" not in res.stdout


def test_an_ordinary_log_line_is_not_an_error(repo, scan):
    info = "\x1b[90m10:53PM\x1b[0m \x1b[32mINF\x1b[0m scanned docs/ERRATA in 3ms"
    assert scan.run(repo, "push", BEFORE=ZEROS, STUB_STDERR=info).returncode == 0


def test_docker_failing_to_run_fails_the_run(repo, scan):
    res = scan.run(repo, "push", BEFORE=ZEROS, STUB_EXIT="125")
    assert res.returncode == 125
    assert "gitleaks did not run" in res.stdout


# the real image, where it is already present (never pulled by a test)


def image_is_present() -> bool:
    if shutil.which("docker") is None:
        return False
    return (
        subprocess.run(
            ["docker", "image", "inspect", IMAGE], capture_output=True
        ).returncode
        == 0
    )


needs_image = pytest.mark.skipif(not image_is_present(), reason="no gitleaks image")


@needs_image
def test_a_commit_that_adds_a_token_shaped_string_is_stopped(repo, scan):
    token = fake_token()
    clean = git(repo, "rev-parse", "HEAD")
    leak = commit(repo, "conf.py", f'x = 1\ntoken = "{token}"\n')
    res = scan.run(repo, "push", real=True, BEFORE=clean)
    out = res.stdout + res.stderr
    assert res.returncode == 1, out
    assert "::error file=conf.py,line=2,title=secret-scan::github-pat" in res.stdout
    assert f"commit {leak[:7]}" in res.stdout
    assert f"fingerprint {leak}:conf.py:github-pat:2" in res.stdout
    assert token not in out and token[4:] not in out  # never the secret itself

    # Taking it out again clears the next change, not the history.
    after = commit(repo, "conf.py", "x = 1\n")
    later = scan.run(repo, "push", real=True, BEFORE=leak)
    assert later.returncode == 0, later.stdout + later.stderr
    assert "secret scan: no finding" in later.stdout
    whole = scan.run(repo, "push", real=True, BEFORE=after, FULL_HISTORY="true")
    assert whole.returncode == 1
    assert token not in whole.stdout + whole.stderr

    # An accepted finding is listed by its fingerprint, and the history is clean.
    commit(repo, ".gitleaksignore", f"{leak}:conf.py:github-pat:2\n")
    accepted = scan.run(repo, "push", real=True, BEFORE=after, FULL_HISTORY="true")
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
