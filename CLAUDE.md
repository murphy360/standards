# Working on murphy360/standards

This repository is the standard every murphy360 project follows (README.md). Changes here reach every project, so:

- Work on a branch and open a PR; never push to main. The owner merges.
- A change to a reusable workflow or a tool is tested here first (ci.yml runs this repository's own workflows on
  itself, and `pytest tests` covers the tools).
- Versioning: projects pin `@v1`. A compatible change moves the `v1` tag after a `v1.x.y` release; a breaking change
  is `v2`, with release notes saying what a project must change. Never move a tag to a commit that fails CI.
- Every action stays pinned to a commit SHA with its version in a comment; Dependabot updates them.
- Keep the templates (templates/) in step with the workflows: a project copies them.
- Prose: short sentences, no em-dashes.
