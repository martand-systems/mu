# Engineering toolchain: code-driven CAD, FEA, CFD, CAM

This folder is a self-contained engineering workbench for designing parts (for example
drone airframes for JATAYU) from code. Everything is installed **inside this folder**.
Deleting the folder uninstalls everything. Setup history and test results are in
`SETUP_REPORT.md`. The JATAYU Phase 1 engineering documents (`JATAYU_Phase1_*.docx`)
are the design context.

## Always activate first

```bash
source ./activate.sh        # zsh or bash; nothing works without it
```

Run this at the start of **every** Bash command. Shell state doesn't persist between
calls, so write commands as `source ./activate.sh && <command>`.

What activation does:
- Puts `./tools/bin` (wrappers) and `./env/bin` (conda env) on `PATH`.
- Redirects every cache, config and temp folder (pip, conda, uv, matplotlib, XDG, `TMPDIR`, text-to-cad state) into `./.cache`.
- Doesn't touch your shell profile.

## The folder rule (hard)

- Never install or write outside this folder. No Homebrew, no sudo, no global pip/npm installs, no `~/.zshrc` edits, nothing in `/Applications`, `/usr/local` or `/opt/homebrew`.
- If a task seems to need a write outside the folder, **stop and ask the user**. Don't work around it.
- Never run `micromamba shell hook`, `micromamba activate`, or the real binary in `tools/bin/real/`. They write to `~/.conda`. Use `micromamba install -p "$ENG_ROOT/env" <pkg>`, which goes through the wrapper.
- text-to-cad: never run its installer (`curl … install.sh`), `cad setup`, or `cad update`. They configure global agent apps and write to `~/.local/bin`. `cad doctor --check` is safe.
- Run GUI and native tools through the wrappers in `tools/bin`. They keep `HOME` and app config inside `./.cache`.

## Layout

| Path | What |
|---|---|
| `activate.sh` | Environment activation (see above) |
| `env/` | Conda-forge env, Python 3.13: OCP 8.0.1.1, build123d 0.13, CadQuery 2.9.0.dev, Gmsh 4.15.2, CalculiX 2.23, VTK 9.7.0, PyVista, OpenVSP API, text-to-cad `cad`. Spec: `env.yml` |
| `tools/bin/` | Wrappers and commands: `micromamba`, `render-png`, `gmsh`, `vsp`, `vspaero`, `pvbatch`, `pvpython`, `paraview`, `openfoam`, `openfoam-eject`, `prusa-slicer`, `freecad`, `freecadcmd`, `cam-python` |
| `tools/cam-venv/` | FreeCAD 1.1.4's Python 3.11 plus build123d 0.12, CadQuery 2.8, ocp-freecad-cam (needs OCP **7.9**) |
| `tools/scripts/` | `render_png.py`, `freecad-env.sh` |
| `tools/pip-stubs/`, `tools/patches/` | Placeholder packages and the `.pth` fix that keep exactly one OCP per env (see SETUP_REPORT) |
| `apps/` | OpenVSP 3.56.0, ParaView 6.2.1, OpenFOAM-v2606.app, PrusaSlicer 2.9.7, FreeCAD 1.1.4 |
| `vendor/text-to-cad/` | text-to-cad v0.11.9 source (CAD skill origin) |
| `.claude/skills/cad/` | Project-level CAD skill (text-to-cad) |
| `.cache/` | All caches, state, temp, downloads (safe to prune; see SETUP_REPORT) |
| `projects/` | **All work goes here**, one folder per part or study |

## Where projects go

Use `projects/<name>/`, for example `projects/jatayu-arm-mount/`. Keep the model script,
analysis scripts, exports (STEP/STL), meshes, solver folders, plots and a short
`README.md` (assumptions, loads, results) together.

The reference examples are the smoke tests: `projects/smoke-test` (CAD and slicing),
`projects/fea-cantilever` (FEA benchmark), `projects/aero-openvsp` (VLM),
`projects/cfd-cavity` (OpenFOAM) and `projects/cam-smoke` (CNC).

## Commands by stage

**CAD (build123d preferred; CadQuery available)**

```bash
python part.py                                   # plain build123d script: export_step / export_stl
cad run part.py                                  # text-to-cad flow (@model, see .claude/skills/cad); prints id
cad model snapshot <id> out.png                  # OCCT render of a model
cad model export <id> part.step part.stl
cad stop                                         # stop the cad server when done
render-png part.step out.png [--views iso,front,top,right] [--edges] [--scalars NAME]
```

- Always check geometry yourself: render a PNG and Read it, and check `part.is_valid`, the solid count, volume and bounding box.
- Units are mm, +Z up.

**Meshing and FEA (Gmsh + CalculiX)**

The pattern is `projects/fea-cantilever/run_cantilever.py`:
- STEP → `gmsh` Python API, with `gmsh.initialize(["-noenv"])` and `Mesh.ElementOrder 2`.
- Write the `.inp` yourself with C3D10 elements. Gmsh's tet10 nodes need reordering: swap the last two.
- Run `ccx -i job` in the case folder.
- Parse `.frd` with the bundled `read_frd()`. `ccx2paraview` is broken with NumPy 2.
- Load with consistent nodal loads, not equal point loads, on quadratic elements.

