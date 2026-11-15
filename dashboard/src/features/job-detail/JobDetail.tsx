// The job detail (docs/design/dashboard.md §5.1): the header, the log as it grows, the resources over the whole run
// (or, on an old node, elapsed only), the run record and the `mu` commands to copy. Opened by #job=<id>.
import { ArrowLeft, CircleArrowUp, Clock3, CloudOff, FileQuestion, ShieldX } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiFailed } from "../../api/client";
import { useJob, useJobMetrics, useOverview } from "../../api/queries";
import type { JobDetail as Detail, JobRow, MetricSample } from "../../api/types";
import { isStale, useNow } from "../../lib/clock";
import { bytes, clock, duration, fullTime, num, shortDuration } from "../../lib/format";
import { noteText } from "../jobs/JobTable";
import { oldNodeLine } from "../machines/MachineCards";
import { closeJob } from "../../lib/route";
import { Banner } from "../../ui/Banner";
import { Card, SectionTitle } from "../../ui/Card";
import { CopyCommand } from "../../ui/CopyCommand";
import { EmptyState } from "../../ui/EmptyState";
import { Meter } from "../../ui/Meter";
import { Skeleton } from "../../ui/Skeleton";
import { BandChip, JobStateBadge } from "../../ui/StateBadge";
import { hm, schedText, timeLabel } from "../../lib/sched";
import { timeBar } from "../../lib/series";
import { LogView } from "./LogView";
import { ResourceChart } from "./ResourceChart";

function BackLink() {
  // a real link (it can open in a new tab and is announced as a link); a plain click goes back in place
  return (
    <a
      href="#"
      onClick={(e) => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
        e.preventDefault();
        closeJob();
      }}
      className="inline-flex items-center gap-1 rounded-sm text-sm text-text-muted hover:text-text"
    >
      <ArrowLeft aria-hidden="true" size={14} />
      Jobs
    </a>
  );
}

function Meta({ k, children, mono = false }: { k: string; children: ReactNode; mono?: boolean }) {
  return (
    <div className="flex min-w-0 items-baseline gap-1.5">
      <dt className="shrink-0 text-xs text-text-muted">{k}</dt>
      <dd className={`min-w-0 truncate text-[13px] tabular-nums text-text ${mono ? "font-mono" : ""}`}>{children}</dd>
    </div>
  );
}

const FINISHED = new Set(["succeeded", "failed", "cancelled", "timeout", "oom", "interrupted", "unknown"]);

