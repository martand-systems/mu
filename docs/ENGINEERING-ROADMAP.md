# μ (mu): engineering capability roadmap

- **Status:** draft v1, a proposal. It does not change the order of work in `docs/ROADMAP.md`; decision 14 in §13 asks
  where it fits
- **Date:** 2026-11-14
- **Owner:** Martand Defense
- **First project:** JATAYU
- **Reads with:** `docs/PLAN.md` (installation, compute, relay, Claude Code integration) and `docs/ROADMAP.md` (the
  order of the work in hand: pairing fixes, dashboard, shell experience). This document covers what they do not: the
  engineering analyses μ offers, and how their results become something a human engineer can check quickly and sign.
- **Evidence behind it:** a read-only review of this repo at commit `94fd2fd`, re-checked against `main` at `1fdd3b3`
  (after v2026.11.9). μ's engineering code (`src/mu/fea.py`, `src/mu/post/`, `src/mu/bench.py`, `benchmarks/`,
  `claude-plugin/`) is identical at both commits; statements about the job layer and PLAN.md describe `1fdd3b3`. And a
  research report, [`docs/research/2026-11-engineering-landscape.md`](research/2026-11-engineering-landscape.md), built
  from six sets of cited notes. Sources are listed in §14.

**How to read the evidence labels.** Statements about this repo were verified by reading the code. Nothing in this
document was verified by running the toolchain, because the review machine had no install. Where a claim rests on a
third-party tool's documented behaviour and not on a run, it is marked **(to confirm)** and has a spike in §9.3.

---

## 1. Summary

**Goal.** Claude Code, using μ, produces the analysis package a competent junior mechanical engineer would produce:
manufacturability, mass, loads, strength, joints, fatigue, tolerances, vibration, aero and thermal checks, each with its
method, sources, assumptions and margins. A human engineer then checks and signs it in a fraction of the time it would
take to do the work. Over time the agent takes on more of the planning and judgement, area by area, when measured
evidence supports it.

**Where μ is today.** μ is a reproducible installer, version manager and remote job runner around good open-source tools. Its own
engineering code is about 450 lines: a linear FEA driver and a renderer. The deepest engineering capability in the repo,
the manufacturability, interference, joint-range and mass checks in the bundled text-to-cad package, is not mentioned in
μ's own instructions and has no benchmark. Of the evidence layer that PLAN.md describes, only the job layer's run
records for remote jobs exist: local runs leave no record, and `mu check`, the skills and the hooks are not built. The
org profile that should hold policy and materials is not visible from this repo.

**What the research says.** Three findings decide the design:

1. **"It ran" is not "it is right".** One CFD agent reports 88.2 % success in its own paper, where success means the
   case executed; an independent benchmark that requires the result to match a reference scores the same agent at
   33.6 %. A finite-element benchmark found 88 % executable output and no fully correct solution. These are 2024 and
   2025 results on older models; the gap, not the values, is the lesson. μ must never report a single success flag.
2. **The reliable systems constrain what the model writes.** One published system has the model fill a validated
   specification and leaves the solver input to human-written templates; it gets under 1 % error on standard
   linear-elastic cases. The benchmark failure analyses suggest that curated data and executable checks will remove
   more errors than better prompting; that is our inference, not a measurement.
3. **No published position accepts AI output without an independent human check.** The four professional bodies whose
   guidance was found say the engineer stays fully responsible and must verify by independent means. Studies of people
   reviewing AI output show they anchor on what the AI shows them. So the review step has to be designed, not assumed.

**The plan in one table.**

| Phase | Theme | Outcome |
|---|---|---|
| **E0** | Stop misleading | The known defects in `mu.fea` are fixed; the bundled DFM, interference and mass checks are documented, benchmarked and reachable as `mu` commands |
| **E1** | Evidence backbone | Findings, run records, requirements, materials with provenance, design criteria, `mu check`, `mu report`, review records. This delivers the core of PLAN.md phase P2 |
| **E2** | Manufacturing package | DFM with shop profiles and evidence images, DFA worksheet, mass ledger, composite layup rules, should-cost range |
| **E3** | Calculation layer | A cited, unit-checked formula library with calculation sheets: bolted joints, laminates, lugs, buckling, fits, tolerance stacks, fatigue screening |
| **E4** | FEA you can sign | Typed studies, selection by CAD face, mesh and convergence gates, singularity handling, margins by failure mode; then contact, preload, buckling, thermal and dynamics as benchmarked templates |
| **E5** | Airframe substantiation | Loads generator, vibration and Campbell checks, composite strength route, test predictions, compliance matrix: the first complete stress report for JATAYU |
| **E6** | Aero, propulsion, thermal | Templated CFD with convergence evidence, propeller and powertrain models, thermal networks |
| **E7** | Judgement and autonomy | FMEA and design-review gates, drawings and model-based definition, measured promotion of the agent's autonomy by area |

**What stays human, permanently on current evidence:** load-case and allowable approval, GD&T authority, completeness of
failure modes, test correlation, anything novel, and the signature.

---

## 2. Goal, scope and the autonomy ladder

### 2.1 Goal

Requirements, restating and extending PLAN.md §1:

1. **A work package, not a number.** Every analysis ends in findings with a status, a method, a source, the evidence
   behind it, and a statement of what was not checked.
2. **Checkable in minutes.** The package is laid out the way a checker works: purpose, configuration, loads, factors,
   allowables, assumptions, method, model checks, an independent cross-check, a minimum-margin table, coverage, open
   items.
3. **Honest about the boundary.** What the agent did, what a tool measured, what a human verified and what nobody
   verified are four different things and are recorded as such.
4. **Measured, not assumed.** μ's own accuracy comes from known-answer benchmarks run on every build. The agent's
   reliability comes from task evaluations and from the checker's findings. Autonomy follows the numbers.
5. **All of PLAN.md still holds:** every capability is a non-interactive CLI command with `--json`; projects are
   self-contained; company rules and materials live in the org profile; toolchain changes go through `toolchain/` and
   the benchmark gate.

### 2.2 Scope

| In scope | Out of scope for this roadmap |
|---|---|
| Mechanical engineering of parts and assemblies: manufacturability, structures, joints, fatigue, tolerances, vibration, external aero, propulsion sizing, thermal | Electrical and electronics design (a later roadmap; the same pattern applies) |
| The artefacts around analysis: requirements, compliance matrix, FMEA draft, review records, reports | Flight control, autonomy software, payload and weapons engineering |
| Small UAV airframes as the first worked domain | Certification itself. μ produces evidence; an authority or customer accepts it |
| Claude Code as the operator, a human engineer as checker and approver | Replacing the checker. A second AI instance is not an independent check (§10) |

### 2.3 Non-goals

- **No simulation-data-management platform.** Industry adoption of such platforms has been weak. μ implements the
  guardrails (templates, envelopes, logs) as files in git.
- **No MCP server now.** Most engineering MCP servers found are single-maintainer and coupled to a GUI. A headless CLI
  with JSON avoids both, in our judgement. MCP is a thin transport that can be added over the CLI later.
- **No model fine-tuning.** Some studies measure real gains from fine-tuning, but it ties the work to one model version
  and is out of reach for a small team. Tools, checks and curated data survive a model change.
- **No vision-based verdicts.** Renders are for the human reviewer and for catching gross errors. A number is never read
  off a picture.

### 2.4 The autonomy ladder

Built from the NAFEMS Professional Simulation Engineer levels and UK-SPEC, which define junior and senior work in usable
terms. Levels are granted **per technical area**, the way NAFEMS certifies per area.

| Level | The agent may | The human does | Evidence needed to hold the level |
|---|---|---|---|
| **A. Supervised** (junior, entry) | Run analyses from a human-approved plan, using org-profile procedures and μ templates | Approves the plan before work starts; checks every package in full | Templates' benchmarks pass at the pinned μ version |
| **B. Procedure-bound** (junior, independent) | Choose among approved methods, do its own model checks, flag anything outside a template's envelope | Full check on safety-critical items; risk-weighted sampling elsewhere, including items the agent did not flag | Level A evidence, plus the agent evaluation pass rate (§8.4), the checker-finding rate over N packages, and seeded-error results |
| **C. Advanced** (senior) | Propose analysis strategy, validation tests and design changes | Reviews strategy and credibility; still verifies independently and signs | Level B evidence sustained, in that area. No regulator text supports dropping the independent check, so C changes what the agent proposes, not who signs |

Rules:
- A change of model, prompts, skills or μ version is a change to the agent. The evidence is re-run before the level is
  kept.
- "Proven techniques and procedures" are delegable. "New analytical techniques" and "technical responsibility" are not.
- No published position found says what depth of checking is sufficient, or addresses autonomous agents. Level B's
  sampling is therefore a company decision that its own recorded evidence has to justify.
- The target is Level A in every area by the end of E6, and Level B in named areas during E7.

---

## 3. Where μ stands

### 3.1 Capability by discipline

| Discipline | In μ itself | Reachable through a bundled tool | Depth today |
|---|---|---|---|
| CAD | The environment and `mu run` | build123d 0.13, CadQuery, text-to-cad 0.11.9 | Parametric parts and assemblies. No fastener library, no dimensioned drawings, no PMI |
| Geometry checks | One instruction line; the `cad-solid` benchmark | OCCT validity; text-to-cad soundness, self-intersection, distance, overlap | Good, but not wrapped or tested by μ |
| Manufacturability | **Nothing** | text-to-cad `cad model dfm`: CNC milling and turning, sheet metal, injection molding, five printing technologies | Rule-based findings with coded results. Undocumented in μ, no benchmark |
| Assembly, kinematics | `mu.fea` fuses touching parts | text-to-cad interference, joint ranges, mechanism states, MJCF/URDF export | Exact interference at given poses. No dynamics |
| Mass properties | Point masses in `mu.fea` | text-to-cad mass, centre of mass, inertia; `bd_materials` densities | Mass and inertia. No ledger, no growth allowance, no CG envelope |
| Meshing | Tet10 meshing in `mu.fea` | Gmsh 4.15.2 | Second-order tets only. No quality report, no shells, no curvature sizing |
| Structural FEA | `src/mu/fea.py`, 354 lines | CalculiX 2.23 by hand-written input | Linear static and modal, isotropic, bonded joints, point loads and gravity |
| CFD | A wrapper, four instruction bullets, one laminar benchmark | OpenFOAM v2606 | No templates, no force, residual or y+ extraction |
| Aero | A wrapper, one benchmark, an independent reference solver | OpenVSP 3.56, VSPAERO | Lift-curve slope of one wing |
| Slicing, CAM | Wrappers and two benchmarks | PrusaSlicer 2.9.7; FreeCAD CAM | G-code out. Nothing judged. CAM has two known silent defects |
| Reporting | **Nothing** | python-docx, matplotlib, pandas | None |
| Provenance | Install manifest, benchmark results; a run record per remote job, written at `mu fetch` | git | Remote jobs only: commit, toolchain version, node, command, output hashes. No record for a local run, and no engineering content (results, checks, mesh) |

### 3.2 Planned in PLAN.md: what is built and what is not

PLAN.md v6 states most of this itself (§6.5, §6.7, §9, §10).

| Planned (PLAN.md) | Status |
|---|---|
| Job layer: `mu compute …`, `mu submit / status / log / fetch / cancel` | **Built** (v2026.11.7 on). The office PC is paired and runs jobs |
| Run records `runs/<id>.json` | Remote jobs only, written at `mu fetch`. Local `mu run` is a bare `exec` and writes none |
| `mu new`, `mu context`, `mu check`, `mu release` | Absent |
| Re-validating a project after its pin moves | By hand; `mu pin` re-runs nothing |
| Skills `mesh`, `fea`, `cfd`, `aero`, `slicing`, `cam`, `validation`, `report` | Absent; only the vendored `cad` skill ships |
| Plugin manifest, slash commands, SessionStart / PreToolUse / Stop hooks | Absent |
| Org profile with validation policy, materials, report templates | In another repository; PLAN.md §13 records it as not verified |
| CI on each push | The relay and job-layer workflows run for their own paths. Nothing runs the benchmark suite on push, so a change to `src/mu/fea.py` is gated only when someone installs |

### 3.3 Defects to fix before anything is built on top

These are in code today. The first two can put a wrong number in front of a reviewer.

