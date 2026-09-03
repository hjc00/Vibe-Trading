import { Flame } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getConceptHeatToneClass } from "@/lib/stockTracker";
import { ChartCardHeader } from "./ChartCardHeader";
import type { SymbolSnapshot } from "@/lib/api";

interface ConceptHeatCardProps {
  symbol: SymbolSnapshot | null;
  onHide?: () => void;
}

/**
 * Concept / thematic-board heat for one symbol (题材/概念热度): the concept
 * boards it belongs to, its hottest board on the whole-market ranking, and the
 * composite heat score (0-100) plus that concept's limit-up count.
 */
export function ConceptHeatCard({ symbol, onHide }: ConceptHeatCardProps) {
  const { t } = useTranslation();
  const concept = symbol?.concept;
  const hasData =
    concept != null &&
    (concept.boards.length > 0 || concept.hottest_concept != null);

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <ChartCardHeader
        title={t("stockTracker.conceptTitle")}
        helpText={t("stockTracker.conceptExplanation")}
        onHide={onHide}
        meta={
          concept?.source && concept.source !== "unavailable"
            ? t("stockTracker.dataSource", { source: concept.source })
            : undefined
        }
      />
      {!hasData ? (
        <div className="flex h-[240px] flex-col items-center justify-center gap-2 text-muted-foreground">
          <Flame className="h-8 w-8 opacity-40" />
          <span className="text-xs">{t("stockTracker.conceptNoData")}</span>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="rounded-lg bg-primary/10 p-3">
            <div className="flex items-center justify-between">
              <p className="text-[10px] text-muted-foreground">
                {t("stockTracker.conceptHottest")}
              </p>
              {concept?.hottest_concept_rank != null && (
                <span className="rounded-full bg-primary/15 px-1.5 py-0.5 font-mono text-[10px] tabular-nums text-primary">
                  #{concept.hottest_concept_rank}
                </span>
              )}
            </div>
            <p className="mt-0.5 truncate text-sm font-semibold">
              {concept?.hottest_concept ?? "—"}
            </p>
            <div className="mt-1 flex items-baseline gap-2">
              <span
                className={cn(
                  "font-mono text-lg font-semibold tabular-nums",
                  getConceptHeatToneClass(concept?.concept_heat_score),
                )}
              >
                {concept?.concept_heat_score != null
                  ? concept.concept_heat_score.toFixed(0)
                  : "—"}
              </span>
              <span className="text-[10px] text-muted-foreground">
                {t("stockTracker.conceptHeatScore")}
              </span>
              {concept?.limit_up_count != null && (
                <span className="text-[10px] text-muted-foreground">
                  · {t("stockTracker.conceptLimitUpCount")} {concept.limit_up_count}
                </span>
              )}
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {concept?.boards.map((board) => {
              const isHottest = board === concept.hottest_concept;
              return (
                <span
                  key={board}
                  className={cn(
                    "rounded-full px-2 py-0.5 text-[10px] font-medium",
                    isHottest
                      ? "bg-primary/15 text-primary"
                      : "bg-muted/40 text-muted-foreground",
                  )}
                >
                  {board}
                </span>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
