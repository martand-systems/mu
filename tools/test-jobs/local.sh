#!/bin/bash
# The job layer end to end on one machine, without root and without systemd (docs/design/job-layer.md §16, unit
# level: "a user-level sshd and a local chisel server as in the experiments"). As the current user, everything on
# 127.0.0.1: a chisel server with the relay's rule shapes, a chisel client holding the node's reverse port, a
# user-level sshd with the real gate as its ForceCommand, and stubs for systemd-run and systemctl that run each job
# unit as a plain background process (no memory or time limits: those are tested on a real systemd by e2e.sh).
# The workstation side is the real `mu compute add / status / submit / status / log / fetch / cancel`, run through
# the dispatcher, with the relay password in a file (the Keychain is never touched).
#
#   CHISEL=/path/to/chisel /bin/bash tools/test-jobs/local.sh      (KEEP=1 keeps the temp folder)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/../.." && pwd)"
CHISEL="${CHISEL:-$(command -v chisel || true)}"
SSHD="${SSHD:-/usr/sbin/sshd}"
[ -x "$CHISEL" ] || { echo "skip: no chisel binary (CHISEL=...)"; exit 0; }
[ -x "$SSHD" ] || { echo "skip: no sshd at $SSHD"; exit 0; }
PY="${PY:-$(command -v python3)}"
ROOT="$(mktemp -d "${TMPDIR:-/tmp}/mu jobs local.XXXXXX")"
PIDS=""
cleanup() {
  for p in $PIDS; do kill "$p" 2>/dev/null; done
  pkill -f "$ROOT" 2>/dev/null
  if [ "${KEEP:-0}" = 1 ]; then echo "kept: $ROOT"; else chmod -R u+w "${ROOT:?}" 2>/dev/null; rm -rf "${ROOT:?}"; fi
}
trap cleanup EXIT
PASS=0; FAILN=0; FAILED=""
ok()   { PASS=$((PASS + 1)); printf '  ok    %s\n' "$1"; }
bad()  { FAILN=$((FAILN + 1)); FAILED="$FAILED
  - $1"; printf '  FAIL  %s\n' "$1"; [ -z "${2:-}" ] || printf '%s\n' "$2" | sed 's/^/        | /' | head -30; }
eq()   { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected: [$3]
got:      [$2]"; fi; }
has()  { case "$2" in *"$3"*) ok "$1" ;; *) bad "$1" "expected to contain: [$3]
in: $2" ;; esac; }
section() { printf '\n%s\n' "$1"; }
jget() { "$PY" -c 'import json,sys
d=json.loads(sys.stdin.read() or "{}")
for k in sys.argv[1].split("."):
    d = d.get(k) if isinstance(d, dict) else None
print("" if d is None else (json.dumps(d) if isinstance(d, (dict, list, bool)) else d))' "$1"; }
wait_port() { local i; for i in $(seq 1 100); do nc -z 127.0.0.1 "$1" 2>/dev/null && return 0; sleep 0.1; done; return 1; }

export HOME_REAL="$HOME" NO_COLOR=1 GIT_CONFIG_NOSYSTEM=1
unset MU_VERSION MU_PROJECT MU_JOBS_FAULT MU_NODE_FAULT SSH_AUTH_SOCK 2>/dev/null || true
B="$((7100 + RANDOM % 800))"; RELAY_PORT=$((18000 + RANDOM % 1000)); SSHD_PORT=$((19000 + RANDOM % 1000))
STUBS="$ROOT/stubs"; SD="$ROOT/systemd"; mkdir -p "$STUBS" "$SD/units" "$ROOT/srv" "$ROOT/log"
chmod 700 "$ROOT/srv"
NODE_HOME="$ROOT/node mu"; WS_HOME="$ROOT/ws mu"

stub_version() {   # stub_version MU_HOME V
  local T="$1/toolchains/$2"
  mkdir -p "$T/recipe/bin" "$T/env/bin" "$T/libexec" "$T/recipe/src"
  printf '{"version": "%s", "recipe_commit": "0000000000", "installed": "2026-11-14 10:00:00", "layers": []}\n' "$2" > "$T/MANIFEST.json"
  printf '#!/bin/bash\ncase "$1" in run) shift; exec "$@" ;; *) echo "stub toolchain: $*" ;; esac\n' > "$T/recipe/bin/mu-toolchain"
  chmod +x "$T/recipe/bin/mu-toolchain"
  ln -sf "$PY" "$T/env/bin/python"; cp -R "$REPO/src/mu" "$T/recipe/src/mu"; ln -sf "$CHISEL" "$T/libexec/chisel"
}
for h in "$NODE_HOME" "$WS_HOME"; do
  mkdir -p "$h/bin"; install -m 755 "$REPO/bin/mu" "$h/bin/mu"; stub_version "$h" v2026.11.7; echo v2026.11.7 > "$h/default"
done
stub_version "$NODE_HOME" v2026.11.6

# ---- systemd stubs: a unit is a background process; its state lives in $SD/units/<unit>/
cat > "$STUBS/systemd-run" <<'S'
#!/bin/bash
unit=""; wd="."; out=/dev/null; envs=()
while [ $# -gt 0 ]; do
  case "$1" in
    --unit=*) unit="${1#--unit=}" ;; --working-directory=*) wd="${1#--working-directory=}" ;;
    --setenv=*) envs+=("${1#--setenv=}") ;;
    -p) shift; case "$1" in StandardOutput=append:*) out="${1#StandardOutput=append:}" ;; esac ;;
    --) shift; break ;;
  esac; shift
