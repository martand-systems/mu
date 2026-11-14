# μ relay (Stage A): chisel behind the server's Traefik

One container on Martand's Docker server (PLAN §7). The office PC and the laptop each open an outbound
`wss://relay.martand.systems` connection on 443; the relay joins the two. It publishes no host port. Dokploy builds it
from this folder and deploys it; nobody logs in to the server.

With `RELAY_ADMIN_TOKEN` set, the container also runs **mu-relayd**, the pairing service (`docs/design/pairing.md`):
`mu compute invite` on the Mac and one line at the PC pair a node by one code, and relay clients are added and removed
through its API instead of Dokploy edits. It answers `https://relay.martand.systems/mu/v1/` on the same route (chisel
passes every request that is not a tunnel to it) and keeps its state on one named volume. Without the token the
container is exactly the chisel-only relay described below.

| File | What it is |
|---|---|
| `Dockerfile` | Alpine pinned by digest, chisel 1.12.0 pinned by SHA-256, mu-relayd built from `control/` (Go, standard library only, nothing downloaded), runs as uid 65532 |
| `compose.yaml` | The Dokploy Compose service. No routers or ports: Dokploy adds those labels and `dokploy-network`. Its own labels define the connection limit. One named volume, `relay-state`, for the pairing state |
| `entrypoint.sh` | Turns `RELAY_KEY` and `RELAY_CLIENTS` into chisel's key and its per-client access rules; applies `RELAY_REVOKED`; starts mu-relayd when `RELAY_ADMIN_TOKEN` is set |
| `control/` | mu-relayd: the pairing and control API, chisel's auth file, chisel's supervisor (`go test ./...`; `must-fail.sh` runs every test with its fault) |
| `gen-credentials.sh` | Makes and keeps the credentials, the admin token and the revoked list (bash and openssl only) |
| `.env.example` | The shape of the variables. Placeholders only |

What each client may do is fixed by its entry in `RELAY_CLIENTS`:

- a **compute** client may open one reverse tunnel, listening on `127.0.0.1:<its port>` inside the relay container;
- a **workstation** client may connect to the listed compute ports on that loopback, and to nothing else.

So relay credentials do not give access to the other containers on `dokploy-network`, to Traefik's API or to the
internet, and a neighbouring container cannot reach a compute node's port. `tools/s4/run.sh` tests this with
chisel's own client and with a client written to ask for tunnels it did not declare. Clients that mu-relayd adds get
exactly the same two rules (rows P5), and its auth file always holds a sentinel user that nobody can log in as, because
chisel lets anyone in when its user list is empty (row P11).

What the relay does **not** give, in Stage A:

- **Secrecy from the server.** The relay joins two plain TCP streams, so the relay container, and anyone with root
  or Docker access on the server, can read everything that passes through it. Traefik and the network in between
  see only chisel's encrypted stream, as long as clients pin the fingerprint. The pinned fingerprint tells a client
  it is talking to this relay; it hides nothing from the relay. Job data is private from the server only once the
  agent's own TLS (PLAN §7.2) runs inside the tunnel.
- **Proof of who is at the other end.** Someone holding the compute node's credentials can take its place while it
  is offline. That, too, is what the agent's pinned certificate is for.
- **Protection against being knocked over.** chisel keeps a connection that never logs in open for ever. Through
  Traefik the limit below (32 at a time per client address) holds that back; a container on `dokploy-network`
  reaches the relay's port directly, with no limit. Enough such connections fill the relay's 256 MiB, Docker
  restarts it, and every tunnel reconnects. Nothing is exposed by that, but the relay can be made unavailable.

## 1. Credentials (on the Mac, once)

The state is one file, `~/.config/mu/relay.env` (mode 600). It is never inside a git checkout.

```sh
relay/gen-credentials.sh init                                 # the relay's key; prints its fingerprint
relay/gen-credentials.sh add-client compute office-pc 7001    # prints the office PC's settings
relay/gen-credentials.sh add-client workstation avi-mac 7001  # prints the Mac's settings
relay/gen-credentials.sh env                                  # the two lines for Dokploy (secret)
```

