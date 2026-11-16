#!/usr/bin/env python3
"""The glossary: one terms file, a page made from it, and the terms the docs use
without defining.

    python3 glossary.py                    # docs/terms.yaml here; exit 1 when docs/GLOSSARY.md is stale
    python3 glossary.py --write            # write docs/GLOSSARY.md from docs/terms.yaml
    python3 glossary.py --undefined        # also list the bold terms the glossary does not define
    python3 glossary.py --undefined --strict              # and fail on them
    python3 glossary.py --terms api/docs/terms.yaml       # another terms file; the page sits beside it

``docs/terms.yaml`` is a list of terms::

    - term: Waypoint
      definition: A point the vehicle steers to.
      synonyms: [WP, mark]              # optional: other names for it
      see: [Route]                      # optional: related terms
    - term: Route
      definition: >
        The waypoints of one mission, in the order the vehicle visits them.
      synonyms:                         # or other names by audience or domain
        operators: [track]
        navy: [lane]

``term`` and ``definition`` are required; ``definition`` is Markdown. ``synonyms``
is a list of names, or a mapping of an audience or a domain to a list of names.
``see`` lists other terms of the file. The file is read by ``yaml_subset.py``: a
strict subset of YAML, with the standard library.

* **The check** (the default): ``GLOSSARY.md`` beside the terms file is what the
  terms give, or the run fails and prints the difference and the command that
  rewrites it. A terms file that cannot be read fails (exit 2), and so does one
  with a problem in it (exit 1): an unknown key, a missing definition, a name
  defined twice (a term or a synonym, compared without case), a synonym that is
  its own term, or a ``see`` that names no term.
* **The page** (``--write``): the terms A to Z, each under a heading of its own,
  so each has an anchor; its other names, and links to the terms it sees; then
  every other name, A to Z, linked to its term. A comment at the top says the
  page is made, and how to make it again.
* **Undefined terms** (``--undefined``): bold text (``**Term**``) in the tracked
  Markdown files under the directory this runs in, that no term or synonym
  names. Headings, code blocks, code spans and the page itself are skipped. Bold
  text counts as a term when it is one to four words, holds a capital or a
  digit, and does not end in punctuation: ``**Note:**`` and ``**Every check
  runs.**`` are lead-ins and ``**never**`` is emphasis. A leading article goes
  (``**A contract**`` is ``contract``), and a plural matches its term. It
  informs, and fails only with ``--strict``. Capitalised words that are not
  bold are not looked at: every sentence starts with one.

No terms file: one line says there is no glossary to check, and the run passes.
The ``glossary`` check of the ``checks`` action runs this file in its
``working-directory``; the ``glossary`` pre-commit hook runs it in the root of the
checkout.

Standard library only.
"""

from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
from pathlib import Path

from code_rules import tracked_files
from yaml_subset import Items, YamlError, load

TERMS = "docs/terms.yaml"
PAGE = "GLOSSARY.md"
FIELDS = ("term", "definition", "synonyms", "see")
OK, FAILED, UNREADABLE = 0, 1, 2
HEADER = (
    "<!-- Made from terms.yaml beside this file by tools/glossary.py of "
    "murphy360/standards. Do not edit it:\n"
    "     change terms.yaml, then run `python3 <standards>/tools/glossary.py "
    "--write`. -->"
)
INTRO = (
    "The terms this project uses, A to Z. Each is defined once, in `terms.yaml` "
    "beside this page."
)
FIX = (
    "Fix: run `python3 <standards>/tools/glossary.py --write{args}` and commit the "
    "page (<standards> is murphy360/standards, checked out at the version this "
    "project's CI calls)"
)
DIFF_LINES = 40
MAX_WORDS = 4
MD_SPECIAL_RE = re.compile(r"([\\`*_\[\]<>|])")
BOLD_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
CODE_SPAN_RE = re.compile(r"(`+).*?\1")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
HEADING_RE = re.compile(r"^ {0,3}#{1,6}(\s|$)")
ARTICLE_RE = re.compile(r"^(?:a|an|the)\s+(?=\S)", re.IGNORECASE)


def key(name: str) -> str:
    """A name as names are compared: without case, one space between words."""
    return " ".join(name.split()).casefold()


# -- the terms file ----------------------------------------------------------------------


def one_line(value, what: str) -> tuple[str, str | None]:
    """A name: text on one line. The text and a problem, or None."""
    if not isinstance(value, str) or not value.strip():
        return "", f"{what} is a name on one line"
    if "\n" in value.strip():
        return "", f"{what} `{value.strip()}` is more than one line"
    return " ".join(value.split()), None


