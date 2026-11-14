# μ node scheduler: several jobs at once, by the cores and memory each needs

**Status: design, revised after an independent review; nothing built.** Written on branch `scheduler` from `main` at
a3a7016 (v2026.11.8, 2026-11-14), from the research note of the same day (build or adopt, algorithm, enforcement, WSL2
hybrid cores) and the code it cites. Every claim about μ's code was read in `src/mu/node.py`, `src/mu/jobs.py`,
`src/mu/proto.py`, `src/mu/fea.py`, `install/hosts/wsl2/mu-wsl2-distro.sh` and `tools/test-jobs/`. The review's
findings and what was done with each are in §19.

**Nothing here has run on the office PC.** The probe job that settles the PC facts (job `20261114-161025-2icaxr`,
"sched-probe", from `~/mu-first-job`) was still `queued` (position 1) on 2026-11-14 evening, behind the owner's JATAYU
jobs: the very problem this design solves. Every statement below about P/E cores, cpuset, Open MPI, `memory.peak` and
the system's memory footprint is **expected, not measured** until §12 has results. §12 lists each fact, its probe item,
the pass criterion and the fallback. The probe runs before the build (ROADMAP step 2.5.0); the paths it can change are
marked "per §12".

Sections: [1 Goal](#1-goal) · [2 Build or adopt](#2-build-or-adopt-plan-17) · [3 Algorithm](#3-the-algorithm) ·
[4 Requests and defaults](#4-requests-and-defaults) · [5 Enforcement](#5-enforcement) · [6 Thread counts](#6-thread-counts-from-the-grant) ·
[7 Changes to the code](#7-changes-to-the-code) · [8 Rules for Claude](#8-rules-for-claude-code-claudeclaude-toolchainmd) ·
[9 Durability](#9-durability-and-recovery-with-n-running-jobs) · [10 Dashboard](#10-what-the-dashboard-shows) ·
[11 Compatibility](#11-compatibility-with-the-previous-release) · [12 PC facts](#12-checked-on-the-pc-before-the-build) ·
[13 Tests](#13-tests-ci-ubuntu-runner-real-systemd) · [14 PC checks after release](#14-pc-checks-after-the-release-by-jobs-no-visit) ·
[15 Effort and order](#15-effort-and-place-in-the-roadmap) · [16 Risks](#16-risks) · [17 Questions](#17-open-questions-for-the-owner) ·
[18 Read](#18-read-in-sources) · [19 Review](#19-review-2026-11-14)

## 1. Goal

**Today** (job-layer §10, `step()` in `node.py`): the node runs one job at a time, first in first out. A job without
`--mem` gets `MemoryMax` = MemTotal − 2 GiB (~21 GB of the VM's 23 GB) and `MU_JOB_CORES` = all 20 cores, an
environment variable only. A 1-second diagnostic waits behind a long JATAYU sweep while most cores may idle (seen on
2026-11-14: 1 running, 3 queued).

**The owner's decision (2026-11-14):** run several jobs at once, scheduled by the cores and memory each needs; small
jobs start at once, big ones are protected from starving; built into μ's own runner after a build-or-adopt check;
designed now, built after the pairing fixes merge (they touch `node.py` too), before the dashboard.

**Goals**

1. Jobs whose requests fit together run together. The sum of memory grants never exceeds what the node sets aside for
   jobs, so **by accident** one job's `oom` is not caused by another. This holds against mistakes, not against a job
   that deliberately leaves its cgroup (§5.1).
2. A `--quick` job starts within seconds, whatever is running.
3. A big job waiting at the head of the queue is never overtaken in a way that delays it. When every running job has
   a known end bound, μ states the latest start (`starts_by`); when it cannot promise one, it says so (`null`).
4. A job uses the CPUs it was granted, and its solvers are told how many (thread variables set by μ).
5. Claude Code, which submits almost every job, can size a job in one call, is shown the last run's measured use as a
   suggestion, and reads in `mu status` why a job waits ("waiting for 12 cores / 16 GB").
6. **No PC visit.** Everything arrives by auto-update; nothing needs root. A later root step (cpuset delegation) only
   hardens CPU placement and is listed for a visit (§5.4).
7. Mixed versions keep working: an old workstation with a new node, and a new workstation with an old node (§11).

**What this does not change by itself.** A job submitted **without flags** is still sized as today: the whole node
(all cores, all job memory) for up to 12 h. One such job runs alone (only `--quick` jobs run beside it), and while it
sits at the head of the queue nothing else ordinary starts. Concurrency comes from Claude passing `--cores`, `--mem`
and `--time` (§8); time-based backfill pays off only when `--time` is realistic. This is deliberate: guessing sizes
for unflagged jobs would risk silent `oom` of long runs (§19, finding 1).

**Non-goals:** several nodes (Slurm's job if it ever comes, §2); priorities and fair share between workstations (one
submitter today, §3.7); P/E-core-aware placement (the guest is not expected to see or keep core types, §5.5, per §12);
GPU scheduling; learning job sizes automatically.

## 2. Build or adopt (PLAN §17)

PLAN §17 already kept the job queue ("the μ-specific parts would remain anyway"). Re-checked for a resource-aware
scheduler:

| Candidate | What it needs on the node | Fit | Verdict |
|---|---|---|---|
| **Slurm** single node (slurmctld + slurmd, `select/cons_tres`, `sched/backfill`, `task/cgroup`) | The usual install is root: `slurm-wlm` (noble: 23.11.4), munge or `auth/slurm` key, two daemons, slurmd as root for cgroup enforcement (and the **cpuset** controller, unverified). It **can** run unprivileged for one user: conda-forge packages it (`slurm` 22.05.9, checked 2026-11-14), with daemons as `mu` and no cgroup plugin | Best algorithm (consumable cores and memory, EASY backfill) | **No.** Root is not the deciding reason. Deciding: two more daemons to keep alive through Windows restarts; a second state store beside μ's job folders that must agree with them; translating μ's job model (snapshot, pin, gate, run records, safe fetch, interrupted rule) to `sbatch` and back; and unprivileged it loses cgroup enforcement, which μ already has. **The answer if μ ever schedules across several nodes** |
| **HyperQueue** | User-space binary, no root; server + worker as `mu` | Resource-aware (CPU counts and generic resources per task), no root | **No**, for the pueue reason: its tasks are children of its worker, not systemd units, so μ would lose the per-job cgroup, `MemoryMax`, cancel and survival of runner restarts, or would have to rebuild them around it |
| **HTCondor** (partitionable slot) | Five daemons; cgroup enforcement wants a root install | Right model (dynamic slots by `request_cpus/request_memory`) | **No**: five daemons and ClassAds for one machine |
| **Nomad** (`exec`/`raw_exec`) | Root for cgroup isolation; BSL 1.1 since 1.7 | A service orchestrator, no backfill | **No** |
| **pueue** | Runs as the user | A slot count per group, no CPU or memory accounting; children are not systemd units | **No** (PLAN §17 said so already) |
| **GNU parallel / `xargs -P`** | Nothing | No persistent queue, no recovery | **Not a node scheduler; useful inside a job** (`parallel -j $MU_JOB_CORES`) |
| **systemd** `MemoryMax=`, `CPUQuota=`, `AllowedCPUs=` | Already there (cpu, memory, pids delegated to `mu`'s user manager) | Enforcement, not admission or ordering | **Adopt as the enforcement** (§5) |

**Build** it into `mu node run`: an allocator, FIFO with a head reservation and EASY backfill, and an express lane, on
the existing one-unit-per-job design. The machinery for concurrency is already there (one transient unit and cgroup per
job, per-job sealed state, a runner pass that already visits every running job, per-job recovery); what is missing is
**admission** (does it fit?), **placement** (which CPUs) and **ordering** (who first?).

**Size, honestly.** The research note put the missing piece at "~300–400 lines". The design as revised is about
**770 lines of code plus about 600 lines of tests**, 7–9 days (§15). The verdict does not change: adopting any
candidate above still leaves μ's job layer in place and adds a second one to keep in step with it.

## 3. The algorithm

### 3.1 Capacity

| Symbol | Value | Where it comes from |
|---|---|---|
| `C` | CPUs the runner may use: `len(os.sched_getaffinity(0))` (20 on the PC) | the kernel; `node.json` `sched.cores` may lower it (§17 Q1) |
| `reserve_system` | **2 GiB**: runner, sshd, tunnel, journald, `git receive-pack` of packs up to 2 GB, several checkouts at once | today's figure, kept until measured (§12 items 7–8) |
| `reserve_express` | 1 GiB, only for express jobs (§3.4) | default |
| `M` | MemTotal − `reserve_system` − `reserve_express`: about **20 GB** on the PC | `/proc/meminfo`; both reserves in `node.json` `sched` |

- **`M` is 1 GiB below today's default.** An unflagged job gets about 20 GB instead of about 21 GB. A job that peaks
  between the two today would end `oom` after the release; `usage` (§7.1) shows each job's peak, and §17 Q2 asks the
  owner to accept this. The system reserve is lowered only after §12 item 7 and §14 check 8 measure the idle and
  push-time footprint.
- Memory is a **hard** resource (over-commit ends in OOM): admission is strict on it. If the system reserve proves too
  small, the kernel's global OOM killer picks the **largest** process, which is a job, not the process that ran out;
  that is why the reserve stays generous until measured.
- CPU is softer (over-commit only slows), but ordinary jobs are admitted strictly on cores too, so that a grant means
  "these CPUs are yours". Only the express lane borrows CPU time, capped at one CPU (§3.4).
- There is **no cap on the number of running jobs**: cores already bound it at `C`. A cap is added only if the PC shows
  I/O thrash (§14 check 3; `io` is not delegated, §5.4).

### 3.2 Request and grant

- **What was asked** is the spec as sent: `limits.cores`, `limits.mem_bytes`, `limits.time_s`, `limits.quick` (already
  in `job.json`, written once at acceptance), and `sched: 1` from a new workstation (§11). Nothing else is stored.
- **Request** `(cores c, mem m, time t, class)` = `resolve(spec, capacity)`, a pure function of the flags and the
  capacity (§4.2), computed by `plan()` on every pass and by `submit` for its answer. Because it is not frozen at
  submit, a job queued before a rollback-and-forward, or before `node.json` changed, is still sized by the current rule.
- **Grant**: written into `state.json` when the job starts (§3.3 "starting"), **before** `systemd-run`, as `limits`
  already is: `{"cores": c, "cpus": [0,1,2,3,4,5,6,7], "mem_bytes": m, "time_s": t, "class": "normal" | "backfilled" |
  "express", "boot_id": …, "start_mono": …, "active_mono": …}`. `start_mono` is `CLOCK_MONOTONIC` just before
  `systemd-run`; `active_mono` is the unit's own `ActiveEnterTimestampMonotonic` (from `systemctl show`, which the
  runner already calls each pass), filled on the first pass that sees the unit active.
- **End bound** of a running job, in `CLOCK_MONOTONIC` seconds (never wall clock: WSL's clock steps, which is why
  `accepted_ns` is already guarded):
  - `active_mono + t + SLACK` once known (`RuntimeMaxSec` counts from the unit's activation; `SLACK` = 10 s covers
    `TimeoutStopSec=5` and a tick);
  - before that, `start_mono + START_WAIT + t + SLACK`, with `START_WAIT` = 60 s, the `systemd-run` timeout.
  - A grant whose `boot_id` is not the current one belongs to a unit that no longer exists; the interrupted rule
    handles that job (§9).
- **Allocation** = the grants of all running jobs, **rebuilt from their `state.json` on every pass**. There is no
  allocation file and nothing kept only in memory, so nothing can disagree with the jobs (§9).
- A running job **without a grant** (written by the previous release, rebuilt by `rebuild_state()`, or whose
  `state.json` raised an error this pass) holds the whole node: the current rule "no job is started while another
  one's state is unknown", kept. While one exists, `starts_by` is `null`.

### 3.3 Order: FIFO with a head reservation and EASY backfill

Each pass (once a second, under `/srv/mu/lock`), one pure function decides; the runner starts what it returns, and
`status` calls the same function to say why a job waits:

```
plan(now_mono, capacity, running grants, queued specs) -> (starts, waiting_for per job, S of the head or None)

0. Each queued job: request = resolve(spec, capacity); a request that no longer fits the capacity at all
   (capacity lowered since submit) ends the job failed, error too-big.
1. free = (C − Σ cores, M − Σ mem, free CPU ids) over ordinary grants; any grant-less running job: free = 0.
2. Express (§3.4): each queued --quick job, in order, takes a lane slot if one is free; otherwise it stays in Q
   below as an ordinary job of (1 core, 512 MiB, 10 min).
3. Q = ordinary queued jobs by accepted_ns. While Q's head fits free: start it.
4. Head H does not fit. S = the earliest end bound at which, releasing running ordinary jobs in end-bound order,
   free ≥ H's request. extra = what is free at S beyond H's request (cores, memory and CPU ids).
   S = None if: a grant-less job runs, disk is below min_free_gb (§3.6), or the valve (6) is open.
5. For each later job b in order, if b fits free now:
     b fits within extra                         → start (class normal), extra −= b: uses only what H will not need;
     S is not None and now + b.time + MARGIN ≤ S → start (class backfilled): it ends before H needs its resources;
     otherwise                                   → waits, waiting_for = {"reservation": {"head": …, "starts_by": S}}.
   MARGIN = START_WAIT + SLACK (70 s).
6. Valve: if now > S + 60 s (a unit outlived its RuntimeMaxSec), no time-based backfill until H starts.
```

- **Starting is checked twice.** A pass may start several jobs one after the other, and each start first checks out
  the snapshot (`prepare_work`, seconds to minutes). So `start_job` re-checks a **backfilled** job **after** the
  checkout and immediately before writing its grant: if `now + t + MARGIN ≤ S` no longer holds, nothing is written and
  the job stays queued (its `work/` is rebuilt at its next start, as today). The grant, its `start_mono` and the
  `state.json` write all come after the checkout, so the bound counts from the real start.
- **Why the head is not delayed.** Every job started past H either uses only `extra` (resources H does not need at
  `S`) or has an end bound ≤ `S`, enforced by systemd's `RuntimeMaxSec`, counted on the monotonic clock, with the
  checkout and the `systemd-run` wait inside the margin. So H starts at or before `S` unless a unit refuses to stop
  (the valve) or the disk is low (`S` is then `null`, never a false promise). Jobs that end early only bring `S`
  forward. Each later job becomes the head in turn, so no job starves.
- **Without `--time`** (the 12 h default) a later job can only use `extra`; time-based backfill needs a realistic
  `--time` (§3.5, §8).
- **CPU ids:** the 20 vCPUs are treated as identical slots (§5.5, per §12), so admission counts cores; the runner gives
  the lowest free ids. No contiguity is needed, now or with cpuset later.
- **Not chosen:** conservative backfill (a reservation for every queued job: more bookkeeping, nothing gained with a
  queue of a handful); priority classes (one submitter; FIFO is what Claude expects; a later `--priority` would only
  reorder Q); a "waited longer than T" guard (the head reservation already guarantees it).
- **Can be shipped in two parts** (§15): part A is steps 0–4 and the `extra` branch of step 5 (no times needed, no
  `starts_by`); part B adds end bounds, the time branch, the valve, `starts_by` and the `--time` hint.

### 3.4 Express lane

A job is **express only when submitted with `--quick`**: 1 core, 512 MiB, 10 minutes. Explicit small flags do not make
a job express (fifty 1-core sweep cases must use the ordinary pool, 20 at a time, not the lane, 2 at a time).
`--quick` together with `--cores`, `--mem` or `--time` is a usage error (`quick-with-flags`, exit 2). Express jobs:

- start at once from `reserve_express` (at most 2 at a time; their memory within the 1 GiB), ahead of the ordinary
  queue. **When the lane is full**, a `--quick` job is not held for the lane: it joins the ordinary queue as a
  1-core, 512 MiB, 10-minute job and starts as soon as that fits (often at once, by `extra` or by time);
- may run on **all** of `C` (they borrow CPU time, not cores) but are capped with **`CPUQuota=100%`** on the unit (the
  cpu controller is delegated). A `--quick -- make -j20` then takes at most one CPU's worth from running jobs; a
  single-threaded diagnostic is not slowed by it;
- end `timeout` at 10 minutes like any job (their `RuntimeMaxSec`);
- take nothing from the pool `S` is computed on, so they cannot delay the head (test Q15).

How much one express thread slows a heavy job beside it is measured by §12 item 9 (the CI runner's 4 cores cannot
show it).

### 3.5 What `--time` means now

- Backfill uses running jobs' end bounds and candidates' `time + MARGIN`. The default 12 h is **conservative**: an
  unflagged job can start past a waiting head only into `extra`, never by time. Nothing breaks; it backfills less.
- A realistic `--time` (expected × 1.5–2) is what lets a medium job slip in ahead of a waiting big one. §8 tells Claude.
- The kill limit and the reservation stay on `--time`. μ does not learn run times.

### 3.6 Disk

The existing rule stays per start: below `min_free_gb` no job starts; each waiting job's `waiting_for` says
`{"disk": {"free_gb": …, "min_free_gb": …}}`, and `starts_by` is `null`. Several jobs means several `work/` copies at
once, so the check matters more; the retention rule of ROADMAP §2.3 Q9 should land with or before this build.

### 3.7 Fairness, later

Each job carries `key` (the workstation's name on the node). With a second workstation: order Q by each key's decayed
usage (core-seconds, 24 h half-life; Slurm's fair-share idea), FIFO within a key; the backfill rule is unchanged. Not
built while ROADMAP §2.1 says no new machines.

## 4. Requests and defaults

### 4.1 What Claude passes

```
mu submit --quick -- bash -c 'cat /proc/pressure/cpu'              # express: 1 core, 512 MiB, 10 min
mu submit --name frame-fea --cores 8 --mem 12G --time 2h -- python fea_frame.py
mu submit --name sweep -- python sweep.py                          # no flags: the whole node, 12 h, runs alone
```

All flags stay **optional**, as today.

### 4.2 Sizing, per dimension (`resolve(spec, capacity)`, pure)

| Order | cores | mem | time (kill limit) |
|---|---|---|---|
| 1 explicit flag | `--cores` | `--mem` | `--time` |
| 2 `--quick` | 1 | 512 MiB | 10 min |
| 3 nothing given | `C` (whole node) | `M` | 12 h |
| old workstation (no `sched` in the spec, §11) | `C` | `--mem` or `M` | `--time` or 12 h |

- Row 3 is today's behaviour (with `M` 1 GiB lower, §3.1).
- The submit answer reports the result and its source per dimension:

```json
"sizing": {"cores": 20, "mem_bytes": 21474836480, "time_s": 43200, "class": "normal",
           "source": {"cores": "default", "mem": "default", "time": "default"},
           "why": "no flags: whole node for up to 12 h; it runs alone"}
```

- **A suggestion, not a size.** When a flag is left off, `mu submit` on the workstation looks in the project's
  `runs/*.json` for the newest fetched run with the same `name` that has `usage`, and adds to `next`: "last 'sweep'
  (20261114-…): working memory peak 4.6 GB, 7.9 cores on average, 41 min: next time pass --cores 8 --mem 6G --time
  1h30m". It is computed on the workstation from files it already has; the node keeps no history index and never
  changes a size by itself.

### 4.3 Requests larger than the node: `too-big`

- `cores > C` or `mem > M`: **refused before the snapshot is uploaded**. `hello` gains `capacity`, the workstation
  compares, and exits 2 with `error: "too-big"`, the node's capacity, and the largest values it accepts in `next` ("the
  node takes at most --cores 20 --mem 20G").
- The node checks again at `submit` (an old workstation has no capacity check): `too-big`, node exit 64, which the
  client maps to exit 2 by this error code. `plan()` checks once more at start (§3.3 step 0).
- Fits the node but not what is free now: queued, with `waiting_for` (§7.1).

## 5. Enforcement

### 5.1 Memory: `MemoryMax` = the grant

`MemoryMax` (proven on the PC by `mu node selftest`) and `MemorySwapMax=0` as today, set to the grant instead of
`M`. Admission keeps Σ grants ≤ `M`. **Not `MemoryHigh`**: it throttles instead of ending the job, and a throttled
solver looks like progress and is not (the reasoning behind `MemorySwapMax=0`). `memory.max` reclaims a job's own page
cache before it kills.

**Both limits are cooperative.** Jobs run as `mu`, the same user as the user manager that owns their units. A job can
call `systemd-run --user` (or `systemctl --user start`) and put work in a new unit outside its own cgroup: that work has
neither the job's `MemoryMax` nor its CPU affinity, and is not counted in any grant (the §12 probe does exactly this on
purpose). The limits hold against accidents (a mesh larger than expected, a hard-coded thread count), not against a job
that spawns units. §8 forbids it; the jobs are the company's own runs.

### 5.2 CPU, now (no root step): affinity, set once by the shim

| | (a) `AllowedCPUs=` (cpuset) | (b) `CPUQuota=` | (c) affinity |
|---|---|---|---|
| Needs | cpuset delegated (root drop-in) and present in the distro's cgroup (per §12) | cpu delegated (it is) | nothing: same-UID `sched_setaffinity(2)` |
| Limits | placement, hard | CPU time per 100 ms, anywhere | placement, inherited across fork and exec |
| Can the job escape? | no | no, throttling is the enforcement | yes: a process may widen its own mask (MPI launchers do) |
| Tools see the right count | yes, including hwloc / Open MPI | no: everything sees 20 | libgomp, Python 3.13 `process_cpu_count()`, `nproc`: yes; hwloc / Open MPI: no |
| MPI / OpenMP behaviour | good | **bad** for many threads: throttled in bursts; spinning ranks stall each other | good |

**Choice for ordinary jobs: (c).**

1. **The shim** (`mu node run-job`, the unit's main process) calls `os.sched_setaffinity(0, grant.cpus)` before it
   starts `mu run …`. Every child inherits it. There is **no** unit `CPUAffinity=` property: it would set the same
   mask on the same process tree a second time, and a rejected property would fail every start.
2. `MU_JOB_CPUS=<list>` and the thread variables (§6), so well-behaved tools never try to widen the mask.
3. **Open MPI** (toolchain: `openmpi-bin` from noble, 4.1.x): its default binding for more than 2 ranks is by package,
   which in this VM is all 20 vCPUs, so it is **expected** to widen the inherited mask (per §12 item 3). μ sets the
   envelope with the MCA parameter behind `--cpu-set` (expected `OMPI_MCA_hwloc_base_cpu_list=<ids>`), fallback
   `OMPI_MCA_hwloc_base_binding_policy=none` (ranks inherit the job's mask). Which one is used is decided by the probe.
4. **Detection, not prevention.** The runner's 5 s sampler (§7.1) reads `Cpus_allowed_list` of **every thread** of
   the job's processes (`/proc/<pid>/task/*/status` for each pid in `cgroup.procs`; a thread can have its own mask)
   and, on a mask wider than the grant, writes once to `events.log` and `state.json` `affinity_widened: {"comm": "…",
   "cpus": "0-19"}`, shown in `mu status` and in the result's `note`.

**(b) only for express jobs**: `CPUQuota=100%` (§3.4). One thread under a one-CPU quota is not throttled; anything
more is held to one CPU.

`CPUWeight` is left at its default (pinned jobs on disjoint CPUs do not compete). §5.4 hardens placement.

### 5.3 What `hello` reports

`limits.cpu`: `"affinity"` now, `"cpuset"` once §5.4 is in place, `"none"` if the shim's call failed (not expected on
Linux; the job then still runs, and the grant is only counted).

### 5.4 Later, with a root step (for a visit, or ROADMAP step 4.2)

| Step | What | Gain | Needs |
|---|---|---|---|
| R-sched-1 | `ensure_cgroup_delegation` writes `Delegate=cpu cpuset io memory pids`. **Today it returns early when its drop-in exists** (line 1117) and writes one only when memory or pids are missing, so it must compare the drop-in's content with the wanted line and rewrite it (and write one where none was needed before) | `AllowedCPUs=` = the grant: hard placement; hwloc and Open MPI see only the grant; `io` for `IOWeight=` between concurrent writers | cpuset present in the distro's root `cgroup.controllers` (§12 item 1); applies when `mu`'s user manager restarts: **one distro restart while no job runs** |
| R-sched-2 | `status` of the host script checks the user manager's `cgroup.controllers` for `cpuset io` | the owner sees it | — |

μ keeps affinity and adds `AllowedCPUs=` when the job cgroup's `cpuset.cpus.effective` equals the grant after start
(systemd does not fail a property whose controller is missing, so μ checks the file). Nothing in this build waits for
R-sched-1.

### 5.5 P/E cores under WSL2 (expected; per §12 items 5–6)

- Client Windows uses the Hyper-V **root scheduler**: each WSL2 vCPU is a thread of the VM's worker process, placed by
  Windows (Thread Director, QoS, power mode) like any other thread. There is no fixed vCPU → physical core mapping, and
  per-VM CPU controls are not supported with the root scheduler.
- The guest is **expected** not to see core types reliably (x86 has no generic `cpu_capacity`; `hybrid_cpu` and CPUID
  0x1A depend on what Hyper-V passes through; a core type for a floating vCPU would mean nothing anyway).
- **So, unless the probe shows otherwise:** the 20 vCPUs are identical slots. Pinning still separates jobs from each
  other (its purpose here). A 20-rank MPI job runs at the pace of whichever vCPUs Windows has on E-cores at the moment,
  and a desktop's two memory channels saturate long before 20 cores: **two 10-core jobs side by side may beat one
  20-core job**. That is measured, not designed: the scaling series of §14 check 7.

## 6. Thread counts from the grant

Set by the runner in the unit's environment (`--setenv`), from the grant:

| Variable | Value | Read by |
|---|---|---|
| `MU_JOB_CORES`, `MU_JOB_CPUS` | c, e.g. `0-7` (express: 1, all CPUs) | μ wrappers, job scripts |
| `OMP_NUM_THREADS` | c | libgomp (CalculiX, Gmsh's OpenMP, OpenMP BLAS) |
| `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`, `NUMBA_NUM_THREADS` | c | numpy / scipy BLAS, numba |
| `PYTHON_CPU_COUNT` | c | Python 3.13 `os.cpu_count()` / `process_cpu_count()`, hence `multiprocessing`, `concurrent.futures` |
| `CCX_NPROC_EQUATION_SOLVER`, `CCX_NPROC_RESULTS` | c | CalculiX (default 1 thread per stage) |
| `OMPI_MCA_hwloc_base_cpu_list` or `…binding_policy=none` (per §12 item 3) | the CPU list | `mpirun` |
| `TMPDIR` | `/srv/mu/jobs/<id>/tmp` (made at start; removed when the result is written **and** by `interrupted()` before a re-run) | keeps concurrent jobs' temp files and Open MPI session directories apart |

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
| constants | `DEFAULT_RESERVE_SYSTEM = 2 << 30`, `DEFAULT_RESERVE_EXPRESS = 1 << 30`, `EXPRESS = (1, 512 << 20, 600)`, `EXPRESS_MAX = 2`, `SLACK_S = 10`, `START_WAIT_S = 60`, `VALVE_S = 60` |
| `capacity(node)` (new) | `C`, `M`, reserves from the kernel and `node.json` `sched` (each key optional; the tests use it to make a small node) |
| `resolve(spec, cap)` (new, pure) | §4.2: flags, `--quick`, defaults, the old-workstation row; per-dimension `source`; raises `too-big` |
| `verb_submit` | `check_spec`, then `resolve` (refuses `too-big`); `job.json` unchanged apart from what the spec carries; answer adds `sizing`, `waiting_for`, `starts_by` from `plan()` |
| `plan(now_mono, cap, running, queued)` (new, pure) | §3.3 and §3.4; returns starts with their CPU ids and class, `waiting_for` per queued job, `S` of the head or `None`. Unit-tested without systemd |
| `step()` | replaces `if not running and queued:` with `plan()`; starts each returned job in order; with `MU_NODE_CHECK=1` (tests) asserts the invariants after each pass: Σ ordinary grants ≤ (`C`, `M`), CPU sets of ordinary grants disjoint, ≤ `EXPRESS_MAX` express; a violation is journalled and written to `/srv/mu/state/invariant-violation`, which every e2e row checks |
| `start_job(j, grant)` | `prepare_work` first; for a backfilled job the re-check of §3.3; **then** `started_*`, `start_mono`, `boot_id`, grant and `start_unconfirmed` into `state.json` in one write, before `systemd-run`; `MemoryMax` = grant; `RuntimeMaxSec` = grant time; `CPUQuota=100%` for express; the §6 environment; `TMPDIR` made |
| `check_running` | when the unit is seen active: records `active_mono` (`ActiveEnterTimestampMonotonic`) and clears `start_unconfirmed` (today an adopted job keeps the flag forever) |
| `run_job` (the shim) | `os.sched_setaffinity(0, MU_JOB_CPUS)` before `Popen` (not for express); reads its own cgroup's `cpu.stat`, `memory.peak`, `memory.events` and the sampler's `usage.json` just before `write_result`; adds `usage` (dashboard §4.2 format, `source: "cgroup"`) |
| sampler (new, in the runner) | every 5th tick, **outside the lock**: for each running job, `cpu.stat`, `memory.current`, `memory.stat` (`anon`, `shmem`), `memory.peak`, `memory.events`, `pids.current` from the unit's `ControlGroup` (recorded in `state.json` at start, never guessed); keeps the running max of `anon + shmem`; replaces `jobs/<id>/usage.json` (rename, no fsync: a view); checks every thread's `Cpus_allowed_list` (§5.2 item 4). The dashboard later adds `metrics.jsonl` and `now.json` to the same loop |
| `classify` / `write_result` | runner-written results (`oom`, `timeout`, `interrupted`) take `usage` from `usage.json` (`source: "runner-sampled"`); removes `tmp/` |
| `interrupted()`, `start_failed()` | also drop `grant`, `start_mono`, `active_mono`, `boot_id`; `interrupted()` also removes `jobs/<id>/tmp` with `work` and `index` |
| `rebuild_state()` | a rebuilt running job has no grant: holds the whole node (§3.2) |
| `run_runner()` | **unchanged**: the version switch still waits for "idle" (§9) |
| `hello_answer` | `features: ["sched", "usage"]`; `capacity: {cores, mem_bytes, reserve_system, reserve_express}`; `allocated`, `free`; `queue: {running, express, queued}`; `limits.cpu` |
| `status_of`, `verb_list` | per job: `request` (from `resolve`), `grant`, `class`, `position` (in the ordinary queue), `waiting_for`, `starts_by` (head only, or `null`), `affinity_widened`, `usage` (finished jobs) |

**State files**

| File | Change |
|---|---|
| `job.json` (sealed, written once by the node) | unchanged; a new workstation's spec carries `sched: 1` and `limits.quick` (§11) |
| `state.json` (sealed, replaced) | gains `grant` (with `boot_id`, `start_mono`, `active_mono`), `cgroup`, `affinity_widened` |
| `jobs/<id>/usage.json` (new, view) | the sampler's latest figures and running maxima; after a runner restart the maxima restart from current values (acceptable: the shim's `memory.peak` is the kernel's own) |
| `jobs/<id>/tmp/` (new) | the job's `TMPDIR`; removed when the result is written and before a re-run |
| `node.json` | optional `sched: {cores, mem_bytes, reserve_system, reserve_express}` |
| `events.log` lines | `granted cores=8 cpus=0-7 mem=6.0G time=2h class=normal`; `backfilled: ends by +1h05m before the head's +1h20m`; `express`; `affinity widened by mpirun: 0-19` |

**Wall-clock times shown to people** (`starts_by`, `ends by`) are computed at answer time as
`wall_now + (bound − mono_now)` and labelled as such; nothing stores or compares wall times.

**`mu status` wording** (built in `jobs.py` from the JSON):

```
queued #1   frame-fea   waiting for 12 cores / 16 GB (8 cores, 10.5 GB free) · starts by 14:20 at the latest
queued #2   mesh-study  waiting: would delay #1 (frame-fea, starts by 14:20) · with --time ≤ 25m it would start now
running     sweep-3     8 cores (0-7) · 6.0 GB · ends by 14:20 · backfilled
running     probe       express · 1 CPU (quota) · 512 MB · 0m03s
```

When `starts_by` is `null`, the line says why ("start time unknown: a job from the previous release holds the node" /
"disk low" / "a job outlived its time limit"). The "with --time ≤ 25m" hint is `waiting_for.reservation.
fits_if_time_s_le`, computed by `plan()`: it tells Claude what a realistic `--time` would have bought next time; it
does not invite cancel-and-resubmit.

### 7.2 `src/mu/jobs.py` and `src/mu/proto.py`

- `mu submit`: `--quick`; `--cores` help becomes "cores granted to the job (CPUs pinned, thread variables set);
  default: the whole node"; `--mem` help "default: node RAM − 3 GB"; spec gains `"sched": 1` and `limits.quick: true`
  for `--quick`; `--quick` with `--cores`/`--mem`/`--time` is `quick-with-flags` (exit 2).
- Before the snapshot: if `hello.features` has `sched`, compare against `hello.capacity` (`too-big`, exit 2). If it
  has not (old node), `--quick` becomes `--cores 1 --mem 512M --time 10m` and the answer's `note` says "office-pc runs
  one job at a time (μ older than v2026.11.N): queued behind N jobs".
- The `next` suggestion from `runs/*.json` (§4.2).
- Answers: `sizing`, `waiting_for`, `starts_by`; prose: `queued #2 · 8 cores, 6.0 GB, 2 h (flags) · waiting for 8 cores
  (4 free) · starts by 14:20`.
- `mu status [<id>]`, `--wait`: the §7.1 wording; JSON passes the node's fields through (node text stays data:
  `node_message`, never `next`).
- `mu compute status`: one more line, `20 cores, 20.0 GB for jobs · 12 cores, 15.0 GB allocated · 2 running, 1
  express, 3 queued · CPU limit: affinity`.
- `mu fetch` copies `usage` and `grant` into `runs/<id>.json`.
- `proto.py`: `PROTOCOL` stays 1; a `NODE_TOO_BIG` mapping by error code; `parse_size` and `parse_duration` unchanged.

### 7.3 Elsewhere

- `src/mu/fea.py`: Gmsh `General.NumThreads` and `run(ncpu=…)` from `MU_JOB_CORES` (§6).
- `install/hosts/wsl2/mu-wsl2-distro.sh`: **nothing in this build.** R-sched-1 and R-sched-2 (§5.4) are a later
  release, applied at a visit.
- `docs/design/job-layer.md` §8.3, §8.4, §10: a pointer to this document and the corrected "one running job" text.
- `docs/using-compute-jobs.md` and `claude/CLAUDE-toolchain.md`: §8.

## 8. Rules for Claude Code (`claude/CLAUDE-toolchain.md`)

Replaces the "Limits" bullet of "What runs on the node" (the pairing branch also edits this file; rebase on it):

- **The node runs several jobs at once**, each on the cores and memory it was granted. **A job without flags gets the
  whole node for up to 12 h and runs alone**: nothing else ordinary starts beside it or, while it waits at the head of
  the queue, past it. Size every job so others can run beside it:
  - quick checks and diagnostics (under a minute, one thread, small memory): `--quick` (1 core, 512 MB, 10 min, at
    most one CPU's worth). They start at once even when the node is full. Not for sweeps: a batch of small cases goes
    in as ordinary jobs with `--cores 1 --mem … --time …`.
  - heavy runs: always `--cores N --mem SIZE --time DUR`.
    - `--mem`: the previous run's `usage.mem_work_peak_bytes` (in `runs/<id>.json`; `mu submit` prints it in `next`)
      × 1.25. First run of an unknown job: leave `--mem` off (whole node) rather than guess low; above the grant the
      job ends `oom`.
    - `--time`: expected × 2. A realistic `--time` lets the job start ahead of a waiting big one; the default 12 h
      never does. Above it the job ends `timeout`.
    - `--cores`: what the solver uses well. Until `docs/verification/` has the node's scaling curve, prefer two jobs of
      10 over one of 20 when you have two runs to make. A serial sweep: `--cores` = how many cases at once.
- **Inside the job, use the grant, never a hard-coded count**: `mpirun -np $MU_JOB_CORES` (μ sets Open MPI's CPU set;
  do not add `--bind-to core` or a `--cpu-set` of your own), `foamDictionary -entry numberOfSubdomains -set
  $MU_JOB_CORES system/decomposeParDict`, Gmsh `General.NumThreads` = `$MU_JOB_CORES`. `OMP_NUM_THREADS` and the BLAS
  variables are already set. A job that runs N processes (MPI ranks, a pool, `parallel -j $MU_JOB_CORES`) sets their
  inner threads to 1 (`mpirun -x OMP_NUM_THREADS=1`).
- **Everything a job starts ends with it.** No `systemd-run`, `systemctl --user start`, or daemons meant to outlive the
  job (`nohup … &`, `setsid`, `disown`): work started that way escapes the job's memory and CPU limits and is counted
  in no grant (§5.1).
- `too-big` (exit 2): the request exceeds the node; `next` gives the largest values. Do not retry unchanged.
- A queued job's `waiting_for` and `starts_by` say why it waits and by when it starts at the latest (`null`: no promise
  can be made now; the line says why). **Do not cancel and resubmit to jump the queue**: it goes to the back. A note
  `affinity widened by …` means the job used CPUs outside its grant: fix the launcher (usually an explicit
  `--bind-to`).

## 9. Durability and recovery with N running jobs

The job layer's rules (job-layer §9) hold per job; what changes is that several jobs are in each state at once.

| Event | What happens | Test |
|---|---|---|
| **Runner restart** (crash, kill, new version) | Each running unit is adopted as today (R25); its grant comes back from its `state.json`; the allocation is rebuilt before anything starts. A running job without a grant holds the whole node until it ends. Never over-commits | Q10 |
| Runner dies after writing a grant, before `systemd-run` | `start_unconfirmed` is set: next pass asks systemd; no unit → `start_failed` (back to the queue, grant dropped); unit exists → adopted with that grant. The grant was on disk first, so the allocation counted it throughout | Q16 |
| Runner dies during a checkout (before any write) | Nothing about the start is on disk; the job is still queued; its partial `work/` is rebuilt at its next start | Q16 |
| Two starts in one pass, runner dies after the first | The first is running (or `start_unconfirmed`) with its grant on disk; the second is still queued. Next pass re-plans with the first counted | Q17 |
| **Windows restart / VM stop / `wsl --shutdown`** | Every unit is gone and the boot id changes. Each running job goes through the interrupted rule: re-run as attempt 2 at its **old** `accepted_ns`, grant and `tmp/` dropped. They return to the front in their original order and start as they fit | Q13 |
| `systemctl show` fails for one job | That job keeps its grant (or holds the node if it has none). Narrower than today's "no job starts while another's state is unknown", and still never over-commits | unit |
| An error in one job's files during a pass | journalled; that job holds its grant or the whole node; the pass goes on (as today, review: durability 4) | unit |
| A grant-less running job (previous release, rebuilt state) | Holds the whole node: nothing ordinary starts, `starts_by` `null`, express still runs | Q18 |
| Cancel of a running job | Stops the unit. Its grant is freed **only when the unit has ended** and the result is written, never when the marker is written. `S` is recomputed next pass | Q11 |
| `oom` of one job | Its cgroup alone is killed (`MemoryMax` per unit, `OOMPolicy=kill`); others are untouched because Σ grants ≤ `M` | Q09 |
| **Version switch** | **Unchanged: only when idle.** Job-layer §12 and the README promise that the default does not move while `state/busy` exists, and the runner holds `busy` while any job is queued or running; switching with jobs running would also let old versions be cleaned up under live shims. With several jobs the node is idle less often, so a release may take effect later. If that is ever seen to starve updates, it is raised as its own change | — |
| `usage.json` lost | It is a view; the next sample rewrites it; runner-written results then lack the working-set peak (`null`) | — |

## 10. What the dashboard shows

The dashboard (ROADMAP step 3) is built after this and reads these fields; nothing in it is needed for the scheduler.

- **Per node**: a stacked bar of granted cores and of granted memory per running job against capacity (`C`, `M`),
  with the express lane and the system reserve drawn apart; `limits.cpu` ("affinity" or "cpuset").
- **Per running job**: used against granted, cores (`cores_avg` from `cpu.stat` against `grant.cores`) and working
  memory (`anon + shmem` against `MemoryMax`); class (normal, backfilled, express); `ends by`; `affinity widened` as a
  warning line.
- **Per queued job**: position, request, `waiting_for` in words ("needs 8 cores, 4 free; next free by 14:20 when
  sweep-3 reaches its limit"), and for the head `starts by` (or why there is none).
- **Finished jobs**: `usage` beside `sizing.source`, so "whole node by default, used 4.6 GB" is visible.
- Dashboard phase 1 then drops "μ starts recording CPU time and peak memory per job" from its own scope (built here;
  detected by `usage` in `hello.features`) and keeps the sampler's `metrics.jsonl`, `now.json` and the
  `stats`/`watch`/`metrics` verbs. Its "17.8 / 20 cores" column becomes "17.8 / 20 granted".

## 11. Compatibility with the previous release

Protocol **stays 1**: every change is an added field or verb behaviour behind a feature flag, never a changed meaning
of an existing field that an older peer relies on. `check_spec` already ignores unknown keys of the spec and of
`limits`, and the client ignores unknown answer fields.

| Workstation | Node | Behaviour |
|---|---|---|
| new | new | everything above |
| **old** (no `sched` in the spec) | new | Sized as today: `cores` granted = `C` (the job runs alone, except for express jobs), `mem` = `--mem` or `M`, time = `--time` or 12 h, `MU_JOB_CORES` = `--cores` or `C` as before. So a job that passes `--cores 4` but runs 16 ranks keeps working. An old workstation's `--mem` above the new `M` (between ~20 and ~21 GB) is now refused `too-big`; the old client shows it as a node error with the node's message |
| new | **old** (no `sched` in `hello.features`) | `--quick` sent as `--cores 1 --mem 512M --time 10m`; no capacity check before upload; the answer notes "one job at a time on this node"; no `sizing`, `waiting_for` or `starts_by` (shown as "—") |
| either | rolled back to the previous release (owner's "reversible") | The old runner adopts running units (it ignores `grant`), sees jobs running and starts nothing more until all have ended, then runs one at a time. Extra files (`usage.json`, `tmp/`) are ignored. Nothing to undo. Rolling forward again resizes queued jobs by the current rule (§3.2) |

The release reaches both machines by auto-update (one daily check each); for up to a day they may differ, and the
table covers each order. **The meaning of `--cores` changes** from advice to a grant for new workstations only, and
`CLAUDE-toolchain.md` changes in the same release, so Claude reads the new rule with the new behaviour.

## 12. Checked on the PC before the build

**Probe pending.** Job `20261114-161025-2icaxr` ("sched-probe", `~/mu-first-job/probe.py`) is queued behind the owner's
JATAYU jobs; no results exist yet (nothing in `~/mu-first-job/runs/`). Items 1–6 are in that job. Items 7–9 are new
after the review and need a second probe job (`sched-probe-2`), submitted after the first. Each item has a fallback;
the results choose between paths already designed, and the lead records them here before the build starts (ROADMAP
2.5.0).

| # | Fact | Probe item (in a job, as `mu`) | Pass criterion | If it fails | Result |
|---|---|---|---|---|---|
| 1 | cpuset is available in the distro's cgroup at all | `cat /sys/fs/cgroup/cgroup.controllers` and the user manager's `cgroup.controllers` | the first lists `cpuset` (needed for R-sched-1); the second shows today's `cpu memory pids` | R-sched-1 is dropped; affinity stays the only CPU placement (no change to this build) | pending |
| 2 | `CPUAffinity=` in a user unit | `systemd-run --user -p CPUAffinity=0-3 --wait --pipe grep Cpus_allowed_list /proc/self/status` | recorded only | not used by the design any more (§5.2); kept because the job is already queued | pending |
| 2b | the shim's way works | `os.sched_setaffinity` to `{0,1}`, then a child: same mask; a child widening itself back | `[0, 1]` in parent and child; widening succeeds (it is cooperative) | (not expected on Linux) `limits.cpu: "none"`; grants are counted, not pinned | pending |
| 3 | Open MPI's CPU-set parameter, and whether `mpirun` widens an inherited mask | `ompi_info --param hwloc base --level 9 \| grep -i cpu`; `taskset -c 0-3 mpirun -np 4 grep Cpus_allowed_list /proc/self/status` with and without `OMPI_MCA_hwloc_base_cpu_list=0-3` and with `OMPI_MCA_hwloc_base_binding_policy=none` | the parameter name exists; with it (or with binding `none`), every rank's list is inside `0-3` | use `binding_policy=none`; if even that widens, the sampler flags it and CLAUDE-toolchain says `--bind-to none` explicitly | pending |
| 4 | `memory.peak` exists in a job unit's cgroup | `cat /sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup)/memory.peak` inside a job | a number | `mem_peak_bytes` from the sampler's running max of `memory.current` (dashboard §4.2) | pending |
| 5 | what the guest sees of P/E cores | `lscpu -e`; `hybrid_cpu` flag; `cpu_capacity`, `topology/core_type`; `/sys/devices/cpu_core`, `cpu_atom` | recorded | §5.5 assumes nothing is visible; if types are visible **and** stable (item 6), a later note may revisit | pending |
| 6 | whether Windows keeps any vCPU → core mapping | a single-thread loop pinned to each vCPU in turn, 3 rounds, timings per vCPU | stable two clusters = quasi-stable; spread = floating | treat as floating (§5.5) | pending |
| 7 | the system's memory footprint at idle | `/proc/meminfo` (MemTotal, MemAvailable) and `memory.current` of `system.slice`, `user.slice` and the user manager, minus the probe job's own cgroup | recorded | keep `reserve_system` = 2 GiB; push time is §14 check 8 | new, `sched-probe-2` |
| 8 | `CPUQuota=100%` in a user unit | `systemd-run --user -p CPUQuota=100% --wait` of a 4-thread busy loop: `cpu.max` reads `100000 100000`; CPU time ≈ wall time | as stated | express units get no quota; §8's `--quick` rule then says "one thread only", and the sampler flags an express job above 1.2 cores | new, `sched-probe-2` |
| 9 | how much an express thread slows a heavy job, and the reverse | a 20-thread busy loop in one unit; a timed single-thread loop alone, then in a second unit with `CPUQuota=100%` beside it; the 20-thread loop's rate before and during | recorded: the single thread within 1.5× of alone; the heavy job slowed ≤ 1/20 | if the single thread is much slower, express units get `CPUWeight=` above the default | new, `sched-probe-2` |

Items 7–9 call `systemd-run --user` inside a probe job on purpose; §8 forbids that in ordinary jobs.

After the release, once, also by a job: the **scaling series** (np 1, 2, 4, 8, 12, 16, 20) of the benchmark suite's
FEA and CFD cases, recorded in `docs/verification/` (§14 check 7). It informs the guidance on `--cores`, not the code.

## 13. Tests (CI, ubuntu runner, real systemd)

**Three layers**, because rows whose fault is "the feature switched off" do not catch subtle errors:

1. **`plan()` unit tests** in `tools/test-jobs/unit.py`, table-driven (fit, `extra`, shadow time, margin, valve,
   express and its fall-through, grant-less running job, disk low, capacity lowered after submit), each with its fault.
2. **A randomised simulation** of `plan()` (seeded, a few thousand steps): random arrivals (sizes, `--time`,
   `--quick`), random early ends, cancels and runner restarts (state rebuilt from the simulated `state.json`s), units
   that run to their limit. Asserted at every step: Σ ordinary grants ≤ (`C`, `M`); CPU sets disjoint; ≤ 2 express;
   **the head starts no later than the first `S` announced for it** (when one was); no job waits forever. Its faults:
   `margin-zero`, `extra-not-reduced`, `cpus-reused`, `express-from-pool`, each must make it fail.
3. **e2e rows** in `tools/test-jobs/e2e.sh` on `ubuntu-24.04` with the real user manager (delegated `cpu memory pids`,
   as P3 measured), each run, then run again with its fault, which must make it fail ("FAILS-OK"), as the job layer's
   R rows. The runner has 4 vCPUs and 16 GB, so the tests set `node.json` `sched: {cores: 4, mem_bytes: 2G}` (`C` = 4,
   `M` = 2 GiB) and use `MU_NODE_TICK` as today. **Every row runs with `MU_NODE_CHECK=1`**: the runner asserts the
   invariants on every pass (§7.1 `step()`), and a row fails if `invariant-violation` exists at its end.

| # | Check | Fault that must make it fail |
|---|---|---|
| Q01 | **concurrent**: two `--cores 2 --mem 512M` jobs run at the same time (overlapping `started_at`…`ended_at`) | `one-at-a-time` (today's rule) |
| Q02 | **memory admission**: three `--cores 1 --mem 1G` jobs with `M` = 2 GiB: two run, the third waits with `waiting_for.resources.mem_bytes` | `no-mem-admission` |
| Q03 | **core packing**: three `--cores 2` jobs on 4 cores: two run, the third waits with `waiting_for.resources.cores` and starts when one ends | `no-core-admission` |
| Q04 | **backfill**: A (`--cores 2 --time 3m`) runs; head H (`--cores 4`) waits; B (`--cores 2 --time 10s`) starts at once (`class: backfilled`); C (`--cores 2 --time 1h`) waits with `waiting_for.reservation` | `backfill-ignores-shadow` (C starts) |
| Q05 | **starvation guard**: H waits behind A; a short job is submitted every 2 s for 90 s; H starts by its first announced `starts_by` | `no-head-reservation` (backfill anything that fits now) |
| Q06 | **express lane**: while a whole-node CPU-bound job runs, a `--quick` job finishes within 15 s; its unit has `CPUQuota=100%` and a 4-thread busy loop in it uses ≤ 1.2 cores; a third `--quick` job, lane full, runs as an ordinary 1-core job once one fits; explicit `--cores 1 --mem 256M --time 1m` is **not** express | `no-express`; `no-express-quota`; `express-by-size` |
| Q07 | **isolation**: in two concurrent jobs every **thread's** `Cpus_allowed_list` equals its grant; the two sets are disjoint; a child **thread** that widens its own mask is flagged `affinity_widened` | `no-affinity` (the shim skips it); `widen-check-main-thread-only` |
| Q08 | **thread counts**: inside a `--cores 3` job, `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `PYTHON_CPU_COUNT`, `CCX_NPROC_EQUATION_SOLVER`, `MU_JOB_CPUS`, `TMPDIR` equal the grant and the job's own folder | `no-thread-env` |
| Q09 | **oom of one job does not touch others**: A (`--mem 512M`) allocates 1.5 G and ends `oom`; B (`--mem 1G`, holding 900 MB) beside it ends `succeeded` with `usage.mem_events.oom_kill` 0 | `mem-max-is-capacity` |
| Q10 | **runner restarts mid-run**: with three jobs running, the runner is killed (`SIGKILL`) and restarts; all three adopted, finish once, grants unchanged; a queued job that fits only if the grants were forgotten stays queued until one ends | `grant-in-memory-only` |
| Q11 | **cancel**: one of three running jobs cancelled ends `cancelled`, its process tree is gone, the other two continue; a queued job then gets its cores, and at no sample do two live jobs share a CPU | `free-on-cancel-request` |
| Q12 | **too big**: `--cores 64` and `--mem 1T` exit 2 `too-big` with the capacity, and nothing reached the node (no push, no submit in `audit.log`); the node's own check refuses a spec sent past the client; a queued job whose request exceeds a capacity lowered after submit ends `too-big` | `no-capacity-check` |
| Q13 | **VM stop with N running**: three running jobs' units are killed with the runner stopped (the interrupted path): all three are re-queued as attempt 2 in their original order, their `tmp/` gone, and start again as they fit | `requeue-new-ns`; `keep-tmp-on-rerun` |
| Q14 | **old workstation**: a spec without `sched` and with `limits.cores = 1` runs with `cores` granted = `C` (alone) and `MU_JOB_CORES` 1 | `legacy-sized` |
| Q15 | **express does not move the head**: H waits with `starts_by` S; two `--quick` jobs start and end; H's `starts_by` is unchanged throughout and H starts by S | `express-from-pool` |
| Q16 | **death around a start**: (a) the runner is killed between the grant write and `systemd-run`: next start, the job is `start_failed` back to the queue with its grant dropped (or adopted if the unit exists), never counted twice; (b) killed during the checkout: the job is still queued, nothing on disk changed | `grant-after-systemd-run` (allocation misses a starting job: the invariant check fires) |
| Q17 | **death after the first of two starts**: two jobs fit in one pass; the runner is killed after the first `systemd-run`; after restart the first is adopted with its grant, the second starts, CPU sets disjoint | `cpus-from-plan-cache` (CPU ids from a stale plan, not the rebuilt allocation) |
| Q18 | **grant-less running job holds the node**: a running job's `grant` is removed from its `state.json` (as the previous release would have written it); no ordinary job starts until it ends, `starts_by` is `null` with the reason, a `--quick` job still runs | `grantless-counts-zero` |
| Q19 | **status wording**: a waiting job's `mu status` says "waiting for 2 cores / 1.0 GB"; the head has `starts_by`; JSON `waiting_for` matches; `starts_by` `null` when disk is low | `no-waiting-for` |
| Q20 | **usage recorded**: shim-written and runner-written (`oom`, `timeout`) results carry `usage` with `cpu_s`, `mem_peak_bytes`, `mem_work_peak_bytes`, `source`; `hello.features` has `usage` | `no-usage` |
| Q21 | **end bound counts from the real start**: a slow checkout (fault-injected 20 s) before a backfilled job: its re-check refuses it if it would now end after `S`; a job's `active_mono` is set and `start_unconfirmed` cleared once the unit is active | `stamp-before-checkout` |

Existing rows R14, R17, R18, R19, R25 run unchanged; R19 ("three submits run one at a time") becomes "three
whole-node jobs run one at a time" (unflagged jobs are whole-node), so it still holds and still fails with
`start-on-submit`. A single thread against 20 busy threads cannot be shown on 4 CI cores: that is §12 item 9.

## 14. PC checks after the release (by jobs, no visit)

Each from the Mac through Claude or the owner, after both machines show the release in `mu compute status`:

| # | Check | Pass |
|---|---|---|
| 1 | `mu compute status office-pc --json` | `features` has `sched` and `usage`; `capacity.cores` 20; `capacity.mem_bytes` ≈ MemTotal − 3 GiB; `limits.cpu` `affinity` |
| 2 | Two `--quick` jobs while a whole-node job runs | both finish within seconds; the big job's log shows no restart |
| 3 | Two `--cores 10 --mem 8G --time 30m` jobs printing `Cpus_allowed_list` and the §6 variables, both writing 2 GB to disk | both running at once; disjoint lists of 10; variables = 10; `/proc/pressure/io` recorded (decides whether a running-job cap is needed, §3.1) |
| 4 | One `--mem 512M` job allocating 1 GB beside a `--mem 4G` job | the first `oom`, the second `succeeded` |
| 5 | Backfill: a whole-node job queued behind a `--cores 10 --time 20m` job; then `--cores 10 --time 5m` and `--cores 10 --time 2h` | the 5 m job starts at once (`backfilled`); the 2 h job waits with `reservation`; the head starts by its `starts_by` |
| 6 | An `mpirun -np $MU_JOB_CORES` job with `--cores 8` | no `affinity_widened`; ranks' lists inside the grant |
| 7 | Scaling series np 1…20, FEA and CFD benchmarks | curve recorded in `docs/verification/`; CLAUDE-toolchain's `--cores` advice updated from it |
| 8 | The system's memory at push time: a `--quick` job samples `/proc/meminfo` and `system.slice` / `user.slice` `memory.current` each second for 5 minutes while the largest project is pushed and submitted | peak footprint outside jobs recorded; `reserve_system` lowered (in `node.json`, no release) only with ≥ 512 MiB margin above it |
| 9 | The JATAYU agent's next real jobs | succeed as before; `usage` recorded; once Claude passes flags, their queue wait drops |

## 15. Effort, and place in the roadmap

| Piece | Part | Lines (approx.) | Days |
|---|---|---|---|
| `capacity`, `resolve`, `plan()` (admission, head reservation by `extra`, express and fall-through, CPU ids), `step()`/`start_job()`/`check_running` changes, recovery, invariant check | A | 330 in `node.py` | 2 |
| `too-big` (node and client), `CPUQuota` for express | A | 40 | ½ |
| `usage` and the 5 s sampler (shared with the dashboard), per-thread affinity check | A | 150 in `node.py` | 1 |
| `jobs.py` and `proto.py`: `--quick`, capacity check, wording, the `next` suggestion from `runs/`, compatibility paths | A | 170 | 1 |
| `fea.py`, CLAUDE-toolchain, using-compute-jobs, job-layer pointers | A | small | ½ |
| End bounds on the monotonic clock, re-check after checkout, time-based backfill, valve, `starts_by`, the `--time` hint | B | 80 in `node.py` | 1 |
| Tests: `plan()` unit tests and the randomised simulation (~200), e2e rows Q01–Q21 (~400), each with its fault | A + B | ~600 | 2–3 |
| **Total** | | **~770 + ~600 tests** | **7–9 days**, no PC visit |

- **If days must be cut**, part A ships first as its own release (admission, head reservation, express, `usage`,
  `too-big`: about 5–6 days with its tests), and part B (time-based backfill, `starts_by`) follows in a second release
  (about 2–3 days). Part A alone already lets small and flagged jobs run beside each other; part B pays off only once
  Claude passes realistic `--time`.
- The earlier estimate (6–8 days, with history sizing, an oom retry and a version switch) undercounted the tests; the
  cuts of §19 remove about 150 lines and the added tests put most of them back.

**Order** (ROADMAP step 2.5): after **step 1** (the pairing fixes, which also touch `node.py` lines 632–645 and 1559,
`jobs.py` and `CLAUDE-toolchain.md`: rebase on them) and after **step 2** (the node's auto-update reports `ok` again,
or the release cannot reach the PC). The probe of §12 (both jobs) runs before the build starts, through jobs. The
owner answers §17 Q1 and Q2 before the release. Dashboard sub-step 3.1 then reuses `usage` and the sampler.

## 16. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| Claude leaves flags off | jobs stay whole-node: no gain | §8 says so plainly; `sizing.why` and the `next` suggestion from the last run |
| The 2 GiB system reserve is too small when a push and several checkouts coincide | the global OOM killer ends the **largest job**, not the offender | reserve kept at today's 2 GiB, measured by §12 item 7 and §14 check 8 before any reduction |
| Unflagged jobs get ~1 GiB less memory than today | a job peaking between ~20 and ~21 GB now ends `oom` | `usage` shows peaks; §17 Q2; `node.json` `sched.reserve_express` can be lowered without a release |
| A job calls `systemd-run --user` or starts a daemon | its work escapes memory and CPU limits | §5.1 states the limits are cooperative; §8 forbids it; the jobs are the company's own |
| Open MPI widens the mask | ranks share CPUs with a neighbour; both slow | μ sets the envelope (§5.2 item 3, per §12); the sampler flags it per thread; R-sched-1 makes it impossible |
| Jobs with hard-coded thread counts | oversubscribe their own grant: slower, others unaffected | affinity confines them; `fea.py` fixed; guidance |
| Memory bandwidth and E-core placement | two jobs each slower than alone | measured by the scaling series; guidance from it |
| Express jobs slow a heavy job | at most one CPU's worth, 10 min, 2 at a time | `CPUQuota=100%`; §12 item 9 measures it |
| Disk: several `work/` copies at once | `disk-low` sooner | per-start check (§3.6); retention rule (ROADMAP §2.3 Q9) |
| I/O contention without the `io` controller | concurrent writers slow each other | measured by §14 check 3; a running-job cap or `IOWeight` after R-sched-1 only if seen |
| New releases take effect later (switch only when idle) | an update waits for a gap between jobs | contract kept (§9); raised separately if ever observed |
| The office PC feels slower to people at it | more cores busy more of the time | the VM may already use all 20 vCPUs today; `node.json` `sched.cores` lowers `C` without a release (§17 Q1) |
| Conflicts with the pairing branch | merge work | small overlap; rebase before building |
| Auto-update still failing | the release does not reach the PC | ROADMAP step 2 is a prerequisite |
| A PC fact differs from the expectation | a path changes | the probe runs before the build; every §12 item has a designed fallback |

## 17. Open questions for the owner

Defaults apply until the owner says otherwise. **Q1 and Q2 need an explicit answer before the release**, because the
office PC is "not to be disturbed".

1. **Keep some of the PC for the office?** Jobs may use all 20 vCPUs today, but one job rarely kept them all busy;
   with several jobs at once they will be busy more of the time. Lower `C` (e.g. 16) so people at the PC always have
   headroom? Default: 20, settable in `node.json` without a release.
2. **Unflagged jobs get about 20 GB instead of about 21 GB** (1 GiB set aside for `--quick` jobs) until the system's
   footprint is measured. Accept? Default yes.
3. **The root step R-sched-1** (cpuset and io delegation, one distro restart) at the next visit or with ROADMAP 4.2?
   Default: with 4.2, only if §12 item 1 shows cpuset available.
4. **A `--priority` flag**, or fair share between workstations: not now (one submitter); revisit with a second
   workstation.

## 18. Read in sources

- Research note of 2026-11-14 (scheduler), with its sources: systemd.resource-control(5), systemd.exec(5),
  `user@.service` of systemd v255 (`Delegate=pids memory cpu`), the kernel's cgroup v2 document (cpuset, cpu.max,
  memory.high/max/peak), sched_setaffinity(2), Slurm `sched_config`, `slurm.conf`, `cgroup_v2`, the noble `slurm-wlm`
  package, HTCondor partitionable slots, Nomad's licence, pueue's README, Microsoft's Hyper-V scheduler types,
  Launchpad 2003985, Open MPI 4.1 `mpirun(1)`, hwloc topology flags, the CalculiX manual (threads), Gmsh `NumThreads`,
  Python 3.13 "What's new"; Lifka 1995 (EASY), Mu'alem & Feitelson 2001, Tsafrir, Etsion & Feitelson 2007 (from
  memory, not re-fetched). conda-forge's `slurm` package listing (22.05.9), checked 2026-11-14. HyperQueue from
  memory, not re-fetched.
- μ at a3a7016: `node.py` (`hw_info`, `scan_jobs`, `queued_jobs`, `status_of`, `write_result`, `hello_answer`,
  `check_spec`, `verb_submit`, `prepare_work`, `mem_default`, `start_job`, `start_failed`, `interrupted`,
  `rebuild_state`, `check_running`, `step`, `set_busy`, `run_runner`, `run_job`, `selftest`), `jobs.py` (submit flags
  and spec, `hello`), `proto.py` (protocol 1), `fea.py` lines 41 and 218, `durable.py` (faults), `mu-wsl2-distro.sh`
  `ensure_cgroup_delegation` (line 1114), `toolchain/linux-64/apt.txt` (`openmpi-bin`, `libopenmpi3t64`), job-layer
  §7–§10, §12, §13, §21.3, dashboard §4.2, §5.1, §6.2, ROADMAP §0–§2, PLAN §17, README (auto-update and `state/busy`).
- The pairing branch's diff against `main` (for the overlap in §15).
- `~/mu-first-job/probe.py` (items 1–6) and `mu status 20261114-161025-2icaxr` (still queued, 2026-11-14 evening).

## 19. Review (2026-11-14)

An independent reviewer read the design, the research note and the code it cites (nothing run on the node). Verdict:
"not ready to build as written; the core is sound; about a third of the scope should be cut, and five defects need
fixing first." **All findings were accepted.** Two were applied in a slightly different form; none was rejected.

| # | Finding | Done |
|---|---|---|
| 1 | History sizing gives no concurrency (cores never shrink) and risks a silent `oom` plus a whole-node re-run that ignores `--no-rerun` | Cut: history sizing, `history.jsonl`, the oom re-run, `request_override`, old Q16/Q17. `usage` kept. The last run's peak is suggested in `next` — **computed on the workstation from `runs/*.json`** rather than on the node, so the node keeps no history index (§4.2) |
| 2 | Express by size traps small jobs two at a time; express units unlimited on CPU | Express only by `--quick`; a full lane falls through to the ordinary queue as a 1-core job; `CPUQuota=100%` on express units; Q06, §12 item 8 (§3.4) |
| 3 | Version switch with jobs running contradicts the `state/busy` contract; `start_unconfirmed` never cleared on an adopted job | Dropped; switch stays "idle" (§9). `check_running` now clears `start_unconfirmed` (needed for end bounds anyway) |
| 4 | `starts_by` not a hard bound: stamped before the checkout, wall-clock, backfill test without slack | Grant and stamp written after `prepare_work`; monotonic clock with boot id; candidates use `time + MARGIN`; `null` when no promise can be made. **In addition**, the bound uses the unit's own `ActiveEnterTimestampMonotonic` once known, and a backfilled job is re-checked after its checkout, since stamping "just before `systemd-run`" still leaves up to 60 s of `systemd-run` and the checkout of jobs started later in the same pass (§3.2, §3.3, Q21) |
| 5 | Memory guarantee overstated; 1 GiB system reserve unmeasured; jobs can leave their cgroup | `reserve_system` 2 GiB, express's 1 GiB out of `M` (~20 GB); measured by §12 item 7 and §14 check 8; §5.1 says both limits are cooperative; §8 forbids `systemd-run`/daemons; §17 Q2 |
| 6 | Affinity set twice | Unit `CPUAffinity=`, the third self-test unit, `selftest.cpu_affinity`, `limits.cpu_affinity_unit` removed (§5.2) |
| 7 | `max_running = 6` unmeasured | Removed; §14 check 3 measures I/O pressure (§3.1) |
| 8 | Sizing frozen at submit | `request` dropped from `job.json`; `resolve()` runs in `plan()` (§3.2) |
| 9 | Unflagged jobs whole-node for 12 h serialise the node | Said plainly in §1 and §8; parts A and B (§3.3, §15) |
| 10 | Build-or-adopt: HyperQueue missing; Slurm root claim overstated; estimate grew | Both rows added and corrected (conda-forge `slurm` 22.05.9 checked); size stated (§2). Verdict unchanged |
| 11 | Tests can pass with the scheduler broken | Randomised simulation, per-pass invariant check in every e2e row, Q15–Q18 and Q21 added; single thread vs 20 threads moved to §12 item 9 (§13) |
| minor | `tmp/` on re-run; per-thread widening check; `usage` in `hello.features`; R-sched-1's early return; owner answer on Q1; effort | All applied (§6, §5.2, §7.1, §5.4, §17, §15) |
