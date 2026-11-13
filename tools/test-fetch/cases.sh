#!/bin/bash
# Download test cases for install/download.sh, run by run.sh under each bash (macOS /bin/bash 3.2, bash 5).
# The shell is set up like the installer's: set -Eeuo pipefail, an ERR trap that calls fail, and fetch called as
# f="$(fetch NAME URL SHA)". Environment from run.sh: REPO TF PORT BIG_SHA SMALL_SHA BIG_SIZE [CASES].
set -Eeuo pipefail
. "$REPO/install/download.sh"
fail() { trap - ERR; printf '%s\n' "$*" >> "$TF/fail.msg"; printf 'error: %s\n' "$*" >&2; exit 1; }
trap 'fail "command failed (line $LINENO): $BASH_COMMAND"' ERR
eval "$(grep '^kill_tree()' "$REPO/install/install.sh")"   # the installer's own cleanup of a job's process tree
export MU_CACHE="$TF/cache"; DL="$MU_CACHE/downloads"; SEED=""
U="http://127.0.0.1:$PORT"
MB=1048576

# child modes for the interrupt cases: a miniature installer (same traps), killed with SIGINT by the parent
if [ "${1:-}" = --child-fg ]; then   # like `f="$(fetch micromamba ...)"` in the installer's main shell
  trap 'exit 130' INT TERM
  f="$(fetch "$2" "$3" "$4")"; echo "unexpected: finished $f"; exit 0
fi
if [ "${1:-}" = --child-job ]; then   # like start_job/app_layer: a background job, cleaned up by kill_tree on exit
  JOBS=""
  cleanup() { local e; for e in $JOBS; do kill_tree "${e#*:}"; done; }
  trap cleanup EXIT; trap 'exit 130' INT TERM
  ( IN_JOB=1; fetch "$2" "$3" "$4" >/dev/null ) > "$TF/job.log" 2>&1 & JOBS="dl:$!"
  wait "${JOBS#*:}"; echo "unexpected: finished"; exit 0
fi

now() { python3 -I -c 'import time; print("%.2f" % time.time())'; }
add() { PROBS="$PROBS${PROBS:+; }$*"; }
sv() { printf '%s\n' "$STATS" | awk -F= -v k="$1" '$1 == k { v = $2 } END { print v + 0 }'; }
mb() { awk -v b="$1" 'BEGIN { printf "%.1f", b / 1048576 }'; }
want() { case " ${CASES:-all} " in *" all "*|*" $1 "*) return 0 ;; *) return 1 ;; esac; }
leftovers() { ls -A "$DL" | tr '\n' ' ' | sed 's/ $//'; }
procs() { pgrep -f "127.0.0.1:$PORT/" | tr '\n' ' ' || true; }   # curl processes still talking to the test server

