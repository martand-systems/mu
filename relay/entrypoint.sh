#!/bin/sh
# μ relay entrypoint. Turns the two environment variables into chisel's configuration and starts chisel:
#
#   RELAY_KEY      the server's private key, "ck-..." (relay/gen-credentials.sh init). Clients pin its fingerprint.
#   RELAY_CLIENTS  comma-separated clients, each  <role>:<name>:<password>:<port>[+<port>...]
#                    compute:<name>:<password>:<port>             may open ONE reverse tunnel, listening on the
#                                                                 relay's own loopback 127.0.0.1:<port>
#                    workstation:<name>:<password>:<port>[+...]   may connect to those loopback ports, nothing else
#   RELAY_KEEPALIVE  optional, default 25s (chisel's default; 0s turns the server's pings off)
#   RELAY_REVOKED    optional, comma-separated client names that may never connect: dropped from RELAY_CLIENTS here,
#                    and (pairing mode) deleted from the state volume at every start. The kill switch that survives
#                    a lost volume (docs/design/pairing.md §6.5)
#   RELAY_ADMIN_TOKEN  optional. Unset or empty: chisel alone, as before (the "legacy" path, byte for byte). Set
#                    (mra_ + 43-128 of A-Za-z0-9_-): the pairing service mu-relayd runs as the main process; it
#                    writes the auth file from these static clients plus the dynamic ones on the state volume,
#                    answers /mu/v1/ behind chisel's --backend and supervises chisel (docs/design/pairing.md §6)
#
# Nothing else is reachable through the relay: not the other containers on dokploy-network, not Traefik's API, not
# the internet. A compute node's port is bound to the relay container's loopback, so a neighbouring container
# cannot reach it either.
#
# The auth file is written to a tmpfs (/run/relay); the root filesystem is read-only. Nothing secret is printed.
set -eu
umask 077
export LC_ALL=C   # byte-wise character ranges in the checks below, whatever shell runs this
fail() { echo "relay: $*" >&2; exit 64; }

RUN_DIR="${RELAY_RUN_DIR:-/run/relay}"
CHISEL="${RELAY_CHISEL:-/usr/local/bin/chisel}"
LISTEN_PORT=8080
PORT_MIN=7001
PORT_MAX=7999
KEEPALIVE="${RELAY_KEEPALIVE:-25s}"
key="${RELAY_KEY:-}"
clients="${RELAY_CLIENTS:-}"
revoked="${RELAY_REVOKED:-}"
admin_token="${RELAY_ADMIN_TOKEN:-}"
RELAYD="${RELAY_RELAYD:-/usr/local/bin/mu-relayd}"

[ -n "$key" ] || fail "RELAY_KEY is not set (Dokploy: Environment tab; see relay/README.md)"
case "$key" in ck-?*) ;; *) fail "RELAY_KEY must be a chisel key starting with ck-" ;; esac
case "${key#ck-}" in *[!A-Za-z0-9+/]*) fail "RELAY_KEY has a character outside base64" ;; esac
[ -n "$clients" ] || fail "RELAY_CLIENTS is not set (Dokploy: Environment tab; see relay/README.md)"
case "$clients" in *[!A-Za-z0-9_:,+-]*) fail "RELAY_CLIENTS has a character outside A-Z a-z 0-9 _ - : , +" ;; esac
case "$KEEPALIVE" in *[!0-9sm]*|'') fail "RELAY_KEEPALIVE must look like 25s or 1m" ;; esac
[ -d "$RUN_DIR" ] && [ -w "$RUN_DIR" ] || fail "$RUN_DIR is not a writable directory (compose.yaml mounts a tmpfs there)"
case "$revoked" in *[!a-z0-9,-]*) fail "RELAY_REVOKED has a character outside a-z 0-9 - ," ;; esac
case ",$revoked," in *,-*) fail "RELAY_REVOKED: a name starts with -" ;; esac
if [ -n "$admin_token" ]; then
  # never printed: only its shape is checked
  case "$admin_token" in mra_*) ;; *) fail "RELAY_ADMIN_TOKEN must start with mra_ (relay/gen-credentials.sh admin-token)" ;; esac
  case "${admin_token#mra_}" in *[!A-Za-z0-9_-]*) fail "RELAY_ADMIN_TOKEN has a character outside A-Z a-z 0-9 _ -" ;; esac
  t="${admin_token#mra_}"
  [ "${#t}" -ge 43 ] && [ "${#t}" -le 128 ] || fail "RELAY_ADMIN_TOKEN must have 43 to 128 characters after mra_"
fi
is_revoked() { case ",$revoked," in *",$1,"*) return 0 ;; esac; return 1; }

check_port() {  # $1 = entry number, $2 = port
  case "$2" in ''|*[!0-9]*) fail "client $1: port '$2' is not a number" ;; esac
  # No leading zero: chisel would listen on 7001 for "07001", while the rules and the duplicate check below compare
  # the text. Two compute clients would then share one port.
  case "$2" in 0*) fail "client $1: port '$2' has a leading zero" ;; esac
  [ "${#2}" -le 5 ] && [ "$2" -ge "$PORT_MIN" ] && [ "$2" -le "$PORT_MAX" ] ||
    fail "client $1: port $2 is outside $PORT_MIN-$PORT_MAX"
}

