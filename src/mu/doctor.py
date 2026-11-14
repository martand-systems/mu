"""mu doctor: check that every tool in the toolchain resolves and starts. `mu doctor [--json]`"""
import json, os, re, shutil, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor

CHECKS = [
    # name, command, substring expected in stdout+stderr (None = exit code 0 is enough)
    ("python",       [sys.executable, "-c", "import sys;print(sys.version.split()[0])"], "3.13"),
    ("ocp",          [sys.executable, "-c", "import OCP,OCP.gp;print('ok')"], "ok"),
    ("build123d",    [sys.executable, "-c", "import build123d;print(build123d.__version__)"], "0.13"),
    ("cadquery",     [sys.executable, "-c", "import cadquery;print(cadquery.__version__)"], "2.9"),
    ("gmsh-api",     [sys.executable, "-c", "import gmsh;print(gmsh.__version__)"], "4.15"),
    ("vtk",          [sys.executable, "-c", "import vtk;print(vtk.vtkVersion.GetVTKVersion())"], "9.7.0"),
    ("pyvista",      [sys.executable, "-c", "import pyvista;print(pyvista.__version__)"], None),
    ("openvsp-api",  [sys.executable, "-c", "import openvsp as v;print(v.GetVSPVersion())"], "3.56"),
    ("mu.fea",       [sys.executable, "-c", "import mu.fea;print('ok')"], "ok"),
    ("gmsh",         ["gmsh", "--version"], "4.15"),
    ("ccx",          ["ccx", "-v"], "2.23"),
    ("cad",          ["cad", "doctor", "--check"], None),
    # text-to-cad's "current" installation must be this toolchain's env (per-version state; see lib/mu-env.sh)
    ("cad-registry", ["cad", "doctor", "--check"], lambda out: _env_registered(out)),
    ("render-png",   ["render-png", "--help"], None),
    ("vspaero",      ["vspaero", "-help"], None),
    ("pvbatch",      ["pvbatch", "--version"], "6.2.1"),
    ("prusa-slicer", ["prusa-slicer", "--help"], "2.9.7"),
    ("cam-python",   ["cam-python", "-c", "import FreeCAD,ocp_freecad_cam,cadquery;print(FreeCAD.Version()[0]+'.'+FreeCAD.Version()[1], cadquery.__version__)"], "2.8"),
    ("openfoam",     ["openfoam", "-c", "echo $WM_PROJECT_VERSION"], "2606"),
    ("git-lfs",      ["git-lfs", "version"], "git-lfs/"),
    # the job layer's relay client (docs/design/job-layer.md §5.4)
    ("chisel",       [os.path.join(os.environ.get("MU_TOOLCHAIN", ""), "libexec", "chisel"), "--version"], "1.12.0"),
]

def _env_registered(out):
    """`cad doctor --check` prints "current  cad-server X (<prefix>)": that prefix must be this toolchain's env."""
    m = re.search(r"^\s*current\s.*\((.+)\)\s*$", out, re.M)
    env = os.path.join(os.environ.get("MU_TOOLCHAIN", "?"), "env")
    return bool(m) and os.path.realpath(os.path.expanduser(m.group(1))) == os.path.realpath(env)

_cache = {}
def _run(cmd):
    """Run a command once (cad doctor --check feeds two checks)."""
    key = tuple(cmd)
    if key not in _cache:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        _cache[key] = (p.returncode, (p.stdout + p.stderr).strip())
    return _cache[key]

def check_layers():
    """Every store layer this toolchain links to is complete (marker) and sealed (read-only)."""
    t = os.environ.get("MU_TOOLCHAIN", "")
    lf = os.path.join(t, "LAYERS")
    if not os.path.exists(lf):
        return dict(name="layers", ok=True, detail="no LAYERS file (toolchain predates the shared store)", seconds=0.0)
    store = os.path.join(os.environ.get("MU_HOME", os.path.dirname(os.path.dirname(t))), "store")
    bad = []
    keys = open(lf).read().split()
    for k in keys:
        d = os.path.join(store, k)
        if not os.path.exists(os.path.join(d, ".mu-layer")):
            bad.append(f"{k} missing")
        elif os.access(d, os.W_OK):
            bad.append(f"{k} writable")
    return dict(name="layers", ok=not bad, detail=("; ".join(bad) if bad else f"{len(keys)} layers complete and sealed"),
                seconds=0.0)

def check_linux_libs():
    """Linux: every app binary finds its shared libraries (a missing one names the Ubuntu package to install)."""
    t = os.environ.get("MU_TOOLCHAIN", "")
    bins = ["apps/ParaView-6.2.1/lib/libvtkRenderingOpenGL2-pv6.2.so*", "apps/OpenVSP-3.56.0-Linux/vspaero",
            "apps/openfoam2606/platforms/*/bin/simpleFoam", "apps/openfoam2606/platforms/*/lib/libOpenFOAM.so",
            "cam-venv/lib/python3.11/site-packages/OCP/OCP*.so"]
    import glob
    missing = set()
    for pat in bins:
        for f in glob.glob(os.path.join(t, pat)):
            env = dict(os.environ)
            if "openfoam2606" in f:   # OpenFOAM's own libraries are found through its environment, not RPATH
                root = f.split("/platforms/")[0] + "/platforms/" + f.split("/platforms/")[1].split("/")[0]
                env["LD_LIBRARY_PATH"] = f"{root}/lib:{root}/lib/sys-openmpi:{root}/lib/dummy"
            out = subprocess.run(["ldd", f], capture_output=True, text=True, env=env).stdout
            missing |= {l.split()[0] for l in out.splitlines() if "not found" in l}
    return dict(name="linux-libs", ok=not missing, seconds=0.0,
                detail=("all app libraries found" if not missing else
                        "missing " + " ".join(sorted(missing)) + ": run sudo ./install.sh --system in the mu repo"))

def run_one(name, cmd, expect):
    t0 = time.time()
    if not shutil.which(cmd[0]) and not os.path.isabs(cmd[0]):
        return dict(name=name, ok=False, detail=f"{cmd[0]} not on PATH", seconds=0.0)
    try:
        rc, out = _run(cmd)
        ok = expect(out) if callable(expect) else (expect in out) if expect else rc == 0
        if expect is None and name == "vspaero":
            ok = "VSPAERO" in out.upper() or rc == 0
        detail = out.splitlines()[0][:100] if out else f"exit {rc}"
        if not ok:
            detail = f"exit {rc}: " + (out[-300:] if out else "")
    except Exception as e:  # noqa: BLE001
        ok, detail = False, repr(e)
    return dict(name=name, ok=ok, detail=detail, seconds=round(time.time() - t0, 2))

def main():
    as_json = "--json" in sys.argv
    # every check starts its own process: run them side by side (the cad pair shares one run)
    try:
        _run(["cad", "doctor", "--check"])
    except Exception:  # noqa: BLE001  (reported by the cad checks themselves)
        pass
    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4)) as ex:
        res = [check_layers()] + ([check_linux_libs()] if sys.platform.startswith("linux") else []) \
            + list(ex.map(lambda c: run_one(*c), CHECKS))
    if as_json:
        print(json.dumps(dict(ok=all(r["ok"] for r in res), checks=res), indent=1))
    else:
        for r in res:
            print(f"{'PASS' if r['ok'] else 'FAIL'}  {r['name']:<13} {r['detail']}")
        print("doctor:", "all tools OK" if all(r["ok"] for r in res) else "PROBLEMS FOUND")
    sys.exit(0 if all(r["ok"] for r in res) else 1)

if __name__ == "__main__":
    main()
