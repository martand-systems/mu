#!/bin/bash
# The page tests' must-fail runs (docs/design/dashboard.md §7.1): each Playwright D-test is run again with its fault
# put in, and must then fail, on its own assertion (the assertion's message must be in the failure output). A page fault is a throw-away build with MU_DASH_FAULT (the real
# build has none: the faults are build-time constants, false, and their code is removed); a server fault is put into
# every dev server by the launcher (MU_JOBS_FAULT). Also the Vitest must-fails (D37, D38, D41, D43 and the component
# parts) with FRONT=1.
#
#   tools/test-dashboard/e2e-faults.sh [NAME-FILTER]     (CI: npm directly; on the Mac: through tools/dashboard-npm)
#   FRONT=1 tools/test-dashboard/e2e-faults.sh           the Vitest must-fails only
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/../.." && pwd)"; DASH="$REPO/dashboard"
FILTER="${1:-}"
PASS=0; FAILN=0; FAILED=""
ok()  { PASS=$((PASS + 1)); printf '  ok    %s\n' "$1"; }
bad() { FAILN=$((FAILN + 1)); FAILED="$FAILED
  - $1"; printf '  FAIL  %s\n' "$1"; [ -z "${2:-}" ] || printf '%s\n' "$2" | tail -40 | sed 's/^/        | /'; }

if [ "$(uname -s)" = Darwin ] && [ -z "${CI:-}" ]; then
  MU_CACHE="${MU_CACHE:-$HOME/Library/Caches/Mu}"
  WORK="$MU_CACHE/dev/tmp/faults.$$"; mkdir -p "$WORK"
  npm_exec() { "$REPO/tools/dashboard-npm" npm exec --no-install -- "$@"; }
  e2e() { DASH_E2E_STATIC="$1" DASH_E2E_SERVER_FAULTS="$2" DASH_E2E_ENV="$3" "$REPO/tools/dashboard-npm" e2e "${@:4}"; }
else
  WORK="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/faults.XXXXXX")"
  npm_exec() { (cd "$DASH" && npx --no-install "$@"); }
  e2e() { STATIC="$1" SERVER_FAULTS="$2" SERVER_ENV="$3" PY="${PY:-python3}" /bin/bash "$HERE/e2e-run.sh" "${@:4}"; }
fi
trap 'rm -rf "$WORK"' EXIT

# On the Mac, $WORK is a folder the sandboxed npm can write: this (unsandboxed) script never opens a file in it by path
# (a symlink planted there by build-time code would turn the redirect into a write anywhere, tools/dashboard-npm). The
# build's output is kept in memory; the fault builds are only handed to the e2e launcher, which serves regular files
# only, opened with O_NOFOLLOW (src/mu/dashboard.py load_static).
build() {   # build FAULT -> the folder of a fault build
  local out="$WORK/build-$1" log
  [ -d "$out" ] && [ ! -L "$out" ] && { printf '%s' "$out"; return 0; }
  if ! log="$(MU_DASH_FAULT="$1" MU_DASH_OUT="$out" npm_exec vite build --logLevel error 2>&1)"; then
    printf '%s\n' "$log" >&2; return 1
  fi
  printf '%s' "$out"
}

