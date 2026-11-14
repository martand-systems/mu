#!/bin/bash
# The job layer end to end on a GitHub ubuntu-24.04 runner (docs/design/job-layer.md §16, "node level"): real
# systemd and cgroup v2, a user mu with linger and a user manager, mu-sshd with the real gate on 127.0.0.1:2222, the
# S4 replica of the relay behind Traefik (tools/s4/run.sh relay), and the workstation as another user (ws) going
# THROUGH the relay. Every row of §16 that can be real here is run, then run again with its fault put in, and must
# then fail. CI only (it creates users and services): .github/workflows/job-layer.yml.
#
#   tools/test-jobs/e2e.sh probe        the probe of what §18 only read (the first CI job)
#   tools/test-jobs/e2e.sh all          set-up, every row, then every row with its fault, then the host script's
#                                       uninstall and reinstall (H rows: the node is made by mu-wsl2-distro.sh itself)
#   ONLY="R01 R06" tools/test-jobs/e2e.sh all      a subset (set-up always runs)
# Results: .scratch/jobs/results.tsv and summary.md; logs in .scratch/jobs/logs/.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$ROOT/.scratch/jobs"; S4W="$ROOT/.scratch/s4"
MODE="${1:-all}"
[ "${CI:-}" = true ] || { echo "e2e.sh: CI only (it creates users and system services)" >&2; exit 2; }
mkdir -p "$WORK/logs" "$WORK/out"; : > "$WORK/results.tsv"
cd /tmp   # commands run as mu and ws cannot enter the checkout
N_FAIL=0
row() {   # row RESULT ID WHAT MEASURED
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$(printf '%s' "$4" | tr '\t\n' '  ' | cut -c1-400)" >> "$WORK/results.tsv"
  printf '%-10s %-4s %s: %s\n' "$1" "$2" "$3" "$(printf '%s' "$4" | tr '\n' ' ' | cut -c1-300)"
  case "$1" in FAIL|MISSED) N_FAIL=$((N_FAIL + 1)) ;; esac
}
want() { [ -z "${ONLY:-}" ] && return 0; case " $ONLY " in *" $1 "*) return 0 ;; esac; return 1; }
M=""   # what a row measured
run_row() {   # run_row ID WHAT FUNCTION
  want "$1" || return 0
  printf '\n-- %s %s\n' "$1" "$2"
  M=""; if "$3"; then row PASS "$1" "$2" "$M"; else row FAIL "$1" "$2" "$M"; fi
}
must_fail() {   # must_fail ID FAULT ON OFF FUNCTION: with the fault on, the row has to fail
  want "$1" || return 0
  printf '\n-- %s with fault %s\n' "$1" "$2"
  "$3"; M=""
  if "$5"; then row MISSED "$1" "with fault $2 the row still passed (the test does not test it)" "$M"
  else row FAILS-OK "$1" "with fault $2 it fails, as it must" "$M"; fi
  "$4"
}
jget() { python3 -c 'import json,sys
t=sys.stdin.read().strip().splitlines()
d=json.loads(t[-1] if t else "{}")
for k in sys.argv[1].split("."):
    d = d.get(k) if isinstance(d, dict) else None
print("" if d is None else (json.dumps(d) if isinstance(d, (dict, list, bool)) else d))' "$1" 2>/dev/null; }
now() { python3 -c 'import time; print("%.2f" % time.time())'; }
since() { python3 -c 'import sys,time; print("%.1f" % (time.time() - float(sys.argv[1])))' "$1"; }
lt() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a + 0 < b + 0) }'; }

MU_UID="$(id -u mu 2>/dev/null || echo 0)"
WSH=/home/ws/.local/share/mu; NODEH=/home/mu/.local/share/mu
WCFG="$WSH/compute/office-pc/ssh_config"; WCP="$WSH/run/office-pc.sock"
WSF=""; DF=""   # the workstation's fault (MU_JOBS_FAULT) and the dispatcher's (MU_DISPATCH_FAULT)
WCWD=/home/ws/proj
ws() {   # the workstation: `mu ...` as ws, in $WCWD
  sudo -u ws -H env -u XDG_CONFIG_HOME -u XDG_RUNTIME_DIR MU_POLL_S=1 MU_JOBS_FAULT="$WSF" MU_DISPATCH_FAULT="$DF" sh -c 'cd "$0" && exec "$@"' "$WCWD" \
    "$WSH/bin/mu" "$@"
}
asws() { sudo -u ws -H env -u XDG_CONFIG_HOME -u XDG_RUNTIME_DIR MU_JOBS_FAULT="$WSF" "$@"; }
asmu() { sudo -u mu -H env -u XDG_CONFIG_HOME XDG_RUNTIME_DIR="/run/user/$MU_UID" "$@"; }
mu_node() { asmu "$NODEH/bin/mu" "$@"; }
close_master() { asws ssh -F "$WCFG" -o "ControlPath=\"$WCP\"" -O exit mu-office-pc >/dev/null 2>&1 || true; }
wssh() { asws ssh -F "$WCFG" -o ControlPath=none "$@"; }
audit_lines() { sudo sh -c 'wc -l < /srv/mu/audit.log' 2>/dev/null || echo unreadable; }   # root reads it (mu's 0700 folder)
nfault() {   # nfault NAMES: the node's faults (root-owned /etc/mu/test-fault); the runner restarts to read them
  if [ -n "$*" ]; then echo "$*" | sudo tee /etc/mu/test-fault >/dev/null; sudo chmod 644 /etc/mu/test-fault
  else sudo rm -f /etc/mu/test-fault; fi
  asmu systemctl --user restart mu-node.service; sleep 1
}
jobstate() { ws status "$1" --json 2>/dev/null | jget "${2:-state}"; }
wait_state() {   # wait_state JOB "STATES" SECONDS -> 0 when the job is in one of STATES
  local end=$(( $(date +%s) + $3 )) s
  while [ "$(date +%s)" -le "$end" ]; do
    s="$(jobstate "$1")"; case " $2 " in *" $s "*) return 0 ;; esac; sleep 1
  done; return 1
}
submit() { ws submit --json "$@" 2>&1 | tee -a "$WORK/logs/submits.log" | jget job_id; }
nodelog() { sudo cat "/srv/mu/jobs/$1/log" 2>/dev/null; }
relay_ctr() { docker ps -aq --filter label=com.docker.compose.project=mu-relay-ci0001 --filter label=com.docker.compose.service=mu-relay | head -1; }
wait_up() {   # until the node answers through the relay (the tunnel reconnects every 15 s at most)
  local i; for i in $(seq 1 ${1:-90}); do ws compute status office-pc --json >/dev/null 2>&1 && return 0; close_master; sleep 1; done; return 1
}
newproj() {   # newproj DIR PIN: a git project of ws with mu.toml, job.py and a commit
  [ -f /tmp/mu-e2e-job.py ] || cat > /tmp/mu-e2e-job.py <<'PYJ'
import os, sys
os.makedirs("results", exist_ok=True)
open("results/out.json", "w").write('{"ok": 1}\n')
print("job ran", sys.argv[1:])
PYJ
  chmod 644 /tmp/mu-e2e-job.py
  asws sh -c 'rm -rf "$0" && mkdir -p "$0" && cd "$0" && git init -q && cp /tmp/mu-e2e-job.py job.py &&
    printf "[mu]\nversion = \"%s\"\n[compute]\noutputs = [\"results/*.json\"]\n" "$1" > mu.toml &&
    git add -A && git commit -qm first' "$1" "$2"
}