Compute ports are 7001–7999, one per compute node, written without a leading zero.

`client NAME` prints a client's settings again; `show` lists clients without passwords. After any later change
(`add-client`, `rotate-client`, `remove-client`, `rotate-key`): paste `env` into Dokploy again, Save, Deploy.
The changed environment makes Docker replace the container, so every tunnel drops for about 13 s and the clients
reconnect by themselves; a removed or rotated client is cut off at that moment. Only `rotate-key` changes the
fingerprint.

The two values are also on the server once deployed: in `relay/.env` in Dokploy's checkout and in the container's
configuration (`docker inspect`). Whoever has root or Docker access there has them.

## 2. Dokploy (in the UI, once)

1. **Projects → Create Project**: name `mu`. Open it.
2. **Create Service → Compose**: name `relay`, **server `quarve-8gb-fsn1-2`** (left empty it deploys on the main
   Dokploy host), Compose Type **Docker Compose** (not Stack).
3. **General → Provider → GitHub**: account `Dokploy-martand`, repository `mu`, branch `main`, Compose Path
   `relay/compose.yaml`, Trigger Type On Push, **Watch Paths `relay/**`** (without it every push to the repo
   redeploys the relay and drops the tunnels for a few seconds). Save.
4. **Environment**: paste the two lines printed by `relay/gen-credentials.sh env`. Leave *Create Environment File*
   on. Save.
5. **Domains → Add Domain**: Service Name `mu-relay`, Host `relay.martand.systems`, Path `/`, Internal Path `/`,
   Strip Path off, Container Port `8080`, Custom Entrypoint off, **HTTPS on, Certificate Provider Let's Encrypt**
   (with "None", Traefik serves its self-signed certificate). **Middlewares: type `mu-relay-inflight@docker` and
   press Add.** That is the connection limit `compose.yaml` defines: at most 32 connections at a time from one
   client address (an IPv6 /64 counts as one), `429` above that. Spelled wrongly, Traefik cannot build the route
   and the host answers `404`. Create.
6. **Advanced**: leave *Isolated Deployment* and *Randomize Compose* off, and add no Mounts (a mount moves the
   build context to the repo root).
7. **General → Preview Compose**: the service must now carry `traefik.enable=true`, two routers with
   ``Host(`relay.martand.systems`)``, `loadbalancer.server.port=8080`, `tls.certresolver=letsencrypt`,
   `…-websecure.middlewares=mu-relay-inflight@docker` next to the three
   `traefik.http.middlewares.mu-relay-inflight…` labels from `compose.yaml`, and the networks `dokploy-network`
   and `default`.
8. **Deploy**. The deployment log ends with `Docker Compose Deployed: ✅`.

Do not press *Traefik → Update* or *Reload* on that server for this: it restarts Traefik and drops every connection
on the server for some seconds.

The connection limit counts per client address as Traefik sees it. `relay.martand.systems` therefore has to point
straight at the server (a plain DNS record, no CDN or proxy in front): behind a proxy all clients would share one
address and one limit of 32. An office behind one public address shares one limit too.

## 3. Check

```sh
curl -sS https://relay.martand.systems/health      # OK, with a valid Let's Encrypt certificate
curl -sI http://relay.martand.systems/health       # a redirect to https
```

For the first minute after the first deploy Traefik may still serve its default certificate while Let's Encrypt
issues the real one. If it does not arrive, read Traefik's log before deploying again: Let's Encrypt allows five
failed validations per host name per hour.

The relay's log (Logs tab, container `…-mu-relay-1`) starts with the client names and the fingerprint. It never
prints a password or the key.

Pressing Deploy again with nothing changed does not restart the relay: Docker keeps a container whose image and
environment are the same. No `Disconnected` in the client logs then means "not replaced", not "reconnected".

## 4. Clients

chisel 1.12.0, the same release as the relay (`https://github.com/jpillora/chisel/releases/tag/v1.12.0`). The
password goes in through the `AUTH` variable so that it is not in the process list. Always pass `--fingerprint`;
never use `--tls-skip-verify`.

Office PC (in the `mu-compute` distro, as `mu`), exposing an agent on its `127.0.0.1:<agent port>`:

