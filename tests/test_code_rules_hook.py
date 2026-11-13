"""The code rules as a pre-commit hook: local equals CI.

``.pre-commit-hooks.yaml`` offers the hooks, ``pyproject.toml`` makes this repository
installable so that pre-commit can build them, and the template config carries
``code-rules``. Nothing here uses the network: the hook is built for real, from the
commit under test, by the ``hook`` job of ci.yml (``tests/hook_project.py``).
"""

import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import hook_project
import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
WORKFLOWS = ROOT / ".github" / "workflows"
MANIFEST = ROOT / ".pre-commit-hooks.yaml"
TEMPLATE = ROOT / "templates" / "pre-commit-config.yaml"
OWN_CONFIG = ROOT / ".pre-commit-config.yaml"
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())
needs_ruff = pytest.mark.skipif(shutil.which("ruff") is None, reason="no ruff")
# what pre-commit accepts in a hook of a manifest, as far as this repository uses it
REQUIRED_KEYS = {"id", "name", "entry", "language"}
KNOWN_KEYS = REQUIRED_KEYS | {"description", "args", "pass_filenames"}
# the one tool a document may tell a reader to run from a checkout of this repository:
# it copies the templates out of that checkout (README, "Staying current")
NEEDS_A_CHECKOUT = {"sync_templates.py"}
STANDARDS_HOOK_RE = re.compile(
    r"^  - repo: https://github\.com/murphy360/standards\n"
    r"    rev: ([0-9a-f]{40})  # (\S.*)\n"
    r"    hooks:\n"
    r"      - id: code-rules\n",
    re.MULTILINE,
)


def manifest_hooks() -> list[dict[str, str]]:
    """The manifest's hooks: a list of flat mappings, read without a YAML library."""
    hooks: list[dict[str, str]] = []
    for line in MANIFEST.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        entry = re.fullmatch(r"(- |  )([\w-]+): (\S.*)", line)
        assert entry, f"not `key: value` of a hook: {line!r}"
        if entry.group(1) == "- ":
            hooks.append({})
        hooks[-1][entry.group(2)] = entry.group(3)
    return hooks


def hook(hook_id: str) -> dict[str, str]:
    return next(h for h in manifest_hooks() if h["id"] == hook_id)


def ruff_pin() -> str:
    """The ruff release the hook's environment gets: pyproject's one dependency."""
    (dependency,) = PYPROJECT["project"]["dependencies"]
    assert re.fullmatch(r"ruff==[\d.]+", dependency), "ruff, pinned to one release"
    return dependency.removeprefix("ruff==")


# the manifest: valid, and each entry is a command the package installs


def test_the_manifest_offers_the_two_hooks_and_each_is_valid():
    hooks = manifest_hooks()
    assert [h["id"] for h in hooks] == ["code-rules", "code-rules-node"]
    for h in hooks:
        assert REQUIRED_KEYS <= set(h) <= KNOWN_KEYS, h
        assert h["language"] == "python"  # pre-commit builds the environment itself
        assert h["pass_filenames"] == "false"  # every file, as CI checks it


def test_each_hook_runs_what_its_workflow_runs():
    assert hook("code-rules")["args"] == "[--all]"
    assert hook("code-rules-node")["args"] == "[--all, --eslint, --no-format]"
    node_lint = (WORKFLOWS / "node-lint.yml").read_text()
    assert "args=(--eslint --no-format)" in node_lint
    assert 'python3 "$tool" --all "${args[@]}"' in node_lint
    assert (
        'python3 "$tool" --all "${args[@]}"'
        in (WORKFLOWS / "python-lint.yml").read_text()
    )


def test_each_entry_is_a_console_script_of_a_module_under_tools():
    scripts = PYPROJECT["project"]["scripts"]
    for h in manifest_hooks():
        module, function = scripts[h["entry"]].split(":")
        assert (TOOLS / f"{module}.py").is_file()
        source = (TOOLS / f"{module}.py").read_text()
        assert re.search(rf"^def {function}\(argv=None\)", source, re.MULTILINE)


# the package: every module under tools/, with no list to keep, and the pinned ruff