# ================================================================================================== set-up
setup() {
  echo "== set-up: the relay replica, the node, the workstation"
  local gz="$WORK/chisel.gz" sha
  sha="$(awk -F'"' '/^\[chisel.linux-64\]/ { s = 1 } s && /^sha256/ { print $2; exit }' "$ROOT/toolchain/apps.toml")"
  curl -fsSL -o "$gz" "$(awk -F'"' '/^\[chisel.linux-64\]/ { s = 1 } s && /^url/ { print $2; exit }' "$ROOT/toolchain/apps.toml")"
  echo "$sha  $gz" | sha256sum -c - || { row FAIL S0 "chisel from toolchain/apps.toml" "SHA-256 differs"; return 1; }
  gunzip -c "$gz" > "$WORK/chisel"; chmod 755 "$WORK/chisel"
  row PASS S0 "chisel client from the apps.toml pin, SHA-256 checked" "$("$WORK/chisel" --version)"
  TRAEFIK_VERSION="${TRAEFIK_VERSION:-3.7.16}" "$ROOT/tools/s4/run.sh" relay > "$WORK/logs/relay-up.log" 2>&1 \
    || { tail -30 "$WORK/logs/relay-up.log"; row FAIL S1 "relay replica" "did not start"; return 1; }
  row PASS S1 "relay and Traefik replica (tools/s4/run.sh relay)" "$(tail -1 "$WORK/logs/relay-up.log")"
  sudo CI=true "$ROOT/tools/test-jobs/node-setup.sh" "$ROOT" "$WORK/chisel" "$S4W/certs/ca.crt" "$S4W/env/compute.env" "$WORK/out/host" \
    > "$WORK/logs/node-setup.log" 2>&1 || { tail -60 "$WORK/logs/node-setup.log"; row FAIL S2 "node set-up" "failed"; return 1; }
  sudo chown -R "$(id -u)" "$WORK/out/host"
  MU_UID="$(id -u mu)"   # the user exists only now
  row PASS S2 "node set-up by the host script: mu-wsl2-distro.sh install --no-wsl, tunnel credential on stdin, install twice more" \
    "changes per run: $(for n in 1 2 3; do sed -n 's/^changed=//p' "$WORK/out/host/install-$n.result"; done | tr '\n' ' ')(run 3 must be 0); uid of mu $MU_UID"
  local fp pw out hk
  fp="$(sed -n 's/^RELAY_FINGERPRINT=//p' "$S4W/env/workstation.env")"; pw="$(sed -n 's/^RELAY_PASSWORD=//p' "$S4W/env/workstation.env")"
  hk="$(sudo mu-wsl2-distro host-key --json)"; echo "$hk" > "$WORK/out/host-key.json"
  out="$(printf '%s\n' "$pw" | sudo -u ws -H "$WSH/bin/mu" compute add office-pc --host-key "$(printf '%s' "$hk" | jget public_key)" \
    --relay https://relay.martand.systems --relay-fingerprint "$fp" --relay-user avi-mac --relay-port 7001 --relay-password-stdin --json 2>&1)"
  echo "$out" > "$WORK/out/compute-add.json"
  [ "$(printf '%s' "$out" | jget ok)" = true ] || { row FAIL S3 "mu compute add" "$out"; return 1; }
  authorize
  row PASS S3 "mu compute add with mu-wsl2-distro host-key's key, then mu-wsl2-distro authorize" "$(printf '%s' "$out" | jget key_fingerprint)"
  newproj "$WCWD" v2026.11.6
  if ! wait_up 60; then
    { sudo ls -la "$WSH/compute/office-pc"; sudo cat /etc/mu/authorized_keys/mu; wssh -vv mu-office-pc hello protocol=1 < /dev/null; } \
      > "$WORK/logs/s4-debug.log" 2>&1; tail -40 "$WORK/logs/s4-debug.log"
    row FAIL S4 "first hello through the relay" "$(ws compute status office-pc --json 2>&1)"; return 1
  fi
  row PASS S4 "first hello through the relay" "$(ws compute status office-pc --json | jget rtt_s) s"
}
PUB() { sudo cat "$WSH/compute/office-pc/id_ed25519.pub" | cut -d' ' -f1,2; }
authorize() { sudo mu-wsl2-distro authorize avi-mac "$(sudo cat "$WSH/compute/office-pc/id_ed25519.pub")" > /dev/null; }   # the host script's own command
revoke() { sudo mu-wsl2-distro revoke avi-mac > /dev/null; }

# ================================================================================================== probe (§16)
p_systemd() { M="$(cat /proc/1/comm)"; [ "$M" = systemd ]; }
p_cgroup2() { M="$(stat -fc %T /sys/fs/cgroup)"; [ "$M" = cgroup2fs ]; }
p_delegated() {
  local f="/sys/fs/cgroup/user.slice/user-$MU_UID.slice/user@$MU_UID.service/cgroup.controllers"
  M="$(cat "$f" 2>&1)"; case " $M " in *" memory "*) return 0 ;; esac; return 1
}
p_oom() {
  asmu systemd-run --user --quiet --unit=probe-oom -p MemoryMax=64M -p MemorySwapMax=0 -- python3 -c "b = b'x' * (256 << 20)" 2>&1
  local i r; for i in $(seq 1 60); do r="$(asmu systemctl --user show probe-oom.service -p Result --value)"
    [ "$(asmu systemctl --user show probe-oom.service -p ActiveState --value)" = active ] || break; sleep 0.5; done
  M="Result=$r"; asmu systemctl --user reset-failed probe-oom.service >/dev/null 2>&1; [ "$r" = oom-kill ]
}
p_sshd() {
  local u a; u="$(sudo /usr/sbin/sshd -T -f /etc/mu/sshd_config -C user=mu,host=localhost,addr=127.0.0.1 | grep -E '^usepam ')"
  a="$(systemctl is-active mu-sshd.service)"
  local out; out="$(wssh mu-office-pc hello protocol=1 2>&1)"
  M="mu-sshd $a; $u; hello: $(printf '%s' "$out" | jget ok) as key $(printf '%s' "$out" | jget key)"
  [ "$a" = active ] && [ "$u" = "usepam yes" ] && [ "$(printf '%s' "$out" | jget key)" = avi-mac ]
}
p_no22() { M="$(ss -ltnH | awk '{print $4}' | tr '\n' ' ')"; ! printf '%s' "$M" | grep -qE '(^| )[^ ]*:22( |$)' && printf '%s' "$M" | grep -q '127.0.0.1:2222'; }
p_selftest() { local o; o="$(mu_node node selftest --json 2>&1)"; M="$(printf '%s' "$o" | jget detail)"; [ "$(printf '%s' "$o" | jget ok)" = true ]; }
probe() {
  run_row P1 "PID 1 is systemd" p_systemd
  run_row P2 "/sys/fs/cgroup is cgroup v2" p_cgroup2
  run_row P3 "mu's user manager is delegated the memory controller" p_delegated
  run_row P4 "systemd-run --user -p MemoryMax=64M ends a 256 MB allocation with Result=oom-kill" p_oom
  run_row P5 "mu-sshd runs with UsePAM yes and logs mu in by key (through the relay)" p_sshd
  run_row P6 "no listener on port 22; mu-sshd on 127.0.0.1:2222 only" p_no22
  run_row P7 "mu node selftest passes" p_selftest
}