| # | Defect | Where | Confidence |
|---|---|---|---|
| D1 | The maximum principal stress field `S1` is computed with two shear components swapped when it is fed nodal stress from the `.frd` file | `fea.py:269-271`, `281-286` | Code verified. The `.frd` component order is from CalculiX's documented format **(to confirm, ES2)** |
| D2 | The margin of safety uses the raw peak integration-point stress, which the project's own validation policy says not to trust. Bonded joints and rigid couplings create singular corners by construction | `fea.py:352-354` | Verified |
| D3 | One scalar allowable per material, and the failure criterion is chosen by whether the material name starts with `cf`, unless the caller overrides it. A carbon part is solved as an isotropic solid and judged by maximum principal stress | `fea.py:185`, `350-353` | Verified |
| D4 | A static step without point loads inherits the previous step's point loads, and the same for gravity | `fea.py:198-205` | Code verified; solver behaviour **(to confirm, ES2)** |
| D5 | Point masses are left out of gravity, so a payload contributes to modes and not to a g-load case | `fea.py:176`, `204-205` | Verified |
| D6 | Partly overlapping parts are assigned to whichever part was added first, silently; parts that do not touch stay disconnected, silently | `fea.py:59-63` | Verified |
| D7 | Solver failure is detected only by a missing file or the text `*ERROR`. The return code and warnings are ignored; there is no timeout | `fea.py:222-225` | Verified |
| D8 | Eigenfrequencies accumulate across modal steps; step results are indexed by position, mixing steps and modes | `fea.py:253`, `293-324` | Verified |
| D9 | Thread counts are fixed at 4, against the repo rule on hardware sizes. Mesh node counts already vary from run to run with the parallel mesher | `fea.py:41`, `218` | Verified |
| D10 | `mu test --quick` fails `fea-cantilever` whenever a reference file exists, because the second mesh's metrics are missing | `src/mu/bench.py:31-34` | Verified by reading |
| D11 | `render-png` passes `.frd` files to a reader that has no CalculiX `.frd` support | `src/mu/post/render_png.py:32-35` | Code verified; reader behaviour **(to confirm)** |
| D12 | The CFD benchmark's docstring lists a vortex as a known answer, and no check reads the velocity field | `benchmarks/cfd-cavity/bench.py:2` | Verified |

The review at `94fd2fd` also found documentation drift. PLAN.md v6 has since corrected nearly all of it. What still
differs between stated rules and the code is in Appendix B.

### 3.4 The unused asset

The pinned text-to-cad release documents, in `claude-plugin/skills/cad/`:

- **Manufacturing checks** for four process families, each finding carrying `rule`, `value`, `limit`, `unit`, `op`,
  `source` and face references, with a result state of `ok`, `failed` or `not_checked` and matching exit codes 0, 1, 3.
- **A review format** that separates pass, fail, review and unverified, and requires a coverage section.
- **Exact interference** at named poses, **joint-range sweeps**, **mass properties**, and geometry diagnostics.

This is the same data model the commercial DFM tools use (feature, measured quantity, operator, threshold, process). μ
should adopt its result vocabulary toolchain-wide instead of inventing another. Three cautions:

- μ has no test of these verdicts, and the skill records that verdict semantics changed at release 0.11.7. A version
  bump could change what "OK" means with no benchmark noticing.
- One independent test of text-to-cad in a clean container failed on an undeclared system library. μ's fresh-build
  benchmark rule exists for exactly this. The same review noted telemetry on by default and a daily version check;
  μ already turns both off in `lib/mu-env.sh`.
- The skill refers to four companion skills (`fea`, `print3d`, `sendcutsend`, `robot-simulation`) that μ does not ship.

---

## 4. What the research found, and what follows for μ

The full report and notes carry the sources. Each row below is a measured result or a published position, or, where it
says so, a practitioner report or an inference, followed by its consequence here. Many measured results are for 2024 and
2025 models, so they show direction and failure types more than current capability. Vendor accuracy claims are excluded
from this table; none had independent evidence.

| # | Finding | Consequence for μ |
|---|---|---|
| R1 | Foam-Agent with Claude 3.5 Sonnet reports 88.2 % success in its own paper, where success means the case ran. CFDLLMBench scores the same framework at 33.6 % when the field must match a reference. FEABench (COMSOL, 2024): 88 % executable, none fully correct. Baselines disagree sharply between papers | Results carry separate states: executed, converged, checks passed, verified against a reference or requirement. No single success flag (§5.1) |
| R2 | On briefs validated by FEA (CadQuery, Gmsh, CalculiX), current coding agents got essentially no strict passes on the first attempt. The best configuration reached 9 of 50 with up to ten feedback attempts, at about 68 minutes per brief (a work-in-progress paper, May 2026). About 58 % of executable CAD programs in another benchmark violated a requirement | Budget for iteration against executable checks. Requirements must be machine-checkable before geometry exists |
| R3 | A system that lets the model write only a validated specification, with human-written solver templates, reached under 1 % error on standard linear-elastic cases (about 5 % on a plasticity case). In CFD, a structured knowledge base built from the solver's own tutorials beat free-text retrieval | The agent writes a typed study; μ generates the solver input. Hand-written input is allowed and placarded (§5.2) |
| R4 | An error-driven review loop added 30 to 40 points of execution success in one ablation, and 13.6 points when scored on correctness; adding more agents added little | One lead agent with precise tool errors. No swarm |
| R5 | Executable geometric tests agreed with human judges far better than image-based judging (AUC 0.928 against about 0.66). Render feedback did not improve pass rates on that benchmark; on another, multi-view inspection helped one model | Measure, do not look. `measure`, `section` and `diff` commands before more renders |
| R6 | Most failures in engineering-design benchmarks are domain-knowledge and constraint errors, not arithmetic. In calculation benchmarks the largest error class is choosing the wrong principle or formula. A formula sheet added 2 to 5 points; the combined effect of a cited library, units and code execution has not been measured by anyone | A closed, cited formula library and curated data. The agent selects; code computes (§7.8) |
| R7 | Finding the applicable rule was far more reliable with the whole governing document in context than with retrieval (F1 0.885 against 0.186 in one benchmark, on 2024 models) | Design criteria and requirements are loaded whole, and compliance output quotes rule ids |
| R8 | Pure vision-model DFM judgement over-flags: GPT-4V (2023) called all 20 test parts unprintable, including the 10 good ones. A 2026 benchmark puts the best model near 49 % on manufacturability, itself scored by a vision judge | The model may only cite measured findings. Anything it adds is labelled "review" |
| R9 | Published DFM limits come from service-bureau guides and differ by a factor of two between shops for the same rule | Rules carry a source and a tier (recommended, feasible, shop). Shop profiles live in the org profile (§7.2) |
| R10 | NASA-STD-5020B and ECSS-E-HB-32-23A are free, current, and give bolted-joint equations with numbers a reviewer can open. Open-source VDI 2230 implementations are not dependable | Implement the public standards as cited functions, tested against their own worked examples (§7.8) |
| R11 | An independent cross-solver benchmark found CalculiX shell elements wrong by more than 99 % on a standard shell test, while solids passed. The test dates from about 2021 and predates CalculiX 2.23. The 2.23 manual has no arc-length procedure and no random-vibration step | Shells are untrusted until μ's own benchmark passes. Thin composite structure is the largest technical risk (§7.7, ES3) |
| R12 | NASA-STD-7009B asks for records, not thresholds: intended use, inputs with pedigree, verification done at every use, every solver warning explained, and an explicit statement where no uncertainty estimate exists | The run record and report skeleton follow it. Unexplained warnings block a pass |
| R13 | The grid convergence index procedure gives the arithmetic for mesh convergence: three meshes, refinement ratio above 1.3, observed order and a safety factor of 1.25 (Celik et al.); 3.0 when only two meshes are run (Roache) | Replace "2 to 3 meshes and Richardson" with reported observed order and flagged non-monotonic convergence (§7.7) |
| R14 | For structures under 150 kg the only freely readable numeric rule set found is NATO AEP-83: ultimate factor 1.5 for hazardous-failure structure, 1.25 otherwise, special factors for fittings, castings and composites. It covers fixed-wing aircraft only, and the 2014 edition read has been superseded by a 2023 edition that was not. EASA's proposed means of compliance gives a thrust-based load factor for multirotors | A project design-criteria file adopts and cites a factor set. μ never hard-codes one (§6.3) |
| R15 | ECSS-E-ST-32C Annex K defines a stress report: factor table with sources, allowables with basis, margin formulae, a minimum-margin summary, a coverage statement | The stress report template (§7.15) |
| R16 | Three engineering regulators (British Columbia, adopted by Ontario; North Carolina; Newfoundland and Labrador) and one federation (Engineers Europe) say the same: the engineer is fully responsible, must verify independently, and must record tool version, inputs, outputs and verification steps | Run records hold the first three. A review record holds the verification act, which most toolchains lack (§6.3) |
| R17 | In a controlled experiment with 29 developers, AI-assisted review saved no time, found no more serious issues, and narrowed where reviewers looked (the authors say it may be underpowered). One 2026 preprint found that making reviewers disposition each assumption reduced over-reliance without extra load. No study of engineers reviewing AI-generated analysis was found | Reports lead with assumptions, not with an AI self-review. Seeded-error audits measure the checker (§10) |
| R18 | Studies of LLM-written FMEA find useful ideation, measurable omissions (recall near 0.56 for failure locations in the one study that measured it, on 2023 open models) and unvalidated severity numbers | The agent drafts structure and a long list; humans own severity and completeness (§7.14) |
| R19 | Practitioner reports: a silent OpenCASCADE boolean failure at a tangent face; and one hardware-as-code project whose rules ban agents from lowering thresholds or stubbing checks to get a pass | Automatic checks after every boolean. Thresholds live where the agent's edits are visible as diffs (§5.6) |
| R20 | No open framework was found that produces loads reports, margins and test predictions for a small UAV; the published multirotor frame studies found rarely state a load factor or failure criterion. This is a limited search, not proof of absence | The E5 package would exceed typical published practice. There is no reference implementation to copy |

---

## 5. Design principles

These are the decisions the rest of the roadmap depends on. Each comes from §4.

### 5.1 Result states, not a success flag

Every analysis command returns the same status block:

| Field | Meaning |
|---|---|
| `status` | `ok`, `failed`, `not_checked` or `usage`. Exit codes 0, 1, 3, 2, as text-to-cad already uses |
| `reason` | The code for a `not_checked` result: an input missing, a selection that did not resolve, a solver that did not converge, a rule with no limit for this material |
| `executed`, `converged` | The tool ran; the solution met its convergence criteria |
| `checks[]` | Each pre-solve and post-solve gate with pass, warn or fail and its measured value |
| `verified_against[]` | The reference case, hand calculation or requirement id the result was compared with |
| `unverified[]` | Everything the command did not or could not judge |

Three vocabularies are in use, at three levels, and all three come from text-to-cad's check and review format:

| Level | Values | Rolls up as |
|---|---|---|
| A command's `status` | `ok`, `failed`, `not_checked`, `usage` | The exit code |
| A gate inside a run (`checks[]`) | pass, warn, fail | Any fail makes the command `failed`; a warn is counted and shown |
| A finding in a report | `pass`, `fail`, `review`, `unverified` | Any `fail` makes the check `failed`; with none, anything not judged makes it `not_checked` |

Rules:
- `not_checked` is never a pass and never "no findings".
- A report cannot label a result verified when `verified_against` is empty; it prints it under "unverified".
- A missing input produces `not_checked` with what is missing. The agent asks; it does not assume.

### 5.2 Typed input, generated solver files

- The agent writes a **study**: a typed, validated description of geometry, named selections, materials, loads,
  constraints, analysis type and mesh controls. μ generates the CalculiX deck or OpenFOAM case from it.
- The agent never edits a solver input file in a templated study. Doing so through `mu run` remains possible; the run
  record marks it `untemplated` and the report placards it as a deviation.
- `mu.fea` already keeps the model out of the deck. The step from a Python API to a validated study adds the envelope
  check, the command-line form the repo rule requires, and the record.

### 5.3 Templates with an envelope and their own evidence

- Each analysis type is a versioned **template** that declares what it is valid for: geometry class, element types, load
  types, parameter ranges.
- Each template is linked to the benchmarks that exercise it. Capability is assessed once per template version;
  results are assessed on every run. This is the split NASA-STD-7009B makes.
- A run outside the envelope is not blocked. It is marked as a deviation and escalated to "expert review required".

### 5.4 An unbroken evidence chain

`requirement → check → finding → run record → inputs by hash`. No number appears in a report without a run record
behind it. `mu check` enforces this, as PLAN.md §6.7 already requires.

