#!/bin/bash
# Stand-in for install/install.sh in the fake releases of tools/test-autoupdate/run.sh. It does what the dispatcher can
# see of a real install, in a fraction of a second: takes the install lock, writes toolchains/<V>/ (LAYERS, .installing
# while it runs) and store layers, replaces the shared dispatcher at the "commands" step, moves the default early when
# told to (as the real one does with --set-default or when there is no default yet), reports a failure's stage in
# $MU_INSTALL_RESULT the way install.sh's fail() does, and writes MANIFEST.json last, which marks a version installed.
# Behaviour comes from the file `mode` next to it, or FAKE_MODE:
#   success | slow (FAKE_SLEEP s) | newdisp (installs a newer dispatcher, rev 99)
#   fail | fail-stomp (benchmarks fail; -stomp moves the default first) | broken (a broken dispatcher, then fail)
#   hang (a broken dispatcher, then sleeps FAKE_SLEEP s, default 599) | build (a layer download fails)
#   apt (preflight: missing Ubuntu packages)
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
V=""; SD=0; ARGS="$*"
while [ $# -gt 0 ]; do case "$1" in --version) V="$2"; shift 2 ;; --set-default) SD=1; shift ;; *) shift ;; esac; done
MODE="${FAKE_MODE:-$(cat "$HERE/mode")}"
echo "$V $MODE $ARGS" >> "${MU_TEST_CALLS:?}"
result() { [ -z "${MU_INSTALL_RESULT:-}" ] || printf '%s\t%s\n' "$1" "$2" > "$MU_INSTALL_RESULT"; echo "μ install failed: $2" >&2; exit 1; }
[ "$MODE" != apt ] || result preflight "missing Ubuntu packages: libfoo1. Install them once with:  sudo ./install.sh --system"
LK="${MU_HOME:?}/.install.lock"
mkdir "$LK" 2>/dev/null || result preflight "another μ install is running"
echo $$ > "$LK/pid"; trap 'rm -rf "${LK:?}"' EXIT
T="$MU_HOME/toolchains/${V:?}"
mkdir -p "$T/recipe/bin" "$MU_HOME/store"; touch "$T/.installing"
printf 'env-shared\napp-%s\n' "$V" > "$T/LAYERS"
for k in env-shared "app-$V"; do mkdir -p "$MU_HOME/store/$k"; touch "$MU_HOME/store/$k/.mu-layer"; done
[ "$MODE" != build ] || result env "download failed: https://example.invalid/conda.tar.bz2 (no internet connection)"
case "$MODE" in   # the "commands" step
  broken|hang) printf '#!/bin/bash\nMU_DISPATCHER_REV=99\necho BROKEN-DISPATCHER-FROM-FAILED-RELEASE\n' > "$MU_HOME/bin/mu"
               echo BROKEN > "$MU_HOME/bin/git-lfs" ;;
  newdisp) sed 's/^MU_DISPATCHER_REV=[0-9]*/MU_DISPATCHER_REV=99/' "$MU_HOME/bin/mu" > "$MU_HOME/bin/mu.new"
           mv "$MU_HOME/bin/mu.new" "$MU_HOME/bin/mu"; chmod 755 "$MU_HOME/bin/mu" ;;
esac
if [ "$SD" = 1 ] || [ ! -f "$MU_HOME/default" ] || [ "$MODE" = fail-stomp ]; then echo "$V" > "$MU_HOME/default"; fi
case "$MODE" in
  slow) sleep "${FAKE_SLEEP:-4}" ;;
  hang) sleep "${FAKE_SLEEP:-599}" ;;
  fail|fail-stomp|broken) result bench "benchmarks failed" ;;
esac
printf '{\n "version": "%s",\n "recipe_commit": "0000000000",\n "installed": "%s"\n}\n' "$V" "$(date '+%Y-%m-%d %H:%M:%S')" > "$T/MANIFEST.json"
rm -f "$T/.installing"
echo "fake μ $V is ready"
