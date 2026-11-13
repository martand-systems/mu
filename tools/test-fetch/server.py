"""Fault-injecting HTTP server for the installer's download tests (stdlib only; run with python3 -I).

    python3 -I server.py DIR PORTFILE [BIG_MB] [SMALL_MB] [PORT]

Writes DIR/big.bin and DIR/small.bin (random bytes) if missing, listens on 127.0.0.1 (PORT, or a free port; written to
PORTFILE) and serves the files with byte-range support. The URL path selects the behaviour:

    /m/<spec>/<file>        the file, with the faults in <spec>
    /r/<spec>/<file>        302 redirect to /m/<spec>/<file>
    /gh/<spec>/<file>       302 redirect to /s/<token>/<spec>/<file> (a new token each time, like GitHub's signed
                            release URLs); a token is valid for expire=S seconds, then answers 403
    /_stats                 counters since the last reset (key=value lines)
    /_reset                 zero the counters and the per-spec fault state
    /_proxyfault/N          the server is also an HTTP CONNECT proxy (for curl --proxytunnel): close the Nth tunnel
                            right after "200 Connection established" (0: none). Counted from the last /_reset.

<spec> is "plain" or faults joined by "+":
    drop=KxN        the first N data requests send K body bytes, then close the connection (curl 18)
    reset=KxN       the first N data requests send K body bytes, then reset the connection (TCP RST: curl 56, as
                    on the office PC; on loopback the RST is sometimes not seen and the connection just hangs, which
                    the stall rule then cuts). Bytes still in the send buffer at the reset are lost in flight.
    stall=KxN       the first N data requests send K body bytes, then stop sending and keep the socket open
    throttle=BPS    every connection sends at most BPS bytes/s
    norange         ignore Range (always 200 with the whole file; Accept-Ranges: bytes is still advertised)
    rangeprobe      honour only the "bytes=0-0" probe; any other range gets the whole file (200)
    r416            any Range gets 416
    maxconn=K       at most K data responses of this spec at a time; more get 503 (a per-client connection limit)
    full200=A-B     data requests A..B (numbered from 1) ignore their Range: 200 with the whole file (a CDN cache miss)
    dropnth=N:K     data request N sends K body bytes, then closes the connection
    expire=S        (with /gh/) a signed token is valid for S seconds (checked when a request starts)
A "data request" is any GET of a file except the 1-byte probe (Range: bytes=0-0).
"""
import io
import os
import select
import socket
import socketserver
import struct
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()
STATS = {}
STATE = {}       # spec -> number of data requests seen (for drop/stall "first N")
TOKENS = {}      # token -> time issued
ACTIVE = {}      # spec -> data responses in progress (maxconn)
PROXY = {"fault": 0, "n": 0}
STOP = threading.Event()


def bump(key, n=1):
    with LOCK:
        STATS[key] = STATS.get(key, 0) + n


