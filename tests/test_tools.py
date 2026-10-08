"""The standards' tools on scratch repositories: the code rules and the standards check.

The code rules have no baseline: ``--all`` checks that every file is clean, and
without it every file a branch changes must be clean. A finding is printed with its
line, rule and fix; a ``# noqa`` hides nothing and is a problem itself. The parts that
run ruff are skipped where ruff is not installed.
"""

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
needs_ruff = pytest.mark.skipif(shutil.which("ruff") is None, reason="no ruff")
LONG = "x = 1  # " + " ".join(["a long comment"] * 7) + "\n"  # one E501 at 88


def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rules, standards = load("code_rules"), load("check_standards")


@pytest.fixture(autouse=True)
def outside_ci(monkeypatch):
    """Each test says itself whether it runs in CI and against which base."""
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


def commit_base(root: Path, message: str = "base"):
    """Commit everything and call it origin/main."""
    commit(root, message)
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")


def dirty_base(tmp_path) -> Path:
    """A repository whose main has one file with a finding and one without."""
    root = repo(tmp_path, {"pkg/dirty.py": LONG, "pkg/clean.py": 'message = "ok"\n'})
    commit_base(root, "a finding nobody has touched")
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


def test_all_and_format_only_are_two_different_checks(capsys):
    with pytest.raises(SystemExit):
        rules.main(["--all", "--format-only"])
    assert "two different checks" in capsys.readouterr().err


# the changed-file rule


@needs_ruff
def test_a_changed_file_with_a_finding_fails(tmp_path, capsys):
    root = dirty_base(tmp_path)
    write(root, {"pkg/dirty.py": LONG + 'z = "touched"\n'})
    commit(root, "touch the dirty file")
    assert run(root) == 1
    assert "changed: pkg/dirty.py: 1 ruff finding(s) (E501 1)" in (
        capsys.readouterr().out
    )


@needs_ruff
def test_a_file_nobody_changed_keeps_its_finding(tmp_path, capsys):
    root = dirty_base(tmp_path)
    write(root, {"pkg/clean.py": 'message = "changed"\n'})
    commit(root, "touch only the clean file")
    assert run(root) == 0, capsys.readouterr().out
    out = capsys.readouterr().out
    assert "1 changed file(s) checked" in out
    assert "code_rules: OK (left in the repository: 1 finding(s), 0 file(s)" in out


@needs_ruff
def test_a_change_not_committed_yet_is_checked(tmp_path, capsys):
    """A local run sees the working tree, before the commit."""
    root = dirty_base(tmp_path)
    write(root, {"pkg/dirty.py": LONG + 'z = "touched"\n'})
    assert rules.changed_files(root) == (["pkg/dirty.py"], None)
    assert run(root) == 1
    assert "changed: pkg/dirty.py" in capsys.readouterr().out


@needs_ruff
def test_a_fixed_file_passes_with_nothing_else_to_update(tmp_path, capsys):
    """What the baseline made a second step: the fix alone is the whole change."""
    root = dirty_base(tmp_path)
    write(root, {"pkg/dirty.py": "x = 1\n"})
    commit(root, "fix the finding")
    assert run(root) == 0, capsys.readouterr().out
    assert "left in the repository: 0 finding(s)" in capsys.readouterr().out


@needs_ruff
def test_a_changed_file_over_the_size_limit_fails(tmp_path, capsys):
    big = "".join(f"v{i} = {i}\n" for i in range(900))
    root = repo(tmp_path, {"pkg/big.py": big})
    commit_base(root, "a large file")
    write(root, {"pkg/big.py": big.replace("v0 = 0", "v0 = 1")})
    commit(root, "touch the large file")
    assert run(root) == 1
    assert "changed: pkg/big.py: 900 lines, over its limit 800" in (
        capsys.readouterr().out
    )


@needs_ruff
def test_a_changed_python_file_must_be_formatted(tmp_path, capsys):
    root = repo(tmp_path, {"pkg/existing.py": 'message = "ok"\n'})
    commit_base(root)
    write(root, {"pkg/new.py": "message = 'needs formatting'\n"})
    commit(root, "add a file")
    assert run(root) == 1
    assert "ruff-format: pkg/new.py: fails `ruff format --check`" in (
        capsys.readouterr().out
    )
    assert run(root, "--no-format") == 0  # a project not re-formatted yet
    assert "file(s) not formatted" not in capsys.readouterr().out


