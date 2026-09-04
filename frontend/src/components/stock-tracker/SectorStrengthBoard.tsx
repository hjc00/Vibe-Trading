import { useCallback, useMemo, useState } from "react";
import { Layers } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { useCardCollapse } from "@/hooks/useCardCollapse";
import {
  formatCapitalAmount,
  formatDataDate,
  getChangeToneClass,
  getQualityToneClass,
} from "@/lib/stockTracker";
import { ChartCardHeader } from "./ChartCardHeader";
import type { ConceptStrength, SectorPeriodMetric, SectorStrength } from "@/lib/api";

interface SectorStrengthBoardProps {
  sectors: SectorStrength[] | undefined;
  concepts?: ConceptStrength[] | undefined;
  tradingDate?: string | null;
  onHide?: () => void;
}

type BoardTab = "industry" | "concept";

const COLLAPSE_STORAGE_KEY = "stockTracker.sectorStrengthCollapsed";
const TAB_STORAGE_KEY = "stockTracker.sectorStrengthTab";
// Boards the watchlist holds are pinned to the top; the whole list is capped at
// this many rows so the dashboard stays compact.
const DISPLAY_LIMIT = 20;

/** A unified row shape so the industry and concept tabs share one table. */
interface BoardRow {
  key: string;
  boardName: string;
  memberCount: number;
  marketRank?: number | null;
  changePct?: number | null;
  fundFlowNet?: number | null;
  upCount?: number | null;
  downCount?: number | null;
  leader?: string | null;
  prosperityScore?: number | null;
  limitUpCount?: number | null;
  periodMetrics?: SectorPeriodMetric[];
}

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

