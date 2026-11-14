#!/usr/bin/env python3
"""A lying compute node, as the workstation's ssh sees it (MU_SSH=<this file>; tools/test-jobs/unit.py, section
untrusted-node). It answers `status` and `fetch` for any job id with what FAKE_PLAN says, so the workstation's own
checks are what decides. Built from the security review's exploit (docs/design/job-layer.md §23).

  FAKE_PLAN     JSON list of {"path": ..., "data": ..., "claim": N (optional: the size the result records)}
  FAKE_GLOBS    JSON list: the globs the node claims the job declared (result.json job.outputs), default ["**"]
  FAKE_REFUSE   a JSON object: printed as the answer to any verb, with exit FAKE_RC (default 69)
  MU_SRC        the src folder (for the sealed record)
"""
import hashlib
import io
import json
import os
import sys
import tarfile

sys.path.insert(0, os.environ["MU_SRC"])
from mu import durable  # noqa: E402

argv = sys.argv[1:]
if "-O" in argv:          # ssh -O exit (closing a master): nothing to do
    sys.exit(0)
req = argv[-1]
words = req.split()
verb = words[0]
args = dict(w.split("=", 1) for w in words[1:] if "=" in w)
if os.environ.get("FAKE_REFUSE"):
    print(os.environ["FAKE_REFUSE"])
    sys.exit(int(os.environ.get("FAKE_RC", "69")))
plan = json.loads(os.environ.get("FAKE_PLAN", "[]"))
globs = json.loads(os.environ.get("FAKE_GLOBS", '["**"]'))
jid = args.get("id")
if verb == "status":
    print(json.dumps({"ok": True, "job_id": jid, "state": "succeeded", "log_size": 0, "attempt": 1}))
    sys.exit(0)
if verb == "fetch":
    outs = [{"path": m["path"], "size": m.get("claim", len(m["data"].encode())),
             "sha256": hashlib.sha256(m["data"].encode()).hexdigest()} for m in plan]
    res = durable.seal({"schema": 1, "job_id": jid, "state": "succeeded", "outputs": outs, "outputs_missing": [],
                        "job": {"outputs": globs, "argv": ["x"]}})
    rdata = json.dumps(res).encode()
    out = sys.stdout.buffer
    tf = tarfile.open(fileobj=out, mode="w|", format=tarfile.PAX_FORMAT)
    ti = tarfile.TarInfo("result.json")
    ti.size = len(rdata)
    tf.addfile(ti, io.BytesIO(rdata))
    if args.get("what") != "result":
        for m in plan:
            b = m["data"].encode()
            ti = tarfile.TarInfo(m["path"])
            ti.size, ti.mode = len(b), 0o644
            tf.addfile(ti, io.BytesIO(b))
    tf.close()
    out.flush()
    sys.exit(0)
print(json.dumps({"ok": False, "error": "unknown-verb"}))
sys.exit(64)
