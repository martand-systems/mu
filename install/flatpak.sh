# shellcheck shell=bash
# μ installer: Flatpak apps on Linux (PrusaSlicer, from Flathub). Sourced by install/install.sh (and by
# tools/s2/probe-prusaslicer.sh). Uses MU_CACHE, fail MSG, note MSG and net_hint URL (install/download.sh) from the caller.
#
#   fp_fetch REF COMMIT URL KEY            puts that commit into the download cache (once per machine)
#   fp_deploy DIR URL KEY REF@COMMIT...    builds a flatpak installation at DIR from the cache, with no network
#   fp ARGS                                flatpak with a session D-Bus
#
# Download cache: $MU_CACHE/flatpak, a flatpak installation used only to download (its repo/ is an OSTree repository).
# flatpak itself does every download from Flathub (Flathub refuses other OSTree clients), straight at the pinned commit:
# `flatpak update --commit=PIN --no-deploy REF`. That command needs REF to be installed, so REF is first installed from
# an empty placeholder commit made locally (no download; it is never run) and stays installed in the cache. Flatpak checks
# Flathub's signature on the summary and the commit with Flathub's key, pinned in the recipe (KEY), and the commit ID
# (a SHA-256 over the commit, which names its file tree by hash, which names every file by hash: a Merkle tree) verifies
# every object pulled. An interrupted pull resumes (fetched objects are kept; the commit stays marked partial until it is
# complete). A ref mu/REF/COMMIT, written last, marks a complete commit: then the network is never used again for it.
# One download at a time per cache (flock), so two installs never fetch the same commit twice. mu gc deletes the refs no
# installed version pins, then the objects no ref reaches (ostree prune).
#
# Layer: a private flatpak installation. Its repository (bare-user-only, flatpak's own mode for a user installation)
# imports the pinned commits from the cache with every object's checksum verified again (pull-local --untrusted), then
# flatpak deploys them from that repository (install --no-pull) and the deployed commits are compared with the pins.
# Only the app and its runtime are installed (--no-related): the extensions flatpak would add on its own (translations,
# GL drivers, codecs) are not needed by the headless CLI and are not pinned, so they would make the layer depend on
# the day it was built. The layer is a copy, not hard links into the cache: sealing a layer (chmod a-w) would change
# modes that are part of a bare-user-only object's checksum, and deleting the cache must never touch a layer.
# All ostree commands here are local (no network).

FP_REMOTE=flathub

# flatpak needs a session bus (dbus-run-session gives one) and the system bus (WSL2/systemd has it; containers don't)
fp() { if [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then flatpak "$@"; else dbus-run-session -- flatpak "$@"; fi; }

fp_cache() { echo "$MU_CACHE/flatpak/repo"; }
# fp_cached REPO REF COMMIT: the commit is complete in the cache (its ref is written after the pull completed)
fp_cached() {
  [ -f "$1/config" ] || return 1
  [ "$(ostree --repo="$1" rev-parse "mu/$2/$3" 2>/dev/null)" = "$3" ] && [ ! -e "$1/state/$3.commitpartial" ]
}

fp_fetch() {
  local ref="$1" commit="$2" url="$3" key="$4" C res
  C="$(fp_cache)"
  fp_cached "$C" "$ref" "$commit" && return 0
  command -v ostree >/dev/null 2>&1 || fail "ostree is not installed (it comes with: sudo ./install.sh --system)"
  mkdir -p "$MU_CACHE/flatpak"
  # one result line: ok, or the reason ("|| true": a signal that ends it must not trip the ERR trap)
  res="$(_fp_pull "$ref" "$commit" "$url" "$key" || true)"
  case "$res" in
    ok) ;;
    "") fail "the flatpak download of $ref was interrupted" ;;
    *) fail "flatpak: $res" ;;
  esac
}