@needs_ruff
def test_a_renamed_file_counts_as_changed(tmp_path, capsys):
    root = dirty_base(tmp_path)
    git(root, "mv", "pkg/dirty.py", "pkg/moved.py")
    commit(root, "rename the dirty file, content unchanged")
    assert rules.changed_files(root) == (["pkg/moved.py"], None)
    assert run(root) == 1
    assert "changed: pkg/moved.py: 1 ruff finding(s)" in capsys.readouterr().out


@needs_ruff
def test_a_file_name_git_would_quote_is_still_checked(tmp_path, capsys):
    root = repo(tmp_path, {"pkg/clean.py": 'message = "ok"\n'})
    commit_base(root)
    write(root, {"pkg/café.py": LONG})
    commit(root, "a file whose name is not ASCII")
    assert rules.changed_files(root) == (["pkg/café.py"], None)
    assert run(root) == 1
    assert "changed: pkg/café.py: 1 ruff finding(s)" in capsys.readouterr().out


@needs_ruff
def test_a_deleted_file_is_not_checked(tmp_path, capsys):
    root = dirty_base(tmp_path)
    git(root, "rm", "-q", "pkg/dirty.py")
    commit(root, "delete the dirty file")
    assert rules.changed_files(root) == ([], None)
    assert run(root) == 0, capsys.readouterr().out
    assert "left in the repository: 0 finding(s)" in capsys.readouterr().out


@needs_ruff
def test_a_changed_file_is_found_without_a_merge_base(tmp_path, capsys):
    """A shallow CI checkout: the base shares no history with HEAD.

    The trees are compared instead.
    """
    root = repo(tmp_path, {"pkg/existing.py": 'message = "ok"\n'})
    commit(root, "base")
    git(root, "checkout", "-q", "--orphan", "main-shallow")
    commit(root, "main, fetched with depth 1")
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(root, "checkout", "-q", "main")
    write(root, {"pkg/new.py": LONG})
    commit(root, "add a file")
    assert rules.changed_files(root) == (["pkg/new.py"], None)
    assert run(root) == 1
    assert "changed: pkg/new.py: 1 ruff finding(s)" in capsys.readouterr().out


@needs_ruff
def test_a_project_in_a_subdirectory_sees_its_own_paths(tmp_path, capsys):
    """The workflow's working-directory: paths are relative to it."""
    root = repo(tmp_path, {"app/pkg/dirty.py": LONG, "other/x.py": LONG})
    commit_base(root)
    write(root, {"app/pkg/dirty.py": LONG + "y = 2\n", "other/x.py": LONG + "y = 2\n"})
    commit(root, "touch both")
    assert rules.changed_files(root / "app") == (["pkg/dirty.py"], None)
    assert run(root / "app") == 1
    out = capsys.readouterr().out
    assert "changed: pkg/dirty.py: 1 ruff finding(s)" in out and "other" not in out


@needs_ruff
def test_an_untracked_checkout_beside_the_project_is_not_counted(tmp_path, capsys):
    """CI checks the standards out into .standards/, untracked, in the project."""
    root = repo(tmp_path, {"pkg/clean.py": 'message = "ok"\n'})
    commit_base(root)
    write(root, {".standards/tools/dirty.py": LONG})
    assert run(root, "--all") == 0, capsys.readouterr().out
    assert "left in the repository: 0 finding(s)" in capsys.readouterr().out


# the base


@needs_ruff
def test_outside_ci_a_missing_base_is_a_note(tmp_path, capsys):
    root = repo(tmp_path, {"pkg/new.py": LONG})
    commit(root, "a file, and no origin/main")
    assert run(root) == 0
    out = capsys.readouterr().out
    assert "skipped the changed-file checks because `origin/main` is not" in out
    assert "changed: pkg/new.py" not in out


@needs_ruff
@pytest.mark.parametrize("name", ["CI", "GITHUB_ACTIONS"])
def test_in_ci_a_missing_base_fails_and_says_how_to_fetch_it(
    tmp_path, capsys, monkeypatch, name
):
    """Without a base nothing would be checked: in CI that is a failure."""
    root = repo(tmp_path, {"pkg/clean.py": 'message = "ok"\n'})
    commit(root, "a clean file, and no origin/main")
    monkeypatch.setenv(name, "true")
    assert run(root) == 1
    out = capsys.readouterr().out
    assert "base: cannot tell what this branch changed" in out
    assert (
        "git fetch --no-tags --depth=1 origin "
        "+refs/heads/main:refs/remotes/origin/main" in out
    )
    assert "CODE_RULES_BASE" in out


