"""Every silencer carries its reason, and the summary counts them (--silencers).

A silencer reaches a green run without fixing anything: a pytest skip or xfail, a
type ignore, a coverage pragma, a shellcheck directive, a `.skip` or `.only` in a
JavaScript test. These tests give each kind to the scan with and without its reason,
then run the tool and both lint workflows' own shell on scratch repositories. Every
silencer below is text in a string, which is itself one of the cases. No network.
"""

import ast
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
WORKFLOWS = ROOT / ".github" / "workflows"
needs_ruff = pytest.mark.skipif(shutil.which("ruff") is None, reason="no ruff")
LEFT = "(left in the repository: 0 finding(s), 0 file(s) over the size limit"
QUIET = f"code_rules: OK {LEFT}"
SHELL_FIX = "or fix what shellcheck found"
ABOVE = "has no reason; say why in a comment on the line above"
NO_CODE = (
    "names no error code (write `# type: ignore[code]`, with the code the type "
    "checker prints)"
)
NO_WHY = (
    "has no reason (say why in a comment after it on the same line, or on the line "
    "above)"
)
UNQUOTED = "#!/bin/sh\n# shellcheck disable=SC2086\necho $1\n"
DIRECTIVE = "# shellcheck disable=SC2046"
SKIPPED = "// The API is not up in CI.\nit.skip('b', () => {});\n"
QUOTED_WHY = "#!/bin/sh\n# The caller passes several words on purpose.\n"


def load(name):
    """Load a tool the way tests/test_tools.py does: by its path, not from sys.path."""
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rules = load("code_rules")
silencers = rules.silencers


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


def commit(root: Path, message: str):
    git(root, "add", "-A", "-f")
    git(root, "commit", "-q", "-m", message)


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


def run(root: Path, *args: str) -> int:
    return rules.main(["--root", str(root), "--no-format", *args])


def brief(found: list) -> list:
    """Each silencer as (line, what it is, whether it carries its reason)."""
    return [(line, label, lacks is None) for line, label, lacks in found]


def in_python(tmp_path: Path, source: str) -> list:
    write(tmp_path, {"a.py": source})
    return brief(silencers.python_silencers(tmp_path / "a.py"))


# the module beside the tool


