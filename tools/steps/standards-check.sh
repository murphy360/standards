#!/usr/bin/env bash
# The step that asks whether a project follows the standards: standards-check.yml's last step, and the
# standards-check part of the `checks` action. Every check runs by default (since v3). Each one that can be turned
# off is a variable: `false`, as a workflow or an action passes a boolean input, turns it off. Anything else leaves
# it on.
#
#   CHECK_PR_TEMPLATE=false standards-check.sh     # check_standards.py --no-require-pr-template
#
# It runs in the root of the project's checkout and exits as tools/check_standards.py does.
set -e
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
args=()
[ "$CHECK_PR_TEMPLATE" != false ] || args+=(--no-require-pr-template)
[ "$CHECK_PRE_COMMIT_CONFIG" != false ] || args+=(--no-require-pre-commit)
[ "$CHECK_TICKET_TEMPLATE" != false ] || args+=(--no-require-ticket-template)
[ "$CHECK_TEMPLATES" != false ] || args+=(--no-require-current-templates)
[ "$CHECK_AGENT_SETTINGS" != false ] || args+=(--no-require-agent-settings)
[ "$CHECK_RUFF_VERSION" != false ] || args+=(--no-require-one-ruff-version)
python3 "$here/../check_standards.py" "${args[@]}"