# ================================================================================================== rows of §16
r_hostkey() {
  close_master
  local n0 out rc; n0="$(audit_lines)"
  sudo cp "$WSH/compute/office-pc/known_hosts" "$WORK/known_hosts.good"
  printf 'mu-office-pc %s\n' "$(ssh-keygen -q -t ed25519 -N '' -f "$WORK/other-hostkey-$RANDOM" -C x >/dev/null; cat "$WORK"/other-hostkey-*.pub | tail -1 | cut -d' ' -f1,2)" \
    | sudo -u ws tee "$WSH/compute/office-pc/known_hosts" >/dev/null
  out="$(ws compute status office-pc --json 2>&1)"; rc=$?
  sudo cp "$WORK/known_hosts.good" "$WSH/compute/office-pc/known_hosts"; sudo chown ws:ws "$WSH/compute/office-pc/known_hosts"
  close_master
  M="exit $rc, $(printf '%s' "$out" | jget error); node requests during it: $(( $(audit_lines) - n0 ))"
  [ "$rc" = 5 ] && [ "$(printf '%s' "$out" | jget error)" = host-key ] && [ "$(audit_lines)" = "$n0" ]
}
regen_config() {   # the generated ssh_config again (with $WSF in force)
  asws python3 -I -S -c 'import sys; sys.path.insert(0, sys.argv[1]); from mu import jobs; open(sys.argv[3], "w").write(jobs.ssh_config_text("office-pc", sys.argv[2]))' \
    "$WSH/toolchains/v2026.11.7/recipe/src" "$WSH" "$WCFG"
}
f_strict_on() { WSF=no-strict-hostkey; regen_config; WSF=""; close_master; }
f_strict_off() { WSF=""; regen_config; close_master; }

r_keyreq() {
  close_master; [ "${KEEP_KEY:-0}" = 1 ] || revoke
  local out rc; out="$(ws compute status office-pc --json 2>&1)"; rc=$?
  authorize; close_master
  M="exit $rc, $(printf '%s' "$out" | jget error)"
  [ "$rc" = 5 ] && [ "$(printf '%s' "$out" | jget error)" = key-not-authorised ]
}
f_keep_on() { KEEP_KEY=1; }
f_keep_off() { KEEP_KEY=0; }

r_noshell() {
  local a b c
  a="$(wssh mu-office-pc 'cat /etc/passwd' 2>&1 < /dev/null)"; b="$(wssh mu-office-pc id 2>&1 < /dev/null)"; c="$(wssh -T mu-office-pc 2>&1 < /dev/null)"
  M="'cat /etc/passwd': $(printf '%s' "$a" | jget error); 'id': $(printf '%s' "$b" | jget error); no command: $(printf '%s' "$c" | jget error)"
  case "$a$b$c" in *root:x:0*|*uid=*) M="$M; A SHELL RAN"; return 1 ;; esac
  [ "$(printf '%s' "$a" | jget error)" = bad-character ] && [ "$(printf '%s' "$b" | jget error)" = unknown-verb ] && \
    [ "$(printf '%s' "$c" | jget error)" = no-command ]
}
sshd_edit() {   # sshd_edit SED: change /etc/mu/sshd_config (saved first) and restart mu-sshd
  sudo cp -n /etc/mu/sshd_config "$WORK/sshd_config.good" 2>/dev/null || true
  sudo sed -i "$1" /etc/mu/sshd_config && sudo systemctl restart mu-sshd.service; sleep 1
}
sshd_restore() { sudo cp "$WORK/sshd_config.good" /etc/mu/sshd_config && sudo systemctl restart mu-sshd.service; sleep 1; }
f_force_on() { sshd_edit '/^ForceCommand /d'; }
f_force_off() { sshd_restore; }

r_notty() {
  local t fwd port=17999 banner
  t="$(wssh -o RequestTTY=force -tt mu-office-pc hello 2>&1 < /dev/null)"
  asws ssh -F "$WCFG" -o ControlPath=none -o ClearAllForwardings=no -o ExitOnForwardFailure=no -N -L "127.0.0.1:$port:127.0.0.1:2222" mu-office-pc > "$WORK/logs/fwd.log" 2>&1 &
  local pid=$!; sleep 4
  banner="$(timeout 5 bash -c "exec 3<>/dev/tcp/127.0.0.1/$port; head -c 7 <&3" 2>/dev/null)"
  sudo kill "$pid" 2>/dev/null; sudo pkill -u ws -f -- "-L 127.0.0.1:$port" 2>/dev/null; wait "$pid" 2>/dev/null
  case "$t" in *"PTY allocation request failed"*) local tty=refused ;; *) local tty=granted ;; esac
  M="terminal: $tty; forward: $( [ "$banner" = SSH-2.0 ] && echo "opened (got $banner)" || echo "no channel ($(grep -o 'administratively prohibited\|open failed[^"]*' "$WORK/logs/fwd.log" | head -1))")"
  [ "$tty" = refused ] && [ "$banner" != SSH-2.0 ]
}
f_locks_on() {
  sshd_edit '/^PermitTTY /d; /^AllowTcpForwarding /d'
  printf '%s avi-mac\n' "$(PUB)" | sudo tee /etc/mu/authorized_keys/mu >/dev/null
}
f_locks_off() { sshd_restore; authorize; }

r_relay() {
  local conf="$WSH/compute/office-pc/node.conf" auth="$WSH/compute/office-pc/relay.auth" out rc t0 t a b c d e
  sudo cp "$conf" "$WORK/node.conf.good"; sudo cp "$auth" "$WORK/relay.auth.good"
  restore() { sudo cp "$WORK/node.conf.good" "$conf"; sudo cp "$WORK/relay.auth.good" "$auth"; sudo chown ws:ws "$conf" "$auth"; sudo chmod 600 "$conf" "$auth"; close_master; }
  wsed() { sudo sed -i "$1" "$conf"; sudo chown ws:ws "$conf"; }
  close_master; printf 'avi-mac:%s\n' "wrongwrongwrongwrongwrong0" | sudo tee "$auth" >/dev/null
  out="$(ws compute status office-pc --json 2>&1)"; rc=$?; a="$rc $(printf '%s' "$out" | jget error)"; restore
  wsed 's|^relay_fingerprint=.*|relay_fingerprint=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=|'
  out="$(ws compute status office-pc --json 2>&1)"; rc=$?; b="$rc $(printf '%s' "$out" | jget error)"; restore
  wsed 's|^relay_port=.*|relay_port=7002|'
  out="$(ws compute status office-pc --json 2>&1)"; rc=$?; c="$rc $(printf '%s' "$out" | jget error)"; restore
  sudo systemctl stop mu-tunnel.service; sleep 1
  t0="$(now)"; out="$(ws compute status office-pc --json 2>&1)"; rc=$?; t="$(since "$t0")"; d="$rc $(printf '%s' "$out" | jget error) in $t s"
  local td="$t"
  sudo systemctl start mu-tunnel.service
  docker stop "$(relay_ctr)" >/dev/null; sleep 1
  t0="$(now)"; out="$(ws compute status office-pc --json 2>&1)"; rc=$?; t="$(since "$t0")"; e="$rc $(printf '%s' "$out" | jget error) in $t s"
  local te="$t"
  docker start "$(relay_ctr)" >/dev/null; wait_up 120 >/dev/null
  M="wrong password: $a; wrong fingerprint: $b; foreign port: $c; node offline: $d; relay down: $e"
  [ "$a" = "5 relay-credential" ] && [ "$b" = "5 relay-fingerprint" ] && [ "$c" = "5 relay-rule" ] && \
    [ "${d% in *}" = "4 node-offline" ] && lt "$td" 5 && [ "${e% in *}" = "4 relay-unreachable" ] && lt "$te" 5
}
f_bare_on() { WSF=bare-proxy; close_master; }
f_bare_off() { WSF=""; close_master; }

