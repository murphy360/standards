# murphy360/standards

The one standard every murphy360 project follows, so that no project rebuilds the same tooling. A project depends on
it by calling its actions and workflows at a pinned version tag (`@v2`), copying its templates, and keeping its
rules.

## What is here

| Path | What it is |
|---|---|
| `actions/checks` | the quick checks as one step of a project's own job: `standards-check`, the lint (`python-lint` or `node-lint`), `shell-lint`, `actionlint` and `secret-scan`, each the check the reusable workflow of that name runs, with the same inputs; `checks` names which run; every one runs, also after one failed, and the step fails at the end ("One job runs the quick checks", below) |
| `actions/take-ownership` | the first step of a project's own job, before the checkout: on a self-hosted runner it takes back the files a previous job's containers left in the workspace; on a GitHub-hosted runner it does nothing |
| `.github/workflows/standards-check.yml` | Fails a project that lacks a `.github/dependabot.yml` covering its ecosystems, pins an action to anything but a commit SHA, or has no instruction file (`AGENTS.md`, or a `CLAUDE.md` alone); `check-templates: true` also fails one whose copy of the shared text of `AGENTS.md` fell behind the template, whose `CLAUDE.md` does not import the `AGENTS.md` beside it, or whose CI has no job named `result` |
| `.github/workflows/python-lint.yml` | ruff with the project's config plus the standard complexity limits, no `# noqa`, and the file-size limit (`tools/code_rules.py`), on every file (`mode: all`, the default) or on every file a change touches (`mode: changed`); `format-check: true` adds `ruff format --check` in the same scope; `paths` limits it to some directories; `format-only: true` proves a pull request only re-formats; `project-ruff: true` lints with the ruff the project pins |
| `.github/workflows/node-lint.yml` | ESLint with the project's config plus the standard complexity limits, and the file-size limit (`tools/code_rules.py --eslint`), on every file (`mode: all`, the default) or on every file a change touches (`mode: changed`); `format-check: true` adds `prettier --check` in the same scope; `paths` limits it to some directories. Added in v2.2.0 |
| `.github/workflows/type-check.yml` | pyright, pinned, on a Python project (`basic` mode unless the project's pyright config sets one), or the project's own `tsc --noEmit` on a TypeScript one (`language: typescript`); `install-command` installs what the code imports, so that pyright sees it; each finding is printed with its line, rule and fix (`tools/type_check.py`). No baseline: a project with debt narrows `include` in its own config |
| `.github/workflows/shell-lint.yml` | shellcheck on every tracked `*.sh`, pinned by digest (v0.11.0, the version the pre-commit template pins) |
| `.github/workflows/actionlint.yml` | actionlint on the project's workflows, pinned by digest |
| `.github/workflows/secret-scan.yml` | gitleaks, pinned by digest, on the commits of a pull request or a push; `full-history: true` scans every commit. A finding names the file, the line and the rule, never the secret |
| `.github/workflows/test-docker.yml` | builds the project's image and runs its tests inside it |
| `.github/workflows/image.yml` | multi-arch image (amd64 and arm64 on native runners) pushed to ghcr.io as one manifest list |
| `tools/code_rules.py` | the code rules: `--all` checks that every file is clean; without it, every file a change touches; each finding printed with its line, rule and fix; `--eslint` adds the project's ESLint and `--prettier` adds `prettier --check`; `--silencers` checks that every skip, xfail, type ignore and the like carries its reason, and counts them. CI runs it, and a project runs it as a pre-commit hook |
| `tools/run_checks.py` | what `actions/checks` runs: each check the step names, whatever the one before it did, then a line per check and the highest exit code; on GitHub a log group per check, an error annotation per failed check and a table in the job summary |
| `tools/steps/` | the shell of each check step, one script each (`standards-check.sh`, `code-rules.sh`, `shell-lint.sh`, `actionlint.sh`, `secret-scan.sh`, `take-ownership.sh`): the reusable workflow and the action run the same file, and the tests run that file |
| `tools/type_check.py` | the type check `type-check` runs: pyright, or the project's `tsc --noEmit` with `--language typescript`; it reads the checker's output and prints each finding the way `code_rules.py` does |
| `.pre-commit-hooks.yaml` | the code rules as pre-commit hooks: `code-rules` is what `python-lint` runs and `code-rules-node` is what `node-lint` runs. pre-commit installs this repository (`pyproject.toml`: the modules under `tools/` and the pinned ruff) into the hook's own environment, so a project installs nothing |
| `tools/check_standards.py` | the checks `standards-check` runs; six more (`--require-pr-template`, `--require-pre-commit`, `--require-ticket-template`, `--require-current-templates`, `--require-agent-settings`, `--require-one-ruff-version`) are opt-in |
| `tools/ruff_version.py` | which ruff a project is linted with: the `ruff-version` input, the project's own pin with `project-ruff: true`, or the standard's default, which is written once, in `pyproject.toml`. The lint workflows install ruff with it and `code_rules.py` imports it, so CI and the hook choose the same way |
| `tools/images/`, `tools/image-ref.sh` | the images the workflows run (shellcheck, actionlint, gitleaks, alpine), each by tag and digest in a Dockerfile of its own, where Dependabot updates them; the script reads one for a workflow |
| `tools/sync_templates.py` | brings a project's copy of the templates up to date: rewrites the shared text of its `AGENTS.md` (or `CLAUDE.md`) from the template and touches nothing else; `--check` changes nothing and exits 1 if something would change; `--to-agents-md` moves a project's instructions from `CLAUDE.md` to `AGENTS.md`; `--all` also copies the templates the project lacks ("Staying current", below) |
| `tools/agent_app.py` | gives each agent its own GitHub identity (a GitHub App): the link that creates it, a one-hour token, the commit author lines |
| `tools/repo-settings.sh` | applies a repository's settings and its branch ruleset (below), and turns on GitHub's secret scanning and push protection where GitHub offers them; `--dry-run` prints what it would send |
| `tools/fleet.py` | the fleet report: one row per repository, with the version of the standard it follows and what it lacks; `--run-tools <ref>` is the rehearsal before a release ("The fleet report", below) |
| `templates/ci.yml` | a project's CI: one `checks` job for the quick checks, the test and image workflows, one run per ref, a fixed-name `result` job to require |
| `templates/eslint.config.mjs` | the ESLint config a TypeScript project starts from |
| `templates/dependabot.yml` | Dependabot for actions, pip, docker and npm, weekly and grouped |
| `templates/AGENTS.md` | the one instruction file, which every agent tool reads: how work arrives, worktrees, Docker tests, the definition of done; the shared text sits between two marker comments, so a tool can compare and rewrite it |
| `templates/CLAUDE.md` | Claude Code's import of that file, `@AGENTS.md`, and nothing else ("Which tools read `AGENTS.md`", below) |
| `templates/pull_request_template.md` | the pull request body from "Definition of done" |
| `templates/ISSUE_TEMPLATE/ticket.md` | the ticket, an implementer's whole brief: the problem or the owner's direction, what to build, the acceptance checklist, "For the implementer", what is out of scope, the decisions the owner reserves |
| `templates/ISSUE_TEMPLATE/config.yml` | keeps the blank issue beside the ticket template, for a quick note |
| `templates/pre-commit-config.yaml` | the code rules (the `code-rules` hook), ruff, ruff format, shellcheck, actionlint and gitleaks, each pinned at the version CI uses, so a finding is caught before the push and a secret before the commit |
| `templates/claude/settings.json` | what a Claude Code session refuses, copied to `.claude/settings.json`: deny rules for the simple forms of the never-rules, and a `PreToolUse` hook that runs the guard before every shell command. Nothing else: no model, no allow list |
| `templates/claude/guard.py` | the guard, copied to `.claude/guard.py`: it refuses `git stash`, a push to the default branch, a reset onto another commit, an admin merge, `--no-verify` and a forced push, says what to do instead, and runs `pre-commit run --all-files` before a push |
| `templates/rulesets/main.json` | the branch ruleset `tools/repo-settings.sh` applies: pull request required, linear history, no force push, the `result` job required; the owner can merge a pull request past a failed check, and nobody can push to main directly |
| `templates/rulesets/version-tags.json` | the tag ruleset `tools/repo-settings.sh` applies: only the owner can create, move or delete a `v*` tag |
| `tests/fixtures/` | the three small projects (Node, Python, Docker) this repository's own CI runs `node-lint`, `python-lint` (with the newer ruff the Python project pins), `type-check`, `test-docker` and `image` on, so every workflow runs here before a tag moves |

The five quick checks (standards-check, python-lint or node-lint, shell-lint, actionlint, secret-scan) exist twice:
as one step of the project's own job, through `actions/checks`, and each as a reusable workflow, a job of its own. A
project starts with the action ("One job runs the quick checks", below). The five workflows keep working through v2
with the same inputs, and are removed in v3. type-check, test-docker and image stay workflows.

The check workflows above (standards-check, python-lint, node-lint, type-check, shell-lint, actionlint, secret-scan) take `runs-on`: GitHub's
`ubuntu-latest` by default, or a JSON list of labels for a self-hosted runner (`'["ci", "X64"]'`). A private
project pays for hosted minutes, and each job is billed at least one minute however short, so a project with its own
runners passes them here. The runner needs Docker; on a self-hosted one each job first takes ownership of files a
previous job's containers left in the workspace. A job that uses the actions is the project's own: it names its
runner as any job does, and `actions/take-ownership` is its first step.

The workflows that run a tool (standards-check, python-lint, node-lint, type-check) take the tool from the commit they were
called at. A project at `@v2` runs the tools of `v2`, a project pinned at a commit runs the tools of that commit, and
this repository's own CI runs the tools of the pull request. GitHub names that commit to the workflow as
`job.workflow_sha`. The run prints it: `standards tools: murphy360/standards@<commit> (<workflow ref>)`. The tools are
fetched into the runner's temp directory, so nothing is added to the project's workspace. The `standards-ref` input
is deprecated and has no default: a project that still passes it keeps working, and it is read only if GitHub does
not name the commit. An action needs none of this: GitHub downloads this repository at the ref the step names, and
the action runs the tools beside it.

## One job runs the quick checks

`templates/ci.yml` has one `checks` job. It is the project's own job, so it names its runner and its time limit, and
its steps are the standard's actions:

```yaml
  checks:
    runs-on: ubuntu-latest   # or [self-hosted, linux]: the job's own line, no JSON string
    timeout-minutes: 15
    steps:
      - uses: murphy360/standards/actions/take-ownership@v2
      - uses: actions/checkout@<commit SHA>  # <version>
        with:
          fetch-depth: 0     # the secret scan reads the commits of the change
      - uses: murphy360/standards/actions/checks@v2
        with:
          format-check: true
```

- **What runs.** `checks` names the checks, separated by spaces, each by the name of the reusable workflow that runs
  the same check. The default is `standards-check python-lint shell-lint actionlint secret-scan`. A JavaScript or
  TypeScript project writes `node-lint` in place of `python-lint`; `node-lint` lints the project's Python files
  too. A second lint, in another directory, is a second `checks` step with `checks: node-lint`, its own
  `working-directory`, and `if: ${{ !cancelled() }}` so that it runs after a failure.
- **Every check runs.** A check that fails does not stop the next one, and the step fails at the end, so one run
  reports every tool's findings. Each check's output is a group of the step's log. The last lines say which passed
  and which failed (`checks: FAILED, 2 of 5: python-lint, actionlint`), each failed check is an error annotation
  that names its group, and the job summary holds the same table. The step's exit code is the highest of the
  checks'. A name that is no check fails the step too, and the others still run.
- **The inputs are the workflows'.** Same names and same defaults: `mode`, `paths`, `format-check`, `format-only`,
  `working-directory`, `no-inline-config`, `silencers`, `project-ruff`, `ruff-version`, `python-version`,
  `node-version`, the six `check-*` inputs of standards-check, `shellcheck-version`, `actionlint-version` and
  `full-history`. Where this page says an input goes on `python-lint`, on `node-lint` or in `standards-check`'s
  `with:`, the `checks` step takes it the same way. `working-directory` is the lint's: the other checks run in the
  root of the checkout. There is no `runs-on` and no `standards-ref`.
- **The tools are the pinned ref's.** An action finds its own files (`github.action_path`), so the step runs the
  tools of the ref it names: nothing is fetched a second time, and nothing is added to the workspace.
- **The checkout is the project's.** It fetches the whole history (`fetch-depth: 0`), because the secret scan
  reads the commits of the change. A shallow checkout fails the secret scan, which says what to set.
- **The runner needs** Docker, git and bash: shellcheck, actionlint and gitleaks run from their images.
- **What stays a job of its own.** `type-check`, `test-docker` and `image` install or build, so each is still a
  reusable workflow, and `result` needs them beside `checks`.

A project that calls the five reusable workflows moves in one pull request. Replace the `standards`, `lint`,
`shell`, `workflows` and `secret-scan` jobs by the `checks` job. Move each `with:` line as it is. Write a `runs-on`
input as the job's own `runs-on`, and drop `standards-ref`. Name `checks` where a `needs:` named the five.

### What a run costs

A private repository is billed for each job, in whole minutes, rounded up. So a job that works for eight seconds
costs a minute.

| | Jobs | Measured | Billed |
|---|---|---|---|
| Before | `standards`, `lint`, `shell`, `workflows`, `secret-scan` | 7 s, 7 s, 8 s, 7 s and 8 s: 37 s | 5 minutes |
| After | `checks` | 11 s for the step that runs the five checks | 1 minute |
| Both | `result` | 3 s | 1 minute |

- **Measured.** On this repository, on GitHub's `ubuntu-latest`: each job from its start to its end, as GitHub's
  API reports it, without the wait for a runner. The five jobs and `result` are those of the run on `main` at
  commit `14151df`. `checks` is the job of this change's own pull request (#56). Its step with the five checks took
  11 s, with the three image pulls and the ruff install. The whole job took 48 s there, 30 s of it in a second
  step that lints the Node fixture, which a project's job does not have.
- **Arithmetic.** The billed minutes are counted from the rule above, one minute for each job under a minute. No
  bill was read: this repository is public, and a public repository is billed nothing. For a private project that
  runs these jobs it is 6 minutes a run before and 2 after. Its test and image jobs cost the same before and after.

## The rules

- **Style: ruff's defaults.** 88 columns and `ruff format`, the de facto Python standard (Black's line length). A
  project changes a setting only with a reason written beside it. Existing projects move to it with one mechanical
  `ruff format` pull request.
