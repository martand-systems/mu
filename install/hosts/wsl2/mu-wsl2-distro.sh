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
# The job layer's node (docs/design/job-layer.md §13; each takes --json and --user):
#   mu-wsl2-distro authorize NAME 'KEY'   let workstation NAME log in with KEY, the "ssh-ed25519 AAAA… NAME" line that
#                                         `mu compute add` prints ("-": the key on stdin). Exit 2 refused, 3 the key is
#                                         already authorised under another name
#   mu-wsl2-distro revoke NAME            remove NAME's key
#   mu-wsl2-distro keys                   the authorised workstations and their key fingerprints
#   mu-wsl2-distro host-key               the node's public host key and its SHA256 fingerprint, for `mu compute add`
#   mu-wsl2-distro tunnel credential      read the relay settings from STDIN (the lines of `relay/gen-credentials.sh
#                                         client <node>`; never an argument, not echoed), write /etc/mu/tunnel.env,
#                                         start the tunnel, wait --wait SECONDS (30) for it. Exit 0 connected, 1 not, 2
#                                         input refused. (tunnel-credential is the same)
#   mu-wsl2-distro tunnel status          is the tunnel connected; exit 0 yes, 1 no (with the reason), 3 not set up
#   mu-wsl2-distro tunnel refresh         the relay's addresses again (DNS): the tunnel may connect to them and nowhere
#                                         else (its egress filter); mu-tunnel-refresh.timer runs it hourly
#   mu-wsl2-distro windows-reach          can a process of the distro start Windows programs or open the Windows drive
#                                         (changes nothing); exit 0 closed, 1 open
# Pairing by one code (docs/design/pairing.md §9.3; the code on stdin, never an argument; asked without echo at a
# terminal). They run /usr/local/lib/mu/mu-pair.py (a root-owned copy of src/mu/pair.py that install puts there):
#   mu-wsl2-distro pair claim             claim the invite with the code and check its offer (mu-wsl2-host.ps1 pair)
#   mu-wsl2-distro pair finish            the relay credential, the inviter's key, the answer, the Mac's confirm
#   mu-wsl2-distro pair run               claim and finish in one (a root shell; CI)
#   mu-wsl2-distro pair abort             undo a pairing that was not answered yet
#   mu-wsl2-distro pair status            is a pairing half done
#   --relay URL                           the relay (default https://relay.martand.systems)
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
#   --host-drive LETTER  install: the Windows drive that holds the distro's disk (mu-wsl2-host.ps1 passes it); recorded
#                        in /var/lib/mu-wsl2/host-drive for μ's disk check, since interop goes off
#   --node-name NAME     install: the node's name for its runner (default: the relay user of the tunnel credential)
#   --no-wsl             not a WSL distro (the CI runner): leave out the Windows steps and checks (interop, drives)
#   --wait SECONDS       tunnel credential: how long to wait for the tunnel to connect (default 30)
#   --from-host          install: run by mu-wsl2-host.ps1, which says how to restart (this script then does not)
#   --relay URL          pair: the relay (default https://relay.martand.systems)
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
#   the job layer        (docs/design/job-layer.md §13) Ubuntu's ssh.socket and ssh.service masked BEFORE openssh-server
#                        is installed; the node's host key /etc/mu/ssh_host_ed25519_key (kept by uninstall: it is the
#                        node's identity); /etc/mu/sshd_config (checked with sshd -t before it is used; status compares
#                        the values sshd -T reports) and mu-sshd.service on 127.0.0.1:2222; /etc/mu/authorized_keys/NAME;
#                        the gate /usr/local/lib/mu/mu-node-gate; /srv/mu (NAME, 0700); cgroup delegation only if the
#                        user manager lacks memory or pids; the chisel client (SHA-256 pinned), the system user mu-tunnel
#                        and the sandboxed mu-tunnel.service (it starts once `tunnel credential` gave it the relay
#                        settings) whose egress filter lets it reach only loopback and the relay's addresses, kept
#                        current by mu-tunnel-refresh.timer; unattended security updates; [interop] enabled=false and [automount] options
#                        uid=0,gid=0,umask=077 in wsl.conf, and the host drive record; mu node enable as NAME
# Exit codes: 0 done (install: see /run/mu-wsl2/install-result for changed=N restart=0|1), 1 failed, 2 usage.
# Test hooks (tools/test-wsl2-host/run.sh sources this file): nothing runs when it is sourced.
set -euo pipefail

MU_USER="mu"
REPO="git@github.com:martand-systems/mu.git"
BRANCH=""; CLONE=0; INSTALL_MU=0; HEARTBEAT=""; JSON=0; OFFLINE=0; GAP=90
NO_WSL=0; HOST_DRIVE=""; NODE_NAME=""; WAIT=30; ARG1=""; ARG2=""; FROM_HOST=0; RELAY_URL_ARG="https://relay.martand.systems"
# [automount] options: Windows drives mounted for root only (§13.4); mu can still ask their free space (statfs)
AUTOMOUNT_OPTIONS='"uid=0,gid=0,umask=077"'
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

# wsl_conf_merge USER NO_WSL < wsl.conf > wsl.conf': what install sets in /etc/wsl.conf. With NO_WSL=0 also the job
# layer's Windows reach (§13.4): [interop] enabled=false (no Linux process starts a Windows program) and [automount]
# enabled=true with options uid=0,gid=0,umask=077 (the drives stay mounted, for root only)
wsl_conf_merge() {
  if [ "${2:-0}" = 1 ]; then ini_set boot systemd true | ini_set user default "$1" | ini_set interop appendWindowsPath false
  else
    ini_set boot systemd true | ini_set user default "$1" | ini_set interop appendWindowsPath false \
      | ini_set interop enabled false | ini_set automount enabled true | ini_set automount options "$AUTOMOUNT_OPTIONS"
  fi
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

# ---------------------------------------------------------------- job layer (docs/design/job-layer.md §13)
# fault hooks for tools/test-wsl2-host and the CI job (each must make its test fail); read only from root's environment
host_fault() { case " ${MU_HOST_FAULT:-} " in *" $1 "*) return 0 ;; esac; return 1; }

# sshd_config_text PORT USER HOSTKEY KEYFILE_PATTERN GATE: the whole configuration of mu-sshd.service (§13.1)
sshd_config_text() {
  cat <<EOF
# mu node sshd (mu-sshd.service), written by mu-wsl2-distro; docs/design/job-layer.md §13.1. This is its whole
# configuration: /etc/ssh/sshd_config and its drop-ins are not read. Loopback only, key only, one user, the gate forced.
Port $1
AddressFamily inet
ListenAddress 127.0.0.1
HostKey $3
PidFile /run/mu-sshd.pid
UsePAM yes
AllowUsers $2
PermitRootLogin no
AuthenticationMethods publickey
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
HostbasedAuthentication no
PermitEmptyPasswords no
StrictModes yes
AuthorizedKeysFile $4
ExposeAuthInfo yes
ForceCommand $5
PermitTTY no
AllowTcpForwarding no
AllowStreamLocalForwarding no
AllowAgentForwarding no
X11Forwarding no
PermitTunnel no
GatewayPorts no
PermitUserRC no
PermitUserEnvironment no
PrintMotd no
PrintLastLog no
MaxAuthTries 3
MaxSessions 10
MaxStartups 10:30:60
LoginGraceTime 20
ClientAliveInterval 30
ClientAliveCountMax 3
LogLevel VERBOSE
EOF
}

# sshd_effective_check CONFIG < `sshd -T -f CONFIG -C user=…` output: every setting written in CONFIG must be what sshd
# says it uses (ListenAddress gains the port, as sshd -T prints it), and there must be no Subsystem (sftp) and no
# AcceptEnv. Prints "<key><TAB>want<TAB>got" for each difference; returns 1 if there is any
sshd_effective_check() {
  host_fault sshd-no-effective-check && { cat > /dev/null; return 0; }
  LC_ALL=C awk -v cfg="$1" '
    BEGIN {
      while ((getline l < cfg) > 0) {
        sub(/\r$/, "", l); if (l ~ /^[ \t]*(#|$)/) continue
        n = split(l, a, /[ \t]+/); k = tolower(a[1]); v = a[2]; for (i = 3; i <= n; i++) v = v " " a[i]
        if (!(k in want)) order[++no] = k
        want[k] = v
      }
      if (("listenaddress" in want) && want["listenaddress"] !~ /:/) want["listenaddress"] = want["listenaddress"] ":" want["port"]
    }
    { k = $1; v = substr($0, length($1) + 2); if (k in got) got[k] = got[k] "," v; else got[k] = v }
    END {
      bad = 0
      for (i = 1; i <= no; i++) {
        k = order[i]
        if (!(k in got)) { print k "\t" want[k] "\t(not reported)"; bad = 1 }
        else if (got[k] != want[k]) { print k "\t" want[k] "\t" got[k]; bad = 1 }
      }
      if ("subsystem" in got) { print "subsystem\t(none)\t" got["subsystem"]; bad = 1 }
      if ("acceptenv" in got) { print "acceptenv\t(none)\t" got["acceptenv"]; bad = 1 }
      exit bad
    }'
}

# key_name_ok NAME: a workstation's name in the key file: [a-z0-9-]{1,32}, not starting with "-" (what the gate reads)
key_name_ok() {
  host_fault no-name-check && return 0
  case "$1" in ""|-*|*[!abcdefghijklmnopqrstuvwxyz0123456789-]*) return 1 ;; esac   # letters spelled out: [a-z] matches A-Z in some locales
  [ "${#1}" -le 32 ]
}

# pubkey_check TEXT: TEXT must be one ed25519 public key, "ssh-ed25519 AAAA… [comment]" (the line `mu compute add`
# prints). Prints "ssh-ed25519 <base64>"; otherwise a reason on stderr and return 1. ssh-keygen -l has the last word
pubkey_check() {
  local t n typ b64 tmp fp
  t="$(printf '%s' "$1" | tr -d '\r')"
  n="$(printf '%s\n' "$t" | grep -c '[^[:space:]]' || true)"
  [ "$n" = 1 ] || { echo "expected one public key line, got ${n:-0} lines" >&2; return 1; }
  t="$(printf '%s\n' "$t" | grep '[^[:space:]]')"
  typ="$(printf '%s\n' "$t" | awk '{ print $1 }')"; b64="$(printf '%s\n' "$t" | awk '{ print $2 }')"
  [ "$typ" = ssh-ed25519 ] || { echo "not an ed25519 public key (it starts with '$(printf '%s' "$typ" | cut -c1-24)'): mu compute add prints one" >&2; return 1; }
  case "$b64" in ""|*[!A-Za-z0-9+/=]*) echo "the key's second field is not base64" >&2; return 1 ;; esac
  if ! host_fault no-keygen-check; then
    tmp="$(mktemp)"; printf '%s %s\n' "$typ" "$b64" > "$tmp"
    fp="$(ssh-keygen -l -f "$tmp" 2> /dev/null)" || { rm -f "$tmp"; echo "ssh-keygen does not read it as a public key (damaged or cut short?)" >&2; return 1; }
    rm -f "$tmp"
    case "$fp" in *"(ED25519)") ;; *) echo "ssh-keygen reads another key type: $fp" >&2; return 1 ;; esac
  fi
  printf '%s %s\n' "$typ" "$b64"
}

# authkeys_put NAME "ssh-ed25519 B64" < key file > new key file: NAME's line becomes "restrict ssh-ed25519 B64 NAME",
# where it was, else at the end; every other line is kept. If the same key is authorised under another name: that name
# on stderr and return 3 (the gate gives a key the first name it finds)
authkeys_put() {
  LC_ALL=C awk -v name="$1" -v key="$2" '
    function fields(s,   n, i) {   # sets kt, kb, nm from a key line (options may come first)
      kt = kb = nm = ""; n = split(s, f, /[ \t]+/)
      for (i = 1; i < n; i++) if (f[i] ~ /^(ssh-|ecdsa-|sk-)/) { kt = f[i]; kb = f[i + 1]; nm = f[i + 2]; return }
    }
    BEGIN { split(key, k, " "); line = "restrict " key " " name; done = 0; clash = "" }
    {
      fields($0)
      if (nm == name) { if (!done) print line; done = 1; next }
      if (kt == k[1] && kb == k[2]) clash = nm
      print
    }
    END { if (!done) print line; if (clash != "") { print clash > "/dev/stderr"; exit 3 } }'
}

# authkeys_del NAME < key file > new key file: without NAME's line(s); returns 1 if NAME had none
authkeys_del() {
  LC_ALL=C awk -v name="$1" '
    { n = split($0, f, /[ \t]+/); nm = ""; for (i = 1; i < n; i++) if (f[i] ~ /^(ssh-|ecdsa-|sk-)/) { nm = f[i + 2]; break } }
    nm == name { found = 1; next }
    { print }
    END { exit !found }'
}

# authkeys_list < key file: "NAME<TAB>TYPE BASE64" per key line
authkeys_list() {
  LC_ALL=C awk '{ n = split($0, f, /[ \t]+/); for (i = 1; i < n; i++) if (f[i] ~ /^(ssh-|ecdsa-|sk-)/) { print f[i + 2] "\t" f[i] " " f[i + 1]; break } }'
}