done
u="$SD/units/$unit"
[ ! -e "$u/pid" ] || ! kill -0 "$(cat "$u/pid")" 2>/dev/null || { echo "Unit $unit already exists." >&2; exit 1; }
rm -rf "$u"; mkdir -p "$u"
( cd "$wd" || exit 1; env "${envs[@]}" "$@" >> "$out" 2>&1 < /dev/null; echo $? > "$u/rc" ) > /dev/null 2>&1 &
echo $! > "$u/pid"
S
cat > "$STUBS/systemctl" <<'S'
#!/bin/bash
[ "$1" = --user ] && shift
cmd="$1"; shift
case "$cmd" in
  is-active) echo active ;;
  show) u="$SD/units/$1"
        if [ ! -e "$u/pid" ]; then printf 'LoadState=not-found\nActiveState=inactive\nSubState=dead\nResult=success\n'
        elif kill -0 "$(cat "$u/pid")" 2>/dev/null && [ ! -e "$u/rc" ]; then printf 'LoadState=loaded\nActiveState=active\nSubState=running\nResult=success\n'
        else rc="$(cat "$u/rc" 2>/dev/null || echo 143)"
             if [ -e "$u/stopped" ] || [ "$rc" = 0 ]; then r=success; else r=exit-code; fi
             [ "$r" = success ] && s=inactive || s=failed
             printf 'LoadState=loaded\nActiveState=%s\nSubState=dead\nResult=%s\n' "$s" "$r"; fi ;;
  stop) u="$SD/units/$1"; [ -e "$u/pid" ] || exit 0; touch "$u/stopped"
        p="$(cat "$u/pid")"; pkill -TERM -P "$p" 2>/dev/null; kill -TERM "$p" 2>/dev/null
        for i in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$p" 2>/dev/null || break; sleep 0.2; done ;;
  reset-failed) u="$SD/units/${1:-none}"; [ -e "$u/pid" ] && ! kill -0 "$(cat "$u/pid")" 2>/dev/null && rm -rf "$u"; exit 0 ;;
  *) exit 0 ;;
