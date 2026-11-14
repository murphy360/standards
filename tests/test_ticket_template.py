"""The ticket template, and the check that a project has one.

``templates/ISSUE_TEMPLATE/ticket.md`` is the shape of a ticket: an implementer's whole
brief. These tests read the files only. They keep the front matter what GitHub's
documentation asks of a Markdown issue template, keep every section in its place, keep
this repository's copy equal to the template, and try
``check_standards.py --require-ticket-template`` both ways.
"""

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates" / "ISSUE_TEMPLATE"
COPIES = ROOT / ".github" / "ISSUE_TEMPLATE"
TICKET = TEMPLATES / "ticket.md"
CONFIG = TEMPLATES / "config.yml"
WORKFLOW = ROOT / ".github" / "workflows" / "standards-check.yml"

# The headings of a ticket, in order, with their level.
SECTIONS = [
    "## Problem",
    "## What to build",
    "## Acceptance",
    "## For the implementer",
    "### Read first",
    "### Build",
    "### Tests",
    "### Docs",
    "### Done",
    "## Out of scope",
    "## Owner decision",
]
# What GitHub's documentation names for a Markdown issue template (read 2026-11-13).
FRONT_MATTER_KEYS = {"name", "about", "title", "labels", "type", "assignees"}
# A value every YAML parser reads as the same plain string: no quote, colon or hash.
PLAIN_VALUE_RE = re.compile(r"[A-Za-z][A-Za-z0-9 ,.'-]*")
BASE = {".github/dependabot.yml": "version: 2\nupdates:\n", "CLAUDE.md": "x"}
MISSING = ".github/ISSUE_TEMPLATE has no ticket template"

_spec = importlib.util.spec_from_file_location(
    "check_standards", ROOT / "tools" / "check_standards.py"
)
standards = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(standards)


def front_matter(text: str) -> tuple[dict[str, str], str]:
    """The front matter's keys and values, and the body under it. No YAML library."""
    lines = text.splitlines()
    assert lines[0] == "---", "the file must open with the front matter"
    end = lines.index("---", 1)
    pairs = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(": ")
        assert sep and PLAIN_VALUE_RE.fullmatch(value), f"not a plain pair: {line!r}"
        pairs[key] = value
    return pairs, "\n".join(lines[end + 1 :])


def repo(root: Path, files: dict[str, str]) -> Path:
    """A git repository holding ``files``, all added, nothing committed."""
    for rel, text in {**BASE, **files}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    return root


# the template: front matter


def test_front_matter_has_the_two_fields_github_needs():
    pairs, _ = front_matter(TICKET.read_text())
    assert pairs["name"] and pairs["about"]
    # "The template name must be more than 3 characters" (GitHub's documentation).
    assert len(pairs["name"]) > 3


def test_front_matter_sets_no_label_and_no_assignee():
    pairs, _ = front_matter(TICKET.read_text())
    assert set(pairs) <= FRONT_MATTER_KEYS
    # A project may have neither the label nor the person, and GitHub would drop them.
    assert not {"labels", "assignees", "type"} & set(pairs)


def test_template_is_a_markdown_file_github_reads():
    # GitHub reads *.md (a template) and *.yml (a form) in .github/ISSUE_TEMPLATE.
    assert TICKET.suffix == ".md"
    assert standards.has_ticket_template([".github/ISSUE_TEMPLATE/" + TICKET.name])


# the template: sections


def test_every_section_is_there_in_order():
    _, body = front_matter(TICKET.read_text())
    headings = [line for line in body.splitlines() if line.startswith("#")]
    assert headings == SECTIONS


def test_every_section_says_in_one_comment_what_goes_in_it():
    _, body = front_matter(TICKET.read_text())
    lines = [line for line in body.splitlines() if line.strip()]
    for heading in SECTIONS:
        after = lines[lines.index(heading) + 1]
        if heading == "## For the implementer":
            assert after == "### Read first"  # its five parts carry the comments
            continue
        assert after.startswith("<!-- ") and after.endswith(" -->"), heading
        assert len(after) > len("<!--  -->") + 20, heading


def test_acceptance_is_a_checklist():
    _, body = front_matter(TICKET.read_text())
    acceptance = body.split("## Acceptance")[1].split("## For the implementer")[0]
    assert "\n- [ ]" in acceptance


def test_template_keeps_the_house_style():
    text = TICKET.read_text() + CONFIG.read_text()
    assert "—" not in text and "–" not in text  # no em-dash, no en-dash
    assert text.endswith("\n") and not re.search(r"[ \t]+$", text, re.MULTILINE)


# the chooser's config


