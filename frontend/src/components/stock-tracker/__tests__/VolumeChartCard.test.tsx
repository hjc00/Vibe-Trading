import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SymbolSnapshot } from "@/lib/api";
import { VolumeChartCard } from "../VolumeChartCard";

const capturedOptions: unknown[] = [];

vi.mock("@/hooks/useChartLifecycle", () => ({
  useChartLifecycle: (_ref: unknown, buildOption: () => unknown) => {
    capturedOptions.push(buildOption());
  },
}));

function makeSymbol(volumeOnly: boolean): SymbolSnapshot {
  const dates = [
    "2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21", "2026-08-24",
    "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28", "2026-08-31",
  ];
  const volumes = [6000, 5800, 6200, 5900, 15000, 6500, 7000, 6800, 7200, 15000];
  // Slight up/down price drift so candles have a clear direction.
  const closes = [100, 101, 100.5, 102, 103, 102.5, 104, 103.5, 105, 106];
  return {
    code: "600519.SH",
    name: "贵州茅台",
    market: "a_share",
    currency: "CNY",
    volume_series: dates.map((date, i) =>
      volumeOnly
        ? { trade_date: date, volume: volumes[i], is_burst: i === 4 || i === 9 }
        : {
            trade_date: date,
            open: closes[i] - 0.5,
            high: closes[i] + 1,
            low: closes[i] - 1,
            close: closes[i],
            volume: volumes[i],
            is_burst: i === 4 || i === 9,
          },
    ),
    period_signals: {
      "5": { metrics: { period: 5, sessions: 5 }, signals: {} },
      "20": { metrics: { period: 20, sessions: 20 }, signals: {} },
    },
  };
}

describe("VolumeChartCard", () => {
  it("renders a composite candlestick + volume chart when OHLC is present", () => {
    render(<VolumeChartCard symbol={makeSymbol(false)} />);

    expect(screen.getByText("Price & volume")).toBeInTheDocument();
    expect(screen.getByText("5d")).toBeInTheDocument();
    expect(screen.getByText(/^Avg:/)).toBeInTheDocument();

    const option = capturedOptions[capturedOptions.length - 1] as {
      series: { type: string; data: unknown[] }[];
    };
    expect(option.series[0]?.type).toBe("candlestick");
    expect(option.series[1]?.type).toBe("bar");
    expect(option.series[0]?.data).toHaveLength(10);
    expect(option.series[1]?.data).toHaveLength(10);
  });

  it("falls back to the volume-only bar chart when the series lacks OHLC", () => {
    render(<VolumeChartCard symbol={makeSymbol(true)} />);

    expect(screen.getByText("Price & volume")).toBeInTheDocument();

    const option = capturedOptions[capturedOptions.length - 1] as {
      series: { type: string; data: unknown[] }[];
    };
    expect(option.series[0]?.type).toBe("bar");
    expect(option.series[0]?.data).toHaveLength(10);
  });

  it("shows the empty state when there is no volume series", () => {
    render(<VolumeChartCard symbol={null} />);
    expect(screen.getByText("No volume data")).toBeInTheDocument();
  });

  it("toggles between one and two column widths from the header", () => {
    render(<VolumeChartCard symbol={makeSymbol(false)} />);

    // Defaults to the wide (two-column) layout.
    const card = screen.getByTestId("volume-chart-card");
    expect(card.className).toContain("sm:col-span-2");
    expect(card.className).toContain("lg:col-span-2");

    fireEvent.click(screen.getByRole("button", { name: "Shrink card to one column" }));
    expect(card.className).not.toContain("col-span-2");

    fireEvent.click(screen.getByRole("button", { name: "Expand card to two columns" }));
    expect(card.className).toContain("sm:col-span-2");
    expect(card.className).toContain("lg:col-span-2");
  });
});
