#!/bin/sh
# Get the Let's Encrypt certificate on first start (DNS-01 on Cloudflare), check renewals
# daily, then run the CMD. acme.sh state lives in /acme (${TATA_ACME}). uvicorn does not
# reload certificates: a renewal stops it and `restart: unless-stopped` brings it back.
set -eu
acme() { /opt/acme.sh/acme.sh --home /opt/acme.sh --config-home /acme "$@"; }

if [ ! -s /acme/live/fullchain.pem ]; then
	rc=0
	acme --issue --server letsencrypt --dns dns_cf -d "$TATA_DOMAIN" --keylength ec-256 \
		--dnssleep 30 || rc=$?   # DoH is blocked on the LAN: no propagation check, CF is fast
	[ "$rc" = 0 ] || [ "$rc" = 2 ] || exit "$rc"   # 2 = already issued, just not installed
	mkdir -p /acme/live
	acme --install-cert -d "$TATA_DOMAIN" --ecc \
		--key-file /acme/live/key.pem --fullchain-file /acme/live/fullchain.pem \
		--reloadcmd 'kill "$(cat /tmp/app.pid 2>/dev/null)" 2>/dev/null || true'
fi

# acme.sh saved the Cloudflare credentials in /acme; the app doesn't need them.
unset CF_Token CF_Zone_ID
echo $$ > /tmp/app.pid   # exec keeps the pid: the reloadcmd stops it
(while :; do acme --cron || true; sleep 86400; done) &
exec "$@"
