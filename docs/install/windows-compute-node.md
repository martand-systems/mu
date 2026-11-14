# A Windows 11 PC as a μ compute node

This guide turns a Windows 11 PC into an always-on μ compute node: a Linux system (Ubuntu 24.04 in WSL2) that runs
μ's solvers for jobs sent from a Mac, keeps running when nobody is signed in to Windows, comes back by itself after a
restart, and is stopped cleanly before Windows restarts. It goes from a new PC to "ready for jobs" and a first job.
You do not need to know WSL; each step says where to type, what to type and what you should see.

> **What has run on real hardware, and what has not (2026-11-14).** The only node is the office PC
> (`MSI-PROZ890-PC`). It was first set up **by hand** in spike S3, then the scripts of this guide were run over that
> setup: `install` found most settings already right and added the rest (the shutdown hook, the folder permissions,
> the job layer). It was then paired with the owner's Mac and ran its first remote job (§9–§10). **Nobody has yet run
> these scripts on a brand-new PC from nothing.** Steps marked *(not yet run from scratch)* are exactly as the
> scripts are written, and their parts are tested in CI (`tools/test-wsl2-host/`, `.github/workflows/job-layer.yml`),
> but on Windows they have only run on a PC that was already set up. If a step prints something this guide does not
> show, stop and send the window's text and the log in `C:\Mu\logs\` to the lead.

> **Known issues in v2026.11.8, and how this guide routes around them** (to be fixed in the next release):
> 1. `wsl -d mu-compute -u mu --cd ~ -- mu …` fails with **`mu: command not found`**: that kind of command starts a
>    shell that does not read `~/.profile`, so `~/.local/bin` is not on its PATH. This guide always writes the full
>    path, `/home/mu/.local/bin/mu`. The Mac's `mu compute invite` and some of μ's own hints still print the short
>    form: use the lines in this guide instead.
> 2. The installer **asks for a sudo password** that the user `mu` does not have when a new release needs a new
>    Ubuntu package. Run the system step as root first (§11.2).
> 3. The `mu autoupdate on` at the end of an install can fail with systemctl's error hidden in the log (the install
>    ends with `auto-update could not be turned on`). Run it by hand (§7.4).

