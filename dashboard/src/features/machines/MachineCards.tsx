// The machine cards (docs/design/dashboard.md §5.1, §5.3): This Mac first, then each node. Every figure shows its
// age; past the stale limit the figures turn grey with "stale · as of …". Node figures are the WSL VM's.
import { CircleArrowUp, Laptop, RotateCw, Server, TriangleAlert } from "lucide-react";
import { useState, type ReactNode } from "react";
import { apiPost } from "../../api/client";
import type { MacCard as Mac, NodeCard as Node } from "../../api/types";
import { isStale, useNow } from "../../lib/clock";
import { bytes, bytesOf, clock, num, pct, plural, shortDuration } from "../../lib/format";
import { AgeLabel } from "../../ui/AgeLabel";
import { Card } from "../../ui/Card";
import { Meter } from "../../ui/Meter";
import { Skeleton } from "../../ui/Skeleton";
import { Sparkline } from "../../ui/Sparkline";
import { MachineBadge, type MachineState } from "../../ui/StateBadge";

function Header({ icon: Icon, name, sub, badge }: { icon: typeof Laptop; name: string; sub?: string; badge: ReactNode }) {
  return (
    <div className="flex items-center gap-2 border-b border-border px-3 py-2">
      <Icon aria-hidden="true" size={15} className="shrink-0 text-text-muted" />
      <h3 className="min-w-0 truncate text-sm font-semibold text-text">{name}</h3>
      {sub && <span className="min-w-0 truncate text-xs text-text-muted">{sub}</span>}
      <span className="ml-auto shrink-0 pl-2">{badge}</span>
    </div>
  );
}

function Footer({ children, age }: { children?: ReactNode; age: ReactNode }) {
  return (
    <div className="mt-auto flex items-end gap-3 border-t border-border px-3 py-1.5 text-xs text-text-muted">
      <span className="min-w-0 flex-1">{children}</span>
      <span className="shrink-0">{age}</span>
    </div>
  );
}

