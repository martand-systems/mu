#!/bin/bash
# Tests of the dashboard's server, API, node reader and CLI (docs/design/dashboard.md §7.1, the Python level). No real
# node, no network beyond 127.0.0.1, no real LaunchAgent: a throw-away HOME and MU_HOME (with a space in the path),
# fake nodes (src/mu/dashfake.py), stubs for launchctl and the browser opener. Each check that guards against a fault
# is run again with the fault put in (MU_JOBS_FAULT), and must then fail ("must-fail" lines).
#
#   /bin/bash tools/test-dashboard/run.sh            (MU_TEST_PYTHONS="py1 py2" chooses the Pythons; the first must have
#                                                     psutil for D08, else D08 is skipped unless MU_TEST_NEED_PSUTIL=1;
#                                                     QUICK=1 skips the slow sections: lifecycle, sampler)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/../.." && pwd)"
REAL_HOME="$HOME"
ROOT="$(mktemp -d "${TMPDIR:-/tmp}/mu dash test.XXXXXX")"
cleanup() { if [ "${KEEP:-0}" = 1 ]; then echo "kept: $ROOT"; else rm -rf "${ROOT:?}"; fi; }
trap cleanup EXIT
PASS=0; FAILN=0; FAILED=""; SKIPN=0; SKIPPED=""
skipped() { SKIPN=$((SKIPN + 1)); SKIPPED="$SKIPPED
  - $1"; printf '  skip  %s\n' "$1"; }
ok()   { PASS=$((PASS + 1)); printf '  ok    %s\n' "$1"; }
bad()  { FAILN=$((FAILN + 1)); FAILED="$FAILED
  - $1"; printf '  FAIL  %s\n' "$1"; [ -z "${2:-}" ] || printf '%s\n' "$2" | sed 's/^/        | /' | head -30; }
section() { printf '\n%s\n' "$1"; }
py_section() {   # py_section PY SECTION: its ok/FAIL lines count here
  local py="$1" out rc; shift
  out="$(cd "$ROOT" && "$py" -I -S "$HERE/unit.py" "$@" 2>&1)"; rc=$?
  printf '%s\n' "$out" | while IFS= read -r l; do printf '  %s\n' "$l"; done
  PASS=$((PASS + $(printf '%s\n' "$out" | grep -c '^ok ')))
  while IFS= read -r l; do SKIPN=$((SKIPN + 1)); SKIPPED="$SKIPPED
  - ${l#skip  } [$*, $(basename "$py")]"; done < <(printf '%s\n' "$out" | grep '^skip ')
  if [ "$rc" != 0 ] || printf '%s\n' "$out" | grep -q '^Traceback'; then
    FAILN=$((FAILN + 1)); FAILED="$FAILED
  - unit.py $* ($(basename "$py"), exit $rc)"
  fi
}
must_fail() {    # must_fail FAULT PY SECTION: with MU_JOBS_FAULT=FAULT the section must report at least one FAIL
  local f="$1" py="$2" out n; shift 2
  out="$(cd "$ROOT" && MU_JOBS_FAULT="$f" "$py" -I -S "$HERE/unit.py" "$@" 2>&1)"
  n="$(printf '%s\n' "$out" | grep -c '^FAIL ')"
  if [ "$n" -gt 0 ]; then ok "must-fail: $*, with fault $f ($n check(s) failed, as they must)"
  else bad "must-fail: $*, with fault $f: nothing failed" "$out"; fi
}

PYS="${MU_TEST_PYTHONS:-}"
if [ -z "$PYS" ]; then
  case "$(uname -s)" in Darwin) d="$(cat "$REAL_HOME/Library/Mu/default" 2>/dev/null)"; t="$REAL_HOME/Library/Mu/toolchains/$d/env/bin/python" ;;
    *) d="$(cat "$REAL_HOME/.local/share/mu/default" 2>/dev/null)"; t="$REAL_HOME/.local/share/mu/toolchains/$d/env/bin/python" ;; esac
  PYS="$(command -v python3)"
  [ -z "$d" ] || [ ! -x "$t" ] || PYS="$t $PYS"
fi
PY1="${PYS%% *}"

export HOME="$ROOT/home"; mkdir -p "$HOME"
export MU_HOME="$ROOT/mu home"; mkdir -p "$MU_HOME"
export NO_COLOR=1
unset MU_JOBS_FAULT MU_NODE_FAULT MU_DASH_BATTERY MU_LAUNCHCTL MU_LAUNCH_AGENTS_DIR MU_OPENER MU_VERSION 2>/dev/null || true

FAST="allowlist controlpath devguard submitline index oldnode errors idle battery wake nodes stale logs fixtures agent http open"
SLOW="lifecycle sampler"
[ "${QUICK:-0}" = 1 ] && SLOW=""
for py in $PYS; do
  section "Python: $py ($("$py" -c 'import sys; print(sys.version.split()[0])'))"
  for s in $FAST $SLOW build; do
    [ "$py" = "$PY1" ] || case " $SLOW " in *" $s "*) continue ;; esac
    section "  $s"; py_section "$py" "$s"
  done
done

section "Faults: each must make its checks fail (Python: $PY1)"
must_fail allow-cancel        "$PY1" allowlist
must_fail shared-controlpath  "$PY1" controlpath
must_fail no-host-check       "$PY1" http
must_fail no-token            "$PY1" http
must_fail fixed-token         "$PY1" http
must_fail csp-unsafe-inline   "$PY1" http
if [ "$(uname -s)" = Darwin ] && /usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate 2>/dev/null | grep -q enabled \
   && [ -z "${CI:-}" ]; then
  skipped "must-fail: http, with fault bind-all (the application firewall is on here: a listener on 0.0.0.0 would ask the user; CI runs it)"
