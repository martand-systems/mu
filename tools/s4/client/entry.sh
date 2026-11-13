#!/bin/sh
# Roles of the S4 test image. The chisel client is the container's main process, so `docker logs` is its log.
#
#   compute      the "office PC": a loopback HTTP service + a reverse tunnel to it on the relay's loopback port
#   workstation  the "laptop": forwards its own 127.0.0.1:7001 to the relay's loopback port
#   killer       a TCP hop that cuts idle connections (idle_killer.py)
#   noise        an unrelated HTTP service (for Traefik configuration reloads)
#   probe        does nothing; the test driver runs curl, chisel and the probe scripts in it with `docker exec`
#
# Settings come from relay/gen-credentials.sh (RELAY_URL, RELAY_FINGERPRINT, RELAY_USER, RELAY_PASSWORD,
# RELAY_PORTS) plus S4_KEEPALIVE (default 25s, chisel's default), and S4_CONNECT_URL + S4_HOSTNAME to reach the
# relay through the killer hop.
set -eu
role="${1:-}"
MB="${S4_BLOB_MB:-200}"

case "$role" in
  compute)
    head -c "$((MB * 1048576))" /dev/urandom > /data/blob.bin
    sha256sum /data/blob.bin | cut -d' ' -f1 > /data/blob.sha256
    head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n' > /data/nonce
    python3 /s4/service.py /data/blob.bin "$(cat /data/nonce)" 9000 &
    remote="R:127.0.0.1:${RELAY_PORTS%%+*}:127.0.0.1:9000" ;;
  workstation)
    head -c "$((MB * 1048576))" /dev/urandom > /data/up.bin
    sha256sum /data/up.bin | cut -d' ' -f1 > /data/up.sha256
    remote="127.0.0.1:7001:127.0.0.1:${RELAY_PORTS%%+*}" ;;
  killer)
    exec python3 /s4/idle_killer.py 443 "$S4_KILLER_TARGET" 443 "${S4_KILLER_IDLE:-45}" ;;
  noise)
    exec python3 /s4/service.py /dev/null noise 9000 0.0.0.0 ;;
  probe)
    exec sleep 2147483647 ;;
  *) echo "entry.sh: unknown role '$role'" >&2; exit 2 ;;
esac

# The password goes in through AUTH, not --auth, so it is not in the process list. The server certificate is
# verified against the test CA (--tls-ca) and the relay's key against the pinned fingerprint.
export AUTH="$RELAY_USER:$RELAY_PASSWORD"
set -- --tls-ca /certs/ca.crt --fingerprint "$RELAY_FINGERPRINT" \
  --keepalive "${S4_KEEPALIVE:-25s}" --max-retry-interval 15s
[ -z "${S4_HOSTNAME:-}" ] || set -- "$@" --hostname "$S4_HOSTNAME"
exec chisel client "$@" "${S4_CONNECT_URL:-$RELAY_URL}" "$remote"
