"""μ node: the clean-up of finished job folders (docs/design/job-layer.md §24, release 2026.11.12). Runs as the user
`mu`, from the runner (tick(): at most once an hour, between job starts) and from `mu node serve` (the verbs `gc`, owner
keys only, and `keep`).

A finished job's folder loses its bulky parts (work/, the git index, tmp/, metrics.jsonl, anything else) and keeps a
small record: job.json, state.json, result.json (with usage and run times), events.log, the cancel and time-limit
markers, usage.json, fetched.json, keep.json, the last LOG_TAIL bytes of its log, and pruned.json (sealed: when, why,
by whom, how many bytes). The swap is one atomic exchange of two folders (renameat2 RENAME_EXCHANGE on Linux,
renamex_np RENAME_SWAP on macOS): a crash leaves the whole folder or the pruned one under the job's name, never a
folder whose record says outputs that are gone. What the swap took out goes to /srv/mu/prune/ and is deleted there
by a detached, low-priority `rm -rf` (or the next clean-up).

When (node.json `gc`, these defaults; mu node settings changes them):
  after_fetch_days 7          prune 7 days after the last fetch of the outputs (the node records `fetch`: fetched.json)
  unfetched_days 30           or 30 days after the end, when never fetched (jobs from before this release: never)
  pressure_extra_gb 10        below min_free_gb + 10 GB free where the jobs live: prune early, fetched jobs first
                              (oldest fetch first), then never-fetched ones that ended over pressure_unfetched_days ago
  pressure_unfetched_days 7   (oldest end first), until the line is passed again
  min_age_h 24                never a job that ended less than 24 h ago
  every_s 3600                the runner's clean-up at most this often
  log_tail_kb 64              the part of the log that is kept
  enabled true
Never: a job without a result (queued, held, running, paused, resuming, draining), a job named in an unfinished job's
state (a lender, an urgent job's victims), a kept job (`mu keep`), a job whose outputs are being fetched (it holds a
shared lock on its job.json).

Test faults (MU_NODE_FAULT, MU_JOBS_FAULT or /etc/mu/test-fault; tools/test-jobs/unit_gc.py):
  prune-in-place        the bulky parts are deleted in the job's own folder, one by one, without the swap
  prune-crash-before-swap  the process dies after building the pruned copy, before the swap (a test injector)
  prune-crash-after-swap   the process dies right after the swap, before the old folder is moved off (injector)
  gc-ignores-unfinished the selection takes jobs without a result too
  gc-ignores-keep       a kept job is pruned like the others
  gc-ignores-refs       a job named in an unfinished job's state is pruned
  gc-no-min-age         the 24 h minimum age is not applied
  gc-fetch-ignored      the selection ignores fetched.json (every job counts as never fetched)
  gc-pressure-all       under disk pressure every candidate is pruned, whatever the free space reached
  gc-pressure-order     under disk pressure the candidates are taken newest first
  gc-hourly-ignored     the runner's tick runs on every pass (not at most once an hour)
  gc-no-start-lock      the runner's clean-up does not take the start lock
  gc-any-key            the gc verb runs for any paired key, not only owner keys
  fetch-not-recorded    the fetch verb does not record the fetch
  fetch-pruned-served   the fetch verb sends a pruned job's result as if its outputs were there
  keep-any-key          keep acts on any job for any key (no ownership check)
  gc-ignores-fetch-lock the clean-up swaps a folder whose outputs are being fetched
and one test injector (not a check): gc-clock-ahead, the clean-up judges ages 40 days ahead (e2e G01 on real systemd)
"""
import ctypes
import ctypes.util
import errno
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time

from mu import durable, proto
from mu.durable import fault

DEFAULTS = {"after_fetch_days": 7, "unfetched_days": 30, "pressure_extra_gb": 10, "pressure_unfetched_days": 7,
            "min_age_h": 24, "every_s": 3600, "log_tail_kb": 64, "enabled": True}
LIMITS = {"after_fetch_days": (1, 3650), "unfetched_days": (1, 3650), "pressure_extra_gb": (0, 100000),
          "pressure_unfetched_days": (1, 3650), "min_age_h": (1, 87600), "every_s": (60, 86400 * 7),
          "log_tail_kb": (4, 65536)}
