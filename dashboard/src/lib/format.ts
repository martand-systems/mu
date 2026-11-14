// Formatting of figures: bytes (decimal GB, as μ's limits are written), durations, ages and clock times. Every
// function takes a possibly-null number and returns text; "—" stands for "no value".
export const DASH = "—";

/** "21.8 GB", "920 MB", "12 kB": one decimal for GB and TB (DIGITS), none below a GB. */
export function bytes(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return DASH;
  const a = Math.abs(n);
  if (a >= 1e12) return `${(n / 1e12).toFixed(digits)} TB`;
  if (a >= 1e9) return `${(n / 1e9).toFixed(digits)} GB`;
  if (a >= 1e6) return `${Math.round(n / 1e6)} MB`;
  if (a >= 1e3) return `${Math.round(n / 1e3)} kB`;
  return `${Math.round(n)} B`;
}

/** "9.1 / 16 GB": two sizes in the larger one's unit. */
export function bytesOf(used: number | null, total: number | null): string {
  if (used === null || total === null) return `${bytes(used)} / ${bytes(total)}`;
  const unit = total >= 1e12 ? 1e12 : total >= 1e9 ? 1e9 : total >= 1e6 ? 1e6 : 1e3;
  const name = unit === 1e12 ? "TB" : unit === 1e9 ? "GB" : unit === 1e6 ? "MB" : "kB";
  const t = total / unit;
  return `${(used / unit).toFixed(1)} / ${Number.isInteger(t) || t >= 100 ? t.toFixed(0) : t.toFixed(1)} ${name}`;
}

export function pct(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return DASH;
  return `${n < 10 ? n.toFixed(1) : Math.round(n)} %`;
}

export function num(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return DASH;
  return n.toFixed(digits);
}

/** Durations as the CLI writes them: 41s, 18m04s, 2h10m47s, 3d04h. */
export function duration(s: number | null | undefined): string {
  if (s === null || s === undefined || !Number.isFinite(s)) return DASH;
  const t = Math.max(0, Math.floor(s));
  const d = Math.floor(t / 86400);
  const h = Math.floor((t % 86400) / 3600);
  const m = Math.floor((t % 3600) / 60);
  const sec = t % 60;
  const two = (x: number) => String(x).padStart(2, "0");
  if (d > 0) return `${d}d${two(h)}h`;
  if (h > 0) return `${h}h${two(m)}m${two(sec)}s`;
  if (m > 0) return `${m}m${two(sec)}s`;
  return `${sec}s`;
}

/** A short wait: "18m", "2h05m", "40s". */
export function shortDuration(s: number | null | undefined): string {
  if (s === null || s === undefined || !Number.isFinite(s)) return DASH;
  const t = Math.max(0, Math.floor(s));
  if (t < 60) return `${t}s`;
  if (t < 3600) return `${Math.floor(t / 60)}m`;
  if (t < 86400) return `${Math.floor(t / 3600)}h${String(Math.floor((t % 3600) / 60)).padStart(2, "0")}m`;
  return `${Math.floor(t / 86400)}d${String(Math.floor((t % 86400) / 3600)).padStart(2, "0")}h`;
}

/** An age: "2 s ago", "4 min ago", "3 h ago". */
export function age(s: number): string {
  const t = Math.max(0, Math.floor(s));
  if (t < 60) return `${t} s ago`;
  if (t < 3600) return `${Math.floor(t / 60)} min ago`;
  if (t < 86400) return `${Math.floor(t / 3600)} h ago`;
  return `${Math.floor(t / 86400)} d ago`;
}

function two(x: number): string {
  return String(x).padStart(2, "0");
}

/** The local clock time of an epoch: "10:42:07"; with the date when it is not today: "14 Nov 10:42". */
export function clock(t: number | null | undefined, nowS?: number): string {
  if (t === null || t === undefined || !Number.isFinite(t)) return DASH;
  const d = new Date(t * 1000);
  const hms = `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
  if (nowS === undefined) return hms;
  const n = new Date(nowS * 1000);
  if (d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate()) return hms;
  const mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][d.getMonth()];
  return `${d.getDate()} ${mon} ${two(d.getHours())}:${two(d.getMinutes())}`;
}

/** An ISO 8601 date-time for <time dateTime>. */
export function iso(t: number): string {
  return new Date(t * 1000).toISOString();
}

/** The full local date and time, for tooltips: "2026-11-14 10:42:07". */
export function fullTime(t: number | null | undefined): string {
  if (t === null || t === undefined || !Number.isFinite(t)) return DASH;
  const d = new Date(t * 1000);
  return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())} ${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
}

export function plural(n: number, one: string, many = one + "s"): string {
  return `${n} ${n === 1 ? one : many}`;
}
