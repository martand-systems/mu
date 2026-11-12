#!/bin/bash
# μ (mu) installer for macOS on Apple Silicon. Builds the full toolchain (workstation + local compute) from the
# locked recipe in this repo, then checks it with `mu doctor` and the benchmark suite. Usually run as ./install.sh.
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
#   --no-modify-path       don't add ~/.local/bin to your shell's PATH
#   -v, --verbose          show the full build output instead of the progress view
#
# Layout (MU_HOME, default ~/Library/Mu):
#   toolchains/<version>/  one per version: recipe snapshot, commands, links to its layers, MANIFEST.json
#   store/                 immutable, content-addressed layers shared by all versions (built once per Mac):
#     app-<name>-<sha>/      an application, unpacked from its pinned archive
#     env-<hash>/            the main environment (conda lock + Python lock)
#     cam-<hash>/            the CAM environment (FreeCAD's Python + its Python lock)
#   logs/                  install logs
# A new version whose locks are unchanged reuses every layer and installs in seconds.
# Caches: ~/Library/Caches/Mu (MU_CACHE). Commands: ~/.local/bin (MU_LINK_DIR). Apart from those, the PATH line
# in your shell profile, and OpenFOAM's temporary read-only mount at /Volumes/OpenFOAM-v2606, nothing is written
# outside MU_HOME.
set -Eeuo pipefail
LAYER_FORMAT="mu-layer-2"     # bump when the way layers are built changes (forces new layer hashes)
DISPATCHER_REV=3              # must match MU_DISPATCHER_REV in bin/mu; bump on every change to bin/mu or bin/git-lfs

