#!/usr/bin/env python3
"""The guard rail behind the never-rules of AGENTS.md: a Claude Code hook.

From murphy360/standards (``templates/claude/guard.py``). Copy it to
``.claude/guard.py``, beside ``.claude/settings.json`` (``templates/claude/``), which
runs it as a ``PreToolUse`` hook before every shell command. It reads the hook input
on stdin (``tool_input.command`` and ``cwd``) and refuses the command, with exit code
2 and the reason on stderr, when the command is one of these:

* ``git stash`` in a form that saves. Instead: commit to the branch.
* a push to the default branch. Instead: push a branch and open a pull request.
* ``git reset --hard`` or ``--soft`` onto another commit. Instead: merge the base.
* ``gh pr merge --admin``, and ``--no-verify`` on a commit or a push.
* a forced push. Instead: a new commit.

Before a ``git push`` it lets through, it runs ``pre-commit run --all-files`` when the
repository has a ``.pre-commit-config.yaml``. A failure refuses the push, with the
last lines of the output. The run has a time limit, 300 seconds, or the number of
seconds in ``GUARD_PRE_COMMIT_TIMEOUT``. Where ``pre-commit`` is not installed the
push goes through with a note: CI is the backstop.

It reads the command the way a shell does, so it finds ``cd x && git stash``,
``(git stash)``, ``$(git stash)``, ``git -C dir stash`` and ``bash -c 'git stash'``,
and it lets ``echo "git stash"`` and a commit message that says "git stash" through.

It is a guard rail, not a sandbox. It cannot see a script that runs the command, a
here-document fed to a shell, a git alias, or a command built from a variable. The
branch ruleset is the real stop. When it cannot read a call it lets the call through
and says so.

Standard library only.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

PRE_COMMIT_TIMEOUT_S = 300  # GUARD_PRE_COMMIT_TIMEOUT, in seconds, replaces it
TAIL_LINES = 30  # of the pre-commit output, in a refusal
NESTING = 4  # how deep `bash -c "bash -c ..."` is followed

RULES = {
    "stash": (
        "never `git stash`: every worktree of a checkout shares one stash stack",
        "commit what you need to keep to your branch.",
    ),
    "default": (
        "never push to the default branch",
        "push your own branch and open a pull request.",
    ),
    "reset": (
        "never `git reset --hard` or `--soft` onto another commit",
        "merge the base into the branch (`git fetch origin && git merge "
        "origin/<base>`); undo a commit with `git revert`.",
    ),
    "admin": (
        "never merge around the checks with `gh pr merge --admin`",
        "fix what fails, and merge when the required checks are green.",
    ),
    "no-verify": (
        "never skip the hooks with `--no-verify`",
        "fix what the hook reports and run the command again.",
    ),
    "force": (
        "never force a push",
        "add a new commit; when the base moved, merge it into the branch.",
    ),
    "pre-commit": (
        "`pre-commit run --all-files` passes before a push",
        "fix what it reports, commit what it changed, and push again.",
    ),
}
NO_PRE_COMMIT = (
    "pre-commit is not installed, so `pre-commit run --all-files` did not run before "
    "this push. CI is the backstop."
)

ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")
NUMBER = re.compile(r"\d+(\.\d+)?[smhd]?$")
REDIRECT = re.compile(r"&?[<>]+[&|]?")
HEREDOC_WORD = re.compile(r"[ \t]*([^\s;&|()<>]+)")
KEYWORDS = {"if", "then", "elif", "else", "do", "while", "until", "!", "{", "time"}
WRAPPERS = {"command", "builtin", "exec", "env", "nohup", "nice", "sudo", "timeout"}
WRAPPERS |= {"stdbuf", "xargs", "ionice", "setsid"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
# The stash subcommands that put nothing new on the shared stack.
STASH_KEEPS = {"list", "show", "pop", "apply", "drop", "clear", "branch", "create"}
STASH_KEEPS |= {"-h", "--help"}
GIT_VALUES = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix"}
PUSH_VALUES = {"--repo", "--push-option", "--receive-pack", "--exec"}
COMMIT_VALUES = {"--message", "--file", "--reuse-message", "--reedit-message"}
COMMIT_VALUES |= {"--template", "--author", "--date", "--cleanup", "--fixup"}
COMMIT_VALUES |= {"--squash", "--trailer", "--pathspec-from-file"}
GH_VALUES = {"--author-email", "--body", "--body-file", "--subject", "--repo"}
GH_VALUES |= {"--match-head-commit"}


def refused(rule: str, detail: str = "") -> str:
    """The refusal for a rule: the rule, then what to do instead."""
    name, instead = RULES[rule]
    return f"Refused by guard.py: {name}. Instead: {instead}{detail}"


class Frame:
    """The simple command being read: its words so far, and the word in progress."""

    def __init__(self) -> None:
        self.words: list[str] = []
        self.word: str | None = None
        self.redirect = False  # the next word is the target of a redirection

    def add(self, text: str) -> None:
        self.word = (self.word or "") + text

    def end_word(self) -> None:
        if self.word is None:
            return
        if not self.redirect:
            self.words.append(self.word)
        self.word, self.redirect = None, False

    def end_command(self, commands: list[list[str]]) -> None:
        self.end_word()
        if self.words:
            commands.append(self.words)
        self.words, self.redirect = [], False


class Scanner:
    """Splits a shell command line into its simple commands, each a list of words.

    It knows quotes, backslashes, comments, redirections, here-documents, the
    separators (``;``, ``&&``, ``||``, ``|``, ``&``, a new line), subshells and
    command substitutions. A substitution's commands are listed like any other, and
    the word that held it keeps ``$()`` in its place.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.commands: list[list[str]] = []
        self.heredocs: list[tuple[str, bool]] = []

    def run(self) -> list[list[str]]:
        while self.pos < len(self.text):
            self.block("")
        return self.commands

    def peek(self) -> str:
        return self.text[self.pos + 1 : self.pos + 2]

    def block(self, closer: str) -> None:
        """Reads commands up to ``closer`` (``)`` or a backtick), or to the end."""
        frame = Frame()
        while self.pos < len(self.text):
            char = self.text[self.pos]
            if char == closer:
                self.pos += 1
                break
            self.step(char, frame)
        frame.end_command(self.commands)

    def step(self, char: str, frame: Frame) -> None:
        if char in " \t":
            frame.end_word()
            self.pos += 1
        elif char in "'\"\\":
            self.quoted(char, frame)
        elif char == "`" or (char == "$" and self.peek() == "("):
            self.substitution(frame)
        elif char in "();|\n" or (char == "&" and self.peek() != ">"):
            self.separator(char, frame)
        elif char in "<>&":
            self.redirection(frame)
        elif char == "#" and frame.word is None:
            end = self.text.find("\n", self.pos)
            self.pos = len(self.text) if end < 0 else end
        else:
            frame.add(char)
            self.pos += 1

    def quoted(self, char: str, frame: Frame) -> None:
        if char == "\\":
            if self.peek() != "\n":  # a backslash before a new line joins the lines
                frame.add(self.peek())
            self.pos += 2
        elif char == "'":
            end = self.text.find("'", self.pos + 1)
            end = len(self.text) if end < 0 else end
            frame.add(self.text[self.pos + 1 : end])
            self.pos = end + 1
        else:
            self.double_quoted(frame)

    def double_quoted(self, frame: Frame) -> None:
        self.pos += 1
        frame.add("")
        while self.pos < len(self.text):
            char = self.text[self.pos]
            if char == '"':
                self.pos += 1
                return
            if char == "\\" and self.peek() and self.peek() in '"\\$`\n':
                frame.add(self.peek().strip("\n"))
                self.pos += 2
            elif char == "`" or (char == "$" and self.peek() == "("):
                self.substitution(frame)
            else:
                frame.add(char)
                self.pos += 1

    def substitution(self, frame: Frame) -> None:
        """``$(...)`` or backticks: the commands inside run, so they are read too."""
        backtick = self.text[self.pos] == "`"
        self.pos += 1 if backtick else 2
        self.block("`" if backtick else ")")
        frame.add("$()")

    def separator(self, char: str, frame: Frame) -> None:
        frame.end_command(self.commands)
        self.pos += 1
        if char == "(":
            self.block(")")
        elif char == "\n":
            self.skip_heredocs()

    def redirection(self, frame: Frame) -> None:
        operator = REDIRECT.match(self.text, self.pos).group()
        if frame.word is not None and frame.word.isdigit():
            frame.word = None  # the file descriptor of `2>file`
        frame.end_word()
        self.pos += len(operator)
        if operator == "<<":
            self.heredoc()
        elif self.text[self.pos : self.pos + 1] != "(":  # not `<(command)`
            frame.redirect = True

    def heredoc(self) -> None:
        """Notes a here-document's end word; its body starts on the next line."""
        strip = self.text[self.pos : self.pos + 1] == "-"
        self.pos += 1 if strip else 0
        found = HEREDOC_WORD.match(self.text, self.pos)
        if found:
            self.pos = found.end()
            self.heredocs.append((re.sub(r"['\"\\]", "", found.group(1)), strip))

    def skip_heredocs(self) -> None:
        """Passes over the bodies of the here-documents the last line opened."""
        for end_word, strip in self.heredocs:
            while self.pos < len(self.text):
                end = self.text.find("\n", self.pos)
                end = len(self.text) if end < 0 else end
                line = self.text[self.pos : end]
                self.pos = min(end + 1, len(self.text))
                if (line.lstrip("\t") if strip else line) == end_word:
                    break
        self.heredocs = []


