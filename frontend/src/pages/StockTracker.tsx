import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { TrendingUp } from "lucide-react";
import { api, type SignalMeta, type TrackerConfig, type TrackerSnapshot } from "@/lib/api";
import { normalizeAShareCode } from "@/lib/stockTracker";
import { useStockTrackerAnalysisStore } from "@/stores/stockTrackerAnalysis";
import { Skeleton } from "@/components/common/Skeleton";
import { TrackerControlBar } from "@/components/stock-tracker/TrackerControlBar";
import { TrackerSummary } from "@/components/stock-tracker/TrackerSummary";
import { TrackerTable } from "@/components/stock-tracker/TrackerTable";
import { TrackerCharts } from "@/components/stock-tracker/TrackerCharts";
import { TrackerAnalyzePanel } from "@/components/stock-tracker/TrackerAnalyzePanel";
import { TrackerAnalysisReport } from "@/components/stock-tracker/TrackerAnalysisReport";

const POLL_INTERVAL_MS = 2000;

export function StockTracker() {
  const { t } = useTranslation();
  const [snapshot, setSnapshot] = useState<TrackerSnapshot | null>(null);
  const [config, setConfig] = useState<TrackerConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [addCode, setAddCode] = useState("");
  const [signalMeta, setSignalMeta] = useState<SignalMeta[]>([]);
  const {
    open: analyzeOpen,
    selectedSymbols,
    focus: analysisFocus,
    userPrompt,
    loading: analysisLoading,
    report: analysisReport,
    error: analysisError,
    history: analysisHistory,
    selectedId,
    setOpen: setAnalyzeOpen,
    setSelectedSymbols,
    setFocus: setAnalysisFocus,
    setUserPrompt,
    setError: setAnalysisError,
    run: runAnalysis,
    loadLatest,
    loadHistory,
    selectAnalysis,
  } = useStockTrackerAnalysisStore();

  const loadSnapshot = useCallback(async () => {
    try {
      const response = await api.getStockTrackerSnapshot();
      const snap = response.snapshot;
      if (snap) {
        setSnapshot(snap);
        if (snap.symbols.length > 0) {
          setSelectedCode((prev) => prev ?? snap.symbols[0].code);
        }
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const loadSettings = useCallback(async () => {
    try {
      const settings = await api.getStockTrackerSettings();
      setConfig(settings.config);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const loadSignals = useCallback(async () => {
    try {
      const response = await api.getStockTrackerSignalMeta();
      setSignalMeta(response.signals);
    } catch (err) {
      // Non-fatal: components fall back to hard-wired metadata.
      setSignalMeta([]);
    }
  }, []);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    try {
      await api.refreshStockTracker();
      pollRefreshStatus();
    } catch (err) {
      setRefreshing(false);
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const pollRefreshStatus = useCallback(() => {
    if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    const interval = setInterval(async () => {
      try {
        const status = await api.getStockTrackerRefreshStatus();
        if (!status.refresh.running) {
          clearInterval(interval);
          pollTimerRef.current = null;
          setRefreshing(false);
          await loadSnapshot();
        }
      } catch {
        clearInterval(interval);
        pollTimerRef.current = null;
        setRefreshing(false);
      }
    }, POLL_INTERVAL_MS);
    pollTimerRef.current = interval;
  }, [loadSnapshot]);

  useEffect(() => {
    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
    };
  }, []);

  const handleSaveConfig = useCallback(
    async (newConfig: TrackerConfig) => {
      try {
        await api.updateStockTrackerSettings(newConfig);
        setConfig(newConfig);
        await refresh();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [refresh],
  );

  const handleAddSymbol = useCallback(async () => {
    if (!config) return;
    const normalized = normalizeAShareCode(addCode);
    if (!normalized) {
      setError(t("stockTracker.invalidSymbol"));
      return;
    }
    if (config.watchlist.includes(normalized)) {
      setError(t("stockTracker.alreadyInWatchlist", { code: normalized }));
      return;
    }
    const nextConfig: TrackerConfig = {
      ...config,
      watchlist: [...config.watchlist, normalized],
    };
    setAddCode("");
    setError(null);
    await handleSaveConfig(nextConfig);
  }, [addCode, config, handleSaveConfig, t]);

  const handleRemoveSymbol = useCallback(
    async (code: string) => {
      if (!config) return;
      const nextConfig: TrackerConfig = {
        ...config,
        watchlist: config.watchlist.filter((c) => c !== code),
      };
      if (selectedCode === code) {
        setSelectedCode(null);
      }
      await handleSaveConfig(nextConfig);
    },
    [config, handleSaveConfig, selectedCode],
  );

  const analysisSectionRef = useRef<HTMLElement>(null);

  const openAnalyze = useCallback(() => {
    const codes = snapshot?.symbols.map((s) => s.code) ?? [];
    setSelectedSymbols(codes);
    setAnalysisError(null);
    setAnalyzeOpen(true);
    requestAnimationFrame(() => {
      analysisSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }, [snapshot, setSelectedSymbols, setAnalysisError, setAnalyzeOpen]);

  useEffect(() => {
    let mounted = true;
    (async () => {
      setLoading(true);
      await Promise.all([loadSettings(), loadSnapshot(), loadSignals(), loadLatest(), loadHistory()]);
      if (mounted) setLoading(false);
    })();
    return () => {
      mounted = false;
    };
  }, [loadSettings, loadSnapshot, loadSignals, loadLatest, loadHistory]);

  const selectedSymbol = snapshot?.symbols.find((s) => s.code === selectedCode) ?? null;
  const settingsConfig = useMemo(
    () =>
      config ?? {
        watchlist: [],
        periods: [],
        signals: [],
        thresholds: { volume_spike: 2, rsi_overbought: 70, rsi_oversold: 30, breakout_window: 20 },
      },
    [config],
  );

  return (
    <div className="min-h-screen p-6 lg:p-8">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-6">
        <section className="flex flex-col gap-4 border-b pb-6">
          <div className="space-y-3">
            <div className="inline-flex items-center gap-2 rounded-md border px-2.5 py-1 text-xs font-medium text-muted-foreground">
              <TrendingUp className="h-3.5 w-3.5" />
              {t("stockTracker.badge")}
            </div>
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">{t("stockTracker.title")}</h1>
              <p className="mt-2 max-w-2xl text-sm text-muted-foreground">{t("stockTracker.subtitle")}</p>
            </div>
          </div>
        </section>

        <TrackerControlBar
          addCode={addCode}
          onAddCodeChange={setAddCode}
          onAdd={handleAddSymbol}
          addDisabled={!config || loading || refreshing}
          settingsConfig={settingsConfig}
          onSaveConfig={handleSaveConfig}
          settingsDisabled={loading || refreshing}
          signalMeta={signalMeta}
          onAnalyze={openAnalyze}
          analyzeDisabled={loading || refreshing || !snapshot || snapshot.symbols.length === 0}
          onRefresh={refresh}
          refreshing={refreshing}
        />

        {error ? (
          <div className="rounded-lg border border-danger/30 bg-danger/5 p-4 text-sm text-danger">{error}</div>
        ) : null}

        {loading ? (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-24 rounded-xl" />
              ))}
            </div>
            <Skeleton className="h-64 rounded-xl" />
          </div>
        ) : (
          <>
            <TrackerSummary
              snapshot={snapshot}
              signals={signalMeta}
              loading={refreshing}
            />

            {!snapshot || snapshot.symbols.length === 0 ? (
              <section className="rounded-xl border border-dashed p-12 text-center text-sm text-muted-foreground">
                {t("stockTracker.noSnapshot")}
              </section>
            ) : (
              <section className="grid gap-4 lg:grid-cols-[1fr_380px]">
                <TrackerTable
                  symbols={snapshot.symbols}
                  periods={config?.periods ?? []}
                  signals={signalMeta}
                  selectedCode={selectedCode}
                  onSelectCode={setSelectedCode}
                  onRemoveSymbol={handleRemoveSymbol}
                />
                <div className="flex flex-col gap-4">
                  <TrackerCharts symbol={selectedSymbol} signals={signalMeta} />
                  <SymbolDetail symbol={selectedSymbol} />
                </div>
              </section>
            )}

            {(analyzeOpen || analysisReport || analysisHistory.length > 0) ? (
              <section ref={analysisSectionRef} className="flex flex-col gap-4 scroll-mt-6">
                <div className="flex flex-col gap-3 rounded-xl border border-border/60 bg-card p-4 shadow-sm sm:flex-row sm:items-center sm:justify-between">
                  <div className="flex items-center gap-2">
                    <h2 className="text-sm font-semibold">{t("stockTracker.analyzeTitle")}</h2>
                    {analysisReport ? (
                      <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                        {t("stockTracker.analysisReport")}
                      </span>
                    ) : null}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <label className="text-xs text-muted-foreground">{t("stockTracker.analysisHistory")}</label>
                    {analysisHistory.length === 0 ? (
                      <span className="text-xs text-muted-foreground/70">{t("stockTracker.analysisHistoryEmpty")}</span>
                    ) : (
                      <select
                        value={selectedId ?? ""}
                        onChange={(e) => selectAnalysis(e.target.value)}
                        disabled={analysisLoading}
                        className="max-w-full rounded-md border bg-background px-2 py-1.5 text-xs outline-none focus:border-primary disabled:opacity-60"
                      >
                        {analysisHistory.map((item) => (
                          <option key={item.id} value={item.id}>
                            {formatAnalysisTimestamp(item.generated_at)} — {item.summary.slice(0, 40)}
                          </option>
                        ))}
                      </select>
                    )}
                  </div>
                </div>

                {analyzeOpen ? (
                  <TrackerAnalyzePanel
                    symbols={snapshot?.symbols ?? []}
                    selectedSymbols={selectedSymbols}
                    onSelectedSymbolsChange={setSelectedSymbols}
                    focus={analysisFocus}
                    onFocusChange={setAnalysisFocus}
                    userPrompt={userPrompt}
                    onUserPromptChange={setUserPrompt}
                    loading={analysisLoading}
                    onRun={runAnalysis}
                    onClose={() => setAnalyzeOpen(false)}
                  />
                ) : null}

                {analysisError ? (
                  <div className="rounded-lg border border-danger/30 bg-danger/5 p-4 text-sm text-danger">{analysisError}</div>
                ) : null}

                <TrackerAnalysisReport report={analysisReport} />
              </section>
            ) : null}

            {(snapshot?.unresolved.length || 0) > 0 || (snapshot?.data_gaps.length || 0) > 0 ? (
              <section className="rounded-xl border border-warning/30 bg-warning/5 p-4 text-sm">
                <p className="font-medium text-warning">{t("stockTracker.dataGaps")}</p>
                <ul className="mt-2 list-inside list-disc space-y-1 text-muted-foreground">
                  {snapshot?.unresolved.map((code) => (
                    <li key={code}>{code}: {t("stockTracker.unresolved")}</li>
                  ))}
                  {snapshot?.data_gaps.map((gap, index) => (
                    <li key={index}>{String(gap.code)}: {String(gap.reason)}</li>
                  ))}
                </ul>
              </section>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

function formatAnalysisTimestamp(iso: string | null | undefined): string {
  if (!iso) return "";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleString();
}

function SymbolDetail({ symbol }: { symbol: import("@/lib/api").SymbolSnapshot | null }) {
  const { t } = useTranslation();
  if (!symbol) return null;

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex flex-col">
          <span className="text-sm font-semibold">{symbol.name ?? symbol.code}</span>
          <span className="font-mono text-xs text-muted-foreground">{symbol.code}</span>
        </div>
        <span className="text-xs text-muted-foreground">{symbol.close?.toFixed(2) ?? "—"}</span>
      </div>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <div className="rounded bg-muted/40 p-2">
          <p className="text-muted-foreground">{t("stockTracker.volumeRatio")}</p>
          <p className="font-mono font-medium">{symbol.period_signals["10"]?.metrics.volume_ratio?.toFixed(2) ?? "—"}</p>
        </div>
        <div className="rounded bg-muted/40 p-2">
          <p className="text-muted-foreground">RSI(14)</p>
          <p className="font-mono font-medium">{symbol.period_signals["10"]?.metrics.rsi?.toFixed(1) ?? "—"}</p>
        </div>
        <div className="rounded bg-muted/40 p-2">
          <p className="text-muted-foreground">{t("stockTracker.volatility")}</p>
          <p className="font-mono font-medium">
            {symbol.period_signals["20"]?.metrics.annualized_volatility != null
              ? `${(symbol.period_signals["20"].metrics.annualized_volatility * 100).toFixed(1)}%`
              : "—"}
          </p>
        </div>
        <div className="rounded bg-muted/40 p-2">
          <p className="text-muted-foreground">MA20</p>
          <p className="font-mono font-medium">{symbol.period_signals["10"]?.metrics.ma20?.toFixed(2) ?? "—"}</p>
        </div>
      </div>
    </div>
  );
}
