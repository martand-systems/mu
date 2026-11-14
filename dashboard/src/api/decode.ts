// Typed decoders for every /api answer (docs/design/dashboard.md §4.8). Hand-written, no schema library: each checks
// the type of every field, clamps numbers to a sane range, caps strings, and turns anything unexpected into null (or a
// safe default) so a hostile or broken answer can never reach a component as the wrong type. Every departure from the
// contract is recorded in `problems`: the page ignores them, the tests (D38) require none on the golden fixtures.
import type {
  JobDetail, JobMetrics, JobRow, JobState, JobsList, Limits, LiveJob, LogPiece, MacCard, MetricSample, NodeCard,
  NodeError, NodeHello, NodeState, NodeStats, Overview, Series, Session, Usage,
} from "./types";

export class ApiMismatch extends Error {} // "api" is not 1: the page and the server are from different releases

type Obj = Record<string, unknown>;

export class Ctx {
  problems: string[] = [];
  note(path: string, what: string): void {
    if (this.problems.length < 200) this.problems.push(`${path}: ${what}`);
  }
}

const MAX_T = 4e9; // epoch seconds (year 2096)
const MAX_BYTES = 1e16;
const MAX_CORES = 4096;

function obj(c: Ctx, v: unknown, path: string): Obj {
  if (v !== null && typeof v === "object" && !Array.isArray(v)) return v as Obj;
  c.note(path, "not an object");
  return {};
}

function objOrNull(c: Ctx, o: Obj, k: string, path: string): Obj | null {
  const v = o[k];
  if (v === null) return null;
  if (v === undefined) {
    c.note(`${path}.${k}`, "missing");
    return null;
  }
  return obj(c, v, `${path}.${k}`);
}

function numV(c: Ctx, v: unknown, path: string, lo: number, hi: number): number | null {
  if (v === null) return null;
  if (v === undefined) {
    c.note(path, "missing");
    return null;
  }
  if (typeof v !== "number" || !Number.isFinite(v)) {
    c.note(path, "not a number");
    return null;
  }
  if (v < lo || v > hi) {
    c.note(path, "out of range");
    return Math.min(Math.max(v, lo), hi);
  }
  return v;
}

function n(c: Ctx, o: Obj, k: string, path: string, lo = 0, hi = MAX_BYTES): number | null {
  return numV(c, o[k], `${path}.${k}`, lo, hi);
}

function nReq(c: Ctx, o: Obj, k: string, path: string, lo = 0, hi = MAX_BYTES, dflt = 0): number {
  const v = n(c, o, k, path, lo, hi);
  if (v === null) {
    if (o[k] === null) c.note(`${path}.${k}`, "null");
    return dflt;
  }
  return v;
}

function strV(c: Ctx, v: unknown, path: string, cap: number): string | null {
  if (v === null) return null;
  if (v === undefined) {
    c.note(path, "missing");
    return null;
  }
  if (typeof v !== "string") {
    c.note(path, "not a string");
    return null;
  }
  if (v.length > cap) {
    c.note(path, "too long");
    return v.slice(0, cap) + "…";
  }
  return v;
}

function s(c: Ctx, o: Obj, k: string, path: string, cap = 200): string | null {
  return strV(c, o[k], `${path}.${k}`, cap);
}

function sReq(c: Ctx, o: Obj, k: string, path: string, cap = 200, dflt = ""): string {
  const v = s(c, o, k, path, cap);
  if (v === null && o[k] === null) c.note(`${path}.${k}`, "null");
  return v ?? dflt;
}

function b(c: Ctx, o: Obj, k: string, path: string): boolean {
  const v = o[k];
  if (typeof v === "boolean") return v;
  c.note(`${path}.${k}`, v === undefined ? "missing" : "not a boolean");
  return false;
}

function arr(c: Ctx, o: Obj, k: string, path: string, cap = 10000): unknown[] {
  const v = o[k];
  if (Array.isArray(v)) {
    if (v.length > cap) c.note(`${path}.${k}`, "too long");
    return v.slice(0, cap);
  }
  if (v !== null) c.note(`${path}.${k}`, v === undefined ? "missing" : "not a list");
  return [];
}

