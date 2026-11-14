#!/usr/bin/env python3
"""Keep a project's copy of the standards' templates current.

    python3 sync_templates.py <checkout>           # rewrite the shared text
    python3 sync_templates.py <checkout> --check   # change nothing; exit 1 if it would
    python3 sync_templates.py <checkout> --all     # also copy the templates it lacks
    python3 sync_templates.py <checkout> --to-agents-md   # CLAUDE.md becomes AGENTS.md

The instruction template is ``templates/AGENTS.md``, the file every agent tool
reads. ``templates/CLAUDE.md`` only imports it (``@AGENTS.md``), for Claude Code.
The shared text sits between two marker comments, which a rendered page does not
show::

    <!-- standards:shared:begin ... -->
    <!-- standards:shared:end -->

A project keeps the same two markers in its ``AGENTS.md``, or in its ``CLAUDE.md``
when it has not moved yet. This tool rewrites the text between them from the
template beside it (``../templates``), and leaves the marker lines and everything
outside them byte for byte. Text that differs only in trailing whitespace or line
endings is current and is not rewritten.
``check_standards.py --require-current-templates`` reads the same block with the
same functions, so the check and the fix never disagree.

A file without markers is never guessed at: the tool changes nothing and says what
to do. ``--shared-until "## This project"`` says where the shared text ends, and
then everything under the title down to that heading is replaced by the block,
between new markers.

``--to-agents-md`` moves a project to the one-file layout. A ``CLAUDE.md`` that
stands alone becomes ``AGENTS.md``, byte for byte, and ``CLAUDE.md`` becomes the
template's import. The block is then brought up to date in ``AGENTS.md`` as above.
Where both files exist and ``CLAUDE.md`` does not import ``AGENTS.md``, the tool
moves nothing unless the two hold the same text: it never merges two files.
Without the flag no file is moved.

``--all`` also copies the pull request template, the pre-commit config,
``dependabot.yml`` and the issue templates when the project lacks them, both
instruction files when the project has none, and the ``CLAUDE.md`` that imports
an ``AGENTS.md`` standing alone. An existing file is kept unless ``--overwrite``
is given. The rulesets are never copied: ``repo-settings.sh`` applies them.

Exit 0 when nothing is left to do, 1 when ``--check`` finds a change to make or a
step is left to do by hand, 2 when the tool cannot run.

Standard library only.
"""

from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
AGENTS, CLAUDE = "AGENTS.md", "CLAUDE.md"
NAMES = (AGENTS, CLAUDE)  # where the block may be, here and in a project
BEGIN_RE = re.compile(r"^\s*<!--\s*standards:shared:begin\b.*-->\s*$")
END_RE = re.compile(r"^\s*<!--\s*standards:shared:end\b.*-->\s*$")
# Claude Code's import, `@AGENTS.md`. It skips code spans and fenced code blocks.
IMPORT_RE = re.compile(r"(?:^|\s)@(?:\./)?AGENTS\.md(?=\s|$)")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
SPAN_RE = re.compile(r"`[^`]*`")
COMMAND = "python3 <standards>/tools/sync_templates.py ."
FIX = (
    f"Fix: `{COMMAND}` (<standards> is murphy360/standards, "
    "checked out at the version this project's CI calls)"
)
ISSUES = ".github/ISSUE_TEMPLATE"
COPIES = (  # (the template, where a project keeps it), for --all
    ("pull_request_template.md", ".github/pull_request_template.md"),
    ("pre-commit-config.yaml", ".pre-commit-config.yaml"),
    ("dependabot.yml", ".github/dependabot.yml"),
    ("ISSUE_TEMPLATE/ticket.md", f"{ISSUES}/ticket.md"),
    ("ISSUE_TEMPLATE/config.yml", f"{ISSUES}/config.yml"),
)
PR_TEMPLATE_NAMES = ("pull_request_template.md", "pull_request_template")
SHOWN = 6  # lines of a difference that are printed
WIDTH = 100  # and the columns of each


class TemplateError(ValueError):
    """A file's block cannot be read; the message says why."""


@dataclass
class Run:
    """One run over a project's instruction files: the text each will have, and
    what the run says. ``changed`` holds what was changed (or would be, when
    ``write`` is off), ``manual`` what is left to do by hand."""

    want: dict
    write: bool
    texts: dict[str, str]
    changed: list[str] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def read(path: Path) -> str:
    """A file's text with its line endings as they are."""
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise TemplateError(f"{path.name} is not UTF-8 text")


