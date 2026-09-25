# Working on <project>

Instructions for any Claude session in this repository. Built from murphy360/standards (templates/CLAUDE.md); keep
the shared parts as they are and add what is particular to this project under "This project".

## How work arrives
The owner hands out work as a ticket (or "the next ticket in the milestone"). The milestone's description carries the
RUN ORDER: take the first open ticket in it that is not assigned to the owner and is not an epic. Read the whole
ticket, including its "For the implementer" section. If a decision the owner reserved is unclear, ask in one comment
on the ticket and take the next one; never guess. Say on the ticket, in one comment, that you started and which branch.

## Where and how to work
- Never change the branch of the main checkout: the owner may be using it. Work in a scratch worktree:
  `git worktree add -b <type>/<short-name> /tmp/<project>-wt/<short-name> origin/main`
  (types: feat/, fix/, docs/, ci/, deploy/). Remove the worktree when the PR is open.
- Tests run in the project's Docker image, not on the host. Build it under your own tag so parallel sessions never
  overwrite each other's images.
- Never touch the owner's running services, stacks or devices unless the ticket says so.

## Definition of done: one pull request
1. The change, small and readable, in the files the ticket names.
2. A unit test for every new behaviour, green in Docker; the PR body pastes the last lines of the output.
3. The documentation in the same PR: the spec for the area, the user-facing manual, and a training or how-to line
   where the project has them. Write for the reader, not the developer.
4. The commit: the first line says what changed in plain words; the body says why; `Closes #N`; a `Note:` line for
   anything the deployer must do.
5. The PR body: what and why, `Closes #N`, the tests and their output, the docs touched, any known issue with its
   ticket. Open it with `gh pr create --base main`, then comment the link on the ticket.
6. Never merge, never push to main, never enable auto-merge, unless the owner has said so for this session.

## Standards every project keeps (murphy360/standards)
- CI calls the shared workflows at a pinned version tag: standards-check, python-lint (ruff at its defaults, 88
  columns, `ruff format`), shell-lint, actionlint, test-docker, image.
- The code rules ratchet (`code_rules_baseline.json`): complexity 15, 15 branches and 60 statements per function,
  800 lines per file (1200 for a test). Never add to a file over the limit; split it in a PR of its own first. When
  you fix a finding, lower the baseline in the same PR (`code_rules.py --update`).
- Every third-party action is pinned to a commit SHA; Dependabot (`.github/dependabot.yml`) keeps them and the
  dependencies current, one grouped PR per ecosystem per week.
- Times are UTC. Secrets never go in the repository, a ticket or a log.
- Prose in docs and tickets: short sentences, one idea per sentence, no em-dashes.

## This project
<what is particular to this project: how to build and test it, its stacks and ports, its devices, its conventions>
