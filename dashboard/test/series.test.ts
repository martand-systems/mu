// D37: gaps stay gaps. A series with a 30 s hole in 5 s samples is shaped with a break (a null), never a line across.
import { describe, expect, it } from "vitest";
import type { MetricSample } from "../src/api/types";
import { resourceColumns, sparkPath, stepOf, thin, withGaps } from "../src/lib/series";

const sample = (t: number): MetricSample => ({ t, cpu: 2, anon: 1e9, file: 1e8, shmem: 0, cur: 1e9, peak: 1e9, max: 8e9, pids: 4, psi_mem: 0 });

describe("D37: gaps", () => {
  it("a 30 s hole in 5 s samples gets a null break", () => {
    const ts = [0, 5, 10, 15, 45, 50, 55];
    const cols = resourceColumns(ts.map((t) => sample(1000 + t)));
    const i = cols.cores.indexOf(null);
    expect(i).toBeGreaterThan(0);
    expect(cols.t[i]).toBeGreaterThan(1015);
    expect(cols.t[i]).toBeLessThan(1045);
    expect(cols.work[i]).toBeNull();
    expect(cols.cache[i]).toBeNull();
  });
  it("evenly spaced samples get no break", () => {
    const cols = resourceColumns([0, 5, 10, 15, 20].map((t) => sample(t)));
    expect(cols.cores.includes(null)).toBe(false);
  });
  it("sparkline series break too, and the path lifts the pen", () => {
    const s: [number, number | null][] = [[0, 10], [2, 20], [4, 30], [40, 40], [42, 50]];
    const p = withGaps(s, stepOf(s.map((x) => x[0]), 2));
    expect(p.v.includes(null)).toBe(true);
    const d = sparkPath(p, 0, 42, 100, 20, 100);
    expect(d.match(/M/g)?.length).toBe(2);
  });
  it("repeated or older samples are dropped, not drawn backwards", () => {
    const cols = resourceColumns([sample(10), sample(5), sample(10), sample(15)]);
    expect(cols.t).toEqual([5, 10, 15]);
  });
  it("thin keeps one sample a minute and the last", () => {
    const rows = Array.from({ length: 50 }, (_, i) => ({ t: i * 5 }));
    const t = thin(rows);
    expect(t.map((r) => r.t)).toEqual([0, 60, 120, 180, 240, 245]);
  });
});
