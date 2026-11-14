"""A project's own linter config cannot switch a standard rule off unseen.

The code rules run ruff and ESLint with the project's config. These tests give a
scratch repository each way a config could hide a standard finding, and check that
the run fails or says so. The ESLint parts run without Node: ESLint's findings are
made up, or its binary is a shell script. No network.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
needs_ruff = pytest.mark.skipif(shutil.which("ruff") is None, reason="no ruff")
BRANCHES = "".join(f"    if a == {i}:\n        return {i}\n" for i in range(17))
# F401, and one function over both the complexity (C901) and the branch (PLR0912) limit
DIRTY = f"import os\n\n\ndef f(a):\n{BRANCHES}    return 0\n"
IGNORE_FOR_EVERY_FILE = (
    '[lint.per-file-ignores]\n"*.py" = ["C901", "PLR0912", "F401"]\n'
)
CANNOT = "cannot switch a standard rule off"
IN_PYPROJECT = "[tool.ruff.lint.extend-per-file-ignores]\n"
UNUSED = "'hidden' is assigned a value but never used."
DISABLED = "// eslint-disable-next-line no-unused-vars\nconst hidden = 1;\n"


def load(name):
    """Load a tool the way tests/test_tools.py does: by its path, not from sys.path."""
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rules = load("code_rules")
config = rules.config


@pytest.fixture(autouse=True)
def outside_ci(monkeypatch):
    for name in ("CI", "GITHUB_ACTIONS", "CODE_RULES_BASE"):
        monkeypatch.delenv(name, raising=False)


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout


def write(root: Path, files: dict):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def repo(root: Path, files: dict) -> Path:
    """A git repository holding ``files``, committed, and called origin/main."""
    root.mkdir(parents=True, exist_ok=True)
    write(root, {"ruff.toml": "# ruff's defaults\n", **files})
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Standards Tests")
    git(root, "config", "user.email", "tests@example.com")
    commit(root, "base")
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    return root


def commit(root: Path, message: str):
    git(root, "add", "-A", "-f")
    git(root, "commit", "-q", "-m", message)


def run(root: Path, *args: str) -> int:
    return rules.main(["--root", str(root), "--no-format", *args])


# the sibling module


def test_the_tool_finds_the_module_beside_it_however_it_is_started(tmp_path):
    """Loaded by path (above), and run as a script from another directory."""
    assert config.__file__ == str(TOOLS / "code_rules_config.py")
    assert rules.STANDARD is config.STANDARD
    res = subprocess.run(
        [sys.executable, str(TOOLS / "code_rules.py"), "--help"],
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    assert "--no-inline-config" in res.stdout


def test_the_second_run_holds_the_standard_rules_alone():
    assert config.STANDARD[0] == "--extend-select"
    alone = config.STANDARD_ALONE
    assert alone[:2] == ["--select", "E,W,F,C901,PLR0912,PLR0915"]
    assert alone[2:8] == config.STANDARD[2:]  # the same three limits
    assert alone[8:] == [
        "--config",
        "lint.per-file-ignores={}",
        "--config",
        "lint.exclude=[]",
    ]


# ruff: a per-file ignore, and lint.exclude


@needs_ruff
def test_a_per_file_ignore_cannot_switch_a_standard_rule_off(tmp_path, capsys):
    """The ticket's first config: every file ignores three standard rules."""
    root = repo(tmp_path, {"ruff.toml": IGNORE_FOR_EVERY_FILE, "src/a.py": DIRTY})
    assert run(root, "--all") == 1
    out = capsys.readouterr().out
    assert "all: src/a.py: 3 ruff finding(s) (C901 1, F401 1, PLR0912 1)" in out
    assert (
        "  src/a.py:1:8: F401 `os` imported but unused. Fix: remove the import" in out
    )
    assert "  src/a.py:4:5: C901 `f` is too complex (18 > 15)" in out
    assert "  src/a.py:4:5: PLR0912 Too many branches (17 > 15)" in out
    assert out.count(f"a per-file ignore (or `lint.exclude`) {CANNOT}") == 3
    assert (
        "code_rules: FAILED, 1 problem(s) (left in the repository: 3 finding(s)" in out
    )


