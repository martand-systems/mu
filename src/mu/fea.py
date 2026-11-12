"""mu.fea: small FE driver (from JATAYU frame/felib.py): build123d solids -> Gmsh (conformal multi-part, 10-node tets) -> CalculiX -> results.

Units: mm, N, tonne, s, MPa. Parts that touch are fused (shared nodes) = bonded-joint idealisation.
Loads/masses enter through rigid bodies (reference node), supports through node sets picked by predicate.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import tempfile
import time

import build123d as bd
import gmsh
import numpy as np

ABQ = [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]      # gmsh tet10 -> CalculiX C3D10 node order


class Model:
    def __init__(self, name: str, workdir, materials: dict):
        self.name = name
        self.dir = pathlib.Path(workdir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.mats = materials
        self.parts = []          # (label, shape, material, size)
        self.nsets, self.rigids, self.masses, self.fixes = {}, [], [], []
        self.steps = []
        self.extra_nodes = []

    # ---------------------------------------------------------------- geometry + mesh
    def add(self, label, shape, material, size):
        self.parts.append((label, shape, material, float(size)))

    def mesh(self, refine=None, optimize=True):
        """refine: optional list of (x,y,z,radius,size) spherical refinement zones."""
        t0 = time.time()
        gmsh.initialize(["-noenv"])
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("General.NumThreads", 4)
        gmsh.model.add(self.name)
        ents = []
        with tempfile.TemporaryDirectory() as d:
            for i, (label, shape, _, _) in enumerate(self.parts):
                f = os.path.join(d, f"p{i}.brep")
                bd.export_brep(shape, f)
                dt = gmsh.model.occ.importShapes(f)
                vols = [t for t in dt if t[0] == 3]
                assert len(vols) == 1, f"{label}: expected 1 solid, got {len(vols)}"
                ents.append(vols[0])
        if len(ents) > 1:
            _, omap = gmsh.model.occ.fragment(ents[:1], ents[1:])
        else:
            omap = [ents]
        gmsh.model.occ.synchronize()
        self.vol_of = {}
        seen = set()
        for (label, _, _, _), m in zip(self.parts, omap):
            tags = [t for (dd, t) in m if dd == 3 and t not in seen]
            assert tags, f"{label}: swallowed by fragment (overlapping solids?)"
            seen.update(tags)
            self.vol_of[label] = tags
        fields = []
        for label, _, _, size in self.parts:
            fid = gmsh.model.mesh.field.add("Constant")
            gmsh.model.mesh.field.setNumber(fid, "VIn", size)
            gmsh.model.mesh.field.setNumber(fid, "VOut", 1e9)
            gmsh.model.mesh.field.setNumber(fid, "IncludeBoundary", 1)
            gmsh.model.mesh.field.setNumbers(fid, "VolumesList", self.vol_of[label])
            fields.append(fid)
        for (x, y, z, r, s) in (refine or []):
            fid = gmsh.model.mesh.field.add("Ball")
            for k, v in (("XCenter", x), ("YCenter", y), ("ZCenter", z), ("Radius", r), ("VIn", s), ("VOut", 1e9),
                         ("Thickness", r)):
                gmsh.model.mesh.field.setNumber(fid, k, v)
            fields.append(fid)
        fmin = gmsh.model.mesh.field.add("Min")
        gmsh.model.mesh.field.setNumbers(fmin, "FieldsList", fields)
        gmsh.model.mesh.field.setAsBackgroundMesh(fmin)
        for k, v in (("Mesh.MeshSizeExtendFromBoundary", 0), ("Mesh.MeshSizeFromPoints", 0),
                     ("Mesh.MeshSizeFromCurvature", 0), ("Mesh.ElementOrder", 2),
                     ("Mesh.HighOrderOptimize", 1 if optimize else 0), ("Mesh.Algorithm3D", 10),
                     ("Mesh.Algorithm", 6)):
            gmsh.option.setNumber(k, v)
        gmsh.model.mesh.generate(3)
        ntags, coords, _ = gmsh.model.mesh.getNodes()
        order = np.argsort(ntags)
        self.nid = ntags[order].astype(np.int64)
        self.xyz = coords.reshape(-1, 3)[order]
        self.conn = {}
        used = np.zeros(self.nid.max() + 1, bool)
        for label, tags in self.vol_of.items():
            cs = []
            for t in tags:
                et, _, en = gmsh.model.mesh.getElements(3, t)
                for e, n in zip(et, en):
                    if e == 11:
                        cs.append(n.astype(np.int64).reshape(-1, 10)[:, ABQ])
            self.conn[label] = np.vstack(cs)
            used[self.conn[label].ravel()] = True
        gmsh.finalize()
        keep = used[self.nid]                      # drop orphan nodes (geometry points etc.)
        self.nid, self.xyz = self.nid[keep], self.xyz[keep]
        self.index = np.full(self.nid.max() + 1, -1, np.int64)
        self.index[self.nid] = np.arange(len(self.nid))
        self.part_nodes = {k: np.unique(v) for k, v in self.conn.items()}
        self.next_node = int(self.nid.max()) + 1
        self.mesh_s = time.time() - t0
        return self

    @property
    def n_nodes(self):
        return len(self.nid)

    @property
    def n_elems(self):
        return sum(len(c) for c in self.conn.values())

    # ---------------------------------------------------------------- sets, couplings, BCs
    def nset(self, name, pred, parts=None):
        """pred(xyz ndarray) -> bool mask. parts: restrict to nodes of these part labels."""
        mask = pred(self.xyz)
        if parts is not None:
            allowed = np.zeros(len(self.nid), bool)
            for pl in parts:
                allowed[self.index[self.part_nodes[pl]]] = True
            mask &= allowed
        ids = self.nid[mask]
        assert len(ids) > 0, f"nset {name} is empty"
        self.nsets[name] = ids
        return ids

    def _new_node(self, xyz):
        n = self.next_node
        self.next_node += 1
        self.extra_nodes.append((n, xyz))
        return n

    def rigid(self, name, nset, ref_xyz):
        ref, rot = self._new_node(ref_xyz), self._new_node(ref_xyz)
        self.rigids.append((name, nset, ref, rot))
        self.nsets["REF_" + name] = np.array([ref, rot])
        return ref, rot

    def mass(self, ref, kg):
        self.masses.append((ref, kg * 1e-3))       # tonne

    def fix(self, nset, dofs=(1, 3)):
        self.fixes.append((nset, dofs))

    def static(self, name, cloads, grav=None):
        """cloads: list of (node, dof, value). grav: (g_mm_s2, (dx,dy,dz)) optional."""
        self.steps.append(("static", name, cloads, grav))

    def frequency(self, n=8):
        self.steps.append(("freq", "modal", n, None))

    # ---------------------------------------------------------------- write / run
    def write(self):
        f = open(self.dir / f"{self.name}.inp", "w")
        f.write("*HEADING\n" + self.name + "\n*NODE, NSET=NALL\n")
        for n, (x, y, z) in zip(self.nid, self.xyz):
            f.write(f"{n},{x:.8g},{y:.8g},{z:.8g}\n")
        for n, (x, y, z) in self.extra_nodes:
            f.write(f"{n},{x:.8g},{y:.8g},{z:.8g}\n")
        eid = 1
        self.eid_range = {}
        for label, c in self.conn.items():
            f.write(f"*ELEMENT, TYPE=C3D10, ELSET=E_{label}\n")
            self.eid_range[label] = (eid, eid + len(c) - 1)
            for row in c:
                f.write(f"{eid}," + ",".join(map(str, row)) + "\n")
                eid += 1
        for i, (ref, m) in enumerate(self.masses):
            f.write(f"*ELEMENT, TYPE=MASS, ELSET=EM{i}\n{eid},{ref}\n*MASS, ELSET=EM{i}\n{m:.6e}\n")
            eid += 1
        for name, ids in self.nsets.items():
            f.write(f"*NSET, NSET={name}\n")
            for k in range(0, len(ids), 12):
                f.write(",".join(map(str, ids[k:k + 12])) + "\n")
        used = sorted({m for _, _, m, _ in self.parts})
        for m in used:
            d = self.mats[m]
            f.write(f"*MATERIAL, NAME={m}\n*ELASTIC\n{d['E']},{d['nu']}\n*DENSITY\n{d['rho']:.4e}\n")
        for label, _, m, _ in self.parts:
            f.write(f"*SOLID SECTION, ELSET=E_{label}, MATERIAL={m}\n")
        for name, nset, ref, rot in self.rigids:
            f.write(f"*RIGID BODY, NSET={nset}, REF NODE={ref}, ROT NODE={rot}\n")
        f.write("*BOUNDARY\n")
        for nset, dofs in self.fixes:
            f.write(f"{nset},{dofs[0]},{dofs[1]},0.0\n")
        refsets = [k for k in self.nsets if k.startswith("REF_")]
        fixsets = sorted({n for n, _ in self.fixes if isinstance(n, str)})
        for kind, name, a, grav in self.steps:
            if kind == "static":
                f.write("*STEP\n*STATIC\n")
                if a:
                    f.write("*CLOAD, OP=NEW\n")
                    for node, dof, val in a:
                        f.write(f"{node},{dof},{val:.8g}\n")
                if grav:
                    f.write(f"*DLOAD, OP=NEW\n")
                    for label in self.conn:
                        f.write(f"E_{label},GRAV,{grav[0]},{grav[1][0]},{grav[1][1]},{grav[1][2]}\n")
                f.write("*NODE FILE\nU\n*EL FILE\nS\n")
                for r in refsets:
                    f.write(f"*NODE PRINT, NSET={r}\nU\n")
                for s in fixsets:
                    f.write(f"*NODE PRINT, NSET={s}, TOTALS=ONLY\nRF\n")
                for label in self.conn:
                    f.write(f"*EL PRINT, ELSET=E_{label}\nS\n")
                f.write("*END STEP\n")
            else:
                f.write(f"*STEP\n*FREQUENCY\n{a}\n*NODE FILE\nU\n*END STEP\n")
        f.close()

    def run(self, ncpu=4):
        self.write()
        env = dict(os.environ, OMP_NUM_THREADS=str(ncpu), CCX_NPROC_EQUATION_SOLVER=str(ncpu))
        t0 = time.time()
        r = subprocess.run(["ccx", "-i", self.name], cwd=self.dir, capture_output=True, text=True, env=env)
        (self.dir / "ccx.log").write_text(r.stdout + r.stderr)
        self.solve_s = time.time() - t0
        if not (self.dir / f"{self.name}.frd").exists() or "*ERROR" in r.stdout:
            raise RuntimeError(f"ccx failed: see {self.dir/'ccx.log'}\n" + r.stdout[-1500:])
        self.dat = parse_dat(self.dir / f"{self.name}.dat")
        self.frd = parse_frd(self.dir / f"{self.name}.frd")
        return self

    # ---------------------------------------------------------------- results
    def ref_u(self, name, step=0):
        """(translation xyz of ref node, rotation vector of rot node [rad]) for rigid body `name`."""
        _, _, ref, rot = next(r for r in self.rigids if r[0] == name)
        blk = [b for b in self.dat if b["kind"] == "displacements" and b["set"] == "REF_" + name.upper()][step]
        d = {int(r[0]): r[1:4] for r in blk["rows"]}
        return np.array(d[ref]), np.array(d[rot])

    def reaction(self, nset, step=0):
        blk = [b for b in self.dat if b["kind"] == "total" and b["set"] == nset.upper()][step]
        return np.array(blk["rows"][0][-3:])

    def ip_stress(self, label, step=0):
        """Integration-point stress tensors (n,6: xx yy zz xy xz yz) of one part."""
        blk = [b for b in self.dat if b["kind"] == "stresses" and b["set"] == ("E_" + label).upper()][step]
        return np.array(blk["rows"])[:, 2:8]

    def frequencies(self):
        return next(b["freq"] for b in self.dat if b["kind"] == "eigen")

    def nodal(self, name, step=0):
        """Nodal field from the .frd ('DISP' or 'STRESS') aligned with self.nid."""
        blk = [b for b in self.frd if b["name"] == name][step]["data"]
        return np.array([blk[int(n)] for n in self.nid])

    def grid(self, step=0, modal=False):
        import pyvista as pv
        cells, pid = [], []
        for i, (label, c) in enumerate(self.conn.items()):
            cells.append(np.hstack([np.full((len(c), 1), 10), self.index[c]]))
            pid.append(np.full(len(c), i))
        cells = np.vstack(cells)
        g = pv.UnstructuredGrid(cells.ravel(), np.full(len(cells), pv.CellType.QUADRATIC_TETRA), self.xyz.copy())
        g.cell_data["part"] = np.concatenate(pid)
        U = self.nodal("DISP", step)[:, :3]
        g.point_data["U"] = U
        g.point_data["Umag"] = np.linalg.norm(U, axis=1)
        if not modal:
            S = self.nodal("STRESS", step)[:, :6]
            g.point_data["von_Mises"] = von_mises(S)
            g.point_data["S1"] = principal(S)[:, 2]
        return g


def von_mises(s):
    xx, yy, zz, xy, a, b = s.T
    return np.sqrt(0.5 * ((xx - yy) ** 2 + (yy - zz) ** 2 + (zz - xx) ** 2) + 3 * (xy**2 + a**2 + b**2))


def principal(s):
    """Sorted principal stresses (n,3) for tensors given as xx yy zz xy xz yz (dat) - order of shear terms irrelevant."""
    t = np.zeros((len(s), 3, 3))
    t[:, 0, 0], t[:, 1, 1], t[:, 2, 2] = s[:, 0], s[:, 1], s[:, 2]
    t[:, 0, 1] = t[:, 1, 0] = s[:, 3]
    t[:, 0, 2] = t[:, 2, 0] = s[:, 4]
    t[:, 1, 2] = t[:, 2, 1] = s[:, 5]
    return np.linalg.eigvalsh(t)


def parse_dat(path):
    out, cur = [], None
    eig = False
    freqs = []
    for line in open(path):
        s = line.strip()
        if "E I G E N V A L U E   O U T P U T" in s:
            eig, cur = True, None
            continue
        if eig:
            p = s.split()
            if len(p) >= 4 and p[0].isdigit():
                try:
                    freqs.append(float(p[3]))
                except ValueError:
                    pass
                continue
            if s.startswith("P A R T I C I P A T I O N") or " for set " in s:
                eig = False
                out.append(dict(kind="eigen", freq=freqs))
            else:
                continue
        if " for set " in s and "time" in s:
            kind = s.split()[0]
            setname = s.split(" for set ")[1].split()[0]
            cur = dict(kind=kind, set=setname, rows=[])
            out.append(cur)
            continue
        if cur is not None and s:
            try:
                cur["rows"].append([float(x) for x in s.split()])
            except ValueError:
                cur = None
    if eig and freqs:
        out.append(dict(kind="eigen", freq=freqs))
    return out


def parse_frd(path):
    out, cur = [], None
    with open(path) as f:
        for line in f:
            key = line[:3]
            if key == " -4":
                cur = dict(name=line[3:].split()[0], data={})
                out.append(cur)
            elif key == " -1" and cur is not None:
                body = line.rstrip("\n")
                cur["data"][int(body[3:13])] = [float(body[13 + 12 * k:25 + 12 * k]) for k in range((len(body) - 13) // 12)]
            elif key == " -3":
                cur = None
    return out


def summarize(model, label, material, mats, step=0, composite=None):
    """Per-part peak stress at integration points + margin of safety. Composite parts use max |principal|."""
    s = model.ip_stress(label, step)
    vm = von_mises(s)
    pr = principal(s)
    m = mats[material]
    comp = material.startswith("cf") if composite is None else composite
    gov = np.abs(pr).max(axis=1) if comp else vm
    peak, p999 = float(gov.max()), float(np.percentile(gov, 99.9))
    return dict(part=label, material=material, criterion="max |principal|" if comp else "von Mises",
                peak=peak, p999=p999, allow=m["allow"], fos=m["fos"], mos=m["allow"] / (m["fos"] * peak) - 1)