### 5.5 Curated data, not model memory

| Data | Carries | Lives in |
|---|---|---|
| Materials and allowables | Value, unit, basis (A, B, S, vendor minimum, in-house test, typical), source and revision, specification, form, direction, environment, approval status | Org profile |
| Formulas | Source document, equation or table number, validity range, units | μ (`mu.calc`), with benchmarks |
| Manufacturing rules | Source, tier (recommended, feasible, shop) | text-to-cad for the general rules; org profile for shop profiles |
| Factors of safety and criteria | Value, source, where it applies | Project design-criteria file, from an org template |

A margin computed on an allowable whose basis is `typical` is flagged in the report and cannot support a closed
requirement.

### 5.6 Gates the agent cannot quietly weaken

- Thresholds, factors, allowables and reference values live in the org profile submodule or in files where any change is
  a visible diff with a stated reason.
- A check that runs zero cases, or passes with nothing to judge, reports that as its result.
- Every check is trusted only after it has been seen to fail: seeded-defect parts for DFM and interference, mutation of
  the model for requirement tests.
- `mu check` includes a stub-and-guess audit before a report can be marked review-ready.

### 5.7 Coverage is part of the result

Every report ends with what was verified, what was assumed, what was not checked and what must be checked by hand. The
text-to-cad review format already does this for DFM; μ extends it to every analysis.

### 5.8 Review is designed

- The checker's act is a record: what was reviewed (by hash), by whom, by what independent method, with a disposition
  per item. Any later change to a reviewed object invalidates the record automatically (§6.3).
- The package offers the checker an independent path: free-body loads, section properties and a bounding hand
  calculation, so the check is not a re-run of the agent's script.
- Reports put assumptions first and ask for a disposition on each.

### 5.9 Proportionality

Evidence depth scales with model risk, following ASME V&V 40: how much the model drives the decision, times how bad a
wrong decision is. A part's criticality class selects the number of meshes, the benchmarks required, the sensitivity
runs and the depth of human check.

### 5.10 A dependency costs a layer

Any change to the lock files rebuilds a 3.4 to 4.3 GB environment layer on every machine. Pure Python under `src/mu`
installs in seconds.

- Prefer in-house code where a method is a few hundred lines and has a public source to test against.
- Take a dependency where the method is large and the library is maintained and permissively licensed.
- Batch lock changes: two bumps across this roadmap (§12).
- GPL and AGPL tools may be called as external commands. They are not imported into `mu`.

---

## 6. Target architecture

### 6.1 Layers

```
 Project repo (git)                                        Org profile (submodule org/)
 ┌────────────────────────────────────────────────┐        ┌──────────────────────────────────┐
 │ requirements/   criteria.toml   loads/         │        │ CLAUDE-org.md  validation policy │
 │ src/ (models)   layups/         studies/       │◄───────│ materials/     with basis+source │
 │ calcs/          fmea/                          │        │ shops/         DFM and rate data │
 │ runs/  findings/  reports/  reviews/           │        │ criteria/      factor templates  │
 │                                                │        │ templates/     report, review    │
 └───────────────────────┬────────────────────────┘        └──────────────────────────────────┘
                         │  every command: --json, exit code, run record
 ┌───────────────────────▼────────────────────────────────────────────────────────────────────┐
 │ mu commands    geom · dfm · dfa · cost · mass · loads · calc · tol · fea · cfd · aero ·    │
 │                thermal · req · fmea · check · report · review · context                    │
 ├────────────────────────────────────────────────────────────────────────────────────────────┤
 │ mu libraries   mu.record  mu.findings  mu.materials  mu.units  mu.calc  mu.tol  mu.fea     │
 │ (src/mu)       mu.cfd  mu.loads  mu.mass  mu.mfg  mu.report  mu.post                       │
 ├────────────────────────────────────────────────────────────────────────────────────────────┤
 │ Bundled tools  text-to-cad · build123d/OCCT · Gmsh · CalculiX · OpenFOAM · OpenVSP ·       │
 │ (pinned)       ParaView/PyVista · PrusaSlicer · FreeCAD CAM                                │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
```

Claude Code works at the top: it writes models, studies, calculation inputs and requirement entries, runs commands, reads
JSON, and iterates. Humans approve the plan, the criteria and materials, and the final review.

### 6.2 Command surface

All are non-interactive, take `--json`, and write a run record. They are added the way `doctor` and `test` are: a case
in `bin/mu-toolchain` and a module under `src/mu`, run in the project's pinned toolchain, which needs no layer rebuild.
(The job-layer commands differ: they run on the default toolchain through `src/mu/cli.py`.)

| Command | Does | Phase |
|---|---|---|
| `mu geom check` | Validity, solid count, closure, self-intersection, volume delta against the last run, STEP round trip | E0 |
| `mu dfm` | Runs the text-to-cad manufacturing check, applies a shop profile, renders evidence images, emits μ findings | E0, E2 |
| `mu interference`, `mu ranges` | Wraps text-to-cad's exact interference and joint sweeps into findings | E0 |
| `mu mass` | Mass, centre of mass and inertia by part and assembly; later the mass ledger and CG envelope | E0, E2 |
| `mu context` | Project state for the session start: version, nodes, open findings, stale reviews, validation gaps | E1 |
| `mu req` | Lists requirements, evaluates their bound checks, prints the compliance matrix | E1 |
| `mu check` | No number without a run record, no stale review, no unexplained warning, no stub; exit 1 on any | E1 |
| `mu report` | Builds a report from findings and run records using an org template | E1 |
| `mu review` | Opens, records and verifies review records by hash | E1 |
| `mu dfa`, `mu cost` | Assembly worksheet with computed columns; should-cost range from slicer and CAM output | E2 |
| `mu calc <method>` | One cited calculation: `bolt`, `laminate`, `lug`, `buckling`, `fit`, `bonded`, `hertz`, … | E3 |
| `mu tol` | Tolerance stacks: worst case, statistical, Monte Carlo on the parametric model | E3 |
| `mu fea` | Runs a typed study; `mu fea converge` runs the mesh study and reports observed order and GCI | E4 |
| `mu loads` | Generates load cases from vehicle parameters and criteria | E5 |
| `mu vib` | Campbell check, forced response, vibration fatigue from modal results | E5 |
| `mu cfd`, `mu aero`, `mu prop` | Templated CFD cases; VSPAERO sweeps; propeller and powertrain performance | E6 |
| `mu thermal` | Lumped thermal network over a mission profile | E6 |
| `mu fmea` | Drafts and validates the FMEA table; checks coverage against the part tree | E7 |
| `mu gate` | Evaluates a design-review gate's entrance criteria from the repository | E7 |

`mu run <tool>` stays as the escape hatch. It writes a minimal record marked `untemplated`.

### 6.3 Data objects

Plain files in the project repository. JSON where μ writes, TOML where people and the agent author. These are sketches
with illustrative values, not final schemas. Each gets a versioned schema in the phase that introduces it: E1 for
findings, run records, requirements, materials, criteria and reviews; E3 for calculation sheets; E4 for studies.

**Finding.** One judged fact. The shape text-to-cad already emits, plus links.

```json
{
  "id": "F-0042", "check": "dfm.cnc", "rule": "cnc.internal_corner_radius",
  "status": "fail", "severity": "error",
  "value": 0.5, "limit": 1.5, "op": "min", "unit": "mm",
  "source": {"doc": "Protolabs Network CNC design guide", "tier": "recommended"},
  "refs": ["hub_plate/face 9"], "at": [12.0, 8.5, -3.0],
  "requirement": "REQ-MFG-003", "run": "runs/2026-11-20T0912-dfm-hub_plate.json",
  "evidence": ["reports/img/F-0042.png"],
  "hint": "Radius of at least 1.5 mm on the four pocket corners, or a smaller tool",
  "disposition": {"state": "open", "by": null, "reason": null}
}
```

`status` is one of `pass`, `fail`, `review` (a qualitative risk for a human), `unverified` (no limit, no measurement, or
not reached). `disposition` records acceptance or a waiver with a name and a reason.