@needs_ruff
def test_the_deprecated_top_level_per_file_ignores_is_emptied_too(tmp_path, capsys):
    root = repo(
        tmp_path,
        {"ruff.toml": '[per-file-ignores]\n"*.py" = ["F"]\n', "src/a.py": DIRTY},
    )
    assert run(root, "--all") == 1
    assert "all: src/a.py: 3 ruff finding(s)" in capsys.readouterr().out


@needs_ruff
def test_a_per_file_ignore_in_a_directorys_own_config_hides_nothing(tmp_path, capsys):
    """ruff reads the nearest config: the second run empties the option in each."""
    root = repo(tmp_path, {"src/ruff.toml": IGNORE_FOR_EVERY_FILE, "src/a.py": DIRTY})
    assert run(root, "--all") == 1
    assert "all: src/a.py: 3 ruff finding(s)" in capsys.readouterr().out


@needs_ruff
def test_lint_exclude_cannot_switch_the_standard_rules_off(tmp_path, capsys):
    """A file under lint.exclude is still ruff's (it is formatted), so it is checked."""
    root = repo(
        tmp_path, {"ruff.toml": '[lint]\nexclude = ["a.py"]\n', "src/a.py": DIRTY}
    )
    assert run(root, "--all") == 1
    out = capsys.readouterr().out
    assert "all: src/a.py: 3 ruff finding(s) (C901 1, F401 1, PLR0912 1)" in out
    assert CANNOT in out and "ruff does not check" not in out


@needs_ruff
def test_a_per_file_ignore_for_a_rule_that_is_not_the_standards_still_works(
    tmp_path, capsys
):
    """T201 (a print) is the project's own rule: it may ignore it where it likes.

    The entry names F401 as well. It hides no finding, so it does no harm.
    """
    own_rule = (
        '[lint]\nextend-select = ["T20"]\n'
        '[lint.per-file-ignores]\n"scripts/*" = ["T201", "F401"]\n'
    )
    files = {"scripts/run.py": 'print("run")\n', "src/b.py": 'print("b")\n'}
    root = repo(tmp_path, {"ruff.toml": own_rule, **files})
    assert run(root, "--all") == 1
    out = capsys.readouterr().out
    assert "scripts/run.py" not in out
    assert "all: src/b.py: 1 ruff finding(s) (T201 1)" in out
    assert CANNOT not in out
    assert "left in the repository: 1 finding(s)" in out
    (root / "src" / "b.py").write_text('message = "b"\n')
    assert run(root, "--all") == 0, capsys.readouterr().out


@needs_ruff
def test_a_finding_both_runs_see_is_counted_once(tmp_path, capsys):
    """F401 with no per-file ignore: the first run reports it and so does the second."""
    root = repo(tmp_path, {"src/a.py": "import os\n"})
    assert run(root, "--all") == 1
    out = capsys.readouterr().out
    assert "all: src/a.py: 1 ruff finding(s) (F401 1)" in out
    assert out.count("src/a.py:1:8: F401") == 1 and CANNOT not in out
    assert "left in the repository: 1 finding(s)" in out


def test_only_a_finding_the_first_run_missed_is_added():
    def finding(code, line=1):
        return {"rel": "a.py", "line": line, "col": 1, "code": code, "message": "m"}

    def fake(root, keep=None, args=config.STANDARD):
        if args is config.STANDARD_ALONE:
            return [finding("F401"), finding("C901", line=4)]
        return [finding("F401"), finding("T201")]

    found = config.unhidden(Path("."), None, fake, lambda code: f"fix {code}")
    assert [f["code"] for f in found] == ["F401", "T201", "C901"]
    assert "fix" not in found[0] and "fix" not in found[1]
    assert found[2]["fix"] == f"fix C901; {config.HIDDEN}"
    line = rules.explained(found, {"a.py"})[-1]
    assert line == f"  a.py:4:1: C901 m. Fix: fix C901; {config.HIDDEN}"


