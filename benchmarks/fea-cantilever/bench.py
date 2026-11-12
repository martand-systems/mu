"""FEA: Gmsh C3D10 + CalculiX via mu.fea. 200 x 20 x 10 mm Al cantilever, 100 N tip load through a rigid tip.
Known answers: Timoshenko tip deflection, Mc/I top-fibre stress at mid-span, reaction = load, mesh convergence."""
import pathlib, sys
import numpy as np
sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))
from _common import check

L, b, h, F = 200.0, 20.0, 10.0, 100.0
MAT = {"al": dict(E=70000.0, nu=0.33, rho=2.70e-9, allow=200.0, fos=1.5, name="Al (benchmark)")}

def run(wd, quick):
    import build123d as bd
    from mu import fea
    E, nu = MAT["al"]["E"], MAT["al"]["nu"]
    I, A, G = b * h**3 / 12, b * h, E / (2 * (1 + nu))
    d_eb = F * L**3 / (3 * E * I)
    d_tim = d_eb + F * L / (5 / 6 * G * A)
    s_mid = F * (L / 2) * (h / 2) / I
    rows = []
    for size in ((5.0,) if quick else (5.0, 2.5)):
        m = fea.Model(f"cant_{size:g}".replace(".", "p"), wd / f"h{size:g}", MAT)
        m.add("beam", bd.Box(L, b, h, align=(bd.Align.MIN, bd.Align.CENTER, bd.Align.CENTER)), "al", size)
        m.mesh()
        m.nset("ROOT", lambda x: np.abs(x[:, 0]) < 1e-6)
        m.nset("TIP", lambda x: np.abs(x[:, 0] - L) < 1e-6)
        m.fix("ROOT")
        ref, _ = m.rigid("tip", "TIP", (L, 0, 0))
        m.static("tip", [(ref, 3, F)])
        m.run()
        u, _ = m.ref_u("tip")
        S = m.nodal("STRESS")
        band = (np.abs(m.xyz[:, 2] - h / 2) < 1e-6) & (np.abs(m.xyz[:, 0] - L / 2) < 6)
        rows.append(dict(size=size, nodes=m.n_nodes, tip=float(u[2]), s_top=float(np.mean(np.abs(S[band, 0]))),
                         reaction_z=float(m.reaction("ROOT")[2]), solve_s=m.solve_s))
    f = rows[-1]
    c = [check("tip_vs_timoshenko", f["tip"], d_tim, 0.015),
         check("tip_vs_euler_bernoulli", f["tip"], d_eb, 0.015),
         check("midspan_top_fibre_stress_vs_Mc/I", f["s_top"], s_mid, 0.02),
         check("reaction_equals_load", abs(f["reaction_z"]), F, 1e-3)]
    if len(rows) > 1:
        c.append(check("mesh_convergence_tip_change", abs(rows[-1]["tip"] - rows[-2]["tip"]) / rows[-1]["tip"], 0.0, 0.01, rel=False))
    m = {f"tip_h{r['size']:g}": r["tip"] for r in rows} | {f"nodes_h{r['size']:g}": r["nodes"] for r in rows} | \
        dict(d_eb=d_eb, d_tim=d_tim, s_mid_theory=s_mid, s_top=f["s_top"])
    return dict(passed=all(x["ok"] for x in c), checks=c, metrics=m)
