"""This repository's CI runs every workflow and every action it ships.

ci.yml calls each reusable workflow on this repository or on a small project in
``tests/fixtures``, uses each action under ``actions/`` by its local path, and
``result`` needs every job. These tests read the files only: they keep a new
workflow or action from being added without its job, and keep the fixtures what the
workflows are meant to meet.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
ACTIONS = ROOT / "actions"
FIXTURES = ROOT / "tests" / "fixtures"
NODE = FIXTURES / "node-project"
DOCKER = FIXTURES / "docker-project"
JOB_RE = re.compile(r"^  ([\w-]+):\s*$")
USES_RE = re.compile(r"^\s+uses:\s*\./\.github/workflows/([\w.-]+)")
ACTION_RE = re.compile(r"^\s+(?:- )?uses:\s*\./actions/([\w-]+)\s*(#.*)?$")
NEEDS_RE = re.compile(r"^\s+needs:\s*\[(.*)\]")
FIXTURE_PATH_RE = re.compile(r"tests/fixtures/[\w./-]+")


def ci_jobs(text: str) -> dict[str, list[str]]:
    """Each job of a workflow with its lines. A small parser: no YAML library."""
    jobs: dict[str, list[str]] = {}
    name, inside = None, False
    for line in text.splitlines():
        if line.rstrip() == "jobs:":
            inside = True
        elif inside and JOB_RE.match(line):
            name = JOB_RE.match(line).group(1)
            jobs[name] = []
        elif name:
            jobs[name].append(line)
    return jobs


def called(jobs: dict[str, list[str]]) -> set[str]:
    """The local reusable workflows the jobs call."""
    hits = (USES_RE.match(line) for lines in jobs.values() for line in lines)
    return {hit.group(1) for hit in hits if hit}


def used(jobs: dict[str, list[str]]) -> set[str]:
    """The local actions the steps of the jobs use."""
    hits = (ACTION_RE.match(line) for lines in jobs.values() for line in lines)
    return {hit.group(1) for hit in hits if hit}


def not_needed(jobs: dict[str, list[str]]) -> set[str]:
    """The jobs ``result`` does not wait for."""
    needs: set[str] = set()
    for line in jobs.get("result", []):
        hit = NEEDS_RE.match(line)
        if hit:
            needs |= {n.strip() for n in hit.group(1).split(",") if n.strip()}
    return set(jobs) - needs - {"result"}


def reusable() -> set[str]:
    return {
        p.name for p in WORKFLOWS.glob("*.yml") if "workflow_call:" in p.read_text()
    }


def actions() -> set[str]:
    """The actions this repository ships: a directory under actions/ each."""
    return {p.parent.name for p in ACTIONS.glob("*/action.y*ml")}


def this_ci() -> dict[str, list[str]]:
    return ci_jobs((WORKFLOWS / "ci.yml").read_text())


SAMPLE = """\
name: ci
jobs:
  lint:
    uses: ./.github/workflows/python-lint.yml
    with:
      mode: changed
  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: ./actions/checks
        with:
          checks: shell-lint
      - name: Later
        uses: ./actions/take-ownership  # a comment
      - uses: someone/actions/other@v1
  result:
    needs: [lint]
    runs-on: ubuntu-latest
"""


def test_the_parser_finds_the_jobs_the_calls_and_a_job_result_forgot():
    jobs = ci_jobs(SAMPLE)
    assert list(jobs) == ["lint", "tests", "result"]
    assert called(jobs) == {"python-lint.yml"}
    assert used(jobs) == {"checks", "take-ownership"}
    assert not_needed(jobs) == {"tests"}


def test_ci_calls_every_reusable_workflow_this_repository_ships():
    shipped = reusable()
    assert {"node-lint.yml", "test-docker.yml", "image.yml"} <= shipped
    missing = shipped - called(this_ci())
    assert not missing, (
        f"ci.yml has no job for {sorted(missing)}: add a fixture and one"
    )


def test_ci_uses_every_action_this_repository_ships():
    """An action is tried by local path, so a pull request proves its own action.
    The job that uses it is one ``result`` needs, as every job is (below)."""
    shipped = actions()
    assert {"checks", "take-ownership"} <= shipped
    missing = shipped - used(this_ci())
    assert not missing, (
        f"no step of ci.yml uses {sorted(missing)}: add `uses: ./actions/<name>`"
    )
    stray = [p for p in ACTIONS.rglob("action.y*ml") if p.parent.parent != ACTIONS]
    assert not stray, "an action is a directory directly under actions/"


def test_result_needs_every_job():
    left_out = not_needed(this_ci())
    assert not left_out, f"result does not need {sorted(left_out)}"


def test_every_fixture_path_ci_names_exists():
    paths = set(FIXTURE_PATH_RE.findall((WORKFLOWS / "ci.yml").read_text()))
    assert paths, "ci.yml names no fixture"
    assert not [p for p in sorted(paths) if not (ROOT / p).exists()]


def test_the_node_fixture_has_the_template_eslint_config():
    template = (ROOT / "templates" / "eslint.config.mjs").read_text()
    assert (NODE / "eslint.config.mjs").read_text() == template


def test_the_node_fixture_is_the_project_the_readme_describes():
    package = json.loads((NODE / "package.json").read_text())
    lock = json.loads((NODE / "package-lock.json").read_text())
    dev = package["devDependencies"]
    assert {"eslint", "@eslint/js", "typescript-eslint", "prettier", "typescript"} <= (
        set(dev)
    )
    assert lock["packages"][""]["devDependencies"] == dev
    assert list(NODE.rglob("*.ts")), "no TypeScript file for ESLint to check"
    # An SVG once stopped prettier, and with it the whole run.
    kept = [p for p in NODE.rglob("*.svg") if "node_modules" not in p.parts]
    assert kept, "no SVG file for prettier to pass over"


def test_the_docker_fixture_has_an_image_and_a_test_for_it():
    dockerfile = (DOCKER / "Dockerfile").read_text()
    assert "COPY test.sh /opt/fixture/test.sh" in dockerfile
    test = (DOCKER / "test.sh").read_text()
    assert "/opt/fixture/test.sh" in test
    assert "cmp -s" in test
