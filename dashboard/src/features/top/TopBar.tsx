// The top bar (48 px): the μ mark, the overall node status, the last update's age, the theme toggle (§5.3). The
// only global status. The tab's title carries the state ("▶ 2 running · μ").
import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect } from "react";
import type { Overview, Session } from "../../api/types";
import { setTheme, useThemeChoice, type ThemeChoice } from "../../lib/theme";
import { AgeLabel } from "../../ui/AgeLabel";
import { Tip } from "../../ui/Tooltip";

export function Brand() {
  return (
    <span className="flex items-center gap-2">
      <span aria-hidden="true" className="flex h-6 w-6 items-center justify-center rounded-sm bg-black font-semibold text-brand ring-1 ring-inset ring-white/10">μ</span>
      <span className="text-[15px] font-semibold tracking-tight text-text">
        <span className="sr-only">μ </span>dashboard
      </span>
    </span>
  );
}

function ThemeToggle() {
  const c = useThemeChoice();
  const opts: { v: ThemeChoice; icon: typeof Sun; label: string }[] = [
    { v: "system", icon: Monitor, label: "Theme: follow the system" },
    { v: "light", icon: Sun, label: "Theme: light" },
    { v: "dark", icon: Moon, label: "Theme: dark" },
  ];
  return (
    <div role="group" aria-label="Theme" className="flex items-center rounded-md border border-border bg-surface p-0.5">
      {opts.map((o) => {
        const Icon = o.icon;
        const on = c === o.v;
        return (
          <Tip key={o.v} content={o.label} side="bottom">
            <button
              type="button"
              aria-pressed={on}
              aria-label={o.label}
              onClick={() => setTheme(o.v)}
              className={`flex h-6 w-7 items-center justify-center rounded-sm ${on ? "bg-surface-raised text-text ring-1 ring-inset ring-border-strong" : "text-text-muted hover:text-text"}`}
            >
              <Icon aria-hidden="true" size={14} />
            </button>
          </Tip>
        );
      })}
    </div>
  );
}

export function TopBar({ session, overview, updatedAt, down = false }: { session: Session | undefined; overview: Overview | undefined; updatedAt: number | null; down?: boolean }) {
  const s = overview?.summary;
  useEffect(() => {
    if (!s) return;
    document.title = s.running > 0 ? `▶ ${s.running} running · μ` : s.queued > 0 ? `… ${s.queued} queued · μ` : "μ dashboard";
  }, [s]);
  return (
    <header className="sticky top-0 z-40 border-b border-border bg-bg/95 backdrop-blur-sm">
      <div className="mx-auto flex h-12 max-w-[1440px] items-center gap-3 px-4 md:gap-4 md:px-6">
        <h1 className="flex items-center"><Brand /></h1>
        {session?.dev && (
          <span className="hidden truncate rounded-sm border border-border-strong px-1.5 py-0.5 text-xs text-text-muted md:inline" data-testid="dev-badge">
            development · fake nodes{session.scenario ? ` · ${session.scenario}` : ""}
          </span>
        )}
        <div className="ml-auto flex min-w-0 items-center gap-3 md:gap-4">
          {down && (
            <span className="hidden items-center gap-1.5 text-xs font-medium text-failed sm:flex">
              <span aria-hidden="true" className="inline-block h-1.5 w-1.5 rounded-full bg-failed" />
              server not answering
            </span>
          )}
          {s && !down && (
            <span className="hidden items-center gap-1.5 text-xs text-text-muted sm:flex" data-testid="nodes-online">
              <span aria-hidden="true" className={`inline-block h-1.5 w-1.5 rounded-full ${s.nodes > 0 && s.online === s.nodes ? "bg-succeeded" : s.online > 0 ? "bg-attention" : "bg-text-muted"}`} />
              <span className="tabular-nums text-text">{s.online} of {s.nodes}</span> {s.nodes === 1 ? "node" : "nodes"} online
            </span>
          )}
          {updatedAt !== null && <span data-testid="updated" className="min-w-0 truncate"><AgeLabel t={updatedAt} prefix="updated " /></span>}
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