**Run record.** Extends PLAN.md §6.7 and the record the job layer already writes at `mu fetch` (`runs/<job-id>.json`:
snapshot commit and tree, toolchain version, node and hardware, command, limits, times, each output's SHA-256). E1 keeps
that shape, writes it for local runs too, and adds the engineering fields below. `docs/ROADMAP.md` step 3.3 plans to
record local `mu run` work for the dashboard; that and this must be one mechanism. Each field maps to a term of the Process Run Crate profile (`instrument`,
`object`, `result`, `agent`, `startTime`, `endTime`, `actionStatus`), so a standards-aligned export is cheap to add.

| Field | Content |
|---|---|
| `id`, `kind`, `template`, `template_version` | For example `fea.static`, `dfm.cnc`, `calc.bolt`; `untemplated` for raw runs |
| `status` block | As §5.1 |
| `inputs[]`, `outputs[]` | Path and SHA-256 of each: models, studies, materials, criteria, meshes, results |
| `project` | Commit, plus a hash of uncommitted changes |
| `toolchain` | μ version, lock hash, solver and mesher versions |
| `host` | Node name and detected hardware |
| `agent` | Who launched it: a person or the AI agent, and which session |
| `command`, `started`, `ended`, `wall_s` | Exactly what ran |
| `mesh` | Element and node counts, quality distribution, gate results |
| `results` | Named scalar results with units: the numbers a report may quote |
| `warnings[]` | Every solver warning, each explained or marked unexplained |
| `deviations[]` | Departures from the template or the org procedure |
| `archive` | Where the raw output lives |

**Requirement.** One entry per "shall", authored in TOML under `requirements/`.

```toml
[[requirement]]
id        = "REQ-STR-004"
text      = "The arm shall carry limit load case LC-03 without yield and 1.5 x limit without rupture."
rationale = "criteria.toml [factors]"
parent    = "REQ-SYS-012"
method    = "analysis"            # analysis | inspection | demonstration | test | similarity
check     = { result = "fea.arm_static.ms_min", op = "min", limit = 0.0 }
class     = "primary"             # criticality class, selects evidence depth (§5.9)
```

The compliance matrix is always generated from these, never maintained by hand.

**Design criteria.** `criteria.toml` in the project, started from an org template. Factors of safety with their source,
special factors (fitting, casting, bonded and composite discontinuity, hot-wet), margin formula ids, load-factor
definitions, the frequency-separation band, convergence thresholds, criticality classes. Every calculation and study
reads its factors from here.

**Material.** One file per material in the org profile. Each property is a record, not a number:

```toml
[Ftu]
value = 290.0
unit = "MPa"
basis = "S"                       # A | B | S | vendor_min | in_house_test | typical
source = "supplier certificate 2026-031, AMS 4027"
form = "plate 6-12 mm"
direction = "L"
environment = "RT"
status = "approved"               # approved | provisional
```

**Study.** The typed input to `mu fea` or `mu cfd`: geometry reference, named selections bound to CAD faces, materials
by id, loads and constraints by load-case id, analysis type, mesh controls, the quantities of interest and where they
are read.

**Calculation sheet.** Output of `mu calc`: reference, method and source with equation numbers, assumptions and validity
checks, inputs with units and provenance, formula, substitution, result, allowable, factors shown separately, margin,
verdict. JSON first; the readable sheet is rendered from it.

**Review record.** The object most toolchains lack.

| Field | Content |
|---|---|
| `objects[]` | Hashes of exactly what was reviewed: report, run records, findings, requirement entries |
| `reviewer`, `role` | A named person; checker or approver |
| `independent_check` | What was verified, and by what means independent of the agent |
| `items[]` | A disposition per assumption and per check item, not one overall tick |
| `signed` | Date, and the commit the review applies to |

`mu check` recomputes the hashes. If a reviewed object or its parent requirement has changed, the review is stale. This
is Doorstop's fingerprint mechanism, applied to analysis.

### 6.4 Project layout

Additions to PLAN.md §6.6, shown for JATAYU:

```
jatayu/
  mu.toml  CLAUDE.md  org/                 as today
  criteria.toml                            design criteria, from the org template
  requirements/*.toml                      requirements with bound checks
  loads/                                   vehicle parameters, generated load cases
  layups/*.toml                            ply tables for composite parts
  frame/  …                                models, as today
  studies/*.toml                           typed FEA and CFD studies
  calcs/*.toml                             calculation inputs
  fmea/*.toml                              FMEA rows
  runs/        (git)                       run records
  findings/    (git, generated)            findings by check
  reports/     (git; images in git-lfs)    generated reports
  reviews/     (git)                       review records
  .scratch/    (ignored)                   solver scratch
```

### 6.5 Where things live

| Thing | μ repo | Org profile | Project |
|---|---|---|---|
| Commands, libraries, templates, benchmarks, skills | ✔ | | |
| Formula library with sources | ✔ | | |
| General DFM rules | text-to-cad | | |
| Shop profiles, rates | | ✔ | overrides |
| Materials and allowables | schema | ✔ data, including provisional records | |
| Validation policy, report and review templates | schema | ✔ | |
| Factor sets | | ✔ templates | `criteria.toml` |
| Requirements, loads, studies, runs, findings, reports, reviews | | | ✔ |

### 6.6 Skills and hooks

Skills ship in `claude-plugin/skills/` and reach a project through `mu link`. Each is short: the commands, the hard-won
rules, and what the check does not judge.

| Skill | Contents | Phase |
|---|---|---|
| `validation` | The result states, the evidence chain, what a review-ready package contains, when to stop and ask | E1 |
| `manufacturing` | A thin layer over the `cad` skill: `mu dfm`, shop profiles, DFA, cost, layup rules | E2 |
| `calc` | The formula library index, calculation-sheet conventions, method selection rules | E3 |
| `fea` | Studies, selections, mesh and convergence gates, singularity handling, margins | E4 |
| `loads`, `vibration` | Load-case generation, Campbell and forced-response checks | E5 |
| `cfd`, `aero` | Case templates, convergence evidence, accuracy bands | E6 |
| `report`, `review-prep` | Report assembly and the checker-oriented layout | E1, E7 |

PLAN.md's `mesh`, `slicing` and `cam` skills are folded into `fea` and `manufacturing`. Slash commands and the plugin
manifest stay as PLAN.md §6.5 describes them.

Hooks, as PLAN.md §6.5 planned: SessionStart runs `mu context`; PreToolUse warns when a solver is called without going
through `mu`; Stop (optional in PLAN.md) runs `mu check`. Today `claude/CLAUDE-toolchain.md` teaches `mu run <solver>`,
which leaves no record. Once analysis commands exist, the instructions teach those, and `mu check` reports every
`untemplated` run.

---

## 7. Capability by discipline

Each subsection gives today's state, the target, the method and its sources, what to build, how μ proves it, and what
stays with the human.

### 7.1 Geometry integrity

- **Today.** One instruction line and a volume benchmark on a prismatic part.
- **Target.** Every model run ends with a geometry gate; silent boolean failures cannot reach analysis.
- **Build.** `mu geom check`: validity, solid count, closed shell, self-intersection, volume change against the previous
  run with an expected sign, STEP export and re-import, and an independent watertightness check on the mesh with
  trimesh. Named faces for loads and constraints must resolve, or the command returns `not_checked`.
- **Proof.** Benchmarks with planted faults: an open shell, a self-intersection, a boolean at an exact tangency, a part
  swallowed by a union.
- **Human.** None routine.

### 7.2 Manufacturability

- **Today.** Present in text-to-cad, absent from μ.
- **Target.** A DFM report per part that a manufacturing engineer recognises: verdict, findings pinned to highlighted
  geometry, source and tier per rule, suggested change, cost kept separate, coverage stated.
- **Method and sources.** General rules stay text-to-cad's. Published numeric limits come from service-bureau guides
  (Protolabs Network, Protolabs, SendCutSend and others), which disagree by a factor of two on some rules, so:
  - **Shop profiles** in the org profile hold each shop's limits, tools, stock and machine envelopes. `mu dfm --shop`
    compares text-to-cad's `measurements` families against the profile and reports three tiers: recommended, feasible,
    this shop.
  - **Evidence images.** Findings already carry face and edge references; a renderer colours them. This is the main
    gap against commercial tools.
  - **Dispositions.** Accepting or waiving a finding is recorded with a name and a reason.
- **Composites.** A layup is metadata; it cannot be recovered from a solid. A small ply-table schema (`layups/`) plus
  hole geometry supports rule checks from public guidance (Abbott Aerospace AA-SB-001): symmetric and balanced stack,
  at most 4 grouped plies, ±45° outer plies, ply drops at 20:1, edge distance at least 3 D, pitch at least 4 D,
  countersink at most 70 % of thickness. These are an aerospace-conservative "recommended" tier.
- **Additive.** Print time, filament and support mass come from PrusaSlicer's G-code. Orientation candidates come from
  text-to-cad.
- **CNC.** Machining time from FreeCAD CAM toolpaths (path length over feed, plus allowances). The existing access test
  uses a zero-diameter tool; a finite tool-and-holder test is a later improvement.
- **Proof.** A seeded-defect part set per process and a known-good set, measuring both missed defects and false alarms.
  The benchmark plate's pocket has sharp vertical corners, so it should already fail the CNC corner rule
  **(to confirm, ES1)**.
- **Human.** Fixturing, tool-holder collisions, bend order, gate and weld-line questions, texture and cosmetic faces,
  thread callouts, and the final process choice. The skill's "check by hand" lists become the report's manual section.

### 7.3 Assembly and should-cost

- **Assembly (DFA).** The geometric half is computable: part and unique-part counts, fastener count and commonality,
  symmetry, extraction directions and a blocking graph, straight-line tool access to each fastener, trapped parts. The
  functional questions that drive minimum part count are not. `mu dfa` fills the computed columns and marks the
  agent's proposed answers "assumed, needs confirmation". The Boothroyd-Dewhurst time tables are in the textbook, not
  in any open source, so an index needs the book or company times.
- **Should-cost.** A process-based model: material, plus setup over batch, plus cycle time at a machine rate, plus
  outside processes, overhead and margin. Output is always low, likely, high with stated assumptions. No public evidence
  supports better than about ±10 % even for calibrated commercial tools. Rates are org-profile data.
- **Calibration.** Quote a reference set of parts at two or three services and fit rate, setup charge and margin.
- **Human.** Minimum-part judgement; supplier choice; any figure used in a commercial decision.

### 7.4 Mass properties

- **Today.** Mass and inertia per part from text-to-cad; JATAYU has a `mass.json` script.
- **Target.** A mass ledger following AIAA S-120A practice: basic mass per item, maturity (estimated, from CAD,
  weighed), growth allowance, predicted mass against an allowable, CG for every payload and battery configuration, and
  the full inertia tensor for the flight-control model.
- **Build.** `mu mass` with the ledger and a CG envelope check. In E5 the loads module reads the CG offset, which
  scales per-motor thrust. S-120A itself is paid and was not read; the practice is taken from public summaries.
- **Proof.** Analytic solids; a mixed-material assembly with a hand-computed CG and inertia.
- **Human.** Growth allowances and weighed values.

### 7.5 Materials and allowables

- **Today.** A dict typed into each script with one `allow` and one `fos`. `bd_materials` gives typical values only.
- **Target.** The material record of §6.3, with the distinction aviation regulation makes for certified aircraft
  (14 CFR 25.613, a transport-category rule): statistically based design values against typical values.
- **Sources by tier.** MMPDS (paid) or supplier-certified minima for aluminium; NCAMP (free) for comparable carbon/epoxy
  prepregs, marked "not process-qualified"; vendor datasheets and in-house coupons for printed polymers, with an
  orientation-specific knockdown.
- **Build.** `mu.materials`: loads the org data, refuses unknown ids, exposes properties with units, and passes the basis
  through to every finding. Knockdowns are named and explicit: statistical basis, environment, open hole, process.
- **Human.** Approving each material record. This is the highest-leverage human input in the whole system.

### 7.6 Loads

- **Today.** Loads are typed into scripts.
- **Target.** A loads report generated from vehicle parameters and the criteria file, each case with an equilibrium
  check.
- **Method and sources.** For multirotors, EASA's proposed means of compliance for SAIL III (via prEN 4709-001) defines
  the manoeuvre factor from installed thrust: `n_max = k × total maximum thrust / minimum weight`, `n_min = −1`. Only the
  redlined proposal of July 2025 was read; the coefficient was ambiguous in it and must be checked against the final
  text. NATO AEP-83 gives factors and, for winged vehicles, the V-n and gust definition. It is a fixed-wing standard, so
  using its factors for a multirotor is by analogy and has to be stated as such in the criteria file.
- **Other frameworks read.** India's QCI certification scheme (FAQ, 2024) expects a static test to limit load, analysis
  showing a 1.5 factor, and drop tests. EASA's and the FAA's civil frameworks are objective-based and leave numbers to
  means of compliance.
- **Case set for a multirotor:** symmetric maximum thrust; hover at maximum weight; maximum differential thrust
  (frame torsion); yaw torque; one motor out; motor torque step and gyroscopic moment at each mount; propeller
  imbalance; wind at maximum tilt; landing (level, one leg first, with drift) from a drop height; payload and battery
  inertia loads; ground handling; battery retention. This list is derived from the cited definitions, not copied from
  a standard: no standard found defines, for example, one-motor-out structural loads. A human approves it.
- **Build.** `mu loads` writes `loads/cases.json`; studies and calculations refer to cases by id.
- **Human.** Approving the case set and the vehicle parameters. Landing load factors on rigid gear are very sensitive to
  stroke and usually need a drop test or a dynamic simulation.

### 7.7 Structural FEA

- **Today.** §3.1 and the defects of §3.3.
- **Target.** A stress result a checker can accept: model checks shown, convergence evidenced, stress read where it
  means something, margins by failure mode with named criteria and factors.

**Step 1, in E0: repair.** Fix D1 and D4 to D9. Withdraw the margin function (D2, D3) until E4 replaces it.

**Step 2, in E4: the trustworthy linear core.**

| Item | Build | Source of the practice |
|---|---|---|
| Typed study and `mu fea` | §5.2; steps, materials and interactions become objects | Constrained-interface results (R3) |
| Selection by CAD face | Bind study selections to named build123d faces; map them to Gmsh surfaces after fragmenting, to physical groups, to element faces. Fail loudly when a name does not resolve **(ES5)** | A large share of FEA-benchmark failures happen at meshing or boundary-condition assignment |
| Pre-solve gates | Units; every selection resolves; rigid-body-motion check; load resultant echo against the load case; materials traced to the org table; mesh gate | VFEAgent, ChatCFD, NASA-STD-7009B |
| Mesh gate | Fail on any inverted or zero-Jacobian element; warn below a quality threshold; report the distribution; curvature-based sizing on by default | Gmsh quality measures; thresholds to calibrate |
| Post-solve gates | Reactions against applied loads per direction; mass against CAD mass; strain energy against external work; every warning explained | AFGC guide, NASA-STD-7009B |
| Convergence | `mu fea converge`: three meshes with ratio above 1.3, observed order, GCI with factor 1.25; non-monotonic convergence flagged, not extrapolated | Celik et al.; Roache |
| Singularity classifier | A peak that grows with refinement while a point a fixed distance away converges is labelled singular: the peak is not reported; a hot-spot extrapolation, linearisation or sectional result is used | The repo's own validation policy, made automatic |
| Margins | By part, load case and failure mode: yield at limit, ultimate at ultimate, with factors from `criteria.toml` and allowables from the materials table | ECSS-E-ST-32C Annex K |
| Free-body output | Interface forces and moments at joints, for bolt and lug calculations and for the checker's independent estimate | Checking practice (§10) |
| Results reader | Keeps step, increment, time and mode for every block; integration-point stress from `.dat`; modal effective mass | pygccx and ccx2paraview studied for ideas, not vendored (pygccx is GPL) |

**Step 3, in E4 and E5: depth, one benchmarked template at a time,** in order of CalculiX's evidence of reliability:

1. Tied and frictionless face-to-face contact on quadratic solids, with a contact-stiffness sensitivity run.
2. Bolt pretension sections with displacement lock.
3. Linear buckling on solids, reported with an explicit knockdown and a note that imperfection sensitivity is not
   captured. CalculiX has no arc-length method.
4. Small-strain plasticity.
5. Steady and transient heat transfer, and sequential thermal stress (CFRP to aluminium mismatch).
6. Modal dynamics and steady-state dynamics, for forced response.
7. Orthotropic solids with orientations, for thick laminates and tubes.
8. Shells, only after ES3.

**The composite question (ES3).** A drone airframe is mostly thin composite. CalculiX expands shells into solids
internally, supports composites on two shell types only, and failed a standard shell benchmark in one independent test.
Three routes, to be decided by a spike on a CFRP plate and tube with hand solutions:

| Route | For | Against |
|---|---|---|
| Orthotropic solids plus laminate theory on section strains | No new dependency; solids are CalculiX's strong side | Element count in thin parts; ply stresses are post-processed; the solid model that passed the shell benchmark used hexahedra, and μ meshes tetrahedra only |
| CalculiX composite shells | Native | Unverified until μ's benchmark passes; output restrictions |
| A second solver for shells (code_aster passed the same benchmark) | Possibly trustworthy shell elements | One benchmark from 2021; its other capabilities were not researched. A new application in `apps.toml` on two platforms; a second deck generator |

Until ES3 reports, laminate strength is by calculation (§7.8) with FEA used for load paths and stiffness. Reviewers
expect hand methods for this in any case.

- **Proof.** §8.1.
- **Human.** Idealisation choices, boundary conditions, anything flagged as a deviation, and the independent check.

### 7.8 Calculations

- **Today.** None. No units library is pinned.
- **Target.** The checks a competent engineer does by hand, done by code the agent selects from, each rendered as a
  calculation sheet a checker can follow line by line.
- **Design.** Evaluations of language models on engineering calculations find the largest error class is the wrong
  principle or formula; invented formulas, unit errors and arithmetic follow, in an order the studies do not agree on.
  So:
  1. A **closed formula library**, `mu.calc`. Each function carries its source document, equation or table number, and
     validity range as metadata, and asserts the range.
  2. **Units on every quantity** (pint), so a dimensional error raises an exception instead of reaching a report.
  3. **All arithmetic in executed code,** with the substitution step rendered.
  4. A **regression suite of published worked examples,** from the standards where they print them.
  5. The agent selects the method and supplies inputs. That selection is where the human check concentrates.

  No published study measures this combination. μ's Tier 4 benchmarks and agent evaluation (§8) will be the first
  evidence of its residual error rate.

| Method | Public source to implement from | Notes |
|---|---|---|
| Bolted joints: preload, separation, slip, bolt strength, margins | NASA-STD-5020B (equations 1 to 23) | Torque-preload scatter of ±25 to 35 % and relaxation in polymer or CFRP clamp stacks must be stated inputs |
| Thread pull-out, inserts, embedding | ECSS-E-HB-32-23A Rev. 1, with worked examples | Insert allowables from test or the maker |
| Composite bolted holes | Bearing, net tension, shear-out; e/D and w/D rules | Bearing/bypass tools exist (`bjsfm`, `lamkit`) but are stale or unproven; cross-check only |
| Bonded joints | Volkersen, Goland-Reissner, Hart-Smith (NASA contractor reports) | Discontinuity factor 2.0 per NASA-STD-5001B; allowable source matters more than the stress model |
| Laminates | Classical laminate theory, first-ply failure (maximum stress, Tsai-Wu, others) | In-house, about 200 lines, validated against composipy and eLamX² |
| Lugs | Air Force Stress Analysis Manual method | No maintained open library. Read through a secondary summary only |
| Buckling | Euler and Johnson columns; plates; thin cylinders with NASA SP-8007 knockdowns | NASA-STD-5001B warns the classic knockdown may be unconservative for some composite cylinders, which matters for CFRP tubes. Values not yet retrieved |
| Fits, Hertz contact, bearings, springs, O-ring glands | ISO 286, standard texts, maker handbooks | In-house functions; open libraries are thin here. ISO 286 is paid and was not read |
| Beams, plates, section properties | Roark; `sectionproperties` if a dependency is justified | |

- **Calculation sheets.** JSON first (§6.3). Rendering by an in-house Markdown and docx writer; `handcalcs` v2 is a
  candidate once it leaves release-candidate status.
- **Margin convention.** `MS = allowable / (FS × applied) − 1`, with fitting and other special factors shown separately,
  and a minimum-margin table per failure mode.
- **Human.** Method selection, input pedigree, and the factor set.

### 7.9 Fatigue and vibration

- **Fatigue.** Stress-life with mean-stress correction and Miner's rule; rainflow counting to ASTM E1049. pyLife (Bosch,
  Apache-2.0) is the natural dependency. For aluminium, a first screen of stress below half of ultimate, which AEP-83
  allows on condition of good detail design.
