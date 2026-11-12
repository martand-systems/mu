# μ (mu): Martand engineering toolchain. Plan

- **Status:** v5, in implementation
- **Date:** 2026-11-12
- **Owner:** Martand Defense
- **First project:** JATAYU
- **Name:** μ, written `mu` wherever only ASCII works (CLI, repo, files). Icon: the μ glyph.

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
| VS Code / Microsoft Word, the application | **mu**: the toolchain. Installed once per machine, company-agnostic | `mu v2026.11.0` |
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
| **Compute node** | A machine running the mu agent and solver stack: remote (`office-pc`) or `local` |
| **Agent** | A service on a compute node. It runs jobs, streams logs and returns results |
| **Relay** | A small service on Martand's server that connects workstations to compute nodes from any network |
| **Org profile** | A company repo (validation policy, templates, materials). Included in each project as a pinned git submodule |
| **Project** | A self-contained git repo for one product or study |
| **Run record** | `runs/<run-id>.json`: the provenance of every solver run |

---

## 3. Current state (as of 2026-11-12)

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
| **Office PC** (`office-pc`) | Ryzen 9, 64 GB RAM, current-generation NVIDIA GPU, M.2 NVMe | Windows 11, x86_64 | **`compute`**: Linux solver stack in WSL2 |
| **Relay server** (`relay`) | `quarve-8gb-fsn1-2`, 195.201.236.190 (Hetzner, 8 GB). A Dokploy deploy server with its own Traefik; ~12–15 containers already running | Linux + Docker (Dokploy) | **`relay`**: one Dokploy compose service behind the server's Traefik, at `relay.martand.systems`. Connects the laptop and the office PC from any network. Stores nothing |

```
     Laptop (anywhere)            195.201.236.190 (Dokploy)                   Office PC (always on)
 ┌─────────────────────────────┐   ┌───────────────────────────┐   ┌───────────────────────────────┐
 │ mu FULL (macOS arm64)      │   │ Traefik (Dokploy-managed) │   │ Windows 11                    │
 │  Claude Code + mu plugin   │wss│  :443 (shared, TLS)       │wss│  WSL2 distro "mu-compute"    │
 │  project git folders        │──►│  relay.martand.systems    │◄──│   ├ mu agent (systemd)       │
 │  viewers                    │443│        │ docker network   │443│   ├ solver stack (linux-64)   │
 │  local compute (fallback)   │   │  [mu-relay container]    │   │   └ /srv/mu/{jobs,archive}   │
 └──────────────┬──────────────┘   │  forwards only, no ports  │   └───────────────────────────────┘
                │                  │  published, stores nothing│
                │                  └───────────────────────────┘
                │ (same office LAN: direct connection, relay skipped)          ▲
                ▼ git push/pull                                                │ release downloads
        GitHub: mu (source, plugin) · mu-org-profile · jatayu ─────────────┘
```

**Both the laptop and the office PC only make outbound connections.** No ports are opened on the office
network, and no VPN software is installed on the laptop.

---

## 5. mu as an installed application

### 5.1 Install locations (like VS Code)

**macOS (laptop):**

| What | Where |
|---|---|
| CLI on `PATH` | `~/.local/bin/mu` (symlink; the installer adds `~/.local/bin` to the shell profile if needed) |
| Toolchain versions (side by side) | `~/Library/Mu/toolchains/<tag>/` (recipe snapshot, wrappers, plugin, links to layers) |
| Shared layers (immutable, content-addressed) | `~/Library/Mu/store/`: `env-<hash>`, `cam-<hash>`, `app-<name>-<sha>` |
| Machine config | `~/Library/Mu/config.toml` (paired nodes, relay URL, defaults) |
| Secrets (pairing keys, tokens) | macOS Keychain |
| Caches and downloads | `~/Library/Caches/Mu/` (safe to delete) |
| Local compute workspaces | `~/Library/Mu/local-compute/` |

- The bundled ParaView, FreeCAD, PrusaSlicer, OpenVSP and OpenFOAM go into **μ's store**, not `/Applications`,
  so they never clash with copies you install yourself, and several mu versions can coexist.
- **Layers (v2026.11.3).** Each environment and app is a read-only layer named by the hash of everything that
  goes into it: lock files, pins, the archives it depends on, and a layer-format version. A toolchain version
  is a recipe snapshot plus symlinks to its layers. A release that doesn't change the locks reuses every layer:
  it installs in about a second and costs a few MB. `mu gc` removes layers no installed version references.
- Uninstall a version: `mu remove <version>`. Remove everything: delete the folders above.

**Windows (office PC, compute role):**

