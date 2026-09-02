import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { MarketSentimentSnapshot } from "@/lib/api";
import { MarketSentimentBar } from "../MarketSentimentBar";

describe("MarketSentimentBar", () => {
  it("renders the temperature score and breadth sub-metrics", () => {
    const sentiment: MarketSentimentSnapshot = {
      sentiment_score: 60,
      limit_up_count: 50,
      limit_down_count: 5,
      broken_ratio: 0.2,
      max_board_height: 7,
      board_ladder: { "2": 10, "7": 1 },
      up_count: 3000,
      down_count: 2000,
      prev_limit_up_perf: 0.01,
      source: "eastmoney",
    };
    render(<MarketSentimentBar sentiment={sentiment} />);

    expect(screen.getByText("Market sentiment")).toBeInTheDocument();
    expect(screen.getByText("60")).toBeInTheDocument();
    expect(screen.getByText("50")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("20.0%")).toBeInTheDocument();
    expect(screen.getByText("3000/2000")).toBeInTheDocument();
  });

  it("shows the unavailable state when sentiment is null", () => {
    render(<MarketSentimentBar sentiment={null} />);
    expect(screen.getByText("Market sentiment unavailable")).toBeInTheDocument();
  });

  it("shows the unavailable state when the source is unavailable", () => {
    render(
      <MarketSentimentBar
        sentiment={{ board_ladder: {}, source: "unavailable", error: "no source" }}
      />,
    );
    expect(screen.getByText("Market sentiment unavailable")).toBeInTheDocument();
  });
});
