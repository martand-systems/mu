#!/bin/bash
# Tests for the WSL2 host adapter (install/hosts/wsl2/): the Linux script's pure functions (wsl.conf merge, heartbeat log
# analysis, GitHub host key parsing, ssh config merge, JSON strings, clean-stop markers; for the job layer the sshd
# configuration and the check of its effective values, key validation, authorize / revoke / keys, the relay settings,
# the tunnel's log, listeners, the security pocket), its syntax, and, when pwsh is installed, the Windows script
# (PowerShell 5.1 syntax check, .wslconfig merge, argument quoting, task XML, the host drive letter). Checks guarded by
# a fault hook (MU_HOST_FAULT) run again with the fault and must then fail ("must-fail").
#
#   tools/test-wsl2-host/run.sh [BASH ...]      default: /bin/bash plus any other bash found (e.g. Homebrew's bash 5)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/../.." && pwd)"
SH="$REPO/install/hosts/wsl2/mu-wsl2-distro.sh"
PS1="$REPO/install/hosts/wsl2/mu-wsl2-host.ps1"
TD="$(mktemp -d "${TMPDIR:-/tmp}/mu test wsl2 host.XXXXXX")"   # a space in the path on purpose
trap 'rm -rf "$TD"' EXIT
if [ $# -gt 0 ]; then BASHES="$*"; else
  BASHES="/bin/bash"; seen="$(/bin/bash -c 'echo $BASH_VERSION')"
  for b in /opt/homebrew/bin/bash /usr/local/bin/bash $(command -v -a bash 2>/dev/null || true); do
    [ -x "$b" ] || continue; v="$("$b" -c 'echo $BASH_VERSION')"
    case " $seen " in *" $v "*) ;; *) BASHES="$BASHES $b"; seen="$seen $v" ;; esac
  done
fi
PASS=0; FAILN=0
ok() { PASS=$((PASS + 1)); echo "ok    $1"; }
bad() { FAILN=$((FAILN + 1)); echo "FAIL  $1"; [ -z "${2:-}" ] || printf '%s\n' "$2" | sed 's/^/      /'; }

# ---- fixtures
nl() { printf '%s\n' "$@"; }
nl '[boot]' 'systemd=true' '' '[user]' 'default=ak' '' '# keep me' '[network]' 'hostname=x' > "$TD/wsl.conf.ak"
nl '[boot]' 'systemd=true' '' '[user]' 'default=mu' '' '# keep me' '[network]' 'hostname=x' '' '[interop]' 'appendWindowsPath=false' > "$TD/wsl.conf.ak.want"
printf '[Boot]\r\nSystemd = true\r\n' > "$TD/wsl.conf.crlf"
printf '[boot]\nsystemd=false' > "$TD/wsl.conf.nonl"   # no newline at the end
: > "$TD/empty"
nl '[boot]' 'systemd=true' '' '[user]' 'default=mu' '' '[interop]' 'appendWindowsPath=false' > "$TD/wsl.conf.fresh.want"
nl '[user]' 'default=mu' '[boot]' 'command=echo hi' '' '' '[x]' > "$TD/wsl.conf.tail"
nl '[user]' 'default=mu' '[boot]' 'command=echo hi' 'systemd=true' '' '' '[x]' > "$TD/wsl.conf.tail.want"
# the 21-NUL line seen on the office PC: the line written 30 s before the restart came back as NULs (its newline too),
# then the first line after the restart was appended to it
{ printf '2026-11-13T09:18:03Z\n2026-11-13T09:19:03Z\n'; head -c 21 /dev/zero; printf '2026-11-13T09:21:15Z\n2026-11-13T09:22:15Z\n'; } > "$TD/hb.nul"
{ for m in 10 11 12 13 14; do printf '2026-11-13T08:%s:00Z\n' "$m"; done; } > "$TD/hb.ok"
nl 2026-11-13T08:10:00Z 2026-11-13T08:11:00Z 2026-11-13T08:16:30Z 2026-11-13T08:17:30Z > "$TD/hb.gap"
nl 2026-12-31T23:59:30Z 2027-01-01T00:00:30Z 2027-01-01T00:00:20Z 2026-11-1 > "$TD/hb.odd"
{ printf '2026-11-13T09:19:03Z\n'; head -c 21 /dev/zero; } > "$TD/hb.nultail"
printf '{"verifiable_password_authentication":false,"ssh_key_fingerprints":{"SHA256_ED25519":"+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU"},\n"ssh_keys":["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl","ecdsa-sha2-nistp256 AAAAE2VjZHNh","ssh-rsa AAAAB3NzaC1yc2E="],"hooks":["192.30.252.0/22"]}' > "$TD/meta.json"
printf '{"ssh_keys":["ssh-rsa AAAAB3NzaC1yc2E="]}' > "$TD/meta-noed.json"
nl '# mine' 'Host build' '  HostName 10.0.0.2' > "$TD/ssh.config"
nl 'ServerAliveInterval 60' > "$TD/ssh.global"
nl '#!/bin/bash' '#   --system               Linux: only install' '#   --user NAME            with --system: let NAME' 'set -Eeuo pipefail' > "$TD/install-release.sh"
nl '#!/bin/bash' '#   --system               Linux: only install' 'set -Eeuo pipefail' > "$TD/install-main.sh"
nl '[boot]' 'systemd=false' 'systemd=true' > "$TD/wsl.conf.dup"
# git's stderr as it really reads (git 2.43, OpenSSH 9.6): the cause first, then the same fixed trailer every time
nl 'git@github.com: Permission denied (publickey).' 'fatal: Could not read from remote repository.' '' \
  'Please make sure you have the correct access rights' 'and the repository exists.' > "$TD/git.denied"
nl 'ERROR: Repository not found.' 'fatal: Could not read from remote repository.' '' \
  'Please make sure you have the correct access rights' 'and the repository exists.' > "$TD/git.notfound"
