// Sparkline: a machine's last 10 minutes, as an SVG path drawn by React (no HTML sink; colours from the CSS tokens,
// so a theme change restyles it without a redraw). A gap in the samples stays a gap (D37).
import type { Series } from "../api/types";
import { sparkPath, stepOf, withGaps } from "../lib/series";

export function Sparkline({ series, now, span = 600, max = 100, stale = false, label }: { series: Series; now: number; span?: number; max?: number; stale?: boolean; label: string }) {
  const W = 120;
  const H = 28;
  const pts = withGaps(series, stepOf(series.map((p) => p[0]), 2));
  const d = sparkPath(pts, now - span, now, W, H, max);
  if (series.length < 6) {
    // a line from one or two points reads as broken: say what is happening instead
    return <span className="min-w-0 flex-1 text-xs text-text-muted" data-testid="spark-collecting">collecting…</span>;
  }
  return (
    <svg viewBox={`0 0 ${W} ${H}`} height={H} role="img" aria-label={label} className="h-7 min-w-0 flex-1 overflow-visible" preserveAspectRatio="none">
      <line x1="0" y1={H - 0.5} x2={W} y2={H - 0.5} className="stroke-border" strokeWidth="1" />
      {d && <path d={d} fill="none" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" className={stale ? "stroke-text-faint" : "stroke-text"} vectorEffect="non-scaling-stroke" />}
    </svg>
  );
}
