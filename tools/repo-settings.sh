#!/usr/bin/env bash
# Applies the repository settings every murphy360 project keeps: squash merge only, auto-merge and "Update branch"
# on, Dependabot security updates on, and two rulesets:
#   templates/rulesets/main.json: pull request required, linear history, no force push, the "result" job required.
#     The owner can merge a pull request past a failed check, but a direct push to main is refused for everyone.
#   templates/rulesets/version-tags.json: only the owner can create, move or delete a v* tag.
#
# The bypass actor is a User (murphy360's numeric GitHub id), not a RepositoryRole: these are personal
# repositories, and GitHub's own schema says "OrganizationAdmin is not applicable for personal repositories".
#
#   tools/repo-settings.sh <owner>/<repo> [--dry-run] [--ruleset <path>]...
#
# `--ruleset` replaces the two default rulesets; give it more than once to apply several. Needs `gh`, authenticated
# with admin rights on the target repository. `--dry-run` prints what would be sent, without calling the API.
set -euo pipefail

usage() {
  echo "Usage: $0 <owner>/<repo> [--dry-run] [--ruleset <path>]..." >&2
  exit 1
}

[ $# -ge 1 ] || usage
repo="$1"
shift

dry_run=0
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ruleset_paths=()

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)
      dry_run=1
      shift
      ;;
    --ruleset)
      [ $# -ge 2 ] || usage
      ruleset_paths+=("$2")
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

if [ "${#ruleset_paths[@]}" -eq 0 ]; then
  ruleset_paths=(
    "$script_dir/../templates/rulesets/main.json"
    "$script_dir/../templates/rulesets/version-tags.json"
  )
fi

for ruleset_path in "${ruleset_paths[@]}"; do
  [ -f "$ruleset_path" ] || {
    echo "ruleset file not found: $ruleset_path" >&2
    exit 1
  }
done

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

# apply_ruleset PATH: creates the ruleset, or replaces the one of the same name.
apply_ruleset() {
  local path="$1" name body existing_id=""
  name="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['name'])" "$path")"
  body="$(cat "$path")"
  echo "Ruleset '$name' from $path"

  if [ "$dry_run" -eq 0 ]; then
    existing_id="$(
      gh api "repos/$repo/rulesets" \
        --jq ".[] | select(.name == \"$name\") | .id" 2>/dev/null || true
    )"
  fi

  if [ -n "$existing_id" ]; then
    api PUT "repos/$repo/rulesets/$existing_id" "$body"
  else
    api POST "repos/$repo/rulesets" "$body"
  fi
}

for ruleset_path in "${ruleset_paths[@]}"; do
  apply_ruleset "$ruleset_path"
done

if [ "$dry_run" -eq 1 ]; then
  echo "Done (dry run: nothing was changed)"
else
  echo "Done"
fi
