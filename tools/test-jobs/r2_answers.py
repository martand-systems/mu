"""Scheduler release 2's answers as the real node code gives them (src/mu/node.py, pausectl.py, nodestats.py), against
the stubs of systemd that unit_sched.py's sched-pause section uses: a frozen lender with a borrower on its cores, a
lender whose resume waits for its borrower (resuming), a held job, an urgent and a low job queued, a finished job that
was paused. What the node sends over `mu node serve`, line for line.

The dashboard's golden fixtures (tools/test-dashboard/fixtures, written by src/mu/dashfake.py) stand in for these
answers in the page's tests; unit.py's section r2-shapes checks that each fixture and the real answer have the same
keys, types, states and wording both ways (faults: drop-field and list-keeps-pauses on the node's side,
fixture-drift, a test fault of this file, on the fixtures' side), and
`python3 -I -S tools/test-jobs/r2_answers.py DIR` writes the real answers to DIR (the integrator's record:
tools/test-dashboard/fixtures/real-r2/).
"""
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "src"))
sys.path.insert(0, HERE)
from mu import durable, sched  # noqa: E402

G = 1 << 30
MI = 1 << 20
KEY = "avi-mac"
PROJ = "p1-0123456789ab"
IDS = {"lender": "20261114-120102-k3f9qa", "borrower": "20261114-142955-2icaxr", "lender2": "20261114-121500-r7mm2x",
       "borrower2": "20261114-143010-b8b8b8", "held": "20261114-130200-h3ld00", "urgent": "20261114-143100-urg3nt",
       "low": "20261114-143200-l0wl0w", "done": "20261114-110000-d0n3d0",
       "filler": "20261114-121600-f1ll3r"}


