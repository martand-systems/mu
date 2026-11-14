// Component tests (Vitest + Testing Library): states always have an icon and a word; the job table filters, sorts
// and links; the copy button's fallback; the node card's refused, offline and old-node states; no HTML from data.
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { decodeOverview } from "../src/api/decode";
import type { JobState } from "../src/api/types";
import { JobTable } from "../src/features/jobs/JobTable";
import { MacCard, NodeCard } from "../src/features/machines/MachineCards";
import { noteServerNow } from "../src/lib/clock";
import { CopyCommand } from "../src/ui/CopyCommand";
import { JOB_LOOK, JobStateBadge, MACHINE_LOOK, MachineBadge, type MachineState } from "../src/ui/StateBadge";
import { TooltipProvider } from "../src/ui/Tooltip";

const fx = (f: string) => decodeOverview(JSON.parse(readFileSync(join(import.meta.dirname, "..", "..", "tools", "test-dashboard", "fixtures", f), "utf8")));
const wrap = (n: ReactNode) => render(<TooltipProvider>{n}</TooltipProvider>);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("StateBadge", () => {
  it("every job state has an icon and a word", () => {
    for (const s of Object.keys(JOB_LOOK) as JobState[]) {
      const { container } = render(<JobStateBadge state={s} />);
      expect(container.querySelector("svg")).not.toBeNull();
      expect(container.textContent).toBe(JOB_LOOK[s].word);
      cleanup();
    }
  });
  it("every machine state has an icon and a word", () => {
    for (const s of Object.keys(MACHINE_LOOK) as MachineState[]) {
      const { container } = render(<MachineBadge state={s} />);
      expect(container.querySelector("svg")).not.toBeNull();
      expect(container.textContent!.length).toBeGreaterThan(2);
      cleanup();
    }
  });
  it("orange (running) marks only the running state", () => {
    for (const s of Object.keys(JOB_LOOK) as JobState[]) {
      const { container } = render(<JobStateBadge state={s} />);
      expect(container.querySelector(".text-running") !== null).toBe(s === "running");
      cleanup();
    }
  });
});

describe("JobTable", () => {
  const o = fx("api-overview.json");
  noteServerNow(o.now);
  it("defaults to this Mac's jobs; 'all workstations' shows the rest", () => {
    wrap(<JobTable jobs={o.jobs} nodes={o.nodes} more={o.more} firstNode="office-pc" />);
    const mine = o.jobs.filter((j) => j.mine).length;
    expect(screen.getByTestId("job-count").textContent).toContain(`${mine} jobs`);
    fireEvent.click(within(screen.getByTestId("filter-from")).getByRole("button", { name: "all workstations" }));
    expect(screen.getByTestId("job-count").textContent).toContain(`${o.jobs.length} jobs`);
  });
  it("filters by node", () => {
    wrap(<JobTable jobs={o.jobs} nodes={o.nodes} more={o.more} firstNode="office-pc" />);
    fireEvent.click(within(screen.getByTestId("filter-from")).getByRole("button", { name: "all workstations" }));
    fireEvent.click(within(screen.getByTestId("filter-node")).getByRole("button", { name: "lab-pc" }));
    const rows = document.querySelectorAll("tr[data-job]");
    expect(rows.length).toBe(o.jobs.filter((j) => j.node === "lab-pc").length);
    rows.forEach((r) => expect(r.getAttribute("data-node")).toBe("lab-pc"));
  });
  it("sorts by memory with a button header and aria-sort", () => {
    wrap(<JobTable jobs={o.jobs} nodes={o.nodes} more={o.more} firstNode="office-pc" />);
    const btn = screen.getByRole("button", { name: /Memory/ });
    fireEvent.click(btn);
    expect(btn.closest("th")!.getAttribute("aria-sort")).toBe("descending");
    const mems = [...document.querySelectorAll('td[data-col="mem"]')].map((td) => td.textContent).filter((t) => t !== "—");
    const val = (t: string | null) => parseFloat(t!) * (t!.includes("GB") ? 1e9 : t!.includes("MB") ? 1e6 : 1e3);
    for (let i = 1; i < mems.length; i++) expect(val(mems[i - 1]!)).toBeGreaterThanOrEqual(val(mems[i]!));
    fireEvent.click(btn);
    expect(btn.closest("th")!.getAttribute("aria-sort")).toBe("ascending");
    fireEvent.click(btn);
    expect(btn.closest("th")!.getAttribute("aria-sort")).toBe("none");
  });
  it("each job name is a real link to #job=<id>", () => {
    wrap(<JobTable jobs={o.jobs} nodes={o.nodes} more={o.more} firstNode="office-pc" />);
    const links = screen.getAllByRole("link");
    expect(links.length).toBe(o.jobs.filter((j) => j.mine).length);
    expect(links[0]!.getAttribute("href")).toMatch(/^#job=\d{8}-\d{6}-[0-9a-z]{6}$/);
  });
  it("empty: the command to submit one", () => {
    wrap(<JobTable jobs={[]} nodes={o.nodes} more={{}} firstNode="office-pc" />);
    expect(screen.getByText("No jobs yet")).toBeTruthy();
    expect(screen.getByText(/mu submit --on office-pc/)).toBeTruthy();
  });
  it("node text is rendered as text, never as HTML", () => {
    const jobs = o.jobs.map((j) => ({ ...j, name: '<img src=x onerror="window.__xss=1">' }));
    const { container } = wrap(<JobTable jobs={jobs} nodes={o.nodes} more={o.more} firstNode="office-pc" />);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getAllByText('<img src=x onerror="window.__xss=1">').length).toBeGreaterThan(0);
  });
});

