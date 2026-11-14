// Banner: one message at a time per scope (page, card, detail): refused, offline, reconnecting, old node, reload,
// server not answering (§5.3). Not a live region: a banner that stays across refreshes is not re-announced.
import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export type BannerTone = "failed" | "attention" | "muted" | "accent";

const TONE: Record<BannerTone, { box: string; icon: string }> = {
  failed: { box: "border-failed/40 bg-surface", icon: "text-failed" },
  attention: { box: "border-attention/40 bg-surface", icon: "text-attention" },
  muted: { box: "border-border-strong bg-surface", icon: "text-text-muted" },
  // accent: the one orange is the banner's own button; the box and icon stay neutral
  accent: { box: "border-border-strong bg-surface", icon: "text-text-muted" },
};

export function Banner({ tone, icon: Icon, title, children, action, testid }: { tone: BannerTone; icon: LucideIcon; title: ReactNode; children?: ReactNode; action?: ReactNode; testid?: string }) {
  const t = TONE[tone];
  return (
    <div className={`flex items-start gap-3 rounded-md border px-3 py-2.5 ${t.box}`} data-testid={testid}>
      <Icon aria-hidden="true" size={16} className={`mt-0.5 shrink-0 ${t.icon}`} />
      <div className="min-w-0 flex-1 text-sm">
        <p className="font-medium text-text">{title}</p>
        {children && <div className="mt-0.5 text-text-muted">{children}</div>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}
