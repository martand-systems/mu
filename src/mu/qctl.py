"""μ queue control and the scheduler's words, workstation side (docs/design/scheduler.md §7.2, §20.2, §20.9, §20.12):
  mu queue [--on NODE] [--json] [--mine]            the whole node's queue, in the order jobs start
  mu hold JOB / mu release JOB                      keep a queued job in its place without starting it / let it go
  mu move JOB --before J2 | --after J2 | --front | --back
  mu priority JOB urgent|high|normal|low
  mu compute update NODE [--wait] [--timeout S]     install the latest μ release on the node, beside running jobs
Every arranging command takes --on NODE, --json and --others (arrange another project's job: only when the owner
asked). The node decides who may do what (the job's workstation or an owner key) and the --others guard, under its
queue lock. Standard library only."""
import os
import time

from mu import proto
from mu.proto import MuError

ARRANGE = ("hold", "release", "move", "priority")


def gb(n):
    return "%.1f GB" % (n / float(1 << 30)) if isinstance(n, (int, float)) else "?"


def hhmm(iso):
    """'2026-11-14T14:20:00Z' -> '14:20' (local time); None -> None."""
    if not iso:
        return None
    try:
        import calendar
        t = calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, TypeError):
        return str(iso)
    return time.strftime("%H:%M" if abs(t - time.time()) < 20 * 3600 else "%d %b %H:%M", time.localtime(t))


