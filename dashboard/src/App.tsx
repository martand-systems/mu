// Phase 1a placeholder (part 1: the pipeline). The real page — machine cards, the job table, the job detail and every
// state of §5.3 — replaces this. It shows the session, or the no-token / server-down states, as text only.
import { useEffect, useState } from "react";
import { ApiFailed, NoToken, ServerDown, apiGet } from "./api/client";
import type { Session } from "./api/types";

type View = { kind: "loading" } | { kind: "ok"; s: Session } | { kind: "no-token" } | { kind: "down" } | { kind: "error" };

export function App() {
  const [view, setView] = useState<View>({ kind: "loading" });
  useEffect(() => {
    let live = true;
    apiGet("/api/session")
      .then((b) => live && setView({ kind: "ok", s: b as Session }))
      .catch((e: unknown) => {
        if (!live) return;
        if (e instanceof NoToken) setView({ kind: "no-token" });
        else if (e instanceof ServerDown) setView({ kind: "down" });
        else if (e instanceof ApiFailed) setView({ kind: "error" });
        else setView({ kind: "error" });
      });
    return () => {
      live = false;
    };
  }, []);
  return (
    <main className="mx-auto max-w-[1440px] px-4 py-6 font-sans text-base text-text md:px-6">
      <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight">
        <span className="text-brand" aria-hidden="true">μ</span>
        <span>dashboard</span>
      </h1>
      <p className="mt-4 text-text-muted" data-testid="state">
        {view.kind === "loading" && "Loading…"}
        {view.kind === "ok" && `Connected to μ ${view.s.version ?? "(unreleased)"}${view.s.dev ? " · development, fake nodes" : ""}.`}
        {view.kind === "no-token" && "This page has no current token: run  mu dashboard open  in a terminal (or ask Claude to)."}
        {view.kind === "down" && "The dashboard server is not running: mu dashboard on (or ask Claude)."}
        {view.kind === "error" && "The dashboard could not read its server: mu dashboard (status), then reload."}
      </p>
    </main>
  );
}
