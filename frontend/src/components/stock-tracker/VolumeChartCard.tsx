import { useEffect, useMemo, useRef, useState } from "react";
import { BarChart3 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { useChartLifecycle } from "@/hooks/useChartLifecycle";
import { getChartTheme } from "@/lib/chart-theme";
import type { SymbolSnapshot } from "@/lib/api";
import { ChartCardHeader } from "./ChartCardHeader";

interface VolumeChartCardProps {
  symbol: SymbolSnapshot | null;
}

/** Compact formatting for A-share lots: 12.3万 / 1.20亿 / 3456. */
function formatVolumeLots(value: number | null | undefined): string {
  if (value === undefined || value === null) return "—";
  if (value >= 1e8) return `${(value / 1e8).toFixed(2)}亿`;
  if (value >= 1e4) return `${(value / 1e4).toFixed(1)}万`;
  return Math.round(value).toLocaleString("en-US");
}

/** Render an ISO date as MM/DD for a compact axis. */
function shortDate(iso: string | null | undefined): string {
  if (!iso) return "";
  return iso.slice(5).replace("-", "/");
}

/**
 * 周期成交量柱状图: switchable-window daily volume bars with a window-average
 * reference line and burst-day highlighting (amber = >= 1.5x the prior
 * 5-session average).
 */
export function VolumeChartCard({ symbol }: VolumeChartCardProps) {
  const { t } = useTranslation();
  const chartRef = useRef<HTMLDivElement>(null);

  const periodSignals = symbol?.period_signals;
  const periods = periodSignals
    ? Object.keys(periodSignals)
        .map(Number)
        .sort((a, b) => a - b)
    : [];

  // Default the bar chart to the 20-session window when available.
  const [period, setPeriod] = useState<number>(20);
  useEffect(() => {
    if (periods.length > 0 && !periods.includes(period)) {
      setPeriod(periods[periods.length - 1]);
    }
  }, [periods, period]);

  const series = useMemo(() => symbol?.volume_series ?? [], [symbol]);
  const chartWindow = useMemo(() => {
    if (series.length === 0) return null;
    const win = period ? series.slice(-period) : series;
    return win.length > 0 ? win : null;
  }, [series, period]);

  const windowAvg = useMemo(() => {
    if (!chartWindow) return null;
    const values = chartWindow
      .map((p) => p.volume)
      .filter((v): v is number => typeof v === "number" && Number.isFinite(v));
    if (values.length === 0) return null;
    return values.reduce((sum, v) => sum + v, 0) / values.length;
  }, [chartWindow]);

  useChartLifecycle(
    chartRef,
    () => {
      const theme = getChartTheme();
      if (!chartWindow || chartWindow.length === 0) {
        return { backgroundColor: "transparent" };
      }
      const count = chartWindow.length;
      const data = chartWindow.map((point, index) => ({
        value: point.volume ?? 0,
        itemStyle: point.is_burst
          ? { color: theme.warningColor }
          : {
              color: index === count - 1 ? theme.infoColor : theme.infoColor + "66",
            },
      }));
      const burstLabel = t("stockTracker.volumeChartBurst");
      const volumeLabel = t("stockTracker.chartVolume");
      return {
        backgroundColor: "transparent",
        tooltip: {
          trigger: "axis" as const,
          backgroundColor: theme.tooltipBg,
          borderColor: theme.tooltipBorder,
          textStyle: { color: theme.tooltipText },
          formatter: (params: unknown) => {
            const arr = Array.isArray(params) ? params : [params];
            const item = arr[0] as { dataIndex?: number } | undefined;
            const idx = Number(item?.dataIndex ?? 0);
            const point = chartWindow[idx];
            const date = shortDate(point?.trade_date);
            const value = point?.volume ?? null;
            const burstTag = point?.is_burst
              ? `<span style="color:${theme.warningColor};margin-left:6px">${burstLabel}</span>`
              : "";
            return `<div style="font-size:12px">
              <div style="margin-bottom:4px;font-weight:500">${date}</div>
              <div>${volumeLabel}: <strong>${formatVolumeLots(value)}</strong>${burstTag}</div>
            </div>`;
          },
        },
        grid: { left: 42, right: 10, top: 8, bottom: 18 },
        xAxis: {
          type: "category",
          data: chartWindow.map((p) => shortDate(p.trade_date)),
          boundaryGap: true,
          axisLine: { lineStyle: { color: theme.axisColor } },
          axisTick: { show: false },
          axisLabel: { color: theme.textColor, fontSize: 9, hideOverlap: true },
        },
        yAxis: {
          type: "value",
          min: 0,
          axisLabel: {
            color: theme.textColor,
            fontSize: 9,
            formatter: (value: number) => formatVolumeLots(value),
          },
          splitLine: { lineStyle: { color: theme.gridColor } },
        },
        series: [
          {
            name: volumeLabel,
            type: "bar",
            data,
            barMaxWidth: 12,
            markLine:
              windowAvg != null
                ? {
                    symbol: "none",
                    silent: true,
                    data: [{ yAxis: windowAvg }],
                    lineStyle: { color: theme.axisColor, type: "dashed" },
                  }
                : undefined,
          },
        ],
      };
    },
    [chartWindow, period, t],
  );

  const hasData = chartWindow != null;

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <ChartCardHeader
        title={t("stockTracker.volumeChartTitle")}
        helpText={t("stockTracker.volumeChartExplanation")}
      />
      {!hasData ? (
        <div className="flex h-[220px] flex-col items-center justify-center gap-2 text-muted-foreground">
          <BarChart3 className="h-8 w-8 opacity-40" />
          <span className="text-xs">{t("stockTracker.volumeNoData")}</span>
        </div>
      ) : (
        <div>
          {periods.length > 0 && (
            <div className="mb-1 flex items-center justify-between px-1">
              <div className="flex items-center gap-0.5">
                {periods.map((p) => (
                  <button
                    key={p}
                    type="button"
                    onClick={() => setPeriod(p)}
                    className={cn(
                      "rounded px-1.5 py-0.5 text-[10px] font-medium transition",
                      p === period
                        ? "bg-primary text-primary-foreground"
                        : "text-muted-foreground hover:bg-muted",
                    )}
                  >
                    {t("stockTracker.volumePeriodUnit", { period: p })}
                  </button>
                ))}
              </div>
              {windowAvg != null && (
                <span className="text-[10px] text-muted-foreground">
                  {t("stockTracker.volumeChartAvg")}:{" "}
                  <span className="font-mono font-medium tabular-nums">
                    {formatVolumeLots(windowAvg)}
                  </span>
                </span>
              )}
            </div>
          )}
          <div className="relative h-[200px] w-full">
            <div ref={chartRef} className="absolute inset-0" />
          </div>
        </div>
      )}
    </div>
  );
}
