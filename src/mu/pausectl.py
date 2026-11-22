"""μ node: pause and resume (docs/design/scheduler.md §20.5-§20.8, release 2; as built: §22). Freeze and thaw a job's
unit (systemd freeze/thaw, the cgroup v2 freezer), lower a frozen job's MemoryMax to what it holds so the rest can be
lent, the two transient timers per job (mu-limit-<id>: the job's --time on running time; mu-thaw-<id>: the dead-man
that thaws a frozen job at its deadline + 120 s whether or not the runner lives), the one stop path that thaws first,
reconciliation with systemd's FreezerState each pass, deadlines, deferred resumes, the valve, and the auto-pause
policy for urgent jobs. The verbs pause and resume are here too; node.py registers them.

Every step of a freeze or a thaw is written to state.json (sealed) before it is taken. Node helpers are imported
lazily (node.py imports this module).

Test faults (durable.fault; each makes a unit check or an e2e row fail, scheduler.md §13):
  pause-noop                the pause is recorded but the unit is not frozen
  thaw-over-borrower        resume thaws a lender while a borrower runs on its cores
  no-valve                  a borrower past its deadline + 60 s keeps its lender frozen
  freeze-after-result       a unit frozen after the shim wrote its result is left frozen
  pause-in-memory-only      the pause is not written to state.json
  requeue-keeps-pause       a re-queued attempt keeps the previous attempt's pause
  cancel-without-thaw       stop_unit() does not thaw a frozen unit first (cancel)
  stop-without-thaw         the same, for every stop
  count-paused-time         no time-limit timer: RuntimeMaxSec = --time counts frozen time
  limit-in-runner-loop      the time limit is checked by the runner's pass instead of a systemd timer
  no-pause-deadline         a pause does not end at its deadline
  repause-shortens          a second pause sets the deadline to now + for, before a borrower's end
  pause-unmarked            the "marked" policy pauses jobs not submitted --pausable
  no-cooldown               auto-pauses ignore the 15 min between two and the 2 per attempt
  keep-pause-after-beneficiary  an auto-pause lasts until its deadline after the urgent job ended
  freeze-before-checkout    the auto-pause freezes its victims before the urgent job's checkout
  no-dead-man               no dead-man timer is armed
  timer-env-inherited       the two timers run with the user manager's environment instead of this user's bus: where
                            PAM gives the manager another user's XDG_RUNTIME_DIR (/etc/environment, as on CI's runner),
                            their systemctl --user gets "Failed to connect to bus: Permission denied" and does nothing
and those of the review of 2026-11-15 (scheduler.md §22.5; each makes a sched-pause unit check fail):
  freezing-left             a freeze cut short after systemd froze the unit stays "freezing" (never thawed at its
                            deadline); one cut short and thawed since keeps the lowered MemoryMax
  refreeze-keeps-limit      re-doing an interrupted freeze leaves the time-limit timer armed (it fires while frozen)
  replan-sees-started       the auto-pause's re-plan counts an urgent job started earlier in the pass as still queued,
                            and a failed auto-pause keeps its count and cooldown
  pause-ignores-drain       the auto-pause and the pause pass treat a job with a result as gone while its unit runs
  no-dead-man-rearm         a frozen job whose dead-man timer is gone (a renewal's re-arm failed) gets none again
  pause-not-sticky          the job's own workstation may resume or re-pause (shorten) an owner's pause
  open-pause-uncapped       a frozen record's open pause runs past its deadline + 120 s (the dead-man thawed it then)
  backstop-no-room          RuntimeMaxSec = t + budget: a whole-budget pause thawed late is killed before its --time
  ignore-mem-restore-failure  a failed MemoryMax restore at a thaw is not retried
  renewal-over-2h           a renewed hand pause may span more than 2 h
  audit-terse-pause         pause and resume audit lines leave out (owner), from= and others=1
  no-pause-count-cap        hand pauses per attempt are not counted
  limit-check-each-pass     the runner asks systemd for every running job's limit timer on every pass
  limit-command-unquoted    the limit timer's /bin/sh command does not quote the job's folder
  cgroup-from-record-only   a job paused before the runner recorded its cgroup lends no memory
  auto-charges-max          an auto-pause needs 30 min of the victim's pause budget, whatever the urgent job's --time
  pause-without-selftest    pause is offered (hello, the verb) though the freezer self-check of this boot failed
  lend-unlowered-mem        a frozen job whose MemoryMax could not be lowered still lends memory
  borrower-ignores-shadow   a borrower admitted for ending before the head's start S is not checked against S again
                            after its checkout (node.start_job)
A test injector (sets up a slow step for an e2e row; not a check):
  auto-checkout-slow        the auto-pause's checkout of the urgent job takes 20 s more (Q37)
"""
import os
import shlex
import time

from mu import durable, proto, sched
from mu.durable import fault

PHASES = ("freezing", "frozen", "thawing")


def N():
    from mu import node
    return node


def phase(fs):
    """FreezerState matched by prefix (systemd may add e.g. frozen-by-parent)."""
    fs = fs or ""
    for p in ("frozen", "freezing", "thawing", "running"):
        if fs.startswith(p):
            return p
    return "unknown"


def unit_of(jid):
    return "mu-job-%s.service" % jid


def limit_unit(jid):
    return "mu-limit-%s" % jid


def thaw_unit(jid):
    return "mu-thaw-%s" % jid


def write_state(j, st):
    j["state"] = st
    if fault("pause-in-memory-only") and st.get("pause"):
        return
    durable.write_record(os.path.join(j["dir"], "state.json"), st)


def mono_now():
    return time.monotonic()


# ------------------------------------------------------------------------------------------------ accounting
def grant_time(g):
    t = (g or {}).get("time_s")
    return t if isinstance(t, (int, float)) and not isinstance(t, bool) else None


def budget_of(g):
    """The most one attempt may be paused in total: min(4 h, t) (§20.6)."""
    t = grant_time(g) or 0
    return int(min(N().PAUSE_BUDGET_S, t))


def open_end(pz, now):
    """Where the open pause ends for the accounting: NOW, but a frozen record's never past its deadline + DEAD_MAN_S,
    when the dead-man timer thaws the unit whether or not the runner lives (review 2026-11-15: a job thawed by it that
    ended before the runner came back counted all the time after as paused)."""
    D = pz.get("deadline_mono")
    if pz.get("phase") == "frozen" and isinstance(D, (int, float)) and not fault("open-pause-uncapped"):
        return min(now, D + N().DEAD_MAN_S)
    return now


def open_paused(st, now=None):
    """Seconds of the pause still open (frozen or thawing, same boot), else 0."""
    pz = st.get("pause") or {}
    if pz.get("phase") in ("frozen", "thawing") and pz.get("boot_id") == N().boot_id() and \
            isinstance(pz.get("since_mono"), (int, float)):
        return max(0.0, open_end(pz, mono_now() if now is None else now) - pz["since_mono"])
    return 0.0


def paused_s(st, now=None):
    return float(st.get("paused_total_s") or 0) + open_paused(st, now)


