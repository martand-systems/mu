#!/bin/bash
# S2 probe: ParaView 6.2.1 Linux tarball, pvbatch headless (OSMesa), no display, no GPU.
. /w/tools/s2/common.sh
apt_install $APT_LIST dbus
mkdir -p /dl /store
fetch https://www.paraview.org/files/v6.2/ParaView-6.2.1-MPI-Linux-Python3.12-x86_64.tar.gz /dl/pv.tgz 91eab8deadb0314856c8780fb7ccb272515cfc4bac417766103cb658ec8be7f5
log "unpack"; tar -xzf /dl/pv.tgz -C /store; rm /dl/pv.tgz
PV=/store/ParaView-6.2.1-MPI-Linux-Python3.12-x86_64; chmod -R a-w /store; res store_MB "$(du -sm /store | cut -f1)"
ldd $PV/bin/pvbatch-real 2>/dev/null | grep "not found" | tee "$OUT/pv-missing-libs.txt" || true
cat > /home/mu/pv.py <<'P'
from paraview.simple import *
s = Sphere(ThetaResolution=64, PhiResolution=64); Show(s); v = GetActiveViewOrCreate("RenderView")
v.ViewSize = [800, 600]; ResetCamera(); SaveScreenshot("/home/mu/sphere.png", v)
info = GetOpenGLInformation(); print("GL:", info.GetVendor(), "|", info.GetRenderer(), "|", info.GetVersion())
P
chown mu /home/mu/pv.py
log "pvbatch offscreen (OSMesa forced)"
as_user env VTK_DEFAULT_OPENGL_WINDOW=vtkOSOpenGLRenderWindow PYTHONPYCACHEPREFIX=/home/mu/.pyc \
  $PV/bin/pvbatch --force-offscreen-rendering /home/mu/pv.py 2>&1 | tee "$OUT/paraview.txt"
cp /home/mu/sphere.png "$OUT/" && res png_bytes "$(stat -c %s /home/mu/sphere.png)"
log "pvbatch with DISPLAY set to a missing X server (as under WSLg without X)"
as_user env DISPLAY=:99 VTK_DEFAULT_OPENGL_WINDOW=vtkOSOpenGLRenderWindow $PV/bin/pvbatch --force-offscreen-rendering /home/mu/pv.py >> "$OUT/paraview.txt" 2>&1 && res display_set ok || res display_set FAIL
[ -s /home/mu/sphere.png ] && res paraview ok
log done