| What | Where |
|---|---|
| Installer and service scripts | `C:\Program Files\Mu Compute\` |
| WSL distro disk | `<NVMe drive>:\Mu\wsl\mu-compute\` |
| Startup task | Task Scheduler entry |
| Agent config | Inside the distro |

**Linux (later):** `/opt/mu` plus `/usr/local/bin/mu`, or a container built on the server with `docker compose build` (§5.3).

**The current `CLAUDE.md` folder rule** ("never write outside this folder") **is replaced** for mu once it's installed:
mu owns the locations above, and projects own their git folders. The rule stays in force for the current
folder until migration is finished.

### 5.2 Install roles

| Role | Contents | Phase 1 machine |
|---|---|---|
| `workstation` | `mu` CLI, Claude Code plugin, light CAD env (build123d, OCP, render, text-to-cad viewer), ParaView GUI, git, gh, git-lfs | (included in `full`) |
| `compute` | mu agent and full solver stack (Gmsh, CalculiX, OpenFOAM, pvbatch/pvserver, VSPAERO, PrusaSlicer, FreeCAD CAM + cam env) | Office PC |
| `full` | Both, plus a built-in `local` node | Laptop |
| `relay` | One Docker container, **built and deployed by Dokploy** from `relay/` in the GitHub repo | 195.201.236.190 |

### 5.3 Built on the device from one locked recipe (no image registry)

mu ships **recipes, not binaries.** Every device builds its own toolchain from the same pinned inputs in the `mu` repo:

- **Lock files:** conda-lock records the exact URL and SHA-256 of every package, for osx-arm64 and linux-64.
- **`apps.toml`:** the exact URL and SHA-256 of each upstream app download.
- **`pins.toml`:** exact git commits.
- **Provisioning scripts:** one per platform.

| Target | How it's built on the device |
|---|---|
| **Windows 11 (office PC)** | The installer creates a WSL2 distro from the official Ubuntu 24.04 WSL image, then runs `toolchain/provision-linux.sh` inside it |
| **macOS arm64 (laptop)** | `install-mac.sh` runs `toolchain/provision-macos.sh` into `~/Library/Mu/toolchains/<tag>/` |
| **Linux server (later)** | `compute/compose.yaml` with `build:`, where the Dockerfile simply runs the same `provision-linux.sh`. Built with `docker compose build` on the server |
| **Relay (Dokploy server)** | Dokploy builds `relay/Dockerfile` on the server from the GitHub repo. It's tiny (one static binary) |

**Same recipe, same packages.** Because every package and download is pinned by hash, two devices that build the same tag get the same versions and builds.
The **benchmark suite runs at the end of every install** (§9). An install that doesn't reproduce the reference answers isn't marked ready.

**Trade-offs of building on the device instead of shipping images:**

| | Build on device (chosen) | Prebuilt image in a registry |
|---|---|---|
| GitHub storage and cost | None beyond the repo | Several GB per version (doesn't fit the Free plan's 500 MB) |
| Install time | ~20–40 min first time (downloads ~6–8 GB from conda-forge and upstream sites) | ~10 min download |
| Upgrades | Only changed packages are re-downloaded (shared package cache) | Whole image layers |
| Depends on upstream sites staying up | Yes. Mitigated by the local package cache, and optionally a cache of `apps.toml` downloads on the office PC or Dokploy server | No |
| Byte-for-byte identical binaries | Same hashes for every package, so effectively yes. The benchmarks prove equivalence | Yes |

### 5.3a ADR-001: compute is Linux; on Windows it runs in WSL2 behind a host adapter

**Status:** accepted, 2026-11-12. This is the decision that's hardest to reverse, so it's written down with its reasoning.

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
   - The agent, the job protocol, `provision-linux.sh` and the benchmarks know nothing about WSL. They only see "a Linux userland with N cores, M GB, an optional GPU".
   - Only the Windows installer and the start/stop/health hooks are WSL-specific (`install/hosts/wsl2/`).
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
| WSL may not run with nobody logged in | systemd plus a startup task; the agent's health is reported to workstations. **Spike S3 tests this first.** If it fails, switch to the Hyper-V adapter, with no change elsewhere |
| Default memory cap of 50% of RAM | `.wslconfig` calculated from detected RAM |
| Slow access to Windows files (`/mnt/c`) | Never used: all workspaces live on the WSL ext4 disk |
| Windows or WSL updates restart the VM | The agent restarts automatically; interrupted jobs are marked and can be resumed or re-run |
| Locked-down corporate PCs or cloud VMs without nested virtualisation | Those nodes use the Hyper-V or native-Linux adapter |

### 5.4 The `mu` source repo

```
mu/                                  (GitHub: martand-systems/mu; also a Claude Code plugin marketplace)
  CLAUDE.md                           instructions for developing mu itself with Claude Code
  install/
    install-mac.sh                    full / workstation on macOS (non-interactive; Claude can run it)
    install-compute-windows.ps1       compute on Windows 11 (wrapped as MuComputeSetup.exe)
    hosts/                            host adapters (ADR-001): wsl2/ (first), hyperv-vm/ (fallback), linux-native/ (later)
    install-compute-linux.sh          later
  toolchain/
    env.yml  conda-lock.yml           osx-arm64 + linux-64 exact pins
    cam-env.yml  cam-lock.yml         FreeCAD CAM env (OCP 7.9, Python 3.11)
    apps.toml                         per-platform app version / URL / SHA-256
    pins.toml                         git deps: cadquery@b3f9afd, text-to-cad@8ea2434
    patches/  pip-stubs/              one-OCP-per-env fixes
  compute/  Dockerfile  wsl/  mu-agent.service
  relay/    Dockerfile, compose.yaml (Dokploy compose service), .env.example, README
  src/mu/
    cli/        new, clone, run, submit, status, log, fetch, compute, doctor, test, upgrade, context, check
    agent/      job API, queue, workspaces, log streaming, result packing
    transport/  direct (LAN) and relay connections, pairing, end-to-end encryption
    fea/        C3D10 .inp writer, gmsh tet10 reorder, read_frd (from felib.py)
    post/       render, plots
  wrappers/                           gmsh, vsp, vspaero, pvbatch, openfoam, prusa-slicer, cam-python, …
  claude-plugin/                      the mu Claude Code plugin (§6.5)
    .claude-plugin/plugin.json
    skills/  commands/  hooks/
  benchmarks/                         fea-cantilever, aero-vlm-wing, cfd-cavity, slice-smoke, cam-smoke
  templates/project/                  skeleton for `mu new`
  .github/workflows/                  build, benchmark, release
