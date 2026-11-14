// Card: a bordered panel (separation by 1 px borders, not shadows: §5.3).
import type { ReactNode } from "react";

export function Card({ children, className = "", label, testid }: { children: ReactNode; className?: string; label?: string; testid?: string }) {
  return (
    <section aria-label={label} data-testid={testid} className={`min-w-0 rounded-md border border-border bg-surface ${className}`}>
      {children}
    </section>
  );
}

export function SectionTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border px-3 py-2">
      <h2 className="text-xs font-semibold uppercase tracking-wider text-text-muted">{children}</h2>
      {right && <div className="flex min-w-0 items-center gap-2">{right}</div>}
    </div>
  );
}
