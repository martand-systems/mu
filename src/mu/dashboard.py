"""μ dashboard: the local server, its API, the Mac's sampler and the `mu dashboard` commands (docs/design/dashboard.md
§4.3, §4.4, §4.6, §4.8). Phase 1a: no node-side change; a node without `features` is read with `hello`, `list`,
`status` and `log` on the old-node budget of §4.3.

    mu dashboard [status] [--json]    is it running, on which port (the URL never carries the token)
    mu dashboard open [--json]        start it if needed, then open the browser through a 0600 redirect file
    mu dashboard on | off             install or remove the LaunchAgent systems.martand.mu.dashboard (macOS)
    mu dashboard serve [--dev [--scenario NAME] [--fake-mac]] [--port N] [--static DIR]   (internal)

The server binds 127.0.0.1 only, checks the Host header, needs X-Mu-Token on every /api/ request (32 random bytes,
new at each start, in run/dashboard.token 0600), sends a strict CSP with Trusted Types on every response, and reaches
nodes only through dashnode.NodeReader (a read-only allowlist). It never reads the Keychain, keys or relay.auth.

Standard library only; psutil is imported from the default toolchain's own site-packages, added to sys.path by hand
(no .pth file runs), with a stdlib fallback.

Test hooks: MU_LAUNCHCTL, MU_LAUNCH_AGENTS_DIR, MU_OPENER (the program that opens the redirect file), MU_DASH_BATTERY
(1 or 0: stub the battery), MU_DASH_OLD_STEP (the old-node list step, s). Test faults (MU_JOBS_FAULT, comma-separated):
  bind-all            listen on 0.0.0.0                       no-host-check       any Host header is served
  no-token            /api/ without the token is served       fixed-token         the token does not change per start
  csp-unsafe-inline   the CSP allows inline scripts and styles open-url-argv     `open` passes the URL with the token
  retry-refused       a refused node (exit 5) is retried      poll-when-idle      nodes are polled with no tab, nothing due
  ignore-battery      on battery, list every 60 s, not 300 s  poll-list-2s        an old node's list every 2 s
  no-lock             no single-instance lock                 rename-field        mem.avail is sent as mem.available
  cpu-first-call      the Mac's CPU from a first cpu_percent  default-limit       list without limit=100
  serve-last-as-live  an old value is sent as current          node-from-config    every job labelled with the first node
  swap-mem-fields     a node's available memory sent as used
"""
import calendar
import codecs
import collections
import fcntl
import hmac
import http.server
import json
import os
import re
import secrets
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mu import dashnode, durable, jobs, proto  # noqa: E402
from mu.durable import fault  # noqa: E402
from mu.proto import MuError  # noqa: E402

API = 1
STALE_S = 15.0            # a value older than this is shown grey, "stale · as of …" (§5.1)
VISIBLE_S = 10.0          # "a tab is visible": an /api request in the last 10 s (§4.3)
LOG_KEEP = 2 << 20        # the server keeps the last 2 MB of a followed log (the page keeps 2 MB or 5,000 lines)
LOG_PIECE = 256 << 10     # one /log answer carries at most this many bytes
LOG_SESSIONS = 8          # open `log follow=1` sessions at most (one per job whose log is being read)
METRICS_CAP = 16 << 20
SERIES_S = 3600           # host series kept in memory (1 h)
CARD_SERIES_S = 600       # the machine cards' sparklines (10 min)
MAC_STEP = 2.0            # the Mac sampler's step
OLD_STEP = 15.0           # an old node's list step while watched and something is unfinished (§4.3, 3.0a may make it 30)
OLD_IDLE_STEP = 60.0
BG_STEP = 60.0            # no tab, a job unfinished, on mains
BATTERY_STEP = 300.0      # ... on battery
FEATURE_BACKSTOP = 300.0  # a node with `watch`: list when rev moves (at most every 5 s), else every 5 min
HELLO_STEP = 600.0
RECENT_SUBMIT_S = 120.0
BACKOFF = (5.0, 10.0, 20.0, 60.0)
LIST_LIMIT = 100
LABEL = "systems.martand.mu.dashboard"
PATH_ENV = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
HERE = os.path.dirname(os.path.abspath(__file__))
RECIPE = os.path.dirname(os.path.dirname(HERE))
STATIC_DIR = os.path.join(RECIPE, "share", "dashboard")
NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml", ".woff2": "font/woff2", ".png": "image/png", ".txt": "text/plain; charset=utf-8"}
ROOT_FILES = ("index.html", "theme.js", "favicon.svg")
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "worker-src 'none'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; "
       "require-trusted-types-for 'script'; trusted-types 'none'")
NO_BUILD_PAGE = (b"<!doctype html><meta charset=utf-8><title>\xce\xbc dashboard</title>"
                 b"<p>The dashboard's page is not built in this copy of \xce\xbc (share/dashboard/ is missing). "
                 b"The API answers; see docs/design/dashboard.md \xc2\xa74.8.</p>")


def csp():
    if fault("csp-unsafe-inline"):
        return CSP.replace("script-src 'self'", "script-src 'self' 'unsafe-inline'").replace(
            "style-src 'self'", "style-src 'self' 'unsafe-inline'")
    return CSP


# ------------------------------------------------------------------------------------------------ small helpers
def num(x, lo=None, hi=None):
    """A finite int or float from untrusted JSON (never a bool or a string), clamped to [LO, HI]; else None."""
    if isinstance(x, bool) or not isinstance(x, (int, float)) or x != x or x in (float("inf"), float("-inf")):
        return None
    if lo is not None and x < lo:
        x = lo
    if hi is not None and x > hi:
        x = hi
    return x


def text(x, cap=200):
    return proto.clean_text(x, cap) if isinstance(x, str) else None


def iso_epoch(s):
    """'2026-11-14T10:15:22Z' -> epoch seconds, or None."""
    if not isinstance(s, str) or not re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$", s):
        return None
    try:
        return float(calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ")))
    except ValueError:
        return None


def rnd(x, n=1):
    return None if x is None else round(x, n)


def run_dir(dev=False):
    d = os.path.join(jobs.mu_home(), "run")
    for p in ([d, os.path.join(d, "dev")] if dev else [d]):
        os.makedirs(p, mode=0o700, exist_ok=True)
        os.chmod(p, 0o700)
    return os.path.join(d, "dev") if dev else d


def write_private(path, data):
    """Write DATA (str or bytes) to PATH, mode 0600, by rename."""
    tmp = "%s.tmp.%d" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data.encode() if isinstance(data, str) else data)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def mac_version():
    v = os.environ.get("MU_INFRA_VERSION")
    if v:
        return v
    try:
        with open(os.path.join(jobs.mu_home(), "default"), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


class Log:
    """logs/dashboard.log, capped at 512 kB (one older copy kept). Never given the token or request headers."""

    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)

    def __call__(self, msg):
        line = "%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), proto.clean_text(msg, 600).replace("\n", " "))
        with self.lock:
            try:
                if os.path.getsize(self.path) > 512 << 10:
                    os.replace(self.path, self.path + ".1")
            except OSError:
                pass
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError:
                pass


# ------------------------------------------------------------------------------------------------ jobs.index and records
class IndexSource:
    """$MU_HOME/jobs.index (one JSON line per submit, written by `mu submit`) and the records it leads to: the
    project's .mu/jobs/<id>.json and runs/<id>.json."""

    def __init__(self, clock):
        self.clock = clock
        self.path = jobs.index_path()
        self._key, self._map = None, {}
        self._rec = {}
        self.lock = threading.Lock()

    def entries(self):
        try:
            st = os.stat(self.path)
            key = (st.st_mtime_ns, st.st_size)
        except OSError:
            return {}
        with self.lock:
            if key != self._key:
                self._map, self._key = jobs.index_read(), key
            return self._map

    def get(self, job_id):
        return self.entries().get(job_id)

    def recent(self, node, within):
        now = self.clock.wall()
        for e in self.entries().values():
            if e.get("node") == node:
                t = iso_epoch(e.get("accepted")) or iso_epoch(e.get("submit_started"))
                if t is not None and now - t <= within:
                    return True
        return False

    def _read(self, path, sealed):
        try:
            st = os.stat(path)
        except OSError:
            return None
        k = (path, st.st_mtime_ns, st.st_size)
        with self.lock:
            if k in self._rec:
                return self._rec[k]
        if sealed:
            val = durable.read_record(path)
        else:
            try:
                with open(path, encoding="utf-8") as f:
                    val = json.loads(f.read(4 << 20))
            except (OSError, ValueError):
                val = None
        with self.lock:
            if len(self._rec) > 512:
                self._rec.clear()
            self._rec[k] = val if isinstance(val, dict) else None
        return self._rec[k]

    def record(self, job_id):
        """The .mu/jobs record of a job this Mac submitted (the spec it sent), or None."""
        e = self.get(job_id)
        root = e and e.get("project_root")
        if not isinstance(root, str) or not os.path.isabs(root):
            return None
        return self._read(os.path.join(root, ".mu", "jobs", job_id + ".json"), True)

    def run_record(self, job_id):
        e = self.get(job_id)
        root = e and e.get("project_root")
        if not isinstance(root, str) or not os.path.isabs(root):
            return None
        return self._read(os.path.join(root, "runs", job_id + ".json"), False)


