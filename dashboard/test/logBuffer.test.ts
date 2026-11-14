// LogView's buffer (§5.3): pieces join across a split line; the page keeps 5,000 lines or 2 MB; a line is cut at
// 10 kB with "… N bytes cut".
import { describe, expect, it } from "vitest";
import { LINE_CAP, LogBuffer, MAX_LINES } from "../src/features/job-detail/logBuffer";

describe("log buffer", () => {
  it("joins a line split across pieces", () => {
    const b = new LogBuffer();
    b.append("step 1\nste");
    b.append("p 2\n");
    expect(b.render()).toBe("step 1\nstep 2");
  });
  it("keeps the last 5,000 lines and counts the dropped ones", () => {
    const b = new LogBuffer();
    b.append(Array.from({ length: MAX_LINES + 10 }, (_, i) => `line ${i}`).join("\n") + "\n");
    expect(b.lines.length).toBe(MAX_LINES);
    expect(b.dropped).toBe(10);
    expect(b.lines[0]!.text).toBe("line 10");
  });
  it("cuts a 1 MB line at 10 kB and says how much was cut", () => {
    const b = new LogBuffer();
    b.append("x".repeat(600000));
    b.append("x".repeat(400000) + "\nnext\n");
    expect(b.lines[0]!.text.length).toBe(LINE_CAP);
    expect(b.lines[0]!.cut).toBe(1000000 - LINE_CAP);
    expect(b.render()).toContain(`… ${1000000 - LINE_CAP} bytes cut\nnext`);
  });
  it("keeps markup as text", () => {
    const b = new LogBuffer();
    b.append('<img src=x onerror="window.__xss=1">\n');
    expect(b.render()).toBe('<img src=x onerror="window.__xss=1">');
  });
});
