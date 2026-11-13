# μ (mu): Martand engineering toolchain. Plan

- **Status:** v6, in implementation
- **Date:** 2026-11-14
- **Owner:** Martand Defense
- **First project:** JATAYU
- **Name:** μ, written `mu` wherever only ASCII works (CLI, repo, files). Icon: the μ glyph.

**Where things stand (2026-11-14):** the four spikes have run and there are no more (§16). The toolchain is installed
on the laptop and the office PC (release v2026.11.6, auto-update on), the office PC runs as an always-on WSL2 node,
and the relay is deployed. No job has run on the office PC yet: the job layer is being built (§6.4a). The phase
table and the path to the first remote job are in §13. Where this plan describes something not built or not measured
yet, it says so; measurements are in `docs/verification/`.

**Changes in v6 (2026-11-14):** brought in line with what the spikes measured and what is built. Office PC hardware
corrected (§4). The Windows node is two host scripts, not `MuComputeSetup.exe` (§5.1, §6.1). The relay section
matches the relay as deployed (§7). The first job layer is OpenSSH inside the relay tunnel (§6.4a, decided
2026-11-14). §12 and §13 have a status column, §14 and §15 are updated, and §16 records the spike results.

**Changes in v5:** renamed to μ (`mu`); ADR-001 records the Linux-compute and WSL2 decision (§5.3a); DNS done; relay set up through the Dokploy UI; JATAYU moved into its repo early.

**Changes in v4:** all repos private; relay at `relay.martand.systems`; Dokploy GitHub app installed; compute nodes are hardware-agnostic (§6.1a); decisions and readiness recorded (§15, §16).

**Changes in v3.3:** no container registry. Every device builds its toolchain from the locked recipe, and Dokploy builds the relay on the server (§5.3, §7.4, §10).

**Changes in v3:** the relay is a Docker container on Martand's existing server, on Martand's existing Dokploy server (195.201.236.190), reached through that server's Traefik at a `martand.systems` subdomain, and deployed by Dokploy from GitHub (§7).

**Changes in v2:**
- JATAYU data is cleared for GitHub.
- Tailscale is replaced by a relay on Martand's own server.
- mu installs system-wide like an application (VS Code-style), no longer confined to one folder.
- Projects are fully self-contained in their git folder.
- Claude Code is the primary user of mu.

---

## 1. Goal

Turn today's engineering toolchain (code-driven CAD, FEA, CFD, aero, slicing and CAM) into
**installable software that Claude Code uses to design hardware**, kept separate from the projects it's used on.

| Analogy | Here | Example |
|---|---|---|
| VS Code / Microsoft Word, the application | **mu**: the toolchain. Installed once per machine, company-agnostic | `mu v2026.11.6` |
| Company template | **Org profile**: Martand Defense's engineering rules, templates, materials | `mu-org-profile` |
| A document / a code repo | **Project**: one product, fully contained in its own git folder | `jatayu` |

Requirements:

1. **Claude Code is the primary user.** Every mu capability can be driven by Claude Code from
   start to finish (design, analysis, validation, reports, managing compute). You use the same Claude Code
   interface. Nothing needs a GUI except viewing results.
2. **One-click install** from GitHub, like an application: a full install on any machine, or split roles.
3. **Two roles that can be split:**
   - The **workstation** is where Claude Code, the project and the viewers live.
   - **Compute** is where meshing, solvers and heavy post-processing run.
4. **A full install** (workstation plus local compute) works completely offline.
5. **The workstation uses remote compute from anywhere** (office, home, travel) through a **relay
   container on Martand's own Docker server**, at a `martand.systems` subdomain. No third-party VPN.
   Everything the server runs comes from GitHub and is deployed with Docker.
6. **When remote compute is unreachable, work carries on** using local compute.
7. **A project is self-contained in its git folder.** Cloning the repo gives everything project- and
   company-specific. The only outside dependency is the installed mu application, at the version the
   project pins.
8. **Reproducible:**
   - Toolchain versions are pinned, benchmarked and tagged.
   - Every result records the version, machine and commit that produced it.
9. **Everything lives on GitHub:** toolchain source and releases, the org profile, projects (data cleared for GitHub).

---

## 2. Terms

| Term | Meaning |
|---|---|
| **mu** | The installed toolchain application: `mu` CLI, environments, solvers, wrappers, Claude Code plugin, benchmarks |
| **Role** | What an install does: `workstation`, `compute`, or `full` (= workstation + local compute) |
| **Compute node** | A machine running the mu job layer and solver stack: remote (`office-pc`) or `local` |
| **Agent** | The service on a compute node that runs jobs, streams logs and returns results. In the first job layer this is `sshd` plus the `mu node` runner (§6.4a); a native agent with the same verbs comes later |
| **Relay** | A small service on Martand's server that connects workstations to compute nodes from any network |
| **Org profile** | A company repo (validation policy, templates, materials). Included in each project as a pinned git submodule |
| **Project** | A self-contained git repo for one product or study |
| **Run record** | `runs/<run-id>.json`: the provenance of every solver run |

---

## 3. Starting point (2026-11-12)

This section describes the hand-built setup μ started from. It is kept as the reference for the migration (§12). The
state today is in §13.

Everything is in one folder, `Martand/JATAYU/ENGINEERING/` (~30 GB), and the toolchain and the project are mixed together:

| Item | Size | Belongs to |
|---|---|---|
| `env/`, `env.yml` (Python 3.13, OCP 8.0.1.1, build123d 0.13, CadQuery git `b3f9afd`, Gmsh 4.15.2, CalculiX 2.23, VTK 9.7.0, PyVista) | 3.4 GB | mu |
| `apps/` (OpenVSP 3.56.0, ParaView 6.2.1, OpenFOAM v2606 .app, PrusaSlicer 2.9.7, FreeCAD 1.1.4) | 6.0 GB | mu |
| `tools/` (wrappers, micromamba, cam-venv with OCP 7.9, patches, pip-stubs, scripts) | 1.3 GB | mu |
| `vendor/text-to-cad` (v0.11.9, `8ea2434`), `.claude/skills/cad` | 66 MB | mu (Claude plugin) |
| `activate.sh`, `SETUP_REPORT.md` | | mu |
| `CLAUDE.md`: commands and folder rules | | mu (Claude plugin) |
| `CLAUDE.md`: "results must be validated" policy | | Org profile |
| `.cache/` | 4.7 GB | mu cache / project scratch |
| `JATAYU_Phase1_*.docx`, `../JATAYU_Technical_Concept.docx` | | Project |
| `projects/jatayu-frame/` (scripts, params, results, img, cad_out, mfg, cfd) | ~370 MB | Project |
| `projects/jatayu-frame/work/` | **14 GB** | Project scratch (regenerable) |
| `projects/jatayu-frame/felib.py` (C3D10 `.inp` writer, `.frd` reader) | | Probably mu (`mu.fea`) |
| Generic docx styling in `build_report.py` | | Org profile report template |

Problems:
- No separation between the toolchain and the project.
- Not reproducible on another machine.
- Hard-coded `../../activate.sh` paths.
- The 5 benchmark projects named in `CLAUDE.md` are **missing** from `projects/`.
- All compute is limited to a 16 GB M1 laptop.

---

## 4. Immediate deployment target

| Machine | Hardware | OS / arch | mu role |
|---|---|---|---|
| **Laptop** (this Mac) | Apple M1, 16 GB RAM | macOS, arm64 | **`full`**: Claude Code, projects, local compute. Sends heavy jobs to `office-pc` when it's reachable |
| **Office PC** (`office-pc`) | `MSI-PROZ890-PC`: Intel Core Ultra 7 265K (20 cores / 20 threads), 31 GB RAM, NVIDIA RTX 5060, one NVMe (C:, 465 GB). Read on the PC in S3 | Windows 11 Pro (build 26300), x86_64, WSL 3.0.5.0 | **`compute`**: Linux solver stack in WSL2 |
| **Relay server** (`relay`) | `quarve-8gb-fsn1-2`, 195.201.236.190 (Hetzner, 8 GB). A Dokploy deploy server with its own Traefik; ~12–15 containers already running | Linux + Docker (Dokploy) | **`relay`**: one Dokploy compose service behind the server's Traefik, at `relay.martand.systems`. Connects the laptop and the office PC from any network. Stores nothing |

```
     Laptop (anywhere)            195.201.236.190 (Dokploy)                   Office PC (always on)
 ┌─────────────────────────────┐   ┌───────────────────────────┐   ┌───────────────────────────────┐
 │ mu FULL (macOS arm64)       │   │ Traefik (Dokploy-managed) │   │ Windows 11                    │
 │  Claude Code + mu plugin    │wss│  :443 (shared, TLS)       │wss│  WSL2 distro "mu-compute"     │
 │  project git folders        │──►│  relay.martand.systems    │◄──│   ├ tunnel client, sshd       │
 │  viewers                    │443│        │ docker network   │443│   ├ mu node (job runner)      │
 │  local compute (fallback)   │   │  [mu-relay container]     │   │   ├ solver stack (linux-64)   │
 └──────────────┬──────────────┘   │  forwards only, no ports  │   │   └ /srv/mu/jobs              │
                │                  │  published, stores nothing│   └───────────────────────────────┘
                │                  └───────────────────────────┘
                │ (office LAN: also through the relay for now, §6.3)          ▲
                ▼ git push/pull                                               │ release downloads
        GitHub: mu (source, plugin) · mu-org-profile · jatayu ────────────────┘
```

**Both the laptop and the office PC only make outbound connections.** No ports are opened on the office
network, and no VPN software is installed on the laptop.

What exists of this picture today: both installs, the WSL2 distro that runs with nobody signed in, and the relay.
The tunnel client, `sshd` and `mu node` on the office PC are the job layer (§6.4a), which is being built and is not
on the PC yet.

---

## 5. mu as an installed application

### 5.1 Install locations (like VS Code)

**macOS (laptop):**

| What | Where |
|---|---|
| CLI on `PATH` | `~/.local/bin/mu` (symlink; the installer adds `~/.local/bin` to the shell profile if needed) |
| Toolchain versions (side by side) | `~/Library/Mu/toolchains/<tag>/` (recipe snapshot, wrappers, plugin, links to layers) |
| Shared layers (immutable, content-addressed) | `~/Library/Mu/store/`: `env-<hash>`, `cam-<hash>`, `app-<name>-<sha>` |
| Auto-update settings, state and logs | `~/Library/Mu/autoupdate.conf`, `state/`, `logs/` |
| Paired compute nodes (job layer, §6.4a; not built yet) | `~/Library/Mu/compute/<name>/`: the workstation's key for that node, the node's pinned host key, the generated ssh config |
| Relay credentials | Today `~/.config/mu/relay.env` (mode 600), written by `relay/gen-credentials.sh`. The job layer design moves the workstation's relay password to the macOS Keychain |
| Caches and downloads | `~/Library/Caches/Mu/` (safe to delete) |
| Local compute workspaces | Not built. A local job runs in the project's working tree, as `mu run` does today (§6.3) |

