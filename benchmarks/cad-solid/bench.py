"""CAD: build123d + CadQuery solids, STEP/STL round trip, headless render. Known answer: analytic plate volume."""
import pathlib, subprocess, sys
sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))
from _common import check, plate, PLATE_VOLUME, sh

def run(wd, quick):
    import build123d as bd
    import cadquery as cq
    p = plate(bd)
    bb = p.bounding_box()
    step, stl, png = wd / "plate.step", wd / "plate.stl", wd / "plate.png"
    bd.export_step(p, str(step)); bd.export_stl(p, str(stl))
    back = bd.import_step(str(step))
    box = cq.Workplane("XY").box(10, 20, 30).val()
    sh(["render-png", str(step), str(png), "--views", "iso,top"])
    c = [check("valid", p.is_valid, None),
         check("solids", len(p.solids()), 1, 0),
         check("volume_mm3", p.volume, PLATE_VOLUME, 1e-9),
         check("bbox_x", bb.size.X, 60, 1e-9), check("bbox_y", bb.size.Y, 40, 1e-9), check("bbox_z", bb.size.Z, 6, 1e-9),
         check("step_roundtrip_volume", back.volume, PLATE_VOLUME, 1e-9),
         check("stl_written", stl.stat().st_size > 10_000, None),
         check("cadquery_box_volume", box.Volume(), 6000, 1e-9),
         check("render_png_written", png.exists() and png.stat().st_size > 5_000, None)]
    m = dict(volume=p.volume, faces=len(p.faces()), edges=len(p.edges()))
    return dict(passed=all(x["ok"] for x in c), checks=c, metrics=m)
