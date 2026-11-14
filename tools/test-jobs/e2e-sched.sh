# The scheduler's end-to-end rows (docs/design/scheduler.md §13, release 1), sourced by tools/test-jobs/e2e.sh for
#   tools/test-jobs/e2e.sh sched        set-up, then every row, then every row with its fault (must fail)
# on the same CI node: real systemd and cgroup v2, the relay replica, the workstation ws (key avi-mac, the node's
# owner: /etc/mu/owners from the pairing) and a second workstation ws2 (key ci-other), two projects of ws and one of
# ws2. node.json sched {cores: NC, mem_bytes: 2 GiB, check: true}: C = NC (the runner's CPUs, at most 4: a private
# repository's runner has 2), M = 2 GiB, and the runner checks the invariants after every pass (a violation goes to
# /srv/mu/state/invariant-violation; every row fails on one). HC = NC / 2: "half the node".
# Rows: Q01-Q14, Q18-Q20 (concurrency), Q22-Q28 (queue control), Q14B (the previous release's client against this
# node), V1 and V3 (a release installed and switched while jobs run, gc only when idle, mu compute update; there is no
# V2). Not here (unit level only: tools/test-jobs/unit_sched.py): Q15, Q16, Q17, Q21.

QLOG=/home/mu/q.log
SRC=/home/mu/.local/share/mu/toolchains/v2026.11.7/recipe/src
W2H=/home/ws2/.local/share/mu
P2=/home/ws/proj2; P3=/home/ws2/proj3
G1=1073741824
NC="$(nproc)"; [ "$NC" -le 4 ] || NC=4; HC=$((NC / 2))
ws2() {   # the second workstation (key ci-other), in $W2CWD
  sudo -u ws2 -H env -u XDG_CONFIG_HOME -u XDG_RUNTIME_DIR MU_POLL_S=1 MU_JOBS_FAULT="$WSF" sh -c 'cd "$0" && exec "$@"' \
    "${W2CWD:-$P3}" "$W2H/bin/mu" "$@"
}
submit2() { ws2 submit --json "$@" 2>&1 | tee -a "$WORK/logs/submits.log" | jget job_id; }
node_py() {   # node_py CODE: python as mu with this checkout's src/mu (node, durable, sched imported)
  asmu python3 -I -S -c 'import sys, os; sys.path.insert(0, sys.argv[1]); os.environ["MU_NODE_ROOT"] = "/srv/mu"
from mu import node, durable, sched
'"$1" "$SRC"
}
qcmd() {   # qcmd SECONDS: a job command that logs its start and end (with nanoseconds) and sleeps between
  printf 'echo start $MU_JOB_ID $(date +%%s.%%N) >> %s; sleep %s; echo end $MU_JOB_ID $(date +%%s.%%N) >> %s' "$QLOG" "$1" "$QLOG"
}
qt() { sudo awk -v w="$1" -v id="$2" '$1 == w && $2 == id { t = $3 } END { print t }' "$QLOG"; }   # the last one
qorder() { sudo awk '$1 == "start" { print $2 }' "$QLOG" | tr '\n' ' '; }
short() { local x; for x in "$@"; do printf '%s ' "${x: -6}"; done; }
jq_() { python3 -c "$1" 2>/dev/null; }
inv() { sudo cat /srv/mu/state/invariant-violation 2>/dev/null | tail -3 | tr '\n' ' '; }
unfinished() { sudo sh -c 'for d in /srv/mu/jobs/*/; do [ -f "$d/result.json" ] || basename "$d"; done' 2>/dev/null; }
drain() {   # every unfinished job cancelled, the node idle, the order file and the violation record gone; fails when a
            # job is left (a row run on a node that is not idle would show nothing about its fault)
  local i n
  for i in $(unfinished); do ws cancel "$i" --on office-pc --json >/dev/null 2>&1 & done; wait
  for n in $(seq 1 90); do [ -z "$(unfinished)" ] && break; sleep 1; done
  sudo rm -f /srv/mu/state/invariant-violation /srv/mu/queue.json
  sudo -u mu sh -c ": > $QLOG"
  [ -z "$(unfinished)" ]
}
qpre() { drain || { M="drain failed: jobs left unfinished: $(unfinished | tr '\n' ' ')"; return 1; }; }
poll() {   # poll SECONDS CMD...: run CMD every second until it succeeds (0) or SECONDS pass (1)
  local end=$(( $(date +%s) + $1 )); shift
  while [ "$(date +%s)" -le "$end" ]; do "$@" && return 0; sleep 1; done; return 1
}
qend() {   # qend RC: the row's result, failed by any invariant violation of the runner during it
  local rc="$1" v; v="$(inv)"
  [ -z "$v" ] || { M="$M · INVARIANT VIOLATED: $v"; rc=1; }
  return "$rc"
}
blocker() { submit --cores "$NC" --mem 256M --time 2m -- sleep "$1"; }   # holds every core
qorder_q() { ws queue --json 2>/dev/null | jq_ 'import json,sys
d = json.loads(sys.stdin.read().strip().splitlines()[-1])
print(" ".join(j["job_id"][-6:] for j in d.get("jobs", []) if j.get("state") in ("queued", "held")))'; }

sched_setup() {
  echo "== scheduler set-up: C = $NC, M = 2 GiB, invariant checks; ws2 (ci-other); projects"
  [ "$NC" -ge 2 ] || { row FAIL QS "scheduler set-up" "this runner has $NC CPU: concurrency needs 2"; return 1; }
  node_py 'node.update_node_info(sched={"cores": '"$NC"', "mem_bytes": 2 << 30, "check": True}, min_free_gb=1)'
  asmu systemctl --user restart mu-node.service; sleep 2
  id ws2 >/dev/null 2>&1 || sudo useradd -m -s /bin/bash ws2
  sudo rm -rf /home/ws2/.local; sudo mkdir -p /home/ws2/.local/share
  sudo cp -a "$WSH" "$W2H"
  sudo rm -rf "$W2H/compute/office-pc/id_ed25519" "$W2H/compute/office-pc/id_ed25519.pub" "$W2H/run" "$W2H/logs"
  sudo chown -R ws2:ws2 /home/ws2/.local
  sudo -u ws2 -H ssh-keygen -q -t ed25519 -N '' -C ci-other -f "$W2H/compute/office-pc/id_ed25519"
  # explicit modes: the runner's default ACLs can leave a new key 0644, which ssh will not use (as in mu compute add)
  sudo chmod 600 "$W2H/compute/office-pc/id_ed25519"; sudo chmod 644 "$W2H/compute/office-pc/id_ed25519.pub"
  sudo -u ws2 -H python3 -I -S -c 'import sys; sys.path.insert(0, sys.argv[1]); from mu import jobs
open(sys.argv[2], "w").write(jobs.ssh_config_text("office-pc", sys.argv[3]))' "$W2H/toolchains/v2026.11.7/recipe/src" \
    "$W2H/compute/office-pc/ssh_config" "$W2H"
  sudo sed -i 's/^workstation=.*/workstation=ci-other/' "$W2H/compute/office-pc/node.conf"
  sudo mu-wsl2-distro authorize ci-other "$(sudo cat "$W2H/compute/office-pc/id_ed25519.pub")" >/dev/null
  sudo -u ws2 -H git config --global user.name "ws2 test"; sudo -u ws2 -H git config --global user.email ws2@example.invalid
  sudo -u ws2 -H git config --global init.defaultBranch main
  sudo -u ws2 -H sh -c 'rm -rf "$0" && mkdir -p "$0" && cd "$0" && git init -q && printf "[mu]\nversion = \"v2026.11.6\"\n" > mu.toml && git add -A && git commit -qm first' "$P3"
  newproj "$P2" v2026.11.6
  cat > /tmp/mu-e2e-mask.py <<'PYM'
import os, sys, threading, time
def mask():
    return [ln.split()[1] for ln in open("/proc/thread-self/status") if ln.startswith("Cpus_allowed_list")][0]
seen = [("main", mask())]
def worker():
    if len(sys.argv) > 1 and sys.argv[1] == "widen":
        os.sched_setaffinity(0, set(range(256)))   # every CPU there is (bits beyond them are ignored)
    seen.append(("thread", mask()))
    time.sleep(12)
t = threading.Thread(target=worker); t.start(); time.sleep(1)
open("masks.txt", "w").write(" ".join(m for _, m in seen) + "\n")
t.join()
PYM
  chmod 644 /tmp/mu-e2e-mask.py; asws cp /tmp/mu-e2e-mask.py "$WCWD/mask.py"
  sudo -u mu sh -c ": > $QLOG"
  local h h2; h="$(ws compute status office-pc --json 2>&1)"; h2="$(W2CWD=$P3 ws2 compute status office-pc --json 2>&1)"
  printf '%s\n%s\n' "$h" "$h2" > "$WORK/logs/sched-setup-hello.json"
  if [ "$(printf '%s' "$h" | jget hello.capacity.cores)" = "$NC" ] && [ "$(printf '%s' "$h2" | jget hello.key)" = ci-other ]; then
    row PASS QS "scheduler set-up: C $NC, M 2 GiB; a second workstation key (ci-other) reaches the node" \
      "capacity $(printf '%s' "$h" | jget hello.capacity); features $(printf '%s' "$h" | jget hello.features); owners $(printf '%s' "$h" | jget hello.owners)"
  else
    row FAIL QS "scheduler set-up" "capacity $(printf '%s' "$h" | jget hello.capacity) (want $NC cores); ws2's key: $(printf '%s' "$h2" | jget hello.key) $(printf '%s' "$h2" | jget message)"; return 1
  fi
}

# ---------------------------------------------------------------------------------------------- concurrency (§13)
q01() {
  qpre || return 1; local a b sa ea sb eb
  a="$(submit --cores "$HC" --mem 512M --time 2m -- sh -c "$(qcmd 6)")"
  b="$(submit --cores "$HC" --mem 512M --time 2m -- sh -c "$(qcmd 6)")"
  wait_state "$a" "succeeded failed" 90; wait_state "$b" "succeeded failed" 90
  sa="$(qt start "$a")"; ea="$(qt end "$a")"; sb="$(qt start "$b")"; eb="$(qt end "$b")"
  M="a $(jobstate "$a") $sa..$ea; b $(jobstate "$b") $sb..$eb; grants $(jobstate "$a" grant.cpus) / $(jobstate "$b" grant.cpus)"
  [ "$(jobstate "$a")" = succeeded ] && [ "$(jobstate "$b")" = succeeded ] && [ -n "$sb" ] && lt "$sb" "$ea" && lt "$sa" "$eb"
  qend $?
}
q02() {
  qpre || return 1; local a b w f i
  a="$(submit --cores 1 --mem 1536M --time 2m -- sleep 15)"; wait_state "$a" running 60
  b="$(submit --cores 1 --mem 1536M --time 2m -- sleep 1)"
  for i in $(seq 1 15); do [ -n "$(ws status "$b" --json | jget waiting_for.resources.mem_bytes)" ] && break; sleep 1; done
  w="$(ws status "$b" --json | jget waiting_for.resources.mem_bytes)"; f="$(ws status "$b" --json | jget waiting_for.resources.free_cores)"
  M="a 1.5 GB job runs; a second one with M = 2 GiB: $(jobstate "$b"), waiting_for.resources.mem_bytes $w with $f core(s) free"
  [ "$(jobstate "$a")" = running ] && [ "$(jobstate "$b")" = queued ] && [ "$w" = 1610612736 ] && [ "${f:-0}" -ge 1 ]
  qend $?
}
q03() {
  qpre || return 1; local a b c w s
  a="$(submit --cores "$HC" --mem 256M --time 2m -- sh -c "$(qcmd 8)")"; b="$(submit --cores "$HC" --mem 256M --time 2m -- sh -c "$(qcmd 8)")"
  c="$(submit --cores "$HC" --mem 256M --time 2m -- sh -c "$(qcmd 1)")"
  wait_state "$a" running 60; wait_state "$b" running 60; sleep 2
  w="$(ws status "$c" --json | jget waiting_for.resources.cores)"; s="$(jobstate "$c")"
  wait_state "$c" "succeeded failed" 90
  M="the third while two run: $s, waiting for $w cores; then $(jobstate "$c"), started $(qt start "$c") after a ended $(qt end "$a")"
  [ "$s" = queued ] && [ "$w" = "$HC" ] && [ "$(jobstate "$c")" = succeeded ] && ! lt "$(qt start "$c")" "$(qt end "$a")"
  qend $?
}
q04() {
  qpre || return 1; local a h b c bc cr hs
  a="$(submit --cores "$HC" --mem 256M --time 3m -- sleep 60)"; wait_state "$a" running 60
  h="$(submit --cores "$NC" --mem 256M --time 2m -- sleep 1)"
  b="$(submit --cores "$HC" --mem 256M --time 10s -- sleep 2)"
  c="$(submit --cores "$HC" --mem 256M --time 1h -- sleep 2)"
  wait_state "$b" "succeeded failed" 30   # B ends: its cores are free, and C (1 h) would fit them now
  local i cs=queued; for i in 1 2 3 4 5 6; do cs="$(jobstate "$c")"; [ "$cs" = queued ] || break; sleep 1; done
  bc="$(jobstate "$b" grant.class)"; cr="$(ws status "$c" --json | jget waiting_for.reservation.head)"
  hs="$(jobstate "$h" starts_by)"
  M="head $(jobstate "$h") starts_by $hs; B $(jobstate "$b") class $bc; C $cs for 6 s after B ended (cores free), reservation for ${cr: -6}"
  [ "$(jobstate "$h")" = queued ] && [ -n "$hs" ] && [ "$bc" = backfilled ] && [ "$(jobstate "$b")" = succeeded ] && \
    [ "$cs" = queued ] && [ "$cr" = "$h" ]
  qend $?
}
q05() {
  qpre || return 1; local a h sb i n=0 hst
  a="$(submit --cores "$HC" --mem 256M --time 40s -- sleep 25)"; wait_state "$a" running 60
  h="$(submit --cores "$NC" --mem 256M --time 1m -- sleep 1)"; sleep 2
  sb="$(jobstate "$h" starts_by)"
  for i in $(seq 1 18); do submit --cores "$HC" --mem 256M --time 20s -- sleep 15 >/dev/null; n=$((n + 1)); sleep 1; done
  wait_state "$h" "succeeded failed" 150
  hst="$(jobstate "$h" started_at)"
  M="head's first starts_by $sb; it started $hst ($(jobstate "$h")); $n short jobs submitted meanwhile"
  [ -n "$sb" ] && [ "$(jobstate "$h")" = succeeded ] && [ "$(python3 -c 'import sys,calendar,time
f = lambda s: calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))
print(int(f(sys.argv[1]) <= f(sys.argv[2]) + 1))' "$hst" "$sb")" = 1 ]
  qend $?
}
q06() {
  qpre || return 1; local w e q1 q2 q3 q1s ca es q3s
  w="$(submit -- sleep 120)"   # whole node; idle CPUs, so a quick job without its quota would take more than one
  wait_state "$w" running 60; sleep 2
  e="$(submit --cores 1 --mem 256M --time 1m -- true)"
  q1="$(submit --quick -- sh -c 'for i in 1 2 3 4; do timeout 20 sh -c "while :; do :; done" & done; wait; exit 0')"
  q2="$(submit --quick -- sleep 40)"
  q3="$(submit --quick -- true)"
  wait_state "$q1" running 30; wait_state "$q2" running 30; sleep 1
  q1s="$(jobstate "$q1")"; es="$(jobstate "$e")"; q3s="$(jobstate "$q3")"
  ws cancel "$w" --json >/dev/null   # an ordinary core frees while both lane slots are still taken
  wait_state "$q3" "succeeded failed" 60; wait_state "$e" "succeeded failed" 60; wait_state "$q1" "succeeded failed" 60
  ca="$(jobstate "$q1" usage.cores_avg)"
  M="beside a whole-node job, seconds after submit: quick $q1s (class $(jobstate "$q1" grant.class)), then $(jobstate "$q1") with cores_avg $ca (4 busy threads); explicit 1-core job $es; 3rd quick $q3s, then class $(jobstate "$q3" grant.class)"
  [ "$q1s" = running ] && [ "$(jobstate "$q1")" = succeeded ] && [ "$(jobstate "$q1" grant.class)" = express ] && \
    [ -n "$ca" ] && lt "$ca" 1.25 && [ "$es" = queued ] && [ "$q3s" = queued ] && [ "$(jobstate "$q3" grant.class)" = normal ]
  qend $?
}
q07() {
  qpre || return 1; local a b ma mb ga gb wa wb
  a="$(submit --cores "$HC" --mem 256M --time 1m -- python3 mask.py)"
  b="$(submit --cores "$HC" --mem 256M --time 1m -- python3 mask.py widen)"
  wait_state "$a" "succeeded failed" 90; wait_state "$b" "succeeded failed" 90
  ma="$(sudo cat "/srv/mu/jobs/$a/work/masks.txt" 2>/dev/null)"; mb="$(sudo cat "/srv/mu/jobs/$b/work/masks.txt" 2>/dev/null)"
  ga="$(jobstate "$a" grant.cpus)"; gb="$(jobstate "$b" grant.cpus)"
  wa="$(jobstate "$a" affinity_widened)"; wb="$(jobstate "$b" affinity_widened)"
  M="a granted $ga, its threads $ma; b granted $gb, its threads $mb; widened: a ${wa:-no}, b ${wb:-no}"
  [ "$ma" = "$ga $ga" ] && [ "${mb%% *}" = "$gb" ] && [ -n "$ga" ] && [ "$ga" != "$gb" ] && [ -z "$wa" ] && \
    printf '%s' "$wb" | grep -q python
  qend $?
}
q08() {
  qpre || return 1; local a e c
  a="$(submit --cores "$HC" --mem 256M --time 1m -- sh -c 'env > env.txt')"; wait_state "$a" "succeeded failed" 60
  e="$(sudo cat "/srv/mu/jobs/$a/work/env.txt" 2>/dev/null)"; c="$(jobstate "$a" grant.cpus)"
  g() { printf '%s\n' "$e" | sed -n "s/^$1=//p"; }
  M="OMP $(g OMP_NUM_THREADS) OPENBLAS $(g OPENBLAS_NUM_THREADS) PYTHON_CPU_COUNT $(g PYTHON_CPU_COUNT) CCX $(g CCX_NPROC_EQUATION_SOLVER) MU_JOB_CPUS $(g MU_JOB_CPUS) (grant $c) TMPDIR $(g TMPDIR)"
  [ "$(g OMP_NUM_THREADS)" = "$HC" ] && [ "$(g OPENBLAS_NUM_THREADS)" = "$HC" ] && [ "$(g PYTHON_CPU_COUNT)" = "$HC" ] && \
    [ "$(g CCX_NPROC_EQUATION_SOLVER)" = "$HC" ] && [ "$(g MU_JOB_CPUS)" = "$c" ] && [ "$(g TMPDIR)" = "/srv/mu/jobs/$a/tmp" ]
  qend $?
}
q09() {
  qpre || return 1; local a b
  a="$(submit --cores 1 --mem 512M --time 2m -- python3 -c "import time; b = b'x' * (1536 << 20); time.sleep(2)")"
  b="$(submit --cores 1 --mem 1G --time 2m -- python3 -c "import time; b = b'x' * (900 << 20); time.sleep(12)")"
  wait_state "$a" "succeeded failed oom" 90; wait_state "$b" "succeeded failed oom" 90
  M="A (512M, allocates 1.5G): $(jobstate "$a"); B (1G, holds 900M): $(jobstate "$b"), oom_kill $(jobstate "$b" usage.mem_events.oom_kill)"
  [ "$(jobstate "$a")" = oom ] && [ "$(jobstate "$b")" = succeeded ] && [ "$(jobstate "$b" usage.mem_events.oom_kill)" = 0 ]
  qend $?
}
q10() {
  qpre || return 1; local x d n ok=1 first i ids=""
  for i in $(seq 1 "$NC"); do ids="$ids $(submit --cores 1 --mem 256M --time 2m -- sh -c "$(qcmd 20)")"; done
  for x in $ids; do wait_state "$x" running 60; done; sleep 2
  d="$(submit --cores 1 --mem 256M --time 1m -- sh -c "$(qcmd 1)")"; sleep 2
  asmu systemctl --user kill -s KILL mu-node.service; sleep 4
  for x in $ids $d; do wait_state "$x" "succeeded failed interrupted" 120; done
  M=""; for x in $ids; do n="$(nodelog "$x" | grep -c '^== μ job')"
    M="$M ${x: -6} $(jobstate "$x") attempt $(jobstate "$x" attempt) started $n;"
    { [ "$(jobstate "$x")" = succeeded ] && [ "$(jobstate "$x" attempt)" = 1 ] && [ "$n" = 1 ]; } || ok=0; done
  first="$(for x in $ids; do qt end "$x"; done | sort -n | head -1)"
  M="$M a 1-core job queued on the full node started $(qt start "$d"), the first running one ended $first"
  [ "$ok" = 1 ] && [ "$(jobstate "$d")" = succeeded ] && ! lt "$(qt start "$d")" "$first"
  qend $?
}
q11() {
  qpre || return 1; local a b d gp alive i ids=""
  b="$(submit --cores 1 --mem 256M --time 2m -- sh -c 'trap "" TERM; sleep 600 & echo $! > gc.pid; while :; do sleep 1; done')"
  for i in $(seq 2 "$NC"); do ids="$ids $(submit --cores 1 --mem 256M --time 2m -- sleep 25)"; done
  for x in $b $ids; do wait_state "$x" running 60; done
  d="$(submit --cores 1 --mem 256M --time 1m -- sh -c "$(qcmd 1)")"; sleep 3
  ws cancel "$b" --json >/dev/null; wait_state "$b" "cancelled failed" 40
  wait_state "$d" "succeeded failed" 60
  gp="$(sudo cat "/srv/mu/jobs/$b/work/gc.pid" 2>/dev/null)"; alive=""; [ -n "$gp" ] && sudo kill -0 "$gp" 2>/dev/null && alive=yes
  a="${ids%% *}"; a="${ids## }"; a="${a%% *}"
  M="the job that ignores SIGTERM: $(jobstate "$b") at $(jobstate "$b" ended_at) (grandchild alive: ${alive:-no}); the others: $(for x in $ids; do jobstate "$x"; done | tr '\n' ' '); the queued job $(jobstate "$d") at $(jobstate "$d" started_at)"
  [ "$(jobstate "$b")" = cancelled ] && [ -z "$alive" ] && [ -n "$gp" ] && [ "$(jobstate "$d")" = succeeded ] && \
    { [ -z "$a" ] || [ "$(jobstate "$a")" = running ] || [ "$(jobstate "$a")" = succeeded ]; }
  qend $?
}
q12() {
  qpre || return 1; local s0 s1 o1 o2 o3 r1 r2 r3 h hs
  s0="$(sudo grep -cE 'verb=submit|receive-pack' /srv/mu/audit.log)"
  o1="$(ws submit --json --cores 64 -- true 2>&1)"; r1=$?
  o2="$(ws submit --json --mem 1T -- true 2>&1)"; r2=$?
  s1="$(sudo grep -cE 'verb=submit|receive-pack' /srv/mu/audit.log)"
  o3="$(WSF=no-capacity-check ws submit --json --cores 64 -- true 2>&1)"; r3=$?
  h="$(submit --cores "$NC" --mem 256M --time 1m --hold -- true)"
  node_py 'node.update_node_info(sched=dict(node.node_info()["sched"], cores='"$HC"'))'
  ws release "$h" --json >/dev/null; wait_state "$h" "failed succeeded" 30; hs="$(jobstate "$h"): $(jobstate "$h" note)"
  node_py 'node.update_node_info(sched=dict(node.node_info()["sched"], cores='"$NC"'))'
  M="--cores 64: exit $r1 $(printf '%s' "$o1" | jget error) ($(printf '%s' "$o1" | jget next)); --mem 1T: exit $r2 $(printf '%s' "$o2" | jget error); submits/pushes at the node $s0 -> $s1; past the client: exit $r3 $(printf '%s' "$o3" | jget error); capacity lowered after submit: $hs"
  [ "$r1" = 2 ] && [ "$(printf '%s' "$o1" | jget error)" = too-big ] && [ "$r2" = 2 ] && [ "$s0" = "$s1" ] && \
    [ "$r3" = 2 ] && [ "$(printf '%s' "$o3" | jget error)" = too-big ] && case "$hs" in "failed: too-big"*) true ;; *) false ;; esac
  qend $?
}
q13() {
  qpre || return 1; local z x ok=1 zs i ids=""
  local cmd='[ ! -e "$TMPDIR/marker" ] || exit 3; touch "$TMPDIR/marker"; '"$(qcmd 15)"
  for i in $(seq 1 "$NC"); do ids="$ids $(submit --cores 1 --mem 256M --time 2m -- sh -c "$cmd")"; done
  for x in $ids; do wait_state "$x" running 60; done; sleep 2
  z="$(submit --cores "$NC" --mem 256M --time 1m -- sh -c "$(qcmd 1)")"; sleep 2
  asmu systemctl --user stop mu-node.service
  for x in $ids; do asmu systemctl --user stop "mu-job-$x.service"; done
  asmu systemctl --user start mu-node.service
  for x in $ids $z; do wait_state "$x" "succeeded failed interrupted" 120; done
  zs="$(qt start "$z")"; M="z (the whole node, queued before the stop) started $zs;"
  for x in $ids; do M="$M ${x: -6} $(jobstate "$x") attempt $(jobstate "$x" attempt) restarted $(qt start "$x");"
    { [ "$(jobstate "$x")" = succeeded ] && [ "$(jobstate "$x" attempt)" = 2 ] && lt "$(qt start "$x")" "$zs"; } || ok=0; done
  [ "$ok" = 1 ] && [ "$(jobstate "$z")" = succeeded ]
  qend $?
}
q14() {
  qpre || return 1; local a n
  a="$(WSF=old-workstation submit --cores 1 --mem 256M --time 1m -- sh -c 'echo $MU_JOB_CORES > n.txt')"
  wait_state "$a" "succeeded failed" 60; n="$(sudo cat "/srv/mu/jobs/$a/work/n.txt" 2>/dev/null)"
  M="a spec without sched, --cores 1: granted $(jobstate "$a" grant.cores) cores ($(jobstate "$a" grant.cpus)), MU_JOB_CORES $n"
  [ "$(jobstate "$a" grant.cores)" = "$NC" ] && [ "$n" = 1 ]
  qend $?
}
q18() {
  qpre || return 1; local x y q why
  x="$(submit --cores 1 --mem 256M --time 2m -- sleep 40)"; wait_state "$x" running 60
  asmu systemctl --user stop mu-node.service
  node_py 'p = "/srv/mu/jobs/'"$x"'/state.json"; st = durable.read_record(p); st.pop("grant", None); durable.write_record(p, st)'
  asmu systemctl --user start mu-node.service; sleep 2
  y="$(submit --cores 1 --mem 256M --time 1m -- true)"
  q="$(submit --quick -- true)"; wait_state "$q" "succeeded failed" 30; sleep 1
  why="$(jobstate "$y" starts_by_why)"
  M="a running job without a grant: a 1-core job $(jobstate "$y") (starts_by '$(jobstate "$y" starts_by)': $why); a --quick job $(jobstate "$q")"
  [ "$(jobstate "$y")" = queued ] && [ -z "$(jobstate "$y" starts_by)" ] && printf '%s' "$why" | grep -q "previous release" && \
    [ "$(jobstate "$q")" = succeeded ]
  local rc=$?; ws cancel "$x" --json >/dev/null; wait_state "$y" "succeeded failed" 60
  M="$M; after its cancel: $(jobstate "$y")"
  [ "$rc" = 0 ] && [ "$(jobstate "$y")" = succeeded ]
  qend $?
}
q19() {
  qpre || return 1; local a h t j sb sb2 t2
  a="$(submit --cores "$NC" --mem 256M --time 1m -- sleep 25)"; wait_state "$a" running 60
  h="$(submit --cores "$HC" --mem 1G --time 1m -- true)"; sleep 2
  t="$(ws status "$h" 2>&1)"; sb="$(jobstate "$h" starts_by)"
  node_py 'node.update_node_info(min_free_gb=100000)'; sleep 2
  sb2="$(jobstate "$h" starts_by)"; t2="$(ws status "$h" 2>&1)"
  node_py 'node.update_node_info(min_free_gb=1)'
  M="text: $t · starts_by $sb · disk low: starts_by '${sb2}', text: $t2"
  printf '%s' "$t" | grep -q "waiting for $HC cores / 1.0 GB" && [ -n "$sb" ] && [ -z "$sb2" ] && printf '%s' "$t2" | grep -q "disk low"
  qend $?
}
q20() {
  qpre || return 1; local s o t f
  s="$(submit --cores 1 --mem 512M --time 1m -- python3 -c 'import time
b = b"x" * (200 << 20); t = time.time()
while time.time() - t < 8: pass')"
  o="$(submit --cores 1 --mem 128M --time 1m -- python3 -c 'import time; time.sleep(8); b = b"x" * (512 << 20)')"
  t="$(submit --cores 1 --mem 256M --time 9s -- sleep 60)"
  for x in $s $o $t; do wait_state "$x" "succeeded failed oom timeout" 90; done
  f="$(ws compute status office-pc --json | jget hello.features)"
  M="shim: $(jobstate "$s") $(jobstate "$s" usage); oom: $(jobstate "$o") $(jobstate "$o" usage.source) peak $(jobstate "$o" usage.mem_peak_bytes); timeout: $(jobstate "$t") $(jobstate "$t" usage.source) cpu $(jobstate "$t" usage.cpu_s); features $f"
  [ "$(jobstate "$s" usage.source)" = cgroup ] && lt 5 "$(jobstate "$s" usage.cpu_s)" && lt 200000000 "$(jobstate "$s" usage.mem_peak_bytes)" && \
    [ -n "$(jobstate "$s" usage.mem_work_peak_bytes)" ] && [ "$(jobstate "$o")" = oom ] && [ "$(jobstate "$o" usage.source)" = runner-sampled ] && \
    [ "$(jobstate "$t")" = timeout ] && [ "$(jobstate "$t" usage.source)" = runner-sampled ] && [ -n "$(jobstate "$t" usage.cpu_s)" ] && \
    printf '%s' "$f" | grep -q '"usage"'
  qend $?
}

