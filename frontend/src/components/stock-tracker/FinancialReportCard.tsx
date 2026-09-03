import { FileText, Loader2, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  type FinancialPeriod,
  type FinancialReportSnapshot,
  type SymbolSnapshot,
  api,
} from "@/lib/api";
import { getChangeToneClass } from "@/lib/stockTracker";
import { cn } from "@/lib/utils";
import { ChartCardHeader } from "./ChartCardHeader";

interface FinancialReportCardProps {
  symbol: SymbolSnapshot | null;
  onHide?: () => void;
}

const PERIOD_OPTIONS = [1, 4, 8];
const DEFAULT_PERIODS = 4;

/** Format an already-percent value with a sign (e.g. 12.3 -> "+12.3%"). */
function signedPct(value: number | null | undefined): string {
  if (value === undefined || value === null) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;
}

/** Format an already-percent value (e.g. 91.8 -> "91.8%"). */
function plainPct(value: number | null | undefined): string {
  if (value === undefined || value === null) return "—";
  return `${value.toFixed(1)}%`;
}

function plainNum(value: number | null | undefined, digits = 2): string {
  if (value === undefined || value === null) return "—";
  return value.toFixed(digits);
}

function periodShortLabel(period: FinancialPeriod): string {
  // 报告期倒序表头，如 "2026 中报"，副行显示月日。
  return `${period.end_date.slice(0, 4)} ${period.report_type}`;
}

function periodDateShort(period: FinancialPeriod): string {
  return period.end_date.slice(5); // "06-30"
}

function beatMissMeta(
  beatMiss: FinancialReportSnapshot["beat_miss"],
): { labelKey: FrBeatKey | null; cls: string } {
  switch (beatMiss) {
    case "beat":
      return { labelKey: "stockTracker.financialReportBeat", cls: "text-success" };
    case "miss":
      return { labelKey: "stockTracker.financialReportMiss", cls: "text-danger" };
    case "inline":
      return { labelKey: "stockTracker.financialReportInline", cls: "text-info" };
    default:
      return { labelKey: null, cls: "text-muted-foreground" };
  }
}

type FrBeatKey =
  | "stockTracker.financialReportBeat"
  | "stockTracker.financialReportMiss"
  | "stockTracker.financialReportInline";

type FrRowLabelKey =
  | "stockTracker.financialReportRevenueYoy"
  | "stockTracker.financialReportNetProfitYoy"
  | "stockTracker.financialReportGrossMargin"
  | "stockTracker.financialReportNetMargin"
  | "stockTracker.financialReportRoe"
  | "stockTracker.financialReportOcfRatio"
  | "stockTracker.financialReportDebtToAssets"
  | "stockTracker.financialReportEps";

type MetricKey =
  | "revenue_yoy"
  | "net_profit_yoy"
  | "gross_margin"
  | "net_margin"
  | "roe"
  | "operating_cashflow_to_net_profit"
  | "debt_to_assets"
  | "eps";

interface MetricDef {
  key: MetricKey;
  labelKey: FrRowLabelKey;
  format: (v: number | null | undefined) => string;
  tone?: (v: number | null | undefined) => string | undefined;
}

const METRICS: MetricDef[] = [
  { key: "revenue_yoy", labelKey: "stockTracker.financialReportRevenueYoy", format: signedPct, tone: getChangeToneClass },
  { key: "net_profit_yoy", labelKey: "stockTracker.financialReportNetProfitYoy", format: signedPct, tone: getChangeToneClass },
  { key: "gross_margin", labelKey: "stockTracker.financialReportGrossMargin", format: plainPct },
  { key: "net_margin", labelKey: "stockTracker.financialReportNetMargin", format: plainPct },
  { key: "roe", labelKey: "stockTracker.financialReportRoe", format: plainPct },
  { key: "operating_cashflow_to_net_profit", labelKey: "stockTracker.financialReportOcfRatio", format: (v) => plainNum(v, 2) },
  {
    key: "debt_to_assets",
    labelKey: "stockTracker.financialReportDebtToAssets",
    format: plainPct,
    tone: (v) => (v !== undefined && v !== null && v > 70 ? "text-danger" : undefined),
  },
  { key: "eps", labelKey: "stockTracker.financialReportEps", format: (v) => plainNum(v, 2) },
];

/**
 * Financial-report reading (财报速读): shows one symbol's latest reported
 * periods in a multi-period table with a 1/4/8 period switch. Auto-loads for
 * the selected symbol — the backend serves the persisted cache when fresh, so
 * re-selecting an already-read symbol is instant. Not part of the daily
 * snapshot; the header button forces a live re-fetch.
 */
