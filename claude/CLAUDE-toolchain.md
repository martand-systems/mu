# μ (mu) toolchain: instructions for Claude Code

This project uses the **μ toolchain** (code-driven CAD, FEA, CFD, aero, slicing, CAM). μ is an installed application.
The project pins its version in `mu.toml`. These instructions come from the installed toolchain, through `.mu/toolchain`
(a link made by `mu link`), so they always match the version in use.

## Running anything

Every tool runs through `mu`. Shell state doesn't persist between Bash calls, so prefix each command:

```bash
mu run python part.py            # build123d / CadQuery / gmsh / PyVista / openvsp / mu.fea
mu run <tool> [args]             # gmsh, ccx, cad, render-png, vsp, vspaero, pvbatch, openfoam, prusa-slicer, cam-python
eval "$(mu env)" && <cmds>       # several commands in one Bash call
mu doctor                        # check every tool resolves and starts
mu test [name]                   # benchmark suite: cad-solid, fea-cantilever, aero-vlm-wing, cfd-cavity, slice-print, cam-plate
mu version                       # toolchain version, recipe commit, layers
mu                               # status: version in use, project, link state
```

- The version comes from the project's `mu.toml`. Don't change it on your own: `mu pin <version>` moves the project
  to another installed version (ask first, then re-run the benchmarks and the project's validation).

- The toolchain is read-only (sealed layers). **Never** install packages into it, and never run `micromamba activate`, `micromamba shell hook`, text-to-cad's
  installer, `cad setup` or `cad update`. Toolchain changes go into the `mu` repo's recipe and a new μ version.
- Project files stay in the project repo. Large regenerable solver output goes in `.scratch/` or another gitignored folder.

## Commands by stage

**CAD (build123d preferred; CadQuery available)**

```bash
mu run python part.py                       # plain build123d script: export_step / export_stl
mu run cad run part.py                      # text-to-cad flow (@model, see the cad skill); prints id
mu run cad model snapshot <id> out.png      # OCCT render of a model
mu run cad model export <id> part.step part.stl
mu run cad stop                             # stop the cad server when done
mu run render-png part.step out.png [--views iso,front,top,right] [--edges] [--scalars NAME]
```

- Always check geometry yourself: render a PNG and Read it, and check `part.is_valid`, the solid count, volume and bounding box.
- Units are mm, +Z up.

**Meshing and FEA (Gmsh + CalculiX): use `mu.fea`**

`from mu import fea` gives `fea.Model`. It covers: build123d solids → Gmsh conformal 10-node tets (node order already converted for
C3D10) → CalculiX → `.dat` / `.frd` parsing. It has node sets by predicate, rigid couplings for loads and masses, fixes,
static and modal steps, nodal fields, reactions and integration-point stresses. The `fea-cantilever` benchmark
(`.mu/toolchain/benchmarks/fea-cantilever/bench.py`) is the minimal example.

- Load quadratic elements through rigid couplings or consistent nodal loads, never equal point loads.
- `ccx2paraview` is broken with NumPy 2. Use `mu.fea.parse_frd`.

**Aero (OpenVSP / VSPAERO)**

- `import openvsp as vsp`, and set `vsp.SetVSPAEROPath(os.environ["MU_VSPAERO_DIR"])` (set by μ on every platform).
- For VLM, set `GeomSet=[SET_NONE]` and `ThinGeomSet=[SET_ALL]` on both `VSPAEROComputeGeometry` and `VSPAEROSweep`.
- **Set `Sref`, `bref` and `cref` explicitly.** The default reference area is 100 (manual mode), which silently scales every coefficient.
- Validate against an independent solution, not Helmbold. Helmbold assumes elliptic loading and overpredicts a rectangular AR 6 wing by ~7%.
  See `benchmarks/aero-vlm-wing/reference_vlm.py`.

**CFD (OpenFOAM v2606, ESI/OpenCFD)**

```bash
mu run openfoam -c 'cd <case> && blockMesh && checkMesh && simpleFoam'
mu run openfoam-eject            # unmount /Volumes/OpenFOAM-v2606 when finished
```

- **Case paths must not contain spaces.** OpenFOAM rejects them (`fileName::stripInvalid`).
- The toolchain is read-only, so a tutorial copied out of `$FOAM_TUTORIALS` is too: `cp -r … case && chmod -R u+w case`.
- On macOS the app mounts a read-only image at `/Volumes/OpenFOAM-v2606` while it's in use.
- MPI-parallel snappyHexMesh hangs on Apple Silicon laptops, so run serially locally. Parallel CFD belongs on a Linux compute node.

**Post-processing**: `mu run pvbatch --force-offscreen-rendering script.py` (ParaView 6.2.1, `paraview.simple`; for OpenFOAM,
`touch case.foam` and use `OpenFOAMReader`). PyVista in the main env handles quick plots.

**3D printing (PrusaSlicer 2.9.7)**: `mu run prusa-slicer --export-gcode --load profile.ini part.stl -o part.gcode`.
Keep an explicit `.ini` profile in the project.

**CNC (FreeCAD CAM via ocp-freecad-cam)**: `mu run cam-python cam.py`. That's Python 3.11 with build123d 0.12 and CadQuery 2.8 (OCP 7.9), **not**
the main env. Design in the main env, export STEP, then `bd.import_step()` it in the CAM script. Never mix objects between the two envs.

- All tool controllers are numbered T1.
- `drill(peck_depth=…)` is ignored; FreeCAD's default of 0.75 × D is used instead.
- **Always inspect the G-code** (depths, feeds, tool changes, a toolpath plot) before cutting.

## Validation

The company validation policy (benchmark first, mesh convergence, no singular peaks, hand checks, stated assumptions, evidence
in the README) is in the org profile (`org/CLAUDE-org.md`). Follow it for every FEA, CFD or aero number used in a decision.
