# μ relay (Stage A): chisel behind the server's Traefik

One container on Martand's Docker server (PLAN §7). The office PC and the laptop each open an outbound
`wss://relay.martand.systems` connection on 443; the relay joins the two. It stores nothing, publishes no host port
and has no volume. Dokploy builds it from this folder and deploys it; nobody logs in to the server.

| File | What it is |
|---|---|
| `Dockerfile` | Alpine pinned by digest, chisel 1.12.0 pinned by SHA-256, runs as uid 65532 |
| `compose.yaml` | The Dokploy Compose service. No labels, ports or volumes: Dokploy adds the Traefik labels and `dokploy-network` |
| `entrypoint.sh` | Turns `RELAY_KEY` and `RELAY_CLIENTS` into chisel's key and its per-client access rules |
| `gen-credentials.sh` | Makes and keeps the credentials (bash and openssl only) |
| `.env.example` | The shape of the two variables. Placeholders only |

What each client may do is fixed by its entry in `RELAY_CLIENTS`:

- a **compute** client may open one reverse tunnel, listening on `127.0.0.1:<its port>` inside the relay container;
- a **workstation** client may connect to the listed compute ports on that loopback, and to nothing else.

So relay credentials do not give access to the other containers on `dokploy-network`, to Traefik's API or to the
internet, and a neighbouring container cannot reach a compute node's port. `tools/s4/run.sh` tests this.

## 1. Credentials (on the Mac, once)

The state is one file, `~/.config/mu/relay.env` (mode 600). It is never inside a git checkout.

```sh
relay/gen-credentials.sh init                                 # the relay's key; prints its fingerprint
relay/gen-credentials.sh add-client compute office-pc 7001    # prints the office PC's settings
relay/gen-credentials.sh add-client workstation avi-mac 7001  # prints the Mac's settings
relay/gen-credentials.sh env                                  # the two lines for Dokploy (secret)
```

`client NAME` prints a client's settings again; `show` lists clients without passwords. After any later change
(`add-client`, `rotate-client`, `remove-client`, `rotate-key`): paste `env` into Dokploy again, Save, Deploy.
Connected clients reconnect by themselves. Only `rotate-key` changes the fingerprint.

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
   (with "None", Traefik serves its self-signed certificate), no middlewares. Create.
6. **Advanced**: leave *Isolated Deployment* and *Randomize Compose* off, and add no Mounts (a mount moves the
   build context to the repo root).
7. **General → Preview Compose**: the service must now carry `traefik.enable=true`, two routers with
   ``Host(`relay.martand.systems`)``, `loadbalancer.server.port=8080`, `tls.certresolver=letsencrypt`, and the
   networks `dokploy-network` and `default`.
8. **Deploy**. The deployment log ends with `Docker Compose Deployed: ✅`.

Do not press *Traefik → Update* or *Reload* on that server for this: it restarts Traefik and drops every connection
on the server for some seconds.

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

## 4. Clients

chisel 1.12.0, the same release as the relay (`https://github.com/jpillora/chisel/releases/tag/v1.12.0`). The
password goes in through the `AUTH` variable so that it is not in the process list. Always pass `--fingerprint`;
never use `--tls-skip-verify`.

Office PC (in the `mu-compute` distro, as `mu`), exposing an agent on its `127.0.0.1:<agent port>`:

```sh
AUTH='office-pc:<RELAY_PASSWORD>' chisel client --fingerprint '<RELAY_FINGERPRINT>' \
  --keepalive 25s --max-retry-interval 15s \
  https://relay.martand.systems R:127.0.0.1:7001:127.0.0.1:<agent port>
```

Mac, making the agent reachable on its own `127.0.0.1:7001`:

```sh
AUTH='avi-mac:<RELAY_PASSWORD>' chisel client --fingerprint '<RELAY_FINGERPRINT>' \
  --keepalive 25s --max-retry-interval 15s \
  https://relay.martand.systems 127.0.0.1:7001:127.0.0.1:7001
```

Keep `--keepalive 25s` (it is what keeps NAT and carrier idle timers from cutting the tunnel, and what notices a
dead link) and `--max-retry-interval 15s` (chisel's default is 5 minutes, which is how long the PC could stay
unreachable after a relay redeploy).

The traffic inside the tunnel is plain TCP between the two loopbacks: the agent's own TLS and token (PLAN §7.2)
go on top of it.

## 5. Changing it

- **chisel version:** change `CHISEL_VERSION` and both sums in `Dockerfile`, push to a branch, and let the
  `s4-relay` workflow pass before it reaches the deploy branch.
- **Testing:** `tools/s4/test-config.sh` (no Docker) and `tools/s4/run.sh` (a replica of the server's Traefik on
  the local Docker; never on the server). CI runs both on every push that touches `relay/` or `tools/s4/`.
- **Removing it:** delete the Dokploy service. Nothing else on the server was changed.
