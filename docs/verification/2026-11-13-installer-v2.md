# Verification: installer v2 (v2026.11.3), 2026-11-13

All installs below ran on the M1 MacBook (16 GB, macOS 27) with a fresh clone, in a clean environment
(`env -i`, `PATH=/usr/bin:/bin:/usr/sbin:/sbin`) and with downloads already in the cache. A first install on a new Mac
also downloads about 3 GB.

## Before / after

| | v2026.11.2 installer | v2026.11.3 installer |
|---|---|---|
| Install, empty store | 310 s + ~40 s benchmarks = **~350 s** | **132 s** including doctor and benchmarks |
| Another version, same locks | ~350 s (everything rebuilt and copied) | **22 s** (install ~1 s, doctor 9 s, benchmarks 12 s) |
| New disk per version | 9.3 GB | first: 6.8 GB; later, same locks: **~20 MB** |
| `mu doctor` | 23 s | 9 s (parallel) |
| Benchmark suite | ~40 s | 12 s (parallel; 33 s on the very first run on a Mac, see below) |
| Network needed with warm caches | yes: pip needed PyPI for build dependencies, and a captive portal broke the baseline run | no: every step is offline first |

### Where the old installer spent its time (warm caches)

| Stage | Seconds | What changed |
|---|---|---|
| Apps (copy out of the DMGs) | 158 | FreeCAD's DMG is bzip2. hdiutil decompresses it on one core (139 s), and parallel reads don't help. `install/dmg2raw.py` expands the UDIF chunks on every core into a raw image (10 s), which attaches without decompression (copy: 18 s). The result is byte-identical (`diff -r`) to the hdiutil copy. Apps unpack in background jobs, and each runs once per Mac (store) |
| conda env | 47 | Still ~45–55 s: 70k files hard-linked, 966 prefix rewrites, 259 binaries re-signed. Safety checks off saves only 8 s, and `--no-pyc` saves nothing. The step is now off the critical path, and it is skipped entirely when the lock is unchanged |
| pip layer | 43 | `uv` (pinned): offline first, with APFS clones from its cache |
| CAM venv | 36 | uv, and it builds in the background as soon as FreeCAD is unpacked |
| doctor | 23 | parallel: 9 s |

## Correctness checks

- **Doctor:** 21/21 checks pass, including the new one that every layer the version links to is complete and sealed.
- **Benchmarks:** 6/6 pass, including the regression checks against the legacy toolchain's reference values.
  - The text-to-cad benchmark takes ~24 s longer the first time a new environment layer is used on a Mac: the cad
    engine starts for the first time, and macOS checks newly written binaries on their first load.
- **Immutability:** the old copy-based store had been modified at run time.
  - FreeCAD wrote 1,310 `.pyc` files and ParaView 213 into their bundles.
  - The pip build wrote `egg-info` into OpenVSP's folder.
  - Layers are now `chmod -R a-w` once built, app Pythons cache bytecode in `state/pycache`, and OpenVSP's packages
    build from a copy.
- **Integrity:** the 71 + 66 PyPI pins now carry SHA-256 hashes and install with `--require-hashes`. URL pins are
  commit-pinned archives or files from SHA-256-pinned apps.

## Review

An independent review of the installer and dispatcher found 8 confirmed and 7 possible problems. All were fixed before
the release. The main ones:
- project paths with spaces defeated pin protection in `mu remove` / `mu gc`
- `--rebuild` could delete layers other versions use
- fragile `mu.toml` parsing (no-space assignments, CRLF)
- orphaned download processes after Ctrl-C
- unvalidated version names reaching `rm -rf`
- a missing timeout on benchmark processes

A test against a throwaway `MU_HOME` (spaces, CRLF, `../` names, pins) confirmed the fixes.