- The bundled ParaView, FreeCAD, PrusaSlicer, OpenVSP and OpenFOAM go into **μ's store**, not `/Applications`,
  so they never clash with copies you install yourself, and several mu versions can coexist.
- **Layers (v2026.11.3).** Each environment and app is a read-only layer named by the hash of everything that
  goes into it: lock files, pins, the archives it depends on, and a layer-format version. A toolchain version
  is a recipe snapshot plus symlinks to its layers. A release that doesn't change the locks reuses every layer:
  it installs in about a second and costs a few MB. `mu gc` removes layers no installed version references.
- Uninstall a version: `mu remove <version>`. Remove everything: delete the folders above.

**Windows (office PC, compute role):**

As installed by `install/hosts/wsl2/mu-wsl2-host.ps1` (§6.1; the paths are parameters, these are the defaults):

| What | Where |
|---|---|
| Host script (the copy later runs use), its recorded settings, logs | `C:\Mu\host\`, `C:\Mu\state\wsl2-host.json`, `C:\Mu\logs\` |
| Shutdown hook (service `mu-wsl-shutdown`) | `C:\Mu\bin\mu-wsl-shutdown.exe` |
| WSL distro disk | `C:\Mu\wsl\mu-compute\` (readable only by the owner, Administrators and SYSTEM) |
| Startup and watchdog task | Task Scheduler, `\mu\mu-compute` |
| WSL settings | `%UserProfile%\.wslconfig` |
| μ itself, the job layer's files | Inside the distro: the Linux layout below, and `/srv/mu` for jobs (§6.4a) |

**Linux (S2, 2026-11-13; the WSL2 compute distro and plain Ubuntu 24.04):** per user, XDG: `~/.local/share/mu/`
(toolchains, store, state, logs), `~/.cache/mu/` (downloads, packages), `~/.local/bin/mu`. One root step,
`sudo ./install.sh --system`, installs the Ubuntu packages in `toolchain/linux-64/apt.txt`. How each tool runs on Linux:

| Tool | linux-64 route (pinned) |
|---|---|
| conda env | same versions as macOS (`toolchain/linux-64/env.explicit.txt`; only build strings differ), software EGL via mesa-llvmpipe |
| OpenFOAM v2606 | ESI's noble `.deb`s unpacked without root (same source as the macOS app); Open MPI 4.1 from Ubuntu |
| ParaView 6.2.1 | Kitware's Linux tarball; pvbatch forced to OSMesa (headless, no GPU) |
| OpenVSP 3.56.0 | official Ubuntu 24.04 `.deb` (vspaero etc.) + the Python API **built on the device** for Python 3.13 (the `.deb`'s API is 3.12-only), a cached layer |
| FreeCAD 1.1.4 | AppImage extracted without FUSE; same Python 3.11.14 bundle hosts the CAM venv |
| PrusaSlicer 2.9.7 | Flathub (its only Linux channel since 2.9), pinned by OSTree commit, in μ's own flatpak installation |

A container server (later) builds the same thing with `docker compose build` (§5.3). Containers need `--privileged`
(flatpak's sandbox) and a system D-Bus; WSL2 with systemd needs neither.

**The current `CLAUDE.md` folder rule** ("never write outside this folder") **is replaced** for mu once it's installed:
mu owns the locations above, and projects own their git folders. The rule stays in force for the current
folder until migration is finished.

### 5.2 Install roles

| Role | Contents | Phase 1 machine |
|---|---|---|
| `workstation` | `mu` CLI, Claude Code plugin, light CAD env (build123d, OCP, render, text-to-cad viewer), ParaView GUI, git, gh, git-lfs | (included in `full`) |
| `compute` | mu job layer and full solver stack (Gmsh, CalculiX, OpenFOAM, pvbatch/pvserver, VSPAERO, PrusaSlicer, FreeCAD CAM + cam env) | Office PC |
| `full` | Both, plus a built-in `local` node | Laptop |
| `relay` | One Docker container, **built and deployed by Dokploy** from `relay/` in the GitHub repo | 195.201.236.190 |

**Built so far:** the installer has no `--role` option. It builds the same full toolchain on every machine (macOS
and Linux), so the laptop and the office PC both have everything. What makes the office PC a compute node is the
host script (§6.1) and, next, the job layer (§6.4a). A lighter `workstation`-only install is not built.

### 5.3 Built on the device from one locked recipe (no image registry)

mu ships **recipes, not binaries.** Every device builds its own toolchain from the same pinned inputs in the `mu` repo:

- **Lock files:** for osx-arm64 and linux-64, in `toolchain/<platform>/`: an explicit conda lock (the exact URL and
  MD5 of every package) and Python locks with a SHA-256 for every package.
- **`apps.toml`:** the exact URL and SHA-256 of each upstream app download. On Linux, PrusaSlicer is pinned by its
  Flathub OSTree commit.
- **`pins.toml`:** exact git commits.
- **One installer** for macOS and Linux, `install/install.sh` (run as `./install.sh`).

| Target | How it's built on the device |
|---|---|
| **Windows 11 (office PC)** | `install/hosts/wsl2/mu-wsl2-host.ps1` installs a WSL2 distro (Ubuntu 24.04), and `./install.sh` runs inside it as the user `mu` (§6.1) |
| **macOS arm64 (laptop)** | `./install.sh` builds into `~/Library/Mu/` (layers in `store/`, versions in `toolchains/<tag>/`) |
| **Linux server (later)** | A compose file with `build:`, where the Dockerfile runs the same `install.sh`. Built with `docker compose build` on the server. Not written |
| **Relay (Dokploy server)** | Dokploy builds `relay/Dockerfile` on the server from the GitHub repo. It's tiny (one static binary) |

**Same recipe, same packages.** Because every package and download is pinned by hash, two devices that build the same tag get the same versions and builds.
The **benchmark suite runs at the end of every install** (§9). An install that doesn't reproduce the reference answers isn't marked ready.

**Trade-offs of building on the device instead of shipping images:**

| | Build on device (chosen) | Prebuilt image in a registry |
|---|---|---|
| GitHub storage and cost | None beyond the repo | Several GB per version (doesn't fit the Free plan's 500 MB) |
| Install time | Measured. Laptop: 2–3 min with the downloads cached, plus about 3 GB to download on a new Mac. Office PC, first install with empty caches: 96 min, nearly all of it waiting for downloads (S3). A later release with unchanged locks: under a minute including the checks (54 s on the laptop, v2026.11.6) | ~10 min download |
| Upgrades | Only changed packages are re-downloaded (shared package cache) | Whole image layers |
| Depends on upstream sites staying up | Yes. Mitigated by the local package cache, and optionally a cache of `apps.toml` downloads on the office PC or Dokploy server | No |
| Byte-for-byte identical binaries | Same hashes for every package, so effectively yes. The benchmarks prove equivalence | Yes |

### 5.3a ADR-001: compute is Linux; on Windows it runs in WSL2 behind a host adapter

**Status:** accepted, 2026-11-12. This is the decision that's hardest to reverse, so it's written down with its reasoning.
S2 and S3 (2026-11-13) tested the two assumptions it rests on, and both hold: every tool has a headless linux-64 route
with the same results as macOS, and the office PC's WSL2 distro runs with nobody signed in.

**There are two separate decisions here, and only the first is hard to reverse:**

1. **Compute runs on Linux (x86_64 or arm64) on every server-type node.** This is the long-lived architectural commitment.
   - The solvers are Linux-first. OpenFOAM's native platform is Linux, and its Windows build is a second-class MinGW port with MS-MPI.
     CalculiX, Gmsh, ParaView headless (EGL), and MPI generally are best supported and fastest on Linux.
   - **One compute platform means one recipe, one benchmark run and one set of numbers.** Native Windows compute would be a third platform
     (alongside linux-64 and macOS) with its own builds, quirks and validation. That cost grows with every tool added.
   - Every future target is already Linux: Linux servers, cloud VMs, HPC clusters, Docker.
   - **The exception is the laptop.** macOS `local` compute stays native because it already works and has to work offline.
     It's validated by the same benchmarks, and it's the only non-Linux compute build.
2. **How Linux runs on a given machine is a replaceable "host adapter".** On Windows the first adapter is **WSL2**.
   - The job layer, the installer and the benchmarks know nothing about WSL. They only see "a Linux userland with N cores, M GB, an optional GPU".
   - Only the Windows host scripts with their start, stop and status checks are WSL-specific (`install/hosts/wsl2/`).
     One exception inside μ: the free-disk check asks Windows which drive holds the distro's virtual disk
     (`install/disk.sh`), and falls back to the Linux figure when Windows doesn't answer.
   - **Switching adapters later** means writing another `install/hosts/<adapter>/` and re-provisioning the node. The rest of mu doesn't change.

**Why WSL2 is the right first adapter on Windows:**

| Option | Verdict |
|---|---|
| **WSL2** | Built into Windows 11 (Home and Pro), Microsoft-maintained real Linux kernel, near-native CPU speed on its ext4 disk, NVIDIA CUDA passthrough supported, scriptable install/import/unregister. **Chosen** |
| Native Windows builds | A third compute platform. OpenFOAM is second-class on Windows; MPI and performance differ. Rejected |
| Docker Desktop | Uses WSL2 underneath anyway, adds a layer, and needs a paid licence for larger companies. Rejected |
| Hyper-V / VirtualBox full VM | Works, but heavier, needs a fixed RAM split, and GPU passthrough is poor (Hyper-V needs Pro). **Kept as the fallback adapter** |
| Dual-boot or reinstall the PC as Linux | Best performance, but takes the PC away from Windows use. Supported anyway, through the native-Linux adapter (later) |

**Known WSL2 risks, and how they're handled:**

| Risk | Handling |
|---|---|
| WSL may not run with nobody logged in | **Tested in S3: it runs.** After a reboot the distro ran 2 h 01 min with nobody signed in. It takes four settings together (§6.1): `instanceIdleTimeout=-1`, a startup task under the Windows user with S4U, automatic sign-in after a restart off, Fast Startup off. The Hyper-V adapter stays the fallback and is not written |
| Default memory cap of 50% of RAM | `.wslconfig` calculated from detected RAM: 75% (23 GB of the office PC's 31 GB) |
| Slow access to Windows files (`/mnt/c`) | Never used: all workspaces live on the WSL ext4 disk |
| A Windows restart powers the VM off with no Linux shutdown (**found in S3**: the last write before a restart came back as 21 NUL bytes) | A preshutdown service stops WSL cleanly first, write-back is faster, and the job layer trusts only results it wrote durably (§6.1, §6.4a). `test-stop` on the PC gives a clean stop; the restart tests on the PC are still to do |
| Windows or WSL updates restart the VM | The startup task brings the distro back after a restart, and its 5-minute repeat after a WSL update. Interrupted jobs are re-run once, then marked `interrupted`. Neither kind of update has happened on the PC yet, so both are unproven |
| Locked-down corporate PCs or cloud VMs without nested virtualisation | Those nodes use the Hyper-V or native-Linux adapter (neither is written) |

### 5.4 The `mu` source repo

The layout as it is on `main` (2026-11-14). Parts of the first plan that are not written are listed under the tree.

```
mu/                                  (GitHub: martand-systems/mu)
  CLAUDE.md                           instructions for developing mu itself with Claude Code
  install.sh                          entry point: runs install/install.sh
  install/
    install.sh                        the installer for macOS and Linux (non-interactive; Claude can run it)
    install-mac.sh                    kept for older instructions; runs install.sh
    download.sh  flatpak.sh           resumable downloads; the Flathub download cache
    disk.sh  dmg2raw.py               free-disk check (WSL: the Windows drive); parallel DMG expansion
    hosts/wsl2/                       host adapter (ADR-001): mu-wsl2-host.ps1, mu-wsl2-distro.sh, README.md
  bin/
    mu                                dispatcher: versions, install, update, use, pin, remove, gc, autoupdate
    mu-toolchain                      per version: run, python, shell, env, doctor, test, link, version, path
  lib/mu-env.sh                       activation
  toolchain/
    env.yml  apps.toml  pins.toml     the spec, per-platform app URL / SHA-256, git pins
    osx-arm64/  linux-64/             explicit conda locks, hashed Python locks; linux: apt.txt, flathub.gpg,
                                      the OpenVSP API build
    patches/  pip-stubs/              one-OCP-per-env fixes
  relay/                              Dockerfile, compose.yaml, entrypoint.sh, gen-credentials.sh, .env.example,
                                      README.md (the Dokploy compose service, §7)
  src/mu/                             fea.py (from felib.py), bench.py, doctor.py, post/render_png.py
  wrappers/macos/  wrappers/linux/    gmsh, vsp, vspaero, pvbatch, openfoam, prusa-slicer, cam-python, …
  claude/CLAUDE-toolchain.md          what a project's Claude Code reads, through `.mu/toolchain` (mu link)
  claude-plugin/skills/cad/           the cad skill (text-to-cad)
  benchmarks/                         cad-solid, fea-cantilever, aero-vlm-wing, cfd-cavity, slice-print, cam-plate;
                                      reference/ (osx-arm64.json, linux-64.json)
  tools/                              lock export; S2 probes (s2/); the S4 test bed (s4/); tests for downloads,
                                      auto-update, the WSL disk check and the WSL2 host scripts
  docs/                               PLAN.md, verification/ (one record per spike), design/ (job-layer.md, arriving
                                      with the job layer)
  legacy/                             the hand-built toolchain's activation script and setup report (reference)
  .github/workflows/                  linux-install and s2-probe (run on demand), s4-relay (on relay changes)