esac
S
chmod +x "$STUBS"/*
export SD

# ---- the node: gate wrapper, sshd, keys
cat > "$ROOT/node-mu" <<S
#!/bin/sh
# what the gate execs: the node's mu with this test's settings (sshd gives a session no environment of ours)
export MU_HOME="$NODE_HOME" MU_NODE_ROOT="$ROOT/srv" MU_SYSTEMCTL="$STUBS/systemctl" MU_SYSTEMD_RUN="$STUBS/systemd-run"
export SD="$SD" MU_NODE_FAULT_FILE="$ROOT/fault" PATH="$PATH"
exec /bin/bash "$NODE_HOME/bin/mu" "\$@"
S
chmod +x "$ROOT/node-mu"
sed -e "s|@MU_BIN@|$ROOT/node-mu|" -e "s|@MU_KEYS@|$ROOT/authorized_keys|" "$REPO/install/hosts/wsl2/mu-node-gate" > "$ROOT/gate"
chmod 755 "$ROOT/gate"
ssh-keygen -q -t ed25519 -N '' -C node -f "$ROOT/hostkey"
: > "$ROOT/authorized_keys"
cat > "$ROOT/sshd_config" <<C
Port $SSHD_PORT
AddressFamily inet
ListenAddress 127.0.0.1
HostKey "$ROOT/hostkey"
PidFile "$ROOT/sshd.pid"
AllowUsers $(id -un)
AuthenticationMethods publickey
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM no
StrictModes no
AuthorizedKeysFile "$ROOT/authorized_keys"
ExposeAuthInfo yes
ForceCommand "$ROOT/gate"
PermitTTY no
AllowTcpForwarding no
AllowAgentForwarding no
X11Forwarding no
PermitUserRC no
MaxSessions 10
LogLevel VERBOSE
C
"$SSHD" -t -f "$ROOT/sshd_config" || { echo "FAIL: sshd -t"; exit 1; }
"$SSHD" -D -e -f "$ROOT/sshd_config" > "$ROOT/log/sshd.log" 2>&1 & PIDS="$PIDS $!"
wait_port "$SSHD_PORT" || { echo "FAIL: sshd did not start"; cat "$ROOT/log/sshd.log"; exit 1; }

# ---- the relay (chisel server, the rule shapes of relay/entrypoint.sh) and the node's tunnel
WS_PW="wsPASSwsPASSwsPASSwsPASS$RANDOM"; PC_PW="pcPASSpcPASSpcPASSpcPASS$RANDOM"
cat > "$ROOT/users.json" <<J
{"office-pc:$PC_PW": ["^R:127\\\\.0\\\\.0\\\\.1:$B\$"], "avi-mac:$WS_PW": ["^127\\\\.0\\\\.0\\\\.1:($B)\$"]}
J
"$CHISEL" server --keygen "$ROOT/relay.key" >/dev/null 2>&1
CHISEL_KEY_FILE="$ROOT/relay.key" "$CHISEL" server --host 127.0.0.1 --port "$RELAY_PORT" --reverse --authfile "$ROOT/users.json" \
  --keepalive 25s > "$ROOT/log/relay.log" 2>&1 & PIDS="$PIDS $!"
wait_port "$RELAY_PORT" || { echo "FAIL: relay"; exit 1; }
sleep 0.3; FP="$(grep -m1 -o 'Fingerprint .*' "$ROOT/log/relay.log" | awk '{print $2}')"
start_tunnel() {
  AUTH="office-pc:$PC_PW" CHISEL_WS_TIMEOUT=10s "$CHISEL" client --fingerprint "$FP" --keepalive 25s --max-retry-interval 2s \
    "http://127.0.0.1:$RELAY_PORT" "R:127.0.0.1:$B:127.0.0.1:$SSHD_PORT" >> "$ROOT/log/tunnel.log" 2>&1 & TUNNEL=$!
  PIDS="$PIDS $TUNNEL"; wait_port "$B"
}
start_tunnel || { echo "FAIL: tunnel"; cat "$ROOT/log/tunnel.log"; exit 1; }

# ---- the node's records: the self-test cannot run on stubs, so it is recorded as passed for this boot
"$PY" - "$REPO/src" "$ROOT/srv" <<'P'
import sys, os
sys.path.insert(0, sys.argv[1]); os.environ["MU_NODE_ROOT"] = sys.argv[2]
from mu import node, durable
durable.write_record(os.path.join(sys.argv[2], "node.json"), {"name": "office-pc", "protocol": 1, "min_free_gb": 0,
    "selftest": {"ok": True, "at": "stub", "boot_id": node.boot_id(), "detail": "recorded by local.sh (stub systemd)"}})
P
node_env() { env MU_HOME="$NODE_HOME" MU_NODE_ROOT="$ROOT/srv" MU_SYSTEMCTL="$STUBS/systemctl" MU_SYSTEMD_RUN="$STUBS/systemd-run" \
  SD="$SD" MU_NODE_FAULT_FILE="$ROOT/fault" MU_NODE_TICK=0.3 "$@"; }
start_runner() { node_env /bin/bash "$NODE_HOME/bin/mu" node run >> "$ROOT/log/runner.log" 2>&1 & RUNNER=$!; PIDS="$PIDS $RUNNER"; }
start_runner

# ---- the workstation
ws() { (cd "${CWD:-$ROOT}" && env MU_HOME="$WS_HOME" MU_POLL_S=0.3 /bin/bash "$WS_HOME/bin/mu" "$@"); }
printf 'μ job layer, local end to end · %s · chisel %s · %s\n' "$ROOT" "$("$CHISEL" --version)" "$("$SSHD" -V 2>&1 | head -1)"

section "pairing and the connection"
out="$(printf '%s\n' "$WS_PW" | ws compute add office-pc --host-key "$(cat "$ROOT/hostkey.pub")" --relay "http://127.0.0.1:$RELAY_PORT" \
  --relay-fingerprint "$FP" --relay-user avi-mac --relay-port "$B" --secret-store file --relay-password-stdin --json 2>&1)"
eq "mu compute add: ok" "$(printf '%s' "$out" | jget ok)" true
# this test logs in as the current user, not mu
sed -i.bak "s/^  User mu$/  User $(id -un)/" "$WS_HOME/compute/office-pc/ssh_config"
printf 'restrict %s avi-mac\n' "$(cut -d' ' -f1,2 "$WS_HOME/compute/office-pc/id_ed25519.pub")" >> "$ROOT/authorized_keys"
out="$(ws compute status office-pc --json 2>&1)"; rc=$?
eq "mu compute status: exit 0" "$rc" 0
eq "  the node answers protocol 1" "$(printf '%s' "$out" | jget hello.protocol)" 1
eq "  the key's name comes from the key file" "$(printf '%s' "$out" | jget hello.key)" avi-mac
eq "  the runner is active" "$(printf '%s' "$out" | jget hello.runner)" active
out2="$(ws compute status office-pc --json 2>&1)"
eq "  a second call reuses the connection" "$(printf '%s' "$out2" | jget reused)" true
echo "  info  first $(printf '%s' "$out" | jget rtt_s) s, reused $(printf '%s' "$out2" | jget rtt_s) s"

section "a job: submit, status --wait, log, fetch"
P="$ROOT/my project"; mkdir -p "$P"
( cd "$P" && git init -q -b main && git config user.name t && git config user.email t@x
  printf '[mu]\nversion = "v2026.11.6"\n[compute]\noutputs = ["results/*.json"]\n' > mu.toml
  printf 'import os, sys\nprint("hello from", os.getcwd().rsplit("/", 2)[-2:])\nos.makedirs("../results", exist_ok=True)\nopen("../results/fea.json", "w").write("{\\"stress\\": 42}\\n")\nprint("argv", sys.argv[1:])\n' > fea.py
  mkdir -p frame; printf 'x\n' > frame/keep.txt
  git add -A && git commit -qm first )
out="$(CWD="$P/frame" ws submit --json -- python3 ../fea.py one "two words" 2>&1)"; rc=$?
eq "mu submit: exit 0" "$rc" 0
[ "$rc" = 0 ] || printf '%s\n' "$out" | sed 's/^/        | /'
JOB="$(printf '%s' "$out" | jget job_id)"; echo "  job $JOB"
eq "  state queued" "$(printf '%s' "$out" | jget state)" queued
out="$(CWD="$P" ws status "$JOB" --wait --timeout 60 --json 2>&1)"; rc=$?
eq "mu status --wait: exit 0, succeeded" "$rc $(printf '%s' "$out" | jget state)" "0 succeeded"
out="$(CWD="$P" ws log "$JOB" 2>&1)"
has "mu log: the job's output" "$out" "hello from"
has "  argv arrives as a list (no shell)" "$out" "argv ['one', 'two words']"
out="$(CWD="$P" ws log "$JOB" --json --offset 5 2>&1)"
eq "mu log --json --offset 5: offset 5, eof" "$(printf '%s' "$out" | jget offset) $(printf '%s' "$out" | jget eof)" "5 true"
out="$(CWD="$P" ws fetch "$JOB" --json 2>&1)"; rc=$?
eq "mu fetch: exit 0" "$rc" 0
eq "  the output is written" "$(cat "$P/results/fea.json" 2>&1)" '{"stress": 42}'
[ -f "$P/runs/$JOB.json" ] && ok "  runs/$JOB.json is written" || bad "  no run record"
has "  the run record names the archive" "$(cat "$P/runs/$JOB.json")" "office-pc:/srv/mu/jobs/$JOB/work"
has "  the node registered the pin" "$(cat "$NODE_HOME/projects.txt")" "$ROOT/srv/pins/"

section "queue, cancel, repeat submit"
J1="$(CWD="$P" ws submit --json -- sleep 30 | jget job_id)"
J2="$(CWD="$P" ws submit --json -- sh -c 'echo second' | jget job_id)"
sleep 1
eq "the second job waits (queued, position 1)" "$(CWD="$P" ws status "$J2" --json | jget state) $(CWD="$P" ws status "$J2" --json | jget position)" "queued 1"
eq "mu cancel of the queued job: cancelled" "$(CWD="$P" ws cancel "$J2" --json | jget state)" cancelled
eq "mu cancel of the running job: cancelled" "$(CWD="$P" ws cancel "$J1" --json | jget state)" cancelled
out="$(CWD="$P" ws status "$J1" --wait --json)"; rc=$?
eq "mu status --wait of a cancelled job: exit 7" "$rc" 7
out="$(CWD="$P" ws submit --resume "$J2" --json 2>&1)"
eq "mu submit --resume of a known id: duplicate, one job" "$(printf '%s' "$out" | jget duplicate) $(printf '%s' "$out" | jget state)" "true cancelled"
out="$(CWD="$P" ws status --json 2>&1)"
eq "mu status lists this project's 3 jobs" "$(printf '%s' "$out" | "$PY" -c 'import json,sys; print(len(json.load(sys.stdin)["jobs"]))')" 3

section "refusals"
cp "$WS_HOME/compute/office-pc/relay.auth" "$ROOT/auth.good"
printf 'avi-mac:wrongwrongwrongwrongwrong\n' > "$WS_HOME/compute/office-pc/relay.auth"
CP="$(ws compute status office-pc --json | jget control_path)"
close_master() { ssh -F "$WS_HOME/compute/office-pc/ssh_config" -o ControlPath="\"$CP\"" -O exit mu-office-pc 2>/dev/null; }
close_master
out="$(ws compute status office-pc --json 2>&1)"; rc=$?
eq "a wrong relay password: exit 5, relay-credential" "$rc $(printf '%s' "$out" | jget error)" "5 relay-credential"
cp "$ROOT/auth.good" "$WS_HOME/compute/office-pc/relay.auth"
kill "$TUNNEL"; sleep 0.5
t0=$(date +%s); out="$(ws compute status office-pc --json 2>&1)"; rc=$?; t1=$(date +%s)
eq "node offline (a shared connection was open): exit 4, node-offline" "$rc $(printf '%s' "$out" | jget error)" "4 node-offline"
[ $((t1 - t0)) -le 16 ] && ok "  within 16 s: 12 s on the stalled master, then a new connection ($((t1 - t0)) s)" || bad "  took $((t1 - t0)) s"
t0=$(date +%s); out="$(ws compute status office-pc --json 2>&1)"; rc=$?; t1=$(date +%s)
eq "node offline, new connection: exit 4, node-offline" "$rc $(printf '%s' "$out" | jget error)" "4 node-offline"
[ $((t1 - t0)) -le 5 ] && ok "  within 5 s ($((t1 - t0)) s)" || bad "  took $((t1 - t0)) s"
start_tunnel
out="$(ws compute status office-pc --json 2>&1)"; rc=$?
eq "the tunnel back: exit 0" "$rc" 0
CWD="$P" ws submit --json -- true > /dev/null
echo v2026.11.5 > /dev/null
( cd "$P" && sed -i.bak 's/v2026.11.6/v2026.11.5/' mu.toml && rm mu.toml.bak && git commit -qam pin5 )
out="$(CWD="$P" ws submit --json -- true 2>&1)"; rc=$?
eq "a pin the node does not have: exit 3, toolchain-missing" "$rc $(printf '%s' "$out" | jget error)" "3 toolchain-missing"
has "  with the command that installs it" "$out" "mu install v2026.11.5"

printf '\n%d passed, %d failed\n' "$PASS" "$FAILN"
if [ "$FAILN" != 0 ]; then
  printf 'failed:%s\n' "$FAILED"
  echo "--- runner log"; tail -30 "$ROOT/log/runner.log"; echo "--- audit"; tail -20 "$ROOT/srv/audit.log" 2>/dev/null
  exit 1
fi
exit 0
