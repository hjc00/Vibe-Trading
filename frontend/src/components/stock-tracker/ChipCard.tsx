import { Users } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import {
  formatChipPct,
  formatMarketCap,
  getChipTrendToneClass,
  getQualityToneClass,
} from "@/lib/stockTracker";
import { ChartCardHeader } from "./ChartCardHeader";
import type { ChipHolderItem, SymbolSnapshot } from "@/lib/api";

interface ChipCardProps {
  symbol: SymbolSnapshot | null;
}

const TREND_LABEL_KEY: Record<string, string> = {
  accumulating: "stockTracker.chipTrendAccumulating",
  distributing: "stockTracker.chipTrendDistributing",
};

/**
 * Chip concentration / institutional movement for one symbol (筹码集中度):
 * shareholder-count trend, average holding value, northbound / fund holdings,
 * and a composite concentration score, with a lightweight holder-count line.
 */
export function ChipCard({ symbol }: ChipCardProps) {
  const { t } = useTranslation();
  const chip = symbol?.chip;
  const hasData =
    chip != null &&
    (chip.holder_count != null || chip.chip_concentration_score != null);

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <ChartCardHeader
        title={t("stockTracker.chipTitle")}
        helpText={t("stockTracker.chipExplanation")}
        meta={
          chip?.source && chip.source !== "unavailable"
            ? t("stockTracker.dataSource", { source: chip.source })
            : undefined
        }
      />
      {!hasData ? (
        <div className="flex h-[240px] flex-col items-center justify-center gap-2 text-muted-foreground">
          <Users className="h-8 w-8 opacity-40" />
          <span className="text-xs">{t("stockTracker.chipNoData")}</span>
          {chip?.error ? (
            <span className="max-w-[220px] truncate text-[10px] text-muted-foreground/70">
              {chip.error}
            </span>
          ) : null}
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              {chip?.holder_trend ? (
                <span
                  className={cn(
                    "rounded-full bg-muted/40 px-2 py-0.5 text-[10px] font-medium",
                    getChipTrendToneClass(chip.holder_trend),
                  )}
                >
                  {t((TREND_LABEL_KEY[chip.holder_trend] ?? chip.holder_trend) as never)}
                </span>
              ) : null}
            </div>
            <span
              className={cn(
                "font-mono text-lg font-semibold tabular-nums",
                getQualityToneClass(chip?.chip_concentration_score),
              )}
            >
              {chip?.chip_concentration_score != null
                ? chip.chip_concentration_score.toFixed(0)
                : "—"}
            </span>
          </div>

          <HolderLine history={chip?.holder_history ?? []} />

          <div className="grid grid-cols-2 gap-2">
            <Box
              label={t("stockTracker.chipHolderChange")}
              value={formatChipPct(chip?.holder_count_change_pct)}
              valueClass={getChipTrendToneClass(
                chip?.holder_count_change_pct != null && chip.holder_count_change_pct < 0
                  ? "accumulating"
                  : chip?.holder_count_change_pct != null
                    ? "distributing"
                    : null,
              )}
            />
            <Box
              label={t("stockTracker.chipAvgHold")}
              value={chip?.avg_hold_amount != null ? formatMarketCap(chip.avg_hold_amount) : "—"}
            />
            <Box
              label={t("stockTracker.chipNorthbound")}
              value={formatChipPct(chip?.northbound_holding_ratio)}
            />
            <Box
              label={t("stockTracker.chipFund")}
              value={formatChipPct(chip?.fund_holding_ratio)}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function Box({
  label,
  value,
  valueClass,
}: {
  label: string;
  value: string;
  valueClass?: string;
}) {
  return (
    <div className="rounded-lg bg-muted/40 p-3">
      <p className="truncate text-[10px] text-muted-foreground">{label}</p>
      <p className={cn("mt-0.5 font-mono text-sm font-semibold tabular-nums", valueClass ?? "text-foreground")}>
        {value}
      </p>
    </div>
  );
}

/** Minimal SVG line of the trailing holder count (green when declining). */
function HolderLine({ history }: { history: ChipHolderItem[] }) {
  const points = history
    .filter((h) => h.holder_count != null)
    .map((h) => h.holder_count as number);
  if (points.length < 2) return null;

  const width = 280;
  const height = 48;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const declining = points[points.length - 1] < points[0];
  const stroke = declining ? "var(--success, #22c55e)" : "var(--danger, #ef4444)";

  const coords = points.map((value, index) => {
    const x = (index / (points.length - 1)) * (width - 4) + 2;
    const y = height - 4 - ((value - min) / range) * (height - 8);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="h-12 w-full rounded-lg bg-muted/40"
      role="img"
      aria-label="holder count trend"
    >
      <polyline
        points={coords.join(" ")}
        fill="none"
        stroke={stroke}
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}