def dur(s):
    s = int(s or 0)
    return "%dh%02dm" % (s // 3600, (s % 3600) // 60) if s >= 3600 else ("%dm" % (s // 60) if s >= 60 else "%ds" % s)


def wait_words(s):
    """Why a queued or held job waits, in words (§7.1 'mu status wording'), from its status JSON."""
    st = s.get("state")
    w = s.get("waiting_for") or {}
    if st == "held":
        h = s.get("held") or {}
        return "held by %s%s (keeps place %s) · mu release %s" % (h.get("by", "?"), " " + hhmm(h.get("at")) if
                                                                   h.get("at") else "", s.get("place"),
                                                                   s.get("job_id", "")[-6:])
    bits = []
    if "resources" in w:
        r = w["resources"]
        bits.append("waiting for %d cores / %s (%s cores, %s free)" % (r.get("cores", 0), gb(r.get("mem_bytes")),
                                                                       r.get("free_cores"), gb(r.get("free_mem_bytes"))))
    elif "reservation" in w:
        r = w["reservation"]
        t = "waiting: would delay #1 (%s%s)" % (r.get("head", "")[-6:], ", starts by %s" % hhmm(r["starts_by"]) if
                                                 r.get("starts_by") else "")
        if r.get("fits_if_time_s_le"):
            t += " · with --time ≤ %s it would start now" % dur(r["fits_if_time_s_le"])
        bits.append(t)
    elif "disk" in w:
        d = w["disk"]
        bits.append("waiting: disk low (%s GB free, jobs need %s)" % (d.get("free_gb"), d.get("min_free_gb")))
    if s.get("starts_by"):
        bits.append("starts by %s at the latest" % hhmm(s["starts_by"]))
    elif s.get("starts_by_why"):
        bits.append(s["starts_by_why"])
    return " · ".join(bits)


def run_words(s):
    g = s.get("grant") or {}
    if not g:
        return "whole node (started by an older runner)" if s.get("state") == "running" else ""
    pz = s.get("paused") if isinstance(s.get("paused"), dict) else None
    if pz:   # release 2 (§20.9): a paused job's state stays running
        tm = s.get("time") or {}
        if pz.get("resume_asked"):
            t = "RESUMING: waits for %s (by %s)" % (" ".join(str(x)[-6:] for x in pz.get("lent_to") or []),
                                                   hhmm(pz.get("resumes_by")))
        elif pz.get("why") == "auto":
            t = "PAUSED for %s until %s" % (str(pz.get("for_job") or "")[-6:], hhmm(pz.get("until")))
        else:
            t = "PAUSED by %s until %s" % (proto.one_line(pz.get("by")), hhmm(pz.get("until")))
        t += " (keeps %s) · %s cores, %s granted" % (gb(pz.get("held_mem_bytes")), g.get("cores"), gb(g.get("mem_bytes")))
        if isinstance(tm.get("run_s"), (int, float)) and isinstance(tm.get("limit_s"), (int, float)):
            t += " · %s run, %s left" % (dur(tm["run_s"]), dur(max(0, tm["limit_s"] - tm["run_s"])))
        return t + (" · pausable" if s.get("pausable") else "")
    if g.get("class") == "borrowing":
        b = g.get("borrowed") or []
        t = "%s cores (%s) · %s · borrowed from %s" % (g.get("cores"), g.get("cpus"), gb(g.get("mem_bytes")),
                                                       " ".join(str(x.get("from"))[-6:] for x in b if isinstance(x, dict)))
        return t + (" · ends by %s" % hhmm(s["ends_by"]) if s.get("ends_by") else "")
    if g.get("class") == "express":
        t = "express · 1 CPU (quota) · %s" % gb(g.get("mem_bytes"))
    else:
        t = "%s cores (%s) · %s" % (g.get("cores"), g.get("cpus"), gb(g.get("mem_bytes")))
    if s.get("ends_by"):
        t += " · ends by %s" % hhmm(s["ends_by"])
    if g.get("class") == "backfilled":
        t += " · backfilled"
    if s.get("affinity_widened"):
        a = s["affinity_widened"]
        t += " · affinity widened by %s: %s" % (a.get("comm"), a.get("cpus"))
    return t


def state_words(s):
    """The scheduler's part of a job's status line, or '' (old nodes send none of these fields)."""
    st = s.get("state")
    if st in ("queued", "held"):
        return wait_words(s)
    if st == "running":
        return run_words(s)
    u = s.get("usage") or {}
    if isinstance(s.get("paused_s"), (int, float)) and s["paused_s"] > 0 and isinstance(s.get("run_s"), (int, float)):
        return "ran %s, paused %s (run_s, not wall_s, for timings)" % (dur(s["run_s"]), dur(s["paused_s"]))
    if u and isinstance(u.get("cpu_s"), (int, float)):
        return "used %s cores on average, %s peak%s" % (
            u.get("cores_avg"), gb(u.get("mem_peak_bytes")),
            " (working set %s)" % gb(u["mem_work_peak_bytes"]) if u.get("mem_work_peak_bytes") else "")
    return ""


def has(ans, feat):
    return feat in (ans.get("features") or [])


def capacity_line(h):
    """For mu compute status (§7.2): 20 cores, 20.0 GB for jobs · 12 cores, 15.0 GB allocated · ..."""
    if not has(h, "sched"):
        return "one job at a time (μ on the node is older than the scheduler)"
    c, a, q = h.get("capacity") or {}, h.get("allocated") or {}, h.get("queue") or {}
    return "%s cores, %s for jobs · %s cores, %s allocated · %s running, %s express, %s queued, %s held · CPU limit: %s" % (
        c.get("cores"), gb(c.get("mem_bytes")), a.get("cores"), gb(a.get("mem_bytes")), q.get("running"),
        q.get("express", 0), q.get("queued"), q.get("held", 0), (h.get("limits") or {}).get("cpu", "?"))


# ------------------------------------------------------------------------------------------------ the next-size hint
def size_hint(root, name, given):
    """§4.2: the newest fetched run of the same name with `usage` -> a `next` suggestion for the flags left off."""
    import json
    if not root or not name:
        return None
    best = None
    try:
        files = [f for f in os.listdir(os.path.join(root, "runs")) if f.endswith(".json")]
    except OSError:
        return None
    for f in sorted(files, reverse=True):
        try:
            with open(os.path.join(root, "runs", f), encoding="utf-8") as fh:
                r = json.load(fh)
        except (OSError, ValueError):
            continue
        if r.get("name") == name and isinstance(r.get("usage"), dict) and r["usage"].get("cpu_s") is not None:
            best = r
            break
    if not best:
        return None
    u = best["usage"]
    peak = u.get("mem_work_peak_bytes") or u.get("mem_peak_bytes") or 0
    cores = max(1, int(round(u.get("cores_avg") or 1)))
    wall = best.get("wall_s") or 0
    flags = []
    if "cores" not in given:
        flags.append("--cores %d" % cores)
    if "mem" not in given and peak:
        flags.append("--mem %dG" % max(1, int(peak * 1.25 / (1 << 30) + 0.999)))
    if "time" not in given and wall:
        flags.append("--time %s" % dur(max(600, wall * 2)))
    if not flags:
        return None
    return "last '%s' (%s): working memory peak %s, %s cores on average, %s: next time pass %s" % (
        name, best.get("job_id"), gb(peak), u.get("cores_avg"), dur(wall), " ".join(flags))


# ------------------------------------------------------------------------------------------------ commands
def tidy(j):
    """A job from the node, as shown here: its free text (name, project, key, submitted host and user) without control
    characters, whatever the node sent (another workstation's spec, an older node)."""
    if not isinstance(j, dict):
        return {}
    for k in ("name", "project", "key"):
        if k in j:
            j[k] = proto.one_line(j[k])
    if "submitted" in j:
        sub = j["submitted"] if isinstance(j["submitted"], dict) else {}
        j["submitted"] = {k: proto.one_line(sub.get(k)) for k in ("host", "user") if k in sub}
    return j


def project_id():
    from mu import jobs
    try:
        return jobs.Project().id
    except MuError:
        return None


def node_for(a, job_id=None):
    from mu import jobs
    if job_id:
        return jobs.job_node(job_id, a.on)[0]
    return jobs.pick_node(a.on)


def too_old(conf, what):
    return MuError(proto.EXIT_NODE_CANNOT, "node-too-old", "%s's μ has no %s (it is older than the scheduler)" % (
        conf["name"], what), "wait for %s's auto-update (mu compute status %s), or: mu compute update %s" % (
        conf["name"], conf["name"], conf["name"]), node=conf["name"])


def cmd_queue(a):
    from mu import jobs
    conf = node_for(a)
    conn = jobs.Conn(conf)
    frm = project_id()
    # an older node refuses the verb, and before it the from= argument (bad-argument): ask hello what it has
    if has(jobs.hello(conn), "queue-control"):
        ans = conn.call("queue", **({"from": frm} if frm else {}))
    else:
        ans = conn.call("list", limit=0)   # an older node: all its jobs, without order or bands
        ans = {"ok": True, "node": conf["name"], "fallback": "list", "jobs": ans.get("jobs", []),
               "note": "%s is older than the scheduler: the list of its jobs, without order or bands" % conf["name"]}
    jobs_ = [tidy(j) for j in ans.get("jobs") or [] if isinstance(j, dict) and isinstance(j.get("job_id"), str)]
    ans["jobs"] = jobs_
    for j in jobs_:
        j["this_project"] = bool(frm) and j.get("project_id") == frm
    if a.mine:
        jobs_ = [j for j in jobs_ if j["this_project"]]
        ans["jobs"] = jobs_
    ans.update(ok=True, exit=0, node=conf["name"], next="mu queue --json")
    if a.json:
        proto.emit_json(ans)
        return 0
    for w in ans.get("warnings") or []:
        print("WARNING: %s" % w)
    if ans.get("fallback"):
        print(ans["note"])
        for j in jobs_:
            print("  %s  %-10s %s" % (j["job_id"][-6:], j.get("state"), j.get("name") or ""))
        return 0
    c, al, fr = ans.get("capacity") or {}, ans.get("allocated") or {}, ans.get("free") or {}
    lt = ans.get("lent") if isinstance(ans.get("lent"), dict) else {}
    print("%s · %s cores, %s for jobs · %s cores, %s granted · %s cores, %s free%s%s" % (
        conf["name"], c.get("cores"), gb(c.get("mem_bytes")), al.get("cores"), gb(al.get("mem_bytes")),
        fr.get("cores"), gb(fr.get("mem_bytes")),
        " · %s cores, %s lent by paused jobs (%s cores, %s borrowed)" % (
            lt.get("cores"), gb(lt.get("mem_bytes")), (lt.get("borrowed") or {}).get("cores"),
            gb((lt.get("borrowed") or {}).get("mem_bytes"))) if lt.get("cores") else "",
        " · auto-pause: %s" % {"marked": "marked jobs only", "any": "any job", "off": "off"}.get(
            ans.get("auto_pause"), ans.get("auto_pause")) if ans.get("auto_pause") else ""))
    run = [j for j in jobs_ if j.get("state") == "running"]
    wait = [j for j in jobs_ if j.get("state") in ("queued", "held")]
    print("RUNNING")
    for j in run:
        print("  %s  %-14s %-14s %-9s %s" % (j["job_id"][-6:], (j.get("name") or "")[:14], (j.get("project") or "")[:14],
                                             (j.get("key") or "")[:9], run_words(j)))
    if not run:
        print("  (none)")
    print("QUEUED, in the order they start")
    for j in wait:
        print("  %s %-6s %s  %-14s %-14s %-9s %s" % (j.get("place"), j.get("band"), j["job_id"][-6:],
                                                     (j.get("name") or "")[:14], (j.get("project") or "")[:14],
                                                     (j.get("key") or "")[:9], wait_words(j)))
    if not wait:
        print("  (none)")
    print("next: mu queue --json")
    return 0


def cmd_arrange(op, a):
    """hold, release, move, priority: the node decides; this sends the claimed project and --others (§20.2)."""
    from mu import jobs
    conf, _ = jobs.job_node(a.job_id, a.on)
    conn = jobs.Conn(conf)
    args = {"id": a.job_id}
    frm = project_id()
    if frm:
        args["from"] = frm
    if a.others:
        args["others"] = 1
    if op == "move":
        given = [x for x in (("before", a.before), ("after", a.after)) if x[1]] + \
            [("to", x) for x, on in (("front", a.front), ("back", a.back)) if on]
        if len(given) != 1:
            raise MuError(proto.EXIT_USAGE, "usage", "mu move needs exactly one of --before JOB, --after JOB, --front, "
                          "--back", "e.g. mu move %s --front" % a.job_id)
        k, v = given[0]
        if k in ("before", "after") and not proto.JOB_ID.match(v or ""):
            raise MuError(proto.EXIT_USAGE, "bad-job-id", "'%s' is not a job id" % v, "mu queue")
        args[k] = v
    if op == "priority":
        args["class"] = a.band
    if not has(jobs.hello(conn), "queue-control"):   # an older node would refuse from= as a bad argument
        raise too_old(conf, "queue control")
    ans = conn.call(op, **args)
    ans.setdefault("exit", 0)
    ans["delays"] = [tidy(d) for d in ans.get("delays") or [] if isinstance(d, dict)]
    ans.update(node=conf["name"], next="mu queue --json")
    if a.json:
        proto.emit_json(ans)
        return 0
    if not ans.get("changed"):
        print("%s: nothing to change (%s, place %s, band %s)" % (a.job_id, ans.get("state"), ans.get("place"),
                                                                 ans.get("band")))
    else:
        was = ans.get("was") or {}
        print("%s %s: %s, place %s (was %s), band %s%s" % (a.job_id, {"hold": "held", "release": "released",
                                                                    "move": "moved", "priority": "band set"}[op],
                                                          ans.get("state"), ans.get("place"), was.get("place"),
                                                          ans.get("band"), " (was %s)" % was.get("band") if
                                                          was.get("band") != ans.get("band") else ""))
    if ans.get("higher_bands_ahead") and op == "move":
        print("  %d job(s) of higher bands stay ahead; to pass them: mu priority %s high (only if the owner asked)" % (
            ans["higher_bands_ahead"], a.job_id))
    for d in ans.get("delays") or []:
        print("  delays %s (%s, %s, %s)" % (d.get("job_id"), d.get("name"), d.get("project"), d.get("key")))
    print("next: mu queue --json")
    return 0


def parse_for(text):
    """--for 15m / 90s / 2h / 1h30m / a number of seconds -> seconds."""
    import re
    t = (text or "").strip().lower()
    if re.fullmatch(r"[0-9]+", t):
        return int(t)
    m = re.fullmatch(r"(?:([0-9]+)h)?(?:([0-9]+)m)?(?:([0-9]+)s)?", t)
    if not t or not m:
        raise MuError(proto.EXIT_USAGE, "usage", "--for %s: give a duration such as 15m, 90s or 1h30m" % text, "mu pause JOB --for 15m")
    return int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0)