r_nosecret() {
  close_master
  local pw cpw hits=0 i scan
  pw="$(sudo sed -n 's/^avi-mac://p' "$WSH/compute/office-pc/relay.auth")"
  cpw="$(sed -n 's/^RELAY_PASSWORD=//p' "$S4W/env/compute.env")"
  ws compute status office-pc --json >/dev/null 2>&1   # a master (and its proxy and chisel) now stays for 300 s
  ws status --json > /dev/null 2>&1 &
  for i in $(seq 1 30); do
    scan="$(sudo sh -c 'for f in /proc/[0-9]*/cmdline; do tr "\0" " " < "$f" 2>/dev/null; echo; done')"
    case "$scan" in *"$pw"*|*"$cpw"*) hits=$((hits + 1)) ;; esac   # (not grep -q in a pipe: with pipefail, its early exit reads as no match)
    sleep 0.1
  done
  wait
  local nproxy authflag; nproxy="$(pgrep -u ws -f 'chisel client' | wc -l)"
  authflag="$(pgrep -u ws -af 'chisel client' | grep -c -- ' --auth ')"
  M="$hits of 30 scans of every process's command line found a relay password (workstation chisel processes: $nproxy, with --auth: $authflag)"
  [ "$hits" = 0 ] && [ "$nproxy" -ge 1 ]
}
f_argv_on() { WSF=auth-in-argv; close_master; }
f_argv_off() { WSF=""; close_master; }

r_lfs() {
  local p=/home/ws/lfsproj out rc
  newproj "$p" v2026.11.6
  asws sh -c 'cd "$0" && git lfs install --local >/dev/null && git lfs track "*.bin" >/dev/null && head -c 20000 /dev/urandom > big.bin && git add -A && git commit -qm lfs' "$p"
  out="$(WCWD="$p" ws submit --json -- python3 job.py 2>&1)"; rc=$?
  M="exit $rc; $(printf '%s' "$out" | jget state)$(printf '%s' "$out" | jget message | cut -c1-200); warnings: $(printf '%s' "$out" | jget warnings.lfs)"
  [ "$rc" = 0 ]
}
f_hooks_on() { WSF=push-hooks; }
f_hooks_off() { WSF=""; }

r_writeonce() {
  local p=/home/ws/woproj-$RANDOM id pid a b c d
  newproj "$p" v2026.11.6
  id="$(WCWD="$p" submit -- true)"; pid="$(WCWD="$p" jobstate "$id" project_id)"
  gp() { asws sh -c 'cd "$0" && c="$1" && shift && GIT_SSH_COMMAND="ssh -F \"$c\" -o ControlPath=none" git -c core.hooksPath=/dev/null push --no-verify "$@" 2>&1' "$p" "$WCFG" "$@"; }
  asws sh -c 'cd "$0" && echo change >> job.py && git commit -qam two' "$p"
  a="$(gp --force "mu-office-pc:$pid.git" "HEAD:refs/mu/snap/$id" | tail -2 | tr '\n' ' ')"
  b="$(gp "mu-office-pc:$pid.git" ":refs/mu/snap/$id" | tail -2 | tr '\n' ' ')"
  c="$(gp "mu-office-pc:$pid.git" "HEAD:refs/heads/main" | tail -2 | tr '\n' ' ')"
  d="$(sudo git --git-dir="/srv/mu/projects/$pid.git" rev-parse "refs/mu/snap/$id" 2>&1) $(sudo git --git-dir="/srv/mu/projects/$pid.git" for-each-ref --format='%(refname)' | tr '\n' ' ')"
  M="overwrite: $a | delete: $b | other ref: $c | refs now: $d"
  case "$a" in *"[remote rejected]"*) ;; *) return 1 ;; esac
  case "$b" in *"[remote rejected]"*) ;; *) return 1 ;; esac
  case "$c" in *"[remote rejected]"*) ;; *) return 1 ;; esac
}
f_hook_on() { nfault no-update-hook; }
f_hook_off() { nfault; }

r_worktree() {
  local p=/home/ws/attrproj id
  newproj "$p" v2026.11.6
  asws sh -c 'cd "$0" && printf "ign.txt export-ignore\nsubst.txt export-subst\n" > .gitattributes && echo kept > ign.txt && printf "\$Format:%%H\$\n" > subst.txt && git add -A && git commit -qm attrs' "$p"
  id="$(WCWD="$p" submit -- sh -c 'test -f ign.txt && grep -q "Format:" subst.txt && echo tree-is-exact')"
  wait_state "$id" "succeeded failed" 90
  M="$(WCWD="$p" jobstate "$id"): $(nodelog "$id" | grep -v '^==' | tail -2 | tr '\n' ' ')"
  [ "$(WCWD="$p" jobstate "$id")" = succeeded ]
}
f_archive_on() { nfault git-archive; }
f_archive_off() { nfault; }

r_incremental() {
  local p=/home/ws/bigproj-$RANDOM s0 s1 s2 id1 id2
  newproj "$p" v2026.11.6
  asws sh -c 'cd "$0" && head -c 1500000 /dev/urandom | base64 > data.txt && git add -A && git commit -qm data' "$p"
  s0="$(sudo du -sb /srv/mu/projects | cut -f1)"
  id1="$(WCWD="$p" submit -- true)"; s1="$(sudo du -sb /srv/mu/projects | cut -f1)"
  asws sh -c 'cd "$0" && echo "# one more line" >> job.py' "$p"
  id2="$(WCWD="$p" submit -- true)"; s2="$(sudo du -sb /srv/mu/projects | cut -f1)"
  M="first snapshot (2 MB of data) +$((s1 - s0)) bytes on the node; second (one line of job.py changed, uncommitted) +$((s2 - s1)) bytes [$id1 $id2]"
  [ -n "$id2" ] && [ $((s2 - s1)) -lt 10240 ]
}
f_full_on() { WSF=full-push; }
f_full_off() { WSF=""; }

r_durable() {
  local id s a
  id="$(submit -- python3 job.py durable)"
  wait_state "$id" "succeeded failed interrupted cancelled timeout oom" 120
  s="$(jobstate "$id")"; a="$(jobstate "$id" attempt)"
  M="$s, attempt $a; $(sudo grep -c 're-queued' "/srv/mu/jobs/$id/events.log") re-run(s); note: $(jobstate "$id" note)"
  [ "$s" = interrupted ] && [ "$a" = 2 ]
}
f_durable_on() { :; }   # (the scenario itself is a fault: before-result)
r_durable_case() { nfault before-result; r_durable; local rc=$?; nfault; return $rc; }
r_durable_fault() { nfault before-result trust-unit-success; r_durable; local rc=$?; nfault; return $rc; }

r_torn() {
  local id1 id2 blk sz s1 a1 s2
  id1="$(submit -- python3 job.py torn)"; wait_state "$id1" "succeeded failed" 90
  sz="$(sudo stat -c %s "/srv/mu/jobs/$id1/result.json")"
  sudo sh -c "head -c $sz /dev/zero > /srv/mu/jobs/$id1/result.json"
  blk="$(submit -- sleep 8)"; id2="$(submit -- python3 job.py torn2)"; sleep 2
  sz="$(sudo stat -c %s "/srv/mu/jobs/$id2/state.json")"; sudo sh -c "head -c $sz /dev/zero > /srv/mu/jobs/$id2/state.json"
  asmu systemctl --user restart mu-node.service
  wait_state "$id2" "succeeded failed interrupted" 120; wait_state "$id1" "succeeded failed interrupted" 120
  s1="$(jobstate "$id1")"; a1="$(jobstate "$id1" attempt)"; s2="$(jobstate "$id2")"
  M="result.json NUL-filled: $s1 attempt $a1 (re-run from job.json); state.json NUL-filled while queued: $s2"
  [ "$s1" = succeeded ] && [ "$a1" = 2 ] && [ "$s2" = succeeded ]
}
f_torn_on() { nfault trust-existence; }
f_torn_off() { nfault; }

r_memory() {
  local id
  id="$(submit --mem 64M -- python3 -c "b = b'x' * (128 << 20); print('allocated 128 MB')")"
  wait_state "$id" "succeeded failed oom" 120
  M="$(jobstate "$id"): $(jobstate "$id" note)"
  [ "$(jobstate "$id")" = oom ]
}
f_mem_on() { nfault no-memory-max; }
f_mem_off() { nfault; }