```sh
AUTH='office-pc:<RELAY_PASSWORD>' CHISEL_WS_TIMEOUT=10s chisel client --fingerprint '<RELAY_FINGERPRINT>' \
  --keepalive 25s --max-retry-interval 15s \
  https://relay.martand.systems R:127.0.0.1:7001:127.0.0.1:<agent port>
```

Mac, making the agent reachable on its own `127.0.0.1:7001`:

```sh
AUTH='avi-mac:<RELAY_PASSWORD>' CHISEL_WS_TIMEOUT=10s chisel client --fingerprint '<RELAY_FINGERPRINT>' \
  --keepalive 25s --max-retry-interval 15s \
  https://relay.martand.systems 127.0.0.1:7001:127.0.0.1:7001
```

Keep all three settings:

- `--keepalive 25s` keeps NAT and carrier idle timers from cutting the tunnel, and is what notices a dead link.
- `--max-retry-interval 15s`: chisel's default is 5 minutes, which is how long the PC could stay unreachable after
  an outage.
- `CHISEL_WS_TIMEOUT=10s`: after a redeploy Traefik routes to the removed container for about 2 s more, and a
  reconnect attempt that lands there waits for Traefik's 30 s dial timeout. With chisel's default (45 s) the tunnel
  is back after 33 s; with 10 s it is back after about 13 s.

What to expect when something breaks (measured in the replica, `docs/verification/2026-11-13-S4-relay.md`):

- relay redeployed or Traefik restarted: the tunnel is back about 13 s later;
- a link that dies without a close (power, cable, NAT): noticed by the keepalive after up to 50 s;
- the compute node dies without a close and is back within that time: the relay still holds its old session and
  refuses the new one ("Server cannot listen") until the 50 s are over. The client keeps trying; about a minute.

The traffic inside the tunnel is plain TCP between the two loopbacks: the agent's own TLS and token (PLAN §7.2)
go on top of it. Until they exist, expose nothing on the compute port that the server's administrators and the
holders of the workstation credentials may not read.

## 5. Changing it

- **chisel version:** change `CHISEL_VERSION` and both sums in `Dockerfile`, push to a branch, and let the
  `s4-relay` workflow pass before it reaches the deploy branch.
