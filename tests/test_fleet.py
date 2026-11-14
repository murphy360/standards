"""tools/fleet.py and tools/fleet_rehearsal.py: the fleet report and the rehearsal.

Every repository here is made up. No test calls GitHub or ``gh``: the function
that fetches, the one that clones and the one that looks for ruff are injected.
"""

import importlib
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))  # fleet.py imports its sibling module
fleet = importlib.import_module("fleet")
fleet_rehearsal = importlib.import_module("fleet_rehearsal")

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
SHA = "0123456789abcdef0123456789abcdef01234567"
CI = f"""\
name: ci
on:
  pull_request:
jobs:
  standards:
    uses: murphy360/standards/.github/workflows/standards-check.yml@v2
    with:
      check-pr-template: true
  lint:
    uses: murphy360/standards/.github/workflows/python-lint.yml@v2  # the lint
    with:
      format-check: true
      mode: changed  # until the debt is paid
      paths: "src tools"
      format-only: ${{{{ contains(github.event.pull_request.labels.*.name, 'x') }}}}
  # node:
  #   uses: murphy360/standards/.github/workflows/node-lint.yml@v1
  shell:
    uses: "murphy360/standards/.github/workflows/shell-lint.yml@{SHA}"  # v2.1.0
  other:
    uses: someone/else/.github/workflows/python-lint.yml@v1
  build:
    runs-on: ubuntu-latest
    steps:
      - name: result
        uses: actions/checkout@{SHA}  # v7.0.1
        with:
          mode: all
  result:
    name: result
    if: always()
    needs: [standards, lint]
    runs-on: ubuntu-latest
    steps:
      - run: echo done
"""


def tree(*names, texts=None):
    texts = texts or {}
    return {"entries": [{"name": n, "object": {"text": texts.get(n)}} for n in names]}


def node(name, ci=None, root=(), github=(), rulesets=(), pulls=(), topics=()):
    """A repository as the GraphQL query returns it."""
    return {
        "name": name,
        "repositoryTopics": {"nodes": [{"topic": {"name": t}} for t in topics]},
        "root": tree(*root),
        "github": tree(*github) if github or ci else None,
        "workflows": tree("ci.yml", texts={"ci.yml": ci}) if ci else None,
        "rulesets": None
        if rulesets is None
        else {"nodes": [{"name": n, "enforcement": e} for n, e in rulesets]},
        "pullRequests": {
            "nodes": [{"createdAt": at, "author": {"login": who}} for who, at in pulls]
        },
    }


def caller(workflow, ref, with_=""):
    return (
        f"jobs:\n  job:\n    uses: murphy360/standards/.github/workflows/"
        f"{workflow}.yml@{ref}\n{with_}"
    )


def pages(*nodes_per_page):
    """A fetch function that serves the nodes one page per call."""
    calls = []

    def fetch(query, variables):
        calls.append(variables)
        index = int(variables["cursor"] or 0)
        return {
            "data": {
                "repositoryOwner": {
                    "repositories": {
                        "pageInfo": {
                            "hasNextPage": index + 1 < len(nodes_per_page),
                            "endCursor": str(index + 1),
                        },
                        "nodes": nodes_per_page[index],
                    }
                }
            }
        }

    fetch.calls = calls
    return fetch


FLEET = [
    node(
        "alpha",
        ci=CI,
        root=("CLAUDE.md", ".pre-commit-config.yaml"),
        github=("dependabot.yml", "PULL_REQUEST_TEMPLATE.md", "workflows"),
        rulesets=[("main", "ACTIVE"), ("version-tags", "ACTIVE")],
        pulls=[
            ("dependabot", "2026-11-01T12:00:00Z"),
            ("dependabot[bot]", "2026-10-03T08:00:00Z"),
            ("someone", "2026-01-01T00:00:00Z"),
        ],
    ),
    node("bravo", ci=caller("python-lint", "v1"), root=("CLAUDE.md",), rulesets=None),
    node("charlie", ci=caller("actionlint", "v2")),
    node("delta", root=("README.md",), topics=("no-standards", "game")),
    node("echo", ci="jobs:\n  test:\n    runs-on: ubuntu-latest\n"),
]


