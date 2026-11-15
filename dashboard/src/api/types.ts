// The shapes of the dashboard's API (src/mu/dashboard.py), as the server sends them. The golden examples are
// tools/test-dashboard/fixtures/api-*.json (D38): the decoders (src/api/decode.ts, to come) are tested on those.
// Times are epoch seconds on this Mac's clock (node times are converted by the server); sizes are bytes; every value
// from a node or a sampler carries its time `t` and `stale` (older than session.stale_after_s).

export type Epoch = number;

export interface Envelope {
  api: 1;
  ok: boolean;
}

export interface ApiError extends Envelope {
  ok: false;
  error: string; // "token" (401), "host" (403), "not-found", "no-such-job", "bad-job-id", "bad-offset", "internal", …
  message: string;
}

export interface Session extends Envelope {
  version: string | null;
  started: Epoch;
  now: Epoch;
  dev: boolean;
  scenario: string | null;
  host: string;
  stale_after_s: number;
  visible_after_s: number;
  poll_ms: { overview: number; job: number; log: number };
  log_keep_bytes: number;
  list_limit: number;
}

export type Series = [Epoch, number | null][];

export interface MacCard {
  name: "This Mac";
  host: string;
  sampler: "psutil" | "stdlib" | "fake";
  t: Epoch | null;
  stale: boolean;
  cpu_pct: number | null;
  per_core: (number | null)[];
  load: number[] | null;
  cores: { logical: number | null; physical: number | null; perf: number | null; eff: number | null };
  mem: { total: number | null; available: number | null; used: number | null; pressure: "ok" | "warn" | "critical" | null };
  battery: { on_battery: boolean; percent: number | null } | null;
  mu_version: string | null;
  series: { cpu_pct: Series; mem_used: Series };
}

export type NodeState = "connecting" | "online" | "offline" | "refused" | "cannot" | "keychain" | "error";

export interface NodeError {
  exit: number; // 4 unreachable (grey, retried), 5 refused (red, never retried), 6 cannot (amber), 1 other
  error: string;
  message: string;
  next: string;
  retry_at: Epoch | null;
  auto_retry: boolean;
  verb: string;
}

export interface NodeHello {
  t: Epoch;
  protocol: number | null;
  key: string | null;
  mu_version: string | null;
  mu_installed: string[];
  cpu_model: string | null;
  cores_logical: number | null;
  cores_physical: number | null;
  mem_total: number | null;
  kernel: string | null;
  wsl: boolean;
  disk: { free_gb: number | null; min_free_gb: number | null };
  runner: string | null;
  last_stop: string | null;
  autoupdate: { enabled: boolean; last_result: string | null };
  queue: { running: number | null; queued: number | null };
  clock_offset_s: number;
}

export interface NodeStats {
  t: Epoch;
  stale: boolean;
  cpu_pct: number | null;
  per_core: (number | null)[];
  load: (number | null)[] | null;
  mem: { total: number | null; avail: number | null; used: number | null; cached: number | null; shmem: number | null };
  psi: { cpu_some10: number | null; mem_some10: number | null };
  disk: { free_gb: number | null; min_free_gb: number | null };
  uptime_s: number | null;
  queue: { running: number | null; queued: number | null; held: number | null; paused: number | null; express: number | null };
  node_stale: boolean;
}

export interface NodeCard {
  name: string;
  link: "relay";
  state: NodeState;
  reconnecting: boolean;
  features: string[];
  old_node: boolean;
  old_node_text: string | null;
  error: NodeError | null;
  offline_since: Epoch | null;
  hello: NodeHello | null;
  stats: NodeStats | null; // null for an old node (no `stats` until phase 1b reaches it)
  series: { cpu_pct: Series };
  attention: { kind: "version" | "last-stop" | "disk" | "autoupdate" | "runner"; text: string }[];
  jobs_listed: number;
  more: boolean; // the node has more finished jobs than list_limit: "older jobs: mu status"
  list_t: Epoch | null;
  list_stale: boolean;
}

export type JobState =
  | "queued" | "held" | "running" | "succeeded" | "failed" | "cancelled" | "timeout" | "oom" | "interrupted" | "unknown";

export interface Usage {
  cpu_s: number | null;
  user_s: number | null;
  sys_s: number | null;
  cores_avg: number | null;
  mem_peak_bytes: number | null; // the cgroup's peak, includes page cache
  mem_work_peak_bytes: number | null; // sampled anon + shmem: the figure to compare
  pids_peak: number | null;
  mem_events: { max: number | null; oom_kill: number | null };
  source: "cgroup" | "runner-sampled" | null; // runner-sampled: up to 5 s old
}

export interface LiveJob {
  t: Epoch;
  stale: boolean;
  cpu_cores: number | null;
  anon: number | null;
  file: number | null;
  shmem: number | null;
  peak: number | null;
  max: number | null; // the job's memory limit
  pids: number | null;
  log_size: number | null;
  grant_cores: number | null; // the cores granted (phase 1b)
  class: GrantClass | null;
  paused: boolean; // frozen (scheduler release 2): its memory is real, its cpu about 0
}

