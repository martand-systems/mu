# μ shell experience: one guided command per journey

The plan for making μ simple to use from a terminal, for the engineer and for Claude Code driving it. Research and
planning only: no code was changed. Read at `v2026.11.8` (branch `shell-ux-research`). It draws on four research
notes written on 2026-11-14 (scratchpad `shell-ux/`): A, the friction audit and target journeys; B, the inventory of
every command, flag, help source and exit code; C, CLI and setup patterns in other tools; D, one-line install and a
Windows side without `wsl`. Where the notes disagreed, §12 records which way this plan went and why.

Related work running separately, which this plan fits and does not repeat: the **local web dashboard** (jobs, nodes,
live CPU and memory; §7) and the **one-code pairing fixes and install guides**. Where an item below may already be
covered by the pairing fixes, it says so.

Sections: [0 Summary](#0-summary) · [1 The problem in numbers](#1-the-problem-in-numbers) ·
[2 Principles](#2-principles) · [3 The output contract](#3-the-output-contract) ·
[4 Target command surface](#4-the-target-command-surface) · [5 Journeys before and after](#5-journeys-before-and-after) ·
[6 Build or adopt](#6-build-or-adopt-plan-17) · [7 The dashboard](#7-how-this-fits-the-dashboard) ·
[8 Phases](#8-phases-ranked-by-friction-removed-per-day) · [9 Risks](#9-risks) ·
[10 Questions for the owner](#10-questions-for-the-owner) · [11 Not done here](#11-not-done-here) ·
[12 Where the research notes disagreed](#12-where-the-research-notes-disagreed)

## 0. Summary

- **Today,** a new user going from nothing to a first remote result types about **42 commands** and does about **35
  web-UI actions**, in **6 places** (Mac Terminal, Claude Code, Windows PowerShell (Admin), the Linux shell in WSL,
  Dokploy, GitHub) with about **25 switches** between them. **5–6 secrets** appear on screen, and there are about **22
  places where a step can fail** with no guidance. Every remote job then costs about 7 commands and 3–5 "is it done?"
  questions to Claude.
- **Why the owner keeps asking Claude what to type:** μ knows the state but never says the next step on success;
  the same action is spelled differently in four shells and μ's own printed PC commands fail (`mu: command not
  found`); nothing shows the whole system or background work; secrets are printed, so some steps must be typed by
  hand outside Claude.
- **Target:** about **5 commands plus the pairing code**, **3 UI actions** (paste, Save, Deploy, once per relay),
  **3 places**, **0 secrets on screen**; one command per remote job and a notification when it ends.
- **How:** one output contract (every result names its next step and where it runs, §3); a home screen at plain `mu`
  (§4.2); one check engine behind `mu doctor`, `mu doctor --fix` and `mu setup` (§4.3, §4.4); a `mu` that works in
  Windows PowerShell (§4.5); `mu run --on NODE` with names instead of ids (§4.6); secrets moved by Keychain, hidden
  dialogs and the clipboard only (§4.7).
- **Build, not adopt:** no new dependency. A ~150-line stdlib `mu/ui.py` and a ~40-line `ui.sh` (§6).
- **First phase** (about 6 days): the bug fixes, the output contract, and the home screen. It removes the most
  frequent friction (the "what now?" and "what's the status?" questions, and the PC commands that fail) for the least
  work (§8).

## 1. The problem in numbers

### 1.1 Journeys today

Counting rules (note A): a **command** is a line a person types (shell, PowerShell, or a code); a **UI action** is one
field or button in a web UI; a **switch** is a move between the six places; a **secret** is a value that grants
access and that the person sees, copies or types; an **ask** is the owner asking Claude "what now?" or "what's the
status?". Counts marked `~` are estimates from the session, not measured.

| # | Journey | Commands / UI actions | Places (switches) | Secrets on screen | Failure points | Asks |
|---|---|---|---|---|---|---|
| J1 | New Mac to first local run | ~10 / 0 + GitHub sign-in | T, C, G (~4) | 1 | 6: auth, `PATH`, `dev-<commit>` label, auto-update failing silently, forgotten `mu link`, missing pin | ~3 |
| J2 | New Windows PC to node installed (scripted) | ~8 / ~5 | W, G, L, T (~8) | 0 | 5: private repo, reboot and re-run, deploy key + org setting, admin window open 1–2 h, sudo prompt | ~5 |
| J2′ | the same by hand (what S3 actually was) | ~40 / ~5 | W, L, G, T, C (~15) | 1 | many | many |
| J3 | Relay, first time | ~10 / ~25 | T (not C), D (~6) | **4–5 printed** | 5: Isolated Deployment, Randomize Compose, Let's Encrypt limit, Deploy not pressed, a misspelt middleware (404) | ~3 |
| J4 | Pairing a node | Mac 1; PC 2 lines (~190 characters) + 24-character code; often +1 PATH fix | C/T, W, L (~3) | 1 (the code) | 2: `mu: command not found` in the line μ prints; `-File ''` when the release lacks `pair` | ~2 |
| J5 | One remote job | ~7 commands, ~10 flags, a 22-character id typed 3–4 times | T/C | 0 | 4: pin missing on node, 2.1 GB untracked folder, LFS and submodules, Claude's wait loop under its time limit | 3–5 |
| J6 | Recovery (offline, version, sudo, failed job, Mac asleep) | 2–6 each, mostly at the PC | C, W, L (2–4) | 0 | — | 1–2 each |
| | **New user to first remote result (J1–J5)** | **~42 / ~35** | **6 places, ~25 switches** | **5–6** | **~22** | **~15** |

### 1.2 The surface today (note B)

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
- **One concept, four names:** node name, relay client, relay user (`--relay-user`), workstation `--name`.
- **Exit code 3** means "the node lacks the pinned μ" in the job layer and "no toolchain installed" in `bin/mu`.
- **Help** comes from six mechanisms; `mu help` filters the toolchain's help with `sed`. **No shell completions.**

### 1.3 Root causes

| # | Cause | Seen in |
|---|---|---|
| R1 | Four shells, each with its own spelling; nothing says which shell you are in | owner typed PowerShell into Linux; J2, J4, J6 |
| R2 | `wsl -d mu-compute -u mu -- mu …` is not a login shell, so `~/.local/bin` is not on `PATH`. μ prints this line itself: `invite.py` `PC_UPDATE`, `PC_PAIR`; `jobs.py` `PC_MU` (every "on the PC: …" hint) | J4, J6 |
| R3 | No home screen for the system; background work (auto-update, open invites, running jobs) is invisible | the repeated "what's the status?" |
| R4 | The order of steps lives in documents (the first-job checklist is 554 lines, the pairing design 1,485), not in μ | "what do I type next?" |
| R5 | Secrets go through the terminal (`gen-credentials.sh env`, `client`), so steps must be typed outside Claude | J3 |
| R6 | Admin steps fall to user `mu`, who has no sudo; `install.sh` line 197 prompts for sudo whenever stdin is a terminal, even for a non-sudoer | J6c |
| R7 | The PC needs GitHub access to a private repo, and a deploy key added by hand in the GitHub UI | J2 |
| R8 | Remote jobs are four verbs keyed by a 22-character id; flags repeat what `mu.toml` could hold; nothing notifies | J5 |
| R9 | Problems are found at submit time, not before (pin missing on node, big untracked folder, LFS) | J5, J6b |

## 2. Principles

Each principle has a check in §8 that fails the build when it is broken.

1. **μ always says the next step.** Every command, on success and on failure, ends with one `next:` line (human) and
   a `steps` list (JSON). Plain `mu` says what to do now. Nobody needs Claude, or a document, to know what to type.
2. **One guided command per journey.** Setting up a Mac, a relay, a PC, pairing, running a remote job: each is one
   command that shows its plan, asks once at a terminal, does the work, verifies, and says the next step. Re-running
   it is always safe.
3. **Nothing secret through Claude, and Claude can still run everything.** No secret in any output, argument list,
   log or JSON. Secrets move only through the Keychain, hidden-answer dialogs, the clipboard (cleared after 90 s) and
   the pairing channel. A step that needs a person is marked as such, so Claude hands it over instead of running it.
4. **Agent-friendly everywhere.** Every command takes `--json` (one object on stdout, stable error names, the exit
   codes of `proto.py`), never prompts without a terminal or with `--json`, and accepts `--yes`, `--dry-run`.
5. **Windows users never type `wsl`, `powershell -File`, or a Linux path.** `mu` works in any PowerShell or cmd
   window at the PC. μ never prints a command containing `wsl -d` or `ExecutionPolicy` for a person to type.
6. **Every printed command says where it runs** when that is not here: `at the PC:`, `in Dokploy:`, `in a browser:`.
7. **Prevent rather than refuse.** Check ahead (pins on the node, big folders, packages a release needs) and fix
   ahead where safe. Refusals stay, but each carries a fix.
8. **Names, not ids.** Jobs are addressed by name (`sweep`), `@last`, or a unique id prefix.
9. **Old spellings keep working.** Every rename keeps an alias for at least three releases; Claude's instructions
   change in the same release.
10. **One source of state.** The terminal home screen and the web dashboard read the same `mu overview --json`.

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
| `steps[].where` | `here`, `pc` (any PowerShell at the PC, via the Windows `mu`), `dokploy`, `browser` |
| `steps[].by` | `agent`: safe for Claude to run. `ask`: changes the project or costs time; Claude asks first. `human`: a person must act (at the PC, in a browser, a code to type) |
| `steps[].secret` | `true` when the step involves a secret. Claude must not run it, read its output, or ask the person to paste the result |
| `data` | The command's result (on success) |

- **Node-supplied text never sets `next`, `steps`, `where`, `by` or `secret`.** `cli.py` already protects `exit` and
  `next` from node-supplied extras; the same rule covers the new fields, and node text stays in `node_message`.
- **Human form:** results on stdout; progress, spinners and the footer on stderr. The footer is one dim line,
  `next: at the PC: mu fix`. Colour and spinners only on a terminal, honouring `NO_COLOR` (as `bin/mu` does now).
- **Prompts** (`Do it now? [Y/n]`) only when stdin and stderr are terminals, no `--json`, no `--yes`. Without a
  terminal μ never prompts: it exits with the step in `steps`. That is the path Claude takes.
- **Exit codes** stay those of `proto.py`. `bin/mu`'s "no toolchain installed" (3) is defined as the same meaning,
  "the needed μ version is not installed", and documented once.
- **Help, `--json` coverage and completions** come from one command table (§4.8), so they cannot drift.

## 4. The target command surface

### 4.1 The command set

Grammar: top-level verbs for what is typed daily (toolchain and jobs); a noun then a verb for the rest (`compute`,
`relay`, `host`). Old forms stay as aliases (principle 9). Commands marked *PC* are the Windows `mu` (§4.5).

| Target | Today | Alias kept | Notes |
|---|---|---|---|
| `mu` | toolchain status | — | the home screen (§4.2). Toolchain lines move into it |
| `mu overview [--json] [--brief]` | — | | the data behind `mu` and the dashboard; `--brief` for Claude's SessionStart hook (PLAN P2 `mu context`) |
| `mu setup [mac\|relay\|pc]` | install.sh, autoupdate, link, relay login, invite… | | the guided path (§4.3); the role is detected when omitted |
| `mu doctor [--fix] [--on NODE]` | `mu doctor` (tools only) | | the check engine (§4.4) |
| `mu install\|update\|use\|pin\|versions\|remove\|gc\|autoupdate` | same | `upgrade`, `default`, `ls`, `list`, `rm`, `uninstall`, `auto-update` | unchanged; gain `--json` and `next` |
| `mu run [--on NODE] [NAME \| -- CMD…]` | `mu run` (local) + `mu submit` + `status --wait` + `log` + `fetch` | | local stays as today; `--on` submits, follows, fetches, exits with the job's result; Ctrl-C detaches (§4.6) |
| `mu submit` | same | | the non-blocking form, for Claude and scripts |
| `mu jobs [NAME]` | `mu status [JOB]` | `mu status JOB` | `mu status` with no argument: Q5 |
| `mu log\|fetch\|cancel NAME` | same, by id | ids still work | names, `@last`, id prefixes |
| `mu attach NAME` | `mu log -f` + `status --wait` | | re-follow a detached run |
| `mu pair NODE` (Mac) | `mu compute invite NODE` | `mu compute invite` | the same word at both ends |
| `mu pair` (*PC*) | two long PowerShell lines | | asks for the code (§4.5) |
| `mu compute list\|status\|remove\|install NODE [V]` | same (no `install`) | `mu compute` alone = list | `install` is job-layer stage 2's remote install as `mu` |
| `mu relay setup\|login\|status\|clients\|remove\|backup\|restore\|rotate` | `gen-credentials.sh` ×4 + `relay login --stdin` | `--stdin` | `setup` and `rotate` fold in `gen-credentials.sh` (§4.7); the script stays as the fallback |
| `mu fix` (*PC*) | the PowerShell `status`, then whatever it said | | applies what `mu` at the PC found failing |
| `mu host install\|status\|test-stop\|uninstall\|system\|sync` (*PC*) | `powershell -NoProfile -ExecutionPolicy Bypass -File C:\Mu\host\mu-wsl2-host.ps1 …` | | `system` = the root package step (R6) |
| `mu shell`, `mu root` (*PC*) | `wsl -d mu-compute -u mu`, `-u root` | | the rare power use |
| `mu completion bash\|zsh\|fish` | — | | §4.8 |
| `mu help [CMD] [--json]` | six help mechanisms | | from the command table |
| `mu node …` | same | | the node's own side; hidden from the Mac's help |

Flag rules: `--json`, `--yes`, `--dry-run`, `--no-input` on every command. Durations accept units everywhere
(`--timeout 10m`; a bare number stays seconds). Secrets and codes are read from a hidden prompt, a dialog, or
`--…-file`/stdin; never from an argument.

### 4.2 Plain `mu`: the home screen

Built from `mu overview --json`: local files plus a cached check of the relay and nodes (at most about 1 s, the
cache refreshed in the background, shown with its age). At most three "needs you" items, then one `next:`.

```
μ v2026.11.8 · auto-update on (next 04:12) · jatayu: pinned v2026.11.8
nodes   office-pc   online · μ v2026.11.8 · idle · 412 GB free      (live: mu dash)
jobs    sweep-aoa   running 41 min on office-pc                       mu attach sweep-aoa
        mesh-conv   succeeded 09:12, not fetched                      mu fetch mesh-conv
needs you
  ! office-pc needs an admin step for v2026.11.9 (one Ubuntu package)  at the PC: mu fix
next: mu fetch mesh-conv
```

- Nothing set up: `μ is not set up on this Mac. next: mu setup`.
- Background work always appears with its age: an auto-update install, an open invite, a node still installing, a
  running job, a relay reconnecting.
- Node offline: the relay's last-seen time and the likely reason ("last connected 3 h ago: off, asleep or
  restarting"), which needs the relay's admin API to report last-seen (phase 5).
- At the PC, `mu` shows the same screen from the node's side: host task, distro, tunnel, runner, paired Mac.

### 4.3 `mu setup`: one guided path per role

The same engine as `mu doctor --fix` (§4.4), run in order: show the plan, ask once, do each step with an OK/FAIL line,
verify, print the next step. `--yes` for Claude; `--dry-run` prints the plan only.

| Role | What it does |
|---|---|
| `mu setup mac` (run at the end of `install.sh`) | installs the latest release tag (a checkout counts only with `--dev`); turns auto-update on and shows the error if that fails; puts `mu` on `PATH`; offers to clone a project, links it, installs its pin; checks the relay login; ends with `next: open Claude Code in ~/…/jatayu` |
| `mu setup relay` (= `mu relay setup`) | §4.7 |
| `mu setup pc` (*PC*) | what `mu host install` does today, then pairing, then the μ install in the background (phase 5) |

`mu link` becomes automatic: any `mu` command in a project whose `.mu/toolchain` is missing or stale re-links it and
says so in one line.

### 4.4 `mu doctor` and `mu doctor --fix`: one check engine

Each check: `{id, group, status: ok|warn|fail, detail, fix: step|null}` using the step fields of §3.

| Group | Checks |
|---|---|
| toolchain | the current doctor's checks; layers sealed; default installed; `mu` on `PATH` for login and non-login shells |
| updates | auto-update on and scheduled; last result; a release that needs a system package (said before it fails) |
| this project | `mu.toml`; pin installed; linked; untracked folders over the snapshot limit; LFS or submodule content (a warning for remote jobs) |
| relay | logged in; reachable; fingerprint and store id unchanged |
| nodes | answering; version; the pins this Mac's projects need; runner enabled; disk; last stop clean |
| PC (`mu doctor` at the PC, or `--on NODE` via the node) | startup task, Fast Startup and ARSO off, tunnel, deploy key, install receipt matches |

`--fix` applies only fixes with `by: agent` that need no secret and no root; it prints the rest as steps with their
place. An install receipt (the nix-installer pattern: every file, task, registry key and `PATH` line the PC installer
changed) makes "what did μ change on this machine?" answerable and lets `uninstall` reverse exactly that.

### 4.5 `mu` at the PC

`C:\Mu\bin\mu.exe`, compiled on the PC by `mu-wsl2-host.ps1 install` with the in-box C# compiler (as the shutdown
service already is, so no signing certificate and no Mark-of-the-Web), `C:\Mu\bin` on the machine `PATH`. About 150
lines of C#.

- **Forwarding.** Every verb not handled locally runs
  `wsl.exe -d mu-compute -u mu --exec /usr/local/lib/mu/mu-winshim <args>`, quoted by the `CommandLineToArgvW` rules.
  `mu-winshim` is a root-owned, ~15-line trampoline installed by the distro script: it refuses to run as root, sets
  `PATH` (with `~/.local/bin`), `LANG=C.UTF-8`, `XDG_RUNTIME_DIR`, `cd ~`, and `exec`s `~/.local/bin/mu`. This ends
  R2 without a login shell. A root-owned `/usr/local/bin/mu` linking to the mu-writable `~/.local/bin/mu` is not
  allowed: root would then run code `mu` controls.
- **The WSL session guard.** A `wsl.exe` call from a signed-in session, before the startup task has started the
  distro, ties the distro's life to that sign-in (the host README's rule). `mu.exe` uses the same guard as `status`:
  call `wsl.exe` only when the task has run since WSL started; otherwise start the task and wait.
- **Local verbs** (`host …`, `pair`, `fix`, `shell`, `root`) never enter the distro first. Verbs that need Admin
  relaunch elevated with one UAC prompt, output redirected to a file under `C:\Mu\logs` that the unelevated parent
  shows (an elevated child's console otherwise closes and the output is lost).
- **`mu pair` is split** (note D, W2): the elevated part does only host settings; the 24-character code is read by the
  unelevated part with echo off. The code never passes through an argument, a UAC window or a transcript. If the
  release lacks `pair`, `mu pair` updates first.
- **`mu host system`** runs the root package step through `wsl -u root` (the owner is the Windows admin). User `mu`
  gets no sudo. The node reports `needs-system-packages`, so the Mac's home screen says `at the PC: mu fix`.
- `WSL_UTF8=1` and code page 65001 so `μ` and box characters print; exit codes passed through (to test on the PC).
- A Start-menu entry "μ compute node" opens `mu` (the home screen) and waits for a key. No tray icon: the PC runs with
  nobody signed in.
- **Where-am-I.** Output from `mu.exe` is headed `[PC]`. Inside the distro, a `command_not_found_handle` answers
  PowerShell-looking input (`powershell …`, `C:\…`, `Get-…`) with "this is the Linux shell; type `exit` to return to
  PowerShell".

### 4.6 Remote jobs: the same verb as local

- `mu run --on office-pc -- python sweep.py` submits, follows the log, fetches the declared outputs when it succeeds,
  and exits with the job's result. **Ctrl-C detaches**; the job runs on; μ prints `reattach: mu attach sweep`.
- **Named jobs in `mu.toml`**, so `mu run --on office-pc sweep` (or `mu run sweep` with a default node) needs no flags:

  ```toml
  [compute]
  on = "office-pc"

  [jobs.sweep]
  run = "python sweep.py"
  time = "2h"
  mem = "16G"
  outputs = ["results/*.json"]
  ```
- **Names as handles:** the newest job of that name in this clone; `@last`; a unique id prefix. An ambiguous name
  exits 2 with the list. `mu run --again sweep --mem 24G` reuses the saved spec.
- **A notification when a job ends:** a LaunchAgent `mu watch` that runs only while `.mu/jobs` has unfinished jobs,
  using the `au_notify` code auto-update already has: "sweep-aoa succeeded on office-pc (41 min). mu fetch
  sweep-aoa". It catches up on wake. Optional auto-fetch into `runs/<id>/` only, never over the working tree (Q6).
- **For Claude:** `mu submit` once, then move on; the SessionStart hook (`mu overview --brief`) reports finished,
  unfetched jobs. No more `mu status --wait --timeout 500` loops.
- **Failed jobs** show the class and the last 10 log lines, with a step: for `oom`, "peak 15.8 of 16 G: `mu run --again
  sweep --mem 24G`".
- **Prevented refusals:** each `hello` tells the node which pins this Mac's projects use; the node's auto-update
  installs missing ones in its window. A big untracked folder still refuses; at a terminal μ offers to add it to
  `.gitignore`; never a silent exclusion.

### 4.7 Secrets: Claude does the work and never sees them

| Need | How | Today |
|---|---|---|
| Show a code to the person | macOS dialog (`osascript`, script on stdin) | yes (`invite`) |
| Take a secret from the person | hidden-answer dialog, or a hidden terminal prompt | no |
| Store it | login Keychain via `security -i` (value on stdin) | yes |
| Hand it to Dokploy | clipboard, cleared after 90 s if unchanged; open the exact Dokploy page; print only "copied 3 lines: paste into Environment, Save, Deploy" | no |
| Move it between μ commands | read the source directly: `mu relay login` reads `~/.config/mu/relay.env` (later the Keychain) itself, no pipe | no |
| Machine to machine | the pairing channel, authenticated by the code | yes |

`mu relay setup` folds the four `gen-credentials.sh` steps into one: makes the key and admin token, copies the env
block, opens Dokploy, polls `/mu/v1/` until `pairing: true` with the expected fingerprint, then logs itself in.
`gen-credentials.sh env` and `client` refuse to write to a terminal (as `login-bundle` already does) and offer the
clipboard instead.

Honest limit: a 0600 file, and a Keychain item created by `security`, can be read by any process of the same user,
Claude's shell included. "Never in output or argv" is a policy that μ enforces in its own output and tests (§8); it is
not an operating-system boundary. A real boundary would need a separate user or an access-controlled item made by a
signed helper. Recorded as a risk (§9).

### 4.8 Help, completions, discovery

- One command table (a data file read by both `bin/mu` and Python) holds every command, its flags, its place, whether
  it needs a human, and its exit codes. `mu help`, `mu help --json` (a self-describing schema for Claude), the
  completions and the `--json`-coverage test (§8) all come from it.
- `mu completion bash|zsh|fish` prints a static script; dynamic values (node names, job names, versions) come from a
  fast `mu --complete nodes|jobs|versions` reading local files only. The bash script uses only bash 3.2 features
  (`compgen -W`, no associative arrays, no `mapfile`). `install.sh` installs it. PowerShell completion for `mu.exe`
  is a later, small addition.
- `claude/CLAUDE-toolchain.md` gains the step fields' meaning: run `by: agent`, ask on `by: ask`, hand over `by:
  human`, never touch `secret: true`.

## 5. Journeys before and after

| # | Journey | Today | Target | Places |
|---|---|---|---|---|
| J1 | New Mac | ~10 commands; `PATH`, `dev-` label, auto-update, link, pin each can fail | `gh repo clone martand-systems/mu ~/.mu-src && ~/.mu-src/install.sh` → ends in `mu setup mac`, 1 answer (which project). Later, with a public stub (Q1): one `curl … \| bash` line using `gh auth login --web` | T or C |
| J2 | New PC | ~8 commands + ~5 GitHub UI actions; a restart; admin window open 1–2 h | phase 3: `mu host install` then `mu pair` (2 commands, no `wsl`, no `-File`). Phase 5: one line at the PC, then the code; WSL install resumes after a restart; μ installs in the background as `mu`; the deploy key travels in the pairing answer and the Mac adds it with `gh` | W once |
| J3 | Relay first time | ~10 commands, ~25 Dokploy actions, 4–5 secrets printed, typed outside Claude | `mu relay setup` (Claude may run it), then paste, Save, Deploy. 0 secrets printed. First-time service creation stays a printed checklist | C + D |
| J4 | Pairing | Mac 1; PC 2 lines (~190 characters) + code; PATH failure | Mac: Claude runs `mu pair office-pc`; PC: `mu pair` + the code | C, W |
| J5 | One remote job | ~7 commands, ~10 flags, id typed 3–4 times, 3–5 asks | `mu run --on office-pc sweep`, or Claude's `mu submit` + a notification; 0 asks | C |
| J6a | Node offline | `mu compute status` → "on the PC: `wsl … mu-wsl2-distro status`" | home screen says last-seen and likely reason; `at the PC: mu` if needed | C (+W) |
| J6b | Version mismatch | exit 3, PC visit or `mu pin` | prevented by pre-installed pins; else step `mu compute install office-pc V` (agent) | C |
| J6c | Sudo needed | sudo prompt for a non-sudoer; apt as root by hand | warned in advance; `at the PC: mu fix`; 0 commands if the owner approves the node system updater (Q4) | W once |
| J6d | Failed job | exit 7, `mu log … --tail 50`, work it out | class + last lines + `mu run --again … --mem 24G` | C |
| J6e | Mac asleep | Claude's wait loop dies; nobody told | `mu watch` catches up and notifies | — |

Totals for J1–J5: from ~42 commands, ~35 UI actions, 6 places, ~25 switches, 5–6 secrets, to ~5 commands plus the
code, 3 UI actions, 3 places, ~3 switches, 0 secrets.

## 6. Build or adopt (PLAN §17)

The rule in PLAN §17: compare new plumbing with open-source tools before building, and record the comparison. Add a
row to PLAN §17's table pointing here when this plan is accepted.

| Need | Considered | Decision and reason |
|---|---|---|
| Prompts, colour, spinners, tables | **rich**, **textual**, **questionary**, **prompt_toolkit**/InquirerPy, **gum** (charm), click/typer | **Build** `mu/ui.py` (~150 lines stdlib: colour, ASCII fallback, step/OK/FAIL lines, spinner, table, confirm, hidden prompt via `getpass`, the `next` footer) and `ui.sh` (~40 lines, bash 3.2). rich and textual cannot load under `python -I -S` without vendoring and slow the ~40 ms start; prompt_toolkit's tree is not worth 3–5 prompts; gum is one more pinned binary on the Mac and the node; μ's hand parsing works |
| Full-screen TUI | textual, htop-style curses | **No.** Live views belong to the web dashboard (§7) |
| Shell completions | argcomplete, shtab, Cobra-style generators | **Build** a static generator from the command table. argcomplete needs the package and a Python start on every Tab; shtab needs argparse everywhere, and `bin/mu` is bash |
| Guided setup and checks | `flutter doctor`, `brew doctor`, `fly launch`/`fly doctor`, `gh auth login`, rustup-init, nix-installer receipts | **Take the patterns** (grouped checks with one fix each; plan, confirm, do, verify; a receipt). None is reusable code: each checks its own product |
| Mac install of a private repo | Homebrew private tap, signed `.pkg`, `gh extension`, fine-grained PAT, curl with a token, **a public stub using `gh auth login --web`** | **Build the stub** (phase 5, Q1). A tap is a second updater and needs a token variable; a `.pkg` still needs private hosting and $99 a year; a PAT is a long-lived secret |
| Windows install and front end | `irm \| iex` installers (Scoop, Chocolatey, Tailscale), winget REST source (rewinged), Scoop private bucket, `winget configure`, Docker Desktop / Rancher Desktop's Windows-native CLI over a hidden distro, a `.cmd` shim, a PowerShell module | **Build `mu.exe`** (the Docker Desktop pattern). `.cmd` mangles `& \| ^ %` in `%*`; a module is invisible to cmd; winget REST had a client 1.28 regression and is too much machinery for 1–3 PCs; Scoop buckets need git credentials on the PC; `winget configure` cannot install the distro and host service. Take `irm \| iex` for the first line from a public, tagged location (Q1) |
| Remote run that feels local | `modal run --detach`, SkyPilot `sky exec`/`logs`/`queue`, pueue (`follow`, groups, callbacks), Slurm | **Take the shape** (one verb, follow by default, detach on Ctrl-C, names, a notify hook). Adopt none: same conclusion as PLAN §17's pueue row; snapshot, pin, run record and safe fetch stay μ's |
| Secrets to a web UI | `pbcopy` with auto-clear (password-manager style), 1Password `op` | **Stock macOS tools.** `op` only if the company adopts 1Password |
| Relay bootstrap | Dokploy API (`compose.update`/`deploy`), relay-generated token in its log (Jenkins/Portainer style), keypair-signed admin requests, Dokploy templates | **`mu relay setup` with the clipboard now.** Dokploy API only for rotations, key read from a hidden prompt and never stored (its key controls the whole server). A token in the log is rejected (persistent logs, first-run hijack race). Keypair admin auth is the best end state; deferred (Python stdlib cannot sign; needs `ssh-keygen -Y sign` and SSHSIG parsing in Go) |
| Notifications | `terminal-notifier`, `osascript display notification` | **`osascript`**, already used by `au_notify`; no new binary |
| Mesh, last-seen, join keys | Headscale, NetBird | Unchanged from PLAN §17: the later option. If adopted, J3 and J4 get simpler again |

## 7. How this fits the dashboard

The terminal says what is happening and what to do; the dashboard shows it live and over time. Neither repeats the
other.

| Concern | Terminal (this plan) | Dashboard (separate work) |
|---|---|---|
| Source of state | `mu overview --json`: one schema, versioned | reads the same `mu overview --json` (or its library function), plus its own live metrics |
| Status | one-screen snapshot, three "needs you" items, one `next` | the full view: every job, every node, history |
| Live CPU and memory | not shown; a node line ends with `(live: mu dash)` | owns it: collection, charts, sampling |
| Next steps | `next` and `steps` in every result | shows the same `steps` (it must not invent its own advice) |
| Job follow | `mu run --on`, `mu attach` (log stream) | log view with the same job names |
| Notifications | `mu watch` (macOS notification, links `mu fetch NAME`) | may open from the notification |
| Opening it | `mu dash` (name to agree with the dashboard work) prints the local URL and opens it | |

Agreements needed with the dashboard work: the `overview` schema (this plan proposes it, phase 1), job names as
handles, and the `steps` field. No TUI is built here; if a terminal live view is ever wanted, it is the dashboard
work's decision.

## 8. Phases, ranked by friction removed per day

Friction is counted as in §1: commands typed, switches, asks and failure points removed, over one new-user setup plus
a normal week (assumed: 5 remote jobs, 1 update, daily "what's the status?"). Days are rough, for one engineer with
Claude, including tests.

| Rank | Item | Days | Friction removed (estimate) | Per day | Phase |
|---|---|---|---|---|---|
| 1 | Fix R2 in `PC_UPDATE`, `PC_PAIR`, `PC_MU` (`--exec /home/mu/.local/bin/mu`), if the pairing fixes have not already | 0.25 | a failed command, an ask and a retype at every PC visit (~4) | ~16 | 1 |
| 2 | `next` on every success + `steps` + `where`, bash and Python (`ui.py`, `ui.sh`) | 2 | most "what do I type next?" asks (~15 a week) and the J4 typing | ~10 | 1 |
| 3 | Home screen + `mu overview --json` | 2 | "what's the status?" (~5–10 asks a week, 3 commands each) | ~8 | 1 |
| 4 | No sudo prompt for non-sudoers (`sudo -n -l` before line 197); fail fast with the root step | 0.5 | the J6c hang and ~3 commands per release that adds a package | ~7 | 1 |
| 5 | `mu relay login` reads `relay.env` itself; `gen-credentials.sh env`/`client` refuse a terminal | 0.5 | the pipe typed outside Claude; secrets on screen (~3) | ~6 | 1 |
| 6 | `install.sh` from a checkout installs the latest tag; auto-update failure shows its error | 0.5 | J1 steps 5–6 (~2–3) | ~5 | 1 |
| 7 | Job names, `@last`, `mu run --on`, `mu attach`, `mu.toml` jobs, `mu watch` notification, failed-job hints | 4 | ~5 commands, an id and ~3 asks per job (~35–40 a week) | ~9 | 2 |
| 8 | Windows `mu.exe` + trampoline + session guard + `mu pair` split + `mu fix`/`mu host system` + Start-menu entry | 4 | every `wsl`/`-File` line, the PATH failure class, the wrong-shell confusion (~10–15 per PC visit) | ~3, but it removes the worst failures | 3 |
| 9 | `mu doctor` engine + `--fix` + `mu setup mac` + auto-link | 4 | J1 down to 1 line (~8), recovery asks | ~3 | 4 |
| 10 | `mu relay setup` (clipboard, poll, self-login) | 1.5 | J3 ~10 commands and 4–5 printed secrets, once per relay or rotation | ~7 once | 4 |
| 11 | Completions, `mu help --json`, the command table | 1.5 | typing and lookups (small, daily) | ~3 | 4 |
| 12 | One-line new PC (bootstrap, reboot resume), deploy key over pairing, μ installed in the background, "installing" node state | 8 | J2: an attended 1–2 h and ~13 actions become a 10-minute visit | ~2, largest wall-clock win | 5 |
| 13 | Node pre-installs pins; relay last-seen; Mac stub installer | 4 | J6a, J6b, J1's clone and sign-in | ~2 | 5 |
| — | Node system updater (root timer for packages), dropping static relay clients, Dokploy API rotations, keypair admin auth | — | J6c to 0; Dokploy edits only for key rotation | — | later, each needs an owner decision and a security review |

### Phase 1: the next step and the home screen (about 6 days)

Items 1–6. Smallest work, most frequent friction.

Acceptance tests:
- **Next-step coverage:** a test walks the command table, runs every command (success and each documented error)
  with `--json` in a sandbox, and fails if any result lacks `next` or `steps`, or has a step without `where` and `by`.
- **Agent-safe:** every command with `--json` and stdin from `/dev/null` under a 10 s timeout prints exactly one JSON
  object and never waits for input.
- **Must fail:** a fake node that returns `next`, `steps`, `by`, `where` or `secret` in its reply cannot change those
  fields in the client's output (extends today's `exit`/`next` test).
- **Must fail:** a lint over μ's source fails if any string meant for a person (hints, `next`, `steps[].cmd`) contains
  `wsl -d` without `--exec`, or `ExecutionPolicy`, outside the Windows front end's own code.
- **Secret canary:** a test relay env with canary values; every `relay` and `gen-credentials.sh` command is run with a
  terminal and without; stdout, stderr, the JSON and a `ps` snapshot taken during each run must not contain a canary.
- **Home screen:** plain `mu` returns in under 1 s with the relay blackholed (shows the cached state and its age),
  and shows "not set up" with `next: mu setup` on a clean `MU_HOME`.
- **No sudo prompt:** `install.sh` run on Ubuntu as a non-sudoer with a terminal and a missing package exits non-zero
  within 5 s, printing the root step, never a password prompt.
- **bash 3.2:** the dispatcher tests run under `/bin/bash` on macOS; with `NO_COLOR` or no terminal, no escape codes
  appear.
- **Count:** the scripted J4 shows at most 2 lines for the person at the PC (the command and the code), and none
  contains `wsl` with `--` and a bare `mu`.

### Phase 2: remote jobs as one command (about 4 days)

Item 7.

Acceptance tests:
- **Count:** a scripted J5 against the CI node (`mu run --on ci-node sweep` with a `[jobs.sweep]` table) takes 1
  command from submit to fetched results, exits 0, and leaves `runs/<id>.json`.
- Ctrl-C during `mu run --on` leaves the job running; `mu attach NAME` resumes the log and fetches at the end.
- An ambiguous name exits 2 and lists the candidates; old full ids still work in every job command.
- `mu watch` posts exactly one notification per finished job (test with the notifier stubbed), and is not running
  when no job is unfinished.
- **Must fail:** auto-fetch, when on, never writes outside `runs/<id>/`.

### Phase 3: `mu` at the PC (about 4 days)

Item 8. Tested on a clean Windows 11 VM (or the office PC), with the steps in a checklist like
`first-job-checklist.md`.

Acceptance tests:
- **Count:** with the host installed, pairing at the PC is `mu pair` plus the code, from a non-admin PowerShell;
  checking the node is `mu`. No line typed contains `wsl`, `powershell` or a path.
- Exit codes 0–10 pass through `mu.exe`; arguments with spaces, quotes, `%`, `&` and UTF-8 arrive unchanged.
- **Must fail:** `mu.exe` run from a signed-in session before the startup task has run does not start WSL itself;
  after signing out, the distro is still running (`test-stop` style check).
- **Must fail:** `/usr/local/lib/mu/mu-winshim` is root-owned and not writable by `mu`, and refuses to run as root.
- **Must fail:** the pairing code never appears in an argument list (process snapshot) or in `C:\Mu\logs`.
- Elevated verbs show their output in the original window.

### Phase 4: setup, doctor, relay, completions (about 7 days)

Items 9–11.

Acceptance tests:
- **Count:** on a clean macOS VM with `gh` signed in, J1 is one line and one answer, and ends with a linked project
  and auto-update on. `mu setup mac` run twice changes nothing the second time.
- `mu doctor --fix` fixes a broken link, a missing pin and auto-update off; it never runs a step marked `human`,
  `secret` or needing root (tested by a check whose fix is a canary command that must not run).
- `mu doctor` warns about a 2.1 GB untracked folder and LFS content before any submit.
- **Count:** J3 against a test relay is `mu relay setup` plus 3 UI actions; the clipboard is empty (or changed) 90 s
  later; the secret canary test passes.
- Completions work under macOS `/bin/bash` 3.2, zsh and fish, and complete node and job names from local files in
  under 100 ms.

### Phase 5: a new PC in one line (about 12 days, after Q1)

Items 12–13.

Acceptance tests:
- **Count:** on a clean Windows 11 VM, from the Mac's printed line to "paired, installing in the background": 1 line
  and the code at the PC, through a forced restart, with nobody re-running anything.
- The Mac's home screen shows the node installing with progress, then a notification "office-pc ready for jobs".
- The node's deploy key is added by the Mac with `gh` and removed by `mu compute remove`.
- **Must fail:** a bootstrap file changed by one byte is refused before it runs.
- **Must fail:** nothing in the bootstrap or the pairing answer lets the relay choose code that runs elevated (the
  pairing threat model treats the relay as an adversary).

## 9. Risks

| Risk | Mitigation |
|---|---|
| Renames break Claude's instructions and the owner's habits | aliases for three releases; `CLAUDE-toolchain.md` changes in the same release; a test runs every old spelling |
| Automatic actions surprise (auto-link, pin installs, `.gitignore` offers) | every automatic action prints one line saying what it did; `--dry-run`; nothing that changes the project runs without `ask` or a terminal "yes" |
| The secret rule is policy, not a boundary (§4.7) | the canary tests; μ never prints, logs or passes secrets as arguments; Claude's instructions say never to read `relay.env` or the Keychain; a separate-user or signed-helper boundary noted as a later option |
| The clipboard leaks a secret (clipboard managers, Universal Clipboard to other Apple devices) | 90 s auto-clear; mark the item with the `org.nspasteboard.ConcealedType` convention through `osascript -l JavaScript` (whether Universal Clipboard honours it is unverified); Q2 |
| `mu.exe` starts WSL from a signed-in session and the node stops at sign-out | the session guard and its must-fail test |
| Elevated output lost; argument quoting through `wsl.exe` | redirect to a log the parent shows; quoting and exit-code tests on the PC |
| The home screen gets slow (network) | cached state with its age; background refresh; the 1 s test |
| Duplicated work with the dashboard and the pairing fixes | §7's ownership table; check the pairing-fix branch for item 1 before starting |
| A public bootstrap (Q1) reveals the infrastructure layout | keep it to the host scripts, which hold no secret; owner decision |
| The node system updater runs as root on data from the network | not in a phase; needs a security review (pinned `known_hosts`, `HOME=/root`, installs only `apt.txt` packages) |
| bash 3.2 limits | `ui.sh` and completions tested under `/bin/bash` on macOS in CI |

## 10. Questions for the owner

1. **Q1, the first script on a fresh PC:** (a) a small public bootstrap repo with the host scripts, tagged per release
   (recommended); (b) make `mu` public; (c) keep the zip or USB stick (phase 5 is then 2 commands, not 1). Serving it
   from the relay was considered and rejected unless its hash is bound by something the relay cannot change (§12).
2. **Q2:** may μ put a secret on the clipboard for 90 s for the Dokploy paste?
3. **Q3:** drop the static relay clients, so Dokploy holds only the relay key and admin token and is edited only to
   rotate them?
4. **Q4:** a root-owned package updater on the node (after a security review), or keep `mu fix` at the PC?
5. **Q5:** `mu status` with no argument: keep it as the job list (no break), or make it the home screen after two
   releases of warning, with jobs at `mu jobs`? Recommended: the second.
6. **Q6:** finished jobs fetched automatically into `runs/<id>/`: on by default, or opt-in?
7. **Q7:** may `mu fix` at the PC restart the distro without asking when no job is running?
8. **Q8:** `mu pair` as the Mac's name for `mu compute invite` (the same word at both ends): yes or no?
9. **Q9:** the dashboard's command name (`mu dash` is assumed here) and the `overview` schema: agree with the
   dashboard work before phase 1 ends.
10. **To check at the PC before phase 3:** `wsl.exe` exit-code and quoting passthrough with `--exec`; `systemctl
    --user` under `--exec` with `XDG_RUNTIME_DIR` set; whether the first WSL install always needs a restart; whether
    the org lets the owner add deploy keys with `gh`.

## 11. Not done here

- No code was changed. Implementation starts after the owner answers §10.
- Second workstations (pairing stage P2), Linux workstations and several users are out of scope; the contract and
  command set are meant to carry over to them unchanged.

## 12. Where the research notes disagreed

| Topic | Notes | Decision here |
|---|---|---|
| The Windows front end | A: `mu.cmd` shim; D: `mu.exe` compiled on the PC | `mu.exe`: `.cmd` mangles `& \| ^ %` and quotes in `%*`. A's session guard is kept |
| How the Windows `mu` reaches the distro's `mu` | A: `--exec /home/mu/.local/bin/mu`; D: a root-owned trampoline | the trampoline, which also sets `XDG_RUNTIME_DIR` and locale; it refuses to run as root, which answers A's warning about root running mu-owned code. Phase 1's bug fix uses A's simpler `--exec` form until `mu.exe` exists |
| The `next` field | C asked: change it to an array, or add a field | keep `next` (string), add `steps` |
| Bootstrap for a fresh PC | A: public bootstrap repo, relay rejected; D: relay serves the host bundle per invite, verified by a 12-character hash in the URL | A's recommendation. A 48-bit hash prefix is too short against a relay treated as an adversary, and the stub runs elevated. If the owner wants the relay route, the bundle must be verified with the full SHA-256 or a key derived from the pairing code, which the relay never learns |
| Autoupdate after install | the brief: a separate step; D: already on by default | already on; the remaining fix is showing the error when turning it on fails (A) |
| `mu.cmd` vs interop off | C asked whether it conflicts | no: interop off stops Linux starting Windows programs; Windows starting the distro is unaffected (D) |