def test_the_tool_finds_the_module_and_offers_the_flag(tmp_path):
    """Loaded by path (above), and run as a script from another directory."""
    assert silencers.__file__ == str(TOOLS / "code_rules_silencers.py")
    res = subprocess.run(
        [sys.executable, str(TOOLS / "code_rules.py"), "--help"],
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    assert "--silencers" in res.stdout
    assert rules.parse_args([]).silencers is False
    assert rules.parse_args(["--silencers"]).silencers is True
    # the check for the one silencer that is never allowed moved here; it still runs
    assert rules.noqa_comments is silencers.noqa_comments


# pytest: a skip or an xfail, as a mark or as a call


@pytest.mark.parametrize(
    "mark, carries",
    [
        ("pytest.mark.skip", False),
        ("pytest.mark.skip()", False),
        ('pytest.mark.skip(reason="")', False),
        ('pytest.mark.skip(reason="  ")', False),
        ("pytest.mark.skip(reason=None)", False),
        ('pytest.mark.skip(reason="the service is gone")', True),
        ('pytest.mark.skip("the service is gone")', True),  # pytest reads it so
        ("pytest.mark.skip(reason=WHY)", True),
        ("pytest.mark.skip(**options)", True),
        ("pytest.mark.xfail", False),
        ("pytest.mark.xfail(strict=True)", False),
        ("pytest.mark.xfail(\"sys.platform == 'win32'\")", False),  # a condition
        ('pytest.mark.xfail(ON_WINDOWS, reason="no fork there")', True),
        ('pytest.mark.xfail(reason="the parser bug, issue 12")', True),
        ("pytest.mark.skipif(ON_WINDOWS)", False),
        ("pytest.mark.skipif(\"sys.platform == 'win32'\")", False),
        ('pytest.mark.skipif(ON_WINDOWS, reason="no fork there")', True),
    ],
)
def test_a_mark_needs_its_reason(mark, carries):
    source = f"import pytest\n\n\n@{mark}\ndef test_it():\n    pass\n"
    [(line, label, lacks)] = silencers.mark_silencers(ast.parse(source))
    assert (line, label) == (4, f"`{mark.split('(')[0]}`")
    assert lacks == (None if carries else silencers.NO_MARK_REASON)


@pytest.mark.parametrize(
    "call, carries",
    [
        ("pytest.skip()", False),
        ('pytest.skip("")', False),
        ("pytest.skip(allow_module_level=True)", False),
        ('pytest.skip("not a git checkout")', True),
        ('pytest.skip(reason="not a git checkout")', True),
        ('pytest.skip(msg="not a git checkout")', True),  # before pytest 7
        ("pytest.skip(f'no {tool}')", True),
        ("pytest.xfail()", False),
        ('pytest.xfail("the parser bug, issue 12")', True),
        ('pytest.importorskip("numpy")', True),  # the module's name is the reason
        ('pytest.importorskip("numpy", reason="the fast path")', True),
    ],
)
def test_a_skip_call_needs_its_text(call, carries):
    """At module level or in a test: the text is the reason."""
    module = f"import pytest\n\n{call}\n"
    function = f"import pytest\n\n\ndef test_it():\n    {call}\n"
    for source in (module, function):
        [(_, label, lacks)] = silencers.mark_silencers(ast.parse(source))
        assert label == f"`{call.split('(')[0]}()`"
        assert lacks == (None if carries else silencers.NO_CALL_REASON)


def test_a_mark_is_found_wherever_it_is_written():
    source = "\n".join(
        [
            "import pytest",  # 1
            "pytestmark = [pytest.mark.skip, pytest.mark.usefixtures('db')]",  # 2
            "slow = pytest.mark.skipif(FAST, reason='takes a minute')",  # 3
            "CASES = [pytest.param(1, marks=pytest.mark.xfail), 2]",  # 4
            "",
            "",
            "@slow",  # 7: the mark was counted where it was made
            "@pytest.mark.parametrize('n', CASES)",  # 8: no silencer
            "def test_it(n):",
            "    with pytest.raises(pytest.skip.Exception):",  # 10: no skip
            "        pytest.skip('inside')",  # 11
            "",
        ]
    )
    assert sorted(brief(silencers.mark_silencers(ast.parse(source)))) == [
        (2, "`pytest.mark.skip`", False),
        (3, "`pytest.mark.skipif`", True),
        (4, "`pytest.mark.xfail`", False),
        (11, "`pytest.skip()`", True),
    ]


def test_pytest_is_known_by_the_names_the_file_imports():
    source = "\n".join(
        [
            "import pytest as pt",
            "from pytest import mark, skip as leave, xfail",
            "",
            "",
            "@pt.mark.skip",  # 5
            "@mark.xfail",  # 6
            "def test_it():",
            "    leave()",  # 8
            "    xfail('known')",  # 9
            "",
        ]
    )
    assert sorted(brief(silencers.mark_silencers(ast.parse(source)))) == [
        (5, "`pytest.mark.skip`", False),
        (6, "`pytest.mark.xfail`", False),
        (8, "`pytest.skip()`", False),
        (9, "`pytest.xfail()`", True),
    ]


@pytest.mark.parametrize("imports", ["import other", "import other\nimport pytest"])
def test_a_skip_that_is_not_pytests_is_not_a_silencer(imports):
    source = (
        f"{imports}\n\n\n@other.mark.skip\ndef test_it(self):\n"
        "    self.mark.xfail\n    other.skip()\n    skip()\n    it.skip()\n"
    )
    assert silencers.mark_silencers(ast.parse(source)) == []


# Python comments: a type ignore and a coverage pragma


@pytest.mark.parametrize(
    "lines, expected",
    [
        (["x = f()  # type: ignore"], [(1, "`# type: ignore`", False)]),
        (
            ["x = f()  # type:ignore[arg-type]"],
            [(1, "`# type: ignore[arg-type]`", False)],
        ),
        (
            ["x = f()  # type: ignore[arg-type]  # the stub has no overload"],
            [(1, "`# type: ignore[arg-type]`", True)],
        ),
        (
            [
                "# The stub has no overload for bytes.",
                "x = f()  # type: ignore[arg-type]",
            ],
            [(2, "`# type: ignore[arg-type]`", True)],
        ),
        (  # a blank line between: the comment is about something else
            ["# The stub has no overload.", "", "x = f()  # type: ignore[arg-type]"],
            [(3, "`# type: ignore[arg-type]`", False)],
        ),
        (  # another directive is not a reason, and neither is a rule
            ["# fmt: off", "x = f()  # type: ignore[arg-type]  # noqa: E501"],
            [(2, "`# type: ignore[arg-type]`", False)],
        ),
        (
            ["# ----", "x = f()  # type: ignore[arg-type]  #"],
            [(2, "`# type: ignore[arg-type]`", False)],
        ),
        (["x = f()  # type: ignore[]  # the stub"], [(1, "`# type: ignore[]`", False)]),
        (
            ["x = f()  # pyright: ignore[reportCallIssue]  # the stub is wrong"],
            [(1, "`# pyright: ignore[reportCallIssue]`", True)],
        ),
        (["x = f()  # pyright: ignore"], [(1, "`# pyright: ignore`", False)]),
        (  # pyright reads a directive after any `#` of the comment
            ["x = f()  # see the stub  # type: ignore[arg-type]"],
            [(1, "`# type: ignore[arg-type]`", False)],
        ),
        (["x = f()  # type: ignored, as mypy says"], []),
        (["x = f()  # the type: ignore it"], []),
        (
            ["def f():  # pragma: no cover", "    pass"],
            [(1, "`# pragma: no cover`", False)],
        ),
        (
            [
                "# Only runs on Windows, and CI has none.",
                "def f():  # pragma: no cover",
                "    pass",
            ],
            [(2, "`# pragma: no cover`", True)],
        ),
        (  # a reason beside it is not above it: one place to look
            ["def f():  # pragma: no cover  (Windows only)", "    pass"],
            [(1, "`# pragma: no cover`", False)],
        ),
        (
            ["if DEBUG:  # PRAGMA NO COVER", "    pass"],
            [(1, "`# pragma: no cover`", False)],
        ),
        (
            [
                "# The fallback for Python 3.10.",
                "def f():  # type: ignore[misc]  # pragma: no cover",
                "    pass",
            ],
            [(2, "`# type: ignore[misc]`", True), (2, "`# pragma: no cover`", True)],
        ),
        (  # a run of directives on lines of their own shares the comment above it
            [
                "# The generated table: one line each.",
                "# fmt: off",
                "# pragma: no cover",
                "T = [1]",
            ],
            [(3, "`# pragma: no cover`", True)],
        ),
    ],
)
def test_a_python_comment_silencer_needs_its_reason(tmp_path, lines, expected):
    assert in_python(tmp_path, "\n".join(lines) + "\n") == expected


def test_a_type_ignore_says_what_it_lacks(tmp_path):
    source = (
        "a = f()  # type: ignore\n"
        "b = f()  # type: ignore[arg-type]\n"
        "c = f()  # type: ignore  # the stub is wrong\n"
        "d = f()  # pyright: ignore\n"
    )
    write(tmp_path, {"a.py": source})
    fix = "; or fix the type error"
    assert [lacks for _, _, lacks in silencers.python_silencers(tmp_path / "a.py")] == [
        f"{NO_CODE} and {NO_WHY}{fix}",
        f"{NO_WHY}{fix}",
        f"{NO_CODE}{fix}",
        f"{NO_CODE.replace('type', 'pyright', 1)} and {NO_WHY}{fix}",
    ]


def test_the_words_in_a_string_or_a_docstring_are_not_silencers(tmp_path):
    source = '''"""How to hide a finding: `# type: ignore`, or

    @pytest.mark.skip
    def test_it():  # pragma: no cover
    """

import pytest

HELP = "write # type: ignore[code], never a bare # type: ignore"
MARK = "@pytest.mark.skip"
CALL = 'pytest.skip()'
BLOCK = """
x = f()  # type: ignore
# pragma: no cover
pytest.mark.xfail
"""


def test_it():
    """pytest.skip() is not called here.  # pragma: no cover"""
    assert pytest.__name__ in MARK
'''
    assert in_python(tmp_path, source) == []


def test_a_file_that_does_not_parse_is_left_to_ruff(tmp_path):
    assert in_python(tmp_path, "def f(:  # type: ignore\n") == []
    assert in_python(tmp_path, "x = (1,  # type: ignore\n") == []


# shell: a shellcheck directive, in a script and in a workflow


@pytest.mark.parametrize(
    "lines, expected",
    [
        (
            ["#!/bin/sh", "# shellcheck disable=SC2086", "echo $1"],
            [(2, "SC2086", False)],
        ),
        (
            [
                "#!/bin/sh",
                "# The caller passes several words.",
                "# shellcheck disable=SC2086",
                "echo $1",
            ],
            [(3, "SC2086", True)],
        ),
        (
            ["  #shellcheck   disable=SC2086,SC2046", "echo $1"],
            [(1, "SC2086,SC2046", False)],
        ),
        (  # a run of directives shares the comment above the first
            [
                "# The file is made at build time.",
                "# shellcheck source=/dev/null",
                "# shellcheck disable=SC1091",
                "# shellcheck disable=SC2086",
                ". ./env",
            ],
            [(3, "SC1091", True), (4, "SC2086", True)],
        ),
        (
            ["# shellcheck source=/dev/null disable=SC1091", ". ./env"],
            [(1, "SC1091", False)],
        ),
        (["#", "# shellcheck disable=SC2086", "echo $1"], [(2, "SC2086", False)]),
        (
            ["# Why.", "", "# shellcheck disable=SC2086", "echo $1"],
            [(3, "SC2086", False)],
        ),
        # shellcheck reads no directive after a command, and none in these
        (["echo $1  # shellcheck disable=SC2086"], []),
        (
            [
                "# shellcheck source=/dev/null",
                "# shellcheck shell=bash",
                "# see shellcheck disable=SC1",
            ],
            [],
        ),
    ],
)
def test_a_shellcheck_directive_needs_a_comment_above(lines, expected):
    found = silencers.shell_silencers("\n".join(lines) + "\n")
    assert brief(found) == [
        (line, f"`# shellcheck disable={codes}`", carries)
        for line, codes, carries in expected
    ]


def test_a_workflows_run_scripts_are_read_too(tmp_path):
    """actionlint runs shellcheck on each `run:` script, so a directive works there."""
    step = (
        "      - run: |\n          # shellcheck disable=SC2046\n          echo $(ls)\n"
    )
    files = {
        ".github/workflows/ci.yml": "jobs:\n  a:\n    steps:\n" + step,
        ".github/workflows/release.yaml": step.replace(
            "- run", "# Each name is one argument.\n      - run"
        ),
        ".github/dependabot.yml": "# shellcheck disable=SC2046\n",
        "docs/ci.yml": "# shellcheck disable=SC2046\n",
        "run.sh": UNQUOTED,
        "run.bash": UNQUOTED,
    }
    write(tmp_path, files)
    assert [
        (rel, line) for rel, line, _, _ in silencers.scan(tmp_path, sorted(files))
    ] == [
        (".github/workflows/ci.yml", 5),
        (".github/workflows/release.yaml", 3),
        ("run.sh", 2),
    ]


# JavaScript and TypeScript tests: .skip and .only


def test_a_skip_in_a_test_file_needs_a_comment_above_and_only_is_a_problem():
    source = "\n".join(
        [
            "describe.skip('the old API', () => {});",  # 1
            "// The service is down until the move.",  # 2
            "it.skip('reads', () => {});",  # 3
            "/*",  # 4
            " * Flaky on the runner: issue 12.",  # 5
            " */",  # 6
            "test.skip('writes', () => {});",  # 7
            "it.only('one', () => {});",  # 8
            "// Only this one matters today.",  # 9
            "describe.only('a', () => {});",  # 10: a reason does not allow it
            "it.concurrent.skip('b', () => {});",  # 11
            "test.skip.each([1, 2])('c %i', () => {});",  # 12
            "const maybe = process.env.API ? it : it.skip;",  # 13
            "test.describe.only('playwright', () => {});",  # 14
            "foo(); // The line above is code.",  # 15
            "it.skip('d', () => {});",  # 16
            "// eslint-disable-next-line no-console",  # 17: not a reason
            "it.skip('e', () => {});",  # 18
            "",
        ]
    )
    assert brief(silencers.js_silencers(source)) == [
        (1, "`describe.skip`", False),
        (3, "`it.skip`", True),
        (7, "`test.skip`", True),
        (8, "`it.only`", False),
        (10, "`describe.only`", False),
        (11, "`it.concurrent.skip`", False),
        (12, "`test.skip`", False),
        (13, "`it.skip`", False),
        (14, "`test.describe.only`", False),
        (16, "`it.skip`", False),
        (18, "`it.skip`", False),
    ]
    lacks = {label: why for _, label, why in silencers.js_silencers(source) if why}
    assert lacks["`it.only`"] == silencers.ONLY
    assert (
        lacks["`describe.skip`"] == f"{ABOVE}, or remove the `.skip` and fix the test"
    )


def test_what_is_not_a_skip_or_an_only():
    source = "\n".join(
        [
            "it.skipIf(isWindows)('a', () => {});",  # a condition, as vitest has it
            "it('skips nothing', () => {});",
            "const a = 'it.only';",
            'const b = "describe.skip(";',
            "const c = `",
            "test.only in a template`;",
            "// it.skip('commented out', () => {});",
            "/* describe.only */",
            "page.it.skip();",
            "submit.only();",
            "audit.skip();",
            "const options = { skip: 1, only: 2 };",
            "",
        ]
    )
    assert silencers.js_silencers(source) == []


@pytest.mark.parametrize(
    "rel, is_test",
    [
        ("src/a.test.ts", True),
        ("src/a.spec.tsx", True),
        ("test/a.js", True),
        ("web/tests/unit/a.mjs", True),
        ("src/__tests__/a.cjs", True),
        ("src/a.ts", False),
        ("src/contest/a.js", False),
        ("tests/a.py", False),
        ("tests/data.json", False),
    ],
)
def test_only_a_javascript_or_typescript_test_file_is_read_for_them(
    tmp_path, rel, is_test
):
    write(tmp_path, {rel: "it.only('x', () => {});\n"})
    assert silencers.is_js_test(rel) is is_test
    assert len(silencers.scan(tmp_path, [rel])) == (1 if is_test else 0)


# the run: problem lines, the count, the two modes


PYTHON_TEST = """import pytest


@pytest.mark.skip
def test_one():
    pass


@pytest.mark.xfail(reason="the parser bug, issue 12")
def test_two():
    pytest.skip()
"""
PYTHON_SOURCE = """def size(value):
    return len(value)  # type: ignore


# The table is generated: nothing to test.
def table():  # pragma: no cover
    return size("ab")  # type: ignore[arg-type]  # the stub takes bytes only


def other():  # pragma: no cover
    return 1
"""
EVERY_KIND = {
    "tests/test_a.py": PYTHON_TEST,
    "pkg/a.py": PYTHON_SOURCE,
    "run.sh": UNQUOTED,
    "ok.sh": QUOTED_WHY + "# shellcheck disable=SC2086\necho $1\n",
    ".github/workflows/ci.yml": f"      - run: |\n{' ' * 10}{DIRECTIVE}\n",
    "web/a.test.ts": f"it.only('a', () => {{}});\n{SKIPPED}",
}


@needs_ruff
def test_each_silencer_without_its_reason_is_a_problem_line_with_its_fix(
    tmp_path, capsys
):
    root = repo(tmp_path, EVERY_KIND)
    assert run(root, "--all", "--silencers") == 1
    out = capsys.readouterr().out.splitlines()
    assert out[:-1] == [
        f"silencer: .github/workflows/ci.yml:2: `{DIRECTIVE}` {ABOVE}, {SHELL_FIX}",
        f"silencer: pkg/a.py:2: `# type: ignore` {NO_CODE} and {NO_WHY}; "
        "or fix the type error",
        f"silencer: pkg/a.py:10: `# pragma: no cover` {ABOVE}, or test the code",
        f"silencer: run.sh:2: `# shellcheck disable=SC2086` {ABOVE}, {SHELL_FIX}",
        f"silencer: tests/test_a.py:4: `pytest.mark.skip` {silencers.NO_MARK_REASON}",
        f"silencer: tests/test_a.py:11: `pytest.skip()` {silencers.NO_CALL_REASON}",
        f"silencer: web/a.test.ts:1: `it.only` {silencers.ONLY}",
    ]
    # seven without a reason and five with one: every silencer is counted
    assert out[-1] == (
        "code_rules: FAILED, 7 problem(s) (left in the repository: 0 finding(s), "
        "0 file(s) over the size limit, 12 silencer(s))"
    )
    assert {rules.problem_file(line) for line in out[:-1]} == set(EVERY_KIND) - {
        "ok.sh"
    }


@needs_ruff
def test_a_silencer_with_its_reason_passes_and_is_counted(tmp_path, capsys):
    files = {
        "tests/test_a.py": PYTHON_TEST.replace(
            "skip\n", 'skip(reason="moved")\n'
        ).replace("skip()", 'skip("no network")'),
        "ok.sh": EVERY_KIND["ok.sh"],
        "web/a.test.ts": SKIPPED,
    }
    root = repo(tmp_path, files)
    assert run(root, "--all", "--silencers") == 0
    assert capsys.readouterr().out == f"{QUIET}, 5 silencer(s))\n"


@needs_ruff
def test_without_the_flag_the_run_is_what_it_was(tmp_path, capsys):
    """Byte for byte: no problem, no count, in either mode."""
    root = repo(tmp_path, EVERY_KIND)
    assert run(root, "--all") == 0
    assert capsys.readouterr().out == f"{QUIET})\n"
    write(root, {"run.sh": UNQUOTED + "echo $2\n"})
    commit(root, "touch a file with a silencer")
    assert run(root) == 0
    assert (
        capsys.readouterr().out == f"code_rules: 1 changed file(s) checked\n{QUIET})\n"
    )
    assert rules.measure(root, rules.parse_args(["--all"]))["silencers"] is None


def test_in_mode_changed_only_a_changed_file_fails_and_every_silencer_is_counted(
    tmp_path, capsys
):
    """No Python file, so ruff is not asked: the check needs no base of its own."""
    root = repo(tmp_path, {"old.sh": UNQUOTED, "new.sh": "#!/bin/sh\necho hello\n"})
    write(root, {"new.sh": UNQUOTED, "web/a.test.js": "test.only('a', () => {});\n"})
    commit(root, "two files with a silencer")
    assert run(root, "--silencers") == 1
    out = capsys.readouterr().out
    assert "silencer: new.sh:2: " in out and "silencer: web/a.test.js:1: " in out
    assert "old.sh" not in out
    assert out.splitlines()[-1] == (
        "code_rules: FAILED, 2 problem(s) (left in the repository: 0 finding(s), "
        "0 file(s) over the size limit, 3 silencer(s))"
    )
    assert run(root, "--all", "--silencers") == 1
    assert "silencer: old.sh:2: " in capsys.readouterr().out


def test_vendored_code_and_files_outside_paths_are_not_read(tmp_path, capsys):
    files = {
        "src/run.sh": UNQUOTED,
        "tools/run.sh": UNQUOTED,
        "web/vendor/run.sh": UNQUOTED,
        "node_modules/x/a.test.js": "it.only('a', () => {});\n",
    }
    root = repo(tmp_path, files)
    assert (
        run(root, "--all", "--silencers", "--paths", "src", "web", "node_modules") == 1
    )
    out = capsys.readouterr().out
    assert out.count("silencer: ") == 1 and "silencer: src/run.sh:2: " in out
    assert out.endswith(", 1 silencer(s))\n")


def test_an_eslint_disable_comment_is_counted_and_stays_the_other_flags_problem(
    tmp_path, capsys
):
    """Counted here; a problem only under --no-inline-config, and only once."""
    files = {
        "src/a.ts": "// eslint-disable-next-line no-console\nconsole.log(1);\n",
        "src/a.test.ts": SKIPPED,
        ".gitignore": "node_modules/\n",
    }
    root = repo(tmp_path, files)
    assert run(root, "--all", "--silencers") == 0
    assert capsys.readouterr().out == f"{QUIET}, 2 silencer(s))\n"
    exe = root / "node_modules" / ".bin" / "eslint"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\necho '[]'\n")
    exe.chmod(0o755)
    assert run(root, "--all", "--silencers", "--eslint", "--no-inline-config") == 1
    out = capsys.readouterr().out
    assert out.count("src/a.ts:1") == 1 and "eslint-disable: src/a.ts:1: " in out
    assert "FAILED, 1 problem(s)" in out and out.endswith(", 2 silencer(s))\n")


# this repository, its workflows, its template and its README


def test_this_repository_has_the_flag_on_and_every_silencer_here_has_its_reason():
    ci = (WORKFLOWS / "ci.yml").read_text()
    lint = ci.split("\n  lint:\n", 1)[1].split("\n  lint-changed:\n", 1)[0]
    assert "      silencers: true" in lint
    a = rules.parse_args(["--all", "--silencers"])
    problems, count = silencers.measure(ROOT, set(rules.tracked_files(ROOT)), a)
    assert problems == []
    assert count >= 2  # at least this file's own mark and image.yml's directive


def step(workflow: str) -> str:
    """The shell of a lint workflow's "Code rules" step, as the workflow holds it."""
    text = (WORKFLOWS / workflow).read_text()
    block = text.split("- name: Code rules", 1)[1].split("run: |\n", 1)[1]
    lines = []
    for line in block.splitlines():
        if line.strip() and not line.startswith(" " * 10):
            break
        lines.append(line[10:])
    return "\n".join(lines) + "\n"


@pytest.mark.parametrize("workflow", ["python-lint.yml", "node-lint.yml"])
def test_both_lint_workflows_take_the_input_and_it_is_off_by_default(workflow):
    text = (WORKFLOWS / workflow).read_text()
    assert "\n      silencers: {type: boolean, default: false, description:" in text
    assert "          SILENCERS: ${{ inputs.silencers }}\n" in text
    assert '[ "$SILENCERS" != true ] || args+=(--silencers)\n' in step(workflow)


@pytest.mark.parametrize("workflow", ["python-lint.yml", "node-lint.yml"])
@pytest.mark.parametrize(
    "given, fails", [("true", True), ("false", False), ("", False)]
)
def test_the_step_passes_the_flag_to_the_tool(tmp_path, workflow, given, fails):
    """The step's own shell. No Python file, so no ruff; ESLint is a script: no Node."""
    root = repo(
        tmp_path / "project", {"run.sh": UNQUOTED, "a.test.ts": "it.only('a');\n"}
    )
    eslint = root / "node_modules" / ".bin" / "eslint"
    eslint.parent.mkdir(parents=True)
    eslint.write_text("#!/bin/sh\necho '[]'\n")
    eslint.chmod(0o755)
    runner_temp = tmp_path / "runner-temp"
    (runner_temp / "standards").mkdir(parents=True)
    (runner_temp / "standards" / "tools").symlink_to(TOOLS)
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "CI": "true",
        "RUNNER_TEMP": str(runner_temp),
        "MODE": "all",
        "FORMAT_CHECK": "false",
        "FORMAT_ONLY": "false",
        "NO_INLINE_CONFIG": "false",
        "SILENCERS": given,
        "PATHS": "",
        "EVENT": "schedule",
        "PR_BASE": "",
        "BEFORE": "",
    }
    res = subprocess.run(
        ["bash", "-e", "-c", step(workflow)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == (1 if fails else 0), res.stdout + res.stderr
    assert ("silencer: run.sh:2: " in res.stdout) is fails
    assert ("silencer: a.test.ts:1: `it.only`" in res.stdout) is fails
    assert (", 2 silencer(s))" in res.stdout) is fails


def test_the_template_and_the_readme_carry_the_flag():
    template = (ROOT / "templates" / "ci.yml").read_text()
    assert "\n      # silencers: true  # " in template  # a commented line: opt-in
    assert "\n  #     # silencers: true " in template  # and in the node job
    readme = (ROOT / "README.md").read_text()
    assert "- **Every silencer carries its reason.**" in readme
    assert "`--silencers`" in readme and "`silencers: true`" in readme
    assert "a removed test or a weakened assertion" in readme
