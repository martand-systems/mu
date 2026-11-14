// ResourceChart: a job's cores and memory over its whole run (docs/design/dashboard.md §5.3). uPlot on a canvas,
// wrapped here so it can be replaced in one file (§8). One cursor across both panes; gaps drawn as gaps (spanGaps
// off, D37); values with units in our own legend, not a floating tooltip. A canvas cannot be restyled by CSS, so the
// colours are read from the tokens and the charts are redrawn when data-theme changes (D42). An aria-label summary,
// and "show as table" lists the samples (one a minute) as a real table.
import { Table2 } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState, type RefObject } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import type { MetricSample } from "../../api/types";
import { bytes, clock, num, timeLabels } from "../../lib/format";
import { resourceColumns, thin, type ResourceColumns } from "../../lib/series";
import { token, useEffectiveTheme } from "../../lib/theme";
import { ToggleButton } from "../../ui/Toggle";

type Colours = { cores: string; mem: string; cache: string; limit: string; attention: string; axis: string; grid: string };

function readColours(): Colours {
  return {
    cores: token("series-cores"), mem: token("series-mem"), cache: token("series-cache"), limit: token("series-limit"),
    attention: token("state-attention"), axis: token("text-muted"), grid: token("border"),
  };
}

function useWidth(ref: RefObject<HTMLDivElement | null>): number {
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(Math.floor(el.clientWidth)));
    ro.observe(el);
    setW(Math.floor(el.clientWidth));
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

function axes(c: Colours, yFmt: (v: number) => string, xLabels: boolean): uPlot.Axis[] {
  const font = "11px -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif";
  return [
    {
      stroke: c.axis, font, space: 60, grid: { stroke: c.grid, width: 1 }, ticks: { stroke: c.grid, width: 1, show: xLabels },
      size: xLabels ? 30 : 4,
      values: (u, vals) => (xLabels ? timeLabels(vals, (u.scales.x!.max ?? 0) - (u.scales.x!.min ?? 0)) : vals.map(() => "")),
    },
    { stroke: c.axis, font, size: 56, grid: { stroke: c.grid, width: 1 }, ticks: { stroke: c.grid, width: 1 }, values: (_u, vals) => vals.map(yFmt) },
  ];
}