@needs_ruff
def test_in_mode_changed_a_hidden_finding_fails_the_file_a_change_touches(
    tmp_path, capsys
):
    files = {"src/a.py": DIRTY, "src/old.py": DIRTY}
    root = repo(tmp_path, {"ruff.toml": IGNORE_FOR_EVERY_FILE, **files})
    (root / "src" / "a.py").write_text(DIRTY + "x = 1\n")
    commit(root, "touch one of the two")
    assert run(root) == 1
    out = capsys.readouterr().out
    assert "changed: src/a.py: 3 ruff finding(s) (C901 1, F401 1, PLR0912 1)" in out
    assert "old.py" not in out and "6 finding(s)" in out


# ruff: extend-per-file-ignores, which the command line cannot empty


COVERS = "ALL E E501 W W291 F F4 F401 C C9 C90 C901 PL PLR PLR09 PLR0912 PLR0915"
OWN = "B B006 EM ERA001 EXE FA FBT FURB C4 C408 PLC PLR0913 PLR2004 PLW T201 D UP"


@pytest.mark.parametrize("selector", COVERS.split())
def test_a_selector_that_covers_a_standard_rule(selector):
    assert config.names_standard_rule(selector)


@pytest.mark.parametrize("selector", ["", *OWN.split()])
def test_a_selector_for_the_projects_own_rules(selector):
    assert not config.names_standard_rule(selector)


def test_an_extend_per_file_ignore_that_names_a_standard_rule_is_a_problem(tmp_path):
    write(
        tmp_path,
        {
            "ruff.toml": "[lint.extend-per-file-ignores]\n"
            '"tests/*" = ["T201", "F401", "E"]\n"scripts/*" = ["T201"]\n',
            "tests/test_a.py": "x = 1\n",
        },
    )
    assert config.ignore_problems(tmp_path, ["tests/test_a.py"]) == [
        "ruff-config: ruff.toml: `extend-per-file-ignores` names F401, E for "
        f"`tests/*`; a per-file ignore {CANNOT}: remove it and fix the findings it hid"
    ]
    assert config.ignore_problems(tmp_path, ["README.md"]) == []  # ruff is not run


def test_every_config_ruff_reads_is_read(tmp_path):
    """pyproject.toml, a directory's own config, a file a config extends, and the
    deprecated top-level table. A config ruff does not read is left alone."""
    root = tmp_path / "project"
    write(
        tmp_path,
        {
            "project/pyproject.toml": '[tool.ruff]\nextend = "../shared/base.toml"\n'
            '[tool.ruff.lint.extend-per-file-ignores]\n"a.py" = ["C901"]\n',
            "shared/base.toml": '[extend-per-file-ignores]\n"b.py" = ["W"]\n',
            "project/pkg/.ruff.toml": "[lint.extend-per-file-ignores]\n"
            '"c.py" = ["PLR0915"]\n',
            # .ruff.toml wins in pkg, and nothing under other/ is a Python file
            "project/pkg/ruff.toml": '[lint.extend-per-file-ignores]\n"d.py" = ["F"]\n',
            "project/other/ruff.toml": "[lint.extend-per-file-ignores]\n"
            '"e.py" = ["F"]\n',
            "pyproject.toml": f'{IN_PYPROJECT}"f.py" = ["F"]\n',
            "project/pkg/deep/mod.py": "x = 1\n",
        },
    )
    lines = config.ignore_problems(root, ["pkg/deep/mod.py"])
    assert [line.split("; ")[0] for line in lines] == [
        "ruff-config: pkg/.ruff.toml: `extend-per-file-ignores` names PLR0915 for "
        "`c.py`",
        "ruff-config: pyproject.toml: `extend-per-file-ignores` names C901 for `a.py`",
        "ruff-config: ../shared/base.toml: `extend-per-file-ignores` names W for "
        "`b.py`",
    ]


def test_the_nearest_config_above_the_project_is_read_when_it_has_none(tmp_path):
    """A project in a subdirectory (working-directory) under the repository's config."""
    write(
        tmp_path,
        {
            "pyproject.toml": f'{IN_PYPROJECT}"a.py" = ["F"]\n',
            "backend/pyproject.toml": '[project]\nname = "backend"\n',  # no ruff table
            "backend/a.py": "x = 1\n",
        },
    )
    lines = config.ignore_problems(tmp_path / "backend", ["a.py"])
    assert len(lines) == 1 and lines[0].startswith("ruff-config: ../pyproject.toml: ")


