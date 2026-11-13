#!/bin/bash
# Makes this machine (a GitHub ubuntu-24.04 runner, as root) a μ compute node the way docs/design/job-layer.md §13
# says the host script will make the WSL2 distro one, plus a workstation user. CI only: it refuses to run outside CI.
#
#   sudo tools/test-jobs/node-setup.sh REPO CHISEL CA_CRT COMPUTE_ENV
#     CHISEL       the chisel 1.12.0 linux-amd64 binary (already checked against toolchain/apps.toml)
#     CA_CRT       the S4 replica's test CA (tools/s4/run.sh relay), added to the system's trust store
#     COMPUTE_ENV  the replica's credentials for office-pc (RELAY_URL, RELAY_FINGERPRINT, RELAY_USER, RELAY_PASSWORD)
#
# What it makes, as in §13 (the host script, mu-wsl2-distro, is to make the same files; this is the reference until
# it does): ssh.socket and ssh.service masked before openssh-server is installed; /etc/mu/ssh_host_ed25519_key;
# /etc/mu/sshd_config; mu-sshd.service on 127.0.0.1:2222; /etc/mu/authorized_keys/mu; the gate in
# /usr/local/lib/mu/mu-node-gate; /srv/mu (mu, 0700); /usr/local/lib/mu/chisel; /etc/mu/tunnel.env and
# mu-tunnel.service (user mu-tunnel). Users: mu (no sudo, linger), ws (the workstation, another user), mu-tunnel.
# Stub toolchains (like tools/test-autoupdate): mu has v2026.11.5 (unpinned), v2026.11.6 (no job layer) and v2026.11.7
# (the default: this checkout's src/mu); ws has v2026.11.6 and v2026.11.7 (with libexec/chisel).
set -euo pipefail
[ "${CI:-}" = true ] || { echo "node-setup.sh: CI only (it creates users and system services)" >&2; exit 2; }
[ "$(id -u)" = 0 ] || { echo "node-setup.sh: run as root" >&2; exit 2; }
REPO="$1"; CHISEL="$2"; CA="$3"; CENV="$4"
cd /   # the other users cannot enter the checkout
val() { sed -n "s/^$2=//p" "$1" | tail -n 1; }
say() { printf '\n== %s\n' "$*"; }

say "users"
id mu >/dev/null 2>&1 || useradd -m -s /bin/bash mu
id ws >/dev/null 2>&1 || useradd -m -s /bin/bash ws
id mu-tunnel >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin mu-tunnel
loginctl enable-linger mu
MU_UID="$(id -u mu)"
for i in $(seq 1 50); do [ -S "/run/user/$MU_UID/bus" ] && break; sleep 0.2; done
ls -la "/run/user/$MU_UID" | head -5

say "openssh-server, with the distribution's ssh units masked first (§13 ensure_ssh_masked)"
ss -ltnH | awk '{print $4}' | grep -E ':22$' && echo "(an sshd already listens on 22 before this script: stopped below)" || true
systemctl mask ssh.socket ssh.service
systemctl stop ssh.socket ssh.service 2>/dev/null || true
dpkg -s openssh-server >/dev/null 2>&1 || DEBIAN_FRONTEND=noninteractive apt-get install -y -q openssh-server
mkdir -p /etc/mu/authorized_keys /usr/local/lib/mu
chmod 755 /etc/mu /etc/mu/authorized_keys /usr/local/lib/mu
[ -f /etc/mu/ssh_host_ed25519_key ] || ssh-keygen -q -t ed25519 -N '' -C mu-node -f /etc/mu/ssh_host_ed25519_key
chmod 600 /etc/mu/ssh_host_ed25519_key

say "/etc/mu/sshd_config (§13.1) and mu-sshd.service (§13.2)"
cat > /etc/mu/sshd_config.new <<'C'
Port 2222
AddressFamily inet
ListenAddress 127.0.0.1
HostKey /etc/mu/ssh_host_ed25519_key
PidFile /run/mu-sshd.pid
UsePAM yes
AllowUsers mu
PermitRootLogin no
AuthenticationMethods publickey
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
HostbasedAuthentication no
PermitEmptyPasswords no
StrictModes yes
AuthorizedKeysFile /etc/mu/authorized_keys/%u
ExposeAuthInfo yes
ForceCommand /usr/local/lib/mu/mu-node-gate
PermitTTY no
AllowTcpForwarding no
AllowStreamLocalForwarding no
AllowAgentForwarding no
X11Forwarding no
PermitTunnel no
GatewayPorts no
PermitUserRC no
PermitUserEnvironment no
PrintMotd no
PrintLastLog no
MaxAuthTries 3
MaxSessions 10
MaxStartups 10:30:60
LoginGraceTime 20
ClientAliveInterval 30
ClientAliveCountMax 3
LogLevel VERBOSE
C
install -d -m 755 /run/sshd   # sshd -t needs its privilege separation folder (the unit's RuntimeDirectory makes it later)
/usr/sbin/sshd -t -f /etc/mu/sshd_config.new
mv /etc/mu/sshd_config.new /etc/mu/sshd_config; chmod 644 /etc/mu/sshd_config
cat > /etc/systemd/system/mu-sshd.service <<'U'
[Unit]
Description=μ node sshd (loopback only, forced command)
After=network.target
[Service]
Type=notify
ExecStartPre=/usr/sbin/sshd -t -f /etc/mu/sshd_config
ExecStart=/usr/sbin/sshd -D -e -f /etc/mu/sshd_config
RuntimeDirectory=sshd
RuntimeDirectoryMode=0755
RuntimeDirectoryPreserve=yes
KillMode=process
Restart=on-failure
RestartSec=2
TimeoutStopSec=5
[Install]
WantedBy=multi-user.target
U
[ -f /etc/mu/authorized_keys/mu ] || : > /etc/mu/authorized_keys/mu
chown root:root /etc/mu/authorized_keys/mu; chmod 644 /etc/mu/authorized_keys/mu

