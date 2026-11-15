# The dashboard's phase 1b rows against a real node (docs/design/dashboard.md §7.1, §7.1.2), sourced by
# tools/test-jobs/e2e.sh for
#   tools/test-jobs/e2e.sh dash        set-up (as for sched), then every row, then every row with its fault (must fail)
# The node's side runs from this checkout (the runner, the verbs); the Mac's side is src/mu/dashboard.py's App with
# its real NodeReader, run as the workstation ws (tools/test-jobs/dash_real.py), at API level.
# Rows: D01 D02 D03 D09 D10 D11 D21 D25 D40 (node side), D05 D06 D07 again against the real node, D40P (with a
# paused job: SKIP on a node without `pause`, which is this branch; the merged branch requires PASS).

FX="$ROOT/tools/test-dashboard/fixtures"
WSRC="$WSH/toolchains/v2026.11.7/recipe/src"
DREAL=/tmp/mu-dash-real.py; DSHAPE=/tmp/mu-dash-shape.py
nodeverb() { wssh mu-office-pc "$@" 2>/dev/null; }   # a node verb through the workstation's own ssh (no master)
nodeverb_for() {   # nodeverb_for SECS VERB ARGS: a streaming verb, ended after SECS. `timeout` runs programs only, not the
  local s="$1"; shift   # shell function wssh (it would fail at once, unseen), so the command is spelled out here
  timeout "$s" sudo -u ws -H env -u XDG_CONFIG_HOME -u XDG_RUNTIME_DIR MU_JOBS_FAULT="$WSF" \
    ssh -F "$WCFG" -o ControlPath=none mu-office-pc "$@" 2>>"$WORK/logs/dash-verbs.log"
}
dash_real() { asws env MU_JOBS_FAULT="$WSF" python3 -I -S "$DREAL" "$WSRC" "$@" 2>>"$WORK/logs/dash-real.log" | tail -1; }
jx() { python3 -c 'import json,sys
d=json.loads(sys.stdin.read() or "{}")
for k in sys.argv[1].split("."):
    d = d.get(k) if isinstance(d, dict) else (d[int(k)] if isinstance(d, list) and k.isdigit() and int(k) < len(d) else None)
print("" if d is None else (json.dumps(d) if isinstance(d, (dict, list, bool)) else d))' "$1" 2>/dev/null; }
nowjson() { sudo cat /srv/mu/metrics/now.json 2>/dev/null; }
shape() {   # shape FIXTURE FILE [OPTIONAL]: D40's check; M gets its verdict
  local r; r="$(python3 -I -S "$DSHAPE" "$FX/$1" "$2" "${3:-}" 2>&1)"; M="$M · $1: $(printf '%s' "$r" | head -3 | tr '\n' ' ')"
  [ "$r" = ok ]
}
musage() {   # the CPU-seconds of μ's own processes on the node: the mu user's slice (runner, sessions) and mu-sshd
  sudo cat "/sys/fs/cgroup/user.slice/user-$MU_UID.slice/cpu.stat" /sys/fs/cgroup/system.slice/mu-sshd.service/cpu.stat \
    2>/dev/null | awk '$1 == "usage_usec" { s += $2 } END { printf "%.2f", s / 1e6 }'
}

dash_setup() {
  echo "== dashboard 1b set-up: the scheduler's node (C = $NC, M = 2 GiB), the helper scripts"
  node_py 'node.update_node_info(sched={"cores": '"$NC"', "mem_bytes": 2 << 30, "check": True}, min_free_gb=1)'
  asmu systemctl --user restart mu-node.service; sleep 6
  sudo install -m 0755 "$ROOT/tools/test-jobs/dash_real.py" "$DREAL"; sudo install -m 0755 "$ROOT/tools/test-jobs/dash_shape.py" "$DSHAPE"
  [ -n "$(nowjson)" ] || { row FAIL DS "dashboard set-up" "the runner wrote no metrics/now.json"; return 1; }
  row PASS DS "dashboard set-up" "now.json present; features $(ws compute status office-pc --json | jget hello.features)"
}

# ---------------------------------------------------------------------------------------------- the node's side
d01() {   # 1 GB allocated, 2 cores busy for 10 s
  qpre || return 1; local j
  j="$(submit --cores 2 --mem 1500M --time 2m -- python3 -c 'import os, time
b = bytearray(1 << 30)
for i in range(0, len(b), 4096): b[i] = 1
pid = os.fork(); t = time.time()
while time.time() - t < 10: pass
if pid: os.wait()')"
  wait_state "$j" "succeeded failed oom" 120
  local u; u="$(ws status "$j" --json | jget usage)"
  M="$(jobstate "$j"); usage $u"
  [ "$(printf '%s' "$u" | jx source)" = cgroup ] && lt 1000000000 "$(printf '%s' "$u" | jx mem_peak_bytes)" && \
    lt 17 "$(printf '%s' "$u" | jx cpu_s)" && lt "$(printf '%s' "$u" | jx cpu_s)" 23.5 && \
    lt 1.5 "$(printf '%s' "$u" | jx cores_avg)" && lt "$(printf '%s' "$u" | jx cores_avg)" 2.5
  qend $?
}
d02() {   # oom and timeout: runner-written usage; mu fetch copies usage into runs/<id>.json
  qpre || return 1; local o t s r
  # The oom job holds 224 MiB (87 % of its limit) for 12 s, so the 5 s sampler sees it, then grows past the limit. A
  # job killed before its first sample has no usage at all (the cgroup is gone at once, the dash-probe found).
  o="$(submit --cores 1 --mem 256M --time 1m -- python3 -c 'import time; b = []
def grow(): b.append(bytearray(16 << 20)); [b[-1].__setitem__(i, 1) for i in range(0, 16 << 20, 4096)]
for i in range(14): grow()
time.sleep(12)
while True: grow(); time.sleep(0.05)')"
  t="$(submit --cores 1 --mem 128M --time 12s -- python3 -c 'while True: pass')"
  s="$(submit --cores 1 --mem 128M --time 1m --output 'out/*.txt' -- sh -c 'mkdir -p out; echo x > out/a.txt')"
  for x in $o $t $s; do wait_state "$x" "succeeded failed oom timeout" 120; done
  ws fetch "$s" --json > "$WORK/out/d02-fetch.json" 2>&1
  r="$(sudo cat "/home/ws/proj/runs/$s.json" 2>/dev/null | jx usage.source)"
  M="oom: $(jobstate "$o") $(jobstate "$o" usage.source) peak $(jobstate "$o" usage.mem_peak_bytes) kills $(jobstate "$o" usage.mem_events.oom_kill); timeout: $(jobstate "$t") $(jobstate "$t" usage.source) cpu $(jobstate "$t" usage.cpu_s); fetched record usage $r"
  [ "$(jobstate "$o")" = oom ] && [ "$(jobstate "$o" usage.source)" = runner-sampled ] && \
    lt 214748364 "$(jobstate "$o" usage.mem_peak_bytes)" && [ "$(jobstate "$t")" = timeout ] && \
    [ "$(jobstate "$t" usage.source)" = runner-sampled ] && lt 5 "$(jobstate "$t" usage.cpu_s)" && [ "$r" = cgroup ]
  qend $?
}
stats_ms() {   # the median of 5 stats answers on the node, in ms
  node_py 'from mu import nodestats; import time
ts = []
for i in range(5):
    t = time.perf_counter(); nodestats.stats_answer(); ts.append((time.perf_counter() - t) * 1000)
print("%.1f" % sorted(ts)[2])'
}
d03() {   # stats: the running job; idle; 2,000 finished jobs (under 100 ms, and no slower than without them: stats
          # never scans the job folders, which a fast runner can do within 100 ms); stale with the runner stopped
  qpre || return 1; local j a idle ms ms0 st
  j="$(submit --cores 1 --mem 256M --time 1m -- python3 -c 'import time; t = time.time()
while time.time() - t < 20: pass')"
  wait_state "$j" running 60; sleep 11
  a="$(nodeverb stats)"; wait_state "$j" "succeeded failed" 60; sleep 7
  idle="$(nodeverb stats | jx running)"
  ms0="$(stats_ms)"
  sudo python3 -I -S - <<'PY'
import json, os
for i in range(2000):
    d = "/srv/mu/jobs/20250101-%06d-dashsy" % i
    os.makedirs(d, exist_ok=True)
    for n in ("job.json", "state.json", "result.json"):
        open(os.path.join(d, n), "w").write(json.dumps({"job_id": os.path.basename(d), "state": "succeeded"}))
os.system("chown -R mu:mu /srv/mu/jobs")
PY
  ms="$(stats_ms)"
  sudo sh -c 'rm -rf /srv/mu/jobs/20250101-*-dashsy'
  asmu systemctl --user stop mu-node.service; sleep 21
  st="$(nodeverb stats)"; asmu systemctl --user start mu-node.service; sleep 6
  M="running: $(printf '%s' "$a" | jx running.0.job_id | cut -c10-) cores $(printf '%s' "$a" | jx running.0.cpu_cores); idle: $idle; 2,000 jobs: ${ms} ms (without them ${ms0} ms); runner stopped 21 s: stale $(printf '%s' "$st" | jx stale) age $(printf '%s' "$st" | jx age_s)"
  [ "$(printf '%s' "$a" | jx running.0.job_id)" = "$j" ] && lt 0.6 "$(printf '%s' "$a" | jx running.0.cpu_cores)" && \
    lt "$(printf '%s' "$a" | jx running.0.cpu_cores)" 1.4 && [ "$idle" = "[]" ] && lt "$ms" 100 && \
    lt "$ms" "$(awk -v a="$ms0" 'BEGIN { print a + 5 }')" && \
    [ "$(printf '%s' "$st" | jx stale)" = true ]
  qend $?
}
d09() {   # the sampler's cost with 1 job and with NC jobs; 5 s between samples; no gap in a job's series
  qpre || return 1; local j k ids=() c1 cn gap n
  j="$(submit --cores 1 --mem 128M --time 2m -- sleep 40)"; wait_state "$j" running 60; sleep 2
  c1="$(for i in 1 2 3 4 5 6; do nowjson | jx sample_ms; sleep 5; done | sort -n | sed -n 3p)"
  for k in $(seq 2 "$NC"); do ids+=("$(submit --cores 1 --mem 128M --time 2m -- sleep 30)"); done
  for k in "${ids[@]}"; do wait_state "$k" running 60; done; sleep 2
  cn="$(for i in 1 2 3 4 5 6; do nowjson | jx sample_ms; sleep 5; done | sort -n | sed -n 3p)"
  wait_state "$j" "succeeded failed" 90
  gap="$(sudo cat "/srv/mu/jobs/$j/metrics.jsonl" | python3 -c 'import json, sys
t = [json.loads(x)["t"] for x in sys.stdin if x.strip()]
print("%d %.1f %.1f" % (len(t), max(b - a for a, b in zip(t, t[1:])), sorted(b - a for a, b in zip(t, t[1:]))[len(t) // 2]))')"
  n="$(dash_real metrics "$j")"
  M="median pass: 1 job ${c1} ms, $NC jobs ${cn} ms (budget $((5 + 3)) / $((5 + 3 * NC)) ms); node series: n, max gap, median step = $gap; the server's series (no dashboard while the job ran): $(printf '%s' "$n" | jx n) samples, max gap $(printf '%s' "$n" | jx max_gap)"
  lt "$c1" 8 && lt "$cn" "$((5 + 3 * NC))" && lt "$(echo "$gap" | cut -d' ' -f2)" 10.5 && \
    lt 4.5 "$(echo "$gap" | cut -d' ' -f3)" && lt "$(echo "$gap" | cut -d' ' -f3)" 5.5 && \
    lt 5 "$(printf '%s' "$n" | jx n)" && lt "$(printf '%s' "$n" | jx max_gap)" 10.5
  qend $?
}
d10() {   # metrics in two pieces (a link cut after N bytes) joined by offset = the node's file
  qpre || return 1; local j size cut a b
  j="$(submit --cores 1 --mem 128M --time 1m -- sleep 25)"; wait_state "$j" "succeeded failed" 90
  sudo cat "/srv/mu/jobs/$j/metrics.jsonl" > "$WORK/out/d10-node"
  size="$(wc -c < "$WORK/out/d10-node")"; cut=$((size / 2))
  nodeverb metrics "id=$j" offset=0 "max=$cut" > "$WORK/out/d10-a"
  nodeverb metrics "id=$j" "offset=$cut" > "$WORK/out/d10-b"
  cat "$WORK/out/d10-a" "$WORK/out/d10-b" > "$WORK/out/d10-joined"
  M="node file $size B; pieces $(wc -c < "$WORK/out/d10-a") + $(wc -c < "$WORK/out/d10-b") B"
  [ "$size" -gt 100 ] && cmp -s "$WORK/out/d10-node" "$WORK/out/d10-joined"
}
d11() {   # watch ends at max; a vanished client leaves no session after the cap; stats/metrics silent, watch two lines
  qpre || return 1; local a0 a1 a2 t0 secs left j
  j="$(submit --cores 1 --mem 128M --time 1m -- true)"; wait_state "$j" "succeeded failed" 60
  a0="$(audit_lines)"
  nodeverb stats >/dev/null; nodeverb metrics "id=$j" offset=0 >/dev/null
  a1="$(audit_lines)"
  t0="$(now)"; nodeverb_for 25 watch interval=2 max=5 > "$WORK/out/d11-watch"; secs="$(since "$t0")"
  a2="$(audit_lines)"
  (nodeverb_for 25 watch interval=2 max=5 | head -1 >/dev/null) & sleep 2
  sudo pkill -u ws -f 'watch interval=2 max=5' 2>/dev/null; sleep 8
  left="$(pgrep -u mu -f 'serve .*watch' | wc -l)"
  M="stats + metrics: $((a1 - a0)) audit lines; watch max=5: ${secs} s, $(wc -l < "$WORK/out/d11-watch") lines, $((a2 - a1)) audit lines; sessions left after the cap: $left"
  [ "$((a1 - a0))" = 0 ] && lt "$secs" 9 && [ "$((a2 - a1))" = 2 ] && [ "$left" = 0 ]
}
d21() {
  local s; s="$(ws compute status office-pc --json)"
  M="stats.ok $(printf '%s' "$s" | jget stats.ok), host.cpu_pct $(printf '%s' "$s" | jget stats.host.cpu_pct)"
  [ "$(printf '%s' "$s" | jget stats.ok)" = true ] && [ -n "$(printf '%s' "$s" | jget stats.host)" ]
}
d25() {   # a visible tab for 2 min during a job: what the tab adds to μ's own CPU on the node, under 2 % of one core
  # μ's processes include release 1's runner, which passes every second with or without a tab (in all, 2.90 CPU-s in
  # 120 s with the tab in the first CI run). So the same 120 s are measured first without the tab (the same job
  # running), and the budget holds for what the tab adds (§7.1.2).
  qpre || return 1; local j u0 u1 u2 r base tab
  j="$(submit --cores 1 --mem 128M --time 6m -- sleep 290)"; wait_state "$j" running 60
  u0="$(musage)"; sleep 120; u1="$(musage)"; r="$(dash_real load 120)"; u2="$(musage)"
  ws cancel "$j" --json >/dev/null 2>&1
  base="$(awk -v a="$u0" -v b="$u1" 'BEGIN { printf "%.2f", b - a }')"
  tab="$(awk -v a="$u1" -v b="$u2" -v c="$base" 'BEGIN { printf "%.2f", (b - a) - c }')"
  M="μ's processes in 120 s: $base CPU-s without the tab, $(awk -v a="$u1" -v b="$u2" 'BEGIN { printf "%.2f", b - a }') with it; the tab adds $tab CPU-s (budget 2.4); the server used $(printf '%s' "$r" | jx calls) (sent $(printf '%s' "$r" | jx sent))"
  lt "$tab" 2.4 && [ "$(printf '%s' "$r" | jx calls)" = watch ]
}
d40() {   # the real node's answers against the golden fixtures (keys and types)
  qpre || return 1; local j feats opt=""
  j="$(submit --cores 1 --mem 128M --time 1m -- sleep 20)"; wait_state "$j" running 60; sleep 11
  nodeverb hello protocol=1 > "$WORK/out/d40-hello"; nodeverb stats > "$WORK/out/d40-stats"
  nodeverb_for 20 watch interval=2 max=4 | head -1 > "$WORK/out/d40-watch"
  wait_state "$j" "succeeded failed" 60
  nodeverb metrics "id=$j" offset=0 > "$WORK/out/d40-metrics"
  feats="$(jx features < "$WORK/out/d40-hello")"
  case "$feats" in *'"pause"'*) ;; *) opt="auto_pause,pause,paused" ;; esac   # scheduler release 2's, not on this node
  M="features $feats"
  local rc=0
  shape node-hello.json "$WORK/out/d40-hello" "$opt" || rc=1
  shape node-stats.json "$WORK/out/d40-stats" "$opt" || rc=1
  shape node-watch-line.json "$WORK/out/d40-watch" "$opt" || rc=1
  shape node-metrics-lines.json "$WORK/out/d40-metrics" "paused" || rc=1
  case "$feats" in *'"stats"'*) ;; *) M="$M · no stats feature"; rc=1 ;; esac
  qend $rc
}
d40p() {   # with a paused job (scheduler release 2): list, status, stats, a watch line and metrics against the r2 fixtures
  case "$(ws compute status office-pc --json | jget hello.features)" in
    *'"pause"'*) ;;
    *) row SKIP D40P "with a paused job, the answers against the release 2 fixtures" "the node has no pause feature (scheduler release 2 not merged)"; return 2 ;;
  esac
  qpre || return 1; local j
  j="$(submit --cores 1 --mem 256M --time 2m --pausable -- sleep 60)"; wait_state "$j" running 60; sleep 6
  # --for: the default pause (30 min) is past this 2-minute job's budget, min(4 h, --time), and is refused pause-limit
  ws pause "$j" --for 90s --json > "$WORK/out/d40p-pause" 2>&1; sleep 12
  nodeverb list limit=100 > "$WORK/out/d40p-list"; nodeverb status "id=$j" > "$WORK/out/d40p-status"
  nodeverb stats > "$WORK/out/d40p-stats"
  nodeverb_for 20 watch interval=2 max=4 | head -1 > "$WORK/out/d40p-watch"
  nodeverb metrics "id=$j" offset=0 > "$WORK/out/d40p-metrics"
  ws resume "$j" --json >/dev/null 2>&1; ws cancel "$j" --json >/dev/null 2>&1
  M=""; local rc=0
  # the shapes below let null match anything, so first: the job really is paused, in every answer
  M="pause: $(jx ok < "$WORK/out/d40p-pause") $(jx error < "$WORK/out/d40p-pause"); status paused.phase '$(jx paused.phase < "$WORK/out/d40p-status")'"
  [ "$(jx paused.phase < "$WORK/out/d40p-status")" = frozen ] || rc=1
  shape node-status-paused.json "$WORK/out/d40p-status" || rc=1
  shape node-list-r2.json "$WORK/out/d40p-list" || rc=1
  shape node-stats-r2.json "$WORK/out/d40p-stats" || rc=1
  shape node-stats-r2.json "$WORK/out/d40p-watch" || rc=1
  python3 -c 'import json,sys; sys.exit(0 if any(json.loads(l).get("paused") == 1 for l in open(sys.argv[1]) if l.strip()) else 1)' \
    "$WORK/out/d40p-metrics" || { M="$M · no metrics line with \"paused\": 1"; rc=1; }
  python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if any(r.get("paused") is True for r in d.get("running", [])) and d["queue"]["paused"] >= 1 else 1)' \
    "$WORK/out/d40p-stats" || { M="$M · stats: no running entry with paused true, or queue.paused 0"; rc=1; }
  qend $rc
}

