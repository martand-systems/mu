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

## Install (macOS, Apple Silicon)

    git clone https://github.com/martand-systems/mu.git && cd mu
    install/install-mac.sh            # full role: builds the toolchain, runs mu doctor + the benchmark suite
    mu help

## Status

| Phase | State |
|---|---|
| Plan | v5 (`docs/PLAN.md`) |
| S1: reproducible macOS build | Pins captured from the legacy toolchain (`toolchain/`); fresh-build test next |
| S2–S4 | Not started |

`legacy/` holds the hand-built toolchain's activation script, setup report and instructions. They're the reference that the recipe has to reproduce.
