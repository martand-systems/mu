# μ shell experience: say the next step, and stop the owner having to ask

The plan for making μ simple to use from a terminal, for the engineer and for Claude Code driving it. Research and
planning only: no code was changed. Read at `v2026.11.8` (branch `shell-ux-research`). It draws on research notes
written on 2026-11-14 (scratchpad `shell-ux/`): A, the friction audit and target journeys; B, the inventory of every
command, flag, help source and exit code (not kept in the scratchpad: §1.2 is spot-checked against the code, not
re-derived); C, CLI and setup patterns in other tools; D, one-line install and a Windows side without `wsl`. Where the
notes disagreed, §12 records which way this plan went.

**Revision 2 (2026-11-14), after review.** The first draft optimised a new user's setup and put the owner's
recurring work, and Claude Code itself, last. This revision re-cuts the phases around the owner's week (one Mac, one
PC, a running relay), adds the Claude Code side to phase 1, makes the Windows `mu` a thin launcher, and moves
new-machine work to "when a new machine is real". §13 lists every review finding and what was done with it.

Related work running separately, which this plan fits and does not repeat: the **local web dashboard** (jobs, nodes,
live CPU and memory; §7) and the **one-code pairing fixes and install guides**. Where an item may already be covered
by the pairing fixes, it says so.

