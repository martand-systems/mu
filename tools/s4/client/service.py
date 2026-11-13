#!/usr/bin/env python3
"""The "agent" on the compute container: a small HTTP service on loopback that the reverse tunnel exposes.

  GET /ping              -> "pong <nonce>"   (the nonce is made at start-up, so only this process can answer)
  GET /blob              -> the blob file    (?corrupt=1 flips one byte: the negative control for the hash check)
  PUT /upload            -> {"bytes": n, "sha256": "..."} of the body received (?corrupt=1 flips one byte first)

  service.py BLOB NONCE [PORT [BIND]]
"""
import hashlib
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BLOB = sys.argv[1]
NONCE = sys.argv[2]
PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 9000
BIND = sys.argv[4] if len(sys.argv) > 4 else "127.0.0.1"
CHUNK = 1 << 20


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/ping":
            return self._send(200, f"pong {NONCE}\n".encode())
        if url.path == "/blob":
            corrupt = parse_qs(url.query).get("corrupt") == ["1"]
            size = os.path.getsize(BLOB)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            sent = 0
            with open(BLOB, "rb") as f:
                while True:
                    chunk = f.read(CHUNK)
                    if not chunk:
                        break
                    if corrupt and sent <= size // 2 < sent + len(chunk):
                        i = size // 2 - sent
                        chunk = chunk[:i] + bytes([chunk[i] ^ 0x01]) + chunk[i + 1:]
                    self.wfile.write(chunk)
                    sent += len(chunk)
            return None
        return self._send(404, b"not found\n")

    def do_PUT(self):
        url = urlparse(self.path)
        if url.path != "/upload":
            return self._send(404, b"not found\n")
        corrupt = parse_qs(url.query).get("corrupt") == ["1"]
        left = int(self.headers.get("Content-Length", "0"))
        h = hashlib.sha256()
        n = 0
        while left > 0:
            chunk = self.rfile.read(min(CHUNK, left))
            if not chunk:
                break
            if corrupt and n == 0:
                chunk = bytes([chunk[0] ^ 0x01]) + chunk[1:]
            h.update(chunk)
            n += len(chunk)
            left -= len(chunk)
        return self._send(200, (json.dumps({"bytes": n, "sha256": h.hexdigest()}) + "\n").encode())


ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
