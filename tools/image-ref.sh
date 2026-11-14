#!/bin/sh
# The container image a workflow runs, read from the file that pins it: tools/images/Dockerfile.<name> holds one
# FROM line, image:tag@sha256:digest, and Dependabot's docker ecosystem keeps it current. A run: step is not a place
# Dependabot reads, so the workflows ask this script instead.
#
#   image-ref.sh shellcheck            koalaman/shellcheck:<tag>@sha256:<digest>   the pin
#   image-ref.sh shellcheck v0.10.0    koalaman/shellcheck:v0.10.0                 a caller's version, by its tag
#
# The reference goes to standard output. One line on standard error says which image it is and why, for the log.
# POSIX sh, sed and head only: the step that needs alpine runs before anything is installed.
set -eu
name="${1:?usage: image-ref.sh <name> [version]}"
version="${2:-}"
file="$(dirname "$0")/images/Dockerfile.$name"
if [ ! -f "$file" ]; then
  echo "image-ref: no image is pinned for '$name' ($file is missing)" >&2
  exit 1
fi
ref="$(sed -n 's/^FROM[[:space:]][[:space:]]*\([^[:space:]][^[:space:]]*\).*$/\1/p' "$file" | head -n 1)"
case "$ref" in
  *:*@sha256:*) ;;
  *)
    echo "image-ref: $file names no image by tag and digest (FROM image:tag@sha256:digest)" >&2
    exit 1
    ;;
esac
pinned="${ref%@*}"
tag="${pinned##*:}"
if [ -n "$version" ] && [ "$version" != "$tag" ]; then
  ref="${pinned%:*}:$version"
  echo "$name image: $ref (the version the caller passed, by its tag: only the pinned release has a digest)" >&2
else
  echo "$name image: $ref (pinned in tools/images/Dockerfile.$name)" >&2
fi
echo "$ref"
