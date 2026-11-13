"""μ job layer, compute-node side (docs/design/job-layer.md §6.4, §7-§12). Runs as the user `mu`.

  mu node serve --key=<name> <verb> [key=value ...]   what the gate (mu-node-gate, sshd's ForceCommand) runs
  mu node run                                        the runner: mu-node.service, one job at a time, FIFO
  mu node run-job <id>                               the shim: main process of the unit mu-job-<id>.service
  mu node enable [--name N] [--min-free-gb G]        write and start ~/.config/systemd/user/mu-node.service
  mu node disable                                    stop and remove it (jobs are kept)
  mu node selftest                                   prove that a user unit's MemoryMax is enforced (§10)
  mu node info                                       the `hello` answer, here

Every verb answers one JSON object (log and fetch: a byte stream) and exits 0, 64, 65, 66, 69, 70 or 78 (§7.2),
never 1, 2 or ssh's 255. Standard library only.

Layout: /srv/mu (MU_NODE_ROOT in tests): node.json, projects/<id>.git, pins/<id>/mu.toml, jobs/<job-id>/, tmp/,
lock, audit.log. Test hooks: MU_NODE_ROOT, MU_SYSTEMCTL, MU_SYSTEMD_RUN, MU_NODE_TICK (runner loop, seconds),
MU_WSL_HOST_DRIVE (the host-drive record), MU_NODE_FAULT or the root-owned /etc/mu/test-fault (durable.faults):
  no-update-hook       a new project repository gets no update hook (snapshots are not write-once)
  git-archive          work/ is made with `git archive | tar -x`
  before-result        the shim exits 0 just before it writes result.json
  trust-unit-success   the runner takes "unit ended, Result=success, no result" as succeeded
  trust-existence      a result.json that exists counts as a result, valid or not
  no-memory-max        job units get no MemoryMax / MemorySwapMax
  killmode-process     job units get KillMode=process
  start-on-submit      submit starts the job at once (no queue)
  node-id              submit gives the job an id of its own (a repeated submit becomes a second job)
  job-in-session       submit runs the job as a child of the ssh session
  no-adopt             a runner that starts finds a running job's unit and starts the job again
  no-pin-register      submit does not register the project's pin in projects.txt
  skip-limits-check    submit takes jobs while the memory self-test fails
  selftest-fail        the self-test reports failure
  fetch-extra-member   fetch adds a member outside the declared outputs
"""
import fcntl
import hashlib
import json
import os
import pwd
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import traceback

from mu import durable, proto
from mu.durable import fault

ROOT = os.environ.get("MU_NODE_ROOT") or "/srv/mu"
DEFAULT_MIN_FREE_GB = 20
DEFAULT_TIME_S = 12 * 3600
GIT_ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
UPDATE_HOOK = """#!/bin/sh
# μ: snapshots are write-once; only refs/mu/snap/<job-id> can be pushed (docs/design/job-layer.md §6.4)
case "$1" in refs/mu/snap/[0-9]*) ;; *) echo "mu: only refs/mu/snap/<job-id> can be pushed here" >&2; exit 1 ;; esac
case "$2" in *[!0]*) [ "$2" = "$3" ] || { echo "mu: snapshot $1 exists and cannot be changed or deleted" >&2; exit 1; } ;; esac
exit 0
"""
ARG_RULES = {"id": proto.JOB_ID, "project": proto.PROJECT_ID, "repo": proto.REPO_NAME, "offset": re.compile(r"^[0-9]{1,15}$"),
             "max": re.compile(r"^[0-9]{1,12}$"), "limit": re.compile(r"^[0-9]{1,5}$"), "follow": re.compile(r"^[01]$"),
             "protocol": re.compile(r"^[0-9]{1,4}$"), "what": re.compile(r"^result$")}


class NodeError(Exception):
    def __init__(self, rc, error, hint="", message="", **extra):
        Exception.__init__(self, error)
        self.rc, self.error, self.hint, self.message, self.extra = rc, error, hint, message, extra


def answer(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def refuse(e):
    out = {"ok": False, "error": e.error, "hint": e.hint, "protocol": proto.PROTOCOL}
    if e.message:
        out["message"] = e.message
    out.update(e.extra)
    answer(out)
    return e.rc


def mu_home():
    return os.environ.get("MU_HOME") or os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "mu")


def p(*parts):
    return os.path.join(ROOT, *parts)


def job_dir(job_id):
    return p("jobs", job_id)


def journal(msg):
    """One line for the journal (syslog) and for /srv/mu/audit.log. Never stdin, never secrets."""
    try:
        import syslog
        syslog.openlog("mu-node", 0, syslog.LOG_USER)
        syslog.syslog(syslog.LOG_INFO, msg)
    except Exception:
        pass
    try:
        with open(p("audit.log"), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (proto.iso(), msg))
    except OSError:
        pass


