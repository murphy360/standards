"""One instruction file every agent reads: ``AGENTS.md``, and a ``CLAUDE.md`` that
imports it.

``templates/AGENTS.md`` holds the shared text. ``templates/CLAUDE.md`` is Claude
Code's import of it. ``check_standards.py`` accepts either name, ``fleet.py`` reports
the name a repository has, and ``sync_templates.py --to-agents-md`` moves a project
from the old layout (the text in ``CLAUDE.md``) without losing a byte of its own
section. Every project here is made up, in a scratch directory: no network.
"""

import importlib
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))  # the tools import their sibling modules
standards = importlib.import_module("check_standards")
sync = importlib.import_module("sync_templates")
fleet = importlib.import_module("fleet")

AGENTS_T = (ROOT / "templates" / "AGENTS.md").read_text()
CLAUDE_T = (ROOT / "templates" / "CLAUDE.md").read_text()
_lines = AGENTS_T.splitlines(keepends=True)
_begin, _end = sync.markers(_lines, "templates/AGENTS.md")
BEGIN, END = _lines[_begin], _lines[_end]
SHARED = "".join(_lines[_begin + 1 : _end])
BLOCK = sync.template()["block"]
TITLE = "# Working on demo\n\n"
# trailing spaces, a tab and no final newline: the move must keep every byte
OURS = "\n## This project\n\nBuild with `make`.  \nPorts:\t8080 and 9090.\n\n\nEnd"
UNMARKED = TITLE + "Shared text from long ago.\n\n## Old rules\n\nOne.\n" + OURS
IMPORT = "@AGENTS.md\n\n## Claude only\n\nUse plan mode for the billing code.\n"
DEPENDABOT = (
    "version: 2\nupdates:\n  - package-ecosystem: github-actions\n    directory: /\n"
)
TO_AGENTS = "--to-agents-md"


def instructions(shared: str = SHARED) -> str:
    """A project's instruction file: its own title, the shared text, its section."""
    return TITLE + BEGIN + shared + END + OURS


def old(shared: str = SHARED) -> str:
    """The shared text as a project that fell behind has it: one rule reworded."""
    lines = shared.splitlines(keepends=True)
    rule = next(i for i, line in enumerate(lines) if line.startswith("- "))
    lines[rule] = "- An old rule the template no longer has.\n"
    return "".join(lines)


def project(tmp_path, files: dict) -> Path:
    """A scratch project with Dependabot, the CI template and ``files``."""
    root = tmp_path / "project"
    given = {
        ".github/dependabot.yml": DEPENDABOT,
        ".github/workflows/ci.yml": (ROOT / "templates" / "ci.yml").read_text(),
        **files,
    }
    for rel, text in given.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(text.encode())
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    return root


def text_of(root: Path, name: str) -> str:
    return (root / name).read_bytes().decode()


def current(root: Path) -> list[str]:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    return standards.check(root, require_current_templates=True)


def section(heading: str) -> str:
    return SHARED.split(heading, 1)[1].split("\n## ")[0]


# the two templates


def test_the_template_is_agents_md_and_claude_md_only_imports_it():
    assert sync.template()["name"] == "AGENTS.md"
    assert AGENTS_T.split(BEGIN)[0].strip() == "# Working on <project>"
    assert AGENTS_T.split(END)[1].lstrip().startswith("## This project")
    # Claude Code expands `@path` inside AGENTS.md too: the template imports nothing
    spans = re.sub(r"`[^`]*`", "", AGENTS_T)
    assert re.findall(r"(?:^|\s)@[\w./~-]+", spans) == []
    first, rest = CLAUDE_T.split("\n", 1)
    assert first == "@AGENTS.md"  # the import, as Claude Code's documentation has it
    assert sync.imports_agents(CLAUDE_T) and sync.block(CLAUDE_T, "CLAUDE.md") is None
    # the rest is one HTML comment, which Claude Code strips: nothing only Claude needs
    assert re.fullmatch(r"\s*<!--.*?-->\s*", rest, re.DOTALL)
    assert "@" not in rest


