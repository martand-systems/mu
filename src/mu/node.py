"""μ job layer, compute-node side (docs/design/job-layer.md §6.4, §7-§12). Runs as the user `mu`.

  mu node serve --key=<name> <verb> [key=value ...]   what the gate (mu-node-gate, sshd's ForceCommand) runs
  mu node run                                        the runner: mu-node.service; several jobs at once, admitted by
                                                     cores and memory (docs/design/scheduler.md, src/mu/sched.py)
  mu node run-job <id>                               the shim: main process of the unit mu-job-<id>.service
  mu node enable [--name N] [--min-free-gb G]        write and start ~/.config/systemd/user/mu-node.service
  mu node disable                                    stop and remove it (jobs are kept)
  mu node selftest                                   prove that a user unit's MemoryMax is enforced (§10)
  mu node info                                       the `hello` answer, here
  mu node settings [gc.KEY=VALUE ...] [min_free_gb=G]  show or change the node's settings in node.json (the clean-up of
                                                     finished job folders: docs/design/job-layer.md §24)
  mu node host-script                                the Windows path of the default release's mu-wsl2-host.ps1, for an
                                                     administrator's PowerShell (docs/design/pairing.md §9.1)

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
  bus-error-is-end     a failed `systemctl show` counts as "the unit ended" (review: durability 1)
  overwrite-result     a result.json that is already valid is replaced by a later one
  trust-run-rc         a failed systemd-run means "not started", without asking systemd (durability 2)
  log-truncate-on-rerun  a re-run deletes the log of the attempt before it (durability 3)
  runner-pass-aborts   one job's error stops the runner's whole pass (durability 4)
  no-start-disk-check  the runner starts a job whatever the free disk space
  no-log-cap           the runner lets a job's log grow without a limit
  no-unit-check-torn   a job whose state.json is unreadable counts as queued, whatever its unit and events say (5)
  delete-damaged       recovery deletes a job folder whose job.json is unreadable (instead of moving it aside)
The scheduler's (docs/design/scheduler.md §13; the plan's own are in src/mu/sched.py):
  stamp-before-checkout   a job's grant and start time are written before its checkout (no re-check after it)
  grant-in-memory-only    the allocation counts only jobs this runner process started (adopted grants forgotten)
  free-on-cancel-request  a running job's grant is freed when its cancel is asked, not when its unit has ended
  mem-max-is-capacity     a job's MemoryMax is the node's job memory, not its grant
  no-express-quota        express units get no CPUQuota
  no-affinity             the shim does not pin the job to its CPUs
  widen-check-main-thread-only  the sampler checks only each process's main thread for a widened CPU mask
  no-thread-env           the thread variables (OMP_NUM_THREADS, ...) and TMPDIR are not set
  no-usage                no usage in results (and hello lacks the feature)
  no-waiting-for          status and list leave out waiting_for and starts_by
  no-capacity-check       submit takes a request larger than the node (the client skips its check too)
  requeue-new-ns          a job re-queued after an interruption goes to the back of its band
  keep-tmp-on-rerun       a re-run keeps the first attempt's TMPDIR
  restart-only-idle       the runner takes a new default only when no job is queued or running
  release-to-back         release puts the job at the back of its band
  partial-order-write     a move writes only the moved job into queue.json
  priority-to-front       priority puts the job at the front of its new band
  holds-in-order-file-only  a hold is kept in queue.json only (not in the job's state.json)
  rebuild-held-as-queued  a lost state.json of a held job is rebuilt as queued
  owner-from-node-json    owners are read from node.json (a file jobs can write), not /etc/mu/owners
  queue-project-filter    `queue` lists only the caller's project
  drop-field              the arranging answers lack `place` (the JSON-shape check must notice)
  held-as-queued          a hold is written as state "queued" with a flag (the previous release's runner starts it)
  list-keeps-pauses       list sends a finished job's pause history, which is status's alone (contract §3.1; r2-shapes)
 the review of 2026-11-15 (docs/design/scheduler.md "Release 1 as built"), each undoing one fix:
  no-size-bounds          a spec's sizes are not bounded (a huge --time overflows every plan: no start, no status)
  no-display-check        name, project and submitted are stored and shown as sent (forged lines, crashes)
  runner-ignores-start-lock  the runner starts jobs while an update holds the start lock
  start-lock-pid-only     a start lock whose pid is alive is held, whatever process now has that pid or boot it is
  starts-after-switch     the runner goes on starting jobs after the default moved, until its restart
  no-needed-versions      the runner does not write $MU_HOME/state/needed-versions (gc may remove a held job's μ)
  free-on-result          a finished job's grant is freed at its result, before its unit has ended
  keep-usage-on-rerun     a re-run keeps attempt 1's usage.json (attempt 2 reports its figures)
  no-cancel-auth          any paired key may cancel any job
  audit-terse             refused arranging lines leave out what was asked; band changes show the new band only
  update-always-force     `update` runs `mu autoupdate run --force` for every key
  grant-after-systemd-run the grant reaches state.json only after systemd-run (a unit can exist without it)
  unconfirmed-as-interrupted  a start whose unit never came counts as an interrupted attempt (not a failed start)
  no-note-active          the runner never records a unit's activation (end bound, cgroup, start_unconfirmed)
release 2 (pause, resume, lending, the timers, auto-pause): the faults are listed in src/mu/pausectl.py and
src/mu/sched.py. Two test injectors here set up a race or a slow step for an e2e row; they are not checks:
  shim-lingers-after-result  the shim waits 8 s after writing result.json before it exits (Q31 b)
  slow-checkout           the checkout of a job named e2e-slow-checkout takes 60 s more, under the lock (Q35)
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

from mu import durable, proto, sched
from mu.durable import fault
from mu import pausectl

ROOT = os.environ.get("MU_NODE_ROOT") or "/srv/mu"
DEFAULT_MIN_FREE_GB = 20
DEFAULT_TIME_S = 12 * 3600
DEFAULT_LOG_CAP_GB = 4        # a job whose log grows past this is stopped (node.json log_cap_gb)
START_TRIES = 3               # systemd-run failures (the unit never existed) before a job is failed
GIT_ENV = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
UPDATE_HOOK = """#!/bin/sh
# μ: snapshots are write-once; only refs/mu/snap/<job-id> can be pushed (docs/design/job-layer.md §6.4)
case "$1" in refs/mu/snap/[0-9]*) ;; *) echo "mu: only refs/mu/snap/<job-id> can be pushed here" >&2; exit 1 ;; esac
case "$2" in *[!0]*) [ "$2" = "$3" ] || { echo "mu: snapshot $1 exists and cannot be changed or deleted" >&2; exit 1; } ;; esac
exit 0
"""
ARG_RULES = {"id": proto.JOB_ID, "project": proto.PROJECT_ID, "repo": proto.REPO_NAME, "offset": re.compile(r"^[0-9]{1,15}$"),
             "max": re.compile(r"^[0-9]{1,12}$"), "limit": re.compile(r"^[0-9]{1,5}$"), "follow": re.compile(r"^[01]$"),
             "protocol": re.compile(r"^[0-9]{1,4}$"), "what": re.compile(r"^result$"),
             # queue control (scheduler.md §20.4): from and others are claims made by code mu owns, like by=
             "before": proto.JOB_ID, "after": proto.JOB_ID, "to": re.compile(r"^(front|back)$"),
             "class": re.compile(r"^(urgent|high|normal|low)$"), "from": proto.PROJECT_ID, "others": re.compile(r"^[01]$")}
FEATURES = ("sched", "usage", "queue-control", "update", "stats", "watch", "metrics", "gc")
ARG_RULES["interval"] = re.compile(r"^[0-9]{1,2}$")
LOCK_WAIT_S = 120             # a verb waits this long for the queue lock (the runner holds it through checkouts)
SAMPLE_EVERY = 5              # ticks between two samples of the running jobs (§7.1)
STARTED_HERE = set()          # fault grant-in-memory-only: the jobs this runner process started
# release 2 (scheduler.md §20.5-§20.7, §22): pause limits are constants, not node.json settings
PAUSE_DEFAULT_S = 30 * 60     # mu pause without --for
PAUSE_HAND_MAX_S = 2 * 3600   # one hand pause at most
PAUSE_BUDGET_S = 4 * 3600     # paused in all per attempt, and never more than the job's own --time
AUTO_PAUSE_S = 30 * 60        # one auto-pause at most (the urgent job's t + MARGIN + 30 s must fit in it)
AUTO_PAUSE_MAX = 2            # auto-pauses per attempt
AUTO_COOLDOWN_S = 15 * 60     # running between two auto-pauses of one job
LEND_MARGIN_MIN = 512 << 20   # held = min(grant, memory.current + max(512 MiB, 5 %))
FREEZE_WAIT_S = 30            # systemctl freeze must reach "frozen" within this
DEAD_MAN_S = 120              # the dead-man timer thaws a frozen job at its deadline + this
VALVE_AFTER_S = 60            # a borrower still alive this long past its lender's deadline is killed
PAUSE_COUNT_MAX = 20          # pauses per attempt (review 2026-11-15: 200 pause/resume cycles cost 4,200 systemd calls)
BACKSTOP_ROOM_S = DEAD_MAN_S + 60   # RuntimeMaxSec's room past t + budget for a thaw after D (the dead-man's at D + 120)
ARG_RULES["for"] = re.compile(r"^[0-9]{1,5}$")
ARG_RULES["dry"] = re.compile(r"^[01]$")      # gc dry=1 (release 2026.11.12, the clean-up: src/mu/nodegc.py)
ARG_RULES["off"] = re.compile(r"^[01]$")      # keep off=1


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
    """flock on /srv/mu/lock around every change to the queue. WAIT (seconds): give up after it with `busy` (exit 69,
    nothing changed), for the arranging verbs while the runner holds the lock through a checkout (§20.8)."""

    def __init__(self, wait=None):
        self.wait = wait

    def __enter__(self):
        self.fd = os.open(p("lock"), os.O_RDWR | os.O_CREAT, 0o600)
        if self.wait is None:
            fcntl.flock(self.fd, fcntl.LOCK_EX)
            return self
        end = time.time() + self.wait
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.time() > end:
                    os.close(self.fd)
                    raise NodeError(proto.NODE_UNAVAILABLE, "busy", "retry in a minute",
                                    message="the queue lock was not free within %d s (the runner is checking out a "
                                    "job); nothing was changed" % self.wait)
                time.sleep(0.2)

    def __exit__(self, *exc):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


# ------------------------------------------------------------------------------------------------ systemd
def user_env():
    """The environment for systemctl --user and systemd-run --user: this user's runtime folder and bus. An inherited
    XDG_RUNTIME_DIR or DBUS_SESSION_BUS_ADDRESS of another user (a system-wide /etc/environment, as on the CI runner,
    reaches the user manager through PAM) gives "Failed to connect to bus: Permission denied"."""
    e = dict(os.environ)
    mine = "/run/user/%d" % os.getuid()
    x = e.get("XDG_RUNTIME_DIR", "")
    try:
        ours = bool(x) and os.stat(x).st_uid == os.getuid()
    except OSError:
        ours = False
    if not ours:
        e["XDG_RUNTIME_DIR"] = mine
    if e.get("DBUS_SESSION_BUS_ADDRESS") and e["XDG_RUNTIME_DIR"] not in e["DBUS_SESSION_BUS_ADDRESS"]:
        e.pop("DBUS_SESSION_BUS_ADDRESS")
    return e


def systemctl(*args, timeout=60):
    cmd = [os.environ.get("MU_SYSTEMCTL") or "systemctl", "--user"] + list(args)
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=user_env(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 1, "", str(e))


def unit_props(unit):
    r = systemctl("show", unit, "-p", "LoadState", "-p", "ActiveState", "-p", "SubState", "-p", "Result",
                  "-p", "ExecMainStatus", "-p", "ExecMainCode", "-p", "ActiveEnterTimestampMonotonic", "-p", "ControlGroup",
                  "-p", "FreezerState")
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


def unit_ended(props):
    """Does systemd say for certain that the unit is over? A failed `systemctl show` (LoadState=error: the bus, a
    re-exec of the user manager, a timeout) says nothing, and the job is taken as still running (review: durability
    1). Ended: the unit is gone (not-found), or loaded and inactive or failed with a Result."""
    load, active = props.get("LoadState"), props.get("ActiveState")
    if load == "error" and fault("bus-error-is-end"):
        return True
    if load == "not-found":
        return True
    return load == "loaded" and active in ("inactive", "failed") and bool(props.get("Result"))


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


def cpu_ids():
    """The CPU ids this process may use (the runner's affinity: all of the VM's vCPUs)."""
    try:
        return sorted(os.sched_getaffinity(0))
    except AttributeError:   # macOS (local tests): no affinity calls
        return list(range(os.cpu_count() or 1))


def capacity(node=None):
    """C, M and the reserves (scheduler.md §3.1), from the kernel and node.json `sched`."""
    return sched.capacity(node if node is not None else node_info(), cpu_ids(), hw_info()["mem_total_kb"] * 1024)


def cpu_limit():
    """limits.cpu in hello (§5.3): affinity set by the shim, or none where the call does not exist."""
    return "affinity" if hasattr(os, "sched_setaffinity") and not fault("no-affinity") else "none"


def owners():
    """The owner keys (§20.4): the names in /etc/mu/owners (root, written by the first pairing by invite); with no such
    file and exactly one key in /etc/mu/authorized_keys/mu, that key; else none (warned about in hello and queue).
    MU_NODE_OWNERS / MU_NODE_KEYS: other files (tests)."""
    if fault("owner-from-node-json"):
        v = node_info().get("owners")
        return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []
    path = os.environ.get("MU_NODE_OWNERS") or "/etc/mu/owners"
    if os.path.exists(path):
        return [x.strip() for x in read_text(path).splitlines() if proto.KEY_NAME.match(x.strip())]
    keys = [ln.split() for ln in read_text(os.environ.get("MU_NODE_KEYS") or "/etc/mu/authorized_keys/mu").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")]
    names = [k[-1] for k in keys if len(k) >= 3]
    return names if len(names) == 1 and proto.KEY_NAME.match(names[0]) else []


def owner_warnings(own):
    if own:
        return []
    return ["no owner key on this node (/etc/mu/owners is missing and the node has more than one key): nobody may "
            "arrange other workstations' jobs; the file is written at the node's next root step"]


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
    def last(k):
        v = re.findall(r"^%s=(.*)$" % k, st, re.M)
        return v[-1] if v else None
    return {"enabled": bool(en) and en[-1] == "on", "last_result": last("last_result"), "last_check": last("last_check"),
            "last_message": last("last_message"), "pending_switch": last("pending_switch") or None,
            "pending_gc": last("pending_gc") or None}


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


def waiting_jobs(jobs):
    """Unfinished jobs that are queued or held (a missing state.json counts as queued, as before)."""
    return [j for j in jobs if not j["result"] and (not j["state"] or j["state"].get("state") in ("queued", "held"))]


def read_order():
    """/srv/mu/queue.json (§20.8): {schema, rev, order: [ids], updated}; {} when missing or unreadable (the walk is
    then FIFO within each band)."""
    return durable.read_record(p("queue.json")) or {}


def write_order(ids, **extra):
    """queue.json replaced whole: every queued and held job, in walk order (§20.3)."""
    old = read_order()
    rec = {"schema": 1, "rev": int(old.get("rev") or 0) + 1, "order": list(ids), "updated": proto.iso()}
    if fault("holds-in-order-file-only"):
        rec["held"] = list(extra.get("held", old.get("held") or []))
    durable.write_record(p("queue.json"), rec)
    return rec


def is_held(j, order=None):
    if fault("held-as-queued"):
        return not j["result"] and bool((j["state"] or {}).get("held_flag"))
    if fault("holds-in-order-file-only"):
        return not j["result"] and j["id"] in ((order if order is not None else read_order()).get("held") or [])
    return not j["result"] and (j["state"] or {}).get("state") == "held"


def walk_jobs(jobs, order=None):
    """The walk (§20.3): queued and held jobs by (band, place in queue.json, accepted_ns). -> [{id, band, held, j}]."""
    order = read_order() if order is None else order
    items = []
    for j in waiting_jobs(jobs):
        st = j["state"] or {}
        ns = j["job"].get("accepted_ns", 0)
        if fault("requeue-new-ns") and st.get("requeued_ns"):
            ns = st["requeued_ns"]
        items.append({"id": j["id"], "band": sched.band_of(st, j["job"]), "accepted_ns": ns, "held": is_held(j, order),
                      "j": j})
    return sched.walk(items, order.get("order"))


def queued_jobs(jobs):
    """The jobs that can start, in walk order (held ones left out)."""
    return [it["j"] for it in walk_jobs(jobs) if not it["held"]]


def grant_of(j):
    g = (j["state"] or {}).get("grant")
    return g if isinstance(g, dict) and isinstance(g.get("cpus"), list) else None


DRAIN_S = 60                  # a result this recent: its unit may still be stopping what the job left (TimeoutStopSec=5)


def draining(j):
    """A job whose shim wrote its result while its unit still runs (stopping leftover processes): its grant stays
    counted until systemd says the unit ended (§9; review 2026-11-15). Only results of the last DRAIN_S seconds are
    asked about, so finished jobs of the past cost no systemctl call."""
    st = j["state"] or {}
    if not j["result"] or st.get("state") != "running" or not grant_of(j) or st.get("unit") in (None, "session"):
        return False
    if fault("free-on-result"):
        return False
    try:
        if time.time() - os.path.getmtime(os.path.join(j["dir"], "result.json")) > DRAIN_S:
            return False
    except OSError:
        return False
    return not unit_ended(unit_props(st["unit"]))


def running_info(jobs, here_only=False, drain=()):
    """[{id, grant, end, started_epoch}] of the running jobs, for the plan (§3.2): grants come from state.json every
    pass; a running job without one holds the whole node. DRAIN: jobs with a result whose unit has not ended yet."""
    out = []
    for j in jobs:
        st = j["state"] or {}
        if (j["result"] and j["id"] not in drain) or st.get("state") != "running":
            continue
        if fault("free-on-cancel-request") and os.path.exists(os.path.join(j["dir"], "cancel")):
            continue
        if here_only and j["id"] not in STARTED_HERE:
            continue
        g = grant_of(j)
        out.append({"id": j["id"], "grant": g, "end": pausectl.end_bound(st) if g else None,
                    "started_epoch": st.get("started_epoch"), "held": False, "pause": pausectl.pause_item(st)})
    return out


def queue_items(w, cap):
    out = []
    for it in w:
        try:
            out.append({"id": it["id"], "held": it["held"], "req": sched.resolve(it["j"]["job"], cap)})
        except sched.TooBig as e:
            out.append({"id": it["id"], "held": it["held"], "error": e})
        except Exception as e:  # noqa: one job whose request cannot be worked out fails; it never stops the pass
            out.append({"id": it["id"], "held": it["held"], "error": sched.BadSpec("%s: %s" % (type(e).__name__, e), cap)})
    return out


def safe_plan(now, cap, running, items, disk):
    """sched.plan(), or, should it raise, a plan in which nothing starts (every running job holding the node): a
    status, queue or hello still answers, and the runner's pass goes on (security review, 2026-11-15)."""
    try:
        return sched.plan(now, cap, running, items, disk)
    except Exception:  # noqa
        if fault("runner-pass-aborts") or fault("no-size-bounds"):
            raise
        journal_once("plan", "runner: the plan failed, nothing starts: %s" % traceback.format_exc().replace("\n", " | ")[-800:])
        return sched.plan(now, cap, [{"id": r["id"], "grant": None, "end": None} for r in running], [], disk)