class Lock:
    """flock on /srv/mu/lock around every change to the queue."""

    def __enter__(self):
        self.fd = os.open(p("lock"), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


# ------------------------------------------------------------------------------------------------ systemd
def user_env():
    e = dict(os.environ)
    if not e.get("XDG_RUNTIME_DIR"):
        e["XDG_RUNTIME_DIR"] = "/run/user/%d" % os.getuid()
    return e


def systemctl(*args, timeout=60):
    cmd = [os.environ.get("MU_SYSTEMCTL") or "systemctl", "--user"] + list(args)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=user_env(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


def unit_props(unit):
    r = systemctl("show", unit, "-p", "LoadState", "-p", "ActiveState", "-p", "SubState", "-p", "Result",
                  "-p", "ExecMainStatus", "-p", "ExecMainCode")
    props = {}
    for line in r.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            props[k] = v
    if r.returncode != 0 and not props:
        props = {"LoadState": "error", "ActiveState": "unknown", "Result": "", "error": r.stderr.strip()}
    return props


def unit_active(props):
    return props.get("ActiveState") in ("active", "activating", "deactivating", "reloading", "unknown")


def runner_state():
    r = systemctl("is-active", "mu-node.service", timeout=15)
    return "active" if r.stdout.strip() == "active" else "inactive"


# ------------------------------------------------------------------------------------------------ machine facts
def read_text(path, default=""):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return default


def boot_id():
    return read_text("/proc/sys/kernel/random/boot_id").strip() or "unknown"


def hw_info():
    cpu = read_text("/proc/cpuinfo")
    model = next((ln.split(":", 1)[1].strip() for ln in cpu.splitlines() if ln.startswith("model name")), "")
    cores, phys = set(), None
    for ln in cpu.splitlines():
        if ln.startswith("physical id"):
            phys = ln.split(":", 1)[1].strip()
        elif ln.startswith("core id"):
            cores.add((phys, ln.split(":", 1)[1].strip()))
    logical = os.cpu_count() or 1
    mem = 0
    m = re.search(r"^MemTotal:\s+([0-9]+) kB", read_text("/proc/meminfo"), re.M)
    if m:
        mem = int(m.group(1))
    else:
        try:
            mem = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // 1024
        except (ValueError, OSError):
            pass
    ver = read_text("/proc/version")
    wsl = None
    if "microsoft" in ver.lower() or os.environ.get("WSL_DISTRO_NAME"):
        wsl = os.uname().release
    return {"cpu_model": model or os.uname().machine, "cores_physical": len(cores) or logical, "cores_logical": logical,
            "mem_total_kb": mem, "kernel": os.uname().release, "wsl": wsl}


def free_gb(path):
    try:
        s = os.statvfs(path)
        return s.f_bavail * s.f_frsize // (1 << 30)
    except OSError:
        return None


def disk_info(node):
    """The smaller of /srv/mu's file system and the Windows drive the host script recorded (§13.4)."""
    out = {"free_gb": free_gb(ROOT), "min_free_gb": node.get("min_free_gb", DEFAULT_MIN_FREE_GB),
           "where": ROOT, "host": None}
    rec = os.environ.get("MU_WSL_HOST_DRIVE") or "/var/lib/mu-wsl2/host-drive"
    line = read_text(rec).strip().split(None, 1)
    if len(line) == 2 and re.match(r"^[A-Za-z]$", line[0]):
        out["host"] = line[0].upper()
        g = free_gb(line[1])
        if g is not None and (out["free_gb"] is None or g < out["free_gb"]):
            out["free_gb"], out["where"] = g, "%s: (%s)" % (line[0].upper(), line[1])
    elif hw_info()["wsl"]:
        out["host"] = "unknown"
    return out


def last_stop():
    v = "unknown"
    for ln in read_text("/var/lib/mu-wsl2/stops.log").splitlines():
        m = re.search(r"previous-stop=([a-z]+)", ln)
        if m:
            v = m.group(1)
    return v


def node_info():
    return durable.read_record(p("node.json")) or {}


def update_node_info(**kw):
    with Lock():
        n = node_info()
        n.update(kw)
        n.setdefault("protocol", proto.PROTOCOL)
        n.setdefault("created", proto.iso())
        durable.write_record(p("node.json"), n)
    return n


def limits_ok(node):
    st = node.get("selftest") or {}
    return bool(st.get("ok")) and st.get("boot_id") == boot_id()


def installed_versions():
    tc = os.path.join(mu_home(), "toolchains")
    try:
        return sorted(v for v in os.listdir(tc) if os.path.isfile(os.path.join(tc, v, "MANIFEST.json")))
    except OSError:
        return []


def default_version():
    return read_text(os.path.join(mu_home(), "default")).strip()


def autoupdate_info():
    conf = read_text(os.path.join(mu_home(), "autoupdate.conf"))
    st = read_text(os.path.join(mu_home(), "state", "autoupdate.state"))
    en = re.findall(r"^\s*enabled\s*=\s*(\S+)", conf, re.M)
    lr = re.findall(r"^last_result=(.*)$", st, re.M)
    return {"enabled": bool(en) and en[-1] == "on", "last_result": lr[-1] if lr else None}


# ------------------------------------------------------------------------------------------------ jobs on disk
def job_result(d):
    r = durable.read_record(os.path.join(d, "result.json"))
    if r is None and fault("trust-existence") and os.path.exists(os.path.join(d, "result.json")):
        return {"state": "succeeded", "job_id": os.path.basename(d), "note": "trusted by existence (test fault)"}
    return r


def scan_jobs():
    """Every job folder with a valid job.json: list of dicts (id, dir, job, state, result)."""
    out = []
    try:
        names = os.listdir(p("jobs"))
    except OSError:
        return out
    for n in names:
        if not proto.JOB_ID.match(n):
            continue
        d = job_dir(n)
        job = durable.read_record(os.path.join(d, "job.json"))
        if not job:
            continue
        out.append({"id": n, "dir": d, "job": job, "state": durable.read_record(os.path.join(d, "state.json")),
                    "result": job_result(d)})
    return out


def queued_jobs(jobs):
    q = [j for j in jobs if not j["result"] and (not j["state"] or j["state"].get("state") == "queued")]
    return sorted(q, key=lambda j: j["job"].get("accepted_ns", 0))


def event(d, text):
    try:
        with open(os.path.join(d, "events.log"), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (proto.iso(), text))
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass


def status_of(j, jobs=None):
    job, st, res = j["job"], j["state"] or {}, j["result"]
    state = res.get("state") if res else st.get("state", "queued")
    pos = None
    if not res and state == "queued":
        q = queued_jobs(jobs if jobs is not None else scan_jobs())
        pos = next((i + 1 for i, x in enumerate(q) if x["id"] == j["id"]), None)
    try:
        log_size = os.path.getsize(os.path.join(j["dir"], "log"))
    except OSError:
        log_size = 0
    return {"ok": True, "job_id": j["id"], "name": job.get("name"), "project_id": job.get("project_id"),
            "state": state, "attempt": (res or st).get("attempt", st.get("attempt", 1)), "position": pos,
            "queued_at": job.get("accepted_at"), "started_at": (res or {}).get("started_at") or st.get("started_at"),
            "ended_at": (res or {}).get("ended_at"), "exit_code": (res or {}).get("exit_code"), "log_size": log_size,
            "note": (res or {}).get("note") or st.get("note"), "unit": st.get("unit"),
            "cancel_requested": os.path.exists(os.path.join(j["dir"], "cancel"))}


def load_job(job_id):
    d = job_dir(job_id)
    job = durable.read_record(os.path.join(d, "job.json"))
    if not job:
        raise NodeError(proto.NODE_NO_SUCH, "no-such-job", "mu status (this project's jobs)",
                        message="the node has no job %s" % job_id)
    return {"id": job_id, "dir": d, "job": job, "state": durable.read_record(os.path.join(d, "state.json")),
            "result": job_result(d)}


def manifest(version):
    try:
        with open(os.path.join(mu_home(), "toolchains", version, "MANIFEST.json"), encoding="utf-8") as f:
            m = json.load(f)
        return {"version": m.get("version", version), "recipe_commit": m.get("recipe_commit"), "layers": m.get("layers", [])}
    except (OSError, ValueError):
        return {"version": version, "recipe_commit": None, "layers": []}


def job_summary(job):
    keys = ("name", "project", "project_id", "snapshot", "tree", "head", "dirty", "mu_version", "argv", "cwd", "outputs",
            "key", "rerun", "submitted")
    return {k: job.get(k) for k in keys}


def log_info(d):
    h, n = hashlib.sha256(), 0
    try:
        with open(os.path.join(d, "log"), "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
                n += len(b)
    except OSError:
        pass
    return {"size": n, "sha256": h.hexdigest()}


def write_result(j, state, written_by, unit_result=None, note=None, **extra):
    st = j["state"] or {}
    now = time.time()
    started = st.get("started_at")
    res = {"schema": 1, "job_id": j["id"], "state": state, "exit_code": extra.pop("exit_code", None),
           "signal": extra.pop("signal", None), "attempt": st.get("attempt", 1), "queued_at": j["job"].get("accepted_at"),
           "started_at": started, "ended_at": proto.iso(now),
           "wall_s": round(now - st.get("started_epoch", now), 1) if started else None,
           "outputs": extra.pop("outputs", []), "outputs_missing": extra.pop("outputs_missing", []),
           "log": log_info(j["dir"]), "toolchain": manifest(j["job"].get("mu_version", "")), "node": node_summary(),
           "limits": st.get("limits"), "unit": {"name": st.get("unit"), "result": unit_result},
           "written_by": written_by, "note": note, "job": job_summary(j["job"])}
    res.update(extra)
    durable.write_record(os.path.join(j["dir"], "result.json"), res)
    event(j["dir"], "result %s (%s%s)" % (state, written_by, ", " + note if note else ""))
    j["result"] = durable.read_record(os.path.join(j["dir"], "result.json"))
    return res


def node_summary():
    n = node_info()
    s = {"name": n.get("name")}
    s.update(hw_info())
    return s


# ------------------------------------------------------------------------------------------------ serve: the verbs
def verb_hello(key, args):
    cp = int(args.get("protocol", "0") or 0)
    node = node_info()
    if cp < proto.PROTOCOL_MIN or cp > proto.PROTOCOL:
        raise NodeError(proto.NODE_PROTOCOL, "protocol-mismatch", "run mu update on the older side",
                        message="protocol mismatch: the client speaks %d, this node %d (accepts %d to %d; μ %s)" % (
                            cp, proto.PROTOCOL, proto.PROTOCOL_MIN, proto.PROTOCOL, default_version()),
                        node_protocol=proto.PROTOCOL, node_mu=default_version())
    return hello_answer(key, node)


def hello_answer(key, node):
    jobs = scan_jobs()
    running = [j for j in jobs if not j["result"] and (j["state"] or {}).get("state") == "running"]
    st = node.get("selftest") or {}
    return {"ok": True, "protocol": proto.PROTOCOL, "protocol_min": proto.PROTOCOL_MIN, "node": node.get("name"),
            "key": key, "mu": {"default": default_version(), "installed": installed_versions(),
                               "infra": os.environ.get("MU_INFRA_VERSION")},
            "hw": hw_info(), "disk": disk_info(node),
            "limits": {"memory": limits_ok(node) or fault("skip-limits-check"), "pids": True, "selftest_at": st.get("at"),
                       "selftest_detail": st.get("detail")},
            "queue": {"running": len(running), "queued": len(queued_jobs(jobs))}, "last_stop": last_stop(),
            "runner": runner_state(), "autoupdate": autoupdate_info(), "time": proto.iso()}


_GIT = []


def git_bin():
    """The first git on PATH that answers --version (as the dispatcher's find_git)."""
    if not _GIT:
        for d in os.environ.get("PATH", "").split(os.pathsep):
            g = os.path.join(d, "git")
            try:
                if os.access(g, os.X_OK) and subprocess.run([g, "--version"], capture_output=True, timeout=20,
                                                            stdin=subprocess.DEVNULL).returncode == 0:
                    _GIT.append(g)
                    break
            except (OSError, subprocess.TimeoutExpired):
                pass
        if not _GIT:
            raise NodeError(proto.NODE_INTERNAL, "no-git", "as root: apt install git", message="no working git on the node")
    return _GIT[0]


def git_node(*args, cwd=None, check=True, input=None):
    env = dict(os.environ)
    env.update(GIT_ENV)
    r = subprocess.run([git_bin()] + list(args), cwd=cwd, env=env, capture_output=True, input=input,
                       stdin=None if input is not None else subprocess.DEVNULL)
    if check and r.returncode != 0:
        raise NodeError(proto.NODE_INTERNAL, "git", message="git %s: %s" % (args[0], r.stderr.decode(errors="replace").strip()))
    return r


def repo_path(name):
    return p("projects", name + ".git")


def verb_receive_pack(key, args):
    name = args.get("repo", "")
    if not proto.PROJECT_ID.match(name):
        raise NodeError(proto.NODE_USAGE, "bad-repository", "expected <slug>-<12 hex>.git (mu submit makes it)")
    repo = repo_path(name)
    with Lock():
        if not os.path.isdir(repo):
            os.makedirs(p("projects"), mode=0o700, exist_ok=True)
            tmp = repo + ".tmp.%d" % os.getpid()
            shutil.rmtree(tmp, ignore_errors=True)
            git_node("init", "-q", "--bare", "--template=", tmp)
            for k, v in (("core.fsync", "all"), ("receive.maxInputSize", "2147483648"), ("receive.fsckObjects", "true"),
                         ("gc.auto", "0"), ("core.logAllRefUpdates", "false")):
                git_node("--git-dir=" + tmp, "config", k, v)
            os.makedirs(os.path.join(tmp, "hooks"), exist_ok=True)
            if not fault("no-update-hook"):
                with open(os.path.join(tmp, "hooks", "update"), "w") as f:
                    f.write(UPDATE_HOOK)
                os.chmod(os.path.join(tmp, "hooks", "update"), 0o755)
            durable.sync_dir(tmp)
            os.rename(tmp, repo)
            durable.sync_dir(p("projects"))
            journal("repository %s created" % name)
    env = dict(os.environ)
    env.update(GIT_ENV)
    sys.stdout.flush()
    os.execve(git_bin(), ["git", "receive-pack", repo], env)


def check_spec(spec, job_id):
    def bad(field, why):
        raise NodeError(proto.NODE_USAGE, "bad-spec", "submit a valid job spec (mu submit makes it)",
                        message="job spec: %s %s" % (field, why), field=field)
    if not isinstance(spec, dict) or spec.get("schema") != 1:
        bad("schema", "must be 1")
    if spec.get("job_id") != job_id:
        bad("job_id", "must equal the id argument")
    if not isinstance(spec.get("project_id"), str) or not proto.PROJECT_ID.match(spec["project_id"]):
        bad("project_id", "is not a project id")
    for k in ("snapshot", "tree", "head"):
        if not isinstance(spec.get(k), str) or not re.match(r"^[0-9a-f]{40}([0-9a-f]{24})?$", spec[k]):
            bad(k, "is not a commit id")
    if not isinstance(spec.get("mu_version"), str) or not proto.VERSION.match(spec["mu_version"]):
        bad("mu_version", "is not a version")
    argv = spec.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and "\0" not in x for x in argv):
        bad("argv", "must be a non-empty list of strings without NUL")
    if proto.safe_relpath(spec.get("cwd", "")) is None:
        bad("cwd", "must be relative, without '..'")
    outs = spec.get("outputs")
    if not isinstance(outs, list) or not all(proto.glob_ok(g) for g in outs):
        bad("outputs", "must be relative globs without '..'")
    lim = spec.get("limits")
    if not isinstance(lim, dict):
        bad("limits", "must be an object")
    for k in ("mem_bytes", "time_s", "cores"):
        v = lim.get(k)
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v <= 0):
            bad("limits." + k, "must be null or a positive integer")
    if not isinstance(spec.get("rerun", True), bool):
        bad("rerun", "must be true or false")
    if not isinstance(spec.get("name", ""), str) or len(spec.get("name", "")) > 200:
        bad("name", "must be a string of at most 200 characters")


def check_snapshot(spec):
    repo = repo_path(spec["project_id"])
    if not os.path.isdir(repo):
        raise NodeError(proto.NODE_NO_SUCH, "no-such-snapshot", "push the snapshot first (mu submit does)",
                        message="no repository %s on the node" % spec["project_id"])
    ref = git_node("--git-dir=" + repo, "rev-parse", "--verify", "-q", "refs/mu/snap/%s^{commit}" % spec["job_id"], check=False)
    if ref.returncode != 0 or ref.stdout.decode().strip() != spec["snapshot"]:
        raise NodeError(proto.NODE_NO_SUCH, "no-such-snapshot", "mu submit --resume %s pushes it again" % spec["job_id"],
                        message="refs/mu/snap/%s is not the snapshot %s" % (spec["job_id"], spec["snapshot"][:12]))
    tree = git_node("--git-dir=" + repo, "rev-parse", spec["snapshot"] + "^{tree}").stdout.decode().strip()
    if tree != spec["tree"]:
        raise NodeError(proto.NODE_USAGE, "bad-spec", "", message="job spec: tree is not the snapshot's tree", field="tree")
    t = git_node("--git-dir=" + repo, "show", spec["snapshot"] + ":mu.toml", check=False)
    try:
        v, _ = proto.mu_toml(t.stdout.decode("utf-8", "replace")) if t.returncode == 0 else (None, None)
    except proto.MuError:
        v = None
    if v != spec["mu_version"]:
        raise NodeError(proto.NODE_USAGE, "bad-spec", "", message="job spec: mu_version %s is not [mu] version of the "
                        "snapshot's mu.toml (%s)" % (spec["mu_version"], v), field="mu_version")


def register_pin(project_id, version):
    """pins/<project-id>/mu.toml, listed in $MU_HOME/projects.txt: auto-update's clean-up keeps that version (§12)."""
    if fault("no-pin-register"):
        return
    d = p("pins", project_id)
    os.makedirs(d, mode=0o700, exist_ok=True)
    text = "# μ: the pin of project %s, registered by the job layer\n[mu]\nversion = \"%s\"\n" % (project_id, version)
    if read_text(os.path.join(d, "mu.toml")) != text:
        durable.write_durable(os.path.join(d, "mu.toml"), text, 0o644)
    reg = os.path.join(mu_home(), "projects.txt")
    lines = read_text(reg).splitlines()
    if d not in lines:
        os.makedirs(mu_home(), exist_ok=True)
        durable.write_durable(reg, "".join(x + "\n" for x in lines + [d] if x), 0o644)


def verb_submit(key, args):
    job_id = args.get("id")
    if not job_id:
        raise NodeError(proto.NODE_USAGE, "bad-argument", "submit id=<job-id>")
    data = sys.stdin.buffer.read(65537)
    if len(data) > 65536:
        raise NodeError(proto.NODE_USAGE, "bad-spec", "", message="the job spec is over 64 kB")
    try:
        spec = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise NodeError(proto.NODE_USAGE, "bad-spec", "", message="the job spec is not JSON")
    check_spec(spec, job_id)
    if fault("node-id"):
        job_id = proto.new_job_id()
    node = node_info()
    with Lock():
        d = job_dir(job_id)
        if os.path.isdir(d):
            j = {"id": job_id, "dir": d, "job": durable.read_record(os.path.join(d, "job.json"))}
            if j["job"]:
                j["state"], j["result"] = durable.read_record(os.path.join(d, "state.json")), job_result(d)
                s = status_of(j)
                journal("submit id=%s key=%s duplicate" % (job_id, key))
                return {"ok": True, "job_id": job_id, "state": s["state"], "position": s["position"], "duplicate": True}
            shutil.rmtree(d)   # never acknowledged: start again
        if runner_state() != "active" and not fault("start-on-submit") and not fault("job-in-session"):
            raise NodeError(proto.NODE_UNAVAILABLE, "not-enabled", "on the PC: wsl -d mu-compute -u mu -- mu node enable",
                            message="the job runner (mu-node.service) is not running")
        disk = disk_info(node)
        if disk["free_gb"] is not None and disk["free_gb"] < disk["min_free_gb"]:
            raise NodeError(proto.NODE_UNAVAILABLE, "disk-low", "free space on the node (old job folders in %s)" % p("jobs"),
                            message="%s GB free on %s; jobs need %s GB" % (disk["free_gb"], disk["where"], disk["min_free_gb"]),
                            largest=largest_jobs())
        if not limits_ok(node) and not fault("skip-limits-check"):
            raise NodeError(proto.NODE_UNAVAILABLE, "limits-unavailable",
                            "on the PC: wsl -d mu-compute -u mu -- mu node selftest (design §19 step 1)",
                            message="the node cannot enforce a job's memory limit (self-test: %s)" % (
                                (node.get("selftest") or {}).get("detail") or "not run since this boot"))
        if spec["mu_version"] not in installed_versions():
            raise NodeError(proto.NODE_TOOLCHAIN, "toolchain-missing", "on the PC: wsl -d mu-compute -u mu -- mu install %s"
                            % spec["mu_version"], message="μ %s is not installed on the node" % spec["mu_version"],
                            installed=installed_versions())
        check_snapshot(spec)
        os.makedirs(p("jobs"), mode=0o700, exist_ok=True)
        tmp = p("jobs", ".%s.%d" % (job_id, os.getpid()))
        shutil.rmtree(tmp, ignore_errors=True)
        os.mkdir(tmp, 0o700)
        now_ns = time.time_ns()
        job = dict(spec, job_id=job_id, accepted_ns=now_ns, accepted_at=proto.iso(now_ns / 1e9), key=key,
                   node=node.get("name"))
        durable.write_record(os.path.join(tmp, "job.json"), job)
        durable.write_record(os.path.join(tmp, "state.json"), {"state": "queued", "attempt": 1, "updated": proto.iso()})
        event(tmp, "accepted from %s (attempt 1)" % key)
        os.rename(tmp, d)
        durable.sync_dir(p("jobs"))
        register_pin(spec["project_id"], spec["mu_version"])
        journal("submit id=%s key=%s project=%s accepted" % (job_id, key, spec["project_id"]))
        j = load_job(job_id)
        if fault("start-on-submit"):
            start_job(j)
        pos = status_of(j)["position"]
    if fault("job-in-session"):
        return run_in_session(load_job(job_id))
    return {"ok": True, "job_id": job_id, "state": "running" if fault("start-on-submit") else "queued", "position": pos,
            "duplicate": False}


def largest_jobs():
    sizes = []
    for n in os.listdir(p("jobs")) if os.path.isdir(p("jobs")) else []:
        tot = 0
        for dp, _, fs in os.walk(job_dir(n)):
            for f in fs:
                try:
                    tot += os.lstat(os.path.join(dp, f)).st_size
                except OSError:
                    pass
        sizes.append((tot, n))
    return [{"job_id": n, "bytes": s} for s, n in sorted(sizes, reverse=True)[:10]]


def run_in_session(j):
    """Fault job-in-session: the job is a child of this ssh session (what the design avoids)."""
    import ctypes
    d = j["dir"]
    st = {"state": "running", "attempt": 1, "unit": "session", "started_at": proto.iso(), "started_epoch": time.time(),
          "limits": {}, "updated": proto.iso()}
    log = open(os.path.join(d, "log"), "ab")

    def pdeath():
        try:
            ctypes.CDLL(None).prctl(1, signal.SIGKILL)
        except Exception:
            pass
    prepare_work(j)
    child = subprocess.Popen([os.path.join(mu_home(), "bin", "mu"), "node", "run-job", j["id"]],
                             cwd=os.path.join(d, "work", j["job"].get("cwd") or ""), stdout=log, stderr=log,
                             stdin=subprocess.DEVNULL, preexec_fn=pdeath, env=dict(os.environ, MU_JOB_ID=j["id"], MU_JOB_DIR=d))
    st["pid"] = child.pid
    durable.write_record(os.path.join(d, "state.json"), st)
    answer({"ok": True, "job_id": j["id"], "state": "running", "position": None, "duplicate": False})
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    while child.poll() is None:
        time.sleep(1)
        sys.stdout.write("\n")
        sys.stdout.flush()   # the session's end kills this process, and with it the job
    return None


def verb_status(key, args):
    return status_of(load_job(args.get("id", "")))


def verb_list(key, args):
    proj = args.get("project")
    limit = int(args.get("limit", "20") or 20)
    jobs = scan_jobs()
    mine = [j for j in jobs if not proj or j["job"].get("project_id") == proj]
    unfinished = [status_of(j, jobs) for j in mine if not j["result"]]
    finished = sorted((status_of(j, jobs) for j in mine if j["result"]), key=lambda s: s.get("ended_at") or "")[-limit:] if limit else []
    return {"ok": True, "jobs": sorted(unfinished + finished, key=lambda s: s["job_id"])}


def verb_log(key, args):
    j = load_job(args.get("id", ""))
    off = int(args.get("offset", "0") or 0)
    cap = int(args["max"]) if args.get("max") else None
    follow = args.get("follow") == "1"
    out = sys.stdout.buffer
    path = os.path.join(j["dir"], "log")
    sent = 0
    while True:
        try:
            with open(path, "rb") as f:
                f.seek(off)
                while cap is None or sent < cap:
                    b = f.read(65536 if cap is None else min(65536, cap - sent))
                    if not b:
                        break
                    out.write(b)
                    out.flush()
                    off += len(b)
                    sent += len(b)
        except FileNotFoundError:
            pass
        if not follow or (cap is not None and sent >= cap):
            return None
        if job_result(j["dir"]):
            try:
                if off >= os.path.getsize(path):
                    return None
            except OSError:
                return None
            continue
        time.sleep(0.5)


def verb_fetch(key, args):
    j = load_job(args.get("id", ""))
    if not j["result"]:
        raise NodeError(proto.NODE_USAGE, "not-finished", "mu status %s --wait" % j["id"],
                        message="job %s has no result yet" % j["id"])
    with open(os.path.join(j["dir"], "result.json"), "rb") as f:
        rdata = f.read()
    import io
    out = sys.stdout.buffer
    tf = tarfile.open(fileobj=out, mode="w|", format=tarfile.PAX_FORMAT)
    ti = tarfile.TarInfo("result.json")
    ti.size, ti.mode, ti.mtime = len(rdata), 0o644, int(time.time())
    tf.addfile(ti, io.BytesIO(rdata))
    if args.get("what") != "result":
        work = os.path.realpath(os.path.join(j["dir"], "work"))
        for o in j["result"].get("outputs") or []:
            rel = proto.safe_relpath(o.get("path", ""))
            if not rel:
                continue
            path = os.path.join(work, rel)
            if not os.path.realpath(path).startswith(work + os.sep):
                continue
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as f:
                st = os.fstat(f.fileno())
                ti = tarfile.TarInfo(rel)
                ti.size, ti.mode, ti.mtime = st.st_size, 0o644, int(st.st_mtime)
                tf.addfile(ti, f)
        if fault("fetch-extra-member"):
            b = b"this member was not declared\n"
            ti = tarfile.TarInfo("outside.txt")
            ti.size, ti.mode = len(b), 0o644
            tf.addfile(ti, io.BytesIO(b))
    tf.close()
    out.flush()
    return None


def verb_cancel(key, args):
    j = load_job(args.get("id", ""))
    d = j["dir"]
    if j["result"]:
        return {"ok": True, "job_id": j["id"], "state": j["result"]["state"], "note": "already finished"}
    durable.write_durable(os.path.join(d, "cancel"), "cancel asked by %s at %s\n" % (key, proto.iso()))
    event(d, "cancel asked by %s" % key)
    with Lock():
        j = load_job(j["id"])
        if not j["result"] and (not j["state"] or j["state"].get("state") == "queued"):
            write_result(j, "cancelled", "node", note="cancelled while queued")
            return {"ok": True, "job_id": j["id"], "state": "cancelled"}
    unit = (j["state"] or {}).get("unit")
    if unit and unit != "session":
        systemctl("stop", unit, timeout=30)
    for _ in range(40):
        r = job_result(d)
        if r:
            return {"ok": True, "job_id": j["id"], "state": r["state"]}
        time.sleep(0.25)
    return {"ok": True, "job_id": j["id"], "state": "cancelling"}


VERBS = {"hello": verb_hello, "receive-pack": verb_receive_pack, "submit": verb_submit, "status": verb_status,
         "list": verb_list, "log": verb_log, "fetch": verb_fetch, "cancel": verb_cancel}


def serve(argv):
    key = "unknown"
    if argv and argv[0].startswith("--key="):
        key = argv[0][6:]
        argv = argv[1:]
    if not proto.KEY_NAME.match(key):
        key = "unknown"
    if not argv:
        return refuse(NodeError(proto.NODE_USAGE, "unknown-verb", "verbs: " + " ".join(sorted(VERBS))))
    verb, rest = argv[0], argv[1:]
    args = {}
    for a in rest:
        k, sep, v = a.partition("=")
        if not sep or k not in ARG_RULES or not ARG_RULES[k].match(v) or k in args:
            journal("refused key=%s verb=%s bad-argument" % (key, verb[:32]))
            return refuse(NodeError(proto.NODE_USAGE, "bad-argument", "see docs/design/job-layer.md §7.3", argument=k[:32]))
        args[k] = v
    if verb not in VERBS:
        journal("refused key=%s verb=%s unknown-verb" % (key, verb[:32]))
        return refuse(NodeError(proto.NODE_USAGE, "unknown-verb", "verbs: " + " ".join(sorted(VERBS)), verb=verb[:32]))
    journal("key=%s verb=%s%s" % (key, verb, (" id=" + args["id"]) if "id" in args else (
        " repo=" + args["repo"] if "repo" in args else "")))
    try:
        if not os.path.isdir(ROOT) or not os.access(ROOT, os.W_OK):
            raise NodeError(proto.NODE_UNAVAILABLE, "not-enabled", "the root step makes it: mu-wsl2-distro install",
                            message="%s is missing or not writable by this user" % ROOT)
        out = VERBS[verb](key, args)
        if out is not None:
            answer(out)
        return 0
    except NodeError as e:
        return refuse(e)
    except BrokenPipeError:
        return 0
    except Exception as e:  # noqa
        journal("internal error in %s: %s" % (verb, traceback.format_exc().replace("\n", " | ")[-1500:]))
        return refuse(NodeError(proto.NODE_INTERNAL, "internal", "the node's journal has the details "
                                "(journalctl --user -t mu-node, /srv/mu/audit.log)", message="%s: %s" % (type(e).__name__, e)))


# ------------------------------------------------------------------------------------------------ the runner
def prepare_work(j):
    """work/ from the snapshot with a private index (§6.4), its tree checked against the spec."""
    d, job = j["dir"], j["job"]
    repo = repo_path(job["project_id"])
    work, idx = os.path.join(d, "work"), os.path.join(d, "index")
    shutil.rmtree(work, ignore_errors=True)
    if os.path.exists(idx):
        os.unlink(idx)
    os.mkdir(work, 0o700)
    if fault("git-archive"):
        a = subprocess.Popen([git_bin(), "--git-dir=" + repo, "archive", job["snapshot"]], stdout=subprocess.PIPE,
                             env=dict(os.environ, **GIT_ENV))
        subprocess.run(["tar", "-x", "-C", work], stdin=a.stdout, check=True)
        a.wait()
        return
    env = dict(os.environ, GIT_INDEX_FILE=idx, **GIT_ENV)
    r = subprocess.run([git_bin(), "--git-dir=" + repo, "--work-tree=" + work, "-c", "core.bare=false", "read-tree", "-u",
                        "--reset", job["snapshot"]], env=env, capture_output=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise RuntimeError("checkout of the snapshot failed: %s" % r.stderr.decode(errors="replace").strip())
    t = subprocess.run([git_bin(), "--git-dir=" + repo, "write-tree"], env=env, capture_output=True, stdin=subprocess.DEVNULL)
    if t.stdout.decode().strip() != job["tree"]:
        raise RuntimeError("the work folder's tree %s differs from the snapshot's %s" % (t.stdout.decode().strip()[:12], job["tree"][:12]))


def mem_default():
    kb = hw_info()["mem_total_kb"]
    return max(256 << 20, kb * 1024 - (2 << 30))


def start_job(j):
    d, job = j["dir"], j["job"]
    st = dict(j["state"] or {"attempt": 1})
    attempt = st.get("attempt", 1)
    unit = "mu-job-%s.service" % j["id"]
    lim = job.get("limits") or {}
    limits = {"mem_bytes": lim.get("mem_bytes") or mem_default(), "time_s": lim.get("time_s") or DEFAULT_TIME_S,
              "cores": lim.get("cores") or hw_info()["cores_physical"]}
    st.update(state="running", attempt=attempt, unit=unit, started_at=proto.iso(), started_epoch=time.time(),
              limits=limits, updated=proto.iso())
    j["state"] = st
    try:
        prepare_work(j)
        wd = os.path.join(d, "work", job.get("cwd") or "")
        if not os.path.isdir(wd):
            raise RuntimeError("the working folder %s is not in the snapshot" % (job.get("cwd") or "."))
    except Exception as e:  # noqa
        durable.write_record(os.path.join(d, "state.json"), st)
        write_result(j, "failed", "runner", note=str(e)[:500])
        return
    durable.write_record(os.path.join(d, "state.json"), st)
    event(d, "start attempt %d as %s" % (attempt, unit))
    systemctl("reset-failed", unit, timeout=15)
    home = mu_home()
    props = ["-p", "Type=exec"]
    if not fault("no-memory-max"):
        props += ["-p", "MemoryMax=%d" % limits["mem_bytes"], "-p", "MemorySwapMax=0"]
    props += ["-p", "RuntimeMaxSec=%d" % limits["time_s"], "-p", "TasksMax=4096",
              "-p", "KillMode=%s" % ("process" if fault("killmode-process") else "control-group"),
              "-p", "TimeoutStopSec=5", "-p", "OOMPolicy=kill", "-p", "LimitCORE=0", "-p", "Restart=no",
              "-p", "StandardOutput=append:%s" % os.path.join(d, "log"),
              "-p", "StandardError=append:%s" % os.path.join(d, "log")]
    cmd = [os.environ.get("MU_SYSTEMD_RUN") or "systemd-run", "--user", "--quiet", "--unit=" + unit,
           "--description=μ job %s" % j["id"]] + props + [
        "--working-directory=" + wd, "--setenv=MU_HOME=" + home, "--setenv=MU_JOB_ID=" + j["id"],
        "--setenv=MU_JOB_DIR=" + d, "--setenv=MU_JOB_CORES=%d" % limits["cores"],
        "--", os.path.join(home, "bin", "mu"), "node", "run-job", j["id"]]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=user_env(), stdin=subprocess.DEVNULL)
        rc, err = r.returncode, r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        rc, err = 1, str(e)
    if rc != 0:
        write_result(j, "failed", "runner", note="systemd-run failed: %s" % err.strip()[:400])
        journal("job %s: systemd-run failed: %s" % (j["id"], err.strip()[:300]))
    else:
        journal("job %s started (attempt %d)" % (j["id"], attempt))


def interrupted(j, why):
    """§9.3: a job whose process ended without a result. Re-run once (attempt 1, rerun true), else interrupted."""
    st = dict(j["state"] or {})
    if fault("trust-unit-success"):
        write_result(j, "succeeded", "runner", unit_result="success", note="taken as succeeded (test fault)")
        return
    if st.get("attempt", 1) == 1 and j["job"].get("rerun", True):
        for f in ("work", "index", "log"):
            x = os.path.join(j["dir"], f)
            if os.path.isdir(x):
                shutil.rmtree(x, ignore_errors=True)
            elif os.path.exists(x):
                os.unlink(x)
        prev = last_stop()
        note = "re-run after %s" % why if prev in ("unknown", "first") else "re-run after %s (previous stop of the node: %s)" % (why, prev)
        st.update(state="queued", attempt=2, note=note, updated=proto.iso())
        for k in ("unit", "started_at", "started_epoch", "pid"):
            st.pop(k, None)
        durable.write_record(os.path.join(j["dir"], "state.json"), st)
        j["state"] = st
        event(j["dir"], "re-queued as attempt 2: %s; previous-stop=%s" % (why, prev))
        journal("job %s re-queued: %s" % (j["id"], why))
        return
    write_result(j, "interrupted", "runner", note=why)


def classify(j, props):
    """§8.5: a job whose unit ended."""
    if j["result"]:
        return
    unit = (j["state"] or {}).get("unit")
    res = props.get("Result", "")
    if os.path.exists(os.path.join(j["dir"], "cancel")):
        write_result(j, "cancelled", "runner", unit_result=res or None, note="cancelled while running")
    elif res == "oom-kill":
        write_result(j, "oom", "runner", unit_result=res, note="the job used more memory than its limit")
    elif res == "timeout":
        write_result(j, "timeout", "runner", unit_result=res, note="the job ran longer than its time limit")
    elif props.get("LoadState") == "not-found" or res in ("success", ""):
        interrupted(j, "the job's process ended without a result")
    else:
        write_result(j, "failed", "runner", unit_result=res, note="the unit ended with %s" % res)
    if unit and unit != "session":
        systemctl("reset-failed", unit, timeout=15)


def session_alive(st):
    pid = st.get("pid")
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def step(first=False):
    """One pass of the runner under the lock. -> True while a job is queued or running."""
    with Lock():
        jobs = scan_jobs()
        running = []
        for j in jobs:
            st = j["state"] or {}
            if j["result"] or st.get("state") != "running":
                continue
            if st.get("unit") == "session":
                if session_alive(st):
                    running.append(j)
                else:
                    j["result"] = job_result(j["dir"])
                    if not j["result"]:
                        interrupted(j, "the ssh session that ran it ended")
                continue
            props = unit_props(st.get("unit", ""))
            if unit_active(props) and props.get("LoadState") != "error":
                if first and fault("no-adopt"):
                    interrupted(j, "the runner restarted (test fault: not adopted)")
                    continue
                if os.path.exists(os.path.join(j["dir"], "cancel")):
                    systemctl("stop", st["unit"], timeout=30)
                running.append(j)
                continue
            j["result"] = job_result(j["dir"])   # the shim may have written it since the scan
            classify(j, props)
            if not j["result"] and (j["state"] or {}).get("state") == "running":
                running.append(j)
        queued = queued_jobs(jobs)
        for j in list(queued):
            if os.path.exists(os.path.join(j["dir"], "cancel")):
                write_result(j, "cancelled", "runner", note="cancelled while queued")
                queued.remove(j)
            elif not j["state"]:
                durable.write_record(os.path.join(j["dir"], "state.json"), {"state": "queued", "attempt": 1,
                                                                             "updated": proto.iso()})
        if not running and queued:
            start_job(queued[0])
            return True
        return bool(running or queued)


def recover():
    """§9.3, at the runner's start: folders without a valid job.json are deleted; the rest goes through step()."""
    try:
        names = os.listdir(p("jobs"))
    except OSError:
        return
    with Lock():
        for n in names:
            d = job_dir(n)
            if n.startswith(".") or not durable.read_record(os.path.join(d, "job.json")):
                shutil.rmtree(d, ignore_errors=True)
                journal("recovery: removed %s (never acknowledged)" % n)


def set_busy(busy):
    f = os.path.join(mu_home(), "state", "busy")
    if busy:
        if read_text(f).split("\n")[0].strip() != str(os.getpid()):
            os.makedirs(os.path.dirname(f), exist_ok=True)
            durable.write_durable(f, "%d\nμ job runner (mu node run): a job is queued or running\n" % os.getpid(), 0o644)
    elif os.path.exists(f) and read_text(f).split("\n")[0].strip() == str(os.getpid()):
        os.unlink(f)


def run_runner():
    if not os.path.isdir(ROOT) or not os.access(ROOT, os.W_OK):
        sys.stderr.write("mu node run: %s is missing or not writable (the root step makes it: mu-wsl2-distro install)\n" % ROOT)
        return proto.NODE_UNAVAILABLE
    started = os.environ.get("MU_INFRA_VERSION") or default_version()
    tick = float(os.environ.get("MU_NODE_TICK", "1"))
    stop = [False]
    signal.signal(signal.SIGTERM, lambda *a: stop.__setitem__(0, True))
    tmp = p("tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, mode=0o700, exist_ok=True)
    os.makedirs(p("jobs"), mode=0o700, exist_ok=True)
    journal("runner started (μ %s, pid %d)" % (started, os.getpid()))
    node = node_info()
    if (node.get("selftest") or {}).get("boot_id") != boot_id():
        selftest()
    recover()
    first = True
    while not stop[0]:
        try:
            busy = step(first)
        except Exception:  # noqa
            journal("runner error: %s" % traceback.format_exc().replace("\n", " | ")[-1500:])
            busy = True
        first = False
        set_busy(busy)
        if not busy and default_version() and default_version() != started:
            journal("runner: the default is now μ %s: restarting with it" % default_version())
            return 0
        for _ in range(int(max(1, tick * 10))):
            if stop[0]:
                break
            time.sleep(tick / max(1, int(tick * 10)))
    set_busy(False)
    journal("runner stopped")
    return 0


# ------------------------------------------------------------------------------------------------ the shim
def cgroup_oom_kills():
    try:
        cg = read_text("/proc/self/cgroup").strip().splitlines()[-1].split("::", 1)[1]
        ev = read_text("/sys/fs/cgroup%s/memory.events" % cg)
        m = re.search(r"^oom_kill ([0-9]+)", ev, re.M)
        return int(m.group(1)) if m else 0
    except (IndexError, ValueError):
        return 0


def eval_outputs(work, patterns):
    import glob
    real = os.path.realpath(work)
    outs, missing, seen = [], [], set()
    for pat in patterns:
        hits = []
        for rel in sorted(glob.glob(pat, root_dir=work, recursive=True)):
            path = os.path.join(work, rel)
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if not os.path.isfile(path) or os.path.islink(path) or not os.path.realpath(path).startswith(real + os.sep):
                continue
            hits.append(rel)
            if rel in seen:
                continue
            seen.add(rel)
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for b in iter(lambda: f.read(1 << 20), b""):
                    h.update(b)
            outs.append({"path": rel.replace(os.sep, "/"), "size": st.st_size, "sha256": h.hexdigest()})
        if not hits:
            missing.append(pat)
    return outs, missing


def run_job(job_id):
    j = load_job(job_id)
    d, job = j["dir"], j["job"]
    st = j["state"] or {}
    home = mu_home()
    sys.stdout.write("== μ job %s · attempt %s · %s · started %s\n" % (job_id, st.get("attempt", 1),
                                                                      " ".join(job["argv"])[:200], proto.iso()))
    sys.stdout.flush()
    env = dict(os.environ, MU_VERSION=job["mu_version"])
    env.pop("MU_PROJECT", None)
    child = subprocess.Popen([os.path.join(home, "bin", "mu"), "run"] + job["argv"], env=env, stdin=subprocess.DEVNULL)
    term = [False]

    def forward(signum, frame):
        term[0] = True
        try:
            child.send_signal(signum)
        except OSError:
            pass
    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(s, forward)
    rc = child.wait()
    if term[0]:
        sys.stdout.write("== stopped by a signal: no result (the runner decides)\n")
        sys.stdout.flush()
        signal.signal(signal.SIGTERM, signal.SIG_DFL)   # end as systemd expects of a stopped unit: by the signal
        os.kill(os.getpid(), signal.SIGTERM)
        return 143
    outs, missing = eval_outputs(os.path.join(d, "work"), job.get("outputs") or [])
    sys.stdout.write("== %s · %d output(s) · ended %s\n" % ("exit %d" % rc if rc >= 0 else "signal %d" % -rc, len(outs),
                                                            proto.iso()))
    sys.stdout.flush()
    durable.sync_file(os.path.join(d, "log"))
    os.sync()
    if fault("before-result"):
        os._exit(0)
    if rc == 0:
        state, ec, sig = "succeeded", 0, None
    elif rc < 0:
        state, ec, sig = ("oom" if cgroup_oom_kills() else "failed"), None, -rc
    else:
        state, ec, sig = "failed", rc, None
    j["state"] = st
    write_result(j, state, "shim", unit_result="success" if state == "succeeded" else None, exit_code=ec, signal=sig,
                 outputs=outs, outputs_missing=missing)
    return 0


# ------------------------------------------------------------------------------------------------ selftest, enable
ALLOC = "import sys\nb = b'\\x01' * (%d << 20)\nopen(sys.argv[1], 'w').write('ok')\n"


def selftest(verbose=False):
    """Two units with MemoryMax=64M: 32 MB must succeed, 256 MB must end with Result=oom-kill (§10)."""
    tag = secrets.token_hex(3)
    os.makedirs(p("tmp"), mode=0o700, exist_ok=True)
    py = sys.executable
    out = {}
    for name, mb in (("small", 32), ("large", 256)):
        unit = "mu-selftest-%s-%s.service" % (tag, name)
        marker = p("tmp", "selftest-%s-%s" % (tag, name))
        cmd = [os.environ.get("MU_SYSTEMD_RUN") or "systemd-run", "--user", "--quiet", "--unit=" + unit,
               "-p", "Type=exec", "-p", "MemoryMax=64M", "-p", "MemorySwapMax=0", "-p", "TasksMax=64",
               "-p", "RuntimeMaxSec=120", "-p", "TimeoutStopSec=5", "-p", "OOMPolicy=kill",
               "--", py, "-I", "-S", "-c", ALLOC % mb, marker]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=user_env(), stdin=subprocess.DEVNULL)
        props = {}
        if r.returncode == 0:
            for _ in range(240):
                props = unit_props(unit)
                if not unit_active(props) or props.get("LoadState") == "not-found":
                    break
                time.sleep(0.25)
        out[name] = {"unit": unit, "start": r.returncode, "error": r.stderr.strip()[:300], "result": props.get("Result"),
                     "active": props.get("ActiveState"), "completed": os.path.exists(marker)}
        systemctl("stop", unit, timeout=15)
        systemctl("reset-failed", unit, timeout=15)
        if os.path.exists(marker):
            os.unlink(marker)
    ok = out["small"]["completed"] and out["small"]["start"] == 0 and out["large"]["result"] == "oom-kill" \
        and not out["large"]["completed"]
    if ok:
        detail = "MemoryMax=64M: 32 MB ran, 256 MB ended with oom-kill"
    elif out["small"]["start"] != 0:
        detail = "systemd-run --user failed: %s" % (out["small"]["error"] or "no message")
    elif not out["small"]["completed"]:
        detail = "a 32 MB job did not complete under MemoryMax=64M (Result=%s)" % out["small"]["result"]
    else:
        detail = "a 256 MB job under MemoryMax=64M ended with Result=%s%s: the memory limit is not enforced" % (
            out["large"]["result"], " after finishing" if out["large"]["completed"] else "")
    if fault("selftest-fail"):
        ok, detail = False, "forced to fail (test fault selftest-fail); really: " + detail
    rec = {"ok": ok, "at": proto.iso(), "boot_id": boot_id(), "detail": detail, "units": out}
    if os.path.isdir(ROOT) and os.access(ROOT, os.W_OK):
        update_node_info(selftest=rec)
    journal("selftest: %s (%s)" % ("PASS" if ok else "FAIL", detail))
    return rec


def sd_escape(s):
    return s.replace("%", "%%").replace("$", "$$")


def unit_dir():
    return os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".config", "systemd", "user")


