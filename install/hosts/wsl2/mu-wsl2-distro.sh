#!/bin/bash
# μ WSL2 host adapter, Linux side: makes this WSL2 distro a μ compute node. Run it as root inside the distro. The Windows
# script (mu-wsl2-host.ps1) copies it to /usr/local/sbin/mu-wsl2-distro and runs it there; it also works by hand:
#   wsl -d mu-compute -u root -- /usr/local/sbin/mu-wsl2-distro status
#
#   mu-wsl2-distro install [options]      set the distro up (idempotent: a second run changes nothing and says so)
#   mu-wsl2-distro status [options]       check everything, change nothing; exit 1 if any check fails
#   mu-wsl2-distro uninstall [options]    undo the system changes (keeps the user, its files, its key and the clone)
#   mu-wsl2-distro heartbeat on|off       the heartbeat test aid on its own (see --heartbeat)
#   mu-wsl2-distro stop-marker boot|stop  used by mu-clean-stop.service
#
#   --user NAME          the unprivileged user μ runs as (default: mu); created without sudo
#   --repo URL           the μ repo (default: git@github.com:martand-systems/mu.git)
#   --branch REF         branch or tag to clone (default: the repo's default branch)
#   --clone              clone the repo into ~NAME/mu once the deploy key works (never updates an existing clone)
#   --install-mu         then install μ: install.sh --system --user NAME as root, then ./install.sh as NAME (implies
#                        --clone; the first install takes 1-2 h, mostly downloads). Skipped once ~NAME/.local/bin/mu
#                        exists: μ updates itself
#   --heartbeat on|off   install / remove mu-heartbeat.service, a test aid that appends the UTC time to
#                        ~NAME/heartbeat.log every 60 s (the log is kept; status analyses it while the unit is on)
#   --json               status: one JSON object on stdout
#   --offline            status: skip the network check (GitHub access)
#   --gap SECONDS        status: report heartbeat gaps longer than this (default 90)
#
# What install does, each step only when needed:
#   /etc/wsl.conf        [boot] systemd=true, [user] default=NAME, [interop] appendWindowsPath=false (other keys kept;
#                        the original is saved in /var/lib/mu-wsl2/ and uninstall puts it back)
#   user NAME            a normal user without sudo; root work goes through `wsl -d <distro> -u root`
#   write-back           /etc/sysctl.d/60-mu-writeback.conf: vm.dirty_expire_centisecs=500, dirty_writeback=100.
#                        Windows can stop the WSL VM without a Linux shutdown; data still in the page cache is then lost
#                        (a file can come back with its size updated and NUL bytes instead of data). This shrinks that
#                        window from about 35 s to about 6 s; it does not close it (only fsync does)
#   journald             SyncIntervalSec=30s (the journal of the last minutes survives a hard stop)
#   stop timeouts        DefaultTimeoutStopSec=8s for system and user services, TimeoutStopSec=9s for user@.service:
#                        WSL gives systemd 10 s ([boot] initTimeout) to power off
#   mu-clean-stop.service  writes and fsyncs /var/lib/mu-wsl2/clean-stop when systemd stops; at the next start it logs
#                        whether the previous stop was clean to /var/lib/mu-wsl2/stops.log
#   linger               NAME's systemd user services (μ auto-update) run without a login
#   GitHub access        NAME's key ~/.ssh/mu_github (ed25519), ~/.ssh/config for github.com, known_hosts pinned to
#                        GitHub's ed25519 host key from api.github.com/meta (cross-checked with ssh-keyscan). The key
#                        has to be added on GitHub as a read-only deploy key by hand: install prints how
# Exit codes: 0 done (install: see /run/mu-wsl2/install-result for changed=N restart=0|1), 1 failed, 2 usage.
# Test hooks (tools/test-wsl2-host/run.sh sources this file): nothing runs when it is sourced.
set -euo pipefail

MU_USER="mu"
REPO="git@github.com:martand-systems/mu.git"
BRANCH=""; CLONE=0; INSTALL_MU=0; HEARTBEAT=""; JSON=0; OFFLINE=0; GAP=90
STATE_DIR="/var/lib/mu-wsl2"
SELF_PATH="/usr/local/sbin/mu-wsl2-distro"
# this file, resolved now: main does `cd /` later, and a relative path (./mu-wsl2-distro.sh) would then point elsewhere
SELF_SRC="$(readlink -f "${BASH_SOURCE[0]}" 2> /dev/null || true)"
[ -n "$SELF_SRC" ] || SELF_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/$(basename "${BASH_SOURCE[0]}")"
MARK_BEGIN="# >>> mu-wsl2: GitHub access for mu (managed by mu-wsl2-distro; edits inside this block are replaced)"
MARK_END="# <<< mu-wsl2"

# ================================================================ pure functions (tested by tools/test-wsl2-host)

# ini_set SECTION KEY VALUE < in > out: sets KEY=VALUE in [SECTION] (section and key names compare case-insensitively),
# keeping every other line. A key that already has this value (ignoring spaces around =) is left exactly as written.
# A missing key goes after the last non-blank line of the section's first occurrence; a missing section is appended.
ini_set() {
  LC_ALL=C awk -v sec="$1" -v key="$2" -v val="$3" '
    function trim(s) { sub(/^[ \t]+/, "", s); sub(/[ \t\r]+$/, "", s); return s }
    function is_header(s) { return s ~ /^[ \t]*\[[^]]*\][ \t\r]*$/ }
    function header_name(s) { s = trim(s); return tolower(trim(substr(s, 2, length(s) - 2))) }
    function flush_blanks(   i) { for (i = 1; i <= nb; i++) print blank[i]; nb = 0 }
    function emit(s) { print s; last = s }
    BEGIN { want = tolower(sec); k = tolower(key); insec = 0; first = 1; done = 0; seen = 0; nb = 0; last = "" }
    is_header($0) {
      if (insec && first && !done) { emit(key "=" val); done = 1 }
      if (insec) first = 0
      flush_blanks()
      insec = (header_name($0) == want); if (insec) seen = 1
      emit($0); next
    }
    insec && /^[ \t\r]*$/ { blank[++nb] = $0; next }
    insec {
      flush_blanks()
      l = $0
      if (l !~ /^[ \t]*[#;]/ && index(l, "=") > 0) {
        kk = tolower(trim(substr(l, 1, index(l, "=") - 1)))
        if (kk == k) {
          v = trim(substr(l, index(l, "=") + 1))
          if (v == val) emit(l); else emit(key "=" val)
          done = 1; next
        }
      }
      emit(l); next
    }
    { emit($0) }
    END {
      if (insec && first && !done) { emit(key "=" val); done = 1 }
      flush_blanks()
      if (!seen) {
        if (NR > 0 && last !~ /^[ \t\r]*$/) print ""
        print "[" sec "]"; print key "=" val
      }
    }'
}

