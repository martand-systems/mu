#!/bin/bash
# μ (mu) installer for macOS (Apple Silicon) and Linux (x86_64; Ubuntu 24.04, including WSL2). Builds the full toolchain
# from the locked recipe in this repo, then checks it with `mu doctor` and the benchmark suite. Run it as ./install.sh.
#
#   ./install.sh [options]
#
#   --version V            version label (default: this commit's git tag, else dev-<commit>)
#   --set-default          make this the default version (automatic when there is none)
#   --force                reinstall this version even if it is already installed
#   --rebuild              also rebuild its shared environments from scratch (implies --force)
#   --quick                skip the benchmarks if this recipe commit already passed them on this Mac
#   --skip-tests           skip the benchmarks (mu doctor still runs)
#   --seed-downloads DIR   reuse archives already on disk (checked against the pinned SHA-256)
#   --no-modify-path       don't add ~/.local/bin to your shell's PATH (and don't turn on auto-update)
#   --no-autoupdate        don't turn on the daily auto-update (mu autoupdate on|off; on by default after an install)
#   -v, --verbose          show the full build output instead of the progress view
#   --system               Linux: only install the Ubuntu packages μ needs (apt; run with sudo, once per machine)
#   --user NAME            with --system: let NAME's systemd user services run without a login (auto-update's timer;
#                          default: the user who ran sudo)
#
# Layout (MU_HOME: ~/Library/Mu on macOS, ~/.local/share/mu on Linux):
#   toolchains/<version>/  one per version: recipe snapshot, commands, links to its layers, MANIFEST.json
#   store/                 immutable, content-addressed layers shared by all versions (built once per Mac):
#     app-<name>-<sha>/      an application, unpacked from its pinned archive
#     env-<hash>/            the main environment (conda lock + Python lock)
#     cam-<hash>/            the CAM environment (FreeCAD's Python + its Python lock)
#   logs/                  install logs
# A new version whose locks are unchanged reuses every layer and installs in seconds.
# Caches: ~/Library/Caches/Mu or ~/.cache/mu (MU_CACHE). Commands: ~/.local/bin (MU_LINK_DIR). Apart from those, the
# PATH line in your shell profile, OpenFOAM's temporary read-only mount at /Volumes/OpenFOAM-v2606 (macOS) and the
# Ubuntu packages of --system (Linux), nothing is written outside MU_HOME.
set -Eeuo pipefail
LAYER_FORMAT="mu-layer-2"     # bump when the way the env and CAM layers are built changes (forces new layer hashes)
FLATPAK_FORMAT="mu-flatpak-2" # the same for flatpak app layers (2: built offline from the download cache, without the
                              # unpinned related refs). Other app layers are keyed by their archives' SHA-256 alone.
DISPATCHER_REV=9              # must match MU_DISPATCHER_REV in bin/mu; bump on every change to bin/mu or bin/git-lfs

RECIPE="$(cd "$(dirname "$0")/.." && pwd)"
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)  PLAT=osx-arm64; FAMILY=macos; MU_HOME="${MU_HOME:-$HOME/Library/Mu}"; MU_CACHE="${MU_CACHE:-$HOME/Library/Caches/Mu}" ;;
  Linux-x86_64)  PLAT=linux-64;  FAMILY=linux; [ -n "${LC_ALL:-}${LANG:-}" ] || export LANG=C.UTF-8; MU_HOME="${MU_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/mu}"; MU_CACHE="${MU_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/mu}" ;;
  *) echo "μ runs on macOS (Apple Silicon) and Linux x86_64; this is $(uname -s) $(uname -m)" >&2; exit 2 ;;
esac
MU_LINK_DIR="${MU_LINK_DIR:-$HOME/.local/bin}"
VERSION=""; SEED=""; SET_DEFAULT=0; FORCE=0; REBUILD=0; TESTS=1; QUICK=0; MODIFY_PATH=1; VERBOSE=0; SYSTEM=0
AUTOUPDATE=1; LINGER_USER=""
while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --seed-downloads) SEED="$2"; shift 2 ;;
    --set-default) SET_DEFAULT=1; shift ;;
    --force) FORCE=1; shift ;;
    --rebuild) REBUILD=1; FORCE=1; shift ;;
    --skip-tests) TESTS=0; shift ;;
    --quick) QUICK=1; shift ;;
    --no-modify-path) MODIFY_PATH=0; shift ;;
    -v|--verbose) VERBOSE=1; shift ;;
    --system) SYSTEM=1; shift ;;
    --no-autoupdate) AUTOUPDATE=0; shift ;;
    --user) LINGER_USER="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,/^set -E/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

# ================================================================ Linux system packages (the only root step)
APT_LIST="$RECIPE/toolchain/$PLAT/apt.txt"
apt_missing() { [ -f "$APT_LIST" ] || return 0; local p; for p in $(grep -v '^#' "$APT_LIST" | sed 's/#.*//'); do
  dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || printf '%s ' "$p"; done; return 0; }
apt_install() {   # installs the missing packages (as root, or through sudo)
  local miss; miss="$(apt_missing)"; [ -n "$miss" ] || return 0
  local SUDO=""; [ "$(id -u)" = 0 ] || SUDO="sudo"
  echo "μ needs these Ubuntu packages: $miss" >&2
  $SUDO env DEBIAN_FRONTEND=noninteractive apt-get update -qq
  $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends $miss
}
[ -z "$LINGER_USER" ] || [ "$SYSTEM" = 1 ] || { echo "--user NAME goes with --system (see --help)" >&2; exit 2; }
if [ "$SYSTEM" = 1 ]; then
  [ "$FAMILY" = linux ] || { echo "--system is only needed on Linux" >&2; exit 0; }
  apt_install; echo "system packages: ready"
  # auto-update is a systemd user timer; linger runs it without a login (the WSL compute node's user never logs in)
  lu="${LINGER_USER:-${SUDO_USER:-}}"
  case "$lu" in ""|root) ;; -*|*[!A-Za-z0-9._-]*) echo "--user: invalid user name '$lu'" >&2; exit 2 ;;
    *) if [ "$(id -u)" = 0 ] && id -u "$lu" >/dev/null 2>&1 && loginctl enable-linger "$lu" 2>/dev/null; then
         echo "user services: $lu's run without a login (systemd linger, for μ auto-update)"
       else echo "note: could not enable systemd linger for $lu (not root, no such user, or no systemd): μ auto-update runs only while $lu is logged in"; fi ;;
  esac
  exit 0
fi

# ================================================================ terminal UI
# fd 3: the terminal (progress, results). fd 5: the build log (every command's output goes there).
exec 3>&1 4>&2
if [ -t 1 ] && [ "${TERM:-dumb}" != dumb ] && [ -z "${NO_COLOR:-}" ]; then
  TTY=1; B=$'\033[1m'; DIM=$'\033[2m'; G=$'\033[32m'; RED=$'\033[31m'; Y=$'\033[33m'; N=$'\033[0m'; CLR=$'\r\033[K'
else
  TTY=0; B=""; DIM=""; G=""; RED=""; Y=""; N=""; CLR=""
fi
[ "$VERBOSE" = 1 ] && TTY=0
MAIN_SUBSHELL=$BASH_SUBSHELL
WORK="$(mktemp -d "${TMPDIR:-/tmp}/mu-install.XXXXXX")"
STATUS="$WORK/status"; : > "$STATUS"
mkdir -p "$MU_HOME/logs"
LOG="$MU_HOME/logs/install-$(date +%Y%m%d-%H%M%S).log"; : > "$LOG"
if [ "$VERBOSE" = 1 ]; then exec 5> >(tee -a "$LOG" >&3); else exec 5>>"$LOG"; fi
SPIN_PID=""; STAGE_TITLE=""; STAGE_KEY=""; STAGE_T0=$SECONDS; TIMINGS=""; LOCKED=0