class FakeIndex(IndexSource):
    """The dev scenario's jobs.index and records (no file of the real $MU_HOME is read)."""

    def __init__(self, clock, scenario):
        IndexSource.__init__(self, clock)
        self.sc = scenario

    def entries(self):
        return {e["job_id"]: e for e in self.sc.index()}

    def record(self, job_id):
        spec = self.sc.spec(job_id)
        return {"schema": 1, "job_id": job_id, "spec": spec, "state": "queued", "accepted": spec["submitted"]["at"]} \
            if spec else None

    def run_record(self, job_id):
        return None


# ------------------------------------------------------------------------------------------------ the Mac's sampler
def load_psutil():
    """psutil from the running Python's own site-packages (the default toolchain's env), added by hand: -I -S skipped
    site, so no .pth file runs. None when it is not there."""
    try:
        import sysconfig
        paths = [sysconfig.get_paths().get(k) for k in ("purelib", "platlib")]
        venv = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
        if os.path.isfile(os.path.join(venv, "pyvenv.cfg")):   # a venv (CI): -S leaves sys.prefix at the base
            paths.insert(0, os.path.join(venv, "lib", "python%d.%d" % sys.version_info[:2], "site-packages"))
        for p in paths:
            if p and os.path.isdir(p) and p not in sys.path:
                sys.path.append(p)
        import psutil
        return psutil
    except Exception:
        return None


def sysctl(*names):
    try:
        r = subprocess.run(["/usr/sbin/sysctl", "-n"] + list(names), capture_output=True, timeout=5,
                           stdin=subprocess.DEVNULL)
        return r.stdout.decode().split("\n") if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


class MacSampler:
    """This Mac's CPU and memory every 2 s while a tab is visible; 1 h in memory."""

    def __init__(self, clock, psutil_mod="auto"):
        self.clock = clock
        self.ps = load_psutil() if psutil_mod == "auto" else psutil_mod
        self.lock = threading.Lock()
        self.cpu = collections.deque(maxlen=int(SERIES_S / MAC_STEP) + 10)
        self.mem = collections.deque(maxlen=int(SERIES_S / MAC_STEP) + 10)
        self.last, self.t, self.primed = None, None, False
        self._battery, self._battery_t = None, -1e9
        self._pressure, self._pressure_t = None, -1e9
        self.cores = self._cores()

    def _cores(self):
        out = {"logical": os.cpu_count(), "physical": None, "perf": None, "eff": None}
        if self.ps:
            try:
                out["physical"] = self.ps.cpu_count(logical=False)
            except Exception:
                pass
        if sys.platform == "darwin":
            v = sysctl("hw.perflevel0.logicalcpu", "hw.perflevel1.logicalcpu")
            if v and v[0].strip().isdigit():
                out["perf"] = int(v[0])
                out["eff"] = int(v[1]) if len(v) > 1 and v[1].strip().isdigit() else 0
        return out

    def battery(self):
        """{"on_battery": bool, "percent": float} or None (no battery), cached 60 s. MU_DASH_BATTERY stubs it."""
        stub = os.environ.get("MU_DASH_BATTERY")
        if stub in ("0", "1"):
            return {"on_battery": stub == "1", "percent": 50.0}
        now = time.monotonic()
        if now - self._battery_t > 60:
            b = None
            if self.ps:
                try:
                    s = self.ps.sensors_battery()
                    if s is not None:
                        b = {"on_battery": not bool(s.power_plugged), "percent": rnd(float(s.percent))}
                except Exception:
                    b = None
            self._battery, self._battery_t = b, now
        return self._battery

    def on_battery(self):
        b = self.battery()
        return bool(b and b["on_battery"])

    def _pressure_now(self):
        if sys.platform != "darwin":
            return None
        now = time.monotonic()
        if now - self._pressure_t > 10:
            v = sysctl("kern.memorystatus_vm_pressure_level")
            lvl = v[0].strip() if v else ""
            self._pressure = {"1": "ok", "2": "warn", "4": "critical"}.get(lvl)
            self._pressure_t = now
        return self._pressure

    def _stdlib_mem(self):
        total = avail = None
        if sys.platform == "darwin":
            v = sysctl("hw.memsize", "hw.pagesize")
            try:
                total, page = int(v[0]), int(v[1])
                r = subprocess.run(["/usr/bin/vm_stat"], capture_output=True, timeout=5, stdin=subprocess.DEVNULL)
                vs = dict(re.findall(r"^Pages (free|inactive|speculative|purgeable)[^:]*:\s+([0-9]+)",
                                     r.stdout.decode(), re.M))
                avail = sum(int(vs.get(k, 0)) for k in ("free", "inactive", "speculative")) * page
            except (TypeError, ValueError, IndexError, OSError, subprocess.TimeoutExpired):
                pass
        else:
            try:
                with open("/proc/meminfo") as f:
                    m = dict(re.findall(r"^(\w+):\s+([0-9]+) kB", f.read(), re.M))
                total, avail = int(m["MemTotal"]) * 1024, int(m["MemAvailable"]) * 1024
            except (OSError, KeyError, ValueError):
                pass
        return total, avail

    def sample(self):
        """Take one sample; returns it (None for psutil's priming call)."""
        t = self.clock.wall()
        cpu, per = None, []
        if self.ps:
            if fault("cpu-first-call"):
                self.ps.cpu_percent(None)                # the bug: a first call with no interval, every time
                cpu = self.ps.cpu_percent(None)
                per = self.ps.cpu_percent(None, percpu=True)
            elif not self.primed:
                self.ps.cpu_percent(None)
                self.ps.cpu_percent(None, percpu=True)
                self.primed = True
                return None
            else:
                cpu = self.ps.cpu_percent(None)
                per = self.ps.cpu_percent(None, percpu=True)
            vm = self.ps.virtual_memory()
            total, avail = int(vm.total), int(vm.available)
        else:
            total, avail = self._stdlib_mem()
        try:
            load = [round(x, 2) for x in os.getloadavg()]
        except OSError:
            load = None
        s = {"t": round(t, 3), "cpu_pct": rnd(cpu), "per_core": [rnd(x) for x in per],
             "load": load, "mem": {"total": total, "available": avail,
                                   "used": (total - avail) if total is not None and avail is not None else None,
                                   "pressure": self._pressure_now()}}
        with self.lock:
            self.last, self.t = s, t
            self.cpu.append((round(t, 1), s["cpu_pct"]))
            self.mem.append((round(t, 1), s["mem"]["used"]))
        return s

    def card(self, series_s=CARD_SERIES_S):
        now = self.clock.wall()
        with self.lock:
            s, t = self.last, self.t
            cpu = [[a, b] for a, b in self.cpu if a >= now - series_s]
            mem = [[a, b] for a, b in self.mem if a >= now - series_s]
        stale = t is None or now - t > STALE_S
        if fault("serve-last-as-live") and t is not None:
            stale, t = False, now
        s = s or {}
        return {"name": "This Mac", "host": socket.gethostname().split(".")[0][:64],
                "sampler": "psutil" if self.ps else "stdlib", "t": rnd(t, 3), "stale": stale,
                "cpu_pct": s.get("cpu_pct"), "per_core": s.get("per_core", []), "load": s.get("load"),
                "cores": dict(self.cores),
                "mem": dict(s.get("mem") or {"total": None, "available": None, "used": None, "pressure": None}),
                "battery": self.battery(), "mu_version": mac_version(),
                "series": {"cpu_pct": cpu, "mem_used": mem}}


