import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { TrendingUp } from "lucide-react";
import { useChartLifecycle } from "@/hooks/useChartLifecycle";
import { useCardCollapse } from "@/hooks/useCardCollapse";
import { getChartTheme } from "@/lib/chart-theme";
import { cn } from "@/lib/utils";
import { formatDataDate } from "@/lib/stockTracker";
import { ChartCardHeader } from "./ChartCardHeader";
import type { IndicatorBar, SymbolSnapshot } from "@/lib/api";

interface IndicatorChartCardProps {
  symbol: SymbolSnapshot | null;
}

type NullableSeries = (number | null)[];

/** Detect up/down crosses between two aligned series, returning bar indices. */
function computeCrosses(fast: NullableSeries, slow: NullableSeries): { golden: number[]; death: number[] } {
  const golden: number[] = [];
  const death: number[] = [];
  for (let i = 1; i < fast.length; i++) {
    const pf = fast[i - 1];
    const cf = fast[i];
    const ps = slow[i - 1];
    const cs = slow[i];
    if (pf == null || cf == null || ps == null || cs == null) continue;
    if (cf > cs && pf <= ps) golden.push(i);
    if (cf < cs && pf >= ps) death.push(i);
  }
  return { golden, death };
}

function buildMarkPoints(indices: number[], values: NullableSeries, color: string, label: string) {
  return indices
    .filter((i) => values[i] != null)
    .map((i) => ({
      coord: [i, values[i] as number],
      value: label,
      symbol: "pin" as const,
      symbolSize: 30,
      itemStyle: { color },
      label: { show: false },
    }));
}

