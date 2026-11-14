// D43 (the tokens' part): every state and series colour against the backgrounds, in both themes, computed from
// tokens.css: text and icons >= 4.5:1, chart lines and marks >= 3:1. text-faint is never used for information.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(join(import.meta.dirname, "..", "src", "styles", "tokens.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

function block(selector: string): Record<string, string> {
  const i = css.indexOf(selector);
  const body = css.slice(css.indexOf("{", i) + 1, css.indexOf("}", i));
  const out: Record<string, string> = {};
  for (const m of body.matchAll(/--([a-z-]+):\s*([^;]+);/g)) out[m[1]!] = m[2]!.trim();
  return out;
}

function resolve(vars: Record<string, string>, name: string): string {
  let v = vars[name]!;
  for (let i = 0; i < 5 && v.startsWith("var("); i++) v = vars[v.slice(6, -1)]!;
  return v;
}

function lum(hex: string): number {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const f = (x: number) => (x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4);
  return 0.2126 * f(c[0]!) + 0.7152 * f(c[1]!) + 0.0722 * f(c[2]!);
}

function ratio(a: string, b: string): number {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x! + 0.05) / (y! + 0.05);
}

const TEXT = ["text", "text-muted", "accent-text", "state-running", "state-queued", "state-succeeded", "state-failed",
  "state-oom", "state-timeout", "state-cancelled", "state-stale", "state-offline", "state-refused", "state-attention"];
const MARKS = ["accent", "focus-ring", "series-cores", "series-mem", "series-cache", "series-limit", "border-strong"];
const BACKS = ["bg", "surface", "surface-raised"];

const dark = block('[data-theme="dark"]');
if (__FAULT_FAINT_SERIES__) dark["series-cache"] = "#262626"; // D43's fault: the cache series at 1.5:1 in dark

describe.each([["light", block(":root,")], ["dark", dark]])("%s theme", (_name, vars) => {
  for (const back of BACKS) {
    for (const t of TEXT) {
      it(`${t} on ${back} >= 4.5:1`, () => {
        expect(ratio(resolve(vars, t), resolve(vars, back))).toBeGreaterThanOrEqual(4.5);
      });
    }
    for (const m of MARKS.filter((x) => x !== "border-strong")) {
      it(`${m} on ${back} >= 3:1`, () => {
        expect(ratio(resolve(vars, m), resolve(vars, back))).toBeGreaterThanOrEqual(3);
      });
    }
  }
  it("text on the brand (the primary button) >= 4.5:1", () => {
    expect(ratio(resolve(vars, "on-brand"), resolve(vars, "brand"))).toBeGreaterThanOrEqual(4.5);
  });
  it("text-faint is below 4.5:1 on surface (decoration only, never information)", () => {
    expect(ratio(resolve(vars, "text-faint"), resolve(vars, "surface"))).toBeLessThan(4.5);
  });
});
