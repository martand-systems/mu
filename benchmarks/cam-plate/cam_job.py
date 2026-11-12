"""Run with cam-python: CAM for the benchmark plate (STEP from the main env). argv: in.step out.nc"""
import sys
import build123d as bd
from ocp_freecad_cam import Endmill, Job

part = bd.import_step(sys.argv[1])
zmax = part.bounding_box().max.Z
part = bd.Pos(0, 0, -zmax) * part                      # top face at Z = 0
top = part.faces().sort_by(bd.Axis.Z)[-1]
bottom = part.faces().sort_by(bd.Axis.Z)[0]
floor = [f for f in part.faces() if abs(f.center().Z + 3) < 1e-6 and f.normal_at().Z > 0.9]
tool = Endmill(diameter=3.0, name="3 mm endmill", speed=18000, h_feed=600, v_feed=150)
job = (Job(top, part, "grbl")
       .pocket(floor, tool, step_down=1.0)
       .profile(bottom, tool, side="out", holes=True, circles=True, perimeter=False, step_down=1.5)
       .profile(bottom, tool, side="out", holes=False, perimeter=True, step_down=1.5))
open(sys.argv[2], "w").write(job.to_gcode())
