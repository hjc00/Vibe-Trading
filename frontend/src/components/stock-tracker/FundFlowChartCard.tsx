import { useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { TrendingUp } from "lucide-react";
import { useChartLifecycle } from "@/hooks/useChartLifecycle";
import { getChartTheme } from "@/lib/chart-theme";
import { formatCapitalAmount, formatDataDate } from "@/lib/stockTracker";
import { ChartCardHeader } from "./ChartCardHeader";
import type { SymbolSnapshot } from "@/lib/api";

interface FundFlowChartCardProps {
  symbol: SymbolSnapshot | null;
  onHide?: () => void;
  collapsed?: boolean;
  onToggle?: () => void;
}

export function FundFlowChartCard({ symbol, onHide, collapsed = false, onToggle }: FundFlowChartCardProps) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);

  const data = useMemo(() => {
    const history = symbol?.capital?.fund_flow?.history;
    if (!history || history.length === 0) return null;
    const chronological = [...history].reverse();
    return {
      dates: chronological.map((h) => h.trade_date ?? ""),
      mainNet: chronological.map((h) => h.main_net ?? null),
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
            text: t("stockTracker.fundFlowDataUnavailable"),
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
                  <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${color ?? theme.upColor}"></span>
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
        grid: { left: 50, right: 20, top: 24, bottom: 24 },
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
        visualMap: {
          show: false,
          dimension: 1,
          pieces: [
            { gt: 0, color: theme.upColor },
            { lt: 0, color: theme.downColor },
            { value: 0, color: theme.textColor },
          ],
        },
        series: [
          {
            name: t("stockTracker.mainForceNetInflow"),
            type: "line",
            data: data.mainNet,
            smooth: true,
            symbol: "circle",
            lineStyle: { width: 2 },
            itemStyle: { color: theme.upColor },
            markLine: {
              data: [{ yAxis: 0, lineStyle: { color: theme.axisColor, type: "dashed" } }],
              symbol: "none",
              label: { show: false },
            },
          },
        ],
      };
    },
    [data, t, collapsed],
  );

  const hasData = data != null && data.mainNet.some((v) => v != null);

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <ChartCardHeader
        title={t("stockTracker.fundFlowChartTitle")}
        helpText={t("stockTracker.fundFlowExplanation")}
        onHide={onHide}
        collapsed={collapsed}
        onToggle={onToggle}
        meta={t("stockTracker.dataDate", {
          date: formatDataDate(symbol?.capital?.fund_flow?.trade_date),
        })}
      />
      {collapsed ? null : (
        <div className="relative h-[240px] w-full">
          <div ref={ref} className="absolute inset-0" />
          {!hasData && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-muted-foreground">
              <TrendingUp className="h-8 w-8 opacity-40" />
              <span className="text-xs">{t("stockTracker.fundFlowDataUnavailable")}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