say "the gate (§7.1), root-owned, with this machine's two values"
sed -e "s|@MU_BIN@|/home/mu/.local/share/mu/bin/mu|" -e "s|@MU_KEYS@|/etc/mu/authorized_keys/mu|" \
  "$REPO/install/hosts/wsl2/mu-node-gate" > /usr/local/lib/mu/mu-node-gate
chown root:root /usr/local/lib/mu/mu-node-gate; chmod 755 /usr/local/lib/mu/mu-node-gate
systemctl daemon-reload
systemctl enable --now mu-sshd.service

say "/srv/mu (§8.1)"
mkdir -p /srv/mu; chown mu:mu /srv/mu; chmod 700 /srv/mu

say "the test CA and the relay's name (the replica runs on this machine)"
cp "$CA" /usr/local/share/ca-certificates/mu-s4-test-ca.crt
update-ca-certificates >/dev/null
grep -q ' relay.martand.systems$' /etc/hosts || echo "127.0.0.1 relay.martand.systems" >> /etc/hosts
getent hosts relay.martand.systems

say "the tunnel (§13.3): /usr/local/lib/mu/chisel, /etc/mu/tunnel.env, mu-tunnel.service"
install -o root -g root -m 755 "$CHISEL" /usr/local/lib/mu/chisel
umask 027
{ printf 'AUTH=%s:%s\n' "$(val "$CENV" RELAY_USER)" "$(val "$CENV" RELAY_PASSWORD)"
  printf 'RELAY_URL=%s\nRELAY_FINGERPRINT=%s\nRELAY_PORT=7001\n' "$(val "$CENV" RELAY_URL)" "$(val "$CENV" RELAY_FINGERPRINT)"; } > /etc/mu/tunnel.env
umask 022
chown root:mu-tunnel /etc/mu/tunnel.env; chmod 640 /etc/mu/tunnel.env
cat > /etc/systemd/system/mu-tunnel.service <<'U'
[Unit]
Description=μ relay tunnel (chisel client, reverse tunnel to mu-sshd)
After=network-online.target mu-sshd.service
Wants=network-online.target
[Service]
User=mu-tunnel
EnvironmentFile=/etc/mu/tunnel.env
Environment=CHISEL_WS_TIMEOUT=10s
ExecStart=/usr/local/lib/mu/chisel client --fingerprint ${RELAY_FINGERPRINT} --keepalive 25s --max-retry-interval 15s ${RELAY_URL} R:127.0.0.1:${RELAY_PORT}:127.0.0.1:2222
Restart=always
RestartSec=5
TimeoutStopSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
[Install]
WantedBy=multi-user.target
U
systemctl daemon-reload
systemctl enable --now mu-tunnel.service

say "stub toolchains"
stub() {   # stub USER V JOBS(0|1) CHISEL(0|1)
  local h; h="$(getent passwd "$1" | cut -d: -f6)/.local/share/mu"; local T="$h/toolchains/$2"
  mkdir -p "$T/recipe/bin" "$T/env/bin" "$T/libexec" "$h/bin"
  printf '{\n "version": "%s",\n "recipe_commit": "0000000000",\n "installed": "2026-11-14 10:00:00",\n "layers": []\n}\n' "$2" > "$T/MANIFEST.json"
  printf '#!/bin/bash\n# stub toolchain %s: mu run executes the command as it is\ncase "$1" in run) shift; exec "$@" ;; *) echo "stub toolchain %s: $*" ;; esac\n' "$2" "$2" > "$T/recipe/bin/mu-toolchain"
  chmod 755 "$T/recipe/bin/mu-toolchain"
  ln -sfn /usr/bin/python3 "$T/env/bin/python"
  if [ "$3" = 1 ]; then rm -rf "$T/recipe/src"; mkdir -p "$T/recipe/src"; cp -R "$REPO/src/mu" "$T/recipe/src/mu"; fi
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

say "the runner: mu node enable (as mu, no root)"
sudo -u mu -H env XDG_RUNTIME_DIR="/run/user/$MU_UID" /home/mu/.local/share/mu/bin/mu node enable --name office-pc --min-free-gb 1 || true

say "state"
systemctl --no-pager status mu-sshd.service mu-tunnel.service | sed -n '1,12p' || true
ss -ltnp | grep -E ':2222|:22 ' || true
