"""μ node scheduler, releases 1 and 2 (docs/design/scheduler.md §3, §4, §20.3, §20.4, §20.6, §20.7): the pure part. Capacity, sizing of a
request (resolve), the queue's walk (bands, explicit order, held jobs), the plan of one runner pass (admission by cores
and memory, a reservation for the head with EASY backfill, the express lane), the invariants every pass must keep, and
the authorisation rule for arranging jobs. No file, clock or systemd access here: node.py reads the state files and
passes them in, so every rule is unit-tested without systemd (tools/test-jobs/unit.py sched, sched-sim). Standard
library only.

Test faults (durable.fault; each must make a unit test or an e2e row fail, scheduler.md §13):
  one-at-a-time           nothing ordinary starts beside a running ordinary job (the previous release's rule)
  no-core-admission       cores are not counted (CPU ids are handed out again)
  no-mem-admission        memory is not counted
  backfill-ignores-shadow a later job that fits now starts whatever its time (no test against S, in the plan nor in
                          the runner's re-check after the checkout)
  no-head-reservation     a head that does not fit is skipped (no reservation at all)
  margin-zero             the time test leaves out START_WAIT + SLACK
  extra-not-reduced       a job started into `extra` does not use it up
  cpus-reused             CPU ids of a start are not taken out of the free set (the next start gets the same ids)
  cpus-from-plan-cache    the same, as the e2e row names it
  express-from-pool       express jobs take cores and memory from the ordinary pool, ignoring the reservation
  no-express              --quick jobs are ordinary (no lane)
  express-by-size         a small explicit request (1 core, ≤ 512 MiB, ≤ 10 min) is express
  legacy-sized            an old workstation's job is sized by its --cores (not the whole node)
  grantless-counts-zero   a running job without a grant holds nothing (instead of the whole node)
  hold-ignored            held jobs are walked as queued
  walk-by-accepted-ns     the explicit order (queue.json) is ignored
  bands-ignored           priority bands are ignored
  no-auth-check           every key may arrange every job
  owner-acts-not-sticky   a job's own workstation may undo an owner's hold or band
  no-others-guard         jobs of other projects may be arranged without others=1
  no-size-bounds          a request's sizes are not bounded (MAX_*): a huge --time overflows the plan
release 2 (§20.6, §20.7):
  lend-without-deadline   a borrower may take a frozen job's cores whatever its time (no test against the deadline D)
  lend-whole-grant        a frozen job lends its whole memory grant, not grant - held
  borrow-past-deadline    the same as lend-without-deadline, as the simulation names it
  borrower-mem-unchecked  a borrower's memory beyond what its lenders lend is not checked against free memory
  lender-ends-ids-freed   a borrower whose lender ended holds no ids of its own (they are handed out again)
  pause-without-mem-check the auto-pause chooses victims by cores only (the urgent job's memory is not checked)
"""
import itertools

from mu.durable import fault

BANDS = ("urgent", "high", "normal", "low")
DEFAULT_RESERVE_SYSTEM = 2 << 30
DEFAULT_RESERVE_EXPRESS = 1 << 30
EXPRESS = {"cores": 1, "mem_bytes": 512 << 20, "time_s": 600}
EXPRESS_MAX = 2
SLACK_S = 10            # TimeoutStopSec=5 and a tick
START_WAIT_S = 60       # the systemd-run timeout
MARGIN_S = START_WAIT_S + SLACK_S
VALVE_S = 60            # a unit this long past its end bound opens the valve: no time-based backfill
DEFAULT_TIME_S = 12 * 3600
MIN_MEM = 256 << 20
# the largest sizes a spec may carry (node.check_spec, and resolve() for jobs already on disk): beyond them the plan's
# float arithmetic and the wall-clock times of status overflow (security review, 2026-11-15)
MAX_CORES = 4096
MAX_MEM_BYTES = 1 << 50
MAX_TIME_S = 365 * 86400


class TooBig(Exception):
    """A request larger than the node (scheduler.md §4.3)."""
    error = "too-big"

    def __init__(self, what, cap):
        Exception.__init__(self, what)
        self.what, self.cap = what, cap