function oneOf<T extends string>(c: Ctx, o: Obj, k: string, path: string, allowed: readonly T[], dflt: T): T {
  const v = o[k];
  if (typeof v === "string" && (allowed as readonly string[]).includes(v)) return v as T;
  c.note(`${path}.${k}`, "unexpected value");
  return dflt;
}

function strList(c: Ctx, o: Obj, k: string, path: string, cap = 200, max = 200): string[] {
  return arr(c, o, k, path, max).map((x, i) => strV(c, x, `${path}.${k}[${i}]`, cap)).filter((x): x is string => x !== null);
}

function series(c: Ctx, o: Obj, k: string, path: string, hi: number): Series {
  const out: Series = [];
  for (const [i, p] of arr(c, o, k, path, 4000).entries()) {
    if (!Array.isArray(p) || p.length !== 2) {
      c.note(`${path}.${k}[${i}]`, "not a point");
      continue;
    }
    const t = numV(c, p[0], `${path}.${k}[${i}][0]`, 0, MAX_T);
    if (t === null) continue;
    out.push([t, numV(c, p[1], `${path}.${k}[${i}][1]`, 0, hi)]);
  }
  return out;
}

function envelope(c: Ctx, v: unknown, path: string): Obj {
  const o = obj(c, v, path);
  if (o.api !== 1) throw new ApiMismatch(`api ${String(o.api)}`);
  if (o.ok !== true) c.note(`${path}.ok`, "not true");
  return o;
}

// ------------------------------------------------------------------------------------------------ session
export function decodeSession(v: unknown, c = new Ctx()): Session {
  const p = "session";
  const o = envelope(c, v, p);
  const poll = obj(c, o.poll_ms, `${p}.poll_ms`);
  return {
    api: 1, ok: true,
    version: s(c, o, "version", p, 64),
    started: nReq(c, o, "started", p, 0, MAX_T),
    now: nReq(c, o, "now", p, 0, MAX_T),
    dev: b(c, o, "dev", p),
    scenario: s(c, o, "scenario", p, 40),
    host: sReq(c, o, "host", p, 200),
    stale_after_s: nReq(c, o, "stale_after_s", p, 1, 3600, 15),
    visible_after_s: nReq(c, o, "visible_after_s", p, 1, 3600, 10),
    poll_ms: {
      overview: nReq(c, poll, "overview", `${p}.poll_ms`, 500, 60000, 2000),
      job: nReq(c, poll, "job", `${p}.poll_ms`, 500, 60000, 2000),
      log: nReq(c, poll, "log", `${p}.poll_ms`, 250, 60000, 1000),
    },
    log_keep_bytes: nReq(c, o, "log_keep_bytes", p, 0, 1e9, 2097152),
    list_limit: nReq(c, o, "list_limit", p, 1, 100000, 100),
  };
}

