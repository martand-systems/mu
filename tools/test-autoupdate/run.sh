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
  bootstrap) [ ! -e "$FLAGS/bootstrap-fails" ] || { echo "Bootstrap failed: 5: Input/output error" >&2; exit 5; }
             touch "$FLAGS/launchd-loaded" ;;
  bootout) [ -e "$FLAGS/launchd-loaded" ] || exit 3; rm -f "$FLAGS/launchd-loaded" ;;
  print) case "$2" in */*/*) [ -e "$FLAGS/launchd-loaded" ] ;; gui/*) [ ! -e "$FLAGS/no-gui" ] ;; *) true ;; esac ;;
esac
S
cat > "$STUBS/systemctl" <<'S'
#!/bin/bash
echo "$*" >> "$CALLS/systemctl.log"
[ ! -e "$FLAGS/no-systemd" ] || { echo "Failed to connect to bus: No medium found" >&2; exit 1; }
if [ -e "$FLAGS/need-xdg" ] && [ "${XDG_RUNTIME_DIR:-}" != "$(cat "$FLAGS/need-xdg")" ]; then
  echo 'Failed to connect to bus: $DBUS_SESSION_BUS_ADDRESS and $XDG_RUNTIME_DIR not defined' >&2; exit 1
fi
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
cat > "$STUBS/caffeinate" <<'S'
#!/bin/bash
echo "$*" >> "$CALLS/caffeinate.log"
S
cat > "$STUBS/fake-ssh" <<'S'
#!/bin/bash
echo "$*" >> "$CALLS/ssh.log"; echo "Permission denied (publickey)." >&2; exit 255
S
chmod +x "$STUBS"/*
export PATH="$STUBS:$PATH"
export MU_LAUNCHCTL="$STUBS/launchctl" MU_SYSTEMCTL="$STUBS/systemctl" MU_LOGINCTL="$STUBS/loginctl"
export MU_PMSET="$STUBS/pmset" MU_OSASCRIPT="$STUBS/osascript" MU_CAFFEINATE="$STUBS/caffeinate"
export MU_AUTOUPDATE_RETRY_S=0 MU_SYSTEMD_RUN_DIR="$ROOT/run/systemd/system" MU_RUN_USER_DIR="$ROOT/run/user"
unset XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS GIT_SSH GIT_SSH_COMMAND 2>/dev/null || true
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
  install -m 755 "$REPO/bin/mu" "$MU_HOME/bin/mu"; echo '#!/bin/sh' > "$MU_HOME/bin/git-lfs"; chmod 755 "$MU_HOME/bin/git-lfs"
  echo "file://$ROOT/repos/$2.git" > "$MU_HOME/source"
  fake_installed "${3:-v2026.11.4}"; echo "${3:-v2026.11.4}" > "$MU_HOME/default"
  : > "$MU_TEST_CALLS"; rm -f "$FLAGS"/* "$CALLS"/notify.log "$CALLS"/caffeinate.log
  mu autoupdate on >/dev/null 2>&1 || bad "fresh $1: mu autoupdate on"
  mu autoupdate set min_free_gb 0 >/dev/null   # the disk test sets its own
}
mu() { /bin/bash "$MU_HOME/bin/mu" "$@"; }
st() { awk -v k="$1" 'index($0, k "=") == 1 { v = substr($0, length(k) + 2) } END { print v }' "$MU_HOME/state/autoupdate.state" 2>/dev/null; }
calls() { grep -c . "$MU_TEST_CALLS" 2>/dev/null || true; }
def() { cat "$MU_HOME/default"; }
rev() { sed -n 's/^MU_DISPATCHER_REV=\([0-9]*\).*/\1/p' "$MU_HOME/bin/mu" | head -1; }
REV="$(sed -n 's/^MU_DISPATCHER_REV=\([0-9]*\).*/\1/p' "$REPO/bin/mu")"   # this checkout's dispatcher
nlogs() { ls "$MU_HOME/logs" 2>/dev/null | grep -c '^autoupdate-'; }

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
has "4th run: skipped" "$out" "failed 3 times: not retried"
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

