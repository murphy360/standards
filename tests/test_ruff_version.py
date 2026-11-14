"""The linter versions: where each is written, and which one a run takes.

ruff: the standard's default is written once, in ``pyproject.toml``; a project may pin
its own (``project-ruff``); the ``ruff-version`` input wins. ``tools/ruff_version.py``
chooses, for the workflows and for the ``code-rules`` hook. The images the workflows
run are each pinned in a Dockerfile under ``tools/images``, which Dependabot reads.

Nothing here uses the network. A test that fails after a pull request from Dependabot
says what else that pull request must change.
"""

import importlib
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))  # the modules as a workflow runs them: side by side
ruff_version = importlib.import_module("ruff_version")
check_standards = importlib.import_module("check_standards")
fleet = importlib.import_module("fleet")
fleet_rehearsal = importlib.import_module("fleet_rehearsal")

WORKFLOWS = ROOT / ".github" / "workflows"
IMAGES = TOOLS / "images"
FIXTURE = ROOT / "tests" / "fixtures" / "python-project"
CONFIGS = (
    ROOT / ".pre-commit-config.yaml",
    ROOT / "templates" / "pre-commit-config.yaml",
)
DEFAULT = ruff_version.default()
STEP = "- name: The standards' tools, from the commit this workflow was called at\n"
OWNERSHIP = "- name: Take ownership of the previous job's files"
FROM_RE = re.compile(r"^FROM ((\S+?):([\w.-]+)@sha256:[0-9a-f]{64})$", re.MULTILINE)
RUFF_REV_RE = re.compile(
    r"repo: https://github\.com/astral-sh/ruff-pre-commit\n"
    r"    rev: ([0-9a-f]{40})  # v([\d.]+)\n"
)
SHA = "0123456789abcdef0123456789abcdef01234567"
needs_ruff = pytest.mark.skipif(shutil.which("ruff") is None, reason="no ruff")


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def release(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def tracked() -> list[str]:
    res = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if res.returncode != 0:
        pytest.skip("not a git checkout")
    return res.stdout.split()


@pytest.fixture(autouse=True)
def no_flags_from_the_environment(monkeypatch):
    """A workflow sets the two values; a test says itself what it passes."""
    for name in (ruff_version.VERSION_ENV, ruff_version.PROJECT_ENV):
        monkeypatch.delenv(name, raising=False)
    ruff_version.at_version.cache_clear()


# the lookup: a project's pin, in each file form


@pytest.mark.parametrize(
    "line",
    [
        "ruff==0.8.4",
        "ruff == 0.8.4",
        "Ruff==0.8.4  # the linter",
        'ruff==0.8.4 ; python_version >= "3.9"',
        "ruff==0.8.4 \\\n    --hash=sha256:" + "a" * 64,
        "  ruff==0.8.4",
    ],
)
def test_a_requirements_line_that_pins_ruff_is_read(tmp_path, line):
    write(tmp_path, {"requirements-dev.txt": f"pytest==8.0.0\n{line}\n"})
    assert ruff_version.project_pins(tmp_path) == [("0.8.4", "requirements-dev.txt")]


@pytest.mark.parametrize(
    "line",
    [
        "ruff",
        "ruff>=0.8.4",
        "ruff~=0.8.4",
        "ruff==0.8.*",
        "# ruff==0.8.4",
        "ruff-lsp==0.8.4",
        "ruff_api==0.8.4",
        "-r requirements.txt",
    ],
)
def test_only_an_exact_pin_of_ruff_itself_counts(tmp_path, line):
    write(tmp_path, {"requirements-dev.txt": f"{line}\n"})
    assert ruff_version.project_pins(tmp_path) == []


def test_requirements_dev_is_read_first_then_the_others_by_name(tmp_path):
    write(
        tmp_path,
        {
            "requirements.txt": "ruff==0.7.1\n",
            "requirements-dev.txt": "ruff==0.8.4\n",
            "requirements-test.txt": "ruff==0.9.2\n",
            "other.txt": "ruff==0.1.0\n",
            "sub/requirements-dev.txt": "ruff==0.2.0\n",  # not the lint's directory
        },
    )
    assert ruff_version.project_pins(tmp_path) == [
        ("0.8.4", "requirements-dev.txt"),
        ("0.9.2", "requirements-test.txt"),
        ("0.7.1", "requirements.txt"),
    ]


def test_a_dependency_group_and_an_extra_of_pyproject_are_read(tmp_path):
    write(
        tmp_path,
        {
            "pyproject.toml": """\
[project]
name = "made-up"
dependencies = ["ruff==0.1.0"]

[project.optional-dependencies]
dev = ["pytest", "ruff==0.9.2"]

[dependency-groups]
lint = ["ruff == 0.8.4", {include-group = "test"}]
test = ["pytest"]
"""
        },
    )
    assert ruff_version.project_pins(tmp_path) == [
        ("0.8.4", "pyproject.toml [dependency-groups] lint"),
        ("0.9.2", "pyproject.toml [project.optional-dependencies] dev"),
    ]  # the runtime dependencies are not development requirements


def test_the_requirements_come_before_pyproject(tmp_path):
    write(
        tmp_path,
        {
            "requirements-dev.txt": "ruff==0.8.4\n",
            "pyproject.toml": '[dependency-groups]\ndev = ["ruff==0.9.2"]\n',
        },
    )
    assert [v for v, _ in ruff_version.project_pins(tmp_path)] == ["0.8.4", "0.9.2"]


@pytest.mark.parametrize(
    "text",
    [
        "not toml [",
        '[project]\noptional-dependencies = "none"\n',
        "[dependency-groups]\ndev = 3\n",
        '[tool.ruff]\nline-length = 100\n[project]\nname = "x"\n',
    ],
)
def test_a_pyproject_that_names_no_pin_or_cannot_be_read_gives_none(tmp_path, text):
    write(tmp_path, {"pyproject.toml": text})
    assert ruff_version.project_pins(tmp_path) == []


def test_without_tomllib_pyproject_is_left_unread(tmp_path, monkeypatch):
    """Python before 3.11: the requirements files still count."""
    write(
        tmp_path,
        {
            "requirements.txt": "ruff==0.7.1\n",
            "pyproject.toml": '[dependency-groups]\ndev = ["ruff==0.9.2"]\n',
        },
    )
    monkeypatch.setattr(ruff_version, "tomllib", None)
    assert ruff_version.project_pins(tmp_path) == [("0.7.1", "requirements.txt")]


# precedence: the input, the project's pin, the standard's default


def test_the_input_wins_then_the_projects_pin_then_the_default(tmp_path):
    write(tmp_path, {"requirements-dev.txt": "ruff==0.8.4\n"})
    choose = ruff_version.choose
    assert choose(tmp_path, "0.9.2", True) == ("0.9.2", "given: ruff-version")
    assert choose(tmp_path, " 0.9.2 ", False) == ("0.9.2", "given: ruff-version")
    assert choose(tmp_path, "", True) == (
        "0.8.4",
        "the project's pin, requirements-dev.txt",
    )
    assert choose(tmp_path / "missing", "", True) == (
        DEFAULT,
        "the standard's default: the project pins no ruff",
    )


def test_without_project_ruff_a_projects_pin_changes_nothing(tmp_path):
    """Off by default in v2: a project that names a ruff keeps the default."""
    write(tmp_path, {"requirements-dev.txt": "ruff==0.8.4\n"})
    assert ruff_version.choose(tmp_path) == (DEFAULT, "the standard's default")


def run_cli(cwd: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("CODE_RULES_")}
    return subprocess.run(
        [sys.executable, str(TOOLS / "ruff_version.py"), *args],
        cwd=cwd,
        env={**clean, **env},
        capture_output=True,
        text=True,
    )


def test_the_command_prints_the_version_and_where_it_comes_from(tmp_path):
    write(tmp_path, {"requirements-dev.txt": "ruff==0.8.4\n"})
    assert run_cli(tmp_path).stdout == f"ruff {DEFAULT} (the standard's default)\n"
    pinned = "ruff 0.8.4 (the project's pin, requirements-dev.txt)\n"
    assert run_cli(tmp_path, "--project-ruff").stdout == pinned
    given = run_cli(tmp_path, "--project-ruff", "--ruff-version", "0.9.2")
    assert given.stdout == "ruff 0.9.2 (given: ruff-version)\n"
    elsewhere = run_cli(ROOT, "--project-ruff", "--root", str(tmp_path))
    assert elsewhere.stdout == pinned


def test_the_command_reads_what_a_workflow_sets_in_the_environment(tmp_path):
    """The job's env carries the two inputs to both steps; an empty input is none."""
    write(tmp_path, {"requirements-dev.txt": "ruff==0.8.4\n"})
    version, project = ruff_version.VERSION_ENV, ruff_version.PROJECT_ENV
    off = run_cli(tmp_path, **{version: "", project: "false"})
    assert off.stdout == f"ruff {DEFAULT} (the standard's default)\n"
    on = run_cli(tmp_path, **{version: "", project: "true"})
    assert on.stdout == "ruff 0.8.4 (the project's pin, requirements-dev.txt)\n"
    given = run_cli(tmp_path, **{version: "0.9.2", project: "true"})
    assert given.stdout == "ruff 0.9.2 (given: ruff-version)\n"


def test_install_asks_pip_for_that_release(tmp_path, monkeypatch, capsys):
    asked = []

    def pip(cmd, **_):
        asked.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(ruff_version.subprocess, "run", pip)
    write(tmp_path, {"requirements-dev.txt": "ruff==0.8.4\n"})
    args = ["--root", str(tmp_path), "--project-ruff", "--install"]
    assert ruff_version.main(args) == 0
    assert asked == [
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "ruff==0.8.4",
        ]
    ]
    assert "ruff 0.8.4 (the project's pin" in capsys.readouterr().out
    assert ruff_version.main(["--root", str(tmp_path)]) == 0
    assert len(asked) == 1  # without --install nothing is installed