# _fp_placeholder REF: install REF in the cache installation from an empty local commit (see above). Its metadata must
# be what Flathub's summary lists for REF (flatpak compares them), so it is read from there first.
_fp_placeholder() {
  local ref="$1" repo="$MU_CACHE/flatpak/repo" d c m
  m="$(fp --user remote-info --show-metadata "$FP_REMOTE" "$ref")" && [ -n "$m" ] || return 1
  d="$(mktemp -d "${TMPDIR:-/tmp}/mu-fp.XXXXXX")"; mkdir -p "$d/files"
  printf '%s\n' "$m" > "$d/metadata"   # byte for byte the commit's xa.metadata (flatpak checks that at deploy)
  c="$(ostree --repo="$repo" commit --orphan --tree=dir="$d" --bind-ref="$ref" --timestamp=@86400 --no-xattrs \
         --canonical-permissions --add-metadata-string=xa.metadata="$m
" \
         -s "μ placeholder: lets flatpak update --commit download a pinned commit directly")" || { rm -rf "$d"; return 1; }
  rm -rf "$d"
  ostree --repo="$repo" refs --delete "mu-placeholder/$ref" >&2
  ostree --repo="$repo" refs --create="mu-placeholder/$ref" "$c" >&2 || return 1
  _fp_set_remote_ref "$repo" "$ref" "$c"
  fp --user install -y --noninteractive --no-pull --no-deps --no-related "$FP_REMOTE" "$ref" >&2
}
_fp_set_remote_ref() { mkdir -p "$1/refs/remotes/$FP_REMOTE/$(dirname "$2")" && echo "$3" > "$1/refs/remotes/$FP_REMOTE/$2"; }

# _fp_pull REF COMMIT URL KEY: in a subshell without the ERR trap or errexit; flatpak's output goes to stderr (the
# log), and one result line to stdout
_fp_pull() {
  ( trap - ERR; set +e
    ref="$1"; commit="$2"; url="$3"; key="$4"; C="$MU_CACHE/flatpak/repo"
    export FLATPAK_USER_DIR="$MU_CACHE/flatpak"
    exec 9>"$MU_CACHE/flatpak.lock"
    if ! flock -n 9; then
      echo "waiting for another install's flatpak download" >&2
      flock -w 14400 9 || { echo "timed out waiting for another install's flatpak download ($MU_CACHE/flatpak.lock)"; exit 1; }
    fi
    if fp_cached "$C" "$ref" "$commit"; then echo ok; exit 0; fi   # the other install downloaded it
    # Flathub, verified with the pinned key (set again every time, so a changed URL or key takes effect)
    if fp --user remotes --columns=name 2>/dev/null | grep -qx "$FP_REMOTE"; then
      fp --user remote-modify --url="$url" --gpg-import="$key" "$FP_REMOTE" >&2
    else
      fp --user remote-add --gpg-import="$key" "$FP_REMOTE" "$url" >&2
    fi || { echo "could not set up the $FP_REMOTE remote in $MU_CACHE/flatpak ($(net_hint "$url"))"; exit 1; }
    if ! ostree --repo="$C" rev-parse "mu-placeholder/$ref" >/dev/null 2>&1 || ! fp --user info "$ref" >/dev/null 2>&1; then
      _fp_placeholder "$ref" || { echo "could not prepare the download of $ref in $MU_CACHE/flatpak"; exit 1; }
    fi
    kb0=$(du -sk "$C/objects" 2>/dev/null | cut -f1); t0=$(date +%s)
    echo "flatpak: downloading $ref@$commit from $url into $C" >&2
    ok=0; i=1
    while [ "$i" -le 4 ]; do
      # a pull that fails resumes on the next attempt (fetched objects stay)
      if fp --user update -y --noninteractive --no-deploy --no-related --no-deps --commit="$commit" "$ref" >&2 \
         && ostree --repo="$C" show "$commit" >/dev/null 2>&1 && [ ! -e "$C/state/$commit.commitpartial" ]; then ok=1; break; fi
      echo "flatpak: download attempt $i failed" >&2
      [ "$i" -lt 4 ] && sleep $((i * 15))
      i=$((i + 1))
    done
    # back to the placeholder: the cache keeps commits by its own refs only (several versions may pin one ref)
    _fp_set_remote_ref "$C" "$ref" "$(ostree --repo="$C" rev-parse "mu-placeholder/$ref")"
    if [ "$ok" = 0 ]; then echo "could not download $ref at commit ${commit:0:12} ($(net_hint "$url"))"; exit 1; fi
    ostree --repo="$C" refs --create="mu/$ref/$commit" "$commit" >&2 || { echo "could not record $ref@${commit:0:12} in $C"; exit 1; }
    kb1=$(du -sk "$C/objects" 2>/dev/null | cut -f1)
    echo "flatpak: cached $ref@${commit:0:12}: +$(( (${kb1:-0} - ${kb0:-0}) / 1024 )) MB in the cache, $(( $(date +%s) - t0 )) s" >&2
    echo ok
  )
}