- **JavaScript and TypeScript style: Prettier's defaults.** ESLint recommended and typescript-eslint recommended
  (`templates/eslint.config.mjs`). ESLint and Prettier are the project's devDependencies, so the lockfile pins them
  and Dependabot updates them. Complexity is 15, and 60 statements per function, the limits ESLint has. The file-size
  limit is the same one. `node-lint` checks them the way `python-lint` checks ruff: `mode: all` by default,
  `mode: changed` while a project pays down debt, and no baseline.
- **Complexity and size.** McCabe complexity 15, 15 branches and 60 statements per function; 800 lines per source
  file, 1200 per test. ESLint checks complexity 15 and 60 statements.
- **Every file is clean.** Every file is formatted, has no ruff finding, keeps every function within the complexity
  limits and is under the size limit. `mode: all`, the default, checks every file on every run, and a finding in any
  file fails it. There is no baseline file and no flag that lets a finding pass.
- **No `# noqa`.** ruff runs with `--ignore-noqa`, so a `# noqa` hides nothing, and a `# noqa` or `# ruff: noqa`
  comment is a problem of its own. A setting a project really needs goes in its ruff config, with its reason beside
  it.
- **What a project's own config may change.** Its ruff config may add rules of its own, set the line length with a
  reason beside it, and exclude files. Every tracked Python file ruff does not check is then printed by name on
  every run, and the last line counts them. The config may not switch a standard rule off. A standard finding under
  `per-file-ignores` or `lint.exclude` is reported like any other, and an `extend-per-file-ignores` entry that names
  a standard rule is a problem of its own. For ESLint, `no-inline-config: true` on `node-lint` does the same to an
  `eslint-disable` comment: it hides nothing and is a problem, as a `# noqa` is. It is off by default in v2.
