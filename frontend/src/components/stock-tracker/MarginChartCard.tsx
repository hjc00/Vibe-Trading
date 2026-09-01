import { useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useChartLifecycle } from "@/hooks/useChartLifecycle";
import { getChartTheme } from "@/lib/chart-theme";
import { formatCapitalAmount } from "@/lib/stockTracker";
import type { SymbolSnapshot } from "@/lib/api";

interface MarginChartCardProps {
  symbol: SymbolSnapshot | null;
}

export function MarginChartCard({ symbol }: MarginChartCardProps) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);

  const data = useMemo(() => {
    const history = symbol?.capital?.margin?.history;
    if (!history || history.length === 0) return null;
    const chronological = [...history].reverse();
    return {
      dates: chronological.map((h) => h.trade_date ?? ""),
      financing: chronological.map((h) => h.financing_balance ?? null),
      marginTotal: chronological.map((h) => h.margin_total_balance ?? null),
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
            text: t("stockTracker.noMarginData"),
            left: "center",
            top: "center",
            textStyle: { color: theme.textColor, fontSize: 14 },
          },
        };
      }

      const hasMarginTotal = data.marginTotal.some((v) => v != null);

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
                  <strong>${formatCapitalAmount(value)}</strong>
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
          data: hasMarginTotal
            ? [t("stockTracker.financingBalance"), t("stockTracker.marginTotalBalance")]
            : [t("stockTracker.financingBalance")],
          textStyle: { color: theme.textColor, fontSize: 11 },
          top: 0,
        },
        grid: { left: 50, right: 20, top: 34, bottom: 24 },
        xAxis: {
          type: "category",
          data: data.dates,
          axisLine: { lineStyle: { color: theme.axisColor } },
          axisLabel: { color: theme.textColor, fontSize: 10 },
        },
        yAxis: {
          type: "value",
          axisLabel: {
            color: theme.textColor,
            fontSize: 10,
            formatter: (value: number) => formatCapitalAmount(value),
          },
          splitLine: { lineStyle: { color: theme.gridColor } },
        },
        series: [
          {
            name: t("stockTracker.financingBalance"),
            type: "line",
            data: data.financing,
            smooth: true,
            symbol: "circle",
            lineStyle: { color: theme.upColor, width: 2 },
            itemStyle: { color: theme.upColor },
          },
          ...(hasMarginTotal
            ? [
                {
                  name: t("stockTracker.marginTotalBalance"),
                  type: "line",
                  data: data.marginTotal,
                  smooth: true,
                  symbol: "circle",
                  lineStyle: { color: theme.infoColor, width: 2, type: "dashed" as const },
                  itemStyle: { color: theme.infoColor },
                },
              ]
            : []),
        ],
      };
    },
    [data, t],
  );

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <div className="mb-2 flex items-center justify-between">
        <h3 className="text-sm font-semibold">{t("stockTracker.marginChartTitle")}</h3>
      </div>
      <div ref={ref} className="h-[240px] w-full" />
    </div>
  );
}
