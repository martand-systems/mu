// Whole-page states (docs/design/dashboard.md §4.6, §5.3): the no-token page, the error page of the boundary, and
// the page-level banners (the server not answering; reload after an update).
import { CircleAlert, KeyRound, PlugZap, RotateCw } from "lucide-react";
import { Component, type ReactNode } from "react";
import { Banner } from "../../ui/Banner";
import { CopyCommand } from "../../ui/CopyCommand";
import { useJobRoute } from "../../lib/route";
import { Brand } from "../top/TopBar";

function Page({ children, testid }: { children: ReactNode; testid: string }) {
  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col justify-center gap-5 px-4 py-12" data-testid={testid}>
      <Brand />
      {children}
    </main>
  );
}

/** No token, or a token from an earlier server start (401). The page holds no data. */
export function NoTokenPage() {
  const job = useJobRoute(); // a `mu submit` link (#job=<id>) keeps its job: open lands on it
  const cmd = job ? `mu dashboard open ${job}` : "mu dashboard open";
  return (
    <Page testid="no-token">
      <div className="flex flex-col gap-3 rounded-md border border-border bg-surface p-5">
        <h2 className="flex items-center gap-2 text-base font-semibold text-text">
          <KeyRound aria-hidden="true" size={16} className="text-text-muted" />
          This page has no current token
        </h2>
        <p className="text-sm text-text-muted">
          The dashboard shows data only to a page opened by <span className="font-mono text-text">mu dashboard open</span>, which
          hands the page a token that changes each time the server starts. Run it in a terminal (or ask Claude to):
        </p>
        <CopyCommand cmd={cmd} primary block what="open" />
        <p className="text-xs text-text-muted">A link printed by <span className="font-mono">mu submit</span> carries no token on purpose: it never appears in a terminal or a log.</p>
      </div>
    </Page>
  );
}

/** The error boundary's page: a fixed message and the commands. It renders NO error text: an error's message can
 * carry node strings (D46). */
export function ErrorPage({ detail }: { detail?: string }) {
  return (
    <Page testid="error-page">
      <div className="flex flex-col gap-3 rounded-md border border-border bg-surface p-5">
        <h2 className="flex items-center gap-2 text-base font-semibold text-text">
          <CircleAlert aria-hidden="true" size={16} className="text-failed" />
          The dashboard hit an unexpected error
        </h2>
        <p className="text-sm text-text-muted">Nothing changed on any node or job. The same information is in the terminal:</p>
        <div className="flex flex-col gap-2">
          <CopyCommand cmd="mu status" block what="status" />
          <CopyCommand cmd="mu dashboard" block what="dashboard status" />
        </div>
        {detail !== undefined && <pre className="whitespace-pre-wrap text-xs text-text-muted">{detail}</pre>}
        <div>
          <button type="button" onClick={() => window.location.reload()} className="inline-flex items-center gap-1.5 rounded-md border border-border-strong bg-surface px-3 py-1.5 text-sm font-medium text-text hover:bg-surface-raised">
            <RotateCw aria-hidden="true" size={14} /> Reload
          </button>
        </div>
      </div>
    </Page>
  );
}

export class Boundary extends Component<{ children: ReactNode }, { failed: boolean; message: string }> {
  state = { failed: false, message: "" };
  static getDerivedStateFromError(e: unknown) {
    return { failed: true, message: __FAULT_BOUNDARY_SHOWS_ERROR__ && e instanceof Error ? e.message : "" };
  }
  render() {
    if (this.state.failed) return <ErrorPage detail={__FAULT_BOUNDARY_SHOWS_ERROR__ ? this.state.message : undefined} />;
    return this.props.children;
  }
}

export function ServerDownBanner() {
  return (
    <Banner tone="failed" icon={PlugZap} title="The dashboard server is not running" testid="server-down"
      action={<CopyCommand cmd="mu dashboard on" primary what="start" />}>
      Every figure below is as of its last answer. Start it again (or ask Claude to); the page reconnects by itself.
    </Banner>
  );
}

export function ReloadBanner() {
  return (
    <Banner tone="accent" icon={RotateCw} title="Reload: the dashboard was updated" testid="reload"
      action={
        <button type="button" onClick={() => window.location.reload()} className="rounded-md bg-brand px-3 py-1 text-sm font-medium text-on-brand hover:brightness-95">
          Reload
        </button>
      }>
      This page and the server are from different μ releases.
    </Banner>
  );
}