# ------------------------------------------------------------------------------------------------ one node
class NodeWorker:
    """One paired node, read through its NodeReader on the traffic plan of §4.3. tick() does whatever is due now;
    the server's thread calls it every half second, the tests call it by hand with a manual clock."""

    def __init__(self, app, reader):
        self.app, self.r, self.clock = app, reader, app.clock
        self.name = reader.name
        self.lock = threading.Lock()
        self.hello, self.hello_t, self.features = None, None, None
        self.offset_s = 0.0
        self.stats, self.stats_t = None, None
        self.series = collections.deque(maxlen=2000)
        self.jobs, self.list_t, self.more = {}, None, False
        self.state, self.error, self.offline_since = "connecting", None, None
        self.reconnecting = False
        self.retry_at, self.backoff_i, self.retry_asked = None, 0, False
        self.need_reconcile = True
        self.next_list = self.next_hello = 0.0
        self.last_list_m = -1e9
        self.stats_rev, self.list_rev = None, None
        self.last_w = self.last_m = None
        self.watch_stop, self.watch_thread = None, None
        self.wake = threading.Event()

    # ---------------------------------------------------------------- the plan
    def unfinished(self):
        return any(s.get("state") in ("queued", "running") for s in self.jobs.values())

    def plan(self):
        """-> (wanted, list step in s)."""
        visible = self.app.visible()
        recent = self.app.index.recent(self.name, RECENT_SUBMIT_S)
        busy = self.unfinished() or recent
        if visible:
            if fault("poll-list-2s") and not self.features:
                return True, 2.0
            if self.features:
                return True, FEATURE_BACKSTOP
            return True, (self.app.old_step if busy else OLD_IDLE_STEP)
        if busy:
            return True, (BATTERY_STEP if self.app.mac.on_battery() and not fault("ignore-battery") else BG_STEP)
        if fault("poll-when-idle"):
            return True, BG_STEP
        return False, None

    def tick(self):
        now_m, now_w = self.clock.mono(), self.clock.wall()
        if self.last_m is not None:
            jump = (now_w - self.last_w) - (now_m - self.last_m)
            if abs(jump) > 30.0:      # a sleep and wake (the wall clock moved, monotonic time did not)
                self.app.log("%s: wall clock jumped %.0f s: reconcile" % (self.name, jump))
                with self.lock:
                    self.need_reconcile, self.reconnecting = True, True
                self.stop_watch()
        self.last_w, self.last_m = now_w, now_m
        if self.state == "refused" and not self.retry_asked and not fault("retry-refused"):
            return                    # exit 5: never retried automatically; the page's retry button asks
        if self.retry_at is not None and now_m < self.retry_at and not self.retry_asked:
            return
        wanted, step = self.plan()
        if not wanted:
            self.stop_watch()
            return
        if self.need_reconcile or self.retry_asked:
            self.retry_asked = False
            self.reconcile()
            self.next_list = self.clock.mono() + step
            return
        if self.features and self.app.visible():
            self.ensure_watch()
        else:
            self.stop_watch()
        now_m = self.clock.mono()
        due = now_m >= self.next_list
        if self.features and self.stats_rev is not None and self.stats_rev != self.list_rev and \
                now_m - self.last_list_m >= 5.0:
            due = True
        if due:
            if self.do_list():
                self.next_list = self.clock.mono() + step
        elif now_m >= self.next_hello:
            self.do_hello()

    def poke(self):
        self.wake.set()

    # ---------------------------------------------------------------- calls
    def _call(self, verb, **args):
        try:
            return self.r.call(verb, **args)
        except MuError as e:
            self.on_error(e, verb)
            return None

    def on_ok(self):
        with self.lock:
            if self.state != "online":
                self.app.log("%s: online" % self.name)
            self.state, self.error, self.offline_since, self.reconnecting = "online", None, None, False
            self.retry_at, self.backoff_i = None, 0

    def on_error(self, e, verb):
        info = dashnode.error_info(e)
        now_w, now_m = self.clock.wall(), self.clock.mono()
        with self.lock:
            if e.code == proto.EXIT_REFUSED:
                state, delay = "refused", None
            elif e.code == proto.EXIT_NODE_CANNOT:
                state, delay = "cannot", 60.0
            elif "keychain" in (e.error + " " + e.message).lower():
                state, delay = "keychain", 300.0
            elif e.code == proto.EXIT_UNREACHABLE:
                state, delay = "offline", BACKOFF[min(self.backoff_i, len(BACKOFF) - 1)]
                self.backoff_i += 1
            else:
                state, delay = "error", BACKOFF[min(self.backoff_i, len(BACKOFF) - 1)]
                self.backoff_i += 1
            if self.state not in ("offline", "refused", "cannot", "keychain", "error") or self.offline_since is None:
                self.offline_since = now_w
            self.state = state
            self.retry_at = None if delay is None else now_m + delay
            info["retry_at"] = None if delay is None else round(now_w + delay, 1)
            info["auto_retry"] = delay is not None
            info["verb"] = verb
            self.error = info
            self.need_reconcile = True
        self.app.log("%s: %s (%s, exit %d)" % (self.name, state, e.error, e.code))
        self.stop_watch()

    def reconcile(self):
        """On start, after a wake and after a reconnect: a new master of our own, hello, stats, list (§4.3)."""
        try:
            self.r.close_master()
        except Exception:
            pass
        if not self.do_hello():
            return False
        if self.features and "stats" in self.features:
            ans = self._call("stats")
            if ans is None and self.state != "online":
                return False
            if ans is not None:
                self.on_stats(ans)
        if not self.do_list():
            return False
        with self.lock:
            self.need_reconcile, self.reconnecting = False, False
        return True

    def do_hello(self):
        w0 = self.clock.wall()
        ans = self._call("hello", protocol=str(proto.PROTOCOL))
        if ans is None:
            return False
        w1 = self.clock.wall()
        nt = iso_epoch(ans.get("time"))
        feats = ans.get("features")
        with self.lock:
            self.hello, self.hello_t = ans, w1
            self.features = [f for f in feats if isinstance(f, str)][:32] if isinstance(feats, list) else None
            if nt is not None:
                off = nt - (w0 + w1) / 2.0
                self.offset_s = off if abs(off) > 2.0 else 0.0
            self.next_hello = self.clock.mono() + HELLO_STEP
        self.on_ok()
        return True

    def do_list(self):
        args = {} if fault("default-limit") else {"limit": str(LIST_LIMIT)}
        ans = self._call("list", **args)
        if ans is None:
            return False
        rows = ans.get("jobs") if isinstance(ans.get("jobs"), list) else []
        jobs_ = {}
        for s in rows:
            if isinstance(s, dict) and proto.JOB_ID.match(str(s.get("job_id") or "")):
                jobs_[s["job_id"]] = s
        fin = sum(1 for s in jobs_.values() if s.get("state") in proto.FINISHED)
        with self.lock:
            self.jobs, self.list_t = jobs_, self.clock.wall()
            self.more = fin >= int(args.get("limit", 20))
            self.last_list_m = self.clock.mono()
            self.list_rev = ans["rev"] if isinstance(ans.get("rev"), int) else self.stats_rev
        self.on_ok()
        return True

    def status(self, job_id):
        """One job's status, read when its detail opens (cached 2 s)."""
        ans = self._call("status", id=job_id)
        if ans is not None:
            with self.lock:
                if proto.JOB_ID.match(str(ans.get("job_id") or "")) and ans["job_id"] in self.jobs:
                    self.jobs[ans["job_id"]] = ans
            self.on_ok()
        return ans

    def on_stats(self, ans):
        if not isinstance(ans, dict) or ans.get("ok") is False:
            return
        age = num(ans.get("age_s"), 0, 1e7) or 0.0
        t = self.clock.wall() - age
        host = ans.get("host") if isinstance(ans.get("host"), dict) else {}
        with self.lock:
            self.stats, self.stats_t = ans, t
            self.series.append((round(t, 1), num(host.get("cpu_pct"), 0, 100)))
            if isinstance(ans.get("rev"), int):
                self.stats_rev = ans["rev"]
                if self.list_rev is not None and self.stats_rev != self.list_rev:
                    self.wake.set()

    # ---------------------------------------------------------------- watch (nodes with features)
    def ensure_watch(self):
        if self.watch_thread is not None and self.watch_thread.is_alive():
            return
        stop = threading.Event()
        self.watch_stop = stop
        self.watch_thread = threading.Thread(target=self._watch, args=(stop,), daemon=True,
                                             name="watch-%s" % self.name)
        self.watch_thread.start()

    def stop_watch(self):
        if self.watch_stop is not None:
            self.watch_stop.set()
        self.watch_stop = None

    def _watch(self, stop):
        buf = bytearray()

        def on_data(b):
            buf.extend(b)
            while b"\n" in buf:
                i = buf.index(b"\n")
                line = bytes(buf[:i])
                del buf[:i + 1]
                try:
                    self.on_stats(proto.clean_obj(json.loads(line.decode("utf-8"))))
                except (ValueError, UnicodeDecodeError):
                    pass
            if len(buf) > 1 << 20:
                del buf[:]
        try:
            self.r.stream("watch", on_data, stop=stop, interval="2", max="600")
        except MuError as e:
            if not stop.is_set():
                self.on_error(e, "watch")

    # ---------------------------------------------------------------- what the API shows
    def node_epoch(self, s):
        t = iso_epoch(s)
        return None if t is None else t - self.offset_s

    def live(self, now):
        """{job_id: live figures} from the newest stats, or {}."""
        st, t = self.stats, self.stats_t
        if not st or t is None:
            return {}
        stale = now - t > STALE_S
        if fault("serve-last-as-live"):
            stale, t = False, now
        out = {}
        for r in st.get("running") if isinstance(st.get("running"), list) else []:
            if isinstance(r, dict) and proto.JOB_ID.match(str(r.get("job_id") or "")):
                out[r["job_id"]] = {"t": rnd(t, 1), "stale": stale, "cpu_cores": num(r.get("cpu_cores"), 0, 4096),
                                    "anon": num(r.get("anon"), 0), "file": num(r.get("file"), 0),
                                    "shmem": num(r.get("shmem"), 0), "peak": num(r.get("peak"), 0),
                                    "max": num(r.get("max"), 0), "pids": num(r.get("pids"), 0),
                                    "log_size": num(r.get("log_size"), 0)}
        return out

    def card(self, series_s=CARD_SERIES_S):
        now = self.clock.wall()
        with self.lock:
            h, ht, st, t = self.hello, self.hello_t, self.stats, self.stats_t
            series = [[a, b] for a, b in self.series if a >= now - series_s]
            state, err, since = self.state, self.error, self.offline_since
            feats, more, list_t, njobs = self.features, self.more, self.list_t, len(self.jobs)
            reconnecting = self.reconnecting
        old = h is not None and not feats
        hello = None
        attention = []
        if h:
            mu = h.get("mu") if isinstance(h.get("mu"), dict) else {}
            hw = h.get("hw") if isinstance(h.get("hw"), dict) else {}
            disk = h.get("disk") if isinstance(h.get("disk"), dict) else {}
            au = h.get("autoupdate") if isinstance(h.get("autoupdate"), dict) else {}
            q = h.get("queue") if isinstance(h.get("queue"), dict) else {}
            mem_kb = num(hw.get("mem_total_kb"), 0)
            hello = {"t": rnd(ht, 1), "protocol": num(h.get("protocol")), "key": text(h.get("key"), 64),
                     "mu_version": text(mu.get("default"), 64),
                     "mu_installed": [text(x, 64) for x in mu.get("installed", [])[:20] if isinstance(x, str)]
                     if isinstance(mu.get("installed"), list) else [],
                     "cpu_model": text(hw.get("cpu_model"), 120), "cores_logical": num(hw.get("cores_logical"), 0),
                     "cores_physical": num(hw.get("cores_physical"), 0),
                     "mem_total": None if mem_kb is None else int(mem_kb) * 1024, "kernel": text(hw.get("kernel"), 120),
                     "wsl": bool(hw.get("wsl")),
                     "disk": {"free_gb": num(disk.get("free_gb"), 0), "min_free_gb": num(disk.get("min_free_gb"), 0)},
                     "runner": text(h.get("runner"), 32), "last_stop": text(h.get("last_stop"), 32),
                     "autoupdate": {"enabled": bool(au.get("enabled")), "last_result": text(au.get("last_result"), 32)},
                     "queue": {"running": num(q.get("running"), 0), "queued": num(q.get("queued"), 0)},
                     "clock_offset_s": round(self.offset_s, 1)}
            mine = mac_version()
            if hello["mu_version"] and mine and hello["mu_version"] != mine:
                attention.append({"kind": "version", "text": "μ %s on %s, %s on this Mac" % (
                    hello["mu_version"], self.name, mine)})
            if hello["last_stop"] and hello["last_stop"] not in ("clean", "first-start"):
                attention.append({"kind": "last-stop", "text": "last stop: %s" % hello["last_stop"]})
            fg, mg = hello["disk"]["free_gb"], hello["disk"]["min_free_gb"]
            if fg is not None and mg is not None and fg < mg:
                attention.append({"kind": "disk", "text": "disk low: %s GB free (minimum %s GB)" % (fg, mg)})
            if hello["autoupdate"]["last_result"] == "failed":
                attention.append({"kind": "autoupdate", "text": "auto-update failed on %s" % self.name})
            if hello["runner"] and hello["runner"] != "active":
                attention.append({"kind": "runner", "text": "the job runner is %s" % hello["runner"]})
        stats = None
        if st and t is not None and not old:
            hs = st.get("host") if isinstance(st.get("host"), dict) else {}
            mem = hs.get("mem") if isinstance(hs.get("mem"), dict) else {}
            total, avail = num(mem.get("total"), 0), num(mem.get("avail"), 0)
            used = None if total is None or avail is None else max(0, total - avail)
            if fault("swap-mem-fields"):
                used, avail = avail, used
            stale = now - t > STALE_S
            tt = t
            if fault("serve-last-as-live"):
                stale, tt = False, now
            q = st.get("queue") if isinstance(st.get("queue"), dict) else {}
            psi = hs.get("psi") if isinstance(hs.get("psi"), dict) else {}
            dk = hs.get("disk") if isinstance(hs.get("disk"), dict) else {}
            memo = {"total": total, "used": used, "cached": num(mem.get("cached"), 0), "shmem": num(mem.get("shmem"), 0)}
            memo["available" if fault("rename-field") else "avail"] = avail
            stats = {"t": rnd(tt, 1), "stale": stale, "cpu_pct": num(hs.get("cpu_pct"), 0, 100),
                     "per_core": [num(x, 0, 100) for x in hs.get("per_core", [])[:1024]]
                     if isinstance(hs.get("per_core"), list) else [],
                     "load": [num(x, 0) for x in hs.get("load", [])[:3]] if isinstance(hs.get("load"), list) else None,
                     "mem": memo,
                     "psi": {"cpu_some10": num(psi.get("cpu_some10"), 0, 100),
                             "mem_some10": num(psi.get("mem_some10"), 0, 100)},
                     "disk": {"free_gb": num(dk.get("free_gb"), 0), "min_free_gb": num(dk.get("min_free_gb"), 0)},
                     "uptime_s": num(hs.get("uptime_s"), 0),
                     "queue": {"running": num(q.get("running"), 0), "queued": num(q.get("queued"), 0)},
                     "node_stale": bool(st.get("stale"))}
        return {"name": self.name, "link": "relay", "state": state, "reconnecting": reconnecting,
                "features": list(feats) if feats else [], "old_node": old,
                "old_node_text": ("update μ on %s for live statistics" % self.name) if old else None,
                "error": dict(err) if err else None,
                "offline_since": rnd(since, 1) if state != "online" else None,
                "hello": hello, "stats": stats, "series": {"cpu_pct": series},
                "attention": attention, "jobs_listed": njobs, "more": more, "list_t": rnd(list_t, 1),
                "list_stale": list_t is None or now - list_t > max(STALE_S, 2 * self.app.old_step + 5)}


