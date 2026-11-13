# WSL2 host adapter: a Windows 11 PC as a μ compute node

Two scripts turn a Windows 11 PC into a μ compute node (ADR-001 in `docs/PLAN.md`):

- **`mu-wsl2-host.ps1`** (Windows, run by a person once) installs a WSL2 distro, makes it run with nobody signed in,
  makes Windows stop it cleanly before a shutdown or restart, and then runs the Linux script inside it.
- **`mu-wsl2-distro.sh`** (Linux, run as root inside the distro) sets up the distro: systemd, the unprivileged user `mu`,
  write-back timers, a clean-stop marker, and access to the μ repo through a read-only deploy key.

Both scripts can be rerun at any time. A second `install` changes nothing and says so, and `status` checks everything
without changing anything. They reproduce the setup tested in spike S3
(`docs/verification/2026-11-13-S3-wsl.md`), plus the clean-shutdown measures that came out of it.

## Run it

On the PC, as the Windows user who will own the distro. That user must be an administrator; the script asks for
elevation itself:

    powershell -NoProfile -ExecutionPolicy Bypass -File .\mu-wsl2-host.ps1 install

- Keep the two scripts together. A copy of the repo folder, a downloaded zip, or the folder seen through
  `\\wsl.localhost\<distro>\...` all work. `-ExecutionPolicy Bypass` is needed because downloaded and network-path
  scripts are blocked by default. It applies to this one run only.
- Run from a `\\wsl.localhost` path, the script copies itself to `%TEMP%` before elevating, because the elevated process
  may not see that path.
