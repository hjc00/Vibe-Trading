import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { TrendingUp, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { api, type SignalMeta, type TrackerConfig, type TrackerSnapshot } from "@/lib/api";
import {
  ALL_ANALYSIS_INDICATOR_KEYS,
  computePriceChange,
  formatQuoteUpdatedAt,
  formatRps,
  getQualityToneClass,
  getRpsToneClass,
  normalizeAShareCode,
} from "@/lib/stockTracker";
import { useStockTrackerAnalysisStore } from "@/stores/stockTrackerAnalysis";
import { Skeleton } from "@/components/common/Skeleton";
import { TrackerControlBar } from "@/components/stock-tracker/TrackerControlBar";
import { TrackerSummary } from "@/components/stock-tracker/TrackerSummary";
import { TrackerTable } from "@/components/stock-tracker/TrackerTable";
import { TrackerCharts } from "@/components/stock-tracker/TrackerCharts";
import { TrackerAnalyzePanel } from "@/components/stock-tracker/TrackerAnalyzePanel";
import { TrackerAnalysisReport } from "@/components/stock-tracker/TrackerAnalysisReport";
import { MarginChartCard } from "@/components/stock-tracker/MarginChartCard";
import { FundFlowChartCard } from "@/components/stock-tracker/FundFlowChartCard";
import { RpsChartCard } from "@/components/stock-tracker/RpsChartCard";
import { RiskMetricsCard } from "@/components/stock-tracker/RiskMetricsCard";
import { ValuationCard } from "@/components/stock-tracker/ValuationCard";
import { EventTimelineCard } from "@/components/stock-tracker/EventTimelineCard";
import { VolumeCard } from "@/components/stock-tracker/VolumeCard";
import { VolumeChartCard } from "@/components/stock-tracker/VolumeChartCard";
import { ConceptHeatCard } from "@/components/stock-tracker/ConceptHeatCard";
import { ConsensusCard } from "@/components/stock-tracker/ConsensusCard";
import { ChipCard } from "@/components/stock-tracker/ChipCard";
import { FinancialReportCard } from "@/components/stock-tracker/FinancialReportCard";
import { MarketSentimentBar } from "@/components/stock-tracker/MarketSentimentBar";
import { SectorStrengthBoard } from "@/components/stock-tracker/SectorStrengthBoard";
import { TrackerTrackRecord } from "@/components/stock-tracker/TrackerTrackRecord";

const POLL_INTERVAL_MS = 2000;

// Fixed render order for the middle detail cards; the first
// `detail_card_count` are shown, at most three per row.
const DETAIL_CARD_COMPONENTS = [
  VolumeCard,
  VolumeChartCard,
  MarginChartCard,
  FundFlowChartCard,
  RpsChartCard,
  RiskMetricsCard,
  ValuationCard,
  EventTimelineCard,
  ConceptHeatCard,
  ConsensusCard,
  ChipCard,
] as const;

export function StockTracker() {
  const { t } = useTranslation();
  const [snapshot, setSnapshot] = useState<TrackerSnapshot | null>(null);
  const [config, setConfig] = useState<TrackerConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [quotesUpdatedAt, setQuotesUpdatedAt] = useState<string | null>(null);
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [addCode, setAddCode] = useState("");
  const [signalMeta, setSignalMeta] = useState<SignalMeta[]>([]);
  const {
    open: analyzeOpen,
    selectedSymbols,
    userPrompt,
    historyLimit,
    loading: analysisLoading,
    report: analysisReport,
    error: analysisError,
    history: analysisHistory,
    selectedId,
    trackRecord,
    setOpen: setAnalyzeOpen,
    setSelectedSymbols,
    setUserPrompt,
    setHistoryLimit,
    setError: setAnalysisError,
    run: runAnalysis,
    loadLatest,
    loadHistory,
    loadTrackRecord,
    deleteAnalysis,
    selectAnalysis,
  } = useStockTrackerAnalysisStore();

  const handleDeleteAnalysis = () => {
    if (!selectedId) return;
    if (window.confirm(t("stockTracker.deleteAnalysisConfirm"))) {
      void deleteAnalysis(selectedId);
    }
  };

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

  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const quoteTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadQuotes = useCallback(async () => {
    try {
      const response = await api.getStockTrackerQuotes();
      if (response.status !== "ok") return;

      setSnapshot((prev) => {
        if (!prev) return prev;
        const quoteByCode = new Map(response.quotes.map((q) => [q.code, q]));
        const nextSymbols = prev.symbols.map((symbol) => {
          const quote = quoteByCode.get(symbol.code);
          if (!quote || quote.close == null) return symbol;
          return {
            ...symbol,
            close: quote.close,
            prev_close: quote.prev_close ?? symbol.prev_close,
            daily_return: quote.daily_return ?? symbol.daily_return,
          };
        });
        return { ...prev, symbols: nextSymbols };
      });
      setQuotesUpdatedAt(new Date().toISOString());
    } catch {
      // Non-fatal: keep the last successful quote visible.
    }
  }, []);

  const stopQuotePolling = useCallback(() => {
    if (quoteTimerRef.current) {
      clearInterval(quoteTimerRef.current);
      quoteTimerRef.current = null;
    }
  }, []);

  const startQuotePolling = useCallback(() => {
    stopQuotePolling();
    const intervalMs = Math.max(5000, (config?.refresh_interval_seconds ?? 10) * 1000);
    loadQuotes();
    quoteTimerRef.current = setInterval(loadQuotes, intervalMs);
  }, [config?.refresh_interval_seconds, loadQuotes, stopQuotePolling]);

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
          if (status.refresh.error) setError(status.refresh.error);
          startQuotePolling();
        }
      } catch {
        clearInterval(interval);
        pollTimerRef.current = null;
        setRefreshing(false);
        startQuotePolling();
      }
    }, POLL_INTERVAL_MS);
    pollTimerRef.current = interval;
  }, [loadSnapshot, startQuotePolling]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    stopQuotePolling();
    try {
      await api.refreshStockTracker();
      pollRefreshStatus();
    } catch (err) {
      setRefreshing(false);
      setError(err instanceof Error ? err.message : String(err));
      startQuotePolling();
    }
  }, [pollRefreshStatus, startQuotePolling, stopQuotePolling]);

  useEffect(() => {
    return () => {
      if (pollTimerRef.current) clearInterval(pollTimerRef.current);
      stopQuotePolling();
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

  // Persist the AI analysis indicator selection without forcing a snapshot
  // refresh (it only affects prompt composition, not snapshot computation).
  // Update state optimistically so a run right after a toggle uses the new set.
  const handleAnalysisIndicatorsChange = useCallback(
    async (keys: string[]) => {
      if (!config || keys.length === 0) return;
      setConfig((prev) => (prev ? { ...prev, analysis_indicators: keys } : prev));
      setError(null);
      try {
        await api.updateStockTrackerSettings({ analysis_indicators: keys });
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [config],
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
      if (config.watchlist.length <= 1) {
        setError(t("stockTracker.cannotRemoveLastSymbol"));
        return;
      }
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
      await Promise.all([loadSettings(), loadSnapshot(), loadSignals(), loadLatest(), loadHistory(), loadTrackRecord()]);
      if (mounted) setLoading(false);
    })();
    return () => {
      mounted = false;
    };
  }, [loadSettings, loadSnapshot, loadSignals, loadLatest, loadHistory, loadTrackRecord]);

  useEffect(() => {
    if (!config || loading) return undefined;
    startQuotePolling();
    return () => {
      stopQuotePolling();
    };
  }, [config, loading, startQuotePolling, stopQuotePolling]);

  const selectedSymbol = snapshot?.symbols.find((s) => s.code === selectedCode) ?? null;
  const settingsConfig = useMemo(
    () =>
      config ?? {
        watchlist: [],
        periods: [],
        signals: [],
        thresholds: { volume_spike: 2, rsi_overbought: 70, rsi_oversold: 30, breakout_window: 20 },
        refresh_interval_seconds: 10,
        detail_card_count: 9,
        analysis_indicators: [...ALL_ANALYSIS_INDICATOR_KEYS],
      },
    [config],
  );

  const detailCards = useMemo(() => {
    const count = Math.max(
      1,
      Math.min(config?.detail_card_count ?? DETAIL_CARD_COMPONENTS.length, DETAIL_CARD_COMPONENTS.length),
    );
    return DETAIL_CARD_COMPONENTS.slice(0, count);
  }, [config?.detail_card_count]);

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
              <section className="flex flex-col gap-4">
                <MarketSentimentBar sentiment={snapshot.market_sentiment} />

                <TrackerTable
                  symbols={snapshot.symbols}
                  periods={config?.periods ?? []}
                  signals={signalMeta}
                  selectedCode={selectedCode}
                  onSelectCode={setSelectedCode}
                  onRemoveSymbol={handleRemoveSymbol}
                  quotesUpdatedAt={quotesUpdatedAt}
                />

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  {detailCards.map((Card) => (
                    <Card key={Card.name} symbol={selectedSymbol} />
                  ))}
                </div>

                <FinancialReportCard symbol={selectedSymbol} />

                <SectorStrengthBoard
                  sectors={snapshot.sectors}
                  concepts={snapshot.concepts}
                  tradingDate={snapshot.trading_date}
                />

                <div className="grid grid-cols-1 gap-4 lg:grid-cols-[380px_1fr]">
                  <SymbolDetail symbol={selectedSymbol} updatedAt={quotesUpdatedAt} />
                  <TrackerCharts symbol={selectedSymbol} signals={signalMeta} />
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
                      <div className="flex items-center gap-1.5">
                        <select
                          value={selectedId ?? ""}
                          onChange={(e) => selectAnalysis(e.target.value)}
                          disabled={analysisLoading}
                          className="max-w-full rounded-md border bg-background px-2 py-1.5 text-xs outline-none focus:border-primary disabled:opacity-60"
                        >
                          {analysisHistory.map((item) => (
                            <option key={item.id} value={item.id}>
                              {formatAnalysisTimestamp(item.generated_at)} — {(item.summary ?? "").slice(0, 40)}
                            </option>
                          ))}
                        </select>
                        <button
                          type="button"
                          onClick={handleDeleteAnalysis}
                          disabled={!selectedId || analysisLoading}
                          aria-label={t("stockTracker.deleteAnalysis")}
                          title={t("stockTracker.deleteAnalysis")}
                          className="rounded-md p-1.5 text-muted-foreground transition hover:bg-danger/10 hover:text-danger disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                    )}
                  </div>
                </div>

                {analyzeOpen ? (
                  <TrackerAnalyzePanel
                    symbols={snapshot?.symbols ?? []}
                    selectedSymbols={selectedSymbols}
                    onSelectedSymbolsChange={setSelectedSymbols}
                    userPrompt={userPrompt}
                    onUserPromptChange={setUserPrompt}
                    historyLimit={historyLimit}
                    onHistoryLimitChange={setHistoryLimit}
                    analysisIndicators={config?.analysis_indicators}
                    onAnalysisIndicatorsChange={handleAnalysisIndicatorsChange}
                    loading={analysisLoading}
                    onRun={() => runAnalysis(config?.analysis_indicators ?? null)}
                    onClose={() => setAnalyzeOpen(false)}
                  />
                ) : null}

                {analysisError ? (
                  <div className="rounded-lg border border-danger/30 bg-danger/5 p-4 text-sm text-danger">{analysisError}</div>
                ) : null}

                <TrackerAnalysisReport report={analysisReport} />
                <TrackerTrackRecord items={trackRecord ?? []} />
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

function SymbolDetail({
  symbol,
  updatedAt,
}: {
  symbol: import("@/lib/api").SymbolSnapshot | null;
  updatedAt: string | null;
}) {
  const { t } = useTranslation();
  if (!symbol) return null;

  const { changeAmount, dailyReturn } = computePriceChange(
    symbol.close,
    symbol.prev_close,
    symbol.daily_return,
  );

  const baselinePeriod = String(
    Object.keys(symbol.period_signals)
      .map((p) => Number(p))
      .sort((a, b) => a - b)[0] ?? "10",
  );
  const metrics = symbol.period_signals[baselinePeriod]?.metrics;
  const excessReturn =
    metrics?.return_pct != null && metrics?.benchmark_return_pct != null
      ? metrics.return_pct - metrics.benchmark_return_pct
      : null;

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex flex-col">
          <span className="text-sm font-semibold">{symbol.name ?? symbol.code}</span>
          <span className="font-mono text-xs text-muted-foreground">{symbol.code}</span>
          {symbol.sector_board && (
            <span className="text-[10px] text-muted-foreground/80">
              {symbol.sector_board}
              {symbol.sector_strength_rank != null
                ? ` · ${t("stockTracker.sectorStrengthRank")} #${symbol.sector_strength_rank}`
                : ""}
            </span>
          )}
        </div>
        <div className="flex flex-col items-end">
          <span className="font-mono text-sm font-semibold tabular-nums">{symbol.close?.toFixed(2) ?? "—"}</span>
          {(changeAmount !== null || dailyReturn !== null) && (
            <span
              className={cn(
                "text-[10px] font-mono tabular-nums",
                (dailyReturn ?? 0) > 0 && "text-success",
                (dailyReturn ?? 0) < 0 && "text-danger",
                (dailyReturn ?? 0) === 0 && "text-muted-foreground",
              )}
            >
              {changeAmount !== null && (
                <>
                  {changeAmount > 0 ? "+" : ""}
                  {changeAmount.toFixed(2)}
                  {" "}
                </>
              )}
              {dailyReturn !== null && (
                <>
                  ({dailyReturn > 0 ? "+" : ""}
                  {(dailyReturn * 100).toFixed(2)}%)
                </>
              )}
            </span>
          )}
          {symbol.prev_close != null && (
            <span className="text-[10px] text-muted-foreground">
              {t("stockTracker.prevClose")}: {symbol.prev_close.toFixed(2)}
            </span>
          )}
          {updatedAt && (
            <span className="text-[10px] text-muted-foreground">
              {t("stockTracker.updatedAt", { when: formatQuoteUpdatedAt(updatedAt, t) })}
            </span>
          )}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-3">
        <div className="rounded bg-muted/40 p-2">
          <p className="text-muted-foreground">{t("stockTracker.volumeRatio")}</p>
          <p className="font-mono font-medium">{metrics?.volume_ratio?.toFixed(2) ?? "—"}</p>
        </div>
        <div className="rounded bg-muted/40 p-2">
          <p className="text-muted-foreground">RSI(14)</p>
          <p className="font-mono font-medium">{metrics?.rsi?.toFixed(1) ?? "—"}</p>
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
          <p className="text-muted-foreground">{t("stockTracker.rpsMarket")}</p>
          <p className={cn("font-mono font-medium tabular-nums", getRpsToneClass(metrics?.rps_market))}>
            {formatRps(metrics?.rps_market)}
          </p>
        </div>
        <div className="rounded bg-muted/40 p-2">
          <p className="text-muted-foreground">{t("stockTracker.rpsSector")}</p>
          <p className={cn("font-mono font-medium tabular-nums", getRpsToneClass(metrics?.rps_sector))}>
            {formatRps(metrics?.rps_sector)}
          </p>
        </div>
        <div className="rounded bg-muted/40 p-2">
          <p className="text-muted-foreground">{t("stockTracker.excessReturn")}</p>
          <p
            className={cn(
              "font-mono font-medium tabular-nums",
              excessReturn != null && excessReturn > 0 && "text-success",
              excessReturn != null && excessReturn < 0 && "text-danger",
              excessReturn != null && excessReturn === 0 && "text-muted-foreground",
            )}
          >
            {excessReturn !== null
              ? `${excessReturn > 0 ? "+" : ""}${(excessReturn * 100).toFixed(2)}%`
              : "—"}
          </p>
        </div>
        <div className="rounded bg-muted/40 p-2">
          <p className="text-muted-foreground">{t("stockTracker.peTtm")}</p>
          <p className="font-mono font-medium tabular-nums">
            {symbol.valuation?.pe_ttm?.toFixed(1) ?? "—"}
          </p>
        </div>
        <div className="rounded bg-muted/40 p-2">
          <p className="text-muted-foreground">{t("stockTracker.pb")}</p>
          <p className="font-mono font-medium tabular-nums">
            {symbol.valuation?.pb?.toFixed(2) ?? "—"}
          </p>
        </div>
        <div className="rounded bg-muted/40 p-2">
          <p className="text-muted-foreground">{t("stockTracker.peg")}</p>
          <p className="font-mono font-medium tabular-nums">
            {symbol.valuation?.peg?.toFixed(2) ?? "—"}
          </p>
        </div>
        <div className="rounded bg-muted/40 p-2">
          <p className="text-muted-foreground">{t("stockTracker.qualityScore")}</p>
          <p className={cn("font-mono font-medium tabular-nums", getQualityToneClass(symbol.valuation?.fundamental_quality_score))}>
            {symbol.valuation?.fundamental_quality_score?.toFixed(0) ?? "—"}
          </p>
        </div>
      </div>
    </div>
  );
}
