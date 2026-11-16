"""The glossary: ``tools/glossary.py`` on scratch projects.

Each project is a git checkout with its files added, so that ``--undefined`` reads
the tracked files as it does in CI. No network.
"""

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
glossary = importlib.import_module("glossary")

GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
TERMS = """\
- term: Waypoint
  definition: A point the vehicle steers to.
  synonyms: [WP, mark]
  see: [Route]
- term: Route
  definition: >
    The waypoints of one mission,
    in order.
  synonyms:
    operators: [track]
  see: [Waypoint]
"""
PAGE = """\
<!-- Made from terms.yaml beside this file by tools/glossary.py of murphy360/standards. Do not edit it:
     change terms.yaml, then run `python3 <standards>/tools/glossary.py --write`. -->

# Glossary

The terms this project uses, A to Z. Each is defined once, in `terms.yaml` beside this page.

## Route

The waypoints of one mission, in order.

Also called: track (operators).

See also: [Waypoint](#waypoint).

## Waypoint

A point the vehicle steers to.

Also called: WP, mark.

See also: [Route](#route).

## Other names

Each other name, and the term it means.

- mark: [Waypoint](#waypoint)
- track (operators): [Route](#route)
- WP: [Waypoint](#waypoint)
"""


def project(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "project"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    env = {**os.environ, **GIT_ENV}
    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", *args], cwd=root, env=env, check=True)
    return root


def run(root: Path, monkeypatch, capsys, *argv: str) -> tuple[int, list[str]]:
    monkeypatch.chdir(root)
    code = glossary.main(list(argv))
    return code, capsys.readouterr().out.splitlines()


# --- the page: made from the terms, and checked against them ---


def test_no_terms_file_passes_and_says_so(tmp_path, monkeypatch, capsys):
    code, out = run(project(tmp_path, {"README.md": "# x\n"}), monkeypatch, capsys)
    assert code == 0
    assert out == [
        "glossary: no docs/terms.yaml here, so there is no glossary to check"
    ]


def test_write_makes_the_page_and_the_check_then_passes(tmp_path, monkeypatch, capsys):
    root = project(tmp_path, {"docs/terms.yaml": TERMS})
    code, out = run(root, monkeypatch, capsys, "--write")
    assert (code, out) == (0, ["glossary: wrote docs/GLOSSARY.md (2 terms)"])
    assert (root / "docs" / "GLOSSARY.md").read_text() == PAGE
    code, out = run(root, monkeypatch, capsys)
    assert (code, out) == (
        0,
        ["glossary: OK, docs/GLOSSARY.md is up to date (2 terms)"],
    )
    code, out = run(root, monkeypatch, capsys, "--write")
    assert out == ["glossary: docs/GLOSSARY.md is up to date (2 terms)"]


def test_a_missing_page_fails_with_the_fix(tmp_path, monkeypatch, capsys):
    code, out = run(project(tmp_path, {"docs/terms.yaml": TERMS}), monkeypatch, capsys)
    assert code == 1
    assert out[0] == "docs/GLOSSARY.md is missing."
    assert out[-1].startswith(
        "glossary: FAILED, docs/GLOSSARY.md is not what the terms"
    )
    assert "`python3 <standards>/tools/glossary.py --write`" in out[-1]


def test_a_stale_page_fails_and_shows_the_difference(tmp_path, monkeypatch, capsys):
    stale = PAGE.replace("A point the vehicle steers to.", "A point.")
    files = {"docs/terms.yaml": TERMS, "docs/GLOSSARY.md": stale}
    code, out = run(project(tmp_path, files), monkeypatch, capsys)
    assert code == 1
    assert "-A point." in out and "+A point the vehicle steers to." in out
    assert out[-1].startswith("glossary: FAILED")


def test_trailing_whitespace_and_line_endings_do_not_count(
    tmp_path, monkeypatch, capsys
):
    page = PAGE.replace("\n", "  \r\n") + "\r\n"
    files = {"docs/terms.yaml": TERMS, "docs/GLOSSARY.md": page}
    code, _ = run(project(tmp_path, files), monkeypatch, capsys)
    assert code == 0


def test_another_terms_file_names_itself_in_the_fix(tmp_path, monkeypatch, capsys):
    root = project(tmp_path, {"api/docs/terms.yaml": TERMS})
    code, out = run(root, monkeypatch, capsys, "--terms", "api/docs/terms.yaml")
    assert code == 1
    assert "--write --terms api/docs/terms.yaml`" in out[-1]
    run(root, monkeypatch, capsys, "--terms", "api/docs/terms.yaml", "--write")
    assert (root / "api" / "docs" / "GLOSSARY.md").read_text() == PAGE


def test_anchors_are_githubs_and_names_are_escaped():
    terms = """\
- term: Glossary
  definition: This page.
- term: C2 link
  definition: The radio link.
- term: C2-link
  definition: A second spelling.
- term: a_b <c>
  definition: Odd characters.
  see: [C2 link]
"""
    entries, owner, problems = glossary.read_terms(glossary.load(terms))
    assert problems == []
    page = glossary.render(entries, owner)
    assert "## a\\_b \\<c\\>\n" in page
    assert "## Glossary\n" in page and "[C2 link](#c2-link)" in page
    anchors = glossary.Anchors()
    assert [
        anchors.add(t) for t in ("Glossary", "Glossary", "glossary-1", "C++ & C#")
    ] == [
        "glossary",
        "glossary-1",
        "glossary-1-1",
        "c--c",
    ]


# --- the terms file: every problem, each with its line ---


def problems_of(text: str) -> list[tuple[int, str]]:
    return glossary.read_terms(glossary.load(text))[2]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "- term: A\n  definition: x\n- term: a\n  definition: y\n",
            [(3, "`a` is defined twice: a term here, and a term on line 1")],
        ),
        (
            "- term: A\n  definition: x\n  synonyms: [Bee]\n"
            "- term: B\n  definition: y\n  synonyms:\n    navy: [bee]\n",
            [
                (
                    4,
                    "`bee` is defined twice: a synonym of `B` here, and a synonym of `A` on line 1",
                )
            ],
        ),
        (
            "- term: A\n  definition: x\n  synonyms: [a]\n",
            [(1, "`a` is a synonym of itself")],
        ),
        (
            "- term: A\n  definition: x\n  see: [B]\n",
            [(1, "`A` sees `B`: no such term")],
        ),
        (
            "- term: A\n  definition: x\n  synonyms: [Ay]\n"
            "- term: B\n  definition: y\n  see: [Ay, B]\n",
            [
                (4, "`B` sees `Ay`, a synonym: name its term, `A`"),
                (4, "`B` sees itself"),
            ],
        ),
        (
            "- term: A\n  definition: x\n  used: everywhere\n",
            [(1, "`used` is not a key of a term (term, definition, synonyms, see)")],
        ),
        ("- term: A\n", [(1, "`A` has no `definition`")]),
        ("- definition: x\n", [(1, "`term` is a name on one line")]),
        (
            "- term: '!!'\n  definition: x\n",
            [(1, "`!!`: a term holds a letter or a digit")],
        ),
        (
            "- term: A\n  definition: x\n  synonyms: Ay\n",
            [(1, "synonyms is a list of names: `[a, b]`")],
        ),
        (
            "- term: A\n  definition: x\n  synonyms:\n    navy: lane\n",
            [(1, "synonyms for navy is a list of names: `[a, b]`")],
        ),
        (
            "- just a name\n",
            [(1, "a term is `term:` and `definition:`, on lines of their own")],
        ),
        (
            "term: A\n",
            [
                (
                    1,
                    "the file is a list of terms, each `- term: ...` with its `definition: ...`",
                )
            ],
        ),
    ],
)
def test_each_problem_is_named_with_its_line(text, expected):
    assert problems_of(text) == expected