# ---------------------------------------------------------------------------------------------- queue control (§20)
q22() {
  qpre || return 1; local r a b c d e o1 o2 o3 qo seq es ec
  r="$(blocker 35)"; wait_state "$r" running 60
  a="$(submit --cores "$NC" --mem 256M --time 1m -- sh -c "$(qcmd 1)")"; b="$(submit --cores "$NC" --mem 256M --time 1m -- sh -c "$(qcmd 1)")"
  c="$(submit --cores "$NC" --mem 256M --time 1m -- sh -c "$(qcmd 1)")"; d="$(submit --cores "$NC" --mem 256M --time 1m -- sh -c "$(qcmd 1)")"
  o1="$(ws hold "$b" --json)"; o2="$(ws hold "$b" --json)"; o3="$(ws release "$b" --json)"
  qo="$(qorder_q)"
  ws hold "$c" --json >/dev/null
  e="$(submit --cores 1 --mem 256M --time 1m --hold -- true)"; es="$(jobstate "$e")"
  ws cancel "$e" --json >/dev/null; ec="$(jobstate "$e")"
  wait_state "$d" "succeeded failed" 120
  ws release "$c" --json >/dev/null; wait_state "$c" "succeeded failed" 60
  seq="$(qorder)"
  M="hold: changed $(printf '%s' "$o1" | jget changed), again $(printf '%s' "$o2" | jget changed); release: place $(printf '%s' "$o3" | jget place); order $(short $qo); --hold at submit: $es, cancelled: $ec; start order $(short $seq) (want $(short $a $b $d $c))"
  [ "$(printf '%s' "$o1" | jget changed)" = true ] && [ "$(printf '%s' "$o2" | jget changed)" = false ] && \
    [ "$(printf '%s' "$o3" | jget place)" = 2 ] && [ "$qo" = "$(short $a $b $c $d | sed 's/ $//')" ] && [ "$es" = held ] && \
    [ "$ec" = cancelled ] && [ "$seq" = "$a $b $d $c " ]
  qend $?
}
q23() {
  qpre || return 1; local r a b c d m1 m2 rn qo seq
  r="$(blocker 30)"; wait_state "$r" running 60
  a="$(submit --cores "$NC" --mem 256M --time 1m -- sh -c "$(qcmd 1)")"; b="$(submit --cores "$NC" --mem 256M --time 1m -- sh -c "$(qcmd 1)")"
  c="$(submit --cores "$NC" --mem 256M --time 1m -- sh -c "$(qcmd 1)")"; d="$(submit --cores "$NC" --mem 256M --time 1m -- sh -c "$(qcmd 1)")"
  m1="$(ws move "$d" --before "$b" --json)"; m2="$(ws move "$d" --before "$b" --json)"
  qo="$(qorder_q)"
  rn="$(ws move "$a" --before "$r" --json 2>&1)"; local rr=$?
  wait_state "$c" "succeeded failed" 120; seq="$(qorder)"
  M="move D --before B: place $(printf '%s' "$m1" | jget place), again changed $(printf '%s' "$m2" | jget changed); queue $(short $qo); --before a running job: exit $rr $(printf '%s' "$rn" | jget error); start order $(short $seq)"
  [ "$qo" = "$(short $a $d $b $c | sed 's/ $//')" ] && [ "$(printf '%s' "$m2" | jget changed)" = false ] && [ "$rr" = 2 ] && \
    [ "$(printf '%s' "$rn" | jget error)" = reference-not-queued ] && [ "$seq" = "$a $d $b $c " ]
  qend $?
}
q24() {
  qpre || return 1; local r n1 n2 h mv seq
  r="$(blocker 25)"; wait_state "$r" running 60
  n1="$(submit --cores "$NC" --mem 256M --time 1m -- sh -c "$(qcmd 1)")"; n2="$(submit --cores "$NC" --mem 256M --time 1m -- sh -c "$(qcmd 1)")"
  h="$(submit --cores "$NC" --mem 256M --time 1m --priority high --others -- sh -c "$(qcmd 1)")"
  mv="$(ws move "$n2" --front --json)"
  wait_state "$n1" "succeeded failed" 120; seq="$(qorder)"
  M="the high job submitted last; move N2 --front: higher_bands_ahead $(printf '%s' "$mv" | jget higher_bands_ahead); start order $(short $seq) (want $(short $h $n2 $n1))"
  [ "$(printf '%s' "$mv" | jget higher_bands_ahead)" = 1 ] && [ "$seq" = "$h $n2 $n1 " ]
  qend $?
}
q25() {
  qpre || return 1; local r a b c n0 n1 bs seq
  r="$(blocker 30)"; wait_state "$r" running 60
  a="$(submit --cores "$NC" --mem 256M --time 1m -- sh -c "$(qcmd 1)")"; b="$(submit --cores "$NC" --mem 256M --time 1m -- sh -c "$(qcmd 1)")"
  c="$(submit --cores "$NC" --mem 256M --time 1m -- sh -c "$(qcmd 1)")"
  ws hold "$b" --json >/dev/null; ws move "$c" --front --json >/dev/null
  n0="$(sudo grep -c 'order file unreadable' /srv/mu/audit.log)"
  sudo sh -c 'sz=$(stat -c %s /srv/mu/queue.json); head -c "$sz" /dev/zero > /srv/mu/queue.json'
  sudo rm -f "/srv/mu/jobs/$b/state.json"
  wait_state "$a" "succeeded failed" 120; wait_state "$c" "succeeded failed" 30
  n1="$(sudo grep -c 'order file unreadable' /srv/mu/audit.log)"; bs="$(jobstate "$b")"; seq="$(qorder)"
  M="queue.json zeroed, B's state.json removed: B $bs; start order $(short $seq) (FIFO within the band: $(short $a $c)); journal lines $n0 -> $n1"
  [ "$bs" = held ] && [ "$seq" = "$a $c " ] && [ "$((n1 - n0))" = 1 ]
  qend $?
}
q26() {
  qpre || return 1; local r o x h1 h2 mv n0 n1 hp oh rl ur w1 w2 ho
  r="$(blocker 40)"; wait_state "$r" running 60
  o="$(submit --cores "$NC" --mem 256M --time 1m -- true)"
  x="$(W2CWD=$P3 submit2 --cores "$NC" --mem 256M --time 1m -- true)"
  h1="$(W2CWD=$P3 ws2 hold "$o" --json 2>&1)"; local e1=$?
  h2="$(W2CWD=$P3 ws2 hold "$x" --json 2>&1)"; local e2=$?; W2CWD=$P3 ws2 release "$x" --json >/dev/null
  mv="$(W2CWD=$P3 ws2 move "$x" --front --json 2>&1)"; local e3=$?
  n0="$(sudo ls /srv/mu/jobs | wc -l)"
  hp="$(W2CWD=$P3 ws2 submit --json --priority high --others --cores 1 -- true 2>&1)"; local e4=$?
  n1="$(sudo ls /srv/mu/jobs | wc -l)"
  oh="$(ws hold "$x" --others --json 2>&1)"; local e5=$?
  rl="$(W2CWD=$P3 ws2 release "$x" --json 2>&1)"; local e6=$?
  ur="$(ws priority "$x" urgent --others --json 2>&1)"; local e7=$?
  sudo mv /etc/mu/owners /etc/mu/owners.saved
  w1="$(ws compute status office-pc --json | jget hello.warnings)"; w2="$(ws queue --json | jget warnings)"
  ho="$(ws release "$x" --others --json 2>&1)"; local e8=$?
  sudo mv /etc/mu/owners.saved /etc/mu/owners
  M="ci-other: hold avi-mac's job exit $e1 $(printf '%s' "$h1" | jget error); hold its own exit $e2; move ahead of avi-mac's exit $e3 $(printf '%s' "$mv" | jget error); submit --priority high exit $e4 $(printf '%s' "$hp" | jget error), jobs $n0 -> $n1; owner holds it (--others) exit $e5; ci-other releases that exit $e6; owner urgent exit $e7; no owners file: warnings $([ -n "$w1" ] && [ "$w1" != "[]" ] && echo yes) $([ -n "$w2" ] && [ "$w2" != "[]" ] && echo yes), owner's release exit $e8"
  [ "$e1" = 5 ] && [ "$(printf '%s' "$h1" | jget error)" = not-allowed ] && [ "$e2" = 0 ] && [ "$e3" = 5 ] && [ "$e4" = 5 ] && \
    [ "$n0" = "$n1" ] && [ "$e5" = 0 ] && [ "$e6" = 5 ] && [ "$e7" = 0 ] && [ "$w1" != "[]" ] && [ -n "$w1" ] && [ "$w2" != "[]" ] && \
    [ -n "$w2" ] && [ "$e8" = 5 ] && [ "$(jobstate "$o")" = queued ]
  qend $?
}
q27() {
  qpre || return 1; local r b a c ql n qtext h1 h2 m1 m2 al
  r="$(blocker 30)"; wait_state "$r" running 60
  b="$(WCWD=$P2 submit --cores "$NC" --mem 256M --time 1m -- true)"
  a="$(submit --cores "$NC" --mem 256M --time 1m -- true)"
  c="$(W2CWD=$P3 submit2 --cores "$NC" --mem 256M --time 1m -- true)"
  ql="$(ws queue --json)"; qtext="$(ws queue 2>&1)"
  n="$(printf '%s' "$ql" | jq_ 'import json,sys
d = json.loads(sys.stdin.read().strip().splitlines()[-1])
print(len([j for j in d["jobs"] if all(k in j for k in ("key", "project_id", "place", "waiting_for", "starts_by")) or j["state"] == "running"]), len({j["key"] for j in d["jobs"]}), len({j["project_id"] for j in d["jobs"]}))')"
  h1="$(ws hold "$b" --json 2>&1)"; local e1=$?
  m1="$(ws move "$a" --front --json 2>&1)"; local e2=$?
  h2="$(ws hold "$b" --others --json 2>&1)"; local e3=$?
  m2="$(ws move "$a" --front --others --json 2>&1)"; local e4=$?
  al="$(sudo grep -c "queue: refused hold id=$b by=avi-mac from=" /srv/mu/audit.log) $(sudo grep -c "queue: hold id=$b.* others=1" /srv/mu/audit.log)"
  M="queue: $n (jobs with all fields, keys, projects); text lists $(printf '%s' "$qtext" | grep -c -e "${a: -6}" -e "${b: -6}" -e "${c: -6}"); hold of another project's job: exit $e1 $(printf '%s' "$h1" | jget error); move ahead of it: exit $e2 $(printf '%s' "$m1" | jget error); with --others: $e3 $e4; audit lines (refused, others=1): $al"
  [ "$n" = "4 2 3" ] && [ "$e1" = 2 ] && [ "$(printf '%s' "$h1" | jget error)" = other-project ] && [ "$e2" = 2 ] && \
    [ "$e3" = 0 ] && [ "$e4" = 0 ] && [ "$al" = "1 1" ]
  qend $?
}
prev_release() {   # the previous release's src/mu, installed on the node as μ v2026.11.9 (its git tag)
  sudo test -f /home/mu/.local/share/mu/toolchains/v2026.11.9/MANIFEST.json && return 0
  local d; d="$(mktemp -d)"
  git -C "$ROOT" fetch -q --depth 1 origin "+refs/tags/v2026.11.9:refs/tags/v2026.11.9" 2>&1 | tail -2
  git -C "$ROOT" archive v2026.11.9 src/mu | tar -x -C "$d" || return 1
  sudo bash -s "$d" <<'S'
T=/home/mu/.local/share/mu/toolchains/v2026.11.9
mkdir -p "$T/recipe/bin" "$T/env/bin" "$T/recipe/src"; cp -R "$1/src/mu" "$T/recipe/src/mu"
printf '{"version": "v2026.11.9", "recipe_commit": "0", "installed": "x", "layers": []}\n' > "$T/MANIFEST.json"
printf '#!/bin/bash\ncase "$1" in run) shift; exec "$@" ;; esac\n' > "$T/recipe/bin/mu-toolchain"; chmod 755 "$T/recipe/bin/mu-toolchain"
ln -sfn /usr/bin/python3 "$T/env/bin/python"; chown -R mu:mu "$T"
S
}
use_node() { echo "$1" | sudo tee /home/mu/.local/share/mu/default >/dev/null; asmu systemctl --user restart mu-node.service; sleep 3; }
q28() {
  qpre || return 1; local r h a ho qo cs x y q hs xs n
  prev_release || { M="the previous release (v2026.11.9) could not be fetched"; return 1; }
  r="$(blocker 30)"; wait_state "$r" running 60
  h="$(submit --cores 1 --mem 256M --time 1m -- true)"; ws hold "$h" --json >/dev/null
  a="$(submit --cores 1 --mem 256M --time 1m -- sh -c "$(qcmd 1)")"; ws move "$a" --front --json >/dev/null
  use_node v2026.11.9
  ho="$(ws hold "$a" --json 2>&1)"; local e1=$?
  qo="$(ws queue --json 2>&1 | jget fallback)"
  cs="$(ws cancel "$h" --json 2>&1 | jget state)"
  wait_state "$a" "succeeded failed" 90
  x="$(submit --cores 1 --mem 256M --time 1m -- sleep 30)"; wait_state "$x" running 60; sleep 2
  hs="$(jobstate "$h")"
  use_node v2026.11.7
  y="$(submit --cores 1 --mem 256M --time 1m -- true)"; q="$(submit --quick -- true)"; sleep 4
  local ys qs; ys="$(jobstate "$y")"; qs="$(jobstate "$q")"
  wait_state "$x" "succeeded failed" 60; wait_state "$h" "cancelled" 20
  n="$(nodelog "$x" | grep -c '^== μ job')"
  M="under the previous release: the held job $hs, a moved one $(jobstate "$a"); new client: hold exit $e1 $(printf '%s' "$ho" | jget error), queue fallback '$qo', cancel of the held job: $cs; back on this release: the held job $(jobstate "$h"); the old runner's job adopted (no grant): a 1-core job $ys, --quick $qs, it ended $(jobstate "$x") started $n time(s)"
  [ "$hs" = held ] && [ "$(jobstate "$a")" = succeeded ] && [ "$e1" = 6 ] && [ "$(printf '%s' "$ho" | jget error)" = node-too-old ] && \
    [ "$qo" = list ] && [ "$cs" = cancelling ] && [ "$(jobstate "$h")" = cancelled ] && [ "$ys" = queued ] && [ "$qs" = succeeded ] && \
    [ "$(jobstate "$x")" = succeeded ] && [ "$n" = 1 ]
  local rc=$?; use_node v2026.11.7; qend $rc
}