# ------------------------------------------------------------------------------------------------ logs and metrics
class LogBuf:
    """The last 2 MB of one job's log, filled by a `log follow=1` session while the page reads it."""

    def __init__(self, worker, job_id, size_hint):
        self.w, self.job_id = worker, job_id
        self.data, self.start = bytearray(), max(0, int(size_hint or 0) - LOG_KEEP)
        self.lock = threading.Lock()
        self.thread, self.stop, self.done, self.error = None, None, False, None
        self.last_req, self.started = time.monotonic(), -1e9

    @property
    def end(self):
        return self.start + len(self.data)

    def ensure(self):
        now = time.monotonic()
        self.last_req = now
        if self.done or (self.thread is not None and self.thread.is_alive()):
            return
        if self.error is not None and now - self.started < 15.0:
            return                    # a failed session is retried after 15 s, not on every poll
        if self.w.state not in ("online", "connecting"):
            return
        self.started = now
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(self.stop,), daemon=True, name="log-" + self.job_id)
        self.thread.start()

    def _run(self, stop):
        def on_data(b):
            with self.lock:
                self.data.extend(b)
                if len(self.data) > LOG_KEEP:
                    cut = len(self.data) - LOG_KEEP
                    del self.data[:cut]
                    self.start += cut
        try:
            rc = self.w.r.stream("log", on_data, stop=stop, id=self.job_id, offset=str(self.end), follow="1")
            if rc == 0 and not stop.is_set():
                self.done = True
                self.error = None
        except MuError as e:
            self.error = dashnode.error_info(e)
        except Exception:
            self.error = {"exit": 1, "error": "internal", "message": "the log could not be read", "next": "mu log"}

    def close(self):
        if self.stop is not None:
            self.stop.set()

    def piece(self, offset):
        with self.lock:
            start, end = self.start, self.end
            truncated = offset < start
            off = min(max(offset, start), end)
            raw = bytes(self.data[off - start:off - start + LOG_PIECE])
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        txt = dec.decode(raw, final=False)
        pending = len(dec.getstate()[0])
        used = len(raw) - pending
        return {"offset": off, "next": off + used, "start": start, "size": end, "truncated_before": truncated,
                "text": proto.clean_text(txt, LOG_PIECE + 16)}