- **Printed polymers and CFRP.** Data is the limit, not the algorithm. μ does not claim a life from literature S-N
  curves for printed parts: it designs to a low static stress ratio and names the test that would verify. Interlayer
  strength of FDM parts is treated as a fraction of in-plane strength until coupon-tested. One cited study found
  strength 37 to 78 % lower across orientations; the factor used is an entry in `criteria.toml`.
- **Campbell check.** For each operating speed, compare structural modes with 1/rev and blade-pass frequencies and flag
  any within the company band. No standard gives a numeric separation for multirotors, so the band is a criterion in
  `criteria.toml`.
- **Forced response.** Imbalance force `m e ω²` at each motor mount through steady-state dynamics; predicted IMU
  vibration against ArduPilot's published bands (below 30 m/s² acceptable, above 60 problematic); isolator natural
  frequency below disturbance over √2.
- **Vibration fatigue.** CalculiX has no random-vibration step. Response comes from integrating transfer functions
  against a PSD outside the solver, then Dirlik (FLife, MIT licence); Steinberg's three-band method would be in-house.
  This is an inferred workflow, not a documented one, and needs its own benchmark.
- **Human.** The load spectrum and duty cycle; acceptance of any life statement.

### 7.10 Tolerance analysis

- **Today.** JATAYU has a project-side `tolerance.json`; nothing in μ.
- **Target.** Stack-ups that are regenerated when the model changes, with capability assumptions printed.
- **Build.**
  - **1D stacks** from a stack definition: worst case, RSS, modified RSS, with a Cpk assumption. In-house; `dimstack`
    as a cross-check.
  - **Monte Carlo on the code-CAD model:** sample each toleranced parameter from its process distribution, rebuild,
    measure the functional requirement (gap, interference, clearance, mass, CG), report yield and the main
    contributors. Rebuild time bounds the sample count, so the 1D stack stays as the quick check.
  - **Capability data** in shop profiles: published tolerance bands, converted to a distribution with the assumption
    stated (for example band = ±3σ).
- **Limit.** Sampling dimensions does not cover form, orientation and position variation unless features are perturbed
  explicitly. No open project does this systematically.
- **Proof.** Textbook stacks with known answers; a Monte Carlo case with an analytic distribution.
- **Human.** Which dimensions are functional; the datum scheme.

### 7.11 Drawings, GD&T and model-based definition

- **Today.** No route to a dimensioned drawing. This is the weakest area.
- **Evidence.** Vision models read mechanical drawings at about 75 to 77 % on question answering; no benchmark measures
  GD&T judgement. So the agent should never read tolerances from pixels, and it should not own GD&T.
- **Route.** Because the CAD is code, tolerances can be attached to features in the model script and written as semantic
  PMI in STEP AP242 through OCCT, then re-read with an independent parser and checked by rule: every toleranced feature
  references an existing datum, datum frames constrain the needed degrees of freedom, fits resolve to valid ISO 286
  classes, a general tolerance is stated. The OCCT write path is thinly documented, so this is a spike with round-trip
  acceptance tests against the NIST models **(ES6)**.
- **2D drawings.** Generated from the model with rule-based dimensioning as a first draft. Tools here moved fast in
  2026; the main candidate is AGPL and its Python line is no longer developed. Invoke it as an external command, or use
  build123d's own drafting helpers.
- **Human.** The datum scheme, functional tolerances and drawing release. Permanently.

### 7.12 CFD and aero

- **Today.** Raw OpenFOAM and VSPAERO; one laminar benchmark that checks no flow quantity.
- **Target.** Templated cases with convergence evidence and a published accuracy band.
- **Layered stack.**
  - VSPAERO for lift distribution, induced drag and stability derivatives, not total drag.
  - A section code or transitional RANS for polars at the real chord Reynolds number: fully turbulent k-ω SST is a
    model-form error at small-UAV scale.
  - OpenFOAM RANS for bluff frames, interference and propellers with MRF.
- **Gates.** `checkMesh` results, y+ distribution against the wall treatment, residual drop, and stationarity of the
  force coefficient over a moving window; three-grid GCI on the force coefficient; mass-flux balance.
- **Accuracy bands to print with results.** The evidence is thin. One vendor-published validation case put
  small-propeller thrust by MRF within 0.4 to 11 % of wind-tunnel data, worse at high speed. One study of a different
  bluff vehicle reported drag within 10 %; no source quantified drag accuracy for a multirotor frame. Streamlined
  low-Reynolds drag from fully turbulent RANS is not credible. μ's own bands come from its Tier 3 benchmarks.
- **Propulsion.** Blade-element momentum theory against the UIUC propeller database; a three-constant motor model with
  battery resistance; hover and forward-flight power, endurance and range. New tools (CCBlade, QPROP, XFOIL) would be
  additions to the toolchain and are decided in E6.
- **Human.** Turbulence and transition modelling choices; whether a predicted difference is inside the band.

### 7.13 Thermal

- **Target.** Component temperatures over a mission on a hot day, and thermal-stress checks at dissimilar joints.
- **Build.** A lumped thermal network solver in Python (windings, stator, ESC, cells, bay air, skin) with convection
  from published correlations; OpenFOAM conjugate heat transfer as the escalation for sealed bays; linear thermal stress
  in CalculiX for CFRP-to-aluminium joints across the temperature range.
- **Environmental design review** as a generated checklist: IP target, galvanic isolation of CFRP from aluminium,
  drainage and venting.
- **Human.** Loss maps and ratings; calibration against a thermal test.

### 7.14 Requirements, FMEA and design reviews

- **Requirements.** §6.3. `mu req` generates the compliance matrix and marks each requirement open, closed by analysis,
  or closed pending test. Doorstop, StrictDoc and sphinx-needs are the mature open tools, and StrictDoc exports ReqIF if
  a customer needs it; the in-house TOML form is the smallest thing that binds a requirement to a run result.
- **FMEA.** AIAG-VDA structure with Action Priority (the handbook is paid and was read through secondary sources).
  Division of labour from the studies: the agent drafts structure and
  function analysis from the part tree, and a long list of failure modes and causes linked to requirements and analyses;
  humans own severity, completeness and controls. Because omission is the measured weakness, `mu fmea` checks coverage
  mechanically: every part × the standard structural failure modes × every load case, with explicit "not applicable,
  because" entries. Ratings proposed by the agent are shown as proposals.
- **Structural safety artefacts.** A primary-structure list derived from the load path, a single-point-failure list,
  and a positive margin plus a test reference required for each.
- **Design-review gates.** Entrance criteria as rows with an evidence path, computed where possible: drawing
  completeness, open TBD count, no negative margin, every requirement has a verification method. Based on NASA NPR
  7123.1D Appendix G and GSFC-STD-1001A. Judgement items are marked as such.
- **Human.** Everything that is a judgement about risk.

### 7.15 Reports and the review package

- **Today.** A project-side docx script; nothing in μ.
- **Target.** `mu report` assembles from findings and run records only. It has no free-text numbers.
- **Stress report skeleton,** merging ECSS-E-ST-32C Annex K, NASA-STD-7009B and the checking literature:

| # | Section | Content |
|---|---|---|
| 1 | Ledger | Pass, warn and fail counts; placards; unexplained warnings. Bad news first |
| 2 | Purpose and configuration | Decision supported; model revision; units; coordinate systems |
| 3 | Assumptions | Each one listed for the checker's disposition, or an explicit "none significant" |
| 4 | Inputs and pedigree | Load cases, factors, allowables, each with source and basis |
| 5 | Method and model | Template and version, idealisation, mesh, solver; deviations |
| 6 | Verification | Benchmark status of the template; convergence table; equilibrium, mass and energy checks |
| 7 | Independent cross-check | Hand calculation or alternate method, with the free-body data to redo it |
| 8 | Results | Margins by part, load case and failure mode; uncertainty band; sensitivities |
| 9 | Minimum-margin summary | One table, each row linked to a run record |
| 10 | Coverage | Against the part tree: analysed, not analysed, by hand; limitations |
| 11 | Reproduction | Commands, versions, hashes |
| 12 | AI-use statement | What the agent did, which records are human-verified |

- **Formats.** Markdown as the source of truth in git; docx through the org template for issue. A machine-readable twin
  always exists.
- **Human.** The review itself (§10).

### 7.16 Test prediction and correlation

