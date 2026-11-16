"""The layering contracts: ``tools/layering.py`` on scratch projects.

Each project is a git checkout with its files added, so that the tool reads the
tracked files as it does in CI. No network.
"""

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
layering = importlib.import_module("layering")

GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
CONFIG = """\
[importlinter]
root_packages =
    shop

[importlinter:contract:domain]
name = The domain imports no web module
type = forbidden
source_modules =
    shop.domain
forbidden_modules =
    shop.web
"""
PACKAGE = {
    "src/shop/__init__.py": "",
    "src/shop/web/__init__.py": "",
    "src/shop/web/views.py": "VIEWS = []\n",
    "src/shop/domain/__init__.py": "",
    "src/shop/domain/cart.py": "from shop.domain import prices\n",
    "src/shop/domain/prices.py": "PRICE = 1\n",
}


def project(tmp_path: Path, files: dict[str, str], config: str | None = CONFIG):
    """A checkout with ``files`` tracked and the contracts in ``.importlinter``."""
    root = tmp_path / "project"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    if config is not None:
        (root / ".importlinter").write_text(config)
    env = {**os.environ, **GIT_ENV}
    for args in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", *args], cwd=root, env=env, check=True)
    return root


def run(root: Path, monkeypatch, capsys, *argv: str) -> tuple[int, list[str]]:
    monkeypatch.chdir(root)
    code = layering.main(list(argv))
    return code, capsys.readouterr().out.splitlines()


def with_ignores(*lines: str) -> str:
    return CONFIG + "ignore_imports =\n" + "".join(f"    {x}\n" for x in lines)


# --- the contracts are kept, or broken, and each finding says what to do ---


def test_no_config_passes_and_says_so(tmp_path, monkeypatch, capsys):
    root = project(tmp_path, PACKAGE, config=None)
    code, out = run(root, monkeypatch, capsys)
    assert code == 0
    assert out == ["layering: no .importlinter here, so there is no contract to check"]


def test_a_kept_contract_passes(tmp_path, monkeypatch, capsys):
    code, out = run(project(tmp_path, PACKAGE), monkeypatch, capsys)
    assert code == 0, out
    assert out == [
        "layering: 6 modules read, in shop",
        "KEPT  The domain imports no web module",
        "layering: OK, 1 of 1 contracts kept",
    ]


def test_a_forbidden_import_breaks_the_contract_at_its_line(
    tmp_path, monkeypatch, capsys
):
    files = {
        **PACKAGE,
        "src/shop/domain/cart.py": "X = 1\nfrom shop.web import views\n",
    }
    code, out = run(project(tmp_path, files), monkeypatch, capsys)
    assert code == 1
    assert out[1] == "BROKEN  The domain imports no web module"
    assert out[2] == (
        "  src/shop/domain/cart.py:2: shop.domain.cart imports shop.web.views, which "
        "the contract forbids (move the code, or list the import in ignore_imports "
        "with its ticket)"
    )
    assert out[-1] == "layering: FAILED, 1 of 1 contracts broken: domain"


@pytest.mark.parametrize(
    ("source", "imported"),
    [
        ("from ..web import views\n", "shop.web.views"),  # relative
        ("from .. import web\n", "shop.web"),
        ("def later():\n    import shop.web.views\n", "shop.web.views"),  # lazy
        ("from shop.web.views import VIEWS\n", "shop.web.views"),  # a name in it
        ("import shop.web\n", "shop.web"),
    ],
)
def test_every_kind_of_import_counts(tmp_path, monkeypatch, capsys, source, imported):
    files = {**PACKAGE, "src/shop/domain/cart.py": source}
    code, out = run(project(tmp_path, files), monkeypatch, capsys)
    assert code == 1
    assert f"shop.domain.cart imports {imported}, which" in out[2]


def test_tests_and_untracked_files_are_not_read(tmp_path, monkeypatch, capsys):
    files = {**PACKAGE, "src/shop/domain/tests/test_cart.py": "import shop.web\n"}
    root = project(tmp_path, files)
    (root / "src/shop/domain/scratch.py").write_text("import shop.web\n")
    code, out = run(root, monkeypatch, capsys)
    assert code == 0, out


# --- the allowed imports: each names its ticket, and the list only shrinks ---

BREACH = {**PACKAGE, "src/shop/domain/cart.py": "import shop.web.views\n"}


@pytest.mark.parametrize(
    "comment", ["# #12", "# owner/repo#12 until the move", "# https://x/o/r/issues/12"]
)
def test_an_allowed_import_with_its_ticket_keeps_the_contract(
    tmp_path, monkeypatch, capsys, comment
):
    config = with_ignores(f"shop.domain.cart -> shop.web.views  {comment}")
    code, out = run(project(tmp_path, BREACH, config), monkeypatch, capsys)
    assert code == 0, out
    assert out[1] == "KEPT  The domain imports no web module, 1 allowed for now"


@pytest.mark.parametrize("comment", ["", "# later", "# 12"])
def test_an_allowed_import_with_no_ticket_fails(tmp_path, monkeypatch, capsys, comment):
    config = with_ignores(f"shop.domain.cart -> shop.web.views  {comment}")
    code, out = run(project(tmp_path, BREACH, config), monkeypatch, capsys)
    assert code == 1
    assert out[2] == (
        "  allowed with no ticket: shop.domain.cart -> shop.web.views (add "
        "`# #<ticket>` to the line: the ticket that removes the import)"
    )