begin() {
  CASE="$1"; PROBS=""; NOTE=""; rm -rf "$DL"; mkdir -p "$DL"; : > "$TF/fail.msg"; : > "$TF/case.log"
  curl -s "$U/_reset" >/dev/null
  unset MU_DL_SEGMENTS MU_DL_STALL_S MU_DL_IDLE_S MU_DL_MAX_ATTEMPTS MU_DL_BACKOFF_MAX MU_DL_SEG_MIN CURL_HOME
}
run_fetch() {   # run_fetch NAME URL SHA: like the installer; sets OUT RC SECS STATS
  local t0 t1; t0=$(now)
  if OUT="$(fetch "$1" "$2" "$3" 2>>"$TF/case.log")"; then RC=0; else RC=$?; fi
  t1=$(now); SECS=$(awk -v a="$t0" -v b="$t1" 'BEGIN { printf "%.1f", b - a }')
  STATS="$(curl -s "$U/_stats")"
}
no_err_trap() { if grep -q 'command failed' "$TF/fail.msg"; then add "ERR trap fired: $(grep 'command failed' "$TF/fail.msg" | head -1)"; fi; }
expect_ok() {   # expect_ok NAME SHA
  if [ "$RC" != 0 ]; then add "fetch failed: $(head -1 "$TF/fail.msg")"; fi
  if [ "$OUT" != "$DL/$1" ]; then add "stdout is not exactly the path: '$OUT'"; fi
  if [ ! -f "$DL/$1" ] || [ "$(sha256 "$DL/$1")" != "$2" ]; then add "SHA-256 of the result is wrong"; fi
  if [ "$(leftovers)" != "$1" ]; then add "files left in downloads/: $(leftovers)"; fi
  no_err_trap
}
expect_fail() {   # expect_fail PATTERN
  if [ "$RC" = 0 ]; then add "fetch succeeded"; fi
  if [ -n "$OUT" ]; then add "stdout not empty: '$OUT'"; fi
  if ! grep -q "$1" "$TF/fail.msg"; then add "no failure message matching '$1' (got: $(head -1 "$TF/fail.msg"))"; fi
  if [ -n "$(leftovers)" ]; then add "files left in downloads/: $(leftovers)"; fi
  no_err_trap
}
zero() {   # zero N: how many data requests started at byte 0 (1 = the file was never restarted)
  if [ "$(sv from_zero)" != "$1" ]; then add "$(sv from_zero) requests started at byte 0, expected $1"; fi
}
served() {   # served LO HI (bytes)
  local b; b=$(sv bytes)
  if [ "$b" -lt "$1" ] || [ "$b" -gt "$2" ]; then add "served $b bytes, expected $1..$2"; fi
}
interrupt() {   # interrupt COMMAND...: run it in its own process group; once 8 MB are in range files (or after 30 s),
  # send SIGINT to the group, as Ctrl-C does (waiting for progress, not a fixed time: a loaded machine starts slowly)
  python3 -I -c 'import glob, os, signal, subprocess, sys, time
p = subprocess.Popen(sys.argv[2:], start_new_session=True)
t = time.time() + 30
def held():
    n = 0
    for f in glob.glob(sys.argv[1] + "/big.bin.part.[0-9][0-9]"):
        try: n += os.path.getsize(f)
        except OSError: pass
    return n
while time.time() < t and held() < 8 << 20 and p.poll() is None: time.sleep(0.1)
os.killpg(p.pid, signal.SIGINT); p.wait(timeout=60)' "$DL" "$@"
}
log_has() { if ! grep -q "$1" "$TF/case.log"; then add "log lacks '$1'"; fi; }
log_lacks() { if grep -q "$1" "$TF/case.log"; then add "log has '$1'"; fi; }
end() {
  local r=PASS; [ -z "$PROBS" ] || r=FAIL
  printf '%-4s %-22s %6ss  served %6s MB  req %3s  %s%s\n' "$r" "$CASE" "$SECS" "$(mb "$(sv bytes)")" \
    "$(( $(sv data_requests) + $(sv probes) ))" "$NOTE" "${PROBS:+  PROBLEMS: $PROBS}"
  if [ "$r" = FAIL ] || [ -n "${VERBOSE:-}" ]; then sed 's/^/        | /' "$TF/case.log" | tail -n 25; fi
}

S=$BIG_SIZE
printf '\n%s: bash %s, curl %s\n' "$BASH" "$BASH_VERSION" "$(curl --version | head -1 | cut -d' ' -f2)"

if want basic; then   # a healthy server: 8 parallel ranges, every byte served once (+1 for the range probe)
  begin basic; run_fetch big.bin "$U/m/plain/big.bin" "$BIG_SHA"; expect_ok big.bin "$BIG_SHA"
  served "$S" $((S + 1)); zero 1; log_has "in 8 parallel ranges"
  [ "$(sv data_requests)" = 8 ] || add "expected 8 range requests, got $(sv data_requests)"
  NOTE="8 ranges"; end
fi
if want cached; then   # unchanged fast path: a verified cached file needs no request
  begin cached; run_fetch big.bin "$U/m/plain/big.bin" "$BIG_SHA" >/dev/null
  curl -s "$U/_reset" >/dev/null; : > "$TF/case.log"
  run_fetch big.bin "$U/m/plain/big.bin" "$BIG_SHA"; expect_ok big.bin "$BIG_SHA"; served 0 0
  NOTE="2nd fetch: 0 requests"; end
