"""The standards' tools on scratch repositories: the code rules and the standards check.

The code rules have no baseline and no changed-files mode: every run checks that every
file is clean. A finding is printed with its line, rule and fix; a ``# noqa`` hides
nothing and is a problem itself. The parts that run ruff are skipped where ruff is not
installed.
"""

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
needs_ruff = pytest.mark.skipif(shutil.which("ruff") is None, reason="no ruff")
DIRTY = "def f(x=[]):\n    return x\n"  # one B006, a mutable default, at 1:9


def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rules, standards = load("code_rules"), load("check_standards")


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout


def write(root: Path, files: dict):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def repo(tmp_path, files):
    """A git repository holding ``files``, all added, nothing committed."""
    write(tmp_path, {"ruff.toml": "# ruff's defaults\n", **files})
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.name", "Standards Tests")
    git(tmp_path, "config", "user.email", "tests@example.com")
    git(tmp_path, "add", "-A")
    return tmp_path


def commit(root: Path, message: str = "a change"):
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)


def dirty_base(tmp_path) -> Path:
    """A repository with one file with a finding and one without."""
    root = repo(tmp_path, {"pkg/dirty.py": DIRTY, "pkg/clean.py": 'message = "ok"\n'})
    commit(root, "a finding")
    return root


def run(root: Path, *args: str) -> int:
    return rules.main(["--root", str(root), *args])


# the file-size limit


def test_file_sizes_and_test_files(tmp_path):
    root = repo(
        tmp_path,
        {
            "src/a.py": "x = 1\n" * 801,
            "src/ok.py": "x = 1\n" * 800,
            "tests/test_a.py": "x = 1\n" * 900,
            "b.sh": "echo\n",
        },
    )
    files = rules.tracked_sources(root)
    assert rules.file_sizes(files, root, 800, 1200) == {"src/a.py": 801}
    assert rules.limit_for("tests/test_a.py") == 1200
    assert rules.limit_for("src/a.py") == 800


def test_there_is_no_baseline_and_no_update(capsys):
    assert not hasattr(rules, "compare") and not hasattr(rules, "lowered")
    assert not (ROOT / "code_rules_baseline.json").exists()
    with pytest.raises(SystemExit):
        rules.main(["--update"])
    assert "unrecognized arguments: --update" in capsys.readouterr().err


def test_the_migration_machinery_is_gone(capsys):
    """v3: no changed-files mode, no base, no format-only proof, no baseline note."""
    for name in ("changed_files", "base_ref", "format_only_problems", "syntax_tree"):
        assert not hasattr(rules, name), name
    assert "OLD_BASELINE" not in vars(rules) and "BASE_ENV" not in vars(rules)
    with pytest.raises(SystemExit):
        rules.main(["--format-only"])
    assert "unrecognized arguments: --format-only" in capsys.readouterr().err


def test_v2s_all_is_accepted_and_changes_nothing(capsys):
    """A v2 hook passes `--all`: it still parses, and is not in the help."""
    assert rules.parse_args(["--all"]).paths == rules.parse_args([]).paths
    with pytest.raises(SystemExit):
        rules.main(["--help"])
    assert "--all" not in capsys.readouterr().out


# every file


@needs_ruff
def test_a_file_with_a_finding_fails_whoever_changed_it(tmp_path, capsys):
    root = dirty_base(tmp_path)
    assert run(root) == 1
    out = capsys.readouterr().out
    assert "all: pkg/dirty.py: 1 ruff finding(s) (B006 1)" in out
    assert "pkg/clean.py" not in out


@needs_ruff
def test_a_change_not_committed_yet_is_checked(tmp_path, capsys):
    """A local run sees the working tree, before the commit."""
    root = repo(tmp_path, {"pkg/clean.py": 'message = "ok"\n'})
    commit(root)
    write(root, {"pkg/clean.py": DIRTY})
    assert run(root) == 1
    assert "all: pkg/clean.py: 1 ruff finding(s)" in capsys.readouterr().out


