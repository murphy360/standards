"""A workflow and its tools come from the same commit.

Each check workflow that runs a tool (standards-check, python-lint, node-lint,
type-check, shell-lint, actionlint, secret-scan) fetches the standards' tools at
``job.workflow_sha``, the commit the workflow file itself was called at, into the
runner's temp directory. The step's shell is run here as the workflow holds it, against
a local repository that stands in for GitHub: no network.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
TOOL = {
    "standards-check": "check_standards.py",
    "python-lint": "code_rules.py",
    "node-lint": "code_rules.py",
    "type-check": "type_check.py",
    "shell-lint": "image-ref.sh",
    "actionlint": "image-ref.sh",
    "secret-scan": "image-ref.sh",
}
CHECKS = tuple(TOOL)
STEP = "- name: The standards' tools, from the commit this workflow was called at\n"
REPOSITORY = "murphy360/standards"


def workflow(name: str) -> str:
    return (WORKFLOWS / f"{name}.yml").read_text()


def tools_step(name: str) -> str:
    """The whole step, from its name to the line before the next step."""
    return workflow(name).split(STEP, 1)[1].split("\n      - ", 1)[0]


def tools_shell(name: str) -> str:
    """The shell of the step, as the workflow holds it."""
    lines = tools_step(name).split("run: |\n", 1)[1].splitlines()
    assert all(line.startswith(" " * 10) for line in lines if line.strip())
    return "\n".join(line[10:] for line in lines) + "\n"


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def server(tmp_path) -> dict:
    """A stand-in for GitHub: the standards with two commits, and v2 at the second.

    GitHub serves any commit by its SHA, so the stand-in is told to as well.
    """
    origin = tmp_path / "server" / REPOSITORY
    origin.mkdir(parents=True)
    git(origin, "init", "-q", "-b", "main")
    git(origin, "config", "user.name", "Standards Tests")
    git(origin, "config", "user.email", "tests@example.com")
    git(origin, "config", "uploadpack.allowAnySHA1InWant", "true")
    shas = []
    for release in ("one", "two"):
        (origin / "tools").mkdir(exist_ok=True)
        (origin / "tools" / "release.txt").write_text(release + "\n")
        git(origin, "add", "-A")
        git(origin, "commit", "-q", "-m", release)
        shas.append(git(origin, "rev-parse", "HEAD").strip())
    git(origin, "tag", "v2")
    return {"url": f"file://{tmp_path / 'server'}", "one": shas[0], "two": shas[1]}


def run_step(tmp_path, server: dict, name: str = "python-lint", **given: str):
    """Run the step in a runner's temp directory; return the result and the tools."""
    temp = tmp_path / "runner-temp"
    temp.mkdir(exist_ok=True)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "SERVER_URL": server["url"],
        "WORKFLOW_REPOSITORY": REPOSITORY,
        "WORKFLOW_SHA": "",
        "WORKFLOW_REF": "",
        "STANDARDS_REF": "",
        **given,
    }
    res = subprocess.run(
        ["bash", "-e", "-c", tools_shell(name)],
        cwd=temp,
        env=env,
        capture_output=True,
        text=True,
    )
    return res, temp / "standards"


def release(tools: Path) -> str:
    return (tools / "tools" / "release.txt").read_text().strip()