r_timecancel() {
  local id1 id2 g1 g2 s1 s2
  id1="$(submit --time 4s -- sh -c 'sleep 600 & echo $! > grandchild.pid; sleep 600')"
  wait_state "$id1" "succeeded failed timeout cancelled" 60; s1="$(jobstate "$id1")"
  id2="$(submit -- sh -c 'sleep 600 & echo $! > grandchild.pid; sleep 600')"
  wait_state "$id2" running 60; sleep 2; ws cancel "$id2" --json >/dev/null; wait_state "$id2" "cancelled failed" 30; s2="$(jobstate "$id2")"
  sleep 1
  g1="$(sudo cat "/srv/mu/jobs/$id1/work/grandchild.pid" 2>/dev/null)"; g2="$(sudo cat "/srv/mu/jobs/$id2/work/grandchild.pid" 2>/dev/null)"
  local alive=""; for g in $g1 $g2; do sudo kill -0 "$g" 2>/dev/null && alive="$alive $g"; done
  M="--time 4s: $s1; cancel: $s2; grandchildren $g1 $g2 still alive:${alive:- none}"
  sudo kill $alive 2>/dev/null
  [ "$s1" = timeout ] && [ "$s2" = cancelled ] && [ -n "$g1" ] && [ -n "$g2" ] && [ -z "$alive" ]
}
f_kill_on() { nfault killmode-process; }
f_kill_off() { nfault; }

r_queue() {
  sudo -u mu sh -c ': > /home/mu/queue.log'
  local a b c seq
  a="$(submit -- sh -c 'echo "start $MU_JOB_ID" >> /home/mu/queue.log; sleep 2; echo "end $MU_JOB_ID" >> /home/mu/queue.log')"
  b="$(submit -- sh -c 'echo "start $MU_JOB_ID" >> /home/mu/queue.log; sleep 2; echo "end $MU_JOB_ID" >> /home/mu/queue.log')"
  c="$(submit -- sh -c 'echo "start $MU_JOB_ID" >> /home/mu/queue.log; sleep 2; echo "end $MU_JOB_ID" >> /home/mu/queue.log')"
  wait_state "$c" "succeeded failed" 90; wait_state "$a" "succeeded failed" 30; wait_state "$b" "succeeded failed" 30
  seq="$(sudo cat /home/mu/queue.log | tr '\n' ' ')"
  M="$seq"
  [ "$seq" = "start $a end $a start $b end $b start $c end $c " ]
}
f_queue_on() { nfault start-on-submit; }
f_queue_off() { nfault; }

r_repeat() {
  local id out n0 n1 rc
  WSF=lose-answer; out="$(ws submit --json -- python3 job.py repeat 2>&1)"; rc=$?; WSF="${SAVED_WSF:-}"
  id="$(printf '%s' "$out" | jget job_id)"
  n0="$(sudo ls /srv/mu/jobs | grep -c .)"
  out="$(ws submit --resume "$id" --json 2>&1)"
  n1="$(sudo ls /srv/mu/jobs | grep -c .)"
  local out2; out2="$(ws submit --resume "$id" --json 2>&1)"
  M="lost answer: exit $rc; --resume: duplicate $(printf '%s' "$out" | jget duplicate); again: duplicate $(printf '%s' "$out2" | jget duplicate); job folders $n0 -> $n1 -> $(sudo ls /srv/mu/jobs | grep -c .)"
  [ "$rc" = 4 ] && [ "$(printf '%s' "$out" | jget duplicate)" = true ] && [ "$n0" = "$n1" ] && \
    [ "$(sudo ls /srv/mu/jobs | grep -c .)" = "$n1" ]
}
f_nodeid_on() { nfault node-id; }
f_nodeid_off() { nfault; }

r_logresume() {
  local id pid a b
  id="$(submit -- python3 -c 'import time
for i in range(60):
    print("line %03d %s" % (i, "x" * 50), flush=True); time.sleep(0.15)')"
  wait_state "$id" running 60
  ws log "$id" -f > "$WORK/out/log-f.txt" 2> "$WORK/out/log-f.err" &
  pid=$!; sleep 3
  sudo pkill -u ws -f 'chisel client'; wait "$pid"
  a="$(sha256sum < "$WORK/out/log-f.txt" | cut -c1-16)"; b="$(nodelog "$id" | sha256sum | cut -c1-16)"
  M="joined pieces sha256 $a, node's log $b ($(wc -c < "$WORK/out/log-f.txt") bytes; $(grep -c resuming "$WORK/out/log-f.err") resume(s))"
  [ "$a" = "$b" ] && [ "$(grep -c resuming "$WORK/out/log-f.err")" -ge 1 ]
}
f_logzero_on() { WSF=log-from-zero; }
f_logzero_off() { WSF=""; }

r_fetchint() {
  local id out rc
  asws rm -rf "$WCWD/results"
  id="$(submit -- python3 job.py fetchint)"; wait_state "$id" "succeeded failed" 90
  sudo sh -c "echo tampered >> /srv/mu/jobs/$id/work/results/out.json"
  out="$(ws fetch "$id" --json 2>&1)"; rc=$?
  M="exit $rc ($(printf '%s' "$out" | jget error)); results/out.json in the project: $(asws test -e "$WCWD/results/out.json" && echo present || echo absent)"
  [ "$rc" = 8 ] && ! asws test -e "$WCWD/results/out.json"
}
f_hash_on() { WSF=no-fetch-hash; }
f_hash_off() { WSF=""; }

r_fetchinside() {
  local id out rc
  asws rm -f "$WCWD/outside.txt"
  id="$(submit -- python3 job.py inside)"; wait_state "$id" "succeeded failed" 90
  nfault fetch-extra-member
  out="$(ws fetch "$id" --json 2>&1)"; rc=$?
  nfault
  M="exit $rc ($(printf '%s' "$out" | jget error)); outside.txt in the project: $(asws test -e "$WCWD/outside.txt" && echo WRITTEN || echo absent)"
  [ "$rc" = 8 ] && ! asws test -e "$WCWD/outside.txt"
}
f_member_on() { WSF=no-member-check; }
f_member_off() { WSF=""; asws rm -f "$WCWD/outside.txt"; }

r_linkdrop() {
  local id out rc s a
  id="$(submit -- python3 -c 'import time; time.sleep(20); print("survived the link drop")')"
  [ -n "$id" ] || id="$(asws sh -c 'ls -t "$0"/.mu/jobs/*.json | head -1 | xargs basename | sed "s/.json$//"' "$WCWD")"
  wait_state "$id" running 60
  sudo systemctl stop mu-tunnel.service; sudo pkill -u ws -f 'chisel client'; sleep 6
  sudo systemctl start mu-tunnel.service; wait_up 90 >/dev/null
  wait_state "$id" "succeeded failed interrupted" 120
  s="$(jobstate "$id")"; a="$(jobstate "$id" attempt)"
  out="$(ws fetch "$id" --json 2>&1)"; rc=$?
  M="$s, attempt $a; fetch after the tunnel came back: exit $rc"
  [ "$s" = succeeded ] && [ "$a" = 1 ] && [ "$rc" = 0 ]
}
r_linkdrop_fault() {   # the submit itself holds the session (job-in-session): run it in the background
  nfault job-in-session
  ws submit --json -- python3 -c 'import time; time.sleep(20); print("survived the link drop")' > "$WORK/out/insession.json" 2>&1 &
  local bg=$! id; sleep 4
  id="$(asws sh -c 'ls -t "$0"/.mu/jobs/*.json | head -1 | xargs basename | sed "s/.json$//"' "$WCWD")"
  sudo systemctl stop mu-tunnel.service; sudo pkill -u ws -f 'chisel client'; sleep 6
  sudo systemctl start mu-tunnel.service; wait "$bg"; nfault; wait_up 90 >/dev/null
  wait_state "$id" "succeeded failed interrupted" 120
  local s a; s="$(jobstate "$id")"; a="$(jobstate "$id" attempt)"
  M="$s, attempt $a"
  [ "$s" = succeeded ] && [ "$a" = 1 ]
}

