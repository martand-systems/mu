// The job table (docs/design/dashboard.md §5.1, §5.3): a plain <table> of our own in 1a. Two filters ("from",
// "node"; segmented buttons, not native <select>s: see ui/Segmented.tsx), three sortable columns whose headers are
// buttons with aria-sort; a row opens the job (the name is a real link; a click anywhere on the row does the same).
// Under 768 px the rows become two-line items.
import { ArrowDown, ArrowUp, ChevronsUpDown, Inbox } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import type { JobRow, NodeCard } from "../../api/types";
import { isStale, useNow } from "../../lib/clock";
import { bytes, clock, duration, num, shortDuration } from "../../lib/format";
import { jobHref, openJob } from "../../lib/route";
import { CopyCommand } from "../../ui/CopyCommand";
import { EmptyState } from "../../ui/EmptyState";
import { Segmented } from "../../ui/Segmented";
import { JobStateBadge } from "../../ui/StateBadge";

type SortKey = "name" | "time" | "mem";
type Sort = { key: SortKey; dir: "ascending" | "descending" } | null;

const FINISHED = new Set(["succeeded", "failed", "cancelled", "timeout", "oom", "interrupted", "unknown"]);

/** The time column's seconds: the wait of a queued job, the elapsed time of a running one, the wall time of a finished one. */
export function jobSeconds(j: JobRow, now: number): number | null {
  if (j.state === "queued") return j.queued_at !== null ? now - j.queued_at : null;
  if (j.state === "running") return j.started_at !== null ? now - j.started_at : null;
  if (j.started_at !== null && j.ended_at !== null) return j.ended_at - j.started_at;
  return null;
}

/** The memory column: live working memory (anon + shmem) of a running job, or the peak of a finished one. */
export function jobMem(j: JobRow): { v: number | null; mark: string; title: string } {
  if (j.state === "running" && j.live) {
    const w = j.live.anon === null && j.live.shmem === null ? null : (j.live.anon ?? 0) + (j.live.shmem ?? 0);
    return { v: w, mark: "", title: "working memory now (anon + shmem)" };
  }
  if (j.usage && j.usage.mem_peak_bytes !== null) {
    const sampled = j.usage.source === "runner-sampled";
    return { v: j.usage.mem_peak_bytes, mark: sampled ? "*~" : "*", title: `cgroup peak, includes file cache${sampled ? "; last sample, up to 5 s old" : ""}` };
  }
  return { v: null, mark: "", title: "" };
}

function jobCores(j: JobRow): { v: number | null; of: number | null } {
  if (j.state === "running" && j.live) return { v: j.live.cpu_cores, of: j.limits?.cores ?? null };
  if (j.usage) return { v: j.usage.cores_avg, of: j.limits?.cores ?? null };
  return { v: null, of: null };
}

function SortHeader({ k, sort, setSort, children, align = "left" }: { k: SortKey; sort: Sort; setSort: (s: Sort) => void; children: ReactNode; align?: "left" | "right" }) {
  const on = sort?.key === k;
  const dir = on ? sort.dir : null;
  const Icon = dir === "ascending" ? ArrowUp : dir === "descending" ? ArrowDown : ChevronsUpDown;
  return (
    <th scope="col" aria-sort={dir ?? "none"} className={`px-2 py-1.5 font-medium ${align === "right" ? "text-right" : "text-left"}`}>
      <button
        type="button"
        onClick={() => setSort(!on ? { key: k, dir: k === "name" ? "ascending" : "descending" } : dir === "descending" && k !== "name" ? { key: k, dir: "ascending" } : dir === "ascending" && k === "name" ? { key: k, dir: "descending" } : null)}
        className={`inline-flex items-center gap-1 rounded-sm hover:text-text ${on ? "text-text" : ""}`}
      >
        {children}
        <Icon aria-hidden="true" size={12} className={on ? "text-accent" : "text-text-muted"} />
      </button>
    </th>
  );
}