# the one place: the default is written in pyproject.toml and nowhere else


def test_the_default_is_the_one_dependency_of_pyproject():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["dependencies"] == [f"ruff=={DEFAULT}"], (
        "pyproject.toml holds one dependency, `ruff==<version>`, on one line: "
        "tools/ruff_version.py reads the default there"
    )
    assert project["name"] == ruff_version.PACKAGE


def test_the_installed_package_names_the_default_where_no_checkout_is(
    tmp_path, monkeypatch
):
    """The hook pre-commit built: the module sits in site-packages, alone."""
    monkeypatch.setattr(ruff_version, "__file__", str(tmp_path / "lib" / "module.py"))
    monkeypatch.setattr(ruff_version.metadata, "requires", lambda name: ["ruff==0.8.4"])
    assert ruff_version.default() == "0.8.4"

    def missing(name):
        raise ruff_version.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(ruff_version.metadata, "requires", missing)
    with pytest.raises(SystemExit, match="Pass --ruff-version"):
        ruff_version.default()


def test_no_ruff_version_is_written_in_a_second_place():
    """The default sits in pyproject.toml, and the fixture's pin in its own file.

    The ruff-pre-commit rev is a commit, with its release in a comment: the next
    test holds it equal. Tests name made-up releases of their own.
    """
    pin = ruff_version.project_pins(FIXTURE)[0][0]
    fixture = "tests/fixtures/python-project/requirements-dev.txt"
    configs = {str(config.relative_to(ROOT)) for config in CONFIGS}
    allowed = {DEFAULT: ("pyproject.toml", configs), pin: (fixture, set())}
    for rel in tracked():
        path = ROOT / rel
        if path.suffix in (".svg", ".json") or not path.is_file():
            continue
        text = path.read_text(errors="replace")
        for version, (home, held_equal) in allowed.items():
            written = re.search(rf"(?<![\d.]){re.escape(version)}(?![\d.])", text)
            assert not written or rel == home or rel in held_equal, (
                f"{rel} names ruff {version}: it is written once, in {home}; read "
                "it from there with tools/ruff_version.py"
            )
        if not rel.startswith("tests/") and rel != "pyproject.toml":
            assert not re.search(r"ruff==\d", text), f"{rel} pins a ruff of its own"