class BadSpec(TooBig):
    """A job on disk whose request cannot be worked out (it fails; the pass goes on with the others)."""
    error = "bad-spec"


def _pos_int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else None


def _nonneg_int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def capacity(node, cpus, mem_total):
    """C, M and the reserves (§3.1). CPUS: the ids this runner may use (os.sched_getaffinity(0)); MEM_TOTAL: bytes.
    node.json `sched` may lower C (cores), M (mem_bytes) and set the reserves."""
    s = node.get("sched") if isinstance(node.get("sched"), dict) else {}
    ids = sorted(cpus)
    c = _pos_int(s.get("cores"))
    if c and c < len(ids):
        ids = ids[:c]
    rs = _nonneg_int(s.get("reserve_system"))
    rs = DEFAULT_RESERVE_SYSTEM if rs is None else rs
    re_ = _nonneg_int(s.get("reserve_express"))
    re_ = DEFAULT_RESERVE_EXPRESS if re_ is None else re_
    m = mem_total - rs - re_
    if _pos_int(s.get("mem_bytes")):
        m = min(m, s["mem_bytes"])
    return {"cores": len(ids), "cpus": ids, "mem_bytes": max(MIN_MEM, m), "reserve_system": rs,
            "reserve_express": re_, "express_max": EXPRESS_MAX}


def human_gb(n):
    return "%.1f GB" % (n / float(1 << 30))