function Row({ j, now }: { j: JobRow; now: number }) {
  const secs = jobSeconds(j, now);
  const mem = jobMem(j);
  const cores = jobCores(j);
  const liveStale = j.state === "running" && j.live !== null && (j.live.stale || isStale(j.live.t, now));
  const rowStale = j.stale || (j.as_of !== null && isStale(j.as_of, now) && !FINISHED.has(j.state));
  const name = j.name ?? "(no name)";
  const timeText = j.state === "queued" ? `waits ${shortDuration(secs)}` : duration(secs);
  const fig = liveStale ? "text-stale" : "text-text";
  return (
    <>
      <tr
        className={`group cursor-pointer border-t border-border hover:bg-surface-raised max-md:grid max-md:grid-cols-[auto_1fr_auto] max-md:items-baseline max-md:gap-x-3 max-md:gap-y-0.5 max-md:px-3 max-md:py-2 ${j.note ? "" : ""}`}
        onClick={(e) => {
          if ((e.target as HTMLElement).closest("a,button")) return;
          openJob(j.job_id);
        }}
        data-job={j.job_id}
        data-node={j.node}
        data-state={j.state}
      >
        <td className="px-2 py-1.5 max-md:col-start-1 max-md:row-start-1 max-md:p-0">
          <JobStateBadge state={j.state} position={j.position} />
        </td>
        <td className="px-2 py-1.5 font-mono text-[13px] text-text-muted tabular-nums max-md:hidden">{j.short}</td>
        <td className="max-w-[18rem] px-2 py-1.5 max-md:col-span-2 max-md:col-start-2 max-md:row-start-1 max-md:max-w-none max-md:p-0">
          {__FAULT_DIV_BUTTON__ ? (
            <div className="truncate font-medium text-text" onClick={() => openJob(j.job_id)}>{name}</div>
          ) : (
            <a href={jobHref(j.job_id)} className="block truncate font-medium text-text underline-offset-2 hover:underline" title={name}>
              {__FAULT_INNER_HTML__ ? (
                // eslint-disable-next-line no-restricted-syntax -- D16's fault: the name rendered as HTML (never in a real build)
                <span dangerouslySetInnerHTML={{ __html: name }} />
              ) : (
                name
              )}
            </a>
          )}
        </td>
        <td className="max-w-[10rem] truncate px-2 py-1.5 text-text-muted max-md:hidden" title={j.project ?? j.project_id ?? ""}>{j.project ?? j.project_id ?? "—"}</td>
        <td className="px-2 py-1.5 text-text max-md:col-start-1 max-md:row-start-2 max-md:p-0 max-md:text-xs max-md:text-text-muted" data-col="node">{j.node}</td>
        <td className={`px-2 py-1.5 text-right text-[13px] tabular-nums max-md:col-start-2 max-md:row-start-2 max-md:p-0 max-md:text-left max-md:text-xs ${rowStale ? "text-stale" : "text-text"}`} data-col="time">
          {timeText}
          {rowStale && j.as_of !== null && <span className="ml-1 font-sans text-xs text-stale">as of {clock(j.as_of, now)}</span>}
        </td>
        <td className={`px-2 py-1.5 text-right text-[13px] tabular-nums max-md:col-start-3 max-md:row-start-2 max-md:p-0 max-md:text-xs ${mem.v === null ? "text-text-muted" : fig}`} title={mem.title} data-col="mem">
          {mem.v === null ? "—" : `${bytes(mem.v)}${mem.mark}`}
        </td>
        <td className={`px-2 py-1.5 text-right text-[13px] tabular-nums max-md:hidden ${cores.v === null ? "text-text-muted" : fig}`} data-col="cores">
          {cores.v === null ? "—" : `${num(cores.v)}${cores.of !== null ? ` / ${cores.of}` : ""}`}
          {liveStale && <span className="ml-1 font-sans text-xs text-stale">stale</span>}
        </td>
      </tr>
      {j.note && (
        <tr className="max-md:block" data-note-for={j.job_id}>
          <td />
          <td colSpan={7} className="px-2 pb-1.5 text-xs text-text-muted max-md:block max-md:px-3">
            {j.state}: {j.note}
            {j.usage?.source === "runner-sampled" && <> (~ last sample, up to 5 s old)</>}
          </td>
        </tr>
      )}
    </>
  );
}