def names_of(value, what: str) -> tuple[list[str], list[str]]:
    """A list of names, and the problems in it."""
    if not isinstance(value, list):
        return [], [f"{what} is a list of names: `[a, b]`"]
    out, problems = [], []
    for item in value:
        name, problem = one_line(item, f"each of {what}")
        out += [name] if name else []
        problems += [problem] if problem else []
    return out, problems


def synonyms_of(value) -> tuple[list[tuple[str, str]], list[str]]:
    """``[(audience, name)]``: the audience is "" for a plain list."""
    if value is None:
        return [], []
    if isinstance(value, dict):
        out, problems = [], []
        for audience, names in value.items():
            got, bad = names_of(names, f"synonyms for {audience}")
            out += [(audience, name) for name in got]
            problems += bad
        return out, problems
    got, problems = names_of(value, "synonyms")
    return [("", name) for name in got], problems


def entry(item, line: int) -> tuple[dict, list[tuple[int, str]]]:
    """One term as {term, definition, synonyms, see, line}, and its problems."""
    if not isinstance(item, dict):
        return {}, [
            (line, "a term is `term:` and `definition:`, on lines of their own")
        ]
    fields = ", ".join(FIELDS)
    problems = [
        f"`{k}` is not a key of a term ({fields})" for k in item if k not in FIELDS
    ]
    term, problem = one_line(item.get("term"), "`term`")
    problems += [problem] if problem else []
    definition = item.get("definition")
    if not isinstance(definition, str) or not definition.strip():
        problems.append(f"`{term or '?'}` has no `definition`")
        definition = ""
    if term and not re.search(r"[^\W_]", term):
        problems.append(f"`{term}`: a term holds a letter or a digit")
    synonyms, bad = synonyms_of(item.get("synonyms"))
    see, bad_see = ([], []) if item.get("see") is None else names_of(item["see"], "see")
    problems += bad + bad_see
    out = {"term": term, "definition": definition.strip(), "line": line}
    return {**out, "synonyms": synonyms, "see": see}, [(line, p) for p in problems]


def describe(kind: str, term: str) -> str:
    return "a term" if kind == "term" else f"a synonym of `{term}`"


def owners(entries: list[dict]) -> tuple[dict[str, tuple[dict, str]], list]:
    """Which term each name belongs to (by ``key``), and the names defined twice."""
    owner: dict[str, tuple[dict, str]] = {}
    problems = []
    for e in entries:
        names = [(e["term"], "term")] + [(s, "synonym") for _, s in e["synonyms"]]
        for name, kind in names:
            k = key(name)
            if kind == "synonym" and k == key(e["term"]):
                problems.append((e["line"], f"`{name}` is a synonym of itself"))
                continue
            if k in owner and owner[k][0] is not e:
                first, first_kind = owner[k]
                problems.append(
                    (
                        e["line"],
                        f"`{name}` is defined twice: {describe(kind, e['term'])} "
                        f"here, and {describe(first_kind, first['term'])} on line "
                        f"{first['line']}",
                    )
                )
                continue
            owner.setdefault(k, (e, kind))
    return owner, problems


def see_problems(entries: list[dict], owner: dict) -> list[tuple[int, str]]:
    problems = []
    for e in entries:
        for name in e["see"]:
            found = owner.get(key(name))
            if found is None:
                problems.append(
                    (e["line"], f"`{e['term']}` sees `{name}`: no such term")
                )
            elif found[1] == "synonym":
                problems.append(
                    (
                        e["line"],
                        f"`{e['term']}` sees `{name}`, a synonym: name its term, "
                        f"`{found[0]['term']}`",
                    )
                )
            elif found[0] is e:
                problems.append((e["line"], f"`{e['term']}` sees itself"))
    return problems


def read_terms(data) -> tuple[list[dict], dict, list[tuple[int, str]]]:
    """The terms, the owner of each name, and every problem, with its line."""
    if not isinstance(data, list) or not data:
        shape = "a list of terms, each `- term: ...` with its `definition: ...`"
        return [], {}, [(1, f"the file is {shape}")]
    lines = data.lines if isinstance(data, Items) else [0] * len(data)
    entries, problems = [], []
    for item, line in zip(data, lines, strict=True):
        got, bad = entry(item, line)
        problems += bad
        entries += [got] if got.get("term") else []
    owner, twice = owners(entries)
    return entries, owner, problems + twice + see_problems(entries, owner)


