// Phase 1a part 1: the pipeline's smoke test. The served page has the CSP (no unsafe-inline, Trusted Types required),
// loads with no violation, enforces Trusted Types, takes the token out of the address bar (D15), and the theme
// attribute is set by theme.js in <head> (D42 proper, before the module runs, comes with the page). The screen tests (D04-D07, D16, D26, D29 with
// every overlay, D42, D46, D47) come with the page.
import { expect, test } from "@playwright/test";
import { devToken, watchViolations } from "./helpers";

test("the page loads under the CSP with no violation and drops the token from the address", async ({ page }) => {
  const violations = await watchViolations(page);
  const resp = await page.goto("/#t=" + devToken());
  const csp = resp?.headers()["content-security-policy"] ?? "";
  expect(csp).toContain("require-trusted-types-for 'script'");
  expect(csp).not.toContain("unsafe-inline");
  expect(csp).not.toContain("unsafe-eval");
  await expect(page.getByTestId("state")).toContainText("Connected to μ");
  expect(page.url()).not.toContain("#t=");
  expect(await page.evaluate(() => window.history.length >= 1 && !window.location.href.includes("t="))).toBe(true);
  expect(await violations()).toEqual([]);
});

test("Trusted Types are enforced: an HTML sink throws", async ({ page, browserName }) => {
  await page.goto("/#t=" + devToken());
  const r = await page.evaluate(() => {
    const hasTT = typeof (window as unknown as { trustedTypes?: unknown }).trustedTypes !== "undefined";
    try {
      const d = document.createElement("div");
      d.innerHTML = "<img src=x>";
      return { hasTT, threw: false };
    } catch {
      return { hasTT, threw: true };
    }
  });
  if (!r.hasTT) {
    test.info().annotations.push({ type: "note", description: `${browserName}: this build has no Trusted Types; the CSP is checked, TT is not` });
    return;
  }
  expect(r.threw).toBe(true);
});

test("without a token the page holds no data and says what to run", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("state")).toContainText("mu dashboard open");
});

for (const scheme of ["light", "dark"] as const) {
  test(`theme: data-theme is ${scheme} by DOMContentLoaded (set by theme.js in <head>); screenshot`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme });
    await page.addInitScript(() => {
      document.addEventListener("DOMContentLoaded", () => {
        (window as unknown as { __themeAtDom: string | null }).__themeAtDom = document.documentElement.getAttribute("data-theme");
      });
    });
    await page.goto("/#t=" + devToken());
    expect(await page.evaluate(() => (window as unknown as { __themeAtDom: string | null }).__themeAtDom)).toBe(scheme);
    await page.screenshot({ path: `test-results/screens/placeholder-${scheme}-${test.info().project.name}.png`, fullPage: true });
  });
}
