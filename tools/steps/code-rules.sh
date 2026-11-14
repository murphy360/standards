#!/usr/bin/env bash
# The "Code rules" step: python-lint.yml's and node-lint.yml's last step, and the lint parts of the `checks`
# action. It finds the base a change is compared with, turns the inputs into flags, and runs tools/code_rules.py.
#
#   code-rules.sh python     # ruff, no noqa, complexity, file size, format
#   code-rules.sh node       # the same with --eslint: the project's ESLint, and prettier with FORMAT_CHECK
#
# The inputs are variables, as a workflow or an action passes them. A boolean is on when it reads `true`.
#   MODE               all (every file) or changed (the files a change touches)
#   FORMAT_CHECK       also ruff format --check, or prettier --check for node
#   FORMAT_ONLY        python only: also prove that a pull request only re-formats
#   NO_INLINE_CONFIG   node only: ESLint runs with --no-inline-config
#   SILENCERS          every silencer carries its reason
#   PATHS              space-separated directories or files (default: every tracked file)
#   EVENT, PR_BASE, BEFORE   github.event_name, the pull request's base SHA, the commit before a push
#
# The base. A pull request is compared with the first parent of the merge commit the job checked out: the base
# branch as the pull request was merged with it. Without a merge commit it is the pull request's base SHA. A push
# is compared with the commit before it. The base is exported as CODE_RULES_BASE. With no base (a schedule, a
# dispatch, a new branch or tag) mode changed checks every file.
#
# It runs in the directory the lint checks, inside the project's checkout, and exits as code_rules.py does.
set -e
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tool="$here/../code_rules.py"
case "${1:-}" in
  python)
    args=()
    [ "$FORMAT_CHECK" = true ] || args+=(--no-format)
    ;;
  node)
    args=(--eslint --no-format)
    if [ "$FORMAT_CHECK" = true ]; then
      args+=(--prettier)
    fi
    [ "$NO_INLINE_CONFIG" != true ] || args+=(--no-inline-config)
    ;;
  *)
    echo "usage: code-rules.sh python|node" >&2
    exit 2
    ;;
esac
language="$1"
[ "$SILENCERS" != true ] || args+=(--silencers)
read -r -a only <<< "$PATHS"
[ ${#only[@]} -eq 0 ] || args+=(--paths "${only[@]}")
case "$MODE" in
  all | changed) ;;
  *) echo "::error::mode is all or changed, not '$MODE'"; exit 1 ;;
esac

# The base commit, in this clone. A job that checked out one commit fetches it, alone (the check workflows). A job
# that checked out the whole history has it already (the checks job, for the secret scan). A fetch at depth 1
# there would cut the clone's history off at the base, so a complete clone fetches without a depth.
have_or_fetch() {
  if git cat-file -e "$1^{commit}" 2> /dev/null; then
    return 0
  fi
  if [ "$(git rev-parse --is-shallow-repository)" = false ]; then
    git fetch --no-tags origin "$1"
  else
    git fetch --no-tags --depth=1 origin "$1"
  fi
}

base=""
if [ "$EVENT" = pull_request ]; then
  # The merge commit the job checked out says what the pull request was merged with: its first parent.
  # The base branch may have moved on since; comparing with it would count files this PR never touched.
  parents=$(git cat-file -p HEAD | sed -n 's/^parent //p')
  first=$(printf '%s\n' "$parents" | head -1)
  if [ "$(printf '%s\n' "$parents" | grep -c .)" -eq 2 ] \
    && have_or_fetch "$first"; then
    base="$first"
    echo "code rules base: $first, the first parent of the merge commit $(git rev-parse HEAD)"
  else
    base="$PR_BASE"
    echo "code rules base: $base, the pull request's base (HEAD is not a merge commit, or its parent did not fetch)"
    have_or_fetch "$base" || echo "::warning::could not fetch the base $base"
  fi
elif [ "$EVENT" = push ] && [ -n "${BEFORE//0/}" ]; then
  base="$BEFORE"  # all zeros: a new branch or tag, nothing before it
  echo "code rules base: $base, the commit before the push"
  have_or_fetch "$base" || echo "::warning::could not fetch the base $base"
fi
[ -z "$base" ] || export CODE_RULES_BASE="$base"
status=0
if [ "$MODE" = all ]; then
  python3 "$tool" --all "${args[@]}" || status=$?
elif [ -z "$base" ]; then
  echo "::notice::no base to compare with on a $EVENT event: every file is checked"
  python3 "$tool" --all "${args[@]}" || status=$?
else
  python3 "$tool" "${args[@]}" || status=$?
fi
if [ "$language" = python ] && [ "$FORMAT_ONLY" = true ]; then
  if [ "$EVENT" != pull_request ]; then
    echo "::notice::format-only proves a pull request; this is a $EVENT event"
  else
    python3 "$tool" --format-only "${args[@]}" || status=$?
  fi
fi
exit "$status"
