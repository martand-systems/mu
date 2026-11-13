#!/usr/bin/env bash
# Spike S4 (docs/PLAN.md §16): does chisel work through Dokploy's Traefik?
#
# Builds a replica of the server on this machine's Docker and tests the relay in it: a reverse tunnel from one
# client, a forward from another, a long idle period, and reconnects. Every result that matters has a negative
# control in the same run: the same check is made once more with the thing it tests broken, and has to notice.
#
#   tools/s4/run.sh         build, test, print the table; exit 0 only if every row passes
#   tools/s4/run.sh down    remove everything it made
#
# The replica (see the files next to this one):
#   dokploy-traefik   started with Dokploy's own `docker run` line and Dokploy's static config, on a swarm overlay
#                     network called dokploy-network, with the Traefik version in TRAEFIK_VERSION
#   the relay         relay/compose.yaml as it is in git, started with Dokploy's own `docker compose` line, plus
#                     the labels and network Dokploy adds from its Domains screen (dokploy-inject.yaml.tmpl) and
#                     an env file written the way Dokploy writes relay/.env
#   a second relay    the same, with every keepalive off and the slow health check (two negative controls)
#   clients           containers outside dokploy-network that reach the relay only through the published 443
#
# relay.martand.systems resolves to this machine inside the client containers only. Nothing here contacts the
# real server, and the script refuses to run on a machine that looks like a Dokploy host.
#
# Settings: TRAEFIK_VERSION (3.6.25), S4_IDLE seconds (240), S4_BLOB_MB (100), S4_WORK (.scratch/s4),
# S4_BREAK=port (deliberately wrong container port: the run must fail; used by CI to test this script).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
S4="$ROOT/tools/s4"
WORK="${S4_WORK:-$ROOT/.scratch/s4}"
TRAEFIK_VERSION="${TRAEFIK_VERSION:-3.6.25}"
IDLE="${S4_IDLE:-240}"
MB="${S4_BLOB_MB:-100}"
BREAK="${S4_BREAK:-}"
HOST=relay.martand.systems
HOST_SILENT=relay-silent.s4.test
APP=mu-relay-ci0001;        ROUTER="$APP-1"          # <appName> and <appName>-<uniqueConfigKey>, as Dokploy names them
APP_SILENT=mu-relay-ci0002; ROUTER_SILENT="$APP_SILENT-2"
OFFICE_NET=s4-clients_office
export S4_WORK="$WORK" S4_BLOB_MB="$MB"
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
                  -f relay/compose.yaml -f "$WORK/inject-main.yaml" "$@" ); }
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

collect() {
  mkdir -p "$WORK/logs"
  local c
  for c in dokploy-traefik s4-compute s4-ws s4-compute-silent s4-ws-silent s4-killer s4-ws-silent-cut s4-ws-ka-cut s4-noise; do
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
  ! ours dokploy-traefik || docker rm -f dokploy-traefik >/dev/null 2>&1
  ! net_ours dokploy-network || docker network rm dokploy-network >/dev/null 2>&1
  [ ! -e "$WORK/swarm-by-s4" ] || docker swarm leave --force >/dev/null 2>&1
  rm -f "$ROOT/relay/.env"
  rm -rf "$WORK" 2>/dev/null || true      # Traefik's acme.json in there belongs to root
}