// ------------------------------------------------------------------------------------------------ machines
export function decodeMac(v: unknown, c: Ctx, p = "mac"): MacCard {
  const o = obj(c, v, p);
  const cores = obj(c, o.cores, `${p}.cores`);
  const mem = obj(c, o.mem, `${p}.mem`);
  const bat = objOrNull(c, o, "battery", p);
  const ser = obj(c, o.series, `${p}.series`);
  const load = o.load === null ? null : arr(c, o, "load", p, 3).map((x, i) => numV(c, x, `${p}.load[${i}]`, 0, 1e5) ?? 0);
  const total = n(c, mem, "total", `${p}.mem`);
  let used = n(c, mem, "used", `${p}.mem`);
  let available = n(c, mem, "available", `${p}.mem`);
  if (__FAULT_MAC_SWAP_MEM__) [used, available] = [available, used];
  return {
    name: "This Mac",
    host: sReq(c, o, "host", p, 200),
    sampler: oneOf(c, o, "sampler", p, ["psutil", "stdlib", "fake"] as const, "stdlib"),
    t: n(c, o, "t", p, 0, MAX_T),
    stale: b(c, o, "stale", p),
    cpu_pct: n(c, o, "cpu_pct", p, 0, 100),
    per_core: arr(c, o, "per_core", p, 1024).map((x, i) => numV(c, x, `${p}.per_core[${i}]`, 0, 100)),
    load,
    cores: {
      logical: n(c, cores, "logical", `${p}.cores`, 0, MAX_CORES),
      physical: n(c, cores, "physical", `${p}.cores`, 0, MAX_CORES),
      perf: n(c, cores, "perf", `${p}.cores`, 0, MAX_CORES),
      eff: n(c, cores, "eff", `${p}.cores`, 0, MAX_CORES),
    },
    mem: {
      total, used, available,
      pressure: mem.pressure === null ? null : oneOf(c, mem, "pressure", `${p}.mem`, ["ok", "warn", "critical"] as const, "ok"),
    },
    battery: bat === null ? null : { on_battery: b(c, bat, "on_battery", `${p}.battery`), percent: n(c, bat, "percent", `${p}.battery`, 0, 100) },
    mu_version: s(c, o, "mu_version", p, 64),
    series: { cpu_pct: series(c, ser, "cpu_pct", `${p}.series`, 100), mem_used: series(c, ser, "mem_used", `${p}.series`, MAX_BYTES) },
  };
}

const NODE_STATES = ["connecting", "online", "offline", "refused", "cannot", "keychain", "error"] as const;
const ATTENTION = ["version", "last-stop", "disk", "autoupdate", "runner"] as const;

function decodeNodeError(c: Ctx, e: Obj, p: string): NodeError {
  return {
    exit: nReq(c, e, "exit", p, 0, 255, 1),
    error: sReq(c, e, "error", p, 64),
    message: sReq(c, e, "message", p, 1000),
    next: sReq(c, e, "next", p, 1000),
    retry_at: n(c, e, "retry_at", p, 0, MAX_T),
    auto_retry: b(c, e, "auto_retry", p),
    verb: sReq(c, e, "verb", p, 32),
  };
}

function decodeHello(c: Ctx, h: Obj, p: string): NodeHello {
  const disk = obj(c, h.disk, `${p}.disk`);
  const au = obj(c, h.autoupdate, `${p}.autoupdate`);
  const q = obj(c, h.queue, `${p}.queue`);
  return {
    t: nReq(c, h, "t", p, 0, MAX_T),
    protocol: n(c, h, "protocol", p, 0, 1000),
    key: s(c, h, "key", p, 100),
    mu_version: s(c, h, "mu_version", p, 64),
    mu_installed: strList(c, h, "mu_installed", p, 64, 50),
    cpu_model: s(c, h, "cpu_model", p, 200),
    cores_logical: n(c, h, "cores_logical", p, 0, MAX_CORES),
    cores_physical: n(c, h, "cores_physical", p, 0, MAX_CORES),
    mem_total: n(c, h, "mem_total", p),
    kernel: s(c, h, "kernel", p, 200),
    wsl: b(c, h, "wsl", p),
    disk: { free_gb: n(c, disk, "free_gb", `${p}.disk`, 0, 1e9), min_free_gb: n(c, disk, "min_free_gb", `${p}.disk`, 0, 1e9) },
    runner: s(c, h, "runner", p, 64),
    last_stop: s(c, h, "last_stop", p, 64),
    autoupdate: { enabled: b(c, au, "enabled", `${p}.autoupdate`), last_result: s(c, au, "last_result", `${p}.autoupdate`, 64) },
    queue: { running: n(c, q, "running", `${p}.queue`, 0, 1e6), queued: n(c, q, "queued", `${p}.queue`, 0, 1e6) },
    clock_offset_s: nReq(c, h, "clock_offset_s", p, -1e9, 1e9),
  };
}

