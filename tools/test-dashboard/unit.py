"""Checks of the dashboard's server, API and node reader (docs/design/dashboard.md §7.1: D05-D08 API parts, D12-D15,
D17-D20, D22-D24, D27, D28 Python part, D38, D39). tools/test-dashboard/run.sh runs each section with each Python
under test, then again with the section's fault put in (MU_JOBS_FAULT), and then at least one check must fail.

Usage: python -I -S unit.py SECTION. Prints "ok    name" or "FAIL  name" per check; exits with the number of failures.
Nothing here touches a real node, the network beyond 127.0.0.1, the real $MU_HOME or a real LaunchAgent: run.sh points
MU_HOME and HOME at a temp folder, and launchctl and the browser opener are stubs."""
import hashlib
import http.client
import json
import os
import plistlib
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "src"))
from mu import dashboard, dashfake, dashnode, durable, jobs, proto  # noqa: E402

CLI = os.path.join(REPO, "src", "mu", "cli.py")
FAILS = [0]


def check(name, cond, detail=""):
    if cond:
        print("ok    %s" % name)
    else:
        FAILS[0] += 1
        print("FAIL  %s" % name)
        if detail:
            print("\n".join("        | " + x for x in str(detail).splitlines()[:30]))


def skip(name, why):
    print("skip  %s (%s)" % (name, why))


def tmpdir(tag):
    return tempfile.mkdtemp(prefix="mu dash %s." % tag)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def static_fixture():
    """A share/dashboard-like folder with traps: a symlink, a bad name, a subfolder, a dotfile, a source map."""
    d = tmpdir("static")
    open(os.path.join(d, "index.html"), "w").write("<!doctype html><title>μ</title><p>INDEX</p>")
    open(os.path.join(d, "theme.js"), "w").write("document.documentElement.dataset.theme='light';")
    open(os.path.join(d, "favicon.svg"), "w").write("<svg xmlns='http://www.w3.org/2000/svg'/>")
    open(os.path.join(d, "BUILD.json"), "w").write("{}")
    a = os.path.join(d, "assets")
    os.makedirs(os.path.join(a, "sub"))
    open(os.path.join(a, "app-abc123.js"), "w").write("console.log(1)")
    open(os.path.join(a, "app-abc123.css"), "w").write("body{}")
    open(os.path.join(a, "a b.js"), "w").write("x")
    open(os.path.join(a, ".hidden.js"), "w").write("x")
    open(os.path.join(a, "app.js.map"), "w").write("{}")
    open(os.path.join(a, "sub", "x.js"), "w").write("x")
    secret = os.path.join(tmpdir("secret"), "secret.js")
    open(secret, "w").write("SECRET")
    os.symlink(secret, os.path.join(a, "evil.js"))
    return d


def req(port, path, method="GET", host=None, headers=None, body=None, timeout=10, addr="127.0.0.1"):
    c = http.client.HTTPConnection(addr, port, timeout=timeout)
    h = {"Host": host or "127.0.0.1:%d" % port}
    h.update(headers or {})
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    hdrs = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    return r.status, hdrs, data


def jget(port, path, token):
    st, h, d = req(port, path, headers={"X-Mu-Token": token})
    return st, (json.loads(d) if d else None)


class InProc:
    """An App with fake nodes, served on a free port in this process."""

    def __init__(self, scenario, clock=None, static=None, nodes=None):
        self.clock = clock or dashfake.ManualClock(dashfake.FIXED_T0)
        self.sc = dashfake.Scenario(scenario, clock=self.clock) if isinstance(scenario, str) else scenario
        if nodes is not None:
            self.sc.nodes = nodes
        readers = {n: dashnode.NodeReader(n, f) for n, f in self.sc.nodes.items()}
        self.app = dashboard.App(dev=True, clock=self.clock, readers=readers,
                                 index=dashboard.FakeIndex(self.clock, self.sc), mac=dashfake.FakeMac(self.clock),
                                 scenario=self.sc, version="v2026.11.9")
        self.srv = None
        if static is not None:
            self.srv = dashboard.bind(0, True)
            self.app.port = self.srv.server_address[1]
            self.srv.app = self.app
            self.app.static = dashboard.load_static(static)
            threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()

    def ticks(self, seconds, step=1.0, visible=True, each=None, real_sleep=0.0):
        n = int(seconds / step)
        for i in range(n):
            if visible:
                self.app.last_api = self.clock.mono()
            for w in self.app.workers.values():
                w.tick()
            if each:
                each(i)
            if real_sleep:
                time.sleep(real_sleep)
            self.clock.advance(step)

    def stop(self):
        for w in self.app.workers.values():
            w.stop_watch()
        if self.srv:
            self.srv.shutdown()
            self.srv.server_close()


def calls(node, verb=None):
    with node.lock:
        return [(t, v, a) for (t, v, a) in node.calls if verb is None or v == verb]


# ------------------------------------------------------------------------------------------------ D17 allowlist
def t_allowlist():
    class Recorder:
        cp = "x"
        n = 0

        def call(self, verb, timeout=30, **a):
            Recorder.n += 1
            return {"ok": True}

        def stream(self, request, on_data, stop=None):
            Recorder.n += 1
            return 0, "", None, False

        def fail(self, *a):
            pass

        def close_master(self):
            pass
    r = dashnode.NodeReader("office-pc", Recorder())
    for verb in ("submit", "cancel", "fetch", "receive-pack", "upload-pack", "node", "../x", "hello;rm"):
        raised = False
        try:
            r.call(verb)
        except dashnode.NotAllowed:
            raised = True
        check("D17: '%s' is refused before anything is sent (call)" % verb, raised and Recorder.n == 0)
        raised = False
        try:
            r.stream(verb, lambda b: None)
        except dashnode.NotAllowed:
            raised = True
        check("D17: '%s' is refused before anything is sent (stream)" % verb, raised and Recorder.n == 0)
    for verb in dashnode.ALLOWED:
        r.call(verb)
    check("D17: the allowed verbs pass", Recorder.n == len(dashnode.ALLOWED))
    src = open(os.path.join(REPO, "src", "mu", "dashboard.py"), encoding="utf-8").read()
    import re
    check("D17: dashboard.py constructs no jobs.Conn or DashConn (only NodeReader.real does)",
          not re.search(r"\b(jobs\.)?Conn\(|DashConn\(", src))
    # end to end: every route, the fake nodes see allowed verbs only
    st = static_fixture()
    p = InProc("busy", static=st)
    tok = p.app.token
    p.ticks(3, real_sleep=0.05)
    job = p.app.rows()[0]["job_id"]
    for path in ("/api/session", "/api/overview", "/api/jobs", "/api/jobs?from=mine", "/api/jobs/%s" % job,
                 "/api/jobs/%s/log?offset=0" % job, "/api/jobs/%s/metrics" % job, "/api/nodes/office-pc",
                 "/api/nodes/lab-pc", "/api/mac", "/api/jobs/20990101-000000-zzzzzz"):
        jget(p.app.port, path, tok)
    req(p.app.port, "/api/nodes/office-pc/retry", "POST", headers={"X-Mu-Token": tok, "Origin": "http://127.0.0.1:%d"
                                                                    % p.app.port, "Sec-Fetch-Site": "same-origin"})
    p.ticks(3, real_sleep=0.05)
    time.sleep(0.3)
    seen = sorted({v for n in p.sc.nodes.values() for (_, v, _) in calls(n)})
    check("D17: across every route the fake nodes were asked only allowed verbs", set(seen) <= set(dashnode.ALLOWED),
          seen)
    p.stop()


