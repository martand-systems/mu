# Engineering toolchain: setup report

- Date: 2026-11-12 (14:09–15:50 NPT)
- Machine: Apple M1 (arm64), macOS 27.0.1 (26A434), 16 GB RAM, 142 GB free at start (122 GB after)
- Xcode and the Command Line Tools were present (the Xcode licence has **not** been accepted; see "Open items")
- Folder: `/Users/avi/Desktop/Martand/JATAYU/ENGINEERING`, **15 GB in total** (about 5 GB of that is reclaimable cache; see "Disk")

## Tools installed

| Tool | Version | Location | Source | Smoke test |
|---|---|---|---|---|
| micromamba | 2.10.0 | `tools/bin/real/micromamba` (wrapper `tools/bin/micromamba`) | GitHub release, SHA-256 verified | ✅ |
| Python env | Python 3.13.18 | `env/` | conda-forge (`env.yml`) | ✅ |
| OCP / OCCT | 8.0.1.1 / 8.0.1 | `env/` | conda-forge | ✅ |
| build123d | 0.13.0 | `env/` | PyPI (OCP itself from conda-forge; see notes) | ✅ |
| CadQuery | 2.9.0.dev0 (git `b3f9afd`) | `env/` | GitHub master (stable 2.8.0 needs OCP <8) | ✅ (box volume check) |
| text-to-cad (`cad`, `cad-server`) | 0.11.9 (commit `8ea2434`) | `vendor/text-to-cad`, installed into `env/`; skill in `.claude/skills/cad` | GitHub | ✅ `cad run`, `cad model snapshot`, `cad model export` |
| Headless renderer | PyVista 0.49.2 / VTK 9.7.0 | `tools/bin/render-png` | conda-forge + own script | ✅ |
| Gmsh (+ Python API) | 4.15.2 | `env/` (wrapper `tools/bin/gmsh`) | conda-forge | ✅ |
| CalculiX `ccx` | 2.23 | `env/bin/ccx` | conda-forge | ✅ |
| OpenVSP (+ Python API, VSPAERO 7.2.2) | 3.56.0 | `apps/OpenVSP-3.56.0-MacOS`, API in `env/` | openvsp.org ARM64 / Py3.13 build | ✅ |
| ParaView (pvpython/pvbatch) | 6.2.1 | `apps/ParaView-6.2.1.app` | paraview.org signed arm64 DMG | ✅ |
| OpenFOAM | v2606 (ESI/OpenCFD), openfoam-app 2.2.3 | `apps/OpenFOAM-v2606.app` | github.com/gerlero/openfoam-app (user chose option A) | ✅ |
| PrusaSlicer | 2.9.7 | `apps/PrusaSlicer-2.9.7.app` | GitHub signed DMG | ✅ |
| FreeCAD | 1.1.4 (Python 3.11.14) | `apps/FreeCAD-1.1.4.app` | GitHub DMG, SHA-256 verified | ✅ |
| ocp-freecad-cam | 2.0.2 | `tools/cam-venv` (with build123d 0.12.0, CadQuery 2.8.0, cadquery-ocp 7.9.3.1) | PyPI | ✅ with caveats |

## Smoke-test results

### Phase 1: CAD (`projects/smoke-test`): PASS
- **Part:** L-shaped motor-mount bracket in build123d (`bracket.py`). It has an inner bend fillet, rounded corners, a 12 mm bore with 4 × M3 on a 25 mm PCD, and 2 × M4.
- **Validity:** 1 solid, valid B-rep (`BRepCheck_Analyzer` + `is_valid`), 20 faces and 54 edges.
- **Size:** 50.00 × 40.00 × 45.00 mm.
- **Volume: 13,886.61 mm³**, +0.37% vs the hand estimate without fillets (the bend fillet adds material). That's 37.5 g in 6061 aluminium.
- **Exports:** `bracket.step`, `bracket.stl`. Re-importing the STEP gives the same volume to within 3.6e-12 mm³.
- **Snapshots:** `bracket_views.png` (PyVista, 4 orthographic and iso views) and `bracket_cad_snapshot.png` (text-to-cad OCCT renderer). Both inspected visually.

