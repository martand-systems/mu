"""μ dashboard phase 1b, the node side (docs/design/dashboard.md §4.2, §7.1.2; the integration contract with scheduler
release 2). Runs as the user `mu`, inside the runner (note_pass, sample hooks) and inside `mu node serve` (the verbs).

  - note_pass(jobs)   once per runner pass, under the queue lock, with the jobs step() already scanned: the node-wide
                      state rev (/srv/mu/metrics/rev), LAST_RUNNING (the ids the sampler reads) and the queue counts.
                      Never raises.
  - job_line()        one line of jobs/<id>/metrics.jsonl (no fsync: a view; a reader ignores a final line without \\n)
  - write_now()       metrics/now.json (rename, no fsync): the host sample, the running jobs, the queue counts
  - stats, watch, metrics   read-only verbs; stats and metrics journal only refusals and errors (QUIET_VERBS), watch
                      journals its open (serve) and its close (here)
  - run_seconds()     a job's run time: wall time minus paused time, the open pause included (contract §3.6)

Node helpers are imported lazily (`from mu import node as N` inside functions): node.py imports this module at its end.
Test hooks: MU_NODE_PROC (a folder standing in for /proc: stat, meminfo, loadavg, uptime, pressure/). Test faults
(MU_NODE_FAULT, MU_JOBS_FAULT or /etc/mu/test-fault, see durable.faults):
  stats-scan           stats calls scan_jobs() (D03: over 100 ms with 2,000 jobs)
  no-age               stats leaves out age_s and stale (D03)
  rev-frozen           the state rev never moves (D05's rev row)
  rename-field-node    stats sends host.mem.avail as host.mem.available (D40)
  metrics-from-zero    metrics ignores offset= (D10: duplicates)
  no-cap               watch ignores max= and its 600 s cap (D11)
  journal-each-call    stats and metrics journal every call (D11)
  paused-line-missing  a sample taken while the job is paused lacks "paused": 1 (unit)
  sampler-scan-all     the sampler scans every job folder instead of LAST_RUNNING (unit; D03/D09's cost)
  cores-avg-by-wall    cores_avg divides by wall time, paused time included (D01, unit)
  rename-field-node-paused  a paused job's running[] entry sends "frozen" instead of "paused" (D40P)
  rev-after-scan       list reads the state rev after its scan (node.verb_list; review 2026-11-15)
  sample-ms-partial    now.json's sample_ms leaves out the host sample and the write (D09; review 2026-11-15)
  watch-ignores-epipe  a watch whose client is gone lives on to its cap (review 2026-11-15)
  fingerprint-parses-dates  the rev's fingerprint parses every finished job's ended_at each pass (review 2026-11-15)
 and phase 1b's in node.py's hunks (B2, B7, B8; listed here so node.py's own list stays scheduler release 2's):
  sample-under-lock    the sampler's pass holds the queue lock (D09)
  usage-from-shim      a shim-written result's usage is the shim's own rusage, not the job's cgroup (D01)
  read-cgroup-after-exit  a runner-written result's usage is read from the job's cgroup, already gone (D02)
"""
import calendar
import hashlib
import json
import os
import sys
import time

from mu.durable import fault

STALE_S = 15.0                # now.json older than this: the runner is not sampling
WATCH_MAX_S = 600             # watch's default and cap
WATCH_EVERY_S = 15.0          # watch sends a line at least this often, changed or not
RECENT_S = 600                # results this recent count in the rev's fingerprint
PAUSED_PHASES = ("freezing", "frozen", "thawing")
LAST_RUNNING = []             # [(job_id, dir)] running at the last runner pass: what sample() reads
QUEUE = {}                    # the queue counts of the last pass
_HOST_PREV = {}               # the last /proc/stat totals (per-core rates between two samples)


def _N():
    from mu import node as N
    return N


def proc(*parts):
    return os.path.join(os.environ.get("MU_NODE_PROC") or "/proc", *parts)


def mdir():
    return _N().p("metrics")


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(1 << 20)
    except OSError:
        return ""


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else None
    except (OSError, ValueError):
        return None


def write_view(path, obj):
    """A view (rename, no fsync): losing it on a power cut costs nothing."""
    os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, separators=(",", ":")))
    os.replace(tmp, path)


def epoch_of(iso):
    try:
        return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return None


def pause_phase(st):
    pz = (st or {}).get("pause")
    return pz.get("phase") if isinstance(pz, dict) else None


def is_paused(st):
    return pause_phase(st) in PAUSED_PHASES


