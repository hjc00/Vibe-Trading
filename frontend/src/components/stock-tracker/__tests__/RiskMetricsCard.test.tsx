import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { SymbolSnapshot } from "@/lib/api";
import { RiskMetricsCard } from "../RiskMetricsCard";

function makeSymbol(risk: SymbolSnapshot["risk"]): SymbolSnapshot {
  return {
    code: "600519.SH",
    name: "贵州茅台",
    market: "a_share",
    currency: "CNY",
    period_signals: {},
    risk,
  };
}

describe("RiskMetricsCard", () => {
  it("renders risk metrics and stop-loss reference when present", () => {
    render(
      <RiskMetricsCard
        symbol={makeSymbol({
          atr_14: 3.25,
          atr_pct: 0.03,
          max_drawdown_60d: -0.1823,
          beta_vs_index: 1.2,
          beta_window: 60,
          benchmark_code: "000300.SH",
          stop_loss_price: 101.4,
          stop_loss_atr_multiple: 2,
        })}
      />,
    );

    expect(screen.getByText("Risk metrics")).toBeInTheDocument();
    expect(screen.getByText("3.25")).toBeInTheDocument();
    expect(screen.getByText("3.0%")).toBeInTheDocument();
    expect(screen.getByText("-18.2%")).toBeInTheDocument();
    expect(screen.getByText("1.20")).toBeInTheDocument();
    expect(screen.getByText("101.40")).toBeInTheDocument();
  });

  it("shows the unavailable message when symbol has no risk data", () => {
    render(<RiskMetricsCard symbol={makeSymbol(null)} />);
    expect(screen.getByText("No risk data")).toBeInTheDocument();
  });

  it("shows the unavailable message when symbol is null", () => {
    render(<RiskMetricsCard symbol={null} />);
    expect(screen.getByText("No risk data")).toBeInTheDocument();
  });
});
