# shellcheck shell=bash
# μ installer: free disk space, WSL-aware. Sourced by install/install.sh. bin/mu (installed on its own, so it can't
# source recipe files) carries an identical copy of the block between the >>> and <<< lines: change both
# (tools/test-wsl-disk/run.sh checks that they match). Runs under macOS /bin/bash 3.2 and bash 5.
#
#   mu_disk_free DIR   prints "<GB free>\t<where>": <where> is empty, except on WSL, where it names the Windows drive that
#                      holds the distro's virtual disk ("C: (WSL disk)") and the free space is the smaller of the two
#
# On WSL2, df sees the inside of the distro's ext4.vhdx: a sparse virtual disk whose limit (1 TB by default) is usually
# far larger than the Windows drive it lives on, so df / can say 934 GB free on a drive with 130 GB free. The VHD's folder
# is the distro's BasePath under HKCU\Software\Microsoft\Windows\CurrentVersion\Lxss (the key whose DistributionName is
# $WSL_DISTRO_NAME), read with reg.exe through Windows interop, by full path (appendWindowsPath may be off). Its drive
# letter maps to that drive's drvfs mount (from /proc/mounts; normally /mnt/c). Fallbacks: without $WSL_DISTRO_NAME
# (systemd services, env -i), every distro's drive counts and the least free wins; without interop (or no answer
# within 15 s), C: is assumed if it is mounted; failing that, only the VHD is measured, and <where> says so.
# Test hooks: MU_WSL=1|0 forces WSL detection, MU_WSL_REG replaces reg.exe, MU_PROC_MOUNTS replaces /proc/mounts.

# >>> mu_disk_free (keep identical in install/disk.sh and bin/mu)
_mu_is_wsl() {
  case "${MU_WSL:-}" in 1) return 0 ;; 0) return 1 ;; esac
  [ -n "${WSL_DISTRO_NAME:-}" ] && return 0
  [ -e /proc/sys/fs/binfmt_misc/WSLInterop ] && return 0
  [ -e /proc/sys/fs/binfmt_misc/WSLInterop-late ] && return 0
  case "$(uname -r 2>/dev/null)" in *[Mm]icrosoft*) return 0 ;; esac
  return 1
}
_mu_kb_free() { df -Pk "$1" 2>/dev/null | awk 'NR==2 && $4 ~ /^[0-9]+$/ {print $4}'; }
# _mu_drive_mount LETTER: where that Windows drive is mounted (drvfs, over 9p or virtiofs), or nothing
_mu_drive_mount() {
  local l u
  l="$(printf '%s' "$1" | tr 'A-Z' 'a-z')"; u="$(printf '%s' "$1" | tr 'a-z' 'A-Z')"
  awk -v l="$l" -v u="$u" '
    $3 == "9p" || $3 == "drvfs" || $3 == "virtiofs" {
      s = substr($1, 1, 2)
      if (s == u ":" || s == l ":" || index($4, "path=" u ":") || index($4, "path=" l ":") || $2 == "/mnt/" l) { print $2; exit }
    }' "${MU_PROC_MOUNTS:-/proc/mounts}" 2>/dev/null | sed 's/\\040/ /g'
}
# _mu_wsl_drives: the drive letters holding the distro's VHD (one per line; every distro's if this one isn't known)
_mu_wsl_drives() {
  local IFS=" $(printf '\t')
" reg="${MU_WSL_REG:-/mnt/c/Windows/System32/reg.exe}" key='HKCU\Software\Microsoft\Windows\CurrentVersion\Lxss' t=""
  [ -x "$reg" ] || return 0
  command -v timeout >/dev/null 2>&1 && t="timeout 15"
  # reg.exe writes CRLF lines (and UTF-16 in some setups): drop NULs and CRs, then read DistributionName / BasePath
  # per subkey. BasePath may be \\?\C:\..., C:\..., or a UNC path (no drive letter: ignored).
  { $t "$reg" query "$key" /s 2>/dev/null < /dev/null || true; } | LC_ALL=C tr -d '\000\r' | LC_ALL=C awk -v want="${WSL_DISTRO_NAME:-}" '
    function flush() { if (base != "") { n++; nm[n] = name; b[n] = base }; name = ""; base = "" }
    /^HKEY_/ { flush(); next }
    { v = $0; sub(/^[ \t]+/, "", v); sub(/[ \t]+$/, "", v) }
    v ~ /^DistributionName[ \t]+REG_SZ[ \t]/ { sub(/^DistributionName[ \t]+REG_SZ[ \t]+/, "", v); name = v }
    v ~ /^BasePath[ \t]+REG_(EXPAND_)?SZ[ \t]/ { sub(/^BasePath[ \t]+REG_(EXPAND_)?SZ[ \t]+/, "", v); base = v }
    END {
      flush(); m = 0
      for (i = 1; i <= n; i++) if (want != "" && nm[i] == want) m = i
      for (i = 1; i <= n; i++) {
        if (m && i != m) continue
        p = b[i]; if (substr(p, 1, 4) == "\\\\?\\") p = substr(p, 5)
        d = substr(p, 1, 1)
        if (substr(p, 2, 1) == ":" && d ~ /^[A-Za-z]$/) print toupper(d)
      }
    }' | sort -u
}
mu_disk_free() {
  local d="$1" kb best="" where="" drives l m k assumed="" IFS=" $(printf '\t')
"   # callers may have changed IFS
  while [ ! -d "$d" ]; do d="$(dirname "$d")"; done
  kb="$(_mu_kb_free "$d")"
  if ! _mu_is_wsl; then
    [ -z "$kb" ] || printf '%s\t\n' "$((kb / 1048576))"
    return 0
  fi
  drives="$(_mu_wsl_drives)"
  [ -n "$drives" ] || { drives=C; assumed=", assumed"; }   # no interop: the default location
  for l in $drives; do
    m="$(_mu_drive_mount "$l")"; [ -n "$m" ] || continue
    k="$(_mu_kb_free "$m")"; [ -n "$k" ] || continue
    if [ -z "$best" ] || [ "$k" -lt "$best" ]; then best="$k"; where="$l: (WSL disk$assumed)"; fi
  done
  if [ -z "$best" ]; then
    [ -z "$kb" ] || printf '%s\tthe WSL virtual disk (Windows drive not found)\n' "$((kb / 1048576))"
    return 0
  fi
  # the VHD itself can be the limit (its maximum size is nearly used up)
  [ -z "$kb" ] || [ "$kb" -ge "$best" ] || { best="$kb"; where="the WSL virtual disk (at its size limit)"; }
  printf '%s\t%s\n' "$((best / 1048576))" "$where"
}
# <<< mu_disk_free
