"""3.0a, isolated: CPU of the node's ssh side (the cgroups holding sshd / the gate) per audited verb call.
Every 2 s prints MU_CG {t, usec: {cgroup: usage_usec}, audit_lines}. Read-only."""
import json, os, time
D = float(os.environ.get("MU_PROBE_S", "200"))
def cg_of(pid):
    try:
        return open("/proc/%s/cgroup" % pid).read().strip().split("::", 1)[1]
    except (OSError, IndexError):
        return None
def comm(pid):
    try:
        return open("/proc/%s/comm" % pid).read().strip()
    except OSError:
        return ""
def usage(cg):
    try:
        for l in open("/sys/fs/cgroup%s/cpu.stat" % cg):
            if l.startswith("usage_usec"):
                return int(l.split()[1])
    except OSError:
        return None
def audit_lines():
    try:
        with open("/srv/mu/audit.log", "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return None
seen = set()
end = time.time() + D
while time.time() < end:
    for p in os.listdir("/proc"):
        if p.isdigit() and comm(p) in ("sshd", "sshd-session", "mu-node-gate") :
            c = cg_of(p)
            if c and "/mu-job" not in c and "run-" not in c:
                seen.add(c)
    print("MU_CG " + json.dumps({"t": round(time.time(), 3), "usec": {c: usage(c) for c in sorted(seen)},
                                  "audit_lines": audit_lines()}), flush=True)
    time.sleep(2)
