#!/usr/bin/env bash
# Auto-fix lint and format in place (the only script that writes to the repo).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
docker build -q --target dev -t tata-dev "$REPO_ROOT" >/dev/null 2>&1 \
    || docker build --target dev -t tata-dev "$REPO_ROOT"
docker run --rm --user "$(id -u):$(id -g)" --network none \
    -v "$REPO_ROOT:/app" -w /app tata-dev \
    sh -c 'ruff check --fix src tests; ruff format src tests'