else
  must_fail bind-all          "$PY1" http
fi
must_fail default-send-error  "$PY1" http
must_fail static-follow-links "$PY1" http
must_fail fault-guard-off     "$PY1" devguard
must_fail open-url-argv       "$PY1" open
must_fail open-trusts-port    "$PY1" open
must_fail open-passes-faults  "$PY1" open
must_fail submit-waits        "$PY1" submitline
must_fail no-index            "$PY1" index
must_fail default-limit       "$PY1" index
must_fail poll-list-2s        "$PY1" oldnode
must_fail retry-refused       "$PY1" errors
must_fail poll-when-idle      "$PY1" idle
must_fail ignore-battery      "$PY1" battery
must_fail node-from-config    "$PY1" nodes
must_fail swap-mem-fields     "$PY1" nodes
must_fail serve-last-as-live  "$PY1" stale
must_fail rename-field        "$PY1" fixtures
must_fail stale-build         "$PY1" build
[ -n "$SLOW" ] && must_fail no-lock "$PY1" lifecycle
if [ -n "$SLOW" ] && "$PY1" -I -S -c "import sys; sys.path.insert(0, '$REPO/src'); from mu import dashboard; sys.exit(dashboard.load_psutil() is None)" 2>/dev/null; then
  must_fail cpu-first-call    "$PY1" sampler
elif [ -n "$SLOW" ]; then
  if [ "${MU_TEST_NEED_PSUTIL:-0}" = 1 ]; then bad "must-fail: sampler, with fault cpu-first-call: no psutil in $PY1"
  else skipped "must-fail: sampler, with fault cpu-first-call (no psutil in $PY1)"; fi
fi

section "Repository checks"
r1="$(sed -n 's/^DISPATCHER_REV=\([0-9]*\).*/\1/p' "$REPO/install/install.sh")"; r2="$(sed -n 's/^MU_DISPATCHER_REV=\([0-9]*\).*/\1/p' "$REPO/bin/mu")"
if [ "$r1" = "$r2" ]; then ok "dispatcher revs match (bin/mu $r2, install.sh $r1)"; else bad "dispatcher revs differ: bin/mu $r2, install.sh $r1"; fi
if grep -q 'compute|submit|status|log|fetch|cancel|relay|node|dashboard) infra' "$REPO/bin/mu"; then ok "bin/mu hands 'dashboard' to the default toolchain"
else bad "bin/mu does not route 'dashboard'"; fi
for x in ./dashboard/node_modules ./dashboard/test-results ./dashboard/playwright-report; do
  if grep -q -- "--exclude $x" "$REPO/install/install.sh"; then ok "install.sh leaves $x out of the recipe"
  else bad "install.sh copies $x into the recipe"; fi
done
# an inline eslint-disable of an HTML-sink rule (no-restricted-*) in the page's source only on a fault's line
sink_disables() {   # sink_disables DIR -> the disables not within 3 lines after a __FAULT_ constant
  local f
  grep -rlE 'eslint-disable[^*]*no-restricted-' "$1" 2>/dev/null | while IFS= read -r f; do
    awk -v F="$f" '/__FAULT_/ { last = NR } /eslint-disable[^*]*no-restricted-/ && (last == 0 || NR - last > 3) { print F ":" NR ": " $0 }' "$f"
  done
}
out="$(sink_disables "$REPO/dashboard/src")"
if [ -z "$out" ]; then ok "the HTML-sink lint rules are disabled inline only on a fault's line (dashboard/src)"
else bad "an HTML-sink lint rule is disabled inline outside a fault" "$out"; fi
mkdir -p "$ROOT/sinkfx"; printf 'const a = 1;\n// eslint-disable-next-line no-restricted-properties\nel.innerHTML = x;\n' > "$ROOT/sinkfx/x.tsx"
if [ -n "$(sink_disables "$ROOT/sinkfx")" ]; then ok "must-fail: a planted inline disable of the sink rule is found"
else bad "must-fail: a planted inline disable of the sink rule was not found"; fi
if /bin/bash -n "$REPO/bin/mu" && /bin/bash -n "$REPO/tools/dashboard-npm"; then ok "bin/mu and tools/dashboard-npm parse with $(/bin/bash -c 'echo $BASH_VERSION')"
else bad "bash -n failed"; fi

printf '\n%d passed, %d failed, %d skipped\n' "$PASS" "$FAILN" "$SKIPN"
if [ "$SKIPN" -gt 0 ]; then
  printf 'skipped (not tested here):%s\n' "$SKIPPED"
  if [ -n "${GITHUB_ACTIONS:-}" ]; then
    printf '%s\n' "$SKIPPED" | sed -n 's/^  - //p' | while IFS= read -r l; do printf '::warning title=skipped check::%s\n' "$l"; done
    [ -z "${GITHUB_STEP_SUMMARY:-}" ] || { printf '### Skipped checks (%s, %d)\n' "$(uname -s)" "$SKIPN"; printf '%s\n' "$SKIPPED" | sed -n 's/^  - /- /p'; } >> "$GITHUB_STEP_SUMMARY"
  fi
fi
[ "$FAILN" = 0 ] || { printf 'failed:%s\n' "$FAILED"; exit 1; }
