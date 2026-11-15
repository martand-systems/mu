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

from mu import durable, proto, sched

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
            return [ln.split()[1][7:-8] for ln in open(os.path.join(st["stub"], "calls")) if ln.startswith("start mu-job-")]
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
    check("the unit: MemoryMax = the grant, RuntimeMaxSec = --time + min(4 h, --time) + 180 s (the backstop; release 2, room for a late thaw), MU_JOB_CPUS, thread variables, TMPDIR" +
          flag("mem-max-is-capacity") + flag("no-thread-env"),
          "MemoryMax=%d" % (512 * MI) in a0 and "RuntimeMaxSec=1380" in a0 and
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


# ------------------------------------------------------------------------------------------------ release 2: pause
PAUSE_SYSTEMCTL_STUB = r"""#!/bin/sh
# systemctl --user for the pause checks: units are lines "UNIT ACTIVESTATE RESULT" in $STUB_DIR/units (the last one
# counts); FreezerState in $STUB_DIR/fz-UNIT (default running); timers are files $STUB_DIR/timer-NAME; every call is
# logged in $STUB_DIR/calls
shift
echo "$*" >> "$STUB_DIR/calls"
case "$1" in
  show)
    line=$(grep "^$2 " "$STUB_DIR/units" 2>/dev/null | tail -1)
    if [ -z "$line" ]; then printf 'LoadState=not-found\nActiveState=inactive\nSubState=dead\nResult=success\n'; exit 0; fi
    fz=$(cat "$STUB_DIR/fz-$2" 2>/dev/null || echo running)
    set -- $line; printf 'LoadState=loaded\nActiveState=%s\nSubState=x\nResult=%s\nActiveEnterTimestampMonotonic=%s\nControlGroup=/user.slice/test/%s\nFreezerState=%s\n' "$2" "$3" "${4:-0}" "$1" "$fz" ;;
  freeze) [ -f "$STUB_DIR/freeze-fails" ] || echo frozen > "$STUB_DIR/fz-$2" ;;
  set-property) [ -f "$STUB_DIR/setprop-fails" ] && exit 1 ;;
  thaw) echo running > "$STUB_DIR/fz-$2" ;;
  is-active) if [ -f "$STUB_DIR/timer-$2" ]; then echo active; else echo inactive; fi ;;
  stop) rm -f "$STUB_DIR/timer-$2"
        case "$2" in mu-job-*) echo "$2 inactive success" >> "$STUB_DIR/units" ;; esac ;;
esac
exit 0
"""
PAUSE_RUN_STUB = r"""#!/bin/sh
# systemd-run --user: a timer (--on-active) makes $STUB_DIR/timer-UNIT.timer; a service is registered as active
t=""; for a in "$@"; do case "$a" in --unit=*) u="${a#--unit=}" ;; --on-active=*) t=1 ;; esac; done
echo "start $u $*" >> "$STUB_DIR/calls"
if [ -f "$STUB_DIR/run-fails-once" ]; then rm -f "$STUB_DIR/run-fails-once"; exit 1; fi
if [ -n "$t" ]; then : > "$STUB_DIR/timer-$u.timer"; exit 0; fi
echo "$u active success" >> "$STUB_DIR/units"
exit 0
"""


def t_sched_pause(base):
    """Release 2 (scheduler.md §20.5-§20.8, §22): lending by CPU-id sets in plan(), the invariants, the victim choice,
    and the runner and the verbs against stubs of systemd that keep a FreezerState per unit."""
    cap = cap_of(4, 2 * G)
    now = 1000.0
    # a frozen lender A (whole node, 1 GiB granted, holds 600 MiB), deadline now + 300
    A = run("A", [0, 1, 2, 3], 1 * G, 3600, now - 100)
    A["pause"] = {"phase": "frozen", "deadline": now + 300, "held_mem": 600 * MI}
    A["end"] = now + 300 + 3500 + sched.SLACK_S
    q = [{"id": "B", "req": req(2, 256 * MI, 20)}, {"id": "C", "req": req(2, 256 * MI, 600)}]
    pl = sched.plan(now, cap, [A], q)
    g = grants(pl)
    check("a frozen lender's cores: a 20 s job borrows 2 of them (class borrowing, until its deadline); a 10 min one "
          "does not" + flag("lend-without-deadline") + flag("borrow-past-deadline"),
          ids(pl) == ["B"] and g["B"]["class"] == "borrowing" and set(g["B"]["cpus"]) <= {0, 1, 2, 3} and
          g["B"]["borrowed"][0]["from"] == "A" and g["B"]["borrowed"][0]["until_mono"] == now + 300, pl["starts"])
    # memory: A lends 1 GiB - 600 MiB = 424 MiB; M = 2 GiB with 1 GiB more held by D (1 core... none left): no free
    D_ = run("D", [], 1 * G, 3600, now - 100)
    pl = sched.plan(now, cap, [A, D_], [{"id": "B", "req": req(2, 512 * MI, 20)}])
    check("a borrower that needs more memory than the lender lends and none is free waits" + flag("lend-whole-grant") +
          flag("borrower-mem-unchecked"), ids(pl) == [], pl["starts"])
    pl = sched.plan(now, cap, [A, D_], [{"id": "B", "req": req(2, 400 * MI, 20)}])
    check("one within the lent 424 MiB starts, all of its memory borrowed",
          ids(pl) == ["B"] and grants(pl)["B"]["borrowed"][0]["mem_bytes"] == 400 * MI, pl["starts"])
    check("plan's lent: 4 cores and 424 MiB lent by frozen jobs" + flag("lend-whole-grant"),
          pl["lent"]["cores"] == 4 and pl["lent"]["mem_bytes"] == 1 * G - 600 * MI, pl["lent"])
    # invariants
    Bg = dict(grants(sched.plan(now, cap, [A], [{"id": "B", "req": req(2, 256 * MI, 20)}]))["B"], active_mono=now)
    B = {"id": "B", "grant": Bg, "end": sched.end_bound(Bg)}
    check("invariants: a borrower inside a frozen lender's ids is fine", sched.invariants(cap, [A, B]) == [],
          sched.invariants(cap, [A, B]))
    A2 = dict(A, pause=None)
    bad = sched.invariants(cap, [A2, B])
    check("invariants: a borrower on a lender that is not frozen is a violation", any("not frozen" in x for x in bad), bad)
    Bl = dict(B, end=now + 400)
    bad = sched.invariants(cap, [A, Bl])
    check("invariants: a borrower that ends after its lender's deadline is a violation", any("deadline" in x for x in bad),
          bad)
    # a borrower whose lender has ended keeps its own CPU ids until its unit ends
    pl2 = sched.plan(now, cap, [B], [{"id": "C", "req": req(2, 256 * MI, 600)}])
    pl3 = sched.plan(now, cap, [B], [{"id": "C", "req": req(3, 256 * MI, 600)}])
    check("a borrower whose lender ended keeps its CPU ids: a 2-core job gets the other two, a 3-core one waits" +
          flag("lender-ends-ids-freed"), ids(pl2) == ["C"] and not set(grants(pl2)["C"]["cpus"]) & set(Bg["cpus"]) and
          ids(pl3) == [], (pl2["starts"], pl3["starts"]))
    # the auto-pause's victims
    c = [{"id": "x", "cores": 4, "est_lent": 1 * G, "active_mono": 1}, {"id": "y", "cores": 2, "est_lent": 0, "active_mono": 5},
         {"id": "z", "cores": 2, "est_lent": 100 * MI, "active_mono": 9}]
    check("victims: the smallest set by jobs then extra cores (y, 2 cores, for a 2-core job)",
          sched.choose_victims(c, 2, 0, 0) == ["z"], sched.choose_victims(c, 2, 0, 0))
    check("victims: the urgent job's memory must fit in free plus the set's lent memory" + flag("pause-without-mem-check"),
          sched.choose_victims(c, 2, 512 * MI, 0) == ["x"], sched.choose_victims(c, 2, 512 * MI, 0))

    # ---------------------------------------------------------------- the runner and the verbs, against stubs
    stubs = os.path.join(base, "bin")
    os.makedirs(stubs, exist_ok=True)
    for name, text in (("systemctl", PAUSE_SYSTEMCTL_STUB), ("systemd-run", PAUSE_RUN_STUB)):
        with open(os.path.join(stubs, name), "w") as f:
            f.write(text)
        os.chmod(os.path.join(stubs, name), 0o755)
    os.environ.update(MU_SYSTEMCTL=os.path.join(stubs, "systemctl"), MU_SYSTEMD_RUN=os.path.join(stubs, "systemd-run"),
                      MU_WSL_HOST_DRIVE="/nonexistent", MU_NODE_OWNERS=os.path.join(base, "owners"), MU_NODE_CHECK="1",
                      MU_CGROUP_ROOT=os.path.join(base, "cg"))
    with open(os.path.join(base, "owners"), "w") as f:
        f.write("ci-owner\n")
    from mu import node, pausectl
    node.capacity = lambda nd=None: sched.capacity(nd if nd is not None else node.node_info(), [0, 1, 2, 3], 64 * G)
    order = []

    def prep(j):
        order.append(("checkout", j["id"], len(calls())))
        shutil.rmtree(os.path.join(j["dir"], "work"), ignore_errors=True)
        os.mkdir(os.path.join(j["dir"], "work"))
    node.prepare_work = prep
    st = {}
    seq = [0]

    def fresh(tag, **conf):
        d = os.path.join(base, tag)
        for x in ("srv/jobs", "stub", "home/state"):
            os.makedirs(os.path.join(d, x))
        node.ROOT = os.path.join(d, "srv")
        os.environ["MU_HOME"] = os.path.join(d, "home")
        os.environ["STUB_DIR"] = st["stub"] = os.path.join(d, "stub")
        sc = {"cores": 4, "mem_bytes": 2 * G}
        sc.update(conf)
        durable.write_record(os.path.join(node.ROOT, "node.json"), {"name": "n", "min_free_gb": 0, "sched": sc,
                             "freezer": {"ok": True, "mem_lend": True, "boot_id": node.boot_id()}})
        node.STARTED_HERE.clear()
        pausectl.LIMIT_SEEN.clear()
        pausectl.DEAD_MAN_SEEN.clear()
        del order[:]

    def mkjob(jid, cores, mem, t, band=None, pausable=False, quick=False):
        seq[0] += 1
        d = node.job_dir(jid)
        os.makedirs(d)
        lim = {"cores": cores, "mem_bytes": mem, "time_s": t}
        if quick:
            lim = {"cores": None, "mem_bytes": None, "time_s": None, "quick": True}
        job = {"schema": 1, "sched": 1, "job_id": jid, "project_id": "p1-0123456789ab", "mu_version": "v1",
               "argv": ["true"], "cwd": "", "outputs": [], "limits": lim, "rerun": True, "accepted_ns": seq[0],
               "key": "ci-owner", "name": jid[-6:]}
        if band:
            job["priority"] = band
        if pausable:
            job["pausable"] = True
        durable.write_record(os.path.join(d, "job.json"), job)
        stt = {"state": "queued", "attempt": 1}
        if band:
            stt["band"] = band
        durable.write_record(os.path.join(d, "state.json"), stt)
        return d

    def calls():
        try:
            return open(os.path.join(st["stub"], "calls")).read().splitlines()
        except OSError:
            return []

    def job(jid):
        return node.load_job(jid)

    def pz(jid):
        return (durable.read_record(os.path.join(node.job_dir(jid), "state.json")) or {}).get("pause") or {}

    def setst(jid, **kw):
        p_ = os.path.join(node.job_dir(jid), "state.json")
        s_ = durable.read_record(p_)
        s_.update(kw)
        durable.write_record(p_, s_)

    def setpause(jid, **kw):
        s_ = durable.read_record(os.path.join(node.job_dir(jid), "state.json"))
        if s_.get("pause"):   # (absent under some faults: the checks that follow fail, they do not crash)
            setst(jid, pause=dict(s_["pause"], **kw))

    def fz(jid):
        try:
            return open(os.path.join(st["stub"], "fz-mu-job-%s.service" % jid)).read().strip()
        except OSError:
            return "running"

    def unit_end(jid, result="success"):
        with open(os.path.join(st["stub"], "units"), "a") as f:
            f.write("mu-job-%s.service inactive %s\n" % (jid, result))

    def memcur(jid, n):
        d = os.path.join(base, "cg", "user.slice", "test", "mu-job-%s.service" % jid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "memory.current"), "w") as f:
            f.write("%d\n" % n)

    def inv():
        try:
            return open(os.path.join(node.ROOT, "state", "invariant-violation")).read()
        except OSError:
            return ""

    def tryverb(f, args):
        try:
            return f("ci-owner", dict({"from": "p1-0123456789ab"}, **args))
        except node.NodeError as e:
            return {"ok": False, "error": e.error, "message": e.message, "rc": e.rc}

    J, K, L, U = "20261115-100000-aaaaaa", "20261115-100001-bbbbbb", "20261115-100002-cccccc", "20261115-100003-uuuuuu"

    # 1. pause, lend, resume
    fresh("pause")
    mkjob(J, 4, 1 * G, 600)
    node.step(first=True)
    node.step()
    memcur(J, 300 * MI)
    check("an ordinary job is started with a time-limit timer for its --time" + flag("count-paused-time"),
          any(c.startswith("start mu-limit-%s " % J) and "--on-active=600s" in c for c in calls()), calls())
    xdg = node.user_env()["XDG_RUNTIME_DIR"]
    check("its time-limit timer runs systemctl --user on this user's bus (XDG_RUNTIME_DIR %s and its bus named in the "
          "timer's service), not the user manager's environment" % xdg + flag("timer-env-inherited"),
          any(c.startswith("start mu-limit-%s " % J) and ("--setenv=XDG_RUNTIME_DIR=%s " % xdg) in c and
              ("--setenv=DBUS_SESSION_BUS_ADDRESS=unix:path=%s/bus " % xdg) in c for c in calls()), calls())
    a = tryverb(pausectl.verb_pause, {"id": J, "for": "300"})
    p0 = pz(J)
    check("mu pause: the unit frozen, the pause on disk (phase frozen, deadline, held = memory.current + 512 MiB)" +
          flag("pause-noop") + flag("pause-in-memory-only"),
          a.get("ok") and ("freeze mu-job-%s.service" % J) in calls() and p0.get("phase") == "frozen" and
          p0.get("held_mem_bytes") == 812 * MI and fz(J)
          == "frozen", (a, p0))
    check("mu pause: the limit timer stopped, the dead-man timer armed at the deadline + 120 s, MemoryMax lowered" +
          flag("no-dead-man"),
          ("stop mu-limit-%s.timer" % J) in calls() and any(c.startswith("start mu-thaw-%s " % J) for c in calls()) and
          any(c.startswith("set-property --runtime mu-job-%s.service MemoryMax=%d" % (J, 812 * MI)) for c in calls()),
          calls()[-12:])
    check("the dead-man timer too runs systemctl --user thaw on this user's bus, not the user manager's environment" +
          flag("timer-env-inherited"),
          any(c.startswith("start mu-thaw-%s " % J) and ("--setenv=XDG_RUNTIME_DIR=%s " % xdg) in c for c in calls()),
          [c for c in calls() if c.startswith("start mu-thaw-")])
    check("the pause answer's shape: paused, time, lent, next" + flag("drop-field"),
          all(k in a for k in ("paused", "time", "lent", "next")) and all(
              k in (a.get("paused") or {}) for k in ("since", "until", "by", "why", "held_mem_bytes", "lent_to",
                                                     "resume_asked", "resumes_by", "frozen")), a)
    mkjob(K, 2, 256 * MI, 20)
    mkjob(L, 2, 256 * MI, 600)
    node.step()
    kg = (job(K)["state"] or {}).get("grant") or {}
    check("a 20 s job borrows the paused job's cores and memory (class borrowing); a 10 min one waits" +
          flag("lend-without-deadline"),
          kg.get("class") == "borrowing" and (job(L)["state"] or {}).get("state") == "queued" and not inv(),
          (kg, (job(L)["state"] or {}).get("state"), inv()))
    s = node.status_of(job(J))
    check("status of the paused job: state running, paused (lent_to the borrower), time, the note" + flag("drop-field"),
          s["state"] == "running" and (s.get("paused") or {}).get("lent_to") == [K] and "time" in s and
          "paused until" in (s.get("note") or ""), s)
    r = tryverb(pausectl.verb_resume, {"id": J})
    check("resume while the borrower runs: resuming, resumes_by, still frozen" + flag("thaw-over-borrower") + flag("drop-field"),
          r.get("state") == "resuming" and "resumes_by" in r and pz(J).get("phase") == "frozen" and
          (pz(J).get("resume_asked") or {}).get("by") == "ci-owner", r)
    node.step()
    check("no sample shares a CPU between two unfrozen jobs" + flag("thaw-over-borrower"), not inv(), inv())
    unit_end(K)
    node.step()
    js = job(J)["state"] or {}
    check("the borrower ended: the lender is thawed on that pass, MemoryMax back to the grant, the pause in its history",
          not js.get("pause") and ("thaw mu-job-%s.service" % J) in calls() and
          any(c.startswith("set-property --runtime mu-job-%s.service MemoryMax=%d" % (J, G)) for c in calls()) and
          len(js.get("pauses") or []) == 1 and js.get("pause_count") == 1, js)
    check("after the thaw the limit timer is armed again with the time left",
          [c for c in calls() if c.startswith("start mu-limit-%s " % J)][-1:] != [] and
          os.path.exists(os.path.join(st["stub"], "timer-mu-limit-%s.timer" % J)), calls()[-6:])

    # 2. the deadline, re-pause raised to a borrower's end, the valve
    fresh("deadline")
    mkjob(J, 4, 1 * G, 3600)
    node.step(first=True)
    node.step()
    memcur(J, 300 * MI)
    tryverb(pausectl.verb_pause, {"id": J, "for": "200"})
    mkjob(K, 2, 256 * MI, 20)
    node.step()
    kend = pausectl.end_bound(job(K)["state"])
    a = tryverb(pausectl.verb_pause, {"id": J, "for": "1"})
    check("a re-pause while a borrower runs is raised to the borrower's end (deadline_raised_to)" + flag("repause-shortens"),
          a.get("ok") and "deadline_raised_to" in a and pz(J).get("deadline_mono") >= kend - 0.01, (a, pz(J), kend))
    setpause(J, deadline_mono=time.monotonic() - 61)
    node.step()
    check("the valve: a borrower alive past the lender's deadline + 60 s is killed, the lender thawed with MemoryMax "
          "still at held (restored once the borrower's unit ended)" + flag("no-valve"),
          any(c.startswith("kill --signal=SIGKILL mu-job-%s" % K) for c in calls()) and not pz(J) and
          (job(J)["state"] or {}).get("mem_restore_pending"), (pz(J), calls()[-6:]))
    unit_end(K)
    node.step()
    check("memory restored once the borrower's unit ended", not (job(J)["state"] or {}).get("mem_restore_pending"),
          job(J)["state"])
    tryverb(pausectl.verb_pause, {"id": J, "for": "200"})
    setpause(J, deadline_mono=time.monotonic() - 1)
    node.step()
    check("a pause ends at its deadline (no borrower)" + flag("no-pause-deadline"), not pz(J), pz(J))

    # 3. limits and refusals
    a = tryverb(pausectl.verb_pause, {"id": J, "for": "8000"})
    check("a pause over 2 h is refused pause-limit", a.get("error") == "pause-limit", a)
    fresh("limits")
    mkjob(J, 4, 1 * G, 600)
    node.step(first=True)
    node.step()
    a = tryverb(pausectl.verb_pause, {"id": J, "for": "700"})
    check("a pause past the budget (min(4 h, --time)) is refused pause-limit with the figures",
          a.get("error") == "pause-limit" and "600" in a.get("message", ""), a)
    mkjob(K, 1, 256 * MI, 600, quick=True)
    node.step()
    a = tryverb(pausectl.verb_pause, {"id": K})
    check("an express job is never paused", a.get("error") == "pause-limit", a)
    setst(J, grant=dict(job(J)["state"]["grant"], limit_timer=False))
    a = tryverb(pausectl.verb_pause, {"id": J})
    check("a job started without a limit timer (the previous release) is refused", a.get("error") == "pause-limit", a)
    a = tryverb(pausectl.verb_pause, {"id": L})
    check("pausing a job the node does not have: no-such-job", a.get("error") == "no-such-job", a)
    mkjob(L, 4, 1 * G, 600)
    a = tryverb(pausectl.verb_pause, {"id": L})
    check("pausing a queued job: not-running", a.get("error") == "not-running", a)

    # 4. cancel of a paused job thaws first; the result's run_s and paused_s; a re-queue drops the pause
    fresh("cancel")
    mkjob(J, 4, 1 * G, 600)
    node.step(first=True)
    node.step()
    tryverb(pausectl.verb_pause, {"id": J, "for": "200"})
    n0 = len(calls())
    pausectl.stop_unit("mu-job-%s.service" % J)
    cc = calls()[n0:]
    ti = [i for i, c in enumerate(cc) if c.startswith("thaw mu-job-")]
    si = [i for i, c in enumerate(cc) if c.startswith("stop mu-job-")]
    check("cancel of a paused job: thawed, then stopped (it gets its SIGTERM)" + flag("cancel-without-thaw") +
          flag("stop-without-thaw"), ti and si and ti[0] < si[0], cc)
    fresh("result")
    mkjob(J, 4, 1 * G, 600)
    node.step(first=True)
    j = job(J)
    s2 = dict(j["state"], started_epoch=time.time() - 35, paused_total_s=15.0)
    j["state"] = s2
    res = node.write_result(j, "timeout", "runner")
    check("result.json: wall 35 s, paused 15 s -> run_s 20, paused_s 15, pauses" + flag("count-paused-time"),
          abs((res.get("run_s") or 0) - 20) < 1.5 and abs((res.get("paused_s") or 0) - 15) < 0.5 and "pauses" in res, res)
    fresh("requeue")
    mkjob(J, 4, 1 * G, 600)
    node.step(first=True)
    node.step()
    tryverb(pausectl.verb_pause, {"id": J, "for": "200"})
    node.interrupted(job(J), "the node stopped")
    js = job(J)["state"]
    check("a re-queued attempt (VM stop while paused) drops the pause; the note says it was paused" +
          flag("requeue-keeps-pause"), js.get("state") == "queued" and not js.get("pause") and
          "was paused" in (js.get("note") or ""), js)

    # 5. the time-limit marker; a job with a result is never kept frozen; frozen outside μ; thawed outside μ
    fresh("timelimit")
    mkjob(J, 4, 1 * G, 600)
    node.step(first=True)
    node.step()
    setst(J, paused_total_s=840.0)
    durable.write_durable(os.path.join(node.job_dir(J), "timelimit"), "x\n")
    unit_end(J, "success")
    node.step()
    r = job(J)["result"] or {}
    check("the limit timer's marker: timeout, 'not counting 14m paused'", r.get("state") == "timeout" and
          "not counting 14m" in (r.get("note") or ""), r)
    fresh("after-result")
    mkjob(J, 4, 1 * G, 600)
    node.step(first=True)
    node.step()
    tryverb(pausectl.verb_pause, {"id": J, "for": "200"})
    j = job(J)
    durable.write_record(os.path.join(j["dir"], "result.json"), {"schema": 1, "job_id": J, "state": "succeeded"})
    node.step()
    check("a unit frozen after the shim wrote its result is thawed at once" + flag("freeze-after-result"),
          fz(J) == "running", calls()[-5:])
    fresh("outside")
    mkjob(J, 4, 1 * G, 600)
    node.step(first=True)
    node.step()
    tryverb(pausectl.verb_pause, {"id": J, "for": "200"})
    with open(os.path.join(st["stub"], "fz-mu-job-%s.service" % J), "w") as f:
        f.write("running\n")   # the dead-man timer thawed it while the runner was down
    node.step()
    ev = open(os.path.join(node.job_dir(J), "events.log")).read()
    check("frozen in state.json but running in systemd: 'thawed outside μ', the pause closed, the limit re-armed",
          not pz(J) and "thawed outside" in ev and os.path.exists(os.path.join(st["stub"], "timer-mu-limit-%s.timer" % J)),
          (pz(J), ev[-300:]))
    with open(os.path.join(st["stub"], "fz-mu-job-%s.service" % J), "w") as f:
        f.write("frozen\n")
    node.step()
    check("frozen in systemd with no pause recorded: thawed", fz(J) == "running", calls()[-4:])
    if os.path.exists(os.path.join(st["stub"], "timer-mu-limit-%s.timer" % J)):
        os.unlink(os.path.join(st["stub"], "timer-mu-limit-%s.timer" % J))
    n0 = len(calls())
    for _ in range(3):
        node.step()
    isact = [c for c in calls()[n0:] if c.startswith("is-active mu-limit-")]
    check("the limit timer is asked of systemd once per runner process and thaw, not on every pass (3 passes: no "
          "is-active)" + flag("limit-check-each-pass"), not isact, isact)
    pausectl.LIMIT_SEEN.clear()   # as after a restart of the runner
    node.step()
    check("an unfrozen job whose limit timer is gone gets one again on the runner's next start" +
          flag("limit-in-runner-loop"),
          os.path.exists(os.path.join(st["stub"], "timer-mu-limit-%s.timer" % J)), calls()[-4:])

    # 6. auto-pause
    for f_, marked, last in (("marked", True, None), ("unmarked", False, None), ("cooldown", True, 60)):
        fresh("auto-" + f_)
        mkjob(J, 4, 1 * G, 3600, pausable=marked)
        node.step(first=True)
        node.step()
        memcur(J, 300 * MI)
        if last:
            setst(J, last_thaw_mono=time.monotonic() - last)
        del order[:]
        mkjob(U, 2, 256 * MI, 60, band="urgent")
        n0 = len(calls())
        node.step()
        ug = (job(U)["state"] or {}).get("grant") or {}
        if f_ == "marked":
            cc = calls()[n0:]
            fi = [i for i, c in enumerate(cc) if c.startswith("freeze ")]
            check("auto-pause: a --pausable job is frozen for an urgent job, which starts on its lent cores in the same "
                  "pass", pz(J).get("why") == "auto" and pz(J).get("for_job") == U and
                  ug.get("class") == "borrowing", (pz(J), ug))
            check("auto-pause: the urgent job's checkout comes before the freeze" + flag("freeze-before-checkout"),
                  [o[:2] for o in order[:1]] == [("checkout", U)] and fi and fi[0] + n0 >= order[0][2],
                  (order, cc[:8]))
            durable.write_record(os.path.join(node.job_dir(U), "result.json"), {"schema": 1, "job_id": U,
                                                                                  "state": "succeeded"})
            unit_end(U)
            node.step()
            check("auto-pause: the victim is thawed as soon as the urgent job ended" + flag("keep-pause-after-beneficiary"),
                  not pz(J), pz(J))
        elif f_ == "unmarked":
            check("auto-pause: a job not submitted --pausable is never paused (policy marked)" + flag("pause-unmarked"),
                  not pz(J) and not ug, (pz(J), ug))
        else:
            check("auto-pause: no second pause within 15 min of running since the last thaw" + flag("no-cooldown"),
                  not pz(J) and not ug, (pz(J), ug))

    # 7. the review of 2026-11-15 (scheduler.md §22.5), each check with the fault that undoes its fix
    def timer_on(name):
        return os.path.exists(os.path.join(st["stub"], "timer-%s.timer" % name))

    def set_fz(jid, v):
        with open(os.path.join(st["stub"], "fz-mu-job-%s.service" % jid), "w") as f:
            f.write(v + "\n")

    def finish(jid):   # the shim wrote its result; its unit is still active (stopping what the job left)
        durable.write_record(os.path.join(node.job_dir(jid), "result.json"), {"schema": 1, "job_id": jid,
                                                                                "state": "succeeded"})

    def freezing(jid, since, deadline):
        setst(jid, pause={"phase": "freezing", "since_mono": since, "deadline_mono": deadline, "boot_id": node.boot_id(),
                          "by": "ci-owner", "why": "hand", "for_job": None, "held_mem_bytes": None,
                          "resume_asked": None})

    # a freeze cut short after systemd froze the unit, its deadline passed: finished and thawed at once, counted
    fresh("rv-freezing")
    mkjob(J, 4, 1 * G, 600)
    node.step(first=True)
    node.step()
    memcur(J, 300 * MI)
    now = time.monotonic()
    freezing(J, now - 90, now - 5)
    node.systemctl("stop", "mu-limit-%s.timer" % J)
    set_fz(J, "frozen")
    node.step()
    js = job(J)["state"] or {}
    check("a freeze cut short after systemd froze the unit ('freezing' on disk, frozen in systemd) is finished, then "
          "thawed at its deadline on the same pass, its 90 s counted as paused" + flag("freezing-left"),
          fz(J) == "running" and not js.get("pause") and 85 <= float(js.get("paused_total_s") or 0) <= 100,
          (fz(J), js.get("pause"), js.get("paused_total_s")))
    fresh("rv-freezing-dropped")
    mkjob(J, 4, 1 * G, 600)
    node.step(first=True)
    node.step()
    now = time.monotonic()
    freezing(J, now - 90, now - 5)
    n0 = len(calls())
    node.step()
    cc = calls()[n0:]
    check("a 'freezing' record dropped (never frozen, or thawed since): MemoryMax back to the grant, the dead-man "
          "timer stopped" + flag("freezing-left"),
          not pz(J) and any(c.startswith("set-property --runtime mu-job-%s.service MemoryMax=%d" % (J, G)) for c in cc)
          and ("stop mu-thaw-%s.timer" % J) in cc, cc)
    fresh("rv-refreeze")
    mkjob(J, 4, 1 * G, 600)
    node.step(first=True)
    node.step()
    now = time.monotonic()
    freezing(J, now, now + 300)
    node.step()
    check("a freeze re-done after a crash before its step 2 stops the time-limit timer (it would fire while frozen)" +
          flag("refreeze-keeps-limit"), fz(J) == "frozen" and not timer_on("mu-limit-%s" % J),
          (fz(J), timer_on("mu-limit-%s" % J)))

    # two urgent jobs in one pass: the second's auto-pause is not defeated by the first, counted only when it works
    U1, U2, F = "20261115-100004-uuuuu1", "20261115-100005-uuuuu2", "20261115-095959-filler"
    fresh("rv-two-urgent")
    mkjob(J, 2, 1 * G, 3600, pausable=True)
    node.step(first=True)
    node.step()
    mkjob(F, 2, 1 * G, 3600)
    node.step()
    memcur(J, 300 * MI)
    unit_end(F)
    finish(F)
    node.step()
    mkjob(U1, 2, 256 * MI, 60, band="urgent")
    mkjob(U2, 2, 256 * MI, 60, band="urgent")
    node.step()
    u1, u2 = job(U1)["state"] or {}, job(U2)["state"] or {}
    check("two urgent jobs in one pass: the first starts on free cores, the second auto-pauses the --pausable job and "
          "starts on its lent cores" + flag("replan-sees-started"),
          u1.get("state") == "running" and (u2.get("grant") or {}).get("class") == "borrowing" and
          pz(J).get("for_job") == U2, (u1.get("state"), u2.get("grant"), pz(J)))
    fresh("rv-rollback")
    mkjob(J, 4, 1 * G, 3600, pausable=True)
    node.step(first=True)
    node.step()
    memcur(J, 300 * MI)
    mkjob(U, 2, 256 * MI, 60, band="urgent")
    real_start = node.start_job
    node.start_job = lambda *a, **k: False   # the urgent job's start fails after the freeze
    try:
        node.step()
    finally:
        node.start_job = real_start
    js = job(J)["state"] or {}
    check("an auto-pause whose urgent job did not start: the victim thawed and the attempt not counted (no "
          "auto_pause_count, no cooldown)" + flag("replan-sees-started"),
          not js.get("pause") and not js.get("auto_pause_count") and js.get("last_thaw_mono") is None and
          fz(J) == "running", js)

    # release 1's drain rule in release 2's paths: a job with a result holds its grant until its unit ended
    X = "20261115-100006-xxxxxx"
    fresh("rv-drain-a", mem_bytes=8 * G)
    mkjob(X, 2, 1 * G, 3600)
    mkjob(J, 2, 1 * G, 3600, pausable=True)
    node.step(first=True)
    node.step()
    memcur(J, 300 * MI)
    finish(X)
    mkjob(U, 2, 256 * MI, 60, band="urgent")
    node.step()
    xc = set(((job(X)["state"] or {}).get("grant") or {}).get("cpus") or [])
    uc = set(((job(U)["state"] or {}).get("grant") or {}).get("cpus") or [])
    check("the auto-pause's re-plan keeps a draining unit's CPU ids (the urgent job borrows the victim's, not X's)" +
          flag("pause-ignores-drain"), uc and not (xc & uc) and (job(U)["state"] or {}).get("grant", {}).get(
              "class") == "borrowing", (sorted(xc), sorted(uc)))
    fresh("rv-drain-b")
    mkjob(J, 4, 2 * G, 3600)
    node.step(first=True)
    node.step()
    memcur(J, 300 * MI)
    tryverb(pausectl.verb_pause, {"id": J, "for": "300"})
    mkjob(K, 2, 1 * G, 20)
    node.step()
    tryverb(pausectl.verb_resume, {"id": J})
    finish(K)
    n0 = len(calls())
    node.step()
    cc = calls()[n0:]
    check("a lender is not thawed (nor its MemoryMax restored) while its borrower's unit drains after its result" +
          flag("pause-ignores-drain"), fz(J) == "frozen" and not any(
              c.startswith("set-property --runtime mu-job-%s.service MemoryMax=%d" % (J, 2 * G)) for c in cc),
          [c for c in cc if J in c])
    unit_end(K)
    node.step()
    check("... and thawed once the borrower's unit ended", fz(J) == "running" and not pz(J), pz(J))

    # a borrower admitted for ending before S is checked against S again after its checkout
    fresh("rv-borrow-S")
    mkjob(J, 4, 1 * G, 3600)
    node.step(first=True)
    node.step()
    memcur(J, 300 * MI)
    tryverb(pausectl.verb_pause, {"id": J, "for": "600"})
    D_ = pz(J).get("deadline_mono")
    mkjob(K, 2, 256 * MI, 20)
    g_ = {"cores": 2, "cpus": [0, 1], "mem_bytes": 256 * MI, "time_s": 20, "class": "borrowing", "env_cores": 2,
          "borrowed": [{"from": J, "cpus": [0, 1], "mem_bytes": 0, "until_mono": D_}]}
    n0 = len(calls())
    node.start_job(job(K), g_, time.monotonic() + 30)   # the checkout took long: it would now end 60 s after S
    check("a borrower whose free memory was admitted for ending before S does not start when, after its checkout, it "
          "would end after S" + flag("borrower-ignores-shadow"),
          (job(K)["state"] or {}).get("state") == "queued" and not any(c.startswith("start mu-job-%s" % K)
                                                                        for c in calls()[n0:]), job(K)["state"])
    now = 1000.0
    A3 = run("A", [0, 1, 2, 3], 1 * G, 3600, now - 100)
    A3["pause"] = {"phase": "frozen", "deadline": now + 300, "held_mem": 600 * MI}
    A3["end"] = now + 300 + 3500 + sched.SLACK_S
    hq = {"id": "H", "req": req(4, 1536 * MI, 600)}
    p1 = sched.plan(now, cap, [A3], [hq, {"id": "B", "req": req(2, 1000 * MI, 20)}])
    p2 = sched.plan(now, cap, [A3], [hq, {"id": "B", "req": req(2, 400 * MI, 20)}])
    s1 = next((x for x in p1["starts"] if x["id"] == "B"), {})
    s2 = next((x for x in p2["starts"] if x["id"] == "B"), {})
    check("plan(): a borrower's start carries S only when its free memory was admitted by ending before S",
          s1.get("S") is not None and "S" in s2 and s2.get("S") is None, (s1, s2))

    # a frozen job always has its dead-man timer
    fresh("rv-deadman")
    mkjob(J, 4, 1 * G, 3600)
    node.step(first=True)
    node.step()
    tryverb(pausectl.verb_pause, {"id": J, "for": "300"})
    open(os.path.join(st["stub"], "run-fails-once"), "w").close()
    a = tryverb(pausectl.verb_pause, {"id": J, "for": "600"})
    check("a renewal whose dead-man re-arm failed is refused pause-failed, the old deadline and its timer kept" +
          flag("no-dead-man-rearm"), a.get("error") == "pause-failed" and timer_on("mu-thaw-%s" % J) and
          abs((pz(J).get("deadline_mono") or 0) - time.monotonic() - 300) < 5, (a, pz(J)))
    if timer_on("mu-thaw-%s" % J):
        os.unlink(os.path.join(st["stub"], "timer-mu-thaw-%s.timer" % J))
    node.step()
    check("a frozen job whose dead-man timer is gone gets it again on the next pass" + flag("no-dead-man-rearm"),
          timer_on("mu-thaw-%s" % J), calls()[-5:])
    n0 = len(calls())
    node.step()
    node.step()
    check("... asked of systemd once per deadline, not every pass",
          not [c for c in calls()[n0:] if c.startswith("is-active mu-thaw-")], calls()[n0:])

    # an owner's pause is sticky: the job's own workstation cannot resume or shorten it
    fresh("rv-sticky")
    mkjob(J, 4, 1 * G, 7200)
    setjob = durable.read_record(os.path.join(node.job_dir(J), "job.json"))
    setjob["key"] = "ws2"
    durable.write_record(os.path.join(node.job_dir(J), "job.json"), setjob)
    node.step(first=True)
    node.step()
    tryverb(pausectl.verb_pause, {"id": J, "for": "1800"})

    def as_ws2(f, args, key="ws2"):
        try:
            return f(key, dict({"from": "p1-0123456789ab"}, **args))
        except node.NodeError as e:
            return {"ok": False, "error": e.error, "rc": e.rc}
    r1 = as_ws2(pausectl.verb_pause, {"id": J, "for": "1"})
    r2 = as_ws2(pausectl.verb_resume, {"id": J})
    check("an owner's pause of another workstation's job: that workstation's re-pause and resume are refused "
          "not-allowed (owner acts are sticky, §20.4)" + flag("pause-not-sticky"),
          r1.get("error") == "not-allowed" and r2.get("error") == "not-allowed" and fz(J) == "frozen" and
          pz(J).get("by") == "ci-owner", (r1, r2, pz(J)))
    as_ws2(pausectl.verb_resume, {"id": J, "others": "1"}, key="ws3")
    aud = open(os.path.join(node.ROOT, "audit.log")).read()
    check("pause and resume audit lines carry (owner), from= and others=1 like the arranging verbs" +
          flag("audit-terse-pause"),
          any("queue: pause id=%s by=ci-owner (owner) from=p1-0123456789ab for=1800s ok" % J in x for x in
              aud.splitlines()) and "refused resume id=%s by=ws3 from=p1-0123456789ab others=1 not-allowed" % J in aud,
          aud[-600:])

    # accounting: an open pause ends at most at D + 120 s; the backstop leaves room for a late thaw
    fresh("rv-open")
    mkjob(J, 4, 1 * G, 3600)
    node.step(first=True)
    node.step()
    tryverb(pausectl.verb_pause, {"id": J, "for": "300"})
    now = time.monotonic()
    setpause(J, since_mono=now - 1520, deadline_mono=now - 1520 + 300)
    ps = pausectl.paused_s(job(J)["state"])
    check("a frozen record's open pause counts at most to its deadline + 120 s (the dead-man thawed it then)" +
          flag("open-pause-uncapped"), abs(ps - 420) < 2, ps)
    from mu import nodestats
    rs = nodestats.run_seconds(job(J)["state"], 2000.0)
    check("... and the dashboard's run time says the same (nodestats.run_seconds)" + flag("open-pause-uncapped"),
          abs(rs - 1580) < 2, rs)
    fresh("rv-backstop")
    mkjob(J, 4, 1 * G, 600)
    node.step(first=True)
    a0 = next((c for c in calls() if c.startswith("start mu-job-%s" % J)), "")
    check("RuntimeMaxSec leaves room past t + budget for a thaw after the deadline (dead-man D + 120 s)" +
          flag("backstop-no-room"), "RuntimeMaxSec=%d" % (600 + 600 + node.BACKSTOP_ROOM_S) in a0 and
          node.BACKSTOP_ROOM_S >= node.DEAD_MAN_S + 30, a0[-400:])

    # a failed MemoryMax restore is retried; renewals; the count of pauses; the limit command's quoting
    fresh("rv-memrestore")
    mkjob(J, 4, 1 * G, 3600)
    node.step(first=True)
    node.step()
    memcur(J, 300 * MI)
    tryverb(pausectl.verb_pause, {"id": J, "for": "300"})
    open(os.path.join(st["stub"], "setprop-fails"), "w").close()
    tryverb(pausectl.verb_resume, {"id": J})
    check("a thaw whose MemoryMax restore failed leaves mem_restore_pending (retried each pass)" +
          flag("ignore-mem-restore-failure"), (job(J)["state"] or {}).get("mem_restore_pending") is True,
          job(J)["state"])
    node.step()
    still = (job(J)["state"] or {}).get("mem_restore_pending")
    os.unlink(os.path.join(st["stub"], "setprop-fails"))
    node.step()
    check("... still pending while set-property fails, cleared once it works" + flag("ignore-mem-restore-failure"),
          still is True and not (job(J)["state"] or {}).get("mem_restore_pending"), (still, job(J)["state"]))
    fresh("rv-renew")
    mkjob(J, 4, 1 * G, 6 * 3600)
    node.step(first=True)
    node.step()
    tryverb(pausectl.verb_pause, {"id": J, "for": "7200"})
    setpause(J, since_mono=time.monotonic() - 7080)
    a = tryverb(pausectl.verb_pause, {"id": J, "for": "7200"})
    b = tryverb(pausectl.verb_pause, {"id": J, "for": "100"})
    check("a renewal may not make one pause longer than 2 h: --for 7200 at 1h58m refused pause-limit, --for 100 "
          "accepted" + flag("renewal-over-2h"), a.get("error") == "pause-limit" and b.get("ok"), (a, b))
    fresh("rv-count")
    mkjob(J, 4, 1 * G, 6 * 3600)
    node.step(first=True)
    node.step()
    setst(J, pause_count=node.PAUSE_COUNT_MAX)
    a = tryverb(pausectl.verb_pause, {"id": J, "for": "60"})
    check("hand pauses per attempt are capped (%d): the next is refused pause-limit" % node.PAUSE_COUNT_MAX +
          flag("no-pause-count-cap"), a.get("error") == "pause-limit" and "times" in a.get("message", ""), a)
    import shlex
    sh = pausectl.limit_command(J)[2]
    words = shlex.split(sh.split(";")[0])
    check("the limit command quotes the job's folder (a node root with a space: one argument)" +
          flag("limit-command-unquoted"), words[-1] == os.path.join(node.job_dir(J), "timelimit") and
          " " in node.ROOT, (words, node.ROOT))

    # the freezer's fallbacks (review 2026-11-15, tests 8): a failed self-check, and MemoryMax that cannot be lowered
    fresh("rv-noselftest")
    durable.write_record(os.path.join(node.ROOT, "node.json"), dict(
        durable.read_record(os.path.join(node.ROOT, "node.json")), freezer={"ok": False, "mem_lend": False,
                                                                           "boot_id": node.boot_id()}))
    mkjob(J, 4, 1 * G, 3600)
    node.step(first=True)
    node.step()
    h = node.hello_answer("ci-owner", node.node_info())
    a = tryverb(pausectl.verb_pause, {"id": J, "for": "60"})
    check("a failed freezer self-check: no pause in hello.features, and mu pause is refused pause-unavailable (node "
          "exit %d, the workstation's 6)" % proto.NODE_UNAVAILABLE + flag("pause-without-selftest"),
          "pause" not in h["features"] and a.get("error") == "pause-unavailable" and a.get("rc") ==
          proto.NODE_UNAVAILABLE and fz(J) == "running", (h["features"], a))
    fresh("rv-nomemlend")
    mkjob(J, 4, 1 * G, 3600)
    node.step(first=True)
    node.step()
    memcur(J, 300 * MI)
    open(os.path.join(st["stub"], "setprop-fails"), "w").close()
    a = tryverb(pausectl.verb_pause, {"id": J, "for": "300"})
    os.unlink(os.path.join(st["stub"], "setprop-fails"))
    check("MemoryMax cannot be lowered on the frozen unit: the pause holds, it keeps its whole grant and lends cores "
          "only (lent memory 0)" + flag("lend-unlowered-mem"),
          a.get("ok") and pz(J).get("held_mem_bytes") == G and (a.get("lent") or {}).get("mem_bytes") == 0 and
          (a.get("lent") or {}).get("cores") == 4, (a.get("lent"), pz(J)))
    # auto-pauses per attempt: at most AUTO_PAUSE_MAX, however long since the last thaw (tests review, 9)
    fresh("rv-auto-max")
    mkjob(J, 4, 1 * G, 3600, pausable=True)
    node.step(first=True)
    node.step()
    memcur(J, 300 * MI)
    setst(J, auto_pause_count=node.AUTO_PAUSE_MAX, last_thaw_mono=time.monotonic() - 2 * node.AUTO_COOLDOWN_S)
    mkjob(U, 2, 256 * MI, 60, band="urgent")
    node.step()
    check("auto-pause: a job auto-paused %d times in this attempt is not paused again, the cooldown long past" %
          node.AUTO_PAUSE_MAX + flag("no-cooldown"), not pz(J) and not (job(U)["state"] or {}).get("grant"),
          (pz(J), job(U)["state"]))
    # an auto-pause charges the victim's budget with its own length, not the 30-minute most (CI, Q37: a 10-minute
    # job could never be auto-paused)
    fresh("rv-auto-budget")
    mkjob(J, 4, 1 * G, 600, pausable=True)
    node.step(first=True)
    node.step()
    memcur(J, 300 * MI)
    mkjob(U, 2, 256 * MI, 60, band="urgent")
    node.step()
    check("auto-pause: a --pausable 10-minute job (budget 600 s) is paused for a 60 s urgent job (its pause: 160 s)" +
          flag("auto-charges-max"), pz(J).get("for_job") == U and
          ((job(U)["state"] or {}).get("grant") or {}).get("class") == "borrowing", (pz(J), job(U)["state"]))
    # a job paused before the runner recorded its cgroup: memory.current read from the unit's ControlGroup
    fresh("rv-early-pause")
    mkjob(J, 4, 1 * G, 3600)
    node.step(first=True)
    setst(J, cgroup=None)
    memcur(J, 300 * MI)
    a = tryverb(pausectl.verb_pause, {"id": J, "for": "300"})
    check("a pause before the runner recorded the job's cgroup still lowers MemoryMax (the cgroup from systemd)" +
          flag("cgroup-from-record-only"), a.get("ok") and pz(J).get("held_mem_bytes") == 812 * MI, (a.get("lent"), pz(J)))
