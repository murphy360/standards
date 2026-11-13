"""A project's copy of the templates stays current: the check and the sync tool.

``check_standards.py --require-current-templates`` compares the shared text of a
project's ``CLAUDE.md`` or ``AGENTS.md`` with the template beside the tool, and
``sync_templates.py`` rewrites it. The projects here keep the shared text in
``CLAUDE.md``, as every project did before the template moved to ``AGENTS.md``;
tests/test_agents_md.py holds the projects that moved. Every project here is made
up, in a scratch directory: no network.
"""

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))  # the tools import their sibling modules
standards = importlib.import_module("check_standards")
sync = importlib.import_module("sync_templates")
fleet_rehearsal = importlib.import_module("fleet_rehearsal")

TEMPLATE = (ROOT / "templates" / sync.template()["name"]).read_text()
_lines = TEMPLATE.splitlines(keepends=True)
_begin, _end = sync.markers(_lines, "templates/AGENTS.md")
BEGIN, END = _lines[_begin], _lines[_end]
SHARED = "".join(_lines[_begin + 1 : _end])
TITLE = "# Working on demo\n\n"
# trailing spaces, a tab and no final newline: the tool must keep every byte
OURS = "\n## This project\n\nBuild with `make`.  \nPorts:\t8080 and 9090.\n\n\nEnd"
DEPENDABOT = (
    "version: 2\nupdates:\n  - package-ecosystem: github-actions\n    directory: /\n"
)
RESULT_PROBLEM = "has a job named `result`"


def claude(shared: str = SHARED, begin: str = BEGIN, end: str = END) -> str:
    """A project's CLAUDE.md: its own title, the shared text, its own section."""
    return TITLE + begin + shared + end + OURS


def project(tmp_path, files: dict | None = None) -> Path:
    """A scratch project made from the templates, plus or instead ``files``."""
    root = tmp_path / "project"
    given = {
        "CLAUDE.md": claude(),
        ".github/dependabot.yml": DEPENDABOT,
        ".github/workflows/ci.yml": (ROOT / "templates" / "ci.yml").read_text(),
        **(files or {}),
    }
    for rel, text in given.items():
        if text is not None:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(text.encode())
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    return root


def old(shared: str = SHARED) -> str:
    """The shared text as an adopter that fell behind has it: a rule reworded,
    and the last rule missing."""
    lines = shared.splitlines(keepends=True)
    rule = next(i for i, line in enumerate(lines) if line.startswith("- "))
    lines[rule] = "- An old rule the template no longer has.\n"
    last = max(i for i, line in enumerate(lines) if line.startswith("- "))
    return "".join(lines[:last] + lines[last + 1 :])


def check(root: Path) -> list[str]:
    return standards.check(root, require_current_templates=True)


# the markers in the template


def test_the_template_holds_the_shared_text_between_two_invisible_markers():
    assert TEMPLATE.count("standards:shared:begin") == 1
    assert TEMPLATE.count("standards:shared:end") == 1
    for marker in (BEGIN, END):  # an HTML comment: a rendered page shows nothing
        assert marker.startswith("<!-- standards:shared:") and marker.endswith("-->\n")
    above, below = TEMPLATE.split(BEGIN)[0], TEMPLATE.split(END)[1]
    assert above.strip() == "# Working on <project>"  # only the title is above
    assert below.lstrip().startswith("## This project")
    assert below.strip().count("\n## ") == 0  # and only "This project" is below
    for heading in ("## How work arrives", "## Standards every project keeps"):
        assert heading in SHARED
    assert "This project" not in "".join(
        line for line in SHARED.splitlines() if line.startswith("#")
    )


def test_the_template_block_is_read_from_whichever_file_holds_the_markers(tmp_path):
    """Made-up templates: the block is found by its markers, not by a file name."""
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "CLAUDE.md").write_text("@AGENTS.md\n")
    (templates / "AGENTS.md").write_text(f"# T\n\n{BEGIN}\nOne rule.\n\n{END}")
    want = sync.template(templates)
    assert want["name"] == "AGENTS.md" and want["block"] == ["One rule."]
    root = project(tmp_path, {"CLAUDE.md": "@AGENTS.md\n"})
    (root / "AGENTS.md").write_text(f"# Demo\n{BEGIN}One rule.\n{END}\nOurs.\n")
    assert sync.block_problems(root, templates) == []
    (root / "AGENTS.md").write_text(f"# Demo\n{BEGIN}Two rules.\n{END}\nOurs.\n")
    (problem,) = sync.block_problems(root, templates)
    assert problem.startswith("AGENTS.md: the shared text is behind")
    (templates / "AGENTS.md").write_text("no markers\n")
    (problem,) = sync.block_problems(root, templates)
    assert "a fault in murphy360/standards" in problem