```

Not written yet:
- the job layer, `src/mu/jobs.py` (workstation side) and `src/mu/node.py` (node side), on branch `job-layer` (§6.4a);
- the rest of the Claude Code plugin (`plugin.json`, the other skills, commands, hooks) and the commands `new`,
  `context` and `check` (§6.5);
- `templates/project/` (the skeleton for `mu new`);
- the other host adapters (`hyperv-vm/`, `linux-native/`) and a Linux-server compose file;
- a native relay and agent (`transport/`, Stage B in §7.3).

---

## 6. Components in detail

### 6.1 Compute node on Windows 11 (office PC)

**Install:** on the PC, as the Windows user who will own the distro (an administrator), from `install/hosts/wsl2/`:

    powershell -NoProfile -ExecutionPolicy Bypass -File .\mu-wsl2-host.ps1 install

**Running this script is the only step that needs a person at the PC.** It can be rerun at any time: a second
`install` changes only what differs, `status` checks everything and changes nothing, `uninstall` puts the Windows
settings back, and `test-stop` tests the clean stop without restarting Windows. The parameters, every setting and the
restart tests are in `install/hosts/wsl2/README.md`. The first plan's `MuComputeSetup.exe` and a Windows service that
keeps `wsl.exe` alive are not built: S3 showed that neither is needed.

What the two scripts (`mu-wsl2-host.ps1` on Windows, `mu-wsl2-distro.sh` inside the distro) set up. The settings in
steps 3 to 5 are the ones S3 found necessary (`docs/verification/2026-11-13-S3-wsl.md`):
1. **WSL2:** installed if missing (one Windows restart, then run the script again), updated if older than 2.5.4.
   Hardware virtualisation must be on in the firmware (Intel VT-x or AMD-V); on the office PC it is.
2. **The distro `mu-compute`:** Ubuntu 24.04, its disk on the NVMe drive (`C:\Mu\wsl\mu-compute`), systemd on, and
   an unprivileged user `mu` without sudo. Admin work goes through `wsl -d mu-compute -u root`.
3. **`.wslconfig`**, calculated on the machine:
   - `[general] instanceIdleTimeout=-1`. This is what keeps the distro running with no `wsl.exe` attached. A running
     systemd service alone does not.
   - `memory=` 75% of RAM (23 GB of the office PC's 31 GB; the WSL default is 50%), `processors=` all, `swap=8GB`,
     `guiApplications=false`.
4. **A scheduled task at system startup**, `\mu\mu-compute`, run as the Windows user with S4U ("run whether the user
   is signed in or not", no stored password). It starts the distro 30 s after boot and repeats every 5 minutes as a
   watchdog, because a WSL update stops the WSL service and the distro with it. The SYSTEM account cannot be used:
   WSL refuses it.
5. **Power and sign-in:** no sleep or hibernate on AC power, Fast Startup off, automatic sign-in after a restart
   (ARSO) off. With ARSO on, Windows signs the user in by itself after a restart (seen in S3), and a WSL session
   started from such a sign-in would end when the user signs out.
6. **A clean stop before Windows shuts down** (the S3 finding: a restart powers the WSL VM off with no Linux
   shutdown, and the last write before one came back as 21 NUL bytes):
   - a Windows service `mu-wsl-shutdown` (LocalSystem) that takes the preshutdown notice and stops `WSLService`, which
     stops every distro cleanly. It gives itself a 90 s preshutdown timeout and puts itself first in
     `PreshutdownOrder`;
   - faster write-back inside the distro (`vm.dirty_expire_centisecs=500`, `vm.dirty_writeback_centisecs=100`), so
     data left in the page cache by a hard stop is about 6 s old at most, not about 35 s;
   - stop timeouts of 8 s for services, inside the 10 s WSL gives systemd to power off;
   - a clean-stop marker, so that `status` and the job layer can tell whether the previous stop was clean.
7. **Repo access:** an SSH key for `mu`, added once by a repo admin as a read-only deploy key on `martand-systems/mu`.
   This is the one manual step outside the script.
8. **μ itself** (with `-InstallMu`): `./install.sh --system --user mu` as root, then `./install.sh` as `mu`. The
   installer runs `mu doctor` and the benchmark suite, and a version that fails them does not become the default.

**State on the office PC (2026-11-14):**
- The S3 settings were made by hand on 2026-11-13. The host scripts, merged to `main` on 2026-11-14, are installed
  there: every `status` check passes, and `test-stop` gives a clean stop in 1.5 s.
- μ v2026.11.6 is installed with auto-update on: `mu doctor` 22/22, benchmarks 6/6 against the macOS reference.
- **Not yet done on the PC:** the restart tests (a baseline restart with the `mu-wsl-shutdown` service stopped, then
  restarts with it), the first real Windows Update restart, and the first WSL update. The owner is away from the
  office; the restart tests wait for the next visit (§13).

**What the job layer adds here (§6.4a, not on the PC yet):** `sshd` on loopback with its gate, the tunnel client as a
service, `/srv/mu`, and the workstation's authorised key. That is one more run of the host script, plus pairing:
the PC's host key goes to the laptop and the laptop's public key goes to the PC. There is no pairing code. Until
then nothing on the laptop can reach the PC; the remote management commands of the first plan
(`mu compute upgrade | restart | logs`) are not built, and `mu compute status` comes with the job layer.

**Machine settings:**
- No sleep or hibernate on AC power, Fast Startup off, ARSO off (set by the host script, above).
- The firmware should restore power after a power cut. Not checked on the office PC.
- Windows Update automatic restart is allowed; the distro comes back through the startup task. Unproven until the
  first real update restart.
- μ keeps itself on the latest release with `mu autoupdate` (§9): a systemd user timer for the user `mu`. That user
  never logs in, so the timer needs linger, set once as root: `sudo ./install.sh --system --user mu`; then install as
  `mu` (`sudo -iu mu`), which turns the timer on. The node's job runner creates `state/busy` before it resolves a
  toolchain version for a job and removes it when the queue is empty: updates (install, switch, cleanup) wait for it.

**Performance:**
- Workspaces are on the WSL ext4 disk (`/srv/mu/jobs`), never on `/mnt/c`.
- MPI ranks and OpenMP threads are set from the physical core count.

**GPU:** CUDA works in WSL2 through the Windows driver (not tried on the office PC). Phase 1 solvers use the CPU only,
and rendering on the node is software (OSMesa, S2). The RTX 5060 is not used by μ yet; it is there for later GPU
solvers, rendering or surrogate models.

### 6.1a Any machine can be a compute node (hardware-agnostic)

The office PC is only the first node. **Nothing in mu is tied to a particular CPU, GPU or amount of RAM.** This is
the design. What exists of it today: the host script and the installer calculate their settings from the machine, and
the first job layer has one node, one job at a time and no scheduling by capability (§6.4a).
- **Detected at install, and re-checked every time the agent starts:**
  - CPU vendor, physical and logical cores
  - RAM, free disk on the workspace drive
  - GPU vendor and model, VRAM, whether CUDA is available
  - OS and architecture

  The agent reports these as its **capabilities** to paired workstations (`mu compute list` shows them).
- **Settings come from the detected hardware, never from fixed numbers:**
  - WSL memory (75% of the machine's RAM) and processors (all)
  - MPI ranks (physical cores), OpenMP threads, Gmsh threads
  - the memory each job may use
- **Scheduling uses capabilities.** A job declares an estimated memory and core need (mu estimates it from mesh size).
  `auto` picks the first preferred node that can run it. A GPU-only job (later) goes only to nodes reporting CUDA.
- **The GPU is optional.** A node without one is still a full compute node.
- **Adding a node** is the same on any Windows 11 or (later) Linux machine: run the host script, pair it, done.
  Several nodes can be paired. Their names come from pairing (`office-pc`, `lab-01`, …).

### 6.2 Full install on the laptop (macOS arm64)

- **Install:** `git clone https://github.com/martand-systems/mu.git && mu/install.sh` (non-interactive, so Claude Code
  can run it). A signed `.pkg` is a P8 item and is not built.
- Builds the toolchain from the locks into `~/Library/Mu/` (shared layers in `store/`, versions in `toolchains/<tag>/`),
  then runs `mu doctor` and the benchmark suite. Measured in S1 and the installer v2 record. The laptop runs
  v2026.11.6 with auto-update on: `mu doctor` 21/21, benchmarks 6/6 (2026-11-14).
- `mu compute add office-pc --host-key … --relay https://relay.martand.systems` will pair the laptop with the office PC
  through the relay (§6.4a; not built yet).