spinner_start() {
  [ "$TTY" = 1 ] || return 0
  ( trap - ERR; set +e
    frames=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏); i=0; dl=""
    while :; do
      if [ $((i % 8)) = 0 ]; then
        kb=$(du -skc "$MU_CACHE"/downloads/*.part "$MU_CACHE"/downloads/*.part.[0-9][0-9] 2>/dev/null | tail -1 | cut -f1)
        if [ "${kb:-0}" -gt 1024 ]; then dl=" · ↓ $((kb / 1024)) MB"; else dl=""; fi
      fi
      printf '%s  %s %-20s %s%s%s%s  %ss' "$CLR" "${frames[$((i % 10))]}" "$STAGE_TITLE" "$DIM" \
        "$(head -c 70 "$STATUS" 2>/dev/null)" "$dl" "$N" "$((SECONDS - STAGE_T0))" >&3
      i=$((i + 1)); sleep 0.12
    done ) &
  SPIN_PID=$!
}
spinner_stop() { [ -n "$SPIN_PID" ] || return 0; kill "$SPIN_PID" 2>/dev/null || true; wait "$SPIN_PID" 2>/dev/null || true; SPIN_PID=""; printf '%s' "$CLR" >&3; }
# stage KEY "Title": starts a timed step (durations land in MANIFEST.json timings_s)
stage() { STAGE_KEY="$1"; STAGE_TITLE="$2"; STAGE_T0=$SECONDS; : > "$STATUS"; printf '\n== %s  [%s]\n' "$2" "$(date +%T)" >&5; spinner_start; }
# ok "detail": finishes the step with a ✓ line
ok() {
  local s=$((SECONDS - STAGE_T0)); TIMINGS="$TIMINGS$STAGE_KEY=$s;"; spinner_stop
  printf '  ✓ %s (%ss)  [%s]\n' "$1" "$s" "$(date +%T)" >&5
  printf '  %s✓%s %-20s %s%-48s%s %4ss\n' "$G" "$N" "$STAGE_TITLE" "$DIM" "$1" "$N" "$s" >&3
}
# note "text": live detail for the current step (spinner line; log)
note() { printf '%s' "$*" > "$STATUS"; printf '  … %s  [%s]\n' "$*" "$(date +%T)" >&5; }
fail() {
  trap - ERR
  # in a subshell (a background job, or a $(...) such as fetch): record the reason for the main shell and exit
  if [ "$BASH_SUBSHELL" != "$MAIN_SUBSHELL" ]; then
    case "$*" in "command failed"*) [ -s "$WORK/lasterr" ] || printf '%s\n' "$*" > "$WORK/lasterr" ;; *) printf '%s\n' "$*" > "$WORK/lasterr" ;; esac
    printf 'error: %s\n' "$*" >&5
    [ -z "${IN_JOB:-}" ] || printf 'error: %s\n' "$*" >&2
    exit 1
  fi
  # a generic "command failed" in the main shell usually follows a subshell's specific reason
  case "$*" in "command failed"*) [ -s "$WORK/lasterr" ] && set -- "$(cat "$WORK/lasterr")" ;; esac
  # for mu autoupdate: the stage that failed (preflight, doctor, bench, ...) and why
  [ -z "${MU_INSTALL_RESULT:-}" ] || printf '%s\t%s\n' "${STAGE_KEY:-preflight}" "$(printf '%s' "$*" | tr '\n\t' '  ')" > "$MU_INSTALL_RESULT" 2>/dev/null || true
  spinner_stop
  printf '  %s✗%s %s\n\n' "$RED" "$N" "${STAGE_TITLE:-install}" >&3
  printf '%sμ install failed:%s %s\n' "$B$RED" "$N" "$*" >&4
  if [ -s "$LOG" ] && [ "$VERBOSE" = 0 ]; then
    printf '\n%slast lines of the log:%s\n' "$DIM" "$N" >&4
    grep -v '^\s*$' "$LOG" | tail -n 25 | sed 's/^/    /' >&4
  fi
  printf '\nfull log: %s\n' "$LOG" >&4
  exit 1
}
trap 'fail "command failed (line $LINENO): $BASH_COMMAND"' ERR
kill_tree() { local c; for c in $(pgrep -P "$1" 2>/dev/null); do kill_tree "$c"; done; kill "$1" 2>/dev/null || true; }
cleanup() {
  spinner_stop
  [ "$TTY" = 1 ] && printf '\033[?25h' >&3
  if [ -n "${JOBS:-}" ]; then
    # background jobs ignore SIGINT: stop their trees, children first (a download's range workers also stop each
    # other's curl on TERM; their partial files stay in the cache for the next run)
    for e in $JOBS; do kill_tree "${e#*:}"; done
    sleep 0.5; detach_under "$STORE"
  fi
  [ "$LOCKED" = 1 ] && rm -rf "$MU_HOME/.install.lock"
  rm -rf "$WORK"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
[ "$TTY" = 1 ] && printf '\033[?25l' >&3   # hide the cursor while the spinner runs

# downloads: fetch (resumable, parallel byte ranges for large files), sha256, sha256_stdin, net_hint
. "$RECIPE/install/download.sh"
# Flatpak apps (Linux): fp_fetch (download cache of pinned OSTree commits), fp_deploy (offline installation), fp
. "$RECIPE/install/flatpak.sh"
# mu_disk_free DIR: free GB (on WSL2, the smaller of the virtual disk and the Windows drive holding it)
. "$RECIPE/install/disk.sh"
clone_tree() { if [ "$FAMILY" = macos ]; then cp -cR "$1" "$2" 2>/dev/null || ditto "$1" "$2"; else cp -a --reflink=auto "$1" "$2"; fi; }
used_mb() { df -Pk "$1" | awk 'NR==2{print int($3/1024)}'; }
human() { awk -v m="$1" 'BEGIN{ if (m >= 1024) printf "%.1f GB", m/1024; else printf "%d MB", m }'; }

# ================================================================ preflight
printf '\n%sμ installer%s\n' "$B" "$N" >&3
stage preflight "Preflight"
if [ "$FAMILY" = macos ]; then
  OSNAME="macOS $(sw_vers -productVersion)"; THIS="this Mac"
  [ "$(sw_vers -productVersion | cut -d. -f1)" -ge 15 ] || fail "macOS 15 or newer is needed (this Mac has $OSNAME): OpenVSP's build requires it"
else
  OSNAME="$(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-Linux}")"; THIS="this machine"
  grep -qi microsoft /proc/version 2>/dev/null && OSNAME="$OSNAME (WSL2)"
  command -v dpkg-query >/dev/null 2>&1 || fail "this Linux installer needs Ubuntu/Debian (dpkg); found $OSNAME"
  miss="$(apt_missing)"
  if [ -n "$miss" ]; then
    if [ "$(id -u)" = 0 ] || sudo -n true 2>/dev/null || [ -t 0 ]; then
      note "Ubuntu packages: $miss"; spinner_stop; apt_install >&5 2>&5 || fail "could not install the Ubuntu packages: $miss"; spinner_start
    else fail "missing Ubuntu packages: $miss. Install them once with:  sudo ./install.sh --system"; fi
  fi
fi
# global lock: one install at a time. A lock is stale if its pid is gone or is no longer an installer.
LK="$MU_HOME/.install.lock"
if ! mkdir "$LK" 2>/dev/null; then
  other="$(cat "$LK/pid" 2>/dev/null || true)"
  if [ -z "$other" ]; then
    sleep 2; other="$(cat "$LK/pid" 2>/dev/null || true)"   # another installer may be between mkdir and writing its pid
    if [ -z "$other" ] && [ -n "$(find "$LK" -maxdepth 0 -mmin -1 2>/dev/null)" ]; then fail "another μ install is starting"; fi
  fi
  if [ -n "$other" ] && ps -p "$other" -o command= 2>/dev/null | grep -q 'install'; then fail "another μ install is running (pid $other)"; fi
  rm -rf "$LK"; mkdir "$LK" || fail "could not take the install lock ($LK)"
fi
LOCKED=1; echo $$ > "$LK/pid"

# recipe commit, tag and origin. Uses a working git if there is one (Apple's /usr/bin/git stub fails until the
# Xcode licence is accepted), else reads .git directly. The install itself never needs git.
GIT=""; for g in $(which -a git 2>/dev/null); do "$g" --version >/dev/null 2>&1 && { GIT="$g"; break; }; done
COMMIT=""; TAG=""; DIRTY=""; ORIGIN=""
if [ -e "$RECIPE/.git" ]; then
  if [ -n "$GIT" ] && COMMIT="$("$GIT" -C "$RECIPE" rev-parse HEAD 2>/dev/null)"; then   # fails e.g. on a clone owned by another user
    TAG="$("$GIT" -C "$RECIPE" describe --tags --exact-match 2>/dev/null || true)"
    DIRTY="$("$GIT" -C "$RECIPE" status --porcelain --untracked-files=no 2>/dev/null | head -1 || true)"
    ORIGIN="$("$GIT" -C "$RECIPE" remote get-url origin 2>/dev/null || true)"
  elif [ -d "$RECIPE/.git" ]; then
    head_ref="$(cat "$RECIPE/.git/HEAD")"
    if [ "${head_ref#ref: }" != "$head_ref" ]; then
      ref="${head_ref#ref: }"
      COMMIT="$(cat "$RECIPE/.git/$ref" 2>/dev/null || awk -v r="$ref" '$2==r{print $1}' "$RECIPE/.git/packed-refs")"
    else COMMIT="$head_ref"; fi
    # packed-refs: "<sha> refs/tags/X" (lightweight) or that line followed by "^<commit>" (annotated, peeled)
    TAG="$(awk -v c="$COMMIT" '/^\^/{ if (substr($1,2)==c && last!="") {print last; exit} next }
          { last=""; if ($2 ~ /^refs\/tags\//) { name=$2; sub("refs/tags/","",name); last=name; if ($1==c) {print name; exit} } }' \
          "$RECIPE/.git/packed-refs" 2>/dev/null || true)"
    ORIGIN="$(awk '/^\[remote "origin"\]/{s=1;next} /^\[/{s=0} s && $1=="url"{print $3; exit}' "$RECIPE/.git/config" 2>/dev/null || true)"
  fi
fi
COMMIT="${COMMIT:-${MU_RECIPE_COMMIT:-unknown}}"
[ -n "$VERSION" ] || VERSION="${TAG:-dev-${COMMIT:0:10}}"
case "$VERSION" in [A-Za-z0-9]*) ;; *) fail "invalid version label '$VERSION'" ;; esac
case "$VERSION" in *[!A-Za-z0-9._-]*|*..*) fail "invalid version label '$VERSION' (letters, digits, . _ - only)" ;; esac
case "$(cd "$RECIPE" && pwd -P)/" in "$(mkdir -p "$MU_HOME" && cd "$MU_HOME" && pwd -P)/"*)
  fail "run the installer from a clone of the mu repo, not from an installed toolchain ($RECIPE)" ;; esac
T="$MU_HOME/toolchains/$VERSION"
STORE="$MU_HOME/store"

if [ -f "$T/MANIFEST.json" ] && [ "$FORCE" = 0 ]; then
  ok "μ $VERSION is already installed"
  if [ "$SET_DEFAULT" = 1 ]; then echo "$VERSION" > "$MU_HOME/default"; fi
  printf '\n  Nothing to do. Default: %s. Reinstall with --force.\n\n' "$(cat "$MU_HOME/default" 2>/dev/null || echo none)" >&3
  exit 0
fi
mkdir -p "$MU_CACHE/downloads" "$MU_CACHE/pkgs" "$MU_HOME/bin" "$MU_HOME/state/home" "$STORE"
DISK0=$(used_mb "$MU_HOME")
T_START=$SECONDS

# recipe snapshot (the source of every layer hash below)
rm -rf "${T:?}"
mkdir -p "$T/bin" "$T/libexec" "$T/apps"
touch "$T/.installing"
mkdir -p "$T/recipe"
( cd "$RECIPE" && tar --exclude .git --exclude __pycache__ --exclude .DS_Store --exclude './toolchain/pip-stubs/*/build' -cf - . ) \
  | ( cd "$T/recipe" && tar -xf - )
R="$T/recipe"
cp "$R/wrappers/$FAMILY/"* "$T/bin/"
chmod +x "$T/bin/"* "$R/bin/"*

# apps.toml -> TSV for this platform's entries: 1 name 2 file 3 url 4 sha256 5 kind 6 source_item 7 target
#   8 python 9 pylib 10 runtime. file/url/sha256 may be space-separated lists (several archives unpacked into one layer).
awk -F'"' -v plat="$PLAT" '
  function out() { if (name!="" && file!="") print name"\t"file"\t"url"\t"sha"\t"kind"\t"item"\t"target"\t"py"\t"pylib"\t"rt }
  /^\[/ { out(); name=""; file=url=sha=kind=item=target=py=pylib=rt=""
           if (index($0, "." plat "]")) { name=$0; sub(/^\[/,"",name); sub("\\." plat "\\].*$","",name) } next }
  name!="" && /^file /        { file=$2 }   name!="" && /^url /    { url=$2 }   name!="" && /^sha256 / { sha=$2 }
  name!="" && /^kind /        { kind=$2 }   name!="" && /^source_item / { item=$2 } name!="" && /^target / { target=$2 }
  name!="" && /^python /      { py=$2 }     name!="" && /^pylib /  { pylib=$2 } name!="" && /^runtime / { rt=$2 }
  END { out() }
' "$R/toolchain/apps.toml" > "$WORK/apps.tsv"
app() { awk -F'\t' -v n="$1" -v c="$2" '$1==n{print $c}' "$WORK/apps.tsv"; }   # app NAME COLUMN
appkey() {   # app-<name>-<16 hex>: the archive hash; for several archives, a build or a flatpak, a hash over all its inputs
  local s; s="$(app "$1" 4)"; case "$s" in *" "*) s="$(printf '%s' "$s" | sha256_stdin)" ;; esac
  if [ "$(app "$1" 5)" = build ]; then   # source + build recipe + the Python it is built for
    s="$(printf '%s\n%s\n%s\n' "$s" "$(sha256 "$R/$(app "$1" 6)")" "$(grep -E '/python-3[.0-9]+-' "$R/toolchain/$PLAT/env.explicit.txt")" | sha256_stdin)"
  elif [ "$(app "$1" 5)" = flatpak ]; then   # the app's commit + the runtime's ref@commit + how the layer is built
    s="$(printf '%s\n%s\n%s\n' "$FLATPAK_FORMAT" "$s" "$(app "$1" 10)" | sha256_stdin)"
  fi
  echo "app-$1-$(printf '%s' "$s" | cut -c1-16)"
}

# layer_key PREFIX "extra strings" PATH... : content hash of the inputs (paths relative to the recipe)
layer_key() {
  local prefix="$1" extra="$2"; shift 2
  { printf '%s\n%s\n%s\n' "$LAYER_FORMAT" "$extra" "$PLAT"
    for p in "$@"; do
      [ -e "$R/$p" ] || continue   # optional inputs (e.g. a platform's own pip stubs)
      ( cd "$R" && find "$p" -type f ! -name .DS_Store ! -path '*/__pycache__/*' ! -path '*/build/*' ! -path '*.egg-info/*' \
          | LC_ALL=C sort | while IFS= read -r f; do printf '%s  %s\n' "$(sha256 "$f")" "$f"; done )
    done
  } | sha256_stdin | cut -c1-16 | sed "s/^/$prefix-/"
}
OS=toolchain/$PLAT
# where OpenVSP's Python packages come from: the app itself (macOS), or the API built for the env's Python (Linux)
PYAPI=openvsp; [ -z "$(app openvsp-api 1)" ] || PYAPI=openvsp-api
ENV_KEY="$(layer_key env "$(appkey $PYAPI) mm:$(app micromamba 4) uv:$(app uv 4)" \
  $OS/env.explicit.txt $OS/env.pip.txt toolchain/pins.toml toolchain/patches toolchain/pip-stubs $OS/pip-stubs)"
CAM_KEY="$(layer_key cam "$(appkey freecad) uv:$(app uv 4)" $OS/cam-venv.pip.txt toolchain/pip-stubs)"
APP_NAMES="$(awk -F'\t' '$5!="binary" && $5!="tar.bin" && $5!="gz.bin" && $1!="uv"{print $1}' "$WORK/apps.tsv")"
LAYERS="$ENV_KEY $CAM_KEY"; for a in $APP_NAMES; do LAYERS="$LAYERS $(appkey "$a")"; done
printf '%s\n' $LAYERS > "$T/LAYERS"   # written early: `mu gc` never removes layers an install is using
# download-cache entries this version pins (mu gc keeps them while the version is installed): "flatpak REF@COMMIT"
awk -F'\t' '$5 == "flatpak" { print "flatpak " $2 "@" $4; if ($10 != "") print "flatpak " $10 }' "$WORK/apps.tsv" > "$T/CACHE_REFS"
# the download cache is shared by every MU_HOME: record this one in it, so mu gc in another keeps these pins too
[ ! -s "$T/CACHE_REFS" ] || fp_register
have_layer() { [ -f "$STORE/$1/.mu-layer" ]; }
# layers are read-only once built: nothing (a tool's bytecode cache, a stray pip install) can change a shared layer
seal() { chmod -R a-w "$1"; }
# detach_under DIR: detach disk images an interrupted unpack left mounted below DIR (never rm through a mount)
detach_under() {
  command -v hdiutil >/dev/null 2>&1 || return 0
  mount | sed -E 's/^.* on (.*) \([^()]*\)$/\1/' | while IFS= read -r m; do
    case "$m" in "$1"/*) hdiutil detach "$m" -quiet 2>/dev/null || hdiutil detach "$m" -force -quiet 2>/dev/null || true ;; esac
  done
}
# (only directories need to be writable to delete: file modes are never changed)
unseal_rm() { [ -e "$1" ] || return 0; detach_under "$1"; find "$1" -type d ! -perm -u+w -exec chmod u+w {} + 2>/dev/null || true; rm -rf "${1:?}"; }
detach_under "$STORE"
for d in "$STORE"/.*.partial; do [ -e "$d" ] && unseal_rm "$d"; done
if [ "$REBUILD" = 1 ]; then
  for k in $LAYERS; do
    users="$(for v in "$MU_HOME/toolchains"/*/; do v="$(basename "$v")"; [ "$v" = "$VERSION" ] && continue
             grep -qx "$k" "$MU_HOME/toolchains/$v/LAYERS" 2>/dev/null && printf '%s ' "$v"; done; true)"
    [ -z "$users" ] || fail "--rebuild: layer $k is shared with installed versions: $users(remove them first: mu remove <version>)"
    unseal_rm "$STORE/$k"
  done
fi
MISSING=""; for k in $LAYERS; do have_layer "$k" || MISSING="$MISSING $k"; done

mu_disk_free "$MU_HOME"; avail_gb="$MU_FREE_GB"; disk_where="$MU_FREE_WHERE"
need_gb=1
case "$MISSING" in *env-*) need_gb=$((need_gb + 6)) ;; esac
case "$MISSING" in *cam-*) need_gb=$((need_gb + 2)) ;; esac
case "$MISSING" in *app-*) need_gb=$((need_gb + 9)) ;; esac
[ -z "$avail_gb" ] || [ "$avail_gb" -ge "$need_gb" ] \
  || fail "need about $need_gb GB free on this disk (have $avail_gb GB${disk_where:+ on $disk_where})"
