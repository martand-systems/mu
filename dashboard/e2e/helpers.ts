// Shared e2e helpers. Each test asks the launcher (tools/test-dashboard/e2e-launcher.py, started outside the
// sandbox) for a fresh `mu dashboard serve --dev` with the scenario it needs, so a scenario's timeline starts with
// the test, and a server fault (MU_JOBS_FAULT) can be put in for the must-fail runs.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test as base, expect, type Page } from "@playwright/test";

export type Dash = { id: number; port: number; token: string; url: string };

function launcher(): { port: number; token: string } {
  const run = process.env.DASH_DEV_RUN;
  if (!run) throw new Error("DASH_DEV_RUN is not set (the folder that holds e2e/launcher.json)");
  return JSON.parse(readFileSync(join(run, "e2e", "launcher.json"), "utf8")) as { port: number; token: string };
}

export async function ask(path: string): Promise<Record<string, unknown>> {
  const l = launcher();
  const r = await fetch(`http://127.0.0.1:${l.port}${path}`, { headers: { "X-Launcher-Token": l.token } });
  const body = (await r.json()) as Record<string, unknown>;
  if (!r.ok) throw new Error(`launcher ${path}: ${JSON.stringify(body)}`);
  return body;
}

export async function startServer(scenario: string, faults: string[] = []): Promise<Dash> {
  const b = await ask(`/start?scenario=${scenario}${faults.length ? `&faults=${faults.join(",")}` : ""}`);
  const port = b.port as number;
  return { id: b.id as number, port, token: b.token as string, url: `http://127.0.0.1:${port}` };
}

export async function restartServer(d: Dash): Promise<Dash> {
  const b = await ask(`/restart?id=${d.id}`);
  return { ...d, token: b.token as string };
}

export async function stopServer(d: Dash): Promise<void> {
  await ask(`/stop?id=${d.id}`);
}

// D29: every page registers a listener for CSP and Trusted Types violations before any script runs, and every test
// fails on any violation (checked after the test by the fixture below).
export async function watchViolations(page: Page): Promise<() => Promise<string[]>> {
  await page.addInitScript(() => {
    const w = window as unknown as { __violations: string[] };
    w.__violations = [];
    document.addEventListener("securitypolicyviolation", (e) => {
      w.__violations.push(`${e.violatedDirective} ${e.blockedURI} ${e.sample}`);
    });
  });
  return () => page.evaluate(() => (window as unknown as { __violations?: string[] }).__violations ?? []);
}

type Fixtures = {
  serve: (scenario: string, faults?: string[]) => Promise<Dash>;
  violations: () => Promise<string[]>;
};

export const test = base.extend<Fixtures>({
  violations: [
    async ({ page }, run) => {
      const get = await watchViolations(page);
      await run(get);
      const v = page.isClosed() ? [] : await get().catch(() => [] as string[]);
      expect(v, "CSP or Trusted Types violations (D29)").toEqual([]);
    },
    { auto: true },
  ],
  serve: async ({}, run) => {
    const started: Dash[] = [];
    await run(async (scenario, faults = []) => {
      const d = await startServer(scenario, faults);
      started.push(d);
      return d;
    });
    for (const d of started) await stopServer(d).catch(() => undefined);
  },
});
export { expect };

/** Open the dashboard as `mu dashboard open` does: the token in the fragment. */
export async function openDash(page: Page, d: Dash, hash = ""): Promise<void> {
  await page.goto(`${d.url}/#t=${d.token}${hash ? "&" + hash : ""}`);
  await expect(page.getByTestId("machines").or(page.getByTestId("job-detail")).or(page.getByTestId("no-token"))).toBeVisible({ timeout: 15000 });
}

export async function setTheme(page: Page, theme: "light" | "dark"): Promise<void> {
  await page.emulateMedia({ colorScheme: theme });
  await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
}

export async function apiJson(page: Page, d: Dash, path: string): Promise<Record<string, unknown>> {
  const r = await page.request.get(`${d.url}${path}`, { headers: { "X-Mu-Token": d.token } });
  return (await r.json()) as Record<string, unknown>;
}