def rows():
    return fleet.report(FLEET, "murphy360", 2, NOW)


def by_name(name):
    return next(r for r in rows() if r["name"] == name)


# --- a workflow's text to its jobs and its calls ---


def test_jobs_reads_keys_names_uses_and_with_values():
    jobs = {j["key"]: j for j in fleet.jobs(CI)}
    assert list(jobs) == ["standards", "lint", "shell", "other", "build", "result"]
    assert jobs["lint"]["with"]["mode"] == "changed"  # the comment is not the value
    assert jobs["lint"]["with"]["paths"] == "src tools"  # nor are the quotes
    assert jobs["lint"]["with"]["format-only"].startswith("${{ contains(")
    assert jobs["build"]["with"] == {}  # a step's `with:` is not the job's
    assert jobs["build"]["name"] is None  # nor is a step's name
    assert jobs["result"]["name"] == "result"


def test_jobs_reads_an_inline_with_and_stops_at_the_next_top_level_key():
    text = (
        "jobs:\n"
        "    lint:\n"
        "        uses: murphy360/standards/.github/workflows/node-lint.yml@v2\n"
        "        with: {mode: changed, format-check: 'true'}\n"
        "env:\n"
        "    not-a-job:\n"
        "        uses: murphy360/standards/.github/workflows/image.yml@v2\n"
    )
    jobs = fleet.jobs(text)
    assert [j["key"] for j in jobs] == ["lint"]
    assert jobs[0]["with"] == {"mode": "changed", "format-check": "true"}
    assert fleet.jobs("name: ci\non: push\n") == []


def test_workflow_calls_give_each_workflow_its_ref_and_kind():
    calls = fleet.workflow_calls(CI, 2)
    assert [(c["workflow"], c["ref"], c["kind"]) for c in calls] == [
        ("standards-check", "v2", "current"),
        ("python-lint", "v2", "current"),
        ("shell-lint", SHA, "sha"),
    ]  # not the commented-out caller, and not another owner's workflow
    assert calls[2]["version"] == "v2.1.0"  # the comment beside a SHA
    assert calls[0]["with"] == {"check-pr-template": "true"}


@pytest.mark.parametrize(
    ("ref", "kind"),
    [
        ("v2", "current"),
        ("v2.3.1", "current"),
        ("v1", "older"),
        ("v1.0.0", "older"),
        (SHA, "sha"),
        ("main", "other"),
        (SHA[:12], "other"),
        ("local", "current"),
    ],
)
def test_ref_kind(ref, kind):
    assert fleet.ref_kind(ref, 2) == kind
    text = caller("image", ref)
    if ref != "local":
        assert fleet.workflow_calls(text, 2)[0]["kind"] == kind


def test_the_standards_repository_calls_its_own_workflows_by_path():
    text = "jobs:\n  lint:\n    uses: ./.github/workflows/python-lint.yml\n"
    assert fleet.workflow_calls(text, 2) == []  # any other repository's own file
    call = fleet.workflow_calls(text, 2, local=True)[0]
    assert (call["workflow"], call["ref"], call["kind"]) == (
        "python-lint",
        "local",
        "current",
    )
    own = fleet.report([node("standards", ci=text)], "murphy360", 2, NOW)[0]
    assert own["kinds"] == ["current"]
    theirs = fleet.report([node("standards", ci=text)], "someone", 2, NOW)[0]
    assert theirs["calls"] == []


def test_result_job_is_a_job_whose_check_is_named_result():
    assert fleet.has_result_job(CI)
    assert fleet.has_result_job("jobs:\n  result:\n    runs-on: x\n")
    assert fleet.has_result_job("jobs:\n  done:\n    name: 'result'\n")
    renamed = "jobs:\n  result:\n    name: Every job passed\n"
    assert not fleet.has_result_job(renamed)
    step = "jobs:\n  build:\n    steps:\n      - name: result\n"
    assert not fleet.has_result_job(step)