@needs_ruff
def test_the_base_may_be_a_commit_named_in_the_environment(
    tmp_path, capsys, monkeypatch
):
    """A push is compared with the commit before it."""
    root = dirty_base(tmp_path)
    before = git(root, "rev-parse", "HEAD").strip()
    git(root, "update-ref", "-d", "refs/remotes/origin/main")
    write(root, {"pkg/dirty.py": LONG + 'z = "touched"\n'})
    commit(root, "touch the dirty file")
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("CODE_RULES_BASE", before)
    assert run(root) == 1
    assert "changed: pkg/dirty.py: 1 ruff finding(s)" in capsys.readouterr().out
    monkeypatch.setenv("CODE_RULES_BASE", "0" * 40)  # a commit that is not here
    assert run(root) == 1
    out = capsys.readouterr().out
    assert f"`{'0' * 40}` is not available" in out
    assert f"git fetch --no-tags --depth=1 origin {'0' * 40}" in out


@needs_ruff
def test_an_old_baseline_file_is_ignored_with_a_note(tmp_path, capsys):
    baseline = {"ruff": {"pkg/dirty.py::E501": 1}, "lines": {}}
    root = dirty_base(tmp_path)
    write(root, {"code_rules_baseline.json": json.dumps(baseline)})
    commit_base(root, "the ratchet's baseline, from before")
    write(root, {"pkg/dirty.py": LONG + 'z = "touched"\n'})
    commit(root, "touch the dirty file the baseline listed")
    assert run(root) == 1  # the baseline no longer excuses it
    out = capsys.readouterr().out
    assert "changed: pkg/dirty.py: 1 ruff finding(s)" in out
    assert out.count("code_rules_baseline.json is no longer read") == 1
    assert "delete it" in out


# --all


@needs_ruff
def test_all_lists_every_file_that_is_not_clean(tmp_path, capsys):
    root = dirty_base(tmp_path)
    write(root, {"pkg/big.py": "x = 1\n" * 801, "pkg/ugly.py": "message = 'q'\n"})
    commit_base(root, "three files out of standard, none changed by a branch")
    assert run(root) == 0  # nothing changed: nothing to check
    capsys.readouterr()
    assert run(root, "--all") == 1
    out = capsys.readouterr().out
    assert "all: pkg/dirty.py: 1 ruff finding(s) (E501 1)" in out
    assert "all: pkg/big.py: 801 lines, over its limit 800" in out
    assert "all: pkg/ugly.py: fails `ruff format --check`" in out
    assert "pkg/clean.py" not in out
    assert "code_rules: FAILED, 3 problem(s)" in out
    assert "1 finding(s), 1 file(s) over the size limit, 1 file(s) not formatted" in out


@needs_ruff
def test_there_is_no_report_only_flag_a_finding_always_fails(tmp_path, capsys):
    """No flag lets a finding pass: --report-only is gone."""
    root = dirty_base(tmp_path)
    with pytest.raises(SystemExit) as refused:
        run(root, "--all", "--report-only")
    assert refused.value.code == 2
    assert run(root, "--all") == 1
    assert "code_rules: FAILED, 1 problem(s)" in capsys.readouterr().out


@needs_ruff
def test_paths_limit_every_check_to_those_directories(tmp_path, capsys):
    root = repo(
        tmp_path,
        {
            "src/pkg/clean.py": 'message = "ok"\n',
            "scratch/dirty.py": LONG,
            "scratch/big.py": "x = 1\n" * 801,
        },
    )
    commit_base(root)
    assert run(root, "--all") == 1
    capsys.readouterr()
    assert run(root, "--all", "--paths", "src") == 0
    assert "0 finding(s), 0 file(s) over the size limit" in capsys.readouterr().out
    assert run(root, "--all", "--paths", "src", "scratch/dirty.py") == 1
    out = capsys.readouterr().out
    assert "all: scratch/dirty.py: 1 ruff finding(s)" in out
    assert "scratch/big.py" not in out