# fp_deploy DIR URL KEY REF@COMMIT...: a flatpak installation at DIR (FLATPAK_USER_DIR) holding exactly these commits,
# built from the cache with no network. Every REF@COMMIT must be cached (fp_fetch). If an object in the cache fails its
# checksum, the cache is repaired (the damaged objects deleted, the commit downloaded again) and the import retried.
fp_deploy() {
  local dir="$1" url="$2" key="$3" C r ref commit refs=""
  shift 3
  C="$(fp_cache)"
  mkdir -p "$dir"
  ostree init --mode=bare-user-only --repo="$dir/repo" || fail "could not create the flatpak repository in $dir"
  # the same Flathub remote a normal installation has (this installation is sealed: it never pulls)
  ostree --repo="$dir/repo" remote add --set=gpg-verify=true --gpg-import="$key" "$FP_REMOTE" "$url"
  for r in "$@"; do
    ref="${r%@*}"; commit="${r##*@}"
    fp_cached "$C" "$ref" "$commit" || fail "flatpak: $ref@${commit:0:12} is not in the cache"
    # --untrusted: every object read from the cache is checksummed again (objects are hard-linked when the cache and
    # the store share a filesystem, so the layer costs almost no extra disk)
    if ! ostree --repo="$dir/repo" pull-local --untrusted "$C" "$commit"; then
      echo "flatpak: $ref@${commit:0:12} failed its checksum check in the cache: repairing the cache"
      _fp_repair "$ref" "$commit" || fail "flatpak: could not repair the download cache $C (delete it and re-run)"
      fp_fetch "$ref" "$commit" "$url" "$key"
      ostree --repo="$dir/repo" pull-local --untrusted "$C" "$commit" \
        || fail "flatpak: $ref@${commit:0:12} is still damaged after downloading it again (delete $C and re-run)"
    fi
    mkdir -p "$dir/repo/refs/remotes/$FP_REMOTE/$(dirname "$ref")"
    echo "$commit" > "$dir/repo/refs/remotes/$FP_REMOTE/$ref"
    refs="$refs $ref"
  done
  # shellcheck disable=SC2086
  FLATPAK_USER_DIR="$dir" fp --user install -y --noninteractive --no-pull --no-related "$FP_REMOTE" $refs \
    || fail "flatpak could not install$refs from the cache"
  for r in "$@"; do
    ref="${r%@*}"; commit="${r##*@}"
    [ "$(FLATPAK_USER_DIR="$dir" fp --user info --show-commit "$ref")" = "$commit" ] || fail "$ref is not at the pinned commit"
  done
}

# _fp_repair REF COMMIT: delete the cache's damaged objects (ostree fsck marks the commits that used them partial) and
# forget REF@COMMIT, so that fp_fetch downloads what is missing
_fp_repair() {
  ( exec 9>"$MU_CACHE/flatpak.lock"; flock -w 14400 9 || exit 1
    ostree --repo="$MU_CACHE/flatpak/repo" fsck --delete --quiet || true
    ostree --repo="$MU_CACHE/flatpak/repo" refs --delete "mu/$1/$2" )
}

# fp_seal DIR: make a flatpak layer read-only. Directories only: its files are hard links shared with the download
# cache, and their modes are part of each object's checksum. (Inside the sandbox the app's files are mounted read-only.)
fp_seal() { find "$1" -type d -exec chmod a-w {} +; }
