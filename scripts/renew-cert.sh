#!/usr/bin/env bash
# Renew the TLS cert with the local step-ca (mTLS renew with the current cert, no secret) and
# restart the container only when the cert actually changed. For hosts where the cert pusher
# does not manage tata. Cron it daily; needs `step` bootstrapped for the CA (STEPPATH).
set -euo pipefail
cd "$(dirname "$0")/.."
TATA_CERTS="$(sed -n 's/^TATA_CERTS=\([^ ]*\).*/\1/p' .env)"
crt="$TATA_CERTS/server.crt"
key="$TATA_CERTS/server.key"
before="$(sha256sum "$crt")"
step ca renew --force --expires-in 240h "$crt" "$key"
if [ "$(sha256sum "$crt")" != "$before" ]; then
    docker restart tata >/dev/null
    logger -t tata-cert "renewed and restarted"
fi
