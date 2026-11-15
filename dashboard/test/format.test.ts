import { describe, expect, it } from "vitest";
import { age, bytes, bytesOf, duration, pct, shortDuration } from "../src/lib/format";
import { isStale, noteServerNow, serverNow, setStaleAfter } from "../src/lib/clock";

describe("format", () => {
  it("bytes in decimal units", () => {
    expect(bytes(22e9, 0)).toBe("22 GB");
    expect(bytes(9.81e9)).toBe("9.8 GB");
    expect(bytes(null)).toBe("—");
    expect(bytesOf(9.1e9, 16e9)).toBe("9.1 / 16 GB");
  });
  it("durations as the CLI writes them", () => {
    expect(duration(1084)).toBe("18m04s");
    expect(duration(7847)).toBe("2h10m47s");
    expect(duration(31)).toBe("31s");
    expect(shortDuration(1080)).toBe("18m");
  });
  it("percent and ages", () => {
    expect(pct(93.14)).toBe("93 %");
    expect(pct(4.94)).toBe("4.9 %");
    expect(age(2.4)).toBe("2 s ago");
    expect(age(240)).toBe("4 min ago");
  });
});

describe("D07 (the stale rule)", () => {
  it("a value older than the limit is stale, from the server's clock", () => {
    setStaleAfter(15);
    noteServerNow(Date.now() / 1000 + 100); // the server's clock runs 100 s ahead of the browser's
    const now = serverNow();
    expect(isStale(now - 14, now)).toBe(false);
    expect(isStale(now - 16, now)).toBe(true);
    expect(isStale(null, now)).toBe(true);
  });
});

describe("review fixes: sizes, the chart's time axis, notes", () => {
  it("one decimal for GB and above, none below", () => {
    expect(bytes(920e6)).toBe("920 MB");
    expect(bytes(665.4e6)).toBe("665 MB");
    expect(bytes(21.8e9)).toBe("21.8 GB");
    expect(bytes(12_300)).toBe("12 kB");
  });
  it("the time axis shows seconds under 10 minutes and the day when a run crosses midnight", async () => {
    const { timeLabels } = await import("../src/lib/format");
    const t0 = new Date(2026, 10, 14, 22, 40, 0).getTime() / 1000;
    expect(timeLabels([t0, t0 + 15, t0 + 30], 300)).toEqual(["22:40:00", "22:40:15", "22:40:30"]);
    expect(timeLabels([t0, t0 + 1800], 3600)).toEqual(["22:40", "23:10"]);
    const late = new Date(2026, 10, 14, 23, 50, 0).getTime() / 1000;
    expect(timeLabels([late, late + 1200], 3600)[0]).toBe("14 Nov 23:50");
  });
  it("a note does not repeat its state word", async () => {
    const { noteText } = await import("../src/features/jobs/JobTable");
    expect(noteText("cancelled", "cancelled while running")).toBe("cancelled while running");
    expect(noteText("failed", "exit 1")).toBe("failed: exit 1");
  });
});