# ---------------------------------------------------------------------------------------------- the Mac's side
d05() {   # a job under its node, each state within 15 s of the node's change (the state rev moves the list)
  qpre || return 1; local j r st en lag1 lag2
  j="$(submit --cores 1 --mem 128M --time 2m -- sleep 25)"
  r="$(dash_real follow "$j" 90)"
  st="$(ws status "$j" --json | jget started_at)"; en="$(ws status "$j" --json | jget ended_at)"
  lag1="$(printf '%s' "$r" | python3 -c 'import json, sys, calendar, time
d = json.loads(sys.stdin.read()); want = sys.argv[1]; t = calendar.timegm(time.strptime(sys.argv[2], "%Y-%m-%dT%H:%M:%SZ"))
print(next(("%.1f" % (a - t) for a, s in d["seen"] if s == want), "never"))' running "$st")"
  lag2="$(printf '%s' "$r" | python3 -c 'import json, sys, calendar, time
d = json.loads(sys.stdin.read()); want = sys.argv[1]; t = calendar.timegm(time.strptime(sys.argv[2], "%Y-%m-%dT%H:%M:%SZ"))
print(next(("%.1f" % (a - t) for a, s in d["seen"] if s == want), "never"))' succeeded "$en")"
  M="rows under $(printf '%s' "$r" | jx rows_node); running seen +$lag1 s, succeeded seen +$lag2 s after the node; seen $(printf '%s' "$r" | jx seen | cut -c1-200)"
  [ "$(printf '%s' "$r" | jx rows_node)" = '["office-pc"]' ] && [ "$lag1" != never ] && [ "$lag2" != never ] && \
    lt "$lag1" 16 && lt "$lag2" 16
  qend $?
}
d06() {   # the card's figures are the node's (the same sample)
  local r; r="$(dash_real figures 12)"
  M="card mem $(printf '%s' "$r" | jx card.mem) · node mem $(printf '%s' "$r" | jx raw_mem) · t $(printf '%s' "$r" | jx raw_t) / $(printf '%s' "$r" | jx card_t_node)"
  [ -n "$(printf '%s' "$r" | jx raw_t)" ] && [ "$(printf '%s' "$r" | jx raw_t)" = "$(printf '%s' "$r" | jx card_t_node)" ] && \
    [ "$(printf '%s' "$r" | jx card.mem.avail)" = "$(printf '%s' "$r" | jx raw_mem.avail)" ] && \
    [ "$(printf '%s' "$r" | jx card.mem.total)" = "$(printf '%s' "$r" | jx raw_mem.total)" ] && \
    [ -n "$(printf '%s' "$r" | jx card.mem.avail)" ]
}
d07() {   # the node's runner stopped: the card's figures turn stale within 25 s (15 s plus a sample)
  local r
  asmu systemctl --user stop mu-node.service
  r="$(dash_real stale 45)"
  asmu systemctl --user start mu-node.service; sleep 6
  M="stale after $(printf '%s' "$r" | jx stale_after_s) s; card stale $(printf '%s' "$r" | jx card.stale), node_stale $(printf '%s' "$r" | jx card.node_stale)"
  [ -n "$(printf '%s' "$r" | jx stale_after_s)" ] && lt "$(printf '%s' "$r" | jx stale_after_s)" 25
}