def markers(lines: list[str], name: str) -> tuple[int, int] | None:
    """The indexes of the two marker lines, or None when the text has neither.
    ``name`` is the file, for the message when the markers are broken."""
    begins = [i for i, line in enumerate(lines) if BEGIN_RE.match(line)]
    ends = [i for i, line in enumerate(lines) if END_RE.match(line)]
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or ends[0] < begins[0]:
        raise TemplateError(
            f"{name}: the markers are broken. It needs one "
            "`standards:shared:begin` line and one `standards:shared:end` line "
            f"under it, and has {len(begins)} and {len(ends)}"
        )
    return begins[0], ends[0]


def normal(lines: list[str]) -> list[str]:
    """Lines as they are compared: no trailing whitespace, no line ending, and no
    blank line at either end."""
    out = [line.rstrip() for line in lines]
    while out and not out[0]:
        out.pop(0)
    while out and not out[-1]:
        out.pop()
    return out


def block(text: str, name: str) -> list[str] | None:
    """The shared text between the markers, normalised. None without markers."""
    lines = text.splitlines()
    found = markers(lines, name)
    return None if found is None else normal(lines[found[0] + 1 : found[1]])


def template(templates: Path = TEMPLATES) -> dict:
    """The template that holds the markers: its name, marker lines and block."""
    for name in NAMES:
        path = templates / name
        if not path.is_file():
            continue
        lines = read(path).splitlines()
        found = markers(lines, f"templates/{name}")
        if found:
            return {
                "name": name,
                "begin": lines[found[0]].strip(),
                "end": lines[found[1]].strip(),
                "block": normal(lines[found[0] + 1 : found[1]]),
            }
    raise TemplateError(
        f"no template in {templates} holds the `standards:shared` markers"
    )


def imports_agents(text: str) -> bool:
    """Whether a ``CLAUDE.md`` imports ``AGENTS.md`` the way Claude Code reads an
    import: ``@AGENTS.md``, outside a code span and a fenced code block."""
    fenced = False
    for line in text.splitlines():
        if FENCE_RE.match(line):
            fenced = not fenced
        elif not fenced and IMPORT_RE.search(SPAN_RE.sub("", line)):
            return True
    return False


def instruction_files(root: Path) -> list[str]:
    """The instruction files a project has. A ``CLAUDE.md`` that is a link to
    ``AGENTS.md`` is one file under two names: only ``AGENTS.md`` is listed, so
    nothing is ever written through the link."""
    present = [name for name in NAMES if (root / name).is_file()]
    if len(present) == 2 and (root / CLAUDE).samefile(root / AGENTS):
        present.remove(CLAUDE)
    return present


def difference(have: list[str], want: list[str]) -> list[str]:
    """The first lines that differ: the project's (``here``) and the template's."""
    diff = list(difflib.unified_diff(have, want, n=0, lineterm=""))[2:]
    changed = [line for line in diff if not line.startswith("@@")]
    shown = []
    for line in changed[:SHOWN]:
        line = ("here:     " if line[0] == "-" else "template: ") + line[1:]
        shown.append(line if len(line) <= WIDTH else line[: WIDTH - 3] + "...")
    if len(changed) > SHOWN:
        shown.append(f"... and {len(changed) - SHOWN} more line(s)")
    return ["  " + line for line in shown]


def was(write: bool) -> str:
    """The verb of a change: made, or only found by ``--check``."""
    return "was" if write else "would be"


def two_files() -> str:
    """The gap of a ``CLAUDE.md`` beside an ``AGENTS.md`` it does not import."""
    return (
        f"{CLAUDE} does not import {AGENTS}: Claude Code reads {CLAUDE} alone and "
        f"other tools read {AGENTS}, so the project keeps two files in step. Put "
        f"what every agent needs in {AGENTS}, and leave in {CLAUDE} the line "
        f"`@{AGENTS}` and what only Claude needs (`{COMMAND} --to-agents-md` does "
        "it when the two hold the same text)"
    )


