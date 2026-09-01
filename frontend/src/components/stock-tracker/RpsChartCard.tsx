import { useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { TrendingUp } from "lucide-react";
import { useChartLifecycle } from "@/hooks/useChartLifecycle";
import { getChartTheme } from "@/lib/chart-theme";
import { formatRps } from "@/lib/stockTracker";
import { ChartCardHeader } from "./ChartCardHeader";
import type { SymbolSnapshot } from "@/lib/api";

interface RpsChartCardProps {
  symbol: SymbolSnapshot | null;
}

export function RpsChartCard({ symbol }: RpsChartCardProps) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);

  const data = useMemo(() => {
    if (!symbol) return null;
    const periods = Object.keys(symbol.period_signals)
      .map((period) => Number(period))
      .sort((a, b) => a - b);
    if (periods.length === 0) return null;
    return {
      periods,
      market: periods.map((period) => symbol.period_signals[String(period)]?.metrics.rps_market ?? null),
      sector: periods.map((period) => symbol.period_signals[String(period)]?.metrics.rps_sector ?? null),
    };
  }, [symbol]);

  useChartLifecycle(
    ref,
    () => {
      const theme = getChartTheme();
      if (!data) {
        return {
          backgroundColor: "transparent",
          title: {
            text: t("stockTracker.noRpsData"),
            left: "center",
            top: "center",
            textStyle: { color: theme.textColor, fontSize: 14 },
          },
        };
      }

      const hasMarket = data.market.some((v) => v != null);
      const hasSector = data.sector.some((v) => v != null);

      if (!hasMarket && !hasSector) {
        return {
          backgroundColor: "transparent",
          title: {
            text: t("stockTracker.noRpsData"),
            left: "center",
            top: "center",
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
          formatter: (params: unknown) => {
            const items = Array.isArray(params) ? params : [];
            const date = String((items[0] as { axisValue?: string } | undefined)?.axisValue ?? "");
            const rows = items
              .filter((item) => (item as { value?: number | null }).value != null)
              .map((item) => {
                const { seriesName, value, color } = item as {
                  seriesName: string;
                  value: number;
                  color?: string;
                };
                return `<div style="display:flex;align-items:center;gap:6px">
                  <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color ?? theme.infoColor}"></span>
                  <span>${seriesName}:</span>
                  <strong>${formatRps(value)}</strong>
                </div>`;
              })
              .join("");
            return `<div style="font-size:12px">
              <div style="margin-bottom:4px;font-weight:500">${date}</div>
              ${rows}
            </div>`;
          },
        },
        legend: {
          data: [
            ...(hasMarket ? [t("stockTracker.rpsMarket")] : []),
            ...(hasSector ? [t("stockTracker.rpsSector")] : []),
          ],
          textStyle: { color: theme.textColor, fontSize: 11 },
          top: 0,
        },
        grid: { left: 40, right: 20, top: 34, bottom: 24 },
        xAxis: {
          type: "category",
          data: data.periods.map((period) => `${period}${t("stockTracker.period")}`),
          axisLine: { lineStyle: { color: theme.axisColor } },
          axisLabel: { color: theme.textColor, fontSize: 10 },
        },
        yAxis: {
          type: "value",
          min: 0,
          max: 100,
          axisLabel: {
            color: theme.textColor,
            fontSize: 10,
            formatter: (value: number) => `${value}`,
          },
          splitLine: { lineStyle: { color: theme.gridColor } },
        },
        series: [
          ...(hasMarket
            ? [
                {
                  name: t("stockTracker.rpsMarket"),
                  type: "line" as const,
                  data: data.market,
                  smooth: true,
                  symbol: "circle",
                  lineStyle: { color: theme.infoColor, width: 2 },
                  itemStyle: { color: theme.infoColor },
                  markLine: {
                    data: [
                      { yAxis: 90, lineStyle: { color: theme.upColor, type: "dashed" }, label: { show: false } },
                      { yAxis: 10, lineStyle: { color: theme.downColor, type: "dashed" }, label: { show: false } },
                    ],
                    symbol: "none",
                  },
                },
              ]
            : []),
          ...(hasSector
            ? [
                {
                  name: t("stockTracker.rpsSector"),
                  type: "line" as const,
                  data: data.sector,
                  smooth: true,
                  symbol: "circle",
                  lineStyle: { color: theme.warningColor, width: 2, type: "dashed" as const },
                  itemStyle: { color: theme.warningColor },
                },
              ]
            : []),
        ],
      };
    },
    [data, t],
  );

  const hasData = data != null && (data.market.some((v) => v != null) || data.sector.some((v) => v != null));

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <ChartCardHeader
        title={t("stockTracker.rpsChartTitle")}
        helpText={t("stockTracker.rpsExplanation")}
      />
      <div className="relative h-[240px] w-full">
        <div ref={ref} className="absolute inset-0" />
        {!hasData && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-muted-foreground">
            <TrendingUp className="h-8 w-8 opacity-40" />
            <span className="text-xs">{t("stockTracker.noRpsData")}</span>
          </div>
        )}
      </div>
    </div>
  );
}
