#!/usr/bin/env bash
# Spike S4 (docs/PLAN.md §16): does chisel work through Dokploy's Traefik?
#
# Builds a replica of the server on this machine's Docker and tests the relay in it: a reverse tunnel from one
# client, a forward from another, a long idle period, and reconnects. Every result that matters has a negative
# control in the same run: the same check is made once more with the thing it tests broken, and has to notice.
#
#   tools/s4/run.sh         build, test, print the table; exit 0 only if every row passes (S4_ONLY=pairing: only
#                           the routing, tunnel, access and pairing groups)
#   tools/s4/run.sh down    remove everything it made
#   tools/s4/run.sh relay   only Traefik and the relay, left running (used by tools/test-jobs/e2e.sh)
#
# The main relay runs in pairing mode (RELAY_ADMIN_TOKEN set: mu-relayd in front of chisel's auth file, the API on
# /mu/v1/ behind chisel's --backend, the state on a named volume); the second relay stays in the legacy mode (chisel
# alone), so both paths are tested in every run. The P rows test the pairing service (docs/design/pairing.md §13.3).
#
# The replica (see the files next to this one):
#   dokploy-traefik   started with Dokploy's own `docker run` line and Dokploy's static config, on a swarm overlay
#                     network called dokploy-network, with the Traefik version in TRAEFIK_VERSION
#   the relay         relay/compose.yaml as it is in git, started with Dokploy's own `docker compose` line, plus
#                     the labels and network Dokploy adds from its Domains screen (dokploy-inject.yaml.tmpl) and
#                     an env file written the way Dokploy writes relay/.env
#   a second relay    the same, with every keepalive off, the slow health check and no middleware in its Domains
#                     entry (three negative controls)
#   clients           containers outside dokploy-network that reach the relay only through the published 443
#
# relay.martand.systems resolves to this machine inside the client containers only. Nothing here contacts the
# real server, and the script refuses to run on a machine that looks like a Dokploy host.
#
# Settings: TRAEFIK_VERSION (3.6.25), S4_IDLE seconds (240, at least 60), S4_BLOB_MB (100), S4_WORK (.scratch/s4).
#
# S4_BREAK breaks one thing on purpose. The run must then fail in the rows that test it, and only there; CI runs
# each one to test this script. A broken run stops after the group of rows the fault belongs to.
#   port     the wrong container port in Dokploy's labels             -> A1
#   acl      the relay without its access rules (break-acl.yaml)      -> X6 X7 X8 X16 X17
#   idle     the keepalive-off workstation goes through the hop that cuts idle connections   -> I2
#   noretry  the workstation's chisel client does not reconnect       -> R1
# The pairing breaks skip the connection-limit, idle and reconnect groups (S4_ONLY=pairing). The relay faults need
# mu-relayd built with -tags faults (the build argument MU_RELAYD_TAGS, never set for a real deploy):
#   adminprefix  mu-relayd compares only the admin token's first 8 characters (fault admin-prefix)   -> P2
#   noreload     the auth file is not rewritten after a change (no-reload)        -> P4 P5 P8
#   wideacl      a workstation's rule allows every port 7000-7999 (wide-ports)    -> X16 P5
#   noevict      a removed client's sessions are not cut (no-evict)               -> P8
#   allowempty   no sentinel, no check, the last client can go (allow-empty)      -> P11
#   novolume     the state on a tmpfs instead of the named volume                 -> X14 P9
#   skipmac      the reference pairing client skips its MAC checks (MU_PAIR_FAULT=skip-mac)   -> P7
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S4="$ROOT/tools/s4"
WORK="${S4_WORK:-$ROOT/.scratch/s4}"
TRAEFIK_VERSION="${TRAEFIK_VERSION:-3.6.25}"
IDLE="${S4_IDLE:-240}"
MB="${S4_BLOB_MB:-100}"
BREAK="${S4_BREAK:-}"
ONLY="${S4_ONLY:-}"
case "$BREAK" in ''|port|acl|idle|noretry) ;;
  adminprefix|noreload|wideacl|noevict|allowempty|novolume|skipmac) ONLY=pairing ;;
  *) echo "run.sh: S4_BREAK must be port, acl, idle, noretry, adminprefix, noreload, wideacl, noevict, allowempty, novolume or skipmac" >&2; exit 2 ;; esac
case "$ONLY" in ''|pairing) ;; *) echo "run.sh: S4_ONLY must be empty or pairing" >&2; exit 2 ;; esac
case "$BREAK" in
  adminprefix) RELAY_FAULT=admin-prefix ;; noreload) RELAY_FAULT=no-reload ;; wideacl) RELAY_FAULT=wide-ports ;;
  noevict) RELAY_FAULT=no-evict ;; allowempty) RELAY_FAULT=allow-empty ;; *) RELAY_FAULT="" ;;
esac
PAIR_FAULT=""; [ "$BREAK" != skipmac ] || PAIR_FAULT=skip-mac
case "$IDLE" in ''|*[!0-9]*) echo "run.sh: S4_IDLE must be a number of seconds" >&2; exit 2 ;; esac
# Under 60 s the hop that cuts after 45 s idle has not cut yet, and its control (I3) would fail for no reason.
[ "$IDLE" -ge 60 ] || { echo "run.sh: S4_IDLE must be at least 60" >&2; exit 2; }
HOST=relay.martand.systems
HOST_SILENT=relay-silent.s4.test
MIDDLEWARE=mu-relay-inflight@docker         # what the owner types into the Domains entry (relay/README.md §2)
APP=mu-relay-ci0001;        ROUTER="$APP-1"          # <appName> and <appName>-<uniqueConfigKey>, as Dokploy names them
APP_SILENT=mu-relay-ci0002; ROUTER_SILENT="$APP_SILENT-2"
OFFICE_NET=s4-clients_office
export S4_WORK="$WORK" S4_BLOB_MB="$MB"
[ "$BREAK" != idle ] || export S4_SILENT_URL=https://killer:443 S4_SILENT_HOSTNAME="$HOST_SILENT"
[ "$BREAK" != noretry ] || export S4_WS_MAX_RETRY=0
MAIN_FILES=(-f relay/compose.yaml -f "$WORK/inject-main.yaml")
[ "$BREAK" != acl ] || MAIN_FILES+=(-f tools/s4/break-acl.yaml)
[ -z "$RELAY_FAULT" ] || MAIN_FILES+=(-f "$WORK/break-fault.yaml")
[ "$BREAK" != novolume ] || MAIN_FILES+=(-f tools/s4/break-novolume.yaml)
VOLUME="${APP}_relay-state"      # what Docker calls compose.yaml's volume in Dokploy's project
N_FAIL=0

# ---------------------------------------------------------------------------------------------------- helpers
say()  { printf '\n== %s\n' "$*"; }
now()  { python3 -c 'import time; print("%.3f" % time.time())'; }
since() { python3 -c 'import sys,time; print("%.1f" % (time.time() - float(sys.argv[1])))' "$1"; }
lt()   { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a + 0 <  b + 0) }'; }
ge()   { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a + 0 >= b + 0) }'; }
val()  { sed -n "s/^$2=//p" "$1" | tail -n 1; }                       # val FILE KEY
json() { python3 -c 'import sys,json; print(json.load(open(sys.argv[1])).get(sys.argv[2]))' "$1" "$2" 2>/dev/null; }

row() {  # row PASS|FAIL|INFO ID WHAT MEASURED
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" >> "$WORK/results.tsv"
  printf '%-4s %-4s %s: %s\n' "$1" "$2" "$3" "$4"
  [ "$1" != FAIL ] || N_FAIL=$((N_FAIL + 1))
}
check() {  # check ID WHAT MEASURED 'CONDITION'   PASS if the condition holds. The condition is written in single
           # quotes and expanded here, so a measured value is never parsed as shell code; it sees the caller's locals.
  if eval "$4"; then row PASS "$1" "$2" "$3"; else row FAIL "$1" "$2" "$3"; fi
}

dc_main()   { ( cd "$ROOT" && env -i PATH="$PATH" HOME="$HOME" docker compose -p "$APP" --env-file relay/.env \
                  "${MAIN_FILES[@]}" "$@" ); }
dc_silent() { ( cd "$ROOT" && env -i PATH="$PATH" HOME="$HOME" docker compose -p "$APP_SILENT" --env-file "$WORK/relay-silent.env" \
                  -f relay/compose.yaml -f "$WORK/inject-silent.yaml" -f tools/s4/slow-health.yaml "$@" ); }
dc_clients() { docker compose -f "$S4/compose.clients.yaml" "$@"; }
probe()     { docker exec s4-probe "$@"; }

ours() { [ "$(docker inspect -f '{{index .Config.Labels "mu.s4"}}' "$1" 2>/dev/null)" = 1 ]; }
net_ours() { [ "$(docker network inspect -f '{{index .Labels "mu.s4"}}' "$1" 2>/dev/null)" = 1 ]; }