dokploy_env() {  # dokploy_env APPNAME CREDENTIAL_FILE [KEY=VALUE...]   the env file as Dokploy writes it
  local app="$1" cred="$2" kv; shift 2
  printf 'APP_NAME="%s"\nCOMPOSE_PROJECT_NAME="%s"\n' "$app" "$app"
  sed -n 's/^\(RELAY_[A-Z]*\)=\(.*\)$/\1="\2"/p' "$cred"
  for kv in "$@"; do printf '%s="%s"\n' "${kv%%=*}" "${kv#*=}"; done
  # Dokploy appends DOCKER_CONFIG=/root/.docker (it deploys as root), and docker compose obeys it. The replica
  # is not root, so the line points at this user's Docker configuration instead.
  printf 'DOCKER_CONFIG="%s"\n' "${DOCKER_CONFIG:-$HOME/.docker}"
}
inject() {  # inject HOST ROUTER PORT   Dokploy's labels and networks for that Domains entry
  sed -e "s/@HOST@/$1/g" -e "s/@ROUTER@/$2/g" -e "s/@PORT@/$3/g" "$S4/dokploy-inject.yaml.tmpl"
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
  "$g" --env "$WORK/cred/silent.env" --url "https://$HOST_SILENT" init >/dev/null 2>&1 || die "gen-credentials init"
  "$g" --env "$WORK/cred/silent.env" --url "https://$HOST_SILENT" add-client compute office-pc 7001 > "$WORK/env/compute-silent.env" 2>/dev/null || die "add-client"
  "$g" --env "$WORK/cred/silent.env" --url "https://$HOST_SILENT" add-client workstation avi-mac 7001 > "$WORK/env/workstation-silent.env" 2>/dev/null || die "add-client"
  dokploy_env "$APP" "$WORK/cred/main.env" > "$ROOT/relay/.env"
  dokploy_env "$APP_SILENT" "$WORK/cred/silent.env" RELAY_KEEPALIVE=0s > "$WORK/relay-silent.env"
  local port=8080; [ "$BREAK" != port ] || port=8081
  inject "$HOST" "$ROUTER" "$port" > "$WORK/inject-main.yaml"
  inject "$HOST_SILENT" "$ROUTER_SILENT" 8080 > "$WORK/inject-silent.yaml"

  # As Dokploy's server set-up does: a one-node swarm and an attachable overlay network.
  if [ "$(docker info -f '{{.Swarm.LocalNodeState}}' 2>/dev/null)" != active ]; then
    docker swarm init --advertise-addr 127.0.0.1 >/dev/null || die "docker swarm init"
    touch "$WORK/swarm-by-s4"
  fi
  docker network create --driver overlay --attachable --label mu.s4=1 dokploy-network >/dev/null || die "network create"

  dc_main build || die "the relay image did not build"
  docker build -q --build-arg RELAY_IMAGE="$APP-mu-relay" -t s4-client "$S4/client" >/dev/null || die "the client image did not build"

  # Dokploy's createTraefikInstance(), with the config directory under $WORK instead of /etc/dokploy/traefik.
  docker run -d --name dokploy-traefik --restart always --label mu.s4=1 \
    -v "$WORK/traefik/traefik.yml:/etc/traefik/traefik.yml" \
    -v "$WORK/traefik/dynamic:/etc/dokploy/traefik/dynamic" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -p 443:443 -p 80:80 -p 443:443/udp "traefik:v$TRAEFIK_VERSION" >/dev/null || die "Traefik did not start"
  docker network connect dokploy-network dokploy-traefik || die "network connect"

  dc_clients up -d probe noise killer >> "$WORK/logs/compose.log" 2>&1 || die "the probe containers did not start"
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
    check A6 "control: without start_interval the relay is not routed for about 30 s" "$t s; before that: $(json "$WORK/out/route-silent.json" before)" 'ge "$t" 25'
  else
    abort A6 "control: without start_interval the relay is not routed for about 30 s" "never in 90 s; answers: $(json "$WORK/out/route-silent.json" before)"
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
  row INFO D5 "Relay container after the transfers (limits: 256 MiB, 0.5 CPU)" "$(docker stats --no-stream --format 'memory {{.MemUsage}}, {{.PIDs}} processes' "$RELAY" 2>/dev/null)"
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
  leaks="$(docker logs "$RELAY" 2>&1 | grep -c -F -e "${C_AUTH#*:}" -e "${W_AUTH#*:}" -e "$(val "$WORK/cred/main.env" RELAY_KEY)")"
  users="$(docker logs "$RELAY" 2>&1 | grep -c 'office-pc')"
  environ="$(docker exec "$RELAY" sh -c "tr '\\0' '\\n' < /proc/1/environ | grep -c '^RELAY_'")"
  check X12 "No password or key in the relay's log; chisel's environment has no RELAY_ variable" "$leaks secret lines among $(docker logs "$RELAY" 2>&1 | wc -l | tr -d ' ') ($users name the client); $environ variables" \
    '[[ $leaks == 0 && $users -ge 1 && $environ == 0 ]]'
  out="$(docker logs "$RELAY" 2>&1 | sed -n 's/.*server: Fingerprint //p' | head -n 1)"
  check X13 "gen-credentials.sh computes the fingerprint chisel prints" "$( [ "$out" = "$FP" ] && echo equal || echo "chisel $out, script $FP" )" '[[ -n $FP && $out == "$FP" ]]'
  out="$(docker inspect -f 'readonly={{.HostConfig.ReadonlyRootfs}} user={{.Config.User}} capdrop={{.HostConfig.CapDrop}} memory={{.HostConfig.Memory}} nanocpus={{.HostConfig.NanoCpus}} pids={{.HostConfig.PidsLimit}} published={{len .HostConfig.PortBindings}} mounts={{len .Mounts}}' "$RELAY")"
  check X14 "compose.yaml's limits are applied by plain docker compose" "$out" \
    '[[ $out == "readonly=true user=65532:65532 capdrop=[ALL] memory=268435456 nanocpus=500000000 pids=256 published=0 mounts=0" ]]'
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
  local t0 t p before after old

  t0="$(now)"; dc_main up -d --force-recreate --remove-orphans >> "$WORK/logs/compose.log" 2>&1; RELAY="$(dc_main ps -q mu-relay)"
  wait_ping s4-ws "pong $NONCE" 90; t="$(since "$t0")"; p="$(ping_via s4-ws)"
  check R1 "Relay container recreated (a redeploy): the tunnel works again (limit 25 s)" "$t s from the start of the redeploy; workstation connects/disconnects $(conns s4-ws)" \
    '[[ $p == "pong $NONCE" ]] && lt "$t" 25'
  # The same redeploy seen by a workstation without CHISEL_WS_TIMEOUT: its first retry reaches Traefik while it
  # still routes to the removed container, and waits for Traefik's 30 s dial timeout. Not asserted (it is a race).
  wait_ping s4-ws-ka-cut "pong $NONCE" 90
  row INFO R1b "The same redeploy for a client with chisel's default 45 s handshake timeout" "$(since "$t0") s; $(docker logs s4-ws-ka-cut 2>&1 | grep -c 'bad handshake') failed handshake(s)"

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
  check R5 "Compute container restarted (the PC rebooting): the workstation reaches the new process (limit 45 s)" "$t s" \
    '[[ -n $NONCE && $NONCE != "$old" && $p == "pong $NONCE" ]] && lt "$t" 45'

  # The link vanishes without a close (power cut, cable, NAT state lost): nobody is told, only the keepalive notices.
  docker network disconnect "$OFFICE_NET" s4-compute; sleep 70; p="$(ping_via s4-ws)"
  check R6 "control: with the compute container's network cut for 70 s the workstation's request fails" \
    "request: $( [ -z "$p" ] && echo failed || echo "'$p'" ); compute connects/disconnects $(conns s4-compute)" '[[ -z $p ]]'
  t0="$(now)"; docker network connect "$OFFICE_NET" s4-compute
  wait_ping s4-ws "pong $NONCE" 90; t="$(since "$t0")"; p="$(ping_via s4-ws)"
  check R7 "Network back: the compute node reconnects by itself and the tunnel works (limit 45 s)" "$t s" '[[ $p == "pong $NONCE" ]] && lt "$t" 45'
}

# -------------------------------------------------------------------------------------------------------- main
case "${1:-run}" in
  down) down; exit 0 ;;
  run) ;;
  *) sed -n '2,24p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
T_RUN="$(now)"
down
guard
setup
test_routing
test_tunnels
test_access
test_idle
test_reconnect
n="$(docker logs dokploy-traefik 2>&1 | grep -c .)"
check Z1 "Traefik logged nothing in the whole run (its default level, ERROR, as Dokploy leaves it): no ACME attempt, no proxy error" "$n lines" '[[ $n == 0 ]]'
row INFO Z2 "Whole run" "$(since "$T_RUN") s"
finish