def block_problems(root: Path, templates: Path = TEMPLATES) -> list[str]:
    """What ``check_standards.py --require-current-templates`` says of the block.

    Every instruction file that holds the markers must hold the template's block,
    and a ``CLAUDE.md`` beside an ``AGENTS.md`` must import it. A project with no
    instruction file gets nothing here: the base check has said so already.
    """
    try:
        want = template(templates)
    except TemplateError as e:
        return [f"{e} (a fault in murphy360/standards, not in this project)"]
    present = instruction_files(root)
    problems, marked, imported = [], 0, False
    for name in present:
        try:
            text = read(root / name)
            have = block(text, name)
        except TemplateError as e:
            problems.append(str(e))
            marked += 1
            continue
        imported = imported or (name == CLAUDE and imports_agents(text))
        if have is None:
            continue
        marked += 1
        if have != want["block"]:
            lines = [
                f"{name}: the shared text is behind templates/{want['name']} "
                f"of murphy360/standards. {FIX}",
                *difference(have, want["block"]),
            ]
            problems.append("\n".join(lines))
    if present and not marked:
        unmarked = [AGENTS] if imported and AGENTS in present else present
        problems.append(
            f"{' and '.join(unmarked)}: no `standards:shared` markers, so the shared "
            f"text cannot be compared with templates/{want['name']}. Run the sync "
            f"tool, which says how to add them: `{COMMAND}`"
        )
    if len(present) == 2 and not imported:
        problems.append(two_files())
    return problems


def rewritten(head: str, want: dict, tail: str, eol: str) -> str:
    """The block between ``head`` (to the begin marker) and ``tail`` (from the end
    marker), with one blank line on each side as the template has it."""
    return head + eol + eol.join(want["block"]) + eol + eol + tail


def by_hand(name: str, want: dict) -> str:
    """What to do about a file without markers: the tool never guesses where a
    project's own text starts."""
    return "\n".join(
        [
            f"{name}: it has no markers, so its text was not changed. Either:",
            f"  put `{want['begin']}`",
            f"  on the line above its shared text and `{want['end']}` on the line",
            "  under it, and run this again;",
            '  or run this with `--shared-until "## This project"`, the heading where',
            "  the project's own text starts. Everything under the title down to that",
            "  heading is then replaced by the shared text, between markers, so move",
            "  text of the project's own under the heading first.",
        ]
    )


def with_markers(
    name: str, lines: list[str], want: dict, until: str, eol: str, write: bool
) -> tuple[str | None, str]:
    """A file without markers, with the block put between its title and the heading
    ``until``. Returns the new text and what was done, or None and what to do."""
    at = [i for i, line in enumerate(lines) if line.strip() == until.strip()]
    if len(at) != 1:
        return None, (
            f"{name}: it has {len(at)} lines that read `{until.strip()}`, and "
            "--shared-until needs exactly one. Its text was not changed."
        )
    first = next((i for i, line in enumerate(lines) if line.strip()), 0)
    start = first + 1 if lines[first].startswith("# ") and first < at[0] else 0
    head = "".join(lines[:start])
    if head:
        head = head.rstrip("\r\n") + eol + eol
    tail = want["end"] + eol + eol + "".join(lines[at[0] :])
    text = rewritten(head + want["begin"] + eol, want, tail, eol)
    done = (
        f"{name}: the text from the title down to `{until.strip()}` "
        f"({at[0] - start} lines) {was(write)} replaced by the shared text of "
        f"templates/{want['name']}, between new markers"
    )
    return text, done


def sync_text(run: Run, name: str, until: str | None) -> None:
    """Bring the block of one instruction file up to date, in ``run.texts``."""
    text, want = run.texts[name], run.want
    lines = text.splitlines(keepends=True)
    eol = "\r\n" if "\r\n" in text else "\n"
    found = markers(lines, name)
    if found is None:
        if until is None:
            run.manual.append(by_hand(name, want))
            return
        new, said = with_markers(name, lines, want, until, eol, run.write)
        if new is None:
            run.manual.append(said)
            return
        run.changed.append(said)
    else:
        have = normal(lines[found[0] + 1 : found[1]])
        if have == want["block"]:
            return
        head, tail = "".join(lines[: found[0] + 1]), "".join(lines[found[1] :])
        new = rewritten(head, want, tail, eol)
        run.changed += [
            f"{name}: the shared text {was(run.write)} rewritten from "
            f"templates/{want['name']}",
            *difference(have, want["block"]),
        ]
    run.texts[name] = new


def importer(templates: Path) -> str:
    """The ``CLAUDE.md`` a project gets: the template's, which imports AGENTS.md."""
    path = templates / CLAUDE
    text = read(path) if path.is_file() else ""
    if not imports_agents(text):
        raise TemplateError(
            f"templates/{CLAUDE} does not import {AGENTS} (`@{AGENTS}`)"
        )
    return text


def copy_instructions(run: Run, templates: Path, copy: bool) -> None:
    """A project with no instruction file: ``--all`` copies both templates."""
    name = run.want["name"]
    both = name == AGENTS and (templates / CLAUDE).is_file()
    if not copy:
        also = f" and templates/{CLAUDE}" if both else ""
        run.manual.append(
            f"{name} is missing. `--all` copies templates/{name}{also}; then fill "
            'in the title and "This project".'
        )
        return
    run.texts[name] = read(templates / name)
    run.changed.append(
        f"{name}: {was(run.write)} copied from templates/{name}; fill in its title "
        'and "This project"'
    )
    if both:
        add_importer(run, templates)