def answers(base):
    """-> {name: answer} from the real node code (each answer the last line `mu node serve` printed)."""
    import unit_sched
    stubs = os.path.join(base, "bin")
    os.makedirs(stubs, exist_ok=True)
    for name, text in (("systemctl", unit_sched.PAUSE_SYSTEMCTL_STUB), ("systemd-run", unit_sched.PAUSE_RUN_STUB)):
        with open(os.path.join(stubs, name), "w") as f:
            f.write(text)
        os.chmod(os.path.join(stubs, name), 0o755)
    os.environ.update(MU_SYSTEMCTL=os.path.join(stubs, "systemctl"), MU_SYSTEMD_RUN=os.path.join(stubs, "systemd-run"),
                      MU_WSL_HOST_DRIVE="/nonexistent", MU_NODE_OWNERS=os.path.join(base, "owners"),
                      MU_NODE_CHECK="1", MU_CGROUP_ROOT=os.path.join(base, "cg"))
    with open(os.path.join(base, "owners"), "w") as f:
        f.write(KEY + "\n")
    from mu import node, pausectl
    node.capacity = lambda nd=None: sched.capacity(nd if nd is not None else node.node_info(), list(range(8)), 64 * G)

    def prep(j):
        shutil.rmtree(os.path.join(j["dir"], "work"), ignore_errors=True)
        os.mkdir(os.path.join(j["dir"], "work"))
    node.prepare_work = prep
    for x in ("srv/jobs", "stub", "home/state"):
        os.makedirs(os.path.join(base, x))
    node.ROOT = os.path.join(base, "srv")
    os.environ["MU_HOME"] = os.path.join(base, "home")
    os.environ["STUB_DIR"] = stub = os.path.join(base, "stub")
    durable.write_record(os.path.join(node.ROOT, "node.json"), {
        "name": "office-pc", "min_free_gb": 0, "sched": {"cores": 8, "mem_bytes": 16 * G},
        "freezer": {"ok": True, "mem_lend": True, "boot_id": node.boot_id()}})
    node.STARTED_HERE.clear()
    seq = [0]

    def mkjob(jid, cores, mem, t, band=None, pausable=False):
        seq[0] += 1
        d = node.job_dir(jid)
        os.makedirs(d)
        job = {"schema": 1, "sched": 1, "job_id": jid, "project_id": PROJ, "mu_version": "v2026.11.11",
               "argv": ["python3", "sim.py"], "cwd": "", "outputs": [], "limits": {"cores": cores, "mem_bytes": mem,
                                                                                   "time_s": t},
               "rerun": True, "accepted_ns": seq[0], "key": KEY, "name": jid[-6:]}
        stt = {"state": "queued", "attempt": 1}
        if band:
            job["priority"] = stt["band"] = band
        if pausable:
            job["pausable"] = True
        durable.write_record(os.path.join(d, "job.json"), job)
        durable.write_record(os.path.join(d, "state.json"), stt)

    def memcur(jid, n):
        d = os.path.join(base, "cg", "user.slice", "test", "mu-job-%s.service" % jid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "memory.current"), "w") as f:
            f.write("%d\n" % n)

    def unit_end(jid):
        with open(os.path.join(stub, "units"), "a") as f:
            f.write("mu-job-%s.service inactive success\n" % jid)

    def serve(verb, *args):
        return unit_sched.serve(node, KEY, verb, *args)

    i = IDS
    # a job that was paused and has finished
    mkjob(i["done"], 2, 1 * G, 3600)
    node.step(first=True)
    node.step()
    memcur(i["done"], 200 * MI)
    serve("pause", "id=" + i["done"], "for=600", "from=" + PROJ)
    serve("resume", "id=" + i["done"], "from=" + PROJ)
    node.step()
    node.write_result(node.load_job(i["done"]), "succeeded", "shim", exit_code=0)   # what the shim does
    unit_end(i["done"])
    node.step()
    # two lenders: one frozen with a borrower, one whose resume waits for its borrower
    mkjob(i["lender"], 4, 6 * G, 21600, pausable=True)
    mkjob(i["lender2"], 2, 2 * G, 7200)
    mkjob(i["filler"], 2, 1 * G, 7200)   # the last 2 free cores: the second borrower can only borrow
    node.step()
    memcur(i["lender"], 5 * G)
    memcur(i["lender2"], 1 * G)
    out = {"pause": serve("pause", "id=" + i["lender"], "for=900", "from=" + PROJ)}
    serve("pause", "id=" + i["lender2"], "for=900", "from=" + PROJ)
    mkjob(i["borrower"], 4, 512 * MI, 600)
    mkjob(i["borrower2"], 2, 256 * MI, 300)
    node.step()
    out["resume"] = serve("resume", "id=" + i["lender2"], "from=" + PROJ)
    # queued: a held job, an urgent one (too big to borrow or to start), a low one
    mkjob(i["held"], 2, 1 * G, 3600)
    serve("hold", "id=" + i["held"], "from=" + PROJ)
    mkjob(i["urgent"], 8, 8 * G, 21600, band="urgent")
    mkjob(i["low"], 8, 1 * G, 3600, band="low")
    node.step()
    out.update({
        "hello": serve("hello", "protocol=1"),
        "list": serve("list", "limit=100"),
        "status-paused": serve("status", "id=" + i["lender"]),
        "status-resuming": serve("status", "id=" + i["lender2"]),
        "status-borrower": serve("status", "id=" + i["borrower"]),
        "status-finished": serve("status", "id=" + i["done"]),
        "queue": serve("queue", "from=" + PROJ),
    })
    _ = pausectl
    # this machine's details are not the node's: the folder and the hardware become the office PC's kind
    h = out["hello"]
    h["disk"]["where"] = "/srv/mu"
    h["hw"].update(cpu_model="13th Gen Intel(R) Core(TM) i7-13700", kernel="6.6.87.2-microsoft-standard-WSL2")
    return out