fi
if want seed; then   # unchanged fast path: --seed-downloads
  begin seed; mkdir -p "$TF/seed"; cp "$TF/srv/small.bin" "$TF/seed/small.bin"
  SEED="$TF/seed"; run_fetch small.bin "$U/m/plain/small.bin" "$SMALL_SHA"; SEED=""; rm -rf "$TF/seed"
  expect_ok small.bin "$SMALL_SHA"; served 0 0; NOTE="copied from the seed folder"; end
fi
if want small; then   # below MU_DL_SEG_MIN: one connection after the probe
  begin small; run_fetch small.bin "$U/m/plain/small.bin" "$SMALL_SHA"; expect_ok small.bin "$SMALL_SHA"
  [ "$(sv data_requests)" = 1 ] || add "expected 1 request, got $(sv data_requests)"
  NOTE="single connection"; end
fi
if want a-drop-ranges; then   # (a) 6 of the 8 range connections close after 4 MB: each range resumes, nothing re-sent
  begin a-drop-ranges; run_fetch big.bin "$U/m/drop=4000000x6/big.bin" "$BIG_SHA"; expect_ok big.bin "$BIG_SHA"
  served "$S" $((S + 1)); zero 1; [ "$(sv drops)" = 6 ] || add "expected 6 drops, got $(sv drops)"; log_has "resuming in"
  NOTE="$(sv drops) drops, all resumed"; end
fi
if want a-drop-single; then   # (a) one connection, closed 3 times at 20 MB each: resumed every time, no byte twice
  begin a-drop-single; export MU_DL_SEGMENTS=1
  run_fetch big.bin "$U/m/drop=20000000x3/big.bin" "$BIG_SHA"; expect_ok big.bin "$BIG_SHA"
  served "$S" "$S"; zero 1; [ "$(sv drops)" = 3 ] || add "expected 3 drops, got $(sv drops)"
  NOTE="$(sv drops) drops (curl 18), all resumed"; end
fi
if want a-reset-single; then   # (a) the office-PC failure (curl 56 "Recv failure"), one connection, 3 TCP resets
  begin a-reset-single; export MU_DL_SEGMENTS=1 MU_DL_STALL_S=5
  run_fetch big.bin "$U/m/reset=20000000x3/big.bin" "$BIG_SHA"; expect_ok big.bin "$BIG_SHA"
  served "$S" $((S + 3 * 4 * MB)); zero 1; [ "$(sv resets)" = 3 ] || add "expected 3 resets, got $(sv resets)"
  NOTE="$(sv resets) resets, all resumed (only bytes lost in flight re-sent)"; end
fi
if want a-reset-ranges; then   # (a) TCP resets on 6 of the 8 ranges
  begin a-reset-ranges; export MU_DL_STALL_S=5
  run_fetch big.bin "$U/m/reset=4000000x6/big.bin" "$BIG_SHA"; expect_ok big.bin "$BIG_SHA"
  served "$S" $((S + 6 * 4 * MB)); zero 1; [ "$(sv resets)" = 6 ] || add "expected 6 resets, got $(sv resets)"
  NOTE="$(sv resets) resets, all resumed"; end
fi
if want b-stall-ranges; then   # (b) 2 range connections go silent (socket open) after 5 MB: cut after 4 s, resumed
  begin b-stall-ranges; export MU_DL_STALL_S=4
  run_fetch big.bin "$U/m/stall=5000000x2/big.bin" "$BIG_SHA"; expect_ok big.bin "$BIG_SHA"
  served "$S" $((S + 1)); zero 1; [ "$(sv stalls)" = 2 ] || add "expected 2 stalls, got $(sv stalls)"
  NOTE="$(sv stalls) stalls cut (MU_DL_STALL_S=4)"; end
fi
if want b-stall-single; then   # (b) one connection goes silent after 20 MB
  begin b-stall-single; export MU_DL_SEGMENTS=1 MU_DL_STALL_S=4
  run_fetch big.bin "$U/m/stall=20000000x1/big.bin" "$BIG_SHA"; expect_ok big.bin "$BIG_SHA"
  served "$S" "$S"; zero 1; [ "$(sv stalls)" = 1 ] || add "expected 1 stall, got $(sv stalls)"
  NOTE="stall cut (MU_DL_STALL_S=4)"; end
