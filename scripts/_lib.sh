# shellcheck shell=bash
# Shared helper: run a command in the dev container with the repo bind-mounted at /app.
# The container is the runtime — nothing is installed on the host.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV_IMAGE="tata-dev"

dev_build() {
    # Quiet on success (the legacy builder nags on stderr); full output on failure.
    docker build -q --target dev -t "$DEV_IMAGE" "$REPO_ROOT" >/dev/null 2>&1 \
        || docker build --target dev -t "$DEV_IMAGE" "$REPO_ROOT"
}

dev_run() {
    dev_build
    docker run --rm \
        --user "$(id -u):$(id -g)" \
        --network none \
        -v "$REPO_ROOT:/app:ro" \
        -w /app \
        -e PYTHONPATH=/app/src \
        "$DEV_IMAGE" "$@"
}