def test_lint_mode_and_format_only():
    assert by_name("alpha")["lint_mode"] == "changed"
    assert by_name("alpha")["format_only"] is True
    assert by_name("bravo")["lint_mode"] == "all"  # the default
    assert by_name("bravo")["format_only"] is False
    assert by_name("charlie")["lint_mode"] is None  # it calls no lint workflow
    both = caller("python-lint", "v2") + (
        "  node:\n    uses: murphy360/standards/.github/workflows/node-lint.yml@v2\n"
        "    with:\n      mode: changed\n      format-only: false\n"
    )
    calls = fleet.workflow_calls(both, 2)
    assert fleet.lint_mode(calls) == "all+changed"
    assert fleet.format_only(calls) is False
    expr = caller("python-lint", "v2", "    with:\n      mode: ${{ vars.MODE }}\n")
    assert fleet.lint_mode(fleet.workflow_calls(expr, 2)) == "expr"


# --- the rest of a row ---


def test_templates_are_read_from_the_names_in_the_root_and_dot_github():
    assert by_name("alpha")["templates"] == {
        "instructions": True,
        "dependabot": True,
        "pre-commit": True,
        "pr-template": True,  # GitHub takes the name in either case
        "result": True,
    }
    assert by_name("bravo")["templates"] == {
        "instructions": True,
        "dependabot": False,
        "pre-commit": False,
        "pr-template": False,
        "result": False,
    }
    assert not any(by_name("delta")["templates"].values())


def test_ruleset_is_yes_off_no_or_unknown():
    assert fleet.ruleset_state({"nodes": [{"name": "main", "enforcement": "ACTIVE"}]})
    assert by_name("alpha")["ruleset"] == "yes"
    assert by_name("bravo")["ruleset"] == "?"  # the API refused: null
    assert by_name("charlie")["ruleset"] == "no"
    off = {"nodes": [{"name": "main", "enforcement": "DISABLED"}]}
    assert fleet.ruleset_state(off) == "off"
    other = {"nodes": [{"name": "version-tags", "enforcement": "ACTIVE"}]}
    assert fleet.ruleset_state(other) == "no"


def test_dependabot_pull_requests_and_the_age_of_the_oldest():
    alpha = by_name("alpha")
    assert (alpha["dependabot_prs"], alpha["oldest_days"]) == (2, 41)
    charlie = by_name("charlie")
    assert (charlie["dependabot_prs"], charlie["oldest_days"]) == (0, None)
    ghost = [{"createdAt": "2026-11-13T11:00:00Z", "author": None}]
    assert fleet.dependabot_prs(ghost, NOW) == (0, None)
    today = [{"createdAt": "2026-11-13T11:00:00Z", "author": {"login": "dependabot"}}]
    assert fleet.dependabot_prs(today, NOW) == (1, 0)


def test_the_topic_says_a_repository_stays_outside():
    assert by_name("delta")["topic"] is True
    assert by_name("echo")["topic"] is False
    assert by_name("alpha")["topic"] is False


# --- the list, the table and the summary ---


def test_list_repositories_follows_the_pages():
    fetch = pages(FLEET[:2], FLEET[2:4], FLEET[4:])
    nodes = fleet.list_repositories("murphy360", fetch)
    assert [n["name"] for n in nodes] == ["alpha", "bravo", "charlie", "delta", "echo"]
    assert [c["cursor"] for c in fetch.calls] == [None, "1", "2"]
    assert all(c["owner"] == "murphy360" for c in fetch.calls)


def test_an_unknown_owner_is_an_error_not_an_empty_report(capsys):
    def fetch(query, variables):
        return {"data": {"repositoryOwner": None}}

    with pytest.raises(fleet.FleetError, match="no owner named `nobody`"):
        fleet.list_repositories("nobody", fetch)
    assert fleet.main(["--owner", "nobody"], fetch=fetch) == 2
    assert "fleet: no owner named `nobody`" in capsys.readouterr().err