- **Why.** Every framework read expects the same chain: declared loads, analysis with stated allowables, a static test
  on primary structure, vibration evidence from flight, a life statement. Analysis that never meets a test stays at the
  lowest credibility level.
- **Build.** For each planned test (arm bending, hub pull, frame torsion, drop, tap test), emit the load schedule,
  fixture reactions, predicted deflections at gauge points and strains at gauge locations, and the pass criteria. After
  the test, a correlation table: predicted, measured, error, model-update notes.
- **Human.** Running the test; deciding whether a model update is justified.

---

## 8. How μ proves itself

The research is blunt on this: published success rates are not comparable across papers, vendor figures are unverified,
and the only accuracy number a team can trust is the one it measures on its own stack. μ needs four kinds of evidence.

### 8.1 Known-answer benchmarks

Extends PLAN.md §9. Benchmarks are written as convergence tests (values at two or three densities and the observed
order), not single-mesh tolerance checks: a cross-solver study shows a fine quadratic-tet result further from the
NAFEMS LE10 target than the coarse one, so a test keyed on one node's stress is brittle.

| Tier | Cases | Proves | Phase |
|---|---|---|---|
| **0. Solver** | CalculiX's own test suite with its reference results, as a pinned data archive | The solver build, BLAS and compiler have not regressed | E0 |
| **1. Linear core** | Existing cantilever, with a third mesh so the observed order can be reported; plate with a hole; thick cylinder under pressure (Lamé); NAFEMS LE1 and LE10; a free-vibration case and a tuning fork; gravity with a point mass; a two-material bonded bar; multi-step loads | Every feature of the linear template, including the paths D1 to D8 broke | E0, E4 |
| **2. Feature** | Hertz contact; a preloaded bolted joint against NASA-STD-5020B; Euler column and plate buckling; an elastoplastic bar; steady conduction and a bimetal strip; forced response of a single-degree-of-freedom system; a composite plate and tube against laminate theory | One case, at least, per template before it is offered | E4, E5 |
| **3. CFD and aero** | A cavity case against Ghia et al.; Poiseuille; flat plate and NACA 0012 from the NASA Turbulence Modeling Resource; an APC propeller from the UIUC database with MRF | Plumbing and convergence gating. The NASA cases sit two orders of magnitude above small-UAV Reynolds numbers, so they do not validate low-Reynolds physics | E6 |
| **4. Calculations** | The worked examples printed in ECSS-E-HB-32-23A; reference cases for NASA-STD-5020B (whether it prints numeric examples is to be confirmed); textbook cases for each formula | `mu.calc` reproduces published numbers | E3 |

NAFEMS problem statements are paid publications. Their targets are widely reproduced; licensing for redistribution in
the repo must be checked before the definitions are committed.

### 8.2 Seeded-defect suites

For checks that judge rather than compute, a benchmark must show both that defects are caught and that good parts pass.

| Suite | Contents | Measures |
|---|---|---|
| DFM, per process | Parts with one planted defect each (sharp internal corner, deep hole, thin wall, missing draft, hole too near a bend, unsupported overhang), and known-good parts | Missed defects and false alarms, per rule |
| Interference and clearance | A plate through a thin cover, a pin slightly out of its bore, named fits | The same |
| Geometry | §7.1's planted faults | The same |
| Requirement checks | Mutations of a passing model: a scaled hole, a dropped feature, a thinner wall | The share of mutations the project's checks detect |
| Gate integrity | A run with zero cases, a stubbed function, a lowered threshold | `mu check` reports each |

These also pin text-to-cad's behaviour, so a version bump that changes a verdict fails a benchmark.

### 8.3 Continuous integration

- **On every push that touches `src/mu`, `benchmarks/` or `claude-plugin/`:** the pure-Python benchmarks against an
  existing install, either as a `mu submit` job from the workstation or on a self-hosted runner. PLAN.md §10 keeps
  hosted CI light and lists self-hosted runners as an option; the owner's rule that the office PC is not to be disturbed
  (`docs/ROADMAP.md` §2.1) applies to that choice. Today no workflow runs the benchmark suite on push, so a change to
  `mu.fea` is gated only when someone installs.
- **On a lock change:** the full fresh-build gate, on both platforms as PLAN.md §9 requires.
- **Per release:** benchmark results kept as `benchmarks/results/<tag>.json`, as PLAN.md §10 intended.
- **In a project:** on each commit, models rebuild; mass and CG are inside budget; no unexpected interference; DFM
  verdicts unchanged or explained; margins within tolerance of the last accepted run; `mu check` passes.

### 8.4 Agent evaluation

Benchmarks test μ. This tests the agent using μ.

- **Task set.** A fixed set of briefs in fresh projects, each with typed requirements and hidden reference checks:
  design a bracket to a load and a mass budget; review a part for a process; size a bolted joint; find the planted
  error in an analysis. The published FEA-validated benchmark is the model for the format.
- **Scored on:** strict pass (every requirement met), the rate of claims with no run record, the rate of seeded defects
  found, the number of attempts and the cost.
- **Run on:** each μ release and each change of model or skills. Expect low first-attempt pass rates and measure the
  gain from iteration; one paper reports 9 of 50 strict passes with up to ten attempts for a strong agent.
- **Used for:** the autonomy ladder (§2.4), and for finding which checks to build next.
- **Cost.** Roughly an hour of agent time per hard task in the published work. Start with ten tasks.

### 8.5 Measures

| Measure | Target |
|---|---|
| Benchmarks passing at the pinned version | All, or the template is withdrawn |
| Reported numbers that resolve to a run record | 100 %, enforced by `mu check` |
| Unverified items per report | Tracked; falling per area as templates land |
| Agent evaluation strict-pass rate, by area | Reported per release; a threshold per autonomy level is set in the org profile |
| Checker findings per package, by severity | Tracked; the basis for promotion |
| Seeded errors caught by the checker | Sampled; guards against over-trust |
| Checker time per package | Tracked; the measure of whether this is working at all |

---

## 9. Phases

### 9.1 Order and dependencies

```
 E0 ──► E1 ──┬──► E2 ────────────────────┐
             └──► E3 ──► E4 ──┬──► E5 ───┼──► E7
                              └──► E6 ───┘
```

- E0 and E1 come first because every later result needs a truthful driver and somewhere to put evidence.
- E2 and E3 are independent of each other after E1.
- E4 needs E3's units and joint calculations (materials come from E1). E5 needs E3 and E4.
- E6 needs E4's typed-study machinery and can run alongside E5.
- None of E0 to E3 needs remote compute: PLAN.md already lists checks and reports as local. E4's convergence studies and
  E6 benefit from the compute node, which has run jobs through `mu submit` since v2026.11.8.
- Against `docs/ROADMAP.md` (pairing fixes, then the dashboard, then the shell experience): E0 touches only μ's
  engineering code, benchmarks and instructions and changes nothing on the node, so it could run alongside the
  dashboard work. Everything from E1 on waits for the owner to place it (decision 14).
- E1 overlaps with that work and must share with it, not duplicate it: the dashboard's `jobs.index` and its recording
  of local `mu run` work (steps 3.1a and 3.3) are the base for run records, and the shell experience's Claude Code
  hooks and status data (step 4.1) are the base for `mu context` and the hooks here.

Sizes are relative: **S** a few focused sessions, **M** several, **L** many, with benchmarks dominating the work.

### 9.2 Phase detail

**E0. Stop misleading (S).**

| Deliverable | Done when |
|---|---|
| Fix D1 and D4 to D11; thread counts from detected hardware; correct D12's docstring | New benchmarks cover each fixed path and fail on the old code |
| Withdraw `summarize`'s margin and name-based criterion (D2, D3) until E1 and E4 replace them; keep stress percentiles as plain results | No μ function returns a margin from a raw peak |
| `mu geom check`, `mu dfm`, `mu interference`, `mu ranges`, `mu mass` as thin wrappers with `--json` and exit codes | Each has a benchmark, including one seeded-defect part per manufacturing process |
| `claude/CLAUDE-toolchain.md` documents them. The validation policy stays in the org profile, as PLAN.md §8 says (decision 13 asks whether μ should also ship a fallback summary) | A fresh project sees the DFM check without reading the vendored skill |
| Doctor checks for the text-to-cad commands; Tier 0 solver suite | A text-to-cad or CalculiX change that alters a verdict fails the gate |
| Appendix B's E0 rows closed | `--json` on every existing command; `THIRD_PARTY.md` written; the vendored skill's dangling references removed |
| Spikes ES1 and ES2 | Recorded in `docs/verification/` |

**E1. Evidence backbone (M).** Delivers the core of PLAN.md P2. The remaining P2 skills arrive with their phases.

| Deliverable | Done when |
|---|---|
| Schemas for finding, run record, requirement, material, criteria, review record; `mu.record`, `mu.findings`, `mu.materials` | Every existing analysis command writes a run record and findings |
| `mu context`, `mu req`, `mu check`, `mu report`, `mu review` | In a fresh project, a report cannot be built from a number that has no run record, and a changed input makes its review stale |
| `mu new` with the project layout of §6.4; `validation` and `report` skills; the hooks of PLAN.md §6.5 | Claude Code in a new project designs a test part and produces a DFM and mass report with a compliance matrix, using only `mu` commands |
| Org profile: validation policy, material records for the JATAYU materials, a criteria template, report and review templates | A human has approved each material record |
| Agent evaluation harness with the first ten tasks (ES7) | A baseline pass rate is recorded |
| A command that re-runs a project's validation after its pin moves and compares the results (PLAN.md §9: done by hand today) | A pin change on JATAYU produces a diff of results from run records |

**E2. Manufacturing package (M).**

| Deliverable | Done when |
|---|---|
| Shop profiles and `mu dfm --shop`; three-tier verdicts | Two real shops are profiled from their published limits |
| Evidence images per finding; dispositions and waivers | A DFM report for a JATAYU machined part and a printed part is accepted by a manufacturing reviewer |
| Layup schema and composite rule checks | The JATAYU plates and tubes are checked against AA-SB-001 rules |
| `mu dfa` worksheet; `mu cost` with low, likely, high from slicer and CAM output | Cost ranges for a reference part set are compared with real quotes and the error is recorded |
| Mass ledger with growth allowance and CG envelope | The JATAYU ledger reproduces `mass.json` and adds configurations |
| Seeded-defect suites for every process; `manufacturing` skill | False-alarm and miss rates are published per rule |

**E3. Calculation layer (M to L).**

| Deliverable | Done when |
|---|---|
| Lock bump 1: units library (§12); `mu.units`, `mu.calc` framework with source metadata and validity assertions | A dimensional error raises; every function names its source and equation |
| Bolted joints to NASA-STD-5020B; thread pull-out and inserts to ECSS-E-HB-32-23A | The ECSS worked examples reproduce to the printed precision; 5020B cases are checked by hand and by a second implementation |
| Laminate theory with first-ply failure; composite bolted-hole checks | Reference laminates match an independent tool's stored results |
| Lugs, buckling, fits, Hertz, bonded joints, beams and plates | Each has a textbook case in Tier 4 |
| Calculation sheets in JSON, Markdown and docx | A checker can follow a sheet line by line without opening code |
| `mu tol`: 1D stacks and Monte Carlo on the parametric model | JATAYU's `tolerance.json` is reproduced; a model change regenerates the stack |
| Fatigue screening for metals; `calc` skill | Stress-ratio screen with stated limits appears in the margin table |

**E4. FEA you can sign (L).**

| Deliverable | Done when |
|---|---|
| Typed study, `mu fea`, selection by CAD face (ES5) | A renamed or removed face makes the study return `not_checked`, not a wrong answer |
| Pre-solve and post-solve gates; mesh gate; warnings ledger | A model with a missing constraint, an unbalanced load or an inverted element is stopped before or flagged after solving |
| `mu fea converge` with observed order and GCI; singularity classifier | The cantilever's singular root peak is classified singular automatically, and its bending stress converges |
| Margins by failure mode from materials and criteria; free-body output | The JATAYU hub and arm produce a minimum-margin table; a hand check from the free-body data agrees within a stated band |
| Templates: contact, pretension, linear buckling, plasticity, thermal stress, orthotropic solids, modal and steady-state dynamics | One Tier 2 benchmark each, passing on both platforms |
| `fea` skill | The skill states each template's envelope and what it does not capture |

**E5. Airframe substantiation (L).**

