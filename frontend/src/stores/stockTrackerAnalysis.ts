import { create } from "zustand";
import {
  api,
  type TrackerAnalysisHistoryItem,
  type TrackerAnalyzeReport,
  type TrackerAnalysisFocus,
} from "@/lib/api";

/**
 * Analysis state for the A-share stock tracker.
 *
 * Lives in a module-level store (not component state) so an in-flight analysis
 * survives the user switching tabs: the page unmounts, but the store and the
 * pending API call keep running, and the result lands in the store when it
 * completes. It also holds the analysis history so the user can switch between
 * past reports.
 */
interface StockTrackerAnalysisState {
  open: boolean;
  selectedSymbols: string[];
  focus: TrackerAnalysisFocus;
  userPrompt: string;
  loading: boolean;
  report: TrackerAnalyzeReport | null;
  error: string | null;
  history: TrackerAnalysisHistoryItem[];
  selectedId: string | null;

  setOpen: (open: boolean) => void;
  setSelectedSymbols: (codes: string[]) => void;
  setFocus: (focus: TrackerAnalysisFocus) => void;
  setUserPrompt: (value: string) => void;
  setReport: (report: TrackerAnalyzeReport | null) => void;
  setError: (error: string | null) => void;
  reset: () => void;

  loadLatest: () => Promise<void>;
  loadHistory: () => Promise<void>;
  selectAnalysis: (id: string) => Promise<void>;
  run: () => Promise<void>;
}

export const useStockTrackerAnalysisStore = create<StockTrackerAnalysisState>(
  (set, get) => ({
    open: false,
    selectedSymbols: [],
    focus: "rank_opportunities",
    userPrompt: "",
    loading: false,
    report: null,
    error: null,
    history: [],
    selectedId: null,

    setOpen: (open) => set({ open }),
    setSelectedSymbols: (selectedSymbols) => set({ selectedSymbols }),
    setFocus: (focus) => set({ focus }),
    setUserPrompt: (userPrompt) => set({ userPrompt }),
    setReport: (report) => set({ report }),
    setError: (error) => set({ error }),
    reset: () =>
      set({
        open: false,
        selectedSymbols: [],
        focus: "rank_opportunities",
        userPrompt: "",
        loading: false,
        report: null,
        error: null,
        history: [],
        selectedId: null,
      }),

    loadLatest: async () => {
      try {
        const response = await api.getStockTrackerAnalysis();
        if (response.report) {
          set({ report: response.report, selectedId: response.id ?? null });
        }
      } catch {
        // Analysis is optional; ignore load failures.
      }
    },

    loadHistory: async () => {
      try {
        const response = await api.getStockTrackerAnalysisHistory();
        set({ history: response.items });
      } catch {
        // History is optional; ignore load failures.
      }
    },

    selectAnalysis: async (id) => {
      try {
        const response = await api.getStockTrackerAnalysisById(id);
        set({ report: response.report, selectedId: response.id ?? id });
      } catch (err) {
        set({ error: err instanceof Error ? err.message : String(err) });
      }
    },

    run: async () => {
      const { selectedSymbols, focus, userPrompt } = get();
      if (selectedSymbols.length === 0) return;
      set({ loading: true, error: null, report: null, selectedId: null });
      try {
        const response = await api.analyzeStockTracker({
          symbols: selectedSymbols,
          focus,
          user_prompt: focus === "custom" ? userPrompt : null,
        });
        set({ report: response.report, selectedId: response.id ?? null });
        await get().loadHistory();
      } catch (err) {
        set({ error: err instanceof Error ? err.message : String(err) });
      } finally {
        set({ loading: false });
      }
    },
  }),
);