- **"Workstation mode" isn't a separate install.** It's the `full` install sending compute to `office-pc`.

### 6.3 Choosing compute and falling back

Planned, in the project's `mu.toml` (today μ reads only `[mu] version` from that file):

```toml
[compute]
policy   = "auto"                  # auto | remote | local
prefer   = ["office-pc", "local"]
local_only = ["cad", "render", "checks", "report"]
max_local_mem = "auto"            # default: 75% of this machine's RAM (12 GB on the 16 GB laptop)
```

| Situation | Behaviour |
|---|---|
| Laptop on the office LAN | Through the relay, like anywhere else. The direct LAN path is deferred: the distro sits behind WSL's NAT, so it needs a Windows port proxy and a firewall rule, and the relay path works in the office too |
| Laptop elsewhere, office PC online | Through the relay |
| Office PC or relay unreachable (relay down, PC off, timeout) | **Falls back to `local`**, says so, and records `host=local` with the reason. μ allows about a minute after a network change or a restart of the node before it decides (S4 measured up to about 60 s for a tunnel to come back) |
| The node answers but refuses: its host key changed, the workstation's key is not authorised, the protocol versions differ | **Stops with the error and never falls back**, so a security failure cannot pass as "offline" |
| The node is reachable but lacks the project's pinned toolchain version, or is short of disk | Stops with a hint. The version is not installed as a side effect of a submit; `--on local` is the explicit way round |
| Job too big for local and the office PC is unreachable | Stops with options: force local at a coarser mesh, or (later, with the native agent) queue it on the laptop until the node is back |
| Link lost mid-job | The job keeps running on the PC. `mu status`, `mu log` and `mu fetch` work again when the link returns |
| Override | `mu submit --on local …` / `--on office-pc …` (with `--on office-pc`, or `policy = "remote"`, it never falls back) |

**Continuity guarantee:** project files, git, Claude Code and all light tasks are always on the laptop.
Losing the office PC or the relay slows down big runs but never blocks work.

**Built so far:** none of this section. Today every run is local (`mu run`). `mu submit --on <node>` comes with the
first stage of the job layer, and the `auto` fallback with its second stage (§6.4a).

### 6.4 Job protocol and data volume

The link is designed for a **small amount of data**:

| Direction | What crosses the link |
|---|---|
| Up | The project working tree as a git snapshot (§6.4a): tracked changes and untracked files, minus gitignored files. Git sends only the objects the node doesn't have, so a typical iteration is kilobytes to a few MB (scripts, params, STEP). Until the job layer's second stage, LFS files arrive as pointer files and submodules as links |
| Down | **Declared outputs only:** results JSON, logs, PNG plots, small VTK extracts. Typically < 10 MB per job. Each file is checked against the SHA-256 in the job's result record |
| Not sent by default | Raw output (meshes, `.frd`, OpenFOAM fields). It stays on the office PC in the job's folder, `/srv/mu/jobs/<job-id>/work/`. `mu fetch --all` (second stage; warns about size) brings it, or it is viewed remotely later |
| Logs | Streamed live by byte offset and compressed by ssh, so Claude can follow residuals. After a dropped link the stream resumes where it stopped |

Limits: `mu fetch` stops above 50 MB and asks. A relay bandwidth cap is left for the native agent.

### 6.4a First job layer: OpenSSH inside the relay tunnel

**Decided 2026-11-14.** The owner asked for the job layer to be built, and the design note's recommendation was
accepted as it stands. Revisit it if a second user or a second node needs more than this gives. The full design, with
its reasons and tests, is `docs/design/job-layer.md` (it arrives with the implementation, from branch `job-layer`).
In short:

- **No custom agent yet.** Transport, encryption, the node's identity and the workstation's credentials come from
  OpenSSH. `sshd` runs in the distro on `127.0.0.1` only, key-only, as the user `mu`, behind a `ForceCommand` gate that
  accepts μ's verbs and nothing else: no terminal, no port forwarding, no sftp. What μ itself writes is the part any
  agent would need: the job store, the runner, durable results, log offsets and run records.
- **Pairing is an exchange of two public keys.** The node's host key is pinned on the workstation, and each
  workstation has its own key, authorised on the node by root. These take the place of the earlier plan's "agent
  certificate pinned at pairing" and "per-workstation token". No secret travels; both sides print fingerprints to
  compare.
- **Upload is a git snapshot.** A snapshot commit of the working tree, made without touching the user's index or
  branch, is pushed to a bare repo on the node. One tree hash names every byte of input.
- **A job is a `systemd-run --user` unit** with memory and time limits, in its own cgroup. The queue is on the node,
  first in first out, **one job at a time**.
- **A job is finished only when a checksummed `result.json` is durably written** (temp file, fsync, rename). After
  any stop, clean or not, a job with no valid result is re-run once and then marked `interrupted`. This is the answer
  to S3's finding that a Windows restart can lose the last writes.
- **A missing pinned toolchain version is refused,** never installed as a side effect of a submit: a first install
  can take over an hour and runs the benchmark gate.
- **Relay path only.** The LAN-direct path is deferred (§6.3). **Fallback to `local` only when the node is
  unreachable,** never when it refuses.
- **Commands:** `mu compute add | list | status | remove` and `mu submit | status | log | fetch | cancel`, each with
  `--json`. The relay port for `office-pc` is 7001 (§7.3).
- **The native agent** (Stage B, §7.3) comes later, with the same verbs over its own transport: when several users,
  several nodes, remote viewing or a node without `sshd` make it necessary.

| Stage | Content | State (2026-11-14) |
|---|---|---|
| 1 | The commands above, the runner, limits, the queue, run records, refusal of a missing pin. Tested in CI, where the GitHub runner stands in for the distro (real systemd and `sshd`, with the chisel and Traefik pair from S4) | In progress on branch `job-layer` |
| 1b | The same on the office PC: one run of the host script (`sshd`, gate, tunnel service, `/srv/mu`), the two public keys exchanged, then `fea-cantilever` as the first job, compared with the local reference | Waits for stage 1 and the next office visit |
| 2 | Restart recovery proven on the PC, `--on auto` fallback, remote toolchain install, disk clean-up, submodule and LFS inputs, `fetch --all`, the cross-host check of §9 | Not started |
| 3 | LAN-direct path, a one-paste pairing code, keys in the Keychain, a job sandbox | When wanted |
| 4 | Native agent | When one of the needs above is real |

Limits of this design, stated plainly:
- **A paired workstation key can run any command as `mu` on the node, because that is what a job is.** The gate is
  a protocol boundary and an audit point, not a sandbox.
- `sshd` runs as root inside the distro. It listens on loopback only, with the relay credential in front of it.
- A job runs project code as `mu`. Through WSL's Windows interop and the `/mnt/c` mount it may also be able to act on
  Windows. This is an open security point, to be settled before real jobs (§11).

Not verified yet (each has a test in the design note): whether systemd user units get working memory limits on
this WSL version; whether Windows interop is reachable from a systemd user unit; how Ubuntu 24.04's socket-activated
`ssh` behaves with a loopback listen address; ssh connection sharing over a chisel `stdio` proxy command.

### 6.5 Claude Code integration (the primary interface)

mu is built **for Claude Code to drive**. Design rules (what is built of them is listed after the rules):

1. **Everything is a CLI command.**
   - Commands are non-interactive, return `--json` output and meaningful exit codes, and long jobs are asynchronous (`submit` → `status`/`log` → `fetch`).
   - Nothing requires a GUI.
2. **The mu Claude Code plugin** ships in each toolchain version. The `mu` repo is also the plugin marketplace.
   - **Skills:** `cad` (from text-to-cad), `mesh`, `fea`, `cfd`, `aero`, `slicing`, `cam`, `validation`, `report`.
     Each knows the mu commands and the hard-won rules: tet10 node reorder, consistent nodal loads, VLM thin-surface settings, singular peaks, and so on.
   - **Slash commands:**
     - `/mu:new` new project
     - `/mu:study` set up an analysis with benchmark and convergence steps
     - `/mu:validate` check the validation trail
     - `/mu:report` build the report
     - `/mu:compute` node status and management
   - **Hooks:**
     - **SessionStart** runs `mu context` and gives Claude: the pinned and installed mu version, which compute nodes are online, the project state, and outstanding validation gaps.
     - **PreToolUse (Bash)** warns when a solver (`ccx`, `simpleFoam`, `gmsh`, …) is called directly instead of through `mu run`/`submit`, so every run gets a record.
     - Optional **Stop** hook runs `mu check`. It flags reported numbers that have no run record behind them.
3. **The org policy is part of the project.**
   - The project's `CLAUDE.md` imports `@org/CLAUDE-org.md` from the org submodule, a path inside the repo.
   - Company rules (the validation policy) apply automatically and are versioned with the project.
4. **Claude can manage the infrastructure too:**
   - installing and upgrading mu on the laptop
   - pairing nodes, upgrading the office PC's toolchain remotely, checking the relay
   - running benchmarks and comparing results across hosts

   The steps that need a person are:
   - on a new Windows node: running the host script as an administrator (§6.1), adding the node's deploy key on
     GitHub, and exchanging the two public keys at pairing (§6.4a);
   - on the server: the relay's DNS record and its Dokploy service and domain (both done), and pasting new
     credentials into Dokploy when a relay client is added or rotated (§7.4).

   Relay code changes deploy from GitHub through Dokploy (§7).
5. **mu itself is developed with Claude Code.** The `mu` repo has its own `CLAUDE.md`, and the benchmarks are its test suite.

**Built so far (2026-11-14):**
- Every command that exists is non-interactive. `mu doctor`, `mu version` and `mu autoupdate` have `--json`.
- `mu link` connects a project to its pinned toolchain for Claude Code: `.mu/toolchain` (with
  `claude/CLAUDE-toolchain.md`, the instructions Claude reads) and `.claude/skills/cad`.
- Of the plugin, only the `cad` skill (from text-to-cad) exists, in `claude-plugin/skills/cad`.
- **Not built:** `plugin.json` and the marketplace, the other skills, the slash commands, the hooks, `mu context`,
  `mu check`, `mu new` and the remote management of nodes. The acceptance test of P2 (§13) has not been run.

### 6.6 The project: self-contained in its git folder

```
jatayu/                       (GitHub: martand-systems/jatayu)
  mu.toml                    [mu] version = "v2026.11.4" · later: [compute] policy, project metadata
  CLAUDE.md                   JATAYU context + `@org/CLAUDE-org.md`
  README.md
  .claude/settings.json       planned: enables the mu plugin at the pinned version; permission allowlist
  .claude/skills/  .mu/       links made by `mu link` (machine-local, gitignored)
  .gitmodules
  org/                        git submodule → martand-systems/mu-org-profile at a pinned commit
  docs/                       Phase 1 docx files, technical concept                     (git-lfs)
  frame/                      from projects/jatayu-frame: params.py, frame.py, fea_*.py, cfd/, mfg/, report
    results/                  JSON summaries                                            (git)
    img/  cad_out/            figures, STEP/STL                                         (git-lfs)
  runs/                       run records                                               (git)
  .scratch/                   local solver scratch                                      (ignored)
```

