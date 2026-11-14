"""One job runs the quick checks: ``actions/checks``, ``actions/take-ownership``
and ``tools/run_checks.py``.

The action is one step of a project's own job. ``run_checks.py`` runs each check
the step names, whatever the one before it did, and fails at the end. The checks'
own shell is in ``tools/steps`` (``test_steps.py``). Here: the aggregation, with a
runner that only records; one run of the real scripts on a scratch project with
one finding for each tool; the action's inputs against the reusable workflows';
and what reads a project that adopts the standard this way (``check_standards.py``,
``fleet.py``, the rehearsal). No network: ``docker`` and ``pip`` are stand-ins.
"""

import importlib
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))
run_checks = importlib.import_module("run_checks")
fleet = importlib.import_module("fleet")
fleet_rehearsal = importlib.import_module("fleet_rehearsal")
check_standards = importlib.import_module("check_standards")
ruff_version = importlib.import_module("ruff_version")

WORKFLOWS = ROOT / ".github" / "workflows"
ACTIONS = ROOT / "actions"
ACTION = (ACTIONS / "checks" / "action.yml").read_text()
OWNERSHIP = (ACTIONS / "take-ownership" / "action.yml").read_text()
TEMPLATE = (ROOT / "templates" / "ci.yml").read_text()
CI = (WORKFLOWS / "ci.yml").read_text()
needs_ruff = pytest.mark.skipif(shutil.which("ruff") is None, reason="no ruff")
SHA = "0123456789abcdef0123456789abcdef01234567"
# The reusable workflow that runs the same check as each part of the action.
REPLACED = (
    "standards-check",
    "python-lint",
    "node-lint",
    "shell-lint",
    "actionlint",
    "secret-scan",
)
NOT_AN_ACTION_INPUT = {"runs-on", "standards-ref"}  # the job's own, and not needed
ENV_OF = {
    "checks": "CHECKS",
    "working-directory": "WORKING_DIRECTORY",
    "mode": "MODE",
    "paths": "PATHS",
    "format-check": "FORMAT_CHECK",
    "format-only": "FORMAT_ONLY",
    "no-inline-config": "NO_INLINE_CONFIG",
    "silencers": "SILENCERS",
    "ruff-version": "CODE_RULES_RUFF_VERSION",
    "project-ruff": "CODE_RULES_PROJECT_RUFF",
    "check-pr-template": "CHECK_PR_TEMPLATE",
    "check-pre-commit-config": "CHECK_PRE_COMMIT_CONFIG",
    "check-ticket-template": "CHECK_TICKET_TEMPLATE",
    "check-templates": "CHECK_TEMPLATES",
    "check-agent-settings": "CHECK_AGENT_SETTINGS",
    "check-ruff-version": "CHECK_RUFF_VERSION",
    "shellcheck-version": "SHELLCHECK_VERSION",
    "actionlint-version": "ACTIONLINT_VERSION",
    "full-history": "FULL_HISTORY",
}
INLINE_INPUT_RE = re.compile(
    r'^      ([\w-]+): \{type: \w+, default: (?:"([^"]*)"|(\w+))', re.MULTILINE
)
BLOCK_INPUT_RE = re.compile(
    r'^      ([\w-]+):\n        type: \w+\n        default: (?:"([^"]*)"|(\w+))',
    re.MULTILINE,
)
ACTION_INPUT_RE = re.compile(
    r'^  ([\w-]+):\n    description: "[^"\n]+"\n    default: "([^"\n]*)"$', re.MULTILINE
)
PIN_RE = re.compile(r"uses: ([\w./-]+)@([0-9a-f]{40})  # (v[\d.]+)$", re.MULTILINE)


# --- the aggregation: every check runs, and the worst exit code is the run's ---


class Recorder:
    """Stands in for the runner of a command: records it, answers as told."""

    def __init__(self, codes: dict[str, int] | None = None):
        self.codes = codes or {}
        self.ran: list[tuple[str, str]] = []

    def __call__(self, argv: list[str], cwd: str) -> int:
        words = [Path(word).name for word in argv if word]
        self.ran.append((" ".join(words[1:]), cwd))
        return next((code for key, code in self.codes.items() if key in words), 0)