```

---

## 6. Components in detail

### 6.1 Compute node on Windows 11 (office PC)

**Install:** run `MuComputeSetup.exe` as administrator. **This is the only step that needs a person at the PC**, once.
After that, Claude Code on the laptop manages the node remotely (`mu compute status | upgrade | restart | logs`).

The installer:
1. Checks the prerequisites: Windows 11, AMD SVM enabled in the BIOS, free space on the NVMe drive, NVIDIA driver (for later).
2. Enables **WSL2**. One reboot the first time; the installer resumes afterwards.
3. Creates the distro `mu-compute` from the official Ubuntu 24.04 WSL image, on the NVMe drive. Clones `martand-systems/mu` at the release tag, then runs `toolchain/provision-linux.sh`: micromamba, the linux-64 lock files, the `apps.toml` downloads (hash-checked), patches. This takes about 20–40 min the first time.
4. Writes `.wslconfig`:
   - `memory=` total RAM minus 8 GB for Windows, calculated on the machine (the WSL default is only 50% of RAM)
   - `processors=<all>`, `swap=16GB`
   - `vmIdleTimeout=-1`
5. Enables systemd in the distro. The **mu agent** runs as a systemd service.
6. Adds a **Task Scheduler task at system startup** that boots the distro, so the agent runs after reboots with nobody logged in.
7. Configures the agent to **connect out to the relay** (§7). No inbound firewall rules are needed.
   For direct LAN use it also listens on the LAN interface (firewall rule limited to the local subnet).
8. Runs `mu test` (the benchmark suite). **If it fails, the agent refuses jobs.**
9. Prints a **pairing code** for the workstation.

**Machine settings:**
- Power plan never sleeps.
- BIOS restores power after a power cut.
- Windows Update automatic restart is allowed; the agent comes back through the startup task.

**Performance:**
- Workspaces are on the WSL ext4 disk (`/srv/mu/jobs`), never on `/mnt/c`.
- MPI ranks and OpenMP threads are set from the physical core count.

**GPU:** CUDA works in WSL2 through the Windows driver. Phase 1 solvers use the CPU only. The GPU is for ParaView EGL rendering, and later GPU solvers or surrogate models.

### 6.1a Any machine can be a compute node (hardware-agnostic)

The office PC is only the first node. **Nothing in mu is tied to a particular CPU, GPU or amount of RAM.**
- **Detected at install, and re-checked every time the agent starts:**
  - CPU vendor, physical and logical cores
  - RAM, free disk on the workspace drive
  - GPU vendor and model, VRAM, whether CUDA is available
  - OS and architecture

  The agent reports these as its **capabilities** to paired workstations (`mu compute list` shows them).
- **Settings come from the detected hardware, never from fixed numbers:**
  - WSL memory (total minus a Windows reserve) and processors (all)
  - MPI ranks (physical cores), OpenMP threads, Gmsh threads
  - the memory each job may use
- **Scheduling uses capabilities.** A job declares an estimated memory and core need (mu estimates it from mesh size).
  `auto` picks the first preferred node that can run it. A GPU-only job (later) goes only to nodes reporting CUDA.
- **The GPU is optional.** A node without one is still a full compute node.
- **Adding a node** is the same on any Windows 11 or (later) Linux machine: run the installer, pair it, done.
  Several nodes can be paired. Their names come from pairing (`office-pc`, `lab-01`, …).

### 6.2 Full install on the laptop (macOS arm64)

- **Install:** download and run `Mu-<tag>.pkg`, or `install-mac.sh --role full` (non-interactive, so Claude Code can run it).
- Builds the toolchain from the locks into `~/Library/Mu/toolchains/<tag>/`, then runs `mu test` on the `local` node.
- `mu compute add office-pc <pairing-code>` pairs the laptop with the office PC through the relay.
- **"Workstation mode" isn't a separate install.** It's the `full` install sending compute to `office-pc`.

### 6.3 Choosing compute and falling back

In the project's `mu.toml`:

```toml
[compute]
policy   = "auto"                  # auto | remote | local
prefer   = ["office-pc", "local"]
local_only = ["cad", "render", "checks", "report"]
max_local_mem = "auto"            # default: 75% of this machine's RAM (12 GB on the 16 GB laptop)
```

| Situation | Behaviour |
|---|---|
| Laptop on the office LAN | Direct connection to `office-pc`, relay skipped |
| Laptop elsewhere, office PC online | Through the relay |
| Office PC or relay unreachable | **Falls back to `local`**, says so, and records `host=local` |
| Job too big for local and the office PC is unreachable | Stops with options: queue it (`--when-available office-pc`, held on the laptop), or force local at a coarser mesh |
| Link lost mid-job | The job keeps running on the PC. `mu status` / `mu fetch` resume when the link returns |
| Override | `mu run --on local …` / `--on office-pc …` |

**Continuity guarantee:** project files, git, Claude Code and all light tasks are always on the laptop.
Losing the office PC or the relay slows down big runs but never blocks work.

### 6.4 Job protocol and data volume

The link is designed for a **small amount of data**:

| Direction | What crosses the link |
|---|---|
| Up | The project working tree, content-addressed (unchanged files never re-sent), excluding gitignored files, `.scratch/` and LFS binaries the job doesn't need. A typical iteration is kilobytes to a few MB (scripts, params, STEP) |
| Down | **Declared outputs only:** results JSON, logs, PNG plots, small VTK extracts. Typically < 10 MB per job |
| Not sent by default | Raw output (meshes, `.frd`, OpenFOAM fields). It stays on the office PC in `/srv/mu/archive/<run-id>/`. Available with `mu fetch --all` (warns about size) or viewed remotely later |
| Logs | Streamed live and compressed, so Claude can follow residuals |

Limits are set in the machine config, for example a warning above 50 MB per transfer, with a relay bandwidth cap.

### 6.5 Claude Code integration (the primary interface)

mu is built **for Claude Code to drive**. Design rules:

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

   The only steps that need a person are:
   - the first Windows install (admin plus a reboot);
   - adding the relay's DNS record and creating the Dokploy service and domain, the first time (or Claude does it with the Dokploy CLI once its detail calls work).

   After that, relay updates deploy automatically from GitHub through Dokploy (§7).
5. **mu itself is developed with Claude Code.** The `mu` repo has its own `CLAUDE.md`, the benchmarks are its test suite, and CI gates releases.

### 6.6 The project: self-contained in its git folder

```
jatayu/                       (GitHub: martand-systems/jatayu)
  mu.toml                    mu = "v2026.11.0" · [compute] policy · project metadata
  CLAUDE.md                   JATAYU context + `@org/CLAUDE-org.md`
  README.md
  .claude/settings.json       enables the mu plugin at the pinned version; permission allowlist (mu, git, gh read-only)
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
- The only outside dependency is the mu application. If the pinned version isn't installed, `mu` offers to install it (a `mu.toml` pin works like an `.nvmrc`).
- `mu new <name>` creates this structure, including the org submodule.
- `mu org update` moves the submodule to a newer org-profile commit, as a normal reviewed commit.

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

