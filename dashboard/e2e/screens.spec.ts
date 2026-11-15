// The owner's review copy (docs/design/dashboard.md §4.8, §5.3): every screen and state in light and dark, saved to
// test-results/screens/<state>-<theme>.png (uploaded by CI as the dashboard-e2e artifact). Not asserted beyond each
// screen being reached: the look is reviewed by a person. Chromium only (the e2e tests cover WebKit).
import type { Page } from "@playwright/test";
import { expect, gotoJob, openDash, setTheme, test, type Dash } from "./helpers";

test.skip(({ browserName }) => browserName !== "chromium", "screenshots once, in Chromium");
test.describe.configure({ mode: "serial" });

const THEMES = ["light", "dark"] as const;

async function shot(page: Page, name: string, full = true): Promise<void> {
  for (const t of THEMES) {
    await setTheme(page, t);
    await page.mouse.move(0, 0);
    await page.waitForTimeout(150);
    await page.screenshot({ path: `test-results/screens/${name}-${t}.png`, fullPage: full });
  }
}

async function firstJob(page: Page, state: string): Promise<string> {
  const row = page.locator(`tr[data-state="${state}"]`).first();
  await expect(row).toBeVisible({ timeout: 30000 });
  return (await row.getAttribute("data-job"))!;
}

async function overview(page: Page, d: Dash, name: string, ready?: (p: Page) => Promise<void>) {
  await openDash(page, d);
  await page.getByTestId("filter-from").getByRole("button", { name: "all workstations" }).click();
  if (ready) await ready(page);
  else await page.waitForTimeout(3500);
  await shot(page, name);
}

test.use({ viewport: { width: 1440, height: 900 } });

test("screens: overview in every scenario", async ({ page, serve }) => {
  test.setTimeout(240000);
  for (const sc of ["busy", "idle", "offline", "refused", "old-node", "r1-node", "paused", "hostile", "many"]) {
    const d = await serve(sc);
    await overview(page, d, `overview-${sc}`);
  }
});

test("screens: scheduler release 2's states (paused, resuming, held, borrower)", async ({ page, serve }) => {
  test.setTimeout(240000);
  const d = await serve("paused");
  await openDash(page, d);
  await page.getByTestId("filter-from").getByRole("button", { name: "all workstations" }).click();
  await page.waitForTimeout(3000);
  const pick = async (sel: string) => (await page.locator(sel).first().getAttribute("data-job"))!;
  const ids: [string, string][] = [
    ["paused", await page.locator('tr[data-job]:has([data-state="paused"])').first().getAttribute("data-job") as string],
    ["resuming", await page.locator('tr[data-job]:has([data-state="resuming"])').first().getAttribute("data-job") as string],
    ["held", await pick('tr[data-state="held"]')],
  ];
  for (const [name, id] of ids) {
    await gotoJob(page, id);
    await expect(page.getByTestId("job-detail")).toBeVisible();
    await page.waitForTimeout(3000);
    await shot(page, `job-${name}`);
    await page.goto(`${d.url}/`);
    await page.getByTestId("filter-from").getByRole("button", { name: "all workstations" }).click();
  }
});

test("screens: stale and reconnecting", async ({ page, serve }) => {
  test.setTimeout(180000);
  const s = await serve("stale");
  await openDash(page, s);
  await expect(page.locator('[data-age="stale"]').first()).toBeVisible({ timeout: 60000 });
  await page.waitForTimeout(2000);
  await shot(page, "overview-stale");
  const w = await serve("wake");
  await openDash(page, w);
  await expect(page.locator('[data-machine-state="reconnecting"]').first()).toBeVisible({ timeout: 60000 }).catch(() => undefined);
  await shot(page, "overview-wake");
});