# ini_get SECTION KEY < in: prints the value of KEY in [SECTION] (the first one if repeated: WSL uses the first and
# warns about the others); nothing if absent
ini_get() {
  LC_ALL=C awk -v sec="$1" -v key="$2" '
    function trim(s) { sub(/^[ \t]+/, "", s); sub(/[ \t\r]+$/, "", s); return s }
    BEGIN { want = tolower(sec); k = tolower(key); insec = 0; found = 0 }
    /^[ \t]*\[[^]]*\][ \t\r]*$/ { s = trim($0); insec = (tolower(trim(substr(s, 2, length(s) - 2))) == want); next }
    !found && insec && $0 !~ /^[ \t]*[#;]/ && index($0, "=") > 0 {
      if (tolower(trim(substr($0, 1, index($0, "=") - 1))) == k) { v = trim(substr($0, index($0, "=") + 1)); found = 1 }
    }
    END { if (found) print v }'
}

# hb_analyze GAP_SECONDS [SINCE_EPOCH] < heartbeat.log: one record per line, tab-separated:
#   lines N | valid N | first TS | last TS | last_epoch N | nul BYTES
#   nulline LINE COUNT PREV_TS NEXT_TS   a line holding NUL bytes: data lost by an unclean stop (its size was written,
#                                        its data was not). NEXT_TS is the first timestamp written after them ("-": none
#                                        yet), the first heartbeat after the restart that followed that stop
#   nulold LINE COUNT PREV_TS NEXT_TS    the same, for a stop before SINCE_EPOCH (NEXT_TS earlier than it)
#   gap PREV_TS NEXT_TS SECONDS          consecutive timestamps further apart than GAP_SECONDS
#   back PREV_TS NEXT_TS                 time went backwards
#   bad LINE TEXT                        a line that is not a timestamp (torn write, or not written by the heartbeat)
# Timestamps are `date -u +%FT%TZ`. No mktime: portable to mawk, gawk and BSD awk.
hb_analyze() {
  LC_ALL=C tr '\000' '\001' | LC_ALL=C awk -v gap="${1:-90}" -v since="${2:-0}" -v soh="$(printf '\001')" '
    function epoch(ts,   y, mo, d, h, mi, s, yy, era, yoe, mm, doy, doe) {
      y = substr(ts, 1, 4) + 0; mo = substr(ts, 6, 2) + 0; d = substr(ts, 9, 2) + 0
      h = substr(ts, 12, 2) + 0; mi = substr(ts, 15, 2) + 0; s = substr(ts, 18, 2) + 0
      yy = (mo <= 2) ? y - 1 : y; era = int(yy / 400); yoe = yy - era * 400
      mm = (mo > 2) ? mo - 3 : mo + 9; doy = int((153 * mm + 2) / 5) + d - 1
      doe = yoe * 365 + int(yoe / 4) - int(yoe / 100) + doy
      return (era * 146097 + doe - 719468) * 86400 + h * 3600 + mi * 60 + s
    }
    function is_ts(s) { return s ~ /^[0-9][0-9][0-9][0-9]-[01][0-9]-[0-3][0-9]T[0-2][0-9]:[0-5][0-9]:[0-6][0-9]Z$/ }
    function clean(s) { gsub(/[^ -~]/, "?", s); return substr(s, 1, 60) }
    BEGIN { valid = 0; nul = 0; prev = ""; pe = 0; npend = 0 }
    {
      l = $0; sub(/\r$/, "", l)
      n = gsub(soh, "", l)
      if (n > 0) { nul += n; pend_line[++npend] = NR; pend_n[npend] = n; pend_prev[npend] = prev }
      if (l == "") { if (n == 0) print "bad\t" NR "\t(empty line)"; next }
      if (!is_ts(l)) { print "bad\t" NR "\t" clean(l); next }
      e = epoch(l)
      kind = (e < since + 0) ? "nulold" : "nulline"
      for (i = 1; i <= npend; i++) print kind "\t" pend_line[i] "\t" pend_n[i] "\t" (pend_prev[i] == "" ? "-" : pend_prev[i]) "\t" l
      npend = 0
      if (prev != "") {
        if (e < pe) print "back\t" prev "\t" l
        else if (e - pe > gap) printf "gap\t%s\t%s\t%d\n", prev, l, e - pe
      } else first = l
      prev = l; pe = e; valid++
    }
    END {
      for (i = 1; i <= npend; i++) print "nulline\t" pend_line[i] "\t" pend_n[i] "\t" (pend_prev[i] == "" ? "-" : pend_prev[i]) "\t-"
      print "lines\t" NR; print "valid\t" valid; print "nul\t" nul
      if (valid > 0) { print "first\t" first; print "last\t" prev; printf "last_epoch\t%d\n", pe }
    }'
}

# gh_meta_ed25519 < api.github.com/meta JSON: prints GitHub's ed25519 host key ("ssh-ed25519 AAAA..."), or nothing
gh_meta_ed25519() {
  LC_ALL=C tr -d '\r\n' | LC_ALL=C grep -o '"ssh-ed25519 [A-Za-z0-9+/]*=*"' | head -n 1 | tr -d '"' || true
}

# ssh_config_merge KEYFILE < config > config': puts the managed github.com block first (ssh uses the first value it
# finds), replacing an earlier copy of it; ends with "Host *" so that settings after it stay global
ssh_config_merge() {
  local key="$1"
  printf '%s\n' "$MARK_BEGIN" "Host github.com" "  HostName github.com" "  User git" "  IdentityFile $key" \
    "  IdentitiesOnly yes" "  HostKeyAlgorithms ssh-ed25519" "  StrictHostKeyChecking yes" "Host *" "$MARK_END"
  LC_ALL=C awk -v b="$MARK_BEGIN" -v e="$MARK_END" '
    $0 == b { skip = 1; next }
    skip && $0 == e { skip = 0; next }
    skip { next }
    { line[++n] = $0 }
    END {
      s = 1; while (s <= n && line[s] ~ /^[ \t\r]*$/) s++
      if (s <= n) { print ""; for (i = s; i <= n; i++) print line[i] }
    }'
}

# repo_slug URL: OWNER/REPO for a GitHub URL (ssh or https), or nothing
repo_slug() {
  printf '%s\n' "$1" | sed -n -E 's#^(git@github\.com:|ssh://git@github\.com/|https://github\.com/)([^/]+/[^/]+)$#\2#p' | sed 's/\.git$//'
}

# git_error_class < git's stderr: "denied: <reason>" or "unreachable: <reason>". The reason is the line that says what
# went wrong: git always ends with the same "Please make sure you have the correct access rights" trailer
git_error_class() {
  local all why
  all="$(cat)"
  why="$(printf '%s\n' "$all" | grep -m 1 -E 'Permission denied|Repository not found|Host key verification failed|Could not resolve|timed out|Connection refused|Connection closed|Network is unreachable|No route to host' || true)"
  [ -n "$why" ] || why="$(printf '%s\n' "$all" | grep -v '^[[:space:]]*$' | head -n 1 || true)"
  case "$why" in
    *"Permission denied"*|*"Repository not found"*) echo "denied: $why" ;;
    *) echo "unreachable: ${why:-timed out}" ;;
  esac
}

# installsh_supports_user FILE: does this install.sh (the real one, install/install.sh) take --system --user NAME?
installsh_supports_user() { grep -Eq '^#[[:space:]]+--user NAME' "$1" 2>/dev/null; }

