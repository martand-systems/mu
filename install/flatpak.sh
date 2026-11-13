# shellcheck shell=bash
# μ installer: Flatpak apps on Linux (PrusaSlicer, from Flathub). Sourced by install/install.sh (and by
# tools/s2/probe-prusaslicer.sh). Uses MU_CACHE, fail MSG, note MSG and net_hint URL (install/download.sh) from the caller.
#
#   fp_fetch REF COMMIT URL KEY            puts that commit into the download cache (once per machine)
#   fp_deploy DIR URL KEY REF@COMMIT...    builds a flatpak installation at DIR from the cache, with no network
#   fp ARGS                                flatpak with a session D-Bus
#
# Download cache: $MU_CACHE/flatpak/repo, an OSTree repository in archive mode (objects stay compressed, as Flathub
# serves them). A pinned commit is pulled from Flathub straight at that commit (ostree pull flathub REF@COMMIT; never
# "latest, then update"). Two checks protect it: the commit ID is a SHA-256 over the commit object, which names its root
# tree by hash, which names every file by hash (a Merkle tree), so every object pulled is verified against the pin; and
# Flathub's signature on the commit is checked with Flathub's key, pinned in the recipe (KEY), not fetched. The pull
# resumes where it stopped (objects already fetched are kept; ostree marks the commit partial until it is complete).
# A ref mu/REF/COMMIT, written last, marks a complete commit: then the network is never used again for it. One pull at a
# time per cache (flock), so two installs never download the same commit twice. mu gc deletes the refs that no
# installed version pins, then the objects no ref reaches (ostree prune).
#
# Layer: a private flatpak installation. Its repository (bare-user-only, flatpak's own mode for a user installation)
# imports the pinned commits from the cache with every object's checksum verified again (pull-local --untrusted), then
# flatpak deploys them from that repository (install --no-pull) and the deployed commits are compared with the pins.
# Only the app and its runtime are installed (--no-related): the extensions flatpak would add on its own (translations,
# GL drivers, codecs) are not needed by the headless CLI and are not pinned, so they would make the layer depend on
# the day it was built. The layer is a copy, not hard links into the cache: sealing a layer (chmod a-w) would change
# modes that are part of a bare-user-only object's checksum, and deleting the cache must never touch a layer.

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
  res="$(_fp_pull "$C" "$ref" "$commit" "$url" "$key" || true)"
  case "$res" in
    ok) ;;
    "") fail "the flatpak download of $ref was interrupted" ;;
    *) fail "flatpak: $res" ;;
  esac
}

# _fp_pull REPO REF COMMIT URL KEY: in a subshell without the ERR trap or errexit; ostree's output goes to stderr (the
# log), and one result line to stdout
_fp_pull() {
  ( trap - ERR; set +e
    C="$1"; ref="$2"; commit="$3"; url="$4"; key="$5"
    exec 9>"$MU_CACHE/flatpak/lock"
    if ! flock -n 9; then
      echo "waiting for another install's flatpak download" >&2
      flock -w 14400 9 || { echo "timed out waiting for another install's flatpak download ($MU_CACHE/flatpak/lock)"; exit 1; }
    fi
    if fp_cached "$C" "$ref" "$commit"; then echo ok; exit 0; fi   # the other install pulled it
    if [ ! -f "$C/config" ]; then
      rm -rf "$C.new"
      { ostree init --mode=archive --repo="$C.new" >&2 && mv "$C.new" "$C"; } || { echo "could not create the cache $C"; exit 1; }
    fi
    # Flathub, verified with the pinned key (set again on every pull, so a changed URL or key takes effect)
    ostree --repo="$C" remote delete --if-exists "$FP_REMOTE" >&2
    ostree --repo="$C" remote add --set=gpg-verify=true --gpg-import="$key" "$FP_REMOTE" "$url" >&2 \
      || { echo "could not configure the $FP_REMOTE remote in $C"; exit 1; }
    kb0=$(du -sk "$C/objects" 2>/dev/null | cut -f1); t0=$(date +%s)
    echo "flatpak: pulling $ref@$commit from $url into $C" >&2
    ok=0; i=1
    while [ "$i" -le 4 ]; do
      # ostree retries network errors itself; a pull that still fails resumes on the next attempt (fetched objects stay)
      if ostree --repo="$C" pull "$FP_REMOTE" "$ref@$commit" >&2; then ok=1; break; fi
      echo "flatpak: pull attempt $i failed" >&2
      [ "$i" -lt 4 ] && sleep $((i * 15))
      i=$((i + 1))
    done
    if [ "$ok" = 0 ]; then echo "could not download $ref at commit ${commit:0:12} ($(net_hint "$url"))"; exit 1; fi
    # the pull wrote a remote ref; the cache keeps commits by its own refs only (several versions may pin one ref)
    ostree --repo="$C" refs --delete "$FP_REMOTE:$ref" >&2
    [ ! -e "$C/state/$commit.commitpartial" ] || { echo "the pull of $ref@${commit:0:12} is incomplete"; exit 1; }
    ostree --repo="$C" refs --create="mu/$ref/$commit" "$commit" >&2 || { echo "could not record $ref@${commit:0:12} in $C"; exit 1; }
    kb1=$(du -sk "$C/objects" 2>/dev/null | cut -f1)
    echo "flatpak: cached $ref@${commit:0:12}: +$(( (${kb1:-0} - ${kb0:-0}) / 1024 )) MB in the cache, $(( $(date +%s) - t0 )) s" >&2
    echo ok
  )
}

# fp_deploy DIR URL KEY REF@COMMIT...: a flatpak installation at DIR (FLATPAK_USER_DIR) holding exactly these commits,
# built from the cache with no network. Every REF@COMMIT must be cached (fp_fetch).
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
    # --untrusted: re-verify the checksum of every object read from the cache
    ostree --repo="$dir/repo" pull-local --untrusted "$C" "$commit" \
      || fail "flatpak: $ref@${commit:0:12} in the cache is damaged (delete $C and re-run)"
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
