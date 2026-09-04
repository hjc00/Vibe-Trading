import { useEffect, useMemo, useRef, useState } from "react";
import { BarChart3, Maximize2, Minimize2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { useChartLifecycle } from "@/hooks/useChartLifecycle";
import { getChartTheme } from "@/lib/chart-theme";
import { safeGet, safeSet } from "@/lib/storage";
import type { SymbolSnapshot, VolumePoint } from "@/lib/api";
import { ChartCardHeader } from "./ChartCardHeader";

interface VolumeChartCardProps {
  symbol: SymbolSnapshot | null;
  onHide?: () => void;
  collapsed?: boolean;
  onToggle?: () => void;
}

const WIDTH_KEY = "stockTracker.volumeChart.wide";

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

function hasCandles(points: VolumePoint[]): boolean {
  return (
    points.length > 0 &&
    points.every(
      (p) =>
        p.open != null && p.high != null && p.low != null && p.close != null,
    )
  );
}

function fmtPrice(value: number | null | undefined): string {
  return value == null ? "—" : value.toFixed(2);
}

/**
 * 周期价量K线: switchable-window daily candles over matching volume bars, with
 * a window-average volume reference line. Volume bars follow the day's
 * up/down direction. When a snapshot lacks OHLC it degrades to the legacy
 * volume-only bar chart. The card can be widened to two grid columns from a
 * header toggle (preference persisted).
 */
export function VolumeChartCard({ symbol, onHide, collapsed = false, onToggle }: VolumeChartCardProps) {
  const { t } = useTranslation();
  const chartRef = useRef<HTMLDivElement>(null);

  // Default to the two-column layout; remember the user's choice across loads.
  const [wide, setWide] = useState<boolean>(() => safeGet(WIDTH_KEY) !== "0");

  const periodSignals = symbol?.period_signals;
  const periods = periodSignals
    ? Object.keys(periodSignals)
        .map(Number)
        .sort((a, b) => a - b)
    : [];

  // Default the chart to the 20-session window when available.
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

  const toggleWide = () => {
    setWide((prev) => {
      safeSet(WIDTH_KEY, prev ? "0" : "1");
      return !prev;
    });
  };

  const wideLabel = t(
    wide
      ? "stockTracker.volumeChartShrink"
      : "stockTracker.volumeChartExpand",
  );

  useChartLifecycle(
    chartRef,
    () => {
      const theme = getChartTheme();
      if (!chartWindow || chartWindow.length === 0) {
        return { backgroundColor: "transparent" };
      }

      const count = chartWindow.length;
      const dates = chartWindow.map((p) => shortDate(p.trade_date));
      const volumeLabel = t("stockTracker.chartVolume");

      // Price candles only when the whole window carries OHLC; otherwise keep
      // the legacy volume-only bars so stale snapshots still render.
      if (hasCandles(chartWindow)) {
        const candleData = chartWindow.map(
          (p) => [p.open, p.close, p.low, p.high] as [number, number, number, number],
        );
        const barData = chartWindow.map((p) => {
          const up = (p.close ?? 0) >= (p.open ?? 0);
          return {
            value: p.volume ?? 0,
            itemStyle: {
              color: up ? theme.volumeUp : theme.volumeDown,
            },
          };
        });
        const openLabel = t("stockTracker.volumeChartOpen");
        const highLabel = t("stockTracker.volumeChartHigh");
        const lowLabel = t("stockTracker.volumeChartLow");
        const closeLabel = t("stockTracker.volumeChartClose");
        const burstLabel = t("stockTracker.volumeChartBurst");

        return {
          backgroundColor: "transparent",
          tooltip: {
            trigger: "axis" as const,
            axisPointer: { type: "cross" as const },
            backgroundColor: theme.tooltipBg,
            borderColor: theme.tooltipBorder,
            textStyle: { color: theme.tooltipText, fontSize: 11 },
            formatter: (params: unknown) => {
              const arr = Array.isArray(params) ? params : [params];
              const item = arr[0] as { dataIndex?: number } | undefined;
              const idx = Number(item?.dataIndex ?? 0);
              const point = chartWindow[idx];
              const close = point?.close ?? null;
              const open = point?.open ?? null;
              const up = close != null && open != null && close >= open;
              const tone = up ? theme.upColor : theme.downColor;
              const burstTag = point?.is_burst
                ? `<span style="color:${theme.warningColor};margin-left:6px">${burstLabel}</span>`
                : "";
              return `<div style="font-size:12px">
                <div style="margin-bottom:4px;font-weight:500">${shortDate(point?.trade_date)}</div>
                <div>${openLabel}: ${fmtPrice(open)} &nbsp; ${highLabel}: ${fmtPrice(point?.high)}</div>
                <div>${lowLabel}: ${fmtPrice(point?.low)} &nbsp; ${closeLabel}: <span style="color:${tone}"><strong>${fmtPrice(close)}</strong></span></div>
                <div>${volumeLabel}: <strong>${formatVolumeLots(point?.volume)}</strong>${burstTag}</div>
              </div>`;
            },
          },
          grid: [
            { left: 8, right: 8, top: 8, height: "58%", containLabel: true },
            { left: 8, right: 8, top: "72%", height: "20%", containLabel: true },
          ],
          xAxis: [
            {
              type: "category" as const,
              data: dates,
              gridIndex: 0,
              boundaryGap: true,
              axisLine: { show: false },
              axisTick: { show: false },
              axisLabel: { show: false },
            },
            {
              type: "category" as const,
              data: dates,
              gridIndex: 1,
              boundaryGap: true,
              axisLine: { lineStyle: { color: theme.axisColor } },
              axisTick: { show: false },
              axisLabel: { color: theme.textColor, fontSize: 9, hideOverlap: true },
            },
          ],
          yAxis: [
            {
              scale: true,
              gridIndex: 0,
              splitLine: { lineStyle: { color: theme.gridColor } },
              axisLabel: {
                color: theme.textColor,
                fontSize: 9,
                formatter: (value: number) => value.toFixed(2),
              },
            },
            {
              type: "value" as const,
              min: 0,
              gridIndex: 1,
              splitLine: { show: false },
              axisLabel: {
                color: theme.textColor,
                fontSize: 9,
                formatter: (value: number) => formatVolumeLots(value),
              },
            },
          ],
          series: [
            {
              name: "K",
              type: "candlestick" as const,
              data: candleData,
              xAxisIndex: 0,
              yAxisIndex: 0,
              itemStyle: {
                color: theme.upColor,
                color0: theme.downColor,
                borderColor: theme.upColor,
                borderColor0: theme.downColor,
              },
            },
            {
              name: volumeLabel,
              type: "bar" as const,
              data: barData,
              xAxisIndex: 1,
              yAxisIndex: 1,
              barMaxWidth: 14,
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
      }

      const data = chartWindow.map((point, index) => ({
        value: point.volume ?? 0,
        itemStyle: point.is_burst
          ? { color: theme.warningColor }
          : {
              color: index === count - 1 ? theme.infoColor : theme.infoColor + "66",
            },
      }));
      const burstLabel = t("stockTracker.volumeChartBurst");
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
          data: dates,
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
    [chartWindow, period, t, collapsed],
  );

  const hasData = chartWindow != null;

  return (
    <div
      data-testid="volume-chart-card"
      className={cn(
        "rounded-xl border border-border/60 bg-card p-4 shadow-sm",
        wide && "sm:col-span-2 lg:col-span-2",
      )}
    >
      <ChartCardHeader
        title={t("stockTracker.volumeChartTitle")}
        helpText={t("stockTracker.volumeChartExplanation")}
        onHide={onHide}
        collapsed={collapsed}
        onToggle={onToggle}
        actions={
          <button
            type="button"
            onClick={toggleWide}
            aria-label={wideLabel}
            title={wideLabel}
            className="inline-flex items-center justify-center rounded p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
          >
            {wide ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
          </button>
        }
      />
      {collapsed ? null : !hasData ? (
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
          <div className={cn("relative w-full", wide ? "h-[300px]" : "h-[260px]")}>
            <div ref={chartRef} className="absolute inset-0" />
          </div>
        </div>
      )}
    </div>
  );
}