- The first run downloads Ubuntu 24.04 (a few hundred MB). It prints the distro's new SSH public key with instructions
  (see [GitHub access](#github-access-the-one-manual-step)). Add the key, then run `install` again: the second run
  clones the repo.
- Add `-InstallMu` to also install μ in the distro: `install.sh --system --user mu` as root, then `./install.sh` as
  `mu`. The first install takes 1–2 h, mostly downloads.
- Add `-Heartbeat on` for the reboot tests below.
- Each elevated run is logged to `C:\Mu\logs\wsl2-host-<action>-<time>.log`.

Afterwards, run it from the copy that install puts in `C:\Mu\host`:

    powershell -NoProfile -ExecutionPolicy Bypass -File C:\Mu\host\mu-wsl2-host.ps1 status

| Action | What it does |
|---|---|
| `install` (default) | Sets everything up. Rerunning it changes only what differs |
| `status` | Prints every check as PASS / FAIL / WARN / INFO and changes nothing. Exits 1 if any check fails. `-Json` prints one JSON object; `-Offline` skips the GitHub check. Needs no elevation |
| `uninstall` | Removes the task and the hook, and restores the settings it changed. Keeps the distro unless `-RemoveDistro` is given and you type the distro's name (`-Force` skips that prompt) |
| `test-stop` | Stops WSL as SYSTEM, exactly as the hook does at a restart, then starts the distro again and shows its status. This is the clean-stop test without restarting Windows. It stops **every** running WSL distro |

| Parameter | Default | |
|---|---|---|
| `-Distro` | `mu-compute` | the distro's name |
| `-Location` | `C:\Mu\wsl\<Distro>` | where its virtual disk goes (NVMe drive) |
| `-Image` | `Ubuntu-24.04` | what `wsl --install` installs |
| `-User` | `mu` | the Linux user μ runs as, without sudo |
| `-MemoryGB` | 75% of the PC's memory | `.wslconfig` `memory` (31 GB PC → 23 GB) |
| `-Processors` | all logical processors | `.wslconfig` `processors` |
| `-SwapGB` | 8 | `.wslconfig` `swap` |
| `-Repo`, `-Branch` | `git@github.com:martand-systems/mu.git`, the default branch | what is cloned to `/home/mu/mu` |
| `-NoClone`, `-InstallMu`, `-Heartbeat on\|off` | | see above |
| `-NoPowerChanges` | | leaves sleep, hibernate, Fast Startup and automatic sign-in as they are |
| `-NoShutdownHook` | | does not install the hook. A restart then stops Linux hard |
| `-NoRestart` | | does not restart WSL to apply changes. They apply at the next restart |
| `-MuRoot` | `C:\Mu` | μ's folder on Windows |

`install` records the memory, processors and switches it used. Later `status` and `install` runs reuse them unless you
pass new ones.

## What it changes on Windows, and why

| Setting | Value | Why |
|---|---|---|
| WSL | installed if missing (then restart Windows and rerun); updated if older than 2.5.4 | `instanceIdleTimeout` needs WSL 2.5.4 or later |
| Distro | `wsl --install Ubuntu-24.04 --name mu-compute --location C:\Mu\wsl\mu-compute --no-launch` | No first-run prompt: the user is created as root inside, and Ubuntu's prompt is marked done (`RunOOBE=0` in the distro's registration) |
| `%UserProfile%\.wslconfig` `[general] instanceIdleTimeout=-1` | merged, other keys kept | This is what keeps the distro running with no `wsl.exe` attached. A running systemd service alone does not keep it alive |
| `.wslconfig` `[wsl2] memory`, `processors`, `swap`, `guiApplications=false` | 75% of RAM, all CPUs, 8 GB, no WSLg | WSL's defaults are 50% of RAM. The node is headless |
| Scheduled task `\mu\mu-compute` | `"C:\Program Files\WSL\wsl.exe" -d mu-compute --exec /bin/true`. At startup +30 s, then every 5 min forever. Runs as you with **S4U** ("run whether signed in or not", no stored password), highest privileges, 5-minute limit, a new run is skipped while one is running | Starts the distro at boot with nobody signed in. The 5-minute repeat is a watchdog: a WSL update stops the WSL service, and with it the distro. SYSTEM can't be used: WSL refuses it (`WSL_E_LOCAL_SYSTEM_NOT_SUPPORTED`) |
| Sleep / hibernate on AC power | never (`powercfg` on the active plan) | A sleeping PC pauses the VM |
| Fast Startup | off (`HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power` `HiberbootEnabled=0`) | With Fast Startup, "Shut down" hibernates the kernel, and startup tasks are reported not to run after a power-on |
| Automatic sign-in after updates (ARSO) | off (`HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System` `DisableAutomaticRestartSignOn=1`) | WSL ties a user's session to the Windows session that started it first. An automatic sign-in could start it from an interactive session that is later signed out, which would end the distro. It also made the first S3 reboot test inconclusive |
| Service `mu-wsl-shutdown` (the shutdown hook) | LocalSystem, automatic, restarts after a failure; `C:\Mu\bin\mu-wsl-shutdown.exe`, compiled here from the C# inside the script with Windows' own .NET Framework compiler | See below |
| `HKLM\SYSTEM\CurrentControlSet\Control\PreshutdownOrder` | `mu-wsl-shutdown` first | Services listed there get the shutdown notice before the others |
| `C:\Mu\bin`, `host`, `state`, `logs` | writable only by Administrators and SYSTEM | `C:\` lets every user modify new folders, and a SYSTEM service runs the exe in `C:\Mu\bin` |

### The shutdown hook

At a Windows restart, Linux was not shut down. In S3, the heartbeat line written just before the restart came back as
21 NUL bytes: the file's new size reached the disk, but its data did not. The reason is that WSL's service
(`WSLService`) never asks to be told about a shutdown, and its VM is powered off hard when the service process dies at
the end of shutdown.

Stopping `WSLService` runs WSL's own clean path for every distro: `systemctl poweroff`, then sync, unmount and VM
power-off. The hook is a small service that registers for the *preshutdown* notice. Windows sends that notice on every
orderly shutdown or restart, whoever is signed in, and waits for the service to finish. Its handler only wakes a
worker and returns. The worker stops `WSLService`, waits up to 80 s, logs to `C:\Mu\logs\mu-wsl-shutdown.log`, then
reports that it is done.

At every start the service sets its own preshutdown timeout to 90 s (Windows' default is 10 s) and puts itself first in
`PreshutdownOrder`. Stopping the service by hand, uninstalling it or reinstalling it never touches WSL.

WSL gives systemd 10 s (`[boot] initTimeout`) to power off. Stock Ubuntu can run over that (WSL issue #42237), which is
why the distro gets `DefaultTimeoutStopSec=8s`. Even when it runs over, WSL still syncs the disk before it powers the
VM off, so no cached data is lost.

## What it changes inside the distro (`mu-wsl2-distro.sh`, as root)

| | |
|---|---|
| `/etc/wsl.conf` | `[boot] systemd=true`, `[user] default=mu`, `[interop] appendWindowsPath=false`, merged. The original is saved in `/var/lib/mu-wsl2/`, and uninstall puts it back |
| user `mu` | a normal user, not in `sudo`. Admin work: `wsl -d mu-compute -u root` |
| `/etc/sysctl.d/60-mu-writeback.conf` | `vm.dirty_expire_centisecs=500`, `vm.dirty_writeback_centisecs=100`. Data left in the page cache by a hard stop is now at most about 6 s old instead of about 35 s. This narrows the window but does not close it; only `fsync` does. It applies to the whole WSL VM |
| `/etc/systemd/journald.conf.d/60-mu.conf` | `SyncIntervalSec=30s` |
| `/etc/systemd/system.conf.d/60-mu-stop.conf` | `DefaultTimeoutStopSec=8s`. WSL gives systemd 10 s (`initTimeout`, left at its default) to power off |
| `mu-clean-stop.service` | When systemd stops, it writes and fsyncs `/var/lib/mu-wsl2/clean-stop`. At the next start it appends `previous-stop=clean`, `unclean` or `first` to `/var/lib/mu-wsl2/stops.log`. This is how a restart nobody watched is judged |
| linger for `mu` | `mu`'s systemd user services, such as μ's auto-update timer, run without a login |
| `~mu/.ssh/mu_github` | ed25519 key. `~/.ssh/config` maps `github.com` to it. `known_hosts` pins GitHub's ed25519 key from `api.github.com/meta`, cross-checked with `ssh-keyscan` |
| `/home/mu/mu` | the clone, once the key works. An existing clone is never touched again: μ updates itself |
| `/usr/local/sbin/mu-wsl2-distro` | the script itself, so you can run `wsl -d mu-compute -u root -- mu-wsl2-distro status` |

μ's own units should set `TimeoutStopSec=5`, and the relay agent (S4) should write its state atomically (temp file,
fsync, rename, fsync the directory) and re-run any job whose result was not durably written. The clean-stop marker
above is what the agent reads to report an unclean stop.

## GitHub access: the one manual step

`install` prints the public key of `mu`. A repo admin adds it once as a **read-only deploy key**:
https://github.com/martand-systems/mu/settings/keys/new. Use the title it suggests (`mu-compute@<PC name>`), paste the
key, and leave "Allow write access" unticked. With the GitHub CLI, from any machine where you are a repo admin:

    gh repo deploy-key add key.pub --repo martand-systems/mu --title "mu-compute@MSI-PROZ890-PC"

The organisation must allow deploy keys (organisation settings → Member privileges). Then run `install` again: it
checks the key and clones. `status` reports `linux.github` as PASS once `mu` can read the repo.

## Check

`status` lists every setting above, plus:

- `task.last-run`: the result of the last run of the watchdog task;
- `hook.last-shutdown`: what the hook logged at the last shutdown before this boot;
- `hook.event-7043`: whether Windows ever gave up waiting for the hook;
- `vm.processors` and `vm.memory`: whether the VM really got the `.wslconfig` values;
- `vm.started`: how long after Windows started the distro came up;
- `sign-in`: when someone first signed in after startup;
- `linux.last-stop`: whether the distro's previous stop was clean;
- `linux.heartbeat.*`: when the heartbeat is on, NUL bytes (FAIL: data lost) and gaps longer than 90 s (WARN: one per
  restart is expected) in `/home/mu/heartbeat.log`.

Inside the distro: `wsl -d mu-compute -u root -- mu-wsl2-distro status` (`--json`, `--offline`, `--gap SECONDS`).

## Undo

    powershell -NoProfile -ExecutionPolicy Bypass -File C:\Mu\host\mu-wsl2-host.ps1 uninstall

This removes the task, the service and its `PreshutdownOrder` entry, and inside the distro the drop-ins, the units and
`/usr/local/sbin/mu-wsl2-distro`. It puts back `/etc/wsl.conf`. It restores every Windows setting it changed to the value
recorded in `C:\Mu\state\wsl2-host.json` before its first change: power, Fast Startup, ARSO, and each `.wslconfig` key
(keys that were absent are removed). Run `wsl --shutdown` afterwards for `.wslconfig` to apply.

It keeps the distro with the user `mu`, its key, the clone and μ; `C:\Mu\logs`; and linger for `mu`. Add `-RemoveDistro`
to delete the distro: it asks you to type the distro's name.

On the office PC the S3 settings were made by hand before this script existed. The script recorded those hand-made
values as the "previous" ones, so `uninstall` leaves them as they are. To change them by hand:
`powercfg /change standby-timeout-ac <minutes>`, `powercfg /change hibernate-timeout-ac <minutes>`,
`HiberbootEnabled=1` (Fast Startup on), and delete `DisableAutomaticRestartSignOn` (ARSO back to its default).

## Restart tests (how the clean-stop measures are judged)

Turn the heartbeat on first: `install -Heartbeat on`. It appends the UTC time to `/home/mu/heartbeat.log` every 60 s,
without fsync, like most tools do. Each test ends with `status`. Three checks decide it:

- `linux.heartbeat.nul`: no NUL bytes;
- `linux.last-stop`: a clean stop;
- `hook.last-shutdown`: the hook stopped WSL. Also check `hook.event-7043`.

1. **Look first** (elevated):

       sc.exe qc WSLService; sc.exe query WSLService; sc.exe sdshow WSLService; sc.exe query vmcompute
       reg query HKLM\SYSTEM\CurrentControlSet\Control /v PreshutdownOrder
       (Get-CimInstance Win32_OperatingSystem).Caption

2. **Stop WSL as SYSTEM, without a restart:** `mu-wsl2-host.ps1 test-stop`. Expect `last-stop: clean`, no NUL bytes,
   and in the distro no ext4 journal recovery at the new start: `wsl -d mu-compute -u root -- dmesg | grep -i 'EXT4.*recover'`
   prints nothing.
3. **Baseline, without the hook.** `Stop-Service mu-wsl-shutdown` (it stays stopped until the next boot), then
   `shutdown /r /t 0`. After the restart, `status` should show `last-stop: UNCLEAN` and `hook.last-shutdown: WARN` (no
   preshutdown logged), and possibly NUL bytes. This shows the detector works.
4. **With the hook** (it starts again by itself after the restart). Do both:
   - sign out and restart from the sign-in screen's power button;
   - `shutdown /r /t 0 /d p:2:17` from an elevated prompt: a planned restart with an operating-system reason, like
     the ones updates log.

   After each, `status` should show `hook.last-shutdown: PASS` ("WSL stopped cleanly"), `last-stop: clean`, no NUL
   bytes, and no event 7043.
5. **The first real Windows Update restart:** the same three checks. It is likely, but not yet proven, that update
   restarts go through preshutdown like any other restart.
6. **Write-back timers:** `wsl -d mu-compute -- sysctl vm.dirty_expire_centisecs` prints 500.

## Known limits

- **Not yet run on the PC:** both scripts, the hook's compile, `test-stop`, and steps 2–5 above. The pure functions
  are tested on macOS by `tools/test-wsl2-host/run.sh`. That covers the `wsl.conf`/`.wslconfig` merges, heartbeat
  analysis, the task definition and its comparison, quoting, the PowerShell 5.1 syntax check, and a C# 5 compile of
  the hook.
- **Who started WSL decides when it ends.** WSL keeps one session per Windows user, tied to the Windows session that
  created it. If you sign in and run `wsl` before the startup task has run, signing out later stops the distro. The
  watchdog starts it again within 5 minutes. `install` starts the distro through the task for this reason, and
  `status` never starts it.
- **A watchdog run during shutdown:** if the 5-minute run falls in the seconds between the hook's stop and the end of
  shutdown, it can start WSL again, and that brief instance is then stopped hard. This is accepted, and the watchdog is
  never disabled.
- **Power cuts and crashes** get no shutdown notice at all. Only fsync in μ's own code protects data then; the
  write-back timers narrow the window.
- **Not used:** `wsl --shutdown --force` (a hard kill), a task on event 1074 (nothing makes Windows wait for it), and a
  Group Policy shutdown script (the fallback if the service ever fails: run `sc.exe stop WSLService` and wait for it).