- `git clone --recursive martand-systems/jatayu` gives **everything project- and company-specific**. Nothing lives outside the folder.
- The only outside dependency is the mu application. If the pinned version isn't installed, `mu` stops and prints the
  command that installs it (a `mu.toml` pin works like an `.nvmrc`). `mu pin [V]` moves a project to another version.
- `mu new <name>` will create this structure, including the org submodule. Not built.
- `mu org update` will move the submodule to a newer org-profile commit, as a normal reviewed commit. Not built.
- The tree above is the target. This plan does not record how far the `jatayu` repo matches it (§12).

### 6.7 Run records

`runs/<run-id>.json` records:
- the project commit, plus a hash of any uncommitted changes
- the mu version and the hash of the lock files it was built from
- the host (`office-pc` / `local`) and hardware
- the command and parameters
- mesh sizes, wall time and key results
- the archive location of the raw output

**Rule:** numbers used in a report or design decision must come from a run whose commit is pushed.
`mu check` enforces this.

**Built so far:** nothing. The first run records are written by the job layer, at `mu fetch`, as
`runs/<job-id>.json` (§6.4a): the snapshot's commit and tree, whether the tree was dirty, the toolchain version that
ran, the node and its hardware, the command, limits and times, and each output with its SHA-256. Local `mu run`
writes no record yet, and `mu check` is not built.

---

## 7. Remote access: relay container on Martand's Docker server

**State (2026-11-14):** the relay is built (`relay/`), tested in CI behind replicas of Dokploy's Traefik, and deployed
on the server. A first check against the real server passed with both ends on the laptop. The office PC's end, and
the job layer that makes the tunnel private from the server, are not there yet. Measurements are in
`docs/verification/2026-11-13-S4-relay.md`; the deploy steps and client settings are in `relay/README.md`.

### 7.1 Constraints

| Fact about the server (2026-11-12) | Consequence for the design |
|---|---|
| `quarve-8gb-fsn1-2`, **195.201.236.190** (Hetzner, Falkenstein, 8 GB). A Dokploy **deploy server**: managed by the main Dokploy instance over SSH, and also running its own Dokploy-managed **Traefik**. 2 projects, ~12–15 containers already on it | The relay is **one more Dokploy service** (its own project). It doesn't change the host, Traefik's config or the other containers |
| Port 22 is used by Dokploy to manage the server; 80/443 belong to Traefik | The relay **publishes no host ports** and never touches host SSH. It's reached **through Traefik on 443** by subdomain |
| A `*.martand.systems` subdomain points to this server | `relay.martand.systems`. Traefik routes it to the container. TLS from Let's Encrypt via Dokploy's HTTPS switch (certificate valid from 2026-11-13) |
| Everything on the server must come from GitHub and be deployed with Docker | `relay/Dockerfile` and `relay/compose.yaml` live in the `mu` repo. **Dokploy pulls them from GitHub, builds and deploys on the server** (§7.4) |

### 7.2 How it works

- **Outbound only.** The office PC and the laptop both open **outbound WebSocket-over-TLS (`wss://`) connections on 443** to
  `relay.martand.systems`, the same kind of traffic as a web browser. It gets through home, hotel and office firewalls with no
  port forwarding anywhere. (Measured from one network so far; not yet from the office.)
- **Traefik does the TLS.** The server's Dokploy-managed Traefik terminates TLS and forwards the subdomain (including the WebSocket upgrade) to the
  `mu-relay` container on `dokploy-network` (container port 8080).
