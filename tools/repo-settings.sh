#!/usr/bin/env bash
# Applies the repository settings every murphy360 project keeps: squash merge only, auto-merge and "Update branch"
# on, Dependabot security updates on, and the branch ruleset (templates/rulesets/main.json): pull request required,
# linear history, no force push, the "result" job required, the owner can bypass.
#
# The ruleset's bypass actor is a User (murphy360's numeric GitHub id), not a RepositoryRole: these are personal
# repositories, and GitHub's own schema says "OrganizationAdmin is not applicable for personal repositories".
#
#   tools/repo-settings.sh <owner>/<repo> [--dry-run] [--ruleset <path>]
#
# Needs `gh`, authenticated with admin rights on the target repository. `--dry-run` prints what would be sent,
# without calling the API.
set -euo pipefail

usage() {
  echo "Usage: $0 <owner>/<repo> [--dry-run] [--ruleset <path>]" >&2
  exit 1
}

[ $# -ge 1 ] || usage
repo="$1"
shift

dry_run=0
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ruleset_path="$script_dir/../templates/rulesets/main.json"

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)
      dry_run=1
      shift
      ;;
    --ruleset)
      [ $# -ge 2 ] || usage
      ruleset_path="$2"
      shift 2
      ;;
    *)
      usage
      ;;
  esac
done

case "$repo" in
  */*) ;;
  *) usage ;;
esac

[ -f "$ruleset_path" ] || {
  echo "ruleset file not found: $ruleset_path" >&2
  exit 1
}

# api METHOD PATH [JSON_BODY]: calls `gh api`, or prints what it would send with --dry-run.
api() {
  local method="$1" path="$2" body="${3:-}"
  if [ "$dry_run" -eq 1 ]; then
    printf 'DRY RUN: gh api --method %s %s\n' "$method" "$path"
    [ -z "$body" ] || printf '%s\n' "$body"
    return 0
  fi
  if [ -n "$body" ]; then
    printf '%s' "$body" | gh api --method "$method" "$path" --input -
  else
    gh api --method "$method" "$path"
  fi
}

echo "Repository settings: squash merge only, auto-merge and \"Update branch\" on"
api PATCH "repos/$repo" '{
  "allow_squash_merge": true,
  "allow_merge_commit": false,
  "allow_rebase_merge": false,
  "allow_auto_merge": true,
  "allow_update_branch": true
}'

echo "Dependabot: vulnerability alerts on"
api PUT "repos/$repo/vulnerability-alerts"

echo "Dependabot: automated security fixes on"
api PUT "repos/$repo/automated-security-fixes"

ruleset_name="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['name'])" "$ruleset_path")"
ruleset_body="$(cat "$ruleset_path")"
echo "Ruleset '$ruleset_name' from $ruleset_path"

existing_id=""
if [ "$dry_run" -eq 0 ]; then
  existing_id="$(
    gh api "repos/$repo/rulesets" \
      --jq ".[] | select(.name == \"$ruleset_name\") | .id" 2>/dev/null || true
  )"
fi

if [ -n "$existing_id" ]; then
  api PUT "repos/$repo/rulesets/$existing_id" "$ruleset_body"
else
  api POST "repos/$repo/rulesets" "$ruleset_body"
fi

if [ "$dry_run" -eq 1 ]; then
  echo "Done (dry run: nothing was changed)"
else
  echo "Done"
fi
