#!/bin/bash
# μ (mu) installer for macOS arm64: builds a full toolchain (workstation + local compute) on this Mac
# from the locked recipe in this repo, then runs `mu doctor` and the benchmark suite.
#
#   install/install-mac.sh [--version V] [--seed-downloads DIR] [--set-default] [--force] [--skip-tests]
#
#   --version V            toolchain version label (default: git tag, else dev-<commit>)
#   --seed-downloads DIR   reuse app archives already on disk (checked against apps.toml SHA-256)
#   --set-default          make this the default toolchain (automatic when none is set)
#   --force                rebuild an existing install of the same version
#   --skip-tests           skip the benchmark suite (doctor still runs)
#
# Installs into   ~/Library/Mu/toolchains/<version>/   (MU_HOME overrides)
# Caches in       ~/Library/Caches/Mu/                                       (MU_CACHE overrides)
# Links `mu` into ~/.local/bin (MU_LINK_DIR overrides). Nothing else outside those folders is written,
# except the OpenFOAM app's temporary read-only mount at /Volumes/OpenFOAM-v2606 while it runs.
set -euo pipefail

RECIPE="$(cd "$(dirname "$0")/.." && pwd)"
MU_HOME="${MU_HOME:-$HOME/Library/Mu}"
MU_CACHE="${MU_CACHE:-$HOME/Library/Caches/Mu}"
MU_LINK_DIR="${MU_LINK_DIR:-$HOME/.local/bin}"
VERSION=""; SEED=""; SET_DEFAULT=0; FORCE=0; TESTS=1
while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="$2"; shift 2 ;;
    --seed-downloads) SEED="$2"; shift 2 ;;
    --set-default) SET_DEFAULT=1; shift ;;
    --force) FORCE=1; shift ;;
    --skip-tests) TESTS=0; shift ;;
    -h|--help) sed -n 2,17p "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1mμ ▸ %s\033[0m\n' "$*"; }
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
avail_gb=$(df -g "$HOME" | awk 'NR==2{print $4}')
[ "$avail_gb" -ge 25 ] || die "need about 25 GB free (have ${avail_gb} GB)"

if [ -e "$T" ]; then
  [ "$FORCE" = 1 ] || die "$T exists. Use --force to rebuild it."
  say "removing previous build of $VERSION"
  rm -rf "${T:?}"
fi
say "installing μ $VERSION → $T"
mkdir -p "$T/bin" "$T/libexec" "$T/apps" "$MU_CACHE/downloads" "$MU_CACHE/pkgs" "$MU_CACHE/pip" "$MU_HOME/bin" "$MU_HOME/state/home"
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
  curl -fL --retry 3 --progress-bar -o "$dest.part" "$url" >&2 || die "download failed: $url"
  local got; got="$(shasum -a 256 "$dest.part" | cut -d' ' -f1)"
  [ "$got" = "$sha" ] || { rm -f "$dest.part"; die "SHA-256 mismatch for $name: got $got, expected $sha"; }
  mv "$dest.part" "$dest"; echo "$dest"
}

# ---------------------------------------------------------------- micromamba
say "micromamba"
mm() { awk -v k="$1" '/^\[micromamba.osx-arm64\]/{s=1;next} /^\[/{s=0} s && $1==k {gsub(/"/,"",$3); print $3}' "$T/recipe/toolchain/apps.toml"; }
f="$(fetch "$(mm file)" "$(mm url)" "$(mm sha256)")"
install -m 755 "$f" "$T/libexec/micromamba"

# ---------------------------------------------------------------- conda env
export MU_TOOLCHAIN="$T" MU_HOME MU_CACHE
set +u; . "$T/recipe/lib/mu-env.sh"; set -u
say "conda env (locked: $(grep -c '^https' "$T/recipe/toolchain/osx-arm64/env.explicit.txt") packages)"
micromamba create -y -q -p "$T/env" --file "$T/recipe/toolchain/osx-arm64/env.explicit.txt" >/dev/null
PY="$T/env/bin/python"
"$PY" -c 'import OCP, vtk, gmsh; print("  OCP, VTK", vtk.vtkVersion.GetVTKVersion(), "gmsh", gmsh.__version__)'