@needs_ruff
def test_all_passes_on_a_clean_repository(tmp_path, capsys):
    root = repo(tmp_path, {"pkg/clean.py": 'message = "ok"\n'})
    commit_base(root)
    assert run(root, "--all") == 0
    assert (
        "code_rules: OK (left in the repository: 0 finding(s), 0 file(s) over the "
        "size limit, 0 file(s) not formatted)"
    ) in capsys.readouterr().out


@needs_ruff
def test_a_file_that_does_not_parse_is_a_problem_not_a_crash(tmp_path, capsys):
    root = repo(tmp_path, {"pkg/ok.py": 'message = "ok"\n'})
    commit_base(root)
    write(root, {"pkg/broken.py": "def f(:\n"})
    commit(root, "a file that does not parse")
    assert run(root) == 1
    out = capsys.readouterr().out
    assert "ruff-format: pkg/broken.py" in out
    assert "changed: pkg/broken.py" in out and "syntax" in out


@needs_ruff
def test_this_repository_meets_its_own_rules(capsys):
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    assert run(ROOT, "--all") == 0, capsys.readouterr().out


# each finding with its line, rule and fix


@needs_ruff
def test_a_changed_files_finding_names_its_line_rule_and_fix(tmp_path, capsys):
    """The log alone says where the finding is and how to fix it."""
    root = dirty_base(tmp_path)
    write(root, {"pkg/dirty.py": LONG + 'z = "touched"\n'})
    commit(root, "touch the dirty file")
    assert run(root) == 1
    out = capsys.readouterr().out
    assert "  pkg/dirty.py:1:89: E501 Line too long" in out
    assert "Fix: wrap the line to the project's line length" in out
    assert "code_rules: FAILED, 1 problem(s)" in out


@needs_ruff
def test_all_names_each_finding_and_its_fix(tmp_path, capsys):
    root = dirty_base(tmp_path)
    assert run(root, "--all") == 1
    out = capsys.readouterr().out
    assert "  pkg/dirty.py:1:89: E501" in out
    assert "pkg/clean.py" not in out


@needs_ruff
def test_a_file_nobody_changed_is_not_explained(tmp_path, capsys):
    root = dirty_base(tmp_path)
    write(root, {"pkg/clean.py": 'message = "changed"\n'})
    commit(root, "touch only the clean file")
    assert run(root) == 0
    assert "Fix:" not in capsys.readouterr().out


def test_each_kind_of_rule_has_a_fix():
    assert "line length" in rules.fix_for("E501")
    assert "__all__" in rules.fix_for("F401")
    assert "conftest.py" in rules.fix_for("F811")
    assert "allows none" in rules.fix_for("RUF100")
    for code in ("C901", "PLR0912", "PLR0915"):
        assert "named helpers" in rules.fix_for(code)
    assert rules.fix_for("B006") == "`ruff rule B006` explains the rule and its fix"


def test_at_most_fifty_findings_are_explained():
    found = [
        {"rel": "a.py", "line": n, "col": 1, "code": "E501", "message": "long"}
        for n in range(1, 53)
    ]
    lines = rules.explained(found, {"a.py"})
    assert len(lines) == 51
    assert lines[0].startswith("  a.py:1:1: E501 long. Fix: ")
    assert lines[-1] == "  ... and 2 more finding(s)"
    assert rules.explained(found, {"b.py"}) == []


# no noqa: ruff runs with --ignore-noqa, and a noqa comment is a problem

LONG_LITERAL = 'x = "' + "a" * 95 + '"'  # one E501 at 88


@needs_ruff
def test_a_used_noqa_hides_nothing_and_fails(tmp_path, capsys):
    root = dirty_base(tmp_path)
    write(root, {"pkg/clean.py": LONG_LITERAL + "  # noqa: E501\n"})
    commit(root, "hide a long line")
    assert run(root) == 1
    out = capsys.readouterr().out
    assert "changed: pkg/clean.py: 1 ruff finding(s) (E501 1)" in out
    assert "  pkg/clean.py:1:89: E501" in out
    assert "noqa: pkg/clean.py:1: a `# noqa` is not allowed" in out


@needs_ruff
def test_a_noqa_comment_fails_even_when_it_hides_nothing(tmp_path, capsys):
    root = dirty_base(tmp_path)
    write(root, {"pkg/clean.py": 'message = "ok"  # ruff: noqa\n'})
    commit(root, "an idle noqa")
    assert run(root) == 1
    assert "noqa: pkg/clean.py:1:" in capsys.readouterr().out
    assert run(root, "--all") == 1
    assert "noqa: pkg/clean.py:1:" in capsys.readouterr().out


