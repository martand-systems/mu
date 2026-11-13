#!/bin/bash
# Stand-in for install/install.sh in the fake releases of tools/test-autoupdate/run.sh. It does what the dispatcher can
# see of a real install, in a fraction of a second: takes the install lock, writes toolchains/<V>/ (LAYERS, .installing
# while it runs) and store layers, moves the default early when told to (as the real one does with --set-default or
# when there is no default yet), and writes MANIFEST.json last, which is what marks a version installed.
# Behaviour comes from the file `mode` next to it (success | fail | fail-stomp | slow), or FAKE_MODE.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
V=""; SD=0; ARGS="$*"
while [ $# -gt 0 ]; do case "$1" in --version) V="$2"; shift 2 ;; --set-default) SD=1; shift ;; *) shift ;; esac; done
MODE="${FAKE_MODE:-$(cat "$HERE/mode")}"
echo "$V $MODE $ARGS" >> "${MU_TEST_CALLS:?}"
LK="${MU_HOME:?}/.install.lock"
mkdir "$LK" || { echo "another μ install is running" >&2; exit 1; }
echo $$ > "$LK/pid"; trap 'rm -rf "${LK:?}"' EXIT
T="$MU_HOME/toolchains/${V:?}"
mkdir -p "$T/recipe/bin" "$MU_HOME/store"; touch "$T/.installing"
printf 'env-shared\napp-%s\n' "$V" > "$T/LAYERS"
for k in env-shared "app-$V"; do mkdir -p "$MU_HOME/store/$k"; touch "$MU_HOME/store/$k/.mu-layer"; done
if [ "$SD" = 1 ] || [ ! -f "$MU_HOME/default" ] || [ "$MODE" = fail-stomp ]; then echo "$V" > "$MU_HOME/default"; fi
case "$MODE" in
  slow) sleep "${FAKE_SLEEP:-4}" ;;
  fail|fail-stomp) echo "μ install failed: benchmarks failed" >&2; exit 1 ;;
esac
printf '{\n "version": "%s",\n "recipe_commit": "0000000000",\n "installed": "%s"\n}\n' "$V" "$(date '+%Y-%m-%d %H:%M:%S')" > "$T/MANIFEST.json"
rm -f "$T/.installing"
echo "fake μ $V is ready"
