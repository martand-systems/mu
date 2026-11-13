#!/bin/bash
# S2 probe: the main env's VTK renders headless (EGL + conda's software Mesa) for render-png / PyVista; no display.
. /w/tools/s2/common.sh
apt_install $APT_LIST
fetch https://github.com/mamba-org/micromamba-releases/releases/download/2.10.0-0/micromamba-linux-64 /usr/local/bin/micromamba 701f39c06f8e6a670a0e186c8479b9e5c0502a2f0cda6cef65d83bdd2875e8bd
chmod +x /usr/local/bin/micromamba
log "conda env from the linux-64 lock"
MAMBA_ROOT_PREFIX=/opt/mamba micromamba create -y -q -p /opt/env --file /w/toolchain/linux-64/env.explicit.txt >/dev/null
log "PyPI part of the Python lock (hashed)"
grep -e '--hash=' /w/toolchain/linux-64/env.pip.txt > /tmp/h.txt
/opt/env/bin/python -m pip install -q --no-deps --require-hashes -r /tmp/h.txt 2>&1 | tail -2
cat > /home/mu/r.sh <<'EOS'
set -e
export __EGL_VENDOR_LIBRARY_DIRS=/opt/env/share/glvnd/egl_vendor.d VTK_DEFAULT_OPENGL_WINDOW=vtkEGLRenderWindow
/opt/env/bin/python -c "import vtk; w=vtk.vtkRenderWindow(); print('window class:', w.GetClassName())"
/opt/env/bin/python /w/src/mu/post/render_png.py /w/tools/s2/fixtures/plate.step /home/mu/plate.png --views iso,top
ls -la /home/mu/plate*.png
EOS
chown mu /home/mu/r.sh
log "render-png on the benchmark plate (EGL, software)"
as_user bash /home/mu/r.sh 2>&1 | tee "$OUT/render.txt"
cp /home/mu/plate*.png "$OUT/" 2>/dev/null && res render ok
log done