def test_problems_fail_the_run_and_nothing_is_written(tmp_path, monkeypatch, capsys):
    terms = TERMS + "- term: route\n  definition: Again.\n"
    root = project(tmp_path, {"docs/terms.yaml": terms})
    code, out = run(root, monkeypatch, capsys, "--write")
    assert code == 1
    assert out == [
        "docs/terms.yaml:12: `route` is defined twice: a term here, and a term on line 5",
        "glossary: FAILED, 1 problem(s) in docs/terms.yaml",
    ]
    assert not (root / "docs" / "GLOSSARY.md").exists()


def test_a_file_it_cannot_read_fails_with_its_line(tmp_path, monkeypatch, capsys):
    root = project(tmp_path, {"docs/terms.yaml": "- term: A\n  definition: a: b\n"})
    code, out = run(root, monkeypatch, capsys)
    assert code == 2
    assert out[0] == "docs/terms.yaml:2: `a: b`: put a value that holds `: ` in quotes"
    assert out[1].startswith("glossary: FAILED, docs/terms.yaml cannot be read")


# --- undefined terms: a report, and a failure only with --strict ---


@pytest.mark.parametrize(
    ("bold", "term"),
    [
        ("Waypoint", "Waypoint"),
        ("A contract", "contract"),
        ("The Survey Box", "Survey Box"),
        ("C2", "C2"),
        ("Note:", None),  # a lead-in
        ("Every check runs.", None),
        ("never", None),  # emphasis
        ("names defined twice", None),
        ("One Two Three Four Five", None),  # a phrase
        ("[Route](#route)", None),  # a link
    ],
)
def test_what_bold_text_counts_as_a_term(bold, term):
    assert glossary.candidate(bold) == term


