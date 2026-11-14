// Radix Tooltip, the only Radix part of phase 1a (docs/design/dashboard.md §3.2): non-modal, no <style> element.
// The content is extra: every tooltip repeats something that is also readable without it (an absolute time, a
// button's own accessible name).
import { Tooltip as T } from "radix-ui";
import type { ReactElement, ReactNode } from "react";

export function TooltipProvider({ children }: { children: ReactNode }) {
  return <T.Provider delayDuration={300} skipDelayDuration={200}>{children}</T.Provider>;
}

export function Tip({ content, children, side = "top" }: { content: ReactNode; children: ReactElement; side?: "top" | "bottom" | "left" | "right" }) {
  return (
    <T.Root>
      <T.Trigger asChild>{children}</T.Trigger>
      <T.Portal>
        <T.Content
          side={side}
          sideOffset={6}
          collisionPadding={8}
          className="tip z-50 max-w-xs rounded-sm border border-border-strong bg-surface-raised px-2 py-1 text-xs text-text shadow-sm"
        >
          {content}
        </T.Content>
      </T.Portal>
    </T.Root>
  );
}
