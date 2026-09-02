import { useTranslation } from "react-i18next";
import { Loader2, Sparkles, X } from "lucide-react";
import type { SymbolSnapshot } from "@/lib/api";

interface TrackerAnalyzePanelProps {
  symbols: SymbolSnapshot[];
  selectedSymbols: string[];
  onSelectedSymbolsChange: (codes: string[]) => void;
  userPrompt: string;
  onUserPromptChange: (value: string) => void;
  loading: boolean;
  onRun: () => void;
  onClose: () => void;
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
}: TrackerAnalyzePanelProps) {
  const { t } = useTranslation();

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