def run(argv=(), codes=None, **env: str):
    """``run_checks.main`` with a recorder; its exit code and the recorder."""
    recorder = Recorder(codes)
    return run_checks.main(list(argv), run=recorder, env=env), recorder


def test_every_check_runs_though_the_first_one_failed(capsys):
    code, recorder = run(codes={"standards-check.sh": 1})
    assert code == 1
    assert [what for what, _ in recorder.ran] == [
        "standards-check.sh",
        "ruff_version.py --install",
        "code-rules.sh python",
        "shell-lint.sh",
        "actionlint.sh",
        "secret-scan.sh",
    ]
    out = capsys.readouterr().out.splitlines()
    assert out[-6:] == [
        "checks: standards-check: FAILED (exit 1), 0s",
        "checks: python-lint: passed, 0s",
        "checks: shell-lint: passed, 0s",
        "checks: actionlint: passed, 0s",
        "checks: secret-scan: passed, 0s",
        "checks: FAILED, 1 of 5: standards-check",
    ]


def test_the_exit_code_is_the_worst_one_and_the_summary_names_each_failure(capsys):
    codes = {"code-rules.sh": 1, "secret-scan.sh": 125, "actionlint.sh": 3}
    code, recorder = run(codes=codes)
    assert code == 125  # the highest: docker's, which says gitleaks did not run
    assert len(recorder.ran) == 6  # nothing was skipped
    out = capsys.readouterr().out
    assert "checks: python-lint: FAILED (exit 1), " in out
    assert "checks: actionlint: FAILED (exit 3), " in out
    assert "checks: secret-scan: FAILED (exit 125), " in out
    assert out.splitlines()[-1] == (
        "checks: FAILED, 3 of 5: python-lint, actionlint, secret-scan"
    )


def test_a_clean_run_passes_and_says_how_many(capsys):
    code, _ = run()
    assert code == 0
    assert capsys.readouterr().out.splitlines()[-1] == "checks: OK, 5 of 5 passed"


def test_inside_one_check_the_commands_stop_at_the_first_failure():
    """No lint without its install: the check failed there, and the next check runs."""
    code, recorder = run(["node-lint", "shell-lint"], codes={"npm": 7})
    assert code == 7
    assert [what for what, _ in recorder.ran] == [
        "ruff_version.py --install",
        "ci",  # npm ci
        "shell-lint.sh",
    ]


def test_the_lints_run_in_the_working_directory_and_the_rest_in_the_root():
    _, recorder = run(
        ["standards-check", "python-lint", "node-lint", "secret-scan"],
        WORKING_DIRECTORY="frontend",
    )
    assert recorder.ran == [
        ("standards-check.sh", "."),
        ("ruff_version.py --install", "frontend"),
        ("code-rules.sh python", "frontend"),
        ("ruff_version.py --install", "frontend"),
        ("ci", "frontend"),
        ("code-rules.sh node", "frontend"),
        ("secret-scan.sh", "."),
    ]


def test_each_command_is_a_file_that_exists_run_the_way_a_workflow_runs_it():
    env = {"SHELLCHECK_VERSION": "v0.9.0", "ACTIONLINT_VERSION": "1.7.7"}
    for part in run_checks.PARTS:
        for argv, _ in run_checks.commands(part, env):
            if argv[0] == "npm":
                assert argv == ["npm", "ci"]
                continue
            assert argv[0] in ("bash", sys.executable), argv
            assert Path(argv[1]).is_file(), argv[1]
    (shell,), (actions,) = (
        run_checks.commands(part, env) for part in ("shell-lint", "actionlint")
    )
    assert shell[0][2] == "v0.9.0" and actions[0][2] == "1.7.7"
    assert run_checks.commands("shell-lint", {})[0][0][2] == ""  # the pinned release


def test_a_name_that_is_no_check_fails_and_the_others_still_run(capsys):
    code, recorder = run(["shelllint", "actionlint"])
    assert code == 2
    assert recorder.ran == [("actionlint.sh", ".")]
    out = capsys.readouterr().out
    assert "`shelllint` is not a check. The checks: standards-check, python-lint" in out
    assert out.splitlines()[-1] == "checks: FAILED, 1 of 2: shelllint"