# json_str TEXT: TEXT as a JSON string literal
json_str() {
  local s
  s="$(printf '%s' "$1" | LC_ALL=C tr -d '\000-\010\013\014\016-\037')"
  s="${s//\\/\\\\}"; s="${s//\"/\\\"}"; s="${s//$'\t'/\\t}"; s="${s//$'\n'/\\n}"; s="${s//$'\r'/\\r}"
  printf '"%s"' "$s"
}

# stop markers (DIR): mu-clean-stop.service runs `boot` when it starts and `stop` when systemd stops it
marker_boot() {
  local d="$1" now result since=""
  mkdir -p "$d"; now="$(date -u +%FT%TZ)"
  if [ -f "$d/clean-stop" ]; then result=clean; since="$(head -c 40 "$d/clean-stop" | tr -cd '0-9TZ:-')"
  elif [ -s "$d/stops.log" ]; then result=unclean
  else result=first; fi
  printf '%s\tstart\tprevious-stop=%s%s\n' "$now" "$result" "${since:+ (stopped $since)}" >> "$d/stops.log"
  rm -f "$d/clean-stop"
  fsync_path "$d/stops.log"; fsync_path "$d"
}
marker_stop() {
  local d="$1" now
  mkdir -p "$d"; now="$(date -u +%FT%TZ)"
  printf '%s\tstop\n' "$now" >> "$d/stops.log"; fsync_path "$d/stops.log"
  printf '%s\n' "$now" > "$d/clean-stop.tmp"; fsync_path "$d/clean-stop.tmp"
  mv -f "$d/clean-stop.tmp" "$d/clean-stop"; fsync_path "$d"
}
fsync_path() { sync -- "$1" 2>/dev/null || true; }   # coreutils sync FILE|DIR: fsync just that

# ================================================================ helpers
CHANGED=0; RESTART=0
changed() { CHANGED=$((CHANGED + 1)); printf '  changed  %s\n' "$*"; }
same() { printf '  ok       %s\n' "$*"; }
warn() { printf '  warning  %s\n' "$*"; }
die() { printf 'mu-wsl2-distro: %s\n' "$*" >&2; exit 1; }
step() { printf '\n== %s\n' "$*"; }
systemd_running() { [ "$(cat /proc/1/comm 2>/dev/null)" = systemd ]; }
is_wsl() { [ -n "${WSL_DISTRO_NAME:-}" ] || [ -e /proc/sys/fs/binfmt_misc/WSLInterop ] || grep -qi microsoft /proc/version 2>/dev/null; }

# put_file PATH MODE < content: writes PATH atomically (fsynced) if its content differs; returns 0 if it wrote
put_file() {
  local path="$1" mode="$2" tmp
  mkdir -p "$(dirname "$path")"
  tmp="$(mktemp "$path.XXXXXX")"
  cat > "$tmp"
  if [ -f "$path" ] && cmp -s "$tmp" "$path"; then rm -f "$tmp"; return 1; fi
  chmod "$mode" "$tmp"; fsync_path "$tmp"; mv -f "$tmp" "$path"; fsync_path "$(dirname "$path")"
  return 0
}

user_home() { getent passwd "$1" | cut -d: -f6; }
as_user() {   # as_user CMD...: as MU_USER, with its HOME (git reads it; ssh uses the passwd entry)
  runuser -u "$MU_USER" -- env HOME="$(user_home "$MU_USER")" USER="$MU_USER" LOGNAME="$MU_USER" "$@"
}
GIT_SSH_ENV="GIT_SSH_COMMAND=ssh -o BatchMode=yes -o ConnectTimeout=15"
git_ssh() { as_user env "$GIT_SSH_ENV" GIT_TERMINAL_PROMPT=0 "$@"; }

# repo_access: "ok", "denied: <reason>" or "unreachable: <reason>"
repo_access() {
  local err; err="$(mktemp)"
  if timeout 60 runuser -u "$MU_USER" -- env HOME="$(user_home "$MU_USER")" "$GIT_SSH_ENV" GIT_TERMINAL_PROMPT=0 \
       git ls-remote --heads "$REPO" > /dev/null 2> "$err"; then rm -f "$err"; echo ok; return 0; fi
  git_error_class < "$err"; rm -f "$err"
  return 0
}

print_deploy_key_help() {
  local pub="$1" slug title
  slug="$(repo_slug "$REPO")"; title="${WSL_DISTRO_NAME:-mu}@$(hostname)"
  printf '\n  The key of %s has to be added on GitHub as a read-only deploy key (once, by a repo admin):\n' "$MU_USER"
  if [ -n "$slug" ]; then printf '    https://github.com/%s/settings/keys/new\n' "$slug"; fi
  printf '    Title: %s    Key: the line below    leave "Allow write access" unticked\n\n' "$title"
  printf '    %s\n\n' "$(cat "$pub")"
  if [ -n "$slug" ]; then
    printf '  Or, from a machine with the GitHub CLI and admin rights on the repo, with the line saved as key.pub:\n'
    printf '    gh repo deploy-key add key.pub --repo %s --title "%s"\n' "$slug" "$title"
  fi
  printf '  The organisation must allow deploy keys (organisation settings > Member privileges).\n'
  printf '  Then run the install again: it checks the key and clones.\n'
}

# ================================================================ install steps
ensure_packages() {
  local miss="" p
  for p in git openssh-client curl ca-certificates; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || miss="$miss $p"
  done
  if [ -z "$miss" ]; then same "packages: git, openssh-client, curl"; return 0; fi
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  # shellcheck disable=SC2086
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $miss
  changed "packages installed:$miss"
}

ensure_user() {
  local g
  if id -u "$MU_USER" > /dev/null 2>&1; then same "user $MU_USER exists (uid $(id -u "$MU_USER"))"
  else useradd --create-home --shell /bin/bash --user-group "$MU_USER"; changed "user $MU_USER created (uid $(id -u "$MU_USER"), no sudo)"; fi
  for g in sudo admin wheel; do
    if id -nG "$MU_USER" | tr ' ' '\n' | grep -qx "$g"; then gpasswd -d "$MU_USER" "$g" > /dev/null; changed "$MU_USER removed from group $g (it runs without sudo)"; fi
  done
  if grep -rqsE "^[[:space:]]*$MU_USER[[:space:]]" /etc/sudoers /etc/sudoers.d 2>/dev/null; then
    warn "a sudoers rule names $MU_USER (/etc/sudoers or /etc/sudoers.d): remove it by hand; $MU_USER should not have sudo"
  fi
}

ensure_wsl_conf() {
  local f=/etc/wsl.conf tmp
  mkdir -p "$STATE_DIR"
  tmp="$(mktemp)"
  if [ -f "$f" ]; then cat "$f"; fi \
    | ini_set boot systemd true | ini_set user default "$MU_USER" | ini_set interop appendWindowsPath false > "$tmp"
  if [ -f "$f" ] && cmp -s "$tmp" "$f"; then rm -f "$tmp"; same "/etc/wsl.conf: systemd, default user $MU_USER, no Windows PATH"; return 0; fi
  if [ ! -e "$STATE_DIR/wsl.conf.orig" ] && [ ! -e "$STATE_DIR/wsl.conf.absent" ]; then
    if [ -f "$f" ]; then cp -p "$f" "$STATE_DIR/wsl.conf.orig"; else : > "$STATE_DIR/wsl.conf.absent"; fi
  fi
  put_file "$f" 0644 < "$tmp" || true; rm -f "$tmp"
  RESTART=1; changed "/etc/wsl.conf: [boot] systemd=true, [user] default=$MU_USER, [interop] appendWindowsPath=false (applies at the next start of the distro)"
}