function decodeStats(c: Ctx, st: Obj, p: string): NodeStats {
  const mem = obj(c, st.mem, `${p}.mem`);
  const psi = obj(c, st.psi, `${p}.psi`);
  const disk = obj(c, st.disk, `${p}.disk`);
  const q = obj(c, st.queue, `${p}.queue`);
  return {
    t: nReq(c, st, "t", p, 0, MAX_T),
    stale: b(c, st, "stale", p),
    cpu_pct: n(c, st, "cpu_pct", p, 0, 100),
    per_core: arr(c, st, "per_core", p, 1024).map((x, i) => numV(c, x, `${p}.per_core[${i}]`, 0, 100)),
    load: st.load === null ? null : arr(c, st, "load", p, 3).map((x, i) => numV(c, x, `${p}.load[${i}]`, 0, 1e5)),
    mem: {
      total: n(c, mem, "total", `${p}.mem`), avail: n(c, mem, "avail", `${p}.mem`), used: n(c, mem, "used", `${p}.mem`),
      cached: n(c, mem, "cached", `${p}.mem`), shmem: n(c, mem, "shmem", `${p}.mem`),
    },
    psi: { cpu_some10: n(c, psi, "cpu_some10", `${p}.psi`, 0, 100), mem_some10: n(c, psi, "mem_some10", `${p}.psi`, 0, 100) },
    disk: { free_gb: n(c, disk, "free_gb", `${p}.disk`, 0, 1e9), min_free_gb: n(c, disk, "min_free_gb", `${p}.disk`, 0, 1e9) },
    uptime_s: n(c, st, "uptime_s", p, 0, 1e10),
    queue: { running: n(c, q, "running", `${p}.queue`, 0, 1e6), queued: n(c, q, "queued", `${p}.queue`, 0, 1e6) },
    node_stale: b(c, st, "node_stale", p),
  };
}

export function decodeNode(v: unknown, c: Ctx, p = "node"): NodeCard {
  const o = obj(c, v, p);
  const err = objOrNull(c, o, "error", p);
  const hello = objOrNull(c, o, "hello", p);
  const stats = objOrNull(c, o, "stats", p);
  const ser = obj(c, o.series, `${p}.series`);
  const features = strList(c, o, "features", p, 32, 32);
  if (__FAULT_ASSUME_FEATURES__ && stats === null && hello !== null) throw new Error("node without stats");
  return {
    name: sReq(c, o, "name", p, 64, "?"),
    link: "relay",
    state: oneOf(c, o, "state", p, NODE_STATES, "error"),
    reconnecting: b(c, o, "reconnecting", p),
    features,
    old_node: b(c, o, "old_node", p),
    old_node_text: s(c, o, "old_node_text", p, 200),
    error: err === null ? null : decodeNodeError(c, err, `${p}.error`),
    offline_since: n(c, o, "offline_since", p, 0, MAX_T),
    hello: hello === null ? null : decodeHello(c, hello, `${p}.hello`),
    stats: stats === null ? null : decodeStats(c, stats, `${p}.stats`),
    series: { cpu_pct: series(c, ser, "cpu_pct", `${p}.series`, 100) },
    attention: arr(c, o, "attention", p, 20).map((x, i) => {
      const a = obj(c, x, `${p}.attention[${i}]`);
      return { kind: oneOf(c, a, "kind", `${p}.attention[${i}]`, ATTENTION, "runner"), text: sReq(c, a, "text", `${p}.attention[${i}]`, 300) };
    }),
    jobs_listed: nReq(c, o, "jobs_listed", p, 0, 1e6),
    more: b(c, o, "more", p),
    list_t: n(c, o, "list_t", p, 0, MAX_T),
    list_stale: b(c, o, "list_stale", p),
  };
}

// ------------------------------------------------------------------------------------------------ jobs
const JOB_STATES = ["queued", "running", "succeeded", "failed", "cancelled", "timeout", "oom", "interrupted", "unknown"] as const;
export const JOB_ID = /^[0-9]{8}-[0-9]{6}-[0-9a-z]{6}$/;

function decodeLimits(c: Ctx, l: Obj, p: string): Limits {
  return { mem_bytes: n(c, l, "mem_bytes", p), time_s: n(c, l, "time_s", p, 0, 1e9), cores: n(c, l, "cores", p, 0, MAX_CORES) };
}