@needs_ruff
def test_a_fixed_file_passes_with_nothing_else_to_update(tmp_path, capsys):
    """What the baseline made a second step: the fix alone is the whole change."""
    root = dirty_base(tmp_path)
    write(root, {"pkg/dirty.py": "x = 1\n"})
    commit(root, "fix the finding")
    assert run(root) == 0, capsys.readouterr().out
    assert "left in the repository: 0 finding(s)" in capsys.readouterr().out


@needs_ruff
def test_a_file_name_git_would_quote_is_still_checked(tmp_path, capsys):
    root = repo(tmp_path, {"pkg/café.py": DIRTY})
    commit(root, "a file whose name is not ASCII")
    assert run(root) == 1
    assert "all: pkg/café.py: 1 ruff finding(s)" in capsys.readouterr().out


@needs_ruff
def test_a_project_in_a_subdirectory_sees_its_own_paths(tmp_path, capsys):
    """The workflow's working-directory: paths are relative to it."""
    root = repo(tmp_path, {"app/pkg/dirty.py": DIRTY, "other/x.py": DIRTY})
    commit(root)
    assert run(root / "app") == 1
    out = capsys.readouterr().out
    assert "all: pkg/dirty.py: 1 ruff finding(s)" in out and "other" not in out


@needs_ruff
def test_an_untracked_checkout_beside_the_project_is_not_counted(tmp_path, capsys):
    """A workflow pinned before the tools left the workspace still puts them there."""
    root = repo(tmp_path, {"pkg/clean.py": 'message = "ok"\n'})
    commit(root)
    write(root, {".standards/tools/dirty.py": DIRTY})
    assert run(root) == 0, capsys.readouterr().out
    assert "left in the repository: 0 finding(s)" in capsys.readouterr().out


@needs_ruff
def test_every_file_that_is_not_clean_is_listed(tmp_path, capsys):
    root = dirty_base(tmp_path)
    write(root, {"pkg/big.py": "x = 1\n" * 801, "pkg/ugly.py": "message = 'q'\n"})
    commit(root, "three files out of standard")
    assert run(root) == 1
    out = capsys.readouterr().out
    assert "all: pkg/dirty.py: 1 ruff finding(s) (B006 1)" in out
    assert "all: pkg/big.py: 801 lines, over its limit 800" in out
    assert "all: pkg/ugly.py: fails `ruff format --check`" in out
    assert "pkg/clean.py" not in out
    assert "code_rules: FAILED, 3 problem(s)" in out
    assert "1 finding(s), 1 file(s) over the size limit, 1 file(s) not formatted" in out
    assert run(root, "--no-format") == 1  # a project not re-formatted yet
    assert "file(s) not formatted" not in capsys.readouterr().out


@needs_ruff
def test_there_is_no_report_only_flag_a_finding_always_fails(tmp_path, capsys):
    """No flag lets a finding pass: --report-only is gone."""
    root = dirty_base(tmp_path)
    with pytest.raises(SystemExit) as refused:
        run(root, "--report-only")
    assert refused.value.code == 2
    assert run(root) == 1
    assert "code_rules: FAILED, 1 problem(s)" in capsys.readouterr().out


@needs_ruff
def test_paths_limit_every_check_to_those_directories(tmp_path, capsys):
    """How a project with debt adopts: it narrows paths to the clean directories."""
    root = repo(
        tmp_path,
        {
            "src/pkg/clean.py": 'message = "ok"\n',
            "scratch/dirty.py": DIRTY,
            "scratch/big.py": "x = 1\n" * 801,
        },
    )
    commit(root)
    assert run(root) == 1
    capsys.readouterr()
    assert run(root, "--paths", "src") == 0
    assert "0 finding(s), 0 file(s) over the size limit" in capsys.readouterr().out
    assert run(root, "--paths", "src", "scratch/dirty.py") == 1
    out = capsys.readouterr().out
    assert "all: scratch/dirty.py: 1 ruff finding(s)" in out
    assert "scratch/big.py" not in out