n_missing=$(echo $MISSING | wc -w | tr -d ' '); n_layers=$(echo $LAYERS | wc -w | tr -d ' ')
ok "$OSNAME · ${avail_gb:-?} GB free${disk_where:+ on $disk_where} · $((n_layers - n_missing)) of $n_layers layers already on $THIS"
# WSL, Windows drive not confirmed: its figure is shown, and warned about when low, but never enforced (install/disk.sh)
if [ -n "$MU_FREE_NOTE" ]; then
  printf '  note: %s\n' "$MU_FREE_NOTE" >&5
  if [ "$MU_FREE_NOTE_GB" -ge "$need_gb" ]; then printf '    %s%s%s\n' "$DIM" "$MU_FREE_NOTE" "$N" >&3
  else printf '  %s!%s %s; this install needs about %s GB\n' "$Y" "$N" "$MU_FREE_NOTE" "$need_gb" >&3; fi
fi
printf '  %sinstalling μ %s%s %s→ %s%s\n' "$B" "$VERSION" "$N" "$DIM" "${T/#$HOME/~}" "$N" >&3

# ================================================================ downloads
# fetch NAME URL SHA256 -> prints the cached path (install/download.sh). Downloads once (resumed after failures; large
# files as parallel byte ranges), verifies SHA-256, can reuse a seed folder.