def add_importer(run: Run, templates: Path) -> None:
    run.texts[CLAUDE] = importer(templates)
    run.changed.append(
        f"{CLAUDE}: {was(run.write)} copied from templates/{CLAUDE}, which imports "
        f"{AGENTS}"
    )


def move_out(run: Run, templates: Path) -> None:
    """``--to-agents-md``, before the block is read: a ``CLAUDE.md`` that stands
    alone becomes ``AGENTS.md``, and ``CLAUDE.md`` becomes the import."""
    texts = run.texts
    if AGENTS in texts:
        if CLAUDE not in texts:
            run.notes.append(f"{AGENTS}: it is the instruction file, nothing to move")
        elif imports_agents(texts[CLAUDE]):
            run.notes.append(f"{CLAUDE}: it imports {AGENTS}, nothing to move")
        return
    if imports_agents(texts[CLAUDE]):
        run.manual.append(
            f"{CLAUDE}: it imports {AGENTS}, and there is no {AGENTS}. Nothing was "
            "moved."
        )
        return
    run.texts = {AGENTS: texts[CLAUDE], CLAUDE: importer(templates)}
    run.changed += [
        f"{AGENTS}: {was(run.write)} written from {CLAUDE}, byte for byte",
        f"{CLAUDE}: {was(run.write)} replaced by templates/{CLAUDE}, which imports "
        f"{AGENTS}",
    ]


def move_same(run: Run, templates: Path) -> None:
    """``--to-agents-md``, after the block is up to date: a ``CLAUDE.md`` that
    holds the text of ``AGENTS.md`` becomes the import. Two texts that differ are
    never merged."""
    texts = run.texts
    if len(texts) < 2 or imports_agents(texts[CLAUDE]):
        return
    if texts[CLAUDE] != texts[AGENTS]:
        run.manual.append(
            f"{CLAUDE} and {AGENTS} both hold instructions and differ, so nothing "
            "was moved: the tool never merges two files. By hand: put what every "
            f"agent needs in {AGENTS}, leave in {CLAUDE} the line `@{AGENTS}` and "
            "what only Claude needs, and run this again."
        )
        return
    texts[CLAUDE] = importer(templates)
    run.changed.append(
        f"{CLAUDE}: it held the same text as {AGENTS}, and {was(run.write)} "
        f"replaced by templates/{CLAUDE}, which imports {AGENTS}"
    )


def old_layout(texts: dict[str, str]) -> str | None:
    """A note for a project that has not moved to AGENTS.md, or None."""
    if CLAUDE not in texts or imports_agents(texts[CLAUDE]):
        return None
    if AGENTS in texts:
        return two_files()
    return (
        f"{CLAUDE}: it holds the instructions. `--to-agents-md` moves them to "
        f"{AGENTS}, the file every agent tool reads, and leaves a {CLAUDE} that "
        "imports it"
    )


def sync_block(
    root: Path,
    want: dict,
    templates: Path,
    *,
    until: str | None = None,
    copy: bool = False,
    write: bool = True,
    move: bool = False,
) -> Run:
    """The project's instruction files: the block brought up to date, and with
    ``move`` the layout too. Returns the run, with what changed, what is left to do
    by hand and its notes."""
    before = {name: read(root / name) for name in instruction_files(root)}
    run = Run(want, write, dict(before))
    if not before:
        copy_instructions(run, templates, copy)
    else:
        if move:
            move_out(run, templates)
        texts = run.texts
        marked = [n for n in texts if block(texts[n], n) is not None]
        unmarked = want["name"] if want["name"] in texts else next(iter(texts))
        for name in marked or [unmarked]:
            sync_text(run, name, until)
        if move:
            move_same(run, templates)
        elif note := old_layout(texts):
            run.notes.append(note)
        if copy and not os.path.lexists(root / CLAUDE):
            add_importer(run, templates)
    if write:
        for name, text in run.texts.items():
            if before.get(name) != text:
                (root / name).write_bytes(text.encode("utf-8"))
    return run


