#!/bin/bash
# S2 probe: FreeCAD 1.1.4 AppImage extracted without FUSE; its Python 3.11 hosts the CAM venv; CAM benchmark job headless.
. /w/tools/s2/common.sh
apt_install $APT_LIST dbus
mkdir -p /dl /store
fetch https://github.com/FreeCAD/FreeCAD/releases/download/1.1.4/FreeCAD_1.1.4-Linux-x86_64-py311.AppImage /dl/fc.AppImage f6dc6ba676e5ac96a565ebc8d657232f94c6158e85b4352141bd1a46f6b43434
log "extract (--appimage-extract, no FUSE)"; chmod +x /dl/fc.AppImage; (cd /store && /dl/fc.AppImage --appimage-extract >/dev/null); rm /dl/fc.AppImage
FC=/store/squashfs-root; chmod -R a-w /store; res store_MB "$(du -sm /store | cut -f1)"
ls $FC/usr/bin | tr '\n' ' ' > "$OUT/freecad-bin.txt"
export QT_QPA_PLATFORM=offscreen
log "bundled python + import FreeCAD"
as_user env PYTHONPYCACHEPREFIX=/home/mu/.pyc $FC/usr/bin/python -c "import sys; sys.path.append('$FC/usr/lib'); import FreeCAD, Part; print('python', sys.version.split()[0], 'FreeCAD', '.'.join(FreeCAD.Version()[:3]))" 2>&1 | tee "$OUT/freecad.txt"
log "CAM venv on the bundled python (--system-site-packages) + the CAM lock (same versions as macOS)"
mkdir -p /home/mu/recipe && cp -r /w/toolchain /home/mu/recipe/ && chown -R mu /home/mu/recipe
sed -e 's| --hash=[^ ]*||g' -e "s|{MU_RECIPE_URL}|file:///home/mu/recipe|g" /w/toolchain/osx-arm64/cam-venv.pip.txt > /home/mu/cam.txt
cat > /home/mu/cam.sh <<EOS
set -euo pipefail
export QT_QPA_PLATFORM=offscreen PYTHONPYCACHEPREFIX=/home/mu/.pyc
$FC/usr/bin/python -m venv --system-site-packages /home/mu/cam-venv
SP=\$(/home/mu/cam-venv/bin/python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])'); echo $FC/usr/lib > \$SP/freecad.pth
/home/mu/cam-venv/bin/python -m pip install -q --no-deps -r /home/mu/cam.txt
/home/mu/cam-venv/bin/python -c 'import FreeCAD, ocp_freecad_cam, cadquery, build123d; print("cam imports ok", cadquery.__version__, build123d.__version__)'
cd /home/mu && /home/mu/cam-venv/bin/python /w/benchmarks/cam-plate/cam_job.py /w/tools/s2/fixtures/plate.step /home/mu/plate_grbl.nc
/home/mu/cam-venv/bin/python - <<'PY'
import re; g=open("/home/mu/plate_grbl.nc").read(); zs=[float(z) for z in re.findall(r"Z(-?\d+\.?\d*)", g)]; cut=[z for z in zs if z<0]
print("cam lines", len(g.splitlines()), "deepest", min(cut), "pocket_floor", any(abs(z+3)<1e-3 for z in cut))
PY
EOS
chown mu /home/mu/cam.txt /home/mu/cam.sh
as_user bash /home/mu/cam.sh 2>&1 | tee -a "$OUT/freecad.txt"
cp /home/mu/plate_grbl.nc "$OUT/" 2>/dev/null && res cam_lines "$(wc -l < /home/mu/plate_grbl.nc)"
grep -q "cam lines" "$OUT/freecad.txt" && res freecad ok
log done