function decodeUsage(c: Ctx, u: Obj, p: string): Usage {
  const ev = obj(c, u.mem_events, `${p}.mem_events`);
  return {
    cpu_s: n(c, u, "cpu_s", p, 0, 1e12),
    user_s: n(c, u, "user_s", p, 0, 1e12),
    sys_s: n(c, u, "sys_s", p, 0, 1e12),
    cores_avg: n(c, u, "cores_avg", p, 0, MAX_CORES),
    mem_peak_bytes: n(c, u, "mem_peak_bytes", p),
    mem_work_peak_bytes: n(c, u, "mem_work_peak_bytes", p),
    pids_peak: n(c, u, "pids_peak", p, 0, 1e9),
    mem_events: { max: n(c, ev, "max", `${p}.mem_events`, 0, 1e12), oom_kill: n(c, ev, "oom_kill", `${p}.mem_events`, 0, 1e12) },
    source: u.source === null ? null : oneOf(c, u, "source", p, ["cgroup", "runner-sampled"] as const, "runner-sampled"),
  };
}

function decodeLive(c: Ctx, l: Obj, p: string): LiveJob {
  return {
    t: nReq(c, l, "t", p, 0, MAX_T),
    stale: b(c, l, "stale", p),
    cpu_cores: n(c, l, "cpu_cores", p, 0, MAX_CORES),
    anon: n(c, l, "anon", p), file: n(c, l, "file", p), shmem: n(c, l, "shmem", p),
    peak: n(c, l, "peak", p), max: n(c, l, "max", p), pids: n(c, l, "pids", p, 0, 1e9),
    log_size: n(c, l, "log_size", p, 0, 1e15),
  };
}

export function decodeJob(v: unknown, c: Ctx, p = "job"): JobRow {
  const o = obj(c, v, p);
  const usage = objOrNull(c, o, "usage", p);
  const live = objOrNull(c, o, "live", p);
  const limits = objOrNull(c, o, "limits", p);
  let id = sReq(c, o, "job_id", p, 40);
  if (!JOB_ID.test(id)) {
    c.note(`${p}.job_id`, "not a job id");
    id = "";
  }
  return {
    job_id: id,
    short: sReq(c, o, "short", p, 16),
    node: sReq(c, o, "node", p, 64, "?"),
    name: s(c, o, "name", p, 200),
    project: s(c, o, "project", p, 200),
    project_id: s(c, o, "project_id", p, 200),
    mine: b(c, o, "mine", p),
    state: oneOf(c, o, "state", p, JOB_STATES, "unknown"),
    position: n(c, o, "position", p, 0, 1e6),
    attempt: n(c, o, "attempt", p, 0, 1e6),
    queued_at: n(c, o, "queued_at", p, 0, MAX_T),
    started_at: n(c, o, "started_at", p, 0, MAX_T),
    ended_at: n(c, o, "ended_at", p, 0, MAX_T),
    exit_code: n(c, o, "exit_code", p, -1e6, 1e6),
    note: s(c, o, "note", p, 500),
    log_size: n(c, o, "log_size", p, 0, 1e15),
    cancel_requested: b(c, o, "cancel_requested", p),
    usage: usage === null ? null : decodeUsage(c, usage, `${p}.usage`),
    live: live === null ? null : decodeLive(c, live, `${p}.live`),
    limits: limits === null ? null : decodeLimits(c, limits, `${p}.limits`),
    as_of: n(c, o, "as_of", p, 0, MAX_T),
    stale: b(c, o, "stale", p),
  };
}

function decodeMore(c: Ctx, o: Obj, p: string): Record<string, boolean> {
  const m = obj(c, o.more, `${p}.more`);
  const out: Record<string, boolean> = {};
  for (const [k, v] of Object.entries(m).slice(0, 64)) {
    if (typeof v === "boolean" && k.length <= 64) out[k] = v;
  }
  return out;
}

function jobsOf(c: Ctx, o: Obj, p: string): JobRow[] {
  return arr(c, o, "jobs", p, 2000).map((x, i) => decodeJob(x, c, `${p}.jobs[${i}]`)).filter((j) => j.job_id !== "");
}

