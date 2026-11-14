// StateBadge: the only place state colours are applied (docs/design/dashboard.md §5.3). Never colour alone: every
// state has an icon and a word. Orange marks "running" only; errors are red, success a restrained green.
import {
  Ban, CircleAlert, CircleCheck, CircleDashed, CircleHelp, CircleSlash, CircleX, Clock3, CloudOff, KeyRound, LoaderCircle,
  MemoryStick, Play, RefreshCw, ShieldX, TimerOff, TriangleAlert, type LucideIcon,
} from "lucide-react";
import type { JobState, NodeState } from "../api/types";

export type Tone = "running" | "queued" | "succeeded" | "failed" | "timeout" | "muted" | "attention" | "online";

const TONE_TEXT: Record<Tone, string> = {
  running: "text-running",
  queued: "text-queued",
  succeeded: "text-succeeded",
  failed: "text-failed",
  timeout: "text-timeout",
  muted: "text-text-muted",
  attention: "text-attention",
  online: "text-succeeded",
};

export function toneClass(t: Tone): string {
  return __FAULT_FAINT_TEXT__ ? "text-text-faint" : TONE_TEXT[t];
}

type Look = { icon: LucideIcon; word: string; tone: Tone };

export const JOB_LOOK: Record<JobState, Look> = {
  running: { icon: Play, word: "running", tone: "running" },
  queued: { icon: Clock3, word: "queued", tone: "queued" },
  succeeded: { icon: CircleCheck, word: "succeeded", tone: "succeeded" },
  failed: { icon: CircleX, word: "failed", tone: "failed" },
  oom: { icon: MemoryStick, word: "oom", tone: "failed" },
  timeout: { icon: TimerOff, word: "timeout", tone: "timeout" },
  cancelled: { icon: Ban, word: "cancelled", tone: "muted" },
  interrupted: { icon: CircleSlash, word: "interrupted", tone: "attention" },
  unknown: { icon: CircleHelp, word: "unknown", tone: "muted" },
};

export type MachineState = NodeState | "reconnecting" | "old-node" | "stale";

export const MACHINE_LOOK: Record<MachineState, Look> = {
  online: { icon: CircleCheck, word: "online", tone: "online" },
  connecting: { icon: LoaderCircle, word: "connecting", tone: "muted" },
  offline: { icon: CloudOff, word: "offline", tone: "muted" },
  refused: { icon: ShieldX, word: "refused", tone: "failed" },
  cannot: { icon: TriangleAlert, word: "cannot connect", tone: "attention" },
  keychain: { icon: KeyRound, word: "Keychain locked", tone: "attention" },
  error: { icon: CircleAlert, word: "error", tone: "failed" },
  reconnecting: { icon: RefreshCw, word: "reconnecting…", tone: "muted" },
  "old-node": { icon: CircleDashed, word: "online · old μ", tone: "online" },
  stale: { icon: Clock3, word: "stale", tone: "muted" },
};

export function JobStateBadge({ state, position }: { state: JobState; position?: number | null }) {
  const l = JOB_LOOK[state];
  const Icon = l.icon;
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap ${toneClass(l.tone)}`} data-state={state}>
      <Icon aria-hidden="true" size={14} strokeWidth={2} className="shrink-0" />
      <span>{l.word}</span>
      {state === "queued" && position !== null && position !== undefined && (
        <span className="font-mono tabular-nums text-text-muted">#{position}</span>
      )}
    </span>
  );
}

export function MachineBadge({ state, label }: { state: MachineState; label?: string }) {
  const s: MachineState = __FAULT_REFUSED_AS_OFFLINE__ && state === "refused" ? "offline" : state;
  const l = MACHINE_LOOK[s];
  const Icon = l.icon;
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap text-xs font-medium ${toneClass(l.tone)}`} data-machine-state={s}>
      <Icon aria-hidden="true" size={13} strokeWidth={2.25} className="shrink-0" />
      <span>{label ?? l.word}</span>
    </span>
  );
}
