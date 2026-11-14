# μ roadmap: what is built next, in what order, and why

- **Written:** 2026-11-14, after the first remote job ran on the office PC (release v2026.11.8).
- **Owner's order:** finish the pairing fixes → build the **dashboard** → then the **shell experience**.
- **The plans themselves** are [docs/design/dashboard.md](design/dashboard.md) and
  [docs/design/shell-ux.md](design/shell-ux.md). Each was researched by several agents, reviewed by an independent
  reviewer, and revised. This file fixes their order, records the owner's decisions, and lists what must be true
  before each step starts. It is meant to stay valid however much else changes before the work begins: where a
  design document and this file disagree about **order or decisions**, this file wins; about **mechanism**, the
  design document wins.
- **Engineering capability** (manufacturability, calculations, deeper FEA, loads, reports and review) has its own
  proposal, [docs/ENGINEERING-ROADMAP.md](ENGINEERING-ROADMAP.md). It does not change the order below. Where it fits
  is an open owner decision (its §13, decision 14).
- **Context:** [docs/PLAN.md](PLAN.md) is the overall plan (§13 phases, §17 build or adopt). What is installed and
  measured is in [docs/verification/](verification/).

## 0. Where things stand (2026-11-14)

| Piece | State |
|---|---|
| Toolchain releases | v2026.11.8 on the Mac and the office PC; auto-update on both |
| Office PC as compute node | Paired with the Mac by one code; first remote job matched the Mac's FEA result (0.005 %); `oom`, `timeout`, `cancel` and the full benchmark suite (6/6 in 13.4 s, against 25–37 s on the Mac) verified through the relay |
| Relay | Deployed on the Dokploy server with the pairing service on |
| JATAYU | A Claude Code agent is moving its heavy computation to the office PC and logging timings for a Mac/PC comparison (`perf/` in the JATAYU repo) |
| Known problem | The node's auto-update reports `last_result: failed` (seen through `mu compute status office-pc --json`); cause being diagnosed with a job on the node. **This blocks everything below**: new node code reaches the PC only through a release and its auto-update |
| Not yet done on the PC | The restart tests (shutdown hook stopped, then on) and a real Windows Update restart. Next office visit; no rush |

## 1. The order

Each step starts only when the one before is merged and released, unless marked "in parallel".

### Step 1. Pairing fixes → v2026.11.9 (in flight)

The independent security and usability reviews of one-code pairing, applied on branch `pairing`, plus:

- the `mu: command not found` bug in every printed PC command (`wsl … -- mu` runs a non-login shell; use
  `--exec /home/mu/.local/bin/mu`);
- the installer asking a non-sudoer for a sudo password; the hidden `systemctl` error when auto-update cannot be
  turned on at install; `mu gc` printing a macOS Time Machine note on Linux (README "Known issues");
