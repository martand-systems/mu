// Scheduler release 2 on the page (the integration contract §6), against the paused fixtures:
// D48 (decoder part): paused and resuming are display states of a running job, derived from `paused`;
// D49: the time bar counts run time only; samples taken while paused break the cores series and are hatched.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { Ctx, decodeJobDetail, decodeJobMetrics, decodeOverview } from "../src/api/decode";
import { schedText, timeLabel } from "../src/lib/sched";
import { resourceColumns, timeBar } from "../src/lib/series";

const dir = join(import.meta.dirname, "..", "..", "tools", "test-dashboard", "fixtures");
const load = (f: string) => JSON.parse(readFileSync(join(dir, f), "utf8")) as unknown;
const ov = decodeOverview(load("api-overview-paused.json"), new Ctx());
const byName = (n: string) => ov.jobs.find((j) => j.name === n && j.state !== "succeeded")!;

describe("D48: display states from the paused fixtures", () => {
  it("a frozen lender is 'paused', its state stays running", () => {
    const j = byName("frame-fea");
    expect(j.state).toBe("running");
    expect(j.display).toBe("paused"); // paused-as-running: the decoder drops `paused`, display is null
  });
  it("a job whose resume was asked is 'resuming'", () => {
    expect(byName("cfd-wing").display).toBe("resuming");
  });
  it("a held job decodes as held (1a decoded it as unknown)", () => {
    expect(byName("mesh-study").state).toBe("held");
  });
  it("the wording: paused, resuming, held, borrower", () => {
    const now = ov.now;
    expect(schedText(byName("frame-fea"), now)).toMatch(/^paused by avi-mac until \d\d:\d\d · resumes by itself$/);
    expect(schedText(byName("cfd-wing"), now)).toMatch(/^resuming: waits for [0-9a-z]{6} \(ends by \d\d:\d\d\)$/);
    expect(schedText(byName("mesh-study"), now)).toMatch(/^held by avi-mac \d\d:\d\d · keeps place \d+$/);
    expect(schedText(byName("sched-probe"), now)).toMatch(/^on cores lent by [0-9a-z]{6} until \d\d:\d\d$/);
  });
});

describe("D49: run time and paused samples", () => {
  const d = decodeJobDetail(load("api-job-paused.json"), new Ctx());
  it("the time bar counts run time, not the paused minutes", () => {
    const tb = timeBar(d.job, d.now)!;
    expect(tb.source).toBe("node");
    expect(tb.paused_s).toBeGreaterThanOrEqual(300);
    expect(tb.run_s).toBe(d.job.time!.run_s); // count-paused-in-run: run_s + paused_s
    expect(tb.run_s).toBeLessThan(d.now - d.job.started_at!);
    expect(timeLabel(tb.run_s, tb.limit_s, tb.paused_s)).toMatch(/run of 6h.* · 5m.* paused \(not counted\)$/);
  });
  it("paused samples break the cores series with a null and are one hatched span; memory goes on", () => {
    const m = decodeJobMetrics(load("api-job-metrics-paused.json"), new Ctx());
    const pausedTs = m.samples.filter((s) => s.paused).map((s) => s.t);
    expect(pausedTs.length).toBeGreaterThan(10);
    const cols = resourceColumns(m.samples);
    for (const t of pausedTs) {
      const i = cols.t.indexOf(t);
      expect(cols.cores[i]).toBeNull(); // paused-cpu-zero: 0, a line drawn through the pause
      expect(cols.work[i]).not.toBeNull();
    }
    expect(cols.paused).toHaveLength(1);
    expect(cols.paused[0]![0]).toBe(pausedTs[0]);
    expect(cols.paused[0]![1]).toBe(pausedTs[pausedTs.length - 1]);
    const iPaused = cols.t.indexOf(pausedTs[pausedTs.length - 1]!);
    const iBefore = cols.t.indexOf(pausedTs[0]!) - 1;
    expect(cols.max[iPaused]!).toBeLessThan(cols.max[iBefore]!); // the limit line steps down while frozen
  });
});

// The same page code on the real node's answers: tools/test-jobs/r2_answers.py runs release 2's node code (node.py,
// pausectl.py) against stubs of systemd and sends what `mu node serve` printed through the Mac's server
// (tools/test-dashboard/fixtures/real-r2/, written by `python3 -I -S tools/test-jobs/r2_answers.py DIR`; unit.py's
// section r2-shapes checks that the dashfake fixtures above keep the real answers' keys, types and states).
describe("D48R: the real node's release 2 answers, through the Mac's server", () => {
  const real = (f: string) => load(join("real-r2", f));
  const rov = decodeOverview(real("api-overview.json"), new Ctx());
  const job = (short: string) => rov.jobs.find((j) => j.job_id.endsWith(short))!;
  it("the real node's frozen lender is 'paused', its state stays running", () => {
    expect(job("k3f9qa").state).toBe("running");
    expect(job("k3f9qa").display).toBe("paused"); // paused-as-running
  });
  it("the real node's lender whose resume waits for its borrower is 'resuming'", () => {
    expect(job("r7mm2x").display).toBe("resuming");
  });
  it("held, borrowing, the bands and a finished job that was paused decode", () => {
    expect(job("h3ld00").state).toBe("held");
    expect(job("2icaxr").class).toBe("borrowing");
    expect(job("2icaxr").grant?.borrowed[0]?.from).toBe("20261114-120102-k3f9qa");
    expect(job("urg3nt").band).toBe("urgent");
    expect(job("l0wl0w").band).toBe("low");
    const done = job("d0n3d0");
    expect(done.state).toBe("succeeded");
    expect(done.paused_s).toBeGreaterThan(0);
  });
  it("the wording on the real answers", () => {
    const now = rov.now;
    expect(schedText(job("k3f9qa"), now)).toMatch(/^paused by avi-mac until \d\d:\d\d · resumes by itself$/);
    expect(schedText(job("r7mm2x"), now)).toMatch(/^resuming: waits for b8b8b8 \(ends by \d\d:\d\d\)$/);
    expect(schedText(job("h3ld00"), now)).toMatch(/^held by avi-mac \d\d:\d\d · keeps place 2$/);
    expect(schedText(job("2icaxr"), now)).toMatch(/^on cores lent by k3f9qa until \d\d:\d\d$/);
  });
  it("the real paused job's detail: its time bar is the node's run time", () => {
    const d = decodeJobDetail(real("api-job-paused.json"), new Ctx());
    const tb = timeBar(d.job, d.now)!;
    expect(tb.source).toBe("node");
    expect(tb.run_s).toBe(d.job.time!.run_s);
    expect(d.job.display).toBe("paused");
  });
});
