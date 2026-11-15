// Shared e2e helpers. Each test asks the launcher (tools/test-dashboard/e2e-launcher.py, started outside the
// sandbox) for a fresh `mu dashboard serve --dev` with the scenario it needs, so a scenario's timeline starts with
// the test, and a server fault (MU_JOBS_FAULT) can be put in for the must-fail runs.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { test as base, expect, type BrowserContext, type Page } from "@playwright/test";

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

// D29: every page of every context a test uses reports CSP and Trusted Types violations to the test runner (a binding
// on the context, so the record survives navigations and covers new pages); the fixture fails the test on any.
export type Violations = {
  list: string[];
  /** Watch a context the test made itself (browser.newContext). */
  watch: (ctx: BrowserContext) => Promise<void>;
  /** Forget what was recorded so far (a test that sets off a violation on purpose). */
  clear: () => Promise<void>;
};

async function watchContext(ctx: BrowserContext, sink: string[]): Promise<void> {
  await ctx.exposeBinding("__muViolation", (_src, v: string) => {
    sink.push(v);
  });
  await ctx.addInitScript(() => {
    document.addEventListener("securitypolicyviolation", (e) => {
      const f = (window as unknown as { __muViolation?: (v: string) => void }).__muViolation;
      f?.(`${e.violatedDirective} ${e.blockedURI} ${e.sample} (${document.location.pathname}${document.location.hash.replace(/t=[0-9a-f]+/, "t=…")})`);
    });
  });
}

const settle = () => new Promise((r) => setTimeout(r, 300));

type Fixtures = {
  serve: (scenario: string, faults?: string[]) => Promise<Dash>;
  violations: Violations;
};

export const test = base.extend<Fixtures>({
  violations: [
    async ({ context }, run) => {
      const list: string[] = [];
      const seen = new Set<BrowserContext>();
      const watch = async (ctx: BrowserContext) => {
        if (seen.has(ctx)) return;
        seen.add(ctx);
        await watchContext(ctx, list);
      };
      await watch(context);
      await run({ list, watch, clear: async () => { await settle(); list.length = 0; } });
      await settle();
      expect(list, "CSP or Trusted Types violations (D29)").toEqual([]);
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

/** Open a job in the page (a fragment change, as a click on its link does; a page.goto to another fragment of the
 * same document is cancelled now and then by WebKit). */
export async function gotoJob(page: Page, id: string | null): Promise<void> {
  await page.evaluate((j) => {
    window.location.hash = `job=${j}`;
  }, id ?? "");
  await expect(page.getByTestId("job-detail").or(page.getByText("No node lists this job"))).toBeVisible({ timeout: 15000 });
}
