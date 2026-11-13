"""Unit-level checks of the job layer that need no systemd, no ssh and no network (tools/test-jobs/run.sh runs them
with each Python under test). Usage: python unit.py SECTION [ARG...]. Prints one line per check, "ok    name" or
"FAIL  name" (with details on indented lines), and exits with the number of failed checks.

Sections: durable, atomic, ids, proxy, classify, sshconfig, controlpath, unpack, snapshot-alone, snapshot-content,
snapshot-report, pins REPO."""
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
    for rc, body, code, err in ((65, '{"ok":false,"error":"protocol-mismatch","hint":"x"}', 5, "protocol-mismatch"),
                                (66, '{"ok":false,"error":"no-such-job"}', 9, "no-such-job"),
                                (69, '{"ok":false,"error":"disk-low"}', 6, "disk-low"),
                                (78, '{"ok":false,"error":"toolchain-missing"}', 3, "toolchain-missing"),
                                (64, '{"ok":false,"error":"bad-character"}', 1, "bad-character"),
                                (70, '{"ok":false,"error":"internal"}', 1, "internal"),
                                (64, '{"ok":false,"error":"not-finished"}', 2, "not-finished")):
        e = jobs.node_error(rc, body, "office-pc")
        check("node exit %d %s -> client exit %d" % (rc, err, code), (e.code, e.error) == (code, err), (e.code, e.error))


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
    if "chisel" in distro:
        ds = re.findall(r"[0-9a-f]{64}", distro[distro.index("chisel"):])
        dvv = re.findall(r"chisel[_ A-Za-z]*VERSION=\"?([0-9.]+)", distro)
        check("mu-wsl2-distro.sh names the same chisel version and linux-64 sum", lin in ds and (not dvv or dvv[0] == version),
              (dvv, ds[:3]))
    else:
        print("skip  mu-wsl2-distro.sh has no chisel pin yet (the host script's tunnel step adds it)")


SECTIONS = {"durable": t_durable_main, "atomic": t_atomic, "ids": t_ids, "proxy": t_proxy, "classify": t_classify,
            "sshconfig": t_sshconfig, "controlpath": t_controlpath, "unpack": t_unpack,
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
