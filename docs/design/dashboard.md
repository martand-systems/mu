# μ dashboard: a local view of jobs, machines and progress

**Status: proposal, revised after review, nothing built.** Written on branch `dashboard-research` from v2026.11.8
(2026-11-14). It merges three research notes (A: architecture and data, read from the code and the live
`~/Library/Mu`; B: build or adopt; C: what the screens should be), then takes in a design review (§11 lists each
finding and what changed). Where the notes disagreed, §2.2 says which way this proposal goes and why. Every claim about
μ's code was checked against `src/mu/*.py`, `bin/mu`, `bin/mu-toolchain` and `install/hosts/wsl2/mu-node-gate`.
Nothing here has run on the office PC yet; §7.0 lists what must be measured there first, and the node side of phase 1
(phase 1b) does not start until it has.

**Revision 2 (2026-11-14, the owner's direction).** The owner wants the dashboard "a little nice" and asked for React or
Next.js, "given the amount of libraries and features we can add down the line". The front end is now a **React +
TypeScript single-page app built with Vite**, compiled in CI and shipped as static files inside each μ release; μ's
stdlib Python server still serves it and the API, with the same token, `Host` check and strict CSP. **Next.js is not
used** (§3.1). No Node.js runs on a user's machine. Phase 1 is split so work starts now: **1a** (the app, the Mac-side
server, fake nodes, the Mac's sampler; no node change) runs in parallel with the concurrent-jobs scheduler; **1b** (the
node's sampler, verbs and usage records) follows the scheduler and the node's auto-update fix (§7.1). Changed: §2,
§3.1–3.2, §4.1, §4.4, §4.6, §4.8 (new), §5.3 (new), §7, §8, §9 Q11–Q12, §10, §11.

**Revision 3 (2026-11-14, after a review of revision 2).** The review approved the shape and asked for four fixes
before the go-ahead, all taken: the CSP analysed part by part (Radix's `ScrollArea` and modal menus dropped, a CSP
table per phase for ECharts and three.js, Trusted Types required); the build-time supply chain (npm code runs with the
developer's rights once a build starts, so the Mac runs npm only through a sandboxed wrapper; the committed bundle is
CI's; the Dependabot flow spelled out); a traffic plan for today's node, which has no `watch`; and the effort, now
1a 8–11 days and phase 1 12–16. Changed: §2.1, §3.2, §4.3, §4.6, §4.6.1, §4.8, §5.3, §7.0, §7.1, §7.6, §8, §9 Q9,
§11.2 (new: which findings were taken and which were not).

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

1. **Build the server small, on stdlib Python; build the page in React.** `mu dashboard serve` runs a
   `ThreadingHTTPServer` on the default toolchain's Python (`-I -S`, like every job-layer command). The page is a
   React + TypeScript single-page app, built by Vite in CI into `share/dashboard/` and committed, so each release
   carries it (§4.8). No Node.js on users' machines, no CDN at runtime. Adopted: SQLite (stdlib, phase 2), psutil on
   the Mac (already hash-pinned at 7.2.2 in both platforms' `env.pip.txt`), and a short list of pinned, permissively
   licensed front-end libraries (§3.2): React, TanStack Query, uPlot, Radix UI's Tooltip, lucide icons, Tailwind CSS
   (build time); TanStack Table and Router from phase 2.
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
   the open log, and `list` only when the node's `rev` moves. The browser polls the local server every 2 s. A node
   without these verbs (every node until 1b reaches it) is polled with `list` on a stated budget (§4.3).
8. **Security.**
   - Binds 127.0.0.1 only and checks the `Host` header.
   - Every API call needs an `X-Mu-Token` header. The token is new at each server start, lives in a 0600 file and in
     the page's `localStorage`, never in a cookie, argv, a log or Claude-visible output.
   - Strict CSP (no `unsafe-inline`, no `unsafe-eval`, never a nonce) with Trusted Types required, so no HTML sink
     works even inside a library. Every node string is rendered as text only; React escapes text, and
     `dangerouslySetInnerHTML` and the other HTML sinks are forbidden by lint.
   - The server reaches nodes only through one wrapper that refuses any verb outside a fixed read-only allowlist.
   - The front end's npm supply chain is locked, audited, licence-checked and rebuilt in CI (§4.6.1).

### 2.2 Where the research disagreed

| Point | A | B | C | Here | Why |
|---|---|---|---|---|---|
| Does a new node verb need a root-owned gate change? | no | yes | yes | **no** | Checked: `mu-node-gate` has no verb list and ends with `exec "$MU_BIN" node serve "--key=$name" "$v" "$@"`; `node.py` `VERBS` and `ARG_RULES` decide. No change to `mu-wsl2-distro.sh` |
| Mac statistics | psutil | `vm_stat`/`sysctl`/`top` | either | **psutil, with a stdlib fallback** | psutil 7.2.2 is already in both pinned envs, so it is not a new dependency. A measured a 23 ms import (site-packages added by path, no `.pth`) and 5 ms per sample. `top -l 1` costs about a second per sample. Fallback: `os.getloadavg`, `sysctl`, `vm_stat` |
| Token transport | header + `localStorage` | HttpOnly cookie | cookie | **header + `localStorage`, rotated per server start** | Cookies are shared by every port of 127.0.0.1, so any other local server would receive the token. `localStorage` is per origin, and the origin includes the port. Rotation limits what a page served by a port squatter could read (§4.6) |
| History store | SQLite | JSONL | SQLite | **JSONL on the node (phase 1); SQLite cache on the Mac (phase 2)** | The node appends cheaply and serves by offset, like `log`. The Mac needs windowed queries, joins and rollups only once it keeps history |
| Start | launchd socket activation | explicit `mu dashboard` | lazy start on `mu submit` | **a plain LaunchAgent; `mu dashboard open` as the only alternative** | Review F5: a spawn from `mu submit` inherits Claude Code's environment and sandbox, and misses local work; socket activation solves an idle cost that does not exist |
| First version touches the node? | yes | yes (`stats`) | no | **yes: usage, the sampler, three verbs**, in phase 1b | Without node statistics the first version misses half of the request; without the sampler, `oom` and `timeout` jobs lose their peak memory (§4.2). Revision 2 releases the Mac side first (1a) because the node side waits for the scheduler and the auto-update fix |
| Front end | vanilla JS, no build | vanilla JS; Preact + htm past 1,500 lines | vanilla JS | **React + TypeScript + Vite, built in CI, static files committed** (revision 2) | The owner's direction (2026-11-14): a nicer page and room for libraries later. The notes optimised for no build step; the build now runs in CI, so users still install nothing new (§3.1) |

## 3. Build or adopt (PLAN §17)

The constraint that decides: the node's only door is ssh inside the chisel tunnel, with the gate as ForceCommand,
`restrict`, `AllowTcpForwarding no` and no shell (job-layer §7.1, §13.1, §14). That rules out every monitor that pulls
from a port on the node. A monitor whose agent dials out to a hub needs the node to reach the workstation, which is a
new inbound path and a change to the security model. None of the candidates knows μ's jobs, snapshots, pins or run
records. The transport through the gate must be built whatever the interface is.

**Version numbers in this table are unverified** unless marked "checked"; they come from the research notes' secondary
pages, and none goes into the PLAN row. The front-end libraries that are adopted were checked against the npm registry
on 2026-11-14 and are pinned in §3.2.

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
| Python web framework | **Streamlit**, **Dash**, **NiceGUI**, **Panel** | yes, but 100+ MB of Python dependencies, websockets per session, hard to keep a strict CSP | **Not adopted**; fine for a throwaway spike only |
| **Front end** (revision 2, §3.1) | **React + TypeScript + Vite**: a static single-page app built in CI | yes: static files served by μ's own server | **Adopted** (the owner's direction, 2026-11-14) |
| | **Next.js** (static export or with its Node server), Remix / React Router framework mode | the server modes need Node.js on every workstation; a static export is React with a heavier build | **Not adopted** (§3.1) |
| | **Svelte 5 / SvelteKit**, **SolidJS**, **Vue 3** | static builds would work | Not adopted: good tools, but a smaller choice of the libraries the owner wants to add later; React's are the widest |
| | **htmx**, **Preact + htm**, vanilla JS (the first draft) | no build step | Not adopted: each later phase would re-build tables, widgets and accessibility by hand; htmx moves the rendering of hostile node text into Python templates |
| Charts | **uPlot** (MIT) | bundled by Vite | **Adopt** for every time series (phase 1), in a thin component of our own |
| | **Apache ECharts** (Apache-2.0), Chart.js (MIT), Plotly.js (MIT, 3 MB+), Recharts (MIT, SVG) | bundled, loaded lazily | ECharts for the comparison bars, scatter and heatmaps (phase 2 on), only if hand-drawn SVG is not enough. Recharts and Plotly are slow or heavy for live series |
| 3D results | **three.js** (MIT) with **react-three-fiber** (MIT), `<model-viewer>`, vtk.js | bundled, loaded lazily | three.js + react-three-fiber in phase 4. `model-viewer` cannot do wireframe or sections. vtk.js only if volume fields are needed |
| Mac statistics | **psutil** | already pinned (7.2.2, checked in `toolchain/*/env.pip.txt`) | **Adopt** |
| Collector, history, server | the above | — | **Build**: the node sampler (about 200 lines, `/proc` + cgroup v2, stdlib), the verbs, the stdlib server, later a SQLite cache |

**Recommendation: build the collector, transport and server on stdlib Python (adopting SQLite and the already-pinned
psutil), and the page as a React + TypeScript app built with Vite (adopting the libraries in §3.2).** Row to add to the
PLAN §17 table once the owner accepts (no version numbers):

> | Local dashboard (`mu dashboard`) | Netdata, Glances, Beszel, Prometheus + Grafana, μ collector + Grafana or Datasette, an exporter behind a relay reverse port, Ray/Prefect/Dagster UIs, MLflow, Streamlit/Dash; for the page, Next.js, Svelte, htmx, vanilla JS | Build: a stdlib Python server and collector, and a React + TypeScript page built with Vite in CI and shipped as static files (compared 2026-11-14, `docs/design/dashboard.md` §3): every monitor needs a port on the node or a reverse path the gate forbids, none knows μ's jobs, and the transport through the gate is needed whatever the interface. Next.js would need a Node.js server on every workstation. Revisit Beszel if the relay moves to Headscale; MLflow as a later add-on fed from run records |

### 3.1 The front end: React + TypeScript + Vite, not Next.js

The owner's direction (2026-11-14): "I want this dashboard to be a little nice. I was heavily considering a React or
Next.js based dashboard, given the amount of libraries and features we can add down the line."

**React + TypeScript + Vite: yes.**

- React has the widest choice of the libraries later phases need: tables, charts, accessible widgets, 3D
  (react-three-fiber), and Claude Code writes it fluently.
- TypeScript checks the API shapes the page reads, which matters because the page renders data from several sources
  with ages and gaps.
- Vite builds a plain static bundle (`index.html`, hashed `.js` and `.css` files). It needs no server at runtime and is
  compatible with a CSP without `unsafe-inline` or `unsafe-eval`.
- React escapes text by default, which is the rule §4.6 already set for hostile node strings.

**Next.js: no.** Its value is server-side rendering, server components, file-system routing with a server, API routes
and image optimisation: features for public web apps with many users and search engines. This page has one user, on
`127.0.0.1`, with data from μ's own server.

- Using Next.js properly needs a **Node.js server on every workstation**: a second runtime to install, pin and update
  through μ's installer, and a second server whose security would have to be reviewed beside μ's Python one (the token,
  `Host` check, CSP and the node allowlist would be split across two processes). Framework servers have had severe
  advisories, for example CVE-2025-29927 (Next.js middleware authorisation bypass, March 2025) and CVE-2025-55182
  (remote code execution through React Server Components, December 2025); a static bundle has no such surface.
- A **static export** (`output: 'export'`) drops those features and leaves React with a heavier build, Next's routing
  conventions and its telemetry (on by default) to turn off.
- Remix / React Router's framework mode has the same trade-off.

**What this changes, and what it does not.**

- The Python server, the API, the token, the `Host` check, the CSP and the node allowlist stay as designed. The server
  also serves the app's static files (§4.8).
- The build runs in CI and on a developer's machine, never on a user's. The built files are committed under
  `share/dashboard/`, because μ installs from git tags (the recipe is the tagged tree, `bin/mu` `cmd_install`); a CI
  job rebuilds them and fails if they differ from what is committed (D28).
- A new supply chain (npm) enters the repo, for building only. §4.6.1 sets its rules; §8 lists the risks.

### 3.2 Front-end libraries, pinned

Few dependencies, well maintained, permissively licensed. **Pinning rule:** the newest release that was at least 14
days old on the day of pinning (cut-off 2026-10-31 for this list), so a compromised or broken release has time to be
found. Versions, release dates, licences and engine ranges were read from the npm registry
(`registry.npmjs.org/<package>`) on 2026-11-14. Sizes are bundlephobia's minified + gzipped size of the whole package;
the real cost is what the build contains, which D36 measures and bounds.

**Shipped in the page:**

| Package | Pin (released) | Newer, not yet pinned | Licence | Size (gzip) | For | Phase |
|---|---|---|---|---|---|---|
| `react`, `react-dom` | 19.3.1 (2026-10-14) | — | MIT | about 60 kB together (estimate: the unminified production file of `react-dom/client` is 110 kB gzipped) | the UI | 1a |
| `@tanstack/react-query` | 5.105.0 (2026-10-26) | 5.107.1 | MIT | 13 kB | polling `/api`, caching, deduplication, pausing in hidden tabs, retry with back-off. Set to `networkMode: 'always'`: the default pauses every query while `navigator.onLine` is false, which would freeze a page whose server is on the same Mac (D41) | 1a |
| `uplot` | 1.6.32 (2025-03-14) | — | MIT | 22 kB | every time series (machine sparklines, the job's resource chart, host history). Wrapped in our own component of about 80 lines; `uplot-react` (1.2.4, MIT) is not adopted. Its last release is from March 2025 and it has one maintainer: acceptable because only our wrapper touches it, so it can be replaced in one file (§8) | 1a |
| `radix-ui` | 1.8.1 (2026-10-12) | — | MIT | 74 kB if all of it were imported; only the parts used are bundled | accessible primitives. **1a: Tooltip only.** Phase 2: Tabs and Popover (non-modal, its default). Styled by us with Tailwind, following shadcn/ui's patterns (MIT, copied as source where useful, not a dependency; the shadcn CLI is not used in CI). **Never used:** `ScrollArea` (it renders a `<style>` element, which `style-src 'self'` blocks: native overflow with scrollbar styling in our stylesheet instead), `Toggle` (a `<button aria-pressed>` instead), and any part in a modal mode (`Dialog`, `AlertDialog`, `DropdownMenu`, `Select` and `Popover` with `modal`), whose scroll lock (`react-remove-scroll`) injects a `<style>` element when it opens. The two job-table filters are native `<select>` elements | 1a |
| `@tanstack/react-table` | 9.3.1 (2026-10-12) | — | MIT | 32 kB whole; tree-shaken | phase 2's job list with filters and search. Not in 1a: a 100-row table with three sortable columns is a few lines of our own, and v9 is a month old | 2 |
| `@tanstack/react-virtual` | not pinned: 3.14.26 (2026-11-03) is under 14 days old | — | MIT | about 5 kB | long logs and job lists, only if phase 2's caps prove too small (§5.3 `LogView` caps the log at numbers in 1a) | 2 (if needed) |
| `lucide-react` | 1.65.0 (2026-10-30) | 1.73.0 | ISC | under 1 kB per icon used | state icons (an icon and a word go with every state colour) | 1a |
| `@tanstack/react-router` | 1.170.46 (2026-10-22) | — | MIT | 31 kB | separate pages, filters kept in the URL with types, hash history. Phase 1 has one page and reads `#job=<id>` itself, so no router until phase 2 | 2 |
| `echarts` | 6.1.0 (2026-05-19) | — | Apache-2.0 | 368 kB whole; only the chart types used, in a lazily loaded chunk | Compare › Performance bars and scatter, the daily compute-hours chart. Adopted only if hand-drawn SVG is not enough; canvas renderer, tooltip only in `renderMode: 'richText'` or none (§4.6, CSP by phase) | 2 (optional) |
| `three` | 0.187.0 (2026-10-30) | — | MIT | 186 kB whole; lazily loaded | STL and untextured GLB previews (its own `OrbitControls`, `STLLoader`, `GLTFLoader`); no Draco, KTX2 or meshopt decoders (§4.6, CSP by phase) | 4 |
| `@react-three/fiber` | 9.8.2 (2026-10-14) | — | MIT | 58 kB | React bindings for three.js (peer: React ≥ 19 and < 19.4, three ≥ 0.156; both met). `@react-three/drei` not adopted | 4 |

**Build and test only (never shipped):**

| Package | Pin (released) | Newer | Licence | For |
|---|---|---|---|---|
| Node.js | 24.22.2 LTS "Krypton" (2026-10-27), with npm 11.19.1 | 24.22.3, 26.13.1 LTS | MIT | the build, in `.nvmrc`, `package.json` `engines` (with `engine-strict`), and CI's `setup-node`. On the Mac, the official tarball checked against a pinned SHA-256, in `$MU_CACHE/dev/`, used only through `tools/dashboard-npm` (§4.6.1) |
| `typescript` | 6.0.3 (2026-04-16) | 7.0.2 | Apache-2.0 | type checking (`tsc --noEmit`; Vite strips types itself). **Not 7.0**: `typescript-eslint` 8.72 declares `typescript >=4.8.4 <6.1.0`. Move when it supports 7 |
| `vite` | 8.3.5 (2026-10-21) | 8.3.8 | MIT | dev server and build (needs Node ^20.19 or ≥ 22.12) |
| `@vitejs/plugin-react` | 6.1.2 (2026-10-05) | 6.3.0 | MIT | JSX and fast refresh in the dev server |
| `tailwindcss`, `@tailwindcss/vite` | 4.3.3 (2026-07-16) | 4.3.5 | MIT | utility CSS compiled at build time into one `.css` file; the design tokens (§5.3) are its theme |
| `eslint` | 10.14.0 (2026-10-30) | 10.15.0 | MIT | lint, including the rules of §4.6.1 |
| `typescript-eslint` | 8.72.1 (2026-10-26) | 8.72.2 | MIT | TypeScript rules |
| `eslint-plugin-react-hooks` | 7.1.1 (2026-04-17) | — | MIT | hooks rules. `eslint-plugin-react` is not adopted: 7.37.5 declares ESLint ≤ 9.7; the HTML-sink ban uses ESLint's own `no-restricted-syntax` and `no-restricted-properties`. `eslint-plugin-jsx-a11y` cannot be used either: 6.10.2, its latest, declares ESLint ≤ 9; accessibility is checked by axe and D26 instead |
| `vitest` | 5.0.5 (2026-10-15) | 5.1.0 | MIT | unit tests of components and data shaping (needs Node ^22.12, ^24 or ≥ 26) |
| `jsdom` | 30.1.3 (2026-10-15) | 31.0.1 | MIT | DOM for Vitest |
| `@testing-library/react` | 16.3.3 (2026-08-27) | — | MIT | component tests |
| `@playwright/test` | 1.64.1 (2026-10-14) | 1.65.1 | Apache-2.0 | end-to-end tests in headless Chromium and WebKit, with the browser builds pinned to that Playwright version |
| `@axe-core/playwright` | 4.14.0 (2026-10-15) | — | **MPL-2.0** | accessibility checks in Playwright. File-level copyleft, test-only and never shipped: allowed by name for dev dependencies (§4.6.1) |
| `@types/react`, `@types/react-dom` | 19.3.0 | — | MIT | types (release date not checked) |
| `actions/setup-node` | v7.1.0, commit `949feb2413d6458794dcd2491c4babbbce0c15c1` (2026-10-08) | — | MIT | CI, pinned by commit like the other actions in `job-layer.yml` |

**Not verified here, checked in phase 1a:** the licences of transitive dependencies (the CI licence job does it on the
real lockfile); the real bundle sizes; that every package installs and builds with install scripts off; that the
lockfile carries the Linux and macOS native packages of the build tools (D35); that the phase 1 set runs with no CSP or
Trusted Types violation with every overlay opened (D29; the Radix analysis above is from Radix's source as known, not
yet run); the publication dates of the `@types` packages. If a part still violates the CSP, it is replaced by a native
element or our own component. **The fallback is never a nonce, a hash or `unsafe-inline`**: a nonce would make the
server rewrite `index.html` per request, and the others weaken the one control that covers code inside
`node_modules`.

## 4. Architecture

### 4.1 Picture

```
 WORKSTATION (Mac)                                               COMPUTE NODE (WSL2 "mu-compute", user mu)
 ┌──────────────────────────────────────────────┐               ┌───────────────────────────────────────────┐
 │ React SPA ── polls /api every 2 s ───────┐   │               │ mu node run (runner, systemd user unit)   │
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
is new), and that is all "push" means here. The browser loads the app's static files (`share/dashboard/`, built in CI
and shipped in the release) from the same server, then reads only `/api` (§4.8).

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
because the user manager is delegated only `cpu memory pids`). The CPU cost of one verb call is measured earlier, on
today's node, before 1a's release (§7.0), because 1a's polling of old nodes depends on it.

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
  estimated 80–150 ms of CPU per call, not measured. §7.0 measures it on today's node before 1a's release, which is
  why the plan below avoids per-second calls.
- **Traffic.** The server makes the node calls, once per node however many tabs are open.

| Situation | Node with `features` (1b and later) | Old node, no `features` (every node until 1b reaches it) |
|---|---|---|
| A tab is visible | one `watch interval=2` session per node (live host and job figures, and `rev`); `list limit=100` when `rev` moves, at most every 5 s, and every 5 min as a backstop; `log id= follow=1` while a job's log is open (one session); `metrics id= offset=` once when a job's detail opens, then the live points come from `watch` | `list limit=100` every **15 s** while the node has an unfinished job or this Mac submitted to it in the last 2 min (`jobs.index` changed), else every **60 s**; `log id= follow=1` while a log is open; `status id=` once when a job's detail opens. No live figures |
| No visible tab, a job unfinished, on mains power | `list limit=100` every 60 s, enough to notice the end | the same |
| No visible tab, a job unfinished, on battery (psutil `sensors_battery`) | `list` every 5 min; the master is allowed to expire (`ControlPersist 300`), so no 15 s keepalives run for the whole job | the same |
| Nothing unfinished, no tab | nothing; the master expires; the server sits in `accept()` | the same |

  "A tab is visible" means an `/api` request in the last 10 s; the page polls only while
  `document.visibilityState` is `visible`.

  **Cost of the old-node mode**, the only mode against the office PC from 1a's release until 1b reaches it: every
  `list` is one ssh session (several sshd `VERBOSE` lines) and one line in `/srv/mu/audit.log`, and costs 80–150 ms of
  node CPU (estimate until §7.0 measures it). At 15 s that is 240 sessions and audit lines an hour (about 50 kB of
  `audit.log`) and 20–36 s of CPU an hour (0.5–1 % of one core), only while a tab is visible and a job is unfinished.
  A job's state change reaches the page within 20 s (15 s plus the call), not the 15 s D05 asks of a node with
  `watch`. If §7.0 measures more than 150 ms per call, the 15 s step becomes 30 s before 1a ships. D39 bounds the call
  count. `audit.log` rotation ships with step 2's release (ROADMAP §1, §9 Q9).

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
- **Static files.** The server also serves the React app from the release's `share/dashboard/` (§4.8): `/` is
  `index.html`, `/theme.js` and `/favicon.svg` two fixed small files, `/assets/<name>` the hashed bundles. It builds a fixed table of those files once at start; no request
  path is ever joined to a directory.
- **API.** Plain JSON, polled. No stream endpoint, which also keeps several open tabs inside the browser's six
  connections per origin. How the app uses it: §4.8.
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
| Hostile node text (any job can write its name, log, notes, metrics lines, files that `hello` reads) | rendered as React text children only, which React escapes; `dangerouslySetInnerHTML`, `innerHTML`, `outerHTML`, `insertAdjacentHTML`, `document.write`, `eval` and `new Function` are lint errors (§4.6.1); no URL from a node becomes an `href` or `src`; the server strips control and escape sequences (`proto.clean_text`) and caps sizes; the app decodes every response with typed decoders that parse numbers as numbers and cap strings (§4.8). CSP on every response: `default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; worker-src 'none'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; require-trusted-types-for 'script'; trusted-types 'none'` (no `unsafe-inline`, no `unsafe-eval`, no nonce; Vite's production output needs none of them), plus `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer` and `Cross-Origin-Opener-Policy: same-origin`. **Trusted Types are a requirement from phase 1a**, not a trial: with no policy allowed, every HTML or script sink (`innerHTML`, `insertAdjacentHTML`, `script.src`, `eval`) throws, including inside `node_modules`, which the lint ban cannot see. Tested in Chromium and WebKit (D29); if the pinned Playwright WebKit build does not enforce Trusted Types, its run checks the CSP only and that is written in the test |
| Libraries that break the CSP | Runtime CSS-in-JS (styled-components, Emotion) and anything that injects `<style>` or inline `<script>` elements or evaluates strings is not adopted. Styles come from the one built `.css` file; React and uPlot set styles through the CSSOM (`element.style`), which the CSP allows. Radix: Tooltip only in 1a; `ScrollArea` and every modal part are never used, because they inject `<style>` elements when they mount or open (§3.2). A violation fires when the element mounts, not at page load, so D29 opens every overlay. The fallback for a violating part is a native element or our own component, never a nonce or `unsafe-inline` |
| The npm supply chain (build time only) | §4.6.1 |
| Secrets | the API returns an allowlist of fields. The server never reads the Keychain, `relay.auth`, keys, invites or `node.conf` beyond `name`, `added` and the host-key fingerprint. It reaches nodes only through `NodeReader` → `jobs.Conn`, whose proxy subprocess reads the relay password, so the server process never holds it. The relay admin API is not used |
| Actions | phases 1–3 are read-only, so an XSS that slipped through could read job data but change nothing. Cancel (phase 4, only if the owner wants it): `POST`, token, Origin check, a confirmation naming the job, node and state, and the same code as `mu cancel`. Never fetch |
| Other people's jobs on a shared node | `list` without a project filter shows every workstation's jobs, argv included. The job table's "from" filter defaults to this workstation's key; §9 Q7 is decided before a second workstation pairs |
| Notifications (phase 3, opt-in) | `osascript` with the text passed as arguments (as `au_notify` does), the job name cleaned and capped |

**CSP by phase.** What each library needs, decided now so no phase loosens the policy by accident. A row that would
need more than the policy above is a design change, reviewed like one.

| Phase | Library or feature | What it needs | Decision | CSP change |
|---|---|---|---|---|
| 1a | React 19, TanStack Query, Vite 8's output (module scripts, `modulePreload` polyfill off), Tailwind 4 (one static `.css`), uPlot (canvas; styles through the CSSOM), Radix Tooltip, lucide icons (inline SVG elements made by React) | nothing beyond `'self'`; no HTML sink | adopted as listed; checked by D29 with Trusted Types on | none |
| 1a | the theme script (§5.3) | an external classic script in `<head>` | a static file served by the fixed table; it sets one attribute, no sink | none |
| 2 | Radix Tabs, Popover (non-modal) | nothing beyond `'self'` | adopted | none |
| 2 | **ECharts** | its default HTML tooltip builds markup with inline `style=` through `innerHTML`: blocked by `style-src`, an HTML sink for node-supplied series names, and broken by Trusted Types. The SVG renderer also builds strings | canvas renderer only; tooltip `renderMode: 'richText'` (drawn on the canvas) or none, with values in our own legend as for uPlot; no `toolbox` image export; series names passed as data only | none |
| 4 | **three.js** (`STLLoader`, `GLTFLoader`) | `GLTFLoader` loads embedded textures through `blob:` URLs (`img-src blob:`, and `connect-src blob:` for `fetch`); Draco, KTX2 and meshopt decoders need `worker-src blob:` and `script-src 'wasm-unsafe-eval'` | **STL and untextured GLB only; no decoder workers and no wasm.** Geometry is fetched as an `ArrayBuffer` through the token-carrying API client, size-capped by the server, then `parse()`d; the loaders never fetch URLs themselves. A model file from a node is hostile binary input to a parser running in the page: the server caps its size, the page parses it in a lazily loaded chunk and shows "cannot preview" on any error. If textures are ever wanted: `blob:` in `img-src` and `connect-src` only, never in `worker-src`, and never wasm | none (textures: `img-src` and `connect-src` gain `blob:`) |

#### 4.6.1 The front end's supply chain

npm packages run on the developer's machine and in CI at build time, and their code ends up in the page. The page
holds job data and a token, so a malicious package could read them; it cannot reach a node, because the server's
allowlist (D17) and the read-only API bound what any script in the page can do.

**What the rules below do not stop.** `--ignore-scripts` stops install scripts only. As soon as `npm run build`,
`test`, `lint` or `dev` runs, Vite, Rolldown, Tailwind, ESLint, TypeScript, Vitest and Playwright execute with the
full rights of whoever runs them. On the owner's Mac that would include the Keychain (the relay password), the node
ssh keys and git push rights to `mu`. Against a malicious *new* version of one of those packages, the only defences are
the 14-day release age, the small number of dependencies, reading the lockfile diff before an update is merged, and
running npm on the Mac only inside the sandbox below. CI is the other place npm code runs, so CI gets no secrets.

The rules:

- **npm on the Mac only through `tools/dashboard-npm`.** A short shell wrapper, the only supported way to run npm or
  npx for `dashboard/`:
  - Node comes from the official tarball (`nodejs.org/dist/v<ver>/node-v<ver>-darwin-arm64.tar.xz`), checked against a
    SHA-256 pinned in `dashboard/node.sha256` (copied from the release's signed `SHASUMS256.txt` when the version is
    pinned), unpacked into `$MU_CACHE/dev/node-<ver>/`. It is never on the global `PATH`; no Homebrew, no nvm, no
    install script. "No Node.js on user machines" is true of the runtime: the owner's Mac has this copy only because it
    is also the developer's machine, and only for this work.
  - The environment is scrubbed (`env -i` plus `PATH`, a `HOME` under `$MU_CACHE/dev/home`, `npm_config_cache` under
    `$MU_CACHE/dev/`): no `SSH_AUTH_SOCK`, no `MU_*`, no `GIT_*`, no tokens.
  - Every npm command runs under a `sandbox-exec` profile that denies reading `~/.ssh`, `~/Library/Keychains`, the
    secret parts of `$MU_HOME` (keys, the relay credential, node configs; only `run/dev/` is readable, for the dev
    token) and git's credential stores; denies the Security server's mach service and running `/usr/bin/security`;
    and allows writes only to `dashboard/`, `share/dashboard/`, `$MU_CACHE/dev/` and the temp folder. `install`
    (`npm ci --ignore-scripts`, Playwright's browser download) may use the network; `build`, `test`, `lint`, `dev` and
    `e2e` may reach only `127.0.0.1`. `mu dashboard serve --dev` is μ's own code and is started outside the sandbox.
  - `sandbox-exec` is deprecated in its man page but still works and is still used by Apple's and others' build tools.
    If it stops working, the fallback is a separate macOS user for front-end work, or accepting the risk in writing
    here. Effort: ½–1 day in 1a, with the Node install.
- **CI holds nothing worth stealing.** `dashboard.yml` has `permissions: contents: read` (as `job-layer.yml` has), no
  secrets, no `pull_request_target`, actions pinned by commit. It cannot push, comment or release.
- **The committed bundle is CI's.** The Linux `front` job uploads `share/dashboard/` as an artifact on every run. The
  bundle that is committed is that artifact (`gh run download`), never a hand-edited tree. A one-time check in 1a
  builds on the Mac through the wrapper and compares byte for byte with CI's; only if they are identical may local
  builds be committed, and D28 still decides. After a rebase or merge the bundle is rebuilt, never hand-merged
  (`.gitattributes`: `share/dashboard/** -diff linguist-generated`, so diffs and GitHub's language count skip it).
- **Locked and exact.** `dashboard/package-lock.json` is committed. Every dependency, direct or dev, is an exact version
  (`.npmrc`: `save-exact=true`). CI and developers install with `npm ci --ignore-scripts` (also `ignore-scripts=true`
  in `.npmrc`), which fails if the lockfile and `package.json` disagree and never runs a package's install script. A
  package that cannot work without its install script is replaced, or its step is run by name in a reviewed script.
- **The lockfile holds both platforms.** A lockfile written on macOS arm64 can leave out the Linux optional native
  packages (Rolldown's, Lightning CSS's, Tailwind's oxide engine), and `npm ci` then fails in CI. Lockfile changes are
  made so that both `darwin-arm64` and `linux-x64` bindings are recorded (in 1a: find the reliable command, for
  example `npm install --package-lock-only`, and write it into `dashboard/README.md`); D35 asserts both are present.
- **Node pinned for the build.** `.nvmrc` holds the exact Node version (§3.2); `package.json` `engines` plus
  `engine-strict=true` refuse another major version; CI's `setup-node` (pinned by commit) reads `.nvmrc`.
- **Updates by pull request only.** Dependabot (built into GitHub, so no third-party app gets access to the private
  repo) opens weekly, grouped PRs for `dashboard/` and for the workflow actions, with a 14-day cooldown on new releases
  (the `cooldown` option; checked when configured). A Dependabot PR changes only `package.json` and the lockfile, so
  D28 fails on it by design, and Dependabot's runs get a read-only token. The flow:
  1. CI runs on the PR as on any other and uploads the rebuilt bundle and the size report as artifacts.
  2. A person, or Claude at the owner's request, reads each updated package's changelog and **the lockfile diff**: new
     transitive packages, changed maintainers or repositories, any new `hasInstallScript`, a jump in size.
  3. Only then they download the artifact and commit it to the PR branch; CI runs again and D28 passes.
  No workflow commits anything. A minified vendor chunk's diff is not reviewable, so the review is of the lockfile and
  the size report, not of `share/dashboard/`. Nothing updates itself.
- **Audit.** CI runs `npm audit --audit-level=high` on every dashboard PR and weekly on `main` (new advisories appear
  without a code change), and `npm audit signatures` (registry signatures and provenance). High or critical fails the
  build; a known false positive is waived in a reviewed file with a reason and an expiry date.
- **Licences.** A stdlib Python script (`tools/dashboard-licences.py`, run with `-I`) reads the `license` field of every
  package in `package-lock.json`. Shipped (non-dev) packages must be MIT, ISC, BSD-2-Clause, BSD-3-Clause, Apache-2.0
  or 0BSD. Dev-only packages may also be CC0-1.0, CC-BY-4.0, BlueOak-1.0.0, Python-2.0, or listed by name with a reason
  (`@axe-core/playwright`, `axe-core`: MPL-2.0). Unknown or missing fails (D34). The build writes the shipped packages'
  licence texts to `share/dashboard/THIRD-PARTY.txt`, the start of the `THIRD_PARTY.md` the repo's `LICENSE` promises.
- **The committed build matches the source.** CI rebuilds and runs `git diff --exit-code -- share/dashboard` (D28).
  `share/dashboard/BUILD.json` records the SHA-256 of the app's source files, the lockfile and the Node version, so the
  ordinary Python CI (no Node) also catches a source change committed without a rebuild. That Python check runs on
  every commit and is the release gate (§7.1).
- **Nothing at runtime from the network.** No CDN, no web fonts, no analytics, no source maps fetched; `connect-src
  'self'`. The page works with the Mac offline.
- **Lint bans the HTML sinks.** ESLint's `no-restricted-syntax` rejects the JSX attribute `dangerouslySetInnerHTML`, and
  `no-restricted-properties` / `no-restricted-globals` reject `innerHTML`, `outerHTML`, `insertAdjacentHTML`,
  `document.write`, `eval` and `Function`. Lint runs in CI; a planted `dangerouslySetInnerHTML` must fail it (D16).
  Lint sees only our code; Trusted Types (§4.6) cover the libraries.
- **Node-supplied strings stay hostile** everywhere in the app: decoded with caps, rendered as text, never used as a
  URL, a CSS value, a class name or a key into an object without a check.
- **The dev server never sees real data.** Vite's dev server (`npm run dev`) binds `127.0.0.1` and proxies `/api` to
  `mu dashboard serve --dev`, which serves only fake nodes (§4.8).

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

### 4.8 The front end: a static single-page app

**Shape.** A React + TypeScript app, built by Vite into static files. The Python server of §4.4 serves those files and
the JSON API from one origin (`http://127.0.0.1:<port>`). The browser never talks to anything else.

**Repository layout.**

```
dashboard/                    app source (not used at runtime)
  package.json, package-lock.json, .npmrc, .nvmrc, node.sha256, README.md
  vite.config.ts, tsconfig.json, eslint.config.js, index.html
  public/                     theme.js (the pre-paint theme script), favicon.svg: copied as-is
  src/
    main.tsx                  boot: move #t= into localStorage, then render
    api/                      client (fetch + X-Mu-Token), decoders, types, query hooks
    lib/                      formatting (bytes, durations, ages), series shaping (gaps), stale rules
    ui/                       design-system components (§5.3): Badge, Card, Meter, AgeLabel, CopyCommand, Banner, …
    features/                 machines/, jobs/, job-detail/, states/
    styles/tokens.css         design tokens, light and dark (§5.3)
  test/                       Vitest unit and component tests
  e2e/                        Playwright specs and config
share/dashboard/              the build, committed (CI's artifact, §4.6.1): index.html, theme.js, favicon.svg,
                              assets/<name>-<hash>.js|.css, BUILD.json, THIRD-PARTY.txt
src/mu/dashboard.py           the server, the API, static serving (§4.4)
src/mu/dashnode.py            NodeReader (§4.3)
src/mu/dashfake.py            fake nodes for --dev and the tests; emits the golden fixtures below
tools/test-dashboard/         Python tests of the server (D12–D15, D17–D20, D22, D27)
tools/test-dashboard/fixtures/*.json   the golden shapes of every node answer (`hello`, `list`, `status`, `stats`,
                              `watch` lines, `metrics` lines) and of every `/api` response: the one contract (D38, D40)
tools/dashboard-npm           the sandboxed npm wrapper for the Mac (§4.6.1)
tools/dashboard-licences.py   the licence check (D34)
.github/workflows/dashboard.yml   the front end's CI jobs (§7.1)
.github/dependabot.yml, .gitattributes
```

`share/dashboard/` is in the recipe that every release installs, so the server finds it at
`$MU_HOME/toolchains/<version>/recipe/share/dashboard/` (the same tree as `src/mu`). The node's copy of the recipe
carries it too and never uses it. **A 1a change to `install/install.sh`:** the recipe copy (`tar` at line 261) excludes
only `.git`, `__pycache__`, `.DS_Store` and the pip-stub builds, so an install from a working tree that has run npm
would copy hundreds of MB of `dashboard/node_modules` into every toolchain. It gains `--exclude ./dashboard/node_modules
--exclude ./dashboard/test-results --exclude ./dashboard/playwright-report`, with a check in the installer's tests.

**Build settings.** `build.outDir` is `../share/dashboard` (emptied on each build); no source maps; no inlined assets
(`assetsInlineLimit: 0`); no module-preload polyfill; a modern target (current Safari, Chrome and Firefox). The vendor
code (React, TanStack, Radix) goes in its own chunk, so a change to the app alone changes one small file in the repo.
ECharts and three.js, when they come, are loaded lazily by the pages that need them. The bundle budget for phase 1 is
250 kB of gzipped JavaScript and 30 kB of CSS on first load (D36). The browser floor is Tailwind 4's: Safari 16.4,
Chrome 111, Firefox 128.

**Serving.** At start the server lists `share/dashboard/` once into a table of regular files whose names match
`^[A-Za-z0-9._-]+$` (no symlinks, no subdirectories beyond `assets/`). `/` returns `index.html` with `Cache-Control:
no-store`; the fixed root files `theme.js` and `favicon.svg` return with `Cache-Control: no-cache`; `/assets/<name>`
returns the file with `Cache-Control: public, max-age=31536000, immutable`; anything else is 404. Content types come from a fixed map (`.html .js .css .svg .woff2 .png .txt`). `index.html` holds no data and no
token. Static files need no token (they hold no data, §4.6); every `/api/` route does.

**How the app uses the API.**

- Endpoints as in §4.4, plus `/api/session` (the server's version, start time and API version; also how the app learns
  that its stored token is stale: 401 → the no-token page).
- Every response carries `"api": 1`. The app and the server ship in the same release, so a mismatch only happens in
  development; the app then shows "reload: the dashboard was updated". The same banner appears when a lazily loaded
  chunk returns 404 after an update (Vite's `vite:preloadError` event; it matters from phase 2, when chunks are lazy).
- TanStack Query holds all server state: `/api/overview` every 2 s; the open job's `/api/jobs/<id>` every 2 s; its log
  by offset (`/api/jobs/<id>/log?offset=`) every 1 s while following; `/api/jobs/<id>/metrics` once, then live points
  from the overview. Polling stops when the tab is hidden (`refetchIntervalInBackground: false`), which is what the
  server's "a tab is visible" rule (§4.3) observes. Errors back off as the CLI classes say (§4.3). Queries use
  `networkMode: 'always'`, so they keep running when macOS reports no network: the server is on the same machine
  (D41).
- A failed `fetch` to the server itself (connection refused or reset) is its own state, distinct from a node being
  offline: "the dashboard server is not running: `mu dashboard on`" (§5.3). A React error boundary around the page
  shows a fixed message and the `mu` commands, and renders **no** error text, since an error's message can carry node
  text.
- Each endpoint has a hand-written decoder (no schema library) that checks types, clamps numbers, caps strings and
  keeps each value's time. Components get decoded, typed values only.
- **One contract.** The shape of every node answer and every `/api` response lives once, as golden JSON in
  `tools/test-dashboard/fixtures/`. `dashfake.py` emits exactly those; the Python tests check the server's responses
  against them; the app's decoders are tested on them (D38). In 1b, the real node's `stats`, `watch` and `metrics`
  output is validated against the same fixtures (D40), so fake and real nodes cannot drift apart. Before 1a freezes
  `now.json`'s shape (§4.2), the scheduler's author reviews it, because concurrent jobs change its `running` and
  `queue` parts.
- Ages are computed in the browser from each value's time and the server's clock offset (from `/api/session`), so a
  value turns stale even when polling stops returning.
- The URL holds the view: `#job=<id>` opens a job (the link `mu submit` prints). The app reads the fragment itself in
  phase 1; TanStack Router takes over with hash history when phase 2 adds pages, keeping `#job=<id>` working.

**Development mode.**

- `mu dashboard serve --dev [--scenario=<name>]` runs the same server on a separate `MU_HOME/run/dev/`, with **fake
  nodes only**: it refuses to construct a real `NodeReader`, installs no LaunchAgent, and writes its port and token to
  `run/dev/`. Scenarios (from `src/mu/dashfake.py`): `idle`, `busy` (two nodes, running, queued and finished jobs,
  including `oom` and `timeout`), `offline`, `refused`, `stale`, `old-node` (no `features`), `hostile` (the D16 job),
  `many` (150 finished jobs), `wake` (a clock jump). Each scenario moves on in time by itself so the page has something
  to show.
- `tools/dashboard-npm run dev` starts Vite's dev server on `127.0.0.1:5173` (`strictPort`); its proxy sends `/api` to
  the dev server's port and adds the `X-Mu-Token` header read from `run/dev/`. Hot reload works; the strict CSP applies
  to the built app, not to Vite's dev page (it injects an inline refresh script). On the Mac every npm command goes
  through `tools/dashboard-npm` (§4.6.1); CI calls npm directly.
- `build` writes `share/dashboard/` (a local check; the committed bundle is CI's); `test` runs Vitest; `e2e` runs
  Playwright against `mu dashboard serve --dev` serving the **built** files with the real headers.
- A states gallery (`#gallery`, present only in the dev build) shows every component in every state of §5.3 side by
  side, light and dark, for review. Because nobody sees the dev build in CI, the `e2e` job also screenshots every
  scenario in light and dark and uploads them as an artifact (not asserted): that is how the owner reviews the look.
  Storybook is optional and not planned.
- The app is built and tested entirely without a real node, which is what lets phase 1a run before the node side
  exists.

## 5. Screens

### 5.1 Phase 1: one page

One page, three parts: machine cards, the job table, and a job detail that opens below or in place of the table
(`#job=<id>`). No separate Nodes or This Mac pages, no search, filters beyond "from" and "node", shortcuts or theme
toggle in phase 1. Light and dark follow the system. The wireframes give content and order; §5.3 gives the look.

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
  office-pc for live statistics"); the dashboard server not answering; an unexpected error. The full list is §5.3.

### 5.2 Later screens

- **Phase 2:** separate Nodes and This Mac pages with host history to 7 days (node) and 90 days (Mac, rolled up);
  search, filters and shortcuts; the job timeline from `events`; Compare › Performance (§6) on the benchmark suite.
- **Phase 2b:** local runs in the job table (`This Mac` as a machine), when recording is on.
- **Phase 3:** progress (stage, fraction, residuals); results as tables and images.
- **Phase 4:** 3D results, run-vs-run comparison, Projects (result series across commits), a daily compute-hours chart.

### 5.3 Visual spec

"A little nice": calm, technical and dense, in the manner of Linear's lists, Grafana's panels and GitHub Actions' run
pages. Information first; no decoration that does not carry data. The spec is a small design system: tokens in
`dashboard/src/styles/tokens.css` as CSS variables, used as Tailwind's theme, so every component draws from the same
set.

**Tokens.**

| Group | Tokens | Rule |
|---|---|---|
| Neutral colour | `bg`, `surface`, `surface-raised`, `border`, `border-strong`, `text`, `text-muted`, `text-faint` | A grey scale (Tailwind's zinc as the starting point); light: white surfaces on a near-white page; dark: near-black page, surfaces one step lighter. Separation by 1 px borders, not shadows |
| Accent | `accent`, `accent-muted`, `focus-ring` | One blue, for links, selection and focus only |
| State colour | `running` blue, `queued` grey-violet, `succeeded` green, `failed` red, `oom` red, `timeout` amber, `cancelled` grey, `stale` and `offline` grey, `refused` red, `attention` amber | Always with an icon and a word (§5.1 rules). Text and icons ≥ 4.5:1 contrast, other marks ≥ 3:1, in both themes (axe, D26) |
| Chart colour | `series-cores`, `series-mem`, `series-cache` (the memory colour, lighter), `limit` (`text-muted`, dashed) | Same meaning on every chart. The limit line is neutral; the memory series turns `attention` above 90 % of the limit |
| Type | `font-sans` (system UI: `-apple-system`, `Segoe UI`, …), `font-mono` (`ui-monospace`, SF Mono, Menlo); sizes 12 / 13 / 14 / 16 / 20 px | No web fonts. Body 14 px, tables and figures 13 px, metadata 12 px. Ids, numbers and logs in monospace with `tabular-nums` |
| Space | 4 px steps: 4, 8, 12, 16, 24, 32 | Table rows 32 px; card padding 12–16 px |
| Shape | radius 6 px (cards, inputs), 4 px (badges) | — |
| Motion | 120 ms fades only | None under `prefers-reduced-motion`; numbers never animate |

Every chart and state colour pair (series on `surface`, state text and icon on `surface` and on its badge background)
is checked for contrast in both themes by a Vitest test that computes it from `tokens.css` (D43), because axe cannot
see inside a canvas.

**Light and dark.**

- Tailwind's `dark:` variant is bound to an attribute from day one (`@custom-variant dark
  (&:where([data-theme=dark], [data-theme=dark] *))`), not to the media query alone, so phase 2's toggle needs no
  restyling.
- The attribute is set before first paint by `theme.js`, a small external classic script in `<head>` (the CSP forbids
  the usual inline script): it reads a stored choice from `localStorage` (phase 2) or else `prefers-color-scheme`,
  sets `data-theme` on `<html>`, and follows system changes. Without it a dark-mode user would see a white flash.
- `<meta name="color-scheme" content="light dark">`, so form controls and scrollbars match.
- uPlot draws on a canvas, which CSS cannot restyle: `ResourceChart` and the sparklines read their colours from the CSS
  variables and redraw when `data-theme` changes. D42 checks both the first paint and the redraw.

**Layout grid.**

- A top bar (48 px): "μ dashboard", the overall node status ("1 of 1 nodes online"), the last update's age.
- Content up to 1,440 px wide, 24 px side margins (16 px under 768 px).
- Machine cards in a row that wraps (`minmax(320px, 1fr)`); the node cards come after "This Mac".
- The job table at full width under the cards.
- Job detail: a header block, then log and resources side by side (7 : 5 columns) at 1,200 px and wider, stacked
  below that.
- Under 768 px, table rows become two-line items (state and name; node, time and memory); nothing scrolls sideways
  except the log.

**Components (phase 1).**

| Component | What it shows | Notes |
|---|---|---|
| `TopBar` | title, nodes online, age of the last update | the only global status. The tab title carries the state ("▶ 1 running · μ", "✕ 1 failed · μ"); a μ favicon (SVG, in the build) |
| `MachineCard` | name, link (`via relay`), state badge, CPU % with a 10-minute sparkline, a memory meter (used, available, job limit), running and queued counts, μ version, "needs attention" lines, the age | one card per machine; node figures labelled "WSL VM" (§9 Q2) |
| `StateBadge` | icon + word + colour for every job and machine state | the only place state colours are applied |
| `Meter` | a value against a limit (cores 17.8 / 20, memory 9.8 / 22 GB) | text value always shown beside the bar |
| `JobTable` | state, short id, name, project, node, wait or elapsed, peak memory, cores | a plain `<table>` of our own in 1a (TanStack Table comes with phase 2's filters); three sortable columns whose headers are buttons with `aria-sort`; the "from" and "node" filters are native `<select>`s; a row opens the detail with Enter or a click |
| `JobHeader` | name, state, full id with copy, node, project @ commit, μ version, attempt, key, times (absolute, as text), limits, copy-the-command buttons | — |
| `LogView` | the log, following, with pause and wrap toggles and its size | monospace; text only. Caps: the page keeps the last 5,000 lines or 2 MB, whichever is smaller, and says "earlier lines: `mu log <id>`"; one line is cut at 10 kB with "… N bytes cut". Pause and wrap are `<button aria-pressed>`; the scroll area is native `overflow: auto` |
| `ResourceChart` | cores and memory (working, cache) over the whole run, the limit as a dashed line | uPlot; one cursor across both panes; gaps drawn as gaps; values with units in our own legend, not a floating tooltip. Colours from the tokens, redrawn on a theme change. An `aria-label` summary ("memory 9.8 GB now, peak 10.3 GB, limit 22 GB; cores 17.8 of 20") and a "show as table" button that lists the samples (thinned to one a minute) as a real table |
| `AgeLabel` | "2 s ago", or "stale · as of 10:42:07" | updates every second without being announced to screen readers; the absolute time on hover (Radix Tooltip) and in a `<time datetime>` element |
| `CopyCommand` | a `mu …` command and a copy button | "copied" for 2 s. `navigator.clipboard.writeText` is called inside the click handler itself, before any `await` (WebKit may refuse it outside the user's gesture); `http://127.0.0.1` counts as a secure context; if the call still fails, the command is selected for ⌘C and the button says so (D47) |
| `Banner` | refused, offline, reconnecting, old node, reload, server not answering | one at a time per scope (page, card, detail) |
| `EmptyState`, `Skeleton` | what to do next; the shape of the content while loading | — |

**States** (each shown in the states gallery and covered by a Playwright scenario):

| State | Machine card | Job table | Job detail |
|---|---|---|---|
| Loading | skeleton of the card's lines | five skeleton rows; nothing before 300 ms | skeleton header, empty log frame |
| Empty | — | "No jobs yet" and the command to submit one | — |
| Live | figures with ages under 15 s | live figures for running jobs | log following, chart growing |
| Stale (> 15 s) | figures grey, "stale · as of …" | running jobs' figures grey | chart stops; no line drawn into the gap |
| Offline | grey badge "offline since 10:41", retry countdown | that node's rows keep their last state, marked "as of" | as stale, with the banner |
| Refused (exit 5) | red badge, the CLI's message, "not retried", a retry button | as offline | banner with the message |
| Old node | "update μ on office-pc for live statistics" | jobs listed, no live figures | no resource chart; elapsed only |
| Reconnecting (after a wake) | "reconnecting…" for at most one reconcile | unchanged | unchanged |
| No token / stale token | the whole page is the no-token page (§4.6), with no data | — | — |
| Updated server, or a lazy chunk 404 | "reload: the dashboard was updated" | — | — |
| Server not answering (a `fetch` to 127.0.0.1 fails; not a node problem) | page banner "the dashboard server is not running: `mu dashboard on` (or ask Claude)"; every figure turns stale | rows kept, "as of" | as stale |
| Unexpected error (error boundary) | the whole page: a fixed message, `mu status` and `mu dashboard`, a reload button; **no error text**, which could carry node strings | — | — |
| No JavaScript, or an older browser | `index.html`'s static text, replaced when the app mounts: "μ dashboard needs JavaScript and Safari 16.4, Chrome 111 or Firefox 128 or newer"; the same in `<noscript>` | — | — |

**The owner's review of the look.** The `e2e` job's screenshots of every scenario in light and dark (§4.8) are the
review copy. 1a is not done until the owner has looked at them once and the changes asked for are made (½ day
budgeted).

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
`job-layer.yml` (fake relay, a node on the Linux runner). Since revision 2 the page is tested at three levels:

- **Python** (`tools/test-dashboard/`, in `job-layer.yml`): the server, token, headers, `NodeReader`, the CLI.
- **Vitest** (`dashboard/test/`): components and data shaping (decoders, formatting, stale rules, gaps in series).
- **Playwright** (`dashboard/e2e/`): the built app against `mu dashboard serve --dev` with fake nodes, in headless
  Chromium (all tests) and WebKit (D05–D07, D16, D29, D42 and D47, since Safari is the Mac's default browser), with
  axe for accessibility. Every test that checks the page is a Playwright test, and each keeps its injected fault.

### 7.0 Gates and probes (half a day to a day)

**Before phase 1a's release (3.0a: the Mac, plus one job on today's node; no node change, no visit):**

- that a process started by a LaunchAgent can read the relay password from the Keychain through `mu compute proxy`
  (the auto-update agent runs in the same domain, but has not needed it);
- **the CPU cost of one verb call on the PC** (sshd session + gate + `bin/mu` + Python): a job on today's node reads
  `/proc/stat` before and after while the Mac makes 50 `hello` and 50 `list limit=100` calls, and reports the size of
  one `audit.log` line. It needs only a job, not the scheduler, and it sets 1a's old-node polling step (§4.3: 15 s if
  a call costs up to about 150 ms of CPU, else 30 s).

Nothing else in 1a depends on the PC.

**Before phase 1b: the gate.** The node's auto-update reports `failed` (`hello.autoupdate.last_result`). New verbs and
the sampler reach the node only through a release and its auto-update (or `mu update` on the PC). Phase 1b does not
start until auto-update on the node succeeds once, and until the concurrent-jobs scheduler, which also changes the
node's runner, has merged (§7.1).

**Probe.** `mu submit -- python probe.py`, plus a few `mu compute status` calls, report:

- the unit's `ControlGroup`, and the WSL kernel version;
- whether `memory.peak`, `pids.peak` and `/proc/pressure/*` exist;
- an OOM-killed unit (a job that allocates past `--mem`): what is left of its cgroup directory and of
  `systemctl --user show` (`MemoryPeak`, `CPUUsageNSec`) right after the kill;
- whether `/proc/<pid>/io` is readable as `mu`;
- the cost of one sampler pass, with as many jobs running as the scheduler allows;
- the verb-call cost again, on the scheduler's runner, to confirm 3.0a's figure.

If the OOM probe shows the cgroup survives, the runner-sampled path stays as the fallback; if the verb call costs more
than about 150 ms of CPU, the traffic plan in §4.3 is revisited before building. The probe runs after the scheduler
has merged, so it measures the runner that 1b will change.

### 7.1 Phase 1: jobs, nodes and live load on one page (1a: 8–11 days, 1b: 3½–5 days)

Phase 1 is split so work can start now. **1a** changes nothing on the node and can be built in parallel with the
concurrent-jobs scheduler. **1b** changes the node's runner, as the scheduler does, so it follows the scheduler's
merge and the node's auto-update fix. Each ends in its own release.

**Phase 1a: the app, the Mac-side server, fake nodes, the Mac's sampler (8–11 days; starts on the owner's
go-ahead).**

- **Front-end setup (3–4 days):** `tools/dashboard-npm` with the pinned Node tarball and the sandbox profile;
  `dashboard/` with Vite, TypeScript, Tailwind, ESLint (with the §4.6.1 bans), Vitest and Playwright; `.nvmrc`,
  `.npmrc`, the lockfile with both platforms' native packages; `share/dashboard/` with `BUILD.json` and
  `THIRD-PARTY.txt`; the CI jobs below with `contents: read`; the licence script; Dependabot and its review flow;
  `.gitattributes`; the one-time Mac-versus-CI build comparison; tests D28, D34–D36 with their faults.
- **The design system and the page (3–4 days):** the tokens and the 13 components of §5.3, two themes with the
  pre-paint script, every state of §5.3 in three scopes; the one page of §5.1; the no-token, error and no-JavaScript
  pages; the states gallery and the e2e screenshots; axe-clean contrast; the owner's review round (½ day). The page is
  built against fake nodes that already answer `stats`, `watch` and `metrics`, so the node figures, the job's live
  cores and memory and the resource chart are finished and tested in 1a and only wait for real data.
- **The contract:** the golden fixtures of §4.8, emitted by `dashfake.py` and decoded by the app; `now.json`'s shape
  reviewed by the scheduler's author before it is frozen.
- **Server and CLI:** `src/mu/dashboard.py` (static serving, API, token, CSP and headers, `Host` check, the lock,
  `run/dashboard.json`); `mu dashboard`, `open`, `on|off`, `serve [--dev]`; the LaunchAgent; the Mac sampler (psutil)
  with 1 h in memory; `src/mu/dashfake.py` with the scenarios of §4.8.
- **`NodeReader`** (allowlist, own ControlPath, wake handling, battery rule, error classes), already speaking `watch`
  and `metrics` to fake nodes and, to today's real nodes, only `hello`, `list`, `status` and `log follow=1`, on the
  old-node budget of §4.3 (D39). Against the office PC, 1a therefore shows every job under its node with its state,
  its queue place, its log as it grows and its times; the node card says "update μ on office-pc for live statistics"
  (the old-node path of D04) until 1b.
- **Mac CLI:** `mu submit` writes `jobs.index` and prints the dashboard line (`jobs.py` `cmd_submit`: a small change;
  if the scheduler also changes `cmd_submit`, the later of the two rebases); the paragraph in `CLAUDE-toolchain.md`.
- **Installer:** the three `tar` excludes for `dashboard/node_modules` and Playwright's output (§4.8).
- **Before the release:** 3.0a (§7.0), which sets the old-node polling step.
- About 2,500–3,500 lines of TypeScript/TSX and CSS, 900 of Python in the server and CLI, about 2,000 of tests.
- **Done when** the owner, with nothing started by hand, opens the page during a real job on the office PC and sees
  the job under its node with its state and growing log, This Mac's live load, and the node card's "live statistics
  after the next node update"; the owner has looked at the light and dark screenshots of every state and the changes
  asked for are in; and every 1a test below passes in CI with its fault shown to fail.

**Phase 1b: the node side (3½–5 days, plus one round trip on the PC; after the scheduler and the auto-update fix).**

- **Node:** the runner's sampler (`now.json`, per-job `metrics.jsonl`, running maxima, `rev`); `cgroup` in
  `state.json`; `usage` in `result.json` (shim and runner); the `stats`, `watch` and `metrics id=` verbs;
  `hello.features`; `usage` in `list` and `status`, `rev` in `list`; journaling as in §4.2. **Re-read the scheduler's
  design and code first**: with jobs running at once, the sampler reads one cgroup per running job, `now.json`'s
  `running` list (already a list) holds several, its cost per pass grows with the number of jobs (D09's budget is per
  pass, so it is re-set for the scheduler's maximum), and the queue counts and "place in queue" follow the scheduler's
  rules. The app already handles several running jobs per node (the `busy` scenario has two).
- **Mac:** `NodeReader` uses `watch` and `metrics` with real nodes that have the features; `mu fetch` copies `usage`
  into the run record and marks `fetched` in `jobs.index`; `mu compute status --json` adds `stats`.
- **Contract:** the real node's `stats`, `watch` and `metrics` output validated against the golden fixtures (D40);
  where the scheduler changed a shape after 1a, the fixtures, decoders and page follow in this phase (½–1 day
  budgeted).
- About 350 lines on the node, 150 on the Mac, 500 of tests, and whatever page changes the scheduler's shapes need.
- **Done when** the whole of phase 1's "done when" below holds.

**CI jobs** (`.github/workflows/dashboard.yml`: `permissions: contents: read`, no secrets, no `pull_request_target`,
actions pinned by commit). `front` and `supply` run on every pull request and every push to `main`, with no path filter
(about two to three minutes). `e2e` runs on changes to `dashboard/**`, `share/dashboard/**`, `src/mu/**`,
`tools/test-dashboard/**`, `toolchain/*/env.pip.txt` and the workflow (all of `src/mu`, because the server imports
`jobs.py` and `proto.py`, and a change to `clean_text` must run the XSS test). All three run weekly on `main`.

| Job | Runner | Steps | Tests |
|---|---|---|---|
| `front` | ubuntu-24.04 | `setup-node` from `.nvmrc`; `npm ci --ignore-scripts`; ESLint; `tsc --noEmit`; Vitest; `npm run build`; upload `share/dashboard/` and the size report as an artifact; `git diff --exit-code -- share/dashboard`; bundle budget | D16 (lint part), D28, D36–D38, D41, D43, Vitest units |
| `supply` | ubuntu-24.04 | `npm ci --ignore-scripts` (fails on lockfile drift); both platforms' native packages in the lockfile; `npm audit --audit-level=high`; `npm audit signatures`; the licence script | D34, D35 |
| `e2e` | ubuntu-24.04 | Python with psutil 7.2.2 from the pinned `env.pip.txt` (`--require-hashes`); `npx playwright install chromium webkit` (versions fixed by the pinned Playwright); Playwright against `mu dashboard serve --dev` serving the committed build; screenshots of every scenario in light and dark uploaded as an artifact | D04–D08, D14 and D15 (page parts), D16, D18 (page part), D26, D29, D39, D42, D46, D47 |
| existing `job-layer.yml` | ubuntu-24.04, macos-15 | the Python tests, including `BUILD.json` against the source without Node; the fixtures against the server's responses; the LaunchAgent on macOS; in 1b, the node side with a real node | D12–D15, D17–D20, D22–D24, D27, D38 (Python part); in 1b D01–D03, D09–D11, D21, D25, D40, and D05–D07 again against a real node |

**Release gate.** The `BUILD.json` check in `job-layer.yml` runs on every commit and must pass on the tagged commit.
Because `mu release` is not built as a command (PLAN §9), the release checklist also checks `gh run list --commit
<sha> --workflow dashboard.yml` shows success before a tag is made; with `front` running on every push to `main`, there
is always such a run.

| # | Check | Fault put in → must fail | Phase · level |
|---|---|---|---|
| D01 | A job that allocates 1 GB and keeps 2 cores busy for 10 s: `usage.mem_peak_bytes ≥ 1 GB`, `cpu_s` 20 ± 15 %, `cores_avg` ≈ 2, `source: "cgroup"` | `usage-from-shim` (read the shim's own rusage, not the cgroup) → peak < 100 MB | 1b · Python, node |
| D02 | An `oom` job (runner-written result) carries `usage` with `source: "runner-sampled"`, `mem_peak_bytes` ≥ 80 % of its limit and an oom marker; a `timeout` job carries `cpu_s` within 5 s of its CPU; `mu fetch` copies `usage` into `runs/<id>.json` | `read-cgroup-after-exit` (the runner reads the unit's cgroup in `classify()`) → `usage` absent or zero | 1b · Python, node |
| D03 | `stats` answers valid JSON, exit 0, with the running job and its cores ≈ the job's; idle → `running: []`; with 2,000 synthetic finished jobs the node-side time stays < 100 ms; with the runner stopped for 20 s → `stale: true` | `stats-scan` (call `scan_jobs`) → over 100 ms; `no-age` → `stale` missing | 1b · Python, node |
| D04 | Against a node without `features` (previous release), the page lists jobs and says "update μ on office-pc for live statistics" | `assume-features` → error page, no jobs | 1a · Playwright (`old-node`) |
| D05 | **Jobs by node.** Two fake nodes, one job submitted to each: `/api/jobs` and the rendered table show each job under its own node, and each follows queued → running → succeeded within 15 s of the node's change | `node-from-config` (every job labelled with the first paired node) → wrong node | 1a · Playwright; 1b again with a real node |
| D06 | **Figures are right.** A fake node returns known `stats`: `/api/nodes/<n>` and the rendered machine card show those numbers (CPU %, used, available, limit, each job's cores and memory) | `swap-mem-fields` (available shown as used) → mismatch | 1a · Playwright; 1b again |
| D07 | **Stale is shown.** Cut the fake node's link: within 15 s every node value carries its age, is greyed and says "stale"; the API marks it `stale: true` | `serve-last-as-live` → values still presented as current | 1a · Playwright, Vitest (stale rule); 1b again |
| D08 | **Mac sampler.** Against a 2-core busy loop for 10 s, the Mac card's CPU equals 2 cores' worth of the machine within ± 25 % | `cpu-first-call` (psutil's first `cpu_percent` with no interval, always 0) → off | 1a · Python (sampler), Playwright (card) |
| D09 | The sampler costs < 5 ms per pass (median); the runner's loop stays at 1 s ± 0.1; with the dashboard killed for 5 min in a 10-min job, the job's series has no gap over 2 × the interval | `sample-under-lock` → the queue waits; `mac-samples-only` → a 5-min gap | 1b · Python, node |
| D10 | `metrics` pieces joined by offset across a killed link equal the node's file byte for byte (as R21) | `metrics-from-zero` → duplicates | 1b · Python, node |
| D11 | `watch` ends at `max`; a client that disappears leaves no session after the cap (5 s in the test); `stats` and `metrics` add no line to `audit.log`, `watch` adds two | `no-cap` → session still open; `journal-each-call` → audit lines grow per call | 1b · Python, node |
| D12 | The server is reachable on 127.0.0.1, refused on the machine's LAN address and on `::1` | `bind-all` → LAN connect succeeds | 1a · Python |
| D13 | `Host: evil.example:<port>` → 403 on every route | `no-host-check` → 200 | 1a · Python |
| D14 | `/api/*` without `X-Mu-Token` → 401, with the token in a cookie only → 401, with a wrong token → 401, with the previous server start's token → 401 and the no-token page, which holds no job or node data | `no-token` → 200; `fixed-token` → old token accepted | 1a · Python (HTTP), Playwright (page) |
| D15 | The token appears in none of: the argv recorded by a stub opener during `mu dashboard open`, `logs/dashboard.log`, the output of `mu submit`, `mu dashboard --json` and `mu compute status --json`; `dashboard-open.html` is gone after first use; once the page has loaded, neither `location` nor `history` holds `#t=` | `open-url-argv` (open `http://…#t=` directly) → found in the stub's argv; `keep-fragment` (skip `replaceState`) → `#t=` in the address bar | 1a · Python, Playwright (address bar) |
| D16 | A job named `<img src=x onerror=…>` that logs HTML, ANSI escapes and a 1 MB line: in Chromium and WebKit no element is created from it, nothing runs, the CSP header is present, the page stays responsive; ESLint rejects a planted `dangerouslySetInnerHTML` | `inner-html` (render the name with `dangerouslySetInnerHTML`, lint rule off) → the handler runs; `lint-off` → the planted line passes lint | 1a · Playwright (Chromium, WebKit), ESLint |
| D17 | **Allowlist.** Unit test of `NodeReader`: every verb outside the list raises before any ssh call; no other code path in `dashboard.py` constructs a `jobs.Conn` (a source check); end to end, a fake node records only allowed verbs while every route is exercised | `allow-cancel` → `cancel` passes the wrapper | 1a · Python |
| D18 | Node offline → "offline since", retries back off; exit 5 (host key) → red, zero further node requests in 5 min | `retry-refused` → requests > 0 | 1a · Python, Playwright |
| D19 | With the dashboard off, killed, its port held by another program, or a server stub that accepts and never answers, `mu submit/status/log/fetch` give the same exit codes and the same `--json` (minus the `dashboard` field), and none of them opens a connection to the dashboard port | `submit-waits` (wait for the server) → hangs on the stub | 1a · Python |
| D20 | **Own master.** While a CLI `mu log -f` runs on the CLI's master, a dashboard `list` that times out (fake node stalls 15 s) closes only the dashboard's master; the CLI's session continues | `shared-controlpath` → the CLI's `mu log -f` ends | 1a · Python, fake node over ssh |
| D21 | `mu compute status <node> --json` includes `stats` from a node with the feature, and omits it (no error) from an older node | `drop-stats` → missing | 1b · Python |
| D22 | `mu submit` writes a `jobs.index` line; job detail finds the project's `.mu/jobs` record through it; with 150 finished jobs the page shows 100 and "older jobs: `mu status`"; the dashboard sends `limit=100` | `no-index` → project unknown; `default-limit` → 20 rows | 1a · Python |
| D23 | `mu dashboard on` → the agent is loaded and the URL answers after the agent is killed (launchd restarts it); `off` → nothing listens; with no tab and nothing unfinished for 5 min, zero node requests and the server's CPU time does not grow by more than 0.1 s | `poll-when-idle` → node requests seen | 1a · Python, macOS |
| D24 | On battery (stubbed) with no tab and a running job: `list` at most every 5 min, and the dashboard's master is gone after `ControlPersist` | `ignore-battery` → `list` every 60 s | 1a · Python |
| D25 | With a tab open for 10 min during a job, μ's own processes on the node use < 2 % of one core; the job's `cpu_s` is within 3 % of a run without the tab | `poll-stats-1s` (poll `stats` per second instead of `watch`) → over budget | 1b · node |
| D26 | **Accessibility** (Playwright, Chromium): every control is reachable with Tab and has an accessible name; every state colour has a text label next to it; no `aria-live` region changes on a routine refresh; axe reports no serious or critical violation (contrast included) in light and dark, in every scenario | `div-button` (a clickable `div`) → unreachable; `faint-text` (state text in `text-faint`) → axe contrast violation | 1a · Playwright + axe |
| D27 | Two `mu dashboard serve` at once → one serves, the other exits 0 | `no-lock` → two processes | 1a · Python |
| D28 | **The committed build is the source's.** CI rebuilds `share/dashboard/` and finds no difference; `BUILD.json`'s source hash matches `dashboard/` (checked in Python, without Node) | `stale-build` (change a component, commit without rebuilding) → both checks fail | 1a · CI `front`, Python |
| D29 | **No CSP or Trusted Types violation.** In Chromium and WebKit, every Playwright scenario registers a `securitypolicyviolation` listener with `addInitScript` (before any page script) and fails on any event; each scenario opens every overlay it has (tooltips, the selects, the chart's table), because a violation fires when an element mounts, not at page load; the served CSP has no `unsafe-inline`, `unsafe-eval`, nonce or hash, and has `require-trusted-types-for 'script'` | `style-tag` (a component that inserts a `<style>` element) → violation; `radix-scrollarea` (use Radix `ScrollArea` in `LogView`) → violation when the log opens; `inner-html-lib` (a helper that sets `innerHTML`, outside lint's reach) → Trusted Types violation; `csp-unsafe-inline` → header check fails | 1a · Playwright (Chromium, WebKit) |
| D34 | **Licences.** Every package in the lockfile has an allowed licence for its kind (shipped or dev, §4.6.1); `THIRD-PARTY.txt` lists every shipped package | a test lockfile with a GPL-3.0 shipped package → fails; one with no `license` → fails | 1a · CI `supply` |
| D35 | **Install is locked.** `npm ci --ignore-scripts` succeeds on the committed lockfile; no install script runs (a canary package in a test fixture writes a file if its script runs); the lockfile records both the `linux-x64` and the `darwin-arm64` native packages of Rolldown, Lightning CSS and Tailwind's oxide | `lock-drift` (a version changed in `package.json` only) → `npm ci` fails; `scripts-on` → the canary file appears; `mac-only-lock` (a lockfile written on macOS without the Linux bindings) → the platform check fails | 1a · CI `supply` |
| D36 | **Bundle budget.** First-load JavaScript ≤ 250 kB gzipped, CSS ≤ 30 kB; no source maps and no `data:` assets in `share/dashboard/` | `import-all-icons` (import the whole icon set) → over budget | 1a · CI `front` |
| D37 | **Gaps stay gaps.** A series with a 30 s hole in 5 s samples is shaped with a break (a `null`), never a straight line across | `interpolate-gap` → no break | 1a · Vitest |
| D38 | **API contract.** The golden fixtures in `tools/test-dashboard/fixtures/` are what `dashfake.py` emits and what the server returns for every scenario (Python), and the app's decoders accept every one (Vitest); out-of-range numbers are clamped and long strings capped | `rename-field` (rename `mem.avail` in the server) → the Python fixture check and the decoder test fail | 1a · Vitest, Python |
| D39 | **Old-node budget.** Against the fake `old-node` scenario with a tab visible for 10 min (fake clock): with a running job, at most 45 `list` calls (40 at 15 s plus reconciles) and each state change shown within 20 s; with nothing unfinished, at most 12; with no tab, the §4.3 rules | `poll-list-2s` (poll `list` every 2 s) → about 300 calls | 1a · Python (call count), Playwright (page) |
| D40 | **Real node matches the fixtures.** In `job-layer.yml`'s real-node run, the node's `stats`, one `watch` line and `metrics` lines validate against the golden fixtures (same keys, types and units) | `rename-field-node` (rename a field in `node.py` only) → validation fails | 1b · Python, node |
| D41 | **Polling survives "offline".** With TanStack Query's `onlineManager.setOnline(false)`, the overview query keeps fetching on its interval | `network-mode-default` (leave `networkMode` at `'online'`) → fetching stops | 1a · Vitest |
| D42 | **Theme.** With the browser in dark mode, `<html data-theme="dark">` is set before the app's module script runs (no white first paint); switching the emulated colour scheme redraws the charts in the other theme's colours (pixel colour of a series read from the canvas) | `theme-in-bundle` (set the attribute from the React bundle instead of `theme.js`) → the attribute is missing at first paint; `chart-fixed-colours` → the canvas colour does not change | 1a · Playwright (Chromium, WebKit) |
| D43 | **Chart and state contrast.** For every chart series and every state colour against its background, in light and dark, computed from `tokens.css`: text and icons ≥ 4.5:1, lines and marks ≥ 3:1 | `faint-series` (cache series at 1.5:1 in dark) → fails | 1a · Vitest |
| D46 | **Failure states.** Stop the server while the page is open → the "server is not running: `mu dashboard on`" banner, not "node offline"; an exception thrown while rendering the `hostile` job → the error page, which contains none of the job's text; with JavaScript off → the static browser-floor text; a lazy chunk that returns 404 → the reload banner | `boundary-shows-error` (render `error.message`) → the hostile string appears; `server-down-as-offline` → node offline shown instead | 1a · Playwright |
| D47 | **Copy works in Safari's engine.** In WebKit on `http://127.0.0.1:<port>`, a copy button puts the exact `mu` command on the clipboard (read back in the test), or, if the clipboard is refused, selects it and says so | `copy-after-await` (call `writeText` after an awaited call) → nothing copied and no fallback shown | 1a · Playwright (WebKit) |

**Done when (phase 1 as a whole, after 1b)** the owner, with nothing started by hand, opens the page during a real job
on the office PC and sees the job under its node, its live cores and memory against the limit, the whole run's chart, and both machines' load with
their ages; and when, after an `oom` job, its row shows its peak memory; and when `mu compute status office-pc --json`
gives Claude the same node figures.

### 7.2 Phase 2: history, more screens, benchmark comparison (4–6 days)

**Scope.**

- **Node:** host history files with 7-day retention; the verbs `metrics day=`, `events` and `bench`.
- **Mac:** the SQLite cache with retention and reconciliation; separate Nodes and This Mac pages; search, filters,
  shortcuts; the job timeline; Compare › Performance on the benchmark suite.
- **Page:** TanStack Router (hash history, filters in the URL); TanStack Table with filtering and search; a theme
  toggle (the attribute and `theme.js` are already in place); ECharts in a lazy chunk only if the comparison needs more
  than SVG bars, under the CSP rules of §4.6; `@tanstack/react-virtual` only if 1a's log and table caps prove too
  small. Page tests are Playwright tests, as in 1a.

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

### 7.5 Phase 4: results and more (about 1–2 weeks, owner's choice)

- Results as tables with tolerance badges, images, residual and convergence plots, mesh-convergence plots.
- three.js with react-three-fiber for STL and untextured GLB, in a lazy chunk, fetched as bytes through the API
  client and parsed in the page, with no decoder workers or wasm (§4.6, CSP by phase). STEP is converted on the node.
- Run-vs-run comparison that shows only the fields that differ.
- Project result series across commits.
- Cancel with a confirmation, if the owner wants it. The tests are the same as D12–D17, plus CSRF: a cross-origin
  POST is refused.
- A systemd user service for Linux workstations.
- An optional `/metrics` export.

### 7.6 Effort, honestly

Phase 1 is now **12–16 days** (1a 8–11, 1b 3½–5), against 6–9 before revision 2 and 9–12 in revision 2, whose
figures the review found light by 2–3 days:

- **Front-end setup, 3–4 days** (was "1½–2"): Vite, TypeScript, Tailwind and lint; four CI jobs; the licence script;
  D28 and D34–D36 with their faults; Dependabot and its review flow; making the build reproducible across macOS and
  Linux and the lockfile hold both platforms; the Node install and the sandboxed npm wrapper (½–1 day of this).
- **The look, 3–4 days** (was "½–1" on top of the page): tokens, 13 components, about 10 states in 3 scopes, two
  themes with a pre-paint script and charts that follow them, the gallery and screenshots, axe-clean contrast and the
  chart contrast test, and one review round with the owner (½ day).
- **The old-node mode and 3.0a, about ½ day:** polling budget, D39, the verb-cost job.
- **+½ day for the split:** two releases and two reviews instead of one.
- **+½–1 day for fake nodes and the golden fixtures,** so the page is finished before the node side exists.
- **1b +½–1 day** for page and fixture changes after the scheduler's shapes settle (D40).
- 1a is about 2,500–3,500 lines of TypeScript/TSX and CSS (revision 2 said 1,400), 900 of Python, about 2,000 of
  tests.

**Later phases keep their earlier ranges** (phase 2 4–6 days, phase 3 3–5, phase 4 about 1–2 weeks). Revision 2 cut
them on the argument that the libraries would make them cheaper; that is not evidenced yet. They are re-estimated
from 1a's measured pace. All phases together are about 5,000–6,500 new lines plus tests. The front end adds a
dependency list to keep current (§3.2, updated by pull request only). The risky parts are still on the node and in
`mu run`, not in the web page:

- the sampler must never slow or block the runner;
- the shim must never change what `mu run` does, which is why it is opt-in and separate.

Phases 1b and 2b need at least one round trip on the real PC or real CAD sessions each; 1a needs none. Node code
reaches the PC only through a release and its auto-update, which is failing today and is the gate in §7.0.

## 8. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| The node's auto-update is failing (`hello.autoupdate.last_result = failed`) | New verbs never arrive | A gate on phase 1 (§7.0), not only a risk; the page shows the node's μ version and "update μ on office-pc" |
| The runner's samples are the only source for `oom`/`timeout` usage | Up to 5 s missing; a fast spike just before the kill may be missed when `memory.peak` is absent | Use `memory.peak` when the kernel has it (it is a kernel-tracked peak, read every 5 s); label `runner-sampled`; §7.0 checks what survives an OOM kill |
| `memory.peak` counts page cache | An OpenFOAM job writing fields looks bigger than it is; `--mem` sized from it is too generous | Label it; compare only sampled `anon + shmem`; show cache separately |
| WSL VM figures, not Windows | Someone using the PC slows a job and the page shows a quiet node | Said on the node card; the owner decides in §9 Q2 whether a root-installed Windows counter is worth it |
| Per-call cost on the node (sshd + gate + bash + Python) | The dashboard's own load shows up in the figures it reports; audit lines bury the important ones | `watch` and `log follow=1` instead of polling; `list` only on `rev`; no journal line for telemetry verbs; for old nodes a stated `list` budget (§4.3, D39); measured on today's node in 3.0a, before 1a ships; D11, D25 |
| A dashboard call disturbing Claude's ssh sessions | A timed-out call ends Claude's push or fetch | Its own ControlPath; it never closes the CLI's master (D20) |
| Keychain access from the LaunchAgent | The node worker cannot connect | Checked in §7.0; if it fails, the owner falls back to `mu dashboard open` from a terminal (§9 Q1) |
| The `mu run` shim changes every local command | A regression in signals, exit codes or TTY behaviour breaks CAD work | Opt-in phase 2b, D44, default only after real use |
| `list` cost grows with history (stage 1 never deletes jobs) | `list` gets slower on the node, and so does every quick call | `list` only when `rev` moves; `limit=100`; a node job-retention rule is a separate decision (§9 Q9) |
| `audit.log` is never rotated | It grows without bound; until 1b, the dashboard's old-node polling adds up to 240 lines an hour while a tab is visible and a job runs (about 50 kB/h) | Monthly rotation, keep 12, ships with step 2's release (§9 Q9), the first release that can reach the node; the old-node budget (§4.3, D39) bounds the lines until then |
| XSS through node text | Reads job data in the browser | Text only, strict CSP, D16; read-only bounds the damage |
| Scope creep: the page becomes where things are changed | Two interfaces drift; Claude cannot see what the user did | Read-only until the owner decides; every action stays a `mu` command; node figures also in `mu compute status --json` |
| More nodes and workstations | Each workstation × node pair holds one `watch` session while visible | Nothing runs with no tab; the relay holds one more WebSocket per workstation and node while active, well within its limits |
| Battery | ssh keepalives every 15 s for the whole job | No tab + battery → `list` every 5 min and the master expires (D24) |
| npm supply chain: a compromised or typo-squatted package, or a hijacked maintainer account, in the page | Code in the page reads job data and the token | Few dependencies (§3.2); exact versions and a committed lockfile; releases at least 14 days old; updates only by a PR whose lockfile diff was read; `npm audit` and `npm audit signatures` in CI and weekly; Trusted Types block HTML and script sinks inside libraries; the server's allowlist and the read-only API bound what a page script can do (§4.6, §4.6.1, D29, D34, D35) |
| **npm code at build time** (Vite, Rolldown, Tailwind, ESLint, Vitest, Playwright and their dependencies) | `--ignore-scripts` does not apply once `build`, `test`, `lint` or `dev` runs: a malicious new version runs with the rights of whoever builds. On the owner's Mac that is the Keychain's relay password, the node ssh keys and git push rights | Only the 14-day age, the small dependency count and reading the lockfile diff stop the package itself. What it can reach is cut down: on the Mac npm runs only through `tools/dashboard-npm` (scrubbed environment, `sandbox-exec` denying keys, Keychain and `$MU_HOME` secrets, no network outside install); in CI the workflow has `contents: read` and no secrets (§4.6.1). If `sandbox-exec` stops working: a separate macOS user, or the risk accepted here in writing |
| Dependencies go stale | Security fixes missed; a large jump later | Dependabot's weekly grouped PRs; the weekly audit on `main`; the pins are reviewed at each release that touches the dashboard |
| The committed build drifts from the source | A release ships a page that is not the reviewed code | CI rebuilds and diffs (D28); `BUILD.json`'s hash checked by the Python CI on every commit, the release gate; `front` runs on every push to `main`, and the release checklist confirms its run on the tagged commit (§7.1) |
| The build is not reproducible across machines (macOS against Linux; native build tools such as Rolldown, Lightning CSS and Tailwind's engine) | D28 fails for a build made on the Mac | The rule, not a fallback: the committed bundle is the Linux `front` job's artifact. Local builds may be committed only if a one-time 1a check shows them byte-identical |
| A lockfile written on the Mac lacks Linux's native packages | `npm ci` fails in CI | Lockfile changes record both platforms; D35 checks |
| Bundle size and repository growth | A slow first load; every rebuild adds hashed files to git history | The 250 kB budget (D36); the vendor chunk changes only when dependencies do; no source maps committed; ECharts and three.js lazy. About 0.3–0.5 MB per phase-1 build, mostly unchanged between builds |
| A library needs `unsafe-inline` or injects `<style>` elements | The CSP has to be loosened, or the page breaks | D29 catches it in CI; such libraries are not adopted, or used in a form that does not (§3.2) |
| Framework churn (React, Vite and TanStack majors) | Upgrade work every year or two | Exact pins and grouped updates; the decoders keep the server independent of the front end; the app is small |
| Node.js on the developer's Mac (the owner's Mac is also the developer's) | A second toolchain on that machine | The official tarball checked against a pinned SHA-256, in `$MU_CACHE/dev/`, never on the global `PATH`, used only through the wrapper; users and the node never need it |
| uPlot has one maintainer and its last release is from March 2025 | No fixes if a bug appears | It sits behind our own component of about 80 lines; replacing it touches one file. ECharts is the fallback |
| The scheduler changes `now.json`'s `running` and `queue` shapes after 1a froze them | The page reads fields the real node does not send | The scheduler's author reviews the shape before 1a freezes it; one set of golden fixtures for fake and real nodes (D38, D40); ½–1 day in 1b for the changes |
| Phase 1b against the scheduler | Both change the node's runner; done in parallel they conflict | 1b starts after the scheduler merges and re-reads its design (§7.1); 1a touches only `cmd_submit` on the Mac side of `jobs.py` |

## 9. Questions for the owner

> **Defaults set 2026-11-14** (the owner may override): see [docs/ROADMAP.md](../ROADMAP.md) §2.3. In short: always
> on; the PC's figures are the WSL2 VM's, labelled, with no Windows-side task (the office PC is not to be
> disturbed); read-only; local `mu run` recording opt-in; comparisons on benchmarks and real project jobs (the JATAYU
> `perf/` log as a source); notifications opt-in; no reverse tunnel ever; node retention 90 days and monthly
> audit-log rotation. The dashboard is built before the shell work, and defines the status data the shell reads.
>
> **Front end (owner's direction, 2026-11-14):** React + TypeScript + Vite, built in CI and shipped as static files;
> not Next.js (§3.1). Questions 11 and 12 are new with revision 2 and have defaults in ROADMAP §2.3.

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
   log monthly, keep 12), separately from the dashboard. Default (ROADMAP §2.3): the `audit.log` rotation ships with
   step 2's release, because the dashboard's old-node polling adds lines from 1a on (§4.3) and step 2's is the first
   release that can reach the node; job archiving can follow later.
10. **History length** (phase 2). Mac: raw 48 h, 1-min rollups 90 days. Node host history 7 days. Enough?
11. **The look** (revision 2). The default is a neutral, technical palette with one blue accent (§5.3). Should the
    page carry Martand's colours or logo instead? If so, send the colour values and a logo file (SVG); they become
    tokens and a file in the build, nothing fetched.
12. **Release 1a on its own?** Recommended: yes. It shows every job by node with its state and log, and This Mac's
    load, weeks before the node figures can arrive (1b waits for the scheduler and the auto-update fix). The node card
    says plainly that live statistics come with the next node update.

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
- Revision 2, checked 2026-11-14: the npm registry's metadata (`registry.npmjs.org/<package>`: latest version, release
  dates, `license`, `engines`, `peerDependencies`) for every package in §3.2; bundlephobia's size API for the shipped
  ones; the React DOM size from its published `react-dom-client.production.js`; Node.js releases from
  `nodejs.org/dist/index.json`; `actions/setup-node`'s latest release through the GitHub API. `bin/mu` `cmd_install`
  (the recipe is the tagged tree, so committed build files ship with it). The scheduler's design
  (`docs/design/scheduler.md`) did not exist yet on any branch; phase 1b re-reads it when it does.
- The two advisories in §3.1, checked by web search on 2026-11-14: CVE-2025-29927 (Next.js middleware authorisation
  bypass, CVSS 9.1, March 2025; self-hosted `next start` servers, not static exports) and CVE-2025-55182 ("React2Shell",
  unauthenticated remote code execution in the React Server Components Flight protocol, CVSS 10, 3 December 2025;
  affects Next.js 15 and 16 with the App Router). A client-only React bundle, as here, uses neither middleware nor
  server components.

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

### 11.1 Revision 2: the owner's direction on the front end (2026-11-14)

The owner asked for a nicer page and for React or Next.js. Taken: React + TypeScript + Vite as a static app built in
CI and served by μ's Python server (§3.1, §4.8); the libraries pinned with licences and sizes (§3.2); the supply-chain
and CSP rules (§4.6, §4.6.1); a visual spec (§5.3); phase 1 split into 1a (no node change, starts now, in parallel with
the concurrent-jobs scheduler) and 1b (after the scheduler and the auto-update fix), re-estimated at 9–12 days in all
(§7.1, §7.6); page tests moved to Playwright and Vitest with their faults kept, and new tests D28, D29 and D34–D38;
the CI jobs; new risks (§8); two owner questions (§9 Q11, Q12). Not taken: Next.js (§3.1). Unchanged: everything on
the node, the transport, the API's routes, the token, the `Host` check and the read-only scope. Its review and what
changed are in §11.2; the front end's setup gets another review with phase 1a's code.

### 11.2 Review of revision 2 and revision 3 (2026-11-14)

An independent review of revision 2 approved it with changes: React + TypeScript + Vite as a committed static bundle
served by the stdlib server is right for a local, single-user, security-sensitive page, and refusing Next.js is
correct. It checked the §3.2 versions against the npm registry (all real; none declares an install script), that
`install/install.sh` copies the whole tree into the recipe (so `share/dashboard/` ships), that `limit` is already in
`ARG_RULES`, and that no `docs/design/scheduler.md` exists yet (still true at revision 3: there is no `scheduler` branch
on the remote either). It found as sound, and they are unchanged: the Next.js reasoning and the two CVEs; the fixed
static-file table, `no-store` on `index.html` and no token in static files; the token in a header; exact pins with the
14-day rule and `npm audit signatures`; the licence allowlist; no CDN, fonts or source maps; Dependabot rather than a
third-party app; the dev server limited to fake nodes; the router deferred to phase 2; the 1a/1b boundary; the
fault-injection style of the new tests.

| Finding | Taken? | Where |
|---|---|---|
| **B1** CSP misses Radix `ScrollArea` (renders `<style>`) and modal `DropdownMenu` (scroll lock injects `<style>`) | Yes. `ScrollArea` and `Toggle` dropped; no modal parts ever; of the two options for the filters, native `<select>` chosen over `DropdownMenu modal={false}` (less code, no overlay to test); 1a's Radix is Tooltip only. D29 opens every overlay, listens from `addInitScript`, runs in WebKit too. The fallback is never a nonce, a hash or `unsafe-inline` | §3.2, §4.6, D29 |
| **B2** CSP for ECharts and three.js asserted, not analysed | Yes. A "CSP by phase" table: ECharts canvas renderer with `richText` or no tooltip; three.js STL and untextured GLB only, bytes fetched through the API client and `parse()`d, no decoder workers or wasm, `blob:` only in `img-src`/`connect-src` if textures are ever wanted; model files treated as hostile input. Trusted Types required from 1a (`trusted-types 'none'`), tested in Chromium and WebKit. One qualification: if the pinned Playwright WebKit build does not enforce Trusted Types, its D29 run checks the CSP only, and the test says so | §3.2, §4.6, §7.5, D29 |
| **B3** supply chain stops install scripts and drift but not a compromised build-time package; Dependabot flow unspecified | Yes, all of it. Build-time execution stated plainly in §4.6.1 and §8; `dashboard.yml` gets `contents: read`, no secrets, no `pull_request_target`; the committed bundle is always CI's artifact, with a one-time 1a check before local builds may be committed; `tools/dashboard-npm` with a scrubbed environment and a `sandbox-exec` profile is built (½–1 day) rather than the risk accepted, and it also denies the Security server, `/usr/bin/security` and the network outside install, since file rules alone do not stop Keychain calls; the Dependabot flow is artifact → lockfile and changelog review → a person commits, no workflow commits; the "reviewed with its effect on `share/dashboard/`" claim removed; D35 asserts both platforms' native packages; Node from the official tarball with a pinned SHA-256 in `$MU_CACHE/dev/` | §3.2, §4.6.1, §4.8, §8, D35 |
| **B4** no traffic plan for today's node; the measurement that bounds it scheduled after 1a | Yes, with one change. An old-node row in §4.3 with its cost stated; refined so that 15 s applies only while the node has an unfinished job or this Mac just submitted to it, else 60 s. The review counted "per visible tab"; the server makes the calls once per node however many tabs are open, so the figures are per node. The verb-cost measurement moved into 3.0a (a job on today's node). D39 added with `poll-list-2s`. **Not taken as written:** building `audit.log` rotation *before 1a's release*. Rotation is node code, and node code reaches the PC only through auto-update, which is broken until step 2; a rotation finished before 1a would still not run on the PC sooner. It ships with step 2's release instead, the first that can reach the node, and the old-node budget keeps the added lines to about 50 kB an hour while watched | §4.3, §7.0, §8, §9 Q9, D39; ROADMAP |
| **S1** fake-node/real-node contract unguarded | Yes. Golden fixtures in `tools/test-dashboard/fixtures/`, emitted by `dashfake.py`, checked against the server (Python) and the decoders (Vitest) in D38, and against the real node in 1b (D40, `rename-field-node`); the scheduler's author reviews `now.json` before 1a freezes it; ½–1 day in 1b; "the page itself should need no change" deleted | §4.8, §7.1, §8 |
| **S2** TanStack Query pauses offline | Yes: `networkMode: 'always'`, D41 | §3.2, §4.8 |
| **S3** a dev install copies `dashboard/node_modules` into every toolchain | Yes: three `tar` excludes, a 1a change to `install.sh` (not made in this docs-only revision) | §4.8, §7.1 |
| **S4** CI gating holes | Yes. `e2e`'s path filter covers `src/mu/**` and `toolchain/*/env.pip.txt`; of the two options, both taken: `front` and `supply` run unfiltered on every PR and push to `main`, and the `BUILD.json` Python check is named the release gate, with the release checklist confirming `dashboard.yml`'s run on the tagged commit; `.gitattributes` and "rebuild after a rebase, never hand-merge" added | §4.6.1, §7.1, §8 |
| **S5** effort light by 2–3 days | Yes. 1a 8–11 days; lines 2,500–3,500 plus about 2,000 of tests; the owner's review round budgeted; later phases back to their earlier ranges until 1a gives a measured pace. One difference: phase 1 in all is **12–16** days, not the review's 11–15, because S1's ½–1 day in 1b is added to 1b (now 3½–5) | §7.1, §7.6; ROADMAP |
| **S6** trim the 1a libraries | Yes. TanStack Table moved to phase 2; Radix shrinks to Tooltip; uPlot's single maintainer and 2025 release noted in §3.2 and §8; `LogView` caps stated as numbers (5,000 lines or 2 MB, 10 kB per line) and `@tanstack/react-virtual` listed for phase 2 if needed, not pinned yet because its latest release (2026-11-03) is under 14 days old | §3.2, §5.3, §7.2, §8 |
| "A little nice": dark-mode mechanics | Yes: `@custom-variant dark` on a `data-theme` attribute; `theme.js` as an external blocking script; `<meta name="color-scheme">`; charts read token colours and redraw; D42 | §4.8, §5.3 |
| Charts and accessibility | Yes: D43 computes chart and state contrast in both themes; an `aria-label` summary per chart; of the two options, a "show as table" button rather than keyboard cursor movement (a real table serves screen readers better) | §5.3 |
| Missing states | Yes: server not answering, error boundary with no node text, no-JavaScript and browser-floor page (Safari 16.4, Chrome 111, Firefox 128), lazy chunk 404 via `vite:preloadError`; D46 | §4.8, §5.3 |
| The owner's review of the look | Yes: `e2e` uploads screenshots of every scenario in light and dark; the owner having looked at them is part of 1a's done-when | §4.8, §5.3, §7.1 |
| Small additions | Yes: favicon, tab title with the state, absolute time on hover, the WebKit copy test (D47), and the `eslint-plugin-jsx-a11y` note (6.10.2 declares ESLint ≤ 9, checked on the registry) | §3.2, §5.3 |

Rejected outright: nothing. Changed from the review's wording: the timing of `audit.log` rotation (B4) and phase 1's
total (S5), with the reasons above. The Radix and three.js behaviour the review described is from knowledge of their
source, not from a run here; D29 in 1a is where it is confirmed.