@pytest.mark.parametrize(
    ("text", "imported"),
    [
        ("@AGENTS.md\n", True),
        ("# Claude\n\nSee @AGENTS.md for the rules.\n", True),
        ("@./AGENTS.md", True),
        ("Read AGENTS.md first.\n", False),  # words, not an import
        ("`@AGENTS.md` is the import.\n", False),  # a code span is skipped
        ("```\n@AGENTS.md\n```\n", False),  # and a fenced block
        ("~~~\n@AGENTS.md\n~~~\n@AGENTS.md\n", True),
        ("mail someone@AGENTS.md\n", False),
        ("@AGENTS.mdx\n", False),
        ("@docs/AGENTS.md\n", False),  # another file
        ("", False),
    ],
)
def test_an_import_is_read_the_way_claude_code_reads_it(text, imported):
    assert sync.imports_agents(text) is imported


def test_the_shared_part_is_short():
    """The ticket's target is under 45 lines. The text is: blank lines, which
    Markdown needs around a heading, are not counted, and with them it is longer."""
    written = [line for line in BLOCK if line]
    assert len(written) < 45
    assert len(BLOCK) < 55
    assert max(len(line) for line in BLOCK) <= 118  # the width was not stretched


def test_standards_keeps_the_command_the_link_and_what_no_tool_enforces():
    kept = section("## Standards every project keeps")
    flat = " ".join(kept.split())
    for needle in (
        "Before a push, run `pre-commit run --all-files`. It is the one command",
        "https://github.com/murphy360/standards#the-rules",
        "Times are UTC.",
        "Secrets never go in the repository, a ticket or a log.",
        "A test fixture is made up, never a saved response from a real account",
        "no personal data",
        "health, school or money record",
        "short sentences, one idea per sentence, no em-dashes",
    ):
        assert needle in flat, needle
    assert "\n## The rules\n" in (ROOT / "README.md").read_text()  # the link's target
    # what CI enforces and prints with its fix is not restated
    for gone in ("complexity", "800", "1200", "noqa", "88 columns", "Dependabot"):
        assert gone not in kept, gone
    assert len([line for line in kept.splitlines() if line.strip()]) <= 9


def test_the_other_shared_sections_keep_their_rules():
    for heading in (
        "## How work arrives",
        "## Where and how to work",
        "## Definition of done: one pull request",
    ):
        assert heading in SHARED
    where = " ".join(section("## Where and how to work").split())
    for needle in (
        "Never change the branch of the main checkout",
        "git worktree add -b <type>/<short-name>",
        "never with the owner's login",
        "Never `git stash`",
        "Never `git reset --soft` or `--hard` onto a base that moved on",
        "git diff --stat origin/<base>...HEAD",
        "Tests run in the project's Docker image, not on the host",
        "Never touch the owner's running services",
    ):
        assert needle in where, needle
    done = section("## Definition of done")
    # owner decision 2026-11-14: CI's run is the record, so no pasted test output
    assert "2. A unit test for every new behaviour, green in Docker." in done
    assert (
        "pastes the last lines" not in done and "the tests and their output" not in done
    )
    # this repository is public: no project's name in the shared text
    assert "Proteus" not in done
    flat = " ".join(done.split())
    for needle in (
        "`gh pr create --draft --base main`",
        "mark it ready **once**, with `gh pr ready`",
        "comment the link on the ticket",
        "never enable auto-merge",
    ):
        assert needle in flat, needle
    assert [line[:2] for line in done.splitlines() if line[:1].isdigit()] == [
        f"{n}." for n in range(1, 8)
    ]


def test_the_pull_request_templates_end_at_closes_and_paste_no_test_output():
    ours = (ROOT / ".github" / "pull_request_template.md").read_text()
    template = (ROOT / "templates" / "pull_request_template.md").read_text()
    assert ours.split("\n", 1)[1] == template.split("\n", 1)[1]
    assert "- [ ]" not in template and "---" not in template
    assert template.endswith("\nCloses #\n")
    headings = [line for line in template.splitlines() if line.startswith("## ")]
    assert headings == [
        "## What and why",
        "## Docs touched",
        "## Known issues",
    ]
    assert "test output" not in template