---

## 7. Remote access: relay container on Martand's Docker server

### 7.1 Constraints

| Fact about the server (2026-11-12) | Consequence for the design |
|---|---|
| `quarve-8gb-fsn1-2`, **195.201.236.190** (Hetzner, Falkenstein, 8 GB). A Dokploy **deploy server**: managed by the main Dokploy instance over SSH, and also running its own Dokploy-managed **Traefik**. 2 projects, ~12–15 containers already on it | The relay is **one more Dokploy service** (its own project). It doesn't change the host, Traefik's config or the other containers |
| Port 22 is used by Dokploy to manage the server; 80/443 belong to Traefik | The relay **publishes no host ports** and never touches host SSH. It's reached **through Traefik on 443** by subdomain |
| A `*.martand.systems` subdomain will point to this server | e.g. `relay.martand.systems`. Traefik routes it to the container. TLS from Let's Encrypt via Dokploy's HTTPS switch |
| Everything on the server must come from GitHub and be deployed with Docker | `relay/Dockerfile` and `relay/compose.yaml` live in the `mu` repo. **Dokploy pulls them from GitHub, builds and deploys on the server** (§7.4) |

### 7.2 How it works

- **Outbound only.** The office PC agent and the laptop both open **outbound WebSocket-over-TLS (`wss://`) connections on 443** to
  `relay.martand.systems`, the same kind of traffic as a web browser. It gets through home, hotel and office firewalls with no
  port forwarding anywhere.
