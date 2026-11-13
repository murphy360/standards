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
- ruff is one version: `pyproject.toml`, `python-lint.yml`, `node-lint.yml` and both pre-commit configs change
  together (a test fails otherwise).
- Keep the templates (templates/) in step with the workflows: a project copies them.
- Prose: short sentences, no em-dashes.