def test_this_repository_has_the_layout_with_its_own_text():
    own = (ROOT / "AGENTS.md").read_text()
    assert own.startswith("# Working on murphy360/standards\n")
    assert sync.block(own, "AGENTS.md") is None  # its own text, not the shared block
    assert (ROOT / "CLAUDE.md").read_bytes() == CLAUDE_T.encode()
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert not re.search(r"^\s*check-templates: true", ci, re.MULTILINE)


# check_standards.py: the base check takes either name


@pytest.mark.parametrize(
    "files",
    [
        {"AGENTS.md": "x"},
        {"AGENTS.md": "x", "CLAUDE.md": "@AGENTS.md\n"},
        {"CLAUDE.md": "x"},  # every adopter before the template moved
        {"AGENTS.md": "x", "CLAUDE.md": "y"},  # no import: only the opt-in check says
    ],
    ids=["agents", "agents+import", "claude", "both"],
)
def test_the_base_check_accepts_either_instruction_file(tmp_path, files, capsys):
    root = project(tmp_path, files)
    assert standards.check(root) == []
    assert standards.main(["--root", str(root)]) == 0
    assert capsys.readouterr().out == "standards: OK\n"


def test_the_base_check_names_agents_md_when_there_is_no_instruction_file(tmp_path):
    (problem,) = standards.check(project(tmp_path, {}))
    assert problem.startswith("AGENTS.md is missing")
    assert "templates/AGENTS.md" in problem and "templates/CLAUDE.md" in problem


# standards-check's last step: the workflow and the checks action run this script.
WORKFLOW_STEP = ROOT / "tools" / "steps" / "standards-check.sh"


