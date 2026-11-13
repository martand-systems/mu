#!/bin/bash
# Tests for the WSL-aware free-disk check (mu_disk_free: install/disk.sh, and its copy in bin/mu) with stubs: a fake
# reg.exe (CRLF output, UTF-16LE with a BOM, failures), a fake /proc/mounts and a df that reports chosen sizes.
#
#   tools/test-wsl-disk/run.sh [BASH ...]      default: /bin/bash plus any other bash found (e.g. Homebrew's bash 5)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/../.." && pwd)"
TD="$(mktemp -d "${TMPDIR:-/tmp}/mu test wsl disk.XXXXXX")"   # a space in the path on purpose
trap 'rm -rf "$TD"' EXIT
if [ $# -gt 0 ]; then BASHES="$*"; else
  BASHES="/bin/bash"; seen="$(/bin/bash -c 'echo $BASH_VERSION')"
  for b in /opt/homebrew/bin/bash /usr/local/bin/bash $(command -v -a bash 2>/dev/null || true); do
    [ -x "$b" ] || continue; v="$("$b" -c 'echo $BASH_VERSION')"
    case " $seen " in *" $v "*) ;; *) BASHES="$BASHES $b"; seen="$seen $v" ;; esac
  done
fi

# the shared block must be identical in both files
block() { sed -n '/^# >>> mu_disk_free/,/^# <<< mu_disk_free/p' "$1"; }
block "$REPO/install/disk.sh" > "$TD/block.sh"; block "$REPO/bin/mu" > "$TD/block-mu.sh"
[ -s "$TD/block.sh" ] || { echo "FAIL: no mu_disk_free block in install/disk.sh"; exit 1; }
if ! cmp -s "$TD/block.sh" "$TD/block-mu.sh"; then
  echo "FAIL: mu_disk_free differs between install/disk.sh and bin/mu:"; diff "$TD/block.sh" "$TD/block-mu.sh" || true; exit 1
fi
echo "ok    the copies in install/disk.sh and bin/mu are identical"

# ---- fixtures
mkdir -p "$TD/home/mu" "$TD/mnt/c" "$TD/mnt/d" "$TD/mnt/e drive" "$TD/bin"
K='HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Lxss'
crlf() { sed 's/$/\r/'; }
# reg.exe query /s output: the Lxss key, then one subkey per distro
reg_text() {   # reg_text "NAME|BASEPATH" ...
  printf '\n%s\n    DefaultDistribution    REG_SZ    {11111111-2222-3333-4444-555555555555}\n    DefaultVersion    REG_DWORD    0x2\n' "$K"
  local i=0 e
  for e in "$@"; do
    i=$((i + 1))
    printf '\n%s\\{0000000%s-aaaa-bbbb-cccc-dddddddddddd}\n' "$K" "$i"
    printf '    State    REG_DWORD    0x1\n    DistributionName    REG_SZ    %s\n    Version    REG_DWORD    0x2\n' "${e%%|*}"
    printf '    BasePath    REG_SZ    %s\n    Flags    REG_DWORD    0xf\n    DefaultUid    REG_DWORD    0x3e8\n' "${e#*|}"
    printf '    VhdFileName    REG_SZ    ext4.vhdx\n'
  done
  printf '\n'
}
fake_reg() {   # fake_reg NAME: a reg.exe that prints $TD/NAME.out and exits with $TD/NAME.rc
  printf '#!/bin/sh\ncat "%s"\nexit $(cat "%s")\n' "$TD/$1.out" "$TD/$1.rc" > "$TD/bin/$1.exe"; chmod +x "$TD/bin/$1.exe"
}
reg_text 'Ubuntu-24.04|C:\Users\Avi\AppData\Local\Packages\CanonicalGroupLimited.Ubuntu24.04LTS_79rhkp1fndgsc\LocalState' \
         'mu-compute|\\?\D:\WSL\mu-compute' | crlf > "$TD/two.out"; echo 0 > "$TD/two.rc"; fake_reg two
{ printf '\377\376'; reg_text 'Ubuntu-24.04|C:\Users\Avi\WSL' 'mu-compute|D:\WSL\mu compute' | crlf | iconv -f UTF-8 -t UTF-16LE; } > "$TD/utf16.out"
echo 0 > "$TD/utf16.rc"; fake_reg utf16
printf 'ERROR: The system was unable to find the specified registry key or value.\r\n' > "$TD/nokey.out"; echo 1 > "$TD/nokey.rc"; fake_reg nokey
reg_text 'Debian|\\wsl.localhost\share\Debian' | crlf > "$TD/unc.out"; echo 0 > "$TD/unc.rc"; fake_reg unc
reg_text 'Ubuntu-24.04|e:\wsl\ubuntu' | crlf > "$TD/e.out"; echo 0 > "$TD/e.rc"; fake_reg e
printf '#!/bin/sh\necho "cannot execute binary file: Exec format error" >&2\nexit 126\n' > "$TD/bin/nointerop.exe"; chmod +x "$TD/bin/nointerop.exe"
# /proc/mounts as WSL2 writes it (drvfs over 9p; the backslash octal-escaped), and over virtiofs
esc() { printf '%s' "$1" | sed 's/ /\\040/g'; }   # /proc/mounts escapes spaces in paths
C_LINE="C:\\134 $(esc "$TD/mnt/c") 9p rw,noatime,dirsync,aname=drvfs;path=C:\;uid=1000;gid=1000;symlinkroot=/mnt/,mmap,access=client,msize=65536,trans=fd,rfd=5,wfd=5 0 0"
D_LINE="drvfsaD $(esc "$TD/mnt/d") virtiofs rw,noatime,dirsync,aname=drvfs;path=D:\;uid=1000;gid=1000 0 0"
E_LINE="E:\\134 $(esc "$TD/mnt/e drive") 9p rw,aname=drvfs;path=E:\\ 0 0"
ROOT_LINE="/dev/sdc / ext4 rw,relatime,discard,errors=remount-ro,data=ordered 0 0"
printf '%s\n%s\n%s\n%s\n' "$ROOT_LINE" "$C_LINE" "$D_LINE" "$E_LINE" > "$TD/mounts"
printf '%s\n%s\n' "$ROOT_LINE" "$D_LINE" > "$TD/mounts-noc"
printf '%s\n' "$ROOT_LINE" > "$TD/mounts-none"