started_at() {  # container -> epoch seconds of its last start
  python3 -c '
import calendar, re, sys
m = re.match(r"(\d+)-(\d+)-(\d+)T(\d+):(\d+):(\d+)(\.\d+)?Z", sys.argv[1])
print("%.3f" % (calendar.timegm(tuple(int(x) for x in m.groups()[:6]) + (0, 0, 0)) + float(m.group(7) or 0)))
' "$(docker inspect -f '{{.State.StartedAt}}' "$1")"
}
nlog()  { local n; n="$(docker logs "$1" 2>&1 | grep -c "$2")"; echo "${n:-0}"; }   # lines in a container log matching
conns() { echo "$(nlog "$1" 'client: Connected')/$(nlog "$1" 'client: Disconnected')"; }   # "connected/disconnected"
nonce() { docker exec "$1" cat /data/nonce 2>/dev/null; }
nonce_changed() { local n; n="$(nonce s4-compute)"; [ -n "$n" ] && [ "$n" != "$1" ] && echo changed; }
ping_via() { docker exec "$1" curl -s -m 3 http://127.0.0.1:7001/ping 2>/dev/null; }      # through that workstation's forward
wait_ping() {  # wait_ping WORKSTATION EXPECTED SECONDS   until the forward answers EXPECTED
  local end=$(( $(date +%s) + $3 ))
  while [ "$(date +%s)" -le "$end" ]; do
    [ "$(ping_via "$1")" = "$2" ] && return 0
    sleep 0.3
  done
  return 1
}
wait_log() {  # wait_log CONTAINER PATTERN SECONDS
  local end=$(( $(date +%s) + $3 ))
  while [ "$(date +%s)" -le "$end" ]; do
    docker logs "$1" 2>&1 | grep -q "$2" && return 0
    sleep 0.5
  done
  return 1
}
wait_code() {  # wait_code EXPECTED SECONDS COMMAND...   until the command prints EXPECTED
  local want="$1" end=$(( $(date +%s) + $2 )); shift 2
  while [ "$(date +%s)" -le "$end" ]; do
    [ "$("$@" 2>/dev/null)" = "$want" ] && return 0
    sleep 0.3
  done
  return 1
}
adhoc() {  # adhoc NAME SECONDS AUTH FINGERPRINT REMOTE   one chisel client attempt from the probe container
  docker exec -e AUTH="$3" s4-probe timeout "$2" chisel client --max-retry-count 0 --tls-ca /certs/ca.crt \
    --fingerprint "$4" "https://$HOST" "$5" > "$WORK/out/adhoc-$1.log" 2>&1
  true
}
adhoc_connected() { grep -q 'client: Connected' "$WORK/out/adhoc-$1.log"; }
adhoc_says()      { grep -qi "$2" "$WORK/out/adhoc-$1.log"; }
adhoc_last()      { grep 'client: Connected\|client: Connection error' "$WORK/out/adhoc-$1.log" | tail -n 1 | sed -e 's/^[0-9/]* [0-9:]* client: //' -e 's/^Connection error: //' | cut -c1-100; }
rawchan() {  # rawchan NAME AUTH DECLARED TARGET...   a client that is not chisel's (client/rawchan/main.go)
  local name="$1" auth="$2"; shift 2
  docker exec -e AUTH="$auth" s4-probe timeout 150 rawchan "https://$HOST" /certs/ca.crt "$FP" "$@" > "$WORK/out/rawchan-$name.log" 2>&1
  true
}
rawchan_line()   { grep "^channel $2 " "$WORK/out/rawchan-$1.log" | cut -d' ' -f3- | cut -c1-70; }       # what happened to that target
rawchan_denied() { grep -q '^config accepted$' "$WORK/out/rawchan-$1.log" && grep -q "^channel $2 REJECTED .*access denied" "$WORK/out/rawchan-$1.log"; }
rawchan_opened() { grep -c '^channel .* OPENED' "$WORK/out/rawchan-$1.log"; }
flood() {  # flood HOSTNAME COUNT HOLD_SECONDS   from the neighbour container, straight to Traefik on dokploy-network
  docker exec s4-noise python3 /s4/ws_flood.py dokploy-traefik 443 /certs/ca.crt "$1" "$2" "$3" 2>&1
}

collect() {
  mkdir -p "$WORK/logs"
  local c
  for c in dokploy-traefik s4-compute s4-ws s4-compute-silent s4-ws-silent s4-killer s4-ws-silent-cut s4-ws-ka-cut s4-noise s4-lab; do
    docker logs "$c" > "$WORK/logs/$c.log" 2>&1 || true
  done
  for c in $(dc_main ps -aq 2>/dev/null); do docker logs "$c" > "$WORK/logs/relay.log" 2>&1 || true; done
  for c in $(dc_silent ps -aq 2>/dev/null); do docker logs "$c" > "$WORK/logs/relay-silent.log" 2>&1 || true; done
  docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}' > "$WORK/logs/ps.txt" 2>&1 || true
}
summary() {
  {
    echo "### S4 relay, Traefik v$TRAEFIK_VERSION${BREAK:+ (deliberately broken: $BREAK)}"
    echo
    echo "chisel $(docker run --rm --entrypoint chisel s4-client --version 2>/dev/null), idle $IDLE s, payload $MB MB, $(docker version -f 'Docker {{.Server.Version}}' 2>/dev/null)"
    echo
    echo "| | # | Check | Measured |"
    echo "|---|---|---|---|"
    awk -F '\t' '{ gsub(/\|/, "/"); printf "| %s | %s | %s | %s |\n", $1, $2, $3, $4 }' "$WORK/results.tsv"
    echo
    echo "$N_FAIL failed."
  } > "$WORK/summary.md"
  echo; cat "$WORK/summary.md"
}
finish() { collect; summary; [ "$N_FAIL" -eq 0 ] || exit 1; exit 0; }
die()   { echo "run.sh: $*" >&2; [ ! -d "$WORK" ] || { tail -n 30 "$WORK/logs/compose.log" 2>/dev/null; collect; }; exit 2; }
abort() { row FAIL "$1" "$2" "$3"; echo "run.sh: stopping here, the rest needs this" >&2; finish; }

guard() {
  command -v docker >/dev/null || die "docker is needed"
  docker info >/dev/null 2>&1 || die "the Docker daemon is not reachable"
  [ ! -e /etc/dokploy ] || die "/etc/dokploy exists: this is a Dokploy host. This script only runs on a scratch machine"
  if docker inspect dokploy-traefik >/dev/null 2>&1 && ! ours dokploy-traefik; then
    die "a container called dokploy-traefik exists that this script did not make: refusing to touch a real Dokploy"
  fi
  if docker network inspect dokploy-network >/dev/null 2>&1 && ! net_ours dokploy-network; then
    die "a network called dokploy-network exists that this script did not make: refusing to touch a real Dokploy"
  fi
  if [ -e "$ROOT/relay/.env" ] && ! grep -q "^APP_NAME=\"$APP\"\$" "$ROOT/relay/.env"; then
    die "relay/.env exists and is not this script's: move it away first (credentials do not belong in the checkout)"
  fi
}

down() {
  guard
  [ ! -d "$WORK" ] || { dc_clients down -v --remove-orphans; dc_main down -v --remove-orphans; dc_silent down -v --remove-orphans; } >/dev/null 2>&1
  ! ours s4-lab || docker rm -f s4-lab >/dev/null 2>&1
  ! ours dokploy-traefik || docker rm -f dokploy-traefik >/dev/null 2>&1
  ! net_ours dokploy-network || docker network rm dokploy-network >/dev/null 2>&1
  [ ! -e "$WORK/swarm-by-s4" ] || docker swarm leave --force >/dev/null 2>&1
  rm -f "$ROOT/relay/.env"
  rm -rf "$WORK" 2>/dev/null || true      # Traefik's acme.json in there belongs to root
}

dokploy_env() {  # dokploy_env APPNAME CREDENTIAL_FILE [KEY=VALUE...]   the env file as Dokploy writes it
  local app="$1" cred="$2" kv; shift 2
  printf 'APP_NAME="%s"\nCOMPOSE_PROJECT_NAME="%s"\n' "$app" "$app"
  sed -n 's/^\(RELAY_[A-Z_]*\)=\(.*\)$/\1="\2"/p' "$cred"
  for kv in "$@"; do printf '%s="%s"\n' "${kv%%=*}" "${kv#*=}"; done
  # Dokploy appends DOCKER_CONFIG=/root/.docker (it deploys as root), and docker compose obeys it. The replica
  # is not root, so the line points at this user's Docker configuration instead.
  printf 'DOCKER_CONFIG="%s"\n' "${DOCKER_CONFIG:-$HOME/.docker}"
}
inject() {  # inject HOST ROUTER PORT [MIDDLEWARES]   Dokploy's labels and networks for that Domains entry
  if [ -n "${4:-}" ]; then sed -e "s/@HOST@/$1/g" -e "s/@ROUTER@/$2/g" -e "s/@PORT@/$3/g" -e "s/@MIDDLEWARES@/$4/g" "$S4/dokploy-inject.yaml.tmpl"
  else sed -e "s/@HOST@/$1/g" -e "s/@ROUTER@/$2/g" -e "s/@PORT@/$3/g" -e '/^ *- .*@MIDDLEWARES@/d' -e 's/@MIDDLEWARES@/none/' "$S4/dokploy-inject.yaml.tmpl"; fi
}

