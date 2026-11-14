# μ node scheduler: several jobs at once, by the cores and memory each needs

**Status: design, nothing built.** Written on branch `scheduler` from `main` at a3a7016 (v2026.11.8, 2026-11-14), from
the research note of the same day (build or adopt, algorithm, enforcement, WSL2 hybrid cores) and the code it cites.
Every claim about μ's code was read in `src/mu/node.py`, `src/mu/jobs.py`, `src/mu/proto.py`, `src/mu/fea.py`,
`install/hosts/wsl2/mu-wsl2-distro.sh` and `tools/test-jobs/`. **Nothing here has run on the office PC.** The probe job
that settles the PC facts (job `20261114-161025-2icaxr`, "sched-probe", from `~/mu-first-job`) is still queued behind
the owner's JATAYU jobs, which is the very problem this design solves. §12 lists each unverified fact, the probe item
that settles it, the pass criterion and the fallback, so the design does not wait on any of them. The lead adds the
probe's results to §12 when it has run.

Sections: [1 Goal](#1-goal) · [2 Build or adopt](#2-build-or-adopt-plan-17) · [3 Algorithm](#3-the-algorithm) ·
[4 Requests and defaults](#4-requests-and-defaults) · [5 Enforcement](#5-enforcement) · [6 Thread counts](#6-thread-counts-from-the-grant) ·
[7 Changes to the code](#7-changes-to-the-code) · [8 Rules for Claude](#8-rules-for-claude-code-claudeclaude-toolchainmd) ·
[9 Durability](#9-durability-and-recovery-with-n-running-jobs) · [10 Dashboard](#10-what-the-dashboard-shows) ·
[11 Compatibility](#11-compatibility-with-the-previous-release) · [12 PC facts](#12-checked-on-the-pc-before-the-build) ·
[13 Tests](#13-tests-ci-ubuntu-runner-real-systemd) · [14 PC checks after release](#14-pc-checks-after-the-release-by-jobs-no-visit) ·
[15 Effort and order](#15-effort-and-place-in-the-roadmap) · [16 Risks](#16-risks) · [17 Questions](#17-open-questions-for-the-owner) ·
[18 Read](#18-read-in-sources)

## 1. Goal

**Today** (job-layer §10, `step()` in `node.py`): the node runs one job at a time, first in first out. A job without
`--mem` gets `MemoryMax` = MemTotal − 2 GiB (~21 GB of the VM's 23 GB) and `MU_JOB_CORES` = all 20 cores, an
environment variable only. A 1-second diagnostic waits behind a long JATAYU sweep while most cores may idle (seen on
2026-11-14: 1 running, 3 queued).

**The owner's decision (2026-11-14):** run several jobs at once, scheduled by the cores and memory each needs; small
jobs start at once, big ones are protected from starving; built into μ's own runner after a build-or-adopt check;
designed now, built after the pairing fixes merge (they touch `node.py` too), before the dashboard.

**Goals**

1. Jobs whose requests fit together run together; the sum of memory grants never exceeds what the node can give, so
   one job's `oom` is never caused by another job.
2. A small job (`--quick`) starts within seconds, whatever is running.
3. A big job waiting at the head of the queue starts no later than a time μ can state (`starts_by`), however many
   small jobs arrive after it.
4. A job uses the CPUs it was granted, and its solvers are told how many (thread variables set by μ).
5. Claude Code, which submits almost every job, can size a job in one call, learns the right size from history, and
   reads in `mu status` why a job waits ("waiting for 12 cores / 16 GB").
6. **No PC visit.** Everything arrives as one release by auto-update; nothing needs root. A later root step (cpuset
   delegation) only hardens CPU placement and is listed for the next visit (§5.4).
7. Mixed versions keep working: an old workstation with a new node, and a new workstation with an old node (§11).

**Non-goals:** several nodes (Slurm's job if it ever comes, §2); priorities and fair share between workstations (one
submitter today, §3.7); P/E-core-aware placement (the guest cannot see or keep core types, §5.5); GPU scheduling.

## 2. Build or adopt (PLAN §17)

PLAN §17 already kept the job queue ("the μ-specific parts would remain anyway"). Re-checked for a resource-aware
scheduler:

| Candidate | What it needs on the node | Fit | Verdict |
|---|---|---|---|
| **Slurm** single node (slurmctld + slurmd, `select/cons_tres`, `sched/backfill`, `task/cgroup`) | Root install of `slurm-wlm` (noble: 23.11.4), munge or `auth/slurm` key, `slurm.conf`, `cgroup.conf`, two root daemons; slurmd runs as root and needs the **cpuset** controller in the distro's cgroup (unverified) | Best algorithm (consumable cores and memory, EASY backfill, cgroup enforcement) | **No.** Every install and config change is a root step on a PC that is "not to be disturbed"; two more daemons to keep alive through Windows restarts; μ keeps its snapshot, pin, gate, run records and safe fetch anyway and would translate jobs to `sbatch` and back. **The answer if μ ever schedules across several nodes** |
| **HTCondor** (partitionable slot) | Five daemons; cgroup enforcement wants a root install | Right model (dynamic slots by `request_cpus/request_memory`) | **No**: five daemons and ClassAds for one machine |
| **Nomad** (`exec`/`raw_exec`) | Root for cgroup isolation; BSL 1.1 since 1.7 | A service orchestrator, no backfill | **No** |
| **pueue** | Runs as the user | A slot count per group, no CPU or memory accounting; its children are not systemd units (μ would lose the per-job cgroup, limits, cancel, survival of runner restarts) | **No** (PLAN §17 said so already) |
| **GNU parallel / `xargs -P`** | Nothing | No persistent queue, no recovery | **Not a node scheduler; useful inside a job** (`parallel -j $MU_JOB_CORES`) |
| **systemd** `CPUAffinity=`, `MemoryMax=`, `CPUWeight=` | Already there (cpu, memory, pids delegated to `mu`'s user manager) | Enforcement, not admission or ordering | **Adopt as the enforcement** (§5) |

**Build** it into `mu node run`: an allocator, FIFO with EASY backfill, an express lane, sizing from history, on the
existing one-unit-per-job design. The machinery for concurrency is already there (one transient unit and cgroup per
job, per-job sealed state, a runner pass that already visits every running job, per-job recovery); what is missing is
**admission** (does it fit?), **placement** (which CPUs), **sizing** (how much?) and **ordering** (who first?).
Estimated size: about 700 lines plus tests, 5–7 days (§15).

## 3. The algorithm

### 3.1 Capacity

| Symbol | Value | Where it comes from |
|---|---|---|
| `C` | CPUs the runner may use: `len(os.sched_getaffinity(0))` (20 on the PC) | the kernel; `node.json` `sched.cores` may lower it |
| `M` | MemTotal − `reserve_system` − `reserve_express` | `/proc/meminfo`; reserves in `node.json` `sched` |
| `reserve_system` | 1 GiB: runner, sshd, tunnel, journald, git's page cache | default |
| `reserve_express` | 1 GiB, only for express jobs (§3.4) | default |
| `max_running` | 6 ordinary jobs at once | default; I/O is not delegated (`io` controller absent), so many writers on one disk would thrash |

With both reserves at 1 GiB, `M` = MemTotal − 2 GiB, **exactly today's `mem_default()`**, so a job that fits today's
default still fits the largest grant. Memory is a **hard** resource (over-commit ends in OOM) and admission is strict on
it. CPU is softer (over-commit only slows), but ordinary jobs are admitted strictly on cores too, so that a grant means
"these CPUs are yours". Only the express lane borrows CPU time (§3.4).

### 3.2 Request and grant

- **Request** `(cores c, mem m, time t, class)`: decided by the node at `submit` (§4) and written into `job.json`,
  which the node writes once at acceptance (it already adds `accepted_ns` and `key` there). `class` is `normal` or
  `express`.
- **Grant**: at start, the runner writes into `state.json`, **before** `systemd-run` (as `limits` already is):
  `{"cores": c, "cpus": [0,1,2,3,4,5,6,7], "mem_bytes": m, "time_s": t, "end_by": started + t + 10, "class": "normal" |
  "backfilled" | "express"}`. The 10 s cover `TimeoutStopSec=5` and one tick.
- **Allocation** = the grants of all running jobs, **rebuilt from their `state.json` on every pass**. There is no
  allocation file and nothing kept only in memory, so nothing can disagree with the jobs (§9).
- A running job **without a grant** (written by the previous release, rebuilt by `rebuild_state()`, or whose
  `state.json` raised an error this pass) holds the whole node: the current rule "no job is started while another
  one's state is unknown", kept.

### 3.3 Order: FIFO with EASY backfill

Each pass (once a second, under `/srv/mu/lock`), one pure function decides; the runner starts what it returns, and
`status` calls the same function to say why a job waits:

```
plan(now, capacity, running grants, queued requests) -> (starts, waiting_for per job, starts_by of the head)

1. free = (C − Σ cores, M − Σ mem, free CPU ids) over ordinary grants; a grant-less running job: free = 0.
2. Express jobs (§3.4) are placed first, from their own reserve.
3. Q = ordinary queued jobs by accepted_ns. While Q's head fits free and fewer than max_running run: start it.
4. Head does not fit: its shadow time S = the earliest end_by at which, releasing running jobs in end_by order,
   free ≥ head's request. extra = what is free at S beyond the head's request.
5. For each later job b in order, if it fits free now (and max_running allows):
     now + b.time ≤ S          → start it (class backfilled): it ends before the head needs its resources;
     b fits within extra        → start it, extra −= b: it uses only what the head will not need;
     otherwise                  → waits, waiting_for = {"reservation": {"head": …, "starts_by": S}}.
6. Safety valve: if now > S + 60 s (a unit outlived its RuntimeMaxSec), no backfill until the head starts.
```

- **Why the head cannot starve.** Every backfilled job either ends before `S` (its `RuntimeMaxSec` is enforced by
  systemd, so `S` is a hard bound, not an estimate) or uses only resources the head will not need at `S`. So the head
  starts at or before `S`. Jobs that end early only bring it forward. Each later job becomes the head in turn, so no
  job starves. The 60 s valve covers only a unit that refuses to stop.
- **CPU ids:** the 20 vCPUs are treated as identical slots (§5.5), so admission counts cores; the runner gives the
  lowest free ids. No contiguity is needed, now or with cpuset later.
- **Not chosen:** conservative backfill (a reservation for every queued job: more bookkeeping, nothing gained with a
  queue of a handful); priority classes (one submitter; FIFO is what Claude expects; a later `--priority` would only
  reorder Q, the backfill rule stays); a "waited longer than T" starvation guard (EASY already guarantees it; the
  valve in step 6 is the only guard).

### 3.4 Express lane

A job is **express** when its request is at most **1 core, 512 MiB and 10 minutes**: `mu submit --quick`, or explicit
flags within those bounds. Express jobs:

- start at once from `reserve_express` (at most 2 at a time, and their memory within 1 GiB), ignoring the ordinary
  queue; a third waits only for the express lane ("waiting for the express lane: 2 express jobs running");
- get affinity to **all** of `C` (they borrow CPU time, not cores). With cgroup group scheduling, a single thread in
  its own cgroup wins most of one CPU against a many-threaded job even at equal `CPUWeight`, so no higher weight is
  needed; the cost is one rank of a heavy job slowed for at most 10 minutes. Test Q06 measures it;
- end `timeout` at 10 minutes like any job (their `RuntimeMaxSec`);
- take nothing from the pool `S` is computed on, so they cannot delay the head.

This is the "small jobs start immediately" half of the owner's decision without weakening the "big jobs are
protected" half.

### 3.5 What `--time` means now

- Backfill uses `end_by` of running jobs and `time` of candidates. The default 12 h is **conservative**: an unflagged
  job is treated as 12 h long, so it can backfill only into `extra`, never ahead of a waiting head. Nothing breaks; it
  backfills less.
- A realistic `--time` (expected × 1.5–2) is what lets a medium job slip in ahead of a waiting big one. §8 tells Claude.
- The kill limit and the reservation stay on `--time`. Learned run times (§4.2) may later give an `estimated_start`
  in `mu status`, labelled a guess; a wrong guess must never delay the head or kill a job.

### 3.6 Disk

The existing rule stays per start: below `min_free_gb` no job starts; each waiting job's `waiting_for` says
`{"disk": {"free_gb": …, "min_free_gb": …}}`. Several jobs means several `work/` copies at once, so the check matters
more; the retention rule of ROADMAP §2.3 Q9 should land with or before this build.

### 3.7 Fairness, later

Each job carries `key` (the workstation's name on the node). With a second workstation: order Q by each key's decayed
usage (core-seconds, 24 h half-life; Slurm's fair-share idea), FIFO within a key; the backfill rule is unchanged. Not
built while ROADMAP §2.1 says no new machines.

## 4. Requests and defaults

### 4.1 What Claude passes

```
mu submit --quick -- bash -c 'cat /proc/pressure/cpu'              # express: 1 core, 512 MiB, 10 min
mu submit --name frame-fea --cores 8 --mem 12G --time 2h -- python fea_frame.py
mu submit --name sweep -- python sweep.py                          # sizes from history, else the whole node
```

All flags stay **optional**: a refusal teaches Claude nothing it can measure, while history does. `--quick` with an
explicit larger value is a usage error (`quick-too-big`, exit 2).

### 4.2 Sizing order, per dimension (decided by the node at `submit`, reported back)

| Order | cores | mem | time (kill limit) |
|---|---|---|---|
| 1 explicit flag | `--cores` | `--mem` | `--time` |
| 2 `--quick` | 1 | 512 MiB | 10 min |
| 3 history (same project and `name` on this node; `name` defaults to the command, so an unnamed job matches the same command) | the last grant (μ never shrinks cores by itself: thread counts follow the grant, so a change would change the run) | max working-set peak (`anon + shmem`) of the last 5 `succeeded` runs × 1.25 + 256 MiB, rounded up to 256 MiB; after an `oom` with grant g: at least 1.5 × g | **not learned**: 12 h. Learning never kills a job sooner than today |
| 4 nothing known | `C` (whole node) | `M` (today's default) | 12 h |

- Row 4 is today's behaviour: no job that runs today starts failing. Such a job runs alone (the express lane still
  serves diagnostics beside it), and the submit answer's `next` says "pass --cores/--mem/--time so other jobs can run
  beside it".
- **A learned memory size can be wrong** (a finer mesh under the same name). Rule: a job whose `mem` came from
  history and that ends `oom` is **re-queued once** at its old `accepted_ns` with `mem = M`, attempt + 1, with a marker
  line in the log (as the interrupted rule does) and the note "oom at a learned 4.6 GB; re-run with the whole node's
  memory". An explicit `--mem` is never retried. So learning costs at most one lost run, never a wrong answer.
- History needs **`usage`** in `result.json`, which the dashboard planned for its phase 3.1 (dashboard §4.2). The
  scheduler now comes first, so **this build takes that piece** (§7.1) and the dashboard reuses it unchanged.
- History is kept in `/srv/mu/history.jsonl` (one line per finished job: `project_id`, `name`, state, grant,
  `usage`), appended by the runner when it sees a result, not fsynced, **rebuildable** from the `result.json` files
  (rebuilt when missing or unreadable). `submit` reads only this file, never every job folder.

The submit answer gains:

```json
"sizing": {"cores": 8, "mem_bytes": 6174015488, "time_s": 43200, "class": "normal",
           "source": {"cores": "history", "mem": "history", "time": "default"},
           "why": "history: 3 runs of 'sweep' (working peak 4.6 GB); time: default 12 h, pass --time to start sooner"}
```

### 4.3 Requests larger than the node: `too-big`

- `cores > C` or `mem > M` (or `--quick` with more): **refused before the snapshot is uploaded**. `hello` gains
  `capacity`, the workstation compares, and exits 2 with `error: "too-big"`, the node's capacity, and the largest
  values it accepts in `next` ("the node takes at most --cores 20 --mem 21G").
- The node checks again at `submit` (an old workstation has no capacity check): `too-big`, node exit 64, which the
  client maps to exit 2 by this error code.
- Fits the node but not what is free now: queued, with `waiting_for` (§7.1).

## 5. Enforcement

### 5.1 Memory: `MemoryMax` = the grant

`MemoryMax` (proven on the PC by `mu node selftest`) and `MemorySwapMax=0` as today, set to the grant instead of
`M`. Admission keeps Σ grants ≤ `M`, so one job's OOM can never be caused by another. **Not `MemoryHigh`**: it
throttles instead of ending the job, and a throttled solver looks like progress and is not (the reasoning behind
`MemorySwapMax=0`). `memory.max` reclaims a job's own page cache before it kills.

### 5.2 CPU, now (no root step): affinity, set twice

| | (a) `AllowedCPUs=` (cpuset) | (b) `CPUQuota=` | (c) affinity |
|---|---|---|---|
| Needs | cpuset delegated (root drop-in) and present in the distro's cgroup (unverified) | nothing | nothing: same-UID `sched_setaffinity(2)` |
| Limits | placement, hard | CPU time per 100 ms, anywhere | placement, inherited across fork and exec |
| Can the job escape? | no | no, throttling is the enforcement | yes: a process may widen its own mask (MPI launchers do) |
| Tools see the right count | yes, including hwloc / Open MPI | no: everything sees 20 | libgomp, Python 3.13 `process_cpu_count()`, `nproc`: yes; hwloc / Open MPI: no |
| MPI / OpenMP behaviour | good | **bad**: throttled in bursts; spinning ranks stall each other | good |

**Choice: (c), set in two places, so it holds whatever the probe finds:**

1. **The shim** (`mu node run-job`, the unit's main process) calls `os.sched_setaffinity(0, grant.cpus)` before it
   starts `mu run …`. This needs nothing from systemd and is inherited by every child.
2. **The unit**: `-p CPUAffinity=<ids>` on `systemd-run`, **only if** the self-test showed a user unit accepts and
   applies it (§12 item 2; recorded as `node.json` `selftest.cpu_affinity`). Otherwise the property is left out, since
   a rejected property would make every start fail.
3. `MU_JOB_CPUS=<list>` and the thread variables (§6), so well-behaved tools never try to widen the mask.
4. **Open MPI** (toolchain: `openmpi-bin` from noble, 4.1.x): its default binding for more than 2 ranks is by
   package, which in this VM is all 20 vCPUs, i.e. it can **widen** the inherited mask. μ sets the envelope with the
   MCA parameter behind `--cpu-set` (expected `OMPI_MCA_hwloc_base_cpu_list=<ids>`; name confirmed by §12 item 3),
   fallback `OMPI_MCA_hwloc_base_binding_policy=none` (`--bind-to none`: ranks inherit the job's mask and the kernel
   places them inside it).
5. **Detection, not prevention.** The runner's 5 s sampler (§7.1) reads `Cpus_allowed_list` of the job's processes
   (`cgroup.procs`) and, on a mask wider than the grant, writes once to `events.log` and `state.json`
   `affinity_widened: {"comm": "…", "cpus": "0-19"}`, shown in `mu status` and in the result's `note`.
6. **No `CPUQuota`.** Possibly later as a loose backstop (grant + 1 core), never tight.

`CPUWeight` is left at its default for all jobs (pinned jobs on disjoint CPUs do not compete; express jobs win their
CPU without it, §3.4). The jobs are the company's own solver runs, not adversaries: cooperative confinement plus
detection is the right strength now; §5.4 hardens it.

### 5.3 What `hello` reports

`limits.cpu`: `"affinity"` now, `"cpuset"` once §5.4 is in place, `"none"` if even the shim's call failed (never seen
on Linux; the job then still runs, and the grant is only counted). `limits.cpu_affinity_unit: true|false` (item 2).

### 5.4 Later, with a root step (for the next PC visit, or ROADMAP step 4.2)

| Step | What | Gain | Needs |
|---|---|---|---|
| R-sched-1 | `ensure_cgroup_delegation` writes `Delegate=cpu cpuset io memory pids` in its drop-in (`user@.service.d/`), today written only when memory or pids are missing | `AllowedCPUs=` = the grant: hard placement; hwloc and Open MPI see only the grant; `io` for `IOWeight=` between concurrent writers | cpuset present in the distro's root `cgroup.controllers` (§12 item 1); the drop-in applies when `mu`'s user manager restarts, i.e. **one distro restart while no job runs** |
| R-sched-2 | `status` of the host script checks the user manager's `cgroup.controllers` for `cpuset io` | the owner sees it | — |

The code path stays the same: μ keeps affinity and adds `AllowedCPUs=` when the job cgroup's
`cpuset.cpus.effective` equals the grant after start (systemd does not fail a property whose controller is missing,
so μ checks the file rather than trusting the property). Nothing in this build waits for R-sched-1.

### 5.5 P/E cores under WSL2

- Client Windows uses the Hyper-V **root scheduler** ("the only supported hypervisor scheduler configuration for
  client systems"): each WSL2 vCPU is a thread of the VM's worker process, placed by Windows (Thread Director, QoS,
  power mode) like any other thread. There is no fixed vCPU → physical core mapping, and per-VM CPU controls are not
  supported with the root scheduler.
- The guest probably cannot see core types reliably (x86 has no generic `cpu_capacity`; `hybrid_cpu` and CPUID 0x1A
  depend on what Hyper-V passes through; a core type for a floating vCPU would mean nothing anyway). §12 items 5–6
  check it.
- **So:** the 20 vCPUs are identical slots. Pinning still separates jobs from each other (its purpose here) and saves
  guest-level migrations. A 20-rank MPI job runs at the pace of whichever vCPUs Windows has on E-cores at the moment,
  and a desktop's two memory channels saturate long before 20 cores: **two 10-core jobs side by side may beat one
  20-core job**. That is measured, not designed: the scaling series of §14 check 7.

## 6. Thread counts from the grant

Set by the runner in the unit's environment (`--setenv`), from the grant:

| Variable | Value | Read by |
|---|---|---|
| `MU_JOB_CORES`, `MU_JOB_CPUS` | c, e.g. `0-7` | μ wrappers, job scripts |
| `OMP_NUM_THREADS` | c | libgomp (CalculiX, Gmsh's OpenMP, OpenMP BLAS) |
| `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`, `NUMBA_NUM_THREADS` | c | numpy / scipy BLAS, numba |
| `PYTHON_CPU_COUNT` | c | Python 3.13 `os.cpu_count()` / `process_cpu_count()`, hence `multiprocessing`, `concurrent.futures` |
| `CCX_NPROC_EQUATION_SOLVER`, `CCX_NPROC_RESULTS` | c | CalculiX (default 1 thread per stage) |
| `OMPI_MCA_hwloc_base_cpu_list` (name per §12 item 3) | the CPU list | `mpirun` |
| `TMPDIR` | `/srv/mu/jobs/<id>/tmp` (made at start, removed when the result is written) | keeps concurrent jobs' temp files and Open MPI session directories apart |

Not settable by environment, so in code or guidance:

- **Gmsh** `General.NumThreads` defaults to 1; `fea.py` hard-codes 4 (line 41) and `run(ncpu=4)` (line 218). Both
  become `int(os.environ.get("MU_JOB_CORES") or <local default>)` in this build.
- **OpenFOAM**: `numberOfSubdomains` must equal the rank count: `foamDictionary -entry numberOfSubdomains -set
  $MU_JOB_CORES system/decomposeParDict`, then `mpirun -np $MU_JOB_CORES … -parallel` (§8).
- **Nested parallelism**: a job that runs c processes (MPI ranks, a pool, `parallel -j`) sets inner threads to 1
  (`mpirun -x OMP_NUM_THREADS=1`), or c × c threads compete inside the grant (§8).

A job whose code ignores all of this (hard-coded 20 threads) is still confined by affinity: it only slows itself.

## 7. Changes to the code

### 7.1 `src/mu/node.py`

| Area | Change |
|---|---|
| constants | `DEFAULT_RESERVE_SYSTEM = 1 << 30`, `DEFAULT_RESERVE_EXPRESS = 1 << 30`, `DEFAULT_MAX_RUNNING = 6`, `EXPRESS = (1, 512 << 20, 600)`, `EXPRESS_MAX = 2`, `GRANT_SLACK_S = 10`, `VALVE_S = 60` |
| `capacity(node)` (new) | `C`, `M`, reserves, `max_running` from the kernel and `node.json` `sched` (each key optional; the tests use it to make a small node) |
| `size_request(spec, node)` (new) | §4.2, from flags, `--quick`, `history.jsonl`, defaults; per-dimension `source`; raises `too-big` |
| `verb_submit` | calls `check_spec`, then `size_request`; writes `request` into `job.json`; answer adds `sizing`, `waiting_for`, `starts_by` |
| `plan(now, cap, running, queued)` (new, pure) | §3.3 and §3.4; returns starts with their CPU ids, `waiting_for` per queued job, `starts_by` of the head. Unit-tested without systemd |
| `step()` | replaces `if not running and queued:` with `plan()`; starts each returned job; a pass that would start more than one job writes each grant before its `systemd-run`, one after the other |
| `start_job(j, grant)` | grant into `state.json` before `systemd-run` (with `start_unconfirmed`, as today); `MemoryMax` = grant; `RuntimeMaxSec` = grant time; `CPUAffinity` when `selftest.cpu_affinity`; the §6 environment; `TMPDIR` made |
| `run_job` (the shim) | `os.sched_setaffinity(0, MU_JOB_CPUS)` before `Popen`; reads its own cgroup's `cpu.stat`, `memory.peak`, `memory.events` and the sampler's `usage.json` just before `write_result`; adds `usage` (dashboard §4.2 format, `source: "cgroup"`) |
| sampler (new, in the runner) | every 5th tick, **outside the lock**: for each running job, `cpu.stat`, `memory.current`, `memory.stat` (`anon`, `shmem`), `memory.peak`, `memory.events`, `pids.current` from the unit's `ControlGroup` (recorded in `state.json` at start, never guessed); keeps the running max of `anon + shmem`; replaces `jobs/<id>/usage.json` (rename, no fsync: a view); checks `Cpus_allowed_list` (§5.2 item 5). The dashboard later adds `metrics.jsonl` and `now.json` to the same loop |
| `classify` / `write_result` | runner-written results (`oom`, `timeout`, `interrupted`) take `usage` from `usage.json` (`source: "runner-sampled"`); `oom` with a history-sized `mem` → re-queued once with `mem = M` (§4.2); every result appends to `history.jsonl` |
| `interrupted()`, `start_failed()` | also drop `grant`; the next start makes a new one from `request` |
| `rebuild_state()` | a rebuilt running job has no grant: holds the whole node (§3.2) |
| `run_runner()` | the version switch: "no job is **starting**" (no `start_unconfirmed`) instead of "idle" (§9) |
| `selftest()` | adds a third unit with `CPUAffinity=0` that prints its `Cpus_allowed_list`: records `cpu_affinity: true|false`; not a gate for `submit` (the shim sets affinity anyway) |
| `hello_answer` | `features: ["sched"]`; `capacity: {cores, mem_bytes, reserve_system, reserve_express, max_running}`; `allocated`, `free`; `queue: {running, express, queued}`; `limits.cpu`, `limits.cpu_affinity_unit` |
| `status_of`, `verb_list` | per job: `request`, `grant`, `class`, `position` (in the ordinary queue), `waiting_for`, `starts_by` (head only), `affinity_widened`, `usage` (finished jobs) |

**State files**

| File | Change |
|---|---|
| `job.json` (sealed, written once by the node) | gains `request` `{cores, mem_bytes, time_s, class, source}` and `sched: 1` from the client (§11) |
| `state.json` (sealed, replaced) | gains `grant`, `cgroup`, `request_override` (only after the oom rule), `affinity_widened` |
| `jobs/<id>/usage.json` (new, view) | the sampler's latest figures and running maxima; rebuilt from the cgroup after a runner restart, so the maxima restart from the current values (acceptable: the shim's `memory.peak` is the kernel's own) |
| `jobs/<id>/tmp/` (new) | the job's `TMPDIR`; removed when the result is written |
| `/srv/mu/history.jsonl` (new, index) | §4.2; rebuildable |
| `node.json` | optional `sched: {cores, mem_bytes, reserve_system, reserve_express, max_running}`; `selftest.cpu_affinity` |
| `events.log` lines | `granted cores=8 cpus=0-7 mem=6.0G time=2h class=normal`; `backfilled: ends by 14:05 before the head's 14:20`; `express`; `affinity widened by mpirun: 0-19`; `oom at a learned size: re-queued with 21.0G` |

**`mu status` wording** (built in `jobs.py` from the JSON):

```
queued #1   frame-fea   waiting for 12 cores / 16 GB (8 cores, 10.5 GB free) · starts by 14:20 at the latest
queued #2   mesh-study  waiting: would delay #1 (frame-fea, starts by 14:20) · with --time ≤ 25m it would start now
running     sweep-3     8 cores (0-7) · 6.0 GB · ends by 14:20 · backfilled
running     probe       express · 1 core · 512 MB · 0m03s
```

The "with --time ≤ 25m" hint is `waiting_for.reservation.fits_if_time_s_le`, computed by `plan()`: it tells Claude
what a realistic `--time` would have bought next time; it does not invite cancel-and-resubmit.

### 7.2 `src/mu/jobs.py` and `src/mu/proto.py`

- `mu submit`: `--quick`; `--cores` help becomes "cores granted to the job (CPUs pinned, thread variables set);
  default: history, else the whole node"; `--mem` help "default: history, else node RAM − 2 GB"; spec gains
  `"sched": 1` and `limits.quick: true` for `--quick`.
- Before the snapshot: if `hello.features` has `sched`, compare against `hello.capacity` (`too-big`, exit 2). If it
  has not (old node), `--quick` becomes `--cores 1 --mem 512M --time 10m` and the answer's `note` says "office-pc runs
  one job at a time (μ older than v2026.11.N): queued behind N jobs".
- Answers: `sizing` (§4.2), `waiting_for`, `starts_by`; prose: `queued #2 · 8 cores, 6.0 GB, 12 h (history: 3 runs of
  'sweep'; time default) · waiting for 8 cores (4 free) · starts by 14:20`.
- `mu status [<id>]`, `--wait`: the §7.1 wording; JSON passes the node's fields through (node text stays data:
  `node_message`, never `next`).
- `mu compute status`: one more line, `20 cores, 21.0 GB for jobs · 12 cores, 15.0 GB allocated · 2 running, 1
  express, 3 queued · CPU limit: affinity`.
- `mu fetch` copies `usage`, `request` and `grant` into `runs/<id>.json`.
- `proto.py`: `PROTOCOL` stays 1; a `NODE_TOO_BIG` mapping by error code; `parse_size` and `parse_duration` unchanged.

### 7.3 Elsewhere

- `src/mu/fea.py`: Gmsh `General.NumThreads` and `run(ncpu=…)` from `MU_JOB_CORES` (§6).
- `install/hosts/wsl2/mu-wsl2-distro.sh`: **nothing in this build.** R-sched-1 and R-sched-2 (§5.4) are a later
  release, applied at a visit.
- `docs/design/job-layer.md` §8.3, §8.4, §10: a pointer to this document and the corrected "one running job" text.
- `docs/using-compute-jobs.md` and `claude/CLAUDE-toolchain.md`: §8.

## 8. Rules for Claude Code (`claude/CLAUDE-toolchain.md`)

Replaces the "Limits" bullet of "What runs on the node" (the pairing branch also edits this file; rebase on it):

- **The node runs several jobs at once**, each on the cores and memory it was granted. Size every job so others can
  run beside it:
  - quick checks and diagnostics (under a minute, one thread, small memory): `--quick` (1 core, 512 MB, 10 min). They
    start at once even when the node is full.
  - heavy runs: always `--cores N --mem SIZE --time DUR`.
    - `--mem`: the previous run's `usage.mem_work_peak_bytes` (in `runs/<id>.json`) × 1.25. First run of an unknown
      job: leave `--mem` off (whole node) rather than guess low; above the grant the job ends `oom`.
    - `--time`: expected × 2. A realistic `--time` lets the job start ahead of a waiting big one; the default 12 h
      never does. Above it the job ends `timeout`.
    - `--cores`: what the solver uses well. Until `docs/verification/` has the node's scaling curve, prefer two jobs of
      10 over one of 20 when you have two runs to make. A serial sweep: `--cores` = how many cases at once.
  - leaving a flag off: μ uses this job name's history on the node, else the whole node. Read `sizing.why` in the
    answer.
- **Inside the job, use the grant, never a hard-coded count**: `mpirun -np $MU_JOB_CORES` (μ sets Open MPI's CPU set;
  do not add `--bind-to core` or a `--cpu-set` of your own), `foamDictionary -entry numberOfSubdomains -set
  $MU_JOB_CORES system/decomposeParDict`, Gmsh `General.NumThreads` = `$MU_JOB_CORES`. `OMP_NUM_THREADS` and the BLAS
  variables are already set. A job that runs N processes (MPI ranks, a pool, `parallel -j $MU_JOB_CORES`) sets their
  inner threads to 1 (`mpirun -x OMP_NUM_THREADS=1`).
- `too-big` (exit 2): the request exceeds the node; `next` gives the largest values. Do not retry unchanged.
- A queued job's `waiting_for` and `starts_by` say why it waits and by when it starts at the latest. **Do not cancel
  and resubmit to jump the queue**: it goes to the back. A note `affinity widened by …` means the job used CPUs outside
  its grant: fix the launcher (usually an explicit `--bind-to`).
- An `oom` at a learned size is re-run once by the node with the whole memory (the status note says so): wait for it.

## 9. Durability and recovery with N running jobs

The job layer's rules (job-layer §9) hold per job; what changes is that several jobs are in each state at once.

| Event | What happens |
|---|---|
| **Runner restart** (crash, kill, new version) | Each running unit is adopted as today (R25); its grant comes back from its `state.json`; the allocation is rebuilt before anything starts. A running job without a grant holds the whole node until it ends. Never over-commits |
| Runner dies between writing a grant and `systemd-run` | `start_unconfirmed` is set: next pass asks systemd; no unit → `start_failed` (back to the queue, grant dropped); unit exists → adopted with that grant. The grant was on disk first, so the allocation counted it throughout |
| Two starts in one pass, runner dies after the first | The first is running (or `start_unconfirmed`) with its grant on disk; the second is still queued. Next pass re-plans with the first counted |
| **Windows restart / VM stop / `wsl --shutdown`** | Every unit is gone. Each running job goes through the interrupted rule: re-run as attempt 2 at its **old** `accepted_ns`, grant dropped. They return to the front in their original order and start as they fit, up to `max_running` |
| `systemctl show` fails for one job | That job keeps its grant (or holds the node if it has none). Narrower than today's "no job starts while another's state is unknown", and still never over-commits |
| An error in one job's files during a pass | journalled; that job holds its grant or the whole node; the pass goes on (as today, review: durability 4) |
| Cancel of a running job | Stops the unit. Its grant is freed **only when the unit has ended** and the result is written, never when the marker is written (test Q11's fault). `S` is recomputed next pass |
| `oom` of one job | Its cgroup alone is killed (`MemoryMax` per unit, `OOMPolicy=kill`); others are untouched because Σ grants ≤ `M` (Q09) |
| **Version switch** | Today the runner restarts into a new default only when idle; with several jobs the node is idle less often. Adoption is proven (R25, Q10), so the switch happens when **no job is starting** (no `start_unconfirmed`). The old shims keep running under the old code and write results the new runner reads. `state/busy` keeps its contract for `mu autoupdate` |
| `history.jsonl` damaged or missing | Rebuilt from `result.json` files at the runner's start; meanwhile sizing falls back to defaults (whole node): conservative |
| `usage.json` lost | It is a view; the next sample rewrites it; runner-written results then lack the working-set peak (`null`) |

## 10. What the dashboard shows

The dashboard (ROADMAP step 3) is built after this and reads these fields; nothing in it is needed for the scheduler.

- **Per node**: a stacked bar of granted cores and of granted memory per running job against capacity (`C`, `M`),
  with the express lane and the system reserve drawn apart; `limits.cpu` ("affinity" or "cpuset").
- **Per running job**: used against granted, cores (`cores_avg` from `cpu.stat` against `grant.cores`) and working
  memory (`anon + shmem` against `MemoryMax`); class (normal, backfilled, express); `ends by`; `affinity widened` as a
  warning line.
- **Per queued job**: position, request, `waiting_for` in words ("needs 8 cores, 4 free; next free by 14:20 when
  sweep-3 reaches its limit"), and for the head `starts by`.
- **Finished jobs**: `usage` and `sizing.source`, so a learned size and its result sit side by side.
- Dashboard phase 1 then drops "μ starts recording CPU time and peak memory per job" from its own scope (built here)
  and keeps the sampler's `metrics.jsonl`, `now.json` and the `stats`/`watch`/`metrics` verbs. Its "17.8 / 20 cores"
  column becomes "17.8 / 20 granted".

## 11. Compatibility with the previous release

Protocol **stays 1**: every change is an added field or verb behaviour behind a feature flag, never a changed meaning
of an existing field that an older peer relies on. `check_spec` already ignores unknown keys of the spec and of
`limits`, and the client ignores unknown answer fields.

| Workstation | Node | Behaviour |
|---|---|---|
| new | new | everything above |
| **old** (no `sched` in the spec) | new | The job is sized as today: `cores` granted = `C` (the job runs alone, except for express jobs), `mem` = `--mem` or `M`, time = `--time` or 12 h, `MU_JOB_CORES` = `--cores` or `C` as before. So a job that passes `--cores 4` but runs 16 ranks keeps working as it does today. The sizing note says "older workstation: whole node" |
| new | **old** (no `features: ["sched"]` in `hello`) | `--quick` sent as `--cores 1 --mem 512M --time 10m`; no capacity check before upload; the answer notes "one job at a time on this node"; no `sizing`, `waiting_for` or `starts_by` in the answers (shown as "—") |
| either | rolled back to the previous release (owner's "reversible") | The old runner adopts running units (it ignores `grant`), sees jobs running and starts nothing more until all have ended, then runs one at a time. Extra files (`usage.json`, `history.jsonl`, `tmp/`) are ignored. Nothing to undo |

The release reaches both machines by auto-update (one daily check each); for up to a day they may differ, and the
table covers each order. **The meaning of `--cores` changes** from advice to a grant for new workstations only, and
`CLAUDE-toolchain.md` changes in the same release, so Claude reads the new rule with the new behaviour.

## 12. Checked on the PC before the build

**Probe pending.** Job `20261114-161025-2icaxr` ("sched-probe", `~/mu-first-job`) is queued behind the owner's JATAYU
jobs. Each item below has a fallback, so the build does not wait for the answers; they choose between paths already
designed. The lead fills the "Result" column when the job has run.

| # | Fact | Probe item (in the job, as `mu`) | Pass criterion | If it fails | Result |
|---|---|---|---|---|---|
| 1 | cpuset is available in the distro's cgroup at all | `cat /sys/fs/cgroup/cgroup.controllers` and `/sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.controllers` | the first lists `cpuset` (needed for R-sched-1); the second shows today's `cpu memory pids` | R-sched-1 is dropped; affinity stays the only CPU placement (no change to this build) | pending |
| 2 | `CPUAffinity=` works in a user unit | `systemd-run --user -p CPUAffinity=0-3 --wait --pipe grep Cpus_allowed_list /proc/self/status` | exit 0 and `0-3` | the unit property is left out (`selftest.cpu_affinity: false`); the shim's own `sched_setaffinity` remains (§5.2) | pending |
| 2b | the shim's way works | `taskset -c 0-3 python3 -c 'import os; print(sorted(os.sched_getaffinity(0)))'`; a child of it shows the same | `[0, 1, 2, 3]` in parent and child | (never expected on Linux) `limits.cpu: "none"`; grants are counted, not pinned | pending |
| 3 | Open MPI's CPU-set parameter, and whether `mpirun` widens an inherited mask | `ompi_info --param hwloc base --level 9 \| grep -i cpu`; `taskset -c 0-3 mpirun -np 4 grep Cpus_allowed_list /proc/self/status` with and without `OMPI_MCA_hwloc_base_cpu_list=0-3` and with `OMPI_MCA_hwloc_base_binding_policy=none` | the parameter name exists; with it (or with binding `none`), every rank's list is inside `0-3` | use `binding_policy=none` (ranks inherit the job's mask); if even that widens, the sampler flags it and CLAUDE-toolchain says `--bind-to none` explicitly | pending |
| 4 | `memory.peak` exists in a job unit's cgroup | `cat /sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup)/memory.peak` inside a job | a number | `mem_peak_bytes` from the sampler's running max of `memory.current` (dashboard §4.2 already plans it) | pending |
| 5 | what the guest sees of P/E cores | `lscpu -e`; `grep -m1 flags /proc/cpuinfo` (`hybrid_cpu`); `ls /sys/devices/system/cpu/cpu0/cpu_capacity`; `ls /sys/bus/event_source/devices/` (`cpu_core`, `cpu_atom`) | recorded, no criterion | the design assumes nothing is visible (§5.5); if types are visible **and** stable (item 6), a later note may revisit | pending |
| 6 | whether Windows keeps any vCPU → core mapping | a single-thread loop pinned to each vCPU in turn (`taskset -c $i`), 3 rounds, timings per vCPU | stable two clusters across rounds = quasi-stable mapping; spread = floating | treat as floating (the design's assumption) | pending |

After the release, once, also by a job: the **scaling series** (np 1, 2, 4, 8, 12, 16, 20) of the benchmark suite's
FEA and CFD cases, recorded in `docs/verification/` (§14 check 7). It informs the guidance on `--cores`, not the code.

## 13. Tests (CI, ubuntu runner, real systemd)

`tools/test-jobs/e2e.sh` rows on `ubuntu-24.04` with the real user manager (delegated `cpu memory pids`, as P3
measured), each run, then run again with its fault, which must make it fail ("FAILS-OK"), as the job layer's R rows.
The runner has 4 vCPUs and 16 GB, so the tests set `node.json` `sched: {cores: 4, mem_bytes: 2G}` (`C` = 4, `M` = 2 GiB) to make a small node
with real limits, and use `MU_NODE_TICK` as today. `plan()` also gets unit tests in `tools/test-jobs/unit.py`
(table-driven: fit, shadow time, extra, valve, express, grant-less running job, `max_running`), each with its fault.

| # | Check | Fault that must make it fail |
|---|---|---|
| Q01 | **concurrent**: two `--cores 2 --mem 512M` jobs run at the same time (overlapping `started_at`…`ended_at`) | `one-at-a-time` (today's rule) |
| Q02 | **memory admission**: three `--cores 1 --mem 1G` jobs with `M` = 2 GiB: two run, the third waits with `waiting_for.resources.mem_bytes` | `no-mem-admission` |
| Q03 | **core packing**: three `--cores 2` jobs on 4 cores: two run, the third waits with `waiting_for.resources.cores` and starts when one ends | `no-core-admission` |
| Q04 | **backfill**: A (`--cores 2 --time 60s`) runs; head H (`--cores 4`) waits; B (`--cores 2 --time 10s`) starts at once (`class: backfilled`); C (`--cores 2 --time 1h`) waits with `waiting_for.reservation` | `backfill-ignores-shadow` (C starts) |
| Q05 | **starvation guard**: H waits behind A; a short job is submitted every 2 s for 90 s; H starts by its `starts_by` (+ 2 ticks) | `no-head-reservation` (backfill anything that fits now) |
| Q06 | **express lane**: while a whole-node CPU-bound job runs, a `--quick` job finishes within 15 s; a third express job waits for the lane | `no-express` |
| Q07 | **isolation**: in two concurrent jobs every process's `Cpus_allowed_list` equals its grant; the two sets are disjoint; a child that calls `sched_setaffinity` wider is flagged `affinity_widened` | `no-affinity` (the shim skips it and the unit property is off) |
| Q08 | **thread counts**: inside a `--cores 3` job, `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `PYTHON_CPU_COUNT`, `CCX_NPROC_EQUATION_SOLVER`, `MU_JOB_CPUS`, `TMPDIR` equal the grant and the job's own folder | `no-thread-env` |
| Q09 | **oom of one job does not touch others**: A (`--mem 512M`) allocates 1.5 G and ends `oom`; B (`--mem 1G`, holding 900 MB) beside it ends `succeeded` with `usage.mem_events.oom_kill` 0 | `mem-max-is-capacity` (every unit gets `MemoryMax` = `M`: A does not end `oom`) |
| Q10 | **runner restarts mid-run**: with three jobs running, the runner is killed (`SIGKILL`) and restarts; all three adopted, finish once, grants unchanged; a queued job that fits only if the grants were forgotten stays queued until one ends | `grant-in-memory-only` (grants not written to `state.json`) |
| Q11 | **cancel**: one of three running jobs cancelled ends `cancelled`, its process tree is gone, the other two continue; a queued job then gets its cores, and at no sample do two live jobs share a CPU | `free-on-cancel-request` (grant freed when the marker is written) |
| Q12 | **too big**: `--cores 64` and `--mem 1T` exit 2 `too-big` with the capacity, and nothing reached the node (no push, no submit in `audit.log`); the node's own check refuses a spec sent past the client | `no-capacity-check` |
| Q13 | **VM stop with N running**: three running jobs' units are killed with the runner stopped (the interrupted path): all three are re-queued as attempt 2 in their original order and start again as they fit | `requeue-new-ns` (re-queued at the back) |
| Q14 | **old workstation**: a spec without `sched` and with `limits.cores = 1` runs with `cores` granted = `C` (alone) and `MU_JOB_CORES` 1 | `legacy-sized` |
| Q15 | **old node**: the node hides `features` (fault `hide-sched` on the node, as an old node would); `mu submit --quick` is accepted, sent as `cores 1, mem 512M, time 10m`, and the answer says "one job at a time" | `assume-sched` (the client reports a `sizing` it cannot have) |
| Q16 | **history sizing**: `sweep` run once with `--mem 1G` using ~200 MB; the next unflagged `sweep` gets `mem` ≈ 200 MB × 1.25 + 256 MB rounded, `source.mem: history`, and its `MemoryMax` equals it | `no-history` |
| Q17 | **oom at a learned size** re-runs once with `M`; an explicit `--mem` oom is not re-run | `no-oom-retry` |
| Q18 | **version switch while jobs run**: a new default appears with two jobs running; the runner restarts into it within 3 s, both jobs are adopted and finish once | `switch-only-when-idle` |
| Q19 | **status wording**: a waiting job's `mu status` says "waiting for 2 cores / 1.0 GB"; the head has `starts_by`; JSON `waiting_for` matches | `no-waiting-for` |
| Q20 | **usage recorded**: shim-written and runner-written (`oom`, `timeout`) results carry `usage` with `cpu_s`, `mem_peak_bytes`, `mem_work_peak_bytes`, `source` | `no-usage` |

Existing rows R14, R17, R18, R19, R25 run unchanged; R19 ("three submits run one at a time") becomes "three
whole-node jobs run one at a time" (unflagged jobs are whole-node), so it still holds and still fails with
`start-on-submit`.

## 14. PC checks after the release (by jobs, no visit)

Each from the Mac through Claude or the owner, after both machines show the release in `mu compute status`:

| # | Check | Pass |
|---|---|---|
| 1 | `mu compute status office-pc --json` | `features` has `sched`; `capacity.cores` 20; `capacity.mem_bytes` ≈ MemTotal − 2 GiB; `limits.cpu` `affinity`; `limits.cpu_affinity_unit` as §12 item 2 found |
| 2 | Two `--quick` jobs while a whole-node job runs | both finish within seconds; the big job's log shows no restart |
| 3 | Two `--cores 10 --mem 8G --time 30m` jobs printing `Cpus_allowed_list` and the §6 variables | both running at once; disjoint lists of 10; variables = 10 |
| 4 | One `--mem 512M` job allocating 1 GB beside a `--mem 4G` job | the first `oom`, the second `succeeded` |
| 5 | Backfill: a whole-node job queued behind a `--cores 10 --time 20m` job; then `--cores 10 --time 5m` and `--cores 10 --time 2h` | the 5 m job starts at once (`backfilled`); the 2 h job waits with `reservation`; the head starts by its `starts_by` |
| 6 | An `mpirun -np $MU_JOB_CORES` job with `--cores 8` | no `affinity_widened`; ranks' lists inside the grant |
| 7 | Scaling series np 1…20, FEA and CFD benchmarks | curve recorded in `docs/verification/`; CLAUDE-toolchain's `--cores` advice updated from it |
| 8 | The JATAYU agent's next real jobs | succeed as before; `sizing.source` shows what they used; their queue wait drops |

## 15. Effort, and place in the roadmap

| Piece | Lines (approx.) | Days |
|---|---|---|
| `plan()`, capacity, grants, express, `step()`/`start_job()` changes, recovery | 350 in `node.py` | 2–2.5 |
| sizing, `history.jsonl`, `too-big`, oom retry | 120 in `node.py` | 1 |
| `usage` and the 5 s sampler (shared with the dashboard), affinity check | 150 in `node.py` | 1 |
| `jobs.py` and `proto.py`: `--quick`, capacity check, `sizing`/`waiting_for` wording, compatibility paths | 150 | 1 |
| `fea.py`, CLAUDE-toolchain, using-compute-jobs, job-layer pointers | small | ½ |
| Tests Q01–Q20 and `plan()` unit tests, each with its fault | ~500 in `e2e.sh`, `unit.py` | 1.5–2 |
| **Total** | ~770 + tests | **6–8 days**, one release, no PC visit |

The research estimated 5–7 days; the oom-retry rule and the old-node/old-workstation paths add about a day.

**Order** (to add to ROADMAP as a step between step 2 and step 3, by the lead): after **step 1** (the pairing fixes,
which also touch `node.py` lines 632–645 and 1559, `jobs.py` and `CLAUDE-toolchain.md`: rebase on them) and after
**step 2** (the node's auto-update reports `ok` again, or the release cannot reach the PC). The probe of §12 can run as
soon as the queue lets it; it does not gate the build. Dashboard sub-step 3.1 then reuses `usage` and the sampler.

## 16. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| A learned `--mem` is too low (bigger case, same name) | one run ends `oom` | re-run once with `M` automatically (§4.2); explicit `--mem` wins |
| Open MPI widens the mask | ranks share CPUs with a neighbour; both slow | μ sets the envelope (§5.2 item 4); the sampler flags it; R-sched-1 makes it impossible |
| Jobs with hard-coded thread counts | oversubscribe their own grant: slower, others unaffected | affinity confines them; `fea.py` fixed; guidance |
| Memory bandwidth and E-core placement | two jobs each slower than alone | measured by the scaling series; guidance from it |
| Claude leaves flags off | jobs stay whole-node: no gain until history or flags | the `next` hint; CLAUDE-toolchain rule; history after the first run |
| Express jobs slow one rank of a heavy MPI job | up to 10 min | small (1 thread); the cap of 2; Q06 measures it |
| Disk: several `work/` copies at once | `disk-low` sooner | per-start check (§3.6); retention rule (ROADMAP §2.3 Q9) |
| I/O contention without the `io` controller | concurrent writers slow each other | `max_running` 6; `IOWeight` after R-sched-1 |
| The office PC feels slower to people at it | more cores busy more of the time | the VM already may use all 20 vCPUs today; `node.json` `sched.cores` can lower `C` (Q1 below) |
| A version switch with jobs running | a bug in adoption would hit several jobs | adoption is R25 and Q10; Q18 covers the switch itself |
| Conflicts with the pairing branch | merge work | small overlap (8 lines of `node.py`); rebase before building |
| Auto-update still failing | the release does not reach the PC | ROADMAP step 2 is a prerequisite |
| A PC fact differs from the expectation | a path changes | every §12 item has a designed fallback |

## 17. Open questions for the owner

Defaults apply until the owner says otherwise.

1. **Keep some of the PC for the office?** Jobs may use all 20 vCPUs today. Lower `C` (e.g. 16) so people at the PC
   always have headroom? Default: no change (20), settable in `node.json` without a release.
2. **`max_running` 6?** Default yes; raise once `io` is delegated.
3. **Express jobs borrow CPU time** from running jobs (one rank slowed up to 10 min) rather than wait? Default yes.
4. **Learned memory below the whole node** for unflagged jobs, with one automatic re-run on `oom`? Default yes. The
   alternative (history only as a hint) keeps unflagged jobs whole-node forever.
5. **The root step R-sched-1** (cpuset and io delegation, one distro restart) at the next visit or with ROADMAP 4.2?
   Default: with 4.2, only if §12 item 1 shows cpuset available.
6. **A `--priority` flag**, or fair share between workstations: not now (one submitter); revisit with a second
   workstation.

## 18. Read in sources

- Research note of 2026-11-14 (scheduler), with its sources: systemd.resource-control(5), systemd.exec(5)
  (`CPUAffinity=`), `user@.service` of systemd v255 (`Delegate=pids memory cpu`), the kernel's cgroup v2 document
  (cpuset, cpu.max, memory.high/max/peak), sched_setaffinity(2), Slurm `sched_config`, `slurm.conf`, `cgroup_v2`,
  the noble `slurm-wlm` package, HTCondor partitionable slots, Nomad's licence, pueue's README, Microsoft's Hyper-V
  scheduler types, Launchpad 2003985, Open MPI 4.1 `mpirun(1)`, hwloc topology flags, the CalculiX manual (threads),
  Gmsh `NumThreads`, Python 3.13 "What's new"; Lifka 1995 (EASY), Mu'alem & Feitelson 2001, Tsafrir, Etsion &
  Feitelson 2007 (from memory, not re-fetched).
- μ at a3a7016: `node.py` (`hw_info`, `queued_jobs`, `status_of`, `write_result`, `hello_answer`, `check_spec`,
  `verb_submit`, `mem_default`, `start_job`, `start_failed`, `interrupted`, `rebuild_state`, `check_running`, `step`,
  `run_runner`, `run_job`, `selftest`), `jobs.py` (submit flags and spec, `hello`), `proto.py` (protocol 1), `fea.py`
  lines 41 and 218, `durable.py` (faults), `mu-wsl2-distro.sh` `ensure_cgroup_delegation`,
  `toolchain/linux-64/apt.txt` (`openmpi-bin`, `libopenmpi3t64`), job-layer §7–§10, §13, §21.3, dashboard §4.2, §5.1,
  §6.2, ROADMAP §0–§2, PLAN §17.
- The pairing branch's diff against `main` (for the overlap in §15).
