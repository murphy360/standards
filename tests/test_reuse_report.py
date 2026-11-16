"""The reuse report: ``tools/reuse_report.py`` on scratch projects.

Each project is a git checkout, so that the report reads the tracked files and a
base commit as it does on a pull request. It informs: every run here exits 0.
"""

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
reuse = importlib.import_module("reuse_report")

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}
# Eight lines that count, with a docstring, a comment and an import that do not.
BLOCK = '''\
def total(items):
    """The sum, said differently in each copy."""
    import math  # an import does not count

    out = 0
    for item in items:  # nor does a comment
        out += item.price
        out += item.tax
    out = math.floor(out)
    print(out)
    return out
'''


def git(root: Path, *args: str) -> str:
    env = {**os.environ, **GIT_ENV}
    res = subprocess.run(
        ["git", *args], cwd=root, env=env, check=True, capture_output=True, text=True
    )
    return res.stdout.strip()


def commit(root: Path, files: dict[str, str]) -> str:
    """Write ``files``, commit them, and return the commit."""
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    if not (root / ".git").exists():
        git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "a change")
    return git(root, "rev-parse", "HEAD")


def run(root: Path, monkeypatch, capsys, *argv: str) -> list[str]:
    monkeypatch.chdir(root)
    assert reuse.main(list(argv)) == 0  # it never fails
    return capsys.readouterr().out.splitlines()


def variant(text: str) -> str:
    """The same code with another docstring and other comments."""
    return text.replace("said differently in each copy", "another doc").replace(
        "# nor does a comment", "# a comment of its own"
    )


# --- the lists ---


def test_a_block_written_twice_is_found_whatever_its_comments(
    tmp_path, monkeypatch, capsys
):
    commit(tmp_path, {"a.py": BLOCK, "pkg/b.py": "X = 1\n\n\n" + variant(BLOCK)})
    out = run(tmp_path, monkeypatch, capsys)
    assert out[0] == (
        "reuse: 2 files read: 1 names defined in more than one file, 1 duplicated "
        "blocks of 8 lines or more"
    )
    assert out[1] == "  name total: a.py:1, pkg/b.py:4"
    assert out[2] == "  block of 8 lines: a.py:1-11, pkg/b.py:4-14"
    assert out[-1] == "reuse: informs, never fails"


def test_seven_lines_alike_are_no_block_and_main_is_no_name(
    tmp_path, monkeypatch, capsys
):
    short = BLOCK.replace("    print(out)\n", "")
    files = {
        "a.py": short.replace("total", "main"),
        "b.py": short.replace("total", "main"),
        "c.py": "def __init__():\n    pass\n",
        "d.py": "def __init__():\n    pass\n",
    }
    commit(tmp_path, files)
    out = run(tmp_path, monkeypatch, capsys)
    assert "0 names defined in more than one file, 0 duplicated blocks" in out[0]


def test_tests_untracked_files_and_files_outside_paths_are_not_read(
    tmp_path, monkeypatch, capsys
):
    files = {
        "src/a.py": BLOCK,
        "tests/test_a.py": BLOCK,
        "src/test_b.py": BLOCK,
        "conftest.py": BLOCK,
        "tools/c.py": BLOCK,
        "src/broken.py": "def (:\n",  # read as text, though it does not parse
    }
    commit(tmp_path, files)
    (tmp_path / "src" / "untracked.py").write_text(BLOCK)
    out = run(tmp_path, monkeypatch, capsys, "--paths", "src")
    assert out[0].startswith("reuse: 2 files read: 0 names")
    out = run(tmp_path, monkeypatch, capsys)
    assert out[0].startswith("reuse: 3 files read: 1 names")
    assert out[1] == "  name total: src/a.py:1, tools/c.py:1"


# --- a pull request: what it added, against its base ---