@needs_ruff
def test_the_word_noqa_in_a_string_is_not_a_comment(tmp_path, capsys):
    root = dirty_base(tmp_path)
    text = '"""A docstring that mentions # noqa."""\n\nmessage = "# noqa"\n'
    write(root, {"pkg/clean.py": text})
    commit(root, "only the text")
    assert run(root) == 0, capsys.readouterr().out


# --format-only: a branch that re-formats files and does nothing else

WIDE = (
    "def area(width, height):\n"
    "    '''The area.\n\n        Of a rectangle.\n    '''\n"
    "    total = {'width': width, 'height': height, 'area': width * height, "
    "'unit': 'm2'}\n"
    "    del (width, height)\n"
    "    return total\n"
)


def format_in_place(root: Path, rel: str):
    subprocess.run(
        ["ruff", "format", "--no-cache", rel], cwd=root, check=True, capture_output=True
    )


def wide_base(tmp_path) -> Path:
    """A repository whose main holds one file that is not formatted."""
    root = repo(tmp_path, {"pkg/wide.py": WIDE, "deploy/stack.yml": "services: {}\n"})
    commit_base(root, "a file nobody has formatted")
    return root


def test_the_syntax_tree_ignores_what_a_formatter_may_change():
    a = "def f():\n    '''Doc.\n\n        More.   \n    '''\n    del (x, y)\n"
    b = 'def f():\n    """Doc.\n\n    More.\n    """\n    del x, y\n'
    assert rules.syntax_tree(a) == rules.syntax_tree(b)
    assert rules.syntax_tree("x = 1\n") != rules.syntax_tree("x = 2\n")
    assert rules.syntax_tree('x = "a"\n') != rules.syntax_tree('x = "a "\n')
    # only a docstring is compared without its layout, never another string
    assert rules.syntax_tree("x = '''a\n  b'''\n") != rules.syntax_tree(
        "x = '''a\nb'''\n"
    )


@needs_ruff
def test_a_file_that_was_only_re_formatted_passes(tmp_path, capsys):
    root = wide_base(tmp_path)
    format_in_place(root, "pkg/wide.py")
    assert (root / "pkg" / "wide.py").read_text() != WIDE
    commit(root, "re-format")
    assert run(root, "--format-only") == 0, capsys.readouterr().out
    assert "format-only: 1 re-formatted file(s), each with the syntax tree it had" in (
        capsys.readouterr().out
    )


@needs_ruff
def test_a_changed_literal_is_not_a_re_format(tmp_path, capsys):
    root = wide_base(tmp_path)
    format_in_place(root, "pkg/wide.py")
    path = root / "pkg" / "wide.py"
    path.write_text(path.read_text().replace('"m2"', '"ft2"'))
    commit(root, "re-format, and one literal changed")
    assert run(root, "--format-only") == 1
    assert "format-only: pkg/wide.py: its code changed" in capsys.readouterr().out


@needs_ruff
def test_a_file_left_unformatted_fails_the_format_only_check(tmp_path, capsys):
    root = wide_base(tmp_path)
    write(root, {"pkg/wide.py": WIDE.replace("    return", "\n    return")})
    commit(root, "a blank line, and still not formatted")
    assert run(root, "--format-only") == 1
    assert "ruff-format: pkg/wide.py" in capsys.readouterr().out


@needs_ruff
@pytest.mark.parametrize(
    "change, words",
    [
        ("yaml", "deploy/stack.yml: is not a Python file"),
        ("add", "pkg/new.py: is added"),
        ("delete", "deploy/stack.yml: is deleted"),
    ],
)
def test_a_format_only_branch_changes_nothing_but_the_layout_of_python_files(
    tmp_path, capsys, change, words
):
    root = wide_base(tmp_path)
    format_in_place(root, "pkg/wide.py")
    if change == "yaml":
        write(root, {"deploy/stack.yml": "services: {a: {}}\n"})
    elif change == "add":
        write(root, {"pkg/new.py": 'message = "new"\n'})
    else:
        git(root, "rm", "-q", "deploy/stack.yml")
    commit(root, "re-format, and something else")
    assert run(root, "--format-only") == 1
    assert f"format-only: {words}" in capsys.readouterr().out