fi
if want c-norange; then   # (c) server ignores Range (200, whole file) and resets the first transfer at 10 MB:
  # probe sees 200 -> one connection; the resume gets 200 -> curl 33 -> start over (the only case that re-sends)
  begin c-norange; run_fetch big.bin "$U/m/norange+drop=10000000x1/big.bin" "$BIG_SHA"; expect_ok big.bin "$BIG_SHA"
  # 2 requests from byte 0: the first transfer and the restart (the resume between them asked for 10 MB on, got 200)
  served $((S + 10000000)) $((S + 10000000 + 16 * MB)); zero 2; log_has "cannot resume"; log_lacks "parallel ranges"
  NOTE="fallback: one connection, restart after curl 33"; end
fi
if want c-norange-ranges; then   # (c) server honours only the 1-byte probe, answers every range with the whole file
  begin c-norange-ranges; run_fetch big.bin "$U/m/rangeprobe/big.bin" "$BIG_SHA"; expect_ok big.bin "$BIG_SHA"
  served "$S" $((S + 16 * MB)); log_has "does not serve ranges reliably"; log_has "continuing on one connection"
  NOTE="ranges refused (curl 63, nothing kept) -> one connection"; end
fi
if want d-416; then   # (d) every Range gets 416, and a stale 1 MB garbage .part is in the cache
  begin d-416; head -c "$MB" /dev/urandom > "$DL/big.bin.part"
  run_fetch big.bin "$U/m/r416/big.bin" "$BIG_SHA"; expect_ok big.bin "$BIG_SHA"
  served "$S" "$S"; zero 1; log_has "cannot resume"
  NOTE="stale partial discarded (416), then one connection"; end
fi
if want d-416-fresh; then   # (d) empty cache, one connection dropped at 10 MB, and the resume answered 416: curl takes
  # that for success (rc 0), so the size must be checked against the 416's Content-Range: start over, no SHA mismatch
  begin d-416-fresh; export MU_DL_SEGMENTS=1
  run_fetch big.bin "$U/m/r416+drop=10000000x1/big.bin" "$BIG_SHA"; expect_ok big.bin "$BIG_SHA"
  served $((S + 10000000)) $((S + 10000000 + 16 * MB)); zero 2; log_has "cannot resume"; log_lacks "does not verify"
  NOTE="416 to the resume -> restart"; end
fi
if want c-one-200; then   # (c) one range request (the 4th) answered with a whole-file 200 (a CDN cache miss): that
  # answer is dropped and the range asked again; the ranges carry on, nothing is re-downloaded
  begin c-one-200; run_fetch big.bin "$U/m/full200=4-4/big.bin" "$BIG_SHA"; expect_ok big.bin "$BIG_SHA"
  served "$S" $((S + 16 * MB)); zero 1; [ "$(sv ranges_ignored)" = 1 ] || add "expected 1 ignored range, got $(sv ranges_ignored)"
  log_has "instead of the range"; log_lacks "continuing on one connection"
  NOTE="200 to one range: asked again, ranges kept"; end
fi
if want c-keep-prefix; then   # (c) the 8th range request drops at 1 MB and the next three requests (its resumes) get
  # whole-file 200s: ranges are given up, and the ranges that join up from byte 0 are kept for the one connection
  begin c-keep-prefix; run_fetch big.bin "$U/m/dropnth=8:1000000+full200=9-11/big.bin" "$BIG_SHA"; expect_ok big.bin "$BIG_SHA"
  # served: the file, plus what follows the kept part once more (one connection), plus the cut-off 200s (<= 24 MB)
  P=$(sed -n 's/.*: \([0-9.]*\) MB from byte 0 kept.*/\1/p' "$TF/case.log" | head -1)
  P=$(awk -v m="${P:-0}" 'BEGIN { printf "%d", m * 1048576 }'); [ "$P" -ge $((10 * MB)) ] || add "kept only $P bytes"
  served "$S" $((2 * S - P + 24 * MB)); zero 1; log_has "continuing on one connection"
  NOTE="fallback kept $(mb "$P") MB, fetched $(mb $((S - P))) MB again"; end