# ------------------------------------------------------------------------------------------------ the app
class App:
    def __init__(self, dev=False, clock=None, readers=None, index=None, mac=None, scenario=None, port=0,
                 version=None, log=None):
        self.dev, self.scenario = dev, scenario
        self.clock = clock or (scenario.clock if scenario else None) or _RealClock()
        self.started = self.clock.wall()
        self.version = version or mac_version()
        self.token = (hashlib_token() if fault("fixed-token") else secrets.token_hex(32))
        self.port = port
        self.log = log or (lambda m: None)
        self.index = index or IndexSource(self.clock)
        self.mac = mac or MacSampler(self.clock)
        self.old_step = float(os.environ.get("MU_DASH_OLD_STEP") or OLD_STEP)
        self.last_api = -1e9
        self.workers = {}
        for name, r in sorted((readers or {}).items()):
            self.workers[name] = NodeWorker(self, r)
        self.logs = collections.OrderedDict()
        self.metrics = {}
        self.lock = threading.Lock()
        self.status_cache = {}
        self.detail_seen = {}
        self.open_file = None
        self.static = {}

    # ---------------------------------------------------------------- activity
    def visible(self):
        return self.clock.mono() - self.last_api < VISIBLE_S

    def touch(self):
        was = self.visible()
        self.last_api = self.clock.mono()
        if not was:
            for w in self.workers.values():
                w.poke()

    def anything_unfinished(self):
        return any(w.unfinished() for w in self.workers.values())

    # ---------------------------------------------------------------- jobs
    def rows(self, node=None, mine=None):
        now = self.clock.wall()
        names = sorted(self.workers)
        out = []
        for name in names:
            w = self.workers[name]
            with w.lock:
                items = list(w.jobs.values())
                list_t = w.list_t
                live = w.live(now)
            for s in items:
                out.append(self.row(w, s, list_t, live.get(s["job_id"]), now, names))
        if node:
            out = [r for r in out if r["node"] == node]
        if mine is not None:
            out = [r for r in out if r["mine"] == mine]
        order = {"running": 0, "queued": 1}
        out.sort(key=lambda r: (order.get(r["state"], 2), r["position"] or 0 if r["state"] == "queued" else 0,
                                -(r["ended_at"] or r["started_at"] or r["queued_at"] or 0)))
        return out

    def row(self, w, s, list_t, live, now, names):
        jid = s["job_id"]
        idx = self.index.get(jid)
        rec = self.index.record(jid) if idx else None
        spec = rec.get("spec") if isinstance(rec, dict) and isinstance(rec.get("spec"), dict) else {}
        lim = spec.get("limits") if isinstance(spec.get("limits"), dict) else None
        st = s.get("state") if s.get("state") in proto.STATES else "unknown"
        u = s.get("usage") if isinstance(s.get("usage"), dict) else None
        usage = None
        if u:
            ev = u.get("mem_events") if isinstance(u.get("mem_events"), dict) else {}
            usage = {"cpu_s": num(u.get("cpu_s"), 0), "user_s": num(u.get("user_s"), 0), "sys_s": num(u.get("sys_s"), 0),
                     "cores_avg": num(u.get("cores_avg"), 0, 4096), "mem_peak_bytes": num(u.get("mem_peak_bytes"), 0),
                     "mem_work_peak_bytes": num(u.get("mem_work_peak_bytes"), 0),
                     "pids_peak": num(u.get("pids_peak"), 0),
                     "mem_events": {"max": num(ev.get("max"), 0), "oom_kill": num(ev.get("oom_kill"), 0)},
                     "source": u.get("source") if u.get("source") in ("cgroup", "runner-sampled") else None}
        node = names[0] if fault("node-from-config") and names else w.name
        return {"job_id": jid, "short": jid[-6:], "node": node, "name": text(s.get("name"), 200),
                "project": text((idx or {}).get("project"), 80), "project_id": text(s.get("project_id"), 80),
                "mine": bool(idx), "state": st, "position": num(s.get("position"), 0),
                "attempt": num(s.get("attempt"), 0), "queued_at": w.node_epoch(s.get("queued_at")),
                "started_at": w.node_epoch(s.get("started_at")), "ended_at": w.node_epoch(s.get("ended_at")),
                "exit_code": num(s.get("exit_code")), "note": text(s.get("note"), 400),
                "log_size": num(s.get("log_size"), 0), "cancel_requested": bool(s.get("cancel_requested")),
                "usage": usage, "live": live if st == "running" else None,
                "limits": None if lim is None else {"mem_bytes": num(lim.get("mem_bytes"), 0),
                                                    "time_s": num(lim.get("time_s"), 0),
                                                    "cores": num(lim.get("cores"), 0)},
                "as_of": rnd(list_t, 1), "stale": list_t is None or now - list_t > max(STALE_S, 2 * self.old_step + 5)}

    def find(self, job_id):
        for w in self.workers.values():
            with w.lock:
                if job_id in w.jobs:
                    return w, dict(w.jobs[job_id])
        return None, None

    def commands(self, job_id, node):
        on = " --on %s" % node if len(self.workers) > 1 else ""
        return {"status": "mu status %s%s" % (job_id, on), "log": "mu log %s -f%s" % (job_id, on),
                "fetch": "mu fetch %s%s" % (job_id, on), "cancel": "mu cancel %s%s" % (job_id, on),
                "node": "mu compute status %s" % node}

    # ---------------------------------------------------------------- API answers
    def api_session(self):
        return {"version": self.version, "started": rnd(self.started, 3), "now": rnd(self.clock.wall(), 3),
                "dev": self.dev, "scenario": self.scenario.name if self.scenario else None,
                "host": socket.gethostname().split(".")[0][:64], "stale_after_s": STALE_S,
                "visible_after_s": VISIBLE_S, "poll_ms": {"overview": 2000, "job": 2000, "log": 1000},
                "log_keep_bytes": LOG_KEEP, "list_limit": LIST_LIMIT}

    def api_overview(self):
        rows = self.rows()
        nodes = [self.workers[n].card() for n in sorted(self.workers)]
        online = sum(1 for n in nodes if n["state"] == "online")
        return {"now": rnd(self.clock.wall(), 3), "mac": self.mac.card(), "nodes": nodes, "jobs": rows,
                "more": {n["name"]: n["more"] for n in nodes},
                "summary": {"nodes": len(nodes), "online": online,
                            "running": sum(1 for r in rows if r["state"] == "running"),
                            "queued": sum(1 for r in rows if r["state"] == "queued")}}

    def api_jobs(self, query):
        node = query.get("node")
        if node and not proto.NODE_NAME.match(node):
            raise ApiError(400, "bad-node", "not a node name")
        frm = query.get("from", "all")
        if frm not in ("all", "mine"):
            raise ApiError(400, "bad-from", "from is all or mine")
        rows = self.rows(node=node, mine=True if frm == "mine" else None)
        return {"now": rnd(self.clock.wall(), 3), "jobs": rows,
                "more": {n: self.workers[n].more for n in sorted(self.workers)}}

    def api_job(self, job_id):
        w, s = self.find(job_id)
        if w is None:
            raise ApiError(404, "no-such-job", "no node listed this job")
        now = self.clock.wall()
        key = (w.name, job_id)
        cached = self.status_cache.get(key)
        opened = now - self.detail_seen.get(key, -1e9) > VISIBLE_S
        self.detail_seen[key] = now
        if opened or cached is None:
            # `status id=` once when the detail opens (§4.3); then the node's list (and watch) keep the row current
            ans = w.status(job_id) if w.state == "online" else None
            if ans is not None:
                self.status_cache[key] = (now, ans)
                s = ans
        elif cached[0] >= (w.list_t or 0):
            s = cached[1]
        with w.lock:
            live = w.live(now)
            list_t = w.list_t
        names = sorted(self.workers)
        row = self.row(w, s, list_t, live.get(job_id), now, names)
        rec = self.index.record(job_id)
        spec = rec.get("spec") if isinstance(rec, dict) and isinstance(rec.get("spec"), dict) else None
        record = None
        if spec:
            sub = spec.get("submitted") if isinstance(spec.get("submitted"), dict) else {}
            lim = spec.get("limits") if isinstance(spec.get("limits"), dict) else {}
            idx = self.index.get(job_id) or {}
            record = {"project_root": proto.tilde(text(idx.get("project_root"), 400) or "") or None,
                      "head": text(spec.get("head"), 64), "snapshot": text(spec.get("snapshot"), 64),
                      "dirty": bool(spec.get("dirty")), "mu_version": text(spec.get("mu_version"), 64),
                      "argv": [text(x, 400) for x in spec.get("argv", [])[:200]] if isinstance(spec.get("argv"), list)
                      else [],
                      "cwd": text(spec.get("cwd"), 400), "outputs": [text(x, 200) for x in spec.get("outputs", [])[:50]]
                      if isinstance(spec.get("outputs"), list) else [],
                      "limits": {"mem_bytes": num(lim.get("mem_bytes"), 0), "time_s": num(lim.get("time_s"), 0),
                                 "cores": num(lim.get("cores"), 0)},
                      "submitted": {"at": iso_epoch(sub.get("at")), "host": text(sub.get("host"), 64),
                                    "user": text(sub.get("user"), 64), "mu": text(sub.get("mu"), 64)},
                      "accepted": iso_epoch(rec.get("accepted"))}
        run = self.index.run_record(job_id)
        runo = None
        if isinstance(run, dict):
            runo = {"fetched_at": iso_epoch(run.get("fetched_at")), "wall_s": num(run.get("wall_s"), 0),
                    "outputs_missing": [text(x, 200) for x in run.get("outputs_missing", [])[:50]]
                    if isinstance(run.get("outputs_missing"), list) else []}
        idx = self.index.get(job_id)
        index = None if not idx else {"key": text(idx.get("key"), 64),
                                      "submit_started": iso_epoch(idx.get("submit_started")),
                                      "accepted": iso_epoch(idx.get("accepted")), "fetched": iso_epoch(idx.get("fetched"))}
        feats = w.features or []
        return {"now": rnd(now, 3), "job": row, "record": record, "run": runo, "index": index,
                "node": {"name": w.name, "state": w.state, "old_node": w.hello is not None and not feats,
                         "metrics": "metrics" in feats},
                "commands": self.commands(job_id, w.name)}

    def logbuf(self, w, job_id, size_hint):
        with self.lock:
            lb = self.logs.get(job_id)
            if lb is None:
                lb = LogBuf(w, job_id, size_hint)
                self.logs[job_id] = lb
                while len(self.logs) > LOG_SESSIONS:
                    _, old = self.logs.popitem(last=False)
                    old.close()
            else:
                self.logs.move_to_end(job_id)
        return lb

    def api_log(self, job_id, query):
        try:
            offset = int(query.get("offset", "0"))
            if offset < 0:
                raise ValueError
        except ValueError:
            raise ApiError(400, "bad-offset", "offset is a whole number of bytes")
        w, s = self.find(job_id)
        if w is None:
            raise ApiError(404, "no-such-job", "no node listed this job")
        if s.get("state") == "queued":
            return {"job_id": job_id, "offset": 0, "next": 0, "start": 0, "size": 0, "truncated_before": False,
                    "text": "", "following": False, "eof": False, "error": None, "t": rnd(self.clock.wall(), 3)}
        lb = self.logbuf(w, job_id, s.get("log_size"))
        lb.ensure()
        p = lb.piece(offset)
        alive = lb.thread is not None and lb.thread.is_alive()
        p.update({"job_id": job_id, "following": alive, "eof": lb.done and p["next"] >= p["size"],
                  "error": lb.error, "t": rnd(self.clock.wall(), 3)})
        return p

    def api_metrics(self, job_id):
        w, s = self.find(job_id)
        if w is None:
            raise ApiError(404, "no-such-job", "no node listed this job")
        feats = w.features or []
        lim = (self.row(w, s, None, None, self.clock.wall(), [w.name]).get("limits") or {})
        base = {"job_id": job_id, "limit_mem": lim.get("mem_bytes"), "limit_cores": lim.get("cores"),
                "t": rnd(self.clock.wall(), 3)}
        if "metrics" not in feats:
            base.update(available=False, reason="old-node" if w.hello is not None else "unknown", samples=[],
                        complete=False)
            return base
        if s.get("state") == "queued":
            base.update(available=True, reason=None, samples=[], complete=False)
            return base
        with self.lock:
            m = self.metrics.get(job_id)
            if m is None:
                m = self.metrics[job_id] = {"offset": 0, "samples": [], "at": -1e9}
        now_m = self.clock.mono()
        running = s.get("state") == "running"
        if now_m - m["at"] > 30.0 or (not running and m["at"] < 0):
            m["at"] = now_m                                   # failures wait 30 s too
            got = bytearray()

            def on_data(b):
                got.extend(b)
                if len(got) > METRICS_CAP:
                    raise MuError(proto.EXIT_ERROR, "too-large", "metrics too large", "")
            try:
                w.r.stream("metrics", on_data, id=job_id, offset=str(m["offset"]))
            except MuError as e:
                base.update(available=True, reason="error", error=dashnode.error_info(e), samples=m["samples"],
                            complete=False)
                return base
            data = bytes(got)
            cut = data.rfind(b"\n") + 1                      # a final line without \n is not yet written
            m["offset"] += cut
            for line in data[:cut].splitlines():
                try:
                    d = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(d, dict) or num(d.get("t")) is None:
                    continue
                m["samples"].append({"t": round(d["t"] - w.offset_s, 1), "cpu": num(d.get("cpu"), 0, 4096),
                                     "anon": num(d.get("anon"), 0), "file": num(d.get("file"), 0),
                                     "shmem": num(d.get("shmem"), 0), "cur": num(d.get("cur"), 0),
                                     "peak": num(d.get("peak"), 0), "max": num(d.get("max"), 0),
                                     "pids": num(d.get("pids"), 0), "psi_mem": num(d.get("psi_mem"), 0, 100)})
            del m["samples"][:-20000]
        base.update(available=True, reason=None, samples=list(m["samples"]), complete=not running)
        return base

    def api_node(self, name):
        w = self.workers.get(name)
        if w is None:
            raise ApiError(404, "no-such-node", "no paired node by that name")
        c = w.card(series_s=SERIES_S)
        c["now"] = rnd(self.clock.wall(), 3)
        return c

    def api_mac(self):
        c = self.mac.card(series_s=SERIES_S)
        c["now"] = rnd(self.clock.wall(), 3)
        return c

    def retry(self, name):
        w = self.workers.get(name)
        if w is None:
            raise ApiError(404, "no-such-node", "no paired node by that name")
        w.retry_asked = True
        w.poke()
        return {"node": name, "retry": True}

    # ---------------------------------------------------------------- housekeeping
    def housekeeping(self):
        now = time.monotonic()
        with self.lock:
            for jid, lb in list(self.logs.items()):
                if now - lb.last_req > VISIBLE_S:
                    lb.close()
                    if lb.done or now - lb.last_req > 600:
                        del self.logs[jid]
            for jid, m in list(self.metrics.items()):
                if self.clock.mono() - m["at"] > 600 and m["at"] > 0:
                    del self.metrics[jid]
            for k, v in list(self.status_cache.items()):
                if self.clock.wall() - v[0] > 600:
                    del self.status_cache[k]
            for k, t in list(self.detail_seen.items()):
                if self.clock.wall() - t > 600:
                    del self.detail_seen[k]
        if self.open_file:
            try:
                if time.time() - os.path.getmtime(self.open_file) > 60:
                    os.unlink(self.open_file)
            except OSError:
                pass

    def first_use(self):
        if self.open_file:
            try:
                os.unlink(self.open_file)
            except OSError:
                pass


