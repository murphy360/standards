# Working on murphy360/standards

This repository is the standard every murphy360 project follows (README.md). Changes here reach every project, so:

- Work on a branch and open a PR; never push to main. The owner merges.
- A change to a reusable workflow or a tool is tested here first (ci.yml runs this repository's own workflows on
  itself, and `pytest tests` covers the tools).
- A new workflow comes with its fixture (a small project in `tests/fixtures/`) and a job in ci.yml that `result` needs.
- Versioning: projects pin the major tag (`@v2`). A compatible change moves that tag after a `v2.x.y` release; a
  breaking change is the next major (`v3`), with release notes saying what a project must change. Never move a tag to a commit that fails CI.
- Every action stays pinned to a commit SHA with its version in a comment; Dependabot updates them.
- After a release, set the `murphy360/standards` rev in `templates/pre-commit-config.yaml` to the released commit,
  with its version in the comment: a commit cannot name itself, so the template follows one step behind.
- The default ruff is written once, in `pyproject.toml` (`dependencies`). The workflows, the hook and CI read it
  there (`tools/ruff_version.py`). Never write a ruff version anywhere else.
- A pull request from Dependabot here:
  - *ruff in `pyproject.toml`*: the default for every project that pins none. It is not an ordinary update. The
    tests fail until the `ruff-pre-commit` rev in `.pre-commit-config.yaml` and `templates/pre-commit-config.yaml`
    is the commit of the same release, and the failure names the command that finds it. Every file here must be
    clean under the new release. Run the rehearsal (`tools/fleet.py --run-tools`) before merging: a project it
    fails makes this a breaking change, which waits for the next major.
  - *an image in `tools/images/`*: shellcheck, actionlint and gitleaks each have a hook in both pre-commit configs
    at the same release. The tests fail until the hook's rev is the commit of that release, and say which. The
    README row of `shell-lint.yml` names the shellcheck version. alpine needs nothing else.
  - *ruff in `tests/fixtures/python-project`*: the fixture's own pin. CI's `lint-pinned` job shows what the new
    release finds in the fixture. It must stay newer than the default.
  - *actions, the Node and Docker fixtures, and the Python fixture's other package*: nothing else to change.
- Keep the templates (templates/) in step with the workflows: a project copies them.
- Prose: short sentences, no em-dashes.