**Contents:** [0 Before you start](#0-before-you-start) · [1 Hardware and BIOS](#1-hardware-and-bios) ·
[2 Windows](#2-windows) · [3 WSL](#3-install-wsl) · [4 The scripts](#4-put-the-scripts-on-the-pc) ·
[5 First run](#5-first-run-of-the-host-script) · [6 Deploy key](#6-the-deploy-key-the-one-manual-github-step) ·
[7 Install μ](#7-second-run-clone-and-install-μ-about-15-h) · [8 Hand over to the task](#8-hand-wsl-to-the-startup-task-and-check) ·
[9 Pairing](#9-pair-the-node-with-the-mac-one-code) · [10 Verify](#10-verify-from-the-mac-and-run-a-first-job) ·
[11 Day 2](#11-day-2-operations) · [12 Troubleshooting](#12-troubleshooting) · [13 Why each setting](#13-why-each-setting-is-there)

## 0. Before you start

**People.** You need a Windows **administrator** account on the PC; a **GitHub admin** of `martand-systems/mu` for
two minutes (step 6); and **the owner with their Mac** for the pairing (step 9), because only a Mac logged in to the
relay with its admin token can pair a node ([relay.md](relay.md)).

**Time** (estimates, except where a measurement is cited):

| Step | Time | Attention |
|---|---|---|
| 1–3 BIOS, Windows, WSL (one restart) | 20–40 min | yes |
| 5 First run of the host script | 5–15 min (downloads Ubuntu, a few hundred MB) | yes |
| 6 Deploy key | 2 min | a GitHub admin |
| 7 Install μ | **about 1.5 h** (96 min on the office PC with empty caches, 19 min of it CPU: it waits for downloads) | none |
| 8 Hand-over and check | 5 min | yes |
| 9 Pairing | 15–25 min with the owner's Mac (most of it the first time `pair` installs the job layer) | yes |
| 10 First job | 5 min | no |

**Where to type.** The owner hit both mistakes: Linux commands typed into PowerShell, and Windows commands typed into
Linux. There are four places, and each command below says which:

| Name in this guide | How to open it | Its prompt looks like |
|---|---|---|
| **Terminal (Admin)** | right-click the Start button → **Terminal (Admin)** → **Yes** at the UAC prompt. It opens Windows PowerShell with administrator rights | `PS C:\WINDOWS\system32>` |
| **PowerShell** | Start → type `Terminal` → Enter (not as administrator) | `PS C:\Users\<you>>` |
| **Linux, as root** | in Terminal (Admin): `wsl -d mu-compute -u root` | `root@<PC name>:/mnt/c/WINDOWS/system32#` or `root@<PC name>:~#` |
| **Mac Terminal** | on the Mac: Applications → Utilities → Terminal (not through Claude Code, where a step says so) | `… %` |

- A Windows command typed into Linux gives `command not found` (for `wsl`, `powershell`, `Get-…`). Type `exit` to
  leave Linux and get the `PS …>` prompt back.
- A Linux command typed into PowerShell gives `… is not recognized as the name of a cmdlet, function, script file,
  or operable program`. Open the Linux shell first, or use the one-line `wsl -d mu-compute -u … -- <command>` form
  this guide gives.
- One-line forms are preferred here: `wsl -d mu-compute -u root -- <command>` runs one Linux command as root and
  returns to PowerShell. `-u mu` runs it as the user μ runs as.

## 1. Hardware and BIOS

What the office PC has (`docs/verification/2026-11-13-S3-wsl.md`): Intel Core Ultra 7 265K (20 cores), 31 GB RAM,
one NVMe drive with 465 GB (130 GB free before μ; 89 GB free on 2026-11-14 with μ and the distro), Windows 11, wired
network. Any x86-64 PC with virtualisation works (PLAN §6.1a). Plan for at least 16 GB of RAM and 100 GB free on an
SSD: μ's store took 19 GB on the office PC, and jobs need room for their files.

In the BIOS/UEFI setup (a key such as Del or F2 at power-on; the menu names differ by maker):

1. **Turn virtualisation on.** Intel: "Intel Virtualization Technology" / "VT-x" (on some boards "Intel VMX"). AMD:
   "SVM Mode". WSL2 does not start without it. Check it later in Windows: Task Manager → Performance → CPU →
   "Virtualization: Enabled".
2. **Recommended, not verified on the office PC: power on after a power cut.** Often "Restore on AC Power Loss" or
   "AC Back" → **Power On**. Without it, the PC stays off after a power cut until someone presses the button. Not set
   or tested on the office PC.
3. **No password or PIN before Windows starts** (a BIOS power-on password, or a BitLocker pre-boot PIN): either one
   stops the PC at boot until someone types it, so the node would not come back after a restart.

## 2. Windows

1. **Windows 11, 64-bit.** The office PC runs Windows 11 build 26300.9711. Its edition (Home or Pro) was not
   recorded. Nothing in the scripts needs Pro: they use the scheduled-task, service and registry settings every
   edition has. Home has not been tested.
2. **Sign in as the administrator who will own the node.** The distro, `%UserProfile%\.wslconfig` and the startup
   task all belong to the Windows user who runs the script, and that user must be an administrator. Use this account
   for every step in this guide. Do not run the script by typing **another** account's password at a UAC prompt: the
   script would then set the node up for that other account (it warns: `running as … while … is signed in`).
   A local account or a Microsoft account: the office PC's account type was not recorded, and the startup task's
   "run whether signed in or not" (S4U) mode has only been tested with that one account.
3. **Install Windows updates and restart** until Windows Update has nothing left, so that the first restarts do not
   come in the middle of the install.
4. **Leave Windows Update on.** Its restarts are handled: the node starts again after them with nobody signed in, and
   is stopped cleanly before them (§13). A real Windows Update restart has not happened on the node yet.
5. **Wired network** is recommended. The node only makes outbound connections (to GitHub, the download servers and the
   relay on port 443); nothing needs opening in a firewall.

## 3. Install WSL

**Where:** Terminal (Admin).

1. Check whether WSL is installed:

   ```powershell
   wsl --version
   ```

   - It prints a block starting `WSL version: 2.…` (or later; the office PC has `3.0.5.0`): WSL is installed. If the
     version is older than 2.5.4, the script updates it (step 5). Go to step 4 of this guide.
   - It prints a help text, or says WSL is not installed: go on.

2. Install WSL without a Linux distribution (the script installs its own):

   ```powershell
   wsl --install --no-distribution
   ```

   It downloads and enables WSL; this takes a few minutes.

3. **Restart Windows** (Start → Power → Restart), sign in again as the same user, open Terminal (Admin), and run
   `wsl --version` again. It must now print `WSL version: …`.

If you skip this step, the script does the same itself: it runs `wsl --install --no-distribution`, prints
`wsl --install --no-distribution: exit code N. Restart Windows, then run this script again.` and stops.

If WSL refuses to start a distro later with a message about virtualisation, go back to step 1.1.

## 4. Put the scripts on the PC

The scripts are in the private repo `martand-systems/mu`, folder `install/hosts/wsl2/`. You need these three files,
together in one folder: `mu-wsl2-host.ps1`, `mu-wsl2-distro.sh`, `mu-node-gate` (`README.md` there is the reference).
Use the latest release (on a Mac with μ: `mu --version`; or the repo's Releases/Tags page on GitHub). Either way works:

- **Download on the PC:** in a browser signed in to a GitHub account that can read the repo, open
  `https://github.com/martand-systems/mu/archive/refs/tags/v2026.11.8.zip` (put the latest release's name in), save
  it, right-click → **Extract All**. The folder is then
  `C:\Users\<you>\Downloads\mu-2026.11.8\install\hosts\wsl2` (GitHub drops the `v` from the folder name).
- **Copy from a Mac** that has the repo: copy the folder `install/hosts/wsl2` to a USB stick, then to the PC, for
  example to `C:\Users\<you>\Downloads\wsl2`.

Windows marks downloaded files as "from the internet" and blocks scripts by default. The commands below pass
`-ExecutionPolicy Bypass`, which allows this one run only; nothing else needs changing.

## 5. First run of the host script

*(Not yet run from scratch. On the office PC, `install` ran over the hand-made setup of S3.)*

**Where:** Terminal (Admin).

1. Go to the folder with the three files (put your folder in):

   ```powershell
   cd $env:USERPROFILE\Downloads\mu-2026.11.8\install\hosts\wsl2
   ```

2. Run the install, naming the release to clone in the Linux system (`-Branch`; a tag works) and asking it to install
   μ there too (`-InstallMu`):

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\mu-wsl2-host.ps1 install -InstallMu -Branch v2026.11.8
   ```

   Why `-Branch`: without it the distro clones the repo's default branch, and an install from a commit after the
   latest release is labelled `dev-<commit>` instead of the release's name. (Read in the installer, not tried on a PC.)

   From a PowerShell that is not elevated, the script says
   `mu-wsl2-host: 'install' needs administrator rights: continuing in an elevated window`, opens a second window and
   ends with `Press Enter to close this window`. From Terminal (Admin) it runs in the same window.

3. Read what it prints. It goes through these sections, each line `ok` (already right) or `changed` (set now). Every
   run is also logged to `C:\Mu\logs\wsl2-host-install-<date>-<time>.log`.

   ```
   mu WSL2 host: install on <PC NAME> for <PC NAME>\<you>
     distro mu-compute (C:\Mu\wsl\mu-compute), user mu; WSL VM: 23 GB of 31 GB, 20 processors, 8 GB swap

   == Folders
   == WSL
   == Distro mu-compute
     installing Ubuntu-24.04 as mu-compute in C:\Mu\wsl\mu-compute (a download of a few hundred MB)
   == WSL settings (C:\Users\<you>\.wslconfig)
   == Inside mu-compute (as root)
   == Windows power
   == Shutdown hook
   == Startup task
   == Start
   ```

   (The numbers in the second line are this PC's: 75% of its memory, all its processors.) The section "Inside
   mu-compute" is the Linux script; its own headings are `System`, `Clean stops and write-back`, `User services`,
   `GitHub access`, the job layer's three sections, and `Security updates`. On this first run it stops at GitHub
   access with a key to add (step 6), says `--install-mu: skipped until mu can read the repo`, and later
   `μ of mu has no job layer yet …`: both are expected now.

4. The end of the first run looks like this:

   ```
   Done: N change(s) (M on Windows, K in mu-compute).
   Still to do: add the deploy key printed above on GitHub, then run this install again (it clones then).
   Check everything:  powershell -NoProfile -ExecutionPolicy Bypass -File C:\Mu\host\mu-wsl2-host.ps1 status
   ```

   It may also print `Next: the relay credential, on stdin: mu-wsl2-distro tunnel credential`: ignore it, the pairing
   in step 9 does that. And it may print a warning that WSL was started "from this sign-in … rather than by the
   startup task": step 8 deals with it.

What this run set up, in short (the full list with the reasons is [§13](#13-why-each-setting-is-there), and
`install/hosts/wsl2/README.md` has every value):

- **Windows:** the distro `mu-compute` (Ubuntu 24.04, its disk in `C:\Mu\wsl\mu-compute`); `.wslconfig`
  (`instanceIdleTimeout=-1`, memory, processors, swap, no GUI); the startup task `\mu\mu-compute`; sleep and
  hibernate never on AC power, Fast Startup off, automatic sign-in after updates off; the service `mu-wsl-shutdown`;
  the folders `C:\Mu\bin`, `host`, `state`, `logs`, `wsl` with tight permissions; a copy of the scripts in
  `C:\Mu\host`.
- **Linux:** the user `mu` (no sudo); systemd; `/etc/wsl.conf`; faster write-back; the clean-stop marker; linger for
  `mu`; `mu`'s SSH key for GitHub; the job layer's sshd on `127.0.0.1:2222` behind a gate, the relay tunnel (not
  started yet: it has no credential), daily security updates; Windows interop off.

Nothing here opens a port on the network, and nothing is sent anywhere except downloads from Microsoft, Ubuntu and
GitHub.

## 6. The deploy key: the one manual GitHub step

The Linux user `mu` reads the μ repo with its own SSH key, registered on the repo as a **read-only deploy key**. The
first run printed it:

```
  The key of mu has to be added on GitHub as a read-only deploy key (once, by a repo admin):
    https://github.com/martand-systems/mu/settings/keys/new
    Title: mu-compute@<PC name>    Key: the line below    leave "Allow write access" unticked

    ssh-ed25519 AAAA… mu-compute@<PC name>

  Or, from a machine with the GitHub CLI and admin rights on the repo, with the line saved as key.pub:
    gh repo deploy-key add key.pub --repo martand-systems/mu --title "mu-compute@<PC name>"
  The organisation must allow deploy keys (organisation settings > Member privileges).
  Then run the install again: it checks the key and clones.
```

1. Copy the `ssh-ed25519 …` line: select it with the mouse and press Ctrl+C (in Terminal), or print it again
   (**Terminal (Admin)**):

   ```powershell
   wsl -d mu-compute -u root -- cat /home/mu/.ssh/mu_github.pub
   ```

   The key is public; it is safe to send by chat or e-mail to the repo admin.
2. A repo admin opens `https://github.com/martand-systems/mu/settings/keys/new`, enters the title it suggested, pastes
   the key, leaves **Allow write access** unticked, and clicks **Add key**. (Deploy keys are already allowed for the
   organisation: that was turned on for the office PC.)

## 7. Second run: clone and install μ (about 1.5 h)

*(Not yet run from scratch. On the office PC, μ was installed by hand from the clone in S3; `-InstallMu` runs the same
two commands.)*

1. **Where:** Terminal (Admin), in the same folder as step 5. Run the same line again:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\mu-wsl2-host.ps1 install -InstallMu -Branch v2026.11.8
   ```

2. Under `== GitHub access` it now says `ok       GitHub: mu can read git@github.com:martand-systems/mu.git` and
   `changed  cloned … into /home/mu/mu`. If it prints the key again, the key is not on GitHub yet, or was added with
   a typo: check step 6.
3. Then the μ install runs, in two parts:
   - `== μ system packages (install.sh --system, as root)`: about two dozen Ubuntu packages, then
     `system packages: ready` and `user services: mu's run without a login (systemd linger, for μ auto-update)`.
   - `== μ (./install.sh as mu; the first install downloads several GB)`: the μ installer's progress view, one line
     per stage. On the office PC the first install took **96 minutes**, mostly waiting for downloads at about 11 MB/s.
     Leave the window open; the PC does not sleep (the first run turned sleep off on AC power). It ends with:

     ```
       μ v2026.11.8 is ready in N s · … of new disk · log ~/.local/share/mu/logs/…
       auto-update: on, daily from HH:MM (mu autoupdate off to opt out)
     ```

     `mu doctor` and the benchmarks run inside the install: on the office PC 23/23 tools and 6/6 benchmarks for
     v2026.11.8.
4. **If the install ends with `auto-update could not be turned on`** (known issue 3), turn it on by hand
   (**Terminal (Admin)**):

   ```powershell
   wsl -d mu-compute -u mu --cd ~ -- /home/mu/.local/bin/mu autoupdate on
   ```

   Expect `auto-update: on. Daily from HH:MM μ checks … for a newer release, installs it, runs` and two more lines.
5. After the μ install, the job layer's last section, `== Job layer: the runner (as mu)`, starts the job runner and
   its memory-limit self-test: `changed  job runner mu-node.service enabled for mu (… memory limit self-test passed)`.
6. The run ends with `Done: N change(s) …` and the `Check everything:` line. No `Still to do` line this time.

If the install was interrupted (window closed, power cut), run the same line again: every step that is done prints
`ok`. The μ install itself is skipped once `/home/mu/.local/bin/mu` exists; to repeat only that part:
`wsl -d mu-compute -u mu -- bash -lc 'cd ~/mu && ./install.sh'`.

## 8. Hand WSL to the startup task, and check

**Why:** WSL keeps one session per Windows user, tied to whoever started it first. The install started it from your
sign-in, so signing out now would stop the Linux system (the startup task would start it again within 5 minutes).
`test-stop` stops WSL exactly as Windows does before a restart, and starts it again through the startup task, which
then owns it. It also applies the Linux settings that only take effect at a restart (interop off, the drive options).

1. **Where:** Terminal (Admin):

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File C:\Mu\host\mu-wsl2-host.ps1 test-stop
   ```

   It prints:

   ```
   mu WSL2 host: test-stop. This stops WSL as SYSTEM, the way the shutdown hook does at a restart.
     warning  every running WSL distro of every user stops now
     mu-wsl-shutdown.exe --stop-wsl as SYSTEM: exit code 0
       <the last lines of C:\Mu\logs\mu-wsl-shutdown.log>
     starting mu-compute again through the startup task

   Inside mu-compute (expect last-stop: a clean stop, and no NUL bytes in the heartbeat log):
   ```

   then the Linux checks. Look for `PASS  last-stop` (a clean stop). On the office PC this stop took 1.5 s.
   (A restart of Windows does the same hand-over.)

2. Check everything (a normal **PowerShell** is enough; `status` needs no administrator rights and changes nothing):

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File C:\Mu\host\mu-wsl2-host.ps1 status
   ```

   Each line is `PASS`, `FAIL`, `WARN` or `INFO` with a name and a detail. Before pairing, expect every line `PASS` or
   `INFO` except the ones about the relay and the Mac, which the pairing in step 9 sets up:
   - `FAIL linux.tunnel.credential  no relay credential yet …`, and possibly `FAIL linux.tunnel.egress` (the tunnel's
     filter is written with the credential);
   - `WARN linux.keys  no workstation is authorised yet …`;
   - `WARN linux.ready  ready for jobs: no (…)`, naming `keys` and the tunnel.

   The last line therefore says `mu WSL2 host status: N check(s) failed` at this point. (Read from the scripts; the
   office PC was never in this state, since it was paired in the same run.) Any other `FAIL` names what to do in its
   detail; [§12](#12-troubleshooting) has the common ones.

3. Optional, to see the "nobody signed in" behaviour yourself: restart Windows and leave it at the sign-in screen for
   ten minutes, then sign in and run `status`. `INFO sign-in` says how many minutes after startup you signed in,
   `INFO vm.started` how many seconds after startup the distro came up (42 s on the office PC), and
   `PASS distro.running`.

## 9. Pair the node with the Mac (one code)

Pairing gives the node its relay credential and authorises the Mac's key, and gives the Mac the node's host key. One
code, made on the Mac and typed at the PC, authenticates all of it; the relay never sees the code. The design is
`docs/design/pairing.md`; the owner's full checklist, with every failure case, is
`docs/design/first-job-checklist.md` §3. This ran on the office PC on 2026-11-14 with v2026.11.8.

**Before:** the relay's pairing service is on and the Mac is logged in to it (`mu relay status` on the Mac says
`pairing on`; [relay.md](relay.md) step 3). The Mac runs the same release as the PC (`mu --version`).

1. **Mac Terminal** (the owner's Mac). Pick the node's name: lower-case letters, digits and `-`, for example
   `office-pc` or `lab-pc`. Then:

   ```sh
   mu compute invite office-pc
   ```

   It prints the invite (valid 30 minutes; make it when you are at the PC), the two lines to type at the PC, and the
   code:

   ```
   invite 7K3M for compute node office-pc (relay port 7001), from avi-mac, valid until 14:52 (30 min)

   At the office PC, open Terminal (Admin) and type these two lines:

     wsl -d mu-compute -u mu --cd ~ -- mu update
     powershell -NoProfile -ExecutionPolicy Bypass -File (wsl -d mu-compute -u mu --cd ~ -- mu node host-script) pair

   When it asks for the pairing code, type:

     7K3M-Q9TD-4WXR-HN2C-PB8F-ZJ6R
   ```

   **Do not type those two lines as printed** (known issue 1): type the ones in steps 2 and 3 below. They do the
   same with the full path to `mu`. The Mac keeps waiting and stays awake.

   Through Claude Code the command is `mu compute invite office-pc --no-wait --json`; the code then appears in a
   dialog on the Mac's screen, never in Claude's output (`claude/CLAUDE-toolchain.md`, "Pairing a node").

2. **At the PC, Terminal (Admin).** First make sure the PC runs the latest release:

   ```powershell
   wsl -d mu-compute -u mu --cd ~ -- /home/mu/.local/bin/mu update
   ```

   It prints `checking … for the latest release…`, then either `μ v2026.11.N is the latest release. Nothing to do.`
   or an install of it (on 2026-11-14: 20 s, every layer reused, doctor 23/23, benchmarks 6/6) ending
   `default is now μ v2026.11.N.`

3. **Same window.** The pairing line (this exact line worked on the office PC on 2026-11-14):

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File (wsl -d mu-compute -u mu --cd ~ -- /home/mu/.local/bin/mu node host-script) pair
   ```

   The part in brackets asks μ inside Linux for the Windows path of the release's host script
   (`\\wsl.localhost\mu-compute\home\mu\.local\share\mu\toolchains\v2026.11.N\recipe\install\hosts\wsl2\mu-wsl2-host.ps1`);
   PowerShell then runs that script with `pair`. In order, it prints:

   1. `mu WSL2 host: pair (the scripts copied to C:\Mu\stage\v2026.11.N)`: it runs from a copy only administrators
      can change.
   2. The first time only, `== What a process of the distro can reach of Windows now (before the change; saved in
      C:\Mu\logs\windows-reach-before.txt)`.
   3. `== Setting up the node (install -NoRestart)`: the same install as before, `ok` for what is set up.
   4. `Pairing code (from the Mac): `: type the code. The screen shows asterisks; dashes, spaces and upper/lower case
      do not matter. Then:
      `code accepted: this PC becomes compute node office-pc on relay port 7001 for avi-mac (key SHA256:…)`.
      A typo is caught before anything is sent and asked again (three tries).
   5. If install changed something only a restart applies: `== Restarting the distro cleanly to apply the settings
      (about a minute)`.
   6. `== Pairing`, then lines like `relay credential written: …`, `avi-mac's key authorised: SHA256:…`,
      `mu-tunnel.service: connected`, `waiting for avi-mac to confirm ...`,
      `confirmed: avi-mac reached this PC through the relay`, `paired with avi-mac`.
   7. `== Status`: the full status, then one line: `paired with avi-mac | ready for jobs: yes`.

4. **On the Mac** meanwhile, the invite finishes. On 2026-11-14 it printed:

   ```
   office-pc answers through the relay: μ v2026.11.8, runner active, memory limit enforced (5.6 s)
   paired office-pc
   ```

If the pair line stops with an error, the message says what to do. The common cases (from the checklist):

| What you see | Then |
|---|---|
| `pair needs an administrator: open Terminal (Admin) and run the same line there` | open Terminal (Admin) |
| `the argument '' to -File does not exist` (or similar), and `mu: command not found` above it | you used the short line the Mac printed: use the line in step 3 |
| `mu node host-script: the default μ … has no host script with the pair action` | the PC's μ is older than v2026.11.8: run step 2 |
| `the code has a typo …` | type it again |
| `invite 7K3M expired …` (exit 9) | a new `mu compute invite office-pc` on the Mac, then the same PC line |
| `the relay does not answer (…): run the same line again in a minute` | run the same line again |
| The window was closed half way, or the PC restarted | run the same line again with the same code: it resumes |
| `SOMEONE ELSE USED THIS CODE FIRST …` or `the invite was changed on the way …` | stop, tell the owner; do not reuse that code |

To pair again later (a new Mac key, a reinstalled PC): on the Mac `mu compute invite office-pc --replace`, and at the
PC `powershell -NoProfile -ExecutionPolicy Bypass -File C:\Mu\host\mu-wsl2-host.ps1 pair`. Once jobs have run on the
node, `pair` refuses to run from the release folder and names this `C:\Mu\host` line.

## 10. Verify from the Mac, and run a first job

1. **At the PC, PowerShell:**

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File C:\Mu\host\mu-wsl2-host.ps1 status
   ```

   Every line `PASS` or `INFO`, including `PASS  linux.keys  avi-mac SHA256:…`, `PASS  linux.tunnel  connected: …`,
   `PASS  linux.node.limits  memory limit enforced: …`, and **`PASS  linux.ready  ready for jobs: yes`**. Last line:
   `mu WSL2 host status: all checks pass`.

2. **Mac Terminal**, from anywhere (the relay is on the internet):

   ```sh
   mu compute status office-pc
   ```

   Exit 0 and two lines. On 2026-11-14 (a second call within 5 minutes reuses the connection: 0.87 s):

   ```
   office-pc: up · 0.87 s (connection reused) · μ v2026.11.8 (installed: v2026.11.8) · runner active · queue 0 running, 0 queued
     Intel Core Ultra 7 265K · 20 cores, 22 GB RAM · disk 89 GB free (…; jobs need …) · memory limit enforced · last stop clean
   ```

   (The exact CPU string and disk detail are the node's; this line is reconstructed from the code and the measured
   values, not copied from the screen.)

3. **A first job**, from any μ project on the Mac (see [../using-compute-jobs.md](../using-compute-jobs.md)). The
   office PC's first job was the `fea-cantilever` benchmark, set up as in `docs/design/first-job-checklist.md` §2.4
   and §4.2:

   ```sh
   mu submit --on office-pc --name fea-cantilever --time 1h -- python -m mu.bench fea-cantilever --workdir bench
   mu status <job-id> --wait
   mu fetch <job-id>
   ```

   On 2026-11-14 (job `20261114-101926-9yb3u4`) it succeeded in 4.5 s, and its tip deflections matched the Mac's to
   0.005 % and 0.001 %. The full record is `docs/verification/2026-11-14-first-remote-job.md`.

The node is ready for jobs.

## 11. Day 2 operations

### 11.1 Auto-update

μ updates itself in the distro: once a day, from an early-morning time picked at random, the user `mu`'s systemd
timer checks GitHub for a newer release, installs it, runs `mu doctor` and the benchmarks, and makes it the default
only if they pass. Projects keep their pins; jobs hold it off while they run. The details are in the top-level
`README.md` ("Updates"). To look (**Terminal (Admin)** or PowerShell):

```powershell
wsl -d mu-compute -u mu --cd ~ -- /home/mu/.local/bin/mu autoupdate
```

To update now: `wsl -d mu-compute -u mu --cd ~ -- /home/mu/.local/bin/mu update`.

The host scripts in `C:\Mu\host` (Windows) and `/usr/local/sbin/mu-wsl2-distro` (Linux, root) are **not** updated
by auto-update. How they should take a newer release's copy once jobs have run on the node is still open
(`docs/design/pairing.md` §14 Q6): the release folder is writable by `mu`, so an administrator should not run
scripts from it after jobs have run. Until that is settled, ask the lead before running a newer release's `install`.

### 11.2 A release that needs a new Ubuntu package (known issue 2)

Auto-update cannot install Ubuntu packages (`mu` has no sudo). It waits and reports it: `mu autoupdate` shows
`μ v… needs a one-time admin step: … install.sh --system …`. A manual `mu update` instead stops at a sudo password
prompt that has no right answer (known issue 2): press Ctrl+C. Then, in **Terminal (Admin)**, bring `mu`'s clone to
the new release (as `mu`, who owns it) and run the system step from it as root (put the new release's name in):

```powershell
wsl -d mu-compute -u mu --cd ~ -- git -C /home/mu/mu fetch --tags --quiet
wsl -d mu-compute -u mu --cd ~ -- git -C /home/mu/mu checkout --quiet v2026.11.N
wsl -d mu-compute -u root -- /home/mu/mu/install.sh --system --user mu
```

The last line lists the packages it installs and ends with `system packages: ready`. Then run the update again:
`wsl -d mu-compute -u mu --cd ~ -- /home/mu/.local/bin/mu update`. *(The office PC hit this with v2026.11.6, which
added `ostree`; it was installed as root by hand. These three lines have not been run.)*

### 11.3 Checking the node

- From the Mac: `mu compute status office-pc` (exit 0 = up).
- At the PC: `C:\Mu\host\mu-wsl2-host.ps1 status` as above. `-Json` gives one JSON object.
- Inside Linux only: `wsl -d mu-compute -u root -- /usr/local/sbin/mu-wsl2-distro status`.
- Logs: `C:\Mu\logs\` (each install, the shutdown hook's `mu-wsl-shutdown.log`); μ's install and auto-update logs
  in `/home/mu/.local/share/mu/logs/` inside Linux.

### 11.4 Signing in and out

You can sign in to the PC, use it, and sign out: the node keeps running (measured in S3: the heartbeat ran on across
a sign-out). One exception: after a WSL update, or if someone runs `wsl` in their sign-in before the startup task has
run, WSL belongs to that sign-in, and signing out stops the distro. The startup task starts it again within 5 minutes.
To hand it over at once: `test-stop` (step 8.1).

Running jobs use the PC's processors and memory; a job's memory limit keeps the Linux system within its share
(`.wslconfig`: 75% of the RAM by default).

### 11.5 Restarts

Windows restarts (by you or by Windows Update) are expected. Before Windows goes down, the `mu-wsl-shutdown` service
stops WSL cleanly; after the restart the startup task starts the distro within about a minute (42 s measured), with
nobody signed in; the tunnel and the job runner start by themselves, and a job interrupted by the restart is run
again once. **Still to prove on the office PC:** the restart tests of `install/hosts/wsl2/README.md` ("Restart
tests", steps 3–5: a baseline restart with the service stopped, then restarts with it) and the first real Windows
Update restart. The procedure is there and in `docs/design/first-job-checklist.md` §5; they need no preparation
beyond the heartbeat (`install -Heartbeat on`).

A power cut gives no warning: data written in the last seconds may be lost, and the job is re-run.

### 11.6 Revoking a Mac, removing the node

- **Remove the node from a Mac:** `mu compute remove office-pc` (Mac Terminal). It forgets the node there and, if
  the Mac holds the relay's admin token, deletes the node's relay client (the node can no longer reach the relay;
  `--keep-relay-client` leaves it). It prints the line that revokes the Mac's key on the PC.
- **Revoke a Mac's key on the node** (Terminal (Admin)); the name is the one `status` lists under `linux.keys`:

  ```powershell
  wsl -d mu-compute -u root -- /usr/local/sbin/mu-wsl2-distro revoke avi-mac
  ```

  It also ends that Mac's open connections. `mu-wsl2-distro keys` lists who is authorised.
- **Revoke at the relay** (a lost Mac): [relay.md](relay.md) §5.

### 11.7 Uninstall

**Terminal (Admin):**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\Mu\host\mu-wsl2-host.ps1 uninstall
```

It removes the startup task, the shutdown service, the job layer inside Linux, and restores every Windows setting it
changed to its value before the first install (recorded in `C:\Mu\state\wsl2-host.json`). It keeps the distro, the
user `mu`, μ, the node's host key, `/srv/mu` (the job folders) and `C:\Mu\logs`. Then run `wsl --shutdown` for
`.wslconfig` to apply. To delete the distro and everything in it as well, add `-RemoveDistro` (it asks you to type
`mu-compute`). Settings that were already right before the first install (on the office PC, the S3 hand-made ones)
are left as they are. The full list is in `install/hosts/wsl2/README.md` ("Undo").

Also remove the deploy key on GitHub (`…/mu/settings/keys`) and, from the Mac, the node (`mu compute remove`).

## 12. Troubleshooting

| What you see | Where | What to do |
|---|---|---|
| `mu: command not found` | any `wsl … -- mu …` line | known issue 1: write `/home/mu/.local/bin/mu` instead of `mu` |
| `… is not recognized as the name of a cmdlet …` | PowerShell | a Linux command in PowerShell: use the `wsl -d mu-compute -u … -- …` form |
| `wsl: command not found`, `powershell: command not found` | a `root@…#` or `mu@…$` prompt | you are in Linux: type `exit` |
| `wsl --install --no-distribution: exit code N. Restart Windows, then run this script again.` | step 5 | restart Windows, run the same line |
| a WSL error about virtualisation, or `0x80370102` | step 3 or 5 | virtualisation is off in the BIOS (step 1.1) |
| `… is missing next to this script …: copy the whole install\hosts\wsl2 folder` | step 5 | the three files are not together (step 4) |
| `C:\Mu belongs to …: another user created it …` | step 5 | someone else made `C:\Mu`. Check it, delete it, run again |
| the deploy key is printed again on the second run | step 7 | the key is not on GitHub, or not this key: step 6 |
| `GitHub: could not check access (…)` | step 5 or 7 | no network to GitHub from Linux: check the PC's network, run again |
| a sudo password prompt during an install or update | step 7, 11 | known issue 2: Ctrl+C, then §11.2 |
| `auto-update could not be turned on` | end of step 7 | known issue 3: step 7.4 |
| `WARN distro.running unknown: the startup task has not run successfully since WSL started …` | `status` | wait 5 minutes, or `Start-ScheduledTask -TaskPath \mu\ -TaskName mu-compute` (Terminal (Admin)), then `status` again |
| `FAIL distro.running … is not running` | `status` | the same; if it stays, `FAIL task.last-run` names the task's error |
| `FAIL vm.memory` / `FAIL vm.processors` | `status` | `.wslconfig` was changed but WSL not restarted: `test-stop` |
| `FAIL linux.node.limits memory limit not proven …` | `status` | `wsl -d mu-compute -u mu --cd ~ -- /home/mu/.local/bin/mu node selftest`. Jobs are refused until it passes. On a WSL older than 2.9.5 the fix is `kernelCommandLine = cgroup_no_v1=all` in `.wslconfig`, then `test-stop` (`docs/design/first-job-checklist.md` A.2) |
| `FAIL linux.tunnel …` after pairing | `status` | `wsl -d mu-compute -u root -- /usr/local/sbin/mu-wsl2-distro tunnel status` gives the reason; usually the network or the relay. It reconnects by itself |
| `FAIL linux.node.disk … GB free …` | `status` | free space on `C:`; old job folders are in `/srv/mu/jobs` |
| from the Mac, `mu compute status` exit 4 | Mac | the PC is off, asleep, or the distro is not running: `status` at the PC |

## 13. Why each setting is there

The evidence is `docs/verification/2026-11-13-S3-wsl.md` (measured on the office PC) unless another source is named.

| Setting | Why |
|---|---|
| **WSL 2.5.4 or later** | needed for `instanceIdleTimeout` |
| **`.wslconfig` `[general] instanceIdleTimeout=-1`** | WSL stops an idle distro when no `wsl.exe` is attached. A running systemd service alone does **not** keep it alive; this setting does (S3) |
| **`.wslconfig` `memory`, `processors`, `swap`, `guiApplications=false`** | WSL's default is 50% of the RAM; the node gets 75% (23 GB of 31 GB on the office PC, 22 GB visible to jobs) and every processor. Headless: no WSLg |
| **The startup task `\mu\mu-compute`**, as you, with **S4U** ("run whether the user is signed in or not", no stored password), at startup + 30 s | starts the distro at boot **with nobody signed in**. Measured: after a restart left at the sign-in screen, the distro ran 2 h 01 min with nobody signed in, its first heartbeat 42 s after boot. The SYSTEM account cannot be used: WSL refuses it (`WSL_E_LOCAL_SYSTEM_NOT_SUPPORTED`) |
| **…repeating every 5 minutes** (a watchdog) | a WSL update stops the WSL service and the distro with it; and if WSL was started from someone's sign-in, signing out stops it. The next run starts it again. (A WSL update has not happened on the node yet) |
| **Sleep and hibernate never on AC power** | a sleeping PC pauses the Linux VM, and jobs with it |
| **Fast Startup off** (`HiberbootEnabled=0`) | with Fast Startup, "Shut down" hibernates the Windows kernel, and startup tasks are reported not to run after a power-on (reported, not measured) |
| **Automatic sign-in after updates off** (ARSO, `DisableAutomaticRestartSignOn=1`) | WSL ties a user's session to the Windows session that started it first. After a restart, Windows signed the user in by itself (13 s after boot, S3 reboot 1); WSL could then belong to that sign-in and stop when it signs out. It also made the first "nobody signed in" test meaningless |
| **The `mu-wsl-shutdown` service** (LocalSystem; first in `PreshutdownOrder`; 90 s preshutdown timeout) | **The finding of S3: a Windows restart stops Linux hard.** The heartbeat line written 30 s before the restart came back as 21 zero bytes: the file's size reached the disk, its data did not. WSL's own service never asks to be told about a shutdown. This service takes Windows' preshutdown notice (sent on every orderly shutdown or restart, whoever is signed in, and Windows waits for it), stops `WSLService`, and so runs WSL's clean path: systemd powers off, the disk is synced and unmounted. `test-stop` runs the same stop: clean, in 1.5 s, on the office PC. A real restart with it has not been measured yet (§11.5). Built on the PC from the C# in the script with Windows' own .NET Framework compiler |
| **Folder permissions under `C:\Mu`** | `C:\` lets every user create folders, and the creator of a folder owns it. The shutdown service runs `C:\Mu\bin\mu-wsl-shutdown.exe` as SYSTEM, so `C:\Mu` and its `bin`, `host`, `state`, `logs` are writable only by Administrators and SYSTEM. `C:\Mu\wsl` holds the distro's disk, with the deploy key and every job's files: only you, Administrators and SYSTEM can open it |
| **Inside Linux: user `mu` without sudo** | jobs run as `mu`; admin work goes through `wsl -d mu-compute -u root`. A job cannot change the system |
| **systemd, `/etc/wsl.conf`** (`[boot] systemd=true`, `[user] default=mu`, `appendWindowsPath=false`) | μ's auto-update timer, the tunnel, sshd and the job runner are systemd services |
| **Faster write-back** (`vm.dirty_expire_centisecs=500`, `vm.dirty_writeback_centisecs=100`) and stop timeouts of 8 s | after a hard stop at most about 6 s of unsynced data is lost instead of about 35 s; WSL gives systemd 10 s to power off |
| **Clean-stop marker** (`mu-clean-stop.service`) | records whether each stop was clean (`status`: `linux.last-stop`; the node reports `last stop clean` to the Mac). This is how a restart nobody watched is judged |
| **Linger for `mu`** | `mu`'s user services (auto-update, the job runner) run with nobody logged in |
| **`mu`'s SSH key and a read-only deploy key** | `mu` clones and updates μ from the private repo without anyone's credentials; it can read, not write |
| **sshd on `127.0.0.1:2222` behind a gate** (`mu-node-gate`), **the tunnel** (`mu-tunnel.service`, chisel, with an egress filter) | the Mac reaches the node only through the relay, over SSH with the node's host key pinned at pairing; nothing listens on the network; the tunnel may connect only to the relay (`docs/design/job-layer.md` §13) |
| **Interop off** (`[interop] enabled=false`), Windows drives for root only | a job cannot start Windows programs or read your Windows files (job-layer §13.4) |
| **Unattended security updates** | the Linux system gets Ubuntu's security fixes daily, without a reboot |