nl 'ssh: Could not resolve hostname github.com: Temporary failure in name resolution' 'fatal: Could not read from remote repository.' '' \
  'Please make sure you have the correct access rights' 'and the repository exists.' > "$TD/git.dns"
nl 'Host key verification failed.' 'fatal: Could not read from remote repository.' '' \
  'Please make sure you have the correct access rights' 'and the repository exists.' > "$TD/git.hostkey"

# the job layer's fixtures: wsl.conf with the Windows reach, keys made here, the relay settings, ss and apt-config output
nl '[boot]' 'systemd=true' '' '[user]' 'default=mu' '' '# keep me' '[network]' 'hostname=x' '' '[interop]' 'appendWindowsPath=false' \
  'enabled=false' '' '[automount]' 'enabled=true' 'options="uid=0,gid=0,umask=077"' > "$TD/wsl.conf.reach.want"
nl '[boot]' 'systemd = true' '[user]' 'default = mu' '[interop]' 'appendWindowsPath = false' 'enabled = false' '[automount]' \
  'enabled = true' 'options = "uid=0,gid=0,umask=077"' > "$TD/wsl.conf.handopts"
ssh-keygen -q -t ed25519 -N '' -C avi-mac -f "$TD/ws" < /dev/null
ssh-keygen -q -t ed25519 -N '' -C build-box -f "$TD/other" < /dev/null
ssh-keygen -q -t ecdsa -N '' -C ec -f "$TD/ec" < /dev/null
WSPUB="$(cat "$TD/ws.pub")"; WSPUB2="$(cut -d' ' -f1,2 "$TD/ws.pub")"; WSFP="$(ssh-keygen -lf "$TD/ws.pub" | awk '{ print $2 }')"
OTHERPUB="$(cat "$TD/other.pub")"; OTHERPUB2="$(cut -d' ' -f1,2 "$TD/other.pub")"; OTHERFP="$(ssh-keygen -lf "$TD/other.pub" | awk '{ print $2 }')"
ECPUB="$(cat "$TD/ec.pub")"
ssh-keygen -q -t ed25519 -N '' -C new -f "$TD/new" < /dev/null; NEWPUB2="$(cut -d' ' -f1,2 "$TD/new.pub")"
CUTPUB="ssh-ed25519 $(cut -d' ' -f2 "$TD/ws.pub" | cut -c1-60) avi-mac"
RPW=0123456789abcdef0123456789abcdef0123456789abcdef; RFP=ubILCH8awo2GQII9Z9eLRD+LOo4qXabtMrJjCleonuI=
printf 'RELAY_URL=https://relay.martand.systems\r\nRELAY_FINGERPRINT=%s\r\nRELAY_ROLE=compute\r\nRELAY_USER=office-pc\r\nRELAY_PASSWORD=%s\r\nRELAY_PORTS=7001\r\n\r\n# chisel client for office-pc (keep --fingerprint; never use --tls-skip-verify):\r\n#   AUTH='"'"'office-pc:<RELAY_PASSWORD>'"'"' CHISEL_WS_TIMEOUT=10s chisel client ...\r\n' "$RFP" "$RPW" > "$TD/gen.out"
printf "export AUTH='office-pc:%s'\nRELAY_URL=\"https://relay.martand.systems/\"\nRELAY_FINGERPRINT=%s\nRELAY_PORT=7001\n" "$RPW" "$RFP" > "$TD/gen.auth"
printf 'AUTH=office-pc:%s\nRELAY_URL=https://relay.martand.systems\nRELAY_FINGERPRINT=%s\nRELAY_PORT=7001\n' "$RPW" "$RFP" > "$TD/tunnel.env.want"
nl 'LISTEN 0      4096   127.0.0.53%lo:53        0.0.0.0:*    users:(("systemd-resolve",pid=5,fd=14))' \
  'LISTEN 0      128        127.0.0.1:2222      0.0.0.0:*    users:(("sshd",pid=7,fd=3))' \
  'LISTEN 0      4096         0.0.0.0:443       0.0.0.0:*    users:(("docker-proxy",pid=8,fd=4))' > "$TD/ss.good"
nl 'LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=9,fd=3))' 'LISTEN 0 128 [::]:22 [::]:* users:(("sshd",pid=9,fd=4))' \
  'LISTEN 0 128 0.0.0.0:2222 0.0.0.0:* users:(("sshd",pid=7,fd=3))' 'LISTEN 0 4096 127.0.0.1:631 0.0.0.0:* users:(("cupsd",pid=3,fd=7))' > "$TD/ss.bad"
nl 'APT::Periodic::Unattended-Upgrade "1";' 'Unattended-Upgrade "";' 'Unattended-Upgrade::Allowed-Origins "";' \
  'Unattended-Upgrade::Allowed-Origins:: "${distro_id}:${distro_codename}-security";' \
  'Unattended-Upgrade::Allowed-Origins:: "${distro_id}ESMApps:${distro_codename}-apps-security";' \
  'Unattended-Upgrade::Allowed-Origins:: "${distro_id}ESM:${distro_codename}-infra-security";' \
  'Unattended-Upgrade::Automatic-Reboot "false";' > "$TD/aptdump.good"
{ cat "$TD/aptdump.good"; echo 'Unattended-Upgrade::Allowed-Origins:: "${distro_id}:${distro_codename}-updates";'; } > "$TD/aptdump.updates"

