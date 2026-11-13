"""μ job layer, workstation side (docs/design/job-layer.md §4-§6, §11): pairing with a compute node, the ssh connection
through the relay, the ProxyCommand that supervises the chisel client, the git snapshot, and the commands
  mu compute add|list|status|remove|proxy, mu submit, mu status, mu log, mu fetch, mu cancel.
Standard library only. Every command takes --json and exits with the codes in proto.py.

Test hooks: MU_SSH (the ssh program), MU_SECURITY (instead of /usr/bin/security), MU_CHISEL (the chisel client),
MU_POLL_S (seconds between status polls, default 5), MU_JOBS_FAULT (comma-separated faults, tools/test-jobs):
  no-strict-hostkey  the generated ssh_config says StrictHostKeyChecking no
  bare-proxy         `mu compute proxy` execs chisel instead of supervising it
  auth-in-argv       the relay credential is passed to chisel as --auth (an argument)
  user-index         the snapshot runs `git add -A` on the user's own index
  no-mu-exclude      the snapshot does not leave out .mu/
  push-hooks         the push runs git's hooks (no --no-verify, no core.hooksPath=/dev/null)
  full-push          each submit pushes to a new repository on the node (a full copy)
  log-from-zero      `mu log -f` resumes from byte 0 after a lost link
  no-fetch-hash      fetch does not compare sizes and SHA-256
  no-member-check    fetch writes any regular tar member, named or not
  lose-answer        submit stops after sending, before reading the node's answer (a lost link)
"""
import argparse
import base64
import getpass
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time

from mu import durable, proto
from mu.durable import fault
from mu.proto import MuError

SERVICE = "systems.martand.mu.relay"
DEFAULT_PORT = 7001
CALL_TIMEOUT = 300


# ------------------------------------------------------------------------------------------------ paths and files
def mu_home():
    h = os.environ.get("MU_HOME")
    if h:
        return h
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Mu")
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "mu")


def compute_root():
    return os.path.join(mu_home(), "compute")


def node_dir(name):
    return os.path.join(compute_root(), name)


def ensure_dir(d, mode=0o700):
    os.makedirs(d, mode=mode, exist_ok=True)
    return d


def read_conf(path):
    """key=value lines, read as text (never executed)."""
    conf = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                conf[k.strip()] = v.strip()
    except OSError:
        return None
    return conf


def write_conf(path, conf, header):
    text = "# %s\n" % header + "".join("%s=%s\n" % (k, conf[k]) for k in conf)
    durable.write_durable(path, text, 0o600)


def paired_nodes():
    try:
        names = sorted(os.listdir(compute_root()))
    except OSError:
        return []
    return [n for n in names if proto.NODE_NAME.match(n) and os.path.isfile(os.path.join(compute_root(), n, "node.conf"))]


def load_node(name):
    if not proto.NODE_NAME.match(name or ""):
        raise MuError(proto.EXIT_USAGE, "bad-node-name", "'%s' is not a node name ([a-z0-9][a-z0-9-]{0,31})" % name,
                      "mu compute list")
    conf = read_conf(os.path.join(node_dir(name), "node.conf"))
    if conf is None:
        raise MuError(proto.EXIT_NO_SUCH, "no-such-node", "no compute node '%s' is paired with this computer" % name,
                      "mu compute list; pair one with: mu compute add %s --host-key ... --relay ..." % name)
    conf["_dir"] = node_dir(name)
    return conf


def pick_node(on):
    if on in proto.RESERVED_NODES:
        raise MuError(proto.EXIT_USAGE, "stage-2", "--on %s comes with stage 2 of the job layer; stage 1 runs jobs on a "
                      "paired node only" % on, "mu compute list, then --on <node>")
    if on:
        return load_node(on)
    nodes = paired_nodes()
    if len(nodes) == 1:
        return load_node(nodes[0])
    if not nodes:
        raise MuError(proto.EXIT_NO_SUCH, "no-node", "no compute node is paired with this computer",
                      "mu compute add <node> --host-key '...' --relay URL --relay-fingerprint FP --relay-user NAME "
                      "--relay-password-stdin")
    raise MuError(proto.EXIT_USAGE, "which-node", "%d nodes are paired (%s): say which one" % (len(nodes), ", ".join(nodes)),
                  "--on <node>")


def safe_path_for_config(p):
    """ssh_config and the ProxyCommand's shell carry these paths in double quotes."""
    if any(c in p for c in '"\'\\$`\n\r'):
        raise MuError(proto.EXIT_USAGE, "unsafe-path", "the path %s holds a character ssh_config cannot carry "
                      "(\" \\ $ ` or a line break)" % p, "set MU_HOME to a folder without them")
    return p.replace("%", "%%")


# ------------------------------------------------------------------------------------------------ the relay password
def relay_account(conf):
    host = re.sub(r"^[a-z]+://", "", conf["relay_url"]).split("/")[0]
    return "%s/%s" % (host, conf["relay_user"])


def security_bin():
    return os.environ.get("MU_SECURITY") or "/usr/bin/security"


