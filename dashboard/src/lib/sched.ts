// The scheduler's words on the page (the integration contract §6): paused, resuming, held and borrowing jobs. The
// page stays read-only: these lines say what is happening and which `mu` command changes it, nothing more.
import type { JobRow } from "../api/types";
import { clock, shortDuration } from "./format";

/** The short id a person reads (the last six characters), or the name of the job when this page knows it. */
export type NameOf = (jobId: string) => string;

export const shortId: NameOf = (id) => id.slice(-6);

/** "14:45" (a clock time without seconds; another day keeps its date). */
export function hm(t: number, now: number): string {
  return clock(t, now).replace(/(\d\d:\d\d):\d\d/, "$1");
}

/** The one line under a job's name for its scheduler state, or null. */
export function schedText(j: JobRow, now: number, nameOf: NameOf = shortId): string | null {
  const z = j.paused;
  if (j.display === "resuming" && z) {
    const waits = z.lent_to.length ? `waits for ${z.lent_to.map(nameOf).join(", ")}` : "thawing";
    return `resuming: ${waits}${z.resumes_by !== null ? ` (ends by ${hm(z.resumes_by, now)})` : ""}`;
  }
  if (j.display === "paused" && z) {
    const until = z.until !== null ? ` until ${hm(z.until, now)}` : "";
    if (z.why === "auto") return `paused for ${z.for_job ? nameOf(z.for_job) : "an urgent job"}${until}`;
    return `paused${z.by ? ` by ${z.by}` : ""}${until}${z.until !== null ? " · resumes by itself" : ""}`;
  }
  if (j.state === "held") {
    const place = j.place ?? j.position;
    return `held${j.held?.by ? ` by ${j.held.by}` : ""}${j.held?.at !== null && j.held?.at !== undefined ? ` ${hm(j.held.at, now)}` : ""}${place !== null ? ` · keeps place ${place}` : ""}`;
  }
  if (j.state === "running" && j.class === "borrowing") {
    const b = j.grant?.borrowed[0];
    return `on cores lent by ${b?.from ? nameOf(b.from) : "a paused job"}${b?.until !== null && b?.until !== undefined ? ` until ${hm(b.until, now)}` : ""}`;
  }
  return null;
}

/** "1h25m run of 6h · 5m paused (not counted)" */
export function timeLabel(run: number, limit: number | null, paused: number): string {
  return `${shortDuration(run)} run${limit !== null ? ` of ${shortDuration(limit)}` : ""}${paused > 0 ? ` · ${shortDuration(paused)} paused (not counted)` : ""}`;
}