def run_seconds(st, wall_s, now=None):
    """§3.6: run_s = wall_s - paused_total_s - open; never below 0."""
    if not isinstance(wall_s, (int, float)):
        return None
    if fault("count-paused-time"):
        return max(0.0, float(wall_s))
    return max(0.0, float(wall_s) - paused_s(st, now))


def run_so_far(st, now=None):
    """Running time of a running job from its unit's activation (or its start), paused time left out."""
    now = mono_now() if now is None else now
    g = st.get("grant") or {}
    a = g.get("active_mono") or g.get("start_mono")
    if not isinstance(a, (int, float)) or g.get("boot_id") not in (None, N().boot_id()):
        e = st.get("started_epoch")
        return run_seconds(st, time.time() - e, now) if isinstance(e, (int, float)) else None
    return run_seconds(st, now - a, now)


def end_bound(st):
    """A running job's end bound with its pauses (§20.6): active + t + paused + SLACK; a paused one at D + (t - run) +
    SLACK. None when unknown."""
    g = st.get("grant") or {}
    base = sched.end_bound(g) if g else None
    if base is None:
        return None
    if g.get("class") not in ("normal", "backfilled"):
        return base
    pz = st.get("pause") or {}
    if pz.get("phase") in PHASES and pz.get("boot_id") == N().boot_id() and \
            isinstance(pz.get("deadline_mono"), (int, float)):
        t, ran = grant_time(g) or 0, run_so_far(st) or 0
        return pz["deadline_mono"] + max(0, t - ran) + sched.SLACK_S
    return base + float(st.get("paused_total_s") or 0)


def pause_item(st):
    """The plan's view of a job's pause: {phase, deadline, held_mem} or None."""
    pz = st.get("pause") or {}
    if pz.get("phase") not in PHASES or pz.get("boot_id") != N().boot_id():
        return None
    return {"phase": pz["phase"], "deadline": pz.get("deadline_mono"), "held_mem": pz.get("held_mem_bytes")}


def borrowers_of(jid, jobs, drain=None):
    """Running jobs with cores borrowed from JID. A borrower with a result counts while its unit still runs (release
    1's drain rule, §21: its grant is held until systemd says the unit ended): DRAIN, the runner's set of such jobs
    this pass, or, when None, systemd asked for each borrower with a recent result (node.draining)."""
    out = []
    for j in jobs:
        st = j["state"] or {}
        if st.get("state") != "running":
            continue
        g = st.get("grant") or {}
        if not (g.get("class") == "borrowing" and any(b.get("from") == jid for b in g.get("borrowed") or [])):
            continue
        if j["result"]:
            if fault("pause-ignores-drain"):
                continue
            if not (j["id"] in drain if drain is not None else N().draining(j)):
                continue
        out.append(j)
    return out


# ------------------------------------------------------------------------------------------------ systemd
def freezer(unit):
    props = N().unit_props(unit)
    return phase(props.get("FreezerState")), props


def stop_timer(name):
    n = N()
    n.systemctl("stop", name + ".timer", timeout=15)
    n.systemctl("reset-failed", name + ".timer", name + ".service", timeout=15)
    r = n.systemctl("is-active", name + ".timer", timeout=15)
    return r.stdout.strip() not in ("active", "activating")


def timer_active(name):
    r = N().systemctl("is-active", name + ".timer", timeout=15)
    return r.stdout.strip() in ("active", "activating")


def run_timer(name, seconds, argv):
    import subprocess
    n = N()
    stop_timer(name)
    env = n.user_env()
    # the timer's service gets the user manager's environment, not ours: name this user's runtime folder and bus, so
    # that its systemctl --user reaches this manager even when PAM gave the manager another user's XDG_RUNTIME_DIR
    bus = [] if fault("timer-env-inherited") else [
        "--setenv=XDG_RUNTIME_DIR=" + env["XDG_RUNTIME_DIR"],
        "--setenv=DBUS_SESSION_BUS_ADDRESS=unix:path=%s/bus" % env["XDG_RUNTIME_DIR"]]
    cmd = [os.environ.get("MU_SYSTEMD_RUN") or "systemd-run", "--user", "--quiet", "--unit=" + name,
           "--on-active=%ds" % max(1, int(seconds)), "--timer-property=AccuracySec=1s"] + bus + [
           "--description=μ timer %s" % name, "--"] + argv
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=sched.START_WAIT_S, env=env,
                           stdin=subprocess.DEVNULL)
        return r.returncode == 0, r.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)


def limit_command(jid):
    """The time-limit timer's fixed command (§20.6): no μ code runs in it, so it works under any release."""
    d = N().job_dir(jid)
    mark = os.path.join(d, "timelimit")
    q = (lambda s: s) if fault("limit-command-unquoted") else shlex.quote   # a node root with a space or a quote
    sh = ("printf 'the job ran its --time\\n' > %s; sync %s 2>/dev/null; systemctl --user thaw %s; "
          "systemctl --user stop %s") % (q(mark), q(mark), q(unit_of(jid)), q(unit_of(jid)))
    return ["/bin/sh", "-c", sh]


def arm_limit(j, seconds):
    if fault("count-paused-time") or fault("limit-in-runner-loop"):
        return True
    ok, err = run_timer(limit_unit(j["id"]), seconds, limit_command(j["id"]))
    if not ok:
        N().event(j["dir"], "the time-limit timer could not be armed (%s): re-armed on the next pass" % err[:200])
    return ok


def arm_dead_man(j, at_mono):
    if fault("no-dead-man"):
        return True
    ok, err = run_timer(thaw_unit(j["id"]), at_mono - mono_now(),
                        ["systemctl", "--user", "thaw", unit_of(j["id"])])
    if not ok:
        N().event(j["dir"], "the dead-man timer could not be armed (%s)" % err[:200])
    return ok


def set_mem(unit, nbytes):
    r = N().systemctl("set-property", "--runtime", unit, "MemoryMax=%d" % int(nbytes), timeout=30)
    return r.returncode == 0


def stop_unit(unit, why=""):
    """The one stop path (§20.5): thaw first when FreezerState is not running, so the job gets its SIGTERM; then
    stop. Every stop of a job's unit goes through here."""
    n = N()
    if not (fault("stop-without-thaw") or fault("cancel-without-thaw")):
        fs, _ = freezer(unit)
        if fs not in ("running", "unknown"):
            n.systemctl("thaw", unit, timeout=30)
    return n.systemctl("stop", unit, timeout=30)


def mem_current(st, cg=None):
    cg = st.get("cgroup") or cg
    if not cg:
        return None
    try:
        with open(os.path.join(os.environ.get("MU_CGROUP_ROOT") or "/sys/fs/cgroup", cg.lstrip("/"), "memory.current")) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def lend_margin(cur):
    return max(N().LEND_MARGIN_MIN, int(0.05 * (cur or 0)))


