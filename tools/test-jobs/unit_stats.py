"""Unit checks of the dashboard's phase 1b node side (src/mu/nodestats.py and its hunks in src/mu/node.py;
docs/design/dashboard.md §7.1.2). Standalone: no systemd, no ssh, no real /proc or cgroup (canned files). Prints
"ok    name" / "FAIL  name"; a check guarding against a fault is tagged "(fault F is on: this must FAIL)" when F is on, so
tools/test-jobs/run.sh counts a must-fail only on the check it targets. Exits with the number of failures.

    python3 -I -S tools/test-jobs/unit_stats.py [SECTION ...]     sections: rev host line verbs sampler runtime cost
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(REPO, "src")
sys.path.insert(0, SRC)
from mu import durable  # noqa: E402

FAILS = [0]
BASE = tempfile.mkdtemp(prefix="mu stats unit.")
os.environ.setdefault("MU_HOME", os.path.join(BASE, "home"))
from mu import node, nodestats  # noqa: E402


def check(name, cond, detail=""):
    if cond:
        print("ok    %s" % name)
    else:
        FAILS[0] += 1
        print("FAIL  %s" % name)
        if detail:
            print("\n".join("        | " + x for x in str(detail).splitlines()[:30]))


def tag(name, f):
    return name + (" (fault %s is on: this must FAIL)" % f if durable.fault(f) else "")


def fresh(name):
    root = os.path.join(BASE, name, "srv")
    os.makedirs(os.path.join(root, "jobs"))
    durable.write_record(os.path.join(root, "node.json"), {"name": "n1", "min_free_gb": 0})
    node.ROOT = root
    return root


def mkjob(jid, state, **st):
    d = node.job_dir(jid)
    os.makedirs(d, exist_ok=True)
    job = {"schema": 1, "job_id": jid, "project_id": "p-0123456789ab", "accepted_ns": 1}
    s = dict({"state": state, "attempt": 1}, **st)
    durable.write_record(os.path.join(d, "job.json"), job)
    durable.write_record(os.path.join(d, "state.json"), s)
    return {"id": jid, "dir": d, "job": job, "state": s, "result": None}


def serve(*argv, env=None, timeout=20):
    """`mu node serve --key=k ARGV` in a child process with this test's root -> (rc, stdout bytes, seconds)."""
    e = dict(os.environ, MU_NODE_ROOT=node.ROOT, **(env or {}))
    code = "import sys; sys.path.insert(0, %r); from mu import node; sys.exit(node.main(sys.argv[1:]))" % SRC
    t0 = time.monotonic()
    try:
        r = subprocess.run([sys.executable, "-I", "-S", "-c", code, "serve", "--key=k"] + list(argv), env=e,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        return r.returncode, r.stdout, time.monotonic() - t0
    except subprocess.TimeoutExpired as x:
        return None, x.stdout or b"", time.monotonic() - t0


def audit_lines():
    try:
        with open(os.path.join(node.ROOT, "audit.log"), encoding="utf-8") as f:
            return f.read().splitlines()
    except OSError:
        return []


# ------------------------------------------------------------------------------------------------ rev
def t_rev():
    fresh("rev")
    durable.write_record(node.p("queue.json"), {"schema": 1, "rev": 7, "order": []})
    a = mkjob("20261115-100000-aaaaaa", "running", grant={"class": "normal", "cpus": [0, 1], "cores": 2})
    b = mkjob("20261115-100100-bbbbbb", "queued")
    nodestats.note_pass([a, b])
    r0 = nodestats.read_rev()
    check("rev: a missing rev file starts at the order's rev + 1", r0 == 8, r0)
    nodestats.note_pass([a, b])
    check("rev: a pass with nothing changed keeps the rev", nodestats.read_rev() == r0, nodestats.read_rev())
    b["state"] = dict(b["state"], state="held", held={"by": "k"})
    nodestats.note_pass([a, b])
    r1 = nodestats.read_rev()
    check(tag("rev: a job held moves the rev by one within the pass", "rev-frozen"), r1 == r0 + 1, (r0, r1))
    a["state"] = dict(a["state"], pause={"phase": "frozen", "since_mono": 1.0})
    nodestats.note_pass([a, b])
    r2 = nodestats.read_rev()
    check(tag("rev: a job paused (pause.phase) moves the rev", "rev-frozen"), r2 == r1 + 1, (r1, r2))
    a["state"] = dict(a["state"], pause={"phase": "frozen", "since_mono": 1.0, "resume_asked": {"by": "k"}})
    nodestats.note_pass([a, b])
    check(tag("rev: a resume asked moves the rev", "rev-frozen"), nodestats.read_rev() == r2 + 1, nodestats.read_rev())
    check("rev: LAST_RUNNING holds the running job only", [x[0] for x in nodestats.LAST_RUNNING] == [a["id"]],
          nodestats.LAST_RUNNING)
    check("rev: the queue counts: running 1, held 1, paused 1",
          nodestats.QUEUE == {"running": 1, "queued": 0, "held": 1, "paused": 1, "express": 0}, nodestats.QUEUE)
    rec = json.loads(open(node.p("metrics", "rev")).read())
    check("rev: the file holds rev and fp", set(rec) == {"rev", "fp"}, rec)
    out = node.verb_list("k", {})
    check(tag("rev: list answers the state rev, not the order's", "rev-frozen"), out["rev"] == nodestats.read_rev()
          and out["rev"] != 7, out["rev"])
    nodestats.note_pass([{"id": "x"}])   # broken input: never raises
    check("rev: note_pass never raises", True)


# ------------------------------------------------------------------------------------------------ host
PROC_STAT = ("cpu  {u} 0 {s} {i} 0 0 0 0 0 0\ncpu0 {u0} 0 {s0} {i0} 0 0 0 0 0 0\ncpu1 {u1} 0 {s1} {i1} 0 0 0 0 0 0\n"
             "intr 0\n")


def canned_proc(d, k):
    os.makedirs(os.path.join(d, "pressure"), exist_ok=True)
    vals = dict(u=1000 * k, s=0, i=1000 * k, u0=900 * k, s0=0, i0=100 * k, u1=100 * k, s1=0, i1=900 * k)
    open(os.path.join(d, "stat"), "w").write(PROC_STAT.format(**vals))
    open(os.path.join(d, "meminfo"), "w").write("MemTotal:       24000000 kB\nMemFree:  100 kB\n"
                                                "MemAvailable:    8000000 kB\nCached:          2000000 kB\n"
                                                "Shmem:            400000 kB\n")
    open(os.path.join(d, "loadavg"), "w").write("18.20 17.90 15.00 3/900 1234\n")
    open(os.path.join(d, "uptime"), "w").write("86400.5 100.0\n")
    open(os.path.join(d, "pressure", "cpu"), "w").write("some avg10=41.20 avg60=30.00 avg300=1.00 total=1\n")
    open(os.path.join(d, "pressure", "memory"), "w").write("some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
                                                           "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")


def t_host():
    fresh("host")
    d = os.path.join(BASE, "host", "proc")
    os.environ["MU_NODE_PROC"] = d
    canned_proc(d, 1)
    nodestats._HOST_PREV.clear()
    nodestats.host_sample()
    canned_proc(d, 2)
    h = nodestats.host_sample()
    check("host: cpu_pct between two samples (50 %)", h["cpu_pct"] == 50.0, h["cpu_pct"])
    check("host: per core (90 %, 10 %)", h["per_core"] == [90.0, 10.0], h["per_core"])
    check("host: meminfo in bytes", h["mem"] == {"total": 24000000 * 1024, "avail": 8000000 * 1024,
                                                 "cached": 2000000 * 1024, "shmem": 400000 * 1024}, h["mem"])
    check("host: load, psi, uptime", h["load"] == [18.2, 17.9, 15.0] and h["psi"] == {"cpu_some10": 41.2,
                                                                                    "mem_some10": 0.0}
          and h["uptime_s"] == 86400, h)
    os.remove(os.path.join(d, "pressure", "cpu"))
    check("host: no PSI file -> null, not absent", nodestats.host_sample()["psi"]["cpu_some10"] is None)
    # stats from now.json: age and stale
    nodestats.write_now([])
    a = nodestats.stats_answer()
    check(tag("stats: a fresh now.json is not stale and has its age", "no-age"),
          a.get("stale") is False and isinstance(a.get("age_s"), float) and a["age_s"] < 5, a.get("age_s"))
    check(tag("stats: host.mem.avail as in the fixture", "rename-field-node"),
          "avail" in ((a.get("host") or {}).get("mem") or {}), (a.get("host") or {}).get("mem"))
    nw = json.load(open(node.p("metrics", "now.json")))
    nw["t"] -= 20
    open(node.p("metrics", "now.json"), "w").write(json.dumps(nw))
    a = nodestats.stats_answer()
    check(tag("stats: now.json 20 s old (the runner stopped) -> stale", "no-age"), a.get("stale") is True, a.get("stale"))
    del os.environ["MU_NODE_PROC"]


# ------------------------------------------------------------------------------------------------ line
def t_line():
    fresh("line")
    j = mkjob("20261115-100000-cccccc", "running", grant={"class": "normal", "cores": 4, "cpus": [0, 1, 2, 3]})
    prev = {"cpu_s": 100.0, "mono": 1000.0}
    u = {"cpu_s": 120.0, "mono": 1005.0, "anon": 5, "file": 6, "shmem": 7, "mem_current": 18, "mem_peak_bytes": 20,
         "mem_max": None, "pids": 3, "psi_mem": 0.5}
    row = nodestats.job_line(j, j["state"], u, prev)
    check("line: cpu is cores between the two samples (4.0)", row["cpu_cores"] == 4.0, row)
    check("line: the running row carries grant_cores, class, paused false",
          row["grant_cores"] == 4 and row["class"] == "normal" and row["paused"] is False, row)
    st = dict(j["state"], pause={"phase": "frozen", "since_mono": 1.0})
    row2 = nodestats.job_line(j, st, dict(u, cpu_s=120.0, mono=1010.0), u)
    lines = [json.loads(x) for x in open(os.path.join(j["dir"], "metrics.jsonl"))]
    check("line: two lines appended, each with the contract's keys",
          len(lines) == 2 and all({"t", "cpu", "anon", "file", "shmem", "cur", "peak", "max", "pids", "psi_mem"}
                                  <= set(x) for x in lines), lines)
    check("line: memory.max 'max' is null", lines[0]["max"] is None, lines[0])
    check("line: a sample while running has no paused key", "paused" not in lines[0], lines[0])
    check(tag("line: a sample taken while paused carries \"paused\": 1 (from state.json)", "paused-line-missing"),
          lines[1].get("paused") == 1, lines[1])
    check("line: a frozen job's cpu is 0 and its row says paused", lines[1]["cpu"] == 0.0 and row2["paused"] is True,
          (lines[1], row2))


# ------------------------------------------------------------------------------------------------ verbs
def t_verbs():
    fresh("verbs")
    j = mkjob("20261115-100000-dddddd", "running")
    data = b"".join(b'{"t":%d,"cpu":1.0}\n' % i for i in range(100)) + b'{"t":100'
    open(os.path.join(j["dir"], "metrics.jsonl"), "wb").write(data)
    nodestats.write_now([])
    n0 = len(audit_lines())
    rc, out, _ = serve("stats")
    a = json.loads(out)
    check("verbs: stats answers JSON, exit 0", rc == 0 and a.get("ok") is True, (rc, out[:200]))
    rc, out, _ = serve("metrics", "id=" + j["id"], "offset=0")
    whole = out
    rc2, out2, _ = serve("metrics", "id=" + j["id"], "offset=%d" % 500)
    check("verbs: metrics from 0 is the file", rc == 0 and whole == data, (rc, len(whole), len(data)))
    check(tag("verbs: metrics from an offset is the file from there (pieces join byte for byte)", "metrics-from-zero"),
          rc2 == 0 and whole[:500] + out2 == data, (len(out2), len(data) - 500))
    rc, out, _ = serve("metrics", "id=" + j["id"], "offset=10", "max=20")
    check("verbs: metrics max= caps the bytes", out == data[10:30], out)
    added = audit_lines()[n0:]
    check(tag("verbs: stats and metrics add no line to audit.log", "journal-each-call"), added == [], added)
    rc, out, _ = serve("metrics", "id=20261115-100000-zzzzzz", "offset=0")
    added = audit_lines()[n0:]
    check("verbs: a refused metrics (no such job) is journalled", rc == 66 and len(added) == 1 and "refused" in added[0]
          and "no-such-job" in added[0], (rc, added))
    n1 = len(audit_lines())
    rc, out, secs = serve("watch", "interval=2", "max=3", timeout=12)
    lines = [json.loads(x) for x in out.splitlines() if x.strip()]
    added = audit_lines()[n1:]
    check(tag("verbs: watch ends at max (3 s)", "no-cap"), rc == 0 and secs < 8, (rc, secs))
    check("verbs: watch sends stats lines", len(lines) >= 1 and all(x.get("ok") and "rev" in x for x in lines), lines)
    check(tag("verbs: watch adds exactly two audit lines (open, close)", "no-cap"),
          len(added) == 2 and "verb=watch" in added[0] and "closed after" in added[1], added)
    rc, out, _ = serve("watch", "interval=1")
    check("verbs: watch interval=1 is refused (2..60)", rc == 64 and b"bad-argument" in out, (rc, out))
    rc, out, _ = serve("watch", "interval=600")
    check("verbs: watch interval=600 is refused by ARG_RULES", rc == 64, (rc, out))
    # a client that disappears: the session ends at the latest with the cap
    e = dict(os.environ, MU_NODE_ROOT=node.ROOT)
    code = "import sys; sys.path.insert(0, %r); from mu import node; sys.exit(node.main(sys.argv[1:]))" % SRC
    pr = subprocess.Popen([sys.executable, "-I", "-S", "-c", code, "serve", "--key=k", "watch", "interval=2", "max=5"],
                          env=e, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    pr.stdout.readline()
    pr.stdout.close()                 # the client goes away
    try:
        pr.wait(timeout=9)
        gone = True
    except subprocess.TimeoutExpired:
        pr.kill()
        gone = False
    check(tag("verbs: a watch whose client disappeared leaves no session after the cap (5 s)", "no-cap"), gone)
    h = node.hello_answer("k", node.node_info())
    check("verbs: hello.features names stats, watch and metrics",
          {"stats", "watch", "metrics"} <= set(h["features"]) and "pause" not in h["features"], h["features"])


# ------------------------------------------------------------------------------------------------ sampler
def t_sampler():
    fresh("sampler")
    jobs = [mkjob("20261115-1000%02d-eeeeee" % i, "running", cgroup="/nonexistent/%d" % i) for i in range(3)]
    for i in range(40):
        d = node.job_dir("20261114-1000%02d-ffffff" % i)
        os.makedirs(d)
        durable.write_record(os.path.join(d, "job.json"), {"schema": 1, "job_id": os.path.basename(d)})
    nodestats.note_pass(jobs[:1])
    calls = [0]
    real = node.scan_jobs

    def counting():
        calls[0] += 1
        return real()
    node.scan_jobs = counting
    try:
        node.sample()
    finally:
        node.scan_jobs = real
    check(tag("sampler: a pass reads LAST_RUNNING, never every job folder", "sampler-scan-all"), calls[0] == 0, calls[0])
    nw = json.load(open(node.p("metrics", "now.json")))
    check("sampler: now.json is written each pass, with the queue counts",
          nw.get("queue", {}).get("running") == 1 and "host" in nw and isinstance(nw.get("t"), float), nw.get("queue"))


# ------------------------------------------------------------------------------------------------ run time
def t_runtime():
    fresh("runtime")
    st = {"paused_total_s": 600}
    check("runtime: run_seconds = wall - paused_total_s", nodestats.run_seconds(st, 1800) == 1200)
    now = 5000.0
    st2 = {"paused_total_s": 600, "pause": {"phase": "frozen", "since_mono": now - 300, "boot_id": node.boot_id()}}
    check("runtime: an open pause counts too", nodestats.run_seconds(st2, 1800, now_mono=now) == 900,
          nodestats.run_seconds(st2, 1800, now_mono=now))
    st3 = {"pause": {"phase": "frozen", "since_mono": now - 300, "boot_id": "another-boot"}}
    check("runtime: a pause of another boot is not open", nodestats.run_seconds(st3, 1800, now_mono=now) == 1800)
    check("runtime: never below 0", nodestats.run_seconds({"paused_total_s": 9999}, 10) == 0)
    ca = nodestats.cores_avg(2400.0, st, 1800)
    check(tag("runtime: cores_avg = cpu_s / run time (2400 cpu-s over 1200 s run = 2.0)", "cores-avg-by-wall"),
          ca == 2.0, ca)
    j = mkjob("20261115-100000-gggggg", "running", paused_total_s=600)
    open(os.path.join(j["dir"], "usage.json"), "w").write(json.dumps({"cpu_s": 2400.0, "mem_peak_bytes": 5, "t": "x"}))
    u = node.sampled_usage(j, 1800)
    check(tag("runtime: a runner-written result's cores_avg by run time", "cores-avg-by-wall"),
          u and u["cores_avg"] == 2.0 and u["source"] == "runner-sampled", u)
    check(tag("runtime: a runner-written result reads usage.json, not the gone cgroup", "read-cgroup-after-exit"),
          u and u.get("cpu_s") == 2400.0 and u.get("mem_peak_bytes") == 5, u)


# ------------------------------------------------------------------------------------------------ cost (D03, unit)
def t_cost():
    fresh("cost")
    for i in range(2000):
        d = node.job_dir("20261101-%06d-hhhhhh" % i)
        os.makedirs(d)
        for n in ("job.json", "state.json", "result.json"):
            open(os.path.join(d, n), "w").write('{"x": 1}')
    nodestats.write_now([])
    calls = [0]
    real = node.scan_jobs

    def counting():
        calls[0] += 1
        return real()
    node.scan_jobs = counting
    try:
        t0 = time.perf_counter()
        a = nodestats.stats_answer()
        dt = time.perf_counter() - t0
    finally:
        node.scan_jobs = real
    check(tag("cost: stats never calls scan_jobs()", "stats-scan"), calls[0] == 0, calls[0])
    check("cost: stats with 2,000 finished job folders answers in under 100 ms (in process)", dt < 0.1 and a["ok"],
          "%.1f ms" % (dt * 1000))


SECTIONS = {"rev": t_rev, "host": t_host, "line": t_line, "verbs": t_verbs, "sampler": t_sampler,
            "runtime": t_runtime, "cost": t_cost}

if __name__ == "__main__":
    try:
        for s in sys.argv[1:] or list(SECTIONS):
            SECTIONS[s]()
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    sys.exit(min(FAILS[0], 100))
