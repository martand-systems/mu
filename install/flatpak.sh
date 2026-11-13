# shellcheck shell=bash
# μ installer: Flatpak apps on Linux (PrusaSlicer, from Flathub). Sourced by install/install.sh (and by
# tools/s2/probe-prusaslicer.sh). Uses MU_CACHE, fail MSG, note MSG and net_hint URL (install/download.sh) from the caller.
#
#   fp_fetch REF COMMIT URL KEY            puts that commit into the download cache (once per machine)
#   fp_deploy DIR URL KEY REF@COMMIT...    builds a flatpak installation at DIR from the cache, with no network
#   fp_register                            records MU_HOME in the cache, so mu gc in any MU_HOME keeps its pins
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
#
# Locking ($MU_CACHE/flatpak.lock, flock): a download holds it exclusively (two installs never fetch the same commit
# twice); an import into a layer holds it shared; mu gc prunes only when it gets it exclusively, at once (else it skips).
# MU_CACHE is shared by every MU_HOME of the user, so each installer records its MU_HOME in $MU_CACHE/flatpak/homes/, and
# mu gc (in any MU_HOME) keeps every commit that some installed version of any recorded MU_HOME pins
# (toolchains/*/CACHE_REFS), deletes the other mu/ refs, then the objects no ref reaches (ostree prune).
#
# Layer: a private flatpak installation, built with no network. Its repository (bare-user-only, flatpak's own mode for a
# user installation) imports the pinned commits from the cache with every object's checksum verified again (pull-local
# --untrusted); flatpak deploys them from that repository (install --no-pull) and the deployed commits are compared with
# the pins. Only the app and its runtime are installed (--no-related): the extensions flatpak would add on its own
# (translations, GL drivers, codecs) are not needed by the headless CLI and are not pinned, so they would make the layer
# depend on the day it was built.
# The layer is a copy that shares no inode with the cache: on one filesystem pull-local hard-links the cache's objects,
# so the layer's objects are then copied (cp --reflink=auto: shared extents where the filesystem can, copy-on-write, so
# still independent). With hard links a write into any one file would change the cache and every other layer at once,
# and the cache's file modes would be the layer's: a bare-user-only object's mode is part of its checksum, so sealing a
# hard-linked layer (chmod a-w) corrupts the cache. As a copy, the layer is sealed like every other layer (seal: chmod -R
# a-w), a damaged cache is repaired without touching any layer, and deleting the cache or a layer never affects the other.
# The price is disk: a layer costs its full size (1.3 GB for PrusaSlicer) on top of the cache, as before the cache.
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
# built from the cache with no network. Each REF@COMMIT is normally cached already (fp_fetch). If one is missing (mu gc
# in another MU_HOME pruned it before this install recorded its pins) it is downloaded again; if an object in the cache
# fails its checksum, the cache is repaired (the damaged objects deleted, the commit downloaded again). Then the import
# is retried.
fp_deploy() {
  local dir="$1" url="$2" key="$3" res="" i r
  shift 3
  mkdir -p "$dir"
  ostree init --mode=bare-user-only --repo="$dir/repo" || fail "could not create the flatpak repository in $dir"
  # the same Flathub remote a normal installation has (this installation is sealed: it never pulls)
  ostree --repo="$dir/repo" remote add --set=gpg-verify=true --gpg-import="$key" "$FP_REMOTE" "$url"
  for i in 1 2 3; do
    res="$(_fp_import "$dir" "$@" || true)"
    case "$res" in
      ok) break ;;
      "missing "*) r="${res#missing }"
        echo "flatpak: ${r%@*}@$(printf '%.12s' "${r##*@}") is no longer in the cache (pruned by mu gc?): downloading it again"
        fp_fetch "${r%@*}" "${r##*@}" "$url" "$key" ;;
      "damaged "*) r="${res#damaged }"
        echo "flatpak: ${r%@*}@$(printf '%.12s' "${r##*@}") failed its checksum check in the cache: repairing the cache"
        _fp_repair "${r%@*}" "${r##*@}" || fail "flatpak: could not repair the download cache $(fp_cache) (delete it and re-run)"
        fp_fetch "${r%@*}" "${r##*@}" "$url" "$key" ;;
      "") fail "flatpak: the import from the download cache was interrupted" ;;
      *) fail "flatpak: $res" ;;
    esac
  done
  [ "$res" = ok ] || fail "flatpak: could not import from the download cache $(fp_cache) (${res}; delete it and re-run)"
  # own inodes: copy the objects that pull-local hard-linked from the cache (see the header)
  if [ -n "$(find "$dir/repo/objects" -type f -links +1 -print -quit)" ]; then
    rm -rf "$dir/repo/objects.copy"
    cp -a --reflink=auto "$dir/repo/objects" "$dir/repo/objects.copy" || fail "flatpak: could not copy the layer's objects"
    rm -rf "$dir/repo/objects"; mv "$dir/repo/objects.copy" "$dir/repo/objects"
  fi
  for r in "$@"; do
    mkdir -p "$dir/repo/refs/remotes/$FP_REMOTE/$(dirname "${r%@*}")"
    echo "${r##*@}" > "$dir/repo/refs/remotes/$FP_REMOTE/${r%@*}"
  done
  # shellcheck disable=SC2046
  FLATPAK_USER_DIR="$dir" fp --user install -y --noninteractive --no-pull --no-related "$FP_REMOTE" $(for r in "$@"; do echo "${r%@*}"; done) \
    || fail "flatpak could not install from the cache: $*"
  for r in "$@"; do
    [ "$(FLATPAK_USER_DIR="$dir" fp --user info --show-commit "${r%@*}")" = "${r##*@}" ] || fail "${r%@*} is not at the pinned commit"
  done
}