json=""; names=" "; compute_ports=" "; n=0; n_compute=0; n_work=0; n_revoked=0; static=""
rest="$clients"
while [ -n "$rest" ]; do
  case "$rest" in *,*) entry="${rest%%,*}"; rest="${rest#*,}" ;; *) entry="$rest"; rest="" ;; esac
  [ -n "$entry" ] || continue
  n=$((n + 1))
  role="${entry%%:*}"; e="${entry#*:}"
  [ "$e" != "$entry" ] || fail "client $n: expected <role>:<name>:<password>:<ports>"
  name="${e%%:*}"; e2="${e#*:}"
  [ "$e2" != "$e" ] || fail "client $n: expected <role>:<name>:<password>:<ports>"
  pass="${e2%%:*}"; ports="${e2#*:}"
  [ "$ports" != "$e2" ] || fail "client $n: expected <role>:<name>:<password>:<ports>"
  case "$ports" in *:*) fail "client $n: too many fields" ;; esac

  case "$name" in ''|-*|*[!a-z0-9-]*) fail "client $n: name must be lower-case letters, digits and '-'" ;; esac
  [ "${#name}" -le 32 ] || fail "client $n: name is longer than 32 characters"
  case "$names" in *" $name "*) fail "client $n: name '$name' is used twice" ;; esac
  names="$names$name "
  case "$pass" in *[!A-Za-z0-9_-]*) fail "client $n ($name): password has a character outside A-Z a-z 0-9 _ -" ;; esac
  [ "${#pass}" -ge 24 ] || fail "client $n ($name): password is shorter than 24 characters"

  case "$role" in
    compute)
      check_port "$n" "$ports"
      case "$compute_ports" in *" $ports "*) fail "client $n ($name): port $ports belongs to another compute client" ;; esac
      compute_ports="$compute_ports$ports "
      rule='^R:127\\.0\\.0\\.1:'"$ports"'$'
      n_compute=$((n_compute + 1))
      ;;
    workstation)
      alt=""; p_rest="$ports"
      while [ -n "$p_rest" ]; do
        case "$p_rest" in *+*) p="${p_rest%%+*}"; p_rest="${p_rest#*+}" ;; *) p="$p_rest"; p_rest="" ;; esac
        check_port "$n" "$p"
        alt="${alt:+$alt|}$p"
      done
      [ -n "$alt" ] || fail "client $n ($name): no port"
      rule='^127\\.0\\.0\\.1:('"$alt"')$'
      n_work=$((n_work + 1))
      ;;
    *) fail "client $n ($name): role must be compute or workstation" ;;
  esac
  if is_revoked "$name"; then
    n_revoked=$((n_revoked + 1))
    case "$role" in compute) n_compute=$((n_compute - 1)) ;; *) n_work=$((n_work - 1)) ;; esac
    continue
  fi
  json="${json:+$json,
}  \"$name:$pass\": [\"$rule\"]"
  static="$static$role	$name	$pass	$ports
"
done
[ "$n" -gt 0 ] || fail "RELAY_CLIENTS has no clients"

if [ -n "$admin_token" ]; then
  # Pairing mode. mu-relayd reads the static clients and its settings from two files on the tmpfs (the key and the
  # token are not put in its environment) and removes the settings file once read.
  printf '%s' "$static" > "$RUN_DIR/static.tsv.tmp"
  mv "$RUN_DIR/static.tsv.tmp" "$RUN_DIR/static.tsv"
  {
    printf 'key=%s\nadmin_token=%s\nkeepalive=%s\nrevoked=%s\n' "$key" "$admin_token" "$KEEPALIVE" "$revoked"
    [ -z "${RELAY_CHISEL:-}" ] || printf 'chisel=%s\n' "$RELAY_CHISEL"
    [ -z "${RELAY_STATE_DIR:-}" ] || printf 'state_dir=%s\n' "$RELAY_STATE_DIR"
    printf 'run_dir=%s\n' "$RUN_DIR"
  } > "$RUN_DIR/relayd.conf.tmp"
  mv "$RUN_DIR/relayd.conf.tmp" "$RUN_DIR/relayd.conf"
  echo "relay: pairing mode; $n_compute compute and $n_work workstation static client(s):$names; $n_revoked revoked; keepalive $KEEPALIVE" >&2
  exec env -i PATH=/usr/local/bin:/usr/bin:/bin ${MU_RELAY_FAULT:+MU_RELAY_FAULT="$MU_RELAY_FAULT"} \
    "$RELAYD" --config "$RUN_DIR/relayd.conf"
fi
[ -n "$json" ] || fail "every client in RELAY_CLIENTS is in RELAY_REVOKED: chisel would start with no users, and chisel with no users lets anyone in"

auth="$RUN_DIR/users.json"
printf '{\n%s\n}\n' "$json" > "$auth.tmp"
mv "$auth.tmp" "$auth"
echo "relay: $n_compute compute and $n_work workstation client(s):$names; keepalive $KEEPALIVE" >&2

# chisel reads the key from CHISEL_KEY_FILE (a path, or the ck- string itself). Everything else is dropped from
# its environment.
exec env -i PATH=/usr/local/bin:/usr/bin:/bin CHISEL_KEY_FILE="$key" \
  "$CHISEL" server --host 0.0.0.0 --port "$LISTEN_PORT" --reverse --authfile "$auth" --keepalive "$KEEPALIVE"
