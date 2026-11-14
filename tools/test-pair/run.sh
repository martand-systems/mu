#!/bin/bash
# Fast tests of the pairing clients (docs/design/pairing.md §13.2): the protocol and the claimer (src/mu/pair.py), the
# Mac's commands (src/mu/invite.py: mu compute invite, mu relay ...) against a stand-in relay on loopback
# (fakerelay.py), the dispatcher's `mu relay` route and `mu node host-script`. No root, no network, no Go. macOS
# (/bin/bash 3.2, the system python3 and the toolchain's Python) and Linux. Every guarded check is run again with its
# fault put in (MU_PAIR_FAULT) and must then fail ("must-fail" lines).
#
#   /bin/bash tools/test-pair/run.sh        (MU_TEST_PYTHONS="py1 py2" chooses the Pythons; QUICK=1 runs the faults
#                                            with the first Python only)
# The end to end run (the real relay behind Traefik, the node's root side, a job) is tools/test-jobs/e2e.sh (Q rows).
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/../.." && pwd)"
REAL_HOME="$HOME"
ROOT="$(mktemp -d "${TMPDIR:-/tmp}/mu pair test.XXXXXX")"
trap 'rm -rf "${ROOT:?}"' EXIT
PASS=0; FAILN=0; FAILED=""
ok()  { PASS=$((PASS + 1)); printf '  ok    %s\n' "$1"; }
bad() { FAILN=$((FAILN + 1)); FAILED="$FAILED
  - $1"; printf '  FAIL  %s\n' "$1"; [ -z "${2:-}" ] || printf '%s\n' "$2" | sed 's/^/        | /' | head -20; }
eq()  { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected: [$3]
got:      [$2]"; fi; }
has() { case "$2" in *"$3"*) ok "$1" ;; *) bad "$1" "expected to contain: [$3]
in: $2" ;; esac; }

PYS="${MU_TEST_PYTHONS:-}"
if [ -z "$PYS" ]; then
  PYS="$(command -v python3)"
  case "$(uname -s)" in Darwin) d="$(cat "$REAL_HOME/Library/Mu/default" 2>/dev/null)"; t="$REAL_HOME/Library/Mu/toolchains/$d/env/bin/python" ;;
    *) d="$(cat "$REAL_HOME/.local/share/mu/default" 2>/dev/null)"; t="$REAL_HOME/.local/share/mu/toolchains/$d/env/bin/python" ;; esac
  [ -z "$d" ] || [ ! -x "$t" ] || PYS="$PYS $t"
fi
PY1="${PYS%% *}"
printf 'μ pairing fast tests · bash %s · %s\n' "$BASH_VERSION" "$ROOT"

# py_sections PY SECTION...: unit.py's ok/FAIL lines count here
py_sections() {
  local py="$1" out rc; shift
  out="$("$py" -I -S "$HERE/unit.py" "$@" 2>&1)"; rc=$?
  printf '%s\n' "$out" | sed 's/^/  /'
  PASS=$((PASS + $(printf '%s\n' "$out" | grep -c '^ok ')))
  if [ "$rc" != 0 ] || printf '%s\n' "$out" | grep -q '^Traceback'; then
    FAILN=$((FAILN + 1)); FAILED="$FAILED
  - unit.py $* ($(basename "$py")): $(printf '%s\n' "$out" | grep -c '^FAIL') FAIL line(s)"
  fi
}
# must_fail FAULT PY SECTION...: with MU_PAIR_FAULT=FAULT the sections must report at least one FAIL
must_fail() {
  local f="$1" py="$2" out n; shift 2
  out="$(MU_PAIR_FAULT="$f" "$py" -I -S "$HERE/unit.py" "$@" 2>&1)"
  n="$(printf '%s\n' "$out" | grep -c '^FAIL')"
  if [ "$n" -gt 0 ]; then ok "must-fail: $* with fault $f ($n check(s) failed, as they must: $(printf '%s\n' "$out" | grep -m1 '^FAIL' | cut -c6-90))"
  else bad "must-fail: $* with fault $f: nothing failed" "$(printf '%s\n' "$out" | tail -5)"; fi
}

for py in $PYS; do
  printf '\n== %s (%s)\n' "$py" "$("$py" -c 'import sys; print(sys.version.split()[0])')"
  if "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    py_sections "$py" code vectors fields reflection offer http flow review
  else
    echo "  skip  Python older than 3.11 (the job commands refuse it): the protocol sections only"
    py_sections "$py" code vectors fields reflection offer http
  fi
done

