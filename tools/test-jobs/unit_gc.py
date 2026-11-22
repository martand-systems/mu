"""Unit checks of release 2026.11.12 (docs/design/job-layer.md §24-§26), run by unit.py:
  gc-node    the node's clean-up of finished job folders against stubs of systemd: the selection (age, fetch, keep,
             references, unfinished jobs, the 24 h minimum), disk pressure (order, stop), the pruned folder and what
             status, log and fetch then say, the fetch record and its lock, crash safety (a crash before and after the
             swap), the verbs gc and keep and their authorisation, the runner's tick (hourly, under the start lock,
             cheap when nothing is due), hello, mu node settings
  gc-client  the workstation against a fake node: mu fetch of a cleaned-up job (exit 11), mu log's tail note,
             mu status's words, mu keep, mu compute gc
  sending    stuck "sending" records: reconciled to "unsent" once their submit is gone and the node has no such job;
             mu status's rows and next steps, mu forget, mu submit --resume without a snapshot
  tz         every human-facing time in local time (run under TZ=Asia/Kathmandu): mu pause, resume, status, queue,
             compute list; a UTC HH:MM anywhere in the words is a leak; --json keeps ISO UTC
Each check names the fault that must make it fail; tools/test-jobs/run.sh runs them again with each fault."""
import base64
import fcntl
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time

from mu import durable, proto

check = None   # set by unit.py
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DAY = 86400
MB = 1 << 20
PROJ = "p-0123456789ab"


def flag(f):
    return " (fault %s is on: this must FAIL)" % f if durable.fault(f) else ""


def ids(n):
    return "20261101-0000%02d-%s" % (n, ("gcjob" + "abcdefghijklmnopqrstuvwxyz"[n % 26]))[:6]