- the follow-ups the documentation pass found (stale lines in `install/hosts/wsl2/README.md`, `relay/README.md`,
  `docs/design/first-job-checklist.md`, `docs/design/job-layer.md` §19; `mu-pair.py` and `C:\Mu\stage\` left behind
  by the Windows uninstall; `Invoke-Elevated` not copying `mu-pair.py`; `-InstallMu` without `-Branch` installing a
  `dev-<commit>` version).

**Merging to main redeploys the relay** (Dokploy watches `relay/**`). Sequence: merge and tag together; check
`https://relay.martand.systems/mu/v1/` answers `"pairing": true` after the redeploy; `mu compute status office-pc`
from the Mac. Done when both machines run v2026.11.9 **by auto-update**, not by hand.

### Step 2. The node's auto-update works again (prerequisite of everything after)

- Find the cause from the node's `state/autoupdate.state` and newest `logs/autoupdate-*.log` (read with a job:
  `mu submit -- bash -c '…'`), fix it in a release, and confirm `hello.autoupdate.last_result` turns `ok` after the
  next daily check. No PC visit, unless the cause is on the Windows side.
- The same release adds monthly rotation of `/srv/mu/audit.log` (keep 12; §2.3 Q9), because the dashboard's 3.1a
  polls old nodes with `list`, one audit line per call.
- Add the cause and the fix to the S3 record and to `docs/install/windows-compute-node.md` §11.

### Step 3. Dashboard (docs/design/dashboard.md)

The page is a React + TypeScript app built with Vite in CI and shipped as static files in each release, served by μ's
own Python server (owner's direction 2026-11-14, §2.3; design §3.1, §4.8). Phase 1 is split:

- **3.1a can start now, in parallel** with the pairing fixes, the auto-update diagnosis and the concurrent-jobs
  scheduler (being designed on branch `scheduler`): it changes nothing on the node, and needs only the owner's go-ahead.
- **3.1b waits** for the scheduler to merge (both change the node's runner) and for step 2 (node code reaches the PC
  only by auto-update). Its probe (3.0) runs after the scheduler, so it measures the runner 3.1b will change.
- Until 3.1b reaches the PC, the dashboard polls the node with `list` on a stated budget (design §4.3: every 15 s
  while a tab is visible and a job is unfinished, else 60 s; 5 min on battery). Each call leaves one `audit.log` line,
  so the `audit.log` rotation ships with step 2's release.
- Review of the plan (revision 3, design §11.2): approved with changes, all applied. On the Mac, npm runs only through
  a sandboxed wrapper; the committed page is always the one CI built.

| Sub-step | Design § | Effort | Needs the PC? |
|---|---|---|---|
| 3.0a Before 3.1a's release: check that the LaunchAgent can read the relay password from the Keychain on the Mac; and measure the CPU cost of one node call (`hello`, `list`) on **today's** PC with one job, which sets the old-node polling step | §7.0 | ½ day | Through one job, no visit; no node change |
| 3.1a **Phase 1a, starts on the owner's go-ahead:** the React app (design system, light and dark, the one page with every state), the Mac-side server (token, CSP with Trusted Types, `Host` check, static files), `mu dashboard on/off/open`, the LaunchAgent, the Mac's sampler, fake nodes and the golden fixtures, `jobs.index` from `mu submit`, the old-node polling budget, the `install.sh` excludes; the sandboxed npm wrapper and pinned Node on the Mac; the front-end CI (build artifact and diff, audit, licences, Playwright in Chromium and WebKit, accessibility, screenshots for the owner). Against today's PC it shows every job by node with state and growing log, and This Mac's load; node load says "update μ on office-pc". Done includes the owner's look at the screenshots | §7.1 (1a) | 8–11 days | No; one release, nothing new runs on the PC |
| 3.0 Gate and probe, **after the scheduler merges and step 2**: one probe job on the PC (cgroup files on the WSL kernel, what survives an `oom`, the sampler's cost with several jobs, the verb cost again) | §7.0 | ½–1 day | Through jobs only, no visit |
| 3.1b **Phase 1b:** the node's sampler (for several jobs at once, as the scheduler runs them), the `stats`/`watch`/`metrics` verbs, `usage` in `result.json` and `runs/<id>.json`, `stats` in `mu compute status --json`. The page's live node figures and the whole run's chart, built in 1a against fake nodes, switch on; the real node's answers checked against the same fixtures, and page changes if the scheduler changed the shapes | §7.1 (1b) | 3½–5 days | One release, delivered by auto-update |
| 3.2 Phase 2: history (SQLite on the Mac), Nodes and This-Mac pages, the **Mac vs node comparison** on the benchmark suite | §7.2 | 4–6 days | No |
| 3.3 Phase 2b: recording of local `mu run` work, opt-in, so project jobs can be compared too | §7.3 | 3–4 days | No |
| 3.4 Phase 3: solver progress (OpenFOAM residuals, CalculiX increments, Gmsh stages) and job-done notifications | §7.4 | 3–5 days | No |
| 3.5 Phase 4: results views, 3D previews, optionally a cancel button | §7.5 | 1–2 weeks | No; owner's choice when to start |

Phase 1 in all: 12–16 days. Phases 2–4 keep these ranges until 1a gives a measured pace.

Acceptance: the tests D01–D29, D34–D43, D46 and D47 of the design, each with its fault shown to fail (1a's in CI before its
release, 1b's after); **done when** the owner, with nothing started by hand, opens the page during a real job on the
office PC and sees what §7.1 lists for 1a, and then for the whole of phase 1.

### Step 4. Shell experience (docs/design/shell-ux.md)

| Sub-step | Design § | Effort | Needs the PC? |
|---|---|---|---|
| 4.1 Phase 1a: every result says the next step and where to run it; plain `mu` is a home screen; Claude Code's status line and two hooks (Claude is told what changed, so the owner need not ask); `mu pair` on the Mac | §8.2 | ~6 days | No |
| 4.2 Phase 1b: update, install and diagnose the node **from the Mac**; a thin `mu` at the PC (`pair`, `fix`, `update`, `host`) so nobody types `wsl` or `powershell` there; no more sudo prompts | §8.3 | ~4 days | Yes, additive only (see decisions) |
| 4.3 Phase 2: jobs by name, `@last`, notifications, failed jobs explain themselves, opt-in automatic fetch | §8.4 | ~2 days | No |
| 4.4 Phase 3: one-line installs, `mu setup`, `mu doctor --fix`, guided relay setup | §8.5 | ~19 days | **Deferred**: no new machine or engineer is expected soon |

Shared with the dashboard: the dashboard is built first, so **its status data (`jobs.index`, node `stats`, the
cache) is the source** the shell's `mu overview`, home screen, status line and hooks read. Phase 1a adapts to what
step 3 built; it does not build a second source.

### Step 5. Ongoing, not scheduled

- The restart tests on the office PC and the first real Windows Update restart (next office visit).
- A retention rule for finished jobs on the node (nothing is deleted today; `list` and `hello` slow down as jobs
  accumulate). Proposed default below; decide before the node holds about 1,000 jobs. (`audit.log` rotation is no
  longer here: it ships with step 2.)
- Headscale stays the fallback for the relay and pairing (PLAN §17) if they become a burden.

## 2. The owner's decisions (2026-11-14)

Answers given by the owner, and the lead's calls where the owner said "I trust your judgment". A later owner
decision overrides any of these; record it here with its date.

### 2.1 For both

| Topic | Decision | Why |
|---|---|---|
| Order | Pairing fixes → dashboard → shell experience | Owner |
| New machines | None expected soon (no second PC, Mac or engineer) | Owner. Shell phase 3 is deferred; nothing is built for a fresh machine before one is coming |
| Who types remote jobs | Almost always Claude Code | Owner. Commands are designed for Claude first (`--json`, `next`, `steps`); `mu run --job NAME` and `[jobs.*]` in `mu.toml` stay deferred |
| The office PC | **Not to be disturbed.** People in the office use it. No OS change, no Windows reinstall, no uninstall/reinstall of the host side for testing. Changes to it must be additive, reversible, delivered by auto-update where possible, and checked with `status` | Owner. A second Windows PC may be budgeted later for destructive tests |
| Windows-only tests | Run on the office PC only as a short, non-destructive manual checklist during an ordinary visit (status, `test-stop`, one pairing check). Anything that needs an uninstall/reinstall waits for a second PC | Follows from the above |
| Queue control (concurrent-jobs scheduler) | **Requirements, from a real case on 2026-11-14:** a 2-minute probe job sat 5th in the queue behind another Claude session's JATAYU jobs, and the only way to let it go first was to ask that session to cancel its queued jobs and resubmit them (losing their place). The scheduler must therefore give: (1) **hold / release** of a queued job, **move** (before/after another job) and **priority**, without cancelling; (2) **pause / resume of a running job** (systemd `freeze`/`thaw` of its cgroup: CPU released at once, memory kept, the job continues exactly where it stopped), so a long sweep can be paused briefly for a small urgent job, by hand and, as a policy, automatically for the express lane; (3) **one view of the whole node queue** across projects and Claude sessions (`list` without `project=` already returns all jobs), and the owner's workstation key allowed to arrange any job on the node; (4) small jobs must not need anyone to step aside: they start at once when they fit. Each with `--json` for Claude | Owner, 2026-11-14 ("the two caveats are also learnings") |
| Updates on a busy node | **Requirement, from 2026-11-14:** the job runner holds `state/busy` while any job is queued or running, and auto-update defers while it exists (`bin/mu` `au_busy`), so a node kept busy by Claude sessions never updates (v2026.11.9 needed a hand-submitted `mu update` job and another session cancelling its queue). Fix in the scheduler release: install a new release beside the old one while jobs run (jobs use their project's pin, so they are unaffected), switch the default and restart the runner at the first moment no job is *starting*, and run `gc` only when idle; plus `mu compute update NODE` from the Mac (shell-ux 1b, brought forward) running as an express job | Lead, 2026-11-14 |

### 2.2 Shell experience (docs/design/shell-ux.md §10)

| Q | Decision | Why |
|---|---|---|
| Q1 next machine | Not soon: phase 3 deferred | Owner |
| Q2 first script on a fresh PC | Deferred with phase 3. When it comes back: a small public bootstrap repo (the design's recommendation) | Lead |
| Q3 Claude Code settings | In each μ project's committed `.claude/settings.json`, written by `mu link` (status line and hooks), with `mu link --no-claude-settings` to opt out. The owner's own user settings are not touched | Lead: every clone of a project gets it; nothing global changes |
| Q4 secret on the clipboard | No. Secrets stay in the Keychain, hidden prompts and Terminal-only steps | Lead: phase 3 is deferred, and the safer path works |
| Q5 deliver releases over the tunnel instead of GitHub | No for now; the spike in phase 1b only measures it. The node keeps its read-only deploy key | Lead: the node then keeps updating while the Mac is off |
| Q6 who types jobs | Claude: `mu run --job` stays deferred | Owner |
| Q7 `mu fix` restarting the distro | Asks first (one `[Y/n]`), and refuses while a job runs | Lead: people use the PC |
| Q8 root package updater on the node | No. `mu fix` at the PC stays, with an early warning from the Mac (`system_packages_missing`) so a visit can be planned | Lead: an always-on root updater needs a security review; package changes are rare |
| Q9 shared schema with the dashboard | The dashboard defines it (built first); the shell reads it | Lead, follows the order |
| Q10 Windows test window | No uninstall/reinstall on the office PC (see 2.1). Phase 1b's Windows-only checks become the non-destructive checklist | Owner |
| Q11 automatic fetch | Yes, opt-in, into `runs/<id>/` only, never over the working tree (phase 2) | Lead: Claude drives jobs; this saves a step without risking files |

### 2.3 Dashboard (docs/design/dashboard.md §9)

Decided by the owner:

| Topic | Decision | Why |
|---|---|---|
| Front end (2026-11-14) | **React + TypeScript + Vite**, a static single-page app built in CI; the built files committed in `share/dashboard/` and shipped in each release; μ's stdlib Python server serves them and the API and keeps the token, `Host` check and strict CSP. No Node.js on users' machines (the owner's Mac, which is also where the page is developed, has a pinned, sandboxed copy for building only). **Not Next.js** | Owner: "a little nice", and room for libraries later. The lead: Next.js's value (server rendering, server components, API routes) needs a Node.js server on every workstation, a second runtime and a second server to secure; a static export is React with a heavier build (design §3.1) |

The other questions have not been answered yet; the lead's defaults below apply until the owner says otherwise.

| Q | Default | Why |
|---|---|---|
| 1 Always on | Yes: `mu dashboard on` by default on a workstation with a paired node; idle costs nothing | The owner asked for a dashboard "whenever the toolchain is running" |
| 2 Windows' own load on the PC | Show the WSL2 VM's figures with a clear label; no Windows-side task | The office PC is not to be disturbed (2.1) |
| 3 Read-only | Yes for phases 1–3; copy-the-command buttons instead of actions; cancel only in phase 4 if wanted; fetch never | Safety of a local web page |
| 4 Record local `mu run` | Opt-in at first (phase 2b) | The design's recommendation |
| 5 Comparison basis | Benchmarks (phase 2) **and** real project jobs (phase 2b); the JATAYU `perf/` log is imported as a source | The owner asked for a Mac vs PC comparison of real work |
| 6 Notifications | Yes, opt-in (phase 3) | Claude drives jobs; a notification tells the owner without asking |
| 7 Others' jobs | Not relevant until a second workstation; default filter "this workstation" | No new machine soon |
| 8 Reverse tunnel/exporter on the node | Never | Security model |
| 9 Retention on the node | Archive finished jobs older than 90 days into `/srv/mu/archive/`; rotate `audit.log` monthly, keep 12. **The rotation ships with step 2's release** (the first that can reach the node; 3.1a's polling of old nodes adds audit lines); archiving can follow with 3.1b | Keeps `list`/`hello` fast and the audit log bounded |
| 10 History length | As designed (Mac raw 48 h, 1-min rollups 90 days; node host history 7 days) | Enough for weekly comparisons |
| 11 The look | A neutral, technical palette with one blue accent, light and dark (design §5.3); Martand's colours or logo if the owner sends them | "A little nice" without a brand decision blocking work |
| 12 Release 1a on its own | Yes | The owner sees jobs by node and their logs weeks before 1b can reach the PC |

## 3. How to start a step later

1. Read this file's step and its decisions, then the design document's sections it names.
2. Check that the step's prerequisites hold (the release on both machines, `mu compute status office-pc` exit 0,
   `hello.autoupdate.last_result` `ok`).
3. Re-read the design's "Read in sources" and code references against the current `main`: names and line numbers
   will have moved. Where the code changed underneath the design, update the design first, in its own commit.
4. Build on a branch in a worktree; prove it in CI (each test with its fault shown to fail); have it reviewed
   independently (security, and usability for Claude Code); merge and release; confirm the machines took the release
   by auto-update.
5. Record what was measured in `docs/verification/`, and update §0 of this file.
