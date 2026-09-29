# murphy360/standards

The one standard every murphy360 project follows, so that no project rebuilds the same tooling. A project depends on
it by calling its workflows at a pinned version tag (`@v1`), copying its templates, and keeping its rules.

## What is here

| Path | What it is |
|---|---|
| `.github/workflows/standards-check.yml` | Fails a project that lacks a `.github/dependabot.yml` covering its ecosystems, pins an action to anything but a commit SHA, or has no `CLAUDE.md` |
| `.github/workflows/python-lint.yml` | ruff with the project's config plus the standard complexity limits, and the file-size limit (`tools/code_rules.py`), on every file a change touches (`mode: changed`, the default) or on every file (`mode: all`); `format-check: true` adds `ruff format --check` in the same scope |
| `.github/workflows/shell-lint.yml` | shellcheck on every tracked `*.sh`, pinned |
| `.github/workflows/actionlint.yml` | actionlint on the project's workflows |
| `.github/workflows/test-docker.yml` | builds the project's image and runs its tests inside it |
| `.github/workflows/image.yml` | multi-arch image (amd64 and arm64 on native runners) pushed to ghcr.io as one manifest list |
| `tools/code_rules.py` | the code rules: every file a change touches must be clean; `--all` checks every file |
| `tools/check_standards.py` | the checks `standards-check` runs |
| `templates/ci.yml` | a project's CI calling the workflows |
| `templates/dependabot.yml` | Dependabot for actions, pip, docker and npm, weekly and grouped |
| `templates/CLAUDE.md` | how an agent session works in a project: worktrees, Docker tests, the definition of done |

## The rules

- **Style: ruff's defaults.** 88 columns and `ruff format`, the de facto Python standard (Black's line length). A
  project changes a setting only with a reason written beside it. Existing projects move to it with one mechanical
  `ruff format` pull request.
- **Complexity and size.** McCabe complexity 15, 15 branches and 60 statements per function; 800 lines per source
  file, 1200 per test.
- **Every file a pull request changes must be clean.** A changed file is formatted, has no ruff finding and is under
  the size limit. A file nobody changes keeps what it has. There is no baseline file and nothing to update: a fix is
  the whole change. A finding can only appear in a file somebody changes, so nothing gets worse, and no file is
  shared between pull requests, so parallel pull requests do not conflict. The last line of every run counts what
  is left in the repository. A project adopts the standard with its debt as it is and pays it down one changed file
  at a time. When the count reads zero, the project moves to `mode: all`, which checks every file on every run.
- **What a change is compared with.** A pull request is compared with the first parent of the merge commit its run
  checks out: the base branch as the pull request was merged with it. If the base branch moves on while the run
  waits, the files that moved are not counted as the pull request's. When the run has no merge commit, the pull
  request's base commit is used. A push is compared with the commit before the push. The run prints which base it
  used.
- **Dependencies stay current.** Every third-party action is pinned to a commit SHA with its version in a comment, and
  Dependabot updates the actions, pip, docker and npm dependencies weekly, one grouped pull request per ecosystem.
  Turn on Dependabot alerts and security updates in each repository's settings.
- **Agents work the same way everywhere.** `CLAUDE.md` from the template.

## Adopting it in a project

A new Python project starts from the template repository, `gh repo create <name> --template
murphy360/template-python`, which already holds the steps below. An existing project takes them by hand:

1. Copy `templates/ci.yml` to `.github/workflows/ci.yml` and keep the jobs that apply. That is all the code rules
   need: the lint job fetches the base it compares with.
2. Copy `templates/dependabot.yml` to `.github/dependabot.yml`; `standards-check` names any ecosystem left out.
3. Copy `templates/CLAUDE.md` to `CLAUDE.md` and fill in "This project".
4. Pin every other action in the project's workflows to a commit SHA.
5. See what is left: `pip install ruff==0.6.9 && python3 <standards>/tools/code_rules.py --all --report-only`.
6. With `format-check: true`, a changed Python file must be formatted too: re-format it in a commit of its own
   before the change. Or re-format everything once, in one `ruff format` pull request. `code_rules.py --format-only`
   proves that such a branch changes only the layout of Python files, so it may touch files that still have findings.
7. When the report reads zero, set `mode: all` on the lint job.

A project that still has a `code_rules_baseline.json` from `v1.0.0` can delete it: the tool no longer reads it, and
says so in a note.

## Versions

Projects pin `@v1`. A compatible change is released as `v1.x.y` and the `v1` tag moves to it; a breaking change is
`v2`, with release notes saying what a project must change. The tag moves only to a commit whose CI passed here.
A change is compatible when no adopter's next ordinary pull request newly fails under it. Before a release, the new
tools are run against every repository that calls `@v1`.