# ------------------------------------------------------------------------------------------------ the node
class Node:
    """A node folder with stubs of systemd; jobs made directly on disk."""

    def __init__(self, base, tag, stubs_text, **nodejson):
        from mu import node, nodegc
        self.N, self.G = node, nodegc
        self.d = os.path.join(base, tag)
        self.root = os.path.join(self.d, "srv")
        self.home = os.path.join(self.d, "home")
        for x in ("srv/jobs", "stub", "home/state", "bin"):
            os.makedirs(os.path.join(self.d, x))
        for name, text in stubs_text.items():
            with open(os.path.join(self.d, "bin", name), "w") as f:
                f.write(text)
            os.chmod(os.path.join(self.d, "bin", name), 0o755)
        with open(os.path.join(self.d, "owners"), "w") as f:
            f.write("avi-mac\n")
        self.env = {"MU_SYSTEMCTL": os.path.join(self.d, "bin", "systemctl"), "MU_SYSTEMD_RUN":
                    os.path.join(self.d, "bin", "systemd-run"), "MU_WSL_HOST_DRIVE": "/nonexistent",
                    "MU_HOME": self.home, "STUB_DIR": os.path.join(self.d, "stub"), "MU_NODE_ROOT": self.root,
                    "MU_NODE_OWNERS": os.path.join(self.d, "owners")}
        os.environ.update(self.env)
        node.ROOT = self.root
        nodegc.LAST.update(at=0.0, again_at=0.0)
        durable.write_record(os.path.join(self.root, "node.json"), dict({"name": "n", "min_free_gb": 0}, **nodejson))

    def job(self, jid, ended_days=None, fetched_days=None, key="avi-mac", state="running", accepted_days=None,
            work_mb=1, log_kb=200, statex=None):
        """A job: finished ENDED_DAYS ago (None: unfinished in STATE), its outputs fetched FETCHED_DAYS ago."""
        now = time.time()
        d = self.N.job_dir(jid)
        os.makedirs(os.path.join(d, "work", "results"))
        acc = now - (accepted_days if accepted_days is not None else (ended_days or 0) + 1) * DAY
        durable.write_record(os.path.join(d, "job.json"), {
            "schema": 1, "job_id": jid, "project_id": PROJ, "project": "p", "mu_version": "v1", "argv": ["true"],
            "cwd": "", "outputs": ["results/*.json"], "limits": {}, "rerun": True, "accepted_ns": int(acc * 1e9),
            "accepted_at": proto.iso(acc), "key": key, "name": "job " + jid[-6:]})
        st = dict({"state": state if ended_days is None else "running", "attempt": 1, "unit": "mu-job-%s.service" % jid},
                  **(statex or {}))
        durable.write_record(os.path.join(d, "state.json"), st)
        with open(os.path.join(d, "work", "results", "out.json"), "w") as f:
            f.write('{"ok": 1}\n')
        with open(os.path.join(d, "work", "big.bin"), "wb") as f:
            f.write(os.urandom(work_mb * MB))
        with open(os.path.join(d, "index"), "wb") as f:
            f.write(b"DIRC" + b"\0" * 4000)
        with open(os.path.join(d, "metrics.jsonl"), "w") as f:
            f.write('{"t": 1}\n' * 2000)
        with open(os.path.join(d, "usage.json"), "w") as f:
            f.write('{"cpu_s": 1}')
        with open(os.path.join(d, "events.log"), "w") as f:
            f.write("2026-11-01T00:00:00Z accepted\n")
        with open(os.path.join(d, "log"), "wb") as f:
            for i in range(log_kb * 1024 // 64):
                f.write(("line %06d of the job's log, padded to sixty-four bytes " % i).ljust(58, ".").encode()[:58] +
                        b".....\n")
        if ended_days is not None:
            durable.write_record(os.path.join(d, "result.json"), {
                "schema": 1, "job_id": jid, "state": "succeeded", "exit_code": 0, "attempt": 1,
                "ended_at": proto.iso(now - ended_days * DAY), "started_at": proto.iso(now - ended_days * DAY - 60),
                "outputs": [{"path": "results/out.json", "size": 10, "sha256": "x"}], "outputs_missing": [],
                "usage": {"cpu_s": 1.0}, "run_s": 60.0})
        if fetched_days is not None:
            durable.write_record(os.path.join(d, "fetched.json"), {
                "schema": 1, "job_id": jid, "first_at": proto.iso(now - fetched_days * DAY),
                "last_at": proto.iso(now - fetched_days * DAY), "count": 1, "keys": [key]})
        return d

    def serve(self, key, verb, *args, data=None):
        """`mu node serve` in a process of its own, as the gate runs it. -> (rc, stdout bytes)."""
        env = dict(os.environ, **self.env)
        r = subprocess.run([sys.executable, "-I", "-S", os.path.join(REPO, "src", "mu", "cli.py"), "node", "serve",
                            "--key=" + key, verb] + list(args), input=data, env=env, capture_output=True, timeout=120)
        return r.returncode, r.stdout

    def js(self, key, verb, *args):
        rc, out = self.serve(key, verb, *args)
        try:
            return rc, json.loads(out.decode().strip().splitlines()[-1])
        except (ValueError, IndexError):
            return rc, {"raw": out[-300:]}


def left(root):
    """What is left in /srv/mu/prune/ (nothing when the folder was never made)."""
    try:
        return os.listdir(os.path.join(root, "prune"))
    except OSError:
        return []


def honest(d):
    """A job folder whose record does not lie: with pruned.json (valid) it may lack its outputs; without one, every
    output its result names is in work/."""
    res = durable.read_record(os.path.join(d, "result.json"))
    if not res:
        return False
    if os.path.exists(os.path.join(d, "pruned.json")):
        return durable.read_record(os.path.join(d, "pruned.json")) is not None
    return all(os.path.isfile(os.path.join(d, "work", o["path"])) for o in res.get("outputs") or []) and \
        os.path.isdir(os.path.join(d, "work")) and os.path.exists(os.path.join(d, "index"))


def t_gc_node(base, stubs):
    from mu import nodegc
    # ---- A. the selection, by age, fetch, keep, references, unfinished
    n = Node(base, "select", stubs)
    F8, F3, U31, U10, K, R, Q, RUN = (ids(i) for i in range(1, 9))
    n.job(F8, ended_days=9, fetched_days=8)
    n.job(F3, ended_days=20, fetched_days=3)
    n.job(U31, ended_days=31)
    n.job(U10, ended_days=10)
    n.job(K, ended_days=40)
    durable.write_record(os.path.join(n.N.job_dir(K), "keep.json"), {"schema": 1, "job_id": K, "by": "avi-mac",
                                                                     "at": proto.iso()})
    n.job(R, ended_days=40)
    n.job(Q, state="held", accepted_days=40)
    n.job(RUN, state="running", statex={"grant": {"class": "borrowing", "borrowed": [{"from": R}]}})
    out = nodegc.run("test", dry=True)
    got = sorted(x["job_id"] for x in out["pruned"])
    check("due by age: the job fetched 8 days ago and the one ended 31 days ago, never fetched; not the one fetched 3 "
          "days ago, not the one ended 10 days ago" + flag("gc-fetch-ignored"),
          F8 in got and U31 in got and F3 not in got and U10 not in got, got)
    check("never a job without a result (a job held for 40 days, a running one)" + flag("gc-ignores-unfinished"),
          Q not in got and RUN not in got, got)
    check("never a kept job (mu keep)" + flag("gc-ignores-keep"), K not in got, got)
    check("never a job a running job depends on (named in its state: a borrower's lender)" + flag("gc-ignores-refs"),
          R not in got, got)
    check("a dry run changes nothing", all(os.path.isdir(os.path.join(n.N.job_dir(x), "work")) for x in (F8, U31)) and
          not os.path.exists(os.path.join(n.N.job_dir(F8), "pruned.json")))

    # ---- B. the prune itself, and what status, log and fetch say then
    res_before = open(os.path.join(n.N.job_dir(F8), "result.json"), "rb").read()
    out = nodegc.run("test")
    d = n.N.job_dir(F8)
    pr = durable.read_record(os.path.join(d, "pruned.json"))
    names = sorted(os.listdir(d))
    check("pruned: work/, the index and metrics.jsonl gone; job.json, state.json, result.json (unchanged), events.log, "
          "usage.json, fetched.json kept; pruned.json sealed with when, why, by and the bytes freed",
          not {"work", "index", "metrics.jsonl"} & set(names) and {"job.json", "state.json", "result.json",
                                                                    "events.log", "usage.json", "fetched.json",
                                                                    "pruned.json", "log"} <= set(names) and
          open(os.path.join(d, "result.json"), "rb").read() == res_before and pr and pr.get("bytes_freed", 0) > MB // 2
          and pr.get("why", "").startswith("fetched 8 days ago") and pr.get("by") == "test", (names, pr))
    log = open(os.path.join(d, "log"), "rb").read()
    check("the log keeps its last 64 kB, from a line's start, after a line saying it is a tail",
          len(log) <= 65 * 1024 and log.startswith(b"== \xce\xbc: the job's folder was cleaned up") and
          log.split(b"\n")[1].startswith(b"line ") and log.endswith(b".....\n"), log[:200])
    rc, s = n.js("avi-mac", "status", "id=" + F8)
    check("status of a pruned job works (exit 0) and says pruned (at, why, bytes_freed, fetched)",
          rc == 0 and s.get("state") == "succeeded" and (s.get("pruned") or {}).get("at") and
          (s.get("fetched") or {}).get("count") == 1, s)
    rc, raw = n.serve("avi-mac", "log", "id=" + F8, "offset=0")
    check("log of a pruned job sends the kept tail", rc == 0 and raw == log, raw[:120])
    rc, f = n.js("avi-mac", "fetch", "id=" + F8, "what=result")
    check("fetch of a pruned job is refused: exit 66, error pruned, with when and why" + flag("fetch-pruned-served"),
          rc == 66 and f.get("error") == "pruned" and (f.get("pruned") or {}).get("at"), (rc, f))
    audit = open(os.path.join(n.root, "audit.log"), encoding="utf-8").read()
    check("each prune is in the journal and the audit log", "gc: pruned id=%s" % F8 in audit and
          "gc: pruned id=%s" % U31 in audit, audit[-600:])
    time.sleep(0.5)
    walked = []
    real_tb = nodegc.tree_bytes
    nodegc.tree_bytes = lambda x: walked.append(x) or real_tb(x)
    out2 = nodegc.run("test")
    nodegc.tree_bytes = real_tb
    check("a second clean-up with nothing due prunes nothing and walks no folder (sizes cached in gc.json)",
          out2["pruned"] == [] and walked == [], (out2["pruned"], walked))
    for _ in range(50):
        if not left(n.root):
            break
        time.sleep(0.1)
    check("the old folders are deleted (a detached rm)", left(n.root) == [],
          left(n.root))

    # ---- C. disk pressure: fetched first (oldest fetch first), then unfetched over 7 days (oldest end first)
    n = Node(base, "pressure", stubs, min_free_gb=20)
    P1, P2, P3, P4, P5, Y = (ids(i) for i in range(11, 17))
    n.job(P1, ended_days=3, fetched_days=2)
    n.job(P2, ended_days=6, fetched_days=5)
    n.job(P3, ended_days=8)
    n.job(P4, ended_days=9)
    n.job(P5, ended_days=3)
    n.job(Y, ended_days=0.08, fetched_days=0.04)
    one = nodegc.tree_bytes(n.N.job_dir(P1))
    line = 20 + 10
    real_space = nodegc.space

    def fake_space(need):
        def sp(node, cache):
            return line - need / float(1 << 30), {"free_gb": int(line - need / float(1 << 30)), "where": n.root,
                                                  "reclaim_inside_bytes": 0, "min_free_gb": 20}
        return sp
    nodegc.space = fake_space(2.5 * one)
    out = nodegc.run("test")
    got = [x["job_id"] for x in out["pruned"]]
    check("disk pressure: the oldest fetch first (5 days, then 2), then the oldest unfetched end over 7 days (9 days); "
          "it stops once the free space is back over min_free_gb + 10 GB" + flag("gc-pressure-order") +
          flag("gc-pressure-all"), got == [P2, P1, P4] and out["pressure"] is True, got)
    check("each pressure prune says why", all(x["why"].startswith("disk pressure") for x in out["pruned"]),
          [x["why"] for x in out["pruned"]])
    nodegc.space = fake_space(1000 * one)
    out = nodegc.run("test")
    got = [x["job_id"] for x in out["pruned"]]
    check("under pressure that pruning cannot relieve: every eligible job, never one that ended under 24 h ago, never "
          "an unfetched one under 7 days" + flag("gc-no-min-age"), got == [P3] and Y not in got and P5 not in got, got)
    nodegc.space = real_space

    # ---- D. crash safety: a crash before or after the swap leaves the whole folder or the pruned one
    for when in ("before", "after"):
        n = Node(base, "crash-" + when, stubs)
        C = ids(20)
        dc = n.job(C, ended_days=40)
        env = dict(os.environ, **n.env)
        env["MU_NODE_FAULT"] = "prune-crash-%s-swap" % when
        code = ("import sys; sys.path.insert(0, %r); from mu import node, nodegc; node.ROOT = %r; nodegc.run('test')"
                % (os.path.join(REPO, "src"), n.root))
        r = subprocess.run([sys.executable, "-I", "-S", "-c", code], env=env, capture_output=True, timeout=120)
        ok_state = honest(dc) and (os.path.exists(os.path.join(dc, "pruned.json")) == (when == "after"))
        check("a crash %s the swap (the process killed, exit %d): the job's folder is %s, and its record does not lie"
              % (when, r.returncode, "whole" if when == "before" else "the pruned one") + flag("prune-in-place"),
              r.returncode == 9 and ok_state, (r.returncode, r.stderr[-300:], sorted(os.listdir(dc))))
        out = nodegc.run("test")
        for _ in range(50):
            if not left(n.root):
                break
            time.sleep(0.1)
        check("  the next clean-up removes what the crash left in prune/ and %s" % (
            "prunes the job" if when == "before" else "leaves the pruned job as it is"),
            left(n.root) == [] and honest(dc) and
            os.path.exists(os.path.join(dc, "pruned.json")), (left(n.root), out["pruned"]))

    # ---- E. the fetch record and the fetch lock
    n = Node(base, "fetch", stubs)
    E = ids(21)
    de = n.job(E, ended_days=40)
    rc, raw = n.serve("avi-mac", "fetch", "id=" + E, "what=result")
    check("fetch what=result does not count as a fetch", rc == 0 and not os.path.exists(os.path.join(de, "fetched.json")),
          rc)
    rc, raw = n.serve("avi-mac", "fetch", "id=" + E)
    f = durable.read_record(os.path.join(de, "fetched.json")) or {}
    check("a whole fetch is recorded on the node (fetched.json: count, last_at, the key)" + flag("fetch-not-recorded"),
          rc == 0 and f.get("count") == 1 and f.get("keys") == ["avi-mac"] and proto.epoch_of(f.get("last_at")), (rc, f))
    durable.write_record(os.path.join(de, "fetched.json"), dict(f, last_at=proto.iso(time.time() - 9 * DAY)))
    lk = os.open(os.path.join(de, "job.json"), os.O_RDONLY)
    fcntl.flock(lk, fcntl.LOCK_SH)    # a fetch in progress holds this
    out = nodegc.run("test")
    fcntl.flock(lk, fcntl.LOCK_UN)
    os.close(lk)
    check("a job whose outputs are being fetched (a shared lock on its job.json) is skipped" +
          flag("gc-ignores-fetch-lock"), out["pruned"] == [] and any("fetched" in x["why"] for x in out["skipped"]) and
          os.path.isdir(os.path.join(de, "work")), (out["pruned"], out["skipped"]))

    # ---- F. keep, G. gc: who may
    n = Node(base, "verbs", stubs)
    MINE, THEIRS = ids(22), ids(23)
    dm = n.job(MINE, ended_days=40, key="ci-ws")
    n.job(THEIRS, ended_days=40, key="ci-ws")
    rc, k = n.js("ci-ws", "keep", "id=" + MINE, "from=" + PROJ)
    check("keep: the job's own workstation keeps it (keep.json)", rc == 0 and k.get("changed") and
          os.path.exists(os.path.join(dm, "keep.json")) and (k.get("kept") or {}).get("by") == "ci-ws", (rc, k))
    rc, k = n.js("ci-other", "keep", "id=" + THEIRS, "from=" + PROJ)
    check("keep: another workstation's key may not (exit 77, not-allowed)" + flag("keep-any-key"),
          rc == 77 and k.get("error") == "not-allowed", (rc, k))
    rc, k = n.js("avi-mac", "keep", "id=" + THEIRS, "from=other-0123456789ab")
    check("keep: an owner key on another project's job without --others: other-project (exit 64)" + flag("keep-any-key"),
          rc == 64 and k.get("error") == "other-project", (rc, k))
    rc, k = n.js("avi-mac", "keep", "id=" + THEIRS, "from=other-0123456789ab", "others=1")
    check("keep: an owner key with --others may", rc == 0 and k.get("changed"), (rc, k))
    rc, k = n.js("avi-mac", "keep", "id=" + THEIRS, "off=1", "others=1")
    check("keep --off ends it", rc == 0 and k.get("changed") and not os.path.exists(
        os.path.join(n.N.job_dir(THEIRS), "keep.json")), (rc, k))
    rc, g = n.js("ci-ws", "gc")
    check("gc: refused to a key that is not an owner's (exit 77)" + flag("gc-any-key"),
          rc == 77 and g.get("error") == "not-allowed" and os.path.isdir(os.path.join(n.N.job_dir(THEIRS), "work")),
          (rc, g))
    rc, g = n.js("avi-mac", "gc", "dry=1")
    check("gc dry=1 (owner): lists the job it would prune, changes nothing",
          rc == 0 and [x["job_id"] for x in g.get("pruned") or []] == [THEIRS] and g.get("dry_run") and
          os.path.isdir(os.path.join(n.N.job_dir(THEIRS), "work")), (rc, g))
    rc, g = n.js("avi-mac", "gc")
    check("gc (owner): prunes it, not the kept one", rc == 0 and [x["job_id"] for x in g.get("pruned") or []] ==
          [THEIRS] and os.path.isdir(os.path.join(dm, "work")), (rc, g))
    rc, k = n.js("ci-ws", "keep", "id=" + THEIRS, "from=" + PROJ)
    check("keep of a pruned job: nothing to keep (changed false, says when)", rc == 0 and k.get("changed") is False and
          "already cleaned up" in (k.get("note") or ""), (rc, k))

    # ---- H. the runner's tick: at most once an hour, never without the start lock
    n = Node(base, "tick", stubs)
    T1, T2 = ids(24), ids(25)
    n.job(T1, ended_days=40)
    first = nodegc.tick()
    n.job(T2, ended_days=40)
    second = nodegc.tick()
    check("the runner's tick runs at most once an hour (the second, a moment later, does nothing)" +
          flag("gc-hourly-ignored"), first and [x["job_id"] for x in first["pruned"]] == [T1] and second is None and
          os.path.isdir(os.path.join(n.N.job_dir(T2), "work")), (first and first["pruned"], second and second["pruned"]))
    n = Node(base, "startlock", stubs)
    T3 = ids(26)
    n.job(T3, ended_days=40)
    sl = n.N.start_lock_path()
    os.makedirs(sl)
    ppid = os.getppid()
    with open(os.path.join(sl, "pid"), "w") as f:   # an update holds the start lock (a live process of this boot)
        f.write("%d\n%s\n%s\n" % (ppid, n.N.boot_id(), n.N.proc_start(ppid)))
    out = nodegc.tick()
    shutil.rmtree(sl)
    check("the clean-up waits while an update holds the start lock (no prune between a version's switch)" +
          flag("gc-no-start-lock"), out and not out.get("ok") and os.path.isdir(os.path.join(n.N.job_dir(T3), "work")),
          out)

    # ---- I. hello, mu node settings
    rc, h = n.js("avi-mac", "hello", "protocol=1")
    check("hello names the feature gc and carries the clean-up's summary (settings, sizes, last run)",
          "gc" in (h.get("features") or []) and isinstance(h.get("gc"), dict) and
          h["gc"]["settings"]["after_fetch_days"] == 7, h.get("gc"))
    env = dict(os.environ, **n.env)
    cli = [sys.executable, "-I", "-S", os.path.join(REPO, "src", "mu", "cli.py"), "node", "settings"]
    r = subprocess.run(cli + ["gc.after_fetch_days=3", "gc.enabled=false", "--json"], env=env, capture_output=True)
    o = json.loads(r.stdout or b"{}")
    r2 = subprocess.run(cli + ["gc.after_fetch_days=0"], env=env, capture_output=True)
    r3 = subprocess.run(cli + ["gc.bogus=1"], env=env, capture_output=True)
    check("mu node settings: gc.after_fetch_days=3 gc.enabled=false are written to node.json; 0 and an unknown key "
          "are refused (exit 2)", r.returncode == 0 and o.get("gc", {}).get("after_fetch_days") == 3 and
          nodegc.settings()["enabled"] is False and r2.returncode == 2 and r3.returncode == 2,
          (r.stdout[-300:], r2.stderr[-200:], r3.stderr[-200:]))


# ------------------------------------------------------------------------------------------------ the workstation
class WS:
    """A workstation (throwaway MU_HOME and HOME) paired with the fake node, and a project."""

    def __init__(self, base, tag, tz=None):
        self.d = os.path.join(base, tag)
        os.makedirs(self.d)
        self.answers = os.path.join(self.d, "answers.json")
        self.log = os.path.join(self.d, "requests.log")
        self.env = dict(os.environ, MU_HOME=os.path.join(self.d, "mu"), MU_SRC=os.path.join(REPO, "src"),
                        MU_SSH=os.path.join(HERE, "fake-node-ssh.py"), MU_CHISEL="/usr/bin/true", HOME=self.d,
                        FAKE_ANSWERS=self.answers, FAKE_LOG=self.log, MU_POLL_S="0.2")
        for k in ("FAKE_PLAN", "FAKE_GLOBS", "FAKE_REFUSE", "MU_PROJECT", "MU_VERSION", "MU_NODE_ROOT"):
            self.env.pop(k, None)
        if tz:
            self.env["TZ"] = tz
        self.cli = [sys.executable, "-I", "-S", os.path.join(REPO, "src", "mu", "cli.py")]
        blob = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + b"\x01" * 32
        hk = "ssh-ed25519 " + base64.b64encode(blob).decode()
        self.set({})
        r = subprocess.run(self.cli + ["compute", "add", "office-pc", "--host-key", hk, "--relay", "https://relay.example",
                                       "--relay-fingerprint", "A" * 43 + "=", "--relay-user", "avi-mac", "--secret-store",
                                       "file", "--relay-password-stdin", "--json"], input=b"pw-pw-pw-pw-pw-pw-pw-pw-pw\n",
                           env=self.env, capture_output=True)
        assert r.returncode == 0, r.stdout + r.stderr
        self.proj = os.path.join(self.d, "proj")
        os.makedirs(self.proj)
        g = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(g + ["init", "-q"], cwd=self.proj, check=True)
        with open(os.path.join(self.proj, "mu.toml"), "w") as f:
            f.write('[mu]\nversion = "v2026.11.6"\n[compute]\noutputs = ["results/*.json"]\n')
        subprocess.run(g + ["add", "mu.toml"], cwd=self.proj, check=True)
        subprocess.run(g + ["commit", "-qm", "init"], cwd=self.proj, check=True)
        self.head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.proj, capture_output=True,
                                   text=True).stdout.strip()

    def set(self, table):
        with open(self.answers, "w") as f:
            json.dump(table, f)
        open(self.log, "w").close()

    def requests(self):
        return [ln.strip() for ln in open(self.log) if ln.strip()]

    def run(self, *args, cwd=None):
        r = subprocess.run(self.cli + list(args), cwd=cwd or self.proj, env=self.env, capture_output=True, timeout=120)
        out = r.stdout.decode("utf-8", "replace")
        o = {}
        if "--json" in args:
            try:
                o = json.loads(out.strip().splitlines()[-1])
            except (ValueError, IndexError):
                pass
        return r.returncode, out + r.stderr.decode("utf-8", "replace"), o

    def record(self, jid, **kw):
        rec = dict({"schema": 1, "job_id": jid, "node": "office-pc", "state": "sending",
                    "spec": {"job_id": jid, "name": "job " + jid[-6:], "outputs": ["results/*.json"],
                             "mu_version": "v2026.11.6", "project_id": "x", "submitted": {"at": "2026-11-15T09:00:00Z"}}},
                   **kw)
        os.makedirs(os.path.join(self.proj, ".mu", "jobs"), exist_ok=True)
        path = os.path.join(self.proj, ".mu", "jobs", jid + ".json")
        durable.write_record(path, rec)
        return path

    def snapref(self, jid):
        subprocess.run(["git", "update-ref", "refs/mu/snap/" + jid, self.head], cwd=self.proj, check=True)