# app_layer NAME: unpack an app into the store (atomic: build in .partial, then rename). Kinds:
#   zip, tar (any tar compression), deb (one or more .debs, dpkg-deb -x, no root), appimage (--appimage-extract, no
#   FUSE), dmg (macOS; expanded on every core by dmg2raw.py), flatpak (Linux; pinned commits, installed at its final path)
app_layer() {
  local name="$1" files urls shas kind item target key tmp f mp src i n
  files="$(app "$1" 2)"; urls="$(app "$1" 3)"; shas="$(app "$1" 4)"; kind="$(app "$1" 5)"; item="$(app "$1" 6)"; target="$(app "$1" 7)"
  key="$(appkey "$name")"
  have_layer "$key" && return 0
  if [ "$kind" = flatpak ]; then flatpak_layer "$name"; return; fi
  if [ "$kind" = build ]; then build_layer "$name"; return; fi
  # fetch every archive (in parallel when there are several)
  set -- $files; n=$#; local fs=""
  for i in $(seq 1 "$n"); do
    ( fetch "$(echo $files | cut -d' ' -f"$i")" "$(echo $urls | cut -d' ' -f"$i")" "$(echo $shas | cut -d' ' -f"$i")" >/dev/null ) &
  done
  wait || true
  for i in $(seq 1 "$n"); do fs="$fs $(fetch "$(echo $files | cut -d' ' -f"$i")" "$(echo $urls | cut -d' ' -f"$i")" "$(echo $shas | cut -d' ' -f"$i")")"; done
  f="${fs# }"
  echo "unpacking $name ($kind)"
  tmp="$STORE/.$key.partial"; unseal_rm "$tmp"; mkdir -p "$tmp/x"
  case "$kind" in
    zip)  if [ "$FAMILY" = macos ]; then ditto -x -k "$f" "$tmp/x"; else unzip -q "$f" -d "$tmp/x"; fi ;;
    tar)  tar -xf "$f" -C "$tmp/x" ;;
    deb)  for i in $f; do dpkg-deb -x "$i" "$tmp/x"; done ;;
    appimage)
      cp "$f" "$tmp/app.AppImage"; chmod +x "$tmp/app.AppImage"
      ( cd "$tmp/x" && "$tmp/app.AppImage" --appimage-extract >/dev/null ) || fail "could not extract $files"
      rm -f "$tmp/app.AppImage" ;;
    dmg)
      mp="$tmp/mnt"; mkdir -p "$mp"
      # hdiutil expands a compressed image on one core (FreeCAD: ~140 s). dmg2raw.py expands its chunks on every core
      # into a raw image, which then attaches without decompression (~25 s). Falls back to hdiutil if it can't.
      local img="$f" class=""
      if [ -n "$BOOT_PY" ] && "$BOOT_PY" "$R/install/dmg2raw.py" "$f" "$tmp/raw.img"; then img="$tmp/raw.img"; class="-imagekey diskimage-class=CRawDiskImage"
      else rm -f "$tmp/raw.img"; fi
      # -noverify: the archive's SHA-256 was just checked, so skip hdiutil's own full read of the image
      if ! { yes || true; } | hdiutil attach $class -nobrowse -readonly -noautoopen -noverify -mountpoint "$mp" "$img" >/dev/null 2>&1; then
        [ "$img" != "$f" ] || fail "hdiutil could not attach $files"
        echo "raw image did not attach; falling back to hdiutil on the DMG"; rm -f "$tmp/raw.img"
        { yes || true; } | hdiutil attach -nobrowse -readonly -noautoopen -noverify -mountpoint "$mp" "$f" >/dev/null 2>&1 || fail "hdiutil could not attach $files"
      fi
      if [ -e "$mp/$item" ]; then src="$mp/$item"; else src="$(find "$mp" -maxdepth 2 -name "$item" -print -quit)"; fi
      [ -n "$src" ] || { hdiutil detach "$mp" -quiet || true; fail "no $item in $files"; }
      ditto "$src" "$tmp/$target"
      hdiutil detach "$mp" -quiet || hdiutil detach "$mp" -force -quiet
      rmdir "$mp"; rm -f "$tmp/raw.img"; item="" ;;
    *) fail "unknown app kind '$kind' for $name" ;;
  esac
  if [ -n "$item" ]; then
    [ -e "$tmp/x/$item" ] || fail "no $item in $files"
    mv "$tmp/x/$item" "$tmp/$target"
  fi
  rm -rf "${tmp:?}/x"
  printf 'app=%s\nsha256=%s\nbuilt=%s\n' "$name" "$shas" "$(date '+%Y-%m-%d %H:%M:%S')" > "$tmp/.mu-layer"
  seal "$tmp"; unseal_rm "$STORE/$key"; mv "$tmp" "$STORE/$key"
}

