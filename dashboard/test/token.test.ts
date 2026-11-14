// D15 (unit part): the token leaves the fragment and the history at boot.
import { beforeEach, describe, expect, it } from "vitest";
import { takeTokenFromFragment, token } from "../src/api/token";

const T = "a".repeat(64);

describe("token from the fragment", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.history.replaceState(null, "", "/");
  });
  it("moves #t= into localStorage and removes it from the address", () => {
    window.history.replaceState(null, "", "/#t=" + T);
    expect(takeTokenFromFragment()).toBe(true);
    expect(token()).toBe(T);
    expect(window.location.hash).toBe("");
    expect(window.location.href).not.toContain(T);
  });
  it("keeps the rest of the fragment (#job=)", () => {
    window.history.replaceState(null, "", "/#t=" + T + "&job=20261114-101500-k3f9qa");
    takeTokenFromFragment();
    expect(window.location.hash).toBe("#job=20261114-101500-k3f9qa");
  });
  it("refuses a token that is not 64 hex characters, and still removes it", () => {
    window.history.replaceState(null, "", "/#t=<img src=x>");
    expect(takeTokenFromFragment()).toBe(false);
    expect(token()).toBeNull();
    expect(window.location.hash).toBe("");
  });
});