@needs_ruff
def test_a_clean_repository_passes(tmp_path, capsys):
    root = repo(tmp_path, {"pkg/clean.py": 'message = "ok"\n'})
    commit(root)
    assert run(root) == 0
    assert (
        "code_rules: OK (left in the repository: 0 finding(s), 0 file(s) over the "
        "size limit, 0 file(s) not formatted, 0 silencer(s))"
    ) in capsys.readouterr().out


@needs_ruff
def test_a_file_that_does_not_parse_is_a_problem_not_a_crash(tmp_path, capsys):
    root = repo(
        tmp_path, {"pkg/ok.py": 'message = "ok"\n', "pkg/broken.py": "def f(:\n"}
    )
    commit(root, "a file that does not parse")
    assert run(root) == 1
    out = capsys.readouterr().out
    assert "all: pkg/broken.py: fails `ruff format --check`" in out
    assert "all: pkg/broken.py" in out and "syntax" in out


@needs_ruff
def test_this_repository_meets_its_own_rules(capsys):
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    assert run(ROOT) == 0, capsys.readouterr().out


# the rule set (#35)


@needs_ruff
def test_the_formatter_owns_the_layout_and_the_lint_looks_for_bugs(tmp_path, capsys):
    """A long line is the formatter's (no E501, no W); B, UP, SIM and I are found."""
    text = (
        "import sys\nimport os\nfrom typing import Optional\n\n\n"  # I001: os first
        "def pairs(a: list, b: list) -> list:\n"
        "    return list(zip(a, b))  # " + "a long comment " * 6 + "\n"  # B905, no E501
        "\n\n"
        "def name(x: Optional[str], y: bool) -> str:\n"  # UP045
        "    if x:\n        if y:\n            return x\n"  # SIM102
        "    return os.sep + sys.prefix\n"
    )
    root = repo(tmp_path, {"pkg/mod.py": text})
    commit(root)
    assert run(root, "--no-format") == 1
    out = capsys.readouterr().out
    assert (
        "all: pkg/mod.py: 4 ruff finding(s) (B905 1, I001 1, SIM102 1, UP045 1)" in out
    )
    assert "E501" not in out and "W291" not in out


def test_the_standard_rules_are_the_owners_set():
    assert rules.config.RULES == (
        "F", "E4", "E7", "E9", "B", "UP", "SIM", "I", "C901", "PLR0915"
    )  # fmt: skip
    flags = " ".join(rules.config.STANDARD)
    assert "PLR0912" not in flags and "E501" not in flags and ",W" not in flags
    assert rules.config.LEAVE_OUT == ["--ignore", "B008"]


# each finding with its line, rule and fix


@needs_ruff
def test_a_finding_names_its_line_rule_and_fix(tmp_path, capsys):
    """The log alone says where the finding is and how to fix it."""
    root = dirty_base(tmp_path)
    assert run(root) == 1
    out = capsys.readouterr().out
    assert "  pkg/dirty.py:1:9: B006 Do not use mutable data structures" in out
    assert "Fix: `ruff rule B006` explains the rule and its fix" in out
    assert "pkg/clean.py" not in out
    assert "code_rules: FAILED, 1 problem(s)" in out


def test_each_kind_of_rule_has_a_fix():
    assert "E501" not in rules.FIXES  # no line length: the formatter's
    assert "__all__" in rules.fix_for("F401")
    assert "conftest.py" in rules.fix_for("F811")
    assert "allows none" in rules.fix_for("RUF100")
    assert "strict=True" in rules.fix_for("B905") and "strict=False" in rules.fix_for(
        "B905"
    )
    assert "from err" in rules.fix_for("B904")
    assert "lambda x=x" in rules.fix_for("B023")
    for code in ("C901", "PLR0915"):
        assert "named helpers" in rules.fix_for(code)
    assert rules.fix_for("B006") == "`ruff rule B006` explains the rule and its fix"
    assert rules.fix_for("UP045", safe_fix=True) == "`ruff check --fix` fixes it"
    assert rules.fix_for("I001", safe_fix=True) == "`ruff check --fix` fixes it"