@pytest.mark.parametrize("config", CONFIGS, ids=lambda p: p.parent.name or "root")
def test_both_pre_commit_configs_hold_ruff_at_the_default(config):
    """What a pull request that moves the default must change with it."""
    pin = RUFF_REV_RE.search(config.read_text())
    assert pin, f"{config.name}: ruff-pre-commit at a commit SHA, `# v<version>`"
    assert pin.group(2) == DEFAULT, (
        f"pyproject.toml says ruff {DEFAULT}, and the ruff-pre-commit rev in "
        f"{config.relative_to(ROOT)} is v{pin.group(2)}. Set the rev to the commit "
        f"of v{DEFAULT} (`gh api repos/astral-sh/ruff-pre-commit/git/ref/tags/"
        f"v{DEFAULT}`) and its comment to `# v{DEFAULT}`, in both pre-commit configs"
    )
    other = RUFF_REV_RE.search(CONFIGS[0].read_text())
    assert pin.group(1) == other.group(1), "the same commit in both configs"


@pytest.mark.parametrize("name", ["python-lint", "node-lint"])
def test_the_lint_workflows_name_no_version_and_ask_the_tool(name):
    text = (WORKFLOWS / f"{name}.yml").read_text()
    assert '      ruff-version: {type: string, default: "", description:' in text
    assert "      project-ruff: {type: boolean, default: false, description:" in text
    env = text.split("    timeout-minutes: 15\n    env:\n", 1)[1].split("    defaults:")
    assert [line.split("  #")[0].strip() for line in env[0].splitlines()] == [
        "CODE_RULES_RUFF_VERSION: ${{ inputs.ruff-version }}",
        "CODE_RULES_PROJECT_RUFF: ${{ inputs.project-ruff }}",
    ]
    install = 'run: python3 "$RUNNER_TEMP/standards/tools/ruff_version.py" --install\n'
    assert text.count(install) == 1 and "pip install" not in text
    lint = 'run: bash "$RUNNER_TEMP/standards/tools/steps/code-rules.sh" '
    assert text.index(STEP) < text.index(install) < text.index(lint)


def test_this_repositorys_ci_installs_the_default_through_the_tool():
    text = (WORKFLOWS / "ci.yml").read_text()
    assert text.count("python3 tools/ruff_version.py --install") == 2  # tests, hook
    assert " ruff-version:" not in text and "ruff==" not in text


# the fixture: a project that pins a newer ruff, linted with it


def test_the_fixture_pins_a_newer_ruff_than_the_default():
    version, source = ruff_version.choose(FIXTURE, "", True)
    assert source == "the project's pin, requirements-dev.txt"
    assert release(version) > release(DEFAULT), (
        f"the python-lint fixture pins ruff {version} and the default is {DEFAULT}: "
        "the fixture is there to be linted with a newer release than the default, "
        "so move its pin in tests/fixtures/python-project/requirements-dev.txt"
    )
    assert ruff_version.choose(FIXTURE) == (DEFAULT, "the standard's default")


def test_ci_lints_the_fixture_with_the_ruff_it_pins():
    text = (WORKFLOWS / "ci.yml").read_text()
    job = text.split("\n  lint-pinned:\n", 1)[1].split("\n  shell:\n", 1)[0]
    assert job.splitlines() == [
        "    uses: ./.github/workflows/python-lint.yml",
        "    with:",
        "      format-check: true",
        "      project-ruff: true",
        "      working-directory: tests/fixtures/python-project",
    ]
    needs = text.split("\n  result:\n", 1)[1].split("steps:", 1)[0]
    assert "lint-pinned" in needs