@pytest.mark.parametrize(
    ("argv", "env", "names"),
    [
        ([], {}, run_checks.DEFAULT.split()),
        ([], {"CHECKS": ""}, run_checks.DEFAULT.split()),
        ([], {"CHECKS": "  "}, run_checks.DEFAULT.split()),
        ([], {"CHECKS": "node-lint  actionlint"}, ["node-lint", "actionlint"]),
        (
            [],
            {"CHECKS": "actionlint,shell-lint\nactionlint"},
            ["actionlint", "shell-lint"],
        ),
        (["secret-scan"], {"CHECKS": "node-lint"}, ["secret-scan"]),
    ],
)
def test_the_checks_are_the_arguments_or_the_input_or_the_default(argv, env, names):
    assert run_checks.named(argv, env) == names
    assert set(run_checks.DEFAULT.split()) < set(run_checks.PARTS)
    assert "node-lint" not in run_checks.DEFAULT  # it lints Python files too


def test_on_github_each_check_is_a_group_and_each_failure_an_annotation(
    capsys, tmp_path
):
    summary = tmp_path / "summary.md"
    code, _ = run(
        ["shell-lint", "actionlint"],
        codes={"actionlint.sh": 1},
        GITHUB_ACTIONS="true",
        GITHUB_STEP_SUMMARY=str(summary),
    )
    assert code == 1
    out = capsys.readouterr().out.splitlines()
    assert out[:4] == [
        "::group::checks: shell-lint",
        "::endgroup::",
        "::group::checks: actionlint",
        "::endgroup::",
    ]
    assert out[-1] == (
        "::error title=checks::actionlint failed (exit 1). Its lines are in the "
        'group "checks: actionlint" of this step\'s log.'
    )
    assert summary.read_text().splitlines() == [
        "### checks",
        "",
        "| check | result | seconds |",
        "|---|---|---|",
        "| shell-lint | passed | 0 |",
        "| actionlint | FAILED (exit 1) | 0 |",
    ]


def test_off_github_there_is_no_group_and_no_annotation(capsys):
    assert run(["actionlint"], codes={"actionlint.sh": 1})[0] == 1
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "checks: actionlint"
    assert "::" not in out


def test_a_command_that_cannot_start_or_is_killed_fails_its_check(tmp_path, capsys):
    assert run_checks.run_command(["no-such-program-here"], str(tmp_path)) == 127
    assert "`no-such-program-here` did not start" in capsys.readouterr().out
    missing = str(tmp_path / "no-such-directory")
    assert run_checks.run_command(["true"], missing) == 127
    assert run_checks.run_command(["sh", "-c", "kill -9 $$"], str(tmp_path)) == 137
    assert run_checks.run_command(["sh", "-c", "exit 3"], str(tmp_path)) == 3
    assert run_checks.worst([]) == 0


# --- the real scripts, on a scratch project with one finding for each tool ---

STUB_DOCKER = """#!/bin/sh
# Stands in for docker: says which tool it was asked for, exits as the test says.
for word in "$@"; do
  case "$word" in
    *shellcheck* | *actionlint* | *gitleaks*) echo "stub docker: $word" ;;
  esac
done
exit "${STUB_EXIT:-0}"
"""
DEPENDABOT = (
    "version: 2\nupdates:\n  - package-ecosystem: github-actions\n    directory: /\n"
)


def scratch_project(tmp_path: Path, files: dict[str, str | None]) -> Path:
    """A project made from the CI template, committed, with a stub docker and a
    stub pip beside it. A file given as None is left out."""
    root = tmp_path / "project"
    given = {
        "AGENTS.md": "# A project\n",
        ".github/dependabot.yml": DEPENDABOT,
        ".github/workflows/ci.yml": TEMPLATE,
        "run.sh": "#!/bin/sh\necho hi\n",
        **files,
    }
    for rel, text in given.items():
        if text is not None:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com"]
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-qm", "x"]):
        subprocess.run([*git, *args], cwd=root, check=True)
    stub = tmp_path / "stub"
    (stub / "bin").mkdir(parents=True)
    (stub / "bin" / "docker").write_text(STUB_DOCKER)
    (stub / "bin" / "docker").chmod(0o755)
    (stub / "pip").mkdir()
    (stub / "pip" / "__init__.py").write_text("")
    (stub / "pip" / "__main__.py").write_text("print('stub pip')\n")
    return root