# tunnel_env_from < the output of `relay/gen-credentials.sh client <node>` (RELAY_URL, RELAY_FINGERPRINT, RELAY_ROLE,
# RELAY_USER, RELAY_PASSWORD, RELAY_PORTS), or AUTH=user:password, RELAY_URL, RELAY_FINGERPRINT, RELAY_PORT lines:
# prints the four lines of /etc/mu/tunnel.env. Blank lines, comments, CR and `export ` are ignored. A missing or
# malformed value: a message naming the field (never its value) on stderr, return 2
tunnel_env_from() {
  local l k v user="" pw="" auth="" url="" fp="" port="" role="" cr
  cr="$(printf '\r')"
  while IFS= read -r l || [ -n "$l" ]; do
    l="${l%"$cr"}"; l="${l#export }"
    case "$l" in ''|'#'*|' '*'#'*) continue ;; *=*) ;; *) continue ;; esac
    k="${l%%=*}"; v="${l#*=}"
    case "$v" in \'*\') v="${v#\'}"; v="${v%\'}" ;; \"*\") v="${v#\"}"; v="${v%\"}" ;; esac
    case "$k" in
      AUTH) auth="$v" ;; RELAY_USER) user="$v" ;; RELAY_PASSWORD) pw="$v" ;; RELAY_URL) url="$v" ;;
      RELAY_FINGERPRINT) fp="$v" ;; RELAY_PORT|RELAY_PORTS) port="$v" ;; RELAY_ROLE) role="$v" ;;
    esac
  done
  if [ -n "$auth" ]; then user="${auth%%:*}"; pw="${auth#*:}"; [ "$auth" != "$user" ] || pw=""; fi
  case "$role" in ""|compute) ;; *) echo "RELAY_ROLE is '$role': the node needs a compute client's settings (relay/gen-credentials.sh client <node>, not the workstation's)" >&2; return 2 ;; esac
  case "$user" in ""|-*|*[!abcdefghijklmnopqrstuvwxyz0123456789-]*) echo "the relay user (RELAY_USER, or the part of AUTH before ':') is missing or not [a-z0-9-]" >&2; return 2 ;; esac
  [ "${#user}" -le 32 ] || { echo "the relay user is longer than 32 characters" >&2; return 2; }
  case "$pw" in *[!A-Za-z0-9_-]*) echo "the relay password (RELAY_PASSWORD) has characters outside A-Z a-z 0-9 _ -" >&2; return 2 ;; esac
  [ "${#pw}" -ge 24 ] || { echo "the relay password (RELAY_PASSWORD, or the part of AUTH after ':') is missing or shorter than 24 characters" >&2; return 2; }
  printf '%s\n' "$url" | grep -Eq '^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?/?$' || { echo "RELAY_URL is missing or not https://<host>[:port]" >&2; return 2; }
  printf '%s\n' "$fp" | grep -Eq '^[A-Za-z0-9+/]{43}=$' || { echo "RELAY_FINGERPRINT is missing or not the relay's fingerprint (44 characters of base64)" >&2; return 2; }
  case "$port" in 7[0-9][0-9][0-9]) [ "$port" -ge 7001 ] || port="" ;; *) port="" ;; esac
  [ -n "$port" ] || { echo "RELAY_PORT (RELAY_PORTS) is missing or not one port from 7001 to 7999" >&2; return 2; }
  printf 'AUTH=%s:%s\nRELAY_URL=%s\nRELAY_FINGERPRINT=%s\nRELAY_PORT=%s\n' "$user" "$pw" "${url%/}" "$fp" "$port"
}

# tunnel_classify < chisel client log lines (the tunnel service's journal): the state after the last line that says
# anything about the connection, as "STATE<TAB>REASON<TAB>LINE". STATE: connected | refused | busy | unreachable |
# starting. Lines of single streams ("tun: …") say nothing about the tunnel and are skipped
tunnel_classify() {
  LC_ALL=C awk '
    / tun: / { next }
    /Invalid fingerprint/ { s = "refused"; r = "relay-fingerprint"; l = $0; next }
    /Authentication failed|unable to authenticate/ { s = "refused"; r = "relay-credential"; l = $0; next }
    /access to .* denied/ { s = "refused"; r = "relay-rule"; l = $0; next }
    /cannot listen|address already in use/ { s = "busy"; r = "relay-port-busy"; l = $0; next }
    /Disconnected|Connection error|Give up|connection attempts exhausted/ { s = "unreachable"; r = "relay-unreachable"; l = $0; next }
    /Connected/ { s = "connected"; r = "connected"; l = $0; next }
    END { if (s == "") { s = "starting"; r = "no-connection-yet" } print s "\t" r "\t" l }'
}

# egress_ips < `getent ahosts HOST` output: the distinct IPv4 and IPv6 addresses, one per line, in order; nothing else
egress_ips() {
  LC_ALL=C awk '$1 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ || ($1 ~ /^[0-9a-fA-F:]+$/ && $1 ~ /:/) { if (!seen[$1]++) print $1 }'
}

# egress_dropin_text IP...: the drop-in for mu-tunnel.service: no address but loopback and these
egress_dropin_text() {
  local ip allow="127.0.0.1/32 ::1/128"
  for ip in "$@"; do case "$ip" in *:*) allow="$allow $ip/128" ;; *) allow="$allow $ip/32" ;; esac; done
  printf '# mu (mu-wsl2-distro tunnel refresh): the tunnel reaches loopback (mu-sshd) and the relay, nothing else.\n'
  printf '# chisel'"'"'s client dials whatever the relay names for a reverse remote (docs/design/job-layer.md §13.3)\n'
  printf '[Service]\nBindReadOnlyPaths=%s:/etc/hosts\nIPAddressDeny=any\nIPAddressAllow=%s\n' "$TUNNEL_HOSTS" "$allow"
}

# tunnel_hosts_text HOST IP...: the hosts file the tunnel sees as /etc/hosts
tunnel_hosts_text() {
  local h="$1" ip; shift
  printf '# mu-tunnel.service sees this as /etc/hosts (mu-wsl2-distro tunnel refresh writes it)\n127.0.0.1 localhost\n::1 localhost\n'
  for ip in "$@"; do printf '%s %s\n' "$ip" "$h"; done
}

# ready_line < the CHECKS file: "yes" or "no<TAB>names" — the checks a job needs, all pass (keys too: at least one
# workstation authorised)
ready_line() {
  LC_ALL=C awk -F'\t' '
    $2 ~ /^(keys|tunnel|tunnel\.credential|tunnel\.egress|sshd\.service|sshd\.config|gate|jobroot|node\.runner|node\.limits|node\.disk|node\.hello|test-fault)$/ {
      seen[$2] = 1; if ($1 != "pass") bad = bad (bad == "" ? "" : " ") $2 }
    END {
      n = split("keys tunnel tunnel.egress node.runner node.limits node.disk node.hello", need, " ")
      for (i = 1; i <= n; i++) if (!(need[i] in seen)) bad = bad (bad == "" ? "" : " ") need[i] "(not checked)"
      if (bad == "") print "yes"; else print "no\t" bad }'
}

# delegation_missing ROOT_CONTROLLERS USER_CONTROLLERS: the controllers among memory and pids that the kernel offers
# (the root cgroup has them) but the user's service manager was not given; empty if none
delegation_missing() {
  local c out=""
  for c in memory pids; do
    case " $1 " in *" $c "*) case " $2 " in *" $c "*) ;; *) out="$out $c" ;; esac ;; esac
  done
  printf '%s' "${out# }"
}

# host_drive_line LETTER [AUTOMOUNT_ROOT]: the line of /var/lib/mu-wsl2/host-drive ("C /mnt/c"); return 1 if LETTER is
# not one drive letter
host_drive_line() {
  local r="${2:-/mnt/}"
  case "$1" in [A-Za-z]) ;; *) return 1 ;; esac
  r="${r%/}"; [ -n "$r" ] || r=""
  printf '%s %s/%s\n' "$(printf '%s' "$1" | tr 'a-z' 'A-Z')" "$r" "$(printf '%s' "$1" | tr 'A-Z' 'a-z')"
}

# listeners_bad PORT < `ss -ltnpH` output: listeners that must not exist: anything on port 22, and any sshd listening
# elsewhere than 127.0.0.1:PORT. Prints "ADDRESS<TAB>PROCESS" each; returns 1 if any
listeners_bad() {
  LC_ALL=C awk -v want="127.0.0.1:$1" '
    { a = $4; p = ""; for (i = 6; i <= NF; i++) p = p (p == "" ? "" : " ") $i
      if (a ~ /:22$/ || (p ~ /"sshd/ && a != want)) { print a "\t" p; bad = 1 } }
    END { exit bad }'
}

# uu_conf_text: unattended upgrades from the security pocket only (and Ubuntu Pro's security pockets, which only apply
# when Pro is attached), every day, never an automatic reboot. The #clear lines drop what 50unattended-upgrades lists
uu_conf_text() {
  cat <<'EOF'
// mu (mu-wsl2-distro): security updates only, applied every day; no automatic reboot (docs/design/job-layer.md §14)
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
#clear Unattended-Upgrade::Allowed-Origins;
#clear Unattended-Upgrade::Origins-Pattern;
Unattended-Upgrade::Allowed-Origins {
  "${distro_id}:${distro_codename}-security";
  "${distro_id}ESMApps:${distro_codename}-apps-security";
  "${distro_id}ESM:${distro_codename}-infra-security";
};
Unattended-Upgrade::Automatic-Reboot "false";
EOF
}

# uu_origins_ok < `apt-config dump Unattended-Upgrade` output: every allowed origin and pattern is a security pocket,
# and there is at least one. Prints the offending entries; returns 1 if any (or none at all)
uu_origins_ok() {
  LC_ALL=C awk -F'"' '
    /^Unattended-Upgrade::(Allowed-Origins|Origins-Pattern):: / { n++; if ($2 !~ /security$/) { print $2; bad = 1 } }
    END { if (n == 0) { print "(no allowed origins)"; bad = 1 } exit bad }'
}

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
pkg_installed() { dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed"; }
# apt_get ARGS...: apt-get, waiting up to 10 min while another apt (the daily apt-daily run) holds a lock; the
# DPkg::Lock::Timeout of APT_OPTS covers dpkg's lock but not the lists lock of `apt-get update`
apt_get() {
  local i out rc
  for i in $(seq 1 40); do
    rc=0; out="$(DEBIAN_FRONTEND=noninteractive apt-get $APT_OPTS "$@" 2>&1)" || rc=$?
    case "$rc:$out" in 0:*) [ -z "$out" ] || printf '%s\n' "$out"; return 0 ;; *"Could not get lock"*|*"Unable to lock"*) sleep 15 ;; *) printf '%s\n' "$out" >&2; return "$rc" ;; esac
  done
  printf '%s\n' "$out" >&2; return "$rc"
}
ensure_packages() {
  local miss="" p
  # openssh-server only after ensure_ssh_masked; python3 reads JSON in status; the two marked ones are removed again
  # by uninstall if this step installed them
  for p in git openssh-client curl ca-certificates python3 openssh-server unattended-upgrades; do
    pkg_installed "$p" || miss="$miss $p"
  done
  if [ -z "$miss" ]; then same "packages: git, openssh-client, curl, python3, openssh-server, unattended-upgrades"; return 0; fi
  mkdir -p "$STATE_DIR"
  for p in $miss; do
    case "$p" in openssh-server|unattended-upgrades) grep -qx "$p" "$STATE_DIR/installed-packages" 2>/dev/null || echo "$p" >> "$STATE_DIR/installed-packages" ;; esac
  done
  apt_get update -qq
  # shellcheck disable=SC2086
  apt_get install -y -qq --no-install-recommends $miss
  changed "packages installed:$miss"
}

# ensure_ssh_masked: before openssh-server is installed, Ubuntu's own ssh.socket and ssh.service are masked, so that
# installing the package never opens port 22 (the node's sshd is mu-sshd.service, §13.2)
ensure_ssh_masked() {
  local u n=0
  mkdir -p "$STATE_DIR"
  for u in ssh.socket ssh.service; do
    if [ "$(readlink "/etc/systemd/system/$u" 2> /dev/null)" = /dev/null ]; then continue; fi
    if systemd_running; then systemctl stop "$u" 2> /dev/null || true; systemctl mask --quiet "$u"
    else rm -f "/etc/systemd/system/$u"; ln -s /dev/null "/etc/systemd/system/$u"; fi
    grep -qx "$u" "$STATE_DIR/masked-units" 2>/dev/null || echo "$u" >> "$STATE_DIR/masked-units"
    n=$((n + 1)); changed "$u masked (the distribution's sshd never listens; mu-sshd.service is the node's)"
  done
  [ "$n" -gt 0 ] || same "ssh.socket and ssh.service masked"
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
  if [ -f "$f" ]; then cat "$f"; fi | wsl_conf_merge "$MU_USER" "$NO_WSL" > "$tmp"
  if [ -f "$f" ] && cmp -s "$tmp" "$f"; then rm -f "$tmp"; same "/etc/wsl.conf: systemd, default user $MU_USER, no Windows PATH$([ "$NO_WSL" = 1 ] || echo ', interop off, drives for root only')"; return 0; fi
  if [ ! -e "$STATE_DIR/wsl.conf.orig" ] && [ ! -e "$STATE_DIR/wsl.conf.absent" ]; then
    if [ -f "$f" ]; then cp -p "$f" "$STATE_DIR/wsl.conf.orig"; else : > "$STATE_DIR/wsl.conf.absent"; fi
  fi
  put_file "$f" 0644 < "$tmp" || true; rm -f "$tmp"
  RESTART=1; changed "/etc/wsl.conf: [boot] systemd=true, [user] default=$MU_USER, [interop] appendWindowsPath=false$([ "$NO_WSL" = 1 ] || echo ", [interop] enabled=false, [automount] options=$AUTOMOUNT_OPTIONS") (applies at the next start of the distro)"
}

# ensure_host_drive: /var/lib/mu-wsl2/host-drive, "C /mnt/c": the Windows drive that holds the distro's disk, as the
# Windows script passed it (--host-drive). bin/mu, install/disk.sh and mu node read it first: with interop off they can
# no longer ask Windows (§13.4)
ensure_host_drive() {
  local f="$STATE_DIR/host-drive" line root
  root="$(ini_get automount root < /etc/wsl.conf 2> /dev/null || true)"
  if [ -z "$HOST_DRIVE" ]; then
    if [ -s "$f" ]; then same "$f: $(cat "$f") (kept; --host-drive changes it)"
    else warn "no --host-drive given and nothing recorded: once interop is off, μ's disk check sees only the virtual disk. Run: mu-wsl2-distro install --host-drive <the letter of the drive that holds the distro> (mu-wsl2-host.ps1 passes it)"; fi
    return 0
  fi
  line="$(host_drive_line "$HOST_DRIVE" "${root:-/mnt/}")" || die "--host-drive takes one drive letter, e.g. C"
  if printf '%s\n' "$line" | put_file "$f" 0644; then changed "$f: $line (μ's disk check reads it first)"; else same "$f: $line"; fi
}