# -- the page ----------------------------------------------------------------------------


def md(text: str) -> str:
    """A name as Markdown text: its special characters escaped."""
    return MD_SPECIAL_RE.sub(r"\\\1", text)


def slug(text: str) -> str:
    """GitHub's anchor of a heading: lower case, punctuation gone, spaces hyphens."""
    return re.sub(r"[^\w\- ]", "", text.strip().lower()).replace(" ", "-")


class Anchors:
    """The anchors of a page's headings, in order: a second of one name gets
    ``-1``, as GitHub gives it (github-slugger)."""

    def __init__(self):
        self.seen: dict[str, int] = {}

    def add(self, text: str) -> str:
        base = result = slug(text)
        while result in self.seen:
            self.seen[base] += 1
            result = f"{base}-{self.seen[base]}"
        self.seen[result] = 0
        return result


def order(name: str) -> tuple[str, str]:
    return key(name), name


def also(e: dict) -> str:
    plain = [md(n) for a, n in e["synonyms"] if not a]
    by_audience = [f"{md(n)} ({md(a)})" for a, n in e["synonyms"] if a]
    return "Also called: " + "; ".join(filter(None, [", ".join(plain), *by_audience]))


def render(entries: list[dict], owner: dict) -> str:
    """GLOSSARY.md: the terms A to Z, then every other name, linked to its term."""
    anchors = Anchors()
    anchors.add("Glossary")
    ordered = sorted(entries, key=lambda e: order(e["term"]))
    ids = {e["term"]: anchors.add(e["term"]) for e in ordered}

    def link(name: str) -> str:
        term = owner[key(name)][0]["term"]
        return f"[{md(term)}](#{ids[term]})"

    out = [HEADER, "", "# Glossary", "", INTRO, ""]
    for e in ordered:
        out += [f"## {md(e['term'])}", ""]
        out += [line.rstrip() for line in e["definition"].splitlines()] + [""]
        out += [also(e) + ".", ""] if e["synonyms"] else []
        see = ", ".join(link(name) for name in e["see"])
        out += [f"See also: {see}.", ""] if see else []
    others = sorted(
        (
            (name, audience, e["term"])
            for e in entries
            for audience, name in e["synonyms"]
        ),
        key=lambda x: (*order(x[0]), x[1]),
    )
    if others:
        anchors.add("Other names")
        out += ["## Other names", "", "Each other name, and the term it means.", ""]
        for name, audience, term in others:
            who = f" ({md(audience)})" if audience else ""
            out.append(f"- {md(name)}{who}: {link(term)}")
    return "\n".join(out).rstrip("\n") + "\n"


def same(a: str, b: str) -> bool:
    """The same text, but for trailing whitespace and line endings."""

    def lines(text: str) -> list[str]:
        out = [line.rstrip() for line in text.splitlines()]
        while out and not out[-1]:
            out.pop()
        return out

    return lines(a) == lines(b)


def write(page: Path, text: str, count: int) -> int:
    if page.is_file() and same(page.read_text(encoding="utf-8"), text):
        print(f"glossary: {page.as_posix()} is up to date ({count} terms)")
        return OK
    page.write_text(text, encoding="utf-8")
    print(f"glossary: wrote {page.as_posix()} ({count} terms)")
    return OK


def check(page: Path, text: str, count: int, fix: str) -> int:
    current = page.read_text(encoding="utf-8") if page.is_file() else None
    if current is not None and same(current, text):
        print(f"glossary: OK, {page.as_posix()} is up to date ({count} terms)")
        return OK
    if current is None:
        print(f"{page.as_posix()} is missing.")
    else:
        diff = list(
            difflib.unified_diff(
                current.splitlines(),
                text.splitlines(),
                f"{page.as_posix()} (as it is)",
                f"{page.as_posix()} (from the terms)",
                lineterm="",
            )
        )
        print("\n".join(diff[:DIFF_LINES]))
        if len(diff) > DIFF_LINES:
            print(f"... and {len(diff) - DIFF_LINES} more lines of difference")
    print(f"glossary: FAILED, {page.as_posix()} is not what the terms give. {fix}")
    return FAILED


# -- undefined terms ---------------------------------------------------------------------


