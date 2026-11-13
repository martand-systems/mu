#!/bin/bash
# S2 probe: OpenFOAM v2606 from ESI's noble .debs, unpacked without root into a read-only store; serial + MPI cavity.
. /w/tools/s2/common.sh
apt_install $APT_LIST dbus
B=https://dl.openfoam.com/repos/deb/dists/noble/main/pool/2606_0
mkdir -p /dl /store/of
fetch $B/binary-amd64/openfoam2606_2606.0~rc2-1_amd64.deb /dl/of.deb ef129961af180427090ff04938845c54a21419376474baa371559bad31e19a26
fetch $B/binary-all/openfoam2606-common_2606.0~rc2-1_all.deb /dl/of-common.deb db8b1e75f3e8af5a5ec39f3f0a37f601b31c05eed6667aac25de58034a11f6ba
fetch $B/binary-all/openfoam2606-tutorials_2606.0~rc2-1_all.deb /dl/of-tut.deb 255f9ddbf7d88dca11b63adb60398ec04992033302e64ab425e56eccb6c16646
log "unpack (dpkg-deb -x, no root needed)"
for d in /dl/of*.deb; do dpkg-deb -x "$d" /store/of; done
OF=/store/of/usr/lib/openfoam/openfoam2606; chmod -R a-w /store; res store_MB "$(du -sm /store | cut -f1)"
cat > /home/mu/run.sh <<EOS
set -euo pipefail
export OMPI_MCA_btl_vader_single_copy_mechanism=none OMPI_MCA_btl=self,vader
cd /home/mu; rm -rf cav; cp -r $OF/tutorials/incompressible/icoFoam/cavity/cavity cav; chmod -R u+w cav; cd cav
$OF/etc/openfoam -c 'echo WM_PROJECT_VERSION=\$WM_PROJECT_VERSION; blockMesh > log.blockMesh && icoFoam > log.icoFoam && checkMesh > log.checkMesh && foamListTimes | tail -1'
grep -m1 "cells:" log.checkMesh
cat > system/decomposeParDict <<'D'
FoamFile { version 2.0; format ascii; class dictionary; object decomposeParDict; }
numberOfSubdomains 2; method scotch;
D
$OF/etc/openfoam -c 'decomposePar -force > log.decompose && mpirun -np 2 --oversubscribe icoFoam -parallel > log.par && reconstructPar -latestTime > log.rec && echo parallel_ok'
which snappyHexMesh simpleFoam >/dev/null 2>&1 || $OF/etc/openfoam -c 'command -v snappyHexMesh simpleFoam potentialFoam'
EOS
chmod +x /home/mu/run.sh; chown mu /home/mu/run.sh
log "run cavity (serial + 2-rank MPI) as user mu"
as_user bash /home/mu/run.sh 2>&1 | tee "$OUT/openfoam.txt"
grep -q parallel_ok "$OUT/openfoam.txt" && res openfoam ok
log done
