import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { SymbolSnapshot } from "@/lib/api";
import { VolumeCard } from "../VolumeCard";

function makeSymbol(): SymbolSnapshot {
  return {
    code: "600519.SH",
    name: "贵州茅台",
    market: "a_share",
    currency: "CNY",
    volume: 15000,
    avg_volume_20: 7000,
    period_signals: {
      "5": {
        metrics: {
          period: 5,
          sessions: 5,
          avg_volume: 6000,
          volume_ratio: 1.4,
          volume_expansion_days: 2,
          volume_expansion_ratio: 0.4,
        },
        signals: {},
      },
      "20": {
        metrics: {
          period: 20,
          sessions: 20,
          avg_volume: 7000,
          volume_ratio: 1.1,
          volume_expansion_days: 4,
          volume_expansion_ratio: 0.2,
        },
        signals: {},
      },
    },
  };
}

describe("VolumeCard", () => {
  it("renders today's volume, ratios and per-period rows", () => {
    render(<VolumeCard symbol={makeSymbol()} />);

    expect(screen.getByText("Volume")).toBeInTheDocument();
    // Header value chips.
    expect(screen.getByText("Today's volume (lots)")).toBeInTheDocument();
    expect(screen.getByText("1.5万")).toBeInTheDocument();
    expect(screen.getByText("Vol ratio vs 5d")).toBeInTheDocument();
    expect(screen.getByText("2.50x")).toBeInTheDocument();
    expect(screen.getByText("Avg 5d volume (lots)")).toBeInTheDocument();
    expect(screen.getAllByText("6,000").length).toBeGreaterThan(0);
    expect(screen.getByText("Avg 20d volume (lots)")).toBeInTheDocument();
    expect(screen.getAllByText("7,000").length).toBeGreaterThan(0);

    // Per-period comparison rows (en locale renders "{period}d").
    expect(screen.getByText("5d")).toBeInTheDocument();
    expect(screen.getByText("20d")).toBeInTheDocument();
    expect(screen.getByText("140%")).toBeInTheDocument();
    expect(screen.getByText("110%")).toBeInTheDocument();
    expect(screen.getByText(/2\/5/)).toBeInTheDocument();
    expect(screen.getByText(/4\/20/)).toBeInTheDocument();
  });

  it("shows the empty state when symbol is null", () => {
    render(<VolumeCard symbol={null} />);
    expect(screen.getByText("No volume data")).toBeInTheDocument();
  });

  it("shows the empty state when there are no volume metrics", () => {
    render(
      <VolumeCard
        symbol={{
          code: "600519.SH",
          market: "a_share",
          currency: "CNY",
          period_signals: {},
        }}
      />,
    );
    expect(screen.getByText("No volume data")).toBeInTheDocument();
  });
});
