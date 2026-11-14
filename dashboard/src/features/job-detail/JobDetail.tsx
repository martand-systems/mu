// The job detail (docs/design/dashboard.md §5.1): the header, the log as it grows, the resources over the whole run
// (or, on an old node, elapsed only), the run record and the `mu` commands to copy. Opened by #job=<id>.
import { ArrowLeft, CircleArrowUp, CloudOff, FileQuestion, ShieldX } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiFailed } from "../../api/client";
import { useJob, useJobMetrics } from "../../api/queries";
import type { JobDetail as Detail, JobRow, MetricSample } from "../../api/types";
import { isStale, useNow } from "../../lib/clock";
import { bytes, clock, duration, fullTime, num, shortDuration } from "../../lib/format";
import { closeJob } from "../../lib/route";
import { Banner } from "../../ui/Banner";
import { Card, SectionTitle } from "../../ui/Card";
import { CopyCommand } from "../../ui/CopyCommand";
import { EmptyState } from "../../ui/EmptyState";
import { Meter } from "../../ui/Meter";
import { Skeleton } from "../../ui/Skeleton";
import { JobStateBadge } from "../../ui/StateBadge";
import { LogView } from "./LogView";
import { ResourceChart } from "./ResourceChart";

function BackLink() {
  return (
    <button type="button" onClick={closeJob} className="inline-flex items-center gap-1 rounded-sm text-sm text-text-muted hover:text-text">
      <ArrowLeft aria-hidden="true" size={14} />
      Jobs
    </button>
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

function Header({ d, now }: { d: Detail; now: number }) {
  const j = d.job;
  const r = d.record;
  const elapsed = j.state === "running" && j.started_at !== null ? now - j.started_at : j.started_at !== null && j.ended_at !== null ? j.ended_at - j.started_at : null;
  const wait = j.state === "queued" && j.queued_at !== null ? now - j.queued_at : null;
  const limits = j.limits ?? r?.limits ?? null;
  return (
    <div className="flex flex-col gap-3" data-testid="job-header">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <BackLink />
        <h2 className="min-w-0 truncate text-lg font-semibold text-text" title={j.name ?? ""}>{j.name ?? "(no name)"}</h2>
        <JobStateBadge state={j.state} position={j.position} />
        {j.cancel_requested && <span className="text-xs text-text-muted">cancel asked</span>}
        <span className="ml-auto text-sm text-text-muted">
          {d.node.name} · via relay
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <CopyCommand cmd={j.job_id} label="copy id" />
      </div>
      <dl className="grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-x-6 gap-y-1.5">
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
        {wait !== null && <Meta k="waiting">{shortDuration(wait)}{j.position !== null && ` · place ${j.position} in the queue`}</Meta>}
        {elapsed !== null && <Meta k={j.state === "running" ? "elapsed" : "wall"}><span data-testid="elapsed">{duration(elapsed)}</span></Meta>}
        {j.exit_code !== null && <Meta k="exit">{j.exit_code}</Meta>}
        {limits && (
          <Meta k="limits">
            {limits.mem_bytes !== null ? bytes(limits.mem_bytes, 0) : "—"} · {limits.time_s !== null ? shortDuration(limits.time_s) : "—"} · {limits.cores !== null ? `${limits.cores} cores` : "—"}
          </Meta>
        )}
      </dl>
      {j.note && <p className="text-sm text-text-muted"><span className="text-text">{j.state}:</span> {j.note}</p>}
      <div className="flex flex-wrap gap-2">
        <CopyCommand cmd={d.commands.log} primary label="copy" />
        <CopyCommand cmd={d.commands.fetch} />
        {(j.state === "running" || j.state === "queued") && <CopyCommand cmd={d.commands.cancel} />}
        <CopyCommand cmd={d.commands.status} />
      </div>
    </div>
  );
}

function liveSample(j: JobRow): MetricSample | null {
  const l = j.live;
  if (!l) return null;
  return { t: l.t, cpu: l.cpu_cores, anon: l.anon, file: l.file, shmem: l.shmem, cur: null, peak: l.peak, max: l.max, pids: l.pids, psi_mem: null };
}

function Resources({ d }: { d: Detail }) {
  const j = d.job;
  const now = useNow();
  const running = j.state === "running";
  const can = d.node.metrics && !d.node.old_node && j.state !== "queued";
  const m = useJobMetrics(j.job_id, running, can);
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
        <p className="flex items-center gap-1.5 text-sm font-medium text-text">
          <CircleArrowUp aria-hidden="true" size={14} className="text-attention" />
          live statistics after the next node update
        </p>
        <p className="text-sm text-text-muted">
          {d.node.name} runs an older μ without the sampler, so this job has no resource chart; its state, log and times are
          shown. Update μ on {d.node.name} for live cores and memory.
        </p>
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
          <Meter label="cores" value={j.live.cpu_cores} max={limC} stale={liveStale} text={`${num(j.live.cpu_cores)}${limC !== null ? ` / ${limC}` : ""}`} />
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
    <dl className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-x-6 gap-y-1.5 border-t border-border px-3 py-3" data-testid="usage">
      <Meta k="CPU time">{duration(u.cpu_s)}</Meta>
      <Meta k="cores used">{num(u.cores_avg)}</Meta>
      <Meta k="peak working">{bytes(u.mem_work_peak_bytes)}</Meta>
      <Meta k="peak (cgroup)">{bytes(u.mem_peak_bytes)}*{u.source === "runner-sampled" ? "~" : ""}</Meta>
      {u.mem_events.oom_kill !== null && u.mem_events.oom_kill > 0 && <Meta k="OOM kills">{u.mem_events.oom_kill}</Meta>}
      <Meta k="processes, peak">{u.pids_peak ?? "—"}</Meta>
      <p className="col-span-full text-xs text-text-muted">
        * includes file cache{u.source === "runner-sampled" ? " · ~ last sample, up to 5 s old" : ""}
      </p>
    </dl>
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
        the node: <CopyCommand cmd={d.commands.node} />
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
            <CopyCommand cmd={`mu status ${id}`} primary />
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
      <div className="grid gap-4 min-[1200px]:grid-cols-[7fr_5fr]">
        <LogView id={d.job.job_id} queued={d.job.state === "queued"} logCmd={d.commands.log} />
        <Card label="resources">
          <SectionTitle right={<span className="text-xs text-text-muted">whole run</span>}>Resources</SectionTitle>
          <Resources d={d} />
        </Card>
      </div>
      <Record d={d} />
    </div>
  );
}