# _fp_import DIR REF@COMMIT...: pull-local each commit from the cache into DIR/repo, holding the cache's lock shared (so
# mu gc can't prune it meanwhile). In a subshell without the ERR trap; ostree's output goes to stderr (the log), and
# one result line to stdout: ok, "missing REF@COMMIT", "damaged REF@COMMIT" or a reason.
_fp_import() {
  ( trap - ERR; set +e
    dir="$1"; shift; C="$MU_CACHE/flatpak/repo"
    exec 9>"$MU_CACHE/flatpak.lock"
    if ! flock -s -n 9; then
      echo "waiting for another install's flatpak download" >&2
      flock -s -w 14400 9 || { echo "timed out waiting for the download cache ($MU_CACHE/flatpak.lock)"; exit 0; }
    fi
    for r in "$@"; do
      fp_cached "$C" "${r%@*}" "${r##*@}" || { echo "missing $r"; exit 0; }
      ostree --repo="$dir/repo" pull-local --untrusted "$C" "${r##*@}" >&2 || { echo "damaged $r"; exit 0; }
    done
    echo ok )
}

# _fp_repair REF COMMIT: delete the cache's damaged objects (ostree fsck marks the commits that used them partial) and
# forget REF@COMMIT, so that fp_fetch downloads what is missing. Layers are copies: none of them is affected.
_fp_repair() {
  ( exec 9>"$MU_CACHE/flatpak.lock"; flock -w 14400 9 || exit 1
    ostree --repo="$MU_CACHE/flatpak/repo" fsck --delete --quiet || true
    ostree --repo="$MU_CACHE/flatpak/repo" refs --delete "mu/$1/$2" )
}

# fp_register: record MU_HOME in the cache (one file per MU_HOME, named by a checksum of its path, holding the path)
fp_register() {
  local h
  h="$(cd "$MU_HOME" && pwd -P)"
  mkdir -p "$MU_CACHE/flatpak/homes"
  printf '%s\n' "$h" > "$MU_CACHE/flatpak/homes/$(printf '%s' "$h" | cksum | cut -d' ' -f1)"
}
