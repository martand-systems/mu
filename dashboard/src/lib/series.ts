// Series shaping (D37): a hole in a series is drawn as a hole. Where two samples are more than twice the sampling
// step apart, a null point is put between them, so neither the SVG sparklines nor uPlot (spanGaps: false) draw a line
// across the gap. Charts never interpolate.
import type { MetricSample, Series } from "../api/types";

export type Points = { t: number[]; v: (number | null)[] };

/** The median spacing of T, a robust guess of the sampling step. */
export function stepOf(t: number[], fallback: number): number {
  if (t.length < 3) return fallback;
  const d: number[] = [];
  for (let i = 1; i < t.length; i++) d.push(t[i]! - t[i - 1]!);
  d.sort((a, b) => a - b);
  const m = d[Math.floor(d.length / 2)]!;
  return m > 0 ? m : fallback;
}

/** Points with a null break wherever two samples are more than 2 x STEP apart. */
export function withGaps(series: Series, step: number): Points {
  const out: Points = { t: [], v: [] };
  let prev: number | null = null;
  for (const [t, v] of series) {
    if (prev !== null && t - prev > 2 * step && !__FAULT_INTERPOLATE_GAP__) {
      out.t.push((prev + t) / 2);
      out.v.push(null);
    }
    out.t.push(t);
    out.v.push(v);
    prev = t;
  }
  return out;
}

/** The resource chart's columns from a job's samples (every 5 s of the run), with breaks in the gaps. */
export type ResourceColumns = {
  t: number[];
  cores: (number | null)[];
  work: (number | null)[]; // anon + shmem: the working memory the limit is compared with
  cache: (number | null)[]; // file: page cache, reclaimable
};

export function resourceColumns(samples: MetricSample[], step = 5): ResourceColumns {
  const out: ResourceColumns = { t: [], cores: [], work: [], cache: [] };
  const sorted = [...samples].sort((a, b) => a.t - b.t);
  let prev: number | null = null;
  for (const s of sorted) {
    if (prev !== null && s.t <= prev) continue; // a repeated or older sample
    if (prev !== null && s.t - prev > 2 * step && !__FAULT_INTERPOLATE_GAP__) {
      out.t.push((prev + s.t) / 2);
      out.cores.push(null);
      out.work.push(null);
      out.cache.push(null);
    }
    out.t.push(s.t);
    out.cores.push(s.cpu);
    out.work.push(s.anon === null && s.shmem === null ? null : (s.anon ?? 0) + (s.shmem ?? 0));
    out.cache.push(s.file);
    prev = s.t;
  }
  return out;
}

/** One sample a minute for the "show as table" view. */
export function thin<T extends { t: number }>(rows: T[], every = 60): T[] {
  const out: T[] = [];
  let last = -Infinity;
  for (const r of rows) {
    if (r.t - last >= every) {
      out.push(r);
      last = r.t;
    }
  }
  const tail = rows[rows.length - 1];
  if (tail && out[out.length - 1] !== tail) out.push(tail);
  return out;
}

/** An SVG path ("M x y L x y …") for a sparkline; a null starts a new segment. */
export function sparkPath(p: Points, x0: number, x1: number, w: number, h: number, max: number): string {
  const span = x1 - x0 || 1;
  let d = "";
  let pen = false;
  for (let i = 0; i < p.t.length; i++) {
    const v = p.v[i];
    if (v === null || v === undefined || p.t[i]! < x0) {
      pen = false;
      continue;
    }
    const x = ((p.t[i]! - x0) / span) * w;
    const y = h - (Math.min(Math.max(v, 0), max) / (max || 1)) * h;
    d += `${pen ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)} `;
    pen = true;
  }
  return d.trim();
}
