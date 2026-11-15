"""Fake compute nodes for `mu dashboard serve --dev` and the tests (docs/design/dashboard.md §4.8).

A FakeNode answers the dashboard's verbs (hello, list, status, log, and for a node with features stats, metrics and
watch) with the shapes of today's node (src/mu/node.py) and of phase 1b's (§4.2), from a scenario that moves on in
time by itself. It has the transport interface of dashnode.DashConn: call(), stream(), fail(), close_master(), and
records every verb it is asked (D17, D18, D39).

Scenarios: idle, busy, offline, refused, stale, old-node, hostile, many, wake (SCENARIOS below says what each shows).

    python3 -I -S src/mu/dashfake.py fixtures DIR     write the golden fixtures (tools/test-dashboard/fixtures)
    python3 -I -S src/mu/dashfake.py scenarios        list the scenarios

Standard library only. Nothing here touches a real node, the network, $MU_HOME's compute folder or jobs.index.
"""
import hashlib
import json
import os
import sys
import threading
import time

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mu import proto  # noqa: E402
from mu.proto import MuError  # noqa: E402

FIXED_T0 = 1794650000.0      # the fixtures' "now": 2026-11-14T07:53:20Z
FEATURES = ["stats", "usage", "watch", "metrics"]
GB = 1000 ** 3

SCENARIOS = {
    "idle": "one node with features, nothing running, five finished jobs",
    "busy": "two nodes (office-pc, lab-pc); two running and one queued on office-pc, finished succeeded, failed, oom, "
            "timeout and cancelled; on lab-pc a job goes queued -> running (+10 s) -> succeeded (+40 s)",
    "offline": "office-pc offline (exit 4, node-offline), lab-pc online with one running job",
    "refused": "office-pc refused (exit 5, host-key): never retried automatically",
    "stale": "office-pc answers for 20 s, then its link is cut (stats age, then exit 4)",
    "old-node": "office-pc without features (today's node): list, hello, status, log only; a job goes queued -> "
                "running (+20 s) -> succeeded (+60 s)",
    "hostile": "a job whose name, note and log hold HTML, ANSI escapes and a 1 MB line; node strings with markup",
    "many": "office-pc with 150 finished jobs and one running (the list holds 100)",
    "wake": "office-pc with features; at +30 s the wall clock jumps 10 minutes ahead (a sleep and wake)",
}


# ------------------------------------------------------------------------------------------------ clocks
class Clock:
    """The real clock."""

    def wall(self):
        return time.time()

    def mono(self):
        return time.monotonic()

    def sleep(self, s):
        time.sleep(s)


class ScenarioClock:
    """Real time, sped up by SPEED, plus jumps of the wall clock (a sleep and wake) that monotonic time does not see."""

    def __init__(self, speed=1.0, jumps=(), t0=None):
        self.speed = float(speed)
        self.m0 = time.monotonic()
        self.w0 = time.time() if t0 is None else t0
        self.jumps = sorted(jumps)   # (at elapsed scenario seconds, jump seconds)

    def elapsed(self):
        return (time.monotonic() - self.m0) * self.speed

    def wall(self):
        e = self.elapsed()
        return self.w0 + e + sum(j for at, j in self.jumps if e >= at)

    def mono(self):
        return self.m0 + self.elapsed()

    def sleep(self, s):
        time.sleep(max(0.0, s) / self.speed)


class ManualClock:
    """A clock the tests move by hand."""

    def __init__(self, t0=FIXED_T0):
        self.w, self.m = t0, 1000.0
        self.lock = threading.Lock()

    def wall(self):
        return self.w

    def mono(self):
        return self.m

    def advance(self, s, wall_jump=0.0):
        with self.lock:
            self.w += s + wall_jump
            self.m += s

    def sleep(self, s):
        time.sleep(min(s, 0.01))


def _h(*parts):
    """A deterministic number in [0, 1) from PARTS (no random state, so answers depend only on the time)."""
    return int(hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12], 16) / float(16 ** 12)


def job_id_at(t, salt):
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    h = hashlib.sha256(str(salt).encode()).digest()
    return time.strftime("%Y%m%d-%H%M%S", time.gmtime(t)) + "-" + "".join(alphabet[b % 36] for b in h[:6])


