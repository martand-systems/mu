# μ pairing by one code

The design for pairing a compute node (and later a second workstation) with one code typed at the PC, through a small
control service added to the relay. It replaces steps 2.3 and 2.6–2.11 of `docs/design/first-job-checklist.md` (the
USB stick, the two key files, the two fingerprint comparisons) and keeps that path working as the fallback. Written on
branch `pairing` from v2026.11.7; nothing here is built yet. What was read in sources to decide it is in §15.

Sections: [1 Summary](#1-summary) · [2 Decisions](#2-decisions-and-what-was-rejected) · [3 User flows](#3-user-flows) ·
[4 The code](#4-the-code) · [5 Protocol](#5-protocol) · [6 Relay service](#6-the-relay-service-mu-relayd) ·
[7 Container and Dokploy](#7-container-compose-dokploy) · [8 Mac](#8-the-workstation-side) ·
[9 PC](#9-the-pc-side) · [10 Second workstation](#10-a-second-workstation-stage-p2) · [11 Migration](#11-migration) ·
[12 Threat model](#12-threat-model) · [13 Tests](#13-tests) · [14 Not tested, open questions](#14-not-tested-and-open-questions) ·
[15 Read in sources](#15-read-in-sources) · [16 Files and order of work](#16-files-and-order-of-work)

## 1. Summary

- **On the Mac:** `mu compute invite office-pc` prints two lines to type at the PC and one code, then waits. It ends by
  itself with `paired office-pc` once the PC has answered and the Mac has reached it through the relay.
- **At the PC (Terminal (Admin)), two commands:** `mu update` (a no-op when the release is there), then one line that runs
  the installed release's host script with the action `pair`. It sets the node up (the existing `install -NoRestart`),
  asks for the code (not echoed, never an argument), restarts the distro cleanly when the setup needs it, writes the
  relay credential, authorises the Mac's key, sends its host key, waits for the Mac and ends with `paired with avi-mac`
  and `ready for jobs: yes`. No stick, no file carried, nothing compared by eye.
- **The code** is 22 characters, `7K3M-Q9TD-4WXR-HN2C-PB8F-ZJ`: a 20-bit invite id (sent to the relay), an 80-bit secret
  (never sent anywhere) and a 10-bit checksum against typos. Every key that crosses (the Mac's public key, the relay URL
  and fingerprint, the node's name and port, the node's host key) is authenticated with HMAC-SHA-256 under keys derived
  from the secret, one key per direction. A relay that sees and changes every message cannot make either side accept a
  key it did not send: it would have to find the secret, 2^80 candidates, before the invite ends (§4.3).
- **The relay** gains `mu-relayd`, a small Go program (standard library only) in the same container as chisel. chisel
  stays in front on port 8080 and passes every request that is not a chisel WebSocket to `mu-relayd` on
  `127.0.0.1:8081` (`chisel server --backend`, §15). So Traefik, Dokploy's Domains entry and the connection limit are
  unchanged. `mu-relayd` creates relay clients through an admin API (token `RELAY_ADMIN_TOKEN`, held by the Mac in the
  Keychain), writes chisel's auth file, which chisel 1.12.0 reloads by itself, and keeps its state in a named Docker
  volume. Without `RELAY_ADMIN_TOKEN` the container behaves exactly as today.
- **Migration:** one last Dokploy edit (paste three environment lines instead of two, Deploy). The static clients
  `office-pc` and `avi-mac` keep working; a dynamic client of the same name takes precedence.
- **Stages:** P1 is everything above for compute nodes, plus the node-side root half of P2 (so that the one visit
  installs it). P2 is `mu compute invite --workstation`: a second laptop paired from an already paired Mac, with no visit
  to the PC (§10).

## 2. Decisions, and what was rejected

The lead's five decisions stand. Where this design refines one, the row says so.

| # | Decision | Here |
|---|---|---|
| 1 | One code; Mac `mu compute invite <node>` waits (with `--json` and a non-waiting mode); one PC command runs the node's root setup from the installed release and pairs | Kept. The PC visit is **two** commands, because the first (`mu update`) is what makes "the installed release" the one with `pair` in it; it is a no-op when auto-update already did it (§3.2). `--workstation` is stage P2 (§10) |
| 2 | A pairing service behind the same Traefik route, path-routed, admin token, dynamic clients with rules as strict as `entrypoint.sh`, static env clients keep working, a named volume | Kept. "Same compose service family" is realised as **the same container**: chisel forwards non-chisel requests to the service (`--backend`), so no Traefik label, Domains entry or second service is needed (§2.1) |
| 3 | id + secret; every key MAC'd; offline brute force infeasible; single use; TTL ~30 min; rate limits, lockout; stdlib Python on both machines; Go or Python on the relay | Kept: 20-bit id, 80-bit secret, HMAC-SHA-256, 30 min to claim, 60 min at most in all; Go (§2.2) |
| 4 | The relay may learn relay credentials but must not be able to substitute keys; revocation removes the dynamic client | Kept. Removing or replacing a client also **restarts chisel**, because chisel 1.12.0 does not cut a removed user's established tunnels (§6.7, §15) |
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
| A PAKE (SPAKE2, CPace) with a short code (6–8 digits) | The right tool when the code must be short, but there is none in Python's standard library or Go's: it would mean hand-written elliptic-curve arithmetic in two languages, constant-time concerns, one more round trip, and a review we cannot get. The price of the simple design is 22 characters typed once per node. If that proves too long, the cheaper step is §14 Q3 (a memory-hard KDF), not a PAKE |
| The relay generates the code | Then the relay knows the secret and can substitute keys: the one property this design exists for |
| The PC shows a code, the owner types it on the Mac | The PC knows neither the relay's fingerprint nor the admin token, so it cannot open the invite; and on the Mac the owner would type it to Claude Code anyway |
| QR codes | The PC side is a PowerShell window and the Mac has no reason to have a camera pointed at it; nothing would read the code |
| Fingerprints compared by eye (today) | People skip it. Kept only as the fallback (§3.8) |
| Trust on first use through the tunnel (`--host-fingerprint`, job-layer §15 stage 3) | Still needs a fingerprint compared, and gives the PC nothing to authenticate the Mac's key with |
| Relay passwords derived from a seed, so nothing secret is on the volume | Couples every dynamic password to one more long-lived secret and still loses the client list with the volume. The server already holds every password in Dokploy's `.env`; storing them on the volume exposes nothing new (§12) |
| A stretched secret (scrypt) to shorten the code | Simpler without. 80 bits are enough unstretched (§4.3); see §14 Q3 |

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

  7K3M-Q9TD-4WXR-HN2C-PB8F-ZJ

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

Pairing code (from the Mac): **************************
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
{"ok": true, "invite": "7K3M", "node": "office-pc", "kind": "node", "port": 7001, "code": "7K3M-Q9TD-4WXR-HN2C-PB8F-ZJ",
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
`message` and `next` and exits with the same code from `mu-wsl2-host.ps1`.

| Situation | Mac (`mu compute invite`) | PC (`pair`) | Exit |
|---|---|---|---|
| Typo in the code (checksum) | — | `the code has a typo (its check characters do not match): compare it with the Mac's screen and type it again` — asked again, 3 times, then exit | 2 `code-typo` |
| Wrong secret with a valid checksum (another invite's code, or a typo the checksum missed: 1 in 1024) | event `a wrong code was tried for invite 7K3M (1 of 5)` | `the relay does not accept this code for invite 7K3M (4 tries left): check it on the Mac's screen` — asked again | 5 `wrong-code` |
| 5 wrong codes | `invite 7K3M is locked: 5 wrong codes were tried. Make a new one: mu compute invite office-pc` | `invite 7K3M is locked (too many wrong codes): ask for a new code on the Mac` | 5 `invite-locked` |
| Expired (30 min unclaimed; 30 min claimed but not answered; 15 min answered but not confirmed) | `invite 7K3M expired at 14:52 before the PC claimed it` (or `… answered it`) and the next command | `invite 7K3M expired at 14:52: make a new one on the Mac (mu compute invite office-pc)` | 9 `invite-expired` |
| Unknown id | — | `there is no invite 7K3M on https://relay.martand.systems: check the first four characters` | 9 `invite-unknown` |
| Already used (claimed by another machine, or finished) | `invite 7K3M was already claimed from 203.0.113.9 at 14:25` shown as an event | `invite 7K3M was already used (claimed at 14:25 from another address). If that was not this PC, on the Mac: mu compute remove office-pc --relay, then a new invite` | 5 `invite-used` |
| The relay changed the offer (MAC fails at the PC) | event `the PC refused the invite: it was changed on the way` and exit 5 | `the invite was changed on the way (the relay or the network): nothing was set up from it. Tell the Mac's owner; do not retry this code` (the PC posts `abort`) | 5 `pair-integrity` |
| The relay changed the answer (MAC fails at the Mac) | `the PC's answer was changed on the way (the relay or the network): nothing was paired, the relay client office-pc was removed` | `avi-mac refused this PC's answer (it arrived changed): nothing was paired` | 5 `pair-integrity` |
| Relay unreachable | at creation: `the relay https://relay.martand.systems does not answer (…): nothing was created`; while waiting: `relay unreachable, trying again` every 5–30 s until the deadline | `the relay does not answer (…). The code stays valid until 14:52: run the same line again` | 4 `relay-unreachable` |
| Relay without pairing (old image, or no `RELAY_ADMIN_TOKEN`) | `the relay has no pairing service (GET /mu/v1/ answered 404, or 503 not-configured): see docs/design/pairing.md §11` | `the relay has no pairing service: use the manual path (first-job-checklist.md)` | 5 `pairing-unsupported` |
| Admin token refused | `the relay refused this computer's admin token: mu relay login again (relay/gen-credentials.sh login-bundle …)` | — | 5 `relay-admin` |
| Node already paired on this Mac | `office-pc is already paired: to pair it again (new host key or new key here): mu compute invite office-pc --replace` | — | 2 `exists` |
| A relay client of that name exists and `--replace` was not given | `the relay already has a client office-pc (dynamic, created 2026-11-20 by invite 7K3M): --replace, or another name` | — | 2 `client-exists` |
| Port taken by another compute client | `relay port 7002 belongs to compute client lab-pc: leave --port out, or pick a free one` | — | 2 `port-taken` |
| The PC runs an older release | — | first line fails: `mu: unknown command 'host-script'` (and PowerShell: `the argument '' to -File does not exist`). Next: run `mu update` (the first line) | — |
| Paired, but the Mac cannot reach the node | `paired office-pc, but it does not answer through the relay yet: node-offline (…). Next: mu compute status office-pc` | `avi-mac has the keys but could not reach this PC through the relay: node-offline` + `tunnel status` | 4 |
| `--wait` timeout passed, still waiting | `still waiting (state claimed, last event 14:26)`, next `mu compute invite --wait 7K3M` | — | 10 `still-waiting` |

### 3.5 Re-pairing

- **The PC was reinstalled** (new host key) or **the Mac lost its folder**: `mu compute invite office-pc --replace`. The
  Mac keeps its old `compute/office-pc/` aside until `finish` and puts it back on any failure. The relay keeps the old
  client until the new answer arrives, restores it on abort or expiry, and at `finish` restarts chisel so that no session
  of the old credential survives (§6.7). The Mac keeps its key for the node (the PC's `authorize` replaces the line of the
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
7K3M-Q9TD-4WXR-HN2C-PB8F-ZJ
└id┘ └──── secret, 16 ────┘ └check
```

- **Alphabet:** Crockford base32, `0123456789ABCDEFGHJKMNPQRSTVWXYZ` (no I, L, O, U). Input is case-insensitive;
  `O`→`0`, `I` and `L`→`1`; spaces and `-` are ignored; any other character is a typo.
- **id:** 4 symbols, 20 bits, made by the Mac with `secrets.choice`. The relay refuses an id it holds or held in the last
  24 hours (409; the Mac draws another, 3 tries).
- **secret S:** 16 symbols, 80 bits, `secrets.choice`; as bytes, the 80 bits big-endian in 10 bytes.
- **check:** 2 symbols, the first 10 bits of `SHA-256("mu-pair-code/1:" + id + secret)` (ASCII, upper case). It is
  computed and checked only on the two machines; it never reaches the relay.
- **Display:** groups of four separated by `-`, the check last. 22 symbols.

### 4.2 Why these sizes

| Part | Bits | Why |
|---|---|---|
| id | 20 | Only a lookup key. It is not secret: it identifies the invite to the relay, which rate-limits and locks it. 20 bits make a collision among the few live and retained invites rare (the Mac retries) and guessing a live one by hammering the relay slow (§6.4: at 20 requests a minute per address that is weeks per address) |
| secret | 80 | The only thing protecting the keys against the relay. Sized for the offline attack of §4.3 with a margin of 2^20 or more |
| check | 10 | Catches 1023 in 1024 typos locally, before anything is sent; the rest count as one wrong code at the relay (4 more allowed). Not a linear code, so a single substitution is not guaranteed to be caught, only with that probability |

### 4.3 The offline attack, sized

The party that sees the MACs is the relay (or whoever controls the server). To substitute a key it must produce a valid
MAC under a key derived from S **before** the honest side verifies. It sees the first MAC (the offer) when the invite is
created and the last verification happens at most 60 minutes later (30 to claim, 30 to answer, both counted from
creation; `finish` 15 minutes after the answer only confirms). Each guess of S costs one HMAC-SHA-256 (≈ 4 SHA-256
compressions) checked against the offer's MAC, or one SHA-256 more against the claim hash.

A generous attacker: 10^4 GPUs at 10^10 guesses per second each (better than any published HMAC-SHA-256 rate) for 3600 s
tries 3.6 × 10^17 ≈ 2^58.3 values. The chance of hitting an 80-bit S is 2^-21.7, about 1 in 3.4 million, for one
pairing. After the invite ends a found S is useless: every key it protected has been verified and pinned, and the relay
refuses anything more for that id.

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
              outcome)            # "ok", or "unreachable:<reason>" (the Mac pinned the key but hello failed)
confirm_mac = HMAC(K_ic, confirm)
```

What this binds: the invite id and the protocol version in every MAC; the node's name and port, the relay URL and
fingerprint and I's public key in the offer; the host key in the answer, tied to that exact offer by its digest; the
confirm tied to both. The node's relay password is not MAC'd: C chooses it and gives it to R only, R checks it anyway,
and a wrong one can only deny service (§12).

**Replay and reflection.** Each direction has its own key, and each message type its own label inside the MAC, so no
MAC is valid as another message or in the other direction. A message from another invite fails because the id and S
differ. Within one invite each type is accepted once in R's state machine, and each side verifies exactly one of each.

### 5.3 What each side checks

C, on the offer: the MAC; `kind` is `node`; `relay_url` equals the URL C contacted (§9.2); `relay_fingerprint` is 43
base64 characters and `=`; `node_name` and `inviter_name` match `[a-z0-9][a-z0-9-]{0,31}`; `port` is 7001–7999;
`inviter_key` is one ed25519 key; `now ≤ expires + 600` (WSL's clock may lag after a sleep); and R's JSON fields
`name` and `port` (R's own view) equal the offer's. Any failure: `abort` with reason `integrity`, exit 5, nothing set up.

I, on the answer: the MAC, `offer_digest` equal to its own, `node_name` equal, `host_key` one ed25519 key. Any failure:
`abort` (R removes the client and restores what it replaced), exit 5, nothing kept.

C, on the confirm: the MAC and both digests. A confirm that fails is reported (`the confirmation did not verify`, exit 5)
but undoes nothing: by then the keys on both sides were each verified on their own.

### 5.4 The relay's state machine

```
                 claim ok (≤3 claims while "claimed": a re-run of the PC command)
   created ───────────────► claimed ──answer──► answered ──finish──► finished
      │  │                     │  │                │
      │  └── 5 wrong claims ───┴──┼──► locked      │
      │                           │                │
      └─────── abort / expiry ────┴────────────────┴──► aborted | expired
```

| State | Entered by | Deadline | Allowed next |
|---|---|---|---|
| `created` | `POST /invites` (admin) | `expires` = created + TTL (default 30 min, at most 60) | claim → `claimed`; wrong claim (count); abort; expiry |
| `claimed` | claim with the right token | min(claimed + 30 min, created + 60 min) | answer (with the current session) → `answered`; claim again with the right token (a new session; the old one dies; at most 3 claims); abort; expiry |
| `answered` | answer | answered + 15 min | finish (admin) → `finished`; abort; expiry. **No new claim, no second answer** |
| `finished`, `aborted`, `expired`, `locked` | | kept 24 h, then purged | none (every request answers the state) |

Side effects: on `answered`, R creates (or replaces) the dynamic client and adds the port to the inviter's workstation
client (§6.5); on `aborted` or `expired` from `answered`, R removes that client and restores the one it replaced; on
`finished`, R drops the replaced entry and, if it replaced or removed anything, restarts chisel (§6.7). A claim counts
as wrong when its token's hash differs from `claim_hash`; 5 wrong claims lock the invite in any non-terminal state
before `answered`.

## 6. The relay service, `mu-relayd`

### 6.1 Process and configuration

`relay/entrypoint.sh` keeps all the checks it has. Then:

- `RELAY_ADMIN_TOKEN` unset or empty: **exactly today's path** (`exec chisel server … --authfile users.json`).
- Set: it must be `mra_` followed by at least 43 characters of `A-Za-z0-9_-` (exit 64 otherwise, the value never
  printed). The entrypoint writes the parsed static clients to `/run/relay/static.tsv` (0600; `role name password ports`
  per line, the same entries it would have written to `users.json`) and execs
  `env -i PATH=… RELAY_KEY=… RELAY_ADMIN_TOKEN=… RELAY_KEEPALIVE=… /usr/local/bin/mu-relayd`. `RELAY_CLIENTS` stays
  required by `compose.yaml`, as today.

`mu-relayd`:

1. reads the token and the key from its environment and unsets them;
2. loads the store from `/var/lib/mu-relay` (§6.6), refuses to start if the directory is not writable by uid 65532
   (exit 64: `the state volume is not writable by 65532: see docs/design/pairing.md §7.2`);
3. reads `static.tsv`, merges (§6.5), writes `/run/relay/users.json`;
4. listens on `127.0.0.1:8081` (`ReadHeaderTimeout 5 s`, `ReadTimeout 10 s`, `WriteTimeout 40 s`, `IdleTimeout 60 s`,
   `MaxHeaderBytes 8 KiB`);
5. starts chisel as its child: `chisel server --host 0.0.0.0 --port 8080 --reverse --authfile /run/relay/users.json
   --keepalive $RELAY_KEEPALIVE --backend http://127.0.0.1:8081` with the environment `PATH`, `CHISEL_KEY_FILE=<key>`,
   `CHISEL_SHUTDOWN_GRACE=2s` and nothing else; chisel's stdout and stderr go to the container log unchanged.

### 6.2 Endpoints

All under `/mu/v1/`, JSON in and out (`Content-Type: application/json`, body at most 16 KiB, unknown members refused),
`Cache-Control: no-store`. Answers are `{"ok": true, …}` or `{"ok": false, "error": "<slug>", "message": "…"}`.

| Method, path | Auth | Body → answer |
|---|---|---|
| `GET /health` | none | `OK\n` when the store is loaded and chisel runs; 503 otherwise. (chisel sends `/health` here once `--backend` is set, §15; the healthcheck and S4 keep working unchanged) |
| `GET /mu/v1/` | none | `{"ok":true,"service":"mu-relay","protocol":1,"pairing":true}` |
| `POST /mu/v1/invites` | admin | `{id, kind, name, port \| ports, inviter, claim_hash, offer, offer_mac, ttl_s, replace}` → `{id, state, expires}`. Checks: §6.3 |
| `GET /mu/v1/invites/{id}?wait=S&since=STATE` | admin | long poll (S ≤ 25) until the state differs from `since` → `{state, events[], claimed_from, answer, answer_mac, deadline}` |
| `POST /mu/v1/invites/{id}/finish` | admin | `{confirm, confirm_mac}` → `{state: "finished", evicted: bool}`. Only from `answered` |
| `POST /mu/v1/invites/{id}/abort` | admin or session | `{reason}` → `{state: "aborted"}` |
| `POST /mu/v1/invites/{id}/claim` | claim token | `{claim}` → `{session, kind, name, port \| ports, inviter, offer, offer_mac, deadline}` |
| `GET /mu/v1/invites/{id}/offer` | session | `{offer, offer_mac, name, port, inviter, state}` (the PC re-reads it after its restart) |
| `POST /mu/v1/invites/{id}/answer` | session | `{answer, answer_mac, password}` → `{state: "answered", client: name}` |
| `GET /mu/v1/invites/{id}/result?wait=S` | session | long poll until `finished`/`aborted`/`expired` → `{state, confirm, confirm_mac}` |
| `GET /mu/v1/clients` | admin | `{store_id, clients: [{name, role, ports, source: static\|dynamic\|tombstone, overrides_static, created, via}]}` |
| `GET /mu/v1/clients/{name}?secret=1` | admin | one client with its password (the Mac's backup, §8.4) |
| `PUT /mu/v1/clients/{name}` | admin | `{role, password, ports}` → creates or replaces a dynamic client (restore, manual add); evicts if it replaced one with another password |
| `DELETE /mu/v1/clients/{name}` | admin | removes a dynamic client, or adds a tombstone for a static one; evicts |
| `GET /mu/v1/events?n=N` | admin | the last N lines of the event log (§6.9) |

Status codes: 400 `bad-request`, 401 `admin-required`, 403 `wrong-code` / `bad-session` / `admin-refused`, 404
`invite-unknown` / `no-such-client`, 409 `invite-used` / `client-exists` / `port-taken` / `id-taken` / `state`, 410
`invite-expired` / `invite-aborted` / `invite-locked`, 413 `too-large`, 429 `rate-limited` (with `Retry-After`), 503
`not-configured` / `store-damaged`.

**Auth.** Admin: `Authorization: Bearer mra_…`, compared as `subtle.ConstantTimeCompare(SHA-256(given),
SHA-256(token))`. Claim: `SHA-256(hex-decoded claim)` against `claim_hash`, constant time. Session: 32 random bytes,
base64url, given to the claimer once; R stores its SHA-256; `Authorization: Mu-Session <token>`; a new claim replaces it.

### 6.3 Checks at invite creation

`kind` is `node` (P1) or `workstation` (P2). Names `[a-z0-9][a-z0-9-]{0,31}`; ports 7001–7999 without a leading zero;
`claim_hash` 64 hex; `id` 4 symbols of the alphabet, not held now or in the last 24 h. The offer's fields must equal the
request's `name`, `port`, `inviter` and `kind` (R reads the offer, it cannot check its MAC). `inviter` must be an existing
workstation client (static or dynamic). For `node`: no other compute client (static or dynamic, ignoring one this name
would replace) has the port; a client of this name exists only with `replace: true` and the same role. Live invites: at
most 8 in all and 4 per inviter.

### 6.4 Rate limits and lockout

The client address is the second-to-last entry of `X-Forwarded-For`: Traefik replaces any `X-Forwarded-For` a client
sends with the client's address (no `trustedIPs` in Dokploy's configuration), and chisel's proxy appends Traefik's own
(§15). A container on `dokploy-network` that talks to port 8080 directly can choose its address; the global limits still
bind it (the same position the README already describes for the connection limit).

| What | Limit |
|---|---|
| Requests without admin auth (claim, offer, answer, result, abort with a session, `GET /mu/v1/`) | 20 per minute per address (bucket of 20), 600 per minute in all |
| Wrong claims | 5 per invite → `locked`; 10 per address per hour → that address gets 429 for an hour on every claim |
| Failed admin auth | 5 per address in 15 min → 429 for 15 min for that address |
| Admin requests | 120 per minute |
| Long polls | at most 2 open per invite per side; `wait` ≤ 25 s (Traefik's read timeout is 60 s) |
| Live invites | 8 in all, 4 per inviter |
| Clients | 64 dynamic clients |

Traefik's `mu-relay-inflight` (32 connections at a time per address) applies to `/mu/v1/` as to the tunnels: same router.

### 6.5 Static and dynamic clients

- **Static** clients come from `RELAY_CLIENTS` (via `static.tsv`); `mu-relayd` never changes them.
- **Dynamic** clients are in the store. A dynamic client **may take a static client's name only with the same role**; it
  then replaces the static entry in the merged auth file (`overrides_static: true`). This is how pairing `office-pc`
  works while `office-pc` is still in `RELAY_CLIENTS`, and how the static `avi-mac` gets a new port: R copies the static
  password into a dynamic `avi-mac` with ports ∪ {the new port}.
- **Tombstones** (`DELETE` of a static client) remove a static name from the merged file. They live in the store, so a
  lost volume brings the static client back: `mu relay clients` then shows it, and the README's rule is to remove a
  tombstoned client from `RELAY_CLIENTS` at the next Dokploy edit.
- **The merged file** has each name once (chisel keys users by name and keeps one of two at random, §15), every compute
  port once, and exactly the rules of `entrypoint.sh`:

  | Role | Rule (JSON string) |
  |---|---|
  | compute, port P | `^R:127\\.0\\.0\\.1:P$` |
  | workstation, ports P1+P2 | `^127\\.0\\.0\\.1:(P1\|P2)$` |

  Passwords `[A-Za-z0-9_-]{24,}`, names `[a-z0-9][a-z0-9-]{0,31}`, ports 7001–7999 without a leading zero, as the
  entrypoint checks. A Go test compares the merged file of static-only input byte for byte with the entrypoint's
  `users.json` for the same `RELAY_CLIENTS` (§13.1).

### 6.6 Storage on the volume

`/var/lib/mu-relay/` (the named volume, owner 65532, 0700):

```
state.json        the store (0600)
state.json.prev   the previous generation (0600)
events.log        one JSON line per event, no secrets (0600); cut to its last 512 KiB when it passes 1 MiB
```

```json
{"format": 1, "store_id": "3f9a1c0e7d2b4a55", "created": "2026-11-20T10:02:11Z", "generation": 17,
 "clients": {"office-pc": {"role": "compute", "password": "…", "ports": [7001], "created": "…", "via": "invite 7K3M",
                           "overrides_static": true}},
 "tombstones": {"old-laptop": {"role": "workstation", "removed": "…"}},
 "invites": {"7K3M": {"kind": "node", "name": "office-pc", "port": 7001, "inviter": "avi-mac", "state": "answered",
                      "claim_hash": "…", "offer": {…}, "offer_mac": "…", "created": 0, "deadline": 0, "bad_claims": 0,
                      "claims": 1, "session_hash": "…", "claimed_from": "198.51.100.23", "answer": {…},
                      "answer_mac": "…", "confirm": null, "replaced": null, "inviter_ports_before": [7001]}}}
```

**Writes** (one goroutine owns the store; every change goes through it): marshal; write `state.json.tmp` (`O_CREAT |
O_TRUNC`, 0600); `fsync`; `rename(state.json → state.json.prev)`; `rename(state.json.tmp → state.json)`; `fsync` the
directory; only then answer the request and rewrite the auth file. A crash between the two renames leaves `.prev` and no
`state.json`: the load takes `.prev` (the change was never acknowledged).

**Loading:** `state.json`, else `state.json.prev`. Both missing: a new store (new `store_id`, event `store-created`).
Both present and unreadable (disk damage; atomic writes make anything else unlikely): `store-damaged`. R then
starts chisel with the static clients only (so the relay stays up for them), logs the reason every minute, answers 503
`store-damaged` on every pairing and admin endpoint except `POST /mu/v1/store/reset` (admin), which moves both files to
`state.json.damaged-<time>` and starts a new store, and keeps `/health` at 200 (a restart would not repair the file).
Until the reset, a static client that had a tombstone is accepted again: the same exposure as a lost volume, below.
Recovery is `mu relay restore` (§8.4).

**The auth file** `/run/relay/users.json` (tmpfs) is rewritten after every committed change that affects clients: write
`users.json.tmp`, `rename` over `users.json`. chisel 1.12.0 watches the directory, waits 100 ms after the last event and
reloads; a file it cannot parse leaves the old users in place (§15). R waits 300 ms after the rename before it answers
the request that caused it, so that a client connecting right after the answer finds itself in chisel's index.

**If the volume is lost** (the service deleted with "delete volumes", a deploy with fresh volumes, Isolated Deployment or
Randomize Compose turned on, a new server): R starts with a new `store_id` and static clients only. Every dynamic client
fails with `Authentication failed` (the PC's `tunnel status`: `relay-credential`; the Mac: exit 5 `relay-credential`).
`mu relay status` notices the changed `store_id` and says `the relay's store is new (lost volume?): mu relay restore`;
`mu relay restore` puts back every client and tombstone from the Mac's backup (§8.4) with `PUT`/`DELETE`. No PC visit.

### 6.7 Eviction: restarting chisel

chisel 1.12.0 refuses a removed user's **new** channels but keeps its **established** ones, and a compute client's reverse
listener stays bound until its session ends (§15). So after a client is removed, replaced with another password, or
tombstoned, R restarts its chisel child: `SIGTERM`, 3 s (`CHISEL_SHUTDOWN_GRACE=2s`), `SIGKILL` if needed, start again.
Every tunnel drops; clients reconnect by themselves (chisel 1.12.0's back-off starts at 1 s; the Mac's ssh retries per
job-layer §5.2). HTTP requests during the gap (about 1 s) get 502 from Traefik. At most one restart per 10 s; changes in
between are batched. Adding a client or adding a port to a workstation never restarts chisel: chisel re-reads the ACL for
every new channel.

### 6.8 Supervision and signals

- chisel exits when R did not ask it to: R logs its exit status and exits 70; Docker restarts the container
  (`restart: unless-stopped`), as when chisel alone died before.
- `SIGTERM`/`SIGINT` to R: stop accepting, `SIGTERM` to chisel, wait up to 6 s, exit 0 (inside Docker's 10 s).
- R is the only parent; `cmd.Wait()` reaps chisel. The healthcheck's `wget` belongs to `docker exec`, not to R.

### 6.9 Logs

The container log gets chisel's lines (unchanged) and R's events, one line each, e.g.
`mu-relayd: invite 7K3M created kind=node name=office-pc port=7001 inviter=avi-mac from=192.0.2.4`,
`… claimed from=198.51.100.23`, `… wrong claim from=203.0.113.9 (1 of 5)`, `… answered host-key=SHA256:Xq1…Rw`,
`… finished`, `client office-pc created (overrides static)`, `chisel restarted (evict: office-pc replaced)`.
Never a password, the admin token, a claim token, a session token or a MAC. A unit test greps every log line of a full
run for each of them.