WRITEBACK_CONF=/etc/sysctl.d/60-mu-writeback.conf
JOURNALD_CONF=/etc/systemd/journald.conf.d/60-mu.conf
STOPTIMEOUT_CONF=/etc/systemd/system.conf.d/60-mu-stop.conf
USER_STOPTIMEOUT_CONF=/etc/systemd/user.conf.d/60-mu-stop.conf
USER_AT_STOPTIMEOUT_CONF=/etc/systemd/system/user@.service.d/60-mu-stop.conf
CLEANSTOP_UNIT=/etc/systemd/system/mu-clean-stop.service
HEARTBEAT_UNIT=/etc/systemd/system/mu-heartbeat.service

ensure_writeback() {
  if put_file "$WRITEBACK_CONF" 0644 <<'EOF'; then changed "$WRITEBACK_CONF: dirty pages written back within ~6 s"; else same "$WRITEBACK_CONF"; fi
# mu (mu-wsl2-distro): Windows can stop the WSL VM without a Linux shutdown (power cut, a crash, or a restart without
# the mu-wsl-shutdown hook). Data still in the page cache is then lost, and a file can keep its new size with NUL
# bytes for data. Write dirty pages back after 5 s instead of 30 s, checking every 1 s instead of 5 s: the window
# shrinks from about 35 s to about 6 s. Only fsync closes it. vm.* applies to the whole WSL VM (every distro).
vm.dirty_expire_centisecs = 500
vm.dirty_writeback_centisecs = 100
EOF
  if [ "$(cat /proc/sys/vm/dirty_expire_centisecs 2>/dev/null)" != 500 ] || [ "$(cat /proc/sys/vm/dirty_writeback_centisecs 2>/dev/null)" != 100 ]; then
    if sysctl -q -p "$WRITEBACK_CONF" > /dev/null 2>&1; then changed "write-back timers applied now (500 / 100)"
    else warn "could not apply $WRITEBACK_CONF now; it applies at the next start"; fi
  fi
}

ensure_journald() {
  if put_file "$JOURNALD_CONF" 0644 <<'EOF'; then
# mu (mu-wsl2-distro): sync the journal every 30 s (default 5 min), so that a hard stop loses at most the last 30 s
[Journal]
SyncIntervalSec=30s
EOF
    changed "$JOURNALD_CONF: journal synced every 30 s"
    if systemd_running; then systemctl restart systemd-journald 2> /dev/null || RESTART=1; else RESTART=1; fi
  else same "$JOURNALD_CONF"; fi
}

ensure_stop_timeout() {
  if put_file "$STOPTIMEOUT_CONF" 0644 <<'EOF'; then
# mu (mu-wsl2-distro): when WSL stops the distro it runs `systemctl poweroff` and waits [boot] initTimeout (10 s by
# default, /etc/wsl.conf) before killing what is left. Units get 8 s to stop instead of 90 s, so that poweroff can
# finish inside that window. The data is synced either way; this keeps the unit stops graceful.
[Manager]
DefaultTimeoutStopSec=8s
EOF
    changed "$STOPTIMEOUT_CONF: units get 8 s to stop (applies at the next start)"; RESTART=1
  else same "$STOPTIMEOUT_CONF"; fi
  # the system setting does not reach the user managers (linger keeps $MU_USER's running): user@.service sets its own
  # 120 s, and units inside a user manager use /etc/systemd/user.conf (90 s)
  if put_file "$USER_STOPTIMEOUT_CONF" 0644 <<'EOF'; then
# mu (mu-wsl2-distro): user services (mu's, kept running by linger) get 8 s to stop instead of 90 s, like system units
[Manager]
DefaultTimeoutStopSec=8s
EOF
    changed "$USER_STOPTIMEOUT_CONF: user services get 8 s to stop (applies at the next start)"; RESTART=1
  else same "$USER_STOPTIMEOUT_CONF"; fi
  if put_file "$USER_AT_STOPTIMEOUT_CONF" 0644 <<'EOF'; then
# mu (mu-wsl2-distro): a user's service manager gets 9 s to stop instead of 120 s (its units get 8 s, user.conf.d)
[Service]
TimeoutStopSec=9s
EOF
    changed "$USER_AT_STOPTIMEOUT_CONF: a user's service manager gets 9 s to stop"
    if systemd_running; then systemctl daemon-reload; else RESTART=1; fi
  else same "$USER_AT_STOPTIMEOUT_CONF"; fi
}

# the script itself, where the units find it
ensure_self() {
  local me="$SELF_SRC"
  [ "$me" = "$SELF_PATH" ] && { same "$SELF_PATH"; return 0; }
  [ -r "$me" ] || die "cannot read this script at $me to install it as $SELF_PATH"
  if put_file "$SELF_PATH" 0755 < "$me"; then changed "$SELF_PATH installed"; else same "$SELF_PATH"; fi
}

ensure_clean_stop_unit() {
  if put_file "$CLEANSTOP_UNIT" 0644 <<EOF; then
[Unit]
Description=mu: record whether the previous stop of this distro was clean
Documentation=file://$SELF_PATH
After=local-fs.target
RequiresMountsFor=$STATE_DIR

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$SELF_PATH stop-marker boot
ExecStop=$SELF_PATH stop-marker stop
TimeoutStopSec=5

[Install]
WantedBy=multi-user.target
EOF
    changed "mu-clean-stop.service installed"
    if systemd_running; then systemctl daemon-reload; fi
  else same "mu-clean-stop.service"; fi
  if ! systemctl is-enabled --quiet mu-clean-stop.service 2> /dev/null; then
    systemctl enable --quiet mu-clean-stop.service; changed "mu-clean-stop.service enabled"
  fi
  if systemd_running && ! systemctl is-active --quiet mu-clean-stop.service; then
    systemctl start mu-clean-stop.service; changed "mu-clean-stop.service started"
  fi
}

ensure_linger() {
  if [ -e "/var/lib/systemd/linger/$MU_USER" ]; then same "linger: $MU_USER's user services run without a login"; return 0; fi
  if systemd_running && loginctl enable-linger "$MU_USER" 2> /dev/null; then :
  else mkdir -p /var/lib/systemd/linger; : > "/var/lib/systemd/linger/$MU_USER"; fi   # what enable-linger does
  changed "linger: $MU_USER's user services run without a login (μ auto-update's timer)"
}