section "free disk check on WSL (the Windows drive under the virtual disk counts)"
mkrepo wsldisk "v2026.11.4:success v2026.11.5:success"
fresh wsldisk wsldisk
mkdir -p "$ROOT/mnt/c"
# reg.exe answers in CRLF; drvfs C: is mounted at $ROOT/mnt/c; a df stub reports 5 GB free there (the VHD: 900 GB)
cat > "$STUBS/reg.exe" <<'S'
#!/bin/sh
sed 's/$/\r/' <<'R'

HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Lxss\{1}
    DistributionName    REG_SZ    Ubuntu-24.04
    BasePath    REG_SZ    C:\Users\u\WSL
R
S
chmod +x "$STUBS/reg.exe"
printf 'C:\\134 %s 9p rw,aname=drvfs;path=C:\\ 0 0\n' "$(printf '%s' "$ROOT/mnt/c" | sed 's/ /\\040/g')" > "$ROOT/mounts"
cat > "$STUBS/df" <<S
#!/bin/sh
case "\$2" in "$ROOT/mnt/c"*) a=5242880 ;; *) a=943718400 ;; esac
printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\nfake 999999999 1 %s 1%% %s\n' "\$a" "\$2"
S
chmod +x "$STUBS/df"
mu autoupdate set min_free_gb 12 >/dev/null
out="$(MU_WSL=1 WSL_DISTRO_NAME=Ubuntu-24.04 MU_WSL_REG="$STUBS/reg.exe" MU_PROC_MOUNTS="$ROOT/mounts" mu autoupdate run)"
eq "WSL: installer not called" "$(calls)" 0
has "WSL: skipped, naming the Windows drive" "$out" "5 GB free on C: (WSL disk), auto-update waits for 12 GB"
# no answer from Windows (interop off): C: is only a guess, so it is reported but not enforced (the VHD is checked)
out="$(MU_WSL=1 WSL_DISTRO_NAME=Ubuntu-24.04 MU_WSL_REG="$STUBS/no-reg.exe" MU_PROC_MOUNTS="$ROOT/mounts" mu autoupdate run)"
has "WSL, interop off: the guessed drive is reported (in the run's log)" "$(cat "$(ls -t "$MU_HOME"/logs/autoupdate-[0-9]*.log | head -1)")" \
  "note: 5 GB free on C: if the WSL disk is there (no answer from Windows"
eq "WSL, interop off: installs (the guess is not enforced)" "$(def)" v2026.11.5
rm -f "$STUBS/df" "$STUBS/reg.exe"
mu autoupdate set min_free_gb 0 >/dev/null

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
has "status: schedule" "$out" "schedule    daily from 04:"
has "status: next check" "$out" "next check  due: the next hourly run"
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

section "a failed release's dispatcher never stays (bin/mu and git-lfs are shared by every version)"
mkrepo disp "v2026.11.4:success v2026.11.5:broken"
fresh disp disp
sum_mu="$(cksum < "$MU_HOME/bin/mu")"; sum_lfs="$(cksum < "$MU_HOME/bin/git-lfs")"
mu autoupdate run >/dev/null; rc=$?
eq "auto-update: run exits 1" "$rc" 1
eq "auto-update: bin/mu restored (this checkout's rev, not the failed release's 99)" "$(rev)" "$REV"
eq "auto-update: bin/mu is the previous file" "$(cksum < "$MU_HOME/bin/mu")" "$sum_mu"
eq "auto-update: bin/git-lfs restored" "$(cksum < "$MU_HOME/bin/git-lfs")" "$sum_lfs"
hasnt "auto-update: mu versions works" "$(mu versions 2>&1)" "BROKEN"
eq "auto-update: default kept" "$(def)" v2026.11.4
out="$(mu update 2>&1)"; rc=$?
check "mu update: exits non-zero" test "$rc" != 0
eq "mu update: bin/mu restored" "$(rev)" "$REV"
eq "mu update: bin/git-lfs restored" "$(cksum < "$MU_HOME/bin/git-lfs")" "$sum_lfs"
eq "mu update: default kept" "$(def)" v2026.11.4
mkrepo disp2 "v2026.11.4:success v2026.11.5:newdisp"
fresh disp2 disp2
mu autoupdate run >/dev/null
eq "a release that passes keeps its newer dispatcher (rev 99)" "$(rev)" 99
eq "... and is the default" "$(def)" v2026.11.5