def disk_block(node):
    """None while jobs may start; else what waiting_for says (§3.6)."""
    if fault("no-start-disk-check"):
        return None
    d = disk_info(node)
    if d["free_gb"] is not None and d["free_gb"] < d["min_free_gb"]:
        return {"free_gb": d["free_gb"], "min_free_gb": d["min_free_gb"], "where": d["where"]}
    return None


def view(jobs=None, node=None):
    """The plan as it stands now, for status, list, queue and hello: read only, no lock."""
    jobs = scan_jobs() if jobs is None else jobs
    node = node_info() if node is None else node
    cap = capacity(node)
    order = read_order()
    w = walk_jobs(jobs, order)
    now = time.monotonic()
    pl = safe_plan(now, cap, running_info(jobs), queue_items(w, cap), disk_block(node))
    return {"plan": pl, "cap": cap, "walk": w, "now": now, "wall": time.time(), "jobs": jobs, "order": order}


def wall_of(v, mono):
    """A monotonic bound as wall-clock ISO time at answer time (§7.1), or None."""
    try:
        return proto.iso(v["wall"] + (mono - v["now"])) if isinstance(mono, (int, float)) else None
    except (OverflowError, OSError, ValueError):
        return None


def waiting_text(v, jid):
    """waiting_for with its times in wall-clock ISO (the plan keeps monotonic seconds)."""
    w = v["plan"]["waiting"].get(jid)
    if not w:
        return None
    w = json.loads(json.dumps(w))
    r = w.get("reservation")
    if r:
        r["starts_by"] = wall_of(v, r.get("starts_by"))
    return w


def event(d, text):
    try:
        with open(os.path.join(d, "events.log"), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (proto.iso(), text))
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass


def status_of(j, v=None):
    """A job's status (job-layer §7.3), with the scheduler's fields (scheduler.md §7.1): request, grant, class, band,
    place, position, held, waiting_for, starts_by, ends_by, usage, affinity_widened. V: view() (computed if absent)."""
    job, st, res = j["job"], j["state"] or {}, j["result"]
    state = res.get("state") if res else st.get("state", "queued")
    v = view() if v is None and not res else v
    pl = v["plan"] if v else {}
    try:
        log_size = os.path.getsize(os.path.join(j["dir"], "log"))
    except OSError:
        log_size = 0
    out = {"ok": True, "job_id": j["id"], "name": shown(job.get("name")), "project_id": job.get("project_id"),
           "state": state, "attempt": (res or st).get("attempt", st.get("attempt", 1)),
           "position": pl.get("position", {}).get(j["id"]) if not res and state == "queued" else None,
           "queued_at": job.get("accepted_at"), "started_at": (res or {}).get("started_at") or st.get("started_at"),
           "ended_at": (res or {}).get("ended_at"), "exit_code": (res or {}).get("exit_code"), "log_size": log_size,
           "note": (res or {}).get("note") or st.get("note"), "unit": st.get("unit"),
           "cancel_requested": os.path.exists(os.path.join(j["dir"], "cancel")),
           "project": shown(job.get("project")), "key": job.get("key"), "band": sched.band_of(st, job)}
    if res:
        if not fault("no-usage"):
            out["usage"] = res.get("usage")
        out["grant"] = res.get("grant")
        out["run_s"], out["paused_s"] = res.get("run_s"), res.get("paused_s")
        out["pauses"] = res.get("pauses")
        out["pausable"] = job.get("pausable") is True
        if res.get("affinity_widened"):
            out["affinity_widened"] = res["affinity_widened"]
        return out
    if v:
        try:
            out["request"] = {k: val for k, val in sched.resolve(job, v["cap"]).items() if k != "env_cores"}
        except sched.TooBig as e:
            out["request"] = {"error": "too-big", "message": str(e)}
    if state in ("queued", "held"):
        out["place"] = pl.get("place", {}).get(j["id"])
        out["held"] = st.get("held") if state == "held" else False
        if not fault("no-waiting-for"):
            out["waiting_for"] = waiting_text(v, j["id"])
            out["starts_by"] = wall_of(v, pl.get("starts_by", {}).get(j["id"])) if j["id"] == pl.get("head") else None
            if j["id"] == pl.get("head") and pl.get("starts_by", {}).get(j["id"]) is None:
                out["starts_by_why"] = START_UNKNOWN.get(pl.get("S_why"), pl.get("S_why"))
    elif state == "running":
        g = grant_of(j)
        out["grant"] = public_grant(g)
        out["class"] = (g or {}).get("class")
        out["ends_by"] = wall_of(v, pausectl.end_bound(st)) if g and v else None
        if st.get("affinity_widened"):
            out["affinity_widened"] = st["affinity_widened"]
        pausectl.job_fields(j, v, v["jobs"] if v else [j], out)
    else:
        out["pausable"] = job.get("pausable") is True
    return out


START_UNKNOWN = {"grantless": "start time unknown: a job from the previous release holds the node",
                 "disk": "start time unknown: disk low", "valve": "start time unknown: a job outlived its time limit",
                 "unknown-end": "start time unknown: a running job's end is not known yet"}


def public_grant(g):
    if not isinstance(g, dict):
        return None
    out = {k: g.get(k) for k in ("cores", "mem_bytes", "time_s", "class") if k in g}
    if isinstance(g.get("cpus"), list):
        out["cpus"] = sched.fmt_cpus(g["cpus"])
    return out


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
    """result.json, written once: a valid result already there is kept (the first word on a job is final; a shim that
    ends after the runner gave up on it cannot change it), and returned."""
    old = durable.read_record(os.path.join(j["dir"], "result.json"))
    if old and not fault("overwrite-result"):
        event(j["dir"], "result %s (%s) not written: the job already has its result (%s)" % (state, written_by,
                                                                                            old.get("state")))
        j["result"] = old
        return old
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
    if st.get("grant"):
        res["grant"] = public_grant(st["grant"])
    # release 2 (§20.6): running time without the pauses, the pauses themselves
    res["run_s"] = round(pausectl.run_seconds(st, res["wall_s"]), 1) if res["wall_s"] is not None else None
    res["paused_s"] = round(pausectl.paused_s(st), 1) if started else 0
    res["pauses"] = list(st.get("pauses") or [])
    if st.get("pauses_more"):
        res["pauses_more"] = st["pauses_more"]
    if st.get("affinity_widened"):
        res["affinity_widened"] = st["affinity_widened"]
    usage = extra.pop("usage", None)
    if usage is None and started and written_by == "runner":
        usage = sampled_usage(j, res.get("wall_s"))
    if usage is not None and not fault("no-usage"):
        res["usage"] = usage
    res.update(extra)
    durable.write_record(os.path.join(j["dir"], "result.json"), res)
    event(j["dir"], "result %s (%s%s)" % (state, written_by, ", " + note if note else ""))
    j["result"] = durable.read_record(os.path.join(j["dir"], "result.json"))
    shutil.rmtree(os.path.join(j["dir"], "tmp"), ignore_errors=True)   # the job's TMPDIR (§6)
    return res


def sampled_usage(j, wall_s):
    """`usage` of a runner-written result (oom, timeout, interrupted, cancelled): the sampler's last figures, since the
    unit's cgroup is gone by now (dashboard §4.2). None when nothing was sampled."""
    u = read_json(os.path.join(j["dir"], "usage.json"))
    if not u:
        return None
    out = {k: u.get(k) for k in ("cpu_s", "user_s", "sys_s", "mem_peak_bytes", "mem_work_peak_bytes", "pids_peak",
                                 "mem_events")}
    if fault("read-cgroup-after-exit"):   # the unit's cgroup is gone by now: this reads nothing
        cg = (j["state"] or {}).get("cgroup") or "/nonexistent"
        g = cg_usage(cg) if os.path.isdir("/sys/fs/cgroup" + cg) else {}
        out = {k: g.get(k) for k in out}
    out["cores_avg"] = nodestats.cores_avg(out.get("cpu_s"), j["state"], wall_s)
    out["source"] = "runner-sampled"
    out["sampled_at"] = u.get("t")
    return out


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else None
    except (OSError, ValueError):
        return None


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


def auto_policy(node=None):
    """node.json sched.auto_pause: "marked" (default: only jobs submitted --pausable), "any" or "off" (§20.7)."""
    a = ((node if node is not None else node_info()).get("sched") or {}).get("auto_pause")
    return a if a in ("marked", "any", "off") else "marked"


def lent_view(pl):
    lt = pl.get("lent") or {"cores": 0, "cpus": [], "mem_bytes": 0, "borrowed": {"cores": 0, "cpus": [], "mem_bytes": 0}}
    b = lt.get("borrowed") or {}
    return {"cores": lt["cores"], "cpus": sched.fmt_cpus(lt["cpus"]), "mem_bytes": lt["mem_bytes"],
            "borrowed": {"cores": b.get("cores", 0), "cpus": sched.fmt_cpus(b.get("cpus") or []),
                         "mem_bytes": b.get("mem_bytes", 0)}}


