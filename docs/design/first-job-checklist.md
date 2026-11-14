# First job on the office PC: the owner's checklist

What the owner does to send the first compute job from the Mac to the office PC through the relay
(`docs/design/job-layer.md`, stage 1b; its §19 lists what only the PC can settle). The node is paired **by one code**
(`docs/design/pairing.md`): no USB stick, no files carried, no fingerprints compared by eye. The manual pairing of
before stays as the fallback, in [appendix A](#appendix-a-the-manual-pairing-the-fallback).

**Order:** [0 preconditions](#0-preconditions), then [1 the bootstrap in Dokploy](#1-once-the-bootstrap-in-dokploy-terminal-on-the-mac)
(once, on the Mac, about 10 minutes), [2 the Mac before the visit](#2-the-mac-before-the-visit),
[3 the visit](#3-the-visit-two-commands-at-the-pc-then-the-code): two commands at the PC and the code (15–25 minutes,
mostly install and one restart of the distro), and [4 after the visit](#4-after-the-visit-from-anywhere-the-mac-only),
from anywhere. Optional later: [5 the restart tests](#5-later-the-pending-restart-tests) and
[6 the open points](#6-the-points-only-the-pc-settles-each-with-its-pass-criterion).

Each step shows what to type and what it prints. Stop at the first output that differs: the message says what to do.
What CI has run of all this, and what it could not, is at the [end](#what-ci-already-showed).

## 0. Preconditions

- Pairing was released in v2026.11.8 (dispatcher rev 9); the fixes of its reviews follow in the next release
  (dispatcher rev 10), which this checklist describes. A merge to main also redeploys the relay (Dokploy watches
  `relay/**`): without `RELAY_ADMIN_TOKEN` it runs chisel exactly as before. The bootstrap of step 1 was done for
  office-pc on 2026-11-14; if `curl -sS https://relay.martand.systems/mu/v1/` already contains `"pairing":true` and
  `mu relay status` says `pairing on`, skip step 1.
- That release changes no env or app layer, and it is tagged at least two days before the visit, so the PC's daily
  auto-update has installed it. (If it has not, the visit's first command installs it: a few minutes.)
- The Mac runs that release: `mu update`, then `mu --version` says `dispatcher rev 10`.

## 1. Once: the bootstrap in Dokploy (Terminal on the Mac)

Type these in **Terminal, not through Claude Code**: the admin token and the relay passwords must never pass through
a transcript. Every line keeps them in files or pipes.

```sh
cd ~/mu && git pull                                   # the clone with the released pairing code
relay/gen-credentials.sh admin-token                  # adds RELAY_ADMIN_TOKEN to ~/.config/mu/relay.env (once)
relay/gen-credentials.sh remove-client office-pc      # recommended (if it says no client 'office-pc', go on)
relay/gen-credentials.sh env                          # three lines
```

In Dokploy: the relay service, **Environment** tab, replace the lines with the three that `env` printed, **Save**, then
**Deploy**. Change nothing else: not the Domains entry, not Advanced (no Mounts; Isolated Deployment and Randomize
Compose stay off: either would give the relay a new, empty volume). About 15 s later:

```sh
curl -sS https://relay.martand.systems/mu/v1/
```

Expect `{"ok":true,"pairing":true,"protocol":1,"service":"mu-relay","store":"ok"}` (what matters: it contains
`"pairing":true`). Then:

```sh
relay/gen-credentials.sh login-bundle avi-mac | mu relay login --stdin
```

Expect:

```
relay https://relay.martand.systems (fingerprint OEc5…k=): pairing service protocol 1, store 3f9a1c0e
admin token, and this computer's relay client avi-mac, stored in the login Keychain
clients: avi-mac (workstation, static, ports 7001)
```

A wrong or missing token: exit 5 `relay-admin`, nothing stored. `pairing: false`, or a 404: the Deploy did not take
the token (check the Environment tab, and the container log for `mu-relayd: …` lines).

## 2. The Mac, before the visit

| # | Do | Expect |
|---|---|---|
| 2.1 | `mu --version` | `μ v2026.11.N (dispatcher rev 10)` |
| 2.2 | `mu relay status` | `pairing on`, the client `avi-mac` |
| 2.3 | `mu compute list` | `no compute node is paired` (exit 0) |
| 2.4 | The first job's project, pinned to the release the PC will have (the same as the Mac's): below | one commit |
| 2.5 | The local reference: `mu test fea-cantilever --workdir /tmp/fea-local` | `mu test: ALL PASS (1/1)`, and `/tmp/fea-local/results.json` |

The project for step 2.4 (the benchmark is the job; `bench/results.json` is the declared output):

```sh
mkdir ~/mu-first-job && cd ~/mu-first-job && git init -q
printf '[mu]\nversion = "%s"\n\n[compute]\noutputs = ["bench/results.json"]\n' "$(cat ~/Library/Mu/default)" > mu.toml
printf 'bench/*/\n' > .gitignore        # the meshes stay out of later snapshots
git add -A && git commit -qm "first job: fea-cantilever"
```

Bring: the Mac and its charger, a phone with a hotspot. No stick.

## 3. The visit: two commands at the PC, then the code

The two PC lines never change; the code is made on the Mac only when the PC asks for it (it is valid 30 minutes, and
the PC asks only after `mu update` and the install, 10–20 minutes in).

`wsl … -- CMD` runs a non-login shell, whose PATH does not have `~/.local/bin`: the lines name `mu` by its full path,
`/home/mu/.local/bin/mu`. (v2026.11.8 printed `-- mu node host-script`, which fails with `mu: command not found`.)

**3.1 At the PC**, open **Terminal (Admin)** (an elevated PowerShell: `pair` refuses to run without it) and type:

```powershell
wsl -d mu-compute -u mu --cd /home/mu -- /home/mu/.local/bin/mu update
```

Expect `checking … for the latest release…`, then `μ v2026.11.N is the latest release. Nothing to do.` (or the
install of it, a few minutes, ending `default is now μ v2026.11.N.`). Then:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File (wsl -d mu-compute -u mu --cd /home/mu -- /home/mu/.local/bin/mu node host-script) pair
```

What it does, in order, and what it prints:

1. `mu WSL2 host: pair (the scripts copied to C:\Mu\stage\v2026.11.N-<12 hex digits>)`: the scripts and the pairing
   client are copied out of the release folder into a new folder only administrators and SYSTEM can write, and run
   from there. (`C:\Mu` gets its owner check and its ACL first; a `C:\Mu\stage` that another user made is refused:
   delete it and run the line again.)
2. The first time only: `== What a process of the distro can reach of Windows now (before the change; saved in
   C:\Mu\logs\windows-reach-before.txt)` (job-layer §19's record, which the old checklist wrote by hand).
3. `== Setting up the node (install -NoRestart)`: the install of [appendix B](#appendix-b-what-the-pair-lines-install-changes-on-this-pc),
   `ok` for what was there and `changed` for the rest. Inside `pair` these lines are expected and need nothing done:
   `info     the relay credential comes from the pairing code (next)` and, when a restart is needed,
   `info     the restart comes after the code (pair does it)`.
4. `Pairing code (from the Mac): `: **now** make the invite on the Mac (3.2). Type the code (24 characters; dashes,
   spaces and case do not matter; the screen shows asterisks). Then `code accepted: this PC becomes compute node
   office-pc on relay port 7001 for avi-mac (key SHA256:…)`. A typo is caught before anything is sent (`the code has
   a typo …`) and asked again, three times in all; after the third: `three tries: run the same line again …`.
5. `== Restarting the distro cleanly to apply the settings (about a minute)`, when install changed what only a restart
   applies (interop off, the drive options): as `test-stop` does, through the shutdown hook and the startup task. The
   code stays in the window's memory meanwhile; it is not asked again.
6. `== Pairing`, the node's side, each line as it happens:

   ```
     relay credential written: relay user office-pc, https://relay.martand.systems, port 7001
     avi-mac's key authorised: SHA256:b7T…9Q (authenticated by the code)
     host key SHA256:Xq1…Rw sent to avi-mac (authenticated by the code)
     mu-tunnel.service: connected
     waiting for avi-mac to confirm ...
     confirmed: avi-mac reached this PC through the relay
   paired with avi-mac
   ```
7. `== Status`: the whole `status` (Windows, then the distro), and one last line:

   ```
   paired with avi-mac | ready for jobs: yes
   ```

**3.2 On the Mac, when the PC shows `Pairing code (from the Mac):`**:

```sh
mu compute invite office-pc
```

```
invite 7K3M for compute node office-pc (relay port 7001), from avi-mac, valid until 14:52 (30 min)

At the office PC, open Terminal (Admin) and type these two lines:

  wsl -d mu-compute -u mu --cd /home/mu -- /home/mu/.local/bin/mu update
  powershell -NoProfile -ExecutionPolicy Bypass -File (wsl -d mu-compute -u mu --cd /home/mu -- /home/mu/.local/bin/mu node host-script) pair

When it asks for the pairing code, type:

  7K3M-Q9TD-4WXR-HN2C-PB8F-ZJ6R

Waiting for the PC. Ctrl-C stops waiting, not the invite: mu compute invite --wait 7K3M
```

It keeps the Mac awake while it waits. **Through Claude Code** (ask Claude now, when the PC asks for the code) the
command is `mu compute invite office-pc --no-wait --json`: the code is then **not** in Claude's output
(`"code": null`) but in a dialog on the Mac's screen. Keep the dialog open until the PC says `code accepted` (it stays
until OK, or until the invite ends); the Mac must be with you at the PC, or someone must read the code to you. Claude
repeats `mu compute invite --wait 7K3M --json --timeout 400` while it exits 10 (`still-waiting`). If the dialog cannot
be shown (no one signed in at the Mac's screen, a locked screen), the invite is cancelled and the command says so
(exit 1 `no-dialog`): run it in Terminal on the Mac. Off macOS, or to have the code printed anyway:
`--show-code stdout`.

On the Mac meanwhile:

```
  14:26:41  claimed by the PC (198.51.100.23); the code matched
  14:31:02  the PC's answer arrived
  14:31:02  the PC's answer verified: host key SHA256:Xq1…Rw, authenticated by the code
  14:31:03  office-pc pinned; this computer's relay client avi-mac may now reach port 7001
  14:31:19  office-pc answers through the relay: μ v2026.11.N, runner active, memory limit enforced (2.1 s)
paired office-pc
```

Nothing needs comparing: each key crossed authenticated by the code, which the relay never sees. Then, at the PC or
anywhere, on the Mac: `mu compute status office-pc`, exit 0, `office-pc: up · … s · … runner active`.

**When it goes otherwise** (each side prints its own words; the PC's line exits with the node's code):

| What | PC | Mac | Then |
|---|---|---|---|
| A typo | `the code has a typo …`, asked again | — | type it again |
| A wrong code that passes the check | `the relay does not accept this code …`, asked again | `a wrong code was tried … (1 of 20)` | type it again: three tries per run, then run the line again (install then has nothing to change); after 20 wrong codes in all the invite is locked: a new invite |
| Expired (30 min to claim) | `invite 7K3M expired …` (exit 9), `next: on the Mac: mu compute invite <node> (a new code); then run this same line again here` | exit 9 | a new `mu compute invite office-pc`; the same PC line |
| The Mac asleep, or Claude stopped: no confirm within about 2 minutes | `paired with avi-mac (not yet confirmed: …)`, then `ready for jobs: yes`, exit 0. **Nothing is undone** | — | later, on the Mac: `mu compute invite --wait 7K3M` (up to 24 h after the invite) verifies and finishes |
| The Mac has the keys but could not reach the PC through the relay yet | `paired with avi-mac, but avi-mac could not reach this PC through the relay yet \| ready for jobs: …` (exit 4) | exit 4 with `node` in the JSON | `wsl -d mu-compute -u root -- mu-wsl2-distro tunnel status`; on the Mac `mu compute status office-pc` |
| Someone else used the code first | `SOMEONE ELSE USED THIS CODE FIRST …` (exit 5): this PC told the relay, which removes the other machine's relay client | `another machine used the code (…); the PC's contest arrived` (exit 5) | a new invite; tell the lead |
| The relay (or the network) changed what crossed | `the invite was changed on the way … nothing was set up` (exit 5) | `the PC's answer was changed on the way … nothing was paired` (exit 5) | tell the lead; do not retry that code |
| The relay unreachable | `the relay does not answer (…): run the same line again in a minute` (exit 4) | `relay unreachable, trying again` | the same line again |
| The relay answering has no pairing service | `the relay answering here has no pairing service`, `next: tell the Mac's owner (curl … must say "pairing":true …)` (exit 5) | — | §1's curl on the Mac; then the same line again |
| The PC's μ is older than v2026.11.8 (no `host-script`) | `mu node: unknown subcommand 'host-script'`, and PowerShell then most likely says `The argument 'pair' to the -File parameter does not exist.` | — | the first line (`mu update`), then the second again |
| The PC's μ is new but its default release has no `pair` | `mu node host-script: … has no host script with the pair action`, `next: run: wsl -d mu-compute -u mu --cd /home/mu -- /home/mu/.local/bin/mu update` (exit 3) | — | that line, then the second again |
| `mu: command not found` | the line of v2026.11.8 (`-- mu …`) was typed | — | the lines above (`/home/mu/.local/bin/mu`) |
| After `--replace` on a rebuilt PC, `-File C:\Mu\host\mu-wsl2-host.ps1 pair` says the file does not exist | the PC was rebuilt: `C:\Mu\host` is gone | — | the `(wsl … mu node host-script) pair` line above instead |
| The restart did not happen (`… was not started again through the startup task`) | `run C:\Mu\host\mu-wsl2-host.ps1 test-stop, then this line again` | — | as it says; the line again also restarts by itself while interop is still on |
| The line stopped half way (window closed, a restart) | — | — | the same second line, the same code: it resumes |
| The same line again on a paired PC | `this PC is already paired (invite 7K3M): nothing to do`, the status, `already paired \| ready for jobs: yes` | — | nothing |
| The office reaches the relay from several public addresses (a load-balanced uplink) | no difference: addresses decide nothing, the Mac only shows each event's address. A pairing becomes **contested** only when a second claim with the right code comes without this PC's session: another machine, or this PC's state lost between two runs | `another machine used the code` | a new invite (the remedy for any contested invite) |

The `pair` line is safe to run again: install repeats nothing it did; a claim resumes with the session it saved; an
answered pairing goes straight to the wait. Once the pairing has finished, the same code is refused (`already-paired`,
"nothing to do") and does not undo it. Pairing again needs a new invite, `mu compute invite office-pc --replace`; at
the PC the Mac then prints the installed copy, `powershell -NoProfile -ExecutionPolicy Bypass -File
C:\Mu\host\mu-wsl2-host.ps1 pair` (after jobs have run, the release folder is not trusted for an administrator).

That ends the visit.

## 4. After the visit, from anywhere (the Mac only)

**4.1 Another network** (a phone's hotspot; S4's second network): `mu compute status office-pc`. Exit 0;
note the seconds (a first connection with PAM is about 12 round trips, 1–3 s; a second within five
minutes says `(connection reused)`).

**4.2 The first job** (any network):

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

**4.3 The three ways a job can end otherwise**, once each:

```sh
mu submit --on office-pc --mem 256M --wait -- python -c "b = b'x' * (1 << 30)"            # ends: … oom, exit 7
mu submit --on office-pc --time 20s --wait -- python -c "import time; time.sleep(600)"    # ends: … timeout, exit 7
J=$(mu submit --on office-pc --json -- python -c "import time; time.sleep(600)" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("job_id", ""))')
[ -n "$J" ] && { sleep 10; mu cancel "$J"; }                                              # … cancelled
```

**4.4 Removing a node** later: `mu compute remove office-pc` forgets it on the Mac and, since the Mac holds the
admin token, also deletes its relay client (the node can no longer reach the relay; `--keep-relay-client`
leaves it). It prints the line that revokes the Mac's key on the node.

Stage 1b is done when 3.1–3.2 and 4.1–4.3 pass.

## 5. Later: the pending restart tests

These are steps 3–5 of "Restart tests" in `install/hosts/wsl2/README.md`, unchanged, plus one with a job running.
Each is a Windows restart; none is needed for the job layer to work.

| # | Do | Pass |
|---|---|---|
| 5.1 | README step 3: `Stop-Service mu-wsl-shutdown`, `shutdown /r /t 0` | `last-stop: UNCLEAN`, `hook.last-shutdown: WARN` (the detector works) |
| 5.2 | README step 4: restart from the sign-in screen; then `shutdown /r /t 0 /d p:2:17` | each: `hook.last-shutdown: PASS`, `last-stop: clean`, no NUL bytes, no event 7043 |
| 5.3 | a clean stop during a job: from the Mac `mu submit --on office-pc -- python -c "import time; time.sleep(120)"`, then on the PC `C:\Mu\host\mu-wsl2-host.ps1 test-stop`; then `mu status <id> --wait` on the Mac | `succeeded`, `attempt 2`, `note` names the stop, and `mu log <id>` shows both attempts with the line `== μ: attempt 1 ended without a result …` between them; `mu compute status office-pc` answers again within about a minute of the distro's start (tunnel and runner come back by themselves) |
| 5.4 | README step 5: the first real Windows Update restart | the same as 5.2, and 5.3's tunnel and runner checks |

## 6. The points only the PC settles, each with its pass criterion

From `docs/design/job-layer.md` §19, `docs/design/pairing.md` §14 and the host script's work; each is checked by a step
above.

| Point | Checked by | Pass |
|---|---|---|
| Auto-update carried a release to the PC | 3.1, first line | `Nothing to do` (or a short install) |
| `-File (wsl … /home/mu/.local/bin/mu node host-script)` in an elevated Windows PowerShell 5.1 | 3.1, second line | `pair` starts |
| The staged copy (a new admin-only folder), the hidden code into `wsl.exe`'s stdin, the restart in the middle | 3.1 steps 1, 4, 5 | `code accepted`, then `paired with avi-mac` after the restart |
| Root's Python reaching the relay through the office network (the real Let's Encrypt chain) | 3.1 step 4 | `code accepted` |
| cgroup v2 memory limits for user units under WSL | 3.1 step 7 `node.limits`, 4.3 `oom` | enforced; the 1 GB job ends `oom` |
| What a job could reach of Windows before the change | 3.1 step 2 | recorded in `C:\Mu\logs\windows-reach-before.txt` (any answer) |
| Interop off for root and for `mu`; `/mnt/c` closed to `mu` but measurable; the startup task still works | 3.1 step 7 `reach.*`, `disk.sure`, `task.last-run` | all PASS |
| The sandboxed tunnel and its egress filter under WSL's kernel, through the office network | 3.1 steps 6 and 7 `tunnel`, `tunnel.egress` | `connected`; "a connection elsewhere is refused by the kernel" |
| The Keychain writes on the real Mac (`mu relay login`, the invite's keys) | 1, 3.2 | stored without a prompt |
| The macOS dialog with the code (Claude Code's form) | 3.2 | the dialog shows the code |
| Everything a job needs, in one line | 3.1 step 7 | `paired with avi-mac \| ready for jobs: yes` |
| The whole path, two networks, the real handshake time | 3.1, 4.1 | exit 0 on both; the times noted |
| The first real job, compared with the local reference | 4.2 | as stated there |
| `oom`, `timeout`, `cancel` on the node | 4.3 | each state once |
| A clean stop of the distro during a job | 5.3 | one re-run, a correct result, both attempts in the log |
| Windows restarts with the hook | 5.1, 5.2, 5.4 | as README says |
| Dokploy keeps the relay's named volume across a deploy | the next relay deploy after step 1 | `mu relay status`: the same store (else `mu relay restore`) |

Still open after the visit (stage 2): an unclean stop during a job, a WSL update that changes the cgroup layout (the
self-test runs again at each boot), idle beyond five minutes and a relay redeploy with a session open, the Keychain
when the Mac is used over ssh, a change of the relay's address (`mu-tunnel-refresh.timer` follows it within the hour),
how `C:\Mu\host` gets newer root-side scripts once jobs have run (pairing.md §14 Q6).

## Appendix A: the manual pairing (the fallback)

Use it when the relay has no pairing service (before step 1, or if it is ever turned off), or for a node whose
operator wants to see and compare the fingerprints. It is still tested (CI's H7 row feeds a credential on stdin and
authorises a key by hand). It needs a USB stick the owner keeps:

| Text | Secret? | From → to | How |
|---|---|---|---|
| The relay settings of `office-pc` (`relay/gen-credentials.sh client office-pc`: URL, fingerprint, user, **password**, port) | **yes** | Mac → PC | **the stick**, written on the Mac at the visit and deleted right after use (reformat it after: FAT does not erase). Fallback: type them at the PC (asked field by field; the password hidden, twice) |
| The PC's host key | no | PC → Mac | the same stick; the Mac checks its fingerprint against the PC's screen |
| The Mac's public key | no | Mac → PC | the same stick; the PC prints its fingerprint, compared with the Mac's screen |

The relay then needs a static `office-pc` client (`relay/gen-credentials.sh add-client compute office-pc 7001`, `env`,
the Dokploy edit; step 1 removed it). On the PC, in **Terminal (Admin)**; the stick is `/Volumes/STICK` on the Mac and
`E:` on the PC. Commands that run as `mu` pass `--cd /home/mu` and name `mu` by its full path (`wsl … --` runs a
non-login shell, whose PATH lacks `~/.local/bin`).

### At the PC: check, then install

**A.1 The release reached the PC** (auto-update; PLAN §14 open point):

```powershell
wsl -d mu-compute -u mu --cd /home/mu -- /home/mu/.local/bin/mu --version
```

Expect `μ v2026.11.N (dispatcher rev 10)`, the same N as the Mac. Otherwise
`wsl -d mu-compute -u mu --cd /home/mu -- /home/mu/.local/bin/mu update` (a few minutes when the release changes no layer, §0) and repeat.

**A.2 Memory limits for jobs work under this WSL** (§19; the one unknown that would stop everything):

```powershell
wsl -d mu-compute -u mu --cd /home/mu -- /home/mu/.local/bin/mu node selftest
```

Expect `PASS  memory limit for user units: MemoryMax=64M: 32 MB ran, 256 MB ended with oom-kill` (a note that
`/srv/mu` does not exist yet is fine). A FAIL prints its checks; on a WSL older than 2.9.5 the fix is
`kernelCommandLine = cgroup_no_v1=all` in `.wslconfig`, `wsl --shutdown`, repeat. Stop here if it still fails:
nodes refuse jobs without the limit.

**A.3 Get the new host scripts out of the release** (this once, before any job ran on the node: the release folder is
writable by `mu`, so later runs use the copy in `C:\Mu\host`):

```powershell
$v = (wsl -d mu-compute -u mu --cd /home/mu --exec cat .local/share/mu/default).Trim()
$h = "$env:TEMP\mu-host-$v"
New-Item -ItemType Directory -Force $h | Out-Null
Copy-Item -Force "\\wsl.localhost\mu-compute\home\mu\.local\share\mu\toolchains\$v\recipe\install\hosts\wsl2\*" $h
Get-ChildItem $h -Name
```

Expect `mu-node-gate`, `mu-wsl2-distro.sh`, `mu-wsl2-host.ps1`, `README.md`.

**A.4 What Windows a process of the distro can reach today** (§19; changes nothing; write the answer down for
PLAN §11):

```powershell
wsl -d mu-compute -u root --exec bash "/home/mu/.local/share/mu/toolchains/$v/recipe/install/hosts/wsl2/mu-wsl2-distro.sh" windows-reach
wsl -d mu-compute -u mu --cd /home/mu -- sh -c 'ls -l /run/WSL/; echo "WSL_INTEROP=$WSL_INTEROP"'
```

Expected today (interop on, `C:` open): `FAIL reach.root` and `FAIL reach.mu.interop` (each shows what `whoami.exe`
printed), `FAIL reach.mu.files`, `PASS reach.mu.df`, `FAIL host-drive`, then `windows-reach: N way(s) open`. The
second line lists WSL's interop sockets as `mu` sees them. Whatever they say is the record; nothing stops here.

**A.5 Install** (`-NoRestart`: step A.7 restarts the distro the clean way):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$h\mu-wsl2-host.ps1" install -NoRestart
```

It prints `ok` for everything set up before, and `changed` for the new steps; the exact list is
[below](#what-install-changes-on-this-pc). It ends with:

```
  warning  no relay credential yet: the tunnel starts once it has one. Run: mu-wsl2-distro tunnel credential ...
mu-wsl2-distro: N change(s).
Next: the relay credential, on stdin: mu-wsl2-distro tunnel credential
...
  note     not restarting (-NoRestart): apply the changes with a clean stop and start: C:\Mu\host\mu-wsl2-host.ps1 test-stop
Done: N change(s) (3 on Windows, M in mu-compute).
```

and under `== Job layer: the runner (as mu)` a `PASS  runner mu-node.service: active` and `PASS  memory limit
self-test`. If it says instead that μ of `mu` has no job layer yet, step A.1 was skipped. (Do not restart any other
way than step A.7.)

### Mac, then PC: the relay credential

**A.6** On the Mac, in the mu clone, with the stick in:

```sh
relay/gen-credentials.sh client office-pc 2>/dev/null > /Volumes/STICK/office-pc.relay
```

On the PC, with the stick in:

```powershell
cmd /c "wsl -d mu-compute -u root --exec /usr/local/sbin/mu-wsl2-distro tunnel credential < E:\office-pc.relay"
Remove-Item E:\office-pc.relay
```

(`Remove-Item` on a FAT stick does not erase the data: reformat the stick after the visit.) Expect:

```
/etc/mu/tunnel.env written (root:mu-tunnel 0640): relay user office-pc, https://relay.martand.systems, port 7001
mu-tunnel.service: connected (connected): 2026/…  client: Connected (Latency …)
```

This is **the PC's end of the tunnel inside WSL2, through the office network** (S4's open point), already through
the tunnel's egress filter (loopback and the relay's addresses only). Exit 1 names the reason and the next step
(`relay-credential`, `relay-fingerprint`, `relay-rule`, `relay-port-busy`, `relay-unreachable`). Without a stick:
`wsl -d mu-compute -u root -- mu-wsl2-distro tunnel credential`; it asks each value (URL, fingerprint, user and port
are shown; the password is hidden and asked twice).

### PC: the clean restart that applies `wsl.conf`, and the full status

**A.7**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Mu\host\mu-wsl2-host.ps1 test-stop
```

It stops WSL as SYSTEM (as the shutdown hook does), starts the distro through the startup task, and prints the
distro's status. Every line must be `PASS` or `INFO`, except `WARN keys` (no workstation yet), and the last line is
`ready for jobs: no (keys)`. The new lines:

| Check | Expect |
|---|---|
| `last-stop` | a clean stop |
| `wsl.conf.interop.enabled`, `wsl.conf.automount.*` | `false`, `true`, `"uid=0,gid=0,umask=077"` |
| `ssh.masked`, `openssh-server`, `listeners` | masked; installed; nothing on port 22, sshd only on `127.0.0.1:2222` |
| `node.hostkey` | `ssh-ed25519 AAAA… SHA256:…` |
| `sshd.config`, `sshd.service` | `36 settings in effect as §13.1 says`; active, listening on `127.0.0.1:2222` |
| `gate`, `jobroot`, `cgroup.delegation` | as install writes it; `/srv/mu (mu, 0700)`; `cpu memory pids` |
| `chisel`, `tunnel.credential`, `tunnel` | pinned; `relay user office-pc …`; `connected: … Connected` (after a restart, too) |
| `tunnel.egress`, `tunnel.refresh` | `only loopback and the relay (…); a connection elsewhere is refused by the kernel`; the timer enabled |
| `updates` | daily, security pockets only |
| `node.runner`, `node.limits`, `node.disk`, `node.hello` | active after the restart (linger); memory limit enforced; `N GB free at C: (/mnt/c)`; one JSON line |
| `host-drive`, `reach.root`, `reach.mu.interop` | `C /mnt/c`; a Windows program does not start (for root, and in a unit of `mu`) |
| `reach.mu.files`, `reach.mu.df`, `disk.sure` | `mu` cannot open `/mnt/c`; can read its free space; μ's disk check counts `C:` as sure |

A `FAIL tunnel.egress` that says "was not refused by it" means WSL's kernel does not enforce the filter (cgroup
BPF): the tunnel and jobs still work, without that protection against a compromised relay. Note it for §19 and go
on; the last line then also names `tunnel.egress`.

Then the Windows side too: `powershell -NoProfile -ExecutionPolicy Bypass -File C:\Mu\host\mu-wsl2-host.ps1 status`.
`task.last-run` must stay `PASS` (the startup task runs `wsl.exe` as `mu` from a Windows folder `mu` can no longer
enter; WSL is expected to report that and still run `/bin/true`).

A `FAIL reach.mu.files` after the restart means the mount options did not hold: the fallback of §13.4 is
`[automount] enabled=false` in `/etc/wsl.conf` (then `test-stop` again; the disk check sees only the virtual disk).

### PC → Mac → PC: pairing

**A.8** On the PC:

```powershell
wsl -d mu-compute -u root --cd / -- mu-wsl2-distro host-key
cmd /c "wsl -d mu-compute -u root --exec /usr/local/sbin/mu-wsl2-distro host-key > E:\office-pc.hostkey.txt"
```

Note the `fingerprint SHA256:…` line on the screen. (The `mu compute add` line it prints is for reference; A.9 is
the same command with the values filled in.)

**A.9** On the Mac, in the mu clone (the password goes through a pipe, never an argument or the screen):

```sh
HK="$(grep '^ssh-ed25519 ' /Volumes/STICK/office-pc.hostkey.txt)"
eval "$(relay/gen-credentials.sh client avi-mac 2>/dev/null | grep -E '^RELAY_(URL|FINGERPRINT)=')"
relay/gen-credentials.sh client avi-mac 2>/dev/null | sed -n 's/^RELAY_PASSWORD=//p' \
  | mu compute add office-pc --host-key "$HK" --relay "$RELAY_URL" --relay-fingerprint "$RELAY_FINGERPRINT" \
      --relay-user avi-mac --relay-port 7001 --relay-password-stdin
cp ~/Library/Mu/compute/office-pc/id_ed25519.pub /Volumes/STICK/avi-mac.pub
```

Expect `paired office-pc (nothing was sent yet)`, `host key SHA256:…` **equal to the PC's screen in A.8** (if not:
`mu compute remove office-pc` and stop: the key was altered on the way), `this computer avi-mac key SHA256:…`, and
`relay password stored in the login Keychain` (the Keychain write on the real Mac, §19). It also prints two ways
to authorise the key on the PC; A.10 is the second (from the stick).

**A.10** On the PC:

```powershell
cmd /c "wsl -d mu-compute -u root --exec /usr/local/sbin/mu-wsl2-distro authorize avi-mac - < E:\avi-mac.pub"
```

Expect `authorised avi-mac: SHA256:…`, **equal to `this computer … key` on the Mac**. The two files left on the stick
are public (the secret one went in A.6); reformat the stick after the visit.

**A.11 The last check at the PC:**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Mu\host\mu-wsl2-host.ps1 status
```

Expect every line `PASS` or `INFO`, `linux.keys  avi-mac SHA256:…` (the fingerprint of A.10), and
**`PASS  linux.ready  ready for jobs: yes`**. (`WARN linux.ready ready for jobs: no (…)` names what is missing.)

**A.12 From the Mac, on the office network:**

```sh
mu compute status office-pc
```

Expect exit 0 and `office-pc: up · <seconds> s · μ v2026.11.N … · runner active …`, then `… memory limit enforced ·
last stop clean`. Note the seconds: a first connection with PAM is about 12 round trips (around 1–3 s); run it twice
within five minutes and the second says `(connection reused)`, well under 1 s.

That ends the manual pairing: go on with [4](#4-after-the-visit-from-anywhere-the-mac-only).

## Appendix B: what the pair line's install changes on this PC

The PC was set up with the earlier scripts and every `status` check passed. On it, the `install -NoRestart` that `pair`
runs changes exactly this (everything else prints `ok`):

On Windows (3):
- `C:\Mu\host\mu-wsl2-host.ps1` and `C:\Mu\host\mu-wsl2-distro.sh` replaced by the new release's copies;
  `C:\Mu\host\mu-node-gate` and `C:\Mu\host\mu-pair.py` added; `C:\Mu\stage\<version>` (the staged
  copy).
- Not changed: WSL, the distro, `.wslconfig`, power settings, the shutdown hook (its C# source is unchanged, so it is
  not rebuilt), the startup task. `-NoRestart`: WSL is not restarted by install.

In the distro (as root):
- `/usr/local/sbin/mu-wsl2-distro` replaced; `/usr/local/lib/mu/mu-node-gate.in` (the gate's source) and
  `/usr/local/lib/mu/mu-pair.py` (the pairing client: root runs this copy) added.
- `ssh.socket` and `ssh.service` masked (the distro has no `openssh-server` yet, so nothing listens on 22 before or after).
- Packages: `openssh-server` installed, and `unattended-upgrades` and `python3` if the image lacks them (the WSL image
  normally has both). The ones install adds are listed in `/var/lib/mu-wsl2/installed-packages` for uninstall.
- `/etc/wsl.conf`: `[interop] enabled=false`, `[automount] enabled=true`, `options="uid=0,gid=0,umask=077"` added
  (the file as it is now is saved to `/var/lib/mu-wsl2/wsl.conf.orig` unless an earlier install saved one); applies
  at the restart of `pair`'s step 5.
- `/var/lib/mu-wsl2/host-drive`: the letter of the drive that holds the distro (`C /mnt/c` if it is on `C:`).
- `/etc/mu/ssh_host_ed25519_key` (+ `.pub`) made; `/etc/mu/sshd_config` written (after `sshd -t`);
  `mu-sshd.service` installed, enabled and started on `127.0.0.1:2222`.
- `/etc/mu/authorized_keys/` and an empty `…/mu`; `/usr/local/lib/mu/mu-node-gate`; `/srv/mu` (mu, 0700).
- Only if `mu`'s user manager lacks `memory` or `pids`: `/etc/systemd/system/user@.service.d/60-mu-delegate.conf`
  (not expected on Ubuntu 24.04).
- `/usr/local/lib/mu/chisel` (downloaded from GitHub, both sums checked); system user `mu-tunnel`;
  `mu-tunnel.service` installed, not enabled until the pairing gives it the credential; `mu-tunnel-refresh.timer` (hourly);
  then `/etc/mu/tunnel.hosts` and `mu-tunnel.service.d/50-mu-egress.conf` (the egress filter: loopback and the
  relay's addresses only).
- `/etc/apt/apt.conf.d/52mu-unattended-upgrades`; `apt-daily.timer` and `apt-daily-upgrade.timer` enabled if they
  were not.
- As `mu`: `~mu/.config/systemd/user/mu-node.service`, enabled and started (with the memory self-test), and
  `/srv/mu/node.json`. At install there is no relay credential yet, so the node takes the host name; the pairing's
  credential renames it to the relay user, `office-pc` (the name labels `hello` and the run records).
- At the pairing: `/etc/mu/tunnel.env` (root:`mu-tunnel` 0640), the Mac's key in
  `/etc/mu/authorized_keys/mu`, `/etc/mu/owners` (`avi-mac`: the inviter of the node's first pairing),
  `/var/lib/mu-wsl2/pair/` (root, 0700: a pairing's state while it runs; the invites this PC used).
- Not touched: the user `mu`, its deploy key, the clone, μ itself, linger, the write-back, journald and stop-timeout
  settings, `mu-clean-stop.service`, the heartbeat.

Undo: `C:\Mu\host\mu-wsl2-host.ps1 uninstall` removes all of it except the node's host key and `/srv/mu`.

## What CI already showed

`.github/workflows/job-layer.yml` runs on branch `pairing` (the branch's report names the green runs). On a GitHub
`ubuntu-24.04` runner it makes the node with these very scripts (`mu-wsl2-distro.sh install --no-wsl`, three times,
with **no** relay credential), starts the S4 replica of the relay and Traefik in pairing mode with no static compute
client, and pairs by one code as above, minus Windows: `ws` (the Mac) runs `relay login` with the replica's
`login-bundle` and `compute invite office-pc --no-wait`, root runs `mu-wsl2-distro pair run` with the code on stdin,
`ws` runs `compute invite --wait` (row S3). Every job-layer row then runs against the node paired this way. The Q rows
check the pairing itself, each shown to fail with its fault put in: a typo never reaches the relay; a wrong secret is
refused; an expired code is refused at both ends; a relay that substitutes the workstation key, the relay fingerprint
or the relay URL in the offer is refused by the node, and one that substitutes the host key in the answer is refused
by the Mac, with nothing set up on either side; the used code at the paired node does not undo its pairing;
`--replace` with the Mac's confirm missing leaves the node paired (`not yet confirmed`) and the Mac finishes later with
`--wait`; no code, password or token is in any sampled argument list, log, journal or the relay's log; `mu compute
remove` deletes the relay client. `tools/test-pair/run.sh` (macOS bash 3.2 and Linux) tests the clients against a
stand-in relay, each check with its fault.

`--no-wsl` leaves out Windows: the `pair` action of `mu-wsl2-host.ps1` is only parsed (Windows PowerShell 5.1 rules,
under pwsh) and its pure functions tested; the staging, the hidden prompt into `wsl.exe`, the restart in the middle and
everything in [6](#6-the-points-only-the-pc-settles-each-with-its-pass-criterion) are the visit's to show.
