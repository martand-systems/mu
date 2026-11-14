# μ node scheduler: several jobs at once, by the cores and memory each needs

**Status: design, revised after an independent review; nothing built.** Written on branch `scheduler` from `main` at
a3a7016 (v2026.11.8, 2026-11-14), from the research note of the same day (build or adopt, algorithm, enforcement, WSL2
hybrid cores) and the code it cites. Every claim about μ's code was read in `src/mu/node.py`, `src/mu/jobs.py`,
`src/mu/proto.py`, `src/mu/fea.py`, `install/hosts/wsl2/mu-wsl2-distro.sh` and `tools/test-jobs/`. The review's
findings and what was done with each are in §19. **Revised again the same evening for the owner's queue-control
requirements** (ROADMAP §2.1, "Queue control"): hold, release, move and priority bands for queued jobs, pause and resume
of running jobs by systemd freeze/thaw (by hand and as a policy for urgent jobs), one view of the whole node's queue,
and the owner's key allowed to arrange any job. They are §20; the sections they change say so (§1, §3.2–§3.4, §3.7,
§7.1, §8–§13, §15–§17). §20 has not been reviewed independently yet.

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
[18 Read](#18-read-in-sources) · [19 Review](#19-review-2026-11-14) ·
[**20 Queue control**](#20-queue-control-the-owners-requirements-of-2026-11-14) (hold, move, priority, pause, the node's queue)

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
8. **The queue can be arranged without cancelling anything** (owner, 2026-11-14): a queued job can be held, released,
   moved or given a priority band; a running job can be paused and resumed (systemd freeze/thaw), by hand or, for
   urgent jobs, by policy; every Claude session sees the whole node's queue; the owner's key may arrange any job
   (§20).

**What this does not change by itself.** A job submitted **without flags** is still sized as today: the whole node
(all cores, all job memory) for up to 12 h. One such job runs alone (only `--quick` jobs run beside it), and while it
sits at the head of the queue nothing else ordinary starts. Concurrency comes from Claude passing `--cores`, `--mem`
and `--time` (§8); time-based backfill pays off only when `--time` is realistic. This is deliberate: guessing sizes
for unflagged jobs would risk silent `oom` of long runs (§19, finding 1).

**Non-goals:** several nodes (Slurm's job if it ever comes, §2); fair share between workstations (one submitter
today, §3.7; priority bands are now in, §20.3); checkpointing or swapping out a paused job's memory (a pause frees
cores, never memory, §20.6); P/E-core-aware placement (the guest is not expected to see or keep core types, §5.5, per §12);
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
  - **With pause (§20.6, part D):** paused time does not count against `t`. The end bound of a running job becomes
    `active_mono + t + paused time + SLACK`, of a paused one `D + (t − run time) + SLACK` (`D`: its resume
    deadline). From part D on, `RuntimeMaxSec` is a backstop (`t` + the pause budget) and **the runner** stops a job
    when its running time reaches `t`; express jobs and borrowers keep `RuntimeMaxSec = t`.
- **Allocation** = the grants of all running jobs, paused ones included (their memory stays allocated; their cores
  are lent until their deadline, §20.6), **rebuilt from their `state.json` on every pass**. There is no
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
1. free = (C − Σ cores, M − Σ mem, free CPU ids) over ordinary grants, paused ones included; any grant-less running
   job: free = 0. lent = the CPU ids of frozen jobs, each with its lender's deadline D (§20.6).
2. Express (§3.4): each queued --quick job, in walk order, takes a lane slot if one is free; otherwise it stays in Q
   below as an ordinary job of (1 core, 512 MiB, 10 min).
3. Q = the walk of §20.3: queued jobs that are not held, by (band, re-queued first, place in queue.json,
   accepted_ns). While Q's head fits free (or free plus lent cores whose D ≥ now + its time + MARGIN): start it.
   [auto-pause, §20.7: if the head is urgent and fits in memory but not in cores, freeze eligible jobs for it]
4. Head H does not fit. S = the earliest end bound at which, releasing running ordinary jobs in end-bound order,
   free ≥ H's request. extra = what is free at S beyond H's request (cores, memory and CPU ids).
   S = None if: a grant-less job runs, disk is below min_free_gb (§3.6), or the valve (6) is open.
5. For each later job b in order, if b fits free now:
     b fits within extra                         → start (class normal), extra −= b: uses only what H will not need;
     S is not None and now + b.time + MARGIN ≤ S → start (class backfilled): it ends before H needs its resources;
     b fits only with lent cores whose D ≥ now + b.time + MARGIN, its other cores by one of the two rules above
                                                 → start (class borrowing, §20.6): it ends before its lender resumes;
     otherwise                                   → waits, waiting_for = {"reservation": {"head": …, "starts_by": S}}.
   MARGIN = START_WAIT + SLACK (70 s).
6. Valve: if now > S + 60 s (a unit outlived its time limit), no time-based backfill until H starts.
7. Pauses (§20.6): a frozen job whose deadline passed, whose resume was asked, or whose auto-pause reason ended is
   thawed once no borrower holds its CPUs (at D + 60 s regardless).
```

- **Starting is checked twice.** A pass may start several jobs one after the other, and each start first checks out
  the snapshot (`prepare_work`, seconds to minutes). So `start_job` re-checks a **backfilled** job **after** the
  checkout and immediately before writing its grant: if `now + t + MARGIN ≤ S` no longer holds, nothing is written and
  the job stays queued (its `work/` is rebuilt at its next start, as today). A **borrowing** job is re-checked the same
  way against its lender's deadline. The grant, its `start_mono` and the `state.json` write all come after the
  checkout, so the bound counts from the real start.
- **Why the head is not delayed.** Every job started past H either uses only `extra` (resources H does not need at
  `S`) or has an end bound ≤ `S`, enforced by systemd's `RuntimeMaxSec`, counted on the monotonic clock, with the
  checkout and the `systemd-run` wait inside the margin. So H starts at or before `S` unless a unit refuses to stop
  (the valve) or the disk is low (`S` is then `null`, never a false promise). Jobs that end early only bring `S`
  forward. Each later job becomes the head in turn, so no job starves. **With queue control** the head can also be
  displaced or delayed by an explicit act (a move, a priority, a pause of a job it waits for); each such act reports
  whose `starts_by` it moved, and the guarantees that remain are G1–G7 of §20.3.
- **Without `--time`** (the 12 h default) a later job can only use `extra`; time-based backfill needs a realistic
  `--time` (§3.5, §8).
- **CPU ids:** the 20 vCPUs are treated as identical slots (§5.5, per §12), so admission counts cores; the runner gives
  the lowest free ids. No contiguity is needed, now or with cpuset later.
- **Not chosen:** conservative backfill (a reservation for every queued job: more bookkeeping, nothing gained with a
  queue of a handful); a "waited longer than T" guard (the head reservation already guarantees it).
- **Priority classes: reversed.** The first version rejected them ("one submitter; FIFO is what Claude expects").
  The owner's requirements of 2026-11-14 (several Claude sessions on one node, ROADMAP §2.1) need them: four bands,
  FIFO inside each unless moved, held jobs skipped, aging (§20.3). With nobody arranging the queue, the walk is still
  today's FIFO.
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
- take nothing from the pool `S` is computed on, so they cannot delay the head (test Q15);
- are never paused and never trigger an auto-pause (§20.7): they do not wait for cores, and freezing a large job for
  a one-CPU job would idle the rest of its cores.

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

Each job carries `key` (the workstation's name on the node). With a second workstation: order each **band** of the walk
(§20.3) by each key's decayed usage (core-seconds, 24 h half-life; Slurm's fair-share idea), explicit moves and FIFO
within a key; the backfill rule is unchanged. Not built while ROADMAP §2.1 says no new machines. Priority bands, holds
and moves (§20) do not wait for it: several Claude sessions already share one key.

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
| queue control, part C (§20.2–§20.4) | `queued_jobs()` becomes the walk (bands, `queue.json`, re-queued first, held skipped); verbs `queue`, `hold`, `release`, `move`, `priority` with their `ARG_RULES`; `owners()` from `/etc/mu/owners`; `state: "held"` (`verb_cancel` and `step()` treat it as they treat `queued`); `rebuild_state()` reads `held`/`released` lines; aging in `step()`; `hello.features` `queue-control` |
| pause, part D (§20.5–§20.7) | verbs `pause`, `resume` writing `control.json`; in `step()`: apply requests, `systemctl --user freeze`/`thaw` with confirmation, reconciliation with `FreezerState`, deadlines, thaw of a job that has a result, the auto-pause policy; `plan()` lends frozen jobs' cores to borrowers; `start_job`: `RuntimeMaxSec = t + budget` for pausable jobs, the runner's own timeout on running time (`stopping`), `classify` reads it; `write_result` adds `run_s`, `paused_s`, `pauses`; `verb_cancel` thaws before stopping; `hello.features` `pause` |

**State files**

| File | Change |
|---|---|
| `job.json` (sealed, written once by the node) | unchanged; a new workstation's spec carries `sched: 1` and `limits.quick` (§11) |
| `state.json` (sealed, replaced) | gains `grant` (with `boot_id`, `start_mono`, `active_mono`), `cgroup`, `affinity_widened` |
| `jobs/<id>/usage.json` (new, view) | the sampler's latest figures and running maxima; after a runner restart the maxima restart from current values (acceptable: the shim's `memory.peak` is the kernel's own) |
| `jobs/<id>/tmp/` (new) | the job's `TMPDIR`; removed when the result is written and before a re-run |
| `node.json` | optional `sched: {cores, mem_bytes, reserve_system, reserve_express, age_after_s, auto_pause, pause: {max_s, budget_s, auto_max_s, auto_count, auto_cooldown_s}}` (§20.3, §20.6, §20.7) |
| `/srv/mu/queue.json` (new, sealed, replaced) | the explicit order inside bands (§20.3, §20.8) |
| `jobs/<id>/control.json` (new, sealed) | a pending pause or resume request (§20.8) |
| `events.log` lines | `granted cores=8 cpus=0-7 mem=6.0G time=2h class=normal`; `backfilled: ends by +1h05m before the head's +1h20m`; `express`; `affinity widened by mpirun: 0-19` |

**Wall-clock times shown to people** (`starts_by`, `ends by`) are computed at answer time as
`wall_now + (bound − mono_now)` and labelled as such; nothing stores or compares wall times.

**`mu status` wording** (built in `jobs.py` from the JSON):

```
queued #1   frame-fea   waiting for 12 cores / 16 GB (8 cores, 10.5 GB free) · starts by 14:20 at the latest
queued #2   mesh-study  waiting: would delay #1 (frame-fea, starts by 14:20) · with --time ≤ 25m it would start now
running     sweep-3     8 cores (0-7) · 6.0 GB · ends by 14:20 · backfilled
running     probe       express · 1 CPU (quota) · 512 MB · 0m03s
running     cfd-sweep   8 cores (8-15) · 12.0 GB · PAUSED by avi-mac until 14:45 (its cores lent) · resumes by itself
held        mesh-study  keeps place 3 · held by avi-mac 13:02 · mu release mesh-study
```

The whole node, across projects, is `mu queue` (§20.9).

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
  can be made now; the line says why). **Never cancel and resubmit to reorder**: it loses its place and its id;
  `mu move` keeps both. A note `affinity widened by …` means the job used CPUs outside its grant: fix the launcher
  (usually an explicit `--bind-to`).