ws_prev_release() {   # the previous release's src/mu as a toolchain of the workstation ws (μ v2026.11.9, its git tag)
  sudo test -f "$WSH/toolchains/v2026.11.9/MANIFEST.json" && return 0
  local d; d="$(mktemp -d)"
  git -C "$ROOT" fetch -q --depth 1 origin "+refs/tags/v2026.11.9:refs/tags/v2026.11.9" 2>&1 | tail -2
  git -C "$ROOT" archive v2026.11.9 src/mu | tar -x -C "$d" || return 1
  sudo bash -s "$d" "$WSH/toolchains/v2026.11.9" <<'S'
T="$2"; mkdir -p "$T/recipe/bin" "$T/env/bin" "$T/recipe/src"; cp -R "$1/src/mu" "$T/recipe/src/mu"
printf '{"version": "v2026.11.9", "recipe_commit": "0", "installed": "x", "layers": []}\n' > "$T/MANIFEST.json"
printf '#!/bin/bash\ncase "$1" in run) shift; exec "$@" ;; esac\n' > "$T/recipe/bin/mu-toolchain"; chmod 755 "$T/recipe/bin/mu-toolchain"
ln -sfn /usr/bin/python3 "$T/env/bin/python"; chown -R ws:ws "$T"
S
}
ws_default() { echo "$1" | sudo tee "$WSH/default" >/dev/null; }
q14b() {
  qpre || return 1; local r h q o s1 l1 w1 rc1 rc2 rc3
  ws_prev_release || { M="the previous release (v2026.11.9) could not be fetched"; return 1; }
  r="$(blocker 45)"; wait_state "$r" running 60
  h="$(submit --cores 1 --mem 256M --time 1m -- true)"; ws hold "$h" --json >/dev/null
  q="$(submit --cores 1 --mem 256M --time 1m -- true)"
  ws_default v2026.11.9   # from here the workstation runs the previous release's client (no scheduler in it)
  s1="$(ws status "$h" --json 2>&1)"; rc1=$?
  l1="$(ws status --json 2>&1)"; rc2=$?
  w1="$(ws status "$q" --wait --timeout 4 --json 2>&1)"; rc3=$?
  o="$(submit --cores 1 --mem 256M --time 1m -- sh -c 'echo $MU_JOB_CORES > n.txt')"
  ws_default v2026.11.7
  ws cancel "$r" --json >/dev/null; wait_state "$o" "succeeded failed" 90; ws cancel "$h" --json >/dev/null
  M="the v2026.11.9 client against this node: status of a held job exit $rc1 state '$(printf '%s' "$s1" | jget state)'; the list exit $rc2 ($(printf '%s' "$l1" | grep -c "$h") line(s) with it); --wait on a queued job exit $rc3 (state '$(printf '%s' "$w1" | jget state)'); its submit: $(jobstate "$o"), granted $(jobstate "$o" grant.cores) cores (MU_JOB_CORES $(sudo cat "/srv/mu/jobs/$o/work/n.txt" 2>/dev/null))"
  [ "$rc1" = 0 ] && [ "$(printf '%s' "$s1" | jget state)" = held ] && [ "$rc2" = 0 ] && printf '%s' "$l1" | grep -q "$h" && \
    [ "$rc3" = 10 ] && [ "$(jobstate "$o")" = succeeded ] && [ "$(jobstate "$o" grant.cores)" = "$NC" ]
  local rc=$?; ws_default v2026.11.7; qend $rc
}

