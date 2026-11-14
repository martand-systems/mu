# μ dashboard: a local view of jobs, machines and progress

**Status: proposal, revised after review, nothing built.** Written on branch `dashboard-research` from v2026.11.8
(2026-11-14). It merges three research notes (A: architecture and data, read from the code and the live
`~/Library/Mu`; B: build or adopt; C: what the screens should be), then takes in a design review (§11 lists each
finding and what changed). Where the notes disagreed, §2.2 says which way this proposal goes and why. Every claim about
μ's code was checked against `src/mu/*.py`, `bin/mu`, `bin/mu-toolchain` and `install/hosts/wsl2/mu-node-gate`.
Nothing here has run on the office PC yet; §7.0 lists what must be measured there first, and phase 1 does not start
until it has.

Sections: [1 Goal](#1-goal-and-non-goals) · [2 Decisions](#2-decisions) · [3 Build or adopt](#3-build-or-adopt-plan-17) ·
[4 Architecture](#4-architecture) · [5 Screens](#5-screens) · [6 Performance comparison](#6-performance-comparison-mac-vs-node) ·
[7 Phases and tests](#7-phases-acceptance-tests-and-effort) · [8 Risks](#8-risks) · [9 Questions](#9-questions-for-the-owner) ·
[10 Read](#10-read-in-sources) · [11 Review](#11-review)

## 1. Goal and non-goals

**Goal.** While μ is installed and switched on, a page at `http://127.0.0.1:<port>/` on the workstation always answers
and shows:

- every job: its state, the compute node it went to, its place in the queue, its log as it grows, and its CPU and
  memory over its whole run;
- live CPU and memory of the workstation and of each compute node, each value with its age;
- later: history, progress, results, and how the same task ran on the Mac and on the node.

It is a **run viewer first and a monitor second**. CPU graphs are a commodity. What no off-the-shelf tool shows is
"this job, from this snapshot and toolchain pin, on this machine, with its log, progress, resources and results, next
to the same job on the other machine".

**Non-goals.**

- **Not a second way to drive μ.** The terminal and Claude Code stay the interface. The page is read-only in the first
  versions. Where a user would act, it offers to copy the `mu` command. It never fetches outputs: a fetch overwrites
  files Claude may be editing and writes a run record that gets committed.
- **Not the only place the data lives.** Every figure the page shows about a node also comes out of
  `mu compute status <node> --json` (§4.7), so Claude sees what the owner sees.
- **Not a source of truth.** It reads μ's records and the nodes' answers. Its own cache can be deleted at any time.
- **Nothing depends on it.** With the dashboard off, crashed or its port taken, every `mu` command behaves exactly as
  today.
- **Not reachable off the machine.** No LAN access, no remote viewing, no accounts. Each workstation runs its own.
- **No new path into the node or the workstation.** No listener on the node, no port forwarding, no reverse tunnel,
  no SaaS.
- **Not Windows' view of the PC** unless the owner decides otherwise (§9 Q2). The node figures are the WSL2 VM's.

## 2. Decisions

### 2.1 In one page

1. **Build it, small, on stdlib Python.** `mu dashboard serve` runs a `ThreadingHTTPServer` on the default toolchain's
   Python (`-I -S`, like every job-layer command). Static HTML/JS/CSS with no build step. One vendored, hash-pinned
   chart library (uPlot, MIT). No CDN. Adopted: SQLite (stdlib, phase 2), uPlot, and psutil on the Mac (already
   hash-pinned at 7.2.2 in both platforms' `env.pip.txt`).
2. **The node measures itself; the Mac pulls.**
   - From phase 1, the node's runner samples the running job's cgroup and the machine's `/proc` files every 5 s, keeps
     `metrics/now.json`, and appends the job's series to `jobs/<id>/metrics.jsonl`.
   - New **read-only verbs** (`stats`, `metrics`, `watch` in phase 1; `events`, `bench`, `progress` later) serve those
     files over the existing ssh session. `stats` is a file read.
   - Job curves have no gaps when the Mac sleeps or the link drops, from the first version, because the node kept
     sampling.
3. **No root step and no protocol bump.** The gate only checks the *shape* of a request and hands any
   `verb key=value…` to `mu node serve`, which owns the verb table (`mu-node-gate`, last line). New verbs need only
   `node.py` and arrive with an ordinary release through the node's auto-update. `hello` gains `features: [...]`. An
   older node answers `unknown-verb`, and the dashboard falls back to `hello` + `list`.
4. **μ starts recording usage.** `result.json` and the run record gain `usage` (CPU time, peak memory, mean cores).
   The job shim reads its own live cgroup; for results the runner writes (`oom`, `timeout`, `interrupted`) the runner
   uses its last samples, because systemd removes the cgroup when the job's last process exits (§4.2).
5. **Lifecycle: a plain LaunchAgent.** `mu dashboard on` installs `systems.martand.mu.dashboard` (`RunAtLoad`,
   `KeepAlive`), like the auto-update agent. The URL always answers and the server's environment is fixed, never
   inherited from whichever command or sandbox happened to start it. An idle server blocked in `accept()` costs no CPU.
   Samplers and node sessions run only while a tab is visible or a job is unfinished. If the owner prefers no
   always-on process, `mu dashboard open` alone starts it (§9 Q1).
6. **Its own ssh master.** The dashboard uses a ControlPath of its own per node and never closes the CLI's, so a slow
   dashboard call can never end Claude's in-flight `mu submit`, `mu fetch` or `mu log -f` (§4.3).
7. **Streams, not polling, toward the node.** One `watch` session per node while a tab is visible, `log follow=1` for
   the open log, and `list` only when the node's `rev` moves. The browser polls the local server every 2 s.
8. **Security.**
   - Binds 127.0.0.1 only and checks the `Host` header.
   - Every API call needs an `X-Mu-Token` header. The token is new at each server start, lives in a 0600 file and in
     the page's `localStorage`, never in a cookie, argv, a log or Claude-visible output.
   - Strict CSP. Every node string is rendered as text only.
   - The server reaches nodes only through one wrapper that refuses any verb outside a fixed read-only allowlist.

### 2.2 Where the research disagreed

| Point | A | B | C | Here | Why |
|---|---|---|---|---|---|
| Does a new node verb need a root-owned gate change? | no | yes | yes | **no** | Checked: `mu-node-gate` has no verb list and ends with `exec "$MU_BIN" node serve "--key=$name" "$v" "$@"`; `node.py` `VERBS` and `ARG_RULES` decide. No change to `mu-wsl2-distro.sh` |
| Mac statistics | psutil | `vm_stat`/`sysctl`/`top` | either | **psutil, with a stdlib fallback** | psutil 7.2.2 is already in both pinned envs, so it is not a new dependency. A measured a 23 ms import (site-packages added by path, no `.pth`) and 5 ms per sample. `top -l 1` costs about a second per sample. Fallback: `os.getloadavg`, `sysctl`, `vm_stat` |
| Token transport | header + `localStorage` | HttpOnly cookie | cookie | **header + `localStorage`, rotated per server start** | Cookies are shared by every port of 127.0.0.1, so any other local server would receive the token. `localStorage` is per origin, and the origin includes the port. Rotation limits what a page served by a port squatter could read (§4.6) |
| History store | SQLite | JSONL | SQLite | **JSONL on the node (phase 1); SQLite cache on the Mac (phase 2)** | The node appends cheaply and serves by offset, like `log`. The Mac needs windowed queries, joins and rollups only once it keeps history |
| Start | launchd socket activation | explicit `mu dashboard` | lazy start on `mu submit` | **a plain LaunchAgent; `mu dashboard open` as the only alternative** | Review F5: a spawn from `mu submit` inherits Claude Code's environment and sandbox, and misses local work; socket activation solves an idle cost that does not exist |
| First version touches the node? | yes | yes (`stats`) | no | **yes: usage, the sampler, three verbs** | Without node statistics the first version misses half of the request; without the sampler, `oom` and `timeout` jobs lose their peak memory (§4.2) |

## 3. Build or adopt (PLAN §17)

The constraint that decides: the node's only door is ssh inside the chisel tunnel, with the gate as ForceCommand,
`restrict`, `AllowTcpForwarding no` and no shell (job-layer §7.1, §13.1, §14). That rules out every monitor that pulls
from a port on the node. A monitor whose agent dials out to a hub needs the node to reach the workstation, which is a
new inbound path and a change to the security model. None of the candidates knows μ's jobs, snapshots, pins or run
records. The transport through the gate must be built whatever the interface is.

**Version numbers below are unverified** unless marked "checked"; they come from the research notes' secondary pages.
Each is checked on the project's release page before anything is pinned, and none goes into the PLAN row.

| Need | Candidates looked at | Fit through the gate, without root | Decision |
|---|---|---|---|
| Node and Mac monitoring with a web UI | **Netdata** v2 (agent GPLv3+; the v2 dashboard is under NCUL1, not open source) | no: port 19999 or streaming to a parent | **Not adopted** |
| | **Glances** web (LGPLv3, psutil + bottle) | no as a web UI (binds 0.0.0.0 by default); `--stdout-json` behind a verb would work but is a third-party program for a few `/proc` reads | **Not adopted** |
| | **Beszel** (MIT; Go agent, no root) | no: SSH mode needs hub→agent port 45876; WebSocket mode needs the node to dial the workstation | **Not adopted now.** Closest fit. Revisit if the relay moves to Headscale (§17 row 1), which would give a real network path |
| | **Prometheus 3 + node_exporter + Grafana** (Grafana AGPLv3) | no for scraping. Grafana would still need μ to serve a JSON API, so μ builds the server anyway | **Not adopted.** Optional later: μ exports `/metrics` text so a team can add Grafana without μ depending on it |
| | Cockpit, cAdvisor, `below`, `atop` | need root or a daemon | Not adopted |
| **Hybrid: μ's collector feeding an adopted viewer** | μ's verbs (built anyway) → a local time-series store (Prometheus, VictoriaMetrics) + **Grafana**; or **Datasette** over μ's SQLite cache | yes: the data arrives through μ's own verbs, nothing new on the node | **Not adopted.** The strongest alternative, kept on record. Against it: hundreds of MB of extra, separately versioned software outside μ's pins; a second login and a second server to keep updated; Grafana shows series well but not a job's log, record and outputs; Datasette browses tables but has no live charts. μ would still build the collector, the transport and the job model, which are most of the work |
| **Unprivileged exporter on the node behind a reverse port** | node_exporter or the Beszel agent as user `mu`, reached through a second reverse port on the relay (chisel `R:`) | no: the tunnel is root-owned (`mu-wsl2-distro.sh`), so adding a reverse port is a root step on the PC; it also lets the relay, and anyone holding its credential, reach a new listener on the node | **Not adopted.** Widens the relay's reach for data μ can serve through the gate. Same decision as §9 Q8 |
| Job and run UI | **Ray** dashboard, **Prefect**, **Dagster**, **Airflow**, **Nomad** UI, Slurm portals, **Open OnDemand** | each replaces μ's job model or needs open ports between machines (§17 already kept μ's queue) | **Not adopted**; layouts borrowed (§5) |
| | **pueue**, a self-hosted **GitHub Actions** runner, **Dozzle** | no web UI; dials SaaS; reads Docker logs only | Not adopted |
| Experiment tracking, run comparison | **MLflow** (Apache-2.0; local `mlflow ui` over a file store) | yes, fed from `runs/*.json` after fetch | **Not now.** About 500 MB, a second source of truth, no live view. The only plausible add-on later, for comparing FEA runs across parameters |
| | Aim, TensorBoard, Sacred + Omniboard, DVC Studio | heavy, MongoDB, or SaaS | Not adopted |
| Web framework | **Streamlit**, **Dash**, **NiceGUI**, **Panel** | yes, but 100+ MB of Python dependencies, websockets per session, hard to keep a strict CSP | **Not adopted**; fine for a throwaway spike only |
| | **htmx**, **Preact + htm** | vendored files | **Not in v1.** Vanilla JS is enough. Consider Preact + htm if the front end passes about 1,500 lines |
| Charts | **uPlot** (MIT, about 45 kB) | vendored | **Adopt** for every time series |
| | Chart.js (MIT), ECharts (Apache-2.0), Plotly.js (MIT, 3 MB+) | vendored | Chart.js or hand-drawn SVG for bars and scatter in phase 4. Plotly is too heavy |
| 3D results | **three.js** (MIT; STLLoader, GLTFLoader), `<model-viewer>`, vtk.js | vendored | three.js in phase 4. `model-viewer` cannot do wireframe or sections. vtk.js only if volume fields are needed |
| Mac statistics | **psutil** | already pinned (7.2.2, checked in `toolchain/*/env.pip.txt`) | **Adopt** |
| Collector, history, server | the above | — | **Build**: the node sampler (about 200 lines, `/proc` + cgroup v2, stdlib), the verbs, the stdlib server, later a SQLite cache |

**Recommendation: build the dashboard on stdlib Python, adopting uPlot, SQLite and the already-pinned psutil.** Row to
add to the PLAN §17 table once the owner accepts (no version numbers):

> | Local dashboard (`mu dashboard`) | Netdata, Glances, Beszel, Prometheus + Grafana, μ collector + Grafana or Datasette, an exporter behind a relay reverse port, Ray/Prefect/Dagster UIs, MLflow, Streamlit/Dash | Build on stdlib Python + vendored uPlot (compared 2026-11-14, `docs/design/dashboard.md` §3): every monitor needs a port on the node or a reverse path the gate forbids, none knows μ's jobs, and the transport through the gate is needed whatever the interface. Revisit Beszel if the relay moves to Headscale; MLflow as a later add-on fed from run records |

## 4. Architecture

### 4.1 Picture

```
 WORKSTATION (Mac)                                               COMPUTE NODE (WSL2 "mu-compute", user mu)
 ┌──────────────────────────────────────────────┐               ┌───────────────────────────────────────────┐
 │ browser tab ── polls /api every 2 s ─────┐   │               │ mu node run (runner, systemd user unit)   │
 │                (X-Mu-Token header)       ▼   │               │  every 5 s, outside the queue lock:        │
 │ mu dashboard serve  (LaunchAgent,            │               │   job cgroup: cpu.stat memory.current/peak │
 │                      127.0.0.1:<port>)       │               │   /proc/stat meminfo loadavg pressure      │
 │  ├ HTTP threads                              │               │  → metrics/now.json   (rev, host, running) │
 │  ├ Mac sampler (psutil), only while needed   │               │  → jobs/<id>/metrics.jsonl                 │
 │  ├ one worker per node ── NodeReader ────────┼──ssh, its own─┼─→ mu-node-gate → mu node serve <verb>      │
 │  │   (allowlist wrapper around jobs.Conn)    │   ControlPath │     stats  = read now.json                 │
 │  │   watch · log follow=1 · list on rev ·    │   inside the  │     watch  = now.json every N s            │
 │  │   metrics offset= · hello · status        │   chisel      │     metrics= metrics.jsonl from offset     │
 │  └ reads: .mu/jobs, runs/, jobs.index,       │   tunnel)     │ mu node run-job (shim, in the job cgroup) │
 │           compute/*/last-status.json         │               │  → result.json + usage{}                   │
 │ mu submit → jobs.index;  mu compute status   │               └───────────────────────────────────────────┘
 │   --json → hello + stats (Claude reads this) │   relay.martand.systems: unchanged; one more WebSocket and
 └──────────────────────────────────────────────┘   ssh master per workstation while the dashboard is active
```

The workstation always opens the session. Inside a session the node may stream (`log follow=1` already does; `watch`
is new), and that is all "push" means here.

### 4.2 On the node

**The sampler (phase 1).** The runner already wakes every second. Every fifth tick, outside the queue lock, it reads
about 10 small files (target under 5 ms, measured in §7.0):

- the running job's cgroup: `cpu.stat` (`usage_usec`, `user_usec`, `system_usec`), `memory.current`, `memory.stat`
  (`anon`, `file`, `shmem`), `memory.peak` if present, `memory.events` (`max`, `oom_kill`), `pids.current`;
- the host: `/proc/stat`, `/proc/meminfo`, `/proc/loadavg`, `/proc/pressure/*` if present, `statvfs` of `/srv/mu`.

It then:

- appends one line to `jobs/<id>/metrics.jsonl` while a job runs:
  `{"t","cpu","anon","file","shmem","cur","peak","max","pids","psi_mem"}` (about 200 B, 1.7 MB for a 12-hour job). It
  stays with the job's folder;
- keeps, per running job, the running maximum of `memory.current` and of `anon + shmem`, the last `cpu.stat` and the
  last `memory.events`. After a runner restart it rebuilds them from the job's `metrics.jsonl`;
- replaces `metrics/now.json` (rename, no fsync: it is a view) with the newest host and job samples, rates computed
  between the last two passes, the queue counts, and a `rev` that increases whenever any job changes state.

Lines are not fsynced. A reader treats a final line without `\n` as not yet written. Losing the last seconds on a
power cut costs nothing.

The runner records the job unit's `cgroup` in `state.json` when the job starts, from
`systemctl --user show mu-job-<id>.service -p ControlGroup`. Its path is never guessed. The sampler is the runner's
only new work; it must never hold the queue lock or slow the 1 s loop (D09).

The node's own host history (`metrics/host-YYYYMMDD.jsonl`, 7 days) is phase 2.

**Usage in the result (phase 1).**

```json
"usage": {"cpu_s": 6662.1, "user_s": 6590.4, "sys_s": 71.7, "cores_avg": 17.9, "mem_peak_bytes": 12020000000,
          "mem_work_peak_bytes": 9810000000, "pids_peak": 44, "mem_events": {"max": 0, "oom_kill": 0},
          "source": "cgroup"}
```

- **Shim-written results** (`succeeded`, `failed`): `mu node run-job` is the job unit's main process and lives in the
  job's cgroup. Just before `write_result` it reads its own cgroup (`/proc/self/cgroup` → `/sys/fs/cgroup/<path>/`),
  which is still there because the shim is in it. `source: "cgroup"`. `mem_work_peak_bytes` comes from the runner's
  samples (via `now.json`), because no kernel file gives a peak of `anon + shmem`.
- **Runner-written results** (`oom`, `timeout`, `interrupted`): systemd removes a unit's cgroup directory when its last
  process exits, even if the unit stays loaded as failed, so `memory.peak` and `cpu.stat` are gone before `classify()`
  runs. Ubuntu 24.04's systemd 255 has no `MemoryPeak` property (added in 256). The runner therefore writes `usage`
  from its last samples: `cpu_s` from the last `cpu.stat` (up to 5 s short), `mem_peak_bytes` from the last
  `memory.peak`, or the running maximum of `memory.current` where `memory.peak` is absent, and `mem_events` from the
  last sample plus the unit's `Result`. `source: "runner-sampled"`, and the page says the figures may be up to 5 s
  old. These are the jobs where peak memory matters most for sizing `--mem`.
- `cores_avg` is `cpu_s / wall_s`. `mem_peak_bytes` counts page cache, as the limit does, and the label says so (§6).
- `mu fetch` copies `usage` into `runs/<id>.json`. `list` and `status` add `usage` for finished jobs.

**`stats` (phase 1).** Returns `metrics/now.json` with its age. It never calls `scan_jobs()` and never runs
`systemctl`. If `now.json` is older than 15 s, the runner is not sampling: the answer says `"stale": true, "age_s": …`
and the page shows the node's figures as stale.

```json
{"ok": true, "t": 1794651234.2, "age_s": 1.8, "stale": false, "node": "office-pc", "rev": 812,
 "host": {"cpu_pct": 93.1, "per_core": [97, 95, …], "load": [18.2, 17.9, 15.0],
          "mem": {"total": 24000000000, "avail": 8100000000, "cached": 2100000000, "shmem": 400000000},
          "psi": {"cpu_some10": 41.2, "mem_some10": 0.0}, "disk": {"free_gb": 89, "min_free_gb": 20}, "uptime_s": 86400},
 "running": [{"job_id": "20261114-101500-k3f9qa", "cpu_cores": 17.8, "anon": 9810000000, "file": 2100000000,
              "shmem": 400000000, "peak": 10300000000, "max": 23622320128, "pids": 44, "log_size": 48211}],
 "queue": {"running": 1, "queued": 1}}
```

**New and changed verbs** (stage-1 grammar; JSON or bytes; exits 0/64/66/70, never 1, 2 or 255):

| Request | Answer | Journal | Phase |
|---|---|---|---|
| `stats` | `now.json` with its age (above) | none on success; refusals and errors as today | 1 |
| `watch interval=<2..60> [max=<s>]` | a `stats` line now and then every interval; the node ends it after `max` (default and cap 600 s); the client reopens | one line at open, one at close | 1 |
| `metrics id=<job> offset=<B> [max=<N>]` | the job's `metrics.jsonl` bytes from `B`, the same code path as `log` | none on success | 1 |
| `hello` | adds `features: ["stats","usage","watch","metrics"]` | as today | 1 |
| `list`, `status` | add `usage` for finished jobs; `list` adds `rev` | as today | 1 |
| `events id=<job>` | the job's `events.log` (start, attempt, result, cancel asked), capped at 64 kB | none on success | 2 |
| `metrics day=<YYYYMMDD> offset=<B>` | host history bytes | none on success | 2 |
| `bench` | the newest `state/bench/*/results.json` of the node's default version, capped | none on success | 2 |
| `progress id=<job> offset=<B>` | JSONL bytes of parsed progress | none on success | 3 |

**Journaling.** Today `serve` journals every call to syslog and to `/srv/mu/audit.log` (`node.py` `journal()`), and
sshd logs each session at `LogLevel VERBOSE`. Nothing rotates `audit.log`. Read-only telemetry polled per call would
bury the submit, cancel and refused lines the log exists for. So the telemetry verbs journal only refusals and errors,
`watch` journals its open and close, and the dashboard's traffic plan (§4.3) keeps sessions few, which also keeps
sshd's root-written lines few. Existing verbs keep their journaling. A rotation rule for `audit.log` is a separate
decision (§9 Q9).

`ARG_RULES` gains `interval` (`^[0-9]{1,2}$`) and, in phase 2, `day` (`^[0-9]{8}$`). The verbs add nothing an attacker
could not already get: a paired key is already code execution as `mu`, and `list` without `project=` already shows
every job on the node. What is new is on the workstation: more node-controlled text reaches a browser (§4.6).

**Still to confirm on the PC** (§7.0): the job unit's `ControlGroup`; that `memory.peak` and `pids.peak` exist (they
need kernel 5.19 or later; the WSL kernel's version is not yet checked); what is left of an OOM-killed unit's cgroup
after the kill; PSI (`CONFIG_PSI`); whether `mu` can read `/proc/<pid>/io` for its own processes (`io.stat` is absent,
because the user manager is delegated only `cpu memory pids`); the CPU cost of one verb call.

### 4.3 Getting node data to the Mac

- **One worker thread per paired node,** each with its own `NodeReader`: a thin wrapper around `jobs.Conn` that
  - refuses, before any ssh call, every verb not in the allowlist `hello list status log stats metrics watch` (plus
    `events bench progress` in later phases). It never sends `submit`, `cancel`, `fetch` or `receive-pack`. The wrapper
    is the only way the server reaches a node, and it is unit-tested on its own (D17);
  - uses **its own ControlPath** per node (`<node>.dash.sock`, placed by the same rule as `control_path()` so it fits
    the socket length limit). When one of its calls times out it closes only that master, never the CLI's. Today
    `Conn.run` with `quick=True` gives up after 12 s on a shared master and calls `close_master()`, which would end a
    concurrent `mu submit` push, `mu fetch` or `mu log -f` (D20). Cost: one more relay WebSocket and one Keychain
    read (by the proxy subprocess) each time the dashboard's master opens. The dashboard does not warm Claude's
    connection.
  - A slow or offline node never blocks the others.
- **Cost of a call.** About 0.9 s on a live master through the relay, about 5 s plus a Keychain read for a new one.
  Each call on the node spawns an sshd session, the gate (`sh`, `awk`, `id`), bash `bin/mu` and Python; the review
  estimated 80–150 ms of CPU per call, not measured. §7.0 measures it, which is why the plan below avoids per-second
  calls.
- **Traffic.**

| Situation | Node traffic |
|---|---|
| A tab is visible | one `watch interval=2` session per node (live host and job figures, and `rev`); `list limit=100` when `rev` moves, at most every 5 s, and every 5 min as a backstop; `log id= follow=1` while a job's log is open (one session); `metrics id= offset=` once when a job's detail opens, then the live points come from `watch` |
| No visible tab, a job unfinished, on mains power | `list limit=100` every 60 s, enough to notice the end |
| No visible tab, a job unfinished, on battery (psutil `sensors_battery`) | `list` every 5 min; the master is allowed to expire (`ControlPersist 300`), so no 15 s keepalives run for the whole job |
| Nothing unfinished, no tab | nothing; the master expires; the server sits in `accept()` |

  "A tab is visible" means an `/api` request in the last 10 s; the page polls only while
  `document.visibilityState` is `visible`.

- **`list` limit.** `verb_list` returns at most `limit` finished jobs (default 20). The dashboard sends `limit=100`.
  When the node has more, the job table ends with "older jobs: `mu status`", and the day's totals are marked "at
  least" if the window does not reach back to midnight.
- **Errors use the CLI's classes.**
  - Exit 4 (relay unreachable, node offline, link lost): grey "offline since …", retry after 5, 10, 20, then 60 s.
  - Exit 5 (host key, key revoked, relay credential, protocol): red, the CLI's message, **no automatic retry**. There
    is a "retry" button.
  - Exit 6: amber.
  - A locked Keychain: "Keychain locked", back off to 5 min.
- **Reconcile** on start, after a wake (wall clock jumped more than 3× the interval against `time.monotonic()`) and
  after a reconnect:
  1. close and reopen the dashboard's own master (after sleep it is very likely dead);
  2. `stats`;
  3. `list` (no project filter);
  4. upsert the jobs;
  5. `metrics` from each stored offset for jobs whose detail is open.
- **Clocks.** Node samples are stored in node time. The offset is estimated from `hello.time` against the call's
  midpoint, and shown when it exceeds 2 s.

### 4.4 The local server and its lifecycle

- **Process.** `mu dashboard serve` → `cli.py` → `src/mu/dashboard.py` on the default version, like the other
  job-layer commands. It adds the env's `site-packages` to `sys.path` by hand to import psutil, so no `.pth` file
  runs.
  - Threads: HTTP, one Mac sampler, one worker per node. The sampler and workers run only while a tab is visible or a
    job is unfinished (§4.3).
  - Phase 1 keeps the Mac's and the nodes' host figures in memory (ring buffers, 1 h). Job series come from the node's
    `metrics.jsonl`, so they are complete from the first version.
- **Started by launchd.** `mu dashboard on` writes and loads `~/Library/LaunchAgents/systems.martand.mu.dashboard.plist`
  (`RunAtLoad`, `KeepAlive`, `MU_HOME`, `MU_CACHE`, `PATH`, log to `logs/dashboard.log`, capped), in the same style as
  the auto-update agent in `bin/mu`. The environment is the agent's, the same every time. The server exits (and launchd
  restarts it) when `$MU_HOME/default` has changed and nothing is being watched, so a new release takes over. It never
  holds `state/busy` and never prevents sleep.
- **Not started by** `mu submit`, `mu status`, `mu run` or the dispatcher. No command waits for it or connects to it.
- **Port.** Preferred `47000 + uid % 1000`, so two users of one Mac differ. If that port is taken, a free one. The
  actual port is always read from `$MU_HOME/run/dashboard.json` (0600: `pid, port, started, version`); nothing assumes
  the preferred port.
- **One instance.** `flock` on `run/dashboard.lock` (a hand-run `serve` and the agent cannot both serve).
- **Commands (phase 1).**
  - `mu dashboard`: status (`--json`: running, port, url without token, nodes being watched).
  - `mu dashboard open`: start if needed (when off), then open the browser with the token (§4.6).
  - `mu dashboard on|off`: install or remove the LaunchAgent; remembered, like auto-update. CI is always off.
  - `serve`: internal.
  - Later, if wanted: `url`, `stop`, `reset-token`.
- **API.** Plain JSON, polled. No stream endpoint, which also keeps several open tabs inside the browser's six
  connections per origin.
  - `/api/overview` (machine cards, running and recent jobs), `/api/jobs`, `/api/jobs/<id>`,
    `/api/jobs/<id>/log?offset=` (served from the server's buffer of the `follow` session), `/api/jobs/<id>/metrics`,
    `/api/nodes/<name>`, `/api/mac`.
  - Every value that came from a node or a sampler carries its time; the server never presents an old value as new.
  - Job ids are checked against `proto.JOB_ID` and node names against the node-name rule. No route takes a path.

### 4.5 Storage

| Where | What | Kept | Phase |
|---|---|---|---|
| Node `jobs/<id>/` | `result.json` + `usage` (sealed); `metrics.jsonl` | with the job | 1 |
| Node `metrics/now.json` | newest samples, queue counts, `rev` | replaced every 5 s | 1 |
| Node `metrics/host-YYYYMMDD.jsonl` | host history | 7 days | 2 |
| Node `jobs/<id>/progress.jsonl` | parsed progress | with the job | 3 |
| Mac `runs/<id>.json` (project) | run record + `usage` | committed, as today | 1 |
| Mac `$MU_HOME/jobs.index` (0600) | one line per submit: `job_id, node, key, project root, submit_started, accepted, fetched` | 1 year | 1 |
| Mac `$MU_HOME/run/dashboard.json`, `dashboard.token` (0600, folder 0700) | port; 32 random bytes, new at each server start | while the server runs | 1 |
| Mac `$MU_HOME/dashboard/cache.sqlite` (0600, WAL, folder 0700) | tables `nodes`, `jobs`, `job_samples`, `host_samples`, `events`, `offsets` | host samples: raw 48 h, then 1-min mean/max 90 days; job series thinned to 2,000 points when the job ends (the full series stays on the node); events 1 year | 2 |
| Mac `$MU_HOME/local-runs/YYYYMM/<id>.json` (0600) | one record per `mu run`, only when recording is on (§6.2) | 90 days or 20,000 records | 2b |

`jobs.index` is how a job id leads to its project folder (for `.mu/jobs` and `runs/<id>.json`). It exists because
`projects.txt` is written only by `mu link` and is read by auto-update's gc; adding submitted projects there would
change what gc keeps. Jobs submitted before phase 1, or from another workstation, show the node's `project_id` only.

### 4.6 Security

| Threat | Measure |
|---|---|
| Other machines | bind `127.0.0.1` only (not `0.0.0.0`, not `::`); print `127.0.0.1`, not `localhost` |
| DNS rebinding | `Host` must be `127.0.0.1:<port>` or `localhost:<port>`, else 403 |
| Other web pages (CSRF, reading data) | every `/api/` request needs `X-Mu-Token`. A custom header forces a CORS preflight, which the server never approves. There are no CORS headers. Any later state-changing request also needs a same-origin `Origin` and `Sec-Fetch-Site: same-origin` |
| Other local users | 127.0.0.1 is shared, so the token is required for every data request. Static files hold no data |
| Getting the token to the browser | `mu dashboard open` reads the port from `run/dashboard.json`, writes `run/dashboard-open.html` (0600), a redirect to `http://127.0.0.1:<port>/#t=<token>`, and opens that **file** (Jupyter's method). argv holds only a path. The page moves the fragment into `localStorage` and removes it with `history.replaceState`. The server deletes the file when the token is first used, or after 60 s. Fragments never reach a server or a `Referer`. The token is never printed, never in `--json`, never in a log |
| A page without a token | The first click is often on the URL `mu submit` printed, which has no token. That page holds no data and says: "run `mu dashboard open` in a terminal (or ask Claude to)". The same page appears when a stored token is from an earlier server start (401) |
| A process squatting the port | With the LaunchAgent the port is held while the user is logged in. The token rotates at each server start, so a token a squatter's page could read from `localStorage` is useless once the real server is back |
| Hostile node text (any job can write its name, log, notes, metrics lines, files that `hello` reads) | render with `textContent` only, never `innerHTML`; strip control and escape sequences (`proto.clean_text`); cap sizes; parse numbers as numbers. CSP: `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`, plus `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer` |
| Secrets | the API returns an allowlist of fields. The server never reads the Keychain, `relay.auth`, keys, invites or `node.conf` beyond `name`, `added` and the host-key fingerprint. It reaches nodes only through `NodeReader` → `jobs.Conn`, whose proxy subprocess reads the relay password, so the server process never holds it. The relay admin API is not used |
| Actions | phases 1–3 are read-only, so an XSS that slipped through could read job data but change nothing. Cancel (phase 4, only if the owner wants it): `POST`, token, Origin check, a confirmation naming the job, node and state, and the same code as `mu cancel`. Never fetch |
| Other people's jobs on a shared node | `list` without a project filter shows every workstation's jobs, argv included. The job table's "from" filter defaults to this workstation's key; §9 Q7 is decided before a second workstation pairs |
| Notifications (phase 3, opt-in) | `osascript` with the text passed as arguments (as `au_notify` does), the job name cleaned and capped |

### 4.7 Terminal and Claude Code

- **Claude sees the same node data.** `mu compute status <node> --json` adds `"stats": {…}` (the `stats` answer) when
  the node has the feature, and the text form adds one line: CPU, memory, and each running job's cores and memory
  against its limit. That gives Claude the node's load when choosing where to run (PLAN §6.3) and the verb a test
  without a browser. `mu status <id> --json` carries `usage` for finished jobs.
- `mu submit` prints one more line when the dashboard is on: `dashboard: http://127.0.0.1:<port>/#job=<id>  (mu
  dashboard open)`, with the port from `run/dashboard.json`. `--json` adds `"dashboard": {"url": …, "running":
  true|false}`. Neither contains the token. `mu submit` only reads that file; it never connects to the server.
- `claude/CLAUDE-toolchain.md` gets a paragraph: mention the job's dashboard link once when a remote job starts; run
  `mu dashboard open` only when the user asks; keep using `mu … --json`; never read the dashboard's API.
- Every object on the page shows the `mu` command that gives the same data, with a copy button (`mu log <id> -f`,
  `mu fetch <id>`, `mu cancel <id>`, `mu compute status office-pc`).

## 5. Screens

### 5.1 Phase 1: one page

One page, three parts: machine cards, the job table, and a job detail that opens below or in place of the table
(`#job=<id>`). No separate Nodes or This Mac pages, no search, filters beyond "from" and "node", shortcuts or theme
toggle in phase 1. Light and dark follow the system.

```
 μ dashboard                                                   ● 1 of 1 nodes online · updated 2 s ago
 ─────────────────────────────────────────────────────────────────────────────────────────────────────
  ┌ ● This Mac ───────────────────┐  ┌ ● office-pc  via relay ───────────────────────────────────┐
  │ CPU  38 %  ▁▂▃▅▃▂▂▃  (8: 4P+4E)│  │ CPU  93 %  ▇▇█▇▇█▇█   WSL VM figures, not Windows'         │
  │ MEM  9.1 / 16 GB · pressure ok│  │ MEM  14.2 GB used of 22 GB job limit · 8.1 GB available    │
  │ on battery                    │  │ 1 running · 1 queued · μ v2026.11.8 (same as this Mac)     │
  └───────────────────────────────┘  └───────────────────────────────────────────────────────────┘
  JOBS     from: this Mac ▾   node: all ▾                                                   12 jobs
  ▶ running    k3f9qa  frame-fea     jatayu  office-pc            18m04s   12.0 GB*   17.8 / 20 cores
  … queued #1  m7c2xz  frame-fea-v2  jatayu  office-pc   waits 18m       —          —
  ✓ succeeded  x9d1ab  cfd-cavity    bench   office-pc            41m12s    6.0 GB*   17.1 / 20
  ✕ oom        ab12cd  frame-fea     jatayu  office-pc          2h10m47s   21.8 GB*~  18.0 / 20
       oom: the job used more memory than its 22 GB limit   (~ last sample, up to 5 s old)
  ⊘ cancelled  q4wz8e  mesh-study    jatayu  office-pc             0m31s    —          —
  older jobs: mu status                * cgroup peak, includes file cache
```

Job detail:

```
 ← Jobs   frame-fea   ▶ running   20261114-101500-k3f9qa [copy]                       office-pc · via relay
 jatayu @ 4f2a91c (dirty)   μ v2026.11.6   attempt 1   key avi-mac   started 10:15:22   elapsed 18m04s
 limits: 22 GB · 12 h · 20 cores        [copy mu log -f]  [copy mu fetch]  [copy mu cancel]
 ┌ LOG ── following ── 48.2 kB ─── [pause] [wrap] ┐  ┌ RESOURCES ── whole run ───────────────────────┐
 │ == μ job 20261114-101500-k3f9qa attempt 1 …    │  │ cores    ▇▇▇█▇▇▇█▇   17.8 / 20                 │
 │ meshing … 412 331 nodes                        │  │ memory   ▃▄▅▅▆▆▆▇▇   9.8 GB anon+shm           │
 │ solving …                                      │  │          ┈┈┈┈┈┈┈┈┈┈  22 GB limit               │
 └────────────────────────────────────────────────┘  │ cache    ▁▂▂▂▃▃▃▃▃   2.1 GB (reclaimable)      │
                                                     └───────────────────────────────────────────────┘
 Record (after fetch): head, tree, snapshot, toolchain, node hardware, argv, cwd, limits, usage
```

Sources, all phase 1:

- Machine cards: psutil on the Mac; `watch`/`stats` on the node; `hello` for versions, runner, last stop, disk and
  auto-update state ("needs attention" items are lines on the node card: last stop unclean, disk low, μ version
  different from the Mac's, auto-update failed).
- Job table: `list limit=100` (no project filter) on each node; `usage` for finished jobs (jobs that finished before
  the phase 1 release show `—`); live figures for running jobs from `watch`. Project names come from `jobs.index`.
- Job detail: `status`; `.mu/jobs` and `runs/<id>.json` found through `jobs.index`; the log through `log follow=1`;
  the resource chart from `metrics` (the whole run) plus live points from `watch`. Progress is elapsed time until
  phase 3.

Rules:

- Every number shows its age. A value older than 15 s turns grey with "as of 10:42:07", and the word "stale".
- Charts never interpolate across a gap.
- Offline is a normal state, not an error.
- Never colour alone: an icon and a word go with each state colour.
- Every control is a real button or link, reachable by keyboard. Nothing is announced by a screen reader on each
  refresh (no live region on figures that tick). Tested in D26.
- Monospace with tabular figures for ids and numbers. `prefers-reduced-motion` respected.
- States: the no-token page (§4.6); loading; empty ("no jobs yet: `mu submit --on office-pc -- …`"); refused (exit 5,
  red banner with the CLI's words and "never retried"); "reconnecting…" after a wake; an old node ("update μ on
  office-pc for live statistics").

### 5.2 Later screens

- **Phase 2:** separate Nodes and This Mac pages with host history to 7 days (node) and 90 days (Mac, rolled up);
  search, filters and shortcuts; the job timeline from `events`; Compare › Performance (§6) on the benchmark suite.
- **Phase 2b:** local runs in the job table (`This Mac` as a machine), when recording is on.
- **Phase 3:** progress (stage, fraction, residuals); results as tables and images.
- **Phase 4:** 3D results, run-vs-run comparison, Projects (result series across commits), a daily compute-hours chart.

## 6. Performance comparison: Mac vs node

The owner did not ask for this in the request that started the dashboard; it is kept as a later option because the
data μ starts recording in phase 1 makes most of it cheap.

### 6.1 The view (phase 2 for the benchmark suite, phase 2b for project jobs)

```
 Compare › Performance    task: [frame-fea  python frame/fea.py --mesh 2.0 ▾]    toolchain v2026.11.8
 inputs: jatayu @ 4f2a91c, clean on both ✓            (a dirty or different commit is shown, never grouped silently)
                 wall      time to answer   CPU time   cores used          peak working mem   kernel peak   runs
 office-pc       6m12s     7m40s            1h51m      17.9 of 20  ████▍   9.8 GB             11.2 GB *     3
 This Mac        41m05s    41m05s           2h38m       3.9 of 8   ██      5.1 GB †           3.0 GB ‡      1
 office-pc is 6.6× faster on wall time, 5.4× faster to an answer (upload, queue and fetch included)
 calibration (mu test, same toolchain):  cfd-cavity 6.1×  fea-cantilever 4.8×  cad-solid 0.9×
  * memory.peak of the job's cgroup, includes file cache    † sum of process RSS, sampled every 1 s
  ‡ ru_maxrss: the largest single process
```

**Which runs are "the same task".**

- The key is the job name (`--name`, or a local run's `--label`), `argv`, `cwd` relative to the project root, and the
  toolchain version.
- Runs are **identical** only when both came from a clean tree at the same `head`. A remote job's `tree` hash comes
  from its snapshot. A local run records `head` and `dirty` only.
- Anything else is shown as **similar**, with the difference named.
- The benchmark suite (`benchmarks/`: cfd-cavity, fea-cantilever, cad-solid, aero-vlm-wing, slice-print, cam-plate)
  is the calibration. It is identical by construction, needs no `mu run` shim, and comes first (phase 2). The node's
  results arrive through the `bench` verb, and the Mac's are in `state/bench/`.

### 6.2 What μ must start recording

| Figure | Node job (source) | Local `mu run` on the Mac (source) | Phase |
|---|---|---|---|
| wall time | `wall_s` (exists) | `wall_s`: monotonic clock in the shim | 1 / 2b |
| time to answer | `jobs.index`: `fetched − submit_started` | = wall | 1 / 2b |
| queue wait | `started_at − queued_at` (exists) | 0 | 1 |
| CPU time (user, system) | cgroup `cpu.stat` (shim) or the last sample (runner), covers the whole tree | `os.wait4` → `ru_utime + ru_stime` of the child, which covers reaped descendants; orphans (e.g. a daemonised `cad` server) are missed and flagged | 1 / 2b |
| cores available | `limits.cores` (`MU_JOB_CORES`) and `cores_logical` | `os.cpu_count()`, plus P/E split from `sysctl hw.perflevel0/1.logicalcpu` | 1 / 2b |
| mean cores used, utilisation | `cpu_s / wall_s`; `/ cores` | same | 1 / 2b |
| kernel peak memory | `memory.peak`, or the sampled max of `memory.current` (includes page cache) | `ru_maxrss` (bytes on macOS), the largest single process, not the tree | 1 / 2b |
| peak working memory (the figure to compare) | max over samples of `anon + shmem` (5 s) | max over samples of the tree's summed RSS (psutil, 1 s, a thread in the shim) | 1 / 2b |
| processes | `pids` (sampled max; `pids.peak` where present) | number of processes seen | 1 / 2b |
| memory limit events | `memory.events` `max`, `oom_kill`; the unit's `Result` | — | 1 |
| I/O bytes | sum of `/proc/<pid>/io` if readable (§4.2) | psutil `io_counters` is not available on macOS: none | 2 |
| hardware, toolchain | `node`, `toolchain` (exist) | `sysctl hw.model, machdep.cpu.brand_string`, `MANIFEST.json` | 1 / 2b |
| provenance | `head, tree, dirty, snapshot` (exist) | `head, dirty`, project root | 2b |

**The local recording shim (phase 2b, opt-in).** It wraps every local command (CAD, render, slicer), which makes it
the riskiest change in the plan, so it is separate from the rest of phase 2 and off by default.

- Today `mu run` is `exec "$@"` (`bin/mu-toolchain` line 18) and leaves no trace. With `MU_RUN_RECORD=1` it becomes
  `exec "$PY" -I -S <recipe>/src/mu/runrec.py "$@"`; without it, nothing changes.
- The shim writes `run/local/<pid>.json` (so a running dashboard can follow it), starts the command in the same
  process group with the same stdio and environment, and ignores SIGINT and SIGQUIT while it waits. The terminal
  delivers them to the child. It forwards SIGTERM and SIGHUP.
- It samples the tree every second in a thread, then calls `wait4`.
- It writes `local-runs/YYYYMM/<id>.json` (`argv` capped at 4 kB, `cwd`, project root, `head`, `dirty`, version,
  times, exit code or signal, `usage`).
- It exits with the child's code, or `128 + signal`.
- The node's job shim never records (the cgroup already measures).
- Expected overhead: about 40 ms of Python start plus 23 ms for the psutil import. D44 bounds it.
- It becomes the default only after D44 has passed and the shim has been used on real CAD sessions by the owner
  (§9 Q4).

**Honesty rules on the page.**

- The kernel peaks (cgroup with cache, largest process) are never compared with each other. Only "peak working
  memory", which is sampled on both machines, sits in the comparison column, with its footnote.
- An M1 "of 8" cores hides that 4 are efficiency cores. The P/E split is shown.
- Node figures are the WSL VM's unless §9 Q2 decides otherwise. If Windows was busy, the page cannot know.

## 7. Phases, acceptance tests and effort

Effort is for Claude Code doing the work with the owner reviewing, in the style of the job layer: tests with a fault
injected that must make them fail ("FAILS-OK"), unit level in `tools/test-jobs/`, end to end in the existing
`job-layer.yml` (fake relay, a node on the Linux runner). Browser checks use headless Chrome, which the macOS and
Ubuntu GitHub runners already have.

### 7.0 Gate before phase 1: the node's auto-update, and one probe on the PC (half a day to a day)

**Gate.** The node's auto-update reports `failed` (`hello.autoupdate.last_result`). New verbs and the sampler reach the
node only through a release and its auto-update (or `mu update` on the PC). Phase 1 does not start until auto-update
on the node succeeds once.

**Probe.** `mu submit -- python probe.py`, plus a few `mu compute status` calls, report:

- the unit's `ControlGroup`, and the WSL kernel version;
- whether `memory.peak`, `pids.peak` and `/proc/pressure/*` exist;
- an OOM-killed unit (a job that allocates past `--mem`): what is left of its cgroup directory and of
  `systemctl --user show` (`MemoryPeak`, `CPUUsageNSec`) right after the kill;
- whether `/proc/<pid>/io` is readable as `mu`;
- the cost of one sampler pass;
- **the CPU cost of one verb call on the PC** (sshd session + gate + `bin/mu` + Python), from `/proc/stat` deltas
  across 50 `hello` calls;
- on the Mac: that a process started by a LaunchAgent can read the relay password from the Keychain through
  `mu compute proxy` (the auto-update agent runs in the same domain, but has not needed it).

If the OOM probe shows the cgroup survives, the runner-sampled path stays as the fallback; if the verb call costs more
than about 150 ms of CPU, the traffic plan in §4.3 is revisited before building.

### 7.1 Phase 1: jobs, nodes and live load on one page (6–9 working days)

**Scope.**

- **Node:** the runner's sampler (`now.json`, per-job `metrics.jsonl`, running maxima, `rev`); `cgroup` in
  `state.json`; `usage` in `result.json` (shim and runner); the `stats`, `watch` and `metrics id=` verbs;
  `hello.features`; `usage` in `list` and `status`, `rev` in `list`; journaling as in §4.2.
- **Mac, CLI:** `mu submit` writes `jobs.index` and prints the dashboard line; `mu fetch` copies `usage` into the run
  record and marks `fetched` in `jobs.index`; `mu compute status --json` adds `stats`.
- **Dashboard:**
  - `mu dashboard`, `open`, `on|off`, `serve`; the LaunchAgent; `src/mu/dashboard.py`; static files in
    `recipe/share/dashboard/`; uPlot vendored with its SHA-256 recorded;
  - `NodeReader` (allowlist, own ControlPath, wake handling, battery rule);
  - the one page of §5.1, with the no-token page;
  - in-memory host history (1 h); job series from the node;
  - the token, CSP and `Host` check;
  - the paragraph in `CLAUDE-toolchain.md`.

About 350 lines on the node, 900 in the server and CLI, 700 of HTML/JS/CSS, and 1,200 of tests. One round trip on the
PC is included.

| # | Check | Fault put in → must fail |
|---|---|---|
| D01 | A job that allocates 1 GB and keeps 2 cores busy for 10 s: `usage.mem_peak_bytes ≥ 1 GB`, `cpu_s` 20 ± 15 %, `cores_avg` ≈ 2, `source: "cgroup"` | `usage-from-shim` (read the shim's own rusage, not the cgroup) → peak < 100 MB |
| D02 | An `oom` job (runner-written result) carries `usage` with `source: "runner-sampled"`, `mem_peak_bytes` ≥ 80 % of its limit and an oom marker; a `timeout` job carries `cpu_s` within 5 s of its CPU; `mu fetch` copies `usage` into `runs/<id>.json` | `read-cgroup-after-exit` (the runner reads the unit's cgroup in `classify()`) → `usage` absent or zero |
| D03 | `stats` answers valid JSON, exit 0, with the running job and its cores ≈ the job's; idle → `running: []`; with 2,000 synthetic finished jobs the node-side time stays < 100 ms; with the runner stopped for 20 s → `stale: true` | `stats-scan` (call `scan_jobs`) → over 100 ms; `no-age` → `stale` missing |
| D04 | Against a node without `features` (previous release), the page lists jobs and says "update μ on office-pc for live statistics" | `assume-features` → error page, no jobs |
| D05 | **Jobs by node.** Two fake nodes, one job submitted to each: `/api/jobs` and the rendered table show each job under its own node, and each follows queued → running → succeeded within 15 s of the node's change | `node-from-config` (every job labelled with the first paired node) → wrong node |
| D06 | **Figures are right.** A fake node returns known `stats`: `/api/nodes/<n>` and the rendered machine card show those numbers (CPU %, used, available, limit, each job's cores and memory) | `swap-mem-fields` (available shown as used) → mismatch |
| D07 | **Stale is shown.** Cut the fake node's link: within 15 s every node value carries its age, is greyed and says "stale"; the API marks it `stale: true` | `serve-last-as-live` → values still presented as current |
| D08 | **Mac sampler.** Against a 2-core busy loop for 10 s, the Mac card's CPU equals 2 cores' worth of the machine within ± 25 % | `cpu-first-call` (psutil's first `cpu_percent` with no interval, always 0) → off |
| D09 | The sampler costs < 5 ms per pass (median); the runner's loop stays at 1 s ± 0.1; with the dashboard killed for 5 min in a 10-min job, the job's series has no gap over 2 × the interval | `sample-under-lock` → the queue waits; `mac-samples-only` → a 5-min gap |
| D10 | `metrics` pieces joined by offset across a killed link equal the node's file byte for byte (as R21) | `metrics-from-zero` → duplicates |
| D11 | `watch` ends at `max`; a client that disappears leaves no session after the cap (5 s in the test); `stats` and `metrics` add no line to `audit.log`, `watch` adds two | `no-cap` → session still open; `journal-each-call` → audit lines grow per call |
| D12 | The server is reachable on 127.0.0.1, refused on the machine's LAN address and on `::1` | `bind-all` → LAN connect succeeds |
| D13 | `Host: evil.example:<port>` → 403 on every route | `no-host-check` → 200 |
| D14 | `/api/*` without `X-Mu-Token` → 401, with the token in a cookie only → 401, with a wrong token → 401, with the previous server start's token → 401 and the no-token page, which holds no job or node data | `no-token` → 200; `fixed-token` → old token accepted |
| D15 | The token appears in none of: the argv recorded by a stub opener during `mu dashboard open`, `logs/dashboard.log`, the output of `mu submit`, `mu dashboard --json` and `mu compute status --json`; `dashboard-open.html` is gone after first use | `open-url-argv` (open `http://…#t=` directly) → found in the stub's argv |
| D16 | A job named `<img src=x onerror=…>` that logs HTML, ANSI escapes and a 1 MB line: in headless Chrome no element is created from it, nothing runs, the CSP header is present, the page stays responsive | `inner-html` → the handler runs |
| D17 | **Allowlist.** Unit test of `NodeReader`: every verb outside the list raises before any ssh call; no other code path in `dashboard.py` constructs a `jobs.Conn` (a source check); end to end, a fake node records only allowed verbs while every route is exercised | `allow-cancel` → `cancel` passes the wrapper |
| D18 | Node offline → "offline since", retries back off; exit 5 (host key) → red, zero further node requests in 5 min | `retry-refused` → requests > 0 |
| D19 | With the dashboard off, killed, its port held by another program, or a server stub that accepts and never answers, `mu submit/status/log/fetch` give the same exit codes and the same `--json` (minus the `dashboard` field), and none of them opens a connection to the dashboard port | `submit-waits` (wait for the server) → hangs on the stub |
| D20 | **Own master.** While a CLI `mu log -f` runs on the CLI's master, a dashboard `list` that times out (fake node stalls 15 s) closes only the dashboard's master; the CLI's session continues | `shared-controlpath` → the CLI's `mu log -f` ends |
| D21 | `mu compute status <node> --json` includes `stats` from a node with the feature, and omits it (no error) from an older node | `drop-stats` → missing |
| D22 | `mu submit` writes a `jobs.index` line; job detail finds the project's `.mu/jobs` record through it; with 150 finished jobs the page shows 100 and "older jobs: `mu status`"; the dashboard sends `limit=100` | `no-index` → project unknown; `default-limit` → 20 rows |
| D23 | `mu dashboard on` → the agent is loaded and the URL answers after the agent is killed (launchd restarts it); `off` → nothing listens; with no tab and nothing unfinished for 5 min, zero node requests and the server's CPU time does not grow by more than 0.1 s | `poll-when-idle` → node requests seen |
| D24 | On battery (stubbed) with no tab and a running job: `list` at most every 5 min, and the dashboard's master is gone after `ControlPersist` | `ignore-battery` → `list` every 60 s |
| D25 | With a tab open for 10 min during a job, μ's own processes on the node use < 2 % of one core; the job's `cpu_s` is within 3 % of a run without the tab | `poll-stats-1s` (poll `stats` per second instead of `watch`) → over budget |
| D26 | **Accessibility** (headless Chrome): every control is reachable with Tab and has an accessible name; every state colour has a text label next to it; no `aria-live` region changes on a routine refresh | `div-button` (a clickable `div`) → unreachable |
| D27 | Two `mu dashboard serve` at once → one serves, the other exits 0 | `no-lock` → two processes |

**Done when** the owner, with nothing started by hand, opens the page during a real job on the office PC and sees the
job under its node, its live cores and memory against the limit, the whole run's chart, and both machines' load with
their ages; and when, after an `oom` job, its row shows its peak memory; and when `mu compute status office-pc --json`
gives Claude the same node figures.

### 7.2 Phase 2: history, more screens, benchmark comparison (4–6 days)

**Scope.**

- **Node:** host history files with 7-day retention; the verbs `metrics day=`, `events` and `bench`.
- **Mac:** the SQLite cache with retention and reconciliation; separate Nodes and This Mac pages; search, filters,
  shortcuts; the job timeline; Compare › Performance on the benchmark suite.

| # | Check | Fault → must fail |
|---|---|---|
| D30 | Host files older than 7 days are deleted; a job's `metrics.jsonl` stays with it; the cache thins a finished job to ≤ 2,000 points | `no-retention` → old files remain |
| D31 | Delete `cache.sqlite`: after reconciliation, the job list and every job series are the same | `cache-only-field` → a field lost |
| D32 | The same benchmark on both machines → one Performance row per machine; a run from a dirty tree is "similar", never grouped as identical | `match-by-name` → grouped as identical |
| D33 | `events` gives every transition and attempt of a re-queued job in order | `drop-requeue` → an attempt missing |

### 7.3 Phase 2b: local run recording, opt-in (3–4 days)

The `mu run` shim and `local-runs/` (§6.2), behind `MU_RUN_RECORD=1`; local runs in the job table; Compare on project
jobs.

| # | Check | Fault → must fail |
|---|---|---|
| D44 | `mu run` with the shim: exit codes 0, 3, 130 (Ctrl-C), 143 (SIGTERM) pass through; stdout bytes identical; `python -c 'input()'` reads from the terminal; a record with `usage` is written, even when the child is killed; without `MU_RUN_RECORD=1` the command is exec'd unchanged; median added time < 80 ms | `swallow-exit` → exit 0 for a failing command; `no-record` → record missing |
| D45 | Locally, 2 busy processes for 10 s with 1 GB in all: `cpu_s` 20 ± 15 %, peak working memory ≥ 1 GB (the tree, not one process) | `maxrss-as-peak` → about 0.5 GB |

### 7.4 Phase 3: progress and notifications (3–5 days)

**Scope.**

- **Progress parsers, on the node, incremental and capped:**
  - `MU_PROGRESS {…}` lines, emitted by `mu.fea` and the benchmarks;
  - CalculiX `.sta` and `.cvg`;
  - OpenFOAM `Time =`, `endTime` from `controlDict`, and residuals from the log or `work/**/log.<app>`;
  - Gmsh stages.
- **The `progress` verb.**
- **`mu.fea`:** stream ccx's output to `ccx.log` as it runs. Today `fea.py:222` uses `capture_output=True` and writes
  only at the end.
- **The Mac:** opt-in macOS notifications when a job ends; the thermal state if it costs little.

| # | Check | Fault → must fail |
|---|---|---|
| D50 | Recorded fixtures (OpenFOAM cavity, ccx `.sta`, Gmsh) give the expected fractions and stages | parser off by a field → wrong fraction |
| D51 | A hostile `MU_PROGRESS` line (2 MB, invalid JSON, HTML, a fraction of 7) is dropped or clamped; memory stays bounded | `no-cap` → memory grows |
| D52 | During a 60 s FEA job, `mu log` shows ccx's output before ccx ends | `capture` → nothing until the end |

### 7.5 Phase 4: results and more (1–2 weeks, owner's choice)

- Results as tables with tolerance badges, images, residual and convergence plots, mesh-convergence plots.
- three.js for STL and GLB. STEP is converted on the node.
- Run-vs-run comparison that shows only the fields that differ.
- Project result series across commits.
- Cancel with a confirmation, if the owner wants it. The tests are the same as D12–D17, plus CSRF: a cross-origin
  POST is refused.
- A systemd user service for Linux workstations.
- An optional `/metrics` export.

### 7.6 Effort, honestly

Phase 1 is 6–9 days, larger than the first draft's 3–5 because its tests now cover the owner's main asks and the
sampler moved in. All phases together are about 3,000–4,000 new lines plus tests, and one vendored library (three in
phase 4) to keep pinned. The risky parts are on the node and in `mu run`, not in the web page:

- the sampler must never slow or block the runner;
- the shim must never change what `mu run` does, which is why it is opt-in and separate.

Phases 1 and 2b need at least one round trip on the real PC or real CAD sessions each. Node code reaches the PC only
through a release and its auto-update, which is failing today and is the gate in §7.0.

## 8. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| The node's auto-update is failing (`hello.autoupdate.last_result = failed`) | New verbs never arrive | A gate on phase 1 (§7.0), not only a risk; the page shows the node's μ version and "update μ on office-pc" |
| The runner's samples are the only source for `oom`/`timeout` usage | Up to 5 s missing; a fast spike just before the kill may be missed when `memory.peak` is absent | Use `memory.peak` when the kernel has it (it is a kernel-tracked peak, read every 5 s); label `runner-sampled`; §7.0 checks what survives an OOM kill |
| `memory.peak` counts page cache | An OpenFOAM job writing fields looks bigger than it is; `--mem` sized from it is too generous | Label it; compare only sampled `anon + shmem`; show cache separately |
| WSL VM figures, not Windows | Someone using the PC slows a job and the page shows a quiet node | Said on the node card; the owner decides in §9 Q2 whether a root-installed Windows counter is worth it |
| Per-call cost on the node (sshd + gate + bash + Python) | The dashboard's own load shows up in the figures it reports; audit lines bury the important ones | `watch` and `log follow=1` instead of polling; `list` only on `rev`; no journal line for telemetry verbs; measured in §7.0; D11, D25 |
| A dashboard call disturbing Claude's ssh sessions | A timed-out call ends Claude's push or fetch | Its own ControlPath; it never closes the CLI's master (D20) |
| Keychain access from the LaunchAgent | The node worker cannot connect | Checked in §7.0; if it fails, the owner falls back to `mu dashboard open` from a terminal (§9 Q1) |
| The `mu run` shim changes every local command | A regression in signals, exit codes or TTY behaviour breaks CAD work | Opt-in phase 2b, D44, default only after real use |
| `list` cost grows with history (stage 1 never deletes jobs) | `list` gets slower on the node, and so does every quick call | `list` only when `rev` moves; `limit=100`; a node job-retention rule is a separate decision (§9 Q9) |
| `audit.log` is never rotated | It grows without bound | Fewer lines from the dashboard; a rotation rule is part of §9 Q9 |
| XSS through node text | Reads job data in the browser | Text only, strict CSP, D16; read-only bounds the damage |
| Scope creep: the page becomes where things are changed | Two interfaces drift; Claude cannot see what the user did | Read-only until the owner decides; every action stays a `mu` command; node figures also in `mu compute status --json` |
| More nodes and workstations | Each workstation × node pair holds one `watch` session while visible | Nothing runs with no tab; the relay holds one more WebSocket per workstation and node while active, well within its limits |
| Battery | ssh keepalives every 15 s for the whole job | No tab + battery → `list` every 5 min and the master expires (D24) |
| Vendored JS goes stale | Security fixes missed | Pinned by hash like the rest; reviewed at each release that touches the dashboard |

## 9. Questions for the owner

> **Defaults set 2026-11-14** (the owner may override): see [docs/ROADMAP.md](../ROADMAP.md) §2.3. In short: always
> on; the PC's figures are the WSL2 VM's, labelled, with no Windows-side task (the office PC is not to be
> disturbed); read-only; local `mu run` recording opt-in; comparisons on benchmarks and real project jobs (the JATAYU
> `perf/` log as a source); notifications opt-in; no reverse tunnel ever; node retention 90 days and monthly
> audit-log rotation. The dashboard is built before the shell work, and defines the status data the shell reads.

1. **Always on?** Recommended: `mu dashboard on` by default on workstations with a paired node: a LaunchAgent, so the
   URL always answers; it uses no CPU and makes no node calls when nothing is being watched. Alternative: no
   always-on process; the dashboard runs only after `mu dashboard open`. CI is always off.
2. **Windows' own load on the office PC.** The node figures are the WSL2 VM's. If someone uses Windows on that PC,
   the page shows a quiet node while jobs run slowly. (a) Accept it and keep the label (recommended for now). (b) A
   root-installed Windows scheduled task writes a small counters file (CPU, memory, top processes) that the distro
   reads. That is a root step on the PC and touches the "drives closed to `mu`" rule, so it needs your explicit yes.
3. **Read-only first?** Recommended: yes for phases 1–3. Should cancel ever be a button (phase 4)? Fetch never.
4. **Record every local `mu run`?** Needed for the Mac side of project-job comparisons. Recommended: opt-in
   (`MU_RUN_RECORD=1`) at first; on by default only after it has run on real CAD sessions. Kept 90 days on the Mac
   only, never committed.
5. **Comparison basis.** The benchmark suite first (phase 2). Real project jobs too (phase 2b)? If so, should Claude
   run a calibration task on both machines now and then?
6. **Desktop notifications when a job ends?** Recommended: opt-in, phase 3.
7. **Teams.** Decide before a second workstation pairs. A node's `list` shows every workstation's jobs, argv included
   (already true in the CLI). Recommended: the page's "from" filter defaults to this workstation's key; is showing
   others' jobs at all acceptable?
8. **Reverse tunnel or exporter on the node, ever?** It would let Beszel, Netdata or node_exporter work unchanged.
   Recommended: no (§3).
9. **Retention on the node.** Stage 1 keeps every job forever, which slows `list`/`hello` over time, and nothing
   rotates `/srv/mu/audit.log`. Decide a rule for both (e.g. archive finished jobs older than 90 days; rotate the audit
   log monthly, keep 12), separately from the dashboard.
10. **History length** (phase 2). Mac: raw 48 h, 1-min rollups 90 days. Node host history 7 days. Enough?

## 10. Read in sources

- Research notes A (architecture), B (build or adopt) and C (UX), 2026-11-14; the design review of this proposal,
  2026-11-14 (§11).
- `README.md`; `docs/PLAN.md` §6.3–6.7, §11, §17; `docs/design/job-layer.md` (§7 gate and verbs, §8 runner and
  unit, §11 outputs and run records, §13 root, §14, §21 tests); `docs/design/pairing.md`.
- `src/mu/node.py`: `VERBS`, `ARG_RULES`, `journal()` (syslog and `audit.log`, called for every verb at line 878),
  `scan_jobs`, `verb_list` (`limit` default 20, line 751; no project filter returns every job), `verb_log`
  (`follow=1`), `step`, `classify`.
- `src/mu/jobs.py`: `Conn.run` (`quick=True`: 12 s on a shared master, then `close_master()`, lines 357–379),
  `control_path`, `ssh_config_text` (`ServerAliveInterval 15`, `ControlPersist 300`), `cmd_compute_status`,
  `cmd_submit`, `cmd_fetch`, `run_record`.
- `src/mu/fea.py:222`; `src/mu/bench.py`; `src/mu/cli.py`; `bin/mu` (the auto-update LaunchAgent); `bin/mu-toolchain`
  (`run) … exec "$@"`); `install/hosts/wsl2/mu-node-gate`; `install/hosts/wsl2/mu-wsl2-distro.sh` (`LogLevel
  VERBOSE`); `toolchain/*/env.pip.txt` (psutil 7.2.2); `claude/CLAUDE-toolchain.md`.
- External (via B and C): the Netdata licence, Beszel agent docs, Grafana licensing, Glances, MLflow releases, uPlot,
  Chart.js, ECharts, three.js manual, the Ray dashboard, the Buildkite build page, the W&B run comparer, Open OnDemand
  active jobs. Versions are unverified until checked on each project's release page (§3).
- systemd behaviour cited from the review and not yet checked on the PC: a unit's cgroup is removed when its last
  process exits; `MemoryPeak` exists from systemd 256. §7.0 checks both.

## 11. Review

A design review (2026-11-14) found the build-versus-adopt conclusion and the "no root step" claim sound, and asked for
the design to be revised before phase 1. Its code claims were checked: `Conn.run`'s 12 s quick timeout and
`close_master()` (`jobs.py` 357–379), `verb_list`'s `limit=20` (`node.py` 751), a journal line for every verb
(`node.py` 878), `LogLevel VERBOSE` (`mu-wsl2-distro.sh` 331), and no rotation of `audit.log` anywhere in `src`, `bin`
or `install`. Its systemd claims (cgroup removed at exit, no `MemoryPeak` in 255) are taken as stated and added to the
§7.0 probe.

| Finding | Taken? | Where |
|---|---|---|
| F1 tests do not cover the main asks | Yes: jobs-by-node, figures, stale and Mac-sampler tests (D05–D08); stub opener (D15); the allowlist as a unit-tested wrapper (D17); "does not wait" against a silent stub (D19) | §7.1 |
| F2 polling does not fit one worker; audit noise | Yes: `watch` and `log follow=1` in phase 1, `list` on `rev`; telemetry verbs not journaled per call; verb CPU cost in the probe | §4.2, §4.3, §7.0 |
| F3 shared ControlPath can end Claude's commands | Yes: the dashboard's own master; reopen it on wake; the "warms Claude's connection" benefit dropped | §4.3, D20 |
| F4 the cgroup is gone before `classify()` | Yes: the sampler moved into phase 1; runner-written usage from samples; on-demand double read and "chart starts when the tab opens" removed; OOM cgroup check in the probe | §4.2, D02, D09 |
| F5 lifecycle built twice; inherited environment | Yes: a plain LaunchAgent; lazy spawn, idle exit, `dashboard.activity`, socket activation and the old D12 deleted; Keychain-from-agent in the probe; explicit-open-only offered as the alternative | §2.1, §4.4, §9 Q1 |
| F6 phase 1 too wide in interface, thin in data | Yes: one page, three commands, polled `/api`, no stream endpoint; re-estimated at 6–9 days | §5.1, §7.1 |
| F7 no project lookup; `list` capped | Yes: `jobs.index` in phase 1; the dashboard sends `limit=100` and says what is beyond it | §4.3, §4.5, D22 |
| F8 terminal-first broken for new data | Yes: `mu compute status --json` carries `stats` | §4.7, D21 |
| F9 WSL figures, not the PC's | Yes: an owner question with both options | §9 Q2 |
| F10 token gaps | Yes: the no-token page; the token rotates at each server start; the port is read from `run/dashboard.json`; `dashboard-open.html` deleted after first use. Of the two options offered, rotation is chosen over `sessionStorage`, because `sessionStorage` would make every new tab need `mu dashboard open` | §4.6, D14, D15 |
| F11 adopt analysis incomplete | Yes: the hybrid row and the reverse-port exporter row added; all versions marked unverified and kept out of the PLAN row (psutil's is the only one checked) | §3 |
| F12 the `mu run` shim tied to the least-requested feature | Yes: phase 2 split; the shim is phase 2b, opt-in, default only after real CAD use | §6.2, §7.3 |
| F13 battery, accessibility, teams, auto-update | Yes: battery rule (D24); accessibility test (D26); teams decided before a second workstation pairs, "from" defaults to this key; auto-update made a gate | §4.3, §5.1, §9 Q7, §7.0 |

Rejected: nothing outright. Two choices were made where the review offered options: token rotation rather than
`sessionStorage` (F10, above), and keeping `watch`'s open and close lines in the journal rather than dropping all
journaling of read-only verbs (F2), so a long-lived session on the node still leaves a trace. One addition beyond the
review: the node's host history file stays in phase 2, because phase 1's charts of a job come from the job's own
`metrics.jsonl` and the host's last hour is kept in memory on the Mac.