def test_dependabot_watches_the_default_and_the_fixtures_pin():
    entries = check_standards.dependabot_entries(
        (ROOT / ".github" / "dependabot.yml").read_text()
    )
    assert ("pip", "/") in entries  # the default, in pyproject.toml
    assert ("pip", "/tests/fixtures/python-project") in entries


def test_the_workflows_step_installs_the_fixtures_pin(tmp_path):
    """The step as python-lint.yml holds it, with the job's env as lint-pinned
    sets it, in the fixture. pip is a stand-in that records what it is asked."""
    text = (WORKFLOWS / "python-lint.yml").read_text()
    step = text.split("- name: ruff (the caller's version, the project's pin", 1)[1]
    shell = step.split("        run: ", 1)[1].splitlines()[0]
    asked = tmp_path / "asked"
    stub = f"import sys\nopen({str(asked)!r}, 'w').write(' '.join(sys.argv[1:]))\n"
    write(tmp_path, {"stub/pip/__init__.py": "", "stub/pip/__main__.py": stub})
    (tmp_path / "standards").symlink_to(ROOT, target_is_directory=True)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "RUNNER_TEMP": str(tmp_path),
        "PYTHONPATH": str(tmp_path / "stub"),
        "CODE_RULES_RUFF_VERSION": "",
        "CODE_RULES_PROJECT_RUFF": "true",
    }
    run = {"cwd": FIXTURE, "capture_output": True, "text": True}
    res = subprocess.run(["bash", "-e", "-c", shell], env=env, **run)
    pin = ruff_version.project_pins(FIXTURE)[0][0]
    assert res.returncode == 0, res.stderr
    assert res.stdout == f"ruff {pin} (the project's pin, requirements-dev.txt)\n"
    assert asked.read_text() == f"install --disable-pip-version-check ruff=={pin}"
    env["CODE_RULES_PROJECT_RUFF"] = "false"  # the input's default
    res = subprocess.run(["bash", "-e", "-c", shell], env=env, **run)
    assert res.stdout == f"ruff {DEFAULT} (the standard's default)\n"
    assert asked.read_text().endswith(f"ruff=={DEFAULT}")


@needs_ruff
def test_the_default_ruff_would_re_format_the_hook_jobs_new_style_file(tmp_path):
    """What makes the `hook` job a proof: its file passes only the pinned release."""
    hook_project = importlib.import_module("hook_project")
    assert hook_project.fixture_pin() == ruff_version.project_pins(FIXTURE)[0][0]
    here = ruff_version.installed(ruff_version.path_command())
    if not ruff_version.same(here, DEFAULT):
        pytest.skip(f"ruff {here} is here, not the default {DEFAULT}")
    write(tmp_path, {"new_style.py": hook_project.NEW_STYLE})
    res = subprocess.run(
        ["ruff", "format", "--check", "--isolated", "--no-cache", "new_style.py"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1 and "Would reformat: new_style.py" in res.stdout, (
        f"ruff {DEFAULT} leaves NEW_STYLE of tests/hook_project.py as it is, so the "
        "hook job no longer tells the default from the release the fixture pins: "
        "give it a file the two format differently"
    )


# the ruff a run takes: the one on PATH, or the chosen release


def fake_ruff(directory: Path, version: str) -> Path:
    """A stand-in for a ruff binary: it only says its version."""
    directory.mkdir(parents=True, exist_ok=True)
    exe = directory / "ruff"
    exe.write_text(f"#!/bin/sh\necho 'ruff {version}'\n")
    exe.chmod(0o755)
    return exe


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A machine with ruff 0.8.4 on PATH, and a cache directory of its own."""
    fake_ruff(tmp_path / "bin", "0.8.4")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(ruff_version, "cache_dir", lambda: tmp_path / "cache")
    return tmp_path


def test_without_a_flag_the_ruff_on_path_runs_whatever_its_version(machine, capsys):
    assert ruff_version.chosen(machine) == {}
    assert capsys.readouterr().out == ""  # and the log says nothing new
    assert ruff_version.command(None) == [str(machine / "bin" / "ruff")]


def test_a_chosen_release_that_is_on_path_is_taken_from_there(machine, monkeypatch):
    monkeypatch.setattr(
        ruff_version, "provision", lambda *a: pytest.fail("installed a second ruff")
    )
    assert ruff_version.command("0.8.4") == [str(machine / "bin" / "ruff")]
    assert ruff_version.same("0.8", "0.8.0") and ruff_version.same("v0.8.4", "0.8.4")
    assert not ruff_version.same("0.8.4", "0.8.40")
    assert not ruff_version.same(None, "0.8.4")


def test_another_release_is_installed_once_into_a_directory_of_its_own(
    machine, monkeypatch
):
    asked = []
    real = subprocess.run

    def run(cmd, **kwargs):
        if cmd[:3] != [sys.executable, "-m", "pip"]:
            return real(cmd, **kwargs)
        asked.append(cmd)
        fake_ruff(Path(cmd[cmd.index("--target") + 1]) / "bin", "0.9.2")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ruff_version.subprocess, "run", run)
    exe = machine / "cache" / "ruff-0.9.2" / "bin" / "ruff"
    assert ruff_version.command("0.9.2") == [str(exe)]
    assert asked[0][-1] == "ruff==0.9.2" and "--no-deps" in asked[0]
    ruff_version.at_version.cache_clear()  # a second run finds it there
    assert ruff_version.command("0.9.2") == [str(exe)]
    assert len(asked) == 1
    assert sorted(p.name for p in (machine / "cache").iterdir()) == ["ruff-0.9.2"]


def test_a_release_pip_cannot_install_stops_the_run_and_names_it(machine, monkeypatch):
    def run(cmd, **kwargs):
        if cmd[:3] != [sys.executable, "-m", "pip"]:
            return subprocess.CompletedProcess(cmd, 1, "", "")
        return subprocess.CompletedProcess(cmd, 1, "", "no network")

    monkeypatch.setattr(ruff_version.subprocess, "run", run)
    with pytest.raises(SystemExit) as stopped:
        ruff_version.command("0.9.2")
    assert "ruff 0.9.2 is not installed here" in str(stopped.value)
    assert "no network" in str(stopped.value)
    assert "pip install ruff==0.9.2" in str(stopped.value)
    assert not (machine / "cache" / "ruff-0.9.2").exists()


def test_the_installed_ruff_goes_inside_the_hooks_environment(tmp_path, monkeypatch):
    """pre-commit's environment is a virtual environment; `pre-commit clean` then
    removes the ruff with it. Outside one it is the user's cache directory."""
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "py_env"))
    assert ruff_version.cache_dir() == tmp_path / "py_env" / "ruff-versions"
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert ruff_version.cache_dir() == tmp_path / "xdg" / ruff_version.PACKAGE