### Phase 2: Meshing and FEA (`projects/fea-cantilever`): PASS
- **Case:** 200 × 20 × 10 mm aluminium cantilever (E = 70 GPa, ν = 0.33), 100 N tip shear, root fully fixed.
- **Pipeline:** build123d STEP → Gmsh 2nd-order tets → CalculiX C3D10 → `.frd` → PyVista and ParaView.
- **Hand calculations:** Euler-Bernoulli tip deflection 2.28571 mm (Timoshenko 2.29027 mm), root bending stress 60.000 MPa.

| Mesh h | Nodes | DOF | Tip δ [mm] | Error vs EB | Root σ (extrapolated) [MPa] | Error | Raw peak von Mises | Solve |
|---|---|---|---|---|---|---|---|---|
| 5 mm | 3,953 | 11,859 | 2.26763 | −0.79% | 60.003 | +0.006% | 59.2 | 0.4 s |
| 2.5 mm | 22,123 | 66,369 | 2.27037 | −0.67% | 60.001 | +0.001% | 60.4 | 2.7 s |
| 1.25 mm | 134,688 | 404,064 | 2.27152 | −0.62% | 59.999 | −0.001% | 72.1 | 65 s (3.1 GB RAM) |

- **Deflection:** converges monotonically. The Richardson estimate is about 2.272 mm, −0.6% vs Euler-Bernoulli. That remainder comes from the model, not the mesh: the fully clamped 3D root face stops the Poisson contraction there and stiffens the beam slightly.
- **Root bending stress:** read from top-fibre σxx over 0.25–0.75 L and extrapolated to the root, as recommended.
- **Raw peak von Mises does not converge.** It's the singularity at the clamped edge, which is why `CLAUDE.md` forbids trusting singular peaks.
- **Plots:** `stress_plot.png` (PyVista), `validation_plot.png` (FE vs theory and convergence), `stress_paraview.png` (pvbatch).