- **Traefik does the TLS.** The server's Dokploy-managed Traefik terminates TLS and forwards the subdomain (including the WebSocket upgrade) to the
  `mu-relay` container on `dokploy-network` (container port 8080).
- **The relay only forwards** and stores nothing.
- **Encryption at two layers:**
  - The tunnel is authenticated and encrypted inside the WebSocket.
  - Inside the tunnel, the laptop ↔ agent traffic is again TLS with the agent's certificate **pinned at pairing**, plus a per-workstation token.

  Neither the relay nor the proxy can read job data.
- **On the office LAN** the laptop connects to the agent directly and skips the relay.
- **Claude and you never see the tunnel.** `mu compute add office-pc <code>` stores the relay URL and credentials (Keychain), and every
  `mu submit/status/log/fetch` picks direct or relay automatically.

### 7.3 Stages

| Stage | Relay software in the container | Why |
|---|---|---|
| **A (start here)** | **chisel** (open source, MIT): TCP tunnelling over WebSocket, SSH-encrypted inside, per-client auth, reverse tunnels. The office PC runs `chisel client … R:office-pc` (a reverse tunnel that ends *inside the container*, never on the host). The laptop's mu runs a chisel client that connects to it | Proven, one small binary, made for running behind an HTTPS reverse proxy. Very little to write |
| **B (later: more users and nodes)** | **A native mu relay** (`src/mu/transport`), same container slot and subdomain | Routing by node ID, per-user tokens, bandwidth quotas, audit log, a team view of nodes |

Moving from A to B changes only the relay's Dockerfile and the mu client. The subdomain, the proxy configuration and the pairings stay the same.

### 7.4 What lives on GitHub, and how Dokploy deploys it

The server is managed by **Dokploy** (§7.1). The relay is deployed the same way as the other services on it: a Dokploy
**Compose service whose source is the `mu` GitHub repo**. Traefik routing is added through Dokploy's **Domains** screen.
No manual SSH, no hand-edited Traefik config.