def hello(*feats):
    return {"ok": True, "protocol": 1, "protocol_min": 1, "node": "office-pc", "key": "avi-mac",
            "mu": {"default": "v2026.11.12", "installed": ["v2026.11.12"]}, "features": ["sched", "queue-control"] +
            list(feats), "capacity": {"cores": 20, "mem_bytes": 20 << 30}, "queue": {"running": 0, "queued": 0},
            "disk": {"free_gb": 80, "min_free_gb": 20}, "limits": {"memory": True}}


def t_gc_client(base):
    w = WS(base, "gcc")
    J = "20261101-100000-prunea"
    os.makedirs(os.path.join(w.proj, ".mu", "jobs"), exist_ok=True)
    w.record(J, state="succeeded", accepted="2026-11-01T10:00:01Z")
    pr = {"at": "2026-11-20T10:00:00Z", "why": "ended 31 days ago, never fetched (after 30 days)", "by": "runner",
          "bytes_freed": 5 << 30, "fetched": None, "log": {"size_before": 900000, "kept": 65000, "tail": True}}
    st = {"ok": True, "job_id": J, "state": "succeeded", "log_size": 65100, "attempt": 1, "pruned": pr,
          "fetched": None, "kept": None}
    w.set({"status:" + J: st})
    rc, out, o = w.run("fetch", J, "--json")
    check("mu fetch of a cleaned-up job: exit 11, error pruned, says it was never fetched and when it was removed" +
          flag("fetch-ignores-pruned"), rc == 11 and o.get("error") == "pruned" and "never been fetched" in
          o.get("message", "") and proto.local_when(pr["at"]) in o.get("message", "") and (o.get("pruned") or {}).get(
              "at") == pr["at"], (rc, out[-400:]))
    rc, out, o = w.run("fetch", J)
    check("  in words too (exit 11, stderr)", rc == 11 and "were removed on office-pc on" in out, out[-300:])
    w.set({"status:" + J: dict(st, pruned=None), "fetch:" + J: {"_rc": 66, "ok": False, "error": "pruned",
                                                               "message": "job was cleaned up", "pruned": pr}})
    rc, out, o = w.run("fetch", J, "--json")
    check("the node's own refusal (an older status without `pruned`): exit 11 too", rc == 11 and
          o.get("error") == "pruned", (rc, out[-300:]))
    w.set({"status:" + J: st, "log:" + J: {"_raw": "== μ: the job's folder was cleaned up ==\nlast line\n"}})
    rc, out, o = w.run("log", J)
    check("mu log of a cleaned-up job shows the kept tail and says it is a tail (stderr)",
          rc == 0 and "last line" in out and "this is the last" in out and "of its log" in out, out[-300:])
    rc, out, o = w.run("log", J, "--json")
    check("  --json: tail true, pruned", rc == 0 and o.get("tail") is True and o.get("pruned", {}).get("at"), o)
    rc, out, o = w.run("status", J)
    check("mu status of a cleaned-up job: when (local time), why, whether it was fetched",
          rc == 0 and "cleaned up on office-pc on %s" % proto.local_when(pr["at"]) in out and "never been fetched" in out,
          out[-400:])
    w.set({"hello": hello("gc"), "keep:" + J: {"ok": True, "exit": 0, "job_id": J, "changed": True,
                                               "kept": {"by": "avi-mac", "at": "2026-11-23T10:00:00Z"}, "pruned": None}})
    rc, out, o = w.run("keep", J)
    reqs = w.requests()
    check("mu keep JOB: sends keep with this project (from=)", rc == 0 and "kept" in out and
          any(x.startswith("keep id=%s from=" % J) for x in reqs), (out, reqs))
    w.set({"hello": hello()})
    rc, out, o = w.run("keep", J, "--json")
    check("mu keep against a node without the clean-up: exit 6 node-too-old", rc == 6 and o.get("error") ==
          "node-too-old", (rc, o))
    w.set({"hello": hello("gc"), "gc": {"ok": True, "dry_run": True, "pruned": [{"job_id": J, "why": "x", "bytes":
                                                                                 1 << 30}], "skipped": [],
                                        "space": {"free_gb": 38, "where": "C:"}, "line_gb": 30, "free_gb_effective": 38,
                                        "not_candidates": {"unfinished": 1}}})
    rc, out, o = w.run("compute", "gc", "office-pc", "--dry-run")
    check("mu compute gc --dry-run: sends gc dry=1, lists what would be pruned", rc == 0 and "would prune 1 job" in out
          and J in out and "gc dry=1" in w.requests(), (out, w.requests()))