# ------------------------------------------------------------------------------------------------ D20 own master
def t_controlpath():
    h = tmpdir("cp")
    os.environ["MU_HOME"] = h
    for n in ("office-pc", "a" * 32):
        mine, cli = dashnode.dash_control_path(n), jobs.control_path(n)
        check("D20: the dashboard's ControlPath for %s... is its own" % n[:12], mine != cli and mine.endswith(".dash.sock"),
              "%s vs %s" % (mine, cli))
        check("D20: it fits the socket length limit", mine == "none" or len(mine.encode()) + 17 <= jobs.sock_limit(), mine)
    d = os.path.join(h, "compute", "office-pc")
    os.makedirs(d)
    conf = {"name": "office-pc", "_dir": d}
    dc, cc = dashnode.DashConn(conf), jobs.Conn(conf)
    check("D20: DashConn and the CLI's Conn use different masters", dc.cp != cc.cp and dc.cp_opt != cc.cp_opt)
    # a timed-out quick call ends only the dashboard's own master
    closed = []
    real_run = subprocess.run

    def fake_run(argv, **kw):
        if "-O" in argv and "exit" in argv:
            closed.append(argv[argv.index("-o") + 1])
        return subprocess.CompletedProcess(argv, 0, b"", b"")
    subprocess.run = fake_run
    try:
        dc.close_master()
    finally:
        subprocess.run = real_run
    check("D20: closing the dashboard's master names only its own ControlPath",
          closed and all(".dash.sock" in c for c in closed) and not any(cc.cp in c for c in closed), closed)
    skip("D20 end to end (a CLI `mu log -f` survives a dashboard timeout over real ssh)",
         "needs an ssh server and a stalling node: phase 1a tests the ControlPath separation only")


# ------------------------------------------------------------------------------------------------ HTTP (D12-D14, CSP)
def serve_proc(home, port, extra=(), env_extra=None, static=None):
    env = dict(os.environ, MU_HOME=home)
    env.update(env_extra or {})
    argv = [sys.executable, "-I", "-S", CLI, "dashboard", "serve", "--dev", "--fake-mac", "--port", str(port)]
    if static:
        argv += ["--static", static]
    p = subprocess.Popen(argv + list(extra), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)
    info = os.path.join(home, "run", "dev", "dashboard.json")
    for _ in range(100):
        if p.poll() is not None:
            break
        try:
            if json.load(open(info)).get("pid") == p.pid:
                break
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    return p


def stop_proc(p):
    if p.poll() is None:
        p.send_signal(signal.SIGTERM)
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()


def lan_addr():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return None if ip.startswith("127.") else ip
    except OSError:
        return None


