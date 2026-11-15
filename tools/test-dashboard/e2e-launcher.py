"""Starts fresh `mu dashboard serve --dev` servers for the Playwright tests (docs/design/dashboard.md §4.8, §7.1).

The page tests need each scenario from its start (a fake node's job goes queued -> running -> succeeded on a fixed
schedule from the server's start), servers with a test fault put in (MU_JOBS_FAULT), and a restart on the same port
(D14: the token rotates). Playwright runs inside the sandbox on the Mac (tools/dashboard-npm), and the dev server is
μ's own code, started outside it (§4.6.1). So this small launcher runs outside the sandbox, and the tests ask it, over
127.0.0.1 with a token of its own, to start, restart and stop servers. It starts only `serve --dev` (fake nodes only:
the server refuses a real node in dev mode), only with a scenario and faults from fixed lists, each with a throw-away
MU_HOME under BASE.

    python3 -I tools/test-dashboard/e2e-launcher.py --base DIR --static BUILD [--python PY] [--faults a,b]
        [--env NAME=VALUE ...]
    writes DIR/launcher.json {port, token} (0600) once it listens; stops every server it started on SIGTERM.

    GET /start?scenario=NAME[&faults=a,b]   -> {"id", "port", "token", "home"}
    GET /restart?id=N                       -> the same port and home, a new process (and a new token)
    GET /stop?id=N, GET /stopall
Every request needs the header X-Launcher-Token. --faults and --env apply to every server (the must-fail runs).
"""
import argparse
import hmac
import http.server
import json
import os
import secrets
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SCENARIOS = {"idle", "busy", "offline", "refused", "stale", "old-node", "r1-node", "paused", "hostile", "many", "wake"}
SERVER_FAULTS = {"node-from-config", "swap-mem-fields", "serve-last-as-live", "fixed-token", "csp-unsafe-inline",
                 "retry-refused", "rename-field", "poll-list-2s", "default-limit", "no-trusted-types", "features-any"}
ENV_ALLOWED = {"MU_DASH_OLD_STEP", "MU_DASH_FAKE_SPEED"}


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Launcher:
    def __init__(self, a):
        self.base, self.static, self.python = a.base, a.static, a.python
        self.faults = [f for f in (a.faults or "").split(",") if f]
        self.env = dict(e.split("=", 1) for e in a.env or [])
        self.procs = {}   # id -> dict(proc, port, home, scenario, faults)
        self.n = 0
        self.lock = threading.Lock()

    def _spawn(self, ent):
        run = os.path.join(ent["home"], "run", "dev")
        for f in ("dashboard.token", "dashboard.json"):
            try:
                os.unlink(os.path.join(run, f))
            except FileNotFoundError:
                pass
        env = {"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8", "HOME": ent["home"], "MU_HOME": ent["home"],
               "MU_CACHE": os.path.join(ent["home"], "cache")}
        env.update(self.env)
        faults = self.faults + ent["faults"]
        if faults:
            env["MU_JOBS_FAULT"] = ",".join(faults)
        log = open(os.path.join(ent["home"], "serve.log"), "ab")
        ent["proc"] = subprocess.Popen(
            [self.python, "-I", "-S", os.path.join(REPO, "src", "mu", "cli.py"), "dashboard", "serve", "--dev",
             "--fake-mac", "--scenario", ent["scenario"], "--port", str(ent["port"]), "--static", self.static],
            env=env, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
        log.close()
        tok = os.path.join(run, "dashboard.token")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if ent["proc"].poll() is not None:
                raise RuntimeError("the dev server exited (%s)" % ent["proc"].returncode)
            if os.path.exists(tok) and os.path.exists(os.path.join(run, "dashboard.json")):
                try:
                    with socket.create_connection(("127.0.0.1", ent["port"]), timeout=0.5):
                        pass
                    ent["token"] = open(tok).read().strip()
                    return
                except OSError:
                    pass
            time.sleep(0.05)
        raise RuntimeError("the dev server did not start in 20 s")

    def _kill(self, ent):
        p = ent.get("proc")
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()

    def start(self, scenario, faults):
        if scenario not in SCENARIOS:
            raise ValueError("unknown scenario")
        if any(f not in SERVER_FAULTS for f in faults):
            raise ValueError("unknown fault")
        with self.lock:
            self.n += 1
            sid = self.n
        home = os.path.join(self.base, "s%d" % sid)
        os.makedirs(home, mode=0o700, exist_ok=True)
        ent = {"port": free_port(), "home": home, "scenario": scenario, "faults": faults}
        self._spawn(ent)
        with self.lock:
            self.procs[sid] = ent
        return {"id": sid, "port": ent["port"], "token": ent["token"], "home": home}

    def restart(self, sid):
        ent = self.procs[sid]
        self._kill(ent)
        for _ in range(100):   # the port is free again once the old process is gone
            try:
                s = socket.socket()
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", ent["port"]))
                s.close()
                break
            except OSError:
                time.sleep(0.05)
        self._spawn(ent)
        return {"id": sid, "port": ent["port"], "token": ent["token"], "home": ent["home"]}

    def stop(self, sid):
        ent = self.procs.pop(sid, None)
        if ent:
            self._kill(ent)
        return {"id": sid, "stopped": True}

    def stopall(self):
        for sid in list(self.procs):
            self.stop(sid)
        return {"stopped": True}


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def reply(self, status, obj):
        b = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        srv = self.server
        if not hmac.compare_digest(self.headers.get("X-Launcher-Token", ""), srv.token):
            return self.reply(401, {"error": "token"})
        u = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        try:
            if u.path == "/start":
                faults = [f for f in q.get("faults", "").split(",") if f]
                return self.reply(200, srv.launcher.start(q.get("scenario", "busy"), faults))
            if u.path == "/restart":
                return self.reply(200, srv.launcher.restart(int(q["id"])))
            if u.path == "/stop":
                return self.reply(200, srv.launcher.stop(int(q["id"])))
            if u.path == "/stopall":
                return self.reply(200, srv.launcher.stopall())
            return self.reply(404, {"error": "not-found"})
        except (ValueError, KeyError, RuntimeError) as e:
            return self.reply(400, {"error": str(e)[:200]})


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--static", required=True)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--faults", default=os.environ.get("DASH_E2E_SERVER_FAULTS", ""))
    ap.add_argument("--env", action="append", default=[])
    a = ap.parse_args()
    for f in (x for x in a.faults.split(",") if x):
        if f not in SERVER_FAULTS:
            sys.exit("e2e-launcher: unknown server fault %r" % f)
    for e in a.env:
        if e.split("=", 1)[0] not in ENV_ALLOWED:
            sys.exit("e2e-launcher: --env %r is not allowed" % e)
    os.makedirs(a.base, mode=0o700, exist_ok=True)
    launcher = Launcher(a)
    srv = Server(("127.0.0.1", 0), Handler)
    srv.launcher, srv.token = launcher, secrets.token_hex(16)
    info = os.path.join(a.base, "launcher.json")
    fd = os.open(info + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"port": srv.server_address[1], "token": srv.token, "pid": os.getpid()}, f)
    os.replace(info + ".tmp", info)

    def on_signal(signum, frame):
        threading.Thread(target=srv.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    try:
        srv.serve_forever(poll_interval=0.2)
    finally:
        launcher.stopall()
        try:
            os.unlink(info)
        except OSError:
            pass
        if os.environ.get("DASH_E2E_KEEP") != "1":
            for d in os.listdir(a.base):
                if d.startswith("s") and d[1:].isdigit():
                    shutil.rmtree(os.path.join(a.base, d), ignore_errors=True)


if __name__ == "__main__":
    main()
