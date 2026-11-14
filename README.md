# murphy360/standards

The one standard every murphy360 project follows, so that no project rebuilds the same tooling. A project depends on
it by calling its workflows at a pinned version tag (`@v2`), copying its templates, and keeping its rules.

## What is here

| Path | What it is |
|---|---|
| `.github/workflows/standards-check.yml` | Fails a project that lacks a `.github/dependabot.yml` covering its ecosystems, pins an action to anything but a commit SHA, or has no `CLAUDE.md`; `check-templates: true` also fails one whose copy of the shared text of `CLAUDE.md` fell behind the template, or whose CI has no job named `result` |
| `.github/workflows/python-lint.yml` | ruff with the project's config plus the standard complexity limits, no `# noqa`, and the file-size limit (`tools/code_rules.py`), on every file (`mode: all`, the default) or on every file a change touches (`mode: changed`); `format-check: true` adds `ruff format --check` in the same scope; `paths` limits it to some directories; `format-only: true` proves a pull request only re-formats |
| `.github/workflows/node-lint.yml` | ESLint with the project's config plus the standard complexity limits, and the file-size limit (`tools/code_rules.py --eslint`), on every file (`mode: all`, the default) or on every file a change touches (`mode: changed`); `format-check: true` adds `prettier --check` in the same scope; `paths` limits it to some directories. Added in v2.2.0 |
| `.github/workflows/type-check.yml` | pyright, pinned, on a Python project (`basic` mode unless the project's pyright config sets one), or the project's own `tsc --noEmit` on a TypeScript one (`language: typescript`); `install-command` installs what the code imports, so that pyright sees it; each finding is printed with its line, rule and fix (`tools/type_check.py`). No baseline: a project with debt narrows `include` in its own config |
| `.github/workflows/shell-lint.yml` | shellcheck on every tracked `*.sh`, pinned (v0.11.0, the version the pre-commit template pins) |
| `.github/workflows/actionlint.yml` | actionlint on the project's workflows |
| `.github/workflows/secret-scan.yml` | gitleaks, pinned by digest, on the commits of a pull request or a push; `full-history: true` scans every commit. A finding names the file, the line and the rule, never the secret |
| `.github/workflows/test-docker.yml` | builds the project's image and runs its tests inside it |
| `.github/workflows/image.yml` | multi-arch image (amd64 and arm64 on native runners) pushed to ghcr.io as one manifest list |
| `tools/code_rules.py` | the code rules: `--all` checks that every file is clean; without it, every file a change touches; each finding printed with its line, rule and fix; `--eslint` adds the project's ESLint and `--prettier` adds `prettier --check`; `--silencers` checks that every skip, xfail, type ignore and the like carries its reason, and counts them. CI runs it, and a project runs it as a pre-commit hook |
| `tools/type_check.py` | the type check `type-check` runs: pyright, or the project's `tsc --noEmit` with `--language typescript`; it reads the checker's output and prints each finding the way `code_rules.py` does |
| `.pre-commit-hooks.yaml` | the code rules as pre-commit hooks: `code-rules` is what `python-lint` runs and `code-rules-node` is what `node-lint` runs. pre-commit installs this repository (`pyproject.toml`: the modules under `tools/` and the pinned ruff) into the hook's own environment, so a project installs nothing |
| `tools/check_standards.py` | the checks `standards-check` runs; five more (`--require-pr-template`, `--require-pre-commit`, `--require-ticket-template`, `--require-current-templates`, `--require-agent-settings`) are opt-in |
| `tools/sync_templates.py` | brings a project's copy of the templates up to date: rewrites the shared text of its `CLAUDE.md` from the template and touches nothing else; `--check` changes nothing and exits 1 if something would change; `--all` also copies the templates the project lacks ("Staying current", below) |
| `tools/agent_app.py` | gives each agent its own GitHub identity (a GitHub App): the link that creates it, a one-hour token, the commit author lines |
| `tools/repo-settings.sh` | applies a repository's settings and its branch ruleset (below), and turns on GitHub's secret scanning and push protection where GitHub offers them; `--dry-run` prints what it would send |
| `tools/fleet.py` | the fleet report: one row per repository, with the version of the standard it follows and what it lacks; `--run-tools <ref>` is the rehearsal before a release ("The fleet report", below) |
| `templates/ci.yml` | a project's CI calling the workflows: one run per ref, a fixed-name `result` job to require |
| `templates/eslint.config.mjs` | the ESLint config a TypeScript project starts from |
| `templates/dependabot.yml` | Dependabot for actions, pip, docker and npm, weekly and grouped |
| `templates/CLAUDE.md` | how an agent session works in a project: worktrees, Docker tests, the definition of done; the shared text sits between two marker comments, so a tool can compare and rewrite it |
| `templates/pull_request_template.md` | the pull request body from "Definition of done" |
| `templates/ISSUE_TEMPLATE/ticket.md` | the ticket, an implementer's whole brief: the problem or the owner's direction, what to build, the acceptance checklist, "For the implementer", what is out of scope, the decisions the owner reserves |
| `templates/ISSUE_TEMPLATE/config.yml` | keeps the blank issue beside the ticket template, for a quick note |
| `templates/pre-commit-config.yaml` | the code rules (the `code-rules` hook), ruff, ruff format, shellcheck, actionlint and gitleaks, each pinned at the version CI uses, so a finding is caught before the push and a secret before the commit |
| `templates/claude/settings.json` | what a Claude Code session refuses, copied to `.claude/settings.json`: deny rules for the simple forms of the never-rules, and a `PreToolUse` hook that runs the guard before every shell command. Nothing else: no model, no allow list |
| `templates/claude/guard.py` | the guard, copied to `.claude/guard.py`: it refuses `git stash`, a push to the default branch, a reset onto another commit, an admin merge, `--no-verify` and a forced push, says what to do instead, and runs `pre-commit run --all-files` before a push |
| `templates/rulesets/main.json` | the branch ruleset `tools/repo-settings.sh` applies: pull request required, linear history, no force push, the `result` job required; the owner can merge a pull request past a failed check, and nobody can push to main directly |
| `templates/rulesets/version-tags.json` | the tag ruleset `tools/repo-settings.sh` applies: only the owner can create, move or delete a `v*` tag |
| `tests/fixtures/` | the three small projects (Node, Python, Docker) this repository's own CI runs `node-lint`, `type-check`, `test-docker` and `image` on, so every workflow runs here before a tag moves |

The check workflows above (standards-check, python-lint, node-lint, type-check, shell-lint, actionlint, secret-scan) take `runs-on`: GitHub's
`ubuntu-latest` by default, or a JSON list of labels for a self-hosted runner (`'["ci", "X64"]'`). A private
project pays for hosted minutes, and each job is billed at least one minute however short, so a project with its own
runners passes them here. The runner needs Docker; on a self-hosted one each job first takes ownership of files a
previous job's containers left in the workspace.

The workflows that run a tool (standards-check, python-lint, node-lint, type-check) take the tool from the commit they were
called at. A project at `@v2` runs the tools of `v2`, a project pinned at a commit runs the tools of that commit, and
this repository's own CI runs the tools of the pull request. GitHub names that commit to the workflow as
`job.workflow_sha`. The run prints it: `standards tools: murphy360/standards@<commit> (<workflow ref>)`. The tools are
fetched into the runner's temp directory, so nothing is added to the project's workspace. The `standards-ref` input
is deprecated and has no default: a project that still passes it keeps working, and it is read only if GitHub does
not name the commit.

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
- **Agents work the same way everywhere.** `CLAUDE.md` from the template: draft until green, ready once, no
  `git stash` across worktrees, never reset onto a moved base.
- **Work arrives as a ticket, in the milestone's RUN ORDER.** The ticket is the implementer's whole brief, so
  every ticket has the sections of `templates/ISSUE_TEMPLATE/ticket.md`. A milestone's description carries a
  numbered RUN ORDER of its tickets. A ticket assigned to the owner is the owner's. An agent takes the first open
  ticket in the order that is not. Where a decision the owner reserved is unclear, the agent asks in one comment
  and takes the next ticket. It never guesses.
- **The never-rules are refused, not only written.** A long session may no longer have `CLAUDE.md` in view, so
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
  Every job carries a `timeout-minutes` (a job calling a reusable workflow cannot set its own, so the reusable
  workflow's job carries it instead), so a stuck job no longer holds a runner for six hours. A single job named
  `result` depends on the rest; it is the one check a ruleset requires, so a cancelled or skipped job elsewhere
  never leaves the required check missing. `templates/rulesets/main.json` and `tools/repo-settings.sh` turn this
  into branch protection: pull request required, linear history, no force push, `result` required, the owner can
  bypass.
- **Local equals CI: one command before a push.** `pre-commit run --all-files` runs what the lint in CI runs.
  `templates/pre-commit-config.yaml` holds the `code-rules` hook (`.pre-commit-hooks.yaml`): the code rules on
  every file, with the complexity limits, the `# noqa` rule, the size limit and the format, printing the lines CI
  prints. It also holds ruff and ruff format at the pin `python-lint.yml` uses, shellcheck at the version
  `shell-lint.yml` uses, actionlint, gitleaks, and the usual hygiene hooks (merge conflicts, large files, end of
  file). Every hook is pinned to a commit SHA. pre-commit builds each hook's own environment, with the pinned
  ruff in it, so nothing is installed on the machine and no second ruff can drift. The machine needs pre-commit
  and Python 3.12 or newer.
- **The hook's commit follows the release.** A project pins `murphy360/standards` in its pre-commit config at the
  commit of the release its CI calls, so the hook and CI hold the same rules. When the standard releases, the
  project moves that `rev` to the new release's commit; the template names it. A project whose lint job passes
  `paths` or `working-directory`, or runs without `format-check`, or in `mode: changed`, gives the hook the same in
  `args` (`--paths src`, `--root <directory>`, `--no-format`, or no `--all`).
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
  is in git for good, and no scanner knows a real name from an invented one (`templates/CLAUDE.md`).
- **The pull request body follows "Definition of done".** `templates/pull_request_template.md`.

## Adopting it in a project

A new Python project starts from the template repository, `gh repo create <name> --template
murphy360/template-python`, which already holds the steps below. An existing project takes them by hand:

1. Copy `templates/ci.yml` to `.github/workflows/ci.yml` and keep the jobs that apply. That is all the code rules
   need: the lint job fetches the base it compares with.
2. Copy `templates/dependabot.yml` to `.github/dependabot.yml`; `standards-check` names any ecosystem left out.
3. Copy `templates/CLAUDE.md` to `CLAUDE.md` and fill in "This project". Keep the two marker comments: the text
   between them is the standard's ("Staying current", below).
4. Copy `templates/pull_request_template.md` to `.github/pull_request_template.md`.
5. Copy `templates/pre-commit-config.yaml` to `.pre-commit-config.yaml`; `pip install pre-commit && pre-commit
   install` once per checkout. It holds the code rules as a hook, so nothing else is installed: no ruff, and no
   checkout of this repository. Turn the matching checks on in `standards-check`'s `with:` once both templates are
   in (`check-pr-template: true`, `check-pre-commit-config: true`): they default off so a project pinned at `@v2`
   sees no new failure until it opts in.
6. Pin every other action in the project's workflows to a commit SHA.
7. See what is left: `pre-commit run --all-files`. The `code-rules` hook prints every finding CI would print.
8. If anything is left, set `mode: changed` on the lint job, and `args: []` on the `code-rules` hook so that it
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
    `check-ticket-template: true` on `standards-check`. Give each milestone a RUN ORDER ("Tickets and milestones",
    below).
12. Copy `templates/claude/settings.json` to `.claude/settings.json` and `templates/claude/guard.py` to
    `.claude/guard.py`, and commit both: a Claude Code session started in the project then refuses the never-rules
    ("The rules", above). The guard needs `python3`. A project that already has a `.claude/settings.json` adds the
    `deny` rules and the `PreToolUse` hook to its own. Then turn on `check-agent-settings: true` in
    `standards-check`'s `with:`: it requires the settings file, its guard hook, and the guard file the hook names.

A JavaScript or TypeScript project calls `node-lint` instead of `python-lint`, or as a job of its own. Copy
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

A copy falls behind when its template changes. The shared text of `CLAUDE.md` is everything between two marker
comments, `<!-- standards:shared:begin -->` and `<!-- standards:shared:end -->`, which a rendered page does not show.
The title and "This project" are outside them. Two things keep a project's copy current:

- **The sync tool.** `python3 <standards>/tools/sync_templates.py <checkout>` rewrites the text between the markers
  from the template and touches nothing outside them. It prints what it changed. `--check` changes nothing and
  exits 1 if something would change. `--all` also copies the pull request template, the pre-commit config and
  `dependabot.yml` when the project lacks them; an existing file is kept unless `--overwrite` is given. The
  rulesets are not copied: `tools/repo-settings.sh` applies them. `<standards>` is a checkout at the version the
  project's CI calls: `git clone --depth 1 --branch v2 https://github.com/murphy360/standards /tmp/standards`.
- **The check.** `check-templates: true` in `standards-check`'s `with:` fails a pull request while the shared text
  differs from the template of the version the workflow is called at. It names the file, shows the first lines
  that differ and prints the command above. Trailing whitespace and line endings do not count. The same input
  requires a CI job named `result`. It defaults off.

A `CLAUDE.md` copied before the markers existed has none. Run the tool once with
`--shared-until "## This project"`: everything under the title down to that heading is replaced by the shared text,
between markers, so move anything of the project's own under the heading first. Without that flag the tool changes
nothing in such a file and says what to do: it never guesses where a project's own text starts.

Run the tool whenever the project moves to a new version of the standard. A project at `@v2` moves when the `v2`
tag does. With `check-templates` on, the first pull request after a release that changed the shared text fails
until the tool has run. The rehearsal (`tools/fleet.py --run-tools <ref>`) names those projects before the tag moves.

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
caller passes `format-only`, the templates it has, whether a CI job is named `result`, whether the `main` ruleset is
applied, and its open Dependabot pull requests with the age of the oldest. Under the table it counts the adopters at
the current major, at an older major and at a commit SHA, and the repositories outside the standard. `--json` prints
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
`check_standards.py` for a caller of `standards-check`, with the checks it turned on. It prints pass or FAIL for
each repository with the last line of each tool, deletes the clones, and exits 1 when a repository fails. `debt`
is a failure of `code_rules.py --all` in a repository that lints in `mode: changed`: its pull requests check only
the files they touch. Run it at the current tag first to see what fails already. `node-lint` is not rehearsed.

`code_rules.py` needs ruff. Where ruff is not installed the rehearsal stops and prints this command, which runs it
in Docker from the root of this repository:

```sh
GH_TOKEN="$(gh auth token)" docker run --rm -e GH_TOKEN \
  -e DEBIAN_FRONTEND=noninteractive -e PIP_DISABLE_PIP_VERSION_CHECK=1 \
  -v "$PWD:/standards:ro" -w /standards python:3.12-slim sh -c '
    apt-get -qq update && apt-get -qq install -y git gh >/dev/null &&
    pip install -q --root-user-action=ignore ruff==0.6.9 &&
    git config --global --add safe.directory /standards &&
    python tools/fleet.py --run-tools <ref>'
```