export function FinancialReportCard({ symbol, onHide }: FinancialReportCardProps) {
  const { t } = useTranslation();
  const code = symbol?.code;
  const [report, setReport] = useState<FinancialReportSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [visible, setVisible] = useState(DEFAULT_PERIODS);
  const requestSeq = useRef(0);

  const runFetch = useCallback((targetCode: string, force = false) => {
    const seq = ++requestSeq.current;
    setLoading(true);
    api
      .getStockTrackerFinancialReport(targetCode, force)
      .then((res) => {
        if (requestSeq.current === seq) setReport(res.report);
      })
      .catch(() => {
        if (requestSeq.current === seq) setReport(null);
      })
      .finally(() => {
        if (requestSeq.current === seq) setLoading(false);
      });
  }, []);

  // Auto-load on mount / symbol change; stale responses from a previously
  // selected symbol are dropped via the sequence guard.
  useEffect(() => {
    requestSeq.current += 1;
    setReport(null);
    setLoading(false);
    if (code) runFetch(code);
  }, [code, runFetch]);

  if (!symbol) return null;

  const latest = report && !report.error && report.periods.length > 0 ? report.periods[0] : null;
  const shown = report?.periods.slice(0, visible) ?? [];
  const beatMeta = report ? beatMissMeta(report.beat_miss) : null;
  const beatKey = beatMeta?.labelKey;
  const readLabel = report ? t("stockTracker.financialReportRefresh") : t("stockTracker.financialReportRead");

  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
      <ChartCardHeader
        title={t("stockTracker.financialReportTitle")}
        helpText={t("stockTracker.financialReportExplanation")}
        onHide={onHide}
        meta={
          report && latest
            ? `${latest.report_type} · ${latest.end_date}`
            : report?.error
              ? report.source
              : undefined
        }
        actions={
          <button
            type="button"
            onClick={() => code && runFetch(code, true)}
            disabled={loading || !code}
            className="inline-flex items-center gap-1 rounded-md border border-border/60 px-2 py-0.5 text-[11px] text-muted-foreground transition hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : report ? (
              <RefreshCw className="h-3 w-3" />
            ) : (
              <FileText className="h-3 w-3" />
            )}
            {loading ? t("stockTracker.financialReportLoading") : readLabel}
          </button>
        }
      />

      {!report ? (
        <div className="flex h-[220px] flex-col items-center justify-center gap-2 text-muted-foreground">
          {loading ? (
            <>
              <Loader2 className="h-8 w-8 animate-spin opacity-40" />
              <span className="text-xs">{t("stockTracker.financialReportLoading")}</span>
            </>
          ) : (
            <>
              <FileText className="h-8 w-8 opacity-40" />
              <span className="text-xs">{t("stockTracker.financialReportEmpty")}</span>
            </>
          )}
        </div>
      ) : !latest ? (
        <div className="flex h-[220px] flex-col items-center justify-center gap-2 text-muted-foreground">
          <FileText className="h-8 w-8 opacity-40" />
          <span className="text-xs">{t("stockTracker.financialReportNoData")}</span>
          {report.error ? (
            <span className="max-w-[280px] truncate text-[10px] text-muted-foreground/70">
              {report.error}
            </span>
          ) : null}
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              {beatKey ? (
                <span className={cn("text-xs font-semibold", beatMeta?.cls)}>
                  {t(beatKey)}
                </span>
              ) : null}
              {report.consensus_eps != null && latest.eps != null ? (
                <span className="text-[11px] text-muted-foreground">
                  {t("stockTracker.financialReportConsensusEps")}{" "}
                  <span className="font-mono tabular-nums">{report.consensus_eps.toFixed(2)}</span>
                  {" · 实际 "}
                  <span className="font-mono tabular-nums">{latest.eps.toFixed(2)}</span>
                </span>
              ) : null}
            </div>
            <div className="flex items-center rounded-md border border-border/60 p-0.5">
              {PERIOD_OPTIONS.map((n) => (
                <button
                  key={n}
                  type="button"
                  aria-pressed={visible === n}
                  onClick={() => setVisible(n)}
                  className={cn(
                    "rounded px-1.5 py-0.5 text-[10px] transition",
                    visible === n
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {n}
                  {t("stockTracker.financialReportPeriodUnit")}
                </button>
              ))}
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full min-w-[420px] border-collapse text-xs">
              <thead>
                <tr className="border-b border-border/60">
                  <th className="py-1.5 pr-3 text-left font-medium text-muted-foreground">
                    {t("stockTracker.financialReportIndicator")}
                  </th>
                  {shown.map((period) => (
                    <th
                      key={period.end_date}
                      title={period.end_date}
                      className="px-2 py-1.5 text-right"
                    >
                      <p className="font-semibold">{periodShortLabel(period)}</p>
                      <p className="text-[9px] font-normal text-muted-foreground">
                        {periodDateShort(period)}
                      </p>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {METRICS.map((metric) => (
                  <tr key={metric.key} className="border-b border-border/30">
                    <td className="py-1.5 pr-3 text-muted-foreground">{t(metric.labelKey)}</td>
                    {shown.map((period) => {
                      const raw = period[metric.key];
                      const tone = metric.tone?.(raw as number | null | undefined);
                      return (
                        <td key={period.end_date} className="px-2 py-1.5 text-right">
                          <span
                            className={cn(
                              "font-mono tabular-nums",
                              tone ?? "text-foreground",
                            )}
                          >
                            {metric.format(raw as number | null | undefined)}
                          </span>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex flex-wrap items-start gap-2">
            {report.red_flags.length > 0 ? (
              <span className="shrink-0 text-[11px] text-danger">▲</span>
            ) : null}
            <div className="flex flex-wrap gap-1.5 text-[11px]">
              {report.red_flags.length === 0 ? (
                <span className="text-muted-foreground">
                  {t("stockTracker.financialReportRedFlags")}: {t("stockTracker.financialReportNoFlags")}
                </span>
              ) : (
                report.red_flags.map((flag, i) => (
                  <span
                    key={`${flag}-${i}`}
                    className="rounded-md bg-danger/10 px-1.5 py-0.5 text-danger"
                  >
                    {flag}
                  </span>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