def through_mac(real):
    """The real answers through the Mac's server (src/mu/dashboard.py), as the page gets them: a fake node of the
    paused scenario that answers hello, list and status with the real node's answers (its stats and streams stay the
    scenario's). -> {"api-overview.json": ..., "api-job-paused.json": ..., "api-job-resuming.json": ...}"""
    from mu import dashboard, dashfake, dashnode
    import calendar
    t = float(calendar.timegm(time.strptime(real["hello"]["time"], "%Y-%m-%dT%H:%M:%SZ")))
    clock = dashfake.ManualClock(t)
    sc = dashfake.Scenario("paused", clock=clock)
    fake = sc.nodes["office-pc"]
    by_id = {}
    for k, v in real.items():
        if k.startswith("status-"):
            by_id[v["job_id"]] = v
    fake.hello = lambda **a: dict(real["hello"])
    fake.list = lambda **a: json.loads(json.dumps(real["list"]))
    fake.status = lambda **a: json.loads(json.dumps(by_id.get(a.get("id")) or {"ok": False, "error": "no-such-job"}))
    readers = {n: dashnode.NodeReader(n, f) for n, f in sc.nodes.items()}
    app = dashboard.App(dev=True, clock=clock, readers=readers, index=dashboard.FakeIndex(clock, sc),
                        mac=dashfake.FakeMac(clock), scenario=sc, port=47001, version="v2026.11.11")
    app.touch()
    for w in app.workers.values():
        w.tick()
    clock.advance(2.0)
    app.mac.sample()

    def wrap(obj):
        out = {"api": dashboard.API, "ok": True}
        out.update(obj)
        return json.loads(json.dumps(out, ensure_ascii=False))
    return {"api-overview.json": wrap(app.api_overview()),
            "api-job-paused.json": wrap(app.api_job(IDS["lender"])),
            "api-job-resuming.json": wrap(app.api_job(IDS["lender2"]))}


# ------------------------------------------------------------------------------------------------ r2-shapes (unit.py)
R2_JOB_KEYS = ("pausable", "paused", "time", "note", "grant", "run_s", "paused_s", "pauses", "held", "band", "place",
               "state", "class")


def kind(v):
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    return type(v).__name__


def paths(v, p="$", out=None):
    """{path: {kinds}} of a JSON value; list items share the path p[]; null is a kind of its own."""
    out = {} if out is None else out
    out.setdefault(p, set()).add(kind(v))
    if isinstance(v, dict):
        for k, x in v.items():
            paths(x, p + "." + k, out)
    elif isinstance(v, list):
        for x in v:
            paths(x, p + "[]", out)
    return out


def r2_paths(jobs):
    """The scheduler's paths of a list of job entries (contract §3.1), with their kinds."""
    out = {}
    for j in jobs:
        for k in R2_JOB_KEYS:
            if k in j:
                paths(j[k], "$." + k, out)
    return out


def shape_diff(real, fixture):
    """Both ways: a path one side has and the other lacks; a path whose non-null kinds differ."""
    d = []
    for p in sorted(set(real) | set(fixture)):
        if p not in fixture:
            d.append("%s: only the real node sends it" % p)
        elif p not in real:
            d.append("%s: only the fixture has it" % p)
        else:
            a, b = real[p] - {"NoneType"}, fixture[p] - {"NoneType"}
            if a and b and a != b:
                d.append("%s: the node sends %s, the fixture has %s" % (p, sorted(a), sorted(b)))
    return d


def values(jobs, path):
    out = set()
    for j in jobs:
        v = j
        for k in path.split("."):
            v = v.get(k) if isinstance(v, dict) else None
        if v is not None:
            out.add(v)
    return out


def template(note):
    import re
    note = re.sub(r"\d\d:\d\d", "HH:MM", note or "")
    return re.sub(r"\b(?=[a-z]*[0-9])[0-9a-z]{6}\b", "ID", note)