def test_config_keeps_blank_issues_and_sets_nothing_else():
    settings = [
        line
        for line in CONFIG.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert settings == ["blank_issues_enabled: true"]


# this repository uses its own template


@pytest.mark.parametrize("name", ["ticket.md", "config.yml"])
def test_this_repositorys_copy_equals_the_template(name):
    assert (COPIES / name).read_bytes() == (TEMPLATES / name).read_bytes()


def test_no_other_file_sits_beside_the_copies():
    names = sorted(p.name for p in COPIES.iterdir())
    assert names == sorted(p.name for p in TEMPLATES.iterdir())


def test_this_repository_passes_its_own_check():
    gaps = standards.check(ROOT, require_ticket_template=True)
    assert not [g for g in gaps if g.startswith(MISSING)]
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert re.search(r"^\s+check-ticket-template: true\b", ci, re.MULTILINE)


# check_standards.py --require-ticket-template


def test_ticket_template_check_is_off_by_default(tmp_path):
    root = repo(tmp_path, {})
    assert standards.check(root) == []
    assert standards.main(["--root", str(root)]) == 0


def test_ticket_template_check_fails_without_a_template(tmp_path):
    root = repo(tmp_path, {})
    gaps = standards.check(root, require_ticket_template=True)
    assert len(gaps) == 1 and gaps[0].startswith(MISSING)
    assert "templates/ISSUE_TEMPLATE/ticket.md" in gaps[0]


@pytest.mark.parametrize(
    "path",
    [
        ".github/ISSUE_TEMPLATE/ticket.md",
        ".github/ISSUE_TEMPLATE/bug.yml",  # an issue form
        ".github/issue_template/Ticket.MD",  # GitHub reads the names in any case
    ],
)
def test_ticket_template_check_passes_wherever_github_finds_one(tmp_path, path):
    root = repo(tmp_path, {path: "---\nname: Ticket\nabout: Work\n---\n"})
    assert standards.check(root, require_ticket_template=True) == []


@pytest.mark.parametrize(
    "path",
    [
        ".github/ISSUE_TEMPLATE/config.yml",  # the chooser's config is not a template
        ".github/ISSUE_TEMPLATE/notes.txt",
        ".github/ISSUE_TEMPLATE/old/ticket.md",  # GitHub does not read a subdirectory
        "ISSUE_TEMPLATE/ticket.md",
        "templates/ISSUE_TEMPLATE/ticket.md",
        ".github/ticket.md",
    ],
)
def test_ticket_template_check_fails_where_github_finds_none(tmp_path, path):
    root = repo(tmp_path, {path: "x\n"})
    gaps = standards.check(root, require_ticket_template=True)
    assert [g for g in gaps if g.startswith(MISSING)]


def test_main_wires_the_ticket_template_flag(tmp_path, capsys):
    root = repo(tmp_path, {})
    assert standards.main(["--root", str(root), "--require-ticket-template"]) == 1
    out = capsys.readouterr().out
    assert MISSING in out and "standards: FAILED, 1 gap(s)" in out
    path = root / ".github" / "ISSUE_TEMPLATE" / "ticket.md"
    path.parent.mkdir(parents=True)
    path.write_text(TICKET.read_text())
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    assert standards.main(["--root", str(root), "--require-ticket-template"]) == 0
    assert "standards: OK" in capsys.readouterr().out


# the workflow input, and the rehearsal


def test_workflow_input_is_off_by_default_and_passes_the_flag():
    text = WORKFLOW.read_text()
    declared = re.search(
        r"^      check-ticket-template:\n"
        r"        type: boolean\n        default: false\n",
        text,
        re.MULTILINE,
    )
    assert declared, "the input must be a boolean that defaults to false"
    # Passed the way the other opt-in checks are: through the environment, never
    # written into the script.
    assert "CHECK_TICKET_TEMPLATE: ${{ inputs.check-ticket-template }}" in text
    assert text.endswith(
        'run: bash "$RUNNER_TEMP/standards/tools/steps/standards-check.sh"\n'
    )
    script = (ROOT / "tools" / "steps" / "standards-check.sh").read_text()
    assert "inputs." not in script
    assert re.search(
        r'if \[ "\$CHECK_TICKET_TEMPLATE" = "true" \]; then\n'
        r"\s+args\+=\(--require-ticket-template\)\n\s*fi\n",
        script,
    )


def test_rehearsal_passes_the_flag_for_a_caller_that_turned_it_on():
    spec = importlib.util.spec_from_file_location(
        "fleet_rehearsal", ROOT / "tools" / "fleet_rehearsal.py"
    )
    rehearsal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rehearsal)
    flags = dict(rehearsal.CHECK_FLAGS)
    assert flags["check-ticket-template"] == "--require-ticket-template"


# the documents that point at it


def test_how_work_arrives_points_at_the_template():
    # Whichever template carries "How work arrives" must name the ticket template
    # (templates/AGENTS.md since the shared text moved there).
    sections = [
        text.split("## How work arrives")[1].split("\n## ")[0]
        for text in (p.read_text() for p in (ROOT / "templates").glob("*.md"))
        if "## How work arrives" in text
    ]
    assert sections, 'no template has a "How work arrives" section'
    assert all(".github/ISSUE_TEMPLATE/ticket.md" in s for s in sections)


def test_readme_names_the_templates_the_flag_and_the_run_order():
    readme = (ROOT / "README.md").read_text()
    for needle in (
        "`templates/ISSUE_TEMPLATE/ticket.md`",
        "`templates/ISSUE_TEMPLATE/config.yml`",
        "`--require-ticket-template`",
        "`check-ticket-template: true`",
        "RUN ORDER\n\n1. #<number> <title>",
    ):
        assert needle in readme, needle