section "the release tag is installed, never a branch with the same name"
mkrepo tagbr "v2026.11.4:success v2026.11.5:success"
w="$ROOT/work/tagbr"; echo fail > "$w/mode"; "$GIT" -C "$w" commit -qam "branch named like the release"
"$GIT" -C "$w" push -q "$ROOT/repos/tagbr.git" "HEAD:refs/heads/v2026.11.5" 2>/dev/null
fresh tagbr tagbr
mu autoupdate run >/dev/null; rc=$?
eq "run exits 0" "$rc" 0
eq "installed the tag's content (mode success), not the branch's (fail)" "$(cut -d' ' -f2 "$MU_TEST_CALLS")" success
eq "default v2026.11.5" "$(def)" v2026.11.5

section "busy marker set during the install: no switch, no gc until it is gone"
mkrepo busy2 "v2026.11.4:success v2026.11.5:slow"
fresh busy2 busy2
for v in v2026.11.2 v2026.11.3; do fake_installed "$v"; done
sleep 60 & job=$!
FAKE_SLEEP=3 mu autoupdate run > "$ROOT/busy2.out" 2>&1 & r1=$!
sleep 1.5; echo "$job" > "$MU_HOME/state/busy"
wait "$r1"; rc=$?
eq "run exits 0" "$rc" 0
eq "default not switched while busy" "$(def)" v2026.11.4
check "v2026.11.5 is installed" test -f "$MU_HOME/toolchains/v2026.11.5/MANIFEST.json"
check "v2026.11.2 not removed" test -f "$MU_HOME/toolchains/v2026.11.2/MANIFEST.json"
check "v2026.11.3 not removed" test -f "$MU_HOME/toolchains/v2026.11.3/MANIFEST.json"
eq "last_result deferred" "$(st last_result)" deferred
eq "pending_switch recorded" "$(st pending_switch)" v2026.11.5
has "summary says why" "$(cat "$ROOT/busy2.out")" "becomes the default once"
kill "$job" 2>/dev/null; wait "$job" 2>/dev/null
mu autoupdate run >/dev/null; rc=$?
eq "busy gone: run exits 0" "$rc" 0
eq "busy gone: switched" "$(def)" v2026.11.5
eq "busy gone: not installed again" "$(calls)" 1
check "busy gone: gc removed v2026.11.3" test ! -e "$MU_HOME/toolchains/v2026.11.3"
eq "pending_switch cleared" "$(st pending_switch)" ""

section "a hung install is stopped after the time limit and counts as a try"
mkrepo hang "v2026.11.4:success v2026.11.5:hang"
fresh hang hang
t0=$SECONDS
out="$(MU_AUTOUPDATE_TIMEOUT_S=3 mu autoupdate run)"; rc=$?
eq "run exits 1" "$rc" 1
check "stopped within ~10 s ($((SECONDS - t0)) s)" test $((SECONDS - t0)) -lt 15
eq "fail_count 1" "$(st fail_count)" 1
eq "fail_reason timeout" "$(st fail_reason)" timeout
has "summary" "$out" "did not finish within 3 s"
check "the installer's sleep was killed" sh -c '! pgrep -f "sleep 599" >/dev/null'
eq "bin/mu restored after the stop" "$(rev)" "$REV"
eq "default kept" "$(def)" v2026.11.4
check "no install lock left behind" sh -c "! test -d '$MU_HOME/.install.lock' || ! ps -p \"\$(cat '$MU_HOME/.install.lock/pid' 2>/dev/null)\" >/dev/null"
has "mu status warns" "$(mu)" "v2026.11.5 did not finish installing"

