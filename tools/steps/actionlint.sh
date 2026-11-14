#!/usr/bin/env bash
# actionlint on the project's workflows: actionlint.yml's step, and the actionlint part of the `checks` action. The
# image is the one tools/images/Dockerfile.actionlint pins by tag and digest. A version, given as the one argument,
# is another release by its tag (image-ref.sh).
#
#   actionlint.sh            # the pinned release
#   actionlint.sh 1.7.7      # that release
#
# It runs in the root of the project's checkout.
set -e
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
image="$(sh "$here/../image-ref.sh" actionlint "${1:-}")"
docker run --rm -v "$PWD:/repo" -w /repo "$image" -color