def cmd_pause(op, a):
    """mu pause JOB [--for D] / mu resume JOB [--timeout S] (scheduler.md §20.2, §20.9): the node decides."""
    from mu import jobs
    conf, _ = jobs.job_node(a.job_id, a.on)
    conn = jobs.Conn(conf)
    args = {"id": a.job_id}
    frm = project_id()
    if frm:
        args["from"] = frm
    if a.others:
        args["others"] = 1
    if op == "pause" and a.for_:
        args["for"] = parse_for(a.for_)
    if not has(jobs.hello(conn), "pause"):
        raise too_old(conf, "pause (freeze/thaw)")
    ans = conn.call(op, **args)
    ans.setdefault("exit", 0)
    ans.update(node=conf["name"])
    if op == "resume" and ans.get("state") == "resuming" and a.timeout is not None:
        end = time.time() + a.timeout
        while time.time() < end:
            time.sleep(max(0.5, min(float(os.environ.get("MU_POLL_S", "5")), max(0.5, end - time.time()))))
            try:
                s = conn.call("status", id=a.job_id)
            except MuError:
                continue
            if not s.get("paused") or s.get("state") != "running":
                ans.update(state=s.get("state"), paused=None, waited=True)
                break
        else:
            ans.update(exit=proto.EXIT_STILL_RUNNING, next="mu queue --json (the resume stays asked; it thaws after "
                       "its borrower ends, by %s)" % hhmm(ans.get("resumes_by")))
    if a.json:
        proto.emit_json(ans)
        return ans["exit"]
    if op == "pause":
        pz = ans.get("paused") or {}
        if ans.get("deadline_raised_to"):
            print("%s: deadline raised to %s (a borrower runs until then)" % (a.job_id, hhmm(ans["deadline_raised_to"])))
        print("%s paused until %s; keeps %s, lends %s cores and %s" % (a.job_id, hhmm(pz.get("until")),
              gb(pz.get("held_mem_bytes")), (ans.get("lent") or {}).get("cores"), gb((ans.get("lent") or {}).get("mem_bytes"))))
    elif not ans.get("changed"):
        print("%s: nothing to resume (%s)" % (a.job_id, ans.get("state")))
    elif ans.get("state") == "resuming":
        print("%s: resuming: waits for %s, by %s" % (a.job_id, " ".join(str(x)[-6:] for x in ans.get("waits_for") or []),
                                                    hhmm(ans.get("resumes_by"))))
    else:
        print("%s resumed (%s)" % (a.job_id, ans.get("state")))
    print("next: %s" % ans.get("next", "mu queue --json"))
    return ans["exit"]


