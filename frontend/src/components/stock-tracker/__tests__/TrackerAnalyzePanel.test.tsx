import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SymbolSnapshot } from "@/lib/api";
import { TrackerAnalyzePanel } from "../TrackerAnalyzePanel";

function symbol(code: string, name?: string): SymbolSnapshot {
  return {
    code,
    name: name ?? null,
    market: "a_share",
    close: 100,
    prev_close: 99,
    daily_return: 1,
    volume: 1000,
    avg_volume_20: 900,
    currency: "CNY",
    period_signals: {},
    diff: null,
    error: null,
  };
}

const baseProps = {
  symbols: [symbol("600519.SH", "贵州茅台"), symbol("000001.SZ", "平安银行")],
  selectedSymbols: [],
  onSelectedSymbolsChange: vi.fn(),
  userPrompt: "",
  onUserPromptChange: vi.fn(),
  loading: false,
  onRun: vi.fn(),
  onClose: vi.fn(),
};

describe("TrackerAnalyzePanel", () => {
  it("renders a chip for every symbol", () => {
    render(<TrackerAnalyzePanel {...baseProps} />);
    expect(screen.getByText("贵州茅台")).toBeInTheDocument();
    expect(screen.getByText("平安银行")).toBeInTheDocument();
  });

  it("disables the run button when no symbols are selected", () => {
    render(<TrackerAnalyzePanel {...baseProps} />);
    expect(screen.getByRole("button", { name: /run analysis/i })).toBeDisabled();
  });

  it("select all reports every symbol code", () => {
    const onSelectedSymbolsChange = vi.fn();
    render(<TrackerAnalyzePanel {...baseProps} onSelectedSymbolsChange={onSelectedSymbolsChange} />);
    fireEvent.click(screen.getByRole("button", { name: /select all/i }));
    expect(onSelectedSymbolsChange).toHaveBeenCalledWith(["600519.SH", "000001.SZ"]);
  });

  it("toggling a chip adds or removes it from the selection", () => {
    const onSelectedSymbolsChange = vi.fn();
    render(
      <TrackerAnalyzePanel
        {...baseProps}
        selectedSymbols={["600519.SH"]}
        onSelectedSymbolsChange={onSelectedSymbolsChange}
      />,
    );
    fireEvent.click(screen.getByText("平安银行"));
    expect(onSelectedSymbolsChange).toHaveBeenCalledWith(["600519.SH", "000001.SZ"]);
  });

  it("always shows the optional extra-instruction textarea", () => {
    render(<TrackerAnalyzePanel {...baseProps} />);
    expect(screen.getByPlaceholderText(/bullish MA alignment/i)).toBeInTheDocument();
  });

  it("typing the extra instruction reports the value", () => {
    const onUserPromptChange = vi.fn();
    render(<TrackerAnalyzePanel {...baseProps} onUserPromptChange={onUserPromptChange} />);
    fireEvent.change(screen.getByPlaceholderText(/bullish MA alignment/i), {
      target: { value: "重点看均线多头排列" },
    });
    expect(onUserPromptChange).toHaveBeenCalledWith("重点看均线多头排列");
  });
});
