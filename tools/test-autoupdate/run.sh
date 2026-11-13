#!/bin/bash
# Tests for `mu autoupdate` (bin/mu): a throwaway HOME, MU_HOME and MU_CACHE in a temp folder (with spaces in its
# path), local bare git repos of fake releases whose install.sh is fake-install.sh, and stubs for launchctl,
# systemctl, loginctl, pmset and osascript. Nothing touches the real toolchain, launchd or systemd. Takes ~1 min.
#
#   /bin/bash tools/test-autoupdate/run.sh          (KEEP=1 keeps the temp folder)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/../.." && pwd)"
ROOT="$(mktemp -d "${TMPDIR:-/tmp}/mu autoupdate test.XXXXXX")"
cleanup() { if [ "${KEEP:-0}" = 1 ]; then echo "kept: $ROOT"; else chmod -R u+w "${ROOT:?}" 2>/dev/null; rm -rf "${ROOT:?}"; fi; }
trap cleanup EXIT
PASS=0; FAILN=0; FAILED=""
ok()   { PASS=$((PASS + 1)); printf '  ok    %s\n' "$1"; }
bad()  { FAILN=$((FAILN + 1)); FAILED="$FAILED
  - $1"; printf '  FAIL  %s\n' "$1"; [ -z "${2:-}" ] || printf '%s\n' "$2" | sed 's/^/        | /' | head -40; }
check() { local d="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else bad "$d"; fi; }
eq()   { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "expected: [$3]
got:      [$2]"; fi; }
has()  { case "$2" in *"$3"*) ok "$1" ;; *) bad "$1" "expected to contain: [$3]
in: $2" ;; esac; }
hasnt() { case "$2" in *"$3"*) bad "$1" "must not contain: [$3]
in: $2" ;; *) ok "$1" ;; esac; }
section() { printf '\n%s\n' "$1"; }

# ---------------------------------------------------------------- isolation
export HOME="$ROOT/home"; mkdir -p "$HOME"
unset MU_VERSION MU_PROJECT XDG_CONFIG_HOME XDG_DATA_HOME XDG_CACHE_HOME CI MU_NO_AUTOUPDATE MU_AUTOUPDATE_SCHEDULER 2>/dev/null || true
export NO_COLOR=1 GIT_CONFIG_NOSYSTEM=1
export MU_CACHE="$ROOT/cache"
export MU_LAUNCH_AGENTS_DIR="$ROOT/LaunchAgents" MU_SYSTEMD_USER_DIR="$ROOT/systemd user"
STUBS="$ROOT/stubs"; FLAGS="$ROOT/flags"; CALLS="$ROOT/calls"; mkdir -p "$STUBS" "$FLAGS" "$CALLS"
export MU_TEST_CALLS="$CALLS/installs.log" FLAGS CALLS
# stubs log their arguments; launchctl/systemctl keep a "loaded" marker; nothing reaches the real ones
cat > "$STUBS/launchctl" <<'S'
#!/bin/bash
echo "$*" >> "$CALLS/launchctl.log"
case "$1" in
  bootstrap) touch "$FLAGS/launchd-loaded" ;;
  bootout) [ -e "$FLAGS/launchd-loaded" ] || exit 3; rm -f "$FLAGS/launchd-loaded" ;;
  print) [ -e "$FLAGS/launchd-loaded" ] ;;
esac
S
cat > "$STUBS/systemctl" <<'S'
#!/bin/bash
echo "$*" >> "$CALLS/systemctl.log"
[ ! -e "$FLAGS/no-systemd" ] || { echo "Failed to connect to bus: No medium found" >&2; exit 1; }
case "$*" in
  "--user enable --now "*) touch "$FLAGS/timer-enabled" ;;
  "--user disable --now "*) rm -f "$FLAGS/timer-enabled" ;;
  "--user is-enabled "*) if [ -e "$FLAGS/timer-enabled" ]; then echo enabled; else echo disabled; exit 1; fi ;;
  "--user show "*NextElapse*) echo "Sat 2026-11-14 04:37:00 IST" ;;