export function SectorStrengthBoard({ sectors, concepts, tradingDate, onHide }: SectorStrengthBoardProps) {
  const { t } = useTranslation();
  const { collapsed, toggle: toggleCollapsed } = useCardCollapse(
    "sector",
    COLLAPSE_STORAGE_KEY,
  );
  const [tab, setTab] = useState<BoardTab>(() => {
    try {
      return localStorage.getItem(TAB_STORAGE_KEY) === "concept" ? "concept" : "industry";
    } catch {
      return "industry";
    }
  });
  const selectTab = useCallback((next: BoardTab) => {
    setTab(next);
    try {
      localStorage.setItem(TAB_STORAGE_KEY, next);
    } catch {
      /* storage unavailable — keep the in-memory state only */
    }
  }, []);

  const isIndustry = tab === "industry";

  // Pin the boards the watchlist holds to the top (in market-rank order), then
  // fill up to DISPLAY_LIMIT with the strongest remaining boards.
  const visibleRows = useMemo<BoardRow[]>(() => {
    const raw = isIndustry ? (sectors ?? []) : (concepts ?? []);
    const mapped: BoardRow[] = raw.map((s) => {
      if (!isIndustry) {
        const c = s as ConceptStrength;
        return {
          key: c.board_name,
          boardName: c.board_name,
          memberCount: c.member_count,
          marketRank: c.market_rank,
          changePct: c.change_pct,
          fundFlowNet: c.fund_flow_net,
          upCount: c.up_count,
          downCount: c.down_count,
          leader: c.leader,
          limitUpCount: c.limit_up_count,
        };
      }
      const sec = s as SectorStrength;
      return {
        key: sec.board_name,
        boardName: sec.board_name,
        memberCount: sec.member_count,
        marketRank: sec.market_rank,
        changePct: sec.change_pct,
        fundFlowNet: sec.fund_flow_net,
        upCount: sec.up_count,
        downCount: sec.down_count,
        leader: sec.leader,
        prosperityScore: sec.prosperity_score,
        periodMetrics: sec.period_metrics,
      };
    });
    const byRank = (a: BoardRow, b: BoardRow) =>
      (a.marketRank ?? Number.POSITIVE_INFINITY) - (b.marketRank ?? Number.POSITIVE_INFINITY);
    const mine = mapped.filter((s) => s.memberCount > 0).sort(byRank);
    const others = mapped.filter((s) => s.memberCount === 0).sort(byRank);
    return [...mine, ...others].slice(0, DISPLAY_LIMIT);
  }, [isIndustry, sectors, concepts]);

  const hasData = visibleRows.length > 0;

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <ChartCardHeader
        title={t("stockTracker.sectorStrengthTitle")}
        helpText={t(
          isIndustry ? "stockTracker.sectorStrengthExplanation" : "stockTracker.conceptStrengthExplanation",
        )}
        meta={t("stockTracker.dataDate", { date: formatDataDate(tradingDate) })}
        onHide={onHide}
        collapsed={collapsed}
        onToggle={toggleCollapsed}
      />
      {!collapsed && (
        <>
          <div className="mb-2 flex gap-1">
            <button
              type="button"
              onClick={() => selectTab("industry")}
              className={cn(
                "rounded-full px-3 py-1 text-xs font-medium transition",
                isIndustry
                  ? "bg-primary/15 text-primary"
                  : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
              )}
            >
              {t("stockTracker.sectorTabIndustry")}
            </button>
            <button
              type="button"
              onClick={() => selectTab("concept")}
              className={cn(
                "rounded-full px-3 py-1 text-xs font-medium transition",
                !isIndustry
                  ? "bg-primary/15 text-primary"
                  : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
              )}
            >
              {t("stockTracker.sectorTabConcept")}
            </button>
          </div>
          {!hasData ? (
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
                    {isIndustry && (
                      <th className="py-2 pr-2 text-right font-medium">
                        {t("stockTracker.sectorTrend")}
                      </th>
                    )}
                    <th className="py-2 pr-2 text-right font-medium">
                      {t("stockTracker.sectorFundFlow")}
                    </th>
                    <th className="py-2 pr-2 text-center font-medium">
                      {t("stockTracker.sectorUpDown")}
                    </th>
                    <th className="py-2 pr-2 text-right font-medium">
                      {isIndustry
                        ? t("stockTracker.sectorProsperity")
                        : t("stockTracker.conceptLimitUpCount")}
                    </th>
                    <th className="py-2 font-medium">{t("stockTracker.sectorLeader")}</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleRows.map((row) => {
                    const inWatchlist = row.memberCount > 0;
                    return (
                      <tr
                        key={row.key}
                        className={cn(
                          "border-b border-border/40 last:border-0",
                          inWatchlist && "bg-primary/5",
                        )}
                      >
                        <td className="py-1.5 pr-2 font-mono tabular-nums text-muted-foreground">
                          {row.marketRank != null ? `#${row.marketRank}` : "—"}
                        </td>
                        <td className="py-1.5 pr-2">
                          <span className="font-medium">{row.boardName}</span>
                          {inWatchlist ? (
                            <span className="ml-1.5 rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
                              {row.memberCount}
                            </span>
                          ) : null}
                        </td>
                        <td
                          className={cn(
                            "py-1.5 pr-2 text-right font-mono tabular-nums",
                            getChangeToneClass(row.changePct),
                          )}
                        >
                          {formatChangePct(row.changePct)}
                        </td>
                        {isIndustry && (
                          <td className="py-1.5 pr-2">
                            <PeriodTrendCell metrics={row.periodMetrics} />
                          </td>
                        )}
                        <td className="py-1.5 pr-2 text-right font-mono tabular-nums text-muted-foreground">
                          {formatCapitalAmount(row.fundFlowNet)}
                        </td>
                        <td className="py-1.5 pr-2 text-center font-mono tabular-nums text-muted-foreground">
                          {row.upCount != null || row.downCount != null
                            ? `${row.upCount ?? "—"}/${row.downCount ?? "—"}`
                            : "—"}
                        </td>
                        <td className="py-1.5 pr-2 text-right font-mono tabular-nums">
                          {isIndustry ? (
                            <span className={getQualityToneClass(row.prosperityScore)}>
                              {row.prosperityScore != null ? row.prosperityScore.toFixed(0) : "—"}
                            </span>
                          ) : (
                            <span className="text-foreground">
                              {row.limitUpCount != null ? row.limitUpCount : "—"}
                            </span>
                          )}
                        </td>
                        <td className="py-1.5 text-muted-foreground">{row.leader ?? "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}