- **The relay only forwards** and stores nothing. Each client has its own credentials and one access rule: a compute
  node may open one reverse tunnel on its own loopback port inside the relay container, and a workstation may connect
  to the compute ports it was given, and to nothing else. So relay credentials are not a way into the server's other
  containers (tested in CI, and against the real server for the forward to Traefik's API).
- **Encryption, and who ends each layer:**

  | Layer | Ends at | Protects |
  |---|---|---|
  | TLS (Let's Encrypt) | Traefik on the server | the WebSocket from the networks in between |
  | chisel's SSH layer, relay fingerprint pinned by the clients | the relay container | the relay's client credentials. The relay sees what is inside |
  | OpenSSH, laptop ↔ node, host key pinned at pairing (§6.4a) | `sshd` in the distro | the job data. The relay and Traefik see only ciphertext |

  **The third layer does not exist until the job layer is on the PC.** In Stage A as deployed today the relay joins
  two plain TCP streams: the relay container, and anyone with root or Docker access on the server, can read whatever
  passes, and has the relay key and every client password. Until the job layer is there, nothing goes through the
  tunnel that the server's administrators may not read.
- **The pinned host key is also what identifies the node.** Someone holding the node's relay credentials can take
  its place on the relay while it is offline; without the node's host key the laptop refuses to talk to them.
- **On the office LAN** the laptop uses the relay too, for now (§6.3).
- **Claude and you never see the tunnel.** `mu compute add office-pc …` stores what the laptop needs, and every
  `mu submit/status/log/fetch` opens the tunnel itself (the laptop's chisel client runs as the proxy command of the ssh
  connection). Not built yet: §6.4a.

**Client settings and measured behaviour (S4).** Clients run chisel 1.12.0 with the relay's fingerprint pinned,
`--keepalive 25s`, `--max-retry-interval 15s` and `CHISEL_WS_TIMEOUT=10s`. With these, in the CI replica:

| Event | Outage |
|---|---|
| Relay redeployed, or Traefik restarted | the tunnel is back after about 13 s |
| A link that dies without a close (power, cable, NAT) | noticed after up to about 50 s |
| A compute node that dies uncleanly and is back within those 50 s | refused by its own stale session for about 60 s, then connected |
| 20 minutes with no traffic | no disconnect, on every Traefik version tested |

`mu` therefore allows about a minute after a network change or a node restart before it falls back to `local`.

**The relay can be made unavailable, though not broken into.** chisel keeps a connection that never logs in open for
ever. A Traefik middleware (`mu-relay-inflight@docker`, defined by labels in `relay/compose.yaml`) allows 32
connections at a time per client address (tested in the replica; the limit was not exercised on the real server). A
container on `dokploy-network`, or someone with hundreds of addresses, can still fill the relay's memory; Docker then
restarts it and the tunnels reconnect.

### 7.3 Stages

| Stage | Relay software in the container | Why |
|---|---|---|
| **A (deployed 2026-11-14)** | **chisel 1.12.0** (open source, MIT): TCP tunnelling over WebSocket, SSH-encrypted inside, per-client auth, reverse tunnels. The office PC runs `chisel client … R:127.0.0.1:7001:127.0.0.1:<sshd port>` (a reverse tunnel that ends *inside the container*, never on the host). The laptop's chisel client connects to that port. Each compute node has one port in 7001–7999; `office-pc` has 7001 | Proven, one small binary, made for running behind an HTTPS reverse proxy. Very little to write |
| **B (later: more users and nodes)** | **A native mu relay and agent**, same container slot and subdomain | Routing by node ID, per-user tokens, bandwidth quotas, audit log, a team view of nodes |

Moving from A to B changes only the relay's Dockerfile and the mu client. The subdomain and the proxy configuration stay
the same, and the job commands keep their verbs (§6.4a).

### 7.4 What lives on GitHub, and how Dokploy deploys it

The server is managed by **Dokploy** (§7.1). The relay is deployed the same way as the other services on it: a Dokploy
**Compose service whose source is the `mu` GitHub repo**. Traefik routing is added through Dokploy's **Domains** screen.
No manual SSH, no hand-edited Traefik config. The step-by-step version of this section is `relay/README.md` §2.

| Where | What |
|---|---|
| `mu/relay/` (git) | `Dockerfile`, `compose.yaml`, `entrypoint.sh`, `gen-credentials.sh`, `.env.example`, `README.md` (deploy steps) |
| Build | Dokploy builds `relay/Dockerfile` on the server: Alpine pinned by digest and the chisel release pinned by SHA-256, running as an unprivileged user |
| Dokploy project **`mu`** (separate from Vaulde, Quarve, MSIPL, …) | Compose service **`relay`**: source = GitHub `martand-systems/mu`, branch `main`, compose path `relay/compose.yaml`, **server = `quarve-8gb-fsn1-2` (195.201.236.190)** |
| Dokploy environment variables (on the service) | `RELAY_KEY` and `RELAY_CLIENTS`. Made on the laptop by `relay/gen-credentials.sh` (its state is `~/.config/mu/relay.env`, mode 600) and pasted into the Environment tab. **Never in git.** On the server they are in Dokploy's `relay/.env` and in the container's configuration |
| Deploys | Dokploy auto-deploy on push to `main`, with **Watch Paths `relay/**`** so that only relay changes redeploy it (there is no `release` branch). Each redeploy that replaces the container drops the tunnels for about 13 s. The owner deploys from the Dokploy UI; the Dokploy CLI is used read-only, and its detail calls return HTTP 400 |
| Changing clients | `gen-credentials.sh` with `add-client`, `rotate-client`, `remove-client` or `rotate-key`, then paste `env` into Dokploy again, Save, Deploy. Only `rotate-key` changes the fingerprint the clients pin |

**Domain setup** in Dokploy (Domains screen), on the `relay` compose service:

| Field | Value |
|---|---|
| Service Name | `mu-relay` |
| Host | `relay.martand.systems` |
| Path / Internal Path | `/` / `/` |
| Strip Path | off |
| Container Port | `8080` |
| Custom Entrypoint | off (uses `websecure`) |
| HTTPS | **on**, Certificate Provider Let's Encrypt |
| Middlewares | `mu-relay-inflight@docker` (the connection limit, §7.2) |

**DNS:** `martand.systems` uses **Porkbun** nameservers (not Cloudflare, so there are no proxy timeouts). The `A` record
`relay → 195.201.236.190` was added on 2026-11-12. It has to stay a plain record with no CDN or proxy in front: the
connection limit counts per client address as Traefik sees it.

Traefik in Dokploy passes WebSocket upgrades through with no extra setting (`101` over HTTP/1.1 in the replica; the
tunnel works on the real server).

Compose file, shortened; `relay/compose.yaml` is the real one. Dokploy adds the router labels and the `dokploy-network`
connection from the Domains settings, so the file has **no routers, no published ports and no volumes**. Its own
labels only define the connection-limit middleware:

```yaml
services:
  mu-relay:
    build: { context: ., dockerfile: Dockerfile }   # relay/ in the mu repo; Dokploy builds it on the server
    restart: unless-stopped
    environment:
      RELAY_KEY: "${RELAY_KEY:?…}"          # both set in Dokploy's Environment tab, not in git
      RELAY_CLIENTS: "${RELAY_CLIENTS:?…}"  # <role>:<name>:<password>:<port>, one entry per client
    expose: ["8080"]                        # internal only; Traefik reaches it on dokploy-network
    labels:
      - traefik.http.middlewares.mu-relay-inflight.inflightreq.amount=32
      # … counted per client address; an IPv6 /64 is one address
    user: "65532:65532"
    read_only: true
    cap_drop: [ALL]
    deploy:
      resources: { limits: { cpus: "0.5", memory: 256M, pids: 256 } }
    healthcheck:
      test: ["CMD", "wget", "-q", "-T", "3", "-O", "/dev/null", "http://127.0.0.1:8080/health"]
      interval: 30s
      start_period: 15s      # Traefik does not route to a container whose health is still "starting":
      start_interval: 1s     # without these two lines every deploy would answer 404 for 30 s
```

**Living alongside the other containers:**
- It's a separate Dokploy project, with no host ports and no volumes.
- It has CPU, memory and process caps (the server has 8 GB and already runs 12–15 containers).
- Deleting the Dokploy service removes it completely.
- Don't press *Traefik → Update* or *Reload* on that server for the relay: it restarts Traefik and drops every
  connection on the server for some seconds.

**Load:** tiny. Job transfers are small (§6.4), and the container idles between jobs. In the replica the relay used
8.5–9.3 MiB of its 256 MiB when idle and under 20 MiB while moving 100 MB each way.

### 7.5 Status of the relay (2026-11-14)

| Item | Status |
|---|---|
| Subdomain | **`relay.martand.systems`** (decided) |
| DNS provider | Porkbun. **Done:** `A` record `relay` → `195.201.236.190` (confirmed at the Porkbun nameservers and 1.1.1.1, 2026-11-12) |
| Dokploy GitHub app on `martand-systems` | **Installed**: provider `Dokploy-martand`, access to all repos |
| Relay code | **Done:** `relay/`, merged to `main` on 2026-11-13 after two reviews (16 findings, all fixed). CI run 43467864565 passed behind Traefik 3.6.7, 3.6.25 and 3.7.16 |
| Dokploy setup | **Done:** deployed by the owner in the Dokploy UI on 2026-11-14 (project `mu`, compose service `relay`, branch `main`, the domain with Let's Encrypt) |
| Check on the real server | **Passed, with both ends on the laptop** (2026-11-14): `/health` answers 200 with a Let's Encrypt certificate, both clients connect with the pinned fingerprint, 50 MB crossed unchanged, the three negative checks refused, 5 minutes idle with no disconnect |
| Still to prove on the real server | The office PC's end in WSL2; a second network (both ends shared the laptop's); idle beyond 5 minutes; a relay redeploy and a Traefik restart; credential rotation; which Traefik version the server runs |

**Fallback.** The first plan kept frp over WebSocket and a high port with its own certificate in reserve, in case
Traefik couldn't serve the subdomain. Traefik serves it, so neither is needed for that. They would only come back
if the office network turns out to block the path from the PC, which has not been tested.

---

## 8. Org profile repo: `martand-systems/mu-org-profile`

```
mu-org-profile/
  org.toml                 name, default mu version, default compute policy, relay URL, divisions
  CLAUDE-org.md            validation policy (benchmark → convergence → no singular peaks → hand checks
                           → stated assumptions → evidence in README), units, sign-off rules
  materials/               approved material data, each value with its source
  templates/report/        docx styles, logo, section skeleton
  templates/project/       Martand additions to the mu project template
  divisions/<name>/        optional overrides that inherit the company defaults
```

It's included in every project as the `org/` submodule. Updating it is a deliberate commit in each project.

**State (2026-11-14):** nothing in the `mu` repo depends on the org profile yet, and its state was not verified for
this version of the plan (§12, §13 P7).

---

## 9. Validation and reproducibility of the toolchain

- **Benchmark suite** (`mu/benchmarks/`, run by `mu test`). Six benchmarks. Each has known-answer checks, and a
  regression check against recorded reference values (`benchmarks/reference/`; the macOS values were recorded on the
  hand-built toolchain):

| Benchmark | Known-answer checks |
|---|---|
| `cad-solid` | A valid single solid: volume and bounding box to 1e-9, STEP round trip, STL and PNG written |
| `fea-cantilever` | Tip deflection within 1.5% of Euler-Bernoulli and Timoshenko, mid-span stress within 2% of Mc/I, reaction equals the load, tip change under 1% between the two finest meshes |
| `aero-vlm-wing` | VSPAERO CLα within 3% of an independent vortex-lattice solution |
| `cfd-cavity` | `checkMesh` passes, 400 cells, runs to the end time, continuity error below 1e-6, ParaView renders the result |
| `slice-print` | PrusaSlicer G-code with 30 layers and a filament mass within bounds |
| `cam-plate` | FreeCAD CAM G-code: deepest cut −6.000 mm, pocket floor present, never below the stock |

- **Installer gate (built):** every install, on every machine, runs `mu doctor` and the suite. A version that fails is
  not made the default. Results so far: laptop 6/6 (S1, v2026.11.6 on 2026-11-14); linux-64 in CI 6/6 on both Ubuntu
  images (S2); office PC 6/6 against the macOS reference (S3, v2026.11.4; and v2026.11.6).
- **Release gate (not built as a command):** the intent is that a tag is only made after the suite passes on fresh
  builds on both the office PC (linux-64 in WSL) and the laptop (macOS arm64). There is no `mu release` yet. What
  holds today is the installer gate and auto-update's rule below: a release that fails on a machine never becomes
  that machine's default.
- **Cross-host check (not yet run):** the JATAYU `L_final` hub model is run on `local` and `office-pc` and must agree
  to within < 0.5%. It needs the job layer (§6.4a, second stage). What has been compared so far is the benchmark
  suite: deterministic results are identical on linux-64 and macOS, and FEA differs by ≤ 0.02%, which is Gmsh's
  run-to-run mesh noise (S2).
- **Moving a project to a new version:** `mu pin [V]` changes the pin in `mu.toml` and relinks the project. It's
  never silent and never automatic. Re-running the project's validation scripts and diffing `results/*.json` after a
  pin change is still done by hand; a command for it is not built.
- **Toolchain updates (`mu autoupdate`, 2026-11-13):** once a day every machine looks for a newer `vYYYY.MM.N` tag at its
  source. It installs that tag the way `mu update` does, so the installer gate (doctor and benchmarks) applies, and
  makes it the default only if the gate passed. A release that fails keeps the old default, is reported in `mu`, and is
  tried at most 3 times. A newer tag resets the count. The run waits on battery (macOS), below `min_free_gb`, while an
  install is running, and while `state/busy` exists, and a check that waited is retried hourly. Only failures of the
  release count (its checks, a hang past `max_hours`, a stop mid-install); network, lock and missing-package problems
  don't. A failed release's installer never leaves its `mu` dispatcher behind. After a switch it removes old versions,
  keeping the previous default for rollback and every version a registered project pins. It never touches `mu.toml`:
  moving a project to a new version stays an explicit, reviewed `mu pin`. The schedule is an hourly LaunchAgent on
  macOS and an hourly systemd user timer on Linux; the run checks once a day. The installer turns it on by default,
  except in CI and with `--no-modify-path` or `--no-autoupdate`.
  It is turned on on both machines (2026-11-14). On the laptop it has not yet carried out an update: v2026.11.6 was
  installed with the installer, and the daily check has not run yet. Whether it has on the office PC was not read.
  Until it has updated a real machine, its behaviour is known from its tests (`tools/test-autoupdate`) only.
- **Version scheme:** `vYYYY.MM.patch`. The current release is **v2026.11.6** (resumable downloads, `mu autoupdate`,
  the Flathub download cache, the WSL disk check). The tag `v2026.11.5` was pushed by mistake on the v2026.11.4
  commit and is left in place, because published tags are not moved. It is not a real release: installing it gives
  v2026.11.4.

---

## 10. GitHub layout

**GitHub organisation:** [`martand-systems`](https://github.com/martand-systems), on the **Free plan** (checked
2026-11-12). `mu` and `jatayu` exist. `mu-org-profile` was not checked for this version of the plan.

| Repo | Visibility | Contents |
|---|---|---|
| `martand-systems/mu` | **Private** (decided). The Free plan works because no images are stored | Source, recipes and lock files, installers, relay, Claude Code plugin marketplace, benchmarks |
| `martand-systems/mu-org-profile` | Private | Org profile (submodule of each project) |
| `martand-systems/jatayu` | Private | Project (git-lfs). **Cleared for GitHub** |

**A release is just a git tag** (`vYYYY.MM.N`, currently `v2026.11.6`; §9) on `mu`. No binary assets and no container registry:
- `mu update` and `mu autoupdate` fetch the tag and build on the device (§5.3). A first install is a clone plus `./install.sh`.
- Dokploy builds the relay from `main`.
- One-click installer downloads attached to a GitHub Release (a `.pkg`, a Windows setup program) are not built (P8).

**Free-plan check:**

| Resource | Free allowance | mu's use |
|---|---|---|
| Repo storage | Generous for source | Text only (recipes, scripts, lock files) |
| GitHub Packages / GHCR | 500 MB | **Not used** |
| Actions minutes | 2,000 / month (macOS ×10) | Light: the relay test bed on relay changes; the Linux install and probe workflows only on demand. The heavy benchmark runs are done on the devices themselves (or later on self-hosted runners on the office PC and laptop) |
| Git LFS | 1 GB storage / 1 GB bandwidth per month | JATAYU binaries ≈ 20 MB today. Fine; buy data packs if it grows |

**Access from the devices** (`mu` is private):
- The laptop clones with your `gh` login.
- The office PC uses a **read-only deploy key** for `mu` only. The host script makes the key, and a repo admin adds it
  once (§6.1). The organisation setting that allows deploy keys was turned on for this (S3).
- Dokploy reads the repo through its GitHub app on `martand-systems` (provider `Dokploy-martand`, installed).

**CI (GitHub Actions)**, kept light. What exists:
- `s4-relay`: on every branch push that touches `relay/` or `tools/s4/`, the credential and entrypoint tests and the
  relay behind three replicas of Dokploy's Traefik. The long run (20 minutes idle, macOS credential tests) is started
  by hand before a chisel or Traefik version change.
- `linux-install` and `s2-probe`: the full Linux install and the per-tool probes, on demand.
- The job layer adds its own workflow (§6.4a).

Not built:
- lint and unit-test runs on each push, and a check that the lock files resolve;
- the **release checklist** (`mu release`): a fresh build plus the benchmarks on the office PC and on the laptop, the
  results committed to `benchmarks/results/<tag>.json`, then the tag;
- optionally, the office PC and the laptop as **self-hosted runners**, so this happens automatically on a tag without
  using hosted minutes.

---

## 11. Security

- **Data classification:** JATAYU design data is cleared for GitHub (confirmed 2026-11-12).
- **Relay** (Stage A, as deployed; tested in S4):
  - outbound-only connections over `wss://` 443 through the server's Traefik
  - per-client credentials and one access rule per client: relay credentials do not give access to the other
    containers on the server, to Traefik's API or to the internet
  - clients pin the relay's fingerprint and refuse a relay with another key
  - the container publishes no host ports, runs read-only as an unprivileged user with all capabilities dropped and
    with CPU, memory and process limits, and doesn't touch host SSH or the other containers; it stores nothing
  - deployed only by Dokploy from GitHub; no extra SSH keys or access to the server are created
  - credentials are never in git. On the laptop they are in `~/.config/mu/relay.env` (mode 600). On the server they
    are in Dokploy's `relay/.env` and the container's configuration, so whoever has root or Docker access there has
    them
- **What the relay does not give:**
  - **Secrecy from the server, until the job layer exists.** The relay container, and anyone with root or Docker
    access on the server, can read the tunnel's traffic (§7.2). End-to-end encryption between laptop and node starts
    when the job layer puts OpenSSH inside the tunnel. Until then nothing confidential goes through it.
  - **Proof of who is at the other end.** The holder of a node's relay credentials can take its place while it is
    offline. The node's pinned host key (§6.4a) is what proves identity.
  - **Protection against being knocked over.** Many idle connections can fill its memory and make it restart (§7.2).
    That exposes nothing, but remote compute is unavailable meanwhile and work falls back to `local`.
- **Compute node (job layer, §6.4a; designed, not on the PC yet):**
  - `sshd` listens on loopback only, key-only, for the user `mu`, who has no sudo; a forced command accepts only μ's
    verbs; forwarding, terminals and sftp are off; the authorised keys file is root-owned, so a job cannot give
    anyone access; a workstation's key can be revoked on the node
  - a paired workstation key can run any command as `mu`: that is what a job is. A job can read and write
    everything `mu` owns on the node (other jobs' data, the μ install, the read-only deploy key) and can open
    network connections from the PC, the office LAN included
  - `sshd` runs as root inside the distro, so a flaw in its login code would give root there. It is reachable only
    through the relay credential or from the PC itself, and gets Ubuntu's security updates
- **Open point, to settle before real jobs: Windows reach from a job.** The distro has Windows interop on and `C:`
  mounted at `/mnt/c`, and the WSL session is started by a scheduled task that runs elevated as the Windows user. So
  a Linux job may be able to run Windows programs as that user, and "no sudo" would then mean little. The design
  turns both off in the distro (`[interop] enabled=false`, `[automount] enabled=false` in `/etc/wsl.conf`) and tests
  it. One thing depends on interop today: μ's disk check on WSL reads the distro's Windows drive with `reg.exe`.
  With interop off it must fall back to the Linux figure without stopping an install or an update. The code has that
  path (S2 record); it has not been checked on the PC. Listed in §14.
- **Credentials:**
  - GitHub access on the laptop is the user's own `gh` login.
  - The compute node has **no GitHub write access**. It has a read-only deploy key for `mu`, to download releases,
    and no access to project repos: project files reach it only as job snapshots.
- **Licences:** a `THIRD_PARTY.md` listing each bundled solver's licence and source is still to be written. Avoid
  Docker Desktop (licensing).

---

## 12. Migrating today's folder

Nothing in the current folder is deleted until the new setup reproduces today's results.

The status column says what the `mu` repo's records show (2026-11-14). The `jatayu` and `mu-org-profile` repos and the
old folder were not inspected for it; "not verified here" marks those gaps.

| Step | Action | Touches current files? | Status |
|---|---|---|---|
| M1 | Freeze the baseline: `jatayu-frame/results/*.json`, the report docx, the run commands from `README.md` | Read only | Done: the results committed in `jatayu` are the baseline M5 compared against |
| M2 | Export exact package lists from `env/` and `tools/cam-venv` into lock files. Record app versions and checksums | Read only | Done: `toolchain/` (S1) |
| M3 | Move `felib.py` generics into `mu.fea`, docx styling into the `mu-org-profile` template, and the `CLAUDE.md` tool knowledge into plugin skills | Copies only | Partly. `mu.fea` exists (`src/mu/fea.py`). The tool knowledge is in `claude/CLAUDE-toolchain.md` and the `cad` skill; the other skills are not written. The docx template: not verified here |
| M4 | Create `jatayu` repo: docs, the frame files (without `work/`, `__pycache__`, possibly `old/`), `mu.toml`, `org/` submodule, a JATAYU-only `CLAUDE.md`, `.claude/settings.json`, LFS rules. Replace the `../../` paths | Copies only | The repo exists and runs with only `mu` on the PATH (M5). It pins μ v2026.11.4. The `org/` submodule and the rest of the list: not verified here |
| M5 | Re-run the README sequence through `mu` on `local` and `office-pc`, and compare with M1 | No | `local`: done on v2026.11.0, and μ reproduces the hand-built toolchain (`docs/verification/2026-11-13-S1-M5.md`). `office-pc`: not done; it needs the job layer |
| M6 | Archive the old `ENGINEERING/` folder. This later frees ~14 GB of `work/` and 4.7 GB of `.cache` | After M5 passes | Not verified here |

M5 found two committed JATAYU artefacts that don't match the current design inputs (the frame STEP and the leg-bracket
G-code). That is a JATAYU data issue, reported to the project, not a toolchain difference.

---

## 13. Phases and acceptance criteria

**Path to the first remote job** (the shortest list of what is left before a job runs on `office-pc`):

1. **Finish the job layer's first stage (P4).** In progress on branch `job-layer`; tested in CI, where the GitHub
   runner stands in for the distro. Needs nothing from the owner.
2. **Merge it and release it.** Both machines take the release (auto-update, or `mu update`).
3. **One visit to the office PC:**
   - run the host script once more: it adds `sshd` with its gate, the tunnel service and `/srv/mu` (P5, P6);
   - give the PC its relay credential, and authorise the laptop's public key; compare the fingerprints;
   - test whether a job can reach Windows through interop, and turn interop and the `/mnt/c` mount off (§11);
   - run the restart tests that are still open from S3: a baseline with the `mu-wsl-shutdown` service stopped, then
     restarts with it (`install/hosts/wsl2/README.md`).
4. **From the laptop:** `mu compute add office-pc …`, then `mu compute status office-pc`. This is also the check S4
   still lacks: the PC's end of the tunnel in WSL2, and a second network.
5. **The first real job:** `fea-cantilever` on `office-pc` through `mu submit`, compared with the local reference.

After that: the job layer's second stage (restart recovery proven on the PC, `auto` fallback, the cross-host check on
the JATAYU model), then P7.

| Phase | Work | Done when | Status (2026-11-14) |
|---|---|---|---|
| **P0 Prerequisites** | Private repos created in `martand-systems` (`mu`, `mu-org-profile`, `jatayu`); Porkbun `A` record for `relay.martand.systems`; Dokploy GitHub provider for the `mu` repo; first setup in the Dokploy UI; admin access to the office PC; hardware virtualisation on | All the §15 items answered | **Done.** `mu-org-profile`: not verified here |
| **P1 macOS full install** | `mu` repo, locks, `apps.toml`, wrappers, CLI (`run`, `test`, `doctor`, `context`), the rebuilt benchmarks, the installer, install to `~/Library/Mu` | A fresh install on the laptop passes all benchmarks offline. `mu` is on `PATH` from any folder | **Done** (S1, installer v2 record; v2026.11.6 runs on the laptop). `mu context` is not built; it moves to P2 |
| **P2 Claude Code plugin and project format** | Plugin (skills, commands, hooks), `mu new`, `mu.toml`, org submodule, run records, `mu check` | Claude Code in a fresh `mu new` project designs and analyses a test part end to end using only mu commands, with run records produced | **Partly.** Built: the `mu.toml` pin, `mu pin`, `mu link`, the `cad` skill, `claude/CLAUDE-toolchain.md`. Not built: the plugin manifest, the other skills, commands, hooks, `mu new`, `mu context`, `mu check`. Run records start with the job layer. The acceptance test has not been run |
| **P3 Linux compute recipe** | linux-64 locks and the Linux path of the installer, tested in fresh Ubuntu containers; `mu release` checklist; first tag | A fresh linux-64 build passes the benchmarks and matches the macOS results | **Done** (S2): one installer for macOS and Linux, 6/6 in CI on both Ubuntu images and on the office PC. The `mu release` checklist is not built (§9) |
| **P4 Job layer** (was "Agent and job protocol") | First stage of §6.4a: `compute add/list/status/remove`, `submit/status/log/fetch/cancel`, upload as a git snapshot, the runner and its queue, run records. Second stage: `auto` fallback, restart recovery on the PC | Works end to end in CI against a node on the GitHub runner, through the relay and Traefik pair from S4 | **In progress** on branch `job-layer` |
| **P5 Windows compute node** (was "installer") | The WSL2 host scripts: distro, startup task, `.wslconfig`, clean stop before shutdown; then the job layer's pieces on the node | One run on the office PC (plus one reboot) leaves a node that passes the benchmarks and survives a reboot with nobody logged in | **Mostly done.** The host scripts are on `main` and installed on the PC: every status check passes, benchmarks 6/6, 2 h 01 min after a reboot with nobody signed in (S3). Pending: the restart tests and the first Windows Update restart; `sshd`, the gate, the tunnel service and the authorised key (one run of the host script, with P4). Remote management commands are not built |
| **P6 Relay (Stage A)** | `relay/Dockerfile` (chisel) and `relay/compose.yaml`, built by Dokploy, Dokploy project `mu` with the `relay` service and domain on 195.201.236.190 (`relay.martand.systems`, HTTPS); chisel client on the node and in the mu CLI; pairing | The laptop runs jobs on `office-pc` **from home**. With the PC off, jobs fall back to `local` without errors | **Relay deployed** (2026-11-14) and checked from the laptop (§7.5). Pending with P4: the tunnel clients and pairing. The LAN-direct path is deferred (§6.3). The acceptance test has not been run |
| **P7 Org profile and JATAYU migration** | `mu-org-profile`, `jatayu` repo, steps M1–M6 | JATAYU frame results reproduce the baseline on both nodes within tolerance. The old folder is archived | **Partly** (§12). The `jatayu` repo exists and pins v2026.11.4; the results reproduce on `local` (M5). On `office-pc`: not done. `mu-org-profile` and M6: not verified here |
| **P8 Hardening** | Relay Stage B and the native agent if needed, `pvserver` remote viewing, signed `.pkg` and Windows installer, uninstallers | Remote viewing works from home | **Not started.** Two of its first items are already done in S2: OpenVSP and FreeCAD CAM run headless on linux-64, and all six benchmarks pass on both machines |

---

## 14. Risks

| Risk | Mitigation, and what is still unproven |
|---|---|
| Upstream download sites change or disappear (apps in `apps.toml`) | Hash-pinned URLs fail loudly, and every machine keeps its downloads in a cache. A mirror of the app downloads on the office PC or the Dokploy server is not set up. On Linux, PrusaSlicer depends on Flathub keeping the pinned commit (S2); conda-forge packages are permanent |
| The WSL2 node doesn't stay up, or loses data when Windows restarts | **S3 passed:** 2 h 01 min after a reboot with nobody signed in. Residual: a Windows restart powers the VM off without a Linux shutdown. Mitigated by the `mu-wsl-shutdown` preshutdown service, faster write-back and durable job results (§6.1, §6.4a); `test-stop` on the PC is clean. **Unproven:** the restart tests on the PC, the first real Windows Update restart, and a WSL update (the 5-minute watchdog should bring the distro back) |
| Power cuts and crashes on the node give no shutdown notice | Only fsync protects data then. The job layer writes results durably and re-runs an interrupted job once. Whether the PC powers on again by itself after a power cut was not checked |
| A job is interrupted by every Windows restart, clean or not | The runner re-runs it once from its immutable snapshot, then marks it `interrupted` (§6.4a). Proven on the PC only in the job layer's second stage |
| The relay can read the tunnel's traffic, until the job layer exists | Nothing confidential goes through the tunnel before OpenSSH runs inside it (§7.2, §11). From then on the relay and Traefik see only ciphertext |
| The relay is made unavailable: many idle connections, a redeploy, a Traefik restart, the server down | A limit of 32 connections per client address through Traefik; clients reconnect by themselves (about 13 s after a redeploy or a Traefik restart). A neighbouring container or an attacker with hundreds of addresses can still knock it over. With the relay down there is no remote compute, in the office too, because there is no LAN-direct path yet: work falls back to `local` |
| Relay tunnel drops on flaky links | 25 s keepalives, automatic reconnect, and jobs keep running on the PC during a drop. Measured in the replica; on real networks only 5 minutes idle on one network so far (§7.5) |
| The office network blocks the PC's outbound `wss://` | Not tested yet: the PC's end of the tunnel is the remaining S4 check. Fallbacks: frp over WebSocket, a high port |
| The 8 GB server is shared with ~12–15 containers | The relay is capped at 0.5 CPU and 256 MB, uses about 9 MiB idle (replica) and moves only small transfers |
| The relay affects other containers on the server | No host ports, its own compose project, resource limits, read-only container; its clients can reach nothing but their own tunnel ports (tested) |
| A job reaches Windows from inside the distro (WSL interop, `/mnt/c`), as the user of the elevated startup task | **Open.** Test it, then turn interop and the automount off in the distro before real jobs (§11). μ's WSL disk check uses `reg.exe` through interop and must then fall back gracefully; that path exists in the code and is not yet checked on the PC |
| A paired workstation key can run any command as `mu` on the node | Accepted for one owner and one node: it is what a job is. Keys are per workstation, root-owned on the node and revocable; `mu` has no sudo. Finer rights come with the native agent |
| Admin work on the office PC needs a person at the PC | Until the job layer is there, nothing reaches the PC from outside, and even then root steps go through the host script at the PC. Steps are collected for one visit (§13) |
| A bad release reaches both machines through auto-update | A release becomes a machine's default only if `mu doctor` and the benchmarks pass there; the previous default is kept for rollback; projects keep their pins. Auto-update has not yet carried out an update on a real machine (§9) |
| arm64 vs x86_64 numerical differences | Measured on the benchmarks (S2, S3): identical where the tools are deterministic, FEA within 0.02%. The cross-host check on the JATAYU model is not yet run; the host is recorded in every run |
| Two compute builds to keep in step | Same recipe, same benchmark gate, one installer |
| MPI-parallel OpenFOAM hangs on the Mac (seen during JATAYU work), so the laptop's CFD is serial only | Local CFD is for small or coarse cases. Parallel CFD goes to `office-pc` (2-rank MPI ran in S2 on linux-64) |
| Claude bypasses mu and runs solvers directly (no run record) | Today only the instructions in `claude/CLAUDE-toolchain.md`. The PreToolUse hook and `mu check` are not built |
| Disk use per toolchain version | Layers are shared: a release with unchanged locks costs a few MB. `mu gc` removes versions and layers nothing uses, and auto-update keeps the default, the previous one and pinned versions. The office PC's store was 19.0 GB at v2026.11.4 (S3) |

**Retired by the spikes:**
- "OpenVSP and FreeCAD headless Linux builds are hard to package": every tool runs headless on linux-64 (S2).
- "Traefik can't route the new subdomain": it does, with a Let's Encrypt certificate (S4, on the real server).
- "WSL may not run with nobody logged in": it does (S3). What is left of that risk is the first row about restarts.

---

## 15. Decisions

**Settled:**

| Decision | Value |
|---|---|
| Company / GitHub org | Martand Defense / `martand-systems` (Free plan) |
| Repo visibility | **All private** |
| Image registry | None. Toolchains are built on each device from locked recipes; Dokploy builds the relay |
| Remote access | Own relay on 195.201.236.190 (Dokploy + Traefik) at `relay.martand.systems`; no Tailscale |
| Dokploy ↔ GitHub | `Dokploy-martand` GitHub app installed on the org |
| mu install | System-wide like VS Code (macOS: `~/Library/Mu` + `mu` on PATH) |
| Compute hardware | Hardware-agnostic. Any Windows 11 PC (later Linux) can be a node (§6.1a) |
| First nodes | Laptop (`full`, macOS arm64, M1) and office PC (`compute`, Windows 11, Intel Core Ultra 7 265K and NVIDIA RTX 5060; §4) |
| JATAYU data on GitHub | Allowed |

**Also settled (2026-11-12):**

| Decision | Value |
|---|---|
| Product name | **μ** / `mu` (CLI `mu`, repo `martand-systems/mu`, icon μ) |
| DNS | `relay.martand.systems` → 195.201.236.190 (done) |
| Relay setup | Martand does it in the Dokploy UI (done 2026-11-14) |
| Compute architecture | ADR-001: Linux compute; WSL2 is the first Windows host adapter |
| Shared libraries | Default: `felib.py` generics → `mu.fea`, docx styling → `mu-org-profile` (M3) |
| JATAYU | Existing work moves into `martand-systems/jatayu` now, ahead of P7, using the current toolchain until mu P1 lands |

**Settled by the spikes and the work since (2026-11-13 and 2026-11-14):**

| Decision | Value |
|---|---|
| Always-on Windows node | Plain WSL2 with systemd, a startup task under S4U, `instanceIdleTimeout=-1`, ARSO and Fast Startup off. No Windows service wrapper and no `MuComputeSetup.exe`; the node is set up by the host scripts in `install/hosts/wsl2/` (S3) |
| Clean stop | A preshutdown service stops WSL before Windows shuts down; μ's own records are written durably and an interrupted job is re-run, not trusted (S3) |
| Installer | One installer for macOS and Linux (`install/install.sh`); on by default: daily auto-update that switches only if the checks pass |
| Linux tool routes | As in §5.1. Nothing is `local`-only (S2) |
| Relay software | chisel 1.12.0 behind Dokploy's Traefik, no Traefik setting changed, no host port (S4) |
| Relay deploys | From `main`, with Watch Paths `relay/**`. No `release` branch |
| Relay credentials | Made by `relay/gen-credentials.sh` on the laptop, pasted into Dokploy by the owner. A `mu relay` command may wrap this later |
| Relay ports | One port per compute node in 7001–7999; `office-pc` is 7001 |
| **First job layer** | **OpenSSH inside the relay tunnel** (§6.4a), not a custom agent yet. **Decided 2026-11-14**: the owner asked for the job layer to be built and the design's recommendation was accepted by default. Revisit if a second user or a second node needs more |
| Upload | A git snapshot pushed to a bare repo on the node, not rsync |
| Jobs | One at a time, first in first out, each a `systemd-run --user` unit with memory and time limits; finished only when a checksummed `result.json` is durably written |
| Missing toolchain version on the node | Refused, not installed automatically |
| LAN-direct path | Deferred; the relay path is used in the office too |
| Fallback to `local` | Only when the node is unreachable, never when it refuses |
| Native agent and relay Stage B | Later, with the same verbs, when several users or nodes need them |
| Releases | `vYYYY.MM.N` tags. `v2026.11.5` is a mistaken tag on the v2026.11.4 commit, left in place; the release after v2026.11.4 is v2026.11.6 |

**Note on the name:** `mu` is also the name of an unrelated email tool (maildir-utils). If it were ever installed, the two CLIs
would collide. mu's installer checks for this and warns.

---

## 16. Spikes and readiness

**How many spikes are there, and is the job layer "S5"?** There are four spikes, S1 to S4, and no S5. A spike was
a short test of an assumption that could have changed the design. All four have run. None changed the
architecture; they changed details, which are now in §6.1 and §7. What remains is building, and it is counted in phases (§13), not spikes: the job layer is **P4**, the
node's side of it is the rest of **P5** and **P6**, and then comes the first real job. The steps are listed at the
top of §13.

| Spike | Tested | Result | Record in `docs/verification/` |
|---|---|---|---|
| S1 Fresh macOS build from the recipe | Today's hand-built setup can be reproduced from a recipe | **Passed.** Fresh clean-environment installs pass `mu doctor` and 6/6 benchmarks, and reproduce the hand-built toolchain on the JATAYU frame analysis (M5) | `2026-11-13-S1-M5.md`, `2026-11-13-installer-v2.md` |
| S2 linux-64 provisioning on Ubuntu 24.04 | Every tool has a working headless Linux route | **Passed.** Every tool, same versions as macOS; deterministic results identical, FEA within 0.02%. Nothing is `local`-only | `2026-11-13-S2-linux.md` |
| S3 WSL2 on the office PC | The always-on compute node is feasible | **Passed.** The distro ran 2 h 01 min after a reboot with nobody signed in; no Windows service wrapper is needed. Found: a Windows restart stops Linux hard. The host scripts add a clean stop; `test-stop` passes on the PC. **Open:** the restart tests and the first Windows Update restart | `2026-11-13-S3-wsl.md` |
| S4 chisel through Dokploy's Traefik on `relay.martand.systems` | The relay design works with this Traefik | **Passes; the PC end is the remaining check.** Passed in CI behind replicas of three Traefik versions, then deployed, and passed against the real server with both ends on the laptop (2026-11-14). **Open:** the PC's end in WSL2, a second network, idle beyond 5 minutes, a redeploy and a Traefik restart, credential rotation | `2026-11-13-S4-relay.md` |

The open items of S3 and S4 are not new spikes. They are checks that need the office PC. Most are done on the next
visit, together with the job layer's set-up (§13); the first Windows Update restart is checked when it happens.

**Where implementation stands (2026-11-14):** P0, P1 and P3 are done. P5 is mostly done and the relay of P6 is
deployed. P4 is in progress. P2 is partly built, and P7 has its `local` half. The status column in §13 has the detail.

**Known issues** queued for the next release are listed in `README.md`.

---

## 17. Later additions (not in scope yet)

- Windows and Linux workstations
- Linux-server compute installer (`docker compose build` with the P3 recipe)
- GPU-accelerated solvers and rendering on the office PC
- Several compute nodes and team members (relay Stage B and the native agent, §6.4a)
- A LAN-direct path to a node in the same office (a Windows port proxy and a firewall rule limited to the local subnet)
- Self-hosted GitHub Actions runner on the office PC, to re-run validations on pull requests
- Remote ParaView (`pvserver`) through the relay