RECIPE="$(cd "$(dirname "$0")/.." && pwd)"
MU_HOME="${MU_HOME:-$HOME/Library/Mu}"
MU_CACHE="${MU_CACHE:-$HOME/Library/Caches/Mu}"
MU_LINK_DIR="${MU_LINK_DIR:-$HOME/.local/bin}"
VERSION=""; SEED=""; SET_DEFAULT=0; FORCE=0; REBUILD=0; TESTS=1; QUICK=0; MODIFY_PATH=1; VERBOSE=0
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
    -h|--help) sed -n '2,/^set -E/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

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
        kb=$(du -skc "$MU_CACHE"/downloads/*.part 2>/dev/null | tail -1 | cut -f1)
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
    for e in $JOBS; do kill_tree "${e#*:}"; done
    sleep 0.5; detach_under "$STORE"
  fi
  [ "$LOCKED" = 1 ] && rm -rf "$MU_HOME/.install.lock"
  rm -rf "$WORK"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
[ "$TTY" = 1 ] && printf '\033[?25l' >&3   # hide the cursor while the spinner runs

# Explain a failed download in plain words (captive portals and HTTPS-intercepting proxies are the usual cause)
net_hint() {
  local out; out="$(curl -sSI --max-time 10 "$1" 2>&1 >/dev/null || true)"
  case "$out" in
    *certificate*|*SSL*|*ssl*) echo "the network is intercepting HTTPS (captive portal, proxy or filtering VPN): sign in to the network or switch networks, then re-run" ;;
    *resolve*) echo "no internet connection (DNS lookup failed)" ;;
    *timed\ out*|*Timeout*) echo "the connection timed out (offline or very slow network)" ;;
    *) echo "${out:-download failed}" ;;
  esac
}
sha256() { /usr/bin/openssl dgst -sha256 -r "$1" | cut -d' ' -f1; }
human() { awk -v m="$1" 'BEGIN{ if (m >= 1024) printf "%.1f GB", m/1024; else printf "%d MB", m }'; }

# ================================================================ preflight
printf '\n%sμ installer%s\n' "$B" "$N" >&3
stage preflight "Preflight"
[ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] || fail "this installer is for macOS on Apple Silicon (arm64)"
MACOS="$(sw_vers -productVersion)"
[ "${MACOS%%.*}" -ge 15 ] || fail "macOS 15 or newer is needed (this Mac has $MACOS): OpenVSP's build requires it"
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
  if [ -n "$GIT" ]; then
    COMMIT="$("$GIT" -C "$RECIPE" rev-parse HEAD)"
    TAG="$("$GIT" -C "$RECIPE" describe --tags --exact-match 2>/dev/null || true)"
    DIRTY="$("$GIT" -C "$RECIPE" status --porcelain --untracked-files=no | head -1 || true)"
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
DISK0=$(df -m "$HOME" | awk 'NR==2{print $3}')
T_START=$SECONDS

# recipe snapshot (the source of every layer hash below)
rm -rf "${T:?}"
mkdir -p "$T/bin" "$T/libexec" "$T/apps"
touch "$T/.installing"
rsync -a --delete --exclude .git --exclude __pycache__ --exclude .DS_Store --exclude '/toolchain/pip-stubs/*/build' "$RECIPE/" "$T/recipe/"
R="$T/recipe"
cp "$R/wrappers/macos/"* "$T/bin/"
chmod +x "$T/bin/"* "$R/bin/"*

# apps.toml -> TSV: name file url sha256 kind source_item target   (osx-arm64 entries)
awk -F'"' '
  /^\[/ { if (name!="" && file!="") print name"\t"file"\t"url"\t"sha"\t"kind"\t"item"\t"target;
           name=""; file=url=sha=kind=item=target=""
           if ($0 ~ /\.osx-arm64\]/) { name=$0; gsub(/^\[|\.osx-arm64\].*$/,"",name) } next }
  name!="" && /^file /        { file=$2 }   name!="" && /^url /    { url=$2 }   name!="" && /^sha256 / { sha=$2 }
  name!="" && /^kind /        { kind=$2 }   name!="" && /^source_item / { item=$2 } name!="" && /^target / { target=$2 }
  END { if (name!="" && file!="") print name"\t"file"\t"url"\t"sha"\t"kind"\t"item"\t"target }
' "$R/toolchain/apps.toml" > "$WORK/apps.tsv"
app() { awk -F'\t' -v n="$1" -v c="$2" '$1==n{print $c}' "$WORK/apps.tsv"; }   # app NAME COLUMN
appkey() { echo "app-$1-$(app "$1" 4 | cut -c1-16)"; }

# layer_key PREFIX "extra strings" PATH... : content hash of the inputs (paths relative to the recipe)
layer_key() {
  local prefix="$1" extra="$2"; shift 2
  { printf '%s\n%s\nosx-arm64\n' "$LAYER_FORMAT" "$extra"
    for p in "$@"; do
      ( cd "$R" && find "$p" -type f ! -name .DS_Store ! -path '*/__pycache__/*' ! -path '*/build/*' ! -path '*.egg-info/*' \
          | LC_ALL=C sort | while IFS= read -r f; do printf '%s  %s\n' "$(sha256 "$f")" "$f"; done )
    done
  } | /usr/bin/openssl dgst -sha256 -r | cut -c1-16 | sed "s/^/$prefix-/"
}
OS=toolchain/osx-arm64
ENV_KEY="$(layer_key env "$(appkey openvsp) mm:$(app micromamba 4) uv:$(app uv 4)" \
  $OS/env.explicit.txt $OS/env.pip.txt toolchain/pins.toml toolchain/patches toolchain/pip-stubs)"
CAM_KEY="$(layer_key cam "$(appkey freecad) uv:$(app uv 4)" $OS/cam-venv.pip.txt toolchain/pip-stubs)"
APP_NAMES="$(awk -F'\t' '$5=="zip"||$5=="dmg"{print $1}' "$WORK/apps.tsv")"
LAYERS="$ENV_KEY $CAM_KEY"; for a in $APP_NAMES; do LAYERS="$LAYERS $(appkey "$a")"; done
printf '%s\n' $LAYERS > "$T/LAYERS"   # written early: `mu gc` never removes layers an install is using
have_layer() { [ -f "$STORE/$1/.mu-layer" ]; }
# layers are read-only once built: nothing (a tool's bytecode cache, a stray pip install) can change a shared layer
seal() { chmod -R a-w "$1"; }
# detach_under DIR: detach disk images an interrupted unpack left mounted below DIR (never rm through a mount)
detach_under() {
  mount | sed -E 's/^.* on (.*) \([^()]*\)$/\1/' | while IFS= read -r m; do
    case "$m" in "$1"/*) hdiutil detach "$m" -quiet 2>/dev/null || hdiutil detach "$m" -force -quiet 2>/dev/null || true ;; esac
  done
}
unseal_rm() { [ -e "$1" ] || return 0; detach_under "$1"; chmod -R u+w "$1" 2>/dev/null || true; rm -rf "${1:?}"; }
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

avail_gb=$(df -g "$HOME" | awk 'NR==2{print $4}')
need_gb=1
case "$MISSING" in *env-*) need_gb=$((need_gb + 6)) ;; esac
case "$MISSING" in *cam-*) need_gb=$((need_gb + 2)) ;; esac
case "$MISSING" in *app-*) need_gb=$((need_gb + 9)) ;; esac
[ "$avail_gb" -ge "$need_gb" ] || fail "need about $need_gb GB free on this disk (have $avail_gb GB)"
n_missing=$(echo $MISSING | wc -w | tr -d ' '); n_layers=$(echo $LAYERS | wc -w | tr -d ' ')
ok "macOS $MACOS · $avail_gb GB free · $((n_layers - n_missing)) of $n_layers layers already on this Mac"
printf '  %sinstalling μ %s%s %s→ %s%s\n' "$B" "$VERSION" "$N" "$DIM" "${T/#$HOME/~}" "$N" >&3

# ================================================================ downloads
# fetch NAME URL SHA256 -> prints the cached path. Downloads once (resumable), verifies SHA-256, can reuse a seed folder.
fetch() {
  local name="$1" url="$2" sha="$3" dest="$MU_CACHE/downloads/$1"
  if [ -f "$dest" ] && [ "$(sha256 "$dest")" = "$sha" ]; then echo "$dest"; return 0; fi
  if [ -n "$SEED" ] && [ -f "$SEED/$name" ] && [ "$(sha256 "$SEED/$name")" = "$sha" ]; then
    cp -c "$SEED/$name" "$dest" 2>/dev/null || cp "$SEED/$name" "$dest"; echo "$dest"; return 0
  fi
  echo "downloading $name" >&2
  if ! curl -fL --retry 4 --retry-delay 2 --connect-timeout 20 -C - -sS -o "$dest.part" "$url" >&2; then
    rm -f "$dest.part"   # a stale partial file can make the resume itself fail: try once more from scratch
    curl -fL --retry 2 --connect-timeout 20 -sS -o "$dest.part" "$url" >&2 || fail "download failed: $url ($(net_hint "$url"))"
  fi
  local got; got="$(sha256 "$dest.part")"
  [ "$got" = "$sha" ] || { rm -f "$dest.part"; fail "SHA-256 mismatch for $name: got $got, expected $sha"; }
  mv "$dest.part" "$dest"; echo "$dest"
}

# app_layer NAME: unpack an app archive into the store (atomic: build in .partial, then rename)
app_layer() {
  local name="$1" file url sha kind item target key tmp f mp src
  file="$(app "$1" 2)"; url="$(app "$1" 3)"; sha="$(app "$1" 4)"; kind="$(app "$1" 5)"; item="$(app "$1" 6)"; target="$(app "$1" 7)"
  key="$(appkey "$name")"
  have_layer "$key" && return 0
  f="$(fetch "$file" "$url" "$sha")"
  echo "unpacking $name"
  tmp="$STORE/.$key.partial"; unseal_rm "$tmp"; mkdir -p "$tmp"
  if [ "$kind" = zip ]; then
    ditto -x -k "$f" "$tmp/x"; mv "$tmp/x/$item" "$tmp/$target"; rm -rf "${tmp:?}/x"
  else
    mp="$tmp/mnt"; mkdir -p "$mp"
    # hdiutil expands a compressed image on one core (FreeCAD: ~140 s). dmg2raw.py expands its chunks on every core
    # into a raw image, which then attaches without decompression (~25 s). Falls back to hdiutil if it can't.
    local img="$f" class=""
    if [ -n "$BOOT_PY" ] && "$BOOT_PY" "$R/install/dmg2raw.py" "$f" "$tmp/raw.img"; then img="$tmp/raw.img"; class="-imagekey diskimage-class=CRawDiskImage"
    else rm -f "$tmp/raw.img"; fi
    # -noverify: the archive's SHA-256 was just checked, so skip hdiutil's own full read of the image
    if ! { yes || true; } | hdiutil attach $class -nobrowse -readonly -noautoopen -noverify -mountpoint "$mp" "$img" >/dev/null 2>&1; then
      [ "$img" != "$f" ] || fail "hdiutil could not attach $file"
      echo "raw image did not attach; falling back to hdiutil on the DMG"; rm -f "$tmp/raw.img"
      { yes || true; } | hdiutil attach -nobrowse -readonly -noautoopen -noverify -mountpoint "$mp" "$f" >/dev/null 2>&1 || fail "hdiutil could not attach $file"
    fi
    if [ -e "$mp/$item" ]; then src="$mp/$item"; else src="$(find "$mp" -maxdepth 2 -name "$item" -print -quit)"; fi
    [ -n "$src" ] || { hdiutil detach "$mp" -quiet || true; fail "no $item in $file"; }
    ditto "$src" "$tmp/$target"
    hdiutil detach "$mp" -quiet || hdiutil detach "$mp" -force -quiet
    rmdir "$mp"; rm -f "$tmp/raw.img"
  fi
  printf 'app=%s\nsha256=%s\nbuilt=%s\n' "$name" "$sha" "$(date '+%Y-%m-%d %H:%M:%S')" > "$tmp/.mu-layer"
  seal "$tmp"; unseal_rm "$STORE/$key"; mv "$tmp" "$STORE/$key"
}

# tools needed to build: micromamba and uv (small, pinned binaries)
f="$(fetch "$(app micromamba 2)" "$(app micromamba 3)" "$(app micromamba 4)" 2>&5)"
install -m 755 "$f" "$T/libexec/micromamba"
f="$(fetch "$(app uv 2)" "$(app uv 3)" "$(app uv 4)" 2>&5)"
tar -xzf "$f" -C "$WORK"
install -m 755 "$WORK/$(app uv 6)/uv" "$T/libexec/uv"
UV="$T/libexec/uv"

export MU_TOOLCHAIN="$T" MU_HOME MU_CACHE MU_INSTALLING=1
set +u; . "$R/lib/mu-env.sh"; set -u
export UV_NO_CONFIG=1 UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=clone
# local paths go to the lock files as percent-encoded file:// URLs (safe with spaces in paths)
furl() { printf 'file://%s' "$(printf '%s' "$1" | sed -e 's/%/%25/g' -e 's/ /%20/g' -e 's/#/%23/g' -e 's/&/%26/g' -e 's/|/%7C/g')"; }
sed_esc() { printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'; }   # make a string safe as a sed s||| replacement
TTC="$(awk -F'"' '/^\[text-to-cad\]/{s=1} s && /^repo/{r=$2} s && /^commit/{c=$2} END{print r"/archive/"c".zip"}' "$R/toolchain/pins.toml")"
subst() { sed -e "s|{MU_RECIPE_URL}|$(sed_esc "$(furl "$R")")|g" -e "s|{MU_APPS_URL}|$(sed_esc "$(furl "$1")")|g" \
              -e "s|{TEXT_TO_CAD_ARCHIVE}|$(sed_esc "$TTC")|g" "$2"; }
# uvi PYTHON REQS: install a Python lock (no resolution: every pin is exact). PyPI pins carry SHA-256 hashes and
# install with --require-hashes; URL/path pins (commit-pinned archives, files from pinned apps) install after them.
# Offline first, so an install with warm caches never touches the network; falls back to fetching what is missing.
uvi() {
  local py="$1" reqs="$2" part flags
  grep -e '--hash=' "$reqs" > "$reqs.hashed" || true
  grep -v -e '--hash=' "$reqs" | grep -v '^[[:space:]]*\(#\|$\)' > "$reqs.plain" || true
  for part in hashed plain; do
    [ -s "$reqs.$part" ] || continue
    flags="--no-deps --compile-bytecode"; [ "$part" = hashed ] && flags="$flags --require-hashes"
    "$UV" pip install --python "$py" $flags --offline -r "$reqs.$part" \
    || { echo "(some Python packages are not cached; fetching)"; "$UV" pip install --python "$py" $flags -r "$reqs.$part" \
         || fail "Python packages could not be installed ($(net_hint https://pypi.org/simple/))"; }
  done
}

# cam_layer: the CAM environment: a venv on FreeCAD's Python 3.11 with the CAM lock (needs only FreeCAD and uv,
# so it builds in the background while the main environment is still building)
cam_layer() {
  local L="$STORE/$CAM_KEY" FCAPP="$STORE/$(appkey freecad)/$(app freecad 7)" CSP
  unseal_rm "$L"
  HOME="$MU_STATE/home" "$FCAPP/Contents/Resources/bin/python" -m venv --system-site-packages "$L"
  subst "$STORE" "$R/$OS/cam-venv.pip.txt" > "$WORK/cam.pip.txt"
  uvi "$L/bin/python" "$WORK/cam.pip.txt"
  # FreeCAD's own modules (FreeCAD, Part, Path/CAM) live in the app bundle's lib folder, not in its site-packages
  CSP="$("$L/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
  echo "$FCAPP/Contents/Resources/lib" > "$CSP/freecad.pth"
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
  spec="$(grep -E '/(python|libpython|python_abi|bzip2|libexpat|libffi|liblzma|libmpdec|libsqlite|libzlib|ncurses|openssl|readline|tk|tzdata|ca-certificates|libcxx)-[0-9]' "$R/$OS/env.explicit.txt")"
  key="$(printf '%s' "$spec" | /usr/bin/openssl dgst -sha256 -r | cut -c1-12)"; d="$MU_CACHE/bootpy-$key"
  if [ ! -f "$d/.ok" ]; then
    rm -rf "$MU_CACHE"/bootpy-*; printf '@EXPLICIT\n%s\n' "$spec" > "$WORK/boot.txt"
    micromamba create -y -q -p "$d" --offline --file "$WORK/boot.txt" >&5 2>&5 \
      || { rm -rf "$d"; micromamba create -y -q -p "$d" --file "$WORK/boot.txt" >&5 2>&5; } || { rm -rf "$d"; return 0; }
    "$d/bin/python" -c 'import bz2, zlib, plistlib, concurrent.futures' >&5 2>&5 || { rm -rf "$d"; return 0; }
    touch "$d/.ok"
  fi
  echo "$d/bin/python"
}

# ================================================================ layers: main environment (foreground) while apps and
# the CAM environment build in the background, in parallel
stage env "Main environment"
BOOT_PY=""; have_layer "$ENV_KEY" && BOOT_PY="$STORE/$ENV_KEY/bin/python"
[ -n "$BOOT_PY" ] || for a in $APP_NAMES; do
  if [ "$(app "$a" 5)" = dmg ] && ! have_layer "$(appkey "$a")"; then note "bootstrap Python for parallel unpacking"; BOOT_PY="$(boot_python)"; break; fi
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
  wait_job openvsp   # its Python packages are part of this layer
  note "Python packages ($n_pip, locked)${JOBS:+ · in parallel: $(running)}"
  # setuptools writes build files into a source folder: build OpenVSP's Python packages from a copy outside the
  # (read-only) app layer, at a stable path so uv's wheel cache still recognises them
  ovsp="$MU_CACHE/build/$(appkey openvsp)"; ovt="$(app openvsp 7)"
  if [ ! -d "$ovsp/$ovt/python" ]; then
    rm -rf "$ovsp" "$ovsp.tmp"; mkdir -p "$ovsp.tmp/$ovt"; cp -cR "$STORE/$(appkey openvsp)/$ovt/python" "$ovsp.tmp/$ovt/python"
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
       case "$(basename "${SHELL:-/bin/zsh}")" in bash) rc="$HOME/.bash_profile" ;; zsh) rc="${ZDOTDIR:-$HOME}/.zprofile" ;; *) rc="$HOME/.profile" ;; esac
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
DISK_MB=$(( $(df -m "$HOME" | awk 'NR==2{print $3}') - DISK0 )); [ "$DISK_MB" -ge 0 ] || DISK_MB=0
"$T/env/bin/python" - "$T" "$VERSION" "$COMMIT" "${DIRTY:+dirty}" "$TIMINGS" "$TOTAL" "$LOG" "$MISSING" "$DISK_MB" <<'PYEOF'
import json, sys, time, platform
t, v, c, dirty, timings, total, log, missing, disk = sys.argv[1:10]
tm = {k: int(s) for k, s in (x.split("=", 1) for x in timings.split(";") if x)}
layers = open(f"{t}/LAYERS").read().split()
json.dump(dict(version=v, recipe_commit=c, recipe_dirty=bool(dirty), platform="osx-arm64", role="full",
               installed=time.strftime("%Y-%m-%d %H:%M:%S"), macos=platform.mac_ver()[0],
               layers=layers, layers_built=missing.split(), install_seconds=int(total), timings_s=tm,
               disk_added_mb=int(disk), log=log),
          open(f"{t}/MANIFEST.json", "w"), indent=1)
PYEOF
rm -f "$T/.installing"
spinner_stop
printf '\n  %sμ %s is ready%s in %s s %s· %s of new disk · log %s%s\n' "$B$G" "$VERSION" "$N" "$TOTAL" "$DIM" "$(human "$DISK_MB")" "${LOG/#$HOME/~}" "$N" >&3
[ -z "$PATH_MSG" ] || printf '  %s!%s %s\n' "$Y" "$N" "$PATH_MSG" >&3
printf '\n  Next:  %smu%s                    status and common commands\n' "$B" "$N" >&3
printf '         %scd <project> && mu link%s  connect a project (mu.toml) for Claude Code\n\n' "$B" "$N" >&3