section "a run killed during the install (systemd stop, launchd bootout) counts as a try"
mkrepo kill "v2026.11.4:success v2026.11.5:hang"
fresh kill kill
mu autoupdate run > /dev/null 2>&1 & r1=$!
for i in 1 2 3 4 5 6 7 8 9 10; do [ "$(calls)" -ge 1 ] && break; sleep 0.5; done
sleep 1
kt() { local c; for c in $(pgrep -P "$1"); do kt "$c"; done; kill -TERM "$1" 2>/dev/null; }
kt "$r1"; wait "$r1" 2>/dev/null
sleep 1
eq "fail_count 1 (recorded before the install started)" "$(st fail_count)" 1
eq "fail_version" "$(st fail_version)" v2026.11.5
eq "fail_reason interrupted" "$(st fail_reason)" interrupted
eq "bin/mu restored by the TERM trap" "$(rev)" "$REV"
check "the installer's sleep was killed" sh -c '! pgrep -f "sleep 599" >/dev/null'
has "mu status says it was stopped" "$(mu)" "v2026.11.5 was stopped while installing"
for i in 2 3; do MU_AUTOUPDATE_TIMEOUT_S=1 mu autoupdate run >/dev/null; done
eq "two more (timed-out) tries: fail_count 3" "$(st fail_count)" 3
out="$(mu autoupdate run)"
has "then it is not retried" "$out" "failed 3 times: not retried"
eq "installer called 3 times" "$(calls)" 3

section "failures that are not the release's do not use up its tries"
mkrepo envf "v2026.11.4:success v2026.11.5:success"
fresh envf envf
mkdir -p "$MU_CACHE/src"; chmod a-w "$MU_CACHE/src"
for i in 1 2 3 4; do mu autoupdate run >/dev/null; done
chmod u+w "$MU_CACHE/src"
eq "recipe download fails 4×: fail_count empty" "$(st fail_count)" ""
eq "last_result error" "$(st last_result)" error
has "says it could not start" "$(st last_message)" "could not start installing"
mu autoupdate run >/dev/null
eq "then it installs" "$(def)" v2026.11.5
mkrepo apt "v2026.11.4:success v2026.11.5:apt"
fresh apt apt
mu autoupdate run >/dev/null; rc=$?
eq "missing packages: exits 1" "$rc" 1
eq "missing packages: needs-admin" "$(st last_result)" needs-admin
eq "missing packages: fail_count empty" "$(st fail_count)" ""
has "mu status: the admin step" "$(mu)" "sudo ./install.sh --system"
mkrepo build "v2026.11.4:success v2026.11.5:build"
fresh build build
mu autoupdate run >/dev/null
eq "download error in the build: fail_count empty" "$(st fail_count)" ""
eq "download error in the build: err_count 1" "$(st err_count)" 1
has "mu status: could not be installed" "$(mu)" "v2026.11.5 could not be installed (1×"
check "incomplete toolchain removed" test ! -e "$MU_HOME/toolchains/v2026.11.5"
mkrepo lk "v2026.11.4:success v2026.11.5:success"
fresh lk lk
sed 's/^  ( set -e; export MU_NO_AUTOUPDATE=1/  mkdir "$MU_HOME\/.install.lock"; echo $$ > "$MU_HOME\/.install.lock\/pid"; ( set -e; export MU_NO_AUTOUPDATE=1/' "$REPO/bin/mu" > "$MU_HOME/bin/mu"
mu autoupdate run >/dev/null; rm -rf "$MU_HOME/.install.lock"
eq "install lock taken between the check and the installer: not counted" "$(st fail_count)" ""
has "... reported as an error" "$(st last_message)" "another μ install is running"
install -m 755 "$REPO/bin/mu" "$MU_HOME/bin/mu"

