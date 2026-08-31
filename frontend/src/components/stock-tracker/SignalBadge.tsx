import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getSignalLabelKey } from "@/lib/stockTracker";
import type { SignalType, SignalValue } from "@/lib/api";

interface SignalBadgeProps {
  type: SignalType;
  signal: SignalValue | undefined;
  compact?: boolean;
}

export function SignalBadge({ type, signal, compact = false }: SignalBadgeProps) {
  const { t } = useTranslation();

  if (!signal || !signal.triggered) {
    return compact ? null : (
      <span className="inline-flex items-center rounded px-2 py-0.5 text-[10px] text-muted-foreground">
        {t(getSignalLabelKey(type))}: {t("stockTracker.none")}
      </span>
    );
  }

  const isStrong = signal.state === "strong";
  const isBullish = signal.description.toLowerCase().includes("bullish") || signal.description.toLowerCase().includes("above");
  const isBearish = signal.description.toLowerCase().includes("bearish") || signal.description.toLowerCase().includes("below");

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded px-2 py-0.5 text-[10px] font-medium",
        isStrong && "bg-primary/15 text-primary",
        !isStrong && isBullish && "bg-success/10 text-success",
        !isStrong && isBearish && "bg-danger/10 text-danger",
        !isStrong && !isBullish && !isBearish && "bg-warning/10 text-warning",
      )}
      title={signal.description}
    >
      {t(getSignalLabelKey(type))}
      {signal.value !== null && signal.value !== undefined && (
        <span className="font-mono tabular-nums">{formatSignalValue(type, signal.value)}</span>
      )}
    </span>
  );
}

function formatSignalValue(type: SignalType, value: number): string {
  if (type === "volume_spike") return `${value.toFixed(2)}x`;
  if (type === "breakout") return `${(value * 100).toFixed(2)}%`;
  if (type === "ma_alignment") return `${(value * 100).toFixed(2)}%`;
  return String(value);
}
