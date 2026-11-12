#!/bin/bash
# Build OpenVSP's Python API (headless, VSP_NO_GRAPHICS) for the main environment's Python on linux-64.
# OpenVSP's Linux releases ship the API only for the system Python (3.12 on Ubuntu 24.04); μ's env is Python 3.13.
#   build-openvsp-api.sh SRC.tar.gz BUILD_DIR PREFIX     (env: PYTHON = a Python 3.13 with numpy; JOBS)
# Verified by tools/s2/probe-openvsp.sh: the aero benchmark gives CLalpha identical to macOS (4.229531327252392).
set -euo pipefail
SRC_TGZ="$1"; B="$2"; PREFIX="$3"; PY="${PYTHON:?PYTHON must point at the env Python}"; J="${JOBS:-$(nproc)}"
mkdir -p "$B"; tar -xzf "$SRC_TGZ" -C "$B"; SRC="$(echo "$B"/OpenVSP-*)"
mkdir -p "$B/libs" "$B/vsp"
( cd "$B/libs" && cmake -DCMAKE_BUILD_TYPE=Release -DVSP_NO_GRAPHICS=true -DVSP_USE_SYSTEM_LIBXML2=true -DVSP_USE_SYSTEM_GLM=true \
    -DVSP_USE_SYSTEM_CMINPACK=true -DVSP_USE_SYSTEM_EIGEN=true "$SRC/Libraries" && make -j"$J" )
( cd "$B/vsp" && cmake -DCMAKE_BUILD_TYPE=Release -DVSP_NO_GRAPHICS=true -DVSP_LIBRARY_PATH="$B/libs" \
    -DPython3_EXECUTABLE="$PY" -DCMAKE_INSTALL_PREFIX="$PREFIX" "$SRC/src" && make -j"$J" && make install )
# conda-forge's Python is statically linked: an extension must take Python's symbols from the interpreter, not
# load a second libpython
for so in $(find "$PREFIX" -name '_vsp*.so'); do
  lib="$(readelf -d "$so" | sed -n 's/.*NEEDED.*\[\(libpython[^]]*\)\].*/\1/p')"
  [ -z "$lib" ] || patchelf --remove-needed "$lib" "$so"
done
[ -d "$PREFIX/python/openvsp/openvsp" ] || { echo "no python/openvsp in $PREFIX" >&2; exit 1; }