def hello_answer(key, node):
    v = view(node=node)
    jobs = v["jobs"]
    running = [j for j in jobs if not j["result"] and (j["state"] or {}).get("state") == "running"]
    st = node.get("selftest") or {}
    cap, pl = v["cap"], v["plan"]
    own = owners()
    express = sum(1 for j in running if (grant_of(j) or {}).get("class") == "express")
    out = {"ok": True, "protocol": proto.PROTOCOL, "protocol_min": proto.PROTOCOL_MIN, "node": node.get("name"),
           "key": key, "mu": {"default": default_version(), "installed": installed_versions(),
                              "infra": os.environ.get("MU_INFRA_VERSION")},
           "hw": hw_info(), "disk": disk_info(node),
           "limits": {"memory": limits_ok(node) or fault("skip-limits-check"), "pids": True, "selftest_at": st.get("at"),
                      "selftest_detail": st.get("detail"), "cpu": cpu_limit()},
           "queue": {"running": len(running), "queued": len([x for x in v["walk"] if not x["held"]]),
                     "held": len([x for x in v["walk"] if x["held"]]), "express": express,
                     "paused": len([j for j in running if pausectl.pause_item(j["state"] or {})])},
           "last_stop": last_stop(), "runner": runner_state(), "autoupdate": autoupdate_info(), "time": proto.iso(),
           # the scheduler (scheduler.md §7.1, §20.12): new fields only, protocol unchanged
           "features": [f for f in FEATURES if not (f == "usage" and fault("no-usage"))] + (
               ["pause"] if pausectl.available(node) else []),
           "auto_pause": auto_policy(node), "pause": {"mem_lend": (node.get("freezer") or {}).get("mem_lend") is True},
           "capacity": {"cores": cap["cores"], "cpus": sched.fmt_cpus(cap["cpus"]), "mem_bytes": cap["mem_bytes"],
                        "reserve_system": cap["reserve_system"], "reserve_express": cap["reserve_express"],
                        "express_max": cap["express_max"]},
           "allocated": {"cores": pl["allocated"]["cores"], "cpus": sched.fmt_cpus(pl["allocated"]["cpus"]),
                         "mem_bytes": pl["allocated"]["mem_bytes"]},
           "free": {"cores": 0, "mem_bytes": 0} if pl.get("S_why") == "grantless" or any(
               grant_of(j) is None for j in running) else {
               "cores": len(cap["cpus"]) - pl["allocated"]["cores"],
               "mem_bytes": max(0, cap["mem_bytes"] - pl["allocated"]["mem_bytes"])},
           "owners": own, "you_are_owner": key in own, "warnings": owner_warnings(own),
           "update": {"running": unit_props(UPDATE_UNIT).get("ActiveState") in ("active", "activating")},
           "gc": nodegc.summary()}   # the clean-up of finished job folders (release 2026.11.12): gc.json, no walk
    if node.get("order_restored_at"):
        out["queue"]["order_restored_at"] = node["order_restored_at"]
    return out


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
            os.sync()   # config, HEAD and the hook on disk before the repository takes its name (durability 6)
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
    bounds = {"mem_bytes": sched.MAX_MEM_BYTES, "time_s": sched.MAX_TIME_S, "cores": sched.MAX_CORES}
    for k in ("mem_bytes", "time_s", "cores"):
        v = lim.get(k)
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v <= 0):
            bad("limits." + k, "must be null or a positive integer")
        if v is not None and v > bounds[k] and not fault("no-size-bounds"):
            bad("limits." + k, "must be at most %d" % bounds[k])
    if lim.get("quick") not in (None, False, True):
        bad("limits.quick", "must be true, false or absent")
    if lim.get("quick") is True and any(lim.get(k) for k in ("mem_bytes", "time_s", "cores")):
        raise NodeError(proto.NODE_USAGE, "quick-with-flags", "--quick alone, or --cores/--mem/--time without it",
                        message="--quick is 1 core, 512 MB, 10 min: it cannot take --cores, --mem or --time")
    if spec.get("priority") is not None and spec.get("priority") not in sched.BANDS:
        bad("priority", "must be one of urgent, high, normal, low")
    for k in ("hold", "others"):
        if spec.get(k) not in (None, False, True):
            bad(k, "must be true, false or absent")
    if not isinstance(spec.get("rerun", True), bool):
        bad("rerun", "must be true or false")
    if not isinstance(spec.get("name", ""), str) or len(spec.get("name", "")) > 200:
        bad("name", "must be a string of at most 200 characters")
    if fault("no-display-check"):
        return
    # what other workstations see of this job (mu queue, status): strings of bounded length; control characters are
    # replaced when the job is stored (display_fields), so an older workstation's multi-line command still submits
    if spec.get("project") is not None and (not isinstance(spec["project"], str) or len(spec["project"]) > 200):
        bad("project", "must be a string of at most 200 characters")
    sub = spec.get("submitted")
    if sub is not None:
        if not isinstance(sub, dict):
            bad("submitted", "must be an object")
        for k in ("at", "host", "user"):
            if sub.get(k) is not None and (not isinstance(sub[k], str) or len(sub[k]) > 200):
                bad("submitted." + k, "must be a string of at most 200 characters")


def display_fields(spec):
    """The spec as stored: name, project and submitted's strings without control or format characters (§20.4: every
    workstation sees them in mu queue, and Claude reads mu queue --json)."""
    if fault("no-display-check"):
        return spec
    out = dict(spec)
    for k in ("name", "project"):
        if isinstance(out.get(k), str):
            out[k] = shown(out[k])
    if isinstance(out.get("submitted"), dict):
        out["submitted"] = {k: shown(v) if isinstance(v, str) else v for k, v in out["submitted"].items()}
    return out


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


def needed_path():
    return os.path.join(mu_home(), "state", "needed-versions")


def write_needed(versions):
    """$MU_HOME/state/needed-versions: the μ version of every unfinished job (queued, held, running), one per line;
    bin/mu's gc keeps them. Written when it changes (review 2026-11-15: held jobs alone leave the node idle, and a
    project's pin moves on with its next submit, so gc could remove the version a held job still needs)."""
    if fault("no-needed-versions"):
        return
    text = "".join("%s\n" % v for v in sorted(set(versions)) if isinstance(v, str) and proto.VERSION.match(v))
    if read_text(needed_path(), None) != text:
        try:
            os.makedirs(os.path.dirname(needed_path()), exist_ok=True)
            durable.write_durable(needed_path(), text, 0o644)
        except OSError as e:
            journal_once("needed", "runner: could not write %s: %s" % (needed_path(), e))


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
    cap = capacity(node)
    try:
        sizing = sched.resolve(spec, cap)
    except sched.TooBig as e:
        if not fault("no-capacity-check"):
            raise NodeError(proto.NODE_USAGE, "too-big", "the node takes at most --cores %d --mem %dM" % (
                cap["cores"], cap["mem_bytes"] >> 20), message="the job asks for more than the node has: %s" % e,
                capacity={"cores": cap["cores"], "mem_bytes": cap["mem_bytes"]})
        sizing = None
    with Lock():
        d = job_dir(job_id)
        if os.path.isdir(d):
            j = {"id": job_id, "dir": d, "job": durable.read_record(os.path.join(d, "job.json"))}
            if j["job"]:
                j["state"], j["result"] = durable.read_record(os.path.join(d, "state.json")), job_result(d)
                s = status_of(j)
                register_pin(j["job"].get("project_id", spec["project_id"]), j["job"].get("mu_version", spec["mu_version"]))
                journal("submit id=%s key=%s duplicate" % (job_id, key))
                return {"ok": True, "job_id": job_id, "state": s["state"], "position": s["position"], "duplicate": True,
                        "place": s.get("place"), "band": s.get("band")}
            set_aside(job_id, "its job.json is unreadable; submitted again")
        own = owners()
        band = spec.get("priority") or "normal"
        delays = []
        if band in ("urgent", "high"):   # §20.4: the submit row; decided here, under the lock
            w = walk_jobs(scan_jobs())
            delays = [x for x in w if sched.BANDS.index(band) < sched.BANDS.index(x["band"]) <= sched.BANDS.index("normal")]
            passes = [{"key": x["j"]["job"].get("key"), "project_id": x["j"]["job"].get("project_id")} for x in delays]
            why = sched.authorise("submit", {"key": key, "project_id": spec["project_id"], "band": "normal"}, key, own,
                                  spec["project_id"], bool(spec.get("others")), passes, band)
            if why is None and not spec.get("others") and not fault("no-others-guard"):
                why = ("other-project", "--priority %s may pass any job: it needs --others" % band)
            if why:
                journal("queue: refused submit id=%s priority=%s by=%s from=%s %s (%s)" % (
                    job_id, band, key, spec["project_id"], why[0], why[1]))
                raise NodeError(proto.NODE_NOPERM if why[0] == "not-allowed" else proto.NODE_USAGE, why[0], "",
                                message="submit --priority %s refused: %s" % (band, why[1]))
        if runner_state() != "active" and not fault("start-on-submit") and not fault("job-in-session"):
            raise NodeError(proto.NODE_UNAVAILABLE, "not-enabled", "on the PC: wsl -d mu-compute -u mu --cd /home/mu -- /home/mu/.local/bin/mu node enable",
                            message="the job runner (mu-node.service) is not running")
        disk = disk_info(node)
        if disk["free_gb"] is not None and disk["free_gb"] < disk["min_free_gb"]:
            raise NodeError(proto.NODE_UNAVAILABLE, "disk-low", "free space on the node (old job folders in %s)" % p("jobs"),
                            message="%s GB free on %s; jobs need %s GB" % (disk["free_gb"], disk["where"], disk["min_free_gb"]),
                            largest=largest_jobs())
        if not limits_ok(node) and not fault("skip-limits-check"):
            raise NodeError(proto.NODE_UNAVAILABLE, "limits-unavailable",
                            "on the PC: wsl -d mu-compute -u mu --cd /home/mu -- /home/mu/.local/bin/mu node selftest (design §19 step 1)",
                            message="the node cannot enforce a job's memory limit (self-test: %s)" % (
                                (node.get("selftest") or {}).get("detail") or "not run since this boot"))
        if spec["mu_version"] not in installed_versions():
            raise NodeError(proto.NODE_TOOLCHAIN, "toolchain-missing", "on the PC: wsl -d mu-compute -u mu --cd /home/mu -- /home/mu/.local/bin/mu install %s"
                            % spec["mu_version"], message="μ %s is not installed on the node" % spec["mu_version"],
                            installed=installed_versions())
        check_snapshot(spec)
        os.makedirs(p("jobs"), mode=0o700, exist_ok=True)
        tmp = p("jobs", ".%s.%d" % (job_id, os.getpid()))
        shutil.rmtree(tmp, ignore_errors=True)
        os.mkdir(tmp, 0o700)
        now_ns = next_accept_ns()
        job = dict(display_fields(spec), job_id=job_id, accepted_ns=now_ns, accepted_at=proto.iso(now_ns / 1e9), key=key,
                   node=node.get("name"))
        durable.write_record(os.path.join(tmp, "job.json"), job)
        st0 = {"state": "queued", "attempt": 1, "updated": proto.iso()}
        if spec.get("priority") in sched.BANDS:
            st0["band"] = spec["priority"]
        event(tmp, "accepted from %s (attempt 1)%s" % (key, ", band %s" % band if band != "normal" else ""))
        if spec.get("hold"):   # held before the rename: the job is never visible as queued (§20.8)
            event(tmp, "held by %s at submit (from=%s)" % (key, spec["project_id"]))
            if fault("holds-in-order-file-only"):
                o = read_order()
                write_order(o.get("order") or [], held=(o.get("held") or []) + [job_id])
            else:
                st0.update(state="held", held={"by": key, "at": proto.iso(), "owner": key in own})
        durable.write_record(os.path.join(tmp, "state.json"), st0)
        register_pin(spec["project_id"], spec["mu_version"])   # before the job exists: auto-update keeps its version
        write_needed(read_text(needed_path()).split() + [spec["mu_version"]])   # and gc, until the runner's next pass
        os.rename(tmp, d)
        durable.sync_dir(p("jobs"))
        journal("submit id=%s key=%s project=%s accepted%s" % (job_id, key, spec["project_id"], "".join(
            " %s=%s" % (k, v) for k, v in (("priority", spec.get("priority")), ("hold", spec.get("hold") and 1),
                                           ("others", spec.get("others") and 1)) if v)))
        j = load_job(job_id)
        if fault("start-on-submit"):
            start_job(j, {"cores": cap["cores"], "cpus": cap["cpus"], "mem_bytes": cap["mem_bytes"],
                          "time_s": sched.DEFAULT_TIME_S, "class": "normal", "env_cores": cap["cores"]})
        s = status_of(j)
    if fault("job-in-session"):
        return run_in_session(load_job(job_id))
    out = {"ok": True, "job_id": job_id, "state": "running" if fault("start-on-submit") else s["state"],
           "position": s["position"], "duplicate": False, "place": s.get("place"), "band": s.get("band"),
           "held": s.get("held"), "waiting_for": s.get("waiting_for"), "starts_by": s.get("starts_by")}
    if sizing:
        out["sizing"] = {k: v for k, v in sizing.items() if k != "env_cores"}
    if delays:
        out["delays"] = [{"job_id": x["id"], "name": shown(x["j"]["job"].get("name")),
                          "project": shown(x["j"]["job"].get("project")), "key": x["j"]["job"].get("key")}
                         for x in delays]
    return out


def next_accept_ns():
    """The queue's order (accepted_ns): the clock, but never at or before the last job accepted, so a clock that
    steps back (WSL's can) does not put a new job ahead of older ones. Under the lock; node.json keeps the last."""
    n = node_info()
    last = n.get("last_accepted_ns") if isinstance(n.get("last_accepted_ns"), int) else 0
    now = max(time.time_ns(), last + 1)
    n["last_accepted_ns"] = now
    n.setdefault("protocol", proto.PROTOCOL)
    durable.write_record(p("node.json"), n)
    return now