def run_for_real(root: Path, stub_exit: str):
    stub = root.parent / "stub"
    env = {
        "PATH": f"{stub / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(root.parent),
        "PYTHONPATH": str(stub),  # `python -m pip` is the stand-in: no network
        "RUNNER_TEMP": str(root.parent / "runner-temp"),
        "STUB_EXIT": stub_exit,
        "EVENT": "schedule",
        "MODE": "all",
        "FORMAT_CHECK": "true",
    }
    (root.parent / "runner-temp").mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, str(TOOLS / "run_checks.py")],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )


@needs_ruff
def test_one_run_reports_a_finding_of_every_tool(tmp_path):
    """The ticket's fixture: one finding for each tool, and all of them in one run.
    ruff and check_standards.py are the real ones. shellcheck, actionlint and
    gitleaks are images: the stand-in exits 1, as each does on a finding."""
    files = {
        "AGENTS.md": None,  # standards-check: no instruction file
        "unused.py": "import os\n",  # ruff: F401
    }
    res = run_for_real(scratch_project(tmp_path, files), "1")
    out = res.stdout
    assert res.returncode == 123, out + res.stderr  # xargs', the highest of the five
    assert "AGENTS.md is missing" in out and "standards: FAILED, 1 gap(s)" in out
    assert "unused.py:1:8: F401" in out and "code_rules: FAILED" in out
    assert "stub docker: koalaman/shellcheck" in out
    assert "stub docker: rhysd/actionlint" in out
    assert "stub docker: ghcr.io/gitleaks/gitleaks" in out and "revoke it first" in out
    last = [re.sub(r", \d+s$", "", line) for line in out.splitlines()[-6:]]
    assert last == [
        "checks: standards-check: FAILED (exit 1)",
        "checks: python-lint: FAILED (exit 1)",
        "checks: shell-lint: FAILED (exit 123)",
        "checks: actionlint: FAILED (exit 1)",
        "checks: secret-scan: FAILED (exit 1)",
        "checks: FAILED, 5 of 5: "
        "standards-check, python-lint, shell-lint, actionlint, secret-scan",
    ]
    # each check's lines come before the next check starts
    later = run_checks.DEFAULT.split()[1:]
    order = [out.index(f"\nchecks: {part}\n") for part in later]
    assert order == sorted(order)
    assert out.index("standards: FAILED") < order[0] < out.index("code_rules: FAILED")


@needs_ruff
def test_a_project_made_from_the_template_passes_every_check(tmp_path):
    root = scratch_project(tmp_path, {"clean.py": "x = 1\n"})
    res = run_for_real(root, "0")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "standards: OK" in res.stdout and "code_rules: OK" in res.stdout
    assert "secret scan: no finding" in res.stdout
    assert res.stdout.splitlines()[-1] == "checks: OK, 5 of 5 passed"


# --- the action: its inputs are the workflows', and each reaches its script ---


def workflow_inputs(workflow: str) -> dict[str, str]:
    """The inputs of a reusable workflow, each with its default as text."""
    text = (WORKFLOWS / f"{workflow}.yml").read_text()
    text = text.split("    inputs:\n", 1)[1].split("\npermissions:", 1)[0]
    found = INLINE_INPUT_RE.findall(text) + BLOCK_INPUT_RE.findall(text)
    declared = re.findall(r"^      ([\w-]+):", text, re.MULTILINE)
    assert sorted(name for name, _, _ in found) == sorted(declared), workflow
    return {name: quoted or bare for name, quoted, bare in found}


def action_inputs() -> dict[str, str]:
    block = ACTION.split("\ninputs:\n", 1)[1].split("\nruns:\n", 1)[0]
    found = dict(ACTION_INPUT_RE.findall(block))
    assert sorted(found) == sorted(re.findall(r"^  ([\w-]+):", block, re.MULTILINE))
    return found