for B in $BASHES; do
  echo "== $B ($("$B" -c 'echo $BASH_VERSION'))"
  if "$B" -n "$SH"; then ok "bash -n mu-wsl2-distro.sh"; else bad "bash -n mu-wsl2-distro.sh"; fi
  # run FUNCTION-CALL: sources the script (main does not run when sourced) and evaluates the call
  run() { "$B" -c '. "$1"; shift; eval "$*"' _ "$SH" "$@" 2>&1 < /dev/null; }
  # t NAME EXPECTED-FILE CALL
  t() {
    local got; got="$(run "$3")" || got="$got
(exit $?)"
    if [ "$got" = "$(cat "$2")" ]; then ok "$1"
    else bad "$1" "$(diff <(cat "$2") <(printf '%s\n' "$got") || true)"; fi
  }
  # e NAME EXPECTED-STRING CALL
  e() { printf '%s\n' "$2" > "$TD/expect"; t "$1" "$TD/expect" "$3"; }

  W='ini_set boot systemd true | ini_set user default mu | ini_set interop appendWindowsPath false'
  t "wsl.conf: change the default user, add [interop], keep the rest" "$TD/wsl.conf.ak.want" "cat '$TD/wsl.conf.ak' | $W"
  t "wsl.conf: second run changes nothing" "$TD/wsl.conf.ak.want" "cat '$TD/wsl.conf.ak.want' | $W"
  t "wsl.conf: from nothing" "$TD/wsl.conf.fresh.want" "cat '$TD/empty' | $W"
  e "wsl.conf: section and key names are case-insensitive, a matching value is kept as written" \
    "$(printf '[Boot]\r\nSystemd = true\r\n\n[user]\ndefault=mu')" "ini_set boot systemd true < '$TD/wsl.conf.crlf' | ini_set user default mu"
  e "wsl.conf: no newline at the end, value changed" "$(printf '[boot]\nsystemd=true')" "ini_set boot systemd true < '$TD/wsl.conf.nonl'"
  t "wsl.conf: key added after the section's last line, before its blank lines" "$TD/wsl.conf.tail.want" "ini_set boot systemd true < '$TD/wsl.conf.tail'"
  e "ini_get: value, CR stripped" "true" "ini_get boot systemd < '$TD/wsl.conf.crlf'"
  e "ini_get: missing key prints nothing" "" "ini_get interop appendWindowsPath < '$TD/wsl.conf.crlf'"
  e "ini_get: a repeated key gives the first value (what WSL uses)" "false" "ini_get boot systemd < '$TD/wsl.conf.dup'"

  e "heartbeat: continuous log" "$(printf 'lines\t5\nvalid\t5\nnul\t0\nfirst\t2026-11-13T08:10:00Z\nlast\t2026-11-13T08:14:00Z\nlast_epoch\t1794557640')" \
    "hb_analyze 90 < '$TD/hb.ok'"
  e "heartbeat: the office PC's 21 NUL bytes, then the next boot's line on the same line" \
    "$(printf 'nulline\t3\t21\t2026-11-13T09:19:03Z\t2026-11-13T09:21:15Z\ngap\t2026-11-13T09:19:03Z\t2026-11-13T09:21:15Z\t132\nlines\t4\nvalid\t4\nnul\t21\nfirst\t2026-11-13T09:18:03Z\nlast\t2026-11-13T09:22:15Z\nlast_epoch\t1794561735')" \
    "hb_analyze 90 < '$TD/hb.nul'"
  e "heartbeat: NULs at the end of the file (no line after them yet)" \
    "$(printf 'nulline\t2\t21\t2026-11-13T09:19:03Z\t-\nlines\t2\nvalid\t1\nnul\t21\nfirst\t2026-11-13T09:19:03Z\nlast\t2026-11-13T09:19:03Z\nlast_epoch\t1794561543')" \
    "hb_analyze 90 < '$TD/hb.nultail'"
  e "heartbeat: a 5.5 min gap" "$(printf 'gap\t2026-11-13T08:11:00Z\t2026-11-13T08:16:30Z\t330')" "hb_analyze 90 < '$TD/hb.gap' | grep '^gap'"
  e "heartbeat: gap threshold is a parameter" "" "hb_analyze 400 < '$TD/hb.gap' | grep '^gap' || true"
  e "heartbeat: across a year end, backwards time, a torn line" \
    "$(printf 'back\t2027-01-01T00:00:30Z\t2027-01-01T00:00:20Z\nbad\t4\t2026-11-1')" "hb_analyze 90 < '$TD/hb.odd' | grep -E '^(gap|back|bad)'"
  e "heartbeat: epoch arithmetic (1970, leap day 2024, 2026-11-13)" "0 1709164800 1794561603" \
    "for t in 1970-01-01T00:00:00Z 2024-02-29T00:00:00Z 2026-11-13T09:20:03Z; do printf '%s\n' \$t | hb_analyze 0 | awk -F'\t' '\$1==\"last_epoch\"{printf \"%s \", \$2}'; done | sed 's/ \$//'"
  e "heartbeat: NULs from a stop before the distro's start are old, from the last stop current" \
    "$(printf 'nulold\t3\t21\t2026-11-13T09:19:03Z\t2026-11-13T09:21:15Z')" "hb_analyze 90 1794561700 < '$TD/hb.nul' | grep -E '^nul(line|old)'"
  e "heartbeat: the start is the very second of the next line: current" \
    "$(printf 'nulline\t3\t21\t2026-11-13T09:19:03Z\t2026-11-13T09:21:15Z')" "hb_analyze 90 1794561675 < '$TD/hb.nul' | grep -E '^nul(line|old)'"
  e "heartbeat: NULs with no line after them are always current" \
    "$(printf 'nulline\t2\t21\t2026-11-13T09:19:03Z\t-')" "hb_analyze 90 1999999999 < '$TD/hb.nultail' | grep -E '^nul(line|old)'"
  e "heartbeat: empty log" "$(printf 'lines\t0\nvalid\t0\nnul\t0')" "hb_analyze 90 < '$TD/empty'"

  e "GitHub meta: the ed25519 host key" "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl" "gh_meta_ed25519 < '$TD/meta.json'"
  e "GitHub meta: no ed25519 key prints nothing" "" "gh_meta_ed25519 < '$TD/meta-noed.json'"

  printf '%s\n' "# >>> mu-wsl2: GitHub access for mu (managed by mu-wsl2-distro; edits inside this block are replaced)" "Host github.com" \
    "  HostName github.com" "  User git" "  IdentityFile ~/.ssh/mu_github" "  IdentitiesOnly yes" "  HostKeyAlgorithms ssh-ed25519" \
    "  StrictHostKeyChecking yes" "Host *" "# <<< mu-wsl2" > "$TD/block"
  { cat "$TD/block"; echo; cat "$TD/ssh.config"; } > "$TD/ssh.config.want"
  t "ssh config: managed block first, the rest kept" "$TD/ssh.config.want" "ssh_config_merge '~/.ssh/mu_github' < '$TD/ssh.config'"
  t "ssh config: second run changes nothing" "$TD/ssh.config.want" "ssh_config_merge '~/.ssh/mu_github' < '$TD/ssh.config.want'"
  t "ssh config: from nothing" "$TD/block" "ssh_config_merge '~/.ssh/mu_github' < '$TD/empty'"
  { cat "$TD/block"; echo; cat "$TD/ssh.global"; } > "$TD/ssh.global.want"
  t "ssh config: global options stay global (after Host *)" "$TD/ssh.global.want" "ssh_config_merge '~/.ssh/mu_github' < '$TD/ssh.global'"

  e "git error: a missing deploy key is denied" "denied: git@github.com: Permission denied (publickey)." "git_error_class < '$TD/git.denied'"
  e "git error: no access to this repo is denied" "denied: ERROR: Repository not found." "git_error_class < '$TD/git.notfound'"
  e "git error: no DNS is unreachable" "unreachable: ssh: Could not resolve hostname github.com: Temporary failure in name resolution" "git_error_class < '$TD/git.dns'"
  e "git error: a host key mismatch is not a missing key" "unreachable: Host key verification failed." "git_error_class < '$TD/git.hostkey'"
  e "git error: nothing on stderr (killed by timeout)" "unreachable: timed out" "git_error_class < '$TD/empty'"
  e "self path: resolved before main's cd /, also when run by a relative path" "$SH" \
    "cd '$(dirname "$SH")' && . ./mu-wsl2-distro.sh && cd / && printf '%s\n' \"\$SELF_SRC\""
  e "repo slug: ssh URL" "martand-systems/mu" "repo_slug git@github.com:martand-systems/mu.git"
  e "repo slug: https URL" "martand-systems/mu" "repo_slug https://github.com/martand-systems/mu"
  e "repo slug: not GitHub" "" "repo_slug git@example.com:a/b.git"
  e "install.sh with --user (release)" "yes" "installsh_supports_user '$TD/install-release.sh' && echo yes || echo no"
  e "install.sh without --user (main)" "no" "installsh_supports_user '$TD/install-main.sh' && echo yes || echo no"
  e "install.sh missing" "no" "installsh_supports_user '$TD/nonexistent' && echo yes || echo no"
  e "json_str: quotes, backslash, tab, newline, control bytes" '"a\"b\\c\td\ne"' "json_str \"\$(printf 'a\"b\\\\c\td\ne\001')\""
  e "json_str: UTF-8 passes through" '"μ ok"' "json_str 'μ ok'"

  rm -rf "$TD/state"
  e "clean-stop marker: first start" "previous-stop=first" "marker_boot '$TD/state'; cut -f3 '$TD/state/stops.log'"
  e "clean-stop marker: start after a clean stop" "previous-stop=clean" \
    "marker_stop '$TD/state'; [ -f '$TD/state/clean-stop' ] && marker_boot '$TD/state'; [ ! -e '$TD/state/clean-stop' ] && tail -n 1 '$TD/state/stops.log' | cut -f3 | cut -d' ' -f1"
  e "clean-stop marker: start after a hard stop" "previous-stop=unclean" "marker_boot '$TD/state'; tail -n 1 '$TD/state/stops.log' | cut -f3"
  e "usage: --help prints the header" "  mu-wsl2-distro install [options]      set the distro up (idempotent: a second run changes nothing and says so)" \
    "main --help | sed -n '5p'"
  e "usage: unknown option" "unknown option: --nope (see --help)