WRITEBACK_CONF=/etc/sysctl.d/60-mu-writeback.conf
JOURNALD_CONF=/etc/systemd/journald.conf.d/60-mu.conf
STOPTIMEOUT_CONF=/etc/systemd/system.conf.d/60-mu-stop.conf
USER_STOPTIMEOUT_CONF=/etc/systemd/user.conf.d/60-mu-stop.conf
USER_AT_STOPTIMEOUT_CONF=/etc/systemd/system/user@.service.d/60-mu-stop.conf
CLEANSTOP_UNIT=/etc/systemd/system/mu-clean-stop.service
HEARTBEAT_UNIT=/etc/systemd/system/mu-heartbeat.service
# the job layer (docs/design/job-layer.md §13)
ETC_MU=/etc/mu
NODE_HOSTKEY=$ETC_MU/ssh_host_ed25519_key
SSHD_CONFIG=$ETC_MU/sshd_config
SSHD_UNIT=/etc/systemd/system/mu-sshd.service
SSHD_PORT=2222
KEYS_DIR="${MU_KEYS_DIR:-$ETC_MU/authorized_keys}"   # MU_KEYS_DIR: tools/test-wsl2-host only
LIB_MU=/usr/local/lib/mu
GATE=$LIB_MU/mu-node-gate
GATE_TEMPLATE=$LIB_MU/mu-node-gate.in
PAIR_PY=$LIB_MU/mu-pair.py                  # src/mu/pair.py: root runs this copy, never the release folder's
PAIR_STATE=/var/lib/mu-wsl2/pair
CHISEL_BIN=$LIB_MU/chisel
# the chisel client of the tunnel: the release and sums of toolchain/apps.toml and relay/Dockerfile (a test checks)
CHISEL_VERSION="1.12.0"
CHISEL_URL="https://github.com/jpillora/chisel/releases/download/v1.12.0/chisel_1.12.0_linux_amd64.gz"
CHISEL_GZ_SHA256="f3f180f1d93aa72cce4e6386f98cc06569a0146fbd65eb4423cf83e6434bcfe6"    # the .gz (the release's checksums file)
CHISEL_BIN_SHA256="811362311f13ac4bdc42fe4e7650aca618a68e3a40f930de88536d18b14d2b52"   # what it unpacks to
TUNNEL_ENV=$ETC_MU/tunnel.env
TUNNEL_UNIT=/etc/systemd/system/mu-tunnel.service
# the tunnel's egress filter (review finding 2): chisel's client dials whatever host:port the relay names for a
# reverse remote, so the unit may reach loopback and the relay's own addresses only; the relay's name resolves
# from a hosts file of its own (no DNS server needs to be reachable). Kept current by mu-tunnel-refresh.timer
TUNNEL_HOSTS=$ETC_MU/tunnel.hosts
TUNNEL_EGRESS=/etc/systemd/system/mu-tunnel.service.d/50-mu-egress.conf
REFRESH_SERVICE=/etc/systemd/system/mu-tunnel-refresh.service
REFRESH_TIMER=/etc/systemd/system/mu-tunnel-refresh.timer
TUNNEL_USER=mu-tunnel
JOB_ROOT=/srv/mu
UU_CONF=/etc/apt/apt.conf.d/52mu-unattended-upgrades
DELEGATE_CONF=/etc/systemd/system/user@.service.d/60-mu-delegate.conf
TEST_FAULT=$ETC_MU/test-fault
APT_OPTS="-o DPkg::Lock::Timeout=300"

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
  local me="$SELF_SRC" g
  g="$(dirname "$me")/mu-node-gate"
  if [ "$me" = "$SELF_PATH" ]; then same "$SELF_PATH"
  else
    [ -r "$me" ] || die "cannot read this script at $me to install it as $SELF_PATH"
    if put_file "$SELF_PATH" 0755 < "$me"; then changed "$SELF_PATH installed"; else same "$SELF_PATH"; fi
  fi
  # the gate's source, kept where a later run of $SELF_PATH finds it (mu-wsl2-host.ps1 copies it there too)
  if [ -f "$g" ]; then
    if put_file "$GATE_TEMPLATE" 0644 < "$g"; then chown root:root "$GATE_TEMPLATE"; changed "$GATE_TEMPLATE (the gate's source)"; else same "$GATE_TEMPLATE"; fi
  fi
  # the pairing client (pair): next to this script (the host script's stage), or src/mu/pair.py of a checkout
  local pp=""
  for pp in "$(dirname "$me")/mu-pair.py" "$(dirname "$me")/../../../src/mu/pair.py"; do [ -f "$pp" ] && break; pp=""; done
  if [ -n "$pp" ]; then
    install -d -m 755 -o root -g root "$LIB_MU"
    if put_file "$PAIR_PY" 0644 < "$pp"; then chown root:root "$PAIR_PY"; changed "$PAIR_PY (the pairing client)"; else same "$PAIR_PY"; fi
  fi
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

# ================================================================ job layer: install steps (docs/design/job-layer.md §13)
SSHD_RESTART=0; TUNNEL_RESTART=0
mu_home_of() { printf '%s/.local/share/mu' "$(user_home "$MU_USER")"; }
mu_bin_of() { printf '%s/bin/mu' "$(mu_home_of)"; }
# mu_job_layer: MU_USER's default μ version has the job layer (recipe/src/mu/cli.py, the release after v2026.11.6)
mu_job_layer() {
  local h v; h="$(mu_home_of)"; v="$(head -n 1 "$h/default" 2> /dev/null || true)"
  [ -n "$v" ] && [ -f "$h/toolchains/$v/recipe/src/mu/cli.py" ] && [ -x "$h/bin/mu" ]
}
mu_recipe_of() { local h; h="$(mu_home_of)"; printf '%s/toolchains/%s/recipe' "$h" "$(head -n 1 "$h/default" 2> /dev/null || true)"; }
as_mu_sd() {   # as_mu_sd CMD...: as MU_USER with a clean environment and its user manager's bus (systemctl --user)
  local uid home; uid="$(id -u "$MU_USER")"; home="$(user_home "$MU_USER")"
  # setsid: no controlling terminal (a process of mu cannot push input into root's terminal; TIOCSTI is off in
  # WSL's kernel anyway); stdin from /dev/null
  runuser -u "$MU_USER" -- setsid -w env -i HOME="$home" USER="$MU_USER" LOGNAME="$MU_USER" SHELL=/bin/bash LANG=C.UTF-8 \
    PATH="$home/.local/bin:/usr/local/bin:/usr/bin:/bin" XDG_RUNTIME_DIR="/run/user/$uid" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" "$@" < /dev/null
}
user_manager_up() {   # MU_USER's systemd user manager runs (linger starts it at boot); starts it if it can
  local uid; uid="$(id -u "$MU_USER" 2> /dev/null)" || return 1
  systemd_running || return 1
  local i
  [ -S "/run/user/$uid/bus" ] || systemctl start "user@$uid.service" 2> /dev/null || true
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do [ -S "/run/user/$uid/bus" ] && return 0; sleep 0.5; done
  return 1
}
gate_template() {
  local g; g="$(dirname "$SELF_SRC")/mu-node-gate"
  if [ -f "$g" ]; then printf '%s\n' "$g"; elif [ -f "$GATE_TEMPLATE" ]; then printf '%s\n' "$GATE_TEMPLATE"; else return 1; fi
}
gate_text() { local t; t="$(gate_template)" || return 1; sed -e "s|@MU_BIN@|$(mu_bin_of)|" -e "s|@MU_KEYS@|$KEYS_DIR/$MU_USER|" "$t"; }
owner_mode() { stat -c '%U:%G %a' "$1" 2> /dev/null; }
fix_owner_mode() {   # fix_owner_mode PATH USER:GROUP MODE
  [ -e "$1" ] || return 0
  [ "$(owner_mode "$1")" = "$2 $3" ] && return 0
  chown "$2" "$1"; chmod "$3" "$1"; changed "$1: owner $2, mode $3"
}
fp_of_line() {   # fp_of_line "TYPE BASE64": SHA256:… of that public key
  local t r; t="$(mktemp)"; printf '%s\n' "$1" > "$t"; r="$(ssh-keygen -l -f "$t" 2> /dev/null | awk '{ print $2 }')"; rm -f "$t"; printf '%s' "$r"
}
hostkey_fp() { ssh-keygen -l -f "$NODE_HOSTKEY.pub" 2> /dev/null | awk '{ print $2 }'; }
tunnel_val() { { sed -n "s/^$1=//p" "$TUNNEL_ENV" 2> /dev/null || true; } | tail -n 1; }   # root reads it; never prints AUTH's password
tunnel_user() { local a; a="$(tunnel_val AUTH)"; printf '%s' "${a%%:*}"; }

ensure_node_hostkey() {
  install -d -m 755 -o root -g root "$ETC_MU"
  if [ -f "$NODE_HOSTKEY" ]; then
    if [ ! -s "$NODE_HOSTKEY.pub" ]; then
      ssh-keygen -y -f "$NODE_HOSTKEY" > "$NODE_HOSTKEY.pub.tmp"; mv -f "$NODE_HOSTKEY.pub.tmp" "$NODE_HOSTKEY.pub"
      changed "$NODE_HOSTKEY.pub rebuilt from the key"
    fi
    fix_owner_mode "$NODE_HOSTKEY" root:root 600; fix_owner_mode "$NODE_HOSTKEY.pub" root:root 644
    same "node host key $NODE_HOSTKEY ($(hostkey_fp))"
  else
    ssh-keygen -q -t ed25519 -N '' -C "mu-node@${WSL_DISTRO_NAME:-$(hostname)}" -f "$NODE_HOSTKEY" < /dev/null
    chown root:root "$NODE_HOSTKEY" "$NODE_HOSTKEY.pub"; chmod 600 "$NODE_HOSTKEY"; chmod 644 "$NODE_HOSTKEY.pub"
    changed "node host key $NODE_HOSTKEY made: $(hostkey_fp) (the node's identity; uninstall keeps it)"
  fi
}

ensure_sshd_config() {
  local tmp out
  install -d -m 755 -o root -g root "$ETC_MU"
  tmp="$(mktemp "$SSHD_CONFIG.XXXXXX")"
  sshd_config_text "$SSHD_PORT" "$MU_USER" "$NODE_HOSTKEY" "$KEYS_DIR/%u" "$GATE" > "$tmp"
  if [ -f "$SSHD_CONFIG" ] && cmp -s "$tmp" "$SSHD_CONFIG"; then rm -f "$tmp"; fix_owner_mode "$SSHD_CONFIG" root:root 644; same "$SSHD_CONFIG"; return 0; fi
  install -d -m 755 /run/sshd   # sshd -t needs its privilege separation folder; mu-sshd.service makes it at each start
  if ! out="$(/usr/sbin/sshd -t -f "$tmp" 2>&1)"; then rm -f "$tmp"; die "sshd -t refused the new $SSHD_CONFIG (the old one is unchanged): $out"; fi
  chown root:root "$tmp"; chmod 644 "$tmp"; fsync_path "$tmp"; mv -f "$tmp" "$SSHD_CONFIG"; fsync_path "$ETC_MU"
  SSHD_RESTART=1; changed "$SSHD_CONFIG (loopback :$SSHD_PORT, key only, user $MU_USER, ForceCommand $GATE; sshd -t passed first)"
}

sshd_unit_text() {
  cat <<EOF
[Unit]
Description=mu node sshd (loopback only, forced command)
Documentation=file://$SELF_PATH
After=network.target

[Service]
Type=notify
ExecStartPre=/usr/sbin/sshd -t -f $SSHD_CONFIG
ExecStart=/usr/sbin/sshd -D -e -f $SSHD_CONFIG
RuntimeDirectory=sshd
RuntimeDirectoryMode=0755
RuntimeDirectoryPreserve=yes
KillMode=process
Restart=on-failure
RestartSec=2
TimeoutStopSec=5

[Install]
WantedBy=multi-user.target
EOF
}

ensure_sshd_unit() {
  local i
  if sshd_unit_text | put_file "$SSHD_UNIT" 0644; then changed "mu-sshd.service installed"; SSHD_RESTART=1; if systemd_running; then systemctl daemon-reload; fi
  else same "mu-sshd.service"; fi
  if ! systemctl is-enabled --quiet mu-sshd.service 2> /dev/null; then systemctl enable --quiet mu-sshd.service; changed "mu-sshd.service enabled"; fi
  systemd_running || return 0
  if ! systemctl is-active --quiet mu-sshd.service; then systemctl start mu-sshd.service; changed "mu-sshd.service started"
  elif [ "$SSHD_RESTART" = 1 ]; then systemctl restart mu-sshd.service; changed "mu-sshd.service restarted (open sessions continue)"; fi
  for i in 1 2 3 4 5 6 7 8 9 10; do ss -ltnH "sport = :$SSHD_PORT" 2> /dev/null | grep -q "127.0.0.1:$SSHD_PORT" && return 0; sleep 0.5; done
  warn "mu-sshd.service does not listen on 127.0.0.1:$SSHD_PORT yet: journalctl -u mu-sshd"
}

ensure_authorized_keys() {
  local f="$KEYS_DIR/$MU_USER"
  if [ ! -d "$KEYS_DIR" ]; then install -d -m 755 -o root -g root "$KEYS_DIR"; changed "$KEYS_DIR (root, 0755)"; fi
  fix_owner_mode "$KEYS_DIR" root:root 755
  if [ ! -f "$f" ]; then
    : > "$f"; chown root:root "$f"; chmod 644 "$f"
    changed "$f (no key yet: mu-wsl2-distro authorize <workstation> '<public key from mu compute add>')"
  else
    fix_owner_mode "$f" root:root 644
    same "$f: $(authkeys_list < "$f" | wc -l | tr -d ' ') key(s)"
  fi
}

