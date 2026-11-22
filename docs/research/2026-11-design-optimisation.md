# Design optimisation for μ: research note

> Research snapshot of 2026-11-22, written for §7.17 and phase E8 of
> [the engineering roadmap](../ENGINEERING-ROADMAP.md). A second, smaller pass than
> [the first report](2026-11-engineering-landscape.md): two researchers, about an hour.

**How far to trust it.** Only the CalculiX 2.23 manual and a few project pages were read in full. Large papers failed to
download, so most of the literature below was seen as abstracts or search-result summaries. No code was run. Each item
carries one of these labels:

| Label | Meaning |
|---|---|
| **read** | The primary page or document was opened and read |
| **summary** | Seen only as an abstract or a search-result summary |
| **inference** | The researchers' or the author's reasoning, not a source's statement |

## 1. Topology optimisation is a classical algorithm, not an AI method

The standard methods are deterministic numerical algorithms that run finite-element solves in a loop:

- **SIMP** (density with penalisation): each element gets a density between 0 and 1; a gradient-based optimiser changes
  the densities to minimise compliance (maximise stiffness) under a volume limit; a filter and a projection keep the
  result free of checkerboards and mesh dependence. A comparative study calls it one of the most widely used methods
  (summary).
- **BESO** (evolutionary): elements are removed and added by a ranking rule. Simpler, heuristic, no optimality guarantee
  (read, from the BESO project's own description).
- **Level set**: the boundary itself is moved. Crisp boundaries, more complex to implement (summary).

In μ the language model would state the problem and rebuild the result as CAD; the algorithm does the optimisation.

## 2. What the open-source stack offers

**CalculiX 2.23 itself (read, manual).**
- `*SENSITIVITY` with `*DESIGN VARIABLES` gives shape sensitivity (surface nodes move along the normal) or
  material-orientation sensitivity. Nothing is density-based.
- Design responses: displacement, eigenfrequency, mass, strain energy, and von Mises, principal and modal stress as
  Kreisselmeier-Steinhauser aggregates. One objective; linearised constraints; geometric constraints for growth,
  shrinkage, packaging and member size.
- `*FEASIBLE DIRECTION` returns a constrained normal-motion field and writes a mesh-morphing input deck.
- The manual states that sensitivity "only works for true 3D-elements", and that its own worked example is manual and
  "by no way optimized": the loop, step size, remeshing and stopping are left to a driver script. Whether 10-node
  tetrahedra work well in the sensitivity step is not stated.
- Element internal energy (`ELSE`) and energy density (`ENER`) are available as output, which is what a compliance
  sensitivity needs (the use of them for that is an inference).

**Topology optimisers coupled to CalculiX.**
- [calculix/beso](https://github.com/calculix/beso): BESO, LGPL-3.0, needs CalculiX 2.17 or later and numpy (FreeCAD only
  for its GUI), driven by a configuration file. 97 commits, no releases; the last commit seen was 3 April 2025 (read).
- Its [description](https://github.com/calculix/beso/wiki/Basic-description) says: it targets a mass and stops on a
  count of failing elements; sensitivity is a failure index over density; checkerboarding is "not fully solved";
  manufacturing constraints beyond filters are "not implemented"; several load cases are supported; second-order
  elements use first-order approximations (read).
- No maintained density-based (SIMP) code coupled to CalculiX was found. PrePoMax has no topology optimisation
  (summary).

**Stand-alone libraries.**

| Library | Licence | Notes | Label |
|---|---|---|---|
| [pyMOTO](https://github.com/aatmdelissen/pyMOTO) 2.0.1 | MIT | numpy, scipy, sympy, matplotlib. Density filters, robust formulations, stress constraints, MMA. Appears voxel-oriented | read (package metadata and project page) |
| [PyTopo3D](https://arxiv.org/html/2504.05604v1) 0.3.0 | MIT | Voxel hexahedra. Published benchmark: 8,192 elements, 200 iterations, 147 s on a laptop | read |
| [FEniTop](https://github.com/missionlab/fenitop) | GPL-3.0 | Unstructured meshes, Helmholtz filter, MMA. Needs FEniCSx: a heavy install | read |
| Kratos Multiphysics | BSD-style (from memory) | Shape and topology applications on unstructured meshes. Heavy install; macOS arm64 wheels unconfirmed | summary |
| TopOpt in PETSc | not stated | Structured grids only; C++ and MPI | read |
| jax-fem, DL4TO, cashocs | GPL or needs torch or FEniCS | Not suited to a lean stack | summary |

**Optimisers.**
- NLopt, already pinned in μ, provides `LD_MMA` and `LD_CCSAQ`: its own globally convergent implementation of the
  Method of Moving Asymptotes, the usual optimiser for density methods
  ([NLopt algorithms](https://nlopt.readthedocs.io/en/latest/NLopt_Algorithms/), read).
- The Python MMA package `mmapy` is GPL-3.0, so it should not be imported (read).
- For parametric sizing with expensive solves: scipy's Sobol sampling and scikit-learn's Gaussian processes are already
  pinned. Optuna (MIT), pymoo (Apache-2.0) and OpenMDAO (Apache-2.0) are light additions. BoTorch and Ax need torch.
  scikit-optimize's upstream repository is read-only (read, package metadata).

**Not found:** a minimal SIMP implementation on unstructured tetrahedral meshes with scipy; measured run times for
50,000 to 500,000 tetrahedra; an open-source route from an organic mesh to clean B-rep CAD.

## 3. Practice

- **Stiffness for layout, stress afterwards.** Stress-constrained topology optimisation is difficult: stresses are
  singular at re-entrant features and local constraints must be aggregated (Kreisselmeier-Steinhauser or p-norm). The
  robust form is compliance under a volume constraint, with stress verified later (summary; the safe-default
  recommendation is an inference).
- **Pitfalls and controls.** Checkerboards, mesh dependence and intermediate "grey" density are controlled by
  filtering, projection and continuation; results still depend on the starting point and parameters. Length-scale
  control is treated in [Lazarov et al.](https://core.ac.uk/display/84003548) (summary).
- **Manufacturing constraints** exist in the literature: Langelaar's
  [overhang filter](https://link.springer.com/article/10.1007/s00158-016-1522-2) for printing (45° critical angle) and
  his milling-access filter, among others (summary). None was found in a tool μ could adopt, and no quantified study of
  the gap between an optimised result and a part a 3-axis shop will make was found.
- **Interpretation.** Altair's and Dassault's documentation describe extracting and smoothing an iso-surface, then
  interpreting and re-verifying, and warn that a density threshold alone "can be misleading" and that smoothing deviates
  from the optimised field
  ([Altair](https://help.altair.com/hwsolvers/os/topics/solvers/os/ossmooth_design_interpretation_r.htm), summary).
- **Mass savings.** Published figures are mostly optimiser-stage. Entries to the
  [GE bracket challenge](https://grabcad.com/challenges/ge-jet-engine-bracket-challenge) ranged from 7 % to 96 % mass
  reduction against a 2,033 g baseline, as entrants' own claims (summary). No independent figure for what survives
  reconstruction and verification was found.
- **Composites.** Laminates are optimised by ply thickness and angle, lamination parameters or discrete material
  optimisation ([Lund](https://vbn.aau.dk/ws/files/266072783/Lund_2017_SMO_DMTO_Failure_preprint.pdf), summary), not
  by isotropic density methods. Strength-based optimisation of anisotropic printed parts is less mature than
  stiffness-based (summary).
- **Benchmarks.** Exact truss solutions for standard problems are published by
  [Rozvany](https://link.springer.com/doi/10.1007/BF01197436) and Lewiński (summary). The analytic values themselves,
  and the reference compliances of the 88-line code, were not retrieved.
- **Standards.** [NASA-STD-6030](https://standards.nasa.gov/standard/nasa/nasa-std-6030) covers additive manufacturing
  for spaceflight (summary). ISO/ASTM 52910 and 52911 were not retrieved. Nothing found addresses the verification of
  optimised designs specifically.

## 4. AI and optimisation

- A May 2026 preprint describes [a multi-agent LLM framework](https://arxiv.org/abs/2605.23273) that formulates,
  validates and runs topology optimisation with self-refinement. Its abstract gives no success rates and says
  engineers must still judge whether a converged design is physically feasible (read, abstract only).
- [Woldseth et al. (2022)](https://arxiv.org/abs/2208.02563), from the group behind much of the field's standard
  work, review neural networks used in place of the iterative solver and find few real advances (read, abstract only).
- No published study of agents mis-defining an optimisation problem was found.

## 5. Gaps

Not retrieved or not researched: the primary texts of the standard references (Bendsøe and Sigmund; Sigmund and Maute
2013; Le et al. 2010); analytic benchmark values; commercial reconstruction tools; independent comparisons of commercial
generative design; hole and cut-out rules for laminates; anisotropy data for powder-bed polymers; ToPy, TopOpt.jl and
several other codes. GitHub's API was not reachable, so most last-commit dates are missing.
