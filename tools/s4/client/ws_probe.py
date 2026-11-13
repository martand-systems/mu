#!/usr/bin/env python3
"""A WebSocket upgrade by hand, the way chisel's client library (gorilla/websocket) does it: TLS without an ALPN
extension, then an HTTP/1.1 GET with Upgrade: websocket. Prints one JSON line with what came back.

  ws_probe.py HOST PORT CAFILE SUBPROTOCOL [SERVER_NAME]
"""
import base64
import json
import os
import socket
import ssl
import sys

host, port, cafile, subprotocol = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
server_name = sys.argv[5] if len(sys.argv) > 5 else host

ctx = ssl.create_default_context(cafile=cafile)     # verifies the chain and the host name; no ALPN is set
raw = socket.create_connection((host, port), timeout=10)
tls = ctx.wrap_socket(raw, server_hostname=server_name)
request = (
    f"GET / HTTP/1.1\r\nHost: {server_name}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
    f"Sec-WebSocket-Key: {base64.b64encode(os.urandom(16)).decode()}\r\nSec-WebSocket-Version: 13\r\n"
    f"Sec-WebSocket-Protocol: {subprotocol}\r\n\r\n"
)
tls.sendall(request.encode())
head = b""
while b"\r\n\r\n" not in head and len(head) < 8192:
    chunk = tls.recv(4096)
    if not chunk:
        break
    head += chunk
lines = head.split(b"\r\n\r\n")[0].decode("latin1").split("\r\n")
headers = {}
for line in lines[1:]:
    name, _, value = line.partition(":")
    headers[name.lower()] = value.strip()
print(json.dumps({
    "tls": tls.version(),
    "alpn_selected": tls.selected_alpn_protocol(),
    "status_line": lines[0],
    "upgrade": headers.get("upgrade", ""),
    "subprotocol": headers.get("sec-websocket-protocol", ""),
}))
tls.close()
