"""3D printing: PrusaSlicer CLI slices the benchmark plate (exported by build123d) with a pinned profile.
Known answers: G-code produced, layer count = 6 mm / 0.2 mm, plausible filament mass for the part volume."""
import pathlib, re, sys
sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))
from _common import check, plate, sh, PLATE_VOLUME

def run(wd, quick):
    import build123d as bd
    stl, gcode = wd / "plate.stl", wd / "plate.gcode"
    bd.export_stl(plate(bd), str(stl))
    ini = pathlib.Path(__file__).with_name("profile_PETG-CF_0.4_0.2.ini")
    sh(["prusa-slicer", "--export-gcode", "--load", str(ini), str(stl), "-o", str(gcode)])
    g = gcode.read_text()
    grams = float(re.search(r"^; filament used \[g\] = ([0-9.]+)", g, re.M).group(1))
    t = re.search(r"^; estimated printing time \(normal mode\) = (.+)$", g, re.M).group(1).strip()
    layers = len(re.findall(r"^;LAYER_CHANGE", g, re.M))
    full_solid_g = PLATE_VOLUME / 1000 * 1.29
    c = [check("gcode_written", len(g) > 10_000, None), check("layers", layers, 30, 0),
         check("filament_g_between_30%_and_110%_of_solid", 0.3 * full_solid_g <= grams <= 1.1 * full_solid_g, None),
         check("print_time_reported", bool(t), None)]
    return dict(passed=all(x["ok"] for x in c), checks=c, metrics=dict(filament_g=grams, layers=layers, print_time=t,
                                                                       gcode_lines=g.count("\n")))
