"""mu doctor: check that every tool in the toolchain resolves and starts. `mu doctor [--json]`"""
import json, os, shutil, subprocess, sys, time

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
    ("cad-registry", ["cad", "doctor", "--check"], "registered"),
    ("render-png",   ["render-png", "--help"], None),
    ("vspaero",      ["vspaero", "-help"], None),
    ("pvbatch",      ["pvbatch", "--version"], "6.2.1"),
    ("prusa-slicer", ["prusa-slicer", "--help"], "2.9.7"),
    ("cam-python",   ["cam-python", "-c", "import FreeCAD,ocp_freecad_cam,cadquery;print(FreeCAD.Version()[0]+'.'+FreeCAD.Version()[1], cadquery.__version__)"], "2.8"),
    ("openfoam",     ["openfoam", "-c", "echo $WM_PROJECT_VERSION"], "2606"),
    ("git-lfs",      ["git-lfs", "version"], "git-lfs/"),
]

def run_one(name, cmd, expect):
    t0 = time.time()
    if not shutil.which(cmd[0]) and not os.path.isabs(cmd[0]):
        return dict(name=name, ok=False, detail=f"{cmd[0]} not on PATH", seconds=0.0)
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        out = (p.stdout + p.stderr).strip()
        ok = (expect in out) if expect else p.returncode == 0
        if expect is None and name == "vspaero":
            ok = "VSPAERO" in out.upper() or p.returncode == 0
        detail = out.splitlines()[0][:100] if out else f"exit {p.returncode}"
        if not ok:
            detail = f"exit {p.returncode}: " + (out[-300:] if out else "")
    except Exception as e:  # noqa: BLE001
        ok, detail = False, repr(e)
    return dict(name=name, ok=ok, detail=detail, seconds=round(time.time() - t0, 2))

def main():
    as_json = "--json" in sys.argv
    res = [run_one(*c) for c in CHECKS]
    if as_json:
        print(json.dumps(dict(ok=all(r["ok"] for r in res), checks=res), indent=1))
    else:
        for r in res:
            print(f"{'PASS' if r['ok'] else 'FAIL'}  {r['name']:<13} {r['detail']}")
        print("doctor:", "all tools OK" if all(r["ok"] for r in res) else "PROBLEMS FOUND")
    sys.exit(0 if all(r["ok"] for r in res) else 1)

if __name__ == "__main__":
    main()