# ------------------------------------------------------------------------------------------------ jobs of a scenario
class FakeJob:
    def __init__(self, node, salt, name, queued, start=None, end=None, final="succeeded", exit_code=None, note=None,
                 project=("jatayu", "jatayu-4f2a91c0d3e1"), mine=True, cores=17.8, mem_gb=9.8, limit_gb=22.0,
                 limit_cores=20, time_s=43200, log="solver", argv=None):
        self.node, self.name, self.queued, self.start, self.end = node, name, queued, start, end
        self.final, self.exit_code, self.note, self.mine = final, exit_code, note, mine
        self.project, self.cores, self.mem_gb, self.limit_gb, self.limit_cores = project, cores, mem_gb, limit_gb, limit_cores
        self.time_s, self.log_kind = time_s, log
        self.id = job_id_at(queued, salt)
        self.argv = argv or ["python", "frame/fea.py", "--mesh", "2.0"]
        self._log = None

    def state(self, t):
        if t < self.queued:
            return None
        if self.start is None or t < self.start:
            return "queued"
        if self.end is None or t < self.end:
            return "running"
        return self.final

    def project_id(self):
        return "%s-%s" % (self.project[0], hashlib.sha256(self.project[0].encode()).hexdigest()[:12])

    # the log: deterministic lines, one every 2 s of running time
    def log_bytes(self, t):
        st = self.state(t)
        if st in (None, "queued") or self.start is None:
            return b""
        upto = min(t, self.end) if self.end is not None else t
        n = int(max(0.0, upto - self.start) / 2.0)
        if self._log is None or self._log[0] != n or self._log[2] != (st == self.final):
            lines = ["== μ job %s attempt 1 on %s: mu run %s ==" % (self.id, self.node, " ".join(self.argv))]
            if self.log_kind == "hostile":
                lines += ['<img src=x onerror="window.__xss=1"> <script>window.__xss=2</script>',
                          "\x1b[31mred ANSI text\x1b[0m \x1b]0;title\x07 bell", "<b>bold?</b> &amp; &lt;tag&gt;",
                          "A" * (1 << 20)]
            lines.append("meshing … %d nodes" % (412331 + int(_h(self.id, "mesh") * 1000)))
            for i in range(min(n, 20000)):
                lines.append("step %5d  t=%.3f  residual %.3e" % (i + 1, (i + 1) * 0.01, 10 ** (-1 - 4 * _h(self.id, i))))
            if st == self.final and self.end is not None:
                lines.append("== μ job %s ended: %s%s ==" % (self.id, self.final,
                                                              "" if self.exit_code is None else " (exit %d)" % self.exit_code))
            self._log = (n, ("\n".join(lines) + "\n").encode(), st == self.final)
        return self._log[1]

    def sample(self, t, node_t):
        """One metrics.jsonl line at T (Mac time) in the node's time NODE_T."""
        e = max(0.0, t - (self.start or t))
        ramp = min(1.0, e / 300.0)
        anon = int(self.mem_gb * GB * (0.35 + 0.65 * ramp) * (0.97 + 0.03 * _h(self.id, "a", int(e // 5))))
        file_ = int(2.1 * GB * ramp)
        shmem = int(0.4 * GB * ramp)
        cur = anon + file_ + shmem
        cpu = round(self.cores * (0.9 + 0.1 * _h(self.id, "c", int(e // 5))), 2)
        return {"t": round(node_t, 1), "cpu": cpu, "anon": anon, "file": file_, "shmem": shmem, "cur": cur,
                "peak": int(cur * 1.04), "max": int(self.limit_gb * GB), "pids": 44, "psi_mem": 0.0}

    def usage(self):
        if self.start is None or self.end is None:
            return None
        wall = max(1.0, self.end - self.start)
        cpu_s = round(self.cores * wall * 0.95, 1)
        oom = self.final == "oom"
        return {"cpu_s": cpu_s, "user_s": round(cpu_s * 0.99, 1), "sys_s": round(cpu_s * 0.01, 1),
                "cores_avg": round(cpu_s / wall, 2),
                "mem_peak_bytes": int((self.limit_gb if oom else self.mem_gb * 1.15) * GB),
                "mem_work_peak_bytes": int((self.limit_gb * 0.99 if oom else self.mem_gb) * GB), "pids_peak": 44,
                "mem_events": {"max": 3 if oom else 0, "oom_kill": 1 if oom else 0},
                "source": "runner-sampled" if self.final in ("oom", "timeout", "interrupted") else "cgroup"}

    def spec(self, t0):
        """The .mu/jobs record's spec, as the workstation wrote it."""
        return {"argv": list(self.argv), "cwd": "", "head": hashlib.sha1(self.id.encode()).hexdigest(),
                "snapshot": hashlib.sha1(("s" + self.id).encode()).hexdigest(), "dirty": _h(self.id, "d") < 0.5,
                "mu_version": "v2026.11.9", "outputs": ["results/*.json"],
                "limits": {"mem_bytes": int(self.limit_gb * GB), "time_s": self.time_s, "cores": self.limit_cores},
                "submitted": {"at": proto.iso(self.queued), "host": "avi-mac", "user": "avi", "mu": "v2026.11.9"}}


def iso_or_none(t):
    return proto.iso(t) if t is not None else None


# ------------------------------------------------------------------------------------------------ one fake node
class FakeNode:
    """A compute node's answers. FAILURE: None, or a function of the scenario time -> None | ("offline"|"refused"|
    "cut", MuError) deciding how calls fail."""

    def __init__(self, name, clock, t0, jobs=(), features=True, skew=0.0, failure=None, hostile=False,
                 mu_version="v2026.11.9", stale_stats=None):
        self.name, self.clock, self.t0 = name, clock, t0
        self.jobs = list(jobs)
        self.features = features
        self.skew = skew
        self.failure = failure
        self.hostile = hostile
        self.mu_version = mu_version
        self.stale_stats = stale_stats
        self.calls = []                 # (wall time, verb, args)
        self.lock = threading.Lock()
        self.cp = "fake:%s.dash.sock" % name
        self.masters_closed = 0
        self._pending = None

    # ---------------------------------------------------------------- transport interface
    def record(self, verb, args):
        with self.lock:
            self.calls.append((self.clock.wall(), verb, dict(args)))

    def count(self, verb=None, since=None):
        with self.lock:
            return sum(1 for (t, v, _) in self.calls if (verb is None or v == verb) and (since is None or t >= since))

    def _fail_now(self):
        if not self.failure:
            return None
        return self.failure(self.clock.wall() - self.t0)

    def call(self, verb, timeout=30, **args):
        self.record(verb, args)
        f = self._fail_now()
        if f:
            kind, err = f
            if kind == "cut":
                self.clock.sleep(min(2.0, timeout))
            raise err
        fn = {"hello": self.hello, "list": self.list, "status": self.status}.get(verb)
        if fn is None and self.features:
            fn = {"stats": self.stats}.get(verb)
        if fn is None:
            raise MuError(proto.EXIT_ERROR, "unknown-verb", "%s refused the request (unknown-verb) [unknown-verb]"
                          % self.name, "the node's μ may differ from this one: mu compute status %s" % self.name,
                          node=self.name)
        return proto.clean_obj(fn(**args))

    def stream(self, request, on_data, stop=None):
        parts = request.split()
        verb, args = parts[0], dict(p.split("=", 1) for p in parts[1:])
        self.record(verb, args)
        f = self._fail_now()
        if f:
            self._pending = f[1]
            return 255, "", None, False
        if verb == "log":
            return self._stream_log(args, on_data, stop)
        if verb == "metrics" and self.features:
            return self._stream_metrics(args, on_data)
        if verb == "watch" and self.features:
            return self._stream_watch(args, on_data, stop)
        self._pending = MuError(proto.EXIT_ERROR, "unknown-verb", "%s refused the request (unknown-verb) "
                                "[unknown-verb]" % self.name, "update μ on %s" % self.name, node=self.name)
        on_data(json.dumps({"ok": False, "error": "unknown-verb", "hint": "verbs: hello list log status"}).encode() + b"\n")
        return 64, "", None, False

    def fail(self, rc, err, st, muxed):
        e = self._pending or MuError(proto.EXIT_UNREACHABLE, "relay-lost", "the connection to the relay was lost "
                                     "[relay-lost]", "retry", node=self.name)
        self._pending = None
        raise e

    def close_master(self):
        with self.lock:
            self.masters_closed += 1

    # ---------------------------------------------------------------- answers
    def now(self):
        return self.clock.wall()

    def node_time(self, t=None):
        return (self.now() if t is None else t) + self.skew

    def visible_jobs(self, t):
        return [j for j in self.jobs if j.state(t) is not None]

    def status_of(self, j, t):
        st = j.state(t)
        q = sorted((x for x in self.visible_jobs(t) if x.state(t) == "queued"), key=lambda x: x.queued)
        pos = next((i + 1 for i, x in enumerate(q) if x is j), None) if st == "queued" else None
        out = {"ok": True, "job_id": j.id, "name": j.name, "project_id": j.project_id(), "state": st, "attempt": 1,
               "position": pos, "queued_at": iso_or_none(j.queued + self.skew),
               "started_at": iso_or_none(j.start + self.skew) if j.start is not None and t >= j.start else None,
               "ended_at": iso_or_none(j.end + self.skew) if st in proto.FINISHED else None,
               "exit_code": j.exit_code if st in proto.FINISHED else None, "log_size": len(j.log_bytes(t)),
               "note": j.note if st in proto.FINISHED else None,
               "unit": ("mu-job-%s.service" % j.id) if st != "queued" else None, "cancel_requested": False}
        if self.features and st in proto.FINISHED:
            out["usage"] = j.usage()
        return out

    def hello(self, protocol=None):
        t = self.now()
        vis = self.visible_jobs(t)
        cpu = "AMD Ryzen 9 7950X 16-Core Processor"
        if self.hostile:
            cpu = '<img src=x onerror="window.__xss=3"> Ryzen \x1b[1mbold\x1b[0m'
        out = {"ok": True, "protocol": proto.PROTOCOL, "protocol_min": proto.PROTOCOL_MIN, "node": self.name,
               "key": "avi-mac", "mu": {"default": self.mu_version, "installed": ["v2026.11.8", self.mu_version],
                                        "infra": self.mu_version},
               "hw": {"cpu_model": cpu, "cores_physical": 16, "cores_logical": 32, "mem_total_kb": 24 * 1024 * 1024,
                      "kernel": "6.6.87.2-microsoft-standard-WSL2", "wsl": "6.6.87.2-microsoft-standard-WSL2"},
               "disk": {"free_gb": 89, "min_free_gb": 20, "where": "C: (/mnt/c)", "host": "C"},
               "limits": {"memory": True, "pids": True, "selftest_at": proto.iso(self.t0 - 86400),
                          "selftest_detail": "ok"},
               "queue": {"running": sum(1 for j in vis if j.state(t) == "running"),
                         "queued": sum(1 for j in vis if j.state(t) == "queued")},
               "last_stop": "clean", "runner": "active",
               "autoupdate": {"enabled": True, "last_result": "ok" if self.features else "failed"},
               "time": proto.iso(self.node_time(t))}
        if self.features:
            out["features"] = list(FEATURES)
        return out

    def list(self, project=None, limit="20"):
        t = self.now()
        limit = int(limit or 20)
        vis = [j for j in self.visible_jobs(t) if not project or j.project_id() == project]
        unf = [self.status_of(j, t) for j in vis if j.state(t) not in proto.FINISHED]
        fin = sorted((self.status_of(j, t) for j in vis if j.state(t) in proto.FINISHED),
                     key=lambda s: s.get("ended_at") or "")[-limit:] if limit else []
        out = {"ok": True, "jobs": sorted(unf + fin, key=lambda s: s["job_id"])}
        if self.features:
            out["rev"] = self.rev(t)
        return out

    def status(self, id=None):
        t = self.now()
        for j in self.visible_jobs(t):
            if j.id == id:
                return self.status_of(j, t)
        raise MuError(proto.EXIT_NO_SUCH, "no-such-job", "%s has no job %s [no-such-job]" % (self.name, id),
                      "mu status (this project's jobs); --on NODE if it ran on another node", node=self.name)

    def rev(self, t):
        """Increases whenever a job changes state."""
        n = 0
        for j in self.jobs:
            for x in (j.queued, j.start, j.end):
                if x is not None and t >= x:
                    n += 1
        return n

    def stats(self, t=None):
        t = self.now() if t is None else t
        vis = self.visible_jobs(t)
        run = [j for j in vis if j.state(t) == "running"]
        busy = sum(j.cores for j in run)
        cpu = min(100.0, round(busy / 32.0 * 100.0 + 2.0 + 3.0 * _h(self.name, int(t // 5)), 1))
        per_core = [int(min(100, cpu + 10 * (_h(self.name, i, int(t // 5)) - 0.5))) for i in range(32)]
        used = sum(j.sample(t, t)["cur"] for j in run) + int(1.2 * GB)
        total = 24 * 1024 ** 3
        age = 1.8
        stale = False
        if self.stale_stats is not None and t - self.t0 >= self.stale_stats:
            age, stale = round(t - (self.t0 + self.stale_stats), 1) + 1.8, True
        return {"ok": True, "t": round(self.node_time(t) - age, 1), "age_s": age, "stale": stale, "node": self.name,
                "rev": self.rev(t),
                "host": {"cpu_pct": cpu, "per_core": per_core, "load": [round(busy * 1.02, 1), round(busy, 1),
                                                                        round(busy * 0.84, 1)],
                         "mem": {"total": total, "avail": max(0, total - used), "cached": int(2.1 * GB),
                                 "shmem": int(0.4 * GB)},
                         "psi": {"cpu_some10": round(41.2 * min(1.0, busy / 20.0), 1), "mem_some10": 0.0},
                         "disk": {"free_gb": 89, "min_free_gb": 20}, "uptime_s": int(t - self.t0 + 86400)},
                "running": [dict(job_id=j.id, cpu_cores=j.sample(t, t)["cpu"], anon=j.sample(t, t)["anon"],
                                 file=j.sample(t, t)["file"], shmem=j.sample(t, t)["shmem"],
                                 peak=j.sample(t, t)["peak"], max=j.sample(t, t)["max"], pids=44,
                                 log_size=len(j.log_bytes(t))) for j in run],
                "queue": {"running": len(run), "queued": sum(1 for j in vis if j.state(t) == "queued")}}

    def metrics_bytes(self, j, t):
        if j.start is None or t < j.start:
            return b""
        upto = min(t, j.end) if j.end is not None else t
        out = []
        k = 0
        while j.start + 5 * k <= upto and k < 20000:
            ts = j.start + 5 * k
            out.append(json.dumps(j.sample(ts, ts + self.skew), separators=(",", ":")))
            k += 1
        return ("\n".join(out) + "\n").encode() if out else b""

    # ---------------------------------------------------------------- streams
    def _find(self, job_id, on_data):
        t = self.now()
        for j in self.visible_jobs(t):
            if j.id == job_id:
                return j
        self._pending = MuError(proto.EXIT_NO_SUCH, "no-such-job", "%s has no job %s [no-such-job]" % (self.name, job_id),
                                "mu status", node=self.name)
        on_data(json.dumps({"ok": False, "error": "no-such-job"}).encode() + b"\n")
        return None

    def _stream_log(self, args, on_data, stop):
        j = self._find(args.get("id"), on_data)
        if j is None:
            return 66, "", None, False
        off = int(args.get("offset", "0") or 0)
        cap = int(args["max"]) if args.get("max") else None
        follow = args.get("follow") == "1"
        sent = 0
        while True:
            if self._fail_now():
                self._pending = self._fail_now()[1]
                return 255, "", None, False
            t = self.now()
            data = j.log_bytes(t)
            if off < len(data):
                piece = data[off:off + 65536 if cap is None else off + min(65536, cap - sent)]
                on_data(piece)
                off += len(piece)
                sent += len(piece)
                continue
            if not follow or (cap is not None and sent >= cap):
                return 0, "", None, False
            if j.state(t) in proto.FINISHED and off >= len(data):
                return 0, "", None, False
            if stop is not None and stop.is_set():
                return -15, "", None, False
            self.clock.sleep(0.5)

    def _stream_metrics(self, args, on_data):
        j = self._find(args.get("id"), on_data)
        if j is None:
            return 66, "", None, False
        data = self.metrics_bytes(j, self.now())
        off = int(args.get("offset", "0") or 0)
        if off < len(data):
            on_data(data[off:])
        return 0, "", None, False

    def _stream_watch(self, args, on_data, stop):
        interval = max(2, min(60, int(args.get("interval", "2") or 2)))
        cap = min(600, int(args.get("max", "600") or 600))
        start = self.now()
        while self.now() - start < cap:
            f = self._fail_now()
            if f:
                kind, err = f
                if kind == "cut":       # a cut link: silence, then ssh gives up
                    while self._fail_now() and not (stop is not None and stop.is_set()) and self.now() - start < cap:
                        self.clock.sleep(0.5)
                        if self.now() - start > 45:
                            break
                self._pending = err
                return 255, "", None, False
            on_data((json.dumps(self.stats(), separators=(",", ":")) + "\n").encode())
            end = self.now() + interval
            while self.now() < end:
                if stop is not None and stop.is_set():
                    return -15, "", None, False
                self.clock.sleep(0.25)
        return 0, "", None, False


# ------------------------------------------------------------------------------------------------ scenarios
def offline_error(name):
    return MuError(proto.EXIT_UNREACHABLE, "node-offline", "the relay is up, but %s is not connected to it (its "
                   "tunnel is down, or the PC is off or asleep) [node-offline]" % name,
                   "retry later; on the PC: wsl -d mu-compute -u root -- mu-wsl2-distro status. A job already "
                   "accepted is unaffected", reason="node-offline", node=name)


def refused_error(name):
    return MuError(proto.EXIT_REFUSED, "host-key", "%s's host key differs from the key pinned at pairing. Never "
                   "retried; nothing was sent [host-key]" % name,
                   "tell the user. If the node was reinstalled on purpose, compare the key the PC shows and pair "
                   "again: mu compute invite %s --replace" % name, reason="host-key", node=name)


def cut_error(name):
    return MuError(proto.EXIT_UNREACHABLE, "relay-lost", "the connection to the relay was lost [relay-lost]",
                   "retry; a job already accepted is unaffected", reason="relay-lost", node=name)


class Scenario:
    """The nodes, the fake jobs.index and the project records of one scenario."""

    def __init__(self, name, clock=None, t0=None):
        if name not in SCENARIOS:
            raise ValueError("unknown scenario '%s' (one of: %s)" % (name, ", ".join(SCENARIOS)))
        jumps = ((30.0, 600.0),) if name == "wake" else ()
        speed = float(os.environ.get("MU_DASH_FAKE_SPEED", "1") or 1)
        self.name = name
        self.clock = clock or ScenarioClock(speed=speed, jumps=jumps, t0=t0)
        self.t0 = self.clock.wall()
        self.nodes = {}
        getattr(self, "_" + name.replace("-", "_"))(self.t0)

    def add(self, node):
        self.nodes[node.name] = node
        return node

    def all_jobs(self):
        return [j for n in self.nodes.values() for j in n.jobs]

    def index(self):
        """Fake jobs.index entries (the Mac's own submits)."""
        out = []
        for j in self.all_jobs():
            if j.mine:
                out.append({"job_id": j.id, "node": j.node, "key": "avi-mac", "project_root": "/Users/example/%s"
                            % j.project[0], "project": j.project[0], "project_id": j.project_id(),
                            "submit_started": proto.iso(j.queued - 3), "accepted": proto.iso(j.queued),
                            "fetched": None})
        return out

    def spec(self, job_id):
        for j in self.all_jobs():
            if j.id == job_id and j.mine:
                return j.spec(self.t0)
        return None

    # ---------------------------------------------------------------- the scenarios
    def _finished_set(self, node, t0, n=5, salt="f"):
        kinds = [("succeeded", 0, None, "cfd-cavity", ("bench", "bench-0a1b2c3d4e5f")),
                 ("failed", 3, None, "frame-fea", ("jatayu", "")),
                 ("oom", None, "the job used more memory than its 22 GB limit", "frame-fea", ("jatayu", "")),
                 ("timeout", None, "the job ran past its 2 h limit", "sweep-aoa", ("jatayu", "")),
                 ("cancelled", None, "cancelled while running", "mesh-study", ("jatayu", ""))]
        out = []
        for i in range(n):
            st, code, note, name, prj = kinds[i % len(kinds)]
            q = t0 - 7200 * (i + 1) - 600
            dur = [2472, 95, 7847, 7200, 31][i % 5]
            out.append(FakeJob(node, "%s%s%d" % (salt, node, i), name, queued=q, start=q + 20, end=q + 20 + dur,
                               final=st, exit_code=code, note=note, project=(prj[0], prj[1]), mine=i % 4 != 3,
                               mem_gb=21.8 if st == "oom" else 6.0, time_s=7200 if st == "timeout" else 43200))
        return out

    def _idle(self, t0):
        self.add(FakeNode("office-pc", self.clock, t0, self._finished_set("office-pc", t0)))

    def _busy(self, t0):
        a = [FakeJob("office-pc", "b1", "frame-fea", queued=t0 - 1100, start=t0 - 1084),
             FakeJob("office-pc", "b2", "frame-fea-v2", queued=t0 - 400, start=t0 - 300, cores=1.9, mem_gb=3.2,
                     limit_cores=2, limit_gb=8.0),
             FakeJob("office-pc", "b3", "mesh-study", queued=t0 - 120, start=None)]
        self.add(FakeNode("office-pc", self.clock, t0, a + self._finished_set("office-pc", t0)))
        b = [FakeJob("lab-pc", "c1", "cfd-cavity", queued=t0 - 2, start=t0 + 10, end=t0 + 40, final="succeeded",
                     exit_code=0, project=("bench", ""), cores=7.6, mem_gb=2.0, limit_gb=12.0, limit_cores=8),
             FakeJob("lab-pc", "c2", "aero-vlm-wing", queued=t0 - 9000, start=t0 - 8990, end=t0 - 8970,
                     final="succeeded", exit_code=0, project=("bench", ""), mine=False, cores=3.0, mem_gb=0.8)]
        self.add(FakeNode("lab-pc", self.clock, t0, b, skew=3.2))

    def _offline(self, t0):
        self.add(FakeNode("office-pc", self.clock, t0, self._finished_set("office-pc", t0, 2),
                          failure=lambda e: ("offline", offline_error("office-pc"))))
        self.add(FakeNode("lab-pc", self.clock, t0, [FakeJob("lab-pc", "o1", "frame-fea", queued=t0 - 700,
                                                              start=t0 - 690)]))

    def _refused(self, t0):
        self.add(FakeNode("office-pc", self.clock, t0, self._finished_set("office-pc", t0, 2),
                          failure=lambda e: ("refused", refused_error("office-pc"))))

    def _stale(self, t0):
        self.add(FakeNode("office-pc", self.clock, t0, [FakeJob("office-pc", "s1", "frame-fea", queued=t0 - 700,
                                                                 start=t0 - 690)] + self._finished_set("office-pc", t0, 2),
                          failure=lambda e: ("cut", cut_error("office-pc")) if e >= 20 else None))

    def _old_node(self, t0):
        jobs = [FakeJob("office-pc", "n1", "frame-fea", queued=t0 - 1, start=t0 + 20, end=t0 + 60,
                        final="succeeded", exit_code=0)] + self._finished_set("office-pc", t0, 3)
        self.add(FakeNode("office-pc", self.clock, t0, jobs, features=False, mu_version="v2026.11.8"))

    def _hostile(self, t0):
        h = FakeJob("office-pc", "h1", '<img src=x onerror="window.__xss=1">\x1b[2J<script>window.__xss=2</script>',
                    queued=t0 - 300, start=t0 - 290, log="hostile",
                    argv=["python", "-c", "print('<b>x</b>')"])
        f = FakeJob("office-pc", "h2", "<svg onload=window.__xss=4>", queued=t0 - 5000, start=t0 - 4990, end=t0 - 4000,
                    final="failed", exit_code=1, note='<a href="javascript:window.__xss=5">click</a>‮evil')
        self.add(FakeNode("office-pc", self.clock, t0, [h, f], hostile=True))

    def _many(self, t0):
        jobs = [FakeJob("office-pc", "m-run", "frame-fea", queued=t0 - 600, start=t0 - 590)]
        for i in range(150):
            q = t0 - 3600 * 3 - i * 600
            jobs.append(FakeJob("office-pc", "m%d" % i, "sweep-%03d" % i, queued=q, start=q + 5, end=q + 305,
                                final="succeeded" if i % 7 else "failed", exit_code=0 if i % 7 else 1, cores=4.0,
                                mem_gb=1.0))
        self.add(FakeNode("office-pc", self.clock, t0, jobs))

    def _wake(self, t0):
        self.add(FakeNode("office-pc", self.clock, t0, [FakeJob("office-pc", "w1", "frame-fea", queued=t0 - 700,
                                                                 start=t0 - 690)] + self._finished_set("office-pc", t0, 2)))


# ------------------------------------------------------------------------------------------------ a fake Mac sampler
class FakeMac:
    """This Mac's card from a deterministic function of the time (the fixtures, screenshots); the same interface and
    shapes as dashboard.MacSampler (the real one reads psutil)."""

    def __init__(self, clock):
        self.clock = clock
        self.lock = threading.Lock()
        self.cpu, self.mem, self.last, self.t = [], [], None, None

    def sample(self):
        t = self.clock.wall()
        k = int(t // 2)
        cpu = round(30 + 10 * _h("mac", k), 1)
        per = [round(min(100.0, cpu + 30 * (_h("mac", k, i) - 0.5)), 1) for i in range(8)]
        total = 16 * 1024 ** 3
        used = int(9.1 * GB + 0.2 * GB * _h("mem", k))
        s = {"t": round(t, 3), "cpu_pct": cpu, "per_core": per, "load": [2.1, 1.9, 1.7],
             "mem": {"total": total, "available": total - used, "used": used, "pressure": "ok"}}
        with self.lock:
            self.last, self.t = s, t
            self.cpu.append((round(t, 1), cpu))
            self.mem.append((round(t, 1), used))
            del self.cpu[:-1810], self.mem[:-1810]
        return s

    def prefill(self, span=600.0):
        """The last SPAN seconds of CPU and memory (dev servers only: the sparklines start full)."""
        now = self.clock.wall()
        t = now - span
        with self.lock:
            while t < now - 2:
                k = int(t // 2)
                self.cpu.append((round(t, 1), round(30 + 10 * _h("mac", k), 1)))
                self.mem.append((round(t, 1), int(9.1 * GB + 0.2 * GB * _h("mem", k))))
                t += 2.0

    def battery(self):
        stub = os.environ.get("MU_DASH_BATTERY")
        return {"on_battery": stub == "1", "percent": 100.0 if stub != "1" else 50.0}

    def on_battery(self):
        return os.environ.get("MU_DASH_BATTERY") == "1"

    def card(self, series_s=600):
        now = self.clock.wall()
        with self.lock:
            s, t = self.last or {}, self.t
            cpu = [[a, b] for a, b in self.cpu if a >= now - series_s]
            mem = [[a, b] for a, b in self.mem if a >= now - series_s]
        return {"name": "This Mac", "host": "fixture-mac", "sampler": "fake", "t": t,
                "stale": t is None or now - t > 15.0, "cpu_pct": s.get("cpu_pct"), "per_core": s.get("per_core", []),
                "load": s.get("load"), "cores": {"logical": 8, "physical": 8, "perf": 4, "eff": 4},
                "mem": dict(s.get("mem") or {"total": None, "available": None, "used": None, "pressure": None}),
                "battery": self.battery(), "mu_version": "v2026.11.9", "series": {"cpu_pct": cpu, "mem_used": mem}}


# ------------------------------------------------------------------------------------------------ fixtures
def node_fixtures(clock=None):
    """The golden node answers (exact): from the busy scenario at FIXED_T0, plus the old node's hello."""
    clock = clock or ManualClock(FIXED_T0)
    sc = Scenario("busy", clock=clock)
    n = sc.nodes["office-pc"]
    run = [j for j in n.jobs if j.state(clock.wall()) == "running"][0]
    fin = [j for j in n.jobs if j.final == "oom"][0]
    old = Scenario("old-node", clock=ManualClock(FIXED_T0)).nodes["office-pc"]
    metrics = n.metrics_bytes(run, clock.wall()).decode().splitlines()
    return {
        "node-hello.json": n.hello(protocol=1),
        "node-hello-old.json": old.hello(protocol=1),
        "node-list.json": n.list(limit="100"),
        "node-list-old.json": old.list(limit="100"),
        "node-status.json": n.status(id=run.id),
        "node-status-finished.json": n.status(id=fin.id),
        "node-stats.json": n.stats(),
        "node-watch-line.json": json.loads(json.dumps(n.stats())),
        "node-metrics-lines.json": [json.loads(x) for x in metrics[:5]],
    }


def write_fixtures(d):
    """Every golden fixture: the node answers here, the /api responses from dashboard.api_fixtures."""
    from mu import dashboard
    os.makedirs(d, exist_ok=True)
    out = dict(node_fixtures())
    out.update(dashboard.api_fixtures())
    for name, obj in sorted(out.items()):
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    return sorted(out)


def main(argv):
    if argv[:1] == ["fixtures"] and len(argv) == 2:
        for n in write_fixtures(argv[1]):
            print(n)
        return 0
    if argv[:1] == ["scenarios"]:
        for k, v in SCENARIOS.items():
            print("%-9s %s" % (k, v))
        return 0
    sys.stderr.write("usage: dashfake.py fixtures DIR | scenarios\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