ensure_gate() {
  gate_template > /dev/null || die "the gate's source, mu-node-gate, is neither next to this script nor in $GATE_TEMPLATE: copy the whole install/hosts/wsl2 folder"
  install -d -m 755 -o root -g root "$LIB_MU"
  if gate_text | put_file "$GATE" 0755; then chown root:root "$GATE"; changed "$GATE (sshd's forced command for $MU_USER; runs $(mu_bin_of) node serve)"
  else fix_owner_mode "$GATE" root:root 755; same "$GATE"; fi
}

ensure_job_root() {
  local grp; grp="$(id -gn "$MU_USER")"
  if [ ! -d "$JOB_ROOT" ]; then install -d -m 700 -o "$MU_USER" -g "$grp" "$JOB_ROOT"; changed "$JOB_ROOT (owner $MU_USER, 0700: the job folders)"
  else fix_owner_mode "$JOB_ROOT" "$MU_USER:$grp" 700; same "$JOB_ROOT"; fi
}

# only if needed: Ubuntu 24.04's user@.service already delegates cpu, memory and pids (systemd 255; the CI probe saw it)
ensure_cgroup_delegation() {
  local uid f rootc userc miss
  uid="$(id -u "$MU_USER")"; f="/sys/fs/cgroup/user.slice/user-$uid.slice/user@$uid.service/cgroup.controllers"
  if [ -f "$DELEGATE_CONF" ]; then same "$DELEGATE_CONF (written earlier: memory and pids were missing)"; return 0; fi
  if ! user_manager_up || [ ! -r "$f" ]; then same "cgroup delegation: $MU_USER's user manager is not running; status checks it"; return 0; fi
  rootc="$(cat /sys/fs/cgroup/cgroup.controllers 2> /dev/null || true)"; userc="$(cat "$f")"
  miss="$(delegation_missing "$rootc" "$userc")"
  if [ -n "$miss" ]; then
    printf '# mu (mu-wsl2-distro): %s was not delegated to user managers; jobs need memory limits (job-layer.md §10)\n[Service]\nDelegate=cpu memory pids\n' "$miss" \
      | put_file "$DELEGATE_CONF" 0644 || true
    systemctl daemon-reload; RESTART=1
    changed "$DELEGATE_CONF: Delegate=cpu memory pids ($miss missing; applies when $MU_USER's user manager restarts: restart the distro)"
  else
    case " $userc " in
      *" memory "*) same "cgroup delegation: $MU_USER's user manager has: $userc (no drop-in needed)" ;;
      *) warn "the kernel gives this distro no cgroup v2 memory controller ($rootc): jobs are refused until it does (docs/design/job-layer.md §19 step 1)" ;;
    esac
  fi
}

tunnel_unit_text() {
  cat <<EOF
[Unit]
Description=mu relay tunnel (chisel client: reverse tunnel from the relay to mu-sshd)
Documentation=file://$SELF_PATH
After=network-online.target mu-sshd.service
Wants=network-online.target
ConditionPathExists=$TUNNEL_ENV

[Service]
User=$TUNNEL_USER
Group=$TUNNEL_USER
EnvironmentFile=$TUNNEL_ENV
Environment=CHISEL_WS_TIMEOUT=10s
ExecStart=$CHISEL_BIN client --fingerprint \${RELAY_FINGERPRINT} --keepalive 25s --max-retry-interval 15s \${RELAY_URL} R:127.0.0.1:\${RELAY_PORT}:127.0.0.1:$SSHD_PORT
Restart=always
RestartSec=5
TimeoutStopSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
RestrictNamespaces=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
SystemCallArchitectures=native
CapabilityBoundingSet=
AmbientCapabilities=
UMask=0077

[Install]
WantedBy=multi-user.target
EOF
}

chisel_ok() { [ -x "$CHISEL_BIN" ] && [ "$(sha256sum "$CHISEL_BIN" 2> /dev/null | cut -d' ' -f1)" = "$CHISEL_BIN_SHA256" ]; }
relay_host() { local u; u="$(tunnel_val RELAY_URL)"; u="${u#*://}"; u="${u%%/*}"; printf '%s' "${u%%:*}"; }
# egress_refresh: the tunnel's hosts file and egress drop-in from the relay's addresses now (getent, outside the
# sandbox). A name that does not resolve leaves both as they are (return 1). Changes: daemon-reload, TUNNEL_RESTART=1
egress_refresh() {
  local host ips chg=0
  [ -f "$TUNNEL_ENV" ] || return 0
  host="$(relay_host)"; [ -n "$host" ] || { warn "no relay host in $TUNNEL_ENV"; return 1; }
  if host_fault no-egress-filter; then   # test hook (e2e): the tunnel as before the review, without the filter
    if [ -f "$TUNNEL_EGRESS" ]; then rm -f "$TUNNEL_EGRESS"; if systemd_running; then systemctl daemon-reload; fi; TUNNEL_RESTART=1; fi
    warn "test fault no-egress-filter: mu-tunnel.service has no egress filter"; return 0
  fi
  ips="$(getent ahosts "$host" 2> /dev/null | egress_ips || true)"
  if [ -z "$ips" ]; then warn "the relay's name $host does not resolve now: the tunnel's egress filter is left as it was"; return 1; fi
  # shellcheck disable=SC2086
  if tunnel_hosts_text "$host" $ips | put_file "$TUNNEL_HOSTS" 0644; then chg=1; fi
  # shellcheck disable=SC2086
  if egress_dropin_text $ips | put_file "$TUNNEL_EGRESS" 0644; then chg=1; fi
  if [ "$chg" = 1 ]; then
    changed "mu-tunnel.service egress: loopback and $host ($(printf '%s' "$ips" | tr '\n' ' ' | sed 's/ $//')) only"
    if systemd_running; then systemctl daemon-reload; fi; TUNNEL_RESTART=1
  else same "mu-tunnel.service egress: loopback and $host ($(printf '%s' "$ips" | tr '\n' ' ' | sed 's/ $//')) only"; fi
}
refresh_units() {
  local chg=0
  if printf '[Unit]\nDescription=mu: the relay'"'"'s addresses for the tunnel'"'"'s egress filter (mu-wsl2-distro tunnel refresh)\nConditionPathExists=%s\n\n[Service]\nType=oneshot\nExecStart=%s tunnel refresh\n' \
       "$TUNNEL_ENV" "$SELF_PATH" | put_file "$REFRESH_SERVICE" 0644; then chg=1; fi
  if printf '[Unit]\nDescription=mu: refresh the tunnel'"'"'s egress filter hourly\n\n[Timer]\nOnBootSec=3min\nOnUnitActiveSec=1h\nRandomizedDelaySec=5min\n\n[Install]\nWantedBy=timers.target\n' \
       | put_file "$REFRESH_TIMER" 0644; then chg=1; fi
  if [ "$chg" = 1 ]; then changed "mu-tunnel-refresh.timer installed (hourly: the relay's addresses for the egress filter)"; if systemd_running; then systemctl daemon-reload; fi
  else same "mu-tunnel-refresh.timer"; fi
  if ! systemctl is-enabled --quiet mu-tunnel-refresh.timer 2> /dev/null; then systemctl enable --quiet mu-tunnel-refresh.timer; changed "mu-tunnel-refresh.timer enabled"; fi
  if systemd_running && ! systemctl is-active --quiet mu-tunnel-refresh.timer; then systemctl start mu-tunnel-refresh.timer; fi
}
ensure_tunnel() {
  local tmp gz cache from
  if [ "$(uname -m)" != x86_64 ]; then warn "the tunnel's chisel client is pinned for x86_64 only (this is $(uname -m)): tunnel not installed"; return 0; fi
  install -d -m 755 -o root -g root "$LIB_MU"
  if chisel_ok; then same "$CHISEL_BIN (chisel $CHISEL_VERSION, SHA-256 as pinned)"
  else
    tmp="$(mktemp -d)"; gz="$tmp/chisel.gz"
    cache="$(user_home "$MU_USER")/.cache/mu/downloads/chisel_${CHISEL_VERSION}_linux_amd64.gz"
    if host_fault chisel-no-download || ! curl -fsSL --max-time 180 -o "$gz" "$CHISEL_URL" 2> /dev/null \
         || [ "$(sha256sum "$gz" | cut -d' ' -f1)" != "$CHISEL_GZ_SHA256" ]; then
      # the cache is mu's (a job could replace it): a regular file, not a link, at most 50 MB, read with a time limit; the
      # sum of what was copied decides (a swap after the check cannot pass the sums below)
      if [ -f "$cache" ] && [ ! -L "$cache" ] && [ "$(stat -c %s "$cache" 2> /dev/null || echo 0)" -le 52428800 ] \
         && timeout 60 cp --no-dereference "$cache" "$gz" 2> /dev/null && [ "$(sha256sum "$gz" | cut -d' ' -f1)" = "$CHISEL_GZ_SHA256" ]; then from="μ's download cache"
      else rm -rf "$tmp"; die "could not download $CHISEL_URL with the pinned SHA-256, and $cache is not there either: check the network, then run install again"; fi
    else from="github.com"; fi
    gunzip -c "$gz" > "$tmp/chisel"
    if [ "$(sha256sum "$tmp/chisel" | cut -d' ' -f1)" != "$CHISEL_BIN_SHA256" ]; then rm -rf "$tmp"; die "the unpacked chisel is not the pinned binary (SHA-256 differs)"; fi
    install -o root -g root -m 755 "$tmp/chisel" "$CHISEL_BIN.new"; mv -f "$CHISEL_BIN.new" "$CHISEL_BIN"; rm -rf "$tmp"
    [ "$("$CHISEL_BIN" --version 2>&1)" = "$CHISEL_VERSION" ] || die "$CHISEL_BIN does not report version $CHISEL_VERSION"
    TUNNEL_RESTART=1; changed "$CHISEL_BIN: chisel $CHISEL_VERSION (from $from; SHA-256 of the archive and of the binary checked)"
  fi
  if id -u "$TUNNEL_USER" > /dev/null 2>&1; then same "system user $TUNNEL_USER"
  else
    useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$TUNNEL_USER"
    changed "system user $TUNNEL_USER (runs the tunnel; no login, no home)"
  fi
  if tunnel_unit_text | put_file "$TUNNEL_UNIT" 0644; then changed "mu-tunnel.service installed"; TUNNEL_RESTART=1; if systemd_running; then systemctl daemon-reload; fi
  else same "mu-tunnel.service"; fi
  refresh_units
  if [ ! -f "$TUNNEL_ENV" ]; then
    warn "no relay credential yet: the tunnel starts once it has one. Run: mu-wsl2-distro tunnel credential (it reads the lines of relay/gen-credentials.sh client <node> from stdin, never from an argument)"
    return 0
  fi
  fix_owner_mode "$TUNNEL_ENV" "root:$TUNNEL_USER" 640
  egress_refresh || true
  tunnel_enable_start
}
tunnel_enable_start() {
  if ! systemctl is-enabled --quiet mu-tunnel.service 2> /dev/null; then systemctl enable --quiet mu-tunnel.service; changed "mu-tunnel.service enabled"; fi
  systemd_running || return 0
  if ! systemctl is-active --quiet mu-tunnel.service; then systemctl start mu-tunnel.service; changed "mu-tunnel.service started"
  elif [ "$TUNNEL_RESTART" = 1 ]; then systemctl restart mu-tunnel.service; changed "mu-tunnel.service restarted"; fi
}

ensure_security_updates() {
  local t
  if uu_conf_text | put_file "$UU_CONF" 0644; then changed "$UU_CONF: unattended upgrades, security pocket only, daily, no automatic reboot"
  else same "$UU_CONF (security updates only)"; fi
  systemd_running || return 0
  for t in apt-daily.timer apt-daily-upgrade.timer; do   # they run unattended-upgrades; some images mask them
    if [ "$(systemctl is-enabled "$t" 2> /dev/null || true)" = masked ]; then systemctl unmask --quiet "$t" 2> /dev/null || true; fi
    if ! systemctl is-enabled --quiet "$t" 2> /dev/null; then
      if systemctl enable --quiet --now "$t" 2> /dev/null; then changed "$t enabled (it runs unattended-upgrades)"; else warn "could not enable $t"; fi
    fi
  done
}

# ensure_node_enabled: MU_USER's job runner (mu-node.service, a user unit: mu node enable needs no root). Runs once μ
# with the job layer is MU_USER's default version; until then it says so and status reports it
ensure_node_enabled() {
  local mubin name cur out rc=0
  mubin="$(mu_bin_of)"
  if ! mu_job_layer; then
    warn "μ of $MU_USER has no job layer yet (it comes with the release after v2026.11.6; μ updates itself daily, or now: wsl -d ${WSL_DISTRO_NAME:-<distro>} -u $MU_USER --cd ~ -- mu update). Then run install again: it enables the runner"
    return 0
  fi
  if ! user_manager_up; then warn "$MU_USER's systemd user manager is not running (linger, a restart of the distro): the runner is enabled by the next install"; return 0; fi
  name="$NODE_NAME"; [ -n "$name" ] || name="$(tunnel_user)"
  cur="$( (as_mu_sd "$mubin" node info --json 2> /dev/null || true) | sed -n 's/.*"node": "\([a-z0-9-]*\)".*/\1/p' | head -n 1)"
  if [ "$(as_mu_sd systemctl --user is-active mu-node.service 2> /dev/null || true)" = active ] \
     && [ "$(as_mu_sd systemctl --user is-enabled mu-node.service 2> /dev/null || true)" = enabled ] \
     && { [ -z "$name" ] || [ "$name" = "$cur" ]; }; then
    same "job runner mu-node.service active for $MU_USER (node ${cur:-?})"; return 0
  fi
  if [ -n "$name" ]; then out="$(as_mu_sd "$mubin" node enable --name "$name" 2>&1)" || rc=$?
  else out="$(as_mu_sd "$mubin" node enable 2>&1)" || rc=$?; fi
  printf '%s\n' "$out" | sed 's/^/    /'
  if [ "$rc" = 0 ]; then changed "job runner mu-node.service enabled for $MU_USER (node ${name:-its host name}; memory limit self-test passed)"
  else warn "mu node enable: exit $rc (above). Jobs are refused until the runner is active and the memory self-test passes (docs/design/job-layer.md §19 step 1)"; fi
}