@pytest.mark.parametrize("workflow", REPLACED)
def test_every_input_of_a_workflow_has_its_equal_in_the_action(workflow):
    """Same name, same default: a project moves its `with:` lines as they are."""
    ours = action_inputs()
    theirs = workflow_inputs(workflow)
    assert set(theirs) >= NOT_AN_ACTION_INPUT  # every check workflow has both
    for name, default in theirs.items():
        if name in NOT_AN_ACTION_INPUT:
            continue
        assert name in ours, f"{workflow}.yml takes `{name}` and the action does not"
        assert ours[name] == default, f"`{name}`: {ours[name]!r} is not {default!r}"


def test_the_action_takes_nothing_a_workflow_does_not_but_the_list_of_checks():
    known = set().union(*(workflow_inputs(name) for name in REPLACED))
    assert set(action_inputs()) - known == {"checks"}
    assert not NOT_AN_ACTION_INPUT & set(action_inputs())
    assert action_inputs()["checks"] == run_checks.DEFAULT
    listed = ACTION.split("  checks:\n    description: ", 1)[1].splitlines()[0]
    assert all(part in listed for part in run_checks.PARTS)


def test_each_input_reaches_the_script_under_the_name_the_script_reads():
    step = ACTION.split("    - name: The checks, each one run, then one result\n")[1]
    for name, variable in ENV_OF.items():
        line = f"        {variable}: ${{{{ inputs.{name} }}}}"
        assert re.search(rf"^{re.escape(line)}(  #.*)?$", step, re.MULTILINE), name
    assert set(ENV_OF) | {"python-version", "node-version"} == set(action_inputs())
    assert "python-version: ${{ inputs.python-version }}" in ACTION
    assert "node-version: ${{ inputs.node-version }}" in ACTION
    # what a workflow's steps read of the event, the action's step reads the same
    for workflow in ("python-lint", "secret-scan"):
        text = (WORKFLOWS / f"{workflow}.yml").read_text()
        for variable in ("EVENT", "PR_BASE", "BEFORE"):
            line = re.search(rf"^ +{variable}: (.*)$", text, re.MULTILINE).group(1)
            assert f"        {variable}: {line}\n" in step, variable
    # every variable a step script reads is one the action's step sets
    scripts = "".join(p.read_text() for p in sorted((TOOLS / "steps").glob("*.sh")))
    read = set(re.findall(r'"\$([A-Z][A-Z_]+)"', scripts))
    own = {"GITHUB_WORKSPACE", "GITLEAKS_IMAGE", "PWD"}  # the runner's, a script's
    assert read - own <= set(ENV_OF.values()) | {"EVENT", "PR_BASE", "BEFORE"}


def test_the_action_runs_its_own_tools_and_fetches_nothing():
    """An action finds its files through github.action_path: the tools are those of
    the ref the project pinned, with no second fetch and no job.workflow_sha."""
    for text in (ACTION, OWNERSHIP):
        assert "using: composite" in text
        assert "        ACTION_PATH: ${{ github.action_path }}\n" in text
        assert "workflow_sha" not in text and "git fetch" not in text
        assert "RUNNER_TEMP" not in text
        runs = re.findall(r"^      run: (.*)$", text, re.MULTILINE)
        shells = re.findall(r"^      shell: (\w+)$", text, re.MULTILINE)
        assert len(runs) == len(shells) == 1  # a composite step names its shell
    assert '      run: python3 "$ACTION_PATH/../../tools/run_checks.py"\n' in ACTION
    assert (ACTIONS / "checks" / ".." / ".." / "tools" / "run_checks.py").is_file()
    assert (
        '      run: sh "$ACTION_PATH/../../tools/steps/take-ownership.sh"\n'
        in OWNERSHIP
    )


def test_take_ownership_acts_on_a_self_hosted_runner_only():
    step = OWNERSHIP.split("\nruns:\n", 1)[1]
    assert "      if: runner.environment == 'self-hosted'\n" in step
    assert step.count("    - ") == 1
    workflow = (WORKFLOWS / "shell-lint.yml").read_text()
    name = "name: Take ownership of the previous job's files (a self-hosted runner)"
    assert name in step and name in workflow  # the same step, by the same name


def test_node_is_set_up_only_for_node_lint():
    step = ACTION.split("    - if: contains(inputs.checks, 'node-lint')\n", 1)[1]
    assert step.startswith("      uses: actions/setup-node@")
    assert "    - uses: actions/setup-python@" in ACTION