def test_at_most_fifty_findings_are_explained():
    found = [
        {"rel": "a.py", "line": n, "col": 1, "code": "B006", "message": "default"}
        for n in range(1, 53)
    ]
    lines = rules.explained(found, {"a.py"})
    assert len(lines) == 51
    assert lines[0].startswith("  a.py:1:1: B006 default. Fix: ")
    assert lines[-1] == "  ... and 2 more finding(s)"
    assert rules.explained(found, {"b.py"}) == []


# no noqa: ruff runs with --ignore-noqa, and a noqa comment is a problem


@needs_ruff
def test_a_used_noqa_hides_nothing_and_fails(tmp_path, capsys):
    root = repo(
        tmp_path, {"pkg/clean.py": "def f(x=[]):  # noqa: B006\n    return x\n"}
    )
    commit(root, "hide a mutable default")
    assert run(root) == 1
    out = capsys.readouterr().out
    assert "all: pkg/clean.py: 1 ruff finding(s) (B006 1)" in out
    assert "  pkg/clean.py:1:9: B006" in out
    assert "noqa: pkg/clean.py:1: a `# noqa` is not allowed" in out


@needs_ruff
def test_a_noqa_comment_fails_even_when_it_hides_nothing(tmp_path, capsys):
    root = repo(tmp_path, {"pkg/clean.py": 'message = "ok"  # ruff: noqa\n'})
    commit(root, "an idle noqa")
    assert run(root) == 1
    assert "noqa: pkg/clean.py:1:" in capsys.readouterr().out


@needs_ruff
def test_the_word_noqa_in_a_string_is_not_a_comment(tmp_path, capsys):
    text = '"""A docstring that mentions # noqa."""\n\nmessage = "# noqa"\n'
    root = repo(tmp_path, {"pkg/clean.py": text})
    commit(root, "only the text")
    assert run(root) == 0, capsys.readouterr().out


# python-lint's code rules step: the shell python-lint.yml and the checks action run


LINT_STEP = TOOLS / "steps" / "code-rules.sh"


def test_the_workflows_check_every_file_and_hold_no_base_detection():
    """No mode, no format-only, no base: the shell left is the step script."""
    for name in ("python-lint.yml", "node-lint.yml"):
        text = (ROOT / ".github" / "workflows" / name).read_text()
        assert "  mode:" not in text and "format-only" not in text, name
        assert "CODE_RULES_BASE" not in text and "PR_BASE" not in text, name
        assert "ratchet" not in text and "--report-only" not in text, name
    step = LINT_STEP.read_text()
    assert "git " not in step and "CODE_RULES_BASE" not in step and "MODE" not in step
    assert 'exec python3 "$here/../code_rules.py" "${args[@]}"\n' in step


def test_node_lint_checks_every_file_and_has_no_baseline():
    """node-lint is python-lint's rule for JavaScript: clean files, no ratchet."""
    text = (ROOT / ".github" / "workflows" / "node-lint.yml").read_text()
    assert "ratchet" not in text and "code_rules_baseline" not in text
    assert "--update" not in text and "@v1" not in text and "@v2" not in text
    assert "node-lint.yml@v3" in text
    step = LINT_STEP.read_text()
    assert "--eslint" in step and "--prettier" in step and "--no-format" in step
    # any commit SHA with its version beside it: Dependabot moves the pin
    for action in ("actions/checkout", "actions/setup-python", "actions/setup-node"):
        assert re.search(rf"{action}@[0-9a-f]{{40}}  # v\d+\.\d+\.\d+\n", text), action
    assert "runner.environment == 'self-hosted'" in text
    assert "timeout-minutes: 15" in text
    py = (ROOT / ".github" / "workflows" / "python-lint.yml").read_text()
    assert (
        py.split("runs-on: ", 1)[1].split("\n", 1)[0]
        == text.split("runs-on: ", 1)[1].split("\n", 1)[0]
    )
    run = 'run: bash "$RUNNER_TEMP/standards/tools/steps/code-rules.sh" '
    assert run + "node\n" in text and run + "python\n" in py
    assert "git fetch" not in step


