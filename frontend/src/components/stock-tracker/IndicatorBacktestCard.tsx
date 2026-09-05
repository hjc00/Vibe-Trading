import { useTranslation } from "react-i18next";
import { useCardCollapse } from "@/hooks/useCardCollapse";
import type { BacktestTradePoint, SymbolSnapshot } from "@/lib/api";
import { BacktestCard } from "./BacktestCard";
import { ChartCardHeader } from "./ChartCardHeader";
import { IndicatorChartCard } from "./IndicatorChartCard";

interface IndicatorBacktestCardProps {
  symbol: SymbolSnapshot | null;
  onHide?: () => void;
  backtestTrades?: BacktestTradePoint[];
  onBacktestResult?: (result: { code: string; trades: BacktestTradePoint[] } | null) => void;
}

/**
 * Combined "技术指标 + 回测" card: the standalone technical chart on top (with
 * the latest backtest B/S overlaid) and the backtest rule builder / results
 * below, sharing one header / collapse / hide.
 */
export function IndicatorBacktestCard({
  symbol,
  onHide,
  backtestTrades,
  onBacktestResult,
}: IndicatorBacktestCardProps) {
  const { t } = useTranslation();
  const { collapsed, toggle } = useCardCollapse("indicator_backtest");

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <ChartCardHeader
        title={t("stockTracker.techBacktestTitle")}
        helpText={t("stockTracker.techBacktestExplanation")}
        onHide={onHide}
        collapsed={collapsed}
        onToggle={toggle}
      />
      {!collapsed ? (
        <div className="flex flex-col gap-4">
          <BacktestCard symbol={symbol} onBacktestResult={onBacktestResult} bare />
          <div className="h-px w-full bg-border/60" />
          <IndicatorChartCard symbol={symbol} backtestTrades={backtestTrades} bare />
        </div>
      ) : null}
    </div>
  );
}
