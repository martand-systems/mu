#!/bin/bash
# The page tests' must-fail runs (docs/design/dashboard.md §7.1): each Playwright D-test is run again with its fault
# put in, and must then fail, on its own assertion. A page fault is a throw-away build with MU_DASH_FAULT (the real
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

build() {   # build FAULT -> the folder of a fault build
  local out="$WORK/build-$1"
  [ -d "$out" ] && { printf '%s' "$out"; return 0; }
  if ! MU_DASH_FAULT="$1" MU_DASH_OUT="$out" npm_exec vite build --logLevel error > "$WORK/build-$1.log" 2>&1; then
    cat "$WORK/build-$1.log" >&2; return 1
  fi
  printf '%s' "$out"
}

# must_fail KIND FAULT TEST-ID PROJECT: run the test with the fault; it must fail, and the failure must be that test's
must_fail() {
  local kind="$1" f="$2" id="$3" proj="$4" static="$REPO/share/dashboard" sf="" env="" out rc
  case "$f$id" in *"$FILTER"*) ;; *) return 0 ;; esac
  case "$kind" in
    page) static="$(build "$f")" || { bad "must-fail $id with page fault $f: the fault build failed"; return; } ;;
    server) sf="$f" ;;
    env) env="$f" ;;
  esac
  out="$(e2e "$static" "$sf" "$env" --project="$proj" -g "$id " --reporter=line --output "$WORK/results-$f" 2>&1)"; rc=$?
  if [ "$rc" != 0 ] && printf '%s\n' "$out" | grep -qE "[0-9]+\) \[$proj\] .*› $id " && ! printf '%s\n' "$out" | grep -q "launcher"; then
    ok "must-fail: $id ($proj) fails with the $kind fault $f"
  else
    bad "must-fail: $id ($proj) with the $kind fault $f: it did not fail as it must (exit $rc)" "$out"
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

if [ "${FRONT:-0}" = 1 ]; then
  echo "Vitest must-fails"
  [ "$(uname -s)" = Darwin ] && [ -z "${CI:-}" ] && { echo "run FRONT=1 in CI (npm runs on the Mac only through tools/dashboard-npm)"; exit 2; }
  vitest_must_fail interpolate-gap test/series.test.ts                 # D37
  vitest_must_fail network-mode-default test/polling.test.tsx          # D41
  vitest_must_fail faint-series test/contrast.test.ts                  # D43
  vitest_must_fail mac-swap-mem test/components.test.tsx               # D08's card part
  vitest_must_fail refused-as-offline test/components.test.tsx         # D18's card part
  vitest_must_fail keep-fragment test/token.test.ts                    # D15's unit part
  # D38: the server's rename-field fault, through fixtures the faulty server emits
  fx="$WORK/fixtures-rename-field"; mkdir -p "$fx"
  if MU_JOBS_FAULT=rename-field python3 -I -S "$REPO/src/mu/dashfake.py" fixtures "$fx" > /dev/null 2>&1; then
    out="$(cd "$DASH" && NO_COLOR=1 FORCE_COLOR=0 MU_DASH_FIXTURES="$fx" npx --no-install vitest run test/decode.test.ts 2>&1 | nocolor)"
    if printf '%s\n' "$out" | grep -qE "Tests +[0-9]+ failed"; then ok "must-fail: test/decode.test.ts fails on the fixtures of a server with rename-field (D38)"
    else bad "must-fail: test/decode.test.ts with rename-field fixtures: nothing failed" "$out"; fi
  else bad "must-fail D38: the faulty server's fixtures could not be written"; fi
else
  echo "Playwright must-fails"
  must_fail page   assume-features        D04 chromium
  must_fail env    MU_DASH_OLD_STEP=60    D39 chromium
  must_fail server node-from-config       D05 chromium
  must_fail server swap-mem-fields        D06 chromium
  must_fail server serve-last-as-live     D07 chromium
  must_fail server fixed-token            D14 chromium
  must_fail page   keep-fragment          D15 chromium
  must_fail page   inner-html             D16 chromium
  must_fail page   inner-html             D16 webkit
  must_fail page   refused-as-offline     D18 chromium
  must_fail page   div-button             D26 chromium
  must_fail page   faint-text             D26 chromium
  must_fail page   style-tag              D29 chromium
  must_fail page   style-tag              D29 webkit
  must_fail page   radix-scrollarea       D29 chromium
  must_fail page   inner-html-lib         D29 chromium
  must_fail page   inner-html-lib         D29 webkit
  must_fail server csp-unsafe-inline      D29 chromium
  must_fail page   theme-in-bundle        D42 chromium
  must_fail page   chart-fixed-colours    D42 chromium
  must_fail page   chart-fixed-colours    D42 webkit
  must_fail page   boundary-shows-error   D46 chromium
  must_fail page   server-down-as-offline D46 chromium
  must_fail page   copy-after-await       D47 webkit
fi

printf '\n%d ok, %d failed\n' "$PASS" "$FAILN"
[ "$FAILN" = 0 ] || { printf 'failed:%s\n' "$FAILED"; exit 1; }