### Phase 3: Aero and CFD: PASS
- **OpenVSP** (`projects/aero-openvsp/wing_vlm.py`): rectangular wing, AR 6, built with the Python API and run through a VSPAERO VLM sweep from 0 to 6°. CLα = 4.618/rad vs the Helmholtz estimate of 4.529/rad (**+2.0%**). It also exports `.vsp3` and STL.
- **OpenFOAM** (`projects/cfd-cavity`): standard tutorial `incompressible/icoFoam/cavity/cavity`. `blockMesh` → `checkMesh` (Mesh OK, 400 cells) → `icoFoam` to t = 0.5 s, final continuity error 9.7e-9. Image: `cavity_U.png` (|U| plus streamlines, ParaView's OpenFOAM reader via pvbatch), showing the expected primary vortex below the lid.
- **ParaView:** pvbatch rendered both the FEA result and the OpenFOAM case offscreen.

### Phase 4: Manufacturing: PASS (CAM with caveats)
- **PrusaSlicer:** the Phase 1 bracket (`bracket.stl`) sliced with `projects/smoke-test/print_PLA_0.4_0.2.ini`: generic 250 × 210 bed, 0.4 nozzle, 0.2 mm layers, PLA, 3 perimeters, 30% gyroid, no supports. The bracket was printed in its modelled orientation, with the base flange on the bed.
  - **Estimated print time: 1 h 39 m 20 s** (normal mode; 1 h 42 m silent).
  - **Material: 14.13 g PLA**, which is 11.39 cm³ or 4,736 mm of 1.75 mm filament (about 0.35 at 25/kg).
  - Output: `bracket_PLA_0.2mm.gcode`.
- **FreeCAD CAM via ocp-freecad-cam** (`projects/cam-smoke/plate_cam.py`): a 60 × 40 × 6 mm plate.
  - Operations: offset pocket (3 mm endmill, 1 mm step-down to −3), G83 peck drilling of 4 × Ø3.4 to −6, and outside profile (1.5 mm steps to −6). The grbl post-processor produced `plate_grbl.nc` (425 lines). Depths checked.
  - **Caveats:**
    1. Every tool controller is numbered T1 (the API has no tool-number parameter).
    2. `peck_depth` is ignored with FreeCAD 1.1.4, which uses its default 0.75 × D = 2.55 mm.
    3. ocp-freecad-cam calls itself experimental and was tested against FreeCAD 1.1.1. Always review the G-code.

## Problems found and how they were handled

1. **CadQuery vs build123d OCP versions.** CadQuery 2.8.0 stable needs OCP <8, while build123d 0.13 and text-to-cad need OCP 8. I used CadQuery git master, which supports OCP 8.0.1.1.
2. **One OCP per environment.** OCP comes only from conda-forge (`ocp=8.0.1.1`). pip's `cadquery-ocp` and `cadquery-ocp-novtk` requirements are met by empty placeholder packages (`tools/pip-stubs/`), so pip never installs a second, conflicting OCP.
3. **CadQuery's setup.py skips its dependencies inside conda Pythons.** I installed `multimethod`, `runtype`, `pyparsing`, `ezdxf`, `nlopt`, `casadi` and `numba` by hand.
4. **text-to-cad's lazy-import hook breaks conda OCP.** `cad-lazy-kernel.pth` lazily loads `OCP`, assuming it's a package as in the pip wheel. conda-forge ships OCP as one extension module, so `from OCP.gp import …` failed. The fix is `env/lib/python3.13/site-packages/zz-conda-ocp-eager.pth` (copy in `tools/patches/`), which limits the hook to `build123d`. The vendor code is unmodified.
5. **text-to-cad paths.**
   - Its engine socket defaulted to the macOS per-user temp folder (`/var/folders/...`), which ignores `TMPDIR`. Fixed with `CAD_ENGINE_DIR`. The one temp folder it had created, `…/T/cad-engine`, was removed.
   - State, cache, launcher, telemetry and updates are controlled by `CAD_STATE_DIR`, `CAD_CACHE_DIR`, `CAD_LAUNCHER_DIR`, `CAD_TELEMETRY=0` and `CAD_UPDATE_CHECK=0`.
   - I ran only `cad doctor -y`, after previewing it with `--check`. Its one-line installer, `cad setup` and the plugin registration were **not** run.
6. **micromamba writes `$HOME/.conda/environments.txt`.** micromamba 2.x has no switch to turn this off, so `tools/bin/micromamba` runs it with `HOME=./.cache/home`. Its shell hook bypasses that wrapper, so `activate.sh` activates `./env` by hand (PATH, `CONDA_PREFIX`, `activate.d`) and never calls `micromamba activate`.
7. **conda-forge ParaView is broken for this env.** Both `paraview 6.2.0` osx-arm64 py313 builds (`hb4aadb3_1`, `hf8a6029_1`) link `libvtk*-9.7.1.dylib` although their metadata allows VTK 9.7.0. OCP 8.0.1.1 pins VTK 9.7.0, and ParaView 6.2.1 needs 9.7.2. After those two conda attempts I removed ParaView from `env/` and used the official ParaView 6.2.1 .app instead.
   - The first download was silently truncated (7.7 MB of 531 MB) and was re-downloaded with a size check.
   - The DMG carries a BSD licence agreement, which I accepted non-interactively.
8. **ccx2paraview 3.2.0 crashes with NumPy 2.** Its principal-stress step sorts complex eigenvalues. I replaced it with a 30-line `.frd` reader (`read_frd()` in `run_cantilever.py`). The package stays installed but shouldn't be relied on.
9. **OpenFOAM mount point.** OpenFOAM's own binaries use relative rpaths, but the bundled conda tools (bash, ncurses, MPI) hard-code `/Volumes/OpenFOAM-v2606`. Mounting at `./.cache/mnt` failed, so the launcher uses the app's normal `/Volumes` mount, which the user approved.
10. **ocp-freecad-cam needs OCP 7.x.** It imports `OCP.GCE2d.GCE2d_MakeSegment`, which OCCT 8 removed, and it imports CadQuery unconditionally. The CAM venv was therefore rebuilt on cadquery-ocp 7.9.3.1 + CadQuery 2.8.0 + build123d 0.12.0, with its own placeholder in `tools/pip-stubs/cadquery-ocp-novtk-7.9`.
    - In that venv, pip's numpy 2.4.6 and vtk 9.5.2 shadow FreeCAD's bundled numpy 1.26.4 and vtk. FreeCAD and CAM work. If another FreeCAD workbench misbehaves in `cam-python`, suspect this first.
11. **FreeCAD writes outside `$HOME`.** It always creates an empty `~/Library/Caches/FreeCAD/v1-1`, because macOS resolves Caches through the system, not `$HOME`. The FreeCAD wrappers (`cam-python`, `freecad`, `freecadcmd`) remove it after each run if it didn't exist beforehand and is still empty. FreeCAD's config and data go to `.cache/freecad`.
12. **Packages that couldn't be resolved from documentation alone** were checked against conda-forge/PyPI/GitHub release APIs at install time (versions above). The OpenVSP Python requirements also pulled `opencv-python`, `pyautogui` and `pandas` into `env/`. `pip check` is clean, and numpy, OCP and VTK are still the conda builds.

## Skipped or not installed
- **OrcaSlicer:** the request offered PrusaSlicer *or* OrcaSlicer, and PrusaSlicer was chosen for its mature CLI. text-to-cad's `print3d` skill expects OrcaSlicer, so that skill wasn't installed; only the `cad` skill was requested.
- **text-to-cad's other skills** (fea, render, pcb, print3d, …) weren't installed; they're available in `vendor/text-to-cad/skills`. Its `fea` skill would create its own CalculiX and Netgen environments inside `.cache/cad/cache` on first use.
- **The text-to-cad CAD Viewer web client.** It's a JS bundle (`scripts/bundle/bundle.sh`) that a source install doesn't include, so the `view:` URLs that `cad run` prints won't serve a page. Snapshots and exports work.
- **OpenFOAM Foundation (openfoam.org) v14.** Its official macOS route is a Multipass VM, a system install. ESI v2606 was used instead.

## Writes outside this folder: audit

**Method:**
- I created a timestamp marker (`.setup_start_marker`, 14:09:32) before installing anything.
- At the end I checked every location the installed tools are known to write to against it: `~/.conda`, `~/.condarc`, `~/.mambarc`, `~/.cache`, `~/.local`, `~/.config` (including ParaView), `~/.matplotlib`, `~/.gmshrc`, `~/.OpenFOAM`, `~/OpenFOAM`, `~/.ipython`, `~/.docker`, `~/.npm`, shell profiles, `~/.gitconfig`, and `~/Library/{Application Support,Preferences,Caches}/{FreeCAD,PrusaSlicer,ParaView,cad,OpenVSP,pip,uv,…}`.
- I also checked `/Applications`, `/usr/local`, `/opt/homebrew` and the macOS temp folder.
- A blanket `find ~ -newer` was too noisy to use, because other apps on the machine are active.

**Exceptions:**
1. **`~/.conda/environments.txt` was modified at 14:34:55. NOT reverted.** The first `micromamba activate` (via the shell hook, before the wrapper fix) appended this folder's `env` path and pruned three stale entries for `~/miniconda3` folders that no longer exist. That's not a functional loss, since they pointed at nothing. My attempt to edit the file back was blocked by the permission system. To remove the line yourself:
   ```bash
   sed -i '' '\#/ENGINEERING/env$#d' ~/.conda/environments.txt
   ```
   The cause is fixed (wrapper plus manual activation), and the timestamp stayed unchanged in later zsh and bash tests.
2. **`~/Library/Caches/FreeCAD/v1-1`** (an empty folder) was created at 15:34:48. It's removed now, and the wrappers clean it up after every run.
3. **`/var/folders/.../T/cad-engine/`** (the text-to-cad engine socket folder) was created at 14:42 and removed at 14:43. It's now redirected into `.cache/cad/engine`.
4. **`/Volumes/OpenFOAM-v2606`** is a temporary read-only mount used while OpenFOAM runs. The user approved it, it writes no files, and it's ejected now (`openfoam-eject`).
5. **Claude Code's own session files** live in `~/.claude/projects/...`. That's the agent, not the toolchain.

Nothing was written to `/Applications`, `/usr/local` or `/opt/homebrew`, and no shell profile was touched. DMGs for ParaView, PrusaSlicer and FreeCAD were mounted under `.cache/mnt` with `-nobrowse`, then unmounted.

**Isolation check:**
- In a clean shell **without** `source ./activate.sh`, none of `cad`, `gmsh`, `ccx`, `render-png`, `openfoam`, `prusa-slicer`, `cam-python`, `pvbatch`, `vsp` or `micromamba` resolve, `python3` is the system one, and `MAMBA_ROOT_PREFIX` is unset.
- **With** it, in both zsh and bash, all of them resolve to `./env/bin` or `./tools/bin`, and `TMPDIR` is `./.cache/tmp/`.

## Uninstall

Delete the folder. Also eject OpenFOAM first if it's mounted (`openfoam-eject`, or eject "OpenFOAM-v2606" in Finder), and optionally remove the `~/.conda/environments.txt` line above.

## Disk

| Item | Size |
|---|---|
| `env/` | 3.4 GB |
| `tools/cam-venv/` | 1.3 GB |
| `apps/` | FreeCAD 2.6 GB, ParaView 1.6 GB, OpenFOAM 1.1 GB, PrusaSlicer 0.35 GB, OpenVSP 0.32 GB |
| `.cache/` | 4.7 GB on disk: conda package cache (hard-linked with `env/`), 1.6 GB installers in `.cache/downloads`, 0.7 GB pip cache |

To reclaim about 3–5 GB, run `rm -rf .cache/downloads .cache/pip && micromamba clean -a -y`. The cost is a slower rebuild later.

## Rebuilding `env/` from scratch
```bash
source ./activate.sh
micromamba create -p ./env -f env.yml
pip install ./tools/pip-stubs/cadquery-ocp ./tools/pip-stubs/cadquery-ocp-novtk
pip install "build123d==0.13.0" "cadquery @ git+https://github.com/CadQuery/cadquery@b3f9afd4f39cbd856d0c46aa30aa37b714b8f6cb" \
    "./vendor/text-to-cad/packages/cad[build123d,trimesh]" ./vendor/text-to-cad/packages/cad-server
pip install "multimethod>=2.1,<3" runtype "pyparsing>=3" "ezdxf>=1.3"
cp tools/patches/zz-conda-ocp-eager.pth "$(python -c 'import sysconfig;print(sysconfig.get_paths()["purelib"])')/"
(cd apps/OpenVSP-3.56.0-MacOS/python && pip install -r requirements.txt)
cad doctor --check && cad doctor -y
```
(These are the exact steps that were run, in order. They haven't been re-run as a script.)

## Open items for the user
- **Optional:** remove the `~/.conda/environments.txt` line (command above).
- **Xcode licence:** it isn't accepted. Nothing here needed it, but compiling OpenFOAM solvers (`wmake`) or other native code with the Xcode toolchain will. That's a system action, `sudo xcodebuild -license`, so it's yours to decide.
- **Docker client** (20.10.23, daemon not running) is outdated. It isn't used by this toolchain.