| Deliverable | Done when |
|---|---|
| `mu loads` with the multirotor case set; criteria file with a cited factor set | A loads report for JATAYU with an equilibrium check per case, approved by a human |
| Composite strength route decided (ES3) and built | Plate and tube benchmarks pass; laminate margins appear per ply and failure mode |
| `mu vib`: Campbell check, forced response, vibration fatigue; lock bump 2 | Modal results are compared with the RPM range; a single-degree-of-freedom benchmark validates the PSD route |
| Test predictions and correlation tables | An arm bending test and a tap test have predictions issued before the test |
| `loads` and `vibration` skills; stress report template; compliance matrix against the chosen basis | **One complete JATAYU stress report is checked by a human engineer, with checker time and findings recorded** |

**E6. Aero, propulsion, thermal (M to L).**

| Deliverable | Done when |
|---|---|
| CFD templates: external bluff body, propeller MRF; gates and GCI | Tier 3 benchmarks pass; JATAYU frame drag is reproduced with a convergence table and an accuracy band |
| Propeller and powertrain model | Thrust and power match UIUC data for a reference propeller within a stated band |
| Thermal network; thermal-stress template in use | Component temperatures over a mission, with the calibration test named |
| `cfd`, `aero` skills | Each states where the method is not credible |

**E7. Judgement and autonomy (M, then continuous).**

| Deliverable | Done when |
|---|---|
| `mu fmea` with coverage checking; safety artefacts | A JATAYU mechanical FMEA draft is reviewed and the human-added rows are counted |
| `mu gate` for preliminary and critical design reviews | Gate status is computed from the repository |
| Drawings and semantic PMI (ES6) | A decision record: adopt, defer or leave with a human |
| `review-prep` skill; seeded-error audits of the checker; promotion rules in the org profile | The first area is promoted to Level B on recorded evidence, or the evidence says not yet |
| A short roadmap for electrical design | Written from what E0 to E6 taught |

### 9.3 Spikes

Each tests an assumption a phase depends on, and runs at the start of that phase or before it. Records go in
`docs/verification/`. Timing: ES1 and ES2 in E0; ES7 in E1; ES4 at the start of E3; ES5 at the start of E4; ES3 during
E4, before E5 commits to a route; ES6 in E7.

| Spike | Tests | If it fails |
|---|---|---|
| **ES1** | Run `cad model dfm` on the benchmark plate, on planted-defect parts and on two JATAYU parts, on both platforms. Does it behave as the skill documents? Is the JSON stable enough to wrap? | Pin the observed behaviour as the benchmark; report gaps upstream; narrow E2 to the processes that work |
| **ES2** | CalculiX facts behind D1, D4 and D11: `.frd` stress component order, load carry-over between steps, the `.frd` reader | Adjust the defect list; the fixes are small either way |
| **ES3** | CFRP plate and tube with hand solutions, modelled as orthotropic solids, as CalculiX composite shells, and in a second solver | Choose the route of §7.7. If all are poor, laminate strength stays by calculation and FEA is used for load paths only |
| **ES4** | Lock bump 1 solves on both platforms at identical versions, and the benchmark suite passes on a fresh build | Use an in-house units layer and defer the dependency |
| **ES5** | Map named build123d faces to Gmsh surfaces after fragmenting, on parts with curved and split faces | Fall back to geometric predicates generated from the face, with a resolution check |
| **ES6** | Write semantic PMI through OCCT, read it back with an independent parser, check against the NIST test models | Drawings and GD&T stay fully manual for now |
| **ES7** | Ten agent-evaluation tasks end to end, once E1's project format exists | Fix the harness before the baseline is trusted |

---

## 10. The human's role

### 10.1 What the published positions require

Four texts were found: the engineering regulators of British Columbia (adopted by Ontario), North Carolina, and
Newfoundland and Labrador, and the federation Engineers Europe. Read together they amount to five tests. The list is
our synthesis of them, not a joint statement. The responsible engineer must:

1. be competent in the domain without the AI;
2. understand how the result was produced;
3. have verified it by a means independent of the AI;
4. be able to answer for every engineering decision;
5. hold records proving the above: tool and version, inputs, outputs, verification steps.

North Carolina's wording is the strictest: "capable of reproducing the output independently". A signature on an unread
package meets none of these. Aviation regulators have not yet addressed generative AI used as an engineering tool; the
tool-qualification logic of DO-330 (read through summaries) applies by analogy: while every output is independently
checked, the tool needs no qualification, and reducing the check is a claim that needs evidence of the tool's
reliability.

Gaps: no text found says what depth of checking is sufficient, and none addresses autonomous agents. Positions from
other bodies (NSPE, Engineers Australia) were found but do not set out these tests. Engineers Canada and defence
guidance were not researched, and no reliable source was found on the Indian position.

μ's records cover item 5. The review record (§6.3) is where item 3 is captured.

### 10.2 Three checkpoints

| When | The human | Why |
|---|---|---|
| Before geometry | Approves the plan: requirements, criteria, materials, load cases, analysis list | Catches a misread intent before work exists. Found independently in research and practice |
| During | Answers when a command returns `not_checked` for a missing input or a deviation is needed | The agent asks; it does not assume |
| After | Checks the package and signs the review record | The independent verification |

### 10.3 The checker's protocol

From published checking procedures and the studies of people reviewing AI output. It is inferred from those studies;
none of it has been tested with engineers reviewing analysis, so μ measures it (§10.4).

- **Estimate first.** The checker records a bounding hand calculation before reading the agent's margin. The package
  supplies the free-body loads and section properties to make this quick.
- **Assumptions first.** Each assumption gets a disposition. In one 2026 preprint this was the intervention that
  reduced over-reliance without adding load.
- **No AI self-review up front.** Reviewers anchor on what is flagged and stop looking elsewhere.
- **A different path.** Re-running the agent's script is not a check. Use a hand calculation, a bounding closed form,
  a different mesh or a different tool.
- **Sign per item.** One disposition per check item, not one overall approval.
- **Sample what was not flagged,** weighted by criticality.
- **A second AI instance is not an independent checker.** It shares the prompts, tools and inputs. This is an inference
  from the independence tests in the checking procedures, not a quoted rule. A critic pass by the agent is useful
  before the human sees the package; it is not the check.

### 10.4 Keeping the checker honest

Trust rises as the agent is usually right, which is when over-reliance is most likely.

- A small share of packages carry a seeded error. The catch rate is the only direct measure of detection.
- Record checker time, findings by severity, and how much of the package was opened.
- Rotate checkers where there is more than one.

### 10.5 What does not delegate

| Stays human | Why |
|---|---|
| Approving loads, factors, materials and allowables | Every margin inherits them |
| The datum scheme, functional tolerances, drawing release | No measured evidence that models judge GD&T |
| Severity ratings and completeness of failure modes | Omission is the measured weakness of generated FMEA |
| Anything outside a template's envelope, and any new method | "New analytical techniques" is the line UK-SPEC draws |
| Test planning decisions and correlation judgement | The link to reality |
| The signature | Every published position |

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| **A confident wrong report.** The largest risk, and the reason for most of this design | Result states, gates, benchmarks per template, coverage sections, margins refused on typical allowables, the review record |
| The agent weakens its own gates | Thresholds in the org profile; changes are diffs with reasons; empty runs fail; the stub audit in `mu check` |
| Checker over-trust | §10.3 and §10.4 |
| text-to-cad is a young, fast-moving, single-maintainer dependency whose verdict semantics have already changed once | Pinned by commit; seeded-defect benchmarks pin behaviour; μ wraps it, so the engine could be replaced behind `mu dfm` |
| CalculiX shells and thin composites | ES3 before any commitment; calculation-based laminate strength meanwhile |
| Published rule values are not this shop's | Tiers and shop profiles; the report says which tier judged each finding |
| Allowables without a statistical basis | Basis is carried to every margin; knockdowns are explicit; coupon tests are named as the way out |
| Standards read in old or proposed editions, or only through summaries (§14 lists them) | The criteria file cites edition and clause; a human confirms against the current text before first use |
| Dependency and layer cost on a 16 GB laptop | In-house where small; two lock bumps; GPL tools as external commands only |
| Scope: this is a lot | Each phase ends in something used on JATAYU. E0 to E2 pay back on their own |
| Benchmarks give false comfort | Convergence-style tests, both platforms, seeded defects, and the agent evaluation, which tests the whole loop |
| Cost of iteration | The literature's strong agent needed about an hour per hard task. Budget per task; many cheap solver runs are expected |
| Evidence gaps in the research itself | Listed in the research report: several vendors and standards were not read, and a search budget ran out part-way. §14 lists the sources this roadmap names without having read the primary text |

---

## 12. Dependencies

Principle from §5.10. Every lock change is one new environment layer on every machine and a full benchmark run.

| Bump | Adds | For | Licence | Notes |
|---|---|---|---|---|
| **1, in E3** | pint | Units on every quantity | BSD | Needs Python 3.12 or later; the environment is 3.13 |
| **2, in E5** | pyLife; FLife | Stress-life fatigue, rainflow; spectral vibration fatigue | Apache-2.0; MIT | Both active in 2026. Check their transitive dependencies against the OCP and VTK pins |
| Data only | CalculiX test suite | Tier 0 | Ships with the CalculiX source | A small pinned archive in `apps.toml`; no code is linked |
| Decided by a spike | A second solver for shells; a section-polar code; propeller tools | ES3; E6 | Varies | Each is a new application on two platforms. Not assumed |

Deliberately **not** added:

| Not added | Instead |
|---|---|
| A VDI 2230 package | None is dependable. Public NASA and ECSS methods are implemented in-house |
| composipy, dimstack, eLamX² as runtime dependencies | Textbook hand solutions are the primary reference. These tools may supply a second opinion, generated once outside the toolchain and stored with provenance: composipy describes itself as for learning, and eLamX² is a Java GUI |
| pygccx, py-fatigue, anastruct (GPL); draftwright, pycba (AGPL) | Read for ideas or invoked as external commands; never imported |
| A templating or PDF library | Reports are Markdown plus python-docx, both already pinned |
| A requirements tool | In-house TOML unless a customer needs ReqIF |

Already pinned and sufficient for most of E0 to E4: numpy, scipy, sympy, pandas, shapely, trimesh, networkx, pydantic,
python-docx, matplotlib, casadi, nlopt, ezdxf, the Gmsh API, PyVista, CalculiX.

---

## 13. Decisions needed

Each has a default, so work can start on E0 without waiting.

| # | Decision | Default if none is given |
|---|---|---|
| 1 | **Structural design basis.** Which authority's or customer's requirements govern JATAYU? | A `criteria.toml` citing NATO AEP-83 factors (a fixed-wing standard, used by analogy) and the EASA thrust-based multirotor load factor, marked provisional until confirmed against the governing text. The research also read India's QCI scheme; no other national scheme was researched |
| 2 | **Who checks.** Who is the checker, and how much time per package is acceptable? | The owner checks; time is recorded from the first package |
| 3 | **Org profile.** Does `mu-org-profile` exist, and who approves a material record? | μ defines the schemas; JATAYU's materials are entered in the org profile with status provisional |
| 4 | **Report format.** Is docx required for issue? | Markdown in git as the source; docx generated through the org template |
| 5 | **Shops.** Which two shops to profile first? | One CNC service and one sheet or print service with published limits |
| 6 | **Licences.** May GPL and AGPL tools be invoked as external commands? | Yes as commands, never imported. `THIRD_PARTY.md` is written in E0 |
| 7 | **A second solver,** if ES3 recommends one | Not adopted without a decision record like ADR-001 |
| 8 | **Material data.** Buy MMPDS, or rely on supplier certificates? | Supplier certificates and vendor minima; NCAMP for composites, marked not process-qualified |
| 9 | **Project scripts.** Which JATAYU scripts (tolerance, checks, mass, report) are generic enough to move into μ, as `felib.py` did? | Review them in E1; the project repository was not visible to this review |
| 10 | **text-to-cad's companion skills.** Adopt, or strip the references from the vendored skill? | Strip the references; μ ships its own skills |
| 11 | **Agent evaluation budget.** How much agent time per release? | Ten tasks per release |
| 12 | **The raw research notes** (about 600 KB, with every source link). The report is in `docs/research/` | The notes stay out of the repo unless wanted |
| 13 | **Policy without an org profile.** Should μ ship a short fallback summary of the validation policy for projects that have no org profile? This would change PLAN.md §8 | No. `mu context` warns when a project has no org profile |
| 14 | **Where this fits in the order of `docs/ROADMAP.md`** (pairing fixes, dashboard, shell experience) | E0 alongside the dashboard work, since it changes nothing on the node. E1 and later when the owner places them, built on the dashboard's and the shell's status data |
| 15 | **`cad run --with` and script-header dependencies.** The vendored skill allows them; μ's toolchain is sealed and offline-first | Not allowed under μ; the skill is edited to say so |

