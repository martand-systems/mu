"""μ node scheduler, release 1 (docs/design/scheduler.md §3, §4, §20.3, §20.4): the pure part. Capacity, sizing of a
request (resolve), the queue's walk (bands, explicit order, held jobs), the plan of one runner pass (admission by cores
and memory, a reservation for the head with EASY backfill, the express lane), the invariants every pass must keep, and
the authorisation rule for arranging jobs. No file, clock or systemd access here: node.py reads the state files and
passes them in, so every rule is unit-tested without systemd (tools/test-jobs/unit.py sched, sched-sim). Standard
library only.

Test faults (durable.fault; each must make a unit test or an e2e row fail, scheduler.md §13):
  one-at-a-time           nothing ordinary starts beside a running ordinary job (the previous release's rule)
  no-core-admission       cores are not counted (CPU ids are handed out again)
  no-mem-admission        memory is not counted
  backfill-ignores-shadow a later job that fits now starts whatever its time (no test against S)
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
"""
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


class TooBig(Exception):
    """A request larger than the node (scheduler.md §4.3)."""

    def __init__(self, what, cap):
        Exception.__init__(self, what)
        self.what, self.cap = what, cap


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
    if not isinstance(t, (int, float)):
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
    C, M, ids_all = cap["cores"], cap["mem_bytes"], list(cap["cpus"])
    occupied, used_mem, ex_n, ex_mem, grantless, ends = set(), 0, 0, 0, [], []
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
        occupied |= cpus
        used_mem += g.get("mem_bytes") or 0
        ends.append((r.get("end"), len(cpus) or g.get("cores") or 0, g.get("mem_bytes") or 0))
    free_ids = [c for c in ids_all if c not in occupied]
    free_mem = M - used_mem
    if grantless and not fault("grantless-counts-zero"):
        free_ids, free_mem = [], 0
    out = {"starts": [], "fail": [], "waiting": {}, "head": None, "S": None, "S_why": None, "starts_by": {},
           "position": {}, "place": {}, "express": ex_n,
           "allocated": {"cores": len(occupied), "cpus": sorted(occupied), "mem_bytes": used_mem}}
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
        return {"cores": n, "cpus": ids, "mem_bytes": req["mem_bytes"], "time_s": req["time_s"], "class": cls,
                "env_cores": req.get("env_cores", n)}

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

    # 3: the head starts while it fits
    i = 0
    while i < len(rest) and fits(rest[i][1]):
        q, req = rest[i]
        out["starts"].append({"id": q["id"], "grant": take(req, "normal"), "S": None})
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
            elif not fits_now:
                out["waiting"][q["id"]] = {"resources": dict(need(req, state, cap), behind=hq["id"])}
            else:
                res = {"head": hq["id"], "starts_by": S_time}
                if S_time is not None and S_time - now - margin >= 1:
                    res["fits_if_time_s_le"] = int(S_time - now - margin)
                out["waiting"][q["id"]] = {"reservation": res}
    out["free"] = {"cores": len(state["ids"]), "cpus": list(state["ids"]), "mem_bytes": max(0, state["mem"])}
    return out


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
        cores += len(g.get("cpus") or [])
        mem += g.get("mem_bytes") or 0
        for c in g.get("cpus") or []:
            if c in seen:
                bad.append("CPU %d granted to %s and %s" % (c, seen[c], r["id"]))
            seen[c] = r["id"]
            if c not in cap["cpus"]:
                bad.append("CPU %d of %s is outside the capacity" % (c, r["id"]))
        for gl in grantless:
            if (r.get("started_epoch") or 0) > (gl.get("started_epoch") or 0):
                bad.append("%s started beside %s, which has no grant" % (r["id"], gl["id"]))
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
