import { useTranslation } from "react-i18next";
import type { SignalMeta, TrackerSnapshot } from "@/lib/api";

interface TrackerSummaryProps {
  snapshot: TrackerSnapshot | null;
  signals: SignalMeta[];
  loading: boolean;
}

export function TrackerSummary({ snapshot, signals, loading }: TrackerSummaryProps) {
  const { t } = useTranslation();

  const visibleSignalNames = new Set(signals.filter((s) => s.show_in_table).map((s) => s.name));

  const symbolCount = snapshot?.symbols.length ?? 0;
  const triggeredSignals = snapshot?.symbols.reduce((count, symbol) => {
    return (
      count +
      Object.values(symbol.period_signals).reduce((inner, ps) => {
        return (
          inner +
          Object.entries(ps.signals)
            .filter(([signalType, signal]) => visibleSignalNames.has(signalType) && signal?.triggered).length
        );
      }, 0)
    );
  }, 0) ?? 0;

  const unresolvedCount = snapshot?.unresolved.length ?? 0;
  const dataGapCount = snapshot?.data_gaps.length ?? 0;

  const cards: { label: string; value: number; tone?: "danger" | "warning" | "neutral" }[] = [
    { label: t("stockTracker.symbols"), value: symbolCount },
    { label: t("stockTracker.todaySignals"), value: triggeredSignals },
    { label: t("stockTracker.unresolved"), value: unresolvedCount, tone: unresolvedCount > 0 ? "danger" : "neutral" },
    { label: t("stockTracker.dataGaps"), value: dataGapCount, tone: dataGapCount > 0 ? "warning" : "neutral" },
  ];

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {cards.map((card) => (
        <div
          key={card.label}
          className="rounded-xl border border-border/60 bg-card p-4 shadow-sm"
        >
          <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            {card.label}
          </p>
          <p
            className={`mt-1 text-2xl font-semibold font-mono tabular-nums ${
              loading ? "animate-pulse text-muted-foreground" : getToneClass(card.tone)
            }`}
          >
            {loading ? "—" : card.value}
          </p>
        </div>
      ))}
    </div>
  );
}

function getToneClass(tone: "danger" | "warning" | "neutral" | undefined): string {
  switch (tone) {
    case "danger":
      return "text-danger";
    case "warning":
      return "text-warning";
    default:
      return "text-foreground";
  }
}
