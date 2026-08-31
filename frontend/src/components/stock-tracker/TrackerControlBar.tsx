import { useTranslation } from "react-i18next";
import { Loader2, Plus, RefreshCw, Sparkles } from "lucide-react";
import { TrackerConfigPanel } from "./TrackerConfigPanel";
import type { TrackerConfig } from "@/lib/api";

interface TrackerControlBarProps {
  addCode: string;
  onAddCodeChange: (value: string) => void;
  onAdd: () => void;
  addDisabled: boolean;
  settingsConfig: TrackerConfig;
  onSaveConfig: (config: TrackerConfig) => void;
  settingsDisabled: boolean;
  onAnalyze: () => void;
  analyzeDisabled: boolean;
  onRefresh: () => void;
  refreshing: boolean;
}

export function TrackerControlBar({
  addCode,
  onAddCodeChange,
  onAdd,
  addDisabled,
  settingsConfig,
  onSaveConfig,
  settingsDisabled,
  onAnalyze,
  analyzeDisabled,
  onRefresh,
  refreshing,
}: TrackerControlBarProps) {
  const { t } = useTranslation();

  return (
    <section className="flex flex-col gap-3 rounded-xl border border-border/60 bg-card p-3 shadow-sm sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={addCode}
          onChange={(e) => onAddCodeChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              onAdd();
            }
          }}
          disabled={addDisabled}
          placeholder={t("stockTracker.addSymbolPlaceholder")}
          className="w-full rounded-md border bg-background px-3 py-2 text-sm outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-60 sm:w-56"
        />
        <button
          type="button"
          onClick={onAdd}
          disabled={addDisabled || !addCode.trim()}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-primary px-3 py-2 text-xs font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <Plus className="h-3.5 w-3.5" />
          {t("stockTracker.add")}
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <TrackerConfigPanel
          config={settingsConfig}
          onSave={onSaveConfig}
          disabled={settingsDisabled}
        />
        <button
          type="button"
          onClick={onAnalyze}
          disabled={analyzeDisabled}
          className="inline-flex items-center gap-2 rounded-md border border-border/60 bg-background px-4 py-2 text-sm font-medium transition hover:bg-muted disabled:cursor-not-allowed disabled:opacity-70"
        >
          <Sparkles className="h-4 w-4" />
          {t("stockTracker.analyze")}
        </button>
        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing}
          className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-70"
        >
          {refreshing ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="h-4 w-4" />
          )}
          {refreshing ? t("stockTracker.refreshing") : t("stockTracker.refresh")}
        </button>
      </div>
    </section>
  );
}
