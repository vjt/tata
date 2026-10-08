#!/usr/bin/env bash
# ruff (lint + format check) and pyright strict, in the dev container.
# shellcheck disable=SC1091
source "$(dirname "$0")/_lib.sh"
dev_run sh -c 'ruff check src tests && ruff format --check src tests && pyright'
