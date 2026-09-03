import { Target } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import {
  formatTrackPrice,
  getChangeToneClass,
  getConsensusRatingToneClass,
} from "@/lib/stockTracker";
import { ChartCardHeader } from "./ChartCardHeader";
import type { SymbolSnapshot } from "@/lib/api";

interface ConsensusCardProps {
  symbol: SymbolSnapshot | null;
  onHide?: () => void;
}

/** Format a fraction (e.g. 0.128) as a signed percent. */
function formatFractionPct(value: number | null | undefined): string {
  if (value === undefined || value === null) return "—";
  return `${value > 0 ? "+" : ""}${(value * 100).toFixed(1)}%`;
}

/** Format a price, falling back to an em dash when absent. */
function formatPriceOrDash(value: number | null | undefined): string {
  return value == null ? "—" : formatTrackPrice(value);
}

/**
 * Sell-side consensus for one symbol (盈利预期/一致预期): coverage, rating mix,
 * consensus EPS, forward PE, target-price range and upside vs the latest close.
 */
export function ConsensusCard({ symbol, onHide }: ConsensusCardProps) {
  const { t } = useTranslation();
  const consensus = symbol?.consensus;
  const hasData =
    consensus != null &&
    (consensus.analyst_count != null ||
      consensus.rating_score != null ||
      consensus.consensus_eps_next != null ||
      consensus.target_price_avg != null);

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <ChartCardHeader
        title={t("stockTracker.consensusTitle")}
        helpText={t("stockTracker.consensusExplanation")}
        onHide={onHide}
        meta={
          consensus?.source && consensus.source !== "unavailable"
            ? t("stockTracker.dataSource", { source: consensus.source })
            : undefined
        }
      />
      {!hasData ? (
        <div className="flex h-[240px] flex-col items-center justify-center gap-2 text-muted-foreground">
          <Target className="h-8 w-8 opacity-40" />
          <span className="text-xs">{t("stockTracker.consensusNoData")}</span>
          {consensus?.error ? (
            <span className="max-w-[220px] truncate text-[10px] text-muted-foreground/70">
              {consensus.error}
            </span>
          ) : null}
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-2 gap-2">
            <Box
              label={t("stockTracker.consensusAnalystCount")}
              value={consensus?.analyst_count != null ? String(consensus.analyst_count) : "—"}
            />
            <Box
              label={t("stockTracker.consensusRatingScore")}
              value={consensus?.rating_score != null ? consensus.rating_score.toFixed(0) : "—"}
              valueClass={getConsensusRatingToneClass(consensus?.rating_score)}
              highlight={consensus?.rating_score != null}
            />
            <Box
              label={t("stockTracker.consensusEpsCur")}
              value={consensus?.consensus_eps_cur != null ? consensus.consensus_eps_cur.toFixed(2) : "—"}
            />
            <Box
              label={t("stockTracker.consensusEpsNext")}
              value={consensus?.consensus_eps_next != null ? consensus.consensus_eps_next.toFixed(2) : "—"}
            />
            <Box
              label={t("stockTracker.consensusForwardPe")}
              value={consensus?.forward_pe != null ? consensus.forward_pe.toFixed(1) : "—"}
            />
            <Box
              label={t("stockTracker.consensusEpsRevision")}
              value={formatFractionPct(consensus?.eps_revision_pct)}
              valueClass={getChangeToneClass(consensus?.eps_revision_pct)}
            />
          </div>

          <div className="rounded-lg bg-muted/40 p-3">
            <div className="flex items-center justify-between">
              <p className="text-[10px] text-muted-foreground">
                {t("stockTracker.consensusTargetPrice")}
              </p>
              <span className="text-[10px] text-muted-foreground">
                {t("stockTracker.consensusUpside")}{" "}
                <span className={cn("font-mono tabular-nums", getChangeToneClass(consensus?.upside_pct))}>
                  {formatFractionPct(consensus?.upside_pct)}
                </span>
              </span>
            </div>
            <p className="mt-1 font-mono text-sm font-semibold tabular-nums">
              {consensus?.target_price_avg != null
                ? formatTrackPrice(consensus.target_price_avg)
                : "—"}
            </p>
            {(consensus?.target_price_low != null || consensus?.target_price_high != null) && (
              <p className="mt-0.5 font-mono text-[10px] tabular-nums text-muted-foreground">
                {formatPriceOrDash(consensus.target_price_low)} – {formatPriceOrDash(consensus.target_price_high)}
              </p>
            )}
            {symbol?.close != null && (
              <p className="mt-1 text-[10px] text-muted-foreground">
                {t("stockTracker.price")}: {symbol.close.toFixed(2)}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function Box({
  label,
  value,
  valueClass,
  highlight,
}: {
  label: string;
  value: string;
  valueClass?: string;
  highlight?: boolean;
}) {
  return (
    <div className={cn("rounded-lg p-3", highlight ? "bg-primary/10" : "bg-muted/40")}>
      <p className="truncate text-[10px] text-muted-foreground">{label}</p>
      <p className={cn("mt-0.5 font-mono text-sm font-semibold tabular-nums", valueClass ?? "text-foreground")}>
        {value}
      </p>
    </div>
  );
}