# ------------------------------------------------------------------------------------------------ freeze / thaw
def freeze(j, D, by, why, for_job=None):
    """§20.5's freeze, each step written before it is taken. -> (True, None) or (False, why). Under /srv/mu/lock."""
    n = N()
    st = dict(j["state"] or {})
    g = st.get("grant") or {}
    unit = st.get("unit") or unit_of(j["id"])
    prev = st.get("pause") if (st.get("pause") or {}).get("phase") in PHASES else None
    st["pause"] = {"phase": "freezing", "since_mono": mono_now(), "deadline_mono": D, "boot_id": n.boot_id(),
                   "by": by, "why": why, "for_job": for_job, "held_mem_bytes": None, "resume_asked": None}
    write_state(j, st)

    def undo(msg):
        n.systemctl("thaw", unit, timeout=30)
        stop_timer(thaw_unit(j["id"]))
        s2 = dict(j["state"])
        s2.pop("pause", None)
        write_state(j, s2)
        rearm_limit(j)
        n.event(j["dir"], "pause failed: %s" % msg)
        n.journal("job %s: pause failed: %s" % (j["id"], msg))
        return False, msg
    # step 2 every time (stopping a stopped timer is harmless): a freeze re-done after a crash (PREV "freezing") may
    # find the limit timer still armed (review 2026-11-15)
    if not (prev and fault("refreeze-keeps-limit")) and not (fault("count-paused-time") or
                                                               fault("limit-in-runner-loop")) and \
            not stop_timer(limit_unit(j["id"])):
        return undo("its time-limit timer could not be stopped")
    if not arm_dead_man(j, D + n.DEAD_MAN_S) and not fault("no-dead-man"):
        return undo("the dead-man timer could not be armed")
    cg = None
    if not fault("pause-noop"):
        n.systemctl("freeze", unit, timeout=n.FREEZE_WAIT_S)
        fs = "unknown"
        for _ in range(n.FREEZE_WAIT_S * 4):
            fs, _p = freezer(unit)
            if fs == "frozen" or "FreezerState" not in _p:
                break
            time.sleep(0.25)
        if fs != "frozen":
            return undo("the unit did not freeze within %d s (FreezerState %s)" % (n.FREEZE_WAIT_S, fs))
        cg = _p.get("ControlGroup") or None
    held = lower_mem(st, unit, cg)
    st = dict(j["state"])
    st["pause"] = dict(st["pause"], phase="frozen", since_mono=mono_now(), held_mem_bytes=held)
    if prev:   # a re-pause keeps the open pause's start
        st["pause"]["since_mono"] = prev.get("since_mono", st["pause"]["since_mono"])
    else:
        st["pause_count"] = int(st.get("pause_count") or 0) + 1
        if why == "auto":
            st["auto_pause_count"] = int(st.get("auto_pause_count") or 0) + 1
    write_state(j, st)
    grant = g.get("mem_bytes") or 0
    n.event(j["dir"], "paused by %s (%s%s) until monotonic %.0f; keeps %s, lends %d cores and %s" % (
        by, why, ", for %s" % for_job if for_job else "", D, sched.human_gb(held), len(g.get("cpus") or []),
        sched.human_gb(grant - held)))
    n.journal("job %s paused by %s (%s)" % (j["id"], by, why))
    return True, None


def lower_mem(st, unit, cg=None):
    """§20.5 step 5 on a frozen unit: MemoryMax = min(grant, memory.current + margin). -> held (the grant when
    memory.current cannot be read or set-property fails: cores are lent, no memory). CG: the unit's ControlGroup as
    systemd just said, for a job paused before the runner recorded it (CI's Q39 paused one a second after its start)."""
    n = N()
    cur = mem_current(st, None if fault("cgroup-from-record-only") else cg)
    grant = (st.get("grant") or {}).get("mem_bytes") or 0
    held = grant
    if cur is not None:
        held = min(grant, cur + lend_margin(cur))
        if held < grant and not set_mem(unit, held):
            n.journal_once("mem-lend", "pause: set-property MemoryMax on a frozen unit failed: cores are lent, no memory")
            if not fault("lend-unlowered-mem"):
                held = grant
    return held


def finish_freeze(j):
    """A freeze cut short after systemd froze the unit (the process doing it died between §20.5 steps 4 and 6: the
    record says "freezing", systemd "frozen"): steps 2, 3, 5 and 6 now, the open pause's start kept; the pass's
    deadline logic then treats it as any frozen job (review 2026-11-15)."""
    n = N()
    st = dict(j["state"] or {})
    pz = st.get("pause") or {}
    unit = st.get("unit") or unit_of(j["id"])
    if not (fault("count-paused-time") or fault("limit-in-runner-loop")):
        stop_timer(limit_unit(j["id"]))
    D = pz.get("deadline_mono") or mono_now()
    arm_dead_man(j, D + n.DEAD_MAN_S)
    held = lower_mem(st, unit)
    st = dict(j["state"])
    st["pause"] = dict(st["pause"], phase="frozen", held_mem_bytes=held)
    st["pause_count"] = int(st.get("pause_count") or 0) + 1
    if pz.get("why") == "auto":
        st["auto_pause_count"] = int(st.get("auto_pause_count") or 0) + 1
    write_state(j, st)
    n.event(j["dir"], "an interrupted pause finished: frozen by systemd, now recorded (keeps %s)" % sched.human_gb(held))
    n.journal("job %s: an interrupted freeze finished" % j["id"])


def rearm_limit(j):
    """An unfrozen ordinary job started with a limit timer: arm it with the remaining running time, or stop the job at
    once (timeout) if none remains. -> False when the job was stopped."""
    st = j["state"] or {}
    g = st.get("grant") or {}
    if not g.get("limit_timer"):
        return True
    left = (grant_time(g) or 0) - (run_so_far(st) or 0)
    if left <= 0:
        durable.write_durable(os.path.join(j["dir"], "timelimit"), "the job ran its --time\n")
        stop_unit(st.get("unit") or unit_of(j["id"]))
        return False
    return arm_limit(j, left)


def thaw(j, restore_mem=True, how="resumed", end_at=None):
    """§20.5's thaw. RESTORE_MEM false: a borrower lives (the valve, the dead-man): MemoryMax stays at held until the
    runner restores it (mem_restore_pending). END_AT: the monotonic time the pause ended, if earlier than now."""
    n = N()
    st = dict(j["state"] or {})
    pz = st.get("pause") or {}
    unit = st.get("unit") or unit_of(j["id"])
    g = st.get("grant") or {}
    st["pause"] = dict(pz, phase="thawing")
    write_state(j, st)
    if restore_mem and not set_mem(unit, g.get("mem_bytes") or 0) and not fault("ignore-mem-restore-failure"):
        restore_mem = False   # retried by the pass (mem_restore_pending), never left at held without a word
        n.event(j["dir"], "MemoryMax could not be set back to the grant at the thaw: retried on each pass")
    n.systemctl("thaw", unit, timeout=30)
    fs = "unknown"
    for _ in range(40):
        fs, _p = freezer(unit)
        if fs in ("running", "unknown"):
            break
        time.sleep(0.25)
    stop_timer(thaw_unit(j["id"]))
    end_pause(j, how, end_at)
    if not restore_mem:
        st = dict(j["state"])
        st["mem_restore_pending"] = True
        write_state(j, st)
    rearm_limit(j)
    return fs == "running"