@pytest.mark.parametrize("name", CHECKS)
def test_the_tools_are_the_commit_the_workflow_was_called_at(tmp_path, server, name):
    """Not the newest commit and not the tag: the one commit the caller pinned."""
    ref = f"{REPOSITORY}/.github/workflows/{name}.yml@{server['one']}"
    res, tools = run_step(
        tmp_path, server, name, WORKFLOW_SHA=server["one"], WORKFLOW_REF=ref
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert release(tools) == "one"
    assert git(tools, "rev-parse", "HEAD").strip() == server["one"]
    assert f"standards tools: {REPOSITORY}@{server['one']} ({ref})" in res.stdout


def test_the_log_prints_the_commit_behind_a_tag(tmp_path, server):
    """A caller at @v2: the workflow ref names the tag, the log names the commit."""
    ref = f"{REPOSITORY}/.github/workflows/python-lint.yml@refs/tags/v2"
    res, tools = run_step(
        tmp_path, server, WORKFLOW_SHA=server["two"], WORKFLOW_REF=ref
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert release(tools) == "two"
    assert f"standards tools: {REPOSITORY}@{server['two']} ({ref})" in res.stdout


def test_standards_ref_does_not_move_the_tools_off_the_workflows_commit(
    tmp_path, server
):
    """A caller that still passes standards-ref keeps working; it changes nothing."""
    res, tools = run_step(
        tmp_path, server, WORKFLOW_SHA=server["one"], STANDARDS_REF="v2"
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert release(tools) == "one"
    assert "::warning::" not in res.stdout


def test_standards_ref_is_read_only_when_github_names_no_commit(tmp_path, server):
    res, tools = run_step(tmp_path, server, WORKFLOW_REPOSITORY="", STANDARDS_REF="v2")
    assert res.returncode == 0, res.stdout + res.stderr
    assert release(tools) == "two"
    assert "::warning::" in res.stdout and "standards-ref, v2" in res.stdout
    assert (
        f"standards tools: {REPOSITORY}@{server['two']} (standards-ref v2)"
        in res.stdout
    )


def test_no_commit_and_no_standards_ref_fails_and_says_what_to_pass(tmp_path, server):
    """Never a silent guess at a version: there is no default ref to fall back on."""
    res, tools = run_step(tmp_path, server)
    assert res.returncode == 1
    assert "::error::" in res.stdout and "Pass standards-ref" in res.stdout
    assert not tools.exists()


def test_a_runner_that_kept_its_temp_directory_gets_the_new_commit(tmp_path, server):
    first, tools = run_step(tmp_path, server, WORKFLOW_SHA=server["two"])
    assert first.returncode == 0 and release(tools) == "two"
    (tools / "left-over.txt").write_text("from the job before\n")
    second, tools = run_step(tmp_path, server, WORKFLOW_SHA=server["one"])
    assert second.returncode == 0, second.stdout + second.stderr
    assert release(tools) == "one" and not (tools / "left-over.txt").exists()


def test_the_step_is_the_same_in_every_check_workflow():
    steps = {tools_step(name) for name in CHECKS}
    assert len(steps) == 1
    step = steps.pop()
    assert "working-directory: ${{ runner.temp }}" in step
    assert "WORKFLOW_SHA: ${{ fromJSON(toJSON(job)).workflow_sha }}" in step
    assert (
        "WORKFLOW_REPOSITORY: ${{ fromJSON(toJSON(job)).workflow_repository }}" in step
    )


@pytest.mark.parametrize("name", CHECKS)
def test_no_default_ref_is_left_to_edit_at_a_major_release(name):
    text = workflow(name)
    lines = text.split("      standards-ref:\n", 1)[1].splitlines()[:3]
    declared = [line.strip() for line in lines]
    assert declared[:2] == ["type: string", 'default: ""']
    assert declared[2].startswith('description: "deprecated')
    assert not re.search(r"ref: v\d", text)
    # the tools are fetched once, by the step above, and never by a pinned checkout
    assert "repository: murphy360/standards" not in text
    assert text.count("inputs.standards-ref") == 1


@pytest.mark.parametrize("name", CHECKS)
def test_the_tools_run_from_the_runners_temp_directory(name):
    text = workflow(name)
    assert ".standards/" not in text
    assert f'"$RUNNER_TEMP/standards/tools/{TOOL[name]}"' in text
    assert text.index(STEP) < text.index("$RUNNER_TEMP/standards/tools/")


def test_nothing_expects_the_tools_in_the_projects_workspace():
    for rel in (".gitignore", "templates/eslint.config.mjs", "templates/ci.yml"):
        assert ".standards" not in (ROOT / rel).read_text(), rel


def test_this_repositorys_own_ci_passes_no_ref():
    """A local call's workflow commit is the commit under test: nothing to pass."""
    text = workflow("ci")
    assert "uses: ./.github/workflows/python-lint.yml" in text
    assert "uses: ./.github/workflows/standards-check.yml" in text
    assert "standards-ref" not in text