def cmd_compute_update(a):
    """mu compute update NODE: the node runs `mu autoupdate run --force` in a unit of its own, at once, beside its
    jobs (scheduler.md §9 "Version switch"); --wait polls hello until that run has a result."""
    from mu import jobs
    conf = jobs.load_node(a.node)
    conn = jobs.Conn(conf)
    h0 = jobs.hello(conn)
    if not has(h0, "update"):
        raise too_old(conf, "update verb")
    before = (h0.get("autoupdate") or {}).get("last_check"), (h0.get("mu") or {}).get("default")
    ans = conn.call("update")
    out = {"ok": True, "exit": 0, "node": conf["name"], "state": ans.get("state"), "unit": ans.get("unit"),
           "log": ans.get("log"), "default_was": before[1], "next": "mu compute status %s" % conf["name"]}
    if a.wait:
        end = time.time() + (a.timeout or 3600)
        h = h0
        while time.time() < end:
            time.sleep(max(1.0, float(os.environ.get("MU_POLL_S", "5"))))
            try:
                h = jobs.hello(conn)
            except MuError:
                continue
            au = h.get("autoupdate") or {}
            if not (h.get("update") or {}).get("running") and (au.get("last_check"), (h.get("mu") or {}).get("default")) != before:
                break
        else:
            out.update(ok=False, exit=proto.EXIT_STILL_RUNNING, error="still-running",
                       message="the update on %s has no result after %d s" % (conf["name"], a.timeout or 3600))
        out.update(default=(h.get("mu") or {}).get("default"), autoupdate=h.get("autoupdate"))
    if a.json:
        proto.emit_json(out)
    else:
        print("%s: update %s (%s; log on the node %s)" % (conf["name"], out["state"], out["unit"], out["log"]))
        if a.wait:
            au = out.get("autoupdate") or {}
            print("  default μ %s (was %s) · auto-update: %s: %s" % (out.get("default"), before[1],
                                                                      au.get("last_result"), au.get("last_message", "")))
        print("next: %s" % out["next"])
    return out["exit"]


