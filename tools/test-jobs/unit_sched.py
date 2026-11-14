"""Unit checks of the scheduler (docs/design/scheduler.md §13, layers 1 and 2), run by unit.py:
  sched         plan() table-driven (fit, extra, shadow time, margin, valve, express and its fall-through, grant-less
                running job, disk low, capacity lowered after submit), resolve(), the walk, moves, authorisation,
                invariants
  sched-sim     a randomised simulation of plan() (seeded): random arrivals, early ends, units that run to their
                limit, slow activations, cancels, VM stops (re-queue at the front), holds, releases and moves
  sched-runner  the runner's passes against stubs of systemd: grants from state files, restart, cancel, re-queue,
                the backfill re-check after a slow checkout, held jobs, the order file, the queue verbs' answers
Each check names the fault that must make it fail; tools/test-jobs/run.sh runs them again with each fault."""
import json
import os
import random
import shutil
import time

from mu import durable, sched

check = None   # set by unit.py
G = 1 << 30
MI = 1 << 20


def flag(f):
    return " (fault %s is on: this must FAIL)" % f if durable.fault(f) else ""


def cap_of(cores=4, mem=2 * G, rex=1 * G):
    return {"cores": cores, "cpus": list(range(cores)), "mem_bytes": mem, "reserve_system": 2 * G,
            "reserve_express": rex, "express_max": 2}


def req(cores, mem, t=3600, cls="normal"):
    return {"cores": cores, "mem_bytes": mem, "time_s": t, "class": cls, "env_cores": cores}


def quick():
    return req(1, 512 * MI, 600, "express")


def run(jid, cpus, mem, t, start, cls="normal"):
    g = {"cores": len(cpus), "cpus": list(cpus), "mem_bytes": mem, "time_s": t, "class": cls, "start_mono": start,
         "active_mono": start}
    return {"id": jid, "grant": g, "end": sched.end_bound(g)}


def ids(pl):
    return [x["id"] for x in pl["starts"]]


def grants(pl):
    return {x["id"]: x["grant"] for x in pl["starts"]}