fi
if want i-maxconn; then   # a server that allows 4 connections per client (503 for more), 1 MB/s each, and a short
  # MU_DL_IDLE_S=3 (each range needs 10 s): the refused ranges keep trying while the others deliver, then get a slot
  begin i-maxconn; export MU_DL_IDLE_S=3 MU_DL_BACKOFF_MAX=1
  run_fetch big.bin "$U/m/maxconn=4+throttle=1000000/big.bin" "$BIG_SHA"; expect_ok big.bin "$BIG_SHA"
  served "$S" $((S + 1)); zero 1; [ "$(sv status_503)" -gt 0 ] || add "no 503 seen"; log_lacks "giving up"
  NOTE="$(sv status_503)x 503, waited for slots"; end
fi
if want i-maxconn-rounds; then   # as above, but MU_DL_MAX_ATTEMPTS=2: the refused ranges give up, and a second round
  # runs them once the first 4 are done
  begin i-maxconn-rounds; export MU_DL_MAX_ATTEMPTS=2 MU_DL_BACKOFF_MAX=1
  run_fetch big.bin "$U/m/maxconn=4+throttle=2000000/big.bin" "$BIG_SHA"; expect_ok big.bin "$BIG_SHA"
  served "$S" $((S + 1)); zero 1; log_has "running them again (round 2)"
  NOTE="$(sv status_503)x 503, refused ranges in round 2"; end
fi
if want j-proxy; then   # through an HTTP CONNECT proxy (.curlrc: proxytunnel) that closes the 5th tunnel right after
  # "200 Connection established": a transient failure, not a refused range (the 200 is the proxy's, not the server's)
  begin j-proxy; curl -s "$U/_proxyfault/5" >/dev/null
  mkdir -p "$TF/curlhome"; printf 'proxytunnel\nproxy = "http://127.0.0.1:%s"\nnoproxy = ""\n' "$PORT" > "$TF/curlhome/.curlrc"
  export CURL_HOME="$TF/curlhome"; run_fetch big.bin "$U/m/plain/big.bin" "$BIG_SHA"; unset CURL_HOME
  expect_ok big.bin "$BIG_SHA"; served "$S" $((S + 1)); zero 1
  [ "$(sv proxy_faults)" = 1 ] || add "expected 1 broken tunnel, got $(sv proxy_faults) (tunnels: $(sv proxy_tunnels))"
  log_lacks "does not serve ranges"; log_lacks "instead of the range"; log_has "resuming in"
  NOTE="$(sv proxy_tunnels) tunnels, 1 broken: resumed"; end
fi
if want k-outage; then   # default settings: 8 ranges at 1 MB/s each; the server goes away at 3 s (connections closed,
  # then refused) and comes back on the same port 40 s later: every range waits and resumes (gave up at ~31 s before)
  begin k-outage; rm -f "$TF/port2"
  python3 -I "$REPO/tools/test-fetch/server.py" "$TF/srv" "$TF/port2" > "$TF/server2.log" 2>&1 & P2=$!
  disown "$P2" 2>/dev/null || true   # it is killed on purpose: no "Terminated" report
  for _ in $(seq 1 100); do [ -s "$TF/port2" ] && break; sleep 0.1; done; PORT2=$(cat "$TF/port2")
  ( trap - ERR; set +e; sleep 3; kill "$P2"; wait "$P2" 2>/dev/null; sleep 40
    exec python3 -I "$REPO/tools/test-fetch/server.py" "$TF/srv" "$TF/port2" 80 3 "$PORT2" >> "$TF/server2.log" 2>&1 ) & P3=$!
  run_fetch big.bin "http://127.0.0.1:$PORT2/m/throttle=1000000/big.bin" "$BIG_SHA"; expect_ok big.bin "$BIG_SHA"
  pkill -P "$P3" 2>/dev/null || true; kill "$P3" 2>/dev/null || true; wait "$P3" 2>/dev/null || true
  log_has "resuming in"; log_lacks "giving up"; STATS=""
  NOTE="40 s outage survived"; end
