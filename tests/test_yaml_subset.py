"""The YAML subset ``tools/glossary.py`` reads terms files with: ``tools/yaml_subset.py``.

What it reads it reads as YAML does; what it does not read is an error with its line.
"""

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
ys = importlib.import_module("yaml_subset")


def test_a_list_of_records_with_every_kind_of_value():
    text = """\
---
# a comment
- term: Waypoint   # a comment after a value
  definition: A point the vehicle steers to.
  synonyms: [WP, 'turn point', "mark", ]
  see:
    - Route
- term: Route
  definition: >
    The waypoints of one mission,
    in order.

    A second paragraph.
  synonyms:
    operators: [track]
    navy:
    - lane
- term: C#
  definition: |
    one line
      an indented line
    # a hash in a block is text
  note: it's a plain value with an apostrophe
"""
    assert ys.load(text) == [
        {
            "term": "Waypoint",
            "definition": "A point the vehicle steers to.",
            "synonyms": ["WP", "turn point", "mark"],
            "see": ["Route"],
        },
        {
            "term": "Route",
            "definition": "The waypoints of one mission, in order.\nA second paragraph.\n",
            "synonyms": {"operators": ["track"], "navy": ["lane"]},
        },
        {
            "term": "C#",
            "definition": "one line\n  an indented line\n# a hash in a block is text\n",
            "note": "it's a plain value with an apostrophe",
        },
    ]


def test_each_item_keeps_its_line():
    data = ys.load("# head\n- a: 1\n\n- b: 2\n  c: 3\n- d\n")
    assert isinstance(data, ys.Items)
    assert data.lines == [2, 4, 6]


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("a:\n  over two\n  lines\n", {"a": "over two lines"}),
        ("a: one\n  two\n\n  three\n", {"a": "one two\nthree"}),
        ("a: >-\n  no line break\n", {"a": "no line break"}),
        ("a: |-\n  x\n  y\n", {"a": "x\ny"}),
        ("a:\n", {"a": None}),
        ("a: []\n", {"a": []}),
        ("a: yes\nb: 12\nc: null\n", {"a": "yes", "b": "12", "c": "null"}),
        ("a: 'it''s'\n", {"a": "it's"}),
        ('a: "tab\\there \\u00e9"\n', {"a": "tab\there \u00e9"}),
        ("a: see http://x.org/a#b\n", {"a": "see http://x.org/a#b"}),
        ("- - a\n  - b\n- c: d\n  e: f\n", [["a", "b"], {"c": "d", "e": "f"}]),
        ("-\n  a: b\n", [{"a": "b"}]),
        ("a:\n  b:\n    c: d\n  e: f\n", {"a": {"b": {"c": "d"}, "e": "f"}}),
        ("", None),
    ],
)
def test_values_read_as_yaml_reads_them(text, value):
    assert ys.load(text) == value


@pytest.mark.parametrize(
    ("text", "line", "words"),
    [
        ("a: b\na: c\n", 2, "`a` is given twice"),
        ("a: b: c\n", 1, "holds `: `"),
        ("a: &x b\n", 1, "an anchor"),
        ("a: *x\n", 1, "an alias"),
        ("a: !tag b\n", 1, "a tag"),
        ("a: {b: c}\n", 1, "{mapping}"),
        ("a: 'x\n  y'\n", 1, "closes on its own line"),
        ("a: [b,\n  c]\n", 1, "closes on its own line"),
        ("a: [b, [c]]\n", 1, "plain or quoted values"),
        ("a: [b, , c]\n", 1, "plain or quoted values"),
        ("a: |+\n  x\n", 1, "a block takes"),
        ("a: >\n  x\n    y\n", 1, "use |"),
        ("\ta: b\n", 1, "a tab"),
        ("a:\n\t- b\n", 2, "a tab"),
        ("a: b\n  - c\n", 2, "starts with `-`"),
        ("a: b # c\n  d\n", 2, "indentation"),
        ("- a: b\n c: d\n", 2, "indentation"),
        ("  a: b\n", 1, "first column"),
        ("a: b\n---\nc: d\n", 2, "expected `key: value`"),
        ('a: "\\q"\n', 1, "single quotes"),
        ("a: 'x' y\n", 1, "after the value"),
        ("a: `x`\n", 1, "in quotes"),
        ("a: |\n    x\n  y\n", 3, "less indented"),
    ],
)
def test_what_it_does_not_read_is_an_error_with_its_line(text, line, words):
    with pytest.raises(ys.YamlError) as caught:
        ys.load(text)
    assert caught.value.line == line
    assert words in caught.value.message
    assert str(caught.value).startswith(f"line {line}: ")