def set_aside(job_id, why):
    """A job folder with a final name whose job.json cannot be read is never deleted: it moves to damaged/ (its result
    and outputs may still be there), and the journal says so (review: durability 5)."""
    d = job_dir(job_id)
    if fault("delete-damaged"):
        shutil.rmtree(d, ignore_errors=True)
        journal("recovery: removed %s (%s)" % (job_id, why))
        return
    os.makedirs(p("damaged"), mode=0o700, exist_ok=True)
    dest = p("damaged", "%s.%d" % (job_id, int(time.time())))
    os.rename(d, dest)
    durable.sync_dir(p("jobs"))
    journal("recovery: %s moved to %s (%s)" % (job_id, dest, why))


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
    j = load_job(args.get("id", ""))
    out = status_of(j, None if j["result"] else view())
    if j["result"]:   # the clean-up's marks (release 2026.11.12): pruned, kept, fetched
        out.update(nodegc.public(j["dir"]))
    elif nodegc.kept(j["dir"]):
        out["kept"] = nodegc.public(j["dir"])["kept"]
    return out


def verb_list(key, args):
    proj = args.get("project")
    limit = int(args.get("limit", "20") or 20)
    # the rev before the scan (review 2026-11-15): a pass that moves it after the scan then makes the Mac list again,
    # instead of old states stamped with the new rev being taken as current for up to 5 minutes
    rev = None if fault("rev-after-scan") else nodestats.read_rev()
    v = view()
    jobs = v["jobs"]
    mine = [j for j in jobs if not proj or j["job"].get("project_id") == proj]
    unfinished = [status_of(j, v) for j in mine if not j["result"]]
    finished = sorted((status_of(j, v) for j in mine if j["result"]), key=lambda s: s.get("ended_at") or "")[-limit:] if limit else []
    for s in finished:   # a finished job's pause history (up to 20 entries) is status's alone (contract §3.1)
        if not fault("list-keeps-pauses"):
            s.pop("pauses", None)
        s.update(nodegc.public(job_dir(s["job_id"])))   # the clean-up's marks, for the jobs sent only
    return {"ok": True, "jobs": sorted(unfinished + finished, key=lambda s: s["job_id"]),
            "rev": nodestats.read_rev() if rev is None else rev}


def verb_log(key, args):
    j = load_job(args.get("id", ""))
    off = int(args.get("offset", "0") or 0)
    cap = int(args["max"]) if args.get("max") else None
    follow = args.get("follow") == "1"
    path = os.path.join(j["dir"], "log")
    sent = 0
    while True:
        off, n = send_range(path, off, None if cap is None else cap - sent)
        sent += n
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


def send_range(path, off, cap=None):
    """The bytes of PATH from OFF (at most CAP) to stdout: log and metrics (dashboard §4.2). -> (new offset, sent)."""
    out = sys.stdout.buffer
    sent = 0
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
    return off, sent


def verb_fetch(key, args):
    j = load_job(args.get("id", ""))
    if not j["result"]:
        raise NodeError(proto.NODE_USAGE, "not-finished", "mu status %s --wait" % j["id"],
                        message="job %s has no result yet" % j["id"])
    # a shared lock on job.json while the outputs are read: the clean-up (nodegc.prune_one) needs it exclusively, so
    # it never swaps a folder that is being sent; a folder swapped while this waited is read again (its new inode)
    for _ in range(3):
        lk = os.open(os.path.join(j["dir"], "job.json"), os.O_RDONLY)
        fcntl.flock(lk, fcntl.LOCK_SH)
        try:
            same = os.fstat(lk).st_ino == os.stat(os.path.join(j["dir"], "job.json")).st_ino
        except OSError:
            same = False
        if same:
            break
        os.close(lk)
    else:
        raise NodeError(proto.NODE_UNAVAILABLE, "busy", "retry in a minute", message="job %s's folder kept changing "
                        "while the fetch waited for it" % j["id"])
    try:
        return send_fetch(key, args, load_job(j["id"]))
    finally:
        os.close(lk)


def send_fetch(key, args, j):
    pr = nodegc.pruned(j["dir"])
    if pr and not fault("fetch-pruned-served"):
        raise NodeError(proto.NODE_NO_SUCH, "pruned", "mu status %s" % j["id"], message="job %s was cleaned up on the "
                        "node at %s (%s): its outputs and working copy were removed" % (j["id"], pr.get("at"),
                                                                                         pr.get("why")),
                        pruned={k: pr.get(k) for k in ("at", "why", "by", "bytes_freed", "fetched")})
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
    if args.get("what") != "result":   # the outputs went out whole: the clean-up counts from here (fetched.json)
        nodegc.note_fetch(j["dir"], j["id"], key)
    return None


def verb_cancel(key, args):
    j = load_job(args.get("id", ""))
    d = j["dir"]
    if j["result"]:
        return {"ok": True, "job_id": j["id"], "state": j["result"]["state"], "note": "already finished"}
    # the rule of hold and move (§20.4): the job's own workstation or an owner key (review 2026-11-15: cancel was the
    # one act any paired key could do to any job). Older workstations send no from=, so no --others guard here
    if not fault("no-cancel-auth") and key not in owners() and j["job"].get("key") != key:
        journal("queue: refused cancel id=%s by=%s not-allowed (the job belongs to workstation %s)" % (
            j["id"], key, j["job"].get("key")))
        raise NodeError(proto.NODE_NOPERM, "not-allowed", "ask the owner of the node, or cancel it from %s" %
                        j["job"].get("key"), message="cancel %s refused: the job belongs to workstation %s" % (
                            j["id"], j["job"].get("key")))
    durable.write_durable(os.path.join(d, "cancel"), "cancel asked by %s at %s\n" % (key, proto.iso()))
    event(d, "cancel asked by %s" % key)
    with Lock():
        j = load_job(j["id"])
        if not j["result"] and (not j["state"] or j["state"].get("state") in ("queued", "held")):
            write_result(j, "cancelled", "node", note="cancelled while held" if is_held(j) else "cancelled while queued")
            return {"ok": True, "job_id": j["id"], "state": "cancelled"}
    unit = (j["state"] or {}).get("unit")
    if unit and unit != "session":
        pausectl.stop_unit(unit)   # thaws a paused job first, so it gets its SIGTERM (§20.5)
    for _ in range(40):
        r = job_result(d)
        if r:
            return {"ok": True, "job_id": j["id"], "state": r["state"]}
        time.sleep(0.25)
    return {"ok": True, "job_id": j["id"], "state": "cancelling"}


# ------------------------------------------------------------------------------------------------ queue control
def shown(v):
    """Free text of a job's spec as answers show it (proto.one_line; the fault no-display-check shows it raw)."""
    return v if fault("no-display-check") else proto.one_line(v)


def queue_entry(j, v, frm):
    s = status_of(j, v)
    job = j["job"]
    sub = job.get("submitted") if isinstance(job.get("submitted"), dict) or fault("no-display-check") else {}
    sub = sub or {}
    s.update(project=shown(job.get("project")), key=job.get("key"),
             submitted={k: shown(sub.get(k)) for k in ("host", "user")},
             this_project=bool(frm) and job.get("project_id") == frm)
    for k in ("unit", "log_size", "cancel_requested"):
        s.pop(k, None)
    return s


def verb_queue(key, args):
    """The whole node's queue (§20.2, §20.9): running jobs, then held and queued ones in walk order. Read only."""
    v = view()
    own = owners()
    frm = args.get("from")
    cap, pl = v["cap"], v["plan"]
    running = sorted((j for j in v["jobs"] if not j["result"] and (j["state"] or {}).get("state") == "running"),
                     key=lambda j: (j["state"] or {}).get("started_at") or "")
    jobs = [queue_entry(j, v, frm) for j in running] + [queue_entry(it["j"], v, frm) for it in v["walk"]]
    if fault("queue-project-filter") and frm:
        jobs = [x for x in jobs if x.get("project_id") == frm]
    grantless = any(grant_of(j) is None for j in running)
    return {"ok": True, "node": node_info().get("name"), "rev": int(v["order"].get("rev") or 0), "owners": own,
            "you_are_owner": key in own, "warnings": owner_warnings(own),
            "capacity": {"cores": cap["cores"], "cpus": sched.fmt_cpus(cap["cpus"]), "mem_bytes": cap["mem_bytes"],
                         "reserve_express": cap["reserve_express"]},
            "allocated": {"cores": pl["allocated"]["cores"], "cpus": sched.fmt_cpus(pl["allocated"]["cpus"]),
                          "mem_bytes": pl["allocated"]["mem_bytes"]},
            "free": {"cores": 0, "mem_bytes": 0} if grantless else {
                "cores": cap["cores"] - pl["allocated"]["cores"], "cpus": sched.fmt_cpus(
                    [c for c in cap["cpus"] if c not in pl["allocated"]["cpus"]]),
                "mem_bytes": max(0, cap["mem_bytes"] - pl["allocated"]["mem_bytes"])},
            "express": {"running": pl.get("express", 0), "max": cap["express_max"]},
            "auto_pause": auto_policy(), "lent": lent_view(pl),
            "head": pl.get("head"), "starts_by_why": START_UNKNOWN.get(pl.get("S_why")) if pl.get("head") else None,
            "jobs": jobs}


def arrange_refuse(op, jid, key, frm, others, why, touch=(), asked=""):
    """ASKED: what was asked (" to=front", " before=K", " class=high"), so a refused line says what it refused."""
    journal("queue: refused %s id=%s%s by=%s%s%s %s (%s)" % (op, jid, "" if fault("audit-terse") else asked, key,
                                                            " from=%s" % frm if frm else "", " others=1" if others
                                                            else "", why[0], why[1]))
    rc = proto.NODE_NOPERM if why[0] == "not-allowed" else proto.NODE_USAGE
    raise NodeError(rc, why[0], "", message="%s %s refused: %s" % (op, jid, why[1]),
                    would_touch=[x["id"] for x in touch])


def verb_arrange(key, args, op):
    """hold, release, move, priority (§20.2-§20.4), under the queue lock; authorisation by the job's workstation or an
    owner key, the --others guard by project (from=), an audit line either way."""
    jid = args.get("id")
    if not jid:
        raise NodeError(proto.NODE_USAGE, "bad-argument", "%s id=<job-id>" % op)
    frm, others = args.get("from"), args.get("others") == "1"
    how = ref = new_band = None
    if op == "move":
        given = [k for k in ("before", "after", "to") if args.get(k)]
        if len(given) != 1:
            raise NodeError(proto.NODE_USAGE, "bad-argument", "move id=J and one of before=K, after=K, to=front|back")
        how = given[0] if given[0] != "to" else args["to"]
        ref = args.get(given[0]) if given[0] != "to" else None
        if ref == jid:
            raise NodeError(proto.NODE_USAGE, "bad-argument", "", message="a job cannot be moved next to itself")
    if op == "priority":
        new_band = args.get("class")
        if not new_band:
            raise NodeError(proto.NODE_USAGE, "bad-argument", "priority id=J class=urgent|high|normal|low")
    own = owners()
    with Lock(wait=LOCK_WAIT_S):
        v0 = view()
        j = next((x for x in v0["jobs"] if x["id"] == jid), None)
        if j is None:
            raise NodeError(proto.NODE_NO_SUCH, "no-such-job", "mu queue", message="the node has no job %s" % jid)
        if j["result"]:
            raise NodeError(proto.NODE_USAGE, "already-finished", "mu status %s" % jid, message="job %s already "
                            "finished (%s)" % (jid, j["result"].get("state")), state=j["result"].get("state"))
        w0 = v0["walk"]
        it = next((x for x in w0 if x["id"] == jid), None)
        if it is None:
            raise NodeError(proto.NODE_USAGE, "not-queued", "", message="job %s is %s, not queued or held: it can no "
                            "longer be arranged (mu cancel %s stops it)" % (jid, (j["state"] or {}).get("state"), jid),
                            state=(j["state"] or {}).get("state"))
        st = dict(j["state"] or {"state": "queued", "attempt": 1})
        job = j["job"]
        info = {"key": job.get("key"), "project_id": job.get("project_id"), "band": it["band"],
                "held_by": (st.get("held") or {}).get("by") if it["held"] else None, "band_by": st.get("band_by")}
        w1, changed = w0, False
        if op == "hold":
            changed = not it["held"]
        elif op == "release":
            changed = it["held"]
            if changed and fault("release-to-back"):
                w1, _ = sched.place_after(w0, jid, "back")
        elif op == "move":
            if ref is not None and not any(x["id"] == ref for x in w0):
                raise NodeError(proto.NODE_USAGE, "reference-not-queued", "mu queue", message="%s is not queued or "
                                "held: a job can be moved only next to a queued or held job" % ref)
            w1, new_band = sched.place_after(w0, jid, how, ref)
            changed = [x["id"] for x in w1] != [x["id"] for x in w0] or new_band != it["band"]
        elif op == "priority":
            w1, _ = sched.place_after(w0, jid, "priority", band=new_band)
            changed = new_band != it["band"] or [x["id"] for x in w1] != [x["id"] for x in w0]
        passes = sched.passed(w0, w1, jid) if op in ("move", "priority") else []
        pinfo = [{"key": x["j"]["job"].get("key"), "project_id": x["j"]["job"].get("project_id"), "id": x["id"]}
                 for x in passes]
        asked = " %s=%s" % (how, ref) if ref else (" to=%s" % how if how else (" class=%s" % new_band if new_band else ""))
        if changed:
            why = sched.authorise(op, info, key, own, frm, others, pinfo, new_band if op in ("move", "priority") else None)
            if why:
                arrange_refuse(op, jid, key, frm, others, why, passes, asked)
        was = {"place": v0["plan"]["place"].get(jid), "band": it["band"]}
        is_owner = key in own
        if changed:
            note = "by %s%s%s%s" % (key, " (owner)" if is_owner else "", " from=%s" % frm if frm else "",
                                    " others=1" if others else "")
            if op == "hold":
                event(j["dir"], "held %s" % note)   # first: a lost state.json is rebuilt held from it (§20.8)
                if fault("holds-in-order-file-only"):
                    o = read_order()
                    write_order([x["id"] for x in w0], held=(o.get("held") or []) + [jid])
                elif fault("held-as-queued"):   # the shape §20.8 rejects: the previous release's runner starts it
                    st.update(state="queued", held_flag=True, held={"by": key, "at": proto.iso(), "owner": is_owner})
                    durable.write_record(os.path.join(j["dir"], "state.json"), st)
                else:
                    st.update(state="held", held={"by": key, "at": proto.iso(), "owner": is_owner}, updated=proto.iso())
                    durable.write_record(os.path.join(j["dir"], "state.json"), st)
            elif op == "release":
                event(j["dir"], "released %s" % note)
                if fault("holds-in-order-file-only"):
                    o = read_order()
                    write_order([x["id"] for x in w1], held=[x for x in (o.get("held") or []) if x != jid])
                else:
                    st.update(state="queued", updated=proto.iso())
                    st.pop("held", None)
                    durable.write_record(os.path.join(j["dir"], "state.json"), st)
                    if fault("release-to-back"):
                        write_order([x["id"] for x in w1])
            else:
                if new_band != it["band"]:   # the band first, then the order (§20.8 write order)
                    st.update(band=new_band, band_by=key, updated=proto.iso())
                    durable.write_record(os.path.join(j["dir"], "state.json"), st)
                write_order([jid] if fault("partial-order-write") else [x["id"] for x in w1])
                event(j["dir"], "%s %s%s: band %s" % (op, how or new_band, " %s" % ref if ref else "", new_band))
        v1 = view()
        sb0, sb1 = v0["plan"]["starts_by"], v1["plan"]["starts_by"]
        pos0, pos1 = v0["plan"]["position"], v1["plan"]["position"]
        delays = []
        for x in w0:
            if x["id"] == jid or x["held"]:
                continue
            worse = (pos1.get(x["id"]) or 0) > (pos0.get(x["id"]) or 0) or (
                x["id"] in sb0 and (x["id"] not in sb1 or (sb1[x["id"]] is None and sb0[x["id"]] is not None) or (
                    sb1[x["id"]] is not None and sb0[x["id"]] is not None and sb1[x["id"]] > sb0[x["id"]] + 1)))
            if changed and worse:
                delays.append({"job_id": x["id"], "name": shown(x["j"]["job"].get("name")),
                               "project": shown(x["j"]["job"].get("project")), "key": x["j"]["job"].get("key"),
                               "starts_by_was": wall_of(v0, sb0.get(x["id"])), "starts_by": wall_of(v1, sb1.get(x["id"]))})
        jn = next(x for x in v1["jobs"] if x["id"] == jid)
        s1 = status_of(jn, v1)
        if changed:
            journal("queue: %s id=%s%s by=%s%s%s%s place %s->%s band %s%s" % (
                op, jid, asked, key, " (owner)" if is_owner else "", " from=%s" % frm if frm else "",
                " others=1" if others else "", was["place"], s1.get("place"),
                s1.get("band") if fault("audit-terse") or was["band"] == s1.get("band") else "%s->%s" % (
                    was["band"], s1.get("band")),
                " delays=%s" % ",".join(d["job_id"] for d in delays) if delays else ""))
            for d in delays:
                event(job_dir(d["job_id"]), "delayed by %s of %s by %s%s" % (op, jid, key, " from=%s" % frm if frm else ""))
    higher = [x for x in v1["walk"] if not x["held"] and sched.BANDS.index(x["band"]) < sched.BANDS.index(s1.get("band") or "normal")]
    out = {"ok": True, "exit": 0, "job_id": jid, "op": op, "changed": bool(changed), "state": s1["state"],
           "band": s1.get("band"), "place": s1.get("place"), "was": was, "higher_bands_ahead": len(higher),
           "held": s1.get("held"), "starts_by": s1.get("starts_by"), "waiting_for": s1.get("waiting_for"),
           "by": {"key": key, "owner": key in own, "from": frm, "others": others}, "delays": delays}
    if fault("drop-field"):
        out.pop("place")
    return out


