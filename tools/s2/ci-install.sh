#!/bin/bash
# CI: a real user install inside a fresh Ubuntu 24.04 container (plain image or the official WSL image).
#   root: the one system step (apt), as an admin would run it once:  sudo ./install.sh --system
#   user: ./install.sh from their own clone (doctor + benchmarks), then a second version to measure layer reuse.
# The flatpak download cache (MU_CACHE/flatpak) is never restored from the CI cache, so install #1 downloads PrusaSlicer
# from Flathub. Before install #2 the PrusaSlicer layer is deleted and Flathub is blocked (/etc/hosts): the layer must be
# rebuilt from the download cache with no network. Then mu gc's pruning of that cache is checked.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8
OUT=/out; mkdir -p $OUT; T0=$SECONDS
log() { printf '[%4ss] %s\n' "$((SECONDS - T0))" "$*" | tee -a $OUT/timeline.txt; }
rx() { cat /sys/class/net/eth0/statistics/rx_bytes 2>/dev/null || echo 0; }   # bytes received by this container
mb() { echo "$(( $1 / 1048576 ))"; }
log "container: $(. /etc/os-release; echo "$PRETTY_NAME") $(uname -m), $(nproc) cpus, $(free -g | awk '/Mem/{print $2}') GB RAM, $(df -h / | awk 'NR==2{print $4}') free"
log "unit tests: WSL disk check (bash $BASH_VERSION)"
bash /w/tools/test-wsl-disk/run.sh bash > $OUT/test-wsl-disk.txt 2>&1 || { cat $OUT/test-wsl-disk.txt; exit 1; }
tail -1 $OUT/test-wsl-disk.txt | tee -a $OUT/timeline.txt
apt-get update -qq && apt-get install -y -qq --no-install-recommends sudo >/dev/null   # (the WSL image has it)
id mu >/dev/null 2>&1 || useradd -m -s /bin/bash mu
echo 'mu ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/mu
cp -a /w /home/mu/mu && chown -R mu:mu /home/mu/mu
[ -d /cache ] && { rm -rf /cache/flatpak /cache/flatpak.lock; mkdir -p /home/mu/.cache && ln -sfn /cache /home/mu/.cache/mu && chown -R mu:mu /cache /home/mu/.cache; }
log "system step: sudo ./install.sh --system"
( cd /home/mu/mu && ./install.sh --system ) 2>&1 | tail -3 | tee -a $OUT/system.txt
# containers have no system D-Bus (WSL2 with systemd does); flatpak's deploy step talks to it
mkdir -p /run/dbus && { dbus-daemon --system --fork 2>/dev/null || true; }
run_user() { runuser -u mu -- env -i HOME=/home/mu USER=mu SHELL=/bin/bash PATH=/usr/local/bin:/usr/bin:/bin TERM=dumb "$@"; }
M=/home/mu/.local/share/mu
log "user install #1 (cold store, cold flatpak cache)"
set +e
r0=$(rx)
run_user bash -c 'cd ~/mu && ./install.sh --no-modify-path' 2>&1 | tee $OUT/install1.txt; rc1=${PIPESTATUS[0]}
log "install #1 rc=$rc1, received $(mb $(( $(rx) - r0 ))) MB over the network"
grep -h "^flatpak: " $M/logs/install-*.log 2>/dev/null | tee -a $OUT/timeline.txt
log "flatpak download cache: $(du -sm /home/mu/.cache/mu/flatpak 2>/dev/null | cut -f1) MB"
if [ "$rc1" = 0 ]; then
  log "delete the PrusaSlicer layer and block Flathub: install #2 must rebuild it from the cache, offline"
  for d in $M/store/app-prusaslicer-*; do find "$d" -type d -exec chmod u+w {} +; rm -rf "$d"; done
  printf '0.0.0.0 dl.flathub.org flathub.org\n:: dl.flathub.org flathub.org\n' >> /etc/hosts
  log "user install #2 (another version, same locks: layers reused, PrusaSlicer from the cache)"
  n1=$(ls $M/logs/install-*.log | wc -l); r0=$(rx)
  run_user bash -c 'cd ~/mu && ./install.sh --version ci-second --no-modify-path' 2>&1 | tee $OUT/install2.txt; rc2=${PIPESTATUS[0]}
  log "install #2 rc=$rc2, received $(( $(rx) - r0 )) bytes over the network"
  l2="$(ls -t $M/logs/install-*.log | head -1)"
  if grep -q "^flatpak: downloading" "$l2"; then log "install #2 downloaded from Flathub: FAIL"; rc2=1
  else log "install #2: no flatpak download ($(grep -c 'installing prusaslicer from the download cache' "$l2") layer rebuilt from the cache)"; fi
  run_user bash -c '~/.local/bin/mu versions; cd ~/mu && ~/.local/bin/mu version; ~/.local/bin/mu doctor --json; ~/.local/bin/mu gc' > $OUT/mu-status.txt 2>&1
  [ "$rc2" = 0 ] || rc1=$rc2
  log "mu gc --yes prunes cached flatpak commits nothing pins, across MU_HOMEs (MU_CACHE is shared)"
  # a stale commit (pinned by nobody), one pinned by another MU_HOME's installed version, a record of a deleted
  # MU_HOME, and a remote ref left on a real commit (a download killed before the reset): gc must remove the stale
  # ref and the deleted MU_HOME's record, keep the other MU_HOME's pin, and put the remote ref back on the placeholder
  run_user bash -c 'R=~/.cache/mu/flatpak/repo; P=app/com.prusa3d.PrusaSlicer/x86_64/stable
    c=$(ostree --repo=$R rev-parse mu-placeholder/$P); a=$(ostree --repo=$R refs | sed -n "s|^mu/$P/||p")
    ostree --repo=$R refs --create=mu/app/org.example.Stale/x86_64/stable/$c $c
    ostree --repo=$R refs --create=mu/app/org.example.Other/x86_64/stable/$c $c
    mkdir -p ~/other-mu/toolchains/v1 && echo "flatpak app/org.example.Other/x86_64/stable@$c" > ~/other-mu/toolchains/v1/CACHE_REFS
    h=$(cd ~/other-mu && pwd -P); echo "$h" > ~/.cache/mu/flatpak/homes/$(printf %s "$h" | cksum | cut -d" " -f1)
    echo /home/mu/deleted-mu > ~/.cache/mu/flatpak/homes/123
    echo "$a" > $R/refs/remotes/flathub/$P
    echo "homes before gc:"; cat ~/.cache/mu/flatpak/homes/*
    ~/.local/bin/mu gc --yes
    echo "refs after gc:"; ostree --repo=$R refs | sort
    echo "homes after gc:"; cat ~/.cache/mu/flatpak/homes/*
    [ "$(cat $R/refs/remotes/flathub/$P)" = "$c" ] && echo "remote ref: back on the placeholder"' > $OUT/gc.txt 2>&1; cat $OUT/gc.txt
  after="$(sed -n '/refs after gc/,/homes after gc/p' $OUT/gc.txt)"
  if echo "$after" | grep -q "org.example.Stale" || ! echo "$after" | grep -q "org.example.Other" \
     || [ "$(echo "$after" | grep -c '^mu/')" != 3 ] || sed -n '/homes after gc/,$p' $OUT/gc.txt | grep -q deleted-mu \
     || ! grep -q "remote ref: back on the placeholder" $OUT/gc.txt; then
    log "gc: FAIL"; rc1=1
  else log "gc: the stale commit was pruned, the 2 pinned commits and the other MU_HOME's pin kept, the deleted MU_HOME's record dropped, the remote ref reset"; fi
  log "the PrusaSlicer layer is sealed like every layer (its files are its own, not the cache's)"
  if [ -n "$(find $M/store/app-prusaslicer-* -type f -perm -u+w -print -quit)" ]; then log "layer: writable files: FAIL"; rc1=1
  else log "layer: every file read-only"; fi
fi
set -e
cp $M/logs/*.log $OUT/ 2>/dev/null || true
for v in $M/toolchains/*/; do cp "$v/MANIFEST.json" "$OUT/MANIFEST-$(basename "$v").json" 2>/dev/null || true; done
cp -r $M/state/bench/install-* $OUT/ 2>/dev/null || true
du -sh $M/store/* /home/mu/.cache/mu/flatpak 2>/dev/null > $OUT/store-sizes.txt || true
log "done"
exit "$rc1"
