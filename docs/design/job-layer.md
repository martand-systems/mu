# μ job layer, first version: OpenSSH inside the relay tunnel

The specification for stage 1 of `docs/PLAN.md` §6.4a. It replaces the design note of 2026-11-13 and corrects it
with what was measured on 2026-11-14 (§17) and read in sources (§18). Stage 1 is implemented on branch `job-layer`
and proven in CI (§21, which also lists where the implementation differs from the text above); nothing has run on
the office PC. What can only be checked at the PC is listed, in order, in §19.

Sections: [1 Summary](#1-summary) · [2 Architecture](#2-architecture) · [3 Where the code lives](#3-where-the-code-lives) ·
[4 Workstation](#4-workstation-files-and-commands) · [5 The connection](#5-the-connection) · [6 Upload](#6-upload-a-git-snapshot) ·
[7 Protocol](#7-protocol-gate-verbs-records) · [8 Node](#8-node-layout-runner-job-units) ·
[9 Durability](#9-durability-and-recovery) · [10 Limits](#10-limits-and-queue) · [11 Outputs](#11-outputs-logs-run-records) ·
[12 Versions](#12-toolchain-versions) · [13 Root steps](#13-what-needs-root-on-the-node) · [14 Security](#14-who-can-do-what) ·
[15 Stages](#15-stages) · [16 Tests](#16-tests-and-how-each-is-shown-to-fail) · [17 Verified](#17-verified-by-experiment) ·
[18 Read](#18-read-in-sources-not-run) · [19 PC](#19-still-unverified-until-the-pc) · [20 Changes](#20-changes-from-the-design-note-and-from-plan-v6) ·
[21 Implemented](#21-implemented-stage-1-in-ci)

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
| `install/hosts/wsl2/mu-wsl2-distro.sh` | new root steps (§13) |
| `install/hosts/wsl2/mu-wsl2-host.ps1` | passes the distro's drive letter to the distro script (§13.4); nothing else changes on Windows |
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

| Command | Network | What it does |
|---|---|---|
| `mu compute add <node> --host-key 'ssh-ed25519 AAAA…' --relay URL --relay-fingerprint FP --relay-user NAME [--relay-port 7001] [--secret-store keychain\|file] --relay-password-stdin` | none | Makes the folder and the key, writes `known_hosts`, `ssh_config`, `node.conf`, stores the relay password (§5.3; read from stdin, or from `MU_RELAY_PASSWORD`; never an argument). Prints the workstation's public key, its SHA256 fingerprint, the host key's fingerprint, and the one command for the node: `wsl -d mu-compute -u root -- mu-wsl2-distro authorize <workstation> '<public key>'`. Refuses an existing node (`mu compute remove` first), a host key that is not one ed25519 key, a password outside `[A-Za-z0-9_-]{24,}` |
| `mu compute status [<node>]` | `hello` | The node's answer (§7.3), the path (`relay`), the time of the round trip and whether the connection was reused. Exit 0, 4 or 5 |
| `mu compute list` | none | Paired nodes with `last-status.json` |
| `mu compute remove <node>` | none | Ends a control connection, deletes the folder and the Keychain item, prints the revoke command for the node |
| `mu compute proxy <node>` | | Internal: the ssh `ProxyCommand` (§5.2) |
| `mu submit [--on <node>] [--name N] [--mem 16G] [--time 6h] [--cores N] [--output GLOB]… [--no-rerun] [--wait] -- <command…>` | `hello`, push, `submit` | §6, §7. `--on` may be left out when exactly one node is paired. `local` and `auto` are stage 2: exit 2 with that message |
| `mu status [<job-id>] [--wait [--timeout S]]` | `status` or `list` | One job, or this project's jobs (the files in `.mu/jobs` joined with the node's `list`). `--wait` asks every 5 s on the shared connection until the job has a result; after a lost link it reconnects for up to `--timeout` (default: no limit). Exit 7 when the job ended otherwise than `succeeded` |
| `mu log <job-id> [--tail N] [--offset B] [-f]` | `log` | Prints the job's log from byte `B`. `--json` (not with `-f`) answers `{job_id, offset, next_offset, text, state, eof}`, so a caller polls with `--offset next_offset`. `-f` follows until the job has a result and resumes from its own byte count after a lost link |
| `mu fetch <job-id> [--into DIR] [--max-size 50M]` | `fetch` | §11. Writes the declared outputs and `runs/<job-id>.json`. Refuses a job without a result (exit 2: "still running") |
| `mu cancel <job-id>` | `cancel` | §8.4 |

`mu submit`, in order. Each refusal happens before anything is sent.

1. Project root: the nearest `mu.toml` upwards. That folder must be the top of a git work tree with at least one
   commit. Otherwise exit 2: "not a μ project", "mu.toml must be at the top of the git repository", "commit once
   first".
2. Pin: `[mu] version`. Missing or `unreleased`: exit 2 with `mu pin` as the hint.
3. Outputs: every `--output`, else `[compute] outputs` in `mu.toml` (a list of globs relative to the project
   root), else none, with the note "no outputs declared: only the log and the result record come back".
4. Job id (§7.4), then the snapshot (§6). Gitlinks and LFS pointers in the snapshot are listed as warnings.
5. `hello`: protocol, pinned version installed on the node (else exit 3), disk and limits (else exit 6).
6. Push the snapshot (§6.3).
7. `submit` with the job spec on stdin (§7.5). The node answers after its fsyncs.
8. Write `.mu/jobs/<job-id>.json`. Print the job id, state and queue position.

A submit interrupted after step 4 is repeated with **the same job id** (`mu submit --resume <job-id>` reads the
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
- `refs/mu/snap/<job-id>` stays in the project's repository: `git diff <S>` shows later exactly what ran.

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
| a job folder without a valid `job.json` | deleted: the submit was never acknowledged, and the workstation sends it again with the same id |
| valid `result.json` | finished |
| `cancel` marker, no result | `result.json = cancelled` |
| `queued`, or a valid `job.json` and no valid `state.json` | queued (in `accepted_ns` order) |
| `running`, unit still active | adopted: the runner restarted, the job did not |
| `running`, unit ended with a failure `Result` | classified (§8.5) |
| `running`, no unit, no valid result | attempt 1 and `rerun` true: `work/`, `index` and `log` deleted, back to `queued` with its old `accepted_ns` (so it is first), attempt 2. Otherwise `result.json = interrupted` |

Each recovery writes to `events.log` whether the node's previous stop was clean (the last `previous-stop=` line of
`/var/lib/mu-wsl2/stops.log`; `unknown` where that file does not exist). `mu status` shows it in `note`.

Not protected: the last seconds of `log` after a hard stop (about 6 s with the write-back settings of the host
script). The log of a re-run job is discarded with its `work/`.

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
| `MU_JOB_CORES` | `--cores`, else the physical cores | for the wrappers (MPI ranks, OpenMP threads). No CPU quota: with one job it adds nothing |

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
  entry of `outputs` in the result it just read, and its size and SHA-256 match; it writes each through a temp file
  and a rename under `--into` (default: the project root). Anything else in the stream, or a mismatch, is exit 8
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
`/etc/mu/tunnel.env`.

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
| The relay container, or a holder of a relay credential | reach the node's ssh login | read or alter job data; pass as the node |
| A paired workstation key | the verbs of §7.3, **which include running any command as `mu`** | get a terminal, forward ports, use sftp, read job data back through git, add or change keys, become root |
| A job (project code as `mu`) | read and write everything `mu` owns: other jobs, the μ install, the read-only deploy key; open network connections from the PC, the office LAN included | add an ssh key (root-owned file), read the relay credential (root-owned, another user), start Windows programs or open Windows files (§13.4, to be confirmed at the PC), become root |

- `sshd` runs as root inside the distro, so a flaw in its login code would give root there. It is reachable only
  through the relay credential or from the PC itself, and gets Ubuntu's security updates.
- The gate and `mu node serve` are not a defence against `mu`. `sshd`'s own settings are.

## 15. Stages

| Stage | In | Out |
|---|---|---|
| **1** (this document; proven in CI) | dispatcher rev 7; `mu compute add / list / status / remove`; `mu submit / status / log / fetch / cancel`; the snapshot push; the gate; `mu node enable / disable / selftest / serve / run / run-job`; queue; memory and time limits; durable records and the recovery table; run records; pin refusal and pin registration; the chisel pin; the root steps of §13; relay path only | `--on local` and `auto`; remote toolchain install; clean-up of job folders; submodule and LFS content; `fetch --all`; a LAN path; more than one node per call |
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
| GitHub `ubuntu-24.04` runners | A VM with systemd as PID 1 and cgroup v2; `loginctl enable-linger` plus `XDG_RUNTIME_DIR=/run/user/<uid>` gives `systemctl --user` for another user | **Not confirmed here.** The probe job of §16 is first |

Sources: systemd `units/user@.service.in` at v255; microsoft/WSL PR 40519 and `wsl.dev/technical-documentation/interop`;
Ubuntu's `openssh` `README.Debian` and `ssh.service` (noble); `receive.denyNonFastForwards` in git's
`builtin/receive-pack.c`; chisel 1.12.0 `share/cos/signal.go` (one handled signal, then the default).

## 19. Still unverified until the PC

Before the visit: the release with the job layer is merged, and the laptop runs it. Bring the laptop. Three texts
must be at hand at the PC: the relay settings of `office-pc` (`relay/gen-credentials.sh client office-pc` on the
laptop; the password is a secret), and the laptop's public key (printed by step 5).

The owner's sequence, in order. Each step prints PASS or FAIL lines; a FAIL has its fix next to it and stops the
sequence. Nothing before step 3 changes the PC.

| # | Where | Do | Settles |
|---|---|---|---|
| 0 | PC | `wsl -d mu-compute -u mu -- mu --version`. If it is not the new release: `wsl -d mu-compute -u mu -- mu update` | auto-update carried a release on the PC (unproven so far, PLAN §14) |
| 1 | PC | `wsl -d mu-compute -u mu -- mu node selftest` | **cgroup memory limits for user units under WSL 3.0.5.** FAIL: read `stat -fc %T /sys/fs/cgroup` and `cat /proc/1/cgroup`; on an older WSL add `kernelCommandLine = cgroup_no_v1=all` to `.wslconfig`, restart WSL, repeat |
| 2 | PC | `wsl -d mu-compute -u root -- mu-wsl2-distro windows-reach` (new; changes nothing) | whether a process of `mu` in a `systemd-run --user` unit can start `/mnt/c/Windows/System32/whoami.exe` and read `C:` today. The answer goes into PLAN §11 either way |
| 3 | PC, elevated PowerShell | `mu-wsl2-host.ps1 install` from the new release, then `mu-wsl2-host.ps1 test-stop` (restarts the distro, so `wsl.conf` applies). The new script is in the release μ installed in the distro: `\\wsl.localhost\mu-compute\home\mu\.local\share\mu\toolchains\<version>\recipe\install\hosts\wsl2\`. Run it from there this once, before any job has run on the node (afterwards that folder is writable by jobs; later runs use the copy in `C:\Mu\host`) | the root steps of §13 on real WSL; that `openssh-server` installs with port 22 never open |
| 4 | PC | `wsl -d mu-compute -u root -- mu-wsl2-distro status` | interop is off for `mu` and for root; `ls /mnt/c` fails and `df /mnt/c` works for `mu` (FAIL: `[automount] enabled=false`, §13.4); the disk check names `C:` as sure; `mu-sshd` listens on `127.0.0.1:2222` only; the effective sshd settings; the runner is active after the restart (linger); `limits.memory` |
| 5 | Laptop | `mu compute add office-pc --host-key '<from step 4>' --relay https://relay.martand.systems --relay-fingerprint <FP> --relay-user avi-mac --relay-password-stdin`; compare the host key fingerprint with the PC's screen | the Keychain write on the real Mac |
| 6 | PC | `wsl -d mu-compute -u root -- mu-wsl2-distro tunnel-credential` (paste the four lines), then `… authorize avi-mac '<public key from step 5>'`; compare the fingerprint with the laptop's screen | **the PC's end of the tunnel in WSL2, through the office network** (S4's open item): `status` shows `tunnel: Connected` |
| 7 | Laptop, on the office network, then on a phone's hotspot | `mu compute status office-pc` | the whole path; S4's second network; the real handshake time, PAM included |
| 8 | Laptop | the `fea-cantilever` benchmark as a job from a small project (a folder with a `mu.toml` and one commit; the test suite of stage 1 has it ready): `mu submit`, `mu status --wait`, `mu log`, `mu fetch`; compare with the local reference | the first real job; `oom`, `timeout` and `cancel` once each with a small script |
| 9 | PC | during a 2-minute job: `mu-wsl2-host.ps1 test-stop`; then `mu status` on the laptop | a clean stop during a job: one re-run, a correct result, `note` says so. (The unclean cases are stage 2, with the S3 restart tests of `install/hosts/wsl2/README.md`.) |

Also open after the visit: the first Windows Update restart during a job; a WSL update that changes the cgroup
layout (the self-test runs again at each boot and refuses jobs if limits are gone); idle beyond five minutes and a
relay redeploy with a session open; the Keychain when the Mac is used over ssh.

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
