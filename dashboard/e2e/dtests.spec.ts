// The page's acceptance tests of phase 1a (docs/design/dashboard.md §7.1), each against a fresh
// `mu dashboard serve --dev` with fake nodes. Every test has a fault that must make it fail (the must-fail runs in
// .github/workflows/dashboard.yml and tools/test-dashboard/e2e-faults.sh): a server fault (MU_JOBS_FAULT, put in by
// the launcher) or a page fault (a build with MU_DASH_FAULT). Chromium runs all; WebKit runs D05-D07, D16, D29, D42
// and D47 (Safari is the Mac's default browser); D29 includes every scenario and page state. Every test also fails on any CSP or Trusted Types violation (D29,
// the auto fixture in helpers.ts: every page of every context the test opens). Each key assertion carries a message
// "Dnn: …": a must-fail run passes only when the failure is that message (tools/test-dashboard/e2e-faults.sh).
import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";
import { apiJson, expect, gotoJob, openDash, restartServer, setTheme, stopServer, test, type Dash } from "./helpers";

const WEBKIT_TOO = /^(D05|D06|D07|D16|D29|D42|D47|D48)\b/;
test.beforeEach(({ browserName }, info) => {
  test.skip(browserName === "webkit" && !WEBKIT_TOO.test(info.title), "Chromium only (§7.1)");
});

type Row = { job: string; node: string; state: string; name: string };

async function rows(page: Page): Promise<Row[]> {
  return page.locator("tr[data-job]").evaluateAll((trs) =>
    trs.map((tr) => ({
      job: tr.getAttribute("data-job") ?? "",
      node: tr.getAttribute("data-node") ?? "",
      state: tr.getAttribute("data-state") ?? "",
      name: tr.querySelector("a,div")?.textContent ?? "",
    })),
  );
}

async function allJobs(page: Page): Promise<void> {
  await page.getByTestId("filter-from").getByRole("button", { name: "all workstations" }).click();
}


