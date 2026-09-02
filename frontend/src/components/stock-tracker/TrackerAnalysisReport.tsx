import { useTranslation } from "react-i18next";
import type {
  RecommendationAction,
  SymbolRecommendation,
  TrackerAnalyzeReport,
} from "@/lib/api";
import {
  formatPriceZoneText,
  formatTrackPrice,
  getActionLabelKey,
  getActionToneClass,
} from "@/lib/stockTracker";

interface TrackerAnalysisReportProps {
  report: TrackerAnalyzeReport | null;
}

const LEGACY_TO_ACTION: Record<string, RecommendationAction> = {
  buy: "buy",
  strong_buy: "buy",
  top_pick: "buy",
  hold: "hold",
  watch: "hold",
  reduce: "reduce",
  reduce_position: "reduce",
  sell: "reduce",
  caution: "reduce",
  avoid: "avoid",
  avoid_position: "avoid",
};

function resolveAction(symbol: SymbolRecommendation): RecommendationAction | null {
  if (symbol.action) return symbol.action;
  const legacy = (symbol.recommendation ?? "").trim().toLowerCase();
  return LEGACY_TO_ACTION[legacy] ?? null;
}

export function TrackerAnalysisReport({ report }: TrackerAnalysisReportProps) {
  const { t } = useTranslation();
  if (!report) return null;

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
        <h3 className="mb-2 text-sm font-semibold">{t("stockTracker.analysisReport")}</h3>
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground/90">
          {report.summary}
        </p>
      </div>

      {report.symbols.length > 0 ? (
        <div className="grid gap-3 md:grid-cols-2">
          {report.symbols.map((symbol) => (
            <SymbolCard key={symbol.code} symbol={symbol} />
          ))}
        </div>
      ) : null}

      {report.portfolio ? (
        <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t("stockTracker.portfolioView")}
          </h4>
          <p className="text-sm">{report.portfolio.theme}</p>
          {report.portfolio.top_pick ? (
            <p className="mt-1 text-sm">
              <span className="text-muted-foreground">{t("stockTracker.topPick")}: </span>
              <span className="font-mono font-medium">{report.portfolio.top_pick}</span>
            </p>
          ) : null}
          {report.portfolio.cautions.length > 0 ? (
            <ul className="mt-2 list-inside list-disc space-y-1 text-sm text-warning">
              {report.portfolio.cautions.map((caution, index) => (
                <li key={index}>{caution}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {report.caveats.length > 0 ? (
        <div className="rounded-xl border border-warning/30 bg-warning/5 p-4 text-sm">
          <p className="font-medium text-warning">{t("stockTracker.caveats")}</p>
          <ul className="mt-2 list-inside list-disc space-y-1 text-muted-foreground">
            {report.caveats.map((caveat, index) => (
              <li key={index}>{caveat}</li>
            ))}
          </ul>
        </div>
      ) : null}

      <p className="text-xs text-muted-foreground">{t("stockTracker.researchDisclaimer")}</p>
    </div>
  );
}

function SymbolCard({ symbol }: { symbol: SymbolRecommendation }) {
  const { t } = useTranslation();
  const action = resolveAction(symbol);
  const confidence = formatConfidence(symbol.confidence);
  const keyMetrics = Object.entries(symbol.key_metrics ?? {});

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex flex-col">
          <span className="text-sm font-semibold">{symbol.name ?? symbol.code}</span>
          <span className="font-mono text-xs text-muted-foreground">{symbol.code}</span>
        </div>
        {action ? (
          <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${getActionToneClass(action)}`}>
            {t(getActionLabelKey(action))}
          </span>
        ) : null}
      </div>

      {(confidence || symbol.time_horizon) ? (
        <div className="mb-2 flex items-center gap-3 text-xs text-muted-foreground">
          {confidence ? (
            <span>
              {t("stockTracker.confidence")}:{" "}
              <span className="font-mono font-semibold text-foreground/90">{confidence}</span>
            </span>
          ) : null}
          {symbol.time_horizon ? (
            <span>
              {t("stockTracker.timeHorizon")}: {symbol.time_horizon}
            </span>
          ) : null}
        </div>
      ) : null}

      {symbol.rationale ? (
        <p className="mb-2 text-sm leading-relaxed text-foreground/90">{symbol.rationale}</p>
      ) : null}

      <StructuredPlan symbol={symbol} />

      {keyMetrics.length > 0 ? (
        <div className="mb-2 flex flex-wrap gap-2">
          {keyMetrics.map(([key, value]) => (
            <div key={key} className="rounded bg-muted/40 px-2 py-1 text-xs">
              <span className="text-muted-foreground">{key}: </span>
              <span className="font-mono font-medium">{formatMetric(value)}</span>
            </div>
          ))}
        </div>
      ) : null}

      {(symbol.risks?.length ?? 0) > 0 ? (
        <div>
          <p className="mb-1 text-xs font-medium text-muted-foreground">{t("stockTracker.risks")}</p>
          <ul className="list-inside list-disc space-y-0.5 text-xs text-foreground/80">
            {symbol.risks!.map((risk, index) => (
              <li key={index}>{risk}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function StructuredPlan({ symbol }: { symbol: SymbolRecommendation }) {
  const { t } = useTranslation();
  const rows: { label: string; value: string | null; tone?: string }[] = [];

  const entryText = formatPriceZoneText(symbol.entry_zone);
  const targetText = formatPriceZoneText(symbol.target_zone);
  if (entryText) rows.push({ label: t("stockTracker.entryZone"), value: entryText, tone: "text-success" });
  if (targetText) rows.push({ label: t("stockTracker.targetZone"), value: targetText });
  if (symbol.stop_loss != null) {
    rows.push({
      label: t("stockTracker.stopLoss"),
      value: formatTrackPrice(symbol.stop_loss),
      tone: "text-danger",
    });
  }
  if (symbol.reduce_trigger) {
    rows.push({ label: t("stockTracker.reduceTrigger"), value: symbol.reduce_trigger });
  }

  if (rows.length === 0) return null;

  return (
    <div className="mb-2 rounded-lg border border-border/40 bg-muted/20 p-2.5 text-xs">
      <div className="space-y-1.5">
        {rows.map((row, index) => (
          <div key={index} className="flex gap-2">
            <span className="w-24 shrink-0 text-muted-foreground">{row.label}</span>
            <span className={`flex-1 font-mono font-medium ${row.tone ?? "text-foreground/90"}`}>
              {row.value}
            </span>
          </div>
        ))}
      </div>
      {(symbol.track_metrics?.length ?? 0) > 0 ? (
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="text-muted-foreground">{t("stockTracker.trackMetrics")}:</span>
          {symbol.track_metrics!.map((metric) => (
            <span key={metric} className="rounded bg-primary/10 px-1.5 py-0.5 font-mono text-[10px] text-primary">
              {metric}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function formatConfidence(value: number | string | null | undefined): string | null {
  if (value == null) return null;
  if (typeof value === "number") return `${Math.round(value)}%`;
  const legacy: Record<string, string> = { high: "80%", medium: "50%", low: "30%" };
  const hit = legacy[value.trim().toLowerCase()];
  return hit ?? value;
}

function formatMetric(value: unknown): string {
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }
  return String(value);
}
