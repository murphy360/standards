"""The standards' tools on scratch repositories: the ratchet and the standards check."""

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rules, standards = load("code_rules"), load("check_standards")


def repo(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def test_the_ratchet_fails_new_grown_and_unlowered(tmp_path):
    out = rules.compare(
        "ruff", {"a::E501": 2, "b::F401": 1, "c::E711": 1}, {"a::E501": 3, "b::F401": 1}
    )
    assert any("a::E501: 2, below its ceiling 3" in o for o in out)
    assert any("c::E711: 1 (new" in o for o in out)
    assert not any(o.startswith("ruff: b::") for o in out)
    first = rules.lowered({"ruff": {"x": 2}, "lines": {}}, {}, True)
    assert first["ruff"] == {"x": 2}
    later = rules.lowered({"ruff": {"x": 1, "new": 1}, "lines": {}}, first, False)
    assert later["ruff"] == {"x": 1}  # lowered; the new finding not taken in


def test_file_sizes_and_test_files(tmp_path):
    root = repo(
        tmp_path,
        {
            "src/a.py": "x = 1\n" * 801,
            "tests/test_a.py": "x = 1\n" * 900,
            "b.sh": "echo\n",
        },
    )
    files = rules.tracked_sources(root)
    assert rules.file_sizes(files, root, 800, 1200) == {"src/a.py": 801}


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not installed")
def test_code_rules_end_to_end(tmp_path):
    root = repo(tmp_path, {"m.py": "import os\n"})
    assert rules.main(["--root", str(root), "--update"]) == 0
    base = json.loads((root / "code_rules_baseline.json").read_text())
    assert base["ruff"] == {"m.py::F401": 1}
    assert rules.main(["--root", str(root)]) == 0
    (root / "m.py").write_text("import os\nimport sys\n")
    assert rules.main(["--root", str(root)]) == 1  # grew


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