// ------------------------------------------------------------------------------------------------ D04, D39
test("D04 an old node (no features): the jobs are listed and the card says to update μ", async ({ page, serve }) => {
  const d = await serve("old-node");
  await openDash(page, d);
  const card = page.getByTestId("card-node-office-pc");
  await expect(card.getByText(/^office-pc runs μ v\S+, older than this Mac's: live figures start after it updates$/), "D04: the card says the node is behind this Mac").toBeVisible();
  await expect(card.locator('[data-machine-state="old-node"]'), "D04: the card says the node is behind this Mac").toBeVisible();
  await expect(page.locator("tr[data-job]").first()).toBeVisible();
  expect((await rows(page)).every((r) => r.node === "office-pc")).toBe(true);
  // the job detail of an old node: no resource chart, elapsed only
  const id = await page.locator('tr[data-state="succeeded"]').first().getAttribute("data-job");
  await gotoJob(page, id);
  await expect(page.getByTestId("resources-old-node"), "D04: the detail of an old node's job has no chart").toBeVisible();
  await expect(page.getByTestId("resource-chart")).toHaveCount(0);
});

test("D04b a v2026.11.10 node (features without stats): the old-node card, no error", async ({ page, serve }) => {
  const d = await serve("r1-node");
  await openDash(page, d);
  const card = page.getByTestId("card-node-office-pc");
  await expect(card.locator('[data-machine-state="old-node"]'), "D04b: the r1 node's card asks for an update").toBeVisible({ timeout: 15000 });
  await expect(card.getByText(/live figures start after it updates$/), "D04b: the r1 node's card asks for an update").toBeVisible();
  await page.waitForTimeout(4000); // a watch or stats call would have been refused by now
  await expect(card.locator('[data-machine-state="error"]'), "D04b: no refused verb turns the card to error").toHaveCount(0);
  await page.getByTestId("filter-from").getByRole("button", { name: "all workstations" }).click();
  const held = page.locator('tr[data-state="held"]');
  await expect(held, "D04b: a held job is shown held").toHaveCount(1);
  await expect(page.locator(`[data-note-for="${await held.getAttribute("data-job")}"]`)).toContainText(/^held by avi-mac \d\d:\d\d · keeps place \d+$/);
});

// ------------------------------------------------------------------------------------------------ D48
test("D48 scheduler release 2 on the page: paused, resuming, held, borrower and bands", async ({ page, serve }) => {
  const d = await serve("paused");
  await openDash(page, d);
  await page.getByTestId("filter-from").getByRole("button", { name: "all workstations" }).click();
  const named = (n: string) => page.locator("tr[data-job]").filter({ has: page.getByRole("link", { name: n, exact: true }) }).filter({ hasNot: page.locator('[data-state="succeeded"]') });
  const lender = named("frame-fea");
  const lid = (await lender.first().getAttribute("data-job"))!;
  await expect(lender.first().locator('[data-state="paused"]'), "D48: the frozen lender shows paused, not running").toBeVisible({ timeout: 15000 });
  await expect(page.locator(`[data-note-for="${lid}"]`)).toContainText(/^paused by avi-mac until \d\d:\d\d · resumes by itself$/);
  // a paused job never shows orange: no running-coloured text in its row
  const orange = await lender.first().evaluate((el) => {
    const run = getComputedStyle(document.documentElement).getPropertyValue("--state-running").trim();
    const probe = document.createElement("span");
    probe.style.color = run;
    document.body.append(probe);
    const want = getComputedStyle(probe).color;
    probe.remove();
    return [...el.querySelectorAll("*")].some((x) => getComputedStyle(x).color === want);
  });
  expect(orange, "D48: no orange on a paused job").toBe(false);
  const res = named("cfd-wing").first();
  await expect(res.locator('[data-state="resuming"]'), "D48: resuming").toBeVisible();
  await expect(page.locator(`[data-note-for="${await res.getAttribute("data-job")}"]`)).toContainText(/^resuming: waits for sched-probe \(ends by \d\d:\d\d\)$/);
  const bor = named("sched-probe").first();
  await expect(page.locator(`[data-note-for="${await bor.getAttribute("data-job")}"]`), "D48: the borrower").toContainText(/^on cores lent by frame-fea until \d\d:\d\d$/);
  await expect(page.locator('tr[data-state="held"] [data-state="held"]'), "D48: held").toBeVisible();
  await expect(page.locator('[data-band="urgent"]'), "D48: the urgent band chip").toBeVisible();
  await expect(page.locator('[data-band="low"]'), "D48: the low band chip").toBeVisible();
  await expect(page.locator('[data-band="normal"]')).toHaveCount(0);
  // the node card: what a paused job keeps, and the paused count
  const card = page.getByTestId("card-node-office-pc");
  await expect(card.locator(`[data-job-line="${lid}"]`), "D48: the card says what the paused job keeps").toContainText(/keeps .* \(paused\)/);
  await expect(card.locator('[data-fig="node-queue"]')).toContainText("2 paused · 1 held");
  // the detail: the resume command, the time bar's words
  await gotoJob(page, lid);
  await expect(page.getByTestId("commands").getByText(/^mu resume \S+$/), "D48: the paused job's command is mu resume").toBeVisible();
  await expect(page.getByTestId("log-status"), "D48: no orange on a paused job").not.toHaveClass(/text-accent-text/);
  await expect(page.getByTestId("time-label"), "D48: the time bar counts run time").toContainText(/run of 6h.* paused \(not counted\) · resumes by itself at \d\d:\d\d$/);
  await expect(page.getByTestId("paused-legend"), "D48: the chart marks the paused span").toBeVisible({ timeout: 15000 });
  await axe(page, "paused detail");
  await page.goto(`${d.url}/`);
  await page.getByTestId("filter-from").getByRole("button", { name: "all workstations" }).click();
  await axe(page, "paused overview");
  const heldId = await page.locator('tr[data-state="held"]').getAttribute("data-job");
  await gotoJob(page, heldId);
  await expect(page.getByTestId("commands").getByText(/^mu release \S+$/), "D48: the held job's command is mu release").toBeVisible();
  // a held job has not started: no log session, no chart (review 2026-11-15: it showed "following" and a skeleton)
  await expect(page.getByTestId("queued-note"), "D48: a held job's page says held, no log or chart").toContainText(/^Held by avi-mac, keeps place \d+ on office-pc\. The log and the chart start when it runs\.$/);
  await expect(page.getByTestId("log-status")).toHaveCount(0);
});

test("D39 old-node budget, the page: each state change of the job is shown within 20 s", async ({ page, serve }) => {
  test.setTimeout(150000);
  const d = await serve("old-node");
  const t0 = Date.now();
  await openDash(page, d);
  const row = page.locator("tr[data-job]").filter({ hasText: "frame-fea" }).first();
  await expect(row).toHaveAttribute("data-state", "queued");
  const id = (await row.getAttribute("data-job"))!;
  const r = page.locator(`tr[data-job="${id}"]`);
  // queued -> running at +20 s, -> succeeded at +60 s (the scenario's clock starts with the server)
  await expect(r, "D39: running within 20 s").toHaveAttribute("data-state", "running", { timeout: 20000 + 20000 - (Date.now() - t0) });
  await expect(r, "D39: succeeded within 20 s").toHaveAttribute("data-state", "succeeded", { timeout: 60000 + 20000 - (Date.now() - t0) });
});

// ------------------------------------------------------------------------------------------------ D05
test("D05 jobs by node: each job under its own node, following queued -> running -> succeeded", async ({ page, serve }) => {
  test.setTimeout(120000);
  const d = await serve("busy");
  const t0 = Date.now();
  await openDash(page, d);
  await allJobs(page);
  // both nodes' lists have arrived
  await expect.poll(async () => new Set((await rows(page)).map((r) => r.node)).size, { timeout: 15000, message: "D05: lab-pc's jobs are listed under lab-pc" }).toBe(2);
  const rs = await rows(page);
  // the scenario: lab-pc holds cfd-cavity (queued at the start) and aero-vlm-wing; everything else is office-pc's
  const lab = rs.filter((r) => r.node === "lab-pc").map((r) => r.name).sort();
  expect(lab, "D05: lab-pc's jobs are listed under lab-pc").toEqual(["aero-vlm-wing", "cfd-cavity"]);
  expect(rs.filter((r) => r.node === "office-pc").length).toBeGreaterThanOrEqual(6);
  // the API agrees with the table
  const api = (await apiJson(page, d, "/api/jobs?from=all")).jobs as { job_id: string; node: string }[];
  for (const r of rs) expect(api.find((j) => j.job_id === r.job)?.node).toBe(r.node);
  // lab-pc's job: running at +10 s and succeeded at +40 s, each shown within 15 s of the node's change
  const job = rs.find((r) => r.node === "lab-pc" && r.name === "cfd-cavity")!;
  const row = page.locator(`tr[data-job="${job.job}"]`);
  await expect(row).toHaveAttribute("data-state", "running", { timeout: 10000 + 15000 - (Date.now() - t0) });
  await expect(row).toHaveAttribute("data-state", "succeeded", { timeout: 40000 + 15000 - (Date.now() - t0) });
  await expect(row).toHaveAttribute("data-node", "lab-pc");
});

// ------------------------------------------------------------------------------------------------ D06
test("D06 figures are right: the node card and the job rows show the fake node's own numbers", async ({ page, serve }) => {
  test.setTimeout(90000);
  const d = await serve("busy");
  await openDash(page, d);
  await allJobs(page);
  const card = page.getByTestId("card-node-office-pc");
  await expect(card.locator('[data-fig="node-avail"]')).toBeVisible();
  // the fake node's raw `stats` answer (a dev-only route): what the node said, before this server's processing of it
  type Raw = { stats: { host: { mem: { total: number; avail: number } }; running: { job_id: string; cpu_cores: number; anon: number; shmem: number }[] } };
  const raw = async () => ((await apiJson(page, d, "/api/dev/fake-stats/office-pc")) as unknown as Raw).stats;
  const gb = (n: number) => `${(n / 1e9).toFixed(1)} GB`;
  // the card shows the node's available and used memory (used = total - available)
  await expect
    .poll(async () => {
      const n = await raw();
      const avail = await card.locator('[data-fig="node-avail"]').textContent();
      const mem = await card.locator('[data-fig="node-mem"]').textContent();
      return avail === gb(n.host.mem.avail) && (mem ?? "").includes(`${((n.host.mem.total - n.host.mem.avail) / 1e9).toFixed(1)} /`);
    }, { timeout: 15000, message: "D06: the card shows the node's memory" })
    .toBe(true);
  // each running job's row: working memory (anon + shmem) and cores as the node says
  const running = (await raw()).running;
  expect(running.length, "D06: the node runs two jobs").toBe(2);
  for (const j of running) {
    const row = page.locator(`tr[data-job="${j.job_id}"]`);
    await expect
      .poll(async () => {
        const now = (await raw()).running.find((x) => x.job_id === j.job_id)!;
        // the node's figures move every 5 s and the row is up to one poll behind: within one step of them
        const mem = parseFloat((await row.locator('td[data-col="mem"]').textContent()) ?? "");
        const cores = parseFloat((await row.locator('td[data-col="cores"]').textContent()) ?? "");
        return Math.abs(mem - (now.anon + now.shmem) / 1e9) <= 0.35 && Math.abs(cores - now.cpu_cores) <= 0.1 * now.cpu_cores + 0.1;
      }, { timeout: 15000, message: "D06: each running job's row shows the node's figures" })
      .toBe(true);
  }
});

// ------------------------------------------------------------------------------------------------ D07
test("D07 stale is shown: after the link is cut every node value carries its age, grey, 'stale'", async ({ page, serve }) => {
  test.setTimeout(90000);
  const d = await serve("stale");
  const t0 = Date.now();
  await openDash(page, d);
  const card = page.getByTestId("card-node-office-pc");
  await expect(card.locator('[data-age="live"]')).toBeVisible();
  const running = page.locator('tr[data-state="running"]').first();
  await expect(running, "D07: the scenario has a running job").toBeVisible();
  // the link is cut at +20 s; within 15 s more (and a poll) every value is stale
  await expect(card.locator('[data-age="stale"]'), "D07: the card's figures turn stale").toBeVisible({ timeout: 20000 + 15000 + 10000 - (Date.now() - t0) });
  await expect(card.locator('[data-age="live"]'), "D07: the card's figures turn stale").toHaveCount(0);
  await expect(card.getByText(/stale · as of \d\d:\d\d:\d\d/)).toBeVisible();
  await expect(card.locator('[data-fig="cpu"]')).toHaveClass(/text-stale/);
  const n = (await apiJson(page, d, "/api/nodes/office-pc")) as { stats: { stale: boolean } | null; state: string };
  expect(n.stats === null || n.stats.stale || n.state !== "online").toBe(true);
  // the running job's live figures turn stale too: marked once on the row, the figures grey, "as of" in its title
  await expect(running, "D07: the running row is marked stale").toHaveAttribute("data-stale", "1");
  await expect(running.locator('td[data-col="cores"]')).toHaveClass(/text-stale/);
  await expect(running).toHaveAttribute("title", /as of \d\d:\d\d:\d\d/);
});

// ------------------------------------------------------------------------------------------------ D14, D15
test("D14 a token from an earlier server start shows the no-token page, with no data", async ({ page, serve }) => {
  let d = await serve("busy");
  await openDash(page, d);
  await expect(page.locator("tr[data-job]").first()).toBeVisible();
  const names = (await rows(page)).map((r) => r.name);
  d = await restartServer(d);
  await page.goto(`${d.url}/`);
  await expect(page.getByTestId("no-token"), "D14: the no-token page").toBeVisible({ timeout: 15000 });
  await expect(page.getByText("mu dashboard open").first()).toBeVisible();
  const text = await page.locator("body").innerText();
  for (const n of names) expect(text).not.toContain(n);
  expect(await page.locator("tr[data-job]").count()).toBe(0);
});

test("D15 the token leaves the address bar and the history", async ({ page, serve }) => {
  const d: Dash = await serve("idle");
  await page.goto(`${d.url}/#t=${d.token}&job=20261114-101500-k3f9qa`);
  await expect(page.getByTestId("job-detail").or(page.getByText("No node lists this job"))).toBeVisible();
  expect(page.url(), "D15: no token in the address bar").not.toContain(d.token);
  expect(page.url()).toContain("#job=20261114-101500-k3f9qa");
  expect(await page.evaluate(() => window.location.href), "D15: no token in the address bar").not.toContain("t=");
  // the entry with #t= was replaced, not pushed: going back never reaches it
  await page.goBack().catch(() => null);
  await page.waitForTimeout(300);
  expect(page.url(), "D15: no token in the history").not.toContain(d.token);
  // and a `mu submit` link without a token keeps its job: the no-token page offers `mu dashboard open <id>`
  const fresh = await page.context().browser()!.newContext();
  const p2 = await fresh.newPage();
  await p2.goto(`${d.url}/#job=20261114-101500-k3f9qa`);
  await expect(p2.getByTestId("no-token")).toBeVisible();
  await expect(p2.locator("code", { hasText: "mu dashboard open 20261114-101500-k3f9qa" })).toBeVisible();
  await fresh.close();
});

// ------------------------------------------------------------------------------------------------ D16
test("D16 hostile node text: nothing is created from it, nothing runs, the page stays responsive", async ({ page, serve }) => {
  test.setTimeout(90000);
  const d = await serve("hostile");
  await page.goto(`${d.url}/#t=${d.token}`);
  await expect(page.getByTestId("machines")).toBeVisible();
  await allJobs(page);
  await expect(page.locator("tr[data-job]").first()).toBeVisible({ timeout: 15000 });
  await page.waitForTimeout(1000);
  const made = () => page.evaluate(() => ({
    xss: (window as unknown as { __xss?: unknown }).__xss ?? null,
    imgs: [...document.querySelectorAll("img")].length,
    scripts: [...document.querySelectorAll("script")].filter((s) => !s.src).length,
  }));
  expect(await made(), "D16: nothing is created from node text and nothing runs").toEqual({ xss: null, imgs: 0, scripts: 0 });
  const hostile = page.locator("tr[data-job]").filter({ hasText: "onerror" }).first();
  await expect(hostile, "D16: node text is shown as text").toBeVisible();
  await expect(hostile.getByText('<img src=x onerror="window.__xss=1">', { exact: false }), "D16: node text is shown as text").toBeVisible();
  // the hostile job's detail: its log holds HTML, ANSI escapes and a 1 MB line
  const id = (await hostile.getAttribute("data-job"))!;
  await gotoJob(page, id);
  await expect(page.getByTestId("log-text")).toContainText("<script>", { timeout: 20000 });
  await expect(page.getByTestId("log-text")).toContainText("bytes cut", { timeout: 30000 });
  const t = Date.now();
  const r = await made();
  expect(Date.now() - t, "D16: the page stays responsive").toBeLessThan(2000);
  expect(r, "D16: nothing is created from node text and nothing runs").toEqual({ xss: null, imgs: 0, scripts: 0 });
  await page.getByRole("button", { name: "wrap" }).click(); // still answers clicks
  await expect(page.getByRole("button", { name: "wrap" })).toHaveAttribute("aria-pressed", "true");
});

// ------------------------------------------------------------------------------------------------ D18
test("D18 offline is grey with 'offline since' and a retry countdown; refused is red, never retried, with a retry button", async ({ page, serve }) => {
  const off = await serve("offline");
  await openDash(page, off);
  const oc = page.getByTestId("card-node-office-pc");
  await expect(oc.locator('[data-machine-state="offline"]'), "D18: offline is shown as offline").toBeVisible({ timeout: 15000 });
  await expect(oc.getByText(/offline since \d\d:\d\d:\d\d/)).toBeVisible();
  await expect(oc.getByText(/next try in/)).toBeVisible();
  await expect(page.getByTestId("card-node-lab-pc").locator('[data-machine-state="online"]')).toBeVisible();
  const ref = await serve("refused");
  await openDash(page, ref);
  const rc = page.getByTestId("card-node-office-pc");
  await expect(rc.locator('[data-machine-state="refused"]'), "D18: refused is red").toBeVisible({ timeout: 15000 });
  await expect(rc.locator('[data-machine-state="refused"]'), "D18: refused is red").toHaveClass(/text-failed/);
  await expect(rc.getByText(/host key differs/)).toBeVisible();
  await expect(rc.getByText("never retried automatically")).toBeVisible();
  await rc.getByRole("button", { name: /retry now/ }).click();
  await expect(rc.getByRole("button", { name: /asked once more/ })).toBeVisible();
});

// ------------------------------------------------------------------------------------------------ D26
async function axe(page: Page, where: string): Promise<void> {
  const r = await new AxeBuilder({ page: page as never }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${where}: ${v.id} (${v.impact}) ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`), "D26: axe").toEqual([]);
}

/** Every element that reacts to a click is a control: a link, a button, or something focusable with a role. A React
 * onClick on a plain element, or a pointer cursor that starts on one, is a fault (div-button). The job row is the one
 * exception: its name is the link (checked below). */
async function noClickableNonControls(page: Page): Promise<void> {
  const bad = await page.evaluate(() =>
    [...document.querySelectorAll("body *")].filter((e) => {
      const k = Object.keys(e).find((x) => x.startsWith("__reactProps$"));
      const props = k ? (e as unknown as Record<string, { onClick?: unknown }>)[k] : undefined;
      const parent = e.parentElement;
      const cursorHere = getComputedStyle(e).cursor === "pointer" && (!parent || getComputedStyle(parent).cursor !== "pointer");
      if (!(typeof props?.onClick === "function" || cursorHere)) return false;
      if (e.matches("a[href], button, input, select, textarea, summary, [role=button][tabindex], [tabindex='0']")) return false;
      if (e.matches("tr[data-job]") && e.querySelector("td a[href]")) return false;
      if (e.matches("time[data-age]")) return false; // a tooltip's trigger (the full time on hover), not a control
      return true;
    }).map((e) => `${e.tagName.toLowerCase()} ${(e.textContent ?? "").slice(0, 30)}`));
  expect(bad, "D26: clickable elements that are not controls").toEqual([]);
}

async function tabThrough(page: Page): Promise<void> {
  // every visible control is reachable with Tab and has an accessible name
  const controls = await page.locator("a[href], button, select, [tabindex='0']").evaluateAll((els) =>
    els.filter((e) => (e as HTMLElement).offsetParent !== null).map((e, i) => {
      e.setAttribute("data-tab-i", String(i));
      return `${i} ${e.tagName} ${(e.getAttribute("aria-label") ?? e.textContent ?? "").slice(0, 40)}`;
    }));
  await page.locator("body").focus();
  const seen = new Set<string>();
  for (let i = 0; i < controls.length + 10; i++) {
    await page.keyboard.press("Tab");
    const f = await page.evaluate(() => {
      const a = document.activeElement as HTMLElement | null;
      if (!a || a === document.body) return null;
      const name = a.getAttribute("aria-label") || a.textContent?.trim() || a.getAttribute("title") || "";
      return { i: a.getAttribute("data-tab-i"), what: `${a.tagName} ${(a.textContent ?? "").slice(0, 40)}`, name };
    });
    if (f) {
      expect(f.name, `D26: accessible name of ${f.what}`).not.toBe("");
      if (f.i !== null) seen.add(f.i);
    }
  }
  for (const c of controls) expect(seen.has(c.split(" ")[0]!), `D26: reachable with Tab: ${c}`).toBe(true);
  await noClickableNonControls(page);
  // every row's name is a link
  const names = await page.locator("tr[data-job] td:nth-child(3)").evaluateAll((tds) => tds.map((td) => td.querySelector("a[href]") !== null));
  expect(names.every(Boolean), "D26: clickable elements that are not controls").toBe(true);
}

async function noLiveChurn(page: Page): Promise<void> {
  const live = () => page.locator("[aria-live], [role=status], [role=alert], [role=log], [role=timer]").evaluateAll((els) => els.map((e) => e.textContent));
  const a = await live();
  await page.waitForTimeout(4500);
  expect(await live(), "D26: no live-region churn").toEqual(a);
}

/** The overview has settled: no card connecting or loading, the table (or its empty state) shown. */
async function settled(page: Page): Promise<void> {
  await expect(page.getByTestId("machines")).toBeVisible({ timeout: 15000 });
  await expect.poll(() => page.locator('[data-machine-state="connecting"], [data-testid="skeleton"], [data-testid="loading"]').count(), { timeout: 20000 }).toBe(0);
  await expect(page.getByTestId("jobs")).toBeVisible();
}

const SCENARIOS = ["busy", "idle", "offline", "refused", "stale", "old-node", "hostile", "many", "wake"];

test("D26 accessibility: keyboard, names, labels, no live churn, axe clean in light and dark, every scenario and state", async ({ page, serve, browser, violations }) => {
  test.setTimeout(420000);
  for (const sc of SCENARIOS) {
    const d = await serve(sc);
    await openDash(page, d);
    await allJobs(page);
    await settled(page);
    for (const theme of ["light", "dark"] as const) {
      await setTheme(page, theme);
      await axe(page, `${sc} ${theme}`);
    }
    await noClickableNonControls(page);
    if (sc === "busy") {
      await tabThrough(page);
      await noLiveChurn(page);
      // every state colour has its word: each badge's text is not empty
      const words = await page.locator("span[data-state] > span, [data-machine-state] span").evaluateAll((s) => s.map((x) => x.textContent?.trim()));
      expect(words.every((w) => w), "D26: every state has a word").toBe(true);
      for (const st of ["running", "queued", "failed"]) {
        const id = await page.locator(`tr[data-state="${st}"]`).first().getAttribute("data-job");
        await gotoJob(page, id);
        if (st === "running") await expect(page.getByTestId("resource-chart")).toBeVisible({ timeout: 15000 });
        for (const theme of ["light", "dark"] as const) {
          await setTheme(page, theme);
          await axe(page, `job detail (${st}) ${theme}`);
        }
        await tabThrough(page);
        await page.goto(`${d.url}/`);
        await allJobs(page);
        await settled(page);
      }
      // page states: empty, server down
      await page.route("**/api/overview", async (r) => {
        const resp = await r.fetch();
        await r.fulfill({ response: resp, json: { ...((await resp.json()) as object), jobs: [] } });
      });
      await page.goto(`${d.url}/`);
      await expect(page.getByText("No jobs yet")).toBeVisible();
      for (const theme of ["light", "dark"] as const) {
        await setTheme(page, theme);
        await axe(page, `empty ${theme}`);
      }
      await page.unroute("**/api/overview");
      await page.goto(`${d.url}/`);
      await settled(page);
      await page.route("**/api/**", (r) => r.abort("connectionrefused"));
      await expect(page.getByTestId("server-down")).toBeVisible({ timeout: 10000 });
      await axe(page, "server down");
      await page.unroute("**/api/**");
      // the no-token page
      const ctx = await browser.newContext();
      await violations.watch(ctx);
      const p2 = await ctx.newPage();
      await p2.goto(`${d.url}/`);
      await expect(p2.getByTestId("no-token")).toBeVisible();
      await axe(p2, "no token");
      await ctx.close();
    }
  }
});

// ------------------------------------------------------------------------------------------------ D29
test("D29 no CSP or Trusted Types violation with every overlay opened", async ({ page, serve }) => {
  test.setTimeout(120000);
  const d = await serve("busy");
  const resp = await page.goto(`${d.url}/#t=${d.token}`);
  const csp = resp?.headers()["content-security-policy"] ?? "";
  for (const bad of ["unsafe-inline", "unsafe-eval", "'nonce-", "'sha256-", "'sha384-", "'sha512-"]) expect(csp, "D29: the CSP").not.toContain(bad);
  expect(csp, "D29: the CSP").toContain("require-trusted-types-for 'script'");
  await expect(page.getByTestId("machines")).toBeVisible();
  // tooltips: an age, the theme buttons (hover and keyboard focus)
  await page.locator("time[data-age]").first().hover();
  await expect(page.getByRole("tooltip").first()).toBeAttached({ timeout: 5000 });
  await page.getByRole("button", { name: "Theme: dark" }).focus();
  await page.getByRole("button", { name: "Theme: dark" }).click();
  await page.getByRole("button", { name: "Theme: light" }).click();
  await page.getByRole("button", { name: "Theme: follow the system" }).click();
  // the filters
  await allJobs(page);
  await page.getByTestId("filter-node").getByRole("button", { name: "lab-pc" }).click();
  await page.getByTestId("filter-node").getByRole("button", { name: "all", exact: true }).click();
  // sorting
  await page.getByRole("button", { name: /Memory/ }).click();
  // the job detail: log (pause, wrap), the chart and its table, the copy buttons
  const id = await page.locator('tr[data-state="running"]').first().getAttribute("data-job");
  await page.locator(`tr[data-job="${id}"] a`).click();
  await expect(page.getByTestId("log-text")).toBeVisible({ timeout: 15000 });
  await page.getByRole("button", { name: "pause" }).click();
  await page.getByRole("button", { name: "wrap" }).click();
  await expect(page.getByTestId("resource-chart")).toBeVisible();
  await page.locator(".resource-pane .u-over").first().hover();
  await page.getByRole("button", { name: "show as table" }).click();
  await expect(page.getByTestId("sample-table")).toBeVisible();
  await page.getByRole("button", { name: "copy status command" }).click();
  await page.waitForTimeout(500);
  await page.getByRole("link", { name: "Jobs" }).click();
  await expect(page.getByTestId("machines")).toBeVisible();
  // every scenario and page state: the next test, "D29 states" (both engines)
});

test("D29 states: every scenario, job detail and page state loads without a violation (both engines)", async ({ page, serve, browser, violations }) => {
  test.setTimeout(480000);
  for (const sc of SCENARIOS) {
    const d = await serve(sc);
    await openDash(page, d);
    await allJobs(page);
    await settled(page);
    if (sc === "refused") await page.getByRole("button", { name: /retry now/ }).click();
    await page.waitForTimeout(300);
    expect(violations.list, "CSP or Trusted Types violations (D29)").toEqual([]); // fail at the first scenario that has one
    if (sc === "busy" || sc === "old-node" || sc === "hostile") {
      for (const st of ["running", "queued", "succeeded", "failed", "oom"]) {
        const row = page.locator(`tr[data-state="${st}"]`).first();
        if (!(await row.count())) continue;
        await gotoJob(page, await row.getAttribute("data-job"));
        await page.waitForTimeout(1500);
        await page.goto(`${d.url}/`);
        await settled(page);
        await allJobs(page);
      }
    }
    if (sc === "busy") {
      // the loading skeleton, the empty state, server down, reload, the job not found, the error page
      await page.route("**/api/overview", async (r) => {
        await new Promise((res) => setTimeout(res, 2000));
        await r.continue().catch(() => undefined);
      });
      await page.goto(`${d.url}/`);
      await expect(page.getByTestId("loading")).toBeVisible();
      await page.unroute("**/api/overview");
      await page.route("**/api/overview", async (r) => {
        const resp = await r.fetch();
        await r.fulfill({ response: resp, json: { ...((await resp.json()) as object), jobs: [] } });
      });
      await page.goto(`${d.url}/`);
      await expect(page.getByText("No jobs yet")).toBeVisible();
      await page.unroute("**/api/overview");
      await page.goto(`${d.url}/`);
      await settled(page);
      await page.route("**/api/**", (r) => r.abort("connectionrefused"));
      await expect(page.getByTestId("server-down")).toBeVisible({ timeout: 10000 });
      await page.unroute("**/api/**");
      await page.route("**/api/overview", async (r) => {
        const resp = await r.fetch();
        await r.fulfill({ response: resp, json: { ...((await resp.json()) as object), api: 2 } });
      });
      await page.goto(`${d.url}/`);
      await expect(page.getByTestId("reload")).toBeVisible({ timeout: 10000 });
      await page.unroute("**/api/overview");
      await page.goto(`${d.url}/`);
      await settled(page);
      await gotoJob(page, "20991231-235959-zzzzzz");
      await expect(page.getByText("No node lists this job")).toBeVisible({ timeout: 15000 });
      await page.goto(`${d.url}/`);
      await settled(page);
      await page.evaluate(() => {
        Number.prototype.toFixed = function () {
          throw new Error("render failed");
        };
      });
      await expect(page.getByTestId("error-page")).toBeVisible({ timeout: 10000 });
      // the no-token page, in a context of its own (watched too)
      const ctx = await browser.newContext();
      await violations.watch(ctx);
      const p2 = await ctx.newPage();
      await p2.goto(`${d.url}/#job=20991231-235959-zzzzzz`);
      await expect(p2.getByTestId("no-token")).toBeVisible();
      await ctx.close();
    }
  }
});

// ------------------------------------------------------------------------------------------------ D42
test("D42 theme: set before the app's module runs; the charts redraw in the other theme's colours", async ({ page, serve }) => {
  const d = await serve("busy");
  // put a probe in front of the app's entry module: what is data-theme when the bundle starts?
  await page.route(/\/assets\/index-[^/]+\.js$/, async (r) => {
    const resp = await r.fetch();
    const body = await resp.text();
    await r.fulfill({ response: resp, body: `window.__themeAtModule = document.documentElement.getAttribute("data-theme");\n${body}` });
  });
  await page.emulateMedia({ colorScheme: "dark" });
  await openDash(page, d);
  expect(await page.evaluate(() => (window as unknown as { __themeAtModule?: string | null }).__themeAtModule), "D42: data-theme is set before the app's module").toBe("dark");
  await page.unroute(/\/assets\/index-[^/]+\.js$/);
  // the chart: pixels of the cores series in the dark theme's colour, then the light theme's after a switch
  const id = await page.locator('tr[data-state="running"]').first().getAttribute("data-job");
  await gotoJob(page, id);
  await expect(page.locator(".resource-pane canvas").first()).toBeVisible({ timeout: 15000 });
  await page.waitForTimeout(500);
  const count = (hex: string) =>
    page.locator(".resource-pane canvas").first().evaluate((c, h) => {
      const cv = c as HTMLCanvasElement;
      const ctx = cv.getContext("2d")!;
      const px = ctx.getImageData(0, 0, cv.width, cv.height).data;
      const [r, g, b] = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
      let n = 0;
      for (let i = 0; i < px.length; i += 4) if (Math.abs(px[i]! - r!) < 8 && Math.abs(px[i + 1]! - g!) < 8 && Math.abs(px[i + 2]! - b!) < 8 && px[i + 3]! > 200) n++;
      return n;
    }, hex);
  const DARK = "#fafafa"; // series-cores in dark
  const LIGHT = "#0a0a0a"; // series-cores in light
  await expect.poll(() => count(DARK)).toBeGreaterThan(30);
  expect(await count(LIGHT)).toBeLessThan(5);
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  await expect.poll(() => count(LIGHT), { timeout: 5000, message: "D42: the chart redraws in the light theme's colours" }).toBeGreaterThan(30);
  expect(await count(DARK), "D42: the chart redraws in the light theme's colours").toBeLessThan(5);
});

// ------------------------------------------------------------------------------------------------ D46
test("D46 failure states: the error page holds no job text; reload after an update; no JavaScript; server stopped is not 'node offline'", async ({ page, serve, browser, violations }) => {
  test.setTimeout(90000);
  const d = await serve("hostile");
  await openDash(page, d);
  await allJobs(page);
  await expect(page.locator("tr[data-job]").first()).toBeVisible();
  // an exception thrown while rendering, whose message is the hostile job's name: the error page shows none of it
  const hostileName = (await page.locator("tr[data-job] a").filter({ hasText: "onerror" }).first().textContent())!;
  const watching = await page.context().newPage(); // for the server-stop part below
  await page.evaluate((msg) => {
    Number.prototype.toFixed = function () {
      throw new Error(msg);
    };
  }, hostileName);
  await expect(page.getByTestId("error-page")).toBeVisible({ timeout: 10000 });
  const text = await page.locator("body").innerText();
  expect(text, "D46: the error page holds no job text").not.toContain("onerror");
  expect(text, "D46: the error page holds no job text").not.toContain(hostileName);
  await expect(page.locator("code", { hasText: /^mu status$/ })).toBeVisible();
  // a different release: reload (no lazy chunks in 1a: the API version is the signal, §7.1)
  const p2 = await page.context().newPage();
  await p2.route("**/api/overview", async (r) => {
    const resp = await r.fetch();
    await r.fulfill({ response: resp, json: { ...((await resp.json()) as object), api: 2 } });
  });
  await p2.goto(`${d.url}/`);
  await expect(p2.getByTestId("reload"), "D46: reload after an update").toBeVisible({ timeout: 10000 });
  await p2.close();
  // JavaScript off: the static browser-floor text, once
  const ctx = await browser.newContext({ javaScriptEnabled: false });
  await violations.watch(ctx);
  const p3 = await ctx.newPage();
  await p3.goto(`${d.url}/`);
  await expect(p3.getByText("μ dashboard needs JavaScript and Safari 16.4, Chrome 111 or Firefox 128 or newer.")).toHaveCount(1);
  await ctx.close();
  // the server really stops (the launcher's /stop): its own banner; the nodes keep their last state
  await openDash(watching, d);
  await expect(watching.locator("tr[data-job]").first()).toBeVisible();
  await stopServer(d);
  await expect(watching.getByTestId("server-down"), "D46: server down is not node offline").toBeVisible({ timeout: 15000 });
  await expect(watching.getByTestId("server-down")).toContainText("mu dashboard on");
  await expect(watching.locator('[data-machine-state="offline"]'), "D46: server down is not node offline").toHaveCount(0);
  await watching.close();
});

// ------------------------------------------------------------------------------------------------ D47
// Safari refuses the clipboard outside the click's own handling; Playwright's WebKit lets userActivation stay active
// for seconds after a click, so the test puts the strict rule back: writeText is allowed only while a click is being
// dispatched (a capture listener on window opens it, a bubble listener plus a 0 ms timer closes it).
async function strictClipboard(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const c = navigator.clipboard;
    const orig = c.writeText.bind(c);
    let inClick = false;
    window.addEventListener("click", () => { inClick = true; }, true);
    window.addEventListener("click", () => { setTimeout(() => { inClick = false; }, 0); }, false);
    c.writeText = (t: string) => (inClick ? orig(t) : Promise.reject(new DOMException("not in a gesture", "NotAllowedError")));
    // every state a copy button passes through ("copied" lasts 2 s: a slow poll could miss it)
    const states: string[] = ((window as unknown as { __copyStates: string[] }).__copyStates = []);
    new MutationObserver((ms) => {
      for (const m of ms) states.push((m.target as Element).getAttribute("data-copy-result") ?? "");
    }).observe(document, { subtree: true, attributes: true, attributeFilter: ["data-copy-result"] });
  });
}