do_install() {
  [ "$(id -u)" = 0 ] || die "run as root: wsl -d <distro> -u root -- $0 install"
  is_wsl || warn "this does not look like WSL: continuing, but the settings are meant for a WSL2 distro"
  printf 'mu-wsl2-distro install: user %s%s\n' "$MU_USER" "${WSL_DISTRO_NAME:+, distro $WSL_DISTRO_NAME}"
  step "System"
  ensure_ssh_masked; ensure_packages; ensure_user; ensure_wsl_conf; ensure_self
  [ "$NO_WSL" = 1 ] || ensure_host_drive
  step "Clean stops and write-back"
  ensure_writeback; ensure_journald; ensure_stop_timeout; ensure_clean_stop_unit
  step "User services"
  ensure_linger
  step "GitHub access"
  ensure_ssh; ACCESS=unknown; ensure_clone
  if [ "$INSTALL_MU" = 1 ]; then
    if [ "$ACCESS" = ok ]; then install_mu; else warn "--install-mu: skipped until $MU_USER can read the repo"; fi
  fi
  step "Job layer: the node's sshd, keys, gate and job folder (docs/design/job-layer.md §13)"
  ensure_node_hostkey; ensure_sshd_config; ensure_sshd_unit; ensure_authorized_keys; ensure_gate; ensure_job_root
  ensure_cgroup_delegation
  step "Job layer: the relay tunnel"
  ensure_tunnel
  step "Security updates"
  ensure_security_updates
  step "Job layer: the runner (as $MU_USER)"
  ensure_node_enabled
  case "$HEARTBEAT" in
    on) step "Heartbeat test aid"; heartbeat_on ;;
    off) step "Heartbeat test aid"; heartbeat_off ;;
  esac
  mkdir -p /run/mu-wsl2
  printf 'changed=%s\nrestart=%s\naccess=%s\n' "$CHANGED" "$RESTART" "${ACCESS%%:*}" > /run/mu-wsl2/install-result
  echo
  if [ "$CHANGED" = 0 ]; then echo "mu-wsl2-distro: nothing to change, already set up."
  else echo "mu-wsl2-distro: $CHANGED change(s)."; fi
  if [ "$RESTART" = 1 ] && [ "$FROM_HOST" = 0 ]; then echo "Restart the distro to apply them (from Windows, cleanly: C:\\Mu\\host\\mu-wsl2-host.ps1 test-stop)."; fi
  # what is left for the job layer, in order
  if [ ! -f "$TUNNEL_ENV" ]; then echo "Next: the relay credential, on stdin: mu-wsl2-distro tunnel credential"
  elif [ ! -s "$KEYS_DIR/$MU_USER" ]; then echo "Next: authorise a workstation: mu-wsl2-distro authorize <name> '<public key printed by mu compute add>' (the host key for it: mu-wsl2-distro host-key)"; fi
}

do_uninstall() {
  [ "$(id -u)" = 0 ] || die "run as root"
  echo "mu-wsl2-distro uninstall (keeps user $MU_USER, its files, its ssh key, the clone and μ itself)"
  uninstall_jobs
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
  echo "Kept: the node's host key $NODE_HOSTKEY (its identity: a reinstall keeps the workstations' pin); the job"
  echo "  folders in $JOB_ROOT (job data; rm -rf $JOB_ROOT by hand)."
  echo "Kept: linger for $MU_USER (loginctl disable-linger $MU_USER); $STATE_DIR/stops.log; in ~$MU_USER/.ssh the key, the"
  echo "  managed github.com block in config (between the '# >>> mu-wsl2' and '# <<< mu-wsl2' lines) and GitHub's key in"
  echo "  known_hosts (ssh-keygen -R github.com -f ~$MU_USER/.ssh/known_hosts); $SELF_PATH itself and the gate's source"
  echo "  $GATE_TEMPLATE and the pairing client $PAIR_PY (mu-wsl2-host.ps1 uninstall deletes them next; by hand:"
  echo "  rm $SELF_PATH $GATE_TEMPLATE $PAIR_PY)."
  echo "Restart the distro to apply (from Windows, cleanly: C:\\Mu\\host\\mu-wsl2-host.ps1 test-stop)."
}

# uninstall_jobs: undoes the job layer's steps, except the node's host key (its identity) and the job folders (data)
uninstall_jobs() {
  local u p f
  step "Job layer"
  if [ -n "$(user_home "$MU_USER" 2> /dev/null || true)" ] && [ -x "$(mu_bin_of)" ] && mu_job_layer && user_manager_up \
     && [ -f "$(user_home "$MU_USER")/.config/systemd/user/mu-node.service" ]; then
    as_mu_sd "$(mu_bin_of)" node disable > /dev/null 2>&1 || true
    changed "job runner mu-node.service stopped and removed for $MU_USER (job folders kept)"
  fi
  if [ -f "$REFRESH_TIMER" ]; then
    systemctl disable --quiet --now mu-tunnel-refresh.timer 2> /dev/null || systemctl disable --quiet mu-tunnel-refresh.timer 2> /dev/null || true
    rm -f "$REFRESH_TIMER" "$REFRESH_SERVICE"; changed "mu-tunnel-refresh.timer stopped and removed"
  fi
  for u in mu-tunnel mu-sshd; do
    if [ -f "/etc/systemd/system/$u.service" ]; then
      systemctl disable --quiet --now "$u.service" 2> /dev/null || systemctl disable --quiet "$u.service" 2> /dev/null || true
      rm -f "/etc/systemd/system/$u.service"; changed "$u.service stopped and removed"
    fi
  done
  if systemd_running; then systemctl daemon-reload; fi
  # kept: $GATE_TEMPLATE, the gate's source, which belongs with $SELF_PATH (mu-wsl2-host.ps1 uninstall deletes both)
  for f in "$TUNNEL_ENV" "$TUNNEL_HOSTS" "$TUNNEL_EGRESS" "$SSHD_CONFIG" "$KEYS_DIR/$MU_USER" "$GATE" "$CHISEL_BIN" "$UU_CONF" \
           "$DELEGATE_CONF" "$STATE_DIR/host-drive"; do
    if [ -e "$f" ]; then rm -f "$f"; changed "$f removed"; fi
  done
  if [ -d "$PAIR_STATE" ]; then rm -rf "$PAIR_STATE"; changed "$PAIR_STATE removed (pairing state)"; fi
  rmdir "$KEYS_DIR" "$LIB_MU" "$(dirname "$TUNNEL_EGRESS")" 2> /dev/null || true
  if systemd_running; then systemctl daemon-reload; fi
  if id -u "$TUNNEL_USER" > /dev/null 2>&1; then userdel "$TUNNEL_USER" 2> /dev/null || true; changed "system user $TUNNEL_USER removed"; fi
  if [ -s "$STATE_DIR/installed-packages" ]; then
    p="$(tr '\n' ' ' < "$STATE_DIR/installed-packages")"
    # shellcheck disable=SC2086
    apt_get remove -y -qq $p > /dev/null && { rm -f "$STATE_DIR/installed-packages"; changed "packages removed (install had added them): $p"; } \
      || warn "could not remove $p (apt-get remove): remove them by hand"
  fi
  if [ -s "$STATE_DIR/masked-units" ]; then
    for u in $(cat "$STATE_DIR/masked-units"); do
      if systemd_running; then systemctl unmask --quiet "$u" 2> /dev/null || true; else [ "$(readlink "/etc/systemd/system/$u")" != /dev/null ] || rm -f "/etc/systemd/system/$u"; fi
      changed "$u unmasked (as before install; not started)"
    done
    rm -f "$STATE_DIR/masked-units"
  fi
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
  local wslkeys="boot:systemd:true user:default:$MU_USER interop:appendWindowsPath:false"
  [ "$NO_WSL" = 1 ] || wslkeys="$wslkeys interop:enabled:false automount:enabled:true automount:options:$AUTOMOUNT_OPTIONS"
  for v in $wslkeys; do
    set -- $(printf '%s' "$v" | tr ':' ' ')
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
  status_jobs
  if [ "$NO_WSL" = 0 ] && is_wsl; then status_reach; fi
  # what the VM got from .wslconfig (the Windows script compares)
  local cpus mem swap
  cpus="$(nproc)"; mem="$(awk '/^MemTotal:/ { printf "%d", $2 / 1024 }' /proc/meminfo)"; swap="$(awk '/^SwapTotal:/ { printf "%d", $2 / 1024 }' /proc/meminfo)"
  check info resources "$cpus CPUs, $mem MiB memory, $swap MiB swap"
  init_started="$(date -u -d "$(ps -o lstart= -p 1 2>/dev/null)" +%FT%TZ 2>/dev/null || true)"
  [ -z "$init_started" ] || check info started "distro started $init_started"

  if [ -f "$PAIR_STATE/current.json" ]; then
    check warn pairing "half done (invite $(sed -n 's/.*"id": *"\([0-9A-Z]\{4\}\)".*/\1/p' "$PAIR_STATE/current.json")): run the pair line again with its code, or mu-wsl2-distro pair abort"
  fi
  local nfail ready why; nfail="$(awk -F'\t' '$1 == "fail"' "$CHECKS" | wc -l | tr -d ' ')"
  ready="$(ready_line < "$CHECKS")"; why="$(printf '%s' "$ready" | cut -s -f2)"; ready="$(printf '%s' "$ready" | cut -f1)"
  if [ "$JSON" = 1 ]; then
    printf '{"ok":%s,"ready":%s,"not_ready":%s,"distro":%s,"user":%s,"cpus":%s,"mem_mib":%s,"swap_mib":%s,"systemd":%s,"started":%s,"checks":' \
      "$([ "$nfail" = 0 ] && echo true || echo false)" "$([ "$ready" = yes ] && echo true || echo false)" "$(json_str "$why")" \
      "$(json_str "${WSL_DISTRO_NAME:-}")" "$(json_str "$MU_USER")" \
      "${cpus:-0}" "${mem:-0}" "${swap:-0}" "$(systemd_running && echo true || echo false)" "$(json_str "$init_started")"
    checks_json; printf '}\n'
  else
    checks_text
    if [ "$nfail" = 0 ]; then echo "mu-wsl2-distro status: all checks pass"; else echo "mu-wsl2-distro status: $nfail check(s) failed"; fi
    if [ "$ready" = yes ]; then echo "ready for jobs: yes (a workstation is authorised, the tunnel is connected and filtered, the runner is active, the memory limit is enforced)"
    else echo "ready for jobs: no ($why)"; fi
  fi
  [ "$nfail" = 0 ]
}
checks_json() {
  local sep="" r n d
  printf '['
  while IFS="$(printf '\t')" read -r r n d; do
    printf '%s{"result":%s,"name":%s,"detail":%s}' "$sep" "$(json_str "$r")" "$(json_str "$n")" "$(json_str "$d")"; sep=","
  done < "$CHECKS"
  printf ']'
}
checks_text() {
  local r n d
  while IFS="$(printf '\t')" read -r r n d; do
    printf '  %-5s %-22s %s\n' "$(printf '%s' "$r" | tr 'a-z' 'A-Z')" "$n" "$d"
  done < "$CHECKS"
}

# ---------------------------------------------------------------- status: the job layer
tunnel_journal() {   # this run's log lines of the tunnel service (chisel's own lines, with its timestamps)
  local id; id="$(systemctl show -p InvocationID --value mu-tunnel.service 2> /dev/null || true)"
  if [ -n "$id" ]; then journalctl -q --no-pager -o cat "_SYSTEMD_INVOCATION_ID=$id" 2> /dev/null || true
  else journalctl -q --no-pager -o cat -u mu-tunnel.service -n 100 2> /dev/null || true; fi
}
hello_via_gate() {   # the gate's answer to hello, run as sshd runs a ForceCommand: the user's shell with -c
  local home sh; home="$(user_home "$MU_USER")"; sh="$(getent passwd "$MU_USER" | cut -d: -f7)"
  timeout 60 runuser -u "$MU_USER" -- env -i HOME="$home" USER="$MU_USER" LOGNAME="$MU_USER" SHELL="$sh" PATH=/usr/bin:/bin \
    SSH_CLIENT="127.0.0.1 1 $SSHD_PORT" SSH_CONNECTION="127.0.0.1 1 127.0.0.1 $SSHD_PORT" SSH_ORIGINAL_COMMAND="hello protocol=1" \
    "$sh" -c "$GATE" < /dev/null 2> /dev/null || true
}
json_get() {   # json_get PATH < one JSON object: the value at a.b.c (true/false/null as JSON), or nothing
  python3 -c 'import json, sys
try:
    d = json.loads(sys.stdin.read().strip().splitlines()[-1])
    for k in sys.argv[1].split("."):
        d = d.get(k) if isinstance(d, dict) else None
    print("" if d is None else (json.dumps(d) if isinstance(d, (bool, dict, list)) else d))
except Exception:
    pass' "$1" 2> /dev/null || true
}