export function decodeOverview(v: unknown, c = new Ctx()): Overview {
  const p = "overview";
  const o = envelope(c, v, p);
  const sum = obj(c, o.summary, `${p}.summary`);
  return {
    api: 1, ok: true,
    now: nReq(c, o, "now", p, 0, MAX_T),
    mac: decodeMac(o.mac, c, `${p}.mac`),
    nodes: arr(c, o, "nodes", p, 64).map((x, i) => decodeNode(x, c, `${p}.nodes[${i}]`)),
    jobs: jobsOf(c, o, p),
    more: decodeMore(c, o, p),
    summary: {
      nodes: nReq(c, sum, "nodes", `${p}.summary`, 0, 1e4), online: nReq(c, sum, "online", `${p}.summary`, 0, 1e4),
      running: nReq(c, sum, "running", `${p}.summary`, 0, 1e6), queued: nReq(c, sum, "queued", `${p}.summary`, 0, 1e6),
    },
  };
}

export function decodeJobsList(v: unknown, c = new Ctx()): JobsList {
  const p = "jobs";
  const o = envelope(c, v, p);
  return { api: 1, ok: true, now: nReq(c, o, "now", p, 0, MAX_T), jobs: jobsOf(c, o, p), more: decodeMore(c, o, p) };
}

export function decodeNodeAnswer(v: unknown, c = new Ctx()): NodeCard & { now: number } {
  const o = envelope(c, v, "nodeAnswer");
  return { ...decodeNode(o, c, "nodeAnswer"), now: numV(c, o.now, "nodeAnswer.now", 0, MAX_T) ?? 0 };
}

export function decodeMacAnswer(v: unknown, c = new Ctx()): MacCard & { now: number } {
  const o = envelope(c, v, "macAnswer");
  return { ...decodeMac(o, c, "macAnswer"), now: numV(c, o.now, "macAnswer.now", 0, MAX_T) ?? 0 };
}

export function decodeJobDetail(v: unknown, c = new Ctx()): JobDetail {
  const p = "detail";
  const o = envelope(c, v, p);
  const rec = objOrNull(c, o, "record", p);
  const run = objOrNull(c, o, "run", p);
  const idx = objOrNull(c, o, "index", p);
  const node = obj(c, o.node, `${p}.node`);
  const cmd = obj(c, o.commands, `${p}.commands`);
  const rp = `${p}.record`;
  return {
    api: 1, ok: true,
    now: nReq(c, o, "now", p, 0, MAX_T),
    job: decodeJob(o.job, c, `${p}.job`),
    record: rec === null ? null : {
      project_root: s(c, rec, "project_root", rp, 500),
      head: s(c, rec, "head", rp, 64),
      snapshot: s(c, rec, "snapshot", rp, 64),
      dirty: b(c, rec, "dirty", rp),
      mu_version: s(c, rec, "mu_version", rp, 64),
      argv: arr(c, rec, "argv", rp, 200).map((x, i) => strV(c, x, `${rp}.argv[${i}]`, 1000)),
      cwd: s(c, rec, "cwd", rp, 500),
      outputs: arr(c, rec, "outputs", rp, 200).map((x, i) => strV(c, x, `${rp}.outputs[${i}]`, 500)),
      limits: decodeLimits(c, obj(c, rec.limits, `${rp}.limits`), `${rp}.limits`),
      submitted: (() => {
        const su = obj(c, rec.submitted, `${rp}.submitted`);
        const sp = `${rp}.submitted`;
        return { at: n(c, su, "at", sp, 0, MAX_T), host: s(c, su, "host", sp, 200), user: s(c, su, "user", sp, 100), mu: s(c, su, "mu", sp, 64) };
      })(),
      accepted: n(c, rec, "accepted", rp, 0, MAX_T),
    },
    run: run === null ? null : {
      fetched_at: n(c, run, "fetched_at", `${p}.run`, 0, MAX_T),
      wall_s: n(c, run, "wall_s", `${p}.run`, 0, 1e10),
      outputs_missing: arr(c, run, "outputs_missing", `${p}.run`, 200).map((x, i) => strV(c, x, `${p}.run.outputs_missing[${i}]`, 500)),
    },
    index: idx === null ? null : {
      key: s(c, idx, "key", `${p}.index`, 100),
      submit_started: n(c, idx, "submit_started", `${p}.index`, 0, MAX_T),
      accepted: n(c, idx, "accepted", `${p}.index`, 0, MAX_T),
      fetched: n(c, idx, "fetched", `${p}.index`, 0, MAX_T),
    },
    node: {
      name: sReq(c, node, "name", `${p}.node`, 64, "?"),
      state: oneOf(c, node, "state", `${p}.node`, NODE_STATES, "error"),
      old_node: b(c, node, "old_node", `${p}.node`),
      metrics: b(c, node, "metrics", `${p}.node`),
    },
    commands: {
      status: sReq(c, cmd, "status", `${p}.commands`, 300),
      log: sReq(c, cmd, "log", `${p}.commands`, 300),
      fetch: sReq(c, cmd, "fetch", `${p}.commands`, 300),
      cancel: sReq(c, cmd, "cancel", `${p}.commands`, 300),
      node: sReq(c, cmd, "node", `${p}.commands`, 300),
    },
  };
}

