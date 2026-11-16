"""What a newer ruff changed, and what the standard does about it.

ruff 0.16.0 turned on about 400 rules for a config that selects none, where it had
59: the standard pins the old default, so a new ruff changes no project's rules.
``ruff format --check`` now prints diagnostics: the tool reads both styles."""

import importlib.util
import re
import shutil
import subprocess
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
needs_ruff = pytest.mark.skipif(shutil.which("ruff") is None, reason="no ruff")
PIN = ["--config", 'lint.select=["E4", "E7", "E9", "F"]']
# F401, E741, B006 and SIM102 are the standard's; W291, C416 and B008 are not
CODE = (
    "import os\n"
    "import sys\n\n"
    "l = 1\n"
    "x = 1 \n"
    "z = [i for i in range(3)]\n\n\n"
    "def f(a=[], b=None, c=os.getcwd()):\n"
    "    if a:\n"
    "        if b:\n"
    "            return a\n"
    "    return b\n"
)


def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


config = load("code_rules").config
ruff_version = load("ruff_version")


def flags(tmp_path: Path, ruff_toml: str | None) -> list[str]:
    if ruff_toml is not None:
        (tmp_path / "ruff.toml").write_text(ruff_toml)
    (tmp_path / "a.py").write_text(CODE)
    return config.project_rules(tmp_path, {"a.py"})


def test_a_project_without_a_config_gets_ruffs_old_default(tmp_path):
    assert flags(tmp_path, None) == [*config.STANDARD, *config.LEAVE_OUT, *PIN]


def test_a_config_that_selects_its_rules_keeps_them(tmp_path):
    want = [*config.STANDARD, *config.LEAVE_OUT]
    assert flags(tmp_path, '[lint]\nselect = ["F", "B"]\n') == want
    assert flags(tmp_path, "select = []\n") == want  # the old top level


def test_a_config_that_names_b008_keeps_it(tmp_path):
    """B008 is not the standard's, and a project may ask for it by name."""
    assert flags(tmp_path, '[lint]\nselect = ["F", "B008"]\n') == config.STANDARD
    assert flags(tmp_path, '[lint]\nextend-select = ["B008"]\n') == [
        *config.STANDARD,
        *PIN,
        "--extend-select",
        "B008",
    ]


def test_the_configs_extend_select_and_ignore_come_after_the_pin(tmp_path):
    toml = (
        '[lint]\nextend-select = ["C4", "PLR0912"]\n'
        'ignore = ["B006", "C416"]\nextend-ignore = ["RET"]\n'
    )
    assert flags(tmp_path, toml) == [
        *config.STANDARD,
        *config.LEAVE_OUT,
        *PIN,
        "--extend-select",
        "C4,PLR0912",
        "--ignore",
        "C416,RET",  # B006 is the standard's: the config cannot ignore it
    ]


def test_an_ignore_of_a_standard_rule_is_not_repeated(tmp_path):
    toml = '[lint]\nignore = ["E741", "F", "SIM102", "UP", "I001", "E501", "PLR0912"]\n'
    assert flags(tmp_path, toml)[-2:] == ["--ignore", "E501,PLR0912"]


def test_every_config_ruff_reads_counts(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "ruff.toml").write_text('[lint]\nextend-select = ["S"]\n')
    (tmp_path / "sub" / "b.py").write_text("x = 1\n")
    (tmp_path / "ruff.toml").write_text('[lint]\nextend-select = ["C4"]\n')
    got = config.project_rules(tmp_path, {"sub/b.py"})
    assert got[-2:] == ["--extend-select", "C4,S"]
    (tmp_path / "sub" / "ruff.toml").write_text('[lint]\nselect = ["F"]\n')
    got = config.project_rules(tmp_path, {"sub/b.py"})
    assert got == [*config.STANDARD, *config.LEAVE_OUT]


def test_before_python_3_11_the_old_default_is_pinned_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "tomllib", None)
    assert flags(tmp_path, '[lint]\nselect = ["B"]\n') == [
        *config.STANDARD,
        *config.LEAVE_OUT,
        *PIN,
    ]


def codes(tmp_path: Path, rules: list[str]) -> list[str]:
    res = subprocess.run(
        [
            "ruff",
            "check",
            "--no-cache",
            "--exit-zero",
            "--ignore-noqa",
            "--output-format",
            "concise",
            *rules,
            ".",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(line.split()[1] for line in res.stdout.splitlines() if ": " in line)


STANDARD_FOUND = ["B006", "E741", "F401", "SIM102"]


@needs_ruff
@pytest.mark.parametrize(
    ("ruff_toml", "expected"),
    [
        (None, STANDARD_FOUND),
        ('[lint]\nextend-select = ["C4"]\n', sorted([*STANDARD_FOUND, "C416"])),
        ('[lint]\nextend-select = ["B008"]\n', sorted([*STANDARD_FOUND, "B008"])),
        (
            '[lint]\nextend-select = ["W"]\nignore = ["B006", "E741"]\n',
            sorted([*STANDARD_FOUND, "W291"]),
        ),
        ('[lint]\nselect = ["W"]\n', sorted([*STANDARD_FOUND, "W291"])),
        ('[lint]\nselect = ["B"]\n', STANDARD_FOUND),  # B, but not B008
    ],
)
def test_ruff_finds_what_the_project_and_the_standard_ask_for(
    tmp_path, ruff_toml, expected
):
    assert codes(tmp_path, flags(tmp_path, ruff_toml)) == expected


# what each release prints for bad.py (does not parse), ugly.py (needs formatting)
# and ok.py, as ``ruff format --check`` printed it
OLD_STDOUT = (
    "Would reformat: ugly.py\n1 file would be reformatted, 1 file already formatted\n"
)
OLD_STDERR = "error: Failed to parse bad.py:2:1: unexpected EOF while parsing\n"
NEW_STDOUT = (
    "invalid-syntax: unexpected EOF while parsing\n --> bad.py:1:7\n  |\n1 | x = (\n"
    "  |      ^\n\nunformatted: File would be reformatted\n --> sub/ugly.py:1:2\n"
    "  |\n  - x=1\n1 + x = 1\n  |\n\n1 file would be reformatted, 1 file already "
    "formatted\n"
)


def test_the_format_check_reads_the_older_output():
    assert ruff_version.format_check(OLD_STDOUT, OLD_STDERR) == (
        ["bad.py"],
        ["ugly.py"],
    )


def test_the_format_check_reads_the_newer_output():
    assert ruff_version.format_check(NEW_STDOUT, "") == (["bad.py"], ["sub/ugly.py"])


def test_a_clean_check_names_no_file():
    assert ruff_version.format_check("2 files already formatted\n", "") == ([], [])


@pytest.mark.parametrize(
    "config", [".pre-commit-config.yaml", "templates/pre-commit-config.yaml"]
)
def test_no_pre_commit_hook_lints_with_ruffs_own_defaults(config):
    """A plain `ruff` hook lints with ruff's defaults: the code rules hook lints."""
    text = (TOOLS.parent / config).read_text()
    plain = re.search(r"^\s*- id: ruff\s*$", text, re.M)
    assert not plain, f"{config}: a plain `ruff` hook, ruff's ~400 default rules"
