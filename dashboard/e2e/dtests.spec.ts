// The page's acceptance tests of phase 1a (docs/design/dashboard.md §7.1), each against a fresh
// `mu dashboard serve --dev` with fake nodes. Every test has a fault that must make it fail (the must-fail runs in
// .github/workflows/dashboard.yml and tools/test-dashboard/e2e-faults.sh): a server fault (MU_JOBS_FAULT, put in by
// the launcher) or a page fault (a build with MU_DASH_FAULT). Chromium runs all; WebKit runs D05-D07, D16, D29, D42
// and D47 (Safari is the Mac's default browser). Every test also fails on any CSP or Trusted Types violation (D29,
// the auto fixture in helpers.ts).
import AxeBuilder from "@axe-core/playwright";
import type { Page } from "@playwright/test";
import { apiJson, expect, openDash, restartServer, setTheme, test, type Dash } from "./helpers";

const WEBKIT_TOO = /^(D05|D06|D07|D16|D29|D42|D47)\b/;
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

const GB = (s: string | null) => {
  const m = /([\d.]+)\s*(GB|MB|kB)/.exec(s ?? "");
  return m ? parseFloat(m[1]!) * (m[2] === "GB" ? 1e9 : m[2] === "MB" ? 1e6 : 1e3) : NaN;
};

// ------------------------------------------------------------------------------------------------ D04, D39
test("D04 an old node (no features): the jobs are listed and the card says to update μ", async ({ page, serve }) => {
  const d = await serve("old-node");
  await openDash(page, d);
  const card = page.getByTestId("card-node-office-pc");
  await expect(card.getByText("live statistics after the next node update")).toBeVisible();
  await expect(card.getByText("update μ on office-pc for live statistics")).toBeVisible();
  await expect(page.locator("tr[data-job]").first()).toBeVisible();
  expect((await rows(page)).every((r) => r.node === "office-pc")).toBe(true);
  // the job detail of an old node: no resource chart, elapsed only
  const id = (await rows(page))[0]!.job;
  await page.goto(`${d.url}/#job=${id}`);
  await expect(page.getByTestId("resources-old-node")).toBeVisible();
  await expect(page.getByTestId("resource-chart")).toHaveCount(0);
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
  await expect(r).toHaveAttribute("data-state", "running", { timeout: 20000 + 20000 - (Date.now() - t0) });
  await expect(r).toHaveAttribute("data-state", "succeeded", { timeout: 60000 + 20000 - (Date.now() - t0) });
});