def test_the_package_holds_every_module_under_tools():
    assert PYPROJECT["build-system"]["build-backend"] == "hatchling.build"
    wheel = PYPROJECT["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel["sources"] == ["tools"]  # installed at the top level, as siblings
    packaged = {p for pattern in wheel["include"] for p in ROOT.glob(pattern)}
    assert packaged == set(TOOLS.rglob("*.py"))  # one in a sub-directory fails here
    assert TOOLS / "code_rules.py" in packaged


def test_ruff_is_one_version_everywhere():
    pin = ruff_pin()
    for name in ("python-lint.yml", "node-lint.yml"):
        default = re.search(
            r'ruff-version: \{type: string, default: "([\d.]+)"',
            (WORKFLOWS / name).read_text(),
        )
        assert default.group(1) == pin, name
    for config in (TEMPLATE, OWN_CONFIG):
        hooks_rev = re.search(
            r"repo: https://github\.com/astral-sh/ruff-pre-commit\n"
            r"    rev: [0-9a-f]{40}  # v([\d.]+)\n",
            config.read_text(),
        )
        assert hooks_rev.group(1) == pin, config.name
    for path in (OWN_CONFIG, WORKFLOWS / "ci.yml"):
        named = re.findall(r"ruff==([\d.]+)", path.read_text())
        assert named and set(named) == {pin}, path.name


# the configs: the template names this repository at a commit, this repository
# runs the tool of its own checkout


def test_the_template_carries_the_hook_pinned_to_a_commit_with_its_version():
    pins = STANDARDS_HOOK_RE.findall(TEMPLATE.read_text())
    assert len(pins) == 1, "murphy360/standards, at a commit SHA, version in a comment"


def test_this_repository_runs_the_hook_on_its_own_checkout():
    text = OWN_CONFIG.read_text()
    local = text.split("  - repo: local\n", 1)[1].split("  - repo: ", 1)[0]
    assert "      - id: code-rules\n" in local
    assert "        entry: python3 tools/code_rules.py --all\n" in local
    assert "        language: python\n" in local  # its own environment, as the hook
    assert f'        additional_dependencies: ["ruff=={ruff_pin()}"]\n' in local
    assert "        pass_filenames: false\n" in local
    assert f"        name: {hook('code-rules')['name']}\n" in local
    assert not STANDARDS_HOOK_RE.search(text)  # never an older commit of itself


def test_both_configs_hold_the_same_hooks_apart_from_the_code_rules():
    def third_party(path: Path) -> list[str]:
        blocks = path.read_text().split("repos:\n", 1)[1].split("  - repo: ")[1:]
        return [
            re.sub(r"^ *#.*\n", "", block, flags=re.MULTILINE)
            for block in blocks
            if not block.startswith(("local", "https://github.com/murphy360/standards"))
        ]

    assert third_party(TEMPLATE) == third_party(OWN_CONFIG)
    assert len(third_party(TEMPLATE)) == 5


# shellcheck: one version in the workflow and in both configs (issue #9)


def test_shellcheck_is_one_version_in_the_workflow_and_the_configs():
    default = re.search(
        r'shellcheck-version: \{type: string, default: "v([\d.]+)"',
        (WORKFLOWS / "shell-lint.yml").read_text(),
    ).group(1)
    for config in (TEMPLATE, OWN_CONFIG):
        hook_pin = re.search(
            r"repo: https://github\.com/shellcheck-py/shellcheck-py\n"
            r"    rev: [0-9a-f]{40}  # v[\d.]+ \(shellcheck ([\d.]+)\)\n",
            config.read_text(),
        )
        assert hook_pin.group(1) == default, config.name
        assert f"shell-lint.yml's ({default})" in config.read_text()
    readme = (ROOT / "README.md").read_text()
    row = next(line for line in readme.splitlines() if "/shell-lint.yml` |" in line)
    assert f"v{default}" in row


# the documents: one command before a push, and no path inside a checkout of this
# repository


def test_no_document_tells_a_reader_to_run_the_code_rules_from_a_checkout():
    documents = [ROOT / "README.md", ROOT / "AGENTS.md", ROOT / "CLAUDE.md"]
    documents += [p for p in (ROOT / "templates").rglob("*") if p.is_file()]
    for path in documents:
        named = set(re.findall(r"<standards>/tools/([\w.-]+)", path.read_text()))
        assert named <= NEEDS_A_CHECKOUT, path.name
    for path in (ROOT / "templates").rglob("*"):
        if path.is_file():  # a project has no such file: the hook runs it
            assert "code_rules.py" not in path.read_text(), path.name


def test_the_agent_template_names_one_command_before_a_push():
    text = (ROOT / "templates" / "AGENTS.md").read_text()
    assert "Before a push, run `pre-commit run --all-files`." in text
    assert "It is the one command" in text
    assert "pip install ruff" not in text
    readme = (ROOT / "README.md").read_text()
    assert "See what is left: `pre-commit run --all-files`" in readme
    assert "| `.pre-commit-hooks.yaml` |" in readme


# the scratch project: the tool fails it on each of the three rules, run as a plain
# script (what the workflows do) and as the installed module (what the hook does)


@pytest.fixture
def project(tmp_path) -> Path:
    hook_project.write_project(tmp_path / "project")
    return tmp_path / "project"


def test_the_scratch_project_breaks_one_rule_in_each_file():
    files = hook_project.project_files()
    assert files["long.py"].count("\n") == 801
    assert files["tangled.py"].count("    if ") == 15  # complexity 16
    assert files["hidden.py"].count("# noqa") == 1


@needs_ruff
def test_the_tool_run_as_a_plain_script_fails_the_project_on_each_rule(project):
    res = subprocess.run(
        [sys.executable, str(TOOLS / "code_rules.py"), "--all"],
        cwd=project,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1, res.stderr
    for line in hook_project.EXPECTED:
        assert line in res.stdout.splitlines()


@needs_ruff
def test_the_installed_modules_print_what_the_script_prints(project, tmp_path):
    """The modules as the wheel installs them: side by side, away from tools/."""
    site = tmp_path / "site"
    site.mkdir()
    for pattern in PYPROJECT["tool"]["hatch"]["build"]["targets"]["wheel"]["include"]:
        for module in ROOT.glob(pattern):
            shutil.copy(module, site / module.name)
    module, function = PYPROJECT["project"]["scripts"]["code-rules"].split(":")
    console_script = (
        f"import sys; from {module} import {function}; sys.exit({function}())"
    )
    res = subprocess.run(
        [sys.executable, "-c", console_script, "--all"],
        cwd=project,
        env={**os.environ, "PYTHONPATH": str(site)},
        capture_output=True,
        text=True,
    )
    script = subprocess.run(
        [sys.executable, str(TOOLS / "code_rules.py"), "--all"],
        cwd=project,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1, res.stderr
    assert res.stdout == script.stdout
    assert hook_project.EXPECTED[0] in res.stdout