def test_an_allowed_import_that_no_longer_happens_fails(tmp_path, monkeypatch, capsys):
    config = with_ignores("shop.domain.cart -> shop.web.views  # #12")
    code, out = run(project(tmp_path, PACKAGE, config), monkeypatch, capsys)
    assert code == 1
    assert out[2] == (
        "  allowed but no longer imported: shop.domain.cart -> shop.web.views "
        "(remove the line from .importlinter)"
    )


def test_an_exempt_module_needs_a_reason_and_a_forbidden_import(
    tmp_path, monkeypatch, capsys
):
    config = CONFIG + (
        "exempt_modules =\n"
        "    shop.domain.cart  # it renders the cart, for now\n"
        "    shop.domain\n"
    )
    code, out = run(project(tmp_path, BREACH, config), monkeypatch, capsys)
    assert code == 1
    # the first exempt line that matches is used, so `shop.domain` is not
    assert out[1:] == [
        "BROKEN  The domain imports no web module, exempt: shop.domain.cart",
        "  exempt but imports nothing the contract forbids: shop.domain (remove the "
        "line from exempt_modules)",
        "  exempt with no reason: shop.domain (add `# <why>` to the line)",
        "layering: FAILED, 1 of 1 contracts broken: domain",
    ]


# --- where the packages are: a src layout, two levels deep, a namespace ---


def test_a_package_two_levels_deep_and_a_namespace_of_scripts(
    tmp_path, monkeypatch, capsys
):
    """The layout of a ROS workspace: src/<pkg>/<pkg>/, beside a setup.py, and
    scripts in tools/ with no __init__.py, which a contract names by source."""
    files = {
        "src/core/setup.py": "import setuptools\n",
        "src/core/core/__init__.py": "",
        "src/core/core/node.py": "",
        "src/core/core/sub/tools/__init__.py": "",  # a subpackage of that name
        "src/web/web/__init__.py": "",
        "tools/report.py": "from core import node\nimport web\n",
    }
    config = """\
[importlinter]
root_packages =
    core
    web

[importlinter:contract:tools]
name = The tools import no web module
type = forbidden
source_modules =
    tools
forbidden_modules =
    web
"""
    code, out = run(project(tmp_path, files, config), monkeypatch, capsys)
    assert code == 1
    assert out[0] == "layering: 5 modules read, in core, web, tools"
    assert out[2].startswith("  tools/report.py:2: tools.report imports web, which")


def test_the_config_of_a_subdirectory_reads_the_packages_there(
    tmp_path, monkeypatch, capsys
):
    files = {f"api/{rel}": text for rel, text in BREACH.items()}
    files["web/shop/__init__.py"] = ""  # another `shop`, outside api/
    root = project(tmp_path, files, config=None)
    (root / "api" / ".importlinter").write_text(CONFIG)
    code, out = run(root, monkeypatch, capsys, "--config", "api/.importlinter")
    assert code == 1
    assert out[2].startswith("  src/shop/domain/cart.py:1: shop.domain.cart imports")
    monkeypatch.chdir(root / "api")  # the action: in its working-directory
    assert layering.main([]) == 1


# --- a file that cannot be read as written is an error, not a pass ---


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (("type = forbidden", "type = layers"), "type layers: only forbidden"),
        (("    shop.web", "    shop.*"), "`shop.*`: wildcards are not supported"),
        (("forbidden_modules =\n    shop.web\n", ""), "needs source_modules and"),
        (("[importlinter]", "[importlinter"), "File contains no section headers"),
    ],
)
def test_a_contract_that_cannot_be_read_fails_with_exit_2(
    tmp_path, monkeypatch, capsys, change, said
):
    config = CONFIG.replace(*change)
    code, out = run(project(tmp_path, PACKAGE, config), monkeypatch, capsys)
    assert code == 2
    assert said in out[0]
    assert out[-1] == "layering: FAILED, the contracts could not be read"


def test_an_ignore_line_that_is_no_import_and_a_package_in_two_places(
    tmp_path, monkeypatch, capsys
):
    config = with_ignores("shop.domain.cart shop.web  # #1")
    code, out = run(project(tmp_path, PACKAGE, config), monkeypatch, capsys)
    assert code == 2 and "is not `importer -> imported`" in out[0]
    files = {**PACKAGE, "lib/shop/__init__.py": ""}
    code, out = run(project(tmp_path / "two", files), monkeypatch, capsys)
    assert code == 2
    assert (
        "package `shop` is in more than one place: ['lib/shop', 'src/shop']" in out[0]
    )
    assert layering.main(["--config", "missing.ini"]) == 2


def test_a_package_that_is_not_here_is_said_and_not_read(tmp_path, monkeypatch, capsys):
    config = CONFIG.replace("    shop.domain\n", "    shop.domain\n    gone\n", 1)
    code, out = run(project(tmp_path, PACKAGE, config), monkeypatch, capsys)
    assert code == 0
    assert out[:2] == [
        "layering: 6 modules read, in shop",
        "layering: package gone is not in this checkout, so it was not read",
    ]


def test_other_sections_are_skipped_and_a_file_that_does_not_parse_is_said(
    tmp_path, monkeypatch, capsys
):
    config = CONFIG + "\n[project:rule:one]\nname = a note for people\n"
    files = {**PACKAGE, "src/shop/web/old.py": "print 'hi'\n"}
    code, out = run(project(tmp_path, files, config), monkeypatch, capsys)
    assert code == 0
    assert out[1] == "layering: src/shop/web/old.py does not parse, so it was not read"