def store_secret(conf, d, password):
    if conf["secret_store"] == "file":
        durable.write_durable(os.path.join(d, "relay.auth"), "%s:%s\n" % (conf["relay_user"], password), 0o600)
        return
    # security -i reads its command from stdin, so the password is in no argument list. Never `-w` as the last
    # option: with no terminal and an empty stdin that stores an EMPTY password and exits 0 (§17 E).
    line = 'add-generic-password -a "%s" -s "%s" -l "μ relay" -w "%s" -U\n' % (relay_account(conf), SERVICE, password)
    try:
        p = subprocess.run([security_bin(), "-i"], input=line.encode(), capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise MuError(1, "keychain", "could not store the relay password in the login Keychain (%s)" % e,
                      "unlock the keychain and retry, or use --secret-store file")
    if p.returncode != 0:
        raise MuError(1, "keychain", "security -i failed (exit %d): %s" % (p.returncode, p.stderr.decode(errors="replace").strip()),
                      "unlock the keychain and retry, or use --secret-store file")
    if read_secret(conf, d) != password:
        raise MuError(1, "keychain", "the Keychain did not keep the relay password (read back differs)",
                      "retry, or use --secret-store file")


def read_secret(conf, d):
    if conf.get("secret_store") == "file":
        try:
            with open(os.path.join(d, "relay.auth"), encoding="utf-8") as f:
                user, _, pw = f.read().strip().partition(":")
        except OSError:
            raise MuError(1, "secret-missing", "the relay password file of %s is missing" % conf.get("name"),
                          "mu compute remove %s, then mu compute add again" % conf.get("name"))
        return pw
    try:
        p = subprocess.run([security_bin(), "find-generic-password", "-a", relay_account(conf), "-s", SERVICE, "-w"],
                           capture_output=True, timeout=10, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise MuError(1, "keychain-locked", "the login keychain is locked or asks for permission",
                      "unlock it (open Keychain Access, or log in at the Mac), or store the password in a file: "
                      "mu compute remove %s, then mu compute add ... --secret-store file" % conf.get("name"))
    except OSError as e:
        raise MuError(1, "keychain", "could not run %s: %s" % (security_bin(), e), "--secret-store file")
    if p.returncode == 44:
        raise MuError(1, "secret-missing", "the relay password of %s is not in the Keychain" % conf.get("name"),
                      "run mu compute add again (mu compute remove %s first)" % conf.get("name"))
    if p.returncode != 0:
        raise MuError(1, "keychain", "security find-generic-password failed (exit %d)" % p.returncode,
                      "unlock the keychain, or use --secret-store file")
    return p.stdout.decode("utf-8", "replace").rstrip("\n")


def delete_secret(conf):
    if conf.get("secret_store") == "keychain":
        try:
            subprocess.run([security_bin(), "delete-generic-password", "-a", relay_account(conf), "-s", SERVICE],
                           capture_output=True, timeout=10, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired):
            pass


# ------------------------------------------------------------------------------------------------ ssh configuration
def ssh_config_text(name, home=None):
    home = home or mu_home()
    d = safe_path_for_config(os.path.join(home, "compute", name))
    mu = safe_path_for_config(os.path.join(home, "bin", "mu"))
    strict = "no" if fault("no-strict-hostkey") else "yes"
    return """Host mu-{n}
  HostKeyAlias mu-{n}
  User mu
  IdentityFile "{d}/id_ed25519"
  IdentitiesOnly yes
  IdentityAgent none
  UserKnownHostsFile "{d}/known_hosts"
  GlobalKnownHostsFile /dev/null
  StrictHostKeyChecking {strict}
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
  ProxyCommand "{mu}" compute proxy {n}
""".format(n=name, d=d, mu=mu, strict=strict)


def sock_limit():
    return 103 if sys.platform == "darwin" else 107


def control_path(name, home=None):
    """<dir>/<node>.sock in the first of $MU_HOME/run and /tmp/mu-<uid> where the socket path fits: ssh adds 17
    bytes while it makes the socket, and from 87 bytes on (macOS) the command itself fails (§5.1)."""
    home = home or mu_home()
    for d in (os.path.join(home, "run"), "/tmp/mu-%d" % os.getuid()):
        p = os.path.join(d, name + ".sock")
        if len(p.encode()) + 17 > sock_limit():
            continue
        if d.startswith("/tmp/mu-"):
            try:
                os.mkdir(d, 0o700)
            except FileExistsError:
                pass
            except OSError:
                continue
            st = os.lstat(d)
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
                continue
        else:
            ensure_dir(d)
        return p
    return "none"


def host_key_parse(text):
    """'ssh-ed25519 AAAA… [comment]' -> (type, base64, fingerprint) or MuError."""
    parts = (text or "").strip().split()
    if len(parts) < 2 or parts[0] != "ssh-ed25519" or "\n" in (text or "").strip():
        raise MuError(proto.EXIT_USAGE, "bad-host-key", "--host-key must be one ed25519 public key: 'ssh-ed25519 AAAA…'",
                      "copy it from the PC: wsl -d mu-compute -u root -- mu-wsl2-distro status")
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except Exception:
        raise MuError(proto.EXIT_USAGE, "bad-host-key", "the host key's base64 is damaged", "copy the key again")
    if not blob.startswith(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20") or len(blob) != 51:
        raise MuError(proto.EXIT_USAGE, "bad-host-key", "that is not an ed25519 host key", "copy the key again")
    return parts[0], parts[1], key_fingerprint(blob)


def key_fingerprint(blob):
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


# ------------------------------------------------------------------------------------------------ calls to the node
def ssh_bin():
    return os.environ.get("MU_SSH") or "ssh"


class Conn:
    """One node: `ssh -F <ssh_config> -o ControlPath=<P> mu-<node> <request>`, classified on failure (§5.2)."""

    def __init__(self, conf):
        self.conf, self.name, self.dir = conf, conf["name"], conf["_dir"]
        self.cfg = os.path.join(self.dir, "ssh_config")
        self.cp = control_path(self.name)
        self.cp_opt = "ControlPath=none" if self.cp == "none" else 'ControlPath="%s"' % self.cp.replace("%", "%%")
        self.run_dir = ensure_dir(os.path.join(mu_home(), "run"))

    def env(self, status_file):
        e = {k: v for k, v in os.environ.items() if k not in ("SSH_AUTH_SOCK", "SSH_ASKPASS", "SSH_ASKPASS_REQUIRE",
                                                                "DISPLAY")}
        e["MU_PROXY_STATUS"] = status_file
        e["MU_HOME"] = mu_home()
        return e

    def argv(self, request, opts=()):
        return [ssh_bin(), "-F", self.cfg, "-o", self.cp_opt] + list(opts) + ["mu-" + self.name, request]

    def new_status(self):
        self._sweep()
        fd, path = tempfile.mkstemp(prefix="proxy.", suffix=".status", dir=self.run_dir)
        os.close(fd)
        return path

    def _sweep(self):   # status files of calls that never cleaned up (killed clients): older than a day
        try:
            for f in os.listdir(self.run_dir):
                p = os.path.join(self.run_dir, f)
                if f.startswith("proxy.") and f.endswith(".status") and time.time() - os.path.getmtime(p) > 86400:
                    os.unlink(p)
        except OSError:
            pass

    def muxed(self):
        try:
            return stat.S_ISSOCK(os.stat(self.cp).st_mode)
        except OSError:
            return False

    def close_master(self):
        if self.cp == "none":
            return
        try:
            subprocess.run([ssh_bin(), "-F", self.cfg, "-o", self.cp_opt, "-O", "exit", "mu-" + self.name],
                           capture_output=True, timeout=15, stdin=subprocess.DEVNULL, env=self.env(os.devnull))
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            os.unlink(self.cp)
        except OSError:
            pass

    def run(self, request, data=None, timeout=CALL_TIMEOUT, retry=True, quick=False):
        """-> (rc, stdout bytes, stderr text, status tuple or None, muxed). Never raises for a node's own exit code.
        QUICK (hello, status, list): on a shared connection, give up after 12 s, end that master and ask once more on
        a new connection. A master whose link died without a close (the node's tunnel gone) would otherwise hold the
        call until ssh's keepalive gives up (3 x 15 s)."""
        status = self.new_status()
        muxed = self.muxed()
        try:
            p = subprocess.run(self.argv(request), input=data if data is not None else None,
                               stdin=subprocess.DEVNULL if data is None else None, capture_output=True,
                               timeout=min(timeout, 12) if (quick and muxed and retry) else timeout, env=self.env(status))
            rc, out, err = p.returncode, p.stdout, p.stderr.decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            st = read_status(status)
            _unlink(status)
            if quick and muxed and retry:
                self.close_master()
                return self.run(request, data, timeout, retry=False)
            raise MuError(proto.EXIT_UNREACHABLE, "timeout", "no answer from %s within %d s (%s)" % (
                self.name, timeout, st[1] if st else "no tunnel status"), "retry; mu compute status %s" % self.name)
        st = settle(status, rc, err, muxed)
        _unlink(status)
        if rc == 255 and muxed and st is None and retry:
            # a master that died with its link: end it and repeat the call once on a new connection
            self.close_master()
            return self.run(request, data, timeout, retry=False)
        return rc, out, err, st, muxed

    def fail(self, rc, err, st, muxed):
        """Raise the MuError for a failed call (rc != 0)."""
        if rc in proto.NODE_TO_CLIENT:
            raise node_error(rc, err, self.name)
        if rc == 255:
            classify_255(self.conf, err, st, muxed)
        raise MuError(proto.EXIT_ERROR, "node-error", "%s answered with exit %d: %s" % (
            self.name, rc, (err.strip() or "(no message)")[-600:]),
            "if μ on the node is older than the job layer: on the PC, wsl -d mu-compute -u mu -- mu update")

    def call(self, verb, data=None, timeout=CALL_TIMEOUT, **args):
        """A JSON verb -> the node's answer (dict). Failures raise MuError with the client's exit code."""
        request = " ".join([verb] + ["%s=%s" % (k, v) for k, v in args.items() if v is not None])
        rc, out, err, st, muxed = self.run(request, data, timeout, quick=verb in ("hello", "status", "list"))
        if rc != 0:
            if rc in proto.NODE_TO_CLIENT:
                raise node_error(rc, out.decode("utf-8", "replace") + "\n" + err, self.name)
            self.fail(rc, err, st, muxed)
        try:
            ans = json.loads(out.decode("utf-8").strip().splitlines()[0] if out.strip() else "")
        except (ValueError, UnicodeDecodeError, IndexError):
            raise MuError(proto.EXIT_ERROR, "bad-answer", "%s's answer to '%s' is not JSON: %r" % (
                self.name, verb, out[:200]), "the node's ~/.bashrc must print nothing; on the PC: "
                "wsl -d mu-compute -u root -- mu-wsl2-distro status")
        if not isinstance(ans, dict):
            raise MuError(proto.EXIT_ERROR, "bad-answer", "%s's answer is not a JSON object" % self.name, "")
        return ans

    def stream(self, request, on_data, data=None):
        """A streaming verb (log, fetch): ON_DATA(bytes) for each piece of stdout. -> (rc, err, st, muxed)."""
        status = self.new_status()
        muxed = self.muxed()
        p = subprocess.Popen(self.argv(request), stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env(status))
        errbuf = []
        t = threading.Thread(target=lambda: errbuf.append(p.stderr.read()), daemon=True)
        t.start()
        if data is not None:
            try:
                p.stdin.write(data)
                p.stdin.close()
            except BrokenPipeError:
                pass
        try:
            while True:
                b = p.stdout.read1(65536) if hasattr(p.stdout, "read1") else p.stdout.read(65536)
                if not b:
                    break
                on_data(b)
        except BaseException:
            p.kill()
            p.wait()
            _unlink(status)
            raise
        rc = p.wait()
        t.join(5)
        err = (errbuf[0] if errbuf else b"").decode("utf-8", "replace")
        st = settle(status, rc, err, muxed)
        _unlink(status)
        return rc, err, st, muxed


def _unlink(p):
    try:
        os.unlink(p)
    except OSError:
        pass


def settle(path, rc, err, muxed):
    """The proxy's verdict for a call that ended. chisel can exit on its own (a refused credential or fingerprint) and
    ssh then ends before the proxy has read chisel's last line: when ssh failed on a new connection and the file says
    no more than "starting" or "ok", wait up to 2 s for the proxy to write the reason."""
    st = read_status(path)
    if rc != 255 or muxed or "HOST IDENTIFICATION HAS CHANGED" in err or "Host key verification failed" in err \
            or "Permission denied (publickey" in err:
        return st
    end = time.time() + 2.0
    while (st is None or st[0] in ("starting", "ok")) and time.time() < end:
        time.sleep(0.1)
        st = read_status(path)
    return st


def read_status(path):
    """The proxy's one line: (class, reason, epoch, log line), or None when the proxy wrote nothing."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            line = f.readline().rstrip("\n")
    except OSError:
        return None
    parts = line.split("\t", 3)
    if len(parts) < 2 or not parts[0]:
        return None
    return tuple(parts + [""] * (4 - len(parts)))


RELAY_REASONS = {
    "relay-fingerprint": ("the relay's key fingerprint differs from the one pinned for this node (relay_fingerprint "
                          "in node.conf). Nothing was sent",
                          "if the relay key was rotated on purpose: mu compute remove {n}, then mu compute add {n} "
                          "with the new fingerprint (relay/gen-credentials.sh fingerprint)"),
    "relay-credential": ("the relay refused this workstation's credential (user {u})",
                         "check the password: relay/gen-credentials.sh client {u}; then mu compute remove {n} and "
                         "mu compute add {n} ... --relay-password-stdin"),
    "relay-rule": ("the relay does not let {u} reach port {p} (its access rules)",
                   "check relay_port in node.conf against the relay's RELAY_CLIENTS (relay/gen-credentials.sh show)"),
    "node-offline": ("the relay is up, but {n} is not connected to it (its tunnel is down, or the PC is off or asleep)",
                     "retry later; on the PC: wsl -d mu-compute -u root -- mu-wsl2-distro status. A job already "
                     "accepted is unaffected"),
    "relay-unreachable": ("the relay at {url} cannot be reached", "check this computer's network and retry; "
                          "mu compute status {n}"),
    "relay-lost": ("the connection to the relay was lost", "retry; a job already accepted is unaffected"),
}


def classify_255(conf, err, st, muxed):
    n, u, p, url = conf["name"], conf.get("relay_user"), conf.get("relay_port"), conf.get("relay_url")
    if st and st[0] in ("refused", "unreachable") and st[1] in RELAY_REASONS:
        msg, hint = RELAY_REASONS[st[1]]
        code = proto.EXIT_REFUSED if st[0] == "refused" else proto.EXIT_UNREACHABLE
        raise MuError(code, st[1], msg.format(n=n, u=u, p=p, url=url) + " [%s]" % st[1],
                      hint.format(n=n, u=u, p=p, url=url), reason=st[1], node=n)
    if st and st[0] == "starting":
        st = ("ok", "started", st[2], st[3])   # the proxy ran; chisel said nothing that decides
    if st and st[0] == "error":
        raise MuError(proto.EXIT_ERROR, st[1], st[3] or st[1], "mu compute status %s" % n, node=n)
    if "REMOTE HOST IDENTIFICATION HAS CHANGED" in err or "Host key verification failed" in err:
        raise MuError(proto.EXIT_REFUSED, "host-key", "%s's host key differs from the key pinned at pairing. Never "
                      "retried; nothing was sent [host-key]" % n,
                      "if the node was reinstalled on purpose, compare the key shown on the PC (mu-wsl2-distro status) "
                      "and pair again: mu compute remove %s; mu compute add %s --host-key ..." % (n, n),
                      reason="host-key", node=n)
    if "Permission denied (publickey" in err:
        raise MuError(proto.EXIT_REFUSED, "key-not-authorised", "this workstation's key is not authorised on %s "
                      "[key-not-authorised]" % n, "on the PC: wsl -d mu-compute -u root -- mu-wsl2-distro authorize "
                      "%s '<public key>' (mu compute list --json shows it)" % u, reason="key-not-authorised", node=n)
    if st is None and not muxed:
        raise MuError(proto.EXIT_ERROR, "proxy-failed", "ssh could not start the tunnel to %s: %s" % (
            n, err.strip()[-400:] or "(no message)"), "check the chisel client: mu doctor; the log: %s" % proto.tilde(
            os.path.join(mu_home(), "logs", "compute-%s.log" % n)), node=n)
    raise MuError(proto.EXIT_UNREACHABLE, "unreachable", "the connection to %s failed: %s" % (
        n, err.strip()[-300:] or "(no message)"), "retry; mu compute status %s" % n, node=n)


def node_error(rc, text, name):
    """A node verb's refusal (exit 64..78): its JSON line -> MuError with the client's code."""
    ans = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                ans = json.loads(line)
                break
            except ValueError:
                pass
    code = proto.NODE_TO_CLIENT.get(rc, proto.EXIT_ERROR)
    err = ans.get("error", "node-error")
    if err == "not-finished":
        code = proto.EXIT_USAGE
    if err == "protocol-mismatch":
        code = proto.EXIT_REFUSED
    msg = ans.get("message") or "%s refused the request: %s" % (name, err)
    extra = {k: v for k, v in ans.items() if k not in ("ok", "error", "hint", "message")}
    return MuError(code, err, "%s [%s]" % (msg, err), ans.get("hint", ""), node=name, **extra)


# ------------------------------------------------------------------------------------------------ the ProxyCommand
PROXY_RULES = [   # tried in order on each chisel log line; the first failure that matches decides (§5.2)
    (re.compile(r"Invalid fingerprint"), "refused", "relay-fingerprint"),
    (re.compile(r"Authentication failed|unable to authenticate"), "refused", "relay-credential"),
    (re.compile(r"access to '.*' denied"), "refused", "relay-rule"),
    (re.compile(r"connect failed.*connection refused"), "unreachable", "node-offline"),
    (re.compile(r"Connection error:"), "unreachable", "relay-unreachable"),
    (re.compile(r"Disconnected|Give up|connection attempts exhausted"), "unreachable", "relay-lost"),
]


def proxy_verdict(line, connected):
    """For one chisel log line: ('ok', 'connected') | (class, reason) | None. Exposed for the unit tests."""
    if "Connected" in line and not connected and "Disconnected" not in line:
        return "ok", "connected"
    for rx, cls, reason in PROXY_RULES:
        if rx.search(line):
            if reason == "relay-unreachable" and connected:
                return "unreachable", "relay-lost"
            if reason == "relay-lost" and not connected:
                return "unreachable", "relay-unreachable"
            return cls, reason
    return None


def find_chisel():
    if os.environ.get("MU_CHISEL"):
        return os.environ["MU_CHISEL"]
    tc = os.path.join(mu_home(), "toolchains")
    v = os.environ.get("MU_INFRA_VERSION", "")
    cands = [v] if v else []
    try:
        cands += sorted(os.listdir(tc), reverse=True)
    except OSError:
        pass
    for c in cands:
        p = os.path.join(tc, c, "libexec", "chisel")
        if c and os.access(p, os.X_OK):
            return p
    raise MuError(proto.EXIT_ERROR, "no-chisel", "no chisel client in this μ install (it comes with the toolchain "
                  "releases after v2026.11.6)", "mu update")


def cmd_proxy(name):
    """ssh's ProxyCommand: chisel client … stdio:127.0.0.1:<port>, supervised. ssh's stdin and stdout go straight to
    chisel; chisel's log goes to logs/compute-<node>.log; the verdict to $MU_PROXY_STATUS."""
    status_path = os.environ.get("MU_PROXY_STATUS", "")
    verdict = [None]

    def status(cls, reason, line=""):
        if (verdict[0] is not None and verdict[0][0] not in ("ok", "starting")) or not status_path:
            return   # the first failure decides; "starting" and "ok connected" are replaced by a later failure
        verdict[0] = (cls, reason)
        try:
            if not os.path.exists(status_path):   # the call that made it is over (a ControlPersist master)
                return
            tmp = "%s.%d" % (status_path, os.getpid())
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("%s\t%s\t%d\t%s\n" % (cls, reason, int(time.time()), line.replace("\t", " ")[:300]))
            os.replace(tmp, status_path)
        except OSError:
            pass

    logdir = ensure_dir(os.path.join(mu_home(), "logs"), 0o755)
    logpath = os.path.join(logdir, "compute-%s.log" % name)
    try:
        if os.path.getsize(logpath) > 1024 * 1024:
            with open(logpath, "rb") as f:
                f.seek(-256 * 1024, 2)
                tail = f.read()
            durable.write_durable(logpath, tail[tail.find(b"\n") + 1:], 0o644)
    except OSError:
        pass
    log = open(logpath, "ab", buffering=0)
    os.dup2(log.fileno(), 2)   # nothing of ours or chisel's reaches ssh's stderr (or a caller's pipe)
    status("starting", "started")   # the caller can tell "the proxy ran" from "ssh could not start it"
    try:
        conf = load_node(name)
        password = read_secret(conf, conf["_dir"])
        chisel = find_chisel()
    except MuError as e:
        status("error", e.error, e.message + (" (next: %s)" % e.hint if e.hint else ""))
        log.write(("mu compute proxy: %s\n" % e.message).encode())
        return 1
    argv = [chisel, "client", "--fingerprint", conf["relay_fingerprint"], "--keepalive", "25s", "--max-retry-count",
            "0", conf["relay_url"], "stdio:127.0.0.1:%s" % conf.get("relay_port", DEFAULT_PORT)]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.path.expanduser("~"),
           "AUTH": "%s:%s" % (conf["relay_user"], password), "CHISEL_WS_TIMEOUT": "10s"}
    if fault("auth-in-argv"):
        argv[2:2] = ["--auth", env.pop("AUTH")]
    if fault("bare-proxy"):
        os.execve(chisel, argv, env)
    child = subprocess.Popen(argv, stdin=0, stdout=1, stderr=subprocess.PIPE, env=env, close_fds=True)
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)   # chisel holds the stream now; ssh sees its end the moment chisel exits
    os.dup2(devnull, 1)
    done = [False]

    def kill_later():
        time.sleep(0.5)
        if child.poll() is None:
            child.kill()

    def stop(signum=None, frame=None):
        first = not done[0]
        done[0] = True
        try:
            child.terminate()   # chisel's first TERM waits for the stream to close (ssh's next keepalive)...
        except OSError:
            pass
        if first:               # ...so KILL half a second later: there is nothing left to flush
            threading.Thread(target=kill_later, daemon=True).start()

    for s in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT, signal.SIGPIPE):
        signal.signal(s, stop)
    connected = False
    for raw in child.stderr:
        log.write(raw)
        if done[0]:
            continue   # what chisel prints while it shuts down is not a reason
        v = proxy_verdict(raw.decode("utf-8", "replace").rstrip(), connected)
        if v is None:
            continue
        if v == ("ok", "connected"):
            connected = True
            status("ok", "connected", raw.decode("utf-8", "replace").rstrip())
            continue
        status(v[0], v[1], raw.decode("utf-8", "replace").rstrip())
        stop()
    rc = child.wait()
    return 0 if rc == 0 else 1