test("screens: job details", async ({ page, serve }) => {
  test.setTimeout(240000);
  const d = await serve("busy");
  await openDash(page, d);
  for (const st of ["running", "queued", "oom", "succeeded", "failed"]) {
    const id = await firstJob(page, st);
    await gotoJob(page, id);
    await expect(page.getByTestId("job-detail")).toBeVisible();
    await page.waitForTimeout(st === "running" ? 4000 : 2500);
    await shot(page, `job-${st}`);
    if (st === "running") {
      await page.getByRole("button", { name: "show as table" }).click();
      await shot(page, "job-running-table");
    }
    await page.goto(`${d.url}/`);
    await expect(page.getByTestId("machines")).toBeVisible();
  }
  const o = await serve("old-node");
  await openDash(page, o);
  const id = await firstJob(page, "succeeded");
  await gotoJob(page, id);
  await expect(page.getByTestId("resources-old-node")).toBeVisible();
  await page.waitForTimeout(2500);
  await shot(page, "job-old-node");
  const h = await serve("hostile");
  await openDash(page, h);
  const hid = await firstJob(page, "running");
  await gotoJob(page, hid);
  await page.waitForTimeout(4000);
  await shot(page, "job-hostile");
  await page.goto(`${h.url}/#job=20991231-235959-zzzzzz`);
  await expect(page.getByText("No node lists this job")).toBeVisible({ timeout: 15000 });
  await shot(page, "job-not-found");
});

test("screens: page states", async ({ page, serve, browser, violations }) => {
  test.setTimeout(180000);
  const d = await serve("busy");
  // loading: the overview held back
  await page.route("**/api/overview", async (r) => {
    await new Promise((res) => setTimeout(res, 4000));
    await r.continue().catch(() => undefined);
  });
  await page.goto(`${d.url}/#t=${d.token}`);
  await page.waitForTimeout(800);
  await shot(page, "loading", false);
  await page.unroute("**/api/overview");
  // empty: no jobs anywhere
  await page.route("**/api/overview", async (r) => {
    const resp = await r.fetch();
    const body = (await resp.json()) as Record<string, unknown>;
    await r.fulfill({ response: resp, json: { ...body, jobs: [] } });
  });
  await page.goto(`${d.url}/`);
  await expect(page.getByText("No jobs yet")).toBeVisible();
  await shot(page, "empty");
  await page.unroute("**/api/overview");
  // the server not answering
  await page.goto(`${d.url}/`);
  await expect(page.getByTestId("machines")).toBeVisible();
  await page.waitForTimeout(2500);
  await page.route("**/api/**", (r) => r.abort("connectionrefused"));
  await expect(page.getByTestId("server-down")).toBeVisible({ timeout: 10000 });
  await page.waitForTimeout(16000);
  await shot(page, "server-down");
  await page.unroute("**/api/**");
  // reload: a different release
  await page.route("**/api/overview", async (r) => {
    const resp = await r.fetch();
    const body = (await resp.json()) as Record<string, unknown>;
    await r.fulfill({ response: resp, json: { ...body, api: 2 } });
  });
  await page.goto(`${d.url}/`);
  await expect(page.getByTestId("reload")).toBeVisible();
  await shot(page, "reload", false);
  await page.unroute("**/api/overview");
  // the error page
  await page.goto(`${d.url}/`);
  await expect(page.getByTestId("machines")).toBeVisible();
  await page.evaluate(() => {
    Number.prototype.toFixed = function () {
      throw new Error("render failed");
    };
  });
  await expect(page.getByTestId("error-page")).toBeVisible({ timeout: 10000 });
  await shot(page, "error", false);
  // no token
  const fresh = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await violations.watch(fresh);
  const p2 = await fresh.newPage();
  await p2.goto(`${d.url}/`);
  await expect(p2.getByTestId("no-token")).toBeVisible();
  await shot(p2, "no-token", false);
  await fresh.close();
  // no JavaScript
  const nojs = await browser.newContext({ javaScriptEnabled: false, viewport: { width: 1440, height: 900 } });
  await violations.watch(nojs);
  const p3 = await nojs.newPage();
  await p3.goto(`${d.url}/`);
  await p3.screenshot({ path: "test-results/screens/no-javascript-light.png" }); // no script: no theme either
  await nojs.close();
});

test("screens: phone width", async ({ page, serve }) => {
  test.setTimeout(120000);
  await page.setViewportSize({ width: 390, height: 844 });
  const d = await serve("busy");
  await overview(page, d, "phone-overview");
  const id = await firstJob(page, "running");
  await gotoJob(page, id);
  await page.waitForTimeout(3500);
  await shot(page, "phone-job");
});