export function IndicatorChartCard({ symbol }: IndicatorChartCardProps) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  const [showBoll, setShowBoll] = useState(true);
  const [showMa, setShowMa] = useState(true);
  const [showMacd, setShowMacd] = useState(true);
  const [showKdj, setShowKdj] = useState(true);
  const { collapsed, toggle } = useCardCollapse("indicator");

  const data = useMemo(() => {
    const bars = symbol?.indicators?.bars ?? [];
    if (bars.length === 0) return null;
    const col = (key: keyof IndicatorBar): NullableSeries => bars.map((b) => b[key] as number | null | undefined ?? null);
    const dif = col("dif");
    const dea = col("dea");
    const k = col("k");
    const d = col("d");
    return {
      bars,
      dates: bars.map((b) => (b.trade_date ?? "").slice(5)),
      ohlc: bars.map((b) => [b.open ?? 0, b.close ?? 0, b.low ?? 0, b.high ?? 0] as [number, number, number, number]),
      volume: col("volume"),
      ma5: col("ma5"),
      ma10: col("ma10"),
      ma20: col("ma20"),
      ma60: col("ma60"),
      dif,
      dea,
      hist: col("macd_hist"),
      k,
      d,
      j: col("j"),
      bbUpper: col("bb_upper"),
      bbMid: col("bb_mid"),
      bbLower: col("bb_lower"),
      pctB: col("pct_b"),
      macdCross: computeCrosses(dif, dea),
      kdjCross: computeCrosses(k, d),
      marks: symbol?.indicators?.divergence_marks ?? [],
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
            text: t("stockTracker.noIndicatorData"),
            left: "center",
            top: "center",
            textStyle: { color: theme.textColor, fontSize: 14 },
          },
        };
      }

      const maColors = ["#f59e0b", "#8b5cf6", "#3b82f6", "#14b8a6"];
      const bollColor = "#a78bfa";
      const macdDiffColor = "#e2e8f0";
      const macdDeaColor = "#f59e0b";

      // Candlestick values carrying a %B breakout outline where relevant.
      const candleData = data.ohlc.map((v, i) => {
        const pctB = data.pctB[i];
        if (pctB != null && (pctB > 1 || pctB < 0)) {
          return {
            value: v,
            itemStyle: {
              color: pctB > 1 ? theme.upColor : theme.downColor,
              color0: pctB > 1 ? theme.upColor : theme.downColor,
              borderColor: pctB > 1 ? theme.upColor : theme.downColor,
              borderColor0: pctB > 1 ? theme.upColor : theme.downColor,
              borderWidth: 1.5,
            },
          };
        }
        return v;
      });

      // Volume bars colored by up/down day.
      const volumeData = data.volume.map((v, i) => {
        const bar = data.bars[i];
        const up = (bar.close ?? 0) >= (bar.open ?? 0);
        return {
          value: v,
          itemStyle: { color: up ? theme.volumeUp : theme.volumeDown },
        };
      });

      const macdHistData = data.hist.map((v) => ({
        value: v,
        itemStyle: { color: (v ?? 0) >= 0 ? theme.upColor : theme.downColor },
      }));

      // Divergence annotations: join the two swing points with a dashed line and
      // tag the recent swing with a "顶背离"/"底背离" pin.
      const priceDivergenceLines: { coord: [number, number] }[][] = [];
      const priceDivergencePoints: { coord: [number, number]; name: string }[] = [];
      const difDivergenceLines: { coord: [number, number] }[][] = [];
      for (const m of data.marks) {
        const pa = m.price_lo_idx;
        const pb = m.price_hi_idx;
        if (pa != null && pb != null && pa < data.bars.length && pb < data.bars.length) {
          const useHigh = m.kind === "top";
          const ya = useHigh ? data.bars[pa].high : data.bars[pa].low;
          const yb = useHigh ? data.bars[pb].high : data.bars[pb].low;
          if (ya != null && yb != null) {
            priceDivergenceLines.push([{ coord: [pa, ya] }, { coord: [pb, yb] }]);
            priceDivergencePoints.push({
              coord: [pb, yb],
              name: m.kind === "top" ? t("stockTracker.divergenceTop") : t("stockTracker.divergenceBottom"),
            });
          }
        }
        const da = m.dif_lo_idx;
        const db = m.dif_hi_idx;
        if (da != null && db != null && da < data.dif.length && db < data.dif.length) {
          const ya = data.dif[da];
          const yb = data.dif[db];
          if (ya != null && yb != null) {
            difDivergenceLines.push([{ coord: [da, ya] }, { coord: [db, yb] }]);
          }
        }
      }

      const macdGoldenPoints = buildMarkPoints(data.macdCross.golden, data.dif, theme.upColor, "金叉");
      const macdDeathPoints = buildMarkPoints(data.macdCross.death, data.dif, theme.downColor, "死叉");
      const kdjGoldenPoints = buildMarkPoints(data.kdjCross.golden, data.k, theme.upColor, "金叉");
      const kdjDeathPoints = buildMarkPoints(data.kdjCross.death, data.k, theme.downColor, "死叉");

      const xAxes = [0, 1, 2, 3].map((gridIndex) => ({
        type: "category" as const,
        gridIndex,
        data: data.dates,
        boundaryGap: true,
        axisLine: { lineStyle: { color: theme.axisColor } },
        axisTick: { show: false },
        axisLabel: {
          show: gridIndex === 3,
          color: theme.textColor,
          fontSize: 10,
        },
        splitLine: { show: false },
      }));

      const valueAxis = (gridIndex: number) => ({
        type: "value" as const,
        gridIndex,
        scale: true,
        axisLabel: { color: theme.textColor, fontSize: 10 },
        splitLine: { lineStyle: { color: theme.gridColor } },
        axisLine: { show: false },
      });

      const series = [
        {
          name: t("stockTracker.price"),
          type: "candlestick",
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: candleData,
          itemStyle: {
            color: theme.upColor,
            color0: theme.downColor,
            borderColor: theme.upColor,
            borderColor0: theme.downColor,
          },
          markLine: {
            symbol: ["none", "none"],
            lineStyle: { color: theme.warningColor, type: "dashed", width: 1 },
            label: { show: false },
            data: priceDivergenceLines,
          },
          markPoint: {
            symbol: "circle",
            symbolSize: 6,
            itemStyle: { color: theme.warningColor },
            label: { color: theme.warningColor, fontSize: 10, formatter: "{b}", position: "top" },
            data: priceDivergencePoints,
          },
        },
        ...(showMa
          ? [
              { name: "MA5", type: "line", xAxisIndex: 0, yAxisIndex: 0, data: data.ma5, symbol: "none", lineStyle: { width: 1, color: maColors[0] }, itemStyle: { color: maColors[0] } },
              { name: "MA10", type: "line", xAxisIndex: 0, yAxisIndex: 0, data: data.ma10, symbol: "none", lineStyle: { width: 1, color: maColors[1] }, itemStyle: { color: maColors[1] } },
              { name: "MA20", type: "line", xAxisIndex: 0, yAxisIndex: 0, data: data.ma20, symbol: "none", lineStyle: { width: 1, color: maColors[2] }, itemStyle: { color: maColors[2] } },
              { name: "MA60", type: "line", xAxisIndex: 0, yAxisIndex: 0, data: data.ma60, symbol: "none", lineStyle: { width: 1, color: maColors[3] }, itemStyle: { color: maColors[3] } },
            ]
          : []),
        ...(showBoll
          ? [
              { name: "BOLL上", type: "line", xAxisIndex: 0, yAxisIndex: 0, data: data.bbUpper, symbol: "none", lineStyle: { width: 1, color: bollColor, type: "dashed" }, itemStyle: { color: bollColor } },
              { name: "BOLL中", type: "line", xAxisIndex: 0, yAxisIndex: 0, data: data.bbMid, symbol: "none", lineStyle: { width: 1, color: bollColor }, itemStyle: { color: bollColor } },
              { name: "BOLL下", type: "line", xAxisIndex: 0, yAxisIndex: 0, data: data.bbLower, symbol: "none", lineStyle: { width: 1, color: bollColor, type: "dashed" }, itemStyle: { color: bollColor } },
            ]
          : []),
        { name: t("stockTracker.chartVolume"), type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: volumeData, barWidth: "60%" },
        ...(showMacd
          ? [
              { name: "MACD", type: "bar", xAxisIndex: 2, yAxisIndex: 2, data: macdHistData, barWidth: "60%" },
              {
                name: "DIF",
                type: "line",
                xAxisIndex: 2,
                yAxisIndex: 2,
                data: data.dif,
                symbol: "none",
                lineStyle: { width: 1, color: macdDiffColor },
                itemStyle: { color: macdDiffColor },
                markPoint: { data: [...macdGoldenPoints, ...macdDeathPoints] },
                markLine: {
                  symbol: ["none", "none"],
                  lineStyle: { color: theme.warningColor, type: "dashed", width: 1 },
                  label: { show: false },
                  data: difDivergenceLines,
                },
              },
              { name: "DEA", type: "line", xAxisIndex: 2, yAxisIndex: 2, data: data.dea, symbol: "none", lineStyle: { width: 1, color: macdDeaColor }, itemStyle: { color: macdDeaColor } },
            ]
          : []),
        ...(showKdj
          ? [
              {
                name: "K",
                type: "line",
                xAxisIndex: 3,
                yAxisIndex: 3,
                data: data.k,
                symbol: "none",
                lineStyle: { width: 1, color: maColors[2] },
                itemStyle: { color: maColors[2] },
                markPoint: { data: [...kdjGoldenPoints, ...kdjDeathPoints] },
              },
              { name: "D", type: "line", xAxisIndex: 3, yAxisIndex: 3, data: data.d, symbol: "none", lineStyle: { width: 1, color: maColors[0] }, itemStyle: { color: maColors[0] } },
              { name: "J", type: "line", xAxisIndex: 3, yAxisIndex: 3, data: data.j, symbol: "none", lineStyle: { width: 1, color: maColors[1] }, itemStyle: { color: maColors[1] } },
            ]
          : []),
      ];

      return {
        backgroundColor: "transparent",
        animation: false,
        axisPointer: { link: [{ xAxisIndex: "all" }] },
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "cross" },
          backgroundColor: theme.tooltipBg,
          borderColor: theme.tooltipBorder,
          textStyle: { color: theme.tooltipText },
          formatter: (params: unknown) => {
            const items = Array.isArray(params) ? (params as { dataIndex?: number }[]) : [];
            const idx = items[0]?.dataIndex;
            if (idx == null) return "";
            const bar = data.bars[idx];
            if (!bar) return "";
            const pct = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(2));
            return `<div style="font-size:12px;line-height:1.7">
              <div style="margin-bottom:4px;font-weight:500">${bar.trade_date ?? ""}</div>
              <div>开 ${pct(bar.open)}　高 ${pct(bar.high)}　低 ${pct(bar.low)}　收 ${pct(bar.close)}</div>
              <div>量 ${pct(bar.volume)}</div>
              <div>DIF ${pct(bar.dif)}　DEA ${pct(bar.dea)}　柱 ${pct(bar.macd_hist)}</div>
              <div>K ${pct(bar.k)}　D ${pct(bar.d)}　J ${pct(bar.j)}</div>
              <div>%B ${pct(bar.pct_b)}　带宽 ${pct(bar.bandwidth)}</div>
            </div>`;
          },
        },
        legend: {
          data: ["MA5", "MA10", "MA20", "MA60", "BOLL上", "BOLL中", "BOLL下", "DIF", "DEA", "K", "D", "J"].filter(
            (name) => {
              if (name.startsWith("MA") && !showMa) return false;
              if (name.startsWith("BOLL") && !showBoll) return false;
              if ((name === "DIF" || name === "DEA") && !showMacd) return false;
              if ((name === "K" || name === "D" || name === "J") && !showKdj) return false;
              return true;
            },
          ),
          textStyle: { color: theme.textColor, fontSize: 10 },
          top: 0,
        },
        grid: [
          { left: 52, right: 18, top: 32, height: "40%" },
          { left: 52, right: 18, top: "50%", height: "9%" },
          { left: 52, right: 18, top: "62%", height: "14%" },
          { left: 52, right: 18, top: "79%", height: "12%" },
        ],
        xAxis: xAxes,
        yAxis: [valueAxis(0), valueAxis(1), valueAxis(2), valueAxis(3)],
        dataZoom: [
          { type: "inside", xAxisIndex: [0, 1, 2, 3], start: 0, end: 100 },
          { type: "slider", xAxisIndex: [0, 1, 2, 3], bottom: 4, height: 18, start: 0, end: 100 },
        ],
        series,
      };
    },
    [data, t, showBoll, showMa, showMacd, showKdj, collapsed],
  );

  const hasData = data != null;
  const lastDate = data?.bars.length ? data.bars[data.bars.length - 1].trade_date : null;

  const toggleChip = (label: string, active: boolean, onClick: () => void) => (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-full border px-2.5 py-0.5 text-[10px] font-medium transition",
        active ? "border-primary/40 bg-primary/10 text-primary" : "border-border text-muted-foreground hover:text-foreground",
      )}
    >
      {label}
    </button>
  );

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <ChartCardHeader
        title={t("stockTracker.indicatorChartTitle")}
        helpText={t("stockTracker.indicatorExplanation")}
        collapsed={collapsed}
        onToggle={toggle}
        meta={t("stockTracker.dataDate", { date: formatDataDate(lastDate) })}
      />
      {!collapsed && (
        <>
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            {toggleChip(t("stockTracker.bollingerBands"), showBoll, () => setShowBoll((v) => !v))}
            {toggleChip(t("stockTracker.movingAverage"), showMa, () => setShowMa((v) => !v))}
            {toggleChip("MACD", showMacd, () => setShowMacd((v) => !v))}
            {toggleChip("KDJ", showKdj, () => setShowKdj((v) => !v))}
          </div>
          <div className="relative h-[560px] w-full">
            <div ref={ref} className="absolute inset-0" />
            {!hasData && (
              <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-muted-foreground">
                <TrendingUp className="h-8 w-8 opacity-40" />
                <span className="text-xs">{t("stockTracker.noIndicatorData")}</span>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