def candidate(bold: str) -> str | None:
    """The term that bold text names, or None when it is a lead-in or emphasis."""
    text = " ".join(bold.split())
    if text[-1] in ".:;,!?" or len(text.split()) > MAX_WORDS:
        return None
    if any(c in text for c in "[]()`<>*_") or not re.search(r"[A-Z0-9]", text):
        return None
    return ARTICLE_RE.sub("", text)


def closes(mark: str, fence: str) -> bool:
    """Whether a fence line ``mark`` closes the code block ``fence`` opened."""
    return mark[0] == fence[0] and len(mark) >= len(fence)


def bold_terms(text: str) -> list[tuple[int, str]]:
    """``(line, term)`` for each bold term of a Markdown text, outside headings,
    code blocks and code spans."""
    out = []
    fence = ""
    for n, line in enumerate(text.splitlines(), 1):
        mark = FENCE_RE.match(line)
        if fence:
            if mark and line.strip() == mark.group(1) and closes(mark.group(1), fence):
                fence = ""
            continue
        if mark:
            fence = mark.group(1)
            continue
        if HEADING_RE.match(line):
            continue
        for bold in BOLD_RE.finditer(CODE_SPAN_RE.sub("", line)):
            term = candidate(bold.group(1))
            out += [(n, term)] if term else []
    return out


def defined(term: str, names: set[str]) -> bool:
    """Whether a name defines ``term``, or ``term`` is the plural of one."""
    k = key(term)
    forms = {k, k[:-1] if k.endswith("s") else k, k[:-2] if k.endswith("es") else k}
    return bool(forms & names)


def undefined(
    root: Path, names: set[str], skip: set[str]
) -> dict[str, tuple[str, list[str]]]:
    """Each bold term no name defines: its spelling and the places it is used."""
    found: dict[str, tuple[str, list[str]]] = {}
    for rel in sorted(tracked_files(root)):
        if not rel.lower().endswith(".md") or rel in skip or "node_modules/" in rel:
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for n, term in bold_terms(text):
            if not defined(term, names):
                found.setdefault(key(term), (term, []))[1].append(f"{rel}:{n}")
    return found


def report_undefined(found: dict[str, tuple[str, list[str]]], terms: str) -> None:
    if found:
        print("Bold terms no term defines, each with the first place it is used:")
    for spelling, places in sorted(found.values(), key=lambda x: order(x[0])):
        more = f" (and {len(places) - 1} more)" if len(places) > 1 else ""
        print(f"  {spelling}: {places[0]}{more}")
    print(
        f"glossary: {len(found)} bold term(s) that {terms} does not define "
        "(a report: --strict fails on them)"
    )


# -- the run -----------------------------------------------------------------------------


def parse_args(argv) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument(
        "--terms",
        default=TERMS,
        help=f"the terms file (default: {TERMS}); the page is {PAGE} beside it",
    )
    ap.add_argument("--write", action="store_true", help=f"write {PAGE} from the terms")
    ap.add_argument(
        "--undefined",
        action="store_true",
        help="also list the bold terms of the Markdown files that no term defines",
    )
    ap.add_argument(
        "--strict", action="store_true", help="with --undefined: fail when there is one"
    )
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    terms = Path(args.terms)
    page = terms.with_name(PAGE)
    name = terms.as_posix()
    if not terms.is_file():
        print(f"glossary: no {name} here, so there is no glossary to check")
        return OK
    try:
        data = load(terms.read_text(encoding="utf-8"))
    except YamlError as error:
        print(f"{name}:{error.line}: {error.message}")
        print(f"glossary: FAILED, {name} cannot be read (docs: the standards README)")
        return UNREADABLE
    except (OSError, UnicodeDecodeError) as error:
        print(f"glossary: FAILED, {name} cannot be read ({error})")
        return UNREADABLE
    entries, owner, problems = read_terms(data)
    for line, problem in sorted(problems):
        print(f"{name}:{line}: {problem}")
    if problems:
        print(f"glossary: FAILED, {len(problems)} problem(s) in {name}")
        return FAILED
    text = render(entries, owner)
    fix = FIX.format(args="" if args.terms == TERMS else f" --terms {name}")
    if args.write:
        code = write(page, text, len(entries))
    else:
        code = check(page, text, len(entries), fix)
    if args.undefined:
        skip = {Path(os.path.relpath(page)).as_posix()}
        found = undefined(Path("."), set(owner), skip)
        report_undefined(found, name)
        code = max(code, FAILED) if found and args.strict else code
    return code


if __name__ == "__main__":
    sys.exit(main())