KEEP = ("job.json", "state.json", "result.json", "events.log", "cancel", "timelimit", "usage.json", "fetched.json",
        "keep.json")
EVENTS_CAP = 1 << 20          # events.log is kept whole up to this, else its last part
TICK_BUDGET_S = 60            # the runner's tick stops pruning after this and comes back a minute later
FETCH_RECENT_S = 600          # a fetch started this recently counts as in progress (the lock is the real guard)
JOB_REF = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-z]{6}")
LAST = {"at": 0.0, "again_at": 0.0}


def _N():
    from mu import node as N
    return N


def settings(node=None):
    """node.json `gc`, each value checked against LIMITS; anything else is the default."""
    node = _N().node_info() if node is None else node
    g = node.get("gc") if isinstance(node.get("gc"), dict) else {}
    out = dict(DEFAULTS)
    for k, v in g.items():
        if k == "enabled" and isinstance(v, bool):
            out[k] = v
        elif k in LIMITS and isinstance(v, int) and not isinstance(v, bool) and LIMITS[k][0] <= v <= LIMITS[k][1]:
            out[k] = v
    return out


def parse_setting(k, v):
    """`gc.KEY=VALUE` of mu node settings -> (key, value) or raises ValueError with the reason."""
    if k == "enabled":
        if v not in ("true", "false"):
            raise ValueError("gc.enabled is true or false")
        return k, v == "true"
    if k not in LIMITS:
        raise ValueError("unknown setting gc.%s (gc.%s)" % (k, ", gc.".join(sorted(DEFAULTS))))
    if not re.match(r"^[0-9]{1,9}$", v):
        raise ValueError("gc.%s takes a whole number" % k)
    lo, hi = LIMITS[k]
    if not lo <= int(v) <= hi:
        raise ValueError("gc.%s must be between %d and %d" % (k, lo, hi))
    return k, int(v)


# ------------------------------------------------------------------------------------------------ the job's markers
def fetched(d):
    return durable.read_record(os.path.join(d, "fetched.json"))


def kept(d):
    return durable.read_record(os.path.join(d, "keep.json"))


def pruned(d):
    return durable.read_record(os.path.join(d, "pruned.json"))


def note_fetch(d, job_id, key):
    """The fetch verb, after it sent a job's outputs whole: fetched.json (first_at, last_at, count, keys)."""
    if fault("fetch-not-recorded"):
        return
    old = fetched(d) or {}
    now = proto.iso()
    keys = [k for k in old.get("keys") or [] if isinstance(k, str)][-9:]
    if key not in keys:
        keys.append(key)
    try:
        durable.write_record(os.path.join(d, "fetched.json"), {
            "schema": 1, "job_id": job_id, "first_at": old.get("first_at") or now, "last_at": now,
            "count": int(old.get("count") or 0) + 1, "keys": keys})
    except OSError as e:   # a full disk must not fail the fetch that just worked
        _N().journal("fetch id=%s: could not record the fetch (%s)" % (job_id, e))


def public(d, res=None):
    """What status and list add for a finished job: pruned, kept, fetched (small, ISO times)."""
    out = {}
    pr = pruned(d)
    out["pruned"] = None if not pr else {k: pr.get(k) for k in ("at", "why", "by", "bytes_freed", "fetched", "log")}
    k = kept(d)
    out["kept"] = None if not k else {"by": k.get("by"), "at": k.get("at")}
    f = fetched(d)
    out["fetched"] = None if not f else {"count": f.get("count"), "first_at": f.get("first_at"),
                                         "last_at": f.get("last_at")}
    return out


# ------------------------------------------------------------------------------------------------ sizes and space
def tree_bytes(d):
    """Bytes a folder holds on disk (allocated blocks where the platform reports them: what df sees), links not
    followed."""
    tot = 0
    for dp, dns, fns in os.walk(d):
        for n in fns + dns:
            try:
                st = os.lstat(os.path.join(dp, n))
            except OSError:
                continue
            tot += getattr(st, "st_blocks", 0) * 512 if hasattr(st, "st_blocks") else st.st_size
    return tot


