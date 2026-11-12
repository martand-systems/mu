#!/bin/bash
# S2 probe: OpenVSP 3.56.0 on linux-64.
#  A) official Ubuntu 24.04 .deb unpacked without root: vspaero headless (its Python API is built for 3.12 only)
#  B) the Python API built from the 3.56.0 source against conda-forge Python 3.13.18 (the main env's Python),
#     headless (VSP_NO_GRAPHICS); then the aero-vlm-wing benchmark with it. Timed: decides build-on-device vs prebuilt.
. /w/tools/s2/common.sh
apt_install ca-certificates curl libgomp1 libxml2 libcminpack1 bzip2 \
  build-essential cmake swig libxml2-dev libcminpack-dev libglm-dev libeigen3-dev patchelf
mkdir -p /dl /store/vsp
fetch https://openvsp.org/zips/old/linux/OpenVSP-3.56.0-Ubuntu-24.04_amd64.deb /dl/vsp.deb 9ed933fc20fe305b2d543823fd30a6e1db3628ce19c86d6915fd0d5cba3a2744
dpkg-deb -x /dl/vsp.deb /store/vsp; find /store/vsp -maxdepth 3 -type d | head -20 > "$OUT/vsp-deb-tree.txt"
VSPBIN="$(dirname "$(find /store/vsp -name vspaero -type f | head -1)")"; res vspaero_dir "$VSPBIN"
ldd "$VSPBIN/vspaero" | grep "not found" > "$OUT/vspaero-missing.txt" || true
as_user "$VSPBIN/vspaero" -help > "$OUT/vspaero-help.txt" 2>&1 || true; head -3 "$OUT/vspaero-help.txt"

log "B) conda-forge Python 3.13.18 + numpy (as in the main env lock)"
fetch https://github.com/mamba-org/micromamba-releases/releases/download/2.10.0-0/micromamba-linux-64 /usr/local/bin/micromamba 701f39c06f8e6a670a0e186c8479b9e5c0502a2f0cda6cef65d83bdd2875e8bd
chmod +x /usr/local/bin/micromamba
MAMBA_ROOT_PREFIX=/opt/mamba micromamba create -y -q -p /opt/py -c conda-forge --override-channels python=3.13.18 numpy=2.5 >/dev/null
/opt/py/bin/python -c 'import sys, sysconfig; print(sys.version.split()[0], "shared:", sysconfig.get_config_var("Py_ENABLE_SHARED"))' | tee -a "$OUT/vsp-build.txt"
log "B) build OpenVSP 3.56.0 Libraries + src (VSP_NO_GRAPHICS) with $(nproc) cores"
curl -fsSL -o /dl/vsp-src.tgz https://codeload.github.com/OpenVSP/OpenVSP/tar.gz/refs/tags/OpenVSP_3.56.0
res vsp_src_sha256 "$(sha256sum /dl/vsp-src.tgz | cut -d' ' -f1)"
mkdir -p /build && tar -xzf /dl/vsp-src.tgz -C /build; SRC=$(echo /build/OpenVSP-*)
mkdir -p /build/libs /build/vsp
t=$SECONDS
(cd /build/libs && cmake -DCMAKE_BUILD_TYPE=Release -DVSP_NO_GRAPHICS=true -DVSP_USE_SYSTEM_LIBXML2=true -DVSP_USE_SYSTEM_GLM=true \
   -DVSP_USE_SYSTEM_CMINPACK=true -DVSP_USE_SYSTEM_EIGEN=true $SRC/Libraries > /out/libs-cmake.log 2>&1 && make -j"$(nproc)" > /out/libs-make.log 2>&1) \
  || { tail -40 /out/libs-*.log; exit 1; }
res vsp_libs_seconds $((SECONDS - t)); t=$SECONDS
(cd /build/vsp && cmake -DCMAKE_BUILD_TYPE=Release -DVSP_NO_GRAPHICS=true -DVSP_LIBRARY_PATH=/build/libs \
   -DPython3_EXECUTABLE=/opt/py/bin/python -DCMAKE_INSTALL_PREFIX=/store/vsp-api $SRC/src > /out/src-cmake.log 2>&1 \
   && make -j"$(nproc)" > /out/src-make.log 2>&1 && make install > /out/src-install.log 2>&1) \
  || { tail -60 /out/src-*.log; exit 1; }
res vsp_src_seconds $((SECONDS - t))
SO="$(find /store/vsp-api -name '_vsp*.so' | head -1)"; res vsp_api_so "$SO"
readelf -d "$SO" | grep NEEDED | tee "$OUT/vsp-so-needed.txt"
if grep -q libpython "$OUT/vsp-so-needed.txt"; then
  log "removing the libpython dependency (extension modules resolve symbols from the interpreter)"
  patchelf --remove-needed "$(grep -o 'libpython[^]]*' "$OUT/vsp-so-needed.txt" | head -1)" "$SO"
fi
PYDIR="$(dirname "$(dirname "$(dirname "$SO")")")"; res vsp_api_python_dir "$PYDIR"; ls "$PYDIR" | tee "$OUT/vsp-api-python-dir.txt"
/opt/py/bin/python -m pip install -q --no-deps "$PYDIR/openvsp_config" "$PYDIR/openvsp" "$PYDIR/utilities" "$PYDIR/degen_geom" 2>&1 | tail -3
/opt/py/bin/python -c 'import openvsp as v; print("openvsp", v.GetVSPVersion())' | tee -a "$OUT/vsp-build.txt"
log "aero-vlm-wing benchmark (the main-env script) with the built API + vspaero"
chown -R mu /home/mu; cp -r /w/benchmarks /home/mu/benchmarks; chown -R mu /home/mu/benchmarks
as_user env PATH="$VSPBIN:$PATH" /opt/py/bin/python - <<'PY' 2>&1 | tee "$OUT/aero.txt"
import importlib.util, json, pathlib, sys
sys.path.insert(0, "/home/mu/benchmarks")
spec = importlib.util.spec_from_file_location("b", "/home/mu/benchmarks/aero-vlm-wing/bench.py"); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
wd = pathlib.Path("/home/mu/aero"); wd.mkdir(exist_ok=True)
r = m.run(wd, False); print(json.dumps(r, default=str)[:1500])
print("CLalpha", r["metrics"].get("CLalpha_per_rad"), "reference osx-arm64 4.229531327252392")
PY
grep -q '"passed": true' "$OUT/aero.txt" && res openvsp ok
tar -C /store -czf "$OUT/openvsp-api-py313-linux64.tgz" vsp-api && res artifact_bytes "$(stat -c %s "$OUT/openvsp-api-py313-linux64.tgz")"
log done
