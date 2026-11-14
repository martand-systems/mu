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
