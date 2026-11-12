"""CNC: FreeCAD CAM via ocp-freecad-cam in the separate CAM env (OCP 7.9), from a STEP made in the main env (OCP 8).
Known answers: pocket floor at Z -3, through cuts to Z -6, no cut below the stock, G-code produced."""
import pathlib, re, sys
sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))
from _common import check, plate, sh

def run(wd, quick):
    import build123d as bd
    step, nc = wd / "plate.step", wd / "plate_grbl.nc"
    bd.export_step(plate(bd), str(step))
    sh(["cam-python", str(pathlib.Path(__file__).with_name("cam_job.py")), str(step), str(nc)])
    g = nc.read_text()
    zs = [float(z) for z in re.findall(r"Z(-?\d+\.?\d*)", g)]
    cut = [z for z in zs if z < 0]
    c = [check("gcode_written", len(g.splitlines()) > 100, None),
         check("deepest_cut_mm", min(cut) if cut else None, -6.0, 0.01, rel=False),
         check("pocket_floor_present", any(abs(z + 3.0) < 1e-3 for z in cut), None),
         check("never_below_stock", min(zs) >= -6.0 - 1e-6 if zs else False, None)]
    return dict(passed=all(x["ok"] for x in c), checks=c, metrics=dict(lines=len(g.splitlines()), deepest=min(cut) if cut else None))
