"""What a newer ruff changed, and what the standard does about it.

ruff 0.16.0 turned on about 400 rules for a config that selects none, where it had
59: the standard pins the old default, so a new ruff changes no project's rules.
``ruff format --check`` now prints diagnostics: the tool reads both styles."""

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
needs_ruff = pytest.mark.skipif(shutil.which("ruff") is None, reason="no ruff")
PIN = ["--config", 'lint.select=["E4", "E7", "E9", "F"]']
# F401, E741 and W291 are the standard's; B006, C416 and SIM102 are not
CODE = (
    "import os\n"
    "l = 1\n"
    "x = 1 \n"
    "z = [i for i in range(3)]\n\n\n"
    "def f(a=[], b=None):\n"
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
    assert flags(tmp_path, None) == [*config.STANDARD, *PIN]


def test_a_config_that_selects_its_rules_keeps_them(tmp_path):
    assert flags(tmp_path, '[lint]\nselect = ["F", "B"]\n') is config.STANDARD
    assert flags(tmp_path, "select = []\n") is config.STANDARD  # the old top level


def test_the_configs_extend_select_and_ignore_come_after_the_pin(tmp_path):
    toml = (
        '[lint]\nextend-select = ["B", "C4"]\n'
        'ignore = ["B006"]\nextend-ignore = ["C4"]\n'
    )
    assert flags(tmp_path, toml) == [
        *config.STANDARD,
        *PIN,
        "--extend-select",
        "B,C4",
        "--ignore",
        "B006,C4",
    ]


def test_an_ignore_of_a_standard_rule_is_not_repeated(tmp_path):
    toml = '[lint]\nignore = ["E741", "F", "PLR0912", "SIM102"]\n'
    assert flags(tmp_path, toml)[-2:] == ["--ignore", "SIM102"]


def test_every_config_ruff_reads_counts(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "ruff.toml").write_text('[lint]\nextend-select = ["SIM"]\n')
    (tmp_path / "sub" / "b.py").write_text("x = 1\n")
    (tmp_path / "ruff.toml").write_text('[lint]\nextend-select = ["B"]\n')
    got = config.project_rules(tmp_path, {"sub/b.py"})
    assert got[-2:] == ["--extend-select", "B,SIM"]
    (tmp_path / "sub" / "ruff.toml").write_text('[lint]\nselect = ["F"]\n')
    assert config.project_rules(tmp_path, {"sub/b.py"}) is config.STANDARD


def test_before_python_3_11_the_old_default_is_pinned_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "tomllib", None)
    assert flags(tmp_path, '[lint]\nselect = ["B"]\n') == [*config.STANDARD, *PIN]


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


@needs_ruff
@pytest.mark.parametrize(
    ("ruff_toml", "expected"),
    [
        (None, ["E741", "F401", "W291"]),
        (
            '[lint]\nextend-select = ["B", "C4"]\n',
            ["B006", "C416", "E741", "F401", "W291"],
        ),
        (
            '[lint]\nextend-select = ["B"]\nignore = ["B006", "E741"]\n',
            ["E741", "F401", "W291"],
        ),
        ('[lint]\nselect = ["SIM"]\n', ["E741", "F401", "SIM102", "W291"]),
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