def t_http():
    home = tmpdir("http")
    st = static_fixture()
    port = free_port()
    p = serve_proc(home, port, static=st)
    try:
        tok = open(os.path.join(home, "run", "dev", "dashboard.token")).read().strip()
        check("the server answers on 127.0.0.1", req(port, "/")[0] == 200)
        lan = lan_addr()
        if lan:
            try:
                socket.create_connection((lan, port), timeout=3).close()
                reached = True
            except OSError:
                reached = False
            check("D12: refused on the LAN address %s" % lan, not reached)
        else:
            skip("D12: LAN address", "this machine has no non-loopback IPv4 address")
        try:
            s6 = socket.socket(socket.AF_INET6)
            s6.settimeout(3)
            s6.connect(("::1", port))
            s6.close()
            v6 = True
        except OSError:
            v6 = False
        check("D12: refused on ::1", not v6)
        routes = ["/", "/theme.js", "/favicon.svg", "/assets/app-abc123.js", "/api/session", "/api/overview",
                  "/api/jobs", "/api/mac", "/api/nodes/office-pc", "/nope"]
        bad = [r for r in routes if req(port, r, host="evil.example:%d" % port, headers={"X-Mu-Token": tok})[0] != 403]
        check("D13: Host evil.example -> 403 on every route", not bad, bad)
        bad = [r for r in routes if req(port, r, host="127.0.0.1:1", headers={"X-Mu-Token": tok})[0] != 403]
        check("D13: Host with another port -> 403", not bad, bad)
        check("D13: Host localhost:<port> is served", req(port, "/", host="localhost:%d" % port)[0] == 200)
        api = ["/api/session", "/api/overview", "/api/jobs", "/api/mac", "/api/nodes/office-pc"]
        check("D14: /api without X-Mu-Token -> 401", all(req(port, r)[0] == 401 for r in api))
        check("D14: token in a cookie only -> 401",
              all(req(port, r, headers={"Cookie": "X-Mu-Token=%s; t=%s" % (tok, tok)})[0] == 401 for r in api))
        check("D14: a wrong token -> 401", all(req(port, r, headers={"X-Mu-Token": "0" * 64})[0] == 401 for r in api))
        check("D14: the token -> 200", all(req(port, r, headers={"X-Mu-Token": tok})[0] == 200 for r in api))
        s, h, d = req(port, "/api/overview")
        body = json.loads(d)
        check("D14: the 401 answer holds no job or node data", body.get("error") == "token" and "jobs" not in body
              and "nodes" not in body and b"office-pc" not in d, d[:300])
        s, h, d = req(port, "/api/overview", "OPTIONS", headers={"Origin": "http://evil.example",
                                                                 "Access-Control-Request-Method": "GET",
                                                                 "Access-Control-Request-Headers": "x-mu-token"})
        check("D14: a CORS preflight is never approved", s >= 400 and not any(k.startswith("access-control-")
                                                                              for k in h), (s, h))
        # CSP and headers on every response
        resp = [req(port, r, headers={"X-Mu-Token": tok}) for r in routes] + [req(port, "/api/overview"),
                                                                              req(port, "/", host="evil.example")]
        csps = [h.get("content-security-policy", "") for (_, h, _) in resp]
        check("CSP on every response (200, 401, 403, 404)", all(c == dashboard.CSP for c in csps),
              "\n".join(sorted(set(csps))))
        c = csps[0]
        check("CSP: no unsafe-inline, unsafe-eval, nonce or hash; Trusted Types required",
              all(x not in c for x in ("unsafe-inline", "unsafe-eval", "nonce-", "sha256-", "sha384-")) and
              "require-trusted-types-for 'script'" in c and "trusted-types 'none'" in c and
              "connect-src 'self'" in c and "frame-ancestors 'none'" in c, c)
        hs = [h for (_, h, _) in resp]
        check("nosniff, no-referrer, COOP same-origin on every response",
              all(h.get("x-content-type-options") == "nosniff" and h.get("referrer-policy") == "no-referrer" and
                  h.get("cross-origin-opener-policy") == "same-origin" for h in hs))
        check("no CORS header on any response", not any(k.startswith("access-control-") for h in hs for k in h))
        s, h, d = req(port, "/")
        check("/ is index.html with Cache-Control: no-store", s == 200 and b"INDEX" in d and
              h.get("cache-control") == "no-store" and h.get("content-type", "").startswith("text/html"))
        s, h, d = req(port, "/assets/app-abc123.js")
        check("/assets/<hashed> is immutable, text/javascript", s == 200 and "immutable" in h.get("cache-control", "")
              and h.get("content-type", "").startswith("text/javascript"))
        check("/theme.js no-cache", req(port, "/theme.js")[1].get("cache-control") == "no-cache")
        for bad_path in ("/assets/evil.js", "/assets/a%20b.js", "/assets/a b.js", "/assets/.hidden.js",
                         "/assets/sub/x.js", "/assets/../index.html", "/../../etc/passwd", "/BUILD.json",
                         "/assets/app.js.map", "/index.html/..", "//etc/passwd", "/assets/%2e%2e/index.html"):
            try:
                s, h, d = req(port, bad_path)
            except (http.client.HTTPException, OSError):
                s, d = 400, b""
            check("static: %s is not served" % bad_path, s in (400, 404) and b"SECRET" not in d, (s, d[:100]))
        s, h, d = req(port, "/api/session", "POST", headers={"X-Mu-Token": tok})
        check("API: POST to a read route -> 405", s == 405)
        s, h, d = req(port, "/api/nodes/office-pc/retry", "POST", headers={"X-Mu-Token": tok,
                                                                          "Origin": "http://evil.example",
                                                                          "Sec-Fetch-Site": "cross-site"})
        check("API: retry from another origin -> 403", s == 403)
        s, h, d = req(port, "/api/nodes/office-pc/retry", "POST", headers={
            "X-Mu-Token": tok, "Origin": "http://127.0.0.1:%d" % port, "Sec-Fetch-Site": "same-origin"})
        check("API: retry from the page's origin -> 200", s == 200)
        for bad_id in ("/api/jobs/x", "/api/jobs/..%2f..%2fetc", "/api/jobs/20261114-101500-K3F9QA",
                       "/api/nodes/Office_PC", "/api/nodes/../x"):
            s = req(port, bad_id, headers={"X-Mu-Token": tok})[0]
            check("API: %s -> 400/404" % bad_id, s in (400, 404), s)
        check("API: unknown job -> 404", req(port, "/api/jobs/20990101-000000-zzzzzz",
                                             headers={"X-Mu-Token": tok})[0] == 404)
        s, h, d = req(port, "/api/session", headers={"X-Mu-Token": tok})
        j = json.loads(d)
        check("every /api answer carries api: 1", j.get("api") == 1 and j.get("ok") is True)
    finally:
        stop_proc(p)
    # the token rotates with each start; the old one is refused (D14 fixed-token)
    p = serve_proc(home, port, static=st)
    try:
        tok2 = open(os.path.join(home, "run", "dev", "dashboard.token")).read().strip()
        check("D14: a new token at each server start", tok2 != tok and len(tok2) == 64)
        check("D14: the previous start's token -> 401", req(port, "/api/session", headers={"X-Mu-Token": tok})[0] == 401)
    finally:
        stop_proc(p)


# ------------------------------------------------------------------------------------------------ lifecycle, D27, D15
def ps_command(pid):
    r = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True)
    return r.stdout


