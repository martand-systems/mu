// The theme toggle (the owner, 2026-11-14: light and dark follow the system, with a toggle). theme.js sets
// data-theme on <html> before first paint from localStorage["mu.theme"] or the system; this module changes the choice
// at runtime. "system" removes the stored choice and follows prefers-color-scheme again.
import { useSyncExternalStore } from "react";

export type ThemeChoice = "system" | "light" | "dark";
const KEY = "mu.theme";
const EVENT = "mu:theme";

function systemDark(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function storedChoice(): ThemeChoice {
  try {
    const v = window.localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(): void {
  const c = storedChoice();
  document.documentElement.setAttribute("data-theme", c === "system" ? (systemDark() ? "dark" : "light") : c);
}

export function setTheme(c: ThemeChoice): void {
  try {
    if (c === "system") window.localStorage.removeItem(KEY);
    else window.localStorage.setItem(KEY, c);
  } catch {
    // storage refused: the choice lasts for this page only
  }
  applyTheme();
  window.dispatchEvent(new Event(EVENT));
}

function subscribe(fn: () => void): () => void {
  window.addEventListener(EVENT, fn);
  window.addEventListener("storage", fn);
  return () => {
    window.removeEventListener(EVENT, fn);
    window.removeEventListener("storage", fn);
  };
}

export function useThemeChoice(): ThemeChoice {
  return useSyncExternalStore(subscribe, storedChoice, () => "system");
}

/** The theme in effect ("light" or "dark"), following data-theme on <html> (charts redraw on a change). */
export function currentTheme(): "light" | "dark" {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

function subscribeAttr(fn: () => void): () => void {
  const mo = new MutationObserver(fn);
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return () => mo.disconnect();
}

export function useEffectiveTheme(): "light" | "dark" {
  return useSyncExternalStore(subscribeAttr, currentTheme, () => "light");
}

/** A design token's value, read from the CSS variables (for canvases, which CSS cannot restyle). */
export function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim() || "#888888";
}