# build_layer NAME: build from a pinned source archive with the recipe's build script (e.g. OpenVSP's Python API on
# Linux). Needs a Python with numpy of the env's version: the bootstrap one, else the env's once its conda part exists.
build_layer() {
  local name="$1" key tmp f bdir py
  key="$(appkey "$name")"
  f="$(fetch "$(app "$1" 2)" "$(app "$1" 3)" "$(app "$1" 4)")"
  py="$BOOT_PY"; [ -n "$py" ] || { while [ ! -s "$WORK/python" ]; do sleep 1; done; py="$(cat "$WORK/python")"; }
  tmp="$STORE/.$key.partial"; unseal_rm "$tmp"; mkdir -p "$tmp"
  bdir="$MU_CACHE/build/$key"; rm -rf "$bdir"
  echo "building $name with $(getconf _NPROCESSORS_ONLN 2>/dev/null || echo ?) cores"
  PYTHON="$py" JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)" bash "$R/$(app "$1" 6)" "$f" "$bdir" "$tmp/$(app "$1" 7)" \
    || fail "building $name failed (see the log)"
  rm -rf "$bdir"
  printf 'app=%s\nsource=%s\nbuilt=%s\n' "$name" "$(app "$1" 4)" "$(date '+%Y-%m-%d %H:%M:%S')" > "$tmp/.mu-layer"
  seal "$tmp"; unseal_rm "$STORE/$key"; mv "$tmp" "$STORE/$key"
}

# flatpak_layer NAME (Linux): a private flatpak installation holding exactly the app and its runtime at their pinned
# commits (install/flatpak.sh). Each commit is downloaded once per machine into the download cache, then the layer is
# built from the cache with no network, as a copy (no inode shared with the cache). Built at its final path; complete
# when .mu-layer exists.
flatpak_layer() {
  local name="$1" ref url commit rt key L fkey="$R/toolchain/$PLAT/flathub.gpg"
  ref="$(app "$1" 2)"; url="$(app "$1" 3)"; commit="$(app "$1" 4)"; rt="$(app "$1" 10)"; key="$(appkey "$1")"
  command -v flatpak >/dev/null 2>&1 || fail "flatpak is not installed (it comes with: sudo ./install.sh --system)"
  [ -S /run/dbus/system_bus_socket ] || [ -S /var/run/dbus/system_bus_socket ] \
    || fail "flatpak needs the system D-Bus, which is not running (in a container: dbus-daemon --system --fork)"
  fp_fetch "${rt%@*}" "${rt##*@}" "$url" "$fkey"
  fp_fetch "$ref" "$commit" "$url" "$fkey"
  echo "installing $name from the download cache (offline)"
  L="$STORE/$key"; unseal_rm "$L"; mkdir -p "$L"
  fp_deploy "$L/$(app "$1" 7)" "$url" "$fkey" "$rt" "$ref@$commit"
  printf 'app=%s\nref=%s\ncommit=%s\nruntime=%s\nformat=%s\nbuilt=%s\n' "$name" "$ref" "$commit" "$rt" "$FLATPAK_FORMAT" \
    "$(date '+%Y-%m-%d %H:%M:%S')" > "$L/.mu-layer"
  seal "$L"   # its own copy of every file (fp_deploy), so the cache's file modes are not touched
}

