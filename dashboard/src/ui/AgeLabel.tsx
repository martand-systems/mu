// AgeLabel: "2 s ago", or "stale · as of 10:42:07" once the value is older than the stale limit (§5.1: every number
// shows its age). It ticks every second but is never a live region: nothing is announced on a routine refresh.
import { useNow, isStale } from "../lib/clock";
import { age, clock, fullTime, iso } from "../lib/format";
import { Tip } from "./Tooltip";

export function AgeLabel({ t, prefix = "", staleOverride, className = "" }: { t: number | null; prefix?: string; staleOverride?: boolean; className?: string }) {
  const now = useNow();
  if (t === null) {
    return <span className={`text-xs text-text-muted ${className}`}>no data yet</span>;
  }
  const stale = staleOverride ?? isStale(t, now);
  return (
    <Tip content={fullTime(t)}>
      <time
        dateTime={iso(t)}
        data-age={stale ? "stale" : "live"}
        className={`whitespace-nowrap text-xs tabular-nums ${stale ? "text-stale" : "text-text-muted"} ${className}`}
      >
        {stale ? `stale · as of ${clock(t, now)}` : `${prefix}${age(now - t)}`}
      </time>
    </Tip>
  );
}