def test_a_flag_prints_the_release_and_its_source(machine, capsys):
    write(machine, {"requirements-dev.txt": "ruff==0.8.4\n"})
    assert ruff_version.chosen(machine, "", True) == {"version": "0.8.4"}
    assert capsys.readouterr().out == (
        "code_rules: ruff 0.8.4 (the project's pin, requirements-dev.txt)\n"
    )
    assert ruff_version.chosen(machine, "0.9.2", True) == {"version": "0.9.2"}
    assert "ruff 0.9.2 (given: ruff-version)" in capsys.readouterr().out


def code_rules(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOLS / "code_rules.py"), "--all", *args],
        cwd=root,
        capture_output=True,
        text=True,
    )


@needs_ruff
def test_the_code_rules_run_the_projects_pin_and_say_so(tmp_path):
    """With the flag, and with the release that is here: nothing to install."""
    here = ruff_version.installed(ruff_version.path_command())
    write(tmp_path, {"requirements-dev.txt": f"ruff=={here}\n", "a.py": "A = 1\n"})
    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", *args], cwd=tmp_path, check=True)
    plain = code_rules(tmp_path)
    assert plain.returncode == 0, plain.stdout + plain.stderr
    assert "code_rules: ruff " not in plain.stdout  # as before: no new line
    pinned = code_rules(tmp_path, "--project-ruff")
    assert pinned.returncode == 0, pinned.stdout + pinned.stderr
    said = f"code_rules: ruff {here} (the project's pin, requirements-dev.txt)"
    assert pinned.stdout.splitlines()[0] == said
    assert pinned.stdout.splitlines()[1:] == plain.stdout.splitlines()
    given = code_rules(tmp_path, "--ruff-version", here)
    assert given.stdout.splitlines()[0] == (
        f"code_rules: ruff {here} (given: ruff-version)"
    )
    from_env = subprocess.run(  # as the workflow's lint step gets it: the job's env
        [sys.executable, str(TOOLS / "code_rules.py"), "--all"],
        cwd=tmp_path,
        env={**os.environ, ruff_version.PROJECT_ENV: "true"},
        capture_output=True,
        text=True,
    )
    assert from_env.stdout == pinned.stdout


def test_the_hooks_take_the_flags_and_the_rehearsal_passes_them():
    manifest = (ROOT / ".pre-commit-hooks.yaml").read_text()
    assert "`--project-ruff`, `--ruff-version <version>`" in manifest
    template = CONFIGS[1].read_text()
    assert "        # args: [--all, --project-ruff]  # with `project-ruff: true`" in (
        template
    )
    assert "          # project-ruff: true  # lint with the ruff the project pins" in (
        (ROOT / "templates" / "ci.yml").read_text()
    )
    given = {"project-ruff": "true", "ruff-version": "0.9.2"}
    (step,) = fleet_rehearsal.plan(
        {"calls": [{"workflow": "python-lint", "with": given}]}
    )
    assert step["args"] == [
        "--all",
        "--no-format",
        "--project-ruff",
        "--ruff-version",
        "0.9.2",
    ]


# the images: each pinned by tag and digest in a file Dependabot reads


def pinned_images() -> dict[str, tuple[str, str, str]]:
    """name: (the whole reference, the image, its tag), one per Dockerfile."""
    out = {}
    for path in sorted(IMAGES.iterdir()):
        found = FROM_RE.findall(path.read_text())
        assert len(found) == 1, f"{path.name}: one FROM line, image:tag@sha256:digest"
        out[path.name.removeprefix("Dockerfile.")] = found[0]
    return out


def image_ref(*args: str, script: Path = TOOLS / "image-ref.sh"):
    return subprocess.run(["sh", str(script), *args], capture_output=True, text=True)