section "scheduled runs: hourly, but one completed check a day; skips are retried the next hour"
mkrepo sch "v2026.11.4:success"
fresh sch sch
n0="$(nlogs)"
mu autoupdate run --scheduled >/dev/null
eq "first scheduled run checks" "$(st last_result)" up-to-date
n1="$(nlogs)"; eq "it wrote a log" "$n1" $((n0 + 1))
mu autoupdate run --scheduled >/dev/null
eq "second scheduled run the same day: nothing, no log" "$(nlogs)" "$n1"
has "status: next check is tomorrow" "$(mu autoupdate status)" "next check  20"
printf 'done_epoch=1\n' >> "$MU_HOME/state/autoupdate.state"
mu autoupdate run --scheduled >/dev/null
eq "a day later: checks again" "$(nlogs)" $((n1 + 1))
mkrepo sch2 "v2026.11.4:success v2026.11.5:success"
fresh sch2 sch2
touch "$FLAGS/battery"
mu autoupdate run --scheduled >/dev/null
eq "on battery: skipped" "$(st last_result)" skipped
mu autoupdate run --scheduled >/dev/null
eq "the next hour, still on battery: tried again (not done for the day)" "$(nlogs)" 2
rm -f "$FLAGS/battery"
mu autoupdate run --scheduled >/dev/null
eq "the next hour, on AC: installs" "$(def)" v2026.11.5
mu autoupdate off >/dev/null; n1="$(nlogs)"
printf 'done_epoch=1\n' >> "$MU_HOME/state/autoupdate.state"
mu autoupdate run --scheduled >/dev/null
eq "off: a scheduled run does nothing (no log)" "$(nlogs)" "$n1"

section "network: listing is retried; ssh keeps core.sshCommand (deploy keys) and never prompts"
mkrepo net "v2026.11.4:success"
fresh net net
echo "file://$ROOT/repos/missing.git" > "$MU_HOME/source"
mu autoupdate run >/dev/null; rc=$?
eq "unreachable source: exits 1" "$rc" 1
has "it retried the listing" "$(cat "$(st last_log)")" "could not list releases (try 2 of 3)"
eq "unreachable source: error" "$(st last_result)" error
"$GIT" config --global core.sshCommand "'$STUBS/fake-ssh' -i /keys/deploy"
echo "ssh://fakehost.invalid/mu.git" > "$MU_HOME/source"
rm -f "$CALLS/ssh.log"; mu autoupdate run >/dev/null
has "core.sshCommand used (deploy key)" "$(cat "$CALLS/ssh.log" 2>/dev/null)" "-i /keys/deploy"
has "with BatchMode=yes" "$(cat "$CALLS/ssh.log" 2>/dev/null)" "BatchMode=yes"
"$GIT" config --global --unset core.sshCommand
mkrepo caf "v2026.11.4:success v2026.11.5:success"
fresh caf caf
mu autoupdate run >/dev/null
has "macOS: the install runs under caffeinate -i -s (stub)" "$(cat "$CALLS/caffeinate.log" 2>/dev/null)" "-i -s -w "

section "gc keeps everything while a registered project's mu.toml can't be read"
mkrepo gcu "v2026.11.4:success v2026.11.5:success"
fresh gcu gcu
fake_installed v2026.11.2
printf '%s\n' "$ROOT/projects/unplugged drive/proj" >> "$MU_HOME/projects.txt"
mu autoupdate run >/dev/null
eq "default switched" "$(def)" v2026.11.5
check "v2026.11.2 kept" test -f "$MU_HOME/toolchains/v2026.11.2/MANIFEST.json"
has "log says why" "$(cat "$(st last_log)")" "gc: skipped: can't read the mu.toml"

section "launchd: LaunchAgent (generated into a temp folder, loaded only into the launchctl stub)"
export MU_AUTOUPDATE_SCHEDULER=launchd
mkrepo sched "v2026.11.4:success v2026.11.5:slow"
fresh "sched & <odd>" sched
P="$MU_LAUNCH_AGENTS_DIR/systems.martand.mu.autoupdate.plist"
check "plist written" test -f "$P"
if command -v plutil >/dev/null 2>&1; then
  out="$(plutil -lint "$P")"; has "plutil -lint passes" "$out" ": OK"
  eq "ProgramArguments[0] is MU_HOME/bin/mu (spaces, & and <> intact)" "$(plutil -extract ProgramArguments.0 raw -o - "$P")" "$MU_HOME/bin/mu"
  eq "ProgramArguments[1..3]" "$(plutil -extract ProgramArguments.1 raw -o - "$P") $(plutil -extract ProgramArguments.2 raw -o - "$P") $(plutil -extract ProgramArguments.3 raw -o - "$P")" "autoupdate run --scheduled"
  eq "EnvironmentVariables.MU_HOME" "$(plutil -extract EnvironmentVariables.MU_HOME raw -o - "$P")" "$MU_HOME"
  check "StartCalendarInterval has no Hour (fires hourly)" sh -c "! plutil -extract StartCalendarInterval.Hour raw -o - '$P' >/dev/null 2>&1"
  m="$(plutil -extract StartCalendarInterval.Minute raw -o - "$P")"
  check "StartCalendarInterval.Minute is 0-59 ($m)" test "$m" -ge 0 -a "$m" -le 59
  eq "minute matches autoupdate.conf" "$m" "$(grep '^minute=' "$MU_HOME/autoupdate.conf" | cut -d= -f2 | sed 's/^0\([0-9]\)/\1/')"
