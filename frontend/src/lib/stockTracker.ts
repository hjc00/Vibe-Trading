import type { SignalMeta, SignalType, SymbolSnapshot } from "@/lib/api";
import type { TFunction } from "i18next";

export const SIGNAL_LABEL_KEYS: Record<SignalType, string> = {
  volume_spike: "stockTracker.volumeSpike",
  breakout: "stockTracker.breakout",
  ma_alignment: "stockTracker.maAlignment",
  rsi: "stockTracker.rsi",
  margin_expansion: "stockTracker.marginExpansion",
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

export function formatCapitalAmount(value: number | null | undefined): string {
  if (value === undefined || value === null) return "—";
  const absValue = Math.abs(value);
  if (absValue >= 1e8) return `${(value / 1e8).toFixed(2)}亿`;
  if (absValue >= 1e4) return `${(value / 1e4).toFixed(2)}万`;
  return value.toFixed(0);
}

export function formatRps(value: number | null | undefined): string {
  if (value === undefined || value === null) return "—";
  return `${value.toFixed(1)}%`;
}

export function getRpsToneClass(value: number | null | undefined): string {
  if (value === undefined || value === null) return "text-muted-foreground";
  if (value >= 90) return "text-success";
  if (value <= 10) return "text-danger";
  return "text-muted-foreground";
}

/**
 * Tone for an up/down percentage change. Positive changes render as success
 * (green), negative as danger (red), matching the inline convention used across
 * the tracker UI.
 */
export function getChangeToneClass(value: number | null | undefined): string {
  if (value === undefined || value === null) return "text-muted-foreground";
  if (value > 0) return "text-success";
  if (value < 0) return "text-danger";
  return "text-muted-foreground";
}

export function formatAtr(value: number | null | undefined): string {
  if (value === undefined || value === null) return "—";
  return value.toFixed(2);
}

export function formatPct(value: number | null | undefined): string {
  if (value === undefined || value === null) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

export function formatBeta(value: number | null | undefined): string {
  if (value === undefined || value === null) return "—";
  return value.toFixed(2);
}

export function formatMarketCap(value: number | null | undefined): string {
  if (value === undefined || value === null) return "—";
  if (value >= 1e12) return `${(value / 1e12).toFixed(2)}万亿`;
  if (value >= 1e8) return `${(value / 1e8).toFixed(1)}亿`;
  if (value >= 1e4) return `${(value / 1e4).toFixed(1)}万`;
  return value.toFixed(0);
}

/**
 * Tone for a valuation percentile. A low percentile means the stock is cheap
 * relative to its own history (good), a high percentile means expensive (bad) —
 * the opposite of the RPS tone mapping.
 */
export function getValuationPercentileTone(value: number | null | undefined): string {
  if (value === undefined || value === null) return "text-muted-foreground";
  if (value <= 30) return "text-success";
  if (value >= 70) return "text-danger";
  return "text-foreground";
}

/** i18n key for the valuation band a percentile falls into, or null when absent. */
export function getValuationBandLabelKey(value: number | null | undefined): string | null {
  if (value === undefined || value === null) return null;
  if (value <= 30) return "stockTracker.valuationCheap";
  if (value >= 70) return "stockTracker.valuationExpensive";
  return "stockTracker.valuationReasonable";
}

export function getQualityToneClass(value: number | null | undefined): string {
  if (value === undefined || value === null) return "text-muted-foreground";
  if (value >= 80) return "text-success";
  if (value >= 60) return "text-info";
  if (value >= 40) return "text-warning";
  return "text-danger";
}

export function getBetaToneClass(value: number | null | undefined): string {
  if (value === undefined || value === null) return "text-muted-foreground";
  if (value >= 1.3) return "text-warning";
  if (value <= 0.8) return "text-info";
  return "text-foreground";
}

export function getDrawdownToneClass(value: number | null | undefined): string {
  if (value === undefined || value === null) return "text-muted-foreground";
  if (value <= -0.2) return "text-danger";
  if (value <= -0.1) return "text-warning";
  return "text-foreground";
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
export interface PriceChange {
  changeAmount: number | null;
  dailyReturn: number | null;
}

export function computePriceChange(
  close: number | null | undefined,
  prevClose: number | null | undefined,
  dailyReturn?: number | null | undefined,
): PriceChange {
  const changeAmount =
    close != null && prevClose != null ? close - prevClose : null;
  const derivedReturn =
    changeAmount != null && prevClose ? changeAmount / prevClose : null;
  return {
    changeAmount,
    dailyReturn: dailyReturn ?? derivedReturn,
  };
}

export function formatQuoteUpdatedAt(iso: string, t: TFunction): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  const seconds = Math.floor((Date.now() - parsed.getTime()) / 1000);
  if (seconds < 5) return t("stockTracker.updatedJustNow");
  if (seconds < 60) return t("stockTracker.updatedSecondsAgo", { count: seconds });
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return t("stockTracker.updatedMinutesAgo", { count: minutes });
  const hours = Math.floor(minutes / 60);
  return t("stockTracker.updatedHoursAgo", { count: hours });
}

export function formatDataDate(value: string | null | undefined): string {
  if (!value) return "—";
  return value.slice(0, 10);
}

export function latestPeriodEndDate(
  symbol: SymbolSnapshot | null | undefined,
): string | null {
  if (!symbol) return null;
  let latest: string | null = null;
  for (const ps of Object.values(symbol.period_signals)) {
    const end = ps.metrics.end_date;
    if (end && (latest === null || end > latest)) latest = end;
  }
  return latest;
}

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