(exit 2)" "main status --nope"
  e "usage: bad --user" "--user: invalid user name 'Root;'
(exit 2)" "main status --user 'Root;'"

  # ---- the job layer (docs/design/job-layer.md §13). mf: the same check with a fault put in must FAIL
  mf() {   # mf NAME FAULT EXPECTED CALL: with MU_HOST_FAULT=FAULT the call must NOT give EXPECTED
    local got; got="$(run "MU_HOST_FAULT='$2'; $4")" || got="$got
(exit $?)"
    if [ "$got" = "$3" ]; then bad "must-fail: $1 (with fault $2 it still passes: the test does not test it)"
    else ok "must-fail: $1 (fault $2 makes it fail)"; fi
  }
  t "wsl.conf: install's merge with the Windows reach (interop off, drives for root)" "$TD/wsl.conf.reach.want" "wsl_conf_merge mu 0 < '$TD/wsl.conf.ak'"
  t "wsl.conf: that merge a second time changes nothing" "$TD/wsl.conf.reach.want" "wsl_conf_merge mu 0 < '$TD/wsl.conf.reach.want'"
  t "wsl.conf: --no-wsl merges only the first three keys" "$TD/wsl.conf.ak.want" "wsl_conf_merge mu 1 < '$TD/wsl.conf.ak'"
  e "wsl.conf: a hand-written matching options line is kept as written" "$(cat "$TD/wsl.conf.handopts")" "wsl_conf_merge mu 0 < '$TD/wsl.conf.handopts'"

  # sshd: the configuration and the check of the values sshd says it uses
  run "sshd_config_text 2222 mu /etc/mu/ssh_host_ed25519_key '/etc/mu/authorized_keys/%u' /usr/local/lib/mu/mu-node-gate" > "$TD/sshd.cfg"
  e "sshd config: §13.1 line for line (36 settings, no Subsystem, no AcceptEnv, no Include)" "36 0" \
    "sshd_config_text 2222 mu /k '/a/%u' /g | grep -c '^[A-Z]' | tr '\n' ' '; sshd_config_text 2222 mu /k /a /g | grep -ciE '^(subsystem|acceptenv|include|match)' || true"
  awk '/^[A-Z]/ { k = tolower($1); v = $2; for (i = 3; i <= NF; i++) v = v " " $i; if (k == "listenaddress") v = v ":2222"; print k " " v }' "$TD/sshd.cfg" > "$TD/sshdT.good"
  printf '%s\n' "x11uselocalhost yes" "permitlisten any" >> "$TD/sshdT.good"   # sshd -T prints more keys than are set
  sed 's/^permittty no$/permittty yes/' "$TD/sshdT.good" > "$TD/sshdT.tty"
  { cat "$TD/sshdT.good"; echo "subsystem sftp /usr/lib/openssh/sftp-server"; } > "$TD/sshdT.sftp"
  grep -v '^forcecommand ' "$TD/sshdT.good" > "$TD/sshdT.noforce"
  { cat "$TD/sshdT.good"; echo "hostkey /etc/ssh/ssh_host_rsa_key"; } > "$TD/sshdT.2keys"
  e "sshd effective values: all as written" "(exit 0)" "sshd_effective_check '$TD/sshd.cfg' < '$TD/sshdT.good'; echo \"(exit \$?)\""
  e "sshd effective values: PermitTTY yes is caught" "$(printf 'permittty\tno\tyes\n(exit 1)')" "sshd_effective_check '$TD/sshd.cfg' < '$TD/sshdT.tty'; echo \"(exit \$?)\""
  e "sshd effective values: a Subsystem (sftp) is caught" "$(printf 'subsystem\t(none)\tsftp /usr/lib/openssh/sftp-server\n(exit 1)')" "sshd_effective_check '$TD/sshd.cfg' < '$TD/sshdT.sftp'; echo \"(exit \$?)\""
  e "sshd effective values: no ForceCommand is caught" "$(printf 'forcecommand\t/usr/local/lib/mu/mu-node-gate\t(not reported)\n(exit 1)')" "sshd_effective_check '$TD/sshd.cfg' < '$TD/sshdT.noforce'; echo \"(exit \$?)\""
  e "sshd effective values: a second host key is caught" "$(printf 'hostkey\t/etc/mu/ssh_host_ed25519_key\t/etc/mu/ssh_host_ed25519_key,/etc/ssh/ssh_host_rsa_key\n(exit 1)')" "sshd_effective_check '$TD/sshd.cfg' < '$TD/sshdT.2keys'; echo \"(exit \$?)\""
  mf "sshd effective values catch PermitTTY yes" sshd-no-effective-check "$(printf 'permittty\tno\tyes\n(exit 1)')" "sshd_effective_check '$TD/sshd.cfg' < '$TD/sshdT.tty'; echo \"(exit \$?)\""
  # the real sshd, as this user, on a copy with this machine's paths (macOS and the Linux runner)
  if [ -x /usr/sbin/sshd ] && [ "$B" = /bin/bash ]; then
    HK="$(mktemp -d "${TMPDIR:-/tmp}/muhk.XXXXXX")"   # HostKey takes no spaces (the node's path has none)
    ssh-keygen -q -t ed25519 -N '' -f "$HK/hk" < /dev/null
    run "sshd_config_text 2222 '$(id -un)' '$HK/hk' '/etc/mu/authorized_keys/%u' /usr/local/lib/mu/mu-node-gate" > "$TD/sshd.real"
    if /usr/sbin/sshd -T -f "$TD/sshd.real" -C "user=$(id -un),host=localhost,addr=127.0.0.1" > "$TD/sshdT.real" 2> "$TD/sshdT.err"; then
      e "sshd -T of the generated configuration (this machine's OpenSSH): every value in effect" "(exit 0)" "sshd_effective_check '$TD/sshd.real' < '$TD/sshdT.real'; echo \"(exit \$?)\""
      sed 's/^PermitTTY no$/PermitTTY yes/' "$TD/sshd.real" > "$TD/sshd.real.tty"
      /usr/sbin/sshd -T -f "$TD/sshd.real.tty" -C "user=$(id -un),host=localhost,addr=127.0.0.1" > "$TD/sshdT.real.tty" 2>&1 || true
      e "sshd -T of an edited copy (PermitTTY yes): caught" "$(printf 'permittty\tno\tyes\n(exit 1)')" "sshd_effective_check '$TD/sshd.real' < '$TD/sshdT.real.tty'; echo \"(exit \$?)\""
    else echo "skip  sshd -T as $(id -un) here: $(head -n 1 "$TD/sshdT.err")"; fi
    rm -rf "$HK"
  fi

  # workstation keys: validation (ssh-keygen decides), the key file's lines
  e "key name: avi-mac ok; bad names refused" "ok no no no no no" \
    "for n in avi-mac Avi -x 'a b' '' aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; do key_name_ok \"\$n\" && printf 'ok ' || printf 'no '; done | sed 's/ \$//'"
  mf "key name refuses upper case" no-name-check "no" "key_name_ok Avi && echo ok || echo no"
  e "public key: the line mu compute add prints (with its comment) becomes type and key" "$WSPUB2" "pubkey_check '$WSPUB'"
  e "public key: CRLF and surrounding blank lines are fine" "$WSPUB2" "pubkey_check \"\$(printf '\r\n$WSPUB\r\n\n')\""
  e "public key: an ECDSA key is refused" "not an ed25519 public key (it starts with 'ecdsa-sha2-nistp256'): mu compute add prints one