/** The job's progress at a glance, beside its state: "5m10s of 12h00m", "waiting 2m · #1 in the queue", "41m12s · exit 0". */
function Progress({ j, now }: { j: Detail["job"]; now: number }) {
  const tb = timeBar(j, now);
  if (j.state === "running" && j.started_at !== null && tb) {
    return (
      <span className="whitespace-nowrap text-base tabular-nums text-text" data-testid="progress">
        <span data-testid="elapsed">{duration(tb.run_s)}</span>
        {tb.limit_s !== null && <span className="text-text-muted"> of {shortDuration(tb.limit_s)}</span>}
      </span>
    );
  }
  if (j.state === "queued" || j.state === "held") {
    return (
      <span className="whitespace-nowrap text-base tabular-nums text-text" data-testid="progress">
        waiting {j.queued_at !== null ? shortDuration(now - j.queued_at) : "—"}
        {j.position !== null && <span className="text-text-muted"> · #{j.position} in the queue</span>}
      </span>
    );
  }
  if (j.started_at !== null && j.ended_at !== null) {
    return (
      <span className="whitespace-nowrap text-base tabular-nums text-text" data-testid="progress">
        <span data-testid="elapsed">{duration(j.ended_at - j.started_at)}</span>
        {j.exit_code !== null && <span className="text-text-muted"> · exit {j.exit_code}</span>}
        {j.run_s !== null && j.paused_s !== null && j.paused_s > 0 && (
          <span className="text-text-muted" data-testid="finished-run"> · {shortDuration(j.run_s)} run · paused {shortDuration(j.paused_s)}</span>
        )}
      </span>
    );
  }
  return null;
}

/** The commands to copy, the next step first and orange: follow a running job's log, fetch a finished job's outputs,
 * wait for a queued one. */
function Commands({ d }: { d: Detail }) {
  const j = d.job;
  const c = d.commands;
  const logOnce = c.log.replace(/ -f\b/, "");
  if (j.display) {   // paused or resuming: the page stays read-only, it gives the command
    return (
      <div className="flex flex-wrap gap-2" data-testid="commands">
        <CopyCommand cmd={c.resume} primary what="resume" />
        <CopyCommand cmd={c.log} what="log" />
        <CopyCommand cmd={c.status} what="status" />
      </div>
    );
  }
  if (j.state === "held") {
    return (
      <div className="flex flex-wrap gap-2" data-testid="commands">
        <CopyCommand cmd={c.release} primary what="release" />
        <CopyCommand cmd={c.cancel} what="cancel" />
      </div>
    );
  }
  if (j.state === "running") {
    return (
      <div className="flex flex-wrap gap-2" data-testid="commands">
        <CopyCommand cmd={c.log} primary what="log" />
        <CopyCommand cmd={c.fetch} what="fetch" />
        <CopyCommand cmd={c.cancel} what="cancel" />
        <CopyCommand cmd={c.status} what="status" />
      </div>
    );
  }
  if (j.state === "queued") {
    return (
      <div className="flex flex-wrap gap-2" data-testid="commands">
        <CopyCommand cmd={`${c.status} --wait --timeout 500`} primary what="wait" />
        <CopyCommand cmd={c.front} what="move to the front" />
        <CopyCommand cmd={c.cancel} what="cancel" />
      </div>
    );
  }
  return (
    <div className="flex flex-wrap gap-2" data-testid="commands">
      <CopyCommand cmd={c.fetch} primary={FINISHED.has(j.state)} what="fetch" />
      <CopyCommand cmd={logOnce} what="log" />
      <CopyCommand cmd={c.status} what="status" />
    </div>
  );
}

function Header({ d, now }: { d: Detail; now: number }) {
  const j = d.job;
  const r = d.record;
  const limits = j.limits ?? r?.limits ?? null;
  const tb = timeBar(j, now);
  const frac = tb && tb.limit_s ? Math.min(1, Math.max(0, tb.run_s / tb.limit_s)) : null;
  const sched = schedText(j, now);
  return (
    <div className="flex flex-col gap-3" data-testid="job-header">
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <BackLink />
          <h2 className="min-w-0 truncate text-lg font-semibold text-text" title={j.name ?? ""}>{j.name ?? "(no name)"}</h2>
          <JobStateBadge state={j.state} position={j.state === "queued" ? null : j.position} display={j.display} />
          <BandChip band={j.band} />
          <Progress j={j} now={now} />
          <span className="whitespace-nowrap text-sm text-text-muted">· on <span className="text-text">{d.node.name}</span></span>
          {j.cancel_requested && <span className="text-xs text-text-muted">cancel asked</span>}
        </div>
        {frac !== null && tb && (
          <div className="flex flex-col gap-1">
            <div className="h-0.5 w-full overflow-hidden rounded-full bg-border" aria-hidden="true" data-testid="time-bar">
              <div className="h-full bg-text" style={{ width: `${(frac * 100).toFixed(2)}%` }} />
            </div>
            <p className="text-xs tabular-nums text-text-muted" data-testid="time-label">
              {timeLabel(tb.run_s, tb.limit_s, tb.paused_s)}
              {j.display === "paused" && j.paused?.until !== null && j.paused?.until !== undefined && <> · resumes by itself at {hm(j.paused.until, now)}</>}
            </p>
          </div>
        )}
      </div>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-1.5 md:grid-cols-[repeat(auto-fill,minmax(200px,1fr))]">
        <Meta mono k="id">{j.job_id}</Meta>
        {(j.project || j.project_id) && (
          <Meta k="project">
            {j.project ?? j.project_id}
            {r?.head && <> @ {r.head.slice(0, 7)}{r.dirty ? " (dirty)" : ""}</>}
          </Meta>
        )}
        {r?.mu_version && <Meta k="μ">{r.mu_version}</Meta>}
        {j.attempt !== null && <Meta k="attempt">{j.attempt}</Meta>}
        {d.index?.key && <Meta mono k="key">{d.index.key}</Meta>}
        {j.queued_at !== null && <Meta k="queued"><span title={fullTime(j.queued_at)}>{clock(j.queued_at, now)}</span></Meta>}
        {j.started_at !== null && <Meta k="started"><span title={fullTime(j.started_at)}>{clock(j.started_at, now)}</span></Meta>}
        {j.ended_at !== null && <Meta k="ended"><span title={fullTime(j.ended_at)}>{clock(j.ended_at, now)}</span></Meta>}
        {limits && (
          <Meta k="limits">
            {limits.mem_bytes !== null ? bytes(limits.mem_bytes, 0) : "—"} · {limits.time_s !== null ? shortDuration(limits.time_s) : "—"} · {limits.cores !== null ? `${limits.cores} cores` : "—"}
          </Meta>
        )}
      </dl>
      {sched ? <p className="text-sm text-text-muted" data-testid="sched-text">{sched}</p>
        : j.note && <p className="text-sm text-text-muted">{noteText(j.state, j.note)}</p>}
      <Commands d={d} />
    </div>
  );
}

function liveSample(j: JobRow): MetricSample | null {
  const l = j.live;
  if (!l) return null;
  return { t: l.t, cpu: l.cpu_cores, anon: l.anon, file: l.file, shmem: l.shmem, cur: null, peak: l.peak, max: l.max, pids: l.pids, psi_mem: null, paused: l.paused };
}

function Resources({ d }: { d: Detail }) {
  const j = d.job;
  const now = useNow();
  const running = j.state === "running";
  const can = d.node.metrics && !d.node.old_node && j.state !== "queued" && j.state !== "held";
  const m = useJobMetrics(j.job_id, running, can);
  const ov = useOverview();
  const card = ov.data?.nodes.find((n) => n.name === d.node.name);
  // live points between metrics reads come from the job row (the node's watch, via the overview's figures)
  const live = useRef<MetricSample[]>([]);
  const [, setTick] = useState(0);
  const ls = liveSample(j);
  useEffect(() => {
    if (!ls || ls.t === live.current[live.current.length - 1]?.t) return;
    live.current = [...live.current, ls].slice(-2000);
    setTick((x) => x + 1);
  }, [ls?.t]); // eslint-disable-line react-hooks/exhaustive-deps
  const samples = useMemo(() => {
    const base = m.data?.samples ?? [];
    const last = base.length ? base[base.length - 1]!.t : -Infinity;
    return base.concat(live.current.filter((s) => s.t > last));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [m.data, live.current.length]);

  if (d.node.old_node || m.data?.reason === "old-node") {
    return (
      <div className="flex flex-col gap-2 px-3 py-4" data-testid="resources-old-node">
        <p className="flex items-start gap-1.5 text-sm text-text">
          <CircleArrowUp aria-hidden="true" size={14} className="mt-0.5 shrink-0 text-attention" />
          <span>{oldNodeLine(d.node.name, card?.hello?.mu_version ?? null, ov.data?.mac.mu_version ?? null)}</span>
        </p>
        <p className="text-sm text-text-muted">This job has no resource chart; its state, log and times are shown.</p>
        <CopyCommand cmd={`mu compute status ${d.node.name}`} what="node status" />
      </div>
    );
  }
  if (j.state === "queued") return <p className="px-3 py-6 text-sm text-text-muted">The job is queued: the chart starts when it runs.</p>;
  if (!d.node.metrics) return <p className="px-3 py-6 text-sm text-text-muted">This node does not record job metrics.</p>;
  if (m.isPending) return <div className="px-3 py-3"><Skeleton lines={4} label="reading the job's metrics" /></div>;
  if (m.data && !m.data.available && samples.length === 0) {
    return <p className="px-3 py-6 text-sm text-text-muted">No resource samples for this job{m.data.reason === "error" ? ": the node could not be read" : ""}.</p>;
  }
  const liveStale = running && j.live !== null && (j.live.stale || isStale(j.live.t, now));
  const lim = m.data?.limit_mem ?? j.live?.max ?? j.limits?.mem_bytes ?? null;
  const limC = m.data?.limit_cores ?? j.limits?.cores ?? null;
  const work = j.live ? (j.live.anon ?? 0) + (j.live.shmem ?? 0) : null;
  return (
    <div className="flex flex-col">
      {running && j.live && (
        <div className="flex flex-col gap-2 border-b border-border px-3 py-3" data-testid="live-figures">
          <Meter label="cores" value={j.live.cpu_cores} max={limC} warnAt={Infinity} stale={liveStale} text={`${num(j.live.cpu_cores)}${limC !== null ? ` / ${limC}` : ""}`} />
          <Meter label="mem" value={work} max={lim} stale={liveStale} text={`${bytes(work)} / ${bytes(lim, 0)}`} />
          {liveStale && <p className="text-xs text-stale">stale · as of {clock(j.live.t, now)}: the chart stops until the node answers again</p>}
        </div>
      )}
      <ResourceChart samples={samples} limitMem={lim} limitCores={limC} label={j.name ?? j.job_id} />
      {j.usage && <Usage j={j} />}
    </div>
  );
}

function Usage({ j }: { j: JobRow }) {
  const u = j.usage!;
  return (
    <div className="flex flex-col gap-1.5 border-t border-border px-3 py-3" data-testid="usage">
    <dl className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-x-6 gap-y-1.5">
      <Meta k="CPU time">{duration(u.cpu_s)}</Meta>
      <Meta k="cores used">{num(u.cores_avg)}</Meta>
      <Meta k="peak working">{bytes(u.mem_work_peak_bytes)}</Meta>
      <Meta k="peak (cgroup)">{bytes(u.mem_peak_bytes)}*{u.source === "runner-sampled" ? "~" : ""}</Meta>
      {u.mem_events.oom_kill !== null && u.mem_events.oom_kill > 0 && <Meta k="OOM kills">{u.mem_events.oom_kill}</Meta>}
      <Meta k="processes, peak">{u.pids_peak ?? "—"}</Meta>
    </dl>
    <p className="text-xs text-text-muted">
      * includes file cache{u.source === "runner-sampled" ? " · ~ last sample, up to 5 s old" : ""}
    </p>
    </div>
  );
}

function Record({ d }: { d: Detail }) {
  const r = d.record;
  return (
    <Card label="record">
      <SectionTitle>Record</SectionTitle>
      {r === null ? (
        <p className="px-3 py-3 text-sm text-text-muted">
          No submit record on this Mac for this job (submitted from another workstation, or before the dashboard): the
          node's project id is <span className="font-mono text-text">{d.job.project_id ?? "—"}</span>.
        </p>
      ) : (
        <dl className="grid grid-cols-[repeat(auto-fill,minmax(260px,1fr))] gap-x-6 gap-y-1.5 px-3 py-3">
          {r.project_root && <Meta mono k="project">{r.project_root}</Meta>}
          {r.head && <Meta mono k="head">{r.head.slice(0, 12)}{r.dirty ? " (dirty)" : ""}</Meta>}
          {r.snapshot && <Meta mono k="snapshot">{r.snapshot.slice(0, 12)}</Meta>}
          {r.cwd !== null && <Meta mono k="cwd">{r.cwd || "."}</Meta>}
          {r.submitted.at !== null && <Meta k="submitted">{fullTime(r.submitted.at)}</Meta>}
          {(r.submitted.user || r.submitted.host) && <Meta k="by">{r.submitted.user ?? "?"}@{r.submitted.host ?? "?"}</Meta>}
          {d.run?.fetched_at != null && <Meta k="fetched">{fullTime(d.run.fetched_at)}</Meta>}
          {r.outputs.length > 0 && <Meta mono k="outputs">{r.outputs.filter(Boolean).join("  ")}</Meta>}
          <div className="col-span-full flex min-w-0 items-baseline gap-1.5">
            <dt className="shrink-0 text-xs text-text-muted">command</dt>
            <dd className="min-w-0 overflow-x-auto whitespace-pre font-mono text-[13px] text-text">{r.argv.map((a) => a ?? "").join(" ")}</dd>
          </div>
        </dl>
      )}
      <div className="flex flex-wrap items-center gap-2 border-t border-border px-3 py-2 text-xs text-text-muted">
        the node: <CopyCommand cmd={d.commands.node} what="node status" />
      </div>
    </Card>
  );
}

function NodeBanner({ d }: { d: Detail }) {
  const s = d.node.state;
  if (s === "offline" || s === "connecting") {
    return (
      <Banner tone="muted" icon={CloudOff} title={`${d.node.name} is offline: the figures below are as of its last answer`} testid="detail-offline">
        The job keeps running on the node; the page catches up when the node answers again.
      </Banner>
    );
  }
  if (s === "refused") {
    return <Banner tone="failed" icon={ShieldX} title={`${d.node.name} refused the connection: never retried automatically`} testid="detail-refused">See the node's card on the jobs page for the CLI's message and a retry button.</Banner>;
  }
  return null;
}

export function JobDetailView({ id }: { id: string }) {
  const q = useJob(id);
  const now = useNow();
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [id]);
  if (q.isPending) {
    return (
      <div className="flex flex-col gap-4" data-testid="job-detail-loading">
        <BackLink />
        <Skeleton lines={3} label="loading the job" />
        <div className="h-64 rounded-md border border-border bg-surface" />
      </div>
    );
  }
  if (!q.data) {
    const e = q.error;
    const missing = e instanceof ApiFailed && (e.status === 404 || e.status === 400);
    return (
      <div className="flex flex-col gap-4">
        <BackLink />
        <Card>
          <EmptyState icon={FileQuestion} title={missing ? "No node lists this job (yet)" : "The job could not be read"}>
            <p>
              <span className="font-mono text-text">{id}</span>
              {missing ? " is not in any paired node's list. A job just submitted appears within seconds." : ""}
            </p>
            <CopyCommand cmd={`mu status ${id}`} primary what="status" />
          </EmptyState>
        </Card>
      </div>
    );
  }
  const d = q.data;
  return (
    <div className="flex flex-col gap-4" data-testid="job-detail" data-job={d.job.job_id} data-state={d.job.state}>
      <Header d={d} now={now} />
      <NodeBanner d={d} />
      {d.job.state === "queued" ? (
        <Card label="log and resources">
          <p className="flex items-center gap-2 px-3 py-6 text-sm text-text-muted" data-testid="queued-note">
            <Clock3 aria-hidden="true" size={14} className="shrink-0" />
            <span>
              Queued{d.job.position !== null ? <>, <span className="text-text">#{d.job.position}</span></> : ""} on {d.node.name}
              {d.job.queued_at !== null && <>, waiting <span className="tabular-nums text-text">{shortDuration(now - d.job.queued_at)}</span></>}. The log and the
              chart start when it runs.
            </span>
          </p>
        </Card>
      ) : (
        <div className="grid gap-4 min-[1200px]:grid-cols-[7fr_5fr]">
          <LogView id={d.job.job_id} queued={false} logCmd={d.commands.log} />
          <Card label="resources">
            <SectionTitle right={<span className="text-xs text-text-muted">whole run</span>}>Resources</SectionTitle>
            <Resources d={d} />
          </Card>
        </div>
      )}
      <Record d={d} />
    </div>
  );
}
