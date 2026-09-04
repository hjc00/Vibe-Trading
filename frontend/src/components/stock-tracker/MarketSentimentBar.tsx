import { ChevronDown, EyeOff, Thermometer } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { useCardCollapse } from "@/hooks/useCardCollapse";
import { getSentimentBandClass, getSentimentBandLabelKey } from "@/lib/stockTracker";
import type { MarketSentimentSnapshot } from "@/lib/api";

interface MarketSentimentBarProps {
  sentiment: MarketSentimentSnapshot | null | undefined;
  onHide?: () => void;
}

function formatRatioPct(value: number | null | undefined): string {
  if (value === undefined || value === null) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

/**
 * Whole-market sentiment thermometer (市场情绪温度计): a full-width
 * blue→gray→red temperature bar with the composite score plus the key breadth
 * sub-metrics (limit-up / limit-down / broken-board / ladder / advance-decline).
 */
export function MarketSentimentBar({ sentiment, onHide }: MarketSentimentBarProps) {
  const { t } = useTranslation();
  const hideCard = t("stockTracker.hideCard");
  const collapseLabel = t("stockTracker.sentimentTitle");
  const { collapsed, toggle } = useCardCollapse("market_sentiment");

  if (!sentiment || sentiment.source === "unavailable") {
    return (
      <div className="flex items-center justify-between rounded-xl border border-border/60 bg-card px-4 py-3 shadow-sm">
        <div className="flex items-center gap-2 text-muted-foreground">
          <Thermometer className="h-4 w-4 opacity-60" />
          <span className="text-xs">{t("stockTracker.sentimentNoData")}</span>
        </div>
        {onHide ? (
          <button
            type="button"
            onClick={onHide}
            aria-label={hideCard}
            title={hideCard}
            className="inline-flex items-center justify-center rounded p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
          >
            <EyeOff className="h-4 w-4" />
          </button>
        ) : null}
      </div>
    );
  }

  const score = sentiment.sentiment_score;
  const bandKey = getSentimentBandLabelKey(score);
  const markerLeft = score != null ? Math.min(Math.max(score, 0), 100) : 0;

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold">{t("stockTracker.sentimentTitle")}</h3>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "font-mono text-lg font-semibold tabular-nums",
              getSentimentBandClass(score),
            )}
          >
            {score != null ? score.toFixed(0) : "—"}
          </span>
          {bandKey ? (
            <span className={cn("text-xs font-medium", getSentimentBandClass(score))}>
              {t(bandKey as never)}
            </span>
          ) : null}
          {onHide ? (
            <button
              type="button"
              onClick={onHide}
              aria-label={hideCard}
              title={hideCard}
              className="inline-flex items-center justify-center rounded p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
            >
              <EyeOff className="h-4 w-4" />
            </button>
          ) : null}
          <button
            type="button"
            onClick={toggle}
            aria-expanded={!collapsed}
            aria-label={collapseLabel}
            title={collapseLabel}
            className="inline-flex items-center justify-center rounded p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
          >
            <ChevronDown
              className={cn("h-4 w-4 transition-transform", collapsed && "rotate-180")}
            />
          </button>
        </div>
      </div>

      {!collapsed && (
        <>
          <div className="relative h-2.5 w-full rounded-full bg-gradient-to-r from-info via-muted to-danger">
            <div
              className="absolute -top-0.5 h-3.5 w-1.5 -translate-x-1/2 rounded-full bg-foreground"
              style={{ left: `${markerLeft}%` }}
              aria-hidden
            />
          </div>

          <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-[10px] sm:grid-cols-3 lg:grid-cols-6">
            <Metric
              label={t("stockTracker.sentimentLimitUp")}
              value={sentiment.limit_up_count != null ? String(sentiment.limit_up_count) : "—"}
              tone="text-success"
            />
            <Metric
              label={t("stockTracker.sentimentLimitDown")}
              value={sentiment.limit_down_count != null ? String(sentiment.limit_down_count) : "—"}
              tone="text-danger"
            />
            <Metric
              label={t("stockTracker.sentimentBrokenRatio")}
              value={formatRatioPct(sentiment.broken_ratio)}
              tone={sentiment.broken_ratio != null && sentiment.broken_ratio > 0.3 ? "text-danger" : undefined}
            />
            <Metric
              label={t("stockTracker.sentimentMaxHeight")}
              value={sentiment.max_board_height != null ? String(sentiment.max_board_height) : "—"}
            />
            <Metric
              label={t("stockTracker.sentimentUpDown")}
              value={
                sentiment.up_count != null || sentiment.down_count != null
                  ? `${sentiment.up_count ?? "—"}/${sentiment.down_count ?? "—"}`
                  : "—"
              }
            />
            <Metric
              label={t("stockTracker.sentimentPrevPerf")}
              value={formatRatioPct(sentiment.prev_limit_up_perf)}
            />
          </div>
        </>
      )}
    </div>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-muted-foreground">{label}</span>
      <span className={cn("font-mono text-xs font-medium tabular-nums", tone ?? "text-foreground")}>
        {value}
      </span>
    </div>
  );
}
