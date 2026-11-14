# shellcheck shell=bash
# μ installer: free disk space, WSL-aware. Sourced by install/install.sh. bin/mu (installed on its own, so it can't
# source recipe files) carries an identical copy of the block between the >>> and <<< lines: change both
# (tools/test-wsl-disk/run.sh checks that they match). Runs under macOS /bin/bash 3.2 and bash 5.
#
#   mu_disk_free DIR   sets MU_FREE_GB (free GB to check), MU_FREE_WHERE (empty, except on WSL: "C: (WSL disk)", or
#                      "the WSL virtual disk ..."), and MU_FREE_NOTE / MU_FREE_NOTE_GB (see below). Call it directly, not
#                      in $(...): it sets variables.
#
# On WSL2, df sees the inside of the distro's ext4.vhdx: a sparse virtual disk whose limit (1 TB by default) is usually
# far larger than the Windows drive it lives on, so df / can say 934 GB free on a drive with 130 GB free. The VHD's folder
# is the distro's BasePath under HKCU\Software\Microsoft\Windows\CurrentVersion\Lxss (the key whose DistributionName is
# $WSL_DISTRO_NAME), read with reg.exe through Windows interop, by full path (appendWindowsPath may be off). Its drive
# letter maps to that drive's drvfs mount (from /proc/mounts; normally /mnt/c). When that drive is known for certain (this
# distro found by name, or every registered distro on one drive), the free GB is the smaller of the drive and the VHD.
# Otherwise only the VHD is checked, because a guessed drive must never stop an install or an auto-update: without
# interop (or no answer within 15 s), or a BasePath with no drive letter, C: is assumed; without $WSL_DISTRO_NAME
# (ssh, systemd services, env -i), the least free drive of all registered distros. That guess is reported in
# MU_FREE_NOTE ("6 GB free on C: if the WSL disk is there (why)") with MU_FREE_NOTE_GB, when it is below the VHD figure.
# No drvfs mount for the drive: only the VHD is measured, and MU_FREE_WHERE says so. A df that fails (a drive gone
# away) is skipped.
# The drive the host script recorded (/var/lib/mu-wsl2/host-drive, "C /mnt/c") is read before reg.exe and counts as sure.
# Test hooks: MU_WSL=1|0 forces WSL detection, MU_WSL_REG replaces reg.exe, MU_PROC_MOUNTS replaces /proc/mounts,
# MU_WSL_HOST_DRIVE replaces the host-drive record.