- **Queue control** (`mu queue`, `hold`, `release`, `move`, `priority`, `pause`, `resume`, `--pausable`): the rules of
  §20.11. In short: look with `mu queue --json`; arrange your own project's queued jobs freely; touch other projects'
  jobs, raise priorities or pause anything only when the owner asked (`--others`); mark long solver runs `--pausable`
  unless they use the network, their own wall-clock timeouts, or measure time.

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
| **Version switch** | **Unchanged: only when idle** (a paused job counts as running; a queue of held jobs only does not, §20.6). Job-layer §12 and the README promise that the default does not move while `state/busy` exists, and the runner holds `busy` while any job is queued or running; switching with jobs running would also let old versions be cleaned up under live shims. With several jobs the node is idle less often, so a release may take effect later. If that is ever seen to starve updates, it is raised as its own change | — |
| `usage.json` lost | It is a view; the next sample rewrites it; runner-written results then lack the working-set peak (`null`) | — |
| Held jobs, bands, the order file, pauses, lending (queue control) | Their own table and races: §20.8. In short: holds and bands live in each job's `state.json` (and holds also in `events.log`); the order in `/srv/mu/queue.json`, whose loss restores FIFO within bands; a pause is recorded before the freeze and lends cores only once frozen; a paused job survives a runner restart frozen, becomes interrupted on a VM stop, is thawed before a cancel | Q22–Q37 |

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
- **Queue control** (§20.10): the node's queue in walk order with bands, `held` and `paused` badges, lent and
  borrowed cores on the cores bar, who arranged what; copy-the-command buttons only (`mu release`, `mu resume`,
  `mu move … --front`), since phases 1–3 are read-only.
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

Queue control's rows (a held job under an old runner, a paused job and a rollback, `node-too-old`) are in §20.12.

The release reaches both machines by auto-update (one daily check each); for up to a day they may differ, and the
table covers each order. **The meaning of `--cores` changes** from advice to a grant for new workstations only, and
`CLAUDE-toolchain.md` changes in the same release, so Claude reads the new rule with the new behaviour.

## 12. Checked on the PC before the build

