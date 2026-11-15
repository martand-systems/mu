"""The dashboard's server (src/mu/dashboard.py App, its real NodeReader) against the CI node, at API level: rows D05,
D06, D07, D09 (the Mac part) and D25 (the load) of tools/test-jobs/e2e-dash.sh. Run as the workstation user with its
installed μ on sys.path (argv[1]); prints one JSON object on its last line. MU_JOBS_FAULT reaches the server's faults.

    dash_real.py SRC follow JOB SECONDS     each change of JOB's state as the API shows it, with the time
    dash_real.py SRC figures SECONDS        the card's figures against a stats answer of the same sample
    dash_real.py SRC stale SECONDS          when the card's stats turn stale (the shell stopped the node's runner)
    dash_real.py SRC metrics JOB            the whole-run series of JOB from the API: count and largest gap
    dash_real.py SRC load SECONDS           a visible tab for SECONDS (D25 measures the node meanwhile)
"""
import json
import sys
import time

sys.path.insert(0, sys.argv[1])
from mu import dashboard, dashfake, dashnode, jobs  # noqa: E402

cmd = sys.argv[2]
app = dashboard.App(dev=False, mac=dashfake.FakeMac(dashboard._RealClock()), readers={"office-pc": dashnode.NodeReader.real(jobs.load_node("office-pc"))})
w = app.workers["office-pc"]
SENT = {}   # the verbs this server really sent the node (review 2026-11-15: D25 reported the node's feature instead)
for _name in ("call", "stream"):
    def _counting(verb, *a, _f=getattr(w.r, _name), **k):
        SENT[verb] = SENT.get(verb, 0) + 1
        return _f(verb, *a, **k)
    setattr(w.r, _name, _counting)


def run(seconds, each=None, step=0.5):
    end = time.time() + seconds
    while time.time() < end:
        app.touch()
        w.tick()
        if each and each():
            break
        time.sleep(step)


out = {"features": None}
try:
    if cmd == "follow":
        jid, secs = sys.argv[3], float(sys.argv[4])
        seen = []

        def each():
            r = next((x for x in app.rows() if x["job_id"] == jid), None)
            s = r["state"] if r else None
            if not seen or seen[-1][1] != s:
                seen.append((round(time.time(), 1), s))
            return s in ("succeeded", "failed")
        run(secs, each)
        out["seen"] = seen
        out["rows_node"] = sorted({r["node"] for r in app.rows()})
    elif cmd == "figures":
        # The card and a stats answer of the same sample: the server sees a new 5 s sample only at its next watch
        # check, so a stats call can land on the next sample. Tick until the card shows the answer's sample (a newer
        # sample meanwhile: ask again), at most 4 tries.
        run(float(sys.argv[3]))
        for _ in range(4):
            raw = w.r.call("stats")
            run(8, lambda: ((w.stats or {}).get("t") or 0) >= (raw.get("t") or 0), step=0.25)
            if (w.stats or {}).get("t") == raw.get("t"):
                break
        card = w.card()
        out["card"] = card.get("stats")
        out["raw_mem"] = (raw.get("host") or {}).get("mem")
        out["raw_t"], out["card_t_node"] = raw.get("t"), (w.stats or {}).get("t")
    elif cmd == "stale":
        t0 = time.time()
        marks = []

        def each():
            c = w.card().get("stats") or {}
            if c.get("stale"):
                marks.append(round(time.time() - t0, 1))
                return True
            return False
        run(float(sys.argv[3]), each)
        out["stale_after_s"] = marks[0] if marks else None
        out["card"] = w.card().get("stats")
    elif cmd == "metrics":
        jid = sys.argv[3]
        run(4)
        m = app.api_metrics(jid)
        ts = [s["t"] for s in m.get("samples", [])]
        out["n"] = len(ts)
        out["max_gap"] = max((b - a for a, b in zip(ts, ts[1:])), default=None)
        out["available"] = m.get("available")
    elif cmd == "load":
        run(float(sys.argv[3]))
        out["sent"] = dict(SENT)
        # one watch stream (reopened at most every 600 s), no stats polling: "watch"; else what it did
        out["calls"] = "watch" if SENT.get("watch", 0) >= 1 and SENT.get("stats", 0) <= 5 else \
            "stats x%d" % SENT.get("stats", 0) if SENT.get("stats") else "list"
    out["features"] = w.features
    out["state"] = w.state
    out["error"] = w.error
finally:
    for x in app.workers.values():
        x.stop_watch()
    try:
        w.r.close_master()
    except Exception:  # noqa
        pass
print(json.dumps(out))