fi
if want e-redirect; then   # (e) a redirect hop: resolved once by the probe; the 8 ranges go straight to the target
  begin e-redirect; run_fetch big.bin "$U/r/plain/big.bin" "$BIG_SHA"; expect_ok big.bin "$BIG_SHA"
  served "$S" $((S + 1)); zero 1; [ "$(sv redirects)" = 1 ] || add "expected 1 redirect, got $(sv redirects)"
  [ "$(sv data_requests)" = 8 ] || add "expected 8 range requests, got $(sv data_requests)"
  NOTE="1 redirect for 8 ranges"; end
fi
if want e-signed-expiry; then   # (e) GitHub-style signed redirect target valid for 2 s; 2 MB/s per connection and
  # every range drops at 3 MB, so each resume comes after expiry: 403 -> resolve the original URL again -> continue
  begin e-signed-expiry
  run_fetch big.bin "$U/gh/expire=2+throttle=2000000+drop=3000000x8/big.bin" "$BIG_SHA"; expect_ok big.bin "$BIG_SHA"
  served "$S" $((S + 64)); zero 1
  [ "$(sv status_403)" -gt 0 ] || add "no 403 seen (test did not exercise expiry)"
  [ "$(sv redirects)" -gt 1 ] || add "URL was never resolved again"
  NOTE="$(sv status_403)x 403 -> $(( $(sv redirects) - 1 )) re-resolves"; end
fi
if want g-adopt-prefix; then   # a 30 MB .part left by an earlier run (old installer): kept as the start of range 1
  begin g-adopt-prefix; head -c $((30 * MB)) "$TF/srv/big.bin" > "$DL/big.bin.part"
  run_fetch big.bin "$U/m/plain/big.bin" "$BIG_SHA"; expect_ok big.bin "$BIG_SHA"
  served $((S - 30 * MB)) $((S - 30 * MB + 1)); log_has "from an earlier attempt kept"
  NOTE="only the missing 50 MB fetched"; end
fi
if want f-throttle; then   # (f) 4 MB/s per connection: one connection vs 8 ranges
  begin f-throttle-1conn; export MU_DL_SEGMENTS=1
  run_fetch big.bin "$U/m/throttle=4000000/big.bin" "$BIG_SHA"; expect_ok big.bin "$BIG_SHA"; served "$S" "$S"
  T1=$SECS; NOTE="MU_DL_SEGMENTS=1"; end
  begin f-throttle-8ranges
  run_fetch big.bin "$U/m/throttle=4000000/big.bin" "$BIG_SHA"; expect_ok big.bin "$BIG_SHA"; served "$S" $((S + 1))
  SP=$(awk -v a="$T1" -v b="$SECS" 'BEGIN { printf "%.1f", (b > 0 ? a / b : 0) }')
  awk -v s="$SP" 'BEGIN { exit !(s >= 4) }' || add "speed-up only ${SP}x"
  NOTE="${SP}x faster than one connection"; end
fi
if want h-interrupt-fg; then   # Ctrl-C (SIGINT to the process group) during a ranged fetch in the main shell
  begin h-interrupt-fg
  t0=$(now)
  interrupt "$BASH" "$0" --child-fg big.bin "$U/m/throttle=1000000/big.bin" "$BIG_SHA" >> "$TF/case.log" 2>&1 || add "child did not exit"
  left=$(procs); [ -z "$left" ] || { sleep 1; left=$(procs); }
  [ -z "$left" ] || { add "orphaned curl processes: $left"; kill $left 2>/dev/null || true; }
  [ -f "$DL/big.bin.part.layout" ] || add "range state not kept for a re-run"
  kept=0; for f in "$DL"/big.bin.part.[0-9][0-9]; do if [ -f "$f" ]; then kept=$((kept + $(_dl_size "$f"))); fi; done
  curl -s "$U/_reset" >/dev/null; : > "$TF/fail.msg"
  run_fetch big.bin "$U/m/plain/big.bin" "$BIG_SHA"; expect_ok big.bin "$BIG_SHA"   # the re-run resumes
  served $((S - kept)) $((S - kept + 1)); [ "$kept" -gt $((5 * MB)) ] || add "only $kept bytes kept"
  NOTE="no orphans; re-run fetched $(mb $((S - kept))) of $(mb "$S") MB"; end
