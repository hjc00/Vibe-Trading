import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { TrendingUp } from "lucide-react";
import { useChartLifecycle } from "@/hooks/useChartLifecycle";
import { useCardCollapse } from "@/hooks/useCardCollapse";
import { getChartTheme } from "@/lib/chart-theme";
import { cn } from "@/lib/utils";
import { formatDataDate } from "@/lib/stockTracker";
import { safeGet, safeSet } from "@/lib/storage";
import { ChartCardHeader } from "./ChartCardHeader";
import type { BacktestTradePoint, IndicatorBar, SymbolSnapshot } from "@/lib/api";

interface IndicatorChartCardProps {
  symbol: SymbolSnapshot | null;
  /** Recent backtest buy/sell fills to overlay on the price pane (same code). */
  backtestTrades?: BacktestTradePoint[];
  /** Render without its own card border/header (for embedding in a shared card). */
  bare?: boolean;
}

const INDICATOR_OPTS_KEY = "stockTracker.indicatorChart.options.v1";

interface IndicatorChartOptions {
  showBoll: boolean;
  showMa: boolean;
  showMacd: boolean;
  showKdj: boolean;
  showCrosses: boolean;
}

const DEFAULT_INDICATOR_OPTS: IndicatorChartOptions = {
  showBoll: true,
  showMa: true,
  showMacd: true,
  showKdj: true,
  showCrosses: true,
};