# ------------------------------------------------------------------------------------------------ run time (§3.6)
def run_seconds(st, wall_s, now_mono=None):
    """wall_s - paused_total_s - the open pause (frozen or thawing, same boot); never below 0. None without wall_s."""
    if not isinstance(wall_s, (int, float)):
        return None
    st = st or {}
    paused = st.get("paused_total_s") if isinstance(st.get("paused_total_s"), (int, float)) else 0
    pz = st.get("pause") if isinstance(st.get("pause"), dict) else {}
    open_s = 0.0
    if pz.get("phase") in ("frozen", "thawing") and isinstance(pz.get("since_mono"), (int, float)):
        if not pz.get("boot_id") or pz.get("boot_id") == _N().boot_id():
            from mu import pausectl   # a frozen record's open pause ends by its deadline + 120 s (the dead-man)
            open_s = max(0.0, pausectl.open_end(pz, time.monotonic() if now_mono is None else now_mono) -
                         pz["since_mono"])
    return max(0.0, wall_s - paused - open_s)


def cores_avg(cpu_s, st, wall_s):
    """cpu_s / run time (contract §3.6). Fault cores-avg-by-wall: by wall time."""
    if not isinstance(cpu_s, (int, float)):
        return None
    run = wall_s if fault("cores-avg-by-wall") else run_seconds(st, wall_s)
    return round(cpu_s / run, 2) if isinstance(run, (int, float)) and run > 0 else None