cat > "$TD/case.sh" <<'EOS'
# runs one case as the installer does: set -E, errexit, pipefail and an ERR trap (inherited by $(...)), so any helper
# that returns non-zero shows up. Prints GB|WHERE|NOTE_GB|NOTE.
set -Eeuo pipefail
trap 'echo "ERR trap: line $LINENO: $BASH_COMMAND"; exit 1' ERR
. "$TD/block.sh"
df() {   # df -Pk PATH with sizes from VHD_GB, C_GB, D_GB, E_GB; DF_FAIL=PATTERN: an I/O error for matching paths
  local g
  case "$2" in ${DF_FAIL:-//none//}) echo "df: $2: Input/output error" >&2; return 1 ;; esac
  case "$2" in */mnt/c*) g=$C_GB ;; */mnt/d*) g=$D_GB ;; */mnt/e*) g=$E_GB ;; *) g=$VHD_GB ;; esac
  printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\nfake 999999999 1 %s 1%% %s\n' "$((g * 1048576 + 1000))" "$2"
}
[ "${TAB_IFS:-}" != 1 ] || IFS="$(printf '\t')"   # a caller that changed IFS
MU_FREE_GB=stale; MU_FREE_NOTE=stale
mu_disk_free "$1"
printf '%s|%s|%s|%s\n' "$MU_FREE_GB" "$MU_FREE_WHERE" "$MU_FREE_NOTE_GB" "$MU_FREE_NOTE"
EOS