# ------------------------------------------------------------------------------------------------ mu compute ...
PASSWORD = re.compile(r"^[A-Za-z0-9_-]{24,}$")
FINGERPRINT = re.compile(r"^[A-Za-z0-9+/]{43}=$")


def cmd_compute_add(a):
    name = a.node
    if not proto.NODE_NAME.match(name) or name in proto.RESERVED_NODES:
        raise MuError(proto.EXIT_USAGE, "bad-node-name", "node name '%s': use [a-z0-9][a-z0-9-]{0,31}, not local or auto"
                      % name, "e.g. office-pc")
    d = node_dir(name)
    if os.path.exists(d):
        raise MuError(proto.EXIT_USAGE, "exists", "%s is already paired" % name, "mu compute remove %s first" % name)
    ktype, kb64, hostfp = host_key_parse(a.host_key)
    if not re.match(r"^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?/?$", a.relay or ""):
        raise MuError(proto.EXIT_USAGE, "bad-relay-url", "--relay must be a URL like https://relay.martand.systems", "")
    if not FINGERPRINT.match(a.relay_fingerprint or ""):
        raise MuError(proto.EXIT_USAGE, "bad-fingerprint", "--relay-fingerprint must be the relay's fingerprint "
                      "(44 characters of base64)", "relay/gen-credentials.sh fingerprint")
    if not proto.NODE_NAME.match(a.relay_user or ""):
        raise MuError(proto.EXIT_USAGE, "bad-relay-user", "--relay-user must be the relay client name, e.g. avi-mac", "")
    if not (7001 <= a.relay_port <= 7999):
        raise MuError(proto.EXIT_USAGE, "bad-relay-port", "--relay-port must be 7001 to 7999", "")
    store = a.secret_store or ("keychain" if sys.platform == "darwin" else "file")
    if a.relay_password_stdin:
        password = sys.stdin.readline().strip()
    else:
        password = os.environ.get("MU_RELAY_PASSWORD", "")
    if not PASSWORD.match(password):
        raise MuError(proto.EXIT_USAGE, "bad-password", "the relay password must be at least 24 characters of "
                      "A-Z a-z 0-9 _ - (read from stdin with --relay-password-stdin, or MU_RELAY_PASSWORD)",
                      "relay/gen-credentials.sh client %s prints it" % a.relay_user)
    workstation = a.name or a.relay_user
    if not proto.KEY_NAME.match(workstation):
        raise MuError(proto.EXIT_USAGE, "bad-name", "--name must be [a-z0-9-]{1,32}", "")
    safe_path_for_config(mu_home())
    keygen = shutil.which("ssh-keygen")
    if not keygen:
        raise MuError(proto.EXIT_ERROR, "no-ssh-keygen", "ssh-keygen was not found", "install OpenSSH")
    ensure_dir(compute_root())
    tmpd = tempfile.mkdtemp(prefix=".add-%s." % name, dir=compute_root())
    try:
        os.chmod(tmpd, 0o700)
        p = subprocess.run([keygen, "-q", "-t", "ed25519", "-N", "", "-C", workstation, "-f",
                            os.path.join(tmpd, "id_ed25519")], capture_output=True, stdin=subprocess.DEVNULL)
        if p.returncode != 0:
            raise MuError(proto.EXIT_ERROR, "keygen", "ssh-keygen failed: %s" % p.stderr.decode(errors="replace"), "")
        durable.write_durable(os.path.join(tmpd, "known_hosts"), "mu-%s %s %s\n" % (name, ktype, kb64), 0o600)
        durable.write_durable(os.path.join(tmpd, "ssh_config"), ssh_config_text(name), 0o600)
        conf = {"name": name, "relay_url": a.relay.rstrip("/"), "relay_fingerprint": a.relay_fingerprint,
                "relay_user": a.relay_user, "relay_port": str(a.relay_port), "secret_store": store,
                "host_key_fingerprint": hostfp, "workstation": workstation, "added": proto.iso()}
        store_secret(conf, tmpd, password)
        write_conf(os.path.join(tmpd, "node.conf"), conf, "μ compute node (mu compute add); read as text")
        os.rename(tmpd, d)
    except BaseException:
        shutil.rmtree(tmpd, ignore_errors=True)
        raise
    with open(os.path.join(d, "id_ed25519.pub"), encoding="utf-8") as f:
        pub = f.read().strip()
    pubfp = key_fingerprint(base64.b64decode(pub.split()[1]))
    auth_cmd = "wsl -d mu-compute -u root -- mu-wsl2-distro authorize %s '%s'" % (workstation, pub)
    out = {"ok": True, "node": name, "workstation": workstation, "public_key": pub, "key_fingerprint": pubfp,
           "host_key_fingerprint": hostfp, "secret_store": store, "authorize_command": auth_cmd,
           "folder": d}
    if a.json:
        proto.emit_json(out)
    else:
        print("paired %s (nothing was sent yet)" % name)
        print("  host key        %s   (compare with the PC's screen)" % hostfp)
        print("  this computer   %s   key %s" % (workstation, pubfp))
        print("  relay password  stored in the %s" % ("login Keychain" if store == "keychain" else "file relay.auth (0600)"))
        print("\nOn the PC, once:\n  %s\nThen: mu compute status %s" % (auth_cmd, name))
    return 0