def test_the_summary_says_what_the_change_added(tmp_path, monkeypatch, capsys):
    base = commit(tmp_path, {"a.py": BLOCK, "b.py": BLOCK, "c.py": "Y = 2\n"})
    commit(tmp_path, {"c.py": BLOCK, "d.py": "def other():\n    pass\n"})
    summary = tmp_path / "summary.md"
    out = run(tmp_path, monkeypatch, capsys, "--base", base, "--summary", str(summary))
    assert out[-4:] == [
        f"reuse: Compared with the base, {base[:12]}. This change adds:",
        "  name total: c.py:1 (already in 2 files)",
        "  block of 8 lines: a.py:1-11, b.py:1-11, c.py:1-11",
        "reuse: informs, never fails",
    ]
    assert summary.read_text().splitlines() == [
        "### reuse",
        "",
        "4 files read: 1 names defined in more than one file, 1 duplicated blocks of "
        "8 lines or more. The report informs and never fails.",
        "",
        f"Compared with the base, {base[:12]}.",
        "",
        "What this pull request adds:",
        "",
        "- name total: c.py:1 (already in 2 files)",
        "- block of 8 lines: a.py:1-11, b.py:1-11, c.py:1-11",
    ]


def test_a_change_that_adds_nothing_says_so(tmp_path, monkeypatch, capsys):
    base = commit(tmp_path, {"a.py": BLOCK, "b.py": BLOCK})
    commit(tmp_path, {"a.py": "Z = 0\n\n" + BLOCK})  # moved, not copied
    summary = tmp_path / "summary.md"
    out = run(tmp_path, monkeypatch, capsys, "--base", base, "--summary", str(summary))
    assert out[-2] == (
        f"reuse: Compared with the base, {base[:12]}. This change adds nothing to "
        "either list."
    )
    assert summary.read_text().splitlines()[-1] == (
        "This pull request adds nothing to either list."
    )


def test_the_base_of_a_working_directory_is_read_there(tmp_path, monkeypatch, capsys):
    base = commit(tmp_path, {"api/a.py": BLOCK, "web/b.py": BLOCK, "api/c.py": ""})
    commit(tmp_path, {"api/c.py": BLOCK})
    out = run(tmp_path / "api", monkeypatch, capsys, "--base", base)
    assert out[0].startswith("reuse: 2 files read: 1 names")
    # New in full: in api/ the base defined it once. Read from the root, the base
    # would have had it twice (with web/b.py), and this would say "already in".
    assert out[-3] == "  name total: a.py:1, c.py:1"


def test_a_base_git_cannot_read_is_said_and_the_head_is_reported(
    tmp_path, monkeypatch, capsys
):
    commit(tmp_path, {"a.py": BLOCK})
    summary = tmp_path / "summary.md"
    out = run(
        tmp_path, monkeypatch, capsys, "--base", "0" * 40, "--summary", str(summary)
    )
    assert out[-2].startswith(f"reuse: The base {'0' * 12} could not be read (")
    assert "nothing to compare" in summary.read_text()


def test_many_additions_are_cut_short_in_the_summary():
    names = [{"name": f"n{i}", "where": [f"a{i}.py:1"]} for i in range(60)]
    head = {"files": 2, "names": names, "blocks": []}
    text = reuse.summary(head, {"names": names, "blocks": []}, "")
    assert text.count("\n- name ") == reuse.MOST_LISTED
    assert text.endswith("- and 10 more (the log lists them)\n")


def test_a_fault_of_its_own_is_said_and_never_fails(tmp_path, monkeypatch, capsys):
    commit(tmp_path, {"a.py": BLOCK})

    def broken(files):
        raise RuntimeError("a bug")

    monkeypatch.setattr(reuse, "build", broken)
    out = run(tmp_path, monkeypatch, capsys)
    assert out == ["reuse: the report did not run (RuntimeError: a bug)"]


@pytest.mark.parametrize(
    ("data", "names", "expected"),
    [
        (b"abc blob 3\nx=1\n", ["a.py"], {"a.py": "x=1"}),
        (b"r:a.py missing\nabc blob 2\nok\n", ["a.py", "b.py"], {"b.py": "ok"}),
        (b"abc blob 1\n\xff\n", ["a.py"], {}),  # not text
    ],
)
def test_the_batch_answer_of_git_is_read_object_by_object(data, names, expected):
    assert reuse.parse_batch(data, names) == expected