printf '\n== faults (each must make its checks fail)\n'
FPYS="$PYS"; [ "${QUICK:-0}" != 1 ] || FPYS="$PY1"
for py in $FPYS; do
  "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || continue
  must_fail no-check "$py" code
  must_fail key-label-swap "$py" vectors
  must_fail skip-field "$py" fields
  must_fail one-key "$py" reflection
  must_fail no-type-label "$py" reflection
  must_fail trust-relay-fields "$py" offer
  must_fail retry-new-body "$py" http
  must_fail skip-offer-mac "$py" flow
  must_fail skip-answer-mac "$py" flow
  must_fail rollback-on-no-confirm "$py" flow
  must_fail no-resume "$py" flow
  must_fail no-rollback "$py" flow
  must_fail code-in-json "$py" flow
  must_fail code-in-argv "$py" flow
  # the reviews of ff2c707 (docs/design/pairing.md §17)
  must_fail keep-code "$py" review
  must_fail trust-finished "$py" review
  must_fail remove-any-port "$py" review
  must_fail static-comes-back "$py" review
  must_fail no-dialog-check "$py" review
  # the root gate of the test hooks has no fault (a fault would pass through it): a copy of src/ without the gate
  rm -rf "$ROOT/ungated"; mkdir -p "$ROOT/ungated"; cp -R "$REPO/src/mu" "$ROOT/ungated/mu"
  "$py" -I -S -c 'import sys; p = sys.argv[1]; s = open(p).read(); t = s.replace("    if not as_root():\n        return True\n", "    return True\n", 1)
assert t != s; open(p, "w").write(t)' "$ROOT/ungated/mu/pair.py"
  out="$(MU_TEST_SRC="$ROOT/ungated" "$py" -I -S "$HERE/unit.py" review 2>&1)"
  if printf '%s\n' "$out" | grep -q '^FAIL security 6'; then ok "must-fail: review with pair.py's root gate taken out (security 6 failed, as it must)"
  else bad "must-fail: review with the root gate taken out: security 6 did not fail" "$(printf '%s\n' "$out" | grep 'security 6')"; fi
done

printf '\n== the dispatcher (bin/mu under /bin/bash %s) and mu node host-script\n' "$(/bin/bash -c 'echo $BASH_VERSION')"
export HOME="$ROOT/home"; mkdir -p "$HOME"
export MU_HOME="$ROOT/mu home"
unset MU_VERSION MU_PROJECT XDG_DATA_HOME 2>/dev/null || true
T="$MU_HOME/toolchains/v2026.11.9"
mkdir -p "$T/env/bin" "$T/recipe/src" "$T/recipe/install/hosts/wsl2" "$MU_HOME/bin"
printf '{\n "version": "v2026.11.9",\n "layers": []\n}\n' > "$T/MANIFEST.json"
ln -s "$PY1" "$T/env/bin/python"
cp -R "$REPO/src/mu" "$T/recipe/src/mu"
cp "$REPO/install/hosts/wsl2/mu-wsl2-host.ps1" "$T/recipe/install/hosts/wsl2/"
echo v2026.11.9 > "$MU_HOME/default"
cp "$REPO/bin/mu" "$MU_HOME/bin/mu"
out="$(cd "$ROOT" && /bin/bash "$MU_HOME/bin/mu" relay --help 2>&1)"; rc=$?
eq "mu relay --help: routed to the default toolchain's job commands (exit 0)" "$rc" 0
has "  it lists mu relay login --stdin" "$out" "mu relay login --stdin"
out="$(cd "$ROOT" && /bin/bash "$MU_HOME/bin/mu" relay status --json 2>&1)"; rc=$?
eq "mu relay status without a login: exit 2 not-logged-in" "$rc $(printf '%s' "$out" | "$PY1" -c 'import json,sys; print(json.loads(sys.stdin.read().strip().splitlines()[-1])["error"])' 2>/dev/null)" "2 not-logged-in"
out="$(cd "$ROOT" && /bin/bash "$MU_HOME/bin/mu" compute invite office-pc --json 2>&1)"; rc=$?
eq "mu compute invite without a login: exit 2" "$rc" 2
has "  the next step is the owner's login-bundle line" "$out" "login-bundle"
out="$(cd "$ROOT" && WSL_DISTRO_NAME=mu-compute /bin/bash "$MU_HOME/bin/mu" node host-script 2>/dev/null)"; rc=$?
real="$(cd "$T/recipe/install/hosts/wsl2" && pwd -P)/mu-wsl2-host.ps1"
eq "mu node host-script (in WSL): exit 0, one line, the default release's host script as a \\\\wsl.localhost path" \
  "$rc $out" "0 \\\\wsl.localhost\\mu-compute$(printf '%s' "$real" | tr '/' '\\')"
out="$(cd "$ROOT" && env -u WSL_DISTRO_NAME /bin/bash "$MU_HOME/bin/mu" node host-script 2>&1)"; rc=$?
eq "mu node host-script outside WSL: exit 2" "$rc" 2
sed "s/'install', 'status', 'uninstall', 'test-stop', 'pair'/'install', 'status', 'uninstall', 'test-stop'/" \
  "$REPO/install/hosts/wsl2/mu-wsl2-host.ps1" > "$T/recipe/install/hosts/wsl2/mu-wsl2-host.ps1"
out="$(cd "$ROOT" && WSL_DISTRO_NAME=mu-compute /bin/bash "$MU_HOME/bin/mu" node host-script 2>&1)"; rc=$?
eq "a release whose host script has no pair action: exit 3" "$rc" 3
has "  and says: run: mu update" "$out" "run: mu update"

printf '\n%d passed, %d failed\n' "$PASS" "$FAILN"
if [ "$FAILN" -gt 0 ]; then printf 'failed:%s\n' "$FAILED"; exit 1; fi