def test_headings_code_blocks_and_code_spans_are_skipped():
    text = (
        "# A **Heading**\n"
        "A **Waypoint** and `**Span**`.\n"
        "```md\n**Fenced**\n```\n"
        "~~~~\n**Tilde**\n```\n**Still fenced**\n~~~~\n"
        "**Route** again\n"
    )
    assert glossary.bold_terms(text) == [(2, "Waypoint"), (11, "Route")]


def test_undefined_terms_are_listed_and_fail_only_when_strict(
    tmp_path, monkeypatch, capsys
):
    readme = (
        "# Readme\n\n"
        "A **Waypoint**, two **Waypoints**, a **WP**, a **Survey Box**.\n"
        "Then the **Survey Box** again, and **Note:** a lead-in.\n"
    )
    terms = TERMS + "- term: Lawn\n  definition: Grass, and a **Not Read** term.\n"
    page = glossary.render(*glossary.read_terms(glossary.load(terms))[:2])
    files = {
        "docs/terms.yaml": terms,
        "docs/GLOSSARY.md": page,  # the page itself is not read
        "README.md": readme,
        "docs/guide.md": "Fly the **Lawnmower**.\n",
    }
    root = project(tmp_path, files)
    code, out = run(root, monkeypatch, capsys, "--undefined")
    assert code == 0
    assert out[1:] == [
        "Bold terms no term defines, each with the first place it is used:",
        "  Lawnmower: docs/guide.md:1",
        "  Survey Box: README.md:3 (and 1 more)",
        "glossary: 2 bold term(s) that docs/terms.yaml does not define "
        "(a report: --strict fails on them)",
    ]
    code, _ = run(root, monkeypatch, capsys, "--undefined", "--strict")
    assert code == 1


# --- the docs and the fixture CI runs the check on ---


def test_the_readme_example_is_a_valid_terms_file():
    text = (ROOT / "README.md").read_text()
    section = text.split("\n## The glossary\n", 1)[1].split("\n## ", 1)[0]
    example = section.split("```yaml\n", 1)[1].split("```", 1)[0]
    entries, _, problems = glossary.read_terms(glossary.load(example))
    assert problems == [] and [e["term"] for e in entries] == ["Waypoint", "Route"]
    assert "| `tools/glossary.py` |" in text and "| `tools/yaml_subset.py` |" in text


def test_the_fixtures_page_is_current(monkeypatch, capsys):
    monkeypatch.chdir(ROOT / "tests" / "fixtures" / "python-project")
    assert glossary.main([]) == 0, capsys.readouterr().out


def test_the_shared_text_tells_agents_to_define_new_terms():
    shared = (ROOT / "templates" / "AGENTS.md").read_text()
    lines = [x for x in shared.splitlines() if "docs/terms.yaml" in x]
    assert lines == [
        "   where the project has them. Write for the reader, not the developer. "
        "Define new terms in `docs/terms.yaml`."
    ]  # in the documentation of the pull request; the check prints the command
