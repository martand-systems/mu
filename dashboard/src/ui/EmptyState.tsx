// EmptyState: what to do next, with the command (§5.3).
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export function EmptyState({ icon: Icon, title, children }: { icon: LucideIcon; title: ReactNode; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
      <Icon aria-hidden="true" size={20} className="text-text-muted" />
      <p className="text-base font-medium text-text">{title}</p>
      {children && <div className="flex max-w-xl flex-col items-center gap-2 text-sm text-text-muted">{children}</div>}
    </div>
  );
}