# >>> mu_disk_free (keep identical in install/disk.sh and bin/mu)
_mu_is_wsl() {
  case "${MU_WSL:-}" in 1) return 0 ;; 0) return 1 ;; esac
  [ -n "${WSL_DISTRO_NAME:-}" ] && return 0
  [ -e /proc/sys/fs/binfmt_misc/WSLInterop ] && return 0
  [ -e /proc/sys/fs/binfmt_misc/WSLInterop-late ] && return 0
  case "$(uname -r 2>/dev/null)" in *[Mm]icrosoft*) return 0 ;; esac
  return 1
}
# (every helper returns 0, even when df or awk fails: callers run under set -E with an ERR trap)
_mu_kb_free() { { df -Pk "$1" 2>/dev/null || true; } | awk 'NR==2 && $4 ~ /^[0-9]+$/ {print $4}'; }
# _mu_drive_mount LETTER: where that Windows drive is mounted (drvfs, over 9p or virtiofs), or nothing
_mu_drive_mount() {
  local l u
  l="$(printf '%s' "$1" | tr 'A-Z' 'a-z')"; u="$(printf '%s' "$1" | tr 'a-z' 'A-Z')"
  { awk -v l="$l" -v u="$u" '
      $3 == "9p" || $3 == "drvfs" || $3 == "virtiofs" {
        s = substr($1, 1, 2)
        if (s == u ":" || s == l ":" || index($4, "path=" u ":") || index($4, "path=" l ":") || $2 == "/mnt/" l) { print $2; exit }
      }' "${MU_PROC_MOUNTS:-/proc/mounts}" 2>/dev/null || true; } | sed 's/\\040/ /g'
}
# _mu_wsl_drives: one line. "sure L": drive L holds this distro's virtual disk. "unsure L M...": this distro is not
# among the registered ones by name (no $WSL_DISTRO_NAME), so it is on one of these drives. "nodrive": its BasePath has no
# drive letter. Nothing: no answer from Windows.
_mu_wsl_drives() {
  local IFS=" $(printf '\t')
" reg="${MU_WSL_REG:-/mnt/c/Windows/System32/reg.exe}" key='HKCU\Software\Microsoft\Windows\CurrentVersion\Lxss' t=""
  [ -x "$reg" ] || return 0
  command -v timeout >/dev/null 2>&1 && t="timeout 15"
  # reg.exe writes CRLF lines (and UTF-16 in some setups): drop NULs and CRs, then read DistributionName / BasePath
  # per subkey. BasePath may be \\?\C:\..., C:\..., or a UNC path (no drive letter).
  { $t "$reg" query "$key" /s 2>/dev/null < /dev/null || true; } | LC_ALL=C tr -d '\000\r' | LC_ALL=C awk -v want="${WSL_DISTRO_NAME:-}" '
    function flush() { if (base != "") { n++; nm[n] = name; b[n] = base }; name = ""; base = "" }
    /^HKEY_/ { flush(); next }
    { v = $0; sub(/^[ \t]+/, "", v); sub(/[ \t]+$/, "", v) }
    v ~ /^DistributionName[ \t]+REG_SZ[ \t]/ { sub(/^DistributionName[ \t]+REG_SZ[ \t]+/, "", v); name = v }
    v ~ /^BasePath[ \t]+REG_(EXPAND_)?SZ[ \t]/ { sub(/^BasePath[ \t]+REG_(EXPAND_)?SZ[ \t]+/, "", v); base = v }
    END {
      flush(); m = 0; k = 0; out = ""
      for (i = 1; i <= n; i++) if (want != "" && nm[i] == want) m = i
      for (i = 1; i <= n; i++) {
        if (m && i != m) continue
        p = b[i]; if (substr(p, 1, 4) == "\\\\?\\") p = substr(p, 5)
        d = toupper(substr(p, 1, 1))
        if (substr(p, 2, 1) == ":" && d ~ /^[A-Z]$/ && !(d in seen)) { seen[d] = 1; k++; out = out " " d }
      }
      if (n == 0) exit
      if (k == 0) { print "nodrive"; exit }
      # sure: this distro found by name, or every registered distro is on the one drive
      print ((m || (want == "" && k == 1)) ? "sure" : "unsure") out
    }'
}
# _mu_host_drive: the drive letter the host script recorded as holding this distro (root-owned, one line "C /mnt/c";
# MU_WSL_HOST_DRIVE names another file), or nothing. Read first and taken as sure: interop may be off (design §13.4)
_mu_host_drive() {
  local l="" r f="${MU_WSL_HOST_DRIVE:-/var/lib/mu-wsl2/host-drive}"
  if [ -r "$f" ]; then { read -r l r < "$f"; } 2>/dev/null || true; fi
  case "${l:-}" in [A-Za-z]) printf '%s' "$l" | tr 'a-z' 'A-Z' ;; esac
  return 0
}
# mu_disk_free DIR: sets MU_FREE_GB (the free GB to check; empty if df failed), MU_FREE_WHERE (where: empty unless WSL)
# and, when the Windows drive could not be confirmed and has less free space than the virtual disk, MU_FREE_NOTE_GB and
# MU_FREE_NOTE (that drive's free GB, to show or warn about but not to enforce)
mu_disk_free() {
  local d="$1" kb ans state drives why="" l m k best="" bl="" all="" IFS=" $(printf '\t')
"   # callers may have changed IFS
  MU_FREE_GB=""; MU_FREE_WHERE=""; MU_FREE_NOTE=""; MU_FREE_NOTE_GB=""
  while [ ! -d "$d" ]; do d="$(dirname "$d")"; done
  kb="$(_mu_kb_free "$d")"
  [ -z "$kb" ] || MU_FREE_GB=$((kb / 1048576))
  _mu_is_wsl || return 0
  l="$(_mu_host_drive)"
  if [ -n "$l" ]; then ans="sure $l"; else ans="$(_mu_wsl_drives)"; fi
  state="${ans%% *}"; drives="${ans#"$state"}"
  case "$state" in
    sure) ;;
    unsure) why="WSL_DISTRO_NAME is not set"; [ -z "${WSL_DISTRO_NAME:-}" ] || why="no distro named $WSL_DISTRO_NAME in the registry" ;;
    nodrive) state=unsure; drives=C; why="its folder has no drive letter, so C: is assumed" ;;
    *) state=unsure; drives=C; why="no answer from Windows (reg.exe), so C: is assumed" ;;
  esac
  for l in $drives; do
    all="$all $l:"
    m="$(_mu_drive_mount "$l")"; [ -n "$m" ] || continue
    k="$(_mu_kb_free "$m")"; [ -n "$k" ] || continue
    if [ -z "$best" ] || [ "$k" -lt "$best" ]; then best="$k"; bl="$l"; fi
  done
  if [ -z "$best" ]; then MU_FREE_WHERE="the WSL virtual disk (Windows drive not found)"; return 0; fi
  if [ "$state" = sure ]; then
    # the smaller of the two; the VHD itself can be the limit (its maximum size is nearly used up)
    if [ -n "$kb" ] && [ "$kb" -lt "$best" ]; then MU_FREE_WHERE="the WSL virtual disk (at its size limit)"
    else MU_FREE_GB=$((best / 1048576)); MU_FREE_WHERE="$bl: (WSL disk)"; fi
    return 0
  fi
  # not confirmed: only the virtual disk is checked; the drive's figure is reported when it is the smaller one
  MU_FREE_WHERE="the WSL virtual disk"
  [ "$(printf '%s' "$all" | wc -w)" -le 1 ] || why="$why; the least free of$all"
  if [ -z "$kb" ] || [ "$best" -lt "$kb" ]; then
    MU_FREE_NOTE_GB=$((best / 1048576)); MU_FREE_NOTE="$MU_FREE_NOTE_GB GB free on $bl: if the WSL disk is there ($why)"
  fi
  return 0
}
# <<< mu_disk_free
