"""tools/steps: the shell of each check step, in one place.

A reusable workflow and the ``checks`` action run the same script, so neither holds
a copy of its own. Each script is run here as they run it: by ``bash``, in a scratch
repository, with the inputs in the environment. ``docker`` and, where a test says
so, ``python3`` are stand-ins that record what they were asked. No network.

The code rules step's base and modes are in ``test_tools.py``, its flags in
``test_silencers.py`` and ``test_code_rules_config.py``, and the secret scan in
``test_secret_scan.py``.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
STEPS = ROOT / "tools" / "steps"
IMAGES = ROOT / "tools" / "images"
CHECKS = (
    "standards-check",
    "python-lint",
    "node-lint",
    "shell-lint",
    "actionlint",
    "secret-scan",
)
TOOLS_STEP = "- name: The standards' tools, from the commit this workflow was called at"
RUN_RE = re.compile(r"^\s+(?:- )?run: (.*)$", re.MULTILINE)
SCRIPT_RUN_RE = re.compile(
    r'(bash|sh|python3) "\$RUNNER_TEMP/standards/tools/([\w./-]+)"( [\w"$ -]+)?'
)
GIT_ENV = {
    "GIT_AUTHOR_NAME": "Standards Tests",
    "GIT_AUTHOR_EMAIL": "tests@example.com",
    "GIT_COMMITTER_NAME": "Standards Tests",
    "GIT_COMMITTER_EMAIL": "tests@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}
STUB = """#!/bin/sh
# Stands in for a program: records what it was asked, exits as the test says.
printf '%s\\n' "$@" > "$STUB_ARGS"
exit "${STUB_EXIT:-0}"
"""
FLAGS = [
    ("CHECK_PR_TEMPLATE", "--require-pr-template"),
    ("CHECK_PRE_COMMIT_CONFIG", "--require-pre-commit"),
    ("CHECK_TICKET_TEMPLATE", "--require-ticket-template"),
    ("CHECK_TEMPLATES", "--require-current-templates"),
    ("CHECK_AGENT_SETTINGS", "--require-agent-settings"),
    ("CHECK_RUFF_VERSION", "--require-one-ruff-version"),
]


def pinned(name: str) -> str:
    """The image a Dockerfile under tools/images names, by tag and digest."""
    text = (IMAGES / f"Dockerfile.{name}").read_text()
    return re.search(r"^FROM (\S+)$", text, re.MULTILINE).group(1)


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        env={**os.environ, **GIT_ENV},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def commit(root: Path, files: dict[str, str], message: str = "a change") -> str:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path) -> Path:
    """A repository with one commit on main, which serves any commit by its SHA."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "uploadpack.allowAnySHA1InWant", "true")
    commit(root, {"README.md": "a project\n"})
    return root


class Stubs:
    """A directory first on the PATH that holds stand-ins, and what they were asked."""

    def __init__(self, tmp_path: Path):
        self.bin = tmp_path / "stub-bin"
        self.bin.mkdir()
        self.asked = tmp_path / "stub-args"

    def add(self, program: str) -> "Stubs":
        (self.bin / program).write_text(STUB)
        (self.bin / program).chmod(0o755)
        return self

    def run(self, argv: list[str], cwd: Path, **env: str):
        full = {
            **os.environ,
            **GIT_ENV,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "STUB_ARGS": str(self.asked),
            **env,
        }
        return subprocess.run(argv, cwd=cwd, env=full, capture_output=True, text=True)

    def args(self) -> list[str]:
        return self.asked.read_text().splitlines()


@pytest.fixture
def stubs(tmp_path) -> Stubs:
    return Stubs(tmp_path).add("docker")


def step(name: str, *args: str) -> list[str]:
    return ["bash", str(STEPS / name), *args]


# the workflows: no shell of their own left


@pytest.mark.parametrize("name", CHECKS)
def test_a_check_workflow_holds_no_shell_but_the_step_that_fetches_the_tools(name):
    """Every other ``run:`` is one line that runs a file under tools/, and the tests
    run that file. The step that fetches the tools is run by test_same_ref.py."""
    text = (WORKFLOWS / f"{name}.yml").read_text()
    runs = RUN_RE.findall(text)
    assert runs.count("|") == 1, "one block of shell: the step that fetches the tools"
    block = text.split(TOOLS_STEP, 1)[1].split("\n      - ", 1)[0]
    assert "run: |\n" in block
    for run in runs:
        if run in ("|", "npm ci"):
            continue
        hit = SCRIPT_RUN_RE.fullmatch(run)
        assert hit, f"{name}.yml: `run: {run}` is not one script under tools/"
        assert (ROOT / "tools" / hit.group(2)).is_file(), run