def pins(text: str) -> dict[str, tuple[str, str]]:
    return {action: (sha, version) for action, sha, version in PIN_RE.findall(text)}


def test_an_action_is_one_commit_everywhere():
    """A third-party action inside an action is pinned like any other, and to the
    commit the workflows pin. Dependabot reads each action.yml in its directory."""
    workflows = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        workflows.update(pins(path.read_text()))
    inside = pins(ACTION)
    assert set(inside) == {"actions/setup-python", "actions/setup-node"}
    for action, pin in inside.items():
        assert pin == workflows[action], (
            f"actions/checks/action.yml pins {action} at {pin[1]} and the workflows "
            f"at {workflows[action][1]}: set both to the same commit"
        )
    assert check_standards.unpinned(ROOT, check_standards.tracked(ROOT)) == []
    entries = check_standards.dependabot_entries(
        (ROOT / ".github" / "dependabot.yml").read_text()
    )
    assert ("github-actions", "/") in entries
    assert ("github-actions", "/actions/*") in entries
    names = sorted(p.parent.name for p in ACTIONS.glob("*/action.yml"))
    assert names == ["checks", "take-ownership"]  # no action deeper than /actions/*


def test_the_template_pins_checkout_at_the_commit_this_repository_does():
    """Dependabot does not read templates/: a pull request that moves
    actions/checkout here moves the template with it."""
    ours, theirs = pins(CI)["actions/checkout"], pins(TEMPLATE)["actions/checkout"]
    assert theirs == ours, (
        f"templates/ci.yml pins actions/checkout at {theirs[1]} ({theirs[0]}) and "
        f"ci.yml at {ours[1]} ({ours[0]}): set the template to the same commit"
    )


# --- check_standards.py: the standard's actions at a version tag ---


@pytest.mark.parametrize(
    ("uses", "allowed"),
    [
        ("murphy360/standards/actions/checks@v2", True),
        ("murphy360/standards/actions/take-ownership@v2.4.0", True),
        (f"murphy360/standards/actions/checks@{SHA}", True),  # a commit SHA, as any
        ("murphy360/standards/actions/checks@main", False),
        ("murphy360/standards/actions/checks@v2-beta", False),
        ("murphy360/standards/actions@v2", False),
        ("someone/standards/actions/checks@v2", False),
        ("murphy360/other/actions/checks@v2", False),
        ("actions/checkout@v7", False),
    ],
)
def test_a_standards_action_is_allowed_at_a_version_tag(tmp_path, uses, allowed):
    step = f"jobs:\n  checks:\n    steps:\n      - uses: {uses}\n"
    files = {".github/workflows/ci.yml": step, "actions/mine/action.yml": step}
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True)
        (tmp_path / rel).write_text(text)
    found = check_standards.unpinned(tmp_path, list(files))
    if allowed:
        assert found == []
    else:
        assert found == [
            f"{rel}:4: `{uses}` is not pinned to a commit SHA" for rel in files
        ]


def test_a_project_made_from_the_template_follows_the_standards(tmp_path):
    root = scratch_project(tmp_path, {})
    assert check_standards.check(root) == []
    live = [line for line in TEMPLATE.splitlines() if not line.lstrip().startswith("#")]
    reusable = [line for line in live if "/.github/workflows/" in line]
    assert [line.split("/")[-1] for line in reusable] == [
        "test-docker.yml@v2",
        "image.yml@v2",
    ]  # the five checks are one job now; the old jobs are a note


# --- fleet.py and the rehearsal: an adopter through the actions ---

ONE_JOB = f"""\
jobs:
  checks:
    runs-on: [self-hosted, linux]
    timeout-minutes: 15
    steps:
      - uses: murphy360/standards/actions/take-ownership@v2
      - uses: actions/checkout@{SHA}  # v7.0.1
        with:
          fetch-depth: 0
      - name: The quick checks
        uses: "murphy360/standards/actions/checks@{SHA}"  # v2.4.0
        with:
          checks: standards-check node-lint shell-lint  # no actionlint here
          mode: changed
          working-directory: web
          check-templates: true
      # - uses: murphy360/standards/actions/checks@v1
      - if: ${{{{ !cancelled() }}}}
        uses: murphy360/standards/actions/checks@v2
        with: {{checks: python-lint, project-ruff: 'true', paths: "src tools"}}
      - uses: someone/else/actions/checks@v2
      - run: echo done
  result:
    needs: [checks]
    runs-on: ubuntu-latest
"""


