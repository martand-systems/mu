// The server's clock as the page sees it, and the stale rule (docs/design/dashboard.md §4.8, §5.1).
// Every value carries its time `t` (epoch seconds on the Mac's clock, node times converted by the server). Ages are
// computed here, from the offset between the server's `now` and this browser's clock, so a value turns stale even
// when polling stops answering (the server is down, the tab slept).
import { useSyncExternalStore } from "react";

let offset = 0; // server now - browser now, in seconds
let staleAfter = 15;

export function noteServerNow(now: number): void {
  if (Number.isFinite(now) && now > 0) offset = now - Date.now() / 1000;
}

export function setStaleAfter(s: number): void {
  if (Number.isFinite(s) && s > 0) staleAfter = s;
}

export function staleAfterS(): number {
  return staleAfter;
}

/** The server's "now", in epoch seconds. */
export function serverNow(): number {
  return Date.now() / 1000 + offset;
}

/** Whether a value of time T is stale at NOW: no time at all, or older than the stale limit. */
export function isStale(t: number | null | undefined, now: number): boolean {
  return t === null || t === undefined || now - t > staleAfter;
}

// One ticker for the whole page: components that show an age subscribe; nothing else re-renders each second.
let tick = Math.floor(serverNow());
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  if (timer === null) {
    timer = setInterval(() => {
      tick = serverNow();
      for (const l of listeners) l();
    }, 1000);
  }
  return () => {
    listeners.delete(fn);
    if (listeners.size === 0 && timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  };
}

function snapshot(): number {
  return tick;
}

/** The server's now, updated once a second. */
export function useNow(): number {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

/** Refresh the ticker at once (after a response moved the offset). */
export function retick(): void {
  tick = serverNow();
  for (const l of listeners) l();
}
