// D38 (the app's part, to be completed with the decoders): every golden /api fixture parses and has api: 1.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const dir = join(import.meta.dirname, "..", "..", "tools", "test-dashboard", "fixtures");

describe("golden /api fixtures", () => {
  for (const f of readdirSync(dir).filter((n) => n.startsWith("api-")).sort()) {
    it(f, () => {
      const body = JSON.parse(readFileSync(join(dir, f), "utf8")) as { api?: unknown };
      expect(body.api).toBe(1);
    });
  }
});