def human_dur(s):
    s = int(s)
    if s % 3600 == 0 and s >= 3600:
        return "%dh" % (s // 3600)
    if s >= 3600:
        return "%dh%02dm" % (s // 3600, (s % 3600) // 60)
    if s % 60 == 0 and s >= 60:
        return "%dm" % (s // 60)
    return "%ds" % s


def resolve(spec, cap):
    """The request of a job (§4.2), a pure function of its spec and the capacity: {cores, mem_bytes, time_s, class
    ("express" | "normal"), env_cores (MU_JOB_CORES), source {cores, mem, time}, why}. Raises TooBig."""
    lim = spec.get("limits") if isinstance(spec.get("limits"), dict) else {}
    c, m = cap["cores"], cap["mem_bytes"]
    fc, fm, ft = _pos_int(lim.get("cores")), _pos_int(lim.get("mem_bytes")), _pos_int(lim.get("time_s"))
    if not fault("no-size-bounds"):
        for flag_, v, mx in (("--cores", fc, MAX_CORES), ("--mem", fm, MAX_MEM_BYTES), ("--time", ft, MAX_TIME_S)):
            if v is not None and v > mx:
                raise TooBig("%s %d > %d (the largest a job may ask for)" % (flag_, v, mx), cap)
    new_ws = bool(spec.get("sched"))
    if new_ws and lim.get("quick") is True and not fault("no-express"):
        return {"cores": 1, "mem_bytes": EXPRESS["mem_bytes"], "time_s": EXPRESS["time_s"], "class": "express",
                "env_cores": 1, "source": {"cores": "quick", "mem": "quick", "time": "quick"},
                "why": "--quick: express lane, 1 CPU's worth, 512 MB, 10 min"}
    if new_ws and lim.get("quick") is True:   # fault no-express: an ordinary 1-core job
        fc, fm, ft = 1, EXPRESS["mem_bytes"], EXPRESS["time_s"]
    if new_ws or fault("legacy-sized"):
        cores, mem, t = fc or c, fm or m, ft or DEFAULT_TIME_S
        src = {"cores": "flag" if fc else "default", "mem": "flag" if fm else "default", "time": "flag" if ft else "default"}
        env = cores
    else:   # an old workstation (no sched in the spec): the whole node, as the previous release ran it (§11)
        cores, mem, t = c, fm or m, ft or DEFAULT_TIME_S
        src = {"cores": "legacy", "mem": "flag" if fm else "default", "time": "flag" if ft else "default"}
        env = fc or c
    if cores > c or mem > m:
        raise TooBig("%s%s" % ("--cores %d > %d" % (cores, c) if cores > c else "",
                               ("; " if cores > c and mem > m else "") + ("--mem %s > %s" % (human_gb(mem), human_gb(m))
                                                                          if mem > m else "")), cap)
    cls = "normal"
    if fault("express-by-size") and cores == 1 and mem <= EXPRESS["mem_bytes"] and t <= EXPRESS["time_s"]:
        cls = "express"
    if src["cores"] == "default" and src["mem"] == "default":
        why = "no flags: whole node for up to %s; it runs alone" % human_dur(t)
    elif src["cores"] == "legacy":
        why = "from a workstation older than the scheduler: the whole node, as before"
    else:
        why = "%d cores, %s, %s (%s)" % (cores, human_gb(mem), human_dur(t), ", ".join(
            "%s %s" % (k, v) for k, v in sorted(src.items())))
    return {"cores": cores, "mem_bytes": mem, "time_s": t, "class": cls, "env_cores": env, "source": src, "why": why}


def band_of(state, job):
    b = (state or {}).get("band") or (job or {}).get("priority") or "normal"
    return b if b in BANDS else "normal"


def walk(items, order):
    """The queue's walk (§20.3): ITEMS = [{id, band, accepted_ns, held}], ORDER = queue.json's list (or None).
    -> ITEMS sorted by (band, index in ORDER (unlisted: after every listed job of the band), accepted_ns)."""
    idx = {} if fault("walk-by-accepted-ns") else {x: i for i, x in enumerate(order or [])}
    n = len(idx)

    def key(it):
        b = 2 if fault("bands-ignored") else BANDS.index(it.get("band") if it.get("band") in BANDS else "normal")
        return (b, idx.get(it["id"], n), it.get("accepted_ns") or 0, it["id"])
    return sorted(items, key=key)


def end_bound(grant):
    """The latest CLOCK_MONOTONIC second a running job's unit can still hold its grant (§3.2), or None."""
    t = grant.get("time_s")
    if not isinstance(t, (int, float)) or isinstance(t, bool) or not 0 <= t <= MAX_TIME_S:
        return None
    if isinstance(grant.get("active_mono"), (int, float)):
        return grant["active_mono"] + t + SLACK_S
    if isinstance(grant.get("start_mono"), (int, float)):
        return grant["start_mono"] + START_WAIT_S + t + SLACK_S
    return None


def fmt_cpus(ids):
    """[0,1,2,3,8] -> '0-3,8'"""
    ids = sorted(set(ids))
    out, i = [], 0
    while i < len(ids):
        j = i
        while j + 1 < len(ids) and ids[j + 1] == ids[j] + 1:
            j += 1
        out.append("%d" % ids[i] if i == j else "%d-%d" % (ids[i], ids[j]))
        i = j + 1
    return ",".join(out)


def parse_cpus(text):
    """'0-3,8' -> [0,1,2,3,8]; None on anything else."""
    out = []
    try:
        for part in (text or "").strip().split(","):
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                out.extend(range(int(a), int(b) + 1))
            else:
                out.append(int(part))
    except ValueError:
        return None
    return sorted(set(out))


def plan(now, cap, running, queue, disk=None):
    """One pass's decisions (§3.3, §3.4, §20.3). Pure.
      NOW      CLOCK_MONOTONIC seconds
      RUNNING  [{id, grant (dict or None: holds the whole node), end (end bound or None)}]
      QUEUE    the walk: [{id, held, req (resolve()'s dict) or error (TooBig)}]
      DISK     None, or {free_gb, min_free_gb, where}: nothing starts
    -> {starts: [{id, grant, S}], fail: [(id, TooBig)], waiting: {id: waiting_for}, head, S, S_why,
        starts_by: {id: S or None}, position: {id: n (among jobs that can start)}, place: {id: n (in the walk)},
        free: {cores, cpus, mem_bytes}, allocated: {...}, express: n}"""
    M, ids_all = cap["mem_bytes"], list(cap["cpus"])
    occupied, used_mem, ex_n, ex_mem, grantless, ends = set(), 0, 0, 0, [], []
    lenders, lent0 = lending(running), {"cores": set(), "mem_bytes": 0, "b_cores": set(), "b_mem": 0}
    alive = {r["id"] for r in running}
    for r in running:
        g = r.get("grant")
        if not g:
            grantless.append(r["id"])
            continue
        if g.get("class") == "express":
            ex_n += 1
            ex_mem += g.get("mem_bytes") or 0
            continue
        cpus = set(g.get("cpus") or [])
        if fault("lender-ends-ids-freed") and g.get("class") == "borrowing" and not any(
                b.get("from") in alive for b in g.get("borrowed") or []):
            cpus = set()
        occupied |= cpus
        mem, cores = firm_mem(g, alive), len(cpus) or g.get("cores") or 0
        if g.get("class") == "borrowing":
            for b in g.get("borrowed") or []:
                if b.get("from") in alive:
                    lent0["b_cores"] |= set(b.get("cpus") or [])
                    lent0["b_mem"] += b.get("mem_bytes") or 0
            cores = len([c for c in cpus if not any(c in (b.get("cpus") or []) and b.get("from") in alive
                                                    for b in g.get("borrowed") or [])])
        used_mem += mem
        ends.append((r.get("end"), cores, mem))
    for lid, L in lenders.items():
        lent0["cores"] |= L["all_ids"]
        lent0["mem_bytes"] += L["lent_mem"]
    free_ids = [c for c in ids_all if c not in occupied]
    free_mem = M - used_mem
    if grantless and not fault("grantless-counts-zero"):
        free_ids, free_mem = [], 0
    out = {"starts": [], "fail": [], "waiting": {}, "head": None, "S": None, "S_why": None, "starts_by": {},
           "position": {}, "place": {}, "express": ex_n,
           "allocated": {"cores": len(occupied), "cpus": sorted(occupied), "mem_bytes": used_mem},
           "lent": {"cores": len(lent0["cores"]), "cpus": sorted(lent0["cores"]), "mem_bytes": lent0["mem_bytes"],
                    "borrowed": {"cores": len(lent0["b_cores"]), "cpus": sorted(lent0["b_cores"]),
                                 "mem_bytes": lent0["b_mem"]}}}
    no_admit_c, no_admit_m = fault("no-core-admission"), fault("no-mem-admission")
    reuse = fault("cpus-reused") or fault("cpus-from-plan-cache")
    one = fault("one-at-a-time")
    margin = 0 if fault("margin-zero") else MARGIN_S
    state = {"ids": free_ids, "mem": free_mem, "ordinary_running": bool(ends or grantless)}

    def fits(req):
        if one and state["ordinary_running"]:
            return False
        okc = no_admit_c or req["cores"] <= len(state["ids"])
        okm = no_admit_m or req["mem_bytes"] <= state["mem"]
        return okc and okm

    def take(req, cls, S=None):
        n = req["cores"]
        ids = state["ids"][:n]
        if len(ids) < n:   # no-core-admission: hand out ids again
            ids = (ids + [c for c in ids_all if c not in ids])[:n]
        if not reuse:
            state["ids"] = [c for c in state["ids"] if c not in ids]
        state["mem"] -= req["mem_bytes"]
        state["ordinary_running"] = True
        ends.append((now + START_WAIT_S + req["time_s"] + SLACK_S, n, req["mem_bytes"]))   # its bound once started
        return {"cores": n, "cpus": ids, "mem_bytes": req["mem_bytes"], "time_s": req["time_s"], "class": cls,
                "env_cores": req.get("env_cores", n)}

    def borrow(req, rest_ok):
        """§20.6: REQ's cores all from frozen lenders whose deadline D leaves room for its time (now + t + MARGIN <=
        D); its memory from those lenders' lent memory first, the rest from free memory if REST_OK(rest). -> a grant
        of class "borrowing", or None. Express-sized and whole-node requests borrow like any other."""
        n, t = req["cores"], req["time_s"]
        no_d = fault("lend-without-deadline") or fault("borrow-past-deadline")
        cands = sorted(((lid, L) for lid, L in lenders.items() if L["ids"] and (no_d or now + t + margin <= L["D"])),
                       key=lambda x: (-len(x[1]["ids"]), x[0]))
        took, left = [], n
        for lid, L in cands:
            if left <= 0:
                break
            ids = sorted(L["ids"])[:left]
            took.append((lid, ids))
            left -= len(ids)
        if left > 0 or not took:
            return None
        need_m, parts = req["mem_bytes"], []
        for lid, ids in took:
            m = min(need_m, max(0, lenders[lid]["mem"]))
            parts.append(m)
            need_m -= m
        if need_m > 0 and not fault("borrower-mem-unchecked") and not no_admit_m and (
                need_m > state["mem"] or not rest_ok(need_m, t)):
            return None
        bl = []
        for (lid, ids), m in zip(took, parts):
            lenders[lid]["ids"] -= set(ids)
            lenders[lid]["mem"] -= m
            bl.append({"from": lid, "cpus": ids, "mem_bytes": m, "until_mono": lenders[lid]["D"]})
        state["mem"] -= max(0, need_m)
        ends.append((now + START_WAIT_S + t + SLACK_S, 0, max(0, need_m)))
        return {"cores": n, "cpus": sorted(c for _, ids in took for c in ids), "mem_bytes": req["mem_bytes"],
                "time_s": t, "class": "borrowing", "env_cores": req.get("env_cores", n), "borrowed": bl}

    # place (in the walk, held jobs counted) and position (among jobs that can start)
    pos = 0
    for i, q in enumerate(queue):
        out["place"][q["id"]] = i + 1
        if not q.get("held"):
            pos += 1
            out["position"][q["id"]] = pos
    if disk:
        for q in queue:
            out["waiting"][q["id"]] = {"held": True} if q.get("held") else {"disk": dict(disk)}
            if not q.get("held"):
                out["starts_by"].setdefault(q["id"], None)
        out["S_why"] = "disk"
        out["free"] = {"cores": len(free_ids), "cpus": list(free_ids), "mem_bytes": max(0, free_mem)}
        return out

    # 0 + 2: requests, failures, the express lane in walk order
    rest = []
    for q in queue:
        if q.get("held") and not fault("hold-ignored"):
            out["waiting"][q["id"]] = {"held": True}
            continue
        if q.get("error") is not None:
            out["fail"].append((q["id"], q["error"]))
            continue
        req = q["req"]
        if req["class"] == "express":
            if fault("express-from-pool"):
                if fits(req):
                    g = take(req, "normal")   # counted in the pool, past any reservation
                    out["starts"].append({"id": q["id"], "grant": g, "S": None})
                    continue
            elif ex_n < EXPRESS_MAX and ex_mem + req["mem_bytes"] <= cap["reserve_express"]:
                ex_n += 1
                ex_mem += req["mem_bytes"]
                g = {"cores": 1, "cpus": list(ids_all), "mem_bytes": req["mem_bytes"], "time_s": req["time_s"],
                     "class": "express", "env_cores": 1}
                out["starts"].append({"id": q["id"], "grant": g, "S": None})
                continue
            req = dict(req, **{"class": "normal"})   # lane full: an ordinary 1-core, 512 MiB, 10-minute job
        rest.append((q, req))
    out["express"] = ex_n

    # 3: the head starts while it fits, on free cores or (§20.6) on cores lent by frozen jobs
    i = 0
    while i < len(rest):
        q, req = rest[i]
        if fits(req):
            out["starts"].append({"id": q["id"], "grant": take(req, "normal"), "S": None})
        else:
            g = borrow(req, lambda m, t: True) if lenders else None
            if g is None:
                break
            out["starts"].append({"id": q["id"], "grant": g, "S": None})
        i += 1
    if fault("no-head-reservation"):
        for q, req in rest[i:]:
            if fits(req):
                out["starts"].append({"id": q["id"], "grant": take(req, "backfilled"), "S": None})
            else:
                out["waiting"][q["id"]] = {"resources": need(req, state, cap)}
        out["free"] = {"cores": len(state["ids"]), "cpus": list(state["ids"]), "mem_bytes": max(0, state["mem"])}
        return out
    if i < len(rest):
        hq, hreq = rest[i]
        out["head"] = hq["id"]
        S, why, extra_c, extra_m = None, None, 0, 0
        if grantless and not fault("grantless-counts-zero"):
            why = "grantless"
        else:
            # S: the earliest end bound at which, releasing running jobs in end order, the head fits; extra: what is
            # free then beyond the head's request (cores are counted: the ids are identical slots, §3.3)
            avail_c, avail_m = len(state["ids"]), state["mem"]
            for e, c, m in sorted(ends, key=lambda x: (x[0] is None, x[0] or 0)):
                avail_c += c
                avail_m += m
                if avail_c >= hreq["cores"] and avail_m >= hreq["mem_bytes"]:
                    S = e
                    break
            if avail_c >= hreq["cores"] and avail_m >= hreq["mem_bytes"]:
                extra_c, extra_m = avail_c - hreq["cores"], avail_m - hreq["mem_bytes"]
            if S is None:
                why = "unknown-end"
            elif now > S + VALVE_S:
                why = "valve"   # a unit outlived its time limit: no promise, no time-based backfill (§3.3 step 6)
        S_time = S if why is None else None
        out["S"], out["S_why"] = S_time, why
        out["starts_by"][hq["id"]] = S_time
        out["waiting"][hq["id"]] = {"resources": need(hreq, state, cap)}
        # 5: later jobs
        def borrow_later(q, req):
            by_s = []

            def rest_ok(m, t):   # memory beyond what is lent: within extra, or ends before S (step 5 of §3.3)
                nonlocal extra_m
                if m <= extra_m:
                    extra_m -= m
                    return True
                by_s.append(True)
                return S_time is not None and now + t + margin <= S_time
            g = borrow(req, rest_ok)
            if g is None:
                return False
            # S only for a start admitted by ending before S: start_job() checks it again after the checkout, as it
            # does a backfilled job's (review 2026-11-15)
            out["starts"].append({"id": q["id"], "grant": g, "S": S_time if by_s else None})
            return True

        for q, req in rest[i + 1:]:
            fits_now = fits(req)
            if fits_now and req["cores"] <= extra_c and req["mem_bytes"] <= extra_m:
                out["starts"].append({"id": q["id"], "grant": take(req, "normal"), "S": None})
                if not fault("extra-not-reduced"):
                    extra_c -= req["cores"]
                    extra_m -= req["mem_bytes"]
            elif fits_now and (fault("backfill-ignores-shadow") or (
                    S_time is not None and now + req["time_s"] + margin <= S_time)):
                out["starts"].append({"id": q["id"], "grant": take(req, "backfilled"), "S": S_time})
            elif lenders and borrow_later(q, req):
                pass
            elif not fits_now:
                out["waiting"][q["id"]] = {"resources": dict(need(req, state, cap), behind=hq["id"])}
            else:
                res = {"head": hq["id"], "starts_by": S_time}
                if S_time is not None and S_time - now - margin >= 1:
                    res["fits_if_time_s_le"] = int(S_time - now - margin)
                out["waiting"][q["id"]] = {"reservation": res}
    out["free"] = {"cores": len(state["ids"]), "cpus": list(state["ids"]), "mem_bytes": max(0, state["mem"])}
    return out


def firm_mem(g, alive):
    """The memory a running ordinary job's grant counts in firm memory (§20.6): its whole grant (a frozen job's too),
    less what a borrower borrowed from a lender that still runs (that part is the lender's)."""
    m = g.get("mem_bytes") or 0
    if g.get("class") == "borrowing":
        m -= sum(b.get("mem_bytes") or 0 for b in g.get("borrowed") or [] if b.get("from") in alive)
    return max(0, m)


def lending(running):
    """§20.6, by CPU-id sets: {lender id: {ids (lendable now), all_ids, D, mem (lendable now), lent_mem}} for each
    running ordinary job whose pause phase is "frozen" (only then is anything lent). RUNNING items carry
    pause = {phase, deadline, held_mem} (deadline on this boot's monotonic clock)."""
    out = {}
    for r in running:
        g, pz = r.get("grant") or {}, r.get("pause") or {}
        if pz.get("phase") != "frozen" or g.get("class") not in ("normal", "backfilled") or \
                not isinstance(pz.get("deadline"), (int, float)):
            continue
        held = pz.get("held_mem") if isinstance(pz.get("held_mem"), int) else (g.get("mem_bytes") or 0)
        lent = (g.get("mem_bytes") or 0) if fault("lend-whole-grant") else max(0, (g.get("mem_bytes") or 0) - held)
        ids = set(g.get("cpus") or [])
        out[r["id"]] = {"ids": set(ids), "all_ids": ids, "D": pz["deadline"], "mem": lent, "lent_mem": lent}
    for r in running:
        g = r.get("grant") or {}
        if g.get("class") != "borrowing":
            continue
        for b in g.get("borrowed") or []:
            L = out.get(b.get("from"))
            if L:
                L["ids"] -= set(b.get("cpus") or [])
                L["mem"] -= b.get("mem_bytes") or 0
    return out


def choose_victims(cands, need_cores, need_mem, free_mem, max_set=4):
    """§20.7 "which jobs": CANDS = eligible running jobs [{id, cores, est_lent, active_mono}]. The smallest set by
    (number of jobs, cores beyond the need), ties by the latest active_mono, then job id, whose cores cover NEED_CORES
    and whose estimated lent memory plus FREE_MEM covers NEED_MEM. -> [ids] or None."""
    best = None
    for n in range(1, min(max_set, len(cands)) + 1):
        for combo in itertools.combinations(cands, n):
            c = sum(x["cores"] for x in combo)
            if c < need_cores:
                continue
            if not fault("pause-without-mem-check") and free_mem + sum(max(0, x["est_lent"]) for x in combo) < need_mem:
                continue
            key = (n, c - need_cores, -max(x.get("active_mono") or 0 for x in combo), sorted(x["id"] for x in combo))
            if best is None or key < best[0]:
                best = (key, [x["id"] for x in combo])
        if best:
            break
    return sorted(best[1]) if best else None


def need(req, state, cap):
    return {"cores": req["cores"], "mem_bytes": req["mem_bytes"], "free_cores": len(state["ids"]),
            "free_mem_bytes": max(0, state["mem"])}


def invariants(cap, running):
    """What every pass must keep (§7.1 step(), MU_NODE_CHECK): RUNNING = [{id, grant, started_epoch, held}].
    -> a list of violations (empty: fine)."""
    bad = []
    C, M = cap["cores"], cap["mem_bytes"]
    cores = mem = ex = ex_mem = 0
    seen = {}
    grantless = [r for r in running if not r.get("grant")]
    by_id = {r["id"]: r for r in running}
    alive = set(by_id)
    lenders = lending(running)
    occ = set()
    for r in running:
        if r.get("held"):
            bad.append("held job %s is running" % r["id"])
        g = r.get("grant")
        if not g:
            continue
        if g.get("class") == "express":
            ex += 1
            ex_mem += g.get("mem_bytes") or 0
            continue
        mem += firm_mem(g, alive)
        shared = set()
        if g.get("class") == "borrowing":   # §20.6: a borrower's ids lie inside frozen lenders' ids, until its D
            for b in g.get("borrowed") or []:
                L = by_id.get(b.get("from"))
                if L is None:
                    continue
                lg, lp = L.get("grant") or {}, L.get("pause") or {}
                for c in b.get("cpus") or []:
                    if c not in (lg.get("cpus") or []):
                        bad.append("CPU %d borrowed by %s is not %s's" % (c, r["id"], b.get("from")))
                    elif lp.get("phase") != "frozen":
                        bad.append("CPU %d of %s shared with %s, which is not frozen" % (c, r["id"], b.get("from")))
                    shared.add(c)
                end, D = r.get("end"), b.get("until_mono")
                if isinstance(end, (int, float)) and isinstance(D, (int, float)) and end > D + 0.5:
                    bad.append("borrower %s ends at %.0f, after %s's deadline %.0f" % (r["id"], end, b.get("from"), D))
        for c in g.get("cpus") or []:
            occ.add(c)
            if c in shared:
                continue
            if c in seen:
                bad.append("CPU %d granted to %s and %s" % (c, seen[c], r["id"]))
            seen[c] = r["id"]
            if c not in cap["cpus"]:
                bad.append("CPU %d of %s is outside the capacity" % (c, r["id"]))
        for gl in grantless:
            if (r.get("started_epoch") or 0) > (gl.get("started_epoch") or 0):
                bad.append("%s started beside %s, which has no grant" % (r["id"], gl["id"]))
    cores = len(occ)
    for lid, L in lenders.items():
        if L["mem"] < 0:
            bad.append("%s lends %d bytes, %d borrowed" % (lid, L["lent_mem"], L["lent_mem"] - L["mem"]))
    if cores > C:
        bad.append("%d cores granted > %d" % (cores, C))
    if mem > M:
        bad.append("%d bytes granted > %d" % (mem, M))
    if ex > EXPRESS_MAX:
        bad.append("%d express jobs > %d" % (ex, EXPRESS_MAX))
    if ex_mem > cap["reserve_express"]:
        bad.append("express memory %d > %d" % (ex_mem, cap["reserve_express"]))
    return bad


# ------------------------------------------------------------------------------------------------ queue control
def place_after(w, jid, how, ref=None, band=None):
    """The walk W (list of {id, band, ...}) after moving JID: how = before|after (REF) | front|back (its band, or
    BAND) | priority (BAND: the back of it). -> (new walk with JID's band set, new band)."""
    j = next(x for x in w if x["id"] == jid)
    rest = [x for x in w if x["id"] != jid]
    if how in ("before", "after"):
        k = next(i for i, x in enumerate(rest) if x["id"] == ref)
        nb = rest[k]["band"]
        at = k if how == "before" else k + 1
    else:
        nb = band or j["band"]
        same = [i for i, x in enumerate(rest) if x["band"] == nb]
        lower = [i for i, x in enumerate(rest) if BANDS.index(x["band"]) > BANDS.index(nb)]
        if how == "front" or (how == "priority" and fault("priority-to-front")):
            at = same[0] if same else (lower[0] if lower else len(rest))
        else:   # back, priority
            at = same[-1] + 1 if same else (lower[0] if lower else len(rest))
    nj = dict(j, band=nb)
    return rest[:at] + [nj] + rest[at:], nb


def passed(old, new, jid):
    """The jobs JID now stands ahead of that were ahead of it before."""
    oi = [x["id"] for x in old]
    ni = [x["id"] for x in new]
    before_old = set(oi[:oi.index(jid)])
    after_new = set(ni[ni.index(jid) + 1:])
    return [x for x in old if x["id"] in before_old & after_new]


def authorise(op, job, key, owners, from_project, others, passes=(), new_band=None):
    """May KEY (claiming project FROM_PROJECT, OTHERS true for --others) do OP to JOB (§20.4)?
    JOB: {key, project_id, held_by, band_by}; PASSES: the jobs OP puts JOB ahead of ({key, project_id}).
    -> None, or (error, message): ("not-allowed", …) or ("other-project", …)."""
    owner = key in owners
    if not fault("no-auth-check"):
        own = job.get("key") == key
        if new_band == "urgent" and not owner:
            return "not-allowed", "only an owner key may use the urgent band"
        if not own and not owner:
            return "not-allowed", "the job belongs to workstation %s" % job.get("key")
        if not owner and not fault("owner-acts-not-sticky"):
            if op == "release" and job.get("held_by") in owners:
                return "not-allowed", "held by the owner %s: only an owner key may release it" % job.get("held_by")
            if (op == "priority" or (op == "move" and new_band != job.get("band"))) and job.get("band_by") in owners:
                return "not-allowed", "its band was set by the owner %s" % job.get("band_by")
        other_ws = sorted({p.get("key") for p in passes if p.get("key") != key})
        if other_ws and not owner:
            return "not-allowed", "it would pass jobs of workstation %s" % ", ".join(str(x) for x in other_ws)
    if not others and not fault("no-others-guard"):
        if job.get("project_id") != from_project:
            return "other-project", "the job belongs to project %s" % job.get("project_id")
        other_p = sorted({p.get("project_id") for p in passes if p.get("project_id") != from_project})
        if other_p:
            return "other-project", "it would pass %d job(s) of %s" % (
                len([p for p in passes if p.get("project_id") != from_project]), ", ".join(str(x) for x in other_p))
    return None