# ------------------------------------------------------------------------------------------------------ set-up
setup() {
  say "Set-up: certificates, credentials, swarm, dokploy-network, images"
  mkdir -p "$WORK"/{env,cred,out,logs,certs,traefik/dynamic/certificates}
  : > "$WORK/results.tsv"

  "$S4/mkcert.sh" "$WORK/certs" ca || die "mkcert ca"
  "$S4/mkcert.sh" "$WORK/certs" leaf relay "$HOST" "$HOST_SILENT" || die "mkcert relay"
  "$S4/mkcert.sh" "$WORK/certs" leaf probe alpn-probe.s4.test || die "mkcert probe"
  cp "$S4/traefik/traefik.yml" "$WORK/traefik/traefik.yml"
  cp "$S4"/traefik/dynamic/*.yml "$WORK/traefik/dynamic/"
  cp "$WORK/certs/relay.crt" "$WORK/certs/relay.key" "$WORK/traefik/dynamic/certificates/"

  # Throw-away credentials, made the way the owner will make the real ones.
  local g="$ROOT/relay/gen-credentials.sh"
  "$g" --env "$WORK/cred/main.env" --url "https://$HOST" init >/dev/null 2>&1 || die "gen-credentials init"
  "$g" --env "$WORK/cred/main.env" --url "https://$HOST" add-client compute office-pc 7001 > "$WORK/env/compute.env" 2>/dev/null || die "add-client"
  "$g" --env "$WORK/cred/main.env" --url "https://$HOST" add-client workstation avi-mac 7001 > "$WORK/env/workstation.env" 2>/dev/null || die "add-client"
  # the main relay in pairing mode: the token made the way the owner makes it; the silent one stays legacy
  "$g" --env "$WORK/cred/main.env" admin-token >/dev/null 2>&1 || die "gen-credentials admin-token"
  grep '^RELAY_ADMIN_TOKEN=' "$WORK/cred/main.env" > "$WORK/env/admin.env"
  if [ -n "$RELAY_FAULT" ]; then
    printf 'services:\n  mu-relay:\n    build:\n      args:\n        MU_RELAYD_TAGS: faults\n    environment:\n      MU_RELAY_FAULT: %s\n' "$RELAY_FAULT" > "$WORK/break-fault.yaml"
  fi
  "$g" --env "$WORK/cred/silent.env" --url "https://$HOST_SILENT" init >/dev/null 2>&1 || die "gen-credentials init"
  "$g" --env "$WORK/cred/silent.env" --url "https://$HOST_SILENT" add-client compute office-pc 7001 > "$WORK/env/compute-silent.env" 2>/dev/null || die "add-client"
  "$g" --env "$WORK/cred/silent.env" --url "https://$HOST_SILENT" add-client workstation avi-mac 7001 > "$WORK/env/workstation-silent.env" 2>/dev/null || die "add-client"
  dokploy_env "$APP" "$WORK/cred/main.env" > "$ROOT/relay/.env"
  dokploy_env "$APP_SILENT" "$WORK/cred/silent.env" RELAY_KEEPALIVE=0s > "$WORK/relay-silent.env"
  local port=8080; [ "$BREAK" != port ] || port=8081
  inject "$HOST" "$ROUTER" "$port" "$MIDDLEWARE" > "$WORK/inject-main.yaml"
  inject "$HOST_SILENT" "$ROUTER_SILENT" 8080 > "$WORK/inject-silent.yaml"     # the second relay's router has no middleware

  # As Dokploy's server set-up does: a one-node swarm and an attachable overlay network.
  if [ "$(docker info -f '{{.Swarm.LocalNodeState}}' 2>/dev/null)" != active ]; then
    docker swarm init --advertise-addr 127.0.0.1 >/dev/null || die "docker swarm init"
    touch "$WORK/swarm-by-s4"
  fi
  docker network create --driver overlay --attachable --label mu.s4=1 dokploy-network >/dev/null || die "network create"

  dc_main build || die "the relay image did not build"
  [ "${1:-}" = relay-only ] || docker build -q --build-arg RELAY_IMAGE="$APP-mu-relay" -t s4-client "$S4/client" >/dev/null || die "the client image did not build"

  # Dokploy's createTraefikInstance(), with the config directory under $WORK instead of /etc/dokploy/traefik.
  docker run -d --name dokploy-traefik --restart always --label mu.s4=1 \
    -v "$WORK/traefik/traefik.yml:/etc/traefik/traefik.yml" \
    -v "$WORK/traefik/dynamic:/etc/dokploy/traefik/dynamic" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -p 443:443 -p 80:80 -p 443:443/udp "traefik:v$TRAEFIK_VERSION" >/dev/null || die "Traefik did not start"
  docker network connect dokploy-network dokploy-traefik || die "network connect"

  [ "${1:-}" = relay-only ] && return 0
  dc_clients up -d probe noise killer >> "$WORK/logs/compose.log" 2>&1 || die "the probe containers did not start"
}

# relay: only Traefik and the main relay, left running for another test (tools/test-jobs/e2e.sh). The throw-away
# credentials are in $WORK/env/compute.env (office-pc, port 7001) and $WORK/env/workstation.env (avi-mac); the test
# CA is $WORK/certs/ca.crt. Clients on this machine reach https://relay.martand.systems once /etc/hosts sends that name
# to 127.0.0.1 (the caller's job; this script never touches the host's files).
relay_only() {
  down; guard; setup relay-only
  dc_main up -d --build --remove-orphans >> "$WORK/logs/compose.log" 2>&1 || die "the relay did not start"
  local i
  for i in $(seq 1 120); do
    [ "$(curl -s -m 3 --cacert "$WORK/certs/ca.crt" --resolve "$HOST:443:127.0.0.1" "https://$HOST/health" 2>/dev/null)" = OK ] && {
      echo "relay up: https://$HOST (Traefik v$TRAEFIK_VERSION), credentials in $WORK/env/"; return 0; }
    sleep 1
  done
  die "the relay did not answer https://$HOST/health within 120 s"
}

# ------------------------------------------------------------------------------------------------------- tests
test_routing() {
  say "Traefik and routing"
  local v; v="$(docker exec dokploy-traefik traefik version 2>/dev/null | awk '/^Version:/ { print $2 }')"
  check T1 "Traefik is the version under test" "v$v" '[[ $v == "$TRAEFIK_VERSION" ]]'

  # Before the relay exists the host must answer 404: the 200 further down comes from the relay, not from Traefik.
  local code_cmd=(probe curl -s -m 3 -o /dev/null -w '%{http_code}' --cacert /certs/ca.crt "https://$HOST/health")
  if wait_code 404 45 "${code_cmd[@]}"; then row PASS T2 "control: with no relay deployed, https://$HOST/health is not served" "404"
  else abort T2 "control: with no relay deployed, https://$HOST/health is not served" "got $("${code_cmd[@]}" 2>&1), expected 404"; fi

  # The second relay first: its health check is the slow one, so it needs about 30 s.
  dc_silent up -d --build --remove-orphans >> "$WORK/logs/compose.log" 2>&1 || die "the second relay did not start"
  RELAY_SILENT="$(dc_silent ps -q mu-relay)"
  probe python3 /s4/wait_http.py "https://$HOST_SILENT/health" /certs/ca.crt 90 > "$WORK/out/route-silent.json" &
  local silent_pid=$!

  # The relay, started the way Dokploy starts it.
  probe python3 /s4/wait_http.py "https://$HOST/health" /certs/ca.crt 60 > "$WORK/out/route-main.json" &
  local main_pid=$!
  dc_main up -d --build --remove-orphans >> "$WORK/logs/compose.log" 2>&1 || die "the relay did not start"
  RELAY="$(dc_main ps -q mu-relay)"
  wait "$main_pid"
  local t
  if [ "$(json "$WORK/out/route-main.json" ok)" = True ]; then
    t="$(python3 -c 'import sys; print("%.1f" % (float(sys.argv[1]) - float(sys.argv[2])))' "$(json "$WORK/out/route-main.json" t)" "$(started_at "$RELAY")")"
    check A1 "Relay answers through Traefik after its container starts (limit 10 s)" "$t s; before that: $(json "$WORK/out/route-main.json" before)" 'lt "$t" 10'
  else
    abort A1 "Relay answers through Traefik after its container starts (limit 10 s)" "never in 60 s; answers: $(json "$WORK/out/route-main.json" before)"
  fi

  local out rc
  out="$(probe curl -s -m 5 --cacert /certs/ca.crt -w ' %{http_code}' "https://$HOST/health" | tr -d '\n')"
  check A2 "https://$HOST/health, certificate verified" "$out" '[[ $out == "OK 200" ]]'
  probe curl -s -m 5 -o /dev/null "https://$HOST/health"; rc=$?
  check A3 "control: curl without the test CA refuses the certificate" "curl exit $rc (60 = certificate not trusted)" '[[ $rc == 60 ]]'
  out="$(probe curl -s -m 5 -o /dev/null -w '%{http_code} %{redirect_url}' "http://$HOST/health")"
  check A4 "http:// is redirected to https://" "$out" '[[ $out == "301 https://$HOST/health" || $out == "308 https://$HOST/health" ]]'
  out="$(probe curl -s -m 5 -o /dev/null -w '%{http_code}' --cacert /certs/ca.crt -H 'Host: nope.s4.test' "https://$HOST/health")"
  check A5 "control: a host name with no router" "$out" '[[ $out == 404 ]]'

  say "WebSocket upgrade"
  out="$(probe python3 /s4/ws_probe.py "$HOST" 443 /certs/ca.crt chisel-v3 2>&1)"; echo "$out"
  check W1 "Upgrade through Traefik with no ALPN offered (as chisel does)" \
    "$(echo "$out" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["status_line"] + ", ALPN " + str(d["alpn_selected"]) + ", " + d["tls"])' 2>/dev/null)" \
    '[[ $out == *"\"status_line\": \"HTTP/1.1 101 Switching Protocols\""* && $out == *"\"alpn_selected\": null"* ]]'
  out="$(probe python3 /s4/ws_probe.py "$HOST" 443 /certs/ca.crt chisel-v0 2>&1)"; echo "$out"
  check W2 "control: the same upgrade with a protocol chisel does not speak gets no 101" \
    "$(echo "$out" | python3 -c 'import sys,json; print(json.load(sys.stdin)["status_line"])' 2>/dev/null)" \
    '[[ $out == *"\"status_line\": \"HTTP/1.1 "* && $out != *" 101 "* ]]'
  out="$(probe sh -c 'python3 /s4/alpn_probe.py /certs/probe.crt /certs/probe.key 18445 2>/dev/null & sleep 1
        chisel client --max-retry-count 0 --tls-ca /certs/ca.crt --hostname alpn-probe.s4.test https://127.0.0.1:18445 127.0.0.1:17009:127.0.0.1:1 >/dev/null 2>&1
        wait')"; echo "$out"
  check W3 "What the chisel client sends: no ALPN, an HTTP/1.1 upgrade" \
    "$(echo "$out" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("ALPN offered %s; %s; Upgrade: %s; %s" % (d["alpn_offered"], d["request_line"], d["upgrade"], d["subprotocol"]))' 2>/dev/null)" \
    '[[ $out == *"\"alpn_offered\": []"* && $out == *"\"request_line\": \"GET / HTTP/1.1\""* && $out == *"\"upgrade\": \"websocket\""* && $out == *"\"subprotocol\": \"chisel-v3\""* ]]'
  out="$(probe sh -c 'python3 /s4/alpn_probe.py /certs/probe.crt /certs/probe.key 18445 2>/dev/null & sleep 1
        curl -s -o /dev/null --cacert /certs/ca.crt --resolve alpn-probe.s4.test:18445:127.0.0.1 https://alpn-probe.s4.test:18445/
        wait')"; echo "$out"
  check W4 "control: the same probe sees curl offer h2" \
    "$(echo "$out" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("ALPN offered %s, selected %s" % (d["alpn_offered"], d["alpn_selected"]))' 2>/dev/null)" \
    '[[ $out == *"\"alpn_offered\": [\"h2\""* ]]'

  wait "$silent_pid"
  if [ "$(json "$WORK/out/route-silent.json" ok)" = True ]; then
    t="$(python3 -c 'import sys; print("%.1f" % (float(sys.argv[1]) - float(sys.argv[2])))' "$(json "$WORK/out/route-silent.json" t)" "$(started_at "$RELAY_SILENT")")"
    check A6 "control: with no start period in the health check (Docker's first probe after 30 s) the relay is not routed until then" "$t s; before that: $(json "$WORK/out/route-silent.json" before)" 'ge "$t" 25'
  else
    abort A6 "control: with no start period in the health check (Docker's first probe after 30 s) the relay is not routed until then" "never in 90 s; answers: $(json "$WORK/out/route-silent.json" before)"
  fi
}

test_tunnels() {
  say "Tunnels: a reverse tunnel from the compute container, a forward from the workstation container"
  FP="$(val "$WORK/env/compute.env" RELAY_FINGERPRINT)";            FP_SILENT="$(val "$WORK/env/compute-silent.env" RELAY_FINGERPRINT)"
  C_AUTH="office-pc:$(val "$WORK/env/compute.env" RELAY_PASSWORD)"; W_AUTH="avi-mac:$(val "$WORK/env/workstation.env" RELAY_PASSWORD)"
  dc_clients up -d compute workstation compute-silent ws-silent ws-silent-cut ws-ka-cut >> "$WORK/logs/compose.log" 2>&1 || die "the clients did not start"
  local c t0; t0="$(now)"
  for c in s4-compute s4-ws s4-compute-silent s4-ws-silent s4-ws-silent-cut s4-ws-ka-cut; do
    wait_log "$c" 'client: Connected' 60 || abort E1 "All six clients connect through Traefik" "$c did not: $(docker logs "$c" 2>&1 | tail -n 2 | tr '\n' ' ')"
  done
  row PASS E1 "All six clients connect through Traefik, fingerprint pinned" "within $(since "$t0") s; $(docker logs s4-ws 2>&1 | grep -o 'Connected (Latency [^)]*)' | head -n 1)"

  NONCE="$(nonce s4-compute)"; local nonce_silent; nonce_silent="$(nonce s4-compute-silent)"
  wait_ping s4-ws "pong $NONCE" 15
  local a b d e
  a="$(ping_via s4-ws)"; b="$(ping_via s4-ws-silent)"; d="$(ping_via s4-ws-ka-cut)"; e="$(ping_via s4-ws-silent-cut)"
  check E2 "Workstation reaches the compute container's loopback service (answer carries a nonce made there)" \
    "main, silent, and both through the idle-cutting hop: $( [ "$a" = "pong $NONCE" ] && echo ok || echo "'$a'" ) $( [ "$b" = "pong $nonce_silent" ] && echo ok || echo "'$b'" ) $( [ "$d" = "pong $NONCE" ] && echo ok || echo "'$d'" ) $( [ "$e" = "pong $nonce_silent" ] && echo ok || echo "'$e'" )" \
    '[[ -n $NONCE && -n $nonce_silent && $NONCE != "$nonce_silent" && $a == "pong $NONCE" && $b == "pong $nonce_silent" && $d == "pong $NONCE" && $e == "pong $nonce_silent" ]]'
  T_SILENT_LAST="$(now)"     # no check touches the silent tunnel again until the idle period is over

  say "Payload: $MB MB each way"
  local want got size secs line bytes
  want="$(docker exec s4-compute cat /data/blob.sha256)"
  line="$(docker exec s4-ws sh -c 'curl -s -m 600 -o /data/dl.bin -w "%{size_download} %{time_total}\n" http://127.0.0.1:7001/blob; sha256sum /data/dl.bin | cut -d" " -f1' | tr '\n' ' ')"
  size="$(echo "$line" | cut -d' ' -f1)"; secs="$(echo "$line" | cut -d' ' -f2)"; got="$(echo "$line" | cut -d' ' -f3)"
  check D1 "Download $MB MB, SHA-256 equal" "$size bytes in $secs s ($(awk -v m="$MB" -v s="$secs" 'BEGIN { printf "%.0f", (s > 0) ? m / s : 0 }') MB/s)" \
    '[[ -n $want && $got == "$want" && $size == $((MB * 1048576)) ]]'
  line="$(docker exec s4-ws sh -c 'curl -s -m 600 -o /data/dl.bin -w "%{size_download}\n" "http://127.0.0.1:7001/blob?corrupt=1"; sha256sum /data/dl.bin | cut -d" " -f1' | tr '\n' ' ')"
  size="$(echo "$line" | cut -d' ' -f1)"; got="$(echo "$line" | cut -d' ' -f2)"
  check D2 "control: one flipped bit in the download is noticed" "$size bytes, SHA-256 $( [ "$got" = "$want" ] && echo equal || echo differs )" \
    '[[ $size == $((MB * 1048576)) && ${#got} == 64 && $got != "$want" ]]'
  want="$(docker exec s4-ws cat /data/up.sha256)"
  line="$(docker exec s4-ws curl -s -m 600 -T /data/up.bin -w ' %{time_total}' http://127.0.0.1:7001/upload | tr '\n' ' ')"
  got="$(echo "$line" | sed -n 's/.*"sha256": "\([0-9a-f]*\)".*/\1/p')"; bytes="$(echo "$line" | sed -n 's/.*"bytes": \([0-9]*\).*/\1/p')"; secs="${line##* }"
  check D3 "Upload $MB MB, SHA-256 equal" "$bytes bytes in $secs s ($(awk -v m="$MB" -v s="$secs" 'BEGIN { printf "%.0f", (s > 0) ? m / s : 0 }') MB/s)" \
    '[[ -n $want && $got == "$want" && $bytes == $((MB * 1048576)) ]]'
  line="$(docker exec s4-ws curl -s -m 600 -T /data/up.bin 'http://127.0.0.1:7001/upload?corrupt=1')"
  got="$(echo "$line" | sed -n 's/.*"sha256": "\([0-9a-f]*\)".*/\1/p')"; bytes="$(echo "$line" | sed -n 's/.*"bytes": \([0-9]*\).*/\1/p')"
  check D4 "control: one flipped bit in the upload is noticed" "$bytes bytes, SHA-256 $( [ "$got" = "$want" ] && echo equal || echo differs )" \
    '[[ $bytes == $((MB * 1048576)) && ${#got} == 64 && $got != "$want" ]]'
  local peak; peak="$(docker exec "$RELAY" cat /sys/fs/cgroup/memory.peak 2>/dev/null)"
  row INFO D5 "Relay container's memory: the highest it has been, and now, after the transfers (limits: 256 MiB, 0.5 CPU)" \
    "peak $(awk -v b="${peak:-0}" 'BEGIN { if (b > 0) printf "%.1f MiB", b / 1048576; else printf "not readable" }'); $(docker stats --no-stream --format 'now {{.MemUsage}}, {{.PIDs}} processes' "$RELAY" 2>/dev/null)"
}

