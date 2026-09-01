import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  computePriceChange,
  formatCapitalAmount,
  formatQuoteUpdatedAt,
  formatSignalValue,
  getSignalLabelKey,
} from "@/lib/stockTracker";
import { SignalBadge } from "./SignalBadge";
import type { SignalMeta, SignalValue, SymbolSnapshot } from "@/lib/api";

interface TrackerTableProps {
  symbols: SymbolSnapshot[];
  periods: number[];
  signals: SignalMeta[];
  selectedCode: string | null;
  onSelectCode: (code: string) => void;
  onRemoveSymbol?: (code: string) => void;
  quotesUpdatedAt?: string | null;
}

const EXPANDED_ROWS_STORAGE_KEY = "stockTracker.expandedRows";

function readExpandedRows(): Set<string> {
  try {
    const raw = localStorage.getItem(EXPANDED_ROWS_STORAGE_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) return new Set(parsed.filter((c): c is string => typeof c === "string"));
  } catch {
    // ignore corrupt storage
  }
  return new Set();
}

function writeExpandedRows(rows: Set<string>): void {
  try {
    localStorage.setItem(EXPANDED_ROWS_STORAGE_KEY, JSON.stringify(Array.from(rows)));
  } catch {
    // ignore storage errors
  }
}

export function TrackerTable({
  symbols,
  periods,
  signals,
  selectedCode,
  onSelectCode,
  onRemoveSymbol,
  quotesUpdatedAt,
}: TrackerTableProps) {
  const { t } = useTranslation();
  const [expandedRows, setExpandedRows] = useState<Set<string>>(() => readExpandedRows());

  useEffect(() => {
    writeExpandedRows(expandedRows);
  }, [expandedRows]);

  const toggleRow = (code: string) => {
    setExpandedRows((prev) => {
      const next = new Set(prev);
      if (next.has(code)) {
        next.delete(code);
      } else {
        next.add(code);
      }
      return next;
    });
  };

  const tableSignals = signals.filter((s) => s.show_in_table);
  const globalSignals = signals.filter((s) => s.is_global);

  return (
    <div className="overflow-hidden rounded-xl border border-border/60 bg-card shadow-sm">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <thead>
            <tr className="border-b bg-muted/40 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
              <th className="py-2 ps-4 pr-4 font-medium">{t("stockTracker.symbols")}</th>
              <th className="py-2 pr-4 font-medium text-right">{t("stockTracker.price")}</th>
              <th className="py-2 pr-4 font-medium text-right">{t("stockTracker.change")}</th>
              <th className="py-2 pr-4 text-right font-medium">{t("stockTracker.delete")}</th>
            </tr>
          </thead>
          <tbody>
            {symbols.map((symbol, index) => {
              const isSelected = symbol.code === selectedCode;
              const isExpanded = expandedRows.has(symbol.code);
              return (
                <>
                  <tr
                    key={symbol.code}
                    onClick={() => onSelectCode(symbol.code)}
                    className={cn(
                      "cursor-pointer border-b last:border-0 transition-colors",
                      index % 2 === 1 && "bg-muted/10",
                      isSelected ? "bg-primary/10" : "hover:bg-muted/40",
                    )}
                  >
                    <td className="py-3 ps-4 pr-4">
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            toggleRow(symbol.code);
                          }}
                          className="inline-flex items-center justify-center rounded p-1 text-muted-foreground transition hover:bg-muted hover:text-foreground"
                          aria-label={isExpanded ? t("stockTracker.collapse") : t("stockTracker.expand")}
                        >
                          {isExpanded ? (
                            <ChevronDown className="h-4 w-4" />
                          ) : (
                            <ChevronRight className="h-4 w-4" />
                          )}
                        </button>
                        <div className="flex flex-col">
                          <span className="text-xs font-medium">{symbol.name ?? symbol.code}</span>
                          <span className="font-mono text-[10px] text-muted-foreground">{symbol.code}</span>
                          <div className="mt-1 flex flex-wrap gap-1">
                            {globalSignals.map((meta) => (
                              <GlobalSignalBadge
                                key={meta.name}
                                meta={meta}
                                signal={symbol.period_signals["10"]?.signals?.[meta.name]}
                              />
                            ))}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="py-3 pr-4 text-right">
                      <div className="flex flex-col items-end">
                        <span className="font-mono text-sm font-semibold tabular-nums">
                          {symbol.close?.toFixed(2) ?? "—"}
                        </span>
                        {symbol.prev_close != null && (
                          <span className="text-[10px] text-muted-foreground">
                            {t("stockTracker.prevClose")}: {symbol.prev_close.toFixed(2)}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="py-3 pr-4 text-right">
                      <CompactChangeCell symbol={symbol} />
                    </td>
                    <td className="py-3 pr-4 text-right">
                      {onRemoveSymbol && (
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            onRemoveSymbol(symbol.code);
                          }}
                          className="inline-flex items-center justify-center rounded p-1.5 text-muted-foreground transition hover:bg-danger/10 hover:text-danger"
                          aria-label={t("stockTracker.delete")}
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      )}
                    </td>
                  </tr>
                  {isExpanded && (
                    <tr
                      key={`${symbol.code}-expanded`}
                      className={cn("border-b last:border-0", isSelected ? "bg-primary/10" : "bg-muted/5")}
                    >
                      <td colSpan={4} className="p-0">
                        <div className="grid grid-cols-1 gap-4 px-4 py-3 sm:grid-cols-[1fr_auto]">
                          <div className="flex flex-col gap-2">
                            {quotesUpdatedAt && (
                              <div className="text-xs text-muted-foreground">
                                {t("stockTracker.updatedAt", { when: formatQuoteUpdatedAt(quotesUpdatedAt, t) })}
                              </div>
                            )}
                            <CapitalCell symbol={symbol} />
                          </div>
                          <div className="flex flex-col gap-4">
                            <div className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-4">
                              {periods.map((period) => {
                                const ps = symbol.period_signals[String(period)];
                                return (
                                  <div key={period} className="flex flex-col gap-1">
                                    <span className="text-[10px] text-muted-foreground">
                                      {period}
                                      {t("stockTracker.period")}
                                    </span>
                                    <div className="flex items-center gap-2">
                                      <ReturnPill value={ps?.metrics.return_pct} />
                                    </div>
                                    <div className="flex flex-wrap gap-1">
                                      {tableSignals.map((meta) => (
                                        <SignalBadge
                                          key={meta.name}
                                          type={meta.name}
                                          signal={ps?.signals?.[meta.name]}
                                          meta={meta}
                                          compact
                                        />
                                      ))}
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                            <CrossDayDiffCell diff={symbol.diff} />
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function CompactChangeCell({ symbol }: { symbol: SymbolSnapshot }) {
  const { changeAmount, dailyReturn } = computePriceChange(symbol.close, symbol.prev_close, symbol.daily_return);

  return (
    <div className="flex flex-col items-end">
      {changeAmount !== null && (
        <span
          className={cn(
            "font-mono text-xs tabular-nums",
            changeAmount > 0 && "text-success",
            changeAmount < 0 && "text-danger",
            changeAmount === 0 && "text-muted-foreground",
          )}
        >
          {changeAmount > 0 ? "+" : ""}
          {changeAmount.toFixed(2)}
        </span>
      )}
      {dailyReturn !== null && (
        <span
          className={cn(
            "font-mono text-xs tabular-nums",
            dailyReturn > 0 && "text-success",
            dailyReturn < 0 && "text-danger",
            dailyReturn === 0 && "text-muted-foreground",
          )}
        >
          {dailyReturn > 0 ? "+" : ""}
          {(dailyReturn * 100).toFixed(2)}%
        </span>
      )}
    </div>
  );
}

function CapitalCell({ symbol }: { symbol: SymbolSnapshot }) {
  const { t } = useTranslation();
  const capital = symbol.capital;
  if (!capital) return null;

  const fundFlowError = capital.fund_flow_error;
  const mainNet = capital.fund_flow.main_net;
  const main5dNet = capital.fund_flow.main_5d_net;
  const hasFundFlow = mainNet != null || main5dNet != null;

  if (hasFundFlow) {
    return (
      <div className="flex flex-col gap-0.5 text-[10px]">
        {mainNet != null && (
          <span
            className={cn(
              "font-mono tabular-nums",
              mainNet > 0 && "text-success",
              mainNet < 0 && "text-danger",
              mainNet === 0 && "text-muted-foreground",
            )}
          >
            {t("stockTracker.mainForceNetInflow")}: {mainNet > 0 ? "+" : ""}
            {formatCapitalAmount(mainNet)}
          </span>
        )}
        {main5dNet != null && (
          <span
            className={cn(
              "font-mono tabular-nums",
              main5dNet > 0 && "text-success",
              main5dNet < 0 && "text-danger",
              main5dNet === 0 && "text-muted-foreground",
            )}
          >
            {t("stockTracker.mainForce5dNetInflow")}: {main5dNet > 0 ? "+" : ""}
            {formatCapitalAmount(main5dNet)}
          </span>
        )}
        {fundFlowError && (
          <span className="text-muted-foreground">{t("stockTracker.fundFlowDataUnavailable")}</span>
        )}
      </div>
    );
  }

  const marginError = capital.margin_error;
  const financingChange = capital.margin.financing_balance_change;

  if (financingChange === null || financingChange === undefined) {
    if (fundFlowError || marginError) {
      return (
        <div className="flex flex-col gap-0.5 text-[10px]">
          <span className="text-muted-foreground">
            {fundFlowError ? t("stockTracker.fundFlowDataUnavailable") : t("stockTracker.capitalDataUnavailable")}
          </span>
        </div>
      );
    }
    return null;
  }

  return (
    <div className="flex flex-col gap-0.5 text-[10px]">
      <span
        className={cn(
          "font-mono tabular-nums",
          financingChange > 0 && "text-success",
          financingChange < 0 && "text-danger",
          financingChange === 0 && "text-muted-foreground",
        )}
      >
        {t("stockTracker.financingBalanceChange")}: {financingChange > 0 ? "+" : ""}
        {formatCapitalAmount(financingChange)}
      </span>
      {(fundFlowError || marginError) && (
        <span className="text-muted-foreground">
          {fundFlowError ? t("stockTracker.fundFlowDataUnavailable") : t("stockTracker.capitalDataUnavailable")}
        </span>
      )}
    </div>
  );
}

function GlobalSignalBadge({ signal, meta }: { signal: SignalValue | undefined; meta: SignalMeta }) {
  const { t } = useTranslation();
  if (!signal || !signal.triggered) return null;
  return (
    <span
      className="inline-flex w-fit items-center gap-1 rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary"
      title={signal.description}
    >
      {t(getSignalLabelKey(meta.name) as never)}
      {signal.value !== null && signal.value !== undefined && (
        <span className="font-mono tabular-nums">{formatSignalValue(meta.format, signal.value)}</span>
      )}
    </span>
  );
}

function ReturnPill({ value }: { value: number | null | undefined }) {
  if (value === undefined || value === null) {
    return <span className="text-muted-foreground">—</span>;
  }
  return (
    <span
      className={cn(
        "font-mono tabular-nums",
        value > 0 && "text-success",
        value < 0 && "text-danger",
        value === 0 && "text-muted-foreground",
      )}
    >
      {value > 0 ? "+" : ""}
      {(value * 100).toFixed(2)}%
    </span>
  );
}

function CrossDayDiffCell({ diff }: { diff: SymbolSnapshot["diff"] }) {
  const { t } = useTranslation();
  if (!diff) return <span className="text-xs text-muted-foreground">—</span>;

  return (
    <div className="flex flex-col gap-1 text-xs">
      {diff.signal_count && (
        <span className="text-muted-foreground">
          {t("stockTracker.todaySignals")}: {diff.signal_count.curr} (
          {diff.signal_count.curr >= diff.signal_count.prev ? "+" : ""}
          {diff.signal_count.curr - diff.signal_count.prev})
        </span>
      )}
      {diff.new_signals.length > 0 && (
        <span className="text-success">
          {t("stockTracker.newSignals")}: {diff.new_signals.map((s) => t(getSignalLabelKey(s) as never)).join(", ")}
        </span>
      )}
      {diff.cleared_signals.length > 0 && (
        <span className="text-muted-foreground">
          {t("stockTracker.clearedSignals")}: {diff.cleared_signals.map((s) => t(getSignalLabelKey(s) as never)).join(", ")}
        </span>
      )}
      {diff.new_signals.length === 0 && diff.cleared_signals.length === 0 && (
        <span className="text-muted-foreground">{t("stockTracker.none")}</span>
      )}
    </div>
  );
}