def t_r2_shapes(check, flag, base):
    """The dashboard's release 2 fixtures (dashfake's paused scenario) against the real node's answers."""
    fx = os.path.join(REPO, "tools", "test-dashboard", "fixtures")

    def load(n):
        d = json.load(open(os.path.join(fx, n), encoding="utf-8"))
        if durable.fault("fixture-drift"):   # the fixtures as they were before the merge, and worse
            for j in d.get("jobs") or []:
                pz, g = j.get("paused"), j.get("grant") or {}
                if pz and pz.get("resume_asked"):
                    j["note"] = "paused for %s until 09:58" % (pz.get("for_job") or "x")[-6:]
                if pz:
                    pz.pop("lent_to", None)
                if g.get("class") == "borrowing":
                    g["class"] = "borrowed"
            if "features" in d:
                d["features"] = [f for f in d["features"] if f != "pause"]
        return d
    real = answers(base)
    rl, fl = real["list"]["jobs"], load("node-list-r2.json")["jobs"]
    d = shape_diff(r2_paths(rl), r2_paths(fl))
    check("list: the scheduler's fields of every job (paused, time, note, grant, run_s, held, band, ...) have the same "
          "keys and types in the real node's answer and in node-list-r2.json" + flag("drop-field") + flag("fixture-drift"),
          not d, "\n".join(d))
    for name, fixture in (("status-paused", "node-status-paused.json"), ("status-finished", "node-status-finished-r2.json")):
        d = shape_diff(r2_paths([real[name]]), r2_paths([load(fixture)]))
        check("%s: the same keys and types as %s" % (name, fixture) + flag("drop-field"), not d, "\n".join(d))
    for path in ("state", "grant.class", "paused.phase", "paused.why", "band"):
        miss = values(rl, path) - values(fl, path)
        check("list: every %s the real node sends also appears in the fixture (%s)" % (path, sorted(values(rl, path))) +
              (flag("fixture-drift") if path == "grant.class" else ""), not miss, sorted(miss))
    rt = set(template(j.get("note")) for j in rl if j.get("paused"))
    ft = set(template(j.get("note")) for j in fl if j.get("paused"))
    check("list: a paused or resuming job's note has the node's wording in the fixture too (%s)" % sorted(rt) +
          flag("fixture-drift"), rt <= ft, (sorted(rt), sorted(ft)))
    check("list: a finished job's pause history is in status only (contract §3.1)" + flag("list-keeps-pauses"),
          not any("pauses" in j for j in rl) and "pauses" in real["status-finished"], [j["job_id"] for j in rl if "pauses" in j])
    h, fh = real["hello"], load("node-hello.json")
    check("hello: the features, auto_pause, pause.mem_lend and queue.paused as in node-hello.json" + flag("fixture-drift"),
          h.get("features") == fh.get("features") and h.get("auto_pause") == fh.get("auto_pause") and
          (h.get("pause") or {}).get("mem_lend") is True and set((fh.get("queue") or {})) <= set(h.get("queue") or {}),
          (h.get("features"), fh.get("features"), h.get("auto_pause"), h.get("pause"), h.get("queue")))
    # the committed record of the real answers (tools/test-dashboard/fixtures/real-r2) is what the node sends now
    rec = os.path.join(fx, "real-r2")
    now = {"node-%s.json" % k: v for k, v in real.items()}
    now.update(through_mac(real))
    names = sorted(x for x in os.listdir(rec) if x.endswith(".json"))
    check("fixtures/real-r2 has the files r2_answers.py writes", names == sorted(now), (names, sorted(now)))
    for n in names:
        if n in now:
            d = shape_diff(paths(now[n]), paths(json.load(open(os.path.join(rec, n), encoding="utf-8"))))
            check("fixtures/real-r2/%s has the keys and types the node and the Mac's server give now (else: python3 -I -S "
                  "tools/test-jobs/r2_answers.py tools/test-dashboard/fixtures/real-r2)" % n + flag("drop-field"),
                  not d, "\n".join(d[:20]))


def main(argv):
    base = tempfile.mkdtemp(prefix="mu-r2-answers-")
    try:
        out = answers(base)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    files = {"node-%s.json" % k: v for k, v in out.items()}
    files.update(through_mac(out))
    if argv:
        os.makedirs(argv[0], exist_ok=True)
        for k, v in sorted(files.items()):
            with open(os.path.join(argv[0], k), "w", encoding="utf-8") as f:
                json.dump(v, f, indent=1, sort_keys=True, ensure_ascii=False)
                f.write("\n")
    else:
        json.dump(files, sys.stdout, indent=1, sort_keys=True, ensure_ascii=False)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
