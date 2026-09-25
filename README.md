# murphy360/standards

The one standard every murphy360 project follows, so that no project rebuilds the same tooling. A project depends on
it by calling its workflows at a pinned version tag (`@v1`), copying its templates, and keeping its rules.

## What is here

| Path | What it is |
|---|---|
| `.github/workflows/standards-check.yml` | Fails a project that lacks a `.github/dependabot.yml` covering its ecosystems, pins an action to anything but a commit SHA, or has no `CLAUDE.md` |
| `.github/workflows/python-lint.yml` | ruff with the project's config plus the standard complexity limits, and the file-size limit, against a ratchet baseline (`tools/code_rules.py`); `format-check: true` adds `ruff format --check` |
| `.github/workflows/shell-lint.yml` | shellcheck on every tracked `*.sh`, pinned |
| `.github/workflows/actionlint.yml` | actionlint on the project's workflows |
| `.github/workflows/test-docker.yml` | builds the project's image and runs its tests inside it |
| `.github/workflows/image.yml` | multi-arch image (amd64 and arm64 on native runners) pushed to ghcr.io as one manifest list |
| `tools/code_rules.py` | the ratchet: today's findings and large files as ceilings that only go down |
| `tools/check_standards.py` | the checks `standards-check` runs |
| `templates/ci.yml` | a project's CI calling the workflows |
| `templates/dependabot.yml` | Dependabot for actions, pip, docker and npm, weekly and grouped |
| `templates/CLAUDE.md` | how an agent session works in a project: worktrees, Docker tests, the definition of done |

## The rules

- **Style: ruff's defaults.** 88 columns and `ruff format`, the de facto Python standard (Black's line length). A
  project changes a setting only with a reason written beside it. Existing projects move to it with one mechanical
  `ruff format` pull request.
- **Complexity and size, with a ratchet.** McCabe complexity 15, 15 branches and 60 statements per function; 800 lines
  per source file, 1200 per test. What a project has when it adopts the standard goes in `code_rules_baseline.json`,
  and every number there may only go down: new findings fail, and a fixed one fails until the baseline is lowered
  (`code_rules.py --update`) in the same pull request. Nobody has to fix everything on day one; nothing gets worse.
- **Dependencies stay current.** Every third-party action is pinned to a commit SHA with its version in a comment, and
  Dependabot updates the actions, pip, docker and npm dependencies weekly, one grouped pull request per ecosystem.
  Turn on Dependabot alerts and security updates in each repository's settings.
- **Agents work the same way everywhere.** `CLAUDE.md` from the template.

## Adopting it in a project

1. Copy `templates/ci.yml` to `.github/workflows/ci.yml` and keep the jobs that apply.
2. Copy `templates/dependabot.yml` to `.github/dependabot.yml`; `standards-check` names any ecosystem left out.
3. Copy `templates/CLAUDE.md` to `CLAUDE.md` and fill in "This project".
4. Pin every other action in the project's workflows to a commit SHA.
5. Write the ratchet baseline once: `pip install ruff==0.6.9 && python3 <standards>/tools/code_rules.py --update`.
6. Optionally, one `ruff format` pull request to reach the standard style.

## Versions

Projects pin `@v1`. A compatible change is released as `v1.x.y` and the `v1` tag moves to it; a breaking change is
`v2`, with release notes saying what a project must change. The tag moves only to a commit whose CI passed here.
