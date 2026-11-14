# μ dashboard: a local view of jobs, machines and progress

**Status: proposal, nothing built.** Written on branch `dashboard-research` from v2026.11.8 (2026-11-14). It merges
three research notes (A: architecture and data, read from the code and the live `~/Library/Mu`; B: build or adopt;
C: what the screens should be). Where they disagreed, §2.2 says which way this proposal goes and why. Every claim
about μ's code was checked against `src/mu/*.py`, `bin/mu`, `bin/mu-toolchain` and `install/hosts/wsl2/mu-node-gate`.
Nothing here has run on the office PC yet; §7.0 lists what must be measured there first.

Sections: [1 Goal](#1-goal-and-non-goals) · [2 Decisions](#2-decisions) · [3 Build or adopt](#3-build-or-adopt-plan-17) ·
[4 Architecture](#4-architecture) · [5 Screens](#5-screens) · [6 Performance comparison](#6-performance-comparison-mac-vs-node) ·
[7 Phases and tests](#7-phases-acceptance-tests-and-effort) · [8 Risks](#8-risks) · [9 Questions](#9-questions-for-the-owner) ·
[10 Read](#10-read-in-sources)

## 1. Goal and non-goals

**Goal.** When μ is in use on a workstation, a page at `http://127.0.0.1:<port>/` shows:

- every job: its state, the compute node it went to, its place in the queue, its log as it grows, and how far it
  has got;
- live CPU and memory of the workstation and of each compute node, and of each running job;
- history and comparisons: what each job used (wall time, CPU time, cores, peak memory) and how the same task ran on
  the Mac and on the node.

It is a **run viewer first and a monitor second**. CPU graphs are a commodity. What no off-the-shelf tool shows is
"this job, from this snapshot and toolchain pin, on this machine, with its log, progress, resources and results, next
to the same job on the other machine".

**Non-goals.**

- **Not a second way to drive μ.** The terminal and Claude Code stay the interface. The page is read-only in the first
  versions. Where a user would act, it offers to copy the `mu` command. It never fetches outputs: a fetch overwrites
  files Claude may be editing and writes a run record that gets committed.
- **Not a source of truth.** It reads μ's records and the nodes' answers. Its own database is a cache that can be
  deleted at any time.
- **Nothing depends on it.** With the dashboard off, crashed or its port taken, every `mu` command behaves exactly as
  today.
- **Not reachable off the machine.** No LAN access, no remote viewing, no accounts. Each workstation runs its own.
- **No new path into the node or the workstation.** No listener on the node, no port forwarding, no reverse tunnel,
  no SaaS.
- **Not Windows' view of the PC.** The node figures are the WSL2 VM's. Windows' own load on the office PC is invisible
  while interop is off (§8).

## 2. Decisions

### 2.1 In one page

1. **Build it, small, on stdlib Python.** `mu dashboard` runs a `ThreadingHTTPServer` on the default toolchain's
   Python (`-I -S`, like every job-layer command). Static HTML/JS/CSS with no build step. One vendored, hash-pinned
   chart library (uPlot, MIT, about 45 kB). No CDN. Adopted: SQLite (stdlib), uPlot, and psutil on the Mac (already
   hash-pinned in both platforms' `env.pip.txt`).
2. **The node measures itself; the Mac pulls.**
   - The node's runner samples the running job's cgroup and the machine's `/proc` files, and appends them to files on
     the node.
   - New **read-only verbs** (`stats`, `metrics`, `events`, later `progress`, `watch`, `bench`) serve those files over
     the existing ssh session.
   - Curves have no gaps when the Mac sleeps or the link drops, because the node kept sampling.
3. **No root step and no protocol bump.** The gate only checks the *shape* of a request and hands any
   `verb key=value…` to `mu node serve`, which owns the verb table (`mu-node-gate`, last line). New verbs need only
   `node.py` and arrive with an ordinary release through the node's auto-update. `hello` gains `features: [...]`. An
   older node answers `unknown-verb`, and the dashboard falls back to `hello` + `list`.
4. **μ starts recording usage.** `result.json` and the run record gain `usage` (CPU time, peak memory, mean cores),
   read from the job's cgroup. Local `mu run` gets a small recording shim that writes the same figures. That makes
   the Mac vs node comparison possible, and it gives Claude real numbers to size `--mem` next time.
5. **Lifecycle.**
   - Job commands start it (`mu submit`, `mu status`, `mu log`, `mu fetch`), and so does `mu dashboard open`.
   - It exits after 15 idle minutes: no tab open, no unfinished job, no job command.
   - It polls hard only while a tab is visible.
   - Phase 3 replaces the spawn with launchd socket activation, so the URL always answers.
6. **Security.**
   - Binds 127.0.0.1 only and checks the `Host` header.
   - Every API call needs an `X-Mu-Token` header. The token lives in a 0600 file and in the page's `localStorage`,
     never in a cookie, argv, a log or Claude-visible output.
   - Strict CSP. Every node string is rendered as text only.
   - The server sends only read-only verbs, from a fixed allowlist.

### 2.2 Where the research disagreed

| Point | A | B | C | Here | Why |
|---|---|---|---|---|---|
| Does a new node verb need a root-owned gate change? | no | yes | yes | **no** | Checked: `mu-node-gate` has no verb list and ends with `exec "$MU_BIN" node serve "--key=$name" "$v" "$@"`; `node.py` `VERBS` and `ARG_RULES` decide. No change to `mu-wsl2-distro.sh` |
| Mac statistics | psutil | `vm_stat`/`sysctl`/`top` | either | **psutil, with a stdlib fallback** | psutil 7.2.2 is already in both pinned envs, so it is not a new dependency. A measured a 23 ms import (site-packages added by path, no `.pth`) and 5 ms per sample. `top -l 1` costs about a second per sample. Fallback: `os.getloadavg`, `sysctl`, `vm_stat` |
| Token transport | header + `localStorage` | HttpOnly cookie | cookie | **header + `localStorage`** | Cookies are shared by every port of 127.0.0.1, so any other local server (a dev server, a notebook) would receive the token. `localStorage` is per origin, and the origin includes the port |
| History store | SQLite | JSONL | SQLite | **SQLite cache on the Mac; JSONL on the node** | The node appends cheaply and serves by offset, like `log`. The Mac needs windowed queries, joins and rollups |
| Start | launchd socket activation | explicit `mu dashboard` | lazy start on `mu submit` | **lazy start from job commands (phase 1), socket activation (phase 3)** | The owner asked for it "whenever it's running". A detached spawn is simple and testable. Socket activation needs `launch_activate_socket` through `ctypes`, which is not yet tried |
| First version touches the node? | yes | yes (`stats`) | no | **yes, a little** | Without node statistics the first version misses half of the request. `stats` and `usage` are about 150 lines on the node |

## 3. Build or adopt (PLAN §17)

The constraint that decides: the node's only door is ssh inside the chisel tunnel, with the gate as ForceCommand,
`restrict`, `AllowTcpForwarding no` and no shell (job-layer §7.1, §13.1, §14). That rules out every monitor that pulls
from a port on the node. A monitor whose agent dials out to a hub needs the node to reach the workstation, which is a
new inbound path and a change to the security model. None of the candidates knows μ's jobs, snapshots, pins or run
records. Versions marked (?) come from secondary pages and must be checked before anything is pinned.

| Need | Candidates looked at | Fit through the gate, without root | Decision |
|---|---|---|---|
| Node and Mac monitoring with a web UI | **Netdata** v2.10 (?) (agent GPLv3+; the v2 dashboard is under NCUL1, not open source) | no: port 19999 or streaming to a parent | **Not adopted** |
| | **Glances** web (LGPLv3, psutil + bottle) | no as a web UI (binds 0.0.0.0 by default); `--stdout-json` behind a verb would work but is a third-party program for a few `/proc` reads | **Not adopted** |
| | **Beszel** 0.18–0.19 (?) (MIT; Go agent, no root) | no: SSH mode needs hub→agent port 45876; WebSocket mode needs the node to dial the workstation | **Not adopted now.** Closest fit. Revisit if the relay moves to Headscale (§17 row 1), which would give a real network path |
| | **Prometheus 3 + node_exporter + Grafana 13** (Grafana AGPLv3) | no for scraping. Grafana would still need μ to serve a JSON API, so μ builds the server anyway | **Not adopted.** Optional later: μ exports `/metrics` text so a team can add Grafana without μ depending on it |
| | Cockpit, cAdvisor, `below`, `atop` | need root or a daemon | Not adopted |
| Job and run UI | **Ray** dashboard, **Prefect**, **Dagster**, **Airflow**, **Nomad** UI, Slurm portals, **Open OnDemand** | each replaces μ's job model or needs open ports between machines (§17 already kept μ's queue) | **Not adopted**; layouts borrowed (§5) |
| | **pueue**, a self-hosted **GitHub Actions** runner, **Dozzle** | no web UI; dials SaaS; reads Docker logs only | Not adopted |
| Experiment tracking, run comparison | **MLflow** 3.x (Apache-2.0; local `mlflow ui` over a file store) | yes, fed from `runs/*.json` after fetch | **Not now.** About 500 MB, a second source of truth, no live view. The only plausible add-on later, for comparing FEA runs across parameters |
| | Aim, TensorBoard, Sacred + Omniboard, DVC Studio | heavy, MongoDB, or SaaS | Not adopted |
| Web framework | **Streamlit**, **Dash**, **NiceGUI**, **Panel** | yes, but 100+ MB of Python dependencies, websockets per session, hard to keep a strict CSP | **Not adopted**; fine for a throwaway spike only |
| | **htmx** 2.0, **Preact + htm** | vendored files | **Not in v1.** Vanilla JS is enough. Consider Preact + htm if the front end passes about 1,500 lines |
| Charts | **uPlot** 1.6.3x (MIT, ~45 kB) | vendored | **Adopt** for every time series |
| | Chart.js 4.5 (MIT), ECharts 6.1 (Apache-2.0), Plotly.js (MIT, 3 MB+) | vendored | Chart.js or hand-drawn SVG for bars and scatter in phase 4. Plotly is too heavy |
| 3D results | **three.js** (MIT; STLLoader, GLTFLoader), `<model-viewer>`, vtk.js | vendored | three.js in phase 4. `model-viewer` cannot do wireframe or sections. vtk.js only if volume fields are needed |
| Collector, history, server | the above | — | **Build**: the node sampler (~200 lines, `/proc` + cgroup v2, stdlib), the verbs, the stdlib server, a SQLite cache |

**Recommendation: build the dashboard on stdlib Python, adopting uPlot, SQLite and the already-pinned psutil.** Row to
add to the PLAN §17 table once the owner accepts:

> | Local dashboard (`mu dashboard`) | Netdata, Glances, Beszel, Prometheus + Grafana, Ray/Prefect/Dagster UIs, MLflow, Streamlit/Dash | Build on stdlib Python + vendored uPlot (compared 2026-11-14, `docs/design/dashboard.md` §3): every monitor needs a port on the node or a reverse path the gate forbids, and none knows μ's jobs. Revisit Beszel if the relay moves to Headscale; MLflow as a later add-on fed from run records |

## 4. Architecture

### 4.1 Picture

```
 WORKSTATION (Mac)                                               COMPUTE NODE (WSL2 "mu-compute", user mu)
 ┌──────────────────────────────────────────────┐               ┌───────────────────────────────────────────┐
 │ browser tab ──fetch(stream), X-Mu-Token──┐   │               │ mu node run (runner, systemd user unit)   │
 │                                          ▼   │               │  every 5 s, outside the queue lock:        │
 │ mu dashboard serve  (127.0.0.1:47xxx)        │               │   job cgroup: cpu.stat memory.stat/peak    │
 │  ├ HTTP + stream threads                     │               │   /proc/stat meminfo loadavg pressure      │
 │  ├ Mac sampler (psutil)                      │               │  → jobs/<id>/metrics.jsonl  (phase 2)      │
 │  ├ one worker per node ── jobs.Conn ─────────┼──ssh (shared──┼─→ mu-node-gate → mu node serve <verb>      │
 │  │   verbs: hello list status log            │   ControlMaster│  → metrics/host-YYYYMMDD.jsonl (phase 2)  │
 │  │          stats metrics events progress    │   inside the  │  → metrics/now.json         (phase 2)      │
 │  │          watch bench   (allowlist)        │   chisel      │ mu node run-job (shim, in the job cgroup) │
 │  ├ SQLite writer → dashboard/cache.sqlite    │   tunnel)     │  → result.json + usage{}    (phase 1)      │
 │  └ reads: .mu/jobs, runs/, jobs.index,       │               └───────────────────────────────────────────┘
 │           local-runs/, compute/*/last-status │
 │ mu run (recording shim) → local-runs/*.json  │   relay.martand.systems: unchanged; carries one more ssh session
 └──────────────────────────────────────────────┘
```

The workstation always opens the session. Inside a session the node may stream (`log follow=1` already does), and
that is all "push" means here.

### 4.2 On the node

**Usage in the result (phase 1).** The shim, `mu node run-job`, is the job unit's main process and lives in the
job's cgroup. Just before `write_result` it reads its own cgroup (`/proc/self/cgroup` → `/sys/fs/cgroup/<path>/`):
`cpu.stat` (`usage_usec`, `user_usec`, `system_usec`), `memory.peak`, `memory.events` (`oom_kill`, `max`, `high`),
`pids.peak`. It then adds this to `result.json`:

```json
"usage": {"cpu_s": 6662.1, "user_s": 6590.4, "sys_s": 71.7, "cores_avg": 17.9, "mem_peak_bytes": 12020000000,
          "pids_peak": 44, "mem_events": {"max": 0, "oom_kill": 0}, "source": "cgroup"}
```

- `cores_avg` is `cpu_s / wall_s`. `mem_peak_bytes` counts page cache, as the limit does. The label must say so (§6).
- When the runner writes the result itself (`oom`, `timeout`, `interrupted`), the shim is gone. The runner reads the
  unit's `ControlGroup` while the unit is still loaded (it is never `--collect`ed) and writes the same block with
  `source: "runner"`.
- The runner records `cgroup` in `state.json` when a job starts, from
  `systemctl --user show mu-job-<id>.service -p ControlGroup`. Its path is never guessed.
- `mu fetch` copies `usage` into `runs/<id>.json`. `list` and `status` add `usage` for finished jobs.

**A pointer to the running job (phase 1).** `hello`, `list` and the runner's `step()` all call `scan_jobs()`. It reads
and checksums every job record, and stage 1 never deletes a job, so the cost grows with history. `stats` must not
call it. The runner therefore writes `/srv/mu/current.json` (`job_id`, `cgroup`, `started_epoch`, `limits`) when it
starts a job and removes it when the job ends. It is a view, not a record: if it is missing or stale, `stats` says
`running: []` and the next `list` corrects it.

**`stats` (phase 1).** One JSON object, never `scan_jobs()`. In phase 1, `stats` measures on demand:

- It reads `/proc/stat` and the current job's `cpu.stat` twice, 0.25 s apart, to get rates.
- It also reads `/proc/meminfo`, `/proc/loadavg`, `/proc/pressure/*` (if present), `statvfs` of `/srv/mu`,
  `systemctl --user is-active mu-node` and the current job's memory files.

In phase 2 it returns `metrics/now.json` instead, which is instant. The answer:

```json
{"ok": true, "t": 1794651234.2, "node": "office-pc", "rev": 812,
 "host": {"cpu_pct": 93.1, "per_core": [97, 95, …], "load": [18.2, 17.9, 15.0],
          "mem": {"total": 24000000000, "avail": 8100000000, "cached": 2100000000, "shmem": 400000000},
          "psi": {"cpu_some10": 41.2, "mem_some10": 0.0}, "disk": {"free_gb": 89, "min_free_gb": 20}, "uptime_s": 86400},
 "running": [{"job_id": "20261114-101500-k3f9qa", "cpu_cores": 17.8, "anon": 9810000000, "file": 2100000000,
              "shmem": 400000000, "peak": 10300000000, "max": 23622320128, "pids": 44, "log_size": 48211}],
 "services": {"runner": "active"}}
```

**The sampler (phase 2).** The runner already wakes every second. Every fifth tick, outside the queue lock, it reads
the files above (about 10 small reads, under 5 ms):

- While a job runs, it appends one line to `jobs/<id>/metrics.jsonl`:
  `{"t","cpu","anon","file","shmem","peak","max","pids","rd","wr","psi_mem"}`. That is about 200 B per line, or
  1.7 MB for a 12-hour job. It stays with the job's folder.
- It appends a host line to `metrics/host-YYYYMMDD.jsonl`: every 5 s while a job runs, every 30 s when idle. Files
  older than 7 days are deleted.
- It replaces `metrics/now.json` (rename, no fsync: it is a view) with the newest samples, the queue counts and a
  `rev` that increases whenever any job changes state. A client polls `list` only when `rev` moved.

Lines are not fsynced. A reader treats a final line without `\n` as not yet written. Losing the last seconds on a
power cut costs nothing.

**New verbs** (stage-1 grammar; JSON or bytes; exits 0/64/66/70, never 1, 2 or 255; one journal line per call):

| Request | Answer | Phase |
|---|---|---|
| `stats` | the object above | 1 |
| `hello` | adds `features: ["stats","usage",…]` | 1 |
| `list`, `status` | add `usage` for finished jobs; `list` adds `rev` (phase 2) | 1 |
| `events id=<job>` | the job's `events.log` (start, attempt, result, cancel asked), capped at 64 kB | 2 |
| `metrics id=<job> offset=<B> [max=<N>]`, `metrics day=<YYYYMMDD> offset=<B>` | JSONL bytes from `B`, the same code path as `log` | 2 |
| `watch interval=<2..60> [max=<s>]` | a `stats` line now and then every interval; the node ends it after `max` (default and cap 600 s) | 2 |
| `bench` | the newest `state/bench/*/results.json` of the node's default version, capped | 2 |
| `progress id=<job> offset=<B>` | JSONL bytes of parsed progress | 3 |

`ARG_RULES` gains `interval` (`^[0-9]{1,2}$`) and `day` (`^[0-9]{8}$`). The verbs add nothing an attacker could not
already get: a paired key is already code execution as `mu`, and `list` without `project=` already shows every job
on the node. What is new is on the workstation: more node-controlled text reaches a browser (§4.6).

**Still to confirm on the PC** (§7.0): the job unit's `ControlGroup`; that `memory.peak` and `pids.peak` exist (the
kernel needs to be 5.19 or later, and WSL's is 6.x); PSI (`CONFIG_PSI`); that `mu` can read `/proc/<pid>/io` for
its own processes (`io.stat` is absent, because the user manager is delegated only `cpu memory pids`).

### 4.3 Getting node data to the Mac

- **One worker thread per paired node,** each with its own `jobs.Conn` and the **same** ControlPath as the CLI. A slow
  or offline node never blocks the others.
- **Cost.** A reused connection costs about 0.9 s per call to the PC through the relay. A new one costs about 5 s plus
  a Keychain read. While the dashboard keeps the master warm, Claude's own `mu status` / `mu log` also take 0.9 s
  instead of 5 s. `MaxSessions 10` leaves room.
- **Modes.**

| Situation | Node traffic, phase 1 | Phase 2 |
|---|---|---|
| A tab is visible | `stats` every 3 s; `list` every 15 s; `log offset=` every 2 s while a log is open | `watch interval=2` (one session); `list` only when `rev` moves; `metrics` from stored offsets |
| No visible tab, a job unfinished | `list` every 60 s (enough to notice the end) | same |
| Nothing unfinished, no tab | nothing; the master expires after `ControlPersist 300`; the server exits after 15 min | same |

- **Errors use the CLI's classes.**
  - Exit 4 (relay unreachable, node offline, link lost): grey "offline since …", retry after 5, 10, 20, then 60 s.
  - Exit 5 (host key, key revoked, relay credential, protocol): red, the CLI's message, **no automatic retry**. There
    is a "retry" button.
  - Exit 6: amber.
  - A locked Keychain: "Keychain locked", back off to 5 min.
- **Allowlist.** The server's node worker may send only `hello list status log stats metrics events progress watch
  bench`. It never sends `submit`, `cancel`, `fetch` or `receive-pack` in phases 1–3. A test proves it (D10).
- **Reconcile** on start, after a wake (wall clock jumped more than 3× the interval against `time.monotonic()`) and
  after a reconnect:
  1. `stats`;
  2. `list` (no project filter);
  3. upsert the jobs;
  4. `metrics` from each stored offset (phase 2).
- **Clocks.** Node samples are stored in node time. The offset is estimated from `hello.time` against the call's
  midpoint, and shown when it exceeds 2 s.

### 4.4 The local server and its lifecycle

- **Process.** `mu dashboard serve` → `cli.py` → `src/mu/dashboard.py` on the default version, like the other
  job-layer commands. It adds the env's `site-packages` to `sys.path` by hand to import psutil, so no `.pth` file
  runs.
  - Threads: HTTP (one per open stream), one Mac sampler, one worker per node, and one SQLite writer behind a queue
    (phase 2).
  - Phase 1 keeps history in memory: ring buffers of 1 h at 2–5 s.
- **Port.** Fixed per user, `47000 + uid % 1000`, so two users of one Mac differ and bookmarks keep working. If the
  port is taken, it falls back to a free port and records it in `$MU_HOME/run/dashboard.json` (0600: `pid, port,
  started, version`).
- **One instance.** `flock` on `run/dashboard.lock`.
- **Started by:**
  - `mu dashboard open`;
  - `mu submit`, `mu status`, `mu log`, `mu fetch`, when the dashboard is on. The command spawns
    `mu dashboard serve` detached (`start_new_session`, stdio to `logs/dashboard.log`, capped) and does not wait.
    The cost is a few ms, and any failure is ignored.
- **Not started by** `mu run` or the dispatcher. Every CAD build goes through them, and they must stay fast and
  side-effect-free.
- **Stops** after 15 min with no connected tab, no unfinished job on any node and no job command (job commands touch
  `run/dashboard.activity`). It also stops when idle and `$MU_HOME/default` has changed, so a new release takes over
  at the next start (the runner's rule). It never holds `state/busy` and never prevents sleep.
- **Phase 3.** A LaunchAgent `systems.martand.mu.dashboard` with a `Sockets` listener on `127.0.0.1:<port>`, no
  `KeepAlive`. launchd listens. A browser connection or `launchctl kickstart` from a job command starts the server,
  which takes the socket through `launch_activate_socket` (via `ctypes`). The URL then always answers. A Linux
  workstation later uses a systemd user `.socket`. Compute nodes never run a dashboard.
- **Commands.**
  - `mu dashboard`: status (`--json`).
  - `mu dashboard open`: start if needed, open the browser.
  - `mu dashboard on|off`: remembered, like auto-update. CI is always off.
  - `mu dashboard url`, `mu dashboard stop`, `mu dashboard reset-token`.
  - `serve`: internal.
- **API.** Every page has a JSON twin under `/api/…`:
  - `/api/overview`, `/api/jobs`, `/api/jobs/<id>`, `/api/jobs/<id>/log?offset=`;
  - `/api/nodes`, `/api/nodes/<name>`, `/api/mac`, `/api/compare?task=`;
  - `/api/stream`: event-stream text over a streamed `fetch()`, because `EventSource` cannot send the token header.
    It falls back to polling every 5 s.

  Job ids are checked against `proto.JOB_ID` and node names against the node-name rule. No route takes a path.

### 4.5 Storage

| Where | What | Kept |
|---|---|---|
| Node `jobs/<id>/` | `result.json` + `usage` (sealed); `metrics.jsonl`, `progress.jsonl` (phase 2–3) | with the job |
| Node `metrics/` | `host-YYYYMMDD.jsonl`, `now.json` | 7 days |
| Node `current.json` | pointer to the running job | while it runs |
| Mac `runs/<id>.json` (project) | run record + `usage` | committed, as today |
| Mac `$MU_HOME/jobs.index` (0600) | one line per submit: `job_id, node, project root, submit_started, accepted, fetched` | 1 year |
| Mac `$MU_HOME/local-runs/YYYYMM/<id>.json` (0600) | one record per `mu run` (§6.2) | 90 days or 20,000 records |
| Mac `$MU_HOME/dashboard/cache.sqlite` (0600, WAL, folder 0700) | tables `nodes`, `jobs`, `job_samples`, `host_samples`, `progress`, `events`, `offsets` | host samples: raw 48 h, then 1-min mean/max 90 days; job series thinned to 2,000 points when the job ends (the full series stays on the node); events 1 year |
| Mac `$MU_HOME/run/dashboard.token` (0600, folder 0700) | 32 random bytes | until `reset-token` |

`jobs.index` exists because `projects.txt` is written only by `mu link` and is read by auto-update's gc. Adding
submitted projects there would change what gc keeps. The cache is disposable. Deleting it loses only the Mac's own
host history.

### 4.6 Security

| Threat | Measure |
|---|---|
| Other machines | bind `127.0.0.1` only (not `0.0.0.0`, not `::`); print `127.0.0.1`, not `localhost` |
| DNS rebinding | `Host` must be `127.0.0.1:<port>` or `localhost:<port>`, else 403 |
| Other web pages (CSRF, reading data) | every `/api/` request needs `X-Mu-Token`. A custom header forces a CORS preflight, which the server never approves. There are no CORS headers. Any later state-changing request also needs a same-origin `Origin` and `Sec-Fetch-Site: same-origin` |
| Other local users | 127.0.0.1 is shared, so the token is required for every data request. Static files hold no data |
| Getting the token to the browser | `mu dashboard open` writes `run/dashboard-open.html` (0600), a redirect to `http://127.0.0.1:<port>/#t=<token>`, and opens that **file** (Jupyter's method). argv holds only a path. The page moves the fragment into `localStorage` and removes it with `history.replaceState`. Fragments never reach a server or a `Referer`. The token is never printed, never in `--json`, never in a log. `mu submit` prints the URL without it |
| Hostile node text (any job can write its name, log, notes, progress lines, files that `hello` reads) | render with `textContent` only, never `innerHTML`; strip control and escape sequences (`proto.clean_text`); cap sizes; parse numbers as numbers. CSP: `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`, plus `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer` |
| Secrets | the API returns an allowlist of fields. The server never reads the Keychain, `relay.auth`, keys, invites or `node.conf` beyond `name`, `added` and the host-key fingerprint. It reaches nodes only through `jobs.Conn`, whose proxy subprocess reads the relay password, so the server process never holds it. The relay admin API is not used |
| Actions | phases 1–3 are read-only, so an XSS that slipped through could read job data but change nothing. Cancel (phase 4, only if the owner wants it): `POST`, token, Origin check, a confirmation naming the job, node and state, and the same code as `mu cancel`. Never fetch |
| Notifications (phase 3, opt-in) | `osascript` with the text passed as arguments (as `au_notify` does), the job name cleaned and capped |

### 4.7 Terminal and Claude Code

- `mu submit` prints one more line: `dashboard: http://127.0.0.1:47501/jobs/<id>  (mu dashboard open)`. `--json`
  adds `"dashboard": {"url": …, "running": true|false}`. Neither contains the token.
- `claude/CLAUDE-toolchain.md` gets a paragraph: mention the job's dashboard link once when a remote job starts. Run
  `mu dashboard open` only when the user asks. Claude keeps using `mu … --json` and never reads the dashboard's API.
- Every object on the page shows the `mu` command that gives the same data, with a copy button (`mu log <id> -f`,
  `mu fetch <id>`, `mu cancel <id>`, `mu compute status office-pc`).

## 5. Screens

Top bar: `μ  Overview · Jobs · Nodes · This Mac · Compare` on the left. On the right: a connection pill
(`1 of 1 nodes online`), a theme toggle and `updated 2 s ago`. `/` focuses search; `?` lists shortcuts (`j`/`k`, `g o`).

Rules for every screen:

- Every number shows its age. A value older than 15 s turns grey with "as of 10:42:07".
- Charts never interpolate across a gap.
- Offline is a normal state, not an error.
- Never colour alone: an icon and a word go with it.
- Monospace with tabular figures for ids and numbers.
- Light and dark themes; `prefers-reduced-motion`; a data-table toggle on each chart.
- Each screen below names its data sources and the phase that fills it.

### 5.1 Overview

```
 μ  [Overview] Jobs  Nodes  This Mac  Compare                 ● 1 of 1 nodes online   ◐   updated 2s ago
 ───────────────────────────────────────────────────────────────────────────────────────────────────────
  MACHINES
  ┌ ● This Mac (local) ──────────┐  ┌ ● office-pc  via relay · call 0.9 s ─────────┐
  │ CPU  38%   ▁▂▃▅▃▂▂▃          │  │ CPU  93% (WSL VM) ▇▇█▇▇█▇█                    │
  │ MEM  9.1 / 16 GB · pressure ok│  │ MEM  14.2 GB used of 22 GB job limit         │
  │ 1 local run: pvbatch  2m     │  │ running frame-fea · 1 queued · μ v2026.11.8 = │
  └──────────────────────────────┘  └──────────────────────────────────────────────┘
  NOW                                                          NEEDS ATTENTION
  ▶ k3f9qa  frame-fea  on office-pc   18m04s  17.8 cores        ! ab12cd  oom  peak 21.8 of 22 GB  (11:02)
    stage: solving (no progress parser yet)  [copy mu log]      ! office-pc last stop: unclean (11-13)
  RECENT                                                        TODAY
  ✓ x9d1ab cfd-cavity  office-pc 41m   ✓ local slice-print 2m   7 jobs · 31 CPU-h · 82 % on office-pc
```

Sources:

- Machine cards: `stats` and psutil (phase 1).
- Needs attention:
  - `list` states `oom`, `timeout`, `failed`, `interrupted` in the last 24 h;
  - `hello.last_stop`, `disk.free_gb < min_free_gb`, a μ version different from the Mac's, `autoupdate.last_result`;
  - all phase 1.
- Progress text: phase 3. Until then, elapsed time and an indeterminate bar.
- "Today": `usage` (phase 1), plus local runs (phase 2).

### 5.2 Jobs

```
 Jobs   [/ search]  state: all ▾  node: all ▾  project: all ▾  from: this Mac ▾                       12 jobs
 ───────────────────────────────────────────────────────────────────────────────────────────────────────
  ACTIVE
  ▶ running    k3f9qa  frame-fea     jatayu  office-pc            18m04s   12.0 GB*  ████████▉ 17.8/20
  … queued #1  m7c2xz  frame-fea-v2  jatayu  office-pc   waits 18m       —         —
  FINISHED
  ✓ succeeded  x9d1ab  cfd-cavity    bench   office-pc            41m12s    6.0 GB*  ████████▋ 17.1/20
  ✓ succeeded  L-3ka9  slice-print   bench   This Mac              2m03s    0.4 GB†  ██▏        2.1/8
  ✕ oom        ab12cd  frame-fea     jatayu  office-pc          2h10m47s   21.8 GB*  ████████▊ 18.0/20
       oom: the job used more memory than its 22 GB limit
  ⊘ cancelled  q4wz8e  mesh-study    jatayu  office-pc             0m31s    —        —
  * cgroup peak, includes file cache    † sum of process RSS, sampled
```

- Rows come from `list` (no project filter) on each node, plus `local-runs/` on the Mac (phase 2).
- "From" filters by the key's name, so other workstations' jobs on a shared node can be hidden. Only this Mac's jobs
  map to a project folder (`jobs.index`). Others show the node's `project_id`.
- Peak memory and cores come from `usage`, so only jobs finished after the phase 1 release have them. Older jobs show
  `—`. A running job's figures come from `stats`.
- Hovering a row shows copy-id and `mu status <id>`.

### 5.3 Job detail

```
 ← Jobs   frame-fea   ▶ running   20261114-101500-k3f9qa [copy]                       office-pc · via relay
 jatayu @ 4f2a91c (dirty)   μ v2026.11.6   attempt 1   key avi-mac   started 10:15:22   elapsed 18m04s
 limits: 22 GB · 12 h · 20 cores        [copy mu log -f]  [copy mu fetch]  [copy mu cancel]
 (!) re-run after an unclean stop of office-pc                                ← the node's note, when present
 [Overview]  Log  Resources  Results  Record  Compare
 queued 10:15:20 ▪──▪ running 10:15:22 ───────────────────────────────────────────────────────── now
 ┌ LOG ── following ── 48.2 kB ─── [pause] [wrap] [find] ┐  ┌ RESOURCES ── last 18 min ─────────────────────┐
 │ == μ job 20261114-101500-k3f9qa attempt 1 …          │  │ cores    ▇▇▇█▇▇▇█▇   17.8 / 20                 │
 │ meshing … 412 331 nodes                              │  │ memory   ▃▄▅▅▆▆▆▇▇   9.8 GB anon+shm           │
 │ solving …                                            │  │          ┈┈┈┈┈┈┈┈┈┈  22 GB limit               │
 └──────────────────────────────────────────────────────┘  │ cache    ▁▂▂▂▃▃▃▃▃   2.1 GB (reclaimable)      │
                                                           └───────────────────────────────────────────────┘
```

| Tab | Phase 1 | Later |
|---|---|---|
| Header, timeline | `status`, `list`, `.mu/jobs` record (head, dirty, pin) | `events` gives every transition and attempt (2) |
| Log | `log offset=` every 2 s while open; follow/pause, wrap, find, "jump to first error"; text only | — |
| Resources | live from `stats` while the page is open; nothing earlier | the full series from `metrics.jsonl`, with no gap across Mac sleep (2) |
| Progress | elapsed only | parsed stage/fraction/residuals (3) |
| Results | declared outputs from the result: path, size, hash; fetched or not | JSON as tables, images inline, residual plots (3); 3D (4) |
| Record | `runs/<id>.json` if fetched: head, tree, dirty, snapshot, toolchain, node hardware, argv, cwd, limits, usage | — |
| Compare | — | the same task on other machines, and run-vs-run (2, 4) |

### 5.4 Nodes

```
 Nodes › office-pc     ● online · via relay · last call 0.9 s, 2 s ago     μ v2026.11.8 (this Mac v2026.11.8 =)
 Intel Core Ultra 7 265K · 20 cores · WSL2 VM 23.6 GB · jobs ≤ 22 GB · runner active · last stop clean · auto-update: failed (!)
 CPU   93 %   cores ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▅          ┐
 MEM   14.2 GB used · 8.1 GB available · 2.1 GB cache  ├ 1 h, one shared cursor
 DISK  89 GB free on C: (min 20)                    ┘
 QUEUE ▶ k3f9qa frame-fea 18m    … m7c2xz frame-fea-v2 (next)
 LINK  connected 08:02 · 3 drops today (from the chisel log)
 Figures are the WSL2 VM's; Windows' own load on this PC is not visible.
```

Sources:

- Live figures and charts: `stats` (phase 1, 1 h in memory). Host history to 7 days: phase 2.
- Header: `hello` (protocol, versions, hardware, disk, `last_stop`, runner, auto-update).
- Link history: `logs/compute-<node>.log` on the Mac, free.

When the node is offline, the card is greyed with its last values, the reason class and the CLI's `next` text. Retry
copies `mu compute status office-pc`.

### 5.5 This Mac

The Nodes layout, fed from psutil:

- CPU per core, shown as 4 P + 4 E on an M1;
- memory used and available, plus pressure from `sysctl kern.memorystatus_vm_pressure_level`, and swap;
- battery and power;
- free disk under `$MU_HOME`.

Below it are the local runs: running ones from `run/local/<pid>.json` (phase 2), followed live with psutil, and
finished ones from `local-runs/`. The thermal state (`pmset -g therm`) comes in phase 3, only if it costs little.

### 5.6 Compare and Performance

See §6. Phase 2 for machine comparison, phase 4 for run-vs-run comparison.

### 5.7 Later screens

- **Projects** (phase 4): pin, last commit, result series across commits (mass, max stress, tip deflection).
- **History**: a daily compute-hours bar chart by machine.
- **States** on every screen:
  - loading shows skeleton rows;
  - an empty screen shows the first command to copy (`mu submit --on office-pc -- …`);
  - refused (exit 5) shows a red banner with the CLI's words and "never retried";
  - "reconnecting…" after a wake;
  - a missing parser falls back to the raw log.

## 6. Performance comparison: Mac vs node

### 6.1 The view

```
 Compare › Performance    task: [frame-fea  python frame/fea.py --mesh 2.0 ▾]    toolchain v2026.11.8
 inputs: jatayu @ 4f2a91c, clean on both ✓            (a dirty or different commit is shown, never grouped silently)
                 wall      time to answer   CPU time   cores used          peak working mem   kernel peak   runs
 office-pc       6m12s     7m40s            1h51m      17.9 of 20  ████▍   9.8 GB             11.2 GB *     3
 This Mac        41m05s    41m05s           2h38m       3.9 of 8   ██      5.1 GB †           3.0 GB ‡      1
 office-pc is 6.6× faster on wall time, 5.4× faster to an answer (upload, queue and fetch included)
 per run  ●●● office-pc   ○ Mac      wall time by commit ──────────────────────────────
 calibration (mu test, same toolchain):  cfd-cavity 6.1×  fea-cantilever 4.8×  cad-solid 0.9×
  * memory.peak of the job's cgroup, includes file cache    † sum of process RSS, sampled every 1 s
  ‡ ru_maxrss: the largest single process
```

**Which runs are "the same task".**

- The key is the job name (`--name`, or a local run's `--label`), `argv`, `cwd` relative to the project root, and the
  toolchain version.
- Runs are **identical** only when both came from a clean tree at the same `head`. A remote job's `tree` hash comes
  from its snapshot. A local run records `head` and `dirty` only, because hashing a dirty tree on every `mu run` is
  too slow.
- Anything else is shown as **similar**, with the difference named.
- The benchmark suite (`benchmarks/`: cfd-cavity, fea-cantilever, cad-solid, aero-vlm-wing, slice-print, cam-plate)
  is the calibration. It is identical by construction. The node's results arrive through the `bench` verb, and the
  Mac's are in `state/bench/`.

### 6.2 What μ must start recording

| Figure | Node job (source) | Local `mu run` on the Mac (source) | Phase |
|---|---|---|---|
| wall time | `wall_s` (exists) | `wall_s`: monotonic clock in the shim | 1 / 2 |
| time to answer | `jobs.index`: `fetched − submit_started` | = wall | 2 |
| queue wait | `started_at − queued_at` (exists) | 0 | 1 |
| CPU time (user, system) | cgroup `cpu.stat`, covers the whole tree, exact | `os.wait4` → `ru_utime + ru_stime` of the child, which covers reaped descendants; orphans (e.g. a daemonised `cad` server) are missed and flagged | 1 / 2 |
| cores available | `limits.cores` (`MU_JOB_CORES`) and `cores_logical` | `os.cpu_count()`, plus P/E split from `sysctl hw.perflevel0/1.logicalcpu` | 1 / 2 |
| mean cores used, utilisation | `cpu_s / wall_s`; `/ cores` | same | 1 / 2 |
| kernel peak memory | `memory.peak` (includes page cache) | `ru_maxrss` (bytes on macOS), the largest single process, not the tree | 1 / 2 |
| peak working memory (the figure to compare) | max over samples of `anon + shmem` (5 s) | max over samples of the tree's summed RSS (psutil, 1 s, a thread in the shim) | 2 |
| processes | `pids.peak` | number of processes seen | 1 / 2 |
| memory limit events | `memory.events` `max`, `oom_kill` | — | 1 |
| I/O bytes | sum of `/proc/<pid>/io` if readable (§4.2) | psutil `io_counters` is not available on macOS: none | 2 |
| hardware, toolchain | `node`, `toolchain` (exist) | `sysctl hw.model, machdep.cpu.brand_string`, `MANIFEST.json` | 1 / 2 |
| provenance | `head, tree, dirty, snapshot` (exist) | `head, dirty`, project root | 2 |

**The local recording shim (phase 2).**

- Today `mu run` is `exec "$@"` (`bin/mu-toolchain` line 18) and leaves no trace. It becomes
  `exec "$PY" -I -S <recipe>/src/mu/runrec.py "$@"`.
- The shim writes `run/local/<pid>.json` (so a running dashboard can follow it), starts the command in the same
  process group with the same stdio and environment, and ignores SIGINT and SIGQUIT while it waits. The terminal
  delivers them to the child. It forwards SIGTERM and SIGHUP.
- It samples the tree every second in a thread, then calls `wait4`.
- It writes `local-runs/YYYYMM/<id>.json` (`argv` capped at 4 kB, `cwd`, project root, `head`, `dirty`, version,
  times, exit code or signal, `usage`).
- It exits with the child's code, or `128 + signal`.
- Kill switch: `MU_RUN_RECORD=0`. The node's job shim sets it, because the cgroup already measures.
- Expected overhead: about 40 ms of Python start plus 23 ms for the psutil import. Phase 2's test D24 bounds it.

**Honesty rules on the page.**

- The kernel peaks (cgroup with cache, largest process) are never compared with each other. Only "peak working
  memory", which is sampled on both machines, sits in the comparison column, with its footnote.
- An M1 "of 8" cores hides that 4 are efficiency cores. The P/E split is shown.
- Node figures are the WSL VM's. If Windows was busy, the page cannot know.

## 7. Phases, acceptance tests and effort

Effort is for Claude Code doing the work with the owner reviewing, in the style of the job layer: tests with a fault
injected that must make them fail ("FAILS-OK"), unit level in `tools/test-jobs/`, end to end in the existing
`job-layer.yml` (fake relay, a node on the Linux runner). Browser checks use headless Chrome, which the macOS and
Ubuntu GitHub runners already have.

### 7.0 Before anything: one probe on the PC (half a day)

`mu submit -- python probe.py` reports:

- the unit's `ControlGroup`;
- whether `memory.peak`, `pids.peak` and `/proc/pressure/*` exist;
- whether `/proc/<pid>/io` is readable as `mu`;
- `systemctl --user is-active mu-node` as `mu`;
- the cost of one sampler pass.

Also find out why the node's auto-update reports `failed` (`hello.autoupdate.last_result`). New verbs reach the node
only through auto-update or `mu update` there, and the plan depends on that path working.

### 7.1 Phase 1: a useful first version (3–5 working days)

**Scope.**

- **Node:** `usage` in `result.json` (shim and runner); `cgroup` in `state.json`; `current.json`; the `stats` verb;
  `hello.features`; `usage` in `list` and `status`.
- **Mac:** `mu fetch` copies `usage` into the run record.
- **Dashboard:**
  - `mu dashboard` with `open|on|off|url|stop|reset-token`, `src/mu/dashboard.py`, static files in
    `recipe/share/dashboard/`, uPlot vendored with its SHA-256 recorded;
  - Overview, Jobs, Job detail (header, log, live resources, record), Nodes, This Mac;
  - in-memory history (1 h);
  - lazy start from job commands, idle exit;
  - the token, CSP and allowlist;
  - `mu submit` prints the URL;
  - the paragraph in `CLAUDE-toolchain.md`.

About 150 lines on the node, 700 in the server, 700 of HTML/JS/CSS and 400 of tests.

| # | Check | Fault put in → must fail |
|---|---|---|
| D01 | A job that allocates 1 GB and keeps 2 cores busy for 10 s: `usage.mem_peak_bytes ≥ 1 GB`, `cpu_s` 20 ± 15 %, `cores_avg` ≈ 2 | `usage-from-shim` (read the shim's own rusage, not the cgroup) → peak < 100 MB |
| D02 | An `oom` job (runner-written result) still carries `usage` with `mem_events.oom_kill ≥ 1`; `mu fetch` copies `usage` into `runs/<id>.json` | `drop-usage` → absent |
| D03 | `stats` answers valid JSON, exit 0, with the running job and its cores ≈ the job's; idle → `running: []`; with 2,000 synthetic finished jobs the node-side time stays < 400 ms | `stats-scan` (call `scan_jobs`) → over 400 ms |
| D04 | Against a node without `features` (previous release), the dashboard lists jobs and says "update μ on office-pc for live statistics" | `assume-features` → error page, no jobs |
| D05 | The server is reachable on 127.0.0.1, refused on the machine's LAN address and on `::1` | `bind-all` → LAN connect succeeds |
| D06 | `Host: evil.example:<port>` → 403 on every route | `no-host-check` → 200 |
| D07 | `/api/*` without `X-Mu-Token` → 401, with the token in a cookie only → 401, with a wrong token → 401 | `no-token` → 200 |
| D08 | The token appears in none of: every process's argv during `mu dashboard open` (30 scans), `logs/dashboard.log`, the output of `mu submit` and `mu dashboard --json` | `open-url-argv` (open `http://…#t=` directly) → found in argv |
| D09 | A job named `<img src=x onerror=…>` that logs HTML, ANSI escapes and a 1 MB line: in headless Chrome no element is created from it, nothing runs, the CSP header is present, the page stays responsive | `inner-html` → the handler runs |
| D10 | A fake node records every request while every route and the stream are exercised: only `hello list status log stats` arrive | `allow-cancel` (a route that cancels) → `cancel` seen |
| D11 | Node offline → "offline since", retries back off; exit 5 (host key) → red, zero further node requests in 5 min | `retry-refused` → requests > 0 |
| D12 | No tab, no unfinished job: the server exits after the idle time (5 s in the test); a tab or a running job keeps it | `never-exit` → still running |
| D13 | With the dashboard off, killed, or its port held by another program, `mu submit/status/log/fetch` give the same exit codes and the same `--json` (minus the `dashboard` field), and `submit` is no slower than 50 ms | `submit-waits` (wait for the server) → over 1 s or fails |
| D14 | Two `mu dashboard open` at once → one server | `no-lock` → two processes |
| D15 | With a tab open for 10 min during a job, μ's own processes on the node use < 2 % of one core; the job's `cpu_s` is within 3 % of a run without the tab | `poll-0.2s` → over budget |

**Done when** the owner opens the page during a real job on the office PC and sees the job, its node, its live cores
and memory against the limit, and both machines' load. After the job, its row shows peak memory and mean cores.

### 7.2 Phase 2: history and the Mac vs node comparison (5–8 days)

**Scope.**

- **Node:** the runner's sampler (`metrics.jsonl`, host history, `now.json`, `rev`); the verbs `metrics`, `events`,
  `watch` and `bench`.
- **Mac:** the SQLite cache with retention and reconciliation; `jobs.index`; the `mu run` recording shim and
  `local-runs/`; "This Mac" as node `local` in the data model.
- **Screens:** Resources tab from series; Compare › Performance; host history to 7 days.

| # | Check | Fault → must fail |
|---|---|---|
| D20 | A 10-min job; the dashboard killed for 5 min in the middle (stands in for sleep); after restart the job's series has no gap over 2 × the interval | `mac-samples-only` → a 5-min gap |
| D21 | `metrics` pieces joined by offset across a killed link equal the node's file byte for byte (as R21) | `metrics-from-zero` → duplicates |
| D22 | Host files older than 7 days are deleted; a job's `metrics.jsonl` stays with it; the cache thins a finished job to ≤ 2,000 points | `no-retention` → old files remain |
| D23 | Delete `cache.sqlite`: after reconciliation, the job list and every job series are the same | `cache-only-field` → a field lost |
| D24 | `mu run` with the shim: exit codes 0, 3, 130 (Ctrl-C), 143 (SIGTERM) pass through; stdout bytes identical; `python -c 'input()'` reads from the terminal; a record with `usage` is written, even when the child is killed; `MU_RUN_RECORD=0` writes none; median added time < 80 ms | `swallow-exit` → exit 0 for a failing command; `no-record` → record missing |
| D25 | Locally, 2 busy processes for 10 s with 1 GB in all: `cpu_s` 20 ± 15 %, peak working memory ≥ 1 GB (the tree, not one process) | `maxrss-as-peak` → about 0.5 GB |
| D26 | The same benchmark on both machines → one Performance row per machine; a run from a dirty tree is "similar", never grouped as identical | `match-by-name` → grouped as identical |
| D27 | `watch` ends at `max`; a client that disappears leaves no session after the cap (5 s in the test) | `no-cap` → session still open |
| D28 | The sampler costs < 5 ms per pass (median) and the runner's loop stays at 1 s ± 0.1 | `sample-under-lock` → the queue waits |

### 7.3 Phase 3: progress, always-on URL, notifications (4–6 days)

**Scope.**

- **Progress parsers, on the node, incremental and capped:**
  - `MU_PROGRESS {…}` lines, emitted by `mu.fea` and the benchmarks;
  - CalculiX `.sta` and `.cvg`;
  - OpenFOAM `Time =`, `endTime` from `controlDict`, and residuals from the log or `work/**/log.<app>`;
  - Gmsh stages.
- **The `progress` verb.**
- **`mu.fea`:** stream ccx's output to `ccx.log` as it runs. Today `fea.py:222` uses `capture_output=True` and writes
  only at the end.
- **The Mac:** a launchd socket-activated agent, opt-in macOS notifications when a job ends, and the thermal state.

| # | Check | Fault → must fail |
|---|---|---|
| D30 | Recorded fixtures (OpenFOAM cavity, ccx `.sta`, Gmsh) give the expected fractions and stages | parser off by a field → wrong fraction |
| D31 | A hostile `MU_PROGRESS` line (2 MB, invalid JSON, HTML, a fraction of 7) is dropped or clamped; memory stays bounded | `no-cap` → memory grows |
| D32 | During a 60 s FEA job, `mu log` shows ccx's output before ccx ends | `capture` → nothing until the end |
| D33 | After a reboot with `on`, connecting to the URL starts the server; `mu dashboard off` → nothing listens | `keepalive` → a server runs with no client |

### 7.4 Phase 4: results and more (1–2 weeks, owner's choice)

- Results as tables with tolerance badges, images, residual and convergence plots, mesh-convergence plots.
- three.js for STL and GLB. STEP is converted on the node.
- Run-vs-run comparison that shows only the fields that differ.
- Project result series across commits.
- Cancel with a confirmation, if the owner wants it. The tests are the same as D07–D10, plus CSRF: a cross-origin
  POST is refused.
- A systemd user socket for Linux workstations.
- An optional `/metrics` export.

### 7.5 Effort, honestly

About 2,500–3,500 new lines across the four phases, plus tests, and one vendored library (three in phase 4) to keep
pinned. The risky parts are on the node and in `mu run`, not in the web page:

- the sampler must never slow or block the runner;
- the shim must never change what `mu run` does.

Phases 1 and 2 need at least one round trip on the real PC each. Node code reaches the PC only through a release and
its auto-update, which is failing today.

## 8. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| The node's auto-update is failing (`hello.autoupdate.last_result = failed`) | New verbs never arrive; the dashboard stays in fallback mode | §7.0: fix first; the dashboard shows the node's μ version and "update μ on office-pc" |
| `memory.peak` counts page cache | An OpenFOAM job writing fields looks bigger than it is; `--mem` sized from it is too generous | Label it; compare only sampled `anon + shmem`; show cache separately |
| WSL VM figures, not Windows | Someone using the PC slows a job and the page cannot say why | Say so on the node page; GPU and Windows load later only if interop policy changes (it should not) |
| The `mu run` shim changes every local command | A regression in signals, exit codes or TTY behaviour breaks CAD work | D24; `MU_RUN_RECORD=0` kill switch; phase 2, not 1 |
| `list` cost grows with history (stage 1 never deletes jobs) | Polling `list` gets slower on the node | Phase 1 polls `list` at 15 s only while a tab is visible; phase 2 polls on `rev`; a node job-retention rule is a separate decision (Q8) |
| XSS through node text | Reads job data in the browser | Text only, strict CSP, D09; read-only bounds the damage |
| Keychain access from a detached or launchd-started process | The node worker cannot connect | Phase 1 spawns from the user's terminal session; check launchd in phase 3 (the auto-update agent runs in the same domain) |
| Scope creep: the page becomes where things are changed | Two interfaces drift; Claude cannot see what the user did | Read-only until the owner decides; every action stays a `mu` command |
| More nodes and workstations | Each workstation × node pair holds one session while visible | `watch` is one session per pair; nothing runs with no tab; the relay holds one more WebSocket per workstation, which is well within its limits |
| A port collides with another program | URL changes | Fallback port recorded in `run/dashboard.json`; `mu dashboard url` |
| Vendored JS goes stale | Security fixes missed | Pinned by hash like the rest; reviewed at each release that touches the dashboard |

## 9. Questions for the owner

1. **On by default?** Recommended: on for workstations with a paired node, started by job commands, `mu dashboard off`
   to stop. CI off.
2. **Read-only first?** Recommended: yes for phases 1–3. Should cancel ever be a button (phase 4)? Fetch never.
3. **Desktop notifications when a job ends?** Recommended: opt-in, phase 3.
4. **Record every local `mu run`?** That is needed for the Mac side of the comparison. Recommended: yes, with
   `MU_RUN_RECORD=0` to turn it off, kept 90 days on the Mac only, never committed.
5. **Comparison basis.** Mostly the benchmark suite, or real project jobs too? If real jobs, should Claude be told to
   run a calibration task on both machines now and then?
6. **History length.** Mac: raw 48 h, 1-min rollups 90 days. Node host history 7 days. Enough?
7. **Reverse tunnel, ever?** It would let Beszel or Netdata work unchanged. Recommended: no.
8. **Job retention on the node.** Stage 1 keeps every job forever, which makes `list`/`hello` slower over time. Decide
   a rule (e.g. archive finished jobs older than 90 days), separately from the dashboard.
9. **Teams later.** A node's `list` shows every workstation's jobs (names, argv). That is already true in the CLI. Is
   it acceptable on a page too?

## 10. Read in sources

- Research notes A (architecture), B (build or adopt) and C (UX), 2026-11-14.
- `README.md`; `docs/PLAN.md` §6.3–6.7, §11, §17; `docs/design/job-layer.md` (§7 gate and verbs, §8 runner and
  unit, §11 outputs and run records, §13 root, §14, §21 tests); `docs/design/pairing.md`.
- `src/mu/node.py`: `VERBS`, `ARG_RULES`, `scan_jobs`, `verb_list` (no project filter returns every job), `verb_log`,
  `step`.
- `src/mu/jobs.py`: `Conn`, the `ControlPersist 300` in `ssh_config_text`, `cmd_submit`, `cmd_fetch`, `run_record`.
- `src/mu/fea.py:222`; `src/mu/bench.py`; `src/mu/cli.py`; `bin/mu`; `bin/mu-toolchain`
  (`run) … exec "$@"`); `install/hosts/wsl2/mu-node-gate`; `toolchain/*/env.pip.txt` (psutil 7.2.2);
  `claude/CLAUDE-toolchain.md`.
- External (via B and C): the Netdata licence, Beszel agent docs, Grafana licensing, Glances, MLflow releases, uPlot,
  Chart.js, ECharts, three.js manual, the Ray dashboard, the Buildkite build page, the W&B run comparer, Open OnDemand
  active jobs. Versions marked (?) need checking on each project's release page before pinning.
