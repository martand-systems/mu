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

**Windows 11 compute node** (a WSL2 distro that runs with nobody signed in and stops cleanly at a restart):
`install/hosts/wsl2/mu-wsl2-host.ps1`, see [`install/hosts/wsl2/README.md`](install/hosts/wsl2/README.md).

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
    mu pin [V]              pin this project to a version (mu.toml) and relink it
    mu gc --yes             remove versions and layers nothing uses
    mu help                 everything

A project pins its version in `mu.toml` (`[mu] version = "v2026.11.3"`). `mu` picks that version inside the project,
so projects on different versions coexist.

## Layout

macOS paths below. On Linux: `~/.local/share/mu/…` and `~/.cache/mu/`.

    ~/Library/Mu/toolchains/<version>/   recipe snapshot, commands, MANIFEST.json, links to its layers
    ~/Library/Mu/store/                  shared read-only layers: env-<hash>, cam-<hash>, app-<name>-<sha>
    ~/Library/Mu/state/                  tool state and caches (text-to-cad, FreeCAD, ParaView, bytecode, benchmarks)
    ~/Library/Mu/logs/                   install logs
    ~/Library/Caches/Mu/                 downloads, conda packages, uv cache (safe to delete; re-downloads)

## Status

| Phase | State |
|---|---|
| Plan | v5 (`docs/PLAN.md`) |
| S1: reproducible macOS build | Done: fresh clean-environment builds pass doctor and benchmarks, and reproduce the legacy toolchain (`docs/verification/`) |
| S2: linux-64 provisioning | Every tool runs headless on Ubuntu 24.04 (plain and WSL images); see `docs/verification/` |
| S3–S4 | Not started |

`legacy/` holds the hand-built toolchain's activation script, setup report and instructions. They're the reference that the recipe has to reproduce.