r_restart() {
  local id s a n
  id="$(submit -- python3 -c 'import time; time.sleep(12); print("done")')"
  wait_state "$id" running 60; sleep 2
  asmu systemctl --user restart mu-node.service
  wait_state "$id" "succeeded failed interrupted" 90
  s="$(jobstate "$id")"; a="$(jobstate "$id" attempt)"; n="$(nodelog "$id" | grep -c '^== μ job')"
  M="$s, attempt $a, started $n time(s)"
  [ "$s" = succeeded ] && [ "$a" = 1 ] && [ "$n" = 1 ]
}
f_adopt_on() { nfault no-adopt; }
f_adopt_off() { nfault; }

r_versions() {
  local p=/home/ws/oldpin out rc a b c
  newproj "$p" v2026.11.4
  out="$(WCWD="$p" ws submit --json -- true 2>&1)"; rc=$?
  a="exit $rc $(printf '%s' "$out" | jget error)"
  case "$(printf '%s' "$out" | jget next)" in *"mu install v2026.11.4"*) a="$a, says mu install v2026.11.4" ;; esac
  out="$(ws status --json 2>&1)"; rc=$?   # this project pins v2026.11.6, which has no job layer
  b="mu status in a project pinned to v2026.11.6: exit $rc"
  out="$(asmu sh -c 'cd / && /home/mu/.local/share/mu/bin/mu gc --yes' 2>&1)"
  c="after mu gc --yes the node has: $(sudo ls "$NODEH/toolchains" | tr '\n' ' ')"
  M="$a; $b; $c"
  case "$a" in "exit 3 toolchain-missing, says mu install v2026.11.4") ;; *) return 1 ;; esac
  [ "$rc" = 0 ] && sudo test -d "$NODEH/toolchains/v2026.11.6" && ! sudo test -d "$NODEH/toolchains/v2026.11.5"
}
restub_node() { sudo bash -s <<'S'
for v in v2026.11.5 v2026.11.6; do T=/home/mu/.local/share/mu/toolchains/$v; [ -d "$T" ] && continue
  mkdir -p "$T/recipe/bin" "$T/env/bin"; printf '{"version": "%s", "recipe_commit": "0", "installed": "x", "layers": []}\n' "$v" > "$T/MANIFEST.json"
  printf '#!/bin/bash\ncase "$1" in run) shift; exec "$@" ;; esac\n' > "$T/recipe/bin/mu-toolchain"; chmod 755 "$T/recipe/bin/mu-toolchain"
  ln -sfn /usr/bin/python3 "$T/env/bin/python"; chown -R mu:mu "$T"; done
S
}
f_pinreg_on() {
  restub_node
  sudo sed -i '\|/srv/mu/pins/|d' "$NODEH/projects.txt"; sudo chown mu:mu "$NODEH/projects.txt"
  nfault no-pin-register; submit -- true >/dev/null; sleep 1
}
f_pinreg_off() { nfault; restub_node; submit -- true >/dev/null; }
f_route_on() { restub_node; DF=route-by-pin; }
f_route_off() { DF=""; restub_node; }

r_limits() {
  local out rc
  out="$(mu_node node selftest --json 2>&1)"
  out="$(ws submit --json -- true 2>&1)"; rc=$?
  M="self-test failing: submit exit $rc ($(printf '%s' "$out" | jget error))"
  [ "$rc" = 6 ] && [ "$(printf '%s' "$out" | jget error)" = limits-unavailable ]
}
r_limits_case() { nfault selftest-fail; r_limits; local rc=$?; nfault; mu_node node selftest >/dev/null 2>&1; return $rc; }
r_limits_fault() { nfault selftest-fail skip-limits-check; r_limits; local rc=$?; nfault; mu_node node selftest >/dev/null 2>&1; return $rc; }

r_disklow() {
  asmu python3 -I -S -c 'import sys,os; sys.path.insert(0, sys.argv[1]); os.environ["MU_NODE_ROOT"]="/srv/mu"
from mu import node; node.update_node_info(min_free_gb=100000)' "$NODEH/toolchains/v2026.11.7/recipe/src"
  local out rc; out="$(ws submit --json -- true 2>&1)"; rc=$?
  asmu python3 -I -S -c 'import sys,os; sys.path.insert(0, sys.argv[1]); os.environ["MU_NODE_ROOT"]="/srv/mu"
from mu import node; node.update_node_info(min_free_gb=1)' "$NODEH/toolchains/v2026.11.7/recipe/src"
  M="min_free_gb 100000: submit exit $rc ($(printf '%s' "$out" | jget error)): $(printf '%s' "$out" | jget message)"
  [ "$rc" = 6 ] && [ "$(printf '%s' "$out" | jget error)" = disk-low ]
}
r_protocol() {
  local out rc; out="$(wssh mu-office-pc hello protocol=2 2>&1 < /dev/null)"; rc=$?
  M="hello protocol=2: exit $rc, $(printf '%s' "$out" | jget error): $(printf '%s' "$out" | jget message)"
  [ "$rc" = 65 ] && [ "$(printf '%s' "$out" | jget error)" = protocol-mismatch ]
}
r_fullcycle() {
  local id out rc
  asws rm -rf "$WCWD/results" "$WCWD/runs"
  id="$(submit -- python3 job.py "two words" three)"
  out="$(ws status "$id" --wait --json 2>&1)"; rc=$?
  local lg; lg="$(ws log "$id" 2>&1)"
  out="$(ws fetch "$id" --json 2>&1)"
  M="status --wait exit $rc; log: $(printf '%s' "$lg" | grep 'job ran'); fetch: $(printf '%s' "$out" | jget written); run record: $(asws test -f "$WCWD/runs/$id.json" && echo yes)"
  [ "$rc" = 0 ] && printf '%s' "$lg" | grep -q "job ran \['two words', 'three'\]" && asws test -f "$WCWD/results/out.json" && asws test -f "$WCWD/runs/$id.json"
}

