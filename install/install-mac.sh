#!/bin/bash
# μ (mu) installer for macOS arm64: builds a full toolchain (workstation + local compute) on this Mac
# from the locked recipe in this repo, then runs `mu doctor` and the benchmark suite.
#
#   install/install-mac.sh [--version V] [--seed-downloads DIR] [--set-default] [--force] [--skip-tests] [--quick]
#
#   --version V            toolchain version label (default: git tag, else dev-<commit>)
#   --seed-downloads DIR   reuse app archives already on disk (checked against apps.toml SHA-256)
#   --set-default          make this the default toolchain (automatic when none is set)
#   --force                rebuild an existing install of the same version
#   --skip-tests           skip the benchmark suite (doctor still runs)
#   --quick                skip the benchmarks if this recipe commit already passed them on this Mac
#
# Installs into   ~/Library/Mu/toolchains/<version>/   (MU_HOME overrides)
# App store       ~/Library/Mu/store/<app>-<sha>/      unpacked once per Mac, APFS-cloned into each version
# Caches in       ~/Library/Caches/Mu/                 downloads, conda packages, pip (MU_CACHE overrides)
# Links `mu` into ~/.local/bin (MU_LINK_DIR overrides). Nothing else outside those folders is written,
# except the OpenFOAM app's temporary read-only mount at /Volumes/OpenFOAM-v2606 while it runs.
set -euo pipefail

RECIPE="$(cd "$(dirname "$0")/.." && pwd)"
MU_HOME="${MU_HOME:-$HOME/Library/Mu}"
MU_CACHE="${MU_CACHE:-$HOME/Library/Caches/Mu}"
MU_LINK_DIR="${MU_LINK_DIR:-$HOME/.local/bin}"
VERSION=""; SEED=""; SET_DEFAULT=0; FORCE=0; TESTS=1; QUICK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --seed-downloads) SEED="$2"; shift 2 ;;
    --set-default) SET_DEFAULT=1; shift ;;
    --force) FORCE=1; shift ;;
    --skip-tests) TESTS=0; shift ;;
    --quick) QUICK=1; shift ;;
    -h|--help) sed -n 2,19p "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1mμ ▸ %s\033[0m\n' "$*"; }