status_jobs() {
  local u n out bad f v uid exp names info st state reason line free min where host
  uid="$(id -u "$MU_USER" 2> /dev/null || echo 0)"
  n=0; for u in ssh.socket ssh.service; do [ "$(readlink "/etc/systemd/system/$u" 2> /dev/null)" != /dev/null ] || n=$((n + 1)); done
  if [ "$n" = 2 ]; then check pass ssh.masked "Ubuntu's ssh.socket and ssh.service are masked"
  else check fail ssh.masked "Ubuntu's ssh.socket and ssh.service are not both masked (run install)"; fi
  if pkg_installed openssh-server; then check pass openssh-server "$(dpkg-query -W -f='${Version}' openssh-server 2> /dev/null)"
  else check fail openssh-server "not installed (run install)"; fi
  out="$( (ss -ltnpH 2> /dev/null || true) | listeners_bad "$SSHD_PORT" || true)"
  if [ -z "$out" ]; then check pass listeners "nothing listens on port 22; sshd only on 127.0.0.1:$SSHD_PORT"
  else check fail listeners "must not listen: $(printf '%s' "$out" | tr '\t\n' ' ;')"; fi
  # the host key, the configuration (by the values sshd uses), the service
  if [ -f "$NODE_HOSTKEY" ] && [ -s "$NODE_HOSTKEY.pub" ] && [ "$(owner_mode "$NODE_HOSTKEY")" = "root:root 600" ]; then
    check pass node.hostkey "$(cut -d' ' -f1,2 "$NODE_HOSTKEY.pub") $(hostkey_fp)"
  else check fail node.hostkey "$NODE_HOSTKEY missing or not root 0600 (run install)"; fi
  if [ -f "$SSHD_CONFIG" ]; then
    exp="$(mktemp)"; sshd_config_text "$SSHD_PORT" "$MU_USER" "$NODE_HOSTKEY" "$KEYS_DIR/%u" "$GATE" > "$exp"
    if out="$(/usr/sbin/sshd -T -f "$SSHD_CONFIG" -C "user=$MU_USER,host=localhost,addr=127.0.0.1" 2>&1)"; then
      bad="$(printf '%s\n' "$out" | sshd_effective_check "$exp" || true)"
      if [ -z "$bad" ]; then check pass sshd.config "$(grep -c '^[A-Z]' "$exp") settings in effect as §13.1 says (sshd -T -C user=$MU_USER,host=localhost,addr=127.0.0.1)"
      else check fail sshd.config "in effect, differs from §13.1: $(printf '%s\n' "$bad" | awk -F'\t' '{ printf "%s%s %s (want %s)", (NR > 1 ? "; " : ""), $1, $3, $2 }') (run install)"; fi
    else check fail sshd.config "sshd -T refuses $SSHD_CONFIG: $(printf '%s' "$out" | head -n 2 | tr '\n' ' ')"; fi
    rm -f "$exp"
  else check fail sshd.config "$SSHD_CONFIG missing (run install)"; fi
  if systemctl is-active --quiet mu-sshd.service 2> /dev/null && systemctl is-enabled --quiet mu-sshd.service 2> /dev/null; then
    if (ss -ltnH "sport = :$SSHD_PORT" 2> /dev/null || true) | grep -q "127.0.0.1:$SSHD_PORT"; then check pass sshd.service "mu-sshd.service active and enabled, listening on 127.0.0.1:$SSHD_PORT"
    else check fail sshd.service "mu-sshd.service is active but nothing listens on 127.0.0.1:$SSHD_PORT (journalctl -u mu-sshd)"; fi
  else check fail sshd.service "mu-sshd.service is $(systemctl is-active mu-sshd.service 2> /dev/null || true), $(systemctl is-enabled mu-sshd.service 2> /dev/null || true) (run install; journalctl -u mu-sshd)"; fi
  # who may log in, and what they get
  f="$KEYS_DIR/$MU_USER"
  if [ -f "$f" ] && [ "$(owner_mode "$f")" = "root:root 644" ] && [ "$(owner_mode "$KEYS_DIR")" = "root:root 755" ]; then
    names="$(authkeys_list < "$f" | while IFS="$(printf '\t')" read -r u v; do printf '%s %s, ' "$u" "$(fp_of_line "$v")"; done)"
    if grep -v '^[[:space:]]*$' "$f" | grep -qv '^restrict '; then check fail keys "a line of $f lacks 'restrict' (only mu-wsl2-distro authorize writes it)"
    elif [ -n "$names" ]; then check pass keys "${names%, }"
    else check warn keys "no workstation is authorised yet: mu-wsl2-distro authorize <name> '<public key from mu compute add>'"; fi
  else check fail keys "$f missing, or not root 0644 in a root 0755 folder (run install)"; fi
  if [ -f "$GATE" ] && [ "$(owner_mode "$GATE")" = "root:root 755" ] && gate_text 2> /dev/null | cmp -s - "$GATE"; then check pass gate "$GATE as install writes it (runs $(mu_bin_of) node serve)"
  else check fail gate "$GATE missing, changed or not root 0755 (run install)"; fi
  if [ "$(owner_mode "$JOB_ROOT")" = "$MU_USER:$(id -gn "$MU_USER" 2> /dev/null) 700" ]; then check pass jobroot "$JOB_ROOT ($MU_USER, 0700)"
  else check fail jobroot "$JOB_ROOT is '$(owner_mode "$JOB_ROOT")', want $MU_USER, 0700 (run install)"; fi
  f="/sys/fs/cgroup/user.slice/user-$uid.slice/user@$uid.service/cgroup.controllers"
  if [ -r "$f" ]; then
    v="$(cat "$f")"
    if [ -z "$(delegation_missing "memory pids" "$v")" ]; then check pass cgroup.delegation "$MU_USER's user manager has: $v"
    else check fail cgroup.delegation "$MU_USER's user manager has only '$v' (memory and pids needed; docs/design/job-layer.md §19 step 1)"; fi
  else check warn cgroup.delegation "$MU_USER's user manager is not running (linger)"; fi
  # the tunnel
  if chisel_ok; then check pass chisel "$CHISEL_BIN $CHISEL_VERSION (SHA-256 as pinned)"; else check fail chisel "$CHISEL_BIN missing or not the pinned binary (run install)"; fi
  if [ -f "$TUNNEL_ENV" ]; then
    if [ "$(owner_mode "$TUNNEL_ENV")" = "root:$TUNNEL_USER 640" ]; then
      check pass tunnel.credential "relay user $(tunnel_user), $(tunnel_val RELAY_URL), port $(tunnel_val RELAY_PORT) ($TUNNEL_ENV, root:$TUNNEL_USER 0640)"
    else check fail tunnel.credential "$TUNNEL_ENV is '$(owner_mode "$TUNNEL_ENV")', want root:$TUNNEL_USER 640 (run install)"; fi
    st="$(tunnel_journal | tunnel_classify)"; state="$(printf '%s' "$st" | cut -f1)"; reason="$(printf '%s' "$st" | cut -f2)"; line="$(printf '%s' "$st" | cut -f3)"
    if systemctl is-active --quiet mu-tunnel.service 2> /dev/null && [ "$state" = connected ]; then check pass tunnel "connected: $line"
    else check fail tunnel "mu-tunnel.service $(systemctl is-active mu-tunnel.service 2> /dev/null || true), $state ($reason)${line:+: $line} (mu-wsl2-distro tunnel status)"; fi
    status_egress
  else check fail tunnel.credential "no relay credential yet: mu-wsl2-distro tunnel credential (reads it from stdin)"; fi
  # security updates
  if pkg_installed unattended-upgrades && [ -f "$UU_CONF" ]; then
    out="$(apt-config dump 2> /dev/null || true)"
    v="$(printf '%s\n' "$out" | sed -n 's/^APT::Periodic::Unattended-Upgrade "\(.*\)";$/\1/p' | tail -n 1)"
    bad="$(printf '%s\n' "$out" | uu_origins_ok || true)"
    u="$(systemctl is-enabled apt-daily-upgrade.timer 2> /dev/null || true)"
    if [ "$v" = 1 ] && [ -z "$bad" ] && [ "$u" = enabled ]; then check pass updates "unattended-upgrades: daily, security pockets only"
    else check fail updates "unattended-upgrades: Unattended-Upgrade '$v', not security: '$(printf '%s' "$bad" | tr '\n' ' ')', apt-daily-upgrade.timer $u (run install)"; fi
  else check fail updates "unattended-upgrades or $UU_CONF missing (run install)"; fi
  if [ -e "$TEST_FAULT" ]; then check fail test-fault "$TEST_FAULT exists: a test hook that must never be on a real node (rm $TEST_FAULT)"; fi
  # the runner, the limits, the gate's answer
  if ! mu_job_layer; then
    check warn node.runner "μ of $MU_USER has no job layer yet (wsl -d ${WSL_DISTRO_NAME:-<distro>} -u $MU_USER --cd ~ -- mu update; then install again)"
    return 0
  fi
  if user_manager_up; then
    v="$(as_mu_sd systemctl --user is-active mu-node.service 2> /dev/null || true)"
    if [ "$v" = active ]; then check pass node.runner "mu-node.service active (user unit of $MU_USER)"
    else check fail node.runner "mu-node.service is $v (run install: it runs mu node enable as $MU_USER)"; fi
    info="$(as_mu_sd "$(mu_bin_of)" node info --json 2> /dev/null || true)"
    if [ "$(printf '%s' "$info" | json_get limits.memory)" = true ]; then check pass node.limits "memory limit enforced: $(printf '%s' "$info" | json_get limits.selftest_detail)"
    else check fail node.limits "memory limit not proven: $(printf '%s' "$info" | json_get limits.selftest_detail) (wsl -d ${WSL_DISTRO_NAME:-<distro>} -u $MU_USER --cd ~ -- mu node selftest; §19 step 1)"; fi
    free="$(printf '%s' "$info" | json_get disk.free_gb)"; min="$(printf '%s' "$info" | json_get disk.min_free_gb)"
    where="$(printf '%s' "$info" | json_get disk.where)"; host="$(printf '%s' "$info" | json_get disk.host)"
    if [ -n "$free" ] && [ -n "$min" ] && [ "$free" -ge "$min" ]; then check pass node.disk "$free GB free at $where (jobs need $min)${host:+, Windows drive $host}"
    else check fail node.disk "${free:-?} GB free at ${where:-?}, jobs need ${min:-?} (submit answers disk-low)"; fi
  else check fail node.runner "$MU_USER's user manager is not running (linger; restart the distro)"; fi
  out="$(hello_via_gate)"
  if [ "$(printf '%s\n' "$out" | grep -c .)" = 1 ] && [ "$(printf '%s' "$out" | json_get ok)" = true ]; then
    check pass node.hello "the gate answers hello with one JSON line, through $MU_USER's login shell as sshd runs it (node $(printf '%s' "$out" | json_get node))"
  else check fail node.hello "the gate's answer to hello is not one JSON line: '$(printf '%s' "$out" | head -c 160 | tr '\n' ' ')' (~$MU_USER/.bashrc must print nothing; μ with the job layer)"; fi
}

# egress_probe DENY ALLOW [TARGET]: in a unit of mu-tunnel with these IPAddressDeny/IPAddressAllow values, one UDP
# datagram to TARGET (default 192.0.2.1:9, TEST-NET-1: no real host). The filter drops a packet as it leaves; for
# UDP the send itself then fails with "Operation not permitted" (a TCP connect would only time out). Without the
# filter the datagram is sent (rc=0). Prints what bash said and rc=N
egress_probe() {
  local t="${3:-192.0.2.1/9}"
  timeout 40 systemd-run --quiet --wait --pipe --collect -p User="$TUNNEL_USER" -p IPAddressDeny="$1" -p IPAddressAllow="$2" \
    -p RuntimeMaxSec=30 /bin/bash -c 'timeout 5 bash -c "exec 3<>/dev/udp/$0 && printf x >&3" 2>&1; echo "rc=$?"' "$t" 2>&1 < /dev/null || true
}
status_egress() {   # the tunnel's egress filter, in effect (review finding 2)
  local deny allow probe host now rec
  deny="$(systemctl show mu-tunnel.service -p IPAddressDeny --value 2> /dev/null || true)"
  allow="$(systemctl show mu-tunnel.service -p IPAddressAllow --value 2> /dev/null || true)"
  if [ ! -f "$TUNNEL_EGRESS" ] || [ -z "$deny" ]; then
    check fail tunnel.egress "mu-tunnel.service has no egress filter: the relay could use the tunnel to reach other addresses (run: mu-wsl2-distro tunnel refresh)"
  elif ! systemd_running; then check info tunnel.egress "filter written ($allow); not checked without systemd"
  else
    probe="$(egress_probe "$deny" "$allow" | tr '\n' ' ')"
    case "$probe" in
      *"Operation not permitted"*) check pass tunnel.egress "only loopback and the relay ($allow); a connection elsewhere is refused by the kernel" ;;
      *) check fail tunnel.egress "the filter is set ($allow), but a datagram to 192.0.2.1 was not refused by it: '$(printf '%s' "$probe" | cut -c1-120)' (the kernel may lack cgroup BPF: docs/design/job-layer.md §19)" ;;
    esac
    host="$(relay_host)"
    now="$(getent ahosts "$host" 2> /dev/null | egress_ips | sort | tr '\n' ' ' || true)"
    rec="$(awk -v h="$host" '$2 == h { print $1 }' "$TUNNEL_HOSTS" 2> /dev/null | sort | tr '\n' ' ')"
    if [ -n "$now" ] && [ "$now" != "$rec" ]; then check warn tunnel.egress.dns "$host is now ${now% }; the filter has ${rec% } (mu-tunnel-refresh.timer updates it within the hour; now: mu-wsl2-distro tunnel refresh)"; fi
  fi
  if systemctl is-enabled --quiet mu-tunnel-refresh.timer 2> /dev/null; then check pass tunnel.refresh "mu-tunnel-refresh.timer enabled (hourly)"
  else check fail tunnel.refresh "mu-tunnel-refresh.timer is not enabled (run install)"; fi
}