@needs_ruff
def test_a_file_that_does_not_parse_is_refused(tmp_path, capsys):
    root = wide_base(tmp_path)
    write(root, {"pkg/wide.py": "def area(:\n"})
    commit(root, "broken")
    assert run(root, "--format-only") == 1
    assert "format-only: pkg/wide.py: it does not parse" in capsys.readouterr().out


@needs_ruff
def test_format_only_needs_a_base_even_outside_ci(tmp_path, capsys):
    root = repo(tmp_path, {"pkg/clean.py": 'message = "ok"\n'})
    commit(root, "no origin/main")
    assert run(root, "--format-only") == 1
    assert "base: cannot prove a format-only change" in capsys.readouterr().out


@needs_ruff
def test_a_re_format_leaves_the_findings_it_found(tmp_path, capsys):
    """The reason for the mode: the changed file still has a finding, and passes."""
    root = repo(tmp_path, {"pkg/dirty.py": LONG + "y = {'a': 1}\n"})
    commit_base(root)
    format_in_place(root, "pkg/dirty.py")
    commit(root, "re-format a file with a long comment")
    assert run(root) == 1  # the changed-file rule refuses it
    assert "changed: pkg/dirty.py" in capsys.readouterr().out
    assert run(root, "--format-only") == 0
    assert "left in the repository: 1 finding(s)" in capsys.readouterr().out


# python-lint's code rules step, run as CI runs it: a shallow checkout of the pull
# request's merge commit, from a remote whose base branch has moved on since


def lint_step() -> str:
    """The shell of python-lint's "Code rules" step, as the workflow holds it."""
    text = (ROOT / ".github" / "workflows" / "python-lint.yml").read_text()
    block = text.split("- name: Code rules", 1)[1].split("run: |\n", 1)[1]
    lines = []
    for line in block.splitlines():
        if line.strip() and not line.startswith(" " * 10):
            break
        lines.append(line[10:])
    return "\n".join(lines) + "\n"


def moved_on_remote(tmp_path) -> dict:
    """A remote: main has a finding, a PR merged into it, then main moved on.

    The pull request changes only pkg/clean.py. After its merge commit was made,
    main changed pkg/dirty.py, which has a finding.
    """
    origin = dirty_base(tmp_path / "origin")
    git(origin, "config", "uploadpack.allowAnySHA1InWant", "true")
    base = git(origin, "rev-parse", "HEAD").strip()
    git(origin, "checkout", "-q", "-b", "pr")
    write(origin, {"pkg/clean.py": 'message = "the pull request"\n'})
    commit(origin, "the pull request")
    head = git(origin, "rev-parse", "HEAD").strip()
    git(origin, "checkout", "-q", "--detach", base)
    git(origin, "merge", "-q", "--no-ff", "-m", "Merge pr into main", "pr")
    merge = git(origin, "rev-parse", "HEAD").strip()
    git(origin, "checkout", "-q", "main")
    write(origin, {"pkg/dirty.py": LONG + "y = 2\n"})
    commit(origin, "main moves on, in a file the pull request never touched")
    tip = git(origin, "rev-parse", "HEAD").strip()
    return {"origin": origin, "base": base, "head": head, "merge": merge, "tip": tip}


def shallow_checkout(tmp_path, origin: Path, commit_sha: str) -> Path:
    """What actions/checkout does: one commit, depth 1."""
    ci = tmp_path / "ci"
    ci.mkdir()
    git(ci, "init", "-q")
    git(ci, "remote", "add", "origin", f"file://{origin}")
    git(ci, "fetch", "-q", "--no-tags", "--depth=1", "origin", commit_sha)
    git(ci, "checkout", "-q", "--detach", "FETCH_HEAD")
    return ci


def run_lint_step(
    tmp_path, ci: Path, pr_base: str, mode: str = "changed", event: str = "pull_request"
):
    workspace = tmp_path / "workspace"
    (workspace / ".standards").mkdir(parents=True)
    (workspace / ".standards" / "tools").symlink_to(TOOLS)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "CI": "true",
        "GITHUB_WORKSPACE": str(workspace),
        "MODE": mode,
        "FORMAT_CHECK": "true",
        "EVENT": event,
        "PR_BASE": pr_base,
        "BEFORE": "",
    }
    return subprocess.run(
        ["bash", "-e", "-c", lint_step()],
        cwd=ci,
        env=env,
        capture_output=True,
        text=True,
    )


