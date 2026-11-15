// Meter: a value against a limit, the text value always beside the bar (§5.3). The bar is decoration (aria-hidden);
// the text carries the figure.
export function Meter({ value, max, text, warnAt = 0.9, stale = false, label }: { value: number | null; max: number | null; text: string; warnAt?: number; stale?: boolean; label: string }) {
  const f = value !== null && max !== null && max > 0 ? Math.min(Math.max(value / max, 0), 1) : 0;
  const warn = f >= warnAt && !stale;
  return (
    <div className="flex min-w-0 items-center gap-2">
      <span className="w-12 shrink-0 text-xs font-medium uppercase tracking-wide text-text-muted">{label}</span>
      <div className="relative h-1.5 min-w-12 flex-1 overflow-hidden rounded-full bg-surface-raised ring-1 ring-inset ring-border" aria-hidden="true">
        <div
          className={`absolute inset-y-0 left-0 rounded-full ${stale ? "bg-text-faint" : warn ? "bg-attention" : "bg-text"}`}
          style={{ width: `${(f * 100).toFixed(1)}%` }}
        />
      </div>
      <span className={`shrink-0 text-[13px] tabular-nums ${stale ? "text-stale" : "text-text"}`}>{text}</span>
    </div>
  );
}