rows() {
  run_row R00 "a whole job: submit, status --wait, log (argv as a list), fetch with a run record" r_fullcycle
  run_row R01 "host key pin: another key in known_hosts gives exit 5 and nothing reaches the node" r_hostkey
  run_row R02 "key required: an unauthorised key gives exit 5" r_keyreq
  run_row R03 "no shell: 'cat /etc/passwd' bad-character, 'id' unknown-verb, no command no-command" r_noshell
  run_row R04 "no terminal (ssh -tt), no forwarding (ssh -N -L)" r_notty
  run_row R06 "relay failures: password, fingerprint, port give exit 5 with the reason; node offline, relay down exit 4 within 5 s" r_relay
  run_row R07 "no relay password in any process's command line while calls run" r_nosecret
  run_row R10 "push with git-lfs hooks installed succeeds" r_lfs
  run_row R11 "snapshots are write-once: overwrite, delete and another ref name refused" r_writeonce
  run_row R12 "work folder equals the tree: export-ignore file present, export-subst unchanged" r_worktree
  run_row R13 "upload is incremental: one changed line grows the node's repositories by under 10 kB" r_incremental
  run_row R14 "durable result: the shim killed before result.json never gives succeeded; re-run once, then interrupted" r_durable_case
  run_row R15 "torn records: result.json / state.json full of NUL bytes read as absent, the job recovered from job.json" r_torn
  run_row R17 "memory limit: a job allocating twice its --mem ends as oom" r_memory
  run_row R18 "time limit and cancel: timeout and cancelled, and a grandchild process is gone" r_timecancel
  run_row R19 "queue: three submits run one at a time in order of acceptance" r_queue
  run_row R20 "repeat submit: a lost answer, then --resume: one job (duplicate: true)" r_repeat
  run_row R21 "log resume: the link killed during log -f; the pieces joined by offset equal the node's log" r_logresume
  run_row R22 "fetch integrity: an output changed on the node after the result gives exit 8 and is not written" r_fetchint
  run_row R23 "fetch stays inside: a tar member outside the declared outputs is refused" r_fetchinside
  run_row R24 "link drop: the tunnel killed during a job; the job finishes, status and fetch work after" r_linkdrop
  run_row R25 "runner restart during a job: adopted, finishes once" r_restart
  run_row R26 "version rules: missing pin exit 3; a registered pin survives mu gc --yes; job commands work in a project pinned without them" r_versions
  run_row R27 "limits unavailable: with the self-test failing, submit is exit 6" r_limits_case
  run_row R30 "disk low: below min_free_gb, submit is exit 6 (disk-low)" r_disklow
  run_row R31 "protocol mismatch: hello with protocol 2 is refused (65, protocol-mismatch)" r_protocol
}
faults() {
  must_fail R01 no-strict-hostkey f_strict_on f_strict_off r_hostkey
  must_fail R02 "key left in the key file" f_keep_on f_keep_off r_keyreq
  must_fail R03 "ForceCommand removed" f_force_on f_force_off r_noshell
  must_fail R04 "both locks removed (restrict and the sshd lines)" f_locks_on f_locks_off r_notty
  must_fail R06 bare-proxy f_bare_on f_bare_off r_relay
  must_fail R07 auth-in-argv f_argv_on f_argv_off r_nosecret
  must_fail R10 "push-hooks (no --no-verify)" f_hooks_on f_hooks_off r_lfs
  must_fail R11 no-update-hook f_hook_on f_hook_off r_writeonce
  must_fail R12 git-archive f_archive_on f_archive_off r_worktree
  must_fail R13 full-push f_full_on f_full_off r_incremental
  must_fail R14 trust-unit-success true true r_durable_fault
  must_fail R15 trust-existence f_torn_on f_torn_off r_torn
  must_fail R17 no-memory-max f_mem_on f_mem_off r_memory
  must_fail R18 killmode-process f_kill_on f_kill_off r_timecancel
  must_fail R19 start-on-submit f_queue_on f_queue_off r_queue
  must_fail R20 node-id f_nodeid_on f_nodeid_off r_repeat
  must_fail R21 log-from-zero f_logzero_on f_logzero_off r_logresume
  must_fail R22 no-fetch-hash f_hash_on f_hash_off r_fetchint
  must_fail R23 no-member-check f_member_on f_member_off r_fetchinside
  must_fail R24 job-in-session true true r_linkdrop_fault
  must_fail R25 no-adopt f_adopt_on f_adopt_off r_restart
  must_fail R26 no-pin-register f_pinreg_on f_pinreg_off r_versions
  must_fail R26 route-by-pin f_route_on f_route_off r_versions
  must_fail R27 skip-limits-check true true r_limits_fault
}

