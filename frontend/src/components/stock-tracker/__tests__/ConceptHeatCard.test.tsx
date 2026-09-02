import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { SymbolSnapshot } from "@/lib/api";
import { ConceptHeatCard } from "../ConceptHeatCard";

function makeSymbol(concept: SymbolSnapshot["concept"]): SymbolSnapshot {
  return {
    code: "600519.SH",
    name: "贵州茅台",
    market: "a_share",
    currency: "CNY",
    period_signals: {},
    concept,
  };
}

describe("ConceptHeatCard", () => {
  it("renders the hottest concept, rank, heat score and limit-up count", () => {
    render(
      <ConceptHeatCard
        symbol={makeSymbol({
          boards: ["白酒概念", "消费"],
          hottest_concept: "白酒概念",
          hottest_concept_rank: 1,
          concept_heat_score: 80,
          limit_up_count: 5,
          source: "eastmoney",
        })}
      />,
    );

    expect(screen.getByText("Concept heat")).toBeInTheDocument();
    // The hottest concept name appears both as the headline and as a highlighted chip.
    expect(screen.getAllByText("白酒概念").length).toBeGreaterThan(0);
    expect(screen.getByText("#1")).toBeInTheDocument();
    expect(screen.getByText("80")).toBeInTheDocument();
    expect(screen.getByText("消费")).toBeInTheDocument();
  });

  it("shows the empty state when symbol is null", () => {
    render(<ConceptHeatCard symbol={null} />);
    expect(screen.getByText("No concept heat data")).toBeInTheDocument();
  });

  it("shows the empty state when the concept has no boards or hottest", () => {
    render(<ConceptHeatCard symbol={makeSymbol({ boards: [], source: "unavailable" })} />);
    expect(screen.getByText("No concept heat data")).toBeInTheDocument();
  });
});