else echo "  skip  plutil (not macOS)"; fi
has "loaded into the gui domain (stub)" "$(cat "$CALLS/launchctl.log")" "bootstrap gui/$(id -u) $P"
has "status: loaded" "$(mu autoupdate status)" "(launchd: loaded)"
n="$(grep -c . "$CALLS/launchctl.log")"
mu autoupdate set hour 3 >/dev/null
eq "set hour: no reload (each run reads the hour)" "$(grep -c . "$CALLS/launchctl.log")" "$n"
mu autoupdate set minute 7 >/dev/null
command -v plutil >/dev/null 2>&1 && eq "set minute 7 rewrites the plist" "$(plutil -extract StartCalendarInterval.Minute raw -o - "$P")" 7
has "status: daily from 03:07" "$(mu autoupdate status)" "daily from 03:07"
FAKE_SLEEP=4 mu autoupdate run > /dev/null 2>&1 & r1=$!; sleep 1.5
n="$(grep -c . "$CALLS/launchctl.log")"
out="$(mu autoupdate set minute 9 2>&1)"
eq "during a run: no bootout/bootstrap" "$(grep -c . "$CALLS/launchctl.log")" "$n"
has "during a run: says when it applies" "$out" "an auto-update is running now"
has "status: a change is waiting" "$(mu autoupdate status)" "a schedule change is waiting"
has "mu status: checking now" "$(mu)" "auto-update: checking for a new release now"
wait "$r1"; rc=$?
eq "the running update was not stopped" "$rc" 0
eq "... and switched" "$(def)" v2026.11.5
mu autoupdate on >/dev/null 2>&1
hasnt "on afterwards: the change is applied" "$(mu autoupdate status)" "a schedule change is waiting"
touch "$FLAGS/bootstrap-fails"
out="$(mu autoupdate on 2>&1)"
has "bootstrap error is reported" "$out" "Input/output error"
hasnt "no fallback to user/ when gui/ exists" "$(tail -1 "$CALLS/launchctl.log")" "bootstrap user/"
rm -f "$FLAGS/bootstrap-fails"; touch "$FLAGS/no-gui"
mu autoupdate on >/dev/null 2>&1
has "no login session: user/ domain" "$(tail -1 "$CALLS/launchctl.log")" "bootstrap user/$(id -u) "
rm -f "$FLAGS/no-gui"
mu autoupdate off >/dev/null
check "off removes the plist" test ! -e "$P"
has "off unloads it" "$(tail -1 "$CALLS/launchctl.log")" "bootout gui/$(id -u)/systems.martand.mu.autoupdate"
eq "off is remembered" "$(grep '^enabled=' "$MU_HOME/autoupdate.conf")" "enabled=off"
check "the real ~/Library/LaunchAgents was not used" test ! -e "$HOME/Library/LaunchAgents"