describe("machine cards", () => {
  it("the node card shows the node's figures (D06's card part)", () => {
    const o = fx("api-overview.json");
    noteServerNow(o.now);
    const n = o.nodes.find((x) => x.name === "office-pc")!;
    wrap(<NodeCard n={n} macVersion="v2026.11.9" />);
    const card = screen.getByTestId("card-node-office-pc");
    expect(within(card).getByText(/WSL VM figures/)).toBeTruthy();
    expect(card.querySelector('[data-fig="node-avail"]')!.textContent).toBe(`${(n.stats!.mem.avail! / 1e9).toFixed(1)} GB`);
  });
  it("This Mac's card shows used and available (D08's card part)", () => {
    const o = fx("api-overview.json");
    noteServerNow(o.now);
    wrap(<MacCard mac={o.mac} />);
    const card = screen.getByTestId("card-mac");
    expect(card.querySelector('[data-fig="mac-avail"]')!.textContent).toBe(`${(o.mac.mem.available! / 1e9).toFixed(1)} GB`);
    expect(card.querySelector('[data-fig="mac-mem"]')!.textContent).toContain(`${(o.mac.mem.used! / 1e9).toFixed(1)} /`);
  });
  it("refused: the CLI's words, 'never retried', a retry button", () => {
    const o = fx("api-overview-refused.json");
    wrap(<NodeCard n={o.nodes[0]!} macVersion={null} />);
    expect(screen.getByText(/host key differs/)).toBeTruthy();
    expect(screen.getByText("never retried automatically")).toBeTruthy();
    expect(screen.getByRole("button", { name: /retry/ })).toBeTruthy();
  });
  it("old node: live statistics after the next node update", () => {
    const o = fx("api-overview-old-node.json");
    wrap(<NodeCard n={o.nodes[0]!} macVersion="v2026.11.9" />);
    expect(screen.getByText("live statistics after the next node update")).toBeTruthy();
    expect(screen.getByText("update μ on office-pc for live statistics")).toBeTruthy();
  });
});

describe("CopyCommand (D47's unit part)", () => {
  it("copies inside the click, then says copied", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    wrap(<CopyCommand cmd="mu log 20261114-101500-k3f9qa -f" />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });
    expect(writeText).toHaveBeenCalledWith("mu log 20261114-101500-k3f9qa -f");
    expect(screen.getByRole("button").textContent).toContain("copied");
  });
  it("selects the command and says so when the clipboard is refused", async () => {
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText: () => Promise.reject(new Error("denied")) } });
    wrap(<CopyCommand cmd="mu fetch x" />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button"));
    });
    expect(screen.getByRole("button").textContent).toContain("selected");
    expect(window.getSelection()!.toString()).toBe("mu fetch x");
  });
});