(exit 1)" "pubkey_check '$ECPUB'"
  e "public key: two keys are refused" "expected one public key line, got 2 lines
(exit 1)" "pubkey_check \"\$(printf '%s\n%s\n' '$WSPUB' '$OTHERPUB')\""
  e "public key: a key cut short is refused (ssh-keygen)" "ssh-keygen does not read it as a public key (damaged or cut short?)
(exit 1)" "pubkey_check '$CUTPUB'"
  e "public key: a private key's first line is refused" "not an ed25519 public key (it starts with '-----BEGIN'): mu compute add prints one
(exit 1)" "pubkey_check '-----BEGIN OPENSSH PRIVATE KEY-----'"
  e "public key: shell characters in the key are refused" "the key's second field is not base64
(exit 1)" "pubkey_check 'ssh-ed25519 AAAA\$(id) x'"
  mf "a key cut short is refused" no-keygen-check "ssh-keygen does not read it as a public key (damaged or cut short?)
(exit 1)" "pubkey_check '$CUTPUB'"
  e "key file: the first key, with restrict, under its name" "restrict $WSPUB2 avi-mac" "authkeys_put avi-mac '$WSPUB2' < '$TD/empty'"
  printf '%s\n' "restrict $OTHERPUB2 build-box" "restrict $WSPUB2 avi-mac" > "$TD/keys.two"
  e "key file: a new key for an existing name replaces its line in place" "$(printf 'restrict %s build-box\nrestrict %s avi-mac' "$OTHERPUB2" "$NEWPUB2")" \
    "authkeys_put avi-mac '$NEWPUB2' < '$TD/keys.two'"
  e "key file: the same key under a second name is refused, naming the first" "build-box