def test_each_image_a_workflow_runs_has_a_dockerfile_dependabot_reads():
    images = pinned_images()
    assert {name: image for name, (_, image, _) in images.items()} == {
        "actionlint": "rhysd/actionlint",
        "alpine": "alpine",
        "gitleaks": "ghcr.io/gitleaks/gitleaks",
        "shellcheck": "koalaman/shellcheck",
    }
    for path in IMAGES.iterdir():  # Dependabot reads a file named *dockerfile*
        assert path.name.startswith("Dockerfile."), path.name
    entries = check_standards.dependabot_entries(
        (ROOT / ".github" / "dependabot.yml").read_text()
    )
    assert ("docker", "/tools/images") in entries


@pytest.mark.parametrize("name", ["actionlint", "alpine", "gitleaks", "shellcheck"])
def test_the_script_reads_the_reference_from_its_file(name):
    ref, image, tag = pinned_images()[name]
    res = image_ref(name)
    assert (res.returncode, res.stdout) == (0, ref + "\n")
    assert (
        res.stderr
        == f"{name} image: {ref} (pinned in tools/images/Dockerfile.{name})\n"
    )
    same = image_ref(name, tag)  # a caller that passes the pinned release
    assert same.stdout == ref + "\n"
    other = image_ref(name, "v9.9.9")  # another release: by its tag, as before
    assert (other.returncode, other.stdout) == (0, f"{image}:v9.9.9\n")
    assert "by its tag" in other.stderr


def test_the_script_refuses_an_unknown_image_and_one_without_a_digest(tmp_path):
    missing = image_ref("nothing")
    assert missing.returncode == 1 and missing.stdout == ""
    assert "no image is pinned for 'nothing'" in missing.stderr
    script = tmp_path / "image-ref.sh"
    shutil.copy(TOOLS / "image-ref.sh", script)
    write(tmp_path, {"images/Dockerfile.loose": "FROM alpine:3\n"})
    loose = image_ref("loose", script=script)
    assert loose.returncode == 1 and loose.stdout == ""
    assert "names no image by tag and digest" in loose.stderr
    assert image_ref(script=script).returncode != 0  # no name at all


def test_no_workflow_names_an_image_itself():
    """A `run:` step is not a place Dependabot reads: the image comes from the
    script, and `test-image` is the image the project's own job just built."""
    steps = sorted((TOOLS / "steps").glob("*.sh"))  # the shell of the check steps
    for path in [*sorted(WORKFLOWS.glob("*.yml")), *steps]:
        text = path.read_text()
        for line in text.splitlines():
            if "docker run" not in line or line.lstrip().startswith("#"):
                continue
            named = re.search(r'"(\$image|\$GITLEAKS_IMAGE|test-image:[^"]*)"', line)
            assert named, f"{path.name}: {line.strip()}"
        for _, image, _ in pinned_images().values():
            assert f"{image}:" not in text, f"{path.name} names {image} itself"


def test_the_ownership_step_reads_alpine_after_the_tools_are_fetched():
    script = (TOOLS / "steps" / "take-ownership.sh").read_text()
    assert 'image="$(sh "$here/../image-ref.sh" alpine)"\n' in script, (
        "the script that takes ownership reads alpine from "
        "tools/images/Dockerfile.alpine, through tools/image-ref.sh"
    )
    with_step = [
        p for p in sorted(WORKFLOWS.glob("*.yml")) if OWNERSHIP in p.read_text()
    ]
    assert len(with_step) >= 6
    for path in with_step:
        text = path.read_text()
        step = text.split(OWNERSHIP, 1)[1].split("\n      - ", 1)[0]
        assert 'run: sh "$RUNNER_TEMP/standards/tools/steps/take-ownership.sh"' in (
            step
        ), f"{path.name}: the step that takes ownership is the one script"
        assert (
            text.index(STEP) < text.index(OWNERSHIP) < text.index("actions/checkout@")
        ), f"{path.name}: the tools are fetched first, then ownership, then checkout"


@pytest.mark.parametrize(
    ("workflow", "name", "given"),
    [
        ("shell-lint", "shellcheck", "shellcheck-version"),
        ("actionlint", "actionlint", "actionlint-version"),
    ],
)
def test_a_version_input_still_reaches_the_image(workflow, name, given):
    text = (WORKFLOWS / f"{workflow}.yml").read_text()
    assert f'      {given}: {{type: string, default: "", description:' in text
    assert f"          VERSION: ${{{{ inputs.{given} }}}}\n" in text
    run = f'run: bash "$RUNNER_TEMP/standards/tools/steps/{workflow}.sh" "$VERSION"\n'
    assert text.endswith(run)
    script = (TOOLS / "steps" / f"{workflow}.sh").read_text()
    assert f'image="$(sh "$here/../image-ref.sh" {name} "${{1:-}}")"\n' in script


def hook_release(config: Path, repo: str, pattern: str) -> str:
    found = re.search(
        rf"repo: https://github\.com/{repo}\n    rev: [0-9a-f]{{40}}  # {pattern}\n",
        config.read_text(),
    )
    assert found, f"{config.name}: {repo} at a commit SHA, its version in a comment"
    return found.group(1)