- **Every silencer carries its reason.** A `# noqa` is not allowed at all. The other ways to turn a check off in a
  file are allowed with a reason, and `silencers: true` on `python-lint` or `node-lint` (`code_rules.py --silencers`)
  checks that each has one. It is off by default in v2.
  - A pytest `skip`, `skipif` or `xfail` mark needs a `reason=`. A condition says when, not why. The text of
    `pytest.mark.skip("text")`, `pytest.skip("text")` and `pytest.xfail("text")` is the reason.
    `pytest.importorskip` names the module that is missing, and that is its reason.
  - A `# type: ignore` or `# pyright: ignore` needs its error code in brackets, and a comment that says why: after
    it on the same line, or on the line above.
  - A `# pragma: no cover` and a `# shellcheck disable=` need a comment on the line above that says why.
    shellcheck directives are read in `*.sh` and in workflow files, where actionlint runs shellcheck on every
    `run:` script.
  - In a JavaScript or TypeScript test file, `.skip` on `it`, `test` or `describe` needs a comment on the line
    above. `.only` is always a problem.

  A silencer without its reason is a problem line with its fix. The last line of every run counts the silencers
  left, with or without a reason, and the `eslint-disable` comments, so a rising count shows. The tool checks that
  a reason is there; a review checks that it is a good one. It cannot see a removed test or a weakened assertion:
  that is the review's part too.
