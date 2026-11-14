#!/bin/bash
# Makes this machine (a GitHub ubuntu-24.04 runner, as root) a μ compute node WITH THE HOST SCRIPT, the way the office
# PC's distro is made one: install/hosts/wsl2/mu-wsl2-distro.sh install --no-wsl (docs/design/job-layer.md §13), the
# relay credential through `mu-wsl2-distro tunnel credential` on stdin, then install again once mu has μ with the job
# layer (that run enables the runner), and a third time, which must change nothing. Only what CI needs on top is done
# by hand here: the replica's test CA and relay name, the workstation user ws, and stub toolchains. CI only.
#
#   sudo tools/test-jobs/node-setup.sh REPO CHISEL CA_CRT COMPUTE_ENV OUT
#     CHISEL       the chisel 1.12.0 linux-amd64 binary (checked against toolchain/apps.toml) for the stub toolchains;
#                  the node's own copy is downloaded and checked by the host script
#     CA_CRT       the S4 replica's test CA (tools/s4/run.sh relay), added to the system's trust store
#     COMPUTE_ENV  the replica's credentials for office-pc: the output of relay/gen-credentials.sh add-client; "-": none
#                  (the node is then paired by one code, tools/test-jobs/e2e.sh S3, docs/design/pairing.md)
#     OUT          where the three install runs' output and results go (install-N.log, install-N.result)
# Stub toolchains (like tools/test-autoupdate): mu has v2026.11.5 (unpinned), v2026.11.6 (no job layer) and v2026.11.7
# (the default: this checkout's src/mu); ws has v2026.11.6 and v2026.11.7 (with libexec/chisel).
set -euo pipefail
[ "${CI:-}" = true ] || { echo "node-setup.sh: CI only (it creates users and system services)" >&2; exit 2; }
[ "$(id -u)" = 0 ] || { echo "node-setup.sh: run as root" >&2; exit 2; }
REPO="$1"; CHISEL="$2"; CA="$3"; CENV="$4"; OUT="$5"
mkdir -p "$OUT"
cd /   # the other users cannot enter the checkout
say() { printf '\n== %s\n' "$*"; }
install_run() {   # install_run N: the host script's install, its output and its result kept
  local rc=0
  "$@" > "$OUT/install-$N.log" 2>&1 || rc=$?
  cat "$OUT/install-$N.log"
  cp /run/mu-wsl2/install-result "$OUT/install-$N.result" 2> /dev/null || true
  echo "exit=$rc" >> "$OUT/install-$N.result"
  [ "$rc" = 0 ] || { echo "node-setup.sh: install run $N failed (exit $rc)" >&2; exit 1; }
}

say "the test CA and the relay's name (the replica runs on this machine)"
cp "$CA" /usr/local/share/ca-certificates/mu-s4-test-ca.crt
update-ca-certificates > /dev/null
grep -q ' relay.martand.systems$' /etc/hosts || echo "127.0.0.1 relay.martand.systems" >> /etc/hosts
getent hosts relay.martand.systems

say "what listens before the host script (the runner has its own sshd on 22)"
ss -ltnpH | awk '{ print $4, $6 }' || true

say "the host script, run 1: mu-wsl2-distro.sh install --no-wsl (from the checkout, as the owner would from the release)"
N=1 install_run bash "$REPO/install/hosts/wsl2/mu-wsl2-distro.sh" install --no-wsl
# what a restart of the distro does on the PC: systemd reads system.conf.d (DefaultTimeoutStopSec) again
systemctl daemon-reexec

if [ "$CENV" != - ]; then
  say "the relay credential, on stdin: the replica's compute.env is gen-credentials' output"
  mu-wsl2-distro tunnel credential --wait 60 < "$CENV" | tee "$OUT/tunnel-credential.log"
else
  say "no relay credential here: the node is paired by one code (e2e.sh S3)"; : > "$OUT/tunnel-credential.log"
fi

say "workstation user ws, stub toolchains"
id ws > /dev/null 2>&1 || useradd -m -s /bin/bash ws
stub() {   # stub USER V JOBS(0|1) CHISEL(0|1)
  local h; h="$(getent passwd "$1" | cut -d: -f6)/.local/share/mu"; local T="$h/toolchains/$2"
  mkdir -p "$T/recipe/bin" "$T/env/bin" "$T/libexec" "$h/bin"
  printf '{\n "version": "%s",\n "recipe_commit": "0000000000",\n "installed": "2026-11-14 10:00:00",\n "layers": []\n}\n' "$2" > "$T/MANIFEST.json"
  printf '#!/bin/bash\n# stub toolchain %s: mu run executes the command as it is\ncase "$1" in run) shift; exec "$@" ;; *) echo "stub toolchain %s: $*" ;; esac\n' "$2" "$2" > "$T/recipe/bin/mu-toolchain"
  chmod 755 "$T/recipe/bin/mu-toolchain"
  ln -sfn /usr/bin/python3 "$T/env/bin/python"
  if [ "$3" = 1 ]; then rm -rf "$T/recipe/src" "$T/recipe/install"; mkdir -p "$T/recipe/src" "$T/recipe/install"; cp -R "$REPO/src/mu" "$T/recipe/src/mu"; cp "$REPO/install/disk.sh" "$T/recipe/install/"; fi
  if [ "$4" = 1 ]; then install -m 755 "$CHISEL" "$T/libexec/chisel"; fi
  install -m 755 "$REPO/bin/mu" "$h/bin/mu"
  chown -R "$1:$1" "$(dirname "$(dirname "$h")")"
}
stub mu v2026.11.5 0 0; stub mu v2026.11.6 0 0; stub mu v2026.11.7 1 1
echo v2026.11.7 > /home/mu/.local/share/mu/default; chown mu:mu /home/mu/.local/share/mu/default
stub ws v2026.11.6 0 0; stub ws v2026.11.7 1 1
echo v2026.11.7 > /home/ws/.local/share/mu/default; chown ws:ws /home/ws/.local/share/mu/default
sudo -u ws -H git config --global user.name "ws test"; sudo -u ws -H git config --global user.email ws@example.invalid
sudo -u ws -H git config --global init.defaultBranch main

say "the host script, run 2: μ of mu has the job layer now, so it enables the runner (mu node enable as mu)"
N=2 install_run mu-wsl2-distro install --no-wsl
say "the host script, run 3: must change nothing"
N=3 install_run mu-wsl2-distro install --no-wsl

say "state"
systemctl --no-pager status mu-sshd.service mu-tunnel.service | sed -n '1,12p' || true
ss -ltnpH | awk '{ print $4, $6 }' || true
