// The view lives in the URL's fragment: #job=<id> opens a job (the link `mu submit` prints). Phase 1 reads it here;
// TanStack Router takes over with hash history in phase 2 (docs/design/dashboard.md §4.8).
import { useSyncExternalStore } from "react";
import { JOB_ID } from "../api/decode";

const EVENT = "mu:route";

function read(): string | null {
  const id = new URLSearchParams(window.location.hash.replace(/^#/, "")).get("job");
  return id !== null && JOB_ID.test(id) ? id : null;
}

/** The fragment's raw job parameter, for "not a job id" (null when there is none). */
export function rawJobParam(): string | null {
  return new URLSearchParams(window.location.hash.replace(/^#/, "")).get("job");
}

function subscribe(fn: () => void): () => void {
  window.addEventListener("hashchange", fn);
  window.addEventListener("popstate", fn);
  window.addEventListener(EVENT, fn);
  return () => {
    window.removeEventListener("hashchange", fn);
    window.removeEventListener("popstate", fn);
    window.removeEventListener(EVENT, fn);
  };
}

export function useJobRoute(): string | null {
  return useSyncExternalStore(subscribe, read, () => null);
}

export function jobHref(id: string): string {
  return `#job=${id}`;
}

export function openJob(id: string): void {
  window.location.hash = `job=${id}`;
}

export function closeJob(): void {
  window.history.pushState(null, "", window.location.pathname + window.location.search);
  window.dispatchEvent(new Event(EVENT));
}