def main(cmd, args):
    from mu import jobs
    p = jobs.parser("mu " + cmd, {
        "queue": "the whole node's queue: running jobs, then held and queued ones in the order they start",
        "hold": "keep a queued job in its place without starting it (mu release lets it go)",
        "release": "let a held job start again, from the place it has",
        "move": "change a queued or held job's place: --before/--after another job, or the --front/--back of its band",
        "priority": "put a queued or held job at the back of a band: urgent (owner key only), high, normal, low",
        "pause": "freeze a running job until --for passes (default 30m, at most 2h); its cores and unused memory are "
                 "lent meanwhile; it resumes by itself",
        "resume": "thaw a paused job now, or as soon as the job borrowing its cores ends"}[cmd])
    p.add_argument("--on", metavar="NODE", help="the node (default: this clone's record of the job, or the only node)")
    if cmd == "queue":
        p.add_argument("--mine", action="store_true", help="this project's jobs only")
        return cmd_queue(p.parse_args(args))
    p.add_argument("job_id")
    p.add_argument("--others", action="store_true", help="the job, or one it passes, is another project's (another "
                   "Claude session's): only when the owner asked")
    if cmd == "move":
        p.add_argument("--before", metavar="JOB")
        p.add_argument("--after", metavar="JOB")
        p.add_argument("--front", action="store_true", help="the front of the job's band")
        p.add_argument("--back", action="store_true", help="the back of the job's band")
    if cmd == "priority":
        p.add_argument("band", choices=("urgent", "high", "normal", "low"))
    if cmd == "pause":
        p.add_argument("--for", dest="for_", metavar="DUR", help="how long, e.g. 15m (default 30m, at most 2h)")
    if cmd == "resume":
        p.add_argument("--timeout", type=float, metavar="S", help="wait up to S seconds for the thaw (exit 10 if a "
                       "borrower still runs then)")
    a = p.parse_args(args)
    if not proto.JOB_ID.match(a.job_id or ""):
        raise MuError(proto.EXIT_USAGE, "bad-job-id", "'%s' is not a job id (YYYYMMDD-HHMMSS-xxxxxx)" % a.job_id, "mu queue")
    if cmd in ("pause", "resume"):
        return cmd_pause(cmd, a)
    return cmd_arrange(cmd, a)