# must_fail TEST-ID PROJECT MESSAGE [page=F] [server=F] [env=NAME=V]: run the test with the faults put in; it must fail,
# the failure must be that test's, and its output must hold MESSAGE: the message of the assertion the fault is meant to
# break (each key expect in dtests.spec.ts carries "Dnn: …"), so a fault that only breaks something else does not count.
must_fail() {
  local id="$1" proj="$2" msg="$3" static="$REPO/share/dashboard" sf="" env="" pf="" out rc x
  shift 3
  for x in "$@"; do
    case "$x" in page=*) pf="${x#page=}" ;; server=*) sf="${x#server=}" ;; env=*) env="${x#env=}" ;; esac
  done
  case "$pf$sf$env$id" in *"$FILTER"*) ;; *) return 0 ;; esac
  local what="${pf:+page fault $pf}${sf:+${pf:+ + }server fault $sf}${env:+env $env}"
  if [ -n "$pf" ]; then static="$(build "$pf")" || { bad "must-fail $id with $what: the fault build failed"; return; }; fi
  out="$(e2e "$static" "$sf" "$env" --project="$proj" -g "$id " --reporter=line --output "$WORK/results-$pf$sf" 2>&1)"; rc=$?
  if [ "$rc" != 0 ] && printf '%s\n' "$out" | grep -qE "[0-9]+\) \[$proj\] .*› $id " && ! printf '%s\n' "$out" | grep -q "launcher" \
     && printf '%s\n' "$out" | grep -qF -- "$msg"; then
    ok "must-fail: $id ($proj) fails on \"$msg\" with the $what"
  else
    bad "must-fail: $id ($proj) with the $what: it did not fail on \"$msg\" (exit $rc)" "$out"
  fi
}

nocolor() { sed $'s/\x1b\\[[0-9;]*m//g'; }

vitest_must_fail() {   # vitest_must_fail FAULT FILE [ENV=VALUE]: the Vitest file must fail with the fault
  local f="$1" file="$2" out rc
  case "$f$file" in *"$FILTER"*) ;; *) return 0 ;; esac
  out="$(cd "$DASH" && env ${3:+"$3"} NO_COLOR=1 FORCE_COLOR=0 MU_DASH_FAULT="$f" npx --no-install vitest run "$file" 2>&1 | nocolor)"; rc=$?
  if [ "$rc" != 0 ] && printf '%s\n' "$out" | grep -qE "Tests +[0-9]+ failed"; then ok "must-fail: $file fails with the fault $f"
  else bad "must-fail: $file with the fault $f: nothing failed (exit $rc)" "$out"; fi
}

vitest_must_fail_on() {   # vitest_must_fail_on FAULT FILE TEST: that test (by its name) must be among the failures
  local f="$1" file="$2" name="$3" out rc
  case "$f$file" in *"$FILTER"*) ;; *) return 0 ;; esac
  out="$(cd "$DASH" && NO_COLOR=1 FORCE_COLOR=0 MU_DASH_FAULT="$f" npx --no-install vitest run "$file" 2>&1 | nocolor)"; rc=$?
  if [ "$rc" != 0 ] && printf '%s\n' "$out" | grep -F "FAIL " | grep -qF -- "$name"; then ok "must-fail: $file fails on \"$name\" with the fault $f"
  else bad "must-fail: $file with the fault $f: \"$name\" did not fail (exit $rc)" "$out"; fi
}

if [ "${FRONT:-0}" = 1 ]; then
  echo "Vitest must-fails"
  [ "$(uname -s)" = Darwin ] && [ -z "${CI:-}" ] && { echo "run FRONT=1 in CI (npm runs on the Mac only through tools/dashboard-npm)"; exit 2; }
  vitest_must_fail interpolate-gap test/series.test.ts                 # D37
  vitest_must_fail network-mode-default test/polling.test.tsx          # D41
  vitest_must_fail faint-series test/contrast.test.ts                  # D43
  vitest_must_fail mac-swap-mem test/components.test.tsx               # D08's card part
  vitest_must_fail refused-as-offline test/components.test.tsx         # D18's card part
  vitest_must_fail keep-fragment test/token.test.ts                    # D15's unit part
  # phase 1b: scheduler release 2 on the page (D48's decoder part, D49), each on the test it targets
  vitest_must_fail_on paused-as-running test/r2.test.ts "a frozen lender is 'paused'"
  vitest_must_fail_on count-paused-in-run test/r2.test.ts "the time bar counts run time"
  vitest_must_fail_on paused-cpu-zero test/r2.test.ts "paused samples break the cores series"
  # D38: the server's rename-field fault, through fixtures the faulty server emits
  fx="$WORK/fixtures-rename-field"; mkdir -p "$fx"
  if MU_JOBS_FAULT=rename-field python3 -I -S "$REPO/src/mu/dashfake.py" fixtures "$fx" > /dev/null 2>&1; then
    out="$(cd "$DASH" && NO_COLOR=1 FORCE_COLOR=0 MU_DASH_FIXTURES="$fx" npx --no-install vitest run test/decode.test.ts 2>&1 | nocolor)"
    if printf '%s\n' "$out" | grep -qE "Tests +[0-9]+ failed"; then ok "must-fail: test/decode.test.ts fails on the fixtures of a server with rename-field (D38)"
    else bad "must-fail: test/decode.test.ts with rename-field fixtures: nothing failed" "$out"; fi
  else bad "must-fail D38: the faulty server's fixtures could not be written"; fi
