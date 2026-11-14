// D41: polling survives "offline". The server is on this Mac, so the overview keeps fetching on its interval while
// TanStack Query's onlineManager says the browser is offline (networkMode: 'always').
import { QueryClientProvider, onlineManager } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { POLL, makeQueryClient, useOverview } from "../src/api/queries";

const fixture = readFileSync(join(import.meta.dirname, "..", "..", "tools", "test-dashboard", "fixtures", "api-overview.json"), "utf8");

describe("D41: polling while the browser reports no network", () => {
  afterEach(() => {
    onlineManager.setOnline(true);
    vi.unstubAllGlobals();
  });
  it("the overview query keeps fetching", async () => {
    const fetchMock = vi.fn(async () => new Response(fixture, { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    window.localStorage.setItem("mu.token", "a".repeat(64));
    POLL.overview = 100;
    onlineManager.setOnline(false);
    const qc = makeQueryClient();
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
    const { unmount } = renderHook(() => useOverview(), { wrapper });
    await new Promise((r) => setTimeout(r, 750));
    unmount();
    qc.clear();
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(3);
  });
});