class _RealClock:
    def wall(self):
        return time.time()

    def mono(self):
        return time.monotonic()

    def sleep(self, s):
        time.sleep(s)


def hashlib_token():
    import hashlib
    return hashlib.sha256(("mu-dashboard-fixed:" + jobs.mu_home()).encode()).hexdigest()


class ApiError(Exception):
    def __init__(self, status, error, message):
        Exception.__init__(self, message)
        self.status, self.error, self.message = status, error, message


# ------------------------------------------------------------------------------------------------ HTTP
def load_static(root):
    """The fixed table of files served: {url path: (bytes, content type, cache-control)}. Built once at start from
    regular files whose names match NAME (no symlink, no folder beyond assets/). No request path is ever joined to a
    folder."""
    table, total = {}, 0

    def add(url, path, name, cache):
        nonlocal total
        ext = os.path.splitext(name)[1].lower()
        if ext not in TYPES or not NAME.match(name) or name.startswith("."):
            return
        try:
            st = os.lstat(path)
        except OSError:
            return
        import stat as _s
        if not _s.S_ISREG(st.st_mode) or st.st_size > 4 << 20 or total + st.st_size > 32 << 20:
            return
        with open(path, "rb") as f:
            data = f.read()
        total += len(data)
        table[url] = (data, TYPES[ext], cache)
    for name in ROOT_FILES:
        add("/" if name == "index.html" else "/" + name, os.path.join(root, name), name,
            "no-store" if name == "index.html" else "no-cache")
    adir = os.path.join(root, "assets")
    try:
        import stat as _s
        if _s.S_ISDIR(os.lstat(adir).st_mode):
            for e in sorted(os.listdir(adir)):
                add("/assets/" + e, os.path.join(adir, e), e, "public, max-age=31536000, immutable")
    except OSError:
        pass
    if "/" not in table:
        table["/"] = (NO_BUILD_PAGE, TYPES[".html"], "no-store")
    return table


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    # SO_REUSEADDR: a restart (launchd's KeepAlive) gets its port back while old connections sit in TIME_WAIT. It
    # never lets two listeners share 127.0.0.1:<port> (that would need SO_REUSEPORT); the lock keeps one instance
    allow_reuse_address = True
    request_queue_size = 32

    def server_bind(self):   # no getfqdn(): a DNS lookup at start could take seconds
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


ROUTE_JOB = re.compile(r"^/api/jobs/([^/]+)(/log|/metrics)?$")
ROUTE_NODE = re.compile(r"^/api/nodes/([^/]+)(/retry)?$")


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "mu-dashboard"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 30

    def log_message(self, fmt, *args):
        self.server.app.log("http " + (fmt % args))

    def headers_common(self, cache="no-store"):
        self.send_header("Content-Security-Policy", csp())
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", cache)

    def send_body(self, status, body, ctype, cache="no-store", head=False):
        self.send_response(status)
        self.headers_common(cache)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def send_json(self, status, obj, head=False):
        out = {"api": API, "ok": 200 <= status < 300}
        out.update(obj)
        body = json.dumps(out, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.send_body(status, body, "application/json; charset=utf-8", "no-store", head)

    def error_json(self, status, error, message, head=False):
        self.send_json(status, {"error": error, "message": message}, head)

    def host_ok(self):
        if fault("no-host-check"):
            return True
        port = self.server.app.port
        return self.headers.get("Host", "") in ("127.0.0.1:%d" % port, "localhost:%d" % port)

    def token_ok(self):
        if fault("no-token"):
            return True
        got = self.headers.get("X-Mu-Token", "")
        return isinstance(got, str) and len(got) == 64 and hmac.compare_digest(got.encode(), self.server.app.token.encode())

    def do_GET(self):
        self.handle_any("GET")

    def do_HEAD(self):
        self.handle_any("HEAD")

    def do_POST(self):
        self.handle_any("POST")

    def _other(self):
        self.handle_any(self.command)

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = do_CONNECT = _other

    def handle_any(self, method):
        app = self.server.app
        head = method == "HEAD"
        try:
            if not self.host_ok():
                return self.error_json(403, "host", "this server answers only to 127.0.0.1:%d" % app.port, head)
            if not self.path.startswith("/"):
                return self.error_json(400, "bad-request", "bad request target", head)
            path, _, qs = self.path.partition("?")
            if path.startswith("/api/") or path == "/api":
                return self.api(method, path, qs, head)
            if method not in ("GET", "HEAD"):
                return self.error_json(405, "method", "read-only", head)
            f = app.static.get(path)
            if f is None:
                return self.error_json(404, "not-found", "no such page", head)
            self.send_body(200, f[0], f[1], f[2], head)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:   # never the details to the client: they could carry node text
            app.log("internal error on %s: %s: %s" % (method, type(e).__name__, str(e)[:300]))
            try:
                self.error_json(500, "internal", "the dashboard server failed on this request", head)
            except Exception:
                pass

    def api(self, method, path, qs, head):
        app = self.server.app
        if not self.token_ok():
            return self.error_json(401, "token", "this page has no current token: run  mu dashboard open  in a "
                                   "terminal (or ask Claude to)", head)
        if self.headers.get("Content-Length") not in (None, "0"):
            return self.error_json(400, "bad-request", "no request bodies", head)
        app.touch()
        query = {}
        for k, v in (x.split("=", 1) if "=" in x else (x, "") for x in qs.split("&") if x):
            if k in query or len(query) > 8:
                return self.error_json(400, "bad-query", "repeated or too many parameters", head)
            query[k] = v
        try:
            m = ROUTE_NODE.match(path)
            if m and m.group(2):
                if method != "POST":
                    raise ApiError(405, "method", "POST only")
                origin = self.headers.get("Origin", "")
                if origin not in ("http://127.0.0.1:%d" % app.port, "http://localhost:%d" % app.port) or \
                        self.headers.get("Sec-Fetch-Site") != "same-origin":
                    raise ApiError(403, "origin", "same-origin requests only")
                name = m.group(1)
                if not proto.NODE_NAME.match(name):
                    raise ApiError(400, "bad-node", "not a node name")
                return self.send_json(200, app.retry(name))
            if method not in ("GET", "HEAD"):
                raise ApiError(405, "method", "read-only")
            if path == "/api/session":
                if query.get("first") == "1":
                    app.first_use()
                return self.send_json(200, app.api_session(), head)
            if path == "/api/overview":
                return self.send_json(200, app.api_overview(), head)
            if path == "/api/jobs":
                return self.send_json(200, app.api_jobs(query), head)
            if path == "/api/mac":
                return self.send_json(200, app.api_mac(), head)
            m = ROUTE_JOB.match(path)
            if m:
                jid = m.group(1)
                if not proto.JOB_ID.match(jid):
                    raise ApiError(400, "bad-job-id", "not a job id")
                if m.group(2) == "/log":
                    return self.send_json(200, app.api_log(jid, query), head)
                if m.group(2) == "/metrics":
                    return self.send_json(200, app.api_metrics(jid), head)
                return self.send_json(200, app.api_job(jid), head)
            if m is None and ROUTE_NODE.match(path):
                name = ROUTE_NODE.match(path).group(1)
                if not proto.NODE_NAME.match(name):
                    raise ApiError(400, "bad-node", "not a node name")
                return self.send_json(200, app.api_node(name), head)
            raise ApiError(404, "not-found", "no such API route")
        except ApiError as e:
            return self.error_json(e.status, e.error, e.message, head)


# ------------------------------------------------------------------------------------------------ serve
def info_path(dev=False):
    return os.path.join(run_dir(dev), "dashboard.json")


def token_path(dev=False):
    return os.path.join(run_dir(dev), "dashboard.token")


def read_info(dev=False):
    """run/dashboard.json if its process is alive, else None."""
    try:
        with open(os.path.join(jobs.mu_home(), "run", "dev" if dev else "", "dashboard.json"), encoding="utf-8") as f:
            d = json.loads(f.read(4096))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get("port"), int) or not pid_alive(d.get("pid")):
        return None
    return d