def fs_used(path):
    try:
        s = os.statvfs(path)
        return (s.f_blocks - s.f_bfree) * s.f_frsize
    except OSError:
        return None


def space(node, cache):
    """The free space the clean-up decides by, in GB: the figure jobs are admitted by (hello's disk: the smaller of
    /srv/mu's file system and the Windows drive the host script recorded). When the Windows drive is the smaller, a
    folder deleted inside WSL's virtual disk does not give C: its space back unless the disk is sparse, but new jobs
    reuse those blocks before the disk grows: so the space freed since the file system's highest use (kept in gc.json,
    `used_peak`) counts as free too. -> (free_gb_effective, detail)."""
    N = _N()
    d = N.disk_info(node)
    used = fs_used(N.ROOT)
    peak = max(cache.get("used_peak") or 0, used or 0)
    cache["used_peak"] = peak
    reclaim = (peak - used) if used is not None and d.get("where") != N.ROOT else 0
    free = d.get("free_gb")
    eff = None if free is None else free + reclaim / float(1 << 30)
    return eff, {"free_gb": free, "where": d.get("where"), "reclaim_inside_bytes": reclaim,
                 "min_free_gb": d.get("min_free_gb")}


# ------------------------------------------------------------------------------------------------ the selection
def ended_epoch(j):
    """When the job ended (result.json ended_at); for a job without a result (only under the fault
    gc-ignores-unfinished) when it was accepted."""
    res = j["result"] or {}
    t = proto.epoch_of(res.get("ended_at") if j["result"] else j["job"].get("accepted_at"))
    if t is None:
        try:
            t = os.path.getmtime(os.path.join(j["dir"], "result.json"))
        except OSError:
            t = time.time()
    return t


def referenced(jobs):
    """Job ids named anywhere in the state of an unfinished job (a borrower's lender, an urgent job's victims, ...)."""
    refs = set()
    if fault("gc-ignores-refs"):
        return refs
    for j in jobs:
        if not j["result"] and j["state"]:
            refs.update(x for x in JOB_REF.findall(json.dumps(j["state"])) if x != j["id"])
    return refs