const LOG_TEXT_CAP = 300 * 1024; // the server sends at most 256 kB per answer

export function decodeLogPiece(v: unknown, c = new Ctx()): LogPiece {
  const p = "log";
  const o = envelope(c, v, p);
  const err = objOrNull(c, o, "error", p);
  return {
    api: 1, ok: true,
    job_id: sReq(c, o, "job_id", p, 40),
    offset: nReq(c, o, "offset", p, 0, 1e15),
    next: nReq(c, o, "next", p, 0, 1e15),
    start: nReq(c, o, "start", p, 0, 1e15),
    size: nReq(c, o, "size", p, 0, 1e15),
    truncated_before: b(c, o, "truncated_before", p),
    text: sReq(c, o, "text", p, LOG_TEXT_CAP),
    following: b(c, o, "following", p),
    eof: b(c, o, "eof", p),
    error: err === null ? null : {
      exit: nReq(c, err, "exit", `${p}.error`, 0, 255, 1), error: sReq(c, err, "error", `${p}.error`, 64),
      message: sReq(c, err, "message", `${p}.error`, 1000), next: sReq(c, err, "next", `${p}.error`, 1000),
    },
    t: nReq(c, o, "t", p, 0, MAX_T),
  };
}

export function decodeJobMetrics(v: unknown, c = new Ctx()): JobMetrics {
  const p = "metrics";
  const o = envelope(c, v, p);
  const reason = o.reason === null ? null : oneOf(c, o, "reason", p, ["old-node", "unknown", "error"] as const, "error");
  return {
    api: 1, ok: true,
    job_id: sReq(c, o, "job_id", p, 40),
    available: b(c, o, "available", p),
    reason,
    samples: arr(c, o, "samples", p, 20000).map((x, i): MetricSample => {
      const m = obj(c, x, `${p}.samples[${i}]`);
      const sp = `${p}.samples[${i}]`;
      return {
        t: nReq(c, m, "t", sp, 0, MAX_T), cpu: n(c, m, "cpu", sp, 0, MAX_CORES), anon: n(c, m, "anon", sp),
        file: n(c, m, "file", sp), shmem: n(c, m, "shmem", sp), cur: n(c, m, "cur", sp), peak: n(c, m, "peak", sp),
        max: n(c, m, "max", sp), pids: n(c, m, "pids", sp, 0, 1e9), psi_mem: n(c, m, "psi_mem", sp, 0, 100),
      };
    }),
    complete: b(c, o, "complete", p),
    limit_mem: n(c, o, "limit_mem", p),
    limit_cores: n(c, o, "limit_cores", p, 0, MAX_CORES),
    t: nReq(c, o, "t", p, 0, MAX_T),
  };
}

export type { JobState, NodeState };