def t_sending(base):
    w = WS(base, "sending")
    S1, S2, S3, S4, S5, S6, S7 = ("20261115-0900%02d-send0%d" % (i, i) for i in range(1, 8))
    w.record(S7)
    w.record(S1)
    w.snapref(S1)
    w.record(S2, spec={"job_id": S2, "submitted": {"at": proto.iso()}})
    w.record(S3)
    w.snapref(S3)
    w.record(S4)
    w.record(S5)
    lock = os.path.join(w.proj, ".mu", "jobs", ".%s.lock" % S3)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)   # a submit still pushing S3
    old = time.time() - 3600
    for j in (S1, S3, S4, S5):
        os.utime(os.path.join(w.proj, ".mu", "jobs", j + ".json"), (old, old))
    nosuch = {"_rc": 66, "ok": False, "error": "no-such-job", "message": "the node has no job"}
    w.set({"list": {"ok": True, "jobs": [{"job_id": S7, "state": "succeeded", "attempt": 1}]}, "status:" + S1: nosuch, "status:" + S2: nosuch, "status:" + S3: nosuch,
           "status:" + S5: nosuch, "status:" + S4: {"ok": True, "job_id": S4, "state": "queued", "position": 1}})
    rc, out, o = w.run("status", "--json")
    rows = {j["job_id"]: j for j in o.get("jobs") or []}
    recs = {j: durable.read_record(os.path.join(w.proj, ".mu", "jobs", j + ".json")) or {} for j in (S1, S2, S3, S4, S5)}
    check("mu status: a stale \"sending\" record (submit gone, node has no such job) becomes unsent, on disk too" +
          flag("no-unsent"), rows.get(S1, {}).get("state") == "unsent" and recs[S1].get("state") == "unsent" and
          rows.get(S5, {}).get("state") == "unsent" and sorted(o.get("reconciled") or []) == [S1, S5],
          (rows.get(S1), recs[S1].get("state"), o.get("reconciled")))
    check("  the next step: --resume when the snapshot is there, else only mu forget",
          "mu submit --resume %s" % S1 in rows.get(S1, {}).get("next", "") and
          "--resume" not in rows.get(S5, {}).get("next", "--resume") and "mu forget %s" % S5 in rows.get(S5, {}).get(
              "next", ""), (rows.get(S1), rows.get(S5)))
    check("  a fresh record (under 10 min) and one whose submit still holds its lock stay sending, the node not asked",
          rows.get(S2, {}).get("state") == "sending" and rows.get(S3, {}).get("state") == "sending" and
          recs[S2].get("state") == "sending" and recs[S3].get("state") == "sending" and
          not any(S2 in r or S3 in r for r in w.requests()), (rows.get(S2), rows.get(S3), w.requests()))
    s7 = durable.read_record(os.path.join(w.proj, ".mu", "jobs", S7 + ".json")) or {}
    check("  a record the node knows (the submit's answer was lost) takes the node's state, asked or in its list",
          rows.get(S4, {}).get("state") == "queued" and recs[S4].get("state") == "queued" and
          s7.get("state") == "succeeded", (rows.get(S4), recs[S4], s7.get("state")))
    check("  only list and status were asked of the node (nothing deleted there)",
          all(r.split()[0] in ("list", "status") for r in w.requests()), w.requests())
    rc, out, o = w.run("status", "--json")
    rc2, text, _ = w.run("status")
    check("shown once: a second mu status reconciles nothing new, and still lists each unsent job with its next step",
          o.get("reconciled") == [] and "not sent · mu submit --resume %s" % S1 in text and
          "never accepted" not in text, (o.get("reconciled"), text))
    rc, out, o = w.run("status", S1, "--json")
    check("mu status JOB of an unsent job: exit 9, error not-sent, the next step", rc == 9 and o.get("error") ==
          "not-sent" and "--resume" in o.get("next", ""), (rc, o))
    rc, out, o = w.run("submit", "--resume", S5, "--json")
    check("mu submit --resume of a job whose snapshot is gone: exit 2 no-snapshot, says mu forget", rc == 2 and
          o.get("error") == "no-snapshot" and "mu forget" in o.get("next", ""), (rc, o))
    w.set({"status:" + S1: nosuch, "status:" + S4: {"ok": True, "job_id": S4, "state": "queued"},
           "status:" + S6: {"ok": True, "job_id": S6, "state": "succeeded"}})
    w.record(S6, state="succeeded")
    rc, out, o = w.run("forget", S1, "--json")
    gone = not os.path.exists(os.path.join(w.proj, ".mu", "jobs", S1 + ".json"))
    ref = subprocess.run(["git", "rev-parse", "--verify", "-q", "refs/mu/snap/" + S1], cwd=w.proj).returncode
    check("mu forget of an unsent job: the record and its snapshot ref are gone; the node was only asked",
          rc == 0 and gone and ref != 0 and w.requests() == ["status id=%s" % S1], (rc, o, w.requests()))
    rc, out, o = w.run("forget", S4, "--json")
    check("mu forget of a job the node still has unfinished: refused (exit 2, node-has-job), the record kept" +
          flag("forget-unfinished"), rc == 2 and o.get("error") == "node-has-job" and
          os.path.exists(os.path.join(w.proj, ".mu", "jobs", S4 + ".json")), (rc, o))
    rc, out, o = w.run("forget", S6, "--json")
    check("mu forget of a finished job: allowed", rc == 0 and o.get("node_state") == "succeeded", (rc, o))
    os.close(fd)


