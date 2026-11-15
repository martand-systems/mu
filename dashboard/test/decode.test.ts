// D38 (the app's part): the decoders accept every golden /api fixture with no departure from the contract, clamp
// out-of-range numbers, cap long strings, refuse wrong types, and stop at an answer from a different API version.
// MU_DASH_FIXTURES points at another fixture folder (the must-fail run with the server's rename-field fault).
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  ApiMismatch, Ctx, decodeJobDetail, decodeJobMetrics, decodeJobsList, decodeLogPiece, decodeMacAnswer, decodeNodeAnswer,
  decodeOverview, decodeSession,
} from "../src/api/decode";

const dir = process.env.MU_DASH_FIXTURES || join(import.meta.dirname, "..", "..", "tools", "test-dashboard", "fixtures");
const load = (f: string) => JSON.parse(readFileSync(join(dir, f), "utf8")) as Record<string, unknown>;

const DECODER: Record<string, (v: unknown, c: Ctx) => unknown> = {
  "api-session.json": decodeSession,
  "api-overview.json": decodeOverview,
  "api-overview-offline.json": decodeOverview,
  "api-overview-old-node.json": decodeOverview,
  "api-overview-refused.json": decodeOverview,
  "api-jobs.json": decodeJobsList,
  "api-job.json": decodeJobDetail,
  "api-job-log.json": decodeLogPiece,
  "api-job-metrics.json": decodeJobMetrics,
  "api-node.json": decodeNodeAnswer,
  "api-mac.json": decodeMacAnswer,
};

describe("D38: every golden /api fixture decodes with no problem", () => {
  const files = readdirSync(dir).filter((n) => n.startsWith("api-") && n !== "api-error-401.json").sort();
  it("each fixture has a decoder", () => {
    expect(files.filter((f) => !DECODER[f])).toEqual([]);
  });
  for (const f of files) {
    it(f, () => {
      const c = new Ctx();
      DECODER[f]!(load(f), c);
      expect(c.problems).toEqual([]);
    });
  }
  it("the 401 answer is an error envelope", () => {
    expect(load("api-error-401.json")).toMatchObject({ api: 1, ok: false, error: "token" });
  });
});

describe("decoders on hostile and broken answers", () => {
  it("clamps out-of-range numbers and notes them", () => {
    const o = load("api-overview.json");
    const mac = { ...(o.mac as Record<string, unknown>), cpu_pct: 4000 };
    const c = new Ctx();
    const d = decodeOverview({ ...o, mac }, c);
    expect(d.mac.cpu_pct).toBe(100);
    expect(c.problems.join()).toContain("cpu_pct: out of range");
  });
  it("caps long strings", () => {
    const o = load("api-overview.json");
    const jobs = (o.jobs as Record<string, unknown>[]).map((j) => ({ ...j, name: "x".repeat(100000) }));
    const d = decodeOverview({ ...o, jobs });
    expect(d.jobs[0]!.name!.length).toBeLessThanOrEqual(201);
  });
  it("turns a wrong type into null, never a string where a number goes", () => {
    const o = load("api-overview.json");
    const jobs = (o.jobs as Record<string, unknown>[]).map((j) => ({ ...j, started_at: "<img src=x>", state: "<b>" }));
    const c = new Ctx();
    const d = decodeOverview({ ...o, jobs }, c);
    expect(d.jobs[0]!.started_at).toBeNull();
    expect(d.jobs[0]!.state).toBe("unknown");
    expect(c.problems.length).toBeGreaterThan(0);
  });
  it("drops a row whose job id is not a job id", () => {
    const o = load("api-overview.json");
    const jobs = [...(o.jobs as Record<string, unknown>[]), { ...(o.jobs as Record<string, unknown>[])[0]!, job_id: "../../etc" }];
    expect(decodeOverview({ ...o, jobs }).jobs.length).toBe((o.jobs as unknown[]).length);
  });
  it("a different API version stops the page (reload)", () => {
    expect(() => decodeOverview({ ...load("api-overview.json"), api: 2 })).toThrow(ApiMismatch);
  });
  it("garbage decodes to safe defaults", () => {
    const c = new Ctx();
    expect(() => decodeJobDetail({ api: 1, ok: true, job: 7, record: "x", node: null }, c)).not.toThrow();
    expect(c.problems.length).toBeGreaterThan(0);
  });
});