# github_host_key: GitHub's ed25519 host key from api.github.com/meta (over TLS), cross-checked with ssh-keyscan when
# port 22 answers. Prints "KEY<tab>checked|"; returns 1 if the API is unreachable, 3 if the two keys differ
github_host_key() {
  local meta key scan
  meta="$(curl -fsSL --max-time 30 https://api.github.com/meta 2> /dev/null)" || return 1
  key="$(printf '%s' "$meta" | gh_meta_ed25519)"
  [ -n "$key" ] || return 1
  scan="$(timeout 20 ssh-keyscan -t ed25519 github.com 2> /dev/null | awk '$2 == "ssh-ed25519" { print $2 " " $3; exit }' || true)"
  if [ -n "$scan" ] && [ "$scan" != "$key" ]; then
    printf 'api.github.com/meta says %s, github.com:22 presents %s\n' "$key" "$scan" >&2; return 3
  fi
  printf '%s\t%s\n' "$key" "${scan:+checked}"
}

ensure_ssh() {
  local home grp ssh key cfg kh tmp hk line checked n rc
  home="$(user_home "$MU_USER")"; grp="$(id -gn "$MU_USER")"; ssh="$home/.ssh"; key="$ssh/mu_github"
  install -d -m 700 -o "$MU_USER" -g "$grp" "$ssh"
  if [ -f "$key" ] && [ -f "$key.pub" ]; then same "ssh key $key"
  elif [ -f "$key" ] && ssh-keygen -y -P '' -f "$key" > "$key.pub.tmp" 2> /dev/null; then
    # only the public half is missing: rebuild it (a new key would no longer match the deploy key on GitHub)
    chown "$MU_USER:$grp" "$key.pub.tmp"; chmod 644 "$key.pub.tmp"; mv -f "$key.pub.tmp" "$key.pub"
    changed "ssh key $key: its public half $key.pub rebuilt from it"
  else
    rm -f "$key.pub.tmp"
    [ ! -f "$key" ] || die "$key exists but $key.pub is missing and could not be rebuilt from it (a passphrase?): fix it, or delete $key for a new key"
    rm -f "$key.pub"
    as_user ssh-keygen -q -t ed25519 -N '' -C "${WSL_DISTRO_NAME:-mu}@$(hostname)" -f "$key"
    changed "ssh key $key generated (ed25519)"; NEW_KEY=1
  fi
  cfg="$ssh/config"; tmp="$(mktemp)"
  if [ -f "$cfg" ]; then cat "$cfg"; fi | ssh_config_merge "~/.ssh/mu_github" > "$tmp"
  if put_file "$cfg" 0600 < "$tmp"; then chown "$MU_USER:$grp" "$cfg"; changed "$cfg: github.com uses mu_github"; else same "$cfg"; fi
  rm -f "$tmp"
  kh="$ssh/known_hosts"; rc=0
  hk="$(github_host_key)" || rc=$?
  if [ "$rc" = 3 ]; then
    die "GitHub's host key differs between api.github.com and github.com:22 (above): not pinning it (a proxy or an attack?)"
  elif [ "$rc" = 0 ]; then
    line="github.com ${hk%%$'\t'*}"; checked="${hk#*$'\t'}"
    n="$( (ssh-keygen -F github.com -f "$kh" 2> /dev/null || true) | grep -vc '^#' || true)"
    if [ -f "$kh" ] && grep -qxF "$line" "$kh" && [ "$n" = 1 ]; then same "known_hosts: GitHub's ed25519 key pinned"
    else
      [ ! -f "$kh" ] || { ssh-keygen -R github.com -f "$kh" > /dev/null 2>&1 || true; rm -f "$kh.old"; }
      printf '%s\n' "$line" >> "$kh"; chown "$MU_USER:$grp" "$kh"; chmod 644 "$kh"
      changed "known_hosts: GitHub's ed25519 key pinned (from api.github.com/meta${checked:+, matches ssh-keyscan})"
    fi
  elif [ -f "$kh" ] && grep -q '^github.com ssh-ed25519 ' "$kh"; then
    warn "api.github.com is not reachable: kept the pinned key in $kh"
  else
    warn "api.github.com is not reachable: GitHub's host key is not pinned yet (run the install again when online)"
  fi
}

ensure_clone() {
  local home dir access
  home="$(user_home "$MU_USER")"; dir="$home/mu"
  access="$(repo_access)"
  ACCESS="$access"
  case "$access" in
    ok) same "GitHub: $MU_USER can read $REPO" ;;
    denied*) warn "GitHub: $MU_USER's key is not accepted yet (${access#denied: })"; print_deploy_key_help "$home/.ssh/mu_github.pub"; return 0 ;;
    *) warn "GitHub: could not check access (${access#unreachable: })"; [ "${NEW_KEY:-0}" = 1 ] && print_deploy_key_help "$home/.ssh/mu_github.pub"; return 0 ;;
  esac
  [ "$CLONE" = 1 ] || return 0
  if [ -d "$dir/.git" ]; then same "clone $dir (left as it is: μ updates itself)"; return 0; fi
  [ ! -e "$dir" ] || { warn "$dir exists and is not a git clone: not cloning"; return 0; }
  if [ -n "$BRANCH" ]; then git_ssh git clone --quiet --branch "$BRANCH" "$REPO" "$dir"; else git_ssh git clone --quiet "$REPO" "$dir"; fi
  changed "cloned $REPO${BRANCH:+ ($BRANCH)} into $dir"
}

install_mu() {
  local home dir uid wslenv=()
  home="$(user_home "$MU_USER")"; dir="$home/mu"; uid="$(id -u "$MU_USER")"
  # disk checks find the Windows drive through WSL interop (install/disk.sh): keep the distro name and interop socket
  [ -z "${WSL_DISTRO_NAME:-}" ] || wslenv+=("WSL_DISTRO_NAME=$WSL_DISTRO_NAME")
  [ -z "${WSL_INTEROP:-}" ] || wslenv+=("WSL_INTEROP=$WSL_INTEROP")
  [ -x "$dir/install.sh" ] || { warn "--install-mu: no clone at $dir yet (add the deploy key first)"; return 0; }
  if [ -e "$home/.local/bin/mu" ]; then
    same "μ is installed for $MU_USER (it updates itself; to install again by hand: wsl -d ${WSL_DISTRO_NAME:-<distro>} -u $MU_USER -- bash -lc 'cd ~/mu && ./install.sh')"
    return 0
  fi
  step "μ system packages (install.sh --system, as root)"
  if installsh_supports_user "$dir/install/install.sh"; then "$dir/install.sh" --system --user "$MU_USER"
  else
    echo "  (this μ version's install.sh has no --user: linger was set above instead)"
    "$dir/install.sh" --system
  fi
  step "μ (./install.sh as $MU_USER; the first install downloads several GB)"
  # the user's systemd manager (linger) has to be reachable for install.sh to turn on auto-update
  if systemd_running; then systemctl start "user@$uid.service" 2> /dev/null || true; fi
  runuser -u "$MU_USER" -- env -i HOME="$home" USER="$MU_USER" LOGNAME="$MU_USER" SHELL=/bin/bash LANG=C.UTF-8 \
    PATH="$home/.local/bin:/usr/local/bin:/usr/bin:/bin" TERM="${TERM:-xterm}" \
    XDG_RUNTIME_DIR="/run/user/$uid" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" ${wslenv[@]+"${wslenv[@]}"} \
    bash -c 'cd "$HOME/mu" && ./install.sh'
  changed "μ installed for $MU_USER"
}