def t_tz(base):
    """Run under TZ=Asia/Kathmandu (UTC+5:45): no HH:MM a person reads may be a UTC one."""
    tz = "Asia/Kathmandu"
    w = WS(base, "tz", tz=tz)
    os.environ["TZ"] = tz
    time.tzset()
    now = int(time.time()) // 60 * 60
    T = {k: proto.iso(now + s) for k, s in (("until", 3000), ("since", -600), ("resumes_by", 1800), ("ends_by", 7200),
                                             ("starts_by", 9000), ("held_at", -3600), ("deadline", 3300),
                                             ("seen", -300))}
    utc = {k: v[11:16] for k, v in T.items()}
    loc = {k: time.strftime("%H:%M", time.localtime(proto.epoch_of(v))) for k, v in T.items()}
    J, B, H, Q = "20261123-010000-paused", "20261123-010001-borrow", "20261123-010002-heldjb", "20261123-010003-queued"
    pz = {"phase": "frozen", "since": T["since"], "until": T["until"], "by": "avi-mac", "why": "hand", "for_job": None,
          "held_mem_bytes": 5 << 30, "lent_to": [B], "resume_asked": None, "resumes_by": None}
    node_note = "paused until %s UTC by avi-mac" % utc["until"]          # what the node itself says (pausectl.note_of)
    running = {"ok": True, "job_id": J, "state": "running", "name": "cfd", "project": "p", "key": "avi-mac",
               "grant": {"cores": 8, "cpus": "0-7", "mem_bytes": 12 << 30, "class": "normal"}, "paused": pz,
               "time": {"limit_s": 21600, "run_s": 5120, "paused_s": 310}, "ends_by": T["ends_by"], "note": node_note,
               "pausable": True}
    resuming = dict(running, paused=dict(pz, resume_asked={"by": "avi-mac", "at": T["since"]},
                                         resumes_by=T["resumes_by"]),
                    note="resuming: waits for %s (ends by %s UTC)" % (B[-6:], utc["resumes_by"]))
    queue = {"ok": True, "node": "office-pc", "capacity": {"cores": 20, "mem_bytes": 20 << 30},
             "allocated": {"cores": 8, "mem_bytes": 12 << 30}, "free": {"cores": 12, "mem_bytes": 8 << 30},
             "lent": {"cores": 8, "mem_bytes": 6 << 30, "borrowed": {"cores": 4, "mem_bytes": 2 << 30}},
             "auto_pause": "marked", "jobs": [running,
                                              {"job_id": B, "state": "running", "grant": {
                                                  "cores": 4, "cpus": "0-3", "mem_bytes": 2 << 30, "class": "borrowing",
                                                  "borrowed": [{"from": J, "until": T["until"]}]},
                                               "ends_by": T["deadline"]},
                                              {"job_id": H, "state": "held", "place": 2, "band": "normal",
                                               "held": {"by": "avi-mac", "at": T["held_at"]}},
                                              {"job_id": Q, "state": "queued", "place": 1, "band": "normal",
                                               "waiting_for": {"resources": {"cores": 16}}, "starts_by": T["starts_by"]}]}
    w.record(J, state="running")
    w.set({"hello": hello("pause"),
           "pause:" + J: {"ok": True, "exit": 0, "job_id": J, "op": "pause", "changed": True, "state": "running",
                          "paused": dict(pz, frozen=True), "lent": {"cores": 8, "mem_bytes": 6 << 30, "until": T["until"]},
                          "note": node_note, "deadline_raised_to": T["deadline"],
                          "next": "it resumes by itself at %s UTC; to resume sooner: mu resume %s" % (utc["until"], J)},
           "resume:" + J: {"ok": True, "exit": 0, "job_id": J, "op": "resume", "changed": True, "state": "resuming",
                           "resumes_by": T["resumes_by"], "waits_for": [B], "paused": resuming["paused"],
                           "note": resuming["note"], "next": "mu queue --json"},
           "status:" + J: running, "list": {"ok": True, "jobs": [resuming]}, "queue": queue})
    outs = {}
    for name, args in (("pause", ("pause", J, "--for", "50m")), ("resume", ("resume", J)), ("status", ("status", J)),
                       ("status list", ("status",)), ("queue", ("queue",))):
        rc, out, _ = w.run(*args)
        outs[name] = (rc, out)
    with open(os.path.join(w.env["MU_HOME"], "compute", "office-pc", "last-status.json"), "w") as f:
        json.dump({"node": "office-pc", "at": T["seen"], "ok": True, "hello": {"mu": {"default": "v"}}}, f)
    rc, out, _ = w.run("compute", "list")
    outs["compute list"] = (rc, out)
    words = {"pause": ("until", "deadline"), "resume": ("resumes_by",), "status": ("until",),
             "status list": ("resumes_by",), "queue": ("until", "held_at", "starts_by", "deadline"),
             "compute list": ("seen",)}
    for name, (rc, out) in outs.items():
        keys = words[name]
        leak = [utc[k] for k in T if utc[k] in out and utc[k] != loc[k]]
        miss = [loc[k] for k in keys if loc[k] not in out]
        check("%s under %s: every time in local time (%s), no UTC time in the words" % (name, tz, ", ".join(
            "%s %s" % (k, loc[k]) for k in keys)) + flag("node-time-words") + flag("utc-words"),
            rc == 0 and not leak and not miss, "exit %s; UTC leaked: %s; local missing: %s\n%s" % (rc, leak, miss, out))
    rc, out, o = w.run("pause", J, "--for", "50m", "--json")
    check("--json keeps ISO UTC in the time fields (paused.until)", rc == 0 and (o.get("paused") or {}).get("until") ==
          T["until"] and loc["until"] in o.get("next", ""), o)