PASS=0; FAILN=0
for B in $BASHES; do
  echo "== $B ($("$B" -c 'echo $BASH_VERSION'))"
  t() {   # t NAME EXPECTED VAR=VALUE...
    local name="$1" want="$2" got; shift 2
    got="$(env -i PATH="/usr/bin:/bin:/usr/sbin:/sbin" TD="$TD" VHD_GB=934 C_GB=130 D_GB=410 E_GB=77 MU_PROC_MOUNTS="$TD/mounts" \
           MU_WSL_REG="$TD/bin/two.exe" "$@" "$B" "$TD/case.sh" "$TD/home/mu/.local/share/mu" 2>&1)" || got="$got (exit $?)"
    if [ "$got" = "$want" ]; then PASS=$((PASS + 1)); echo "ok    $name"
    else FAILN=$((FAILN + 1)); echo "FAIL  $name"; echo "      expected: [$want]"; echo "      got:      [$got]"; fi
  }
  NI="no answer from Windows (reg.exe), so C: is assumed"
  t "not WSL: df as it is"                      "934|||"                   MU_WSL=0
  t "WSL, distro found (CRLF): its drive C:"    "130|C: (WSL disk)||"      WSL_DISTRO_NAME=Ubuntu-24.04
  t "WSL, \\\\?\\D:\\ base path: D:"            "410|D: (WSL disk)||"      WSL_DISTRO_NAME=mu-compute
  t "WSL, UTF-16LE output with BOM"             "410|D: (WSL disk)||"      WSL_DISTRO_NAME=mu-compute MU_WSL_REG="$TD/bin/utf16.exe"
  t "WSL, UTF-16LE, other distro"               "130|C: (WSL disk)||"      WSL_DISTRO_NAME=Ubuntu-24.04 MU_WSL_REG="$TD/bin/utf16.exe"
  t "WSL, VHD nearly full: VHD limits"          "20|the WSL virtual disk (at its size limit)||" WSL_DISTRO_NAME=Ubuntu-24.04 VHD_GB=20
  t "WSL, distro on D:, C: nearly full: C: ignored" "410|D: (WSL disk)||"  WSL_DISTRO_NAME=mu-compute C_GB=6
  t "WSL, lower-case drive letter, mount with a space" "77|E: (WSL disk)||" WSL_DISTRO_NAME=Ubuntu-24.04 MU_WSL_REG="$TD/bin/e.exe"
  t "WSL, one distro, no distro name: its drive is sure" "77|E: (WSL disk)||" MU_WSL=1 MU_WSL_REG="$TD/bin/e.exe"
  # not confirmed: only the VHD is enforced; the guessed drive is reported (when it is the smaller figure)
  t "WSL, no distro name, two drives: VHD checked, least free noted" \
    "934|the WSL virtual disk|130|130 GB free on C: if the WSL disk is there (WSL_DISTRO_NAME is not set; the least free of C: D:)" MU_WSL=1
  t "WSL, no distro name, D: fuller"            "934|the WSL virtual disk|60|60 GB free on D: if the WSL disk is there (WSL_DISTRO_NAME is not set; the least free of C: D:)" MU_WSL=1 D_GB=60
  t "WSL, unknown distro name"                  "934|the WSL virtual disk|60|60 GB free on D: if the WSL disk is there (no distro named Other in the registry; the least free of C: D:)" WSL_DISTRO_NAME=Other D_GB=60
  t "WSL, interop off, C: nearly full: not enforced" "934|the WSL virtual disk|6|6 GB free on C: if the WSL disk is there ($NI)" WSL_DISTRO_NAME=Ubuntu-24.04 MU_WSL_REG="$TD/bin/nointerop.exe" C_GB=6
  t "WSL, no reg.exe at all: C: assumed"        "934|the WSL virtual disk|130|130 GB free on C: if the WSL disk is there ($NI)" WSL_DISTRO_NAME=Ubuntu-24.04 MU_WSL_REG=/nonexistent/reg.exe
  t "WSL, reg error: C: assumed"                "934|the WSL virtual disk|130|130 GB free on C: if the WSL disk is there ($NI)" WSL_DISTRO_NAME=Ubuntu-24.04 MU_WSL_REG="$TD/bin/nokey.exe"
  t "WSL, UNC base path: C: assumed"            "934|the WSL virtual disk|130|130 GB free on C: if the WSL disk is there (its folder has no drive letter, so C: is assumed)" WSL_DISTRO_NAME=Debian MU_WSL_REG="$TD/bin/unc.exe"
  t "WSL, assumed C: larger than the VHD: no note" "100|the WSL virtual disk||" WSL_DISTRO_NAME=Ubuntu-24.04 MU_WSL_REG=/nonexistent VHD_GB=100
  t "WSL, no interop, no C: mount: VHD only"    "934|the WSL virtual disk (Windows drive not found)||" WSL_DISTRO_NAME=x MU_WSL_REG=/nonexistent MU_PROC_MOUNTS="$TD/mounts-noc"
  t "WSL, drive not mounted: VHD only"          "934|the WSL virtual disk (Windows drive not found)||" WSL_DISTRO_NAME=Ubuntu-24.04 MU_PROC_MOUNTS="$TD/mounts-none"
  t "WSL, no /proc/mounts at all: VHD only"     "934|the WSL virtual disk (Windows drive not found)||" WSL_DISTRO_NAME=Ubuntu-24.04 MU_PROC_MOUNTS="$TD/nonexistent"
  # df failing (a drive gone away: Input/output error) skips that drive and never trips the ERR trap
  t "WSL, df fails on the distro's drive"       "410|the WSL virtual disk (Windows drive not found)||" WSL_DISTRO_NAME=mu-compute DF_FAIL='*/mnt/d*' VHD_GB=410
  t "WSL, no distro name, df fails on D:"       "934|the WSL virtual disk|130|130 GB free on C: if the WSL disk is there (WSL_DISTRO_NAME is not set; the least free of C: D:)" MU_WSL=1 DF_FAIL='*/mnt/d*'
  t "df fails on the VHD too"                   "|the WSL virtual disk (Windows drive not found)||" WSL_DISTRO_NAME=Ubuntu-24.04 DF_FAIL='*'
  t "df fails, sure drive known: drive figure"  "130|C: (WSL disk)||"      WSL_DISTRO_NAME=Ubuntu-24.04 DF_FAIL='*/mu/*'
  t "not WSL, df fails: empty"                  "|||"                      MU_WSL=0 DF_FAIL='*'
  t "caller's IFS is a tab"                     "934|the WSL virtual disk|60|60 GB free on D: if the WSL disk is there (WSL_DISTRO_NAME is not set; the least free of C: D:)" MU_WSL=1 D_GB=60 TAB_IFS=1
  t "WSL, MU_HOME not created yet: parent dir"  "130|C: (WSL disk)||"      WSL_DISTRO_NAME=Ubuntu-24.04
done
echo; echo "$PASS passed, $FAILN failed"
[ "$FAILN" = 0 ]