def test_every_step_script_is_run_by_a_workflow_and_by_bash():
    """No script is left behind when a workflow goes, and none needs its mode bits:
    a workflow and the action name the shell that runs it."""
    workflows = "".join(p.read_text() for p in sorted(WORKFLOWS.glob("*.yml")))
    scripts = sorted(STEPS.glob("*.sh"))
    assert [p.name for p in scripts] == [
        "actionlint.sh",
        "code-rules.sh",
        "secret-scan.sh",
        "shell-lint.sh",
        "standards-check.sh",
        "take-ownership.sh",
    ]
    for path in scripts:
        shell = "sh" if path.name == "take-ownership.sh" else "bash"
        assert f'{shell} "$RUNNER_TEMP/standards/tools/steps/{path.name}"' in workflows
        first = path.read_text().splitlines()[0]
        assert first == ("#!/bin/sh" if shell == "sh" else "#!/usr/bin/env bash")


# take-ownership.sh


def test_take_ownership_gives_the_workspace_to_the_user_of_the_job(tmp_path, stubs):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    res = stubs.run(
        ["sh", str(STEPS / "take-ownership.sh")],
        tmp_path,
        GITHUB_WORKSPACE=str(workspace),
    )
    assert res.returncode == 0, res.stderr
    owner = f"{os.getuid()}:{os.getgid()}"
    assert stubs.args() == [
        "run",
        "--rm",
        "-v",
        f"{workspace}:/w",
        pinned("alpine"),
        "chown",
        "-R",
        owner,
        "/w",
    ]
    assert "alpine image: " in res.stderr  # image-ref.sh says which, for the log


def test_take_ownership_fails_when_docker_does(tmp_path, stubs):
    res = stubs.run(
        ["sh", str(STEPS / "take-ownership.sh")],
        tmp_path,
        GITHUB_WORKSPACE=str(tmp_path),
        STUB_EXIT="125",
    )
    assert res.returncode == 125


# shell-lint.sh and actionlint.sh


def test_shell_lint_gives_shellcheck_every_tracked_script(repo, stubs):
    commit(repo, {"run.sh": "echo hi\n", "deploy/up.sh": "echo up\n", "a.py": ""})
    (repo / "scratch.sh").write_text("echo not tracked\n")
    res = stubs.run(step("shell-lint.sh"), repo)
    assert res.returncode == 0, res.stderr
    args = stubs.args()
    assert args[:6] == ["run", "--rm", "-v", f"{repo}:/mnt", "-w", "/mnt"]
    assert args[6] == pinned("shellcheck")
    assert sorted(args[7:]) == ["deploy/up.sh", "run.sh"]


def test_shell_lint_takes_another_release_by_its_tag(repo, stubs):
    commit(repo, {"run.sh": "echo hi\n"})
    assert stubs.run(step("shell-lint.sh", "v0.9.0"), repo).returncode == 0
    assert "koalaman/shellcheck:v0.9.0" in stubs.args()
    assert stubs.run(step("shell-lint.sh", ""), repo).returncode == 0  # the input
    assert pinned("shellcheck") in stubs.args()  # left empty: the pin


def test_shell_lint_passes_without_a_script_and_fails_with_a_finding(repo, stubs):
    res = stubs.run(step("shell-lint.sh"), repo, STUB_EXIT="1")
    assert res.returncode == 0  # no tracked *.sh: shellcheck is not run at all
    assert not stubs.asked.exists()
    commit(repo, {"run.sh": "echo $1\n"})
    res = stubs.run(step("shell-lint.sh"), repo, STUB_EXIT="1")
    assert res.returncode != 0  # xargs reports that the command failed


def test_actionlint_runs_its_image_on_the_checkout(repo, stubs):
    res = stubs.run(step("actionlint.sh"), repo)
    assert res.returncode == 0, res.stderr
    assert stubs.args() == [
        "run",
        "--rm",
        "-v",
        f"{repo}:/repo",
        "-w",
        "/repo",
        pinned("actionlint"),
        "-color",
    ]
    assert stubs.run(step("actionlint.sh", "1.7.7"), repo).returncode == 0
    assert "rhysd/actionlint:1.7.7" in stubs.args()
    assert stubs.run(step("actionlint.sh"), repo, STUB_EXIT="1").returncode == 1


# standards-check.sh


@pytest.mark.parametrize(("variable", "flag"), FLAGS)
def test_standards_check_turns_each_input_into_its_flag(repo, stubs, variable, flag):
    stubs.add("python3")
    res = stubs.run(step("standards-check.sh"), repo, **{variable: "true"})
    assert res.returncode == 0, res.stderr
    tool, *flags = stubs.args()
    assert Path(tool).resolve() == ROOT / "tools" / "check_standards.py"
    assert flags == [flag]