def test_summary_counts_adopters_by_ref_and_the_repositories_outside():
    assert fleet.summary(rows()) == {
        "repositories": 5,
        "adopters": 3,
        "current": 1,  # charlie: every call at v2
        "older": 1,  # bravo
        "sha": 1,  # alpha: one call at a commit
        "other": 0,
        "outside_with_topic": 1,  # delta
        "outside_without_topic": 1,  # echo
    }
    lines = fleet.summary_lines(fleet.summary(rows()), 2)
    assert lines[0] == "5 repositories, 3 adopters."
    assert "  at the current major (v2) only: 1" in lines
    assert "  outside, without the topic: 1" in lines
    assert not any("another ref" in line for line in lines)
    branch = fleet.report([node("x", ci=caller("image", "main"))], "o", 2, NOW)
    assert "  calling another ref (a branch): 1" in fleet.summary_lines(
        fleet.summary(branch), 2
    )


def test_table_has_a_row_per_repository_and_a_column_per_workflow_called():
    lines = fleet.table(rows())
    head = lines[0].split("  ")
    head = [cell.strip() for cell in head if cell.strip()]
    assert head == [
        "repository",
        "actionlint",
        "python",
        "shell",
        "standards",
        "lint",
        "format-only",
        "instructions",
        "dependabot",
        "pre-commit",
        "pr-template",
        "result",
        "ruleset",
        "dependabot PRs",
        "oldest",
        "no-standards",
    ]
    cells = [line.split() for line in lines[1:]]
    # adopters first, each group by name
    assert [c[0] for c in cells] == ["alpha", "bravo", "charlie", "delta", "echo"]
    assert cells[0][1:] == (
        ["-", "v2", SHA[:7], "v2", "changed", "yes"]
        + ["CLAUDE.md", "yes", "yes", "yes", "yes", "yes", "2", "41d", "-"]
    )
    assert cells[1][1:] == ["-", "v1", "-", "-", "all", "-", "CLAUDE.md"] + [
        "-"
    ] * 4 + [
        "?",
        "0",
        "-",
        "-",
    ]
    assert cells[3][1:] == ["-"] * 11 + ["no", "0", "-", "yes"]
    assert len({len(line) for line in lines[:2]}) <= 2  # columns line up
    assert lines[1].index(" changed ") == lines[0].index(" lint ")


def test_main_prints_the_table_the_summary_and_the_note_once(capsys):
    refused = [*FLEET, node("foxtrot", rulesets=None)]
    assert fleet.main(["--owner", "murphy360"], fetch=pages(refused), now=NOW) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("repository")
    assert "6 repositories, 3 adopters." in out
    assert out.count("ruleset `?`") == 1
    assert "refused to list the rulesets of 2 repositories" in out
    assert "admin rights" in out

    assert fleet.main(["--owner", "murphy360"], fetch=pages(FLEET[2:]), now=NOW) == 0
    assert "ruleset `?`" not in capsys.readouterr().out  # nothing was refused


def test_main_json_is_the_same_report_for_a_program(capsys):
    args = ["--owner", "murphy360", "--json", "--major", "2"]
    assert fleet.main(args, fetch=pages(FLEET), now=NOW) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["owner"] == "murphy360"
    assert doc["major"] == 2
    assert doc["repositories"] == rows()
    assert doc["summary"] == fleet.summary(rows())
    assert len(doc["notes"]) == 1


def test_repo_limits_the_report_and_major_changes_what_is_current(capsys):
    args = ["--owner", "murphy360", "--json", "--repo", "bravo", "--major", "1"]
    assert fleet.main(args, fetch=pages(FLEET), now=NOW) == 0
    doc = json.loads(capsys.readouterr().out)
    assert [r["name"] for r in doc["repositories"]] == ["bravo"]
    assert doc["repositories"][0]["kinds"] == ["current"]
    args = ["--owner", "murphy360", "--repo", "zulu"]
    assert fleet.main(args, fetch=pages(FLEET), now=NOW) == 2
    assert "murphy360 has no repository named zulu" in capsys.readouterr().err


