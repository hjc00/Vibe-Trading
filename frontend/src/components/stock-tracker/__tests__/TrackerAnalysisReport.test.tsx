import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { TrackerAnalyzeReport } from "@/lib/api";
import { TrackerAnalysisReport } from "../TrackerAnalysisReport";

function report(): TrackerAnalyzeReport {
  return {
    summary: "整体偏多，但需关注风险。",
    symbols: [
      {
        code: "600519.SH",
        name: "贵州茅台",
        recommendation: "top_pick",
        confidence: "high",
        rationale: "均线多头排列且放量突破。",
        key_metrics: { rsi: 60.5, volume_ratio: 1.6 },
        risks: ["估值偏高"],
        time_horizon: "2-4 周",
      },
    ],
    portfolio: {
      theme: "短期机会集中在消费板块",
      top_pick: "600519.SH",
      cautions: ["注意整体量能持续性"],
    },
    caveats: ["仅基于技术信号，未考虑基本面"],
  };
}

describe("TrackerAnalysisReport", () => {
  it("renders null when there is no report", () => {
    const { container } = render(<TrackerAnalysisReport report={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the summary, per-symbol card, portfolio and caveats", () => {
    render(<TrackerAnalysisReport report={report()} />);

    expect(screen.getByText("整体偏多，但需关注风险。")).toBeInTheDocument();
    expect(screen.getByText("贵州茅台")).toBeInTheDocument();
    expect(screen.getByText("top_pick")).toBeInTheDocument();
    expect(screen.getByText("均线多头排列且放量突破。")).toBeInTheDocument();
    expect(screen.getByText("短期机会集中在消费板块")).toBeInTheDocument();
    expect(screen.getByText(/research only, not investment advice/i)).toBeInTheDocument();
  });

  it("shows the research disclaimer", () => {
    render(<TrackerAnalysisReport report={report()} />);
    expect(screen.getByText(/research only, not investment advice/i)).toBeInTheDocument();
  });
});