def preferred_port():
    return 47000 + os.getuid() % 1000


def bind(port, explicit):
    host = "0.0.0.0" if fault("bind-all") else "127.0.0.1"
    try:
        return Server((host, port), Handler)
    except OSError:
        if explicit:
            raise
        return Server((host, 0), Handler)


def build_dev(scenario_name, fake_mac):
    from mu import dashfake
    dashnode.dev_mode(True)
    sc = dashfake.Scenario(scenario_name)
    readers = {n: dashnode.NodeReader(n, fake) for n, fake in sc.nodes.items()}
    mac = dashfake.FakeMac(sc.clock) if fake_mac else MacSampler(sc.clock)
    return dict(dev=True, scenario=sc, readers=readers, index=FakeIndex(sc.clock, sc), mac=mac)


def build_real():
    readers = {}
    for name in jobs.paired_nodes():
        try:
            readers[name] = dashnode.NodeReader.real(jobs.load_node(name))
        except MuError:
            continue
    return dict(dev=False, readers=readers)


def cmd_serve(a):
    dev = bool(a.dev)
    rd = run_dir(dev)
    log = Log(os.path.join(jobs.mu_home(), "logs", "dashboard-dev.log" if dev else "dashboard.log"))
    lockf = None
    if not fault("no-lock"):
        lockf = open(os.path.join(rd, "dashboard.lock"), "a")
        try:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            info = read_info(dev)
            msg = "the dashboard is already being served%s" % (" on port %d" % info["port"] if info else "")
            if a.json:
                proto.emit_json({"ok": True, "exit": 0, "already": True, "port": info["port"] if info else None})
            else:
                sys.stderr.write("mu: %s\n" % msg)
            return 0
    parts = build_dev(a.scenario or "busy", a.fake_mac) if dev else build_real()
    port = a.port or preferred_port()
    srv = bind(port, bool(a.port))
    app = App(port=srv.server_address[1], log=log, **parts)
    app.static = load_static(a.static or STATIC_DIR)
    srv.app = app
    default_at_start = mac_version()
    write_private(token_path(dev), app.token + "\n")
    info = {"pid": os.getpid(), "port": app.port, "started": proto.iso(app.started), "version": app.version,
            "dev": dev, "scenario": app.scenario.name if app.scenario else None}
    write_private(info_path(dev), json.dumps(info) + "\n")
    app.open_file = os.path.join(rd, "dashboard-open.html")
    log("serving on 127.0.0.1:%d (pid %d, %s%s, %d node(s))" % (app.port, os.getpid(), app.version or "?",
                                                                  ", dev " + app.scenario.name if dev else "",
                                                                  len(app.workers)))
    if a.json:
        proto.emit_json({"ok": True, "exit": 0, "port": app.port, "url": "http://127.0.0.1:%d/" % app.port,
                         "dev": dev})
    stop = threading.Event()

    def on_signal(signum, frame):
        stop.set()
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGHUP, on_signal)
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True)
    t.start()

    def worker_loop(w):
        while not stop.is_set():
            try:
                w.tick()
            except Exception as e:
                log("%s: worker error %s: %s" % (w.name, type(e).__name__, str(e)[:200]))
            w.wake.wait(0.5)
            w.wake.clear()
    for w in app.workers.values():
        threading.Thread(target=worker_loop, args=(w,), daemon=True, name="node-" + w.name).start()

    def mac_loop():
        while not stop.is_set():
            if app.visible():
                try:
                    app.mac.sample()
                except Exception as e:
                    log("mac sampler: %s" % type(e).__name__)
                stop.wait(MAC_STEP)
            else:
                stop.wait(0.5)
    threading.Thread(target=mac_loop, daemon=True, name="mac").start()
    n = 0
    try:
        while not stop.wait(1.0):
            app.housekeeping()
            n += 1
            if not dev and n % 30 == 0 and mac_version() != default_at_start and not app.visible() \
                    and not app.anything_unfinished():
                log("the default μ changed (%s -> %s): exiting so launchd starts the new one" % (
                    default_at_start, mac_version()))
                break
    finally:
        srv.shutdown()
        srv.server_close()
        for w in app.workers.values():
            w.stop_watch()
        for lb in list(app.logs.values()):
            lb.close()
        try:
            with open(info_path(dev), encoding="utf-8") as f:
                mine = json.load(f).get("pid") == os.getpid()
        except (OSError, ValueError):
            mine = False
        if mine:
            for p in (info_path(dev), token_path(dev), app.open_file):
                try:
                    os.unlink(p)
                except OSError:
                    pass
        log("stopped (pid %d)" % os.getpid())
    return 0


# ------------------------------------------------------------------------------------------------ the LaunchAgent
def agents_dir():
    return os.environ.get("MU_LAUNCH_AGENTS_DIR") or os.path.expanduser("~/Library/LaunchAgents")


def plist_path():
    return os.path.join(agents_dir(), LABEL + ".plist")


def plist_bytes():
    import plistlib
    home = jobs.mu_home()
    cache = os.environ.get("MU_CACHE") or os.path.expanduser("~/Library/Caches/Mu")
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [os.path.join(home, "bin", "mu"), "dashboard", "serve"],
        "EnvironmentVariables": {"MU_HOME": home, "MU_CACHE": cache, "PATH": PATH_ENV},
        "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10, "ProcessType": "Background",
        "StandardOutPath": os.path.join(home, "logs", "launchd-dashboard.log"),
        "StandardErrorPath": os.path.join(home, "logs", "launchd-dashboard.log"),
    })


def launchctl(*args):
    try:
        r = subprocess.run([os.environ.get("MU_LAUNCHCTL") or "launchctl"] + list(args), capture_output=True,
                           timeout=30, stdin=subprocess.DEVNULL)
        return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace").strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def agent_state():
    if sys.platform != "darwin" and not os.environ.get("MU_LAUNCH_AGENTS_DIR"):
        return "unsupported"
    if not os.path.isfile(plist_path()):
        return "not installed"
    uid = os.getuid()
    for dom in ("gui/%d" % uid, "user/%d" % uid):
        if launchctl("print", "%s/%s" % (dom, LABEL))[0] == 0:
            return "loaded"
    return "installed (not loaded)"


def conf_path():
    return os.path.join(jobs.mu_home(), "dashboard.conf")


def enabled():
    c = jobs.read_conf(conf_path()) or {}
    return c.get("enabled", "off")


def cmd_on(a):
    if sys.platform != "darwin" and not os.environ.get("MU_LAUNCH_AGENTS_DIR"):
        raise MuError(proto.EXIT_USAGE, "unsupported", "the dashboard's LaunchAgent is for macOS; on Linux, a systemd "
                      "user service comes later", "mu dashboard open (starts it for this login)")
    if os.environ.get("CI") and not os.environ.get("MU_LAUNCH_AGENTS_DIR"):
        raise MuError(proto.EXIT_USAGE, "ci", "the dashboard is always off in CI", "")
    home = jobs.mu_home()
    for ch in ('"', "\\", "\n", "\r"):
        if ch in home:
            raise MuError(proto.EXIT_USAGE, "bad-path", "MU_HOME holds a quote, backslash or line break", "")
    os.makedirs(os.path.join(home, "logs"), mode=0o700, exist_ok=True)
    os.makedirs(agents_dir(), exist_ok=True)
    p = plist_path()
    durable.write_durable(p, plist_bytes().decode("utf-8"), 0o644)
    jobs.write_conf(conf_path(), {"enabled": "on"}, "mu dashboard on|off (docs/design/dashboard.md §4.4)")
    uid = os.getuid()
    launchctl("bootout", "gui/%d/%s" % (uid, LABEL))
    launchctl("bootout", "user/%d/%s" % (uid, LABEL))
    dom = "gui/%d" % uid if launchctl("print", "gui/%d" % uid)[0] == 0 else "user/%d" % uid
    rc, err = launchctl("bootstrap", dom, p)
    loaded = rc == 0
    out = {"ok": True, "exit": 0, "enabled": "on", "agent": "loaded" if loaded else "installed (not loaded)",
           "plist": proto.tilde(p)}
    if not loaded:
        out["note"] = "launchd loads it at the next login (%s)" % proto.clean_text(err, 200)
    if a.json:
        proto.emit_json(out)
    else:
        print("dashboard: on (%s). mu dashboard open · mu dashboard off" % ("running from now on" if loaded else
                                                                           out["note"]))
    return 0