def candidates(jobs, st, now, refs=None):
    """Finished jobs that may be pruned, each {j, ended, fetched_at, due, why_due}; and the counts of the others."""
    refs = referenced(jobs) if refs is None else refs
    out, skipped = [], {"unfinished": 0, "kept": 0, "pruned": 0, "referenced": 0, "young": 0}
    for j in jobs:
        if not j["result"] and not fault("gc-ignores-unfinished"):
            skipped["unfinished"] += 1
            continue
        d = j["dir"]
        if pruned(d):
            skipped["pruned"] += 1
            continue
        if kept(d) and not fault("gc-ignores-keep"):
            skipped["kept"] += 1
            continue
        if j["id"] in refs:
            skipped["referenced"] += 1
            continue
        ended = ended_epoch(j)
        if now - ended < st["min_age_h"] * 3600 and not fault("gc-no-min-age"):
            skipped["young"] += 1
            continue
        f = None if fault("gc-fetch-ignored") else fetched(d)
        fat = proto.epoch_of((f or {}).get("last_at"))
        if fat is not None and now - fat < FETCH_RECENT_S:
            skipped["young"] += 1
            continue
        if fat is not None:
            due = now - fat >= st["after_fetch_days"] * 86400
            why = "fetched %d days ago" % ((now - fat) // 86400)
        else:
            due = now - ended >= st["unfetched_days"] * 86400
            why = "ended %d days ago, never fetched" % ((now - ended) // 86400)
        out.append({"j": j, "ended": ended, "fetched_at": fat, "fetched": f, "due": due, "why_due": why})
    return out, skipped


def pressure_order(cands, st, now):
    """Under disk pressure: fetched jobs, oldest fetch first; then never-fetched ones older than
    pressure_unfetched_days, oldest end first."""
    a = sorted((c for c in cands if c["fetched_at"] is not None), key=lambda c: c["fetched_at"])
    b = sorted((c for c in cands if c["fetched_at"] is None and now - c["ended"] >=
                st["pressure_unfetched_days"] * 86400), key=lambda c: c["ended"])
    if fault("gc-pressure-order"):
        a.reverse()
        b.reverse()
    return a + b


# ------------------------------------------------------------------------------------------------ the swap
_SWAP = []


def exchange(a, b):
    """Swap two folders atomically. -> None, or the reason it cannot be done here."""
    if not _SWAP:
        libc = ctypes.CDLL(ctypes.util.find_library("c") or None, use_errno=True)
        if sys.platform == "darwin":
            fn = getattr(libc, "renamex_np", None)
            _SWAP.append(("darwin", fn))
        else:
            fn = getattr(libc, "renameat2", None)
            _SWAP.append(("linux", fn if fn is not None else getattr(libc, "syscall", None)))
    kind, fn = _SWAP[0]
    if fn is None:
        return "no atomic folder swap in this C library"
    ea, eb = os.fsencode(a), os.fsencode(b)
    if kind == "darwin":
        rc = fn(ea, eb, ctypes.c_uint(2))                                   # RENAME_SWAP
    elif getattr(fn, "__name__", "") == "renameat2":
        rc = fn(ctypes.c_int(-100), ea, ctypes.c_int(-100), eb, ctypes.c_uint(2))   # AT_FDCWD, RENAME_EXCHANGE
    else:
        nr = {"x86_64": 316, "aarch64": 276}.get(os.uname().machine)
        if nr is None:
            return "no renameat2 on %s" % os.uname().machine
        rc = fn(ctypes.c_long(nr), ctypes.c_int(-100), ea, ctypes.c_int(-100), eb, ctypes.c_uint(2))
    if rc != 0:
        e = ctypes.get_errno()
        return "the swap failed: %s" % (os.strerror(e) if e else "error")
    return None


def tail_bytes(path, n):
    """The last N bytes of a file, cut to start after a newline (no half line, no half UTF-8 character), and the
    file's size."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - n))
            b = f.read(n)
    except OSError:
        return b"", 0
    if size > n:
        i = b.find(b"\n")
        b = b[i + 1:] if i >= 0 else b""
    return b, size


def copy_small(src, dst):
    with open(src, "rb") as f:
        data = f.read()
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
        durable._sync(fd)
    finally:
        os.close(fd)
    st = os.stat(src)
    os.utime(dst, (st.st_atime, st.st_mtime))


def prune_dir():
    return _N().p("prune")


def build_pruned(j, why, by, st, size_before, newd):
    """The pruned copy of J's folder in NEWD (same file system): the kept files, the log's tail, pruned.json."""
    d = j["dir"]
    os.mkdir(newd, 0o700)
    names = sorted(os.listdir(d))
    removed = []
    for n in names:
        src = os.path.join(d, n)
        if n in KEEP and os.path.isfile(src) and not os.path.islink(src):
            if n == "events.log" and os.path.getsize(src) > EVENTS_CAP:
                b, _ = tail_bytes(src, EVENTS_CAP)
                durable.write_durable(os.path.join(newd, n), b"== mu: earlier events removed at the clean-up ==\n" + b)
            else:
                copy_small(src, os.path.join(newd, n))
        elif n != "log":
            removed.append(n)
    tail, size = tail_bytes(os.path.join(d, "log"), st["log_tail_kb"] << 10)
    head = ("== μ: the job's folder was cleaned up (%s): its outputs and working copy were removed; this is the last "
            "%d bytes of its log of %d bytes ==\n" % (why, len(tail), size)).encode("utf-8") if size > len(tail) else b""
    durable.write_durable(os.path.join(newd, "log"), head + tail)
    f = fetched(d)
    rec = {"schema": 1, "job_id": j["id"], "at": proto.iso(), "why": why, "by": by, "bytes_before": size_before,
           "removed": removed, "log": {"size_before": size, "kept": len(tail), "tail": size > len(tail)},
           "fetched": None if not f else {"count": f.get("count"), "last_at": f.get("last_at")}}
    rec["bytes_freed"] = max(0, size_before - tree_bytes(newd) - 4096)
    durable.write_record(os.path.join(newd, "pruned.json"), rec)
    durable.sync_dir(newd)
    return rec


def prune_in_place(j, why, by, st, size_before):
    """Fault prune-in-place: what the swap prevents (a crash leaves a folder whose result names outputs that are
    gone, and no pruned.json)."""
    d = j["dir"]
    for n in sorted(os.listdir(d), key=lambda x: (x != "work", x)):   # the bulk first, as anyone would
        if n in KEEP or n == "log":
            continue
        path = os.path.join(d, n)
        shutil.rmtree(path) if os.path.isdir(path) and not os.path.islink(path) else os.unlink(path)
        if fault("prune-crash-before-swap"):
            os._exit(9)
    tail, size = tail_bytes(os.path.join(d, "log"), st["log_tail_kb"] << 10)
    durable.write_durable(os.path.join(d, "log"), tail)
    rec = {"schema": 1, "job_id": j["id"], "at": proto.iso(), "why": why, "by": by, "bytes_before": size_before,
           "bytes_freed": max(0, size_before - tree_bytes(d)), "removed": [], "log": {"size_before": size,
                                                                                    "kept": len(tail)}}
    durable.write_record(os.path.join(d, "pruned.json"), rec)
    return rec, None


def prune_one(c, why, by, st, sizes):
    """Prune one candidate, under the queue lock. -> (pruned.json, garbage folder) or (None, reason skipped)."""
    N = _N()
    jid = c["j"]["id"]
    d = N.job_dir(jid)
    with N.Lock(wait=N.LOCK_WAIT_S):
        try:
            fd = os.open(os.path.join(d, "job.json"), os.O_RDONLY)
        except OSError:
            return None, "gone"
        try:
            try:
                if not fault("gc-ignores-fetch-lock"):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return None, "its outputs are being fetched"
            j = N.load_job(jid)   # again, under the lock
            if (not j["result"] and not fault("gc-ignores-unfinished")) or pruned(d) or (
                    kept(d) and not fault("gc-ignores-keep")):
                return None, "changed"
            size = sizes.get(jid)
            if not isinstance(size, int):
                size = tree_bytes(d)
            if fault("prune-in-place"):
                return prune_in_place(j, why, by, st, size)
            os.makedirs(prune_dir(), mode=0o700, exist_ok=True)
            newd = os.path.join(prune_dir(), "%s.%d" % (jid, os.getpid()))
            if os.path.lexists(newd):
                shutil.rmtree(newd, ignore_errors=True)
            rec = build_pruned(j, why, by, st, size, newd)
            if fault("prune-crash-before-swap"):
                os._exit(9)
            err = exchange(newd, d)
            if err:
                shutil.rmtree(newd, ignore_errors=True)
                return None, err
            durable.sync_dir(N.p("jobs"))
            durable.sync_dir(prune_dir())
            if fault("prune-crash-after-swap"):
                os._exit(9)
            old = newd + ".old"
            os.rename(newd, old)   # the old folder now holds the whole job: it goes
            N.event(d, "pruned by %s: %s; %s freed" % (by, why, proto.human_size(rec["bytes_freed"])))
        finally:
            os.close(fd)
    N.journal("gc: pruned id=%s by=%s (%s): %d bytes freed; kept job.json, state, result, the log's last %d bytes" % (
        jid, by, why, rec["bytes_freed"], rec["log"]["kept"]))
    return rec, old


def remove_later(paths):
    """Delete the old folders without waiting: a detached rm at the lowest CPU and I/O priority."""
    paths = [x for x in paths if x and os.path.isdir(x)]
    if not paths:
        return
    cmd = ["rm", "-rf", "--"] + paths
    if shutil.which("ionice"):
        cmd = ["ionice", "-c3"] + cmd
    if shutil.which("nice"):
        cmd = ["nice", "-n", "19"] + cmd
    try:
        subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, close_fds=True)
    except OSError:
        for x in paths:
            shutil.rmtree(x, ignore_errors=True)


def leftovers():
    """What a crash left in /srv/mu/prune/: an unswapped pruned copy (`<id>.<pid>`, made before the swap: garbage)
    or an old folder (`.old`, or `<id>.<pid>` after a swap: the job's name already holds the pruned copy). Either
    way nothing under that folder is the job's record any more; deleted (only by the holder of gc.lock)."""
    try:
        return [os.path.join(prune_dir(), n) for n in os.listdir(prune_dir())]
    except OSError:
        return []


# ------------------------------------------------------------------------------------------------ the clean-up
def gc_lock(wait):
    fd = os.open(_N().p("gc.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    end = time.time() + wait
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.time() >= end:
                os.close(fd)
                return None
            time.sleep(0.2)


def read_cache():
    return durable.read_record(_N().p("gc.json")) or {}


def write_cache(c):
    try:
        durable.write_record(_N().p("gc.json"), c)
    except OSError:
        pass


def run(by="runner", dry=False, node=None, now=None, budget_s=None):
    """One clean-up: the jobs due by age, then disk pressure. Takes gc.lock (one clean-up at a time) and, unless DRY,
    the start lock (never while jobs are being started, never while an update moves the default). -> the answer."""
    N = _N()
    node = N.node_info() if node is None else node
    st = settings(node)
    now = time.time() if now is None else now
    if fault("gc-clock-ahead"):   # a test injector (e2e G01): the clean-up judges ages 40 days from now
        now += 40 * 86400
    t0 = time.time()
    fd = gc_lock(0 if by == "runner" else 30)
    if fd is None:
        return {"ok": False, "error": "busy", "message": "another clean-up is running"}
    started_lock = False
    try:
        cache = read_cache()
        if not dry:
            remove_later(leftovers())
            if not fault("gc-no-start-lock"):
                end = time.time() + (0 if by == "runner" else 30)
                while not N.take_start_lock():
                    if time.time() >= end:
                        return {"ok": False, "error": "busy", "message": "an update or a job start holds the start "
                                "lock (%s)" % N.start_lock_path()}
                    time.sleep(0.5)
                started_lock = True
        jobs = N.scan_jobs()
        cands, skipped = candidates(jobs, st, now)
        sizes = dict(cache.get("sizes") or {})
        ids = {j["id"] for j in jobs}
        for c in cands:   # a finished job's size does not change: walked once, then cached
            if not isinstance(sizes.get(c["j"]["id"]), int):
                sizes[c["j"]["id"]] = tree_bytes(c["j"]["dir"])
        sizes = {k: v for k, v in sizes.items() if k in ids}
        eff, sp = space(node, cache)
        line = sp["min_free_gb"] + st["pressure_extra_gb"] if isinstance(sp.get("min_free_gb"), (int, float)) else None
        plan = [(c, "%s (after %s days)" % (c["why_due"], st["after_fetch_days"] if c["fetched_at"] is not None else
                                            st["unfetched_days"])) for c in cands if c["due"]]
        chosen = {c["j"]["id"] for c, _ in plan}
        freed_plan = sum(sizes.get(c["j"]["id"], 0) for c, _ in plan)
        pressure = eff is not None and line is not None and (eff + freed_plan / float(1 << 30)) < line
        if pressure:
            gain = freed_plan
            for c in pressure_order([c for c in cands if c["j"]["id"] not in chosen], st, now):
                if eff + gain / float(1 << 30) >= line and not fault("gc-pressure-all"):
                    break
                plan.append((c, "disk pressure: %.0f GB free where the jobs live, under min_free_gb %s + %s GB (%s)"
                             % (eff, sp["min_free_gb"], st["pressure_extra_gb"], c["why_due"])))
                gain += sizes.get(c["j"]["id"], 0)
        done, garbage, skip = [], [], []
        if not st["enabled"] and by == "runner":
            plan = []
        for c, why in plan:
            jid = c["j"]["id"]
            if dry:
                done.append({"job_id": jid, "why": why, "bytes": sizes.get(jid, 0), "dry_run": True})
                continue
            if budget_s is not None and time.time() - t0 > budget_s:
                skip.append({"job_id": jid, "why": "time budget of this pass used up: next pass"})
                continue
            if started_lock:
                N.refresh_start_lock()
            try:
                rec, old = prune_one(c, why, by, st, sizes)
            except Exception as e:  # noqa: one job's error never stops the others
                N.journal("gc: id=%s: %s: %s" % (jid, type(e).__name__, e))
                skip.append({"job_id": jid, "why": "%s: %s" % (type(e).__name__, e)})
                continue
            if rec is None:
                skip.append({"job_id": jid, "why": old})
                if old and old.startswith(("no atomic", "the swap failed", "no renameat2")):
                    break   # this node cannot swap: nothing more is tried
                continue
            done.append({"job_id": jid, "why": why, "bytes": rec["bytes_freed"]})
            garbage.append(old)
            sizes.pop(jid, None)
        if started_lock:
            N.drop_start_lock()
            started_lock = False
        remove_later(garbage)
        freed = sum(x["bytes"] for x in done)
        cands2 = [c for c in cands if c["j"]["id"] not in {x["job_id"] for x in done}] if not dry else cands
        finished = [j for j in jobs if j["result"]]
        out = {"ok": True, "by": by, "dry_run": dry, "at": proto.iso(now), "pruned": done, "skipped": skip,
               "freed_bytes": freed, "pressure": pressure, "line_gb": line, "space": sp, "free_gb_effective":
               None if eff is None else round(eff + (freed / float(1 << 30) if not dry else 0), 1),
               "settings": st, "not_candidates": skipped,
               "finished": {"jobs": len(finished), "bytes": sum(sizes.get(j["id"], 0) for j in finished
                                                                 if not pruned(j["dir"]))},
               "due": {"jobs": len([c for c in cands2 if c["due"]]),
                       "bytes": sum(sizes.get(c["j"]["id"], 0) for c in cands2 if c["due"])},
               "prunable": {"jobs": len(cands2), "bytes": sum(sizes.get(c["j"]["id"], 0) for c in cands2)},
               "took_s": round(time.time() - t0, 2)}
        if not st["enabled"]:
            out["note"] = "the runner's clean-up is off (node.json gc.enabled false)"
        cache.update(sizes=sizes, as_of=proto.iso(now), finished=out["finished"], due=out["due"],
                     prunable=out["prunable"], space=sp, line_gb=line)
        if not dry:
            cache["last_run"] = {"at": out["at"], "by": by, "pruned": len(done), "freed_bytes": freed,
                                 "pressure": pressure, "skipped": len(skip)}
        write_cache(cache)
        if done and not dry:
            N.journal("gc: %d job folder(s) pruned by %s, %d bytes freed%s" % (len(done), by, freed, " (disk pressure)"
                                                                               if pressure else ""))
        out["more"] = bool(skip and any("next pass" in x["why"] for x in skip))
        return out
    finally:
        if started_lock:
            N.drop_start_lock()
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def tick(stop=None, now=None):
    """From the runner's loop, between two passes (so never during a job start): at most once every gc.every_s
    (one minute later when a pass ran out of its time budget); cheap when nothing is due (no folder is walked twice:
    sizes are cached in gc.json)."""
    now = time.time() if now is None else now
    if stop and stop[0]:
        return None
    if not fault("gc-hourly-ignored"):
        if not LAST["at"]:   # once per runner process: the last run's time and the setting (no read on every pass)
            LAST["at"] = proto.epoch_of((read_cache().get("last_run") or {}).get("at")) or 1.0
            LAST["every"] = settings()["every_s"]
        if now < LAST["again_at"] or (now - LAST["at"] < LAST.get("every", DEFAULTS["every_s"]) and
                                      not LAST["again_at"]):
            return None
    out = run("runner", budget_s=TICK_BUDGET_S)
    LAST["at"] = now
    LAST["every"] = (out.get("settings") or {}).get("every_s") or DEFAULTS["every_s"]
    LAST["again_at"] = now + 60 if out.get("more") or not out.get("ok") else 0.0   # busy or unfinished: a minute
    return out


def summary():
    """hello's `gc`: the last clean-up and the sizes it measured (gc.json; no folder is walked here)."""
    c = read_cache()
    st = settings()
    return {"enabled": st["enabled"], "settings": st, "as_of": c.get("as_of"), "last_run": c.get("last_run"),
            "finished": c.get("finished"), "due": c.get("due"), "prunable": c.get("prunable"),
            "line_gb": c.get("line_gb")}


# ------------------------------------------------------------------------------------------------ verbs
def verb_gc(key, args):
    """`mu compute gc NODE [--dry-run]`: a clean-up now, by an owner key only (it deletes other workstations' job
    folders); --dry-run lists what it would prune and frees nothing (any owner key too)."""
    N = _N()
    own = N.owners()
    dry = args.get("dry") == "1"
    if key not in own and not fault("gc-any-key"):
        N.journal("gc: refused by=%s not-allowed (owner keys only)" % key)
        raise N.NodeError(proto.NODE_NOPERM, "not-allowed", "ask the owner (%s) to run it" % ", ".join(own) if own else
                          "the node has no owner key", message="mu compute gc: only an owner key may clean up the "
                          "node's job folders (they are every workstation's)")
    out = run("%s (mu compute gc)" % key, dry=dry)
    if not out.get("ok"):
        raise N.NodeError(proto.NODE_UNAVAILABLE, "busy", "retry in a minute", message="clean-up not run: %s" %
                          out.get("message"))
    out.pop("ok", None)
    return dict({"ok": True}, **out)


def verb_keep(key, args):
    """`mu keep JOB [--off]`: exempt a job from the clean-up (keep.json in its folder), or end that. The job's own
    workstation, or an owner key with --others (the hold rule, scheduler.md §20.4)."""
    N = _N()
    jid = args.get("id")
    if not jid:
        raise N.NodeError(proto.NODE_USAGE, "bad-argument", "keep id=<job-id> [off=1]")
    off = args.get("off") == "1"
    from mu import sched
    own = N.owners()
    with N.Lock(wait=N.LOCK_WAIT_S):
        j = N.load_job(jid)
        d = j["dir"]
        if not fault("keep-any-key"):
            why = sched.authorise("hold", {"key": j["job"].get("key"), "project_id": j["job"].get("project_id")}, key,
                                  own, args.get("from"), args.get("others") == "1")
            if why:
                N.journal("gc: refused keep%s id=%s by=%s%s%s %s (%s)" % (" off" if off else "", jid, key,
                                                                         " from=%s" % args["from"] if args.get("from")
                                                                         else "", " others=1" if args.get("others") ==
                                                                         "1" else "", why[0], why[1]))
                raise N.NodeError(proto.NODE_NOPERM if why[0] == "not-allowed" else proto.NODE_USAGE, why[0],
                                  "only if the owner asked: the same command with --others" if why[0] ==
                                  "other-project" else "ask the owner", message="keep %s refused: %s" % (jid, why[1]))
        pr, k = pruned(d), kept(d)
        if pr:
            return dict({"ok": True, "exit": 0, "job_id": jid, "changed": False, "kept": None,
                         "note": "already cleaned up on %s: nothing left to keep" % pr.get("at")}, **public(d))
        if off:
            if k:
                os.unlink(os.path.join(d, "keep.json"))
                durable.sync_dir(d)
                N.event(d, "keep ended by %s" % key)
        elif not k:
            durable.write_record(os.path.join(d, "keep.json"), {"schema": 1, "job_id": jid, "by": key,
                                                                "at": proto.iso()})
            N.event(d, "kept by %s: exempt from the clean-up" % key)
        changed = bool(k) == off
        N.journal("gc: keep%s id=%s by=%s%s %s" % (" off" if off else "", jid, key, " (owner)" if key in own else "",
                                                   "ok" if changed else "unchanged"))
        out = {"ok": True, "exit": 0, "job_id": jid, "changed": changed,
               "state": (j["result"] or {}).get("state") or (j["state"] or {}).get("state", "queued")}
        out.update(public(d))
        return out


VERBS = {"gc": verb_gc, "keep": verb_keep}