(exit 3)" "authkeys_put laptop '$OTHERPUB2' < '$TD/keys.two' 2>&1 > /dev/null"
  e "key file: authorising the same name and key again changes nothing" "$(cat "$TD/keys.two")" "authkeys_put avi-mac '$WSPUB2' < '$TD/keys.two'"
  e "key file: revoke removes only that name" "restrict $WSPUB2 avi-mac" "authkeys_del build-box < '$TD/keys.two'"
  e "key file: revoke of a name that is not there says so" "(exit 1)" "set +e; authkeys_del nobody < '$TD/keys.two' > /dev/null; echo \"(exit \$?)\""
  e "key file: list names and keys" "$(printf 'build-box\t%s\navi-mac\t%s' "$OTHERPUB2" "$WSPUB2")" "authkeys_list < '$TD/keys.two'"
  # the subcommands, on a key folder of this test (MU_KEYS_DIR: the test hook that also lets them run without root)
  rm -rf "$TD/keys"; mkdir -p "$TD/keys"; : > "$TD/keys/mu"
  K="MU_KEYS_DIR='$TD/keys'; KEYS_DIR='$TD/keys';"
  e "authorize: writes the line, prints the fingerprint" "authorised avi-mac: $WSFP" "$K main authorize avi-mac '$WSPUB' | head -n 1"
  e "authorize: the key file is restrict + key + name, mode 0644" "restrict $WSPUB2 avi-mac -rw-r--r--" "cat '$TD/keys/mu' | tr '\n' ' '; ls -l '$TD/keys/mu' | cut -c1-10"
  e "authorize: again: nothing changes" '{"ok":true,"name":"avi-mac","fingerprint":"'"$WSFP"'","changed":false}' "$K main authorize avi-mac '$WSPUB' --json"
  e "authorize: the key from stdin (-)" '{"ok":true,"name":"build-box","fingerprint":"'"$OTHERFP"'","changed":true}' "$K printf '%s\n' '$OTHERPUB' | main authorize build-box - --json"
  e "authorize: a bad name is exit 2 and writes nothing" "2 2" "set +e; $K (main authorize 'Avi Mac' '$WSPUB' --json > /dev/null); echo \"\$? \$(wc -l < '$TD/keys/mu' | tr -d ' ')\""
  e "authorize: a bad key is exit 2, the reason and the next step in the JSON" '{"ok":false,"exit":2,"message":"not an authorisable key: ssh-keygen does not read it as a public key (damaged or cut short?)","next":"copy the whole line '"'"'ssh-ed25519 AAAA… <name>'"'"' that mu compute add printed"}
(exit 2)' "$K main authorize laptop '$CUTPUB' --json"
  e "authorize: the same key as another name is exit 3" "(exit 3)" "set +e; $K (main authorize laptop '$WSPUB' > /dev/null 2>&1); echo \"(exit \$?)\""
  e "keys: names and fingerprints" "$(printf '  %-20s %s\n  %-20s %s' avi-mac "$WSFP" build-box "$OTHERFP")" "$K main keys"
  e "revoke: removes the name" "revoked build-box" "$K main revoke build-box | cut -d: -f1; grep -c build-box '$TD/keys/mu' > /dev/null || true"
  e "revoke: again: nothing to do, exit 0" '{"ok":true,"name":"build-box","changed":false,"connections_ended":0}' "$K main revoke build-box --json"
  e "keys --json after revoke" '{"ok":true,"keys":[{"name":"avi-mac","fingerprint":"'"$WSFP"'","key":"'"$WSPUB2"'"}]}' "$K main keys --json"
  mf "authorize refuses a key cut short" no-keygen-check "(exit 2)" "set +e; $K (main authorize laptop2 '$CUTPUB' > /dev/null 2>&1); echo \"(exit \$?)\""

  # the relay settings for the tunnel (stdin of tunnel credential)
  e "relay settings: gen-credentials' lines (CRLF, comments) become tunnel.env" "$(cat "$TD/tunnel.env.want")" "tunnel_env_from < '$TD/gen.out'"
  e "relay settings: AUTH, RELAY_URL, RELAY_FINGERPRINT, RELAY_PORT lines, export and quotes" "$(cat "$TD/tunnel.env.want")" "tunnel_env_from < '$TD/gen.auth'"
  e "relay settings: a workstation's settings are refused" "RELAY_ROLE is 'workstation': the node needs a compute client's settings (relay/gen-credentials.sh client <node>, not the workstation's)
(exit 2)" "sed 's/^RELAY_ROLE=compute/RELAY_ROLE=workstation/' '$TD/gen.out' | tunnel_env_from"
  e "relay settings: no password" "the relay password (RELAY_PASSWORD, or the part of AUTH after ':') is missing or shorter than 24 characters