def cmd_off(a):
    uid = os.getuid()
    launchctl("bootout", "gui/%d/%s" % (uid, LABEL))
    launchctl("bootout", "user/%d/%s" % (uid, LABEL))
    try:
        os.unlink(plist_path())
    except OSError:
        pass
    jobs.write_conf(conf_path(), {"enabled": "off"}, "mu dashboard on|off (docs/design/dashboard.md §4.4)")
    if a.json:
        proto.emit_json({"ok": True, "exit": 0, "enabled": "off", "agent": "not installed"})
    else:
        print("dashboard: off (mu dashboard open still starts it for this login)")
    return 0


def status_obj():
    info = read_info()
    return {"ok": True, "exit": 0, "running": bool(info), "port": info["port"] if info else None,
            "url": ("http://127.0.0.1:%d/" % info["port"]) if info else None,
            "pid": info.get("pid") if info else None, "started": info.get("started") if info else None,
            "version": info.get("version") if info else None, "enabled": enabled(), "agent": agent_state(),
            "nodes": jobs.paired_nodes()}


def cmd_status(a):
    out = status_obj()
    if a.json:
        proto.emit_json(out)
        return 0
    if out["running"]:
        print("dashboard: running on %s (pid %s, μ %s) · mu dashboard open" % (out["url"], out["pid"], out["version"]))
    else:
        print("dashboard: not running · mu dashboard open (or mu dashboard on to keep it running)")
    print("  agent: %s · enabled: %s · nodes: %s" % (out["agent"], out["enabled"], ", ".join(out["nodes"]) or "none"))
    return 0


def port_answers(port, timeout=1.0):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def cmd_open(a):
    info = read_info()
    if not info or not port_answers(info["port"]):
        if agent_state() == "loaded":
            launchctl("kickstart", "gui/%d/%s" % (os.getuid(), LABEL))
        else:
            cli = os.path.join(HERE, "cli.py")
            env = {k: v for k, v in os.environ.items() if k in ("MU_HOME", "MU_CACHE", "MU_INFRA_VERSION", "HOME",
                                                                "PATH", "LANG", "MU_JOBS_FAULT", "MU_SSH",
                                                                "MU_SECURITY", "MU_CHISEL", "USER", "LOGNAME")}
            subprocess.Popen([sys.executable, "-I", "-S", cli, "dashboard", "serve"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                             close_fds=True, env=env)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            info = read_info()
            if info and port_answers(info["port"]):
                break
            time.sleep(0.2)
        else:
            raise MuError(proto.EXIT_ERROR, "not-started", "the dashboard server did not start",
                          "see %s" % proto.tilde(os.path.join(jobs.mu_home(), "logs", "dashboard.log")))
    try:
        with open(token_path(), encoding="utf-8") as f:
            token = f.read().strip()
    except OSError:
        raise MuError(proto.EXIT_ERROR, "no-token", "the dashboard's token file is missing", "mu dashboard open again")
    url = "http://127.0.0.1:%d/" % info["port"]
    page = os.path.join(run_dir(), "dashboard-open.html")
    full = "%s#t=%s" % (url, token)
    write_private(page, '<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer">'
                  '<meta http-equiv="refresh" content="0;url=%s"><title>μ dashboard</title>'
                  '<p><a href="%s">Open the μ dashboard</a></p>\n' % (full, full))
    opener = os.environ.get("MU_OPENER") or ("open" if sys.platform == "darwin" else "xdg-open")
    argv = [opener, full if fault("open-url-argv") else page]
    try:
        r = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=30)
        opened = r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        opened = False
    if a.json:
        proto.emit_json({"ok": True, "exit": 0, "url": url, "opened": opened})
    else:
        print("dashboard: %s%s" % (url, " (opened in the browser)" if opened else
                                   " (could not open a browser: open %s)" % proto.tilde(page)))
    return 0


# ------------------------------------------------------------------------------------------------ fixtures (D38)
def api_fixtures():
    """The golden /api responses: the busy scenario at the fixtures' fixed time, plus the old-node, offline and
    refused node cards and the 401 answer. The same code builds them as serves them."""
    from mu import dashfake

    def make(name):
        clock = dashfake.ManualClock(dashfake.FIXED_T0)
        sc = dashfake.Scenario(name, clock=clock)
        readers = {n: dashnode.NodeReader(n, f) for n, f in sc.nodes.items()}
        app = App(dev=True, clock=clock, readers=readers, index=FakeIndex(clock, sc), mac=dashfake.FakeMac(clock),
                  scenario=sc, port=47001, version="v2026.11.9")
        app.touch()
        for w in app.workers.values():
            w.tick()
        clock.advance(2.0)
        app.mac.sample()
        return app, sc

    def wrap(obj):
        out = {"api": API, "ok": True}
        out.update(obj)
        return json.loads(json.dumps(out, ensure_ascii=False))

    old = os.environ.get("MU_INFRA_VERSION")
    os.environ["MU_INFRA_VERSION"] = "v2026.11.9"
    try:
        app, sc = make("busy")
        node = app.workers["office-pc"]
        run = max((j for j, s in node.jobs.items() if s.get("state") == "running"),
                  key=lambda j: node.jobs[j].get("started_at") or "")
        fin = sorted(j for j, s in node.jobs.items() if s.get("state") == "failed")[0]
        session = app.api_session()
        session["host"] = "fixture-mac"
        out = {"api-session.json": wrap(session), "api-overview.json": wrap(app.api_overview()),
               "api-jobs.json": wrap(app.api_jobs({})), "api-job.json": wrap(app.api_job(run)),
               "api-job-metrics.json": wrap(app.api_metrics(run)), "api-node.json": wrap(app.api_node("office-pc")),
               "api-mac.json": wrap(app.api_mac())}
        lb = app.logbuf(node, fin, 0)
        lb._run(threading.Event())
        p = lb.piece(0)
        p.update({"job_id": fin, "following": False, "eof": True, "error": None, "t": rnd(app.clock.wall(), 3)})
        out["api-job-log.json"] = wrap(p)
        for scen, key in (("old-node", "api-overview-old-node.json"), ("offline", "api-overview-offline.json"),
                          ("refused", "api-overview-refused.json")):
            a2, _ = make(scen)
            out[key] = wrap(a2.api_overview())
        out["api-error-401.json"] = {"api": API, "ok": False, "error": "token", "message": "this page has no current "
                                     "token: run  mu dashboard open  in a terminal (or ask Claude to)"}
        return out
    finally:
        if old is None:
            os.environ.pop("MU_INFRA_VERSION", None)
        else:
            os.environ["MU_INFRA_VERSION"] = old


# ------------------------------------------------------------------------------------------------ the command
HELP = """mu dashboard: a local page of jobs, nodes and this Mac's load (docs/design/dashboard.md)

  mu dashboard [status] [--json]  running? on which port (the URL never carries the token)
  mu dashboard open [--json]      start it if needed and open the browser (the token goes through a 0600 file)
  mu dashboard on | off           keep it running with a LaunchAgent (macOS), or remove the agent
  mu dashboard serve [--dev [--scenario NAME] [--fake-mac]] [--port N] [--static DIR]
                                  the server itself (launchd runs it; --dev serves fake nodes only)
The page is read-only. Claude keeps using  mu … --json ; it never reads the dashboard's API.
"""


def main(args):
    import argparse
    p = jobs.parser("mu dashboard", HELP)
    p.add_argument("sub", nargs="?", default="status", choices=("status", "open", "on", "off", "serve"))
    p.add_argument("--dev", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--scenario", help=argparse.SUPPRESS)
    p.add_argument("--fake-mac", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--port", type=int, help=argparse.SUPPRESS)
    p.add_argument("--static", help=argparse.SUPPRESS)
    a = p.parse_args(args)
    if a.sub != "serve" and (a.dev or a.scenario or a.port or a.static or a.fake_mac):
        raise MuError(proto.EXIT_USAGE, "usage", "--dev, --scenario, --port and --static belong to `serve`",
                      "mu dashboard serve --dev")
    if a.scenario and not a.dev:
        raise MuError(proto.EXIT_USAGE, "usage", "--scenario needs --dev", "mu dashboard serve --dev --scenario busy")
    if a.scenario:
        from mu import dashfake
        if a.scenario not in dashfake.SCENARIOS:
            raise MuError(proto.EXIT_USAGE, "usage", "unknown scenario '%s'" % proto.clean_text(a.scenario, 40),
                          "one of: " + ", ".join(dashfake.SCENARIOS))
    if a.port is not None and not 1024 <= a.port <= 65535:
        raise MuError(proto.EXIT_USAGE, "usage", "--port must be 1024-65535", "")
    return {"status": cmd_status, "open": cmd_open, "on": cmd_on, "off": cmd_off, "serve": cmd_serve}[a.sub](a)