@pytest.mark.parametrize("config", CONFIGS, ids=lambda p: p.parent.name or "root")
def test_the_shellcheck_and_actionlint_hooks_are_the_images_releases(config):
    """What a pull request that moves an image must change with it."""
    images = pinned_images()
    where = config.relative_to(ROOT)
    shellcheck = images["shellcheck"][2].removeprefix("v")
    hook = hook_release(
        config, "shellcheck-py/shellcheck-py", r"v[\d.]+ \(shellcheck ([\d.]+)\)"
    )
    assert hook == shellcheck, (
        f"tools/images/Dockerfile.shellcheck is at {shellcheck} and the shellcheck-py "
        f"rev in {where} is at {hook}: set the rev to the commit of the "
        f"shellcheck-py release for {shellcheck}, in both pre-commit configs"
    )
    assert f"shell-lint.yml's ({shellcheck})" in config.read_text(), where
    actionlint = images["actionlint"][2]
    hook = hook_release(config, "rhysd/actionlint", r"v([\d.]+)")
    assert hook == actionlint, (
        f"tools/images/Dockerfile.actionlint is at {actionlint} and the actionlint "
        f"rev in {where} is at {hook}: set the rev to the commit of v{actionlint} "
        "(`gh api repos/rhysd/actionlint/git/ref/tags/v<version>`), in both configs"
    )
    assert f"actionlint.yml's ({actionlint})" in config.read_text(), where


def test_the_readme_names_the_shellcheck_release():
    readme = (ROOT / "README.md").read_text()
    row = next(line for line in readme.splitlines() if "/shell-lint.yml` |" in line)
    tag = pinned_images()["shellcheck"][2]
    assert tag in row, f"the README row of shell-lint.yml names shellcheck {tag}"


# the opt-in check: every ruff version a project names is the same one


def lint_job(given: str = "") -> str:
    uses = "murphy360/standards/.github/workflows/python-lint.yml@v2"
    return f"name: ci\njobs:\n  lint:\n    uses: {uses}\n{given}"


def pre_commit(rev: str = f"{SHA}  # v0.8.4", hook: str = "") -> str:
    return f"""\
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: {rev}
    hooks:
      - id: ruff
  - repo: https://github.com/murphy360/standards
    rev: {SHA}  # v2.3.0
    hooks:
      - id: code-rules
{hook}      - id: code-rules-node
"""


def problems(root: Path, files: dict[str, str]) -> list[str]:
    write(root, files)
    return ruff_version.pin_problems(root, sorted(files), fleet.workflow_calls)


AGREEING = {
    "requirements-dev.txt": "ruff==0.8.4\n",
    ".pre-commit-config.yaml": pre_commit(
        hook="        args: [--all, --project-ruff]\n"
    ).replace("      - id: code-rules-node\n", ""),
    ".github/workflows/ci.yml": lint_job("    with:\n      project-ruff: true\n"),
}


def test_a_project_whose_pins_agree_passes(tmp_path):
    assert problems(tmp_path, AGREEING) == []


def test_a_project_that_names_no_ruff_at_all_passes(tmp_path):
    files = {"README.md": "a project\n", ".github/workflows/ci.yml": lint_job()}
    assert problems(tmp_path, files) == []


def test_a_pin_the_lint_job_and_the_hook_do_not_follow_is_a_problem(tmp_path):
    files = {
        **AGREEING,
        ".pre-commit-config.yaml": pre_commit(),
        ".github/workflows/ci.yml": lint_job(),
    }
    (problem,) = problems(tmp_path, files)
    assert problem.startswith("ruff is not one version here")
    for place in (
        "0.8.4 in requirements-dev.txt",
        f"{DEFAULT} in .github/workflows/ci.yml, python-lint (the standard's default)",
        "0.8.4 in .pre-commit-config.yaml, the ruff-pre-commit rev",
        f"{DEFAULT} in .pre-commit-config.yaml, the code-rules hook (the standard's",
        f"{DEFAULT} in .pre-commit-config.yaml, the code-rules-node hook",
    ):
        assert place in problem, place
    assert "`project-ruff: true`" in problem and "`--project-ruff`" in problem


@pytest.mark.parametrize(
    ("rel", "text", "named"),
    [
        (
            ".pre-commit-config.yaml",
            pre_commit(f"{SHA}  # v0.9.2", "        args: [--all, --project-ruff]\n"),
            "0.9.2 in .pre-commit-config.yaml, the ruff-pre-commit rev",
        ),
        (
            ".pre-commit-config.yaml",
            pre_commit("v0.9.2", "        args: [--all, --project-ruff]\n"),
            "0.9.2 in .pre-commit-config.yaml, the ruff-pre-commit rev",
        ),
        (
            ".pre-commit-config.yaml",
            pre_commit(hook="        args:\n          - --ruff-version=0.9.2\n"),
            "0.9.2 in .pre-commit-config.yaml, the code-rules hook (given",
        ),
        (
            ".github/workflows/ci.yml",
            lint_job('    with:\n      ruff-version: "0.9.2"\n'),
            "0.9.2 in .github/workflows/ci.yml, python-lint (given: ruff-version)",
        ),
        (
            "requirements.txt",
            "ruff==0.9.2\n",
            "0.9.2 in requirements.txt",
        ),
    ],
)
def test_each_place_that_names_another_version_is_a_problem(tmp_path, rel, text, named):
    files = {**AGREEING, rel: text}
    if rel.startswith(".pre-commit"):  # drop the hook that follows nothing
        files[rel] = text.replace("      - id: code-rules-node\n", "")
    (problem,) = problems(tmp_path, files)
    assert named in problem
    assert "0.8.4 in requirements-dev.txt" in problem


