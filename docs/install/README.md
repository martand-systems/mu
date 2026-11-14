# Installing μ: which guide, in what order

μ runs on two kinds of machine. A **workstation** is a Mac where people and Claude Code design parts and run quick
analyses. A **compute node** is an always-on Linux machine that runs long solver jobs sent from a workstation; today
that is the office PC, a Windows 11 PC running Linux in WSL2. The two talk through the **relay** at
`relay.martand.systems`, a small service on Martand's Docker server.

| You want to | Guide | Who | Time |
|---|---|---|---|
| Use μ on your Mac | [mac.md](mac.md) | every engineer | 2–3 min with the downloads cached (measured); a new Mac also downloads about 3 GB first (not timed) |
| Run jobs on the office PC from your Mac | [../using-compute-jobs.md](../using-compute-jobs.md) | engineers, Claude Code | read once: 10 min |
| Turn a Windows 11 PC into a compute node | [windows-compute-node.md](windows-compute-node.md) | whoever sets up the PC, with the owner for two steps | about half a day (estimate): under an hour of typing, about 1.5 h of unattended install (96 min measured on the office PC), then the pairing |
| Set up or look after the relay | [relay.md](relay.md) | the owner (holds the relay's credentials) | the first setup is a Dokploy form; later changes take minutes |

## Order for a new setup

1. **The relay** ([relay.md](relay.md)). It is already running for Martand; skip this unless it is being rebuilt.
   Pairing a compute node needs its pairing service turned on (the admin token, once).
2. **The owner's Mac** ([mac.md](mac.md)), logged in to the relay with `mu relay login` (relay.md, step 3). Only a
   Mac that holds the relay's admin token can pair a compute node.
3. **The compute node** ([windows-compute-node.md](windows-compute-node.md)). It ends with the pairing, done from that
   Mac, and a first job.
4. **Other engineers' Macs** ([mac.md](mac.md)). They can use μ locally at once. Sending jobs to the office PC from a
   second Mac is not built yet (stage P2 of `docs/design/pairing.md` §10); ask the owner.

## What is where

- Day-to-day commands, updates and the folder layout: the top-level [`README.md`](../../README.md).
- The plan and its status: [`docs/PLAN.md`](../PLAN.md).
- What was measured, and on which machine: [`docs/verification/`](../verification/).
- The design documents behind the job layer and pairing: [`docs/design/`](../design/). The guides here link to the
  parts that matter; you do not need to read them to install anything.

## Status of these guides (2026-11-14, μ v2026.11.8)

- **Mac:** the installer has run on the owner's M1 MacBook many times, including fresh clean-environment installs
  (`docs/verification/2026-11-13-S1-M5.md`, `2026-11-13-installer-v2.md`).
- **Windows compute node:** the office PC was first set up **by hand** (spike S3), then the host scripts were run over
  that setup and adopted it, then it was paired and ran its first remote job on 2026-11-14. **A run of the scripts on
  a brand-new PC, from nothing, has not happened yet.** The Windows guide marks each step that has only run in CI or
  was only read in the scripts.
- **Relay:** deployed on 2026-11-14; its pairing service was turned on the same day.
