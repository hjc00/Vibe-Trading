import { useTranslation } from "react-i18next";
import type { SymbolRecommendation, TrackerAnalyzeReport } from "@/lib/api";

interface TrackerAnalysisReportProps {
  report: TrackerAnalyzeReport | null;
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

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex flex-col">
          <span className="text-sm font-semibold">{symbol.name ?? symbol.code}</span>
          <span className="font-mono text-xs text-muted-foreground">{symbol.code}</span>
        </div>
        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${recommendationTone(symbol.recommendation)}`}>
          {symbol.recommendation}
        </span>
      </div>

      <div className="mb-2 flex items-center gap-3 text-xs text-muted-foreground">
        <span>
          {t("stockTracker.confidence")}: {symbol.confidence}
        </span>
        {symbol.time_horizon ? (
          <span>
            {t("stockTracker.timeHorizon")}: {symbol.time_horizon}
          </span>
        ) : null}
      </div>

      <p className="mb-2 text-sm leading-relaxed text-foreground/90">{symbol.rationale}</p>

      {Object.keys(symbol.key_metrics).length > 0 ? (
        <div className="mb-2 flex flex-wrap gap-2">
          {Object.entries(symbol.key_metrics).map(([key, value]) => (
            <div key={key} className="rounded bg-muted/40 px-2 py-1 text-xs">
              <span className="text-muted-foreground">{key}: </span>
              <span className="font-mono font-medium">{formatMetric(value)}</span>
            </div>
          ))}
        </div>
      ) : null}

      {symbol.risks.length > 0 ? (
        <div>
          <p className="mb-1 text-xs font-medium text-muted-foreground">{t("stockTracker.risks")}</p>
          <ul className="list-inside list-disc space-y-0.5 text-xs text-foreground/80">
            {symbol.risks.map((risk, index) => (
              <li key={index}>{risk}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function recommendationTone(recommendation: string): string {
  switch (recommendation) {
    case "top_pick":
      return "bg-primary/10 text-primary";
    case "avoid":
    case "caution":
      return "bg-danger/10 text-danger";
    default:
      return "bg-muted text-muted-foreground";
  }
}

function formatMetric(value: unknown): string {
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }
  return String(value);
}