def unwrap(words: list[str]) -> list[str]:
    """The command a line really runs: no leading assignment, keyword or wrapper."""
    rest = list(words)
    while rest:
        if ASSIGNMENT.match(rest[0]) or rest[0] in KEYWORDS:
            rest = rest[1:]
        elif os.path.basename(rest[0]) in WRAPPERS:
            rest = rest[1:]
            while rest and (rest[0].startswith("-") or NUMBER.match(rest[0])):
                rest = rest[1:]
        else:
            break
    return rest


def nested_script(words: list[str]) -> str:
    """The script handed to ``eval``, or to a shell after ``-c``."""
    if words[0] == "eval":
        return " ".join(words[1:])
    for index, word in enumerate(words):
        if index and re.fullmatch(r"-[a-z]*c", word):
            return " ".join(words[index + 1 : index + 2])
    return ""


def short_options(cluster: str, valued: str, attached: str) -> tuple[list[str], bool]:
    """The letters of ``-abc`` as options, and whether the next word is a value."""
    options = []
    for index, letter in enumerate(cluster):
        options.append("-" + letter)
        if letter in attached:
            return options, False
        if letter in valued:
            return options, index == len(cluster) - 1
    return options, False


def split_args(
    args: list[str], valued_long: set[str], valued: str = "", attached: str = ""
) -> tuple[list[str], list[str]]:
    """(the options, the other words) of a subcommand's arguments.

    The value an option takes is neither: ``-m "use --no-verify"`` names no option.
    """
    options: list[str] = []
    others: list[str] = []
    index = 0
    while index < len(args):
        word = args[index]
        if word == "--":
            others += args[index + 1 :]
            break
        if word.startswith("--"):
            options.append(word.split("=")[0])
            index += 1 if word in valued_long else 0
        elif word.startswith("-") and len(word) > 1:
            found, takes_next = short_options(word[1:], valued, attached)
            options += found
            index += 1 if takes_next else 0
        else:
            others.append(word)
        index += 1
    return options, others


