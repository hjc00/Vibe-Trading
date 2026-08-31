import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TrackerAnalyzeReport } from "@/lib/api";
import { useStockTrackerAnalysisStore } from "../stockTrackerAnalysis";

const apiMock = vi.hoisted(() => ({
  analyzeStockTracker: vi.fn(),
  getStockTrackerAnalysis: vi.fn(),
  getStockTrackerAnalysisHistory: vi.fn(),
  getStockTrackerAnalysisById: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    api: { ...actual.api, ...apiMock },
  };
});

function report(): TrackerAnalyzeReport {
  return {
    summary: "综述",
    symbols: [],
    portfolio: { theme: "", top_pick: null, cautions: [] },
    caveats: [],
  };
}

beforeEach(() => {
  useStockTrackerAnalysisStore.getState().reset();
  apiMock.analyzeStockTracker.mockReset();
  apiMock.getStockTrackerAnalysis.mockReset();
  apiMock.getStockTrackerAnalysisHistory.mockReset();
  apiMock.getStockTrackerAnalysisById.mockReset();
});

describe("stockTrackerAnalysis store", () => {
  it("has correct defaults", () => {
    const s = useStockTrackerAnalysisStore.getState();
    expect(s.open).toBe(false);
    expect(s.selectedSymbols).toEqual([]);
    expect(s.focus).toBe("rank_opportunities");
    expect(s.userPrompt).toBe("");
    expect(s.loading).toBe(false);
    expect(s.report).toBeNull();
    expect(s.error).toBeNull();
    expect(s.history).toEqual([]);
    expect(s.selectedId).toBeNull();
  });

  it("run is a no-op when no symbols are selected", async () => {
    await useStockTrackerAnalysisStore.getState().run();
    expect(apiMock.analyzeStockTracker).not.toHaveBeenCalled();
  });

  it("run sets loading, calls the API, stores the report and refreshes history", async () => {
    apiMock.analyzeStockTracker.mockResolvedValue({
      status: "ok",
      report: report(),
      id: "20260831T101500000000",
    });
    apiMock.getStockTrackerAnalysisHistory.mockResolvedValue({
      status: "ok",
      items: [{ id: "20260831T101500000000", summary: "综述" }],
    });
    const store = useStockTrackerAnalysisStore.getState();
    store.setSelectedSymbols(["600519.SH"]);
    store.setFocus("risk_check");

    const pending = store.run();
    expect(useStockTrackerAnalysisStore.getState().loading).toBe(true);

    await pending;
    const s = useStockTrackerAnalysisStore.getState();
    expect(s.loading).toBe(false);
    expect(s.report?.summary).toBe("综述");
    expect(s.selectedId).toBe("20260831T101500000000");
    expect(s.history).toHaveLength(1);
    expect(apiMock.analyzeStockTracker).toHaveBeenCalledWith({
      symbols: ["600519.SH"],
      focus: "risk_check",
      user_prompt: null,
    });
  });

  it("run stores an error on failure", async () => {
    apiMock.analyzeStockTracker.mockRejectedValue(new Error("boom"));
    const store = useStockTrackerAnalysisStore.getState();
    store.setSelectedSymbols(["600519.SH"]);

    await store.run();
    const s = useStockTrackerAnalysisStore.getState();
    expect(s.loading).toBe(false);
    expect(s.report).toBeNull();
    expect(s.error).toBe("boom");
  });

  it("loadHistory populates the history list", async () => {
    apiMock.getStockTrackerAnalysisHistory.mockResolvedValue({
      status: "ok",
      items: [{ id: "a", summary: "first" }, { id: "b", summary: "second" }],
    });
    await useStockTrackerAnalysisStore.getState().loadHistory();
    expect(useStockTrackerAnalysisStore.getState().history).toHaveLength(2);
  });

  it("loadLatest restores the report and selected id", async () => {
    apiMock.getStockTrackerAnalysis.mockResolvedValue({
      status: "ok",
      report: report(),
      id: "latest-id",
    });
    await useStockTrackerAnalysisStore.getState().loadLatest();
    const s = useStockTrackerAnalysisStore.getState();
    expect(s.report?.summary).toBe("综述");
    expect(s.selectedId).toBe("latest-id");
  });

  it("selectAnalysis fetches a report by id", async () => {
    apiMock.getStockTrackerAnalysisById.mockResolvedValue({
      status: "ok",
      report: report(),
      id: "picked-id",
    });
    await useStockTrackerAnalysisStore.getState().selectAnalysis("picked-id");
    const s = useStockTrackerAnalysisStore.getState();
    expect(s.report?.summary).toBe("综述");
    expect(s.selectedId).toBe("picked-id");
    expect(apiMock.getStockTrackerAnalysisById).toHaveBeenCalledWith("picked-id");
  });
});