// ------------------------------------------------------------------------------------------------ D05
test("D05 jobs by node: each job under its own node, following queued -> running -> succeeded", async ({ page, serve }) => {
  test.setTimeout(120000);
  const d = await serve("busy");
  const t0 = Date.now();
  await openDash(page, d);
  await allJobs(page);
  // both nodes' lists have arrived
  await expect.poll(async () => new Set((await rows(page)).map((r) => r.node)).size, { timeout: 15000 }).toBe(2);
  const rs = await rows(page);
  // the scenario: lab-pc holds cfd-cavity (queued at the start) and aero-vlm-wing; everything else is office-pc's
  const lab = rs.filter((r) => r.node === "lab-pc").map((r) => r.name).sort();
  expect(lab).toEqual(["aero-vlm-wing", "cfd-cavity"]);
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
test("D06 figures are right: the node card and the job rows show the node's numbers", async ({ page, serve }) => {
  const d = await serve("busy");
  await openDash(page, d);
  await allJobs(page);
  const card = page.getByTestId("card-node-office-pc");
  await expect(card.locator('[data-fig="node-avail"]')).toBeVisible();
  type N = { stats: { cpu_pct: number; mem: { total: number; avail: number; used: number } } };
  type J = { jobs: { job_id: string; node: string; state: string; live: { anon: number; shmem: number; cpu_cores: number } | null }[] };
  // what the card shows is what /api/nodes/office-pc says (polled together: the figures move every 2 s)
  await expect
    .poll(async () => {
      const n = (await apiJson(page, d, "/api/nodes/office-pc")) as unknown as N;
      const avail = await card.locator('[data-fig="node-avail"]').textContent();
      const mem = await card.locator('[data-fig="node-mem"]').textContent();
      const want = `${(n.stats.mem.avail / 1e9).toFixed(1)} GB`;
      const used = `${(n.stats.mem.used / 1e9).toFixed(1)} /`;
      return avail === want && (mem ?? "").includes(used);
    }, { timeout: 15000 })
    .toBe(true);
  // and those numbers are the node's: used memory holds at least the running jobs' working memory
  const n = (await apiJson(page, d, "/api/nodes/office-pc")) as unknown as N;
  const jobs = ((await apiJson(page, d, "/api/jobs?from=all")) as unknown as J).jobs.filter((j) => j.node === "office-pc" && j.live);
  const work = jobs.reduce((s, j) => s + j.live!.anon + j.live!.shmem, 0);
  expect(jobs.length).toBe(2);
  expect(n.stats.mem.used).toBeGreaterThanOrEqual(work);
  expect(n.stats.mem.used + n.stats.mem.avail).toBeLessThanOrEqual(n.stats.mem.total * 1.001);
  const cardUsed = GB(await card.locator('[data-fig="node-mem"]').textContent());
  expect(cardUsed).toBeGreaterThanOrEqual(work * 0.98);
  // each running job's row: working memory and cores as the node says
  for (const j of jobs) {
    const row = page.locator(`tr[data-job="${j.job_id}"]`);
    await expect
      .poll(async () => {
        const fresh = ((await apiJson(page, d, "/api/jobs?from=all")) as unknown as J).jobs.find((x) => x.job_id === j.job_id)!;
        const mem = await row.locator('td[data-col="mem"]').textContent();
        const cores = await row.locator('td[data-col="cores"]').textContent();
        return mem === `${((fresh.live!.anon + fresh.live!.shmem) / 1e9).toFixed(1)} GB` && (cores ?? "").startsWith(fresh.live!.cpu_cores.toFixed(1));
      }, { timeout: 15000 })
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
  // the link is cut at +20 s; within 15 s more (and a poll) every value is stale
  await expect(card.locator('[data-age="stale"]')).toBeVisible({ timeout: 20000 + 15000 + 10000 - (Date.now() - t0) });
  await expect(card.locator('[data-age="live"]')).toHaveCount(0);
  await expect(card.getByText(/stale · as of \d\d:\d\d:\d\d/)).toBeVisible();
  await expect(card.locator('[data-fig="cpu"]')).toHaveClass(/text-stale/);
  const n = (await apiJson(page, d, "/api/nodes/office-pc")) as { stats: { stale: boolean } | null; state: string };
  expect(n.stats === null || n.stats.stale || n.state !== "online").toBe(true);
  // the running job's live figures turn stale too
  const running = page.locator('tr[data-state="running"]').first();
  if (await running.count()) await expect(running.locator('td[data-col="cores"]')).toContainText("stale");
});

// ------------------------------------------------------------------------------------------------ D14, D15
test("D14 a token from an earlier server start shows the no-token page, with no data", async ({ page, serve }) => {
  let d = await serve("busy");
  await openDash(page, d);
  await expect(page.locator("tr[data-job]").first()).toBeVisible();
  const names = (await rows(page)).map((r) => r.name);
  d = await restartServer(d);
  await page.goto(`${d.url}/`);
  await expect(page.getByTestId("no-token")).toBeVisible({ timeout: 15000 });
  await expect(page.getByText("mu dashboard open").first()).toBeVisible();
  const text = await page.locator("body").innerText();
  for (const n of names) expect(text).not.toContain(n);
  expect(await page.locator("tr[data-job]").count()).toBe(0);
});

test("D15 the token leaves the address bar and the history", async ({ page, serve }) => {
  const d: Dash = await serve("idle");
  await page.goto(`${d.url}/#t=${d.token}&job=20261114-101500-k3f9qa`);
  await expect(page.getByTestId("job-detail").or(page.getByText("No node lists this job"))).toBeVisible();
  expect(page.url()).not.toContain(d.token);
  expect(page.url()).toContain("#job=20261114-101500-k3f9qa");
  expect(await page.evaluate(() => window.location.href)).not.toContain("t=");
});

// ------------------------------------------------------------------------------------------------ D16
test("D16 hostile node text: nothing is created from it, nothing runs, the page stays responsive", async ({ page, serve }) => {
  test.setTimeout(90000);
  const d = await serve("hostile");
  const resp = await page.goto(`${d.url}/#t=${d.token}`);
  expect(resp?.headers()["content-security-policy"]).toContain("require-trusted-types-for 'script'");
  await expect(page.getByTestId("machines")).toBeVisible();
  await allJobs(page);
  const hostile = page.locator("tr[data-job]").filter({ hasText: "onerror" }).first();
  await expect(hostile).toBeVisible();
  await expect(hostile.getByText('<img src=x onerror="window.__xss=1">', { exact: false })).toBeVisible();
  // the hostile job's detail: its log holds HTML, ANSI escapes and a 1 MB line
  const id = (await hostile.getAttribute("data-job"))!;
  await page.goto(`${d.url}/#job=${id}`);
  await expect(page.getByTestId("log-text")).toContainText("<script>", { timeout: 20000 });
  await expect(page.getByTestId("log-text")).toContainText("bytes cut", { timeout: 30000 });
  const t = Date.now();
  const r = await page.evaluate(() => ({
    xss: (window as unknown as { __xss?: unknown }).__xss ?? null,
    imgs: [...document.querySelectorAll("img")].length,
    scripts: [...document.querySelectorAll("script")].filter((s) => !s.src).length,
  }));
  expect(Date.now() - t).toBeLessThan(2000);
  expect(r).toEqual({ xss: null, imgs: 0, scripts: 0 });
  await page.getByRole("button", { name: "wrap" }).click(); // still answers clicks
  await expect(page.getByRole("button", { name: "wrap" })).toHaveAttribute("aria-pressed", "true");
});

// ------------------------------------------------------------------------------------------------ D18
test("D18 offline is grey with 'offline since' and a retry countdown; refused is red, never retried, with a retry button", async ({ page, serve }) => {
  const off = await serve("offline");
  await openDash(page, off);
  const oc = page.getByTestId("card-node-office-pc");
  await expect(oc.locator('[data-machine-state="offline"]')).toBeVisible({ timeout: 15000 });
  await expect(oc.getByText(/offline since \d\d:\d\d:\d\d/)).toBeVisible();
  await expect(oc.getByText(/next try in/)).toBeVisible();
  await expect(page.getByTestId("card-node-lab-pc").locator('[data-machine-state="online"]')).toBeVisible();
  const ref = await serve("refused");
  await openDash(page, ref);
  const rc = page.getByTestId("card-node-office-pc");
  await expect(rc.locator('[data-machine-state="refused"]')).toBeVisible({ timeout: 15000 });
  await expect(rc.locator('[data-machine-state="refused"]')).toHaveClass(/text-failed/);
  await expect(rc.getByText(/host key differs/)).toBeVisible();
  await expect(rc.getByText("never retried automatically")).toBeVisible();
  await rc.getByRole("button", { name: /retry now/ }).click();
  await expect(rc.getByRole("button", { name: /asked once more/ })).toBeVisible();
});

// ------------------------------------------------------------------------------------------------ D26
async function axe(page: Page, where: string): Promise<void> {
  const r = await new AxeBuilder({ page: page as never }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${where}: ${v.id} (${v.impact}) ${v.nodes.slice(0, 3).map((n) => n.target.join(" ")).join(" | ")}`)).toEqual([]);
}

async function tabThrough(page: Page): Promise<void> {
  // every visible control is reachable with Tab and has an accessible name
  const controls = await page.locator("a[href], button, select, [tabindex='0']").evaluateAll((els) =>
    els.filter((e) => (e as HTMLElement).offsetParent !== null).map((e) => e.outerHTML.slice(0, 80)));
  await page.locator("body").focus();
  const seen = new Set<string>();
  for (let i = 0; i < controls.length + 10; i++) {
    await page.keyboard.press("Tab");
    const f = await page.evaluate(() => {
      const a = document.activeElement as HTMLElement | null;
      if (!a || a === document.body) return null;
      const name = a.getAttribute("aria-label") || a.textContent?.trim() || (a as HTMLSelectElement).labels?.[0]?.textContent?.trim() || a.getAttribute("title") || "";
      return { html: a.outerHTML.slice(0, 80), name };
    });
    if (f) {
      expect(f.name, `accessible name of ${f.html}`).not.toBe("");
      seen.add(f.html);
    }
  }
  for (const c of controls) expect(seen.has(c), `reachable with Tab: ${c}`).toBe(true);
  // a clickable element that is not a control is a fault (div-button): every row's name is a link
  const names = await page.locator("tr[data-job] td:nth-child(3)").evaluateAll((tds) => tds.map((td) => td.querySelector("a[href]") !== null));
  expect(names.every(Boolean)).toBe(true);
}

async function noLiveChurn(page: Page): Promise<void> {
  const live = () => page.locator("[aria-live], [role=status], [role=alert], [role=log], [role=timer]").evaluateAll((els) => els.map((e) => e.textContent));
  const a = await live();
  await page.waitForTimeout(4500);
  expect(await live()).toEqual(a);
}

test("D26 accessibility: keyboard, names, labels, no live churn, axe clean in light and dark", async ({ page, serve }) => {
  test.setTimeout(240000);
  for (const sc of ["busy", "offline", "refused", "old-node", "hostile"]) {
    const d = await serve(sc);
    await openDash(page, d);
    await allJobs(page);
    await page.waitForTimeout(2500);
    for (const theme of ["light", "dark"] as const) {
      await setTheme(page, theme);
      await axe(page, `${sc} ${theme}`);
    }
    if (sc === "busy") {
      await tabThrough(page);
      await noLiveChurn(page);
      // every state colour has its word: each badge's text is not empty
      const words = await page.locator("[data-state] span, [data-machine-state] span").evaluateAll((s) => s.map((x) => x.textContent?.trim()));
      expect(words.every((w) => w)).toBe(true);
      const id = await page.locator('tr[data-state="running"]').first().getAttribute("data-job");
      await page.goto(`${d.url}/#job=${id}`);
      await expect(page.getByTestId("resource-chart")).toBeVisible({ timeout: 15000 });
      for (const theme of ["light", "dark"] as const) {
        await setTheme(page, theme);
        await axe(page, `job detail ${theme}`);
      }
      await tabThrough(page);
      await page.goto(`${d.url}/`);
    }
  }
});

// ------------------------------------------------------------------------------------------------ D29
test("D29 no CSP or Trusted Types violation with every overlay opened", async ({ page, serve }) => {
  test.setTimeout(120000);
  const d = await serve("busy");
  const resp = await page.goto(`${d.url}/#t=${d.token}`);
  const csp = resp?.headers()["content-security-policy"] ?? "";
  for (const bad of ["unsafe-inline", "unsafe-eval", "'nonce-", "'sha256-", "'sha384-", "'sha512-"]) expect(csp).not.toContain(bad);
  expect(csp).toContain("require-trusted-types-for 'script'");
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
  await page.getByRole("button", { name: /Copy: mu status/ }).click();
  await page.waitForTimeout(500);
  // the no-token page and the error page mount without a violation too (the fixture checks after the test)
  await page.getByRole("button", { name: "Jobs" }).click();
  await expect(page.getByTestId("machines")).toBeVisible();
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
  expect(await page.evaluate(() => (window as unknown as { __themeAtModule?: string | null }).__themeAtModule)).toBe("dark");
  await page.unroute(/\/assets\/index-[^/]+\.js$/);
  // the chart: pixels of the cores series in the dark theme's colour, then the light theme's after a switch
  const id = await page.locator('tr[data-state="running"]').first().getAttribute("data-job");
  await page.goto(`${d.url}/#job=${id}`);
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
  await expect.poll(() => count(LIGHT), { timeout: 5000 }).toBeGreaterThan(30);
  expect(await count(DARK)).toBeLessThan(5);
});

// ------------------------------------------------------------------------------------------------ D46
test("D46 failure states: server down is not 'node offline'; the error page holds no job text; no JavaScript; reload", async ({ page, serve, browser }) => {
  test.setTimeout(90000);
  const d = await serve("hostile");
  await openDash(page, d);
  await allJobs(page);
  await expect(page.locator("tr[data-job]").first()).toBeVisible();
  // the server stops answering (connection refused): its own banner; the nodes keep their last state
  await page.route("**/api/**", (r) => r.abort("connectionrefused"));
  await expect(page.getByTestId("server-down")).toBeVisible({ timeout: 10000 });
  await expect(page.getByTestId("server-down")).toContainText("mu dashboard on");
  await expect(page.locator('[data-machine-state="offline"]')).toHaveCount(0);
  await page.unroute("**/api/**");
  await expect(page.getByTestId("server-down")).toHaveCount(0, { timeout: 10000 });
  // an exception thrown while rendering, whose message is the hostile job's name: the error page shows none of it
  const hostileName = (await page.locator("tr[data-job] a").filter({ hasText: "onerror" }).first().textContent())!;
  await page.evaluate((msg) => {
    Number.prototype.toFixed = function () {
      throw new Error(msg);
    };
  }, hostileName);
  await expect(page.getByTestId("error-page")).toBeVisible({ timeout: 10000 });
  const text = await page.locator("body").innerText();
  expect(text).not.toContain("onerror");
  expect(text).not.toContain(hostileName);
  await expect(page.getByText("mu status", { exact: true })).toBeVisible();
  // a different release: reload
  const p2 = await page.context().newPage();
  await p2.route("**/api/overview", async (r) => {
    const resp = await r.fetch();
    await r.fulfill({ response: resp, json: { ...((await resp.json()) as object), api: 2 } });
  });
  await p2.goto(`${d.url}/`);
  await expect(p2.getByTestId("reload")).toBeVisible({ timeout: 10000 });
  await p2.close();
  // JavaScript off: the static browser-floor text
  const ctx = await browser.newContext({ javaScriptEnabled: false });
  const p3 = await ctx.newPage();
  await p3.goto(`${d.url}/`);
  await expect(p3.getByText("μ dashboard needs JavaScript and Safari 16.4, Chrome 111 or Firefox 128 or newer.").first()).toBeVisible();
  await ctx.close();
});

// ------------------------------------------------------------------------------------------------ D47
test("D47 copy works in Safari's engine: the exact command reaches the clipboard, or is selected with a word", async ({ page, serve, browserName, context }) => {
  test.skip(browserName !== "webkit", "WebKit only");
  void context;
  const d = await serve("busy");
  await openDash(page, d);
  const id = await page.locator('tr[data-state="running"]').first().getAttribute("data-job");
  await page.goto(`${d.url}/#job=${id}`);
  const btn = page.getByRole("button", { name: /^Copy: mu log/ });
  await expect(btn).toBeVisible({ timeout: 15000 });
  const cmd = (await btn.getAttribute("aria-label"))!.replace(/^Copy: /, "");
  await btn.click();
  await expect(btn).toHaveAttribute("data-copy-result", /copied|select/, { timeout: 4000 });
  const res = await btn.getAttribute("data-copy-result");
  if (res === "copied") {
    // read it back: paste into a field (the clipboard as the user would use it)
    await page.evaluate(() => {
      const ta = document.createElement("textarea");
      ta.id = "paste-probe";
      document.body.appendChild(ta);
      ta.focus();
    });
    await page.keyboard.press("Meta+V");
    const pasted = await page.locator("#paste-probe").inputValue();
    test.info().annotations.push({ type: "clipboard", description: `copied; pasted back: ${JSON.stringify(pasted)}` });
    expect(pasted).toBe(cmd);
  } else {
    expect(await page.evaluate(() => window.getSelection()?.toString())).toBe(cmd);
    await expect(btn).toContainText("selected: press ⌘C");
  }
});