- **Each finding says how to fix it.** A file that fails is followed by one line per finding: path, line and column,
  the rule, the linter's message, and a one-sentence fix. At most 50 are shown.
- **Types are checked.** `type-check` runs pyright on a Python project and the project's own `tsc --noEmit` on a
  TypeScript one. A name, an argument or an attribute that does not exist fails there, before any test runs. pyright
  runs in `basic` mode unless the project's pyright config sets `typeCheckingMode`. An error is a finding; a warning
  is counted and fails nothing. Each finding is printed like a code-rules finding: path, line and column, the rule,
  the message and a one-sentence fix. There is no baseline and no input that lets a finding pass. A project with
  debt narrows `include` in its pyright config or its `tsconfig.json`, with the reason beside it, and widens it as
  it pays the debt down. A `# type: ignore` in a file is a silencer, and carries its reason (above). The check is
  opt-in: a project sees nothing until it calls the workflow.
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
- **The linters stay current the same way.** Each linter version sits where Dependabot reads it.
  - *ESLint and Prettier* are the project's devDependencies, and its lockfile pins them.
  - *ruff* is the project's too, with `project-ruff: true` on `python-lint` or `node-lint`. The project writes
    `ruff==<version>` in `requirements-dev.txt`. Another `requirements*.txt`, a dependency group or an extra of
    `pyproject.toml` counts too, and `requirements-dev.txt` is read first. Only an exact pin counts, and only in
    the directory the lint runs in. Dependabot's pip pull request moves the pin, and CI on that pull request lints
    with the new release: its findings show up there, alone, and not on someone's unrelated change. The run prints
    the release and the file that names it. A project that pins none gets the standard's default, and the
    `ruff-version` input still wins over both. `project-ruff` is off by default in v2, because a project that
    already names a ruff in those files would change version unasked. It is the default in v3.
  - *The standard's default ruff* is written once, in this repository's `pyproject.toml`
    (`tools/ruff_version.py` reads it). A new default reaches every project that pins none at once, so it moves
    only with a release that says so.
  - *shellcheck, actionlint, gitleaks and alpine* run as container images. Each is named by tag and digest in a
    Dockerfile of its own under `tools/images/`, which Dependabot's docker ecosystem reads. Dependabot reads no
    image in a `run:` step and no `docker://` reference in a workflow. `shellcheck-version` and
    `actionlint-version` still take another release, by its tag.
  - *One ruff, locally and in CI.* With `--project-ruff` in its `args`, the `code-rules` hook runs the release the
    project pins: it installs that release into its own environment on the first run. `check-ruff-version: true`
    on `standards-check` fails a project whose ruff versions differ: its pins, its `ruff-pre-commit` rev, what its
    `code-rules` hook runs and what its lint jobs run.
- **Agents work the same way everywhere.** `AGENTS.md` from the template, the one file every agent tool reads:
  draft until green, ready once, no `git stash` across worktrees, never reset onto a moved base. It names the one
  command before a push and links here for the rules; it does not repeat the numbers CI enforces.
- **Work arrives as a ticket, in the milestone's RUN ORDER.** The ticket is the implementer's whole brief, so
  every ticket has the sections of `templates/ISSUE_TEMPLATE/ticket.md`. A milestone's description carries a
  numbered RUN ORDER of its tickets. A ticket assigned to the owner is the owner's. An agent takes the first open
  ticket in the order that is not. Where a decision the owner reserved is unclear, the agent asks in one comment
  and takes the next ticket. It never guesses.
- **The never-rules are refused, not only written.** A long session may no longer have `AGENTS.md` in view, so
  `templates/claude/settings.json` and `templates/claude/guard.py`, copied to `.claude/`, make Claude Code refuse
  the command before it runs: `git stash` in a form that saves (listing or showing a stash is fine), a push to the
  default branch, `git reset --hard` or `--soft` onto another commit, `gh pr merge --admin`, `--no-verify` on a
  commit or a push, and a forced push. A refusal names the rule and says what to do instead. The guard reads the
  command the way a shell does: it finds it after `cd x &&`, after `;`, in a subshell, in `$(...)`, after
  `git -C dir` and in `bash -c '...'`, and it lets through a command that only says the words (an `echo`, a `grep`,
  a commit message). Before a push it lets through, it runs `pre-commit run --all-files` when the project has a
  `.pre-commit-config.yaml`, and refuses the push with the end of the output when that fails. The run has a time
  limit of 300 seconds (`GUARD_PRE_COMMIT_TIMEOUT` sets another); past it the push is refused, and the session
  runs the command itself once, which installs the hooks. Where `pre-commit` is not installed the push goes
  through with a one-line note, and CI is the backstop. The deny rules in the settings file cover the simple forms
  on their own, should the guard not start (no `python3`), but they give no reason.
