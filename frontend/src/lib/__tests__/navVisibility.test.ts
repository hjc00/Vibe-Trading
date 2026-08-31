import { afterEach, describe, expect, it } from "vitest";
import { NAV_CONFIG, getNavVisibility, isNavVisible, setNavVisibility } from "../navVisibility";

describe("navVisibility", () => {
  afterEach(() => {
    window.localStorage.clear();
  });

  it("returns an empty object when nothing is stored", () => {
    expect(getNavVisibility()).toEqual({});
  });

  it("persists and reads visibility preferences", () => {
    const config = { "/options": false, "/alpha-zoo": false };
    setNavVisibility(config);
    expect(getNavVisibility()).toEqual(config);
  });

  it("treats unconfigured routes as visible by default", () => {
    expect(isNavVisible("/options", {})).toBe(true);
    expect(isNavVisible("/options", { "/options": true })).toBe(true);
    expect(isNavVisible("/options", { "/options": false })).toBe(false);
  });

  it("ignores invalid stored JSON", () => {
    window.localStorage.setItem("vibe-nav-visibility", "not-json");
    expect(getNavVisibility()).toEqual({});
  });

  it("lists every configurable route", () => {
    expect(NAV_CONFIG.map((item) => item.to)).toEqual([
      "/",
      "/runtime",
      "/scheduled",
      "/reports",
      "/portfolio",
      "/alpha-zoo",
      "/options",
      "/settings",
      "/correlation",
    ]);
  });
});
