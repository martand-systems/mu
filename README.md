# μ (mu)

Martand's engineering toolchain for **designing hardware with Claude Code**: code-driven CAD (build123d, CadQuery),
meshing (Gmsh), FEA (CalculiX), CFD (OpenFOAM), aero (OpenVSP/VSPAERO), post-processing (ParaView, PyVista), 3D printing
(PrusaSlicer) and CNC (FreeCAD CAM).

μ is an installed application. Projects live in their own git repos (for example `martand-systems/jatayu`) and pin a μ version
in `mu.toml`.

- **Roles:** `workstation` (Claude Code, projects, viewers), `compute` (solvers), `full` (both, works offline)
- **Compute:** Linux. On Windows it runs in WSL2 through a host adapter (ADR-001). macOS has a native local build
- **Remote compute:** through the relay at `relay.martand.systems`, deployed by Dokploy from `relay/`
- **Builds:** on the device, from the locked recipe in `toolchain/`. No image registry

The plan and architecture are in [`docs/PLAN.md`](docs/PLAN.md).

## Install

**macOS (Apple Silicon)**

    git clone https://github.com/martand-systems/mu.git && mu/install.sh

**Linux x86_64 (Ubuntu 24.04, including WSL2 on Windows)**

    git clone https://github.com/martand-systems/mu.git && sudo mu/install.sh --system && mu/install.sh

