# Toolchain recipe

Exact inputs for building the μ toolchain on a device (see `docs/PLAN.md` §5.3).

| File | What |
|---|---|
| `env.yml` | Human-readable conda-forge spec for the main env |
| `osx-arm64/env.explicit.txt`, `linux-64/env.explicit.txt` | Exact conda package URLs + MD5. The macOS list was exported from the working legacy env; the Linux list is the same versions re-solved for linux-64 (`tools/lock-linux-64/`) |
| `<platform>/env.pip.txt` | Python layer of the main env, every PyPI package with its SHA-256 (`tools/hash_lock.py`). Other entries are commit-pinned archives or come from the apps |
| `<platform>/cam-venv.pip.txt`, `osx-arm64/cam-venv.python.txt` | FreeCAD CAM env (Python 3.11, OCP 7.9): its Python layer, hashed the same way |
| `linux-64/apt.txt` | The Ubuntu 24.04 packages μ needs (`sudo ./install.sh --system`) |
| `linux-64/flathub.gpg`, `linux-64/build-openvsp-api.sh`, `linux-64/pip-stubs/` | Flathub's signing key (PrusaSlicer), the on-device build of OpenVSP's Python 3.13 API, Linux-only stubs |
| `apps.toml` | Upstream apps pinned by SHA-256, per platform (PrusaSlicer on Linux by its Flathub OSTree commit) |
| `pins.toml` | Git dependencies pinned by commit |
| `patches/`, `pip-stubs/` | The one-OCP-per-env fixes (see `legacy/SETUP_REPORT.md`) |

Status (2026-11-14): fresh builds from these files pass `mu doctor` and the benchmarks on macOS arm64 (S1) and on
linux-64 (S2, and on the office PC in S3). Records: `docs/verification/`. The installer that reads them is
`install/install.sh`.