# tools needed to build: micromamba and uv (small, pinned binaries)
f="$(fetch "$(app micromamba 2)" "$(app micromamba 3)" "$(app micromamba 4)" 2>&5)"
install -m 755 "$f" "$T/libexec/micromamba"
f="$(fetch "$(app uv 2)" "$(app uv 3)" "$(app uv 4)" 2>&5)"
tar -xzf "$f" -C "$WORK"
install -m 755 "$WORK/$(app uv 6)/uv" "$T/libexec/uv"
UV="$T/libexec/uv"
# the job layer's relay client (docs/design/job-layer.md §5.4): a pinned binary in libexec, in no layer
f="$(fetch "$(app chisel 2)" "$(app chisel 3)" "$(app chisel 4)" 2>&5)"
gunzip -c "$f" > "$T/libexec/chisel.tmp" && chmod 755 "$T/libexec/chisel.tmp" && mv "$T/libexec/chisel.tmp" "$T/libexec/chisel"
CHISEL_VERSION="$(awk -F'"' '/^\[/ { s = ($0 == "[chisel]") } s && /^version / { print $2; exit }' "$R/toolchain/apps.toml")"
[ "$("$T/libexec/chisel" --version 2>&1)" = "$CHISEL_VERSION" ] || fail "the chisel client does not report version $CHISEL_VERSION"

export MU_TOOLCHAIN="$T" MU_HOME MU_CACHE MU_INSTALLING=1
set +u; . "$R/lib/mu-env.sh"; set -u
# uv links installed files to its cache: APFS clones on macOS; hard links on Linux (ext4 has no clones, and layers are
# read-only, so shared inodes are safe)
export UV_NO_CONFIG=1 UV_PYTHON_DOWNLOADS=never
if [ "$FAMILY" = macos ]; then export UV_LINK_MODE=clone; else export UV_LINK_MODE=hardlink; fi
# local paths go to the lock files as percent-encoded file:// URLs (safe with spaces in paths)
furl() { printf 'file://%s' "$(printf '%s' "$1" | sed -e 's/%/%25/g' -e 's/ /%20/g' -e 's/#/%23/g' -e 's/&/%26/g' -e 's/|/%7C/g')"; }
sed_esc() { printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'; }   # make a string safe as a sed s||| replacement
TTC="$(awk -F'"' '/^\[text-to-cad\]/{s=1} s && /^repo/{r=$2} s && /^commit/{c=$2} END{print r"/archive/"c".zip"}' "$R/toolchain/pins.toml")"
subst() { sed -e "s|{MU_RECIPE_URL}|$(sed_esc "$(furl "$R")")|g" -e "s|{MU_APPS_URL}|$(sed_esc "$(furl "$1")")|g" \
              -e "s|{TEXT_TO_CAD_ARCHIVE}|$(sed_esc "$TTC")|g" "$2"; }
# uvi PYTHON REQS: install a Python lock (no resolution: every pin is exact). PyPI pins carry SHA-256 hashes and
# install with --require-hashes; URL/path pins (commit-pinned archives, files from pinned apps) install after them.
# Offline first, so an install with warm caches never touches the network; falls back to fetching what is missing.
# On a cold cache the offline attempt fails with a long "requirements are unsatisfiable" report: expected, and marked
# as such in the log so that it is not mistaken for the real error (a real failure is the online attempt's output).
uvi() {
  local py="$1" reqs="$2" part flags
  grep -e '--hash=' "$reqs" > "$reqs.hashed" || true
  grep -v -e '--hash=' "$reqs" | grep -v '^[[:space:]]*\(#\|$\)' > "$reqs.plain" || true
  for part in hashed plain; do
    [ -s "$reqs.$part" ] || continue
    flags="--no-deps --compile-bytecode"; [ "$part" = hashed ] && flags="$flags --require-hashes"
    # its lines are indented and tagged, so that none can pass for the job's "error: " line (see wait_job)
    echo "--- uv: offline attempt ($(basename "$reqs").$part, cached packages only; failing here is expected on a cold cache)"
    if "$UV" pip install --python "$py" $flags --offline -r "$reqs.$part" 2>&1 | sed 's/^/    (offline) /'; then
      echo "--- uv: offline attempt installed everything"
    else
      echo "--- uv: end of the expected offline failure above (some packages are not cached yet); online install:"
      "$UV" pip install --python "$py" $flags -r "$reqs.$part" \
        || fail "Python packages could not be installed ($(net_hint https://pypi.org/simple/))"
      echo "--- uv: online install done"
    fi
  done
}

# cam_layer: the CAM environment: a venv on FreeCAD's Python 3.11 with the CAM lock (needs only FreeCAD and uv,
# so it builds in the background while the main environment is still building)
cam_layer() {
  local L="$STORE/$CAM_KEY" FCAPP="$STORE/$(appkey freecad)/$(app freecad 7)" CSP
  unseal_rm "$L"
  HOME="$MU_STATE/home" "$FCAPP/$(app freecad 8)" -m venv --system-site-packages "$L"
  subst "$STORE" "$R/$OS/cam-venv.pip.txt" > "$WORK/cam.pip.txt"
  uvi "$L/bin/python" "$WORK/cam.pip.txt"
  # FreeCAD's own modules (FreeCAD, Part, Path/CAM) live in the app bundle's lib folder, not in its site-packages
  CSP="$("$L/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
  echo "$FCAPP/$(app freecad 9)" > "$CSP/freecad.pth"
  ln -sfn "$FCAPP" "$T/apps/$(app freecad 7)"; ln -sfn "$L" "$T/cam-venv"
  "$T/bin/cam-python" -c 'import FreeCAD, ocp_freecad_cam' || fail "the CAM environment cannot import FreeCAD / ocp_freecad_cam"
  printf 'layer=%s\nrecipe=%s\nbuilt=%s\n' "$CAM_KEY" "$COMMIT" "$(date '+%Y-%m-%d %H:%M:%S')" > "$L/.mu-layer"
  seal "$L"
}
freecad_then_cam() { app_layer freecad; have_layer "$CAM_KEY" || cam_layer; }

# boot_python: a small Python (from the conda lock) for dmg2raw.py, cached per Mac, so the DMGs can expand while
# the main environment builds. Empty output means none: DMGs then expand with hdiutil.
boot_python() {
  local spec key d
  spec="$(grep -E '/(python|libpython|python_abi|cpython|python-gil|bzip2|libexpat|libffi|liblzma|libmpdec|libsqlite|libzlib|ncurses|openssl|readline|tk|tzdata|ca-certificates|libcxx|libgcc|libgcc-ng|libstdcxx|libstdcxx-ng|libgomp|_libgcc_mutex|_openmp_mutex|llvm-openmp|ld_impl_linux-64|libuuid|libnsl|libxcrypt|numpy|libblas|libcblas|liblapack|libopenblas|libgfortran|libgfortran5|libgfortran-ng)-[0-9]' "$R/$OS/env.explicit.txt")"
  key="$(printf '%s' "$spec" | /usr/bin/openssl dgst -sha256 -r | cut -c1-12)"; d="$MU_CACHE/bootpy-$key"
  if [ ! -f "$d/.ok" ]; then
    rm -rf "$MU_CACHE"/bootpy-*; printf '@EXPLICIT\n%s\n' "$spec" > "$WORK/boot.txt"
    micromamba create -y -q -p "$d" --offline --file "$WORK/boot.txt" >&5 2>&5 \
      || { rm -rf "$d"; micromamba create -y -q -p "$d" --file "$WORK/boot.txt" >&5 2>&5; } || { rm -rf "$d"; return 0; }
    "$d/bin/python" -c 'import bz2, zlib, plistlib, concurrent.futures, numpy' >&5 2>&5 || { rm -rf "$d"; return 0; }
    touch "$d/.ok"
  fi
  echo "$d/bin/python"
}

# ================================================================ layers: main environment (foreground) while apps and
# the CAM environment build in the background, in parallel
stage env "Main environment"
BOOT_PY=""; have_layer "$ENV_KEY" && BOOT_PY="$STORE/$ENV_KEY/bin/python"
[ -n "$BOOT_PY" ] || for a in $APP_NAMES; do
  case "$(app "$a" 5)" in dmg|build) have_layer "$(appkey "$a")" || { note "bootstrap Python (for parallel unpacking and builds)"; BOOT_PY="$(boot_python)"; break; } ;; esac
done
JOBS=""; APP_NEW=""
start_job() { local n="$1"; shift; ( IN_JOB=1; "$@" ) > "$WORK/job-$n.log" 2>&1 & JOBS="$JOBS $n:$!"; }
for a in $APP_NAMES; do
  have_layer "$(appkey "$a")" && continue
  APP_NEW="$APP_NEW $a"
  if [ "$a" = freecad ]; then start_job freecad freecad_then_cam; else start_job "$a" app_layer "$a"; fi
done
have_layer "$(appkey freecad)" && ! have_layer "$CAM_KEY" && start_job cam cam_layer
wait_job() {   # wait_job NAME: block until that background job is done (no-op if there is none)
  local e
  for e in $JOBS; do
    [ "${e%%:*}" = "$1" ] || continue
    if ! wait "${e#*:}"; then cat "$WORK/job-$1.log" >&5; rm -f "$WORK/lasterr"; fail "$1: $( { grep '^error: ' "$WORK/job-$1.log" | grep -v '^error: command failed' || grep '^error: ' "$WORK/job-$1.log" || echo "error: failed (see the log)"; } | head -1 | sed 's/^error: //')"; fi
    cat "$WORK/job-$1.log" >&5
  done
  JOBS="$(for e in $JOBS; do [ "${e%%:*}" = "$1" ] || printf '%s ' "$e"; done)"
}
running() { local e n=""; for e in $JOBS; do n="$n ${e%%:*}"; done; echo "${n# }"; }

if have_layer "$ENV_KEY"; then
  ok "reused ${ENV_KEY}"
else
  L="$STORE/$ENV_KEY"; unseal_rm "$L"
  n_conda=$(grep -c '^https' "$R/$OS/env.explicit.txt"); n_pip=$(grep -c '^[A-Za-z]' "$R/$OS/env.pip.txt")
  note "conda packages ($n_conda, locked)${JOBS:+ · in parallel: $(running)}"
  if ! micromamba create -y -q -p "$L" --offline --file "$R/$OS/env.explicit.txt" >&5 2>&5; then
    note "downloading conda packages (first install only)"
    unseal_rm "$L"
    micromamba create -y -q -p "$L" --file "$R/$OS/env.explicit.txt" >&5 2>&5 \
      || fail "conda packages could not be installed ($(net_hint https://conda.anaconda.org/conda-forge/))"
  fi
  "$L/bin/python" -c 'import OCP, vtk, gmsh' >&5 2>&5 || fail "the conda environment does not import OCP / VTK / gmsh"
  echo "$L/bin/python" > "$WORK/python"   # a build waiting for a Python can start now
  case " $(running) " in *" $PYAPI "*) note "waiting for $PYAPI (its Python packages are part of this layer)" ;; esac
  wait_job "$PYAPI"
  note "Python packages ($n_pip, locked)${JOBS:+ · in parallel: $(running)}"
  # setuptools writes build files into a source folder: build OpenVSP's Python packages from a copy outside the
  # (read-only) app layer, at a stable path so uv's wheel cache still recognises them
  ovsp="$MU_CACHE/build/$(appkey $PYAPI)"; ovt="$(app $PYAPI 7)"
  if [ ! -d "$ovsp/$ovt/python" ]; then
    rm -rf "$ovsp" "$ovsp.tmp"; mkdir -p "$ovsp.tmp/$ovt"; clone_tree "$STORE/$(appkey $PYAPI)/$ovt/python" "$ovsp.tmp/$ovt/python"
    chmod -R u+w "$ovsp.tmp"; mv "$ovsp.tmp" "$ovsp"
  fi
  subst "$ovsp" "$R/$OS/env.pip.txt" > "$WORK/env.pip.txt"
  uvi "$L/bin/python" "$WORK/env.pip.txt" >&5 2>&5
  SP="$("$L/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
  cp "$R/toolchain/patches/"*.pth "$SP/"
  # the mu Python package comes from whichever toolchain is active, so this layer stays version-independent
  echo 'import os, sys; _t = os.environ.get("MU_TOOLCHAIN"); _t and sys.path.append(_t + "/recipe/src")' > "$SP/mu-src.pth"
  "$UV" pip check --python "$L/bin/python" >&5 2>&5 || "$L/bin/python" -m pip check >&5 2>&5 || fail "dependency check failed in the main environment"
  printf 'layer=%s\nrecipe=%s\nbuilt=%s\n' "$ENV_KEY" "$COMMIT" "$(date '+%Y-%m-%d %H:%M:%S')" > "$L/.mu-layer"
  seal "$L"
  ok "built: $n_conda conda + $n_pip Python packages"
