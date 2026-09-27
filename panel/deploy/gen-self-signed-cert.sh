#!/bin/sh
# Makes the panel's self-signed TLS certificate (10 years) for PANEL_TLS=self-signed.
#   ./gen-self-signed-cert.sh 203.0.113.5          # an IP address
#   ./gen-self-signed-cert.sh panel.home.arpa      # or a host name
# Writes certs/panel.crt and certs/panel.key next to this script and prints the SHA-256 fingerprint:
# compare it with the one the AmneziaVPN app and the browser show on the first connection.
set -eu

name="${1:?usage: $0 <IP address or host name of the panel>}"
dir="$(cd "$(dirname "$0")" && pwd)/certs"
mkdir -p "$dir"

if [ -e "$dir/panel.key" ] && [ "${FORCE:-}" != "1" ]; then
    echo "certs/panel.key already exists; run with FORCE=1 to replace it (every client must trust the new one)" >&2
    exit 1
fi

case "$name" in
    *[!0-9.]*) san="DNS:$name" ;;  # anything but digits and dots is a host name
    *) san="IP:$name" ;;
esac
case "$name" in
    *:*) san="IP:$name" ;;         # IPv6
esac

umask 077
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:prime256v1 -nodes -days 3650 -sha256 \
    -subj "/CN=$name" -addext "subjectAltName=$san" \
    -keyout "$dir/panel.key.new" -out "$dir/panel.crt.new"
# Temporary names first: a failed run leaves no half-made key behind.
mv "$dir/panel.key.new" "$dir/panel.key"
mv "$dir/panel.crt.new" "$dir/panel.crt"
chmod 644 "$dir/panel.crt"
# Caddy runs as root in its container, but the key must stay private on the host.
chmod 600 "$dir/panel.key"

echo "Certificate for $san written to $dir"
openssl x509 -in "$dir/panel.crt" -noout -fingerprint -sha256