def test_a_config_that_does_not_parse_is_left_to_ruff(tmp_path):
    write(tmp_path, {"ruff.toml": "[lint\n", "a.py": "x = 1\n"})
    assert config.ignore_problems(tmp_path, ["a.py"]) == []


def test_before_python_3_11_the_run_says_the_config_was_not_read(tmp_path, monkeypatch):
    write(
        tmp_path,
        {"ruff.toml": '[lint.extend-per-file-ignores]\n"a.py" = ["F"]\n', "a.py": ""},
    )
    assert len(config.ignore_problems(tmp_path, ["a.py"])) == 1
    monkeypatch.setattr(config, "tomllib", None)
    assert config.ignore_problems(tmp_path, ["a.py"]) == []
    notes = config.notes({"unchecked": [], "files": ["a.py"]})
    assert len(notes) == 1 and "needs Python 3.11 or newer" in notes[0]
    assert config.notes({"unchecked": [], "files": ["a.ts"]}) == []


@needs_ruff
def test_an_extend_per_file_ignore_fails_the_run(tmp_path, capsys):
    hidden = '[lint.extend-per-file-ignores]\n"*.py" = ["F401"]\n'
    root = repo(tmp_path, {"ruff.toml": hidden, "src/a.py": "import os\n"})
    assert run(root, "--all") == 1
    out = capsys.readouterr().out
    assert (
        "ruff-config: ruff.toml: `extend-per-file-ignores` names F401 for `*.py`" in out
    )
    assert "code_rules: FAILED, 1 problem(s)" in out
    # mode changed: a problem of the config's is reported when a change touches it
    (root / "src" / "a.py").write_text("import os\n\nx = 1\n")
    commit(root, "touch the file, not the config")
    assert run(root) == 0, capsys.readouterr().out
    (root / "ruff.toml").write_text(hidden + "# touched\n")
    commit(root, "touch the config")
    assert run(root) == 1
    assert "ruff-config: ruff.toml: " in capsys.readouterr().out


# ruff: a tracked file it does not check


@needs_ruff
def test_a_file_the_config_excludes_is_printed_and_counted(tmp_path, capsys):
    """The ticket's second config. Not a failure: tracked generated code is real."""
    root = repo(
        tmp_path,
        {
            "ruff.toml": 'extend-exclude = ["src"]\n',
            "src/a.py": DIRTY,
            "pkg/ok.py": "x = 1\n",
        },
    )
    assert run(root, "--all") == 0
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "code_rules: note: ruff does not check src/a.py: the project's ruff config, "
        "ruff's default excludes or a .gitignore leaves it out",
        "code_rules: OK (left in the repository: 0 finding(s), 0 file(s) over the "
        "size limit, 1 Python file(s) ruff does not check)",
    ]
    assert run(root) == 0  # mode changed says it as well: on every run
    assert "ruff does not check src/a.py" in capsys.readouterr().out


@needs_ruff
def test_ruffs_default_excludes_and_a_gitignore_are_not_silent_either(tmp_path, capsys):
    root = repo(
        tmp_path,
        {
            "dist/built.py": DIRTY,  # ruff excludes dist by default
            "local.py": DIRTY,
            ".gitignore": "local.py\n",  # tracked all the same (git add -f)
            "pkg/ok.py": "x = 1\n",
            "vendor.txt": "not Python\n",
        },
    )
    assert config.unchecked(root, rules.tracked_sources(root), rules.ruff_cmd()) == [
        "dist/built.py",
        "local.py",
    ]
    assert run(root, "--all") == 0
    assert "2 Python file(s) ruff does not check" in capsys.readouterr().out
    assert run(root, "--all", "--paths", "pkg") == 0  # only the files in --paths
    assert "ruff does not check" not in capsys.readouterr().out


@needs_ruff
def test_a_run_that_checks_every_file_says_nothing_new(tmp_path, capsys):
    root = repo(tmp_path, {"pkg/ok.py": "x = 1\n"})
    assert run(root, "--all") == 0
    assert capsys.readouterr().out == (
        "code_rules: OK (left in the repository: 0 finding(s), 0 file(s) over the "
        "size limit)\n"
    )