fi
ln -sfn "$STORE/$ENV_KEY" "$T/env"
unset MU_ACTIVE; set +u; . "$R/lib/mu-env.sh"; set -u   # again, now that env/ exists (runs its activate.d scripts)

stage cam "CAM environment"
CAM_NEW=0; case " $(running) " in *" freecad "*|*" cam "*) CAM_NEW=1 ;; esac
[ "$CAM_NEW" = 1 ] && note "FreeCAD Python venv + CAM packages"
wait_job freecad; wait_job cam
have_layer "$CAM_KEY" || fail "the CAM environment was not built"
ln -sfn "$STORE/$CAM_KEY" "$T/cam-venv"
if [ "$CAM_NEW" = 1 ]; then ok "built: FreeCAD Python 3.11 + $(grep -c '^[A-Za-z]' "$R/$OS/cam-venv.pip.txt") packages"; else ok "reused ${CAM_KEY}"; fi

stage apps "Applications"
note "unpacking into the shared store"
for a in $APP_NAMES; do
  wait_job "$a"
  have_layer "$(appkey "$a")" || fail "$a is missing from the store"
  ln -sfn "$STORE/$(appkey "$a")/$(app "$a" 7)" "$T/apps/$(app "$a" 7)"
done
n_new=$(echo $APP_NEW | wc -w | tr -d ' '); n_apps=$(echo $APP_NAMES | wc -w | tr -d ' ')
if [ "$n_new" = 0 ]; then ok "$n_apps apps, all reused"; else ok "$n_apps apps ($n_new unpacked in parallel, $((n_apps - n_new)) reused)"; fi

# ================================================================ this version: text-to-cad, commands, PATH
stage commands "Commands"
note "registering text-to-cad (per-version state)"
rm -rf "${MU_STATE:?}/cad/$VERSION"   # a reinstall starts from a clean registration
cad doctor -y --no-server >"$WORK/cad.log" 2>&1 || { cat "$WORK/cad.log" >&5; fail "cad doctor -y failed"; }
cat "$WORK/cad.log" >&5
grep -q "registered" "$WORK/cad.log" || fail "text-to-cad did not register this installation"
# the dispatcher is shared by all versions: never replace a newer one with an older one
cur_rev="$(sed -n 's/^MU_DISPATCHER_REV=\([0-9]*\).*/\1/p' "$MU_HOME/bin/mu" 2>/dev/null | head -1 || true)"
if [ "${cur_rev:-0}" -lt "$DISPATCHER_REV" ] || [ ! -x "$MU_HOME/bin/git-lfs" ]; then
  install -m 755 "$R/bin/mu" "$MU_HOME/bin/mu"
  install -m 755 "$R/bin/git-lfs" "$MU_HOME/bin/git-lfs"