def own_issue_templates(root: Path) -> list[Path]:
    """The issue templates of the project's own, where GitHub reads them: ``*.md``
    or ``*.yml`` in ``.github/ISSUE_TEMPLATE``, in any case. ``config.yml`` is not
    a template, and ``ticket.md`` there is the project's copy of the standard's."""
    github, found = root / ".github", []
    for folder in sorted(github.iterdir()) if github.is_dir() else []:
        if folder.is_dir() and folder.name.lower() == "issue_template":
            found += [
                entry
                for entry in sorted(folder.iterdir())
                if entry.suffix.lower() in (".md", ".yml")
                and entry.name.lower() != "config.yml"
                and entry != root / ISSUES / "ticket.md"
            ]
    return found


def existing_copy(root: Path, dest: str, issues: list[Path]) -> Path | None:
    """The project's copy of a template: at ``dest``, or for the pull request
    template anywhere GitHub would find one, or for the issue templates an issue
    template of the project's own (``issues``)."""
    if (root / dest).exists():
        return root / dest
    if dest.startswith(ISSUES + "/"):
        return issues[0] if issues else None
    if Path(dest).name not in PR_TEMPLATE_NAMES:
        return None
    for folder in (root / ".github", root, root / "docs"):
        if folder.is_dir():
            for entry in sorted(folder.iterdir()):
                if entry.name.lower() in PR_TEMPLATE_NAMES:
                    return entry
    return None


def copy_templates(
    root: Path, templates: Path, *, overwrite=False, write=True
) -> tuple[list[str], list[str]]:
    """Copy the other templates a project lacks. Returns what changed (or would)
    and notes on the files that were kept."""
    changed, notes = [], []
    issues = own_issue_templates(root)
    for source, dest in COPIES:
        data = (templates / source).read_bytes()
        target, have = root / dest, existing_copy(root, dest, issues)
        if have is None:
            changed.append(f"{dest}: {was(write)} copied from templates/{source}")
        elif have != target or not have.is_file():
            kept = have.relative_to(root).as_posix()
            what = ISSUES if dest.startswith(ISSUES + "/") else dest
            note = f"{what}: not copied, the project has {kept}"
            if note not in notes:  # one note for the two issue templates
                notes.append(note)
            continue
        elif have.read_bytes() == data:
            continue
        elif overwrite:
            changed.append(f"{dest}: {was(write)} replaced by templates/{source}")
        else:
            notes.append(
                f"{dest}: kept as it is; it differs from templates/{source} "
                "(--overwrite replaces it)"
            )
            continue
        if write:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    return changed, notes


def parse_args(argv) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("checkout", help="the project's checkout")
    ap.add_argument(
        "--check",
        action="store_true",
        help="change nothing; exit 1 if something would change",
    )
    ap.add_argument(
        "--to-agents-md",
        action="store_true",
        help="move the instructions of a CLAUDE.md that stands alone to AGENTS.md, "
        "byte for byte, and leave a CLAUDE.md that imports it",
    )
    ap.add_argument(
        "--all",
        action="store_true",
        help="also copy the pull request template, the pre-commit config, "
        "dependabot.yml, the issue templates and the instruction files when the "
        "project lacks them",
    )
    ap.add_argument(
        "--overwrite",
        action="store_true",
        help="with --all: replace a copy that differs from its template",
    )
    ap.add_argument(
        "--shared-until",
        metavar="HEADING",
        help="in a file without markers: the heading where the project's own text "
        'starts ("## This project"); everything under the title down to it is '
        "replaced by the shared text, between markers",
    )
    a = ap.parse_args(argv)
    if a.overwrite and not a.all:
        ap.error("--overwrite goes with --all")
    if not Path(a.checkout).is_dir():
        ap.error(f"{a.checkout} is not a directory")
    return a


def main(argv=None, templates: Path = TEMPLATES) -> int:
    a = parse_args(argv)
    root = Path(a.checkout).resolve()
    write = not a.check
    try:
        run = sync_block(
            root,
            template(templates),
            templates,
            until=a.shared_until,
            copy=a.all,
            write=write,
            move=a.to_agents_md,
        )
    except TemplateError as e:
        print(f"sync_templates: {e}", file=sys.stderr)
        return 2
    changed, notes = run.changed, run.notes
    if a.all:
        copied, kept = copy_templates(
            root, templates, overwrite=a.overwrite, write=write
        )
        changed, notes = changed + copied, notes + kept
    for line in changed + run.manual + notes:
        print(line)
    count = sum(1 for line in changed if not line.startswith(" "))
    verb = "to make" if a.check else "made"
    summary = f"{count} change(s) {verb}" if count else "nothing to change"
    if run.manual:
        summary += f", {len(run.manual)} step(s) left to do by hand"
    print(f"sync_templates: {summary}")
    return 1 if run.manual or (a.check and changed) else 0


if __name__ == "__main__":
    sys.exit(main())
