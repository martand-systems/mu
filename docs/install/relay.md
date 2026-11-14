# Running the μ relay

For whoever looks after the relay (today: the owner). The reference, with every setting and the reasons, is
[`relay/README.md`](../../relay/README.md); this page is the short path through it. The design of the pairing service
is `docs/design/pairing.md`.

## What it is

One Docker container on Martand's Docker server (`195.201.236.190`), deployed by Dokploy from the `relay/` folder of
the repo and reached at `https://relay.martand.systems` through the server's Traefik. Compute nodes and Macs each
open an outbound connection to it on port 443, and it joins them: that is how a Mac reaches the office PC from
anywhere without opening a port at the office. It runs chisel (the tunnel) and, when its admin token is set,
`mu-relayd` (the pairing service: `mu compute invite`). Job data crosses it inside SSH, end to end between the Mac and
the node, so the relay sees only ciphertext (`docs/design/job-layer.md` §2).

**State, and where it is:**

| What | Where | Secret? |
|---|---|---|
| The relay's key, the static clients' passwords, the admin token, the revoked list | `~/.config/mu/relay.env` on the owner's Mac (mode 600), and Dokploy's Environment tab | yes |
| Clients made by pairing, grants, tombstones | the container's named volume `relay-state` | yes (passwords) |
| A backup of the volume's clients | the owner's Mac, in the login Keychain (`mu relay backup`) | yes |
| The admin token and this Mac's relay client, for `mu` | the owner's Mac, login Keychain (`mu relay login`) | yes |

**Rule:** type the commands on this page in **Terminal on the Mac, not through Claude Code**. The admin token and the
passwords must never pass through a Claude transcript; every command below keeps them in files and pipes.

**Status (2026-11-14):** deployed on 2026-11-14; the pairing service turned on the same day (redeployed with
`RELAY_ADMIN_TOKEN`); the office PC paired through it. Still not checked on the real server: a relay redeploy with a
session open and the reconnect time, idle beyond 5 minutes, credential rotation, whether Dokploy keeps the volume
across a deploy (`docs/verification/2026-11-13-S4-relay.md`).

## 1. Credentials (once, on the Mac)

In a clone of the repo (`~/mu`):

```sh
cd ~/mu && git pull
relay/gen-credentials.sh init                                 # the relay's key; prints its fingerprint
relay/gen-credentials.sh add-client workstation avi-mac 7001  # the owner's Mac (a static client)
relay/gen-credentials.sh admin-token                          # turns pairing on (prints nothing secret)
relay/gen-credentials.sh env                                  # the lines for Dokploy: secret, paste only there
```

Compute nodes need no static client: pairing makes theirs. (`relay/README.md` §1 is the manual path, with a static
`add-client compute office-pc 7001`.) `relay/gen-credentials.sh show` lists the clients without passwords.

## 2. Dokploy (once, in its web UI)

Follow `relay/README.md` §2, steps 1–8: a project `mu`, a Compose service `relay` on the server
`quarve-8gb-fsn1-2`, provider GitHub (`Dokploy-martand`, repo `mu`, branch `main`, Compose Path
`relay/compose.yaml`, **Watch Paths `relay/**`**), the Environment from step 1, the domain `relay.martand.systems`
on container port 8080 with HTTPS and Let's Encrypt and the middleware `mu-relay-inflight@docker`, then **Deploy**.
Leave *Isolated Deployment* and *Randomize Compose* off and add no Mounts: either would give the relay a new, empty
volume. The DNS record (`A relay → 195.201.236.190`, at Porkbun) already exists.

## 3. Check it, and log the Mac in

```sh
curl -sS https://relay.martand.systems/health      # OK
curl -sS https://relay.martand.systems/mu/v1/      # {"ok":true,"pairing":true,"protocol":1,"service":"mu-relay","store":"ok"}
```