(exit 2)" "grep -v PASSWORD '$TD/gen.out' | tunnel_env_from"
  e "relay settings: http:// is refused" "RELAY_URL is missing or not https://<host>[:port]
(exit 2)" "sed 's|^RELAY_URL=https|RELAY_URL=http|' '$TD/gen.out' | tunnel_env_from"
  e "relay settings: a wrong fingerprint length" "RELAY_FINGERPRINT is missing or not the relay's fingerprint (44 characters of base64)
(exit 2)" "sed 's|^RELAY_FINGERPRINT=.|RELAY_FINGERPRINT=|' '$TD/gen.out' | tunnel_env_from"
  e "relay settings: two ports (a workstation's) or 7000 are refused" "2 2" \
    "set +e; sed 's|^RELAY_PORTS=.*|RELAY_PORTS=7001+7002|' '$TD/gen.out' | tunnel_env_from 2> /dev/null; printf '%s ' \$?; sed 's|^RELAY_PORTS=.*|RELAY_PORTS=7000|' '$TD/gen.out' | tunnel_env_from 2> /dev/null; echo \$?"
  e "relay settings: an error never shows the password" "0" "sed 's|^RELAY_URL=.*|RELAY_URL=x|' '$TD/gen.out' | tunnel_env_from 2>&1 | grep -c '$RPW' || true"

  # the tunnel's journal, as chisel 1.12.0 writes it (the lines of tools/test-jobs/unit.py and of the S4 replica)
  e "tunnel log: connected" "$(printf 'connected\tconnected\t2026/11/14 10:00:01 client: Connected (Latency 1.2ms)')" "printf '%s\n' '2026/11/14 10:00:00 client: Connecting to wss://relay.martand.systems:443' '2026/11/14 10:00:01 client: Connected (Latency 1.2ms)' | tunnel_classify"
  e "tunnel log: a stream error after Connected is not a tunnel state" "connected" "printf '%s\n' 'client: Connected (Latency 1ms)' 'client: tun: proxy#R:127.0.0.1:7001=>2222: conn#3: Stream error: Connection error: dial tcp: connection refused' | tunnel_classify | cut -f1"
  e "tunnel log: wrong password" "$(printf 'refused\trelay-credential')" "printf '%s\n' 'client: Connection error: ssh: handshake failed: ssh: unable to authenticate, attempted methods [none password], no supported methods remain' 'client: Retrying in 1s...' | tunnel_classify | cut -f1,2"
  e "tunnel log: wrong fingerprint" "$(printf 'refused\trelay-fingerprint')" "printf '%s\n' 'client: Connection error: ssh: handshake failed: Invalid fingerprint (abc=)' | tunnel_classify | cut -f1,2"
  e "tunnel log: a port the relay does not allow" "$(printf 'refused\trelay-rule')" "printf '%s\n' \"client: Connection error: server: access to 'R:127.0.0.1:7002:127.0.0.1:2222' denied\" | tunnel_classify | cut -f1,2"
  e "tunnel log: the relay still holds the old session" "$(printf 'busy\trelay-port-busy')" "printf '%s\n' 'client: Connection error: server: Server cannot listen on R:127.0.0.1:7001=>127.0.0.1:2222' | tunnel_classify | cut -f1,2"
  e "tunnel log: connected, then lost" "$(printf 'unreachable\trelay-unreachable')" "printf '%s\n' 'client: Connected (Latency 1ms)' 'client: Disconnected' 'client: Connection error: dial tcp 1.2.3.4:443: connect: connection refused' | tunnel_classify | cut -f1,2"
  e "tunnel log: lost, then connected again" "connected" "printf '%s\n' 'client: Disconnected' 'client: Retrying in 1s...' 'client: Connected (Latency 1ms)' | tunnel_classify | cut -f1"
  e "tunnel log: nothing yet" "$(printf 'starting\tno-connection-yet\t')" "tunnel_classify < '$TD/empty'"

  # the tunnel's egress filter (review finding 2): the relay's addresses, the drop-in, the hosts file
  e "egress: getent ahosts lines give each address once (v4 and v6), nothing else" "$(printf '195.201.236.190\n2a01:4f8:1c1c::1')" \
    "printf '%s\n' '195.201.236.190 STREAM relay.martand.systems' '195.201.236.190 DGRAM' '195.201.236.190 RAW' '2a01:4f8:1c1c::1 STREAM' 'garbage line' '1.2.3 STREAM' | egress_ips"
  e "egress: the drop-in allows loopback and the relay only" "$(printf '[Service]\nBindReadOnlyPaths=/etc/mu/tunnel.hosts:/etc/hosts\nIPAddressDeny=any\nIPAddressAllow=127.0.0.1/32 ::1/128 195.201.236.190/32 2a01:4f8:1c1c::1/128')" \
    "egress_dropin_text 195.201.236.190 2a01:4f8:1c1c::1 | grep -v '^#'"
  e "egress: the tunnel's hosts file names the relay" "$(printf '127.0.0.1 localhost\n::1 localhost\n195.201.236.190 relay.martand.systems')" \
    "tunnel_hosts_text relay.martand.systems 195.201.236.190 | grep -v '^#'"
  # ready for jobs: the checks a job needs (status's last line)
  printf 'pass\tkeys\tavi-mac SHA256:x\npass\ttunnel\tconnected\npass\ttunnel.egress\tx\npass\tnode.runner\tx\npass\tnode.limits\tx\npass\tnode.disk\tx\npass\tnode.hello\tx\ninfo\twsl\tx\n' > "$TD/checks.ready"
  e "ready for jobs: yes when keys, tunnel, egress, runner, limits, disk and hello pass" "yes" "ready_line < '$TD/checks.ready'"
  e "ready for jobs: no workstation yet (WARN keys) is not ready" "$(printf 'no\tkeys')" "sed 's/^pass\tkeys/warn\tkeys/' '$TD/checks.ready' | ready_line"
  e "ready for jobs: a check that did not run is not ready" "$(printf 'no\tnode.hello(not checked)')" "grep -v node.hello '$TD/checks.ready' | ready_line"

  # listeners, delegation, the drive record, the security pocket
  e "listeners: mu-sshd on 127.0.0.1:2222 only is fine" "(exit 0)" "listeners_bad 2222 < '$TD/ss.good'; echo \"(exit \$?)\""
  e "listeners: port 22 and an sshd elsewhere are caught" "$(printf '0.0.0.0:22\tusers:((\"sshd\",pid=9,fd=3))\n[::]:22\tusers:((\"sshd\",pid=9,fd=4))\n0.0.0.0:2222\tusers:((\"sshd\",pid=7,fd=3))\n(exit 1)')" "listeners_bad 2222 < '$TD/ss.bad'; echo \"(exit \$?)\""
  e "cgroup delegation: nothing missing" "" "delegation_missing 'cpuset cpu io memory hugetlb pids rdma misc' 'cpu memory pids'"
  e "cgroup delegation: memory missing (the kernel has it)" "memory" "delegation_missing 'cpu io memory pids' 'cpu pids'"
  e "cgroup delegation: memory missing in the kernel too: no drop-in would help" "" "delegation_missing 'cpu pids' 'cpu pids'"
  e "host drive record: C, default mount root" "C /mnt/c" "host_drive_line c"
  e "host drive record: D under another automount root" "D /win/d" "host_drive_line D /win/"
  e "host drive record: not a letter" "(exit 1)" "set +e; host_drive_line CD; echo \"(exit \$?)\""
  e "security updates: the security pockets only" "(exit 0)" "uu_origins_ok < '$TD/aptdump.good'; echo \"(exit \$?)\""
  e "security updates: -updates is caught" "\${distro_id}:\${distro_codename}-updates