STEPS = "jobs:\n  checks:\n    steps:\n"


def test_steps_reads_each_step_that_uses_an_action_with_its_inputs():
    found = fleet.steps(ONE_JOB)
    assert [step["uses"].split("@")[0].strip('"') for step in found] == [
        "murphy360/standards/actions/take-ownership",
        "actions/checkout",
        "murphy360/standards/actions/checks",
        "murphy360/standards/actions/checks",
        "someone/else/actions/checks",
    ]  # not the commented-out step, and not the step that only runs a command
    assert found[1]["with"] == {"fetch-depth": "0"}
    assert found[2]["name"] == "The quick checks"
    assert found[2]["with"]["checks"] == "standards-check node-lint shell-lint"
    assert found[2]["with"]["mode"] == "changed"
    assert found[3]["with"] == {
        "checks": "python-lint",
        "project-ruff": "true",
        "paths": "src tools",
    }
    assert fleet.steps("name: ci\non: push\n") == []
    assert fleet.jobs(ONE_JOB)[0]["with"] == {}  # a step's `with:` is not the job's


def test_a_checks_step_counts_as_each_check_it_runs_at_the_steps_ref():
    calls = fleet.workflow_calls(ONE_JOB, 2)
    assert [(c["workflow"], c["ref"], c["kind"]) for c in calls] == [
        ("take-ownership", "v2", "current"),
        ("checks", SHA, "sha"),
        ("standards-check", SHA, "sha"),
        ("node-lint", SHA, "sha"),
        ("shell-lint", SHA, "sha"),
        ("checks", "v2", "current"),
        ("python-lint", "v2", "current"),
    ]
    assert {c["via"] for c in calls} == {"action"}
    assert calls[1]["version"] == "v2.4.0"  # the comment beside a SHA
    assert calls[3]["with"]["working-directory"] == "web"
    job = "jobs:\n  a:\n    uses: murphy360/standards/.github/workflows/image.yml@v2\n"
    assert [c["via"] for c in fleet.workflow_calls(job, 2)] == ["workflow"]


def test_the_default_list_and_a_list_only_a_run_resolves():
    step = STEPS + "      - uses: murphy360/standards/actions/checks@v2\n"
    names = [c["workflow"] for c in fleet.workflow_calls(step, 2)]
    assert names == ["checks", *run_checks.DEFAULT.split()]
    expr = step + "        with:\n          checks: ${{ vars.CHECKS }}\n"
    assert [c["workflow"] for c in fleet.workflow_calls(expr, 2)] == ["checks"]
    typo = step + "        with:\n          checks: shelllint actionlint\n"
    assert [c["workflow"] for c in fleet.workflow_calls(typo, 2)] == [
        "checks",
        "actionlint",
    ]


def test_the_standards_repository_uses_its_own_actions_by_path():
    step = STEPS + "      - uses: ./actions/checks\n"
    assert fleet.workflow_calls(step, 2) == []  # any other repository's own action
    calls = fleet.workflow_calls(step, 2, local=True)
    assert {(c["ref"], c["kind"]) for c in calls} == {("local", "current")}
    ours = [c for c in fleet.workflow_calls(CI, 2, local=True) if c["via"] == "action"]
    assert {c["workflow"] for c in ours} == {
        "take-ownership",
        "checks",
        *run_checks.PARTS,
    }


def node(name: str, ci: str) -> dict:
    """A repository as fleet.py's query returns it, with one workflow file."""
    entry = {"name": "ci.yml", "object": {"text": ci}}
    return {"name": name, "workflows": {"entries": [entry]}}