else
  echo "Playwright must-fails"
  V="CSP or Trusted Types violations (D29)"
  must_fail D04 chromium "D04: the card says the node is behind this Mac"  page=assume-features
  must_fail D04b chromium "D04b: the r1 node's card asks for an update"     server=features-any
  must_fail D48 chromium "D48: the frozen lender shows paused, not running" page=paused-as-running
  must_fail D48 webkit   "D48: the frozen lender shows paused, not running" page=paused-as-running
  must_fail D39 chromium "D39: running within 20 s"                         env=MU_DASH_OLD_STEP=60
  must_fail D05 chromium "D05: lab-pc's jobs are listed under lab-pc"       server=node-from-config
  must_fail D06 chromium "D06: the card shows the node's memory"            server=swap-mem-fields
  must_fail D07 chromium "D07: the card's figures turn stale"               server=serve-last-as-live
  must_fail D14 chromium "D14: the no-token page"                           server=fixed-token
  must_fail D15 chromium "D15: no token in the address bar"                 page=keep-fragment
  must_fail D15 chromium "D15: no token in the history"                     page=push-fragment
  must_fail D16 chromium "D16: nothing is created from node text and nothing runs" page=inner-html server=no-trusted-types
  must_fail D16 webkit   "D16: nothing is created from node text and nothing runs" page=inner-html server=no-trusted-types
  must_fail D18 chromium "D18: refused is red"                              page=refused-as-offline
  must_fail D18 chromium "$V"                                               page=violation-offline
  must_fail D26 chromium "D26: clickable elements that are not controls"    page=div-button
  must_fail D26 chromium "D26: axe"                                         page=faint-text
  must_fail D29 chromium "$V"                                               page=style-tag
  must_fail D29 webkit   "$V"                                               page=style-tag
  must_fail D29 chromium "$V"                                               page=radix-scrollarea
  must_fail D29 chromium "$V"                                               page=inner-html-lib
  must_fail D29 webkit   "$V"                                               page=inner-html-lib
  must_fail D29 webkit   "$V"                                               page=violation-offline
  must_fail D29 chromium "D29: the CSP"                                     server=csp-unsafe-inline
  must_fail D42 chromium "D42: data-theme is set before the app's module"   page=theme-in-bundle
  must_fail D42 webkit   "D42: data-theme is set before the app's module"   page=theme-in-bundle
  must_fail D42 chromium "D42: the chart redraws in the light theme's colours" page=chart-fixed-colours
  must_fail D42 webkit   "D42: the chart redraws in the light theme's colours" page=chart-fixed-colours
  must_fail D46 chromium "D46: the error page holds no job text"            page=boundary-shows-error
  must_fail D46 chromium "D46: server down is not node offline"             page=server-down-as-offline
  must_fail D47 webkit   "D47: copied inside the gesture"                   page=copy-after-await
  must_fail D47 webkit   "D47: a refused clipboard selects the command"     page=copy-no-fallback
fi

printf '\n%d ok, %d failed\n' "$PASS" "$FAILN"
[ "$FAILN" = 0 ] || { printf 'failed:%s\n' "$FAILED"; exit 1; }