def test_the_default_major_is_the_one_this_repository_is_at():
    text = (ROOT / "pyproject.toml").read_text()
    major = int(re.search(r'^version = "(\d+)\.', text, re.MULTILINE).group(1))
    assert fleet.default_major() == major


def test_the_report_is_never_written_to_a_file(capsys):
    """Most repositories it names are private and this one is public: the tool
    has no option that writes the report anywhere."""
    with pytest.raises(SystemExit):
        fleet.main(["--owner", "murphy360", "--output", "report.txt"])
    assert "unrecognized arguments" in capsys.readouterr().err
    source = (ROOT / "tools" / "fleet.py").read_text()
    source += (ROOT / "tools" / "fleet_rehearsal.py").read_text()
    assert "write_text" not in source
    assert not re.search(r"open\([^)]*[\"'][wa]", source)


def test_gh_graphql_keeps_the_data_when_a_field_was_refused(monkeypatch):
    seen = []

    def run(cmd, **kwargs):
        seen.append(cmd)
        answer = {"data": {"repositoryOwner": {}}, "errors": [{"type": "FORBIDDEN"}]}
        return subprocess.CompletedProcess(cmd, 1, json.dumps(answer), "gh: refused")

    monkeypatch.setattr(fleet.subprocess, "run", run)
    answer = fleet.gh_graphql("query", {"owner": "murphy360", "cursor": None})
    assert answer["data"] == {"repositoryOwner": {}}
    assert seen[0][:3] == ["gh", "api", "graphql"]
    assert "owner=murphy360" in seen[0]
    assert not any(arg.startswith("cursor=") for arg in seen[0])  # null: left out


def test_gh_graphql_says_why_it_failed(monkeypatch):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 4, "", "gh: not signed in")

    monkeypatch.setattr(fleet.subprocess, "run", run)
    with pytest.raises(fleet.FleetError, match="not signed in"):
        fleet.gh_graphql("query", {})

    def missing(cmd, **kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(fleet.subprocess, "run", missing)
    with pytest.raises(fleet.FleetError, match="`gh` did not run"):
        fleet.gh_graphql("query", {})


# --- the rehearsal ---

TOOL = """\
import sys
from pathlib import Path
print("{name}", *sys.argv[1:])
bad = Path("{marker}").exists()
print("{name}: " + ("FAILED" if bad else "OK"))
sys.exit(1 if bad else 0)
"""


def git(cwd, *args):
    cmd = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args]
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def standards(tmp_path):
    """A made-up standards checkout: two stub tools, committed and tagged v9."""
    root = tmp_path / "standards"
    (root / "tools").mkdir(parents=True)
    for name, marker in (("code_rules", "debt"), ("check_standards", "gap")):
        text = TOOL.format(name=name, marker=marker)
        (root / "tools" / f"{name}.py").write_text(text)
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "tools")
    git(root, "tag", "v9")
    (root / "tools" / "code_rules.py").write_text("raise SystemExit('not v9')\n")
    return root


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """Where the rehearsal's temporary directory goes, so a test can look."""
    place = tmp_path / "tmp"
    place.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(place))
    return place


def cloner(markers=None, fail=()):
    """A clone function that makes a directory, with the files a test asks for."""
    made = []

    def clone(slug, dest):
        if slug in fail:
            return "repository not found"
        dest.mkdir()
        (dest / "src").mkdir()
        for marker in (markers or {}).get(slug, ()):
            (dest / marker).write_text("")
        made.append((slug, dest))
        return None

    clone.made = made
    return clone