# check_standards.py --require-current-templates


def test_a_project_made_from_the_templates_passes(tmp_path):
    assert check(project(tmp_path)) == []


def test_the_check_is_off_by_default(tmp_path):
    root = project(tmp_path, {"CLAUDE.md": "x", ".github/workflows/ci.yml": None})
    assert standards.check(root) == []
    assert len(check(root)) == 2  # no markers, and no `result` job


def test_an_old_block_fails_and_names_the_file_the_lines_and_the_command(tmp_path):
    root = project(tmp_path, {"CLAUDE.md": claude(old())})
    (problem,) = check(root)
    first, *shown = problem.splitlines()
    assert first.startswith("CLAUDE.md: the shared text is behind templates/AGENTS.md")
    assert "`python3 <standards>/tools/sync_templates.py .`" in first
    assert shown[0] == "  here:     - An old rule the template no longer has."
    assert shown[1].startswith("  template: - ")
    assert len(shown) <= sync.SHOWN + 1 and all(len(s) <= 102 for s in shown)


def test_a_long_difference_is_cut_short():
    have = [f"old {n}" for n in range(40)]
    shown = sync.difference(have, ["new " + "x" * 300])
    assert len(shown) == sync.SHOWN + 1
    assert shown[-1] == f"  ... and {41 - sync.SHOWN} more line(s)"
    assert sync.difference(["x" * 300], [])[0].endswith("...")


def test_a_file_without_markers_fails_and_says_to_run_the_sync_tool(tmp_path):
    root = project(tmp_path, {"CLAUDE.md": TITLE + SHARED + OURS})
    (problem,) = check(root)
    assert problem.startswith("CLAUDE.md: no `standards:shared` markers")
    assert "Run the sync tool" in problem and sync.COMMAND in problem


def test_whitespace_only_differences_pass(tmp_path):
    spaced = "\n\n" + SHARED.replace("\n", "  \t\n") + "\n\n"
    windows = claude(spaced).replace("\n", "\r\n")
    root = project(tmp_path, {"CLAUDE.md": windows})
    assert check(root) == []
    assert sync.main([str(root)]) == 0  # and the tool has nothing to rewrite
    assert (root / "CLAUDE.md").read_bytes() == windows.encode()


def test_broken_markers_are_a_gap_of_their_own(tmp_path):
    root = project(tmp_path, {"CLAUDE.md": claude(end="")})
    (problem,) = check(root)
    assert problem.startswith("CLAUDE.md: the markers are broken")
    assert "has 1 and 0" in problem
    assert sync.main([str(root)]) == 2  # the tool does not guess either


def test_ci_needs_a_job_named_result_in_any_workflow(tmp_path):
    job = "jobs:\n  {key}:\n    {name}runs-on: ubuntu-latest\n    steps: []\n"
    plain = job.format(key="tests", name="")
    root = project(tmp_path, {".github/workflows/ci.yml": plain})
    (problem,) = check(root)
    assert RESULT_PROBLEM in problem and "templates/ci.yml" in problem
    for n, (key, name) in enumerate(
        [
            ("result", ""),
            ("gate", "name: result\n    "),
            ("gate", 'name: "result"\n    '),
        ]
    ):
        other = project(
            tmp_path / str(n),
            {
                ".github/workflows/ci.yml": plain,
                ".github/workflows/gate.yaml": job.format(key=key, name=name),
            },
        )
        assert check(other) == []
    renamed = job.format(key="result", name="name: Every check\n    ")
    root = project(tmp_path / "renamed", {".github/workflows/ci.yml": renamed})
    assert RESULT_PROBLEM in check(root)[0]  # the check's name is what counts
    none = project(tmp_path / "none", {".github/workflows/ci.yml": None})
    assert RESULT_PROBLEM in check(none)[0]