# ---------------------------------------------------------------- apps
say "applications (apps.toml, SHA-256 pinned)"
"$PY" - "$T/recipe/toolchain/apps.toml" > "$T/.apps.tsv" <<'PYEOF'
import sys, tomllib
d = tomllib.load(open(sys.argv[1], "rb"))
for name, a in d.items():
    p = a.get("osx-arm64")
    if p and p.get("kind") in ("zip", "dmg"):
        print("\t".join([name, p["file"], p["url"], p["sha256"], p["kind"], p["source_item"], p["target"]]))
PYEOF
while IFS=$'\t' read -r name file url sha kind item target; do
  f="$(fetch "$file" "$url" "$sha")"
  echo "  $name → apps/$target"
  stage="$(mktemp -d "$MU_CACHE/stage.XXXXXX")"
  if [ "$kind" = zip ]; then
    ditto -x -k "$f" "$stage"
    src="$stage/$item"
    ditto "$src" "$T/apps/$target"
  else
    mp="$stage/mnt"; mkdir -p "$mp"
    { yes || true; } | hdiutil attach -nobrowse -readonly -noautoopen -mountpoint "$mp" "$f" >/dev/null
    if [ -e "$mp/$item" ]; then src="$mp/$item"; else src="$(find "$mp" -maxdepth 2 -name "$item" -print -quit)"; fi
    [ -n "$src" ] || { hdiutil detach "$mp" -quiet || true; die "no $item in $file"; }
    ditto "$src" "$T/apps/$target"
    hdiutil detach "$mp" -quiet || hdiutil detach "$mp" -force -quiet
  fi
  rm -rf "${stage:?}"
done < "$T/.apps.tsv"

# ---------------------------------------------------------------- pip layer (main env)
say "pip layer (main env)"
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
say "CAM env (FreeCAD Python + ocp-freecad-cam)"
FCPY="$T/apps/FreeCAD-1.1.4.app/Contents/Resources/bin/python"
HOME="$MU_STATE/home" "$FCPY" -m venv --system-site-packages "$T/cam-venv"
subst "$T/recipe/toolchain/osx-arm64/cam-venv.pip.txt" > "$T/.cam.pip.txt"
"$T/cam-venv/bin/python" -m pip install -q --no-deps -r "$T/.cam.pip.txt"
# FreeCAD's own modules (FreeCAD, Part, Path/CAM) live in the app bundle's lib folder, not in its Python's site-packages
CSP="$("$T/cam-venv/bin/python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
echo "$T/apps/FreeCAD-1.1.4.app/Contents/Resources/lib" > "$CSP/freecad.pth"
"$T/bin/cam-python" -c 'import FreeCAD, ocp_freecad_cam' || die "CAM env cannot import FreeCAD / ocp_freecad_cam"

# ---------------------------------------------------------------- text-to-cad registration (inside μ state)
say "text-to-cad"
cad doctor -y --no-server >/dev/null 2>&1 || cad doctor -y >/dev/null 2>&1 || true
cad doctor --check >/dev/null || die "cad doctor --check failed"

# ---------------------------------------------------------------- manifest, dispatcher, PATH link
"$PY" - "$T" "$VERSION" "$COMMIT" "${DIRTY:+dirty}" <<'PYEOF'
import json, sys, time, platform
t, v, c, dirty = sys.argv[1:5]
json.dump(dict(version=v, recipe_commit=c, recipe_dirty=bool(dirty), platform="osx-arm64", role="full",
               installed=time.strftime("%Y-%m-%d %H:%M:%S"), macos=platform.mac_ver()[0]),
          open(f"{t}/MANIFEST.json", "w"), indent=1)
PYEOF
install -m 755 "$T/recipe/bin/mu" "$MU_HOME/bin/mu"
if [ "$SET_DEFAULT" = 1 ] || [ ! -f "$MU_HOME/default" ]; then echo "$VERSION" > "$MU_HOME/default"; fi
mkdir -p "$MU_LINK_DIR"; ln -sfn "$MU_HOME/bin/mu" "$MU_LINK_DIR/mu"
rm -f "$T/.installing" "$T/.apps.tsv"

say "doctor"
MU_VERSION="$VERSION" "$MU_HOME/bin/mu" doctor || die "mu doctor found problems"
if [ "$TESTS" = 1 ]; then
  say "benchmarks"
  MU_VERSION="$VERSION" "$MU_HOME/bin/mu" test --workdir "$MU_STATE/bench/install-$VERSION" || die "benchmarks failed"
  "$T/bin/openfoam-eject" >/dev/null 2>&1 || true
fi
say "μ $VERSION installed. Default: $(cat "$MU_HOME/default"). Try: mu help"
