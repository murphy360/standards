#!/usr/bin/env bash
# The step that asks whether a project follows the standards: standards-check.yml's last step, and the
# standards-check part of the `checks` action. Each opt-in check is a variable that reads `true`, as a workflow or
# an action passes a boolean input. Anything else leaves the check off.
#
#   CHECK_PR_TEMPLATE=true standards-check.sh     # check_standards.py --require-pr-template
#
# It runs in the root of the project's checkout and exits as tools/check_standards.py does.
set -e
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
args=()
if [ "$CHECK_PR_TEMPLATE" = "true" ]; then
  args+=(--require-pr-template)
fi
if [ "$CHECK_PRE_COMMIT_CONFIG" = "true" ]; then
  args+=(--require-pre-commit)
fi
if [ "$CHECK_TICKET_TEMPLATE" = "true" ]; then
  args+=(--require-ticket-template)
fi
if [ "$CHECK_TEMPLATES" = "true" ]; then
  args+=(--require-current-templates)
fi
if [ "$CHECK_AGENT_SETTINGS" = "true" ]; then
  args+=(--require-agent-settings)
fi
if [ "$CHECK_RUFF_VERSION" = "true" ]; then
  args+=(--require-one-ruff-version)
fi
python3 "$here/../check_standards.py" "${args[@]}"