| Where | What |
|---|---|
| `mu/relay/` (git) | `Dockerfile`, `compose.yaml`, `.env.example`, `README.md` (deploy steps) |
| Build | Dokploy builds `relay/Dockerfile` on the server. A multi-stage build gives a ~20 MB image (chisel static binary on a minimal base). The build takes under a minute, so it's no burden on the 8 GB server |
| Dokploy project **`mu`** (new, separate from Vaulde, Quarve, MSIPL, …) | Compose service **`relay`**: source = GitHub `martand-systems/mu`, compose path `relay/compose.yaml`, **server = `quarve-8gb-fsn1-2` (195.201.236.190)** |
| Dokploy environment variables (on the service) | Relay client credentials. Generated by `mu relay add-client`, **never in git** |
| Deploys | Dokploy auto-deploy on push to the `release` branch (GitHub webhook via Dokploy's GitHub provider). Claude can also trigger one from the laptop with the Dokploy CLI (`dokploy compose deploy`) |

**Domain setup** in Dokploy (the Domains screen in your screenshot), on the `relay` compose service:

| Field | Value |
|---|---|
| Service Name | `mu-relay` |
| Host | `relay.martand.systems` |
| Path / Internal Path | `/` / `/` |
| Strip Path | off |
| Container Port | `8080` |
| Custom Entrypoint | off (uses `websecure`) |
| HTTPS | **on**: Let's Encrypt certificate via Traefik |
| Middlewares | none at first. Later, optionally a rate limit |

**DNS:** `martand.systems` uses **Porkbun** nameservers (not Cloudflare, so there are no proxy timeouts).
Checked 2026-11-12: `relay.martand.systems` currently resolves to Porkbun's parking wildcard (`pixie.porkbun.com`).
**Add an `A` record `relay → 195.201.236.190`** in Porkbun before the Dokploy domain is created, so Let's Encrypt can issue the certificate.

Traefik in Dokploy passes WebSocket upgrades through by default, so nothing else is needed for `wss://`.

Compose sketch. Dokploy adds the Traefik labels and the `dokploy-network` connection from the Domains settings, so the file
has **no labels and no published ports**:

```yaml
services:
  mu-relay:
    build: { context: ., dockerfile: Dockerfile }   # relay/ in the mu repo; Dokploy builds it on the server
    restart: unless-stopped
    environment:
      - RELAY_AUTH=${RELAY_AUTH}          # set in Dokploy env, not in git
    expose: ["8080"]                      # internal only; Traefik reaches it on dokploy-network
    read_only: true
    deploy:
      resources: { limits: { cpus: "0.5", memory: 256M } }
    healthcheck:
      test: ["CMD", "/relay", "health"]
      interval: 30s
```

**Living alongside the other containers:**
- It's a separate Dokploy project, with no host ports and no volumes.
- It has CPU and memory caps (the server has 8 GB and already runs 12–15 containers).
- Deleting the Dokploy service removes it completely.

**Load:** tiny. Job transfers are small (§6.4), and the container idles between jobs.

### 7.5 Status of the relay inputs (2026-11-12)

| Item | Status |
|---|---|
| Subdomain | **`relay.martand.systems`** (decided) |
| DNS provider | Porkbun. **Done:** `A` record `relay` → `195.201.236.190` (confirmed at the Porkbun nameservers and 1.1.1.1, 2026-11-12) |
| Dokploy GitHub app on `martand-systems` | **Installed**: provider `Dokploy-martand`, access to all repos |
| Dokploy setup | **Done in the Dokploy UI by Martand** (decided). The CLI is only used read-only |

**Fallback**, if Traefik can't serve the subdomain: a Dokploy port mapping on a spare high port with its own certificate. This is less firewall-friendly, so it's a last resort.

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

---

## 9. Validation and reproducibility of the toolchain

- **Benchmark suite** (`mu/benchmarks/`):

| Benchmark | Pass criterion |
|---|---|
| `fea-cantilever` | 3 meshes against Euler-Bernoulli: tip deflection within 0.6%, root stress within 0.001% |
| `aero-vlm-wing` | VSPAERO against reference |
| `cfd-cavity` | Against Ghia et al. |
| `slice-smoke` | PrusaSlicer output within expected bounds |
| `cam-smoke` | FreeCAD CAM G-code checks |

- **Release gate:** a tag is only made after the suite passes on fresh builds on both the office PC (linux-64 in WSL) and the laptop (macOS arm64).
- **Installer gate:** every install (laptop, office PC) runs the suite before it's marked ready.
- **Cross-host check:** the JATAYU `L_final` hub model is run on `local` and `office-pc` and must agree to within < 0.5%.
- **Upgrades:** `mu upgrade` changes a project's pin, re-runs its validation scripts and diffs `results/*.json`. It's never silent.
- **Version scheme:** `vYYYY.MM.patch`.

---

## 10. GitHub layout

**GitHub organisation:** [`martand-systems`](https://github.com/martand-systems). Checked 2026-11-12: it's on the **Free plan** and has no repos yet.

| Repo | Visibility | Contents |
|---|---|---|
| `martand-systems/mu` | **Private** (decided). The Free plan works because no images are stored | Source, recipes and lock files, installers, relay, Claude Code plugin marketplace, benchmarks |
| `martand-systems/mu-org-profile` | Private | Org profile (submodule of each project) |
| `martand-systems/jatayu` | Private | Project (git-lfs). **Cleared for GitHub** |

**A release is just a git tag** (`v2026.11.0`) on `mu`. No binary assets and no container registry:
- Installers clone `mu` at the tag and build on the device (§5.3).
- Dokploy builds the relay from the repo.
- The small installer entry points (`install-mac.sh`, `MuComputeSetup.exe`) can be attached to the GitHub Release for a one-click download. They're a few MB, so well within the Free plan.

**Free-plan check:**

| Resource | Free allowance | mu's use |
|---|---|---|
| Repo storage | Generous for source | Text only (recipes, scripts, lock files) |
| GitHub Packages / GHCR | 500 MB | **Not used** |
| Actions minutes | 2,000 / month (macOS ×10) | Light: lint, unit tests, the relay build. The heavy benchmark runs are done on the devices themselves (or on self-hosted runners on the office PC and laptop) |
| Git LFS | 1 GB storage / 1 GB bandwidth per month | JATAYU binaries ≈ 20 MB today. Fine; buy data packs if it grows |

**Access from the devices, if `mu` is private:**
- The laptop clones with your `gh` login.
- The office PC uses a **read-only deploy key** for `mu` only, created by the installer during pairing.
- Dokploy needs the Dokploy GitHub app installed on `martand-systems`.

**Dokploy ↔ GitHub:** Dokploy currently has three GitHub providers (`Dokploy-gridflow`, `Dokploy-akhemka`, `msi-dokploy`) and
**none for `martand-systems`**. Install the Dokploy GitHub app on the org. It's needed for a private `mu` and for push-to-deploy.

**CI (GitHub Actions)**, kept light:
- On each push: lint, unit tests, a relay image build and tunnel smoke test, and a check that the lock files resolve.
- **Release checklist** (`mu release`):
  - a fresh build plus the benchmarks on the office PC and on the laptop;
  - the results are committed to `benchmarks/results/<tag>.json`;
  - then the tag is created.
- Optionally, register the office PC and the laptop as **self-hosted runners** so this happens automatically on a tag, without using hosted minutes.

---

## 11. Security

- **Data classification:** JATAYU design data is cleared for GitHub (confirmed 2026-11-12).
- **Relay:**
  - outbound-only connections over `wss://` 443 through the server's Traefik
  - end-to-end encryption between laptop and agent; the relay stores nothing
  - per-client credentials, kept in the server's `.env` and the clients' Keychain, never in git
  - the container publishes no host ports, runs read-only with CPU and memory limits, and doesn't touch host SSH or the other containers
  - deployed only by Dokploy from GitHub; no extra SSH keys or access to the server are created
- **Agent:** runs as an unprivileged user in WSL and accepts only mu job types from paired workstations. Pairing can be revoked.
- **Credentials:**
  - GitHub tokens and pairing keys live in the laptop's Keychain.
  - The compute node has **no GitHub write access**. It only downloads mu releases.
- **Licences:** a `THIRD_PARTY.md` lists each bundled solver's licence and source. Avoid Docker Desktop (licensing).

---

## 12. Migrating today's folder

Nothing in the current folder is deleted until the new setup reproduces today's results.

| Step | Action | Touches current files? |
|---|---|---|
| M1 | Freeze the baseline: `jatayu-frame/results/*.json`, the report docx, the run commands from `README.md` | Read only |
| M2 | Export exact package lists from `env/` and `tools/cam-venv` into lock files. Record app versions and checksums | Read only |
| M3 | Move `felib.py` generics into `mu.fea`, docx styling into the `mu-org-profile` template, and the `CLAUDE.md` tool knowledge into plugin skills | Copies only |
| M4 | Create `jatayu` repo: docs, the frame files (without `work/`, `__pycache__`, possibly `old/`), `mu.toml`, `org/` submodule, a JATAYU-only `CLAUDE.md`, `.claude/settings.json`, LFS rules. Replace the `../../` paths | Copies only |
| M5 | Re-run the README sequence through `mu` on `local` and `office-pc`, and compare with M1 | No |
| M6 | Archive the old `ENGINEERING/` folder. This later frees ~14 GB of `work/` and 4.7 GB of `.cache` | After M5 passes |

---

## 13. Phases and acceptance criteria

| Phase | Work | Done when |
|---|---|---|
| **P0 Prerequisites** | Private repos created in `martand-systems` (`mu`, `mu-org-profile`, `jatayu`); Porkbun `A` record for `relay.martand.systems`; Dokploy GitHub provider for the `mu` repo; Dokploy CLI detail calls working (or first setup in the Dokploy UI); admin access to the office PC; AMD SVM on | All the §15 items answered |
| **P1 macOS full install** | `mu` repo, locks, `apps.toml`, wrappers, CLI (`run`, `test`, `doctor`, `context`), the rebuilt 5 benchmarks, `install-mac.sh`, install to `~/Library/Mu` | A fresh install on the laptop passes all benchmarks offline. `mu` is on `PATH` from any folder |
| **P2 Claude Code plugin and project format** | Plugin (skills, commands, hooks), `mu new`, `mu.toml`, org submodule, run records, `mu check` | Claude Code in a fresh `mu new` project designs and analyses a test part end to end using only mu commands, with run records produced |
| **P3 Linux compute recipe** | `provision-linux.sh` and linux-64 locks, tested in a local Ubuntu container/VM on the laptop; `mu release` checklist; first tag | A fresh linux-64 build passes the benchmarks and matches the macOS results |
| **P4 Agent and job protocol** | Agent, `compute add/list/status`, `submit/status/log/fetch`, content-addressed upload, `auto` fallback | Works end to end against the `local` node and a Linux test container on the laptop |
| **P5 Windows compute installer** | `MuComputeSetup.exe`, startup task, `.wslconfig`, remote management commands | One run on the office PC (plus one reboot) leaves an agent that passes the benchmarks and survives a reboot with nobody logged in |
| **P6 Relay (Stage A)** | `relay/Dockerfile` (chisel) and `relay/compose.yaml`, built by Dokploy, Dokploy project `mu` with the `relay` service and domain on 195.201.236.190 (`relay.martand.systems`, HTTPS); chisel client in the agent and in the mu CLI; pairing; LAN direct path | The laptop runs jobs on `office-pc` **from home**. With the PC off, jobs fall back to `local` without errors |
| **P7 Org profile and JATAYU migration** | `mu-org-profile`, `jatayu` repo, steps M1–M6 | JATAYU frame results reproduce the baseline on both nodes within tolerance. The old folder is archived |
| **P8 Hardening** | OpenVSP and FreeCAD CAM headless in the linux-64 recipe, relay Stage B if needed, `pvserver` remote viewing, signed `.pkg` and `.exe`, uninstallers | All 5 benchmarks run on both nodes. Remote viewing works from home |

---

## 14. Risks

| Risk | Mitigation |
|---|---|
| Upstream download sites change or disappear (apps in `apps.toml`) | Hash-pinned URLs fail loudly. Keep a mirror cache of the app downloads on the office PC or Dokploy server; conda-forge packages are permanent |
| OpenVSP and FreeCAD headless Linux builds are hard to package | Ship FEA and CFD on the office PC first. Aero and CAM run on `local` (macOS native) meanwhile |
| WSL2 doesn't stay up without a logged-in user | systemd, a startup task, `vmIdleTimeout=-1`; P5 tests a reboot with nobody logged in |
| Traefik can't route the new subdomain | Dokploy Domains handles it like the other services; last resort is a Dokploy port mapping on a high port |
| The 8 GB server is shared with ~12–15 containers | The relay is capped at 0.5 CPU and 256 MB and moves only small transfers |
| Relay tunnel drops on flaky links | Clients reconnect automatically; 30 s keepalives; jobs keep running on the PC during a drop |
| The relay affects other containers on the server | No host ports, its own compose project, resource limits, read-only container |
| Relay server down | Laptop falls back to `local`. On the office LAN, the direct path still works |
| arm64 vs x86_64 numerical differences | Cross-host check; host recorded in every run |
| Two compute builds to keep in step | Same lock-file source, same benchmark gate |
| MPI-parallel OpenFOAM hangs on the Mac (seen during JATAYU work), so the laptop's CFD is serial only | Local CFD is for small or coarse cases. Parallel CFD goes to `office-pc` (native Linux MPI in WSL2) |
| Claude bypasses mu and runs solvers directly (no run record) | PreToolUse hook warning, `mu check`, and skills that teach the mu route |
| ~6 GB+ per toolchain version on the laptop | `mu gc` removes versions no project pins. Shared caches |

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
| First nodes | Laptop (`full`, macOS arm64) and office PC (`compute`, Windows 11, Ryzen CPU and NVIDIA GPU) |
| JATAYU data on GitHub | Allowed |

**Also settled (2026-11-12):**

| Decision | Value |
|---|---|
| Product name | **μ** / `mu` (CLI `mu`, repo `martand-systems/mu`, icon μ) |
| DNS | `relay.martand.systems` → 195.201.236.190 (done) |
| Relay setup | Martand does it in the Dokploy UI |
| Compute architecture | ADR-001: Linux compute; WSL2 is the first Windows host adapter |
| Shared libraries | Default: `felib.py` generics → `mu.fea`, docx styling → `mu-org-profile` (M3) |
| JATAYU | Existing work moves into `martand-systems/jatayu` now, ahead of P7, using the current toolchain until mu P1 lands |

**Note on the name:** `mu` is also the name of an unrelated email tool (maildir-utils). If it were ever installed, the two CLIs
would collide. mu's installer checks for this and warns.

---

## 16. Implementation readiness

**Verdict: ready to start implementation (P0 → P1 → P2) now.** Later phases have their design fixed. Their detailed protocol and API
specs are written at the start of each phase, and the early spikes below are what could change a design decision.

**Spikes to run first (days, not weeks), because they test assumptions the plan depends on:**

| Spike | Tests | If it fails |
|---|---|---|
| S1 Fresh macOS build from lock files exported from today's `env/` and `cam-venv`, in a new folder | Today's hand-built setup can be reproduced from a recipe | Fix the recipe (pins, patches) before anything else |
| S2 linux-64 provisioning in an Ubuntu 24.04 container on the laptop: conda env, CalculiX, Gmsh, OpenFOAM v2606 (ESI apt repo), ParaView headless, OpenVSP + Python API, PrusaSlicer, FreeCAD 1.1.4 headless + CAM env | Every tool has a working headless Linux route | Mark that tool `local`-only (macOS) until solved. FEA and CFD must pass |
| S3 WSL2 on the office PC: import Ubuntu, systemd service, starts after a reboot with nobody logged in, memory setting applies | The always-on compute node is feasible | Use a Windows service wrapper that keeps `wsl.exe` running |
| S4 chisel through Dokploy's Traefik on `relay.martand.systems` (after the DNS record): a reverse tunnel from one client, a forward from another, a long idle period, reconnect | The relay design works with this Traefik | Try frp over WebSocket. Last resort: a high port |

**Before P1 starts:** answer open items 2 and 3 above (name, shared libraries), or accept the defaults.

---

## 17. Later additions (not in scope yet)

- Windows and Linux workstations
- Linux-server compute installer (`docker compose build` with the P3 recipe)
- GPU-accelerated solvers and rendering on the office PC
- Several compute nodes and team members (relay Stage B)
- Self-hosted GitHub Actions runner on the office PC, to re-run validations on pull requests
- Remote ParaView (`pvserver`) through the relay
