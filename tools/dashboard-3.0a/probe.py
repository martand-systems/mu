"""3.0a (docs/design/dashboard.md §7.0): the CPU cost of one verb call on today's node. Submitted as a tiny job
(mu submit --mem 512M --time 5m -- python probe.py); while it runs, burst.py on the Mac makes 50 `hello` and 50
`list limit=100` calls. This job only reads: every 2 s it prints one line `MU_VERB_COST {...}` with the machine's
/proc/stat totals (jiffies; USER_HZ from os.sysconf) and its own cgroup's CPU, and at the end the sizes of the last
lines of /srv/mu/audit.log if it can read them. burst.py turns the log into ms of node CPU per call."""
import json
import os
import time

DURATION = float(os.environ.get("MU_PROBE_S", "240"))


def proc_stat():
    with open("/proc/stat") as f:
        v = [int(x) for x in f.readline().split()[1:]]
    return sum(v), v[3] + (v[4] if len(v) > 4 else 0)


def own_cpu_usec():
    try:
        rel = open("/proc/self/cgroup").read().strip().split("::", 1)[1]
        for line in open("/sys/fs/cgroup%s/cpu.stat" % rel):
            if line.startswith("usage_usec"):
                return int(line.split()[1])
    except (OSError, IndexError, ValueError):
        return None
    return None


hz = os.sysconf("SC_CLK_TCK")
end = time.time() + DURATION
print("MU_VERB_COST " + json.dumps({"start": time.time(), "hz": hz, "ncpu": os.cpu_count()}), flush=True)
while time.time() < end:
    total, idle = proc_stat()
    print("MU_VERB_COST " + json.dumps({"t": round(time.time(), 3), "total": total, "idle": idle,
                                        "own_usec": own_cpu_usec()}), flush=True)
    time.sleep(2)
sizes = None
try:
    with open("/srv/mu/audit.log", "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 65536))
        lines = f.read().splitlines()[-200:]
    sizes = [len(x) + 1 for x in lines if b"list" in x or b"hello" in x][-100:]
except OSError as e:
    sizes = "unreadable: %s" % e.__class__.__name__
print("MU_VERB_COST " + json.dumps({"audit_line_bytes": sizes}), flush=True)
