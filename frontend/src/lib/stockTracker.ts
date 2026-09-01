import type { SignalMeta, SignalType } from "@/lib/api";

export const SIGNAL_LABEL_KEYS: Record<SignalType, string> = {
  volume_spike: "stockTracker.volumeSpike",
  breakout: "stockTracker.breakout",
  ma_alignment: "stockTracker.maAlignment",
  rsi: "stockTracker.rsi",
} as const;

export function getSignalLabelKey(signal: SignalType): string {
  return SIGNAL_LABEL_KEYS[signal] ?? signal;
}

export function formatSignalValue(format: SignalMeta["format"] | undefined, value: number): string {
  if (format === "multiple") return `${value.toFixed(2)}x`;
  if (format === "percent") return `${(value * 100).toFixed(2)}%`;
  if (format === "price") return value.toFixed(2);
  return String(value);
}

/**
 * Infer the exchange suffix from the first two digits of a 6-digit A-share code.
 */
function inferAShareExchange(numeric: string): "SH" | "SZ" | "BJ" | null {
  if (!/^\d{6}$/.test(numeric)) {
    return null;
  }
  const prefix = numeric.slice(0, 2);
  if (["60", "68", "69"].includes(prefix)) {
    return "SH";
  }
  if (["00", "30"].includes(prefix)) {
    return "SZ";
  }
  if (
    ["80", "81", "82", "83", "84", "85", "86", "87", "88", "89", "40", "41", "42", "43"].includes(
      prefix,
    )
  ) {
    return "BJ";
  }
  return null;
}

/**
 * Normalize an A-share code to the ``6-digit.EXCHANGE`` form.
 *
 * The prefix is trusted more than the suffix: ``000938.SH`` is corrected to
 * ``000938.SZ`` because ``00`` prefixes are Shenzhen. This handles codes pasted
 * from sources that use the wrong venue suffix.
 *
 * If the code is a bare 6-digit number, infer the exchange from the prefix:
 *   - 60/68/69 -> .SH
 *   - 00/30    -> .SZ
 *   - 8/4      -> .BJ
 *
 * Returns null for unrecognized formats.
 */
export function normalizeAShareCode(code: string): string | null {
  const cleaned = code.trim().toUpperCase();
  const match = cleaned.match(/^(\d{6})(?:\.(SH|SZ|BJ))?$/);
  if (!match) {
    return null;
  }
  const numeric = match[1];
  const inferred = inferAShareExchange(numeric);
  if (inferred) {
    return `${numeric}.${inferred}`;
  }
  const suffix = match[2];
  if (suffix) {
    return `${numeric}.${suffix}`;
  }
  return null;
}