# ------------------------------------------------------------------------------------------------ the pass (rev)
def fingerprint(jobs, order, now=None):
    now = time.time() if now is None else now
    N = _N()
    rows = []
    # ISO times compare as strings: no time.strptime per finished job per pass (review 2026-11-15: 9 ms a pass with
    # 2,000 finished jobs)
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - RECENT_S))
    for j in jobs:
        st, res = j.get("state") or {}, j.get("result")
        if res:
            if fault("fingerprint-parses-dates"):
                t = epoch_of(res.get("ended_at"))
                if t is None or now - t > RECENT_S:
                    continue
            else:
                e = res.get("ended_at")
                if not isinstance(e, str) or len(e) != 20 or e < cutoff:
                    continue
        pz = st.get("pause") if isinstance(st.get("pause"), dict) else {}
        g = st.get("grant") if isinstance(st.get("grant"), dict) else {}
        try:
            band = N.sched.band_of(st, j.get("job") or {})
        except Exception:  # noqa
            band = None
        rows.append([j.get("id"), (res or {}).get("state") or st.get("state", "queued"),
                     (res or st).get("attempt", st.get("attempt", 1)), band, st.get("state") == "held" and not res,
                     pz.get("phase"), bool(pz.get("resume_asked")), g.get("class")])
    rows.sort(key=lambda r: str(r[0]))
    blob = json.dumps({"jobs": rows, "order": int((order or {}).get("rev") or 0)}, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()


def note_pass(jobs):
    """Once per runner pass (step(), under the lock). Never raises: the runner's pass must go on."""
    try:
        N = _N()
        running, counts = [], {"running": 0, "queued": 0, "held": 0, "paused": 0, "express": 0}
        for j in jobs:
            if j.get("result"):
                continue
            st = j.get("state") or {}
            s = st.get("state", "queued")
            if s == "running":
                running.append((j["id"], j["dir"]))
                counts["running"] += 1
                if is_paused(st):
                    counts["paused"] += 1
                if (st.get("grant") or {}).get("class") == "express":
                    counts["express"] += 1
            elif s == "held":
                counts["held"] += 1
            else:
                counts["queued"] += 1
        LAST_RUNNING[:] = running
        QUEUE.clear()
        QUEUE.update(counts)
        order = N.read_order()
        path = os.path.join(mdir(), "rev")
        old = read_json(path)
        if fault("rev-frozen") and old:
            return
        fp = fingerprint(jobs, order)
        if old and old.get("fp") == fp and isinstance(old.get("rev"), int):
            return
        rev = old["rev"] + 1 if old and isinstance(old.get("rev"), int) else int(order.get("rev") or 0) + 1
        write_view(path, {"rev": rev, "fp": fp})
    except Exception:  # noqa
        try:
            import traceback
            _N().journal_once("nodestats", "runner: note_pass: %s" % traceback.format_exc().replace("\n", " | ")[-800:])
        except Exception:  # noqa
            pass


def read_rev():
    """The node's state rev, read at answer time (list, stats, every watch line). Before the first pass: the order's."""
    r = read_json(os.path.join(mdir(), "rev"))
    if r and isinstance(r.get("rev"), int):
        return r["rev"]
    try:
        return int(_N().read_order().get("rev") or 0)
    except Exception:  # noqa
        return 0


# ------------------------------------------------------------------------------------------------ the host
def _cpu_lines():
    out = {}
    for ln in read_text(proc("stat")).splitlines():
        if ln.startswith("cpu"):
            parts = ln.split()
            try:
                vals = [int(x) for x in parts[1:]]
            except ValueError:
                continue
            if len(vals) >= 4:
                idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
                total = sum(vals[:8])
                out[parts[0]] = (total, idle)
    return out


def _pct(now, prev):
    dt = now[0] - (prev[0] if prev else 0)
    di = now[1] - (prev[1] if prev else 0)
    return round(max(0.0, min(100.0, 100.0 * (dt - di) / dt)), 1) if dt > 0 else None


def _psi(name):
    """some avg10 of /proc/pressure/NAME, or None where PSI is absent."""
    for ln in read_text(proc("pressure", name)).splitlines():
        if ln.startswith("some"):
            for f in ln.split()[1:]:
                k, _, v = f.partition("=")
                if k == "avg10":
                    try:
                        return float(v)
                    except ValueError:
                        return None
    return None


def host_sample():
    """/proc/stat (per core), /proc/meminfo, /proc/loadavg, /proc/pressure/*, statvfs(/srv/mu), uptime."""
    N = _N()
    cur = _cpu_lines()
    prev = dict(_HOST_PREV)
    _HOST_PREV.clear()
    _HOST_PREV.update(cur)
    cores = sorted((k for k in cur if k != "cpu"), key=lambda k: int(k[3:]) if k[3:].isdigit() else 0)
    mi = {}
    for ln in read_text(proc("meminfo")).splitlines():
        k, _, v = ln.partition(":")
        v = v.split()
        if v and v[0].isdigit():
            mi[k.strip()] = int(v[0]) * 1024
    load = read_text(proc("loadavg")).split()
    try:
        load = [float(x) for x in load[:3]]
    except ValueError:
        load = None
    up = read_text(proc("uptime")).split()
    try:
        uptime = int(float(up[0]))
    except (IndexError, ValueError):
        uptime = None
    node = N.node_info()
    try:
        free = N.free_gb(N.ROOT)
    except Exception:  # noqa
        free = None
    return {"cpu_pct": _pct(cur["cpu"], prev.get("cpu")) if "cpu" in cur else None,
            "per_core": [_pct(cur[k], prev.get(k)) for k in cores],
            "load": load or None,
            "mem": {"total": mi.get("MemTotal"), "avail": mi.get("MemAvailable"), "cached": mi.get("Cached"),
                    "shmem": mi.get("Shmem")},
            "psi": {"cpu_some10": _psi("cpu"), "mem_some10": _psi("memory")},
            "disk": {"free_gb": free, "min_free_gb": node.get("min_free_gb", N.DEFAULT_MIN_FREE_GB)},
            "uptime_s": uptime}


# ------------------------------------------------------------------------------------------------ per job
def job_line(j, st, u, prev):
    """One metrics.jsonl line from this sample U and the last one PREV (usage.json before this pass); appended."""
    cpu = None
    try:
        dt = u["mono"] - prev["mono"]
        dc = u["cpu_s"] - prev["cpu_s"]
        if dt > 0 and dc >= 0:
            cpu = round(dc / dt, 2)
    except (KeyError, TypeError):
        pass
    line = {"t": round(time.time(), 1), "cpu": cpu, "anon": u.get("anon"), "file": u.get("file"),
            "shmem": u.get("shmem"), "cur": u.get("mem_current"), "peak": u.get("mem_peak_bytes"),
            "max": u.get("mem_max"), "pids": u.get("pids"), "psi_mem": u.get("psi_mem")}
    if is_paused(st) and not fault("paused-line-missing"):
        line["paused"] = 1
    try:
        with open(os.path.join(j["dir"], "metrics.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(line, separators=(",", ":")) + "\n")
    except OSError:
        pass
    try:
        log_size = os.path.getsize(os.path.join(j["dir"], "log"))
    except OSError:
        log_size = None
    g = st.get("grant") if isinstance(st.get("grant"), dict) else {}
    row = {"job_id": j["id"], "cpu_cores": cpu, "anon": u.get("anon"), "file": u.get("file"), "shmem": u.get("shmem"),
           "peak": u.get("mem_peak_bytes"), "max": u.get("mem_max"), "pids": u.get("pids"), "log_size": log_size,
           "grant_cores": g.get("cores"), "class": g.get("class"), "paused": is_paused(st)}
    if fault("rename-field-node-paused") and row["paused"]:
        row["frozen"] = row.pop("paused")
    return row


def write_now(rows, t0=None):
    """metrics/now.json; sample_ms: what this pass of the sampler cost from T0 (perf_counter at its start) to the
    write, the host sample included (D09 reads it; stats does not send it). Review 2026-11-15: it was taken before the
    host sample, so a slow host sample did not show."""
    N = _N()
    q = dict(QUEUE) or {"running": len(rows), "queued": 0, "held": 0, "paused": 0, "express": 0}
    t_host = time.perf_counter()
    host = host_sample()
    end = t_host if fault("sample-ms-partial") else time.perf_counter()
    write_view(os.path.join(mdir(), "now.json"), {"t": round(time.time(), 1), "node": N.node_info().get("name"),
                                                  "host": host, "running": rows, "queue": q,
                                                  "sample_ms": None if t0 is None else round((end - t0) * 1000, 2)})


# ------------------------------------------------------------------------------------------------ the verbs
def stats_answer():
    if fault("stats-scan"):
        _N().scan_jobs()
    now = read_json(os.path.join(mdir(), "now.json")) or {}
    t = now.get("t") if isinstance(now.get("t"), (int, float)) else None
    age = round(max(0.0, time.time() - t), 1) if t is not None else None
    host = now.get("host") if isinstance(now.get("host"), dict) else None
    if host is not None and fault("rename-field-node") and isinstance(host.get("mem"), dict):
        host["mem"]["available"] = host["mem"].pop("avail", None)
    out = {"ok": True, "t": t, "age_s": age, "stale": t is None or age > STALE_S,
           "node": now.get("node") or _N().node_info().get("name"), "rev": read_rev(), "host": host,
           "running": now.get("running") if isinstance(now.get("running"), list) else [],
           "queue": now.get("queue") if isinstance(now.get("queue"), dict) else None}
    if fault("no-age"):
        del out["age_s"], out["stale"]
    return out


def verb_stats(key, args):
    return stats_answer()


def verb_watch(key, args):
    N = _N()
    iv = int(args.get("interval", "2") or 2)
    if not 2 <= iv <= 60:
        raise N.NodeError(N.proto.NODE_USAGE, "bad-argument", "watch interval=2..60 (seconds)", argument="interval")
    cap = min(int(args["max"]), WATCH_MAX_S) if args.get("max") else WATCH_MAX_S
    if fault("no-cap"):
        cap = 10 ** 9
    t0 = time.monotonic()
    lines, why, last, sent_at = 0, "max reached", None, -1e9
    out = sys.stdout
    try:
        while True:
            a = stats_answer()
            k = (a.get("t"), a.get("rev"))
            now = time.monotonic()
            if k != last or now - sent_at >= WATCH_EVERY_S:
                out.write(json.dumps(a, separators=(",", ":")) + "\n")
                out.flush()
                lines, last, sent_at = lines + 1, k, now
            left = cap - (time.monotonic() - t0)
            if left <= 0:
                break
            time.sleep(min(iv, left))
    except (BrokenPipeError, ConnectionResetError, OSError) as e:
        if fault("watch-ignores-epipe") and isinstance(e, (BrokenPipeError, ConnectionResetError)):
            try:
                fd = os.open(os.devnull, os.O_WRONLY)
                os.dup2(fd, sys.stdout.fileno())
            except OSError:
                pass
            time.sleep(max(0.0, cap - (time.monotonic() - t0)))   # the session lives on to its cap
        why = "client gone" if isinstance(e, (BrokenPipeError, ConnectionResetError)) or getattr(e, "errno", 0) == 32 \
            else "error: %s" % e
        try:   # the interpreter's own flush at exit must not meet the broken pipe again
            fd = os.open(os.devnull, os.O_WRONLY)
            os.dup2(fd, sys.stdout.fileno())
        except OSError:
            pass
    N.journal("key=%s verb=watch closed after %ds, %d lines, %s" % (key, int(time.monotonic() - t0), lines, why))
    return None


def verb_metrics(key, args):
    N = _N()
    j = N.load_job(args.get("id", ""))
    off = 0 if fault("metrics-from-zero") else int(args.get("offset", "0") or 0)
    cap = int(args["max"]) if args.get("max") else None
    N.send_range(os.path.join(j["dir"], "metrics.jsonl"), off, cap)
    return None


VERBS = {"stats": verb_stats, "watch": verb_watch, "metrics": verb_metrics}
QUIET_VERBS = ("stats", "metrics")