@needs_ruff
def test_a_pull_request_is_compared_with_its_merge_commits_first_parent(tmp_path):
    """The base branch moved on after the merge commit: only the PR's files count.

    The pull request's base SHA is given as the moved-on tip, the worst case: the
    merge commit, not the event, says what the pull request was merged with.
    """
    r = moved_on_remote(tmp_path)
    ci = shallow_checkout(tmp_path, r["origin"], r["merge"])
    res = run_lint_step(tmp_path, ci, pr_base=r["tip"])
    assert res.returncode == 0, res.stdout + res.stderr
    assert f"code rules base: {r['base']}, the first parent of the merge" in res.stdout
    assert "code_rules: 1 changed file(s) checked" in res.stdout
    # the control: compared with the moved-on tip, the untouched file would fail it
    git(ci, "fetch", "-q", "--no-tags", "--depth=1", "origin", r["tip"])
    res = subprocess.run(
        ["python3", str(TOOLS / "code_rules.py")],
        cwd=ci,
        env={**os.environ, "CI": "true", "CODE_RULES_BASE": r["tip"]},
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1 and "changed: pkg/dirty.py" in res.stdout


@needs_ruff
def test_a_pull_request_without_a_merge_commit_uses_its_base_sha(tmp_path):
    r = moved_on_remote(tmp_path)
    ci = shallow_checkout(tmp_path, r["origin"], r["head"])
    res = run_lint_step(tmp_path, ci, pr_base=r["base"])
    assert res.returncode == 0, res.stdout + res.stderr
    assert f"code rules base: {r['base']}, the pull request's base" in res.stdout
    assert "code_rules: 1 changed file(s) checked" in res.stdout


@needs_ruff
def test_mode_all_checks_every_file_of_the_pull_request(tmp_path):
    """The default: a finding in a file the pull request never touched fails it."""
    r = moved_on_remote(tmp_path)
    ci = shallow_checkout(tmp_path, r["origin"], r["merge"])
    res = run_lint_step(tmp_path, ci, pr_base=r["base"], mode="all")
    assert res.returncode == 1, res.stdout + res.stderr
    assert "all: pkg/dirty.py: 1 ruff finding(s) (E501 1)" in res.stdout
    assert "  pkg/dirty.py:1:89: E501" in res.stdout


@needs_ruff
def test_mode_changed_without_a_base_checks_every_file(tmp_path):
    r = moved_on_remote(tmp_path)
    ci = shallow_checkout(tmp_path, r["origin"], r["merge"])
    res = run_lint_step(tmp_path, ci, pr_base="", event="schedule")
    assert res.returncode == 1, res.stdout + res.stderr
    assert "no base to compare with on a schedule event: every file is checked" in (
        res.stdout
    )
    assert "all: pkg/dirty.py" in res.stdout


def test_the_workflow_checks_every_file_by_default():
    text = (ROOT / ".github" / "workflows" / "python-lint.yml").read_text()
    assert 'mode: {type: string, default: "all"' in text
    assert "ratchet" not in text and "--report-only" not in text
    assert "export CODE_RULES_BASE" in lint_step()


# the standards check


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
    assert any("CLAUDE.md is missing" in g for g in gaps)
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


# the opt-in pull request template and pre-commit config checks (v2.1.0, default off)


def test_pr_template_and_pre_commit_checks_are_off_by_default(tmp_path):
    root = repo(
        tmp_path,
        {
            ".github/dependabot.yml": (
                "version: 2\nupdates:\n"
                "  - package-ecosystem: pip\n    directory: /\n"
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


def test_main_wires_the_require_flags(tmp_path, capsys):
    root = repo(
        tmp_path,
        {
            ".github/dependabot.yml": "version: 2\nupdates:\n",
            "CLAUDE.md": "x",
        },
    )
    assert standards.main(["--root", str(root)]) == 0
    assert standards.main(["--root", str(root), "--require-pr-template"]) == 1
    out = capsys.readouterr().out
    assert "pull_request_template.md is missing" in out
    assert standards.main(["--root", str(root), "--require-pre-commit"]) == 1
    out = capsys.readouterr().out
    assert ".pre-commit-config.yaml is missing" in out
