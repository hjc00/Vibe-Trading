import { useTranslation } from "react-i18next";
import { Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { SignalBadge } from "./SignalBadge";
import type { SignalType, SignalValue, SymbolSnapshot } from "@/lib/api";

interface TrackerTableProps {
  symbols: SymbolSnapshot[];
  periods: number[];
  signals: SignalType[];
  selectedCode: string | null;
  onSelectCode: (code: string) => void;
  onRemoveSymbol?: (code: string) => void;
}

export function TrackerTable({ symbols, periods, signals, selectedCode, onSelectCode, onRemoveSymbol }: TrackerTableProps) {
  const { t } = useTranslation();

  return (
    <div className="overflow-hidden rounded-xl border border-border/60 bg-card shadow-sm">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b bg-muted/40 text-left text-[11px] uppercase tracking-wide text-muted-foreground">
              <th className="py-2 ps-4 pr-4 font-medium">{t("stockTracker.symbols")}</th>
              {periods.map((period) => (
                <th key={period} className="py-2 pr-4 font-medium text-center">
                  {period}
                  {t("stockTracker.period")}
                </th>
              ))}
              <th className="py-2 pr-4 font-medium">{t("stockTracker.crossDayChange")}</th>
              <th className="py-2 pr-4 text-right font-medium">{t("stockTracker.delete")}</th>
            </tr>
          </thead>
          <tbody>
            {symbols.map((symbol, index) => {
              const isSelected = symbol.code === selectedCode;
              return (
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
                    <div className="flex flex-col">
                      <span className="text-xs font-medium">{symbol.name ?? symbol.code}</span>
                      <span className="font-mono text-[10px] text-muted-foreground">{symbol.code}</span>
                      <span className="text-[10px] text-muted-foreground">
                        {symbol.close?.toFixed(2) ?? "—"}
                        {symbol.daily_return !== undefined && symbol.daily_return !== null && (
                          <span
                            className={cn(
                              "ms-1 font-mono tabular-nums",
                              symbol.daily_return > 0 && "text-success",
                              symbol.daily_return < 0 && "text-danger",
                            )}
                          >
                            {symbol.daily_return > 0 ? "+" : ""}
                            {(symbol.daily_return * 100).toFixed(2)}%
                          </span>
                        )}
                      </span>
                      <GlobalMaAlignment signal={symbol.period_signals["10"]?.signals?.ma_alignment} />
                    </div>
                  </td>
                  {periods.map((period) => {
                    const ps = symbol.period_signals[String(period)];
                    return (
                      <td key={period} className="py-3 pr-4">
                        <div className="flex flex-col gap-1">
                          <div className="flex items-center justify-center gap-2 text-xs">
                            <ReturnPill value={ps?.metrics.return_pct} />
                          </div>
                          <div className="flex flex-wrap justify-center gap-1">
                            {signals
                              .filter((signalType) => signalType !== "ma_alignment")
                              .map((signalType) => (
                                <SignalBadge
                                  key={signalType}
                                  type={signalType}
                                  signal={ps?.signals?.[signalType]}
                                  compact
                                />
                              ))}
                          </div>
                        </div>
                      </td>
                    );
                  })}
                  <td className="py-3 pr-4">
                    <CrossDayDiffCell diff={symbol.diff} />
                  </td>
                  <td className="py-3 pr-4 text-right">
                    {onRemoveSymbol && (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          onRemoveSymbol(symbol.code);
                        }}
                        className="rounded p-1 text-muted-foreground transition hover:bg-danger/10 hover:text-danger"
                        aria-label={t("stockTracker.delete")}
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function GlobalMaAlignment({ signal }: { signal: SignalValue | undefined }) {
  const { t } = useTranslation();
  if (!signal || !signal.triggered) return null;
  return (
    <span className="mt-1 inline-flex w-fit items-center gap-1 rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary" title={signal.description}>
      {t("stockTracker.maAlignment")}
      {signal.value !== null && signal.value !== undefined && (
        <span className="font-mono tabular-nums">{(signal.value * 100).toFixed(2)}%</span>
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
          {t("stockTracker.newSignals")}: {diff.new_signals.join(", ")}
        </span>
      )}
      {diff.cleared_signals.length > 0 && (
        <span className="text-muted-foreground">
          {t("stockTracker.clearedSignals")}: {diff.cleared_signals.join(", ")}
        </span>
      )}
      {diff.new_signals.length === 0 && diff.cleared_signals.length === 0 && (
        <span className="text-muted-foreground">{t("stockTracker.none")}</span>
      )}
    </div>
  );
}