def parse_spec(spec):
    out = {}
    if spec == "plain":
        return out
    for item in spec.split("+"):
        k, _, v = item.partition("=")
        out[k] = v
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    root = "."

    def log_message(self, fmt, *args):
        with LOCK:
            with open(os.path.join(self.root, "requests.log"), "a") as f:
                f.write("%.3f %s %s\n" % (time.time(), self.headers.get("Range", "-"), fmt % args))

    def send_simple(self, code, body=b"", headers=()):
        self.send_response(code)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_CONNECT(self):
        """A forwarding CONNECT proxy (only to 127.0.0.1). The Nth tunnel (see /_proxyfault) closes right after the
        proxy's 200, before the server's answer: a TLS reset or timeout behind a corporate proxy looks like this."""
        host, _, port = self.path.rpartition(":")
        with LOCK:
            PROXY["n"] += 1
            broken = PROXY["n"] == PROXY["fault"]
        bump("proxy_tunnels")
        self.close_connection = True
        if host not in ("127.0.0.1", "localhost"):
            return self.send_simple(403, b"")
        self.send_response(200, "Connection established")
        self.end_headers()
        self.wfile.flush()
        if broken:
            bump("proxy_faults")
            return
        try:
            up = socket.create_connection(("127.0.0.1", int(port)))
        except OSError:
            return
        a, b = self.connection, up
        socks = [a, b]
        try:
            while True:
                r, _, _ = select.select(socks, [], [], 1.0)
                if STOP.is_set():
                    return
                for s in r:
                    data = s.recv(65536)
                    if not data:
                        return
                    (b if s is a else a).sendall(data)
        except OSError:
            return
        finally:
            up.close()

    def do_GET(self):
        parts = self.path.split("/")
        if self.path == "/_stats":
            with LOCK:
                body = "".join("%s=%s\n" % kv for kv in sorted(STATS.items())).encode()
            return self.send_simple(200, body)
        if self.path == "/_reset":
            with LOCK:
                STATS.clear(); STATE.clear(); TOKENS.clear(); PROXY["n"] = 0; PROXY["fault"] = 0
            return self.send_simple(200, b"ok\n")
        if len(parts) == 3 and parts[1] == "_proxyfault":
            with LOCK:
                PROXY["fault"] = int(parts[2]); PROXY["n"] = 0
            return self.send_simple(200, b"ok\n")
        # /r/<spec>/<file> and /gh/<spec>/<file>: redirects
        if len(parts) == 4 and parts[1] == "r":
            bump("redirects")
            return self.send_simple(302, b"", [("Location", "/m/%s/%s" % (parts[2], parts[3]))])
        if len(parts) == 4 and parts[1] == "gh":
            bump("redirects")
            tok = uuid.uuid4().hex[:12]
            with LOCK:
                TOKENS[tok] = time.time()
            return self.send_simple(302, b"", [("Location", "/s/%s/%s/%s" % (tok, parts[2], parts[3]))])
        token = None
        if len(parts) == 5 and parts[1] == "s":
            token, spec, name = parts[2], parts[3], parts[4]
        elif len(parts) == 4 and parts[1] == "m":
            spec, name = parts[2], parts[3]
        else:
            bump("status_404")
            return self.send_simple(404, b"not found\n")
        path = os.path.join(self.root, name)
        if "/" in name or not os.path.isfile(path):
            bump("status_404")
            return self.send_simple(404, b"not found\n")
        f = parse_spec(spec)
        size = os.path.getsize(path)
        rng = self.headers.get("Range")
        probe = rng == "bytes=0-0"
        if not probe:
            with LOCK:
                STATE[spec] = STATE.get(spec, 0) + 1
                nth = STATE[spec]
            bump("data_requests")
        else:
            nth = 0
            bump("probes")
        if token is not None:
            with LOCK:
                issued = TOKENS.get(token, 0)
            if time.time() - issued > float(f.get("expire", "1e9")):
                bump("status_403")
                return self.send_simple(403, b"signature expired\n")
        if not probe and "maxconn" in f:
            with LOCK:
                busy = ACTIVE.get(spec, 0) >= int(f["maxconn"])
                if not busy:
                    ACTIVE[spec] = ACTIVE.get(spec, 0) + 1
            if busy:
                bump("status_503")
                return self.send_simple(503, b"too many connections\n")
            try:
                return self.serve(f, spec, path, size, rng, probe, nth)
            finally:
                with LOCK:
                    ACTIVE[spec] -= 1
        return self.serve(f, spec, path, size, rng, probe, nth)

    def serve(self, f, spec, path, size, rng, probe, nth):
        ignore = False
        if "full200" in f and not probe:
            a, _, b = f["full200"].partition("-")
            ignore = int(a) <= nth <= int(b or a)
            if ignore and rng:
                bump("ranges_ignored")
        start, end = 0, size  # [start, end)
        partial = False
        if rng and "norange" not in f and not ("rangeprobe" in f and not probe) and not ignore:
            if "r416" in f:
                bump("status_416")
                return self.send_simple(416, b"", [("Content-Range", "bytes */%d" % size)])
            unit, _, r = rng.partition("=")
            a, _, b = r.partition("-")
            if unit != "bytes" or not a.isdigit() or int(a) >= size:
                bump("status_416")
                return self.send_simple(416, b"", [("Content-Range", "bytes */%d" % size)])
            start = int(a)
            end = min(int(b) + 1, size) if b.isdigit() else size
            partial = True
        if not probe and (not rng or rng.startswith("bytes=0-")):
            bump("from_zero")  # requests that ask for the file from its first byte (a start or restart)
        self.send_response(206 if partial else 200)
        bump("status_%d" % (206 if partial else 200))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start))
        if partial:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end - 1, size))
        self.end_headers()
        if self.command == "HEAD":
            return
        limit = None
        mode = None
        for key in ("drop", "reset", "stall"):
            if key in f:
                k, _, n = f[key].partition("x")
                if not probe and nth <= int(n or 1):
                    limit, mode = int(k), key
        if "dropnth" in f and not probe:
            n, _, k = f["dropnth"].partition(":")
            if nth == int(n):
                limit, mode = int(k), "drop"
        bps = int(f["throttle"]) if "throttle" in f else 0
        sent = 0
        t0 = time.time()
        with open(path, "rb") as fh:
            fh.seek(start)
            remaining = end - start
            while remaining > 0:
                n = min(65536, remaining)
                if limit is not None:
                    n = min(n, limit - sent)
                    if n <= 0:
                        break
                chunk = fh.read(n)
                try:
                    self.wfile.write(chunk)
                except OSError:
                    bump("client_aborts")
                    self.close_connection = True
                    return
                sent += len(chunk)
                bump("bytes", len(chunk))
                remaining -= len(chunk)
                if bps:
                    ahead = sent / bps - (time.time() - t0)
                    if ahead > 0:
                        time.sleep(ahead)
        if limit is not None and sent >= limit and remaining > 0:
            self.close_connection = True
            if mode == "drop":
                bump("drops")
                try:
                    self.wfile.flush()
                    self.connection.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
            elif mode == "reset":
                bump("resets")
                try:  # like a middlebox or a dying NAT entry ("Recv failure"), not a clean close
                    self.wfile.flush()
                    self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                    self.wfile.close(); self.rfile.close(); self.connection.close()  # all references: RST now
                    self.wfile = io.BytesIO()  # for the handler's final flush
                except OSError:
                    pass
            else:
                bump("stalls")
                try:
                    self.wfile.flush()
                except OSError:
                    pass
                deadline = time.time() + 300
                while time.time() < deadline and not STOP.is_set():
                    time.sleep(0.2)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):  # HTTPServer.server_bind does a reverse DNS lookup (getfqdn), slow or hanging in sandboxes
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def main():
    root, portfile = sys.argv[1], sys.argv[2]
    big_mb = int(sys.argv[3]) if len(sys.argv) > 3 else 80
    small_mb = int(sys.argv[4]) if len(sys.argv) > 4 else 3
    for name, mb in (("big.bin", big_mb), ("small.bin", small_mb)):
        p = os.path.join(root, name)
        if not os.path.exists(p):
            with open(p + ".tmp", "wb") as fh:
                for _ in range(mb):
                    fh.write(os.urandom(1 << 20))
            os.replace(p + ".tmp", p)
    Handler.root = root
    port = int(sys.argv[5]) if len(sys.argv) > 5 else 0
    srv = Server(("127.0.0.1", port), Handler)
    with open(portfile + ".tmp", "w") as fh:
        fh.write(str(srv.server_address[1]))
    os.replace(portfile + ".tmp", portfile)
    try:
        srv.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        STOP.set()


if __name__ == "__main__":
    main()