def end_pause(j, how, end_at=None):
    """The pause moves into the attempt's history; paused_total_s, last_thaw_mono."""
    n = N()
    st = dict(j["state"] or {})
    pz = st.pop("pause", None) or {}
    now = mono_now()
    end = min(now, end_at) if isinstance(end_at, (int, float)) else now
    since = pz.get("since_mono")
    dur = max(0.0, end - since) if isinstance(since, (int, float)) and pz.get("boot_id") == n.boot_id() and \
        pz.get("phase") in ("frozen", "thawing") else 0.0
    g = st.get("grant") or {}
    hist = list(st.get("pauses") or [])
    entry = {"from": proto.iso(time.time() - (now - since)) if dur else proto.iso(),
             "to": proto.iso(time.time() - (now - end)), "by": pz.get("by"), "why": pz.get("why"),
             "for_job": pz.get("for_job"),
             "lent_mem_bytes": max(0, (g.get("mem_bytes") or 0) - (pz.get("held_mem_bytes") or g.get("mem_bytes") or 0))}
    if len(hist) < 20:
        hist.append(entry)
    else:
        st["pauses_more"] = int(st.get("pauses_more") or 0) + 1
    st.update(pauses=hist, paused_total_s=round(float(st.get("paused_total_s") or 0) + dur, 1), last_thaw_mono=now)
    write_state(j, st)
    n.event(j["dir"], "%s after %ds paused" % (how, dur))


# ------------------------------------------------------------------------------------------------ the runner's pass
def reconcile(j, props):
    """One running job, each pass (§20.8): systemd's FreezerState (by prefix) wins over state.json."""
    n = N()
    st = j["state"] or {}
    if props.get("ActiveState") != "active":
        return
    fs = phase(props.get("FreezerState"))
    pz = st.get("pause") or {}
    unit = st.get("unit") or unit_of(j["id"])
    if pz and pz.get("boot_id") != n.boot_id():
        s2 = dict(st)
        s2.pop("pause", None)
        write_state(j, s2)
        return
    ph = pz.get("phase")
    if not pz:
        if fs in ("frozen", "freezing"):
            n.systemctl("thaw", unit, timeout=30)
            n.event(j["dir"], "frozen outside μ (no pause recorded): thawed")
        elif st.get("mem_restore_pending"):
            pass   # handled by the pass once no borrower lives
        return
    if ph == "freezing" and fs == "frozen" and not fault("freezing-left"):
        finish_freeze(j)   # the deadline, a resume, the valve: runner_pass, later in this pass
    elif ph == "freezing" and fs == "running":
        if (pz.get("deadline_mono") or 0) > mono_now() + 5:
            freeze(j, pz["deadline_mono"], pz.get("by"), pz.get("why"), pz.get("for_job"))
        else:   # never frozen, or thawed since (the dead-man): MemoryMax back to the grant, its timer gone
            if not fault("freezing-left"):
                set_mem(unit, (st.get("grant") or {}).get("mem_bytes") or 0)
                stop_timer(thaw_unit(j["id"]))
            s2 = dict(st)
            s2.pop("pause", None)
            write_state(j, s2)
            rearm_limit(j)
    elif ph == "frozen" and fs == "running":
        D = pz.get("deadline_mono") or mono_now()
        at = D + n.DEAD_MAN_S if mono_now() >= D + n.DEAD_MAN_S else None
        live = borrowers_of(j["id"], n.scan_jobs())
        if not live:
            set_mem(unit, (st.get("grant") or {}).get("mem_bytes") or 0)
        stop_timer(thaw_unit(j["id"]))
        end_pause(j, "thawed outside μ", at)
        if live:
            s2 = dict(j["state"])
            s2["mem_restore_pending"] = True
            write_state(j, s2)
        rearm_limit(j)
        n.journal("job %s: thawed outside μ" % j["id"])
    elif ph == "thawing":
        thaw(j, restore_mem=not st.get("mem_restore_pending"), how="resumed (thaw finished after a restart)")


LIMIT_SEEN = {}    # job id -> (attempt, start, last_thaw_mono) whose limit timer this runner process saw armed
DEAD_MAN_SEEN = {}  # job id -> the deadline whose dead-man timer this runner process saw armed


def check_limit(j):
    """An unfrozen ordinary job with a limit timer must have one armed (after a restart, a dead-man thaw, an arm that
    failed), else it gets one with its remaining time, or is stopped (timeout). systemd is asked once per runner
    process and again after each thaw, not on every pass (review 2026-11-15: one more systemctl per running job per
    second): LIMIT_SEEN."""
    st = j["state"] or {}
    g = st.get("grant") or {}
    if (st.get("pause") or {}).get("phase") in PHASES:
        LIMIT_SEEN.pop(j["id"], None)
        return
    if fault("limit-in-runner-loop") and g.get("limit_timer"):
        if (run_so_far(st) or 0) >= (grant_time(g) or 0):
            durable.write_durable(os.path.join(j["dir"], "timelimit"), "the job ran its --time\n")
            stop_unit(st.get("unit") or unit_of(j["id"]))
        return
    if not g.get("limit_timer") or fault("count-paused-time"):
        return
    seen = (st.get("attempt"), g.get("start_mono"), st.get("last_thaw_mono"))
    if LIMIT_SEEN.get(j["id"]) == seen and not fault("limit-check-each-pass"):
        return
    if timer_active(limit_unit(j["id"])) or rearm_limit(j):
        LIMIT_SEEN[j["id"]] = seen


def check_dead_man(j, D):
    """A frozen job always has its dead-man timer (§20.8): one whose re-arm failed (a renewal, a restart) gets it again
    at D + 120 s. Asked of systemd once per deadline and runner process (DEAD_MAN_SEEN)."""
    if fault("no-dead-man-rearm") or fault("no-dead-man") or DEAD_MAN_SEEN.get(j["id"]) == D:
        return
    if timer_active(thaw_unit(j["id"])) or arm_dead_man(j, D + N().DEAD_MAN_S):
        DEAD_MAN_SEEN[j["id"]] = D