`"pairing": false`, or a 404 on `/mu/v1/`: the deploy did not take the admin token (check the Environment tab, and
the container log for `mu-relayd: …` lines). `"store":"unwritable"` or `"damaged"`: `relay/README.md` §6.2.

Then store the admin token and the Mac's relay client in the Mac's Keychain:

```sh
relay/gen-credentials.sh login-bundle avi-mac | mu relay login --stdin
mu relay status
```

`mu relay login` prints the relay, its fingerprint and `admin token, and this computer's relay client avi-mac, stored
in the login Keychain`; a wrong token stops with exit 5 (`relay-admin`) and stores nothing. `mu relay status` prints
`relay https://relay.martand.systems: pairing on (store ok, id …); this computer: avi-mac` and one line per client.

This Mac can now pair compute nodes (`mu compute invite`, [windows-compute-node.md](windows-compute-node.md) §9).

## 4. Day to day

| Task | Command (Mac Terminal) |
|---|---|
| Is it up, is pairing on | `mu relay status` (exit 5 and `the relay's store is new (lost volume?): mu relay restore` if the volume was replaced) |
| Which clients exist | `mu relay clients` (names, roles, ports; never a password) |
| Back up the clients made by pairing | `mu relay backup` (also taken by `mu relay login`, after each pairing, removal and restore) |
| Remove a client now | `mu relay remove NAME`. chisel restarts, so that client's sessions end at once; every other tunnel reconnects within about 2 s |
| Remove a compute node | `mu compute remove NODE` (also deletes its relay client; `--keep-relay-client` leaves it) |

## 5. Rotate and revoke

After any change to `relay.env`, paste `relay/gen-credentials.sh env` into Dokploy's Environment tab, **Save**,
**Deploy**. Docker replaces the container; every tunnel drops for about 13 s and reconnects by itself (measured in the
replica, `relay/README.md` §1).

| Task | Commands |
|---|---|
| New password for a static client | `relay/gen-credentials.sh rotate-client NAME`, then `env` and the Dokploy edit; the client needs its new settings (`client NAME`) |
| New relay key | `relay/gen-credentials.sh rotate-key`, then the Dokploy edit. Every client then needs the new fingerprint, and the paired nodes and Macs have the old one pinned: pair each node again (`mu compute invite NODE --replace`, a visit to the PC) or use the manual path. Not tested; avoid unless the key leaked |
| New admin token | `relay/gen-credentials.sh admin-token --rotate`, the Dokploy edit, then `relay/gen-credentials.sh login-bundle avi-mac \| mu relay login --stdin` again. Clients and pairings are not affected |
| Revoke a lost Mac or node for good | `relay/gen-credentials.sh revoke NAME` (and `admin-token --rotate` if the lost Mac held the token), then the Dokploy edit. The name can never connect again, even if the volume is lost. Then revoke its key on each node: `wsl -d mu-compute -u root -- /usr/local/sbin/mu-wsl2-distro revoke NAME` at the PC |

## 6. The volume

The clients made by pairing live on the named volume `<appName>_relay-state`. Dokploy keeps it across deploys,
pushes and restarts. It is lost only by deleting the service with *delete volumes* ticked, a deploy with *fresh
volumes*, turning on *Isolated Deployment* or *Randomize Compose*, or moving to a new server. Then the relay starts
with an empty store: static clients keep working, paired clients are refused until restored. On the Mac:

```sh
mu relay status            # says the store is new
mu relay restore --dry-run # what it would put back from the Mac's backup
mu relay restore           # puts every client, grant and tombstone back; no visit to the PC
```

Details, and the damaged-store case: `relay/README.md` §6.6.

## 7. Turning pairing off, removing the relay

- **Pairing off:** remove `RELAY_ADMIN_TOKEN` from the Environment, Deploy. The relay is chisel only, with the static
  clients; every paired client stops working until the token comes back (the volume stays).
- **Removing it:** delete the Dokploy service with *delete volumes* ticked. Nothing else on the server was changed.