Sections: [0 Summary](#0-summary) · [1 The problem in numbers](#1-the-problem-in-numbers) ·
[2 Principles](#2-principles) · [3 The output contract](#3-the-output-contract) ·
[4 Target command surface](#4-the-target-command-surface) · [5 Journeys before and after](#5-journeys-before-and-after) ·
[6 Build or adopt](#6-build-or-adopt-plan-17) · [7 The dashboard](#7-how-this-fits-the-dashboard) ·
[8 Phases](#8-phases) · [9 Risks](#9-risks) · [10 Questions for the owner](#10-questions-for-the-owner) ·
[11 Not done here](#11-not-done-here) · [12 Where the research notes disagreed](#12-where-the-research-notes-disagreed) ·
[13 Review](#13-review)

## 0. Summary

- **The owner's week today** (one Mac, one PC, a running relay; assumed 5 remote jobs, a daily "what's the status?",
  one node-side version mismatch): about **27 asks to Claude**, about **1 visit to the PC** with 2–3 typed lines (one
  of which μ itself prints wrong), and a further PC visit with ~3 lines whenever a release needs an Ubuntu package.
- **Why the owner keeps asking:** μ knows the state but never says the next step on success; Claude Code is never
  told what changed (a job finishing, a node going offline, an update waiting); the same action is spelled
  differently in four shells and μ's own printed PC lines fail (`mu: command not found`); fixes that could run from
  the Mac are printed as "on the PC: …".
- **After phase 1** (about 10 days): about **5 asks a week**, **0 routine PC visits**, and **1 short line** at the PC
  (`mu fix`) only when a release needs an Ubuntu package, warned days ahead. Claude sees μ's state in its status line
  and is told of changes before each prompt.
- **After phase 2** (about 2 days): about **1 ask a week**; jobs are addressed by name or `@last`; a macOS
  notification when a job ends; failed jobs say why and what to try.
- **New machine** (second PC, Mac or user): today about 42 commands and about 35 web-UI actions. Phase 1 trims
  pairing to `mu pair` plus the code at the PC. The rest (one-line Mac and PC installs, `mu setup`, `mu relay setup`)
  is phase 3, built **when a new machine is actually coming** (§10 Q1); it reaches about 5 commands plus the code, and
  about 28 UI actions for a first relay (service creation stays a checklist), 3 for a key rotation.
- **How:** one output contract (every result names its next step, where it runs and who may run it, §3); a home
  screen at plain `mu` fed by `mu overview` (§4.2), which also feeds Claude Code's status line and hooks (§4.9); fixes
  run from the Mac where they run as user `mu` (§4.6); a thin `mu` at the PC for what must happen there (§4.5).
- **Build, not adopt:** no new dependency. A stdlib `mu/ui.py` and a bash 3.2 `ui.sh`; Claude Code's own status line
  and hooks are adopted as they are (§6).

## 1. The problem in numbers

### 1.1 Journeys today

Counting rules (note A): a **command** is a line a person types (shell, PowerShell, or a code); a **UI action** is one
field or button in a web UI; a **switch** is a move between the six places; a **secret** is a value that grants
access and that the person sees, copies or types; an **ask** is the owner asking Claude "what now?" or "what's the
status?". Counts marked `~` are estimates from the session, not measured.

| # | Journey | Recurs for the owner? | Commands / UI actions | Places (switches) | Secrets on screen | Failure points | Asks |
|---|---|---|---|---|---|---|---|
| J1 | New Mac to first local run | no (next Mac) | ~10 / 0 + GitHub sign-in | T, C, G (~4) | 1 | 6: auth, `PATH`, `dev-<commit>` label, auto-update failing silently, forgotten `mu link`, missing pin | ~3 |
| J2 | New Windows PC to node installed (scripted) | no (next PC) | ~8 / ~5 | W, G, L, T (~8) | 0 | 5: private repo, reboot and re-run, deploy key + org setting, admin window open 1–2 h, sudo prompt | ~5 |
| J2′ | the same by hand (what S3 actually was) | no | ~40 / ~5 | W, L, G, T, C (~15) | 1 | many | many |
| J3 | Relay, first time | no (rotation only) | ~10 / ~25 | T (not C), D (~6) | **4–5 printed** | 5: Isolated Deployment, Randomize Compose, Let's Encrypt limit, Deploy not pressed, a misspelt middleware (404) | ~3 |
| J4 | Pairing a node | rarely (re-pair, next PC) | Mac 1; PC 2 lines (~190 characters) + 24-character code; often +1 PATH fix | C/T, W, L (~3) | 1 (the code) | 2: `mu: command not found` in the line μ prints; `-File ''` when the release lacks `pair` | ~2 |
| J5 | One remote job | **yes, several a week** | ~7 commands, ~10 flags, a 22-character id typed 3–4 times (mostly by Claude) | T/C | 0 | 4: pin missing on node, 2.1 GB untracked folder, LFS and submodules, Claude's wait loop under its time limit | 3–5 |
| J6 | Recovery (offline, version, sudo, failed job, Mac asleep) | **yes** | 2–6 each, mostly at the PC | C, W, L (2–4) | 0 | — | 1–2 each |
| | **New user to first remote result (J1–J5)** | | **~42 / ~35** | **6 places, ~25 switches** | **5–6** | **~22** | **~15** |

### 1.2 The surface today (note B, spot-checked)

- **6 entry points, 4 grammars, 4 contexts:** `bin/mu` (bash), the Python job commands, `mu node` (node side),
  `mu-toolchain`, `mu-wsl2-distro.sh` (root in WSL), `mu-wsl2-host.ps1` (Windows admin), plus `gen-credentials.sh` and
  `install.sh`. They run in macOS Terminal, Windows admin PowerShell, WSL as `mu`, or WSL as root, and no command says
  which context it belongs in.
- **"Status" means five things:** plain `mu` (toolchain), `mu status` (jobs), `mu compute status`, the distro's
  `status`, the PowerShell script's `status`. Checks live in three places (`mu doctor`, `mu node selftest`, the distro
  `status`), with no single "is everything ready?" view.
- **Next steps only on errors, only in Python.** `cli.py` errors carry `next`; successes never do, and `bin/mu`'s `die`,
  the PowerShell and the distro script mostly have none.
- **`--json` is missing** from install, update, use, pin, remove, gc, versions, plain `mu`, `gen-credentials.sh`; the
  distro and PowerShell scripts have it on `status` only, spelled `-Json` in PowerShell.
- **Five ways to pass a secret or code on stdin**, four waiting primitives with different exit codes, durations in
  seconds in some flags and `6h` in others, three confirmation styles (`--yes`, `--force`, typing the name).
- **Exit code 3** means "the node lacks the pinned μ" in the job layer and "no toolchain installed" in `bin/mu`.
- **Nothing reaches Claude Code unless Claude asks.** PLAN P2's SessionStart hook (`mu context`) is not built; there
  is no status line, and a job ending mid-session is noticed only when someone asks.

### 1.3 Root causes

| # | Cause | Seen in |
|---|---|---|
| R1 | Four shells, each with its own spelling; nothing says which shell you are in | owner typed PowerShell into Linux; J2, J4, J6 |
| R2 | `wsl -d mu-compute -u mu -- mu …` is not a login shell, so `~/.local/bin` is not on `PATH`. μ prints this line itself: `invite.py` `PC_UPDATE`, `PC_PAIR`; `jobs.py` `PC_MU` (every "on the PC: …" hint) | J4, J6 |
| R3 | No home screen for the system; background work (auto-update, open invites, running jobs) is invisible | the repeated "what's the status?" |
| R4 | The order of steps lives in documents (the first-job checklist is 554 lines, the pairing design 1,485), not in μ | "what do I type next?" |
| R5 | Secrets go through the terminal (`gen-credentials.sh env`, `client`), so steps must be typed by hand outside Claude | J3 |
| R6 | Admin steps fall to user `mu`, who has no sudo; `install.sh` line 197 prompts for sudo whenever stdin is a terminal, even for a non-sudoer | J6c |
| R7 | The PC needs GitHub access to a private repo, and a deploy key added by hand in the GitHub UI | J2 |
| R8 | Remote jobs are four verbs keyed by a 22-character id; nothing notifies | J5 |
| R9 | Problems are found at submit time, not before (pin missing on node, big untracked folder, LFS) | J5, J6b |
| R10 | Claude Code learns μ's state only by running commands; changes between prompts never reach it | every "is it done?" |
| R11 | Fixes that run as user `mu` (update, install a version, enable, selftest) can only be started at the PC, though the Mac already has an authenticated tunnel to that user | J6b, J6a |

## 2. Principles

Each principle has a check in §8 that fails the build when it is broken.

1. **μ always says the next step.** Every command on the paths a journey uses, on success and on failure, ends with
   one `next:` line (human) and a `steps` list (JSON). Plain `mu` says what to do now.
2. **The measure is "nobody had to ask".** A journey is done when an agent with no documents, following only what μ
   prints, gets from the start to the result (§8, the walkthrough test).
3. **Claude is told, not only able to ask.** What changed since the last prompt reaches Claude Code without anyone
   asking; the status line shows what is running and what needs the owner.
4. **Do it from here before sending anyone to the PC.** Anything that runs as user `mu` is started from the Mac
   through the tunnel. Only root and Windows-level work is printed as "at the PC".
5. **Nothing secret through Claude.** No secret in any output, argument list, log or JSON. Steps that handle a secret
   are `by: human, secret: true` and run in Terminal, never by Claude.
6. **Agent-friendly everywhere.** Every command on the journey paths takes `--json` (one object on stdout, stable
   error names, the exit codes of `proto.py`), never prompts without a terminal or with `--json`, and accepts `--yes`.
7. **Windows users never type `wsl`, `powershell -File`, or a Linux path.** μ never prints a command containing
   `wsl -d` or `ExecutionPolicy` for a person to type (enforced once the Windows `mu` ships, §8 phase 1b).
8. **Every printed command says where it runs** when that is not here: `at the PC:`, `in Dokploy:`, `in a browser:`.
9. **Every printed command exists.** A `next` or `steps[].cmd` that does not parse as a command of the same release
   fails the build.
10. **Old spellings keep working.** Every rename keeps an alias for at least three releases; Claude's instructions
    change in the same release. Meanings of existing commands do not change.
11. **One source of state.** The home screen, Claude's status line and hooks, and the web dashboard read the same
    `mu overview`.

## 3. The output contract

One envelope for every command, built in one place (`proto.py` for Python, `ui.sh` for the dispatcher):

```json
{"ok": false, "exit": 3, "error": "pin-not-on-node",
 "message": "office-pc has no μ v2026.11.7 (this project pins it)",
 "next": "mu compute install office-pc v2026.11.7",
 "steps": [
   {"cmd": "mu compute install office-pc v2026.11.7", "where": "here", "by": "agent", "why": "installs the pin on the node (about 4 min)"},
   {"cmd": "mu pin v2026.11.8", "where": "here", "by": "ask", "why": "or move this project to the node's version"}
 ],
 "data": {}}
```

| Field | Rule |
|---|---|
| `next` | Kept as today: one human-readable string, the first step. Existing readers (Claude's instructions, scripts) keep working. Decided here (note C asked): add `steps`, do not change `next`'s type |
| `steps[].where` | `here`, `pc` (any PowerShell at the PC, via the Windows `mu`), `terminal` (the Mac's Terminal, not Claude: secret steps), `dokploy`, `browser` |
| `steps[].by` | `agent`: safe for Claude to run. `ask`: changes the project or costs time; Claude asks first. `human`: a person must act (at the PC, in a browser, a code to type, a secret) |
| `steps[].secret` | `true` when the step involves a secret. Always with `by: human`. Claude must not run it, read its output, or ask the person to paste the result; it relays the line verbatim with its `where` |
| `data` | The command's result (on success) |

- **Node-supplied text never sets `next`, `steps`, `where`, `by` or `secret`.** `cli.py` already protects `exit` and
  `next` from node-supplied extras; the same rule covers the new fields, and node text stays in `node_message`.
- **Human form:** results on stdout; progress, spinners and the footer on stderr. The footer is one dim line,
  `next: at the PC: mu fix`. Colour and spinners only on a terminal, honouring `NO_COLOR` (as `bin/mu` does now).
- **Prompts** (`Do it now? [Y/n]`) only when stdin and stderr are terminals, no `--json`, no `--yes`. Without a
  terminal μ never prompts: it exits with the step in `steps`. That is the path Claude takes.
- **Exit codes** stay those of `proto.py`. `bin/mu`'s "no toolchain installed" (3) is defined as the same meaning,
  "the needed μ version is not installed", and documented once.
- **A small command table** (a data file read by `bin/mu` and Python: verb, sub-verb, place, `by`, exit codes) is
  built in phase 1 for the tests (every printed command parses, every journey command has `--json`) and for
  `mu help --json`. Shell completions from it are a later, low-priority addition (§4.8).

## 4. The target command surface

### 4.1 The command set

Grammar: top-level verbs for what is typed daily (toolchain and jobs); a noun then a verb for the rest (`compute`,
`relay`, `host`). Old forms stay as aliases (principle 10). Commands marked *PC* are the Windows `mu` (§4.5). The
phase is in the last column.

| Target | Today | Alias kept | Notes | Phase |
|---|---|---|---|---|
| `mu` | toolchain status | — | the home screen (§4.2). Toolchain lines move into it | 1 |
| `mu overview [--json] [--brief] [--changes]` | — | | the data behind `mu`, Claude's status line and hooks (§4.9), and the dashboard | 1 |
| `mu pair NODE` (Mac) | `mu compute invite NODE` | `mu compute invite` | the same word at both ends; done now, while `invite` is one release old | 1 |
| `mu compute update\|install\|enable\|selftest NODE [V]` | only at the PC | | runs as user `mu` on the node through the tunnel; `by: agent` (§4.6) | 1 |
| `mu compute list\|status\|remove NODE` | same | `mu compute` alone = list | | — |
| `mu install\|update\|use\|pin\|versions\|remove\|gc\|autoupdate` | same | `upgrade`, `default`, `ls`, `list`, `rm`, `uninstall`, `auto-update` | `install` and `update` gain `--json` and `next` in phase 1 (journey paths); the rest later | 1 / later |
| `mu submit\|status\|log\|fetch\|cancel` | same | | `next` and `steps` on every result in phase 1; names, `@last`, id prefixes in phase 2 | 1, 2 |
| `mu jobs [NAME]` | `mu status [JOB]` | | a synonym only; `mu status` keeps its meaning (the job list) | 2 |
| `mu pair` (*PC*) | two long PowerShell lines | | asks for the code (§4.5) | 1 |
| `mu` (*PC*) | the PowerShell `status` | | the node's home screen | 1 |
| `mu fix` (*PC*) | the PowerShell `status`, then whatever it said | | applies what `mu` at the PC found failing, including the root package step (§4.6) | 1 |
| `mu update` (*PC*) | `wsl … mu update` + re-running the host script | | updates the μ release in the distro and the host files, with one UAC prompt | 1 |
| `mu host install\|status\|test-stop\|uninstall` (*PC*) | `powershell -NoProfile -ExecutionPolicy Bypass -File C:\Mu\host\mu-wsl2-host.ps1 …` | | the same verbs, through the launcher | 1 |
| `mu help [CMD] [--json]` | six help mechanisms | | from the command table | 1 (`--json`), later (text) |
| `mu doctor [--fix] [--on NODE]` | `mu doctor` (tools only) | | the check engine (§4.4) | 3 |
| `mu setup [mac\|relay\|pc]` | install.sh, link, relay login, invite… | | the guided path for a new machine (§4.3) | 3 |
| `mu relay setup\|login\|rotate\|status\|clients\|remove\|backup\|restore` | `gen-credentials.sh` ×4 + `relay login --stdin` | `--stdin` | `setup`, `rotate`, `login` are `by: human, secret: true` (§4.7) | 3 |
| `mu run --job NAME [--on NODE]`, `mu attach NAME` | `mu submit` + `status --wait` + `log` + `fetch` | | only if the owner types jobs by hand (§4.6, §10 Q6) | on request |
| `mu completion bash\|zsh` | — | | from the command table; low value while Claude does most typing. No fish | later |
| `mu node …` | same | | the node's own side; hidden from the Mac's help | — |

Not planned (decided in §13): `mu shell` and `mu root` at the PC, a `command_not_found_handle` in the distro,
changing what `mu status` means.

Flag rules for commands on the journey paths: `--json`, `--yes`, `--no-input`. Durations accept units everywhere
(`--timeout 10m`; a bare number stays seconds). Secrets and codes are read from a hidden prompt, a dialog, or a file
μ itself wrote; never from an argument.

### 4.2 Plain `mu`: the home screen

Built from `mu overview --json` (§4.9): local files plus a cached check of the relay and nodes, shown with its age.
At most three "needs you" items, then one `next:`. Every command it prints exists in the same release (principle 9).

End of phase 1 (job names as given to `--name`; addressing is still by id; `mu fix` exists from phase 1b, and in
1a the same line prints the transitional root command of §4.6):

```
μ v2026.11.8 · auto-update on (next 04:12) · jatayu: pinned v2026.11.8
nodes   office-pc   online (checked 40 s ago) · μ v2026.11.8 · idle · 412 GB free
jobs    sweep-aoa   running 41 min on office-pc                 mu log 01JD3KQ8Z5M2X7RVA4HC9P
        mesh-conv   succeeded 09:12, not fetched                mu fetch 01JD3H2N7W4Y8BQX0TK6FE
needs you
  ! office-pc: v2026.11.9 (auto-update tonight) needs one Ubuntu package   at the PC: mu fix
next: mu fetch 01JD3H2N7W4Y8BQX0TK6FE
```

Phase 2 replaces the ids with names (`mu fetch mesh-conv`) and adds `@last`.

- Nothing set up: `μ is not set up on this Mac.` and the install steps (phase 1); `next: mu setup` only from phase 3.
- Background work always appears with its age: an auto-update install, an open invite, a node still installing, a
  running job, a relay reconnecting.
- Node offline: "not answering since 09:40 (last checked 1 min ago)" from the cache. The likely reason from the
  relay's last-seen time needs a relay admin API change (later).
- Host files at the PC older than the node's μ release: `office-pc: Windows side is v2026.11.6 · at the PC: mu
  update` (§4.5).
- At the PC, `mu` shows the same screen from the node's side: host task, distro, tunnel, runner, paired Mac.

### 4.3 `mu setup`: one guided path per role (phase 3, when a new machine is coming)

The same engine as `mu doctor --fix` (§4.4), run in order: show the plan, ask once, do each step with an OK/FAIL line,
verify, print the next step. `--yes` for Claude where no secret is involved; `--dry-run` prints the plan only.

| Role | What it does |
|---|---|
| `mu setup mac` (run at the end of `install.sh`) | installs the latest release tag (a checkout counts only with `--dev`); turns auto-update on and shows the error if that fails; puts `mu` on `PATH`; offers to clone a project, links it, installs its pin, offers the Claude Code settings (§4.9); hands the relay login to Terminal (`by: human, secret: true`); ends with `next: open Claude Code in ~/…/jatayu` |
| `mu setup relay` (= `mu relay setup`) | §4.7, in Terminal |
| `mu setup pc` (*PC*) | what `mu host install` does today, then pairing, then the μ install in the background |

`mu link` becomes automatic: any `mu` command in a project whose `.mu/toolchain` is missing or stale re-links it and
says so in one line.

### 4.4 `mu doctor` and `mu doctor --fix`: one check engine (phase 3)

Each check: `{id, group, status: ok|warn|fail, detail, fix: step|null}` using the step fields of §3.

| Group | Checks |
|---|---|
| toolchain | the current doctor's checks; layers sealed; default installed; `mu` on `PATH` for login and non-login shells |
| updates | auto-update on and scheduled; last result; a release that needs a system package (said before it fails) |
| this project | `mu.toml`; pin installed; linked; untracked folders over the snapshot limit; LFS or submodule content (a warning for remote jobs) |
| relay | logged in; reachable; fingerprint and store id unchanged |
| nodes | answering; version; the pins this Mac's projects need; runner enabled; disk; last stop clean; host files current |
| PC (`mu doctor` at the PC, or `--on NODE` via the node) | startup task, Fast Startup and ARSO off, tunnel, deploy key, install receipt matches |

`--fix` applies only fixes with `by: agent` that need no secret and no root; it prints the rest as steps with their
place. An install receipt (the nix-installer pattern) makes "what did μ change on this machine?" answerable and lets
`uninstall` reverse exactly that. Phase 1's `overview` already carries the checks the home screen needs; the engine
generalises them.

### 4.5 `mu` at the PC: a thin launcher

`C:\Mu\bin\mu.exe`, compiled on the PC by `mu-wsl2-host.ps1 install` with the in-box C# compiler (as the shutdown
service already is: no signing certificate, no Mark-of-the-Web), `C:\Mu\bin` on the machine `PATH`. About 40 lines of
C#, with no logic of its own:

- **It always runs** `powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Mu\host\mu-wsl2-host.ps1 run <verb>`
  and passes the exit code back. The installed `.ps1` gains a `run` verb that dispatches the fixed list below. The
  WSL session guard (a `wsl.exe` call from a signed-in session before the startup task has started the distro ties
  the distro's life to that sign-in) stays only in the `.ps1`: one copy of the rule.
- **Fixed verb list, no free arguments:** plain `mu` (the node's home screen), `pair`, `fix`, `update`,
  `host install|status|test-stop|uninstall`. Unknown verbs print the list. No general forwarding into the distro, so
  no quoting layer to get wrong. (With a fixed list a `.cmd` shim would also work; `mu.exe` is kept because `.cmd`
  asks "Terminate batch job (Y/N)?" on Ctrl-C and the compiler is already used.)
- **Into the distro** the `.ps1` calls `wsl.exe -d mu-compute -u mu --exec /home/mu/.local/bin/mu …` with fixed
  arguments (the R2 fix). The node's `mu` sets `PATH`, `LANG` and `XDG_RUNTIME_DIR` itself when it finds them missing
  (to verify at the PC: `systemctl --user` under `--exec`).
- **Elevation:** verbs that need Admin relaunch the `.ps1` elevated with one UAC prompt, output redirected to a file
  under `C:\Mu\logs` that the unelevated parent shows. `mu pair` is split (note D, W2): the elevated part does only
  host settings; the 24-character code is read by the unelevated part with echo off, never in an argument, a UAC
  window, a transcript or `C:\Mu\logs` (which Windows Users can read).
- **`mu fix`** runs what the node's home screen lists as failing, including the root package step (§4.6) through
  `wsl.exe -d mu-compute -u root --exec /usr/local/lib/mu/mu-system-packages` (the owner is the Windows admin; user
  `mu` gets no sudo).
- **Where-am-I:** output from `mu.exe` is headed `[PC]`. The distro script sets the Linux prompt to
  `[linux mu@office-pc] ~$` (and `[linux root@office-pc]`), so a PowerShell line typed there is visibly in the wrong
  place.
- `WSL_UTF8=1` and code page 65001 so `μ` prints; a Start-menu entry "μ compute node" opens `mu` and waits for a key.

**How the Windows side is updated.** The distro cannot reach Windows (interop is off), so nothing updates
`C:\Mu\host` or `mu.exe` by itself.
- The startup task passes the host files' version to the distro when it starts it (to verify against how the task
  starts the distro today); the node reports it in `info`, and the Mac's home screen shows `at the PC: mu update` when
  it is older than the node's μ release.
- `mu update` at the PC updates the release in the distro (as `mu`), then copies the new host files into
  `C:\Mu\host` elevated, after showing the release version and the files' SHA-256 (the Mac's `mu compute status`
  shows the expected hash, from its own copy of the same release). `mu.exe` is recompiled only when its source hash
  changes, which should be rare for 40 fixed lines.
- This copy runs mu-owned files as Windows admin, as today's `PC_PAIR` line already does (`-File (wsl … mu node
  host-script)`). §9 records it; the hash shown on both sides is the check until a better one exists.

### 4.6 From the Mac first, then the PC; remote jobs

**Node maintenance from the Mac (phase 1).** New tunnel verbs on the node, each running as user `mu` in a transient
unit with a log, the same authority a paired Mac already has through jobs:

| Mac command | Runs on the node | Replaces |
|---|---|---|
| `mu compute update office-pc` | `mu update` | `at the PC: wsl … mu update` |
| `mu compute install office-pc V` | `mu install V` (job layer stage 2's planned verb) | a PC visit for a pin the node lacks (J6b) |
| `mu compute enable office-pc` | `mu node enable` | a PC visit after a runner stop |
| `mu compute selftest office-pc` | `mu node selftest` | a PC visit to diagnose |

All are `by: agent`. What remains "at the PC" is root work (packages) and Windows work (host files, power settings).

**Delivering the release from the Mac (spike in phase 1, build in phase 3 if accepted).** Today the node fetches μ
from GitHub with a deploy key, which needed an org setting and a hand step. The job layer already pushes git
snapshots to the node over the tunnel (`receive-pack`). The same path could push a μ release tag from the Mac's own
copy into a node-side bare repo, and `mu compute update` would install from it.
- It removes the deploy key, the org setting and the node's GitHub access (R7).
- It needs no relay storage and no signing key: the push is authenticated by pairing, and a paired Mac can already
  run any code as `mu` through a job, so user `mu` trusts nothing new.
- Open points: the node then updates only when a Mac is online (auto-update becomes "the Mac's auto-update pushes
  after installing"); a tag already present with a different commit is refused (tags can be moved on GitHub, not on
  the node); the root package step must not trust it more than today (it does not: see the root package step below).
- Note D dismissed this as large, assuming relay storage and a signing key; the push path needs neither. The spike
  (half a day) measures push size and time for a release over the relay.

**The root package step (phase 1).** `install.sh` stops prompting for sudo when `sudo -n -l` fails (line 197) and
fails within seconds with the step. The step is a **root-owned** script, `/usr/local/lib/mu/mu-system-packages`,
installed by the distro script. It reads the release's `toolchain/linux-64/apt.txt`, accepts only lines matching
`^[a-z0-9][a-z0-9.+-]*$` (at most 64), shows them, and runs `apt-get install --no-install-recommends` from the
configured Ubuntu sources only. It never runs code from the mu-owned checkout (no `install.sh --system` as root).
- Phase 1's exact step, printed by the node and the home screen: `at the PC: mu fix`. Until the PC has the phase-1
  host files: `at the PC: wsl -d mu-compute -u root --exec /usr/local/lib/mu/mu-system-packages`, which is the last
  `wsl` line μ prints, and only once.
- **Warned ahead:** the release check in CI flags any change to `apt.txt` between tags. The node, when it sees a newer
  release, compares its `apt.txt` with `dpkg-query` (no root needed) and reports `system_packages_missing` in `info`
  (an additive field; an older node simply lacks it). Auto-update holds that release instead of failing at 04:00, and
  the home screen says so.

**Remote jobs, phase 2 (about 2 days).**
- **Names as handles:** the newest job of that name in this clone (`--name` already exists at submit), `@last`, a
  unique id prefix. An ambiguous name exits 2 with the list. Old full ids work everywhere.
- **A notification when a job ends:** a LaunchAgent `mu watch` that runs only while `.mu/jobs` has unfinished jobs,
  using the `au_notify` code auto-update already has: "sweep-aoa succeeded on office-pc (41 min). mu fetch sweep-aoa".
  It catches up on wake. It also refreshes the `overview` cache, so Claude's hooks see the change (§4.9).
- **Failed jobs** show the class and the last 10 log lines, with a step: for `oom`, "peak 15.8 of 16 G: resubmit with
  `--mem 24G`".
- **For Claude:** `mu submit` once, then move on; the hooks report the finish. No more `mu status --wait --timeout
  500` loops.

**Deferred until the owner says he types jobs by hand (§10 Q6):** a blocking `mu run --job NAME --on NODE` that
submits, follows, fetches and detaches on Ctrl-C; `mu attach`; named job specs (`[jobs.sweep]`) in `mu.toml`;
`--again`. If built: `mu run --job sweep` (not `mu run sweep`, which today runs a program called `sweep`), and the
default node lives in per-clone config (`.mu/config`), not in the committed `mu.toml`, which must not name one
workstation's node.

**Later, owner's week:** the node pre-installs the pins this Mac's projects use (each `hello` lists them), so J6b is
prevented rather than fixed; a big untracked folder offers a `.gitignore` line at a terminal.

### 4.7 Secrets: steps that handle a secret run in Terminal

| Need | How | Today |
|---|---|---|
| Show a code to the person | macOS dialog (`osascript`, script on stdin) | yes (`invite`) |
| Take a secret from the person | hidden-answer dialog, or a hidden terminal prompt | no |
| Store it | login Keychain via `security -i` (value on stdin) | yes |
| Hand it to Dokploy | clipboard, cleared after 90 s if unchanged; open the exact Dokploy page; print only "copied 3 lines: paste into Environment, Save, Deploy" | no |
| Move it between μ commands | read the source directly: `mu relay login` reads `~/.config/mu/relay.env` itself, no pipe | no |
| Machine to machine | the pairing channel, authenticated by the code | yes |

- **`mu relay setup`, `rotate` and `login` are `by: human, secret: true`, `where: terminal`.** Claude prints the line
  for the owner to type in Terminal and does not run it. Letting Claude run `setup` would put the admin token on a
  clipboard that Claude's shell can read with `pbpaste` for 90 s; the saving is one command, once.
- **No gap between refusing a terminal and the clipboard route.** `gen-credentials.sh env` and `client` start
  refusing to write to a terminal in the same release that adds the clipboard route (phase 3), not before.
- **Re-running `mu relay setup` is safe:** a re-run after a failed poll reuses the key and token it generated (kept
  in `relay.env`, 0600) and only re-copies them; it makes new ones only with `--new`. A must-fail test re-runs it and
  checks the values did not change.
- **Phase 3, and only because a machine needs it:** the owner's relay exists, so `relay setup` is worth nothing to him
  until a key rotation or a second relay. Counted honestly: a first relay goes from ~10 commands and ~25 UI actions to
  1 command and ~28 UI actions (service creation stays a printed checklist, plus paste, Save, Deploy); a rotation goes
  to 1 command and 3 UI actions.

Honest limit: a 0600 file, and a Keychain item created by `security`, can be read by any process of the same user,
Claude's shell included. "Never in output or argv" is a policy μ enforces in its own output and tests (§8); it is not
an operating-system boundary. Recorded as a risk (§9).

### 4.8 Help and discovery

- The phase-1 command table (§3) gives `mu help --json`, a self-describing list for Claude, and the tests. Text help
  from it and bash/zsh completions (bash 3.2 only: `compgen -W`, no associative arrays) are later and small; fish is
  dropped.
- `claude/CLAUDE-toolchain.md` gains the step fields' meaning: run `by: agent`, ask on `by: ask`, hand over `by:
  human` with its `where`, never run or read `secret: true` (§4.9).

### 4.9 Claude Code: where the owner asks

The owner asks his questions inside Claude Code, so phase 1 puts μ's state there, using Claude Code's own surfaces
rather than anything new.

| Surface | What it does | Source |
|---|---|---|
| **Status line** | one line, always visible: `μ office-pc ● · 1 running · 1 to fetch · 1 needs you` | `mu overview --brief`, from the cache only, under 100 ms |
| **SessionStart hook** | gives Claude the snapshot at the start of a session: version, pin, nodes, running and unfetched jobs, "needs you" items with their steps (PLAN P2's `mu context`) | `mu overview --brief --json` |
| **UserPromptSubmit hook** | before each prompt, adds only what changed since the last prompt of this session ("sweep-aoa succeeded 4 min ago, not fetched: `mu fetch …`"), or nothing | `mu overview --changes --session ID`, a cursor per session under `$MU_HOME/state/claude/` |

- **Installed** into the project's committed `.claude/settings.json` by `mu link --claude` (`by: ask`, once per
  project), so every clone of the project gets it; moved into the PLAN P2 plugin if that lands. If the owner already
  has a status line, μ's is a command he can append to his. Where it lives is §10 Q3.
- **Never slows or breaks a session:** the hooks read the cache only, never the network; on any error they print
  nothing and exit 0; a stale cache triggers a detached refresh and is shown with its age.
- **Never carries a secret:** `overview` holds no code, token or key (a must-fail test, §8).
- **Claude's own background work**, which μ cannot see, gets a rule in `CLAUDE-toolchain.md`: after starting anything
  in the background, say in one line what is running and how to check it; relay μ's `steps` verbatim, with their
  `where`, instead of paraphrasing; never answer "what do I type?" with a command μ did not print.
- To agree with the dashboard work: the same `overview` schema and cache (§7).

**The `overview` cache.** One writer, `overview.refresh()`, writes `$MU_HOME/state/overview.json` atomically (temporary
file and rename) under a lock, so concurrent refreshes do not race. It runs: after any job-layer command (which has
just talked to the node anyway); detached from plain `mu`, the status line or a hook when the cache is older than
60 s; and from `mu watch` in phase 2. Staleness rule: shown with its age; a node is "online" only if it answered in
the last 2 minutes; older than 10 minutes, the whole node line is marked stale.

## 5. Journeys before and after

### 5.1 The owner's week, and a new machine, by phase

Estimates (`~`), counted as in §1. The week assumes 5 remote jobs submitted by Claude (one fails), a daily status
check, one node-side version mismatch; a package release about once a month is shown on its own row.

| | Today | After phase 1 | After phase 2 | After phase 3 |
|---|---|---|---|---|
| **Owner's week:** asks to Claude | ~27 (5 status, ~20 job, ~2 mismatch) | ~5 (job finishes when not in a session) | ~1 | ~1 |
| commands the owner types | ~3 (the mismatch, at the PC, one wrong) | 0 | 0 | 0 |
| PC visits / switches | ~1 / ~3 | 0 / 0 | 0 / 0 | 0 / 0 |
| a release needing a package | ~3 lines at the PC, a sudo hang, ~2 asks | 1 line (`mu fix`) + UAC, warned ahead, 0 asks | same | same (0 if Q8's root updater is approved) |
| secrets on screen | 0 | 0 | 0 | 0 |
| **New machine (J1–J5):** commands / UI actions | ~42 / ~35 | ~38 / ~35 (pairing shorter) | ~37 / ~35 | ~5 + code / ~28 (3 on rotation) |
| places / switches | 6 / ~25 | 6 / ~22 | 6 / ~22 | 3 / ~3 |
| secrets on screen | 5–6 | 5–6 | 5–6 | 0 |

### 5.2 Each journey

| # | Journey | Today | Target | Phase |
|---|---|---|---|---|
| J5 | One remote job | ~7 commands, ~10 flags, id typed 3–4 times, 3–5 asks | Claude's `mu submit`; the status line and prompt hook report the end; `mu fetch NAME`; 0 asks | 1 (told), 2 (names, notification) |
| J6a | Node offline | `mu compute status` → "on the PC: `wsl … mu-wsl2-distro status`" | home screen and status line say since when; `mu compute selftest` from the Mac; `at the PC: mu` only if the tunnel is down | 1 |
| J6b | Version mismatch | exit 3, PC visit or `mu pin` | step `mu compute install office-pc V` (`by: agent`), Claude runs it; prevented later by pre-installed pins | 1 |
| J6c | Sudo needed | sudo prompt for a non-sudoer; apt as root by hand | warned days ahead; `at the PC: mu fix` + UAC; 0 commands if Q8's root updater is approved | 1 |
| J6d | Failed job | exit 7, `mu log … --tail 50`, work it out | class + last lines + a resubmit step | 2 |
| J6e | Mac asleep | Claude's wait loop dies; nobody told | `mu watch` catches up and notifies; the next prompt's hook tells Claude | 2 |
| J4 | Pairing | Mac 1; PC 2 lines (~190 characters) + code; PATH failure | Mac: Claude runs `mu pair office-pc`; PC: `mu pair` + the code (with the host already installed) | 1 |
| J1 | New Mac | ~10 commands | `gh repo clone martand-systems/mu ~/.mu-src && ~/.mu-src/install.sh` → ends in `mu setup mac`, 1 answer | 3 |
| J2 | New PC | ~8 commands + ~5 GitHub UI actions; a restart; admin window open 1–2 h | one short line at the PC, then the code; WSL install resumes after a restart; μ installs in the background; no deploy key if the release travels from the Mac (§4.6) | 3 |
| J3 | Relay first time | ~10 commands, ~25 Dokploy actions, 4–5 secrets printed | `mu relay setup` in Terminal, then the service checklist and paste, Save, Deploy; 0 secrets printed | 3 |

### 5.3 Recovery paths

Each row has a stable `error` name and its `steps`, and each is a fault injected into the walkthrough test (§8). The
faults use the existing injection switch (`MU_PAIR_FAULT` and its siblings).

| Case | `error` | `steps` (where, by) | Phase |
|---|---|---|---|
| Wrong pairing code typed | `code-mismatch` | "re-type the code shown on the Mac" (pc, human); after 3 tries the invite is void: `mu pair office-pc` (here, agent) | 1 |
| Code expired | `invite-expired` | `mu pair office-pc` for a new code (here, agent), then `mu pair` (pc, human) | 1 |
| Second pair attempt for a paired node | `already-paired` | `mu compute status office-pc` (here, agent); to replace it, `mu compute remove office-pc` then `mu pair office-pc` (here, ask) | 1 |
| Relay down during pairing or setup | `relay-unreachable` | "retry in a minute"; `mu overview` shows since when (here, agent); the relay URL to open (browser, human) | 1 |
| Pairing or host install interrupted | `setup-incomplete` | re-run the same command; it resumes (pc, human) | 1 (pair), 3 (install) |
| UAC declined | `elevation-declined` | "the host settings were not changed; run `mu fix` again and accept the prompt" (pc, human) | 1 |
| Standard (non-admin) Windows account | `needs-admin` | "sign in as an administrator, or ask one to run `mu fix`" (pc, human) | 1 |
| Reinstalled Mac | `relay-not-logged-in`, `node-unknown` | `mu relay restore` (terminal, human, secret), then `mu pair office-pc` (here, agent) | 1 (steps), 3 (restore flow) |
| Node renamed or removed on the Mac | `node-unknown` | `mu compute list` (here, agent); re-pair under the new name (here, ask) | 1 |
| Host files older than the release | `host-stale` | `at the PC: mu update` (pc, human) | 1 |
| Release needs a package | `needs-system-packages` | `at the PC: mu fix` (pc, human) | 1 |

## 6. Build or adopt (PLAN §17)

The rule in PLAN §17: compare new plumbing with open-source tools before building, and record the comparison. Add a
row to PLAN §17's table pointing here when this plan is accepted.

| Need | Considered | Decision and reason |
|---|---|---|
| Telling Claude Code μ's state | **Claude Code's status line, SessionStart and UserPromptSubmit hooks, slash commands, a plugin** (PLAN P2); an MCP server for μ | **Adopt Claude Code's surfaces** (§4.9): the cheapest change, and where the owner asks. Slash commands add nothing over `mu` itself. An MCP server would duplicate `--json` and needs a running process; rejected. The plugin is the later home for the hooks |
| Prompts, colour, spinners, tables | **rich**, **textual**, **questionary**, **prompt_toolkit**/InquirerPy, **gum** (charm), click/typer | **Build** `mu/ui.py` (stdlib: colour, ASCII fallback, step/OK/FAIL lines, spinner, table, confirm, hidden prompt via `getpass`, the `next` footer) and `ui.sh` (bash 3.2). rich and textual cannot load under `python -I -S` without vendoring and slow the ~40 ms start; prompt_toolkit's tree is not worth 3–5 prompts; gum is one more pinned binary |
| Full-screen TUI | textual, htop-style curses | **No.** Live views belong to the web dashboard (§7) |
| Shell completions | argcomplete, shtab, Cobra-style generators | **Later, small, from the command table.** Low value while Claude does most typing. argcomplete needs a Python start on every Tab; shtab needs argparse, and `bin/mu` is bash |
| Admin steps at the PC without visiting it | **Windows OpenSSH server**, WinRM / PowerShell Remoting, an RMM agent | **Rejected.** Each opens an inbound admin-level door on the PC with a second authentication system, against the node's outbound-only design (PLAN); WinRM and SSH also need firewall and account changes on a production machine. Root and Windows-level steps stay at the PC, made short (`mu fix`) and rare (§4.6) |
| Getting the μ release onto the node | GitHub with a deploy key (today); relay-hosted bundle with a signing key (note D); **the Mac pushes the release over the job layer's `receive-pack`** | **Spike the push** (§4.6): it removes the deploy key and the org setting and needs no new key or storage |
| Guided setup and checks | `flutter doctor`, `brew doctor`, `fly launch`/`fly doctor`, `gh auth login`, rustup-init, nix-installer receipts | **Take the patterns** (grouped checks with one fix each; plan, confirm, do, verify; a receipt). None is reusable code |
| Mac install of a private repo | Homebrew private tap, signed `.pkg`, `gh extension`, fine-grained PAT, curl with a token, **a public stub using `gh auth login --web`** | **Build the stub** (phase 3, Q1). A tap is a second updater and needs a token variable; a `.pkg` still needs private hosting and $99 a year; a PAT is a long-lived secret |
| Windows front end | `irm \| iex` installers (Scoop, Chocolatey, Tailscale), winget REST source, Scoop private bucket, `winget configure`, Docker Desktop's Windows CLI over a hidden distro, a `.cmd` shim, a PowerShell module | **A thin `mu.exe` launcher** over the installed `.ps1` (§4.5). A module is invisible to cmd; winget REST and Scoop buckets are too much machinery for 1–3 PCs and need credentials on the PC. `irm \| iex` for the first line of a new PC in phase 3, under §8's trust rules |
| Remote run that feels local | `modal run --detach`, SkyPilot, pueue, Slurm | **Take the shape** if `mu run --job` is ever built (on request). Adopt none, as PLAN §17's pueue row |
| Secrets to a web UI | `pbcopy` with auto-clear, 1Password `op` | **Stock macOS tools**, in Terminal only. `op` only if the company adopts 1Password |
| Relay bootstrap | Dokploy API, a relay-generated token in its log, keypair-signed admin requests, Dokploy templates | **`mu relay setup` with the clipboard, phase 3.** Dokploy API only for rotations, key from a hidden prompt and never stored. A token in the log is rejected (persistent logs, first-run race). Keypair admin auth deferred (Python stdlib cannot sign) |
| Notifications | `terminal-notifier`, `osascript display notification` | **`osascript`**, already used by `au_notify` |
| Mesh, last-seen, join keys | Headscale, NetBird | Unchanged from PLAN §17: the later option |

## 7. How this fits the dashboard

The terminal and Claude Code say what is happening and what to do; the dashboard shows it live and over time.

| Concern | Terminal and Claude Code (this plan) | Dashboard (separate work) |
|---|---|---|
| Source of state | `mu overview --json` and its cache (§4.9): one schema, versioned | reads the same, plus its own live metrics |
| Status | one-screen snapshot, three "needs you" items, one `next`; a one-line status line in Claude Code | the full view: every job, every node, history |
| Live CPU and memory | not shown | owns it |
| Next steps | `next` and `steps` in every result | shows the same `steps` (it must not invent its own advice) |
| Notifications | `mu watch` (phase 2) | may open from the notification |
| Opening it | a home-screen hint once its command name is agreed | |

Agreements needed with the dashboard work before phase 1 ends: the `overview` schema, the cache file and its single
writer, job names as handles, and the `steps` field (§10 Q9).

## 8. Phases

Ranked by friction removed from the owner's week per day; new-machine work waits for a new machine. Days are rough,
for one engineer with Claude, including tests.

### 8.1 The walkthrough test (every phase)

The acceptance test that measures the goal, "nobody had to ask", in two forms:

- **Follower (every commit, CI):** a script that, from a start state, runs `steps[0]` when it is `by: agent`, plays a
  scripted person for `by: human` steps, and stops at the goal. It passes only if it reaches the goal and runs no
  command that μ did not print. It runs each phase's journeys against the CI node, with each §5.3 fault injected.
- **Cold agent (each release):** a fresh Claude Code with no documents and no `CLAUDE-toolchain.md`, only μ's output
  to go on, gets from "paired node, fresh clone" to a fetched result, and recovers from each injected fault. Pass:
  zero commands that μ did not print. It is not deterministic, so a failure is read, not just counted.
- **With the owner at the PC** (phase 1b and phase 3): the same script, the owner typing only what μ printed.

### 8.2 Phase 1a: tell Claude, say the next step (about 6 days)

| Item | Days |
|---|---|
| R2 fix: `PC_UPDATE`, `PC_PAIR`, `PC_MU` use `--exec /home/mu/.local/bin/mu` (if the pairing fixes have not already) | 0.25 |
| `mu pair NODE` on the Mac (alias `mu compute invite`) | 0.25 |
| Contract (`next`, `steps`, `where`, `by`, `secret`) on the J4–J6 paths only: `submit`, `status`, `log`, `fetch`, `cancel`, `compute list/status/invite/remove`, `install`, `update`, plain `mu`; `ui.py`, `ui.sh`; `--json` added to `install` and `update` | 2 |
| The minimal command table and the "every printed command parses" check | 0.5 |
| `mu overview` (`--json`, `--brief`, `--changes`), the cache writer, the home screen | 1.5 |
| Claude Code: status line, SessionStart and UserPromptSubmit hooks, `mu link --claude`, the `CLAUDE-toolchain.md` rule | 1.5 |

Acceptance tests:
- **Must fail: every printed command exists.** Every `next` and `steps[].cmd` emitted across the phase's tests, and
  every hint constant in the source, parses against the command table of the same release. `where: pc` steps are
  checked against the Windows `mu`'s verb list; until it ships in 1b, the few transitional PC lines (the R2-fixed
  `wsl … --exec` forms and the `.ps1` lines) sit in an explicit allowlist; after 1b it holds only §4.6's one transitional root line, printed only to a node
  that reports pre-phase-1 host files.
- **Next-step coverage:** each listed command, on success and on each documented error, run with `--json` in a sandbox,
  has `next` and `steps`, and each step has `where` and `by`.
- **Agent-safe:** those commands with `--json` and stdin from `/dev/null` under a 10 s timeout print exactly one JSON
  object and never wait for input.
- **Must fail:** a fake node returning `next`, `steps`, `by`, `where` or `secret` cannot change those fields in the
  client's output (extends today's `exit`/`next` test).
- **Home screen:** plain `mu` returns in under 1 s with the relay blackholed (cached state and its age), and on a clean
  `MU_HOME` says μ is not set up and prints the install steps. The mock in §4.2 is the expected shape at the end of
  phase 1b.
- **Claude hooks:** the status-line and hook commands return in under 100 ms from the cache, print nothing and exit 0
  with `MU_HOME` missing or the cache corrupt, and the prompt hook prints a finished job once, then nothing.
- **Must fail: no secret in state.** With a pairing code and a canary relay env active, the code and canaries never
  appear in `mu overview --json`, the cache, the hook output or μ's logs.
- **bash 3.2:** dispatcher tests under `/bin/bash` on macOS; with `NO_COLOR` or no terminal, no escape codes.
- **Follower:** J5 and J6b against the CI node with the faults of §5.3 that phase 1a covers.

### 8.3 Phase 1b: fix from the Mac, a thin `mu` at the PC (about 4 days)

| Item | Days |
|---|---|
| Node verbs and Mac commands: `mu compute update/install/enable/selftest` | 1.5 |
| `mu.exe` launcher, the `.ps1` `run` verb, `pair` split, `fix`, `update` with the host-version report, Linux prompt | 1.5 |
| Sudo fail-fast, the root-owned package script, `system_packages_missing`, the CI `apt.txt` check | 0.75 |
| Spike: push a release to the node over `receive-pack` (size, time, trust notes) | 0.5 |

Acceptance tests:
- **Count (with the owner at the PC):** with the phase-1 host files installed, pairing at the PC is `mu pair` plus the
  code from a non-admin PowerShell; checking the node is `mu`; a package release is `mu fix` and one UAC prompt. No
  line typed contains `wsl`, `powershell` or a path.
- **Must fail: lint.** No string meant for a person (hints, `next`, `steps[].cmd`) contains `wsl -d` or
  `ExecutionPolicy`, outside the Windows front end's own code and the one transitional root line of §4.6.
- **Must fail:** `mu.exe` run from a signed-in session before the startup task has run does not start WSL; after
  signing out, the distro is still running (`test-stop` style). One guard: a test greps that only the `.ps1` calls
  `wsl.exe`.
- **Must fail:** the pairing code never appears in an argument list or in `C:\Mu\logs`; tested with stubbed
  `security`, `osascript` and `pbcopy` binaries (and their PowerShell equivalents) that record their argument lists.
- **Must fail:** `/usr/local/lib/mu/mu-system-packages` is root-owned, not writable by `mu`, and refuses a list line
  outside the pattern (a test list with `foo; rm -rf /` and `../x`).
- **No sudo prompt:** `install.sh` on Ubuntu as a non-sudoer, with a terminal and a missing package, exits non-zero
  within 5 s and prints the step, never a password prompt.
- **`mu compute install`** against the CI node installs a version the node lacks, and a submit pinned to it then runs.
- **Where they run:** the logic (verb routing, the guard's decision, exit-code mapping) under `pwsh` on Linux, as
  `tools/test-wsl2-host` does today. The Windows-only checks are a **manual checklist on the office PC**, run in an
  agreed window that includes an uninstall and reinstall of the host side (CI has only `ubuntu-24.04` and
  `macos-15`; §10 Q10).

### 8.4 Phase 2: jobs by name, and told when they end (about 2 days)

Names, `@last` and id prefixes; `mu jobs` as a synonym; the `mu watch` notification (which also refreshes the cache);
failed-job hints.

Acceptance tests:
- An ambiguous name exits 2 and lists the candidates; old full ids still work in every job command.
- `mu watch` posts exactly one notification per finished job (notifier stubbed), and is not running when no job is
  unfinished; after a simulated sleep it catches up.
- A job killed for memory shows its class, peak and a resubmit step.
- **Follower:** J5 by name and J6d, J6e.

### 8.5 Phase 3: a new machine (on demand, about 19 days, when §10 Q1 says one is coming)

| Item | Days |
|---|---|
| `mu doctor` engine, `--fix`, `mu setup mac`, auto-link, `install.sh` installs the latest tag and shows an auto-update failure | 4 |
| `mu relay setup/rotate/login` in Terminal (clipboard, poll, idempotent re-run, self-login); `gen-credentials.sh` refuses a terminal in the same release | 1.5 |
| One-line new PC: the stage-1 and stage-2 bootstrap, reboot resume, μ installed in the background, "installing" node state | 8 |
| The release pushed from the Mac (if the spike is accepted), or the deploy key added by `gh` | 2 |
| Node pre-installs pins; Mac stub installer; text help and bash/zsh completions | 3.5 |

**The one line at a new PC** (decided enough to build; Q2 picks the host):
- The line is typed by hand, so it is short: `irm https://<short public URL> | iex`. A short URL cannot be pinned to a
  commit, and git tags can be moved, so the URL is not trusted for anything that runs elevated.
- **Stage 1** is tiny (about 30 lines, readable on screen), runs unelevated, and does only this: asks for the pairing
  code (echo off), runs the pairing handshake, receives over the authenticated channel the SHA-256 of stage 2 (which
  the Mac computed from its own copy of the release), downloads stage 2, checks the full hash, and only then asks for
  UAC. The relay never learns the code, so it cannot change the hash.
- **Deploy key, only if the release push is not taken:** read-only, this repository only, added by the Mac with `gh`
  and removed by `mu compute remove`. The Mac's `gh` login needs admin rights on the repository (classic `repo` scope,
  or a fine-grained token with Administration read and write on that repository only).

Acceptance tests:
- **Count:** on a clean Windows 11 machine, from the Mac's printed line to "paired, installing in the background": 1
  line and the code at the PC, through a forced restart, with nobody re-running anything.
- **Must fail:** a stage 2 changed by one byte is refused by stage 1 before any UAC prompt; a stage 1 served with a
  different hash pinned in the pairing answer refuses too; nothing in the bootstrap or the pairing answer lets the
  relay choose code that runs elevated.
- **Count:** on a clean macOS machine with `gh` signed in, J1 is one line and one answer; `mu setup mac` run twice
  changes nothing the second time.
- `mu doctor --fix` fixes a broken link, a missing pin and auto-update off; it never runs a step marked `human`,
  `secret` or needing root (a check whose fix is a canary command that must not run).
- **Relay:** J3 against a test relay is `mu relay setup` in Terminal plus the checklist; re-running after a failed
  poll keeps the same key and token (must fail if they change); with stubbed `security`, `osascript` and `pbcopy`
  recording their arguments, no canary appears in any argument list, output or log; the clipboard is cleared 90 s
  later.
- **Where:** the Windows tests need a clean machine: a second x64 PC, or the office PC in a window with an uninstall and
  reinstall (§10 Q10).

### 8.6 Later, each needing an owner decision

Node system updater (a root timer for packages, Q8); relay last-seen for offline nodes; dropping static relay
clients; Dokploy API rotations; keypair admin auth; `mu run --job`/`attach`/`[jobs.*]` (Q6).

## 9. Risks

| Risk | Mitigation |
|---|---|
| The owner still asks, because the hooks or status line are not where he looks | the cold-agent walkthrough; ask the owner after two weeks how many times he asked; the asks count in §5.1 is the measure |
| Hooks slow or break Claude Code sessions | cache-only reads under 100 ms; print nothing and exit 0 on error; a test with a corrupt cache |
| The prompt hook floods Claude's context | only changes since the last prompt, at most 5 lines, nothing when nothing changed |
| Host files run as Windows admin come from a mu-owned location (today's `PC_PAIR` already does this; the update path in §4.5 continues it) | the SHA-256 shown at the PC and on the Mac; to verify whether sealed release layers can be altered by `mu`; a stronger check (stage-1-style hash from the pairing channel) when phase 3 builds that channel |
| The release push changes who the node trusts for μ code | user `mu` already runs whatever a paired Mac submits; the root package script reads only a validated name list; tags immutable on the node |
| Renames break Claude's instructions and the owner's habits | only `mu pair` (alias kept); `mu status` keeps its meaning; `CLAUDE-toolchain.md` changes in the same release; a test runs every old spelling |
| The secret rule is policy, not a boundary (§4.7) | secret steps in Terminal only; the stub-recording tests; Claude's instructions say never to read `relay.env` or the Keychain |
| The clipboard leaks a secret (clipboard managers, Universal Clipboard) | phase 3 only; 90 s auto-clear; the `org.nspasteboard.ConcealedType` marker (whether Universal Clipboard honours it is unverified); Q4 |
| `mu.exe` starts WSL from a signed-in session and the node stops at sign-out | the guard lives only in the `.ps1`; its must-fail test |
| Windows tests have no machine | `pwsh` on Linux for logic; a manual checklist on the office PC in an agreed window, or a second x64 PC (Q10) |
| The cold-agent test is not deterministic | the deterministic follower gates commits; the agent run is read by a person each release |
| Duplicated work with the dashboard and the pairing fixes | §7's agreements; check the pairing-fix branch for the R2 fix and `mu pair` before starting |
| A public bootstrap reveals the infrastructure layout | stage 1 holds no secret and no addresses beyond the release host; owner decision (Q2) |
| bash 3.2 limits | `ui.sh` tested under `/bin/bash` on macOS in CI |

## 10. Questions for the owner

> **Answered 2026-11-14.** The owner's answers, and the lead's calls where the owner left it to judgment, are in
> [docs/ROADMAP.md](../ROADMAP.md) §2.1–2.2. In short: no new machine soon (phase 3 deferred); Claude types the jobs;
> the office PC is not to be disturbed (no uninstall/reinstall tests there; Windows-only checks become a short
> non-destructive checklist); Claude Code settings go in each project via `mu link`; no clipboard secrets; no root
> package updater; `mu fix` asks before restarting the distro; automatic fetch opt-in. Order: after the dashboard.

1. **Q1, when does the next machine arrive?** A second PC, a second Mac, or a second engineer: if none in the next
   two months, phase 3 waits; if one is coming, phase 3 is scheduled to finish before it.
2. **Q2, the first script on a fresh PC (phase 3):** (a) a small public bootstrap repo with stage 1 and the host
   scripts, tagged per release (recommended); (b) make `mu` public; (c) keep the zip or USB stick (2 commands, not 1).
   In every case stage 2 is checked against the hash from the pairing channel (§8.5).
3. **Q3, Claude Code settings:** μ's hooks and status line in the project's committed `.claude/settings.json`
   (recommended: every clone gets them), or in your user settings only? Do you already use a status line?
4. **Q4 (phase 3):** may μ put a secret on the clipboard for 90 s for the Dokploy paste, run by you in Terminal?
5. **Q5, the release from the Mac:** after the spike, deliver μ to the node over the tunnel and drop the deploy key
   and the node's GitHub access? The node would then update only when a Mac is online.
6. **Q6:** do you type remote jobs by hand, or does Claude? If you do, `mu run --job NAME` and `[jobs.*]` move into a
   phase; if not, they stay deferred.
7. **Q7:** may `mu fix` at the PC restart the distro without asking when no job is running?
8. **Q8:** a root-owned package updater on the node (after a security review), so a package release needs no PC
   visit at all, or keep `mu fix` at the PC?
9. **Q9:** the dashboard's command name and the `overview` schema and cache: agree with the dashboard work before
   phase 1a ends.
10. **Q10:** for Windows tests, may the office PC have an agreed window with an uninstall and reinstall of the host
    side (and no jobs), or should a second x64 machine be budgeted?
11. **Q11:** should finished jobs be fetched automatically, into `runs/<id>/` only and never over the working tree?
    If yes, phase 2 adds it, opt-in unless you say otherwise.
12. **To check at the PC before phase 1b:** `wsl.exe` exit-code passthrough with `--exec`; `systemctl --user` under
    `--exec` with `XDG_RUNTIME_DIR` set; how the startup task starts the distro (for the host-version report).

## 11. Not done here

- No code was changed. Implementation starts after the owner answers §10.
- Second workstations (pairing stage P2), Linux workstations and several users are out of scope; the contract and
  command set are meant to carry over to them unchanged.

## 12. Where the research notes disagreed

| Topic | Notes | Decision here |
|---|---|---|
| The Windows front end | A: `mu.cmd` shim; D: `mu.exe` with general forwarding | a thin `mu.exe` with a fixed verb list over the installed `.ps1` (review). With no free arguments, `.cmd`'s quoting problems no longer decide it; Ctrl-C behaviour does |
| How the Windows `mu` reaches the distro's `mu` | A: `--exec /home/mu/.local/bin/mu`; D: a root-owned trampoline | A's `--exec` form; the node's `mu` fills in `PATH`, locale and `XDG_RUNTIME_DIR` itself. The trampoline is dropped with general forwarding |
| The `next` field | C asked: change it to an array, or add a field | keep `next` (string), add `steps` |
| Bootstrap for a fresh PC | A: public bootstrap repo, relay rejected; D: relay serves the host bundle per invite, verified by a 12-character hash in the URL | A's public stage 1, with stage 2 verified by the full SHA-256 received over the pairing channel, which the relay cannot change (§8.5) |
| Release to the node | D: relay-hosted, signed, large | the Mac pushes it over the tunnel; spike first (§4.6) |
| Autoupdate after install | the brief: a separate step; D: already on by default | already on; the remaining fix is showing the error when turning it on fails (A), phase 3 |

## 13. Review

The review of revision 1 accepted the direction (output contract, home screen, the R2 fix) and asked for a re-cut. Its
sixteen findings and what this revision did:

| # | Finding | Outcome |
|---|---|---|
| 1 | The headline measured a new user, not the owner's week | **Accepted.** §0 and §5.1 lead with the owner's week, per phase; new-machine work is phase 3, scheduled on Q1 |
| 2 | Phase 1's tests depended on later phases | **Accepted.** The "every printed command parses" must-fail test; a minimal command table in phase 1; the Windows `mu` pulled into phase 1b so its count and lint tests run there; the home screen uses only phase-1 commands |
| 3 | Claude Code was barely touched | **Accepted.** Status line, SessionStart and prompt hooks, the `CLAUDE-toolchain.md` rule, in phase 1a (§4.9) |
| 4 | No test measured "nobody had to ask" | **Accepted, with an addition:** the cold-agent walkthrough per release, plus a deterministic follower on every commit, because an agent run cannot gate commits reliably (§8.1) |
| 5 | Remove PC visits before making them nicer | **Accepted.** `mu compute update/install/enable/selftest` in phase 1b; the release push as a spike (§4.6) |
| 6 | `mu.exe` over-built and duplicating the guard | **Accepted.** Thin launcher, fixed verbs, one guard in the `.ps1`; `mu root` and the `command_not_found_handle` dropped for a prompt; the update path written down (§4.5). `mu shell` dropped too, being the same general forwarding |
| 7 | Phase 2 mixed cheap value with churn | **Accepted.** Phase 2 is names, `@last`, notification, failed-job hints; `mu run --job`, `attach`, `[jobs.*]`, `--again` wait on Q6; default node per clone |
| 8 | Relay item mis-counted and mis-ranked | **Accepted.** Count corrected; refusal and clipboard ship together; idempotent re-run with a must-fail test; moved to phase 3 |
| 9 | Two soft spots on secrets | **Accepted.** `relay setup/rotate/login` are `by: human, secret: true` in Terminal; stub-recording tests replace the `ps` snapshot; the no-secret-in-state test covers `overview`, logs and `C:\Mu\logs` |
| 10 | `irm \| iex` under-specified | **Accepted.** Stage 1 short and unelevated; stage 2 checked by the full SHA-256 from the pairing channel before UAC; deploy-key limits and `gh` scope stated (§8.5) |
| 11 | The sudo fix only failed faster | **Accepted.** A root-owned script reading a validated name list; the CI `apt.txt` check; the node warns ahead and auto-update holds; the exact command printed (§4.6) |
| 12 | Renames | **Accepted.** `mu pair` now; `mu status` keeps its meaning, `mu jobs` is a synonym; old Q5 removed |
| 13 | Phase 1's estimate light | **Accepted, and higher than suggested:** about 10 days (6 + 4), not 9, because the four node verbs and the release-push spike are now in it. `next` coverage scoped to the J4–J6 paths; the cache writer and staleness rule named (§4.9); `system_packages_missing` is an additive protocol field |
| 14 | Windows and clean-Mac tests had nowhere to run | **Accepted.** `pwsh` on Linux for logic; a manual office-PC checklist in an agreed window, or a second x64 machine (Q10) |
| 15 | Recovery paths absent | **Accepted.** §5.3, each row a fault in the walkthrough test |
| 16 | Build-versus-adopt gaps | **Accepted.** Rows for Claude Code's surfaces, Windows OpenSSH/WinRM (rejected on security grounds) and the release push; fish completions dropped, the command table kept for the tests |

**Rejected or changed from the review's proposal:**
- The review's re-cut phase 1 (about 9 days) is kept in content but split into 1a and 1b and estimated at about 10
  days; the review did not cost the four node verbs or the spike separately.
- The review proposed the cold-agent walkthrough as the test; it is kept as the release gate, but the per-commit gate
  is the deterministic follower, because an LLM run varies between runs.
- None of the review's findings was rejected outright.
