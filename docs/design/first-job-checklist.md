# First job on the office PC: the owner's checklist

What the owner does to send the first compute job from the Mac to the office PC through the relay
(`docs/design/job-layer.md`, stage 1b; its §19 lists what only the PC can settle). Everything here was run in CI
on a GitHub `ubuntu-24.04` runner (see "What CI already showed" at the end); nothing has run on the PC.

**Order:** the [preconditions](#0-preconditions), then [now, on the Mac](#1-now-on-the-mac), then
[the visit](#2-next-office-visit-one-sequence-about-15-minutes), which is one sequence of about 15 minutes. After it,
optional: [the restart tests](#3-after-the-sequence-the-pending-restart-tests) and the
[open points](#4-the-points-only-the-pc-settles-each-with-its-pass-criterion), each with its pass criterion.

Each step shows what to type and what it prints. Stop at the first output that differs: the message says what to do.

## 0. Preconditions

- The `job-layer` branch is merged and released (a version after v2026.11.6). The job commands, `libexec/chisel`
  and the new host scripts reach the PC only through a release that its daily auto-update installs.
- The Mac runs that release (`mu update` on the Mac).
- The relay is deployed with the clients `office-pc` (compute, port 7001) and `avi-mac` (workstation, 7001). It is.

## How the secret and the two public keys travel

Three texts go between the Mac and the PC. Only one is secret.

| Text | Secret? | From → to | How |
|---|---|---|---|
| The relay settings of `office-pc` (`relay/gen-credentials.sh client office-pc`: URL, fingerprint, user, **password**, port) | **yes** | Mac → PC | **A USB stick**, written on the Mac at the visit and deleted right after use. Never chat, mail, git or a cloud folder. Fallback without a stick: type them at the PC (only the 48-character password is secret; what you type is not shown) |
| The PC's host key | no | PC → Mac | The same stick (or any channel: the Mac checks its fingerprint against the PC's screen) |
| The Mac's public key | no | Mac → PC | The same stick (or any channel: the PC prints its fingerprint, compared with the Mac's screen) |

The stick must be one the owner keeps. Deleting a file on a FAT stick does not erase it; reformat the stick after
the visit, or keep it with the other secrets. The relay settings never appear in an argument list on the PC: the
`< file` redirection feeds them to `mu-wsl2-distro tunnel credential` on stdin, which writes them to
`/etc/mu/tunnel.env` (root:`mu-tunnel`, 0640; user `mu`, and so every job, cannot read it).

Below, the stick is `/Volumes/STICK` on the Mac and `E:` on the PC: use the real names.

## 1. Now, on the Mac

Nothing can be paired from away: `mu compute add` needs the PC's host key, and the PC makes it when the new host script
runs. What can be done now:

| # | Do | Expect |
|---|---|---|
| 1.1 | `mu --version` | `μ v2026.11.N (dispatcher rev 7)`, N after 6. Otherwise `mu update` |
| 1.2 | `mu compute list` | `no compute node is paired: mu compute add <node> ...` (exit 0). `has no job commands` means the release is not installed |
| 1.3 | In the mu clone: `relay/gen-credentials.sh show` | `compute office-pc ports 7001` and `workstation avi-mac ports 7001` |
| 1.4 | `curl -sS https://relay.martand.systems/health` | `OK` |
| 1.5 | The first job's project, pinned to the release the PC will have (the same as the Mac's): see below | one commit |
| 1.6 | The local reference: `mu test fea-cantilever --workdir /tmp/fea-local` | `mu test: ALL PASS (1/1)`, and `/tmp/fea-local/results.json` |

The project for step 1.5 (the benchmark is the job; `bench/results.json` is the declared output):

```sh
mkdir ~/mu-first-job && cd ~/mu-first-job && git init -q
printf '[mu]\nversion = "%s"\n\n[compute]\noutputs = ["bench/results.json"]\n' "$(cat ~/Library/Mu/default)" > mu.toml
printf 'bench/*/\n' > .gitignore        # the meshes stay out of later snapshots
git add -A && git commit -qm "first job: fea-cantilever"
```

Bring: the Mac, its charger, a USB stick, a phone with a hotspot.

## 2. Next office visit: one sequence, about 15 minutes

On the PC, open **Terminal (Admin)** (an elevated PowerShell): then nothing opens a second window. Commands that run as
`mu` pass `--cd ~`: from step 2.6 on, `mu` may not enter the Windows folder PowerShell starts in.

### At the PC: check, then install

**2.1 The release reached the PC** (auto-update; PLAN §14 open point):

```powershell
wsl -d mu-compute -u mu --cd ~ -- mu --version
```

Expect `μ v2026.11.N (dispatcher rev 7)`, the same N as the Mac. Otherwise
`wsl -d mu-compute -u mu --cd ~ -- mu update` (it builds nothing new for this release; a few minutes) and repeat.

**2.2 Memory limits for jobs work under this WSL** (§19 step 1; the one unknown that would stop everything):

```powershell
wsl -d mu-compute -u mu --cd ~ -- mu node selftest
```

Expect `PASS  memory limit for user units: MemoryMax=64M: 32 MB ran, 256 MB ended with oom-kill` (a note that
`/srv/mu` does not exist yet is fine). A FAIL prints its checks; on a WSL older than 2.9.5 the fix is
`kernelCommandLine = cgroup_no_v1=all` in `.wslconfig`, `wsl --shutdown`, repeat. Stop here if it still fails:
nodes refuse jobs without the limit.

**2.3 Get the new host scripts out of the release** (this once, before any job ran on the node: the release folder is
writable by `mu`, so later runs use the copy in `C:\Mu\host`):

```powershell
$v = (wsl -d mu-compute -u mu --cd ~ --exec cat .local/share/mu/default).Trim()
$h = "$env:TEMP\mu-host-$v"
Copy-Item -Recurse -Force "\\wsl.localhost\mu-compute\home\mu\.local\share\mu\toolchains\$v\recipe\install\hosts\wsl2" $h
Get-ChildItem $h -Name
```

Expect `mu-node-gate`, `mu-wsl2-distro.sh`, `mu-wsl2-host.ps1`, `README.md`.

**2.4 What Windows a process of the distro can reach today** (§19 step 2; changes nothing; write the answer down for
PLAN §11):

```powershell
wsl -d mu-compute -u root --exec bash "/home/mu/.local/share/mu/toolchains/$v/recipe/install/hosts/wsl2/mu-wsl2-distro.sh" windows-reach
```

Expected today (interop on, `C:` open): `FAIL reach.root` and `FAIL reach.mu.interop` (each shows what `whoami.exe`
printed), `FAIL reach.mu.files`, `PASS reach.mu.df`, `FAIL host-drive`, then `windows-reach: N way(s) open`. Whatever
it says is the record; nothing stops here.

**2.5 Install** (`-NoRestart`: step 2.7 restarts the distro the clean way):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$h\mu-wsl2-host.ps1" install -NoRestart
```

It prints `ok` for everything set up before, and `changed` for the new steps; the exact list is
[below](#what-install-changes-on-this-pc). It ends with:

```
  warning  no relay credential yet: the tunnel starts once it has one. Run: mu-wsl2-distro tunnel credential ...
mu-wsl2-distro: N change(s).
Restart the distro to apply them all (from Windows: wsl --terminate mu-compute).
Next: the relay credential, on stdin: mu-wsl2-distro tunnel credential
...
  warning  not restarting (-NoRestart): the changes apply at the next start of WSL ...
Done: N change(s) (3 on Windows, M in mu-compute).
```

and under `== Job layer: the runner (as mu)` a `PASS  runner mu-node.service: active` and `PASS  memory limit
self-test`. If it says instead that μ of `mu` has no job layer yet, step 2.1 was skipped.

### Mac, then PC: the relay credential

**2.6** On the Mac, in the mu clone, with the stick in:

```sh
relay/gen-credentials.sh client office-pc 2>/dev/null > /Volumes/STICK/office-pc.relay
```

On the PC, with the stick in:

```powershell
cmd /c "wsl -d mu-compute -u root --exec /usr/local/sbin/mu-wsl2-distro tunnel credential < E:\office-pc.relay"
Remove-Item E:\office-pc.relay
```

Expect:

```
/etc/mu/tunnel.env written (root:mu-tunnel 0640): relay user office-pc, https://relay.martand.systems, port 7001
mu-tunnel.service: connected (connected): 2026/…  client: Connected (Latency …)
```

This is **the PC's end of the tunnel inside WSL2, through the office network** (S4's open point). Exit 1 names the
reason and the next step (`relay-credential`, `relay-fingerprint`, `relay-rule`, `relay-port-busy`,
`relay-unreachable`). Without a stick: `wsl -d mu-compute -u root -- mu-wsl2-distro tunnel credential`, type the six
lines, then an empty line.

### PC: the clean restart that applies `wsl.conf`, and the full status

**2.7**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Mu\host\mu-wsl2-host.ps1 test-stop
```

It stops WSL as SYSTEM (as the shutdown hook does), starts the distro through the startup task, and prints the
distro's status. Every line must be `PASS` or `INFO`, except `WARN keys` (no workstation yet). The new lines:

| Check | Expect |
|---|---|
| `last-stop` | a clean stop |
| `wsl.conf.interop.enabled`, `wsl.conf.automount.*` | `false`, `true`, `"uid=0,gid=0,umask=077"` |
| `ssh.masked`, `openssh-server`, `listeners` | masked; installed; nothing on port 22, sshd only on `127.0.0.1:2222` |
| `node.hostkey` | `ssh-ed25519 AAAA… SHA256:…` |
| `sshd.config`, `sshd.service` | `36 settings in effect as §13.1 says`; active, listening on `127.0.0.1:2222` |
| `gate`, `jobroot`, `cgroup.delegation` | as install writes it; `/srv/mu (mu, 0700)`; `cpu memory pids` |
| `chisel`, `tunnel.credential`, `tunnel` | pinned; `relay user office-pc …`; `connected: … Connected` (after a restart, too) |
| `updates` | daily, security pockets only |
| `node.runner`, `node.limits`, `node.disk`, `node.hello` | active after the restart (linger); memory limit enforced; `N GB free at C: (/mnt/c)`; one JSON line |
| `host-drive`, `reach.root`, `reach.mu.interop` | `C /mnt/c`; a Windows program does not start (for root, and in a unit of `mu`) |
| `reach.mu.files`, `reach.mu.df`, `disk.sure` | `mu` cannot open `/mnt/c`; can read its free space; μ's disk check counts `C:` as sure |

Then the Windows side too: `powershell -NoProfile -ExecutionPolicy Bypass -File C:\Mu\host\mu-wsl2-host.ps1 status`.
`task.last-run` must stay `PASS` (the startup task runs `wsl.exe` as `mu` from a Windows folder `mu` can no longer
enter; WSL is expected to report that and still run `/bin/true`).

A `FAIL reach.mu.files` after the restart means the mount options did not hold: the fallback of §13.4 is
`[automount] enabled=false` in `/etc/wsl.conf` (then `test-stop` again; the disk check sees only the virtual disk).

### PC → Mac → PC: pairing

**2.8** On the PC:

```powershell
wsl -d mu-compute -u root --cd / -- mu-wsl2-distro host-key
cmd /c "wsl -d mu-compute -u root --exec /usr/local/sbin/mu-wsl2-distro host-key > E:\office-pc.hostkey.txt"
```

Note the `fingerprint SHA256:…` line on the screen.

**2.9** On the Mac, in the mu clone (the password goes through a pipe, never an argument):

```sh
HK="$(grep '^ssh-ed25519 ' /Volumes/STICK/office-pc.hostkey.txt)"
eval "$(relay/gen-credentials.sh client avi-mac 2>/dev/null | grep -E '^RELAY_(URL|FINGERPRINT)=')"
relay/gen-credentials.sh client avi-mac 2>/dev/null | sed -n 's/^RELAY_PASSWORD=//p' \
  | mu compute add office-pc --host-key "$HK" --relay "$RELAY_URL" --relay-fingerprint "$RELAY_FINGERPRINT" \
      --relay-user avi-mac --relay-port 7001 --relay-password-stdin
cp ~/Library/Mu/compute/office-pc/id_ed25519.pub /Volumes/STICK/avi-mac.pub
```

Expect `paired office-pc (nothing was sent yet)`, `host key SHA256:…` **equal to the PC's screen in 2.8** (if not:
`mu compute remove office-pc` and stop: the key was altered on the way), `this computer avi-mac key SHA256:…`, and
`relay password stored in the login Keychain` (the Keychain write on the real Mac, §19 step 5).

