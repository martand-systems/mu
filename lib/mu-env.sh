# μ runtime environment. Sourced (bash or zsh) with MU_TOOLCHAIN set to an installed toolchain:
#     MU_TOOLCHAIN=".../Mu/toolchains/<version>" . "$MU_TOOLCHAIN/recipe/lib/mu-env.sh"
# Puts the toolchain on PATH and keeps every tool's cache/config/temp inside μ's own folders.
: "${MU_TOOLCHAIN:?MU_TOOLCHAIN must point at an installed toolchain}"
export MU_TOOLCHAIN
export MU_HOME="${MU_HOME:-$(cd "$MU_TOOLCHAIN/../.." && pwd)}"
export MU_STATE="$MU_HOME/state"
export MU_CACHE="${MU_CACHE:-$HOME/Library/Caches/Mu}"
if [ -n "${MU_ACTIVE:-}" ] && [ "${MU_ACTIVE}" = "$MU_TOOLCHAIN" ]; then return 0 2>/dev/null; fi
export MU_ACTIVE="$MU_TOOLCHAIN"
mkdir -p "$MU_STATE/tmp" "$MU_STATE/home"

# micromamba / conda (never ~/.conda: the micromamba wrapper also redirects HOME)
export MAMBA_ROOT_PREFIX="$MU_STATE/mamba"
export MAMBA_EXE="$MU_TOOLCHAIN/bin/micromamba"
export MAMBARC="$MU_TOOLCHAIN/recipe/toolchain/mambarc.yaml"
export CONDARC="$MAMBARC"
export CONDA_PKGS_DIRS="$MU_CACHE/pkgs"

# per-tool caches and config
export XDG_CACHE_HOME="$MU_STATE/xdg-cache" XDG_CONFIG_HOME="$MU_STATE/xdg-config"
export XDG_DATA_HOME="$MU_STATE/xdg-data" XDG_STATE_HOME="$MU_STATE/xdg-state"
export PIP_CACHE_DIR="$MU_CACHE/pip" PIP_CONFIG_FILE=/dev/null PIP_DISABLE_PIP_VERSION_CHECK=1
export PYTHONNOUSERSITE=1
export MPLCONFIGDIR="$MU_STATE/mpl" PYVISTA_USERDATA_PATH="$MU_STATE/pyvista"
export NUMBA_CACHE_DIR="$MU_STATE/numba" IPYTHONDIR="$MU_STATE/ipython" JUPYTER_CONFIG_DIR="$MU_STATE/jupyter"
export UV_CACHE_DIR="$MU_CACHE/uv" UV_NO_MODIFY_PATH=1 UV_PYTHON_PREFERENCE=only-system
export GMSH_HOME="$MU_STATE/home"
export TMPDIR="$MU_STATE/tmp/"

# text-to-cad (cad): state, cache, engine socket; no telemetry, no self-update
# per toolchain version: text-to-cad keeps one "current" installation in its registry, so versions must not share it
_MU_CAD="$MU_STATE/cad/$(basename "$MU_TOOLCHAIN")"
export CAD_STATE_DIR="$_MU_CAD/state" CAD_CACHE_DIR="$_MU_CAD/cache"
export CAD_ENGINE_DIR="$_MU_CAD/engine" CAD_LAUNCHER_DIR="$_MU_CAD/launcher"
unset _MU_CAD
export CAD_TELEMETRY=0 CAD_UPDATE_CHECK=0 DO_NOT_TRACK=1

# activate the env by hand (micromamba's shell hook would write ~/.conda)
export CONDA_PREFIX="$MU_TOOLCHAIN/env" CONDA_DEFAULT_ENV="$MU_TOOLCHAIN/env"
export PATH="$MU_TOOLCHAIN/bin:$MU_TOOLCHAIN/env/bin:$PATH"
if [ -d "$MU_TOOLCHAIN/env/etc/conda/activate.d" ]; then
  for _f in "$MU_TOOLCHAIN"/env/etc/conda/activate.d/*.sh; do [ -r "$_f" ] && . "$_f"; done
  unset _f
fi