def runner_pass(running, jobs, now=None, drain=()):
    """After the per-job loop (§20.6, §20.7): deadlines, deferred resumes, the end of auto-pauses, the valve, memory
    restored once no borrower lives, a dead-man timer for each frozen job. RUNNING: the running jobs of this pass;
    DRAIN: the jobs with a result whose unit has not ended yet (they still hold their grant, §21)."""
    n = N()
    now = mono_now() if now is None else now
    by_id = {j["id"]: j for j in jobs}
    drain = set(drain)
    for j in list(running):
        st = j["state"] or {}
        pz = st.get("pause") or {}
        live = borrowers_of(j["id"], jobs, drain)
        if st.get("mem_restore_pending") and not pz and not live:
            if not set_mem(st.get("unit") or unit_of(j["id"]), (st.get("grant") or {}).get("mem_bytes") or 0) and \
                    not fault("ignore-mem-restore-failure"):
                n.journal_once("mem-restore-" + j["id"], "job %s: MemoryMax could not be set back to its grant: "
                               "retried each pass" % j["id"])
                continue
            s2 = dict(st)
            s2.pop("mem_restore_pending", None)
            write_state(j, s2)
            n.event(j["dir"], "memory limit restored to its grant (no borrower left)")
            continue
        if pz.get("phase") != "frozen" or pz.get("boot_id") != n.boot_id():
            continue
        D = pz.get("deadline_mono") or 0
        check_dead_man(j, D)
        if fault("no-pause-deadline"):
            D = float("inf")
        if live and now >= D + n.VALVE_AFTER_S and not fault("no-valve"):
            for b in live:   # the valve (§20.6): past its own limit, it cannot keep the lender frozen
                n.systemctl("kill", "--signal=SIGKILL", (b["state"] or {}).get("unit") or unit_of(b["id"]), timeout=15)
                n.event(b["dir"], "killed by the valve: past %s's pause deadline + 60 s" % j["id"])
            n.event(j["dir"], "valve: borrower(s) %s killed at the deadline + 60 s; thawed sharing their CPUs until "
                    "their units end" % " ".join(b["id"] for b in live))
            thaw(j, restore_mem=False, how="thawed by the valve")
            continue
        if live and not fault("thaw-over-borrower"):
            continue
        beneficiary_gone = False
        if pz.get("why") == "auto" and pz.get("for_job") and not fault("keep-pause-after-beneficiary"):
            b = by_id.get(pz["for_job"])
            bst = (b or {}).get("state") or {}
            ended = bool((b or {}).get("result")) and (fault("pause-ignores-drain") or b["id"] not in drain)
            beneficiary_gone = b is None or ended or bst.get("state") not in ("running", "queued") or \
                (bst.get("state") == "queued" and now >= (pz.get("since_mono") or now) + 120)
        if now >= D:
            thaw(j, how="resumed at its deadline")
        elif pz.get("resume_asked"):
            thaw(j, how="resumed (asked by %s)" % (pz["resume_asked"].get("by")))
        elif beneficiary_gone:
            thaw(j, how="resumed: the urgent job %s it was paused for ended" % pz["for_job"])


def finish_with_result(j):
    """A job whose shim wrote its result: a frozen unit is thawed at once (a job with a result is never kept frozen),
    its timers stopped, its pause closed (§20.8, Q31)."""
    st = j["state"] or {}
    if st.get("state") != "running" or not (st.get("pause") or st.get("timers")):
        return
    unit = st.get("unit") or unit_of(j["id"])
    if not fault("freeze-after-result"):
        fs, _ = freezer(unit)
        if fs in ("frozen", "freezing"):
            N().systemctl("thaw", unit, timeout=30)
            N().event(j["dir"], "frozen after its result: thawed")
    stop_timer(limit_unit(j["id"]))
    stop_timer(thaw_unit(j["id"]))
    s2 = dict(st)
    s2.pop("timers", None)
    if s2.get("pause") and not fault("freeze-after-result"):
        s2.pop("pause")
    write_state(j, s2)


def drop_for_requeue(st):
    """A re-queued attempt starts without the previous attempt's pause (Q33)."""
    if fault("requeue-keeps-pause"):
        return st
    had = bool(st.get("pause"))
    for k in ("pause", "paused_total_s", "pause_count", "auto_pause_count", "last_thaw_mono", "pauses", "pauses_more",
              "mem_restore_pending", "timers"):
        st.pop(k, None)
    if had:
        st["note"] = (st.get("note") or "") + "; was paused when the node stopped"
    return st


# ------------------------------------------------------------------------------------------------ auto-pause (§20.7)
def auto_candidate(pl, by_id, running, cap, node):
    """Conditions 1-2 of §20.7 on this pass's plan: the policy allows it, and the head H (not started) is urgent with
    an explicit --time short enough for one auto-pause. -> {H, req, policy} or None."""
    n = N()
    policy = n.auto_policy(node)
    hid = pl.get("head")
    if policy == "off" or not hid or hid not in by_id or not running or not available(node):
        return None
    if any(x["id"] == hid for x in pl["starts"]):
        return None
    H = by_id[hid]
    if sched.band_of(H["state"] or {}, H["job"]) != "urgent" or (H["state"] or {}).get("state") != "queued":
        return None
    lim = H["job"].get("limits") or {}
    if not isinstance(lim.get("time_s"), int) or lim.get("quick"):
        return None
    try:
        req = sched.resolve(H["job"], cap)
    except Exception:  # noqa
        return None
    if req["class"] == "express" or req["time_s"] + sched.MARGIN_S + 30 > n.AUTO_PAUSE_S:
        return None
    return {"H": H, "req": req, "policy": policy}


def eligible(running, policy, now=None, need_s=None):
    """Condition 3 of §20.7: running jobs that may be auto-paused, with their estimated lent memory. NEED_S: how long
    this auto-pause lasts (30 s + the urgent job's t + MARGIN); its budget must hold that, not the 30-minute most
    (review 2026-11-15, CI: a 10-minute job, budget 600 s, could never be auto-paused for a 20 s urgent job)."""
    n = N()
    now = mono_now() if now is None else now
    need_s = n.AUTO_PAUSE_S if need_s is None or fault("auto-charges-max") else need_s
    out = []
    for j in running:
        st, job = j["state"] or {}, j["job"]
        g = st.get("grant") or {}
        if g.get("class") not in ("normal", "backfilled") or not g.get("limit_timer"):
            continue
        if sched.band_of(st, job) == "urgent" or (st.get("pause") or {}).get("phase") in PHASES:
            continue
        if policy == "marked" and job.get("pausable") is not True and not fault("pause-unmarked"):
            continue
        if not fault("no-cooldown"):
            if int(st.get("auto_pause_count") or 0) >= n.AUTO_PAUSE_MAX:
                continue
            last = st.get("last_thaw_mono")
            if isinstance(last, (int, float)) and now - last < n.AUTO_COOLDOWN_S:
                continue
        if float(st.get("paused_total_s") or 0) + need_s > budget_of(g):
            continue
        cur = mem_current(st)
        est = (g.get("mem_bytes") or 0) - (cur + lend_margin(cur)) if cur is not None else 0
        out.append({"id": j["id"], "cores": len(g.get("cpus") or []), "est_lent": est,
                    "active_mono": g.get("active_mono") or g.get("start_mono") or 0})
    return out