**2.10** On the PC:

```powershell
cmd /c "wsl -d mu-compute -u root --exec /usr/local/sbin/mu-wsl2-distro authorize avi-mac - < E:\avi-mac.pub"
```

Expect `authorised avi-mac: SHA256:…`, **equal to `this computer … key` on the Mac**. Then delete the stick's three
files (and reformat it later).

### Mac: the whole path, then the first job

**2.11** On the office network, then on the phone's hotspot (S4's second network):

```sh
mu compute status office-pc
```

Expect exit 0 and `office-pc: up · <seconds> s · μ v2026.11.N … · runner active …`, then `… memory limit enforced ·
last stop clean`. Note the seconds: a first connection with PAM is about 12 round trips (around 1–3 s); run it twice
within five minutes and the second says `(connection reused)`, well under 1 s.

**2.12 The first job** (hotspot or office network):

```sh
cd ~/mu-first-job
mu submit --on office-pc --name fea-cantilever --time 1h -- python -m mu.bench fea-cantilever --workdir bench
mu status <job-id> --wait          # exit 0: succeeded
mu log <job-id> --tail 20          # the benchmark's own PASS lines
mu fetch <job-id>                  # bench/results.json and runs/<job-id>.json
python3 - <<'EOF'
import json
pc = json.load(open("bench/results.json"))["results"]["fea-cantilever"]
mac = json.load(open("/tmp/fea-local/results.json"))["results"]["fea-cantilever"]
print("passed: PC", pc["passed"], " Mac", mac["passed"])
for k in ("tip_h5", "tip_h2.5", "nodes_h5", "nodes_h2.5"):
    a, b = pc["metrics"][k], mac["metrics"][k]
    print("%-11s PC %-12.6g Mac %-12.6g  %.3f %%" % (k, a, b, 100 * abs(a - b) / b))
EOF
```

