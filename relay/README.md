# μ relay (Stage A): chisel behind the server's Traefik

One container on Martand's Docker server (PLAN §7). The office PC and the laptop each open an outbound
`wss://relay.martand.systems` connection on 443; the relay joins the two. It stores nothing, publishes no host port
and has no volume. Dokploy builds it from this folder and deploys it; nobody logs in to the server.

| File | What it is |
|---|---|
| `Dockerfile` | Alpine pinned by digest, chisel 1.12.0 pinned by SHA-256, runs as uid 65532 |
| `compose.yaml` | The Dokploy Compose service. No routers, ports or volumes: Dokploy adds those labels and `dokploy-network`. Its own labels define the connection limit |
| `entrypoint.sh` | Turns `RELAY_KEY` and `RELAY_CLIENTS` into chisel's key and its per-client access rules |
| `gen-credentials.sh` | Makes and keeps the credentials (bash and openssl only) |
| `.env.example` | The shape of the two variables. Placeholders only |

What each client may do is fixed by its entry in `RELAY_CLIENTS`:

- a **compute** client may open one reverse tunnel, listening on `127.0.0.1:<its port>` inside the relay container;
- a **workstation** client may connect to the listed compute ports on that loopback, and to nothing else.

So relay credentials do not give access to the other containers on `dokploy-network`, to Traefik's API or to the
internet, and a neighbouring container cannot reach a compute node's port. `tools/s4/run.sh` tests this with
chisel's own client and with a client written to ask for tunnels it did not declare.

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
- **Testing:** `tools/s4/test-config.sh` (no Docker) and `tools/s4/run.sh` (a replica of the server's Traefik on
  the local Docker; never on the server). CI runs both on every branch push that touches `relay/` or `tools/s4/`,
  with 4 minutes of idle time. The long run (20 minutes idle on every Traefik version, and the credential tests on
  macOS) starts from *Run workflow* with idle 1200, or with `[s4-long]` in the pushed commit's message. Run it
  before a new chisel or Traefik version is deployed.
- **Removing it:** delete the Dokploy service. Nothing else on the server was changed.
