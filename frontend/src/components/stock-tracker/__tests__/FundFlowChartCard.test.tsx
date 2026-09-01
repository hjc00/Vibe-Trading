import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SymbolSnapshot } from "@/lib/api";
import { FundFlowChartCard } from "../FundFlowChartCard";

const capturedOptions: unknown[] = [];

vi.mock("@/hooks/useChartLifecycle", () => ({
  useChartLifecycle: (_ref: unknown, buildOption: () => unknown) => {
    capturedOptions.push(buildOption());
  },
}));

vi.mock("@/lib/chart-theme", () => ({
  getChartTheme: () => ({
    gridColor: "#e5e7eb",
    textColor: "#6b7280",
    axisColor: "#374151",
    upColor: "#22c55e",
    downColor: "#ef4444",
    tooltipBg: "rgba(255,255,255,0.96)",
    tooltipBorder: "#e5e7eb",
    tooltipText: "#374151",
    infoColor: "#3b82f6",
  }),
}));

function makeSymbol(fundFlowHistory: SymbolSnapshot["capital"]["fund_flow"]["history"]): SymbolSnapshot {
  return {
    code: "600519.SH",
    name: "贵州茅台",
    market: "SH",
    currency: "CNY",
    period_signals: {},
    capital: {
      fund_flow: {
        trade_date: "2026-08-31",
        main_net: 1.2e8,
        main_5d_net: 4.5e8,
        history: fundFlowHistory,
      },
      margin: {
        trade_date: "2026-08-31",
        history: [],
      },
      fund_flow_source: "eastmoney",
      margin_source: "unavailable",
    },
  };
}

describe("FundFlowChartCard", () => {
  it("renders the title and chart container when data is present", () => {
    const symbol = makeSymbol([
      { trade_date: "2026-08-25", main_net: -5e7 },
      { trade_date: "2026-08-26", main_net: 2e7 },
      { trade_date: "2026-08-27", main_net: 1e8 },
      { trade_date: "2026-08-28", main_net: -3e7 },
      { trade_date: "2026-08-31", main_net: 1.2e8 },
    ]);
    render(<FundFlowChartCard symbol={symbol} />);

    expect(screen.getByText("Main-force capital flow trend")).toBeInTheDocument();
    const option = capturedOptions[capturedOptions.length - 1] as {
      series: { data: (number | null)[] }[];
      visualMap?: { pieces: { color: string }[] };
    };
    expect(option.series[0].data).toEqual([1.2e8, -3e7, 1e8, 2e7, -5e7]);
    expect(option.visualMap?.pieces.map((p) => p.color)).toEqual(["#22c55e", "#ef4444", "#6b7280"]);
  });

  it("shows unavailable message when there is no fund flow data", () => {
    render(<FundFlowChartCard symbol={makeSymbol([])} />);

    const option = capturedOptions[capturedOptions.length - 1] as {
      title?: { text: string };
    };
    expect(option.title?.text).toBe("Fund flow data unavailable");
  });

  it("shows unavailable message when capital is null", () => {
    const symbol = makeSymbol([]);
    delete (symbol as SymbolSnapshot & { capital?: SymbolSnapshot["capital"] }).capital;
    render(<FundFlowChartCard symbol={symbol} />);

    const option = capturedOptions[capturedOptions.length - 1] as {
      title?: { text: string };
    };
    expect(option.title?.text).toBe("Fund flow data unavailable");
  });
});
