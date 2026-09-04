import { Activity } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getChangeToneClass } from "@/lib/stockTracker";
import type { SymbolSnapshot } from "@/lib/api";
import { ChartCardHeader } from "./ChartCardHeader";

interface VolumeCardProps {
  symbol: SymbolSnapshot | null;
  onHide?: () => void;
  collapsed?: boolean;
  onToggle?: () => void;
}

/** Compact formatting for A-share lots: 12.3万 / 1.20亿 / 3456. */
function formatVolumeLots(value: number | null | undefined): string {
  if (value === undefined || value === null) return "—";
  if (value >= 1e8) return `${(value / 1e8).toFixed(2)}亿`;
  if (value >= 1e4) return `${(value / 1e4).toFixed(1)}万`;
  return Math.round(value).toLocaleString("en-US");
}

/** Format a volume multiple (e.g. 1.15) with an x suffix. */
function formatVolumeRatio(value: number | null | undefined): string {
  if (value === undefined || value === null) return "—";
  return `${value.toFixed(2)}x`;
}

/** Format a period-to-prior volume ratio as a percentage of the prior window. */
function formatRatioPercent(value: number | null | undefined): string {
  if (value === undefined || value === null) return "—";
  return `${Math.round(value * 100)}%`;
}

/**
 * 量能 overview: today's traded volume versus the 5/20-day averages, plus a
 * per-period energy comparison (window avg volume vs the prior equal window,
 * and how many 放量 bursts occurred inside the window).
 */
export function VolumeCard({ symbol, onHide, collapsed = false, onToggle }: VolumeCardProps) {
  const { t } = useTranslation();
  const periodSignals = symbol?.period_signals;
  const periods = periodSignals
    ? Object.keys(periodSignals)
        .map(Number)
        .sort((a, b) => a - b)
    : [];

  const metrics5 = periodSignals?.["5"]?.metrics;
  const metrics20 = periodSignals?.["20"]?.metrics;
  const todayVolume = symbol?.volume ?? null;
  const avgVolume5 = metrics5?.avg_volume ?? null;
  const avgVolume20 = metrics20?.avg_volume ?? symbol?.avg_volume_20 ?? null;
  const ratioVs5 =
    todayVolume != null && avgVolume5 != null && avgVolume5 > 0
      ? todayVolume / avgVolume5
      : null;

  const hasPeriodRow = periods.some((p) => {
    const m = periodSignals?.[String(p)]?.metrics;
    return m?.avg_volume != null || m?.volume_ratio != null || m?.volume_expansion_days != null;
  });
  const hasData =
    (todayVolume != null && avgVolume5 != null) ||
    (todayVolume != null && avgVolume20 != null) ||
    hasPeriodRow;

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <ChartCardHeader
        title={t("stockTracker.volumeTitle")}
        helpText={t("stockTracker.volumeExplanation")}
        onHide={onHide}
        collapsed={collapsed}
        onToggle={onToggle}
      />
      {collapsed ? null : !hasData ? (
        <div className="flex h-[220px] flex-col items-center justify-center gap-2 text-muted-foreground">
          <Activity className="h-8 w-8 opacity-40" />
          <span className="text-xs">{t("stockTracker.volumeNoData")}</span>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-2 gap-2">
            <Box
              label={t("stockTracker.volumeToday")}
              value={formatVolumeLots(todayVolume)}
            />
            <Box
              label={t("stockTracker.volumeRatioVs5")}
              value={formatVolumeRatio(ratioVs5)}
              highlight={ratioVs5 != null}
              valueClass={getChangeToneClass(ratioVs5 != null ? ratioVs5 - 1 : null)}
            />
            <Box
              label={t("stockTracker.volumeAvg5")}
              value={formatVolumeLots(avgVolume5)}
            />
            <Box
              label={t("stockTracker.volumeAvg20")}
              value={formatVolumeLots(avgVolume20)}
            />
          </div>

          {periods.length > 0 && (
            <div className="rounded-lg bg-muted/40 p-2">
              <div className="grid grid-cols-[34px_1fr_1fr_1fr] gap-2 px-1 pb-1 text-[10px] text-muted-foreground">
                <span>{t("stockTracker.period")}</span>
                <span className="text-right">{t("stockTracker.volumePeriodAvg")}</span>
                <span className="text-right">{t("stockTracker.volumePeriodChange")}</span>
                <span className="text-right">{t("stockTracker.volumeExpansionDays")}</span>
              </div>
              {periods.map((p) => {
                const m = periodSignals?.[String(p)]?.metrics;
                const change = m?.volume_ratio ?? null;
                const expansionDays = m?.volume_expansion_days ?? null;
                const expansionRatio = m?.volume_expansion_ratio ?? null;
                return (
                  <div
                    key={p}
                    className="grid grid-cols-[34px_1fr_1fr_1fr] items-center gap-2 border-t border-border/50 px-1 py-1 font-mono text-[11px] tabular-nums"
                  >
                    <span className="text-muted-foreground">
                      {t("stockTracker.volumePeriodUnit", { period: p })}
                    </span>
                    <span className="text-right">{formatVolumeLots(m?.avg_volume)}</span>
                    <span
                      className={cn(
                        "text-right",
                        getChangeToneClass(change != null ? change - 1 : null),
                      )}
                    >
                      {formatRatioPercent(change)}
                    </span>
                    <span className="text-right">
                      {expansionDays != null && expansionRatio != null ? (
                        <span>
                          {expansionDays}/{m?.sessions ?? "—"}
                          <span className="ml-1 text-muted-foreground">
                            {(expansionRatio * 100).toFixed(0)}%
                          </span>
                        </span>
                      ) : (
                        "—"
                      )}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Box({
  label,
  value,
  valueClass,
  highlight,
}: {
  label: string;
  value: string;
  valueClass?: string;
  highlight?: boolean;
}) {
  return (
    <div className={cn("rounded-lg p-3", highlight ? "bg-primary/10" : "bg-muted/40")}>
      <p className="truncate text-[10px] text-muted-foreground">{label}</p>
      <p className={cn("mt-0.5 font-mono text-sm font-semibold tabular-nums", valueClass ?? "text-foreground")}>
        {value}
      </p>
    </div>
  );
}
