# μ job layer, first version: OpenSSH inside the relay tunnel

The specification for stage 1 of `docs/PLAN.md` §6.4a. It replaces the design note of 2026-11-13 and corrects it
with what was measured on 2026-11-14 (§17) and read in sources (§18). Stage 1 is implemented on branch `job-layer`
and proven in CI (§21, which also lists where the implementation differs from the text above); the root steps of §13
are in the host script, which CI uses to make its node (§22). Nothing has run on the office PC. What can only be
checked at the PC is listed in §19; the owner's visit is `docs/design/first-job-checklist.md`.

Sections: [1 Summary](#1-summary) · [2 Architecture](#2-architecture) · [3 Where the code lives](#3-where-the-code-lives) ·
[4 Workstation](#4-workstation-files-and-commands) · [5 The connection](#5-the-connection) · [6 Upload](#6-upload-a-git-snapshot) ·
[7 Protocol](#7-protocol-gate-verbs-records) · [8 Node](#8-node-layout-runner-job-units) ·
[9 Durability](#9-durability-and-recovery) · [10 Limits](#10-limits-and-queue) · [11 Outputs](#11-outputs-logs-run-records) ·
[12 Versions](#12-toolchain-versions) · [13 Root steps](#13-what-needs-root-on-the-node) · [14 Security](#14-who-can-do-what) ·
[15 Stages](#15-stages) · [16 Tests](#16-tests-and-how-each-is-shown-to-fail) · [17 Verified](#17-verified-by-experiment) ·
[18 Read](#18-read-in-sources-not-run) · [19 PC](#19-still-unverified-until-the-pc) · [20 Changes](#20-changes-from-the-design-note-and-from-plan-v6) ·
[21 Implemented](#21-implemented-stage-1-in-ci) · [22 Host script](#22-the-host-script-13-in-mu-wsl2-distrosh-in-ci)

## 1. Summary

- Transport, encryption, node identity and workstation credentials come from OpenSSH. A dedicated `sshd` in the
  distro listens on `127.0.0.1:2222` only, key-only, for the user `mu`, behind a forced command (the gate).
- The laptop reaches it through the relay: `ssh` with `ProxyCommand mu compute proxy <node>`, which runs
  `chisel client … stdio:127.0.0.1:7001`. The node's tunnel client holds `R:127.0.0.1:7001:127.0.0.1:2222`.
- Upload is a git snapshot commit pushed to a bare repo on the node. A job is a `systemd-run --user` unit with a
  memory and a time limit. One job runs at a time; the queue is on the node, first in first out.
- A job is finished only when a checksummed `result.json` is durably written. Anything else found after a stop is
  re-run once, then marked `interrupted`.
- **A paired workstation key can run any command as `mu` on the node, because that is what a job is.** The gate
  is a protocol boundary and an audit point, not a sandbox (§14).

## 2. Architecture

```
laptop: mu submit / status / log / fetch        relay.martand.systems           office PC, distro mu-compute
 ssh -F $MU_HOME/compute/office-pc/ssh_config    Traefik :443 (TLS)             mu-tunnel.service (chisel client)
   ProxyCommand: mu compute proxy office-pc                                       R:127.0.0.1:7001:127.0.0.1:2222
     chisel client … stdio:127.0.0.1:7001 ──wss──► mu-relay ◄──wss────────────────┘
        │                                    (chisel server, forwards only)
        └─────────── OpenSSH session, end to end, host key pinned ──────────────► mu-sshd.service 127.0.0.1:2222
                                                                                   ForceCommand mu-node-gate
                                                                                   └ mu node serve <verb>  (user mu)
                                                                                 mu-node.service (runner, user mu)
                                                                                   └ mu-job-<id>.service (one job)
```

| Layer | Ends at | Protects |
|---|---|---|
| TLS (Let's Encrypt) | Traefik on the server | the WebSocket from the networks in between |
| chisel's SSH layer, relay fingerprint pinned | the relay container | the relay's client credentials; the relay sees what is inside |
| OpenSSH, laptop ↔ node, host key pinned at pairing | `mu-sshd` in the distro | job data. The relay and Traefik see only ciphertext |

A compromised relay, or a stolen relay credential, can reach the node's `sshd` login and nothing more. It cannot
pass as the node: the host key is pinned.

## 3. Where the code lives

| File | What |
|---|---|
| `bin/mu` (rev 7) | sends `compute`, `submit`, `status`, `log`, `fetch`, `cancel`, `node` to the **default** toolchain's Python; reads the recorded Windows drive in `mu_disk_free` (§13.4) |
| `src/mu/cli.py` | entry point of those commands: puts `recipe/src` on `sys.path`, parses arguments, `--json`, exit codes |
| `src/mu/jobs.py` | workstation side: pairing, ssh invocation, the proxy, snapshot, submit, status, log, fetch, cancel, run records |
| `src/mu/node.py` | node side: `serve` (the verbs), `run` (the runner), `run-job` (the shim), `enable`, `disable`, `selftest` |
| `src/mu/durable.py` | `write_durable`, `seal`, `read_record` (§9), used by both sides; the test faults |
| `src/mu/proto.py` | what both sides share: protocol number, exit codes, name rules, job id, globs, `MuError` |
| `install/hosts/wsl2/mu-node-gate` | the forced command, POSIX `sh`, installed root-owned (§7.1); the host script replaces `@MU_BIN@` and `@MU_KEYS@` |
| `install/hosts/wsl2/mu-wsl2-distro.sh` | new root steps (§13) and the commands `authorize`, `revoke`, `keys`, `host-key`, `tunnel credential`, `tunnel status`, `windows-reach` (§22) |
| `install/hosts/wsl2/mu-wsl2-host.ps1` | passes the distro's drive letter to the distro script (§13.4) and copies the gate's source in; nothing else changes on Windows |
| `docs/design/first-job-checklist.md` | the owner's visit: one sequence, with the expected output of each step |
| `toolchain/apps.toml`, `install/install.sh` | the chisel client, pinned (§5.4) |
| `tools/test-jobs/` | tests (§16); `.github/workflows/job-layer.yml` runs them on branch pushes |

**Infrastructure commands never use the project's pin.** The job layer is new, and a project may pin a version that
has none of it (`jatayu` pins v2026.11.4). Both machines follow the latest release, so the default versions agree,
and `hello` checks the protocol number. Only the job itself runs under the pin (`MU_VERSION=<pin> mu run …`).

Dispatcher routing, bash 3.2 compatible:

```sh
infra() {   # commands of the job layer: always the default toolchain, never the project's pin or $MU_VERSION
  local d py f; d="$(default_version)"
  [ -n "$d" ] || die "no μ toolchain is installed yet. In a clone of martand-systems/mu, run: ./install.sh" 3
  installed "$d" || die "the default μ $d is not installed completely. Reinstall it: mu install $d --force" 3
  py="$TC/$d/env/bin/python"; f="$TC/$d/recipe/src/mu/cli.py"
  [ -x "$py" ] || die "μ $d has no Python at $(tilde "$py"). Reinstall it: mu install $d --force" 3
  [ -f "$f" ] || die "μ $d has no job commands (they start with the release after v2026.11.6). Run: mu update" 3
  MU_INFRA_VERSION="$d" exec "$py" -I -S "$f" "$@"
}
…
  compute|submit|status|log|fetch|cancel|node) infra "$cmd" "$@" ;;
```

- **`-I -S` is required, not a nicety.** The toolchain's Python took 1.1 s to start with its site packages (a `.pth`
  file imports the `cad` package at every start) and 0.04 s with `-S`, in the same minute (§17 F). The job layer imports only the
  standard library, so it needs nothing from site packages. With `-I` the script's folder is not on `sys.path`;
  `cli.py` inserts `recipe/src` itself (checked: `python -I -S cli.py` imports `mu.jobs`, no site-packages path).
- `lib/mu-env.sh` is **not** sourced: it redirects `XDG_CONFIG_HOME`, `TMPDIR` and `PATH`, and `systemctl --user`,
  `ssh` and `git` must see the caller's environment. Unit files go to `~/.config/systemd/user`, taken from the
  password database's home, not from `XDG_CONFIG_HOME`.
- The code runs on Python 3.11 or later (the toolchain has 3.13; CI may use the runner's). Standard library only.
- `git` is found as the dispatcher's `find_git` does: the first `git` on `PATH` that answers `--version`. On this
  Mac `/usr/bin/git` exits 69 until the Xcode licence is accepted, and Homebrew's works.
- `mu status` was free: `mu` alone prints the dispatcher's status, and `mu status` reached the toolchain as an
  unknown command.

## 4. Workstation: files and commands

### 4.1 Files

```
$MU_HOME/compute/<node>/       0700
  node.conf                    key=value, read as text: name, relay_url, relay_fingerprint, relay_user, relay_port,
                               secret_store (keychain|file), host_key_fingerprint, added
  id_ed25519, id_ed25519.pub   this workstation's key for this node (0600; made by ssh-keygen, no passphrase)
  known_hosts                  one line: mu-<node> ssh-ed25519 AAAA…
  ssh_config                   generated (§5.1); used with ssh -F, so the user's own configuration never applies
  relay.auth                   only with secret_store=file: "<relay_user>:<password>", 0600
  last-status.json             the last `hello` answer and when (for `mu compute list`)
$MU_HOME/run/                  0700: ssh control sockets, one proxy status file per ssh call
$MU_HOME/logs/compute-<node>.log   chisel's log lines; cut to its last 256 kB when it passes 1 MB
<project>/.mu/jobs/<job-id>.json   what this workstation knew at submit (sealed record, §9); `.mu/` is never uploaded
<project>/runs/<job-id>.json       the run record, written at fetch (§11); tracked by git
```

Node names: `[a-z0-9][a-z0-9-]{0,31}`, the relay's rule for client names. `local` and `auto` are reserved.

### 4.2 Commands

Every command takes `--json` (one JSON object on stdout, nothing else there) and uses these exit codes:

| Exit | Meaning | What to do next (the message says it) |
|---|---|---|
| 0 | done | |
| 1 | unexpected error | read the message; `mu compute status <node>` |
| 2 | wrong usage, or the project cannot be submitted as it is | fix the arguments, `mu.toml` or the repository |
| 3 | toolchain version: none installed here, or the node lacks the project's pin | `mu install <v>` here; on the node `mu install <v>` (stage 2: `mu compute install`) |
| 4 | unreachable: relay down, node offline, timeout, link lost | retry; `mu compute status`; the job, if accepted, is unaffected |
| 5 | refused: host key differs, key not authorised, relay credential, fingerprint or rule refused, protocol mismatch | **never retried, never a fallback.** The message names which |
| 6 | the node cannot take the job: disk below `min_free_gb`, memory limit not enforceable, snapshot too large | free space or fix the node, then submit again |
| 7 | the job ended, not as `succeeded` (only from `--wait`) | `mu log <id> --tail 50` |
| 8 | integrity: a fetched output does not match the result record | fetch again; if it repeats, the node's copy changed after the job |
| 9 | no such node or job | `mu compute list`, `mu status` |
| 10 | still running after `--timeout` (`mu status <id> --wait --timeout S`, `mu submit --wait --timeout S`); the job goes on | ask again: `mu status <id> --wait --timeout S`, or `mu log <id> --tail 20` |

Every error carries `exit`, `error`, `message` and `next` in its JSON. `next` is always the workstation's own text
for that error: what a node says goes to `node_message` (cleaned of control characters, capped), never to `next`,
`exit` or `ok` (§23, finding S4). Once a submit has a job id, every error carries `job_id`, and `next` says `mu
submit --resume <id>`. The dispatcher's own refusals (no toolchain, no job layer) answer the same JSON with `--json`.

| Command | Network | What it does |
|---|---|---|
| `mu compute add <node> --host-key 'ssh-ed25519 AAAA…' --relay URL --relay-fingerprint FP --relay-user NAME [--relay-port 7001] [--secret-store keychain\|file] --relay-password-stdin` | none | Makes the folder and the key, writes `known_hosts`, `ssh_config`, `node.conf`, stores the relay password (§5.3; read from stdin, or from `MU_RELAY_PASSWORD`; never an argument). Prints the workstation's public key, its SHA256 fingerprint, the host key's fingerprint, and the one command for the node: `wsl -d mu-compute -u root -- mu-wsl2-distro authorize <workstation> '<public key>'`. Refuses an existing node (`mu compute remove` first), a host key that is not one ed25519 key, a password outside `[A-Za-z0-9_-]{24,}` |
| `mu compute status [<node>]` | `hello` | The node's answer (§7.3), the path (`relay`), the time of the round trip and whether the connection was reused. Exit 0, 4 or 5 |
| `mu compute list` | none | Paired nodes with `last-status.json` |
| `mu compute remove <node>` | none | Ends a control connection, deletes the folder and the Keychain item, prints the revoke command for the node |
| `mu compute proxy <node>` | | Internal: the ssh `ProxyCommand` (§5.2) |
| `mu submit [--on <node>] [--name N] [--mem SIZE] [--time DUR] [--cores N] [--output GLOB]… [--no-rerun] [--wait [--timeout S]] [--allow-large] [--resume ID] -- <command…>` | `hello`, push, `submit` | §6, §7. `--on` may be left out when exactly one node is paired. `local` and `auto` are stage 2: exit 2 with that message. A command starting with `mu run` is refused (the node already runs `mu run <command>`). Over 100 MB of uncommitted or untracked files: exit 2 `large-untracked` unless `--allow-large` |
| `mu status [<job-id>] [--wait [--timeout S]]` | `status` or `list` | One job, or this project's jobs (the files in `.mu/jobs` joined with the node's `list`). `--wait` asks every 5 s on the shared connection until the job has a result; after a lost link it reconnects for up to `--timeout` (default: no limit). Exit 7 when the job ended otherwise than `succeeded` |
| `mu log <job-id> [--tail N] [--offset B] [-f]` | `log` | Prints the job's log from byte `B`. `--json` (not with `-f`) answers `{job_id, offset, next_offset, text, state, eof}`, so a caller polls with `--offset next_offset`. `-f` follows until the job has a result and resumes from its own byte count after a lost link |
| `mu fetch <job-id> [--into DIR] [--max-size 50M] [--output GLOB]…` | `fetch` | §11. Writes the declared outputs and `runs/<job-id>.json`. The declared globs come from this clone's record of the job (`.mu/jobs/<id>.json`), never from the node; a clone without that record needs `--output` (exit 2 `no-local-record`). Refuses a job without a result (exit 2: "still running") |
| `mu cancel <job-id> [--local]` | `cancel` | §8.4. A job this clone never got accepted (record in state `sending`, and the node answers `no-such-job`, or `--local`): its record and snapshot ref are removed |

`mu submit`, in order. Each refusal happens before anything is sent.

1. Project root: the nearest `mu.toml` upwards. That folder must be the top of a git work tree with at least one
   commit. Otherwise exit 2: "not a μ project", "mu.toml must be at the top of the git repository", "commit once
   first".
2. Pin: `[mu] version`. Missing or `unreleased`: exit 2 with `mu pin` as the hint.
3. Outputs: every `--output`, else `[compute] outputs` in `mu.toml` (a list of globs relative to the project
   root), else none, with the note "no outputs declared: only the log and the result record come back".
4. `hello`: protocol, pinned version installed on the node (else exit 3), runner, disk and limits (else exit 6).
   Asked before anything of the project is made, so a refused or offline node leaves no snapshot and no record.
5. The size of the uncommitted and untracked files (exit 2 above 100 MB without `--allow-large`), the job id
   (§7.4), then the snapshot (§6). Gitlinks and LFS pointers in the snapshot are listed as warnings.
6. Push the snapshot (§6.3).
7. `submit` with the job spec on stdin (§7.5). The node answers after its fsyncs.
8. Write `.mu/jobs/<job-id>.json`. Print the job id, state and queue position.

A submit interrupted after step 5 is repeated with **the same job id** (`mu submit --resume <job-id>` reads the
spec kept in `.mu/jobs/<job-id>.json`, which is written once before step 5 with `state: "sending"`). The node
answers a known id with the existing job, never a second one.

## 5. The connection

### 5.1 Generated `ssh_config`

Paths are written in double quotes: a path with a space works quoted and fails unquoted (§17 A).

```
Host mu-office-pc
  HostKeyAlias mu-office-pc
  User mu
  IdentityFile "<MU_HOME>/compute/office-pc/id_ed25519"
  IdentitiesOnly yes
  IdentityAgent none
  UserKnownHostsFile "<MU_HOME>/compute/office-pc/known_hosts"
  GlobalKnownHostsFile /dev/null
  StrictHostKeyChecking yes
  UpdateHostKeys no
  CheckHostIP no
  HostKeyAlgorithms ssh-ed25519
  PreferredAuthentications publickey
  PasswordAuthentication no
  KbdInteractiveAuthentication no
  BatchMode yes
  ClearAllForwardings yes
  ForwardAgent no
  ForwardX11 no
  RequestTTY no
  PermitLocalCommand no
  Compression yes
  ConnectTimeout 30
  ServerAliveInterval 15
  ServerAliveCountMax 3
  ControlMaster auto
  ControlPersist 300
  ProxyCommand "<MU_HOME>/bin/mu" compute proxy office-pc
```

Every call is `ssh -F <ssh_config> -o ControlPath=<P> mu-<node> <request>`, with `SSH_AUTH_SOCK` and `SSH_ASKPASS`
removed from the environment, `MU_PROXY_STATUS=<a new file in $MU_HOME/run>` added, and stdin from the request body
or `/dev/null`.

**`ControlPath` is computed at run time, never written in the file.** A Unix socket path is at most 103 bytes on
macOS and 107 on Linux, and ssh adds 17 bytes while it creates the socket. Measured on the Mac: 86 bytes work, and
from 87 bytes on **the command itself fails** (exit 255, "unix_listener: path … too long"), it does not merely lose
the sharing. Rule: `P = <dir>/<node>.sock` with `<dir>` the first of `$MU_HOME/run` and `/tmp/mu-<uid>` (made 0700;
refused unless it is a real folder owned by the user with mode 0700) for which `len(P) + 17` is at most 103 on
macOS and 107 on Linux. If neither fits: `ControlPath=none`, and each command pays a full handshake.

**Connection sharing matters.** A first connection costs about 12 end-to-end round trips and a reused one about 2
(§17 A). With 200 ms to the relay from each side that is about 5 s against 0.9 s. `ControlPersist 300` keeps the
connection for five minutes after the last command. A master that died with its link: on exit 255 from a
multiplexed call, the client runs `ssh -O exit`, removes the socket and repeats the call once on a new connection.

`MaxSessions 10` on the node: an eleventh simultaneous session on one connection is refused by the node, and ssh
then makes a connection of its own for that command (seen with 12 parallel commands: all 12 succeeded).

### 5.2 `mu compute proxy <node>`

A small supervising process, not a bare `exec chisel`. It starts

```
chisel client --fingerprint <relay_fingerprint> --keepalive 25s --max-retry-count 0 <relay_url> stdio:127.0.0.1:<relay_port>
```

with ssh's stdin and stdout handed straight to chisel (nothing is copied through Python), chisel's stderr on a
pipe, and an environment of exactly `PATH`, `HOME`, `AUTH=<relay_user>:<password>` and `CHISEL_WS_TIMEOUT=10s`.
The password is never in an argument list. It then:

- appends chisel's log lines to `$MU_HOME/logs/compute-<node>.log`. Left alone they land in ssh's stderr (chisel
  writes nothing but the stream to stdout, so the stream itself is never damaged);
- writes one line, `<class>\t<reason>\t<epoch>\t<log line>`, to `$MU_PROXY_STATUS`: the rules below are tried in
  order on each line, and the **first** line that matches a failure rule decides. What chisel prints while it
  shuts down is not a reason;
- ends chisel when the tunnel cannot work, when ssh ends (`SIGHUP`, `SIGTERM`, `SIGINT`, `SIGPIPE`) and when the
  relay is lost: `SIGTERM`, then `SIGKILL` 0.5 s later. chisel's first `SIGTERM` is a graceful stop that waits for
  the stdio stream, which with a dead relay closes only at ssh's next keepalive (15 s).

| chisel log line contains | Class | Reason | Exit |
|---|---|---|---|
| `Connected` | ok | connected | |
| `Invalid fingerprint` | refused | relay-fingerprint | 5 |
| `Authentication failed`, `unable to authenticate` | refused | relay-credential | 5 |
| `access to '…' denied` | refused | relay-rule | 5 |
| `connect failed` … `connection refused` (the relay is up, nothing listens on the node's port) | unreachable | node-offline | 4 |
| `Connection error:` before `Connected` | unreachable | relay-unreachable | 4 |
| `Disconnected`, `Give up`, `connection attempts exhausted` after `Connected` | unreachable | relay-lost | 4 |

`--max-retry-count 0` and no `--max-retry-interval`: a new chisel session cannot resume an ssh stream, so retrying
inside the proxy only delays the error (60 s against under 2 s, §17 A, F). The node's tunnel client keeps the retry
settings of `relay/README.md`.

How the caller classifies a failed call. ssh exits 255 for every failure of its own, so **node verbs never exit
255** (§7.2), and the cause comes from the status file first and ssh's stderr second:

| Status file | ssh stderr contains | Result |
|---|---|---|
| refused … | | exit 5, with the reason |
| unreachable … | | exit 4, with the reason |
| ok | `REMOTE HOST IDENTIFICATION HAS CHANGED`, `Host key verification failed` | exit 5: host key differs from the pin |
| ok | `Permission denied (publickey)` | exit 5: this workstation's key is not authorised on the node |
| ok or none | anything else, or a timeout | exit 4 |
| none (the proxy did not start) | | exit 1, with ssh's message |

### 5.3 The relay password on the workstation

| | macOS (default) | Linux, or `--secret-store file` |
|---|---|---|
| Where | login Keychain, generic password, service `systems.martand.mu.relay`, account `<relay host>/<relay_user>` | `$MU_HOME/compute/<node>/relay.auth`, mode 0600 in a 0700 folder |
| Write | `/usr/bin/security -i` with one line on its stdin: `add-generic-password -a "<account>" -s "<service>" -l "μ relay" -w "<password>" -U`. Then read it back and compare | `write_durable` |
| Read | `/usr/bin/security find-generic-password -a <account> -s <service> -w`, **with a 10 s limit** | read the file |
| Delete | `security delete-generic-password -a <account> -s <service>` | unlink |

- Never `add-generic-password … -w` as the last option without a value: with no terminal and an empty stdin it
  stores an **empty password and exits 0** (§17 E). `security -i` reads stdin whether or not a terminal exists.
- The password's characters are checked first (`[A-Za-z0-9_-]`, what `relay/gen-credentials.sh` makes), so the
  quoted form needs no escaping.
- A read costs 250 to 450 ms on the loaded test machine. It happens once per new connection.
- A locked keychain makes the read wait for an unlock dialog and never return. After the 10 s limit: exit 1,
  "the login keychain is locked or asks for permission: unlock it (open Keychain Access, or log in at the Mac),
  or store the password in a file: `mu compute remove <node>`, then `mu compute add … --secret-store file`".
  Exit 44 from `security` means the item is gone: "run `mu compute add` again".
- What the Keychain adds: the password is encrypted at rest and not in file backups. It does not hide it from other
  programs of the same user while the keychain is unlocked. The ssh key stays a 0600 file until stage 3.
- Test hook: `MU_SECURITY` names the program to run instead of `/usr/bin/security`.

### 5.4 Where the chisel client comes from

| Machine | Path | How |
|---|---|---|
| Any μ install (the laptop, and `mu` on the node) | `$MU_HOME/toolchains/<v>/libexec/chisel` | a new entry in `toolchain/apps.toml`, fetched by the installer like micromamba |
| The node's tunnel service (runs as `mu-tunnel`, not `mu`) | `/usr/local/lib/mu/chisel`, root-owned | the host script downloads the same archive, checks the same SHA-256, unpacks it as root (§13) |

```toml
[chisel]
version = "1.12.0"
source  = "github.com/jpillora/chisel (relay client; the same release and sums as relay/Dockerfile)"
[chisel.osx-arm64]
file   = "chisel_1.12.0_darwin_arm64.gz"
sha256 = "707a4b932eea214765146504a0df246cefc415b4297af65a80dd67cf69ba85a9"
url    = "https://github.com/jpillora/chisel/releases/download/v1.12.0/chisel_1.12.0_darwin_arm64.gz"
kind   = "gz.bin"
[chisel.linux-64]
file   = "chisel_1.12.0_linux_amd64.gz"
sha256 = "f3f180f1d93aa72cce4e6386f98cc06569a0146fbd65eb4423cf83e6434bcfe6"
url    = "https://github.com/jpillora/chisel/releases/download/v1.12.0/chisel_1.12.0_linux_amd64.gz"
kind   = "gz.bin"
```

In `install/install.sh`: add `gz.bin` to the kinds left out of `APP_NAMES`, and next to micromamba and uv fetch it,
`gunzip -c` it to `$T/libexec/chisel`, mode 755, and require `chisel --version` to print `1.12.0`. It is in no layer
key, so no layer is rebuilt and the release installs in under a minute. `mu doctor` gets one more line. This was
chosen over a separate download into `$MU_HOME` by the job commands: it reuses the resumable, checksummed download
and its cache, works offline afterwards, and costs three lines. It needs a release; so does the job layer.

A test asserts that the three places naming the version and sums agree: `apps.toml`, `relay/Dockerfile`,
`mu-wsl2-distro.sh`. The sums above were compared with the release's `chisel_1.12.0_checksums.txt` on 2026-11-14.

## 6. Upload: a git snapshot

### 6.1 Making the snapshot

Nothing of the user's is touched: not the index, the branch, `HEAD`, the stash or any file (§17 C).

```sh
GITDIR=$(git rev-parse --absolute-git-dir); IDX=$GITDIR/mu-index.$$
cp "$GITDIR/index" "$IDX" 2>/dev/null || GIT_INDEX_FILE=$IDX git read-tree HEAD   # no index yet: start from HEAD
GIT_INDEX_FILE=$IDX git add -A -- . ':(exclude).mu'
T=$(GIT_INDEX_FILE=$IDX git write-tree); rm -f "$IDX" "$IDX.lock"
S=$(GIT_AUTHOR_NAME=mu GIT_AUTHOR_EMAIL=mu@localhost GIT_COMMITTER_NAME=mu GIT_COMMITTER_EMAIL=mu@localhost \
    GIT_AUTHOR_DATE=<time in the job id, UTC> GIT_COMMITTER_DATE=<the same> \
    git commit-tree "$T" -p HEAD -m "mu job <job-id>")
git update-ref refs/mu/snap/<job-id> "$S"
```

- Every git command of the snapshot runs with the project root as its working folder.
- **The index is copied, not rebuilt.** Both ways give the same commit; the copy keeps git's record of unchanged
  files and took 1.4 s where `read-tree HEAD` took 19.9 s (4,000 files, 80 MB). It also keeps a sparse checkout
  right. Its one cost: a file marked `assume-unchanged` is taken as the index has it.
- **The identity and the dates are fixed.** `git commit-tree` fails with "Author identity unknown" when the user
  has no `user.name`. Fixed values also make the same job id give the same commit, so a repeated submit pushes
  nothing new.
- `dirty = (T != HEAD^{tree})`. `head`, `tree`, `snapshot` and `dirty` go into the job spec and the run record.
- `refs/mu/snap/<job-id>` stays in the project's repository: `git diff <S>` shows later exactly what ran. The refs of
  accepted jobs beyond the newest 10 are removed at each submit (the node keeps them); the refs of jobs not yet
  accepted stay for `--resume`.
- **The push sends everything reachable from `HEAD` that the node lacks**, history included: a secret that was
  committed once and deleted later reaches the node with the first push. Rewrite such history before the first
  submit.

What the snapshot holds, and its limits in stage 1:

| In the working tree | In the snapshot |
|---|---|
| tracked files as they are now, staged or not; untracked files that are not ignored; deletions; mode changes; symlinks (as links) | yes |
| ignored files; untracked files under the top-level `.mu/` (the job files, the toolchain link) | no |
| a submodule (`org/`) | **only a link** to the commit the submodule has checked out. Its files, and any uncommitted change in it, do not reach the node: the folder is empty there |
| a folder that is its own git repository but not a submodule | the same: a link only. git prints a hint about an "embedded repository" |
| a file tracked by git-lfs, committed or new | **the pointer file** (131 bytes), not the content. A new file is also stored in the project's local LFS folder as a side effect of git's filter |

`mu submit` lists every gitlink (`git ls-tree -r T`, mode 160000) and every declared input that is an LFS pointer,
and refuses (exit 2) when the job's working folder is inside a gitlink. Sending submodule and LFS content is stage 2.

### 6.2 Identity of the project on the node

`<project-id> = <slug>-<12 hex>`: the project folder's name in lower case with everything outside `[a-z0-9-]`
replaced by `-` (at most 40 characters, not starting with `-`), and the first 12 hex digits of the smallest root
commit id (`git rev-list --max-parents=0 HEAD | sort | head -1`). Clones of one project share one repository on the
node. A shallow clone has another root; it is refused with "unshallow first".

### 6.3 The push

```sh
GIT_SSH_COMMAND='ssh -F "<ssh_config>" -o ControlPath=<P>' GIT_SSH_VARIANT=ssh GIT_TERMINAL_PROMPT=0 \
git -c core.hooksPath=/dev/null push --no-verify --no-recurse-submodules --porcelain \
    "mu-<node>:<project-id>.git" "<S>:refs/mu/snap/<job-id>"
```

- **`--no-verify` is required.** In a project with git-lfs set up, a plain `git push` runs the LFS pre-push hook,
  which asks the node for an LFS API and fails the push (§17 C).
- git sends only what the node lacks: after a first snapshot, one changed line cost 802 bytes.
- The node refuses a pack over `receive.maxInputSize` (2 GiB by default; exit 6 with the size and the hint to
  ignore or LFS-track large files).
- Nothing can be read back through git: `git fetch` from the node is refused by the gate.

### 6.4 On the node

`mu node serve receive-pack repo=<project-id>` (what the gate makes of `git-receive-pack '<project-id>.git'`)
creates the bare repository on first use and execs `git receive-pack`:

```sh
git init -q --bare /srv/mu/projects/<project-id>.git
git config core.fsync all                 # objects and the ref are on disk before the push reports success
git config receive.maxInputSize 2147483648
git config receive.fsckObjects true
git config gc.auto 0                      # nothing is pruned behind a queued job
# hooks/update, mode 755:
#!/bin/sh
case "$1" in refs/mu/snap/[0-9]*) ;; *) echo "mu: only refs/mu/snap/<job-id> can be pushed here" >&2; exit 1 ;; esac
case "$2" in *[!0]*) [ "$2" = "$3" ] || { echo "mu: snapshot $1 exists and cannot be changed or deleted" >&2; exit 1; } ;; esac
```

**The hook is what makes snapshots write-once.** `receive.denyNonFastForwards` and `receive.denyDeletes` apply only
under `refs/heads/`: with both set, a forced overwrite and a delete of `refs/mu/snap/…` succeeded (§17 C).

Every git call on the node runs with `GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1`.

The job's working folder is made by a checkout with a private index, **not by `git archive`**, which drops
`export-ignore` files and rewrites `export-subst` files (§17 C):

```sh
GIT_INDEX_FILE=$JOB/index git --git-dir=$REPO --work-tree=$JOB/work -c core.bare=false read-tree -u --reset <S>
[ "$(GIT_INDEX_FILE=$JOB/index git --git-dir=$REPO write-tree)" = "<tree of the job spec>" ]   # else the job fails
```

## 7. Protocol: gate, verbs, records

Protocol number: **1**. It changes when a verb, an argument or a record changes meaning. `hello` carries the
client's number; the node answers with its own and the lowest it still accepts. A mismatch is exit 5 with both μ
versions and "run `mu update` on the older side".

### 7.1 The gate: `/usr/local/lib/mu/mu-node-gate`

Root-owned, mode 755, POSIX `sh`. It checks the **shape** of the request and hands it to `mu node serve`, which owns
the verb table. The grammar, exactly:

```
request  = verb *( 1*SP arg )  |  "git-receive-pack '" repo ".git'"
           at most 512 bytes; only the characters A-Z a-z 0-9 . _ : = , + - and space (the git form adds ' ' ')
verb     = 1*( a-z | "-" ), not starting with "-"
arg      = key "=" value          key starts with a-z; at most 15 args
repo     = 1*80( a-z | 0-9 | "." | "_" | "-" ), not starting with "." or "-"
```

No `/`, no quotes, no shell characters, no newline, no tab: anything structured goes on stdin as JSON. The gate
runs with `set -f`, so the split on spaces never globs. It then does, and nothing else:

```sh
exec /home/mu/.local/share/mu/bin/mu node serve "--key=<name>" <verb> <args…>
```

- `<name>` is the comment of the authorised key that logged in. sshd (`ExposeAuthInfo yes`) writes
  `publickey <type> <base64>` to the file `$SSH_USER_AUTH`; the gate looks that key up in the root-owned
  `/etc/mu/authorized_keys/mu`. A name outside `[a-z0-9-]`, or no match, becomes `unknown`.
- A refused request prints one JSON line, `{"ok":false,"error":"<code>","hint":"…","protocol":1}`, and exits 64.
  Codes: `no-command`, `too-long`, `bad-character`, `bad-request`, `bad-verb`, `bad-argument`, `bad-repository`.
- It sets `PATH=/usr/local/bin:/usr/bin:/bin`, `LC_ALL=C` and, if unset, `XDG_RUNTIME_DIR=/run/user/<uid>`.
- The two values that differ per machine (the path of `mu`, the key file) are written in by the host script.
- sshd runs it through `mu`'s login shell (`bash -c`), and Debian's bash reads `~/.bashrc` for ssh commands. `mu`'s
  `~/.bashrc` must print nothing; Ubuntu's default returns at once when the shell is not interactive. A node check
  compares the bytes of `hello` with valid JSON.

The prototype of this file, with 28 accepted and refused requests giving the same answers under `sh`, `dash` and
`bash`, is the experiment's `mu-node-gate` (§17 B).

### 7.2 Exit codes of node verbs

The client reads the JSON; the exit code only has to be unambiguous. sysexits values cannot collide with ssh's 255
or a shell's 126, 127 and 128+n. A verb never exits 255, 1 or 2.

| Exit | `error` codes |
|---|---|
| 0 | (`ok: true`) |
| 64 | the gate's codes; `unknown-verb`, `bad-argument`, `bad-spec` |
| 65 | `protocol-mismatch` |
| 66 | `no-such-job`, `no-such-snapshot` |
| 69 | `disk-low`, `limits-unavailable`, `not-enabled` (the runner is not running) |
| 70 | `internal` (with a line for the journal) |
| 78 | `toolchain-missing` |

Client mapping: 65 → 5; 66 → 9; 69 → 6; 78 → 3; 64 and 70 → 1.

### 7.3 Verbs (stage 1)

Arguments are `key=value`. Every answer except `log` and `fetch` is one JSON object and a newline.

| Request | stdin | Answer |
|---|---|---|
| `hello protocol=1` | | `{ok, protocol, protocol_min, node, key, mu:{default, installed:[…]}, hw:{cpu_model, cores_physical, cores_logical, mem_total_kb, kernel, wsl}, disk:{free_gb, min_free_gb, where, host}, limits:{memory, pids, selftest_at}, queue:{running, queued}, last_stop:"clean"\|"unclean"\|"unknown", runner:"active"\|"inactive", autoupdate:{enabled, last_result}, time}` |
| `git-receive-pack '<project-id>.git'` | git's pack protocol | git's |
| `submit id=<job-id>` | the job spec (§7.5), at most 64 kB | `{ok, job_id, state, position, duplicate}` |
| `status id=<job-id>` | | `{ok, job_id, state, attempt, position, queued_at, started_at, ended_at, exit_code, log_size, note}`; `note` says e.g. "re-run after an unclean stop of office-pc" |
| `list project=<project-id> [limit=N]` | | `{ok, jobs:[…the status fields…]}`: unfinished jobs and the last `N` finished (default 20) |
| `log id=<job-id> offset=<B> [max=<bytes>] [follow=1]` | | raw bytes of the log from `B`. With `follow=1` it keeps sending until the job has a result and the file's end is reached |
| `fetch id=<job-id> [what=result]` | | a tar stream (§11): `result.json`, then every output. With `what=result`, `result.json` alone |
| `cancel id=<job-id>` | | `{ok, job_id, state}` |

Every call is written to the journal with the key's name, the verb and the job id (never stdin).

### 7.4 Job id

`YYYYMMDD-HHMMSS-rrrrrr`: the UTC time the workstation made it, and six characters from `0-9a-z` chosen at random.
Pattern `^[0-9]{8}-[0-9]{6}-[0-9a-z]{6}$`. It is made on the workstation so that a repeated submit names the same
job. The node orders its queue by its own acceptance time, not by the id.

### 7.5 Job spec (stdin of `submit`, stored as `job.json`)

```json
{"schema": 1, "job_id": "20261114-101500-k3f9qa", "name": "frame fea",
 "project": "jatayu", "project_id": "jatayu-0123456789ab",
 "snapshot": "<40 hex>", "tree": "<40 hex>", "head": "<40 hex>", "dirty": true,
 "mu_version": "v2026.11.6", "argv": ["python", "fea_frame.py"], "cwd": "frame",
 "outputs": ["frame/results/*.json", "frame/img/*.png"],
 "limits": {"mem_bytes": null, "time_s": null, "cores": null}, "rerun": true,
 "submitted": {"at": "2026-11-14T10:15:00Z", "host": "avi-mac", "user": "avi", "mu": "v2026.11.7", "path": "relay"}}
```

The node refuses (`bad-spec`, with the field) unless: `job_id` equals the `id` argument; `snapshot` is a commit in
the project's repository and `refs/mu/snap/<job-id>` points at it (`no-such-snapshot` otherwise); its tree is
`tree`; `[mu] version` in the snapshot's `mu.toml` equals `mu_version`; `argv` is a non-empty list of strings
without NUL; `cwd` and every `outputs` entry are relative, without `..`, not absolute; the limits are null or
positive integers. `argv` runs without a shell: `mu run argv[0] argv[1:]…` in `work/<cwd>`. Shell syntax is the
caller's `-- bash -c '…'`.

## 8. Node: layout, runner, job units

### 8.1 Layout

`/srv/mu`, made by root, owned by `mu`, mode 0700 (PLAN §6.1 names the path):

```
/srv/mu/
  node.json                     name, protocol, created
  projects/<project-id>.git/    bare repository (§6.4)
  pins/<project-id>/mu.toml     the project's pin, listed in $MU_HOME/projects.txt (§12)
  jobs/<job-id>/
    job.json                    the accepted spec plus accepted_ns and the key's name; sealed; written once
    state.json                  queued | running, attempt, unit, times; sealed; replaced atomically
    events.log                  one line per transition, fsynced
    cancel                      present = cancel was asked (durable)
    index                       git index of work/
    work/                       the job's own copy of the project
    log                         the job's stdout and stderr
    result.json                 sealed; written once, last. It exists and is valid = the job is finished
  tmp/                          emptied when the runner starts
  lock                          flock for every change to the queue
```

A finished job's folder is its archive; the run record points at `office-pc:/srv/mu/jobs/<job-id>/work`. Nothing
is deleted in stage 1 (`mu compute gc` is stage 2); `submit` refuses below `min_free_gb` (default 20) with
`disk-low` and the ten largest job folders.

### 8.2 `mu node enable` (as `mu`, no root)

Writes `~/.config/systemd/user/mu-node.service`, then `systemctl --user daemon-reload` and `enable --now`. Runs
`mu node selftest` and records the result in `node.json`. `mu node disable` stops and removes it; jobs are left.

```ini
[Unit]
Description=μ job runner (mu node run)
[Service]
ExecStart="<MU_HOME>/bin/mu" node run
Environment="MU_HOME=<MU_HOME>"
Restart=always
RestartSec=2
KillMode=process
TimeoutStopSec=5
[Install]
WantedBy=default.target
```

Linger (set by the host script already) starts it at boot with nobody logged in.

### 8.3 The runner: `mu node run`

One loop, once a second, holding `/srv/mu/lock` for each change:

1. First pass only: empty `tmp/`, run the recovery table (§9.3).
2. If a job is `running`: read its unit (`systemctl --user show mu-job-<id>.service -p LoadState,ActiveState,Result`).
   Still active: nothing. Ended: classify (§8.5).
3. Else take the queued job with the smallest `accepted_ns`: make `work/` (§6.4), write `state.json = running,
   attempt n`, then start the unit (§8.4). A checkout or start that fails is `failed` with the reason.
4. Keep `$MU_HOME/state/busy` (the runner's pid on its first line) while any job is queued or running; remove it
   when none is. That is the contract `mu autoupdate` already has.
5. Idle and `$MU_HOME/default` is not the version this runner started under: exit 0. systemd starts the new code.

### 8.4 The job unit

```sh
systemd-run --user --quiet --unit=mu-job-<id> --description="μ job <id>" \
  -p Type=exec -p MemoryMax=<bytes> -p MemorySwapMax=0 -p RuntimeMaxSec=<s> -p TasksMax=4096 \
  -p KillMode=control-group -p TimeoutStopSec=5 -p OOMPolicy=kill -p LimitCORE=0 -p Restart=no \
  -p StandardOutput=append:/srv/mu/jobs/<id>/log -p StandardError=append:/srv/mu/jobs/<id>/log \
  --working-directory=/srv/mu/jobs/<id>/work/<cwd> \
  --setenv=MU_HOME=… --setenv=MU_JOB_ID=<id> --setenv=MU_JOB_DIR=/srv/mu/jobs/<id> --setenv=MU_JOB_CORES=<n> \
  -- <MU_HOME>/bin/mu node run-job <id>
```

- Its own unit means its own cgroup: the limits cover the whole process tree, cancel ends all of it, and it lives
  through the end of the ssh session, the tunnel and a restart of the runner.
- No `--collect`: a failed unit must stay loaded so that the runner can read its `Result`. The runner calls
  `systemctl --user reset-failed` after it has written the result.
- **The shim, `mu node run-job <id>`,** is the unit's main process. It runs `MU_VERSION=<pin> mu run <argv…>` as
  its child with the same stdout and stderr, waits, and then:
  1. evaluates the declared outputs in `work/` and hashes them (§11);
  2. `fsync`s the log and calls `os.sync()`;
  3. writes `result.json` durably: `succeeded` for exit code 0, `failed` with the exit code or signal otherwise;
  4. exits 0.
  On `SIGTERM` it passes the signal to its child and exits without a result: the runner decides what that was.
- **Cancel:** `mu node serve cancel` writes the `cancel` marker durably. Queued: it writes `result.json =
  cancelled` at once, under the lock. Running: `systemctl --user stop mu-job-<id>.service`; the runner classifies.

### 8.5 Classifying a unit that ended

| Found | Result |
|---|---|
| a valid `result.json` (the shim wrote it) | finished as it says |
| `cancel` marker, no valid result | `cancelled` |
| unit `Result=oom-kill` | `oom` |
| unit `Result=timeout` | `timeout` |
| unit failed otherwise (`signal`, `exit-code`, `core-dump`, `resources`) | `failed`, with the unit's result |
| unit gone or inactive with `Result=success`, no valid result | the shim died unseen: the interrupted rule (§9.3) |

Results written by the runner carry `written_by: "runner"`, the unit's result and no outputs.

## 9. Durability and recovery

### 9.1 `write_durable(path, data)`

A temp file in the same folder (`.<name>.<pid>.<random>.tmp`, `O_EXCL`, mode 0600), write, `fsync`, `os.replace`,
`fsync` of the folder. On macOS the two syncs use `fcntl(F_FULLFSYNC)` (plain `fsync` there leaves the data in the
drive's cache), falling back to `fsync`. After it returns, a power cut leaves the old file or the new one, whole.

### 9.2 Sealed records

`seal(obj)`: the object plus `"sha256"`, the SHA-256 of its canonical form without that field
(`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)` as UTF-8). `read_record(path)` returns the
object, or **nothing** when the file is missing, unreadable, over 16 MB, not UTF-8, not JSON, not an object, has no
`sha256`, or the sum is wrong. **A reader treats nothing as "not written".** That is the whole recovery rule, and it
covers the S3 failure (the right size, all NUL bytes) even though the write order should already prevent it.

`job.json`, `state.json`, `result.json`, `node.json`, the workstation's `.mu/jobs/*.json` and `runs/*.json` are all
sealed and written with `write_durable`.

### 9.3 Recovery, when the runner starts

| Found | Action |
|---|---|
| a temporary job folder (`jobs/.<id>.<pid>`) | deleted: the submit was never acknowledged, and the workstation sends it again with the same id |
| a job folder with its final name but no valid `job.json` (damage on disk) | moved to `/srv/mu/damaged/<id>.<time>` with whatever result and outputs it has, journalled; never deleted |
| valid `result.json` | finished |
| `cancel` marker, no result | `result.json = cancelled` |
| `queued`, or a valid `job.json`, no valid `state.json` and no `start attempt` in `events.log` | queued (in `accepted_ns` order; `accepted_ns` never goes back, whatever the clock does) |
| no valid `state.json`, but `events.log` records `start attempt N` | rebuilt as `running`, attempt N, and classified as below (never started twice, the attempt counter not reset) |
| `running`, unit still active, or `systemctl show` failed (bus, re-exec, timeout) | adopted: the runner restarted, the job did not. A failed `show` is never taken as "ended" |
| `running`, unit loaded and `inactive`/`failed` with a failure `Result` | classified (§8.5) |
| `running`, `systemd-run` failed and systemd does not know the unit | back to `queued`, the attempt kept; `failed` after 3 such starts |
| `running`, no unit, no valid result | attempt 1 and `rerun` true: `work/` and `index` deleted, the log **kept** with a line `== μ: attempt 1 ended without a result (…); re-run as attempt 2 ==` (offsets stay valid for a reader), back to `queued` with its old `accepted_ns` (so it is first), attempt 2. Otherwise `result.json = interrupted` |

Each recovery writes to `events.log` whether the node's previous stop was clean (the last `previous-stop=` line of
`/var/lib/mu-wsl2/stops.log`; `unknown` where that file does not exist). `mu status` shows it in `note`.

`result.json` is written once: a valid one is never replaced (a shim that ends after the runner gave up on it cannot
change the answer). One job's error (a full disk, an I/O error) is journalled and the pass goes on with the others;
no job is started while another one's state is unknown, or while the free space is below `min_free_gb` (the job
waits with a note). A job whose log grows past `log_cap_gb` (node.json, default 4) is stopped and ends `failed`.

Not protected: the last seconds of `log` after a hard stop (about 6 s with the write-back settings of the host
script). fsync itself is not tested: the tests kill writers (SIGKILL), they do not cut power.

## 10. Limits and queue

One running job, first in first out, queue on the node. FEA and CFD want every core and most of the memory, and a
queue on a laptop that sleeps is worth less than one on the node.

| Property | Value | Why |
|---|---|---|
| `MemoryMax` | `--mem`, else the node's memory minus 2 GiB (from `/proc/meminfo` `MemTotal`) | a job that outgrows the node ends as `oom`; the distro stays up |
| `MemorySwapMax` | 0 | a solver in swap looks like progress and is not |
| `RuntimeMaxSec` | `--time`, else 12 h | `timeout` |
| `OOMPolicy=kill` | | one process killed for memory ends the whole job, the shim included, so the outcome is one: `oom` |
| `TasksMax` | 4096 | |
| `KillMode=control-group`, `TimeoutStopSec=5` | | cancel and shutdown end MPI ranks and other children within the 8 s the host adapter gives user services |
| `MU_JOB_CORES` | `--cores`, else the physical cores | an environment variable only: no μ wrapper reads it yet, so the job passes it on itself (`mpirun -np $MU_JOB_CORES`, `OMP_NUM_THREADS=$MU_JOB_CORES`). No CPU quota: with one job it adds nothing |

`--mem` takes `K`, `M`, `G` (powers of 1024); `--time` takes `s`, `m`, `h`, `d`.

**The memory limit must be real.** `mu node selftest` starts two units with `MemoryMax=64M`: one allocates 32 MB and
must succeed, one allocates 256 MB and must end with `Result=oom-kill`. It runs at `mu node enable`, and again
whenever the node's boot id differs from the one in `node.json`. While it fails, `hello` reports
`limits.memory: false` and `submit` answers `limits-unavailable` (exit 6) with what to check (§19 step 1). There is
no switch to run without the limit.

Not set: `CPUWeight` and `IOWeight`. The user manager is delegated `pids`, `memory` and `cpu` only (§18), so
`IOWeight` would need a root drop-in to do anything, and `CPUWeight` in a user unit only ranks `mu`'s own units
against each other. The kernel's default shares already give `sshd` and the tunnel (system slice) half the machine
against the user slice. Sandboxing (`ProtectHome`, `PrivateTmp`) is stage 3.

## 11. Outputs, logs, run records

- **Declared outputs** are globs relative to the project root, evaluated in `work/` when the command ends:
  `glob.glob(pattern, root_dir=work, recursive=True)`; regular files only; a symlink is never followed out of
  `work/`; a pattern that matches nothing is listed in `outputs_missing` and is not an error.
- **`result.json`:**

```json
{"schema": 1, "job_id": "…", "state": "succeeded", "exit_code": 0, "signal": null, "attempt": 1,
 "queued_at": "…", "started_at": "…", "ended_at": "…", "wall_s": 412.6,
 "outputs": [{"path": "frame/results/fea.json", "size": 1830, "sha256": "…"}], "outputs_missing": [],
 "log": {"size": 48211, "sha256": "…"},
 "toolchain": {"version": "v2026.11.6", "recipe_commit": "…", "layers": ["…"]},
 "node": {"name": "office-pc", "cpu_model": "…", "cores_physical": 20, "cores_logical": 20, "mem_total_kb": 24000000,
          "kernel": "…", "wsl": "…"},
 "limits": {"mem_bytes": 23622320128, "time_s": 43200, "cores": 20},
 "unit": {"name": "mu-job-….service", "result": "success"}, "written_by": "shim", "sha256": "…"}
```

  States: `queued`, `running`, then exactly one of `succeeded`, `failed`, `cancelled`, `timeout`, `oom`,
  `interrupted`. `toolchain` is `MANIFEST.json` of the version that ran.
- **`fetch`:** the node sends an uncompressed tar (ssh compresses): `result.json` first, then each output under its
  path. The client reads it as a stream. It accepts a member only if it is a regular file, its path is the next
  entry of `outputs` in the result it just read, it matches a glob **this clone declared** (its record in
  `.mu/jobs`, or `--output`; never the globs the node's result names), no part of its path is `.git` or `.mu` (in
  any case or Unicode form), its tar size equals the recorded size before anything is read, and its SHA-256
  matches; it writes each through a temp file, fsync and a rename under `--into` (default: the project root). The
  client's glob check matches no hidden name with a wildcard, as the node's `glob.glob` (a pattern part that itself
  starts with `.` may). Anything else in the stream, or a mismatch, is exit 8
  and nothing is kept from that file on. The client asks for `what=result` first: when the outputs add up to more than
  `--max-size` (default 50 MB, PLAN §6.4) it stops with exit 2, the sizes and the option to pass.
- **Run record,** `runs/<job-id>.json`, written at fetch (sealed):

| PLAN §6.7 | Fields |
|---|---|
| project commit, hash of uncommitted changes | `head`, `tree`, `dirty`, `snapshot` |
| μ version and lock hash | `toolchain` from the result |
| host and hardware | `node` from the result; `path` |
| command and parameters | `argv`, `cwd`, `limits`, `attempt`, `name`, the key's name |
| wall time, key results | times, `state`, `exit_code`; each output with size and SHA-256 |
| archive location | `office-pc:/srv/mu/jobs/<job-id>/work` |

## 12. Toolchain versions

- The pin missing on the node: **refused** (`toolchain-missing`, exit 3, with the node's installed versions). A
  first install can take over an hour and runs the benchmark gate; it never starts from a submit.
- **Pins are registered on the node,** or auto-update's clean-up would remove a version a project on the laptop
  still uses. On every accepted submit the node writes `pins/<project-id>/mu.toml` (`[mu] version = "<pin>"`) and
  adds that folder to `$MU_HOME/projects.txt`. The dispatcher's `pinned_versions` then keeps it; clean-up is also
  skipped while `state/busy` exists, which covers a queued job with an older pin.
- The node's own code is the default version (§3). A switch of the default waits for `state/busy` to go.

## 13. What needs root on the node

New steps of `install/hosts/wsl2/mu-wsl2-distro.sh`, in its style: one `ensure_*` each, idempotent, a `check` in
`status`, undone by `uninstall`. They do not depend on WSL: the CI job runs the same functions on the GitHub
runner (`--no-wsl` skips §13.4).

| Step | What | Check in `status` |
|---|---|---|
| `ensure_ssh_masked` | **before** the package is installed: `systemctl mask ssh.socket ssh.service`, so that installing `openssh-server` never opens port 22 | both masked; no listener on 22 |
| `ensure_packages` | adds `openssh-server` | installed |
| `ensure_node_hostkey` | `/etc/mu/ssh_host_ed25519_key` (`ssh-keygen -t ed25519 -N ''`), root, 0600. **Kept by uninstall**: it is the node's identity | present; `status` prints the public key and its SHA256 fingerprint |
| `ensure_sshd_config` | `/etc/mu/sshd_config` (§13.1), checked with `sshd -t -f` before it replaces the old one | every line of §13.1 compared with `sshd -T -f /etc/mu/sshd_config -C user=mu,host=localhost,addr=127.0.0.1` |
| `ensure_sshd_unit` | `/etc/systemd/system/mu-sshd.service` (§13.2), enabled and started | active; `ss -ltnH` shows `127.0.0.1:2222` and no other sshd listener |
| `ensure_authorized_keys` | `/etc/mu/authorized_keys/` (root, 0755) and `…/mu` (root, 0644). Subcommands: `authorize <name> '<public key>'` (one ed25519 key, checked with `ssh-keygen -l -f`; name `[a-z0-9-]{1,32}`; written as `restrict <type> <key> <name>`; an existing name is replaced), `revoke <name>`, `keys` | names and fingerprints |
| `ensure_gate` | `/usr/local/lib/mu/mu-node-gate` (root, 0755) with the two machine values written in | equal to the script's copy |
| `ensure_job_root` | `/srv/mu`, owner `mu`, mode 0700 | owner and mode |
| `ensure_tunnel` | `/usr/local/lib/mu/chisel` (downloaded, SHA-256 of §5.4 checked, unpacked as root; falls back to the same archive in `mu`'s download cache, checked the same way); system user `mu-tunnel`; `/etc/systemd/system/mu-tunnel.service` (§13.3). Subcommand `tunnel-credential`: reads `AUTH`, `RELAY_URL`, `RELAY_FINGERPRINT` and `RELAY_PORT` lines from stdin into `/etc/mu/tunnel.env` (root:`mu-tunnel`, 0640), then enables and starts the service. Never from arguments | active; the last `Connected` line of its journal |
| `ensure_security_updates` | unattended upgrades, security pocket only | enabled |
| `ensure_wsl_conf` (extended), `ensure_host_drive` | §13.4 | §13.4 |
| `ensure_node_enabled` | as `mu`: `mu node enable`, when `mu` has a version with the job layer | runner active; `limits.memory` |

### 13.1 `/etc/mu/sshd_config`

The whole configuration of `mu-sshd.service`. `/etc/ssh/sshd_config` and its drop-ins are not read.

```
Port 2222
AddressFamily inet
ListenAddress 127.0.0.1
HostKey /etc/mu/ssh_host_ed25519_key
PidFile /run/mu-sshd.pid
UsePAM yes
AllowUsers mu
PermitRootLogin no
AuthenticationMethods publickey
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
HostbasedAuthentication no
PermitEmptyPasswords no
StrictModes yes
AuthorizedKeysFile /etc/mu/authorized_keys/%u
ExposeAuthInfo yes
ForceCommand /usr/local/lib/mu/mu-node-gate
PermitTTY no
AllowTcpForwarding no
AllowStreamLocalForwarding no
AllowAgentForwarding no
X11Forwarding no
PermitTunnel no
GatewayPorts no
PermitUserRC no
PermitUserEnvironment no
PrintMotd no
PrintLastLog no
MaxAuthTries 3
MaxSessions 10
MaxStartups 10:30:60
LoginGraceTime 20
ClientAliveInterval 30
ClientAliveCountMax 3
LogLevel VERBOSE
```

- No `Subsystem` line (no sftp), no `AcceptEnv` (a client's `SetEnv` did not arrive in the experiment), no `Include`.
- **`UsePAM yes` is needed:** `mu` has no password, and without PAM sshd treats such an account as locked.
- `restrict` in the key file and the `Permit…`/`Allow…` lines here are two independent locks on terminals and
  forwarding: either alone held in the experiment, and with both removed a terminal and a forward worked. Only
  `ForceCommand` stops a shell; `restrict` does not (§17 B).

### 13.2 `mu-sshd.service`

A dedicated instance, so that Ubuntu's socket activation, its generator and the image's own drop-ins play no part
(§18), and so that CI and the PC run the same unit.

```ini
[Unit]
Description=μ node sshd (loopback only, forced command)
After=network.target
[Service]
Type=notify
ExecStartPre=/usr/sbin/sshd -t -f /etc/mu/sshd_config
ExecStart=/usr/sbin/sshd -D -e -f /etc/mu/sshd_config
RuntimeDirectory=sshd
RuntimeDirectoryMode=0755
RuntimeDirectoryPreserve=yes
KillMode=process
Restart=on-failure
RestartSec=2
TimeoutStopSec=5
[Install]
WantedBy=multi-user.target
```

### 13.3 `mu-tunnel.service`

```ini
[Unit]
Description=μ relay tunnel (chisel client, reverse tunnel to mu-sshd)
After=network-online.target mu-sshd.service
Wants=network-online.target
[Service]
User=mu-tunnel
EnvironmentFile=/etc/mu/tunnel.env
Environment=CHISEL_WS_TIMEOUT=10s
ExecStart=/usr/local/lib/mu/chisel client --fingerprint ${RELAY_FINGERPRINT} --keepalive 25s --max-retry-interval 15s ${RELAY_URL} R:127.0.0.1:${RELAY_PORT}:127.0.0.1:2222
Restart=always
RestartSec=5
TimeoutStopSec=5
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
[Install]
WantedBy=multi-user.target
```

The password is in `AUTH` in the environment file, never in the command line. A job (user `mu`) cannot read
`/etc/mu/tunnel.env`. The unit also has `RestrictAddressFamilies`, `ProtectKernel*`, an empty capability set and
the rest of `tunnel_unit_text` in the host script.

**Egress filter** (§23, finding S2). chisel's client, when it holds a reverse remote, dials whatever `host:port`
the server names when it opens a channel (`Outbound: hasReverse`, no ACL, in chisel 1.12.0's `client/client.go`);
`127.0.0.1:2222` in `R:…` is only advice to the server. So the drop-in
`/etc/systemd/system/mu-tunnel.service.d/50-mu-egress.conf` limits the unit to loopback and the relay:

```ini
[Service]
BindReadOnlyPaths=/etc/mu/tunnel.hosts:/etc/hosts
IPAddressDeny=any
IPAddressAllow=127.0.0.1/32 ::1/128 <the relay's addresses>
```

`/etc/mu/tunnel.hosts` maps the relay's name to its addresses, so chisel needs no DNS server (on WSL the resolver is
an address of the Windows host, which must stay closed). `mu-wsl2-distro tunnel refresh` resolves the name (as
root, outside the sandbox), rewrites both files when the addresses changed and restarts the tunnel;
`mu-tunnel-refresh.timer` runs it hourly, `tunnel credential` and `install` too. A name that does not resolve leaves
both as they were. `status` checks the filter in effect: a unit of `mu-tunnel` with the same `IPAddressDeny` and
`IPAddressAllow` as the running unit must get "Operation not permitted" sending one UDP datagram to 192.0.2.1
(TEST-NET-1). (UDP, because the filter drops a packet as it leaves: a UDP send then fails at once, while a TCP
connect only times out; CI's first run showed exactly that.) The workstation's own chisel uses a `stdio:` remote, not a reverse one, so its client dials nothing for the server.

### 13.4 Windows reach: interop off, drives closed to `mu`, the drive recorded by root

Today the distro can start Windows programs (interop) and has `C:` at `/mnt/c`, and the WSL session belongs to an
administrator's elevated task. A job may therefore be able to act on Windows as that administrator.

**Decision:** in `/etc/wsl.conf`

```ini
[interop]
enabled = false
appendWindowsPath = false
[automount]
enabled = true
options = "uid=0,gid=0,umask=077"
```

and the Windows host script, which knows the distro's `-Location` for certain, passes the drive letter to the
distro script, which writes `/var/lib/mu-wsl2/host-drive` (root, 0644; one line, e.g. `C /mnt/c`). `mu_disk_free`
(`bin/mu` and `install/disk.sh`, the identical block; test hook `MU_WSL_HOST_DRIVE`) reads that file **first** and
treats it as "sure"; `reg.exe` stays as the second way for a distro the host script did not set up; the present
fallback stays third. The node's own disk figure (`hello`, `disk-low`) is the smaller of `/srv/mu` and that mount.

Why this and not the two alternatives:

| Option | Verdict |
|---|---|
| Interop and automount both off (the design note, PLAN §11) | The disk check then sees only the virtual disk. On the office PC that is 934 GB "free" against 130 GB really free on `C:` (S3 record). Jobs keep their folders in stage 1, so a blind disk check is how the node would fill the drive. **Kept as the fallback** if the mount options turn out not to hold |
| Interop on for root only | **No supported way** (by reading, §18). The `WSLInterop` binfmt handler is registered once for the whole VM, and the `wsl.conf` switch is per distro, all or nothing; a per-user split would rest on the permissions of sockets under `/run/WSL` that WSL recreates |
| **Interop off; drives mounted for root only; drive recorded by root (chosen)** | `mu` can ask for the free space of `/mnt/c` (`statfs` needs no permission on the folder itself; checked on macOS for a 0700 root folder) and can open nothing in it. No Linux process can start a Windows program. Root's reach is unchanged: root can mount a Windows drive whatever `wsl.conf` says |

`status` checks, as `mu` inside a `systemd-run --user` unit and as root: a Windows program does not start; as `mu`,
`ls /mnt/c` fails and `df -Pk /mnt/c` works; `mu_disk_free` reports the drive as sure. A failed check is a FAIL, and
the fix is `[automount] enabled=false` (then the record is removed and the disk figure is the virtual disk's, with
`disk.host: "unknown"` in `hello`). The change applies at the next start of the distro (`test-stop` does that).

### 13.5 On Windows

Nothing new for the relay path. Only if §19 step 1 fails on an older WSL: `kernelCommandLine = cgroup_no_v1=all` in
`.wslconfig`. WSL forwards Windows' `localhost:2222` to the distro, so programs on the Windows side reach the login
too; key-only authentication covers it.

## 14. Who can do what

| Who | Can | Cannot |
|---|---|---|
| Anyone on the internet or the office LAN | nothing: `mu-sshd` listens on loopback only | |
| The relay container or its host (Dokploy), or a holder of a relay credential | reach the node's ssh login (127.0.0.1:2222 through the tunnel), and refuse or delay traffic | read or alter job data (ssh inside); pass as the node (pinned host key); use the node's tunnel to reach anything but loopback and the relay's own addresses (the egress filter, §13.3; before it, chisel's client would dial any address the relay named: the distro's loopback services, the Windows host, the office LAN, the internet from the office's address) |
| A paired workstation key | the verbs of §7.3, **which include running any command as `mu`**, and therefore everything a job can do: read and change every other workstation's projects, snapshots, logs and results on the node, and make a later `mu fetch` of theirs carry whatever the node says | get a terminal, forward ports, use sftp, add or change keys, become root; make another workstation's `mu fetch` write outside the globs that workstation declared, or into `.git`/`.mu` (§11); set another workstation's `next`, `exit` or `ok` (§4.2) |
| A job (project code as `mu`) | read and write everything `mu` owns: other jobs, the μ install, `node.py` itself, the read-only deploy key, `/srv/mu/audit.log`; open network connections from the PC, the office LAN included | add an ssh key (root-owned file), read the relay credential (root-owned, another user), start Windows programs or open Windows files (§13.4, to be confirmed at the PC), become root, keep using a revoked key's open connection |

- `sshd` runs as root inside the distro, so a flaw in its login code would give root there. It is reachable only
  through the relay credential or from the PC itself, and gets Ubuntu's security updates.
- The gate and `mu node serve` are not a defence against `mu`. `sshd`'s own settings are.
- **The node is not trusted by the workstation.** Any job of any project can rewrite what the node answers. So
  `mu fetch` takes the declared globs from the workstation's own record, never writes into `.git` or `.mu`, checks
  each output's size before reading it and its SHA-256 after; answers are capped (1 MB, the result 16 MB).
- **Attribution:** the `key=` in `/srv/mu/audit.log` and in the `mu-node` journal lines is written by code `mu`
  owns (and `audit.log` is writable by `mu`), so any job can forge it. Only sshd's own root-written lines
  (`Accepted publickey for mu … SHA256:…`, `journalctl -u mu-sshd`) are evidence of which key logged in.
- **Revoking** removes the key line; the gate then refuses that key on every new session, also over a connection
  opened before (sshd reads the key file only at login), and `revoke` ends `mu`'s open ssh connections (the other
  workstations reconnect by themselves).

## 15. Stages

| Stage | In | Out |
|---|---|---|
| **1** (this document; proven in CI) | dispatcher rev 8; `mu compute add / list / status / remove`; `mu submit / status / log / fetch / cancel`; the snapshot push; the gate; `mu node enable / disable / selftest / serve / run / run-job`; queue; memory and time limits; durable records and the recovery table; run records; pin refusal and pin registration; the chisel pin; the root steps of §13; relay path only | `--on local` and `auto`; remote toolchain install; clean-up of job folders; submodule and LFS content; `fetch --all`; a LAN path; more than one node per call |
| **1b** (one visit) | §19 | |
| **2** | restart recovery proven on the PC; `--on auto` fallback to local; `mu compute install`; `mu compute gc`; submodule and LFS inputs; `fetch --all` (rsync with `--partial`); the cross-host check of PLAN §9 | |
| **3** | LAN-direct path; pairing by fingerprint (`--host-fingerprint`: the key is read through the tunnel and accepted only with that fingerprint) or one code; the ssh key in the Keychain; a job sandbox | |
| **4** | the native agent, with the same verbs over its own transport | |

## 16. Tests, and how each is shown to fail

`tools/test-jobs/run.sh` in the style of `tools/test-autoupdate`: a throwaway `HOME` and `MU_HOME` in a folder with
a space in its name, stub toolchain versions (`MANIFEST.json`, a `mu-toolchain` that runs the command, `env/bin/python`
linked to the test's Python), and each test shown to fail with its fault put in. Fault hooks are read from
`MU_NODE_FAULT` (node side: only from the runner's and sshd's own environment, which a client cannot set) and
`MU_JOBS_FAULT` (workstation side).

Two levels:

- **Unit level, macOS and Linux, no root:** the gate grammar; `durable.py`; the snapshot; the proxy's rules against
  recorded chisel lines; `mu compute add` with a `MU_SECURITY` stub; exit codes; a user-level `sshd` and a local
  chisel server as in the experiments (§17).
- **Node level, `ubuntu-24.04` runner:** the user `mu` with linger, the root steps of §13 from the real script
  (`--no-wsl`), the relay and Traefik replica of `tools/s4`, a second user as the workstation.

**The first CI job is a probe** of what was only read (§18), before anything is built on it: PID 1 is systemd;
`/sys/fs/cgroup` is cgroup v2; a new user with linger gets `user@<uid>.service` with `memory` in its
`cgroup.controllers`; `systemd-run --user -p MemoryMax=64M` kills a 256 MB allocation with `Result=oom-kill`;
`mu-sshd.service` starts with `UsePAM yes` and logs `mu` in by key; no listener on port 22 appears.

| Test | Passes when | Fault that must make it fail |
|---|---|---|
| Host key pin | a `known_hosts` with another key gives exit 5 and nothing is sent | `StrictHostKeyChecking no` in the generated config |
| Key required | an unauthorised key gives exit 5 | the key added to the key file |
| No shell | `ssh … 'id'` gets the gate's `bad-character`; `ssh` with no command gets `no-command` | `ForceCommand` removed |
| No terminal, no forwarding | `ssh -tt` gets no terminal; `ssh -N -L` cannot open a channel | **both** locks removed: `restrict` and the sshd lines |
| Gate grammar | the 28 requests of the experiment, under `sh`, `dash` and `bash` | the character check removed |
| Relay failures | wrong password, wrong fingerprint and a foreign port give exit 5 with the right reason; node offline and relay down give exit 4 within 5 s | the proxy replaced by a bare `exec chisel` (the reasons are lost, and node offline takes the full `ConnectTimeout`) |
| No secret in arguments | while a call runs, no process's command line holds the relay password | `AUTH` passed as an argument |
| Snapshot leaves the project alone | index, `HEAD`, branch, stash and every file's mtime are unchanged | `git add -A` on the user's index |
| Snapshot content | staged, unstaged, untracked, deleted, mode and symlink are in; ignored files and `.mu/` are not; gitlinks and LFS pointers are reported | the `.mu` exclusion removed |
| Push with LFS hooks | the push succeeds in a project with git-lfs set up | `--no-verify` removed |
| Snapshots are write-once | a forced overwrite and a delete are refused; another ref name is refused | the update hook removed |
| Work folder equals the tree | an `export-ignore` file is present and an `export-subst` file is unchanged | `git archive` |
| Upload is incremental | a second submit with one changed line grows the node's repository by under 10 kB | a full copy |
| Durable result | the shim killed just before `result.json` (`before-result`) never yields `succeeded`; the job is re-run once, then `interrupted` | the runner treating "unit ended with success" as `succeeded` |
| Torn records | `result.json` or `state.json` overwritten with NUL bytes of the same length reads as absent and the job is recovered from `job.json` | the checksum check off |
| Atomic write | 120 kills of a writer leave 0 damaged records | the write done in place (90 of 120 damaged in the experiment) |
| Memory limit | a job allocating twice its `--mem` ends as `oom` | `MemoryMax` left out |
| Time limit, cancel | `timeout` and `cancelled` are reported and a grandchild process is gone | `KillMode=process` |
| Queue | three submits run one at a time in order of acceptance | jobs started straight from `submit` |
| Repeat submit | the same job id twice is one job (`duplicate: true`) | an id made by the node |
| Log resume | the ssh session killed during `log -f`; the pieces joined by offset have the SHA-256 of the node's log | resuming from offset 0 |
| Fetch integrity | an output changed on the node after the result gives exit 8 and is not written | the hash check skipped |
| Fetch stays inside | a tar member outside the declared outputs is refused | the member check skipped |
| Link drop | the tunnel killed during a job: the job finishes; `status` and `fetch` work after the tunnel is back | the job run as a child of the ssh session |
| Runner restart | `systemctl --user restart mu-node` during a job: the job is adopted and finishes once | the runner starting it again |
| Version rules | a missing pin is exit 3; a registered pin survives `mu gc --yes`; infrastructure commands work in a project pinned to a version without the job layer | the pin registration removed; the dispatcher routing by the project's pin |
| Limits unavailable | with the self-test failing, `submit` is exit 6 | the check skipped |
| Pins agree | `apps.toml`, `relay/Dockerfile` and the distro script name the same chisel version and sums | one changed |
| bash 3.2 | `bin/mu` passes `bash -n` and the routing test under `/bin/bash` on macOS | |
| *Added with the review (§23):* | | |
| Fetch trusts this clone, not the node (S1) | the real `mu fetch` against a lying node (`fake-node-ssh.py`, globs `**`) refuses `mu.toml`, `.git/config`, `.claude/settings.json` outside the declared `results/*.json`; without a local record, exit 2 | `trust-node-globs` |
| Hidden names and `.git`/`.mu` (S1b) | `glob_match` accepts exactly what `glob.glob` finds; `.git/config`, `.GIT/config`, `.claude/settings.json` refused | `dot-globs` |
| Revoke (S3) | the gate refuses a key no longer in the key file (unit, local sshd over an open ControlMaster, e2e R05: the master ends and a call after it is exit 5) | the gate's check removed, and `revoke-keeps-sessions` |
| A node's words stay data (S4) | a refusal with `hint`, `next`, `exit`, `node`, `code` and escape sequences: `next` and `exit` are the workstation's, no crash, `node_message` cleaned | `node-hint` |
| Size guard (S5) | a member of 3 MB recorded as 10 bytes is refused before it is read | `no-size-guard` |
| Egress filter (S2) | R32: a unit with the real unit's `IPAddressDeny`/`IPAddressAllow` reaches mu-sshd over TCP; to the runner's own address (which answers without the filter) a TCP connect fails and a UDP send gets "Operation not permitted"; `status`'s `tunnel.egress` | `no-egress-filter` (R32 and H1) |
| Runner against failing systemd (D1–D5) | stubs: a failed `show`; `systemd-run` failing after making the unit, and failing without one; `state.json` NUL-filled while running; a re-run's log; one job's folder unwritable; low disk; a log over its cap; a damaged folder at recovery; a second result | `bus-error-is-end`, `trust-run-rc`, `no-unit-check-torn`, `log-truncate-on-rerun`, `runner-pass-aborts`, `no-start-disk-check`, `no-log-cap`, `delete-damaged`, `overwrite-result` |
| Every row ran | the IDs in `results.tsv` equal the expected rows and faults (Z1, Z2) | |

Not testable in CI, and said so in the record: WSL's cgroups, Windows interop and the drive mount, the office
network, a Windows restart during a job, the Keychain in a session without a GUI.

## 17. Verified by experiment

Run on 2026-11-14 on the M1 MacBook (macOS 27.0.1, OpenSSH 10.3p1, git 2.40.0, git-lfs 3.8.1, chisel 1.12.0, the
toolchain's Python 3.13.18). Everything listened on `127.0.0.1` and ran as the current user: a chisel server with
an auth file of the production rule shapes, a chisel client holding `R:127.0.0.1:7001`, and a user-level
`/usr/sbin/sshd -D -f <own config>` with its own host key and key file. Remote Login was not enabled; the real
relay and its credentials were not used. The scripts and their outputs are in the session's scratch folder
(`jobs/experiments/`, `jobs/results/`), not in the repository; stage 1's tests replace them.

**The machine was busy with other work during most runs (load average 14 to 29).** Counts, byte sizes, exit codes
and messages are exact. Times are given only where a run on the quieter machine exists or where the ratio is the
point.

**A. ssh through `ProxyCommand chisel client … stdio:127.0.0.1:7001`**

| Checked | Result |
|---|---|
| Works at all, with the production rule shape `^127\.0\.0\.1:(7001)$` for the workstation | yes: `hello` answered through relay, tunnel, sshd and gate |
| 8 MiB of random bytes up and back | SHA-256 equal |
| What chisel prints | nothing on stdout but the stream; three log lines per connection on **stderr**, which ssh shows as its own unless redirected |
| `ControlMaster auto`, four commands | one chisel process, one relay connection, one handshake. 12 parallel commands: all succeeded; 2 were refused on the master by `MaxSessions 10` ("Session open refused by peer") and ssh carried them on connections of their own |
| Time of a first connection and of a reused one, all on loopback (quiet run) | 0.16 s and 0.07 s |
| The same with a delay of D ms each way on both legs (quiet run) | D=25: 1.43 s and 0.30 s; D=50: 2.65 s and 0.50 s; D=100: 5.03 s and 0.90 s. That is about 12 end-to-end round trips for a first connection and 2 for a reused one |
| A path with a space in the config | works in double quotes; unquoted: "keyword controlpath extra arguments at end of line" |
| `ControlPath` length | 86 bytes: works. 87 to 103 bytes: exit 255, "unix_listener: path … too long". 104 bytes and more: exit 255, "ControlPath too long" |
| Host key differs from the pin | exit 255 at once, "REMOTE HOST IDENTIFICATION HAS CHANGED"; nothing sent |
| Key not authorised | exit 255, "Permission denied (publickey)" |
| Relay password wrong; relay fingerprint wrong; a port the client may not reach | exit 255, ssh says only "Connection closed by UNKNOWN port 65535"; chisel's log has "Authentication failed", "Invalid fingerprint", "access to '127.0.0.1:7002' denied" |
| Node offline (relay up, nothing on 7001), bare chisel | ssh waits its whole `ConnectTimeout` (10.1 s); chisel logs "connect failed … connection refused" at once |
| Relay killed during a session, `--max-retry-count 0`, bare chisel | chisel logs "Give up" at once but keeps the stream open; ssh ends 12 to 13 s later, at its next keepalive |
| The same with chisel's default retries | ssh ends after 60 s ("Timeout, server not responding") |

**B. The forced command**

| Checked | Result |
|---|---|
| 28 requests against the gate under `sh`, `dash` and `bash` | identical answers. Accepted: `hello`, `hello a=1  b=x.y:z,+-_`, `git-receive-pack '<id>.git'`. Refused, each with its code: empty; `;` `$( )` backticks `*` `\|` quotes `/` newline tab; a leading `-`; an argument without `=`; upper-case verb; a repository with `..`, `/`, a space, a leading `-`, no `.git`, or a second word; `git-upload-pack`; 600 bytes; 16 arguments |
| Exit codes 0, 1, 3, 7, 64, 254, 255 of a verb | arrive unchanged; **255 is the same number ssh uses for its own failures** |
| stdin | 30 bytes of JSON arrive; `ssh -n` gives 0 bytes |
| Environment of the verb | `HOME`, `LOGNAME`, `USER`, `SHELL`, `PATH`, `SSH_CLIENT`, `SSH_CONNECTION`, `SSH_ORIGINAL_COMMAND`, `SSH_USER_AUTH`, `TMPDIR`; a client's `SetEnv` does not arrive |
| Key name from `SSH_USER_AUTH` and the key file | `avi-mac`; with `ExposeAuthInfo no`: `unknown` |
| sftp | "subsystem request failed" |
| All locks on | no shell, no terminal, no forward ("administratively prohibited") |
| `ForceCommand` removed (the rest on) | **a shell command runs**: `restrict` does not stop it |
| sshd locks off, `restrict` on; and the reverse | no terminal, no forward in both |
| Both off | a terminal and a working forward |
| `command=` in the key file with `ForceCommand` set | `ForceCommand` wins |

**C. Snapshot and push** (a project with staged, unstaged, untracked, ignored and deleted files, a mode change, a
symlink, a dirty submodule, an embedded repository, two LFS files, a stash)

| Checked | Result |
|---|---|
| The user's repository before and after (HEAD, branch, index bytes, stash, `git status`, every file's mtime, size and mode) | identical; the staged/unstaged split is still there |
| Index from `read-tree HEAD` against a copy of the user's index | the same commit; 19.9 s against 1.4 s on 4,000 files |
| The same job id twice | the same commit id |
| No `user.name` | `commit-tree` fails: "Author identity unknown" |
| Submodule and embedded repository | gitlinks at their checked-out commits; the submodule's uncommitted change and untracked file are not in the snapshot |
| LFS files | 131-byte pointers; the never-committed one too |
| Plain `git push` through the gate with LFS hooks installed | **fails**: the pre-push hook asks for an LFS API |
| `push --no-verify --no-recurse-submodules` | succeeds; first snapshot 2,266 bytes on the node |
| A second snapshot with 80 MB of new files, then a third with one line changed | the third grew the node's repository by 802 bytes |
| The same push again | "Everything up-to-date" |
| Forced overwrite and delete of a snapshot ref, `receive.denyNonFastForwards` and `denyDeletes` true, no hook | **both succeed** |
| The same with the update hook | both refused; another ref name refused; a new snapshot accepted; with the hook removed again the overwrite succeeds |
| `receive.maxInputSize` 1 MB, 3 MB pushed | "pack exceeds maximum allowed size" |
| `git fetch` from the node | refused by the gate |
| `git archive` against `read-tree -u` with a private index | `git archive` drops the `export-ignore` file and expands `export-subst`; the checkout has all 13 files unchanged, mode and symlink kept, tree written back equal to the snapshot's |

**D. Durable writes and sealed records** (Python 3.13)

| Checked | Result |
|---|---|
| Ten damaged records: NUL bytes of the same length, half NUL, truncated, empty, a changed state with the old sum, one digit changed, no sum, not an object, another record's sum, not UTF-8 | all read as absent |
| The same changed record with the check off | accepted: the check is what rejects it |
| A record re-formatted by another writer (indent, key order, escapes) | accepted |
| 120 kills (`SIGKILL`) of a process rewriting a record | 0 damaged |
| The same with the write done in place | 90 of 120 damaged |
| Cost per write on this Mac | 17 ms with `F_FULLFSYNC`, 2 to 4 ms with `fsync` |

**E. Keychain** (a throwaway service name, deleted afterwards; a throwaway keychain file for the locked case)

| Checked | Result |
|---|---|
| `security -i` with the command on stdin | item stored; only `security -i` is in the process list |
| `add-generic-password … -w` last, stdin empty, no terminal | **an item with an empty password, exit 0** |
| Read with `find-generic-password -w` | the value, 250 to 450 ms |
| `-U` | replaces the value |
| Missing item | exit 44 |
| From a launchd job in the GUI domain | the login keychain's item is readable |
| A locked keychain, read from the GUI domain | no answer: it waits for an unlock dialog (ended by the experiment's guard after 8 s) |
| `create-keychain` | does not change the user's search list |

**F. The proxy as a supervising process, and Python's start**

| Checked | Result |
|---|---|
| 8 MiB through it | SHA-256 equal; chisel's lines no longer in ssh's stderr |
| ssh ended, killed with `TERM`, killed with `KILL` | no proxy and no chisel process left |
| Status file for the nine cases of A | `ok`; `ok` with ssh's host-key message; `ok` with "Permission denied"; `refused relay-credential`; `refused relay-fingerprint`; `refused relay-rule`; `unreachable node-offline`; `unreachable relay-unreachable` (relay down, and a name that does not resolve) |
| Node offline | fails in 1.4 to 2.8 s on the loaded machine, against 10 s |
| Relay killed during a session | ssh ends 0.8 to 1.7 s later, against 12 to 13 s. With `SIGTERM` alone it still took 12.5 s: chisel's graceful stop waits for the stream |
| Toolchain Python start, best of 7 in the same loaded minute | `-I`: 1,078 ms; `-I -S`: 44 ms. In a profiled start, 1.2 s of the 1.3 s is one `.pth` line that imports `cad` |
| `python -I -S cli.py` with `recipe/src` inserted by the script | imports `mu.jobs`; no site-packages path |

**Not tested on the Mac, plainly:** anything with systemd, cgroups or PAM; the Linux chisel binary; the real relay,
Traefik and real network latency (the delay figures come from a local proxy); power loss (a killed process keeps
the page cache); the Keychain in a session without a GUI (the `user/501` launchd domain refused the job, and Remote
Login stayed off); transfer speed (the load made every figure meaningless: a local `git clone` of the same 80 MB
took 32 s).

## 18. Read in sources, not run

| Question | What the sources say | Consequence |
|---|---|---|
| Which cgroup controllers does a user manager get on Ubuntu 24.04? | systemd 255's `user@.service` has `Delegate=pids memory cpu` | `MemoryMax`, `MemorySwapMax` and `TasksMax` work in `systemd-run --user` without a root drop-in. `io` is not delegated: `IOWeight` dropped |
| Does WSL's kernel give systemd a cgroup v2 memory controller? | Up to WSL 2.9.4 the default was both hierarchies with the controllers on v1; users set `kernelCommandLine = cgroup_no_v1=all`. PR microsoft/WSL#40519 (merged 2026-07-14, tags 2.9.5 and later) puts each distro in its own cgroup v2 subtree (`/wsl-user/distro-<pid>/systemd`), default on (`isolateDistroCgroup`), and "will not allow the use of cgroupv1". Which controllers it enables in the distro's subtree is not stated | The PC runs WSL 3.0.5.0, so v2 is expected. **Unverified**; it is step 1 at the PC, and `mu node selftest` is the proof |
| Linger and user units at a stop | Linger starts `user@<uid>.service` at boot. A clean stop sends `SIGTERM` to each user unit and waits the stop timeout (8 s here), then kills. A hard stop of the VM runs nothing. Transient units live in `/run` and are gone after either | The runner comes back by itself; a job unit never does. The recovery table treats both stops alike |
| `ssh.socket` on Ubuntu 24.04 | Socket activation is the default; a generator turns `Port` and `ListenAddress` into `ListenStream=` lines; bugs are open for `ListenAddress` and `AddressFamily` (LP 2089198, 2103632). Reverting needs the generator masked, the socket disabled and the service enabled | A dedicated `mu-sshd.service` with its own config file; the distribution's two units masked before the package is installed |
| A user without a password and `UsePAM` | Ubuntu's `ssh.service` is `Type=notify` with `RuntimeDirectory=sshd`; its README ties clean session ends to `UsePAM yes` | `UsePAM yes`, `Type=notify`, `RuntimeDirectory=sshd` in §13 |
| WSL interop without `WSL_INTEROP` | `/init` tries `/run/WSL/<pid>_interop` for its own pid, then each parent's, up to init. `[interop]` in `wsl.conf` switches it per distro | A process in a user unit may well reach it. Tested, then switched off (§19) |
| GitHub `ubuntu-24.04` runners | A VM with systemd as PID 1 and cgroup v2; `loginctl enable-linger` plus `XDG_RUNTIME_DIR=/run/user/<uid>` gives `systemctl --user` for another user | **Confirmed by the probe job** (CI run 43531869932, §21): systemd 255 as PID 1, cgroup2fs, `cpu memory pids` delegated to `mu`'s manager, `oom-kill` under `MemoryMax=64M`, `mu-sshd` with `UsePAM yes` |

Sources: systemd `units/user@.service.in` at v255; microsoft/WSL PR 40519 and `wsl.dev/technical-documentation/interop`;
Ubuntu's `openssh` `README.Debian` and `ssh.service` (noble); `receive.denyNonFastForwards` in git's
`builtin/receive-pack.c`; chisel 1.12.0 `share/cos/signal.go` (one handled signal, then the default).

## 19. Still unverified until the PC

The owner's sequence is `docs/design/first-job-checklist.md`; its §4 lists what each step settles. (The table that
was here was an older copy of that sequence and has been removed; the checklist is the one to follow.)

What only the PC can settle:

- auto-update carrying a release to the PC (PLAN §14);
- cgroup v2 memory limits for user units under WSL 3.0.5 (`mu node selftest`);
- **the tunnel's egress filter under WSL's kernel**: `IPAddressDeny`/`IPAddressAllow` need cgroup BPF; `status`'s
  `tunnel.egress` check proves it there (a probe unit's UDP send must get "Operation not permitted"); and the
  relay's name resolving from the distro for `tunnel refresh`;
- interop off for root and for `mu`, and `/mnt/c` closed to `mu` but measurable, after a restart; **and whether `mu`
  can still reach WSL's interop socket (`/run/WSL/*_interop`, `$WSL_INTEROP`) once interop is off** —
  `windows-reach` tests starting a Windows program through binfmt only (to check at the visit:
  `wsl -d mu-compute -u mu --cd ~ -- sh -c 'ls -l /run/WSL/; echo "$WSL_INTEROP"'`);
- `openssh-server` installing with port 22 never open, and the other root steps on real WSL;
- the startup task once `mu` cannot enter `/mnt/c` (`task.last-run`);
- the tunnel from WSL2 through the office network, and the whole path from a second network with PAM's real
  handshake time;
- the Keychain write on the real Mac;
- the first real job against the local reference, and `oom`, `timeout`, `cancel` once each;
- a clean stop of the distro during a job (one re-run, a correct result, the log kept with its marker line).

Also open after the visit: the first Windows Update restart during a job; an unclean stop during a job; a WSL update
that changes the cgroup layout (the self-test runs again at each boot and refuses jobs if limits are gone); idle
beyond five minutes and a relay redeploy with a session open; the Keychain when the Mac is used over ssh; a change of
the relay's address (the refresh timer follows it within the hour; CI only checks the files it writes).

## 20. Changes from the design note, and from PLAN v6

From the design note of 2026-11-13:

| # | Was | Now | Why |
|---|---|---|---|
| 1 | relay port 2201 | **7001** | the deployed relay's port for `office-pc` |
| 2 | a drop-in `/etc/ssh/sshd_config.d/10-mu.conf`, `ssh.socket` off, `ssh.service` on | a dedicated `mu-sshd.service` with `/etc/mu/sshd_config` and its own host key; the distribution's units masked first | socket activation and its generator are out of the picture; the same unit runs in CI; `UsePAM yes` made explicit |
| 3 | `ProxyCommand` execs chisel | `mu compute proxy` supervises chisel: `--max-retry-count 0`, a status file, the log kept out of ssh's stderr, kill on a dead tunnel | ssh's 255 says nothing about the cause; node offline took 10 s and a lost relay 13 to 60 s |
| 4 | `ControlPath ~/Library/Mu/run/%C`, `ControlPersist 60`, `ConnectTimeout 10` | path computed per call under the socket-length limit; 300; 30 | a long `MU_HOME` breaks the socket; a first connection is about 12 round trips; the proxy now ends the offline case early |
| 5 | `read-tree HEAD` for the temporary index; `git commit-tree` as the user | the user's index copied; fixed identity and dates; `.mu` excluded | 14 times faster; no failure without `user.name`; the same job id gives the same commit |
| 6 | plain `git push` | `--no-verify --no-recurse-submodules`, `core.hooksPath=/dev/null` | the LFS pre-push hook fails the push |
| 7 | `receive.denyDeletes`/`denyNonFastForwards` implied | an `update` hook | those settings do not cover `refs/mu/…` |
| 8 | `git archive \| tar -x` for `work/` | `read-tree -u --reset` with a private index, tree checked | `git archive` changes the tree (`export-ignore`, `export-subst`) |
| 9 | infrastructure commands "sent to the default toolchain" | exactly `python -I -S recipe/src/mu/cli.py`, without `mu-env.sh` | 1.1 s against 0.05 s per start; `XDG_CONFIG_HOME` must stay the user's |
| 10 | `CPUWeight`, `IOWeight`, `ensure_cgroup_delegation` | dropped; `mu node selftest` and refusal when the memory limit is not real | `io` is not delegated; a limit that is not enforced must not pass silently |
| 11 | a `wait` verb; results "from the unit's Result" | no `wait` (the client polls on the shared connection); a classification table; a `cancel` marker; no `--collect` | fewer long-lived sessions; one rule per outcome |
| 12 | node verbs' exit codes unspecified | sysexits values; never 255, 1 or 2 | 255 is ssh's own |
| 13 | "the Mac's [credential] into the Keychain" | `security -i`, a 10 s limit on reads, a file store for Linux and on request | the `-w` trap; a locked keychain never answers |
| 14 | chisel's source left open | `toolchain/apps.toml`, kind `gz.bin`, into `libexec/`; root's own verified copy on the node | §5.4 |
| 15 | interop and automount both off | interop off; drives mounted for root only; the drive recorded by root and read by the disk check | 934 GB against 130 GB (§13.4) |
| 16 | default outputs "to be agreed" | `[compute] outputs` in `mu.toml`; none declared is allowed | §4.2 |
| 17 | `mu log` text only | `--json` with `next_offset` for polling | Claude Code reads it |
| 18 | `--on local` and `auto` in `mu submit` | stage 2; `--on` optional with one node | stage 1 is the remote path only |

Against PLAN v6 (the lead merges; this document does not edit the plan):

- §11 and §13 say the design turns interop **and the `/mnt/c` mount** off. This specification keeps the mount, closed
  to everyone but root, for the reason in §13.4, and keeps "both off" as the fallback.
- §6.4a lists four unverified points. Now: ssh connection sharing over chisel `stdio` is **verified** (§17 A);
  Ubuntu's socket-activated ssh is **avoided** (§13.2); cgroup memory limits under WSL and interop from a user unit
  are **still unverified** (§19 steps 1 and 2).
- §5.4 names `src/mu/jobs.py` and `src/mu/node.py`. There are two more files: `cli.py` and `durable.py`.
- §5.1 says the relay password moves to the Keychain: it does, in stage 1. The ssh key stays a file until stage 3,
  as §6.4a's stage table says.
- Everything else agrees: the commands, port 7001, one job at a time, the refusal of a missing pin, relay path
  only, fallback rules (stage 2), 50 MB fetch limit, `state/busy`.

## 21. Implemented: stage 1 in CI

Branch `job-layer`. Proven on GitHub's `ubuntu-24.04` runners (`.github/workflows/job-layer.yml`, run
43531869932: 4 jobs green; 38 rows passed, 24 faults each made their row fail) and in the fast tests on macOS 15 (bash 3.2) and Linux. Nothing has run on the office PC.

### 21.1 Commands, as built

| Command | What it does |
|---|---|
| `mu compute add` | makes `$MU_HOME/compute/<node>/` (key, `known_hosts`, `ssh_config`, `node.conf`), stores the relay password (Keychain via `security -i`, or `relay.auth`), prints the key, both fingerprints and the `authorize` command for the PC. `--name` sets this workstation's name on the node (default: the relay user) |
| `mu compute list / status / remove` | `last-status.json`; `hello` through the relay with the round trip and whether the connection was reused; end the master, delete folder and Keychain item, print the `revoke` command |
| `mu compute proxy` | ssh's ProxyCommand: `chisel client … stdio:` with `AUTH` in its environment, its log in `logs/compute-<node>.log`, its verdict in `$MU_PROXY_STATUS` |
| `mu submit` | the checks of §4.2, the snapshot (§6.1), `hello`, push, `submit`; `.mu/jobs/<id>.json` before sending; `--resume <id>` sends the same job again |
| `mu status [<id>] [--wait]` | one job, or this project's (`list` joined with `.mu/jobs`); exit 7 when it ended otherwise than `succeeded` |
| `mu log <id> [-f] [--tail N] [--offset B] [--json]` | `-f` resumes from its own byte count after a lost link; `--json` gives `offset`, `next_offset`, `eof` |
| `mu fetch <id>` | `status`, then `what=result`, then the stream; each output checked (§11) and written through a temp file; `runs/<id>.json` |
| `mu cancel <id>` | queued: `cancelled` at once; running: the unit is stopped and the runner's classification is awaited (10 s) |
| `mu node serve / run / run-job / enable / disable / selftest / info` | §7.3, §8.3, §8.4, §8.2, §10; `info` is the `hello` answer on the node itself; `enable --name N --min-free-gb G` |

### 21.2 Where the implementation differs from the text above, and why

| # | Text | Implementation | Why |
|---|---|---|---|
| 1 | — | `src/mu/proto.py`, a fifth module | what both sides share (protocol, exit codes, names, job id, globs) in one place |
| 2 | gate "with the two values written in" | the file holds `@MU_BIN@` and `@MU_KEYS@`; until both are replaced it refuses everything (`not-installed`) | a gate copied without its values must not run anything |
| 3 | `-o ControlPath=<P>` | `-o 'ControlPath="<P>"'`, also inside `GIT_SSH_COMMAND` | an unquoted path with a space fails on the command line as it does in the file (§17 A) |
| 4 | the proxy writes one verdict | it writes `starting` first; on a failed new connection the client waits up to 2 s for the reason | chisel exits by itself on a refused credential or fingerprint, and ssh can end before the proxy has read chisel's last line: seen once on macOS CI as `proxy-failed` instead of `relay-credential` |
| 5 | calls share the master for 300 s | `hello`, `status` and `list` on a shared connection give up after 12 s, end that master and ask once more on a new connection | when the node's tunnel died under an open master, a call hung for 44 s (3 × 15 s keepalive) in the local test; now it answers `node-offline` in under 16 s |
| 6 | node faults "from the runner's and sshd's own environment" | `MU_NODE_FAULT`, and the root-owned file `/etc/mu/test-fault` | sshd gives a session none of its own environment; a job (user `mu`) cannot write `/etc/mu` |
| 7 | `fetch` refuses a job without a result | the client asks `status` first (exit 2 "still running"); the node's `fetch` answers `not-finished` (64), which the client also maps to 2 | |
| 8 | `result.json` fields of §11 | plus `job`: the spec's name, argv, cwd, outputs, snapshot, head, tree, dirty, pin and key | the run record can be written by a clone that did not submit the job |
| 9 | the shim writes `failed` with the signal | when its child died by a signal and the unit's cgroup counted an OOM kill, it writes `oom` | `OOMPolicy=kill` and the shim race; both now give `oom` |
| 10 | the shim exits after `SIGTERM` | it re-raises `SIGTERM` on itself | systemd then sees a stopped unit as stopped, not as an exit code |
| 11 | `systemctl --user` "with `XDG_RUNTIME_DIR` if unset" | `XDG_RUNTIME_DIR` is set to `/run/user/<uid>` unless it is this user's, and a `DBUS_SESSION_BUS_ADDRESS` outside it is dropped | on the runner, `/etc/environment` reaches `mu`'s user manager through PAM; `systemd-run` from `mu-node.service` failed with "Failed to connect to bus: Permission denied" |
| 12 | `ssh-keygen` makes the key | the modes are set afterwards (key 0600, `.pub` 0644) | a default ACL on the runner's home left the key 0644, and ssh refused it ("bad permissions") |
| 13 | `sshd -t -f` checks the new configuration | `/run/sshd` must exist first (`install -d -m 755 /run/sshd`) | "Missing privilege separation directory" when the distribution's ssh units are masked and never ran: **the host script needs this** |
| 14 | the runner's interrupted rule at start-up | applied in every pass | a shim that ends without a result during normal operation is the same case |
| 15 | "Upload is incremental: one changed line" | one changed line of a small file beside 2 MB of data: +458 bytes on the node | git stores a changed large file as a new object (a 1.5 MB file changed by one line grows the node by about its compressed size); what is not sent again is everything unchanged |
| 16 | torn records, fault "checksum check off" | in CI the fault is `trust-existence` (a `result.json` that exists counts); the checksum itself is shown by the unit test of a changed record with the old sum | a file of NUL bytes is not JSON, so the sum is not what rejects it |
| 17 | push fault "`--no-verify` removed" | `push-hooks` removes `--no-verify` and `core.hooksPath=/dev/null` | either one alone keeps the LFS hook from running |
| 18 | — | `.mu/jobs/.gitignore` (`*`) | the job records never show in `git status` |
| 19 | `mu_disk_free` reads the record | `_mu_host_drive` in `bin/mu` and `install/disk.sh`, test hook `MU_WSL_HOST_DRIVE` | as §13.4 |

### 21.3 The tests

`tools/test-jobs/run.sh` (unit level, macOS and Linux): the gate's 28 requests under `sh`, `dash` and `bash`, the
dispatcher's routing under `/bin/bash` 3.2, durable records (ten damaged forms, 120 kills), ids, globs, the proxy's
rules against recorded chisel lines, the classification table, `ssh_config` byte for byte and through `ssh -G`, the
ControlPath rule, unpacking (symlink, absolute, `..`, undeclared, size, SHA-256, a project folder that is a link),
the snapshot, the chisel pins, `mu compute add / list / remove` with a stub for `security` (and a locked keychain).
Each guarded check runs again with its fault and must fail. `tools/test-jobs/local.sh`: a user-level `sshd`, a
chisel server and client on 127.0.0.1, stub `systemd-run`/`systemctl`; the whole cycle through the real gate.

`tools/test-jobs/e2e.sh` (`ubuntu-24.04`): `tools/s4/run.sh relay` (Traefik 3.7.16 and the relay from `relay/`),
`node-setup.sh` (§13 on the runner), the workstation as the user `ws`. Every row runs, then every row again with its
fault, which must make it fail ("FAILS-OK"):

| # | Check | Result, measured | Fault put in → result |
|---|---|---|---|
| S0 | chisel client from the apps.toml pin, SHA-256 checked | **PASS**: 1.12.0 | — |
| S1 | relay and Traefik replica (tools/s4/run.sh relay) | **PASS**: relay up: https://relay.martand.systems (Traefik v3.7.16), credentials in /home/runner/work/mu/mu/.scratch/s4/env/ | — |
| S2 | node set-up (§13 on the runner: mu-sshd, gate, /srv/mu, mu-tunnel, runner) | **PASS**: uid of mu 1002 | — |
| S3 | mu compute add (file store on Linux), key authorised on the node | **PASS**: SHA256:ZUlFU18/16Kw4lydswNTTMfuw6CGNrjY4MZSeBDTqYI | — |
| S4 | first hello through the relay | **PASS**: 0.071 s | — |
| P1 | PID 1 is systemd | **PASS**: systemd | — |
| P2 | /sys/fs/cgroup is cgroup v2 | **PASS**: cgroup2fs | — |
| P3 | mu's user manager is delegated the memory controller | **PASS**: cpu memory pids | — |
| P4 | systemd-run --user -p MemoryMax=64M ends a 256 MB allocation with Result=oom-kill | **PASS**: Result=oom-kill | — |
| P5 | mu-sshd runs with UsePAM yes and logs mu in by key (through the relay) | **PASS**: mu-sshd active; usepam yes; hello: true as key avi-mac | — |
| P6 | no listener on port 22; mu-sshd on 127.0.0.1:2222 only | **PASS**: 127.0.0.53%lo:53 0.0.0.0:443 0.0.0.0:80 127.0.0.1:2222 127.0.0.54:53 *:2377 [::]:443 [::]:80 *:7946 | — |
| P7 | mu node selftest passes | **PASS**: MemoryMax=64M: 32 MB ran, 256 MB ended with oom-kill | — |
| R00 | a whole job: submit, status --wait, log (argv as a list), fetch with a run record | **PASS**: status --wait exit 0; log: job ran ['two words', 'three']; fetch: ["results/out.json"]; run record: yes | — |
| R01 | host key pin: another key in known_hosts gives exit 5 and nothing reaches the node | **PASS**: exit 5, host-key; node requests during it: 0 | `no-strict-hostkey` → **FAILS-OK**: exit 0, ; node requests during it: 1 |
| R02 | key required: an unauthorised key gives exit 5 | **PASS**: exit 5, key-not-authorised | `key left in the key file` → **FAILS-OK**: exit 0, |
| R03 | no shell: 'cat /etc/passwd' bad-character, 'id' unknown-verb, no command no-command | **PASS**: 'cat /etc/passwd': bad-character; 'id': unknown-verb; no command: no-command | `ForceCommand removed` → **FAILS-OK**: 'cat /etc/passwd': ; 'id': ; no command: ; A SHELL RAN |
| R04 | no terminal (ssh -tt), no forwarding (ssh -N -L) | **PASS**: terminal: refused; forward: no channel (open failed: administratively prohibited: open failed | `both locks removed (restrict and the sshd lines)` → **FAILS-OK**: terminal: granted; forward: opened (got SSH-2.0) |
| R06 | relay failures: password, fingerprint, port give exit 5 with the reason; node offline, relay down exit 4 within 5 s | **PASS**: wrong password: 5 relay-credential; wrong fingerprint: 5 relay-fingerprint; foreign port: 5 relay-rule; node offline: 4 node-offline in 0.2 s; relay down: 4 relay-unreach… | `bare-proxy` → **FAILS-OK**: wrong password: 4 unreachable; wrong fingerprint: 4 unreachable; foreign port: 4 unreachable; node offline: 4 … |
| R07 | no relay password in any process's command line while calls run | **PASS**: 0 of 30 scans of every process's command line found a relay password (workstation chisel processes: 1, with --auth: 0) | `auth-in-argv` → **FAILS-OK**: 30 of 30 scans of every process's command line found a relay password (workstation chisel processes: 1, with -… |
| R10 | push with git-lfs hooks installed succeeds | **PASS**: exit 0; queued; warnings: ["big.bin"] | `push-hooks (no --no-verify)` → **FAILS-OK**: exit 1; the snapshot could not be pushed to office-pc: Remote "mu-office-pc:lfsproj-88b2e9ef7860.git" does not… |
| R11 | snapshots are write-once: overwrite, delete and another ref name refused | **PASS**: overwrite:  ! [remote rejected] HEAD -> refs/mu/snap/20261114-000907-6sjlht (hook declined) error: failed to push some refs to 'mu-office-pc:woproj-32154-6a0ed7b6f28b.git… | `no-update-hook` → **FAILS-OK**: overwrite: To mu-office-pc:woproj-21766-d5193d98a666.git  + bca7408...45d3aa6 HEAD -> refs/mu/snap/20261114-00… |
| R12 | work folder equals the tree: export-ignore file present, export-subst unchanged | **PASS**: succeeded: tree-is-exact | `git-archive` → **FAILS-OK**: failed: |
| R13 | upload is incremental: one changed line grows the node's repositories by under 10 kB | **PASS**: first snapshot (2 MB of data) +1574990 bytes on the node; second (one line of job.py changed, uncommitted) +454 bytes [20261114-000911-ck4i0x 20261114-000911-122ig8] | `full-push` → **FAILS-OK**: first snapshot (2 MB of data) +1574966 bytes on the node; second (one line of job.py changed, uncommitted) +15… |
| R14 | durable result: the shim killed before result.json never gives succeeded; re-run once, then interrupted | **PASS**: interrupted, attempt 2; 1 re-run(s); note: the job's process ended without a result | `trust-unit-success` → **FAILS-OK**: succeeded, attempt 1; 0 re-run(s); note: |
| R15 | torn records: result.json / state.json full of NUL bytes read as absent, the job recovered from job.json | **PASS**: result.json NUL-filled: succeeded attempt 2 (re-run from job.json); state.json NUL-filled while queued: succeeded | `trust-existence` → **FAILS-OK**: result.json NUL-filled: succeeded attempt 1 (re-run from job.json); state.json NUL-filled while queued: succee… |
| R17 | memory limit: a job allocating twice its --mem ends as oom | **PASS**: oom: the job used more memory than its limit | `no-memory-max` → **FAILS-OK**: succeeded: |
| R18 | time limit and cancel: timeout and cancelled, and a grandchild process is gone | **PASS**: --time 4s: timeout; cancel: cancelled; grandchildren 15106 15388 still alive: none | `killmode-process` → **FAILS-OK**: --time 4s: timeout; cancel: cancelled; grandchildren 28112 28374 still alive: 28112 28374 |
| R19 | queue: three submits run one at a time in order of acceptance | **PASS**: start 20261114-000947-5ob83j end 20261114-000947-5ob83j start 20261114-000948-8hj820 end 20261114-000948-8hj820 start 20261114-000948-g0gl4a end 20261114-000948-g0gl4a | `start-on-submit` → **FAILS-OK**: start 20261114-001247-apazwe start 20261114-001247-f57p4c start 20261114-001247-dpj5wm end 20261114-001247-apa… |
| R20 | repeat submit: a lost answer, then --resume: one job (duplicate: true) | **PASS**: lost answer: exit 4; --resume: duplicate true; again: duplicate true; job folders 17 -> 17 -> 17 | `node-id` → **FAILS-OK**: lost answer: exit 4; --resume: duplicate false; again: duplicate false; job folders 37 -> 38 -> 39 |
| R21 | log resume: the link killed during log -f; the pieces joined by offset equal the node's log | **PASS**: joined pieces sha256 244c5b2ff54666e6, node's log 244c5b2ff54666e6 (3851 bytes; 1 resume(s)) | `log-from-zero` → **FAILS-OK**: joined pieces sha256 b7489ea22b813d5d, node's log 9be6e0fb49637ed0 (5307 bytes; 1 resume(s)) |
| R22 | fetch integrity: an output changed on the node after the result gives exit 8 and is not written | **PASS**: exit 8 (integrity); results/out.json in the project: absent | `no-fetch-hash` → **FAILS-OK**: exit 0 (); results/out.json in the project: present |
| R23 | fetch stays inside: a tar member outside the declared outputs is refused | **PASS**: exit 8 (integrity); outside.txt in the project: absent | `no-member-check` → **FAILS-OK**: exit 0 (); outside.txt in the project: WRITTEN |
| R24 | link drop: the tunnel killed during a job; the job finishes, status and fetch work after | **PASS**: succeeded, attempt 1; fetch after the tunnel came back: exit 0 | `job-in-session` → **FAILS-OK**: succeeded, attempt 2 |
| R25 | runner restart during a job: adopted, finishes once | **PASS**: succeeded, attempt 1, started 1 time(s) | `no-adopt` → **FAILS-OK**: failed, attempt 2, started 0 time(s) |
| R26 | version rules: missing pin exit 3; a registered pin survives mu gc --yes; job commands work in a project pinned without them | **PASS**: exit 3 toolchain-missing, says mu install v2026.11.4; mu status in a project pinned to v2026.11.6: exit 0; after mu gc --yes the node has: v2026.11.6 v2026.11.7 | `no-pin-register` → **FAILS-OK**: exit 3 toolchain-missing, says mu install v2026.11.4; mu status in a project pinned to v2026.11.6: exit 0; aft…<br>`route-by-pin` → **FAILS-OK**: exit 3 ; mu status in a project pinned to v2026.11.6: exit 3; after mu gc --yes the node has: v2026.11.6 v2026… |
| R27 | limits unavailable: with the self-test failing, submit is exit 6 | **PASS**: self-test failing: submit exit 6 (limits-unavailable) | `skip-limits-check` → **FAILS-OK**: self-test failing: submit exit 0 () |
| R30 | disk low: below min_free_gb, submit is exit 6 (disk-low) | **PASS**: min_free_gb 100000: submit exit 6 (disk-low): office-pc has 83 GB free (/srv/mu); jobs need 100000 GB | — |
| R31 | protocol mismatch: hello with protocol 2 is refused (65, protocol-mismatch) | **PASS**: hello protocol=2: exit 65, protocol-mismatch: protocol mismatch: the client speaks 2, this node 1 (accepts 1 to 1; μ v2026.11.7) | — |

Run on the unit level only (fast jobs): gate grammar (R05), snapshot leaves the project alone and its content (R08,
R09), atomic write (R16), pins agree (R28), bash 3.2 (R29), each with its fault.

### 21.4 Not tested, and why

- Everything of §19: WSL's cgroups and the memory limit there, interop and the drive mount, the office network, a
  Windows restart or `wsl --shutdown` during a job, the real relay and Traefik with real latency.
- The Keychain itself (a stub stands in for `security`; §17 E is the record of the real one); a session without a GUI.
- An unclean stop of the node (power cut): the recovery table is exercised by a shim that ends without a result,
  torn records and a runner restart, not by a stopped VM. fsync itself is untested: every test kills a writer with
  SIGKILL, which loses nothing that was written; only a power cut (a VM, dm-log-writes) would show a missing fsync.
- Since the review (§23), the runner's classification is also tested at unit level against stubs of `systemctl` and
  `systemd-run` that fail on purpose (`tools/test-jobs/unit.py runner`); before, only end to end on a systemd that
  never failed.
- `mu fetch` above 50 MB and transfer speed; `--max-size` is checked by a unit of the client only.
- `tools/test-autoupdate` runs on macOS only: on the Linux runner it reports shellcheck warnings on lines unchanged
  from `main` and two checks that depend on the machine (a leftover `sleep 599`, `systemd-analyze --user verify`).
  `tools/test-fetch` is timing-sensitive (failed once locally and once in CI, passed on the rerun; this branch does
  not touch the downloader) and is not in this workflow.

## 22. The host script: §13 in `mu-wsl2-distro.sh`, in CI

The root steps of §13 are in `install/hosts/wsl2/mu-wsl2-distro.sh`, and CI makes its node with them: since run
43541460952 the probe and e2e jobs no longer build the node by hand. The owner's visit is
`docs/design/first-job-checklist.md` (one sequence, about 15 minutes; it replaces the order of §19, whose points it
checks one by one).

### 22.1 What `install` does now, and the new commands

`install` adds, after the steps it had (each idempotent, a check in `status`, undone by `uninstall`):
`ensure_ssh_masked` (before the packages), `openssh-server`, `unattended-upgrades` and `python3` in
`ensure_packages`, the [interop]/[automount] keys in `ensure_wsl_conf` and `ensure_host_drive` (not with `--no-wsl`),
then `ensure_node_hostkey`, `ensure_sshd_config`, `ensure_sshd_unit`, `ensure_authorized_keys`, `ensure_gate`,
`ensure_job_root`, `ensure_cgroup_delegation`, `ensure_tunnel`, `ensure_security_updates`, `ensure_node_enabled`.
New options: `--host-drive L`, `--node-name N`, `--no-wsl`, `--wait S`. New commands (each with `--json` and
documented exit codes): `authorize NAME 'KEY'|-`, `revoke NAME`, `keys`, `host-key`, `tunnel credential`
(`tunnel-credential`), `tunnel status`, `windows-reach`. `mu-wsl2-host.ps1 install` passes `--host-drive` (the
letter of the distro's registered folder) and copies `mu-node-gate` in with the script.

`status` gains: `ssh.masked`, `openssh-server`, `listeners` (nothing on 22; an sshd only on `127.0.0.1:2222`),
`node.hostkey`, `sshd.config` (every value `sshd -T -C user=mu,host=localhost,addr=127.0.0.1` reports for the
installed file, compared with the text §13.1 specifies, plus no `Subsystem` and no `AcceptEnv`), `sshd.service`,
`keys` (every line has `restrict`), `gate` (byte-equal to what install writes), `jobroot`, `cgroup.delegation`,
`chisel` (the installed binary's SHA-256), `tunnel.credential` (owner and mode), `tunnel` (the last state in the
service's journal), `updates`, `test-fault`, `node.runner`, `node.limits`, `node.disk`, `node.hello` (the gate's
answer, run as sshd runs it); on WSL also `host-drive`, `reach.root`, `reach.mu.interop`, `reach.mu.files`,
`reach.mu.df`, `disk.sure`. The Windows script shows them as `linux.<name>` without a change.

### 22.2 Where it differs from §13, and why

| # | §13 (or the task) | Built | Why |
|---|---|---|---|
| 1 | the task: an sshd drop-in, `ssh.socket` off, `ssh.service` on | §13 as written: `mu-sshd.service`, `/etc/mu/sshd_config`, Ubuntu's units masked | §20 #2 decided it; CI proves the masked units never listen (`P6`, `H1 listeners`) |
| 2 | `tunnel-credential` | `tunnel credential` (the old name works too) and `tunnel status`. Input: the lines `relay/gen-credentials.sh client <node>` prints, as they are (CR, comments, `RELAY_PORTS`), or `AUTH=`/`RELAY_*` lines. From a terminal it reads to an empty line with echo off | the owner moves one file, unchanged; every refusal names the field, never the value (a unit test checks the password is in no message) |
| 3 | chisel's archive SHA-256 | the archive's and the unpacked binary's (`811362…2b52`), the binary's checked by `status` | `status` can tell that the installed binary is still the pinned one |
| 4 | `mu-tunnel.service` as §13.3 | plus `Group=`, `ConditionPathExists=/etc/mu/tunnel.env`, `PrivateDevices`, `ProtectKernelTunables/Modules`, `ProtectControlGroups`, `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX`, `RestrictNamespaces`, `RestrictRealtime`, `RestrictSUIDSGID`, `LockPersonality`, `SystemCallArchitectures=native`, empty capability sets, `UMask=0077` | a stronger sandbox for the one process that faces the relay; without a credential the unit is skipped, not failing. Proven on the runner's kernel, not yet on WSL's (§22.4) |
| 5 | uninstall undoes everything but the host key | it also keeps `/srv/mu` (job data) and the gate's source while the script stays; it removes `openssh-server` and `unattended-upgrades` only if install added them, and unmasks only the units install masked (`/var/lib/mu-wsl2/installed-packages`, `masked-units`) | data is not install's to delete; a reinstall from `/usr/local/sbin` needs the gate's source (CI run 43536617666 found it missing); uninstall must not remove what it did not add |
| 6 | the gate "with the two machine values written in" | its source travels as `/usr/local/lib/mu/mu-node-gate.in` (root, 0644): `mu-wsl2-host.ps1` copies it in, and a run from a checkout copies it from beside the script | `mu-wsl2-host.ps1` copies only the script itself into the distro |
| 7 | `ensure_cgroup_delegation` dropped (§20 #10) | kept, as asked, "only if needed": a drop-in only when `mu`'s user manager lacks `memory` or `pids` that the kernel has | on Ubuntu 24.04 it writes nothing (CI); it covers an image whose `user@.service` delegates less |
| 8 | `ensure_node_enabled` "when mu has a version with the job layer" | as said; its name is `--node-name`, else the relay user of the credential, else the host name; `tunnel credential` renames a node that took the host name | install runs before the credential exists on the PC (checklist 2.5) |
| 9 | `status` "every line of §13.1 compared with `sshd -T`" | `sshd -T` of the installed file, compared with the text install would write, not with the installed file | an edited file would otherwise agree with itself (`H1` with `PermitTTY yes` shows the check fails) |
| 10 | `windows-reach` (§19 step 2) | for `mu`, a copy of `whoami.exe` in `/var/lib/mu-wsl2/reach/` (removed after) runs in a `systemd-run --user` unit | `mu` cannot read `/mnt/c` after §13.4, so only interop decides |
| 11 | unattended upgrades, security pocket only | `/etc/apt/apt.conf.d/52mu-unattended-upgrades` with `#clear` of Ubuntu's lists, then the three security pockets; `apt-daily*.timer` unmasked and enabled if they were not | the runner had both timers disabled; `unattended-upgrade --dry-run` there lists only `noble-security`, `noble-apps-security`, `noble-infra-security` (`H5`) |
| 12 | — | `python3` among the packages | `status` reads `mu node info --json` |
| 13 | §19 step 3: `install`, then `test-stop` | `install -NoRestart`, then `test-stop` | install's own restart is `wsl --terminate`; `test-stop` is the clean stop, and it hands WSL to the startup task |
| 14 | — | from PowerShell, commands as `mu` pass `--cd ~` | after §13.4, `mu` cannot enter the Windows folder PowerShell starts in |
| 15 | — | `apt-get` is retried for up to 10 minutes while another apt holds a lock | with the daily apt timers on, `apt-daily` may be running `apt-get update`; `DPkg::Lock::Timeout` does not cover the lists lock (CI run 43539220281: a reinstall stopped with exit 100) |

### 22.3 CI (run 43541460952)

`tools/test-jobs/node-setup.sh` runs `mu-wsl2-distro.sh install --no-wsl` from the checkout, then
`tunnel credential` with the replica's `gen-credentials` output on stdin, then install twice more; `e2e.sh` pairs
the workstation with `host-key` and `authorize`, runs every row of §21.3 against that sshd, gate and tunnel, and adds:

| # | Check | Result, measured | Fault put in → result |
|---|---|---|---|
| S2 | the node made by the host script: install, `tunnel credential` on stdin, install twice more | **PASS**: 35 changes, then 1 (the runner, once μ of `mu` had the job layer), then 0 | — |
| S3 | `mu compute add` with `host-key`'s key, then `authorize` | **PASS** | — |
| H1 | the script's `status` (`--no-wsl --offline`) | **PASS**: 40 checks, none failed | `PermitTTY yes` in `/etc/mu/sshd_config` → **FAILS-OK** (`sshd.config: permittty yes (want no)`); a line added to the gate → **FAILS-OK** (`gate`); `/etc/mu/test-fault` present → **FAILS-OK**; `tunnel.env` 0644 → **FAILS-OK** (`tunnel.credential`); a listener on port 22 → **FAILS-OK** (`listeners`); the tunnel stopped → **FAILS-OK** (`tunnel`) |
| H2 | install is idempotent | **PASS**: the third run, `nothing to change, already set up` | — |
| H3 | `tunnel status` | **PASS**: connected (`client: Connected`); the relay password in no output of the set-up | — |
| H4 | `keys`, `host-key` | **PASS**: `avi-mac` with the workstation's fingerprint, its line with `restrict`; the host key's fingerprint is the one the workstation pinned | — |
| H5 | security pocket only | **PASS**: `unattended-upgrade --dry-run`: `Allowed origins are: o=Ubuntu,a=noble-security, o=UbuntuESMApps,a=noble-apps-security, o=UbuntuESM,a=noble-infra-security` | — |
| H6 | `uninstall` | **PASS**: nothing left (units, files, `mu-tunnel`, masks, runner, listener on 2222); the host key unchanged; `/srv/mu/jobs` kept | — |
| H7 | install again, credential, authorize | **PASS**: the workstation reaches the node with its old pin; one more install changes nothing | — |

Every row of §21.3 passed again against this node (R00–R31, P1–P7), and each of their faults made its row fail:
45 PASS, 30 FAILS-OK, nothing failed or missed.

Unit level (`tools/test-wsl2-host/run.sh`, macOS bash 3.2 and Linux, plus `ps-tests.ps1` under pwsh): the
`wsl.conf` merge with the Windows keys, the sshd text and its effective-value check (fixtures, and this machine's real
`sshd -T` of the generated file and of an edited copy), key names, public key validation (`ssh-keygen` decides: ECDSA,
two keys, a key cut short, a private key line, shell characters), the key file's put/del/list, `authorize`,
`revoke`, `keys` end to end on a test folder, the relay settings parser (seven refusals, the password in no message),
the tunnel log classification on chisel's lines, listeners, delegation, the drive record, the security pocket check,
the rendered gate, the pinned sums; `Get-HostDriveLetter` in PowerShell. Faults that must make their check fail:
`sshd-no-effective-check`, `no-name-check`, `no-keygen-check` (twice). `tools/test-jobs/run.sh`: the chisel pins of
the distro script, with a changed sum shown to fail.

### 22.4 Not tested until the PC

- Everything WSL does: `[interop] enabled=false` and the `[automount]` options after a restart of the distro, the
  `reach.*` and `disk.sure` checks, the host drive record from `mu-wsl2-host.ps1`, `windows-reach` (CI has no
  Windows; `--no-wsl` leaves these out).
- The startup task as `mu` once `mu` cannot enter `/mnt/c/WINDOWS/system32` (WSL is expected to report the failed
  `chdir` and still run `/bin/true`): checklist 2.7, `task.last-run`.
- The tunnel's sandbox under WSL's kernel, its egress filter there (cgroup BPF), and the tunnel through the office
  network and its proxy, if any. CI proves the filter on the runner's kernel (R32, and `status`'s `tunnel.egress`).
- `mu-tunnel-refresh.timer` following a real change of the relay's address (CI checks the files `tunnel refresh`
  writes and the filter they give, not a DNS change).
- `mu-wsl2-host.ps1`'s new code on Windows (`Copy-IntoDistro`, `--host-drive`, the gate on an elevated relaunch from a
  UNC path): parsed under PowerShell 5.1 rules and its pure function tested under pwsh 7 only.
- That `install` on the configured PC changes exactly the list in the checklist: CI starts from a fresh runner.
- `apt-get remove` of `openssh-server` on uninstall is exercised only where the package was preinstalled (the runner),
  so the removal branch itself ran only for `unattended-upgrades`.

## 23. Review

Three independent reviews of this branch (security, durability and correctness, usability) found 33 problems:
7 security findings (S1–S6, S1b) and 2 gaps, 10 durability findings (D1–D10), 16 usability findings (U1.1–U1.9,
U2.1–U2.7) and a table of inconsistencies. Each finding was re-read in the code. All were real except where a row
below says otherwise. Every security and durability fix has a test that is also run with the fault that undoes
the fix, and must then fail (§16, the rows added with the review; `tools/test-jobs/run.sh` runs 27 such
must-fail checks, `local.sh` one, e2e R05 and R32 and H1 one more each).

### 23.1 Security

| # | Finding | Fix | Test (fault) |
|---|---|---|---|
| S1 | **critical:** `mu fetch` took the allowed output globs from the node's own `result.json`; a node could write `.git/config` (a `core.fsmonitor` command runs at the next `git status`), `.claude/settings.json` hooks, `mu.toml`, sources | the globs come from this clone's record (`.mu/jobs/<id>.json`) only; without it, `--output` or exit 2 `no-local-record` | the real `mu fetch` against `fake-node-ssh.py` (`trust-node-globs`) |
| S1b | the Mac's glob matched hidden names (`**/*.json` took `.claude/settings.json`; `.GIT` is `.git` on macOS) | a wildcard matches no hidden name (as `glob.glob`); no path part `.git` or `.mu` after case folding and NFC/NFD | unit, and `glob_match` = `glob.glob` on a tree (`dot-globs`) |
| S2 | chisel's client dials whatever the relay names for a reverse remote: a compromised relay could use `mu-tunnel` to reach the distro's loopback, the Windows host, the office LAN, the internet | egress filter: `IPAddressDeny=any`, loopback and the relay's addresses only, the relay's name from a hosts file of the tunnel's own; hourly `tunnel refresh`; `status` proves it in effect | e2e R32 and H1 (`no-egress-filter`); host-script units |
| S3 | after `revoke`, a ControlMaster opened before could keep opening sessions (each one any verb) | the gate refuses a key that is not in the key file; `revoke` ends `mu`'s open ssh connections | unit gate, `local.sh` over a real master, e2e R05 (the gate's check removed + `revoke-keeps-sessions`) |
| S4 | the node's `hint` became the client's `next`; its extra keys overrode `exit`, `next`; `node`/`code` crashed the client; node text reached the terminal raw | `next` is the workstation's text per error code; node text in `node_message`, cleaned, capped; an allowlist of extra fields; every node string cleaned of control characters; a job's log is cleaned when stdout is a terminal | unit and the real CLI (`node-hint`) |
| S5 | the node could fill the Mac's memory (unbounded answers) or disk (a member longer than recorded was written to its end) | answers capped at 1 MB, the result at 16 MB; a member's tar size is checked before it is read and writing stops past it | unit, a 3 MB member recorded as 10 bytes (`no-size-guard`) |
| S6 | §14 understated what a paired key and the audit log mean; history travels with the push | §14 rewritten (a paired key = everything a job can do; `key=` lines are forgeable, sshd's are not); §6.1 says history travels | documentation |
| gap | interop off: can `mu` still reach WSL's interop socket? | a check at the visit (checklist 2.4) and §19 | at the PC |
| gap | root's cache fallback in `ensure_tunnel` read a file in `mu`'s `~/.cache` | a regular file, not a link, ≤ 50 MB, copied with a time limit, the copy's sum decides | by reading (the download works in CI) |
| hardening | `runuser` to `mu` with root's terminal (TIOCSTI) | `setsid -w` and stdin from `/dev/null` in `as_mu_sd` (TIOCSTI is off in WSL's kernel anyway) | the e2e set-up runs through it |

### 23.2 Durability and correctness

| # | Finding | Fix | Test (fault) |
|---|---|---|---|
| D1 | **high:** one failed `systemctl show` (bus error, user-manager re-exec, timeout) made a running job "interrupted": its `work/` and log deleted, a second start, the queue freed beside it, the result later overwritten | a failed `show` is "still running"; "ended" only for `not-found`, or `inactive`/`failed` with a `Result`; `result.json` written once | `unit.py runner` (`bus-error-is-end`, `overwrite-result`) |
| D2 | any `systemd-run` failure was `failed`, even when the unit had started | systemd is asked: a known unit stays running; an unknown one goes back to the queue (3 tries); no start once the runner has SIGTERM | `trust-run-rc` |
| D3 | a re-run deleted the log: a reader at byte N got attempt 2's bytes from N on, spliced | the log is kept, with a fsynced marker line; `log --json` has `attempt` | `log-truncate-on-rerun` |
| D4 | one job's I/O error stopped the whole runner; nothing capped a log; free space was not checked at start; a shim that could not write its result left no reason | per-job `try`; a job is never started beside one in an unknown state; no start below `min_free_gb` (a note says so); `log_cap_gb` (4) stops a job; the shim writes why to the log and the runner's note quotes it | `runner-pass-aborts`, `no-start-disk-check`, `no-log-cap` |
| D5 | a torn `state.json` made a running job "queued" (started twice, attempt reset); a torn `job.json` made recovery delete the folder | the state is rebuilt from `events.log` and the unit; damaged folders go to `/srv/mu/damaged/`, never deleted (submit's duplicate path too) | `no-unit-check-torn`, `delete-damaged` |
| D6 | a new project repository was renamed before its files were on disk | `os.sync()` before the rename | by reading (a power cut is not testable here) |
| D7 | the pin was registered after the job existed (a crash between: auto-update could remove the version) | registered before the rename, and again on a duplicate submit | by reading; R26 covers registration |
| D8 | fetched outputs were not fsynced, the run record was | each output fsynced before its rename, the folders synced at the end | by reading |
| D9 | the queue followed the wall clock (WSL's can step back); the busy marker is dropped on SIGTERM with jobs running | `accepted_ns` never goes back (node.json keeps the last). **The busy marker is not changed:** `au_busy` already ignores a marker whose pid has exited, so keeping it would not hold anything back; `keep_previous=1` keeps the running job's toolchain for one switch | unit (the clock) |
| D10 | tests: classification only end to end; fsync untested; nothing checked that every row ran | the runner section of `unit.py`; Z1/Z2 compare the rows and faults that ran with the expected list; §21.4 says fsync is untested | Z1, Z2 |

### 23.3 Usability (Claude Code, and the owner)

| # | Finding | Fix |
|---|---|---|
| U1.1 | `--timeout` ran out with exit 4 ("unreachable") | exit 10 `still-running`; `mu submit --wait --timeout`; `mu status --wait` without an id is exit 2 |
| U1.2 | a refused or offline node still cost a snapshot, a ref and a record; errors lacked the job id | `hello` before the snapshot; over 100 MB uncommitted is exit 2 unless `--allow-large`; `job_id` and `--resume` in every later error; `mu cancel` forgets an unaccepted job; refs of accepted jobs beyond the newest 10 are removed |
| U1.3 | no Claude-facing doc for remote jobs | `claude/CLAUDE-toolchain.md`, "Compute jobs on a remote node" |
| U1.4 | `--relay-password-stdin` echoed a typed password | `getpass` at a terminal; `host-key` prints the command with the pipe |
| U1.5 | hints named the wrong command, name or form | `host-key`; `authorize <workstation> '<key>'` with its fingerprint; `--cd ~` in every `-u mu` hint; `toolchain-missing` and `disk-low` say what to do at the PC. **Not added:** a `mu node prune` verb (a paired key could then delete others' results); the hint names `rm -rf /srv/mu/jobs/<id>` |
| U1.6 | thin help | `mu compute --help`, help strings, exit codes in every parser's epilog and in `mu help`, `mu node --help`, a forgotten `--` explained |
| U1.7 | the `--json` contract | the dispatcher's own errors in JSON (rev 8); `submit --wait --json` includes `submitted`; `exit` in the status object (`ok` = exit 0); concrete `next` everywhere |
| U1.8 | `mu` said nothing of nodes and jobs | two local lines (no network) and the cheat-sheet |
| U1.9 | a long push was silent | one line before the push (snapshot, HEAD, uncommitted). **Not done:** git's `--progress` (stderr is parsed for errors) |
| U2.1 | "about 15 minutes" | 30–45 min at the PC; the hotspot, the first job and the three endings moved after the visit (Mac only); §0 asks for a release with no layer change, tagged two days ahead |
| U2.2 | no single "ready" check | `status` ends with `ready for jobs: yes/no (…)`, `ready` in JSON, `linux.ready` on Windows; checklist 2.11 |
| U2.3 | three restart instructions | `--from-host`: the distro script leaves it to the ps1, which says `test-stop` |
| U2.4 | two authorize commands; blind typing of six values | `mu compute add` prints both forms and writes `authorize.txt`; `tunnel credential` asks field by field, the password hidden and asked twice |
| U2.5 | `Copy-Item` nesting | `New-Item` and `…\wsl2\*` |
| U2.6 | 2.13's snippet broke on a failed submit | `--wait` forms; the cancel case guarded |
| U2.7 | §19 was a stale second sequence | replaced by a pointer to the checklist and the list of open points |
| table | `MU_JOB_CORES` "for the wrappers" | says the job passes it on; the other rows are covered above |

### 23.4 What each party can still do

- **A holder of a paired workstation key**, or **any job** (they are the same power): run anything as `mu` on the
  node; read and change every other workstation's projects, snapshots, logs and results there; forge the `key=`
  attribution in `audit.log`; make another workstation's `mu fetch` bring back wrong *content* for the files that
  workstation declared (each checked against the node's own record, which the same party controls). They cannot get
  root, read the relay credential, add a key, reach Windows (if §13.4 holds at the PC), or make a fetch write outside
  the declared globs or into `.git`/`.mu`, or steer a workstation's `next`.
- **A compromised relay or Dokploy host:** see, delay or cut the encrypted ssh traffic, and reach the node's sshd
  login (key-only). With the egress filter, nothing else through the node's tunnel; without it (a kernel without
  cgroup BPF: `tunnel.egress` fails) the reach of S2 remains.
- **A revoked key:** nothing from the moment `revoke` returns (new sessions refused by the gate, open connections
  ended).

### 23.5 CI

Run 43547849592 on commit e486484, all four jobs green:

- fast tests, Linux: `tools/test-jobs/run.sh` 247 passed (27 must-fail checks among them), `local.sh` 34 (the revoke
  over a real ControlMaster and its must-fail included), `test-wsl-disk` 29, `test-wsl2-host` 185;
- fast tests, macOS (bash 3.2): `run.sh` 247, `local.sh` 34, `test-autoupdate` 254, `test-wsl2-host` 185;
- probe: green;
- end to end: 49 PASS (47 rows, then Z1 and Z2: every expected row and fault ran), 33 faults each shown to fail,
  0 missed. R05: mu's ssh connections on the node 1 before the revoke, 0 after, a call after it exit 5; with the
  faults, 1 after and the call works. R32: TCP to mu-sshd works through the filter, TCP to the runner's own address
  times out and a UDP send there gets "Operation not permitted"; without the filter all three go through. H1:
  `ready for jobs` true, and false with the egress filter removed.

The first run (43546105043) found three things the local tests could not: the filter drops a TCP SYN silently, so
the probes use UDP; the workstation's ssh master outlives the server side for a while, so R05 counts the node's
side; and two older faults no longer bit (R22's appended bytes are now caught by the size check before the hash
check, so the tamper keeps the size; the systemd-run check made `no-adopt` harmless, so the fault now stops the
unit and runs it again). A `test-autoupdate` check on macOS failed once in that run (a lock of the previous,
killed install still held; unchanged code) and passed in the next. `tools/test-fetch` passed locally (it fails
under load, as before; not in the workflow).

Not tested, in plain words: the egress filter, the refresh timer and the ready line on WSL's kernel (only the
runner's); a real change of the relay's address; fsync under a power cut; the fetch of a real job from the PC.
