#!/usr/bin/env bash
# Credentials for the μ relay: the server key (clients pin its fingerprint) and one user per client.
# This is the stand-alone form of the future `mu relay add-client`. Needs only bash and openssl.
#
#   relay/gen-credentials.sh [--env FILE] [--url URL] COMMAND
#
#   init                                new server key (refuses to replace one; see rotate-key)
#   add-client compute NAME PORT        a compute node: one reverse tunnel on the relay's loopback PORT (7001-7999)
#   add-client workstation NAME PORT[+PORT...]   a workstation: may connect to those compute ports
#   rotate-client NAME                  new password for NAME
#   remove-client NAME
#   rotate-key                          new server key: every client then needs the new fingerprint
#   fingerprint                         the server key's fingerprint
#   show                                fingerprint and clients, without passwords
#   client NAME                         NAME's settings, including its password
#   env                                 the two lines for Dokploy's Environment tab (secret)
#
# FILE (default $MU_RELAY_ENV, else ~/.config/mu/relay.env; mode 600) is the only state. It holds exactly what the
# relay container reads: RELAY_KEY and RELAY_CLIENTS. It must never be inside a git checkout that gets pushed.
# After any change: paste `env` into Dokploy (Environment tab, Save) and press Deploy. The relay restarts,
# connected clients reconnect by themselves, and the fingerprint only changes with rotate-key.
set -euo pipefail
umask 077
# Byte-wise character ranges: in a UTF-8 locale [a-z] also matches upper-case letters in bash on macOS, and a name
# like "Mac" would be accepted here and then refused by the relay's entrypoint at start-up.
export LC_ALL=C

ENV_FILE="${MU_RELAY_ENV:-$HOME/.config/mu/relay.env}"
URL="${MU_RELAY_URL:-https://relay.martand.systems}"
PORT_MIN=7001; PORT_MAX=7999

die() { echo "gen-credentials: $*" >&2; exit 1; }
usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --env) [ $# -ge 2 ] || usage 2; ENV_FILE="$2"; shift 2 ;;
    --url) [ $# -ge 2 ] || usage 2; URL="$2"; shift 2 ;;
    -h|--help) usage 0 ;;
    -*) die "unknown option $1" ;;
    *) break ;;
  esac
done
[ $# -ge 1 ] || usage 2
cmd="$1"; shift
command -v openssl >/dev/null || die "openssl is needed"

KEY=""; CLIENTS=""
load() {
  [ -f "$ENV_FILE" ] || die "$ENV_FILE does not exist; run: $0 --env '$ENV_FILE' init"
  KEY="$(sed -n 's/^RELAY_KEY=//p' "$ENV_FILE" | tail -n 1)"
  CLIENTS="$(sed -n 's/^RELAY_CLIENTS=//p' "$ENV_FILE" | tail -n 1)"
  case "$KEY" in ck-?*) ;; *) die "$ENV_FILE has no RELAY_KEY" ;; esac
}
save() {
  mkdir -p "$(dirname "$ENV_FILE")"
  local tmp="$ENV_FILE.tmp.$$"
  printf 'RELAY_KEY=%s\nRELAY_CLIENTS=%s\n' "$KEY" "$CLIENTS" > "$tmp"
  chmod 600 "$tmp"; mv "$tmp" "$ENV_FILE"
}