# ------------------------------------------------------------------------------------------------ layer 1
def t_sched():
    cap = cap_of()
    # fit and packing
    pl = sched.plan(0, cap, [], [{"id": "a", "req": req(2, 512 * MI)}, {"id": "b", "req": req(2, 512 * MI)}])
    g = grants(pl)
    check("two 2-core jobs on 4 cores both start, on disjoint CPUs" + flag("one-at-a-time") + flag("cpus-reused"),
          ids(pl) == ["a", "b"] and not set(g["a"]["cpus"]) & set(g["b"]["cpus"]), pl["starts"])
    pl = sched.plan(0, cap, [], [{"id": x, "req": req(2, 256 * MI)} for x in "abc"])
    check("three 2-core jobs on 4 cores: two start, the third waits for cores" + flag("no-core-admission"),
          ids(pl) == ["a", "b"] and (pl["waiting"].get("c") or {}).get("resources", {}).get("cores") == 2, pl)
    pl = sched.plan(0, cap, [], [{"id": x, "req": req(1, 1 * G)} for x in "abc"])
    check("three 1 GB jobs with 2 GB for jobs: two start, the third waits for memory" + flag("no-mem-admission"),
          ids(pl) == ["a", "b"] and (pl["waiting"].get("c") or {}).get("resources", {}).get("mem_bytes") == G, pl)

    # the head reservation and backfill: A (2 cores) runs until its bound; H (4 cores) waits
    now = 1000.0
    A = run("A", [0, 1], 512 * MI, 180, now - 10)
    S = A["end"]
    short = int(S - now - sched.MARGIN_S)
    q = [{"id": "H", "req": req(4, 512 * MI)}, {"id": "B", "req": req(2, 256 * MI, short)},
         {"id": "C", "req": req(2, 256 * MI, 3600)}]
    pl = sched.plan(now, cap, [A], q)
    check("the head waits with starts_by = A's end bound; B (ends before it) starts backfilled on the 2 free cores" +
          flag("backfill-ignores-shadow") + flag("no-head-reservation"),
          pl["head"] == "H" and pl["starts_by"]["H"] == S and ids(pl) == ["B"] and grants(pl)["B"]["class"] ==
          "backfilled", pl)
    pl = sched.plan(now, cap, [A], [{"id": "H", "req": req(4, 512 * MI)}, {"id": "C", "req": req(2, 256 * MI, 3600)}])
    check("C (2 cores free, but 1 h) waits with a reservation that says which --time would have let it start now" +
          flag("backfill-ignores-shadow") + flag("no-head-reservation"),
          ids(pl) == [] and pl["waiting"].get("C", {}).get("reservation", {}).get("fits_if_time_s_le") == short and
          pl["waiting"]["C"]["reservation"]["head"] == "H", pl)
    pl = sched.plan(now, cap, [A], [{"id": "H", "req": req(4, 512 * MI)}, {"id": "B", "req": req(2, 256 * MI, short + 1)}])
    check("one second longer than S - now - MARGIN: B waits (the margin covers systemd-run and the stop)" +
          flag("margin-zero"), ids(pl) == [], pl)
    # extra: H needs 3 cores; at S 4 are free, so one core is extra
    q = [{"id": "H", "req": req(3, 512 * MI)}, {"id": "x", "req": req(1, 256 * MI, 7200)},
         {"id": "y", "req": req(1, 256 * MI, 7200)}]
    pl = sched.plan(now, cap, [run("A", [0, 1], 512 * MI, 180, now - 10)], q)
    check("extra: a long 1-core job starts on the one core H will not need; a second one does not" +
          flag("extra-not-reduced"), ids(pl) == ["x"] and grants(pl)["x"]["class"] == "normal", pl)
    # memory counts for the reservation too
    pl = sched.plan(now, cap, [run("A", [0], 512 * MI, 180, now - 10)],
                    [{"id": "H", "req": req(2, 1792 * MI)}, {"id": "m", "req": req(1, 512 * MI, 7200)}])
    check("a long job that fits the cores but takes memory the head needs at S waits" + flag("no-mem-admission"),
          ids(pl) == [], pl)
    # the valve
    late = run("A", [0, 1], 512 * MI, 60, now - 400)
    pl = sched.plan(now, cap, [late], [{"id": "H", "req": req(4, 512 * MI)}, {"id": "B", "req": req(2, 256 * MI, 10)}])
    check("a unit 60 s past its bound opens the valve: starts_by null (valve), no time-based backfill",
          pl["starts_by"].get("H") is None and pl["S_why"] == "valve" and ids(pl) == [], pl)

    # the express lane
    whole = run("W", [0, 1, 2, 3], 2 * G, 43200, now)
    pl = sched.plan(now, cap, [whole], [{"id": "q1", "req": quick()}, {"id": "q2", "req": quick()},
                                        {"id": "q3", "req": quick()}])
    check("beside a whole-node job, two --quick jobs start in the lane; the third waits as an ordinary 1-core job" +
          flag("no-express") + flag("express-from-pool"),
          ids(pl) == ["q1", "q2"] and all(g["class"] == "express" for g in grants(pl).values()) and
          "resources" in pl["waiting"].get("q3", {}), pl)
    pl = sched.plan(now, cap, [run("A", [0, 1], 512 * MI, 180, now - 10)],
                    [{"id": "H", "req": req(4, 512 * MI)}, {"id": "q", "req": quick()}])
    check("an express job does not change the head's starts_by and takes nothing from the pool" + flag("express-from-pool"),
          ids(pl) == ["q"] and pl["starts_by"].get("H") == S and pl["free"]["cores"] == 2, pl)
    r = sched.resolve({"sched": 1, "limits": {"cores": 1, "mem_bytes": 256 * MI, "time_s": 60}}, cap)
    check("explicit --cores 1 --mem 256M --time 1m is not express" + flag("express-by-size"), r["class"] == "normal", r)
    try:
        sched.resolve({"sched": 1, "limits": {"time_s": 10 ** 400}}, cap)
        tb = None
    except sched.TooBig as e:
        tb = e
    check("a --time past MAX_TIME_S (365 days) is too-big in resolve(): the plan never adds a huge number to a float" +
          flag("no-size-bounds"), tb is not None and "--time" in str(tb), tb)
    r = sched.resolve({"sched": 1, "limits": {"quick": True}}, cap)
    check("--quick is express: 1 core, 512 MiB, 10 min" + flag("no-express"),
          r["class"] == "express" and (r["cores"], r["mem_bytes"], r["time_s"]) == (1, 512 * MI, 600), r)

    # a running job without a grant holds the node
    gl = {"id": "old", "grant": None, "end": None}
    pl = sched.plan(now, cap, [gl], [{"id": "a", "req": req(1, 256 * MI)}, {"id": "q", "req": quick()}])
    check("a running job without a grant (previous release) holds the node: nothing ordinary starts, starts_by null, "
          "a --quick job still runs" + flag("grantless-counts-zero"),
          ids(pl) == ["q"] and pl["starts_by"].get("a", "x") is None and pl["S_why"] == "grantless", pl)
    # disk
    pl = sched.plan(now, cap, [], [{"id": "a", "req": req(1, 256 * MI)}, {"id": "q", "req": quick()}],
                    disk={"free_gb": 1, "min_free_gb": 20, "where": "/srv/mu"})
    check("disk low: nothing starts, each job waits for disk, starts_by null",
          ids(pl) == [] and "disk" in pl["waiting"]["a"] and pl["starts_by"]["a"] is None, pl)
    # capacity lowered after submit
    try:
        sched.resolve({"sched": 1, "limits": {"cores": 8}}, cap)
        tb = None
    except sched.TooBig as e:
        tb = e
    pl = sched.plan(now, cap, [], [{"id": "big", "error": tb}, {"id": "a", "req": req(1, 256 * MI)}])
    check("a request over the capacity is too-big; in the plan it fails and the next job starts",
          tb is not None and [x[0] for x in pl["fail"]] == ["big"] and ids(pl) == ["a"], (tb, pl))
    # sizing
    r = sched.resolve({"limits": {"cores": 1, "mem_bytes": None}}, cap)
    check("an old workstation's job (no sched) is granted the whole node, MU_JOB_CORES its --cores" + flag("legacy-sized"),
          r["cores"] == 4 and r["env_cores"] == 1 and r["mem_bytes"] == cap["mem_bytes"], r)
    r = sched.resolve({"sched": 1, "limits": {}}, cap)
    check("no flags: whole node, M, 12 h, sources default", (r["cores"], r["mem_bytes"], r["time_s"]) ==
          (4, cap["mem_bytes"], 43200) and set(r["source"].values()) == {"default"}, r)
    c2 = sched.capacity({"sched": {"cores": 4, "mem_bytes": 2 * G}}, range(20), 23 * G)
    c3 = sched.capacity({}, range(20), 23576412 * 1024)
    check("capacity: node.json sched lowers C and M; the PC's defaults are 20 cores and MemTotal - 3 GiB",
          c2["cores"] == 4 and c2["cpus"] == [0, 1, 2, 3] and c2["mem_bytes"] == 2 * G and c3["cores"] == 20 and
          c3["mem_bytes"] == 23576412 * 1024 - 3 * G, (c2, c3))

    # the workstation's side of sizing (scheduler.md §4.3, §11, §20.12)
    from mu import jobs, proto

    class A:
        cores = mem = time = None
        quick = hold = others = False
        priority = None
    new_node = {"features": ["sched", "usage", "queue-control"], "capacity": {"cores": 20, "mem_bytes": 20 * G}}
    old_node = {"queue": {"running": 1, "queued": 2}}

    def sizes(ans, limits, **kw):
        a = A()
        for k, v in kw.items():
            setattr(a, k, v)
        try:
            return jobs.check_sizes(a, ans, "office-pc", limits), limits
        except proto.MuError as e:
            return (e.code, e.error), limits
    r1 = sizes(new_node, {"cores": 64, "mem_bytes": None, "time_s": None}, cores=64)
    r2 = sizes(new_node, {"cores": 8, "mem_bytes": 8 * G, "time_s": 60}, cores=8)
    r3 = sizes(old_node, {"cores": None, "mem_bytes": None, "time_s": None, "quick": True}, quick=True)
    r4 = sizes(old_node, {"cores": None, "mem_bytes": None, "time_s": None}, priority="high", others=True)
    check("the workstation refuses --cores 64 on a 20-core node before anything is sent (exit 2 too-big)" +
          flag("no-capacity-check"), r1[0] == (2, "too-big"), r1)
    check("a request that fits passes", r2[0] is None, r2)
    check("an older node: --quick is sent as --cores 1 --mem 512M --time 10m, with a note",
          r3[1] == {"cores": 1, "mem_bytes": 512 * MI, "time_s": 600} and "one job at a time" in (r3[0] or ""), r3)
    check("an older node: --priority is refused before the upload (exit 6 node-too-old)", r4[0] == (6, "node-too-old"), r4)

    # held jobs
    pl = sched.plan(now, cap, [], [{"id": "h", "held": True, "req": req(4, 256 * MI)}, {"id": "a", "req": req(4, 256 * MI)}])
    check("a held job is skipped: the next starts, the held one waits {held: true}" + flag("hold-ignored"),
          ids(pl) == ["a"] and pl["waiting"]["h"] == {"held": True} and pl["place"] == {"h": 1, "a": 2} and
          pl["position"] == {"a": 1}, pl)

    # the walk
    items = [{"id": "a", "band": "normal", "accepted_ns": 1}, {"id": "b", "band": "normal", "accepted_ns": 2},
             {"id": "c", "band": "high", "accepted_ns": 3}, {"id": "d", "band": "normal", "accepted_ns": 4},
             {"id": "e", "band": "low", "accepted_ns": 0}]
    w = [x["id"] for x in sched.walk(items, ["d", "a"])]
    check("the walk: bands first (high before normal before low), then queue.json's order, then accepted_ns" +
          flag("bands-ignored") + flag("walk-by-accepted-ns"), w == ["c", "d", "a", "b", "e"], w)
    w0 = sched.walk([{"id": x, "band": "normal", "accepted_ns": i} for i, x in enumerate("ABCD")], [])
    w1, nb = sched.place_after(w0, "D", "before", "B")
    check("move D --before B with no order file: A, D, B, C", [x["id"] for x in w1] == list("ADBC"), w1)
    wb = sched.walk([{"id": "A", "band": "normal", "accepted_ns": 1}, {"id": "H", "band": "high", "accepted_ns": 2},
                     {"id": "B", "band": "normal", "accepted_ns": 3}], [])
    w2, nb2 = sched.place_after(wb, "B", "front")
    w3, nb3 = sched.place_after(wb, "A", "before", "H")
    check("--front stays in the job's band (behind the high job); --before a high job puts it in high",
          [x["id"] for x in w2] == ["H", "B", "A"] and nb2 == "normal" and [x["id"] for x in w3] == ["A", "H", "B"]
          and nb3 == "high", (w2, w3))
    wp = sched.walk([{"id": "h1", "band": "high", "accepted_ns": 1}, {"id": "n1", "band": "normal", "accepted_ns": 2},
                     {"id": "X", "band": "normal", "accepted_ns": 3}], [])
    w4, _ = sched.place_after(wp, "X", "priority", band="high")
    check("priority X high puts X behind the high jobs already there" + flag("priority-to-front"),
          [x["id"] for x in w4] == ["h1", "X", "n1"], w4)
    check("passed(): the jobs a move puts the job ahead of", [x["id"] for x in sched.passed(w0, w1, "D")] == ["B", "C"])

    # authorisation (§20.4)
    own = ["ci-owner"]
    J = {"key": "ci-owner", "project_id": "p1", "band": "normal"}
    K = {"key": "ci-other", "project_id": "p2", "band": "normal"}
    cases = [
        ("another key may not hold the owner's job", sched.authorise("hold", J, "ci-other", own, "p1", True),
         "not-allowed", "no-auth-check"),
        ("a key may hold its own job", sched.authorise("hold", K, "ci-other", own, "p2", False), None, None),
        ("a key may not move its job ahead of another workstation's",
         sched.authorise("move", K, "ci-other", own, "p2", True, [{"key": "ci-owner", "project_id": "p2"}]),
         "not-allowed", "no-auth-check"),
        ("the owner may arrange any job (with --others for another project)",
         sched.authorise("hold", K, "ci-owner", own, "p1", True), None, None),
        ("urgent is for an owner key only", sched.authorise("priority", K, "ci-other", own, "p2", False, [], "urgent"),
         "not-allowed", "no-auth-check"),
        ("an owner's hold cannot be released by the job's own workstation",
         sched.authorise("release", dict(K, held_by="ci-owner"), "ci-other", own, "p2", False), "not-allowed",
         "owner-acts-not-sticky"),
        ("an owner's band cannot be changed by the job's own workstation",
         sched.authorise("priority", dict(K, band_by="ci-owner"), "ci-other", own, "p2", False, [], "low"),
         "not-allowed", "owner-acts-not-sticky"),
        ("another project's job needs --others, even for the owner",
         sched.authorise("hold", K, "ci-owner", own, "p1", False), "other-project", "no-others-guard"),
        ("passing another project's job needs --others",
         sched.authorise("move", J, "ci-owner", own, "p1", False, [{"key": "ci-owner", "project_id": "p2"}]),
         "other-project", "no-others-guard"),
        ("no owner file and two keys: nobody may arrange another's job",
         sched.authorise("hold", K, "ci-owner", [], "p2", False), "not-allowed", "no-auth-check"),
    ]
    for name, got, want, f in cases:
        check(name + (flag(f) if f else ""), (got[0] if got else None) == want, got)

    # invariants
    bad = sched.invariants(cap, [{"id": "a", "grant": {"cpus": [0, 1], "mem_bytes": G, "class": "normal"}},
                                 {"id": "b", "grant": {"cpus": [1, 2], "mem_bytes": G, "class": "normal"}}])
    ok_ = sched.invariants(cap, [{"id": "a", "grant": {"cpus": [0, 1], "mem_bytes": G, "class": "normal"}},
                                 {"id": "e", "grant": {"cpus": [0, 1, 2, 3], "mem_bytes": 512 * MI, "class": "express"}}])
    check("invariants: shared CPUs are found; an express job beside ordinary ones is fine", bool(bad) and not ok_,
          (bad, ok_))
    check("end bound: activation + time + SLACK, or start + START_WAIT + time + SLACK before activation",
          sched.end_bound({"time_s": 100, "start_mono": 5, "active_mono": 7}) == 117 and
          sched.end_bound({"time_s": 100, "start_mono": 5}) == 175)
    check("fmt_cpus / parse_cpus", sched.fmt_cpus([0, 1, 2, 3, 8, 10, 11]) == "0-3,8,10-11" and
          sched.parse_cpus("0-3,8") == [0, 1, 2, 3, 8])


