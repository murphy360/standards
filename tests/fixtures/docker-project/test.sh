#!/bin/sh
# The docker fixture's one test. test-docker.yml runs it inside the image it built, from the mounted checkout.
# It passes only when both halves worked: the image holds the copy the Dockerfile took from the build context, and
# the checkout is mounted, so that copy and this file are the same.
set -eu
here=$(dirname "$0")
built=/opt/fixture/test.sh
if [ ! -f "$built" ]; then
  echo "FAILED: $built is missing: this is not the fixture's image"
  exit 1
fi
if ! cmp -s "$built" "$here/test.sh"; then
  echo "FAILED: the image's copy of test.sh differs from the checkout's"
  exit 1
fi
echo "OK: ran in the fixture's image (built on $(cat /opt/fixture/arch)), from the mounted checkout"
