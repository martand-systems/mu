# μ roadmap: what is built next, in what order, and why

- **Written:** 2026-11-14, after the first remote job ran on the office PC (release v2026.11.8).
- **Owner's order:** finish the pairing fixes → build the **dashboard** → then the **shell experience**.
- **The plans themselves** are [docs/design/dashboard.md](design/dashboard.md) and
  [docs/design/shell-ux.md](design/shell-ux.md). Each was researched by several agents, reviewed by an independent
  reviewer, and revised. This file fixes their order, records the owner's decisions, and lists what must be true
  before each step starts. It is meant to stay valid however much else changes before the work begins: where a
  design document and this file disagree about **order or decisions**, this file wins; about **mechanism**, the
  design document wins.
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
- Add the cause and the fix to the S3 record and to `docs/install/windows-compute-node.md` §11.

### Step 3. Dashboard (docs/design/dashboard.md)

| Sub-step | Design § | Effort | Needs the PC? |
|---|---|---|---|
| 3.0 Gate and probe: auto-update proven (step 2); one probe job on the PC (cgroup files on the WSL kernel, what survives an `oom`, the CPU cost of one verb call); the LaunchAgent can read the Keychain on the Mac | §7.0 | ½–1 day | Through jobs only, no visit |
| 3.1 Phase 1: one page — jobs by node with state, live cores and memory per job against its limit, the whole run's chart, both machines' load with the age of each figure; `mu dashboard on/off/open`; μ starts recording CPU time and peak memory per job (`usage` in `result.json` and `runs/<id>.json`) | §7.1 | 6–9 days | One release, delivered by auto-update |
| 3.2 Phase 2: history (SQLite on the Mac), Nodes and This-Mac pages, the **Mac vs node comparison** on the benchmark suite | §7.2 | 4–6 days | No |
| 3.3 Phase 2b: recording of local `mu run` work, opt-in, so project jobs can be compared too | §7.3 | 3–4 days | No |
| 3.4 Phase 3: solver progress (OpenFOAM residuals, CalculiX increments, Gmsh stages) and job-done notifications | §7.4 | 3–5 days | No |
| 3.5 Phase 4: results views, 3D previews, optionally a cancel button | §7.5 | 1–2 weeks | No; owner's choice when to start |

Acceptance: the tests D01–D27 of the design, each with its fault shown to fail; **done when** the owner, with nothing
started by hand, opens the page during a real job on the office PC and sees what §7.1 lists.

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
- A retention rule for finished jobs and `/srv/mu/audit.log` on the node (nothing is deleted today; `list` and
  `hello` slow down as jobs accumulate). Proposed default below; decide before the node holds about 1,000 jobs.
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

The owner has not answered these yet; the lead's defaults below apply until the owner says otherwise.

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
| 9 Retention on the node | Archive finished jobs older than 90 days into `/srv/mu/archive/`; rotate `audit.log` monthly, keep 12. Built with step 2 or 3.1 | Keeps `list`/`hello` fast |
| 10 History length | As designed (Mac raw 48 h, 1-min rollups 90 days; node host history 7 days) | Enough for weekly comparisons |

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
