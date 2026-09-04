import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, Loader2, Sparkles, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { SymbolSnapshot } from "@/lib/api";
import {
  ANALYSIS_FOCUS_OPTIONS,
  ANALYSIS_INDICATORS,
  ALL_ANALYSIS_INDICATOR_KEYS,
} from "@/lib/stockTracker";

interface TrackerAnalyzePanelProps {
  symbols: SymbolSnapshot[];
  selectedSymbols: string[];
  onSelectedSymbolsChange: (codes: string[]) => void;
  userPrompt: string;
  onUserPromptChange: (value: string) => void;
  loading: boolean;
  onRun: () => void;
  onClose: () => void;
  historyLimit?: number;
  onHistoryLimitChange?: (value: number) => void;
  /** Indicator blocks to feed the LLM; toggling persists via onAnalysisIndicatorsChange. */
  analysisIndicators?: string[];
  onAnalysisIndicatorsChange?: (keys: string[]) => void;
  /** Analysis emphasis preset (balanced|technical); persists via onAnalysisFocusChange. */
  analysisFocus?: string;
  onAnalysisFocusChange?: (focus: string) => void;
}

export function TrackerAnalyzePanel({
  symbols,
  selectedSymbols,
  onSelectedSymbolsChange,
  userPrompt,
  onUserPromptChange,
  loading,
  onRun,
  onClose,
  historyLimit = 5,
  onHistoryLimitChange = () => {},
  analysisIndicators = [...ALL_ANALYSIS_INDICATOR_KEYS],
  onAnalysisIndicatorsChange = () => {},
  analysisFocus = "balanced",
  onAnalysisFocusChange = () => {},
}: TrackerAnalyzePanelProps) {
  const { t } = useTranslation();
  const [indicatorsOpen, setIndicatorsOpen] = useState(true);

  const allCodes = symbols.map((s) => s.code);
  const allSelected = symbols.length > 0 && symbols.every((s) => selectedSymbols.includes(s.code));

  const toggleSymbol = (code: string) => {
    if (selectedSymbols.includes(code)) {
      onSelectedSymbolsChange(selectedSymbols.filter((c) => c !== code));
    } else {
      onSelectedSymbolsChange([...selectedSymbols, code]);
    }
  };

  const selectAll = () => onSelectedSymbolsChange(allCodes);
  const clearAll = () => onSelectedSymbolsChange([]);

  const toggleIndicator = (key: string) => {
    if (analysisIndicators.includes(key)) {
      onAnalysisIndicatorsChange(analysisIndicators.filter((k) => k !== key));
    } else {
      onAnalysisIndicatorsChange([...analysisIndicators, key]);
    }
  };

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between">
        <div className="inline-flex items-center gap-2 text-sm font-semibold">
          <Sparkles className="h-4 w-4" />
          {t("stockTracker.analyzeTitle")}
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
          aria-label={t("stockTracker.cancel")}
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="mb-3 flex items-center justify-between">
        <p className="text-xs text-muted-foreground">{t("stockTracker.selectSymbols")}</p>
        <div className="flex items-center gap-2 text-xs">
          <button
            type="button"
            onClick={selectAll}
            disabled={loading || allSelected}
            className="text-primary transition hover:opacity-80 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {t("stockTracker.selectAll")}
          </button>
          <button
            type="button"
            onClick={clearAll}
            disabled={loading || selectedSymbols.length === 0}
            className="text-primary transition hover:opacity-80 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {t("stockTracker.clear")}
          </button>
        </div>
      </div>

      <div className="mb-4 flex flex-wrap gap-2">
        {symbols.map((symbol) => {
          const selected = selectedSymbols.includes(symbol.code);
          return (
            <button
              key={symbol.code}
              type="button"
              onClick={() => toggleSymbol(symbol.code)}
              disabled={loading}
              className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-60 ${
                selected
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-border/60 bg-background text-muted-foreground hover:border-primary/50"
              }`}
            >
              {symbol.name ?? symbol.code}
              <span className="font-mono text-[10px] text-muted-foreground">{symbol.code}</span>
            </button>
          );
        })}
      </div>

      <div className="mb-3">
        <label className="mb-1 block text-xs text-muted-foreground">
          {t("stockTracker.extraPrompt")}
        </label>
        <textarea
          value={userPrompt}
          onChange={(e) => onUserPromptChange(e.target.value)}
          disabled={loading}
          rows={2}
          placeholder={t("stockTracker.customPromptPlaceholder")}
          className="w-full rounded-md border bg-background px-3 py-2 text-xs outline-none focus:border-primary disabled:opacity-60"
        />
      </div>

      <div className="mb-4 flex items-center gap-2">
        <label className="shrink-0 text-xs text-muted-foreground" htmlFor="tracker-history-limit">
          {t("stockTracker.historyLimitLabel")}
        </label>
        <input
          id="tracker-history-limit"
          type="number"
          min={0}
          max={30}
          value={historyLimit}
          onChange={(e) => {
            const next = Math.max(0, Math.min(30, Math.floor(Number(e.target.value) || 0)));
            onHistoryLimitChange(next);
          }}
          disabled={loading}
          className="w-16 rounded-md border bg-background px-2 py-1 text-xs outline-none focus:border-primary disabled:opacity-60"
        />
        <span className="text-[11px] text-muted-foreground">{t("stockTracker.historyLimitHint")}</span>
      </div>

      <div className="mb-4 rounded-md border border-border/60 p-3">
        <p className="mb-2 text-xs font-medium text-muted-foreground">{t("stockTracker.analysisFocus")}</p>
        <div className="flex flex-wrap gap-1.5">
          {ANALYSIS_FOCUS_OPTIONS.map((opt) => {
            const active = analysisFocus === opt.key;
            return (
              <button
                key={opt.key}
                type="button"
                onClick={() => onAnalysisFocusChange(opt.key)}
                disabled={loading}
                className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-60 ${
                  active
                    ? "border-primary bg-primary/10 text-primary"
                    : "border-border/60 bg-background text-muted-foreground hover:border-primary/50"
                }`}
              >
                {t(opt.labelKey as never)}
              </button>
            );
          })}
        </div>
        <p className="mt-1.5 text-[10px] text-muted-foreground">{t("stockTracker.analysisFocusHint")}</p>
      </div>

      <div className="mb-4 rounded-md border border-border/60 p-3">
        <button
          type="button"
          onClick={() => setIndicatorsOpen((v) => !v)}
          aria-expanded={indicatorsOpen}
          className="flex w-full items-center justify-between gap-2 text-left disabled:cursor-not-allowed disabled:opacity-60"
          disabled={loading}
        >
          <span className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
            {t("stockTracker.analysisIndicators")}
            <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
              {analysisIndicators.length}/{ANALYSIS_INDICATORS.length}
            </span>
          </span>
          <ChevronDown
            className={cn("h-3.5 w-3.5 shrink-0 text-muted-foreground transition", indicatorsOpen && "rotate-180")}
          />
        </button>

        {indicatorsOpen && (
          <div className="mt-2 border-t border-border/60 pt-2">
            <p className="mb-1.5 text-[10px] text-muted-foreground">{t("stockTracker.analysisIndicatorsHint")}</p>
            <div className="grid grid-cols-2 gap-1 sm:grid-cols-3 lg:grid-cols-4">
              {ANALYSIS_INDICATORS.map((indicator) => {
                const checked = analysisIndicators.includes(indicator.key);
                return (
                  <label
                    key={indicator.key}
                    className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-xs transition hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={loading}
                      onChange={() => toggleIndicator(indicator.key)}
                      className="h-4 w-4 accent-primary"
                    />
                    <span className="truncate">{t(indicator.labelKey as never)}</span>
                  </label>
                );
              })}
            </div>
          </div>
        )}
      </div>

      <button
        type="button"
        onClick={onRun}
        disabled={loading || selectedSymbols.length === 0}
        className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-70"
      >
        {loading ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Sparkles className="h-4 w-4" />
        )}
        {loading ? t("stockTracker.analyzing") : t("stockTracker.runAnalysis")}
      </button>
    </div>
  );
}