def _eslint(rel, code="no-unused-vars", message="'x' is unused.", line=1, col=1):
    return {
        "rel": rel,
        "line": line,
        "col": col,
        "code": code,
        "message": message,
        "tool": "eslint",
        "func": None,
    }


def test_eslint_findings_are_read_per_file_rule_and_function(tmp_path):
    out = json.dumps(
        [
            {
                "filePath": str(tmp_path / "src" / "a.ts"),
                "messages": [
                    {
                        "ruleId": "complexity",
                        "severity": 2,
                        "line": 4,
                        "column": 1,
                        "message": "Function 'digest' has a complexity of 16. "
                        "Maximum allowed is 15.",
                    },
                    {
                        "ruleId": "max-statements",
                        "message": "Arrow function has too many statements (61).",
                    },
                    {"ruleId": "no-unused-vars", "message": "'x' is unused."},
                    {
                        "ruleId": "no-unused-vars",
                        "severity": 1,
                        "message": "'kept' is unused.",
                    },
                    {"ruleId": "no-unused-vars", "message": "'y' is unused."},
                    {"ruleId": None, "message": "Parsing error"},
                ],
            },
            {"filePath": str(tmp_path / "b.js"), "messages": []},
            {
                "filePath": str(tmp_path / "other.ts"),
                "messages": [{"ruleId": "semi", "message": "missing"}],
            },
        ]
    )
    found = rules.parse_eslint(out, tmp_path, keep={"src/a.ts", "b.js"})
    assert rules.eslint_count(found) == {
        "src/a.ts::complexity::digest": 1,
        "src/a.ts::max-statements::?": 1,
        "src/a.ts::no-unused-vars": 2,
        "src/a.ts::syntax": 1,
    }
    assert "named helpers" in rules.fix_for("complexity", "eslint")
    assert "parses" in rules.fix_for("syntax", "eslint")
    assert rules.parse_prettier(
        "Checking formatting...\n[warn] src/a.ts\n"
        "[warn] Code style issues found in the above file. Forgot to run Prettier?\n"
    ) == ["src/a.ts"]


def test_a_typescript_file_with_an_eslint_finding_fails(tmp_path, capsys, monkeypatch):
    root = repo(
        tmp_path,
        {"src/a.ts": "export const a = 1;\n", "src/old.ts": "export const o = 1;\n"},
    )
    commit(root)
    monkeypatch.setattr(
        rules,
        "eslint_findings",
        lambda *a, **k: [_eslint("src/a.ts"), _eslint("src/old.ts", message="'o'")],
    )
    monkeypatch.setattr(rules, "ruff_findings", lambda *a, **k: pytest.fail("ran ruff"))
    assert run(root, "--eslint") == 1
    out = capsys.readouterr().out
    assert "all: src/a.ts: 1 eslint finding(s) (no-unused-vars 1)" in out
    assert "all: src/old.ts: 1 eslint finding(s)" in out
    assert "src/a.ts:1:1: no-unused-vars 'x' is unused." in out
    assert "the ESLint rule `no-unused-vars`" in out
    assert "2 finding(s)" in out


def test_a_clean_typescript_project_passes_and_skips_ruff(
    tmp_path, capsys, monkeypatch
):
    root = repo(tmp_path, {"src/a.ts": "export const a = 1;\n"})
    commit(root)
    monkeypatch.setattr(rules, "eslint_findings", lambda *a, **k: [])
    monkeypatch.setattr(rules, "ruff_findings", lambda *a, **k: pytest.fail("ran ruff"))
    assert run(root, "--eslint") == 0
    assert "code_rules: OK (left in the repository: 0 finding(s)" in (
        capsys.readouterr().out
    )


def test_a_typescript_file_over_the_size_limit_fails(tmp_path, capsys, monkeypatch):
    root = repo(tmp_path, {"src/a.ts": "export const a = 1;\n" * 801})
    commit(root, "grow")
    monkeypatch.setattr(rules, "eslint_findings", lambda *a, **k: [])
    assert run(root, "--eslint") == 1
    assert "all: src/a.ts: 801 lines, over its limit 800" in capsys.readouterr().out


