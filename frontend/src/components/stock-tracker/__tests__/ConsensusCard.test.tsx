import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { SymbolSnapshot } from "@/lib/api";
import { ConsensusCard } from "../ConsensusCard";

function makeSymbol(consensus: SymbolSnapshot["consensus"]): SymbolSnapshot {
  return {
    code: "600519.SH",
    name: "贵州茅台",
    market: "a_share",
    currency: "CNY",
    period_signals: {},
    close: 100,
    consensus,
  };
}

describe("ConsensusCard", () => {
  it("renders coverage, rating, EPS, forward PE and target price", () => {
    render(
      <ConsensusCard
        symbol={makeSymbol({
          analyst_count: 10,
          rating_score: 80,
          consensus_eps_cur: 2.0,
          consensus_eps_next: 2.5,
          forward_pe: 40.0,
          target_price_avg: 150,
          target_price_low: 120,
          target_price_high: 180,
          upside_pct: 0.5,
          rating_distribution: { 买入: 8, 中性: 2 },
          source: "eastmoney",
        })}
      />,
    );

    expect(screen.getByText("Consensus")).toBeInTheDocument();
    expect(screen.getByText("10")).toBeInTheDocument();
    expect(screen.getByText("80")).toBeInTheDocument();
    expect(screen.getByText("2.00")).toBeInTheDocument();
    expect(screen.getByText("2.50")).toBeInTheDocument();
    expect(screen.getByText("40.0")).toBeInTheDocument();
    expect(screen.getByText("150")).toBeInTheDocument();
    expect(screen.getByText("+50.0%")).toBeInTheDocument();
  });

  it("shows the empty state when symbol is null", () => {
    render(<ConsensusCard symbol={null} />);
    expect(screen.getByText("No consensus data")).toBeInTheDocument();
  });

  it("shows the empty state when no consensus fields present", () => {
    render(<ConsensusCard symbol={makeSymbol({ rating_distribution: {} })} />);
    expect(screen.getByText("No consensus data")).toBeInTheDocument();
  });
});