heartbeat_on() {
  local home; home="$(user_home "$MU_USER")"
  if put_file "$HEARTBEAT_UNIT" 0644 <<EOF; then
# mu test aid (mu-wsl2-distro --heartbeat on|off): a stand-in for the relay agent while testing restarts. It writes
# without fsync on purpose, like most tools: \`mu-wsl2-distro status\` reports gaps and NUL bytes in the log.
[Unit]
Description=mu test aid: append the UTC time to $home/heartbeat.log every 60 s
After=local-fs.target

[Service]
User=$MU_USER
ExecStart=/bin/sh -c 'while :; do date -u +%%FT%%TZ >> $home/heartbeat.log; sleep 60; done'
Restart=always
TimeoutStopSec=5

[Install]
WantedBy=multi-user.target
EOF
    changed "mu-heartbeat.service installed"
    if systemd_running; then systemctl daemon-reload; fi
  else same "mu-heartbeat.service"; fi
  if ! systemctl is-enabled --quiet mu-heartbeat.service 2> /dev/null; then systemctl enable --quiet mu-heartbeat.service; changed "mu-heartbeat.service enabled"; fi
  if systemd_running && ! systemctl is-active --quiet mu-heartbeat.service; then systemctl start mu-heartbeat.service; changed "mu-heartbeat.service started"; fi
}

heartbeat_off() {
  local home; home="$(user_home "$MU_USER" || true)"
  if [ -f "$HEARTBEAT_UNIT" ]; then
    systemctl disable --quiet --now mu-heartbeat.service 2> /dev/null || systemctl disable --quiet mu-heartbeat.service 2> /dev/null || true
    rm -f "$HEARTBEAT_UNIT"; if systemd_running; then systemctl daemon-reload; fi
    changed "mu-heartbeat.service removed${home:+ (its log $home/heartbeat.log is kept)}"
  else same "mu-heartbeat.service is not installed"; fi
}

do_install() {
  [ "$(id -u)" = 0 ] || die "run as root: wsl -d <distro> -u root -- $0 install"
  is_wsl || warn "this does not look like WSL: continuing, but the settings are meant for a WSL2 distro"
  printf 'mu-wsl2-distro install: user %s%s\n' "$MU_USER" "${WSL_DISTRO_NAME:+, distro $WSL_DISTRO_NAME}"
  step "System"
  ensure_packages; ensure_user; ensure_wsl_conf; ensure_self
  step "Clean stops and write-back"
  ensure_writeback; ensure_journald; ensure_stop_timeout; ensure_clean_stop_unit
  step "User services"
  ensure_linger
  step "GitHub access"
  ensure_ssh; ACCESS=unknown; ensure_clone
  if [ "$INSTALL_MU" = 1 ]; then
    if [ "$ACCESS" = ok ]; then install_mu; else warn "--install-mu: skipped until $MU_USER can read the repo"; fi
  fi
  case "$HEARTBEAT" in
    on) step "Heartbeat test aid"; heartbeat_on ;;
    off) step "Heartbeat test aid"; heartbeat_off ;;
  esac
  mkdir -p /run/mu-wsl2
  printf 'changed=%s\nrestart=%s\naccess=%s\n' "$CHANGED" "$RESTART" "${ACCESS%%:*}" > /run/mu-wsl2/install-result
  echo
  if [ "$CHANGED" = 0 ]; then echo "mu-wsl2-distro: nothing to change, already set up."
  else echo "mu-wsl2-distro: $CHANGED change(s)."; fi
  if [ "$RESTART" = 1 ]; then echo "Restart the distro to apply them all (from Windows: wsl --terminate ${WSL_DISTRO_NAME:-<distro>})."; fi
}

do_uninstall() {
  [ "$(id -u)" = 0 ] || die "run as root"
  echo "mu-wsl2-distro uninstall (keeps user $MU_USER, its files, its ssh key, the clone and μ itself)"
  heartbeat_off
  if [ -f "$CLEANSTOP_UNIT" ]; then
    systemctl disable --quiet mu-clean-stop.service 2> /dev/null || true
    rm -f "$CLEANSTOP_UNIT"; if systemd_running; then systemctl daemon-reload; fi
    changed "mu-clean-stop.service removed (its log $STATE_DIR/stops.log is kept)"
  fi
  local f
  for f in "$WRITEBACK_CONF" "$JOURNALD_CONF" "$STOPTIMEOUT_CONF" "$USER_STOPTIMEOUT_CONF" "$USER_AT_STOPTIMEOUT_CONF"; do
    if [ -f "$f" ]; then rm -f "$f"; changed "$f removed"; fi
  done
  rmdir /etc/systemd/user.conf.d /etc/systemd/system/user@.service.d 2> /dev/null || true
  if systemd_running; then systemctl daemon-reload; fi
  if [ "$(cat /proc/sys/vm/dirty_expire_centisecs 2>/dev/null)" = 500 ]; then
    sysctl -q -w vm.dirty_expire_centisecs=3000 vm.dirty_writeback_centisecs=500 > /dev/null 2>&1 || true
    changed "write-back timers back to the kernel defaults (3000 / 500)"
  fi
  if [ -f "$STATE_DIR/wsl.conf.orig" ]; then
    cp -p "$STATE_DIR/wsl.conf.orig" /etc/wsl.conf; rm -f "$STATE_DIR/wsl.conf.orig"; changed "/etc/wsl.conf restored"
  elif [ -f "$STATE_DIR/wsl.conf.absent" ]; then
    rm -f /etc/wsl.conf "$STATE_DIR/wsl.conf.absent"; changed "/etc/wsl.conf removed (there was none)"
  fi
  echo "Kept: linger for $MU_USER (loginctl disable-linger $MU_USER); $STATE_DIR/stops.log; in ~$MU_USER/.ssh the key, the"
  echo "  managed github.com block in config (between the '# >>> mu-wsl2' and '# <<< mu-wsl2' lines) and GitHub's key in"
  echo "  known_hosts (ssh-keygen -R github.com -f ~$MU_USER/.ssh/known_hosts); $SELF_PATH itself (mu-wsl2-host.ps1"
  echo "  uninstall deletes it next; by hand: rm $SELF_PATH)."
  echo "Restart the distro to apply (from Windows: wsl --terminate ${WSL_DISTRO_NAME:-<distro>})."
}

# ================================================================ status
CHECKS=""
check() { printf '%s\t%s\t%s\n' "$1" "$2" "$(printf '%s' "$3" | tr '\t\n' '  ')" >> "$CHECKS"; }   # RESULT NAME DETAIL

# distro_start_epoch: when this distro's init (PID 1) started, in seconds since 1970 (0 if unknown). Its start time
# in /proc/1/stat is in clock ticks since the VM booted (btime in /proc/stat); the VM can outlive a distro restart.
distro_start_epoch() {
  local bt st hz
  bt="$(awk '$1 == "btime" { print $2 }' /proc/stat 2> /dev/null || true)"
  st="$(sed 's/^.*) //' /proc/1/stat 2> /dev/null | awk '{ print $20 }' || true)"
  hz="$(getconf CLK_TCK 2> /dev/null || echo 100)"
  case "$bt$st$hz" in ""|*[!0-9]*) echo 0 ;; *) echo $((bt + st / hz)) ;; esac
}

