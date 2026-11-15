// A two-state button: <button aria-pressed> (never Radix Toggle, §3.2).
import type { LucideIcon } from "lucide-react";

export function ToggleButton({ pressed, onChange, icon: Icon, children, title }: { pressed: boolean; onChange: (v: boolean) => void; icon: LucideIcon; children: string; title?: string }) {
  return (
    <button
      type="button"
      aria-pressed={pressed}
      title={title}
      onClick={() => onChange(!pressed)}
      className={`inline-flex items-center gap-1 rounded-sm border px-2 py-0.5 text-xs font-medium ${pressed ? "border-border-strong bg-surface-raised text-text" : "border-border bg-surface text-text-muted hover:text-text"}`}
    >
      <Icon aria-hidden="true" size={12} />
      {children}
    </button>
  );
}
