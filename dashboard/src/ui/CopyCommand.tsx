// CopyCommand: a `mu …` command with a copy button (docs/design/dashboard.md §4.7, §5.3). The page never acts: it
// offers the command. navigator.clipboard.writeText is called inside the click handler itself, before any await
// (WebKit refuses it outside the user's gesture); if the clipboard is refused, the command is selected for ⌘C and the
// button says so (D47). The primary command of a view has the orange fill: the one place orange is a fill.
import { Check, Copy } from "lucide-react";
import { useEffect, useRef, useState } from "react";

type Result = "idle" | "copied" | "select";

export function CopyCommand({ cmd, primary = false, label, block = false }: { cmd: string; primary?: boolean; label?: string; block?: boolean }) {
  const [res, setRes] = useState<Result>("idle");
  const codeRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (res === "idle") return;
    const t = setTimeout(() => setRes("idle"), res === "copied" ? 2000 : 6000);
    return () => clearTimeout(t);
  }, [res]);

  function selectIt() {
    const el = codeRef.current;
    const sel = window.getSelection();
    if (!el || !sel) return;
    const r = document.createRange();
    r.selectNodeContents(el);
    sel.removeAllRanges();
    sel.addRange(r);
    setRes("select");
  }

  function onClick() {
    if (__FAULT_COPY_AFTER_AWAIT__) {
      void (async () => {
        await new Promise((r) => setTimeout(r, 1500));
        await navigator.clipboard.writeText(cmd);
        setRes("copied");
      })();
      return;
    }
    let p: Promise<void>;
    try {
      p = navigator.clipboard.writeText(cmd); // first, inside the gesture
    } catch {
      selectIt();
      return;
    }
    p.then(() => setRes("copied"), selectIt);
  }

  const btn = primary
    ? "border-transparent bg-brand text-on-brand hover:brightness-95"
    : "border-border-strong bg-surface text-text hover:bg-surface-raised";
  return (
    <span className={`${block ? "flex" : "inline-flex"} min-w-0 max-w-full items-stretch overflow-hidden rounded-md border border-border bg-surface-raised text-sm`}>
      <code ref={codeRef} className={`min-w-0 truncate px-2 ${block ? "flex-1" : ""} py-1 font-mono text-[13px] text-text select-all`} title={cmd} data-cmd>
        {cmd}
      </code>
      <button
        type="button"
        onClick={onClick}
        className={`inline-flex shrink-0 items-center gap-1 border-l px-2 py-1 text-xs font-medium ${btn}`}
        data-copy-cmd={cmd}
        data-copy-result={res}
      >
        {res === "copied" ? <Check aria-hidden="true" size={13} /> : <Copy aria-hidden="true" size={13} />}
        <span>{res === "copied" ? "copied" : res === "select" ? "selected: press ⌘C" : (label ?? "copy")}</span>
        <span className="sr-only"> {cmd}</span>
      </button>
    </span>
  );
}
