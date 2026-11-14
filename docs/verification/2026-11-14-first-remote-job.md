# Verification: pairing by one code and the first remote job on the office PC, 2026-11-14

**Question (PLAN §13, "Path to the first remote job"; `docs/design/first-job-checklist.md` §3–§4):** with release
v2026.11.8, does pairing by one code work on the real office PC and the real relay, and does a job sent from the Mac
run on the PC, through the relay, with the same result as on the Mac?
**Answer:** yes. The PC was paired with the owner's Mac by one code, the first remote job (`fea-cantilever`) succeeded
and matched the Mac's result, and the three other ways a job can end (`oom`, `timeout`, `cancelled`) each happened
once on the PC.

The numbers below were read from the owner's session by the lead and passed on; the screen output itself is not in
the repo.

## Before the visit

- **v2026.11.8 released** (merge `52bc680`): pairing by one code (`mu compute invite` on the Mac, one `pair` line at
  the PC), the relay's pairing service (`mu-relayd`), dispatcher rev 9.
- **The relay was redeployed with `RELAY_ADMIN_TOKEN`:** pairing on.

## At the PC

| Step | Result |
|---|---|
| `mu update` (as `mu`) | v2026.11.8 installed in **20 s**: every layer reused, `mu doctor` 23/23, benchmarks 6/6 |
| The pair line | worked with the full path to `mu` (below) |

**Found: `mu: command not found`.** The line the Mac prints, `wsl -d mu-compute -u mu --cd ~ -- mu …`, starts a shell
that does not read `~/.profile`, so `~/.local/bin` is not on its PATH. The line that worked:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File (wsl -d mu-compute -u mu --cd ~ -- /home/mu/.local/bin/mu node host-script) pair
```

To be fixed in a coming release (the same short form is in `src/mu/invite.py`, `src/mu/node.py`'s hints,
`mu-wsl2-distro.sh`'s hints and the design documents). The guides use the full path until then
(`docs/install/windows-compute-node.md`).

## On the Mac

The invite ended with:

```
office-pc answers through the relay: μ v2026.11.8, runner active, memory limit enforced (5.6 s)
paired office-pc
```

`mu compute status office-pc`: **up, 0.87 s** with a reused connection; Intel Core Ultra 7 265K, 20 cores, **22 GB
RAM for jobs**, **89 GB free**, memory limit enforced, last stop clean. (Which network the Mac was on was not
recorded.)

This also settles S4's first open item on the real server: the PC's end of the tunnel, in WSL2, through the office
network.

## The first remote job

`fea-cantilever` as in `docs/design/first-job-checklist.md` §4.2, job **`20261114-101926-9yb3u4`**: `succeeded` in
**4.5 s**.

| Metric | Office PC | Mac | Difference |
|---|---|---|---|
| tip deflection, h = 5 | 2.26735 mm | 2.26746 mm | 0.005 % |
| tip deflection, h = 2.5 | 2.27030 mm | 2.27033 mm | 0.001 % |
| nodes, h = 5 | 3,837 | 3,892 | 1.4 % |
| nodes, h = 2.5 | 21,609 | 21,505 | 0.5 % |

The node counts differ because Gmsh's mesh differs slightly between the platforms. Both pass the checklist's
criteria: deflections within 0.5 % of each other, node counts within 5 %, and each run `passed` against its own
platform's reference. (Rows in the order the checklist's comparison prints the metrics `tip_h5`, `tip_h2.5`,
`nodes_h5`, `nodes_h2.5`.)

## Failure paths on the PC

| Job | Result |
|---|---|
| `--mem 256M`, a 1 GiB allocation | ended `oom`, exit 7 |
| `--time 20s`, a 600 s sleep | ended `timeout`, exit 7 |
| `mu cancel` on a running job | `cancelled` |
| all six benchmarks as one job | **6/6 PASS in 13.4 s** (the Mac's suite takes about 25–37 s) |

So the cgroup v2 memory limit for user units works under the PC's WSL (job-layer §19's main unknown), and so do the
time limit and cancel.

## Not done yet

- The restart tests of `install/hosts/wsl2/README.md` (a baseline restart with `mu-wsl-shutdown` stopped, then
  restarts with it), and a clean stop during a job (checklist §5.3).
- A real Windows Update restart; a WSL update.
- A second network for the Mac, recorded as such; idle beyond 5 minutes and a relay redeploy with a session open.
- Whether Dokploy keeps the relay's volume across the next deploy (`mu relay status`: the same store).
- The JATAYU frame model on the PC (PLAN P7).
