"""NodeReader: the only way the dashboard server reaches a compute node (docs/design/dashboard.md §4.3).

- It refuses, before any ssh call, every verb outside a fixed read-only allowlist (hello list status log stats metrics
  watch). It never sends submit, cancel, fetch or receive-pack (D17).
- A real reader talks through DashConn: jobs.Conn with a ControlPath of its own (<node>.dash.sock, placed by the same
  rule as jobs.control_path), so a dashboard call that times out ends only the dashboard's master, never the one the
  CLI (and Claude's `mu submit`, `mu fetch`, `mu log -f`) is using (D20).
- In development mode (`mu dashboard serve --dev`) no real reader can be built: fake nodes only (§4.8).

Standard library only. Test faults (MU_JOBS_FAULT): allow-cancel (the allowlist lets `cancel` through),
shared-controlpath (the dashboard uses the CLI's ControlPath).
"""
import os
import subprocess
import threading

from mu import jobs, proto
from mu.durable import fault
from mu.proto import MuError

ALLOWED = ("hello", "list", "status", "log", "stats", "metrics", "watch")
CALL_TIMEOUT = 30          # a dashboard call never waits like a CLI call (300 s)
_DEV = [False]             # set by `serve --dev`: no real NodeReader may be built


class NotAllowed(Exception):
    """A verb outside the allowlist: raised before anything is sent."""


def dev_mode(on=True):
    _DEV[0] = bool(on)


def dash_control_path(name, home=None):
    """<dir>/<node>.dash.sock, by the rule of jobs.control_path (the socket path must fit macOS's 104 bytes)."""
    if fault("shared-controlpath"):
        return jobs.control_path(name, home)
    return jobs.control_path(name + ".dash", home)


class DashConn(jobs.Conn):
    """jobs.Conn with the dashboard's own ControlPath, and a stream that can be stopped from another thread."""

    def __init__(self, conf):
        jobs.Conn.__init__(self, conf)
        self.cp = dash_control_path(self.name)
        self.cp_opt = "ControlPath=none" if self.cp == "none" else 'ControlPath="%s"' % self.cp.replace("%", "%%")

    def stream(self, request, on_data, data=None, stop=None):
        """As jobs.Conn.stream, plus STOP (a threading.Event): when it is set, the ssh process is killed and the call
        returns (rc -15 or the like). ON_DATA may also raise to stop."""
        status = self.new_status()
        muxed = self.muxed()
        p = subprocess.Popen(self.argv(request), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, env=self.env(status))
        errbuf = []
        t = threading.Thread(target=lambda: errbuf.append(p.stderr.read()), daemon=True)
        t.start()
        if stop is not None:
            def killer():
                while p.poll() is None:
                    if stop.wait(0.5):
                        try:
                            p.kill()
                        except OSError:
                            pass
                        return
            threading.Thread(target=killer, daemon=True).start()
        try:
            while True:
                b = p.stdout.read1(65536)
                if not b:
                    break
                on_data(b)
        except BaseException:
            p.kill()
            p.wait()
            jobs._unlink(status)
            raise
        rc = p.wait()
        t.join(5)
        err = (errbuf[0] if errbuf else b"").decode("utf-8", "replace")
        st = jobs.settle(status, rc, err, muxed) if not (stop is not None and stop.is_set()) else None
        jobs._unlink(status)
        return rc, err, st, muxed


class NodeReader:
    """One paired node, read-only. TRANSPORT is a DashConn (real) or a dashfake.FakeNode (tests, --dev); both have
    call(verb, timeout=, **args), stream(request, on_data, stop=), fail(rc, err, st, muxed) and close_master()."""

    def __init__(self, name, transport):
        if not proto.NODE_NAME.match(name or ""):
            raise ValueError("not a node name")
        self.name, self.t = name, transport

    @classmethod
    def real(cls, conf):
        if _DEV[0]:
            raise RuntimeError("development mode serves fake nodes only: no real NodeReader")
        return cls(conf["name"], DashConn(conf))

    @staticmethod
    def allowed(verb):
        return verb in ALLOWED or (verb == "cancel" and fault("allow-cancel"))

    def _check(self, verb):
        if not self.allowed(verb):
            raise NotAllowed("verb '%s' is not on the dashboard's read-only allowlist" % str(verb)[:32])

    def call(self, verb, timeout=CALL_TIMEOUT, **args):
        """A JSON verb -> the node's answer (dict, cleaned). Failures raise MuError with the CLI's exit codes."""
        self._check(verb)
        return self.t.call(verb, timeout=timeout, **args)

    def stream(self, verb, on_data, stop=None, **args):
        """A streaming verb (log, metrics, watch): ON_DATA(bytes) per piece. Returns normally when the node ended the
        stream (or STOP was set); raises MuError when the call failed."""
        self._check(verb)
        request = " ".join([verb] + ["%s=%s" % (k, v) for k, v in args.items() if v is not None])
        rc, err, st, muxed = self.t.stream(request, on_data, stop=stop)
        if rc != 0 and not (stop is not None and stop.is_set()):
            self.t.fail(rc, err, st, muxed)
        return rc

    def close_master(self):
        self.t.close_master()

    @property
    def control_path(self):
        return getattr(self.t, "cp", None)


def error_info(e):
    """A MuError -> the fields the API shows: exit, error name, message (cleaned), next step."""
    if isinstance(e, MuError):
        return {"exit": int(e.code), "error": proto.clean_text(e.error, 60),
                "message": proto.clean_text(e.message, 600), "next": proto.clean_text(e.hint or "", 600)}
    return {"exit": proto.EXIT_ERROR, "error": "internal", "message": "the dashboard failed to read the node",
            "next": "mu compute status"}


def node_conf_ok(name):
    return os.path.isfile(os.path.join(jobs.node_dir(name), "node.conf"))