(exit 1)" "uu_origins_ok < '$TD/aptdump.updates'; echo \"(exit \$?)\""
  e "security updates: the conf clears Ubuntu's list, then names only security pockets" "2 3 0" \
    "uu_conf_text | grep -c '^#clear' | tr '\n' ' '; uu_conf_text | grep -c -- '-security\";' | tr '\n' ' '; uu_conf_text | grep -c -- '-updates' || true"
  # the gate as install writes it (the user's home as getent would say on the node)
  e "gate: both machine values written in, nothing left to fill" "0 MU_BIN='/home/mu/.local/share/mu/bin/mu' KEYS='/etc/mu/authorized_keys/mu'" \
    "set +e; user_home() { echo /home/mu; }; gate_text | grep -c \"='@MU_\" | tr '\n' ' '; gate_text | grep -E '^(MU_BIN|KEYS)=' | sed 's/ *#.*//' | tr '\n' ' ' | sed 's/ \$//'"
  e "chisel pin: the version and both sums are what toolchain/apps.toml and the release say" "1.12.0 f3f180f1d93aa72cce4e6386f98cc06569a0146fbd65eb4423cf83e6434bcfe6 811362311f13ac4bdc42fe4e7650aca618a68e3a40f930de88536d18b14d2b52" \
    "echo \$CHISEL_VERSION \$CHISEL_GZ_SHA256 \$CHISEL_BIN_SHA256"
  e "the tunnel unit: the credential only from the environment file, never in the command line" "0 1" \
    "set +e; tunnel_unit_text | grep '^ExecStart=' | grep -c -i -E 'auth|pass' | tr '\n' ' '; tunnel_unit_text | grep -c '^EnvironmentFile=/etc/mu/tunnel.env\$'"
  e "usage: authorize without a key" "usage: mu-wsl2-distro authorize NAME 'ssh-ed25519 AAAA… NAME' (or - to read the key from stdin)
(exit 2)" "main authorize avi-mac"
  e "usage: --host-drive takes one letter" "--host-drive takes one drive letter, e.g. C
(exit 2)" "main status --host-drive CD"
  e "usage: pair needs one of claim, finish, run, abort, status" "usage: mu-wsl2-distro pair claim|finish|run|abort|status [--relay URL] [--json] (the code on stdin)
(exit 2)" "main pair bogus"
  e "pair: refuses without root, before reading any code" "mu-wsl2-distro: run as root: wsl -d <distro> -u root -- mu-wsl2-distro pair
(exit 1)" "set +e; id() { echo 1000; }; MU_KEYS_DIR= main pair claim --relay https://relay.martand.systems < /dev/null"
done

# ---- the Windows script, if PowerShell is installed here (pwsh 7: the parse also runs a PowerShell 5.1 syntax check)
if command -v pwsh > /dev/null 2>&1; then
  echo "== pwsh ($(pwsh -NoProfile -c '$PSVersionTable.PSVersion.ToString()'))"
  if [ ! -f "$PS1" ]; then bad "mu-wsl2-host.ps1 exists"
  elif LC_ALL=C grep -n '[^ -~	]' "$PS1" > "$TD/nonascii"; then bad "mu-wsl2-host.ps1 is ASCII only (Windows PowerShell 5.1 reads BOM-less files as ANSI)" "$(head -5 "$TD/nonascii")"
  else ok "mu-wsl2-host.ps1 is ASCII only"; fi
  if out="$(pwsh -NoProfile -NonInteractive -File "$HERE/ps-tests.ps1" "$PS1" 2>&1)"; then
    printf '%s\n' "$out" | grep -E '^(ok|FAIL) ' || true
    PASS=$((PASS + $(printf '%s\n' "$out" | grep -c '^ok ' || true)))
  else
    printf '%s\n' "$out" | sed 's/^/      /'
    PASS=$((PASS + $(printf '%s\n' "$out" | grep -c '^ok ' || true))); FAILN=$((FAILN + $(printf '%s\n' "$out" | grep -c '^FAIL ' || true) + 1))
  fi
else
  echo "(pwsh not installed: the Windows script is not checked)"
fi
echo; echo "$PASS passed, $FAILN failed"
[ "$FAILN" = 0 ]
