import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { getSignalLabelKey, formatSignalValue } from "@/lib/stockTracker";
import type { SignalMeta, SignalType, SignalValue } from "@/lib/api";

interface SignalBadgeProps {
  type: SignalType;
  signal: SignalValue | undefined;
  compact?: boolean;
  meta?: SignalMeta;
}

export function SignalBadge({ type, signal, compact = false, meta }: SignalBadgeProps) {
  const { t } = useTranslation();

  if (!signal || !signal.triggered) {
    return compact ? null : (
      <span className="inline-flex items-center rounded px-2 py-0.5 text-[10px] text-muted-foreground">
        {t(getSignalLabelKey(type) as never)}: {t("stockTracker.none")}
      </span>
    );
  }

  const isStrong = signal.state === "strong";
  const direction = meta?.direction ?? inferDirectionFromDescription(signal.description);
  const isBullish = direction === "bullish" || (direction === "both" && isBullishDescription(signal.description));
  const isBearish = direction === "bearish" || (direction === "both" && isBearishDescription(signal.description));

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
      {t(getSignalLabelKey(type) as never)}
      {signal.value !== null && signal.value !== undefined && (
        <span className="font-mono tabular-nums">{formatSignalValue(meta?.format, signal.value)}</span>
      )}
    </span>
  );
}

function inferDirectionFromDescription(description: string): "bullish" | "bearish" | "neutral" {
  const lower = description.toLowerCase();
  if (isBullishDescription(lower)) return "bullish";
  if (isBearishDescription(lower)) return "bearish";
  return "neutral";
}

function isBullishDescription(description: string): boolean {
  return /bullish|above|overbought/.test(description.toLowerCase());
}

function isBearishDescription(description: string): boolean {
  return /bearish|below|oversold/.test(description.toLowerCase());
}
