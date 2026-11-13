"""tools/type_check.py and type-check.yml: pyright for Python, tsc for TypeScript.

Every checker here is made up: pyright's JSON and tsc's lines are written by hand, or
the binary is a shell script that prints them. No Node and no network. One test runs
the real pyright when it is on the PATH, and is skipped otherwise; this repository's
CI runs the real thing in its `types` jobs.
"""

import importlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
WORKFLOWS = ROOT / ".github" / "workflows"
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(TOOLS))  # type_check.py imports its sibling module
type_check = importlib.import_module("type_check")
code_rules = importlib.import_module("code_rules")

GENERATED = ".type-check-pyright.json"
MISSING = 'Cannot access attribute "no_such_attribute" for class "Version"'
MEMBER_FIX = (
    "use a name the object has: read its definition, then correct the spelling or "
    "add what is missing"
)
TSC_OUTPUT = """\
src/bad.ts(2,26): error TS2339: Property 'missing' does not exist on type 'string'.
src/bad.ts(3,7): error TS2322: Type 'A' is not assignable to type 'B'.
  Types of parameters 'x' and 'x' are incompatible.
    Type 'number' is not assignable to type 'string'.
error TS18003: No inputs were found in config file '/p/tsconfig.json'.
"""


def diagnostic(
    root: Path, rel: str, line: int, col: int, message: str, rule: str | None, **more
) -> dict:
    """One entry of pyright's ``generalDiagnostics``; it counts from 0."""
    entry = {
        "file": str(root / rel),
        "severity": "error",
        "message": message,
        "range": {
            "start": {"line": line, "character": col},
            "end": {"line": line, "character": col + 5},
        },
        **more,
    }
    if rule:
        entry["rule"] = rule
    return entry


def pyright_json(entries: list[dict], files: int = 3, warnings: int = 0) -> str:
    """What ``pyright --outputjson`` prints."""
    errors = sum(1 for e in entries if e["severity"] == "error")
    return json.dumps(
        {
            "version": "9.9.9",
            "time": "1794613523229",
            "generalDiagnostics": entries,
            "summary": {
                "filesAnalyzed": files,
                "errorCount": errors,
                "warningCount": warnings,
                "informationCount": 0,
                "timeInSec": 0.5,
            },
        }
    )


def missing_attribute(root: Path) -> dict:
    """The fault the workflow is for: a call to an attribute that does not exist."""
    message = f'{MISSING}\n  Attribute "no_such_attribute" is unknown'
    return diagnostic(
        root, "src/release.py", 14, 24, message, "reportAttributeAccessIssue"
    )


def script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


@pytest.fixture
def fake_pyright(tmp_path, monkeypatch):
    """A pyright that is a shell script. It keeps its arguments and the config the
    tool generated for it, prints the JSON it is given and exits as it is told."""
    seen = tmp_path / "seen"
    seen.mkdir()
    script(
        tmp_path / "bin" / "pyright",
        f'printf "%s\\n" "$@" > "{seen}/args"\n'
        f'[ ! -f {GENERATED} ] || cp {GENERATED} "{seen}/config"\n'
        f'cat "{seen}/out"\n'
        f'exit "$(cat "{seen}/status")"\n',
    )
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")

    def give(output: str, status: int = 0) -> Path:
        (seen / "out").write_text(output)
        (seen / "status").write_text(str(status))
        return seen

    return give