**Aero (OpenVSP / VSPAERO)**

```bash
python script.py      # import openvsp as vsp; vsp.SetVSPAEROPath(f"{ENG_ROOT}/apps/OpenVSP-3.56.0-MacOS")
vsp / vspaero          # CLI wrappers
```

For VLM, set `GeomSet=SET_NONE` and `ThinGeomSet=SET_ALL` on both `VSPAEROComputeGeometry`
and `VSPAEROSweep`. See `projects/aero-openvsp/wing_vlm.py`.

**CFD (OpenFOAM v2606, ESI/OpenCFD flavour)**

```bash
openfoam -c 'cd projects/<case> && blockMesh && checkMesh && simpleFoam'   # one-shot
openfoam                                                                    # interactive shell
openfoam-eject                                                              # unmount when done
```

- The app mounts its read-only volume at `/Volumes/OpenFOAM-v2606` while in use. The user approved that. Eject when finished.
- Keep cases in `projects/`. `HOME` and `FOAM_RUN` are inside `./.cache/home`.
- Compiling custom solvers (`wmake`) needs the Xcode licence accepted (`sudo xcodebuild -license`). That's a system action, so ask the user.

**Post-processing (ParaView 6.2.1)**

```bash
pvbatch --force-offscreen-rendering script.py    # paraview.simple; OpenFOAM: touch case.foam, OpenFOAMReader
```

PyVista in `./env` handles quick plots. See `projects/fea-cantilever/pv_post.py` and `projects/cfd-cavity/pv_cavity.py`.

**3D printing (PrusaSlicer 2.9.7 CLI)**

```bash
prusa-slicer --export-gcode --load profile.ini part.stl -o part.gcode
grep -E '^; (estimated printing time|filament used)' part.gcode
```

Keep an explicit `.ini` profile in the project (example: `projects/smoke-test/print_PLA_0.4_0.2.ini`).

**CNC toolpaths (FreeCAD CAM via ocp-freecad-cam)**

```bash
cam-python projects/<name>/cam.py   # Python 3.11, build123d 0.12 / CadQuery 2.8 (OCP 7.9), NOT ./env
```

- Design in `./env`, export STEP, then `bd.import_step()` it in the CAM script. The two envs carry different OCP versions, so never mix their objects.
- Known limitations of ocp-freecad-cam 2.0.2 with FreeCAD 1.1.4:
  - All tool controllers are numbered T1.
  - `drill(peck_depth=…)` is ignored, and FreeCAD's default of 0.75 × diameter is used.
  - The project calls itself experimental. **Always inspect the G-code**: depths, feeds, tool changes, and a toolpath plot before cutting.

## Simulation results must be validated before they're trusted

This is non-negotiable for any FEA, CFD or aero number used in a design decision:

1. **Benchmark the workflow first.** Before trusting a new kind of analysis (element type, BC style, solver settings, turbulence model), reproduce a case with a known answer: beam theory, plate formulae, a tutorial with reference data, or published experimental or VLM data. Report the % error. `projects/fea-cantilever` is the template: three meshes, tip deflection within 0.6% of Euler-Bernoulli, root stress within 0.001%.
2. **Mesh convergence.** Run at least 2, preferably 3, mesh densities (refinement ratio ≈ 2). Report the quantity of interest at each, the change between levels, and a Richardson estimate if monotonic. A result that changes by more than a few % between the two finest meshes isn't converged.
3. **Don't trust singular peaks.** Raw peak stress at re-entrant corners, point loads and fully fixed edges grows without bound as the mesh is refined. In the cantilever, raw peak von Mises went 59 → 60 → 72 MPa. Read stresses away from singularities, use fillets as in the real geometry, or linearise or extrapolate.
4. **Sanity-check with hand calculations:** reaction forces equal applied loads, mass and volume match the CAD, the deflection direction and order of magnitude are right, and the stress is near F/A or Mc/I.
5. **State assumptions and limits:** material values (and their source), boundary conditions, load cases, linear vs nonlinear, and what wasn't modelled (bolt preload, contact, fatigue, buckling, dynamics). For CFD: y+, residual convergence, mass-flow balance, and the time-step or Courant number.
6. Put the validation evidence in the project's `README.md` next to the result. A number with no validation trail is a guess, so label it that way.

## Maintenance

- Add a conda package with `micromamba install -p "$ENG_ROOT/env" <pkg>` and record it in `env.yml`.
- Add a pip package with `pip install <pkg>`, after checking it doesn't pull `cadquery-ocp`, `vtk` or `numpy` over the conda builds (`pip check`; `micromamba list -p ./env ocp vtk-base numpy`).
- Free disk space by deleting `.cache/downloads` and running `micromamba clean -a`. Recreating `./env` from `env.yml` then needs the post-install steps in `SETUP_REPORT.md`.
