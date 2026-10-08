# murphy360/standards

The one standard every murphy360 project follows, so that no project rebuilds the same tooling. A project depends on
it by calling its workflows at a pinned version tag (`@v2`), copying its templates, and keeping its rules.

## What is here

| Path | What it is |
|---|---|
| `.github/workflows/standards-check.yml` | Fails a project that lacks a `.github/dependabot.yml` covering its ecosystems, pins an action to anything but a commit SHA, or has no `CLAUDE.md` |
| `.github/workflows/python-lint.yml` | ruff with the project's config plus the standard complexity limits, no `# noqa`, and the file-size limit (`tools/code_rules.py`), on every file (`mode: all`, the default) or on every file a change touches (`mode: changed`); `format-check: true` adds `ruff format --check` in the same scope; `paths` limits it to some directories; `format-only: true` proves a pull request only re-formats |
| `.github/workflows/shell-lint.yml` | shellcheck on every tracked `*.sh`, pinned |
| `.github/workflows/actionlint.yml` | actionlint on the project's workflows |
| `.github/workflows/test-docker.yml` | builds the project's image and runs its tests inside it |
| `.github/workflows/image.yml` | multi-arch image (amd64 and arm64 on native runners) pushed to ghcr.io as one manifest list |
| `tools/code_rules.py` | the code rules: `--all` checks that every file is clean; without it, every file a change touches; each finding printed with its line, rule and fix |
| `tools/check_standards.py` | the checks `standards-check` runs; two more (`--require-pr-template`, `--require-pre-commit`) are opt-in |
| `tools/repo-settings.sh` | applies a repository's settings and its branch ruleset (below); `--dry-run` prints what it would send |
| `templates/ci.yml` | a project's CI calling the workflows: one run per ref, a fixed-name `result` job to require |
| `templates/dependabot.yml` | Dependabot for actions, pip, docker and npm, weekly and grouped |
| `templates/CLAUDE.md` | how an agent session works in a project: worktrees, Docker tests, the definition of done |
| `templates/pull_request_template.md` | the pull request body from "Definition of done" |
| `templates/pre-commit-config.yaml` | ruff, ruff format, shellcheck and actionlint, pinned, so a finding is caught before the push |
| `templates/rulesets/main.json` | the branch ruleset `tools/repo-settings.sh` applies: pull request required, linear history, no force push, the `result` job required, the owner can bypass |

## The rules

- **Style: ruff's defaults.** 88 columns and `ruff format`, the de facto Python standard (Black's line length). A
  project changes a setting only with a reason written beside it. Existing projects move to it with one mechanical
  `ruff format` pull request.
- **Complexity and size.** McCabe complexity 15, 15 branches and 60 statements per function; 800 lines per source
  file, 1200 per test.
- **Every file is clean.** Every file is formatted, has no ruff finding, keeps every function within the complexity
  limits and is under the size limit. `mode: all`, the default, checks every file on every run, and a finding in any
  file fails it. There is no baseline file and no flag that lets a finding pass.
- **No `# noqa`.** ruff runs with `--ignore-noqa`, so a `# noqa` hides nothing, and a `# noqa` or `# ruff: noqa`
  comment is a problem of its own. A setting a project really needs goes in its ruff config, with its reason beside
  it.
- **Each finding says how to fix it.** A file that fails is followed by one line per finding: path, line and column,
  the rule, ruff's message, and a one-sentence fix. At most 50 are shown.
- **A project with debt.** `mode: changed` checks every file a change touches: a changed file must be clean, a file
  nobody changes keeps what it has, and the last line of every run counts what is left. A project adopts the
  standard with its debt as it is, pays it down one changed file at a time, and drops `mode: changed` when the count
  reads zero.
- **What a change is compared with** (`mode: changed`, and `format-only`). A pull request is compared with the first parent of the merge commit its run
  checks out: the base branch as the pull request was merged with it. If the base branch moves on while the run
  waits, the files that moved are not counted as the pull request's. When the run has no merge commit, the pull
  request's base commit is used. A push is compared with the commit before the push. The run prints which base it
  used, and passes it to the tool as `CODE_RULES_BASE`.
- **Dependencies stay current.** Every third-party action is pinned to a commit SHA with its version in a comment, and
  Dependabot updates the actions, pip, docker and npm dependencies weekly, one grouped pull request per ecosystem.
  Turn on Dependabot alerts and security updates in each repository's settings (`tools/repo-settings.sh` does this).
