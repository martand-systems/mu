"""CFD: OpenFOAM v2606 icoFoam lid-driven cavity tutorial (Re 10) + ParaView offscreen render.
Known answers: mesh OK (400 cells), run reaches t = 0.5, continuity error tiny, primary vortex below the lid."""
import pathlib, re, sys
sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))
from _common import check, sh

PV = '''
from paraview.simple import *
r = OpenFOAMReader(FileName=r"{case}/case.foam"); r.MeshRegions = ["internalMesh"]; r.CellArrays = ["U", "p"]
r.UpdatePipeline(max(r.TimestepValues))
v = GetActiveViewOrCreate("RenderView"); v.ViewSize = [800, 800]
d = Show(r, v); ColorBy(d, ("POINTS", "U", "Magnitude")); v.ViewTime = max(r.TimestepValues)
ResetCamera(v); SaveScreenshot(r"{png}", v)
'''

def run(wd, quick):
    case = wd / "cavity"
    sh(["openfoam", "-c", f'{{ [ ! -e "{case}" ] || chmod -R u+w "{case}"; }} && rm -rf "{case}" && cp -r "$FOAM_TUTORIALS/incompressible/icoFoam/cavity/cavity" "{case}" && chmod -R u+w "{case}" && '
                           f'cd "{case}" && blockMesh > log.blockMesh 2>&1 && checkMesh > log.checkMesh 2>&1 && icoFoam > log.icoFoam 2>&1'])
    cm = (case / "log.checkMesh").read_text(); ico = (case / "log.icoFoam").read_text()
    cells = int(re.search(r"cells:\s+(\d+)", cm).group(1))
    times = [float(t) for t in re.findall(r"^Time = ([0-9.eE+-]+)", ico, re.M)]
    cont = [abs(float(x)) for x in re.findall(r"continuity errors : sum local = ([0-9.eE+-]+)", ico)]
    (case / "case.foam").touch()
    png = wd / "cavity_U.png"
    (wd / "pv.py").write_text(PV.format(case=case, png=png))
    sh(["pvbatch", "--force-offscreen-rendering", str(wd / "pv.py")])
    c = [check("checkMesh_ok", "Mesh OK" in cm, None), check("cells", cells, 400, 0),
         check("final_time", times[-1] if times else None, 0.5, 1e-9),
         check("final_continuity_error_below_1e-6", cont[-1] if cont else None, 0.0, 1e-6, rel=False),
         check("paraview_png_written", png.exists() and png.stat().st_size > 5_000, None)]
    return dict(passed=all(x["ok"] for x in c), checks=c,
                metrics=dict(cells=cells, steps=len(times), final_continuity=cont[-1] if cont else None))
