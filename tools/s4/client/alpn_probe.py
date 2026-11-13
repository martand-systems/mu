#!/usr/bin/env python3
"""What does a TLS client offer, and what does it send first?

A one-connection TLS server. It reads the raw ClientHello (before the TLS library sees it) and lists the ALPN
protocols the client offers, completes the handshake offering "h2, http/1.1" like Traefik's websecure entrypoint,
reads the first request head, answers 404 and exits. One JSON line on stdout:

  {"alpn_offered": [...], "alpn_selected": null|"h2"|..., "request_line": "...", "upgrade": "...", "subprotocol": "..."}

Used to show that the chisel client sends no ALPN extension and upgrades over HTTP/1.1 (curl is the positive
control: it offers h2 and http/1.1).
"""
import json
import socket
import ssl
import sys
import time

cert, key, port = sys.argv[1], sys.argv[2], int(sys.argv[3])


def client_hello_alpn(sock):
    """Peeks the first TLS record and returns the ALPN protocol names in the ClientHello ([] if no extension)."""
    data = b""
    for _ in range(200):
        data = sock.recv(16384, socket.MSG_PEEK)
        if len(data) >= 5 and len(data) >= 5 + int.from_bytes(data[3:5], "big"):
            break
        time.sleep(0.02)                # the rest of the record is still on its way
    if len(data) < 5 or data[0] != 0x16:
        raise ValueError("not a TLS handshake record")
    rec = data[5:5 + int.from_bytes(data[3:5], "big")]
    if rec[0] != 0x01:
        raise ValueError("not a ClientHello")
    p = 4 + 2 + 32                      # handshake header, client version, random
    p += 1 + rec[p]                     # session id
    p += 2 + int.from_bytes(rec[p:p + 2], "big")   # cipher suites
    p += 1 + rec[p]                     # compression methods
    if p + 2 > len(rec):
        return []
    end = p + 2 + int.from_bytes(rec[p:p + 2], "big")
    p += 2
    while p + 4 <= end:
        etype = int.from_bytes(rec[p:p + 2], "big")
        elen = int.from_bytes(rec[p + 2:p + 4], "big")
        body = rec[p + 4:p + 4 + elen]
        if etype == 16:                 # application_layer_protocol_negotiation
            names, q = [], 2
            while q < len(body):
                names.append(body[q + 1:q + 1 + body[q]].decode("ascii", "replace"))
                q += 1 + body[q]
            return names
        p += 4 + elen
    return []


ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(cert, key)
ctx.set_alpn_protocols(["h2", "http/1.1"])
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", port))
srv.listen(1)
print("ready", file=sys.stderr, flush=True)
srv.settimeout(30)
raw, _ = srv.accept()
raw.settimeout(10)
out = {"alpn_offered": client_hello_alpn(raw), "alpn_selected": None, "request_line": "", "upgrade": "", "subprotocol": ""}
try:
    tls = ctx.wrap_socket(raw, server_side=True)
    out["alpn_selected"] = tls.selected_alpn_protocol()
    head = b""
    while b"\r\n\r\n" not in head and len(head) < 8192:
        chunk = tls.recv(4096)
        if not chunk:
            break
        head += chunk
        if out["alpn_selected"] == "h2":
            break                       # an HTTP/2 preface, not a request head
    lines = head.split(b"\r\n")
    out["request_line"] = lines[0].decode("latin1")
    for line in lines[1:]:
        name, _, value = line.decode("latin1").partition(":")
        if name.lower() == "upgrade":
            out["upgrade"] = value.strip()
        if name.lower() == "sec-websocket-protocol":
            out["subprotocol"] = value.strip()
    if out["alpn_selected"] != "h2":
        tls.sendall(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
    tls.close()
except (ssl.SSLError, OSError) as e:
    out["error"] = str(e)
print(json.dumps(out), flush=True)
