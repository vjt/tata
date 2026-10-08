#!/usr/bin/env bash
# Full gate: lint + types + tests. Run by the pre-commit hook.
set -euo pipefail
cd "$(dirname "$0")/.."
scripts/lint.sh
scripts/test.sh