def runner_unit(home):
    return """[Unit]
Description=μ job runner (mu node run)
[Service]
ExecStart="{mu}" node run
Environment="MU_HOME={home}"
Restart=always
RestartSec=2
KillMode=process
TimeoutStopSec=5
[Install]
WantedBy=default.target
""".format(mu=sd_escape(os.path.join(home, "bin", "mu")), home=sd_escape(home))


def node_main_error(msg, hint, as_json, code=1):
    if as_json:
        proto.emit_json({"ok": False, "exit": code, "message": msg, "next": hint})
    else:
        sys.stderr.write("mu node: %s\n    next: %s\n" % (msg, hint))
    return code


def cmd_enable(args, as_json):
    name, min_free = None, None
    it = iter(args)
    for a in it:
        if a == "--name":
            name = next(it, "")
        elif a == "--min-free-gb":
            min_free = next(it, "")
    if name is not None and not proto.NODE_NAME.match(name):
        return node_main_error("--name must be [a-z0-9][a-z0-9-]{0,31}", "e.g. --name office-pc", as_json, 2)
    if min_free is not None and not re.match(r"^[0-9]{1,6}$", min_free):
        return node_main_error("--min-free-gb must be a whole number", "", as_json, 2)
    if not os.path.isdir(ROOT) or not os.access(ROOT, os.W_OK):
        return node_main_error("%s is missing or not writable by %s" % (ROOT, pwd.getpwuid(os.getuid()).pw_name),
                               "as root, once: mu-wsl2-distro install (it makes /srv/mu, owner mu, mode 0700)", as_json, 69)
    kw = {}
    if name or not node_info().get("name"):
        kw["name"] = name or os.uname().nodename.split(".")[0].lower()
    if min_free is not None:
        kw["min_free_gb"] = int(min_free)
    elif "min_free_gb" not in node_info():
        kw["min_free_gb"] = DEFAULT_MIN_FREE_GB
    n = update_node_info(**kw)
    st = selftest()
    ud = unit_dir()
    os.makedirs(ud, exist_ok=True)
    durable.write_durable(os.path.join(ud, "mu-node.service"), runner_unit(mu_home()), 0o644)
    r1 = systemctl("daemon-reload")
    r2 = systemctl("enable", "--now", "mu-node.service")
    if r2.returncode != 0:
        systemctl("restart", "mu-node.service")
    else:
        systemctl("restart", "mu-node.service")
    active = runner_state()
    out = {"ok": active == "active" and st["ok"], "node": n.get("name"), "runner": active, "selftest": st["ok"],
           "selftest_detail": st["detail"], "unit": os.path.join(ud, "mu-node.service"),
           "errors": [x for x in (r1.stderr.strip(), r2.stderr.strip()) if x]}
    if as_json:
        proto.emit_json(out)
    else:
        print("%s  runner mu-node.service: %s" % ("PASS" if active == "active" else "FAIL", active))
        print("%s  memory limit self-test: %s" % ("PASS" if st["ok"] else "FAIL", st["detail"]))
        if not st["ok"]:
            print("      jobs are refused until it passes; see docs/design/job-layer.md §19 step 1")
    return 0 if out["ok"] else 69


