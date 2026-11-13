#!/bin/bash
# Tests for the WSL2 host adapter (install/hosts/wsl2/): the Linux script's pure functions (wsl.conf merge, heartbeat log
# analysis, GitHub host key parsing, ssh config merge, JSON strings, clean-stop markers), its syntax, and, when pwsh is
# installed, the Windows script (PowerShell 5.1 syntax check, .wslconfig merge, argument quoting, task XML).
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
