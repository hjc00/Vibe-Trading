import { ShieldAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import {
  formatAtr,
  formatBeta,
  formatDataDate,
  formatPct,
  getBetaToneClass,
  getDrawdownToneClass,
  latestPeriodEndDate,
} from "@/lib/stockTracker";
import { ChartCardHeader } from "./ChartCardHeader";
import type { SymbolSnapshot } from "@/lib/api";

interface RiskMetricsCardProps {
  symbol: SymbolSnapshot | null;
}

export function RiskMetricsCard({ symbol }: RiskMetricsCardProps) {
  const { t } = useTranslation();

  const risk = symbol?.risk;
  const hasData =
    risk != null &&
    (risk.atr_14 != null ||
      risk.max_drawdown_60d != null ||
      risk.beta_vs_index != null);

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <ChartCardHeader
        title={t("stockTracker.riskTitle")}
        helpText={t("stockTracker.riskExplanation")}
        meta={t("stockTracker.dataDate", {
          date: formatDataDate(latestPeriodEndDate(symbol)),
        })}
      />
      {!hasData ? (
        <div className="flex h-[240px] flex-col items-center justify-center gap-2 text-muted-foreground">
          <ShieldAlert className="h-8 w-8 opacity-40" />
          <span className="text-xs">{t("stockTracker.noRiskData")}</span>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          <StatBox
            label={`${t("stockTracker.atr")}(14)`}
            value={formatAtr(risk.atr_14)}
            sub={risk.atr_pct != null ? formatPct(risk.atr_pct) : undefined}
          />
          <StatBox
            label={t("stockTracker.maxDrawdown60")}
            value={formatPct(risk.max_drawdown_60d)}
            valueClass={getDrawdownToneClass(risk.max_drawdown_60d)}
          />
          <StatBox
            label={t("stockTracker.beta")}
            value={formatBeta(risk.beta_vs_index)}
            valueClass={getBetaToneClass(risk.beta_vs_index)}
            sub={risk.benchmark_code ?? undefined}
          />
          <StatBox
            label={`${t("stockTracker.stopLoss")} ${risk.stop_loss_atr_multiple != null ? `(${risk.stop_loss_atr_multiple}×ATR)` : ""}`}
            value={risk.stop_loss_price != null ? risk.stop_loss_price.toFixed(2) : "—"}
            valueClass={risk.stop_loss_price != null ? "text-danger" : undefined}
            highlight
          />
        </div>
      )}
    </div>
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
  sub?: string;
  valueClass?: string;
  highlight?: boolean;
}) {
  return (
    <div className={cn("rounded-lg p-3", highlight ? "bg-danger/10" : "bg-muted/40")}>
      <p className="truncate text-[10px] text-muted-foreground">{label}</p>
      <p className={cn("mt-0.5 font-mono text-sm font-semibold tabular-nums", valueClass ?? "text-foreground")}>
        {value}
      </p>
      {sub && <p className="mt-0.5 truncate text-[10px] text-muted-foreground">{sub}</p>}
    </div>
  );
}