# ------------------------------------------------------------------------------------------------ layer 2
def t_sched_sim(steps=3000, seed=20261115):
    """Event-driven: time jumps to the next arrival or end. Asserted at every step: Σ ordinary grants ≤ (C, M), CPU
    sets disjoint, ≤ 2 express; the head starts no later than the first S announced for it (reset by a VM stop or a
    queue act, which may change what the head waits for); held jobs never start; every job ends."""
    rnd = random.Random(seed)
    cap = cap_of(cores=8, mem=16 * G, rex=1 * G)
    now = 0.0
    queue, running, done = [], {}, set()
    first_S, violations, head_late, held_started, no_promise = {}, [], [], [], []
    next_arrival = 0.0
    n_jobs = 0
    counts = {"starts": 0, "backfilled": 0, "express": 0, "requeued": 0, "acts": 0, "cancels": 0}
    order = []   # explicit order (moves)
    for step in range(steps + 100000):
        arrivals_on = step < steps
        if step == steps:   # arrivals stop: every hold is released, then the queue must drain
            for q in queue:
                q["held"] = False
            first_S.clear()
        ends = [r["actual_end"] for r in running.values()]
        cands = ([next_arrival] if arrivals_on else []) + ends
        if not cands:
            if not queue:
                break
            now += 1
        else:
            now = max(now, min(cands))
        for jid in [k for k, r in running.items() if r["actual_end"] <= now]:
            done.add(jid)
            del running[jid]
        if arrivals_on and next_arrival <= now:
            n_jobs += 1
            jid = "j%04d" % n_jobs
            k = rnd.random()
            if k < 0.12:
                rq = quick()
            elif k < 0.17:
                rq = req(8, 16 * G, rnd.choice([600, 3600]))
            else:
                rq = req(rnd.randint(1, 8), rnd.randint(1, 16) * G // 2, rnd.choice([120, 300, 900, 1800, 3600, 7200]))
            queue.append({"id": jid, "req": rq, "ns": n_jobs, "held": False,
                          "run": rq["time_s"] if rnd.random() < 0.5 else rnd.uniform(5, rq["time_s"])})
            next_arrival = now + rnd.expovariate(1 / 90.0)
        # random events
        e = rnd.random() if arrivals_on else 1.0
        if e < 0.01 and running:   # a VM stop: every unit gone, re-queued at the front in their old order
            back = sorted(running.values(), key=lambda r: r["q"]["ns"])
            for r in back:
                r["q"]["run"] = r["q"]["req"]["time_s"] * rnd.random()
            queue = [r["q"] for r in back] + queue
            order = [r["q"]["id"] for r in back] + [x for x in order if x not in {r["q"]["id"] for r in back}]
            running.clear()
            first_S.clear()
            counts["requeued"] += len(back)
        elif e < 0.03 and queue:   # a cancel of a queued job
            x = rnd.choice(queue)
            queue.remove(x)
            done.add(x["id"])
            first_S.pop(x["id"], None)
            counts["cancels"] += 1
        elif e < 0.06 and queue:   # a hold, release or move: an explicit act
            x = rnd.choice(queue)
            a = rnd.random()
            if a < 0.4:
                x["held"] = not x["held"]
            else:
                ids_ = [q["id"] for q in walk_sim(queue, order)]
                ids_.remove(x["id"])
                ids_.insert(rnd.randint(0, len(ids_)), x["id"])
                order = ids_
            first_S.clear()
            counts["acts"] += 1
        w = walk_sim(queue, order)
        pl = sched.plan(now, cap, [{"id": k, "grant": r["grant"], "end": sched.end_bound(r["grant"])}
                                   for k, r in running.items()],
                        [{"id": q["id"], "held": q["held"], "req": q["req"]} for q in w])
        h = pl["head"]
        if h and pl["starts_by"].get(h) is not None:
            first_S.setdefault(h, pl["starts_by"][h])
        # no grant-less job, no disk limit, no unit past its bound here: whenever a job that may start does not, the
        # plan names a head and promises it a start (a scheduler without the reservation promises nothing)
        left_ = {q["id"] for q in w if not q["held"]} - {x["id"] for x in pl["starts"]}
        if left_ and (not h or pl["starts_by"].get(h) is None):
            no_promise.append((step, sorted(left_)[:3], h))
        for x in pl["starts"]:
            q = next(qq for qq in queue if qq["id"] == x["id"])
            if q["held"]:
                held_started.append(q["id"])
            act = now + (rnd.choice([0, 1, 5, 30, 55]) if x["grant"]["class"] != "express" else 0)   # ≤ START_WAIT
            g = dict(x["grant"], start_mono=now, active_mono=act)
            run_s = min(q["run"], g["time_s"])
            running[q["id"]] = {"grant": g, "actual_end": act + run_s, "q": q}
            queue.remove(q)
            counts["starts"] += 1
            counts[x["grant"]["class"]] = counts.get(x["grant"]["class"], 0) + 1
            if q["id"] in first_S and now > first_S[q["id"]] + 1e-6:
                head_late.append((q["id"], now - first_S[q["id"]]))
            first_S.pop(q["id"], None)
        bad = sched.invariants(cap, [{"id": k, "grant": r["grant"]} for k, r in running.items()])
        if bad:
            violations.append((step, bad[:2]))
    left = [q["id"] for q in queue if not q["held"]]
    check("simulation (%d steps, %d jobs, %s): grants within capacity, CPU sets disjoint, at most 2 express" % (
        steps, n_jobs, ", ".join("%s %d" % kv for kv in sorted(counts.items()))) + flag("cpus-reused"),
        not violations, violations[:5])
    check("simulation: every head starts no later than the first starts_by announced for it" + flag("margin-zero") +
          flag("extra-not-reduced") + flag("express-from-pool"), not head_late, head_late[:5])
    check("simulation: no held job ever starts" + flag("hold-ignored"), not held_started, held_started[:5])
    check("simulation: while jobs wait, there is a head with a starts_by" + flag("no-head-reservation"),
          not no_promise, no_promise[:5])
    check("simulation: no job waits forever (all ended or held when arrivals stop)", not left and not running,
          (left[:5], list(running)[:5]))
    check("simulation: backfill and the express lane were exercised", counts.get("backfilled", 0) > 20 and
          counts.get("express", 0) > 20, counts)


def walk_sim(queue, order):
    idx = {x: i for i, x in enumerate(order)}
    return sorted(queue, key=lambda q: (idx.get(q["id"], len(idx)), q["ns"]))


# ------------------------------------------------------------------------------------------------ the runner
SYSTEMD_RUN_STUB = r"""#!/bin/sh
# systemd-run --user: registers the unit as active (unless run-nocreate), keeps its arguments; exits with run-rc
for a in "$@"; do case "$a" in --unit=*) u="${a#--unit=}" ;; esac; done
if grep -q "^$u active" "$STUB_DIR/units" 2>/dev/null; then echo "Unit $u was already loaded" >&2; exit 1; fi
[ -f "$STUB_DIR/run-nocreate" ] || echo "$u active success" >> "$STUB_DIR/units"
echo "start $u" >> "$STUB_DIR/calls"
printf '%s\n' "$@" > "$STUB_DIR/args-$u"
for a in "$@"; do case "$a" in --setenv=MU_JOB_DIR=*) cp "${a#--setenv=MU_JOB_DIR=}/state.json" "$STUB_DIR/state-at-run-$u" ;; esac; done
rc=$(cat "$STUB_DIR/run-rc" 2>/dev/null || echo 0); [ "$rc" = 0 ] || echo "Failed to start transient service unit" >&2
exit $rc
"""


def t_sched_runner(base, stubs_text):
    """BASE: a temp folder; STUBS_TEXT: unit.py's systemctl stub. The runner's passes with grants on disk."""
    stubs = os.path.join(base, "bin")
    os.makedirs(stubs, exist_ok=True)
    for name, text in (("systemctl", stubs_text), ("systemd-run", SYSTEMD_RUN_STUB)):
        with open(os.path.join(stubs, name), "w") as f:
            f.write(text)
        os.chmod(os.path.join(stubs, name), 0o755)
    os.environ.update(MU_SYSTEMCTL=os.path.join(stubs, "systemctl"), MU_SYSTEMD_RUN=os.path.join(stubs, "systemd-run"),
                      MU_WSL_HOST_DRIVE="/nonexistent", MU_HOME=os.path.join(base, "home"),
                      MU_NODE_OWNERS=os.path.join(base, "owners"), MU_NODE_CHECK="1")
    with open(os.path.join(base, "owners"), "w") as f:
        f.write("ci-owner\n")
    from mu import node
    slow = {"s": 0}
    # the test's node, whatever this machine is: 4 CPUs and 64 GB (node.json sched lowers M to 2 GB)
    node.capacity = lambda nd=None: sched.capacity(nd if nd is not None else node.node_info(), [0, 1, 2, 3], 64 * G)

    def prep(j):
        shutil.rmtree(os.path.join(j["dir"], "work"), ignore_errors=True)
        os.mkdir(os.path.join(j["dir"], "work"))
        if slow["s"]:
            time.sleep(slow["s"])
    node.prepare_work = prep
    st = {}

    def fresh(tag, **sched_conf):
        d = os.path.join(base, tag)
        for x in ("srv/jobs", "stub", "home/state"):
            os.makedirs(os.path.join(d, x))
        node.ROOT = os.path.join(d, "srv")
        os.environ["MU_HOME"] = os.path.join(d, "home")
        os.environ["STUB_DIR"] = st["stub"] = os.path.join(d, "stub")
        conf = {"cores": 4, "mem_bytes": 2 * G}
        conf.update(sched_conf)
        durable.write_record(os.path.join(node.ROOT, "node.json"), {"name": "n", "min_free_gb": 0, "sched": conf})
        node.STARTED_HERE.clear()

    seq = [0]

    def mkjob(jid, limits=None, key="ci-owner", project="p1-0123456789ab", state="queued", band=None, new=True):
        seq[0] += 1
        d = node.job_dir(jid)
        os.makedirs(d)
        job = {"schema": 1, "job_id": jid, "project_id": project, "mu_version": "v1", "argv": ["true"], "cwd": "",
               "outputs": [], "limits": dict({"cores": None, "mem_bytes": None, "time_s": None}, **(limits or {})),
               "rerun": True, "accepted_ns": seq[0], "key": key, "name": jid[-6:]}
        if new:
            job["sched"] = 1
        if band:
            job["priority"] = band
        durable.write_record(os.path.join(d, "job.json"), job)
        durable.write_record(os.path.join(d, "state.json"), {"state": state, "attempt": 1})
        return d

    def unit_line(jid, active, result):
        with open(os.path.join(st["stub"], "units"), "a") as f:
            f.write("mu-job-%s.service %s %s\n" % (jid, active, result))

    def started():
        try:
            return [ln.split()[1][7:-8] for ln in open(os.path.join(st["stub"], "calls")) if ln.startswith("start ")]
        except OSError:
            return []

    def args_of(jid):
        try:
            return open(os.path.join(st["stub"], "args-mu-job-%s.service" % jid)).read().splitlines()
        except OSError:
            return []

    def S(jid):
        return node.load_job(jid)["state"] or {}

    def at_run(jid):   # the job's state.json as it was when systemd-run was called
        return durable.read_record(os.path.join(st["stub"], "state-at-run-mu-job-%s.service" % jid)) or {}

    def violation():
        f = os.path.join(node.ROOT, "state", "invariant-violation")
        return open(f).read() if os.path.exists(f) else ""

    J = ["20261115-1000%02d-%s" % (i, c * 6) for i, c in enumerate("abcdefghij")]
    two = {"cores": 2, "mem_bytes": 512 * MI, "time_s": 600}

    # 1. concurrency, grants in state.json, the unit's properties and environment
    fresh("conc")
    mkjob(J[0], two)
    mkjob(J[1], two)
    mkjob(J[2], two)
    node.step(first=True)
    g0, g1 = S(J[0]).get("grant") or {}, S(J[1]).get("grant") or {}
    a0 = args_of(J[0])
    check("two 2-core jobs start in one pass with disjoint CPUs in their state.json; the third waits" +
          flag("one-at-a-time") + flag("cpus-reused") + flag("cpus-from-plan-cache"),
          started() == [J[0], J[1]] and set(g0.get("cpus", [])).isdisjoint(g1.get("cpus", [0])) and
          S(J[2]).get("state") == "queued" and not violation(), (started(), g0, g1, violation()))
    check("the unit: MemoryMax = the grant, RuntimeMaxSec = --time, MU_JOB_CPUS, thread variables, TMPDIR" +
          flag("mem-max-is-capacity") + flag("no-thread-env"),
          "MemoryMax=%d" % (512 * MI) in a0 and "RuntimeMaxSec=600" in a0 and
          "--setenv=MU_JOB_CPUS=%s" % sched.fmt_cpus(g0.get("cpus", [])) in a0 and "--setenv=OMP_NUM_THREADS=2" in a0
          and "--setenv=PYTHON_CPU_COUNT=2" in a0 and "--setenv=CCX_NPROC_EQUATION_SOLVER=2" in a0 and
          any(x.startswith("--setenv=TMPDIR=") and x.endswith("/tmp") for x in a0) and
          os.path.isdir(os.path.join(node.job_dir(J[0]), "tmp")), a0)
    r0 = at_run(J[0])
    check("the grant is in state.json before systemd-run is called (running, its CPUs, start_unconfirmed): a unit "
          "never exists without it (e2e Q16, unit level)" + flag("grant-after-systemd-run"),
          r0.get("state") == "running" and (r0.get("grant") or {}).get("cpus") == g0.get("cpus") and
          r0.get("start_unconfirmed") is True, r0)

    # 2. a restart of the runner: grants come back from state.json, nothing is over-committed
    node.STARTED_HERE.clear()
    mkjob(J[3], {"cores": 1, "mem_bytes": 256 * MI, "time_s": 7200})
    node.step(first=True)
    check("after a runner restart the grants come back from state.json: a job that fits only if they were forgotten "
          "stays queued" + flag("grant-in-memory-only"),
          started() == [J[0], J[1]] and S(J[3]).get("state") == "queued" and not violation(), (started(), violation()))

    # 3. cancel: the grant is freed only when the unit has ended
    with open(os.path.join(node.job_dir(J[0]), "cancel"), "w") as f:
        f.write("x\n")
    node.step()
    still = started() == [J[0], J[1]]
    unit_line(J[0], "inactive", "success")
    node.step()
    check("a cancelled job keeps its cores until its unit has ended, then the next job gets them" +
          flag("free-on-cancel-request"),
          still and node.load_job(J[0])["result"]["state"] == "cancelled" and J[2] in started() and not violation(),
          (still, started(), violation()))

    # 4. a VM stop with several running: re-queued at the front in their old order, TMPDIR gone
    fresh("vmstop")
    for i in range(3):
        mkjob(J[i], {"cores": 1, "mem_bytes": 256 * MI, "time_s": 600})
    mkjob(J[3], {"cores": 4, "mem_bytes": 256 * MI, "time_s": 600})
    node.step(first=True)
    with open(os.path.join(st["stub"], "units"), "w") as f:
        f.write("")
    for i in range(3):   # (makedirs: under the fault one-at-a-time only the first of them started)
        os.makedirs(os.path.join(node.job_dir(J[i]), "tmp"), exist_ok=True)
        open(os.path.join(node.job_dir(J[i]), "tmp", "scratch"), "w").close()
    mkjob(J[4], {"cores": 4, "mem_bytes": 256 * MI, "time_s": 600})
    for i in range(3):   # attempt 1's figures from the sampler
        with open(os.path.join(node.job_dir(J[i]), "usage.json"), "w") as f:
            json.dump({"cpu_s": 99.0, "mem_peak_bytes": 1 << 30}, f)
    for i in range(3):   # the same as the runner adopting nothing: the units are gone
        node.interrupted(node.load_job(J[i]), "the node stopped")
    w = [x["id"] for x in node.walk_jobs(node.scan_jobs())]
    check("three interrupted jobs go back to the front in their old order, ahead of later jobs" + flag("requeue-new-ns"),
          w[:3] == J[:3] and all(S(J[i]).get("attempt") == 2 and not S(J[i]).get("grant") for i in range(3)), w)
    check("their TMPDIR is gone before the re-run" + flag("keep-tmp-on-rerun"),
          not any(os.path.exists(os.path.join(node.job_dir(J[i]), "tmp")) for i in range(3)))
    check("and attempt 1's sampled usage: attempt 2's result carries its own figures" + flag("keep-usage-on-rerun"),
          not any(os.path.exists(os.path.join(node.job_dir(J[i]), "usage.json")) for i in range(3)))

    # 5. a start whose unit never came (runner killed between grant and systemd-run): back to the queue, grant dropped
    fresh("unconfirmed")
    d = mkjob(J[0], two)
    s0 = {"state": "running", "attempt": 1, "unit": "mu-job-%s.service" % J[0], "start_unconfirmed": True,
          "grant": {"cores": 2, "cpus": [0, 1], "mem_bytes": 512 * MI, "time_s": 600, "class": "normal",
                    "start_mono": time.monotonic(), "boot_id": "x"}, "started_epoch": time.time()}
    durable.write_record(os.path.join(d, "state.json"), s0)
    mkjob(J[1], {"cores": 4, "mem_bytes": 256 * MI, "time_s": 600})
    node.step(first=True)
    s1 = S(J[0])
    check("a grant written before a systemd-run that never ran (the runner died between): a failed start, not an "
          "attempt; back in the queue and started again once, in this pass, on the cores its stale grant had; the "
          "4-core job waits" + flag("unconfirmed-as-interrupted"),
          started() == [J[0]] and s1.get("attempt") == 1 and s1.get("start_failures") == 1 and
          S(J[1]).get("state") == "queued" and not violation(), (s1, started(), violation()))

    # 6. the backfill re-check after a slow checkout
    fresh("recheck")
    mkjob(J[0], two)
    j = node.load_job(J[0])
    slow["s"] = 1.5
    ok = node.start_job(j, {"cores": 2, "cpus": [0, 1], "mem_bytes": 512 * MI, "time_s": 600, "class": "backfilled",
                            "env_cores": 2}, S=time.monotonic() + 600 + sched.MARGIN_S + 1.0)
    slow["s"] = 0
    check("a backfilled job whose checkout outlasts its slack is not started: nothing written, still queued" +
          flag("stamp-before-checkout"), ok is False and S(J[0]).get("state") == "queued" and
          not S(J[0]).get("grant") and started() == [], (ok, S(J[0]), started()))

    # 6b. the first pass that sees the unit active records its activation (the end bound counts from it, e2e Q21),
    # its cgroup, and clears start_unconfirmed (an adopted start whose systemd-run reply was lost)
    fresh("active")
    d = mkjob(J[0], two)
    t0 = time.monotonic()
    durable.write_record(os.path.join(d, "state.json"), {
        "state": "running", "attempt": 1, "unit": "mu-job-%s.service" % J[0], "start_unconfirmed": True,
        "started_epoch": time.time(), "grant": {"cores": 2, "cpus": [0, 1], "mem_bytes": 512 * MI, "time_s": 600,
                                                "class": "normal", "start_mono": t0 - 50, "boot_id": node.boot_id()}})
    unit_line(J[0], "active", "success %d" % int((t0 - 20) * 1e6))
    node.step(first=True)
    s1 = S(J[0])
    g1 = s1.get("grant") or {}
    check("the first pass that sees the unit active: active_mono from systemd, the end bound from it (not from the "
          "start + 60 s), the cgroup recorded, start_unconfirmed cleared" + flag("no-note-active"),
          abs((g1.get("active_mono") or 0) - (t0 - 20)) < 0.01 and
          abs(sched.end_bound(g1) - (t0 - 20 + 600 + sched.SLACK_S)) < 0.01 and
          s1.get("cgroup", "").endswith(J[0] + ".service") and "start_unconfirmed" not in s1, s1)

    # 7. a running job without a grant (the previous release) holds the node; express still runs
    fresh("grantless")
    d = mkjob(J[0], {})
    durable.write_record(os.path.join(d, "state.json"), {"state": "running", "attempt": 1, "started_epoch": time.time() - 5,
                                                        "unit": "mu-job-%s.service" % J[0]})
    unit_line(J[0], "active", "success")
    mkjob(J[1], {"cores": 1, "mem_bytes": 256 * MI, "time_s": 60})
    mkjob(J[2], {"quick": True})
    node.step(first=True)
    st_line = node.status_of(node.load_job(J[1]))
    check("a running job without a grant holds the node: the 1-core job waits (starts_by null, saying why); a --quick "
          "job runs, its unit with CPUQuota=100%" + flag("grantless-counts-zero") + flag("no-express-quota"),
          started() == [J[2]] and S(J[1]).get("state") == "queued" and st_line.get("starts_by") is None and
          "previous release" in (st_line.get("starts_by_why") or "") and "CPUQuota=100%" in args_of(J[2]) and
          not violation(), (started(), st_line, args_of(J[2]), violation()))

    # 8. held jobs; cancel of a held job; rebuild from events.log; the order file lost
    fresh("held")
    mkjob(J[0], {"cores": 4, "mem_bytes": 256 * MI, "time_s": 60})
    out = serve(node, "ci-owner", "hold", "id=" + J[0], "from=p1-0123456789ab")
    mkjob(J[1], {"cores": 4, "mem_bytes": 256 * MI, "time_s": 60})
    node.step(first=True)
    check("a held job is skipped; the next starts" + flag("hold-ignored"),
          out.get("changed") is True and (out.get("held") or {}).get("by") == "ci-owner" and started() == [J[1]],
          (out, started()))
    again = serve(node, "ci-owner", "hold", "id=" + J[0], "from=p1-0123456789ab")
    check("hold twice: changed false", again.get("changed") is False and again.get("ok") is True, again)
    os.unlink(os.path.join(node.job_dir(J[0]), "state.json"))
    unit_line(J[1], "inactive", "success")
    node.step()
    check("a held job whose state.json is lost is rebuilt held from events.log and not started" +
          flag("rebuild-held-as-queued"), S(J[0]).get("state") == "held" and J[0] not in started(), (S(J[0]), started()))
    mkjob(J[2], {"cores": 1, "mem_bytes": 256 * MI, "time_s": 60}, band="high")
    mkjob(J[3], {"cores": 1, "mem_bytes": 256 * MI, "time_s": 60})
    serve(node, "ci-owner", "move", "id=" + J[3], "to=front", "from=p1-0123456789ab")
    with open(os.path.join(node.ROOT, "queue.json"), "wb") as f:
        f.write(b"\0" * 64)
    w = [(x["id"], x["held"]) for x in node.walk_jobs(node.scan_jobs())]
    node.step()
    check("queue.json zeroed: the walk is FIFO within bands, holds and bands kept, the file moved aside once" +
          flag("holds-in-order-file-only") + flag("bands-ignored"),
          w == [(J[2], False), (J[0], True), (J[3], False)] and J[0] not in started() and
          any(x.startswith("queue.json.unreadable") for x in os.listdir(node.ROOT)), (w, started()))
    rel = serve(node, "ci-owner", "cancel", "id=" + J[0])
    check("mu cancel of a held job: cancelled at once", rel.get("state") == "cancelled", rel)

    # 9. queue control through serve(): order, bands, authorisation, the --others guard, answer shapes
    fresh("arrange")
    for x in J[:5]:
        mkjob(x, {"cores": 4, "mem_bytes": 256 * MI, "time_s": 60}, key="ci-owner")
    mkjob(J[5], {"cores": 4, "mem_bytes": 256 * MI, "time_s": 60}, key="ci-other", project="p2-0123456789ab")
    node.step(first=True)   # J0 runs; J1..J5 wait
    A, B, C, D, E = J[1:6]
    mv = serve(node, "ci-owner", "move", "id=" + D, "before=" + B, "from=p1-0123456789ab")
    order = [x["job_id"] for x in serve(node, "ci-owner", "queue")["jobs"] if x["state"] == "queued"]
    check("move D --before B (no order file before): A, D, B, C, E; the answer gives place 2, was 4" +
          flag("walk-by-accepted-ns") + flag("partial-order-write") + flag("drop-field"),
          started() == [J[0]] and order == [A, D, B, C, E] and mv.get("place") == 2 and
          mv.get("was", {}).get("place") == 4 and mv.get("changed") is True and
          [x["job_id"] for x in mv.get("delays") or []] == [B, C], (order, mv))
    no = serve(node, "ci-other", "hold", "id=" + A, "from=p2-0123456789ab", rc=77)
    check("another workstation's key cannot hold the owner's job: exit 77 not-allowed, nothing changed" +
          flag("no-auth-check"), no.get("error") == "not-allowed" and S(A).get("state") == "queued", no)
    op = serve(node, "ci-owner", "hold", "id=" + E, "from=p1-0123456789ab", rc=64)
    check("the owner holding another project's job without --others: exit 64 other-project" + flag("no-others-guard"),
          op.get("error") == "other-project" and S(E).get("state") == "queued", op)
    oh = serve(node, "ci-owner", "hold", "id=" + E, "from=p1-0123456789ab", "others=1")
    rl = serve(node, "ci-other", "release", "id=" + E, "from=p2-0123456789ab", rc=77)
    check("the owner's hold (with --others) cannot be released by the job's own workstation" +
          flag("owner-acts-not-sticky"), oh.get("changed") is True and rl.get("error") == "not-allowed" and
          S(E).get("state") == "held", (oh, rl))
    nq = serve(node, "ci-owner", "move", "id=" + A, "before=" + J[0], "from=p1-0123456789ab", rc=64)
    nr = serve(node, "ci-owner", "hold", "id=" + J[0], "from=p1-0123456789ab", rc=64)
    check("--before a running job is reference-not-queued; hold of a running job is not-queued (both 64)",
          nq.get("error") == "reference-not-queued" and nr.get("error") == "not-queued", (nq, nr))
    pr = serve(node, "ci-owner", "priority", "id=" + C, "class=high", "from=p1-0123456789ab")
    ql = serve(node, "ci-other", "queue", "from=p2-0123456789ab")
    check("priority high moves the job first; queue (asked by the other key) lists every job of both keys and "
          "projects with key, project, place, band" + flag("bands-ignored") + flag("queue-project-filter"),
          pr.get("band") == "high" and pr.get("place") == 1 and {x["key"] for x in ql["jobs"]} == {"ci-owner", "ci-other"}
          and all(k in ql["jobs"][-1] for k in ("place", "band", "project_id", "key", "waiting_for")) and
          ql.get("owners") == ["ci-owner"] and ql.get("you_are_owner") is False, (pr, ql))
    shapes = {"queue": (ql, ("ok", "node", "rev", "owners", "you_are_owner", "warnings", "capacity", "allocated",
                             "free", "jobs")),
              "move": (mv, ("ok", "exit", "job_id", "op", "changed", "state", "band", "place", "was",
                            "higher_bands_ahead", "starts_by", "waiting_for", "by", "delays")),
              "not-allowed": (no, ("ok", "error", "hint", "protocol", "message", "would_touch")),
              "other-project": (op, ("ok", "error", "message", "would_touch")),
              "not-queued": (nr, ("ok", "error", "message", "state"))}
    miss = {k: [f for f in fields if f not in a] for k, (a, fields) in shapes.items()}
    check("answer shapes: every field of §20.9 present" + flag("drop-field"), not any(miss.values()), miss)
    ur = serve(node, "ci-other", "priority", "id=" + E, "class=urgent", "from=p2-0123456789ab", rc=77)
    audit = open(os.path.join(node.ROOT, "audit.log")).read()
    check("audit lines say what was asked (a refused priority: class=urgent) and a band change as old->new" +
          flag("audit-terse"), ur.get("error") == "not-allowed" and
          "queue: refused priority id=%s class=urgent by=ci-other" % E in audit and
          ("queue: priority id=%s class=high by=ci-owner" % C) in audit and "band normal->high" in audit,
          [x for x in audit.splitlines() if "queue:" in x][-4:])
    cn = serve(node, "ci-other", "cancel", "id=" + B, rc=77)
    check("cancel follows the same rule: another workstation's key cannot cancel the owner's job (exit 77)" +
          flag("no-cancel-auth"), cn.get("error") == "not-allowed" and S(B).get("state") == "queued" and
          not os.path.exists(os.path.join(node.job_dir(B), "cancel")), cn)
    cs = serve(node, "ci-other", "cancel", "id=" + E)
    co = serve(node, "ci-owner", "cancel", "id=" + D)
    check("  its own job it may cancel; an owner key any job", cs.get("state") == "cancelled" and
          co.get("state") == "cancelled", (cs, co))

    # 10. hostile specs (security review 2026-11-15): sizes that overflow, free text that forges lines or crashes
    fresh("hostile")
    base_spec = {"schema": 1, "job_id": J[0], "project_id": "p1-0123456789ab", "snapshot": "a" * 40, "tree": "b" * 40,
                 "head": "c" * 40, "mu_version": "v1", "argv": ["true"], "cwd": "", "outputs": [], "sched": 1,
                 "limits": {"cores": None, "mem_bytes": None, "time_s": None}}

    def refused(**kw):
        spec = json.loads(json.dumps(base_spec))
        for k, v in kw.items():
            if k.startswith("limits_"):
                spec["limits"][k[7:]] = v
            else:
                spec[k] = v
        try:
            node.check_spec(spec, J[0])
            return None
        except node.NodeError as e:
            return e.error
    r1 = refused(limits_time_s=10 ** 400)
    r2 = refused(limits_mem_bytes=1 << 60)
    check("check_spec: limits.time_s = 10**400 and mem_bytes = 2**60 are bad-spec" + flag("no-size-bounds"),
          r1 == "bad-spec" and r2 == "bad-spec", (r1, r2))
    r3, r4, r5 = refused(project={"a": 1}), refused(submitted="x"), refused(submitted={"host": ["h"]})
    r6 = refused(name="ok\n  1 urgent FORGED LINE \x1b[2J")
    check("check_spec: a project that is not a string, a submitted that is not an object, a host that is not a string "
          "are bad-spec; a name with a newline (an older workstation's multi-line command) is accepted" +
          flag("no-display-check"), (r3, r4, r5, r6) == ("bad-spec", "bad-spec", "bad-spec", None), (r3, r4, r5, r6))
    stored = node.display_fields(dict(base_spec, name="ok\n  1 urgent FORGED \x1b[2J\u202e", project="p\r",
                                      submitted={"host": "h\x07", "user": "u", "at": "t"}))
    check("the stored spec's name, project, host carry no control or format character" + flag("no-display-check"),
          not any(c in json.dumps(stored, ensure_ascii=False) for c in "\n\r\x1b\x07\u202e") and
          stored["name"].startswith("ok "), stored)
    # jobs already on disk (or from a node without these checks): the plan, hello and queue still work
    mkjob(J[0], {"cores": 1, "mem_bytes": 256 * MI, "time_s": 10 ** 400})
    mkjob(J[1], {"cores": 1, "mem_bytes": 256 * MI, "time_s": 60})
    d2 = mkjob(J[2], {"cores": 4, "mem_bytes": 256 * MI, "time_s": 60}, key="ci-other", project="p2-0123456789ab")
    jj = durable.read_record(os.path.join(d2, "job.json"))
    jj.update(name="ok\n  1 urgent FORGED LINE \x1b[2J", project={"a": 1}, submitted="x")
    durable.write_record(os.path.join(d2, "job.json"), jj)
    try:
        node.step(first=True)
        err = None
    except Exception as e:  # noqa
        err = "%s: %s" % (type(e).__name__, e)
    r0 = node.load_job(J[0])["result"] or {}
    check("a job on disk with time_s 10**400: the pass goes on, that job fails too-big, the next starts" +
          flag("no-size-bounds"), err is None and r0.get("state") == "failed" and r0.get("error") == "too-big" and
          J[1] in started(), (err, r0.get("note"), started()))
    ql = serve(node, "ci-owner", "queue")
    txt = json.dumps(ql, ensure_ascii=False)
    check("mu queue with a job whose name forges lines, project is an object and submitted a string: answered, the "
          "free text without control characters" + flag("no-display-check"),
          ql.get("ok") is True and "\n" not in txt.replace("\\n", "") and "\x1b" not in txt and
          any(x["job_id"] == J[2] for x in ql.get("jobs", [])), ql)

    # 11. the start lock (scheduler.md §9 "Version switch"; review 2026-11-15): an update holds it while it moves the
    # default; a lock left by a process that is gone (killed, another boot, its pid reused) must not stop every start
    fresh("startlock")
    real_boot = node.boot_id
    node.boot_id = lambda: "boot-this"
    lk = node.start_lock_path()
    holder = os.getppid()   # a live process that is not this one (the shell that runs the tests)

    def lock(pid, boot="boot-this", start=None, age=0):
        os.makedirs(lk, exist_ok=True)
        with open(os.path.join(lk, "pid"), "w") as f:
            f.write("%d\n%s\n%s\n" % (pid, boot, node.proc_start(pid) if start is None else start))
        if age:
            t = time.time() - age
            os.utime(os.path.join(lk, "pid"), (t, t))
    try:
        mkjob(J[0], {"cores": 1, "mem_bytes": 256 * MI, "time_s": 60})
        lock(holder)
        node.step(first=True)
        held = started() == [] and os.path.isdir(lk)
        shutil.rmtree(lk)
        node.step()
        check("the runner starts nothing while an update holds the start lock (a live process of this boot); once it "
              "is gone the job starts" + flag("runner-ignores-start-lock"), held and started() == [J[0]],
              (held, started()))
        mkjob(J[1], {"cores": 1, "mem_bytes": 256 * MI, "time_s": 60})
        lock(holder, boot="boot-before-the-restart")
        node.step()
        other_boot = J[1] in started() and not os.path.isdir(lk)
        mkjob(J[2], {"cores": 1, "mem_bytes": 256 * MI, "time_s": 60})
        lock(holder, age=node.START_LOCK_STALE_S + 60)
        node.step()
        old = J[2] in started()
        check("a lock naming a live pid but written in another boot, or not refreshed for START_LOCK_STALE_S, is stale: "
              "removed, and the jobs start" + flag("start-lock-pid-only"), other_boot and old, (other_boot, old, started()))
        if node.proc_start(holder):   # Linux: the pid's start time differs (a reused pid, or a thread id)
            mkjob(J[3], {"cores": 1, "mem_bytes": 256 * MI, "time_s": 60})
            lock(holder, start="1")
            node.step()
            check("a lock whose pid is now another process (start time differs) is stale" + flag("start-lock-pid-only"),
                  J[3] in started(), started())
        lock(holder, boot="boot-before-the-restart")
        node.clear_stale_start_lock()
        gone = not os.path.isdir(lk)
        lock(holder)
        node.clear_stale_start_lock()
        check("at the runner's start a stale lock is removed and a live one kept" + flag("start-lock-pid-only"),
              gone and os.path.isdir(lk), gone)
        shutil.rmtree(lk)
        # the default moved under the runner (au_switch dropped the lock right after): nothing more starts before
        # the runner restarts on the new default (a node of its own: on Linux J0-J3 hold every core by now)
        fresh("switched")
        mkjob(J[4], {"cores": 1, "mem_bytes": 256 * MI, "time_s": 60})
        os.makedirs(os.environ["MU_HOME"], exist_ok=True)
        with open(os.path.join(os.environ["MU_HOME"], "default"), "w") as f:
            f.write("v2\n")
        node.step(started="v1")
        check("once the default is not the one the runner started with, it starts nothing more (the new runner will)" +
              flag("starts-after-switch"), J[4] not in started() and not os.path.isdir(lk), started())
        node.step(started="v2")
        check("  the runner on the new default starts it", J[4] in started(), started())
    finally:
        node.boot_id = real_boot

    # 15. the parts of §20 the e2e rows Q23, Q24, Q26 leave out: move --after and --back, a held job as the
    # reference, a re-run passed by --front; an interrupted low job back at the front of low; one key, no owners file
    fresh("moves")
    mkjob(J[0], {"cores": 4, "mem_bytes": 256 * MI, "time_s": 600})
    for x in J[1:6]:
        mkjob(x, {"cores": 4, "mem_bytes": 256 * MI, "time_s": 60})
    node.step(first=True)   # J0 runs
    A, B, C, D, E = J[1:6]
    fr = "from=p1-0123456789ab"

    def order():
        return [x["id"] for x in node.walk_jobs(node.scan_jobs())]
    serve(node, "ci-owner", "move", "id=" + A, "after=" + C, fr)
    o1 = order()
    serve(node, "ci-owner", "move", "id=" + B, "to=back", fr)
    o2 = order()
    serve(node, "ci-owner", "hold", "id=" + D, fr)
    mh = serve(node, "ci-owner", "move", "id=" + E, "before=" + D, fr)
    o3 = order()
    check("move A --after C: B C A D E; B --back: C A D E B; E --before the held D: C A E D B" +
          flag("walk-by-accepted-ns"), o1 == [B, C, A, D, E] and o2 == [C, A, D, E, B] and o3 == [C, A, E, D, B] and
          mh.get("changed") is True, (o1, o2, o3, mh))
    node.interrupted(node.load_job(J[0]), "the node stopped")   # J0: attempt 2, at the front
    o4 = order()
    serve(node, "ci-owner", "move", "id=" + B, "to=front", fr)
    o5 = order()
    check("an interrupted job goes to the front (J0 C A E D B); a later move --front passes it (B J0 C ...): the "
          "re-run is a place in the order, not a tier" + flag("walk-by-accepted-ns"),
          o4 == [J[0], C, A, E, D, B] and o5 == [B, J[0], C, A, E, D], (o4, o5))
    fresh("lowband")
    mkjob(J[0], {"cores": 4, "mem_bytes": 256 * MI, "time_s": 600}, band="low")
    node.step(first=True)   # the low job runs
    mkjob(J[1], {"cores": 4, "mem_bytes": 256 * MI, "time_s": 60}, band="low")
    mkjob(J[2], {"cores": 4, "mem_bytes": 256 * MI, "time_s": 60})
    node.interrupted(node.load_job(J[0]), "the node stopped")
    o6 = order()
    check("an interrupted low job goes back to the front of low: behind the normal job, ahead of the other low one" +
          flag("requeue-new-ns"), o6 == [J[2], J[0], J[1]], o6)
    keys = os.path.join(base, "keys1")
    with open(keys, "w") as f:
        f.write("restrict ssh-ed25519 AAAAx avi-mac\n")
    old = dict((k, os.environ.get(k)) for k in ("MU_NODE_OWNERS", "MU_NODE_KEYS"))
    os.environ.update(MU_NODE_OWNERS=os.path.join(base, "no-owners-file"), MU_NODE_KEYS=keys)
    one = node.owners()
    with open(keys, "a") as f:
        f.write("restrict ssh-ed25519 AAAAy ci-other\n")
    two = node.owners()
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    check("no owners file and one key: that key is the owner; two keys: none" + flag("owner-from-node-json"),
          one == ["avi-mac"] and two == [], (one, two))

    # 13. a job whose shim wrote its result keeps its grant until its unit has ended (it may still be stopping
    # what the job left behind; review 2026-11-15)
    fresh("drain")
    mkjob(J[0], {"cores": 4, "mem_bytes": 256 * MI, "time_s": 60})
    node.step(first=True)
    node.write_result(node.load_job(J[0]), "succeeded", "shim", exit_code=0)
    mkjob(J[1], {"cores": 4, "mem_bytes": 256 * MI, "time_s": 60})
    busy = node.step()
    early = J[1] in started()
    unit_line(J[0], "inactive", "success")
    node.step()
    check("the next whole-node job waits while the finished job's unit is still stopping, and starts once it ended" +
          flag("free-on-result"), busy and not early and J[1] in started() and not violation(), (early, started()))

    # 14. mu compute update: --force (auto-update off, a release's failed tries) only for an owner key
    fresh("update")
    open(os.path.join(st["stub"], "units"), "w").close()
    u1 = serve(node, "ci-other", "update")
    a1 = open(os.path.join(st["stub"], "args-mu-update.service")).read().split()
    open(os.path.join(st["stub"], "units"), "w").close()
    u2 = serve(node, "ci-owner", "update")
    a2 = open(os.path.join(st["stub"], "args-mu-update.service")).read().split()
    check("mu compute update by another workstation's key runs `mu autoupdate run` without --force; by the owner's "
          "with it" + flag("update-always-force"), u1.get("forced") is False and "--force" not in a1 and
          a1[-2:] == ["autoupdate", "run"] and u2.get("forced") is True and a2[-1] == "--force", (u1, a1[-3:], a2[-3:]))

    # 12. the versions unfinished jobs need, for bin/mu's gc (review 2026-11-15): a held job alone leaves the node idle
    fresh("needed")
    for jid, ver, state in ((J[0], "v2026.11.8", "held"), (J[1], "v2026.11.9", "queued")):
        d = mkjob(jid, {"cores": 4, "mem_bytes": 256 * MI, "time_s": 60}, state=state)
        jj = durable.read_record(os.path.join(d, "job.json"))
        durable.write_record(os.path.join(d, "job.json"), dict(jj, mu_version=ver))
    node.step(first=True)
    node.write_result(node.load_job(J[1]), "succeeded", "shim", exit_code=0)
    unit_line(J[1], "inactive", "success")
    busy = node.step()
    need = open(node.needed_path()).read() if os.path.exists(node.needed_path()) else None
    check("with only a held job left the node is idle for updates, and $MU_HOME/state/needed-versions lists its "
          "version for gc" + flag("no-needed-versions"), busy is False and need == "v2026.11.8\n", (busy, need))


def serve(node, key, verb, *args, rc=0):
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        got = node.serve(["--key=" + key, verb] + list(args))
    try:
        out = json.loads(buf.getvalue().strip().splitlines()[-1])
    except (ValueError, IndexError):
        out = {"raw": buf.getvalue()}
    if got != rc:
        out["_rc"] = got
    return out
