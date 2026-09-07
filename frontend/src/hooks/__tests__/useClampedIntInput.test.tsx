import { act, renderHook } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { useClampedIntInput } from "../useClampedIntInput";

describe("useClampedIntInput", () => {
  it("keeps intermediate keystrokes below min instead of snapping back (type 300 with min 5)", () => {
    const commits: number[] = [];
    const { result } = renderHook(() =>
      useClampedIntInput(10, (next) => commits.push(next), 5),
    );

    act(() => result.current.onChange("3"));
    expect(result.current.value).toBe("3"); // display shows the raw text…
    expect(commits).toEqual([5]); // …while the committed value clamps to min

    act(() => result.current.onChange("30"));
    expect(result.current.value).toBe("30");
    expect(commits).toEqual([5, 30]);

    act(() => result.current.onChange("300"));
    expect(result.current.value).toBe("300");
    expect(commits).toEqual([5, 30, 300]);
  });

  it("settles the display on the clamped value after blur", () => {
    const { result } = renderHook(() => {
      const [value, setValue] = useState(10);
      return { input: useClampedIntInput(value, setValue, 5), value };
    });
    act(() => result.current.input.onChange("3"));
    expect(result.current.input.value).toBe("3"); // raw text shown while typing
    expect(result.current.value).toBe(5); // …committed value clamped to min
    act(() => result.current.input.onBlur());
    expect(result.current.input.value).toBe("5"); // buffer cleared → clamped value shows
  });

  it("lets the parent value flow through when not editing", () => {
    const { result, rerender } = renderHook(
      ({ value }) => useClampedIntInput(value, () => {}, 5),
      { initialProps: { value: 10 } },
    );
    expect(result.current.value).toBe("10");
    rerender({ value: 300 });
    expect(result.current.value).toBe("300");
  });

  it("clamps to max when provided", () => {
    const commits: number[] = [];
    const { result } = renderHook(() =>
      useClampedIntInput(60, (next) => commits.push(next), 10, 3600),
    );
    act(() => result.current.onChange("99999"));
    expect(commits).toEqual([3600]);
  });

  it("rejects non-digit input and clears to empty mid-edit", () => {
    const commits: number[] = [];
    const { result } = renderHook(() =>
      useClampedIntInput(10, (next) => commits.push(next), 5),
    );
    act(() => result.current.onChange("1a"));
    expect(result.current.value).toBe("10");
    expect(commits).toEqual([]);

    act(() => result.current.onChange(""));
    expect(result.current.value).toBe("");
    expect(commits).toEqual([]); // empty commits nothing, parent keeps last value
  });

  it("re-commits when the callback identity changes", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { result, rerender } = renderHook(
      ({ onCommit }) => useClampedIntInput(10, onCommit, 5),
      { initialProps: { onCommit: first } },
    );
    act(() => result.current.onChange("7"));
    expect(first).toHaveBeenCalledWith(7);
    rerender({ onCommit: second });
    act(() => result.current.onChange("8"));
    expect(second).toHaveBeenCalledWith(8);
  });
});
