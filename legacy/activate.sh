# Engineering toolchain activation. Usage:  source ./activate.sh
# Everything (env, apps, caches) lives in this folder; nothing is installed system-wide.
# Works in zsh and bash. Deactivate by closing the shell (or `toolchain_deactivate`).

if [ -n "${BASH_SOURCE:-}" ]; then _tc_src="${BASH_SOURCE[0]}"; else _tc_src="${(%):-%x}"; fi
export ENG_ROOT="$(cd "$(dirname "$_tc_src")" && pwd)"
unset _tc_src

if [ -n "${ENG_TOOLCHAIN_ACTIVE:-}" ]; then return 0 2>/dev/null; fi
export ENG_TOOLCHAIN_ACTIVE=1
export _ENG_OLD_PATH="$PATH"

# --- micromamba / conda ------------------------------------------------------
export MAMBA_ROOT_PREFIX="$ENG_ROOT/tools/mamba"
export MAMBA_EXE="$ENG_ROOT/tools/bin/micromamba"   # wrapper: keeps HOME inside the folder
export MAMBARC="$ENG_ROOT/tools/mambarc.yaml"
export CONDARC="$ENG_ROOT/tools/mambarc.yaml"
export CONDA_PKGS_DIRS="$ENG_ROOT/.cache/conda-pkgs"
export CONDA_ENVS_PATH="$ENG_ROOT"

# --- caches and per-user dirs redirected into ./.cache -------------------------
export XDG_CACHE_HOME="$ENG_ROOT/.cache/xdg-cache"
export XDG_CONFIG_HOME="$ENG_ROOT/.cache/xdg-config"
export XDG_DATA_HOME="$ENG_ROOT/.cache/xdg-data"
export XDG_STATE_HOME="$ENG_ROOT/.cache/xdg-state"
export PIP_CACHE_DIR="$ENG_ROOT/.cache/pip"
export PIP_CONFIG_FILE=/dev/null          # ignore ~/.pip / ~/.config/pip
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PYTHONNOUSERSITE=1                 # never read/write ~/.local/lib/python*
export MPLCONFIGDIR="$ENG_ROOT/.cache/mpl"
export NPM_CONFIG_CACHE="$ENG_ROOT/.cache/npm"
export NPM_CONFIG_PREFIX="$ENG_ROOT/.cache/npm-global"
export UV_CACHE_DIR="$ENG_ROOT/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$ENG_ROOT/.cache/uv-python"
export UV_TOOL_DIR="$ENG_ROOT/.cache/uv-tools"
export UV_TOOL_BIN_DIR="$ENG_ROOT/.cache/uv-tools/bin"
export UV_NO_MODIFY_PATH=1
export UV_PYTHON_PREFERENCE=only-system   # uv uses ./env's python, never downloads one
export PYVISTA_USERDATA_PATH="$ENG_ROOT/.cache/pyvista"
export NUMBA_CACHE_DIR="$ENG_ROOT/.cache/numba"
export IPYTHONDIR="$ENG_ROOT/.cache/ipython"
export JUPYTER_CONFIG_DIR="$ENG_ROOT/.cache/jupyter"
export GMSH_HOME="$ENG_ROOT/.cache/home"
# temp files (python tempfile, cad engine socket, solvers) stay in the folder too
mkdir -p "$ENG_ROOT/.cache/tmp" && export TMPDIR="$ENG_ROOT/.cache/tmp/"

# --- text-to-cad (cad command): state, cache, no telemetry, no self-update -----
export CAD_STATE_DIR="$ENG_ROOT/.cache/cad/state"
export CAD_CACHE_DIR="$ENG_ROOT/.cache/cad/cache"
export CAD_ENGINE_DIR="$ENG_ROOT/.cache/cad/engine"     # engine socket; default is /var/folders/...
export CAD_LAUNCHER_DIR="$ENG_ROOT/.cache/cad/launcher"   # never ~/.local/bin
export CAD_TELEMETRY=0
export CAD_UPDATE_CHECK=0
export DO_NOT_TRACK=1

# --- activate ./env WITHOUT `micromamba shell hook` ------------------------------
# The hook hard-codes the real micromamba binary (bypassing the HOME wrapper), and
# `micromamba activate` then rewrites ~/.conda/environments.txt. So activate by hand:
# PATH + CONDA_PREFIX + the env's own activate.d scripts. `micromamba` on PATH is the
# wrapper in ./tools/bin; use it for install/update:  micromamba install -p "$ENG_ROOT/env" <pkg>
export CONDA_PREFIX="$ENG_ROOT/env"
export CONDA_DEFAULT_ENV="$ENG_ROOT/env"
export PATH="$ENG_ROOT/tools/bin:$ENG_ROOT/env/bin:$PATH"
if [ -d "$ENG_ROOT/env/etc/conda/activate.d" ]; then
  for _f in "$ENG_ROOT"/env/etc/conda/activate.d/*.sh; do [ -r "$_f" ] && . "$_f"; done
  unset _f
fi

toolchain_deactivate() {
  export PATH="$_ENG_OLD_PATH"
  unset ENG_TOOLCHAIN_ACTIVE _ENG_OLD_PATH
  echo "toolchain env vars remain until the shell exits; open a new shell for a clean state"
}