function loadIndicatorOptions(): IndicatorChartOptions {
  const raw = safeGet(INDICATOR_OPTS_KEY);
  if (!raw) return { ...DEFAULT_INDICATOR_OPTS };
  try {
    const parsed = JSON.parse(raw) as Partial<IndicatorChartOptions>;
    return {
      showBoll: typeof parsed.showBoll === "boolean" ? parsed.showBoll : true,
      showMa: typeof parsed.showMa === "boolean" ? parsed.showMa : true,
      showMacd: typeof parsed.showMacd === "boolean" ? parsed.showMacd : true,
      showKdj: typeof parsed.showKdj === "boolean" ? parsed.showKdj : true,
      showCrosses: typeof parsed.showCrosses === "boolean" ? parsed.showCrosses : true,
    };
  } catch {
    return { ...DEFAULT_INDICATOR_OPTS };
  }
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

/** Map backtest buy/sell fills onto the indicator bars by trading date. */
function buildBacktestMarks(
  bars: IndicatorBar[],
  trades: BacktestTradePoint[] | undefined,
  theme: ReturnType<typeof getChartTheme>,
) {
  if (!bars.length || !trades || !trades.length) return [];
  const idxByDate = new Map<string, number>();
  bars.forEach((bar, i) => {
    if (bar.trade_date) idxByDate.set(String(bar.trade_date), i);
  });

  const marks: {
    coord: [number, number];
    symbol: "circle";
    symbolSize: number;
    itemStyle: { color: string };
    label: { show: boolean; formatter: string; color: string; fontWeight: number };
  }[] = [];
  const byDate = new Map<string, BacktestTradePoint[]>();
  for (const trade of trades) {
    const key = String(trade.date);
    if (!idxByDate.has(key)) continue;
    const list = byDate.get(key) ?? [];
    list.push(trade);
    byDate.set(key, list);
  }
  // Same-day multiple fills (e.g. S then B) would overlap at one point; fan
  // them out vertically around the bar so every mark stays visible.
  for (const [date, fills] of byDate) {
    const barIdx = idxByDate.get(date)!;
    const bar = bars[barIdx];
    const hi = bar.high ?? bar.close ?? 0;
    const lo = bar.low ?? bar.close ?? 0;
    const span = Math.max(hi - lo, (bar.close ?? 0) * 0.005, 0.001);
    fills.forEach((trade, k) => {
      const buy = trade.side === "buy";
      const offset = (k - (fills.length - 1) / 2) * span * 0.15;
      marks.push({
        coord: [barIdx, trade.price + offset],
        symbol: "circle",
        symbolSize: 13,
        itemStyle: { color: buy ? theme.upColor : theme.downColor },
        label: { show: true, formatter: buy ? "B" : "S", color: "#ffffff", fontWeight: 700 },
      });
    });
  }
  return marks;
}

// Per-pane heights and the matching container height so the chart shrinks when
// sub-panels (MACD / KDJ) are hidden. Must mirror the pane layout in the builder.
const TECH_PANE_HEIGHTS: Record<string, number> = { price: 300, volume: 66, macd: 112, kdj: 112 };
const TECH_PANE_GAP = 10;

function techChartHeight(showMacd: boolean, showKdj: boolean): number {
  const order = ["price", "volume", ...(showMacd ? ["macd"] : []), ...(showKdj ? ["kdj"] : [])];
  const height = order.reduce((acc, key) => acc + TECH_PANE_HEIGHTS[key], 0);
  const gaps = Math.max(order.length - 1, 0) * TECH_PANE_GAP;
  // 34 = legend/top offset before the first grid; 26 ≈ bottom padding for the
  // dataZoom slider.
  return 34 + height + gaps + 26;
}

export function IndicatorChartCard({ symbol, backtestTrades, bare = false }: IndicatorChartCardProps) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDivElement>(null);
  const [initialOpts] = useState(loadIndicatorOptions);
  const [showBoll, setShowBoll] = useState(initialOpts.showBoll);
  const [showMa, setShowMa] = useState(initialOpts.showMa);
  const [showMacd, setShowMacd] = useState(initialOpts.showMacd);
  const [showKdj, setShowKdj] = useState(initialOpts.showKdj);
  const [showCrosses, setShowCrosses] = useState(initialOpts.showCrosses);
  const { collapsed, toggle } = useCardCollapse("indicator");

  // Persist sub-panel / cross toggles so the chart view survives reloads.
  useEffect(() => {
    safeSet(
      INDICATOR_OPTS_KEY,
      JSON.stringify({ showBoll, showMa, showMacd, showKdj, showCrosses }),
    );
  }, [showBoll, showMa, showMacd, showKdj, showCrosses]);

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
      const backtestMarks = buildBacktestMarks(data.bars, backtestTrades, theme);
      const macdCrossData = showCrosses ? [...macdGoldenPoints, ...macdDeathPoints] : [];
      const kdjCrossData = showCrosses ? [...kdjGoldenPoints, ...kdjDeathPoints] : [];

      const paneHeights: Record<string, number> = { price: 300, volume: 66, macd: 112, kdj: 112 };
      const paneGap = 10;
      const paneOrder: string[] = [
        "price",
        "volume",
        ...(showMacd ? ["macd"] : []),
        ...(showKdj ? ["kdj"] : []),
      ];
      const gridIndex = new Map(paneOrder.map((key, i) => [key, i] as const));
      const gi = (key: string) => gridIndex.get(key) ?? 0;

      const grids: { left: number; right: number; top: number; height: number }[] = [];
      const xAxes: Record<string, unknown>[] = [];
      const yAxes: Record<string, unknown>[] = [];
      let cursor = 34;
      paneOrder.forEach((key) => {
        const g = gi(key);
        const h = paneHeights[key];
        grids.push({ left: 52, right: 18, top: cursor, height: h });
        xAxes.push({
          type: "category",
          gridIndex: g,
          data: data.dates,
          boundaryGap: true,
          axisLine: { lineStyle: { color: theme.axisColor } },
          axisTick: { show: false },
          axisLabel: { show: g === paneOrder.length - 1, color: theme.textColor, fontSize: 10 },
          splitLine: { show: false },
        });
        yAxes.push({
          type: "value",
          gridIndex: g,
          scale: true,
          axisLabel: { color: theme.textColor, fontSize: 10 },
          splitLine: { lineStyle: { color: theme.gridColor } },
          axisLine: { show: false },
        });
        cursor += h + paneGap;
      });

      const series = [
        {
          name: t("stockTracker.price"),
          type: "candlestick",
          xAxisIndex: gi("price"),
          yAxisIndex: gi("price"),
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
            data: [...priceDivergencePoints, ...backtestMarks],
          },
        },
        ...(showMa
          ? [
              { name: "MA5", type: "line", xAxisIndex: gi("price"), yAxisIndex: gi("price"), data: data.ma5, symbol: "none", lineStyle: { width: 1, color: maColors[0] }, itemStyle: { color: maColors[0] } },
              { name: "MA10", type: "line", xAxisIndex: gi("price"), yAxisIndex: gi("price"), data: data.ma10, symbol: "none", lineStyle: { width: 1, color: maColors[1] }, itemStyle: { color: maColors[1] } },
              { name: "MA20", type: "line", xAxisIndex: gi("price"), yAxisIndex: gi("price"), data: data.ma20, symbol: "none", lineStyle: { width: 1, color: maColors[2] }, itemStyle: { color: maColors[2] } },
              { name: "MA60", type: "line", xAxisIndex: gi("price"), yAxisIndex: gi("price"), data: data.ma60, symbol: "none", lineStyle: { width: 1, color: maColors[3] }, itemStyle: { color: maColors[3] } },
            ]
          : []),
        ...(showBoll
          ? [
              { name: "BOLL上", type: "line", xAxisIndex: gi("price"), yAxisIndex: gi("price"), data: data.bbUpper, symbol: "none", lineStyle: { width: 1, color: bollColor, type: "dashed" }, itemStyle: { color: bollColor } },
              { name: "BOLL中", type: "line", xAxisIndex: gi("price"), yAxisIndex: gi("price"), data: data.bbMid, symbol: "none", lineStyle: { width: 1, color: bollColor }, itemStyle: { color: bollColor } },
              { name: "BOLL下", type: "line", xAxisIndex: gi("price"), yAxisIndex: gi("price"), data: data.bbLower, symbol: "none", lineStyle: { width: 1, color: bollColor, type: "dashed" }, itemStyle: { color: bollColor } },
            ]
          : []),
        { name: t("stockTracker.chartVolume"), type: "bar", xAxisIndex: gi("volume"), yAxisIndex: gi("volume"), data: volumeData, barWidth: "60%" },
        ...(showMacd
          ? [
              { name: "MACD", type: "bar", xAxisIndex: gi("macd"), yAxisIndex: gi("macd"), data: macdHistData, barWidth: "60%" },
              {
                name: "DIF",
                type: "line",
                xAxisIndex: gi("macd"),
                yAxisIndex: gi("macd"),
                data: data.dif,
                symbol: "none",
                lineStyle: { width: 1, color: macdDiffColor },
                itemStyle: { color: macdDiffColor },
                markPoint: { data: macdCrossData },
                markLine: {
                  symbol: ["none", "none"],
                  lineStyle: { color: theme.warningColor, type: "dashed", width: 1 },
                  label: { show: false },
                  data: difDivergenceLines,
                },
              },
              { name: "DEA", type: "line", xAxisIndex: gi("macd"), yAxisIndex: gi("macd"), data: data.dea, symbol: "none", lineStyle: { width: 1, color: macdDeaColor }, itemStyle: { color: macdDeaColor } },
            ]
          : []),
        ...(showKdj
          ? [
              { name: "K", type: "line", xAxisIndex: gi("kdj"), yAxisIndex: gi("kdj"), data: data.k, symbol: "none", lineStyle: { width: 1, color: maColors[2] }, itemStyle: { color: maColors[2] }, markPoint: { data: kdjCrossData } },
              { name: "D", type: "line", xAxisIndex: gi("kdj"), yAxisIndex: gi("kdj"), data: data.d, symbol: "none", lineStyle: { width: 1, color: maColors[0] }, itemStyle: { color: maColors[0] } },
              { name: "J", type: "line", xAxisIndex: gi("kdj"), yAxisIndex: gi("kdj"), data: data.j, symbol: "none", lineStyle: { width: 1, color: maColors[1] }, itemStyle: { color: maColors[1] } },
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
        grid: grids,
        xAxis: xAxes,
        yAxis: yAxes,
        dataZoom: [
          { type: "inside", xAxisIndex: paneOrder.map((_, i) => i), start: 0, end: 100 },
          { type: "slider", xAxisIndex: paneOrder.map((_, i) => i), bottom: 4, height: 18, start: 0, end: 100 },
        ],
        series,
      };
    },
    [data, t, showBoll, showMa, showMacd, showKdj, showCrosses, collapsed, backtestTrades],
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

  const content = (
    <>
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        {toggleChip(t("stockTracker.bollingerBands"), showBoll, () => setShowBoll((v) => !v))}
        {toggleChip(t("stockTracker.movingAverage"), showMa, () => setShowMa((v) => !v))}
        {toggleChip("MACD", showMacd, () => setShowMacd((v) => !v))}
        {toggleChip("KDJ", showKdj, () => setShowKdj((v) => !v))}
        {toggleChip("金叉/死叉", showCrosses, () => setShowCrosses((v) => !v))}
      </div>
      <div className="relative w-full" style={{ height: techChartHeight(showMacd, showKdj) }}>
        <div ref={ref} className="absolute inset-0" />
        {!hasData && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-muted-foreground">
            <TrendingUp className="h-8 w-8 opacity-40" />
            <span className="text-xs">{t("stockTracker.noIndicatorData")}</span>
          </div>
        )}
      </div>
    </>
  );

  // Bare mode: embed inside a shared card (no own border/header).
  if (bare) return content;

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <ChartCardHeader
        title={t("stockTracker.indicatorChartTitle")}
        helpText={t("stockTracker.indicatorExplanation")}
        collapsed={collapsed}
        onToggle={toggle}
        meta={t("stockTracker.dataDate", { date: formatDataDate(lastDate) })}
      />
      {!collapsed && content}
    </div>
  );
}