def cputime(pid):
    r = subprocess.run(["ps", "-o", "cputime=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    if not r:
        r = subprocess.run(["ps", "-o", "time=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    parts = r.replace("-", ":").split(":")
    try:
        return sum(float(x) * m for x, m in zip(reversed(parts), (1, 60, 3600, 86400)))
    except ValueError:
        return None


def t_lifecycle():
    home = tmpdir("life")
    port = free_port()
    p = serve_proc(home, port)
    rd = os.path.join(home, "run", "dev")
    try:
        info = json.load(open(os.path.join(rd, "dashboard.json")))
        tok = open(os.path.join(rd, "dashboard.token")).read().strip()
        check("run/dev/dashboard.json names the pid and port", info.get("pid") == p.pid and info.get("port") == port)
        mode = lambda f: oct(os.stat(f).st_mode & 0o777)
        check("dashboard.json and dashboard.token are 0600, run/ and run/dev/ 0700",
              mode(os.path.join(rd, "dashboard.json")) == "0o600" and mode(os.path.join(rd, "dashboard.token")) == "0o600"
              and mode(rd) == "0o700" and mode(os.path.dirname(rd)) == "0o700")
        check("the token is 32 random bytes (64 hex)", len(tok) == 64 and int(tok, 16) >= 0)
        check("D15: the token is not in the server's argv", tok not in ps_command(p.pid) and ps_command(p.pid))
        # D27: a second serve exits 0 and serves nothing
        q = subprocess.run([sys.executable, "-I", "-S", CLI, "dashboard", "serve", "--dev", "--fake-mac", "--json"],
                           env=dict(os.environ, MU_HOME=home), capture_output=True, text=True, timeout=20)
        check("D27: a second `serve` exits 0 while one serves", q.returncode == 0 and "already" in q.stdout,
              q.stdout + q.stderr)
        info2 = json.load(open(os.path.join(rd, "dashboard.json")))
        check("D27: ... and the first one keeps serving", info2.get("pid") == p.pid and
              req(port, "/api/session", headers={"X-Mu-Token": tok})[0] == 200)
        # idle: no tab, nothing unfinished -> the server's CPU time barely grows (D23, shortened to 20 s)
        time.sleep(12)    # past the visible window of the requests above
        c0 = cputime(p.pid)
        time.sleep(20)
        c1 = cputime(p.pid)
        check("D23: idle for 20 s, the server's CPU time grows by at most 0.1 s", c0 is not None and c1 is not None and
              c1 - c0 <= 0.1, (c0, c1))
        log = open(os.path.join(home, "logs", "dashboard-dev.log")).read()
        check("D15: the token is not in logs/dashboard-dev.log", tok not in log and "serving on" in log)
    finally:
        stop_proc(p)
    check("SIGTERM: the server exits 0", p.returncode == 0, p.returncode)
    check("on exit, dashboard.json and the token file are removed",
          not os.path.exists(os.path.join(rd, "dashboard.json")) and not os.path.exists(os.path.join(rd, "dashboard.token")))


def t_open():
    home = tmpdir("open")
    os.environ["MU_HOME"] = home
    rec = os.path.join(home, "opener.argv")
    opener = os.path.join(home, "opener.sh")
    open(opener, "w").write('#!/bin/sh\nfor a in "$@"; do printf "%%s\\n" "$a"; done >> "%s"\n' % rec)
    os.chmod(opener, 0o755)
    port = free_port()
    env = dict(os.environ, MU_HOME=home, MU_OPENER=opener)
    # a non-dev server on a fake-node-free MU_HOME (no paired nodes)
    p = subprocess.Popen([sys.executable, "-I", "-S", CLI, "dashboard", "serve", "--port", str(port)], env=env,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            if os.path.exists(os.path.join(home, "run", "dashboard.token")):
                break
            time.sleep(0.1)
        tok = open(os.path.join(home, "run", "dashboard.token")).read().strip()
        r = subprocess.run([sys.executable, "-I", "-S", CLI, "dashboard", "open", "--json"], env=env,
                           capture_output=True, text=True, timeout=30)
        argv = open(rec).read() if os.path.exists(rec) else ""
        page = os.path.join(home, "run", "dashboard-open.html")
        check("D15: `open` exits 0 and names the URL without the token", r.returncode == 0 and tok not in r.stdout and
              ("127.0.0.1:%d" % port) in r.stdout, r.stdout + r.stderr)
        check("D15: the opener's argv holds only the redirect file's path, never the token",
              argv.strip() == page and tok not in argv, argv)
        check("D15: the redirect file is 0600 and redirects to #t=<token>",
              os.path.exists(page) and oct(os.stat(page).st_mode & 0o777) == "0o600" and
              ("#t=" + tok) in open(page).read())
        st, _ = jget(port, "/api/session?first=1", tok)
        check("D15: the redirect file is gone after the token's first use", st == 200 and not os.path.exists(page))
        for args in (["dashboard", "--json"], ["dashboard", "status", "--json"], ["dashboard"]):
            o = subprocess.run([sys.executable, "-I", "-S", CLI] + args, env=env, capture_output=True, text=True,
                               timeout=20)
            check("D15: `mu %s` holds no token" % " ".join(args), o.returncode == 0 and tok not in o.stdout + o.stderr
                  and ("127.0.0.1:%d" % port) in o.stdout, o.stdout + o.stderr)
        j = json.loads(subprocess.run([sys.executable, "-I", "-S", CLI, "dashboard", "--json"], env=env,
                                      capture_output=True, text=True).stdout)
        check("`mu dashboard --json`: running, port, url, agent, enabled, nodes",
              j.get("running") is True and j.get("port") == port and j.get("url") == "http://127.0.0.1:%d/" % port
              and "agent" in j and "enabled" in j and j.get("nodes") == [], j)
        log = open(os.path.join(home, "logs", "dashboard.log")).read()
        check("D15: the token is not in logs/dashboard.log", tok not in log)
    finally:
        stop_proc(p)


# ------------------------------------------------------------------------------------------------ D19, D22, submit's line
def t_submitline():
    home = tmpdir("submit")
    os.environ["MU_HOME"] = home
    url, running = jobs.dashboard_link("20261114-101500-k3f9qa")
    check("D19: no dashboard.json -> no URL, not running, nothing waited for", url is None and running is False)
    # a stub that accepts and never answers holds the dashboard's port
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    accepted = []

    def acceptor():
        while True:
            try:
                c, _ = srv.accept()
                accepted.append(c)
            except OSError:
                return
    threading.Thread(target=acceptor, daemon=True).start()
    os.makedirs(os.path.join(home, "run"), exist_ok=True)
    tok = "f" * 64
    open(os.path.join(home, "run", "dashboard.token"), "w").write(tok)
    open(os.path.join(home, "run", "dashboard.json"), "w").write(json.dumps({"pid": os.getpid(), "port": port}))
    res = []
    t = threading.Thread(target=lambda: res.append(jobs.dashboard_link("20261114-101500-k3f9qa")), daemon=True)
    t0 = time.time()
    t.start()
    t.join(5)
    check("D19: with a silent server on the port, the submit's dashboard line does not wait", res and
          time.time() - t0 < 2, "hung" if not res else "")
    time.sleep(0.2)
    check("D19: ... and never connects to the dashboard", not accepted, "%d connection(s)" % len(accepted))
    if res:
        url, running = res[0]
        check("§4.7: the line is http://127.0.0.1:<port>/#job=<id>, without the token",
              url == "http://127.0.0.1:%d/#job=20261114-101500-k3f9qa" % port and tok not in url and running)
    srv.close()
    open(os.path.join(home, "run", "dashboard.json"), "w").write(json.dumps({"pid": 999999, "port": port}))
    check("a dead pid -> not running", jobs.dashboard_link("20261114-101500-k3f9qa") == (None, False))
    src = open(os.path.join(REPO, "src", "mu", "jobs.py"), encoding="utf-8").read()
    check("D19: jobs.py never imports the dashboard module", "import dashboard" not in src and
          "from mu import dashboard" not in src)


def t_index():
    home = tmpdir("index")
    os.environ["MU_HOME"] = home
    prj = os.path.join(home, "my project")
    os.makedirs(os.path.join(prj, ".mu", "jobs"))
    jid = "20261114-101500-k3f9qa"
    spec = {"argv": ["python", "x.py"], "limits": {"mem_bytes": 1 << 30, "time_s": 600, "cores": 2},
            "head": "a" * 40, "snapshot": "b" * 40, "dirty": False, "mu_version": "v2026.11.9", "cwd": "",
            "outputs": [], "submitted": {"at": "2026-11-14T10:15:00Z", "host": "mac", "user": "u", "mu": "v"}}
    durable.write_record(os.path.join(prj, ".mu", "jobs", jid + ".json"),
                         {"schema": 1, "job_id": jid, "node": "office-pc", "spec": spec, "accepted": "2026-11-14T10:15:03Z"})
    jobs.index_append({"job_id": jid, "node": "office-pc", "key": "k", "project_root": prj, "project": "my project",
                       "project_id": "my-project-0123456789ab", "name": "n", "submit_started": "2026-11-14T10:15:00Z",
                       "accepted": "2026-11-14T10:15:03Z", "fetched": None})
    p = os.path.join(home, "jobs.index")
    check("D22: `mu submit`'s index line is written, 0600", os.path.exists(p) and
          oct(os.stat(p).st_mode & 0o777) == "0o600")
    with open(p, "a") as f:
        f.write("not json\n{\"job_id\": \"../../etc\"}\n")
    idx = jobs.index_read()
    check("D22: index_read finds the job and skips bad lines", list(idx) == [jid], list(idx))
    src = dashboard.IndexSource(dashfake.Clock())
    rec = src.record(jid)
    check("D22: the job's .mu/jobs record is found through jobs.index", isinstance(rec, dict) and
          rec.get("spec", {}).get("limits", {}).get("cores") == 2, rec)
    # the API carries the project and limits for an indexed job
    clock = dashfake.ManualClock(dashfake.FIXED_T0)
    node = dashfake.FakeNode("office-pc", clock, clock.wall(), [])
    app = dashboard.App(dev=True, clock=clock, readers={"office-pc": dashnode.NodeReader("office-pc", node)},
                        index=src, mac=dashfake.FakeMac(clock))
    w = app.workers["office-pc"]
    w.jobs = {jid: {"job_id": jid, "name": "n", "state": "running", "project_id": "x"}}
    row = app.rows()[0]
    check("D22: the job row has the project and the limits from the record", row["project"] == "my project" and
          row["mine"] and row["limits"] == {"mem_bytes": 1 << 30, "time_s": 600, "cores": 2}, row)
    # many: 150 finished jobs -> 100 rows of finished jobs and "older jobs"
    p2 = InProc("many")
    p2.ticks(2)
    n = p2.sc.nodes["office-pc"]
    lists = calls(n, "list")
    rows = p2.app.rows()
    fin = [r for r in rows if r["state"] in proto.FINISHED]
    check("D22: the dashboard sends limit=100", lists and all(a.get("limit") == "100" for (_, _, a) in lists), lists)
    check("D22: with 150 finished jobs the table holds 100 of them and says there are more",
          len(fin) == 100 and p2.app.api_overview()["more"]["office-pc"] is True, (len(fin), len(rows)))
    p2.stop()


# ------------------------------------------------------------------------------------------------ D39 old-node budget
def old_scenario(clock, run_whole=True):
    t0 = clock.wall()
    if run_whole:
        jobs_ = [dashfake.FakeJob("office-pc", "r", "long", queued=t0 - 200, start=t0 - 100)]
    else:
        jobs_ = []
    jobs_ += [dashfake.FakeJob("office-pc", "f%d" % i, "done", queued=t0 - 9000 - i * 100, start=t0 - 8990 - i * 100,
                               end=t0 - 8000 - i * 100, final="succeeded", exit_code=0) for i in range(3)]
    return dashfake.FakeNode("office-pc", clock, t0, jobs_, features=False, mu_version="v2026.11.8")


def t_oldnode():
    clock = dashfake.ManualClock(dashfake.FIXED_T0)
    p = InProc("old-node", clock=clock, nodes={"office-pc": old_scenario(clock)})
    p.ticks(600)
    n = len(calls(p.sc.nodes["office-pc"], "list"))
    check("D39: a tab visible 10 min, a job running on an old node: at most 45 list calls", n <= 45, n)
    check("D39: ... and it is polled (at least 30)", n >= 30, n)
    verbs = sorted({v for (_, v, _) in calls(p.sc.nodes["office-pc"])})
    check("D39: an old node is asked only hello, list (no stats, watch, metrics)", set(verbs) <= {"hello", "list"},
          verbs)
    p.stop()
    clock = dashfake.ManualClock(dashfake.FIXED_T0)
    p = InProc("old-node", clock=clock, nodes={"office-pc": old_scenario(clock, run_whole=False)})
    p.ticks(600)
    n = len(calls(p.sc.nodes["office-pc"], "list"))
    check("D39: nothing unfinished: at most 12 list calls in 10 min", n <= 12, n)
    p.stop()
    # each state change within 20 s (the old-node scenario: queued -> running at +20 -> succeeded at +60)
    clock = dashfake.ManualClock(dashfake.FIXED_T0)
    p = InProc("old-node", clock=clock)
    node = p.sc.nodes["office-pc"]
    target = [j for j in node.jobs if j.name == "frame-fea" and j.final == "succeeded" and j.start and
              j.start > clock.wall()][0]
    lag = {"since": None, "max": 0.0}

    def each(i):
        t = clock.wall()
        truth = target.state(t)
        seen = next((r["state"] for r in p.app.rows() if r["job_id"] == target.id), None)
        if seen != truth:
            if lag["since"] is None:
                lag["since"] = t
            lag["max"] = max(lag["max"], t - lag["since"])
        else:
            lag["since"] = None
    p.ticks(120, each=each)
    check("D39: an old node's state changes reach the API within 20 s", lag["max"] <= 20.0, lag["max"])
    card = p.app.workers["office-pc"].card()
    check("D04 (API): the old node's card says 'update μ on office-pc for live statistics'",
          card["old_node"] and card["old_node_text"] == "update μ on office-pc for live statistics" and
          card["stats"] is None, card)
    p.stop()


# ------------------------------------------------------------------------------------------------ D18 errors
def t_errors():
    clock = dashfake.ManualClock(dashfake.FIXED_T0)
    p = InProc("offline", clock=clock)
    p.ticks(160)
    n = p.sc.nodes["office-pc"]
    ts = [t - dashfake.FIXED_T0 for (t, _, _) in calls(n)]
    gaps = [round(b - a) for a, b in zip(ts, ts[1:])]
    check("D18: an offline node is retried after 5, 10, 20, then 60 s", gaps[:4] == [5, 10, 20, 60], gaps)
    c = p.app.workers["office-pc"].card()
    check("D18: its card says offline since, exit 4, the CLI's words, and when it retries",
          c["state"] == "offline" and c["offline_since"] == dashfake.FIXED_T0 and c["error"]["exit"] == 4 and
          c["error"]["auto_retry"] is True and "not connected" in c["error"]["message"], c)
    check("D18: the other node is unaffected", p.app.workers["lab-pc"].card()["state"] == "online")
    p.stop()
    clock = dashfake.ManualClock(dashfake.FIXED_T0)
    p = InProc("refused", clock=clock)
    p.ticks(2)
    n = p.sc.nodes["office-pc"]
    first = len(calls(n))
    p.ticks(300)
    more = len(calls(n)) - first
    check("D18: refused (exit 5): zero further node requests in 5 min", more == 0, more)
    c = p.app.workers["office-pc"].card()
    check("D18: ... red state with the CLI's message, no automatic retry", c["state"] == "refused" and
          c["error"]["exit"] == 5 and c["error"]["auto_retry"] is False and "host key" in c["error"]["message"], c)
    p.app.retry("office-pc")
    p.ticks(1)
    check("D18: the retry button asks once", len(calls(n)) - first == 1, len(calls(n)) - first)
    p.stop()


# ------------------------------------------------------------------------------------------------ D23 idle, D24 battery
def t_idle():
    clock = dashfake.ManualClock(dashfake.FIXED_T0)
    p = InProc("old-node", clock=clock, nodes={"office-pc": old_scenario(clock, run_whole=False)})
    p.ticks(300, visible=False)
    n = len(calls(p.sc.nodes["office-pc"]))
    check("D23: no tab and nothing unfinished for 5 min: zero node requests", n == 0, n)
    p.stop()


def t_battery():
    os.environ["MU_DASH_BATTERY"] = "1"
    clock = dashfake.ManualClock(dashfake.FIXED_T0)
    p = InProc("old-node", clock=clock, nodes={"office-pc": old_scenario(clock)})
    p.ticks(5)                                  # a tab saw the running job
    t_hidden = clock.wall()
    p.ticks(900, visible=False)
    ls = [t for (t, v, _) in calls(p.sc.nodes["office-pc"], "list") if t >= t_hidden]
    gaps = [b - a for a, b in zip(ls, ls[1:])]
    check("D24: on battery, no tab, a job running: list at most every 5 min", gaps and min(gaps) >= 299, gaps)
    check("D24: ... and still noticed (at least 2 lists in 15 min)", len(ls) >= 2, ls)
    os.environ["MU_DASH_BATTERY"] = "0"
    clock = dashfake.ManualClock(dashfake.FIXED_T0)
    p2 = InProc("old-node", clock=clock, nodes={"office-pc": old_scenario(clock)})
    p2.ticks(5)
    t_hidden = clock.wall()
    p2.ticks(300, visible=False)
    ls = [t for (t, v, _) in calls(p2.sc.nodes["office-pc"], "list") if t >= t_hidden]
    gaps = [round(b - a) for a, b in zip(ls, ls[1:])]
    check("§4.3: on mains, no tab, a job running: list every 60 s", gaps and all(g == 60 for g in gaps), gaps)
    skip("D24: the dashboard's ssh master is gone after ControlPersist", "real ssh; phase 1a uses fake nodes only")
    p.stop()
    p2.stop()


# ------------------------------------------------------------------------------------------------ D05-D07 (API parts), wake
def t_nodes():
    clock = dashfake.ManualClock(dashfake.FIXED_T0)
    p = InProc("busy", clock=clock)
    lab = p.sc.nodes["lab-pc"]
    target = [j for j in lab.jobs if j.name == "cfd-cavity"][0]
    lag = {"since": None, "max": 0.0, "states": set(), "nodes": set()}

    def each(i):
        t = clock.wall()
        truth = target.state(t)
        row = next((r for r in p.app.rows() if r["job_id"] == target.id), None)
        seen = row["state"] if row else None
        if row:
            lag["nodes"].add(row["node"])
            lag["states"].add(seen)
        if seen != truth:
            lag["since"] = lag["since"] if lag["since"] is not None else t
            lag["max"] = max(lag["max"], t - lag["since"])
        else:
            lag["since"] = None
    p.ticks(60, each=each, real_sleep=0.03)
    check("D05: lab-pc's job is listed under lab-pc (and only there)", lag["nodes"] == {"lab-pc"}, lag["nodes"])
    check("D05: it follows queued -> running -> succeeded", {"queued", "running", "succeeded"} <= lag["states"],
          lag["states"])
    check("D05: each change reaches the API within 15 s of the node's change", lag["max"] <= 15.0, lag["max"])
    off = [r for r in p.app.rows() if r["node"] == "office-pc"]
    check("D05: office-pc's jobs are under office-pc", off and all(r["job_id"] in {j.id for j in
                                                                                    p.sc.nodes["office-pc"].jobs}
                                                                    for r in off))
    # D06 figures
    node = p.sc.nodes["office-pc"]
    w = p.app.workers["office-pc"]
    ans = node.stats()
    w.on_stats(ans)
    c = w.card()
    mem = ans["host"]["mem"]
    check("D06: the node card shows the node's numbers (available, used = total - available, CPU %)",
          c["stats"]["mem"].get("avail") == mem["avail"] and c["stats"]["mem"]["used"] == mem["total"] - mem["avail"]
          and c["stats"]["cpu_pct"] == ans["host"]["cpu_pct"], (c["stats"]["mem"], mem))
    live = w.live(clock.wall())
    ok = all(live[r["job_id"]]["cpu_cores"] == r["cpu_cores"] and live[r["job_id"]]["anon"] == r["anon"] and
             live[r["job_id"]]["max"] == r["max"] for r in ans["running"])
    check("D06: each running job's cores, memory and limit are the node's", ok and len(live) == len(ans["running"]))
    p.stop()


def t_stale():
    clock = dashfake.ManualClock(dashfake.FIXED_T0)
    p = InProc("stale", clock=clock)
    p.ticks(10, real_sleep=0.03)
    c = p.app.workers["office-pc"].card()
    check("D07: while the link holds, the figures are live", c["stats"] and c["stats"]["stale"] is False, c["stats"])
    p.ticks(40, real_sleep=0.03)
    time.sleep(0.3)
    c = p.app.workers["office-pc"].card()
    rows = [r for r in p.app.rows() if r["node"] == "office-pc"]
    check("D07: 20 s after the cut, the node's figures are marked stale with their time",
          c["stats"] and c["stats"]["stale"] is True and c["stats"]["t"] <= clock.wall() - 15, c["stats"])
    run = [r for r in rows if r["state"] == "running"]
    check("D07: the running job's live figures are marked stale", run and run[0]["live"] and
          run[0]["live"]["stale"] is True, run[0]["live"] if run else rows)
    check("D07: the job rows say as of when", all(r["as_of"] is not None for r in rows))
    p.stop()


def t_wake():
    clock = dashfake.ManualClock(dashfake.FIXED_T0)
    p = InProc("old-node", clock=clock, nodes={"office-pc": old_scenario(clock)})
    p.ticks(20)
    n = p.sc.nodes["office-pc"]
    closed, hellos = n.masters_closed, len(calls(n, "hello"))
    clock.advance(0, wall_jump=600)
    p.ticks(3)
    check("wake: a wall-clock jump reconciles: own master reopened, hello and list again",
          n.masters_closed == closed + 1 and len(calls(n, "hello")) == hellos + 1, (n.masters_closed, closed))
    check("wake: the card is not left 'reconnecting'", p.app.workers["office-pc"].card()["reconnecting"] is False)
    p.stop()


def t_devguard():
    dashnode.dev_mode(True)
    try:
        dashnode.NodeReader.real({"name": "office-pc", "_dir": "/nonexistent"})
        refused = False
    except RuntimeError:
        refused = True
    check("§4.8: in dev mode no real NodeReader can be built", refused)
    dashnode.dev_mode(False)


def t_logs():
    st = static_fixture()
    p = InProc("hostile", static=st)
    p.ticks(2)
    tok = p.app.token
    h = [r for r in p.app.rows() if r["state"] == "running"][0]
    s, j = jget(p.app.port, "/api/jobs/%s/log?offset=0" % h["job_id"], tok)
    text, nxt = "", 0
    for _ in range(40):
        s, j = jget(p.app.port, "/api/jobs/%s/log?offset=%d" % (h["job_id"], nxt), tok)
        text += j["text"]
        nxt = j["next"]
        if len(text) > (1 << 20) + 500:
            break
        time.sleep(0.05)
    check("log: pieces join by offset", s == 200 and text.startswith("== μ job") and nxt > 1 << 20, (s, nxt))
    check("log: control and escape sequences are removed", "\x1b" not in text and "\x07" not in text and
          "red ANSI text" in text)
    check("log: the HTML stays text (the page renders it as text)", "<img src=x onerror=" in text)
    s, j = jget(p.app.port, "/api/jobs/%s/log?offset=-1" % h["job_id"], tok)
    check("log: a negative offset -> 400", s == 400)
    s, j = jget(p.app.port, "/api/overview", tok)
    names = [r["name"] for r in j["jobs"]]
    check("API: node strings arrive cleaned of control characters", all("\x1b" not in (n or "") for n in names) and
          all("‮" not in (r["note"] or "") for r in j["jobs"]), names)
    p.stop()
    # a multi-byte character split across pieces is never cut in half
    lb = dashboard.LogBuf(None, "20261114-101500-k3f9qa", 0)
    lb.data.extend("aé€😀".encode() * 10)
    p1 = lb.piece(0)
    check("log: utf-8 decoding is whole characters", p1["text"] == "aé€😀" * 10 and p1["next"] == len(lb.data))
    del lb.data[-1]
    p2 = lb.piece(0)
    check("log: an incomplete final character waits for its bytes", p2["next"] == len(lb.data) - 3 and
          p2["text"].endswith("€"), (p2["next"], len(lb.data)))


# ------------------------------------------------------------------------------------------------ D38 fixtures
FREE_KEYS = {"more"}


def shape(a, b, path="$"):
    """Differences in keys and types between A (served) and B (fixture). None matches anything."""
    if a is None or b is None:
        return []
    if isinstance(a, bool) or isinstance(b, bool):
        return [] if isinstance(a, bool) and isinstance(b, bool) else ["%s: %r vs %r" % (path, type(a), type(b))]
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return []
    if isinstance(a, dict) and isinstance(b, dict):
        if path.split(".")[-1] in FREE_KEYS:
            return []
        out = []
        if set(a) != set(b):
            out.append("%s: keys %s vs %s" % (path, sorted(set(a) - set(b)), sorted(set(b) - set(a))))
        for k in set(a) & set(b):
            out += shape(a[k], b[k], path + "." + k)
        return out
    if isinstance(a, list) and isinstance(b, list):
        return shape(a[0], b[0], path + "[0]") if a and b else []
    return [] if type(a) is type(b) else ["%s: %s vs %s" % (path, type(a).__name__, type(b).__name__)]


def t_fixtures():
    fx = os.path.join(HERE, "fixtures")
    d = tmpdir("fixtures")
    names = dashfake.write_fixtures(d)
    have = sorted(x for x in os.listdir(fx) if x.endswith(".json"))
    check("D38: the committed fixtures are the set dashfake emits", names == have, (names, have))
    diff = [n for n in names if os.path.exists(os.path.join(fx, n)) and
            open(os.path.join(d, n), "rb").read() != open(os.path.join(fx, n), "rb").read()]
    check("D38: every committed fixture is byte for byte what dashfake and the server emit now", not diff, diff)
    load = lambda n: json.load(open(os.path.join(fx, n)))
    st = static_fixture()
    for scen in dashfake.SCENARIOS:
        p = InProc(scen, static=st)
        p.ticks(3, real_sleep=0.03)
        tok = p.app.token
        probs = []
        for path, fixture in (("/api/session", "api-session.json"), ("/api/overview", "api-overview.json"),
                              ("/api/jobs", "api-jobs.json"), ("/api/mac", "api-mac.json")):
            s, j = jget(p.app.port, path, tok)
            probs += ["%s %s" % (path, x) for x in shape(j, load(fixture))]
        s, j = jget(p.app.port, "/api/overview", tok)
        for node in j["nodes"]:
            probs += ["node card %s" % x for x in shape(node, load("api-overview.json")["nodes"][0])]
            s2, nj = jget(p.app.port, "/api/nodes/%s" % node["name"], tok)
            probs += ["node %s" % x for x in shape(nj, load("api-node.json"))]
        if j["jobs"]:
            jid = j["jobs"][0]["job_id"]
            s3, jj = jget(p.app.port, "/api/jobs/%s" % jid, tok)
            probs += ["job %s" % x for x in shape(jj, load("api-job.json"))]
            s4, mj = jget(p.app.port, "/api/jobs/%s/metrics" % jid, tok)
            probs += ["metrics %s" % x for x in shape(mj, load("api-job-metrics.json"))]
        check("D38: scenario %s: every /api answer has the fixtures' keys and types" % scen, not probs, "\n".join(probs))
        p.stop()
    hello = load("node-hello.json")
    check("D38: the node fixtures carry hello.features and the old node's does not",
          "features" in hello and "features" not in load("node-hello-old.json"))
    stats = load("node-stats.json")
    check("D38: stats has the shape of §4.2", set(stats) >= {"ok", "t", "age_s", "stale", "node", "rev", "host",
                                                            "running", "queue"} and
          set(stats["host"]["mem"]) == {"total", "avail", "cached", "shmem"}, sorted(stats))


# ------------------------------------------------------------------------------------------------ D08 sampler
def t_sampler():
    ps = dashboard.load_psutil()
    if ps is None:
        if os.environ.get("MU_TEST_NEED_PSUTIL") == "1":
            check("D08: psutil is importable from this Python's own site-packages", False)
        else:
            skip("D08: the Mac sampler", "no psutil in this Python (the toolchain's Python has it)")
        return
    check("D08: psutil comes from this Python's own site-packages, imported by path", ps.__file__.startswith(
        os.path.realpath(sys.prefix)) or ps.__file__.startswith(sys.prefix), ps.__file__)
    m = dashboard.MacSampler(dashfake.Clock(), psutil_mod=ps)
    n = os.cpu_count() or 1

    def mean_cpu(seconds):
        vals = []
        m.sample()
        end = time.time() + seconds
        while time.time() < end:
            time.sleep(1.0)
            s = m.sample()
            if s and s["cpu_pct"] is not None:
                vals.append(s["cpu_pct"])
        return sum(vals) / len(vals) if vals else 0.0
    base = mean_cpu(4) * n / 100.0
    procs = [subprocess.Popen([sys.executable, "-I", "-S", "-c", "import time\nt=time.time()+12\nwhile time.time()<t: pass"])
             for _ in range(2)]
    time.sleep(1)
    busy = mean_cpu(10) * n / 100.0
    for q in procs:
        q.wait()
    got = busy - base
    check("D08: two busy cores read as 2 cores' worth (± 25 %%): %.2f (baseline %.2f, %d logical)" % (got, base, n),
          1.5 <= got <= 2.5 or (n <= 2 and busy >= 0.75 * n), "busy %.2f base %.2f" % (busy, base))
    c = m.card()
    check("D08: the card has CPU, per core, memory used/available/total and its time",
          c["cpu_pct"] is not None and c["per_core"] and c["mem"]["total"] and c["mem"]["available"] is not None and
          c["t"] and c["sampler"] == "psutil", c)


# ------------------------------------------------------------------------------------------------ D28 BUILD.json
def t_build():
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("dbi", os.path.join(REPO, "tools", "dashboard-build-info.py"))
    dbi = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dbi)
    src = os.path.join(REPO, "dashboard")
    bj = os.path.join(REPO, "share", "dashboard", "BUILD.json")
    if durable.fault("stale-build"):         # a component changed and committed without a rebuild
        d = tmpdir("stale")
        shutil.copytree(src, os.path.join(d, "dashboard"), ignore=shutil.ignore_patterns("node_modules"))
        f = os.path.join(d, "dashboard", "src", "main.tsx")
        open(f, "a").write("\n// changed without a rebuild\n")
        src = os.path.join(d, "dashboard")
    if not os.path.exists(bj):
        check("D28: share/dashboard/BUILD.json exists (the committed build)", False, "not built yet")
        return
    built = json.load(open(bj))
    now = dbi.source_info(src)
    check("D28: BUILD.json's source hash matches dashboard/ (no Node needed)",
          built.get("source_sha256") == now["source_sha256"], (built.get("source_sha256"), now["source_sha256"]))
    check("D28: BUILD.json's Node is .nvmrc's", built.get("node") == now["node"], (built.get("node"), now["node"]))
    check("D28: BUILD.json's lockfile hash matches", built.get("lockfile_sha256") == now["lockfile_sha256"])


# ------------------------------------------------------------------------------------------------ LaunchAgent (D23 part)
def t_agent():
    home = tmpdir("agent")
    os.environ["MU_HOME"] = home
    agents = os.path.join(home, "LaunchAgents")
    rec = os.path.join(home, "launchctl.log")
    stub = os.path.join(home, "launchctl")
    open(stub, "w").write('#!/bin/sh\nprintf "%%s\\n" "$*" >> "%s"\ncase "$1" in print) case "$2" in gui/*/*) '
                          '[ -f "%s/loaded" ] && exit 0; exit 113 ;; gui/*) exit 0 ;; *) exit 113 ;; esac ;; '
                          'bootstrap) touch "%s/loaded" ;; bootout) rm -f "%s/loaded" ;; esac\nexit 0\n'
                          % (rec, home, home, home))
    os.chmod(stub, 0o755)
    env = dict(os.environ, MU_HOME=home, MU_LAUNCH_AGENTS_DIR=agents, MU_LAUNCHCTL=stub)
    env.pop("CI", None)
    r = subprocess.run([sys.executable, "-I", "-S", CLI, "dashboard", "on", "--json"], env=env, capture_output=True,
                       text=True, timeout=30)
    pl = os.path.join(agents, dashboard.LABEL + ".plist")
    check("D23: `mu dashboard on` writes the LaunchAgent and loads it", r.returncode == 0 and os.path.exists(pl) and
          json.loads(r.stdout).get("agent") == "loaded", r.stdout + r.stderr)
    d = plistlib.load(open(pl, "rb"))
    check("D23: RunAtLoad, KeepAlive, `mu dashboard serve`, MU_HOME, MU_CACHE, PATH, a log file",
          d.get("RunAtLoad") is True and d.get("KeepAlive") is True and
          d.get("ProgramArguments") == [os.path.join(home, "bin", "mu"), "dashboard", "serve"] and
          set(d.get("EnvironmentVariables", {})) == {"MU_HOME", "MU_CACHE", "PATH"} and
          d.get("StandardErrorPath", "").endswith("launchd-dashboard.log"), d)
    if shutil.which("plutil"):
        check("D23: the plist passes plutil -lint", subprocess.run(["plutil", "-lint", "-s", pl]).returncode == 0)
    log = open(rec).read()
    check("D23: loaded with launchctl bootstrap into the gui domain", "bootstrap gui/%d %s" % (os.getuid(), pl) in log,
          log)
    j = json.loads(subprocess.run([sys.executable, "-I", "-S", CLI, "dashboard", "--json"], env=env,
                                  capture_output=True, text=True).stdout)
    check("`mu dashboard --json` says enabled on, agent loaded", j["enabled"] == "on" and j["agent"] == "loaded", j)
    r = subprocess.run([sys.executable, "-I", "-S", CLI, "dashboard", "off", "--json"], env=env, capture_output=True,
                       text=True, timeout=30)
    log = open(rec).read()
    check("D23: `off` boots it out and removes the plist", r.returncode == 0 and not os.path.exists(pl) and
          "bootout gui/%d/%s" % (os.getuid(), dashboard.LABEL) in log, r.stdout + r.stderr)
    j = json.loads(subprocess.run([sys.executable, "-I", "-S", CLI, "dashboard", "--json"], env=env,
                                  capture_output=True, text=True).stdout)
    check("`mu dashboard --json` says enabled off, agent not installed", j["enabled"] == "off" and
          j["agent"] == "not installed", j)
    skip("D23: launchd restarts a killed server; the URL answers after login",
         "a real LaunchAgent is not loaded by the tests (the owner's Mac and CI runners)")


SECTIONS = {
    "allowlist": t_allowlist, "controlpath": t_controlpath, "http": t_http, "lifecycle": t_lifecycle, "open": t_open,
    "submitline": t_submitline, "index": t_index, "oldnode": t_oldnode, "errors": t_errors, "idle": t_idle,
    "battery": t_battery, "nodes": t_nodes, "stale": t_stale, "wake": t_wake, "devguard": t_devguard, "logs": t_logs,
    "fixtures": t_fixtures, "sampler": t_sampler, "build": t_build, "agent": t_agent,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in SECTIONS:
        sys.stderr.write("usage: unit.py SECTION (%s)\n" % " ".join(SECTIONS))
        sys.exit(2)
    SECTIONS[sys.argv[1]]()
    sys.stdout.flush()
    os._exit(min(FAILS[0], 100))
