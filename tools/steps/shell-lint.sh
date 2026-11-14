#!/usr/bin/env bash
# Runs shellcheck on every tracked *.sh: shell-lint.yml's step, and the shell-lint part of the `checks` action.
# The image is the one tools/images/Dockerfile.shellcheck pins by tag and digest. A version, given as the one
# argument, is another release by its tag (image-ref.sh).
#
#   shell-lint.sh            # the pinned release
#   shell-lint.sh v0.10.0    # that release
#
# It runs in the root of the project's checkout. No tracked *.sh: nothing runs, and it passes.
set -e
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
image="$(sh "$here/../image-ref.sh" shellcheck "${1:-}")"
git ls-files -z '*.sh' | xargs -0 -r docker run --rm -v "$PWD:/mnt" -w /mnt "$image"