esac
S
cat > "$STUBS/loginctl" <<'S'
#!/bin/bash
echo "$*" >> "$CALLS/loginctl.log"
cat "$FLAGS/linger" 2>/dev/null || echo no
S
cat > "$STUBS/pmset" <<'S'
#!/bin/bash
if [ -e "$FLAGS/battery" ]; then echo "Now drawing from 'Battery Power'"; else echo "Now drawing from 'AC Power'"; fi
echo " -InternalBattery-0 (id=1)	80%; discharging; 3:00 remaining present: true"
S
cat > "$STUBS/osascript" <<'S'
#!/bin/bash
shift 6; printf '%s | %s\n' "$1" "$2" >> "$CALLS/notify.log"
S
chmod +x "$STUBS"/*
export PATH="$STUBS:$PATH"
export MU_LAUNCHCTL="$STUBS/launchctl" MU_SYSTEMCTL="$STUBS/systemctl" MU_LOGINCTL="$STUBS/loginctl"
export MU_PMSET="$STUBS/pmset" MU_OSASCRIPT="$STUBS/osascript"
GIT="$(command -v git)"
mkdir -p "$ROOT/cwd"; cd "$ROOT/cwd" || exit 1   # no mu.toml above this folder

# ---------------------------------------------------------------- fixtures
# mkrepo NAME "TAG:MODE ...": a bare repo of fake releases (each tag: install.sh = fake-install.sh, mode = MODE)
mkrepo() {
  local w="$ROOT/work/$1" t
  mkdir -p "$w" "$ROOT/repos"; "$GIT" -C "$w" init -q
  "$GIT" -C "$w" config user.name test; "$GIT" -C "$w" config user.email test@example.invalid
  cp "$HERE/fake-install.sh" "$w/install.sh"; chmod +x "$w/install.sh"
  "$GIT" -C "$w" add -A; "$GIT" -C "$w" commit -qm init; "$GIT" init -q --bare "$ROOT/repos/$1.git"
  for t in $2; do addtag "$1" "${t%%:*}" "${t#*:}"; done
}
addtag() {   # addtag REPO TAG MODE
  local w="$ROOT/work/$1"
  echo "$3" > "$w/mode"; "$GIT" -C "$w" add -A; "$GIT" -C "$w" commit -qm "$2 ($3)" --allow-empty
  "$GIT" -C "$w" tag "$2"; "$GIT" -C "$w" push -q "$ROOT/repos/$1.git" "refs/tags/$2" 2>/dev/null
}
fake_installed() {   # fake_installed V: an installed version with its own layer and a shared one
  local T="$MU_HOME/toolchains/$1" k
  mkdir -p "$T/recipe/bin"; printf 'env-shared\napp-%s\n' "$1" > "$T/LAYERS"
  printf '#!/bin/bash\necho "  mu run <cmd> [args]    (fake toolchain)"\n' > "$T/recipe/bin/mu-toolchain"; chmod +x "$T/recipe/bin/mu-toolchain"
  for k in env-shared "app-$1"; do mkdir -p "$MU_HOME/store/$k"; touch "$MU_HOME/store/$k/.mu-layer"; done
  printf '{\n "version": "%s",\n "recipe_commit": "0000000000",\n "installed": "2026-11-01 10:00:00"\n}\n' "$1" > "$T/MANIFEST.json"
}
fresh() {   # fresh NAME REPO [DEFAULT]: a new MU_HOME using REPO as its release source, auto-update on
  export MU_HOME="$ROOT/homes/$1 mu"
  mkdir -p "$MU_HOME/bin" "$MU_HOME/state"
  install -m 755 "$REPO/bin/mu" "$MU_HOME/bin/mu"
  echo "file://$ROOT/repos/$2.git" > "$MU_HOME/source"
  fake_installed "${3:-v2026.11.4}"; echo "${3:-v2026.11.4}" > "$MU_HOME/default"
  : > "$MU_TEST_CALLS"; rm -f "$FLAGS"/* "$CALLS"/notify.log
  mu autoupdate on >/dev/null 2>&1 || bad "fresh $1: mu autoupdate on"
  mu autoupdate set min_free_gb 0 >/dev/null   # the disk test sets its own
}
mu() { /bin/bash "$MU_HOME/bin/mu" "$@"; }
st() { awk -v k="$1" 'index($0, k "=") == 1 { v = substr($0, length(k) + 2) } END { print v }' "$MU_HOME/state/autoupdate.state" 2>/dev/null; }
calls() { grep -c . "$MU_TEST_CALLS" 2>/dev/null || true; }
def() { cat "$MU_HOME/default"; }

printf 'μ auto-update tests · bash %s · %s\n' "$BASH_VERSION" "$ROOT"
[ "${BASH_VERSINFO[0]}" -ge 4 ] && echo "note: not bash 3.2 (run with /bin/bash on macOS to test the 3.2 path)"

section "syntax"
for f in "$REPO/bin/mu" "$REPO/install/install.sh" "$REPO/install.sh" "$HERE/run.sh" "$HERE/fake-install.sh"; do
  check "bash -n ${f#$REPO/}" /bin/bash -n "$f"
done
if command -v shellcheck >/dev/null 2>&1; then
  out="$(shellcheck -S warning "$REPO/bin/mu" 2>&1)"; eq "shellcheck -S warning bin/mu" "$out" ""
else echo "  skip  shellcheck (not installed)"; fi

section "release tags: only vYYYY.MM.N from the source, compared numerically"
mkrepo numeric "v2026.11.4:success v2026.9.99:success v2026.11.10:success v2026.11.11-rc1:success v2027.1:success vX.1.2:success"
fresh numeric numeric v2026.11.9
out="$(mu autoupdate run)"; rc=$?
eq "run exits 0" "$rc" 0
eq "installs v2026.11.10 (not 9.99, not rc/2-part/odd tags)" "$(head -1 "$MU_TEST_CALLS" | cut -d' ' -f1)" "v2026.11.10"
eq "default switched to v2026.11.10" "$(def)" "v2026.11.10"
has "summary line names the result and log" "$out" "installed: μ v2026.11.10 is the default"
has "installer got --no-modify-path, not --set-default" "$(cat "$MU_TEST_CALLS")" "--version v2026.11.10 --no-modify-path"
hasnt "installer never got --set-default" "$(cat "$MU_TEST_CALLS")" "--set-default"
has "notification after the update" "$(cat "$CALLS/notify.log" 2>/dev/null)" "μ v2026.11.10 installed"

section "no newer release: nothing happens"
mkrepo same "v2026.11.3:success v2026.11.4:success"
fresh same same
out="$(mu autoupdate run)"; rc=$?
eq "run exits 0" "$rc" 0
eq "installer not called" "$(calls)" 0
eq "default unchanged" "$(def)" v2026.11.4
eq "last_result up-to-date" "$(st last_result)" up-to-date
has "summary" "$out" "up-to-date: μ v2026.11.4 is the latest release"
check "no notification" test ! -s "$CALLS/notify.log"

section "a failing release: default kept, 3 tries, then waits for a newer tag"
mkrepo failing "v2026.11.4:success v2026.11.5:fail-stomp"
fresh failing failing
mkdir -p "$ROOT/projects/p2"; printf '[mu]\nversion = "v2026.11.4"\n' > "$ROOT/projects/p2/mu.toml"
echo "$ROOT/projects/p2" > "$MU_HOME/projects.txt"; sum0="$(cksum < "$ROOT/projects/p2/mu.toml")"
for i in 1 2 3; do
  mu autoupdate run >/dev/null; rc=$?
  eq "try $i: run exits 1" "$rc" 1
  eq "try $i: default still v2026.11.4 (the installer moved it; restored)" "$(def)" v2026.11.4
  eq "try $i: fail_count" "$(st fail_count)" "$i"
done
check "the incomplete v2026.11.5 is removed" test ! -e "$MU_HOME/toolchains/v2026.11.5"
check "no install lock left behind" test ! -e "$MU_HOME/.install.lock"
check "no auto-update lock left behind" test ! -e "$MU_HOME/.autoupdate.lock"
has "failure notification" "$(cat "$CALLS/notify.log")" "μ v2026.11.5 failed its checks"
fl="$(st fail_log)"; check "fail_log exists" test -f "$fl"
has "fail log holds the installer's error" "$(cat "$fl")" "benchmarks failed"
out="$(mu)"
has "mu status warns" "$out" "auto-update: v2026.11.5 failed its checks (log: "
has "mu status says what it is still on" "$out" "still on v2026.11.4"
out="$(mu autoupdate run)"; rc=$?
eq "4th run: installer not called again" "$(calls)" 3
has "4th run: skipped" "$out" "failed its checks 3 times: not retried"
eq "4th run exits 0" "$rc" 0
mu autoupdate run --force >/dev/null
eq "--force retries anyway" "$(calls)" 4
eq "mu.toml pins untouched" "$(cksum < "$ROOT/projects/p2/mu.toml")" "$sum0"
addtag failing v2026.11.6 success
out="$(mu autoupdate run)"; rc=$?
eq "newer tag: run exits 0" "$rc" 0
eq "newer tag: installed and default" "$(def)" v2026.11.6
eq "newer tag: failure record cleared" "$(st fail_version)" ""
hasnt "mu status: no warning any more" "$(mu)" "failed its checks"
has "mu status: one line" "$(mu)" "auto-update: on, last check "
has "mu status: names the installed version" "$(mu)" "μ v2026.11.6 installed"
eq "mu.toml pins still untouched" "$(cksum < "$ROOT/projects/p2/mu.toml")" "$sum0"

section "policy install-only"
mkrepo inst "v2026.11.4:success v2026.11.5:success"
fresh inst inst
mu autoupdate set policy install-only >/dev/null
mu autoupdate run >/dev/null; rc=$?
eq "run exits 0" "$rc" 0
check "v2026.11.5 installed" test -f "$MU_HOME/toolchains/v2026.11.5/MANIFEST.json"
eq "default stays v2026.11.4" "$(def)" v2026.11.4
has "mu status line" "$(mu)" "μ v2026.11.5 installed"
out="$(mu autoupdate run)"
eq "second run: no reinstall" "$(calls)" 1
has "second run: says it is installed, not default" "$out" "the default stays μ v2026.11.4"
mu autoupdate set policy switch >/dev/null
out="$(mu autoupdate run)"
eq "policy switch: no reinstall" "$(calls)" 1
eq "policy switch: default moves to the installed v2026.11.5" "$(def)" v2026.11.5

section "free disk check"
mkrepo disk "v2026.11.4:success v2026.11.5:success"
fresh disk disk
mu autoupdate set min_free_gb 999999 >/dev/null
out="$(mu autoupdate run)"
eq "installer not called" "$(calls)" 0
has "skipped for disk" "$out" "auto-update waits for 999999 GB"
eq "default unchanged" "$(def)" v2026.11.4
mu autoupdate set min_free_gb 0 >/dev/null; mu autoupdate run >/dev/null
eq "with room: installs" "$(def)" v2026.11.5

section "battery (pmset stub)"
mkrepo batt "v2026.11.4:success v2026.11.5:success"
fresh batt batt
touch "$FLAGS/battery"
out="$(mu autoupdate run)"
eq "on battery: installer not called" "$(calls)" 0
has "on battery: skipped" "$out" "on battery power"
mu autoupdate run --force >/dev/null
eq "--force installs on battery" "$(def)" v2026.11.5

section "busy marker defers"
mkrepo busy "v2026.11.4:success v2026.11.5:success"
fresh busy busy
echo "" > "$MU_HOME/state/busy"
out="$(mu autoupdate run)"
eq "busy: installer not called" "$(calls)" 0
has "busy: deferred" "$out" "deferred: busy"
sleep 0 & dead=$!; wait "$dead"
echo "$dead" > "$MU_HOME/state/busy"
mu autoupdate run >/dev/null
eq "stale busy marker (pid gone): installs" "$(def)" v2026.11.5
grep -q "ignoring a stale busy marker" "$(st last_log)" && ok "stale marker reported in the log" || bad "stale marker reported in the log"

section "locks: a running install, and two runs at once"
mkrepo lock "v2026.11.4:success v2026.11.5:slow"
fresh lock lock
cat > "$ROOT/hold-install-lock.sh" <<'S'
#!/bin/bash
mkdir "$MU_HOME/.install.lock" && echo $$ > "$MU_HOME/.install.lock/pid"; sleep 3; rm -rf "${MU_HOME:?}/.install.lock"
S
/bin/bash "$ROOT/hold-install-lock.sh" & hp=$!; sleep 1
out="$(mu autoupdate run)"
eq "install running: installer not called" "$(calls)" 0
has "install running: deferred" "$out" "an install is running"
wait "$hp"
FAKE_SLEEP=4 mu autoupdate run > "$ROOT/run1.out" 2>&1 & r1=$!; sleep 1.5
out="$(mu autoupdate run)"; rc=$?
has "second run skips" "$out" "another auto-update is running"
eq "second run exits 0" "$rc" 0
out="$(mu remove v2026.11.4 2>&1)"
has "mu remove refuses during the auto-update's install" "$out" "an install is running"
wait "$r1"; rc=$?
eq "first run finishes" "$rc" 0
eq "first run installed v2026.11.5" "$(def)" v2026.11.5
eq "installer ran once" "$(calls)" 1
mkdir "$MU_HOME/.autoupdate.lock"; echo 99999 > "$MU_HOME/.autoupdate.lock/pid"
addtag lock v2026.11.6 success
mu autoupdate run >/dev/null
eq "stale auto-update lock is taken over" "$(def)" v2026.11.6

section "gc after a switch: keeps default, previous and pinned versions"
mkrepo gc "v2026.11.4:success v2026.11.5:success"
fresh gc gc
for v in v2026.11.1 v2026.11.2 v2026.11.3 dev-abc1234; do fake_installed "$v"; done
mkdir -p "$ROOT/projects/p1 pinned"; printf '# project\n[mu]\nversion = "v2026.11.2"   # pinned\n' > "$ROOT/projects/p1 pinned/mu.toml"
echo "$ROOT/projects/p1 pinned" > "$MU_HOME/projects.txt"; sum1="$(cksum < "$ROOT/projects/p1 pinned/mu.toml")"
mu autoupdate run >/dev/null
eq "default v2026.11.5" "$(def)" v2026.11.5
check "keeps the previous default v2026.11.4" test -f "$MU_HOME/toolchains/v2026.11.4/MANIFEST.json"
check "keeps v2026.11.2 (pinned by a project)" test -f "$MU_HOME/toolchains/v2026.11.2/MANIFEST.json"
check "keeps dev-abc1234 (not a release)" test -f "$MU_HOME/toolchains/dev-abc1234/MANIFEST.json"
check "removes v2026.11.3" test ! -e "$MU_HOME/toolchains/v2026.11.3"
check "removes v2026.11.1" test ! -e "$MU_HOME/toolchains/v2026.11.1"
check "prunes v2026.11.3's own layer" test ! -e "$MU_HOME/store/app-v2026.11.3"
check "keeps the shared layer" test -e "$MU_HOME/store/env-shared"
check "keeps the pinned version's layer" test -e "$MU_HOME/store/app-v2026.11.2"
eq "project mu.toml untouched" "$(cksum < "$ROOT/projects/p1 pinned/mu.toml")" "$sum1"
eq "previous_default recorded (rollback)" "$(st previous_default)" v2026.11.4
mu use v2026.11.4 >/dev/null && eq "rollback with mu use works" "$(def)" v2026.11.4
mkrepo gc2 "v2026.11.4:success v2026.11.5:success"
fresh gc2 gc2
for v in v2026.11.2 v2026.11.3; do fake_installed "$v"; done
mu autoupdate set keep_previous 2 >/dev/null; mu autoupdate run >/dev/null
check "keep_previous=2 keeps v2026.11.3 too" test -f "$MU_HOME/toolchains/v2026.11.3/MANIFEST.json"
check "keep_previous=2 removes v2026.11.2" test ! -e "$MU_HOME/toolchains/v2026.11.2"
mkrepo gc3 "v2026.11.4:success v2026.11.5:success"
fresh gc3 gc3
fake_installed v2026.11.2; mu autoupdate set gc off >/dev/null; mu autoupdate run >/dev/null
check "gc=off removes nothing" test -f "$MU_HOME/toolchains/v2026.11.2/MANIFEST.json"

section "settings are parsed, never executed; logs are capped at 20"
mkrepo conf "v2026.11.4:success"
fresh conf conf
cat >> "$MU_HOME/autoupdate.conf" <<C
min_free_gb=\$(touch "$ROOT/pwned")
policy=\`touch "$ROOT/pwned"\`
hour=25
C
out="$(mu autoupdate status --json)"
check "status --json is valid JSON" python3 -c 'import json,sys; json.loads(sys.argv[1])' "$out"
has "invalid min_free_gb falls back to 12" "$out" '"min_free_gb": 12'
has "invalid policy falls back to switch" "$out" '"policy": "switch"'
has "invalid hour falls back to 4" "$out" '"time": "04:'
mu autoupdate run >/dev/null
check "nothing in the settings ran" test ! -e "$ROOT/pwned"
out="$(mu autoupdate set policy 'x; touch y' 2>&1)"; rc=$?
eq "mu autoupdate set rejects bad values" "$rc" 2
for i in $(seq 1 25); do touch -t 202601010000 "$MU_HOME/logs/autoupdate-20260101-0000$(printf %02d "$i").log"; done
mu autoupdate run >/dev/null
eq "keeps the newest 20 auto-update logs" "$(ls "$MU_HOME/logs" | grep -c '^autoupdate-')" 20
mu autoupdate off >/dev/null
out="$(mu autoupdate run)"
has "off: a scheduled run does nothing" "$out" "auto-update is off"
has "mu status when off" "$(mu)" "auto-update: off (mu autoupdate on)"

section "status"
mkrepo stat "v2026.11.4:success"
fresh stat stat
out="$(mu autoupdate status)"
has "status: on" "$out" "auto-update: on"
has "status: schedule" "$out" "schedule    daily at 04:"
has "status: next run" "$out" "next run    "
has "status: never checked" "$out" "last check  never"
has "mu status before the first check" "$(mu)" "auto-update: on, first check at 04:"
mu autoupdate run >/dev/null
out="$(mu autoupdate status)"
has "status: last check" "$out" "last check  20"
has "status: last log" "$out" "last log    "
touch "$MU_HOME/state/busy"; has "status: busy" "$(mu autoupdate status)" "busy        "
has "status --json: busy" "$(mu autoupdate status --json)" '"busy": true'
has "mu help lists autoupdate" "$(mu help)" "mu autoupdate on|off"
hasnt "a path beside HOME is not shown as ~ (HOME=.../home, MU_HOME=.../homes/...)" "$(mu autoupdate status)" "~s/"

section "launchd: LaunchAgent (generated into a temp folder, loaded only into the launchctl stub)"
export MU_AUTOUPDATE_SCHEDULER=launchd
mkrepo sched "v2026.11.4:success"
fresh "sched & <odd>" sched
P="$MU_LAUNCH_AGENTS_DIR/systems.martand.mu.autoupdate.plist"
check "plist written" test -f "$P"
if command -v plutil >/dev/null 2>&1; then
  out="$(plutil -lint "$P")"; has "plutil -lint passes" "$out" ": OK"
  eq "ProgramArguments[0] is MU_HOME/bin/mu (spaces, & and <> intact)" "$(plutil -extract ProgramArguments.0 raw -o - "$P")" "$MU_HOME/bin/mu"
  eq "ProgramArguments[1..2]" "$(plutil -extract ProgramArguments.1 raw -o - "$P") $(plutil -extract ProgramArguments.2 raw -o - "$P")" "autoupdate run"
  eq "EnvironmentVariables.MU_HOME" "$(plutil -extract EnvironmentVariables.MU_HOME raw -o - "$P")" "$MU_HOME"
  eq "StartCalendarInterval.Hour" "$(plutil -extract StartCalendarInterval.Hour raw -o - "$P")" 4
  m="$(plutil -extract StartCalendarInterval.Minute raw -o - "$P")"
  check "StartCalendarInterval.Minute is 0-59 ($m)" test "$m" -ge 0 -a "$m" -le 59
  eq "minute matches autoupdate.conf" "$m" "$(grep '^minute=' "$MU_HOME/autoupdate.conf" | cut -d= -f2 | sed 's/^0\([0-9]\)/\1/')"
else echo "  skip  plutil (not macOS)"; fi
has "loaded with launchctl bootstrap (stub)" "$(cat "$CALLS/launchctl.log")" "bootstrap gui/$(id -u) $P"
has "status: loaded" "$(mu autoupdate status)" "(launchd: loaded)"
mu autoupdate set hour 3 >/dev/null
command -v plutil >/dev/null 2>&1 && eq "set hour 3 rewrites the plist" "$(plutil -extract StartCalendarInterval.Hour raw -o - "$P")" 3
mu autoupdate off >/dev/null
check "off removes the plist" test ! -e "$P"
has "off unloads it" "$(tail -1 "$CALLS/launchctl.log")" "bootout gui/$(id -u)/systems.martand.mu.autoupdate"
eq "off is remembered" "$(grep '^enabled=' "$MU_HOME/autoupdate.conf")" "enabled=off"
check "the real ~/Library/LaunchAgents was not used" test ! -e "$HOME/Library/LaunchAgents"

section "systemd: user service + timer (written to a temp folder; systemctl/loginctl stubs)"
export MU_AUTOUPDATE_SCHEDULER=systemd
mkrepo sd "v2026.11.4:success"
rm -f "$CALLS/systemctl.log"
fresh 'sd 50% $HOME x' sd
SD="$MU_SYSTEMD_USER_DIR"
check "service written" test -f "$SD/mu-autoupdate.service"
check "timer written" test -f "$SD/mu-autoupdate.timer"
svc="$(cat "$SD/mu-autoupdate.service")"; tmr="$(cat "$SD/mu-autoupdate.timer")"
esc="$(printf '%s' "$MU_HOME/bin/mu" | sed -e 's/%/%%/g' -e 's/\$/$$/g')"
has "ExecStart quotes the path, escapes % and \$" "$svc" "ExecStart=\"$esc\" autoupdate run"
has "Environment MU_HOME (escapes %)" "$svc" "Environment=\"MU_HOME=$(printf '%s' "$MU_HOME" | sed 's/%/%%/g')\""
has "Type=oneshot" "$svc" "Type=oneshot"
has "OnCalendar daily" "$tmr" "OnCalendar=*-*-* 04:"
has "RandomizedDelaySec" "$tmr" "RandomizedDelaySec="
has "Persistent=true" "$tmr" "Persistent=true"
has "WantedBy=timers.target" "$tmr" "WantedBy=timers.target"
has "daemon-reload + enable --now (stub)" "$(cat "$CALLS/systemctl.log")" "--user enable --now mu-autoupdate.timer"
out="$(mu autoupdate on 2>&1)"
has "linger off: prints the one-time root command" "$out" "sudo loginctl enable-linger $(id -un)"
out="$(mu autoupdate on --quiet 2>/dev/null)"
eq "--quiet prints one line" "$(printf '%s\n' "$out" | grep -c .)" 1
echo yes > "$FLAGS/linger"
out="$(mu autoupdate on 2>&1)"
hasnt "linger on: no hint" "$out" "enable-linger"
has "status: next run from systemctl" "$(mu autoupdate status)" "next run    Sat 2026-11-14 04:37:00"
mu autoupdate off >/dev/null
check "off removes the units" test ! -e "$SD/mu-autoupdate.timer"
touch "$FLAGS/no-systemd"
out="$(mu autoupdate on 2>&1)"; rc=$?
eq "no systemd user manager: on exits 1" "$rc" 1
has "no systemd: clear message" "$out" "systemd user manager isn't available"
rm -f "$FLAGS/no-systemd"
if command -v systemd-analyze >/dev/null 2>&1; then
  mu autoupdate on >/dev/null 2>&1
  out="$(systemd-analyze --user verify "$SD/mu-autoupdate.service" "$SD/mu-autoupdate.timer" 2>&1)"; rc=$?
  eq "systemd-analyze verify" "$rc" 0
elif command -v docker >/dev/null 2>&1 && docker image inspect ubuntu:24.04 >/dev/null 2>&1; then
  out="$(docker run --rm -v "$REPO/bin/mu:/mu:ro" ubuntu:24.04 bash -c '
    set -e; apt-get update -qq >/dev/null; apt-get install -y -qq --no-install-recommends systemd >/dev/null 2>&1
    export HOME=/root MU_HOME="/root/mu home" MU_AUTOUPDATE_SCHEDULER=systemd MU_SYSTEMD_USER_DIR=/root/units MU_SYSTEMCTL=true MU_LOGINCTL=true
    mkdir -p "$MU_HOME/bin"; install -m 755 /mu "$MU_HOME/bin/mu"; "$MU_HOME/bin/mu" autoupdate on >/dev/null 2>&1
    systemd-analyze verify /root/units/mu-autoupdate.service /root/units/mu-autoupdate.timer' 2>&1)"; rc=$?
  eq "systemd-analyze verify (docker ubuntu:24.04)" "$rc" 0
else echo "  skip  systemd-analyze verify (no systemd-analyze here, and no docker with ubuntu:24.04 running)"; fi
unset MU_AUTOUPDATE_SCHEDULER

section "installer"
out="$("$REPO/install/install.sh" --help)"
has "install.sh --help: --no-autoupdate" "$out" "--no-autoupdate"
has "install.sh --help: --user NAME" "$out" "--user NAME"
out="$("$REPO/install/install.sh" --user someone 2>&1)"; rc=$?
eq "install.sh --user without --system exits 2" "$rc" 2
has "install.sh --user without --system says why" "$out" "goes with --system"
src="$(cat "$REPO/install/install.sh")"
has "hook skips CI / --no-modify-path / MU_NO_AUTOUPDATE / an earlier choice" "$src" '[ "$AUTOUPDATE" = 1 ] && [ "$MODIFY_PATH" = 1 ] && [ "${MU_NO_AUTOUPDATE:-}" != 1 ] && [ "${CI:-}" != true ]'
eq "dispatcher revs match" "$(sed -n 's/^DISPATCHER_REV=\([0-9]*\).*/\1/p' "$REPO/install/install.sh")" "$(sed -n 's/^MU_DISPATCHER_REV=\([0-9]*\).*/\1/p' "$REPO/bin/mu")"

printf '\n%s passed, %s failed\n' "$PASS" "$FAILN"
[ "$FAILN" = 0 ] || { printf 'failed:%s\n' "$FAILED"; exit 1; }