fi
case "$ORIGIN" in https://*|git@*|ssh://*) echo "$ORIGIN" > "$MU_HOME/source" ;; esac
if [ "$SET_DEFAULT" = 1 ] || [ ! -f "$MU_HOME/default" ] || [ ! -d "$MU_HOME/toolchains/$(cat "$MU_HOME/default")" ]; then
  echo "$VERSION" > "$MU_HOME/default"
fi
mkdir -p "$MU_LINK_DIR"
ln -sfn "$MU_HOME/bin/mu" "$MU_LINK_DIR/mu"
ln -sfn "$MU_HOME/bin/git-lfs" "$MU_LINK_DIR/git-lfs"   # plain `git` in any shell can push LFS repos
PATH_MSG=""
case ":${MU_USER_PATH:-$PATH}:" in
  *":$MU_LINK_DIR:"*) ;;
  *) if [ "$MODIFY_PATH" = 1 ]; then
       # Ubuntu's ~/.profile already adds ~/.local/bin (when it exists) at the next login
       case "$FAMILY-$(basename "${SHELL:-/bin/zsh}")" in macos-bash) rc="$HOME/.bash_profile" ;; *-zsh) rc="${ZDOTDIR:-$HOME}/.zprofile" ;; *) rc="$HOME/.profile" ;; esac
       if ! grep -qs "$MU_LINK_DIR\|\.local/bin" "$rc"; then
         lp="$MU_LINK_DIR"; [ "$lp" = "$HOME/.local/bin" ] && lp='$HOME/.local/bin'
         printf '\n# added by the μ installer\nexport PATH="%s:$PATH"\n' "$lp" >> "$rc"
         PATH_MSG="added ${MU_LINK_DIR/#$HOME/~} to PATH in ${rc/#$HOME/~}: open a new terminal"
       fi
     else PATH_MSG="${MU_LINK_DIR/#$HOME/~} is not on your PATH: add it to use mu"; fi ;;
esac
ok "mu, git-lfs → ${MU_LINK_DIR/#$HOME/~} · default $(cat "$MU_HOME/default")"

# ================================================================ checks
stage doctor "Doctor"
note "starting every tool"
if MU_VERSION="$VERSION" "$MU_HOME/bin/mu" doctor > "$WORK/doctor.txt" 2>&1; then
  cat "$WORK/doctor.txt" >&5
  ok "$(grep -c '^PASS' "$WORK/doctor.txt")/$(grep -cE '^(PASS|FAIL)' "$WORK/doctor.txt") tools start and report the pinned versions"
else
  cat "$WORK/doctor.txt" >&5; grep '^FAIL' "$WORK/doctor.txt" | sed 's/^/    /' >&4 || true
  fail "mu doctor found problems"
fi
PASSED="$MU_STATE/bench/passed/$COMMIT"
if [ "$TESTS" = 1 ] && [ "$QUICK" = 1 ] && [ -z "$DIRTY" ] && [ "$COMMIT" != unknown ] && [ -f "$PASSED" ]; then
  stage bench "Benchmarks"; ok "skipped: recipe ${COMMIT:0:10} passed on this Mac $(cat "$PASSED")"
elif [ "$TESTS" = 1 ]; then
  stage bench "Benchmarks"
  note "known-answer checks: CAD, FEA, aero, CFD, slicing, CAM (in parallel)"
  rm -rf "$MU_STATE/bench/install-$VERSION"
  if MU_VERSION="$VERSION" "$MU_HOME/bin/mu" test --workdir "$MU_STATE/bench/install-$VERSION" > "$WORK/bench.txt" 2>&1; then
    cat "$WORK/bench.txt" >&5
    "$T/bin/openfoam-eject" >/dev/null 2>&1 || true
    [ -n "$DIRTY" ] || [ "$COMMIT" = unknown ] || { mkdir -p "$(dirname "$PASSED")"; date "+%Y-%m-%d %H:%M" > "$PASSED"; }
    ok "$(grep -c '^PASS' "$WORK/bench.txt")/$(grep -cE '^(PASS|FAIL)' "$WORK/bench.txt") passed, incl. regression vs reference"
  else
    cat "$WORK/bench.txt" >&5; grep -A3 '^FAIL' "$WORK/bench.txt" | sed 's/^/    /' >&4 || true
    "$T/bin/openfoam-eject" >/dev/null 2>&1 || true
    fail "benchmarks failed"
  fi
fi

# ================================================================ manifest (written last: marks a complete install)
TOTAL=$((SECONDS - T_START))
DISK_MB=$(( $(used_mb "$MU_HOME") - DISK0 )); [ "$DISK_MB" -ge 0 ] || DISK_MB=0
"$T/env/bin/python" - "$T" "$VERSION" "$COMMIT" "${DIRTY:+dirty}" "$TIMINGS" "$TOTAL" "$LOG" "$MISSING" "$DISK_MB" "$PLAT" "$OSNAME" <<'PYEOF'
import json, sys, time, platform
t, v, c, dirty, timings, total, log, missing, disk, plat, osname = sys.argv[1:12]
tm = {k: int(s) for k, s in (x.split("=", 1) for x in timings.split(";") if x)}
layers = open(f"{t}/LAYERS").read().split()
json.dump(dict(version=v, recipe_commit=c, recipe_dirty=bool(dirty), platform=plat, role="full",
               installed=time.strftime("%Y-%m-%d %H:%M:%S"), os=osname,
               layers=layers, layers_built=missing.split(), install_seconds=int(total), timings_s=tm,
               disk_added_mb=int(disk), log=log),
          open(f"{t}/MANIFEST.json", "w"), indent=1)
PYEOF
rm -f "$T/.installing"
spinner_stop
printf '\n  %sμ %s is ready%s in %s s %s· %s of new disk · log %s%s\n' "$B$G" "$VERSION" "$N" "$TOTAL" "$DIM" "$(human "$DISK_MB")" "${LOG/#$HOME/~}" "$N" >&3
[ -z "$PATH_MSG" ] || printf '  %s!%s %s\n' "$Y" "$N" "$PATH_MSG" >&3
# auto-update: on by default for a person's install; not for CI or --no-modify-path, and never over an earlier off
# (while it says on, this sets the scheduler up again, e.g. after systemd or linger became available)
if [ "$AUTOUPDATE" = 1 ] && [ "$MODIFY_PATH" = 1 ] && [ "${MU_NO_AUTOUPDATE:-}" != 1 ] && [ "${CI:-}" != true ] \
   && ! grep -Eqs '^[[:space:]]*enabled[[:space:]]*=[[:space:]]*off' "$MU_HOME/autoupdate.conf"; then
  au_rc=0; "$MU_HOME/bin/mu" autoupdate on --quiet > "$WORK/autoupdate.txt" 2>&5 || au_rc=$?
  au_line="$(head -1 "$WORK/autoupdate.txt")"; [ -n "$au_line" ] || au_line="auto-update could not be turned on (see the log): mu autoupdate on"
  if [ "$au_rc" = 0 ]; then printf '  %s\n' "$au_line" >&3; else printf '  %s!%s %s\n' "$Y" "$N" "$au_line" >&3; fi
fi
printf '\n  Next:  %smu%s                    status and common commands\n' "$B" "$N" >&3
printf '         %scd <project> && mu link%s  connect a project (mu.toml) for Claude Code\n\n' "$B" "$N" >&3