def cmd_compute_list(a):
    rows = []
    for n in paired_nodes():
        conf = read_conf(os.path.join(node_dir(n), "node.conf")) or {}
        last = None
        try:
            with open(os.path.join(node_dir(n), "last-status.json"), encoding="utf-8") as f:
                last = json.load(f)
        except (OSError, ValueError):
            pass
        pub = ""
        try:
            with open(os.path.join(node_dir(n), "id_ed25519.pub"), encoding="utf-8") as f:
                pub = f.read().strip()
        except OSError:
            pass
        rows.append({"node": n, "relay": conf.get("relay_url"), "relay_user": conf.get("relay_user"),
                     "relay_port": conf.get("relay_port"), "host_key_fingerprint": conf.get("host_key_fingerprint"),
                     "workstation": conf.get("workstation"), "public_key": pub, "added": conf.get("added"),
                     "last_status": last})
    if a.json:
        proto.emit_json({"ok": True, "nodes": rows})
    elif not rows:
        print("no compute node is paired: mu compute add <node> ...")
    else:
        for r in rows:
            ls = r["last_status"] or {}
            seen = ("last answer %s, μ %s" % (ls.get("at", "?"), (ls.get("hello") or {}).get("mu", {}).get("default"))
                    if ls.get("ok") else ("last try %s: %s" % (ls.get("at"), ls.get("error")) if ls else "never asked"))
            print("%-14s %s (port %s, as %s) · %s" % (r["node"], r["relay"], r["relay_port"], r["relay_user"], seen))
    return 0


def hello(conn):
    ans = conn.call("hello", protocol=proto.PROTOCOL)
    p, pmin = ans.get("protocol"), ans.get("protocol_min", ans.get("protocol"))
    if not isinstance(p, int) or proto.PROTOCOL < (pmin or 0) or p < proto.PROTOCOL_MIN:
        raise MuError(proto.EXIT_REFUSED, "protocol-mismatch", "protocol mismatch: this workstation speaks %d (μ %s), "
                      "%s speaks %s, accepts from %s (μ %s)" % (proto.PROTOCOL, os.environ.get("MU_INFRA_VERSION", "?"),
                                                                conn.name, p, pmin, (ans.get("mu") or {}).get("default")),
                      "run mu update on the older side")
    return ans


def cmd_compute_status(a):
    names = [a.node] if a.node else paired_nodes()
    if not names:
        raise MuError(proto.EXIT_NO_SUCH, "no-node", "no compute node is paired", "mu compute add ...")
    results, worst = [], 0
    for n in names:
        conf = load_node(n)
        conn = Conn(conf)
        reused = conn.muxed()
        t0 = time.time()
        rec = {"node": n, "at": proto.iso(), "path": "relay", "control_path": conn.cp}
        try:
            ans = hello(conn)
            rec.update(ok=True, rtt_s=round(time.time() - t0, 3), reused=reused, hello=ans)
        except MuError as e:
            rec.update(ok=False, exit=e.code, error=e.error, message=e.message, next=e.hint, rtt_s=round(time.time() - t0, 3))
            worst = worst or e.code
        try:
            durable.write_durable(os.path.join(conf["_dir"], "last-status.json"), json.dumps(rec) + "\n", 0o600)
        except OSError:
            pass
        results.append(rec)
    if a.json:
        proto.emit_json(results[0] if a.node else {"ok": worst == 0, "nodes": results})
    else:
        for r in results:
            if r["ok"]:
                h = r["hello"]
                print("%s: up · %.2f s%s · μ %s (installed: %s) · runner %s · queue %s running, %s queued" % (
                    r["node"], r["rtt_s"], " (connection reused)" if r["reused"] else "", h["mu"]["default"],
                    " ".join(h["mu"]["installed"]), h.get("runner"), h["queue"]["running"], h["queue"]["queued"]))
                d, lim = h.get("disk", {}), h.get("limits", {})
                print("  %s · %s cores, %.0f GB RAM · disk %s GB free (%s; jobs need %s) · memory limit %s · last stop %s" % (
                    h["hw"].get("cpu_model"), h["hw"].get("cores_physical"), h["hw"].get("mem_total_kb", 0) / 1048576.0,
                    d.get("free_gb"), d.get("where"), d.get("min_free_gb"), "enforced" if lim.get("memory") else
                    "NOT enforced (jobs are refused)", h.get("last_stop")))
            else:
                sys.stderr.write("%s: %s\n    next: %s\n" % (r["node"], r["message"], r["next"]))
    return worst