def test_a_local_hook_with_a_ruff_of_its_own_is_read(tmp_path):
    local = """\
repos:
  - repo: local
    hooks:
      - id: code-rules
        entry: python3 tools/code_rules.py --all
        language: python
        additional_dependencies: ["ruff==0.9.2"]
"""
    (problem,) = problems(tmp_path, {**AGREEING, ".pre-commit-config.yaml": local})
    assert "0.9.2 in .pre-commit-config.yaml, the code-rules hook (its dep" in problem


def test_a_rev_that_names_no_release_is_a_problem(tmp_path):
    files = {**AGREEING, ".pre-commit-config.yaml": pre_commit(SHA)}
    found = problems(tmp_path, files)
    assert any("the ruff-pre-commit rev names no release" in p for p in found)


def test_a_lint_job_in_another_directory_reads_the_pin_there(tmp_path):
    job = "    with:\n      project-ruff: true\n      working-directory: backend\n"
    files = {
        "backend/requirements-dev.txt": "ruff==0.8.4\n",
        ".github/workflows/ci.yml": lint_job(job),
        ".pre-commit-config.yaml": pre_commit(
            hook="        args: [--all, --project-ruff, --root, backend]\n"
        ).replace("      - id: code-rules-node\n", ""),
    }
    assert problems(tmp_path, files) == []
    files[".pre-commit-config.yaml"] = pre_commit(
        hook="        args: [--all, --project-ruff]\n"
    ).replace("      - id: code-rules-node\n", "")
    (problem,) = problems(tmp_path, files)  # the hook reads the root: no pin there
    assert "0.8.4 in backend/requirements-dev.txt" in problem
    assert f"{DEFAULT} in .pre-commit-config.yaml, the code-rules hook" in problem


def test_a_version_only_a_run_resolves_is_left_out(tmp_path):
    job = "    with:\n      ruff-version: ${{ vars.RUFF }}\n"
    files = {".github/workflows/ci.yml": lint_job(job)}
    assert problems(tmp_path, files) == []


def project(root: Path, files: dict[str, str]) -> Path:
    """A project that passes the checks that are always on."""
    write(root, {"CLAUDE.md": "# a project\n", **files})
    updates = "  - package-ecosystem: {}\n    directory: /\n"
    dependabot = "version: 2\nupdates:\n" + "".join(
        updates.format(eco) for eco in ("github-actions", "pip")
    )
    write(root, {".github/dependabot.yml": dependabot})
    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", *args], cwd=root, check=True)
    return root


def test_the_check_is_off_until_a_project_turns_it_on(tmp_path, capsys):
    files = {**AGREEING, ".github/workflows/ci.yml": lint_job()}
    root = project(tmp_path, files)
    assert check_standards.main(["--root", str(root)]) == 0
    assert capsys.readouterr().out == "standards: OK\n"
    assert check_standards.main(["--root", str(root), "--require-one-ruff-version"])
    out = capsys.readouterr().out
    assert out.startswith("ruff is not one version here, so local and CI can differ:")
    assert out.endswith("standards: FAILED, 1 gap(s)\n")


def test_the_workflow_passes_check_ruff_version_to_the_tool(tmp_path):
    """The last step's script, as the workflow runs it, against the real tools."""
    text = (WORKFLOWS / "standards-check.yml").read_text()
    declared = text.split("      check-ruff-version:\n", 1)[1].splitlines()[:2]
    assert [line.strip() for line in declared] == ["type: boolean", "default: false"]
    assert "CHECK_RUFF_VERSION: ${{ inputs.check-ruff-version }}" in text
    assert text.endswith(
        'run: bash "$RUNNER_TEMP/standards/tools/steps/standards-check.sh"\n'
    )
    files = {**AGREEING, ".github/workflows/ci.yml": lint_job()}
    root = project(tmp_path / "project", files)
    env = {"PATH": os.environ["PATH"]}
    results = {}
    for on in ("false", "true"):
        results[on] = subprocess.run(
            ["bash", str(TOOLS / "steps" / "standards-check.sh")],
            cwd=root,
            env={**env, "CHECK_RUFF_VERSION": on},
            capture_output=True,
            text=True,
        )
    assert results["false"].returncode == 0, results["false"].stderr
    assert results["true"].returncode == 1, results["true"].stderr
    assert "ruff is not one version here" in results["true"].stdout
    assert "# check-ruff-version: true" in (ROOT / "templates" / "ci.yml").read_text()
    assert ("check-ruff-version", "--require-one-ruff-version") in (
        fleet_rehearsal.CHECK_FLAGS
    )


def test_this_repository_holds_two_versions_on_purpose_and_no_third():
    """The default everywhere, and the newer release its fixture pins: the reason
    ci.yml leaves check-ruff-version off here."""
    pin = ruff_version.project_pins(FIXTURE)[0][0]
    (problem,) = ruff_version.pin_problems(ROOT, tracked(), fleet.workflow_calls)
    places = problem.split(": ", 1)[1].split(". Pin ruff once")[0].split("; ")
    assert len(places) >= 6
    for place in places:
        version = place.split(" in ", 1)[0]
        if version == pin:
            assert "python-project" in place or "the project's pin" in place, place
        else:
            assert version == DEFAULT, place