def cmd_disable(as_json):
    systemctl("disable", "--now", "mu-node.service")
    f = os.path.join(unit_dir(), "mu-node.service")
    if os.path.exists(f):
        os.unlink(f)
    systemctl("daemon-reload")
    if as_json:
        proto.emit_json({"ok": True, "runner": runner_state()})
    else:
        print("the job runner is stopped and removed; job folders in %s are kept" % ROOT)
    return 0


def cmd_selftest(as_json):
    if not os.path.isdir(ROOT):
        sys.stderr.write("note: %s does not exist yet: the result is not recorded\n" % ROOT)
    st = selftest()
    if as_json:
        proto.emit_json(dict(st, ok=st["ok"]))
    else:
        print("%s  memory limit for user units: %s" % ("PASS" if st["ok"] else "FAIL", st["detail"]))
        if not st["ok"]:
            print("      check: stat -fc %T /sys/fs/cgroup (cgroup2fs), cat /proc/1/cgroup,")
            print("      cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.controllers (memory)")
            print("      on WSL older than 2.9.5: kernelCommandLine = cgroup_no_v1=all in .wslconfig, wsl --shutdown, repeat")
    return 0 if st["ok"] else 69


def main(args):
    as_json = "--json" in args
    rest = [a for a in args if a != "--json"]
    sub = rest[0] if rest else ""
    if sub == "serve":
        return serve(args[1:])
    if sub == "run":
        return run_runner()
    if sub == "run-job":
        if len(rest) != 2 or not proto.JOB_ID.match(rest[1]):
            sys.stderr.write("usage: mu node run-job <job-id>\n")
            return 64
        try:
            return run_job(rest[1])
        except NodeError as e:
            sys.stderr.write("mu node run-job: %s\n" % (e.message or e.error))
            return 70
    if sub == "enable":
        return cmd_enable(rest[1:], as_json)
    if sub == "disable":
        return cmd_disable(as_json)
    if sub == "selftest":
        return cmd_selftest(as_json)
    if sub == "info":
        a = hello_answer("local", node_info())
        if as_json:
            proto.emit_json(a)
        else:
            print(json.dumps(a, indent=1))
        return 0
    sys.stderr.write(__doc__.split("\n\n")[0] + "\n")
    return 64