def test_standards_check_leaves_a_check_off_unless_its_input_reads_true(repo, stubs):
    stubs.add("python3")
    off = {variable: "false" for variable, _ in FLAGS}
    off["CHECK_TEMPLATES"] = "yes"  # not `true`: off
    assert stubs.run(step("standards-check.sh"), repo, **off).returncode == 0
    assert len(stubs.args()) == 1  # the tool, and no flag
    every = {variable: "true" for variable, _ in FLAGS}
    assert stubs.run(step("standards-check.sh"), repo, **every).returncode == 0
    assert stubs.args()[1:] == [flag for _, flag in FLAGS]
    failed = stubs.run(step("standards-check.sh"), repo, STUB_EXIT="1")
    assert failed.returncode == 1  # the tool's exit code is the step's


# code-rules.sh: what the other test files leave


LINT_ENV = {"MODE": "changed", "FORMAT_CHECK": "false", "CI": "true"}


def test_code_rules_needs_a_language_and_a_mode_it_knows(repo, stubs):
    for args in ([], ["ruby"]):
        res = stubs.run(step("code-rules.sh", *args), repo, **LINT_ENV)
        assert res.returncode == 2 and "usage: code-rules.sh python|node" in res.stderr
    res = stubs.run(step("code-rules.sh", "python"), repo, MODE="some", EVENT="push")
    assert res.returncode == 1
    assert "::error::mode is all or changed, not 'some'" in res.stdout


def pull_request(repo: Path) -> dict[str, str]:
    """A pull request merged into a main that moved on: the merge commit is HEAD."""
    stale = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "feat")
    head = commit(repo, {"run.sh": "echo hi\n"}, "the pull request")
    git(repo, "checkout", "-q", "main")
    moved = commit(repo, {"other.sh": "echo other\n"}, "main moves on")
    git(repo, "checkout", "-q", "--detach")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge", "feat")
    return {"stale": stale, "head": head, "moved": moved}


def test_a_complete_clone_keeps_its_history_when_the_base_is_there(repo, stubs):
    """The checks job checks out the whole history for the secret scan. The base
    is in it, so nothing is fetched: a fetch at depth 1 would cut the history off
    at the base, and the scan after it would read every older commit as new."""
    shas = pull_request(repo)
    count = git(repo, "rev-list", "--count", "HEAD")
    git(repo, "remote", "add", "origin", "file:///nowhere")  # a fetch would fail
    res = stubs.run(
        step("code-rules.sh", "python"),
        repo,
        **LINT_ENV,
        EVENT="pull_request",
        PR_BASE=shas["stale"],
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert f"code rules base: {shas['moved']}, the first parent of the merge" in (
        res.stdout
    )
    assert "code_rules: 1 changed file(s) checked" in res.stdout
    assert git(repo, "rev-parse", "--is-shallow-repository") == "false"
    assert git(repo, "rev-list", "--count", "HEAD") == count


def test_a_complete_clone_fetches_a_missing_base_without_a_depth(repo, stubs, tmp_path):
    """A force push: the commit before it is not in the clone. It is fetched whole,
    so the clone stays complete."""
    before = commit(repo, {"old.sh": "echo old\n"}, "the commit a force push drops")
    git(repo, "update-ref", "refs/dropped", before)
    git(repo, "reset", "-q", "--hard", "HEAD~1")
    commit(repo, {"run.sh": "echo hi\n"}, "the forced push")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "--branch", "main", f"file://{repo}", str(clone))
    assert git(clone, "rev-list", "--count", "HEAD") == "2"
    res = stubs.run(
        step("code-rules.sh", "python"), clone, **LINT_ENV, EVENT="push", BEFORE=before
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert f"code rules base: {before}, the commit before the push" in res.stdout
    assert "could not fetch" not in res.stdout
    assert git(clone, "cat-file", "-t", before) == "commit"
    assert git(clone, "rev-parse", "--is-shallow-repository") == "false"
    assert git(clone, "rev-list", "--count", "HEAD") == "2"


def test_a_shallow_clone_fetches_the_base_alone_as_the_workflows_always_did(
    repo, stubs, tmp_path
):
    before = git(repo, "rev-parse", "HEAD")
    commit(repo, {"run.sh": "echo hi\n"})
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "--depth=1", f"file://{repo}", str(clone))
    res = stubs.run(
        step("code-rules.sh", "python"), clone, **LINT_ENV, EVENT="push", BEFORE=before
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert "code_rules: 1 changed file(s) checked" in res.stdout
    assert git(clone, "rev-list", "--count", "--all") == "1"  # still one commit deep
    assert git(clone, "cat-file", "-t", before) == "commit"


def test_format_only_is_python_lints_and_says_when_it_proves_nothing(repo, stubs):
    env = {**LINT_ENV, "MODE": "all", "FORMAT_ONLY": "true", "EVENT": "push"}
    res = stubs.run(step("code-rules.sh", "python"), repo, **env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert "::notice::format-only proves a pull request; this is a push" in res.stdout