# ---------------------------------------------------------------- status: what a process of the distro can reach of Windows
reach_mount() { local m; m="$(cut -d' ' -f2 "$STATE_DIR/host-drive" 2> /dev/null || true)"; [ -n "$m" ] || m="$(ini_get automount root < /etc/wsl.conf 2> /dev/null || true)"; [ -n "$m" ] || m=/mnt/; case "$m" in */c|*/[a-z]) printf '%s' "$m" ;; *) printf '%s/c' "${m%/}" ;; esac; }
status_reach() {
  local mnt exe out rc dir drive where
  if [ -s "$STATE_DIR/host-drive" ]; then check pass host-drive "$(cat "$STATE_DIR/host-drive") ($STATE_DIR/host-drive: μ's disk check reads it first)"
  else check fail host-drive "no record of the Windows drive: install --host-drive <letter> (mu-wsl2-host.ps1 install passes it)"; fi
  mnt="$(reach_mount)"; exe="$mnt/Windows/System32/whoami.exe"
  if [ ! -d "$mnt/Windows" ]; then check info reach.mount "$mnt/Windows is not there ([automount] off?): no Windows program to start from it"; fi
  # root: interop alone decides (root reads every drive)
  if [ -f "$exe" ]; then
    rc=0; out="$(timeout 20 "$exe" 2>&1 < /dev/null)" || rc=$?
    if [ "$rc" = 0 ]; then check fail reach.root "root started $exe: '$(printf '%s' "$out" | head -n 1)' (interop is on: [interop] enabled=false applies at the next start of the distro)"
    else check pass reach.root "a Windows program does not start for root (exit $rc: $(printf '%s' "$out" | head -n 1 | cut -c1-80))"; fi
  fi
  # MU_USER in a user unit, as a job runs: a copy of whoami.exe it can read, so that only interop decides
  if [ -f "$exe" ] && user_manager_up; then
    dir="$STATE_DIR/reach"; install -d -m 755 "$dir"; install -m 755 "$exe" "$dir/whoami.exe"
    rc=0; out="$(as_mu_sd timeout 40 systemd-run --user --quiet --wait --pipe --collect -p RuntimeMaxSec=30 -- "$dir/whoami.exe" 2>&1 < /dev/null)" || rc=$?
    rm -rf "$dir"
    if [ "$rc" = 0 ]; then check fail reach.$MU_USER.interop "a unit of $MU_USER started a Windows program: '$(printf '%s' "$out" | head -n 1)' (interop is on: restart the distro after install)"
    else check pass reach.$MU_USER.interop "a Windows program does not start in a unit of $MU_USER (exit $rc: $(printf '%s' "$out" | head -n 1 | cut -c1-80))"; fi
  fi
  if [ -d "$mnt" ]; then
    if runuser -u "$MU_USER" -- ls "$mnt" > /dev/null 2>&1; then check fail reach.$MU_USER.files "$MU_USER can list $mnt ([automount] options $AUTOMOUNT_OPTIONS apply at the next start of the distro; if they do not hold: [automount] enabled=false, docs/design/job-layer.md §13.4)"
    else check pass reach.$MU_USER.files "$MU_USER cannot open $mnt"; fi
    if runuser -u "$MU_USER" -- df -Pk "$mnt" > /dev/null 2>&1; then check pass reach.$MU_USER.df "$MU_USER can read the free space of $mnt"
    else check fail reach.$MU_USER.df "$MU_USER cannot read the free space of $mnt: μ's disk check would not see the Windows drive"; fi
  fi
  # what μ's own disk check (install/disk.sh, the same block as bin/mu) says, as MU_USER
  if [ -f "$(mu_recipe_of)/install/disk.sh" ] && [ -s "$STATE_DIR/host-drive" ]; then
    drive="$(cut -d' ' -f1 "$STATE_DIR/host-drive")"
    where="$(runuser -u "$MU_USER" -- env -i HOME="$(user_home "$MU_USER")" PATH=/usr/bin:/bin WSL_DISTRO_NAME="${WSL_DISTRO_NAME:-}" \
      bash -c '. "$1"; mu_disk_free "$HOME"; printf "%s|%s" "$MU_FREE_WHERE" "$MU_FREE_NOTE"' _ "$(mu_recipe_of)/install/disk.sh" 2> /dev/null || true)"
    case "$where" in "$drive:"*"|") check pass disk.sure "μ's disk check counts $drive: as sure (${where%|})" ;;
      *) check fail disk.sure "μ's disk check says '$where', want $drive: as sure (a toolchain older than the job layer reads no record)" ;; esac
  fi
}

# ================================================================ job layer: subcommands
need_root() { [ "$(id -u)" = 0 ] || [ -n "${MU_KEYS_DIR:-}" ] || die "run as root: wsl -d ${WSL_DISTRO_NAME:-<distro>} -u root -- mu-wsl2-distro $*"; }
fail_with() {   # fail_with EXIT MESSAGE NEXT: the error (one JSON object with --json) and the exit code
  if [ "$JSON" = 1 ]; then printf '{"ok":false,"exit":%s,"message":%s,"next":%s}\n' "$1" "$(json_str "$2")" "$(json_str "$3")"
  else printf 'mu-wsl2-distro: %s\n' "$2" >&2; [ -z "$3" ] || printf '    next: %s\n' "$3" >&2; fi
  exit "$1"
}

# authorize NAME KEY|-: NAME's workstation key (the line `mu compute add` prints; "-" reads it from stdin) may log in.
# Exit 0 done (or already so), 2 the name or the key refused, 3 the key is authorised under another name, 1 other
cmd_authorize() {
  local name="$1" text="$2" norm err f tmp rc=0 other fp chg=false why
  need_root authorize
  if [ "$text" = - ]; then text="$(head -c 16384)"; fi
  key_name_ok "$name" || fail_with 2 "the name '$name' is not [a-z0-9-], 1 to 32 characters, not starting with '-'" "the workstation's name as mu compute add printed it, e.g. avi-mac"
  err="$(mktemp)"
  if ! norm="$(pubkey_check "$text" 2> "$err")"; then why="$(cat "$err")"; rm -f "$err"; fail_with 2 "not an authorisable key: $why" "copy the whole line 'ssh-ed25519 AAAA… <name>' that mu compute add printed"; fi
  rm -f "$err"
  f="$KEYS_DIR/$MU_USER"
  [ -f "$f" ] || fail_with 1 "$f does not exist" "run mu-wsl2-distro install first"
  tmp="$(mktemp "$f.XXXXXX")"; err="$(mktemp)"
  authkeys_put "$name" "$norm" < "$f" > "$tmp" 2> "$err" || rc=$?
  if [ "$rc" = 3 ]; then other="$(cat "$err")"; rm -f "$tmp" "$err"; fail_with 3 "this key is already authorised as '$other'" "mu-wsl2-distro revoke $other, then authorize it again under the name you want (or keep '$other')"; fi
  rm -f "$err"
  [ "$rc" = 0 ] || { rm -f "$tmp"; fail_with 1 "could not rewrite $f" ""; }
  fp="$(fp_of_line "$norm")"
  if cmp -s "$tmp" "$f"; then rm -f "$tmp"
  else
    chmod 644 "$tmp"; [ "$(id -u)" != 0 ] || chown root:root "$tmp"
    fsync_path "$tmp"; mv -f "$tmp" "$f"; fsync_path "$KEYS_DIR"; chg=true
  fi
  if [ "$JSON" = 1 ]; then printf '{"ok":true,"name":%s,"fingerprint":%s,"changed":%s}\n' "$(json_str "$name")" "$(json_str "$fp")" "$chg"
  else
    if [ "$chg" = true ]; then echo "authorised $name: $fp"; else echo "$name was already authorised with this key: $fp"; fi
    echo "  compare this fingerprint with the one mu compute add printed for $name"
  fi
}

cmd_revoke() {
  local name="$1" f tmp chg=false
  need_root revoke
  key_name_ok "$name" || fail_with 2 "the name '$name' is not [a-z0-9-], 1 to 32 characters" "mu-wsl2-distro keys lists the names"
  f="$KEYS_DIR/$MU_USER"; [ -f "$f" ] || fail_with 1 "$f does not exist" "nothing is authorised (mu-wsl2-distro install makes it)"
  tmp="$(mktemp "$f.XXXXXX")"
  if authkeys_del "$name" < "$f" > "$tmp"; then
    chmod 644 "$tmp"; [ "$(id -u)" != 0 ] || chown root:root "$tmp"; fsync_path "$tmp"; mv -f "$tmp" "$f"; fsync_path "$KEYS_DIR"; chg=true
  else rm -f "$tmp"; fi
  # sshd reads the key file only when a connection logs in: a connection opened before (a ControlMaster can be kept
  # open for as long as its owner likes) would go on opening sessions. The gate refuses a key that is not in the file;
  # and every open connection of the user ends here (the other workstations reconnect by themselves)
  local ended=0
  if [ "$chg" = true ] && [ "$(id -u)" = 0 ] && ! host_fault revoke-keeps-sessions && id -u "$MU_USER" > /dev/null 2>&1; then
    ended="$(pgrep -u "$MU_USER" -x 'sshd|sshd-session' 2> /dev/null | wc -l | tr -d ' ')"
    pkill -TERM -u "$MU_USER" -x 'sshd|sshd-session' 2> /dev/null || true
  fi
  if [ "$JSON" = 1 ]; then printf '{"ok":true,"name":%s,"changed":%s,"connections_ended":%s}\n' "$(json_str "$name")" "$chg" "${ended:-0}"
  elif [ "$chg" = true ]; then echo "revoked $name: its key can no longer log in, and no open connection can start anything ($ended open connection(s) of $MU_USER ended; other workstations reconnect by themselves)"
  else echo "$name was not authorised: nothing changed"; fi
}

cmd_keys() {
  local f n k sep="" count=0 list
  need_root keys
  f="$KEYS_DIR/$MU_USER"; [ -f "$f" ] || fail_with 1 "$f does not exist" "run mu-wsl2-distro install"
  list="$(authkeys_list < "$f")"
  [ "$JSON" = 0 ] || printf '{"ok":true,"keys":['
  if [ -n "$list" ]; then
    while IFS="$(printf '\t')" read -r n k; do
      count=$((count + 1))
      if [ "$JSON" = 1 ]; then printf '%s{"name":%s,"fingerprint":%s,"key":%s}' "$sep" "$(json_str "$n")" "$(json_str "$(fp_of_line "$k")")" "$(json_str "$k")"; sep=","
      else printf '  %-20s %s\n' "$n" "$(fp_of_line "$k")"; fi
    done <<KEYS
$list
KEYS
  fi
  if [ "$JSON" = 1 ]; then printf ']}\n'; elif [ "$count" = 0 ]; then echo "  no workstation is authorised (mu-wsl2-distro authorize <name> '<public key>')"; fi
}

cmd_host_key() {
  local pub fp node url rfp port
  need_root host-key
  [ -s "$NODE_HOSTKEY.pub" ] || fail_with 1 "this node has no host key yet" "run mu-wsl2-distro install"
  pub="$(cut -d' ' -f1,2 "$NODE_HOSTKEY.pub")"; fp="$(hostkey_fp)"
  node="${NODE_NAME:-$(tunnel_user)}"; url="$(tunnel_val RELAY_URL)"; rfp="$(tunnel_val RELAY_FINGERPRINT)"; port="$(tunnel_val RELAY_PORT)"
  if [ "$JSON" = 1 ]; then
    printf '{"ok":true,"public_key":%s,"fingerprint":%s,"node":%s,"relay_url":%s,"relay_fingerprint":%s,"relay_port":%s}\n' \
      "$(json_str "$pub")" "$(json_str "$fp")" "$(json_str "$node")" "$(json_str "$url")" "$(json_str "$rfp")" "$(json_str "$port")"
    return 0
  fi
  echo "The host key of this node (public: it may travel any way; pairing compares its fingerprint):"
  echo "$pub"
  echo "fingerprint $fp"
  echo
  echo "On the workstation, in the mu clone (its own relay user; the password goes through the pipe, never on screen):"
  echo "  relay/gen-credentials.sh client <workstation> 2>/dev/null | sed -n 's/^RELAY_PASSWORD=//p' | mu compute add ${node:-<node>} --host-key '$pub' --relay ${url:-<relay URL>} --relay-fingerprint ${rfp:-<relay fingerprint>} --relay-port ${port:-<port>} --relay-user <workstation> --relay-password-stdin"
  echo "It prints the host key's fingerprint: it must be $fp."
}

tunnel_installed() { id -u "$TUNNEL_USER" > /dev/null 2>&1 && [ -x "$CHISEL_BIN" ] && [ -f "$TUNNEL_UNIT" ]; }
tunnel_hint() {
  case "$1" in
    relay-credential) echo "the relay refused the user or the password: compare with relay/gen-credentials.sh client <node> on the Mac, then mu-wsl2-distro tunnel credential again" ;;
    relay-fingerprint) echo "the relay's key does not match RELAY_FINGERPRINT: relay/gen-credentials.sh fingerprint on the Mac (never skip the check)" ;;
    relay-rule) echo "the relay does not let this user listen on RELAY_PORT: relay/gen-credentials.sh show on the Mac" ;;
    relay-port-busy) echo "the relay still holds this node's previous session, or another client owns the port: wait a minute (the tunnel retries every 15 s)" ;;
    relay-unreachable) echo "the relay cannot be reached from here: curl -sS <RELAY_URL>/health; the network, DNS or a proxy" ;;
    *) echo "journalctl -u mu-tunnel -n 30" ;;
  esac
}