export type GrantClass = "normal" | "express" | "borrowing";
export type Band = "urgent" | "high" | "normal" | "low";
// Display states (contract §2): a paused or resuming job's `state` stays "running"
export type DisplayState = "paused" | "resuming";

export interface Who {
  by: string | null;
  at: Epoch | null;
}

export interface Paused {
  phase: "freezing" | "frozen" | "thawing" | null;
  since: Epoch | null;
  until: Epoch | null; // resumes by itself then
  by: string | null;
  why: "hand" | "auto" | null;
  for_job: string | null; // an auto-pause: the urgent job it made room for
  held_mem_bytes: number | null; // the memory it keeps while frozen
  lent_to: string[];
  resume_asked: Who | null;
  resumes_by: Epoch | null; // once asked: the latest borrower's end bound
}

export interface Grant {
  cores: number | null;
  cpus: string | null;
  mem_bytes: number | null;
  time_s: number | null;
  class: GrantClass | null;
  borrowed: { from: string | null; cpus: string | null; mem_bytes: number | null; until: Epoch | null }[];
}

export interface JobTime {
  limit_s: number | null;
  run_s: number | null; // run time, paused time not counted
  paused_s: number | null;
  budget_left_s: number | null;
}

export interface Limits {
  mem_bytes: number | null;
  time_s: number | null;
  cores: number | null;
}

export interface JobRow {
  job_id: string;
  short: string;
  node: string;
  name: string | null;
  project: string | null;
  project_id: string | null;
  mine: boolean; // submitted from this Mac (in jobs.index): the "from: this Mac" filter
  state: JobState;
  position: number | null;
  attempt: number | null;
  queued_at: Epoch | null;
  started_at: Epoch | null;
  ended_at: Epoch | null;
  exit_code: number | null;
  note: string | null;
  log_size: number | null;
  cancel_requested: boolean;
  usage: Usage | null; // finished jobs on a node with the `usage` feature
  live: LiveJob | null; // running jobs on a node with `stats`
  limits: Limits | null; // from this Mac's .mu/jobs record (mine only)
  as_of: Epoch | null; // the node list this row came from
  stale: boolean;
  // the scheduler's fields (release 1: band, held, place, class, grant, ends_by; release 2: the rest); null when absent
  band: Band | null;
  held: Who | null;
  place: number | null;
  class: GrantClass | null;
  grant: Grant | null;
  ends_by: Epoch | null;
  pausable: boolean | null;
  paused: Paused | null;
  time: JobTime | null;
  run_s: number | null; // finished jobs
  paused_s: number | null;
  display: DisplayState | null; // derived in decode.ts, never sent
}

export interface Overview extends Envelope {
  now: Epoch;
  mac: MacCard;
  nodes: NodeCard[];
  jobs: JobRow[]; // sorted: running, queued (by position), then finished, newest first
  more: Record<string, boolean>;
  summary: { nodes: number; online: number; running: number; queued: number; held: number; paused: number };
}

export interface JobsList extends Envelope {
  now: Epoch;
  jobs: JobRow[];
  more: Record<string, boolean>;
}

export interface JobDetail extends Envelope {
  now: Epoch;
  job: JobRow;
  record: {
    project_root: string | null;
    head: string | null;
    snapshot: string | null;
    dirty: boolean;
    mu_version: string | null;
    argv: (string | null)[];
    cwd: string | null;
    outputs: (string | null)[];
    limits: Limits;
    submitted: { at: Epoch | null; host: string | null; user: string | null; mu: string | null };
    accepted: Epoch | null;
  } | null;
  run: { fetched_at: Epoch | null; wall_s: number | null; outputs_missing: (string | null)[] } | null;
  index: { key: string | null; submit_started: Epoch | null; accepted: Epoch | null; fetched: Epoch | null } | null;
  node: { name: string; state: NodeState; old_node: boolean; metrics: boolean };
  commands: { status: string; log: string; fetch: string; cancel: string; node: string; resume: string; release: string; front: string };
}

export interface LogPiece extends Envelope {
  job_id: string;
  offset: number; // the byte offset this text starts at (may be > the asked offset when truncated_before)
  next: number; // ask for this offset next
  start: number; // the oldest byte the server still holds
  size: number; // the bytes the server has seen so far
  truncated_before: boolean; // the asked offset is older than the server's 2 MB: "earlier lines: mu log <id>"
  text: string; // control characters removed; render as text only
  following: boolean;
  eof: boolean;
  error: Omit<NodeError, "retry_at" | "auto_retry" | "verb"> | null;
  t: Epoch;
}

export interface MetricSample {
  t: Epoch;
  cpu: number | null; // cores
  anon: number | null;
  file: number | null;
  shmem: number | null;
  cur: number | null;
  peak: number | null;
  max: number | null;
  pids: number | null;
  psi_mem: number | null;
  paused: boolean; // taken while the job was frozen: a break in the cores, a hatched span
}

export interface JobMetrics extends Envelope {
  job_id: string;
  available: boolean;
  reason: "old-node" | "unknown" | "error" | null;
  samples: MetricSample[]; // every 5 s of the whole run; draw a gap where two samples are > 2 steps apart
  complete: boolean;
  limit_mem: number | null;
  limit_cores: number | null;
  t: Epoch;
}