- **Testing:** `tools/s4/test-config.sh` (no Docker), `relay/control` (`go test ./...`, and `must-fail.sh`) and
  `tools/s4/run.sh` (a replica of the server's Traefik on the local Docker; never on the server). CI runs both on every branch push that touches `relay/` or `tools/s4/`,
  with 4 minutes of idle time. The long run (20 minutes idle on every Traefik version, and the credential tests on
  macOS) starts from *Run workflow* with idle 1200, or with `[s4-long]` in the pushed commit's message. Run it
  before a new chisel or Traefik version is deployed.
- **Removing it:** delete the Dokploy service, with *delete volumes* ticked (the pairing state volume). Nothing else
  on the server was changed.

## 6. The pairing service (mu-relayd)

### 6.1 Turning it on: the one Dokploy edit

On the Mac, in Terminal (not through Claude Code: the token must not pass through its transcript):

```sh
relay/gen-credentials.sh admin-token        # adds RELAY_ADMIN_TOKEN to ~/.config/mu/relay.env; prints nothing secret
relay/gen-credentials.sh env                # now three lines (four with RELAY_REVOKED)
```

In Dokploy: the service's **Environment** tab, replace the lines with the new ones, **Save**, **Deploy**. Nothing else
changes: not the Domains entry, not Advanced (leave *Isolated Deployment* and *Randomize Compose* off and add no
Mounts). The changed environment makes Docker replace the container; it starts mu-relayd, which creates its store on
the volume and starts chisel as its child.

### 6.2 Checking it

```sh
curl -sS https://relay.martand.systems/health      # OK (now answered by mu-relayd while chisel runs)
curl -sS https://relay.martand.systems/mu/v1/      # {"ok":true,"pairing":true,"protocol":1,"service":"mu-relay","store":"ok"}
```

`"store":"unwritable"` means the volume is not writable by uid 65532 and `"damaged"` that its files could not be read:
the static clients keep working either way, and pairing is off until it is fixed (§6.5). The container log starts
with `relay: pairing mode; …` and `mu-relayd: store <id> (ok), N client(s) in the auth file`; each change is one line
(`invite 7K3M claimed from=…`, `client office-pc removed`, `chisel restarted (…)`), never a password, token or code.
On the Mac, `relay/gen-credentials.sh login-bundle avi-mac | mu relay login --stdin` stores the token in the Keychain
and checks it (`GET /mu/v1/clients`).

### 6.3 What it changes

- **Static clients** (`RELAY_CLIENTS`) work as before. mu-relayd never changes them and never copies their passwords.
- **Dynamic clients** are made by pairing (`mu compute invite`) or by the API (`PUT /mu/v1/clients/NAME`); each has the
  password its node chose. They get exactly the rules above. A new client is accepted by chisel within a second,
  without a restart and without touching other tunnels (chisel 1.12.0 reloads its auth file by itself).
- **Removing a client** (`mu relay remove NAME`, `mu compute remove NAME --relay`, or `DELETE /mu/v1/clients/NAME`)
  restarts chisel once, at most every 10 s, because chisel keeps a removed user's open tunnels otherwise: every tunnel
  drops for about a second and reconnects by itself. Removing a static client leaves a tombstone in the store; the
  last client cannot be removed (409 `last-client`).
- A static workstation that pairs a second node is given the new port as a **grant** (its password stays the static
  one). Deleting a compute client takes its port away from every workstation.

### 6.4 Rotating the admin token

```sh
relay/gen-credentials.sh admin-token --rotate
relay/gen-credentials.sh env                 # paste into Dokploy, Save, Deploy
relay/gen-credentials.sh login-bundle avi-mac | mu relay login --stdin
```

The old token stops working at the deploy. Clients and pairings are not affected (they are on the volume).

### 6.5 Revoking a client, also without the Mac

With the Mac: `mu relay remove NAME` (immediate). Without it (a stolen Mac, a lost laptop), or to be sure it holds even
if the volume is lost:

```sh
relay/gen-credentials.sh revoke NAME          # adds NAME to RELAY_REVOKED
relay/gen-credentials.sh admin-token --rotate # if the Mac that held the token is gone
relay/gen-credentials.sh env                  # paste into Dokploy, Save, Deploy
```

At every start the entrypoint drops a revoked name from the static list and mu-relayd deletes any dynamic client,
grant or tombstone of that name; the API refuses to create it again. `unrevoke NAME` takes it off the list. Then revoke
the client's keys on the nodes (`mu-wsl2-distro revoke NAME` as root on the PC, or the job layer's procedure).

### 6.6 The volume, and losing it

Docker names it `<appName>_relay-state` (Dokploy's project name). Dokploy keeps it across deploys, pushes and
restarts. It is lost only by: deleting the service with *delete volumes* ticked, a deploy with *fresh volumes*,
turning on *Isolated Deployment* (with isolated volumes) or *Randomize Compose*, or a new server. Then mu-relayd starts
with a new, empty store (a new `store_id`): every **dynamic** client is refused (`Authentication failed`) until it is
put back, the static clients keep working, tombstones are gone (that is what `RELAY_REVOKED` is for), and open invites
are gone (make new ones). On the Mac, `mu relay status` notices the new store and `mu relay restore` puts every client,
grant and tombstone back from the Mac's backup; no visit to the PC.

If the store's files are damaged (both `state.json` and `state.json.prev` unreadable), pairing answers 503
`store-damaged` and the static clients keep working; `POST /mu/v1/store/reset` (admin) moves the files aside and starts
a new store, then `mu relay restore`.

### 6.7 Turning it off

Remove `RELAY_ADMIN_TOKEN` in Dokploy and deploy: the container is the chisel-only relay again, with the static
clients only; every dynamic client stops working (the volume stays, unused, and comes back if the token does).

### 6.8 The API

`docs/design/pairing.md` §6.2 is the reference (endpoints, authentication, errors, limits). Clients act on the `error`
slug only; the `message` is for people reading logs and is never shown by μ.