def test_the_report_sees_an_adopter_through_the_actions():
    now = datetime(2026, 11, 13, tzinfo=timezone.utc)
    rows = fleet.report(
        [node("one-job", ONE_JOB), node("template", TEMPLATE)], "someone", 2, now
    )
    one_job, template = rows
    assert one_job["kinds"] == ["current", "sha"] and one_job["one_job"] is True
    assert one_job["lint_mode"] == "all+changed"  # python-lint, and node-lint changed
    assert template["kinds"] == ["current"] and template["one_job"] is True
    assert template["lint_mode"] == "all" and template["format_only"] is True
    assert template["templates"]["result"] is True
    counts = fleet.summary(rows)
    assert (counts["adopters"], counts["one_job"], counts["sha"]) == (2, 2, 1)
    assert "  with the one checks job (the checks action): 2" in (
        fleet.summary_lines(counts, 2)
    )
    head = fleet.table(rows)[0].split()
    assert head[:8] == [
        "repository",
        "actionlint",
        "checks",
        "image",
        "node",
        "python",
        "secret-scan",
        "shell",
    ]
    cells = fleet.table(rows)[1].split()
    short = SHA[:7]
    assert cells[:8] == ["one-job", "-", f"{short},v2", "-", short, "v2", "-", short]


def test_the_rehearsal_runs_the_tools_the_way_a_checks_step_asks():
    row = {"calls": fleet.workflow_calls(ONE_JOB, 2)}
    assert fleet_rehearsal.plan(row) == [
        {
            "tool": "check_standards.py",
            "args": ["--require-current-templates"],
            "cwd": ".",
            "changed": False,
        },
        {
            "tool": "code_rules.py",
            "args": [
                "--all",
                "--no-format",
                *("--paths", "src", "tools"),
                "--project-ruff",
            ],
            "cwd": ".",
            "changed": False,
        },
    ]  # node-lint is not rehearsed, as for a caller of node-lint.yml


def test_the_ruff_check_reads_the_lint_of_a_checks_step(tmp_path):
    """check-ruff-version: a version the step passes is one more place that names
    ruff, as it is for a job that calls python-lint.yml."""
    ci = (
        STEPS + "      - uses: murphy360/standards/actions/checks@v2\n"
        "        with:\n          ruff-version: 0.9.2\n"
    )
    files = {".github/workflows/ci.yml": ci, "requirements-dev.txt": "ruff==0.8.0\n"}
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    (problem,) = ruff_version.pin_problems(tmp_path, list(files), fleet.workflow_calls)
    assert "0.8.0 in requirements-dev.txt" in problem
    assert "0.9.2 in .github/workflows/ci.yml, python-lint (given: ruff-version)" in (
        problem
    )


# --- the template, this repository's CI and the README ---


def test_the_template_has_one_checks_job_that_is_the_projects_own():
    job = TEMPLATE.split("\n  checks:\n", 1)[1].split("\n  test:\n", 1)[0]
    lines = [line for line in job.splitlines() if not line.lstrip().startswith("#")]
    assert lines[:2] == ["    runs-on: ubuntu-latest", "    timeout-minutes: 15"]
    uses = [line.split("uses: ")[1].split("@")[0] for line in lines if "uses: " in line]
    assert uses == [
        "murphy360/standards/actions/take-ownership",  # before the checkout
        "actions/checkout",
        "murphy360/standards/actions/checks",
    ]
    assert "runs-on: '[" not in TEMPLATE  # no JSON list: the job names its runner
    assert "    needs: [checks, test, image]\n" in TEMPLATE
    assert fleet.has_result_job(TEMPLATE)
    # every input a project may want is there to uncomment
    for name in set(action_inputs()) - {"python-version", "node-version"}:
        named = re.search(rf"^\s+(# )?{name}: ", job, re.MULTILINE)
        versions = name in ("shellcheck-version", "actionlint-version", "ruff-version")
        assert named or versions, f"templates/ci.yml does not show `{name}`"
    assert "They are removed in v3." in TEMPLATE  # the old jobs, as a note


def test_this_repository_runs_the_checks_the_way_the_template_does():
    job = CI.split("\n  checks:\n", 1)[1].split("\n  tests:\n", 1)[0]
    assert "      - uses: ./actions/take-ownership\n" in job
    assert job.count("        uses: ./actions/checks\n") == 1  # the second step
    assert job.count("      - uses: ./actions/checks\n") == 1  # the first
    assert "          fetch-depth: 0 " in job
    second = job.split("      - if: ${{ !cancelled() }}\n", 1)[1]
    assert "          checks: node-lint\n" in second
    assert "          working-directory: tests/fixtures/node-project\n" in second
