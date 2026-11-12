"""Headless PNG renderer for CAD/mesh/result files (PyVista + VTK, offscreen).

Usage:
  render-png MODEL OUT.png [--views iso,top,front,right] [--edges] [--scalars NAME] [--size 1600x1200]

MODEL: .step/.stp/.brep (tessellated via OCP), .stl/.obj/.ply/.vtk/.vtu/.msh/.inp...
--views: comma list; more than one -> a grid image. Default: iso,front,top,right.
--scalars: colour by a point/cell array (e.g. von Mises from FEA), with a scalar bar.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile

import pyvista as pv

pv.OFF_SCREEN = True


def load(path: str) -> pv.DataSet:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".step", ".stp", ".brep"):
        import build123d as bd

        shape = bd.import_step(path) if ext != ".brep" else bd.import_brep(path)
        with tempfile.TemporaryDirectory() as d:
            stl = os.path.join(d, "t.stl")
            bd.export_stl(shape, stl, tolerance=0.01, angular_tolerance=0.1)
            return pv.read(stl)
    if ext in (".msh", ".inp", ".frd"):
        import meshio

        return pv.from_meshio(meshio.read(path))
    return pv.read(path)


CAMERAS = {
    "iso": ("iso", None),
    "front": ("xz", False),
    "back": ("xz", True),
    "top": ("xy", False),
    "bottom": ("xy", True),
    "right": ("yz", False),
    "left": ("yz", True),
}


def set_view(p: pv.Plotter, name: str) -> None:
    plane, neg = CAMERAS[name]
    if plane == "iso":
        p.view_isometric()
    else:
        getattr(p, f"view_{plane}")(negative=bool(neg))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model")
    ap.add_argument("out")
    ap.add_argument("--views", default="iso,front,top,right")
    ap.add_argument("--edges", action="store_true", help="draw mesh edges")
    ap.add_argument("--scalars", default=None)
    ap.add_argument("--size", default="1600x1200")
    ap.add_argument("--title", default=None)
    a = ap.parse_args(argv)

    mesh = load(a.model)
    views = [v.strip() for v in a.views.split(",") if v.strip()]
    w, h = (int(x) for x in a.size.lower().split("x"))
    n = len(views)
    shape = (1, 1) if n == 1 else (2, (n + 1) // 2)
    p = pv.Plotter(off_screen=True, shape=shape, window_size=(w, h), border=False)
    for i, v in enumerate(views):
        p.subplot(*divmod(i, shape[1])) if n > 1 else None
        kw = dict(show_edges=a.edges, smooth_shading=False)
        if a.scalars:
            p.add_mesh(mesh, scalars=a.scalars, cmap="turbo", scalar_bar_args={"title": a.scalars}, **kw)
        else:
            p.add_mesh(mesh, color="#9db4cf", specular=0.3, **kw)
        p.add_text(v + (f"  |  {a.title}" if a.title and i == 0 else ""), font_size=10)
        p.show_axes()
        set_view(p, v)
        if v != "iso":
            p.enable_parallel_projection()  # true orthographic for side views
        p.reset_camera()
    p.set_background("white")
    p.screenshot(a.out)
    b = mesh.bounds
    print(f"wrote {a.out}  ({n} views)  bounds x[{b[0]:.2f},{b[1]:.2f}] y[{b[2]:.2f},{b[3]:.2f}] z[{b[4]:.2f},{b[5]:.2f}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