def project(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "project"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def fake_tsc(root: Path, output: str, status: int) -> Path:
    """The project's own tsc, as a shell script in its node_modules."""
    args = root / "tsc-args"
    script(
        root / "node_modules" / ".bin" / "tsc",
        'if [ "$1" = --version ]; then echo "Version 6.0.3"; exit 0; fi\n'
        f'printf "%s\\n" "$@" > "{args}"\n'
        f"cat << 'EOF'\n{output}EOF\n"
        f"exit {status}\n",
    )
    return args


# pyright's output


def test_a_call_to_a_missing_attribute_is_printed_with_where_it_is(tmp_path):
    """The line says the file, the line, the column, the rule and the fix."""
    found, _ = type_check.parse_pyright(
        pyright_json([missing_attribute(tmp_path)]), tmp_path
    )
    assert type_check.report(found, "pyright") == [
        "types: src/release.py: 1 pyright finding(s) (reportAttributeAccessIssue 1)",
        f"  src/release.py:15:25: reportAttributeAccessIssue {MISSING}. "
        f"Fix: {MEMBER_FIX}",
    ]


def test_a_finding_has_the_form_the_code_rules_print(tmp_path):
    """One function prints both, so the two cannot drift apart."""
    found, _ = type_check.parse_pyright(
        pyright_json([missing_attribute(tmp_path)]), tmp_path
    )
    lines = type_check.report(found, "pyright")
    assert lines[1:] == code_rules.explained(found, {"src/release.py"})
    ruff = {"rel": "a.py", "line": 3, "col": 1, "code": "F401", "message": "unused"}
    shape = r"  \S+:\d+:\d+: \S+ .+\. Fix: .+"
    assert re.fullmatch(shape, lines[1])
    assert re.fullmatch(shape, code_rules.explained([ruff], {"a.py"})[0])


def test_only_the_first_line_of_a_message_is_printed(tmp_path):
    found, _ = type_check.parse_pyright(
        pyright_json([missing_attribute(tmp_path)]), tmp_path
    )
    assert found[0]["message"] == MISSING


def test_a_warning_is_counted_and_is_not_a_finding(tmp_path):
    warning = diagnostic(
        tmp_path, "a.py", 0, 0, '"x" is possibly unbound', "reportPossiblyUnbound"
    ) | {"severity": "warning"}
    found, summary = type_check.parse_pyright(
        pyright_json([warning], warnings=1), tmp_path
    )
    assert found == []
    assert summary["warningCount"] == 1 and summary["version"] == "9.9.9"


def test_an_error_pyright_names_no_rule_for_is_still_a_finding(tmp_path):
    entry = diagnostic(tmp_path, "a.py", 2, 0, "Expected expression", None)
    found, _ = type_check.parse_pyright(pyright_json([entry]), tmp_path)
    assert [(f["rel"], f["line"], f["col"], f["code"]) for f in found] == [
        ("a.py", 3, 1, "error")
    ]
    assert found[0]["fix"].startswith("pyright names no rule for this")


def test_a_rule_with_no_fix_of_its_own_names_where_to_read_about_it(tmp_path):
    entry = diagnostic(tmp_path, "a.py", 0, 0, "No overloads", "reportSomethingNew")
    found, _ = type_check.parse_pyright(pyright_json([entry]), tmp_path)
    assert "`reportSomethingNew`" in found[0]["fix"]
    assert "`TS9999`" in type_check.fix_for("TS9999", "tsc")


def test_a_file_outside_the_project_keeps_its_own_path(tmp_path):
    entry = diagnostic(Path("/elsewhere"), "lib.py", 0, 0, "wrong", "reportCallIssue")
    found, _ = type_check.parse_pyright(pyright_json([entry]), tmp_path)
    assert found[0]["rel"] == "/elsewhere/lib.py"


def test_output_that_is_not_json_stops_the_run(tmp_path):
    with pytest.raises(SystemExit, match="pyright did not return JSON"):
        type_check.parse_pyright("No configuration file found.", tmp_path)


def test_at_most_fifty_findings_are_shown_and_the_rest_are_counted(tmp_path):
    entries = [
        diagnostic(tmp_path, "a.py", n, 0, f"wrong {n}", "reportCallIssue")
        for n in range(60)
    ]
    found, _ = type_check.parse_pyright(pyright_json(entries), tmp_path)
    lines = type_check.report(found, "pyright")
    assert lines[0] == "types: a.py: 60 pyright finding(s) (reportCallIssue 60)"
    assert len(lines) == 1 + code_rules.MAX_EXPLAINED + 1
    assert lines[-1] == "  ... and 10 more finding(s)"


# tsc's output


def test_tsc_errors_are_read_one_per_error_line():
    found = type_check.parse_tsc(TSC_OUTPUT)
    assert [(f["rel"], f["line"], f["col"], f["code"]) for f in found] == [
        ("src/bad.ts", 2, 26, "TS2339"),
        ("src/bad.ts", 3, 7, "TS2322"),
        ("tsconfig.json", 1, 1, "TS18003"),  # no file: it is about the config
    ]
    assert found[0]["message"] == "Property 'missing' does not exist on type 'string'"
    assert found[0]["fix"] == MEMBER_FIX


def test_a_tsc_finding_is_printed_like_a_pyright_one():
    lines = type_check.report(type_check.parse_tsc(TSC_OUTPUT), "tsc")
    assert lines[0] == "types: src/bad.ts: 2 tsc finding(s) (TS2322 1, TS2339 1)"
    assert lines[2] == (
        "  src/bad.ts:2:26: TS2339 Property 'missing' does not exist on type "
        f"'string'. Fix: {MEMBER_FIX}"
    )
    assert not [line for line in lines if "Types of parameters" in line]


def test_tsc_output_with_no_error_holds_no_finding():
    assert type_check.parse_tsc("") == []
    assert type_check.parse_tsc("Version 6.0.3\ntsc: The TypeScript Compiler\n") == []


# the project's pyright config, and the mode


@pytest.mark.parametrize(
    "files, expected",
    [
        ({}, (None, None)),
        ({"pyproject.toml": "[tool.ruff]\nline-length = 100\n"}, (None, None)),
        (
            {"pyproject.toml": '[tool.pyright]\ninclude = ["src"]\n'},
            ("pyproject.toml", None),
        ),
        (
            {"pyproject.toml": '[tool.pyright]\ntypeCheckingMode = "strict"\n'},
            ("pyproject.toml", "strict"),
        ),
        (
            {"pyproject.toml": '[tool.pyright]\nextends = "base.json"\n'},
            ("pyproject.toml", "extends"),
        ),
        ({"pyrightconfig.json": '{"include": ["src"]}'}, ("pyrightconfig.json", None)),
        (
            {"pyrightconfig.json": '{"typeCheckingMode" : "standard"}'},
            ("pyrightconfig.json", "standard"),
        ),
        (
            {"pyrightconfig.json": '{"extends": "base.json"}'},
            ("pyrightconfig.json", "extends"),
        ),
        (  # pyright reads pyrightconfig.json first
            {
                "pyrightconfig.json": "{}",
                "pyproject.toml": '[tool.pyright]\ntypeCheckingMode = "strict"\n',
            },
            ("pyrightconfig.json", None),
        ),
    ],
)
def test_the_projects_config_and_the_mode_it_sets(tmp_path, files, expected):
    assert type_check.project_config(project(tmp_path, {"a.py": "", **files})) == (
        expected
    )


def test_a_mode_in_a_comment_is_not_a_mode(tmp_path):
    """pyrightconfig.json may hold comments, and the reason for an include is one."""
    text = (
        "{\n"
        '  // "typeCheckingMode": "strict" comes later; see "extends": below\n'
        '  /* "extends": "base.json" */\n'
        '  "include": ["src"], // only src: the rest has debt, http://example.com\n'
        "}\n"
    )
    root = project(tmp_path, {"pyrightconfig.json": text})
    assert type_check.project_config(root) == ("pyrightconfig.json", None)


def test_a_pyproject_that_does_not_parse_stops_the_run(tmp_path):
    root = project(tmp_path, {"pyproject.toml": "[tool.pyright]\ninclude = [\n"})
    with pytest.raises(SystemExit, match="pyproject.toml does not parse"):
        type_check.project_config(root)


# a run with pyright


def test_with_no_config_pyright_runs_in_basic_mode(tmp_path, fake_pyright, capsys):
    root = project(tmp_path, {"src/a.py": "x = 1\n"})
    seen = fake_pyright(pyright_json([], warnings=1))
    assert type_check.main(["--root", str(root)]) == 0
    assert (seen / "args").read_text().split() == [
        "--outputjson",
        "--project",
        GENERATED,
    ]
    assert json.loads((seen / "config").read_text()) == {"typeCheckingMode": "basic"}
    assert capsys.readouterr().out == (
        "type_check: OK (pyright 9.9.9, mode basic: 0 error(s), 1 warning(s), "
        "3 file(s) analysed)\n"
    )


def test_the_generated_config_is_gone_after_the_run(tmp_path, fake_pyright):
    """Nothing is left in the project's workspace, whether the run passed or not."""
    root = project(tmp_path, {"src/a.py": "x = 1\n"})
    fake_pyright(pyright_json([]))
    assert type_check.main(["--root", str(root)]) == 0
    assert not (root / GENERATED).exists()
    fake_pyright("fatal", status=2)
    with pytest.raises(SystemExit):
        type_check.main(["--root", str(root)])
    assert not (root / GENERATED).exists()


@pytest.mark.parametrize(
    "name, text",
    [
        ("pyrightconfig.json", '{\n  "include": ["src"] // the reason\n}\n'),
        ("pyproject.toml", '[tool.pyright]\ninclude = ["src"]\n'),
    ],
)
def test_a_config_that_names_no_mode_is_extended_with_basic(
    tmp_path, fake_pyright, capsys, name, text
):
    """The project's include holds, and the mode is the standard's."""
    root = project(tmp_path, {"src/a.py": "x = 1\n", name: text})
    seen = fake_pyright(pyright_json([]))
    assert type_check.main(["--root", str(root)]) == 0
    assert json.loads((seen / "config").read_text()) == {
        "extends": name,
        "typeCheckingMode": "basic",
    }
    assert (root / name).read_text() == text
    assert "mode basic:" in capsys.readouterr().out


@pytest.mark.parametrize(
    "name, text, said",
    [
        (
            "pyproject.toml",
            '[tool.pyright]\ntypeCheckingMode = "strict"\n',
            "mode strict, set in pyproject.toml:",
        ),
        (
            "pyrightconfig.json",
            '{"extends": "base.json"}',
            "mode set by the config pyrightconfig.json extends:",
        ),
    ],
)
def test_a_config_that_decides_the_mode_is_left_alone(
    tmp_path, fake_pyright, capsys, name, text, said
):
    root = project(tmp_path, {"src/a.py": "x = 1\n", name: text})
    seen = fake_pyright(pyright_json([]))
    assert type_check.main(["--root", str(root)]) == 0
    assert (seen / "args").read_text().split() == ["--outputjson"]
    assert not (seen / "config").exists()
    assert said in capsys.readouterr().out


def test_a_finding_fails_the_run_and_the_last_line_counts(
    tmp_path, fake_pyright, capsys
):
    root = project(tmp_path, {"src/release.py": "x = 1\n"})
    entries = [
        missing_attribute(root),
        diagnostic(root, "src/other.py", 0, 4, "Expected 1 more", "reportCallIssue"),
    ]
    fake_pyright(pyright_json(entries), status=1)
    assert type_check.main(["--root", str(root)]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[:2] == [
        "types: src/other.py: 1 pyright finding(s) (reportCallIssue 1)",
        "types: src/release.py: 1 pyright finding(s) (reportAttributeAccessIssue 1)",
    ]
    assert out[2].startswith("  src/other.py:1:5: reportCallIssue Expected 1 more. ")
    assert out[3].startswith("  src/release.py:15:25: reportAttributeAccessIssue ")
    assert out[-1] == (
        "type_check: FAILED, 2 problem(s) (pyright 9.9.9, mode basic: 2 error(s), "
        "0 warning(s), 3 file(s) analysed)"
    )


def test_a_run_that_analysed_no_file_fails(tmp_path, fake_pyright, capsys):
    """A check that read nothing proves nothing."""
    config = '[tool.pyright]\ninclude = ["nowhere"]\n'
    root = project(tmp_path, {"pyproject.toml": config})
    fake_pyright(pyright_json([], files=0))
    assert type_check.main(["--root", str(root)]) == 1
    out = capsys.readouterr().out
    assert "  pyproject.toml:1:1: no-file pyright analysed no file. Fix: make" in out
    assert "type_check: FAILED, 1 problem(s)" in out


@pytest.mark.parametrize("status", [2, 3, 4])
def test_a_pyright_that_did_not_run_is_not_a_pass(tmp_path, fake_pyright, status):
    """pyright exits 3 on a config it cannot read, and still prints its JSON."""
    root = project(tmp_path, {"src/a.py": "x = 1\n"})
    fake_pyright(pyright_json([]), status=status)
    with pytest.raises(SystemExit, match="pyright did not run"):
        type_check.main(["--root", str(root)])


def test_no_pyright_says_how_to_get_one(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(SystemExit, match="pyright not found"):
        type_check.main(["--root", str(project(tmp_path, {"a.py": ""}))])


# a run with tsc


def test_tsc_runs_from_the_projects_node_modules_with_its_tsconfig(tmp_path, capsys):
    root = project(tmp_path, {"src/a.ts": "export const a = 1;\n"})
    args = fake_tsc(root, "", 0)
    assert type_check.main(["--root", str(root), "--language", "typescript"]) == 0
    assert args.read_text().split() == [
        "--noEmit",
        "--pretty",
        "false",
        "--project",
        ".",
    ]
    assert capsys.readouterr().out == "type_check: OK (tsc 6.0.3: 0 error(s))\n"


def test_a_tsc_error_fails_the_run(tmp_path, capsys):
    root = project(tmp_path, {"src/bad.ts": "x\n"})
    fake_tsc(root, TSC_OUTPUT, 2)
    assert type_check.main(["--root", str(root), "--language", "typescript"]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "types: src/bad.ts: 2 tsc finding(s) (TS2322 1, TS2339 1)"
    assert out[-1] == "type_check: FAILED, 2 problem(s) (tsc 6.0.3: 3 error(s))"


def test_a_tsc_that_failed_without_an_error_line_is_not_a_pass(tmp_path):
    root = project(tmp_path, {"src/a.ts": "x\n"})
    fake_tsc(root, "Segmentation fault\n", 139)
    with pytest.raises(SystemExit, match="tsc did not run"):
        type_check.main(["--root", str(root), "--language", "typescript"])


def test_no_tsc_says_what_to_install(tmp_path):
    root = project(tmp_path, {"src/a.ts": "x\n"})
    with pytest.raises(SystemExit, match="add typescript to devDependencies"):
        type_check.main(["--root", str(root), "--language", "typescript"])


def test_a_language_the_tool_does_not_check_is_refused(tmp_path, capsys):
    with pytest.raises(SystemExit):
        type_check.main(["--root", str(tmp_path), "--language", "go"])
    assert "invalid choice: 'go'" in capsys.readouterr().err


# the real pyright, where there is one


@pytest.mark.skipif(shutil.which("pyright") is None, reason="no pyright")
def test_the_real_pyright_finds_a_missing_attribute_and_passes_these_tools(tmp_path):
    source = "def shout(word: str) -> str:\n    return word.no_such_attribute()\n"
    root = project(tmp_path, {"src/a.py": source})
    tool = [sys.executable, str(TOOLS / "type_check.py")]
    res = subprocess.run([*tool, "--root", str(root)], capture_output=True, text=True)
    assert res.returncode == 1, res.stdout + res.stderr
    assert "  src/a.py:2:17: reportAttributeAccessIssue " in res.stdout
    own = subprocess.run([*tool, "--root", str(ROOT)], capture_output=True, text=True)
    assert own.returncode == 0, own.stdout + own.stderr


# type-check.yml, ci.yml, the fixtures and the template


def workflow() -> str:
    return (WORKFLOWS / "type-check.yml").read_text()


def step_shell(name: str) -> str:
    """The shell of one step of type-check.yml, as the workflow holds it."""
    block = workflow().split(f"- name: {name}", 1)[1].split("run: |\n", 1)[1]
    lines = []
    for line in block.splitlines():
        if line.strip() and not line.startswith(" " * 10):
            break
        lines.append(line[10:])
    return "\n".join(lines) + "\n"


def test_the_workflow_is_a_check_like_the_others():
    text = workflow()
    assert "permissions:\n  contents: read\n" in text
    assert "    timeout-minutes: 15\n" in text
    assert "        working-directory: ${{ inputs.working-directory }}\n" in text
    assert "if: runner.environment == 'self-hosted'" in text
    lint = (WORKFLOWS / "python-lint.yml").read_text()
    runs_on = [line for line in lint.splitlines() if "runs-on" in line]
    assert len(runs_on) == 2 and all(line in text for line in runs_on)


def test_pyright_is_pinned_and_nothing_new_is_on_by_default():
    text = workflow()
    pin = text.split("      pyright-version: {type: string, default: ", 1)[1]
    version = pin.split(",", 1)[0].strip('"')
    assert version.count(".") == 2 and version.replace(".", "").isdigit()
    assert '"pyright@$PYRIGHT_VERSION"' in text
    assert '      language: {type: string, default: "python",' in text
    assert '      install-command: {type: string, default: "",' in text
    # no input lets a finding pass
    inputs = text.split("    inputs:\n", 1)[1].split("permissions:", 1)[0]
    for word in ("baseline", "ignore", "allow", "continue-on-error", "warn"):
        assert word not in inputs
    assert "continue-on-error" not in text


def test_each_language_installs_only_what_it_needs():
    text = workflow()
    assert "if: inputs.language == 'python' && inputs.install-command != ''" in text
    assert "      - if: inputs.language == 'typescript'\n        run: npm ci\n" in text
    assert 'run: bash -euo pipefail -c "$INSTALL_COMMAND"' in text
    # pyright goes to the runner's temp directory, never into the project
    assert '--prefix "$RUNNER_TEMP/pyright"' in text
    assert 'python3 "$RUNNER_TEMP/standards/tools/type_check.py"' in text


@pytest.mark.parametrize(
    "language, status", [("python", 0), ("typescript", 0), ("Python", 1), ("", 1)]
)
def test_the_language_step_refuses_what_the_workflow_does_not_check(language, status):
    res = subprocess.run(
        ["bash", "-e", "-c", step_shell("The language is one this workflow checks")],
        env={"PATH": os.environ["PATH"], "LANGUAGE": language},
        capture_output=True,
        text=True,
    )
    assert res.returncode == status, res.stdout + res.stderr
    assert ("::error::language is python or typescript" in res.stdout) is bool(status)


def test_ci_runs_both_languages_and_this_repositorys_own_tools():
    text = (WORKFLOWS / "ci.yml").read_text()
    assert text.count("uses: ./.github/workflows/type-check.yml") == 3
    assert "      working-directory: tests/fixtures/python-project\n" in text
    assert "      install-command: pip install -r requirements.txt" in text
    assert "      language: typescript\n" in text


def test_this_repository_narrows_include_and_says_why():
    """The standard's own example of debt: the reason sits beside the include."""
    text = (ROOT / "pyproject.toml").read_text()
    assert type_check.project_config(ROOT) == ("pyproject.toml", None)
    above, table = text.split("[tool.pyright]\n", 1)
    assert table.splitlines()[0] == 'include = ["tools"]'
    reason = above.rstrip().splitlines()[-3:]
    assert all(line.startswith("# ") for line in reason)
    assert "tests/ is left out for now" in " ".join(reason)


def test_the_python_fixture_needs_its_dependency_installed():
    """No pyright config, so the basic mode; one import that only pip can resolve."""
    fixture = FIXTURES / "python-project"
    assert type_check.project_config(fixture) == (None, None)
    pins = [
        line
        for line in (fixture / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert len(pins) == 1 and "==" in pins[0]
    package = pins[0].split("==")[0]
    sources = list(fixture.rglob("*.py"))
    assert sources and f"from {package}." in sources[0].read_text()
    dependabot = (ROOT / ".github" / "dependabot.yml").read_text()
    assert "directory: /tests/fixtures/python-project\n" in dependabot


def test_the_node_fixture_has_a_tsconfig_for_its_source():
    node = FIXTURES / "node-project"
    config = json.loads((node / "tsconfig.json").read_text())
    assert config["include"] == ["src"]
    assert config["compilerOptions"]["strict"] is True
    assert list((node / "src").glob("*.ts"))
    package = json.loads((node / "package.json").read_text())
    assert "typescript" in package["devDependencies"]


def test_the_ci_template_shows_the_job_commented_out():
    text = (ROOT / "templates" / "ci.yml").read_text()
    job = "  #   uses: murphy360/standards/.github/workflows/type-check.yml@v2\n"
    assert "  # types:\n" + job in text
    assert "  #     install-command: " in text
    live = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    assert not [line for line in live if "type-check" in line]


def test_the_readme_says_what_a_project_passes_and_that_there_is_no_baseline():
    text = (ROOT / "README.md").read_text()
    assert "| `.github/workflows/type-check.yml` |" in text
    assert "| `tools/type_check.py` |" in text
    assert "- **Types are checked.**" in text
    assert "`install-command`" in text
    rule = text.split("- **Types are checked.**", 1)[1].split("\n- **", 1)[0]
    assert "no baseline" in rule and "`include`" in rule