def test_no_python_file_means_ruff_is_not_asked(tmp_path, capsys, monkeypatch):
    root = repo(tmp_path, {"src/a.ts": "export const a = 1;\n"})
    monkeypatch.setattr(rules, "ruff_cmd", lambda: pytest.fail("asked ruff"))
    assert config.unchecked(root, ["src/a.ts"], ["no-such-ruff"]) == []
    assert run(root, "--all") == 0
    assert "ruff does not check" not in capsys.readouterr().out


# ESLint: --no-inline-config and the eslint-disable comment


def eslint_json(root: Path, rel: str, *messages: dict) -> str:
    """What ESLint prints with --format json for one file."""
    return json.dumps([{"filePath": str(root / rel), "messages": list(messages)}])


def unused(line: int) -> dict:
    return {
        "ruleId": "no-unused-vars",
        "severity": 2,
        "line": line,
        "column": 7,
        "message": UNUSED,
    }


def fake_eslint(root: Path, output: str = "[]") -> Path:
    """A project's ESLint that records its arguments and prints ``output``."""
    exe = root / "node_modules" / ".bin" / "eslint"
    exe.parent.mkdir(parents=True)
    (exe.parent / "output.json").write_text(output)
    exe.write_text(
        '#!/bin/sh\necho "$@" > "$(dirname "$0")/args"\n'
        'cat "$(dirname "$0")/output.json"\n'
    )
    exe.chmod(0o755)
    return exe.parent / "args"


def test_a_disable_comment_over_a_real_finding_both_are_reported(tmp_path, capsys):
    """ESLint runs with --no-inline-config, so it reports the finding under the
    comment (made up here, as ESLint prints it), and the comment is a problem."""
    root = repo(tmp_path, {"src/a.ts": DISABLED, ".gitignore": "node_modules/\n"})
    args = fake_eslint(root, eslint_json(root, "src/a.ts", unused(2)))
    assert run(root, "--eslint", "--all", "--no-inline-config") == 1
    out = capsys.readouterr().out
    assert "--no-inline-config" in args.read_text().split()
    assert "all: src/a.ts: 1 eslint finding(s) (no-unused-vars 1)" in out
    assert f"  src/a.ts:2:7: no-unused-vars {UNUSED}." in out
    assert (
        "eslint-disable: src/a.ts:1: an `eslint-disable` comment is not allowed; "
        "remove it and fix the finding it hid"
    ) in out
    assert (
        "code_rules: FAILED, 2 problem(s) (left in the repository: 1 finding(s)" in out
    )


def test_without_the_option_eslint_runs_as_it_did(tmp_path, capsys):
    """The default under v2: the comment still works, and nothing new is reported."""
    root = repo(tmp_path, {"src/a.ts": DISABLED, ".gitignore": "node_modules/\n"})
    args = fake_eslint(root)
    assert run(root, "--eslint", "--all") == 0, capsys.readouterr().out
    assert "--no-inline-config" not in args.read_text()
    assert args.read_text().split()[:2] == ["--format", "json"]
    assert "eslint-disable" not in capsys.readouterr().out


def test_in_mode_changed_a_disable_comment_fails_the_file_a_change_touches(
    tmp_path, capsys, monkeypatch
):
    root = repo(tmp_path, {"src/a.ts": DISABLED, "src/old.ts": DISABLED})
    (root / "src" / "a.ts").write_text(DISABLED + "export const a = 1;\n")
    commit(root, "touch one of the two")
    seen = []
    monkeypatch.setattr(rules, "eslint_findings", lambda *a: seen.append(a[2:]) or [])
    assert run(root, "--eslint", "--no-inline-config") == 1
    out = capsys.readouterr().out
    assert seen == [(["--no-inline-config"],)]
    assert "eslint-disable: src/a.ts:1: " in out and "old.ts" not in out