**Probe pending.** Job `20261114-161025-2icaxr` ("sched-probe", `~/mu-first-job/probe.py`) is queued behind the owner's
JATAYU jobs; no results exist yet (nothing in `~/mu-first-job/runs/`). Items 1–6 are in that job. Items 7–9 are new
after the review, items 10–13 (freeze and thaw, for part D's pause) after the owner's queue-control requirements; all
go in a second probe job (`sched-probe-2`), submitted after the first. Today's node runs one job at a time and has
no queue control, so it waits its turn like the first did: submit it when the JATAYU queue is short. Each item has a fallback;
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

| 10 | **freeze/thaw of a user unit under WSL2** (§20.5) | `systemctl --version`; a unit `systemd-run --user -p Type=exec` running a counter that writes a number to a file every 100 ms; `systemctl --user freeze`, then `show -p FreezerState` and the unit cgroup's `cgroup.events`; read the counter twice 5 s apart; `memory.current` before and after; `thaw`; the counter again; the time each call took | systemd ≥ 246; `FreezerState=frozen` and `frozen 1` within 1 s; the counter flat while frozen and continuing (not restarting) after the thaw; `memory.current` unchanged within 5 % | **no pause on this node**: `hello.features` lacks `pause`, `mu pause` exits 6 `pause-unavailable`; holds, moves and priorities (part C) are unaffected | new, `sched-probe-2` |
| 11 | `RuntimeMaxSec` while frozen; whether it can be changed at runtime (§20.6) | a unit with `RuntimeMaxSec=15` frozen at 5 s for 25 s: when does it stop, with which `Result`; `systemctl --user set-property --runtime <unit> RuntimeMaxSec=60` on a running unit | recorded | expected: it stops at 15 s wall (frozen time counts) and `set-property` refuses: the design as written (backstop plus the runner's own limit). If `set-property` works, μ extends `RuntimeMaxSec` by each pause instead | new, `sched-probe-2` |
| 12 | an MPI job and a wall-clock timeout through a pause (§20.5) | `mpirun -np 4` of a busy loop that checksums its work, frozen 20 s, thawed; and `timeout 10 sleep 8` frozen at 2 s for 15 s | the MPI job completes with the same checksum as an unpaused run; the `timeout` case is **expected to kill** its child at the thaw (recorded for §20.11's rule) | if MPI fails after a thaw, `mpirun` jobs are refused `--pausable` and §20.11 says so | new, `sched-probe-2` |
| 13 | stopping a frozen unit (§20.8) | `systemctl --user stop` of a frozen unit with `TimeoutStopSec=5`; whether systemd thaws it first (does `SIGTERM` arrive?), the time to stop, `Result` | stops within 6 s | none needed: μ thaws before stopping anyway; the figure checks the cancel path's timing | new, `sched-probe-2` |

Items 7–13 call `systemd-run --user` inside a probe job on purpose; §8 forbids that in ordinary jobs. Items 10–13 decide
part D only; parts A–C do not wait for them.

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

**Queue control rows** (§20), on the same runner, with two workstation keys in the CI node (`ci-owner` in
`/etc/mu/owners`, and `ci-other`), every row with `MU_NODE_CHECK=1`. The per-pass invariant check gains: no held job
started; Σ memory grants (paused included) ≤ `M`; CPU sets of jobs not frozen disjoint; every borrower's end bound ≤
its lender's deadline; no frozen unit past its deadline + 60 s; no frozen unit whose job has a result. The `plan()`
unit tests and the randomised simulation gain random holds, releases, moves, priorities, aging, hand pauses and
auto-pauses, and assert the same, plus G1 (the head starts by its first `S` unless an explicit act, which the
simulation logs, moved it) and G5 (auto-pause counts and cooldowns); their faults: `held-in-walk`,
`borrow-past-deadline`, `thaw-over-borrower`, `free-mem-on-pause`, each must make the simulation fail.

| # | Check | Fault that must make it fail |
|---|---|---|
| Q22 | **hold / release**: three queued `--cores 4` jobs A, B, C behind a running one; `hold B`: when the runner frees, C starts before B; B keeps `place` 2; `release B` (during a runner pass) puts it back at its place; `hold` twice is `changed: false`; `--hold` at submit; `mu cancel` of a held job ends it `cancelled` at once | `hold-ignored` (B starts); `release-to-back` (B behind C) |
| Q23 | **move**: `--before`, `--after`, `--front`, `--back` give the order asked, in `mu queue --json` and in the actual start order; a repeated move is `changed: false`; `--before` a running job is `reference-not-queued` (exit 2) | `walk-by-accepted-ns` (moves have no effect on the start order) |
| Q24 | **bands and aging**: a `high` job submitted last starts first; `--front` of a `normal` job stays behind it and the answer gives `higher_bands_ahead: 1` and the `mu priority` step; `--before` a `high` job moves a job into `high`; with `age_after_s` = 20 s a `low` job becomes `normal`, then `high` | `bands-ignored`; `no-aging` |
| Q25 | **order file and held state recovery**: `queue.json` zeroed (the S3 shape): the order falls back to FIFO within bands, holds and bands are kept, one journal line; `state.json` of a held job removed: it is rebuilt held from `events.log` and not started | `holds-in-order-file-only` (the held job starts); `rebuild-held-as-queued` |
| Q26 | **authorisation**: `ci-other` cannot hold, move, pause or raise `ci-owner`'s job (exit 5 `not-allowed`, an audit line, nothing changed); it can arrange its own, but not move it ahead of `ci-owner`'s; `ci-owner` can arrange both and set `urgent`; with `/etc/mu/owners` removed and one key, that key is the owner; with two keys and no file, nobody is | `no-auth-check`; `owner-from-node-json` (an owner list a job could write) |
| Q27 | **one view, two sessions**: jobs from two projects and both keys: `mu queue --json` lists all of them in walk order with `key`, `project`, `place`, `waiting_for`, `starts_by`, held and paused; the text lists the same order; the client's guard refuses `hold` of the other project's job without `--others` (exit 2, nothing sent to the node: no audit line) | `queue-project-filter`; `no-others-guard` |
| Q28 | **mixed versions**: the previous release's runner on a node holding a held job and a moved order: the held job is not started, the rest run FIFO; a new client against an old node: `mu hold` exits 6 `node-too-old`, `mu queue` falls back to `list` and says so | `held-as-queued` (`state.json` `queued` with a flag: the old runner starts it) |
| Q29 | **pause and resume by hand**: a 2-core job increments a counter file; `mu pause --for 60s`: `FreezerState=frozen`, the counter does not move for 5 s, `memory.current` unchanged; a queued `--cores 2 --time 20s` job starts on the lent CPUs (`class: borrowing`) and a `--time 5m` one does not; `mu resume`: same pid, attempt 1, the counter continues from its value | `pause-noop`; `lend-without-deadline` (the 5-minute job starts: the invariant check fires) |
| Q30 | **resume waits for the borrower**: resume asked while a borrower runs: `resuming` with `resumes_by` (exit 10 with `--timeout 2`), thawed within one pass of the borrower's end; at no sample do two unfrozen jobs share a CPU; with a borrower that ignores `SIGTERM` past its limit, the lender is thawed at its deadline + 60 s with an `events.log` line | `thaw-over-borrower`; `no-valve` |
| Q31 | **races**: (a) pause sent as the job exits: the answer is `already-finished` with its state, no frozen unit remains; (b) the shim frozen right after writing `result.json` (fault-injected pause in the shim): the runner thaws it and the job ends once; (c) resume after the unit ended: `changed: false`, exit 0; (d) `hold` sent during a 20 s fault-injected checkout of the same job: `not-queued`, the job runs exactly once | `freeze-after-result` (b stays frozen); `start-twice` |
| Q32 | **runner restart while paused**: runner `SIGKILL`ed with a job frozen: after the restart the unit is still frozen, the deadline unchanged, the job thawed at the deadline and finished once; the pause and lending were never in memory only | `pause-in-memory-only` |
| Q33 | **VM stop while paused**: the frozen unit is killed with the runner stopped (Q13's path): the job is re-queued as attempt 2 at the front of its band, `pause` dropped, `note` says it was paused | `requeue-keeps-pause` |
| Q34 | **cancel while paused**: ends `cancelled` within 10 s, its process tree gone, the job saw `SIGTERM` (its trap writes a file), its memory freed only after the unit ended | `cancel-without-thaw` (no `SIGTERM` file) |
| Q35 | **time accounting**: a `--time 20s` loop paused 15 s ends `timeout` at about 20 s of running and 35 s wall; `result.json` `run_s` ≈ 20, `paused_s` ≈ 15, `pauses` has the entry; an unpaused `--time 20s` job still ends `timeout` at about 20 s; with the runner stopped, the `RuntimeMaxSec` backstop still ends a job (`t` + budget) | `count-paused-time` (timeout at 20 s wall); `no-runner-timeout` |
| Q36 | **limits**: `--for 10s` resumes by itself at 10 s; a pause beyond `max_s` or the budget is refused `pause-limit` with the figures; pausing an express job or a borrower is refused; a job needing the paused job's memory does not start while it is paused | `no-pause-deadline`; `free-mem-on-pause` |
| Q37 | **auto-pause**: with `auto_pause: marked`, a `--pausable --cores 4 --mem 512M` job running on `C` = 4 and an `urgent --cores 2 --mem 256M --time 20s` job submitted: the first is frozen, the urgent job runs on its lent CPUs, the first is thawed when it ends (before the deadline); an unmarked job is never paused; a second urgent job within the cooldown gets no pause; an urgent job whose memory does not fit gets no pause; an urgent job cancelled while queued ends the pause at once; `--quick` never pauses anything | `pause-unmarked`; `no-cooldown`; `pause-without-mem-check`; `keep-pause-after-beneficiary` |

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
| **Total, parts A and B** | | **~770 + ~600 tests** | **7–9 days**, no PC visit |
| Queue control, part C: the walk, holds, moves, priority bands, aging, `mu queue`, authorisation, audit (§20.13) | C | ~430 + ~250 tests | 3½–4 |
| Queue control, part D: pause and resume by freeze/thaw, lent cores, the runner's own time limit, the auto-pause policy (§20.13) | D | ~430 + ~300 tests | 5–6 |
| **Total with queue control** | | **~1,630 + ~1,150 tests** | **about 16–19 days**, no PC visit |

- **If days must be cut**, part A ships first as its own release (admission, head reservation, express, `usage`,
  `too-big`: about 5–6 days with its tests), and part B (time-based backfill, `starts_by`) follows in a second release
  (about 2–3 days). Part A alone already lets small and flagged jobs run beside each other; part B pays off only once
  Claude passes realistic `--time`.
- The earlier estimate (6–8 days, with history sizing, an oom retry and a version switch) undercounted the tests; the
  cuts of §19 remove about 150 lines and the added tests put most of them back.
- **Queue control in two releases** (§20.13): part C ships with A and B in the **first release** (it needs nothing new
  on the PC and, with `--quick`, solves the 2026-11-14 case); part D is a **second release**, after probe items 10–13
  and some days of the first release running real JATAYU work, because it changes how time limits are enforced and
  rests on freeze/thaw under WSL, which nothing has measured yet.

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
| Claude sessions arrange each other's jobs | one session's sweep pushed back or paused by another without the owner knowing | `--others` guard on the workstation; §20.11 (only when the owner asked, and say so); `delays` in each answer and `events.log` of each delayed job; the audit log |
| A paused job breaks on thaw (its own wall-clock timeout, a network peer, a timing measurement) | a failed or wrong run | pause is by hand or opt-in (`--pausable`); §20.5's table and §20.11; probe item 12; `paused_s` in every result |
| freeze/thaw does not work under the PC's WSL kernel or user manager | no pause on the node | probe item 10 before part D; part C does not depend on it; `pause-unavailable` says so |
| The runner's own time limit (part D) misses a job | a job runs past `--time` | systemd's `RuntimeMaxSec` backstop (`t` + budget ≤ 2 `t`); Q35 tests both |
| A held job forgotten for days | its project waits for nothing | `mu queue` and the dashboard show held jobs with who and since when; held jobs do not keep the node busy, so auto-update is not blocked |
| Rollback while a job is paused | the old runner never thaws it; it ends at the backstop | a paused job keeps the node busy (no automatic switch); the README's rollback note says "resume paused jobs first" |

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
4. ~~A `--priority` flag: not now~~ **Superseded** by the owner's queue-control requirements (ROADMAP §2.1,
   2026-11-14): bands, holds, moves and pause are §20. Fair share between workstations: still with a second one.
5. **Auto-pause default.** `sched.auto_pause` = `"marked"` (only jobs submitted `--pausable`), or `"any"` (every
   ordinary job the owner has not excluded)? Default `"marked"`: freezing breaks jobs with their own timeouts or
   network peers, and only the submitter knows. Changeable in `node.json` without a release.
6. **Pause limits.** A hand pause at most 2 h; at most 4 h (and never more than the job's own `--time`) paused per
   attempt; an auto-pause at most 30 min, twice per attempt, 15 min of running in between. Default: as stated.
7. **`urgent` for the owner only** (it can pause other jobs); `high` past another workstation's job also owner-only.
   Today every Claude session on the Mac uses the owner's key, so this binds only a later second workstation; between
   sessions the `--others` guard and §20.11 apply. Default: as stated.
8. **Part D as a second release** (pause after the queue order). Default yes (§15).

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
- For §20, from memory, not re-fetched: systemctl(1) `freeze`/`thaw` (systemd 246) and the `FreezerState` property;
  the kernel's cgroup v2 document (`cgroup.freeze`, `cgroup.events`; fatal signals still end frozen tasks);
  systemd.service(5) `RuntimeMaxSec`; systemctl(1) `set-property` (resource-control settings); Slurm's `scontrol
  hold/release/top` and `squeue` for names. Read in the repository: `node.py` (`serve`, `ARG_RULES`, `VERBS`,
  `verb_cancel`, `queued_jobs`, `status_of`, `step`, `start_job`, `classify`, `interrupted`, `rebuild_state`,
  `run_runner`, `set_busy`), `install/hosts/wsl2/mu-node-gate`, `pair.py` `write_owner`, pairing §9.6, job-layer
  §7, §9, §10, §14, §23.4, shell-ux §2–§4.1, dashboard §4.6, §4.7, §5.1, §7.1, ROADMAP §2.1 and §2.3.

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

**After the review** (same day): the owner's queue-control requirements (ROADMAP §2.1) reversed one of this
design's calls (no priority classes, §3.3) and added §20. §20 needs its own independent review before part C is built
(security: the authorisation table and its honest limits; durability: §20.8; usability for Claude: §20.9, §20.11).

## 20. Queue control (the owner's requirements of 2026-11-14)

**Why this section exists.** ROADMAP §2.1, row "Queue control" (owner, 2026-11-14), from a real case: the probe job
`sched-probe` sat 5th in the queue behind another Claude session's JATAYU jobs. The only way to let it go first was
for that session to cancel its queued jobs and resubmit them (losing their place), and the probe would still have
waited for the running CFD job. Several Claude sessions now share one node. §1–§19 were written before these
requirements and did not meet them (§3.3 even rejected priority classes). This section adds what was missing; every
earlier section it changes says so and points here. Nothing here is built.

| # | Requirement (owner) | Where |
|---|---|---|
| R1 | **hold / release** a queued job, **move** it before or after another job or to the front, **priority**, all without cancelling | §20.2–§20.4 |
| R2 | **pause / resume a running job** by systemd freeze/thaw of its cgroup (CPU released at once, memory kept, the job continues exactly where it stopped), by hand **and** as a policy so an urgent job can briefly pause a long sweep | §20.5–§20.7 |
| R3 | **one view of the whole node queue** across projects and Claude sessions; the owner's workstation key may arrange **any** job on the node | §20.2, §20.4 |
| R4 | small jobs never need anyone to step aside: they start at once when they fit | §20.1 |
| R5 | every operation with `--json` for Claude | §20.9 |

### 20.1 The 2026-11-14 case under this design, and R4

- **The probe needed one core, a few hundred MB and two minutes.** Submitted `--quick`, it takes an express slot
  (§3.4) and starts at once, even beside an unflagged whole-node job. Nobody steps aside. This is R4, and it needs no
  operation from anyone: the express lane and backfill (§3.3) start every job that fits.
- **A small job that needs more than the lane** (say 4 cores, 2 GB, 5 minutes): `--cores 4 --mem 2G --time 10m`
  starts at once when 4 cores and 2 GB are free and either the head of the queue does not need them or the job ends
  before the head's `starts_by`.
- **It must simply go first among the queued jobs:** `mu move <probe> --front`. The JATAYU jobs keep their order
  behind it; nothing is cancelled.
- **It cannot start because a running job holds the cores:** `mu pause <cfd> --for 15m` (when the owner asks). The
  CFD job's cores are lent until the pause's deadline; the probe starts on them because it ends before that deadline;
  the CFD job then continues from where it stopped.
- **Pause frees cores, never memory.** If the running job was unflagged (the whole job memory, §4.2), nothing
  ordinary fits beside it, paused or not; only the express lane does. That is why §8 keeps telling Claude to size
  jobs.
- **Claude sees the node:** `mu queue --json` shows what runs, what is paused, held and queued, in the order they will
  start, who submitted each job and from which project, why each waits and by when it starts.

### 20.2 Commands (workstation)

| Command | What it does | Who may |
|---|---|---|
| `mu queue [--on NODE] [--json] [--mine]` | The **whole node's** queue: running (with paused ones marked), held, queued in the order they will start. Per job: id, name, project, owner workstation (`key`), `submitted.host`, request and grant, band, place, `waiting_for`, `starts_by` / `ends_by`, pause and lending. `--mine`: this project's jobs only (`mu status` stays the per-project list, unchanged) | any paired key |
| `mu hold JOB` / `mu release JOB` | A held job keeps its place but is skipped: it starts nothing, takes no reservation and blocks nothing. `release` puts it back in the walk at the same place | the job's workstation; an owner key |
| `mu move JOB --before JOB2 \| --after JOB2 \| --front \| --back` | Changes the job's place. `--front`/`--back`: the front or back of its own **band**; `--before`/`--after` another queued job: next to it, taking that job's band (said in the answer) | same; passing another workstation's job needs an owner key |
| `mu priority JOB urgent\|high\|normal\|low` | Moves the job to the back of that band | same; raising past another workstation's job, and `urgent`, need an owner key |
| `mu submit … --priority CLASS --hold --pausable` | The band at submit (default `normal`); submit already held; let the auto-pause policy pause this job (§20.7) | `urgent`: owner key |
| `mu pause JOB [--for DUR] [--timeout S]` | Freezes a running job for `DUR` (default 30m, at most `sched.pause.max_s`, 2 h); its cores are lent until then; it resumes by itself at the deadline | the job's workstation; an owner key |
| `mu resume JOB [--timeout S]` | Thaws a paused job, as soon as no borrower is on its cores (they end by the deadline, §20.6) | same |

Every command takes `--json`, `--on NODE` (default: the job's node from this clone's record, else the only paired
node) and `--others` (below). `pause` and `resume` wait up to `--timeout` (default 60 s) for the change to be complete.

**Names (shell-ux §4.1).** The shell plan keeps top-level verbs for daily job work, so these are top-level too:
`queue`, `hold`, `release`, `move`, `priority`, `pause`, `resume`. **Nothing existing is renamed**: `mu status` keeps
its meaning (this project's jobs; `mu jobs` stays its phase-2 synonym), and `mu queue` is new and node-wide. The task's
sketch said `--node N`; every job command already says `--on NODE`, so these do too (no `--node` alias). `release` is
Slurm's word for un-holding, which Claude knows; no later command about μ's own releases may take it (those are
`update` and `install`). Each command gets a row in shell-ux §4.1 and its command table; shell-ux phase 2's names,
`@last` and id prefixes apply to them as to `status` and `cancel`.

**`--others`: a guard between Claude sessions, on the workstation.** All Claude sessions on the owner's Mac use one
workstation key (`avi-mac`), which is also the node's owner, so the node cannot tell sessions apart. The client
therefore refuses (exit 2, `other-project`) any operation that arranges a job of **another project**, or that moves a
job ahead of, or pauses, another project's job, unless `--others` is given. `CLAUDE-toolchain.md` says when to pass it
(§20.11). Two sessions working in the same project are not told apart; nothing here needs them to be.

### 20.3 Order: priority bands, explicit order inside each, held jobs skipped

**The walk.** `plan()` (§3.3) no longer takes queued jobs by `accepted_ns`. It walks one ordered list:

```
W = queued jobs that are not held, sorted by
      (band: urgent < high < normal < low,
       re-queued after an interruption first (attempt ≥ 2, in their old accepted_ns order: today's rule, kept),
       place in /srv/mu/queue.json (jobs not listed come after the listed ones),
       accepted_ns)
```

- **The head** is W's first job; the head reservation, `S`, `extra` and backfill of §3.3 apply to it unchanged. Held
  jobs are not in W at all: never the head, never a candidate, no reservation, no `waiting_for` but `held`.
- **Express** jobs (`--quick`) take a lane slot in W's order; when the lane is full they stay in W at their place, as
  §3.4 says.
- A new job goes to the back of its band (it is not in `queue.json`, so it sorts by `accepted_ns` after the listed
  ones): FIFO, exactly as today when nobody arranges anything.

**Chosen: bands with an explicit order inside.** Not explicit order only, because: (1) `mu submit --priority high`
needs a well-defined place without the submitter reading the queue; with explicit order only, priority would be a
one-off move, and a later `normal` arrival could not be kept behind it; (2) the auto-pause policy needs a class
(`urgent`) that names who may pause others; (3) with nobody arranging, bands with FIFO inside are today's FIFO
exactly; (4) aging (below) needs bands to move a job through. Not priority **numbers** with a formula (Slurm's
multifactor): four named bands are what a person or Claude can predict from `mu queue`. Fair share between
workstations (§3.7), if it comes, orders jobs within a band.

**`move` across bands.** `--before`/`--after` a job of another band puts the job in that band, next to it; the answer
says `"band": "high", "was": {"band": "normal", …}`. So "before X" always means exactly that place. `--front` and
`--back` stay inside the job's own band; when higher-band jobs stay ahead, the answer says how many and gives the step
(`mu priority JOB high` to pass them).

**Aging.** A queued, unheld job that has waited longer than `sched.age_after_s` (default 24 h) moves up one band (low
→ normal → high; never to `urgent`), once per period, to the back of the new band; `events.log` and the audit log say
so.

**What still holds (starvation guarantees).**

| | Guarantee |
|---|---|
| G1 | The head starts no later than its announced `starts_by`, **unless an explicit act changes what it waits for**: a job placed ahead of it (move, priority, an `urgent` or `high` submit, a release of a held job placed ahead), or a pause of a running job it waits for (`starts_by` moves by the pause). Each such act's answer lists the jobs whose `starts_by` it moved (`delays`), and `events.log` of each delayed job says by whom |
| G2 | A held job blocks nothing and is never started |
| G3 | Within a band, a job is never passed by a later arrival; only an explicit move puts a job ahead of it |
| G4 | Aging: a job cannot be kept behind newer higher-band arrivals for ever; it reaches `high` within 2 × `age_after_s` |
| G5 | Auto-pause is bounded per running job: at most 2 per attempt, each at most 30 min, at least 15 min of running between two (§20.7): it delays a job by at most an hour |
| G6 | A hand pause ends at its deadline (at most 2 h); the paused time of one attempt is at most its pause budget (§20.6) |
| G7 | §3.3's invariants still hold on every pass: Σ memory grants (paused jobs included) ≤ `M`; the CPU sets of jobs **not frozen** are disjoint; a borrower ends before its lender's deadline |

No guarantee survives an owner who keeps moving one job to the back; that is the owner's call, and the audit log shows
it.

### 20.4 Node verbs, `ARG_RULES`, authorisation, audit

**Verbs** (in `node.py`'s `VERBS`; the gate needs no change: it checks only the shape, `verb = [a-z-]+`, `key=value`
arguments, job-layer §7.1):

| Request | Answer (§20.9) | Lock |
|---|---|---|
| `queue` | the whole node's queue: capacity, allocation, `rev`, the jobs in walk order (§20.9) | read only, no lock |
| `hold id=J` / `release id=J` | the job's place, band, `held`, `delays` | `/srv/mu/lock` |
| `move id=J before=K` / `after=K` / `to=front` / `to=back` | same | lock |
| `priority id=J class=C` | same | lock |
| `pause id=J [for=S]` | the pause as recorded; the verb waits (≤ the client's `--timeout`) for the runner to apply it | lock to record; the runner freezes |
| `resume id=J` | same, for the thaw | same |

New `ARG_RULES`: `before` and `after` = `proto.JOB_ID`; `to` = `^(front|back)$`; `class` =
`^(urgent|high|normal|low)$`; `for` = `^[0-9]{1,6}$` (seconds). `submit`'s spec gains `priority`, `hold` and
`limits.pausable` (checked by `check_spec`; unknown keys are already ignored by old nodes, §11).

**Who may arrange which job.** The verb receives the caller's key name from the gate (`--key=`, taken by root's gate
from the root-owned key file, job-layer §7.1). The node reads, on every call:

- **Owners**: the names in `/etc/mu/owners` (root, 0644; written by pairing at the node's first pairing by invite,
  pairing §9.6: on the office PC, `avi-mac`). If that file does not exist (a node paired by hand, first-job-checklist appendix
  A) and `/etc/mu/authorized_keys/mu` holds exactly one key, that key's name is the owner; otherwise there is none.
- **A job's workstation**: `job.json` `key`, written by the node at acceptance.

| Operation | The job's own workstation | An owner key | Any other paired key |
|---|---|---|---|
| `queue`, `status`, `list` | yes | yes | yes (job-layer §14: a key already reads everything) |
| `hold`, `release`, `pause`, `resume`; `move` and `priority` (not `urgent`) that pass only this workstation's own jobs | yes | yes | **no**: `not-allowed` |
| `move` or `priority` that puts the job ahead of **another workstation's** job | no | yes | no |
| `priority urgent`, `submit --priority urgent` | no | yes | no |
| `submit --pausable` (own job) | yes | yes | — |

**This is a guard against mistakes, not a security boundary**, and the design says so as job-layer §14 does: any
paired key can run a job as `mu`, and a job can rewrite every state file on the node. The rule stops one workstation
or session from reshuffling another's work by accident; it cannot stop a key holder who wants to.

**Audit.** Every call is already journalled with key, verb and id (job-layer §7.3). Each operation adds one outcome
line to `/srv/mu/audit.log` and the journal, and one to the job's `events.log`:

```
queue: hold id=…2icaxr by=avi-mac (owner) place 5 kept
queue: move id=…2icaxr to=front by=avi-mac (owner) place 5→1 band normal delays=…k3f9qa,…m7c2xz
queue: priority id=…p0bx1c normal→high by=avi-mac (owner) delays=…
queue: aged id=…q4wz8e low→normal after 24h
queue: refused hold id=…k3f9qa by=bob-mac not-allowed (job of avi-mac)
pause: id=…k3f9qa by=avi-mac (hand) for=900s deadline=+15m: frozen in 0.3s; cores 0-7 lent until 14:45
pause: id=…k3f9qa auto for=…u8tz0q (urgent) until +12m10s
resume: id=…k3f9qa (deadline | by avi-mac | beneficiary ended) after 11m52s paused; total paused this attempt 11m52s
queue: order file unreadable: FIFO within each band restored (holds and bands kept)
```

The `by=` attribution has the limits job-layer §14 states (written by code `mu` owns).

**What another workstation sees.** Everything `mu queue` shows, for every job: name, project, owner workstation,
`submitted.host` and `user`, sizes, states, who held, moved or paused it. No command lines, environment or logs in
`queue` (`mu log` of another's job remains possible, as today).

### 20.5 Pause and resume: the mechanism

- **systemd `freeze` / `thaw`.** `systemctl --user freeze mu-job-<id>.service` writes `1` to `cgroup.freeze` of the
  unit's cgroup and waits for the kernel's `frozen 1` in `cgroup.events`; `thaw` writes `0`. The verbs came with
  systemd 246 (2020) on cgroup v2; the node runs systemd 255 (noble). The unit property `FreezerState` reads
  `running`, `freezing`, `frozen` or `thawing`; `ActiveState` stays `active`, so the runner's `unit_ended()` is
  unaffected. `cgroup.freeze` is a core cgroup v2 file, not a controller, so it needs no delegation beyond what the
  user manager has: the user manager can freeze its own units. **Expected, not measured** on the PC's WSL kernel:
  §12 item 10.
- **What the cgroup v2 freezer does.** Every thread of every process in the cgroup (and its children) is stopped at
  its next return to user space and stays stopped until thawed. It is not `SIGSTOP`: no signal is sent, parents see no
  stop or continue through `wait()`, and nothing in the job can notice except by the clock. A thread in an
  uninterruptible kernel wait (`D` state, heavy disk I/O) is frozen when that wait ends, so freezing can take a moment
  (`freezing`). Signals sent to a frozen task stay pending until thaw, **except a fatal signal: `SIGKILL` still ends a
  frozen task** (that is what makes cancel, `oom` and shutdown work, §20.8).
- **Memory, files, network.** Memory stays as it was (anonymous memory cannot leave: `MemorySwapMax=0`; the kernel may
  drop the job's clean page cache under pressure). Open files, file locks, pipes, shared memory and sockets stay open.
  The kernel's TCP stack still answers for a socket (ACKs, keepalive) until its buffer fills; the program reads
  nothing. Peers with their own application-level timeouts may give up during a long pause.
- **Clocks and timers.** `CLOCK_MONOTONIC` and wall time run on while frozen; CPU-time clocks stop. A timer that
  expires during the pause fires at the thaw. A job that measures elapsed time sees the pause in it.
- **MPI ranks.** All ranks of a job and their launcher (`mpirun`, its PMIx server) are in the job's cgroup and are
  frozen together; one rank frozen a few milliseconds before another only spins on shared memory until frozen too. On
  one node Open MPI keeps no heartbeat between ranks by default. §12 item 12 checks an `mpirun -np 4` job through a
  20 s pause.
- **Who freezes.** Only the runner, in `step()` under the queue lock, as only the runner starts units. The `pause` and
  `resume` verbs record the request (§20.8) and wait for the runner's next pass (≤ 1 s) to apply it. The runner calls
  `systemctl --user freeze` with a 30 s limit and confirms `FreezerState=frozen`; **the cores are lent only after
  that**. If the job is still `freezing` after 30 s, the runner thaws it, the pause fails (`pause-failed`, the job runs
  on) and the answer says so. A thaw is confirmed the same way.

**Why freezing is safe for μ's solvers, and when it is not.**

| Job | Safe to pause? | Why |
|---|---|---|
| CalculiX (`ccx`, OpenMP/pthreads), Gmsh, numpy/scipy, a Python sweep without timeouts | yes | pure computation and file I/O; no internal deadlines |
| OpenFOAM with `mpirun` on the node | yes, expected (§12 item 12) | all ranks frozen together; `writeControl clockTime` and `cpuTime` function objects only shift when files are written |
| anything wrapped in a wall-clock limit: `timeout 600 ccx …`, `subprocess.run(timeout=…)`, `future.result(timeout=…)`, a watchdog thread | **no**, or only with limits longer than run time plus pause | the timer runs while frozen and fires at the thaw: the job kills its own child |
| a job that uses the network (downloads, a licence server, a remote database) | **no** | the peer may drop the connection during the pause |
| benchmarks and timing runs (`mu bench`, the scaling series, JATAYU `perf/`) | **no** | the measured time would include the pause; their records would be wrong |
| MPI across several machines | not applicable (one node) | freezing one node's ranks would trip the others' timeouts |

μ's own shim (`mu node run-job`) is in the cgroup and has no wall-clock deadline (it waits for its child and forwards
signals); the build checks that it stays so. `result.json` writes are sealed and renamed, so a freeze in the middle of
one leaves the old file or none, never a torn one.

### 20.6 Accounting while paused: memory, cores, time

- **Memory: still held, strictly.** A paused job keeps its whole memory grant in the allocation (Σ memory grants ≤
  `M` counts it). Nothing is lent: thawing must never meet a node whose memory was given away.
- **Cores: lent until the deadline, never taken back by force.** Every pause has a **deadline** `D` (the hand pause's
  `--for`, or the auto-pause's length). While the job is frozen its CPU ids are **lent**: another job may be started on
  them only if its own end bound is before `D` (`now + t + MARGIN ≤ D`, the backfill rule of §3.3 with `D` in place of
  `S`); such a job is a **borrower** and its grant records `borrowed: {from, cpus, until}`. So at `D` the cores are free
  again by construction.
- **Resume waits for the borrowers; it does not preempt them.** `mu resume` before `D` thaws the job at once if no
  borrower is on its cores, else as soon as the last borrower ends, which is before `D`; the answer says `resuming`
  with `resumes_by`. Chosen over preempting (freezing or stopping the borrower) because that would cascade (a borrower
  pausing for its lender, its own borrowers…), and over thawing onto shared CPUs because the CPU-set invariant (G7)
  would no longer hold. The wait is bounded by `D` and is usually zero.
- **Valve.** A borrower that outlives its time limit (a unit that will not stop) cannot keep a job frozen: at `D` + 60 s
  the runner thaws the lender anyway, both share those CPUs until the borrower is gone, and `events.log` says so (the
  same valve as §3.3 step 6).
- **A borrower is never paused**, by hand or by policy (`pause-limit`, "it runs on cores lent by … until …"), and
  never lends: its RuntimeMaxSec is its time exactly.
- **Time limit: paused time does not count.** systemd's `RuntimeMaxSec` counts wall time since the unit became active,
  frozen time included (to be confirmed, §12 item 11), and systemd's documentation lists `set-property` for resource
  settings, not `RuntimeMaxSec` (also §12 item 11). So:
  - every ordinary job that may be paused gets `RuntimeMaxSec = t + budget` as a **backstop**, where `budget` (the most
    one attempt may be paused in total) = min(`sched.pause.budget_s`, default 4 h, `t`);
  - **the runner enforces `t` itself, on running time**: run time = (now − `active_mono`) − paused time. When it
    reaches `t`, the runner writes `stopping: "timeout"` into `state.json`, then stops the unit; `classify` reads that
    field and writes `timeout` ("ran 2h, its --time, not counting 14m paused"). systemd's backstop fires only if the
    runner is down for longer than the budget;
  - express jobs and borrowers are never paused and keep `RuntimeMaxSec = t`;
  - if item 11 shows `RuntimeMaxSec` can be changed at runtime, μ extends it by each pause instead, and the runner's
    own timeout is dropped.
- **End bounds** (§3.2) of a running job become `active_mono + t + paused time + SLACK`; of a paused job,
  `D + (t − run time) + SLACK`: so a waiting head sees a paused job's cores come back at its resume plus its remaining
  run, and its `starts_by` moves by the pause (G1).
- **Records.** `result.json` keeps `wall_s` (started to ended) and adds `run_s` (wall minus paused), `paused_s` and
  `pauses` (each `{from, to, by, why: "hand"|"auto", for_job}`, at most 20, then a count). `usage.cpu_s` needs no
  change (a frozen job uses no CPU). The dashboard's comparisons (dashboard §6) use `run_s` and flag any job with
  `paused_s > 0`.
- **Limits** (`node.json` `sched.pause`, each optional): `max_s` 2 h per hand pause; `budget_s` 4 h per attempt (and
  never more than the job's own `t`); `auto_max_s` 30 min per auto-pause; `auto_count` 2 per attempt; `auto_cooldown_s`
  15 min of running between two auto-pauses. A pause that would exceed one is refused `pause-limit` with the figures.
  A second `mu pause` of a paused job sets a new deadline from now, within the same limits.
- **The node stays "busy"** (`state/busy`) while a job is paused, as while it runs, so a version switch never meets a
  frozen unit (§9). A queue of **held jobs only** is not busy: holds can last days and must not stop auto-update; held
  jobs survive the runner's restart (they are on disk).

### 20.7 Auto-pause: the policy for urgent jobs

**Not for `--quick` jobs.** They never wait for cores (the lane borrows CPU time, §3.4), and freezing a 20-core
sweep for a 1-core job would idle 19 cores. Auto-pause is for jobs **in the `urgent` band** that need more than the
lane gives.

On a pass where the head `H` does not fit (§3.3 step 4), the runner pauses running jobs for it when **all** hold:

1. the node's policy allows it: `node.json` `sched.auto_pause` = `"marked"` (default: only jobs submitted
   `--pausable`), `"any"` (any ordinary job), or `"off"`;
2. `H` is `urgent`, not held, and has an explicit `--time` with `t + MARGIN ≤ auto_max_s` (30 min);
3. **`H`'s memory fits now** without counting any victim's (paused jobs keep theirs, §20.6);
4. a set of **eligible** running jobs frees enough cores: ordinary (not express, not a borrower, not `urgent`), not
   already paused, pausable under the policy, fewer than `auto_count` auto-pauses this attempt, at least
   `auto_cooldown_s` of running since its last thaw, and its pause budget not spent;
5. `H` cannot start by `extra`, by backfill or on cores already lent.

**Which jobs.** The smallest eligible set by (number of jobs, then cores beyond the need), ties broken by the latest
`active_mono`, then job id: deterministic, testable. Each is paused with `why: "auto"`, `for_job: H` and `D = now +
t_H + MARGIN + 30 s` (the freeze time). `H` starts on the next pass, on the lent cores (it ends before `D`). **An
auto-pause ends as soon as its reason does**: when `H` ends, or leaves the queue (cancelled, held, moved behind
another job), or fails to start, the victims are resumed on that pass; otherwise at `D`.

**Why it is opt-in.** Freezing is transparent to the solvers μ runs (§20.5), but not to jobs with their own
wall-clock timeouts, network peers or timing measurements. Only the submitter knows which it is, so the default asks
the submitter (`--pausable`), and §20.11 tells Claude when to mark a job. The owner can widen it to `"any"` for the
node without a release.

### 20.8 Durability of the new state, and races

All new state is in the job layer's sealed records (`write_durable`, `seal`; a record that fails its check is "not
written", job-layer §9.2), written under `/srv/mu/lock`.

| State | Where | Written by | If lost or unreadable |
|---|---|---|---|
| held | `state.json` `state: "held"`, `held: {by, at}` (a held job is not `queued`, so the previous release's runner ignores it: §20.12) | the verb | `rebuild_state()` (which takes a job with no start in `events.log` as queued) also reads the last `held by …` / `released by …` line of `events.log`, which the verb writes first, so a lost `state.json` never starts a held job |
| band | `state.json` `band`; `job.json` `priority` is what was asked at submit | the verb, aging | `job.json` `priority`, else `normal` |
| order | `/srv/mu/queue.json`: `{"schema": 1, "rev": n, "order": [job ids], "updated": …}`, replaced whole | `move`, `priority` (the moved job's place), aging | Unreadable or missing: the order is (band, re-queued first, `accepted_ns`): FIFO inside each band, holds and bands intact; journalled once; `hello.queue.order_restored_at` set. Ids of jobs neither queued nor held are ignored and dropped at the next write (a held job keeps its entry, hence its place) |
| pause request | `jobs/<id>/control.json`: `{"op": "pause"\|"resume", "for_s", "by", "at"}` | the verb | none pending: the verb's caller sees no `paused` and retries (both operations are idempotent) |
| pause | `state.json` `pause: {phase: "freezing"\|"frozen"\|"thawing", since_mono, deadline_mono, boot_id, by, why, for_job}`, `paused_total_s`, `pause_count`, `auto_pause_count`, `last_thaw_mono`, `stopping` | the runner | `rebuild_state()` rebuilds a running job without a grant (it holds the node, §3.2); the runner reads `FreezerState` and thaws a frozen unit it has no record for |
| lending | the borrower's grant `borrowed: {from, cpus, until_mono}` | the runner, at start | the allocation is rebuilt from grants each pass (§3.2) |

**Write order.** A cross-band move writes the job's `state.json` (band) first, then `queue.json`: a crash in between
leaves the job in its new band at its old place or by `accepted_ns`, a valid order; the client, having no answer,
retries, and every operation is idempotent (`hold` of a held job, `move` to where it already is: `changed: false`). A
pause is recorded as `phase: "freezing"` **before** `systemctl freeze` and as `frozen` after it is confirmed; the
cores are lent only from `frozen`. A thaw is recorded `thawing`, then the unit is thawed, then `pause` is moved into
the attempt's history.

**Reconciling with systemd, every pass.** systemd's `FreezerState` wins: `phase` `freezing` but the unit running (the
runner died before the freeze) → the freeze is done now if `D` is still ahead, else the pause is dropped; `frozen`
but the unit running (thawed outside μ) → the pause ends, `events.log` says "thawed outside μ"; no `pause` but the unit
frozen → thawed.

| Event | What happens | Test |
|---|---|---|
| Runner restart while a job is paused | The unit stays frozen (systemd keeps the cgroup frozen); the runner adopts it with its `pause`, deadline on the monotonic clock with `boot_id`; thaws at `D` as planned | Q32 |
| Windows restart, VM stop, `wsl --shutdown` while paused | The unit is gone, the boot id changes: the interrupted rule (§9) re-runs it as attempt 2 at the front of its band, pause dropped, `note` "was paused when the node stopped" | Q33 |
| Cancel of a paused job | `verb_cancel` and the runner **thaw first, then stop** (so the job gets its `SIGTERM` and can clean up; systemd's own handling of stopping a frozen unit is not relied on). `SIGKILL` after `TimeoutStopSec=5` ends frozen tasks anyway. The grant is freed when the unit has ended (§9) | Q34 |
| `oom` while paused | A frozen job cannot allocate, so it cannot reach its own `MemoryMax`. The kernel's global OOM killer can still kill a frozen process (`SIGKILL` works); the unit ends `oom` and the result's `note` says "while paused". Its borrowers are untouched | — |
| Pause while the job finishes | The runner classifies a unit that ended before it freezes anything; the request is answered `already-finished` with the result's state. A unit frozen just after the shim wrote `result.json` is thawed at once (**a job with a result is never kept frozen**) | Q31 |
| Resume after the unit ended | `changed: false`, the job's final state, exit 0 | Q31 |
| Move, hold or priority while the job starts | The verb and `step()` take the same lock; the verb finds the job `running` and answers `not-queued` with `next: mu pause JOB` (or nothing to do). The start is never undone, never doubled | Q31 |
| Release of a held job while the runner walks | Same lock: it joins the next pass | Q22 |
| Cancel of a held job | `verb_cancel` and the runner treat `held` as they treat `queued`: `cancelled` at once ("cancelled while held") | Q22 |
| The verb waits for the lock | The runner can hold it through a checkout (`prepare_work`, seconds to minutes). Verbs wait up to 120 s, then answer `busy` (exit 6, "retry in a minute"); nothing was changed | unit |

### 20.9 Answers, JSON shapes, exit codes, wording

**`mu queue --json`** (the node's `queue` answer, plus `this_project` per job added by the client):

```json
{"ok": true, "node": "office-pc", "rev": 1841, "auto_pause": "marked", "owners": ["avi-mac"], "you_are_owner": true,
 "capacity": {"cores": 20, "mem_bytes": 21474836480},
 "allocated": {"cores": 16, "mem_bytes": 17179869184, "lent_cores": 8},
 "jobs": [
  {"job_id": "20261114-120102-k3f9qa", "name": "cfd-sweep", "project": "jatayu", "project_id": "jatayu-0123456789ab",
   "key": "avi-mac", "submitted": {"host": "avi-mac", "user": "avi"}, "this_project": false,
   "state": "running", "band": "normal", "pausable": true,
   "request": {"cores": 8, "mem_bytes": 12884901888, "time_s": 21600},
   "grant": {"cores": 8, "cpus": "0-7", "mem_bytes": 12884901888, "class": "normal"},
   "paused": {"since": "2026-11-14T14:30:05Z", "until": "2026-11-14T14:45:05Z", "by": "avi-mac", "why": "hand",
              "lent_to": ["20261114-142955-2icaxr"]},
   "time": {"limit_s": 21600, "run_s": 5120, "paused_s": 310}, "ends_by": "2026-11-14T19:12:00Z"},
  {"job_id": "20261114-142955-2icaxr", "name": "sched-probe", "state": "running", "band": "normal",
   "grant": {"cores": 4, "cpus": "0-3", "mem_bytes": 2147483648, "class": "borrowing",
             "borrowed": {"from": "20261114-120102-k3f9qa", "until": "2026-11-14T14:45:05Z"}}, "…": "…"},
  {"job_id": "20261114-123000-m7c2xz", "name": "frame-fea-v2", "state": "queued", "band": "normal", "place": 1,
   "held": false, "waiting_for": {"resources": {"cores": 12, "mem_bytes": 17179869184}},
   "starts_by": "2026-11-14T19:12:00Z", "…": "…"},
  {"job_id": "20261114-123500-q4wz8e", "name": "mesh-study", "state": "held", "band": "normal", "place": 2,
   "held": {"by": "avi-mac", "at": "2026-11-14T13:02:00Z"}, "waiting_for": {"held": true}, "starts_by": null}]}
```

`place` counts held jobs too (a held job keeps its number); `position` in `status` and `list` stays the place among
jobs that can start (old readers keep their meaning). Times shown to people are wall-clock at answer time, from the
monotonic bounds (§7.1).

**An arranging answer** (`hold`, `release`, `move`, `priority`):

```json
{"ok": true, "exit": 0, "job_id": "20261114-161025-2icaxr", "op": "move", "changed": true,
 "state": "queued", "band": "normal", "place": 1, "was": {"place": 5, "band": "normal"},
 "higher_bands_ahead": 0, "starts_by": null, "waiting_for": {"resources": {"cores": 20}},
 "delays": [{"job_id": "20261114-123000-m7c2xz", "name": "frame-fea-v2", "project": "jatayu", "key": "avi-mac",
             "starts_by_was": "2026-11-14T19:12:00Z", "starts_by": null}],
 "next": "mu queue --json",
 "steps": [{"cmd": "mu queue --json", "where": "here", "by": "agent", "why": "the whole node's queue now"}]}
```

**A pause answer** adds `paused` (`since`, `until`, `frozen: true`), `lent: {cores, cpus, until}`,
`memory_kept_bytes`, `time: {limit_s, run_s, paused_s, budget_left_s}` and `delays`; `next` is "it resumes by
itself at 14:45; to resume sooner: mu resume JOB".

**Exit codes** (the client's, `proto.py`; the node's in brackets):

| Exit | `error` | When | `next` |
|---|---|---|---|
| 0 | — | done, or nothing to change (`changed: false`) | `mu queue --json` |
| 2 | `other-project` | the client's guard (§20.2) | "only if the owner asked: the same command with --others" (`by: ask`) |
| 2 | `not-queued`, `not-running`, `reference-not-queued`, `already-finished` [64] | the job (or `--before` job) is not in the state the operation needs; mapped by error code, as `too-big` | the operation that fits its state (`mu pause JOB` for a running one, `mu hold JOB` for a queued one, `mu move JOB --front`) |
| 2 | `pause-limit` [64] | over `max_s` or the budget, an express job, a borrower | the figures and when it may be paused again |
| 5 | `not-allowed` [77, new] | this key may not arrange that job (§20.4) | "ask the owner (avi-mac) to run: mu …" (`by: human`) |
| 6 | `node-too-old` (client), `pause-unavailable`, `pause-failed`, `busy` [69] | the node lacks the feature; its freezer does not work (§12 item 10); the freeze did not complete in 30 s; the lock was not free in 120 s | wait for the node's auto-update (`mu compute status NODE`); nothing; retry; retry in a minute |
| 9 | `no-such-job` [66] | | `mu queue` |
| 10 | — | `pause`/`resume` accepted but not complete after `--timeout`: `state` `pausing` or `resuming`, with `by` | `mu queue --json`; the request stays in force |

Node exit 77 (`EX_NOPERM`) is new in job-layer §7.2's table; the client maps it to 5 ("refused, never retry").

**Text** (`mu queue`, built in `jobs.py` from the JSON):

```
office-pc · 20 cores, 20.0 GB for jobs · 16 cores, 16.0 GB granted (8 cores lent) · auto-pause: marked jobs only
RUNNING
  k3f9qa  cfd-sweep     jatayu        avi-mac  8 cores 12.0 GB  PAUSED by avi-mac until 14:45 · 1h25m run, 4h35m left · pausable
  2icaxr  sched-probe   mu-first-job  avi-mac  4 cores  2.0 GB  on 4 cores lent by k3f9qa · ends by 14:41
  x9d1ab  quick-check   jatayu        avi-mac  express · 1 CPU (quota)
QUEUED, in the order they start
  1 normal  m7c2xz  frame-fea-v2  jatayu  avi-mac  waiting for 12 cores / 16 GB · starts by 19:12
  2 normal  q4wz8e  mesh-study    jatayu  avi-mac  HELD by avi-mac 13:02 (keeps place 2) · mu release q4wz8e
next: mu queue --json
```

### 20.10 What the dashboard shows

Dashboard phases 1–3 are **read-only** (ROADMAP §2.3 Q3; dashboard §4.6): the page shows the queue and gives the
command, never a button that changes it.

- **Per node**: the queue in walk order, with band chips (`urgent`, `high`, `low`; `normal` unmarked), `held` and
  `paused` badges (with "until 14:45" and who), lent and borrowed cores drawn on the cores bar (§10) as hatched spans
  with the deadline, `auto-pause: marked` and the owners.
- **Per job**: who held, moved or paused it and when (from `events.log`), `run_s` beside `wall_s` and `paused_s`.
- **Copy-the-command buttons** only (dashboard §4.7): `mu release JOB`, `mu resume JOB`, `mu move JOB --front`,
  `mu queue`. The server's read-only verb allowlist (dashboard §4.6) gains `queue` and nothing else.
- Real buttons, if ever, come with phase 4 under the same rules as its optional cancel button (token, Origin check, a
  confirmation naming the job, node and state, the same code as the `mu` command), and only on the owner's decision.
- Dashboard phase 1b reads `queue` (or `list` plus `rev`) and the shapes above; its golden fixtures (D40) gain a
  `queue` scenario with held, paused and borrowing jobs.

### 20.11 Rules for Claude (added to §8 and `CLAUDE-toolchain.md`)

- **Look before you wait:** `mu queue --json` shows the whole node. Read `waiting_for` and `starts_by` there before
  deciding anything.
- **Small checks: `--quick`.** They start at once; nobody needs to move, hold or pause anything for them.
- **Your own project's queued jobs** you may hold, release and move among themselves freely (e.g. run the decisive
  case of a sweep first: `mu move JOB --front`). Never cancel and resubmit to reorder: that loses the job's place and
  its id; `mu move` keeps both.
- **Other projects' jobs** (another Claude session's work) — holding, moving or pausing them, or moving your job ahead
  of them — **only when the owner asked**, with `--others`, and say in your reply what you changed. `priority high`
  and `urgent`, and every `mu pause`, likewise only when the owner asked.
- **Mark long solver runs `--pausable`** when they use no network, no wall-clock timeouts of their own (`timeout`,
  `subprocess` timeouts) and are not timing measurements. Never mark benchmarks, `perf/` runs or the scaling series.
- **`not-allowed` (exit 5)**: another workstation's job; relay the `next` line to the owner, do not retry.
- A paused job's result carries `paused_s`; use `run_s` for timings, never `wall_s`.

### 20.12 Compatibility (added to §11)

Protocol stays 1. `hello.features` gains `queue-control` (the order, holds, bands, `queue`) and, in the second release
(§20.13), `pause`; `hello` gains `owners`, `you_are_owner`, `auto_pause` and `queue: {…, held, paused}`.

| Workstation | Node | Behaviour |
|---|---|---|
| new | without `queue-control` | `mu queue` falls back to `list` without `project=` (all jobs, no order or bands) and says so; the arranging commands exit 6 `node-too-old` with "wait for office-pc's auto-update (μ ≥ v…)"; `--priority`, `--hold`, `--pausable` are refused before the upload, not sent and ignored |
| new | with `queue-control`, without `pause` | `mu pause`/`resume` exit 6 `node-too-old`; `--pausable` is recorded and takes effect once the node has `pause` |
| old | new | No new commands. Its `mu status` shows a held job's state `held` (an unknown word it prints as is) and a paused job as `running` with the `note` "paused until 14:45 by avi-mac"; `--wait` keeps waiting, which is right |
| either | rolled back to a release without queue control | The old runner takes only `state: "queued"` jobs: **held jobs stay held** (ignored, not started) until a roll-forward; bands and `queue.json` are ignored (FIFO). A paused job keeps the node busy, so the runner never switches versions under it (§20.6); a manual rollback while a job is paused would leave it frozen until the backstop: **resume paused jobs before rolling back** (the README's rollback note says so) |

### 20.13 Effort, and place in step 2.5

| Piece | Part | Lines (approx.) | Days |
|---|---|---|---|
| The walk (bands, `queue.json`, re-queued first, aging), `hold`/`release`/`move`/`priority`/`queue` verbs, owners and the authorisation table, audit lines, `state: "held"` with its `rebuild_state` path, `--priority`/`--hold` at submit | C | 230 in `node.py` | 1½ |
| `mu queue` (text and JSON), the five arranging commands, `--others`, exit codes and `next`, compatibility paths | C | 200 in `jobs.py`, `proto.py` | 1 |
| Tests: walk and authorisation unit tests, simulation additions, Q22–Q28 | C | 250 | 1–1½ |
| Pause/resume by hand: `control.json`, freeze/thaw in `step()` with confirmation and reconciliation, lent cores and borrowers in `plan()`, deadlines, the runner's own time limit and the `RuntimeMaxSec` backstop, `run_s`/`paused_s`/`pauses`, cancel/oom/restart/interrupted paths, `pause`/`resume` commands | D | 260 in `node.py`, 80 in `jobs.py` | 2½ |
| The auto-pause policy (`--pausable`, `sched.auto_pause`, victim choice, limits, early end) | D | 90 | 1 |
| Tests: Q29–Q37, simulation with pauses, borrowers and auto-pauses | D | 300 | 1½–2 |
| **Total** | | **~860 + ~550 tests** | **8½–10 days** |

**Place in ROADMAP step 2.5.** Part C (queue order and view) is cheap, needs nothing new on the PC, and fixes the
owner's case on its own together with `--quick`: it ships **in the first release with parts A and B** (2.5.3). Part D
(pause and auto-pause) needs probe items 10–13 on the PC first and changes how time limits are enforced, so it is a
**second release** (2.5.5), after the first has run real JATAYU work on the PC for some days. Step 2.5 in all grows
from 7–9 days to about 16–19.