def test_prettier_fails_every_unformatted_file(tmp_path, capsys, monkeypatch):
    root = repo(tmp_path, {"src/a.ts": "export const a = 1;\n", "README.md": "# hi\n"})
    commit(root)
    monkeypatch.setattr(rules, "eslint_findings", lambda *a, **k: [])
    monkeypatch.setattr(
        rules, "prettier_unformatted", lambda *a, **k: ["src/a.ts", "README.md"]
    )
    assert run(root, "--eslint", "--prettier") == 1
    out = capsys.readouterr().out
    assert "all: src/a.ts: fails `prettier --check`" in out
    assert "all: README.md: fails `prettier --check`" in out
    assert "2 file(s) failing prettier" in out


def test_paths_limit_eslint_and_prettier_to_those_files(tmp_path, monkeypatch):
    root = repo(
        tmp_path,
        {"src/a.ts": "export const a = 1;\n", "other/b.ts": "export const b = 1;\n"},
    )
    commit(root)
    seen = {}

    def fake(root, keep=None, extra=()):
        seen["keep"], seen["extra"] = keep, extra
        return []

    def prettier(root, paths):
        seen["paths"] = paths
        return []

    monkeypatch.setattr(rules, "eslint_findings", fake)
    monkeypatch.setattr(rules, "prettier_unformatted", prettier)
    assert run(root, "--eslint", "--prettier", "--paths", "src") == 0
    assert "src/a.ts" in seen["keep"] and "other/b.ts" not in seen["keep"]
    assert seen["paths"] == ("src",)
    assert seen["extra"] == ["--no-inline-config"]  # the default since v3
    assert run(root, "--eslint", "--inline-config") == 0
    assert seen["extra"] == ()


def test_eslint_needs_the_projects_eslint(tmp_path):
    root = repo(tmp_path, {"src/a.ts": "export const a = 1;\n"})
    with pytest.raises(SystemExit, match="eslint not found"):
        run(root, "--eslint")


def test_prettier_needs_the_projects_prettier(tmp_path, monkeypatch):
    root = repo(tmp_path, {"src/a.ts": "export const a = 1;\n"})
    commit(root)
    monkeypatch.setattr(rules, "eslint_findings", lambda *a, **k: [])
    with pytest.raises(SystemExit, match="prettier not found"):
        run(root, "--eslint", "--prettier")


def test_prettier_skips_files_it_has_no_parser_for(tmp_path):
    root = repo(
        tmp_path,
        {"web/src/a.ts": "export const a = 1;\n", "web/public/icon.svg": "<svg/>\n"},
    )
    exe = root / "node_modules" / ".bin" / "prettier"
    exe.parent.mkdir(parents=True)
    exe.write_text('#!/bin/sh\necho "$@" > "$(dirname "$0")/args"\n')
    exe.chmod(0o755)
    assert rules.prettier_unformatted(root, ()) == []
    args = (exe.parent / "args").read_text().split()
    assert "--ignore-unknown" in args
    assert "web/src/a.ts" in args and "web/public/icon.svg" not in args


def test_standards_check_finds_every_gap(tmp_path):
    wf = (
        "jobs:\n  a:\n    steps:\n"
        "      - uses: actions/checkout@v4\n      - uses: ./local\n"
    )
    root = repo(
        tmp_path,
        {
            ".github/workflows/ci.yml": wf,
            "pyproject.toml": "",
            "app/Dockerfile": "FROM x\n",
        },
    )
    gaps = standards.check(root)
    assert any("dependabot.yml is missing" in g for g in gaps)
    assert any("actions/checkout@v4" in g for g in gaps) and not any(
        "./local" in g for g in gaps
    )
    assert any("AGENTS.md is missing" in g for g in gaps)
    dep = (
        "version: 2\nupdates:\n"
        "  - package-ecosystem: github-actions\n    directory: /\n"
        "  - package-ecosystem: pip\n    directory: /\n"
    )
    (root / ".github" / "dependabot.yml").write_text(dep)
    (root / "CLAUDE.md").write_text("x")
    sha = "3d3c42e5aac5ba805825da76410c181273ba90b1"
    pinned = f"      - uses: actions/checkout@{sha}  # v7.0.1\n"
    (root / ".github/workflows/ci.yml").write_text(
        "jobs:\n  a:\n    steps:\n" + pinned + "  b:\n"
        "    uses: murphy360/standards/.github/workflows/python-lint.yml@v1\n"
    )
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    assert standards.check(root) == [
        ".github/dependabot.yml does not watch docker in /app"
    ]