def cmd_compute_remove(a):
    conf = load_node(a.node)
    Conn(conf).close_master()
    delete_secret(conf)
    shutil.rmtree(conf["_dir"])
    ws = conf.get("workstation") or conf.get("relay_user")
    cmd = "wsl -d mu-compute -u root -- mu-wsl2-distro revoke %s" % ws
    if a.json:
        proto.emit_json({"ok": True, "node": a.node, "revoke_command": cmd})
    else:
        print("removed %s from this computer. To revoke this computer's key on the node, on the PC:\n  %s" % (a.node, cmd))
    return 0


# ------------------------------------------------------------------------------------------------ the project, git
_git = [None]


def find_git():
    if _git[0]:
        return _git[0]
    for d in os.environ.get("PATH", "").split(os.pathsep):
        g = os.path.join(d, "git")
        if os.access(g, os.X_OK):
            try:
                if subprocess.run([g, "--version"], capture_output=True, timeout=20, stdin=subprocess.DEVNULL).returncode == 0:
                    _git[0] = g
                    return g
            except (OSError, subprocess.TimeoutExpired):
                pass
    raise MuError(proto.EXIT_ERROR, "no-git", "no working git on PATH", "install git (macOS: xcode-select --install)")


def git(cwd, *args, env=None, input=None, check=True):
    e = dict(os.environ)
    if env:
        e.update(env)
    p = subprocess.run([find_git()] + list(args), cwd=cwd, env=e, input=input, capture_output=True,
                       stdin=None if input is not None else subprocess.DEVNULL)
    if check and p.returncode != 0:
        raise MuError(proto.EXIT_ERROR, "git", "git %s failed: %s" % (" ".join(args[:3]), p.stderr.decode(errors="replace").strip()), "")
    return p.stdout.decode("utf-8", "replace").strip() if check else p