# ================================================================================================== the host script (H rows)
hstatus() { sudo mu-wsl2-distro status --no-wsl --offline --json 2>/dev/null | tail -n 1; }
failed_checks() { python3 -c 'import json,sys
d = json.loads(sys.stdin.read() or "{}")
print(" ".join("%s(%s)" % (c["name"], c["detail"][:90]) for c in d.get("checks", []) if c["result"] == "fail") or "none")'; }
h_status() {
  local j; j="$(hstatus)"; echo "$j" > "$WORK/out/host-status-$RANDOM.json"
  M="ok $(printf '%s' "$j" | jget ok); $(printf '%s' "$j" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d["checks"]), "checks")'); failed: $(printf '%s' "$j" | failed_checks)"
  [ "$(printf '%s' "$j" | jget ok)" = true ]
}
h_idempotent() {
  local c3 e3; c3="$(sed -n 's/^changed=//p' "$WORK/out/host/install-3.result")"; e3="$(sed -n 's/^exit=//p' "$WORK/out/host/install-3.result")"
  M="run 1: $(sed -n 's/^changed=//p' "$WORK/out/host/install-1.result") changes, run 2: $(sed -n 's/^changed=//p' "$WORK/out/host/install-2.result"), run 3: $c3 (exit $e3); run 3 says: $(tail -n 3 "$WORK/out/host/install-3.log" | tr '\n' ' ')"
  [ "$c3" = 0 ] && [ "$e3" = 0 ]
}
h_tunnel() {
  local j; j="$(sudo mu-wsl2-distro tunnel status --json)"; M="$(printf '%s' "$j" | jget state): $(printf '%s' "$j" | jget line); user $(printf '%s' "$j" | jget relay_user) port $(printf '%s' "$j" | jget relay_port)"
  [ "$(printf '%s' "$j" | jget ok)" = true ] && ! sudo grep -q "$(sed -n 's/^RELAY_PASSWORD=//p' "$S4W/env/compute.env")" "$WORK/out/host/tunnel-credential.log" "$WORK/logs/node-setup.log"
}
h_keys() {
  local k hk a; k="$(sudo mu-wsl2-distro keys --json)"; hk="$(sudo mu-wsl2-distro host-key --json)"; a="$(cat "$WORK/out/compute-add.json")"
  M="keys: $(printf '%s' "$k" | python3 -c 'import json,sys; print(" ".join(x["name"] + " " + x["fingerprint"] for x in json.load(sys.stdin)["keys"]))'); host key $(printf '%s' "$hk" | jget fingerprint), the workstation pinned $(printf '%s' "$a" | jget host_key_fingerprint)"
  [ "$(printf '%s' "$k" | python3 -c 'import json,sys; print(" ".join(x["name"] + " " + x["fingerprint"] for x in json.load(sys.stdin)["keys"]))')" = "avi-mac $(printf '%s' "$a" | jget key_fingerprint)" ] \
    && [ "$(printf '%s' "$hk" | jget fingerprint)" = "$(printf '%s' "$a" | jget host_key_fingerprint)" ] \
    && sudo grep -qx "restrict $(PUB) avi-mac" /etc/mu/authorized_keys/mu
}
h_updates() {   # what unattended-upgrades itself says it would take, on the runner (a dry run)
  local o; o="$(sudo unattended-upgrade --dry-run --debug 2>&1 | grep -m 1 -i 'allowed origins are' || true)"
  M="${o:-unattended-upgrade printed no 'Allowed origins' line}"
  [ -n "$o" ] && ! printf '%s' "$o" | grep -Eq -- '-updates|-backports|-proposed' && printf '%s' "$o" | grep -q -- '-security'
}
host_rows() {
  run_row H1 "the host script's status: every check passes (--no-wsl --offline)" h_status
  run_row H2 "install is idempotent: the third run changes nothing" h_idempotent
  run_row H3 "tunnel status: connected; the relay password in no output of the set-up" h_tunnel
  run_row H4 "keys, host-key: the workstation's key under its name, with restrict; the pinned host key is the node's" h_keys
  run_row H5 "unattended-upgrades takes the security pocket only (its own dry run)" h_updates
}
# the faults that status must notice, each put in and taken out again
hf_sshd_on() { sshd_edit 's/^PermitTTY no$/PermitTTY yes/'; }
hf_sshd_off() { sshd_restore; }
hf_gate_on() { sudo cp /usr/local/lib/mu/mu-node-gate "$WORK/gate.good"; echo '# changed' | sudo tee -a /usr/local/lib/mu/mu-node-gate > /dev/null; }
hf_gate_off() { sudo cp "$WORK/gate.good" /usr/local/lib/mu/mu-node-gate; }
hf_testfault_on() { echo nothing | sudo tee /etc/mu/test-fault > /dev/null; }
hf_testfault_off() { sudo rm -f /etc/mu/test-fault; }
hf_envmode_on() { sudo chmod 644 /etc/mu/tunnel.env; }
hf_envmode_off() { sudo chmod 640 /etc/mu/tunnel.env; }
hf_22_on() { sudo sh -c 'nohup python3 -m http.server 22 --bind 127.0.0.1 > /dev/null 2>&1 & echo $! > /tmp/mu-port22.pid'; sleep 1; }
hf_22_off() { sudo sh -c 'kill "$(cat /tmp/mu-port22.pid)"; rm -f /tmp/mu-port22.pid'; sleep 1; }
hf_tunnel_on() { sudo systemctl stop mu-tunnel.service; }
hf_tunnel_off() { sudo systemctl start mu-tunnel.service; wait_up 90 > /dev/null; }
host_faults() {
  must_fail H1 "PermitTTY yes in /etc/mu/sshd_config" hf_sshd_on hf_sshd_off h_status
  must_fail H1 "a line added to the gate" hf_gate_on hf_gate_off h_status
  must_fail H1 "/etc/mu/test-fault present" hf_testfault_on hf_testfault_off h_status
  must_fail H1 "tunnel.env readable by everyone (0644)" hf_envmode_on hf_envmode_off h_status
  must_fail H1 "something listening on port 22" hf_22_on hf_22_off h_status
  must_fail H1 "the tunnel stopped" hf_tunnel_on hf_tunnel_off h_status
}
# last: uninstall, then install again. The node's host key must survive, so the workstation's pin still holds
h_uninstall() {
  local fp0 fp1 left="" kept="" f u
  fp0="$(sudo ssh-keygen -lf /etc/mu/ssh_host_ed25519_key.pub | awk '{ print $2 }')"
  sudo mu-wsl2-distro uninstall > "$WORK/out/host/uninstall.log" 2>&1 || { M="uninstall failed: $(tail -n 5 "$WORK/out/host/uninstall.log" | tr '\n' ' ')"; return 1; }
  for f in /etc/systemd/system/mu-sshd.service /etc/systemd/system/mu-tunnel.service /etc/mu/sshd_config /etc/mu/tunnel.env \
           /etc/mu/authorized_keys/mu /usr/local/lib/mu/mu-node-gate /usr/local/lib/mu/mu-node-gate.in /usr/local/lib/mu/chisel \
           /etc/apt/apt.conf.d/52mu-unattended-upgrades /home/mu/.config/systemd/user/mu-node.service; do
    sudo test -e "$f" && left="$left $f"
  done
  id mu-tunnel > /dev/null 2>&1 && left="$left user:mu-tunnel"
  ss -ltnH | awk '{ print $4 }' | grep -q ':2222$' && left="$left listener:2222"
  for u in ssh.socket ssh.service; do [ "$(readlink "/etc/systemd/system/$u")" = /dev/null ] && left="$left masked:$u"; done
  [ "$(asmu systemctl --user is-active mu-node.service 2>/dev/null)" = active ] && left="$left runner-active"
  fp1="$(sudo ssh-keygen -lf /etc/mu/ssh_host_ed25519_key.pub 2>/dev/null | awk '{ print $2 }')"
  sudo test -d /srv/mu/jobs && kept="$kept /srv/mu/jobs"
  M="left behind:${left:- nothing}; host key $fp0 -> ${fp1:-gone}; kept:${kept:- (no job folders)}"
  [ -z "$left" ] && [ -n "$fp1" ] && [ "$fp0" = "$fp1" ] && [ -n "$kept" ]
}
h_reinstall() {
  local c rc=0
  sudo mu-wsl2-distro install --no-wsl > "$WORK/out/host/reinstall-1.log" 2>&1 || rc=$?
  [ "$rc" = 0 ] || { M="install after uninstall: exit $rc"; return 1; }
  sudo mu-wsl2-distro tunnel credential --wait 60 < "$S4W/env/compute.env" > "$WORK/out/host/reinstall-credential.log" 2>&1 || { M="tunnel credential: $(tail -n 2 "$WORK/out/host/reinstall-credential.log" | tr '\n' ' ')"; return 1; }
  authorize
  close_master
  if ! wait_up 90; then M="the workstation (its pin unchanged since S3) does not reach the reinstalled node: $(ws compute status office-pc --json 2>&1 | tail -n 1)"; return 1; fi
  sudo mu-wsl2-distro install --no-wsl > "$WORK/out/host/reinstall-2.log" 2>&1 || rc=$?
  c="$(sudo sed -n 's/^changed=//p' /run/mu-wsl2/install-result)"
  M="reinstalled; hello through the relay with the workstation's old pin: $(ws compute status office-pc --json | jget ok); install once more: $c changes (exit $rc)"
  [ "$c" = 0 ] && [ "$rc" = 0 ]
}
host_cycle() {
  run_row H6 "uninstall removes the job layer's units, files, user and masks; keeps the host key and the job folders" h_uninstall
  run_row H7 "install again, credential, authorize: the workstation's old pin still holds; then install changes nothing" h_reinstall
}

collect() {
  sudo journalctl -u mu-sshd -u mu-tunnel --no-pager > "$WORK/logs/system-units.log" 2>&1 || true
  sudo mu-wsl2-distro status --no-wsl --offline > "$WORK/logs/host-status.txt" 2>&1 || true
  sudo systemd-analyze security mu-tunnel.service --no-pager > "$WORK/logs/mu-tunnel-security.txt" 2>&1 || true
  sudo journalctl _UID="$MU_UID" --no-pager > "$WORK/logs/mu-user.log" 2>&1 || true
  sudo cp /srv/mu/audit.log "$WORK/logs/audit.log" 2>/dev/null || true
  asmu systemctl --user show-environment 2>&1 | sed 's/=.*//' > "$WORK/logs/mu-manager-env-names.txt" || true
  asmu systemctl --user status mu-node.service --no-pager > "$WORK/logs/mu-node-status.txt" 2>&1 || true
  sudo sh -c 'for d in /srv/mu/jobs/*/; do echo "== $d"; cat "$d/events.log" 2>/dev/null; done' > "$WORK/logs/events.log" 2>&1 || true
  sudo cp "$WSH/logs/compute-office-pc.log" "$WORK/logs/ws-chisel.log" 2>/dev/null || true
  docker logs "$(relay_ctr)" > "$WORK/logs/relay.log" 2>&1 || true
  sudo chown -R "$(id -u)" "$WORK/logs" 2>/dev/null || true
}
summary() {
  {
    echo "### μ job layer, end to end ($MODE)"; echo
    echo "ubuntu $(lsb_release -rs 2>/dev/null), $(systemctl --version | head -1), $(ssh -V 2>&1), $(git --version), python $(python3 -c 'import sys; print(sys.version.split()[0])'), Traefik ${TRAEFIK_VERSION:-3.7.16}"; echo
    echo "| | # | Check | Measured |"; echo "|---|---|---|---|"
    awk -F '\t' '{ gsub(/\|/, "/"); printf "| %s | %s | %s | %s |\n", $1, $2, $3, $4 }' "$WORK/results.tsv"
    echo; echo "$N_FAIL failed or missed."
  } > "$WORK/summary.md"
  cat "$WORK/summary.md"
}

case "$MODE" in
  probe) setup && probe ;;
  all) if setup; then probe; host_rows; rows; [ "${NO_FAULTS:-0}" = 1 ] || { faults; host_faults; }; host_cycle; fi ;;
  *) echo "usage: e2e.sh probe|all" >&2; exit 2 ;;
esac
collect; summary
[ "$N_FAIL" = 0 ]