test_access() {
  say "Access rules (relay/entrypoint.sh) and container hardening"
  local w_pw_bad="avi-mac:000000000000000000000000000000000000000000000000" ip out rc
  adhoc allowed 6 "$W_AUTH" "$FP" 127.0.0.1:7901:127.0.0.1:7001
  check X1 "control: the workstation's own forward is accepted" "$(adhoc_last allowed)" 'adhoc_connected allowed'
  adhoc badpw 20 "$w_pw_bad" "$FP" 127.0.0.1:7901:127.0.0.1:7001
  check X2 "A wrong password is refused" "$(adhoc_last badpw)" '! adhoc_connected badpw && adhoc_says badpw "authenticat"'
  adhoc noauth 20 "" "$FP" 127.0.0.1:7901:127.0.0.1:7001
  check X3 "No credentials are refused" "$(adhoc_last noauth)" '! adhoc_connected noauth && adhoc_says noauth "authenticat"'
  adhoc badfp 20 "$W_AUTH" "$FP_SILENT" 127.0.0.1:7901:127.0.0.1:7001
  check X4 "The client refuses a relay whose key is not the pinned one" "$(adhoc_last badfp)" '! adhoc_connected badfp && adhoc_says badfp "invalid fingerprint"'

  out="$(docker exec "$RELAY" wget -q -T 3 -O - http://dokploy-traefik:8080/api/version 2>&1)"
  check X5 "control: the relay container itself can reach Traefik's API on dokploy-network" "$(echo "$out" | cut -c1-60)" '[[ $out == *"\"Version\""* ]]'
  adhoc traefik 20 "$W_AUTH" "$FP" 127.0.0.1:7902:dokploy-traefik:8080
  check X6 "A workstation cannot forward to it (or to anything but its compute port)" "$(adhoc_last traefik)" '! adhoc_connected traefik && adhoc_says traefik "denied"'
  adhoc wsrev 20 "$W_AUTH" "$FP" R:127.0.0.1:7002:127.0.0.1:9000
  check X7 "A workstation cannot open a reverse tunnel" "$(adhoc_last wsrev)" '! adhoc_connected wsrev && adhoc_says wsrev "denied"'
  adhoc cany 20 "$C_AUTH" "$FP" R:0.0.0.0:7001:127.0.0.1:9000
  adhoc cport 20 "$C_AUTH" "$FP" R:127.0.0.1:7002:127.0.0.1:9000
  adhoc cfwd 20 "$C_AUTH" "$FP" 127.0.0.1:7903:127.0.0.1:7001
  check X8 "A compute node cannot listen on another address or port, or open forwards" "$(adhoc_last cany) / $(adhoc_last cport) / $(adhoc_last cfwd)" \
    '! adhoc_connected cany && adhoc_says cany "denied" && ! adhoc_connected cport && adhoc_says cport "denied" && ! adhoc_connected cfwd && adhoc_says cfwd "denied"'
  local before; before="$(conns s4-compute)"
  adhoc csecond 20 "$C_AUTH" "$FP" R:127.0.0.1:7001:127.0.0.1:9000
  check X9 "A second client with the compute node's credentials cannot take its port while it is connected" "$(adhoc_last csecond); first client $(conns s4-compute) connects/disconnects" \
    '! adhoc_connected csecond && [[ $(conns s4-compute) == "$before" && $(ping_via s4-ws) == "pong $NONCE" ]]'

  ip="$(docker inspect -f '{{(index .NetworkSettings.Networks "dokploy-network").IPAddress}}' "$RELAY")"
  out="$(docker exec s4-noise curl -s -m 3 -o /dev/null -w '%{http_code}' "http://$ip:8080/health")"
  check X10 "control: a neighbour on dokploy-network reaches the relay's own port 8080" "$out" '[[ $out == 200 ]]'
  docker exec s4-noise curl -s -m 3 -o /dev/null "http://$ip:7001/ping"; rc=$?
  out="$(docker exec "$RELAY" wget -q -T 3 -O - http://127.0.0.1:7001/ping 2>&1)"
  check X11 "The neighbour cannot reach the compute node's port; inside the relay it is there" "neighbour: curl exit $rc (7 = refused); relay loopback: ${out%% *}" \
    '[[ $rc == 7 && $out == "pong $NONCE" ]]'

  local leaks users environ
  leaks="$(docker logs "$RELAY" 2>&1 | grep -c -F -e "${C_AUTH#*:}" -e "${W_AUTH#*:}" -e "$(val "$WORK/cred/main.env" RELAY_KEY)" -e "$(val "$WORK/cred/main.env" RELAY_ADMIN_TOKEN)")"
  users="$(docker logs "$RELAY" 2>&1 | grep -c 'office-pc')"
  environ="$(docker exec "$RELAY" sh -c "tr '\\0' '\\n' < /proc/1/environ | grep -c '^RELAY_'")"
  check X12 "No password or key in the relay's log; no client password in chisel's environment (the key is there, by design)" "$leaks secret lines among $(docker logs "$RELAY" 2>&1 | wc -l | tr -d ' ') ($users name the client); $environ RELAY_ variables" \
    '[[ $leaks == 0 && $users -ge 1 && $environ == 0 ]]'
  out="$(docker logs "$RELAY" 2>&1 | sed -n 's/.*server: Fingerprint //p' | head -n 1)"
  check X13 "gen-credentials.sh computes the fingerprint chisel prints" "$( [ "$out" = "$FP" ] && echo equal || echo "chisel $out, script $FP" )" '[[ -n $FP && $out == "$FP" ]]'
  out="$(docker inspect -f 'readonly={{.HostConfig.ReadonlyRootfs}} user={{.Config.User}} capdrop={{.HostConfig.CapDrop}} memory={{.HostConfig.Memory}} nanocpus={{.HostConfig.NanoCpus}} pids={{.HostConfig.PidsLimit}} published={{len .HostConfig.PortBindings}} mounts={{range .Mounts}}{{.Type}}:{{.Name}}:{{.Destination}}:rw={{.RW}} {{end}}' "$RELAY")"
  check X14 "compose.yaml's limits are applied by plain docker compose; the one mount is the state volume" "$out" \
    '[[ $out == "readonly=true user=65532:65532 capdrop=[ALL] memory=268435456 nanocpus=500000000 pids=256 published=0 mounts=volume:$VOLUME:/var/lib/mu-relay:rw=true " ]]'

  # chisel's own client never asks for a tunnel it did not declare, so X6-X8 show that the relay checks the
  # declaration. A client written to cheat declares what it may have and then asks for channels to other places.
  rawchan ws "$W_AUTH" 127.0.0.1:7001:127.0.0.1:7001 127.0.0.1:7001 dokploy-traefik:8080 127.0.0.1:8080 127.0.0.1:7002
  out="$(rawchan_line ws 127.0.0.1:7001)"
  check X15 "control: a client that is not chisel's logs in as the workstation and opens a channel to its compute port" "$out" \
    '[[ $out == "OPENED HTTP/1."*" 200 OK | pong $NONCE" ]]'
  check X16 "The same session then asks for channels to Traefik's API, the relay's own port and another compute port: each refused" \
    "$(rawchan_line ws dokploy-traefik:8080) / $(rawchan_line ws 127.0.0.1:8080) / $(rawchan_line ws 127.0.0.1:7002)" \
    'rawchan_denied ws dokploy-traefik:8080 && rawchan_denied ws 127.0.0.1:8080 && rawchan_denied ws 127.0.0.1:7002 && [[ $(rawchan_opened ws) == 1 ]]'
  rawchan compute "$C_AUTH" - 127.0.0.1:7001 dokploy-traefik:8080 127.0.0.1:8080
  check X17 "With the compute node's credentials and nothing declared: no channel at all, not even to its own port" \
    "$(rawchan_line compute 127.0.0.1:7001) / $(rawchan_line compute dokploy-traefik:8080) / $(rawchan_line compute 127.0.0.1:8080)" \
    'rawchan_denied compute 127.0.0.1:7001 && rawchan_denied compute dokploy-traefik:8080 && rawchan_denied compute 127.0.0.1:8080 && [[ $(rawchan_opened compute) == 0 ]]'
}

test_limit() {
  local limit n out before pid i
  limit="$(sed -n 's/^ *- traefik\.http\.middlewares\.mu-relay-inflight\.inflightreq\.amount=\([0-9]*\)$/\1/p' "$ROOT/relay/compose.yaml")"
  n=$(( ${limit:-0} + 8 ))
  say "Connection limit (compose.yaml's middleware, named in the Domains entry): $limit at a time from one address"
  # chisel keeps a connection that never logs in open for ever. The neighbour container opens $n of them, straight
  # to Traefik on dokploy-network, so Traefik sees them all come from one address that no other client has.
  out="$(flood "$HOST_SILENT" "$n" 1)"
  check L1 "control: $n connections that never log in, from one address, to the second relay (no middleware in its Domains entry): all accepted" "$out" \
    '[[ $out == "{\"101\": $n}" ]]'
  before="$(conns s4-compute) $(conns s4-ws)"
  flood "$HOST" "$n" 25 > "$WORK/out/flood-main.json" & pid=$!
  for i in $(seq 1 100); do [ -s "$WORK/out/flood-main.json" ] && break; sleep 0.3; done
  out="$(cat "$WORK/out/flood-main.json")"
  check L2 "The same to the relay: $limit are accepted, the other 8 get 429" "$out" '[[ -n $limit && $out == "{\"101\": $limit, \"429\": 8}" ]]'
  out="$(probe python3 /s4/ws_probe.py "$HOST" 443 /certs/ca.crt chisel-v3 2>&1 | sed -n 's/.*"status_line": "\([^"]*\)".*/\1/p')"
  check L3 "While those $limit are held open, a client at another address still connects, and the tunnels are untouched" \
    "new connection: $out; connects/disconnects: compute and workstation $(conns s4-compute) $(conns s4-ws)" \
    '[[ $out == "HTTP/1.1 101 Switching Protocols" && "$(conns s4-compute) $(conns s4-ws)" == "$before" && $(ping_via s4-ws) == "pong $NONCE" ]]'
  wait "$pid"
  out="$(flood "$HOST" 1 0)"
  check L4 "After they are closed the same address connects again" "$out" '[[ $out == "{\"101\": 1}" ]]'
}

test_idle() {
  say "Idle for $IDLE s (Traefik's readTimeout is 60 s and idleTimeout 180 s)"
  local left="$IDLE" step
  while [ "$left" -gt 0 ]; do step=$(( left < 60 ? left : 60 )); sleep "$step"; left=$((left - step)); echo "  ... $((IDLE - left)) s"; done
  local main silent ka cut cuts lat silent_idle p
  silent_idle="$(since "$T_SILENT_LAST")"
  main="$(conns s4-compute) $(conns s4-ws)"; silent="$(conns s4-compute-silent) $(conns s4-ws-silent)"
  ka="$(conns s4-ws-ka-cut)"; cut="$(nlog s4-ws-silent-cut 'client: Disconnected')"; cuts="$(nlog s4-killer 'CUT after')"
  lat="$(docker exec s4-ws curl -s -m 5 -o /dev/null -w '%{time_total}' http://127.0.0.1:7001/ping)"
  p="$(ping_via s4-ws)"
  check I1 "Keepalive 25 s: both tunnels are still the first connection after $IDLE s idle" "connects/disconnects: compute and workstation $main; first request after idle ${lat} s" \
    '[[ $main == "1/0 1/0" && $p == "pong $NONCE" ]]'
  p="$(ping_via s4-ws-silent)"
  check I2 "Keepalive off on relay and clients: the same after ${silent_idle%.*} s with no bytes at all" "connects/disconnects: compute and workstation $silent" \
    '[[ $silent == "1/0 1/0" && $p == "pong $(nonce s4-compute-silent)" ]]'
  check I3 "control: the silent tunnel through a hop that cuts after 45 s idle is cut, and the count shows it" "$cuts cuts by the hop, $cut disconnects logged by the client" \
    '[[ $cuts -ge 1 && $cut -ge 1 ]]'
  check I4 "Keepalive 25 s through the same hop: not cut" "connects/disconnects $ka" '[[ $ka == "1/0" ]]'

  say "Traefik configuration reloads (another service on the server starts and stops)"
  local i ok=1 code_cmd=(probe curl -s -m 3 -o /dev/null -w '%{http_code}' -H 'Host: noise.s4.test' "http://$HOST/ping")
  wait_code 200 20 "${code_cmd[@]}" || ok=0
  for i in 1 2 3; do
    docker stop -t 1 s4-noise >/dev/null; wait_code 404 20 "${code_cmd[@]}" || ok=0
    docker start s4-noise >/dev/null;     wait_code 200 20 "${code_cmd[@]}" || ok=0
  done
  main="$(conns s4-compute) $(conns s4-ws)"; p="$(ping_via s4-ws)"
  check I5 "Three stop/start cycles of a neighbour, each seen by Traefik (its route went 404 and came back): tunnels untouched" \
    "route changes seen: $( [ "$ok" = 1 ] && echo "6 of 6" || echo "not all" ); connects/disconnects $main" \
    '[[ $ok == 1 && $main == "1/0 1/0" && $p == "pong $NONCE" ]]'
}

test_reconnect() {
  say "Reconnects (clients run with --max-retry-interval 15s and CHISEL_WS_TIMEOUT=10s)"
  local t0 t p before after old rc twin refused

  # --force-recreate: Dokploy's own `up -d --build` leaves a container alone when neither the image nor the
  # environment changed. The row needs the relay to be a new container, and checks that it is.
  docker logs "$RELAY" > "$WORK/logs/relay-before-redeploy.log" 2>&1
  old="$RELAY"; before="$(nlog s4-ws 'client: Disconnected')"
  t0="$(now)"; dc_main up -d --force-recreate --remove-orphans >> "$WORK/logs/compose.log" 2>&1; rc=$?; RELAY="$(dc_main ps -q mu-relay)"
  wait_ping s4-ws "pong $NONCE" 90; t="$(since "$t0")"; p="$(ping_via s4-ws)"; after="$(nlog s4-ws 'client: Disconnected')"
  check R1 "Relay container recreated (a redeploy): a new container, the tunnel dropped and works again (limit 25 s)" \
    "$t s from the start of the redeploy; container $( [ -n "$RELAY" ] && [ "$RELAY" != "$old" ] && echo replaced || echo "not replaced (compose exit $rc)" ); workstation disconnects $before -> $after" \
    '[[ $rc == 0 && -n $RELAY && $RELAY != "$old" && $after -gt $before && $p == "pong $NONCE" ]] && lt "$t" 25'
  # The same redeploy seen by a workstation without CHISEL_WS_TIMEOUT: its first retry reaches Traefik while it
  # still routes to the removed container, and waits for Traefik's 30 s dial timeout. Not asserted (it is a race).
  wait_ping s4-ws-ka-cut "pong $NONCE" 90
  row INFO R1b "The same redeploy for a client with chisel's default 45 s handshake timeout" "$(since "$t0") s; $(docker logs s4-ws-ka-cut 2>&1 | grep -c 'bad handshake') failed handshake(s)"
  [ "$BREAK" != noretry ] || return 0

  dc_main stop >> "$WORK/logs/compose.log" 2>&1; sleep 20; p="$(ping_via s4-ws)"
  check R2 "control: with the relay stopped for 20 s the workstation's request fails, and the host answers 404" \
    "request: $( [ -z "$p" ] && echo failed || echo "'$p'" ); https://$HOST/health: $(probe curl -s -m 3 -o /dev/null -w '%{http_code}' --cacert /certs/ca.crt "https://$HOST/health")" '[[ -z $p ]]'
  t0="$(now)"; dc_main start >> "$WORK/logs/compose.log" 2>&1
  wait_ping s4-ws "pong $NONCE" 90; t="$(since "$t0")"; p="$(ping_via s4-ws)"
  check R3 "Relay started again: the tunnel works again (limit 45 s)" "$t s" '[[ $p == "pong $NONCE" ]] && lt "$t" 45'

  before="$(nlog s4-ws 'client: Disconnected')"
  t0="$(now)"; docker restart dokploy-traefik >/dev/null
  sleep 1; wait_ping s4-ws "pong $NONCE" 90; t="$(since "$t0")"; p="$(ping_via s4-ws)"; after="$(nlog s4-ws 'client: Disconnected')"
  check R4 "Traefik restarted: every tunnel drops (the disconnect count shows it) and works again (limit 60 s)" "$t s; workstation disconnects $before -> $after" \
    '[[ $p == "pong $NONCE" && $after -gt $before ]] && lt "$t" 60'

  old="$NONCE"; t0="$(now)"; docker restart -t 2 s4-compute >/dev/null
  wait_code changed 30 nonce_changed "$old"
  NONCE="$(nonce s4-compute)"
  wait_ping s4-ws "pong $NONCE" 90; t="$(since "$t0")"; p="$(ping_via s4-ws)"
  check R5 "Compute container restarted cleanly (its chisel client closes the session first): the workstation reaches the new process (limit 45 s)" "$t s" \
    '[[ -n $NONCE && $NONCE != "$old" && $p == "pong $NONCE" ]] && lt "$t" 45'

  # The link vanishes without a close (power cut, cable, NAT state lost): nobody is told, only the keepalive notices.
  docker network disconnect "$OFFICE_NET" s4-compute; sleep 70; p="$(ping_via s4-ws)"
  check R6 "control: with the compute container's network cut for 70 s the workstation's request fails" \
    "request: $( [ -z "$p" ] && echo failed || echo "'$p'" ); compute connects/disconnects $(conns s4-compute)" '[[ -z $p ]]'
  t0="$(now)"; docker network connect "$OFFICE_NET" s4-compute
  wait_ping s4-ws "pong $NONCE" 90; t="$(since "$t0")"; p="$(ping_via s4-ws)"
  check R7 "Network back: the compute node reconnects by itself and the tunnel works (limit 45 s)" "$t s" '[[ $p == "pong $NONCE" ]] && lt "$t" 45'

  # The compute end dies without a close and is back before the relay has noticed (a PC that loses power and boots
  # fast, a WSL VM that is killed and started again). The relay still holds the dead session and its port, for up
  # to 50 s (a 25 s ping interval and a 25 s reply timeout). Here: the first container is frozen, and a second one
  # with the same credentials starts at once.
  docker pause s4-compute >/dev/null; t0="$(now)"
  dc_clients up -d compute-twin >> "$WORK/logs/compose.log" 2>&1
  wait_log s4-compute-twin 'client: Connecting' 30; twin="$(nonce s4-compute-twin)"
  wait_ping s4-ws "pong $twin" 120; t="$(since "$t0")"; p="$(ping_via s4-ws)"; refused="$(nlog s4-compute-twin 'cannot listen')"
  check R8 "Compute end gone without a close and back at once: refused while the relay holds the dead session, then connected (limit 75 s)" \
    "$t s; refused $refused time(s) (\"Server cannot listen\") first" \
    '[[ -n $twin && $twin != "$NONCE" && $p == "pong $twin" && $refused -ge 1 ]] && lt "$t" 75'
  docker logs s4-compute-twin > "$WORK/logs/s4-compute-twin.log" 2>&1
  docker rm -f s4-compute-twin >/dev/null 2>&1
  t0="$(now)"; docker unpause s4-compute >/dev/null
  wait_ping s4-ws "pong $NONCE" 90; t="$(since "$t0")"; p="$(ping_via s4-ws)"
  check R9 "The first container thawed, the second gone: it finds its session dead and takes its port back (limit 45 s)" "$t s" \
    '[[ $p == "pong $NONCE" ]] && lt "$t" 45'
}

# --------------------------------------------------------------------------------------------- pairing service
# The admin's and the claimer's requests run in the probe container, an outside client like the Mac and the PC. The
# admin token goes in through the environment (docker exec -e NAME, the value not on the command line) and the test
# codes through stdin.
api() {  # api METHOD PATH [JSON]   as the admin; prints "<status> <body>"
  docker exec -e TOKEN -e M="$1" -e P="$2" -e D="${3:-}" s4-probe sh -c '
    if [ -n "$D" ]; then set -- -H "Content-Type: application/json" --data-binary "$D"; else set --; fi
    code=$(curl -s -m 40 -o /tmp/api.out -w "%{http_code}" -X "$M" -H "Authorization: Bearer $TOKEN" "$@" \
      --cacert /certs/ca.crt "https://relay.martand.systems$P"); echo "$code $(cat /tmp/api.out)"'
}
anon() {  # anon METHOD PATH [JSON] [EXTRA-HEADER]   without auth; prints "<status> <body>"
  docker exec -e M="$1" -e P="$2" -e D="${3:-}" -e H="${4:-X-S4: 1}" s4-probe sh -c '
    if [ -n "$D" ]; then set -- -H "Content-Type: application/json" --data-binary "$D"; else set --; fi
    code=$(curl -s -m 40 -o /tmp/anon.out -w "%{http_code}" -X "$M" -H "$H" "$@" --cacert /certs/ca.crt \
      "https://relay.martand.systems$P"); echo "$code $(cat /tmp/anon.out)"'
}
jget() { python3 -c 'import json,sys
d=json.loads(sys.stdin.read().split(" ",1)[1])
for k in sys.argv[1].split("."):
    d = d[int(k)] if isinstance(d, list) else d.get(k) if isinstance(d, dict) else None
print(json.dumps(d) if isinstance(d, (dict, list, bool)) or d is None else d)' "$1" 2>/dev/null; }
pref() {  # pref SUBCOMMAND ARGS...   the reference pairing client in the probe; stdin passed through
  docker exec -i -e PAIR_ADMIN_TOKEN -e MU_PAIR_FAULT="$PAIR_FAULT" -e PAIR_REF_TAMPER="${TAMPER:-}" s4-probe \
    python3 /s4/pair_ref.py "$@" --relay "https://$HOST" --ca /certs/ca.crt
}
pref_invite() {  # pref_invite NAME PORT STATE [TTL]   prints the code
  pref invite --fingerprint "$FP" --name "$1" --port "$2" --state "$3" --ttl "${4:-1800}" 2> "$WORK/out/pref-invite-$1.err"
}
HK="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEj8m4yS4hostkeyOfTheTestNodeOnlyForS4xyzABCD"
fwd_ping() {  # fwd_ping AUTH LOCAL REMOTE   a forward from the probe as AUTH, one GET /ping through it
  docker exec -e AUTH="$1" -e L="$2" -e R="$3" -e FP="$FP" s4-probe sh -c '
    chisel client --max-retry-count 0 --tls-ca /certs/ca.crt --fingerprint "$FP" https://relay.martand.systems \
      "127.0.0.1:$L:127.0.0.1:$R" > "/tmp/fwd-$L.log" 2>&1 & pid=$!
    out=""; for i in $(seq 1 40); do out=$(curl -s -m 2 "http://127.0.0.1:$L/ping") && [ -n "$out" ] && break; sleep 0.25; done
    kill $pid 2>/dev/null; wait $pid 2>/dev/null; echo "$out"'
}

test_pairing() {
  say "Pairing service: mu-relayd behind chisel's --backend, on the same route (docs/design/pairing.md)"
  TOKEN="$(val "$WORK/cred/main.env" RELAY_ADMIN_TOKEN)"; PAIR_ADMIN_TOKEN="$TOKEN"; export TOKEN PAIR_ADMIN_TOKEN
  local out a b c code t0 rc st before after started lab_nonce pw
  out="$(anon GET /mu/v1/)"
  a="$(probe curl -s -m 5 --cacert /certs/ca.crt "https://$HOST/health")"
  b="$(probe curl -s -m 5 -o /dev/null -w '%{http_code}' --cacert /certs/ca.crt "https://$HOST_SILENT/mu/v1/")"
  check P1 "https://$HOST/mu/v1/ answers through Traefik on the relay's one route; /health is still OK; the legacy relay has no /mu/v1/" \
    "$(echo "$out" | cut -c1-90); /health $a; legacy relay $b" \
    '[[ $out == "200 "*"\"pairing\":true"*"\"service\":\"mu-relay\""* && $a == OK && $b == 404 ]]'
  # an invite that is left to expire (its ttl is the minimum, 60 s); checked in P6
  local exp_code; exp_code="$(pref_invite exp-pc 7009 /tmp/inv-exp.json 60)"; local t_exp; t_exp="$(now)"

  a="$(anon GET /mu/v1/clients | cut -d' ' -f1)"
  b="$(TOKEN="${TOKEN:0:8}$(printf 'x%.0s' $(seq 1 $(( ${#TOKEN} - 8 ))))" api GET /mu/v1/clients | cut -d' ' -f1)"
  c="$(api GET /mu/v1/clients | cut -d' ' -f1)"
  check P2 "The admin API needs the token: none 401, a wrong one with the right first 8 characters 403, the right one 200" "$a $b $c" \
    '[[ $a == 401 && $b == 403 && $c == 200 ]]'

  before="$(conns s4-compute) $(conns s4-ws)"; started="$(started_at "$RELAY")"
  code="$(pref_invite lab-pc 7002 /tmp/inv-lab.json)"
  a="$(printf '%s\r\n' "$code" | pref claim --state /tmp/claim-lab.json --host-key "$HK" --password-file /tmp/lab.pw 2>&1)"
  b="$(pref wait --state /tmp/inv-lab.json --timeout 30 2>&1)"
  c="$(pref result --state /tmp/claim-lab.json --timeout 30 2>&1)"
  check P3 "Invite end to end through Traefik (the reference client, code typed with CR LF): created, claimed, answered, verified, accepted, finished, confirmed" \
    "claim: $(echo "$a" | cut -c1-60); inviter: $(echo "$b" | cut -c1-60); claimer: $(echo "$c" | cut -c1-60)" \
    '[[ ${#code} == 29 && $a == *"\"ok\": true"* && $b == *"\"state\": \"finished\""* && $b == *"$HK"* && $c == *"\"confirmed\": true"* ]]'

  pw="$(docker exec s4-probe cat /tmp/lab.pw 2>/dev/null)"
  printf 'RELAY_URL=https://%s\nRELAY_FINGERPRINT=%s\nRELAY_ROLE=compute\nRELAY_USER=lab-pc\nRELAY_PASSWORD=%s\nRELAY_PORTS=7002\n' "$HOST" "$FP" "$pw" > "$WORK/env/lab.env"
  docker rm -f s4-lab >/dev/null 2>&1
  t0="$(now)"
  docker run -d --name s4-lab --label mu.s4=1 --network "$OFFICE_NET" --add-host "$HOST:host-gateway" \
    -v "$WORK/certs/ca.crt:/certs/ca.crt:ro" --env-file "$WORK/env/lab.env" -e S4_BLOB_MB=1 s4-client compute >/dev/null
  wait_log s4-lab 'client: Connected' 20; t="$(since "$t0")"
  lab_nonce="$(nonce s4-lab)"
  a="$(fwd_ping "$W_AUTH" 7912 7002)"; b="$(ping_via s4-ws)"; after="$(conns s4-compute) $(conns s4-ws)"
  check P4 "The node's new dynamic client is accepted by chisel after the auth-file rewrite, with no restart; avi-mac reaches it on the granted port; the other tunnels untouched" \
    "lab-pc connected after $t s; avi-mac -> 7002: ${a:-nothing}; container restarted: $( [ "$(started_at "$RELAY")" = "$started" ] && echo no || echo yes ); chisel restarts: $(nlog "$RELAY" 'chisel restarted'); connects/disconnects $before -> $after" \
    '[[ -n $lab_nonce && $a == "pong $lab_nonce" && $b == "pong $NONCE" && $(started_at "$RELAY") == "$started" && $(nlog "$RELAY" "chisel restarted") == 0 && $after == "$before" ]] && lt "$t" 15'

  # The access rules of dynamic clients, with the client that is not chisel's (as X15-X17 for the static ones).
  api PUT /mu/v1/clients/dyn-ws '{"role":"workstation","password":"dyn-ws-password-0123456789abcdef","ports":[7002]}' > "$WORK/out/put-dyn-ws.txt"
  sleep 1
  rawchan dynws "dyn-ws:dyn-ws-password-0123456789abcdef" 127.0.0.1:7002:127.0.0.1:7002 127.0.0.1:7002 127.0.0.1:7001 dokploy-traefik:8080 127.0.0.1:8080 127.0.0.1:8081
  rawchan dynpc "lab-pc:$pw" - 127.0.0.1:7002 127.0.0.1:7001 dokploy-traefik:8080 127.0.0.1:8081
  adhoc labrev 20 "lab-pc:$pw" "$FP" R:127.0.0.1:7001:127.0.0.1:9000
  adhoc labany 20 "lab-pc:$pw" "$FP" R:0.0.0.0:7002:127.0.0.1:9000
  check P5 "Dynamic clients get exactly the static rules: the workstation opens its port only; the compute node no channel and no other listener" \
    "dyn-ws: $(rawchan_line dynws 127.0.0.1:7002 | cut -c1-30) / 7001 $(rawchan_line dynws 127.0.0.1:7001 | cut -c1-20) / traefik $(rawchan_line dynws dokploy-traefik:8080 | cut -c1-20); lab-pc: $(rawchan_opened dynpc) opened, R:7001 $(adhoc_last labrev | cut -c1-30)" \
    '[[ $(rawchan_line dynws 127.0.0.1:7002) == "OPENED HTTP/1."*" 200 OK | pong $lab_nonce" ]] && rawchan_denied dynws 127.0.0.1:7001 && rawchan_denied dynws dokploy-traefik:8080 && rawchan_denied dynws 127.0.0.1:8080 && rawchan_denied dynws 127.0.0.1:8081 && [[ $(rawchan_opened dynws) == 1 ]] && rawchan_denied dynpc 127.0.0.1:7002 && rawchan_denied dynpc 127.0.0.1:7001 && rawchan_denied dynpc 127.0.0.1:8081 && [[ $(rawchan_opened dynpc) == 0 ]] && ! adhoc_connected labrev && adhoc_says labrev denied && ! adhoc_connected labany && adhoc_says labany denied'

  # Codes: wrong and unknown look the same; used; expired; the per-address rate limit.
  local s1 s2 cl unk wrong used expd lim
  s1="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  cl="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  unk="$(anon POST /mu/v1/invites/ZZZZ/claim "{\"claim\":\"$cl\",\"session\":\"$s1\"}")"
  wrong="$(anon POST "/mu/v1/invites/$(echo "$code" | cut -c1-4)/claim" "{\"claim\":\"$cl\",\"session\":\"$s1\"}")"
  used="$(printf '%s\n' "$code" | pref claim --state /tmp/claim-again.json --host-key "$HK" 2>&1)"
  while ! ge "$(since "$t_exp")" 62; do sleep 1; done
  expd="$(printf '%s\n' "$exp_code" | pref claim --state /tmp/claim-exp.json --host-key "$HK" 2>&1)"
  lim="$(docker exec s4-noise sh -c 'for i in $(seq 1 31); do curl -s -m 5 -o /dev/null -w "%{http_code} " --connect-to relay.martand.systems:443:dokploy-traefik:443 --cacert /certs/ca.crt https://relay.martand.systems/mu/v1/; done')"
  check P6 "Codes: an unknown id and a wrong code get the same answer; a used code is refused; an expired one too; the 31st request in a minute from one address gets 429" \
    "unknown: $(echo "$unk" | cut -c1-40); wrong: $( [ "$unk" = "$wrong" ] && echo same || echo "$(echo "$wrong" | cut -c1-40)" ); used: $(echo "$used" | cut -c1-50); expired: $(echo "$expd" | cut -c1-50); limit: $(echo "$lim" | tr ' ' '\n' | sort | uniq -c | tr -s ' \n' ' ')" \
    '[[ $unk == "403 "*"wrong-code"* && $unk == "$wrong" && $used == *"invite-used"* && $expd == *"invite-expired"* && $lim == "$(printf "200 %.0s" $(seq 1 30))429 " ]]'

  # Integrity: a key changed on the way is refused by the side that checks it; a second claimer is refused and contests.
  local c1 c2 c3 r1 r2 r3 r4 clients
  c1="$(pref_invite tamp-pc 7003 /tmp/inv-tamp.json)"
  r1="$(printf '%s\r\n' "$c1" | TAMPER=offer pref claim --state /tmp/claim-tamp.json --host-key "$HK" 2>&1)"
  c2="$(pref_invite tama-pc 7004 /tmp/inv-tama.json)"
  printf '%s\r\n' "$c2" | pref claim --state /tmp/claim-tama.json --host-key "$HK" > /dev/null 2>&1
  r2="$(TAMPER=answer pref wait --state /tmp/inv-tama.json --timeout 30 2>&1)"
  c3="$(pref_invite con-pc 7005 /tmp/inv-con.json)"
  printf '%s\r\n' "$c3" | pref claim --state /tmp/claim-con.json --host-key "$HK" > /dev/null 2>&1
  r3="$(printf '%s\r\n' "$c3" | pref contest --state /tmp/contest.json 2>&1)"
  r4="$(pref wait --state /tmp/inv-con.json --timeout 30 2>&1)"
  clients="$(api GET /mu/v1/clients | jget clients | python3 -c 'import json,sys; print(" ".join(sorted(c["name"] for c in json.load(sys.stdin))))')"
  check P7 "A changed offer is refused by the claimer, a changed answer by the inviter (both abort, no client stays); a second claimer is refused, contests, and the inviter refuses to finish" \
    "offer: $(echo "$r1" | cut -c1-40); answer: $(echo "$r2" | cut -c1-40); contest: $(echo "$r3" | cut -c1-30); inviter: $(echo "$r4" | cut -c1-40); clients: $clients" \
    '[[ $r1 == *"pair-integrity"* && $r2 == *"pair-integrity"* && $r3 == *"\"contested\": true"* && $r4 == *"contested"* && " $clients " != *" tamp-pc "* && " $clients " != *" tama-pc "* && " $clients " != *" con-pc "* && " $clients " == *" lab-pc "* ]]'

  # Removing a client: its established session is cut (chisel restarted), it is refused afterwards, the others return.
  # P7's removals restarted chisel; at most one restart per 10 s, so let that window pass first.
  sleep 11; wait_ping s4-ws "pong $NONCE" 30
  before="$(nlog s4-lab 'client: Disconnected')"
  t0="$(now)"; out="$(api DELETE /mu/v1/clients/lab-pc)"
  wait_log s4-lab 'client: Disconnected' 20; t="$(since "$t0")"
  wait_log s4-lab 'uthentication failed' 20
  a="$(fwd_ping "$W_AUTH" 7913 7002)"
  wait_ping s4-ws "pong $NONCE" 30; b="$(ping_via s4-ws)"
  c="$(api GET /mu/v1/clients/avi-mac | jget client.ports)"
  check P8 "Removing the node's client cuts its tunnel at once and refuses it afterwards; avi-mac loses the port; the static tunnels come back" \
    "$(echo "$out" | cut -c1-40); lab-pc disconnected after $t s ($(nlog s4-lab 'client: Disconnected') time(s)), then refused $(nlog s4-lab 'uthentication failed') time(s); avi-mac -> 7002: ${a:-nothing}; ports $c; static: ${b:-nothing}" \
    '[[ $out == "200 "* && $(nlog s4-lab "client: Disconnected") -gt $before && $(nlog s4-lab "uthentication failed") -ge 1 && -z $a && $b == "pong $NONCE" && $c == "["* && $c != *7002* ]] && lt "$t" 15'
  docker logs s4-lab > "$WORK/logs/s4-lab.log" 2>&1; docker rm -f s4-lab >/dev/null 2>&1

  say "Pairing state on the named volume"
  local sid old half keep
  api PUT /mu/v1/clients/keep-pc '{"role":"compute","password":"keep-pc-password-0123456789abcdef","ports":[7006]}' > /dev/null
  half="$(pref_invite half-pc 7007 /tmp/inv-half.json)"
  sid="$(api GET /mu/v1/clients | jget store_id)"
  old="$RELAY"
  docker logs "$RELAY" > "$WORK/logs/relay-pairing.log" 2>&1  # P1-P8 as mu-relayd saw them (the redeploys below replace it)
  dc_main up -d --force-recreate --remove-orphans >> "$WORK/logs/compose.log" 2>&1; RELAY="$(dc_main ps -q mu-relay)"
  wait_code OK 60 probe curl -s -m 3 --cacert /certs/ca.crt "https://$HOST/health"
  a="$(api GET /mu/v1/clients | jget store_id)"
  b="$(api GET "/mu/v1/invites/$(echo "$half" | cut -c1-4)" | jget invite.state)"
  adhoc keep 8 "keep-pc:keep-pc-password-0123456789abcdef" "$FP" R:127.0.0.1:7006:127.0.0.1:9000
  check P9 "A redeploy (container recreated) keeps the store: the same store id, the dynamic client accepted, a half-done invite still open" \
    "container $( [ "$RELAY" != "$old" ] && echo replaced || echo "not replaced" ); store $sid -> $a; invite: $b; keep-pc: $(adhoc_last keep)" \
    '[[ -n $sid && $RELAY != "$old" && $a == "$sid" && $b == created ]] && adhoc_connected keep'

  dc_main down >> "$WORK/logs/compose.log" 2>&1
  docker volume rm "$VOLUME" >> "$WORK/logs/compose.log" 2>&1
  dc_main up -d --remove-orphans >> "$WORK/logs/compose.log" 2>&1; RELAY="$(dc_main ps -q mu-relay)"
  wait_code OK 60 probe curl -s -m 3 --cacert /certs/ca.crt "https://$HOST/health"
  a="$(api GET /mu/v1/clients | jget store_id)"
  adhoc keep2 15 "keep-pc:keep-pc-password-0123456789abcdef" "$FP" R:127.0.0.1:7006:127.0.0.1:9000
  adhoc static2 8 "$W_AUTH" "$FP" 127.0.0.1:7914:127.0.0.1:7001
  check P10 "A lost volume: a new store, the dynamic client refused, the static clients unaffected" \
    "store $sid -> $a; keep-pc: $(adhoc_last keep2); avi-mac: $(adhoc_last static2)" \
    '[[ -n $a && $a != null && $a != "$sid" ]] && ! adhoc_connected keep2 && adhoc_says keep2 "authenticat" && adhoc_connected static2'

  # The auth file never empties: chisel 1.12.0 lets anyone in when it has no users (server.go authUser).
  a="$(api DELETE /mu/v1/clients/office-pc | cut -d' ' -f1)"
  b="$(api DELETE /mu/v1/clients/avi-mac)"
  sleep 1
  adhoc nobody 15 "nobody:not-a-password-000000000000000000000" "$FP" 127.0.0.1:7915:127.0.0.1:8081
  c="$(docker exec "$RELAY" sh -c 'grep -c "^  \"_mu-sentinel:" /run/relay/users.json')"
  check P11 "Removing every client is refused at the last one; the auth file keeps its sentinel user; a client with no valid password is refused" \
    "office-pc: $a; avi-mac: $(echo "$b" | cut -c1-50); sentinel lines $c; anyone: $(adhoc_last nobody)" \
    '[[ $a == 200 && $b == "409 "*"last-client"* && $c == 1 ]] && ! adhoc_connected nobody && adhoc_says nobody "authenticat"'
  api DELETE /mu/v1/tombstones/office-pc > /dev/null

  # A volume the relay cannot write: the static clients keep working, pairing answers 503.
  dc_main -f tools/s4/rootvolume.yaml up -d --force-recreate --remove-orphans >> "$WORK/logs/compose.log" 2>&1; RELAY="$(dc_main ps -q mu-relay)"
  wait_code OK 60 probe curl -s -m 3 --cacert /certs/ca.crt "https://$HOST/health"
  a="$(anon GET /mu/v1/)"; b="$(api GET /mu/v1/clients | cut -c1-40)"
  adhoc static3 8 "$W_AUTH" "$FP" 127.0.0.1:7916:127.0.0.1:7001
  check P12 "A state directory the relay cannot write: /mu/v1/ says so, the admin API answers 503, the static clients still connect" \
    "$(echo "$a" | cut -c1-70); $b; avi-mac: $(adhoc_last static3)" \
    '[[ $a == "200 "*"\"pairing\":false"*"\"store\":\"unwritable\""* && $b == "503 "*"store-unwritable"* ]] && adhoc_connected static3'
  dc_main up -d --force-recreate --remove-orphans >> "$WORK/logs/compose.log" 2>&1; RELAY="$(dc_main ps -q mu-relay)"
  wait_code OK 60 probe curl -s -m 3 --cacert /certs/ca.crt "https://$HOST/health"

  a="$(docker exec "$RELAY_SILENT" sh -c 'tr "\0" " " < /proc/1/cmdline' 2>/dev/null)"
  b="$(docker exec "$RELAY_SILENT" sh -c 'grep -c sentinel /run/relay/users.json; [ -e /run/relay/static.tsv ] && echo tsv || echo no-tsv' 2>/dev/null | tr '\n' ' ')"
  c="$(docker exec "$RELAY" sh -c 'tr "\0" " " < /proc/1/cmdline' 2>/dev/null)"
  check P13 "Legacy mode (no RELAY_ADMIN_TOKEN) is today's relay: chisel is the main process with no --backend, its auth file has no sentinel; pairing mode runs mu-relayd" \
    "legacy: $(echo "$a" | cut -c1-90); $b; pairing: $(echo "$c" | cut -c1-50)" \
    '[[ $a == "/usr/local/bin/chisel server --host 0.0.0.0 --port 8080 --reverse --authfile /run/relay/users.json --keepalive 0s "* && $a != *backend* && $b == "0 no-tsv " && $c == "/usr/local/bin/mu-relayd --config /run/relay/relayd.conf "* ]]'
}

# -------------------------------------------------------------------------------------------------------- main
case "${1:-run}" in
  down) down; exit 0 ;;
  relay) relay_only; exit 0 ;;
  run) ;;
  *) sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
T_RUN="$(now)"
down
guard
setup
test_routing
test_tunnels
test_access
[ "$BREAK" != acl ] || finish
if [ "$ONLY" != pairing ]; then
  test_limit
  test_idle
  [ "$BREAK" != idle ] || finish
  test_reconnect
  [ "$BREAK" != noretry ] || finish
fi
test_pairing
# Traefik logs at ERROR, as Dokploy leaves it. A failed ACME order, or a router or middleware it cannot build, is
# logged there. A 502 or 504 answered for the relay is not (Traefik logs those at DEBUG): R1b is where one shows.
n="$(docker logs dokploy-traefik 2>&1 | grep -c .)"
check Z1 "Traefik logged no line at its ERROR level in the whole run: no ACME attempt, no router or middleware it could not build" "$n lines" '[[ $n == 0 ]]'
row INFO Z2 "Whole run" "$(since "$T_RUN") s"
finish