fi
if want h-interrupt-job; then   # Ctrl-C while the fetch runs in a background job: the installer's cleanup kills the tree
  begin h-interrupt-job
  interrupt "$BASH" "$0" --child-job big.bin "$U/m/throttle=1000000/big.bin" "$BIG_SHA" >> "$TF/case.log" 2>&1 || add "child did not exit"
  left=$(procs); [ -z "$left" ] || { sleep 1; left=$(procs); }
  [ -z "$left" ] || { add "orphaned curl processes: $left"; kill $left 2>/dev/null || true; }
  [ -f "$DL/big.bin.part.layout" ] || add "range state not kept for a re-run"
  SECS=-; STATS="$(curl -s "$U/_stats")"; NOTE="no orphans after the installer's kill_tree"; end
fi
if want fail-404; then   # a permanent error fails at once, with the installer's message, leaving nothing behind
  begin fail-404; run_fetch nope.bin "$U/m/plain/nope.bin" "$BIG_SHA"; expect_fail "download failed: $U/m/plain/nope.bin"
  awk -v s="$SECS" 'BEGIN { exit !(s < 5) }' || add "took ${SECS}s"; NOTE="fails fast"; end
fi
if want fail-offline; then   # nothing listening: bounded by time without data (MU_DL_IDLE_S=4, backoff 1 s), then fail
  begin fail-offline; export MU_DL_IDLE_S=4 MU_DL_BACKOFF_MAX=1
  run_fetch small.bin "http://127.0.0.1:9/small.bin" "$SMALL_SHA"; expect_fail "download failed"
  log_has "giving up after"; awk -v s="$SECS" 'BEGIN { exit !(s >= 3 && s < 15) }' || add "took ${SECS}s"   # (whole seconds: 3-4 s)
  NOTE="gave up after 4 s without data"; end
fi
if want fail-outage-ranges; then   # the server goes away for good during a ranged fetch (MU_DL_IDLE_S=4, backoff 1 s):
  # every range gives up 4 s after the last byte, no second round (the data stopped for all), and the range state stays
  begin fail-outage-ranges; export MU_DL_IDLE_S=4 MU_DL_BACKOFF_MAX=1; rm -f "$TF/port3"
  python3 -I "$REPO/tools/test-fetch/server.py" "$TF/srv" "$TF/port3" > "$TF/server3.log" 2>&1 & P3=$!
  disown "$P3" 2>/dev/null || true
  for _ in $(seq 1 100); do [ -s "$TF/port3" ] && break; sleep 0.1; done
  ( trap - ERR; set +e; sleep 2; kill "$P3" ) &
  run_fetch big.bin "http://127.0.0.1:$(cat "$TF/port3")/m/throttle=1000000/big.bin" "$BIG_SHA"; wait $! || true
  [ "$RC" != 0 ] || add "fetch succeeded"; [ -z "$OUT" ] || add "stdout not empty: '$OUT'"
  grep -q "download failed" "$TF/fail.msg" || add "no 'download failed' message"; no_err_trap
  [ -f "$DL/big.bin.part.layout" ] || add "range state not kept for a re-run"
  case " $(leftovers) " in *.hdr*|*norange*) add "files left: $(leftovers)" ;; esac
  awk -v s="$SECS" 'BEGIN { exit !(s >= 4 && s < 15) }' || add "took ${SECS}s"; log_lacks "round 2"
  STATS=""; NOTE="gave up 4 s after the last byte; ranges kept"; end
fi
if want fail-sha; then   # a wrong checksum: fails with both values, removes the bad file
  begin fail-sha; run_fetch small.bin "$U/m/plain/small.bin" 0000000000000000000000000000000000000000000000000000000000000000
  expect_fail "SHA-256 mismatch for small.bin: got $SMALL_SHA"; NOTE="mismatch reported, file removed"; end
fi
rm -rf "$DL"