def test_plan_runs_the_tools_the_way_the_callers_ask():
    steps = fleet_rehearsal.plan(by_name("alpha"))
    assert steps == [
        {
            "tool": "check_standards.py",
            "args": ["--require-pr-template"],
            "cwd": ".",
            "changed": False,
        },
        {
            "tool": "code_rules.py",
            "args": ["--all", "--paths", "src", "tools"],  # format-check: true
            "cwd": ".",
            "changed": True,
        },
    ]
    bravo = fleet_rehearsal.plan(by_name("bravo"))
    assert bravo[0]["args"] == ["--all", "--no-format"]  # no format-check
    assert fleet_rehearsal.plan(by_name("charlie")) == []  # actionlint only
    twice = caller("python-lint", "v2") + (
        "  again:\n    uses: murphy360/standards/.github/workflows/python-lint.yml@v2\n"
        "  sub:\n    uses: murphy360/standards/.github/workflows/python-lint.yml@v2\n"
        "    with:\n      working-directory: src\n      paths: ${{ vars.PATHS }}\n"
    )
    row = fleet.report([node("x", ci=twice)], "o", 2, NOW)[0]
    steps = fleet_rehearsal.plan(row)
    assert [s["cwd"] for s in steps] == [".", "src"]  # the same run is not repeated
    both = caller("python-lint", "v2", "    with:\n      mode: changed\n") + (
        "  all:\n    uses: murphy360/standards/.github/workflows/python-lint.yml@v2\n"
    )
    row = fleet.report([node("x", ci=both)], "o", 2, NOW)[0]
    steps = fleet_rehearsal.plan(row)
    assert [s["changed"] for s in steps] == [False]  # one caller checks every file


def test_tools_at_takes_the_tools_as_they_are_at_the_ref(standards, tmp_path):
    tools = fleet_rehearsal.tools_at("v9", standards, tmp_path / "out")
    assert "code_rules: " in (tools / "code_rules.py").read_text()  # not the edit
    assert (tools / "check_standards.py").exists()
    with pytest.raises(RuntimeError, match="cannot read tools/ at `v10`"):
        fleet_rehearsal.tools_at("v10", standards, tmp_path / "out2")


def test_rehearsal_runs_the_tools_at_the_ref_and_says_pass_fail_or_debt(
    standards, scratch
):
    clone = cloner({"murphy360/alpha": ["debt"], "murphy360/bravo": ["debt", "gap"]})
    seen = []
    results = fleet_rehearsal.rehearse(
        rows(), "murphy360", "v9", standards, clone=clone, emit=seen.append
    )
    assert seen == results
    # only the adopters are cloned; charlie calls no workflow that runs a tool
    assert [slug for slug, _ in clone.made] == ["murphy360/alpha", "murphy360/bravo"]
    alpha, bravo, charlie = results
    assert alpha == {
        "name": "alpha",
        "result": "debt",  # it lints in mode changed
        "runs": [
            {
                "tool": "check_standards.py --require-pr-template",
                "cwd": ".",
                "result": "pass",
                "last": "check_standards: OK",
            },
            {
                "tool": "code_rules.py --all --paths src tools",
                "cwd": ".",
                "result": "debt",
                "last": "code_rules: FAILED",
            },
        ],
    }
    assert bravo["result"] == "FAIL"  # mode all: a finding fails its pull requests
    assert bravo["runs"][0]["last"] == "code_rules: FAILED"
    assert charlie == {"name": "charlie", "result": "-", "runs": []}


def test_rehearsal_deletes_its_clones(standards, scratch):
    clone = cloner()
    fleet_rehearsal.rehearse(rows(), "murphy360", "v9", standards, clone=clone)
    assert len(clone.made) == 2
    assert not any(dest.exists() for _, dest in clone.made)
    assert list(scratch.iterdir()) == []


def test_rehearsal_deletes_its_clones_when_it_is_stopped(standards, scratch):
    clone = cloner()

    def run(argv, cwd):
        assert cwd.is_dir()  # the clone is there while the tool runs
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        fleet_rehearsal.rehearse(
            rows(), "murphy360", "v9", standards, clone=clone, run=run
        )
    assert len(clone.made) == 1
    assert list(scratch.iterdir()) == []


def test_a_clone_that_fails_is_an_error_and_the_rest_still_run(standards, scratch):
    clone = cloner(fail=("murphy360/alpha",))
    results = fleet_rehearsal.rehearse(
        rows(), "murphy360", "v9", standards, clone=clone
    )
    assert results[0]["result"] == "error"
    assert results[0]["runs"] == [
        {"tool": "clone", "result": "error", "last": "repository not found"}
    ]
    assert results[1]["result"] == "pass"
    assert list(scratch.iterdir()) == []


