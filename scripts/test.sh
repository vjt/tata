#!/usr/bin/env bash
# Run pytest in the dev container.
# Usage: scripts/test.sh                    — full suite
#        scripts/test.sh tests/test_foo.py  — one file
#        scripts/test.sh -k name            — by name
# shellcheck disable=SC1091
source "$(dirname "$0")/_lib.sh"
dev_run pytest "${@:-tests/}"
