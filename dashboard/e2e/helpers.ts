import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Page } from "@playwright/test";

export function devToken(): string {
  const run = process.env.DASH_DEV_RUN;
  if (!run) throw new Error("DASH_DEV_RUN is not set (the dev server's run/dev folder)");
  return readFileSync(join(run, "dashboard.token"), "utf8").trim();
}

// D29: listen for CSP and Trusted Types violations before any page script runs.
export async function watchViolations(page: Page): Promise<() => Promise<string[]>> {
  await page.addInitScript(() => {
    const w = window as unknown as { __violations: string[] };
    w.__violations = [];
    document.addEventListener("securitypolicyviolation", (e) => {
      w.__violations.push(`${e.violatedDirective} ${e.blockedURI} ${e.sample}`);
    });
  });
  return () => page.evaluate(() => (window as unknown as { __violations: string[] }).__violations);
}