def test_rehearsal_prints_each_repository_and_exits_1_on_a_fail(
    standards, scratch, capsys
):
    def which(name):
        return "/usr/bin/" + name

    clone = cloner({"murphy360/bravo": ["debt"]})
    code = fleet_rehearsal.main(
        rows(), "murphy360", "v9", standards, which=which, clone=clone
    )
    out = capsys.readouterr().out.splitlines()
    assert code == 1
    assert out[:6] == [
        "alpha: pass",
        "  pass  check_standards.py --require-pr-template: check_standards: OK",
        "  pass  code_rules.py --all --paths src tools: code_rules: OK",
        "bravo: FAIL",
        "  FAIL  code_rules.py --all --no-format: code_rules: FAILED",
        "charlie: - (it calls no workflow that runs these tools)",
    ]
    assert (
        out[-1] == "3 adopters rehearsed at v9: 1 FAIL, 1 pass, 1 with nothing to run."
    )

    clone = cloner({"murphy360/alpha": ["debt"]})
    code = fleet_rehearsal.main(
        rows(), "murphy360", "v9", standards, which=which, clone=clone, as_json=True
    )
    doc = json.loads(capsys.readouterr().out)
    assert code == 0  # debt in a mode-changed repository is not a failure
    assert doc["ref"] == "v9"
    assert [r["result"] for r in doc["rehearsal"]] == ["debt", "pass", "-"]
    assert any(note.startswith("debt: ") for note in doc["notes"])


def test_rehearsal_says_that_node_lint_is_not_rehearsed():
    row = fleet.report([node("x", ci=caller("node-lint", "v2"))], "o", 2, NOW)
    result = {"name": "x", "result": "-", "runs": []}
    lines = fleet_rehearsal.closing_lines([result], row, "v9")
    assert lines[0] == "1 adopters rehearsed at v9: 1 with nothing to run."
    assert lines[1].startswith("node-lint is not rehearsed (1 repositories call it)")


def test_without_ruff_the_rehearsal_stops_and_names_the_docker_command(
    standards, scratch, capsys
):
    clone = cloner()
    code = fleet_rehearsal.main(
        rows(), "murphy360", "v2.3.0", standards, which=lambda name: None, clone=clone
    )
    captured = capsys.readouterr()
    assert code == 2
    assert clone.made == []  # nothing was cloned
    assert list(scratch.iterdir()) == []
    assert captured.out == ""
    assert "ruff is not on PATH" in captured.err
    assert "docker run --rm" in captured.err
    assert "python tools/fleet.py --run-tools v2.3.0" in captured.err


def test_the_docker_command_installs_the_default_ruff_and_names_no_version():
    hint = fleet_rehearsal.docker_hint("v2")
    assert "python tools/ruff_version.py --install &&" in hint
    assert "ruff==" not in hint  # the default is written once, in pyproject.toml
    readme = (ROOT / "README.md").read_text()
    for line in fleet_rehearsal.docker_hint("<ref>").splitlines()[3:]:
        assert line.strip() in readme  # README documents the same command


def test_fleet_main_hands_the_adopters_to_the_rehearsal(monkeypatch, capsys):
    seen = {}

    def rehearsal(rows, owner, ref, root, *, as_json=False):
        seen.update(rows=rows, owner=owner, ref=ref, root=root, as_json=as_json)
        return 1

    monkeypatch.setattr(fleet.fleet_rehearsal, "main", rehearsal)
    args = ["--owner", "murphy360", "--run-tools", "v9", "--repo", "alpha"]
    assert fleet.main(args, fetch=pages(FLEET), now=NOW) == 1
    assert [r["name"] for r in seen["rows"]] == ["alpha"]
    assert (seen["owner"], seen["ref"], seen["root"]) == ("murphy360", "v9", ROOT)
    assert capsys.readouterr().out == ""  # the rehearsal prints, not the table
