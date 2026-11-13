#!/usr/bin/env python3
"""Keep a project's copy of the standards' templates current.

    python3 sync_templates.py <checkout>           # rewrite the shared text
    python3 sync_templates.py <checkout> --check   # change nothing; exit 1 if it would
    python3 sync_templates.py <checkout> --all     # also copy the templates it lacks

The shared text of the instruction template (``templates/CLAUDE.md``) sits between
two marker comments, which a rendered page does not show::

    <!-- standards:shared:begin ... -->
    <!-- standards:shared:end -->

A project keeps the same two markers in its ``CLAUDE.md`` or ``AGENTS.md``. This
tool rewrites the text between them from the template beside it (``../templates``),
and leaves the marker lines and everything outside them byte for byte. Text that
differs only in trailing whitespace or line endings is current and is not rewritten.
``check_standards.py --require-current-templates`` reads the same block with the
same functions, so the check and the fix never disagree.

A file without markers is never guessed at: the tool changes nothing and says what
to do. ``--shared-until "## This project"`` says where the shared text ends, and
then everything under the title down to that heading is replaced by the block,
between new markers.

``--all`` also copies the pull request template, the pre-commit config and
``dependabot.yml`` when the project lacks them, and the instruction file when the
project has none. An existing file is kept unless ``--overwrite`` is given. The
rulesets are never copied: ``repo-settings.sh`` applies them.

Exit 0 when nothing is left to do, 1 when ``--check`` finds a change to make or a
step is left to do by hand, 2 when the tool cannot run.

Standard library only.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
NAMES = ("CLAUDE.md", "AGENTS.md")  # where the block may be, here and in a project
BEGIN_RE = re.compile(r"^\s*<!--\s*standards:shared:begin\b.*-->\s*$")
END_RE = re.compile(r"^\s*<!--\s*standards:shared:end\b.*-->\s*$")
COMMAND = "python3 <standards>/tools/sync_templates.py ."
FIX = (
    f"Fix: `{COMMAND}` (<standards> is murphy360/standards, "
    "checked out at the version this project's CI calls)"
)
COPIES = (  # (the template, where a project keeps it), for --all
    ("pull_request_template.md", ".github/pull_request_template.md"),
    ("pre-commit-config.yaml", ".pre-commit-config.yaml"),
    ("dependabot.yml", ".github/dependabot.yml"),
)
PR_TEMPLATE_NAMES = ("pull_request_template.md", "pull_request_template")
SHOWN = 6  # lines of a difference that are printed
WIDTH = 100  # and the columns of each


class TemplateError(ValueError):
    """A file's block cannot be read; the message says why."""


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


def block_problems(root: Path, templates: Path = TEMPLATES) -> list[str]:
    """What ``check_standards.py --require-current-templates`` says of the block.

    Every instruction file that holds the markers must hold the template's block.
    A project with no instruction file gets nothing here: the check that
    ``CLAUDE.md`` exists has said so already.
    """
    try:
        want = template(templates)
    except TemplateError as e:
        return [f"{e} (a fault in murphy360/standards, not in this project)"]
    present = [name for name in NAMES if (root / name).is_file()]
    problems, marked = [], 0
    for name in present:
        try:
            have = block(read(root / name), name)
        except TemplateError as e:
            problems.append(str(e))
            marked += 1
            continue
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
        problems.append(
            f"{' and '.join(present)}: no `standards:shared` markers, so the shared "
            f"text cannot be compared with templates/{want['name']}. Run the sync "
            f"tool, which says how to add them: `{COMMAND}`"
        )
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
            f"{name}: it has no markers, so nothing was changed. Either:",
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
            "--shared-until needs exactly one. Nothing was changed."
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


