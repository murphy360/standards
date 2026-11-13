"""The settings template that turns the never-rules into refusals.

``templates/claude/settings.json`` runs ``templates/claude/guard.py`` before every
shell command of a Claude Code session. The guard is run here the way Claude Code
runs it: the documented ``PreToolUse`` input on stdin, the verdict in the exit code
(2 refuses, with the reason on stderr). Git runs on scratch repositories and
``pre-commit`` is a stub on PATH: no network.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates" / "claude"
GUARD = TEMPLATES / "guard.py"
SETTINGS = TEMPLATES / "settings.json"
WORKFLOW = ROOT / ".github" / "workflows" / "standards-check.yml"


def load(name: str, path: Path):
    """A file as a module. No bytecode is written: a ``__pycache__`` in templates/
    would be one more file a project copies."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    before, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = before
    return module


guard = load("guard", GUARD)
standards = load("check_standards", ROOT / "tools" / "check_standards.py")
rehearsal = load("fleet_rehearsal", ROOT / "tools" / "fleet_rehearsal.py")


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def project(tmp_path) -> Path:
    """A clone on the branch feat/x, with a remote whose default branch is main."""
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Standards Tests")
    git(root, "config", "user.email", "tests@example.com")
    (root / "README.md").write_text("a project\n")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("A = 1\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    git(root, "init", "-q", "--bare", str(tmp_path / "remote.git"))
    git(root, "remote", "add", "origin", str(tmp_path / "remote.git"))
    git(root, "push", "-q", "origin", "main")
    git(root, "checkout", "-q", "-b", "feat/x")
    return root


@pytest.fixture
def path(tmp_path) -> Path:
    """A PATH that holds git and nothing else, so ``pre-commit`` is what a test adds."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "git").symlink_to(shutil.which("git"))
    return bin_dir


def stub_pre_commit(bin_dir: Path, body: str) -> Path:
    """A ``pre-commit`` that logs how it was called, then runs ``body``."""
    log = bin_dir / "pre-commit.log"
    stub = bin_dir / "pre-commit"
    stub.write_text(f'#!/bin/sh\necho "$PWD: $*" >> "{log}"\n{body}\n')
    stub.chmod(0o755)
    return log


def hook(command: str, cwd: Path, bin_dir: Path | None = None, **env: str):
    """Runs the guard on one Bash call, with the input Claude Code documents."""
    event = {
        "session_id": "abc123",
        "transcript_path": "/home/user/.claude/projects/x/transcript.jsonl",
        "cwd": str(cwd),
        "permission_mode": "default",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command, "description": "a command"},
        "tool_use_id": "toolu_01ABC123",
    }
    environment = {**os.environ, **env}
    if bin_dir:
        environment["PATH"] = str(bin_dir)
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


STASH = ("never `git stash`", "commit what you need to keep to your branch")
DEFAULT = ("never push to the default branch", "open a pull request")
RESET = ("never `git reset --hard` or `--soft`", "merge the base into the branch")
ADMIN = ("`gh pr merge --admin`", "merge when the required checks are green")
NO_VERIFY = ("never skip the hooks with `--no-verify`", "fix what the hook reports")
FORCE = ("never force a push", "add a new commit")

REFUSED = [
    # git stash, in every form that saves
    (STASH, "git stash"),
    (STASH, "git stash push -m wip"),
    (STASH, "git stash save"),
    (STASH, "git stash -u"),
    (STASH, "git stash --include-untracked"),
    (STASH, "cd src && git stash"),
    (STASH, "ls; git stash"),
    (STASH, "ls\ngit stash"),
    (STASH, "git status | cat; git stash push"),
    (STASH, "(git stash)"),
    (STASH, 'echo "$(git stash)"'),
    (STASH, "echo `git stash`"),
    (STASH, "git -C src stash"),
    (STASH, "git -c core.editor=true stash"),
    (STASH, "bash -c 'git stash'"),
    (STASH, 'eval "git stash"'),
    (STASH, "if true; then git stash; fi"),
    (STASH, "for f in a b; do git stash; done"),
    (STASH, "git stash >/dev/null 2>&1"),
    (STASH, "FOO=1 /usr/bin/git stash"),
    (STASH, "timeout 30 git stash"),
    (STASH, "git stash # keep it for later"),
    # a push to the default branch
    (DEFAULT, "git push origin main"),
    (DEFAULT, "git push -u origin main"),
    (DEFAULT, "git push origin HEAD:main"),
    (DEFAULT, "git push origin feat/x:refs/heads/main"),
    (DEFAULT, "git push origin :main"),
    (DEFAULT, "git push origin master"),
    (DEFAULT, "git push --all"),
    (DEFAULT, "git add -A && git commit -m done && git push origin main"),
    (DEFAULT, "git -C src push origin HEAD:main"),
    (DEFAULT, "(cd src; git push origin main)"),
    # git reset --hard or --soft onto another commit
    (RESET, "git reset --hard origin/main"),
    (RESET, "git reset --soft origin/main"),
    (RESET, "git reset --soft HEAD~1"),
    (RESET, "git reset -q --hard ORIG_HEAD"),
    (RESET, "git fetch origin && git reset --hard origin/main"),
    (RESET, "git -C src reset --hard origin/main"),
    # a merge with an admin bypass
    (ADMIN, "gh pr merge --admin"),
    (ADMIN, "gh pr merge 5 --admin --squash"),
    (ADMIN, "gh pr merge 5 --squash -R owner/repo --admin"),
    (ADMIN, "gh pr checks 5; gh pr merge 5 --squash --admin"),
    (ADMIN, 'echo "$(gh pr merge 5 --admin)"'),
    # --no-verify on a commit or a push
    (NO_VERIFY, "git commit --no-verify -m x"),
    (NO_VERIFY, "git commit -m x --no-verify"),
    (NO_VERIFY, "git commit -n -m x"),
    (NO_VERIFY, "git commit -anm x"),
    (NO_VERIFY, "git commit -m x -n"),
    (NO_VERIFY, "git push --no-verify origin feat/x"),
    (NO_VERIFY, "git push --no-veri origin feat/x"),
    (NO_VERIFY, "git add -A && git commit -m x --no-verify"),
    (NO_VERIFY, "git -C src push origin feat/x --no-verify"),
    # a forced push, to any branch
    (FORCE, "git push --force origin feat/x"),
    (FORCE, "git push -f origin feat/x"),
    (FORCE, "git push -uf origin feat/x"),
    (FORCE, "git push --force-with-lease origin feat/x"),
    (FORCE, "git push --force-with-lease=feat/x:abc123 origin feat/x"),
    (FORCE, "git push --force-w origin feat/x"),
    (FORCE, "git push origin +feat/x"),
    (FORCE, "git push --mirror"),
    (FORCE, "git fetch origin && git push -f"),
    (FORCE, "git -C src push origin feat/x --force"),
]

ALLOWED = [
    "git stash list",
    "git stash show -p stash@{0}",
    "git stash pop",
    "ls && git stash list | head -3",
    'echo "git stash"',
    "echo git stash",
    'echo ";" git stash',
    'grep -rn "git stash" .',
    "git log --oneline | grep stash",
    "git log --grep='push --force'",
    "cat <<EOF\ngit stash\nEOF",
    "# git stash\nls",
    'git commit -m "say never git stash, never --no-verify, never push --force"',
    "git commit -m 'gh pr merge --admin is refused'",
    "git commit -m -n",
    "git commit -m \"$(cat <<'EOF'\nNever git stash.\n\ngit push -f origin main\n"
    'EOF\n)"',
    "git reset --hard",
    "git reset --hard HEAD",
    "git reset --soft HEAD",
    "git reset HEAD~1",
    "git reset -- src/a.py",
    "gh pr merge 5 --squash",
    'gh pr merge 5 --squash --body "merged without --admin"',
    "gh pr view 5 --json title",
    "git push -u origin feat/x",
    "git push origin HEAD",
    "git push",
    "git push origin feat/x 2>&1 | tail -3",
    "git push origin --tags",
    "git push origin feat/x:refs/heads/feat/main",
    "git push --no-force-with-lease origin feat/x",
    "git push -n origin feat/x",
    "ls -la",
    "",
]


@pytest.mark.parametrize(("rule", "command"), REFUSED, ids=[c for _, c in REFUSED])
def test_a_command_that_breaks_a_rule_is_refused(project, path, rule, command):
    name, instead = rule
    done = hook(command, project, path)
    assert done.returncode == 2, done.stderr
    assert done.stderr.startswith("Refused by guard.py: ")
    assert name in done.stderr
    assert "Instead: " in done.stderr and instead in done.stderr
    assert done.stdout == ""


@pytest.mark.parametrize("command", ALLOWED)
def test_a_command_that_only_mentions_the_words_goes_through(project, path, command):
    done = hook(command, project, path)
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")


def test_a_bare_push_is_refused_on_the_default_branch_only(project, path):
    assert hook("git push", project, path).returncode == 0
    git(project, "checkout", "-q", "main")
    for command in ("git push", "git push origin", "git push -u origin HEAD"):
        done = hook(command, project, path)
        assert done.returncode == 2, command
        assert DEFAULT[0] in done.stderr
    assert hook("git push origin main:feat/y", project, path).returncode == 0


def test_a_bare_push_goes_where_git_says_the_branch_pushes(project, path):
    git(project, "config", "push.default", "upstream")
    git(project, "fetch", "-q", "origin")
    git(project, "branch", "-q", "--set-upstream-to=origin/main")
    done = hook("git push", project, path)
    assert done.returncode == 2
    assert DEFAULT[0] in done.stderr


def test_the_default_branch_is_the_one_the_remote_names(project, path):
    assert hook("git push origin trunk", project, path).returncode == 0
    git(
        project,
        *("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk"),
    )
    done = hook("git push origin trunk", project, path)
    assert done.returncode == 2
    assert DEFAULT[0] in done.stderr


def test_the_guard_reads_a_command_the_way_a_shell_does():
    def commands(text):
        return guard.Scanner(text).run()

    assert commands("cd x && git stash; ls | wc -l") == [
        ["cd", "x"],
        ["git", "stash"],
        ["ls"],
        ["wc", "-l"],
    ]
    assert commands("echo 'a b' \"c $(git stash) d\" e\\ f") == [
        ["git", "stash"],
        ["echo", "a b", "c $() d", "e f"],
    ]
    assert commands("git stash 2>&1 >out.txt &") == [["git", "stash"]]
    assert commands("a <<-EOF | b\n\tgit stash\n\tEOF\nc") == [["a"], ["b"], ["c"]]
    assert commands("diff <(git stash list) x") == [
        ["diff"],
        ["git", "stash", "list"],
        ["x"],
    ]
    assert guard.unwrap(["A=1", "env", "B=2", "nice", "-n", "5", "git", "push"]) == [
        "git",
        "push",
    ]


def test_a_call_the_guard_cannot_read_goes_through_with_a_note(project, path):
    done = subprocess.run(
        [sys.executable, str(GUARD)],
        input="not json",
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 1  # a non-blocking error: neither 0 nor 2
    assert "could not read this call and let it through" in done.stderr
    assert len(done.stderr.splitlines()) == 1
    for event in ({}, {"tool_name": "Edit", "tool_input": {"file_path": "/x"}}):
        done = subprocess.run(
            [sys.executable, str(GUARD)],
            input=json.dumps(event),
            capture_output=True,
            text=True,
            check=False,
        )
        assert (done.returncode, done.stdout, done.stderr) == (0, "", "")


# pre-commit before a push


@pytest.fixture
def hooked(project) -> Path:
    """The project, with a pre-commit config."""
    (project / ".pre-commit-config.yaml").write_text("repos: []\n")
    return project


def test_a_push_runs_pre_commit_on_every_file_first(hooked, path):
    log = stub_pre_commit(path, "exit 0")
    done = hook("git push -u origin feat/x", hooked, path)
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")
    assert log.read_text() == f"{hooked}: run --all-files\n"


def test_pre_commit_runs_at_the_root_of_the_repository_the_push_is_in(hooked, path):
    log = stub_pre_commit(path, "exit 0")
    for command in (
        "cd src && git push origin feat/x",
        f"git -C {hooked / 'src'} push origin feat/x",
        "cd $NOWHERE && git push origin feat/x",
    ):
        cwd = hooked.parent if " -C " in command else hooked
        assert hook(command, cwd, path).returncode == 0, command
    assert log.read_text() == f"{hooked}: run --all-files\n" * 3


def test_a_push_is_refused_with_the_tail_when_pre_commit_fails(hooked, path):
    lines = "; ".join(f"echo line {n}" for n in range(1, 101))
    stub_pre_commit(path, f"{lines}; echo 'ruff....Failed' >&2; exit 1")
    done = hook("git add -A && git push origin feat/x", hooked, path)
    assert done.returncode == 2
    assert "`pre-commit run --all-files` passes before a push" in done.stderr
    assert "Instead: fix what it reports, commit what it changed" in done.stderr
    assert "ruff....Failed" in done.stderr and "line 100" in done.stderr
    assert "line 50\n" not in done.stderr  # the tail, not the whole output
    assert len(done.stderr.splitlines()) <= guard.TAIL_LINES + 3


def test_a_push_goes_through_with_a_note_where_pre_commit_is_not_installed(
    hooked, path
):
    done = hook("git push origin feat/x", hooked, path)
    assert (done.returncode, done.stderr) == (0, "")
    output = json.loads(done.stdout)
    note = output["systemMessage"]
    assert "pre-commit is not installed" in note and "CI is the backstop" in note
    assert "\n" not in note
    assert output["hookSpecificOutput"] == {
        "hookEventName": "PreToolUse",
        "additionalContext": note,
    }


def test_pre_commit_has_a_time_limit(hooked, path):
    stub_pre_commit(path, "exec /bin/sleep 30")
    done = hook("git push origin feat/x", hooked, path, GUARD_PRE_COMMIT_TIMEOUT="1")
    assert done.returncode == 2
    assert "It did not finish in 1 seconds" in done.stderr
    assert "Run `pre-commit run --all-files` yourself" in done.stderr
    assert guard.PRE_COMMIT_TIMEOUT_S == 300
    assert "300 seconds" in guard.__doc__


def test_pre_commit_does_not_run_when_nothing_is_pushed(hooked, project, path):
    log = stub_pre_commit(path, "exit 1")
    for command in (
        "git push -n origin feat/x",
        "git push --dry-run",
        "git push origin main",
        "git push --force origin feat/x",
        "git commit -m 'git push'",
        "git status",
    ):
        hook(command, hooked, path)
    assert not log.exists()
    (hooked / ".pre-commit-config.yaml").unlink()
    assert hook("git push origin feat/x", hooked, path).returncode == 0
    assert not log.exists()  # a project without a config has nothing to run


# the settings file


def settings() -> dict:
    return json.loads(SETTINGS.read_text())


def test_the_settings_run_the_guard_before_every_shell_command():
    (group,) = settings()["hooks"]["PreToolUse"]
    assert group["matcher"] == "Bash"
    (handler,) = group["hooks"]
    assert handler["type"] == "command"
    assert handler["command"] == "python3"
    assert handler["args"] == ["${CLAUDE_PROJECT_DIR}/.claude/guard.py"]
    # The hook must outlast the guard's own limit, or Claude Code cancels the hook
    # first and the push goes through unchecked.
    assert handler["timeout"] > guard.PRE_COMMIT_TIMEOUT_S


def test_the_settings_hold_only_the_refusals():
    assert sorted(p.name for p in TEMPLATES.iterdir()) == ["guard.py", "settings.json"]
    data = settings()
    assert set(data) == {"permissions", "hooks"}
    assert set(data["permissions"]) == {"deny"}
    assert set(data["hooks"]) == {"PreToolUse"}
    assert all(
        re.fullmatch(r"Bash\(.+\)", rule) for rule in data["permissions"]["deny"]
    )


def denied(command: str) -> list[str]:
    """The deny rules a command matches, by the documented wildcard rules:
    ``*`` is any text, and a rule's only ``*``, after a space at its end, also
    matches the bare command."""
    hits = []
    for rule in settings()["permissions"]["deny"]:
        pattern = rule[len("Bash(") : -1]
        expression = re.escape(pattern).replace(r"\*", ".*")
        if pattern.endswith(" *") and pattern.count("*") == 1:
            expression = re.escape(pattern[:-2]) + "( .*)?"
        if re.fullmatch(expression, command, re.DOTALL):
            hits.append(rule)
    return hits


@pytest.mark.parametrize(
    "command",
    [
        "git stash",
        "git stash -u",
        "git stash push -m wip",
        "git stash save",
        "git push origin main",
        "git push -u origin main",
        "git push origin HEAD:main",
        "git push origin master",
        "git push --force origin feat/x",
        "git push origin feat/x --force-with-lease",
        "git push -f",
        "git push origin feat/x -f",
        "git push origin feat/x --no-verify",
        "git commit --no-verify -m x",
        "git commit -m x --no-verify",
        "git commit -n -m x",
        "git commit -m x -n",
        "git reset --hard origin/main",
        "git reset --soft origin/main",
        "gh pr merge 5 --squash --admin",
    ],
)
def test_the_deny_rules_cover_the_simple_form_of_each_refusal(project, path, command):
    assert denied(command), command
    assert hook(command, project, path).returncode == 2  # and the guard agrees


@pytest.mark.parametrize(
    "command",
    [
        "git stash list",
        "git stash show -p",
        "git stash pop",
        "git push -u origin feat/x",
        "git push origin feat/maintenance",
        "git push",
        "git commit -m 'never --no-verify, never -n, never --force'",
        "git commit -m x",
        "git reset --hard",
        "git reset --hard HEAD",
        "gh pr merge 5 --squash",
        "git status",
    ],
)
def test_the_deny_rules_leave_the_harmless_forms_alone(command):
    assert denied(command) == []


# check_standards.py --require-agent-settings


BASE = {
    ".github/dependabot.yml": "version: 2\nupdates:\n",
    "CLAUDE.md": "x",
}
COPIED = {
    ".claude/settings.json": SETTINGS.read_text(),
    ".claude/guard.py": GUARD.read_text(),
}


def repo(root: Path, files: dict) -> Path:
    """A git repository holding ``files``, all added."""
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    return root


def gaps(root: Path) -> list[str]:
    return standards.check(root, require_agent_settings=True)


def test_the_agent_settings_check_is_off_by_default(tmp_path):
    root = repo(tmp_path, BASE)
    assert standards.check(root) == []
    assert standards.main(["--root", str(root)]) == 0


def test_the_check_wants_the_settings_and_the_guard_they_name(tmp_path):
    root = repo(tmp_path / "none", BASE)
    assert gaps(root) == [
        ".claude/settings.json is missing (copy settings.json and guard.py "
        "from templates/claude in murphy360/standards)"
    ]

    root = repo(tmp_path / "both", {**BASE, **COPIED})
    assert gaps(root) == []

    only_settings = {**BASE, ".claude/settings.json": COPIED[".claude/settings.json"]}
    root = repo(tmp_path / "no-guard", only_settings)
    assert gaps(root) == [
        ".claude/settings.json runs .claude/guard.py, which is missing "
        "(copy guard.py from templates/claude in murphy360/standards)"
    ]


def test_the_check_wants_a_guard_hook_on_the_shell_tool(tmp_path):
    wanted = "has no PreToolUse hook on Bash that runs the guard"
    hooks = settings()["hooks"]
    handler = hooks["PreToolUse"][0]["hooks"][0]
    shell_form = {
        "type": "command",
        "command": 'python3 "$CLAUDE_PROJECT_DIR"/.claude/hooks/guard.sh',
    }
    cases = {
        "no-hooks": ({"permissions": {"deny": ["Bash(git stash)"]}}, wanted),
        "other-tool": (
            {"hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": [handler]}]}},
            wanted,
        ),
        "after": ({"hooks": {"PostToolUse": hooks["PreToolUse"]}}, wanted),
        "other-hook": (
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "./lint.sh"}],
                        }
                    ]
                }
            },
            wanted,
        ),
        "a-list": ([], wanted),
        "every-tool": ({"hooks": {"PreToolUse": [{"hooks": [handler]}]}}, None),
        "two-tools": (
            {"hooks": {"PreToolUse": [{"matcher": "Bash|Edit", "hooks": [handler]}]}},
            None,
        ),
        "shell-form": (
            {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [shell_form]}]}},
            ".claude/hooks/guard.sh, which is missing",
        ),
    }
    for name, (data, gap) in cases.items():
        files = {**BASE, **COPIED, ".claude/settings.json": json.dumps(data)}
        found = gaps(repo(tmp_path / name, files))
        assert (len(found) == 1 and gap in found[0]) if gap else found == [], name

    broken = {**BASE, **COPIED, ".claude/settings.json": "{ // a comment\n}"}
    found = gaps(repo(tmp_path / "broken", broken))
    assert len(found) == 1
    assert found[0].startswith(".claude/settings.json is not valid JSON")


def test_main_wires_the_agent_settings_flag(tmp_path, capsys):
    root = repo(tmp_path, BASE)
    assert standards.main(["--root", str(root), "--require-agent-settings"]) == 1
    assert ".claude/settings.json is missing" in capsys.readouterr().out


def test_the_workflow_passes_its_input_to_the_tool():
    text = WORKFLOW.read_text()
    declared = text.split("      check-agent-settings:\n", 1)[1].splitlines()[:2]
    assert [line.strip() for line in declared] == ["type: boolean", "default: false"]
    assert "CHECK_AGENT_SETTINGS: ${{ inputs.check-agent-settings }}" in text
    assert (
        'if [ "$CHECK_AGENT_SETTINGS" = "true" ]; then\n'
        "            args+=(--require-agent-settings)\n"
    ) in text
    assert ("check-agent-settings", "--require-agent-settings") in rehearsal.CHECK_FLAGS
