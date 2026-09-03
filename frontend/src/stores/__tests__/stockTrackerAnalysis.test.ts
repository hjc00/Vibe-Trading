import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TrackerAnalyzeReport } from "@/lib/api";
import { useStockTrackerAnalysisStore } from "../stockTrackerAnalysis";

const apiMock = vi.hoisted(() => ({
  analyzeStockTracker: vi.fn(),
  getStockTrackerAnalysis: vi.fn(),
  getStockTrackerAnalysisHistory: vi.fn(),
  getStockTrackerAnalysisById: vi.fn(),
  getStockTrackerTrackRecord: vi.fn(),
  deleteStockTrackerAnalysis: vi.fn(),
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
  apiMock.getStockTrackerTrackRecord.mockReset();
  apiMock.deleteStockTrackerAnalysis.mockReset();
  apiMock.getStockTrackerTrackRecord.mockResolvedValue({ status: "ok", items: [] });
  apiMock.deleteStockTrackerAnalysis.mockResolvedValue({ status: "ok", deleted: "x" });
});

describe("stockTrackerAnalysis store", () => {
  it("has correct defaults", () => {
    const s = useStockTrackerAnalysisStore.getState();
    expect(s.open).toBe(false);
    expect(s.selectedSymbols).toEqual([]);
    expect(s.userPrompt).toBe("");
    expect(s.loading).toBe(false);
    expect(s.report).toBeNull();
    expect(s.error).toBeNull();
    expect(s.history).toEqual([]);
    expect(s.selectedId).toBeNull();
    expect(s.trackRecord).toBeNull();
    expect(s.historyLimit).toBe(5);
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
    apiMock.getStockTrackerTrackRecord.mockResolvedValue({
      status: "ok",
      items: [
        {
          analysis_id: "20260831T101500000000",
          code: "600519.SH",
          action: "buy",
          status: "active",
          target_zone: { low: 1600, high: 1700 },
          current_close: 1500,
        },
      ],
    });
    const store = useStockTrackerAnalysisStore.getState();
    store.setSelectedSymbols(["600519.SH"]);

    const pending = store.run();
    expect(useStockTrackerAnalysisStore.getState().loading).toBe(true);

    await pending;
    const s = useStockTrackerAnalysisStore.getState();
    expect(s.loading).toBe(false);
    expect(s.report?.summary).toBe("综述");
    expect(s.selectedId).toBe("20260831T101500000000");
    expect(s.history).toHaveLength(1);
    expect(s.trackRecord).toHaveLength(1);
    expect(apiMock.analyzeStockTracker).toHaveBeenCalledWith({
      symbols: ["600519.SH"],
      user_prompt: null,
      history_limit: 5,
    });
    expect(apiMock.getStockTrackerTrackRecord).toHaveBeenCalled();
  });

  it("run sends a non-empty extra instruction", async () => {
    apiMock.analyzeStockTracker.mockResolvedValue({
      status: "ok",
      report: report(),
      id: "20260831T101500000000",
    });
    apiMock.getStockTrackerAnalysisHistory.mockResolvedValue({
      status: "ok",
      items: [],
    });
    const store = useStockTrackerAnalysisStore.getState();
    store.setSelectedSymbols(["600519.SH"]);
    store.setUserPrompt("重点看均线多头排列");

    await store.run();
    expect(apiMock.analyzeStockTracker).toHaveBeenCalledWith({
      symbols: ["600519.SH"],
      user_prompt: "重点看均线多头排列",
      history_limit: 5,
    });
  });

  it("run sends a custom history limit", async () => {
    apiMock.analyzeStockTracker.mockResolvedValue({
      status: "ok",
      report: report(),
      id: "20260831T101500000000",
    });
    apiMock.getStockTrackerAnalysisHistory.mockResolvedValue({
      status: "ok",
      items: [],
    });
    const store = useStockTrackerAnalysisStore.getState();
    store.setSelectedSymbols(["600519.SH"]);
    store.setHistoryLimit(0);

    await store.run();
    expect(apiMock.analyzeStockTracker).toHaveBeenCalledWith({
      symbols: ["600519.SH"],
      user_prompt: null,
      history_limit: 0,
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

  it("loadTrackRecord populates the prediction list", async () => {
    apiMock.getStockTrackerTrackRecord.mockResolvedValue({
      status: "ok",
      items: [
        {
          analysis_id: "a",
          code: "600519.SH",
          action: "buy",
          status: "active",
          target_zone: { low: 1600, high: 1700 },
          current_close: 1500,
        },
      ],
    });
    await useStockTrackerAnalysisStore.getState().loadTrackRecord();
    expect(useStockTrackerAnalysisStore.getState().trackRecord).toHaveLength(1);
    expect(useStockTrackerAnalysisStore.getState().trackRecord?.[0].status).toBe("active");
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

  it("deleteAnalysis removes the current report and refreshes history", async () => {
    apiMock.getStockTrackerAnalysis.mockResolvedValue({
      status: "ok",
      report: report(),
      id: "to-delete",
    });
    await useStockTrackerAnalysisStore.getState().loadLatest();
    expect(useStockTrackerAnalysisStore.getState().selectedId).toBe("to-delete");

    apiMock.getStockTrackerAnalysisHistory.mockResolvedValue({
      status: "ok",
      items: [],
    });
    await useStockTrackerAnalysisStore.getState().deleteAnalysis("to-delete");

    expect(apiMock.deleteStockTrackerAnalysis).toHaveBeenCalledWith("to-delete");
    const s = useStockTrackerAnalysisStore.getState();
    expect(s.selectedId).toBeNull();
    expect(s.report).toBeNull();
    expect(s.history).toEqual([]);
  });
});