function Pane({ data, opts, width, onCursor, label }: { data: uPlot.AlignedData; opts: Omit<uPlot.Options, "width">; width: number; onCursor: (i: number | null) => void; label: string }) {
  const host = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const cursorCb = useRef(onCursor);
  useEffect(() => {
    cursorCb.current = onCursor;
  }, [onCursor]);
  useEffect(() => {
    if (!host.current || width < 50) return;
    const u = new uPlot(
      { ...opts, width, hooks: { setCursor: [(self) => cursorCb.current(self.cursor.idx ?? null)] } },
      data,
      host.current,
    );
    plot.current = u;
    return () => {
      u.destroy();
      plot.current = null;
    };
    // the chart is rebuilt when its options change (a theme change); new data only calls setData below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opts, width]);
  useEffect(() => {
    plot.current?.setData(data);
  }, [data]);
  return <div ref={host} role="img" aria-label={label} className="resource-pane" />;
}

export function ResourceChart({ samples, limitMem, limitCores, label }: { samples: MetricSample[]; limitMem: number | null; limitCores: number | null; label: string }) {
  const theme = useEffectiveTheme();
  const box = useRef<HTMLDivElement>(null);
  const width = useWidth(box);
  const [idx, setIdx] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const cols: ResourceColumns = useMemo(() => resourceColumns(samples), [samples]);
  // colours from the tokens; read again when the theme changes (chart-fixed-colours: read once only)
  const colourKey = __FAULT_CHART_FIXED_COLOURS__ ? "fixed" : theme;
  const colours = useMemo(readColours, [colourKey]);

  const lastWork = [...cols.work].reverse().find((v) => v !== null) ?? null;
  const hot = limitMem !== null && lastWork !== null && lastWork > 0.9 * limitMem;
  const syncKey = useId();

  const coresOpts = useMemo((): Omit<uPlot.Options, "width"> => ({
    height: 150,
    legend: { show: false },
    cursor: { sync: { key: syncKey }, drag: { x: false, y: false }, points: { size: 5 } },
    scales: { x: { time: true }, y: { range: (_u, _lo, hi) => [0, Math.max(limitCores ?? 0, hi, 1) * 1.05] } },
    axes: axes(colours, (v) => num(v, 0), false),
    series: [
      {},
      { label: "cores", stroke: colours.cores, width: 1.5, spanGaps: false, points: { show: false } },
      { label: "cores limit", stroke: colours.limit, width: 1, dash: [4, 4], spanGaps: true, points: { show: false } },
    ],
  }), [colours, limitCores, syncKey]);

  const memOpts = useMemo((): Omit<uPlot.Options, "width"> => ({
    height: 190,
    legend: { show: false },
    cursor: { sync: { key: syncKey }, drag: { x: false, y: false }, points: { size: 5 } },
    scales: { x: { time: true }, y: { range: (_u, _lo, hi) => [0, Math.max(limitMem ?? 0, hi, 1) * 1.05] } },
    axes: axes(colours, (v) => bytes(v, 0), true),
    series: [
      {},
      { label: "working memory", stroke: hot ? colours.attention : colours.mem, width: 1.5, spanGaps: false, points: { show: false } },
      { label: "cache", stroke: colours.cache, width: 1, spanGaps: false, points: { show: false } },
      { label: "memory limit", stroke: colours.limit, width: 1, dash: [4, 4], spanGaps: true, points: { show: false } },
    ],
  }), [colours, limitMem, hot, syncKey]);

  const coresData = useMemo((): uPlot.AlignedData => [cols.t, cols.cores, cols.t.map(() => limitCores)], [cols, limitCores]);
  const memData = useMemo((): uPlot.AlignedData => [cols.t, cols.work, cols.cache, cols.t.map(() => limitMem)], [cols, limitMem]);

  const at = idx !== null && idx < cols.t.length ? idx : cols.t.length - 1;
  const pick = (a: (number | null)[]) => {
    for (let i = at; i >= 0; i--) if (a[i] !== null && a[i] !== undefined) return a[i]!;
    return null;
  };
  const peakWork = cols.work.reduce<number | null>((m, v) => (v !== null && (m === null || v > m) ? v : m), null);
  const summary = `${label}: memory ${bytes(lastWork)} now, peak ${bytes(peakWork)}, limit ${bytes(limitMem)}; cores ${num(pick(cols.cores))}${limitCores !== null ? ` of ${limitCores}` : ""}`;

  if (cols.t.length === 0) return <p className="px-3 py-6 text-sm text-text-muted">No samples yet: the node samples the job every 5 s once it runs.</p>;

  return (
    <div className="flex flex-col gap-2 px-3 py-3" data-testid="resource-chart" aria-label={summary}>
      <dl className="flex flex-wrap gap-x-4 gap-y-1 text-xs tabular-nums">
        <div className="flex items-center gap-1.5"><span aria-hidden="true" className="inline-block h-0.5 w-3 bg-[var(--series-cores)]" /><dt className="font-sans text-text-muted">cores</dt><dd className="text-text">{num(pick(cols.cores))}{limitCores !== null && ` / ${limitCores}`}</dd></div>
        <div className="flex items-center gap-1.5"><span aria-hidden="true" className={`inline-block h-0.5 w-3 ${hot ? "bg-attention" : "bg-[var(--series-mem)]"}`} /><dt className="font-sans text-text-muted">working (anon+shm)</dt><dd className="text-text">{bytes(pick(cols.work))}</dd></div>
        <div className="flex items-center gap-1.5"><span aria-hidden="true" className="inline-block h-0.5 w-3 bg-[var(--series-cache)]" /><dt className="font-sans text-text-muted">cache (reclaimable)</dt><dd className="text-text">{bytes(pick(cols.cache))}</dd></div>
        <div className="flex items-center gap-1.5"><span aria-hidden="true" className="inline-block w-3 border-t border-dashed border-[var(--series-limit)]" /><dt className="font-sans text-text-muted">limit</dt><dd className="text-text">{bytes(limitMem)}</dd></div>
        <div className="flex items-center gap-1.5"><dt className="font-sans text-text-muted">at</dt><dd className="text-text">{clock(cols.t[at] ?? null)}</dd></div>
      </dl>
      <div ref={box} className="flex min-w-0 flex-col gap-1">
        <p className="text-xs text-text-muted" aria-hidden="true">cores</p>
        <Pane data={coresData} opts={coresOpts} width={width} onCursor={setIdx} label={`cores over the run: ${summary}`} />
        <p className="mt-2 text-xs text-text-muted" aria-hidden="true">memory</p>
        <Pane data={memData} opts={memOpts} width={width} onCursor={setIdx} label={`memory over the run: ${summary}`} />
      </div>
      <div>
        <ToggleButton pressed={table} onChange={setTable} icon={Table2}>show as table</ToggleButton>
      </div>
      {table && <SampleTable samples={samples} />}
    </div>
  );
}

function SampleTable({ samples }: { samples: MetricSample[] }) {
  const rows = thin([...samples].sort((a, b) => a.t - b.t));
  return (
    <div className="max-h-72 overflow-auto rounded-sm border border-border" tabIndex={0} role="region" aria-label="samples, one a minute">
      <table className="w-full text-xs" data-testid="sample-table">
        <caption className="sr-only">The job's samples, one a minute</caption>
        <thead className="sticky top-0 bg-surface-raised text-text-muted">
          <tr>
            <th scope="col" className="px-2 py-1 text-left font-medium">time</th>
            <th scope="col" className="px-2 py-1 text-right font-medium">cores</th>
            <th scope="col" className="px-2 py-1 text-right font-medium">working</th>
            <th scope="col" className="px-2 py-1 text-right font-medium">cache</th>
            <th scope="col" className="px-2 py-1 text-right font-medium">peak</th>
          </tr>
        </thead>
        <tbody className="font-mono tabular-nums">
          {rows.map((s) => (
            <tr key={s.t} className="border-t border-border">
              <td className="px-2 py-0.5">{clock(s.t)}</td>
              <td className="px-2 py-0.5 text-right">{num(s.cpu, 2)}</td>
              <td className="px-2 py-0.5 text-right">{bytes(s.anon === null ? null : s.anon + (s.shmem ?? 0))}</td>
              <td className="px-2 py-0.5 text-right">{bytes(s.file)}</td>
              <td className="px-2 py-0.5 text-right">{bytes(s.peak)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