def auto_pause(ap, jobs, running, cap, walk, disk, drain=()):
    """§20.7 in one pass, inside the start lock: 1. checkout of H; 2. re-plan (H may now fit without a pause; the
    conditions may no longer hold); 3. D = now + 30 s + t + MARGIN; 4. freeze each victim (any failure: thaw the
    ones frozen, H waits); 5. start H as a borrower on the lent cores (if that fails: thaw the victims at once, and
    the attempt is not counted). DRAIN: the pass's jobs whose unit still runs after their result (§21).
    -> True when H was started."""
    n = N()
    H, req, policy = ap["H"], ap["req"], ap["policy"]

    def plan_now():
        # the walk as it stands now: a job started earlier in this pass (start_job updated its state) is running, not
        # also still queued ahead of H (review 2026-11-15: two urgent jobs defeated the auto-pause)
        w = walk if fault("replan-sees-started") else [
            it for it in walk if not it["j"]["result"] and (it["j"]["state"] or {}).get("state", "queued") in
            ("queued", "held")]
        return n.safe_plan(time.monotonic(), cap, n.running_info(jobs, drain=() if fault("pause-ignores-drain") else
                                                                 drain), n.queue_items(w, cap), disk)

    def victims():
        pl = plan_now()
        if any(x["id"] == H["id"] for x in pl["starts"]):
            return pl, None
        need_s = n.FREEZE_WAIT_S + req["time_s"] + sched.MARGIN_S
        return pl, sched.choose_victims(eligible(running, policy, need_s=need_s), req["cores"], req["mem_bytes"],
                                        (pl.get("free") or {}).get("mem_bytes") or 0)
    pl, vs = victims()
    if vs is None and not any(x["id"] == H["id"] for x in pl["starts"]):
        return False
    by_id = {j["id"]: j for j in jobs}
    frozen = []
    before = {}   # each victim's auto_pause_count and last_thaw_mono before: an auto-pause that fails is not counted

    def give_up(how):
        for f in frozen:
            thaw(by_id[f], how=how)
            if fault("replan-sees-started"):
                continue
            s2 = dict(by_id[f]["state"] or {})
            for k, v in before.get(f, {}).items():
                if v is None:
                    s2.pop(k, None)
                else:
                    s2[k] = v
            write_state(by_id[f], s2)

    def freeze_all():
        D = mono_now() + n.FREEZE_WAIT_S + req["time_s"] + sched.MARGIN_S
        for vid in vs:
            vst = by_id[vid]["state"] or {}
            before[vid] = {"auto_pause_count": vst.get("auto_pause_count"), "last_thaw_mono": vst.get("last_thaw_mono")}
            ok, err = freeze(by_id[vid], D, "auto", "auto", H["id"])
            if not ok:
                give_up("auto-pause given up (%s)" % err)
                return False
            frozen.append(vid)
        return True
    if fault("freeze-before-checkout"):
        if not freeze_all():
            return False
    try:
        if fault("auto-checkout-slow"):   # test injector (Q37): the victims must not be frozen during this
            time.sleep(20)
            n.refresh_start_lock()
        n.prepare_work(H)
    except Exception as e:  # noqa
        give_up("auto-pause given up: the urgent job's checkout failed")
        n.event(H["dir"], "auto-pause: the checkout failed (%s); left to its ordinary start" % str(e)[:300])
        return False
    n.refresh_start_lock()
    if not frozen:
        pl, vs = victims()   # the state after the checkout (step 2)
        x = next((x for x in pl["starts"] if x["id"] == H["id"]), None)
        if x is not None:   # it fits now without a pause
            return n.start_job(H, x["grant"], x["S"], prepared=True)
        if vs is None:
            return False
        if not freeze_all():
            return False
    n.event(H["dir"], "auto-pause: %s frozen for it" % " ".join(vs))
    pl = plan_now()
    x = next((x for x in pl["starts"] if x["id"] == H["id"]), None)
    if x is None or not n.start_job(H, x["grant"], x["S"], prepared=True):
        give_up("auto-pause ended: the urgent job did not start")
        return False
    return True


# ------------------------------------------------------------------------------------------------ the answers
def paused_view(j, v, jobs):
    """§3.1 of the contract: the `paused` object of a running job, or None."""
    st = j["state"] or {}
    pz = st.get("pause") or {}
    if pz.get("phase") not in PHASES:
        return None
    n = N()
    vv = v or {"wall": time.time(), "now": mono_now()}
    lent_to = [b["id"] for b in borrowers_of(j["id"], jobs)]
    ra = pz.get("resume_asked")
    resumes_by = None
    if ra:
        ends = [x for x in (end_bound(b["state"] or {}) for b in borrowers_of(j["id"], jobs)) if x is not None]
        resumes_by = n.wall_of(vv, max(ends)) if ends else n.wall_of(vv, mono_now())
    since = pz.get("since_mono")
    return {"phase": pz.get("phase"), "since": n.wall_of(vv, since) if pz.get("boot_id") == n.boot_id() else None,
            "until": n.wall_of(vv, pz.get("deadline_mono")) if pz.get("boot_id") == n.boot_id() else None,
            "by": pz.get("by"), "why": pz.get("why"), "for_job": pz.get("for_job"),
            "held_mem_bytes": pz.get("held_mem_bytes"), "lent_to": lent_to, "resume_asked": ra,
            "resumes_by": resumes_by}


def time_view(j):
    st = j["state"] or {}
    g = st.get("grant") or {}
    ordinary = g.get("class") in ("normal", "backfilled")
    run = run_so_far(st)
    ps = paused_s(st) if ordinary else 0
    left = max(0, budget_of(g) - int(ps)) if ordinary and g.get("limit_timer") else 0
    return {"limit_s": grant_time(g), "run_s": int(run) if run is not None else None, "paused_s": int(ps),
            "budget_left_s": left}


def utc_hm(iso):
    """'2026-11-14T14:45:05Z' -> '14:45 UTC': the node does not know the workstation's time zone, so its own words
    say which zone they use (release 2026.11.12; a workstation of that release rebuilds them in its local time from
    the ISO fields, qctl.pause_note). '?' when unknown."""
    return "%s UTC" % iso[11:16] if isinstance(iso, str) and len(iso) >= 16 else "?"


def note_of(pv):
    if not pv:
        return None
    hh = utc_hm(pv.get("until"))
    if pv.get("resume_asked"):
        return "resuming: waits for %s (ends by %s)" % (" ".join(x[-6:] for x in pv.get("lent_to") or []),
                                                        utc_hm(pv.get("resumes_by")))
    if pv.get("why") == "auto":
        return "paused for %s until %s" % ((pv.get("for_job") or "")[-6:], hh)
    return "paused until %s by %s" % (hh, pv.get("by"))


def job_fields(j, v, jobs, out):
    """Adds pausable, paused, time, note to a running job's status entry (node with the pause feature)."""
    out["pausable"] = j["job"].get("pausable") is True
    st = j["state"] or {}
    if st.get("state") != "running" or j["result"]:
        return out
    pv = paused_view(j, v, jobs)
    out["paused"] = pv
    if not fault("drop-field"):
        out["time"] = time_view(j)
    if pv:
        out["note"] = note_of(pv)
    g = st.get("grant") or {}
    if g.get("class") == "borrowing" and isinstance(out.get("grant"), dict):
        vv = v or {"wall": time.time(), "now": mono_now()}
        out["grant"]["borrowed"] = [{"from": b.get("from"), "cpus": sched.fmt_cpus(b.get("cpus") or []),
                                     "mem_bytes": b.get("mem_bytes"), "until": N().wall_of(vv, b.get("until_mono"))}
                                    for b in g.get("borrowed") or []]
    return out


# ------------------------------------------------------------------------------------------------ the verbs
def available(node=None):
    n = N()
    node = n.node_info() if node is None else node
    fz = node.get("freezer") or {}
    if fault("pause-without-selftest"):
        return True
    return fz.get("ok") is True and fz.get("boot_id") == n.boot_id()