def abbreviates(option: str, full: str, shortest: int) -> bool:
    """Whether git reads ``option`` as ``full``: it accepts an unambiguous start."""
    return len(option) >= shortest and full.startswith(option)


def skips_hooks(options: list[str]) -> bool:
    return any(abbreviates(option, "--no-verify", 9) for option in options)


def git_call(words: list[str]) -> tuple[list[str], str, list[str]]:
    """(the ``-C`` directories, the subcommand, its arguments) of a git command."""
    directories = []
    index = 1
    while index < len(words) and words[index].startswith("-"):
        if words[index] == "-C":
            directories += words[index + 1 : index + 2]
        index += 2 if words[index] in GIT_VALUES else 1
    return directories, "".join(words[index : index + 1]), words[index + 1 :]


def git_out(where: Path, *args: str) -> str:
    """What a read-only git command prints, or nothing when it fails."""
    try:
        done = subprocess.run(
            ["git", "-C", str(where), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def stash_refusal(args: list[str]) -> str:
    return "" if args and args[0] in STASH_KEEPS else refused("stash")


def reset_refusal(args: list[str]) -> str:
    options, others = split_args(args, set())
    onto = others[0] if others else "HEAD"
    moves = {"--hard", "--soft"} & set(options)
    return refused("reset") if moves and onto not in ("HEAD", "@") else ""


def commit_refusal(args: list[str]) -> str:
    options, _ = split_args(args, COMMIT_VALUES, "mFCct", "Su")
    return refused("no-verify") if "-n" in options or skips_hooks(options) else ""


def gh_refusal(args: list[str]) -> str:
    options, others = split_args(args, GH_VALUES, "AbFtR")
    merges = others[:2] == ["pr", "merge"]
    return refused("admin") if merges and "--admin" in options else ""


def forced(options: list[str], refspecs: list[str]) -> bool:
    return (
        bool({"-f", "--force", "--mirror"} & set(options))
        or any(abbreviates(option, "--force-with-lease", 9) for option in options)
        or any(spec.startswith("+") for spec in refspecs)
    )


def branch_name(ref: str) -> str:
    """``origin/main`` and ``refs/heads/main`` are both the branch ``main``."""
    if ref.startswith("refs/heads/"):
        return ref[len("refs/heads/") :]
    return ref.partition("/")[2]


def to_default_branch(options: list[str], others: list[str], where: Path) -> bool:
    """Whether a push would update ``main``, ``master`` or the remote's default."""
    if {"--all", "--branches"} & set(options):
        return True
    refspecs = others[1:]
    if not refspecs and "--tags" in options:
        return False
    remote = others[0] if others else "origin"
    head = git_out(where, "symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD")
    here = {git_out(where, "symbolic-ref", "--short", "HEAD")}
    if not refspecs:  # a bare push goes where git says this branch pushes to
        here.add(branch_name(git_out(where, "rev-parse", "--abbrev-ref", "@{push}")))
    targets: set[str] = set()
    for spec in refspecs or ["HEAD"]:
        target = spec.lstrip("+").rpartition(":")[2]
        target = branch_name(target) if target.startswith("refs/heads/") else target
        targets |= here if target in ("", "HEAD", "@") or "$" in target else {target}
    return bool(targets & ({"main", "master", branch_name(head)} - {""}))


class Guard:
    """Reads one command line; keeps the pushes it let through, for pre-commit."""

    def __init__(self, cwd: str) -> None:
        self.start = Path(cwd)
        self.cwd = self.start
        self.pushes: list[Path] = []

    def check(self, text: str, depth: int = 0) -> str:
        """The refusal for a command line, or nothing when it breaks no rule."""
        for words in Scanner(text).run():
            message = self.command(unwrap(words), depth)
            if message:
                return message
        return ""

    def command(self, words: list[str], depth: int) -> str:
        program = os.path.basename(words[0]) if words else ""
        if program == "cd":
            self.cwd = self.cwd.joinpath(*words[1:2])
        elif program in SHELLS or program == "eval":
            script = nested_script([program, *words[1:]])
            return self.check(script, depth + 1) if depth < NESTING else ""
        elif program == "gh":
            return gh_refusal(words[1:])
        elif program == "git":
            return self.git(words)
        return ""

    def git(self, words: list[str]) -> str:
        directories, subcommand, args = git_call(words)
        if subcommand == "stash":
            return stash_refusal(args)
        if subcommand == "reset":
            return reset_refusal(args)
        if subcommand == "commit":
            return commit_refusal(args)
        if subcommand == "push":
            where = self.cwd.joinpath(*directories)
            return self.push(args, where if where.is_dir() else self.start)
        return ""

    def push(self, args: list[str], where: Path) -> str:
        options, others = split_args(args, PUSH_VALUES, "o")
        if {"-n", "--dry-run"} & set(options):  # on a push, -n is a dry run
            return ""
        if skips_hooks(options):
            return refused("no-verify")
        if forced(options, others[1:]):
            return refused("force")
        if to_default_branch(options, others, where):
            return refused("default")
        self.pushes.append(where)
        return ""


def time_limit() -> int:
    value = os.environ.get("GUARD_PRE_COMMIT_TIMEOUT", "")
    return int(value) if value.isdigit() and int(value) else PRE_COMMIT_TIMEOUT_S


def pre_commit(where: Path) -> tuple[str, str]:
    """(a refusal, a note) from ``pre-commit run --all-files`` before a push."""
    root = git_out(where, "rev-parse", "--show-toplevel")
    if not root or not (Path(root) / ".pre-commit-config.yaml").is_file():
        return "", ""
    if not shutil.which("pre-commit"):
        return "", NO_PRE_COMMIT
    limit = time_limit()
    run = subprocess.Popen(
        ["pre-commit", "run", "--all-files"],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
        env={**os.environ, "PRE_COMMIT_COLOR": "never"},
    )
    try:
        output, _ = run.communicate(timeout=limit)
    except subprocess.TimeoutExpired:
        os.killpg(run.pid, signal.SIGKILL)
        run.communicate()
        detail = (
            f"\nIt did not finish in {limit} seconds. Run `pre-commit run "
            "--all-files` yourself (the first run installs the hooks), then push."
        )
        return refused("pre-commit", detail), ""
    if run.returncode == 0:
        return "", ""
    tail = "\n".join(output.splitlines()[-TAIL_LINES:])
    return refused("pre-commit", f"\nThe end of its output:\n{tail}"), ""


def guard(event: dict) -> int:
    """The exit code for one hook input: 2 refuses the call, 0 lets it through."""
    command = (event.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not re.search(r"git|gh", command):
        return 0
    reader = Guard(event.get("cwd") or os.getcwd())
    message = reader.check(command)
    notes = []
    for where in dict.fromkeys(reader.pushes):
        if message:
            break
        message, note = pre_commit(where)
        notes += [note] if note else []
    if message:
        print(message, file=sys.stderr)
        return 2
    if notes:
        extra = {"hookEventName": "PreToolUse", "additionalContext": notes[0]}
        print(json.dumps({"systemMessage": notes[0], "hookSpecificOutput": extra}))
    return 0


def main() -> int:
    try:
        return guard(json.load(sys.stdin))
    except Exception as error:  # a guard rail that breaks must not stop the work
        note = f"guard.py could not read this call and let it through: {error!r}"
        print(note, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
