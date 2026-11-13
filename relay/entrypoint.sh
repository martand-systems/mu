#!/bin/sh
# μ relay entrypoint. Turns the two environment variables into chisel's configuration and starts chisel:
#
#   RELAY_KEY      the server's private key, "ck-..." (relay/gen-credentials.sh init). Clients pin its fingerprint.
#   RELAY_CLIENTS  comma-separated clients, each  <role>:<name>:<password>:<port>[+<port>...]
#                    compute:<name>:<password>:<port>             may open ONE reverse tunnel, listening on the
#                                                                 relay's own loopback 127.0.0.1:<port>
#                    workstation:<name>:<password>:<port>[+...]   may connect to those loopback ports, nothing else
#   RELAY_KEEPALIVE  optional, default 25s (chisel's default; 0s turns the server's pings off)
#
# Nothing else is reachable through the relay: not the other containers on dokploy-network, not Traefik's API, not
# the internet. A compute node's port is bound to the relay container's loopback, so a neighbouring container
# cannot reach it either.
#
# The auth file is written to a tmpfs (/run/relay); the root filesystem is read-only. Nothing secret is printed.
set -eu
umask 077
fail() { echo "relay: $*" >&2; exit 64; }

RUN_DIR="${RELAY_RUN_DIR:-/run/relay}"
CHISEL="${RELAY_CHISEL:-/usr/local/bin/chisel}"
LISTEN_PORT=8080
PORT_MIN=7001
PORT_MAX=7999
KEEPALIVE="${RELAY_KEEPALIVE:-25s}"
key="${RELAY_KEY:-}"
clients="${RELAY_CLIENTS:-}"

[ -n "$key" ] || fail "RELAY_KEY is not set (Dokploy: Environment tab; see relay/README.md)"
case "$key" in ck-?*) ;; *) fail "RELAY_KEY must be a chisel key starting with ck-" ;; esac
case "${key#ck-}" in *[!A-Za-z0-9+/]*) fail "RELAY_KEY has a character outside base64" ;; esac
[ -n "$clients" ] || fail "RELAY_CLIENTS is not set (Dokploy: Environment tab; see relay/README.md)"
case "$clients" in *[!A-Za-z0-9_:,+-]*) fail "RELAY_CLIENTS has a character outside A-Z a-z 0-9 _ - : , +" ;; esac
case "$KEEPALIVE" in *[!0-9sm]*|'') fail "RELAY_KEEPALIVE must look like 25s or 1m" ;; esac
[ -d "$RUN_DIR" ] && [ -w "$RUN_DIR" ] || fail "$RUN_DIR is not a writable directory (compose.yaml mounts a tmpfs there)"

check_port() {  # $1 = entry number, $2 = port
  case "$2" in ''|*[!0-9]*) fail "client $1: port '$2' is not a number" ;; esac
  [ "${#2}" -le 5 ] && [ "$2" -ge "$PORT_MIN" ] && [ "$2" -le "$PORT_MAX" ] ||
    fail "client $1: port $2 is outside $PORT_MIN-$PORT_MAX"
}

json=""; names=" "; compute_ports=" "; n=0; n_compute=0; n_work=0
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
  json="${json:+$json,
}  \"$name:$pass\": [\"$rule\"]"
done
[ "$n" -gt 0 ] || fail "RELAY_CLIENTS has no clients"

auth="$RUN_DIR/users.json"
printf '{\n%s\n}\n' "$json" > "$auth.tmp"
mv "$auth.tmp" "$auth"
echo "relay: $n_compute compute and $n_work workstation client(s):$names; keepalive $KEEPALIVE" >&2

# chisel reads the key from CHISEL_KEY_FILE (a path, or the ck- string itself). Everything else is dropped from
# its environment.
exec env -i PATH=/usr/local/bin:/usr/bin:/bin CHISEL_KEY_FILE="$key" \
  "$CHISEL" server --host 0.0.0.0 --port "$LISTEN_PORT" --reverse --authfile "$auth" --keepalive "$KEEPALIVE"
