# Toolchain recipe

Exact inputs for building the μ toolchain on a device (see `docs/PLAN.md` §5.3).

| File | What |
|---|---|
| `env.yml` | Human-readable conda-forge spec for the main env |
| `osx-arm64/env.explicit.txt` | Exact conda package URLs + MD5 for macOS arm64 (exported from the working legacy env) |
| `osx-arm64/env.pip-freeze.txt` | Pip layer of the main env. `file://` entries point to the legacy folder: the recipe replaces them with `pins.toml` sources and `pip-stubs/` |
| `osx-arm64/cam-venv.*` | FreeCAD CAM env (Python 3.11, OCP 7.9) pip layer |
| `apps.toml` | Upstream apps pinned by SHA-256 |
| `pins.toml` | Git dependencies pinned by commit |
| `patches/`, `pip-stubs/` | The one-OCP-per-env fixes (see `legacy/SETUP_REPORT.md`) |

Status: **S1 in progress.** The pins are captured from the working toolchain. The next step is a fresh build from these files into a new folder, with
the benchmarks passing.
