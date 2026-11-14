#!/usr/bin/env bash
# The secret scan: secret-scan.yml's step, and the secret-scan part of the `checks` action. gitleaks, from the
# image tools/images/Dockerfile.gitleaks pins by tag and digest, over the commits of a change.
#
#   EVENT=pull_request PR_BASE=<sha> secret-scan.sh    # the pull request's own commits
#   EVENT=push BEFORE=<sha> secret-scan.sh             # the commits the push added
#   FULL_HISTORY=true secret-scan.sh                   # every commit HEAD reaches
#
# What is scanned. A pull request: from the first parent of the merge commit the job checked out (the base branch
# as the pull request was merged with it) to HEAD, or from PR_BASE when HEAD is not a merge commit. A push: from
# BEFORE, the commit before it. With no base (a new branch or tag, a schedule, a dispatch, a base that a force push
# removed) or with FULL_HISTORY, every commit HEAD reaches. The run prints which it used.
#
# The history. The range is only known against its base, and in a shallow clone the oldest commit looks as if it
# added every file, so old findings would be blamed on the change. A shallow clone is refused: the job checks out
# with fetch-depth: 0.
#
# It runs in the root of the project's checkout. Exit 0: no finding. Exit 1: a finding, or an error gitleaks
# logged. Exit 2: a shallow clone. Any other: docker's, when gitleaks did not run.
set -euo pipefail
: "${EVENT:=}" "${PR_BASE:=}" "${BEFORE:=}" "${FULL_HISTORY:=false}"  # a workflow and the action set all four
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "$(git rev-parse --is-shallow-repository)" = true ]; then
  echo "::error::the secret scan needs the history, and this clone is shallow: check out with fetch-depth: 0 (actions/checkout)"
  exit 2
fi
GITLEAKS_IMAGE="$(sh "$here/../image-ref.sh" gitleaks)"
base=""
if [ "$FULL_HISTORY" = true ]; then
  echo "secret scan: full-history is on"
elif [ "$EVENT" = pull_request ]; then
  # The merge commit the job checked out says what the pull request was merged with: its first parent.
  # The base branch may have moved on since; its newer commits are not this pull request's.
  if git rev-parse -q --verify 'HEAD^2' > /dev/null; then
    base="$(git rev-parse 'HEAD^1')"
  else
    base="$PR_BASE"
  fi
elif [ "$EVENT" = push ] && [ -n "${BEFORE//0/}" ]; then
  base="$BEFORE"  # all zeros: a new branch or tag, nothing before it
fi
if [ -n "$base" ] && ! git cat-file -e "$base^{commit}" 2> /dev/null; then
  echo "::notice::the base $base is not in this clone (a force push removed it)"
  base=""
fi
if [ -n "$base" ]; then
  range="$base..HEAD"
  echo "secret scan: $range, $(git rev-list --count --no-merges "$range") commit(s) of this $EVENT"
else
  range="HEAD"
  [ "$FULL_HISTORY" = true ] || echo "::notice::no base to compare with on a $EVENT event"
  echo "secret scan: the whole history of $(git rev-parse HEAD), $(git rev-list --count HEAD) commit(s)"
fi

# One line per finding, and nothing of the secret: gitleaks' own verbose output also prints the line of
# source the secret sits in, which can hold a second one.
work="${RUNNER_TEMP:-$(mktemp -d)}/secret-scan"
mkdir -p "$work"
cat > "$work/findings.tmpl" << 'TEMPLATE'
{{ range . -}}
::error file={{ .File }},line={{ .StartLine }},title=secret-scan::{{ .RuleID }} at {{ .File }}:{{ .StartLine }}, commit {{ .Commit | trunc 7 }}, fingerprint {{ .Fingerprint }}
{{ end -}}
TEMPLATE
status=0
docker run --rm -v "$PWD:/repo:ro" -v "$work:/tmpl:ro" -w /repo "$GITLEAKS_IMAGE" git . \
  --redact --no-banner --log-opts="$range" \
  --report-format template --report-template /tmpl/findings.tmpl --report-path - \
  2> "$work/gitleaks.log" || status=$?
cat "$work/gitleaks.log"
# gitleaks exits 0 when git itself failed (it logs the error and reports "no leaks found"), so an error
# line fails the run: a scan that read nothing proves nothing.
if sed 's/\x1b\[[0-9;]*m//g' "$work/gitleaks.log" | grep -Eq '^[^ ]+ (ERR|FTL) '; then
  echo "::error::gitleaks reported an error (above), so the scan proves nothing"
  exit 1
fi
case "$status" in
  0) echo "secret scan: no finding" ;;
  1)
    echo "A secret that reached GitHub is compromised: revoke it first, then take it out of the code."
    echo "The commit that added it still holds it, so the run stays red until its fingerprint (above) is"
    echo "in .gitleaksignore. A false positive goes there too, or in .gitleaks.toml, with its reason."
    exit 1
    ;;
  *)
    echo "::error::gitleaks did not run (exit $status)"
    exit "$status"
    ;;
esac