def verb_hold(key, args):
    return verb_arrange(key, args, "hold")


def verb_release(key, args):
    return verb_arrange(key, args, "release")


def verb_move(key, args):
    return verb_arrange(key, args, "move")


def verb_priority(key, args):
    return verb_arrange(key, args, "priority")


UPDATE_UNIT = "mu-update.service"


def verb_update(key, args):
    """`mu compute update NODE` (scheduler.md §9 "Version switch", shell-ux §4.6): `mu autoupdate run --force` in a
    transient unit of its own, started at once whatever the queue (it takes no job's cores or place); the new release
    is installed beside the running jobs and becomes the default between two starts. Its log: /srv/mu/maint/."""
    props = unit_props(UPDATE_UNIT)
    if props.get("ActiveState") in ("active", "activating"):
        return {"ok": True, "changed": False, "unit": UPDATE_UNIT, "state": "running", "default": default_version(),
                "autoupdate": autoupdate_info(), "note": "an update is already running"}
    os.makedirs(p("maint"), mode=0o700, exist_ok=True)
    log = p("maint", "update-%s.log" % time.strftime("%Y%m%d-%H%M%S", time.gmtime()))
    home = mu_home()
    systemctl("reset-failed", UPDATE_UNIT, timeout=15)
    # an owner key forces it (also with auto-update off, and past a release's failed tries); another key runs the
    # check as the hourly timer would, now (review 2026-11-15: any paired key could repeat a failing install)
    force = key in owners() or fault("update-always-force")
    cmd = [os.environ.get("MU_SYSTEMD_RUN") or "systemd-run", "--user", "--quiet", "--collect", "--unit=" + UPDATE_UNIT,
           "--description=μ update (mu compute update, by %s)" % key, "-p", "Type=exec", "-p", "Nice=10",
           "-p", "StandardOutput=append:%s" % log, "-p", "StandardError=append:%s" % log,
           "--setenv=MU_HOME=" + home, "--", os.path.join(home, "bin", "mu"), "autoupdate", "run"] + (
        ["--force"] if force else [])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=user_env(), stdin=subprocess.DEVNULL)
        rc, err = r.returncode, r.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        rc, err = 1, str(e)
    journal("update by=%s%s: %s" % (key, " (owner, --force)" if force else "", "started %s, log %s" % (UPDATE_UNIT, log)
                                     if rc == 0 else "failed: %s" % err[:300]))
    if rc != 0:
        raise NodeError(proto.NODE_INTERNAL, "update-failed", "", message="could not start the update unit: %s" % err[:300])
    return {"ok": True, "changed": True, "unit": UPDATE_UNIT, "state": "started", "log": log, "started_at": proto.iso(),
            "default": default_version(), "autoupdate": autoupdate_info(), "forced": force}


VERBS = {"hello": verb_hello, "receive-pack": verb_receive_pack, "submit": verb_submit, "status": verb_status,
         "list": verb_list, "log": verb_log, "fetch": verb_fetch, "cancel": verb_cancel, "queue": verb_queue,
         "hold": verb_hold, "release": verb_release, "move": verb_move, "priority": verb_priority, "update": verb_update,
         "pause": pausectl.verb_pause, "resume": pausectl.verb_resume}


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
    quiet = verb in nodestats.QUIET_VERBS and not fault("journal-each-call")   # dashboard §4.2: refusals only
    if not quiet:
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
        if quiet:
            journal("refused key=%s verb=%s%s %s" % (key, verb, (" id=" + args["id"]) if "id" in args else "", e.error))
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


THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMBA_NUM_THREADS", "PYTHON_CPU_COUNT",
               "CCX_NPROC_EQUATION_SOLVER", "CCX_NPROC_RESULTS")


def job_env(j, grant):
    """The unit's environment from the grant (scheduler.md §6): the job's cores and CPUs, the thread variables of the
    solvers μ ships, Open MPI's binding left to the inherited mask (§12 item 3 was not probed: its fallback), TMPDIR."""
    cpus = sched.fmt_cpus(grant["cpus"])
    env = {"MU_JOB_CORES": str(grant.get("env_cores") or grant["cores"]), "MU_JOB_CPUS": cpus,
           "MU_JOB_CLASS": grant.get("class") or "normal"}
    if fault("no-thread-env"):
        return env
    n = str(grant["cores"])
    for k in THREAD_VARS:
        env[k] = n
    if grant.get("class") != "express":
        env["OMPI_MCA_hwloc_base_binding_policy"] = "none"
    env["TMPDIR"] = os.path.join(j["dir"], "tmp")
    return env


def start_job(j, grant, S=None, prepared=False):
    """Start one job with GRANT (from the plan): the checkout first, then for a backfilled job the re-check against the
    head's start S (§3.3), then grant, start time and start_unconfirmed in state.json in one write, then systemd-run
    (§9: the grant is on disk before the unit can exist). -> True if a unit was asked for."""
    d, job = j["dir"], j["job"]
    st = dict(j["state"] or {"attempt": 1})
    attempt = st.get("attempt", 1)
    unit = "mu-job-%s.service" % j["id"]
    g = {k: grant[k] for k in ("cores", "cpus", "mem_bytes", "time_s", "class", "env_cores", "borrowed") if k in grant}
    # release 2 (§20.6): an ordinary job's --time is enforced on running time by a timer; RuntimeMaxSec is the backstop
    g["limit_timer"] = g.get("class") in ("normal", "backfilled")
    limits = {"mem_bytes": g["mem_bytes"], "time_s": g["time_s"], "cores": g.get("env_cores") or g["cores"]}

    def stamp():
        g.update(boot_id=boot_id(), start_mono=time.monotonic(), active_mono=None)
        st.update(state="running", attempt=attempt, unit=unit, started_at=proto.iso(), started_epoch=time.time(),
                  limits=limits, grant=g, updated=proto.iso())
        st.pop("note", None) if (st.get("note") or "").startswith("waiting") else None
    if fault("stamp-before-checkout"):
        stamp()
        st["start_unconfirmed"] = True
        durable.write_record(os.path.join(d, "state.json"), st)
    j["state"] = st
    try:
        if fault("slow-checkout") and job.get("name") == "e2e-slow-checkout":   # test injector (Q35)
            time.sleep(60)
        if not prepared:
            prepare_work(j)
        wd = os.path.join(d, "work", job.get("cwd") or "")
        if not os.path.isdir(wd):
            raise RuntimeError("the working folder %s is not in the snapshot" % (job.get("cwd") or "."))
    except Exception as e:  # noqa
        if not fault("stamp-before-checkout"):
            stamp()
        durable.write_record(os.path.join(d, "state.json"), st)
        write_result(j, "failed", "runner", note=str(e)[:500])
        return False
    # a backfilled job, or a borrower whose memory from free memory was admitted for ending before S (review
    # 2026-11-15: plan() gives a borrower S only then), is checked against the head's start again after its checkout
    if g.get("class") in ("backfilled", "borrowing") and S is not None and not fault("stamp-before-checkout") and \
            not fault("backfill-ignores-shadow") and not (g.get("class") == "borrowing" and
                                                          fault("borrower-ignores-shadow")):
        late = time.monotonic() + g["time_s"] + sched.MARGIN_S - S
        if late > 0:   # the checkout took too long: it would now end after the head's start (§3.3); nothing written
            event(d, "not started: after its checkout it would end %ds after the head's start; stays queued" % late)
            return False
    if g.get("class") == "borrowing" and not (fault("lend-without-deadline") or fault("borrow-past-deadline")):
        for b in g.get("borrowed") or []:   # after the checkout: each lender still frozen, its deadline still ahead
            L = durable.read_record(os.path.join(job_dir(b["from"]), "state.json")) or {}
            pz = L.get("pause") or {}
            if pz.get("phase") != "frozen" or time.monotonic() + g["time_s"] + sched.MARGIN_S > (pz.get("deadline_mono") or 0):
                event(d, "not started: the cores lent by %s are no longer lent for its time; stays queued" % b["from"])
                return False
    if not fault("stamp-before-checkout"):
        stamp()
    os.makedirs(os.path.join(d, "tmp"), mode=0o700, exist_ok=True)   # removed with the result and before a re-run
    if os.path.exists(os.path.join(d, "timelimit")):   # an earlier attempt's marker
        os.unlink(os.path.join(d, "timelimit"))
    st["start_unconfirmed"] = True   # until systemd-run says it started the unit
    if not fault("grant-after-systemd-run"):   # the grant on disk before the unit can exist (§9, e2e Q16)
        durable.write_record(os.path.join(d, "state.json"), st)
    STARTED_HERE.add(j["id"])
    event(d, "start attempt %d as %s: granted cores=%d cpus=%s mem=%s time=%s class=%s" % (
        attempt, unit, g["cores"], sched.fmt_cpus(g["cpus"]), sched.human_gb(g["mem_bytes"]),
        sched.human_dur(g["time_s"]), g.get("class")))
    systemctl("reset-failed", unit, timeout=15)
    home = mu_home()
    props = ["-p", "Type=exec"]
    if not fault("no-memory-max"):
        mm = capacity()["mem_bytes"] if fault("mem-max-is-capacity") else g["mem_bytes"]
        props += ["-p", "MemoryMax=%d" % mm, "-p", "MemorySwapMax=0"]
    if g.get("class") == "express" and not fault("no-express-quota"):
        props += ["-p", "CPUQuota=100%"]
    backstop = g["time_s"] + (min(PAUSE_BUDGET_S, g["time_s"]) + (0 if fault("backstop-no-room") else BACKSTOP_ROOM_S)
                              if g.get("limit_timer") and not fault("count-paused-time") else 0)
    props += ["-p", "RuntimeMaxSec=%d" % backstop, "-p", "TasksMax=4096",
              "-p", "KillMode=%s" % ("process" if fault("killmode-process") else "control-group"),
              "-p", "TimeoutStopSec=5", "-p", "OOMPolicy=kill", "-p", "LimitCORE=0", "-p", "Restart=no",
              "-p", "StandardOutput=append:%s" % os.path.join(d, "log"),
              "-p", "StandardError=append:%s" % os.path.join(d, "log")]
    env = ["--setenv=%s=%s" % kv for kv in sorted(job_env(j, g).items())]
    cmd = [os.environ.get("MU_SYSTEMD_RUN") or "systemd-run", "--user", "--quiet", "--unit=" + unit,
           "--description=μ job %s" % j["id"]] + props + [
        "--working-directory=" + wd, "--setenv=MU_HOME=" + home, "--setenv=MU_JOB_ID=" + j["id"],
        "--setenv=MU_JOB_DIR=" + d] + env + [
        "--", os.path.join(home, "bin", "mu"), "node", "run-job", j["id"]]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=sched.START_WAIT_S, env=user_env(),
                           stdin=subprocess.DEVNULL)
        rc, err = r.returncode, r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        rc, err = 1, str(e)
    if fault("grant-after-systemd-run"):
        durable.write_record(os.path.join(d, "state.json"), st)
    if rc == 0:
        st.pop("start_unconfirmed", None)
        if g.get("limit_timer"):
            st["timers"] = True
        durable.write_record(os.path.join(d, "state.json"), st)
        if g.get("limit_timer"):
            pausectl.arm_limit(j, g["time_s"])   # §20.6: --time on running time (re-armed each pass if this fails)
        journal("job %s started (attempt %d, %s)" % (j["id"], attempt, g.get("class")))
        return True
    journal("job %s: systemd-run failed: %s" % (j["id"], err.strip()[:300]))
    if fault("trust-run-rc"):
        write_result(j, "failed", "runner", note="systemd-run failed: %s" % err.strip()[:400])
        return True
    # a failed or slow systemd-run may still have made the unit (a lost reply, a timeout): ask systemd. Only a unit
    # that does not exist is a start that failed; anything else stays "running" and the next pass classifies it
    if unit_props(unit).get("LoadState") == "not-found":
        start_failed(j, "systemd-run failed: %s" % err.strip()[:300])
    else:
        event(d, "systemd-run failed (%s), but systemd knows the unit: left to the next pass" % err.strip()[:200])
    return True