def sync_file(
    path: Path, want: dict, until: str | None, write: bool
) -> tuple[list[str], list[str]]:
    """Bring one instruction file up to date. Returns what changed (or would,
    when ``write`` is off) and what is left to do by hand."""
    text = read(path)
    lines = text.splitlines(keepends=True)
    eol = "\r\n" if "\r\n" in text else "\n"
    found = markers(lines, path.name)
    if found is None:
        if until is None:
            return [], [by_hand(path.name, want)]
        new, said = with_markers(path.name, lines, want, until, eol, write)
        if new is None:
            return [], [said]
        changed = [said]
    else:
        have = normal(lines[found[0] + 1 : found[1]])
        if have == want["block"]:
            return [], []
        head, tail = "".join(lines[: found[0] + 1]), "".join(lines[found[1] :])
        new = rewritten(head, want, tail, eol)
        changed = [
            f"{path.name}: the shared text {was(write)} rewritten from "
            f"templates/{want['name']}",
            *difference(have, want["block"]),
        ]
    if write:
        path.write_bytes(new.encode("utf-8"))
    return changed, []


def sync_block(
    root: Path, want: dict, templates: Path, *, until=None, copy=False, write=True
) -> tuple[list[str], list[str]]:
    """The shared block of the project's instruction files: what changed, and what
    is left to do by hand."""
    present = [name for name in NAMES if (root / name).is_file()]
    if not present:
        name = want["name"]
        if not copy:
            return [], [
                f"{name} is missing. `--all` copies templates/{name}; then fill in "
                'its title and "This project".'
            ]
        if write:
            (root / name).write_bytes((templates / name).read_bytes())
        return [
            f"{name}: {was(write)} copied from templates/{name}; fill in its title "
            'and "This project"'
        ], []
    marked = [n for n in present if block(read(root / n), n) is not None]
    unmarked = want["name"] if want["name"] in present else present[0]
    changed, manual = [], []
    for name in marked or [unmarked]:
        did, left = sync_file(root / name, want, until, write)
        changed += did
        manual += left
    return changed, manual


def existing_copy(root: Path, dest: str) -> Path | None:
    """The project's copy of a template: at ``dest``, or for the pull request
    template anywhere GitHub would find one."""
    if (root / dest).exists():
        return root / dest
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
    for source, dest in COPIES:
        data = (templates / source).read_bytes()
        target, have = root / dest, existing_copy(root, dest)
        if have is None:
            changed.append(f"{dest}: {was(write)} copied from templates/{source}")
        elif have != target or not have.is_file():
            kept = have.relative_to(root).as_posix()
            notes.append(f"{dest}: not copied, the project has {kept}")
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


def main(argv=None, templates: Path = TEMPLATES) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("checkout", help="the project's checkout")
    ap.add_argument(
        "--check",
        action="store_true",
        help="change nothing; exit 1 if something would change",
    )
    ap.add_argument(
        "--all",
        action="store_true",
        help="also copy the pull request template, the pre-commit config, "
        "dependabot.yml and the instruction file when the project lacks them",
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
    root = Path(a.checkout).resolve()
    if a.overwrite and not a.all:
        ap.error("--overwrite goes with --all")
    if not root.is_dir():
        ap.error(f"{a.checkout} is not a directory")
    write = not a.check
    try:
        changed, manual = sync_block(
            root,
            template(templates),
            templates,
            until=a.shared_until,
            copy=a.all,
            write=write,
        )
    except TemplateError as e:
        print(f"sync_templates: {e}", file=sys.stderr)
        return 2
    notes = []
    if a.all:
        copied, notes = copy_templates(
            root, templates, overwrite=a.overwrite, write=write
        )
        changed += copied
    for line in changed + manual + notes:
        print(line)
    count = sum(1 for line in changed if not line.startswith(" "))
    verb = "to make" if a.check else "made"
    summary = f"{count} change(s) {verb}" if count else "nothing to change"
    if manual:
        summary += f", {len(manual)} step(s) left to do by hand"
    print(f"sync_templates: {summary}")
    return 1 if manual or (a.check and changed) else 0


if __name__ == "__main__":
    sys.exit(main())
