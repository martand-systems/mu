"""3.0a, the Mac's half (docs/design/dashboard.md §7.0). Run with the default toolchain's Python once probe.py's job
is running:   <toolchain>/env/bin/python -I -S tools/dashboard-3.0a/burst.py burst NODE
It makes 50 `hello` and 50 `list limit=100` calls (read-only verbs, through the CLI's own connection code) and prints
the Mac-time window. After the job ends and its log is fetched (mu log JOB > probe.log):
    python3 tools/dashboard-3.0a/burst.py cost probe.log WINDOW.json
prints the node's CPU per call (busy jiffies in the window, minus the baseline rate before it, minus the probe's own
CPU) and the audit.log line size. 15 s stays the old-node step if a call costs up to ~150 ms of CPU, else 30 s (§4.3).
Nothing here runs by itself; it was written in 1a and has NOT been run (the PC was busy with JATAYU jobs)."""
import json
import os
import sys
import time


def burst(node):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))
    from mu import jobs
    conn = jobs.Conn(jobs.load_node(node))
    conn.call("hello", protocol="1", timeout=60)          # warm the master first: the burst measures calls, not setup
    t0 = time.time()
    for _ in range(50):
        conn.call("hello", protocol="1", timeout=60)
    for _ in range(50):
        conn.call("list", limit="100", timeout=60)
    t1 = time.time()
    ans = conn.call("hello", protocol="1", timeout=60)
    print(json.dumps({"t0": t0, "t1": t1, "calls": 100, "node_time_after": ans.get("time"), "mac_time_after": time.time()}))


def cost(log, window):
    w = json.load(open(window))
    rows = [json.loads(x.split("MU_VERB_COST ", 1)[1]) for x in open(log) if "MU_VERB_COST " in x]
    hz = rows[0]["hz"]
    s = [r for r in rows if "total" in r]
    import calendar
    skew = calendar.timegm(time.strptime(w["node_time_after"], "%Y-%m-%dT%H:%M:%SZ")) - w["mac_time_after"]
    t0, t1 = w["t0"] + skew, w["t1"] + skew
    busy = lambda r: r["total"] - r["idle"]
    before = [r for r in s if r["t"] < t0]
    inside = [r for r in s if t0 <= r["t"] <= t1]
    if len(before) < 3 or len(inside) < 3:
        sys.exit("not enough samples before or inside the burst window")
    base = (busy(before[-1]) - busy(before[0])) / (before[-1]["t"] - before[0]["t"])
    span = inside[-1]["t"] - inside[0]["t"]
    extra = (busy(inside[-1]) - busy(inside[0])) - base * span
    own = ((inside[-1]["own_usec"] or 0) - (inside[0]["own_usec"] or 0)) / 1e6 * hz
    calls = w["calls"] * span / (t1 - t0)
    ms = (extra - own) / hz / calls * 1000
    print(json.dumps({"ms_cpu_per_call": round(ms, 1), "baseline_jiffies_per_s": round(base, 2), "window_s": round(span, 1),
                      "audit_line_bytes": rows[-1].get("audit_line_bytes"),
                      "old_node_step_s": 15 if ms <= 150 else 30}, indent=1))


if __name__ == "__main__":
    if sys.argv[1:2] == ["burst"] and len(sys.argv) == 3:
        burst(sys.argv[2])
    elif sys.argv[1:2] == ["cost"] and len(sys.argv) == 4:
        cost(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