`--system` installs the Ubuntu packages μ needs (`toolchain/linux-64/apt.txt`: OpenFOAM's MPI runtime, GL/X client
libraries, flatpak, build tools for OpenVSP's Python API). It's the only step that needs root. If you skip it, the installer
asks for sudo when something is missing. Everything else installs per user in `~/.local/share/mu`.

That's it. The installer builds the full toolchain from the locked recipe, checks every tool (`mu doctor`) and runs the
benchmark suite. It puts `mu` on your PATH (`~/.local/bin`, added to your shell profile if needed). Options:
`./install.sh --help`.

| Install | Time | New disk |
|---|---|---|
| First install, downloads cached (a reinstall) | about 2–3 min | about 9 GB |
| Another version with the same locks (most releases) | about 1 s + 20 s of checks | a few MB |
| First install on a new Mac | plus downloading about 3 GB | |

**Why it's fast:** environments and apps are immutable, content-addressed **layers** in `~/Library/Mu/store/`, built once
per Mac and shared by every version that has the same locks. A version is a recipe snapshot plus links to its layers.
Layers build in parallel: the DMGs expand on every core and the CAM environment builds alongside the main one.
Python packages install with `uv`, as APFS clones from its cache. With warm caches nothing touches the network: every
step tries offline first.

**Integrity:** every app archive, conda package and PyPI package is pinned by hash (SHA-256 / MD5). Layers are sealed
read-only once built, and `mu doctor` checks that they are.

## Use

    mu                      status: the version this folder uses, its project, common commands
    mu run python part.py   run a tool (python, gmsh, ccx, cad, openfoam, pvbatch, prusa-slicer, cam-python, …)
    mu doctor · mu test     check every tool · run the benchmark suite
    cd <project> && mu link connect a project (mu.toml) for Claude Code
    mu versions             installed versions, the default, and which projects pin each
    mu update               install the latest release and make it the default
    mu autoupdate           automatic updates: status (on, off, run, set: see Updates)
    mu pin [V]              pin this project to a version (mu.toml) and relink it
    mu gc --yes             remove versions and layers nothing uses
    mu help                 everything

A project pins its version in `mu.toml` (`[mu] version = "v2026.11.3"`). `mu` picks that version inside the project,
so projects on different versions coexist.

## Updates

The installer turns on a **daily auto-update**. Each day, from an early-morning time picked at random per machine, μ
checks the repo for a newer release tag (`vYYYY.MM.N`; only tags, never a branch of the same name). It installs that
release the same way `mu update` does, including `mu doctor` and the benchmark suite, and makes it the default **only
if they pass**. Nothing else changes:

- **Projects keep their pins.** A project's `mu.toml` is never edited. Move one with `cd <project> && mu pin`.
- **A failing release changes nothing.** The old default stays, and so does the shared `mu` command (a release's
  installer replaces it before its checks run: it is put back if they fail or the install is stopped). `mu` shows a
  warning with the log, and you get a notification on macOS. A release that fails its checks, hangs (stopped after
  `max_hours`, 6) or is stopped mid-install is tried at most 3 times. Problems that aren't the release's don't use up
  those tries: no network, another install running, missing Ubuntu packages (shown as a one-time admin step:
  `sudo ./install.sh --system`). A newer release resets the count.
- **It waits** while on battery (macOS), while less than `min_free_gb` (12 GB) is free, while an install is running,
  and while `~/Library/Mu/state/busy` exists. A check that waited, or couldn't reach the repo, is tried again the next
  hour, so a laptop that is closed or unplugged at check time still updates later that day. On macOS the install keeps
  the Mac awake while it is on AC power.
- **Compute jobs hold `state/busy`.** Create it before resolving a toolchain version (write your pid on its first line,
  or leave it empty) and remove it when the jobs are done. While it exists, nothing is installed, the default doesn't
  move and nothing is removed. A release that finished installing becomes the default at the next run after that.
- **Old versions are cleaned up after a switch.** It keeps the new default, the previous one (roll back with
  `mu use <previous>`) and every version a registered project pins. If a registered project's `mu.toml` can't be read
  (an unplugged drive), nothing is removed.

Commands:

    mu autoupdate                 status: on or off, schedule, next check, last check and result (--json)
    mu autoupdate on | off        turn it on or off (off is remembered: reinstalling doesn't turn it back on)
    mu autoupdate run [--force]   check now (--force: also on battery, and after 3 failures)
    mu autoupdate set KEY VALUE   policy switch|install-only, min_free_gb, gc on|off, keep_previous, hour, minute, max_hours

Settings live in `~/Library/Mu/autoupdate.conf` (plain `key=value`), state in `state/autoupdate.state` and logs in
`logs/autoupdate-*.log` (the newest 20 are kept). `policy=install-only` installs and checks new releases but leaves the
default alone. To opt out at install time, use `./install.sh --no-autoupdate`. CI runs (`CI=true`, `MU_NO_AUTOUPDATE=1`,
`--no-modify-path`) never turn it on. Before deleting μ by hand, run `mu autoupdate off`.

**Scheduler.** On macOS it's a LaunchAgent, `~/Library/LaunchAgents/systems.martand.mu.autoupdate.plist`, that fires
every hour at the chosen minute; the run itself decides whether the day's check is due. A time missed while the Mac
sleeps runs when it wakes. Changing the schedule while an update runs doesn't stop it (run `mu autoupdate on` again
afterwards). On Linux it's a systemd user timer, `~/.config/systemd/user/mu-autoupdate.timer`, hourly with
`Persistent=true`. A user timer runs only while that user's systemd manager runs. For an account that never logs in,
such as the WSL compute node's agent user, enable linger once as root: `sudo ./install.sh --system --user <name>` (or
`sudo loginctl enable-linger <name>`). `sudo ./install.sh --system` does this for the user who ran sudo. After that,
`sudo -iu <name> mu autoupdate on` works from an admin's shell (μ finds the user's `/run/user/<uid>` itself). WSL
needs `[boot] systemd=true` in `/etc/wsl.conf`. `mu autoupdate` tells you when either is missing; while it says on,
the next install sets the timer up again. The source repo must be readable without a prompt (Keychain, `gh auth` or
a deploy key, also through `core.sshCommand`): the check never asks for credentials.

## Layout

macOS paths below. On Linux: `~/.local/share/mu/…` and `~/.cache/mu/`.

    ~/Library/Mu/toolchains/<version>/   recipe snapshot, commands, MANIFEST.json, links to its layers
    ~/Library/Mu/store/                  shared read-only layers: env-<hash>, cam-<hash>, app-<name>-<sha>
    ~/Library/Mu/state/                  tool state and caches (text-to-cad, FreeCAD, ParaView, bytecode, benchmarks)
    ~/Library/Mu/logs/                   install and auto-update logs
    ~/Library/Mu/autoupdate.conf         auto-update settings (mu autoupdate)
    ~/Library/Caches/Mu/                 downloads, conda packages, uv cache (safe to delete; re-downloads)

## Status

| Phase | State |
|---|---|
| Plan | v5 (`docs/PLAN.md`) |
| S1: reproducible macOS build | Done: fresh clean-environment builds pass doctor and benchmarks, and reproduce the legacy toolchain (`docs/verification/`) |
| S2: linux-64 provisioning | Every tool runs headless on Ubuntu 24.04 (plain and WSL images); see `docs/verification/` |
| S3–S4 | Not started |

`legacy/` holds the hand-built toolchain's activation script, setup report and instructions. They're the reference that the recipe has to reproduce.
