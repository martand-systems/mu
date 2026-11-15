// LogView: the job's log as it grows (docs/design/dashboard.md §5.3). It asks /api/jobs/<id>/log by byte offset,
// every second while following and the tab is visible, and keeps asking at once while the server holds more
// (`next < size`). Text only, in one <pre>; the scroll area is native overflow: auto (never Radix ScrollArea, which
// injects a <style> element). Pause and wrap are <button aria-pressed>.
import { Pause, ScrollText, WrapText } from "lucide-react";
import { ScrollArea } from "radix-ui";
import { useEffect, useLayoutEffect, useReducer, useRef, useState } from "react";
import { NoToken, apiGet } from "../../api/client";
import { decodeLogPiece } from "../../api/decode";
import { POLL } from "../../api/queries";
import { bytes } from "../../lib/format";
import { ToggleButton } from "../../ui/Toggle";
import { LogBuffer } from "./logBuffer";

type Status = { following: boolean; eof: boolean; size: number; truncated: boolean; error: string | null; started: boolean };

export function useLog(id: string, paused: boolean, queued: boolean) {
  const buf = useRef(new LogBuffer());
  const next = useRef(0);
  const [, bump] = useReducer((x: number) => x + 1, 0);
  const [st, setSt] = useState<Status>({ following: false, eof: false, size: 0, truncated: false, error: null, started: false });

  useEffect(() => {
    buf.current = new LogBuffer();
    next.current = 0;
    setSt({ following: false, eof: false, size: 0, truncated: false, error: null, started: false });
    bump();
  }, [id]);

  const eof = st.eof;
  useEffect(() => {
    if (paused || eof) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const schedule = (ms: number) => {
      if (alive) timer = setTimeout(run, ms);
    };
    async function run() {
      if (!alive) return;
      if (document.visibilityState !== "visible") return schedule(POLL.log);
      try {
        const p = decodeLogPiece(await apiGet(`/api/jobs/${id}/log?offset=${next.current}`));
        if (!alive) return;
        buf.current.append(p.text);
        next.current = Math.max(p.next, next.current);
        setSt((s) => ({
          following: p.following, eof: p.eof, size: p.size, truncated: s.truncated || p.truncated_before,
          error: p.error ? p.error.message : null, started: true,
        }));
        bump();
        if (p.eof) return;
        schedule(p.next < p.size ? 50 : queued ? POLL.log * 3 : POLL.log);
      } catch (e) {
        if (!alive || e instanceof NoToken) return;
        schedule(POLL.log * 3);
      }
    }
    void run();
    const onVis = () => {
      if (document.visibilityState === "visible" && timer === null) void run();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      alive = false;
      if (timer !== null) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [id, paused, eof, queued]);

  return { buf: buf.current, st };
}

export function LogView({ id, queued, logCmd, frozen = false }: { id: string; queued: boolean; logCmd: string; frozen?: boolean }) {
  const [paused, setPaused] = useState(false);
  const [wrap, setWrap] = useState(false);
  const { buf, st } = useLog(id, paused, queued);
  const box = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const text = buf.render();

  useLayoutEffect(() => {
    const el = box.current;
    if (el && atBottom.current && !paused) el.scrollTop = el.scrollHeight;
  }, [text, paused]);

  const status = st.eof ? "complete" : paused ? "paused" : st.following ? "following" : st.started ? "waiting" : "connecting";
  const pre = (
    <pre className={`m-0 p-3 font-mono text-[12.5px] leading-[1.55] text-text ${wrap ? "whitespace-pre-wrap break-all" : "whitespace-pre"}`} data-testid="log-text">
      {text}
    </pre>
  );
  return (
    <section aria-label="log" className="flex min-w-0 flex-col rounded-md border border-border bg-surface" data-testid="log">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border px-3 py-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-text-muted">Log</h2>
        <span className={`text-xs ${status === "following" && !frozen ? "text-accent-text" : "text-text-muted"}`} data-testid="log-status">{status}</span>
        <span className="font-mono text-xs tabular-nums text-text-muted">{bytes(st.size)}</span>
        <span className="ml-auto flex items-center gap-1.5">
          {status !== "complete" && status !== "waiting" && !queued && (
            <ToggleButton pressed={paused} onChange={setPaused} icon={Pause}>pause</ToggleButton>
          )}
          <ToggleButton pressed={wrap} onChange={setWrap} icon={WrapText}>wrap</ToggleButton>
        </span>
      </div>
      {(st.truncated || buf.dropped > 0) && (
        <p className="border-b border-border px-3 py-1.5 text-xs text-text-muted" data-testid="log-earlier">
          earlier lines: <code className="font-mono text-text">{logCmd.replace(/ -f\b/, "")}</code>
        </p>
      )}
      {st.error && <p className="border-b border-border px-3 py-1.5 text-xs text-text-muted">the log could not be read from the node: {st.error}</p>}
      {__FAULT_RADIX_SCROLLAREA__ ? (
        <ScrollArea.Root className="h-[min(60vh,560px)]">
          <ScrollArea.Viewport className="h-full">{pre}</ScrollArea.Viewport>
          <ScrollArea.Scrollbar orientation="vertical"><ScrollArea.Thumb /></ScrollArea.Scrollbar>
        </ScrollArea.Root>
      ) : (
        <div
          ref={box}
          tabIndex={0}
          role="region"
          aria-label={`log of job ${id}`}
          onScroll={(e) => {
            const el = e.currentTarget;
            atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
          }}
          className="log-scroll h-[min(60vh,560px)] overflow-auto"
        >
          {text === "" ? (
            <p className="flex items-center gap-2 p-3 text-sm text-text-muted">
              <ScrollText aria-hidden="true" size={14} />
              {queued ? "The job is queued: its log starts when it runs." : st.started ? "No output yet." : "Reading the log…"}
            </p>
          ) : (
            pre
          )}
        </div>
      )}
    </section>
  );
}