function sorted(rows: JobRow[], sort: Sort, now: number): JobRow[] {
  if (!sort) return rows;
  const val = (j: JobRow): number | string | null =>
    sort.key === "name" ? (j.name ?? "").toLowerCase() : sort.key === "time" ? jobSeconds(j, now) : jobMem(j).v;
  const out = [...rows];
  out.sort((a, b) => {
    const x = val(a);
    const y = val(b);
    if (x === null && y === null) return 0;
    if (x === null) return 1; // no value: always last
    if (y === null) return -1;
    const c = x < y ? -1 : x > y ? 1 : 0;
    return sort.dir === "ascending" ? c : -c;
  });
  return out;
}

export function JobTable({ jobs, nodes, more, firstNode }: { jobs: JobRow[]; nodes: NodeCard[]; more: Record<string, boolean>; firstNode: string | null }) {
  const now = useNow();
  const [from, setFrom] = useState<"mine" | "all">("mine");
  const [node, setNode] = useState<string>("all");
  const [sort, setSort] = useState<Sort>(null);
  const nodeNames = useMemo(() => nodes.map((n) => n.name), [nodes]);
  const shown = useMemo(
    () => sorted(jobs.filter((j) => (from === "all" || j.mine) && (node === "all" || j.node === node)), sort, now),
    [jobs, from, node, sort, now],
  );
  const anyMore = Object.entries(more).some(([n, m]) => m && (node === "all" || node === n));
  const hidden = jobs.length - shown.length;
  return (
    <section aria-labelledby="jobs-title" className="min-w-0 rounded-md border border-border bg-surface" data-testid="jobs">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border px-3 py-2">
        <h2 id="jobs-title" className="text-xs font-semibold uppercase tracking-wider text-text-muted">Jobs</h2>
        <Segmented label="from" testid="filter-from" value={from} onChange={setFrom}
          options={[{ v: "mine", text: "this Mac" }, { v: "all", text: "all workstations" }]} />
        {nodeNames.length > 1 && (
          <Segmented label="node" testid="filter-node" value={node} onChange={setNode}
            options={[{ v: "all", text: "all" }, ...nodeNames.map((n) => ({ v: n, text: n }))]} />
        )}
        <span className="ml-auto text-xs text-text-muted tabular-nums" data-testid="job-count">
          {shown.length} {shown.length === 1 ? "job" : "jobs"}
          {hidden > 0 && <> · {hidden} hidden by the filters</>}
        </span>
      </div>
      {shown.length === 0 ? (
        jobs.length === 0 ? (
          <EmptyState icon={Inbox} title="No jobs yet">
            <p>Submit one from a project folder; it appears here within seconds.</p>
            <CopyCommand cmd={`mu submit --on ${firstNode ?? "office-pc"} -- python …`} primary label="copy" />
          </EmptyState>
        ) : (
          <EmptyState icon={Inbox} title="No jobs match the filters">
            <p>{hidden} {hidden === 1 ? "job is" : "jobs are"} hidden: choose “all workstations” or another node.</p>
          </EmptyState>
        )
      ) : (
        <div className="overflow-x-auto max-md:overflow-visible">
          <table className="w-full border-collapse text-sm max-md:block">
            <caption className="sr-only">Jobs on the paired nodes; choose a job's name to open its log and details</caption>
            <thead className="text-xs text-text-muted max-md:hidden">
              <tr>
                <th scope="col" className="px-2 py-1.5 text-left font-medium">State</th>
                <th scope="col" className="px-2 py-1.5 text-left font-medium">Id</th>
                <SortHeader k="name" sort={sort} setSort={setSort}>Name</SortHeader>
                <th scope="col" className="px-2 py-1.5 text-left font-medium">Project</th>
                <th scope="col" className="px-2 py-1.5 text-left font-medium">Node</th>
                <SortHeader k="time" sort={sort} setSort={setSort} align="right">Wait / time</SortHeader>
                <SortHeader k="mem" sort={sort} setSort={setSort} align="right">Memory</SortHeader>
                <th scope="col" className="px-2 py-1.5 text-right font-medium">Cores</th>
              </tr>
            </thead>
            <tbody className="max-md:block">
              {shown.map((j) => (
                <Row key={j.job_id} j={j} now={now} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border px-3 py-2 text-xs text-text-muted">
        {anyMore && (
          <span data-testid="older-jobs">
            older jobs: <code className="font-mono text-text">mu status</code>
          </span>
        )}
        <span>* cgroup peak, includes file cache · ~ last sample, up to 5 s old · memory of a running job: working set (anon + shmem)</span>
      </div>
    </section>
  );
}