class Project:
    def __init__(self, start=None):
        d = os.path.realpath(start or os.getcwd())
        root = d
        while not os.path.isfile(os.path.join(root, "mu.toml")):
            parent = os.path.dirname(root)
            if parent == root:
                raise MuError(proto.EXIT_USAGE, "not-a-project", "not a μ project: no mu.toml here or in any parent folder",
                              "cd into a project (a git repository with mu.toml at its top)")
            root = parent
        self.root, self.cwd = root, d
        p = git(root, "rev-parse", "--show-toplevel", check=False)
        if p.returncode != 0:
            raise MuError(proto.EXIT_USAGE, "not-git", "%s is not in a git repository" % root,
                          "mu.toml must be at the top of the git repository: git init, add, commit")
        top = os.path.realpath(p.stdout.decode().strip())
        if top != root:
            raise MuError(proto.EXIT_USAGE, "mu-toml-not-at-top", "mu.toml must be at the top of the git repository "
                          "(it is in %s, the repository starts at %s)" % (root, top), "move mu.toml to %s" % top)
        if git(root, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode != 0:
            raise MuError(proto.EXIT_USAGE, "no-commit", "the repository has no commit yet", "commit once first")
        if git(root, "rev-parse", "--is-shallow-repository") == "true":
            raise MuError(proto.EXIT_USAGE, "shallow", "a shallow clone has another root commit than the project",
                          "unshallow first: git fetch --unshallow")
        with open(os.path.join(root, "mu.toml"), encoding="utf-8") as f:
            self.pin, self.outputs = proto.mu_toml(f.read())
        roots = sorted(git(root, "rev-list", "--max-parents=0", "HEAD").split())
        slug = re.sub(r"[^a-z0-9-]", "-", os.path.basename(root).lower()).lstrip("-")[:40].rstrip("-") or "project"
        self.name = os.path.basename(root)
        self.id = "%s-%s" % (slug, roots[0][:12])
        rel = os.path.relpath(d, root)
        self.rel_cwd = "" if rel == "." else rel.replace(os.sep, "/")
        self.jobs_dir = os.path.join(root, ".mu", "jobs")

    def job_record_path(self, job_id):
        return os.path.join(self.jobs_dir, job_id + ".json")

    def write_job_record(self, rec):
        ensure_dir(self.jobs_dir, 0o755)
        gi = os.path.join(self.jobs_dir, ".gitignore")
        if not os.path.exists(gi):
            durable.write_durable(gi, "# μ's own records of jobs sent from this clone (never uploaded)\n*\n", 0o644)
        durable.write_record(self.job_record_path(rec["job_id"]), rec, 0o644)

    def read_job_record(self, job_id):
        return durable.read_record(self.job_record_path(job_id))


def snapshot(root, job_id):
    """The working tree as a commit on top of HEAD, made with a temporary index (§6.1): the user's index, branch,
    HEAD, stash and files are not touched. -> dict(snapshot, tree, head, dirty, gitlinks, lfs)."""
    gitdir = git(root, "rev-parse", "--absolute-git-dir")
    idx = os.path.join(gitdir, "mu-index.%d" % os.getpid())
    user_index = fault("user-index")
    env = {} if user_index else {"GIT_INDEX_FILE": idx}
    try:
        if not user_index:
            try:
                shutil.copyfile(os.path.join(gitdir, "index"), idx)
            except FileNotFoundError:
                git(root, "read-tree", "HEAD", env=env)
        spec = ["--", "."] if fault("no-mu-exclude") else ["--", ".", ":(exclude).mu"]
        git(root, "add", "-A", *spec, env=env)
        tree = git(root, "write-tree", env=env)
    finally:
        for f in (idx, idx + ".lock"):
            _unlink(f)
    when = "%d +0000" % proto.job_id_epoch(job_id)
    ident = {"GIT_AUTHOR_NAME": "mu", "GIT_AUTHOR_EMAIL": "mu@localhost", "GIT_COMMITTER_NAME": "mu",
             "GIT_COMMITTER_EMAIL": "mu@localhost", "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    snap = git(root, "commit-tree", tree, "-p", "HEAD", "-m", "mu job %s" % job_id, env=ident)
    git(root, "update-ref", "refs/mu/snap/%s" % job_id, snap)
    head = git(root, "rev-parse", "HEAD")
    head_tree = git(root, "rev-parse", "HEAD^{tree}")
    return dict(snapshot=snap, tree=tree, head=head, dirty=tree != head_tree, **scan_tree(root, tree))


def scan_tree(root, tree):
    """Gitlinks (submodules, embedded repositories) and LFS pointer files in a tree: their content does not travel."""
    gitlinks, small = [], []
    for line in git(root, "ls-tree", "-r", "-l", "-z", tree).split("\0"):
        if not line:
            continue
        meta, path = line.split("\t", 1)
        mode, typ, oid, size = meta.split()
        if mode == "160000":
            gitlinks.append(path)
        elif typ == "blob" and size.isdigit() and int(size) < 1024:
            small.append((oid, path))
    lfs = []
    if small:
        p = git(root, "cat-file", "--batch", input=("\n".join(o for o, _ in small) + "\n").encode(), check=False)
        data, i = p.stdout, 0
        for oid, path in small:
            nl = data.index(b"\n", i)
            size = int(data[i:nl].split()[2])
            body = data[nl + 1:nl + 1 + size]
            i = nl + 1 + size + 1
            if body.startswith(b"version https://git-lfs.github.com/spec/v1"):
                lfs.append(path)
    return {"gitlinks": gitlinks, "lfs": lfs}


def push(conn, root, project_id, snap, job_id):
    status = conn.new_status()
    safe_path_for_config(conn.cfg)
    env = dict(conn.env(status))
    env.update(GIT_SSH_COMMAND="'%s' -F '%s' -o '%s'" % (ssh_bin(), conn.cfg, conn.cp_opt), GIT_SSH_VARIANT="ssh",
               GIT_TERMINAL_PROMPT="0")
    env.pop("GIT_SSH", None)
    repo = project_id
    args = [find_git()] + ([] if fault("push-hooks") else ["-c", "core.hooksPath=/dev/null"]) + ["push"] + \
        ([] if fault("push-hooks") else ["--no-verify"]) + ["--no-recurse-submodules", "--porcelain",
                                                            "mu-%s:%s.git" % (conn.name, repo), "%s:refs/mu/snap/%s" % (snap, job_id)]
    muxed = conn.muxed()
    p = subprocess.run(args, cwd=root, env=env, capture_output=True, stdin=subprocess.DEVNULL)
    err = p.stderr.decode("utf-8", "replace")
    st = settle(status, 255 if p.returncode and "Could not read from remote" in err else 0, err, muxed)
    _unlink(status)
    if p.returncode == 0:
        return
    if "pack exceeds maximum allowed size" in err:
        raise MuError(proto.EXIT_NODE_CANNOT, "snapshot-too-large", "the snapshot is larger than the node accepts "
                      "(receive.maxInputSize, 2 GiB)", "ignore large generated files (.gitignore) or track them with "
                      "git-lfs, then submit again")
    if st and st[0] in ("refused", "unreachable", "error") or "Host key verification failed" in err or \
            "Permission denied (publickey" in err:
        classify_255(conn.conf, err, st, muxed)
    raise MuError(proto.EXIT_ERROR, "push-failed", "the snapshot could not be pushed to %s: %s" % (
        conn.name, err.strip()[-600:]), "mu compute status %s; retry with mu submit --resume %s" % (conn.name, job_id))


# ------------------------------------------------------------------------------------------------ submit, status, log
def parse_command(args):
    if "--" in args:
        i = args.index("--")
        return args[:i], args[i + 1:]
    return args, []


def cmd_submit(a, command):
    prj = Project()
    if a.resume:
        if not proto.JOB_ID.match(a.resume):
            raise MuError(proto.EXIT_USAGE, "bad-job-id", "'%s' is not a job id" % a.resume, "mu status")
        rec = prj.read_job_record(a.resume)
        if not rec:
            raise MuError(proto.EXIT_NO_SUCH, "no-such-job", "this clone has no record of job %s (.mu/jobs)" % a.resume,
                          "mu status")
        spec, node_name = rec["spec"], rec["node"]
        conf = load_node(node_name)
    else:
        if not command:
            raise MuError(proto.EXIT_USAGE, "no-command", "nothing to run", "mu submit [options] -- <command ...>")
        conf = pick_node(a.on)
        node_name = conf["name"]
        if not prj.pin or prj.pin == "unreleased":
            raise MuError(proto.EXIT_USAGE, "no-pin", "the project has no released μ version in mu.toml ([mu] version)",
                          "mu pin")
        if not proto.VERSION.match(prj.pin):
            raise MuError(proto.EXIT_USAGE, "bad-pin", "[mu] version '%s' in mu.toml is not a version" % prj.pin, "mu pin")
        outputs = a.output or prj.outputs
        for g in outputs:
            if not proto.glob_ok(g):
                raise MuError(proto.EXIT_USAGE, "bad-output", "output '%s' must be a glob relative to the project root, "
                              "without '..'" % g, "e.g. --output 'results/*.json'")
        limits = {"mem_bytes": None, "time_s": None, "cores": None}
        if a.mem:
            limits["mem_bytes"] = proto.parse_size(a.mem)
            if not limits["mem_bytes"]:
                raise MuError(proto.EXIT_USAGE, "bad-mem", "--mem takes a size like 16G, 512M", "")
        if a.time:
            limits["time_s"] = proto.parse_duration(a.time)
            if not limits["time_s"]:
                raise MuError(proto.EXIT_USAGE, "bad-time", "--time takes a duration like 90s, 30m, 6h, 2d", "")
        if a.cores is not None:
            if a.cores < 1:
                raise MuError(proto.EXIT_USAGE, "bad-cores", "--cores must be at least 1", "")
            limits["cores"] = a.cores
        if any("\0" in x for x in command):
            raise MuError(proto.EXIT_USAGE, "bad-command", "the command holds a NUL byte", "")
        job_id = proto.new_job_id()
        snap = snapshot(prj.root, job_id)
        for g in snap["gitlinks"]:
            if prj.rel_cwd == g or prj.rel_cwd.startswith(g + "/"):
                raise MuError(proto.EXIT_USAGE, "cwd-in-gitlink", "the working folder %s is inside the submodule %s, "
                              "whose files do not reach the node in stage 1" % (prj.rel_cwd, g), "run from outside it")
        pid = prj.id
        if fault("full-push"):   # a repository of its own on the node for every job: a full copy each time
            pid = "%s-%s" % (pid[:-13], hashlib.sha256(job_id.encode()).hexdigest()[:12])
        spec = {"schema": 1, "job_id": job_id, "name": a.name or " ".join(command)[:80], "project": prj.name,
                "project_id": pid, "snapshot": snap["snapshot"], "tree": snap["tree"], "head": snap["head"],
                "dirty": snap["dirty"], "mu_version": prj.pin, "argv": command, "cwd": prj.rel_cwd,
                "outputs": outputs, "limits": limits, "rerun": not a.no_rerun,
                "submitted": {"at": proto.iso(), "host": socket.gethostname().split(".")[0], "user": getpass.getuser(),
                              "mu": os.environ.get("MU_INFRA_VERSION", "?"), "path": "relay"}}
        rec = {"schema": 1, "job_id": job_id, "node": node_name, "state": "sending", "spec": spec,
               "warnings": {"gitlinks": snap["gitlinks"], "lfs": snap["lfs"]}}
        prj.write_job_record(rec)
    job_id = spec["job_id"]
    warnings = rec.get("warnings", {})
    if not a.json:
        for g in warnings.get("gitlinks", []):
            sys.stderr.write("warning: %s is a submodule or embedded repository: only its commit id travels\n" % g)
        for g in warnings.get("lfs", []):
            sys.stderr.write("warning: %s is a git-lfs pointer: its content does not reach the node\n" % g)
        if not spec["outputs"]:
            sys.stderr.write("note: no outputs declared: only the log and the result record come back\n")
    conn = Conn(conf)
    ans = hello(conn)
    if spec["mu_version"] not in (ans.get("mu") or {}).get("installed", []):
        raise MuError(proto.EXIT_VERSION, "toolchain-missing", "%s has no μ %s, which this project pins (installed "
                      "there: %s)" % (node_name, spec["mu_version"], ", ".join(ans["mu"]["installed"]) or "none"),
                      "on the PC: wsl -d mu-compute -u mu -- mu install %s   (or move the project: mu pin)" % spec["mu_version"],
                      job_id=job_id)
    if ans.get("runner") != "active":
        raise MuError(proto.EXIT_NODE_CANNOT, "not-enabled", "the job runner on %s is not running" % node_name,
                      "on the PC: wsl -d mu-compute -u mu -- mu node enable", job_id=job_id)
    if not (ans.get("limits") or {}).get("memory"):
        raise MuError(proto.EXIT_NODE_CANNOT, "limits-unavailable", "%s cannot enforce a job's memory limit (its "
                      "self-test failed), so it takes no jobs" % node_name,
                      "on the PC: wsl -d mu-compute -u mu -- mu node selftest (docs/design/job-layer.md §19 step 1)",
                      job_id=job_id)
    disk = ans.get("disk") or {}
    if isinstance(disk.get("free_gb"), (int, float)) and disk["free_gb"] < disk.get("min_free_gb", 0):
        raise MuError(proto.EXIT_NODE_CANNOT, "disk-low", "%s has %s GB free (%s); jobs need %s GB" % (
            node_name, disk["free_gb"], disk.get("where"), disk.get("min_free_gb")),
            "free space on the node (old job folders are under /srv/mu/jobs), then submit again", job_id=job_id)
    push(conn, prj.root, spec["project_id"], spec["snapshot"], job_id)
    if fault("lose-answer"):
        conn.run("submit id=%s" % job_id, json.dumps(spec).encode())
        raise MuError(proto.EXIT_UNREACHABLE, "relay-lost", "the link was lost before the node's answer arrived "
                      "(test fault lose-answer)", "mu submit --resume %s" % job_id, job_id=job_id)
    res = conn.call("submit", data=json.dumps(spec).encode(), id=job_id)
    rec = prj.read_job_record(job_id) or {"schema": 1, "job_id": job_id, "node": node_name, "spec": spec}
    rec.update(state=res.get("state"), accepted=proto.iso(), duplicate=bool(res.get("duplicate")))
    prj.write_job_record(rec)
    out = {"ok": True, "job_id": job_id, "node": node_name, "state": res.get("state"), "position": res.get("position"),
           "duplicate": bool(res.get("duplicate")), "snapshot": spec["snapshot"], "dirty": spec["dirty"],
           "warnings": warnings}
    if a.wait:
        a.job_id, a.timeout = job_id, None
        if not a.json:
            print("%s accepted by %s (%s)" % (job_id, node_name, res.get("state")))
        return cmd_status(a)
    if a.json:
        proto.emit_json(out)
    else:
        pos = " (position %s)" % res["position"] if res.get("position") else ""
        print("%s %s on %s%s%s" % (job_id, res.get("state"), node_name, pos, " — already known: not sent twice" if
                                   res.get("duplicate") else ""))
        print("  mu status %s --wait · mu log %s -f · mu fetch %s" % (job_id, job_id, job_id))
    return 0


def job_node(job_id, on=None):
    """The node of a job: --on, else this clone's record of it, else the only paired node."""
    if not proto.JOB_ID.match(job_id or ""):
        raise MuError(proto.EXIT_USAGE, "bad-job-id", "'%s' is not a job id (YYYYMMDD-HHMMSS-xxxxxx)" % job_id, "mu status")
    if on:
        return load_node(on), None
    prj = None
    try:
        prj = Project()
        rec = prj.read_job_record(job_id)
        if rec:
            return load_node(rec["node"]), prj
    except MuError:
        pass
    return pick_node(None), prj


def poll_s():
    try:
        return max(0.2, float(os.environ.get("MU_POLL_S", "5")))
    except ValueError:
        return 5.0


def describe(s):
    st = s.get("state")
    bits = ["%s %s" % (s.get("job_id"), st)]
    if s.get("attempt", 1) and s.get("attempt", 1) > 1:
        bits.append("attempt %s" % s["attempt"])
    if st == "queued" and s.get("position"):
        bits.append("position %s" % s["position"])
    if s.get("exit_code") is not None:
        bits.append("exit %s" % s["exit_code"])
    if s.get("note"):
        bits.append(s["note"])
    return " · ".join(bits)


def cmd_status(a):
    if not a.job_id:
        prj = Project()
        local = {}
        try:
            for f in sorted(os.listdir(prj.jobs_dir)):
                r = durable.read_record(os.path.join(prj.jobs_dir, f)) if f.endswith(".json") else None
                if r:
                    local[r["job_id"]] = r
        except OSError:
            pass
        nodes = sorted({r["node"] for r in local.values()}) or paired_nodes()[:1]
        jobs, errors = [], []
        for n in nodes:
            try:
                ans = Conn(load_node(n)).call("list", project=prj.id, limit=20)
                for j in ans.get("jobs", []):
                    j["node"] = n
                    jobs.append(j)
            except MuError as e:
                errors.append({"node": n, "error": e.error, "message": e.message, "exit": e.code})
        seen = {j["job_id"] for j in jobs}
        for jid, r in local.items():
            if jid not in seen and r.get("state") == "sending":
                jobs.append({"job_id": jid, "node": r["node"], "state": "sending", "note": "not accepted yet: mu submit --resume %s" % jid})
        jobs.sort(key=lambda j: j["job_id"])
        if a.json:
            proto.emit_json({"ok": not errors, "project_id": prj.id, "jobs": jobs, "errors": errors})
        else:
            for j in jobs:
                print("%-24s %-10s %s" % (j["job_id"], j.get("node"), describe(j)[len(j["job_id"]) + 1:]))
            for e in errors:
                sys.stderr.write("%s: %s\n" % (e["node"], e["message"]))
            if not jobs and not errors:
                print("no jobs from this project yet: mu submit -- <command>")
        return errors[0]["exit"] if errors else 0
    conf, _ = job_node(a.job_id, getattr(a, "on", None))
    conn = Conn(conf)
    deadline = (time.time() + a.timeout) if a.timeout else None
    last = None
    while True:
        try:
            s = conn.call("status", id=a.job_id)
        except MuError as e:
            if not a.wait or e.code != proto.EXIT_UNREACHABLE or (deadline and time.time() > deadline):
                raise
            if not a.json:
                sys.stderr.write("lost the link (%s): retrying\n" % e.error)
            time.sleep(poll_s())
            continue
        if not a.wait or s.get("state") in proto.FINISHED:
            break
        if not a.json and describe(s) != last:
            print(describe(s))
            last = describe(s)
        if deadline and time.time() > deadline:
            raise MuError(proto.EXIT_UNREACHABLE, "timeout", "job %s has no result after %s s (%s)" % (
                a.job_id, a.timeout, s.get("state")), "mu status %s --wait" % a.job_id)
        time.sleep(poll_s())
    s["node"] = conf["name"]
    if a.json:
        proto.emit_json(s)
    else:
        print(describe(s))
    if a.wait and s.get("state") != "succeeded":
        return proto.EXIT_JOB_FAILED
    return 0


def cmd_log(a):
    conf, _ = job_node(a.job_id, a.on)
    conn = Conn(conf)
    out = sys.stdout.buffer
    if a.follow:
        if a.json:
            raise MuError(proto.EXIT_USAGE, "usage", "--json does not go with -f", "poll with mu log <id> --json --offset N")
        offset = a.offset or 0
        if a.tail is not None:
            offset = tail_offset(conn, a.job_id, a.tail)
        got = [0]

        def on_data(b):
            out.write(b)
            out.flush()
            got[0] += len(b)
        while True:
            start = offset + got[0]
            if fault("log-from-zero") and got[0]:
                start = 0   # the fault: the pieces overlap
            rc, err, st, muxed = conn.stream("log id=%s offset=%d follow=1" % (a.job_id, start), on_data)
            if rc == 0:
                return 0
            try:
                conn.fail(rc, err, st, muxed)
            except MuError as e:
                if e.code != proto.EXIT_UNREACHABLE:
                    raise
                sys.stderr.write("mu: lost the link (%s); resuming from byte %d\n" % (e.error, offset + got[0]))
                time.sleep(min(2.0, poll_s()))
    s = conn.call("status", id=a.job_id)
    size = s.get("log_size") or 0
    offset = a.offset or 0
    if a.tail is not None:
        offset = tail_offset(conn, a.job_id, a.tail, s)
    chunks = []
    rc, err, st, muxed = conn.stream("log id=%s offset=%d" % (a.job_id, offset), chunks.append)
    if rc != 0:
        conn.fail(rc, err, st, muxed)
    data = b"".join(chunks)
    if a.tail is not None:
        lines = data.splitlines(True)
        keep = b"".join(lines[-a.tail:]) if a.tail else b""
        offset += len(data) - len(keep)
        data = keep
    nxt = offset + len(data)
    if a.json:
        proto.emit_json({"ok": True, "job_id": a.job_id, "offset": offset, "next_offset": nxt,
                         "text": data.decode("utf-8", "replace"), "state": s.get("state"),
                         "eof": s.get("state") in proto.FINISHED and nxt >= size})
    else:
        out.write(data)
        out.flush()
    return 0


def tail_offset(conn, job_id, n, s=None):
    s = s or conn.call("status", id=job_id)
    size = s.get("log_size") or 0
    return max(0, size - max(65536, n * 400))


# ------------------------------------------------------------------------------------------------ fetch
class FetchError(Exception):
    pass


def safe_target(into, rel):
    """INTO/REL, refusing any existing component that is a symlink (a link in the project could lead outside)."""
    parts = rel.split("/")
    cur = into
    for part in parts[:-1]:
        cur = os.path.join(cur, part)
        try:
            st = os.lstat(cur)
        except FileNotFoundError:
            os.mkdir(cur, 0o755)
            continue
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise FetchError("%s: %s is a link or not a folder" % (rel, cur))
    target = os.path.join(cur, parts[-1])
    try:
        st = os.lstat(target)
        if not stat.S_ISREG(st.st_mode):
            raise FetchError("%s exists and is not a regular file" % rel)
    except FileNotFoundError:
        pass
    return target


def unpack_outputs(stream, result, into, declared):
    """Read the tar stream of `fetch` after result.json: each member must be the next output of RESULT, a regular file
    of the recorded size and SHA-256, under a declared glob. -> list of written paths. Raises FetchError (exit 8)."""
    expected = list(result.get("outputs") or [])
    written = []
    tf = tarfile.open(fileobj=stream, mode="r|")
    first = True
    for m in tf:
        if first:
            first = False
            if m.name != "result.json":
                raise FetchError("the stream does not start with result.json")
            continue
        check = not fault("no-member-check")
        if check:
            if not expected:
                raise FetchError("unexpected member %r: every declared output was already received" % m.name)
            want = expected.pop(0)
            rel = proto.safe_relpath(want.get("path", ""))
            if m.name != want.get("path") or rel is None or rel == "":
                raise FetchError("member %r is not the next declared output %r" % (m.name, want.get("path")))
            if not m.isreg():
                raise FetchError("member %r is not a regular file" % m.name)
            if not any(proto.glob_match(g, rel) for g in declared):
                raise FetchError("member %r matches none of the declared outputs" % m.name)
        else:
            want, rel = {"size": m.size, "sha256": None}, m.name
            if not m.isreg():
                continue
        target = safe_target(into, rel)
        fd, tmp = tempfile.mkstemp(prefix=".mu-fetch.", dir=os.path.dirname(target))
        h, n = hashlib.sha256(), 0
        try:
            src = tf.extractfile(m)
            with os.fdopen(fd, "wb") as f:
                while True:
                    b = src.read(1 << 20)
                    if not b:
                        break
                    h.update(b)
                    n += len(b)
                    f.write(b)
            if not fault("no-fetch-hash") and check and (n != want.get("size") or h.hexdigest() != want.get("sha256")):
                raise FetchError("%s: %d bytes, sha256 %s; the result records %s bytes, %s" % (
                    rel, n, h.hexdigest()[:16], want.get("size"), str(want.get("sha256"))[:16]))
            os.chmod(tmp, 0o644)
            os.replace(tmp, target)
        except BaseException:
            _unlink(tmp)
            raise
        written.append(rel)
    if expected and not fault("no-member-check"):
        raise FetchError("the stream ended before %s" % expected[0].get("path"))
    return written


class _Pipe:
    """A file-like reader over a byte iterator fed by another thread (tarfile reads the stream as it arrives)."""

    def __init__(self):
        import queue
        self.q, self.buf, self.eof = queue.Queue(maxsize=64), b"", False

    def feed(self, b):
        self.q.put(b)

    def close(self):
        self.q.put(None)

    def read(self, n=-1):
        while not self.eof and (n < 0 or len(self.buf) < n):
            b = self.q.get()
            if b is None:
                self.eof = True
                break
            self.buf += b
        if n < 0:
            out, self.buf = self.buf, b""
        else:
            out, self.buf = self.buf[:n], self.buf[n:]
        return out


def fetch_result(conn, job_id):
    chunks = []
    rc, err, st, muxed = conn.stream("fetch id=%s what=result" % job_id, chunks.append)
    if rc != 0:
        if rc in proto.NODE_TO_CLIENT:
            raise node_error(rc, b"".join(chunks).decode("utf-8", "replace") + "\n" + err, conn.name)
        conn.fail(rc, err, st, muxed)
    import io
    try:
        tf = tarfile.open(fileobj=io.BytesIO(b"".join(chunks)), mode="r:")
        m = tf.next()
        if m is None or m.name != "result.json" or not m.isreg():
            raise FetchError("no result.json in the answer")
        res = durable.parse_record(tf.extractfile(m).read())
    except (tarfile.TarError, FetchError) as e:
        raise MuError(proto.EXIT_INTEGRITY, "bad-result", "the node's result record could not be read: %s" % e,
                      "mu fetch %s again" % job_id)
    if not res or res.get("job_id") != job_id:
        raise MuError(proto.EXIT_INTEGRITY, "bad-result", "the node's result record for %s is damaged" % job_id,
                      "mu fetch %s again; if it repeats: mu status %s" % (job_id, job_id))
    return res


def cmd_fetch(a):
    conf, prj = job_node(a.job_id, a.on)
    conn = Conn(conf)
    maxb = proto.parse_size(a.max_size or "50M")
    if not maxb:
        raise MuError(proto.EXIT_USAGE, "bad-max-size", "--max-size takes a size like 50M", "")
    s = conn.call("status", id=a.job_id)
    if s.get("state") not in proto.FINISHED:
        raise MuError(proto.EXIT_USAGE, "not-finished", "job %s is still %s: it has no result yet" % (
            a.job_id, s.get("state")), "mu status %s --wait" % a.job_id)
    res = fetch_result(conn, a.job_id)
    total = sum(o.get("size", 0) for o in res.get("outputs") or [])
    if total > maxb:
        raise MuError(proto.EXIT_USAGE, "too-large", "the outputs of %s add up to %s, over --max-size %s" % (
            a.job_id, proto.human_size(total), proto.human_size(maxb)),
            "mu fetch %s --max-size %dM" % (a.job_id, total // (1 << 20) + 1),
            outputs=[{"path": o["path"], "size": o["size"]} for o in res.get("outputs") or []])
    if prj is None:
        try:
            prj = Project()
        except MuError:
            prj = None
    into = os.path.realpath(a.into or (prj.root if prj else os.getcwd()))
    if not os.path.isdir(into):
        raise MuError(proto.EXIT_USAGE, "bad-into", "--into %s is not a folder" % into, "")
    declared = (res.get("job") or {}).get("outputs") or []
    pipe = _Pipe()
    box = {}

    def consume():
        try:
            box["written"] = unpack_outputs(pipe, res, into, declared)
        except (FetchError, tarfile.TarError) as e:
            box["error"] = e
        except BaseException as e:  # noqa
            box["error"] = e
        finally:
            # drain whatever is left, so the ssh reader never blocks on a full queue
            while not pipe.eof:
                pipe.read(1 << 20)
    t = threading.Thread(target=consume, daemon=True)
    t.start()
    rc, err, st, muxed = conn.stream("fetch id=%s" % a.job_id, pipe.feed)
    pipe.close()
    t.join()
    if "error" in box:
        e = box["error"]
        if isinstance(e, (FetchError, tarfile.TarError)):
            raise MuError(proto.EXIT_INTEGRITY, "integrity", "fetch of %s refused: %s. Nothing from that file on was "
                          "written" % (a.job_id, e), "mu fetch %s again; if it repeats, the node's copy changed after "
                          "the job" % a.job_id)
        raise e
    if rc != 0:
        conn.fail(rc, err, st, muxed)
    written = box.get("written", [])
    record = run_record(res, conf["name"], prj)
    run_path = None
    if prj:
        ensure_dir(os.path.join(prj.root, "runs"), 0o755)
        run_path = os.path.join(prj.root, "runs", a.job_id + ".json")
        durable.write_record(run_path, record, 0o644)
    out = {"ok": True, "job_id": a.job_id, "state": res.get("state"), "written": written, "into": into,
           "run_record": run_path, "outputs_missing": res.get("outputs_missing", [])}
    if a.json:
        proto.emit_json(out)
    else:
        print("%s %s: %d output(s) written under %s%s" % (a.job_id, res.get("state"), len(written), proto.tilde(into),
                                                           "; run record %s" % os.path.relpath(run_path, prj.root) if run_path else ""))
        for m in res.get("outputs_missing", []):
            print("  matched nothing: %s" % m)
    return 0


def run_record(res, node, prj):
    job = res.get("job") or {}
    return {"schema": 1, "job_id": res["job_id"], "name": job.get("name"), "project": job.get("project"),
            "project_id": job.get("project_id"), "head": job.get("head"), "tree": job.get("tree"),
            "dirty": job.get("dirty"), "snapshot": job.get("snapshot"), "argv": job.get("argv"), "cwd": job.get("cwd"),
            "mu_version": job.get("mu_version"), "key": job.get("key"), "state": res.get("state"),
            "exit_code": res.get("exit_code"), "signal": res.get("signal"), "attempt": res.get("attempt"),
            "queued_at": res.get("queued_at"), "started_at": res.get("started_at"), "ended_at": res.get("ended_at"),
            "wall_s": res.get("wall_s"), "limits": res.get("limits"), "outputs": res.get("outputs"),
            "outputs_missing": res.get("outputs_missing"), "log": res.get("log"), "toolchain": res.get("toolchain"),
            "node": res.get("node"), "unit": res.get("unit"), "path": "relay",
            "archive": "%s:/srv/mu/jobs/%s/work" % (node, res["job_id"]), "fetched_at": proto.iso(),
            "result_sha256": res.get("sha256")}


def cmd_cancel(a):
    conf, _ = job_node(a.job_id, a.on)
    ans = Conn(conf).call("cancel", id=a.job_id)
    if a.json:
        proto.emit_json(ans)
    else:
        print("%s %s" % (a.job_id, ans.get("state")))
    return 0


# ------------------------------------------------------------------------------------------------ arguments
class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise MuError(proto.EXIT_USAGE, "usage", message, self.format_usage().strip())


def parser(prog):
    p = Parser(prog=prog, add_help=True)
    p.add_argument("--json", action="store_true", help="one JSON object on stdout")
    return p


def main(cmd, args):
    if cmd == "compute":
        sub = args[0] if args and not args[0].startswith("-") else "list"
        rest = args[1:] if args and not args[0].startswith("-") else args
        if sub == "proxy":
            if len(rest) != 1:
                raise MuError(2, "usage", "usage: mu compute proxy <node> (ssh's ProxyCommand)", "")
            return cmd_proxy(rest[0])
        p = parser("mu compute " + sub)
        if sub == "add":
            p.add_argument("node")
            p.add_argument("--host-key", required=True)
            p.add_argument("--relay", required=True)
            p.add_argument("--relay-fingerprint", required=True)
            p.add_argument("--relay-user", required=True)
            p.add_argument("--relay-port", type=int, default=DEFAULT_PORT)
            p.add_argument("--secret-store", choices=("keychain", "file"))
            p.add_argument("--relay-password-stdin", action="store_true")
            p.add_argument("--name", help="this workstation's name on the node (default: the relay user)")
            return cmd_compute_add(p.parse_args(rest))
        if sub in ("list", "ls"):
            return cmd_compute_list(p.parse_args(rest))
        if sub == "status":
            p.add_argument("node", nargs="?")
            return cmd_compute_status(p.parse_args(rest))
        if sub in ("remove", "rm"):
            p.add_argument("node")
            return cmd_compute_remove(p.parse_args(rest))
        raise MuError(2, "usage", "unknown: mu compute %s" % sub, "mu compute add|list|status|remove")
    if cmd == "submit":
        opts, command = parse_command(args)
        p = parser("mu submit")
        p.add_argument("--on")
        p.add_argument("--name")
        p.add_argument("--mem")
        p.add_argument("--time")
        p.add_argument("--cores", type=int)
        p.add_argument("--output", action="append", default=[])
        p.add_argument("--no-rerun", action="store_true")
        p.add_argument("--wait", action="store_true")
        p.add_argument("--resume", metavar="JOB_ID")
        a = p.parse_args(opts)
        a.timeout = None
        return cmd_submit(a, command)
    if cmd == "status":
        p = parser("mu status")
        p.add_argument("job_id", nargs="?")
        p.add_argument("--on")
        p.add_argument("--wait", action="store_true")
        p.add_argument("--timeout", type=float)
        return cmd_status(p.parse_args(args))
    if cmd == "log":
        p = parser("mu log")
        p.add_argument("job_id")
        p.add_argument("--on")
        p.add_argument("--tail", type=int)
        p.add_argument("--offset", type=int)
        p.add_argument("-f", "--follow", action="store_true")
        return cmd_log(p.parse_args(args))
    if cmd == "fetch":
        p = parser("mu fetch")
        p.add_argument("job_id")
        p.add_argument("--on")
        p.add_argument("--into")
        p.add_argument("--max-size")
        return cmd_fetch(p.parse_args(args))
    if cmd == "cancel":
        p = parser("mu cancel")
        p.add_argument("job_id")
        p.add_argument("--on")
        return cmd_cancel(p.parse_args(args))
    raise MuError(2, "usage", "unknown command: %s" % cmd, "mu help")