section "systemd: user service + timer (written to a temp folder; systemctl/loginctl stubs)"
export MU_AUTOUPDATE_SCHEDULER=systemd
mkdir -p "$MU_SYSTEMD_RUN_DIR" "$MU_RUN_USER_DIR/$(id -u)"
mkrepo sd "v2026.11.4:success"
rm -f "$CALLS/systemctl.log"
fresh 'sd 50% $HOME x' sd
SD="$MU_SYSTEMD_USER_DIR"
check "service written" test -f "$SD/mu-autoupdate.service"
check "timer written" test -f "$SD/mu-autoupdate.timer"
svc="$(cat "$SD/mu-autoupdate.service")"; tmr="$(cat "$SD/mu-autoupdate.timer")"
esc="$(printf '%s' "$MU_HOME/bin/mu" | sed -e 's/%/%%/g' -e 's/\$/$$/g')"
has "ExecStart quotes the path, escapes % and \$" "$svc" "ExecStart=\"$esc\" autoupdate run --scheduled"
has "Environment MU_HOME (escapes %)" "$svc" "Environment=\"MU_HOME=$(printf '%s' "$MU_HOME" | sed 's/%/%%/g')\""
has "Type=oneshot" "$svc" "Type=oneshot"
has "OnCalendar hourly at the minute" "$tmr" "OnCalendar=*-*-* *:"
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
has "status: next check" "$(mu autoupdate status)" "next check  due"
printf '%s' "$MU_RUN_USER_DIR/$(id -u)" > "$FLAGS/need-xdg"
out="$(mu autoupdate on 2>&1)"; rc=$?
eq "sudo -u / su shell (no XDG_RUNTIME_DIR): on works via /run/user/UID" "$rc" 0
has "... and the timer is enabled" "$(mu autoupdate status)" "(systemd: loaded)"
out="$(XDG_RUNTIME_DIR="$ROOT" mu autoupdate on 2>&1)"; rc=$?
eq "this user's own XDG_RUNTIME_DIR is kept; the manager can't be reached there: exits 1" "$rc" 1
has "... message: can't reach the user manager, with the fix" "$out" "can't reach $(id -un)'s systemd user manager"
rm -f "$FLAGS/need-xdg"
mu autoupdate off >/dev/null
check "off removes the units" test ! -e "$SD/mu-autoupdate.timer"
touch "$FLAGS/no-systemd"; rmdir "$MU_SYSTEMD_RUN_DIR"
out="$(mu autoupdate on 2>&1)"; rc=$?
eq "no systemd: on exits 1" "$rc" 1
has "no systemd: clear message" "$out" "no systemd on this machine"
out="$(mu autoupdate on --quiet 2>/dev/null)"
has "--quiet says why in its one line" "$out" "on, but not scheduled: no systemd"
eq "the choice stays on (the installer retries while it says on)" "$(grep '^enabled=' "$MU_HOME/autoupdate.conf")" "enabled=on"
rm -f "$FLAGS/no-systemd"; mkdir -p "$MU_SYSTEMD_RUN_DIR"
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
has "hook skips CI / --no-modify-path / MU_NO_AUTOUPDATE" "$src" '[ "$AUTOUPDATE" = 1 ] && [ "$MODIFY_PATH" = 1 ] && [ "${MU_NO_AUTOUPDATE:-}" != 1 ] && [ "${CI:-}" != true ]'
hook_cond() { ! grep -Eqs '^[[:space:]]*enabled[[:space:]]*=[[:space:]]*off' "$1"; }
has "hook condition in install.sh" "$src" "! grep -Eqs '^[[:space:]]*enabled[[:space:]]*=[[:space:]]*off' \"\$MU_HOME/autoupdate.conf\""
check "hook runs with no autoupdate.conf" hook_cond "$ROOT/nonexistent.conf"
printf 'enabled=on\n' > "$ROOT/h.conf"; check "hook runs again while it says on (scheduling is retried)" hook_cond "$ROOT/h.conf"
printf 'enabled = off\n' > "$ROOT/h.conf"; check "hook never overrides off" sh -c "! grep -Eqs '^[[:space:]]*enabled[[:space:]]*=[[:space:]]*off' \"\$1\"; test \$? = 1" _ "$ROOT/h.conf"
has "installer reports the failing stage to mu autoupdate" "$src" 'printf '"'"'%s\t%s\n'"'"' "${STAGE_KEY:-preflight}"'
eq "dispatcher revs match" "$(sed -n 's/^DISPATCHER_REV=\([0-9]*\).*/\1/p' "$REPO/install/install.sh")" "$(sed -n 's/^MU_DISPATCHER_REV=\([0-9]*\).*/\1/p' "$REPO/bin/mu")"

printf '\n%s passed, %s failed\n' "$PASS" "$FAILN"
[ "$FAILN" = 0 ] || { printf 'failed:%s\n' "$FAILED"; exit 1; }