test("D47 gesture: copy runs inside the click, and the exact command reaches the clipboard (Safari's engine)", async ({ page, serve, browserName }) => {
  test.skip(browserName !== "webkit", "WebKit only");
  test.setTimeout(90000);
  await strictClipboard(page);
  const d = await serve("busy");
  await openDash(page, d);
  const id = await page.locator('tr[data-state="running"]').first().getAttribute("data-job");
  await gotoJob(page, id);
  const btn = page.locator('button[data-copy-cmd^="mu log"]');
  await expect(btn).toBeVisible({ timeout: 15000 });
  await expect(btn).toHaveAccessibleName("copy log command");
  const cmd = (await btn.getAttribute("data-copy-cmd"))!;
  await btn.click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { __copyStates: string[] }).__copyStates), { timeout: 4000, message: "D47: copied inside the gesture" }).toContain("copied");
  // read it back: paste into a field, as the user would
  await page.evaluate(() => {
    const ta = document.createElement("textarea");
    ta.id = "paste-probe";
    ta.setAttribute("aria-label", "paste probe");
    document.body.appendChild(ta);
    ta.focus();
  });
  await page.keyboard.press("ControlOrMeta+V");
  await expect(page.locator("#paste-probe"), "D47: the exact command is pasted").toHaveValue(cmd);
});

test("D47 refused: a refused clipboard selects the command for ⌘C and says so (Safari's engine)", async ({ page, serve, browserName }) => {
  test.skip(browserName !== "webkit", "WebKit only");
  test.setTimeout(90000);
  await strictClipboard(page);
  const d = await serve("busy");
  await openDash(page, d);
  const id = await page.locator('tr[data-state="running"]').first().getAttribute("data-job");
  await gotoJob(page, id);
  await page.evaluate(() => {
    navigator.clipboard.writeText = () => Promise.reject(new DOMException("refused", "NotAllowedError"));
  });
  const fetchBtn = page.locator('button[data-copy-cmd^="mu fetch"]');
  await expect(fetchBtn).toBeVisible({ timeout: 15000 });
  const fetchCmd = (await fetchBtn.getAttribute("data-copy-cmd"))!;
  await fetchBtn.click();
  await expect(fetchBtn, "D47: a refused clipboard selects the command").toContainText("selected: press ⌘C");
  expect(await page.evaluate(() => window.getSelection()?.toString()), "D47: a refused clipboard selects the command").toBe(fetchCmd);
});