# The heartbeat checks, only while the unit is installed. NUL bytes fail only when they come from the stop just
# before this start of the distro (the first heartbeat after them is later than the start). Older ones, such as those
# of an earlier restart test, are listed as info, so that each restart is judged on its own.
status_heartbeat() {
  local home log a lines valid nul first last le now age ngap=0 nnow=0 rec since
  home="$(user_home "$MU_USER")"; log="$home/heartbeat.log"
  if [ ! -f "$HEARTBEAT_UNIT" ]; then
    [ ! -f "$log" ] || check info heartbeat "off (mu-heartbeat.service is not installed; its log $log is kept)"
    return 0
  fi
  if systemd_running && systemctl is-active --quiet mu-heartbeat.service; then check pass heartbeat.service "active"
  else check fail heartbeat.service "installed but not running"; fi
  if [ ! -f "$log" ]; then check fail heartbeat.log "$log does not exist yet"; return 0; fi
  since="$(distro_start_epoch)"
  a="$(hb_analyze "$GAP" "$since" < "$log")"
  lines="$(printf '%s\n' "$a" | awk -F'\t' '$1 == "lines" { print $2 }')"
  valid="$(printf '%s\n' "$a" | awk -F'\t' '$1 == "valid" { print $2 }')"
  nul="$(printf '%s\n' "$a" | awk -F'\t' '$1 == "nul" { print $2 }')"
  first="$(printf '%s\n' "$a" | awk -F'\t' '$1 == "first" { print $2 }')"
  last="$(printf '%s\n' "$a" | awk -F'\t' '$1 == "last" { print $2 }')"
  le="$(printf '%s\n' "$a" | awk -F'\t' '$1 == "last_epoch" { print $2 }')"
  check info heartbeat.log "$valid timestamps in $lines lines, ${first:-none} to ${last:-none}"
  if [ "${nul:-0}" -gt 0 ]; then
    while IFS="$(printf '\t')" read -r rec l1 l2 l3 l4; do
      case "$rec" in
        nulline) nnow=$((nnow + 1)); check fail heartbeat.nul "line $l1: $l2 NUL bytes between $l3 and $l4 (data lost by the stop before this start: it was not clean)" ;;
        nulold) check info heartbeat.nul-old "line $l1: $l2 NUL bytes between $l3 and $l4 (an earlier stop, before this start of the distro)" ;;
      esac
    done <<EOF
$a
EOF
  fi
  [ "$nnow" -gt 0 ] || check pass heartbeat.nul "no NUL bytes from the stop before this start of the distro"
  while IFS="$(printf '\t')" read -r rec l1 l2 l3; do
    case "$rec" in
      gap) ngap=$((ngap + 1)); [ "$ngap" -gt 30 ] || check warn heartbeat.gap "$l1 -> $l2 ($l3 s without a heartbeat; a restart shows up as one gap)" ;;
      back) check warn heartbeat.back "time went backwards: $l1 -> $l2" ;;
      bad) check warn heartbeat.bad "line $l1 is not a timestamp: $l2" ;;
    esac
  done <<EOF
$a
EOF
  [ "$ngap" -le 30 ] || check warn heartbeat.gap "... $((ngap - 30)) more gaps"
  [ "$ngap" -gt 0 ] || check pass heartbeat.gaps "no gap longer than $GAP s"
  if [ -n "$le" ]; then
    now="$(date +%s)"; age=$((now - le))
    if [ "$age" -gt "$GAP" ]; then check fail heartbeat.fresh "last heartbeat $age s ago ($last)"
    else check pass heartbeat.fresh "last heartbeat $age s ago"; fi
  fi
}