- **The guard is a guard rail, not a sandbox.** It cannot see a script or a Makefile target that runs the command,
  a here-document fed to a shell, a git alias, a command built from a variable, or a tool other than the shell (an
  API call that merges). Claude
  Code reads `.claude/settings.json` from the folder a session starts in: a session started above the project,
  which only changes into it, is not guarded, and neither is another agent tool. When the guard cannot read a call
  it lets the call through and says so. The branch ruleset is the real stop.
- **Parallel agent work does not queue or collide.** `templates/ci.yml` runs one pipeline per ref: a pull request's
  superseded run is cancelled, so a second push never waits behind the first, and a push to main always finishes.
  Every job carries a `timeout-minutes` (`checks` sets its own; a job calling a reusable workflow cannot, so the
  reusable workflow's job carries it instead), so a stuck job no longer holds a runner for six hours. A single job named
  `result` depends on the rest; it is the one check a ruleset requires, so a cancelled or skipped job elsewhere
  never leaves the required check missing. `templates/rulesets/main.json` and `tools/repo-settings.sh` turn this
  into branch protection: pull request required, linear history, no force push, `result` required, the owner can
  bypass.
- **Local equals CI: one command before a push.** `pre-commit run --all-files` runs what the lint in CI runs.
  `templates/pre-commit-config.yaml` holds the `code-rules` hook (`.pre-commit-hooks.yaml`): the code rules on
  every file, with the complexity limits, the `# noqa` rule, the size limit and the format, printing the lines CI
  prints. It also holds ruff and ruff format at the standard's default ruff, shellcheck at the version
  `shell-lint.yml` uses, actionlint, gitleaks, and the usual hygiene hooks (merge conflicts, large files, end of
  file). Every hook is pinned to a commit SHA. pre-commit builds each hook's own environment, with the default
  ruff in it, so nothing is installed on the machine and no second ruff can drift. A project that pins its own
  ruff gives the hook `--project-ruff`, and the hook runs that release ("The linters stay current", above). The
  machine needs pre-commit and Python 3.12 or newer.
- **The hook's commit follows the release.** A project pins `murphy360/standards` in its pre-commit config at the
  commit of the release its CI calls, so the hook and CI hold the same rules. When the standard releases, the
  project moves that `rev` to the new release's commit; the template names it. A project whose lint job passes
  `paths` or `working-directory`, or runs without `format-check`, or in `mode: changed`, or with `project-ruff` or
  `ruff-version`, gives the hook the same in `args` (`--paths src`, `--root <directory>`, `--no-format`, no
  `--all`, `--project-ruff`, `--ruff-version <version>`).
- **A secret is stopped before the commit, and again in CI.** The gitleaks hook refuses a commit that stages a
  credential. `secret-scan` scans the commits of a pull request or a push with the same gitleaks version, so a
  commit made without the hook is stopped before it is merged. A finding names the file, the line and the rule,
  never the secret. `full-history: true` scans every commit: once when a project adopts it, or on a schedule. A
  secret that reached GitHub is revoked first; its fingerprint then goes in `.gitleaksignore`, and a false
  positive goes there too, or in `.gitleaks.toml`, with its reason. pre-commit builds gitleaks from source at
  the pin on first use, and fetches Go when the machine has none.
- **GitHub's own secret scanning is on where GitHub offers it.** GitHub's documentation (read 2026-11-13) gives
  secret scanning and push protection free to a public repository, and to a private one only under an
  organization that pays for GitHub Secret Protection, or under an Enterprise Managed User. A private repository
  on an ordinary personal account gets neither, so there the hook and `secret-scan` are the only check.
  `tools/repo-settings.sh` asks for both, and warns and carries on where GitHub refuses.
- **A test fixture is made up.** It is never a saved response from a real account: once committed, personal data
  is in git for good, and no scanner knows a real name from an invented one (`templates/AGENTS.md`).
- **The pull request body follows "Definition of done".** `templates/pull_request_template.md`.

## Adopting it in a project

A new Python project starts from the template repository, `gh repo create <name> --template
murphy360/template-python`, which already holds the steps below. An existing project takes them by hand:

1. Copy `templates/ci.yml` to `.github/workflows/ci.yml` and keep the jobs that apply. Its `checks` job runs the
   quick checks ("One job runs the quick checks", above). That is all the code rules need: the lint has the base
   it compares with.
2. Copy `templates/dependabot.yml` to `.github/dependabot.yml`; `standards-check` names any ecosystem left out.
3. Copy `templates/AGENTS.md` to `AGENTS.md` and `templates/CLAUDE.md` to `CLAUDE.md`. In `AGENTS.md`, fill in
   the title and "This project", and keep the two marker comments: the text between them is the standard's
   ("Staying current", below). `CLAUDE.md` stays as it is: it imports `AGENTS.md`.
4. Copy `templates/pull_request_template.md` to `.github/pull_request_template.md`.
5. Copy `templates/pre-commit-config.yaml` to `.pre-commit-config.yaml`; `pip install pre-commit && pre-commit
   install` once per checkout. It holds the code rules as a hook, so nothing else is installed: no ruff, and no
   checkout of this repository. Turn the matching checks on in the `checks` step's `with:` once both templates are
   in (`check-pr-template: true`, `check-pre-commit-config: true`): they default off so a project pinned at `@v2`
   sees no new failure until it opts in.
6. Pin every other action in the project's workflows to a commit SHA.
7. See what is left: `pre-commit run --all-files`. The `code-rules` hook prints every finding CI would print.
8. If anything is left, set `mode: changed` on the `checks` step, and `args: []` on the `code-rules` hook so that it
   checks the same files: the ones the branch changes. The last line of each run counts what is left. Remove both
   when it reads zero.
9. With `format-check: true`, Python files must be formatted too. Re-format everything once, in one `ruff format`
   pull request, and set `format-only: true` on that run (for example from a `format-only` label): it proves that
   the pull request changes only the layout of Python files.
10. `tools/repo-settings.sh <owner>/<repo>` (`--dry-run` first): squash merge only, auto-merge and "Update branch"
    on, Dependabot security updates on, secret scanning and push protection on where GitHub offers them, and the
    branch and tag rulesets (`templates/rulesets/main.json`,
    `templates/rulesets/version-tags.json`) applied to the repository.
11. Copy `templates/ISSUE_TEMPLATE/` to `.github/ISSUE_TEMPLATE/` (`ticket.md` and `config.yml`), then set
    `check-ticket-template: true` on the `checks` step. Give each milestone a RUN ORDER ("Tickets and
    milestones", below).
12. Copy `templates/claude/settings.json` to `.claude/settings.json` and `templates/claude/guard.py` to
    `.claude/guard.py`, and commit both: a Claude Code session started in the project then refuses the never-rules
    ("The rules", above). The guard needs `python3`. A project that already has a `.claude/settings.json` adds the
    `deny` rules and the `PreToolUse` hook to its own. Then turn on `check-agent-settings: true` in the
    `checks` step's `with:`: it requires the settings file, its guard hook, and the guard file the hook names.
13. Pin ruff in the project, so that a new ruff arrives as one pull request of its own. Write `ruff==<version>` in
    `requirements-dev.txt`, starting from the version the lint prints today. Set `project-ruff: true` on the
    `checks` step and `args: [--all, --project-ruff]` on the `code-rules` hook, and set the `ruff-pre-commit` rev to the
    commit of the same release. `templates/dependabot.yml` already watches pip, so Dependabot opens a pull request
    when ruff releases: CI lints it with the new release, and the fixes go in that pull request. Turn on
    `check-ruff-version: true` on the same step, which fails when those places disagree.

A JavaScript or TypeScript project names `node-lint` in `checks` in place of `python-lint`, or runs it as a second
`checks` step in its own directory. Copy
`templates/eslint.config.mjs` to `eslint.config.mjs`. Add `eslint`, `@eslint/js`, `typescript-eslint` and `prettier`
to devDependencies and commit the lockfile. `npm ci` installs them, and Dependabot updates them. The same two modes
apply, and `format-check: true` runs `prettier --check` on the same files. In `.pre-commit-config.yaml`, use the
`code-rules-node` hook in place of `code-rules` (add `--prettier` to its `args` with `format-check: true`), run
`npm ci`, and see what ESLint reports with `pre-commit run --all-files`.

The type check is a job of its own; `templates/ci.yml` shows it, commented out. A Python project passes
`install-command`: the command that installs what its code imports into the job's Python. That is
`pip install -r requirements.txt`, `pip install -e .`, or `pip install -e ".[dev]"` when the tests are checked too.
Without it every third-party import is a finding. A project that imports only the standard library passes nothing.
pyright reads the project's `pyrightconfig.json`, or `[tool.pyright]` in `pyproject.toml`. With neither it checks
every Python file. A TypeScript project passes `language: typescript`, and needs a `tsconfig.json` and `typescript`
in devDependencies. To see the findings before the first push, run the checker itself: `npx pyright@<version>`
with the version `type-check.yml` pins, or `npx tsc --noEmit`. With no pyright config, pyright alone runs in its own
default mode, `standard`, which reports a little more than `basic`; a project that sets `typeCheckingMode` in its
config has one mode in both places. A project with findings left narrows `include` to the directories that are
clean, writes the reason beside it, and widens it as the rest is fixed. This repository does so itself, in
`pyproject.toml`.

A project that still has a `code_rules_baseline.json` from `v1.0.0` can delete it: the tool no longer reads it, and
says so in a note.

### Staying current

A copy falls behind when its template changes. The shared text of `AGENTS.md` is everything between two marker
comments, `<!-- standards:shared:begin -->` and `<!-- standards:shared:end -->`, which a rendered page does not show.
The title and "This project" are outside them. Two things keep a project's copy current:

- **The sync tool.** `python3 <standards>/tools/sync_templates.py <checkout>` rewrites the text between the markers
  from the template and touches nothing outside them. It prints what it changed. `--check` changes nothing and
  exits 1 if something would change. `--all` also copies the pull request template, the pre-commit config,
  `dependabot.yml` and the issue templates (`ticket.md` and `config.yml`, when the project has no issue template
  of its own) when the project lacks them; an existing file is kept unless `--overwrite` is given. The
  rulesets are not copied: `tools/repo-settings.sh` applies them. `<standards>` is a checkout at the version the
  project's CI calls: `git clone --depth 1 --branch v2 https://github.com/murphy360/standards /tmp/standards`.
- **The check.** `check-templates: true` in `standards-check`'s `with:` fails a pull request while the shared text
  differs from the template of the version the workflow is called at. It names the file, shows the first lines
  that differ and prints the command above. Trailing whitespace and line endings do not count. The same input
  requires a CI job named `result`, and a `CLAUDE.md` beside an `AGENTS.md` to import it. It defaults off.

An instruction file copied before the markers existed has none. Run the tool once with
`--shared-until "## This project"`: everything under the title down to that heading is replaced by the shared text,
between markers, so move anything of the project's own under the heading first. Without that flag the tool changes
nothing in such a file and says what to do: it never guesses where a project's own text starts.

A project that copied the template before `AGENTS.md` existed keeps its instructions in `CLAUDE.md`. It still
passes: `standards-check` accepts either file, and the tool rewrites the shared text where it is. Nothing is moved
unless the project asks, with `--to-agents-md`:

- **`CLAUDE.md` alone.** It becomes `AGENTS.md`, byte for byte, so the title and "This project" are kept. The
  shared text is then brought up to date there, and `CLAUDE.md` becomes the template's import. A file without
  markers moves as it is; add `--shared-until "## This project"` to give it the shared text in the same run.
- **Both files, and `CLAUDE.md` imports `AGENTS.md`**, or **`AGENTS.md` alone.** Nothing to move. `--all` gives an
  `AGENTS.md` that stands alone the `CLAUDE.md` that imports it.
- **Both files, and no import.** The tool never merges two files. If both hold the same text, `CLAUDE.md` becomes
  the import. If they differ, it moves nothing and says what to do by hand: put what every agent needs in
  `AGENTS.md`, and leave in `CLAUDE.md` the line `@AGENTS.md` and what only Claude needs.

`--check` shows the move and changes nothing.

Run the tool whenever the project moves to a new version of the standard. A project at `@v2` moves when the `v2`
tag does. With `check-templates` on, the first pull request after a release that changed the shared text fails
until the tool has run. The rehearsal (`tools/fleet.py --run-tools <ref>`) names those projects before the tag moves.

### Which tools read `AGENTS.md`

`AGENTS.md` is the name the agent tools agreed on (https://agents.md): plain Markdown at the root of a repository.
Read in each tool's own documentation on 2026-11-13: Claude Code loads `AGENTS.md` through the `@AGENTS.md` import
in `CLAUDE.md`, and from v2.1.277 reads it directly when a project has no `CLAUDE.md`, though some sessions cannot,
so the template keeps the import (https://code.claude.com/docs/en/memory); Codex reads `AGENTS.md` before any work;
GitHub Copilot reads it, the nearest one in the tree first; Jules looks for it in the root. Not verified: Grok,
whose documentation does not name the file, and the other tools https://agents.md lists (Cursor, Gemini CLI, Aider
and more), which were taken from that list only. A project that has both files and no import keeps two files in
step, because Claude Code then reads `CLAUDE.md` alone.

### Tickets and milestones

GitHub reads issue templates from `.github/ISSUE_TEMPLATE` on the default branch (its documentation, read
2026-11-13). A Markdown template needs `name` and `about` in its front matter. The standard's template sets
nothing else: no label and no assignee, because a project may not have them. Once the copy is merged, a new issue
offers "Ticket" and a blank issue. The blank one stays for the owner's quick notes (`config.yml`).

The ticket keeps every section, with "none" where one has nothing to say. "Acceptance" is the checklist someone
else verifies. "Done", under "For the implementer", says where the work stops: what the one pull request holds,
and what is left to the owner. "Owner decision" lists what the owner reserves. With "none" there, nothing is
reserved.

GitHub has no template for a milestone, so the description is written by hand in this shape:

```text
<one or two sentences: what this milestone is for, and where it came from>

RUN ORDER

1. #<number> <title>
2. #<number> <title>
3. #<number> <title>

Tickets assigned to the owner are the owner's. An agent takes the first open ticket that is not.
```

An agent takes its work from the order, so a ticket that is not in it is not taken: a new ticket gets its line
when it is filed. `gh` has no milestone command, so the description is set through the API:
`gh api -X PATCH repos/<owner>/<repo>/milestones/<number> -F description=@milestone.txt`.
### The refusals in another agent tool

The settings file is Claude Code's, read from the project a session starts in. The same hook in a settings source
of the session itself guards every clone that session works in (verified by passing the hook with `--settings`;
the user file `~/.claude/settings.json` takes the same keys, and is the owner's to change). For every tool,
GitHub refuses a push and a forced push to main where the ruleset is applied, and an agent identity holds no
bypass (`templates/rulesets/main.json`, "Agent identities", below); nothing on GitHub's side stops `git stash`, a
reset or `--no-verify`. Codex, Gemini CLI, Cursor and GitHub Copilot each document a hook that runs before a shell
command and can refuse it: Codex `PreToolUse` in `hooks.json` (exit code 2, the reason on stderr), Gemini CLI
`BeforeTool` in its `settings.json` (the shell tool is `run_shell_command`; exit code 2), Cursor
`beforeShellExecution` in `.cursor/hooks.json` (exit code 2, or `"permission": "deny"`), Copilot `preToolUse` in
`.github/hooks/*.json` (`"permissionDecision": "deny"`). Read in each tool's documentation on 2026-11-13. Not
verified: none of them was run with `guard.py`, and each sends its own input, so the guard may need a small
adapter there.

### The rulesets' bypass actor

Both rulesets name the owner as an `actor_type: "User"` bypass actor by numeric GitHub id, not a `RepositoryRole`:
GitHub's own schema says `OrganizationAdmin` does not apply to a personal repository, and every murphy360 project is
one. A project under a different owner changes `actor_id` in both files to that owner's numeric id
(`curl https://api.github.com/users/<login>`, the `id` field).

In `main.json` the bypass is `bypass_mode: "pull_request"`. The owner can merge a pull request whose checks failed,
but a direct push to main is refused, from the owner's login too. In `version-tags.json` the bypass is `always`:
the owner moves a major tag by hand when releasing. A session that signs in with the owner's login still holds
both bypasses, so agent sessions are meant to sign in as an identity of their own, with the write role and no bypass.

### Agent identities

Each agent (Claude, Grok, Codex, Copilot, Jules) signs in to GitHub as its own GitHub App, never with the owner's
login. Its commits and pull requests show as `<owner>-<agent>[bot]`, and it holds neither ruleset's bypass. An
agent that already pushes through its own App, from its vendor, needs nothing more; check it on a real pull request.

For an agent that would otherwise use the owner's login:

1. `python3 tools/agent_app.py register claude` prints a link. Open it, create the App, generate a private key,
   and install the App on the repositories the agent works in. Keep the key outside every repository, readable
   only by the agent (`chmod 600`).
2. The agent's session sets `AGENT_APP_ID` and `AGENT_APP_KEY` (the key's path), then
   `export GH_TOKEN="$(python3 tools/agent_app.py token)"` and `gh auth setup-git`. The token lasts an hour; run
   it again for a longer session.
3. `python3 tools/agent_app.py git-identity <app-slug>` prints the `git config` lines that make the App the
   commits' author.
4. An agent the owner lets cut versions is named to the tag ruleset:
   `tools/repo-settings.sh <owner>/<repo> --release-app <app-id>`. It moves a version tag only when the owner
   says so; the ruleset keeps every other agent out.

A key on a machine is as safe as the machine: an agent that can read the owner's own `gh` login can still use it.
Give each agent its own key and nothing else.

### From v1 to v2

v2 is a breaking release. What changed, and what a project must do:

- **The ratchet baseline is gone.** `code_rules_baseline.json` and `--update` no longer exist. Delete the file.
- **`mode: all` is the default.** A finding in any file now fails every run. A project with findings left sets
  `mode: changed` until it has none.
- **`# noqa` is not allowed.** A finding under a `# noqa` is reported, and the comment is a problem itself. Fix
  the finding and remove the comment.
- **`--report-only` is gone.** `pre-commit run --all-files` lists what is left; it fails while anything is.
- **New:** each finding is printed with its line, rule and fix; `paths` limits the check to some directories; and
  `format-only: true` proves a pull request only re-formats.

## Versions

Projects pin `@v2`. A compatible change is released as `v2.x.y` and the `v2` tag moves to it; a breaking change is
`v3`, with release notes saying what a project must change. The tag moves only to a commit whose CI passed here.
A change is compatible when no adopter's next ordinary pull request newly fails under it. The step before a release
is the rehearsal: `python3 tools/fleet.py --run-tools <ref>` runs the new tools, as they are at `<ref>`, against
every repository that calls the standard's workflows.

### The fleet report

`python3 tools/fleet.py` prints one table, a row per repository of the owner that is not archived. A row shows the
workflows the repository calls and at which ref (`v2`, an older major, or a commit SHA), its lint mode, whether a
caller passes `format-only`, the instruction file it has (`AGENTS.md`, `CLAUDE.md` or `both`), the other
templates it has, whether a CI job is named `result`, whether the `main` ruleset is
applied, and its open Dependabot pull requests with the age of the oldest. A repository that runs the checks in one
job is an adopter like any other: the `checks` column shows the ref of its `checks` step, and each check the step
names shows that ref in its own column, so the lint mode and `format-only` read the same for both ways of calling
a check. Under the table it counts the adopters at
the current major, at an older major and at a commit SHA, the adopters with the one `checks` job, and the
repositories outside the standard. `--json` prints
the same for a program, `--repo <name>` keeps one repository, and `--owner` names another owner. It needs `gh`,
signed in.

A repository that stays outside the standard says so with the repository topic `no-standards`
(`gh repo edit <owner>/<repo> --add-topic no-standards`). No list of names is kept here.

The report is printed and never written to a file. This repository is public and most of the repositories the
report names are private: do not commit its output, and do not paste it in a pull request or a ticket. Counts are
fine.

`--run-tools <ref>` is the rehearsal. It takes `tools/` and `templates/` of this checkout at `<ref>` (a tag, a branch or a commit),
clones each adopter into a temporary directory, and runs the tools the way the adopter's workflows would:
`code_rules.py --all` for a caller of `python-lint`, with its `format-check`, `paths` and `working-directory`, and
`check_standards.py` for a caller of `standards-check`, with the checks it turned on. A `checks` step counts as a
caller of each check it names, with the step's inputs. It prints pass or FAIL for
each repository with the last line of each tool, deletes the clones, and exits 1 when a repository fails. `debt`
is a failure of `code_rules.py --all` in a repository that lints in `mode: changed`: its pull requests check only
the files they touch. Run it at the current tag first to see what fails already. `node-lint` is not rehearsed.

`code_rules.py` needs ruff. Where ruff is not installed the rehearsal stops and prints this command, which runs it
in Docker from the root of this repository:

```sh
GH_TOKEN="$(gh auth token)" docker run --rm -e GH_TOKEN \
  -e DEBIAN_FRONTEND=noninteractive -e PIP_ROOT_USER_ACTION=ignore \
  -v "$PWD:/standards:ro" -w /standards python:3.12-slim sh -c '
    apt-get -qq update && apt-get -qq install -y git gh >/dev/null &&
    python tools/ruff_version.py --install &&
    git config --global --add safe.directory /standards &&
    python tools/fleet.py --run-tools <ref>'
```