# tunnel status: exit 0 connected, 1 not connected (the reason and what to do), 3 not set up
cmd_tunnel_status() {
  local st state reason line active code=0
  need_root tunnel status
  tunnel_installed || fail_with 3 "the tunnel is not installed" "run mu-wsl2-distro install"
  [ -f "$TUNNEL_ENV" ] || fail_with 3 "the tunnel has no relay credential" "mu-wsl2-distro tunnel credential (paste the lines of relay/gen-credentials.sh client <node>)"
  active="$(systemctl is-active mu-tunnel.service 2> /dev/null || true)"
  st="$(tunnel_journal | tunnel_classify)"; state="$(printf '%s' "$st" | cut -f1)"; reason="$(printf '%s' "$st" | cut -f2)"; line="$(printf '%s' "$st" | cut -f3)"
  if [ "$active" != active ] || [ "$state" != connected ]; then code=1; fi
  if [ "$JSON" = 1 ]; then
    printf '{"ok":%s,"service":%s,"state":%s,"reason":%s,"line":%s,"relay_user":%s,"relay_url":%s,"relay_port":%s,"next":%s}\n' \
      "$([ "$code" = 0 ] && echo true || echo false)" "$(json_str "$active")" "$(json_str "$state")" "$(json_str "$reason")" \
      "$(json_str "$line")" "$(json_str "$(tunnel_user)")" "$(json_str "$(tunnel_val RELAY_URL)")" "$(json_str "$(tunnel_val RELAY_PORT)")" \
      "$(json_str "$([ "$code" = 0 ] || tunnel_hint "$reason")")"
  else
    echo "mu-tunnel.service $active: $state ($reason)"
    [ -z "$line" ] || echo "  last: $line"
    echo "  relay user $(tunnel_user) at $(tunnel_val RELAY_URL), R:127.0.0.1:$(tunnel_val RELAY_PORT) -> 127.0.0.1:$SSHD_PORT"
    [ "$code" = 0 ] || echo "  next: $(tunnel_hint "$reason")"
  fi
  return "$code"
}

# credential_prompt: at a terminal, the relay settings field by field. URL, fingerprint, user and port are public and
# shown; the password is not, and is asked twice. Prints the lines tunnel_env_from reads
credential_prompt() {
  local url fp user port pw pw2
  printf 'The relay settings of this node (relay/gen-credentials.sh client <node> on the Mac shows them).\n' >&2
  printf '  RELAY_URL (e.g. https://relay.martand.systems): ' >&2; IFS= read -r url || return 1
  printf '  RELAY_FINGERPRINT (44 characters, ends with =): ' >&2; IFS= read -r fp || return 1
  printf '  RELAY_USER (this node, e.g. office-pc): ' >&2; IFS= read -r user || return 1
  printf '  RELAY_PORTS (e.g. 7001): ' >&2; IFS= read -r port || return 1
  trap 'stty echo 2> /dev/null || true' EXIT INT TERM
  stty -echo 2> /dev/null || true
  printf '  RELAY_PASSWORD (not shown): ' >&2; IFS= read -r pw || { stty echo 2> /dev/null; return 1; }
  printf '\n  the password again: ' >&2; IFS= read -r pw2 || { stty echo 2> /dev/null; return 1; }
  stty echo 2> /dev/null || true; echo >&2
  if [ "$pw" != "$pw2" ]; then echo "the two passwords differ" >&2; return 1; fi
  printf 'RELAY_URL=%s\nRELAY_FINGERPRINT=%s\nRELAY_ROLE=compute\nRELAY_USER=%s\nRELAY_PASSWORD=%s\nRELAY_PORTS=%s\n' "$url" "$fp" "$user" "$pw" "$port"
}

# tunnel credential: reads the relay settings from stdin (never an argument; not echoed from a terminal), writes
# /etc/mu/tunnel.env (root:mu-tunnel 0640), (re)starts the tunnel and waits up to --wait seconds for it to connect.
# Exit 0 connected, 1 written but not connected (the reason and what to do), 2 input refused (nothing written)
cmd_tunnel_credential() {
  local input="" l envtext tmp st="" state="" reason="" line="" i chg=false
  need_root tunnel credential
  tunnel_installed || fail_with 1 "the tunnel is not installed yet" "run mu-wsl2-distro install first"
  if [ -t 0 ]; then
    input="$(credential_prompt)" || fail_with 2 "the relay settings were not entered; nothing was written" "run it again, or feed the lines of relay/gen-credentials.sh client <node> on stdin"
  else input="$(head -c 65536)"; fi
  envtext="$(printf '%s\n' "$input" | tunnel_env_from)" || fail_with 2 "the relay settings were refused (above); nothing was written" "give the six lines of relay/gen-credentials.sh client <node> (RELAY_URL to RELAY_PORTS) on stdin"
  install -d -m 755 -o root -g root "$ETC_MU"
  tmp="$(mktemp "$TUNNEL_ENV.XXXXXX")"   # 0600 from mktemp; root:mu-tunnel 0640 before it takes the name
  printf '%s\n' "$envtext" > "$tmp"; chown "root:$TUNNEL_USER" "$tmp"; chmod 640 "$tmp"
  if [ -f "$TUNNEL_ENV" ] && cmp -s "$tmp" "$TUNNEL_ENV"; then rm -f "$tmp"; fix_owner_mode "$TUNNEL_ENV" "root:$TUNNEL_USER" 640 > /dev/null
  else fsync_path "$tmp"; mv -f "$tmp" "$TUNNEL_ENV"; fsync_path "$ETC_MU"; chg=true; TUNNEL_RESTART=1; fi
  egress_refresh > /dev/null || true
  tunnel_enable_start > /dev/null
  # the runner, if enabled, takes the relay user as the node's name (install ran before there was a credential)
  if [ -z "$NODE_NAME" ] && mu_job_layer && user_manager_up; then ensure_node_enabled > /dev/null 2>&1 || true; fi
  if systemd_running; then
    for i in $(seq 1 "$WAIT"); do
      st="$(tunnel_journal | tunnel_classify)"; state="$(printf '%s' "$st" | cut -f1)"
      case "$state" in connected|refused) break ;; esac
      sleep 1
    done
    reason="$(printf '%s' "$st" | cut -f2)"; line="$(printf '%s' "$st" | cut -f3)"
  fi
  if [ "$JSON" = 1 ]; then
    printf '{"ok":%s,"changed":%s,"state":%s,"reason":%s,"line":%s,"relay_user":%s,"relay_url":%s,"relay_port":%s,"next":%s}\n' \
      "$([ "$state" = connected ] && echo true || echo false)" "$chg" "$(json_str "$state")" "$(json_str "$reason")" "$(json_str "$line")" \
      "$(json_str "$(tunnel_user)")" "$(json_str "$(tunnel_val RELAY_URL)")" "$(json_str "$(tunnel_val RELAY_PORT)")" \
      "$(json_str "$([ "$state" = connected ] || tunnel_hint "$reason")")"
  else
    echo "$TUNNEL_ENV $([ "$chg" = true ] && echo written || echo unchanged) (root:$TUNNEL_USER 0640): relay user $(tunnel_user), $(tunnel_val RELAY_URL), port $(tunnel_val RELAY_PORT)"
    echo "mu-tunnel.service: ${state:-?} (${reason:-?})${line:+: $line}"
    [ "$state" = connected ] || echo "  next: $(tunnel_hint "$reason")"
  fi
  [ "$state" = connected ]
}

# tunnel refresh: the relay's addresses again (mu-tunnel-refresh.timer, hourly); restarts the tunnel when they changed.
# Exit 0 done, 1 the name does not resolve (nothing changed), 3 no credential yet
cmd_tunnel_refresh() {
  need_root tunnel refresh
  [ -f "$TUNNEL_ENV" ] || fail_with 3 "the tunnel has no relay credential" "mu-wsl2-distro tunnel credential"
  local rc=0 out restarted=false t; t="$(mktemp)"
  egress_refresh > "$t" 2>&1 || rc=$?   # (not in a $(...): TUNNEL_RESTART must reach this shell)
  out="$(cat "$t")"; rm -f "$t"
  if [ "$TUNNEL_RESTART" = 1 ] && systemd_running && systemctl is-active --quiet mu-tunnel.service; then
    systemctl restart mu-tunnel.service; restarted=true
  fi
  if [ "$JSON" = 1 ]; then printf '{"ok":%s,"restarted":%s,"relay":%s,"allow":%s}\n' "$([ "$rc" = 0 ] && echo true || echo false)" "$restarted" \
      "$(json_str "$(relay_host)")" "$(json_str "$(sed -n 's/^IPAddressAllow=//p' "$TUNNEL_EGRESS" 2> /dev/null)")"
  else printf '%s\n' "$out"; [ "$restarted" = false ] || echo "  changed  mu-tunnel.service restarted (new egress filter)"; fi
  return "$rc"
}

# windows-reach: what a process of this distro can reach of Windows now (interop, the drive). Changes nothing.
# Exit 0 closed, 1 some way open (FAIL lines), 3 not WSL
cmd_windows_reach() {
  local nfail
  need_root windows-reach
  is_wsl || fail_with 3 "this is not WSL: nothing to check" ""
  CHECKS="$(mktemp)"; trap 'rm -f "$CHECKS"' EXIT
  status_reach
  nfail="$(awk -F'\t' '$1 == "fail"' "$CHECKS" | wc -l | tr -d ' ')"
  if [ "$JSON" = 1 ]; then printf '{"ok":%s,"checks":' "$([ "$nfail" = 0 ] && echo true || echo false)"; checks_json; printf '}\n'
  else checks_text; if [ "$nfail" = 0 ]; then echo "windows-reach: closed"; else echo "windows-reach: $nfail way(s) open (FAIL above)"; fi; fi
  [ "$nfail" = 0 ]
}

# pair claim|finish|run|abort|status: the node's side of pairing by one code (docs/design/pairing.md §9.3). The code
# comes on stdin (a terminal: asked without echo); mu-pair.py calls this script back for the credential, the key and the
# tunnel's state. Exit codes are mu-pair.py's: 0 paired, 2 typo or usage, 4 unreachable, 5 refused, 9 expired
cmd_pair() {
  local ver py=/usr/bin/python3 a
  need_root pair
  [ -f "$PAIR_PY" ] || fail_with 1 "the pairing client $PAIR_PY is missing" "run mu-wsl2-distro install (from the release that has pairing)"
  [ -x "$py" ] || py="$(command -v python3 || true)"
  [ -n "$py" ] || fail_with 1 "python3 is missing" "apt-get install python3-minimal"
  ver="$(head -n 1 "$(mu_home_of)/default" 2> /dev/null || true)"
  case "$ver" in ''|*[!A-Za-z0-9._-]*) ver=unknown ;; esac
  set -- "$ARG1" --relay "$RELAY_URL_ARG" --mu-version "$ver"
  [ "$JSON" = 0 ] || set -- "$@" --json
  # a clean environment: nothing of the caller's reaches root's Python but the test hooks named here
  exec env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin LANG=C.UTF-8 ${MU_PAIR_FAULT:+MU_PAIR_FAULT=$MU_PAIR_FAULT} \
    ${MU_PAIR_CONFIRM_WAIT:+MU_PAIR_CONFIRM_WAIT=$MU_PAIR_CONFIRM_WAIT} "$py" -I -S "$PAIR_PY" "$@"
}

# ================================================================ main
usage() { sed -n '2,/^set -euo/p' "$SELF_SRC" | sed '$d' | sed 's/^# \{0,1\}//'; }
main() {
  local action="${1:-}"; [ $# -gt 0 ] && shift
  case "$action" in
    install|status|uninstall|keys|host-key|windows-reach) ;;
    authorize) [ $# -ge 2 ] || { echo "usage: mu-wsl2-distro authorize NAME 'ssh-ed25519 AAAA… NAME' (or - to read the key from stdin)" >&2; exit 2; }
               ARG1="$1"; ARG2="$2"; shift 2 ;;
    revoke) [ $# -ge 1 ] || { echo "usage: mu-wsl2-distro revoke NAME" >&2; exit 2; }; ARG1="$1"; shift ;;
    tunnel) case "${1:-}" in status|credential|refresh) ARG1="$1"; shift ;; *) echo "usage: mu-wsl2-distro tunnel status|credential|refresh" >&2; exit 2 ;; esac ;;
    tunnel-credential) action=tunnel; ARG1=credential ;;
    pair) case "${1:-}" in claim|finish|run|abort|status) ARG1="$1"; shift ;; *) echo "usage: mu-wsl2-distro pair claim|finish|run|abort|status [--relay URL] [--json] (the code on stdin)" >&2; exit 2 ;; esac ;;
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
      --no-wsl) NO_WSL=1; shift ;;
      --host-drive) HOST_DRIVE="${2:-}"; shift 2 ;;
      --node-name) NODE_NAME="${2:-}"; shift 2 ;;
      --wait) WAIT="${2:-}"; shift 2 ;;
      --from-host) FROM_HOST=1; shift ;;
      --relay) RELAY_URL_ARG="${2:-}"; shift 2 ;;
      *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
  done
  case "$MU_USER" in ""|root|-*|*[!a-z0-9_-]*) echo "--user: invalid user name '$MU_USER'" >&2; exit 2 ;; esac
  case "$HEARTBEAT" in ""|on|off) ;; *) echo "--heartbeat takes on or off" >&2; exit 2 ;; esac
  case "$GAP" in ""|*[!0-9]*) echo "--gap takes a number of seconds" >&2; exit 2 ;; esac
  case "$WAIT" in ""|*[!0-9]*) echo "--wait takes a number of seconds" >&2; exit 2 ;; esac
  case "$HOST_DRIVE" in ""|[A-Za-z]) ;; *) echo "--host-drive takes one drive letter, e.g. C" >&2; exit 2 ;; esac
  [ -z "$NODE_NAME" ] || key_name_ok "$NODE_NAME" || { echo "--node-name: [a-z0-9-], 1 to 32 characters" >&2; exit 2; }
  cd /   # runuser'd commands (git) must be able to read the working directory
  case "$action" in
    install) do_install ;;
    status) do_status ;;
    uninstall) do_uninstall ;;
    authorize) cmd_authorize "$ARG1" "$ARG2" ;;
    revoke) cmd_revoke "$ARG1" ;;
    keys) cmd_keys ;;
    host-key) cmd_host_key ;;
    tunnel) case "$ARG1" in status) cmd_tunnel_status ;; refresh) cmd_tunnel_refresh ;; *) cmd_tunnel_credential ;; esac ;;
    windows-reach) cmd_windows_reach ;;
    pair) cmd_pair ;;
    heartbeat)
      [ "$(id -u)" = 0 ] || die "run as root"
      case "$HEARTBEAT" in on) heartbeat_on ;; off) heartbeat_off ;; *) echo "heartbeat on|off" >&2; exit 2 ;; esac ;;
  esac
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then main "$@"; fi
