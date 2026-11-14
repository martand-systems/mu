# Running jobs on a compute node

`mu submit` runs a command on a paired compute node (the office PC: Linux, 20 cores, 22 GB of RAM for jobs) instead
of on your Mac, from a snapshot of your project, under the project's pinned μ. You get the job's log, its declared
output files and a run record back. This page is for engineers and for Claude Code; Claude Code's own rules are the
"Compute jobs" section of `claude/CLAUDE-toolchain.md`, which this page agrees with.

**Status (2026-11-14, v2026.11.8):** the office PC (`office-pc`) is paired with the owner's Mac (`avi-mac`) and has run
its first jobs (`docs/verification/2026-11-14-first-remote-job.md`). Pairing a second Mac is not built yet (stage P2
of `docs/design/pairing.md`); until then, jobs go from the owner's Mac. There is no automatic fallback to local runs
yet (the job layer's second stage).

## When to run remotely, when locally

| Run locally (`mu run …`) | Submit to the node (`mu submit …`) |
|---|---|
| anything under about 5 minutes | long runs: FEA and CFD sweeps, mesh-convergence series |
| interactive or visual work: CAD, renders, the cad server, viewers | parallel OpenFOAM (MPI-parallel snappyHexMesh hangs on Apple Silicon laptops) |
| a check whose answer you need right away | anything that would keep the laptop busy or awake for long |
| when no node answers (`mu compute status` not exit 0) | |

Claude Code asks before its first remote run if you did not ask for remote runs, and does not retry an unreachable
node in a loop.

## Before the first job

```sh
mu compute list                 # the paired nodes (no network)
mu compute status office-pc     # does it answer? exit 0 = up
```

`mu compute status` prints the node's μ versions, its runner, its queue, its hardware, free disk, whether the memory
limit is enforced and whether its last stop was clean. On 2026-11-14 it took 0.87 s with a reused connection (a first
connection takes a few seconds).

The project needs:
- a `mu.toml` with a released version (`[mu] version = "v2026.11.8"`), installed on the node too. The node keeps up
  with releases by itself; a pin it lacks is refused with exit 3 (§Exit codes);
- to be a git repo (the snapshot is a git commit);
- optionally, its outputs declared in `mu.toml`:

  ```toml
  [compute]
  outputs = ["results/*.json", "frame/report.md"]
  ```

## The commands

### Submit

```sh
mu submit --name sweep --time 2h -- python sweep.py
```

Everything after `--` is the command. The node runs it as `mu run <command>`, so give the bare command
(`-- python sweep.py`, never `-- mu run python sweep.py`), in the same folder (relative to the project root) you
submitted from. It prints the snapshot it sends, then the job id and its state:

```
20261114-101926-9yb3u4: snapshot 3f2a1c0e9d = HEAD 9c1d4b7a20 + uncommitted changes; pushing to office-pc (only what it lacks)...
20261114-101926-9yb3u4 queued on office-pc
  mu status 20261114-101926-9yb3u4 --wait --timeout 500 · mu log 20261114-101926-9yb3u4 --tail 20 · mu fetch 20261114-101926-9yb3u4
```

(Format from the code; the ids and hashes here are illustrative.) Options:

| Option | Meaning | Default |
|---|---|---|
| `--on NODE` | the node | the only paired node (needed when several are paired) |
| `--name TEXT` | a label for the job | the command |
| `--mem SIZE` | memory limit (`512M`, `16G`; powers of 1024). Above it the job ends `oom` | the node's RAM minus 2 GB |
| `--time DUR` | wall-time limit (`90s`, `30m`, `6h`, `2d`). Then it ends `timeout` | 12 h |
| `--cores N` | sets `$MU_JOB_CORES` only; pass it to the solver yourself (`mpirun -np $MU_JOB_CORES`, `OMP_NUM_THREADS=$MU_JOB_CORES`). Not enforced | all physical cores |
| `--output GLOB` | an output to bring back, repeatable | `[compute] outputs` in `mu.toml` |
| `--wait [--timeout S]` | then wait for the result, as `mu status ID --wait` | |
| `--no-rerun` | do not re-run after an interrupted attempt | re-run once |
| `--allow-large` | send over 100 MB of uncommitted or untracked files | refused |
| `--resume ID` | send a job again that was not accepted (same id, same snapshot) | |
| `--json` | one JSON object (read `exit`, `error`, `next`) | |

One job runs at a time on a node; the others queue, first in first out.

### Status

```sh
mu status                                  # this project's jobs (plain `mu` is the toolchain's status)
mu status 20261114-101926-9yb3u4           # one job: id, state, attempt, exit code, note
mu status 20261114-101926-9yb3u4 --wait --timeout 500
```

States: `queued`, `running`, then exactly one of `succeeded`, `failed`, `cancelled`, `timeout`, `oom`,
`interrupted` (also `sending`: a job this clone never got accepted). `--wait` exits 0 only for `succeeded`, 7 for any
other end, 10 if `--timeout` ran out first (the job keeps running: ask again). It rides out a lost link.

### Log

```sh
mu log 20261114-101926-9yb3u4 --tail 50     # the last 50 lines of stdout and stderr
mu log 20261114-101926-9yb3u4 -f            # follow until the job ends
```

`-f` blocks; in a call with a time limit (Claude Code), poll with `--json --offset N` instead. A job whose log grows
past 4 GB is stopped and ends `failed`.

### Fetch

```sh
mu fetch 20261114-101926-9yb3u4
```

It writes the declared outputs into the project at their paths (overwriting), and the run record
`runs/<job-id>.json`, and prints `<job-id> succeeded: N output(s) written under ~/…; run record runs/<job-id>.json`
(plus `matched nothing: <glob>` for a declared output the job did not make). `--into DIR` writes elsewhere.
It stops above 50 MB of outputs unless `--max-size` allows more.

### Cancel

```sh
mu cancel 20261114-101926-9yb3u4            # stops a queued or running job: … cancelled
```

## What travels to the node

A snapshot of your working tree, made without touching your index, branch or files: `HEAD` plus uncommitted changes
plus untracked files that are not gitignored. Git sends only what the node lacks, so a typical submit is kilobytes.

| In your project | Reaches the node |
|---|---|
| tracked files as they are now (staged or not), untracked files that are not ignored, deletions | yes |
| **everything committed, history included** | yes: a secret committed once and deleted later reaches the node with the first push |
| gitignored files | **no**: generate big inputs (meshes) inside the job |
| a **git submodule** (for example `org/`) | **only its commit id**: the folder is empty on the node. Submitting from inside one is refused |
| a **git-lfs** file | **only the pointer** (about 130 bytes), not the content |
| over 100 MB of uncommitted or untracked files | refused unless `--allow-large`: gitignore generated files |

`mu submit` warns about each submodule and LFS pointer it sends. Sending their content is the job layer's second stage.

## What comes back

- **Declared outputs only**: globs relative to the project root (`results/*.json`), regular files only, never into
  `.git` or `.mu`, and a wildcard matches no hidden name. Every file is checked against the size and SHA-256 the node
  recorded; a mismatch is exit 8 and nothing from that file on is written. Keep outputs small.
- **The log**, through `mu log`.
- **The run record** `runs/<job-id>.json`, written at fetch: the job id and name, the project's commit (`head`), the
  snapshot and whether it had uncommitted changes, the command and folder, the limits, the attempt, the state and
  exit code, start and end times and wall time, each output with size and SHA-256, the toolchain that ran (version,
  recipe commit, layers), the node and its hardware, and where the full work folder stays on the node
  (`office-pc:/srv/mu/jobs/<job-id>/work`). **Commit it with the results it backs.**

After `succeeded`: fetch, then validate as for a local run (the company validation policy in the org profile applies
to remote results too).

## Exit codes

Every job command takes `--json`; read `exit`, `error` and `next`. `next` is μ's own advice; a node's words, if any,
are in `node_message`.

| Exit | Meaning | What to do |
|---|---|---|
| 0 | ok | |
| 2 | usage or project (arguments, `mu.toml`, the repo, outputs too large to fetch) | fix what it names |
| 3 | the node lacks the project's pinned μ | tell the owner (installing takes over an hour at the PC), or `mu pin` a version the node has |
| 4 | unreachable: relay or node offline, link lost | retry later. An accepted job keeps running |
| 5 | refused: host key, key not authorised or revoked, relay credential, protocol | never retry; tell the owner |
| 6 | the node cannot take jobs: disk low, memory limit not enforced, runner stopped | tell the owner; the message names the cause |
| 7 | the job ended but not `succeeded` | `mu log <id> --tail 50` |
| 8 | fetch integrity | `mu fetch` once more |
| 9 | no such node or job | |
| 10 | still running after `--timeout` | ask again |

If a submit failed after printing a job id (`job_id` in the JSON), send the same job with `mu submit --resume <id>`,
never a fresh submit.

## Limits and failures, as measured on the office PC

| Case (2026-11-14) | Result |
|---|---|
| `fea-cantilever` benchmark, first remote job | `succeeded` in 4.5 s; tip deflections within 0.005 % and 0.001 % of the Mac's |
| all six benchmarks as one job | 6/6 PASS in 13.4 s (the Mac's suite: about 25–37 s) |
| `--mem 256M`, a 1 GiB allocation | `oom`, exit 7 |
| `--time 20s`, a 600 s sleep | `timeout`, exit 7 |
| `mu cancel` on a running job | `cancelled` |

**Restarts:** if the node stops during a job (a Windows restart, a clean stop), the job is re-run once from the start
when the node is back (`attempt 2`, and the log keeps both attempts with a line `== μ: attempt 1 ended without a
result …` between them); a second interruption ends it `interrupted`. This is tested in CI; on the office PC it is
still to be run (`docs/design/first-job-checklist.md` §5.3).

## Example: the first remote job

From `docs/design/first-job-checklist.md` §2.4 and §4.2: a project whose job is μ's own FEA benchmark.

```sh
mkdir ~/mu-first-job && cd ~/mu-first-job && git init -q
printf '[mu]\nversion = "%s"\n\n[compute]\noutputs = ["bench/results.json"]\n' "$(cat ~/Library/Mu/default)" > mu.toml
printf 'bench/*/\n' > .gitignore
git add -A && git commit -qm "first job: fea-cantilever"

mu submit --on office-pc --name fea-cantilever --time 1h -- python -m mu.bench fea-cantilever --workdir bench
mu status <job-id> --wait
mu log <job-id> --tail 20
mu fetch <job-id>          # bench/results.json and runs/<job-id>.json
```

## More

- How the node is set up: [install/windows-compute-node.md](install/windows-compute-node.md).
- The design (protocol, durability, security): `docs/design/job-layer.md`.