do_status() {
  [ "$(id -u)" = 0 ] || die "run as root: wsl -d <distro> -u root -- $0 status"
  CHECKS="$(mktemp)"; trap 'rm -f "$CHECKS"' EXIT
  local v home key kh cfg last init_started=""
  if systemd_running; then check pass systemd "systemd is PID 1"; else check fail systemd "PID 1 is $(cat /proc/1/comm 2>/dev/null) (needs [boot] systemd=true and a restart of the distro)"; fi
  if is_wsl; then check info wsl "distro ${WSL_DISTRO_NAME:-?}, kernel $(uname -r)"; else check warn wsl "not WSL"; fi
  # wsl.conf
  for v in "boot systemd true" "user default $MU_USER" "interop appendWindowsPath false"; do
    set -- $v
    if [ "$(ini_get "$1" "$2" < /etc/wsl.conf 2>/dev/null)" = "$3" ]; then check pass "wsl.conf.$1.$2" "$3"
    else check fail "wsl.conf.$1.$2" "is '$(ini_get "$1" "$2" < /etc/wsl.conf 2>/dev/null)', want '$3'"; fi
  done
  # user
  if id -u "$MU_USER" > /dev/null 2>&1; then
    if id -nG "$MU_USER" | tr ' ' '\n' | grep -qxE 'sudo|admin|wheel'; then check fail user "$MU_USER is in an admin group: $(id -nG "$MU_USER")"
    else check pass user "$MU_USER (uid $(id -u "$MU_USER")), no sudo"; fi
  else check fail user "$MU_USER does not exist"; fi
  [ -e "/var/lib/systemd/linger/$MU_USER" ] && check pass linger "$MU_USER's user services run without a login" || check fail linger "not enabled for $MU_USER"
  # write-back, journald, stop timeout
  [ -f "$WRITEBACK_CONF" ] && check pass writeback.conf "$WRITEBACK_CONF" || check fail writeback.conf "$WRITEBACK_CONF missing"
  v="$(cat /proc/sys/vm/dirty_expire_centisecs 2>/dev/null)/$(cat /proc/sys/vm/dirty_writeback_centisecs 2>/dev/null)"
  [ "$v" = 500/100 ] && check pass writeback.live "dirty_expire/writeback = $v centisecs" || check fail writeback.live "dirty_expire/writeback = $v centisecs, want 500/100"
  [ -f "$JOURNALD_CONF" ] && check pass journald "SyncIntervalSec=30s" || check fail journald "$JOURNALD_CONF missing"
  if [ -f "$STOPTIMEOUT_CONF" ]; then
    if systemd_running; then
      v="$(systemctl show --property=DefaultTimeoutStopUSec --value 2>/dev/null || true)"
      [ "$v" = 8s ] && check pass stop-timeout "DefaultTimeoutStopSec=8s" || check fail stop-timeout "systemd uses $v (restart the distro to apply 8s)"
    else check pass stop-timeout "DefaultTimeoutStopSec=8s (file)"; fi
  else check fail stop-timeout "$STOPTIMEOUT_CONF missing"; fi
  if [ -f "$USER_STOPTIMEOUT_CONF" ] && [ -f "$USER_AT_STOPTIMEOUT_CONF" ]; then check pass stop-timeout.user "user services 8 s, user@.service 9 s"
  else check fail stop-timeout.user "$USER_STOPTIMEOUT_CONF or $USER_AT_STOPTIMEOUT_CONF missing"; fi
  # clean stops
  if [ -f "$CLEANSTOP_UNIT" ] && systemctl is-enabled --quiet mu-clean-stop.service 2>/dev/null; then
    if ! systemd_running || systemctl is-active --quiet mu-clean-stop.service; then check pass clean-stop.unit "enabled"
    else check fail clean-stop.unit "enabled but not active"; fi
  else check fail clean-stop.unit "mu-clean-stop.service not installed"; fi
  if [ -s "$STATE_DIR/stops.log" ]; then
    last="$(grep -F 'previous-stop=' "$STATE_DIR/stops.log" | tail -n 1 || true)"
    case "$last" in
      *previous-stop=clean*) check pass last-stop "${last%%$'\t'*}: started after a clean stop${last##*previous-stop=clean}" ;;
      *previous-stop=unclean*) check warn last-stop "${last%%$'\t'*}: started after an UNCLEAN stop (Windows stopped the VM without a Linux shutdown)" ;;
      *previous-stop=first*) check info last-stop "${last%%$'\t'*}: first start with the marker installed" ;;
    esac
    v="$(grep -c 'previous-stop=unclean' "$STATE_DIR/stops.log" || true)"
    check info stops "$(grep -c 'previous-stop=' "$STATE_DIR/stops.log" || true) starts recorded, ${v:-0} after an unclean stop ($STATE_DIR/stops.log)"
  fi
  # GitHub access
  home="$(user_home "$MU_USER" 2>/dev/null || true)"
  if [ -n "$home" ]; then
    key="$home/.ssh/mu_github"; kh="$home/.ssh/known_hosts"; cfg="$home/.ssh/config"
    [ -f "$key" ] && [ -f "$key.pub" ] && check pass ssh.key "$key.pub: $(cut -d' ' -f1,3 "$key.pub")" || check fail ssh.key "$key missing"
    grep -qxF "$MARK_BEGIN" "$cfg" 2>/dev/null && check pass ssh.config "github.com uses mu_github" || check fail ssh.config "no managed github.com block in $cfg"
    grep -q '^github.com ssh-ed25519 ' "$kh" 2>/dev/null && check pass ssh.known_hosts "GitHub's ed25519 key pinned" || check fail ssh.known_hosts "GitHub's host key not pinned in $kh"
    if [ "$OFFLINE" = 0 ]; then
      v="$(repo_access)"
      case "$v" in ok) check pass github "$MU_USER can read $REPO" ;; *) check fail github "$v" ;; esac
    fi
    if [ -d "$home/mu/.git" ]; then check info clone "$home/mu at $(as_user git -C "$home/mu" describe --tags --always 2>/dev/null || echo '?')"
    else check info clone "no clone at $home/mu"; fi
    if [ -x "$home/.local/bin/mu" ]; then check info mu "installed ($home/.local/bin/mu)"; else check info mu "not installed for $MU_USER"; fi
    status_heartbeat
  fi
  # what the VM got from .wslconfig (the Windows script compares)
  local cpus mem swap
  cpus="$(nproc)"; mem="$(awk '/^MemTotal:/ { printf "%d", $2 / 1024 }' /proc/meminfo)"; swap="$(awk '/^SwapTotal:/ { printf "%d", $2 / 1024 }' /proc/meminfo)"
  check info resources "$cpus CPUs, $mem MiB memory, $swap MiB swap"
  init_started="$(date -u -d "$(ps -o lstart= -p 1 2>/dev/null)" +%FT%TZ 2>/dev/null || true)"
  [ -z "$init_started" ] || check info started "distro started $init_started"

  local nfail; nfail="$(awk -F'\t' '$1 == "fail"' "$CHECKS" | wc -l | tr -d ' ')"
  if [ "$JSON" = 1 ]; then
    printf '{"ok":%s,"distro":%s,"user":%s,"cpus":%s,"mem_mib":%s,"swap_mib":%s,"systemd":%s,"started":%s,"checks":[' \
      "$([ "$nfail" = 0 ] && echo true || echo false)" "$(json_str "${WSL_DISTRO_NAME:-}")" "$(json_str "$MU_USER")" \
      "${cpus:-0}" "${mem:-0}" "${swap:-0}" "$(systemd_running && echo true || echo false)" "$(json_str "$init_started")"
    local sep=""
    while IFS="$(printf '\t')" read -r r n d; do
      printf '%s{"result":%s,"name":%s,"detail":%s}' "$sep" "$(json_str "$r")" "$(json_str "$n")" "$(json_str "$d")"; sep=","
    done < "$CHECKS"
    printf ']}\n'
  else
    while IFS="$(printf '\t')" read -r r n d; do
      printf '  %-5s %-22s %s\n' "$(printf '%s' "$r" | tr 'a-z' 'A-Z')" "$n" "$d"
    done < "$CHECKS"
    if [ "$nfail" = 0 ]; then echo "mu-wsl2-distro status: all checks pass"; else echo "mu-wsl2-distro status: $nfail check(s) failed"; fi
  fi
  [ "$nfail" = 0 ]
}

# ================================================================ main
usage() { sed -n '2,/^set -euo/p' "$SELF_SRC" | sed '$d' | sed 's/^# \{0,1\}//'; }
main() {
  local action="${1:-}"; [ $# -gt 0 ] && shift
  case "$action" in
    install|status|uninstall) ;;
    heartbeat) HEARTBEAT="${1:-}"; [ $# -gt 0 ] && shift ;;
    stop-marker)
      case "${1:-}" in boot) marker_boot "$STATE_DIR" ;; stop) marker_stop "$STATE_DIR" ;; *) usage >&2; exit 2 ;; esac
      exit 0 ;;
    -h|--help|help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  while [ $# -gt 0 ]; do
    case "$1" in
      --user) MU_USER="${2:-}"; shift 2 ;;
      --repo) REPO="${2:-}"; shift 2 ;;
      --branch) BRANCH="${2:-}"; shift 2 ;;
      --clone) CLONE=1; shift ;;
      --install-mu) INSTALL_MU=1; CLONE=1; shift ;;
      --heartbeat) HEARTBEAT="${2:-}"; shift 2 ;;
      --json) JSON=1; shift ;;
      --offline) OFFLINE=1; shift ;;
      --gap) GAP="${2:-}"; shift 2 ;;
      *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
  done
  case "$MU_USER" in ""|root|-*|*[!a-z0-9_-]*) echo "--user: invalid user name '$MU_USER'" >&2; exit 2 ;; esac
  case "$HEARTBEAT" in ""|on|off) ;; *) echo "--heartbeat takes on or off" >&2; exit 2 ;; esac
  case "$GAP" in ""|*[!0-9]*) echo "--gap takes a number of seconds" >&2; exit 2 ;; esac
  cd /   # runuser'd commands (git) must be able to read the working directory
  case "$action" in
    install) do_install ;;
    status) do_status ;;
    uninstall) do_uninstall ;;
    heartbeat)
      [ "$(id -u)" = 0 ] || die "run as root"
      case "$HEARTBEAT" in on) heartbeat_on ;; off) heartbeat_off ;; *) echo "heartbeat on|off" >&2; exit 2 ;; esac ;;
  esac
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then main "$@"; fi