# stage NAME: start a timed stage (durations go to MANIFEST.json "timings_s")
STAGE=""; STAGE_T0=$SECONDS; TIMINGS=""
stage() { [ -n "$STAGE" ] && TIMINGS="$TIMINGS$STAGE=$((SECONDS-STAGE_T0));"; STAGE="$1"; STAGE_T0=$SECONDS; [ "$1" = end ] || say "$1"; }
die() { printf '\nμ install failed: %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] || die "this installer is for macOS on Apple Silicon (arm64)"
if command -v mu >/dev/null 2>&1 && ! grep -q 'μ dispatcher' "$(command -v mu)" 2>/dev/null; then
  echo "warning: another 'mu' is on PATH ($(command -v mu)), probably maildir-utils. μ's link in $MU_LINK_DIR may shadow it or be shadowed." >&2
fi
# recipe commit and tag: use a working git if there is one (the macOS /usr/bin/git stub fails until the
# Xcode licence is accepted), else read .git directly. The install itself never needs git.
GIT=""; for g in $(which -a git 2>/dev/null); do "$g" --version >/dev/null 2>&1 && { GIT="$g"; break; }; done
if [ -n "$GIT" ]; then
  COMMIT="$("$GIT" -C "$RECIPE" rev-parse HEAD)"
  TAG="$("$GIT" -C "$RECIPE" describe --tags --exact-match 2>/dev/null || true)"
  DIRTY="$("$GIT" -C "$RECIPE" status --porcelain --untracked-files=no | head -1)"
else
  head_ref="$(cat "$RECIPE/.git/HEAD")"
  if [ "${head_ref#ref: }" != "$head_ref" ]; then
    ref="${head_ref#ref: }"
    COMMIT="$(cat "$RECIPE/.git/$ref" 2>/dev/null || awk -v r="$ref" '$2==r{print $1}' "$RECIPE/.git/packed-refs")"
  else COMMIT="$head_ref"; fi
  # packed-refs: "<sha> refs/tags/X" (lightweight) or that line followed by "^<commit>" (annotated, peeled)
  TAG="$(awk -v c="$COMMIT" '/^\^/{ if (substr($1,2)==c && last!="") {print last; exit} next }
        { last=""; if ($2 ~ /^refs\/tags\//) { name=$2; sub("refs/tags/","",name); last=name; if ($1==c) {print name; exit} } }' \
        "$RECIPE/.git/packed-refs" 2>/dev/null || true)"
  DIRTY=""
fi
[ -n "$VERSION" ] || VERSION="${TAG:-dev-${COMMIT:0:10}}"
T="$MU_HOME/toolchains/$VERSION"
STORE="$MU_HOME/store"
avail_gb=$(df -g "$HOME" | awk 'NR==2{print $4}')
need_gb=25; [ -n "$(ls -A "$STORE" 2>/dev/null)" ] && need_gb=8   # apps already in the store cost ~nothing (APFS clones)
[ "$avail_gb" -ge "$need_gb" ] || die "need about $need_gb GB free (have ${avail_gb} GB)"

if [ -e "$T" ]; then
  [ "$FORCE" = 1 ] || die "$T exists. Use --force to rebuild it."
  say "removing previous build of $VERSION"
  rm -rf "${T:?}"
fi
T_START=$SECONDS
stage "installing μ $VERSION → $T"
mkdir -p "$T/bin" "$T/libexec" "$T/apps" "$MU_CACHE/downloads" "$MU_CACHE/pkgs" "$MU_CACHE/pip" "$MU_HOME/bin" "$MU_HOME/state/home" "$STORE"
touch "$T/.installing"

# ---------------------------------------------------------------- recipe snapshot + wrappers
rsync -a --delete --exclude .git --exclude __pycache__ "$RECIPE/" "$T/recipe/"
cp "$T/recipe/wrappers/macos/"* "$T/bin/"
chmod +x "$T/bin/"* "$T/recipe/bin/"*

# fetch NAME URL SHA256 -> path in cache (downloads once; verifies hash; can reuse a seed folder)
fetch() {
  local name="$1" url="$2" sha="$3" dest="$MU_CACHE/downloads/$1"
  if [ -f "$dest" ] && [ "$(shasum -a 256 "$dest" | cut -d' ' -f1)" = "$sha" ]; then echo "$dest"; return; fi
  if [ -n "$SEED" ] && [ -f "$SEED/$name" ] && [ "$(shasum -a 256 "$SEED/$name" | cut -d' ' -f1)" = "$sha" ]; then
    cp -c "$SEED/$name" "$dest" 2>/dev/null || cp "$SEED/$name" "$dest"; echo "$dest"; return
  fi
  echo "  downloading $name" >&2
  curl -fL --retry 3 -sS -o "$dest.part" "$url" >&2 || die "download failed: $url"
  local got; got="$(shasum -a 256 "$dest.part" | cut -d' ' -f1)"
  [ "$got" = "$sha" ] || { rm -f "$dest.part"; die "SHA-256 mismatch for $name: got $got, expected $sha"; }
  mv "$dest.part" "$dest"; echo "$dest"
}

# ---------------------------------------------------------------- downloads (micromamba now; apps in parallel)
# apps.toml -> TSV: name file url sha256 kind source_item target   (osx-arm64 entries)
awk -F'"' '
  /^\[/ { if (name!="" && file!="") print name"\t"file"\t"url"\t"sha"\t"kind"\t"item"\t"target;
           name=""; file=url=sha=kind=item=target=""
           if ($0 ~ /\.osx-arm64\]/) { name=$0; gsub(/^\[|\.osx-arm64\].*$/,"",name) } next }
  name!="" && /^file /        { file=$2 }   name!="" && /^url /    { url=$2 }   name!="" && /^sha256 / { sha=$2 }
  name!="" && /^kind /        { kind=$2 }   name!="" && /^source_item / { item=$2 } name!="" && /^target / { target=$2 }
  END { if (name!="" && file!="") print name"\t"file"\t"url"\t"sha"\t"kind"\t"item"\t"target }
' "$T/recipe/toolchain/apps.toml" > "$T/.apps.tsv"
stage "micromamba"
IFS=$'\t' read -r _ mfile murl msha _ < <(awk -F'\t' '$1=="micromamba"' "$T/.apps.tsv")
f="$(fetch "$mfile" "$murl" "$msha")"
install -m 755 "$f" "$T/libexec/micromamba"
# app archives download in the background while the conda env builds (no-op when cached)
PREFETCH_PIDS=""
while IFS=$'\t' read -r name file url sha kind _; do
  [ "$kind" = zip ] || [ "$kind" = dmg ] || continue
  ( fetch "$file" "$url" "$sha" >/dev/null ) & PREFETCH_PIDS="$PREFETCH_PIDS $!"
done < "$T/.apps.tsv"

# ---------------------------------------------------------------- conda env
export MU_TOOLCHAIN="$T" MU_HOME MU_CACHE
set +u; . "$T/recipe/lib/mu-env.sh"; set -u
stage "conda env (locked: $(grep -c '^https' "$T/recipe/toolchain/osx-arm64/env.explicit.txt") packages)"
micromamba create -y -q -p "$T/env" --file "$T/recipe/toolchain/osx-arm64/env.explicit.txt" >/dev/null
PY="$T/env/bin/python"
"$PY" -c 'import OCP, vtk, gmsh; print("  OCP, VTK", vtk.vtkVersion.GetVTKVersion(), "gmsh", gmsh.__version__)'

# ---------------------------------------------------------------- apps (shared store + APFS clones)
stage "applications (apps.toml, SHA-256 pinned; store: $STORE)"
for pid in $PREFETCH_PIDS; do wait "$pid" || die "an app download failed (see above)"; done
: > "$T/STORE_KEYS"
while IFS=$'\t' read -r name file url sha kind item target; do
  [ "$kind" = zip ] || [ "$kind" = dmg ] || continue
  key="$name-${sha:0:16}"; echo "$key" >> "$T/STORE_KEYS"
  if [ ! -d "$STORE/$key/$target" ]; then
    f="$(fetch "$file" "$url" "$sha")"
    echo "  $name: unpacking into the store (once per Mac)"
    tmp="$STORE/.$key.partial"; rm -rf "${tmp:?}"; mkdir -p "$tmp"
    if [ "$kind" = zip ]; then
      ditto -x -k "$f" "$tmp/x"; mv "$tmp/x/$item" "$tmp/$target"; rm -rf "${tmp:?}/x"
    else
      mp="$tmp/mnt"; mkdir -p "$mp"
      { yes || true; } | hdiutil attach -nobrowse -readonly -noautoopen -mountpoint "$mp" "$f" >/dev/null 2>&1
      if [ -e "$mp/$item" ]; then src="$mp/$item"; else src="$(find "$mp" -maxdepth 2 -name "$item" -print -quit)"; fi
      [ -n "$src" ] || { hdiutil detach "$mp" -quiet || true; die "no $item in $file"; }
      ditto "$src" "$tmp/$target"
      hdiutil detach "$mp" -quiet || hdiutil detach "$mp" -force -quiet
      rmdir "$mp"
    fi
    rm -rf "${STORE:?}/$key"; mv "$tmp" "$STORE/$key"
  fi
  echo "  $name → apps/$target"
  cp -cR "$STORE/$key/$target" "$T/apps/$target" 2>/dev/null || ditto "$STORE/$key/$target" "$T/apps/$target"
done < "$T/.apps.tsv"

# ---------------------------------------------------------------- pip layer (main env)
stage "pip layer (main env)"
TTC="$(awk -F'"' '/^\[text-to-cad\]/{s=1} s && /^repo/{r=$2} s && /^commit/{c=$2} END{print r"/archive/"c".zip"}' "$T/recipe/toolchain/pins.toml")"
# local paths go to pip as percent-encoded file:// URLs (safe if MU_HOME contains spaces)
furl() { "$PY" -c 'import sys, pathlib; print(pathlib.Path(sys.argv[1]).as_uri())' "$1"; }
RECIPE_URL="$(furl "$T/recipe")"; APPS_URL="$(furl "$T/apps")"
subst() { sed -e "s|{MU_RECIPE_URL}|$RECIPE_URL|g" -e "s|{MU_APPS_URL}|$APPS_URL|g" -e "s|{TEXT_TO_CAD_ARCHIVE}|$TTC|g" "$1"; }
subst "$T/recipe/toolchain/osx-arm64/env.pip.txt" > "$T/.env.pip.txt"
"$PY" -m pip install -q --no-deps -r "$T/.env.pip.txt"
SP="$("$PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
cp "$T/recipe/toolchain/patches/"*.pth "$SP/"
echo "$T/recipe/src" > "$SP/mu-src.pth"
"$PY" -m pip check >/dev/null || { "$PY" -m pip check; die "pip check failed in the main env"; }

# ---------------------------------------------------------------- CAM env (FreeCAD's Python 3.11, OCP 7.9)
stage "CAM env (FreeCAD Python + ocp-freecad-cam)"
FCPY="$T/apps/FreeCAD-1.1.4.app/Contents/Resources/bin/python"
HOME="$MU_STATE/home" "$FCPY" -m venv --system-site-packages "$T/cam-venv"
subst "$T/recipe/toolchain/osx-arm64/cam-venv.pip.txt" > "$T/.cam.pip.txt"
"$T/cam-venv/bin/python" -m pip install -q --no-deps -r "$T/.cam.pip.txt"
# FreeCAD's own modules (FreeCAD, Part, Path/CAM) live in the app bundle's lib folder, not in its Python's site-packages
CSP="$("$T/cam-venv/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
echo "$T/apps/FreeCAD-1.1.4.app/Contents/Resources/lib" > "$CSP/freecad.pth"
"$T/bin/cam-python" -c 'import FreeCAD, ocp_freecad_cam' || die "CAM env cannot import FreeCAD / ocp_freecad_cam"

# ---------------------------------------------------------------- text-to-cad registration (inside μ state)
stage "text-to-cad"
cad doctor -y --no-server > "$T/.cad-doctor.log" 2>&1 || { cat "$T/.cad-doctor.log"; die "cad doctor -y failed"; }
grep -q "registered" "$T/.cad-doctor.log" || { cat "$T/.cad-doctor.log"; die "text-to-cad did not register this installation"; }
rm -f "$T/.cad-doctor.log"

# ---------------------------------------------------------------- dispatcher, PATH links
stage "commands on PATH"
install -m 755 "$T/recipe/bin/mu" "$MU_HOME/bin/mu"
install -m 755 "$T/recipe/bin/git-lfs" "$MU_HOME/bin/git-lfs"
if [ "$SET_DEFAULT" = 1 ] || [ ! -f "$MU_HOME/default" ]; then echo "$VERSION" > "$MU_HOME/default"; fi
mkdir -p "$MU_LINK_DIR"
ln -sfn "$MU_HOME/bin/mu" "$MU_LINK_DIR/mu"
ln -sfn "$MU_HOME/bin/git-lfs" "$MU_LINK_DIR/git-lfs"   # plain `git` in any shell can push LFS repos
rm -f "$T/.apps.tsv"

stage "doctor"
MU_VERSION="$VERSION" "$MU_HOME/bin/mu" doctor || die "mu doctor found problems"
PASSED="$MU_STATE/bench/passed/$COMMIT"
if [ "$TESTS" = 1 ] && [ "$QUICK" = 1 ] && [ -z "$DIRTY" ] && [ -f "$PASSED" ]; then
  say "benchmarks: skipped (--quick; recipe ${COMMIT:0:10} passed on this Mac $(cat "$PASSED"))"
elif [ "$TESTS" = 1 ]; then
  stage "benchmarks"
  MU_VERSION="$VERSION" "$MU_HOME/bin/mu" test --workdir "$MU_STATE/bench/install-$VERSION" || die "benchmarks failed"
  "$T/bin/openfoam-eject" >/dev/null 2>&1 || true
  [ -n "$DIRTY" ] || { mkdir -p "$(dirname "$PASSED")"; date "+%Y-%m-%d %H:%M" > "$PASSED"; }
fi
stage end

# ---------------------------------------------------------------- manifest (written last: marks a complete install)
"$PY" - "$T" "$VERSION" "$COMMIT" "${DIRTY:+dirty}" "$TIMINGS" "$((SECONDS-T_START))" <<'PYEOF'
import json, sys, time, platform
t, v, c, dirty, timings, total = sys.argv[1:7]
tm = {k: int(s) for k, s in (x.split("=", 1) for x in timings.split(";") if x)}
tm = {k.split(" (")[0].split(" →")[0]: s for k, s in tm.items()}
json.dump(dict(version=v, recipe_commit=c, recipe_dirty=bool(dirty), platform="osx-arm64", role="full",
               installed=time.strftime("%Y-%m-%d %H:%M:%S"), macos=platform.mac_ver()[0],
               store_keys=open(f"{t}/STORE_KEYS").read().split(), install_seconds=int(total), timings_s=tm),
          open(f"{t}/MANIFEST.json", "w"), indent=1)
PYEOF
rm -f "$T/.installing"
say "μ $VERSION installed in $((SECONDS-T_START)) s ($(echo "$TIMINGS" | tr ';' ' ')). Default: $(cat "$MU_HOME/default"). Try: mu help"
