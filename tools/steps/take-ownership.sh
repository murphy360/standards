#!/bin/sh
# Take back the files a previous job's containers left in the workspace of a self-hosted runner. A container runs
# as root, so what it writes belongs to root, and the next checkout cannot remove it. It runs before the checkout:
# the step of every check workflow that says so, and the `take-ownership` action in a project's own job.
#
#   take-ownership.sh        # chown -R the workspace to the user this job runs as
#
# alpine comes from tools/images/Dockerfile.alpine, by tag and digest (image-ref.sh). POSIX sh only: nothing is
# installed yet when this runs.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
image="$(sh "$here/../image-ref.sh" alpine)"
docker run --rm -v "$GITHUB_WORKSPACE:/w" "$image" chown -R "$(id -u):$(id -g)" /w
