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
gc-review  the checks added after the review of 2026-11-23 (job-layer.md §24.7)
Most checks name the fault that must make them fail; tools/test-jobs/run.sh runs each section again with each fault and
counts only a FAIL of a check tagged with it. The checks without a fault are listed in job-layer.md §24.4 (the review of
2026-11-23 found the earlier claim "every check" false)."""
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


def tz_on(tz="Asia/Kathmandu"):
    """This process and the workstations it starts in TZ (UTC+5:45 by default: no local HH:MM equals a UTC one)."""
    os.environ["TZ"] = tz
    time.tzset()
    return tz


def when_local(iso):
    """proto.local_when's form, computed here without its test fault (utc-words must not change the expectation)."""
    lt = time.localtime(proto.epoch_of(iso))
    return time.strftime("%d %b %H:%M" if lt.tm_year == time.localtime().tm_year else "%d %b %Y %H:%M", lt)


def local_not_utc(text, iso):
    """TEXT shows ISO's local date and time and not its UTC HH:MM."""
    return when_local(iso) in text and iso[11:16] not in text


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
            fetched_rec(d, jid, key, now - fetched_days * DAY, owner=key)
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


def fetched_rec(d, jid, key, at, owner):
    """fetched.json as the node's note_fetch writes it: one fetch by KEY at AT, the job's own key OWNER."""
    durable.write_record(os.path.join(d, "fetched.json"), {
        "schema": 1, "job_id": jid, "first_at": proto.iso(at), "last_at": proto.iso(at), "count": 1, "keys": [key],
        "owner": owner, "by": {key: {"count": 1, "first_at": proto.iso(at), "last_at": proto.iso(at)}}})


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
    V, AP, URG, PAU = ids(9), ids(10), ids(30), ids(31)   # review 2026-11-23: other references than a lender
    n.job(V, ended_days=40)
    n.job(AP, ended_days=40)
    n.job(URG, state="running", statex={"urgent": {"victims": [V]}})
    n.job(PAU, state="running", statex={"paused": {"phase": "frozen", "by": "auto", "for_job": AP}})
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
    check("  nor one named otherwise in an unfinished job's state (an urgent job's victims, a paused job's for_job)" +
          flag("gc-ignores-refs"), V not in got and AP not in got, got)
    check("a dry run changes nothing" + flag("gc-dry-prunes"),
          all(os.path.isdir(os.path.join(n.N.job_dir(x), "work")) for x in (F8, U31)) and
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
          and pr.get("why", "").startswith("fetched by avi-mac 8 days ago") and pr.get("by") == "test", (names, pr))
    log = open(os.path.join(d, "log"), "rb").read()
    check("the log keeps its last 64 kB, from a line's start, after a line saying it is a tail" + flag("prune-in-place"),
          len(log) <= 65 * 1024 and log.startswith(b"== \xce\xbc: the job's folder was cleaned up") and
          log.split(b"\n")[1].startswith(b"line ") and log.endswith(b".....\n"), log[:200])
    rc, s = n.js("avi-mac", "status", "id=" + F8)
    check("status of a pruned job works (exit 0) and says pruned (at, why, bytes_freed, fetched)" +
          flag("status-no-gc-marks"),
          rc == 0 and s.get("state") == "succeeded" and (s.get("pruned") or {}).get("at") and
          (s.get("fetched") or {}).get("count") == 1, s)
    rc, raw = n.serve("avi-mac", "log", "id=" + F8, "offset=0")
    check("log of a pruned job sends the kept tail", rc == 0 and raw == log, raw[:120])
    rc, f = n.js("avi-mac", "fetch", "id=" + F8, "what=result")
    check("fetch of a pruned job is refused: exit 66, error pruned, with when and why" + flag("fetch-pruned-served"),
          rc == 66 and f.get("error") == "pruned" and (f.get("pruned") or {}).get("at"), (rc, f))
    audit = open(os.path.join(n.root, "audit.log"), encoding="utf-8").read()
    check("each prune is in the journal and the audit log" + flag("prune-in-place"), "gc: pruned id=%s" % F8 in audit and
          "gc: pruned id=%s" % U31 in audit, audit[-600:])
    time.sleep(0.5)
    walked = []
    real_tb = nodegc.tree_bytes
    nodegc.tree_bytes = lambda x: walked.append(x) or real_tb(x)
    out2 = nodegc.run("test")
    nodegc.tree_bytes = real_tb
    check("a second clean-up with nothing due prunes nothing and walks no folder (sizes cached in gc.json)" +
          flag("gc-sizes-not-cached"),
          out2["pruned"] == [] and walked == [], (out2["pruned"], walked))
    for _ in range(50):
        if not left(n.root):
            break
        time.sleep(0.1)
    check("the old folders are deleted (a detached rm)" + flag("gc-no-remove"), left(n.root) == [],
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
    RF = ids(17)
    n.job(RF, ended_days=3, fetched_days=2 / 1440.0)   # its outputs fetched 2 minutes ago (perhaps still being sent)
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
    check("  never one fetched under 10 minutes ago, even under pressure" + flag("gc-no-fetch-recent"), RF not in got and
          os.path.isdir(os.path.join(n.N.job_dir(RF), "work")), got)
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
              % (when, r.returncode, "whole" if when == "before" else "the pruned one") +
              (flag("prune-in-place") if when == "before" else ""),   # in place, the injector after it never fires
              r.returncode == 9 and ok_state, (r.returncode, r.stderr[-300:], sorted(os.listdir(dc))))
        out = nodegc.run("test")
        for _ in range(50):
            if not left(n.root):
                break
            time.sleep(0.1)
        check("  the next clean-up removes what the crash left in prune/ and %s" % (
            "prunes the job" if when == "before" else "leaves the pruned job as it is") + flag("gc-no-leftovers"),
            left(n.root) == [] and honest(dc) and
            os.path.exists(os.path.join(dc, "pruned.json")), (left(n.root), out["pruned"]))

    # ---- E. the fetch record and the fetch lock
    n = Node(base, "fetch", stubs)
    E = ids(21)
    de = n.job(E, ended_days=40)
    rc, raw = n.serve("avi-mac", "fetch", "id=" + E, "what=result")
    check("fetch what=result does not count as a fetch" + flag("fetch-result-counts"),
          rc == 0 and not os.path.exists(os.path.join(de, "fetched.json")),
          rc)
    rc, raw = n.serve("avi-mac", "fetch", "id=" + E)
    f = durable.read_record(os.path.join(de, "fetched.json")) or {}
    check("a whole fetch is recorded on the node (fetched.json: count, last_at, the key)" + flag("fetch-not-recorded"),
          rc == 0 and f.get("count") == 1 and f.get("keys") == ["avi-mac"] and proto.epoch_of(f.get("last_at")), (rc, f))
    fetched_rec(de, E, "avi-mac", time.time() - 9 * DAY, owner="avi-mac")
    lk = os.open(os.path.join(de, "job.json"), os.O_RDONLY)
    fcntl.flock(lk, fcntl.LOCK_SH)    # a fetch in progress holds this
    out = nodegc.run("test")
    fcntl.flock(lk, fcntl.LOCK_UN)
    os.close(lk)
    check("a job whose outputs are being fetched (a shared lock on its job.json) is skipped" +
          flag("gc-ignores-fetch-lock"), out["pruned"] == [] and any("fetched" in x["why"] for x in out["skipped"]) and
          os.path.isdir(os.path.join(de, "work")), (out["pruned"], out["skipped"]))
    # the fetch's side (review 2026-11-23: only the clean-up's side was tested): the clean-up holds the folder, a fetch
    # waits; the folder is swapped meanwhile; the fetch then answers pruned and sends nothing
    E2 = ids(27)
    de2 = n.job(E2, ended_days=40)
    lk = os.open(os.path.join(de2, "job.json"), os.O_RDONLY)
    fcntl.flock(lk, fcntl.LOCK_EX)    # what prune_one holds
    env = dict(os.environ, **n.env)
    p = subprocess.Popen([sys.executable, "-I", "-S", os.path.join(REPO, "src", "mu", "cli.py"), "node", "serve",
                          "--key=avi-mac", "fetch", "id=" + E2], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(2.0)
    waiting = p.poll() is None
    newd = os.path.join(n.root, "prune", "%s.test" % E2)
    os.makedirs(os.path.dirname(newd), exist_ok=True)
    nodegc.build_pruned(n.N.load_job(E2), "test", "test", nodegc.settings(), 0, newd)
    swapped = nodegc.exchange(newd, de2)
    fcntl.flock(lk, fcntl.LOCK_UN)
    os.close(lk)
    fo, fe = p.communicate(timeout=60)
    check("the fetch waits while the clean-up holds the job's folder (its exclusive lock on job.json)" +
          flag("fetch-no-lock"), waiting and swapped is None, (waiting, swapped, p.returncode, fe[-300:]))
    try:
        fj = json.loads(fo.decode().strip().splitlines()[-1])
    except (ValueError, IndexError):
        fj = {"raw": fo[:200]}
    check("  then, the folder swapped meanwhile, it answers pruned (exit 66) and sends no output" +
          flag("fetch-no-lock") + flag("fetch-pruned-served"), p.returncode == 66 and fj.get("error") == "pruned" and
          b"out.json" not in fo, (p.returncode, fj, fe[-300:]))
    shutil.rmtree(newd, ignore_errors=True)
    # a fetch by another paired key is recorded, but only the job's own workstation's fetches count (review 2026-11-23)
    OJ, OW = ids(28), ids(29)
    doj = n.job(OJ, ended_days=9, key="ci-ws")
    rc, raw = n.serve("ci-other", "fetch", "id=" + OJ)
    f = durable.read_record(os.path.join(doj, "fetched.json")) or {}
    check("a whole fetch by another paired key is recorded under that key (fetched.json by, owner)" +
          flag("fetch-not-recorded"),
          rc == 0 and f.get("owner") == "ci-ws" and list((f.get("by") or {}).keys()) == ["ci-other"], (rc, f))
    fetched_rec(doj, OJ, "ci-other", time.time() - 8 * DAY, owner="ci-ws")
    n.job(OW, ended_days=9, fetched_days=8, key="ci-ws")
    out = nodegc.run("test", dry=True)
    got = [x["job_id"] for x in out["pruned"]]
    check("  it does not count: 8 days after another key's fetch the job is not due (never fetched by its own "
          "workstation: 30 days), while one its workstation fetched 8 days ago is" + flag("gc-any-fetch"),
          OJ not in got and OW in got, [(x["job_id"], x["why"]) for x in out["pruned"]])
    rc, sj = n.js("avi-mac", "status", "id=" + OJ)
    fs = sj.get("fetched") or {}
    check("  status says who fetched it: keys, the owner, by_owner none" + flag("gc-any-fetch"),
          rc == 0 and fs.get("keys") == ["ci-other"] and fs.get("owner") == "ci-ws" and fs.get("by_owner") is None, sj)

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
    check("gc dry=1 (owner): lists the job it would prune, changes nothing" + flag("gc-ignores-keep") +
          flag("gc-dry-prunes"),
          rc == 0 and [x["job_id"] for x in g.get("pruned") or []] == [THEIRS] and g.get("dry_run") and
          os.path.isdir(os.path.join(n.N.job_dir(THEIRS), "work")), (rc, g))
    rc, g = n.js("avi-mac", "gc")
    check("gc (owner): prunes it, not the kept one" + flag("gc-ignores-keep"),
          rc == 0 and [x["job_id"] for x in g.get("pruned") or []] ==
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
          "are refused (exit 2)" + flag("gc-settings-unchecked"),
          r.returncode == 0 and o.get("gc", {}).get("after_fetch_days") == 3 and
          nodegc.settings()["enabled"] is False and r2.returncode == 2 and r3.returncode == 2,
          (r.stdout[-300:], r2.stderr[-200:], r3.stderr[-200:]))


def t_gc_review(base, stubs):
    """The checks added after the review of 2026-11-23 (job-layer.md §24.7), each with its fault."""
    from mu import nodegc
    G = 1 << 30
    # ---- the disk figure: space freed inside the virtual disk, counted only until C: gets it back
    n = Node(base, "space", stubs, min_free_gb=20)
    real = (n.N.disk_info, nodegc.fs_used, nodegc.host_free_bytes)
    cur = {}
    n.N.disk_info = lambda node: {"free_gb": cur["c"] // G, "min_free_gb": 20, "where": "C: (/mnt/c)", "host": "C"}
    nodegc.fs_used = lambda path: cur["used"]
    nodegc.host_free_bytes = lambda d: cur["c"]

    def eff(used, c, cache):
        cur.update(used=used * G, c=c * G)
        return nodegc.space({}, cache)[0]
    try:
        fixed = {}
        a = [eff(60, 5, fixed), eff(45, 5, fixed), eff(50, 5, fixed), eff(50, 15, fixed)]
        check("disk figure, a virtual disk that is not sparse: 15 GB freed inside count as free (5 + 15), new data "
              "reuses them (5 + 10), a compaction gives them to C: (15 + 0)" + flag("gc-reclaim-double"),
              a == [5, 20, 15, 15], a)
        sparse = {}
        b = [eff(60, 5, sparse), eff(45, 20, sparse), eff(45, 15, sparse)]
        check("disk figure, a sparse virtual disk: C: got the 15 GB back, they are not counted twice (20, not 35); "
              "Windows then filling C: lowers the figure (15)" + flag("gc-reclaim-double"), b == [5, 20, 15], b)
        # the review's sequence through a whole clean-up: 300 GB inside once, 150 freed and given back to a sparse
        # C:, then Windows fills C: to 15 GB: under min_free_gb 20 + 10, a fetched job is pruned
        FJ = ids(40)
        n.job(FJ, ended_days=3, fetched_days=2)
        for used, c in ((300, 10), (150, 160)):
            cur.update(used=used * G, c=c * G)
            nodegc.run("test", dry=True)
        cur.update(used=150 * G, c=15 * G)
        out = nodegc.run("test")
        check("  a sparse disk that Windows filled again: pressure at 15 GB on C:, the fetched job pruned" +
              flag("gc-reclaim-double"), out["pressure"] and [x["job_id"] for x in out["pruned"]] == [FJ] and
              "15 GB free for new jobs (15 GB on C: (/mnt/c))" in out["pruned"][0]["why"],
              (out["pressure"], out.get("free_gb_effective"), out["pruned"]))
    finally:
        n.N.disk_info, nodegc.fs_used, nodegc.host_free_bytes = real

    # ---- the log: never followed through a link, never a FIFO (the clean-up holds the queue lock)
    n = Node(base, "fifo", stubs)
    FF, LJ = ids(41), ids(42)
    dff = n.job(FF, ended_days=40)
    os.unlink(os.path.join(dff, "log"))
    os.mkfifo(os.path.join(dff, "log"))
    dlj = n.job(LJ, ended_days=40)
    secret = os.path.join(n.d, "outside.txt")
    with open(secret, "w") as f:
        f.write("SECRET of another folder\n")
    os.unlink(os.path.join(dlj, "log"))
    os.symlink(secret, os.path.join(dlj, "log"))
    env = dict(os.environ, **n.env)
    code = ("import sys, json; sys.path.insert(0, %r); from mu import node, nodegc; node.ROOT = %r; "
            "print(json.dumps([x['job_id'] for x in nodegc.run('test')['pruned']]))" % (os.path.join(REPO, "src"), n.root))
    try:
        r = subprocess.run([sys.executable, "-I", "-S", "-c", code], env=env, capture_output=True, timeout=30)
        rc, got = r.returncode, r.stdout.decode()
    except subprocess.TimeoutExpired:
        rc, got = "timeout (still blocked after 30 s)", ""
    pf = durable.read_record(os.path.join(dff, "pruned.json")) or {}
    check("a job whose log is a FIFO: the clean-up does not block (it holds the queue lock), the job is pruned, its "
          "log not kept and why" + flag("gc-log-follows"), rc == 0 and FF in got and
          (pf.get("log") or {}).get("not_kept") == "not a regular file", (rc, got, pf.get("log")))
    lg = open(os.path.join(dlj, "log"), "rb").read() if os.path.isfile(os.path.join(dlj, "log")) else b""
    check("  a job whose log is a link: not followed (the other file's text is not copied)" + flag("gc-log-follows"),
          b"SECRET" not in lg and not os.path.islink(os.path.join(dlj, "log")) and
          ((durable.read_record(os.path.join(dlj, "pruned.json")) or {}).get("log") or {}).get("not_kept") == "a link",
          (lg[:200], (durable.read_record(os.path.join(dlj, "pruned.json")) or {}).get("log")))

    # ---- a keep.json that does not read still keeps
    n = Node(base, "keepbad", stubs)
    UK = ids(43)
    duk = n.job(UK, ended_days=40)
    with open(os.path.join(duk, "keep.json"), "w") as f:
        f.write('{"by": "written by hand"}\n')
    out = nodegc.run("test")
    check("a keep.json that does not read (unsealed, by hand): the job is kept, and the answer warns" +
          flag("keep-needs-seal") + flag("gc-ignores-keep"),
          out["pruned"] == [] and os.path.isdir(os.path.join(duk, "work")) and
          any(UK in w for w in out.get("warnings") or []), (out["pruned"], out.get("warnings")))

    # ---- the runner's size walk has a time budget (a first pass over many folders)
    n = Node(base, "walk", stubs)
    W = [ids(44 + i) for i in range(3)]
    for x in W:
        n.job(x, ended_days=40)
    walked = []
    real_tb, real_wb = nodegc.tree_bytes, nodegc.WALK_BUDGET_S
    nodegc.tree_bytes = lambda x: walked.append(x) or real_tb(x)
    nodegc.WALK_BUDGET_S = 0
    try:
        out = nodegc.run("runner", budget_s=nodegc.TICK_BUDGET_S)
        cache = durable.read_record(os.path.join(n.root, "gc.json")) or {}
        check("the runner's size walk stops at its budget (one folder here), prunes nothing before the candidates are "
              "measured, keeps what it measured and comes back (more)" + flag("gc-walk-unbudgeted"),
              len(walked) == 1 and out["pruned"] == [] and out.get("more") and len(cache.get("sizes") or {}) == 1,
              (len(walked), out["pruned"], out.get("more"), cache.get("sizes")))
        for _ in range(4):
            out = nodegc.run("runner", budget_s=nodegc.TICK_BUDGET_S)
        check("  passes later every folder is measured once and the jobs are pruned",
              len([x for x in walked if os.path.dirname(x) == n.N.p("jobs")]) == 3 and
              all(os.path.exists(os.path.join(n.N.job_dir(x), "pruned.json")) for x in W), (walked, out["pruned"]))
    finally:
        nodegc.tree_bytes, nodegc.WALK_BUDGET_S = real_tb, real_wb

    # ---- a full file system: the ballast makes room for the pruned copy
    n = Node(base, "full", stubs)
    nodegc.run("test")   # a pass on an empty node leaves the ballast
    had = os.path.exists(nodegc.ballast_path())
    BJ = ids(47)
    n.job(BJ, ended_days=40)
    real_cs = nodegc.copy_small

    def full(src, dst):
        if os.path.exists(nodegc.ballast_path()) or not had:
            raise OSError(28, "No space left on device")
        return real_cs(src, dst)
    nodegc.copy_small = full
    try:
        out = nodegc.run("test")
    finally:
        nodegc.copy_small = real_cs
    check("a full file system (ENOSPC): the ballast is deleted, the job pruned, the ballast made again" +
          flag("gc-no-ballast"), had and [x["job_id"] for x in out["pruned"]] == [BJ] and
          os.path.getsize(nodegc.ballast_path()) == nodegc.BALLAST_BYTES and "prune" not in os.path.basename(
              nodegc.ballast_path()), (had, out["pruned"], out["skipped"]))

    # ---- an error after the swap: the job is pruned (its name holds the pruned copy), and said so
    n = Node(base, "postswap", stubs)
    PS = ids(48)
    dps = n.job(PS, ended_days=40)
    real_sd = durable.sync_dir

    def sd(path):
        if os.path.realpath(path) == os.path.realpath(n.N.p("jobs")):
            raise OSError(5, "Input/output error")
        return real_sd(path)
    durable.sync_dir = sd
    try:
        out = nodegc.run("test")
    finally:
        durable.sync_dir = real_sd
    check("an error after the swap (fsync of jobs/): the job is listed as pruned, its folder is the pruned one" +
          flag("gc-post-swap-error-skips"), [x["job_id"] for x in out["pruned"]] == [PS] and
          os.path.exists(os.path.join(dps, "pruned.json")), (out["pruned"], out["skipped"]))
    nodegc.run("test")
    for _ in range(50):
        if not left(n.root):
            break
        time.sleep(0.1)
    check("  the next clean-up deletes what it left in prune/", left(n.root) == [], left(n.root))

    # ---- gc.enabled false: the runner's clean-up does nothing; mu compute gc still does
    n = Node(base, "off", stubs, gc={"enabled": False})
    OF = ids(49)
    dof = n.job(OF, ended_days=40)
    out = nodegc.tick()
    check("gc.enabled false: the runner's clean-up prunes nothing" + flag("gc-enabled-ignored"),
          out is not None and out["pruned"] == [] and os.path.isdir(os.path.join(dof, "work")), out and out["pruned"])
    rc, g = n.js("avi-mac", "gc")
    check("  mu compute gc (an owner) still prunes", rc == 0 and [x["job_id"] for x in g.get("pruned") or []] == [OF],
          (rc, g.get("pruned")))

    # ---- the runner's loop calls the clean-up (runner_pass: step, then tick)
    n = Node(base, "pass", stubs)
    RP = ids(50)
    drp = n.job(RP, ended_days=40)
    n.N.runner_pass(True, [False], None)
    check("the runner's pass runs the clean-up after its step (node.runner_pass)" + flag("gc-no-tick"),
          os.path.exists(os.path.join(drp, "pruned.json")), sorted(os.listdir(drp)))

    # ---- a keep racing the pass: checked again under the queue lock
    n = Node(base, "race", stubs)
    KR = ids(51)
    dkr = n.job(KR, ended_days=40)
    real_c = nodegc.candidates

    def cands_then_keep(*a, **k):
        r = real_c(*a, **k)
        durable.write_record(os.path.join(dkr, "keep.json"), {"schema": 1, "job_id": KR, "by": "avi-mac",
                                                              "at": proto.iso()})
        return r
    nodegc.candidates = cands_then_keep
    try:
        out = nodegc.run("test")
    finally:
        nodegc.candidates = real_c
    check("a mu keep between the selection and the prune: the job is checked again under the queue lock and kept" +
          flag("gc-no-recheck") + flag("gc-ignores-keep"),
          out["pruned"] == [] and os.path.isdir(os.path.join(dkr, "work")) and
          any(x["why"] == "changed" for x in out["skipped"]), (out["pruned"], out["skipped"]))

    # ---- a node that cannot swap folders: nothing pruned, nothing deleted in place, one try
    n = Node(base, "noswap", stubs)
    NS = [ids(52), ids(53)]
    for x in NS:
        n.job(x, ended_days=40)
    real_x = nodegc.exchange
    nodegc.exchange = lambda a, b: "the swap failed: Operation not supported"
    try:
        out = nodegc.run("test")
    finally:
        nodegc.exchange = real_x
    check("a node that cannot swap folders: nothing pruned, every folder whole, one try, the reason in the answer" +
          flag("gc-swap-fallback"), out["pruned"] == [] and len(out["skipped"]) == 1 and
          "Operation not supported" in out["skipped"][0]["why"] and all(honest(n.N.job_dir(x)) and not os.path.exists(
              os.path.join(n.N.job_dir(x), "pruned.json")) for x in NS), (out["pruned"], out["skipped"]))

    # ---- mu compute status's figure: every finished folder measured, the count of measured ones
    n = Node(base, "figure", stubs)
    for i, (e, kp) in enumerate(((40, False), (0.5, False), (40, True))):
        d = n.job(ids(54 + i), ended_days=e, work_mb=4)
        if kp:
            durable.write_record(os.path.join(d, "keep.json"), {"schema": 1, "job_id": ids(54 + i), "by": "avi-mac",
                                                                "at": proto.iso()})
    out = nodegc.run("test", dry=True)
    fin = out["finished"]
    check("the finished folders' figure measures the young and the kept ones too (3 jobs of over 4 MB each)" +
          flag("gc-measure-candidates-only"),
          fin["jobs"] == 3 and fin["measured"] == 3 and fin["bytes"] > 12 * MB, fin)


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
    w = WS(base, "gcc", tz=tz_on())   # review 2026-11-23: on a UTC machine (CI) a UTC time looked local
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
    check("  in words too (exit 11, stderr)" + flag("fetch-ignores-pruned"),
          rc == 11 and "were removed on office-pc on" in out, out[-300:])
    w.set({"status:" + J: dict(st, pruned=None), "fetch:" + J: {"_rc": 66, "ok": False, "error": "pruned",
                                                               "message": "job was cleaned up", "pruned": pr}})
    rc, out, o = w.run("fetch", J, "--json")
    check("the node's own refusal (an older status without `pruned`): exit 11 too" + flag("pruned-not-mapped"),
          rc == 11 and
          o.get("error") == "pruned", (rc, out[-300:]))
    w.set({"status:" + J: st, "log:" + J: {"_raw": "== μ: the job's folder was cleaned up ==\nlast line\n"}})
    rc, out, o = w.run("log", J)
    check("mu log of a cleaned-up job shows the kept tail and says it is a tail (stderr)",
          rc == 0 and "last line" in out and "this is the last" in out and "of its log" in out, out[-300:])
    rc, out, o = w.run("log", J, "--json")
    check("  --json: tail true, pruned", rc == 0 and o.get("tail") is True and o.get("pruned", {}).get("at"), o)
    rc, out, o = w.run("log", J)
    check("  the note's time is local (Asia/Kathmandu)" + flag("utc-words"), rc == 0 and local_not_utc(out, pr["at"]),
          out[-300:])
    rc, out, o = w.run("status", J)
    check("mu status of a cleaned-up job: when (local time), why, whether it was fetched" + flag("utc-words"),
          rc == 0 and "cleaned up on office-pc on %s" % when_local(pr["at"]) in out and "never been fetched" in out and
          pr["at"][11:16] not in out, out[-400:])
    # review 2026-11-23: the outputs' fetch refused as pruned after the status said otherwise (cleaned up in between)
    w.set({"status:" + J: dict(st, pruned=None), "fetch-outputs:" + J: {
        "_rc": 66, "ok": False, "error": "pruned", "message": "job was cleaned up", "pruned": pr}})
    rc, out, o = w.run("fetch", J, "--json")
    check("a job cleaned up between mu fetch's calls: exit 11 pruned (the node's refusal), not 8 integrity" +
          flag("fetch-tar-error-first"), rc == 11 and o.get("error") == "pruned", (rc, out[-300:]))
    whole = dict(pr, log={"size_before": 169, "kept": 169, "tail": False})
    w.set({"status:" + J: dict(st, pruned=whole), "log:" + J: {"_raw": "a short log\n"}})
    rc, out, o = w.run("log", J)
    check("mu log of a cleaned-up job whose log was kept whole: says so, not \"the last 169 B of 169 B\"" +
          flag("log-note-says-tail"), rc == 0 and "its whole log was kept" in out and "this is the last" not in out,
          out[-300:])
    w.set({"list": {"ok": True, "jobs": [{"job_id": J, "state": "succeeded", "attempt": 1, "exit_code": 0,
                                          "pruned": pr}]}})
    rc, out, o = w.run("status")
    check("mu status (the list) marks a cleaned-up job, in local time" + flag("list-ignores-pruned") + flag("utc-words"),
          rc == 0 and "cleaned up %s" % when_local(pr["at"]) in out and pr["at"][11:16] not in out, out[-300:])
    other = dict(pr, fetched={"count": 2, "first_at": "2026-11-10T08:00:00Z", "last_at": "2026-11-12T08:00:00Z",
                              "keys": ["ci-other"], "owner": "avi-mac", "by_owner": None})
    mine = dict(pr, fetched={"count": 1, "first_at": "2026-11-12T08:00:00Z", "last_at": "2026-11-12T08:00:00Z",
                             "keys": ["avi-mac"], "owner": "avi-mac",
                             "by_owner": {"count": 1, "last_at": "2026-11-12T08:00:00Z"}})
    words = []
    for p in (other, mine):
        w.set({"status:" + J: dict(st, pruned=p)})
        words.append(w.run("status", J)[1])
    check("mu status says who fetched a cleaned-up job's outputs: the job's workstation, or another key only" +
          flag("fetched-words-no-keys"), "the job's workstation avi-mac never fetched them; ci-other did" in words[0] and
          "fetched by avi-mac (the job's workstation) 1 time(s)" in words[1], words)
    w.set({"hello": hello("gc"), "keep:" + J: {"ok": True, "exit": 0, "job_id": J, "changed": True,
                                               "kept": {"by": "avi-mac", "at": "2026-11-23T10:00:00Z"}, "pruned": None}})
    rc, out, o = w.run("keep", J)
    reqs = w.requests()
    check("mu keep JOB: sends keep with this project (from=)", rc == 0 and "kept" in out and
          any(x.startswith("keep id=%s from=" % J) for x in reqs), (out, reqs))
    w.set({"hello": hello()})
    rc, out, o = w.run("keep", J, "--json")
    check("mu keep against a node without the clean-up: exit 6 node-too-old" + flag("keep-no-feature-check"),
          rc == 6 and o.get("error") ==
          "node-too-old", (rc, o))
    w.set({"hello": hello("gc"), "keep:" + J: {"ok": True, "exit": 0, "job_id": J, "changed": False, "kept": None,
                                               "pruned": pr, "note": "already cleaned up on %s" % pr["at"]}})
    rc, out, o = w.run("keep", J)
    check("mu keep of a cleaned-up job: when, in local time" + flag("utc-words"),
          rc == 0 and "already cleaned up on %s" % when_local(pr["at"]) in out and pr["at"][11:16] not in out, out)
    h = dict(hello("gc"), hw={"cpu_model": "cpu", "cores_physical": 20, "mem_total_kb": 1 << 20}, runner="active",
             last_stop="clean", gc={"enabled": True, "as_of": "2026-11-23T03:00:00Z", "finished": {
                 "jobs": 5, "bytes": 3 << 30, "measured": 3, "pruned": 2}, "due": {"jobs": 0, "bytes": 0},
                 "prunable": {"jobs": 1, "bytes": 1 << 30}})
    w.set({"hello": h})
    rc, out, o = w.run("compute", "status", "office-pc")
    check("mu compute status says how many finished folders are measured so far" + flag("gc-line-no-measured"),
          rc == 0 and "in 5 finished job(s) (3 of them measured so far)" in out and "2 cleaned up before" in out, out)
    w.set({"hello": hello("gc"), "gc": {"ok": True, "dry_run": True, "pruned": [{"job_id": J, "why": "x", "bytes":
                                                                                 1 << 30}], "skipped": [],
                                        "space": {"free_gb": 38, "where": "C:"}, "line_gb": 30, "free_gb_effective": 38,
                                        "not_candidates": {"unfinished": 1}}})
    rc, out, o = w.run("compute", "gc", "office-pc", "--dry-run")
    check("mu compute gc --dry-run: sends gc dry=1, lists what would be pruned", rc == 0 and "would prune 1 job" in out
          and J in out and "gc dry=1" in w.requests(), (out, w.requests()))


def t_sending(base):
    w = WS(base, "sending", tz=tz_on())
    S1, S2, S3, S4, S5, S6, S7, S8, S9, S10 = ("20261115-0900%02d-send%02d" % (i, i) for i in range(1, 11))
    w.record(S7)
    w.record(S1)
    w.snapref(S1)
    w.record(S2, spec={"job_id": S2, "submitted": {"at": proto.iso()}})
    w.record(S3)
    w.snapref(S3)
    w.record(S4)
    w.record(S5)
    w.record(S10)
    lock = os.path.join(w.proj, ".mu", "jobs", ".%s.lock" % S3)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)   # a submit still pushing S3
    old = time.time() - 3600
    for j in (S1, S3, S4, S5, S10):
        os.utime(os.path.join(w.proj, ".mu", "jobs", j + ".json"), (old, old))
    nosuch = {"_rc": 66, "ok": False, "error": "no-such-job", "message": "the node has no job"}
    busy = {"_rc": 69, "ok": False, "error": "busy", "message": "the queue lock is busy"}
    w.set({"list": {"ok": True, "jobs": [{"job_id": S7, "state": "succeeded", "attempt": 1}]}, "status:" + S1: nosuch, "status:" + S2: nosuch, "status:" + S3: nosuch,
           "status:" + S5: nosuch, "status:" + S4: {"ok": True, "job_id": S4, "state": "queued", "position": 1},
           "status:" + S10: busy})
    rc, out, o = w.run("status", "--json")
    rows = {j["job_id"]: j for j in o.get("jobs") or []}
    recs = {j: durable.read_record(os.path.join(w.proj, ".mu", "jobs", j + ".json")) or {} for j in (S1, S2, S3, S4, S5)}
    check("mu status: a stale \"sending\" record (submit gone, node has no such job) becomes unsent, on disk too" +
          flag("no-unsent"), rows.get(S1, {}).get("state") == "unsent" and recs[S1].get("state") == "unsent" and
          rows.get(S5, {}).get("state") == "unsent" and sorted(o.get("reconciled") or []) == [S1, S5],
          (rows.get(S1), recs[S1].get("state"), o.get("reconciled")))
    r10 = durable.read_record(os.path.join(w.proj, ".mu", "jobs", S10 + ".json")) or {}
    check("  a record whose status call fails otherwise (busy) stays sending: only no-such-job marks it" +
          flag("unsent-any-error"), rows.get(S10, {}).get("state") == "sending" and r10.get("state") == "sending" and
          S10 not in (o.get("reconciled") or []), (rows.get(S10), r10.get("state"), o.get("reconciled")))
    check("  the unsent row's words give the submit time in local time" + flag("utc-words"),
          local_not_utc(rows.get(S1, {}).get("note") or "", "2026-11-15T09:00:00Z"), rows.get(S1))
    check("  the next step: --resume when the snapshot is there, else only mu forget" + flag("no-unsent"),
          "mu submit --resume %s" % S1 in rows.get(S1, {}).get("next", "") and
          "--resume" not in rows.get(S5, {}).get("next", "--resume") and "mu forget %s" % S5 in rows.get(S5, {}).get(
              "next", ""), (rows.get(S1), rows.get(S5)))
    check("  a fresh record (under 10 min) and one whose submit still holds its lock stay sending, the node not asked" +
          flag("unsent-ignores-lock"),
          rows.get(S2, {}).get("state") == "sending" and rows.get(S3, {}).get("state") == "sending" and
          recs[S2].get("state") == "sending" and recs[S3].get("state") == "sending" and
          not any(S2 in r or S3 in r for r in w.requests()), (rows.get(S2), rows.get(S3), w.requests()))
    s7 = durable.read_record(os.path.join(w.proj, ".mu", "jobs", S7 + ".json")) or {}
    check("  a record the node knows (the submit's answer was lost) takes the node's state, asked or in its list" +
          flag("no-unsent"),
          rows.get(S4, {}).get("state") == "queued" and recs[S4].get("state") == "queued" and
          s7.get("state") == "succeeded", (rows.get(S4), recs[S4], s7.get("state")))
    check("  only list and status were asked of the node (nothing deleted there)",
          all(r.split()[0] in ("list", "status") for r in w.requests()), w.requests())
    rc, out, o = w.run("status", "--json")
    rc2, text, _ = w.run("status")
    check("shown once: a second mu status reconciles nothing new, and still lists each unsent job with its next step" +
          flag("no-unsent"),
          o.get("reconciled") == [] and "not sent · mu submit --resume %s" % S1 in text and
          "never accepted" not in text, (o.get("reconciled"), text))
    rc, out, o = w.run("status", S1, "--json")
    check("mu status JOB of an unsent job: exit 9, error not-sent, the next step" + flag("no-unsent"),
          rc == 9 and o.get("error") ==
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
    check("mu forget of a finished job this clone never fetched: refused (exit 2, not-fetched: mu fetch needs the "
          "record), the record kept" + flag("forget-unfetched"), rc == 2 and o.get("error") == "not-fetched" and
          "--force" in o.get("next", "") and os.path.exists(os.path.join(w.proj, ".mu", "jobs", S6 + ".json")), (rc, o))
    rc, out, o = w.run("forget", S6, "--force", "--json")
    check("  with --force: forgotten, and it says how mu fetch can still get the outputs",
          rc == 0 and o.get("node_state") == "succeeded" and "--output" in o.get("note", ""), (rc, o))
    w.record(S8, state="succeeded")
    os.makedirs(os.path.join(w.proj, "runs"), exist_ok=True)
    with open(os.path.join(w.proj, "runs", S8 + ".json"), "w") as f:
        f.write("{}")
    w.set({"status:" + S8: {"ok": True, "job_id": S8, "state": "succeeded"}})
    rc, out, o = w.run("forget", S8, "--json")
    check("mu forget of a finished job this clone fetched (runs/ID.json): allowed, the run record stays",
          rc == 0 and "runs/%s.json stays" % S8 in o.get("note", ""), (rc, o))
    w.record(S9, state="succeeded")
    for ans, what in ((busy, "busy"), ({"_rc": 255, "ok": False}, "unreachable")):
        w.set({"status:" + S9: ans})
        rc, out, o = w.run("forget", S9, "--json")
        check("mu forget when the node cannot say (%s): refused, nothing forgotten" % what + flag("forget-unreachable"),
              rc != 0 and o.get("error") != "usage" and os.path.exists(os.path.join(w.proj, ".mu", "jobs", S9 + ".json")),
              (rc, o))
    w.set({"status:" + S3: nosuch})
    rc, out, o = w.run("forget", S3, "--json")
    check("mu forget while a submit is still sending the job (its lock held): refused (submit-running)" +
          flag("forget-during-submit"), rc == 2 and o.get("error") == "submit-running" and
          os.path.exists(os.path.join(w.proj, ".mu", "jobs", S3 + ".json")), (rc, o))
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
    table = {"hello": hello("pause"),
           "pause:" + J: {"ok": True, "exit": 0, "job_id": J, "op": "pause", "changed": True, "state": "running",
                          "paused": dict(pz, frozen=True), "lent": {"cores": 8, "mem_bytes": 6 << 30, "until": T["until"]},
                          "note": node_note, "deadline_raised_to": T["deadline"],
                          "next": "it resumes by itself at %s UTC; to resume sooner: mu resume %s" % (utc["until"], J)},
           "resume:" + J: {"ok": True, "exit": 0, "job_id": J, "op": "resume", "changed": True, "state": "resuming",
                           "resumes_by": T["resumes_by"], "waits_for": [B], "paused": resuming["paused"],
                           "note": resuming["note"], "next": "mu queue --json (it thaws by %s UTC)" % utc["resumes_by"]},
           "status:" + J: running, "list": {"ok": True, "jobs": [resuming]}, "queue": queue}
    w.set(table)
    outs = {}
    for name, args in (("pause", ("pause", J, "--for", "50m")), ("resume", ("resume", J)), ("status", ("status", J)),
                       ("status list", ("status",)), ("queue", ("queue",))):
        rc, out, _ = w.run(*args)
        outs[name] = (rc, out)
    with open(os.path.join(w.env["MU_HOME"], "compute", "office-pc", "last-status.json"), "w") as f:
        json.dump({"node": "office-pc", "at": T["seen"], "ok": True, "hello": {"mu": {"default": "v"}}}, f)
    rc, out, _ = w.run("compute", "list")
    outs["compute list"] = (rc, out)
    w.set(dict(table, hello=dict(hello("gc", "pause"), hw={"cpu_model": "cpu", "cores_physical": 20,
                                                           "mem_total_kb": 1 << 20},
                         runner="active", last_stop="clean", gc={
                             "enabled": True, "as_of": T["seen"], "finished": {"jobs": 1, "bytes": 1 << 30, "measured": 1},
                             "due": {"jobs": 0, "bytes": 0}, "prunable": {"jobs": 0, "bytes": 0},
                             "last_run": {"at": T["held_at"], "pruned": 1, "freed_bytes": 1 << 30}})))
    rc, out, _ = w.run("compute", "status", "office-pc")   # its job folders' line (gc_line; review 2026-11-23)
    outs["compute status"] = (rc, out)
    words = {"pause": ("until", "deadline"), "resume": ("resumes_by",), "status": ("until",),
             "status list": ("resumes_by",), "queue": ("until", "held_at", "starts_by", "deadline"),
             "compute list": ("seen",), "compute status": ("seen", "held_at")}
    # node-time-words (the node's own words passed through) can show only where the node's words are printed: queue and
    # compute list build theirs from fields (review 2026-11-23: a tag that cannot fail proves nothing)
    nodewords = ("pause", "resume", "status", "status list")
    for name, (rc, out) in outs.items():
        keys = words[name]
        leak = [utc[k] for k in T if utc[k] in out and utc[k] != loc[k]]
        miss = [loc[k] for k in keys if loc[k] not in out]
        check("%s under %s: every time in local time (%s), no UTC time in the words" % (name, tz, ", ".join(
            "%s %s" % (k, loc[k]) for k in keys)) + (flag("node-time-words") if name in nodewords else "") +
            flag("utc-words"),
            rc == 0 and not leak and not miss, "exit %s; UTC leaked: %s; local missing: %s\n%s" % (rc, leak, miss, out))
    rc, out, o = w.run("pause", J, "--for", "50m", "--json")
    check("--json keeps ISO UTC in the time fields (paused.until), next local" + flag("utc-words"),
          rc == 0 and (o.get("paused") or {}).get("until") ==
          T["until"] and loc["until"] in o.get("next", ""), o)
