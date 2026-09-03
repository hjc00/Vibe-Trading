import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SymbolSnapshot } from "@/lib/api";
import { VolumeChartCard } from "../VolumeChartCard";

const capturedOptions: unknown[] = [];

vi.mock("@/hooks/useChartLifecycle", () => ({
  useChartLifecycle: (_ref: unknown, buildOption: () => unknown) => {
    capturedOptions.push(buildOption());
  },
}));

function makeSymbol(): SymbolSnapshot {
  return {
    code: "600519.SH",
    name: "贵州茅台",
    market: "a_share",
    currency: "CNY",
    volume_series: [
      { trade_date: "2026-08-18", volume: 6000, is_burst: false },
      { trade_date: "2026-08-19", volume: 5800, is_burst: false },
      { trade_date: "2026-08-20", volume: 6200, is_burst: false },
      { trade_date: "2026-08-21", volume: 5900, is_burst: false },
      { trade_date: "2026-08-24", volume: 15000, is_burst: true },
      { trade_date: "2026-08-25", volume: 6500, is_burst: false },
      { trade_date: "2026-08-26", volume: 7000, is_burst: false },
      { trade_date: "2026-08-27", volume: 6800, is_burst: false },
      { trade_date: "2026-08-28", volume: 7200, is_burst: false },
      { trade_date: "2026-08-31", volume: 15000, is_burst: true },
    ],
    period_signals: {
      "5": { metrics: { period: 5, sessions: 5 }, signals: {} },
      "20": { metrics: { period: 20, sessions: 20 }, signals: {} },
    },
  };
}

describe("VolumeChartCard", () => {
  it("renders a period-switchable daily volume bar chart", () => {
    render(<VolumeChartCard symbol={makeSymbol()} />);

    expect(screen.getByText("Period volume")).toBeInTheDocument();
    // Period switch + avg caption.
    expect(screen.getAllByText("5d").length).toBeGreaterThan(0);
    expect(screen.getByText(/^Avg:/)).toBeInTheDocument();

    const option = capturedOptions[capturedOptions.length - 1] as {
      series: { type: string; data: unknown[] }[];
      xAxis: { data: string[] };
    };
    expect(option.series[0]?.type).toBe("bar");
    expect(option.series[0]?.data).toHaveLength(10);
    expect(option.xAxis.data).toHaveLength(10);
  });

  it("shows the empty state when there is no volume series", () => {
    render(<VolumeChartCard symbol={null} />);
    expect(screen.getByText("No volume data")).toBeInTheDocument();
  });
});
