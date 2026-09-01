import { useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useChartLifecycle } from "@/hooks/useChartLifecycle";
import { getChartTheme } from "@/lib/chart-theme";
import type { SignalMeta, SymbolSnapshot } from "@/lib/api";

interface TrackerChartsProps {
  symbol: SymbolSnapshot | null;
  signals: SignalMeta[];
}

export function TrackerCharts({ symbol, signals }: TrackerChartsProps) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);

  const visibleSignalNames = useMemo(
    () => new Set(signals.filter((s) => s.show_in_table).map((s) => s.name)),
    [signals],
  );

  const data = useMemo(() => {
    if (!symbol) return null;
    const periods = Object.keys(symbol.period_signals)
      .map((period) => Number(period))
      .sort((a, b) => a - b);
    return {
      periods,
      returns: periods.map((period) => symbol.period_signals[String(period)]?.metrics.return_pct ?? 0),
      signalCounts: periods.map((period) =>
        Object.entries(symbol.period_signals[String(period)]?.signals ?? {}).filter(
          ([signalType, signal]) => visibleSignalNames.has(signalType) && signal?.triggered,
        ).length,
      ),
    };
  }, [symbol, visibleSignalNames]);

  useChartLifecycle(
    ref,
    () => {
      const theme = getChartTheme();
      if (!data) {
        return {
          backgroundColor: "transparent",
          title: {
            text: t("stockTracker.selectSymbol"),
            left: "center",
            textStyle: { color: theme.textColor, fontSize: 14 },
          },
        };
      }

      return {
        backgroundColor: "transparent",
        tooltip: {
          trigger: "axis",
          backgroundColor: theme.tooltipBg,
          borderColor: theme.tooltipBorder,
          textStyle: { color: theme.tooltipText },
        },
        grid: { left: 50, right: 20, top: 30, bottom: 30 },
        xAxis: {
          type: "category",
          data: data.periods.map((period) => `${period}${t("stockTracker.period")}`),
          axisLine: { lineStyle: { color: theme.axisColor } },
          axisLabel: { color: theme.textColor },
        },
        yAxis: {
          type: "value",
          axisLabel: {
            color: theme.textColor,
            formatter: (value: number) => `${(value * 100).toFixed(1)}%`,
          },
          splitLine: { lineStyle: { color: theme.gridColor } },
        },
        series: [
          {
            name: t("stockTracker.return"),
            type: "bar",
            data: data.returns.map((value) => ({
              value,
              itemStyle: {
                color: value >= 0 ? theme.upColor : theme.downColor,
              },
            })),
            barWidth: "40%",
          },
          {
            name: t("stockTracker.todaySignals"),
            type: "line",
            data: data.signalCounts,
            lineStyle: { color: theme.infoColor },
            itemStyle: { color: theme.infoColor },
            yAxisIndex: 0,
            symbol: "circle",
          },
        ],
      };
    },
    [data, t],
  );

  if (!symbol) {
    return (
      <div className="flex h-[300px] items-center justify-center rounded-xl border border-border/60 bg-card text-sm text-muted-foreground">
        {t("stockTracker.selectSymbol")}
      </div>
    );
  }

  return <div ref={ref} className="h-[300px] w-full rounded-xl border border-border/60 bg-card p-2" />;
}