# ---------------------------------------------------------------------------------------------- updates on a busy node
REL=/home/mu/rel.git
release_repo() {   # release_repo TAG...: fake releases of this checkout's src/mu in $REL (as mu: git refuses repositories
                   # of another owner); the node's auto-update source points there
  local w; w="$(sudo -u mu mktemp -d /home/mu/relwork.XXXXXX)"
  sudo cp -R "$ROOT/src" "$w/src"; sudo chown -R mu:mu "$w/src"
  sudo -u mu -H bash -s "$w" "$@" <<'S'
w="$1"; shift
rm -rf /home/mu/rel.git; git init -q --bare /home/mu/rel.git   # anew: no tag of an earlier row stays
cd "$w" && git init -q && git config user.name rel && git config user.email rel@example.invalid
cat > install.sh <<'I'
#!/bin/bash
# a stand-in release installer: a stub toolchain of this release's src/mu, MANIFEST.json last
set -eu
here="$(cd "$(dirname "$0")" && pwd)"; V=""
while [ $# -gt 0 ]; do case "$1" in --version) V="$2"; shift 2 ;; *) shift ;; esac; done
T="$MU_HOME/toolchains/$V"; mkdir -p "$T/recipe/bin" "$T/env/bin" "$T/recipe/src"; touch "$T/.installing"
rm -rf "$T/recipe/src/mu"; cp -R "$here/src/mu" "$T/recipe/src/mu"
printf '#!/bin/bash\ncase "$1" in run) shift; exec "$@" ;; esac\n' > "$T/recipe/bin/mu-toolchain"; chmod 755 "$T/recipe/bin/mu-toolchain"
ln -sfn /usr/bin/python3 "$T/env/bin/python"
cat "/sys/fs/cgroup$(sed -n 's/^0:://p' /proc/self/cgroup)/memory.max" > "$T/install-memory.max" 2>/dev/null || true; sleep 3
printf '{"version": "%s", "recipe_commit": "0", "installed": "x", "layers": []}\n' "$V" > "$T/MANIFEST.json"; rm -f "$T/.installing"
echo "fake μ $V is ready"
I
chmod 755 install.sh && git add -A && git commit -qm rel
for t in "$@"; do git tag -f "$t" >/dev/null && git push -q -f /home/mu/rel.git "refs/tags/$t"; done
S
  sudo rm -rf "$w"
  echo "file://$REL" | sudo -u mu tee /home/mu/.local/share/mu/source >/dev/null
  asmu "$NODEH/bin/mu" autoupdate set min_free_gb 0 >/dev/null 2>&1
}
au_state() { sudo sed -n "s/^$1=//p" /home/mu/.local/share/mu/state/autoupdate.state | tail -1; }
v1() {
  qpre || return 1; local a out d n ra rs a0 mm
  # this row's own audit lines and releases: an earlier row's restart line or tag (V3 pushes v2026.11.11) proves nothing
  a0="$(sudo wc -l < /srv/mu/audit.log)"
  sudo rm -rf /home/mu/.local/share/mu/toolchains/v2026.11.10 /home/mu/.local/share/mu/toolchains/v2026.11.11
  release_repo v2026.11.10
  a="$(submit --cores 1 --mem 256M --time 2m -- sh -c "$(qcmd 25)")"; wait_state "$a" running 60; sleep 2
  out="$(asmu "$NODEH/bin/mu" autoupdate run --force 2>&1)"
  d="$(sudo cat /home/mu/.local/share/mu/default)"
  mm="$(sudo cat /home/mu/.local/share/mu/toolchains/v2026.11.10/install-memory.max 2>/dev/null)"
  for n in $(seq 1 20); do sudo tail -n +"$((a0 + 1))" /srv/mu/audit.log | grep -q "runner started (μ v2026.11.10" && break; sleep 1; done
  rs="$(sudo tail -n +"$((a0 + 1))" /srv/mu/audit.log | grep -m1 "runner started (μ v2026.11.10" | cut -d' ' -f1)"
  ra="$(jobstate "$a")"
  wait_state "$a" "succeeded failed interrupted" 90
  n="$(nodelog "$a" | grep -c '^== μ job')"
  M="with a job running: $out · default $d; the install's memory.max $mm; the runner restarted on it at ${rs:-never} while the job was $ra (the job ended $(qt end "$a")); the job $(jobstate "$a"), attempt $(jobstate "$a" attempt), started $n time(s); deferred gc '$(au_state pending_gc)'"
  [ "$d" = v2026.11.10 ] && [ -n "$rs" ] && [ "$ra" = running ] && lt "$(date -ud "$rs" +%s)" "$(qt end "$a")" && \
    [ "$mm" = $((1572864 * 1024)) ] && [ "$(jobstate "$a")" = succeeded ] && [ "$(jobstate "$a" attempt)" = 1 ] && \
    [ "$n" = 1 ] && [ -n "$(au_state pending_gc)" ]
  local rc=$?; use_node v2026.11.7; qend $rc
}
v3() {
  qpre || return 1; local a out h d n
  release_repo v2026.11.10 v2026.11.11
  a="$(submit --cores "$NC" --mem 256M --time 2m -- sh -c "$(qcmd 40)")"; wait_state "$a" running 60; sleep 2
  out="$(ws compute update office-pc --json --wait --timeout 120 2>&1)"
  d="$(printf '%s' "$out" | jget default)"; local as; as="$(jobstate "$a")"
  wait_state "$a" "succeeded failed interrupted" 120
  n="$(nodelog "$a" | grep -c '^== μ job')"
  M="mu compute update with a whole-node job running: $(printf '%s' "$out" | jget state) $(printf '%s' "$out" | jget unit), default now '$d' (was $(printf '%s' "$out" | jget default_was)) while the job was $as; the job $(jobstate "$a") started $n time(s); $(printf '%s' "$out" | jget autoupdate.last_message)"
  [ "$(printf '%s' "$out" | jget ok)" = true ] && [ "$d" = v2026.11.11 ] && [ "$as" = running ] && [ "$(jobstate "$a")" = succeeded ] && [ "$n" = 1 ]
  local rc=$?; use_node v2026.11.7; qend $rc
}
f_v3_on() { asmu systemctl --user set-environment MU_DISPATCH_FAULT=busy-defers; }
f_v3_off() { asmu systemctl --user unset-environment MU_DISPATCH_FAULT; use_node v2026.11.7; }

sched_rows() {
  run_row Q01 "concurrent: two jobs of half the node (--cores $HC --mem 512M) run at the same time" q01
  run_row Q02 "memory admission: three 1 GB jobs with M = 2 GiB: two run, the third waits for memory" q02
  run_row Q03 "core packing: three 2-core jobs on 4 cores: two run, the third waits for cores and starts when one ends" q03
  run_row Q04 "backfill: a short job starts past the waiting head (backfilled); a 1 h one waits with a reservation" q04
  run_row Q05 "starvation guard: a 4-core head behind a running job starts by its first starts_by while short jobs keep coming" q05
  run_row Q06 "express lane: --quick runs beside a whole-node job within seconds, held to one CPU; the third waits as an ordinary 1-core job; explicit small flags are not express" q06
  run_row Q07 "isolation: every thread's CPU mask is its job's grant, two jobs disjoint; a thread that widens its mask is flagged" q07
  run_row Q08 "thread counts: OMP_NUM_THREADS, OPENBLAS, PYTHON_CPU_COUNT, CCX, MU_JOB_CPUS, TMPDIR from the grant" q08
  run_row Q09 "oom of one job does not touch the other (Σ grants ≤ M)" q09
  run_row Q10 "the runner killed (SIGKILL) with three jobs running: all adopted with their grants, each run once; a job that fits only if they were forgotten waits" q10
  run_row Q11 "cancel of one of three running jobs: cancelled, its process tree gone, its cores freed only when its unit ended" q11
  run_row Q12 "too big: --cores 64 and --mem 1T exit 2 too-big before anything is sent; the node refuses one sent past the client; a capacity lowered after submit fails the job too-big" q12
  run_row Q13 "VM stop with three running: re-queued as attempt 2 ahead of a later job, their TMPDIR gone" q13
  run_row Q14 "old workstation: a spec without sched is granted the whole node, MU_JOB_CORES its --cores" q14
  run_row Q18 "a running job without a grant (the previous release's state) holds the node; starts_by null with the reason; --quick still runs" q18
  run_row Q19 "status wording: waiting for 2 cores / 1.0 GB; the head's starts_by; null with the reason when disk is low" q19
  run_row Q20 "usage: shim-written (cgroup) and runner-written (oom, timeout: runner-sampled) results carry usage; hello has the feature" q20
  run_row Q22 "hold / release: a held job keeps its place and is skipped; release puts it back; hold twice changed false; --hold at submit; cancel of a held job" q22
  run_row Q23 "move: --before gives the order asked in mu queue and in the start order; again changed false; --before a running job exit 2" q23
  run_row Q24 "bands: a high job submitted last starts first; --front stays behind it (higher_bands_ahead 1)" q24
  run_row Q25 "queue.json zeroed and a held job's state.json removed: FIFO within bands, the hold kept, one journal line" q25
  run_row Q26 "authorisation: ci-other cannot arrange avi-mac's jobs or pass them (exit 5); the owner's acts are sticky; no owners file with two keys: nobody, warned" q26
  run_row Q27 "one view of two workstations and three projects; the node's --others guard (exit 2 other-project) and its audit lines" q27
  run_row Q28 "mixed versions: the previous release's runner leaves a held job alone and cancels it as cancelling; a new client against it: node-too-old, queue falls back; the new runner adopts the old one's job without a grant" q28
  run_row Q14B "an older workstation (the v2026.11.9 client) against this node: a held job's status, the list, --wait on a queued job (exit 10), its submit granted the whole node" q14b
  run_row V1 "a release installs beside a running job and becomes the default between starts; the runner restarts on it and adopts the job (run once); gc deferred" v1
  run_row V3 "mu compute update office-pc with a whole-node job running: installed and made the default at once (not queued behind it)" v3
}
sched_faults() {
  must_fail Q01 one-at-a-time 'nfault one-at-a-time' nfault q01
  must_fail Q02 no-mem-admission 'nfault no-mem-admission' nfault q02
  must_fail Q03 no-core-admission 'nfault no-core-admission' nfault q03
  must_fail Q04 backfill-ignores-shadow 'nfault backfill-ignores-shadow' nfault q04
  must_fail Q05 no-head-reservation 'nfault no-head-reservation' nfault q05
  must_fail Q05 backfill-ignores-shadow 'nfault backfill-ignores-shadow' nfault q05
  must_fail Q06 no-express 'nfault no-express' nfault q06
  must_fail Q06 no-express-quota 'nfault no-express-quota' nfault q06
  must_fail Q06 express-by-size 'nfault express-by-size' nfault q06
  must_fail Q07 no-affinity 'nfault no-affinity' nfault q07
  must_fail Q07 widen-check-main-thread-only 'nfault widen-check-main-thread-only' nfault q07
  must_fail Q08 no-thread-env 'nfault no-thread-env' nfault q08
  must_fail Q09 mem-max-is-capacity 'nfault mem-max-is-capacity' nfault q09
  must_fail Q10 grant-in-memory-only 'nfault grant-in-memory-only' nfault q10
  must_fail Q11 free-on-cancel-request 'nfault free-on-cancel-request' nfault q11
  must_fail Q12 no-capacity-check 'nfault no-capacity-check; WSF=no-capacity-check' 'nfault; WSF=""' q12
  must_fail Q13 requeue-new-ns 'nfault requeue-new-ns' nfault q13
  must_fail Q13 keep-tmp-on-rerun 'nfault keep-tmp-on-rerun' nfault q13
  must_fail Q14 legacy-sized 'nfault legacy-sized' nfault q14
  must_fail Q18 grantless-counts-zero 'nfault grantless-counts-zero' nfault q18
  must_fail Q19 no-waiting-for 'nfault no-waiting-for' nfault q19
  must_fail Q20 no-usage 'nfault no-usage' nfault q20
  must_fail Q22 hold-ignored 'nfault hold-ignored' nfault q22
  must_fail Q22 release-to-back 'nfault release-to-back' nfault q22
  must_fail Q23 walk-by-accepted-ns 'nfault walk-by-accepted-ns' nfault q23
  must_fail Q23 partial-order-write 'nfault partial-order-write' nfault q23
  must_fail Q24 bands-ignored 'nfault bands-ignored' nfault q24
  must_fail Q25 holds-in-order-file-only 'nfault holds-in-order-file-only' nfault q25
  must_fail Q25 rebuild-held-as-queued 'nfault rebuild-held-as-queued' nfault q25
  must_fail Q26 no-auth-check 'nfault no-auth-check' nfault q26
  must_fail Q26 owner-from-node-json 'nfault owner-from-node-json' nfault q26
  must_fail Q26 owner-acts-not-sticky 'nfault owner-acts-not-sticky' nfault q26
  must_fail Q27 queue-project-filter 'nfault queue-project-filter' nfault q27
  must_fail Q27 no-others-guard 'nfault no-others-guard' nfault q27
  must_fail Q28 held-as-queued 'nfault held-as-queued' nfault q28
  must_fail Q14B held-as-queued 'nfault held-as-queued' nfault q14b
  must_fail V1 restart-only-idle 'nfault restart-only-idle' nfault v1
  must_fail V3 "busy-defers (the previous release's rule, in the update unit's environment)" f_v3_on f_v3_off v3
}
SCHED_ROWS="QS Q01 Q02 Q03 Q04 Q05 Q06 Q07 Q08 Q09 Q10 Q11 Q12 Q13 Q14 Q18 Q19 Q20 Q22 Q23 Q24 Q25 Q26 Q27 Q28 Q14B V1 V3"
SCHED_FAULTS="Q01 Q02 Q03 Q04 Q05 Q05 Q06 Q06 Q06 Q07 Q07 Q08 Q09 Q10 Q11 Q12 Q13 Q13 Q14 Q18 Q19 Q20 Q22 Q22 Q23 Q23 Q24 Q25 Q25 Q26 Q26 Q26 Q27 Q27 Q28 Q14B V1 V3"
