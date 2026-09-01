import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SymbolSnapshot } from "@/lib/api";
import { RpsChartCard } from "../RpsChartCard";

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
    warningColor: "#f59e0b",
    success: "#22c55e",
    danger: "#ef4444",
  }),
}));

function makeSymbol(rpsMarket: number | null, rpsSector: number | null): SymbolSnapshot {
  return {
    code: "600519.SH",
    name: "贵州茅台",
    market: "a_share",
    currency: "CNY",
    period_signals: {
      "10": {
        metrics: {
          period: 10,
          sessions: 10,
          rps_market: rpsMarket,
          rps_sector: rpsSector,
        },
        signals: {},
      },
      "20": {
        metrics: {
          period: 20,
          sessions: 20,
          rps_market: rpsMarket != null ? rpsMarket - 5 : null,
          rps_sector: rpsSector != null ? rpsSector - 5 : null,
        },
        signals: {},
      },
    },
  };
}

describe("RpsChartCard", () => {
  it("renders the title and chart container when data is present", () => {
    const symbol = makeSymbol(85, 70);
    render(<RpsChartCard symbol={symbol} />);

    expect(screen.getByText("RPS trend")).toBeInTheDocument();
    const option = capturedOptions[capturedOptions.length - 1] as {
      xAxis: { data: string[] };
      series: { data: (number | null)[]; name: string }[];
    };
    expect(option.xAxis.data).toEqual(["10Period", "20Period"]);
    const marketSeries = option.series.find((s) => s.name === "RPS Market");
    expect(marketSeries?.data).toEqual([85, 80]);
    const sectorSeries = option.series.find((s) => s.name === "RPS Sector");
    expect(sectorSeries?.data).toEqual([70, 65]);
  });

  it("shows unavailable message when there is no RPS data", () => {
    render(<RpsChartCard symbol={makeSymbol(null, null)} />);

    const option = capturedOptions[capturedOptions.length - 1] as {
      title?: { text: string };
    };
    expect(option.title?.text).toBe("No RPS data");
  });

  it("shows unavailable message when symbol is null", () => {
    render(<RpsChartCard symbol={null} />);

    const option = capturedOptions[capturedOptions.length - 1] as {
      title?: { text: string };
    };
    expect(option.title?.text).toBe("No RPS data");
  });
});
