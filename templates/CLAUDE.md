# Working on <project>

<!-- standards:shared:begin (do not edit down to the end marker: tools/sync_templates.py rewrites this text) -->

Instructions for any Claude session in this repository. Built from murphy360/standards (templates/CLAUDE.md); keep
the shared parts as they are and add what is particular to this project under "This project".

## How work arrives

The owner hands out work as a ticket (or "the next ticket in the milestone"). The milestone's description carries the
RUN ORDER: take the first open ticket in it that is not assigned to the owner and is not an epic. Read the whole
ticket, including its "For the implementer" section; a ticket has the sections of `.github/ISSUE_TEMPLATE/ticket.md`.
If a decision the owner reserved is unclear, ask in one comment
on the ticket and take the next one; never guess. Say on the ticket, in one comment, that you started and which branch.

## Where and how to work

- Never change the branch of the main checkout: the owner may be using it. Work in a scratch worktree:
  `git worktree add -b <type>/<short-name> /tmp/<project>-wt/<short-name> origin/main`
  (types: feat/, fix/, docs/, ci/, deploy/). Remove the worktree when the PR is open.
- Sign in to GitHub as this agent's own GitHub App, never with the owner's login (murphy360/standards,
  "Agent identities"). Cut a version only when the owner says so in this session.
- Never `git stash`. Every worktree of a checkout shares one stash stack, so a stash left by one session collides
  with another's work; commit what you need to keep instead.
- Never `git reset --soft` or `--hard` onto a base that moved on. Merge the base into the branch instead, so the
  history stays what it was.
- Before a push, run `git diff --stat origin/<base>...HEAD` and check it lists only this ticket's files.
- Tests run in the project's Docker image, not on the host. Build it under your own tag so parallel sessions never
  overwrite each other's images.
- Never touch the owner's running services, stacks or devices unless the ticket says so.
- Where the project has `.claude/settings.json`, Claude Code refuses the git and gh commands these rules forbid.

## Definition of done: one pull request

1. The change, small and readable, in the files the ticket names.
2. A unit test for every new behaviour, green in Docker; the PR body pastes the last lines of the output.
3. The documentation in the same PR: the spec for the area, the user-facing manual, and a training or how-to line
   where the project has them. Write for the reader, not the developer.
4. The commit: the first line says what changed in plain words; the body says why; `Closes #N`; a `Note:` line for
   anything the deployer must do.
5. Open the pull request as a **draft** (a draft still runs CI). Push to it as often as needed while you work. Once
   the tests and the lint are green locally, mark it ready **once**, with `gh pr ready`. After that, push again
   only to fix a CI failure or a review comment, and batch the fixes into one push.
6. The PR body: what and why, `Closes #N`, the tests and their output, the docs touched, any known issue with its
   ticket. Open it with `gh pr create --draft --base main`, then comment the link on the ticket.
7. Queue it when it is ready: the owner wants finished pull requests merged. Add the project's merge label
   (Proteus: `queue`) so its merge train tests and merges it; where a project has no train, squash-merge once the
   required checks are green. Never push to main, never enable auto-merge, never merge around the checks with an
   admin bypass. Leave the merge to the owner only when the ticket reserves it or a decision in it.

## Standards every project keeps (murphy360/standards)

- CI calls the shared workflows at a pinned version tag: standards-check, python-lint (ruff at its defaults, 88
  columns, `ruff format`) or node-lint (ESLint, and Prettier's defaults when `format-check` is on), shell-lint,
  actionlint, secret-scan, test-docker, image. One run per ref: a pull request's superseded run is cancelled, and
  a fixed-name `result` job is what a ruleset requires.
- The code rules: complexity 15, 15 branches and 60 statements per function, 800 lines per file (1200 for a
  test). Every file is clean: formatted, no finding, no `# noqa`, under the size limit. CI checks every file on
  every run, and a finding anywhere fails it; each finding is printed with its line, rule and fix. A change that
  would take a file over the limit splits it in the same PR. There is no baseline and no flag that skips the
  rules. A JavaScript or TypeScript project keeps complexity 15 and 60 statements with ESLint (node-lint), and
  `prettier --check` when formatting is on. A PR that only re-formats Python is labelled `format-only`, and CI
  proves it changed no code.
- Before a push, run `pre-commit run --all-files`. It is the one command: the hooks (`.pre-commit-config.yaml`,
  from the template) are the code rules as CI runs them, ruff, ruff format, shellcheck, actionlint and gitleaks,
  each at the version CI uses, so what passes here passes the lint in CI, and a secret is caught before the
  commit. pre-commit builds each hook's own environment: install no linter by hand.
  `pip install pre-commit && pre-commit install` once per checkout.
- Every third-party action is pinned to a commit SHA; Dependabot (`.github/dependabot.yml`) keeps them and the
  dependencies current, one grouped PR per ecosystem per week.
- Times are UTC. Secrets never go in the repository, a ticket or a log.
- A test fixture is made up. It is never a saved response from a real account.
- Personal data is anything about a real person: a name, an address, an email, a phone number, a birth date,
  an account or record number, a location, a health, school or money record. It never goes in a fixture.
- Prose in docs and tickets: short sentences, one idea per sentence, no em-dashes.

<!-- standards:shared:end -->

## This project

<what is particular to this project: how to build and test it, its stacks and ports, its devices, its conventions>