function CpuRow({ cpu, series, now, stale, label }: { cpu: number | null; series: Node["series"]["cpu_pct"]; now: number; stale: boolean; label: string }) {
  return (
    <div className="flex items-center gap-3">
      <span className="w-12 shrink-0 text-xs font-medium uppercase tracking-wide text-text-muted">CPU</span>
      <span className={`w-16 shrink-0 text-xl font-semibold tabular-nums tracking-tight ${stale ? "text-stale" : "text-text"}`} data-fig="cpu">
        {pct(cpu)}
      </span>
      <Sparkline series={series} now={now} stale={stale} label={label} />
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ This Mac
export function MacCard({ mac }: { mac: Mac }) {
  const now = useNow();
  const stale = isStale(mac.t, now);
  const c = mac.cores;
  const cores = c.logical !== null ? `${c.logical} cores${c.perf !== null && c.eff !== null ? ` (${c.perf}P + ${c.eff}E)` : ""}` : undefined;
  return (
    <Card label="This Mac" testid="card-mac" className="flex flex-col">
      <Header icon={Laptop} name="This Mac" sub={mac.host} badge={<MachineBadge state={stale ? "stale" : "online"} label={stale ? "stale" : "live"} />} />
      <div className="flex flex-col gap-2 px-3 py-3">
        {mac.t === null ? (
          <Skeleton lines={3} label="sampling This Mac" />
        ) : (
          <>
            <CpuRow cpu={mac.cpu_pct} series={mac.series.cpu_pct} now={now} stale={stale} label={`This Mac CPU, last 10 minutes; now ${pct(mac.cpu_pct)}`} />
            <div data-fig="mac-mem">
              <Meter label="MEM" value={mac.mem.used} max={mac.mem.total} stale={stale} text={`${bytesOf(mac.mem.used, mac.mem.total)}`} />
            </div>
            <dl className="flex flex-wrap gap-x-4 gap-y-1 pl-15 text-xs text-text-muted">
              <div className="flex gap-1"><dt>available</dt><dd className={`tabular-nums ${stale ? "text-stale" : "text-text"}`} data-fig="mac-avail">{bytes(mac.mem.available)}</dd></div>
              {mac.mem.pressure && (
                <div className="flex gap-1"><dt>pressure</dt><dd className={mac.mem.pressure === "ok" ? "text-text" : "text-attention"}>{mac.mem.pressure}</dd></div>
              )}
              {mac.load && <div className="flex gap-1"><dt>load</dt><dd className="tabular-nums text-text">{mac.load.map((x) => num(x)).join(" ")}</dd></div>}
            </dl>
          </>
        )}
      </div>
      <Footer age={<AgeLabel t={mac.t} />}>
        {cores ?? "cores unknown"}
        {mac.battery ? (mac.battery.on_battery ? ` · on battery${mac.battery.percent !== null ? ` ${Math.round(mac.battery.percent)} %` : ""}` : " · on mains power") : ""}
        {mac.mu_version && <> · μ {mac.mu_version}</>}
        {mac.sampler !== "psutil" && <> · sampler: {mac.sampler}</>}
      </Footer>
    </Card>
  );
}

// ------------------------------------------------------------------------------------------------ a node
export function nodeLook(n: Node): MachineState {
  if (n.reconnecting) return "reconnecting";
  if (n.state === "online" && n.old_node) return "old-node";
  return n.state;
}

function RetryButton({ name }: { name: string }) {
  const [state, setState] = useState<"idle" | "asked" | "failed">("idle");
  return (
    <button
      type="button"
      className="inline-flex items-center gap-1 rounded-sm border border-border-strong bg-surface px-2 py-0.5 text-xs font-medium text-text hover:bg-surface-raised"
      onClick={() => {
        apiPost(`/api/nodes/${encodeURIComponent(name)}/retry`).then(() => setState("asked"), () => setState("failed"));
      }}
    >
      <RotateCw aria-hidden="true" size={12} />
      {state === "asked" ? "asked once more" : state === "failed" ? "retry failed" : "retry now"}
    </button>
  );
}

function NodeProblem({ n, now }: { n: Node; now: number }) {
  const e = n.error;
  if (n.state === "offline" || (n.state === "connecting" && e)) {
    const wait = e?.retry_at ? Math.max(0, e.retry_at - now) : null;
    return (
      <div className="flex flex-col gap-1 text-sm" data-testid="node-offline">
        <p className="text-text">
          offline{n.offline_since !== null && <> since <span className="tabular-nums">{clock(n.offline_since, now)}</span></>}
          {wait !== null && <span className="text-text-muted"> · next try in <span className="tabular-nums">{shortDuration(wait)}</span></span>}
        </p>
        {e && <p className="text-xs text-text-muted">{e.message}</p>}
        <p className="text-xs text-text-muted">Offline is a normal state: jobs already accepted keep running on the node.</p>
      </div>
    );
  }
  if (n.state === "refused" && !__FAULT_REFUSED_AS_OFFLINE__) {
    return (
      <div className="flex flex-col gap-1.5 text-sm" data-testid="node-refused">
        {e && <p className="text-text">{e.message}</p>}
        {e?.next && <p className="text-xs text-text-muted">{e.next}</p>}
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium text-failed">never retried automatically</span>
          <RetryButton name={n.name} />
        </div>
      </div>
    );
  }
  if (n.state === "refused" && __FAULT_REFUSED_AS_OFFLINE__) {
    return <p className="text-sm text-text">offline</p>;
  }
  if (e && (n.state === "cannot" || n.state === "keychain" || n.state === "error")) {
    const wait = e.retry_at ? Math.max(0, e.retry_at - now) : null;
    return (
      <div className="flex flex-col gap-1 text-sm" data-testid={`node-${n.state}`}>
        <p className="text-text">{e.message}</p>
        {e.next && <p className="text-xs text-text-muted">{e.next}</p>}
        {wait !== null && <p className="text-xs text-text-muted">next try in <span className="tabular-nums">{shortDuration(wait)}</span></p>}
      </div>
    );
  }
  if (n.state === "connecting") return <Skeleton lines={3} label={`connecting to ${n.name}`} />;
  return null;
}

export function NodeCard({ n, macVersion }: { n: Node; macVersion: string | null }) {
  const now = useNow();
  const st = n.stats;
  const h = n.hello;
  const statsStale = st ? st.stale || st.node_stale || isStale(st.t, now) : true;
  const helloT = h?.t ?? null;
  const ageT = st?.t ?? n.list_t ?? helloT;
  const q = st?.queue ?? h?.queue;
  const look = nodeLook(n);
  const wsl = h?.wsl ?? true;
  const total = st?.mem.total ?? h?.mem_total ?? null;
  return (
    <Card label={`node ${n.name}`} testid={`card-node-${n.name}`} className="flex flex-col">
      <Header icon={Server} name={n.name} sub="via relay" badge={<MachineBadge state={look === "online" && st && statsStale ? "stale" : look} />} />
      <div className="flex flex-col gap-2 px-3 py-3">
        {n.reconnecting && <p className="text-xs text-text-muted">reconnecting… (one reconcile after a sleep)</p>}
        <NodeProblem n={n} now={now} />
        {n.state === "online" && st && (
          <>
            <CpuRow cpu={st.cpu_pct} series={n.series.cpu_pct} now={now} stale={statsStale} label={`${n.name} CPU, last 10 minutes; now ${pct(st.cpu_pct)}`} />
            <div data-fig="node-mem">
              <Meter label="MEM" value={st.mem.used} max={total} stale={statsStale} text={bytesOf(st.mem.used, total)} />
            </div>
            <dl className="flex flex-wrap gap-x-4 gap-y-1 pl-15 text-xs text-text-muted">
              <div className="flex gap-1"><dt>available</dt><dd className={`tabular-nums ${statsStale ? "text-stale" : "text-text"}`} data-fig="node-avail">{bytes(st.mem.avail)}</dd></div>
              <div className="flex gap-1"><dt>cache</dt><dd className={`tabular-nums ${statsStale ? "text-stale" : "text-text"}`}>{bytes(st.mem.cached)}</dd></div>
              {st.disk.free_gb !== null && <div className="flex gap-1"><dt>disk free</dt><dd className="tabular-nums text-text">{st.disk.free_gb} GB</dd></div>}
            </dl>
          </>
        )}
        {n.state === "online" && !st && n.old_node && (
          <div className="flex flex-col gap-1.5" data-testid="node-old">
            <p className="flex items-center gap-1.5 text-sm text-text">
              <CircleArrowUp aria-hidden="true" size={14} className="shrink-0 text-attention" />
              live statistics after the next node update
            </p>
            <p className="text-xs text-text-muted">{n.old_node_text ?? `update μ on ${n.name} for live statistics`}</p>
            {h && (
              <p className="text-xs text-text-muted">
                {h.cpu_model && <>{h.cpu_model} · </>}
                {h.cores_logical !== null && <>{h.cores_logical} threads · </>}
                {bytes(h.mem_total, 0)} {wsl ? "(WSL VM)" : ""}
              </p>
            )}
          </div>
        )}
        {(n.state === "online" || (q && n.state !== "refused")) && q && (
          <p className="pl-15 text-xs text-text-muted" data-fig="node-queue">
            <span className="text-text">{plural(q.running ?? 0, "running", "running")}</span> · <span className="text-text">{q.queued ?? 0} queued</span>
            {h?.mu_version && (
              <> · μ {h.mu_version}{macVersion && h.mu_version === macVersion ? " (same as this Mac)" : ""}</>
            )}
          </p>
        )}
        {n.attention.length > 0 && (
          <ul className="flex flex-col gap-1 border-t border-border pt-2" aria-label={`${n.name}: needs attention`}>
            {n.attention.map((a, i) => (
              <li key={i} className="flex items-start gap-1.5 text-xs text-text">
                <TriangleAlert aria-hidden="true" size={13} className="mt-px shrink-0 text-attention" />
                <span><span className="font-medium text-attention">attention</span> · {a.text}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <Footer age={<AgeLabel t={ageT} staleOverride={n.state !== "online" || (st ? statsStale : n.list_stale) ? true : undefined} />}>
        {n.state === "online" && wsl ? "WSL VM figures, not Windows'" : n.state === "online" ? "" : "the node's last answer"}
      </Footer>
    </Card>
  );
}

export function MachineCards({ mac, nodes }: { mac: Mac; nodes: Node[] }) {
  return (
    <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr))]" data-testid="machines">
      <MacCard mac={mac} />
      {nodes.map((n) => (
        <NodeCard key={n.name} n={n} macVersion={mac.mu_version} />
      ))}
    </div>
  );
}
