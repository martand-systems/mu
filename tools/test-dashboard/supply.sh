#!/bin/bash
# The supply-chain checks of the dashboard's npm packages (docs/design/dashboard.md §4.6.1: D34, D35), each shown to
# fail with its fault. Run by .github/workflows/dashboard.yml's `supply` job on Linux, with npm on PATH (CI only: on
# the owner's Mac npm runs only through tools/dashboard-npm). Needs the network for npm ci.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/../.." && pwd)"; DASH="$REPO/dashboard"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
PASS=0; FAILN=0
ok()  { PASS=$((PASS + 1)); printf '  ok    %s\n' "$1"; }
bad() { FAILN=$((FAILN + 1)); printf '  FAIL  %s\n' "$1"; [ -z "${2:-}" ] || printf '%s\n' "$2" | sed 's/^/        | /' | head -20; }

bindings() {   # bindings LOCKFILE: both platforms' native packages of Rolldown, Lightning CSS and Tailwind's oxide
  python3 -I - "$1" <<'P'
import json, sys
pk = json.load(open(sys.argv[1]))["packages"]
need = ["@rolldown/binding-linux-x64-gnu", "@rolldown/binding-darwin-arm64", "lightningcss-linux-x64-gnu",
        "lightningcss-darwin-arm64", "@tailwindcss/oxide-linux-x64-gnu", "@tailwindcss/oxide-darwin-arm64"]
names = {k[k.rindex("node_modules/") + 13:] for k in pk if "node_modules/" in k}
miss = [n for n in need if n not in names]
print("missing: " + ", ".join(miss) if miss else "both platforms recorded")
sys.exit(1 if miss else 0)
P
}

echo "D35: the lockfile holds both platforms' native packages"
if out="$(bindings "$DASH/package-lock.json")"; then ok "linux-x64 and darwin-arm64 bindings recorded ($out)"; else bad "bindings" "$out"; fi
python3 -I -c "
import json,sys; d=json.load(open(sys.argv[1]))
d['packages']={k:v for k,v in d['packages'].items() if 'linux' not in k}
json.dump(d,open(sys.argv[2],'w'))" "$DASH/package-lock.json" "$T/mac-only-lock.json"
if bindings "$T/mac-only-lock.json" >/dev/null; then bad "must-fail: mac-only-lock passed the platform check"
else ok "must-fail: mac-only-lock (a lockfile without the Linux bindings) fails the platform check"; fi

echo "D35: npm ci refuses a package.json that disagrees with the lockfile"
mkdir -p "$T/drift"; cp "$DASH/package.json" "$DASH/package-lock.json" "$DASH/.npmrc" "$T/drift/"
sed -i.bak 's/"uplot": "1.6.32"/"uplot": "1.6.31"/' "$T/drift/package.json"
if (cd "$T/drift" && npm ci --ignore-scripts --no-audit --dry-run >"$T/drift.log" 2>&1); then
  bad "must-fail: lock-drift: npm ci accepted a changed package.json" "$(tail -5 "$T/drift.log")"
else ok "must-fail: lock-drift: npm ci fails ($(grep -m1 -o 'in sync\|lock file' "$T/drift.log" || echo refused))"; fi

echo "D35: no install script runs (a canary package whose postinstall writes a file)"
mkdir -p "$T/canary/pkg" "$T/canary/app"
cat > "$T/canary/pkg/package.json" <<J
{"name": "mu-canary", "version": "1.0.0", "license": "MIT",
 "scripts": {"postinstall": "node -e \"require('fs').writeFileSync('$T/canary/RAN', 'ran')\""}}
J
(cd "$T/canary/pkg" && npm pack --silent >/dev/null 2>&1)
cat > "$T/canary/app/package.json" <<J
{"name": "app", "version": "1.0.0", "private": true, "dependencies": {"mu-canary": "file:../pkg/mu-canary-1.0.0.tgz"}}
J
cp "$DASH/.npmrc" "$T/canary/app/.npmrc"
(cd "$T/canary/app" && npm install --package-lock-only --ignore-scripts --no-audit >/dev/null 2>&1)
(cd "$T/canary/app" && npm ci --ignore-scripts --no-audit >/dev/null 2>&1)
if [ -e "$T/canary/RAN" ]; then bad "the canary's install script ran under npm ci --ignore-scripts"
else ok "npm ci --ignore-scripts (and .npmrc ignore-scripts=true): the canary's script did not run"; fi
(cd "$T/canary/app" && rm -rf node_modules && npm ci --no-audit >/dev/null 2>&1)
if [ -e "$T/canary/RAN" ]; then bad "the canary's install script ran under plain npm ci: .npmrc's ignore-scripts=true is not honoured"
else ok "plain npm ci (no flag; dashboard/.npmrc ignore-scripts=true): the canary's script did not run"; fi
(cd "$T/canary/app" && rm -rf node_modules .npmrc && npm ci --no-audit >/dev/null 2>&1)
if [ -e "$T/canary/RAN" ]; then ok "must-fail: no-npmrc: without .npmrc plain npm ci runs the canary (the check sees it)"; rm -f "$T/canary/RAN"
else bad "must-fail: no-npmrc: the canary did not run without .npmrc (the check proves nothing)"; fi
(cd "$T/canary/app" && rm -rf node_modules && npm ci --ignore-scripts=false --no-audit >/dev/null 2>&1)
if [ -e "$T/canary/RAN" ]; then ok "must-fail: scripts-on: with scripts allowed the canary file appears (the check sees it)"
else bad "must-fail: scripts-on: the canary did not run even with scripts on (the check proves nothing)"; fi

echo "D34: licences"
if out="$(python3 -I "$REPO/tools/dashboard-licences.py" 2>&1)"; then ok "every package's licence is allowed for its kind ($(printf '%s' "$out" | tail -1))"
else bad "licences" "$out"; fi
for f in lock-gpl-shipped lock-no-license; do
  if python3 -I "$REPO/tools/dashboard-licences.py" --lock "$HERE/supply/$f.json" >/dev/null; then bad "must-fail: $f passed"
  else ok "must-fail: $f fails the licence check"; fi
done

printf '\n%d passed, %d failed\n' "$PASS" "$FAILN"
[ "$FAILN" = 0 ]