dash_rows() {
  run_row D01 "usage of a shim-written result: 1 GB peak, 20 cpu-s, cores_avg 2, source cgroup" d01
  run_row D02 "usage of runner-written results (oom, timeout: runner-sampled); mu fetch copies usage" d02
  run_row D03 "stats: the running job, idle [], under 100 ms with 2,000 finished jobs, stale with the runner stopped" d03
  run_row D09 "the sampler: median pass within 5 ms + 3 ms per job, 5 s apart, no gap; the server's series has none either" d09
  run_row D10 "metrics pieces joined by offset across a cut equal the node's file" d10
  run_row D11 "watch ends at max, no session left; stats and metrics journal nothing, watch two lines" d11
  run_row D21 "mu compute status --json carries stats" d21
  run_row D25 "a visible tab for 2 min during a job: μ's CPU on the node under 2 % of a core, through one watch" d25
  run_row D40 "the real node's hello, stats, watch line and metrics lines have the golden fixtures' shape" d40
  run_row D05 "the server against the real node: the job under its node, each state within 15 s" d05
  run_row D06 "the server against the real node: the card's memory is the node's sample" d06
  run_row D07 "the server against the real node: the runner stopped, the figures stale within 25 s" d07
  printf '\n-- D40P\n'; M=""; d40p; case $? in 0) row PASS D40P "with a paused job, the answers against the release 2 fixtures" "$M" ;;
    2) ;; *) row FAIL D40P "with a paused job, the answers against the release 2 fixtures" "$M" ;; esac
  # On a node with `pause` (the merged branch) D40P is an expected row (Z1: it must have run) and its fault an
  # expected must-fail (Z2); here it reports SKIP and neither is expected.
  if node_pauses; then DASH_ROWS="$DASH_ROWS D40P"; DASH_FAULTS="$DASH_FAULTS D40P"; fi
}
node_pauses() { case "$(ws compute status office-pc --json | jget hello.features)" in *'"pause"'*) return 0 ;; esac; return 1; }
dash_faults() {
  must_fail D01 usage-from-shim 'nfault usage-from-shim' nfault d01
  must_fail D02 read-cgroup-after-exit 'nfault read-cgroup-after-exit' nfault d02
  must_fail D03 stats-scan 'nfault stats-scan' nfault d03
  must_fail D03 no-age 'nfault no-age' nfault d03
  must_fail D09 mac-samples-only 'WSF=mac-samples-only' 'WSF=""' d09
  must_fail D10 metrics-from-zero 'nfault metrics-from-zero' nfault d10
  must_fail D11 no-cap 'nfault no-cap' nfault d11
  must_fail D11 journal-each-call 'nfault journal-each-call' nfault d11
  must_fail D21 drop-stats 'WSF=drop-stats' 'WSF=""' d21
  must_fail D25 poll-stats-1s 'WSF=poll-stats-1s' 'WSF=""' d25 'used stats x' 
  must_fail D40 rename-field-node 'nfault rename-field-node' nfault d40
  must_fail D05 rev-frozen 'nfault rev-frozen' nfault d05
  must_fail D06 swap-mem-fields 'WSF=swap-mem-fields' 'WSF=""' d06
  must_fail D07 serve-last-as-live 'WSF=serve-last-as-live' 'WSF=""' d07
  if node_pauses; then must_fail D40P rename-field-node-paused 'nfault rename-field-node-paused' nfault d40p 'paused: missing|no running entry with paused true'; fi
}
DASH_ROWS="DS D01 D02 D03 D09 D10 D11 D21 D25 D40 D05 D06 D07"
DASH_FAULTS="D01 D02 D03 D03 D09 D10 D11 D11 D21 D25 D40 D05 D06 D07"
