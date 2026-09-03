import { create } from "zustand";
import {
  api,
  type TrackerAnalysisHistoryItem,
  type TrackerAnalyzeReport,
  type TrackerTrackRecordItem,
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
  userPrompt: string;
  // How many of the model's most recent per-symbol records to reference (0 = none).
  historyLimit: number;
  loading: boolean;
  report: TrackerAnalyzeReport | null;
  error: string | null;
  history: TrackerAnalysisHistoryItem[];
  selectedId: string | null;
  trackRecord: TrackerTrackRecordItem[] | null;

  setOpen: (open: boolean) => void;
  setSelectedSymbols: (codes: string[]) => void;
  setUserPrompt: (value: string) => void;
  setHistoryLimit: (value: number) => void;
  setReport: (report: TrackerAnalyzeReport | null) => void;
  setError: (error: string | null) => void;
  reset: () => void;

  loadLatest: () => Promise<void>;
  loadHistory: () => Promise<void>;
  loadTrackRecord: () => Promise<void>;
  selectAnalysis: (id: string) => Promise<void>;
  deleteAnalysis: (id: string) => Promise<void>;
  run: () => Promise<void>;
}

export const useStockTrackerAnalysisStore = create<StockTrackerAnalysisState>(
  (set, get) => ({
    open: false,
    selectedSymbols: [],
    userPrompt: "",
    historyLimit: 5,
    loading: false,
    report: null,
    error: null,
    history: [],
    selectedId: null,
    trackRecord: null,

    setOpen: (open) => set({ open }),
    setSelectedSymbols: (selectedSymbols) => set({ selectedSymbols }),
    setUserPrompt: (userPrompt) => set({ userPrompt }),
    setHistoryLimit: (historyLimit) => set({ historyLimit }),
    setReport: (report) => set({ report }),
    setError: (error) => set({ error }),
    reset: () =>
      set({
        open: false,
        selectedSymbols: [],
        userPrompt: "",
        historyLimit: 5,
        loading: false,
        report: null,
        error: null,
        history: [],
        selectedId: null,
        trackRecord: null,
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

    loadTrackRecord: async () => {
      try {
        const response = await api.getStockTrackerTrackRecord();
        set({ trackRecord: response.items });
      } catch {
        // Track record is optional; ignore load failures.
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

    deleteAnalysis: async (id) => {
      try {
        await api.deleteStockTrackerAnalysis(id);
        const deletingCurrent = get().selectedId === id;
        if (deletingCurrent) {
          set({ report: null, selectedId: null });
        }
        await Promise.all([get().loadHistory(), get().loadTrackRecord()]);
      } catch (err) {
        set({ error: err instanceof Error ? err.message : String(err) });
      }
    },

    run: async () => {
      const { selectedSymbols, userPrompt, historyLimit } = get();
      if (selectedSymbols.length === 0) return;
      set({ loading: true, error: null, report: null, selectedId: null });
      try {
        const response = await api.analyzeStockTracker({
          symbols: selectedSymbols,
          user_prompt: userPrompt.trim() ? userPrompt.trim() : null,
          history_limit: historyLimit,
        });
        set({ report: response.report, selectedId: response.id ?? null });
        await Promise.all([get().loadHistory(), get().loadTrackRecord()]);
      } catch (err) {
        set({ error: err instanceof Error ? err.message : String(err) });
      } finally {
        set({ loading: false });
      }
    },
  }),
);
