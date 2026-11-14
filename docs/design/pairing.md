# μ pairing by one code

The design for pairing a compute node (and later a second workstation) with one code typed at the PC, through a small
control service added to the relay. It replaces steps 2.3 and 2.6–2.11 of `docs/design/first-job-checklist.md` (the
USB stick, the two key files, the two fingerprint comparisons) and keeps that path working as the fallback. Written on
branch `pairing` from v2026.11.7. What was read in sources to decide it is in §15. §0 lists what changed after the
design review (`8605813`), finding by finding; the sections below are written in their changed form.

Sections: [0 After the review](#0-after-the-review) · [1 Summary](#1-summary) · [2 Decisions](#2-decisions-and-what-was-rejected) · [3 User flows](#3-user-flows) ·
[4 The code](#4-the-code) · [5 Protocol](#5-protocol) · [6 Relay service](#6-the-relay-service-mu-relayd) ·
[7 Container and Dokploy](#7-container-compose-dokploy) · [8 Mac](#8-the-workstation-side) ·
[9 PC](#9-the-pc-side) · [10 Second workstation](#10-a-second-workstation-stage-p2) · [11 Migration](#11-migration) ·
[12 Threat model](#12-threat-model) · [13 Tests](#13-tests) · [14 Not tested, open questions](#14-not-tested-and-open-questions) ·
[15 Read in sources](#15-read-in-sources) · [16 Files and order of work](#16-files-and-the-order-of-work) ·
[17 Review](#17-review)

## 0. After the review

The review of `8605813` (scratchpad `pairing/design-review.md`) found the cryptographic core sound and thirteen problems
around it. Every finding is applied, with the two deviations named in the last column.

| # | Finding | Change | Where |
|---|---|---|---|
| 1 | An empty auth file makes chisel an open proxy (`server.go` `authUser`: no users, no check) | The auth file always holds a sentinel user `_mu-sentinel` (random password per start, rule `^-$`, which no address matches); every write is checked on its exact bytes first (sentinel present, unique names, only the two anchored loopback rule shapes, ports 7001–7999, one compute client per port, never `""` or `*`); removing the last real client is refused (409 `last-client`). Fault `allow-empty`, S4 row P10. *Deviation:* the check asks for the sentinel plus **at least one** entry, not "at least 2 users": a relay whose every static client is revoked (`RELAY_REVOKED`) starts with the sentinel alone, which authenticates nobody | §6.5 |
| 2 | Copying a static password into a dynamic override defeats rotation and revocation | No password is ever copied. A static workstation gets extra ports as a **grant** (`grants: {avi-mac: [7002]}`); its password is always the static one. A dynamic client carries only the password its claimer chose (or a `PUT` gave). New optional `RELAY_REVOKED=name,…` in Dokploy: those names are dropped from the static list, and every dynamic client and grant of those names is deleted at every start, so it survives a lost volume | §6.5, §12 |
| 3 | The PC rolls back if the Mac is not live within 15 minutes | The PC never rolls back for a missing confirm: once its tunnel is connected it waits about 2 minutes, then exits 0 `paired with avi-mac (not yet confirmed)`. R keeps an answered invite and its client for 24 h after creation (then `unconfirmed`, client kept). The Mac verifies a late answer up to 24 h after `created` (`--wait`), and keeps the Mac awake (`caffeinate -i -w`) while it waits. The secret is now **90 bits** (24 h against the §4.3 attacker: 2^-27.1) | §4, §5.4, §8.3, §9.3 |
| 4 | A code thief can win the race; re-claims replace the PC's session | **Session continuity**: a claim gives a session; the PC saves it before anything else and resumes with it (`claim` with `session`). A claim without the current session never replaces it: it is refused (`invite-used`) and marks the invite `contested`, whatever the address (Q5 gone). **Authenticated contest**: a PC refused with `invite-used` posts `contest_mac = HMAC(K_ci, enc(LABEL,"contest",id,offer_digest))` with its claim token; R removes the client that invite created, and the Mac (which verifies the MAC) refuses or un-pins the node | §5.2, §5.4, §12.1 |
| 5 | Relay text reaches Claude Code and the elevated console | Clients show only their own text, chosen by the `error` slug and `state`; R's `message` is never shown or forwarded. Addresses must parse as IP literals, times as integers, names by the name rule; control characters are stripped. Relay fault `evil-text` puts injected text into every message: no client output may contain it | §3.4, §5.3 |
| 6 | PowerShell 5.1 pipes the code with CR LF | The code parser drops a leading U+FEFF and trailing CR/LF before checking; a unit test feeds the exact bytes. The ps1 writes the code to `wsl.exe`'s standard input through `System.Diagnostics.Process` (no cmdlet pipeline, so no Module Logging record) | §4.1, §9.2 |
| 7 | Signed key requests (P2) let any workstation add lasting keys or revoke the owner | Only the node's **owner** keys (the inviter of the first pairing, `/etc/mu/owners`) may sign; each key records who authorised it and revoking a key revokes the keys it authorised; the nonce is checked (`[0-9a-f]{32}`) before it is used in a path; request and signature are copied to a root-only folder before `ssh-keygen -Y verify`. The dormant parser is installed at the first visit, owner-only (accepted explicitly, so P2 needs no visit) | §9.6 |
| 8 | An unverified answer replaces a working client (`--replace`) | For a replacement, R keeps the old client at `answer` and swaps it only at the admin call `POST …/accept`, which the Mac makes after verifying the answer. A new name is still created at `answer` | §5.4, §8.3 |
| 9 | The PC's 60-minute bound does not hold against the relay | The PC records when it first used each id (root state) and refuses an id first used more than 60 minutes ago; every PC request has a timeout (connect 10 s, long poll 35 s); the PC prints no expiry time before a successful claim | §4.3, §9.3 |
| 10 | Lockout and rate limits are levers for denial of service | Lockout at **20** wrong claims; an unknown id and a wrong code get the same answer (403 `wrong-code`); no global bucket for claims; the per-address table is capped (oldest evicted); a noisy address is held off for 15 min, not an hour | §6.4 |
| 11 | Store and merge details | `state.json` exists at every point of a write (`.prev` made by a hard link); a fixed rule when a Dokploy edit makes static and dynamic disagree (the dynamic client wins, the static entry is dropped and listed as `shadowed`); deleting a compute client removes its port from every workstation's grants and ports; anchored Go regexes; passwords at most 128 characters; `mu-relayd` refuses WebSocket upgrades | §6.5, §6.6 |
| 12 | The PC can be left half set up | Every `pair` run first reconciles `current.json` (asks R with its session; a claim never answered whose invite ended is undone); `status` reports a half-done pairing; `offer_digest` is stored and the re-read offer must be identical | §9.3 |
| 13 | Protocol hygiene | Duplicate JSON members refused on all three sides; `relay_url` must be `https` and is compared normalised, and the Mac prints `-Relay URL` in the PC line when it is not the default; no downgrade (the protocol is fixed by `LABEL` inside every MAC; a client never accepts a lower version because R says so); the confirm's outcome is a fixed list; the scripts are staged in `C:\Mu\stage` (Administrators and SYSTEM only) rather than `%TEMP%`; `-File (wsl …)` breaking on stray wsl.exe output is listed as untested. *Rejected:* the optional mod-32 check symbol: a typo the 10-bit check misses costs one counted wrong try of 20, nothing more, and the simpler check stays | §4.1, §5, §9.1, §14 |

The lead's decisions stand, with decision 3's lockout at 20 tries (finding 10). Open questions: Q1 two commands,
accepted; Q2 the code is printed; Q3 no scrypt, 90 bits instead; Q4 no admin token for a second workstation; Q5 solved
by finding 4; Q6 inherited from the job layer; Q7 `--relay` stays opt-in; Q8 `RELAY_REVOKED`.

## 1. Summary

- **On the Mac:** `mu compute invite office-pc` prints two lines to type at the PC and one code, then waits. It ends by
  itself with `paired office-pc` once the PC has answered and the Mac has reached it through the relay.
- **At the PC (Terminal (Admin)), two commands:** `mu update` (a no-op when the release is there), then one line that runs
  the installed release's host script with the action `pair`. It sets the node up (the existing `install -NoRestart`),
  asks for the code (not echoed, never an argument), restarts the distro cleanly when the setup needs it, writes the
  relay credential, authorises the Mac's key, sends its host key, waits for the Mac and ends with `paired with avi-mac`
  and `ready for jobs: yes`. No stick, no file carried, nothing compared by eye.
- **The code** is 24 characters, `7K3M-Q9TD-4WXR-HN2C-PB8F-ZJ6R`: a 20-bit invite id (sent to the relay), a 90-bit
  secret (never sent anywhere) and a 10-bit checksum against typos. Every key that crosses (the Mac's public key, the relay URL
  and fingerprint, the node's name and port, the node's host key) is authenticated with HMAC-SHA-256 under keys derived
  from the secret, one key per direction. A relay that sees and changes every message cannot make either side accept a
  key it did not send: it would have to find the secret, 2^90 candidates, within 24 hours (§4.3).
- **The relay** gains `mu-relayd`, a small Go program (standard library only) in the same container as chisel. chisel
  stays in front on port 8080 and passes every request that is not a chisel WebSocket to `mu-relayd` on
  `127.0.0.1:8081` (`chisel server --backend`, §15). So Traefik, Dokploy's Domains entry and the connection limit are
  unchanged. `mu-relayd` creates relay clients through an admin API (token `RELAY_ADMIN_TOKEN`, held by the Mac in the
  Keychain), writes chisel's auth file, which chisel 1.12.0 reloads by itself, and keeps its state in a named Docker
  volume. Without `RELAY_ADMIN_TOKEN` the container behaves exactly as today. The auth file is never empty (a sentinel
  user, §6.5), and `RELAY_REVOKED` in Dokploy revokes a client even if the volume is lost.
- **Migration:** one last Dokploy edit (paste three environment lines instead of two, Deploy). The static clients
  `office-pc` and `avi-mac` keep working; a dynamic client of the same name and role takes precedence, with its own
  password (none is ever copied, §6.5).
- **Stages:** P1 is everything above for compute nodes, plus the node-side root half of P2 (so that the one visit
  installs it). P2 is `mu compute invite --workstation`: a second laptop paired from an already paired Mac, with no visit
  to the PC (§10).

## 2. Decisions, and what was rejected

The lead's five decisions stand. Where this design refines one, the row says so.

| # | Decision | Here |
|---|---|---|
| 1 | One code; Mac `mu compute invite <node>` waits (with `--json` and a non-waiting mode); one PC command runs the node's root setup from the installed release and pairs | Kept. The PC visit is **two** commands, because the first (`mu update`) is what makes "the installed release" the one with `pair` in it; it is a no-op when auto-update already did it (§3.2). `--workstation` is stage P2 (§10) |
| 2 | A pairing service behind the same Traefik route, path-routed, admin token, dynamic clients with rules as strict as `entrypoint.sh`, static env clients keep working, a named volume | Kept. "Same compose service family" is realised as **the same container**: chisel forwards non-chisel requests to the service (`--backend`), so no Traefik label, Domains entry or second service is needed (§2.1) |
| 3 | id + secret; every key MAC'd; offline brute force infeasible; single use; TTL ~30 min; rate limits, lockout; stdlib Python on both machines; Go or Python on the relay | Kept: 20-bit id, 90-bit secret, HMAC-SHA-256, 30 min to claim, 60 min to answer, the Mac verifies within 24 h; lockout at 20 wrong claims (review finding 10); Go (§2.2) |
| 4 | The relay may learn relay credentials but must not be able to substitute keys; revocation removes the dynamic client | Kept. Removing or replacing a client also **restarts chisel**, because chisel 1.12.0 does not cut a removed user's established tunnels (§6.7, §15). `RELAY_REVOKED` revokes from Dokploy, without the Mac and without the volume (§6.5) |
| 5 | ≤ 3 commands at the PC, from the installed release, the clean restart, a final status; the manual path stays | Kept: 2 commands; the restart and the status are inside the second |

### 2.1 One container, two processes, rather than a second service

| Option | Verdict |
|---|---|
| **chisel in front, `mu-relayd` behind it on loopback (chosen)** | No change to Traefik, to the Domains entry or to the middleware. The tunnel path is byte for byte the S4-tested one. `mu-relayd` is chisel's parent, so it can restart chisel to cut a revoked client (§6.7). The auth file stays on the container's tmpfs. One image, one healthcheck (chisel sends `/health` to the backend, so the check covers both). Cost: two processes in one container, so `mu-relayd` must supervise chisel and pass signals (§6.8); a backend that is down answers 502 on `/mu/v1/` and fails the healthcheck |
| A second compose service `mu-pair` | Needs a second Domains entry (Path `/mu/v1`, another port: one more manual Dokploy setting, and its own middleware) or hand-written router labels that must match what Dokploy generates; needs the volume shared by two containers so chisel can read the auth file from it; cannot restart chisel without the Docker socket, so a revoked client keeps its established tunnels. Rejected |
| `mu-relayd` in front, proxying WebSockets to chisel | Puts new code in the path of every tunnel byte. Rejected |

### 2.2 Go, not Python, for the service

A static Go binary built from the standard library only (`net/http`, `crypto/sha256`, `crypto/subtle`, `crypto/rand`,
`encoding/json`, `os/exec`): no interpreter or packages in the image, memory-safe, one file to copy, `go.mod` with no
`require` (built with `GOPROXY=off`, so nothing is fetched). The relay never computes a MAC (it never has the secret), so
no code needs to be shared with the Python clients. Python would add about 40 MB of Alpine packages whose versions move
under the digest-pinned base, and an interpreter to the attack surface. Cost: a second language in the repo, and a
`golang` builder image pulled once on the server (about 300 MB of disk).

### 2.3 Rejected alternatives for the pairing itself

| Alternative | Why not |
|---|---|
| A PAKE (SPAKE2, CPace) with a short code (6–8 digits) | The right tool when the code must be short, but there is none in Python's standard library or Go's: it would mean hand-written elliptic-curve arithmetic in two languages, constant-time concerns, one more round trip, and a review we cannot get. The price of the simple design is 24 characters typed once per node. If that proves too long, the cheaper step is §14 Q3 (a memory-hard KDF), not a PAKE |
| The relay generates the code | Then the relay knows the secret and can substitute keys: the one property this design exists for |
| The PC shows a code, the owner types it on the Mac | The PC knows neither the relay's fingerprint nor the admin token, so it cannot open the invite; and on the Mac the owner would type it to Claude Code anyway |
| QR codes | The PC side is a PowerShell window and the Mac has no reason to have a camera pointed at it; nothing would read the code |
| Fingerprints compared by eye (today) | People skip it. Kept only as the fallback (§3.8) |
| Trust on first use through the tunnel (`--host-fingerprint`, job-layer §15 stage 3) | Still needs a fingerprint compared, and gives the PC nothing to authenticate the Mac's key with |
| Relay passwords derived from a seed, so nothing secret is on the volume | Couples every dynamic password to one more long-lived secret and still loses the client list with the volume. The server already holds every password in Dokploy's `.env`; storing them on the volume exposes nothing new (§12) |
| A stretched secret (scrypt) to shorten the code | Simpler without. 90 bits are enough unstretched (§4.3); see §14 Q3 |

## 3. User flows

Output below is what the commands print. `14:22`-style times are local. `NN` stands for the release's patch number.

### 3.1 Once, before the first invite (the owner, in Terminal on the Mac, not through Claude Code)

The admin token and the relay passwords must never pass through Claude Code's transcript, so these two steps are typed
by the owner (`gen-credentials.sh` already works this way):

```sh
cd ~/mu && git pull                                   # the release with pairing
relay/gen-credentials.sh admin-token                  # adds RELAY_ADMIN_TOKEN to ~/.config/mu/relay.env (once)
relay/gen-credentials.sh remove-client office-pc      # optional, recommended: the PC never received it (§11)
relay/gen-credentials.sh env                          # three lines: paste them into Dokploy's Environment tab, Save, Deploy
```

After the deploy (about 15 s):

```sh
curl -sS https://relay.martand.systems/mu/v1/          # {"ok":true,"service":"mu-relay","protocol":1,"pairing":true}
relay/gen-credentials.sh login-bundle avi-mac | mu relay login --stdin
```

```
relay https://relay.martand.systems (fingerprint OEc5…k=): pairing service protocol 1, store 3f9a1c0e (created 2026-11-20 10:02)
admin token, and this computer's relay client avi-mac, stored in the login Keychain
clients: avi-mac (workstation, static, ports 7001)
```

`mu relay login` checks the admin token (`GET /mu/v1/clients`) before it stores anything. Wrong token: exit 5
`relay-admin`, nothing stored.

### 3.2 Pairing a compute node: the office visit

**On the Mac** (the owner, or Claude Code with §3.3):

```
$ mu compute invite office-pc
invite 7K3M for compute node office-pc (relay port 7001), from avi-mac, valid until 14:52 (30 min)

At the office PC, open Terminal (Admin) and type these two lines:

  wsl -d mu-compute -u mu --cd ~ -- mu update
  powershell -NoProfile -ExecutionPolicy Bypass -File (wsl -d mu-compute -u mu --cd ~ -- mu node host-script) pair

When it asks for the pairing code, type:

  7K3M-Q9TD-4WXR-HN2C-PB8F-ZJ6R

Waiting for the PC. Ctrl-C stops waiting, not the invite: mu compute invite --wait 7K3M
  14:26:41  claimed by the PC (198.51.100.23); the code matched
  14:31:02  the PC's answer arrived: host key SHA256:Xq1…Rw, authenticated by the code
  14:31:05  this computer's relay client avi-mac may now reach port 7001
  14:31:19  office-pc answers through the relay: μ v2026.11.NN, runner active, memory limit enforced (2.1 s)
paired office-pc
```

The port is the lowest compute port the relay has free, or the one an existing `office-pc` client has; `--port N` sets
it. For a node this Mac has paired before (re-pairing, §3.5) the second PC line is
`powershell -NoProfile -ExecutionPolicy Bypass -File C:\Mu\host\mu-wsl2-host.ps1 pair` instead (§9.1 says why).

**At the PC**, in Terminal (Admin):

```
PS C:\WINDOWS\system32> wsl -d mu-compute -u mu --cd ~ -- mu update
μ v2026.11.NN is installed and is the default: nothing to do.
PS C:\WINDOWS\system32> powershell -NoProfile -ExecutionPolicy Bypass -File (wsl -d mu-compute -u mu --cd ~ -- mu node host-script) pair
mu WSL2 host: pair (μ v2026.11.NN, the scripts copied to C:\Users\avi\AppData\Local\Temp\mu-host-v2026.11.NN)

== What a process of the distro can reach of Windows now (before the change; saved in C:\Mu\logs\windows-reach-before.txt)
  FAIL reach.root ... windows-reach: 4 way(s) open

== Setting up the node (install -NoRestart)
  ...                                    (as checklist 2.5: ok for what exists, changed for the new steps)
Done: 34 change(s) (3 on Windows, 31 in mu-compute).

Pairing code (from the Mac): ****************************
  code accepted: this PC becomes compute node office-pc on relay port 7001 for avi-mac (until 14:52)

== Restarting the distro cleanly to apply the settings (about a minute)
  mu-wsl-shutdown.exe --stop-wsl as SYSTEM: exit code 0
  starting mu-compute again through the startup task

== Pairing
  relay credential written: relay user office-pc, https://relay.martand.systems, port 7001
  avi-mac's key authorised: SHA256:b7T…9Q (authenticated by the code)
  host key SHA256:Xq1…Rw sent to avi-mac (authenticated by the code)
  mu-tunnel.service: connected
  waiting for avi-mac to confirm ... confirmed: avi-mac reached this PC through the relay
paired with avi-mac

== Status
  PASS  linux.keys       avi-mac SHA256:b7T…9Q
  ...                                    (every check of `status`, as checklist 2.7 and 2.11)
  PASS  linux.ready      ready for jobs: yes

paired with avi-mac · ready for jobs: yes
```

Exit 0. The `pair` action is safe to run again: it repeats what is missing and nothing else (§9.4).

### 3.3 Claude Code on the Mac

A command that waits for the owner's walk to the PC does not fit one tool call, so Claude uses the non-waiting form:

```sh
mu compute invite office-pc --no-wait --json
```
```json
{"ok": true, "invite": "7K3M", "node": "office-pc", "kind": "node", "port": 7001, "code": "7K3M-Q9TD-4WXR-HN2C-PB8F-ZJ6R",
 "expires": "2026-11-20T14:52:00+01:00", "pc_commands": ["wsl -d mu-compute -u mu --cd ~ -- mu update",
 "powershell -NoProfile -ExecutionPolicy Bypass -File (wsl -d mu-compute -u mu --cd ~ -- mu node host-script) pair"],
 "wait_command": "mu compute invite --wait 7K3M --json --timeout 540"}
```

then repeats `mu compute invite --wait 7K3M --json --timeout 540` while it exits 10 (`still-waiting`, with `state` and
the last event). The final object is `{"ok": true, "node": "office-pc", "state": "finished", "host_key_fingerprint":
…, "hello": {…}}`. Claude shows the owner the code and the two PC lines, and nothing else secret is ever in its output.

The code itself does pass through Claude's transcript this way. That is accepted (it is single use, worthless once used
or after 60 minutes, §12), and `--show-code dialog` keeps it out: the code then appears only in a macOS dialog
(`osascript display dialog`) and the JSON carries `"code": null` (§14 Q2).

### 3.4 Errors, as each side prints them

Every Mac error is the job layer's JSON shape (`exit`, `error`, `message`, `next`; job-layer §4.2). The PC prints
`message` and `next` and exits with the same code from `mu-wsl2-host.ps1`. Both are the client's **own** text, chosen
by the relay's `error` slug and `state`: the relay's `message` is never shown, logged into Claude's context or
forwarded (review finding 5). Names, addresses and times from the relay are shown only after they parse (the name rule,
an IP literal, an integer), with control characters stripped.

| Situation | Mac (`mu compute invite`) | PC (`pair`) | Exit |
|---|---|---|---|
| Typo in the code (checksum) | — | `the code has a typo (its check characters do not match): compare it with the Mac's screen and type it again` — asked again, 3 times, then exit | 2 `code-typo` |
| Wrong secret with a valid checksum, or an id the relay does not hold (the two get the same answer, review finding 10) | event `a wrong code was tried for invite 7K3M (1 of 20)` | `the relay does not accept this code: check it on the Mac's screen` — asked again | 5 `wrong-code` |
| 20 wrong codes | `invite 7K3M is locked: 20 wrong codes were tried. Make a new one: mu compute invite office-pc` | `invite 7K3M is locked (too many wrong codes): ask for a new code on the Mac` | 5 `invite-locked` |
| Expired (30 min unclaimed; 30 min claimed but not answered; 15 min answered but not confirmed) | `invite 7K3M expired at 14:52 before the PC claimed it` (or `… answered it`) and the next command | `invite 7K3M expired at 14:52: make a new one on the Mac (mu compute invite office-pc)` | 9 `invite-expired` |
| Already used (claimed by another machine, or finished) | the invite is `contested`; with the PC's contest (§12.1): `invite 7K3M: another machine used the code first; office-pc was not paired (or was un-paired): make a new invite` | `SOMEONE ELSE USED THIS CODE FIRST. The relay client it made has been removed and avi-mac has been told. Make a new invite on the Mac.` (the PC posts its contest) | 5 `invite-used` |
| The relay changed the offer (MAC fails at the PC) | event `the PC refused the invite: it was changed on the way` and exit 5 | `the invite was changed on the way (the relay or the network): nothing was set up from it. Tell the Mac's owner; do not retry this code` (the PC posts `abort`) | 5 `pair-integrity` |
| The relay changed the answer (MAC fails at the Mac) | `the PC's answer was changed on the way (the relay or the network): nothing was paired, the relay client office-pc was removed` | `avi-mac refused this PC's answer (it arrived changed): nothing was paired` | 5 `pair-integrity` |
| Relay unreachable | at creation: `the relay https://relay.martand.systems does not answer (…): nothing was created`; while waiting: `relay unreachable, trying again` every 5–30 s until the deadline | `the relay does not answer (…): run the same line again in a minute` (no time: the PC cannot know the invite's deadline before a successful claim, review finding 9) | 4 `relay-unreachable` |
| Relay without pairing (old image, or no `RELAY_ADMIN_TOKEN`) | `the relay has no pairing service (GET /mu/v1/ answered 404, or 503 not-configured): see docs/design/pairing.md §11` | `the relay has no pairing service: use the manual path (first-job-checklist.md)` | 5 `pairing-unsupported` |
| Admin token refused | `the relay refused this computer's admin token: mu relay login again (relay/gen-credentials.sh login-bundle …)` | — | 5 `relay-admin` |
| Node already paired on this Mac | `office-pc is already paired: to pair it again (new host key or new key here): mu compute invite office-pc --replace` | — | 2 `exists` |
| A relay client of that name exists and `--replace` was not given | `the relay already has a client office-pc (dynamic, created 2026-11-20 by invite 7K3M): --replace, or another name` | — | 2 `client-exists` |
| Port taken by another compute client | `relay port 7002 belongs to compute client lab-pc: leave --port out, or pick a free one` | — | 2 `port-taken` |
| The PC runs an older release | — | first line fails: `mu: unknown command 'host-script'` (and PowerShell: `the argument '' to -File does not exist`). Next: run `mu update` (the first line) | — |
| Paired, but the Mac cannot reach the node | `paired office-pc, but it does not answer through the relay yet: node-offline (…). Next: mu compute status office-pc` | `avi-mac has the keys but could not reach this PC through the relay: node-offline` + `tunnel status` | 4 |
| The Mac does not confirm within about 2 minutes of the PC's answer (asleep, offline, Claude stopped) | `mu compute invite --wait 7K3M` later (up to 24 h after creation) verifies, says hello and finishes | `paired with avi-mac (not yet confirmed: avi-mac confirms when it next runs mu compute invite --wait 7K3M)` and `ready for jobs: yes`. Nothing is undone (review finding 3) | 0 |
| `--wait` timeout passed, still waiting | `still waiting (state claimed, last event 14:26)`, next `mu compute invite --wait 7K3M` | — | 10 `still-waiting` |

### 3.5 Re-pairing

- **The PC was reinstalled** (new host key) or **the Mac lost its folder**: `mu compute invite office-pc --replace`. The
  Mac keeps its old `compute/office-pc/` aside until `finish` and puts it back on any failure. The relay keeps the old
  client until the new answer arrives; then it replaces it and restarts chisel at once, so that no session of the old
  credential survives and holds the node's port while the new tunnel tries to bind it (§6.7). On abort or expiry it puts
  the old client back. The Mac keeps its key for the node (the PC's `authorize` replaces the line of the
  same name); `--new-key` makes a new one.
- At the PC the line is `C:\Mu\host\mu-wsl2-host.ps1 pair`: install is a no-op there, so no restart; the code, the
  credential, the key, the answer and the wait take under a minute. Without the Windows side
  (`wsl -d mu-compute -u root -- mu-wsl2-distro pair`) the same happens from a root shell of the distro.

### 3.6 A second workstation

Stage P2, §10: on the paired Mac `mu compute invite --workstation bob-mbp`, on the new laptop `mu compute join`, with the
same code. No visit to the PC.

### 3.7 Removing and revoking

| Command | What it does |
|---|---|
| `mu compute remove office-pc` | As today: this Mac forgets the node (folder, Keychain item, ControlMaster). Prints the revoke command for the PC |
| `mu compute remove office-pc --relay` | Also deletes the relay client `office-pc` (the node can no longer reach the relay: decommissioning). Asks for the admin token from the Keychain |
| `mu relay remove <name>` | Deletes a dynamic client, or masks a static one with a tombstone (§6.5). chisel is restarted: every session of that client ends now, every other client reconnects within about 2 s (§6.7). A workstation removed here can reach no node's `sshd` any more: they listen on loopback behind the relay |
| `mu relay clients` | The clients (static, dynamic, tombstoned), roles, ports, who created them, never a password |
| `mu compute revoke office-pc bob-mbp` (P2) | Removes bob-mbp's ssh key on the node by a signed request (§10) |

### 3.8 The fallback: the manual path

Unchanged and still tested: `relay/gen-credentials.sh`, static clients in `RELAY_CLIENTS`, `mu-wsl2-distro tunnel
credential` on stdin, `host-key`, `authorize`, `mu compute add --host-key … --relay-password-stdin`, as in
`first-job-checklist.md`. It is what to use when the relay has no pairing service, or for a node whose operator should
see the fingerprints.

## 4. The code

### 4.1 Format

```
7K3M-Q9TD-4WXR-HN2C-PB8F-ZJ6R
└id┘ └───── secret, 18 ─────┘└check: the last 2 symbols
```

- **Alphabet:** Crockford base32, `0123456789ABCDEFGHJKMNPQRSTVWXYZ` (no I, L, O, U). Input is case-insensitive;
  `O`→`0`, `I` and `L`→`1`; spaces and `-` are ignored; a leading U+FEFF and trailing CR and LF are dropped first
  (Windows PowerShell 5.1 writes `CR LF` after a string piped to a native program, review finding 6); any other
  character is a typo. A unit test feeds the exact bytes `b"\xef\xbb\xbf7K3M-…-ZJ6R\r\n"`.
- **id:** 4 symbols, 20 bits, made by the Mac with `secrets.choice`. The relay refuses an id it holds or held in the last
  24 hours (409; the Mac draws another, 3 tries).
- **secret S:** 18 symbols, 90 bits, `secrets.choice`; as bytes, the 90 bits big-endian in 12 bytes (the top 6 bits
  zero).
- **check:** 2 symbols, the first 10 bits of `SHA-256("mu-pair-code/1:" + id + secret)` (ASCII, upper case). It is
  computed and checked only on the two machines; it never reaches the relay.
- **Display:** groups of four separated by `-`, the check last. 24 symbols.

### 4.2 Why these sizes

| Part | Bits | Why |
|---|---|---|
| id | 20 | Only a lookup key. It is not secret: it identifies the invite to the relay, which rate-limits and locks it. 20 bits make a collision among the few live and retained invites rare (the Mac retries) and guessing a live one by hammering the relay slow (§6.4: at 20 requests a minute per address that is weeks per address) |
| secret | 90 | The only thing protecting the keys against the relay. Sized for the offline attack of §4.3 over 24 hours (the Mac's late verification, review finding 3) with a margin of 2^27 |
| check | 10 | Catches 1023 in 1024 typos locally, before anything is sent; the rest count as one wrong code at the relay (19 more allowed). Not a linear code, so a single substitution is not guaranteed to be caught, only with that probability (review finding 13: a mod-32 check symbol was considered and not taken) |

### 4.3 The offline attack, sized

The party that sees the MACs is the relay (or whoever controls the server). To substitute a key it must produce a valid
MAC under a key derived from S **before** the honest side verifies. It sees the first MAC (the offer) when the invite is
created. The two verifications that matter are bounded by the verifier's **own** clock, never by R's:

- the PC verifies the offer within 60 minutes of its first use of the id (it records that time, review finding 9), so
  the window against the PC is the owner's walk to the PC plus 60 minutes;
- the Mac verifies the answer at most 24 hours after it created the invite (late verification after a sleep, review
  finding 3).

Each guess of S costs one HMAC-SHA-256 (≈ 4 SHA-256 compressions) checked against the offer's MAC, or one SHA-256 more
against the claim hash. A generous attacker: 10^4 GPUs at 10^10 guesses per second each (better than any published
HMAC-SHA-256 rate) tries 2^58.3 values in an hour and 2^62.9 in 24 hours. Against a 90-bit S that is a chance of 2^-31.7
for the PC's window and 2^-27.1 (1 in 150 million) for the Mac's, per pairing. After the invite ends a found S is
useless: every key it protected has been verified and pinned, and the relay refuses anything more for that id.

## 5. Protocol

Parties: **I** the inviter (the paired Mac), **C** the claimer (the PC's root side, or in P2 a new laptop), **R** the
relay service. I holds the admin token; C proves knowledge of the code; R stores and forwards and can verify neither MAC.

### 5.1 Keys

```python
LABEL = "mu-pair/1"
def enc(*fields):                       # unambiguous: each UTF-8 field with its 4-byte big-endian length
    return b"".join(len(b).to_bytes(4, "big") + b for b in (f.encode("utf-8") for f in fields))
def kdf(S, label, iid):
    return hmac.new(S, enc(LABEL, label, iid), hashlib.sha256).digest()

K_claim = kdf(S, "claim", id)              # C presents it to R; R stores SHA-256(K_claim)
K_ic    = kdf(S, "mac inviter->claimer", id)
K_ci    = kdf(S, "mac claimer->inviter", id)
claim_token = K_claim.hex()                # 64 hex
claim_hash  = hashlib.sha256(K_claim).hexdigest()
```

HMAC with distinct labels is a PRF, so R learning `K_claim` (it does, at the claim) tells it nothing about `K_ic` or
`K_ci`. Every MAC is the full 32-byte HMAC-SHA-256 in hex, compared with `hmac.compare_digest`.

### 5.2 Messages and exactly what each MAC covers

Each message is carried as a flat JSON object of string members (R stores and returns it unchanged; it never computes or
checks a MAC). Member names are the field names below; `kind` and `id` are members too. Duplicate members are refused
on all three sides (review finding 13).

Public keys are normalised to `ssh-ed25519 <base64>` (no comment); the base64 must decode to the SSH wire form of type
`ssh-ed25519` with a 32-byte key, anything else is refused. Times are Unix seconds as decimal strings. Ports are decimal
without a leading zero. Each message travels as a JSON object whose members are exactly these fields (unknown or missing
members: refused); the MAC is recomputed from the fields in this order.

**OFFER** (I → C, key `K_ic`) for a compute node:

```
offer = enc(LABEL, "offer", id, "node",
            relay_url,            # e.g. https://relay.martand.systems, no trailing slash
            relay_fingerprint,    # chisel's 44-character SHA-256 fingerprint
            node_name,            # = the node's relay user and its name in run records
            port,                 # its relay port, "7001"
            inviter_name,         # I's workstation name = its relay user, e.g. avi-mac
            inviter_key,          # I's public key for this node
            created, expires,     # I's clock, for display and a skew-tolerant check
            mu_version)           # I's μ release, e.g. v2026.11.8
offer_mac    = HMAC(K_ic, offer)
offer_digest = SHA-256(offer).hex()
```

**ANSWER** (C → I, key `K_ci`):

```
answer = enc(LABEL, "answer", id, offer_digest, "node",
             node_name,           # repeated: C states what it became
             host_key,            # the node's /etc/mu/ssh_host_ed25519_key.pub
             mu_version)          # C's μ release
answer_mac = HMAC(K_ci, answer)
```

**CONFIRM** (I → C, key `K_ic`):

```
confirm = enc(LABEL, "confirm", id, offer_digest, SHA-256(answer).hex(),
              outcome)            # one of: ok, unreachable:node-offline, unreachable:relay-credential,
                                  #   unreachable:host-key, unreachable:timeout (a fixed list, review finding 13)
confirm_mac = HMAC(K_ic, confirm)
```

What this binds: the invite id and the protocol version in every MAC; the node's name and port, the relay URL and
fingerprint and I's public key in the offer; the host key in the answer, tied to that exact offer by its digest; the
confirm tied to both. The node's relay password is not MAC'd: C chooses it and gives it to R only, R checks it anyway,
and a wrong one can only deny service (§12).

**CONTEST** (C → I, key `K_ci`; review finding 4), sent by a claimer whose claim was refused as `invite-used`:

```
contest = enc(LABEL, "contest", id, offer_digest)
contest_mac = HMAC(K_ci, contest)
```

**No downgrade.** There is one protocol, `mu-pair/1`, and `LABEL` fixes it inside every MAC; a client never accepts a
lower version because R's `GET /mu/v1/` says so.

**Replay and reflection.** Each direction has its own key, and each message type its own label inside the MAC, so no
MAC is valid as another message or in the other direction. A message from another invite fails because the id and S
differ. Within one invite each type is accepted once in R's state machine, and each side verifies exactly one of each.

### 5.3 What each side checks

C, on the offer: the MAC; `kind` is `node`; `relay_url` is `https://` and equals the URL C contacted after normalising
(host in lower case, no default port, no trailing slash; §9.2); `relay_fingerprint` is 43 base64 characters and `=`;
`node_name` and `inviter_name` match `[a-z0-9][a-z0-9-]{0,31}`; `port` is 7001–7999; `inviter_key` is one ed25519 key;
`now ≤ expires + 600` (WSL's clock may lag after a sleep); C's own record of its first use of this id is less than 60
minutes old (§4.3); and R's view (`invite.name`, `invite.port`, `invite.inviter`) equals the offer's. Any failure:
`abort` with reason `integrity`, exit 5, nothing set up.

I, on the answer: the MAC, `offer_digest` equal to its own, `node_name` equal, `host_key` one ed25519 key, the invite
not `contested`, and its own clock not past `created + 86400` (the bound of §4.3 is enforced by I, not by R). Any
failure: `abort` (R removes the client and restores what it replaced), exit 5, nothing kept. On a contest (the invite's
`contest` member set, §12.1): I verifies `contest_mac`; if it verifies, I refuses to finish, or un-pins the node if it
finished less than 24 hours ago, and says so.

C, on the confirm: the MAC and both digests. A confirm that fails is reported (`the confirmation did not verify`, exit 5)
but undoes nothing: by then the keys on both sides were each verified on their own.

### 5.4 The relay's state machine

```
               claim (first right token; the claimer's session is stored)
   created ─────────────────────► claimed ──answer──► answered ──finish──► finished
      │ │                             │                   │ │
      │ └─ 20 wrong claims ─► locked  │                   │ └─ created+24 h ─► unconfirmed (client kept)
      │                               │                   │
      └──── abort / expires ──────────┴── abort / ────────┴──► aborted | expired
                                          created+60 min
```

| State | Entered by | R's deadline | Allowed next |
|---|---|---|---|
| `created` | `POST /invites` (admin) | `expires` = created + `ttl_s` (default 1800, at most 3600) | claim → `claimed`; wrong claims (20 lock it); abort; expiry → `expired` |
| `claimed` | the first claim with the right token | `answer_by` = created + 3600 | answer (with the session) → `answered`; a claim with the same session resumes; a claim with the right token and **no or another session** is refused (`invite-used`) and sets `contested`; abort; expiry → `expired`. Wrong claims are counted but do not lock: the session holder has proved the code, and locking would only let a guesser kill its pairing |
| `answered` | answer | `confirm_by` = created + 86400 | accept (admin; swaps a replaced client, review finding 8); finish (admin) → `finished`; abort; at `confirm_by` → `unconfirmed` |
| `unconfirmed` | `confirm_by` passed | — | finish (a Mac whose clock is a little behind); abort (removes the client) |
| `finished`, `aborted`, `expired`, `locked` | | — | none, except a contest (§12.1) |

Every invite record, terminal or not, is kept until 24 hours after it reached a terminal state (or `created` + 48 h for
an answered one) and then purged; its id cannot be reused while the record exists. The `version` counter of an invite
goes up at every change, and the long polls wait on it.

**Sessions** (review finding 4, with one change): the claimer **chooses** its session (32 random bytes, base64url, 43
characters), saves it, and only then sends the claim with it. R stores its SHA-256 at the first right claim. A claim
that is retried because its answer was lost therefore carries the same session and resumes, instead of being refused as
a second claimer. A right claim with another session never replaces the stored one.

**Side effects:**

- `answer`, new name: R creates the dynamic client (`role` from `kind`, the claimer's password, the invite's port or
  ports, `via: invite <id>`) and grants the port to the inviter (§6.5). No restart.
- `answer`, a name that exists (`replace: true` at creation; a static or a dynamic client of the same role): R keeps the
  password as `pending` and changes nothing yet. `accept` (the Mac, after verifying) swaps the client in, grants the port
  and restarts chisel (§6.7: the old credential's sessions would keep the port's listener). `accept` on an invite that
  created a new name is a no-op that answers `ok`.
- `abort` or a contest after the client was made: R removes the client this invite made (only while it is still the one
  this invite made: `via` matches), restores what it replaced (a dynamic entry is put back; a static one comes back by
  itself), removes the grant it added and restarts chisel. A `pending` password is dropped.
- `finished`: nothing more. `expired` from `claimed`: nothing to undo (no client was made before the answer).
- `unconfirmed`: nothing is removed (review finding 3). The Mac decides by its own clock.

## 6. The relay service, `mu-relayd`

### 6.1 Process and configuration

`relay/entrypoint.sh` keeps all the checks it has, and applies `RELAY_REVOKED` (optional, comma-separated names) to the
static list in both modes. Then:

- `RELAY_ADMIN_TOKEN` unset or empty: **exactly today's path** (`exec chisel server … --authfile users.json`).
- Set: it must be `mra_` followed by 43 to 128 characters of `A-Za-z0-9_-` (exit 64 otherwise, the value never printed).
  The entrypoint writes the parsed static clients to `/run/relay/static.tsv` (0600; `role<TAB>name<TAB>password<TAB>ports`
  per line, the entries it would have written to `users.json`, revoked names left out) and execs `env -i PATH=…
  RELAY_KEY=… RELAY_ADMIN_TOKEN=… RELAY_KEEPALIVE=… RELAY_REVOKED=… /usr/local/bin/mu-relayd`. `RELAY_CLIENTS` stays
  required by `compose.yaml`, as today.

`mu-relayd`:

1. reads the token, the key and the settings from its environment and clears its environment;
2. loads the store from `/var/lib/mu-relay` (§6.6). A directory it cannot write (no volume, or one owned by root) is
   treated like a damaged store: the static clients keep working and the pairing and admin endpoints answer 503
   `store-unwritable`, so a volume mistake never takes the tunnels down;
3. deletes every dynamic client, grant and tombstone named in `RELAY_REVOKED`, reads `static.tsv`, merges (§6.5),
   checks and writes `/run/relay/users.json`;
4. listens on `127.0.0.1:8081` (`ReadHeaderTimeout 5 s`, `ReadTimeout 10 s`, `WriteTimeout 40 s`, `IdleTimeout 60 s`,
   `MaxHeaderBytes 8 KiB`);
5. starts chisel as its child: `chisel server --host 0.0.0.0 --port 8080 --reverse --authfile /run/relay/users.json
   --keepalive $RELAY_KEEPALIVE --backend http://127.0.0.1:8081` with the environment `PATH`, `CHISEL_KEY_FILE=<key>`,
   `CHISEL_SHUTDOWN_GRACE=2s` and nothing else; chisel's stdout and stderr go to the container log unchanged.

### 6.2 The HTTP API (protocol 1)

Reached as `https://relay.martand.systems/mu/v1/…`: chisel passes every request that is not a chisel WebSocket to
`mu-relayd`. Bodies are JSON objects (`Content-Type` is not checked), at most 16 KiB, decoded strictly: unknown
members, duplicate members, trailing data and wrong types are refused (400 `bad-request`). Every answer is JSON with
`Cache-Control: no-store`: `{"ok": true, …}` or `{"ok": false, "error": "<slug>", "message": "<English>"}`. Clients act
on `error` only and never display `message` (§3.4). A request with `Upgrade` is refused (400). Times are Unix seconds
(integers), ports integers.

**Authentication.**

| Kind | How | Checked as |
|---|---|---|
| admin | `Authorization: Bearer <RELAY_ADMIN_TOKEN>` | SHA-256 of both, `subtle.ConstantTimeCompare` |
| claim | the member `claim`: `K_claim` in 64 lower-case hex | SHA-256 of the decoded 32 bytes against the invite's `claim_hash`, constant time |
| session | `Authorization: Mu-Session <session>` (43 base64url characters, chosen by the claimer, §5.4) | SHA-256 against the stored hash, constant time |

**Endpoints.**

| Method and path | Auth | Request body | Answer (`ok: true` plus) |
|---|---|---|---|
| `GET /health` | none | | `OK\n` (text) while chisel runs, also with a damaged store; 503 otherwise |
| `GET /mu/v1/` | none | | `service: "mu-relay"`, `protocol: 1`, `pairing: true` (false when the store is unusable), `store: "ok" \| "unwritable" \| "damaged"` |
| `POST /mu/v1/invites` | admin | `id, kind, name, port` (node) or `ports` (workstation), `inviter, claim_hash, offer, offer_mac, ttl_s, replace` | 201, `invite` (the view below) |
| `GET /mu/v1/invites` | admin | | `invites`: every invite record held, as views without `offer`/`answer` |
| `GET /mu/v1/invites/{id}?wait=W&since=V` | admin | | `invite`. With `wait` (1–25 s) it answers as soon as `invite.version` > `V`, else after `W` seconds with the same view |
| `POST /mu/v1/invites/{id}/accept` | admin | `{}` | `invite`. Only in `answered`/`unconfirmed`; swaps a replaced client (§5.4) |
| `POST /mu/v1/invites/{id}/finish` | admin | `confirm, confirm_mac` | `invite` (state `finished`). Only from `answered`/`unconfirmed`, and only after `accept` when the invite replaces a client (409 `not-accepted`) |
| `POST /mu/v1/invites/{id}/abort` | admin, or the session before `answered` | `reason`: `integrity`, `cancelled`, `contested`, `timeout` or `other` | `invite` (state `aborted`) |
| `POST /mu/v1/invites/{id}/claim` | claim | `claim, session` | `resumed` (bool), `invite`, `offer`, `offer_mac` |
| `GET /mu/v1/invites/{id}/offer` | session | | `invite`, `offer`, `offer_mac` |
| `POST /mu/v1/invites/{id}/answer` | session | `answer, answer_mac, password` | `invite`. Only in `claimed`; the same answer sent again (a retry) answers the same |
| `GET /mu/v1/invites/{id}/result?wait=W&since=V` | session | | `invite`, `confirm`, `confirm_mac` (null until finished) |
| `POST /mu/v1/invites/{id}/contest` | claim | `claim, contest, contest_mac` | `invite` (§12.1). Any state while the record is held |
| `GET /mu/v1/clients` | admin | | `store_id, store_created, generation, clients[], tombstones[], revoked[], shadowed[]` |
| `GET /mu/v1/clients/{name}?secret=1` | admin | | `client`; with `secret=1` and a dynamic client, its `password` too (the Mac's backup, §8.4) |
| `PUT /mu/v1/clients/{name}` | admin | `role, password, ports` | 201 or 200, `client`. Creates or replaces a dynamic client (restore, manual add); restarts chisel if it replaced one |
| `DELETE /mu/v1/clients/{name}` | admin | | `removed: "dynamic" \| "tombstone"`. A dynamic client is deleted (and the static entry of that name, if any, comes back); a static one gets a tombstone. Restarts chisel |
| `PUT /mu/v1/grants/{name}` | admin | `ports` | `client`. Sets the extra ports of a static workstation (restore); `[]` removes the grant |
| `DELETE /mu/v1/tombstones/{name}` | admin | | the static client comes back |
| `GET /mu/v1/events?n=N` | admin | | `events`: the last N (≤ 500) lines of the event log |
| `POST /mu/v1/store/reset` | admin | `{}` | only with a damaged store: moves both files aside and starts a new store |

**The invite view** (`invite`): `id, kind, name, port` or `ports, inviter, replace, state, contested, accepted, client`
(`none`, `pending`, `created`, `removed`), `created, expires, answer_by, confirm_by, version, wrong_claims, claims`
(`[{at, from, outcome}]`, outcome `first`, `resumed` or `refused`), `events` (`[{at, event, from}]`, at most 64),
and for the admin also `offer, offer_mac, answer, answer_mac, confirm, confirm_mac, contest, contest_mac` (null until
present). The session holder sees `id, kind, name, port(s), inviter, state, contested, accepted, client, created,
expires, answer_by, confirm_by, version`.

**Errors** (status, `error`): 400 `bad-request`, 400 `upgrade-refused`; 401 `admin-required`, 401 `session-required`;
403 `admin-refused`, 403 `wrong-code` (a wrong claim token **and** an unknown id: the same answer, review finding 10),
403 `bad-session`; 404 `not-found` (an unknown path, or an unknown id or client for the admin), 404 `no-such-client`;
409 `id-taken`, `invite-used`, `client-exists`, `port-taken`, `state`, `not-accepted`, `last-client`, `revoked`,
`too-many-invites`, `too-many-clients`, `inviter-unknown`, `role-mismatch`; 410 `invite-expired`, `invite-aborted`,
`invite-locked`; 413 `too-large`; 429 `rate-limited` (with `Retry-After`); 500 `internal`; 503 `store-unwritable`,
`store-damaged`. A right claim token on a terminal invite answers its state (410, or 409 `invite-used` when finished,
unconfirmed or held by another session). The 409 `invite-used` answer to a **right** claim token also carries `offer`
and `offer_mac`: the refused claimer proved the code, checks the offer's MAC and needs its digest for its contest
(§5.2, §12.1). A wrong token never sees it.

### 6.3 Checks at invite creation

- `id`: 4 symbols of the alphabet (upper case; R does not apply the aliases); not held (409 `id-taken`).
- `kind`: `node` or `workstation`. `name`, `inviter`: `^[a-z0-9][a-z0-9-]{0,31}$`. `port` (node) 7001–7999; `ports`
  (workstation) 1–16 distinct ports of that range, each one a port the inviter itself has.
- `claim_hash`, `offer_mac`: 64 lower-case hex. `ttl_s`: 60–3600, default 1800.
- `offer`: an object of 1–20 string members, names `^[a-z_]{1,32}$`, values at most 8 KiB of printable ASCII and
  newlines. R reads it but cannot check its MAC: `offer.kind`, `offer.id` and `offer.inviter_name` must equal the request,
  and `offer.node_name`/`offer.port` (node) or `offer.new_name`/`offer.ports` (workstation, `7001+7002`) too.
- `inviter` is a workstation client, static or dynamic (409 `inviter-unknown`), not revoked.
- `name`: not revoked; not `inviter`; no other compute client has the port (409 `port-taken`); a client of this name
  exists only with `replace: true` (409 `client-exists`) and the same role (409 `role-mismatch`); no other live invite
  is for this name.
- Live invites (not terminal): at most 8 in all and 4 per inviter (409 `too-many-invites`). Dynamic clients: at most 64.

### 6.4 Rate limits and lockout

The client address is the second-to-last entry of `X-Forwarded-For` (all such headers joined): Traefik replaces what a
client sends with the client's address (no `trustedIPs` in Dokploy's configuration) and chisel's proxy appends Traefik's
(§15). With fewer than two entries the connection's own address is used. It must parse as an IP address; an IPv6
address counts by its /64. The address is used for limits and display only, never for a security decision (review
finding 4). A container on `dokploy-network` that talks to port 8080 directly can choose its address; only the limits
that are not per address bind it.

| What | Limit |
|---|---|
| Requests without admin auth (claim, contest, session endpoints, `GET /mu/v1/`) | 30 per minute per address (bucket of 30); no global bucket (review finding 10) |
| Wrong claims | 20 per invite while `created` → `locked`; 10 per address in 15 min → that address gets 429 on claims and contests for 15 min |
| Failed admin auth | 10 per address in 15 min → 429 on admin endpoints for that address for 15 min |
| Admin requests | 240 per minute in all |
| Per-address table | at most 4096 addresses; the least recently seen is dropped |
| Long polls | at most 4 open per invite; `wait` ≤ 25 s (Traefik's read timeout is 60 s) |
| Live invites | 8 in all, 4 per inviter |
| Clients | 64 dynamic clients |

Traefik's `mu-relay-inflight` (32 connections at a time per address) applies to `/mu/v1/` as to the tunnels: same router.

### 6.5 Clients: static, dynamic, grants, revoked, and the auth file

- **Static** clients come from `RELAY_CLIENTS` (via `static.tsv`); `mu-relayd` never changes them and never copies
  their passwords (review finding 2).
- **Dynamic** clients are in the store, each with the password its claimer chose or its `PUT` gave. A dynamic client may
  take a static client's name only with the same role (an invite with `replace`, or `PUT`); it then replaces that static
  entry in the auth file (`overrides_static`).
- **Grants** give a static workstation more ports: `grants: {"avi-mac": [7002]}`. The auth file's rule for `avi-mac` is
  its static ports plus its grant, with its static password. A grant whose static client is gone is ignored. A dynamic
  workstation's extra ports go into its own `ports`.
- **Revoked** (`RELAY_REVOKED`, Dokploy): the entrypoint drops those names from the static list; at every start
  `mu-relayd` deletes dynamic clients, grants and tombstones of those names; the API refuses to create them (409
  `revoked`). This is the kill switch that survives a lost volume, and §12's stolen-Mac step.
- **Tombstones** (`DELETE` of a static client) remove a static name from the auth file. They live in the store, so a
  lost volume brings the client back; `mu relay remove` of a static client therefore prints the `RELAY_REVOKED` line to
  add at the next Dokploy edit.
- **Deleting a compute client** removes its port from every grant and every dynamic workstation's ports (review
  finding 11), so a reused port never gives an old workstation the new node's `sshd`.
- **When a Dokploy edit makes static and dynamic disagree** (review finding 11): a dynamic client and a static one of
  the same name but another role, or a static compute client on a dynamic compute client's port: the dynamic client
  wins, the static entry is left out of the auth file and listed in `shadowed` by `GET /clients` (and in the log at
  every start).
- **The sentinel** (review finding 1): the auth file always holds the user `_mu-sentinel` (a name no client can have:
  `_` is outside the name alphabet) with a password drawn at each start and the rule `^-$`, which no chisel address
  (`host:port`, `R:host:port`) can match. chisel turns authentication off only when its user list is empty
  (`server.go` `authUser`); with the sentinel it never is.
- **The rules** are exactly `entrypoint.sh`'s:

  | Role | Rule (JSON string) |
  |---|---|
  | compute, port P | `^R:127\\.0\\.0\\.1:P$` |
  | workstation, ports P1+P2 | `^127\\.0\\.0\\.1:(P1\|P2)$` |

  Passwords `^[A-Za-z0-9_-]{24,128}$`, names `^[a-z0-9][a-z0-9-]{0,31}$`, ports 7001–7999 (anchored Go regexes and
  integer ports). A Go test compares the auth file for static-only input with the entrypoint's `users.json` for the same
  `RELAY_CLIENTS`.
- **The check before every write** (review finding 1): the exact bytes about to be renamed into place are decoded again
  (duplicate members refused) and must hold the sentinel (so chisel's user list is never empty; the sentinel alone
  authenticates nobody, which is where a relay whose every static client is revoked starts); every name is the sentinel or
  matches the name rule and appears once; every rule list has one rule of one of the two shapes above with ports
  7001–7999; no compute port twice. On failure nothing is written, the old file stays, the change is rolled back and the
  request answers 500 `internal`. Removing the last real client is refused before that (409 `last-client`).

### 6.6 Storage on the volume

`/var/lib/mu-relay/` (the named volume, owner 65532, 0700):

```
state.json        the store (0600)
state.json.prev   the previous generation (0600)
events.log        one JSON line per event, no secrets (0600); cut to its last 512 KiB when it passes 1 MiB
```

```json
{"format": 1, "store_id": "3f9a1c0e7d2b4a55", "created": 1795000000, "generation": 17,
 "clients": {"office-pc": {"role": "compute", "password": "…", "ports": [7001], "created": 1795000100,
                           "via": "invite 7K3M"}},
 "grants": {"avi-mac": [7001]},
 "tombstones": {"old-laptop": {"removed": 1795000200}},
 "invites": {"7K3M": {"kind": "node", "name": "office-pc", "port": 7001, "inviter": "avi-mac", "state": "answered", …}}}
```

**Writes** (one mutex owns the store; every change goes through it, review finding 11): marshal; write
`state.json.tmp` (0600); `fsync`; `link(state.json, state.json.prev.tmp)` and `rename` it over `state.json.prev`;
`rename(state.json.tmp, state.json)`; `fsync` the directory. `state.json` exists at every point. Only then is the auth
file rewritten and the request answered.

**Loading:** `state.json`, else `state.json.prev`. Both missing: a new store (new `store_id`, event `store-created`).
`state.json` unreadable but `.prev` good: `.prev` (logged). Both present and unreadable: `store-damaged`: R starts
chisel with the static clients only, answers 503 `store-damaged` on every pairing and admin endpoint except
`POST /mu/v1/store/reset`, which moves both files to `state.json.damaged-<time>` and starts a new store, and keeps
`/health` at 200. Recovery is `mu relay restore` (§8.4).

**The auth file** `/run/relay/users.json` (tmpfs) is rewritten after every committed change that affects it: write
`users.json.tmp`, `rename` over `users.json`. chisel 1.12.0 watches the directory, waits 100 ms after the last event and
reloads; a file it cannot parse leaves the old users in place (§15). R waits 300 ms after the rename before it answers
the request that caused it, so that a client connecting right after the answer finds itself in chisel's index.

**If the volume is lost** (the service deleted with "delete volumes", a deploy with fresh volumes, Isolated Deployment or
Randomize Compose turned on, a new server): R starts with a new `store_id` and the static clients only (minus
`RELAY_REVOKED`). Every dynamic client fails with `Authentication failed`. `mu relay status` notices the changed
`store_id` and says `the relay's store is new (lost volume?): mu relay restore`; `mu relay restore` puts back every
client, grant and tombstone from the Mac's backup (§8.4). No PC visit.

### 6.7 Eviction: restarting chisel

chisel 1.12.0 refuses a removed user's **new** channels but keeps its **established** ones, and a compute client's reverse
listener stays bound until its session ends (§15). So after a client is removed, replaced with another password, has a
port taken away or is tombstoned, R restarts its chisel child: `SIGTERM`, 3 s (`CHISEL_SHUTDOWN_GRACE=2s`), `SIGKILL`
if needed, start again. Every tunnel drops; clients reconnect by themselves (the job layer's clients retry within 15 s).
HTTP requests during the gap (about 1 s) get 502 from Traefik. At most one restart per 10 s; changes in between are
batched into the next. Adding a client, a port or a grant never restarts chisel: chisel re-reads the rules for every new
channel.

### 6.8 Supervision and signals

- chisel exits when R did not ask it to: R logs its exit status and exits 70; Docker restarts the container
  (`restart: unless-stopped`), as when chisel alone died before.
- `SIGTERM`/`SIGINT` to R: stop accepting, `SIGTERM` to chisel, wait up to 6 s, exit 0 (inside Docker's 10 s).
- R is the only parent; it reaps chisel. The healthcheck's `wget` belongs to `docker exec`, not to R.

### 6.9 Logs

The container log gets chisel's lines (unchanged) and R's events, one line each, e.g.
`mu-relayd: invite 7K3M created kind=node name=office-pc port=7001 inviter=avi-mac`,
`… claimed from=198.51.100.23`, `… wrong claim from=203.0.113.9 (1 of 20)`, `… answered`, `… finished`,
`client office-pc created (invite 7K3M)`, `chisel restarted (office-pc removed)`. Never a password, the admin token, a
claim token, a session or a MAC. A unit test greps every log line of a full scripted run for each of them.

### 6.10 Faults for the tests

`mu-relayd` built with `-tags faults` reads `MU_RELAY_FAULT` (comma-separated); the image Dokploy builds has no fault
code at all (the build argument `MU_RELAYD_TAGS` is empty unless the S4 replica sets it). Each fault switches off one
protection, and the test that guards it must then fail (§13): `allow-empty`, `unanchored-rule`, `wide-ports`,
`override-any-role`, `no-port-check`, `ignore-tombstones`, `ignore-revoked`, `copy-static-password`, `no-prev`,
`no-reload`, `no-evict`, `no-lockout`, `no-expiry`, `no-contest`, `reclaim-replaces`, `replace-at-answer`,
`xff-first`, `admin-prefix`, `allow-unknown`, `allow-upgrade`, `log-secrets`, and two that play the attacker for the
clients' tests: `tamper-offer` (one character of `offer.inviter_key` changed in every claim answer) and `tamper-answer`
(one character of `answer.host_key` changed toward the admin), and `evil-text` (every `message` and event carries an
injected instruction).

## 7. Container, compose, Dokploy

### 7.1 Image (`relay/Dockerfile`)

```dockerfile
ARG ALPINE=alpine:3.24.4@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf
# the Go image tools/s4/client already pins (Go 1.26.4)
ARG GOLANG=golang:1.26.4-alpine3.24@sha256:3ad57304ad93bbec8548a0437ad9e06a455660655d9af011d58b993f6f615648

FROM ${GOLANG} AS build
# empty in production; the S4 replica sets "faults" for its must-fail runs (§6.10)
ARG MU_RELAYD_TAGS=
WORKDIR /src
COPY control/ ./
ENV CGO_ENABLED=0 GOFLAGS=-mod=readonly GOPROXY=off GOTOOLCHAIN=local GOWORK=off
RUN go vet -tags "$MU_RELAYD_TAGS" ./... && go build -tags "$MU_RELAYD_TAGS" -trimpath -ldflags='-s -w -buildid=' -o /mu-relayd .

FROM ${ALPINE} AS fetch
# … unchanged: chisel 1.12.0, SHA-256 pinned …

FROM ${ALPINE}
COPY --from=fetch /chisel /usr/local/bin/chisel
COPY --from=build /mu-relayd /usr/local/bin/mu-relayd
COPY entrypoint.sh /usr/local/bin/relay-entrypoint
# the state volume's mount point: Docker copies owner and mode into a new, empty named volume at its first mount
RUN install -d -o 65532 -g 65532 -m 0700 /var/lib/mu-relay
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/bin/sh", "/usr/local/bin/relay-entrypoint"]
```

`relay/.dockerignore` adds `!control/go.mod` and `!control/*.go` (tests included: they are not compiled into the binary).
`GOPROXY=off` with a `go.mod` that has no `require` proves at every build that nothing is downloaded. The tests are not a
build stage: Dokploy's `docker compose up --build` would run them on the server (§13.1 runs them in CI).

### 7.2 `relay/compose.yaml`

```yaml
services:
  mu-relay:
    # … unchanged: build, restart, expose 8080, labels (mu-relay-inflight), user, read_only, tmpfs /run/relay,
    #   cap_drop, security_opt, limits, healthcheck, logging …
    environment:
      RELAY_KEY: "${RELAY_KEY:?…}"
      RELAY_CLIENTS: "${RELAY_CLIENTS:?…}"
      RELAY_KEEPALIVE: "${RELAY_KEEPALIVE:-25s}"
      # optional: set, the pairing service runs (docs/design/pairing.md); unset, chisel alone, as before
      RELAY_ADMIN_TOKEN: "${RELAY_ADMIN_TOKEN:-}"
      # optional: names that may never connect, whatever the volume holds (the kill switch, §6.5)
      RELAY_REVOKED: "${RELAY_REVOKED:-}"
    volumes:
      # dynamic clients and invites (docs/design/pairing.md §6.6). The only state the relay keeps; a named volume,
      # kept by Dokploy across deploys. Not a Dokploy "Mount" (Advanced tab), which would move the build context
      - relay-state:/var/lib/mu-relay
volumes:
  relay-state: {}
```

- The volume is project-scoped: Docker names it `<appName>_relay-state`. No `name:` key, so the two relays of the S4
  replica (and a second Dokploy service, should one ever exist) cannot share one store.
- `read_only: true` stays: the volume is the one writable path besides the tmpfs. The tmpfs `/run/relay` keeps the auth
  file and `static.tsv`; both are rebuilt at every start.
- No Traefik label, router, port or network changes. Dokploy's Domains entry (Path `/`, port 8080, middleware
  `mu-relay-inflight@docker`) stays as it is. The routing by path happens inside the container: chisel takes a request
  with `Upgrade: websocket` and `Sec-WebSocket-Protocol: chisel-v3`; every other request goes to `mu-relayd`, which
  answers `/health` and `/mu/v1/…` and 404 for everything else (as chisel did; `/version` is now 404, nothing uses it).
- The header comment of `compose.yaml` and the README lose "no volumes".

### 7.3 Dokploy and the volume (read in Dokploy's source, §15)

| Dokploy action | What happens to `relay-state` |
|---|---|
| Push to `main` touching `relay/**`, or Deploy | `docker compose -p <appName> … up -d --build --remove-orphans`: the volume is kept and mounted into the new container |
| Deploy with Dokploy's fresh-volumes option | `down --volumes` first: **the volume is deleted** (§6.6 "if the volume is lost") |
| Delete the service with "delete volumes" ticked | deleted; unticked: kept, and reused by a service with the same appName |
| Isolated Deployment with isolated volumes, or Randomize Compose | the volume gets a suffix: a new empty one. Both stay off (README §2 step 6 already says so; it now says why twice) |
| Advanced → Mounts | not used. Only Dokploy Mounts switch the command to `--project-directory <repo root>` (the build-context problem); a volume in `compose.yaml` does not |

Optional, not needed: Dokploy's volume backups. The Mac's backup (§8.4) is the recovery path.

### 7.4 Hardening

The same as the chisel container today: uid 65532, read-only root, all capabilities dropped, `no-new-privileges`, 0.5
CPU, 256 MiB, 256 pids. `mu-relayd` adds about 10 MiB. It runs no shell and executes nothing but chisel at a fixed path.

## 8. The workstation side

### 8.1 Files and secrets

```
$MU_HOME/relay/                    0700
  relay.conf                       url, fingerprint, workstation (this computer's relay user), secret_store, store_id,
                                   logged_in   (key=value, as node.conf)
  admin.token                      only with secret_store=file, 0600
  backup.json                      only with secret_store=file, 0600: clients (with passwords) and tombstones, store_id
  invites/<id>/                    0700, while an invite lives: id_ed25519(.pub) (this computer's new key for the node),
                                   invite.json (kind, node, port, replace, created, expires, the offer's fields,
                                   offer_mac, last state), code (only with secret_store=file, 0600)
```

| Secret | macOS (default) | Linux, or `--secret-store file` |
|---|---|---|
| this computer's relay password | Keychain `systems.martand.mu.relay`, account `<relay host>/<relay user>` (the job layer's item, unchanged) | `relay.auth` in each node folder, as today |
| the admin token | Keychain `systems.martand.mu.relay-admin`, account `<relay host>` | `$MU_HOME/relay/admin.token` |
| the backup | Keychain `systems.martand.mu.relay-backup`, account `<relay host>` (one JSON value) | `$MU_HOME/relay/backup.json` |
| an invite's code | Keychain `systems.martand.mu.invite`, account `<relay host>/<id>`; deleted when the invite ends | `invites/<id>/code` |

Written and read exactly as job-layer §5.3 (`security -i` with one line on stdin; reads with a 10 s limit; a locked
keychain is exit 1 with that hint). Nothing secret is ever an argument, in a log or in a run record.

### 8.2 Commands

| Command | What it does |
|---|---|
| `mu relay login --stdin` | Reads the lines of `relay/gen-credentials.sh login-bundle <workstation>` (`RELAY_URL`, `RELAY_FINGERPRINT`, `RELAY_ROLE=workstation`, `RELAY_USER`, `RELAY_PASSWORD`, `RELAY_PORTS`, `RELAY_ADMIN_TOKEN`) from stdin, checks `GET /mu/v1/` and `GET /mu/v1/clients` with the token, then stores the token, the relay password and `relay.conf`, and makes the first backup. Refuses a terminal on stdin (the lines must come through a pipe) |
| `mu relay status` | `GET /mu/v1/` and the clients; says if `store_id` changed since the last backup (§6.6) |
| `mu relay clients` | §3.7 |
| `mu relay remove NAME` | `DELETE /mu/v1/clients/NAME`; updates the backup |
| `mu relay backup` | fetches every dynamic client with its password and every tombstone into the backup |
| `mu relay restore [--dry-run]` | puts back, with `PUT`/`DELETE`, every client and tombstone of the backup that the relay lacks or has differently; prints each |
| `mu compute invite NODE [--port N] [--replace] [--new-key] [--ttl MIN] [--no-wait] [--show-code dialog] [--json]` | §8.3 |
| `mu compute invite --wait ID [--timeout S] [--json]` | continues an invite made with `--no-wait`, or after Ctrl-C |
| `mu compute invite --cancel ID` | `abort`; removes the staging folder and the code |
| `mu compute remove NODE [--relay]` | §3.7 |

New in `relay/gen-credentials.sh` (bash 3.2, `LC_ALL=C`, as the rest of it): `admin-token` (adds
`RELAY_ADMIN_TOKEN=mra_<43 chars of base64url from openssl rand 32>` to the state file if it has none; `admin-token
--rotate` replaces it), `login-bundle NAME` (the client's lines plus the token, to stdout; refuses when stdout is a
terminal), and `env` printing the token's line when there is one. The state file stays the single record of what is in
Dokploy's environment.

### 8.3 `mu compute invite`, step by step

1. Read `relay.conf` and the admin token. None: exit 2 `not-logged-in`, next: the `login-bundle | mu relay login`
   line (§3.1), to be typed by the owner.
2. `NODE` must match the node-name rule. A folder `compute/NODE` exists and no `--replace`: exit 2 `exists`.
3. `GET /mu/v1/clients`: the port is `--port`, else the existing client's, else the lowest compute port 7001–7999 that no
   compute client has. `--port` taken by another compute client: exit 2 `port-taken`.
4. Make `invites/<id>/` with a new ed25519 key (`ssh-keygen -t ed25519 -N '' -C <workstation>`), or with `--replace`
   and no `--new-key` a copy of the node's present key. Draw id, S, check (§4.1). Store the code (§8.1).
5. Build the offer (§5.2: `relay_url`, `relay_fingerprint` from `relay.conf`, `inviter_name` = this computer's relay
   user, `inviter_key`, `created`, `expires` = now + TTL, `mu_version` = `MU_INFRA_VERSION`) and its MAC; `POST
   /mu/v1/invites`. 409 `id-taken`: a new id (3 tries). 404, or 503 `store-unwritable`/`store-damaged`: exit 5
   `pairing-unsupported`.
6. Print the PC lines and the code (or with `--no-wait --json` the object of §3.3, and exit 0). The second PC line is
   the `C:\Mu\host` form when `node.conf` of a node of that name records `host_script=installed` (a pairing by invite
   finished on it before), else the `mu node host-script` form. With a relay other than the default, the line carries
   `-Relay <url>` (review finding 13).
7. Wait: `GET /mu/v1/invites/{id}?wait=25&since=<version>` in a loop, under `caffeinate -i -w <its pid>` on macOS so
   that an idle Mac does not sleep (review finding 3); print each event once with its time (the Mac's own words for
   each event type; addresses only if they parse as IP literals, review finding 5). A network
   error: `relay unreachable, trying again` and back off 5, 10, 20, 30 s. Until the invite's deadline, or `--timeout`
   (exit 10 `still-waiting`). Ctrl-C: `stopped waiting; the invite stays valid until 14:52: mu compute invite --wait
   7K3M` (exit 130).
8. `answered`: verify (§5.3), up to 24 h after `created` by the Mac's clock (`--wait` after a sleep still verifies).
   Failure, or the invite `contested`: `abort` with reason `integrity` or `contested`, remove `invites/<id>/`, exit 5.
   Then `POST accept` (for a replacement R swaps the client only now, review finding 8).
9. Install the node: write `known_hosts`, `ssh_config`, `node.conf` (`relay_user` = this computer's relay user,
   `relay_port`, `relay_url`, `relay_fingerprint`, `secret_store`, `host_key_fingerprint`, `workstation`, `added`,
   `paired_via=invite <id>`, `host_script=installed`) into the staging folder, then (with `--replace`) rename the old
   folder to `compute/.replaced-NODE`, rename the staging folder to `compute/NODE`. The relay password is the existing
   Keychain item; no copy is made. This is `mu compute add`'s code, split so both use it.
10. `hello` through the relay (`mu compute status NODE`'s code), a fresh connection each time, every 5 s for up to
    180 s (the PC's tunnel connects after its answer; chisel reloads within a second).
11. `POST finish` with the confirm (outcome `ok` or one of the fixed `unreachable:` reasons, §5.2). Remove
    `compute/.replaced-NODE`, the staging folder and the code. Update the backup (§8.4). For 24 hours after a pairing,
    `--wait`, `compute status` and `submit` look at the invite once more: a verified contest (§12.1) un-pins the node
    and says why.
12. Print `paired NODE` (exit 0), or `paired NODE, but it does not answer through the relay yet: <reason>` (exit 4).

Any failure after step 9 and before step 11 puts the old folder back (with `--replace`) or removes the new one, and
aborts the invite. Every step's error carries `invite` and the `--wait` command in `next`, so a caller can resume.

### 8.4 The backup, and restoring it

The relay is the only holder of the node's relay password (the PC chose it and gave it to the relay only). So that a
lost volume does not mean a trip to the PC, the Mac keeps a copy: after every `finish`, `remove` or `PUT` it fetches
the changed client with `?secret=1` and every tombstone into the backup (§8.1), with the relay's `store_id` and the time.
`mu relay restore` is then Mac-only (§6.6). The backup holds relay passwords and nothing else; the Mac already holds the
static ones in `relay.env`.

## 9. The PC side

### 9.1 Finding the installed release's host script, as an administrator

`mu node host-script` (new, in `src/mu/node.py`; as `mu`, no root, no network) prints one line:

```
\\wsl.localhost\mu-compute\home\mu\.local\share\mu\toolchains\v2026.11.NN\recipe\install\hosts\wsl2\mu-wsl2-host.ps1
```

from `$MU_HOME/default`, `WSL_DISTRO_NAME` and the user's home, after checking the file exists. Exit 3 with `run: mu
update` when the default version has no host script with a `pair` action; exit 2 outside WSL. Its stdout is that line
only; messages go to stderr.

In the elevated PowerShell, `powershell -NoProfile -ExecutionPolicy Bypass -File (wsl … mu node host-script) pair`:
PowerShell evaluates the parentheses first (wsl.exe's output is ASCII, one line, the newline dropped), then starts
Windows PowerShell 5.1 with the execution policy bypassed for that process only. Nothing is copied by hand.

**Which copy is trusted.** The release folder belongs to `mu` and so, once jobs run, to any job. Running a script from
it as an administrator is safe only before any job has run, which is the case at the first visit (the same rule as
checklist 2.3). After that, the copies in `C:\Mu\host` are used (the elevated install writes them, and once §13.4 of
the job layer applies, no Linux process reaches `C:`). Hence:

- the Mac prints the `C:\Mu\host` line for any node it paired by invite before (§8.3 step 6);
- `pair` started from a `\\wsl.localhost\…` path refuses when `/srv/mu/jobs` holds a job: `jobs have run on this node,
  so its release folder is not trusted for an administrator: run C:\Mu\host\mu-wsl2-host.ps1 pair`. That stops a
  mistake, not a job that rewrote the script (which would leave the check out), which is why the first rule exists;
- later updates of the root-side scripts come through `C:\Mu\host\mu-wsl2-host.ps1 install` from a copy the
  administrator trusts. How that copy is refreshed without the release folder is an open point of the job layer, not
  new here (§14 Q6).

**Staging.** Started from a `\\…` path, `pair` copies itself, `mu-wsl2-distro.sh`, `mu-node-gate` and
`..\..\..\src\mu\pair.py` to `C:\Mu\stage\<version>` (a folder created with an ACL for Administrators and SYSTEM
only; not `%TEMP%`, which a non-elevated process of the same user could rewrite between the copy and the run, review
finding 13) and runs that copy in the same window (`Start-Process
-NoNewWindow -Wait`, as `Invoke-As64Bit` does): the restart in the middle of `pair` takes `\\wsl.localhost` away for a
minute. Not elevated: the existing `Invoke-Elevated` (a new window; `Read-Host` works there).

### 9.2 `mu-wsl2-host.ps1 pair` (Windows PowerShell 5.1, ASCII only)

New action `pair`, new parameter `-Relay URL` (default `https://relay.martand.systems`). In order:

1. Stage and elevate (§9.1). Refuse from `\\…` when jobs exist (§9.1).
2. The first time on this PC (no `C:\Mu\logs\windows-reach-before.txt`): run `mu-wsl2-distro windows-reach` from the
   staged script and save its output there (checklist 2.4's record, kept automatically).
3. `install` with `-NoRestart` (exactly `Invoke-Install`; its output as today). Remember `restart` from
   `/run/mu-wsl2/install-result`. A failed install stops here with its own message.
4. The code: `Read-Host -AsSecureString 'Pairing code (from the Mac)'` (asterisks; not echoed, not in a transcript,
   not in PSReadLine's history), converted through a BSTR that is zeroed after. Written to the standard input of
   `wsl -d <distro> -u root --exec /usr/local/sbin/mu-wsl2-distro pair claim --json --relay <url>` started through
   `System.Diagnostics.Process` (one variable, no cmdlet pipeline, so Module Logging never records it; the receiving
   parser drops the CR LF, review finding 6). Exit 2
   `code-typo` or 5 `wrong-code`: ask again (3 times in all). Other errors: their message and exit.
5. If `restart` was 1: the clean restart (`Invoke-TestStop`'s stop and start, without its status print).
6. `mu-wsl2-distro pair finish --json` with the code on stdin. It prints its lines (§9.3) as they happen.
7. `Invoke-Status`, then one summary line: `paired with <inviter> · ready for jobs: yes` (or `no (<checks>)`).
   Exit code: `finish`'s, else `status`'s.

The code lives in PowerShell's memory and on stdin pipes only. Re-running `pair` asks for it again.

### 9.3 `mu-wsl2-distro pair` (root, in the distro)

`/usr/local/sbin/mu-wsl2-distro` gains `pair claim`, `pair finish`, `pair abort` and `pair run` (claim and finish in one,
for a root shell or CI; asks for the code with echo off at a terminal). Each reads the code from stdin (never an
argument), takes `--json` and `--relay URL`, and runs the protocol in `/usr/local/lib/mu/mu-pair.py`, a root-owned copy
of `src/mu/pair.py` (stdlib only, Python 3.11+; run as `/usr/bin/python3 -I -S`). `install` puts it there (`ensure_self`
copies it from beside the script, or from `../../../src/mu/pair.py` in a checkout; the ps1 copies it in with
`Copy-IntoDistro`). Root never runs Python from the release folder.

State between `claim` and `finish` (they are separate processes, and the distro restarts between them):
`/var/lib/mu-wsl2/pair/current.json`, root 0600: id, relay URL, session, phase, `offer_digest`, the password once
chosen, and what was replaced (for an undo before the answer). Not the code. `/var/lib/mu-wsl2/pair/first-use` records
when each id was first used (§4.3; entries older than 48 h are dropped).

Before anything else, every `pair` run **reconciles** an existing `current.json` for another id (review finding 12): it
asks R with the session (`GET /result`); a pairing that never answered and whose invite ended, is unknown or is older
than 24 h is undone (the steps below that it records); an answered one is kept. `status` shows `pairing: half done
(invite 7K3M)` while a `current.json` exists.

`claim`:

1. Parse the code (§4.1): `code-typo` exit 2. Refuse an id first used more than 60 minutes ago (§4.3). If
   `current.json` holds this id in phase `answered`, print `resuming invite ID` and exit 0, leaving the rest to
   `finish` (§9.5).
2. Draw the session (32 random bytes, base64url) and write `current.json` (phase `claiming`) **before** sending it, so a
   retry after a lost answer resumes instead of looking like a second claimer (§5.4). `POST /invites/{id}/claim` with
   `K_claim` and the session over HTTPS (Python's default context: the system's CA store, WebPKI host name check;
   connect timeout 10 s, review finding 9). Errors: §3.4. `invite-used` with this PC's own right code: post the contest
   (§12.1) and say so loudly, exit 5.
3. Verify the offer (§5.3); on failure `POST abort {reason: "integrity"}`, exit 5.
4. Update `current.json` (phase `claimed`, `offer_digest`) and print `code accepted: this PC becomes compute node NODE
   on relay port PORT for INVITER`; with `--json` `{ok, invite, node, port, inviter, inviter_key_fingerprint}`.

`finish`:

1. Read `current.json`; re-read the offer with the session (`GET /offer`), verify it again (it did not come through
   the restart in memory) and require its digest to equal the stored `offer_digest`.
2. The node's relay password: from `current.json` if this invite already has one (a resumed run), else
   `secrets.token_urlsafe(32)` (43 characters of `A-Za-z0-9_-`), saved to `current.json` first.
3. The relay credential: the existing `tunnel credential --wait 0`, fed `AUTH=<node>:<password>`, `RELAY_URL`,
   `RELAY_FINGERPRINT` (the offer's) and `RELAY_PORT` on stdin. The tunnel starts and retries every few seconds until the
   relay knows the client. A previous `/etc/mu/tunnel.env` is kept as `tunnel.env.before-pair` for rollback.
4. Authorise the inviter: the existing `authorize <inviter> -` with the offer's key on stdin (the previous line of that
   name, if any, kept in `current.json` for rollback). The runner's node name: `install --node-name` is not needed; the
   credential's relay user is the node's name (job-layer §22.2 #8).
5. `POST answer` with the answer, its MAC and the password. Phase `answered`.
6. Wait for `mu-tunnel.service: connected` (up to 60 s; the same classification as `tunnel status`). For a
   replacement the relay swaps the client only when the Mac accepts (§5.4), so this wait then follows the Mac.
7. `GET /result?wait=25` (each request with a 35 s timeout) for up to about 2 minutes, printing `waiting for INVITER to
   confirm …`. Verify the confirm (§5.3).
8. `finished` with outcome `ok`: print `paired with INVITER`, exit 0. Outcome `unreachable:…`: print it with `tunnel
   status`, exit 4. No confirm within the 2 minutes: print `paired with INVITER (not yet confirmed: INVITER confirms
   when it next runs mu compute invite --wait ID)` and exit 0: nothing is undone, because everything installed was
   authenticated by the code (review finding 3). Remove `current.json` and `tunnel.env.before-pair` in all three cases.
9. `aborted` (the Mac refused the answer, or a contest), or a verification failure before step 5: undo steps 3 and 4
   (the old `tunnel.env` back, or none and the tunnel stopped; the old key line back, or the new one revoked), print
   what was undone, exit 5.

Every step is idempotent within an invite: a `finish` that stopped (window closed, PC restarted) is run again by the same
`pair` line, whose `claim` sees `current.json` for the same id in phase `answered` and goes straight to step 6 (the relay
refuses a new claim then, by design; the session in `current.json` is what resumes it).

### 9.4 Does turning interop off affect the remaining steps?

`install` writes `[interop] enabled=false` and the `[automount]` options; they take effect at the restart of step 5.
Everything after it is driven from Windows (`wsl.exe -u root --exec …`), which interop does not govern (it governs Linux
starting Windows programs). Root's Python reaches the relay over the network with the distro's resolver, not through
interop, and the tunnel's egress filter applies to `mu-tunnel.service` only. `mu node host-script` runs before `install`
(interop still on) and needs nothing from Windows anyway. What does change after the restart: commands as `mu` from
PowerShell need `--cd ~` (all lines here have it), and μ's disk check reads the drive record instead of `reg.exe`
(job-layer §13.4). Doing the restart **before** the Mac's key is authorised also means no workstation key ever works on
the node while interop is on.

### 9.5 Running `pair` again

| Where it stopped | What the same line does |
|---|---|
| Before the code was accepted | everything again; install says `ok` for what it did |
| After `claim`, before `answer` (e.g. during the restart) | install no-op; asks the code; the claim carries the session from `current.json` and resumes; `finish` |
| After `answer`, before the confirm | asks the code; `claim` finds `current.json` in phase `answered` for this id and resumes the wait |
| After the confirm | a new invite is needed (the relay answers `invite-used`) |

### 9.6 Signed key requests: the node's root half of stage P2, installed at the first visit

So that a second workstation (§10) can be added without a visit, the first visit's `install` already installs the root
side; nothing uses it until P2. An **owner** workstation sends a request signed with its own ssh key; root applies it
only if the signature verifies against an owner key (review finding 7):

- `/etc/mu/owners` (root, 0644) lists the owner names: the inviter of the node's first pairing by invite (`pair` writes
  it). Only their keys go into the `allowed_signers` file that `ssh-keygen -Y verify` uses.
- Every key line authorised by a request records its authoriser (`/etc/mu/authorized-by`, `name authoriser`).
  Revoking a key revokes, in cascade, every key it authorised.
- An owner may `revoke` any name but not the last owner key; a non-owner can sign nothing.
- The dormant parser is installed and enabled at the first visit, owner-only, accepted explicitly: with the owner rule
  it gives no workstation a power it does not already have, and enabling it later would need a visit.

- **Gate verb `keyreq`** (as `mu`): reads at most 8 KiB `{"msg": "…", "sig": "…"}` from stdin and writes it to
  `/srv/mu/keyreq/<nonce>.json`. **`keyreq-status <nonce>`** reads root's result file. Both are new verbs in the gate's
  grammar and `node.py serve`.
- **The request text** (`msg`), signed with `ssh-keygen -Y sign -n mu-keyreq@martand.systems -f <the workstation's key
  for this node>`:
  ```
  mu-keyreq/1
  node office-pc
  signer avi-mac
  action authorize | revoke
  name bob-mbp
  key ssh-ed25519 AAAA…          (authorize only)
  nonce <32 hex>
  not-after <unix seconds, at most 15 min ahead>
  ```
- **Root:** `mu-keyreq.path` (`PathExistsGlob=/srv/mu/keyreq/*.json`) starts `mu-keyreq.service` (root, oneshot,
  `StartLimitBurst`), which runs `mu-wsl2-distro keyreq apply`: for at most 20 files, open with `O_NOFOLLOW`, require a
  regular file owned by `mu` of at most 8 KiB, copy it into a root-only folder, parse strictly (exact lines, the name
  and key rules of `authorize`; the nonce `^[0-9a-f]{32}$` before it is used in any path), check `node` (this node's
  name), `signer` (an owner), `not-after`, the nonce against `/var/lib/mu-wsl2/keyreq-seen` (24 h), then `ssh-keygen
  -Y verify -f <allowed_signers built from /etc/mu/authorized_keys/mu, namespace restricted> -I <signer> -n
  mu-keyreq@martand.systems -s <sig>`. Rules: `authorize` only for a name not present (never replaces a key); `revoke`
  as above. `status` reports when the path unit hit its start limit (a job can fill the folder). Then the existing `authorize`/`revoke`; the result (`applied`, or the refusal) to
  `/var/lib/mu-wsl2/keyreq/<nonce>.result` (root, 0644) and the request file removed.
- **Why this is no new power:** a paired workstation key can already run anything as `mu`, and so could hand its own
  access to anyone. A job cannot forge a request (it has no workstation's private key) and can only delete pending ones.
  The new risk is root parsing input `mu` can write; it is kept to the strict parser above and `ssh-keygen`'s verify.

## 10. A second workstation (stage P2)

Laptop B joins from paired Mac A; nobody visits the PC. The protocol is §5 with `kind` `workstation`; the node-side
step is the signed key request of §9.6, which the first visit already installed.

```
A$ mu compute invite --workstation bob-mbp            # all nodes A has paired, or --nodes office-pc,lab-pc
invite Q2W8 for workstation bob-mbp (nodes office-pc), from avi-mac, valid until 16:40 (30 min)
On bob-mbp, with μ installed, type:  mu compute join      and when it asks, the pairing code:  Q2W8-…-…
Waiting …
  16:12:03  claimed by bob-mbp (192.0.2.50)
  16:12:04  bob-mbp's keys arrived, authenticated by the code
  16:12:09  office-pc: bob-mbp's key authorised (signed request, applied by the node's root)
paired bob-mbp with office-pc

B$ mu compute join
Pairing code (from the other computer): ************************** 
  code accepted: this computer becomes workstation bob-mbp for office-pc, invited by avi-mac
  waiting for avi-mac … confirmed
  office-pc: up · 1.8 s · μ v2026.11.NN · runner active
paired with office-pc
```

- **Offer** (`K_ic`): `enc(LABEL, "offer", id, "workstation", relay_url, relay_fingerprint, new_name, ports
  ("7001+7002"), inviter_name, nodes, created, expires, mu_version)`, where `nodes` is one line per node, sorted by
  name: `<node> <port> ssh-ed25519 <base64>` (A's pinned host keys, which B therefore receives authenticated).
- **Answer** (`K_ci`): `enc(LABEL, "answer", id, offer_digest, "workstation", new_name, keys)`, `keys` one line per
  node, sorted: `<node> ssh-ed25519 <base64>` (B makes one key per node, as `mu compute add` does). B's relay password
  goes to R only; R creates the workstation client `new_name` with the invite's ports.
- **A**, after verifying: for each node, a signed `authorize new_name <B's key for it>` request (§9.6) over A's own ssh
  connection, then `keyreq-status` until `applied` (30 s). A node that is offline is reported and left for `mu compute
  grant NODE new_name` later (A keeps B's public keys in `$MU_HOME/relay/workstations/<name>.json`).
- **Confirm** (`K_ic`): outcome `ok`, or `partial:office-pc=ok,lab-pc=offline`.
- **B**, on the confirm: writes one `compute/<node>/` per node (known_hosts from the offer, ssh_config, node.conf with
  `relay_user = new_name`), stores its relay password in its Keychain, runs `hello` on each, prints `paired with …`.
- **B gets no admin token.** It can submit jobs, not invite or remove (§14 Q4).
- **Revoking B:** `mu relay remove bob-mbp` (immediate: no relay access, so no node is reachable) and `mu compute revoke
  office-pc bob-mbp` (a signed `revoke` request; the key line goes).

## 11. Migration

Today: the relay runs chisel alone with the static clients `office-pc` (compute, 7001) and `avi-mac` (workstation,
7001). No node is paired; the PC has never received a credential.

1. **The lead merges `pairing` and releases it.** The merge touches `relay/**`, so Dokploy redeploys the relay from
   `main`: the new image, with no `RELAY_ADMIN_TOKEN`, runs chisel exactly as before (`entrypoint.sh` takes today's path;
   the S4 rows prove it, §13.3). The empty volume is created and not used. The Mac's tunnel (if open) drops for about
   13 s. The release must reach the PC by auto-update before the visit (checklist §0).
2. **The one last Dokploy edit** (§3.1): `admin-token`, `remove-client office-pc` (recommended; without it the invite
   needs `--replace`, and the dynamic `office-pc` takes the static one's place when the Mac accepts, §6.5), `env`,
   paste three lines, Save, Deploy. Nothing else in Dokploy changes:
   not the Domains entry, not Advanced. The changed environment makes Docker replace the container; it starts with
   `mu-relayd`, a new store and the static `avi-mac`.
3. **`mu relay login`** on the Mac (§3.1).
4. **The visit** (§3.2): `mu compute invite office-pc`; two lines at the PC.

From then on, clients change through the API and the Mac (`mu compute invite`, `mu relay remove`), with no Dokploy
edit. `avi-mac` stays static: its password is in `relay.env` and the Keychain already; when a second node is paired R
gives it the new port as a grant, never a copy of its password (§6.5). Rotating the relay key or the admin token, or changing a static
client, is still `gen-credentials.sh` and a Dokploy edit.

**Going back:** remove `RELAY_ADMIN_TOKEN` in Dokploy and deploy: chisel alone again, with the static clients only, so
every dynamic client stops working. To keep the paired PC working, first move its client to the static list:
`mu relay export office-pc | relay/gen-credentials.sh import` (both refuse a terminal; the entry travels through the
pipe), then `env` and the Dokploy edit.

## 12. Threat model

| Who | Before (the checklist: stick, fingerprints by eye) | After (one code) |
|---|---|---|
| **The network** between either machine and the server | Nothing: TLS to Traefik, chisel's pinned fingerprint. The stick could be lost with the node's relay password on it (FAT does not erase) | Nothing: TLS, and every key MAC'd. No stick |
| **The relay or the server, compromised** (root, Docker, Dokploy) | Has every relay credential and the relay key. Cannot substitute keys: they travel by stick and are compared by eye — **if** the owner compares | Has every relay credential, the relay key, the admin token (in the environment) and the dynamic passwords (on the volume): **no new power**, since it could always edit its own auth file. Sees offers, answers (public keys only), claim tokens, the PC's address. **Cannot** make the Mac pin another host key, the PC authorise another workstation key, or the PC pin another relay fingerprint: each needs the 90-bit secret within the verifier's own window (2^-27.1 for the Mac's 24 h, 2^-31.7 for the PC's hour, with 10^4 GPUs, §4.3). **Cannot** steer what either client prints (§3.4). Can deny service: refuse, delay, lock or expire invites, drop the confirm, restart chisel |
| **A stolen admin token** (from the Mac) | The equivalent was `relay.env` plus Dokploy access | Create and delete relay clients, read every relay password, create invites, restart chisel. A client it creates reaches a node's `sshd` login, which is key-only: nothing more. **Cannot** authorise a key on any node, or pair a PC without someone typing a code there. Rotate: `gen-credentials.sh admin-token --rotate`, `env`, Dokploy edit, `mu relay login` |
| **The code, observed or stolen** before it is used (over the shoulder, from Claude Code's transcript, a photo of the Mac's screen) | — | Whoever claims **and answers first** becomes "office-pc" to the Mac. It cannot make the real PC trust a wrong workstation key (that needs the relay too). The real PC's claim is then refused (`invite-used`, whatever its address); the PC posts its authenticated contest, R removes the client the thief's answer made, and the Mac refuses to finish, or un-pins the node within 24 h of finishing (§12.1). A thief can contest too: that is only the denial of service it has anyway. After use or expiry the code is worthless |
| **A shoulder-surfer at the PC** | Fingerprints are public; the no-stick fallback asks the relay password hidden | The code is not echoed at the PC |
| **A stolen Mac** | Its node keys and relay password: job power until revoked on the node | The same, plus the admin token. From elsewhere, one Dokploy edit: add `avi-mac` to `RELAY_REVOKED` and set a new `RELAY_ADMIN_TOKEN` (`gen-credentials.sh revoke avi-mac`, `admin-token --rotate`, `env`), Deploy. `RELAY_REVOKED` removes every static entry, dynamic client and grant of that name at start, whatever the volume holds (review finding 2); no password of it was ever copied into the store. Then revoke its keys on the nodes |
| **A job on the node** | Everything `mu` can do (job-layer §14) | The same. It cannot read `/var/lib/mu-wsl2/pair/` (root 0600) or reach `C:\Mu\host` (after §13.4); it can rewrite the release folder, which an administrator therefore runs only before any job (§9.1); it can delete pending key requests, not forge them (§9.6) |

### 12.1 Contested invites

Addresses play no part (review finding 4; Q5 is gone). An invite is `contested` when:

- a claim arrives with the right token but without the session the first claimer chose (§5.4): it is refused with
  `invite-used`, and the stored session is never replaced; or
- a contest arrives: `POST …/contest` with the right claim token and `contest_mac = HMAC(K_ci, enc(LABEL, "contest", id,
  offer_digest))`. R accepts it in any state while it holds the invite (24 h after it ended), and removes the client
  that invite made (restoring what it replaced, and restarting chisel).

The Mac refuses to finish a contested invite (`invite 7K3M: another machine used the code first; nothing was paired;
make a new invite`), and for 24 hours after finishing it looks again at the invite (§8.3 step 11): a contest whose MAC
verifies un-pins the node (`office-pc was un-paired: another machine had used the code`). A claimer that lost the race
says so loudly (§3.4). Two PCs typing one code, or a thief racing the owner, end the same way: no pairing, a new invite.

## 13. Tests

Every guarded check also runs with its fault put in and must fail, as in the job layer. Fault hooks: `MU_RELAY_FAULT` in
`mu-relayd` **only when built with `-tags faults`** (the production image is built without the tag, so it has no fault
code at all); `MU_PAIR_FAULT` in `pair.py` (as `MU_JOBS_FAULT`); `S4_BREAK` in the replica.

### 13.1 `mu-relayd` unit tests (`relay/control`, `go test -race ./...`; CI job `control` in `s4-relay.yml`)

Built; `relay/control/must-fail.sh` runs each fault below with `-tags faults` and checks that every test named for it
fails. With `MU_RELAYD_CHISEL` set (CI downloads the pinned chisel 1.12.0) the integration test runs mu-relayd and the
real chisel on loopback.

| Test | Passes when | Fault → must fail |
|---|---|---|
| `TestRulesEqualEntrypoint` | for each vector, the auth file mu-relayd writes from the entrypoint's `static.tsv` equals the legacy entrypoint's `users.json` (sentinel aside) | `unanchored-rule` |
| `TestRuleSemantics` | the two rule shapes allow exactly their tunnels (Go's `regexp`, as chisel matches); 20 denied addresses | `unanchored-rule`, `wide-ports` |
| `TestCheckAuthFileRefuses` | the last check refuses `{}`, no sentinel, `""`, `*`, unanchored, other address, port 8080, two rules, none, duplicate keys, a name or a port twice, a short password, wide ports, an opened sentinel | — (direct) |
| `TestDynamicClientChecks` | `PUT` refuses bad passwords, ports, roles, names, a static compute port; a dynamic workstation's rule lists its ports | `wide-ports`, `no-port-check` |
| `TestRoleMismatch`, `TestCreateChecks` | a dynamic client never takes a name of another role; every §6.3 check | `override-any-role`, `no-port-check` |
| `TestInviteNodeEndToEnd` | create, claim, offer, answer (client in the auth file, port granted, no restart), retry, second answer refused, accept, finish, result, `?secret=1` | `no-reload` |
| `TestOfferAndAnswerUnchanged` | the relay returns offer and answer byte for byte | `tamper-offer`, `tamper-answer` |
| `TestWrongCodeSameAsUnknownAndLockout` | unknown id and wrong code: identical answers; 20 wrong claims lock; the right code then gets 410 | `no-lockout` |
| `TestWrongClaimsDoNotLockAClaimedInvite`, `TestPerAddressHoldOff` | a claimed invite is never locked by guesses; 10 wrong claims hold one address off, not another | — |
| `TestSessionContinuityAndContested` | the same session resumes from any address; another session with the right code, same address: 409, contested, gets the offer, never the session | `reclaim-replaces`, `no-contest` |
| `TestContestRemovesClient` | a contest with the right code removes the invite's client and restarts chisel; a wrong code cannot contest | `no-contest`, `no-evict` |
| `TestReplaceSwapsAtAccept` | `replace`: nothing changes at the answer; finish before accept is 409; accept swaps and restarts; abort restores the static client | `replace-at-answer`, `no-evict` |
| `TestDeadlines` | 30 min unclaimed → expired; claimed, unanswered after 60 min → expired; answered → `unconfirmed` after 24 h with the client kept; purged 24 h later | `no-expiry` |
| `TestAbort` | the claimer may abort only before answering; reasons are a fixed list; an admin abort removes the client | `no-evict` |
| `TestDeleteAndLastClient`, `TestAuthFileNeverEmpty` | delete (dynamic, tombstone), the last client refused, the sentinel always there, also with every static client revoked | `allow-empty`, `ignore-tombstones`, `ignore-revoked`, `no-evict` |
| `TestRevokedAtStart`, `TestShadowedStatic`, `TestPortStrippedWhenComputeDeleted`, `TestGrantsNeverCopyPasswords` | §6.5's rules | `ignore-revoked`, `copy-static-password` |
| `TestStrictRequests`, `TestUpgradeRefused` | duplicate or unknown members, trailing data → 400; 16 KiB + 1 → 413; an `Upgrade` → 400 | `allow-unknown`, `allow-upgrade` |
| `TestRateLimitAndAddress`, `TestAddressTableCapped`, `TestAdminAuth` | 31st unauthenticated request → 429 by XFF's second-to-last entry; the table capped; admin: 401, 403 for a token with the right first 8 characters, 10 failures hold the address off | `xff-first`, `admin-prefix` |
| `TestLongPoll` | a long poll answers within a second of the change | — |
| `TestNoSecretsInLogs`, `TestErrorTextIsFixed` | no token, claim token, session, password, MAC or sentinel in the logs or `events.log`; fixed error text | `log-secrets`, `evil-text` |
| `TestStorePersistsAcrossRestart`, `TestStorePrevUsedWhenCurrentIsDamaged`, `TestStoreDamagedAndReset`, `TestEmptyVolumeIsANewStore`, `TestStoreUnwritable` | §6.6 | `no-prev` |
| `TestStoreSurvivesKills` | 40 writer processes killed at random points: the store always loads, at the last acknowledged generation or one more | — |
| `TestEvictionRestartsChiselOnceAndBatches`, `TestChiselDyingIsReported` | the supervisor with a stand-in chisel: one restart, batched within 10 s, after its answer went out; chisel's own exit reported | — |
| `TestIntegrationWithChisel` (real chisel) | I1 the API through `--backend`; I3 a `PUT` client accepted within 3 s, no restart, no tunnel dropped; I4 a grant reaches the new port; I5 a dynamic workstation and compute client get nothing more than their rule; I6 removal cuts the session, refuses the client, the others return; I7 removing the last client refused, an unknown client refused | I3 `no-reload`, I5 `wide-ports`, I6 `no-evict`, I7 `allow-empty` |

### 13.2 `pair.py` and the Mac commands (`tools/test-pair/`, from `tools/test-jobs/run.sh`; macOS bash 3.2 and Linux)

| Test | Passes when | Fault → must fail |
|---|---|---|
| Code | round trip; aliases O/I/L; separators; every single-symbol substitution and adjacent transposition of 2000 random codes is caught by the check at least 1 - 2^-9 of the time; a wrong length or symbol is a typo | `no-check` |
| Known answers | fixed id and S give fixed `K_claim`, `claim_hash`, offer, answer and confirm MACs (vectors in the test, computed once by hand with `openssl dgst -hmac`) | `key-label-swap` |
| Every field bound | changing any one field of the offer, the answer or the confirm (each in turn) makes verification fail | `skip-field` (one field left out of `enc`) |
| Reflection | an offer MAC does not verify as an answer or a confirm, nor in the other direction | `one-key` (`K_ic` used both ways), `no-type-label` |
| Offer checks of §5.3 | each refused: other URL than the one contacted, bad fingerprint, two keys, a non-ed25519 key, R's name differs from the offer's | `trust-relay-fields` |
| Mac commands | `relay login` (refuses a terminal; stores via a `security` stub; refused token stores nothing), `invite --no-wait`/`--wait`/`--cancel`, `--replace` rollback on failure, `remove --relay` — against `mu-relayd` and chisel on 127.0.0.1 behind a test CA (`tools/test-pair/local.sh`) | `no-rollback` |
| Secrets | the code, S, the session and every password appear in no argv (`ps` sampled during a run), no log, no `--json` output except `code` in `invite --no-wait` | `code-in-argv` |
| Dispatcher | `mu relay …` routes to the default toolchain under bash 3.2 | — |

### 13.3 The S4 replica (`tools/s4/run.sh`, three Traefik versions)

The main relay runs in pairing mode (its token made by `gen-credentials.sh admin-token`), the second ("silent") relay
stays in the legacy mode, so both paths are tested in every run and all existing rows (A, W, E, D, X, L, I, R) keep
running; X14 now also checks the one mount (the named volume). The P rows run last, from the probe container (an
outside client through Traefik), with a reference implementation of §4–§5 (`tools/s4/client/pair_ref.py`, standard
library only) playing the Mac and the PC; `S4_BREAK` runs only the routing, tunnel, access and pairing groups.

| Row | Passes when | `S4_BREAK` → must fail |
|---|---|---|
| P1 | `/mu/v1/` answers through Traefik on the relay's one route; `/health` OK; the legacy relay has no `/mu/v1/` | — |
| P2 | the admin API: no token 401, a token with the right first 8 characters 403, the right one 200 | `adminprefix` |
| P3 | an invite end to end (code typed with CR LF): created, claimed, offer verified, answered, answer verified, accepted, finished, confirm verified | — |
| P4 | the new client connects within seconds with no container or chisel restart; avi-mac reaches it on the granted port; the static tunnels' connect counts unchanged | `noreload` |
| P5 | `rawchan` as a dynamic workstation opens its port only (not 7001, 8080, 8081, Traefik); as the dynamic compute client no channel; chisel's client cannot listen elsewhere | `noreload`, `wideacl` (with X16) |
| P6 | unknown id and wrong code: the same 403; a used code `invite-used`; an expired one `invite-expired`; 30 requests a minute from one address, the 31st 429 | — |
| P7 | an offer changed on the way is refused by the claimer, an answer by the inviter (both abort, no client stays); a second claimer is refused, contests, and the inviter refuses to finish | `skipmac` (the reference client's `MU_PAIR_FAULT=skip-mac`) |
| P8 | `DELETE` cuts the node's open tunnel within seconds, it is refused afterwards, avi-mac loses the port, the static tunnels come back | `noreload`, `noevict` |
| P9 | `up -d --force-recreate` (a Dokploy deploy): same store id, the dynamic client accepted, a half-done invite still open | `novolume` (with X14) |
| P10 | the volume deleted: a new store, the dynamic client refused, the static ones unaffected | — |
| P11 | removing every client stops at the last one; the auth file keeps its sentinel; a client with no valid password is refused | `allowempty` |
| P12 | a state directory not writable by 65532: `/mu/v1/` says `unwritable`, the admin API 503, the static clients connect | — |
| P13 | legacy mode is today's relay (chisel the main process, no `--backend`, no sentinel); pairing mode runs mu-relayd | — |

### 13.4 The job layer's end-to-end run (`tools/test-jobs/e2e.sh`, `ubuntu-24.04`)

S3 (`mu compute add` + `authorize`) is replaced by the pairing: the replica relay in admin mode; `ws` runs `relay login`
(bundle from the replica's `gen-credentials.sh`), `invite --no-wait --json`; root runs `mu-wsl2-distro pair run` with
the code on stdin (CI has no PowerShell side); `ws` runs `invite --wait`. Every R and H row then runs as before against
the node paired this way. New rows:

| Row | Passes when | Fault → must fail |
|---|---|---|
| Q1 | both sides say paired; the relay client is dynamic; `status` says `ready for jobs: yes` and lists `avi-mac` | — |
| Q2 | a code with a wrong check character: exit 2, the relay saw nothing | `MU_PAIR_FAULT=no-check` |
| Q3 | a valid check with another secret: exit 5 `wrong-code`, the counter at 1; 5 of them lock | relay `no-lockout` (faults build) |
| Q4 | expiry (TTL 20 s in the test): exit 9 on both sides, the PC rolled back | relay `no-expiry` |
| Q5 | the relay changes one byte of the offer's key (relay fault `tamper-offer`, the attack): the PC refuses, exit 5, nothing authorised | `MU_PAIR_FAULT=skip-offer-mac` |
| Q6 | the relay changes the answer's host key (`tamper-answer`): the Mac refuses, no node folder, the client removed | `MU_PAIR_FAULT=skip-answer-mac` |
| Q7 | `--replace` with a regenerated node host key: the Mac pins the new key; the old tunnel session is evicted | `no-evict` |
| Q8 | `mu relay remove office-pc`: the Mac's next `compute status` is exit 4 `node-offline` within 20 s | `no-evict` |
| Q9 | relay stopped while the Mac waits: `relay unreachable, trying again`, and it completes when the relay is back | — |
| Q10 | a `pair` run killed after `answer` resumes with the same line and finishes | `MU_PAIR_FAULT=no-resume` |
| Q11 | a claim from a second address (a second container) marks the invite contested; the Mac aborts | relay `no-contest` |
| Q12 | no secret in any log, journal, container log, run record or sampled argv of the whole run | `code-in-argv` |
| Q13 (P2) | `invite --workstation` + `join` as a second user; the node applies the signed request; a request signed by an unknown key, replayed, expired or for another node is refused | `MU_KEYREQ_FAULT=no-verify` |

`.github/workflows/job-layer.yml` runs on branch `job-layer` only; it gets `pairing` added.

### 13.5 The rest

- `tools/s4/test-config.sh`: the entrypoint in admin mode (token format refused without printing it; `static.tsv`
  written; the legacy path unchanged when the token is empty); `gen-credentials.sh admin-token`, `login-bundle` (refuses
  a terminal), `env` with three lines — on macOS bash 3.2 too.
- `tools/test-wsl2-host`: `pair` parses under PowerShell 5.1's rules and is ASCII; its argument handling and the
  staging path function under pwsh; the distro script's `pair` and `keyreq apply` on a test folder (as `authorize` is
  tested today).

## 14. Not tested, and open questions

**Not tested until the PC or the server:**

- Windows: the `pair` action under Windows PowerShell 5.1 (only parsed), `-ExecutionPolicy Bypass -File (…)` with a
  `\\wsl.localhost` path, the staging copy surviving the restart, `Read-Host -AsSecureString` piped into `wsl.exe`,
  `WSL_DISTRO_NAME` in a `wsl -u mu` process, the distro restart in the middle.
- Root's Python reaching `https://relay.martand.systems` from WSL through the office network (CI uses a test CA and no
  proxy), and the real Let's Encrypt chain in Ubuntu's CA store.
- Dokploy keeping the volume across a real deploy, and Docker's copy-up of the mount point's owner on the server's
  Docker version: read in Dokploy's source and tested on CI's Docker only.
- chisel's eviction restart behind the real Traefik and with real latency.
- The real Keychain (a stub stands in, as for the job layer); a store write under a power cut (fsync is not testable
  here, as in the job layer).
- How long typing 24 hidden characters takes the owner.
- `-File (wsl … mu node host-script)` when wsl.exe writes anything else to its standard output (a notice): the
  subexpression becomes an array and the arguments shift (review finding 13).

**Open questions, answered by the lead after the review:**

- **Q1.** The PC visit is two commands: accepted.
- **Q2.** The code is printed by default (the owner may follow the Mac only through Claude's remote session, where a
  macOS dialog is invisible); `--show-code dialog` stays an option. Session continuity and the authenticated contest
  (§12.1) are what make printing acceptable.
- **Q3.** No scrypt. The secret is 90 bits instead of 80 (two more characters), because the Mac now verifies up to 24 h.
- **Q4.** No admin token for a second workstation.
- **Q5.** Gone: addresses no longer decide anything (§12.1).
- **Q6.** How `C:\Mu\host` gets newer root-side scripts after jobs have run: inherited from the job layer, still open.
- **Q7.** `mu compute remove` removes the relay client only with `--relay`.
- **Q8.** A lost volume no longer revives a revoked client: `RELAY_REVOKED` (§6.5).

## 15. Read in sources

chisel v1.12.0 (`fe4f4fe`, the release the relay and clients pin):

- `share/settings/users.go`: the auth-file watcher watches the file's **directory** ("so the watch survives editors and
  orchestrators which replace the file rather than write to it"), debounces events for 100 ms, then reloads; a read,
  JSON or regex error returns before the swap, so the old users stay. Users are kept in a map keyed by name, so two
  entries with one name leave one of them at random: the merged file must have unique names.
- `server/server_handler.go`: the user is re-resolved **per channel** from the live index, so a reload applies to new
  tunnels of connected clients; a user removed during a handshake is refused. Nothing ends an established session or a
  reverse listener (also stated in the `--authfile` help and in `.github/releases/1.12-changes.md`: "removed users
  lose new tunnels but established ones aren't cut"). Hence the eviction restart (§6.7).
- `server/server_handler.go`, `server/server.go`: with `--backend` set, every request that is not a chisel WebSocket
  (`Upgrade: websocket` and the `chisel-v3` subprotocol) goes to the backend, `/health` and `/version` included; the proxy
  is `httputil.NewSingleHostReverseProxy` with the path kept and `Host` rewritten, so `X-Forwarded-For` gains Traefik's
  address.
- `share/cnet/http_server.go`: chisel's HTTP server sets no header timeout (the README's existing note); a `SIGTERM`
  drains for `CHISEL_SHUTDOWN_GRACE` (5 s by default) before it closes.

Dokploy (`42fe79c`, 2026-11-13), `packages/server/src`:

- `utils/builders/compose.ts`: a compose deploy is `docker compose -p <appName> [--project-directory <repo>] --env-file
  … -f <compose path> up -d --build --remove-orphans`; `--project-directory` is added only when the service has Dokploy
  Mounts.
- `services/compose.ts`: `down --volumes` only on a deploy with fresh volumes, and on delete only when "delete volumes"
  is chosen.
- `utils/docker/collision.ts`, `utils/docker/compose.ts`: Isolated Deployment (with isolated volumes) and Randomize
  Compose add a suffix to every volume name.

`tools/s4/traefik/traefik.yml` (Dokploy's static Traefik configuration): no `forwardedHeaders.trustedIPs`, so Traefik
does not pass on a client's `X-Forwarded-For`; read timeout 60 s.

## 16. Files, and the order of work

| Step | Files | Done when |
|---|---|---|
| P1a, the relay (built on branch `pairing`) | `relay/control/` (Go: `main.go`, `relay.go`, `invites.go`, `api.go`, `rules.go`, `store.go`, `limits.go`, `chisel.go`, `strictjson.go`, `faults.go`/`nofaults.go`, tests, `must-fail.sh`), `relay/entrypoint.sh` (pairing mode, `static.tsv`, `relayd.conf`, `RELAY_REVOKED`), `relay/Dockerfile`, `relay/compose.yaml`, `relay/.dockerignore`, `relay/gen-credentials.sh` (`admin-token`, `revoke`/`unrevoke`, `login-bundle`, `env`), `relay/README.md` §6; `tools/s4/` (pairing mode, P rows, `pair_ref.py`, the overrides), `tools/s4/test-config.sh`; `.github/workflows/s4-relay.yml` (job `control`, seven pairing must-fail runs). Not built: `mu relay export`/`gen-credentials.sh import` (§11 "going back") | §13.1, §13.3 and the existing S4 rows green on three Traefik versions |
| P1b, the clients | `src/mu/pair.py`; `src/mu/jobs.py` (`compute invite`, `compute remove --relay`, `relay …`, `mu compute add` split); `src/mu/cli.py` and `bin/mu` (route `relay`); `src/mu/node.py` (`host-script`, `keyreq`, `keyreq-status`); `install/hosts/wsl2/mu-node-gate` (the two verbs); `mu-wsl2-distro.sh` (`pair`, `keyreq apply`, `ensure_self` + `mu-pair.py`, `ensure_keyreq` units, uninstall); `mu-wsl2-host.ps1` (`pair`, `-Relay`, staging, `Copy-HostScripts` + `pair.py`); `tools/test-pair/`, `tools/test-jobs/e2e.sh`, `.github/workflows/job-layer.yml`; `claude/CLAUDE-toolchain.md` (Claude's use of `--no-wait`/`--wait`); `docs/design/first-job-checklist.md` (the short visit first, the present path kept as the fallback) | §13.2, §13.4 Q1–Q12 and every existing job-layer row green |
| P2 | `src/mu/jobs.py` (`invite --workstation`, `join`, `grant`, `revoke`) | Q13 green |

`docs/PLAN.md` and `README.md` are updated by the lead at the merge. The merge to `main` redeploys the relay (legacy
mode until the Dokploy edit of §11 step 2).

## 17. Review

The review of the built relay side (P1a, `79b51b1`…`49d762d`) by the fix-up pass of 2026-11-14.

**Findings handed to the fix-up.** The security and the usability review each delivered no findings (their results
reached the fix-up empty). Nothing was skipped for that reason, and nothing was applied from them; §0 remains the
record of the design review.

**Found while bringing CI back to green** (each with a test that fails without the fix):

| # | What failed | Cause | Fix | Shown to fail |
|---|---|---|---|---|
| F1 | `TestIntegrationWithChisel` I6 on CI (run 43571059503): "the removed client connected again", its log `connection refused`, not `authentication failed` | A test fault, not a product one. The removed client's single try (`--max-retry-count 0`) fell between the old chisel draining and the new one listening, so the network refused it before the auth file could | I6 waits for `chisel restarted` and `/health` through chisel before the try (`bb2ab83`) | `must-fail.sh` with the real chisel: `no-evict` still fails I6 (at "the removed client's session was not cut"); all 24 unit faults and 4 integration rows noticed (local, Go 1.26.4, chisel 1.12.0 darwin_arm64) |
| F2 | S4 P7 on Traefik v3.6.25 (same run): the second claimer's contest answered 403 `wrong-code` | The reference client's `Relay.call` reused the name `body` for the answer, so a retry after a 502 (chisel restarting for the eviction of the tampered invites) sent the 502's empty answer `{}` as the request: a contest without its claim token. The earlier P7 failure on v3.6.7 (run 43569958153) is the same retry path | `call()` keeps the request body; the relay's log of P1–P8 is now kept as `logs/relay-pairing.log` in the S4 artifact (`e8eff1a`) | `tools/s4/test-config.sh` "a retry after a 502 sends the same request body": with the old `call()` it reports `got '200 [{'claim': 'c'}, {}, {}]'` and exits 1 |

**For P1b (the real clients).** F2 is the rule `pair.py` must keep: every retry of a claim, answer, contest, abort or
confirm sends the identical request (same session, same body), and a client never treats a 502/503/504 without an
`error` slug as an answer. `tools/test-pair/` should carry the same check against `pair.py`. mu-relayd itself behaved
correctly in F2: a contest without a valid claim token must be refused, and it is counted as a wrong claim.

**What the branch holds, and what it does not.** P1a (the relay) is built and tested (§13.1, §13.3). P1b (`pair.py`,
`mu compute invite`, `mu relay login`, `mu node host-script`, the `pair` actions of `mu-wsl2-host.ps1` and
`mu-wsl2-distro.sh`, `tools/test-pair/`) and P2 are **not built**: the owner flow of §3.1–§3.3 cannot be run yet, and
the visit stays on the manual path of `first-job-checklist.md` until P1b is released. Merging this branch to `main`
redeploys the production relay (Dokploy watches `relay/**`); without `RELAY_ADMIN_TOKEN` it runs chisel exactly as
before (S4 legacy rows), with an unused empty volume.