# the pull request template and pre-commit config checks: opt-in in v2.1.0, on by
# default since v3. check() takes each as a keyword; the command line turns all on.


def test_check_takes_the_pr_template_and_pre_commit_checks_by_keyword(tmp_path):
    root = repo(
        tmp_path,
        {
            ".github/dependabot.yml": (
                "version: 2\nupdates:\n  - package-ecosystem: pip\n    directory: /\n"
            ),
            "CLAUDE.md": "x",
        },
    )
    assert standards.check(root) == []
    assert (
        standards.check(root, require_pr_template=True, require_pre_commit=True) != []
    )


def test_pr_template_check_finds_it_in_the_usual_places(tmp_path):
    base = {
        ".github/dependabot.yml": "version: 2\nupdates:\n",
        "CLAUDE.md": "x",
    }
    root = repo(tmp_path, base)
    gaps = standards.check(root, require_pr_template=True)
    assert any("pull_request_template.md is missing" in g for g in gaps)

    root = repo(tmp_path / "b", {**base, ".github/pull_request_template.md": ""})
    assert standards.check(root, require_pr_template=True) == []

    root = repo(
        tmp_path / "c",
        {**base, ".github/PULL_REQUEST_TEMPLATE/bug.md": ""},
    )
    assert standards.check(root, require_pr_template=True) == []


def test_pre_commit_check_wants_the_file_at_the_root(tmp_path):
    base = {
        ".github/dependabot.yml": "version: 2\nupdates:\n",
        "CLAUDE.md": "x",
    }
    root = repo(tmp_path, base)
    gaps = standards.check(root, require_pre_commit=True)
    assert any(".pre-commit-config.yaml is missing" in g for g in gaps)

    root = repo(tmp_path / "d", {**base, ".pre-commit-config.yaml": "repos: []\n"})
    assert standards.check(root, require_pre_commit=True) == []


NO_REQUIRE = [
    "--no-require-pr-template",
    "--no-require-pre-commit",
    "--no-require-ticket-template",
    "--no-require-current-templates",
    "--no-require-agent-settings",
    "--no-require-one-ruff-version",
]


def test_main_runs_every_check_unless_a_flag_turns_one_off(tmp_path, capsys):
    """v3: the six checks are on; each has a `--no-` flag, and v2's flag still parses."""
    root = repo(
        tmp_path,
        {
            ".github/dependabot.yml": "version: 2\nupdates:\n",
            "CLAUDE.md": "x",
        },
    )
    assert standards.main(["--root", str(root), *NO_REQUIRE]) == 0
    capsys.readouterr()
    assert standards.main(["--root", str(root)]) == 1
    out = capsys.readouterr().out
    assert "pull_request_template.md is missing" in out
    assert ".pre-commit-config.yaml is missing" in out
    assert ".claude/settings.json is missing" in out
    only_pr = [f for f in NO_REQUIRE if f != "--no-require-pr-template"]
    assert standards.main(["--root", str(root), *only_pr]) == 1
    out = capsys.readouterr().out
    assert "pull_request_template.md is missing" in out
    assert ".pre-commit-config.yaml" not in out
    flags = [*only_pr, "--require-pr-template"]  # v2's flag: on, as it already is
    assert standards.main(["--root", str(root), *flags]) == 1