@pytest.mark.parametrize("templates", ["false", "true"])
def test_a_project_with_only_agents_md_passes_the_workflow(tmp_path, templates):
    """The workflow's own step, against the real tools: with every check off, as
    an adopter calls it today, and with the template check on."""
    root = project(tmp_path, {"AGENTS.md": instructions()})
    res = subprocess.run(
        ["bash", str(WORKFLOW_STEP)],
        cwd=root,
        env={"PATH": os.environ["PATH"], "CHECK_TEMPLATES": templates},
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout == "standards: OK\n"


# check_standards.py --require-current-templates


@pytest.mark.parametrize(
    "files",
    [
        {"AGENTS.md": instructions(), "CLAUDE.md": CLAUDE_T},  # from the templates
        {"AGENTS.md": instructions()},
        {"AGENTS.md": instructions(), "CLAUDE.md": IMPORT},
        {"CLAUDE.md": instructions()},  # a project that has not moved
    ],
    ids=["templates", "agents", "agents+claude-only-text", "claude"],
)
def test_each_layout_with_the_current_text_passes_the_template_check(tmp_path, files):
    assert current(project(tmp_path, files)) == []


def test_an_old_block_in_agents_md_fails_and_names_it(tmp_path):
    files = {"AGENTS.md": instructions(old()), "CLAUDE.md": CLAUDE_T}
    (problem,) = current(project(tmp_path, files))
    assert problem.startswith(
        "AGENTS.md: the shared text is behind templates/AGENTS.md"
    )


def test_a_claude_md_beside_an_agents_md_must_import_it(tmp_path):
    files = {"AGENTS.md": "Rules for the other tools.\n", "CLAUDE.md": instructions()}
    root = project(tmp_path, files)
    assert standards.check(root) == []  # the base check does not look
    (problem,) = current(root)
    assert problem.startswith("CLAUDE.md does not import AGENTS.md")
    assert "two files in step" in problem and TO_AGENTS in problem
    (root / "CLAUDE.md").write_text(IMPORT)
    (root / "AGENTS.md").write_text(instructions())
    assert current(root) == []


def test_without_markers_the_gap_names_the_file_that_should_hold_them(tmp_path):
    root = project(tmp_path, {"AGENTS.md": UNMARKED, "CLAUDE.md": "@AGENTS.md\n"})
    (problem,) = current(root)
    assert problem.startswith("AGENTS.md: no `standards:shared` markers")


def test_a_claude_md_linked_to_agents_md_is_one_file(tmp_path, capsys):
    root = project(tmp_path, {"AGENTS.md": instructions(old())})
    (root / "CLAUDE.md").symlink_to("AGENTS.md")
    (problem,) = current(root)  # said once, of AGENTS.md
    assert problem.startswith("AGENTS.md: the shared text is behind")
    assert sync.main([str(root), TO_AGENTS, "--all"]) == 0
    assert (root / "CLAUDE.md").is_symlink()  # never written through
    assert text_of(root, "AGENTS.md") == instructions()
    assert "AGENTS.md: it is the instruction file, nothing to move" in (
        capsys.readouterr().out
    )
    assert current(root) == []


# sync_templates.py: the default moves nothing


def test_the_default_rewrites_the_block_where_it_is_and_moves_nothing(tmp_path, capsys):
    root = project(tmp_path, {"CLAUDE.md": instructions(old())})
    assert sync.main([str(root)]) == 0
    assert text_of(root, "CLAUDE.md") == instructions()
    assert not (root / "AGENTS.md").exists()
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "CLAUDE.md: the shared text was rewritten from templates/AGENTS.md"
    note = out[-2]  # it says how to move, and counts as no change
    assert (
        note.startswith("CLAUDE.md: it holds the instructions.") and TO_AGENTS in note
    )
    assert out[-1] == "sync_templates: 1 change(s) made"
    assert sync.main([str(root), "--check"]) == 0


def test_the_default_rewrites_agents_md_and_leaves_claude_md_alone(tmp_path, capsys):
    root = project(tmp_path, {"AGENTS.md": instructions(old()), "CLAUDE.md": IMPORT})
    assert sync.main([str(root)]) == 0
    assert text_of(root, "AGENTS.md") == instructions()
    assert text_of(root, "CLAUDE.md") == IMPORT
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "AGENTS.md: the shared text was rewritten from templates/AGENTS.md"
    assert out[-1] == "sync_templates: 1 change(s) made" and TO_AGENTS not in "".join(
        out
    )
    assert sync.main([str(root)]) == 0
    assert capsys.readouterr().out == "sync_templates: nothing to change\n"


def test_the_default_says_when_two_files_are_kept_in_step(tmp_path, capsys):
    files = {"AGENTS.md": "Other tools.\n", "CLAUDE.md": instructions(old())}
    root = project(tmp_path, files)
    assert sync.main([str(root)]) == 0
    assert text_of(root, "AGENTS.md") == "Other tools.\n"
    assert text_of(root, "CLAUDE.md") == instructions()
    assert "CLAUDE.md does not import AGENTS.md" in capsys.readouterr().out


# sync_templates.py --to-agents-md, from each starting state


@pytest.mark.parametrize("eol", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_a_claude_md_with_markers_moves_and_keeps_its_own_section(
    tmp_path, capsys, eol
):
    before = instructions(old()).replace("\n", eol)
    root = project(tmp_path, {"CLAUDE.md": before})
    assert sync.main([str(root), TO_AGENTS]) == 0
    after = text_of(root, "AGENTS.md")
    assert after == instructions().replace("\n", eol)
    assert after.endswith((END + OURS).replace("\n", eol))  # "This project", every byte
    assert after.startswith(TITLE.replace("\n", eol))
    assert text_of(root, "CLAUDE.md") == CLAUDE_T
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "AGENTS.md: was written from CLAUDE.md, byte for byte"
    assert out[1].startswith("CLAUDE.md: was replaced by templates/CLAUDE.md")
    assert out[2] == "AGENTS.md: the shared text was rewritten from templates/AGENTS.md"
    assert out[-1] == "sync_templates: 3 change(s) made"
    assert current(root) == []
    assert sync.main([str(root), TO_AGENTS]) == 0  # a second run has nothing to do
    assert capsys.readouterr().out == (
        "CLAUDE.md: it imports AGENTS.md, nothing to move\n"
        "sync_templates: nothing to change\n"
    )
    assert text_of(root, "AGENTS.md") == after


def test_a_current_claude_md_moves_without_a_changed_byte(tmp_path):
    before = instructions()
    root = project(tmp_path, {"CLAUDE.md": before})
    assert sync.main([str(root), TO_AGENTS]) == 0
    assert (root / "AGENTS.md").read_bytes() == before.encode()
    assert text_of(root, "CLAUDE.md") == CLAUDE_T


def test_a_claude_md_without_markers_moves_as_it_is(tmp_path, capsys):
    root = project(tmp_path, {"CLAUDE.md": UNMARKED})
    assert sync.main([str(root), TO_AGENTS]) == 1  # moved; the block is left by hand
    assert (root / "AGENTS.md").read_bytes() == UNMARKED.encode()
    assert text_of(root, "CLAUDE.md") == CLAUDE_T
    out = capsys.readouterr().out
    assert "AGENTS.md: it has no markers, so its text was not changed" in out
    assert out.endswith("2 change(s) made, 1 step(s) left to do by hand\n")
    assert sync.main([str(root), "--shared-until", "## This project"]) == 0
    assert text_of(root, "AGENTS.md") == instructions()
    assert current(root) == []


def test_a_claude_md_without_markers_moves_and_gets_the_block_in_one_run(tmp_path):
    root = project(tmp_path, {"CLAUDE.md": UNMARKED})
    argv = [str(root), TO_AGENTS, "--shared-until", "## This project"]
    assert sync.main(argv) == 0
    after = text_of(root, "AGENTS.md")
    assert after == instructions() and after.endswith(END + OURS)
    assert text_of(root, "CLAUDE.md") == CLAUDE_T
    assert current(root) == []


def test_with_both_files_and_an_import_nothing_moves(tmp_path, capsys):
    root = project(tmp_path, {"AGENTS.md": instructions(old()), "CLAUDE.md": IMPORT})
    assert sync.main([str(root), TO_AGENTS]) == 0
    assert text_of(root, "AGENTS.md") == instructions()  # the block, as always
    assert text_of(root, "CLAUDE.md") == IMPORT  # what only Claude needs is kept
    assert "CLAUDE.md: it imports AGENTS.md, nothing to move" in capsys.readouterr().out


def test_with_both_files_holding_the_same_text_claude_md_becomes_the_import(
    tmp_path, capsys
):
    files = {"AGENTS.md": instructions(old()), "CLAUDE.md": instructions(old())}
    root = project(tmp_path, files)
    assert sync.main([str(root), TO_AGENTS]) == 0
    assert text_of(root, "AGENTS.md") == instructions()
    assert text_of(root, "CLAUDE.md") == CLAUDE_T
    assert "CLAUDE.md: it held the same text as AGENTS.md" in capsys.readouterr().out
    assert current(root) == []


def test_with_both_files_that_differ_nothing_is_merged(tmp_path, capsys):
    theirs = "# Demo for other tools\n\nRun `make test`.\n"
    root = project(tmp_path, {"AGENTS.md": theirs, "CLAUDE.md": instructions(old())})
    assert sync.main([str(root), TO_AGENTS]) == 1
    assert text_of(root, "AGENTS.md") == theirs
    assert text_of(root, "CLAUDE.md") == instructions()  # only its block was rewritten
    out = capsys.readouterr().out
    assert "both hold instructions and differ, so nothing was moved" in out
    assert "never merges two files" in out and "`@AGENTS.md`" in out
    assert out.endswith("1 change(s) made, 1 step(s) left to do by hand\n")


def test_an_agents_md_alone_has_nothing_to_move(tmp_path, capsys):
    root = project(tmp_path, {"AGENTS.md": instructions()})
    assert sync.main([str(root), TO_AGENTS]) == 0
    assert text_of(root, "AGENTS.md") == instructions()
    assert not (root / "CLAUDE.md").exists()  # not asked for: --all adds it
    assert capsys.readouterr().out == (
        "AGENTS.md: it is the instruction file, nothing to move\n"
        "sync_templates: nothing to change\n"
    )
    assert sync.main([str(root)]) == 0  # and the default has no note to print
    assert capsys.readouterr().out == "sync_templates: nothing to change\n"


def test_check_shows_the_move_and_changes_nothing(tmp_path, capsys):
    before = instructions(old())
    root = project(tmp_path, {"CLAUDE.md": before})
    assert sync.main([str(root), TO_AGENTS, "--check"]) == 1
    out = capsys.readouterr().out
    assert "AGENTS.md: would be written from CLAUDE.md, byte for byte" in out
    assert "CLAUDE.md: would be replaced by templates/CLAUDE.md" in out
    assert out.endswith("sync_templates: 3 change(s) to make\n")
    assert text_of(root, "CLAUDE.md") == before and not (root / "AGENTS.md").exists()


def test_an_import_of_a_missing_agents_md_is_left_to_the_owner(tmp_path, capsys):
    root = project(tmp_path, {"CLAUDE.md": IMPORT})
    assert sync.main([str(root), TO_AGENTS]) == 1
    assert text_of(root, "CLAUDE.md") == IMPORT and not (root / "AGENTS.md").exists()
    assert "there is no AGENTS.md. Nothing was moved." in capsys.readouterr().out


def test_a_template_claude_md_that_does_not_import_stops_the_tool(tmp_path, capsys):
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "AGENTS.md").write_text(AGENTS_T)
    (templates / "CLAUDE.md").write_text("Read AGENTS.md.\n")
    root = project(tmp_path, {"CLAUDE.md": instructions()})
    assert sync.main([str(root), TO_AGENTS], templates=templates) == 2
    assert "templates/CLAUDE.md does not import AGENTS.md" in capsys.readouterr().err
    assert text_of(root, "CLAUDE.md") == instructions()
    assert not (root / "AGENTS.md").exists()


# sync_templates.py --all: the instruction files and the issue templates


def test_all_gives_a_project_without_instructions_both_files(tmp_path, capsys):
    root = project(tmp_path, {})
    assert sync.main([str(root)]) == 1
    said = capsys.readouterr().out
    assert "AGENTS.md is missing. `--all` copies templates/AGENTS.md and" in said
    assert sync.main([str(root), "--all"]) == 0
    assert text_of(root, "AGENTS.md") == AGENTS_T
    assert text_of(root, "CLAUDE.md") == CLAUDE_T
    out = capsys.readouterr().out
    assert "AGENTS.md: was copied from templates/AGENTS.md; fill in its title" in out
    assert "CLAUDE.md: was copied from templates/CLAUDE.md, which imports" in out
    assert current(root) == []


def test_all_adds_the_import_to_an_agents_md_alone_and_no_agents_md_to_a_claude_md(
    tmp_path,
):
    alone = project(tmp_path / "a", {"AGENTS.md": instructions()})
    assert sync.main([str(alone), "--all"]) == 0
    assert text_of(alone, "CLAUDE.md") == CLAUDE_T
    assert text_of(alone, "AGENTS.md") == instructions()
    unmoved = project(tmp_path / "b", {"CLAUDE.md": instructions()})
    assert sync.main([str(unmoved), "--all"]) == 0
    assert not (unmoved / "AGENTS.md").exists()  # only --to-agents-md moves
    assert text_of(unmoved, "CLAUDE.md") == instructions()


def test_all_copies_the_issue_templates_when_the_project_has_none(tmp_path, capsys):
    root = project(tmp_path, {"AGENTS.md": instructions(), "CLAUDE.md": CLAUDE_T})
    assert standards.check(root, require_ticket_template=True) != []
    assert sync.main([str(root), "--all", "--check"]) == 1
    assert not (root / ".github" / "ISSUE_TEMPLATE").exists()
    assert sync.main([str(root), "--all"]) == 0
    out = capsys.readouterr().out
    for name in ("ticket.md", "config.yml"):
        rel = f".github/ISSUE_TEMPLATE/{name}"
        assert f"{rel}: was copied from templates/ISSUE_TEMPLATE/{name}" in out
        source = ROOT / "templates" / "ISSUE_TEMPLATE" / name
        assert (root / rel).read_bytes() == source.read_bytes()
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    assert standards.check(root, require_ticket_template=True) == []
    assert sync.main([str(root), "--all", "--check"]) == 0  # nothing left to copy


@pytest.mark.parametrize(
    "theirs",
    [".github/ISSUE_TEMPLATE/bug.yml", ".github/issue_template/Feature.MD"],
)
def test_all_adds_no_issue_template_beside_the_projects_own(tmp_path, capsys, theirs):
    files = {"AGENTS.md": instructions(), "CLAUDE.md": CLAUDE_T, theirs: "Ours.\n"}
    root = project(tmp_path, files)
    assert sync.main([str(root), "--all", "--overwrite"]) == 0
    folder = (root / theirs).parent
    assert [entry.name for entry in folder.iterdir()] == [Path(theirs).name]
    assert text_of(root, theirs) == "Ours.\n"
    out = capsys.readouterr().out
    note = f".github/ISSUE_TEMPLATE: not copied, the project has {theirs}"
    assert out.count(note) == 1 and "ticket.md" not in out


def test_all_keeps_a_ticket_template_that_differs_unless_overwrite(tmp_path, capsys):
    ticket = ".github/ISSUE_TEMPLATE/ticket.md"
    files = {"AGENTS.md": instructions(), "CLAUDE.md": CLAUDE_T, ticket: "Ours.\n"}
    root = project(tmp_path, files)
    assert sync.main([str(root), "--all"]) == 0
    assert text_of(root, ticket) == "Ours.\n"
    out = capsys.readouterr().out
    assert f"{ticket}: kept as it is; it differs from templates/ISSUE_TEMPLATE/" in out
    assert ".github/ISSUE_TEMPLATE/config.yml: was copied" in out  # it had none
    assert sync.main([str(root), "--all", "--overwrite"]) == 0
    template = ROOT / "templates" / "ISSUE_TEMPLATE" / "ticket.md"
    assert text_of(root, ticket) == template.read_text()


# fleet.py: the instruction file, whichever name it has


def test_the_fleet_report_names_the_instruction_file():
    assert fleet.instructions({"AGENTS.md", "README.md"}) == "AGENTS.md"
    assert fleet.instructions({"CLAUDE.md"}) == "CLAUDE.md"
    assert fleet.instructions({"AGENTS.md", "CLAUDE.md"}) == "both"
    assert fleet.instructions({"README.md"}) is None
    now = datetime(2026, 11, 13, tzinfo=timezone.utc)
    rows = []
    for name, root in (
        ("alpha", ("AGENTS.md",)),
        ("bravo", ("AGENTS.md", "CLAUDE.md")),
        ("charlie", ("CLAUDE.md",)),
        ("delta", ("README.md",)),
    ):
        node = {"name": name, "root": {"entries": [{"name": n} for n in root]}}
        rows.append(fleet.row(node, 2, now, None))
    assert [r["instructions"] for r in rows] == ["AGENTS.md", "both", "CLAUDE.md", None]
    assert [r["templates"]["instructions"] for r in rows] == [True, True, True, False]
    head, *lines = fleet.table(rows)
    column = head.split().index("instructions")
    assert [line.split()[column] for line in lines] == [
        "AGENTS.md",
        "both",
        "CLAUDE.md",
        "-",
    ]


# the documents


def test_readme_describes_the_layout_the_move_and_who_reads_agents_md():
    readme = (ROOT / "README.md").read_text()
    for needle in (
        "| `templates/AGENTS.md` |",
        "| `templates/CLAUDE.md` |",
        "`@AGENTS.md`",
        "`--to-agents-md`",
        "Copy `templates/AGENTS.md` to `AGENTS.md` and `templates/CLAUDE.md` to",
        "### Which tools read `AGENTS.md`",
        "Not verified: Grok",
        "https://code.claude.com/docs/en/memory",
        "https://agents.md",
    ):
        assert needle in readme, needle
    assert "Copy `templates/CLAUDE.md` to `CLAUDE.md` and fill in" not in readme
