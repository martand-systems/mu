// Server state through TanStack Query (docs/design/dashboard.md §4.8): the overview every 2 s, the open job every 2 s,
// its metrics once. Polling runs only while the tab is visible (refetchIntervalInBackground: false), which is what
// the server's "a tab is visible" rule observes (§4.3). networkMode 'always': the server is on this Mac, so the page
// keeps asking while macOS reports no network (D41).
import { QueryClient, useQuery } from "@tanstack/react-query";
import { ApiFailed, NoToken, apiGet } from "./client";
import { ApiMismatch, decodeJobDetail, decodeJobMetrics, decodeOverview, decodeSession } from "./decode";
import { noteServerNow, retick, setStaleAfter } from "../lib/clock";

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        networkMode: __FAULT_NETWORK_MODE_DEFAULT__ ? "online" : "always",
        retry: false, // the interval is the retry: a failure shows at once, and the next poll tries again
        refetchIntervalInBackground: false,
        refetchOnWindowFocus: true,
        staleTime: 0,
        gcTime: 5 * 60 * 1000,
      },
    },
  });
}

/** Errors after which polling stops: the page is replaced (no token) or must be reloaded (a different release). */
export function fatal(e: unknown): boolean {
  return e instanceof NoToken || e instanceof ApiMismatch;
}

function interval(ms: number) {
  return (q: { state: { error: unknown } }) => (fatal(q.state.error) ? false : ms);
}

export const POLL = { overview: 2000, job: 2000, log: 1000 };

export function useSession() {
  return useQuery({
    queryKey: ["session"],
    queryFn: async () => {
      const s = decodeSession(await apiGet("/api/session"));
      noteServerNow(s.now);
      setStaleAfter(s.stale_after_s);
      POLL.overview = s.poll_ms.overview;
      POLL.job = s.poll_ms.job;
      POLL.log = s.poll_ms.log;
      retick();
      return s;
    },
    staleTime: Infinity,
    refetchInterval: false,
  });
}

export function useOverview() {
  return useQuery({
    queryKey: ["overview"],
    queryFn: async () => {
      const o = decodeOverview(await apiGet("/api/overview"));
      noteServerNow(o.now);
      return o;
    },
    refetchInterval: interval(POLL.overview),
  });
}

export function useJob(id: string) {
  return useQuery({
    queryKey: ["job", id],
    queryFn: async () => {
      const d = decodeJobDetail(await apiGet(`/api/jobs/${id}`));
      noteServerNow(d.now);
      return d;
    },
    refetchInterval: (q) => {
      const e = q.state.error;
      if (fatal(e)) return false;
      if (e instanceof ApiFailed && e.status === 404) return 5000; // not listed (yet): ask less often
      return POLL.job;
    },
  });
}

/** The job's resource series: once when the detail opens (the server refreshes it at most every 30 s); again every
 * 30 s while the job runs, so a long-open detail keeps the whole run. Live points in between come from the job row. */
export function useJobMetrics(id: string, running: boolean, enabled: boolean) {
  return useQuery({
    queryKey: ["metrics", id],
    queryFn: async () => decodeJobMetrics(await apiGet(`/api/jobs/${id}/metrics`)),
    enabled,
    refetchInterval: running ? 30000 : false,
  });
}
