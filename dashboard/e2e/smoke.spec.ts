// The pipeline's smoke test: the served page has the CSP (no unsafe-inline, Trusted Types required), loads with no
// violation, enforces Trusted Types, and takes the token out of the address bar (D15).
import { expect, openDash, test } from "./helpers";

test("the page loads under the CSP with no violation and drops the token from the address", async ({ page, serve }) => {
  const d = await serve("idle");
  const resp = await page.goto(`${d.url}/#t=${d.token}`);
  const csp = resp?.headers()["content-security-policy"] ?? "";
  expect(csp).toContain("require-trusted-types-for 'script'");
  expect(csp).toContain("trusted-types 'none'");
  for (const bad of ["unsafe-inline", "unsafe-eval", "nonce-", "sha256-"]) expect(csp).not.toContain(bad);
  await expect(page.getByTestId("machines")).toBeVisible();
  expect(page.url()).not.toContain("t=");
});

test("Trusted Types are enforced: an HTML sink throws", async ({ page, serve, browserName }) => {
  const d = await serve("idle");
  await openDash(page, d);
  const r = await page.evaluate(() => {
    const hasTT = typeof (window as unknown as { trustedTypes?: unknown }).trustedTypes !== "undefined";
    try {
      const el = document.createElement("div");
      // eslint-disable-next-line no-restricted-syntax, no-restricted-properties -- the test of Trusted Types itself
      el.innerHTML = "<img src=x>";
      return { hasTT, threw: false };
    } catch {
      return { hasTT, threw: true };
    }
  });
  expect(r.hasTT, `${browserName} has Trusted Types`).toBe(true);
  expect(r.threw).toBe(true);
  // the deliberate sink above raised a violation event; it is this test's own, not the page's
  await page.evaluate(() => ((window as unknown as { __violations: string[] }).__violations = []));
});
