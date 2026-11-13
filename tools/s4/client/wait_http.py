#!/usr/bin/env python3
"""Polls an https URL about ten times a second until it answers 200, and says when that happened and what it
answered before. One JSON line: {"ok": true, "t": <epoch seconds of the first 200>, "before": {"404": 31}}.

  wait_http.py URL CAFILE TIMEOUT_SECONDS
"""
import json
import ssl
import sys
import time
import urllib.error
import urllib.request

url, cafile, limit = sys.argv[1], sys.argv[2], float(sys.argv[3])
ctx = ssl.create_default_context(cafile=cafile)
before = {}
deadline = time.time() + limit
while time.time() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=2, context=ctx) as r:
            code = str(r.status)
    except urllib.error.HTTPError as e:
        code = str(e.code)
    except Exception as e:                      # refused, reset, TLS failure, timeout
        code = type(e).__name__
    if code == "200":
        print(json.dumps({"ok": True, "t": time.time(), "before": before}))
        sys.exit(0)
    before[code] = before.get(code, 0) + 1
    time.sleep(0.1)
print(json.dumps({"ok": False, "t": time.time(), "before": before}))
sys.exit(1)