---

## 14. Sources

The research report gives the full citations, the evidence quality of each, and the gaps. The references this roadmap
leans on most:

**Agent performance and design**
- [CFDLLMBench](https://arxiv.org/abs/2509.20374); [Foam-Agent 2.0](https://arxiv.org/abs/2509.18178);
  [ChatCFD](https://arxiv.org/abs/2506.02019); [FEABench](https://arxiv.org/abs/2504.06260)
- [Self-improving CAD agents with FEA as feedback](https://arxiv.org/abs/2605.17448);
  [CADEngBench](https://arxiv.org/abs/2608.09296); [CADTests](https://arxiv.org/abs/2605.07807)
- [A constrained natural-language interface for FE simulation](https://arxiv.org/abs/2606.10928);
  [VFEAgent](https://arxiv.org/abs/2605.28978); [EngDesign](https://arxiv.org/abs/2509.16204);
  [DesignQA](https://arxiv.org/abs/2404.07917); [MechVQA](https://arxiv.org/abs/2605.30794)
- [GPT-4V on engineering design tasks (Picard et al.)](https://arxiv.org/html/2311.12668v3);
  [MUSE](https://arxiv.org/abs/2605.28579)

**Simulation credibility**
- [NASA-STD-7009B](https://standards.nasa.gov/sites/default/files/standards/NASA/B/1/NASA-STD-7009B-Final-3-5-2024.pdf);
  [Celik et al., procedure for estimating discretization error](https://qa12.asme.org/wwwasmeorg/media/ResourceFiles/Shop/Journals/JFENumAccuracy.pdf)
- [CalculiX 2.23 manual](https://www.dhondt.de/ccx_2.23.pdf);
  [CoFEA hemispherical shell benchmark](https://cofea.readthedocs.io/en/latest/benchmarks/005-hemispherical-shell-point-load/results.html);
  [CoFEA thick plate (LE10)](https://cofea.readthedocs.io/en/latest/benchmarks/001-thick-plate/results.html)
- [AFGC guide: presenting a finite-element calculation note](https://wiki.afgc.asso.fr/books/finite-element-modeling-and-computations-in-the-field-of-civil-engineering/page/f-how-to-properly-present-the-finite-element-calculation-note);
  [UIUC propeller database](https://m-selig.ae.illinois.edu/props/propDB.html)

**Calculation methods and data**
- [NASA-STD-5020B](https://standards.nasa.gov/sites/default/files/standards/NASA/B/0/2021-08-06-nasa-std-5020b_final.pdf);
  [ECSS-E-HB-32-23A Rev. 1](https://ecss.nl/wp-content/uploads/2023/02/ECSS-E-HB-32-23A-Rev.1(6February2023).pdf);
  [NASA-STD-5001B](https://standards.nasa.gov/sites/default/files/standards/NASA/B-w/CHANGE-3/3/2022-10-24-NASA-STD-5001B-w-Change-3-Approved.pdf)
- [Hart-Smith, adhesive-bonded joints](https://ntrs.nasa.gov/api/citations/19740005082/downloads/19740005082.pdf);
  [NASA SP-8007, 2020 revision](https://ntrs.nasa.gov/citations/20205011530);
  [Abbott Aerospace AA-SB-001](https://abbottaerospace.com/?p=55173)
- [NCAMP](https://www.wichita.edu/industry_and_defense/NIAR/Research/ncamp.php); [MMPDS](https://www.mmpds.org/);
  [14 CFR 25.613](https://www.law.cornell.edu/cfr/text/14/25.613)
- [pint](https://pypi.org/project/pint/); [pyLife](https://github.com/boschresearch/pylife);
  [FLife](https://github.com/ladisk/FLife); [composipy](https://github.com/rafaelpsilva07/composipy);
  [dimstack](https://github.com/phcreery/dimstack); [handcalcs](https://github.com/connorferster/handcalcs);
  [pygccx](https://github.com/calculix/pygccx)

**Manufacturing**
- [Protolabs Network CNC design guide](https://www.hubs.com/knowledge-base/how-design-parts-cnc-machining/);
  [SendCutSend guidelines](https://sendcutsend.com/guidelines/cnc-machining/);
  [text-to-cad](https://github.com/earthtojake/text-to-cad) and
  [an independent review of it](https://andrew.ooo/posts/text-to-cad-review-agent-cad-plugin/)

**Airframe criteria**
- [NATO AEP-83 / STANAG 4703, Edition A (2014)](https://assets.publishing.service.gov.uk/media/5a7ec5c940f0b62305b83155/20140916-STANAG-4703_AEP-83_A__1_.pdf)
- [EASA means of compliance for UAS operated in SAIL III](https://www.easa.europa.eu/en/document-library/product-certification-consultations/means-compliance-moc-design-uas-operated-sail)
- [QCI UAS certification scheme FAQ (India, 2024)](https://padd.qci.org.in/wp-content/uploads/2024/04/QCI_UAS_FAQs_V3_23_Apr_2024.pdf);
  [ArduPilot vibration guidance](https://ardupilot.org/copter/docs/common-measuring-vibration.html)

**Design assurance and oversight**
- [ECSS-E-ST-32C](https://ecss.nl/wp-content/uploads/standards/ecss-e/ECSS-E-ST-32C31July2008.pdf);
  [NPR 7123.1D Appendix G](https://nodis3.gsfc.nasa.gov/displayDir.cfm?Internal_ID=N_PR_7123_001D_&page_name=AppendixG);
  [GSFC-STD-1001A](https://standards.nasa.gov/sites/default/files/standards/GSFC/A/0/gsfc-std-1001-a_extension_04-01-2020.pdf)
- [Doorstop](https://doorstop.readthedocs.io/en/latest/);
  [Process Run Crate](https://www.researchobject.org/workflow-run-crate/profiles/process_run_crate/)
- [EGBC practice advisory on AI, as adopted by PEO](https://www.peo.on.ca/sites/default/files/2025-11/EGBC_Use_of_Artificial_Intelligence_in_Professional_Work.pdf);
  [North Carolina board guidelines](https://www.ncbels.org/wp-content/uploads/2025/07/Guidelines-on-the-Use-of-Artificial-Intelligence-in-Engineering-and-Surveying-Services-FINAL.pdf);
  [PEGNL guideline](https://pegnl.ca/wp-content/uploads/2026/06/PEGNL-Practice-Guideline-for-the-Use-of-AI-V6.pdf);
  [Engineers Europe advice note](https://engineerseurope.com/sites/default/files/2026-07/Advice_Note_on_AI.pdf)
- [AI-assisted code review experiment](https://arxiv.org/html/2411.11401v3);
  [NAFEMS PSE levels](https://nafems.org/professional-development/certification/about/levels);
  [UK-SPEC competence table](https://www.engc.org.uk/media/n2edd124/uk-spec-competence-table.pdf)

**Named in this roadmap but not read in the primary text.** Each needs a human to open the current document before μ
relies on it.

| Source | How it was read |
|---|---|
| NATO AEP-83 Edition 3 (2023) | Paid. The 2014 Edition A was read |
| prEN 4709-001 and the final EASA means of compliance | Only EASA's redlined proposal of July 2025 |
| ASTM F3298, STANAG 4671 | Paid; not read |
| ANSI/AIAA S-120A | Paid; public summaries only. It is written for space systems |
| AIAG-VDA FMEA handbook; AS9100D; DO-330 | Secondary sources and vendor summaries |
| ASME V&V 40 | A journal paper and slides, not the standard |
| Air Force Stress Analysis Manual (lugs); ISO 286; ASTM E1049; Roark | Secondary summaries or package documentation |
| NAFEMS benchmark problem statements | Targets not transcribed; redistribution licence unknown |
| NASA SP-8007 knockdown values; MMPDS | Not retrieved; MMPDS is paid |

---

## Appendix A. Small-UAV substantiation checklist, mapped to phases

From the research, condensed. "CS" marks a company-set criterion, where no standard value was found.

| # | Analysis | Acceptance basis | Today | Phase |
|---|---|---|---|---|
| 1 | Design basis and compliance matrix | Every requirement has evidence | None | E1 |
| 2 | Mass properties ledger | Predicted mass within allowable; CG inside envelope in every configuration; overweight case | Mass only | E2 |
| 3 | Flight loads, multirotor | Thrust-based load factor; differential thrust, yaw, motor-out, gyroscopic and imbalance cases; equilibrium per case | Typed by hand | E5 |
| 4 | Flight loads, winged or VTOL | V-n and gust envelope (AEP-83) | VSPAERO present | E5, later |
| 5 | Landing, handling, transport | Drop height or sink speed; masses retained at the landing factor | None | E5 |
| 6 | Static strength, metallic fittings | No yield at limit; no rupture at 1.5 × (1.25 where not hazardous); fitting factor 1.15; margins not negative | Partial | E4 |
| 7 | Composite plate and tube strength | First-ply failure or strain limits, with a hot-wet factor | Isotropic solid | E3, E5 |
| 8 | Bolted joints in composites | Bearing, bypass, e/D, w/D, pull-through | None | E3 |
| 9 | Clamps, tube crushing, bonded inserts | No slip at limit; no crush; adhesive margin with factors (CS) | None | E3, E4 |
| 10 | Buckling | Factor at least 1.0 at ultimate, with knockdown (CS) | None | E3, E4 |
| 11 | Printed parts | Margins with interlayer and temperature knockdowns; process specification and tests | Slicing only | E3, E5 |
| 12 | Stiffness and deflection | Arm tip and thrust-vector tilt limits (CS) | Exists | E4 |
| 13 | Modal and Campbell | No mode inside the company band of 1/rev or blade-pass at dwell speeds (CS) | Modal exists | E5 |
| 14 | Forced response, IMU and gimbal vibration | Published IMU vibration bands; isolator frequency rule | None | E5 |
| 15 | Vibration fatigue and durability | Damage below 1 with a scatter factor; life limit stated | None | E5 |
| 16 | Flutter and divergence (winged) | Free to 1.2 × dive speed | None | Later |
| 17 | Propulsion performance | Hover throttle and thrust-to-weight targets (CS); endurance at maximum weight | None | E6 |
| 18 | Drag, download, interaction, stability | Performance and control margins in maximum wind (CS) | Frame drag CFD | E6 |
| 19 | Thermal | Component temperatures below ratings; structure at 54 °C or an extra 1.25 factor | None | E6 |
| 20 | Thermal-structural mismatch | Bond and insert margins at temperature extremes (CS) | None | E4 |
| 21 | Environmental design review | IP target; galvanic isolation; drainage | None | E6 |
| 22 | Safety artefacts | No single structural failure with catastrophic effect without margin and test evidence | None | E7 |
| 23 | Test plans and predictions | No detrimental deformation at limit; no rupture at ultimate, with the hold time from the governing text; correlation table | None | E5 |
| 24 | Manufacturing substantiation | Conformity within tolerances; process specifications | Partial | E2, E3 |

## Appendix B. Where stated rules and the code still disagree

PLAN.md v6 corrected the documentation drift the first review found. These remain.

| Stated | Today | Phase |
|---|---|---|
| CLAUDE.md: every capability is a CLI command with `--json` | The job-layer commands, `doctor`, `version` and `autoupdate` have it. `render-png`, `mu link`, `mu pin` and others do not; FEA is a library with no command | E0 for existing commands; E4 for `mu fea` |
| CLAUDE.md: no hard-coded hardware sizes | Thread counts of 4 in `mu.fea` (D9) | E0 |
| PLAN.md §6.5 and §6.7: a hook warns when a solver runs outside `mu`; every run has a record | No hooks; a local `mu run` leaves no record | E1 |
| PLAN.md §11 and `LICENSE`: a `THIRD_PARTY.md` | Not written | E0 |
| `toolchain/env.yml` header: "recreate: micromamba create …" | Legacy text; the installer does not read the file | E0 |
| `benchmarks/cfd-cavity/bench.py` docstring: a vortex as a known answer | No check reads the velocity field (D12) | E0 |
| The `cad` skill points to `fea`, `print3d`, `sendcutsend` and `robot-simulation` skills | None is shipped (decision 10) | E0 |