def start_failed(j, why):
    """The unit was never made: back to the queue (the attempt is not used up), failed after START_TRIES."""
    st = dict(j["state"] or {})
    n = int(st.get("start_failures", 0)) + 1
    if n >= START_TRIES:
        write_result(j, "failed", "runner", note="%s (%d starts failed)" % (why, n))
        return
    st.update(state="queued", start_failures=n, note="start %d of %d failed: %s" % (n, START_TRIES, why),
              updated=proto.iso())
    for k in ("unit", "started_at", "started_epoch", "pid", "start_unconfirmed", "grant", "cgroup"):
        st.pop(k, None)
    durable.write_record(os.path.join(j["dir"], "state.json"), st)
    j["state"] = st
    shutil.rmtree(os.path.join(j["dir"], "tmp"), ignore_errors=True)
    event(j["dir"], "start failed, queued again: %s" % why)


def append_log(d, line):
    """One line of μ's own in the job's log, on disk before anything depends on it."""
    try:
        with open(os.path.join(d, "log"), "ab") as f:
            f.write(line.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass


def interrupted(j, why):
    """§9.3: a job whose process ended without a result. Re-run once (attempt 1, rerun true), else interrupted. The
    log is kept and goes on (the next attempt appends after a marker line), so a client following it by byte offset
    never misses or splices output (review: durability 3)."""
    st = dict(j["state"] or {})
    if fault("trust-unit-success"):
        write_result(j, "succeeded", "runner", unit_result="success", note="taken as succeeded (test fault)")
        return
    if st.get("attempt", 1) == 1 and j["job"].get("rerun", True):
        requeue_front(j)   # queue.json first, then state.json (§20.8 write order)
        for f in ("work", "index") + (() if fault("keep-tmp-on-rerun") else ("tmp",)) + (
                () if fault("keep-usage-on-rerun") else ("usage.json",)) + (
                ("log",) if fault("log-truncate-on-rerun") else ()):
            x = os.path.join(j["dir"], f)
            if os.path.isdir(x):
                shutil.rmtree(x, ignore_errors=True)
            elif os.path.exists(x):
                os.unlink(x)
        prev = last_stop()
        note = "re-run after %s" % why if prev in ("unknown", "first") else "re-run after %s (previous stop of the node: %s)" % (why, prev)
        if not fault("log-truncate-on-rerun"):
            append_log(j["dir"], "\n== μ: attempt 1 ended without a result (%s); re-run as attempt 2 ==\n" % why)
        st.update(state="queued", attempt=2, note=note, updated=proto.iso())
        if fault("requeue-new-ns"):
            st["requeued_ns"] = time.time_ns()
        for k in ("unit", "started_at", "started_epoch", "pid", "start_unconfirmed", "grant", "cgroup", "affinity_widened"):
            st.pop(k, None)
        st = pausectl.drop_for_requeue(st)   # the attempt's pause and its history go with it (§20.8, Q33)
        for x in ("timelimit",):
            if os.path.exists(os.path.join(j["dir"], x)):
                os.unlink(os.path.join(j["dir"], x))
        durable.write_record(os.path.join(j["dir"], "state.json"), st)
        j["state"] = st
        event(j["dir"], "re-queued as attempt 2: %s; previous-stop=%s" % (why, prev))
        journal("job %s re-queued: %s" % (j["id"], why))
        return
    write_result(j, "interrupted", "runner", note=why)


def requeue_front(j):
    """§20.3: an interrupted job goes to the front of its own band in queue.json, after the jobs re-queued with it that
    were accepted before it (several keep their old order); not a sort tier, so a later move --front passes it."""
    if fault("requeue-new-ns"):
        return
    try:
        w = [x for x in walk_jobs(scan_jobs()) if x["id"] != j["id"]]
        band = sched.band_of(j["state"], j["job"])
        ns = j["job"].get("accepted_ns", 0)
        same = [i for i, x in enumerate(w) if x["band"] == band]   # contiguous: the walk is sorted by band
        if same:
            at = same[0]
            for i in same:
                x = w[i]
                if i == at and (x["j"]["state"] or {}).get("attempt", 1) > 1 and x["j"]["job"].get("accepted_ns", 0) < ns:
                    at = i + 1
                else:
                    break
        else:
            lower = [i for i, x in enumerate(w) if sched.BANDS.index(x["band"]) > sched.BANDS.index(band)]
            at = lower[0] if lower else len(w)
        ids = [x["id"] for x in w]
        ids.insert(at, j["id"])
        write_order(ids)
    except Exception:  # noqa: the order file is a view of the walk; FIFO within bands still holds without it
        journal("job %s: could not write the queue order at its re-queue: %s" % (j["id"], traceback.format_exc()[-300:]))


def log_last_mu_line(d):
    """The last line μ's shim wrote to the log ("== ..."), for a note that says why a unit failed."""
    try:
        with open(os.path.join(d, "log"), "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 65536))
            lines = [x for x in f.read().decode("utf-8", "replace").splitlines() if x.startswith("== ")]
        return lines[-1][3:200] if lines else ""
    except OSError:
        return ""


def classify(j, props):
    """§8.5: a job whose unit ended (unit_ended(props) is true)."""
    if j["result"]:
        return
    st = j["state"] or {}
    unit = st.get("unit")
    res = props.get("Result", "")
    if props.get("LoadState") == "not-found" and st.get("start_unconfirmed") and not fault("unconfirmed-as-interrupted"):
        start_failed(j, "the unit did not start (systemd-run failed and systemd does not know it)")
        return
    if os.path.exists(os.path.join(j["dir"], "logcap")):
        write_result(j, "failed", "runner", unit_result=res or None, note=read_text(os.path.join(j["dir"], "logcap")).strip()
                     or "the log grew past its limit")
    elif os.path.exists(os.path.join(j["dir"], "timelimit")) and not os.path.exists(os.path.join(j["dir"], "cancel")):
        t = (st.get("grant") or {}).get("time_s") or 0
        ps = pausectl.paused_s(st)
        write_result(j, "timeout", "runner", unit_result=res or None, note="ran %s, its --time%s" % (
            sched.human_dur(t), ", not counting %s paused" % sched.human_dur(int(ps)) if ps >= 1 else ""))
    elif os.path.exists(os.path.join(j["dir"], "cancel")):
        write_result(j, "cancelled", "runner", unit_result=res or None, note="cancelled while running")
    elif res == "oom-kill":
        pz = st.get("pause") or {}
        write_result(j, "oom", "runner", unit_result=res, note="the job used more memory than its limit" + (
            " while paused" if pz.get("phase") in pausectl.PHASES else " while its memory was lent" if
            st.get("mem_restore_pending") else ""))
    elif res == "timeout":
        write_result(j, "timeout", "runner", unit_result=res, note="the job ran longer than its time limit")
    elif props.get("LoadState") == "not-found" or res in ("success", ""):
        interrupted(j, "the job's process ended without a result")
    else:
        last = log_last_mu_line(j["dir"])
        write_result(j, "failed", "runner", unit_result=res, note="the unit ended with %s%s" % (
            res, " (the log's last line from μ: %s)" % last if last else ""))
    if unit and unit != "session":
        systemctl("reset-failed", unit, timeout=15)
        if st.get("timers") or st.get("pause"):
            pausectl.stop_timer(pausectl.limit_unit(j["id"]))
            pausectl.stop_timer(pausectl.thaw_unit(j["id"]))


def session_alive(st):
    pid = st.get("pid")
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def attempts_started(d):
    """The highest "start attempt N" in events.log (0 if none): what a job's state was, when state.json is lost."""
    n = 0
    for m in re.finditer(r"start attempt ([0-9]+) as ", read_text(os.path.join(d, "events.log"))):
        n = max(n, int(m.group(1)))
    return n


def rebuild_state(j):
    """state.json unreadable (on-disk damage): rebuilt from the unit and events.log instead of taken as queued, so a
    running job is not started a second time and the attempt counter is not reset (review: durability 5)."""
    if j["state"] or j["result"] or fault("no-unit-check-torn"):
        return
    n = attempts_started(j["dir"])
    if not n:
        # it never started: queued (what the caller does with a missing state), unless events.log says it is held:
        # the hold verb writes that line before state.json, so a lost state.json never starts a held job (§20.8)
        hold = re.findall(r"^\S+ (held|released) by ([a-z0-9-]+)", read_text(os.path.join(j["dir"], "events.log")), re.M)
        if hold and hold[-1][0] == "held" and not fault("rebuild-held-as-queued"):
            st = {"state": "held", "attempt": 1, "held": {"by": hold[-1][1], "at": proto.iso(), "owner": hold[-1][1] in owners()},
                  "updated": proto.iso(), "note": "state.json was unreadable: rebuilt as held from events.log"}
            durable.write_record(os.path.join(j["dir"], "state.json"), st)
            j["state"] = st
            journal("job %s: state.json unreadable, rebuilt as held" % j["id"])
        return
    unit = "mu-job-%s.service" % j["id"]
    st = {"state": "running", "attempt": n, "unit": unit, "updated": proto.iso(),
          "note": "state.json was unreadable: rebuilt from events.log"}
    durable.write_record(os.path.join(j["dir"], "state.json"), st)
    j["state"] = st
    event(j["dir"], "state.json unreadable: rebuilt as running, attempt %d (%s)" % (n, unit))
    journal("job %s: state.json unreadable, rebuilt as running attempt %d" % (j["id"], n))


def log_cap_bytes(node):
    v = node.get("log_cap_gb", DEFAULT_LOG_CAP_GB)
    return int(float(v) * (1 << 30)) if isinstance(v, (int, float)) and v > 0 else DEFAULT_LOG_CAP_GB << 30


def check_running(j, first, cap):
    """One running job: -> True while it is still running."""
    st = j["state"] or {}
    if st.get("unit") == "session":
        if session_alive(st):
            return True
        j["result"] = job_result(j["dir"])
        if not j["result"]:
            interrupted(j, "the ssh session that ran it ended")
        return False
    props = unit_props(st.get("unit", ""))
    if not unit_ended(props):
        if props.get("LoadState") == "error":
            event(j["dir"], "systemctl show failed (%s): taken as still running" % (props.get("error") or "")[:200])
        elif props.get("ActiveState") == "active":
            note_active(j, props)
            pausectl.reconcile(j, props)   # systemd's FreezerState wins (§20.8)
            pausectl.check_limit(j)
        if first and fault("no-adopt"):   # a runner that does not adopt: it stops the job and runs it again
            pausectl.stop_unit(st["unit"])
            interrupted(j, "the runner restarted (test fault: not adopted)")
            return False
        if os.path.exists(os.path.join(j["dir"], "cancel")):
            pausectl.stop_unit(st["unit"])
        elif not fault("no-log-cap"):
            try:
                size = os.path.getsize(os.path.join(j["dir"], "log"))
            except OSError:
                size = 0
            if size > cap and not os.path.exists(os.path.join(j["dir"], "logcap")):
                durable.write_durable(os.path.join(j["dir"], "logcap"), "the log grew past %s: the job was stopped\n"
                                      % proto.human_size(cap))
                event(j["dir"], "log over %d bytes: stopping %s" % (cap, st["unit"]))
                pausectl.stop_unit(st["unit"])
        return True
    j["result"] = job_result(j["dir"])   # the shim may have written it since the scan
    classify(j, props)
    return not j["result"] and (j["state"] or {}).get("state") == "running"


def note_active(j, props):
    """The first pass that sees a job's unit active: its activation time (the end bound counts from it, §3.2), its
    cgroup (for the sampler, never guessed), and start_unconfirmed cleared (an adopted job kept it forever before)."""
    if fault("no-note-active"):
        return
    st = j["state"] or {}
    g = grant_of(j)
    try:
        act = int(props.get("ActiveEnterTimestampMonotonic") or 0) / 1e6
    except ValueError:
        act = 0
    cg = props.get("ControlGroup") or ""
    want = (g is not None and not g.get("active_mono") and act > 0) or (cg and st.get("cgroup") != cg) or \
        st.get("start_unconfirmed")
    if not want:
        return
    st = dict(st)
    if g is not None and not g.get("active_mono") and act > 0 and g.get("boot_id") == boot_id():
        st["grant"] = dict(g, active_mono=act)
    if cg:
        st["cgroup"] = cg
    st.pop("start_unconfirmed", None)
    durable.write_record(os.path.join(j["dir"], "state.json"), st)
    j["state"] = st


def step(first=False, stop=None, started=None):
    """One pass of the runner under the lock. -> True while a job is queued or running (held jobs alone do not keep the
    node busy, §20.6). A failure with one job is journalled and the pass goes on with the others (review: durability
    4). Once STOP[0] is set (SIGTERM), no job is started. What starts is decided by sched.plan() from the grants in
    the running jobs' state.json, rebuilt every pass (scheduler.md §3.2, §9). STARTED: the default this runner
    started with; once the default is another (an update moved it), nothing more starts until the runner restarts."""
    if first and not pausectl.available() and not os.environ.get("MU_SYSTEMD_RUN"):   # (stubbed systemd: the tests set it)
        pausectl.freezer_check()   # §12 item 10 on this boot (outside the lock): the pause feature only when it passes
    with Lock():
        jobs = scan_jobs()
        nodestats.note_pass(jobs)
        node = node_info()
        cap_log = log_cap_bytes(node)
        running, drain = [], set()
        for j in jobs:
            try:
                rebuild_state(j)
                st = j["state"] or {}
                if j["result"]:
                    pausectl.finish_with_result(j)   # a job with a result is never kept frozen; its timers go
                if j["result"] and draining(j):
                    drain.add(j["id"])
                    continue
                if j["result"] or st.get("state") != "running":
                    continue
                if check_running(j, first, cap_log):
                    running.append(j)
            except Exception:  # noqa
                if fault("runner-pass-aborts"):
                    raise
                journal("runner: job %s: %s" % (j["id"], traceback.format_exc().replace("\n", " | ")[-800:]))
                if (j["state"] or {}).get("state") == "running":
                    running.append(j)   # unknown: it keeps its grant, or holds the whole node without one
        try:   # release 2: deadlines, deferred resumes, the end of auto-pauses, the valve, memory restored (§20.6)
            pausectl.runner_pass(running, jobs, drain=drain)
        except Exception:  # noqa
            if fault("runner-pass-aborts"):
                raise
            journal("runner: pauses: %s" % traceback.format_exc().replace("\n", " | ")[-800:])
        if os.path.exists(p("queue.json")) and not durable.read_record(p("queue.json")):
            order_lost(node)
        order = read_order()
        w = []
        for it in walk_jobs(jobs, order):
            j = it["j"]
            try:
                if os.path.exists(os.path.join(j["dir"], "cancel")):
                    write_result(j, "cancelled", "runner", note="cancelled while held" if it["held"] else
                                 "cancelled while queued")
                    continue
                if not j["state"]:
                    durable.write_record(os.path.join(j["dir"], "state.json"), {"state": "queued", "attempt": 1,
                                                                                 "updated": proto.iso()})
                    j["state"] = {"state": "queued", "attempt": 1}
                w.append(it)
            except Exception:  # noqa
                if fault("runner-pass-aborts"):
                    raise
                journal("runner: job %s: %s" % (j["id"], traceback.format_exc().replace("\n", " | ")[-800:]))
        busy = bool(running or drain or any(not it["held"] for it in w))
        write_needed([j["job"].get("mu_version") for j in jobs if not j["result"]])
        if stop and stop[0]:
            return busy
        cap = capacity(node)
        disk = disk_block(node)
        pl = safe_plan(time.monotonic(), cap, running_info(jobs, here_only=fault("grant-in-memory-only"), drain=drain),
                       queue_items(w, cap), disk)
        by_id = {j["id"]: j for j in jobs}
        if disk:   # the head says why it waits (as before), once
            head = next((it["j"] for it in w if not it["held"]), None)
            note = "waiting: %s GB free on %s, jobs need %s" % (disk["free_gb"], disk["where"], disk["min_free_gb"])
            if head is not None and (head["state"] or {}).get("note") != note:
                st = dict(head["state"] or {"state": "queued", "attempt": 1}, note=note, updated=proto.iso())
                durable.write_record(os.path.join(head["dir"], "state.json"), st)
                event(head["dir"], note)
        for jid, e in pl["fail"]:
            try:
                kind = getattr(e, "error", "too-big")
                write_result(by_id[jid], "failed", "runner", note="%s: %s%s" % (kind, e, " (the node's capacity was lowered "
                             "after the job was accepted)" if kind == "too-big" and "largest a job" not in str(e) else ""),
                             error=kind)
            except Exception:  # noqa
                journal("runner: job %s: %s" % (jid, traceback.format_exc().replace("\n", " | ")[-800:]))
        ap = pausectl.auto_candidate(pl, by_id, running, cap, node) if not disk else None
        if pl["starts"] or ap:
            if not take_start_lock():
                journal_once("start-lock", "runner: an update or a clean-up holds %s: no job starts until it is "
                             "done" % start_lock_path())
                return busy
            try:
                dv = default_version()
                if started and dv and dv != started and not fault("starts-after-switch"):
                    # the update moved the default: the next start is the new runner's (review 2026-11-15)
                    journal_once("switch", "runner: the default is now μ %s: no job starts before the restart" % dv)
                    return busy
                for x in pl["starts"]:
                    if stop and stop[0]:
                        break
                    refresh_start_lock()
                    try:
                        if start_job(by_id[x["id"]], x["grant"], x["S"]):
                            busy = True
                    except Exception:  # noqa
                        if fault("runner-pass-aborts"):
                            raise
                        journal("runner: starting job %s: %s" % (x["id"], traceback.format_exc().replace("\n", " | ")[-800:]))
                if ap and not (stop and stop[0]):
                    try:   # §20.7, inside the start lock: checkout, re-plan, freeze the victims, start the urgent job
                        if pausectl.auto_pause(ap, jobs, running, cap, w, disk, drain):
                            busy = True
                    except Exception:  # noqa
                        if fault("runner-pass-aborts"):
                            raise
                        journal("runner: auto-pause: %s" % traceback.format_exc().replace("\n", " | ")[-800:])
            finally:
                drop_start_lock()
        if checking(node):
            check_invariants(cap)
        return busy


_ONCE = set()


def journal_once(tag, msg):
    if tag not in _ONCE:
        _ONCE.add(tag)
        journal(msg)


def order_lost(node):
    """queue.json unreadable (§20.8): the walk falls back to (band, accepted_ns), holds and bands are kept (they are in
    each job's state.json); the file is moved aside so this is journalled once."""
    try:
        os.rename(p("queue.json"), p("queue.json.unreadable.%d" % int(time.time())))
    except OSError:
        pass
    journal("queue: order file unreadable: FIFO within each band restored (holds and bands kept)")
    n = node_info()
    n["order_restored_at"] = proto.iso()
    durable.write_record(p("node.json"), n)


def checking(node):
    return os.environ.get("MU_NODE_CHECK") == "1" or (node.get("sched") or {}).get("check") is True


def check_invariants(cap):
    """MU_NODE_CHECK=1 (the tests): the allocation rebuilt from disk after the pass must keep §3.3's invariants; a
    violation is journalled and appended to /srv/mu/state/invariant-violation, which every e2e row checks."""
    run = []
    for j in scan_jobs():
        st = j["state"] or {}
        if not j["result"] and st.get("state") == "running":
            run.append({"id": j["id"], "grant": grant_of(j), "started_epoch": st.get("started_epoch"),
                        "held": bool(st.get("held")), "pause": pausectl.pause_item(st),
                        "end": pausectl.end_bound(st) if grant_of(j) else None})
    bad = sched.invariants(cap, run)
    if bad:
        os.makedirs(p("state"), exist_ok=True)
        with open(p("state", "invariant-violation"), "a", encoding="utf-8") as f:
            f.write("%s %s\n" % (proto.iso(), "; ".join(bad)))
        journal("runner: INVARIANT VIOLATION: %s" % "; ".join(bad))
    return bad


START_LOCK_STALE_S = 960      # a start lock not refreshed for this long is stale, whoever holds it (MU_SWITCH_WAIT_S + 60)


def start_lock_path():
    return os.path.join(mu_home(), "state", "start.lock")


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True


def proc_start(pid):
    """A process's start time (/proc/PID/stat field 22, clock ticks since boot): with the boot id, it tells the process
    that wrote a lock from a later one given the same pid (or a thread id, which kill(0) also accepts). '' where there
    is no /proc (macOS)."""
    try:
        t = read_text("/proc/%d/stat" % pid)
        return t[t.rindex(")") + 2:].split()[19] if t else ""
    except (ValueError, IndexError):
        return ""


def lock_identity():
    """What a start lock's pid file holds: pid, boot id, start time (bin/mu au_take_start_lock writes the same)."""
    return "%d\n%s\n%s\n" % (os.getpid(), boot_id(), proc_start(os.getpid()))


def start_lock_holder(d):
    """-> (state, why): "free" (no lock), "live" (held by a live process of this boot that wrote it and refreshed it
    within START_LOCK_STALE_S), "taking" (made in the last minute, its pid not written yet) or "stale" (the lock of
    a process that is gone: another boot, its pid now someone else's, or not refreshed for too long; review
    2026-11-15: a lock left by a killed runner or updater stopped every start and every switch)."""
    if not os.path.isdir(d):
        return "free", ""
    f = os.path.join(d, "pid")
    lines = read_text(f).split("\n")
    pid = lines[0].strip()
    try:
        age = time.time() - os.stat(f if os.path.exists(f) else d).st_mtime
    except OSError:
        age = 0
    if not pid.isdigit():
        return ("taking", "") if age < 60 else ("stale", "no pid written for %d s" % age)
    if not pid_alive(int(pid)):
        return "stale", "pid %s has exited" % pid
    if fault("start-lock-pid-only"):   # the first form: a live pid holds the lock, whoever it now is
        return "live", ""
    b = lines[1].strip() if len(lines) > 1 else ""
    if b and b != "unknown" and boot_id() != "unknown" and b != boot_id():
        return "stale", "from another boot"
    t = lines[2].strip() if len(lines) > 2 else ""
    now_t = proc_start(int(pid))
    if t and now_t and t != now_t:
        return "stale", "pid %s is now another process" % pid
    if age > START_LOCK_STALE_S:
        return "stale", "not refreshed for %d s" % age
    return "live", ""


def take_start_lock():
    """The lock an update takes to move the default between two starts (scheduler.md §9 "Version switch"; bin/mu
    au_switch): a folder with the holder's identity. The runner holds it while it starts jobs, refreshing it before
    each start; a stale one is removed (journalled)."""
    if fault("runner-ignores-start-lock"):
        return True
    d = start_lock_path()
    try:
        os.makedirs(os.path.dirname(d), exist_ok=True)
    except OSError:
        return True   # no MU_HOME state folder (tests): nothing to coordinate with
    for _ in range(3):
        try:
            os.mkdir(d)
            with open(os.path.join(d, "pid"), "w") as f:
                f.write(lock_identity())
            return True
        except FileExistsError:
            state, why = start_lock_holder(d)
            pid = read_text(os.path.join(d, "pid")).split("\n")[0].strip()
            if state == "live" and pid != str(os.getpid()):
                return False
            if state == "taking":
                return False
            if state == "stale":
                journal("runner: removed a stale start lock (%s)" % why)
            shutil.rmtree(d, ignore_errors=True)
        except OSError:
            return True
    return False


def refresh_start_lock():
    """Before each start: the lock's age shows the runner still works through its starts."""
    try:
        os.utime(os.path.join(start_lock_path(), "pid"))
    except OSError:
        pass


def drop_start_lock():
    d = start_lock_path()
    if read_text(os.path.join(d, "pid")).split("\n")[0].strip() == str(os.getpid()):
        shutil.rmtree(d, ignore_errors=True)


def clear_stale_start_lock():
    """At the runner's start: a lock nobody live holds (a runner or updater killed while holding it) goes now."""
    d = start_lock_path()
    state, why = start_lock_holder(d)
    if state == "stale" or (state == "live" and read_text(os.path.join(d, "pid")).split("\n")[0].strip() ==
                            str(os.getpid())):
        shutil.rmtree(d, ignore_errors=True)
        journal("runner: removed a stale start lock at start (%s)" % (why or "its own pid"))


def recover():
    """§9.3, at the runner's start: temporary folders (a name starting with '.': never acknowledged) are deleted; a
    folder with a final name whose job.json is unreadable is moved to damaged/, never deleted; the rest goes through
    step()."""
    try:
        names = os.listdir(p("jobs"))
    except OSError:
        return
    with Lock():
        for n in names:
            d = job_dir(n)
            if n.startswith("."):
                shutil.rmtree(d, ignore_errors=True)
                journal("recovery: removed %s (never acknowledged)" % n)
            elif not durable.read_record(os.path.join(d, "job.json")):
                try:
                    set_aside(n, "job.json unreadable at the runner's start")
                except OSError as e:
                    journal("recovery: could not move %s aside: %s" % (n, e))


def cg_read(cg, name):
    return read_text("/sys/fs/cgroup%s/%s" % (cg, name))


def cg_kv(text):
    out = {}
    for ln in text.splitlines():
        parts = ln.split()
        if len(parts) == 2 and parts[1].isdigit():
            out[parts[0]] = int(parts[1])
    return out


def cg_usage(cg, prev=None):
    """The figures of a job's cgroup (dashboard §4.2): cpu.stat, memory.current/.peak/.stat/.events, pids. PREV: the
    last sample, whose running maxima carry on."""
    prev = prev or {}
    cpu = cg_kv(cg_read(cg, "cpu.stat"))
    mst = cg_kv(cg_read(cg, "memory.stat"))
    ev = cg_kv(cg_read(cg, "memory.events"))
    cur = read_text("/sys/fs/cgroup%s/memory.current" % cg).strip()
    peak = read_text("/sys/fs/cgroup%s/memory.peak" % cg).strip()
    pids = read_text("/sys/fs/cgroup%s/pids.current" % cg).strip()
    cur = int(cur) if cur.isdigit() else None
    work = (mst.get("anon", 0) + mst.get("shmem", 0)) if mst else None
    mmax = read_text("/sys/fs/cgroup%s/memory.max" % cg).strip()
    psi = re.search(r"^some .*avg10=([0-9.]+)", read_text("/sys/fs/cgroup%s/memory.pressure" % cg), re.M)

    def mx(a, b):
        vals = [x for x in (a, b) if isinstance(x, int)]
        return max(vals) if vals else None
    out = {"t": proto.iso(), "cpu_s": round(cpu["usage_usec"] / 1e6, 2) if "usage_usec" in cpu else prev.get("cpu_s"),
           "user_s": round(cpu["user_usec"] / 1e6, 2) if "user_usec" in cpu else prev.get("user_s"),
           "sys_s": round(cpu["system_usec"] / 1e6, 2) if "system_usec" in cpu else prev.get("sys_s"),
           "mem_current": cur,
           "mem_peak_bytes": mx(int(peak) if peak.isdigit() else None, mx(cur, prev.get("mem_peak_bytes"))),
           "mem_work_peak_bytes": mx(work, prev.get("mem_work_peak_bytes")),
           "mem_events": {"max": ev.get("max", 0), "oom_kill": ev.get("oom_kill", 0)} if ev else prev.get("mem_events"),
           "pids": int(pids) if pids.isdigit() else None,
           "pids_peak": mx(int(pids) if pids.isdigit() else None, prev.get("pids_peak")),
           # phase 1b (dashboard §4.2): the figures of one metrics.jsonl line, and the monotonic time of the cpu rate
           "anon": mst.get("anon"), "file": mst.get("file"), "shmem": mst.get("shmem"),
           "mem_max": int(mmax) if mmax.isdigit() else None, "psi_mem": float(psi.group(1)) if psi else None,
           "mono": round(time.monotonic(), 3)}
    return out


def thread_masks(cg, main_only=False):
    """(comm, Cpus_allowed_list) of every thread of every process of a cgroup (a thread can have a mask of its own)."""
    out = []
    for pid in read_text("/sys/fs/cgroup%s/cgroup.procs" % cg).split():
        if main_only:
            tasks = [os.path.join("/proc", pid)]
        else:
            try:
                tasks = [os.path.join("/proc", pid, "task", t) for t in os.listdir(os.path.join("/proc", pid, "task"))]
            except OSError:
                continue
        for t in tasks:
            txt = read_text(os.path.join(t, "status"))
            m = re.search(r"^Cpus_allowed_list:\s*(\S+)", txt, re.M)
            c = re.search(r"^Name:\s*(.*)$", txt, re.M)
            if m:
                out.append((c.group(1).strip() if c else "?", m.group(1)))
    return out


def sample():
    """Every SAMPLE_EVERY ticks, outside the lock (§7.1): each running job's usage.json (a view: rename, no fsync), its
    metrics.jsonl line, metrics/now.json (dashboard §4.2), and the per-thread CPU mask check (§5.2 item 4). Only the
    jobs the last pass saw running are read (nodestats.LAST_RUNNING), never every job folder. Nothing here decides
    anything."""
    if fault("sample-under-lock"):
        with Lock():
            return _sample()
    return _sample()


def _sample():
    t0 = time.perf_counter()
    if fault("sampler-scan-all"):
        todo = [(j["id"], j["dir"]) for j in scan_jobs()]
    else:
        todo = list(nodestats.LAST_RUNNING)
    rows = []
    for jid, d in todo:
        st = durable.read_record(os.path.join(d, "state.json")) or {}
        if st.get("state") != "running" or not st.get("cgroup") or os.path.exists(os.path.join(d, "result.json")):
            continue
        j = {"id": jid, "dir": d, "state": st, "result": None}
        cg = st["cgroup"]
        if not os.path.isdir("/sys/fs/cgroup" + cg):
            continue
        path = os.path.join(j["dir"], "usage.json")
        prev = read_json(path) or {}
        u = cg_usage(cg, prev)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(u, f)
        os.replace(tmp, path)
        rows.append(nodestats.job_line(j, st, u, prev))
        g = grant_of(j)
        if not g or g.get("class") == "express" or st.get("affinity_widened"):
            continue
        allowed = set(g["cpus"])
        for comm, lst in thread_masks(cg, main_only=fault("widen-check-main-thread-only")):
            ids = sched.parse_cpus(lst)
            if ids is not None and not set(ids) <= allowed:
                with Lock():   # a short write of a running job's state.json
                    jj = load_job(j["id"])
                    if not jj["result"] and (jj["state"] or {}).get("state") == "running":
                        s2 = dict(jj["state"], affinity_widened={"comm": comm[:64], "cpus": lst})
                        durable.write_record(os.path.join(j["dir"], "state.json"), s2)
                event(j["dir"], "affinity widened by %s: %s (granted %s)" % (comm[:64], lst, sched.fmt_cpus(g["cpus"])))
                break
    nodestats.write_now(rows, t0)


def set_busy(busy):
    f = os.path.join(mu_home(), "state", "busy")
    if busy:
        if read_text(f).split("\n")[0].strip() != str(os.getpid()):
            os.makedirs(os.path.dirname(f), exist_ok=True)
            durable.write_durable(f, "%d\nμ job runner (mu node run): a job is queued or running\n" % os.getpid(), 0o644)
    elif os.path.exists(f) and read_text(f).split("\n")[0].strip() in (str(os.getpid()), "restarting"):
        os.unlink(f)   # ours, or the one a runner left while restarting with a new default


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
    clear_stale_start_lock()
    first = True
    n = 0
    while not stop[0]:
        try:
            busy = step(first, stop, started)
        except Exception:  # noqa
            journal("runner error: %s" % traceback.format_exc().replace("\n", " | ")[-1500:])
            busy = True
        first = False
        set_busy(busy)
        try:   # the clean-up of finished job folders: between two passes (no job is starting), at most once an hour
            nodegc.tick(stop)
        except Exception:  # noqa
            journal_once("gc", "runner: clean-up: %s" % traceback.format_exc().replace("\n", " | ")[-800:])
        n += 1
        if n % SAMPLE_EVERY == 0:
            try:
                sample()   # outside the lock (§7.1)
            except Exception:  # noqa
                journal_once("sampler", "runner: sampler: %s" % traceback.format_exc().replace("\n", " | ")[-800:])
        # a new default takes effect between two passes, i.e. when no job is starting (scheduler.md §9, "Version
        # switch", superseded 2026-11-14): running units are adopted by the new runner, queued jobs keep their places
        if default_version() and default_version() != started and (not busy or not fault("restart-only-idle")):
            journal("runner: the default is now μ %s: restarting with it (%s)" % (
                default_version(), "jobs queued or running are adopted" if busy else "idle"))
            if busy:   # the marker stays busy (no pid: never taken as stale) until the new runner rewrites it
                durable.write_durable(os.path.join(mu_home(), "state", "busy"), "restarting\nμ job runner: restarting "
                                      "with a new default; jobs are queued or running\n", 0o644)
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


def own_usage(j, wall_s):
    """`usage` of a shim-written result: the shim's own cgroup (the job's unit, still there while the shim runs),
    with the working-set peak from the sampler's usage.json (no kernel file has a peak of anon + shmem)."""
    try:
        cg = read_text("/proc/self/cgroup").strip().splitlines()[-1].split("::", 1)[1]
    except IndexError:
        return None
    if not os.path.isdir("/sys/fs/cgroup" + cg):
        return None
    prev = read_json(os.path.join(j["dir"], "usage.json")) or {}
    u = cg_usage(cg, prev)
    if fault("usage-from-shim"):   # the shim's own rusage, not the job's cgroup (D01 must see the difference)
        import resource
        r = resource.getrusage(resource.RUSAGE_SELF)
        u = dict(u, cpu_s=round(r.ru_utime + r.ru_stime, 2), user_s=round(r.ru_utime, 2), sys_s=round(r.ru_stime, 2),
                 mem_peak_bytes=r.ru_maxrss * 1024, mem_work_peak_bytes=r.ru_maxrss * 1024)
    out = {k: u.get(k) for k in ("cpu_s", "user_s", "sys_s", "mem_peak_bytes", "mem_work_peak_bytes", "pids_peak",
                                 "mem_events")}
    st = durable.read_record(os.path.join(j["dir"], "state.json")) or j["state"]   # paused time (contract §3.6)
    out["cores_avg"] = nodestats.cores_avg(out.get("cpu_s"), st, wall_s) if wall_s > 0 else None
    out["source"] = "cgroup"
    return out


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
    cpus = sched.parse_cpus(os.environ.get("MU_JOB_CPUS", ""))
    if cpus and os.environ.get("MU_JOB_CLASS") != "express" and not fault("no-affinity"):
        try:   # the grant's CPUs, inherited by every child (scheduler.md §5.2): cooperative, checked by the sampler
            os.sched_setaffinity(0, cpus)
        except (AttributeError, OSError) as e:
            sys.stdout.write("== μ: could not pin the job to CPUs %s (%s): it runs unpinned\n" % (
                os.environ.get("MU_JOB_CPUS"), e))
    t0 = time.time()
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
    j["state"] = durable.read_record(os.path.join(d, "state.json")) or st   # the runner's since the start (affinity_widened)
    usage = None if fault("no-usage") else own_usage(j, time.time() - t0)
    try:
        write_result(j, state, "shim", unit_result="success" if state == "succeeded" else None, exit_code=ec, signal=sig,
                     outputs=outs, outputs_missing=missing, usage=usage)
    except OSError as e:   # the runner's note then says why (it reads the last "== " line)
        sys.stdout.write("== μ: could not write result.json (%s: %s); the job %s\n" % (
            type(e).__name__, e, "succeeded" if state == "succeeded" else "ended " + state))
        sys.stdout.flush()
        return 70
    if fault("shim-lingers-after-result"):   # test injector (Q31 b): a pause may land after the result
        time.sleep(8)
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
    rec = {"ok": ok, "at": proto.iso(), "boot_id": boot_id(), "detail": detail, "units": out,
           "freezer": pausectl.freezer_check()}   # release 2: the pause feature only when this passes
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


def cmd_settings(args, as_json):
    """`mu node settings [gc.KEY=VALUE ...] [min_free_gb=G]`: node.json's settings that people may change, checked,
    written under the queue lock (node.json is sealed: a hand edit makes it unreadable). Shows them all."""
    if not os.path.isdir(ROOT) or not os.access(ROOT, os.W_OK):
        return node_main_error("%s is missing or not writable" % ROOT, "as root, once: mu-wsl2-distro install", as_json, 69)
    gc_new, other = {}, {}
    for a in args:
        k, sep, v = a.partition("=")
        try:
            if not sep:
                raise ValueError("give KEY=VALUE, e.g. gc.after_fetch_days=7")
            if k.startswith("gc."):
                kk, vv = nodegc.parse_setting(k[3:], v)
                gc_new[kk] = vv
            elif k == "min_free_gb":
                if not re.match(r"^[0-9]{1,6}$", v):
                    raise ValueError("min_free_gb takes a whole number of GB")
                other[k] = int(v)
            else:
                raise ValueError("unknown setting %s (gc.KEY or min_free_gb)" % k)
        except ValueError as e:
            return node_main_error(str(e), "mu node settings (shows them)", as_json, 2)
    changed = []
    if gc_new or other:
        with Lock():
            n = node_info()
            g = dict(n.get("gc") or {}) if isinstance(n.get("gc"), dict) else {}
            for k, v in gc_new.items():
                if g.get(k, nodegc.DEFAULTS[k]) != v:
                    changed.append("gc.%s=%s" % (k, json.dumps(v)))
                g[k] = v
            n["gc"] = g
            for k, v in other.items():
                if n.get(k) != v:
                    changed.append("%s=%s" % (k, v))
                n[k] = v
            n.setdefault("protocol", proto.PROTOCOL)
            durable.write_record(p("node.json"), n)
        if changed:
            journal("settings: %s (mu node settings, uid %d)" % (" ".join(changed), os.getuid()))
    n = node_info()
    out = {"ok": True, "changed": changed, "gc": nodegc.settings(n), "gc_defaults": nodegc.DEFAULTS,
           "min_free_gb": n.get("min_free_gb", DEFAULT_MIN_FREE_GB), "log_cap_gb": n.get("log_cap_gb", DEFAULT_LOG_CAP_GB)}
    if as_json:
        proto.emit_json(out)
    else:
        for k, v in sorted(out["gc"].items()):
            print("gc.%-24s %s%s" % (k, json.dumps(v), "" if v == nodegc.DEFAULTS[k] else "  (default %s)" %
                                     json.dumps(nodegc.DEFAULTS[k])))
        print("%-27s %s" % ("min_free_gb", out["min_free_gb"]))
        if changed:
            print("changed: %s" % " ".join(changed))
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


def cmd_host_script(as_json):
    r"""One line on stdout: \\wsl.localhost\<distro>\...\recipe\install\hosts\wsl2\mu-wsl2-host.ps1 of the default
    release, after checking that it exists and has the `pair` action. Messages go to stderr. No root, no network.
    Exit 2 outside WSL, 3 when the default release has no host script with `pair` (run: mu update)."""
    distro = os.environ.get("WSL_DISTRO_NAME", "")
    if not re.match(r"^[A-Za-z0-9._-]{1,64}$", distro):
        sys.stderr.write("mu node host-script: this is not a WSL distro (WSL_DISTRO_NAME is not set)\n")
        return 2
    v = default_version()
    script = os.path.join(mu_home(), "toolchains", v or "-", "recipe", "install", "hosts", "wsl2", "mu-wsl2-host.ps1")
    try:
        with open(script, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        text = ""
    if not v or not re.search(r"ValidateSet\([^)]*'pair'", text):
        sys.stderr.write("mu node host-script: the default μ %s has no host script with the pair action\n    next: run: "
                         "wsl -d mu-compute -u mu --cd /home/mu -- /home/mu/.local/bin/mu update\n" % (v or "(none)"))
        return 3
    real = os.path.realpath(script)
    if any(c in real for c in "\"'`$\n\r") or not real.startswith("/"):
        sys.stderr.write("mu node host-script: the path %s holds a character PowerShell would misread\n" % real)
        return 3
    win = "\\\\wsl.localhost\\%s%s" % (distro, real.replace("/", "\\"))
    if as_json:
        proto.emit_json({"ok": True, "path": win, "version": v})
    else:
        sys.stdout.write(win + "\n")
    return 0


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
    if sub == "host-script":
        return cmd_host_script(as_json)
    if sub == "settings":
        return cmd_settings(rest[1:], as_json)
    if sub == "info":
        a = hello_answer("local", node_info())
        if as_json:
            proto.emit_json(a)
        else:
            print(json.dumps(a, indent=1))
        return 0
    usage = __doc__.split("\n\n")[1] if "\n\n" in __doc__ else __doc__
    if sub in ("-h", "--help", "help"):
        sys.stdout.write("mu node: the compute node's own side (docs/design/job-layer.md §8)\n\n%s\n" % usage)
        return 0
    sys.stderr.write("mu node: unknown subcommand %r\n\n%s\n" % (sub, usage))
    return 64


from mu import nodestats  # noqa: E402
from mu import nodegc  # noqa: E402
VERBS.update(nodestats.VERBS)
VERBS.update(nodegc.VERBS)