- **Agents work the same way everywhere.** `CLAUDE.md` from the template: draft until green, ready once, no
  `git stash` across worktrees, never reset onto a moved base.
- **Parallel agent work does not queue or collide.** `templates/ci.yml` runs one pipeline per ref: a pull request's
  superseded run is cancelled, so a second push never waits behind the first, and a push to main always finishes.
  Every job carries a `timeout-minutes` (a job calling a reusable workflow cannot set its own, so the reusable
  workflow's job carries it instead), so a stuck job no longer holds a runner for six hours. A single job named
  `result` depends on the rest; it is the one check a ruleset requires, so a cancelled or skipped job elsewhere
  never leaves the required check missing. `templates/rulesets/main.json` and `tools/repo-settings.sh` turn this
  into branch protection: pull request required, linear history, no force push, `result` required, the owner can
  bypass.
- **Pre-commit catches a finding before the push.** `templates/pre-commit-config.yaml`: ruff and ruff format at the
  pin `python-lint.yml` uses, shellcheck, actionlint, and the usual hygiene hooks (merge conflicts, large files,
  end of file). Every hook pinned to a commit SHA.
- **The pull request body follows "Definition of done".** `templates/pull_request_template.md`.

## Adopting it in a project

A new Python project starts from the template repository, `gh repo create <name> --template
murphy360/template-python`, which already holds the steps below. An existing project takes them by hand:

1. Copy `templates/ci.yml` to `.github/workflows/ci.yml` and keep the jobs that apply. That is all the code rules
   need: the lint job fetches the base it compares with.
2. Copy `templates/dependabot.yml` to `.github/dependabot.yml`; `standards-check` names any ecosystem left out.
3. Copy `templates/CLAUDE.md` to `CLAUDE.md` and fill in "This project".
4. Copy `templates/pull_request_template.md` to `.github/pull_request_template.md`.
5. Copy `templates/pre-commit-config.yaml` to `.pre-commit-config.yaml`; `pip install pre-commit && pre-commit
   install` once per checkout. Turn the matching checks on in `standards-check`'s `with:` once both templates are
   in (`check-pr-template: true`, `check-pre-commit-config: true`): they default off so a project pinned at `@v2`
   sees no new failure until it opts in.
6. Pin every other action in the project's workflows to a commit SHA.
7. See what is left: `pip install ruff==0.6.9 && python3 <standards>/tools/code_rules.py --all`.
8. If anything is left, set `mode: changed` on the lint job until it reads zero, then remove it.
9. With `format-check: true`, Python files must be formatted too. Re-format everything once, in one `ruff format`
   pull request, and set `format-only: true` on that run (for example from a `format-only` label): it proves that
   the pull request changes only the layout of Python files.
10. `tools/repo-settings.sh <owner>/<repo>` (`--dry-run` first): squash merge only, auto-merge and "Update branch"
    on, Dependabot security updates on, and the branch ruleset (`templates/rulesets/main.json`) applied to the
    repository.

A project that still has a `code_rules_baseline.json` from `v1.0.0` can delete it: the tool no longer reads it, and
says so in a note.

### The ruleset's bypass actor

`templates/rulesets/main.json` names the owner as an `actor_type: "User"` bypass actor by numeric GitHub id, not a
`RepositoryRole`: GitHub's own schema says `OrganizationAdmin` does not apply to a personal repository, and every
murphy360 project is one. A project under a different owner changes `actor_id` to that owner's numeric id
(`curl https://api.github.com/users/<login>`, the `id` field).

### From v1 to v2

v2 is a breaking release. What changed, and what a project must do:

- **The ratchet baseline is gone.** `code_rules_baseline.json` and `--update` no longer exist. Delete the file.
- **`mode: all` is the default.** A finding in any file now fails every run. A project with findings left sets
  `mode: changed` until it has none.
- **`# noqa` is not allowed.** A finding under a `# noqa` is reported, and the comment is a problem itself. Fix
  the finding and remove the comment.
- **`--report-only` is gone.** Run `code_rules.py --all` to list what is left; it exits 1 while anything is.
- **New:** each finding is printed with its line, rule and fix; `paths` limits the check to some directories; and
  `format-only: true` proves a pull request only re-formats.

## Versions

Projects pin `@v2`. A compatible change is released as `v2.x.y` and the `v2` tag moves to it; a breaking change is
`v3`, with release notes saying what a project must change. The tag moves only to a commit whose CI passed here.
A change is compatible when no adopter's next ordinary pull request newly fails under it. Before a release, the new
tools are run against every repository that calls the current major tag.
