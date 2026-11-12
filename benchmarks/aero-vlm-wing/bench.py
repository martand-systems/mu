"""Aero: OpenVSP API + VSPAERO VLM. Rectangular wing, AR 6, alpha 0..6 deg.
Known answer: lift-curve slope vs an independent flat-plate vortex-lattice solution (reference_vlm.py, ~4.23 /rad).
The Helmbold formula assumes elliptic loading and overpredicts a rectangular wing by ~7%, so it is only a sanity band."""
import math, os, pathlib, shutil, sys
sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))
from _common import check

def run(wd, quick):
    import openvsp as vsp
    vsp.VSPRenew()
    vsp.SetVSPAEROPath(os.environ.get("MU_VSPAERO_DIR") or str(pathlib.Path(shutil.which("vspaero")).resolve().parent))
    span, chord = 6.0, 1.0                          # AR 6
    w = vsp.AddGeom("WING")
    vsp.SetDriverGroup(w, 1, vsp.SPAN_WSECT_DRIVER, vsp.ROOTC_WSECT_DRIVER, vsp.TIPC_WSECT_DRIVER)
    vsp.SetParmVal(w, "Span", "XSec_1", span / 2)
    vsp.SetParmVal(w, "Root_Chord", "XSec_1", chord)
    vsp.SetParmVal(w, "Tip_Chord", "XSec_1", chord)
    vsp.SetParmVal(w, "Sweep", "XSec_1", 0.0)
    vsp.SetParmVal(w, "SectTess_U", "XSec_1", 24)
    vsp.SetParmVal(w, "Tess_W", "Shape", 25)
    vsp.Update()
    fname = str(wd / "wing.vsp3")
    vsp.SetVSP3FileName(fname); vsp.WriteVSPFile(fname)
    for an in ("VSPAEROComputeGeometry", "VSPAEROSweep"):
        vsp.SetAnalysisInputDefaults(an)
        vsp.SetIntAnalysisInput(an, "GeomSet", [vsp.SET_NONE])
        vsp.SetIntAnalysisInput(an, "ThinGeomSet", [vsp.SET_ALL])
    vsp.ExecAnalysis("VSPAEROComputeGeometry")
    an = "VSPAEROSweep"
    vsp.SetDoubleAnalysisInput(an, "Sref", [span * chord])
    vsp.SetDoubleAnalysisInput(an, "bref", [span])
    vsp.SetDoubleAnalysisInput(an, "cref", [chord])
    vsp.SetDoubleAnalysisInput(an, "AlphaStart", [0.0]); vsp.SetDoubleAnalysisInput(an, "AlphaEnd", [6.0])
    vsp.SetIntAnalysisInput(an, "AlphaNpts", [4])
    vsp.SetDoubleAnalysisInput(an, "MachStart", [0.0]); vsp.SetIntAnalysisInput(an, "MachNpts", [1])
    vsp.SetIntAnalysisInput(an, "WakeNumIter", [3])
    rid = vsp.ExecAnalysis(an)
    polar = vsp.FindLatestResultsID("VSPAERO_Polar")
    alpha = list(vsp.GetDoubleResults(polar, "Alpha"))
    cl = list(vsp.GetDoubleResults(polar, "CLtot")) or list(vsp.GetDoubleResults(polar, "CL"))
    n = len(alpha)
    ma, mc = sum(alpha) / n, sum(cl) / n
    slope_deg = sum((a - ma) * (c - mc) for a, c in zip(alpha, cl)) / sum((a - ma) ** 2 for a in alpha)
    cla = slope_deg * 180 / math.pi
    AR = span / chord
    helmbold = 2 * math.pi * AR / (2 + math.sqrt(AR**2 + 4))
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from reference_vlm import cla as ref_cla
    ref = ref_cla(AR=AR, ns=80, nc=16)
    c = [check("points", n, 4, 0), check("CL_at_0deg_near_zero", abs(cl[0]), 0.0, 0.01, rel=False),
         check("CLalpha_vs_independent_VLM", cla, ref, 0.03),
         check("CLalpha_within_10%_of_Helmbold", cla, helmbold, 0.10)]
    return dict(passed=all(x["ok"] for x in c), checks=c,
                metrics=dict(CLalpha_per_rad=cla, reference_vlm=ref, helmbold=helmbold, CL=cl, alpha=alpha))
