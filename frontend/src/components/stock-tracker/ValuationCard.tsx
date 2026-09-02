import type { ReactNode } from "react";
import { Gauge } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { cn } from "@/lib/utils";
import {
  formatDataDate,
  formatMarketCap,
  getQualityToneClass,
  getValuationBandLabelKey,
  getValuationPercentileTone,
} from "@/lib/stockTracker";
import { ChartCardHeader } from "./ChartCardHeader";
import type { SymbolSnapshot } from "@/lib/api";

interface ValuationCardProps {
  symbol: SymbolSnapshot | null;
}

export function ValuationCard({ symbol }: ValuationCardProps) {
  const { t } = useTranslation();
  const valuation = symbol?.valuation;
  const hasData =
    valuation != null &&
    (valuation.pe_ttm != null ||
      valuation.pb != null ||
      valuation.fundamental_quality_score != null ||
      valuation.roe != null);

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <ChartCardHeader
        title={t("stockTracker.valuationTitle")}
        helpText={t("stockTracker.valuationExplanation")}
        meta={t("stockTracker.dataDate", {
          date: formatDataDate(symbol?.valuation?.trade_date),
        })}
      />
      {!hasData ? (
        <div className="flex h-[240px] flex-col items-center justify-center gap-2 text-muted-foreground">
          <Gauge className="h-8 w-8 opacity-40" />
          <span className="text-xs">{t("stockTracker.noValuationData")}</span>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          <StatBox
            label={`${t("stockTracker.peTtm")} · ${t("stockTracker.percentile3y")}`}
            value={formatMultiple(valuation.pe_ttm)}
            sub={percentileLine(t, valuation.pe_percentile_3y)}
          />
          <StatBox
            label={`${t("stockTracker.pb")} · ${t("stockTracker.percentile3y")}`}
            value={formatMultiple(valuation.pb)}
            sub={percentileLine(t, valuation.pb_percentile_3y)}
          />
          <StatBox label={t("stockTracker.peg")} value={formatMultiple(valuation.peg)} />
          <StatBox
            label={t("stockTracker.dividendYield")}
            value={valuation.dividend_yield != null ? `${valuation.dividend_yield.toFixed(2)}%` : "—"}
            sub={
              valuation.total_market_cap != null
                ? `${t("stockTracker.totalMarketCap")} ${formatMarketCap(valuation.total_market_cap)}`
                : undefined
            }
          />
          <StatBox
            label={t("stockTracker.roe")}
            value={valuation.roe != null ? `${valuation.roe.toFixed(1)}%` : "—"}
            sub={
              valuation.net_profit_yoy != null
                ? `${t("stockTracker.netProfitYoy")} ${valuation.net_profit_yoy.toFixed(1)}%`
                : undefined
            }
          />
          <StatBox
            label={t("stockTracker.qualityScore")}
            value={valuation.fundamental_quality_score != null ? valuation.fundamental_quality_score.toFixed(0) : "—"}
            valueClass={getQualityToneClass(valuation.fundamental_quality_score)}
            highlight={valuation.fundamental_quality_score != null}
          />
        </div>
      )}
    </div>
  );
}

function formatMultiple(value: number | null | undefined): string {
  if (value === undefined || value === null) return "—";
  return value.toFixed(2);
}

function percentileLine(
  t: TFunction,
  value: number | null | undefined,
): ReactNode {
  if (value === undefined || value === null) return undefined;
  const bandKey = getValuationBandLabelKey(value);
  return (
    <span className="flex items-center justify-between gap-1">
      <span className={cn("font-mono tabular-nums", getValuationPercentileTone(value))}>
        {value.toFixed(0)}
      </span>
      <span>{bandKey ? t(bandKey as never) : ""}</span>
    </span>
  );
}

function StatBox({
  label,
  value,
  sub,
  valueClass,
  highlight,
}: {
  label: string;
  value: string;
  sub?: ReactNode;
  valueClass?: string;
  highlight?: boolean;
}) {
  return (
    <div className={cn("rounded-lg p-3", highlight ? "bg-primary/10" : "bg-muted/40")}>
      <p className="truncate text-[10px] text-muted-foreground">{label}</p>
      <p className={cn("mt-0.5 font-mono text-sm font-semibold tabular-nums", valueClass ?? "text-foreground")}>
        {value}
      </p>
      {sub && <div className="mt-0.5 text-[10px] text-muted-foreground">{sub}</div>}
    </div>
  );
}