Pass: both `passed: True` (each against its own platform's reference, `benchmarks/reference/`), tip deflections
within 0.5 % of each other, node counts within 5 %. `runs/<job-id>.json` names `office-pc`, the snapshot and the
toolchain.

**2.13 The three ways a job can end otherwise**, once each (each `mu status … --wait` exits 7):

```sh
J=$(mu submit --on office-pc --mem 256M --json -- python -c "b = b'x' * (1 << 30)" | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])')
mu status $J --wait                # state oom
J=$(mu submit --on office-pc --time 20s --json -- python -c "import time; time.sleep(600)" | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])')
mu status $J --wait                # state timeout, after about 20 s
J=$(mu submit --on office-pc --json -- python -c "import time; time.sleep(600)" | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])')
sleep 10; mu cancel $J             # state cancelled
```

That ends the sequence. Stage 1b is done when 2.1–2.13 pass.

### What install changes on this PC

The PC was set up with the earlier scripts and every `status` check passed. On it, `install -NoRestart` changes
exactly this (everything else prints `ok`):

On Windows (3):
- `C:\Mu\host\mu-wsl2-host.ps1` and `C:\Mu\host\mu-wsl2-distro.sh` replaced by the new release's copies;
  `C:\Mu\host\mu-node-gate` added.
- Not changed: WSL, the distro, `.wslconfig`, power settings, the shutdown hook (its C# source is unchanged, so it is
  not rebuilt), the startup task. `-NoRestart`: WSL is not restarted by install.

In the distro (as root):
- `/usr/local/sbin/mu-wsl2-distro` replaced; `/usr/local/lib/mu/mu-node-gate.in` (the gate's source) added.
- `ssh.socket` and `ssh.service` masked (the distro has no `openssh-server` yet, so nothing listens on 22 before or after).
- Packages: `openssh-server` installed, and `unattended-upgrades` and `python3` if the image lacks them (the WSL image
  normally has both). The ones install adds are listed in `/var/lib/mu-wsl2/installed-packages` for uninstall.
- `/etc/wsl.conf`: `[interop] enabled=false`, `[automount] enabled=true`, `options="uid=0,gid=0,umask=077"` added
  (the file as it is now is saved to `/var/lib/mu-wsl2/wsl.conf.orig` unless an earlier install saved one); applies
  at the restart of 2.7.
- `/var/lib/mu-wsl2/host-drive`: the letter of the drive that holds the distro (`C /mnt/c` if it is on `C:`).
- `/etc/mu/ssh_host_ed25519_key` (+ `.pub`) made; `/etc/mu/sshd_config` written (after `sshd -t`);
  `mu-sshd.service` installed, enabled and started on `127.0.0.1:2222`.
- `/etc/mu/authorized_keys/` and an empty `…/mu`; `/usr/local/lib/mu/mu-node-gate`; `/srv/mu` (mu, 0700).
- Only if `mu`'s user manager lacks `memory` or `pids`: `/etc/systemd/system/user@.service.d/60-mu-delegate.conf`
  (not expected on Ubuntu 24.04).
- `/usr/local/lib/mu/chisel` (downloaded from GitHub, both sums checked); system user `mu-tunnel`;
  `mu-tunnel.service` installed, not enabled until 2.6 gives it the credential.
- `/etc/apt/apt.conf.d/52mu-unattended-upgrades`; `apt-daily.timer` and `apt-daily-upgrade.timer` enabled if they
  were not.
- As `mu`: `~mu/.config/systemd/user/mu-node.service`, enabled and started (with the memory self-test), and
  `/srv/mu/node.json`. At 2.5 there is no relay credential yet, so the node takes the host name; `tunnel credential`
  (2.6) renames it to the relay user, `office-pc` (the name labels `hello` and the run records).
- Not touched: the user `mu`, its deploy key, the clone, μ itself, linger, the write-back, journald and stop-timeout
  settings, `mu-clean-stop.service`, the heartbeat.

Undo: `C:\Mu\host\mu-wsl2-host.ps1 uninstall` removes all of it except the node's host key and `/srv/mu`.

## 3. After the sequence: the pending restart tests

These are steps 3–5 of "Restart tests" in `install/hosts/wsl2/README.md`, unchanged, plus one with a job running.
Each is a Windows restart; none is needed for the job layer to work.

| # | Do | Pass |
|---|---|---|
| 3.1 | README step 3: `Stop-Service mu-wsl-shutdown`, `shutdown /r /t 0` | `last-stop: UNCLEAN`, `hook.last-shutdown: WARN` (the detector works) |
| 3.2 | README step 4: restart from the sign-in screen; then `shutdown /r /t 0 /d p:2:17` | each: `hook.last-shutdown: PASS`, `last-stop: clean`, no NUL bytes, no event 7043 |
| 3.3 | §19 step 9: from the Mac `mu submit --on office-pc -- python -c "import time; time.sleep(120)"`, then on the PC `C:\Mu\host\mu-wsl2-host.ps1 test-stop`; then `mu status <id> --wait` on the Mac | `succeeded`, `attempt 2`, `note` names the stop; `mu compute status office-pc` answers again within about a minute of the distro's start (tunnel and runner come back by themselves) |
| 3.4 | README step 5: the first real Windows Update restart | the same as 3.2, and 3.3's tunnel and runner checks |

## 4. The points only the PC settles, each with its pass criterion

From `docs/design/job-layer.md` §19 and the host script's work; each is checked by a step above.

| Point | Checked by | Pass |
|---|---|---|
| Auto-update carried a release to the PC | 2.1 | the PC reports the Mac's version |
| cgroup v2 memory limits for user units under WSL 3.0.5 | 2.2, 2.7 `node.limits`, 2.13 `oom` | `oom-kill` in the self-test; the 1 GB job ends `oom` |
| What a job could reach of Windows before the change | 2.4 | recorded (any answer) |
| `openssh-server` installs with port 22 never open; the root steps on real WSL | 2.5, 2.7 `listeners` | nothing on 22 at any point |
| Interop off for root and for `mu`; `/mnt/c` closed to `mu` but measurable | 2.7 `reach.*` | all PASS after the restart |
| μ's disk check sees `C:` with interop off | 2.7 `disk.sure`, `node.disk` | `C:` counted as sure |
| The startup task still works with `/mnt/c` closed to `mu` | 2.7 Windows `status` | `task.last-run` PASS |
| The sandboxed tunnel service under WSL's kernel | 2.6, 2.7 `tunnel` | connected, and again after the restart |
| The tunnel from WSL2 through the office network | 2.6 | `connected` |
| The Keychain write on the real Mac | 2.9 | `stored in the login Keychain`; 2.11 works without a prompt |
| The whole path, two networks, the real handshake time with PAM | 2.11 | exit 0 on both; the times noted |
| The first real job, compared with the local reference | 2.12 | as stated there |
| `oom`, `timeout`, `cancel` on the node | 2.13 | each state once |
| A clean stop of the distro during a job | 3.3 | one re-run, a correct result |
| Windows restarts with the hook | 3.1, 3.2, 3.4 | as README says |

Still open after the visit (stage 2): an unclean stop during a job, a WSL update that changes the cgroup layout (the
self-test runs again at each boot), idle beyond five minutes and a relay redeploy with a session open, the Keychain
when the Mac is used over ssh.

## What CI already showed

`.github/workflows/job-layer.yml` on branch `job-layer` (green in run 43541460952: 45 PASS, 30 faults each shown to fail) makes the node on a GitHub `ubuntu-24.04` runner with these
very scripts (`mu-wsl2-distro.sh install --no-wsl` three times, the credential through `tunnel credential` on stdin
from the relay replica's `gen-credentials` output), runs the whole job-layer suite through a replica of the relay and
Traefik against that sshd, gate and tunnel, checks the script's own `status` (each check shown to fail with its fault
put in), then uninstalls and reinstalls. `--no-wsl` leaves out the Windows parts, which is why 2.4, 2.7 `reach.*`,
`host-drive`, `disk.sure` and the task check exist only here.
