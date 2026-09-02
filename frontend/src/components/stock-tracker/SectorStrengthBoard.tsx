import { useCallback, useMemo, useState } from "react";
import { Layers } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import {
  formatCapitalAmount,
  formatDataDate,
  getChangeToneClass,
  getQualityToneClass,
} from "@/lib/stockTracker";
import { ChartCardHeader } from "./ChartCardHeader";
import type { SectorPeriodMetric, SectorStrength } from "@/lib/api";

interface SectorStrengthBoardProps {
  sectors: SectorStrength[] | undefined;
  tradingDate?: string | null;
}

const COLLAPSE_STORAGE_KEY = "stockTracker.sectorStrengthCollapsed";
// Boards the watchlist holds are pinned to the top; the whole list is capped at
// this many rows so the dashboard stays compact.
const DISPLAY_LIMIT = 20;

function formatChangePct(value: number | null | undefined): string {
  if (value === undefined || value === null) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

/** Format a return ratio (e.g. 0.052) as a signed percent string (+5.2%). */
function formatReturnPct(value: number | null | undefined): string {
  if (value === undefined || value === null) return "—";
  return `${value > 0 ? "+" : ""}${(value * 100).toFixed(1)}%`;
}

function PeriodTrendCell({ metrics }: { metrics: SectorPeriodMetric[] | undefined }) {
  if (!metrics || metrics.length === 0) {
    return <span className="text-muted-foreground">—</span>;
  }
  return (
    <div className="flex flex-wrap items-center justify-end gap-1">
      {metrics.map((m) => {
        const rps = m.avg_rps_market;
        return (
          <span
            key={m.period}
            title={rps != null ? `RPS ${rps.toFixed(1)}%` : undefined}
            className={cn(
              "rounded px-1 py-0.5 font-mono text-[10px] tabular-nums",
              m.avg_return_pct != null && m.avg_return_pct > 0 && "bg-success/10 text-success",
              m.avg_return_pct != null && m.avg_return_pct < 0 && "bg-danger/10 text-danger",
              (m.avg_return_pct == null || m.avg_return_pct === 0) &&
                "bg-muted/40 text-muted-foreground",
            )}
          >
            {m.period}D {formatReturnPct(m.avg_return_pct)}
          </span>
        );
      })}
    </div>
  );
}

export function SectorStrengthBoard({ sectors, tradingDate }: SectorStrengthBoardProps) {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(COLLAPSE_STORAGE_KEY) === "1";
    } catch {
      return false;
    }
  });
  const toggleCollapsed = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(COLLAPSE_STORAGE_KEY, next ? "1" : "0");
      } catch {
        /* storage unavailable — keep the in-memory state only */
      }
      return next;
    });
  }, []);

  const hasData = sectors !== undefined && sectors.length > 0;

  // Pin the boards the watchlist holds to the top (in market-rank order), then
  // fill up to DISPLAY_LIMIT with the strongest remaining boards.
  const visibleSectors = useMemo(() => {
    if (!sectors || sectors.length === 0) return [];
    const byRank = (a: SectorStrength, b: SectorStrength) =>
      (a.market_rank ?? Number.POSITIVE_INFINITY) - (b.market_rank ?? Number.POSITIVE_INFINITY);
    const mine = sectors.filter((s) => s.member_count > 0).sort(byRank);
    const others = sectors.filter((s) => s.member_count === 0).sort(byRank);
    return [...mine, ...others].slice(0, DISPLAY_LIMIT);
  }, [sectors]);

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <ChartCardHeader
        title={t("stockTracker.sectorStrengthTitle")}
        helpText={t("stockTracker.sectorStrengthExplanation")}
        meta={t("stockTracker.dataDate", { date: formatDataDate(tradingDate) })}
        collapsed={collapsed}
        onToggle={toggleCollapsed}
      />
      {!collapsed &&
        (!hasData ? (
          <div className="flex h-[240px] flex-col items-center justify-center gap-2 text-muted-foreground">
            <Layers className="h-8 w-8 opacity-40" />
            <span className="text-xs">{t("stockTracker.sectorNoData")}</span>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] border-collapse text-xs">
              <thead>
                <tr className="border-b border-border/60 text-left text-[10px] text-muted-foreground">
                  <th className="py-2 pr-2 font-medium">{t("stockTracker.sectorRank")}</th>
                  <th className="py-2 pr-2 font-medium">{t("stockTracker.sectorBoard")}</th>
                  <th className="py-2 pr-2 text-right font-medium">
                    {t("stockTracker.sectorChangePct")}
                  </th>
                  <th className="py-2 pr-2 text-right font-medium">
                    {t("stockTracker.sectorTrend")}
                  </th>
                  <th className="py-2 pr-2 text-right font-medium">
                    {t("stockTracker.sectorFundFlow")}
                  </th>
                  <th className="py-2 pr-2 text-center font-medium">
                    {t("stockTracker.sectorUpDown")}
                  </th>
                  <th className="py-2 pr-2 text-right font-medium">
                    {t("stockTracker.sectorProsperity")}
                  </th>
                  <th className="py-2 font-medium">{t("stockTracker.sectorLeader")}</th>
                </tr>
              </thead>
              <tbody>
                {visibleSectors.map((sector) => {
                  const inWatchlist = sector.member_count > 0;
                  return (
                    <tr
                      key={sector.board_name}
                      className={cn(
                        "border-b border-border/40 last:border-0",
                        inWatchlist && "bg-primary/5",
                      )}
                    >
                      <td className="py-1.5 pr-2 font-mono tabular-nums text-muted-foreground">
                        {sector.market_rank != null ? `#${sector.market_rank}` : "—"}
                      </td>
                      <td className="py-1.5 pr-2">
                        <span className="font-medium">{sector.board_name}</span>
                        {inWatchlist ? (
                          <span className="ml-1.5 rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                            {sector.member_count}
                          </span>
                        ) : null}
                      </td>
                      <td
                        className={cn(
                          "py-1.5 pr-2 text-right font-mono tabular-nums",
                          getChangeToneClass(sector.change_pct),
                        )}
                      >
                        {formatChangePct(sector.change_pct)}
                      </td>
                      <td className="py-1.5 pr-2">
                        <PeriodTrendCell metrics={sector.period_metrics} />
                      </td>
                      <td className="py-1.5 pr-2 text-right font-mono tabular-nums text-muted-foreground">
                        {formatCapitalAmount(sector.fund_flow_net)}
                      </td>
                      <td className="py-1.5 pr-2 text-center font-mono tabular-nums text-muted-foreground">
                        {sector.up_count != null || sector.down_count != null
                          ? `${sector.up_count ?? "—"}/${sector.down_count ?? "—"}`
                          : "—"}
                      </td>
                      <td
                        className={cn(
                          "py-1.5 pr-2 text-right font-mono tabular-nums",
                          getQualityToneClass(sector.prosperity_score),
                        )}
                      >
                        {sector.prosperity_score != null ? sector.prosperity_score.toFixed(0) : "—"}
                      </td>
                      <td className="py-1.5 text-muted-foreground">{sector.leader ?? "—"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ))}
    </div>
  );
}
