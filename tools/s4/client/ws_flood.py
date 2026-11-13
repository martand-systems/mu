#!/usr/bin/env python3
"""Opens COUNT WebSocket upgrades to the relay, one after another, and keeps every one open without ever speaking
SSH: connections that never log in, which a chisel server keeps for ever. Prints one JSON line with how many got
which HTTP status ({"101": 32, "429": 8}), holds the connections for HOLD seconds, then closes them.

  ws_flood.py HOST PORT CAFILE SERVER_NAME COUNT HOLD_SECONDS
"""
import base64
import json
import os
import socket
import ssl
import sys
import time

host, port, cafile, server_name = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
count, hold = int(sys.argv[5]), float(sys.argv[6])

ctx = ssl.create_default_context(cafile=cafile)
held, seen = [], {}
for _ in range(count):
    try:
        tls = ctx.wrap_socket(socket.create_connection((host, port), timeout=10), server_hostname=server_name)
        tls.sendall((
            f"GET / HTTP/1.1\r\nHost: {server_name}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {base64.b64encode(os.urandom(16)).decode()}\r\nSec-WebSocket-Version: 13\r\n"
            "Sec-WebSocket-Protocol: chisel-v3\r\n\r\n").encode())
        head = b""
        while b"\r\n" not in head and len(head) < 8192:
            chunk = tls.recv(4096)
            if not chunk:
                break
            head += chunk
        parts = head.split(b"\r\n")[0].decode("latin1").split(" ")
        code = parts[1] if len(parts) > 1 else "closed"
        held.append(tls)
    except Exception as e:                      # refused, reset, TLS failure, timeout
        code = type(e).__name__
    seen[code] = seen.get(code, 0) + 1
print(json.dumps(seen, sort_keys=True), flush=True)
time.sleep(hold)
for s in held:
    try:
        s.close()
    except OSError:
        pass
