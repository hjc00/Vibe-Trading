import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SectionNav, type NavSection } from "../SectionNav";

const SECTIONS: NavSection[] = [
  { id: "st-overview", labelKey: "stockTracker.navOverview" },
  { id: "st-watchlist", labelKey: "stockTracker.navWatchlist" },
  { id: "st-backtest", labelKey: "stockTracker.navBacktest" },
  { id: "st-detail-cards", labelKey: "stockTracker.navDetailCards" },
  { id: "st-financial-sector", labelKey: "stockTracker.navFinancialSector" },
  { id: "st-charts", labelKey: "stockTracker.navCharts" },
  { id: "st-analysis", labelKey: "stockTracker.navAiAnalysis" },
];

function renderNav(props: Partial<Parameters<typeof SectionNav>[0]> = {}) {
  const onNavigate = vi.fn();
  const onBackToTop = vi.fn();
  render(
    <SectionNav
      sections={SECTIONS}
      activeId={null}
      onNavigate={onNavigate}
      onBackToTop={onBackToTop}
      {...props}
    />,
  );
  return { onNavigate, onBackToTop };
}

describe("SectionNav", () => {
  it("renders the outline title, each section label and the back-to-top action", () => {
    renderNav();
    expect(screen.getByRole("complementary", { name: "On this page" })).toBeInTheDocument();
    for (const label of [
      "Overview",
      "Watchlist",
      "Backtest",
      "Detail cards",
      "Financial & sector",
      "Charts",
      "AI analysis",
    ]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "Back to top" })).toBeInTheDocument();
  });

  it("marks the active section with aria-current", () => {
    renderNav({ activeId: "st-backtest" });
    expect(screen.getByRole("button", { name: "Backtest" }).getAttribute("aria-current")).toBe(
      "true",
    );
    expect(screen.getByRole("button", { name: "Overview" }).getAttribute("aria-current")).toBeNull();
  });

  it("calls onNavigate with the section id on click", () => {
    const { onNavigate } = renderNav();
    fireEvent.click(screen.getByRole("button", { name: "Charts" }));
    expect(onNavigate).toHaveBeenCalledWith("st-charts");
  });

  it("calls onBackToTop when the back-to-top action is clicked", () => {
    const { onBackToTop } = renderNav();
    fireEvent.click(screen.getByRole("button", { name: "Back to top" }));
    expect(onBackToTop).toHaveBeenCalledTimes(1);
  });

  it("renders nothing when there are no sections", () => {
    const { container } = render(
      <SectionNav sections={[]} activeId={null} onNavigate={vi.fn()} onBackToTop={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