def test_main_wires_the_flag_when_run_as_a_script_from_another_directory(tmp_path):
    root = project(tmp_path, {"CLAUDE.md": claude(old())})
    tool = [sys.executable, str(ROOT / "tools" / "check_standards.py"), "--root", root]
    res = subprocess.run(tool, cwd=tmp_path, capture_output=True, text=True)
    assert res.returncode == 0 and res.stdout == "standards: OK\n"
    res = subprocess.run(
        [*tool, "--require-current-templates"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 1, res.stdout + res.stderr
    assert sync.COMMAND in res.stdout
    assert res.stdout.endswith("standards: FAILED, 1 gap(s)\n")


# sync_templates.py


def test_sync_rewrites_only_the_block_and_keeps_the_rest_byte_for_byte(
    tmp_path, capsys
):
    begin = "  <!-- standards:shared:begin: this project's own wording -->  \n"
    before = claude(old(), begin=begin)
    root = project(tmp_path, {"CLAUDE.md": before})
    assert sync.main([str(root)]) == 0
    after = (root / "CLAUDE.md").read_bytes().decode()
    assert after.startswith(TITLE + begin)  # the title and the marker line
    assert after.endswith(END + OURS)  # "This project", byte for byte
    assert after == claude(begin=begin)
    assert check(root) == []
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "CLAUDE.md: the shared text was rewritten from templates/AGENTS.md"
    assert out[1].startswith("  here:     ") and out[-1].endswith("1 change(s) made")
    assert sync.main([str(root)]) == 0  # a second run has nothing to do
    assert capsys.readouterr().out.endswith("\nsync_templates: nothing to change\n")
    assert (root / "CLAUDE.md").read_bytes().decode() == after


def test_sync_keeps_the_line_endings_of_the_file(tmp_path):
    before = claude(old()).replace("\n", "\r\n")
    root = project(tmp_path, {"CLAUDE.md": before})
    assert sync.main([str(root)]) == 0
    after = (root / "CLAUDE.md").read_bytes().decode()
    assert after == claude().replace("\n", "\r\n")


def test_check_changes_nothing_and_exits_1_when_something_would_change(
    tmp_path, capsys
):
    before = claude(old())
    root = project(tmp_path, {"CLAUDE.md": before})
    assert sync.main([str(root), "--check"]) == 1
    out = capsys.readouterr().out
    assert "CLAUDE.md: the shared text would be rewritten" in out
    assert out.endswith("sync_templates: 1 change(s) to make\n")
    assert (root / "CLAUDE.md").read_bytes() == before.encode()
    assert sync.main([str(root), "--check", "--all"]) == 1
    assert not (root / ".pre-commit-config.yaml").exists()
    assert sync.main([str(root), "--all"]) == 0
    assert sync.main([str(root), "--check", "--all"]) == 0


def test_a_file_without_markers_is_never_guessed_at(tmp_path, capsys):
    before = TITLE + "Shared text from long ago.\n\n## Old rules\n\nOne.\n" + OURS
    root = project(tmp_path, {"CLAUDE.md": before})
    assert sync.main([str(root)]) == 1
    out = capsys.readouterr().out
    assert "CLAUDE.md: it has no markers, so its text was not changed" in out
    assert BEGIN.strip() in out and '--shared-until "## This project"' in out
    assert out.endswith("nothing to change, 1 step(s) left to do by hand\n")
    assert sync.main([str(root), "--shared-until", "## Their project"]) == 1
    assert "it has 0 lines that read `## Their project`" in capsys.readouterr().out
    assert (root / "CLAUDE.md").read_bytes() == before.encode()


def test_shared_until_says_where_the_block_goes(tmp_path, capsys):
    before = TITLE + "Shared text from long ago.\n\n## Old rules\n\nOne.\n" + OURS
    root = project(tmp_path, {"CLAUDE.md": before})
    assert sync.main([str(root), "--shared-until", "## This project"]) == 0
    after = (root / "CLAUDE.md").read_bytes().decode()
    assert after == claude()  # the title, the block between markers, their section
    assert "(7 lines) was replaced by the shared text" in capsys.readouterr().out
    assert check(root) == []
    untitled = project(tmp_path / "b", {"CLAUDE.md": "Old.\n" + OURS.lstrip("\n")})
    assert sync.main([str(untitled), "--shared-until", "## This project"]) == 0
    after = (untitled / "CLAUDE.md").read_bytes().decode()
    assert after.startswith(BEGIN) and after.endswith(END + OURS)


def test_all_copies_the_templates_a_project_lacks_and_keeps_the_ones_it_has(
    tmp_path, capsys
):
    root = project(tmp_path, {".github/dependabot.yml": DEPENDABOT, "CLAUDE.md": None})
    assert sync.main([str(root)]) == 1  # nothing to rewrite, and it says so
    assert "AGENTS.md is missing. `--all` copies" in capsys.readouterr().out
    assert sync.main([str(root), "--all"]) == 0
    out = capsys.readouterr().out
    for rel, source in (
        ("AGENTS.md", "AGENTS.md"),
        ("CLAUDE.md", "CLAUDE.md"),
        (".github/ISSUE_TEMPLATE/ticket.md", "ISSUE_TEMPLATE/ticket.md"),
        (".github/ISSUE_TEMPLATE/config.yml", "ISSUE_TEMPLATE/config.yml"),
        (".github/pull_request_template.md", "pull_request_template.md"),
        (".pre-commit-config.yaml", "pre-commit-config.yaml"),
    ):
        assert f"{rel}: was copied from templates/{source}" in out
        assert (root / rel).read_bytes() == (ROOT / "templates" / source).read_bytes()
    assert (root / ".github/dependabot.yml").read_text() == DEPENDABOT  # kept
    assert ".github/dependabot.yml: kept as it is" in out and "--overwrite" in out
    assert out.endswith("sync_templates: 6 change(s) made\n")
    assert not (root / "rulesets").exists() and not (root / ".github/rulesets").exists()
    assert sync.main([str(root), "--all", "--overwrite"]) == 0
    template = (ROOT / "templates" / "dependabot.yml").read_text()
    assert (root / ".github/dependabot.yml").read_text() == template
    assert "dependabot.yml: was replaced by" in capsys.readouterr().out


def test_all_does_not_add_a_second_pull_request_template(tmp_path, capsys):
    root = project(tmp_path, {".github/PULL_REQUEST_TEMPLATE.md": "Ours.\n"})
    assert sync.main([str(root), "--all", "--overwrite"]) == 0
    assert not (root / ".github/pull_request_template.md").exists()
    assert (root / ".github/PULL_REQUEST_TEMPLATE.md").read_text() == "Ours.\n"
    out = capsys.readouterr().out
    assert "not copied, the project has .github/PULL_REQUEST_TEMPLATE.md" in out


def test_overwrite_needs_all_and_the_checkout_must_exist(tmp_path):
    for argv in ([str(tmp_path), "--overwrite"], [str(tmp_path / "missing")]):
        with pytest.raises(SystemExit) as e:
            sync.main(argv)
        assert e.value.code == 2


# the workflow, the CI template and the rehearsal


def test_the_workflow_passes_check_templates_to_the_tool(tmp_path):
    """The last step's shell, as the workflow holds it, against the real tools."""
    text = (ROOT / ".github" / "workflows" / "standards-check.yml").read_text()
    declared = text.split("      check-templates:\n", 1)[1].splitlines()[:2]
    assert [line.strip() for line in declared] == ["type: boolean", "default: false"]
    assert "CHECK_TEMPLATES: ${{ inputs.check-templates }}" in text
    step = text.split("- name: Does this repository follow the standards?\n", 1)[1]
    lines = step.split("run: |\n", 1)[1].splitlines()
    shell = "\n".join(line[10:] for line in lines) + "\n"
    temp = tmp_path / "runner-temp"
    temp.mkdir()
    (temp / "standards").symlink_to(ROOT, target_is_directory=True)
    root = project(tmp_path, {"CLAUDE.md": claude(old())})
    env = {"PATH": os.environ["PATH"], "RUNNER_TEMP": str(temp)}
    results = {}
    for on in ("false", "true"):
        results[on] = subprocess.run(
            ["bash", "-e", "-c", shell],
            cwd=root,
            env={**env, "CHECK_TEMPLATES": on},
            capture_output=True,
            text=True,
        )
    assert results["false"].returncode == 0, results["false"].stderr
    assert results["true"].returncode == 1, results["true"].stderr
    assert "CLAUDE.md: the shared text is behind" in results["true"].stdout


def test_the_ci_template_names_the_input():
    text = (ROOT / "templates" / "ci.yml").read_text()
    assert "#   check-templates: true" in text


def test_the_rehearsal_runs_the_template_check_an_adopter_turned_on(tmp_path):
    call = {"workflow": "standards-check", "with": {"check-templates": "true"}}
    (step,) = fleet_rehearsal.plan({"calls": [call]})
    assert step["args"] == ["--require-current-templates"]
    made_up = tmp_path / "standards"
    for rel in ("tools/check_standards.py", "templates/CLAUDE.md"):
        (made_up / rel).parent.mkdir(parents=True)
        (made_up / rel).write_text("made up\n")
    for args in (
        ["init", "-q"],
        ["add", "."],
        ["-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "x"],
        ["tag", "v9"],
    ):
        subprocess.run(["git", *args], cwd=made_up, check=True)
    tools = fleet_rehearsal.tools_at("v9", made_up, tmp_path / "out")
    # the template is beside the tools, where check_standards.py looks for it
    assert (tools.parent / "templates" / "CLAUDE.md").exists()
