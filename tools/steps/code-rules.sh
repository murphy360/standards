#!/usr/bin/env bash
# The "Code rules" step: python-lint.yml's and node-lint.yml's last step, and the lint parts of the `checks`
# action. It turns the inputs into flags and runs tools/code_rules.py, which checks every file in PATHS.
#
#   code-rules.sh python     # ruff, no noqa, complexity, file size, silencers, format
#   code-rules.sh node       # the same with --eslint: the project's ESLint, and prettier with FORMAT_CHECK
#
# The inputs are variables, as a workflow or an action passes them. A boolean reads `true` or `false`.
#   FORMAT_CHECK       `true`: also ruff format --check, or prettier --check for node
#   NO_INLINE_CONFIG   node only: `false` lets an eslint-disable comment hide a finding (--inline-config)
#   SILENCERS          `false`: no check that every silencer carries its reason (--no-silencers)
#   PATHS              space-separated directories or files (default: every tracked file)
# CODE_RULES_PROJECT_RUFF and CODE_RULES_RUFF_VERSION reach the tool through the environment (ruff_version.py).
#
# It runs in the directory the lint checks, inside the project's checkout, and exits as code_rules.py does.
set -e
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "${1:-}" in
  python)
    args=()
    [ "$FORMAT_CHECK" = true ] || args+=(--no-format)
    ;;
  node)
    args=(--eslint --no-format)
    [ "$FORMAT_CHECK" != true ] || args+=(--prettier)
    [ "$NO_INLINE_CONFIG" != false ] || args+=(--inline-config)
    ;;
  *)
    echo "usage: code-rules.sh python|node" >&2
    exit 2
    ;;
esac
[ "$SILENCERS" != false ] || args+=(--no-silencers)
read -r -a only <<< "$PATHS"
[ ${#only[@]} -eq 0 ] || args+=(--paths "${only[@]}")
exec python3 "$here/../code_rules.py" "${args[@]}"
