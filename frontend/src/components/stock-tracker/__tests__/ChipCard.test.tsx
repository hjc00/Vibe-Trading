import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { SymbolSnapshot } from "@/lib/api";
import { ChipCard } from "../ChipCard";

function makeSymbol(chip: SymbolSnapshot["chip"]): SymbolSnapshot {
  return {
    code: "600519.SH",
    name: "贵州茅台",
    market: "a_share",
    currency: "CNY",
    period_signals: {},
    chip,
  };
}

describe("ChipCard", () => {
  it("renders concentration score, trend and holding boxes", () => {
    render(
      <ChipCard
        symbol={makeSymbol({
          holder_count: 100000,
          holder_count_change_pct: -5,
          holder_trend: "accumulating",
          avg_hold_amount: 500000,
          northbound_holding_ratio: 1.5,
          fund_holding_ratio: 2.0,
          chip_concentration_score: 70,
          holder_history: [
            { end_date: "2026-06-30", holder_count: 110000 },
            { end_date: "2026-09-30", holder_count: 100000 },
          ],
          source: "eastmoney",
        })}
      />,
    );

    expect(screen.getByText("Chip concentration")).toBeInTheDocument();
    expect(screen.getByText("Accumulating")).toBeInTheDocument();
    expect(screen.getByText("70")).toBeInTheDocument();
  });

  it("shows the empty state when symbol is null", () => {
    render(<ChipCard symbol={null} />);
    expect(screen.getByText("No chip data")).toBeInTheDocument();
  });

  it("shows the empty state when chip has no holder count or score", () => {
    render(<ChipCard symbol={makeSymbol({ holder_history: [], source: "unavailable" })} />);
    expect(screen.getByText("No chip data")).toBeInTheDocument();
  });
});
