import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { SectorStrength } from "@/lib/api";
import { SectorStrengthBoard } from "../SectorStrengthBoard";

const sectors: SectorStrength[] = [
  {
    board_code: "BK0477",
    board_name: "白酒",
    change_pct: 2.5,
    fund_flow_net: 1_200_000_000,
    up_count: 8,
    down_count: 2,
    leader: "600519.SH",
    market_rank: 1,
    member_count: 2,
    members: ["600519.SH", "000858.SZ"],
    period_metrics: [
      { period: 10, avg_return_pct: 0.052, avg_rps_market: 88.0 },
      { period: 20, avg_return_pct: 0.081 },
    ],
    prosperity_score: 72.4,
    source: "eastmoney",
  },
  {
    board_code: "BK1036",
    board_name: "半导体",
    change_pct: -1.2,
    fund_flow_net: -300_000_000,
    up_count: 3,
    down_count: 12,
    leader: null,
    market_rank: 2,
    member_count: 0,
    members: [],
    period_metrics: [],
    prosperity_score: null,
    source: "eastmoney",
  },
];

afterEach(() => {
  localStorage.clear();
});

describe("SectorStrengthBoard", () => {
  it("renders the sector ranking table with period trend", () => {
    render(<SectorStrengthBoard sectors={sectors} tradingDate="2026-08-31" />);

    expect(screen.getByText("Sector strength")).toBeInTheDocument();
    expect(screen.getByText("白酒")).toBeInTheDocument();
    expect(screen.getByText("#1")).toBeInTheDocument();
    expect(screen.getByText("+2.50%")).toBeInTheDocument();
    expect(screen.getByText("12.00亿")).toBeInTheDocument();
    expect(screen.getByText("8/2")).toBeInTheDocument();
    expect(screen.getByText("72")).toBeInTheDocument();
    expect(screen.getByText("600519.SH")).toBeInTheDocument();

    // Per-period watchlist trend chips.
    expect(screen.getByText("10D +5.2%")).toBeInTheDocument();
    expect(screen.getByText("20D +8.1%")).toBeInTheDocument();

    // Ranking-only board renders a dash for missing prosperity and no trend.
    expect(screen.getByText("-1.20%")).toBeInTheDocument();
  });

  it("shows the empty state when no sectors", () => {
    render(<SectorStrengthBoard sectors={[]} tradingDate={null} />);
    expect(screen.getByText("No sector strength data")).toBeInTheDocument();
  });

  it("shows the empty state when sectors is undefined", () => {
    render(<SectorStrengthBoard sectors={undefined} />);
    expect(screen.getByText("No sector strength data")).toBeInTheDocument();
  });

  it("collapses and expands the table body", () => {
    render(<SectorStrengthBoard sectors={sectors} tradingDate="2026-08-31" />);
    expect(screen.getByText("白酒")).toBeInTheDocument();

    const toggle = screen.getByRole("button", { name: "Sector strength" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(toggle);
    expect(screen.queryByText("白酒")).not.toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(toggle);
    expect(screen.getByText("白酒")).toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-expanded", "true");
  });

  it("pins watchlist boards to the front and caps the list at 20", () => {
    const manySectors: SectorStrength[] = [
      // 22 strong non-watchlist boards ranked 1..22.
      ...Array.from({ length: 22 }, (_, i) => ({
        board_name: `板块${i + 1}`,
        change_pct: 9 - i,
        market_rank: i + 1,
        member_count: 0,
        members: [] as string[],
        period_metrics: [],
        source: "eastmoney" as const,
      })),
      // One watchlist board ranked poorly (#60) — must still appear at the top.
      {
        board_name: "自选行业",
        change_pct: -3,
        market_rank: 60,
        member_count: 1,
        members: ["600519.SH"],
        period_metrics: [],
        source: "eastmoney" as const,
      },
    ];
    render(<SectorStrengthBoard sectors={manySectors} />);

    const rows = screen.getAllByRole("row");
    // Header row + exactly 20 data rows.
    expect(rows).toHaveLength(21);
    // The watchlist board is pinned to the first data row despite rank #60.
    expect(rows[1]).toHaveTextContent("自选行业");
    // The strongest remaining board follows it.
    expect(rows[2]).toHaveTextContent("板块1");
    // Boards beyond the 20-row cap are hidden.
    expect(screen.queryByText("板块21")).not.toBeInTheDocument();
  });
});
