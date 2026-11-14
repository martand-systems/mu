#!/bin/bash
# Playwright against `mu dashboard serve --dev`, for CI (docs/design/dashboard.md §7.1). Starts the e2e launcher
# (e2e-launcher.py: a fresh dev server per test), runs Playwright with ARGS, stops the launcher and every server.
# On the Mac use tools/dashboard-npm e2e instead (it runs Playwright inside the sandbox; the launcher outside).
#
#   STATIC=DIR PY=python [SERVER_FAULTS=a,b] [SERVER_ENV=NAME=VALUE] tools/test-dashboard/e2e-run.sh ARGS...
# Needs DASH_DEV_RUN (where the launcher writes e2e/launcher.json); Playwright is run from dashboard/.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/../.." && pwd)"
: "${DASH_DEV_RUN:?DASH_DEV_RUN must name a folder}"
STATIC="${STATIC:-$REPO/share/dashboard}"; PY="${PY:-python3}"
base="$DASH_DEV_RUN/e2e"; mkdir -p "$base"; chmod 700 "$DASH_DEV_RUN" "$base"; rm -f "$base/launcher.json"
args=(--base "$base" --static "$STATIC" --python "$PY")
[ -n "${SERVER_FAULTS:-}" ] && args+=(--faults "$SERVER_FAULTS")
[ -n "${SERVER_ENV:-}" ] && args+=(--env "$SERVER_ENV")
"$PY" -I "$HERE/e2e-launcher.py" "${args[@]}" > "$base/launcher.log" 2>&1 &
lp=$!
trap 'kill "$lp" 2>/dev/null; wait "$lp" 2>/dev/null' EXIT
for _ in $(seq 100); do [ -f "$base/launcher.json" ] && break; sleep 0.1; done
[ -f "$base/launcher.json" ] || { cat "$base/launcher.log" >&2; echo "e2e-run: the launcher did not start" >&2; exit 1; }
cd "$REPO/dashboard" && npx --no-install playwright test "$@"
