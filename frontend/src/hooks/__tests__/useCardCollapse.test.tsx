import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { cardCollapseKey, useCardCollapse } from "../useCardCollapse";

describe("useCardCollapse", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("defaults to expanded when nothing is stored", () => {
    const { result } = renderHook(() => useCardCollapse("volume"));
    expect(result.current.collapsed).toBe(false);
  });

  it("reads a stored collapsed preference", () => {
    localStorage.setItem(cardCollapseKey("volume"), "1");
    const { result } = renderHook(() => useCardCollapse("volume"));
    expect(result.current.collapsed).toBe(true);
  });

  it("persists the toggled state to localStorage", () => {
    const { result } = renderHook(() => useCardCollapse("volume"));
    act(() => result.current.toggle());
    expect(result.current.collapsed).toBe(true);
    expect(localStorage.getItem(cardCollapseKey("volume"))).toBe("1");
  });
});