# A chisel key is "ck-" + unpadded base64 of the P-256 private key (SEC 1 DER).
new_key() {
  printf 'ck-%s' "$(openssl ecparam -name prime256v1 -genkey -noout -outform DER | openssl base64 -A | tr -d '=')"
}
# chisel's fingerprint: base64(SHA-256(the public key in SSH wire format)), as `chisel server` prints at start-up.
fingerprint() {
  local b64="${1#ck-}"
  case $(( ${#b64} % 4 )) in 2) b64="$b64==" ;; 3) b64="$b64=" ;; 1) die "the key is damaged" ;; esac
  {
    printf '\000\000\000\023ecdsa-sha2-nistp256\000\000\000\010nistp256\000\000\000\101'
    printf '%s' "$b64" | openssl base64 -d -A | openssl ec -inform DER -pubout -outform DER 2>/dev/null | tail -c 65
  } | openssl dgst -sha256 -binary | openssl base64 -A
}
new_password() { openssl rand -hex 24; }   # 192 bits; only 0-9 a-f, so it survives every quoting layer

check_name() {
  case "$1" in ''|-*|*[!a-z0-9-]*) die "client name '$1': use lower-case letters, digits and '-'" ;; esac
  [ "${#1}" -le 32 ] || die "client name '$1' is longer than 32 characters"
}
check_port() {
  case "$1" in ''|*[!0-9]*) die "port '$1' is not a number" ;; esac
  case "$1" in 0*) die "port '$1' has a leading zero" ;; esac   # "07001" is port 7001 to chisel: see the entrypoint
  [ "${#1}" -le 5 ] && [ "$1" -ge "$PORT_MIN" ] && [ "$1" -le "$PORT_MAX" ] || die "port $1 is outside $PORT_MIN-$PORT_MAX"
}
entry_of() {  # prints NAME's entry, or nothing
  local IFS=','; local e
  for e in $CLIENTS; do
    if [ "$(printf '%s' "$e" | cut -d: -f2)" = "$1" ]; then printf '%s' "$e"; fi
  done
  return 0
}
without() {   # prints CLIENTS without NAME
  local IFS=','; local e out=""
  for e in $CLIENTS; do [ "$(printf '%s' "$e" | cut -d: -f2)" = "$1" ] || out="${out:+$out,}$e"; done
  printf '%s' "$out"
}
print_client() {  # $1 = entry
  local role name pass ports
  IFS=: read -r role name pass ports <<< "$1"
  printf 'RELAY_URL=%s\nRELAY_FINGERPRINT=%s\nRELAY_ROLE=%s\nRELAY_USER=%s\nRELAY_PASSWORD=%s\nRELAY_PORTS=%s\n' \
    "$URL" "$(fingerprint "$KEY")" "$role" "$name" "$pass" "$ports"
  {
    echo
    echo "# chisel client for $name (keep --fingerprint; never use --tls-skip-verify):"
    if [ "$role" = compute ]; then
      echo "#   AUTH='$name:<RELAY_PASSWORD>' CHISEL_WS_TIMEOUT=10s chisel client --fingerprint '<RELAY_FINGERPRINT>' \\"
      echo "#     --keepalive 25s --max-retry-interval 15s $URL R:127.0.0.1:$ports:127.0.0.1:<agent port on this machine>"
    else
      echo "#   AUTH='$name:<RELAY_PASSWORD>' CHISEL_WS_TIMEOUT=10s chisel client --fingerprint '<RELAY_FINGERPRINT>' \\"
      echo "#     --keepalive 25s --max-retry-interval 15s $URL 127.0.0.1:<local port>:127.0.0.1:<one of $ports>"
    fi
  } >&2
}
changed() { echo "Updated $ENV_FILE. Paste the output of '$0 env' into Dokploy's Environment tab, Save, Deploy." >&2; }

case "$cmd" in
  init)
    if [ -f "$ENV_FILE" ] && grep -q '^RELAY_KEY=ck-' "$ENV_FILE"; then die "$ENV_FILE already has a key (rotate-key replaces it)"; fi
    KEY="$(new_key)"; CLIENTS=""; save
    echo "RELAY_FINGERPRINT=$(fingerprint "$KEY")"
    echo "Created $ENV_FILE. Next: add-client." >&2 ;;
  rotate-key)
    load; KEY="$(new_key)"; save
    echo "RELAY_FINGERPRINT=$(fingerprint "$KEY")"
    echo "Every client must be given this fingerprint, or it will refuse to connect." >&2; changed ;;
  fingerprint) load; fingerprint "$KEY"; echo ;;
  add-client)
    [ $# -eq 3 ] || usage 2
    load; role="$1"; name="$2"; ports="$3"
    check_name "$name"
    [ -z "$(entry_of "$name")" ] || die "client '$name' exists (rotate-client or remove-client)"
    case "$role" in
      compute)
        check_port "$ports"
        ( IFS=','; for e in $CLIENTS; do
            case "$e" in compute:*) [ "$(printf '%s' "$e" | cut -d: -f4)" != "$ports" ] || die "port $ports belongs to $(printf '%s' "$e" | cut -d: -f2)" ;; esac
          done ) ;;
      workstation)
        ( IFS='+'; for p in $ports; do check_port "$p"; done )
        case "$ports" in *++*|+*|*+) die "ports: use PORT or PORT+PORT" ;; esac ;;
      *) die "role must be compute or workstation" ;;
    esac
    entry="$role:$name:$(new_password):$ports"
    CLIENTS="${CLIENTS:+$CLIENTS,}$entry"; save
    print_client "$entry"; changed ;;
  rotate-client)
    [ $# -eq 1 ] || usage 2
    load; old="$(entry_of "$1")"; [ -n "$old" ] || die "no client '$1'"
    IFS=: read -r role name _ ports <<< "$old"
    entry="$role:$name:$(new_password):$ports"
    rest="$(without "$1")"; CLIENTS="${rest:+$rest,}$entry"; save
    print_client "$entry"; changed ;;
  remove-client)
    [ $# -eq 1 ] || usage 2
    load; [ -n "$(entry_of "$1")" ] || die "no client '$1'"
    CLIENTS="$(without "$1")"; save; changed ;;
  client)
    [ $# -eq 1 ] || usage 2
    load; e="$(entry_of "$1")"; [ -n "$e" ] || die "no client '$1'"
    print_client "$e" ;;
  show)
    load
    echo "url          $URL"
    echo "fingerprint  $(fingerprint "$KEY")"
    ( IFS=','; for e in $CLIENTS; do
        printf '%-12s %-20s ports %s\n' "$(printf '%s' "$e" | cut -d: -f1)" "$(printf '%s' "$e" | cut -d: -f2)" "$(printf '%s' "$e" | cut -d: -f4)"
      done ) ;;
  env) load; printf 'RELAY_KEY=%s\nRELAY_CLIENTS=%s\n' "$KEY" "$CLIENTS" ;;
  *) usage 2 ;;
esac
