// The μ dashboard's one page (docs/design/dashboard.md §5.1): the top bar, then either the machine cards and the
// job table, or one job's detail (#job=<id>). Every state of §5.3 is decided here or in the component it belongs to.
import { QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState, useSyncExternalStore } from "react";
import { NoToken, ServerDown } from "./api/client";
import { ApiMismatch } from "./api/decode";
import { makeQueryClient, useOverview, useSession } from "./api/queries";
import { token } from "./api/token";
import type { Overview } from "./api/types";
import { JobDetailView } from "./features/job-detail/JobDetail";
import { JobTable } from "./features/jobs/JobTable";
import { MachineCards } from "./features/machines/MachineCards";
import { Boundary, NoTokenPage, ReloadBanner, ServerDownBanner } from "./features/states/States";
import { TopBar } from "./features/top/TopBar";
import { useJobRoute } from "./lib/route";
import { Skeleton } from "./ui/Skeleton";
import { TooltipProvider } from "./ui/Tooltip";

const queryClient = makeQueryClient();

// a lazily loaded chunk that 404s after an update (Vite's vite:preloadError): "reload: the dashboard was updated"
let updated = false;
const updListeners = new Set<() => void>();
window.addEventListener("vite:preloadError", (e) => {
  e.preventDefault();
  updated = true;
  for (const l of updListeners) l();
});
function useUpdated(): boolean {
  return useSyncExternalStore((fn) => {
    updListeners.add(fn);
    return () => updListeners.delete(fn);
  }, () => updated);
}

function LoadingPage() {
  return (
    <div className="flex flex-col gap-4" data-testid="loading">
      <div className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr))]">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-[186px] rounded-md border border-border bg-surface p-3">
            <Skeleton lines={4} label="loading the machines" />
          </div>
        ))}
      </div>
      <div className="rounded-md border border-border bg-surface p-3">
        <Skeleton lines={5} label="loading the jobs" />
      </div>
    </div>
  );
}

function asServerDown(o: Overview): Overview {
  // server-down-as-offline (D46's fault): the server's own failure shown as every node offline
  return { ...o, nodes: o.nodes.map((n) => ({ ...n, state: "offline" as const, stats: null })) };
}

function Dashboard() {
  const session = useSession();
  const ov = useOverview();
  const jobId = useJobRoute();
  const upd = useUpdated();
  const errs = [session.error, ov.error];
  if (errs.some((e) => e instanceof NoToken)) return <NoTokenPage />;
  const mismatch = upd || errs.some((e) => e instanceof ApiMismatch);
  const down = ov.error instanceof ServerDown;
  let data = ov.data;
  if (__FAULT_SERVER_DOWN_AS_OFFLINE__ && down && data) data = asServerDown(data);
  const showDown = down && !__FAULT_SERVER_DOWN_AS_OFFLINE__;
  return (
    <>
      <TopBar session={session.data} overview={data} updatedAt={ov.dataUpdatedAt ? (data?.now ?? null) : null} down={showDown} />
      <main className="mx-auto flex max-w-[1440px] flex-col gap-4 px-4 py-4 md:px-6 md:py-5">
        {mismatch && <ReloadBanner />}
        {showDown && <ServerDownBanner />}
        {!data ? (
          mismatch ? null : down || ov.isError ? (
            <p className="text-sm text-text-muted" data-testid="no-data">No data yet.</p>
          ) : (
            <LoadingPage />
          )
        ) : jobId ? (
          <JobDetailView key={jobId} id={jobId} />
        ) : (
          <>
            <MachineCards mac={data.mac} nodes={data.nodes} jobs={data.jobs} />
            <JobTable jobs={data.jobs} nodes={data.nodes} more={data.more} firstNode={data.nodes[0]?.name ?? null} />
          </>
        )}
      </main>
    </>
  );
}

function Faults() {
  // style-tag and inner-html-lib (D29's faults): code that inserts a <style> element, and a helper outside lint's
  // reach that sets innerHTML; the CSP and Trusted Types must stop both
  useEffect(() => {
    if (__FAULT_STYLE_TAG__) {
      const s = document.createElement("style");
      s.textContent = ".mu-fault { color: red }";
      document.head.appendChild(s);
    }
    if (__FAULT_INNER_HTML_LIB__) {
      try {
        Reflect.set(document.createElement("div"), "inner" + "HTML", "<b>fault</b>");
      } catch {
        // Trusted Types throw; the violation event is what the test sees
      }
    }
  }, []);
  return null;
}

export function App() {
  const [hasToken] = useState(() => token() !== null);
  if (!hasToken) return <NoTokenPage />;
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <Boundary>
          {(__FAULT_STYLE_TAG__ || __FAULT_INNER_HTML_LIB__) && <Faults />}
          <Dashboard />
        </Boundary>
      </TooltipProvider>
    </QueryClientProvider>
  );
}