def parse_for(args):
    n = N()
    v = args.get("for")
    return int(v) if v else n.PAUSE_DEFAULT_S


def who(key, args, own):
    """The audit's caller words (§20.4): by=KEY (owner) from=P others=1."""
    if fault("audit-terse-pause"):
        return "by=%s" % key
    return "by=%s%s%s%s" % (key, " (owner)" if key in own else "", " from=%s" % args["from"] if args.get("from") else "",
                            " others=1" if args.get("others") == "1" else "")


def auth(op, j, key, args, st=None):
    """§20.4: the hold rule (the job's workstation or an owner key; --others for another project), and an owner's
    pause is sticky: only an owner key may resume it or renew it (review 2026-11-15). ST: the job's state.json as read
    under the lock (the pause's `by`)."""
    n = N()
    own = n.owners()
    job = j["job"]
    why = sched.authorise("hold", {"key": job.get("key"), "project_id": job.get("project_id")}, key, own,
                          args.get("from"), args.get("others") == "1")
    pz = (st if st is not None else (j["state"] or {})).get("pause") or {}
    if not why and key not in own and pz.get("phase") in PHASES and pz.get("by") in own and \
            not fault("pause-not-sticky"):
        why = ("not-allowed", "paused by the owner %s: only an owner key may resume or renew it" % pz.get("by"))
    if why:
        n.journal("queue: refused %s id=%s %s %s (%s)" % (op, j["id"], who(key, args, own), why[0], why[1]))
        rc = proto.NODE_NOPERM if why[0] == "not-allowed" else proto.NODE_USAGE
        raise n.NodeError(rc, why[0], "only if the owner asked: the same command with --others" if why[0] ==
                          "other-project" else "ask the owner", message="%s %s refused: %s" % (op, j["id"], why[1]))


def limit_refuse(jid, msg, **extra):
    n = N()
    n.journal("queue: refused pause id=%s pause-limit (%s)" % (jid, msg))
    return n.NodeError(proto.NODE_USAGE, "pause-limit", "mu queue --json", message="pause of %s refused: %s" % (jid, msg),
                       **extra)


def verb_pause(key, args):
    """§20.5: freeze a running ordinary job until now + for (at most 2 h; 4 h per attempt, never more than its t)."""
    n = N()
    jid = args.get("id")
    if not jid:
        raise n.NodeError(proto.NODE_USAGE, "bad-argument", "pause id=<job-id> [for=<s>]")
    j = n.load_job(jid)
    if j["result"]:
        raise n.NodeError(proto.NODE_USAGE, "already-finished", "mu status %s" % jid, message="job %s already "
                          "finished (%s)" % (jid, j["result"].get("state")), state=j["result"].get("state"))
    auth("pause", j, key, args)
    if not available():
        raise n.NodeError(proto.NODE_UNAVAILABLE, "pause-unavailable", "nothing to do: this node cannot pause jobs",
                          message="the node's freezer self-check did not pass: jobs cannot be paused here")
    for_s = parse_for(args)
    own = n.owners()
    with n.Lock(wait=n.LOCK_WAIT_S):
        j = n.load_job(jid)
        if j["result"]:
            raise n.NodeError(proto.NODE_USAGE, "already-finished", "mu status %s" % jid, message="job %s already "
                              "finished (%s)" % (jid, j["result"].get("state")), state=j["result"].get("state"))
        st = j["state"] or {}
        auth("pause", j, key, args, st)   # again under the lock: an owner's pause may have landed since
        if st.get("state") != "running":
            raise n.NodeError(proto.NODE_USAGE, "not-running", "mu hold %s" % jid, message="job %s is %s, not running: "
                              "a queued job is held, not paused (mu hold %s)" % (jid, st.get("state", "queued"), jid),
                              state=st.get("state", "queued"))
        props = n.unit_props(st.get("unit") or unit_of(jid))
        if n.unit_ended(props):
            raise n.NodeError(proto.NODE_USAGE, "already-finished", "mu status %s" % jid, message="job %s has just "
                              "ended" % jid, state="running")
        g = st.get("grant") or {}
        if g.get("class") == "express":
            raise limit_refuse(jid, "an express (--quick) job is never paused")
        if g.get("class") == "borrowing":
            raise limit_refuse(jid, "it runs on cores lent by %s until its deadline: a borrower is never paused" %
                               " ".join(b.get("from", "?")[-6:] for b in g.get("borrowed") or []))
        if not g.get("limit_timer"):
            raise limit_refuse(jid, "started before pause was available (no time-limit timer): its frozen time would "
                               "count against its --time")
        if for_s > n.PAUSE_HAND_MAX_S or for_s < 1:
            raise limit_refuse(jid, "--for %ds: a pause lasts at most %ds" % (for_s, n.PAUSE_HAND_MAX_S),
                               limit_s=n.PAUSE_HAND_MAX_S)
        now = mono_now()
        pz = st.get("pause") or {}
        jobs = n.scan_jobs()
        live = borrowers_of(jid, jobs)
        D = now + for_s
        raised = None
        if pz.get("phase") in PHASES and not fault("repause-shortens"):
            ends = [x for x in (end_bound(b["state"] or {}) for b in live) if x is not None]
            if ends and max(ends) > D:
                D = raised = max(ends)
        already = paused_s(st)
        budget = budget_of(g)
        extra = (D - (pz.get("since_mono") or now)) if pz.get("phase") == "frozen" else (D - now)
        if float(st.get("paused_total_s") or 0) + extra > budget:
            raise limit_refuse(jid, "it may be paused %ds in all (min(4 h, its --time)); %ds used, this pause would "
                               "make %ds" % (budget, already, float(st.get("paused_total_s") or 0) + extra),
                               budget_s=budget, paused_s=int(already))
        if pz.get("phase") == "frozen" and not raised and extra > n.PAUSE_HAND_MAX_S and \
                not fault("renewal-over-2h"):   # one pause, renewed or not, lasts at most 2 h (owner, 2026-11-14)
            raise limit_refuse(jid, "--for %ds: this pause began %ds ago and may last %ds in all; at most --for %ds "
                               "now" % (for_s, now - (pz.get("since_mono") or now), n.PAUSE_HAND_MAX_S, max(
                                   0, n.PAUSE_HAND_MAX_S - (now - (pz.get("since_mono") or now)))),
                               limit_s=n.PAUSE_HAND_MAX_S)
        if pz.get("phase") not in PHASES and int(st.get("pause_count") or 0) >= n.PAUSE_COUNT_MAX and \
                not fault("no-pause-count-cap"):
            raise limit_refuse(jid, "it was paused %d times in this run already (at most %d)" % (
                int(st.get("pause_count") or 0), n.PAUSE_COUNT_MAX))
        if pz.get("phase") == "frozen":
            s2 = dict(st)
            s2["pause"] = dict(pz, deadline_mono=D, by=key, why="hand", for_job=None, resume_asked=None)
            write_state(j, s2)
            ok, err = arm_dead_man(j, D + n.DEAD_MAN_S), None
            if not ok and not fault("no-dead-man-rearm"):   # never frozen without a dead-man: the old deadline stays
                write_state(j, dict(j["state"], pause=pz))
                arm_dead_man(j, (pz.get("deadline_mono") or now) + n.DEAD_MAN_S)
                err = "the dead-man timer could not be armed for the new deadline; the pause keeps its old one"
            else:
                ok = True
                n.event(j["dir"], "pause renewed by %s until monotonic %.0f%s" % (
                    key, D, " (raised to a borrower's end)" if raised else ""))
        else:
            ok, err = freeze(j, D, key, "hand")
        n.journal("queue: pause id=%s %s for=%ds %s" % (jid, who(key, args, own), for_s, "ok" if ok else "pause-failed"))
        if not ok:
            raise n.NodeError(proto.NODE_UNAVAILABLE, "pause-failed", "retry", message="pause of %s failed: %s" % (
                jid, err))
        v = n.view()
        j = n.load_job(jid)
        pv = paused_view(j, v, v["jobs"])
        g = (j["state"] or {}).get("grant") or {}
        out = {"ok": True, "exit": 0, "job_id": jid, "op": "pause", "changed": True, "state": "running",
               "paused": dict(pv or {}, frozen=True), "time": time_view(j),
               "lent": {"cores": len(g.get("cpus") or []), "cpus": sched.fmt_cpus(g.get("cpus") or []),
                        "mem_bytes": max(0, (g.get("mem_bytes") or 0) - ((pv or {}).get("held_mem_bytes") or 0)),
                        "until": (pv or {}).get("until")},
               "delays": [], "note": note_of(pv),
               "next": "it resumes by itself at %s; to resume sooner: mu resume %s" % (utc_hm((pv or {}).get("until")), jid)}
        if raised:
            out["deadline_raised_to"] = n.wall_of(v, raised)
        if fault("drop-field"):
            out.pop("paused")
        return out


