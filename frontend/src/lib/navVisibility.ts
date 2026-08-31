// Sidebar navigation visibility configuration. Each main nav item can be
// shown or hidden independently; hidden routes remain accessible by direct URL.

import { safeGet, safeSet } from "./storage";

export const STORAGE_KEY = "vibe-nav-visibility";

export interface NavItemConfig {
  readonly to: string;
  readonly i18nKey: `layout.${string}`;
}

// Order matches the current sidebar. These are the configurable items.
export const NAV_CONFIG = [
  { to: "/", i18nKey: "layout.agent" },
  { to: "/runtime", i18nKey: "layout.runtime" },
  { to: "/scheduled", i18nKey: "layout.scheduled" },
  { to: "/reports", i18nKey: "layout.reports" },
  { to: "/portfolio", i18nKey: "layout.portfolio" },
  { to: "/alpha-zoo", i18nKey: "layout.alphaZoo" },
  { to: "/options", i18nKey: "layout.optionsLab" },
  { to: "/settings", i18nKey: "layout.settings" },
  { to: "/correlation", i18nKey: "layout.correlation" },
] as const satisfies readonly NavItemConfig[];

export type NavVisibility = Record<string, boolean>;

export function getNavVisibility(): NavVisibility {
  const raw = safeGet(STORAGE_KEY);
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as NavVisibility;
    }
  } catch {
    /* ignore invalid JSON */
  }
  return {};
}

export function setNavVisibility(config: NavVisibility): void {
  safeSet(STORAGE_KEY, JSON.stringify(config));
}

export function isNavVisible(to: string, config: NavVisibility = getNavVisibility()): boolean {
  // Default to visible when no explicit preference is stored.
  return config[to] !== false;
}