def test_every_form_of_the_disable_comment_is_found(tmp_path):
    source = "\n".join(
        [
            "/* eslint-disable */",  # 1
            "/* eslint-disable no-alert, no-console -- a reason */",  # 2
            "alert(1); // eslint-disable-line no-alert",  # 3
            "// eslint-disable-next-line",  # 4
            "/*eslint-disable-next-line no-alert*/",  # 5
            "/**",  # 6
            " * eslint-disable no-undef",  # 7: ESLint reads no directive after a star
            " */",  # 8
            "const jsx = <p>{/* eslint-disable-next-line no-undef */}</p>;",  # 9
            "/* eslint-enable */",  # 10: turns nothing off
            "// see the docs on eslint-disable",  # 11: not a directive
            "// eslint-disabled is not a directive",  # 12
            'const a = "// eslint-disable-next-line in a string";',  # 13
            "const b = '/* eslint-disable */';",  # 14
            "const c = `",  # 15
            "/* eslint-disable */ in a template ${a}`;",  # 16
            "const d = 'it\\'s // eslint-disable-line';",  # 17
            "const url = 'https://example.com'; // eslint-disable-line",  # 18
            "",
        ]
    )
    files = {f"src/a{suffix}": source for suffix in config.JS_SUFFIXES}
    write(tmp_path, {**files, "src/a.py": "# eslint-disable-next-line\n"})
    found = config.eslint_disable_comments(sorted([*files, "src/a.py"]), tmp_path)
    assert len(found) == 7 * len(config.JS_SUFFIXES)
    assert [line.split(": ")[1] for line in found if "src/a.tsx:" in line] == [
        f"src/a.tsx:{n}" for n in (1, 2, 3, 4, 5, 9, 18)
    ]
    assert {rules.problem_file(line) for line in found} == set(files)


def test_vendored_code_is_not_scanned(tmp_path):
    files = {
        "src/a.js": "/* eslint-disable */\n",
        "node_modules/x/index.js": "/* eslint-disable */\n",
        "web/vendor/lib.js": "/* eslint-disable */\n",
    }
    root = repo(tmp_path, files)
    a = rules.parse_args(["--eslint", "--no-inline-config"])
    assert config.problems(root, set(files), a) == [
        "eslint-disable: src/a.js:1: an `eslint-disable` comment is not allowed; "
        "remove it and fix the finding it hid"
    ]
    assert config.problems(root, set(files), rules.parse_args(["--eslint"])) == []


# node-lint.yml


def node_lint() -> str:
    return (ROOT / ".github" / "workflows" / "node-lint.yml").read_text()


# The shell of the "Code rules" step: node-lint.yml and the checks action run it.
STEP = ROOT / "tools" / "steps" / "code-rules.sh"


def test_node_lint_takes_no_inline_config_and_it_is_off_by_default():
    text = node_lint()
    assert (
        "      no-inline-config: {type: boolean, default: false, description:" in text
    )
    assert "          NO_INLINE_CONFIG: ${{ inputs.no-inline-config }}\n" in text
    assert text.endswith('tools/steps/code-rules.sh" node\n')
    assert '[ "$NO_INLINE_CONFIG" != true ] || args+=(--no-inline-config)' in (
        STEP.read_text()
    )
    python_lint = (ROOT / ".github" / "workflows" / "python-lint.yml").read_text()
    assert "strict-config" not in text + python_lint  # the ruff part is simply on


@pytest.mark.parametrize("given, fails", [("true", True), ("false", False)])
def test_the_node_lint_step_passes_the_option_to_the_tool(tmp_path, given, fails):
    """The step's own script, on a project whose ESLint is a script: no Node."""
    root = repo(tmp_path / "project", {"src/a.ts": DISABLED})
    args = fake_eslint(
        root, eslint_json(root, "src/a.ts", unused(2)) if fails else "[]"
    )
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "CI": "true",
        "MODE": "all",
        "FORMAT_CHECK": "false",
        "NO_INLINE_CONFIG": given,
        "PATHS": "",
        "EVENT": "schedule",
        "PR_BASE": "",
        "BEFORE": "",
    }
    res = subprocess.run(
        ["bash", str(STEP), "node"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == (1 if fails else 0), res.stdout + res.stderr
    assert ("--no-inline-config" in args.read_text()) is fails
    assert ("eslint-disable: src/a.ts:1: " in res.stdout) is fails