def verb_resume(key, args):
    """§20.6: thaw at once if no borrower is on the job's cores, else resume_asked (the runner thaws it after the
    last borrower ends, before D)."""
    n = N()
    jid = args.get("id")
    if not jid:
        raise n.NodeError(proto.NODE_USAGE, "bad-argument", "resume id=<job-id>")
    j = n.load_job(jid)
    if j["result"]:
        return {"ok": True, "exit": 0, "job_id": jid, "op": "resume", "changed": False, "state": j["result"].get("state"),
                "note": "already finished", "next": "mu status %s" % jid}
    auth("resume", j, key, args)
    own = n.owners()
    with n.Lock(wait=n.LOCK_WAIT_S):
        j = n.load_job(jid)
        st = j["state"] or {}
        if j["result"] or not (st.get("pause") or {}).get("phase") in PHASES:
            state = (j["result"] or {}).get("state") or st.get("state", "queued")
            return {"ok": True, "exit": 0, "job_id": jid, "op": "resume", "changed": False, "state": state,
                    "note": "not paused", "next": "mu queue --json"}
        auth("resume", j, key, args, st)
        jobs = n.scan_jobs()
        live = borrowers_of(jid, jobs)
        if live and not fault("thaw-over-borrower"):
            s2 = dict(st)
            s2["pause"] = dict(st["pause"], resume_asked={"by": key, "at": proto.iso()})
            write_state(j, s2)
            n.event(j["dir"], "resume asked by %s: waits for %s" % (key, " ".join(b["id"] for b in live)))
            n.journal("queue: resume id=%s %s deferred (borrowers %s)" % (jid, who(key, args, own),
                                                                        " ".join(b["id"] for b in live)))
            v = n.view()
            pv = paused_view(n.load_job(jid), v, v["jobs"])
            out = {"ok": True, "exit": 0, "job_id": jid, "op": "resume", "changed": True, "state": "resuming",
                   "resumes_by": (pv or {}).get("resumes_by"), "waits_for": [b["id"] for b in live],
                   "paused": pv, "note": note_of(pv), "next": "mu queue --json"}
            if fault("drop-field"):
                out.pop("resumes_by")
            return out
        thaw(j, how="resumed by %s" % key)
        n.journal("queue: resume id=%s %s ok" % (jid, who(key, args, own)))
        j = n.load_job(jid)
        return {"ok": True, "exit": 0, "job_id": jid, "op": "resume", "changed": True, "state": "running",
                "paused": None, "time": time_view(j), "next": "mu queue --json"}


# ------------------------------------------------------------------------------------------------ freezer self-check
def freezer_check():
    """§12 item 10 on this node: a test unit frozen (FreezerState frozen), its MemoryMax lowered while frozen, thawed
    (running). -> {ok, mem_lend, detail, at, boot_id}; written to node.json `freezer`."""
    import secrets
    import subprocess
    import sys
    n = N()
    unit = "mu-selftest-%s-freeze.service" % secrets.token_hex(3)
    out = {"ok": False, "mem_lend": False, "at": proto.iso(), "boot_id": n.boot_id(), "detail": ""}
    try:
        r = subprocess.run([os.environ.get("MU_SYSTEMD_RUN") or "systemd-run", "--user", "--quiet", "--unit=" + unit,
                            "-p", "MemoryMax=64M", "-p", "RuntimeMaxSec=60", "-p", "TimeoutStopSec=5", "--",
                            sys.executable, "-I", "-S", "-c", "import time\nwhile True: time.sleep(0.1)"],
                           capture_output=True, text=True, timeout=60, env=n.user_env(), stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            out["detail"] = "systemd-run failed: %s" % r.stderr.strip()[:200]
            return out
        time.sleep(0.5)
        if "FreezerState" not in N().unit_props(unit):
            out["detail"] = "systemd reports no FreezerState for a running unit (systemd older than 246, or no cgroup v2)"
            return out
        n.systemctl("freeze", unit, timeout=30)
        fs = "unknown"
        for _ in range(40):
            fs, _p = freezer(unit)
            if fs == "frozen" or "FreezerState" not in _p:
                break
            time.sleep(0.25)
        if fs != "frozen":
            out["detail"] = "systemctl --user freeze: FreezerState %s, not frozen" % fs
            return out
        out["mem_lend"] = set_mem(unit, 48 << 20)
        n.systemctl("thaw", unit, timeout=30)
        for _ in range(40):
            fs, _p = freezer(unit)
            if fs == "running" or "FreezerState" not in _p:
                break
            time.sleep(0.25)
        out["ok"] = fs == "running"
        out["detail"] = "freeze and thaw work%s" % ("; MemoryMax lowered while frozen" if out["mem_lend"] else
                                                     "; set-property MemoryMax on a frozen unit failed (cores only)")
        if not out["ok"]:
            out["detail"] = "systemctl --user thaw: FreezerState %s, not running" % fs
        return out
    finally:
        n.systemctl("stop", unit, timeout=15)
        n.systemctl("reset-failed", unit, timeout=15)
        if os.path.isdir(n.ROOT) and os.access(n.ROOT, os.W_OK):
            n.update_node_info(freezer=out)
        n.journal("selftest: freezer %s (%s)" % ("PASS" if out["ok"] else "FAIL", out["detail"]))
