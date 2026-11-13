#!/bin/bash
# CI: a real user install inside a fresh Ubuntu 24.04 container (plain image or the official WSL image).
#   root: the one system step (apt), as an admin would run it once:  sudo ./install.sh --system
#   user: ./install.sh from their own clone (doctor + benchmarks), then a second version to measure layer reuse.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8
OUT=/out; mkdir -p $OUT; T0=$SECONDS
log() { printf '[%4ss] %s\n' "$((SECONDS - T0))" "$*" | tee -a $OUT/timeline.txt; }
log "container: $(. /etc/os-release; echo "$PRETTY_NAME") $(uname -m), $(nproc) cpus, $(free -g | awk '/Mem/{print $2}') GB RAM, $(df -h / | awk 'NR==2{print $4}') free"
apt-get update -qq && apt-get install -y -qq --no-install-recommends sudo >/dev/null   # (the WSL image has it)
id mu >/dev/null 2>&1 || useradd -m -s /bin/bash mu
echo 'mu ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/mu
cp -a /w /home/mu/mu && chown -R mu:mu /home/mu/mu
[ -d /cache ] && { mkdir -p /home/mu/.cache && ln -sfn /cache /home/mu/.cache/mu && chown -R mu:mu /cache /home/mu/.cache; }
log "system step: sudo ./install.sh --system"
( cd /home/mu/mu && ./install.sh --system ) 2>&1 | tail -3 | tee -a $OUT/system.txt
# containers have no system D-Bus (WSL2 with systemd does); flatpak's deploy step talks to it
mkdir -p /run/dbus && { dbus-daemon --system --fork 2>/dev/null || true; }
run_user() { runuser -u mu -- env -i HOME=/home/mu USER=mu SHELL=/bin/bash PATH=/usr/local/bin:/usr/bin:/bin TERM=dumb "$@"; }
log "user install #1 (cold store)"
set +e
run_user bash -c 'cd ~/mu && ./install.sh --no-modify-path' 2>&1 | tee $OUT/install1.txt; rc1=${PIPESTATUS[0]}
log "install #1 rc=$rc1"
if [ "$rc1" = 0 ]; then
  log "user install #2 (another version, same locks: layers reused)"
  run_user bash -c 'cd ~/mu && ./install.sh --version ci-second --no-modify-path' 2>&1 | tee $OUT/install2.txt; rc2=${PIPESTATUS[0]}
  log "install #2 rc=$rc2"
  run_user bash -c '~/.local/bin/mu versions; cd ~/mu && ~/.local/bin/mu version; ~/.local/bin/mu doctor --json' > $OUT/mu-status.txt 2>&1
fi
set -e
M=/home/mu/.local/share/mu
cp $M/logs/*.log $OUT/ 2>/dev/null || true
for v in $M/toolchains/*/; do cp "$v/MANIFEST.json" "$OUT/MANIFEST-$(basename "$v").json" 2>/dev/null || true; done
cp -r $M/state/bench/install-* $OUT/ 2>/dev/null || true
du -sh $M/store/* 2>/dev/null > $OUT/store-sizes.txt || true
log "done"
exit "$rc1"
